"""Doc-vs-code consistency gate.

This round actually shipped four different gate counts across SKILL/README/pipeline (12/13/14)
while the code had 15, and SPEC §8 pointed at a `verify_e2e.py` that never existed. Docs that
disagree with code are worse than no docs, so the check is scripted.

Run: python check_docs.py    (exit 1 on any mismatch)
"""
import os, io, re, sys, json, glob

# A GBK console cannot encode '–' or '\ufffd', and this script printing one used to abort the whole
# self-test run. Never let a cosmetic character decide an exit code.
try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or 'utf-8', errors='backslashreplace')
    sys.stderr.reconfigure(encoding=sys.stderr.encoding or 'utf-8', errors='backslashreplace')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)


def gates_in_code():
    s = io.open(os.path.join(HERE, 'caofeng.py'), encoding='utf-8').read()
    return sorted({int(m) for m in re.findall(r"res\['G(\d+)_", s)})


def doc_text(name):
    p = os.path.join(SKILL, name)
    return io.open(p, encoding='utf-8').read() if os.path.exists(p) else ''


def kb_json(name):
    p = os.path.join(SKILL, 'kb', name)
    if not os.path.exists(p):
        return None
    try:
        return json.load(io.open(p, encoding='utf-8'))
    except Exception:
        return None


# SPEC §7 carries the threshold table as `- key = value` lines; this maps each code-side key to
# (how to render it, how to parse what the doc says). The point is that a doc number no longer can
# drift from the code number: fourth-round verification found G10 documented as 命中=0 while the
# code scored <70, video prompts documented 6/6 while the code accepted 5/6, and the G3b message
# said <1280 while the gate used 1080.
def th_contract():
    import caofeng as C
    T = C.TH
    exp = {}

    def ratio_pair(key, total):
        return '%d/%d' % (T[key], total)

    exp['img_sections_min'] = (ratio_pair('img_sections_min', len(C.IMG_SECTIONS)),
                               lambda s: s)
    exp['vid_sections_min'] = (ratio_pair('vid_sections_min', len(C.VID_SECTIONS)), lambda s: s)
    exp['gif_sections_min'] = (ratio_pair('gif_sections_min', len(C.GIF_SECTIONS)), lambda s: s)
    for k in ('img_chars_min', 'vid_chars_min', 'gif_chars_min',
              'embed_short_edge', 'fetch_short_edge', 'mp4_bytes_min', 'gif_frames_min',
              'gif_short_edge', 'ai_score_min', 'relevance_min', 'morph_pages_min',
              'trans_dur_ms'):

        if k in T:
            exp[k] = (str(T[k]), lambda s: int(re.sub(r'[^\d]', '', s) or -1))
    # 反向也要闭合：TH 里新增一条却忘了进对账表，就等于阈值可以悄悄改而没人发现。
    # video_dur / morph_dur 是合并写法，它们覆盖的四个键不再单独要求成行。
    covered = set(['video_dur_min', 'video_dur_max', 'morph_ms_min', 'morph_ms_max',
                   'ar_min', 'ar_max'])
    for k in T:
        if k not in exp and k not in covered:
            exp[k] = (str(T[k]), lambda s: int(re.sub(r'[^\d]', '', s) or -1))
    exp['video_dur'] = ('%.1f-%.1f 秒' % (T['video_dur_min'], T['video_dur_max']),
                        lambda s: tuple(int(x) for x in re.findall(r'\d+', s)))
    exp['morph_dur'] = ('%d-%d 毫秒' % (T['morph_ms_min'], T['morph_ms_max']),
                        lambda s: tuple(int(x) for x in re.findall(r'\d+', s)))
    exp['motion_ratio_min'] = ('%d%%' % round(T['motion_ratio_min'] * 100),
                               lambda s: int(re.sub(r'[^\d]', '', s) or -1))
    exp['frame0_mad_max'] = ('%g' % T['frame0_mad_max'],
                             lambda s: float(re.sub(r'[^\d.]', '', s) or -1))
    exp['aspect_ratio'] = ('%g-%g' % (T['ar_min'], T['ar_max']),
                           lambda s: tuple(float(x) for x in re.findall(r'\d+\.?\d*', s)))
    return exp


def check_thresholds(spec):
    exp = th_contract()
    found = {}
    for m in re.finditer(r'^[-*]\s*(\w+)\s*=\s*(.+?)\s*$', spec, re.M):
        if m.group(1) in exp:
            found[m.group(1)] = m.group(2)
    problems = []
    notes = []
    for key, (want, parse) in sorted(exp.items()):
        if key not in found:
            problems.append("SPEC 阈值表缺 %s（代码里的这一条没有文档口径）" % key)
            continue
        got = found[key]
        if key in ('video_dur', 'morph_dur', 'aspect_ratio'):
            ok = parse(got) == parse(want)
        elif got == want:
            ok = True
        else:
            ok = parse(got) == parse(want)
        if ok:
            notes.append("阈值 %s = %s（与代码一致）" % (key, got))
        else:
            problems.append("SPEC 写 %s = %s，代码实际是 %s" % (key, got, want))
    # §7.1 必须覆盖 TH 的每一个键。旧写法只比对"文档里写了的键"，所以**漏写的键永远不会被发现**
    # （ar_min/ar_max、video_dur_*、morph_ms_* 三条长期只在代码里、文档查不到就是这么躲过去的）；
    # 而那句"逐键对账（N 个键"的正则从未匹配上 SPEC 的实际措辞，等于没锁。
    import caofeng as _C
    i71 = spec.find(u'7.1 阈值表')
    j71 = spec.find(u'\n## ', i71 + 10) if i71 >= 0 else -1
    sec71 = spec[i71:j71] if i71 >= 0 and j71 > i71 else u''
    if not sec71:
        problems.append(u'SPEC 里找不到 §7.1 阈值表（逐键对账没有对象）')
    else:
        undocumented = [k for k in _C.TH if k not in sec71]
        if undocumented:
            problems.append(u'TH 有 %d 个键没写进 SPEC §7.1：%s' % (
                len(undocumented), ', '.join(undocumented[:8])))
        else:
            notes.append(u'TH 全部 %d 个键都能在 SPEC §7.1 查到' % len(_C.TH))
    return problems, notes


def check_card_gate_words():
    """卡里 gate 字段点名的缺陷词，必须在引擎里真的有一条报错文案对应。

    第六轮实测到 `giant-numeral` 卡一直写着"G8 孤字"，而 G8 从来没实现过孤字检查——
    闸编号存在不代表那条检查存在。这里把 gate 文案里的中文关键词抽出来，
    逐个回 `caofeng.py` 找，找不到就是空头支票。
    """
    problems, notes = [], []
    fp = os.path.join(SKILL, 'kb', 'deck_tech.json')
    cp = os.path.join(HERE, 'caofeng.py')
    if not (os.path.exists(fp) and os.path.exists(cp)):
        return [u'deck_tech.json 或 caofeng.py 缺失'], []
    code = io.open(cp, encoding='utf-8').read()
    cards = json.load(io.open(fp, encoding='utf-8')).get('cards') or []
    # 只查这些"具体缺陷"词；"版式/节奏/可编辑"这类是闸名，不算承诺
    DEFECT = [u'孤字', u'越界', u'压行', u'修复', u'顺序', u'署名', u'水印', u'低清',
              u'相关度', u'透明', u'帧', u'时长', u'单元格', u'可编辑']
    seen = set()
    for c in cards:
        gate = c.get('gate') or ''
        for w in DEFECT:
            if w in gate:
                seen.add((c['id'], w))
    missing = [(cid, w) for cid, w in sorted(seen) if w not in code]
    for cid, w in missing:
        problems.append(u'卡 %s 的 gate 点名"%s"，但 caofeng.py 里没有任何对应检查文案' % (cid, w))
    if not missing:
        notes.append(u'卡里 gate 点名的 %d 个缺陷词全部在引擎里有对应检查' % len(seen))
    return problems, notes


def tutorial_audit(n_mp4, missing, doc_n, have_txt, stored):
    """教程目录 ↔ 稿库 ↔ SPEC 文案三条账，做成纯函数：正反例都能拿假数据喂它自证。

    2026-11-09 的教训是"coverage 说转写了 253 份，用户那个教程目录 24 条里其实只有 2 条有稿"——
    **汇总数会自欺**，只有拿每个 mp4 的文件名（就是它自己的 md5）逐条查稿在不在才算数。
    """
    p, n = [], []
    if not n_mp4:
        return p, [u'教程目录不在本机（没有 mp4），逐条对账跳过——本项不计入结论']
    if missing:
        p.append(u'教程目录有 %d 条 mp4 在 kb/transcripts 里没有逐字稿（%s）——"已全量研习"的说法不成立'
                 % (len(missing), u'、'.join(x[:12] for x in missing[:3])))
    if doc_n is None:
        p.append(u'SPEC §18.3 里找不到「实有 **N 条**教程」这句（对账器自己瞎，本项作废）')
    elif doc_n != n_mp4:
        p.append(u'SPEC §18.3 写教程 %d 条，目录实际 %d 条' % (doc_n, n_mp4))
    want = int(stored or 0) + n_mp4
    if have_txt != want:
        p.append(u'kb/transcripts 实际 %d 份 ≠ 语料稿 %s + 教程稿 %d（应 %d）——'
                 u'研读台账与稿库对不上，要么有稿没记账，要么记了稿不在'
                 % (have_txt, stored, n_mp4, want))
    if not p:
        n.append(u'教程逐条对账闭合：%d 条 mp4 全有稿、SPEC 条数与目录一致、稿库 %d = 语料 %s + 教程 %d'
                 % (n_mp4, have_txt, stored, n_mp4))
    return p, n


def check_coverage_numbers():
    """README/SKILL 里那些"研读覆盖"数字必须等于 kb/coverage.json。

    第六轮复验实测到 README 写 166 个模板/1038 条/182 条讲解/249 份逐字稿，
    coverage.json 却是 167/1041/184/252——这类数一旦手抄就再没人回头核。
    """
    problems, notes = [], []
    try:
        cov = json.load(io.open(os.path.join(SKILL, 'kb', 'coverage.json'), encoding='utf-8'))
    except Exception as e:
        return ["kb/coverage.json 读不出来：%s" % str(e)[:60]], []
    claims = [(u'(\d+) 个模板、', 'decks_total'), (u'(\d+) 页 OOXML', 'slides_total'),
              (u'(\d+) 段真实 AI 提示词', 'prompt_blocks'),
              (u'(\d+) 条去重教程视频', 'unique_videos'),
              (u'(\d+) 个模板内嵌的', 'decks_with_embedded_video'),
              (u'内嵌的 (\d+) 条条目', 'video_entries_total'),
              (u'(\d+) 条全部抽帧', 'videos_frames_extracted'),
              (u'(\d+) 份逐字稿入库', 'transcripts_stored'),
              (u'(\d+) 条转出讲解内容', 'videos_with_speech'),
              (u'(\d+) 条无声或纯配乐', 'videos_silent_or_music')]
    txt = u''
    for f in ('README.md', 'SKILL.md'):
        fp = os.path.join(SKILL, f)
        if os.path.exists(fp):
            txt += io.open(fp, encoding='utf-8').read()
    for pat, key in claims:
        m = re.search(pat, txt)
        if not m:
            problems.append(u'文档里找不到「%s」这条研读数字（coverage.%s）' % (pat, key))
            continue
        got, want = int(m.group(1)), cov.get(key)
        if want is None:
            problems.append(u'coverage.json 没有键 %s' % key)
        elif got != want:
            problems.append(u'文档写 %s=%d，coverage.json 实际 %d' % (key, got, want))
        else:
            notes.append(u'研读数字 %s=%d 与 coverage.json 一致' % (key, got))
    # 公开版已移除第三方逐字稿（kb/transcripts/）与研习脚本，相关"稿库↔教程目录"
    # 对账的对象不再分发；coverage.json 里的数字作为研习史实在文档里保留，但不再
    # 要求稿库文件在场，否则公开包会因缺文件而误报。
    if not os.path.isdir(os.path.join(SKILL, 'kb', 'transcripts')):
        notes.append(u'公开版无 kb/transcripts/，教程逐条对账与稿库清单对账跳过（研习期专属）')
        return problems, notes
    # 教程目录（用户另投喂、要求逐条同颗粒度研习的那批）逐条对账，不拿任何汇总数当结论。
    try:
        import corpus_path as CP
        _cdir = CP.corpus_dir()
    except Exception:
        _cdir = None
    tdir = os.path.join(os.path.dirname(os.path.normpath(_cdir)), u'PPT视频教程') if _cdir else u''
    tvid = sorted(glob.glob(os.path.join(tdir, u'*.mp4'))) if tdir and os.path.isdir(tdir) else []
    _tr = {os.path.basename(x)[:-4] for x in
           glob.glob(os.path.join(SKILL, 'kb', 'transcripts', '*.txt'))}
    _spec_t = re.search(u'实有 \\*\\*(\\d+) 条\\*\\*教程', doc_text('SPEC.md'))
    _tp, _tn = tutorial_audit(len(tvid),
                              [os.path.basename(x) for x in tvid
                               if os.path.basename(x)[:-4] not in _tr],
                              int(_spec_t.group(1)) if _spec_t else None,
                              len(_tr), cov.get('transcripts_stored'))
    problems.extend(_tp)
    notes.extend(_tn)
    # 发出去的清单（skill.json 的 contents.corpus）也吃同一把尺：它以前写着
    # tutorial_videos_transcribed=253，把"语料内嵌视频去重数"当成"教程目录转写数"，正是 ④ 那类混淆。
    try:
        _sj = json.load(io.open(os.path.join(SKILL, 'skill.json'), encoding='utf-8'))
        _cc = _sj.get('contents', {}).get('corpus') or {}
        _acc = [(u'tutorial_videos_transcribed', len(tvid), u'教程目录条数', bool(tvid)),
                (u'corpus_videos_dedup', cov.get(u'videos_frames_extracted'), u'语料内嵌视频去重数', True)]
        _sjbad = 0
        for _k, _want, _lbl, _checkable in _acc:
            if _k not in _cc:
                _sjbad += 1
                problems.append(u'skill.json contents.corpus 缺键 %s（%s没有定义点，装出去的包就成了手写数）'
                                % (_k, _lbl))
            elif not _checkable:
                continue
            elif _cc[_k] != _want:
                _sjbad += 1
                problems.append(u'skill.json 写 %s=%s，实测 %s（%s）' % (_k, _cc[_k], _want, _lbl))
        if not _sjbad:
            notes.append(u'skill.json 语料清单与实测一致（语料去重 %s 条%s）'
                         % (_cc.get(u'corpus_videos_dedup'),
                            u'、教程 %s 条' % _cc.get(u'tutorial_videos_transcribed') if tvid
                            else u'（教程目录不在本机，该条未验）'))
    except Exception as e:
        problems.append(u'skill.json 读不出来，语料清单没法对账：%s' % str(e)[:60])
    if tvid:
        # 断言要自证咬得住：同一把尺喂"缺稿 + 台账不闭合"的假账必须报错，喂一致的真账不许报错
        _gp, _gn = tutorial_audit(len(tvid), [], len(tvid), len(_tr), cov.get('transcripts_stored'))
        _bp, _bn = tutorial_audit(len(tvid), [u'deadbeef' * 4], len(tvid) - 1, len(_tr) + 1,
                                  cov.get('transcripts_stored'))
        if _gp:
            problems.append(u'教程对账正例被误报 %d 条（%s）——判据与真目录没对齐'
                            % (len(_gp), _gp[0][:40]))
        if len(_bp) < 2:
            problems.append(u'教程对账反证不咬（假账只报出 %d 条，应≥2：缺稿、台账不闭合）——这条对账是哑的'
                            % len(_bp))
    return problems, notes


def check_slot_tables():
    """prompt-grammar.md 的槽位覆盖率表必须逐格等于 kb/prompt_stats.json。

    第六轮复验实测到旧表写"定格收束 6/22"，而全语料含"定格"的只有 4 块、视频块内 3 块，
    这个数**不可能成立**；还有三行（动效事件/禁项负面/运动约束）根本没有检测器。
    """
    problems, notes = [], []
    fp = os.path.join(SKILL, 'references', 'prompt-grammar.md')
    sp = os.path.join(SKILL, 'kb', 'prompt_stats.json')
    if not (os.path.exists(fp) and os.path.exists(sp)):
        return [u'prompt-grammar.md 或 prompt_stats.json 缺失'], []
    st = json.load(io.open(sp, encoding='utf-8')).get('slot_coverage_by_kind') or {}
    txt = io.open(fp, encoding='utf-8').read()
    for kind, marker in (('video', u'视频块 n='), ('image', u'图片块 n=')):
        d = st.get(kind)
        i = txt.find(marker)
        if not d:
            problems.append(u'prompt_stats.json 缺 slot_coverage_by_kind.%s' % kind)
            continue
        if i < 0:
            problems.append(u'prompt-grammar.md 找不到「%s」这张表' % marker)
            continue
        seg = txt[i:]
        nxt = seg.find(u'\n## ', 5)          # 只吃这张表所在的小节，别把下一张表读进来
        if nxt > 0:
            seg = seg[:nxt]
        m = re.search(r'n=(\d+)', seg)
        n = int(m.group(1)) if m else -1
        if n != d['n']:
            problems.append(u'%s 表分母写 n=%d，prompt_stats 实际 %d' % (kind, n, d['n']))
        rows = re.findall(r'^\| ([^|]+?) \| (\d+)/(\d+) \|', seg, re.M)
        if len(rows) < 8:
            problems.append(u'%s 表只解析到 %d 行，槽位表被改写得太多了' % (kind, len(rows)))
        bad = []
        for lab, got, den in rows:
            lab = lab.strip()
            if u'/' in lab:          # "时长 / 首尾帧 / …" 这种合并行不逐格对
                continue
            want = d['slots'].get(lab)
            if want is None:
                bad.append(u'%s：prompt_stats 里没有这个检测器' % lab)
            elif int(got) != want or int(den) != d['n']:
                bad.append(u'%s：文档 %s/%s，实测 %d/%d' % (lab, got, den, want, d['n']))
        if bad:
            problems.append(u'%s 槽位表 %d 行与实测不符：%s' % (kind, len(bad), u'；'.join(bad[:3])))
        else:
            notes.append(u'%s 槽位表 %d 行逐格与 prompt_stats 一致' % (kind, len(rows)))
    return problems, notes


def check_card_counts():
    """Technique-card counts in prose must equal kb/deck_tech.json, and cinema-recipes.md must
    actually contain that many cards (audit4 低9: the shipped report still said 10 of 12)."""
    import json
    problems, notes = [], []
    try:
        dt = json.load(io.open(os.path.join(SKILL, 'kb', 'deck_tech.json'), encoding='utf-8'))
        n_cards = len(dt.get('cards') or [])
    except Exception as e:
        return ["kb/deck_tech.json 读不出来：%s" % str(e)[:60]], []
    if not n_cards:
        return ["kb/deck_tech.json 里一张卡都没有"], []
    for f in ['SKILL.md', 'README.md', 'SPEC.md', os.path.join('references', 'cinema-recipes.md'),
              os.path.join('references', 'pipeline.md'), os.path.join('references', 'prompt-grammar.md'),
              os.path.join('references', 'model-routing.md')]:
        t = doc_text(f)
        for mm in re.finditer(r'(\d{1,2})\s*(?:张(?:实测)?技法卡|张卡)', t):
            if int(mm.group(1)) != n_cards:
                problems.append("%s 写「%s 张技法卡」，kb 实际 %d 张" % (f, mm.group(1), n_cards))
            else:
                notes.append("%s 技法卡口径一致（%d 张）" % (f, n_cards))
    cr = doc_text(os.path.join('references', 'cinema-recipes.md'))
    n_doc = len(re.findall(r'^## 卡 ', cr, re.M))
    kb_ids = [c['id'] for c in (dt.get('cards') or [])]
    missing_ids = [i for i in kb_ids if i not in cr]
    if n_doc != n_cards:
        problems.append("cinema-recipes.md 只有 %d 张卡的正文，kb 有 %d 张" % (n_doc, n_cards))
    else:
        notes.append("cinema-recipes 逐卡正文 %d 张，与 kb 数量一致" % n_cards)
    if missing_ids:
        problems.append("这些技法卡在 cinema-recipes.md 里没有对应章节：%s" % missing_ids[:4])
    return problems, notes


def check_corpus_tables():
    """corpus_tables.py 的每一格实测数字都必须能在 kb 的原始 JSON 里查到同一个数。

    Why: 色板、版式、动画三张表是引擎的默认值来源。手抄一次就会在语料重算后过期，
    而引擎照着过期数字装配、文档照着过期数字吹——这条把"表里的数字"钉回"量出来的数字"。
    """
    import corpus_tables as CT
    problems, notes = [], []
    cs = kb_json('color_system.json')
    la = kb_json('layout_archetypes.json')
    am = kb_json('animation_mechanics.json')
    eg = kb_json('exemplar_grades.json')
    if not all((cs, la, am, eg)):
        return ["kb 缺 color_system/layout_archetypes/animation_mechanics/exemplar_grades "
                "之一（跑 study_style_anim.py + grade_exemplars.py + build_kb.py）"], []

    # 1) 色板里出现的每个十六进制值，必须是语料真用过的色（按角色统计或家族主色）
    measured = set()
    for role, d in (cs.get('colors_by_role') or {}).items():
        measured.update(k.split('|')[-1].upper() for k in d)
    for hx, _n in (cs.get('theme_accent_colors') or []):
        measured.add((hx or '').upper())
    for fam, v in (cs.get('palette_families') or {}).items():
        for row in v.get('examples') or []:
            measured.add((row.get('lead') or '').upper())
            for hx in (row.get('palette_used') or []):
                measured.add((hx or '').upper())
    for pair, _n in (cs.get('gradient_top_pairs') or []):
        measured.update(x.upper() for x in pair.split(','))
    bad = []
    for name, p in CT.PALETTES.items():
        for k in ('base_hex', 'text_hex', 'title_hex', 'accent_hex', 'cyan_hex', 'gold_hex',
                  'panel_hex', 'dark_hex'):
            hx = (p.get(k) or '').upper()
            if hx and hx not in measured:
                bad.append('%s.%s=%s' % (name, k, hx))
        for _pos, hx in (p.get('grad') or []):
            if hx.upper() not in measured:
                bad.append('%s.grad=%s' % (name, hx))
    if bad:
        problems.append("色板里有没量过的色值（必须在 kb/color_system.json 出现）：%s"
                        % ','.join(bad[:8]))
    else:
        notes.append("六个色板 %d 个色值全部回锚到 kb/color_system.json" %
                     sum(8 + len(p.get('grad') or []) for p in CT.PALETTES.values()))

    # 2) 版式原型：页数/占比与 layout_archetypes 对账，好示范数与 exemplar_grades 对账
    share_all = la.get('archetype_share') or {}
    share_good = eg.get('archetype_share_good') or {}
    mism = []
    for name, row in CT.LAYOUTS.items():
        a = share_all.get(name)
        if not a or a['pages'] != row['pages'] or abs(a['pct'] - row['pct']) > 0.15:
            mism.append('%s 原始数 %s vs kb %s' % (name, (row['pages'], row['pct']),
                                                   a and (a['pages'], a['pct'])))
        g = share_good.get(name)
        if 'good_pages' in row:
            if not g or g['pages'] != row['good_pages'] or abs(g['pct'] - row['good_pct']) > 0.15:
                mism.append('%s 好示范数 %s vs kb %s' % (name, (row.get('good_pages'),
                                                                row.get('good_pct')),
                                                         g and (g['pages'], g['pct'])))
    for k, v in (CT.PLACEHOLDER_SLOTS.get('counts') or {}).items():
        if (la.get('placeholder_types') or {}).get(k) != v:
            mism.append('占位符 %s 数 %s vs kb %s' % (k, v, la.get('placeholder_types', {}).get(k)))
    for k, box in (CT.PLACEHOLDER_SLOTS.items()):
        if k == 'counts':
            continue
        kb_box = (la.get('placeholder_median_box') or {}).get(k)
        if not kb_box or [round(x, 3) for x in kb_box] != [round(x, 3) for x in box]:
            mism.append('占位符 %s 中位框 %s vs kb %s' % (k, box, kb_box))
    if mism:
        problems.append("版式数字与 kb 不一致：%s" % '；'.join(mism[:6]))
    else:
        notes.append("版式 %d 个原型的页数/占比/好示范数 + 占位符几何全部与 kb 对账一致"
                     % len(CT.LAYOUTS))

    # 3) 动画三元组：必须是语料真出现过的组合，次数与册数一致
    combos = am.get('combo_signatures') or {}
    amis = []
    for name, spec in CT.ANIM.items():
        key = '%s:%s:%s' % spec['triple']
        row = combos.get(key)
        if not row:
            amis.append('%s 的 %s 在语料里没出现过' % (name, key))
            continue
        if row['n'] != spec['n']:
            amis.append('%s n=%s vs kb %s' % (name, spec['n'], row['n']))
        if spec.get('decks') is not None and row['decks'] != spec['decks']:
            amis.append('%s decks=%s vs kb %s' % (name, spec['decks'], row['decks']))
    for name, spec in CT.ANIM.items():
        f = spec.get('filter')
        if f and f not in (am.get('animEffect_filters') or {}):
            amis.append('%s 的 filter=%s 语料里没有' % (name, f))
    if amis:
        problems.append("动画表与 kb/animation_mechanics.json 不一致：%s" % '；'.join(amis[:6]))
    else:
        notes.append("动画 %d 条 preset 三元组（含 filter 签名）全部在语料实测表里" % len(CT.ANIM))

    # 4) 示范质量分层数字
    ex = CT.EXEMPLAR
    eis = []
    KEYMAP = {'pages': 'n_pages', 'good_pages': 'n_good', 'bad_pages': 'n_bad',
              'junk_decks': 'n_junk_decks'}
    for k, kk in KEYMAP.items():
        if ex[k] != eg.get(kk):
            eis.append('%s=%s vs kb %s' % (k, ex[k], eg.get(kk)))
    for k in ('chars_median', 'max_pt_median', 'anim_pages', 'media_pages'):
        if ex['good_page_metrics'].get(k) != (eg.get('good_pages') or {}).get(k):
            eis.append('好示范 %s=%s vs kb %s' % (
                k, ex['good_page_metrics'].get(k), (eg.get('good_pages') or {}).get(k)))
    if abs(ex['bad_share_pct'] - eg.get('bad_share_pct', -1)) > 0.15:
        eis.append('bad_share_pct=%s vs kb %s' % (ex['bad_share_pct'], eg.get('bad_share_pct')))
    for reason, n in ex['reasons'].items():
        if eg.get('reasons', {}).get(reason) != n:
            eis.append('判据 %s=%s vs kb %s' % (reason, n, eg.get('reasons', {}).get(reason)))
    if eis:
        problems.append("示范质量数字与 kb/exemplar_grades.json 不一致：%s" % '；'.join(eis[:6]))
    else:
        notes.append("好示范 %d 页 / 坏示范 %d 页 / %d 条判据与 kb 逐格一致" % (
            ex['good_pages'], ex['bad_pages'], len(ex['reasons'])))

    # 5) 视频交互与转场的引用数
    vis = []
    if CT.VIDEO_CLICK['pages'] != am.get('interactive_seq_pages'):
        vis.append('interactiveSeq 页数 %s vs kb %s' % (CT.VIDEO_CLICK['pages'],
                                                        am.get('interactive_seq_pages')))
    if CT.VIDEO_CLICK['decks'] != len(am.get('interactive_seq_decks') or []):
        vis.append('interactiveSeq 册数 %s vs kb %s' % (CT.VIDEO_CLICK['decks'],
                                                        len(am.get('interactive_seq_decks') or [])))
    calls = am.get('cmd_type_and_call') or {}
    if calls.get('call|playFrom') != CT.VIDEO_CLICK['playFrom']:
        vis.append('playFrom %s vs kb %s' % (CT.VIDEO_CLICK['playFrom'], calls.get('call|playFrom')))
    if calls.get('call|togglePause') != CT.VIDEO_CLICK['togglePause']:
        vis.append('togglePause %s vs kb %s' % (CT.VIDEO_CLICK['togglePause'],
                                                calls.get('call|togglePause')))
    if vis:
        problems.append("视频点击链数字与 kb 不一致：%s" % '；'.join(vis))
    else:
        notes.append("视频交互链（playFrom/togglePause/interactiveSeq）四项与 kb 一致")

    # 6) 判据阈值必须在 SPEC 里有对应文字，否则"拦什么"只存在于代码里
    spec = doc_text('SPEC.md')
    for key, val in (('dense_chars_min', CT.BAD_RULES['dense_chars_min']),
                     ('tiny_pt_max', CT.BAD_RULES['tiny_pt_max']),
                     ('text_zone_lum_max', CT.BAD_RULES['text_zone_lum_max'])):
        if str(val) not in spec:
            problems.append("坏示范判据 %s=%s 没写进 SPEC（G19 拦的阈值文档必须能查到）"
                            % (key, val))

    # 7) 同级基线必须只用好示范页校准（非模版元素不学习沉淀），且 SPEC §16 靶子与基线一致
    pb = kb_json('parity_baseline.json')
    pbs = []
    if not pb:
        pbs.append('缺 kb/parity_baseline.json（跑 deck_parity.py --calibrate）')
    else:
        if pb.get('calibration') != 'good_pages_only':
            pbs.append('基线口径=%s（必须 good_pages_only：非模版元素不学习沉淀）'
                       % pb.get('calibration'))
        if not (pb.get('excluded_bad_pages', 0) > 0 and pb.get('skipped_junk_decks', 0) > 0):
            pbs.append('基线没真剔坏页/junk 册（excluded_bad=%s skipped_junk=%s）'
                       % (pb.get('excluded_bad_pages'), pb.get('skipped_junk_decks')))
        badc = {}
        for row in (eg.get('page_index') or []):
            badc[row[0]] = badc.get(row[0], 0) + 1
        leaked = [r['deck'] for r in (pb.get('per_deck') or [])
                  if badc.get(r['deck'], 0) >= r['pages']]
        if leaked:
            pbs.append('junk 册混进基线（好页<=坏页）：%s' % '、'.join(leaked[:4]))
        for key, label in (('chars_p50', '每页字数 p50'), ('chars_p90', '每页字数 p90'),
                           ('hero_rate', '大数字率'), ('picture_rate', '有图率')):
            med = (pb.get(key) or {}).get('median')
            if med is None:
                continue
            present = str(med) in spec or (med == int(med) and str(int(med)) in spec)
            if not present:
                pbs.append('SPEC §16 查不到基线中位 %s=%s（文档与基线两套口径）' % (label, med))
    if pbs:
        problems.append("同级基线没把非模版元素剔干净或与 SPEC 不一致：%s" % '；'.join(pbs[:6]))
    else:
        notes.append("同级基线 good_pages_only（剔 %s 坏页/跳 %s junk 册），per_deck 无 junk，"
                     "SPEC §16 靶子与基线一致" % (pb.get('excluded_bad_pages'),
                                                  pb.get('skipped_junk_decks')))
    return problems, notes


# ---------------------------------------------------------------------------
# 指引可执行性对账。2026-11-09 做了一次"干净上下文复验"：派一个没读过对话的代理只按
# SKILL/SPEC 复述流程，它抓出六类"文档自己跑不通"的硬伤——
#   ① bash 块里没有 refine / ack 的命令行，而 build 没 ack 会退出码 1（序列照抄必然卡死）；
#   ② fetch 行写 --min-edge 1080，代码默认 1920（照抄就产出会被 G3 拦的素材）；
#   ③ 声明"29 道闸"但清单只数到 G28（agent 只读 SKILL 就不知道有开场解剖闸）；
#   ④ 引擎可产出卡数三处分别写 15 / 10 / 8，代码 RECIPE_TOKENS 实际 12；
#   ⑤ 过渡页编号字号 SPEC 里 120pt 与 96pt 并存，SKILL 一个字没写；
#   ⑥ G28 视频判据三处各说一套（角区启发式 / 时序检测 / 原件模板 0.45）。
# 这类洞靠自觉补不住，所以每条都做成断言，并配一组"把文档改坏必须报错"的差分反证。

def _sec(text, start, end=None):
    i = text.find(start)
    if i < 0:
        return ''
    j = text.find(end, i + len(start)) if end else -1
    return text[i:j if j > 0 else len(text)]


def _flat(text):
    return re.sub(r'\s+', ' ', text)


def _rep_span(text, anchor, old, new, span=1500):
    i = text.find(anchor)
    if i < 0:
        return text
    seg = text[i:i + span].replace(old, new, 1)
    return text[:i] + seg + text[i + span:]


def _rep_in_sec(text, sec_start, sec_end, old, new):
    """在某个小节内部替换：闸清单从标题到 G29 行有两三千字，固定 span 会替换不到。"""
    i = text.find(sec_start)
    if i < 0:
        return text
    j = text.find(sec_end, i + len(sec_start))
    j = j if j > 0 else len(text)
    return text[:i] + text[i:j].replace(old, new, 1) + text[j:]


def guidance_facts():
    src = io.open(os.path.join(HERE, 'caofeng.py'), encoding='utf-8').read()
    f = {}
    m = re.search(r'^RECIPE_TOKENS = \{(.*?)^\}', src, re.S | re.M)
    f['tokens'] = re.findall(r"^    ['\"]([A-Za-z0-9_-]+)['\"]\s*:", m.group(1), re.M) if m else []
    # 子命令名可以带连字符（fetch-video / materials-review）——旧正则只认 [a-z]+，
    # 于是 14 个子命令只对账了 12 个，那两个从来没被检查过。
    f['subcmds'] = sorted(set(re.findall(r"sub\.add_parser\('([a-z][a-z-]*)'", src)))
    i = src.find("sub.add_parser('fetch')")
    nxt = src.find("sub.add_parser(", i + 22)
    mm = re.search(r"--min-edge', type=int, default=(\d+)", src[i:nxt if nxt > i else i + 3000])
    f['fetch_min_edge'] = int(mm.group(1)) if mm else None
    # 相关度这把尺：SPEC §7 G3 承诺"fetch 筛选、G3 复核、装配拒用三处同一把尺子"。
    # 2026-11-13 审计抓到 fetch 的 argparse 默认值写死 34，而 G3/装配读 TH['relevance_min']=50，
    # 承诺是空的。这里把"默认值到底引用 TH 还是写死数字"原样抓出来，交给 check_guidance 判。
    mm = re.search(r"--min-rel',\s*type=int,\s*default=([^,\n)]+)", src[i:nxt if nxt > i else i + 3000])
    f['fetch_min_rel'] = mm.group(1).strip() if mm else None
    import caofeng as _C
    f['rel_min_th'] = _C.TH['relevance_min']
    mm = re.search(r"fit_pt\(_num,[^\[]*\[([0-9,\s]+)\]", src)
    f['num_ladder'] = sorted(int(x) for x in re.findall(r'\d+', mm.group(1))) if mm else []
    g28 = _sec(src, 'def g28_watermark', '\ndef ')
    f['wm_thresh'] = sorted({x.group(1) for x in re.finditer(r'thresh=([\d.]+)', g28)})
    kb = kb_json('deck_tech.json') or {}
    f['cards'] = len(kb.get('cards') or [])
    return f


def check_guidance(facts, skill, spec, gate_ids):
    p, n = [], []
    nt = len(facts['tokens'])
    lad = facts['num_ladder']

    gsec = _sec(skill, u'## 机器闸', u'\n## ')
    listed = set(int(x) for x in re.findall(r'G(\d+)', gsec))
    miss = [g for g in gate_ids if g not in listed]
    if miss:
        p.append(u'SKILL 机器闸清单缺 G%s（代码里有，只读 SKILL 的 agent 不知道有这道闸）'
                 % ','.join(map(str, miss)))
    else:
        n.append(u'SKILL 机器闸清单覆盖全部 %d 道闸' % len(gate_ids))

    flat = _flat(skill)
    nofn = [c for c in facts['subcmds']
            if ('caofeng.py %s ' % c) not in flat and ('%s --work' % c) not in flat]
    if nofn:
        p.append(u'SKILL 里没有可直接照抄的命令行：%s' % ' '.join(nofn))
    else:
        n.append(u'SKILL 命令序列可直接照抄（%d 个子命令都有写法，含 refine/ack）' % len(facts['subcmds']))

    s7 = _sec(spec, u'## 7.', u'## 8.')
    for label, hay in ((u'SKILL', skill), (u'SPEC §7', s7)):
        nums = [int(x) for x in re.findall(u'那\\s*(\\d+)\\s*项', hay)]
        if nums and set(nums) != {nt}:
            p.append(u'%s 写「那 %s 项」引擎可产出，代码 RECIPE_TOKENS 实际 %d 项'
                     % (label, '/'.join(map(str, sorted(set(nums)))), nt))
        elif nums:
            n.append(u'%s 引擎可产出卡数与代码一致（%d 项）' % (label, nums[0]))
    absent = [t for t in facts['tokens'] if t not in s7]
    if absent:
        p.append(u'SPEC §7 的 G6 行没列全引擎可产出卡：%s' % ' '.join(absent))
    else:
        n.append(u'SPEC §7 G6 行逐名列出 %d 张引擎可产出卡' % nt)

    for label, hay in ((u'SKILL', skill), (u'SPEC', spec)):
        cnt = [int(x) for x in re.findall(u'全库共\\s*(\\d+)\\s*张技法卡', hay)]
        if cnt and set(cnt) != {facts['cards']}:
            p.append(u'%s 写技法库 %s 张，kb/deck_tech.json 实际 %d 张'
                     % (label, '/'.join(map(str, sorted(set(cnt)))), facts['cards']))
        elif cnt:
            n.append(u'%s 技法库张数与 kb 一致（%d 张）' % (label, cnt[0]))

    line = [l for l in skill.splitlines() if 'caofeng.py fetch' in l]
    if not line:
        p.append(u'SKILL 的 bash 块里没有 `caofeng.py fetch` 这一行')
    elif facts['fetch_min_edge'] and \
            ('--min-edge %d' % facts['fetch_min_edge']) not in _flat(line[0]):
        p.append(u'SKILL 的 fetch 行门槛与代码默认值不一致（代码 --min-edge %d）'
                 % facts['fetch_min_edge'])
    else:
        n.append(u'SKILL fetch 行带的 --min-edge 就是代码默认值（%d）' % facts['fetch_min_edge'])

    # 相关度这把尺只能有一个定义点：SPEC §7 G3 明写"fetch 筛选、G3 复核、装配拒用三处同一把尺子"，
    # 而 2026-11-13 审计抓到 fetch 的 argparse 默认值写死 34、G3/装配读 TH['relevance_min']=50，
    # 那句承诺是空的。默认值一旦写成字面量，TH 改动时它就静默过期，所以这里连"写法"一起判。
    _th_rel, _d = facts['rel_min_th'], facts['fetch_min_rel']
    if not _d:
        p.append(u'caofeng.py 的 --min-rel 默认值没匹配到（对账器自己瞎，本项作废）')
    elif u'TH[' not in _d or u'relevance_min' not in _d:
        p.append(u'fetch --min-rel 默认值写成 %s，是相关度的第二个定义点；'
                 u'G3 复核与装配拒用读 TH["relevance_min"]=%d，SPEC §7 承诺的"三处同一把尺子"不成立'
                 % (_d, _th_rel))
    else:
        n.append(u'fetch --min-rel 与 G3/装配同一定义点（TH["relevance_min"]=%d）' % _th_rel)
    _doc_rel = []
    for _lbl, _hay in ((u'SPEC §7 G3', _flat(s7)),
                       (u'pipeline G3', _flat(doc_text(os.path.join('references', 'pipeline.md'))))):
        for _m in re.finditer(u'相关度≥(\\d+)\\s*[%％]', _hay or u''):
            _doc_rel.append((_lbl, int(_m.group(1))))
    if not _doc_rel:
        p.append(u'SPEC §7 / pipeline 都没匹配到"相关度≥N%"这句（对账器自己瞎，本项作废）')
    else:
        _off = [(l, v) for l, v in _doc_rel if v != _th_rel]
        if _off:
            p.append(u'%s 写相关度≥%d%%，代码 TH["relevance_min"]=%d'
                     % (u'、'.join(l for l, _ in _off), _off[0][1], _th_rel))
        else:
            n.append(u'相关度阈值文档↔代码逐处一致（%d 处，均 %d%%）' % (len(_doc_rel), _th_rel))

    k = spec.find(u'`DL.fit_pt`')
    win = _flat(spec[k:k + 320]) if k >= 0 else ''
    mm = re.search(r'([0-9]+(?:/[0-9]+)+)\s*pt', win)
    doc_lad = sorted(int(x) for x in mm.group(1).split('/')) if mm else []
    if lad and doc_lad != lad:
        p.append(u'SPEC §18 的过渡页字号阶梯 %s 与代码 fit_pt 阶梯 %s 不一致'
                 % ('/'.join(map(str, doc_lad)), '/'.join(map(str, lad))))
    elif lad:
        n.append(u'SPEC §18 过渡页字号阶梯与代码同一份（%d 档，下限 %dpt）' % (len(lad), lad[0]))
    if lad and ('floor=%dpt' % lad[0]) not in skill:
        p.append(u'SKILL 没写过渡页编号字号下限（代码阶梯最低 %dpt）' % lad[0])
    elif lad:
        n.append(u'SKILL 写了编号字号下限 floor=%dpt' % lad[0])

    stale = [x for x in (u'以时序叠加层检测为唯一定义点',) if x in skill or x in spec]
    if stale:
        p.append((u'文档还在用废弃的视频水印口径：%s（§19 与代码以原件模板 %s 为唯一定义点）')
                 % (u' '.join(stale), facts['wm_thresh'][0] if facts['wm_thresh'] else '?'))
    thr = facts['wm_thresh'][0] if facts['wm_thresh'] else None
    for label, anchor in ((u'SKILL G28', u'G28 水印清除'), (u'SPEC G28', u'| G28 水印清除')):
        hay = skill if label.startswith('SKILL') else spec
        row = _flat(_sec(hay, anchor, u'\n'))
        if not row:
            p.append(u'%s 行找不到' % label)
            continue
        if thr and thr not in row:
            p.append(u'%s 没写代码实际用的水印模板阈值 %s' % (label, thr))
        elif u'原件' not in row:
            p.append(u'%s 没写"拿 AI产物 原件抠模板"这条权威判据' % label)
        else:
            n.append(u'%s 与代码同口径（原件模板 %s）' % (label, thr))
    return p, n


def check_guidance_selftest(facts, skill, spec, gate_ids):
    """断言本身要自证咬得住：把文档按六种真实漂移改坏，每一种都必须报错。"""
    p, n = [], []
    lad = facts['num_ladder']
    thr = facts['wm_thresh'][0] if facts['wm_thresh'] else '0.45'
    cases = [
        (u'闸清单删掉最后一道闸的 id',
         _rep_in_sec(skill, u'## 机器闸', u'\n## ',
                     u'G%d 开场解剖' % gate_ids[-1], u'GXX 开场解剖'), spec),
        (u'命令序列删掉 refine 写法',
         skill.replace(u'caofeng.py refine', u'caofeng.py refineX'), spec),
        (u'引擎可产出卡数改回旧数',
         skill.replace(u'那 %d 项' % len(facts['tokens']), u'那 %d 项' % (len(facts['tokens']) + 3)),
         spec),
        (u'技法库张数改错',
         skill.replace(u'全库共 %d 张技法卡' % facts['cards'],
                       u'全库共 %d 张技法卡' % (facts['cards'] - 1)), spec),
        (u'fetch 行门槛写回 1080',
         skill.replace(u'--min-edge %d' % facts['fetch_min_edge'], u'--min-edge 1080'), spec),
        (u'字号阶梯少写最低一档',
         skill, spec.replace(u'%spt' % '/'.join(map(str, lad)),
                             u'%spt' % '/'.join(map(str, lad[1:])))),
        (u'SKILL 字号下限写歪',
         skill.replace(u'floor=%dpt' % lad[0], u'floor=%dpt' % (lad[0] - 6)), spec),
        (u'G28 行阈值改回旧的 0.30',
         _rep_span(skill, u'G28 水印清除', thr, u'0.30'), spec),
        (u'文档重新出现废弃口径',
         skill.replace(u'G28 水印清除', u'G28 水印清除（**视频以时序叠加层检测为唯一定义点**'),
         spec),
        (u'SPEC §7 G6 行漏列一张可产出卡',
         skill, _rep_span(spec, u'| G6 节奏', facts['tokens'][-1], u'split-XXX')),
        (u'SPEC §7 G3 的相关度阈值写回旧的 34',
         skill, spec.replace(u'相关度≥50%（fetch 筛选', u'相关度≥34%（fetch 筛选')),
    ]
    bites = 0
    skill0, spec0 = doc_text('SKILL.md'), spec
    for label, sk, sp in cases:
        if sk == skill0 and sp == spec0:
            p.append(u'差分夹具没改动任何字节（锚点失效）：%s' % label)
            continue
        got, _ = check_guidance(facts, sk, sp, gate_ids)
        if got:
            bites += 1
        else:
            p.append(u'差分反证不咬：%s —— 断言是哑的' % label)
    # 代码侧漂移也要能咬：把 --min-rel 的默认值改回写死的 34（2026-11-13 审计 ③ 的真形态）、
    # 再把 TH 与文档阈值错开一格——两种都必须报错，否则"三处同一把尺子"这条断言是哑的。
    for label, mut in ((u'fetch --min-rel 默认值写回死数 34', {u'fetch_min_rel': u'34'}),
                       (u'相关度 TH 与文档阈值错开', {u'rel_min_th': facts['rel_min_th'] + 13})):
        got, _ = check_guidance(dict(facts, **mut), skill, spec, gate_ids)
        if got:
            bites += 1
        else:
            p.append(u'差分反证不咬（代码侧）：%s —— 断言是哑的' % label)
    clean, _ = check_guidance(facts, skill, spec, gate_ids)
    if clean:
        p.append(u'正例（当前文档）被自己的断言判了 %d 条，说明断言与真文档没对齐' % len(clean))
    else:
        n.append(u'指引可执行性差分反证 %d 项全咬住（正例零误报）' % bites)
    return p, n


def main(count=None):

    # count is passed in by test_gates.py; reading the report file when called from inside the
    # self-test is self-referential (the file is only written at the end) and always lags one run.
    code = gates_in_code()
    problems = []
    notes = []
    n_code = len(code)
    notes.append("代码里的闸：G%s（共 %d 道）" % (
        '–G'.join([str(code[0]), str(code[-1])]) if code else '?', n_code))
    if code and code != list(range(1, n_code + 1)):
        problems.append("闸编号不连续：%s" % code)

    # every doc that states a count must agree with the code
    D1 = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}

    def cn_num(s):
        """中文数字 → int，支持 六 / 十六 / 二十六 / 二十 这一类写法。

        旧实现用一张只列到「十六」的表 + 一个漏掉前导十位的正则，
        于是「二十六道闸」被读成 16、报成口径不一致（假 BAD，文档其实是对的）。
        闸数超过 20 之后这种写法必然会用到，所以这里按位值真解析。"""
        s = s.strip()
        if not s:
            return None
        if s.isdigit():
            return int(s)
        if '十' in s:
            a, _, b = s.partition(u'十')
            v = (D1.get(a, 1) if a else 1) * 10
            if b:
                if b not in D1:
                    return None
                v += D1[b]
            return v
        return D1.get(s)

    for f in ['SKILL.md', 'README.md', 'SPEC.md', os.path.join('references', 'pipeline.md')]:
        t = doc_text(f)
        for m in re.finditer(r'(\d{1,2})\s*道(?:机器)?闸', t):
            if int(m.group(1)) != n_code:
                problems.append("%s 写「%s 道闸」，代码实际 %d 道" % (f, m.group(1), n_code))
            else:
                notes.append("%s 闸数口径一致（%d）" % (f, n_code))
        # counts written in Chinese characters are the ones that rot first ("十二道闸" survived
        # three revisions while the code had grown to 15)
        for m in re.finditer(r'([一二三四五六七八九]?十[一二三四五六七八九]?|[一二三四五六七八九])'
                             r'\s*道(?:机器)?闸', t):
            v = cn_num(m.group(1))
            if v is not None and v != n_code:
                problems.append("%s 写「%s道闸」，代码实际 %d 道" % (f, m.group(1), n_code))
            elif v == n_code:
                notes.append("%s 中文闸数口径一致（%s=%d）" % (f, m.group(1), v))

    # SPEC's gate table must list every gate id present in code
    spec = doc_text('SPEC.md')
    listed = sorted({int(m) for m in re.findall(r'^\| G(\d+) ', spec, re.M)})
    missing = [g for g in code if g not in listed]
    if missing:
        problems.append("SPEC §7 表格里缺 G%s（代码里有）" % ','.join(map(str, missing)))
    else:
        notes.append("SPEC §7 表格覆盖全部 %d 道闸" % n_code)

    # 阈值逐条对账：文档里的数字与代码 TH 必须一模一样（audit4 [中]5 找到的正是这类漂移）
    try:
        th_problems, th_notes = check_thresholds(spec)
        problems.extend(th_problems)
        notes.extend(th_notes)
    except Exception as e:
        problems.append("阈值对账跑不起来：%s" % str(e)[:100])

    cp, cn = check_card_counts()
    problems.extend(cp)
    notes.extend(cn)

    # 色板/版式/动画/示范分层四张表逐格回锚 kb 原始 JSON（引擎默认值不许是手抄数）
    try:
        ctp, ctn = check_corpus_tables()
        problems.extend(ctp)
        notes.extend(ctn)
    except Exception as e:
        problems.append("实测表回锚跑不起来：%s" % str(e)[:120])

    # 研读覆盖与槽位覆盖率：这两类数是文档里最容易手抄抄错的，全部改成与 kb 的 json 逐格对账
    try:
        gwp, gwn = check_card_gate_words()
        problems.extend(gwp)
        notes.extend(gwn)
    except Exception as e:
        problems.append("卡 gate 用词对账跑不起来：%s" % str(e)[:100])
    try:
        cvp, cvn = check_coverage_numbers()
        problems.extend(cvp)
        notes.extend(cvn)
    except Exception as e:
        problems.append("研读数字对账跑不起来：%s" % str(e)[:100])
    try:
        slp, sln = check_slot_tables()
        problems.extend(slp)
        notes.extend(sln)
    except Exception as e:
        problems.append("槽位表对账跑不起来：%s" % str(e)[:100])

    # 指引可执行性：只读 SKILL/SPEC 的 agent 能不能照抄命令、文档数字与代码是不是同一份
    try:
        gf = guidance_facts()
        gup, gun = check_guidance(gf, doc_text('SKILL.md'), spec, code)
        problems.extend(gup)
        notes.extend(gun)
        sdp, sdn = check_guidance_selftest(gf, doc_text('SKILL.md'), spec, code)
        problems.extend(sdp)
        notes.extend(sdn)
    except Exception as e:
        problems.append("指引可执行性对账跑不起来：%s" % str(e)[:120])



    # every script referenced by the docs must exist
    ref = doc_text(os.path.join('references', 'pipeline.md')) + doc_text('SKILL.md') + doc_text('README.md')
    for name in sorted(set(re.findall(r'`?([a-z_]+\.py)`?', ref))):
        if name in ('verify_e2e.py',):
            problems.append("文档引用了不存在的脚本 %s" % name)
            continue
        if not os.path.exists(os.path.join(HERE, name)):
            problems.append("文档引用 %s，scripts/ 里没有" % name)

    # Self-test count: docs promise a *floor* ("30 项以上"), not an exact number. An exact promise
    # goes stale the instant a case is added, and a stale number in the docs is what we are here
    # to catch. A floor is checkable and keeps rising as the suite grows.
    st = os.path.join(os.environ.get('TEMP', ''), 'caofeng_gate_selftest', 'gate_selftest.md')
    real = count
    if real is None and os.path.exists(st):
        m = re.search(r'全部 (\d+) 项检查通过', io.open(st, encoding='utf-8').read())
        real = int(m.group(1)) if m else None
    if real:
        for f in ['SKILL.md', 'README.md', 'SPEC.md', os.path.join('references', 'pipeline.md')]:
            t = doc_text(f)
            for mm in re.finditer(r'(\d+)\s*项(?:以上)?反向(?:自证|对照)', t):
                promised = int(mm.group(1))
                if real < promised:
                    problems.append("%s 写「%d 项以上反向自证」，实测只有 %d 项" % (f, promised, real))
                elif promised < 10:
                    problems.append("%s 的反向自证承诺 %d 项过低（代码实测 %d 项）" % (f, promised, real))
                else:
                    notes.append("%s 反向自证口径达标（承诺≥%d，实测 %d）" % (f, promised, real))
            for mm in re.finditer(r'（(\d+) 项）', t):
                if real < int(mm.group(1)):
                    problems.append("%s 写「(%s 项)」，实测 %d 项" % (f, mm.group(1), real))
        notes.append("反向自证实测 %d 项" % real)

    out = io.open(os.path.join(os.environ.get('TEMP', '.'), 'caofeng_doc_check.md'), 'w', encoding='utf-8')
    out.write("# 文档-代码对账\n\n")
    for x in notes:
        out.write("- OK %s\n" % x)
    if problems:
        out.write("\n## 不一致\n\n")
        for x in problems:
            out.write("- " + x + "\n")
    else:
        out.write("\n## 结论\n\n全部一致。\n")
    out.close()
    for x in notes:
        print("OK  " + x)
    for x in problems:
        print("BAD " + x)
    if problems:
        sys.exit(1)
    print("文档与代码一致（%d 道闸）" % n_code)


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
