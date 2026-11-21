"""Adversarial self-test for the gates: prove each detector actually fires.

A gate that can only say "pass" is decoration. For every measurable gate we build a deliberately
bad fixture and require the gate to reject it, plus a good fixture it must accept.
Run: python test_gates.py
Exit 0 only if every gate bites.
"""
import os, io, sys, re, json, shutil, subprocess
import glob
import deck_measure as DM

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import caofeng as C
from PIL import Image, ImageDraw

# A GBK console cannot encode every character these scripts print; a cosmetic character must
# never abort a run or decide an exit code.
try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or 'utf-8', errors='backslashreplace')
    sys.stderr.reconfigure(encoding=sys.stderr.encoding or 'utf-8', errors='backslashreplace')
except Exception:
    pass
# children must speak UTF-8: capturing a GBK-encoded Chinese message as UTF-8 turns every
# assertion on the wording into mojibake, which is how a real fix looked like a failing test
os.environ['PYTHONIOENCODING'] = 'utf-8'

TMP_ROOT = os.path.join(os.environ.get('TEMP', '.'), 'caofeng_gate_selftest')
# a second concurrent run (another agent verifying the same skill) used to rmtree the shared
# sandbox out from under the first one, so the suite died mid-way with a missing shots.json
TMP = os.path.join(TMP_ROOT, 'run-%d' % os.getpid())

def prune_old_runs(keep=None):
    """跑一次留一个沙箱：这套自证一轮要造 0.8–1.2GB 的帧图与成品，
    以前每个 run-* 目录都留在 %TEMP% 里，79 轮就吃了 45GB。
    只删带 gate_selftest.md（本脚本自己写的结论文件）的 run-* 目录，别的不动。"""
    keep = int(os.environ.get('CAOFENG_SELFTEST_KEEPRUNS',
                              '1' if keep is None else keep))
    base = TMP_ROOT
    if not os.path.isdir(base):
        return 0, 0
    runs = []
    for d in sorted(os.listdir(base)):
        p = os.path.join(base, d)
        if not re.match(r'^run-[0-9]+$', d) or not os.path.isdir(p):
            continue
        if not os.path.exists(os.path.join(p, 'gate_selftest.md')):
            continue
        runs.append((os.path.getmtime(p), p))
    runs.sort(reverse=True)
    freed = 0
    n_del = 0
    for _, p in runs[max(0, keep):]:
        for root, dirs, files in os.walk(p):
            for f in files:
                try:
                    freed += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
        try:
            shutil.rmtree(p)
            n_del += 1
        except Exception:
            pass
    return n_del, freed




def lum_of(rgb):
    return C.rel_lum(rgb)


def measure_contrast(bg_rgb, text_rgb, pattern='photo'):
    """Re-implement the G4 estimator on a synthetic crop so we can feed it known truth."""
    W, H = 400, 120
    im = Image.new('RGB', (W, H), tuple(int(x) for x in bg_rgb))
    px = im.load()
    if pattern == 'bright_half':
        for x in range(W // 2, W):
            for y in range(H):
                px[x, y] = tuple(int(v) for v in bg_rgb)
        for x in range(0, W // 2):
            for y in range(H):
                px[x, y] = (240, 240, 240)
    # glyph strokes carry the text colour
    for y in range(20, 100, 6):
        for x in range(10, W - 10, 3):
            px[x, y] = tuple(int(v) for v in text_rgb)
    arr = [im.getpixel((x, y)) for y in range(im.height) for x in range(im.width)]
    textL = lum_of(text_rgb)
    lums = [lum_of(p) for p in arr]
    rest = [l for l in lums if abs(l - textL) > 0.08] or lums
    rest.sort()
    bg = rest[len(rest) // 2]
    return (max(textL, bg) + 0.05) / (min(textL, bg) + 0.05), bg


def main():
    fails = []
    notes = []
    # 干净沙箱：上一版跑在固定 %TEMP%\caofeng_gate_selftest 时，别人留下的旧 cli/ 会让
    # plan 之后读 shots.json 直接 FileNotFoundError，而崩退码是 1 —— 看起来像"闸拦下了不合格成品"。
    if os.path.isdir(TMP):
        shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)


    # --- G4 must reject white text on a bright photo -----------------------------
    ratio, bg = measure_contrast((235, 235, 235), (255, 255, 255))
    ok_reject = ratio < 3.0
    notes.append("G4 反证（白字压亮底）：测得 %.2f:1，要求 <3.0 -> %s" % (ratio, 'reject' if ok_reject else 'PASS(错)'))
    if not ok_reject:
        fails.append("G4 不咬：白字压在近白背景上仍被判合格")

    # --- G4 must accept white text on a properly dimmed photo --------------------
    ratio2, bg2 = measure_contrast((18, 20, 26), (255, 255, 255))
    ok_accept = ratio2 >= 4.5
    notes.append("G4 正证（白字压暗底）：测得 %.2f:1，要求 >=4.5 -> %s" % (ratio2, 'accept' if ok_accept else 'REJECT(错)'))
    if not ok_accept:
        fails.append("G4 误伤：正常压暗后的白字被判不合格")

    # --- the old (wrong) estimator would have flattered the bright case ----------
    # Real failure shape: the text box straddles a bright sky and a dark corner. The old
    # "darkest 15th percentile = background" rule grabs the dark corner and reports a healthy
    # ratio even though the glyphs actually sit on near-white.
    W, H = 400, 120
    im = Image.new('RGB', (W, H), (232, 232, 232))
    d = im.load()
    for x in range(0, int(W * 0.30)):          # the dark corner
        for y in range(H):
            d[x, y] = (14, 14, 16)
    for y in range(20, 100, 6):                # white glyphs across the whole box
        for x in range(10, W - 10, 3):
            d[x, y] = (255, 255, 255)
    lums = sorted(lum_of(im.getpixel((x, y))) for y in range(im.height) for x in range(im.width))
    dark_pct = lums[max(0, int(len(lums) * 0.15))]
    naive = (lum_of((255, 255, 255)) + 0.05) / (dark_pct + 0.05)
    # what the glyphs actually sit on: the bright three-quarters
    truth = measure_contrast((232, 232, 232), (255, 255, 255))[0]
    notes.append("对照（框内 70%% 亮 + 30%% 暗角，白字横压整框）：旧算法『取 15 分位最暗当背景』算出 %.2f:1，"
                 "而字真正压着的亮底只有 %.2f:1 -> 旧算法会放行 %s" % (
                     naive, truth, naive >= 3.0 and truth < 3.0))
    if naive >= 3.0 and truth < 3.0:
        notes.append("  -> 反向对照成立：这个用例确实只有新算法能拦下，旧算法放行")
    else:
        fails.append("反向对照不成立：旧算法 %.2f vs 真值 %.2f，没能造出『旧放行、新拦下』的分歧" % (naive, truth))

    # --- G2 prompt grammar must reject a thin prompt -----------------------------
    thin = "生成一张科技风背景图，16:9，蓝色。"
    got, flags = C.score_image_prompt(thin)
    ok_thin = len(got) < 7 or len(thin) < 350
    notes.append("G2 反证（20 字提示词）：命中段 %s，长度 %d -> %s" % (got, len(thin), 'reject' if ok_thin else 'PASS(错)'))
    if not ok_thin:
        fails.append("G2 不咬：极短提示词被判合格")

    good = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'references',
                                'prompt-grammar.md'), encoding='utf-8').read()
    import styles as S
    sample = S.image_prompt("华东区域医美机构联合运营复盘", 'tech_blue', [0.05, 0.55, 0.9, 0.36])
    got2, flags2 = C.score_image_prompt(sample)
    ok_good = len(got2) >= 7 and len(sample) >= 350 and flags2['留白占位'] and flags2['禁文字']
    notes.append("G2 正证（scaffold 产出的九段式）：命中 %d 段，%d 字，留白=%s 禁字=%s -> %s" % (
        len(got2), len(sample), flags2['留白占位'], flags2['禁文字'], 'accept' if ok_good else 'REJECT(错)'))
    if not ok_good:
        fails.append("G2 误伤：技能自己生成的合格提示词被拦")

    # --- G2 video: must require attachment naming --------------------------------
    vbad = "生成一段 8 秒 16:9 的科技感视频，画面里有粒子和光环，镜头缓慢推进，最后稳定停留。不要出现文字和水印。" * 3
    gv, fv = C.score_video_prompt(vbad)
    notes.append("G2 反证（无附件点名的视频提示词，%d 字）：段=%s，附件点名=%s -> %s" % (
        len(vbad), gv, fv['附件点名'], 'reject' if not fv['附件点名'] else 'PASS(错)'))
    if fv['附件点名']:
        fails.append("G2 不咬：没点名的视频提示词被放行")
    vgood = S.video_prompt("智能工厂物流", 'tech_blue', [0.06, 0.18, 0.88, 0.66], '附件1', secs=8)
    gv2, fv2 = C.score_video_prompt(vgood)
    # 生成器必须喂得过自己的闸：6/6 段全中。上一版它只命中 5 段——模板写的是"0—2 秒"（长破折号），
    # 检测式只认 "-"/"–"，等于技能的产物过不了技能自己的标准，而阈值 5 恰好放过去，没人发现。
    ok_v = (len(gv2) == len(C.VID_SECTIONS) and len(vgood) >= 400
            and fv2['附件点名'] and fv2['禁文字'] and fv2['留白占位'])
    notes.append("G2 正证（生成器喂自己的检测器）：段=%d/%d，%d 字，留白占位=%s -> %s" % (
        len(gv2), len(C.VID_SECTIONS), len(vgood), fv2['留白占位'], 'accept' if ok_v else 'REJECT(错)'))
    if not ok_v:
        fails.append("G2 误伤或生成器不合格：视频提示词只命中 %s（应 %d 段全中，留白占位=%s）" % (
            gv2, len(C.VID_SECTIONS), fv2['留白占位']))
    # 反证：抽掉秒级分镜段落，段数必须掉下来并被阈值拦住
    vno = re.sub(u'【[^】]*秒：[^】]*】[^【\n]*', '', vgood)
    gv3, fv3 = C.score_video_prompt(vno)
    bites = ('秒级分镜' not in gv3) and len(gv3) < len(C.VID_SECTIONS)
    notes.append("G2 反证（删掉所有『0—2 秒』分段）：秒级分镜命中=%s，段数 %d/%d -> %s" % (
        '秒级分镜' in gv3, len(gv3), len(C.VID_SECTIONS), 'reject' if bites else 'PASS(错)'))
    if not bites:
        fails.append("G2 不咬：删光秒级分镜后段数没掉下来（检测式或模板有一方是假的）")
    no_blank = re.sub(u'容器内部必须始终保持完全空白[^。]*。', u'容器里放满装饰。', vgood)
    _, fb2 = C.score_video_prompt(no_blank)
    notes.append("G2 反证（把留白占位改成『放满装饰』）：留白占位=%s -> %s" % (
        fb2['留白占位'], 'reject' if not fb2['留白占位'] else 'PASS(错)'))
    if fb2['留白占位']:
        fails.append("G2 不咬：视频提示词没有留白占位声明却放行")

    # --- G5 geometry: a box outside the reserved zone must be caught -------------
    from pptx import Presentation
    from pptx.util import Emu
    prs = Presentation()
    prs.slide_width = Emu(C.EMU_W)
    prs.slide_height = Emu(C.EMU_H)
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    C.add_text(sl, "越界的一行字", [0.02, 0.02, 0.5, 0.1], 30, bold=True, name='title')
    sp = list(sl.shapes)[0]
    zone = [0.05, 0.55, 0.9, 0.36]
    x0, y0 = sp.left / C.EMU_W, sp.top / C.EMU_H
    x1, y1 = (sp.left + sp.width) / C.EMU_W, (sp.top + sp.height) / C.EMU_H
    caught = (x0 < zone[0] - 0.01 or y0 < zone[1] - 0.01 or x1 > zone[0] + zone[2] + 0.01
              or y1 > zone[1] + zone[3] + 0.01)
    notes.append("G5 反证（文字放在页顶，留白区在页中）：box=[%.2f,%.2f,%.2f,%.2f] -> %s" % (
        x0, y0, x1, y1, 'reject' if caught else 'PASS(错)'))
    if not caught:
        fails.append("G5 不咬：越出留白区的文本框没被发现")

    # --- G5 must ignore the full-bleed scrim (it has an empty text frame) --------
    C.add_scrim(sl)
    scrim = [s for s in sl.shapes if s.name == 'scrim'][0]
    empty_ok = scrim.has_text_frame and not scrim.text_frame.text.strip()
    notes.append("G5 正证（满幅蒙版形状带空文本框）：应被跳过 -> %s" % ('ok' if empty_ok else 'FAIL'))
    if not empty_ok:
        fails.append("G5 的跳过条件不成立，会误报蒙版越界")

    # --- G7: a deck with zero text shapes must be rejected ----------------------
    prs2 = Presentation()
    prs2.slide_width = Emu(C.EMU_W)
    prs2.slide_height = Emu(C.EMU_H)
    prs2.slides.add_slide(prs2.slide_layouts[6])
    ntxt = sum(1 for s in prs2.slides for sh in s.shapes if sh.has_text_frame and sh.text_frame.text.strip())
    notes.append("G7 反证（空白页拼的空 deck）：真文本框=%d -> %s" % (ntxt, 'reject' if ntxt == 0 else 'PASS(错)'))
    if ntxt != 0:
        fails.append("G7 不咬：没有文字层的 deck 被判可编辑")

    # --- G6: out-of-range durations ------------------------------------------
    bad_morph = [m for m in (800, 2000, 4000) if not (1500 <= m <= 2500)]
    notes.append("G6 反证（morph 800ms/4000ms）：拦下 %d 个 -> %s" % (len(bad_morph), 'reject' if len(bad_morph) == 2 else 'PASS(错)'))
    if len(bad_morph) != 2:
        fails.append("G6 不咬：越界平滑时长没被拦")

    # --- G3: a small or licence-less asset must be rejected --------------------
    recs = [{'w': 800, 'h': 600, 'license': 'CC0', 'file': __file__},
            {'w': 4000, 'h': 3000, 'license': '', 'file': __file__}]
    small = [r for r in recs if min(r['w'], r['h']) < 1920]
    nolic = [r for r in recs if not r['license']]
    notes.append("G3 反证（800px 图 / 无许可图）：短边拦 %d 条，许可拦 %d 条 -> %s" % (
        len(small), len(nolic), 'reject' if small and nolic else 'PASS(错)'))
    if not (small and nolic):
        fails.append("G3 不咬：小图或无许可素材没被拦")

    # --- G3 content safety: an off-topic political photo must be hard-rejected --
    bad_title = "File:Protest in Hong Kong 2019.jpg"
    caught_sens = C.is_sensitive(bad_title)
    notes.append("G3 反证（把抗议照片配到收入页）：黑名单命中=%s -> %s" % (
        caught_sens, 'reject' if caught_sens else 'PASS(错)'))
    if not caught_sens:
        fails.append("G3 不咬：抗议/政治类图片没有被硬黑名单拦下")
    rel_offtopic = C.relevance("File:THANK YOU NYC.jpg", ["next step goals plan"])
    rel_one_tok = C.relevance("File:Whatever.jpg", ["下一步"])
    rel_good = C.relevance("File:Shanghai_Pudong_skyline_night.jpg", ["Shanghai Pudong skyline"])
    # 中心词判据的正证：检索词尾节是修饰语（dawn/exterior）时，主体命中必须仍然算对题。
    # 旧写法把最后一个 token 当中心词，实测把 v5 一百多页判成"相关度 0%"而素材其实就在库里。
    rel_tail = C.relevance("File:UTA Fort Worth and Ashton Depot.jpg", ["bus depot at dawn"])
    rel_ext = C.relevance("Intercity coach bus on motorway", ["intercity coach bus exterior"])
    # 反证：只命中尾节修饰语、主体词一个都没中，仍然要归零（不能用"放宽"当借口放行离题图）
    rel_dawnonly = C.relevance("File:Mountain dawn mist.jpg", ["bus depot at dawn"])
    notes.append("G3 相关度：离题图=%d%%，单中文词=%d%%（都必须是 0），对题图=%d%%；"
                 "尾节修饰语情形 depot=%d%% exterior=%d%%，只命中 dawn=%d%%" % (
                     rel_offtopic, rel_one_tok, rel_good, rel_tail, rel_ext, rel_dawnonly))
    if not (rel_offtopic == 0 and rel_one_tok == 0 and rel_good >= 34):
        fails.append("G3 相关度算法不咬：离题或单 token 情形没归零")
    if not (rel_tail >= 50 and rel_ext >= 50 and rel_dawnonly == 0):
        fails.append("相关度中心词判据仍是旧的：尾节修饰语把对题图判成 0%（覆盖率被自己的打分卡死），"
                     "或放宽过头让只命中修饰语的图过了线")

    # --- G3 aspect: a portrait image used as full-bleed must be caught ---------
    portrait = Image.new('RGB', (1200, 1800), (20, 20, 20))
    pr = portrait.size[0] / float(portrait.size[1])
    caught_ratio = not (1.45 <= pr <= 1.95)
    notes.append("G3 反证（竖图当整页背景，比例 %.2f）：越界=%s -> %s" % (pr, not caught_ratio, 'reject' if caught_ratio else 'PASS(错)'))
    if not caught_ratio:
        fails.append("G3 不咬：竖图当整页背景没被发现")

    # --- normalize_asset(crop169) must turn a portrait into 16:9 --------------
    # --- G11: a video page with no mp4 must be a hard failure -----------------
    wp = {'out': TMP, 'gen': os.path.join(TMP, 'gen'), 'materials': TMP,
          'promptpack': os.path.join(TMP, 'pp')}
    os.makedirs(os.path.join(wp['gen'], 'p001'), exist_ok=True)
    os.makedirs(wp['promptpack'], exist_ok=True)
    io.open(os.path.join(wp['promptpack'], 'p001_video.txt'), 'w', encoding='utf-8').write('x')
    shots_fake = {'pages': [{'page': 1, 'role': 'cover', 'asset_kind': 'video_bg',
                             'need_material': True},
                            {'page': 2, 'role': 'data', 'asset_kind': 'image',
                             'need_material': True}]}
    vp = [s2['page'] for s2 in shots_fake['pages']
          if s2.get('asset_kind') == 'video_bg' or s2['role'] in ('cover', 'divider', 'closing')]
    miss = [n for n in vp if not C.find_gen(TMP, n, 'video')]
    notes.append("G11 反证（要求整页视频但 gen/ 无 mp4）：video_pages=%s 缺=%s -> %s" % (
        vp, miss, 'reject' if miss else 'PASS(错)'))
    if not miss:
        fails.append("G11 不咬：视频页没有 mp4 却没被发现")
    any_video = any(C.find_gen(TMP, s2['page'], 'video') for s2 in shots_fake['pages'])
    notes.append("G11 反证（全册 0 个真视频页）：any_video=%s -> %s" % (
        any_video, 'reject' if not any_video else 'PASS(错)'))
    if any_video:
        fails.append("G11 判定异常：明明没放 mp4 却找到了视频")

    # --- pack: a page that needs an asset but has no prompt must NOT be ok ----
    rec_empty = {'page': 7, 'role': 'data', 'headline': 'x', 'files': {}, 'problems': []}
    need = rec_empty['files'] == {}
    notes.append("pack 反证（要求素材却零提示词的页）：files=%s -> %s" % (
        rec_empty['files'], 'reject' if need else 'PASS(错)'))
    if not need:
        fails.append("pack 的空提示词判定不成立")

    # --- G1: a fabricated sentence must be caught as off-docx ------------------
    dnorm = re.sub(r'\s+', '', '2026年公司在华东12城签约268家医美机构')
    fab = re.sub(r'\s+', '', '我们已获得国家卫健委试点牌照并覆盖 300 家三甲医院')
    caught_g1 = fab not in dnorm and len(fab) > 8
    notes.append("G1 反证（编造一句原文里没有的话）：判定为离源=%s -> %s" % (
        caught_g1, 'reject' if caught_g1 else 'PASS(错)'))
    if not caught_g1:
        fails.append("G1 不咬：编造的句子没被发现")

    # --- G13 coverage: the old split_headline data loss must be caught --------
    src_lines = ['2026年公司在华东 12 城签约 268 家医美机构，实际履约 214 家，履约率 79.9%。',
                 '第一，把曾合作医美的中小门店纳入潜在池，共 412 家，转化 96 家。']
    head, rest = C.split_headline([src_lines[0]])
    pages_text = {re.sub(r'\s+', '', x) for x in ([head] + list(rest))}
    lost = [l for l in src_lines
            if not any(re.sub(r'\s+', '', l) in p or p in re.sub(r'\s+', '', l)
                       for p in pages_text)]
    notes.append("G13 反证（标题化后正文是否丢句）：split 后保留 %d/%d 句，缺=%s -> %s" % (
        len(rest) + 1, 1, lost[:1], 'reject' if lost else 'accept(正文含原句)' ))
    if rest != [src_lines[0]]:
        fails.append("split_headline 又丢内容了：body=%s" % rest)

    # --- 标题退化反证（真渲染页 29 实测：整页标题只剩一个巨大的"七"）-------------
    # 两条路都会造成它：plan 侧 split_headline 把枚举记号算进 22 字预算，下一句放不下就 break；
    # 装配侧 clip_headline 在标点处裁完只剩 '七'。判据必须"宁可不裁也不上屏单字"。
    import display_refine as _DR59
    _ord_para = (u'七，新品证号归工厂还是我们自己持证后委托生产。'
                 u'两个选择的毛利率层级差 20 到 30 个百分点。')
    _h59, _r59 = C.split_headline([_ord_para])
    _h59_n = len(re.sub(u'\\s', u'', _h59 or u''))
    # 见证：裸尺子确实会裁出单字——不先证明这一点，下面的"没裁"可能只是判据没跑
    _raw59 = _DR59._clip(u'七，新品证号归工厂还是我们自己持证后委托生产', _DR59.HEADLINE_MAX)
    _witness = (_raw59 or u'').rstrip(u'，、,') in (u'七', u'七，')
    _c_ok, _c_did = C.clip_headline(u'七，新品证号归工厂还是我们自己持证后委托生产')
    _c_refuse = (not _c_did) or len(re.sub(u'\\s', u'', _c_ok or u'')) >= 4
    # 正例：句号就是天然边界，必须裁得出来（不许退化成"不裁"把 19 字整句推给闸）
    _p_ok, _p_did = C.clip_headline(u'中国的医院分两种。一种门口排队排到街上')
    _pos = (_p_did and _p_ok == u'中国的医院分两种')
    _title_ok = (_h59_n > 2 and not _DR59.BARE_ORDINAL.search(_h59 or u'')
                 and _witness and _c_refuse and _pos)
    notes.append(u"标题退化反证：裸 _clip 会裁出 %r（见证=%s）｜split 出 %r（%d 字）｜"
                 u"clip 拒绝上屏单字=%s｜句号边界正例裁出 %r=%s -> %s" % (
                     _raw59, _witness, _h59[:14], _h59_n, _c_refuse, _p_ok, _pos,
                     'ok' if _title_ok else 'FAIL'))
    if not _title_ok:
        fails.append(u"标题退化没被拦住：split 得到 %r（裸序号=%s）、clip 得到 %r 裁过=%s、"
                     u"裸尺子见证=%s（应为 '七'，否则这条反证证明不了什么）、"
                     u"句号正例应裁出 '中国的医院分两种' 实得 %r" % (
                         _h59, _DR59.BARE_ORDINAL.search(_h59 or u''), _c_ok, _c_did,
                         _raw59, _p_ok))

    # --- G9 coverage must be machine-checkable, not a self-report -------------
    # 基线数字不许写死在测试里（删掉一个字节级重复的模板后，写死的 172/2237 立刻变成假失败）：
    # 模板数直接数语料目录，页数用台账算出来的 stats.n_slides，两边都独立于 coverage.json。
    cov = C.kb('coverage.json')
    import corpus_path as CP
    src = CP.corpus_dir()
    n_src_decks = len([f for f in os.listdir(src) if f.lower().endswith('.pptx')]) \
        if src and os.path.isdir(src) else cov.get('decks_total')
    n_ledger_slides = (C.kb('deck_tech.json').get('stats') or {}).get('n_slides')
    ok_cov = (cov.get('decks_total') == n_src_decks
              and cov.get('slides_total') == n_ledger_slides
              and cov.get('rendered_slides') == cov.get('slides_total')
              and cov.get('render_matches_ledger') is True)
    notes.append("G9 正证（模板数=语料目录实数、页数=台账实数、渲染页数==台账页数）：%s vs 目录%d/台账%d -> %s" % (
        {k: cov.get(k) for k in ('decks_total', 'slides_total', 'rendered_slides')},
        n_src_decks, n_ledger_slides, 'ok' if ok_cov else 'FAIL'))
    if not ok_cov:
        fails.append("G9 覆盖不自证：渲染数与台账数不一致")

    # --- add_text must refuse EMU-sized zones (this pushed the CC-BY sheet off-canvas) ---
    try:
        C.add_text(list(prs2.slides)[0], "署名页测试", [8918691840000, 2821929840000,
                                                       130807480320000, 3762573120000], 20)
        caught_zone = False
    except ValueError:
        caught_zone = True
    notes.append("G8 反证（把 EMU 当 0-1 分数传给 add_text）：拒绝=%s -> %s" % (
        caught_zone, 'reject' if caught_zone else 'PASS(错，会写到画布外)'))
    if not caught_zone:
        fails.append("add_text 不拒绝 EMU 尺寸入参，署名页会再次被放到画布外")

    # --- G15: a video with no reference frame / no attachment mention must fail -
    n_v = len([1 for _ in [1]])
    pdir = os.path.join(TMP, 'gen', 'p001')
    os.makedirs(pdir, exist_ok=True)
    vp = os.path.join(pdir, 'video.mp4')
    open(vp, 'wb').write(b'' * 64)
    img_path = os.path.join(pdir, 'image.png')
    has_img = os.path.exists(img_path)
    bad_pp = os.path.join(TMP, 'pp')
    os.makedirs(bad_pp, exist_ok=True)
    io.open(os.path.join(bad_pp, 'p001_video.txt'), 'w', encoding='utf-8').write(
        '生成一段 8 秒 16:9 的科技感视频，画面有粒子和光环，镜头缓慢推进，最后稳定停留。禁止出现任何文字与水印。' * 3)
    txt = io.open(os.path.join(bad_pp, 'p001_video.txt'), encoding='utf-8').read()
    names_ref = bool(re.search(r'附件|作为首帧|首帧|参考图|尾帧', txt))
    chain_broken = (not has_img) or (not names_ref)
    notes.append("G15 反证（有 mp4 但无首帧/提示词未点名附件）：has_img=%s names_ref=%s -> %s" % (
        has_img, names_ref, 'reject' if chain_broken else 'PASS(错)'))
    if not chain_broken:
        fails.append("G15 不咬：首帧链断了却没被发现")
    io.open(os.path.join(bad_pp, 'p001_video.txt'), 'w', encoding='utf-8').write(
        '以附件1作为首帧与唯一构图依据，生成一段 8 秒 16:9 的科技感视频。' * 3)
    names_ref2 = bool(re.search(r'附件|作为首帧|首帧|参考图|尾帧',
                                io.open(os.path.join(bad_pp, 'p001_video.txt'), encoding='utf-8').read()))
    notes.append("G15 正证（提示词点名附件1作为首帧）：names_ref=%s -> %s" % (
        names_ref2, 'ok' if names_ref2 else 'FAIL'))
    if not names_ref2:
        fails.append("G15 误伤：点名了附件却判链断")

    # --- G14: a deck whose morph has no paired anchor must be caught ----------
    prs3 = Presentation()
    prs3.slide_width = Emu(C.EMU_W)
    prs3.slide_height = Emu(C.EMU_H)
    for i in range(2):
        sl3 = prs3.slides.add_slide(prs3.slide_layouts[6])
        C.add_text(sl3, "第%d页标题" % (i + 1), [0.08, 0.3, 0.84, 0.2], 44, bold=True, name='title')
    C.add_morph(list(prs3.slides)[1], 2000)      # transition only, no shared anchor
    import zipfile
    tmp3 = os.path.join(TMP, 'morph_noanchor.pptx')
    prs3.save(tmp3)
    z3 = zipfile.ZipFile(tmp3)
    n_m = sum(1 for f in z3.namelist() if f.startswith('ppt/slides/slide') and b'morph option' in z3.read(f))
    n_a = sum(1 for f in z3.namelist() if f.startswith('ppt/slides/slide') and b'morph_anchor' in z3.read(f))
    z3.close()
    caught14 = n_m > 0 and n_a < n_m
    notes.append("G14 反证（写了 morph 但没有同名锚点）：trans=%d anchor=%d -> %s" % (
        n_m, n_a, 'reject' if caught14 else 'PASS(错)'))
    if not caught14:
        fails.append("G14 不咬：平滑没有锚点却没被发现")
    # and the fix path must produce a pair
    C.ensure_anchor(list(prs3.slides)[0]); C.ensure_anchor(list(prs3.slides)[1])
    tmp4 = os.path.join(TMP, 'morph_withanchor.pptx')
    prs3.save(tmp4)
    z4 = zipfile.ZipFile(tmp4)
    n_a2 = sum(1 for f in z4.namelist() if f.startswith('ppt/slides/slide') and b'morph_anchor' in z4.read(f))
    z4.close()
    notes.append("G14 正证（补锚点后）：anchor 页 %d -> %s" % (n_a2, 'ok' if n_a2 >= n_m else 'FAIL'))
    if n_a2 < n_m:
        fails.append("share_anchor/ensure_anchor 没真的配对锚点")

    # --- normalize_asset really shrinks a big image ---------------------------
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP, exist_ok=True)
    big = os.path.join(TMP, 'big.jpg')
    Image.effect_noise((4200, 2800), 60).convert('RGB').save(big, quality=95)
    outp = os.path.join(TMP, 'small.jpg')
    C.normalize_asset(big, outp, crop169=True)
    im2 = Image.open(outp)
    ratio = im2.size[0] / float(im2.size[1])
    ok_norm = max(im2.size) <= 3200 and os.path.getsize(outp) < os.path.getsize(big)         and abs(ratio - 16 / 9.0) < 0.02
    notes.append("归一化：%.1fMB -> %.1fMB，尺寸 %s -> %s -> %s" % (
        os.path.getsize(big) / 1048576, os.path.getsize(outp) / 1048576,
        '4200x2800', '%dx%d(%.3f)' % (im2.size[0], im2.size[1], ratio),
        'ok' if ok_norm else 'FAIL'))
    if not ok_norm:
        fails.append("normalize_asset 没把长边压到 3200 以内")

    # --- differential CLI test: run the real `check` command and diff its verdict ----------
    # Everything above recomputes gate logic in-process, which a reviewer rightly called
    # tautological. These runs go through `python caofeng.py check` on a synthetic work dir and
    # require (a) the bad deck to exit non-zero naming G1, (b) the same deck, once the fabricated
    # sentence is removed, to stop blaming G1 while still hard-failing on the poisoned manifest.
    HERE = os.path.dirname(os.path.abspath(__file__))
    engine = os.path.join(HERE, 'caofeng.py')
    def _dl_png(path):
        Image.effect_noise((1920, 1080), 45).convert('RGB').save(path)

    _DL_VIDEO = [None]

    def _dl_video(dest):
        """投放回来的 mp4：真用 ffmpeg 出一段噪点短片（闸按字节数与时长判占位文件，假文件过不了）。"""
        if not _DL_VIDEO[0] or not os.path.exists(_DL_VIDEO[0]):
            ff = None
            try:
                import imageio_ffmpeg
                ff = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                ff = None
            cache = os.path.join(TMP, '_delivery_video.mp4')
            if ff:
                fr = os.path.join(TMP, '_delivery_frames')
                shutil.rmtree(fr, ignore_errors=True)
                os.makedirs(fr)
                for i in range(18):
                    Image.effect_noise((1920, 1080), 40 + i).convert('RGB').save(
                        os.path.join(fr, 'f%03d.png' % i))
                subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y',
                                '-framerate', '12', '-i', os.path.join(fr, 'f%03d.png'),
                                '-pix_fmt', 'yuv420p', '-crf', '18', cache], capture_output=True)
                shutil.rmtree(fr, ignore_errors=True)
            if os.path.exists(cache) and os.path.getsize(cache) > 70000:
                _DL_VIDEO[0] = cache
        if _DL_VIDEO[0]:
            shutil.copy(_DL_VIDEO[0], dest)
            return True
        return False

    def _dl_gif(path, n=12):
        frames = []
        for k in range(n):
            im = Image.new('RGBA', (900, 900), (0, 0, 0, 0))
            ImageDraw.Draw(im).rectangle([40 + k * 30, 200, 340 + k * 30, 560],
                                         fill=(220, 190, 110, 255))
            frames.append(im.convert('P'))
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0,
                       transparency=0, disposal=2)

    def prep_delivery(work, bad=None, deliver_products=False):
        """夹具必须走公开回执入口，不许走后门。
        默认 declined：等价于"卡片已发给用户、用户回『这些产物我不做了』"，成品形态与加闸前一致，
        这样各条闸的反证测的还是它们自己那条规则。deliver_products=True 时才真把产物做回来
        （gen/ 补齐 + ack --ready），用于 G26 的正证。"""
        subprocess.run([sys.executable, engine, 'pack', '--work', work],
                       capture_output=True, text=True, encoding='utf8', errors='replace')
        if not deliver_products:
            r = subprocess.run([sys.executable, engine, 'ack', '--work', work, '--declined',
                                '--note', u'夹具：用户回「这些产物我不做了，按现状出片」'],
                               capture_output=True, text=True, encoding='utf8', errors='replace')
            if r.returncode != 0 and bad is not None:
                bad.append(u'ack --declined rc=%d %s' % (r.returncode, (r.stdout or u'')[-260:]))
            return r.returncode == 0
        pp = os.path.join(work, 'promptpack')
        kinds = {'image': '.png', 'first': '.png', 'last': '.png', 'video': '.mp4', 'gif': '.gif'}
        todo = []
        if os.path.isdir(pp):
            for fn in sorted(os.listdir(pp)):
                m = re.match(r'p(\d+)_(image|first|last|video|gif)\.txt$', fn)
                if m:
                    todo.append((int(m.group(1)), m.group(2)))
        no_video = []
        for n, kind in todo:
            d = os.path.join(work, 'gen', 'p%03d' % n)
            if not os.path.isdir(d):
                os.makedirs(d)
            dest = os.path.join(d, kind + kinds[kind])
            if os.path.exists(dest):
                continue
            if kind in ('image', 'first', 'last'):
                _dl_png(dest)
            elif kind == 'gif':
                _dl_gif(dest)
            elif not _dl_video(dest):
                no_video.append(n)
        r = subprocess.run([sys.executable, engine, 'ack', '--work', work, '--ready',
                            '--note', u'夹具：用户回「已就绪」，产物已放回'],
                           capture_output=True, text=True, encoding='utf8', errors='replace')
        if r.returncode != 0 and no_video:
            r = subprocess.run([sys.executable, engine, 'ack', '--work', work, '--no-video',
                                '--note', u'夹具：本机没有可用的视频产物'],
                               capture_output=True, text=True, encoding='utf8', errors='replace')
        if r.returncode != 0 and bad is not None:
            bad.append(u'ack rc=%d %s' % (r.returncode, (r.stdout or u'')[-260:]))
        return r.returncode == 0

    cli = os.path.join(TMP, 'cli')
    rep_p = os.path.join(cli, 'report', '验收报告.md')
    try:
        import docx as _dx
        shutil.rmtree(cli, ignore_errors=True)
        os.makedirs(cli, exist_ok=True)
        dpath = os.path.join(cli, 'doc.docx')
        _d = _dx.Document()
        _d.add_heading('季度经营复盘', 0)
        _d.add_heading('一、渠道签约', 1)
        _d.add_paragraph('2026年公司在华东 12 城签约 268 家医美机构，实际履约 214 家，履约率 79.9%。')
        _d.add_paragraph('第二，把曾合作医美的中小门店纳入潜在池，共 412 家，转化 96 家。')
        _d.save(dpath)
        rp = subprocess.run([sys.executable, engine, 'plan', dpath, '--work', cli, '--auto-pages'],
                            capture_output=True, text=True, encoding='utf8', errors='replace')
        ol_p = os.path.join(cli, 'outline.json')
        ol = json.load(io.open(ol_p, encoding='utf-8'))

        def run_check():
            if os.path.exists(rep_p):
                os.remove(rep_p)
            r = subprocess.run([sys.executable, engine, 'check', '--work', cli],
                               capture_output=True, text=True, encoding='utf8', errors='replace')
            txt = io.open(rep_p, encoding='utf-8').read() if os.path.exists(rep_p) else ''
            return r.returncode, txt

        FAB = '我们已获得国家卫健委试点牌照并覆盖 300 家三甲医院'
        ol['pages'][1]['body'] = list(ol['pages'][1].get('body') or []) + [FAB]
        json.dump(ol, io.open(ol_p, 'w', encoding='utf-8'), ensure_ascii=False)
        rc1, t1 = run_check()
        g1_fired = ('G1:' in t1) and rc1 != 0

        ol['pages'][1]['body'] = [x for x in ol['pages'][1]['body'] if x != FAB]
        json.dump(ol, io.open(ol_p, 'w', encoding='utf-8'), ensure_ascii=False)
        # poison the material manifest with a protest photo and no source_page, straight through
        # the gate: fetch-time filtering is not enough, check must re-screen what is on disk.
        man_p = os.path.join(cli, 'materials', 'manifest.jsonl')
        io.open(man_p, 'w', encoding='utf-8').write(json.dumps(
            {'page': 2, 'term': 'medical operations', 'file': os.path.abspath(__file__),
             'url': 'https://commons.wikimedia.org/wiki/File:Public_demo',
             'source_page': 'File:Public demonstration in the square.jpg',
             'w': 4000, 'h': 3000, 'license': 'CC BY-SA 4.0', 'relevance': 90},
            ensure_ascii=False) + '\n')
        rc2, t2 = run_check()
        g1_cleared = 'G1:' not in t2
        g3_sens_fired = ('命中敏感/离域黑名单' in t2) and rc2 != 0
        # --allow-sensitive must NOT unblock a protest photo on a business deck
        if os.path.exists(rep_p):
            os.remove(rep_p)
        r3 = subprocess.run([sys.executable, engine, 'check', '--work', cli,
                             '--allow-sensitive', 'medical-gore'],
                            capture_output=True, text=True, encoding='utf8', errors='replace')
        t3 = io.open(rep_p, encoding='utf-8').read() if os.path.exists(rep_p) else ''
        g3_still_fired = '命中敏感/离域黑名单' in t3 and r3.returncode != 0
        ol = json.load(io.open(ol_p, encoding='utf-8'))
        ol['allow_sensitive'] = ['protest']
        json.dump(ol, io.open(ol_p, 'w', encoding='utf-8'), ensure_ascii=False)
        if os.path.exists(rep_p):
            os.remove(rep_p)
        r4 = subprocess.run([sys.executable, engine, 'check', '--work', cli],
                            capture_output=True, text=True, encoding='utf8', errors='replace')
        t4 = io.open(rep_p, encoding='utf-8').read() if os.path.exists(rep_p) else ''
        g3_allow_works = '命中敏感/离域黑名单' not in t4
        unknown = subprocess.run([sys.executable, engine, 'fetch', '--work', cli,
                                  '--allow-sensitive', 'everything'],
                                 capture_output=True, text=True, encoding='utf8', errors='replace')
        typo_refused = unknown.returncode != 0
        # an empty manifest means the poison is gone; restore for the next run
        io.open(man_p, 'w', encoding='utf-8').close()
        notes.append("真实 CLI 反证（跑 caofeng.py check）：编造句 rc=%d 命中G1=%s / 去 poison 后 G1 消失=%s -> %s" % (
            rc1, g1_fired, g1_cleared, 'reject+recover' if (g1_fired and g1_cleared) else 'FAIL'))
        if not (g1_fired and g1_cleared):
            fails.append("CLI check 不咬或误报：编造句应命中 G1（rc=%s 命中=%s），去掉后应消失（=%s）" % (
                rc1, g1_fired, g1_cleared))
        notes.append("真实 CLI 反证（manifest 里藏抗议照片）：G3 复检命中=%s，放行 medical-gore 仍命中=%s，"
                     "声明放行 protest 才放行=%s，类别名写错被拒=%s -> %s" % (
                         g3_sens_fired, g3_still_fired, g3_allow_works, typo_refused,
                         'ok' if (g3_sens_fired and g3_still_fired and g3_allow_works and typo_refused) else 'FAIL'))
        if not (g3_sens_fired and g3_still_fired and g3_allow_works and typo_refused):
            fails.append("G3 安全复检链不完整：命中=%s 跨类不放行=%s 显式放行=%s 错名拒绝=%s" % (
                g3_sens_fired, g3_still_fired, g3_allow_works, typo_refused))

        # G13 must measure the deliverable: build the deck, then check body coverage against XML
        bp = subprocess.run([sys.executable, engine, 'build', '--work', cli],
                            capture_output=True, text=True, encoding='utf8', errors='replace')
        deck = None
        for f in os.listdir(os.path.join(cli, 'out')):
            if f.endswith('.pptx'):
                deck = os.path.join(cli, 'out', f)
        pages_txt = C.pptx_slide_texts(deck) if deck else []
        # 提炼契约（SPEC §12）下屏幕上只有短句：逐句上屏那条判据换成"事实单元必须在屏"，
        # 逐字模式那几条仍然要求整句在屏。这里按 build_log 实际用的模式分别核，不看计划。
        import display_refine as _DR
        blog_cov = json.load(io.open(os.path.join(cli, 'out', 'build_log.json'), encoding='utf-8'))
        refined_cov = {b['page'] for b in blog_cov if b.get('display_mode') == 'refined'}
        # 结论以闸自己的读数为准（G13 整句核对 + G24 事实单元核对），不在测试里另装一把尺子：
        # 夹具没有"agent 写 display_lines"这一步，测试自定的数字宽度只会和闸互相打脸。
        rc_cov, t_cov = run_check()
        f_g13 = re.search(r'"missing_from_pptx":\s*(\d+)', t_cov or '')
        f_g24 = re.search(r'G24_事实覆盖[\s\S]{0,900}?"missing":\s*(\d+)', t_cov or '')
        missing_sent = [] if (f_g13 and f_g13.group(1) == '0') else ['G13 missing=%s' % (
            f_g13.group(1) if f_g13 else '报告没读数')]
        missing_fact = [] if (f_g24 and f_g24.group(1) == '0') else ['G24 missing=%s' % (
            f_g24.group(1) if f_g24 else '报告没读数')]
        notes.append("G13/G24 正证（读数取自验收报告本体：整句核对 + 逐事实核对）："
                     "读到 %d 页，%s %s（提炼页 %s）-> %s" % (
                         len(pages_txt), missing_sent or '整句 0 缺失', missing_fact or '事实 0 缺失',
                         sorted(refined_cov),
                         'ok' if (deck and pages_txt and not missing_sent and not missing_fact)
                         else 'FAIL'))
        if not deck:
            fails.append("CLI build 没有产出 pptx：%s" % (bp.stdout or '')[-120:])
        elif not pages_txt:
            fails.append("G13 正证失败：成品 pptx 读不到任何页文本")
        elif missing_sent or missing_fact:
            fails.append("G13 正证失败：成品 pptx 缺 %d 句整句正文（例：%s）/ 缺 %d 个事实单元（例：%s）" % (
                len(missing_sent), (missing_sent[0] if missing_sent else '')[:40],
                len(missing_fact), (missing_fact[0] if missing_fact else '')[:30]))
    except Exception as e:
        notes.append("真实 CLI 反证：异常 %s -> FAIL" % str(e)[:120])
        fails.append("CLI 差分测试没跑成：%s" % str(e)[:160])

    # --- colour field must not plot a bare year as a magnitude ------------------
    # field_bg only draws bars for a unit that carries >=3 same-unit values, so the fixture has
    # three "家"; the headline year must not sneak in as a fourth unit-less magnitude.
    mag = C.magnitudes({'headline': '2026 年华东签约',
                        'body': ['共 412 家，转化 96 家，续约 55 家，履约率 79.9%，投入 1.6 亿元。']})
    yr = [v for v, u in mag if u == '' and 1900 <= v <= 2100]
    grp = C.same_unit_groups(mag)
    field_ok = (not yr) and len(grp.get('家', [])) == 3 and 'num' not in grp
    notes.append("色场数值抽取：拿到 %s；裸年份残留=%s（必须空）；同单位分组 %s -> %s" % (
        sorted(mag), yr, {k: sorted(v) for k, v in grp.items()}, 'ok' if field_ok else 'FAIL'))
    if not field_ok:
        fails.append("field_bg 的量级抽取不对：年份=%s 分组=%s（应为 3 个“家”且没有裸数字组）" % (
            yr, list(grp)))

    # --- delivery card must show the title the deck will actually carry --------
    import delivery as _dl
    card_dir = os.path.join(TMP, 'card')
    os.makedirs(card_dir, exist_ok=True)
    _dl.write([{'page': 2, 'headline': '规划草案标题', 'role': 'data', 'files': {}}],
              {2: {'file': os.path.abspath(__file__), 'url': 'https://example.com/x',
                   'w': 4000, 'h': 3000, 'license': 'CC BY-SA 4.0', 'relevance': 80}},
              {'pages': [{'page': 2, 'role': 'data', 'headline': '成片用的标题',
                          'reserved_zone': [0.08, 0.55, 0.5, 0.3]}]},
              os.path.join(card_dir, 'delivery_card.md'))
    card = io.open(os.path.join(card_dir, 'delivery_card.md'), encoding='utf-8').read()
    card_ok = ('成片用的标题' in card) and ('规划草案标题' not in card) and ('CC BY-SA 4.0' in card)
    notes.append("投放卡标题：取分镜标题=%s，未回落到草案=%s -> %s" % (
        '成片用的标题' in card, '规划草案标题' not in card, 'ok' if card_ok else 'FAIL'))
    if not card_ok:
        fails.append("投放卡显示的是 outline 草案标题，和成片页面上的标题不一致，agent 会按错的标题写提示词")

    # --- study scripts must fail *friendly* when the work dir is missing -------------------
    # SKILL.md promises CAOFENG_WORK/CAOFENG_SRC and "refuse when the work dir is missing".
    # 光 grep 'require_work' 证明不了任何事：四个脚本把守卫定义在调用**之后**，
    # 缺目录时是 NameError(rc=1) 而不是友好退出 2 —— 复现链条其实早就断了。
    study = ['visual_metrics.py', 'deck_render.py']  # 公开版仅保留出片链上的落点守卫
    sh_dir = os.path.dirname(os.path.abspath(__file__))
    bad_guard = []
    for f in study:
        env = dict(os.environ)
        env['CAOFENG_WORK'] = os.path.join(TMP, 'no-such-work-dir')
        env['PYTHONIOENCODING'] = 'utf-8'
        try:
            r_ = subprocess.run([sys.executable, os.path.join(sh_dir, f)],
                                capture_output=True, text=True, encoding='utf8',
                                errors='replace', env=env, timeout=180)
        except Exception as exc_:
            bad_guard.append('%s 起不来 %s' % (f, str(exc_)[:60]))
            continue
        blob = (r_.stdout or '') + (r_.stderr or '')
        if r_.returncode != 2 or 'Traceback' in blob or u'工作目录不存在' not in blob:
            bad_guard.append('%s rc=%s 有traceback=%s' % (f, r_.returncode, 'Traceback' in blob))
    r_m = subprocess.run([sys.executable, os.path.join(sh_dir, 'montage.py')],
                         capture_output=True, text=True, encoding='utf8', errors='replace')
    montage_ok = (r_m.returncode == 2 and u'用法' in (r_m.stdout or '')
                  and 'Traceback' not in (r_m.stderr or ''))
    notes.append("研习脚本落点守卫（真跑：缺工作目录必须退出码 2 且无 traceback）：%d 个里不合格 %s；"
                 "montage 裸跑给用法=%s -> %s" % (
                     len(study), bad_guard or '无', montage_ok,
                     'ok' if (not bad_guard and montage_ok) else 'FAIL'))
    if bad_guard:
        fails.append("这些会写盘的研习脚本在缺工作目录时不是友好停止（守卫定义在使用之后也算不合格）：%s" % bad_guard)
    if not montage_ok:
        fails.append("montage.py 裸跑抛 IndexError，非程序员看不出该怎么用")


    # --- 表格通道：真跑 plan/build/check，再抠掉一格看闸咬不咬 -----------------
    # 这一条存在的原因：Word 台账的单元格普遍只有 2-5 字，正好落在 G13 句子比对的长度豁免线以下，
    # 于是"整张表没装进成品"这件事闸看不见。现在表格按格核对，抠掉一格必须报错。
    tb_dir = os.path.join(TMP, 'tbl')
    shutil.rmtree(tb_dir, ignore_errors=True)
    os.makedirs(tb_dir)
    import docx as _dx2
    import zipfile as _zl
    engine = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'caofeng.py')
    tb_doc = os.path.join(tb_dir, 'tbl.docx')
    _t = _dx2.Document()
    _t.add_heading('城市台账', 0)
    _t.add_heading('一、分区明细', 1)
    _t.add_paragraph('2026 年华东分区台账如下，潜在池合计 412 家。')
    _tab = _t.add_table(rows=3, cols=3)
    for _i, _row in enumerate([('城市', '潜在门店', '已签约'),
                               ('上海', '128', '41'), ('杭州', '96', '28')]):
        for _j, _v in enumerate(_row):
            _tab.rows[_i].cells[_j].text = _v
    _t.save(tb_doc)
    for args in (['plan', tb_doc, '--auto-pages'], ['build']):
        subprocess.run([sys.executable, engine] + args + ['--work', tb_dir],
                       capture_output=True, text=True, encoding='utf8', errors='replace')
    tb_rep = os.path.join(tb_dir, 'report', '验收报告.md')

    def run_check_at(work):
        rep = os.path.join(work, 'report', '验收报告.md')
        if os.path.exists(rep):
            os.remove(rep)
        r = subprocess.run([sys.executable, engine, 'check', '--work', work],
                           capture_output=True, text=True, encoding='utf8', errors='replace')
        return r.returncode, (io.open(rep, encoding='utf-8').read() if os.path.exists(rep) else '')

    rc_clean, t_clean = run_check_at(tb_dir)
    n_cells = re.search(r'"table_cells":\s*(\d+)', t_clean)
    cell_gate_quiet = u'单元格在成品对应页找不到' not in t_clean
    tbl_landed = n_cells and int(n_cells.group(1)) >= 9 and cell_gate_quiet
    notes.append("表格通道正证（3x3 台账真装配）：读到 %s 格，逐格核对无缺失=%s -> %s" % (
        n_cells.group(1) if n_cells else 0, cell_gate_quiet, 'ok' if tbl_landed else 'FAIL'))
    if not tbl_landed:
        fails.append("表格没进成品或逐格核对误报（table_cells=%s）" % (n_cells.group(1) if n_cells else '缺字段'))

    deck_tb = os.path.join(tb_dir, 'out', 'deck.pptx')
    hit = None
    if os.path.exists(deck_tb):
        zin = _zl.ZipFile(deck_tb)
        names = [nm for nm in zin.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', nm)]
        payload = {nm: zin.read(nm) for nm in zin.namelist()}
        zin.close()
        for nm in names:
            if u'上海'.encode('utf8') in payload[nm]:
                payload[nm] = payload[nm].replace(u'<a:t>上海</a:t>'.encode('utf8'),
                                                  u'<a:t>城市名被删</a:t>'.encode('utf8'), 1)
                hit = nm
                break
        if hit:
            zout = _zl.ZipFile(deck_tb, 'w', _zl.ZIP_DEFLATED)
            for nm, data in payload.items():
                zout.writestr(nm, data)
            zout.close()
    rc_poison, t_poison = run_check_at(tb_dir)
    cell_gate_bites = (u'单元格在成品对应页找不到' in t_poison) and rc_poison != 0
    notes.append("表格通道反证（把成品里「上海」这一格改成别的字）：G13 命中=%s，退出码=%d -> %s" % (
        u'单元格在成品对应页找不到' in t_poison, rc_poison, 'reject' if cell_gate_bites else 'PASS(错)'))
    if not cell_gate_bites:
        fails.append("G13 逐格核对不咬：抠掉一个台账单元格（%s）后仍然放行" % hit)

    # --- 第三轮复验点名的"不可能失败的闸"，逐条给出反证 --------------------------
    # 每条都必须走真 CLI 或真共享判定函数，不许在测试里重算一遍。
    def run_check_at(work):
        rep = os.path.join(work, 'report', u'验收报告.md')
        if os.path.exists(rep):
            os.remove(rep)
        r = subprocess.run([sys.executable, engine, 'check', '--work', work],
                           capture_output=True, text=True, encoding='utf8', errors='replace')
        return r.returncode, (io.open(rep, encoding='utf-8').read() if os.path.exists(rep) else '')

    # (1) G12：没渲染过就报合格 —— 复验实测的老 bug
    rc_norender, t_norender = run_check_at(cli)
    g12_bites = (u'没有渲染凭据' in t_norender) and rc_norender != 0
    notes.append("G12 反证（check 不带 --render）：命中=%s 退出码=%d -> %s" % (
        u'没有渲染凭据' in t_norender, rc_norender, 'reject' if g12_bites else 'PASS(错)'))
    if not g12_bites:
        fails.append("G12 不咬：从没渲染过的 deck 不带 --render 也能过")

    # (2) G1：编造的话写在 shots.json 标题里（build 会覆盖到成品），老 G1 只看 outline 会放行
    sp = os.path.join(cli, 'shots.json')
    sh = json.load(io.open(sp, encoding='utf-8'))
    orig = {}
    for s in sh['pages']:
        orig[s['page']] = s.get('headline')
    for s in sh['pages']:
        if s['page'] == 2:
            s['headline'] = u'收入翻番到 3.2 亿元'
    json.dump(sh, io.open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
    subprocess.run([sys.executable, engine, 'build', '--work', cli],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_g1, t_g1 = run_check_at(cli)
    g1_deck_bites = (u'G1:' in t_g1 and u'3.2' in t_g1) and rc_g1 != 0
    notes.append("G1 反证（成品标题写原文没有的数字 3.2 亿元）：命中=%s 退出码=%d -> %s" % (
        u'G1:' in t_g1, rc_g1, 'reject' if g1_deck_bites else 'PASS(错)'))
    if not g1_deck_bites:
        fails.append("G1 不咬成品：shots 标题里的编造数字进了 pptx 却没被发现")
    for s in sh['pages']:
        s['headline'] = orig[s['page']]
    json.dump(sh, io.open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
    subprocess.run([sys.executable, engine, 'build', '--work', cli],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_g1b, t_g1b = run_check_at(cli)
    g1_recovers = u'G1:' not in t_g1b
    notes.append("G1 撤销反证（标题改回原文措辞）：G1 消失=%s -> %s" % (
        g1_recovers, 'ok' if g1_recovers else 'FAIL'))
    if not g1_recovers:
        fails.append("G1 撤销后仍报错（误伤）：%s" % (re.search(u'G1:[^\\n]*', t_g1b) or [u'']) [0][:120])

    # (3) G3b：成品里嵌的图短边不足，manifest 说多大都没用
    tiny = os.path.join(cli, 'gen', 'p002')
    os.makedirs(tiny, exist_ok=True)
    Image.new('RGB', (900, 506), (30, 40, 60)).save(os.path.join(tiny, 'image.png'))
    sh = json.load(io.open(sp, encoding='utf-8'))
    for s in sh['pages']:
        if s['page'] == 2:
            s['asset_kind'] = 'image'
    json.dump(sh, io.open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
    subprocess.run([sys.executable, engine, 'build', '--work', cli],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_soft, t_soft = run_check_at(cli)
    # build 自己就该拒绝把 900x506 铺满整页（改走色场），闸再兜底
    refused = u'短边<1080' in t_soft or u'素材只有 900x506' in (
        io.open(os.path.join(cli, 'out', 'build_log.json'), encoding='utf-8').read())
    notes.append("G3b 反证（900x506 的图当整页背景）：装配拒绝或闸命中=%s 退出码=%d -> %s" % (
        refused, rc_soft, 'reject' if refused else 'PASS(错)'))
    if not refused:
        fails.append("G3b 不咬：低分辨率图被铺成整页背景却没被发现（也没在装配时降级成色场）")

    # (4) G15：两个视频页用同一个 mp4 / 首帧与 poster 不是同一画面
    import hashlib
    v1 = os.path.join(cli, 'gen', 'p001', 'video.mp4')
    os.makedirs(os.path.dirname(v1), exist_ok=True)
    made = False
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        # build the clip from PIL frames instead of lavfi: the bundled ffmpeg on this box has no
        # lavfi input, and "testsrc failed" would otherwise look like a gate defect
        frames = os.path.join(cli, 'gen', '_frames')
        shutil.rmtree(frames, ignore_errors=True)
        os.makedirs(frames)
        import random
        rnd = random.Random(7)
        for i in range(30):
            # flat colour compresses to ~37KB and would trip the gate's own 64KB floor for the
            # wrong reason; real footage is noisy, so the fixture is noisy too
            px = bytes(rnd.randrange(256) for _ in range(64 * 64 * 3))
            fr = Image.frombytes('RGB', (64, 64), px).resize((1920, 1080))
            ImageDraw.Draw(fr).rectangle([80 + i * 40, 300, 300 + i * 40, 700], fill=(220, 200, 120))
            fr.save(os.path.join(frames, 'f%03d.png' % i))
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-framerate', '15',
                        '-i', os.path.join(frames, 'f%03d.png'), '-pix_fmt', 'yuv420p',
                        '-crf', '20', v1], capture_output=True)
        made = os.path.exists(v1) and os.path.getsize(v1) > 65536
        shutil.rmtree(frames, ignore_errors=True)
    except Exception:
        made = False
    if made:
        pages = [s['page'] for s in sh['pages']][:2]      # 用这份 deck 真实存在的两页，别凭空造页号
        pa, pb = pages[0], pages[1] if len(pages) > 1 else pages[0] + 1
        d2 = os.path.join(cli, 'gen', 'p%03d' % pb)
        os.makedirs(d2, exist_ok=True)
        shutil.copy(v1, os.path.join(d2, 'video.mp4'))
        Image.new('RGB', (1920, 1080), (200, 30, 30)).save(os.path.join(d2, 'image.png'))
        for s in sh['pages']:
            if s['page'] in (pa, pb):
                s['asset_kind'] = 'video_bg'
        if pb not in {s['page'] for s in sh['pages']}:
            sh['pages'].append({'page': pb, 'role': 'data', 'asset_kind': 'video_bg',
                                'headline': u'测试页', 'reserved_zone': [0.08, 0.16, 0.84, 0.70]})
        json.dump(sh, io.open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
        subprocess.run([sys.executable, engine, 'build', '--work', cli],
                       capture_output=True, text=True, encoding='utf8', errors='replace')
        rc_v, t_v = run_check_at(cli)
        dup = u'同一个 mp4' in t_v
        mism = u'不是同一个画面' in t_v
        notes.append("G15 反证（页%s/%s 共用同一 mp4 + poster 与首帧不同）：重复命中=%s 首帧不符命中=%s 退出码=%d -> %s" % (
            pa, pb, dup, mism, rc_v, 'reject' if (dup and mism) else 'PASS(错)'))
        if not (dup and mism):
            fails.append("G15 不咬：重复视频（SHA1 相同）或首帧/poster 不一致没被发现（dup=%s mism=%s）" % (dup, mism))
        # 正证：poster 就是视频第 0 帧时不该误报
        poster = os.path.join(d2, 'image.png')
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-i',
                        os.path.join(d2, 'video.mp4'), '-frames:v', '1', poster], capture_output=True)
        rc_v2, t_v2 = run_check_at(cli)
        ok_chain = u'不是同一个画面' not in t_v2
        notes.append("G15 正证（poster 换成视频第0帧后）：误报消失=%s -> %s" % (
            ok_chain, 'ok' if ok_chain else 'FAIL'))
        if not ok_chain:
            fails.append("G15 误伤：poster 就是首帧时仍报不匹配")
        # (4c) 首帧附件唯一口径（2026-11-09 用户"提示词为何重复发"查实的那两条）：
        #      ① 两页卡指到同一张附件必须拦；改指各自专属图后消失
        #      ② 尺子被 poster 顶平且认领凭证没记 attachment → 必须如实报"量不出结果"
        # 全程在 cli 的副本里做：这一段会重写 gen/ 与 promptpack/，留在原沙箱里会把后面的
        # 推镜兜底/断头数字/溢出/G1 反证全带偏（实测就是连红 5 项）。
        cli_att = os.path.join(TMP, 'cli_att_shared')
        shutil.rmtree(cli_att, ignore_errors=True)
        shutil.copytree(cli, cli_att)
        shared = os.path.join(cli_att, 'materials', 'shared_att.jpg')
        os.makedirs(os.path.dirname(shared), exist_ok=True)
        Image.new('RGB', (1920, 1080), (20, 60, 90)).save(shared)
        for _p in (pa, pb):
            io.open(os.path.join(cli_att, 'promptpack', 'p%03d_video.txt' % _p), 'w',
                    encoding='utf-8').write(
                u'首帧附件用 %s，画面唯一的构图、透视、配色依据就是它。'
                u'生成一段约 8 秒、16:9 横版、超高清的测试主题 PPT 动态背景视频。' % shared)
        rc_sh, t_sh = run_check_at(cli_att)
        shared_hit = u'首帧附件是同一张图' in t_sh or u'首帧附件其实是同一张图' in t_sh
        a1 = os.path.join(cli_att, 'materials', 'att_a.jpg')
        a2 = os.path.join(cli_att, 'materials', 'att_b.jpg')
        Image.new('RGB', (1920, 1080), (20, 60, 90)).save(a1)
        Image.new('RGB', (1920, 1080), (210, 190, 40)).save(a2)
        io.open(os.path.join(cli_att, 'promptpack', 'p%03d_video.txt' % pa), 'w', encoding='utf-8').write(
            u'首帧附件用 %s，画面唯一的构图、透视、配色依据就是它。'
            u'生成一段约 8 秒、16:9 横版、超高清的测试主题 PPT 动态背景视频。' % a1)
        io.open(os.path.join(cli_att, 'promptpack', 'p%03d_video.txt' % pb), 'w', encoding='utf-8').write(
            u'首帧附件用 %s，画面唯一的构图、透视、配色依据就是它。'
            u'生成一段约 8 秒、16:9 横版、超高清的测试主题 PPT 动态背景视频。' % a2)
        rc_ok, t_ok = run_check_at(cli_att)
        shared_gone = (u'首帧附件是同一张图' not in t_ok
                       and u'首帧附件其实是同一张图' not in t_ok)
        notes.append(u"G15 反证（两页首帧附件同一张图）：命中=%s，改成各自专属图后消失=%s -> %s" % (
            shared_hit, shared_gone, 'ok' if (shared_hit and shared_gone) else 'FAIL'))
        if not (shared_hit and shared_gone):
            fails.append(u"G15 不咬/误伤：两页共用同一张首帧附件没被发现，或改开后仍报（命中=%s 消失=%s）"
                         % (shared_hit, shared_gone))
        for _p in (pa, pb):
            io.open(os.path.join(cli_att, 'promptpack', 'p%03d_video.txt' % _p), 'w',
                    encoding='utf-8').write(
                u'首帧附件用 %s，画面唯一的构图、透视、配色依据就是它。'
                u'生成一段约 8 秒、16:9 横版、超高清的测试主题 PPT 动态背景视频。'
                % os.path.join(cli_att, 'gen', 'p%03d' % _p, 'image.png'))
        rc_bl, t_bl = run_check_at(cli_att)
        blind_hit = u'量不出结果' in t_bl
        for _p in (pa, pb):
            _vs = os.path.join(cli_att, 'gen', 'p%03d' % _p, 'video_source.json')
            json.dump({'src': os.path.join(cli_att, 'gen', 'p%03d' % _p, 'video.mp4'),
                       'attachment': os.path.join(cli_att, 'gen', 'p%03d' % _p, 'image.png')},
                      io.open(_vs, 'w', encoding='utf-8'))
        rc_bl2, t_bl2 = run_check_at(cli_att)
        blind_gone = u'量不出结果' not in t_bl2
        notes.append(u"G15 反证（尺子被 poster 顶平=量不出结果）：无凭证命中=%s，"
                     u"补上认领凭证 attachment 后消失=%s -> %s" % (
                         blind_hit, blind_gone, 'ok' if (blind_hit and blind_gone) else 'FAIL'))
        if not (blind_hit and blind_gone):
            fails.append(u"G15 不咬/误伤：poster 顶平尺子没报，或记了 attachment 仍报（命中=%s 消失=%s）"
                         % (blind_hit, blind_gone))
    else:
        notes.append("G15 反证：本机 ffmpeg 造不出 >64KB 的 1080p 测试片段，跳过 -> 需人工确认")
        fails.append("G15 首帧链/重复视频的反证没跑成（ffmpeg 造片段失败）")

    # 推镜兜底的装配级反证：独立工作目录（cli 的 p001 早被别的夹具放过 mp4，不能在里面造
    # "video_bg 缺视频"的前提）。拷一份 cli、抽掉 p001 的 mp4、只留静态图 → build 必须真写
    # 12s/6% 推镜并记 bg_push_in；其余无视频页不许带。
    _push_wired = False
    try:
        _pc = os.path.join(TMP, 'push_cli')
        shutil.rmtree(_pc, ignore_errors=True)
        shutil.copytree(cli, _pc)
        _pv = os.path.join(_pc, 'gen', 'p001', 'video.mp4')
        if os.path.exists(_pv):
            os.remove(_pv)
        _pd = os.path.join(_pc, 'gen', 'p001')
        os.makedirs(_pd, exist_ok=True)
        # 噪点图：纯色底 zoompan 出片 <64KB 会被当占位拒，夹具需要真能出推镜 mp4
        import random as _rndp
        _pim = Image.new('RGB', (1920, 1080))
        _pim.putdata([(_rndp.randint(0, 60), _rndp.randint(20, 90), _rndp.randint(40, 140))
                      for _ in range(1920 * 1080)])
        _pim.save(os.path.join(_pd, 'image.png'))
        _psp = os.path.join(_pc, 'shots.json')
        _psh = json.load(io.open(_psp, encoding='utf-8'))
        for _s in _psh['pages']:
            if _s['page'] == 1:
                _s['asset_kind'] = 'video_bg'
        json.dump(_psh, io.open(_psp, 'w', encoding='utf-8'), ensure_ascii=False)
        subprocess.run([sys.executable, engine, 'build', '--work', _pc],
                       capture_output=True, text=True, encoding='utf8', errors='replace')
        _pbl = json.load(io.open(os.path.join(_pc, 'out', 'build_log.json'), encoding='utf-8'))
        _r1 = [r for r in _pbl if r.get('page') == 1]
        _ro = [r for r in _pbl if r.get('page') != 1 and not r.get('video')]
        _push_wired = bool(_r1 and _r1[0].get('bg_push_in') and _r1[0].get('video')) and \
            all(not r.get('bg_push_in') for r in _ro)
        notes.append("推镜兜底装配级：video_bg 无视频页 bg_push_in=%s（且真出满铺推镜 mp4=%s）、其余无视频页"
                     "全无推镜=%s -> %s" % (
                         bool(_r1 and _r1[0].get('bg_push_in')),
                         bool(_r1 and _r1[0].get('video')),
                         all(not r.get('bg_push_in') for r in _ro),
                         'ok' if _push_wired else 'FAIL'))
    except Exception as _epw:
        notes.append("推镜兜底装配级没跑成：%s" % str(_epw)[:120])
    if not _push_wired:
        fails.append("推镜兜底没接进装配：video_bg 缺视频的页必须记 bg_push_in 且真出满铺推镜 mp4"
                     "（语料法 attach_video，非 animScale），其余无视频页不许带")

    # ---------------- SPEC §16：目检抓到的四类"分数看不见"的缺陷，规则与闸都要能咬
    import data_layout as _DL16
    _f20 = _DL16.items_from_numbers(
        [u'主办方的口径是为不少于 500 名女性提供乳腺',
         u'现场另向 300 位“三八红旗手”等女性代表捐赠健康体检卡',
         u'这个 500 要按原样读'])
    _f70 = _DL16.items_from_numbers(
        [u'车长 7 米', u'座位 12 个', u'外廓 5070 毫米', u'整备质量 385 千克'])
    _city16 = _DL16.items_from_numbers(
        [u'杭州 9 家', u'宁波 7 家', u'温州 4 家', u'绍兴 3 家'])
    _ser_ok = (not _f20 and not _f70 and len(_city16) >= 4 and all(c[0] for c in _city16))
    notes.append(u"条形组只在真系列上画：不可比的三数=%s 公告参数=%s 四城系列=%d 条全带类别名 -> %s" % (
        len(_f20), len(_f70), len(_city16), u'ok' if _ser_ok else u'FAIL'))
    if not _ser_ok:
        fails.append(u"假图表规则不成立：'500 名女性/300 位/这个 500' 与 7 米/12 座/5070mm "
                     u"这类不可比数字必须出不了组（items_from_numbers 返空），"
                     u"而 '杭州 9 家…' 这种真系列必须每条都带类别名")

    _geo16_ok = False
    try:
        from pptx import Presentation as _P16
        from pptx.util import Inches as _I16
        import study_data_design as _SD16
        _pal16 = {'panel_hex': '003069', 'accent_hex': '016DFF',
                  'cyan_hex': '66D9F7', 'page_bg': '09192B'}
        _ink16 = {'text': 'FFFFFF', 'muted': '9FB6CE'}
        _pr16 = _P16()
        _pr16.slide_width, _pr16.slide_height = _I16(13.333), _I16(7.5)
        _sa = _pr16.slides.add_slide(_pr16.slide_layouts[6])
        _DL16.bar_group(_sa, [(u'杭州', 9.0, u'9 家'), (u'宁波', 7.0, u'7 家'),
                              (u'温州', 4.0, u'4 家')], [0.08, 0.26, 0.84, 0.52],
                        _pal16, _ink16)
        _sb = _pr16.slides.add_slide(_pr16.slide_layouts[6])
        _DL16.number_row(_sb, [(u'三级医院', 89.6, u'89.6%', u'%'),
                               (u'同期专科', 77.65, u'77.65%', u'%')],
                         [0.08, 0.60, 0.84, 0.26], _pal16, _ink16, C.add_text)
        _sc = _pr16.slides.add_slide(_pr16.slide_layouts[6])
        _DL16.chip_strip(_sc, [u'全国妇产 45.37%', u'2023 年'], [0.08, 0.62, 0.84, 0.14],
                         _pal16, _ink16, C.add_text)
        _gp16 = os.path.join(TMP, 'parity_shape16.pptx')
        _pr16.save(_gp16)
        import deck_parity as _DP16
        _rw16 = _DP16.measure_deck(_gp16)
        _geo16_ok = bool(_rw16) and _rw16[0]['bars'] >= 3 and \
            _rw16[1]['cards'] >= 3 and _rw16[2]['cards'] >= 3
        notes.append(u"数据件按语料比例画才被同级尺认出（柱 bars=%s 卡 cards=%s/%s）-> %s" % (
            _rw16[0]['bars'], _rw16[1]['cards'], _rw16[2]['cards'],
            u'ok' if _geo16_ok else u'FAIL'))
    except Exception as _eg16:
        notes.append(u"数据件几何反证没跑成：%s" % str(_eg16)[:120])
    if not _geo16_ok:
        fails.append(u"数据件几何不合语料口径：bar_group 必须画出 ≥3 根同宽细柱（高/宽 ≥1.6），"
                     u"number_row/chip_strip 必须画出 ≥3 个 0.08–0.35 页宽 × 0.08–0.45 页高的承载面板，"
                     u"否则图形化率是假的")

    _rep16 = os.path.join(cli, u'report', u'验收报告.md')

    def _chk16():
        if os.path.exists(_rep16):
            os.remove(_rep16)
        subprocess.run([sys.executable, engine, 'build', '--work', cli],
                       capture_output=True, text=True, encoding='utf8', errors='replace')
        subprocess.run([sys.executable, engine, 'check', '--work', cli],
                       capture_output=True, text=True, encoding='utf8', errors='replace')
        return io.open(_rep16, encoding='utf-8').read() if os.path.exists(_rep16) else u''

    _ol_p16 = os.path.join(cli, 'outline.json')
    _ol16 = json.load(io.open(_ol_p16, encoding='utf-8'))
    # 夹具前提必须先取证：truncated 判据量的是"完整串在全簿还在不在"，而 '2026' 这类年份
    # 在落款和别页到处都是——在那种页注入真前缀，闸不报才是正确行为。上一版只挑第一个
    # ≥3 位的数，套件重排后恰好撞上 2026，反证就"不咬"（实为夹具失效，不是闸坏）。
    # 现在只挑**全册唯一出现**的数字 token，前提成立才做注入/撤销差分。
    _chk16()
    _dk16_0 = os.path.join(cli, 'out', 'deck.pptx')
    _all16 = (C.norm_content(C.pptx_all_text(_dk16_0))
              if os.path.exists(_dk16_0) else u'')
    _pi16, _tok16 = None, None
    for _i16, _p16 in enumerate(_ol16['pages']):
        _own16 = re.sub(u'\\s', u'', u' '.join(
            list(_p16.get('source_span') or _p16.get('body') or [])))
        for _m16 in re.finditer(r'\d[\d,\.]{2,}', _own16):
            _t16 = _m16.group(0)
            # 取样域必须与判据声明的域一致：g24 这条写的是"本页原文里的更长数字"，
            # 所以 token 既要在这页原文里，又要全册唯一（注入后完整串真从屏上消失）。
            # 跨页截断（数属于台账另一句）不在这条判据的声明范围内，另见 #57 记账，
            # 不许把那条情形塞进这条反证来"顺手证明闸覆盖了跨页"。
            if len(_t16) >= 3 and _all16.count(C.norm_content(_t16)) == 1:
                _pi16, _tok16 = _i16, _t16
                break
        if _tok16:
            break
    _trunc_hit = _trunc_clear = False
    if _tok16:
        # 装配侧 repair_head_number 会在 build 时把断头数补回，所以闸的反证必须直接改成品 XML：
        # 把某一页标题改成原文数字的真前缀，check 必须拦；重装配还原后必须放行
        _chk16()
        _dk16 = os.path.join(cli, 'out', 'deck.pptx')
        from pptx import Presentation as _P16t
        _pr16t = _P16t(_dk16)
        _sl16t = _pr16t.slides[_pi16]
        _done16 = False
        for _sp16 in _sl16t.shapes:
            if _sp16.has_text_frame and (_sp16.name or u'').startswith(u'title'):
                _tf16 = _sp16.text_frame
                _sz16 = None
                for _pa in _tf16.paragraphs:
                    for _r in _pa.runs:
                        if _r.font.size:
                            _sz16 = _r.font.size
                            break
                    if _sz16:
                        break
                # 必须整段替换：标题常被 break_head 拆成多段，只改第 0 段的 run 会把
                # 残留段拼在截断数后面，造出 '2.46.9' 这种原文里根本没有的畸形 token——
                # 那不是"裁在数字中间"，是夹具自己把样本做坏了（上一轮就是这样假失败的）。
                _tf16.clear()
                _rn16 = _tf16.paragraphs[0].add_run()
                _rn16.text = u'覆盖 ' + _tok16[:2]
                if _sz16:
                    _rn16.font.size = _sz16
                _after16 = C.norm_content(u' '.join(
                    s.text_frame.text for s in _sl16t.shapes if s.has_text_frame))
                _done16 = (C.norm_content(_tok16) not in _after16
                           and C.norm_content(_tok16[:2]) in _after16)
                if not _done16:
                    notes.append(u"断头数字反证的注入没落地：注入后该页仍含完整串=%s 或不含前串=%s"
                                 % (C.norm_content(_tok16) in _after16,
                                    C.norm_content(_tok16[:2]) not in _after16))
                break
        if _done16:
            _pr16t.save(_dk16)
            if os.path.exists(_rep16):
                os.remove(_rep16)
            subprocess.run([sys.executable, engine, 'check', '--work', cli],
                           capture_output=True, text=True, encoding='utf8', errors='replace')
            _trunc_hit = (u'前缀' in (io.open(_rep16, encoding='utf-8').read()
                                     if os.path.exists(_rep16) else u''))
        _trunc_clear = (u'前缀' not in _chk16())
    notes.append(u"断头数字闸（G24 truncated）：全册唯一数字 token=%s，注入 '覆盖 %s' ← 原文 '%s' "
                 u"被拦=%s｜还原后放行=%s -> %s" % (
                     u'有' if _tok16 else u'无(前提不成立)',
                     _tok16[:2] if _tok16 else u'-', _tok16 or u'-', _trunc_hit, _trunc_clear,
                     u'ok' if (_trunc_hit and _trunc_clear) else u'FAIL'))
    if not _tok16:
        fails.append(u"断头数字反证的夹具失效：这册里找不到一个「全册只出现一次」的 ≥3 位数字，"
                     u"注入真前缀时完整串仍可能在别处出现，闸不报才是对的——必须先把前提做出来，"
                     u"不许把跳过当通过")
    elif not (_trunc_hit and _trunc_clear):
        fails.append(u"断头数字闸不咬或误伤：标题把 '268' 裁成 '26' 这类必须判不合格，"
                     u"补全后必须放行（装配侧 repair_head_number 要真把整串补回）")

    # (5) G9：覆盖不变量必须能失败（用闸自己那份判定）
    good = C.coverage_invariants(C.kb('coverage.json'))
    broken = {'rendered_decks': 171, 'decks_total': 172, 'rendered_slides': 2200,
              'slides_total': 2237, 'render_matches_ledger': False,
              'videos_frames_extracted': 200, 'unique_videos': 250,
              'videos_failed': 3, 'prompt_blocks': 40}
    bad = C.coverage_invariants(broken)
    g9_ok = (not good) and len(bad) >= 5
    notes.append("G9 不变量（共用同一份判定）：真台账违规=%d，坏台账违规=%d 条 -> %s" % (
        len(good), len(bad), 'ok' if g9_ok else 'FAIL'))
    if not g9_ok:
        fails.append("G9 覆盖不变量不咬或误伤：真台账 %s / 坏台账 %d 条" % (good, len(bad)))

    # (6) G10：ai_tells 永远退出 0，分数必须由闸自己读出来
    audit_sample = (u'at_ratio     0.61   [target<0.35]\n  slogan top: 口号=15\n'
                    u'SCORE sample.md = 44.5\n')
    s_bad, h_bad = C.parse_ai_tells(audit_sample)
    s_good, h_good = C.parse_ai_tells(u'SCORE deck_text.md = 99.0\n  circled_lines  0  [target=0 in prose]\n')
    s_none, _ = C.parse_ai_tells(u'子进程 GBK 崩了，没有分数行\n')
    g10_ok = (s_bad == 44.5 and s_bad < 70) and s_good == 99.0 and s_none is None
    notes.append("G10 分数解析（复验给的 AI 味样本 44.5 / 我们的 99.0 / 无分数输出）：%.1f / %s / %s -> %s" % (
        s_bad or -1, s_good, s_none, 'ok' if g10_ok else 'FAIL'))
    if not g10_ok:
        fails.append("G10 读不出分数或阈值判错：%s %s %s" % (s_bad, s_good, s_none))

    # (7) 文字变长但框不动：必须留下 overflow 痕迹
    ol = os.path.join(cli, 'outline.json')
    olj = json.load(io.open(ol, encoding='utf-8'))
    olj['verbatim'] = True      # 溢出这条反证管的是逐句上屏那条路（提炼模式下短句本来就不溢出）
    long_line = u'这一页故意塞入超长正文' + u'，各城市签约门店数、履约率与单店月出货都要在同一页交代清楚' * 40
    for pg in olj['pages']:
        if pg['page'] == 3:
            pg['body'] = [long_line]
    json.dump(olj, io.open(ol, 'w', encoding='utf-8'), ensure_ascii=False)
    subprocess.run([sys.executable, engine, 'build', '--work', cli],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    blog = json.load(io.open(os.path.join(cli, 'out', 'build_log.json'), encoding='utf-8'))
    rec3 = [b for b in blog if b.get('page') == 3]
    flagged = bool(rec3 and (rec3[0].get('overflow') or rec3[0].get('overflow_lines')))
    rc_ov, t_ov = run_check_at(cli)
    ov_ok = flagged and (u'超出留白区' in t_ov) and rc_ov != 0
    notes.append("溢出反证（页3 塞入超长正文）：build_log 记了 overflow=%s，check 点名超出留白区=%s -> %s" % (
        bool(rec3 and (rec3[0].get('overflow') or rec3[0].get('overflow_lines'))),
        u'超出留白区' in t_ov, 'reject' if ov_ok else 'PASS(错)'))
    if not ov_ok:
        fails.append("文字溢出藏住了：框高压到剩余空间却没记 overflow、闸也不报错")

    # --- SPEC 阈值与执行口径对齐后的反证（复验点名"文档写 7/9 代码跑 6/9"那类漂移） ----
    sh = json.load(io.open(sp, encoding='utf-8'))
    for s in sh['pages']:
        if s['page'] == 3:
            s['tech_recipe'] = 'T99_不存在的卡'
        if s['page'] == 2:
            s['role'] = 'divider'
            s['motion'] = ''
            s['tech_recipe'] = ''
            s['morph_from_prev'] = False
            s['asset_kind'] = 'none'
    json.dump(sh, io.open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
    rc_tr, t_tr = run_check_at(cli)
    tr_bites = u'不是 kb/deck_tech.json 里的技法卡' in t_tr
    notes.append("G2 反证（tech_recipe 写一个不存在的技法卡）：命中=%s -> %s" % (
        tr_bites, 'reject' if tr_bites else 'PASS(错)'))
    if not tr_bites:
        fails.append("G2 不咬：tech_recipe 指向不存在的卡片却没被发现")

    # G6/G14 现在量的是成品：先数真 build 出来的 slide XML（正证），再把 timing 与 morph
    # 从 pptx 里整个抠掉（反证）。旧版只改 shots.json 的计划字段就能"造"出不合格，
    # 说明它量的是计划不是交付物（audit4 [中]4）。
    deck_cli = os.path.join(cli, 'out', 'deck.pptx')
    facts0 = C.deck_motion_facts(deck_cli)
    entr_pages = sorted(k for k, v in facts0.items() if v['entr_nodes'])
    morph_pages0 = sorted(k for k, v in facts0.items() if v['morph_ms'])
    notes.append("G6 正证（数成品 XML）：%d 页带入场动效节点、%d 页带 morph（时长 %s ms）-> %s" % (
        len(entr_pages), len(morph_pages0),
        sorted({facts0[k]['morph_ms'] for k in morph_pages0}),
        'ok' if entr_pages and morph_pages0 else 'FAIL'))
    if not entr_pages:
        fails.append("build 没把入场动效写进成品 OOXML，G6 只能空转")
    if not morph_pages0:
        fails.append("成品里一页 morph 都没有，隔页视差不成立")

    def strip_motion(deck_path):
        import zipfile as _zz
        zin = _zz.ZipFile(deck_path)
        items = {n: zin.read(n) for n in zin.namelist()}
        zin.close()
        for n in list(items):
            if re.match(r'ppt/slides/slide\d+\.xml$', n):
                t = items[n].decode('utf-8', 'replace')
                t = re.sub(r'<p:timing[\s>].*?</p:timing>', '', t, flags=re.S)
                t = re.sub(r'<mc:AlternateContent[\s>].*?</mc:AlternateContent>', '', t, flags=re.S)

                items[n] = t.encode('utf-8')
        tmp = deck_path + '.tampered'
        with _zz.ZipFile(tmp, 'w', _zz.ZIP_DEFLATED) as zo:
            for k, v in items.items():
                zo.writestr(k, v)
        shutil.move(tmp, deck_path)

    strip_motion(deck_cli)
    facts1 = C.deck_motion_facts(deck_cli)
    rc_mt, t_mt = run_check_at(cli)
    m_before = sum(1 for v in facts0.values() if v['entr_nodes'] or v['morph_ms'])
    m_after = sum(1 for v in facts1.values() if v['entr_nodes'] or v['morph_ms'])
    motion_dropped = m_after < m_before
    g14_bites = u'页平滑' in t_mt
    notes.append("G6/G14 反证（从 pptx 抠掉全部 timing+morph）：带动效页数 %d→%d（测量随成品变=%s），"
                 "平滑≥2 页命中=%s -> %s" % (
                     m_before, m_after, motion_dropped, g14_bites,
                     'reject' if (motion_dropped and g14_bites) else 'PASS(错)'))
    if not motion_dropped:
        fails.append("G6 量的还是计划：成品里删光 timing/transition 后，动效页数没有掉下来")
    if not g14_bites:
        fails.append("G14 不咬：成品里删光 morph 后没报「平滑页数不足」")

    subprocess.run([sys.executable, engine, 'build', '--work', cli],
                   capture_output=True, text=True, encoding='utf8', errors='replace')

    # (2b) G1 短正文 / 中文数字断言：旧版跳过 len<8 的文本框，7 个字的假话能直接上片
    ol2_p = os.path.join(cli, 'outline.json')
    ol2 = json.load(io.open(ol2_p, encoding='utf-8'))
    ol2['verbatim'] = True      # 短句假话要"上屏"才谈得上溯源，这条走的也是逐句上屏
    for pg in ol2['pages']:
        if pg['page'] == 3:
            pg['body'] = list(pg.get('body') or []) + [u'明年营收翻三倍', u'全部达标']
    json.dump(ol2, io.open(ol2_p, 'w', encoding='utf-8'), ensure_ascii=False)
    subprocess.run([sys.executable, engine, 'build', '--work', cli],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_sh, t_sh = run_check_at(cli)
    short_hit = (u'G1:' in t_sh) and (u'明年营收翻三倍' in t_sh or u'全部达标' in t_sh)
    for pg in ol2['pages']:
        if pg['page'] == 3:
            pg['body'] = [x for x in (pg.get('body') or [])
                          if x not in (u'明年营收翻三倍', u'全部达标')]
    json.dump(ol2, io.open(ol2_p, 'w', encoding='utf-8'), ensure_ascii=False)
    subprocess.run([sys.executable, engine, 'build', '--work', cli],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_sh2, t_sh2 = run_check_at(cli)
    # 撤销后只该剩下前面溢出用例故意留下的长句假话；我塞的两句必须从报告里彻底消失
    short_recovers = (u'明年营收翻三倍' not in t_sh2) and (u'全部达标' not in t_sh2)
    ol2.pop('verbatim', None)
    json.dump(ol2, io.open(ol2_p, 'w', encoding='utf-8'), ensure_ascii=False)

    notes.append("G1 反证（7 字假话「明年营收翻三倍」+ 无数字「全部达标」进正文框）：拦下=%s，"
                 "撤销后恢复=%s -> %s" % (short_hit, short_recovers,
                                          'ok' if (short_hit and short_recovers) else 'FAIL'))
    if not short_hit:
        fails.append("G1 仍放过短正文：成品里的短句假话没被点名")
    if not short_recovers:
        fails.append("G1 误伤：撤掉假句子后仍然报溯源问题（%s）" % (
            (re.search(u'G1:[^\\n]*', t_sh2) or [u''])[0][:120]))


    # pack 只要有一页不合格就必须非 0（复验实测：9 页合格 1 页空着也当成功）
    pp_dir = os.path.join(cli, 'promptpack')
    os.makedirs(pp_dir, exist_ok=True)
    sh = json.load(io.open(sp, encoding='utf-8'))
    for s in sh['pages']:
        s['asset_kind'] = 'image'
        s['need_material'] = True
        s['role'] = 'data' if s['page'] != 1 else 'cover'
    json.dump(sh, io.open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
    for f in os.listdir(pp_dir):
        os.remove(os.path.join(pp_dir, f))
    r_pk = subprocess.run([sys.executable, engine, 'pack', '--work', cli],
                          capture_output=True, text=True, encoding='utf8', errors='replace')
    pack_bites = r_pk.returncode != 0
    notes.append("pack 反证（要求素材却零提示词）：退出码=%d -> %s" % (
        r_pk.returncode, 'reject' if pack_bites else 'PASS(错)'))
    if not pack_bites:
        fails.append("pack 不咬：有不合格页仍然退出码 0")

    # --allow-still：降级必须留痕，不能变成"静默通过"
    tbl_dir = os.path.join(TMP, 'tbl')
    rc_plain, t_plain = run_check_at(tbl_dir)
    video_hard = (u'mp4' in t_plain) or (u'视频页' in t_plain)
    if os.path.exists(os.path.join(tbl_dir, 'report', u'验收报告.md')):
        os.remove(os.path.join(tbl_dir, 'report', u'验收报告.md'))
    r_as = subprocess.run([sys.executable, engine, 'check', '--work', tbl_dir, '--allow-still'],
                          capture_output=True, text=True, encoding='utf8', errors='replace')
    t_as = io.open(os.path.join(tbl_dir, 'report', u'验收报告.md'), encoding='utf-8').read() \
        if os.path.exists(os.path.join(tbl_dir, 'report', u'验收报告.md')) else ''
    concl = t_as.split(u'## 结论')[-1] if u'## 结论' in t_as else ''
    if r_as.returncode == 0:
        # 除了"没有视频"没有别的硬伤时，结论行必须自己写明"降级交付、不算电影级"，
        # 不许再写光秃秃的「全部闸通过」——把放行当满分就是假绿
        disclosed = (u'降级交付' in concl) and (u'不算' in concl) and (u'**全部闸通过。**' not in concl)
    else:
        # 还有别的硬伤：结论照实列硬伤，同时报告里必须带着降级小节（不许静默吞掉）
        disclosed = (u'降级交付_无视频背景' in t_as) and (u'不合格' in concl)
    still_ok = (not video_hard) or disclosed
    notes.append(u"--allow-still（用户不投放视频）：不开时视频硬伤=%s，开后 rc=%d，"
                 u"结论口径诚实（全绿自报降级 / 有硬伤照实列）=%s -> %s" % (
                     video_hard, r_as.returncode, disclosed, u'ok' if still_ok else u'FAIL'))
    if video_hard and not still_ok:
        fails.append(u"--allow-still 的结论口径不诚实：全绿时没自报降级，或有硬伤时没照实列出（假绿）")



    # plan 喂非 docx 必须是友好停止，不是 Python traceback
    notdoc = os.path.join(cli, 'notadocx.txt')
    io.open(notdoc, 'w', encoding='utf-8').write(u'这不是 docx')
    r_nd = subprocess.run([sys.executable, engine, 'plan', notdoc, '--work', cli, '--auto-pages'],
                          capture_output=True, text=True, encoding='utf8', errors='replace')
    nd_ok = r_nd.returncode == 2 and u'不是' in (r_nd.stdout or '') and 'Traceback' not in (r_nd.stderr or '')
    notes.append("plan 反证（喂 .txt）：退出码=%d 友好停止=%s -> %s" % (
        r_nd.returncode, nd_ok, 'reject' if nd_ok else 'PASS(错)'))
    if not nd_ok:
        fails.append("plan 对非 docx 输入不是友好停止（rc=%d，stderr 有 traceback=%s）" % (
            r_nd.returncode, 'Traceback' in (r_nd.stderr or '')))

    # --- 全链沙箱（plan→scaffold→pack→build）：G2 / G3 / 媒体绑定 / GIF / 截图指纹 --------
    # 这一节专门盯第四轮复验点名的"假绿"：pack.json 被删后 G2 变成空闸、素材被换成同尺寸
    # 的另一张后 G3 照旧报绿、建完再动 mp4/GIF 没人管、验收截图可被替换。全部改成量交付物。
    gs = os.path.join(TMP, 'gs')
    shutil.rmtree(gs, ignore_errors=True)
    os.makedirs(gs)
    gs_doc = os.path.join(gs, 'doc.docx')
    _g = _dx.Document()
    _g.add_heading(u'门店运营复盘', 0)
    _g.add_heading(u'一、履约情况', 1)
    _g.add_paragraph(u'2026 年 3 月华东 12 城实际履约 214 家，履约率 79.9%，单店月均出货 1.6 万元。')
    _g.add_paragraph(u'第二，客诉处理时长从 72 小时压缩到 26 小时，退费率 3.1%。')
    _g.add_heading(u'二、下阶段动作', 1)
    _g.add_paragraph(u'把 412 家潜在门店分成 A/B 两池，A 池 96 家本周完成签约。')
    _g.save(gs_doc)

    def gs_run(args):
        return subprocess.run([sys.executable, engine] + args + [u'--work', gs],
                              capture_output=True, text=True, encoding='utf8', errors='replace')

    def gs_check():
        rep = os.path.join(gs, u'report', u'验收报告.md')
        if os.path.exists(rep):
            os.remove(rep)
        r = subprocess.run([sys.executable, engine, u'check', u'--work', gs],
                           capture_output=True, text=True, encoding='utf8', errors='replace')
        return r.returncode, (io.open(rep, encoding=u'utf-8').read() if os.path.exists(rep) else u'')

    chain_bad = []
    r_ = subprocess.run([sys.executable, engine, u'plan', gs_doc, u'--work', gs, '--auto-pages'],
                        capture_output=True, text=True, encoding='utf8', errors='replace')
    if r_.returncode != 0:
        chain_bad.append(u'plan rc=%d %s' % (r_.returncode, (r_.stdout or u'')[-120:]))
    r2 = gs_run([u'scaffold'])
    if r2.returncode != 0:
        chain_bad.append(u'scaffold rc=%d %s' % (r2.returncode, (r2.stdout or u'')[-120:]))
    # 抽象主体页必须按 SKILL 的口令补一个具体场景词再重跑 scaffold（这是文档里写明的恢复路径）
    shg0 = json.load(io.open(os.path.join(gs, u'shots.json'), encoding=u'utf-8'))
    need_sub = [s_ for s_ in shg0[u'pages'] if s_.get(u'subject_needs_review')]
    if need_sub:
        args = [u'scaffold']
        for s_ in need_sub:
            args += [u'--subject', u'%d=城市街区的门店清晨开门营业' % s_[u'page']]
        r2b = gs_run(args)
        if r2b.returncode != 0:
            chain_bad.append(u'scaffold --subject rc=%d' % r2b.returncode)
    notes.append(u"抽象主体恢复路径：%d 页被 scaffold 标 subject_needs_review，补 --subject 后重跑 -> %s" % (
        len(need_sub), u'ok' if not [c for c in chain_bad if c.startswith(u'scaffold')] else u'FAIL'))
    r2 = gs_run([u'pack'])
    if r2.returncode != 0:
        chain_bad.append(u'pack rc=%d %s' % (r2.returncode, (r2.stdout or u'')[-160:]))
    prep_delivery(gs, chain_bad)
    r2 = gs_run([u'build'])
    if r2.returncode != 0:
        chain_bad.append(u'build rc=%d %s' % (r2.returncode, (r2.stdout or u'')[-160:]))

    notes.append(u"全链沙箱（plan→scaffold→pack→build）：%s -> %s" % (
        u'四步全退出码 0' if not chain_bad else chain_bad, u'ok' if not chain_bad else u'FAIL'))
    if chain_bad:
        fails.append(u"全链沙箱没建起来，G2/G3/GIF/截图反证无法执行：%s" % chain_bad)
    else:
        try:
            # (A) G2：手改提示词不重跑 pack / 直接删 pack.json
            gsc = os.path.join(gs, u'shots.json')
            pdir = os.path.join(gs, u'promptpack')
            p1 = os.path.join(pdir, u'p001_image.txt')
            if not os.path.exists(p1):
                files0 = sorted(os.listdir(pdir))
                p1 = os.path.join(pdir, files0[0]) if files0 else None
            base_txt = io.open(p1, encoding=u'utf-8').read() if p1 else u''
            rc0, t0 = gs_check()
            g2_quiet_before = (u'G2:' not in t0) and (u'一致' in t0)

            # (A0) 主体直写两条硬判据（2026-11-09 用户"提示词为何重复发"查实后加的）：
            #      ① 旧的"与「章节名」直接相关的核心视觉主体"间接句式必须被拦；
            #      ② 两页主体特征逐字相同必须被拦；撤销后必须零报。
            _imgs = sorted(f for f in os.listdir(pdir) if f.endswith(u'_image.txt'))
            if len(_imgs) >= 2:
                import re as _re0
                fa, fb = [os.path.join(pdir, x) for x in _imgs[:2]]
                ta, tb = io.open(fa, encoding=u'utf-8').read(), io.open(fb, encoding=u'utf-8').read()
                _mA = _re0.search(u'主体特征：[^\n]*\n', ta)
                _mB = _re0.search(u'主体特征：[^\n]*\n', tb)
                _ind = u'主体特征：画面中央偏下设置与「测试章节」直接相关的核心视觉主体，占画面宽度约 22%。\n'
                io.open(fa, u'w', encoding=u'utf-8').write(ta.replace(_mA.group(0), _ind, 1))
                rc_i, t_i = gs_check()
                ind_bites = u'间接话' in t_i
                io.open(fa, u'w', encoding=u'utf-8').write(ta)      # 先撤销①，再单独验②
                io.open(fb, u'w', encoding=u'utf-8').write(
                    tb.replace(_mB.group(0), _mA.group(0), 1))
                rc_d, t_d = gs_check()
                dup_bites = u'逐字相同' in t_d
                io.open(fb, u'w', encoding=u'utf-8').write(tb)
                rc_r, t_r = gs_check()
                subj_clean = (u'间接话' not in t_r) and (u'逐字相同' not in t_r)
                notes.append(u"G2 主体直写反证：间接句式命中=%s，两页主体句逐字相同命中=%s，"
                             u"撤销后零误报=%s -> %s" % (
                                 ind_bites, dup_bites, subj_clean,
                                 'ok' if (ind_bites and dup_bites and subj_clean) else 'FAIL'))
                if not (ind_bites and dup_bites and subj_clean):
                    fails.append(u"G2 主体判据不咬或误伤（间接=%s 重复=%s 撤销后干净=%s）" % (
                        ind_bites, dup_bites, subj_clean))

            cut = base_txt
            for sec in (u'光线', u'色彩', u'材质'):
                cut = cut.replace(sec, u'')
            io.open(p1, u'w', encoding=u'utf-8').write(cut)
            rc1, t1 = gs_check()
            g2_bites = (u'G2:' in t1) and (u'不一致' in t1)
            os.remove(os.path.join(pdir, u'pack.json'))
            rc2, t2 = gs_check()
            g2_no_pack = (u'G2:' in t2) and (u'未跑 pack' in t2)
            io.open(p1, u'w', encoding=u'utf-8').write(base_txt)
            gs_run([u'pack'])
            rc3, t3 = gs_check()
            g2_recovers = u'G2:' not in t3
            notes.append(u"G2 反证（改坏一份提示词不重跑 pack→再删 pack.json）：初检安静=%s，"
                         u"手改被拦=%s，删凭据后仍能重评=%s，恢复后=%s -> %s" % (
                             g2_quiet_before, g2_bites, g2_no_pack, g2_recovers,
                             u'ok' if (g2_bites and g2_no_pack and g2_recovers) else u'FAIL'))
            if not g2_bites:
                fails.append(u"G2 不咬：提示词被改坏（缺 3 段）却没重跑 pack，check 仍没报结构问题")
            if not g2_no_pack:
                fails.append(u"G2 仍是空闸：删掉 pack.json 后 bad_pages 归零，说明它读的是凭据不是提示词")
            if not g2_recovers:
                fails.append(u"G2 误伤：提示词恢复并重新 pack 后仍然报错")

            # (B) G3：偷换成同尺寸的另一张图（manifest 的 SHA1/字节不动）
            mat = os.path.join(gs, u'materials')
            os.makedirs(mat, exist_ok=True)
            f1 = os.path.join(mat, u'p001.jpg')
            Image.effect_noise((2400, 1350), 55).convert(u'RGB').save(f1, quality=90)
            rec = {u'page': 1, u'file': f1, u'w': 4200, u'h': 2800,
                   u'bytes': os.path.getsize(f1), u'sha1': C.sha1(f1),
                   u'url': u'https://upload.wikimedia.org/wikipedia/commons/a/ab/Shanghai_skyline.jpg',
                   u'source_page': u'https://commons.wikimedia.org/wiki/File:Shanghai_skyline.jpg',
                   u'title': u'Shanghai skyline at dusk', u'artist': u'Qoder Test',
                   u'license': u'CC BY-SA 4.0', u'relevance': 90,
                   u'proxy_used': u'http://127.0.0.1:7897'}

            mf = os.path.join(mat, u'manifest.jsonl')
            io.open(mf, u'w', encoding=u'utf-8').write(json.dumps(rec, ensure_ascii=False) + u'\n')
            rcA, tA = gs_check()
            honest_quiet = u'SHA1/字节数不符' not in tA
            Image.effect_noise((2400, 1350), 90).convert(u'RGB').save(f1, quality=95)
            rcB, tB = gs_check()
            swap_bites = (u'G3:' in tB) and (u'SHA1/字节数不符' in tB)
            rec[u'sha1'] = C.sha1(f1)
            rec[u'bytes'] = os.path.getsize(f1)
            io.open(mf, u'w', encoding=u'utf-8').write(json.dumps(rec, ensure_ascii=False) + u'\n')
            rcC, tC = gs_check()
            swap_quiet_after = u'SHA1/字节数不符' not in tC
            # (B2) 联网口径硬约束：国外 URL 少代理、国内 URL 挂代理，两个方向都要拦
            mf_no = dict(rec)
            mf_no.pop(u'proxy_used', None)
            io.open(mf, u'w', encoding=u'utf-8').write(json.dumps(mf_no, ensure_ascii=False) + u'\n')
            rcN1, tN1 = gs_check()
            net_missing = u'联网口径对不上' in tN1
            mf_cn = dict(rec)
            mf_cn[u'url'] = u'https://pic.example-cdn.com/shanghai-night-skyline.jpg'
            mf_cn[u'source_page'] = u'https://pic.example-cdn.com/detail/12345'
            io.open(mf, u'w', encoding=u'utf-8').write(json.dumps(mf_cn, ensure_ascii=False) + u'\n')
            rcN2, tN2 = gs_check()
            net_domestic_proxy = u'国内资源却挂了代理' in tN2
            io.open(mf, u'w', encoding=u'utf-8').write(json.dumps(rec, ensure_ascii=False) + u'\n')
            rcN3, tN3 = gs_check()
            net_quiet_after = u'联网口径对不上' not in tN3
            notes.append(u"G3 联网口径（国外缺代理 / 国内挂代理 / 修正后）：拦=%s/%s，恢复=%s -> %s" % (
                net_missing, net_domestic_proxy, net_quiet_after,
                u'ok' if (net_missing and net_domestic_proxy and net_quiet_after) else u'FAIL'))
            if not net_missing:
                fails.append(u"硬约束 7 没有闸：国外素材没记 proxy_used 也没被发现")
            if not net_domestic_proxy:
                fails.append(u"硬约束 7 方向反了没被发现：国内资源挂了代理仍然报绿")
            if not net_quiet_after:
                fails.append(u"联网口径误伤：正确的国外+7897 记录仍被判不合格")

            notes.append(u"G3 反证（换成同尺寸的另一张图）：诚实 manifest 安静=%s，"
                         u"偷换被拦=%s，补记 SHA1 后恢复=%s -> %s" % (
                             honest_quiet, swap_bites, swap_quiet_after,
                             u'ok' if (honest_quiet and swap_bites and swap_quiet_after) else u'FAIL'))
            if not swap_bites:
                fails.append(u"G3 不咬：素材文件被换成另一张，manifest 的 SHA1 却没重新核对")

            # (C) 内嵌媒体绑定：建完之后动源文件（视频）或动图，必须和成品里的字节对上
            gv = os.path.join(gs, u'gen', u'p001')
            os.makedirs(gv, exist_ok=True)
            vp = os.path.join(gv, u'video.mp4')
            clip_ok = False
            try:
                import imageio_ffmpeg
                ff2 = imageio_ffmpeg.get_ffmpeg_exe()
                fr = os.path.join(gs, u'_fr')
                shutil.rmtree(fr, ignore_errors=True)
                os.makedirs(fr)
                import random as _rnd
                rnd2 = _rnd.Random(11)
                for i in range(45):
                    px = bytes(rnd2.randrange(256) for _ in range(64 * 64 * 3))
                    im = Image.frombytes(u'RGB', (64, 64), px).resize((1920, 1080))
                    ImageDraw.Draw(im).rectangle([90 + i * 20, 320, 340 + i * 20, 700],
                                                 fill=(210, 190, 110))
                    im.save(os.path.join(fr, u'f%03d.png' % i))
                subprocess.run([ff2, u'-hide_banner', u'-loglevel', u'error', u'-y',
                                u'-framerate', u'8', u'-i', os.path.join(fr, u'f%03d.png'),
                                u'-pix_fmt', u'yuv420p', u'-crf', u'22', vp], capture_output=True)
                clip_ok = os.path.exists(vp) and os.path.getsize(vp) > 65536
                shutil.rmtree(fr, ignore_errors=True)
            except Exception:
                clip_ok = False
            if clip_ok:
                shg = json.load(io.open(gsc, encoding=u'utf-8'))
                for s_ in shg[u'pages']:
                    if s_[u'page'] == 1:
                        s_[u'asset_kind'] = u'video_bg'
                json.dump(shg, io.open(gsc, u'w', encoding=u'utf-8'), ensure_ascii=False)
                prep_delivery(gs)
                gs_run([u'build'])
                rcD, tD = gs_check()
                bind_quiet = u'内嵌 mp4' not in tD
                io.open(vp, u'ab').write(b'\x00' * 8192)
                rcE, tE = gs_check()
                bind_bites = (u'G3:' in tE) and (u'内嵌 mp4' in tE)
                # 干净复验的 F1：--allow-still 以前按关键字挑"可降级"的硬伤，
                # 结果把"内嵌 mp4 字节被换过"这类完整性硬伤一起吞了——降级口子变成假绿
                rep_gsb = os.path.join(gs, u'report', u'验收报告.md')
                if os.path.exists(rep_gsb):
                    os.remove(rep_gsb)
                r_gsb = subprocess.run([sys.executable, engine, u'check', u'--work', gs,
                                        u'--allow-still'],
                                       capture_output=True, text=True, encoding='utf8',
                                       errors='replace')
                t_gsb = (io.open(rep_gsb, encoding=u'utf-8').read()
                         if os.path.exists(rep_gsb) else u'')
                bind_survives_still = (r_gsb.returncode != 0) and (u'内嵌 mp4' in t_gsb)
                notes.append(u"G3 内嵌媒体绑定（成品 vs 源 mp4）：正常装配安静=%s，"
                             u"建完后动源文件被拦=%s，开 --allow-still 也照样拦=%s -> %s" % (
                                 bind_quiet, bind_bites, bind_survives_still,
                                 u'ok' if (bind_quiet and bind_bites and bind_survives_still) else u'FAIL'))
                if not bind_bites:
                    fails.append(u"内嵌媒体不咬：建完 deck 后替换 mp4 源文件，G3 仍报素材合格")
                if not bind_survives_still:
                    fails.append(u"--allow-still 吞掉了媒体完整性硬伤（F1 假绿）：口子只该放行"
                                 u"「这页没有视频」，不该放行「视频被换过」")
                os.remove(vp)
                for s_ in shg[u'pages']:
                    if s_[u'page'] == 1:
                        s_[u'asset_kind'] = u'image'
                json.dump(shg, io.open(gsc, u'w', encoding=u'utf-8'), ensure_ascii=False)

            # (D) 动图通道：多帧 GIF 正证、单帧反证、缺产物反证
            gd = os.path.join(gs, u'gen', u'p002')
            os.makedirs(gd, exist_ok=True)
            gif_p = os.path.join(gd, u'anim.gif')

            def write_gif(path, nframes, size=(800, 800), opaque=False):
                """透明底（正证）或不透明黑底（反证：叠在矢量色场上必然露方框）两种动图。"""
                import make_gif as _mg
                imgs = []
                for k in range(max(1, nframes)):
                    if opaque:
                        im = Image.new(u'RGB', size, (10, 12, 18))
                        ImageDraw.Draw(im).rectangle([40 + k * 25, 200, 300 + k * 25, 500],
                                                     fill=(220, 190, 110))
                    else:
                        im = Image.new(u'RGBA', size, (0, 0, 0, 0))
                        ImageDraw.Draw(im).rectangle([40 + k * 25, 200, 300 + k * 25, 500],
                                                     fill=(220, 190, 110, 255))
                    imgs.append(im)
                if opaque:
                    imgs[0].save(path, save_all=True, append_images=imgs[1:],
                                 duration=80, loop=0)
                else:
                    _mg.save_transparent(imgs, path, 80, [(220, 190, 110), (180, 150, 90),
                                                          (140, 110, 70)])



            import styles as _sty
            gp = os.path.join(pdir, u'p002_gif.txt')
            io.open(gp, u'w', encoding=u'utf-8').write(
                _sty.gif_prompt(u'金色粒子沿弧线流转', u'ink_wash',
                                [0.58, 0.16, 0.36, 0.5], u'附件1 为参考图', 4))
            shg2 = json.load(io.open(gsc, encoding=u'utf-8'))
            for s_ in shg2[u'pages']:
                if s_[u'page'] == 2:
                    s_[u'asset_kind'] = u'gif'
                    s_[u'tech_recipe'] = u'gif-motion-element'
            json.dump(shg2, io.open(gsc, u'w', encoding=u'utf-8'), ensure_ascii=False)
            write_gif(gif_p, 24)
            prep_delivery(gs)
            gs_run([u'build'])
            rcF, tF = gs_check()
            req1 = re.search(r'"gif_pages_required":\s*(\d+)', tF)
            bad1 = re.search(r'"gif_pages_bad":\s*\[\s*\]', tF)
            gif_ok_line = bool(req1 and req1.group(1) == u'1') and bool(bad1)

            # (D2) 把这份成品的 timing/morph 抠干净：运动页比例与"隔页必带动效"必须按成品说话
            strip_motion(os.path.join(gs, u'out', u'deck.pptx'))
            rcX, tX = gs_check()
            ratio_bites = u'页真的带动效' in tX
            div_bites = u'隔页在成品里既没有平滑' in tX
            notes.append(u"G6 反证（沙箱成品抠掉 timing+morph，只剩 1 页动图）："
                         u"≥30%% 运动页命中=%s，隔页必带动效命中=%s -> %s" % (
                             ratio_bites, div_bites,
                             u'ok' if (ratio_bites and div_bites) else u'FAIL'))
            if not (ratio_bites and div_bites):
                fails.append(u"G6 的两条 SPEC §2 硬指标不咬成品：删光动效后运动比例/隔页规则没同时报错")

            write_gif(gif_p, 1)

            rcG, tG = gs_check()
            gif_1frame = u'放出来就是静图' in tG
            write_gif(gif_p, 24, opaque=True)
            rcG2, tG2 = gs_check()
            gif_boxy = u'露出一块方框' in tG2
            write_gif(gif_p, 24)
            rcG3, tG3 = gs_check()
            gif_transparent_ok = u'露出一块方框' not in tG3
            os.remove(gif_p)
            rcH, tH = gs_check()
            gif_missing = u'没有 gif 产物' in tH
            notes.append(u"G11 动图通道（透明 24 帧正证 / 1 帧 / 不透明底叠色场 / 缺失）："
                         u"合格放行=%s，单帧被拦=%s，露方框被拦=%s，改透明后放行=%s，无产物被拦=%s -> %s" % (
                             gif_ok_line, gif_1frame, gif_boxy, gif_transparent_ok, gif_missing,
                             u'ok' if (gif_ok_line and gif_1frame and gif_boxy
                                       and gif_transparent_ok and gif_missing) else u'FAIL'))
            if not gif_ok_line:
                fails.append(u"动图正证不成立：多帧 GIF 没被认成合格动图页（%s）" % (
                    (re.search(u'G11:[^\n]*', tF) or [u''])[0][:120] if u'G11:' in tF else u'见报告'))
            if not gif_1frame:
                fails.append(u"动图反证不咬：单帧 GIF（静图）仍然被判合格")
            if not gif_boxy:
                fails.append(u"动图反证不咬：不透明底 GIF 叠在矢量色场上（会露一块方框）没被发现")
            if not gif_transparent_ok:
                fails.append(u"动图误伤：带透明索引的 GIF 仍被判露方框")
            if not gif_missing:
                fails.append(u"动图反证不咬：删掉 GIF 产物后 G11 没报「没有 gif 产物」")


            # (E) G12：验收截图必须逐张绑定，换一张就要判不合格
            rdd = os.path.join(gs, u'report', u'render')
            shutil.rmtree(rdd, ignore_errors=True)
            os.makedirs(rdd)
            deck_gs = os.path.join(gs, u'out', u'deck.pptx')
            from pptx import Presentation as _Px
            n_slides = len(list(_Px(deck_gs).slides)) if os.path.exists(deck_gs) else 0
            if n_slides:
                for i in range(1, n_slides + 1):
                    Image.effect_noise((1600, 900), 20).convert(u'RGB').save(
                        os.path.join(rdd, u'p%03d.png' % i))
                png_map = {f: C.sha1(os.path.join(rdd, f))
                           for f in sorted(os.listdir(rdd)) if f.endswith(u'.png')}
                C.jw(os.path.join(gs, u'report', u'render_stamp.json'),
                     {u'deck_sha1': C.sha1(deck_gs), u'pngs': len(png_map),
                      u'at': C.now(), u'png_sha1': png_map})
                rcI, tI = gs_check()
                fp_quiet = u'G12:' not in tI
                Image.effect_noise((1600, 900), 200).convert(u'RGB').save(
                    os.path.join(rdd, u'p003.png'))
                rcJ, tJ = gs_check()
                fp_bites = (u'G12:' in tJ) and (u'渲染截图与凭据对不上' in tJ)
                st = json.load(io.open(os.path.join(gs, u'report', u'render_stamp.json'),
                                       encoding=u'utf-8'))
                st.pop(u'png_sha1', None)
                C.jw(os.path.join(gs, u'report', u'render_stamp.json'), st)
                rcK, tK = gs_check()
                old_fmt = u'逐张 PNG 指纹' in tK
                notes.append(u"G12 截图指纹（逐张 SHA1）：一致时安静=%s，改一张被拦=%s，"
                             u"旧格式凭据被拦=%s -> %s" % (
                                 fp_quiet, fp_bites, old_fmt,
                                 u'ok' if (fp_quiet and fp_bites and old_fmt) else u'FAIL'))
                if not fp_bites:
                    fails.append(u"G12 不咬：替换一张渲染截图后仍然报视觉验收合格")
                if not old_fmt:
                    fails.append(u"G12 放过旧格式凭据（只有张数、没有逐张指纹）")
            else:
                fails.append(u"G12 截图指纹测试没跑：沙箱里读不到 deck 页数")

            # (F) 需求 4 的"附带上传的图片"必须落到具体文件：投放卡要列出绝对路径，
            #     而且改了素材类型后不能留着上一世的提示词叫人白跑
            shg4 = json.load(io.open(gsc, encoding=u'utf-8'))
            for s_ in shg4[u'pages']:
                if s_[u'page'] == 1:
                    s_[u'asset_kind'] = u'video_bg'
            json.dump(shg4, io.open(gsc, u'w', encoding=u'utf-8'), ensure_ascii=False)
            gi1 = os.path.join(gs, u'gen', u'p001')
            os.makedirs(gi1, exist_ok=True)
            Image.effect_noise((1920, 1080), 40).convert(u'RGB').save(
                os.path.join(gi1, u'image.png'))
            gs_run([u'scaffold'])
            prep_delivery(gs)
            gs_run([u'pack'])
            card_p = os.path.join(pdir, u'delivery_card.md')
            card_txt = io.open(card_p, encoding=u'utf-8').read() if os.path.exists(card_p) else u''
            att_named = (u'要上传的附件' in card_txt) and (u'image.png' in card_txt)

            # 把页 1 从 video_bg 改回 image，video 段必须被 scaffold 清掉（否则卡上会叫人选首尾帧）
            shg5 = json.load(io.open(gsc, encoding=u'utf-8'))
            for s_ in shg5[u'pages']:
                if s_[u'page'] == 1:
                    s_[u'asset_kind'] = u'image'
            json.dump(shg5, io.open(gsc, u'w', encoding=u'utf-8'), ensure_ascii=False)
            gs_run([u'scaffold'])
            stale_video = os.path.exists(os.path.join(pdir, u'p001_video.txt'))
            notes.append(u"投放卡与素材类型一致性：卡里点名要上传的附件=%s，改成图片页后 video 段已清掉=%s -> %s" % (
                att_named, not stale_video, u'ok' if (att_named and not stale_video) else u'FAIL'))
            if not att_named:
                fails.append(u"需求 4 没落地：投放卡没把要上传的参考图写成具体绝对路径")
            if stale_video:
                fails.append(u"scaffold 没清掉与页面素材类型不符的提示词（video_bg→image 后仍留 video 段）")

        except Exception as exc2:
            import traceback as _tb
            notes.append(u"全链沙箱反证异常 -> FAIL：%s" % str(exc2)[:120])
            fails.append(u"全链沙箱反证崩了：%s" % _tb.format_exc()[-300:])

    # 全绿但降级的那一支单独造：把 tbl 夹具改成"不需要任何素材 + 有 2 页平滑"，
    # 这样除了"没有视频页"以外不该有硬伤，才能验到 rc==0 时结论必须自报降级
    tbl_sh_p = os.path.join(tbl_dir, 'shots.json')
    tbl_sh = json.load(io.open(tbl_sh_p, encoding='utf-8'))
    for s_ in tbl_sh['pages']:
        s_['asset_kind'] = 'none'
        s_['need_material'] = False
        s_['morph_from_prev'] = s_['page'] in (2, 3)
        if s_['page'] == 2:
            s_['role'] = 'divider'
    json.dump(tbl_sh, io.open(tbl_sh_p, 'w', encoding='utf-8'), ensure_ascii=False)
    # 封面四件套 + 过渡看点（G29）：降级样本夹具也要合规封面，否则 G29 把"全绿降级"误判成硬伤
    tbl_ol_p = os.path.join(tbl_dir, 'outline.json')
    tbl_ol = json.load(io.open(tbl_ol_p, encoding='utf-8'))
    for pg_ in tbl_ol['pages']:
        if pg_.get('role') == 'cover':
            pg_['cover_slogan'] = u'把空着的产能开到楼下'
            pg_['cover_kicker'] = pg_.get('headline') or u'测试册'
            pg_['cover_subtitle'] = u'价值主张副标题'
            pg_['cover_chips'] = [u'市场', u'模式', u'财务']
            pg_['subtitle'] = u'编制：项目组'
        if pg_.get('role') == 'divider':
            pg_['divider_highlights'] = [u'章节看点短句']
    json.dump(tbl_ol, io.open(tbl_ol_p, 'w', encoding='utf-8'), ensure_ascii=False)
    prep_delivery(tbl_dir)
    subprocess.run([sys.executable, engine, 'build', '--work', tbl_dir],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rep_as2 = os.path.join(tbl_dir, u'report', u'验收报告.md')
    if os.path.exists(rep_as2):
        os.remove(rep_as2)
    r_as2 = subprocess.run([sys.executable, engine, 'check', '--work', tbl_dir,
                            '--allow-still', '--allow-field', '--render'],
                           capture_output=True, text=True, encoding='utf8', errors='replace')

    t_as2 = io.open(rep_as2, encoding='utf-8').read() if os.path.exists(rep_as2) else u''
    concl2 = t_as2.split(u'## 结论')[-1]
    green_degraded = (r_as2.returncode == 0) and (u'降级交付' in concl2) and \
                     (u'不算' in concl2) and (u'**全部闸通过。**' not in concl2)
    # 同一个目录不加 --allow-still 时必须仍然判不合格（口子只能由用户显式开）
    if os.path.exists(rep_as2):
        os.remove(rep_as2)
    r_as3 = subprocess.run([sys.executable, engine, 'check', '--work', tbl_dir],
                           capture_output=True, text=True, encoding='utf8', errors='replace')
    t_as3 = io.open(rep_as2, encoding='utf-8').read() if os.path.exists(rep_as2) else u''
    strict_still_fails = (r_as3.returncode != 0) and (u'真视频页' in t_as3)
    notes.append(u"--allow-still 全绿分支（无素材需求 + 2 页平滑，另开 --allow-field 让 G20 走声明）："
                 u"开后 rc=0 且结论自报降级=%s，不开时仍拦=%s -> %s" % (
                     green_degraded, strict_still_fails,
                     u'ok' if (green_degraded and strict_still_fails) else u'FAIL'))
    if not green_degraded:
        fails.append(u"全绿但降级的样本没在结论里自报降级（或 rc 不为 0，夹具没造对）：%s" % concl2[:120])
    if not strict_still_fails:
        fails.append(u"--allow-still 不是显式口子：不加它也没拦住「没有视频页」")


    # --- 技法卡 13/14（表格棋盘转场 / 立体展台）：卡必须真的装进成品，抠掉或压错行都要拦 ------
    # 这一节是"文档承诺与代码逐条对账"的机器版：14 张卡里有 2 张以前只活在文档里。
    deck_t = os.path.join(tbl_dir, 'out', 'deck.pptx')
    sh_t_p = os.path.join(tbl_dir, 'shots.json')
    sh_t = json.load(io.open(sh_t_p, encoding='utf-8'))
    tbl_pgs = [s_['page'] for s_ in sh_t['pages'] if s_.get('role') == 'table']
    # 展台（panel3d）只装在 statement 大数字页；优先选 statement，避免选到过渡页/数据页没有展台
    hero_pgs = [s_['page'] for s_ in sh_t['pages'] if s_.get('role') == 'statement'] or [
        s_['page'] for s_ in sh_t['pages'] if s_.get('role') != 'table' and s_['page'] > 1]
    tbl_pg = tbl_pgs[0] if tbl_pgs else None
    hero_pg = hero_pgs[0] if hero_pgs else None
    for s_ in sh_t['pages']:
        if s_['page'] == tbl_pg:
            s_['tech_recipe'] = u'table-motion'
            s_['morph_from_prev'] = False      # 一页只有一个 p:transition，别和 G14 抢
        elif s_['page'] == hero_pg:
            s_['tech_recipe'] = u'3d-model-depth'
    json.dump(sh_t, io.open(sh_t_p, 'w', encoding='utf-8'), ensure_ascii=False)

    def tbl_rebuild():
        r = subprocess.run([sys.executable, engine, 'build', '--work', tbl_dir],
                           capture_output=True, text=True, encoding='utf8', errors='replace')
        return r.returncode

    def patch_deck(deck_path, sub):
        import zipfile as _zz
        zin = _zz.ZipFile(deck_path)
        items = {n: zin.read(n) for n in zin.namelist()}
        zin.close()
        for n in list(items):
            if re.match(r'ppt/slides/slide\d+\.xml$', n):
                items[n] = sub(items[n].decode('utf-8', 'replace')).encode('utf-8')
        tmp = deck_path + '.tampered'
        with _zz.ZipFile(tmp, 'w', _zz.ZIP_DEFLATED) as zo:
            for k, v in items.items():
                zo.writestr(k, v)
        shutil.move(tmp, deck_path)

    rc_rb0 = tbl_rebuild()
    rc_rt, t_rt = run_check_at(tbl_dir)
    quiet_rt = u'技法卡只写在分镜里' not in t_rt
    tr_seen = bool(re.search(r'"table_transition_pages":\s*\[\s*\d', t_rt))
    sp_seen = bool(re.search(r'"pages_with_sp3d":\s*\[\s*\d', t_rt))
    collide_quiet = u'咬到了别的文字行' not in t_rt

    # (a) 成品里抠掉 p:checker：声明了 table-motion 却没装，必须拦
    patch_deck(deck_t, lambda t: re.sub(r'<p:checker\s*/>', '', t))
    rc_ck, t_ck = run_check_at(tbl_dir)
    checker_bites = (u'技法卡只写在分镜里' in t_ck) and (u'table-motion' in t_ck)

    # (b) 抠掉 a:sp3d：声明了 3d-model-depth 却没装，同样要拦
    tbl_rebuild()
    patch_deck(deck_t, lambda t: re.sub(r'<a:sp3d[^>]*>.*?</a:sp3d>|<a:sp3d[^/>]*/>',
                                        '', t, flags=re.S))
    rc_sp, t_sp = run_check_at(tbl_dir)
    sp3d_bites = (u'技法卡只写在分镜里' in t_sp) and (u'3d-model-depth' in t_sp)

    # (c) 把展台的边拉长到压住下一行正文：越界闸量不到（还在画布内），G8 的行碰撞必须量到。
    #     系数盲扫会漏：实测展台底 0.503、下一行 0.507–0.562，"切过该行"的系数窗口只有
    #     1.01–1.15，从 1.35 起扫第一档就把整行包进去（规则里合法）→ 永远不咬。
    #     改为按成品几何把展台底边精确切到下一行中线：部分重叠必然成立，与字号/留白无关。
    collide_bites = False
    tbl_rebuild()
    from pptx import Presentation as _Prs
    _pr = _Prs(deck_t)
    _target_cy = None
    for _sl in _pr.slides:
        _pn = next((s for s in _sl.shapes if s.name == 'panel3d'), None)
        if _pn is None:
            continue
        _pb = _pn.top + _pn.height
        _row = next((s for s in _sl.shapes if s is not _pn and s.has_text_frame
                     and (s.text_frame.text or '').strip() and s.top is not None
                     and s.top >= _pb - 1000), None)
        if _row is not None:
            _target_cy = int(_row.top + _row.height * 0.5 - _pn.top)
        break
    if _target_cy and _target_cy > 0:
        patch_deck(deck_t, lambda t: re.sub(
            r'(name="panel3d"[\s\S]{0,1500}?<a:ext cx="\d+" cy=")(\d+)',
            lambda m: m.group(1) + str(_target_cy), t))
        rc_cl, t_cl = run_check_at(tbl_dir)
        collide_bites = u'咬到了别的文字行' in t_cl
    if not collide_bites:
        for _cf in (1.05, 1.10, 1.15, 1.35, 1.6, 2.0, 2.4):
            tbl_rebuild()
            patch_deck(deck_t, lambda t, _f=_cf: re.sub(
                r'(name="panel3d"[\s\S]{0,1500}?<a:ext cx="\d+" cy=")(\d+)',
                lambda m: m.group(1) + str(int(int(m.group(2)) * _f)), t))
            rc_cl, t_cl = run_check_at(tbl_dir)
            if u'咬到了别的文字行' in t_cl:
                collide_bites = True
                break

    # (d) 把 fill 排到 ln 后面（就是这轮修掉的那个写法）：真 PowerPoint 会弹修复，G8 要拦
    tbl_rebuild()

    def _bad_order(t):
        i = t.find(u'name="panel3d"')
        if i < 0:
            return t
        j = t.find(u'</p:sp>', i)
        seg = t[i:j]
        m1 = re.search(r'<a:solidFill>[\s\S]*?</a:solidFill>', seg)
        m2 = re.search(r'<a:ln[^>]*>[\s\S]*?</a:ln>', seg)
        if not (m1 and m2) or m2.start() < m1.start():
            return t
        new = (seg[:m1.start()] + m2.group(0) + seg[m1.end():m2.start()]
               + m1.group(0) + seg[m2.end():])
        return t[:i] + new + t[j:]

    patch_deck(deck_t, _bad_order)
    rc_or, t_or = run_check_at(tbl_dir)
    order_bites = u'不合 CT_SpPr' in t_or
    tbl_rebuild()
    # (e) 表要广义：mask-dim 卡声明了却把 a:gradFill 抠掉，同样得拦（证明不是只给 13/14 写死）
    sh_md = json.load(io.open(sh_t_p, encoding='utf-8'))
    for s_ in sh_md['pages']:
        if s_['page'] not in (tbl_pg, hero_pg):
            s_['tech_recipe'] = u'mask-dim'   # 其余页真的只产蒙版，声明这张卡是诚实的
    json.dump(sh_md, io.open(sh_t_p, 'w', encoding='utf-8'), ensure_ascii=False)
    tbl_rebuild()
    rc_md0, t_md0 = run_check_at(tbl_dir)
    maskdim_quiet = u'技法卡只写在分镜里' not in t_md0
    patch_deck(deck_t, lambda t: re.sub(r'<a:gradFill[\s\S]*?</a:gradFill>', '', t))
    rc_mk, t_mk = run_check_at(tbl_dir)
    maskdim_bites = (u'技法卡只写在分镜里' in t_mk) and (u'mask-dim' in t_mk)
    # (f) 手工类卡不许误伤：bool-shape 是 PowerPoint 里点的，引擎不产，声明了也不该报
    sh_m = json.load(io.open(sh_t_p, encoding='utf-8'))
    for s_ in sh_m['pages']:
        s_['tech_recipe'] = u'bool-shape'
    json.dump(sh_m, io.open(sh_t_p, 'w', encoding='utf-8'), ensure_ascii=False)
    tbl_rebuild()
    rc_m2, t_m2 = run_check_at(tbl_dir)
    manual_no_false = u'技法卡只写在分镜里' not in t_m2
    # (f2) 主题色锁卡必须真验（2026-11-09 查实旧键 theme-palette-lock 不是任何 kb 卡 id，
    #      声明永远查不到=死代码）：palette-lock 声明了、theme1.xml 的 accent1 被抠掉 → G6 必须点名
    sh_pl = json.load(io.open(sh_t_p, encoding='utf-8'))
    for s_ in sh_pl['pages']:
        s_['tech_recipe'] = u'palette-lock'
    json.dump(sh_pl, io.open(sh_t_p, 'w', encoding='utf-8'), ensure_ascii=False)
    tbl_rebuild()
    rc_pl0, t_pl0 = run_check_at(tbl_dir)
    palette_quiet = u'技法卡只写在分镜里' not in t_pl0
    # patch_deck 只改 slide XML，accent1 在 theme1.xml 里——这里必须直接改主题部件
    import zipfile as _zzp
    _zin = _zzp.ZipFile(deck_t)
    _items = {n: _zin.read(n) for n in _zin.namelist()}
    _zin.close()
    for _n in list(_items):
        if _n == u'ppt/theme/theme1.xml':
            _items[_n] = re.sub(r'<a:accent1>[\s\S]*?</a:accent1>', '',
                                _items[_n].decode('utf-8', 'replace')).encode('utf-8')
    _tmp = deck_t + '.tampered'
    with _zzp.ZipFile(_tmp, 'w', _zzp.ZIP_DEFLATED) as _zo:
        for _k, _v in _items.items():
            _zo.writestr(_k, _v)
    shutil.move(_tmp, deck_t)
    rc_pl, t_pl = run_check_at(tbl_dir)
    palette_bites = (u'技法卡只写在分镜里' in t_pl) and (u'palette-lock' in t_pl)
    tbl_rebuild()
    notes.append(u"palette-lock token 反证：正常装配安静=%s，抠掉 theme accent1 被 G6 点名=%s -> %s" % (
        palette_quiet, palette_bites, 'ok' if (palette_quiet and palette_bites) else 'FAIL'))
    if not (palette_quiet and palette_bites):
        fails.append(u"palette-lock 死键/误伤：声明了主题色锁，抠掉 accent1 没被拦或正常装配被误报"
                     u"（安静=%s 被拦=%s）" % (palette_quiet, palette_bites))
    # (g) 卡 15/16 也必须真装：双色调锁色 a:duotone、放大强调 p:animScale
    gi1 = os.path.join(tbl_dir, u'gen', u'p001')
    os.makedirs(gi1, exist_ok=True)
    Image.effect_noise((1920, 1080), 40).convert(u'RGB').save(os.path.join(gi1, u'image.png'))
    sh_du = json.load(io.open(sh_t_p, encoding='utf-8'))
    for s_ in sh_du['pages']:
        if s_['page'] == 1:
            s_['asset_kind'] = u'image'
            s_['need_material'] = True
            s_['duotone'] = True
            s_['tech_recipe'] = u'blip-duotone'
        elif s_['page'] == 3:
            s_['tech_recipe'] = u'emphasis-scale'
    json.dump(sh_du, io.open(sh_t_p, 'w', encoding='utf-8'), ensure_ascii=False)
    tbl_rebuild()
    rc_du0, t_du0 = run_check_at(tbl_dir)
    du_quiet = (u'blip-duotone' not in t_du0 or u'技法卡只写在分镜里' not in t_du0)
    facts_du = C.deck_motion_facts(deck_t)
    du_emitted = bool(facts_du.get(1, {}).get('has_duotone'))
    scale_emitted = facts_du.get(3, {}).get('anim_scale', 0) > 0
    patch_deck(deck_t, lambda t: re.sub(r'<a:duotone[\s\S]*?</a:duotone>', '', t))
    rc_du1, t_du1 = run_check_at(tbl_dir)
    duo_bites = (u'技法卡只写在分镜里' in t_du1) and (u'blip-duotone' in t_du1)
    tbl_rebuild()
    patch_deck(deck_t, lambda t: re.sub(r'<p:animScale[\s\S]*?</p:animScale>', '', t))
    rc_du2, t_du2 = run_check_at(tbl_dir)
    scale_bites = (u'技法卡只写在分镜里' in t_du2) and (u'emphasis-scale' in t_du2)
    tbl_rebuild()
    # (h) 孤字尾行：giant-numeral 卡的 gate 一直写"G8 孤字"而 G8 从没实现过这条。
    #     正证=正常成品零孤字；反证=把标题框压窄，让同一句话折出 1 个字的尾行，必须被拦。
    rc_orq, t_orq = run_check_at(tbl_dir)
    orphan_quiet = u'孤字尾行' not in t_orq

    # 反证不能靠猜宽度：用闸自己的换行模型二分出一个"刚好把末行挤成 1 个字"的框宽，
    # 这样这条反证测的是闸，而不是我手气。
    from pptx.util import Emu as _Emu
    from pptx import Presentation as _Px      # 1558 原来吃 1303 那次条件导入留下的变量
    prs_o = _Px(deck_t)
    narrowed = False
    for si in range(len(list(prs_o.slides))):
        ttl = [sp for sp in prs_o.slides[si].shapes if sp.name == 'title' and sp.has_text_frame]
        if not ttl:
            continue
        sp0 = ttl[0]
        para = sp0.text_frame.paragraphs[0]
        txt = ''.join(r.text for r in para.runs).strip()
        pt = max([r.font.size.pt for r in para.runs if r.font.size] or [34.0])
        lo, hi = 0.10, sp0.width / float(C.EMU_W)
        hit = None
        for _ in range(28):
            mid = (lo + hi) / 2.0
            nln, tl = C.orphan_tail(txt, pt, mid)
            if nln >= 2 and 0 < tl <= 2:
                hit = mid
                break
            if nln < 2:
                hi = mid
            else:
                lo = mid
        if hit:
            sp0.width = _Emu(int(hit * C.EMU_W))
            narrowed = True
            break
    if narrowed:
        prs_o.save(deck_t)
    rc_orp, t_orp = run_check_at(tbl_dir)
    orphan_bites = narrowed and (u'孤字尾行' in t_orp)
    tbl_rebuild()
    notes.append(u"G8 孤字尾行（正常成品安静=%s，标题框压窄后拦=%s）-> %s" % (
        orphan_quiet, orphan_bites, u'ok' if (orphan_quiet and orphan_bites) else u'FAIL'))
    if not orphan_bites:
        fails.append(u"G8 的孤字检查不咬：标题折行只剩 1 个字仍然报绿（卡里承诺过 G8 孤字）")
    if not orphan_quiet:
        fails.append(u"G8 孤字误伤：正常装配的成品被判有孤字尾行")
    # 大数字那串的断行点必须在源头就没掉：val 常常本身就是 '46.9 万元'（带普通空格），
    # 只给 unit 前加 nbsp 不够（v5 成品实测 51 处 110pt 孤字"元"就是这么来的）
    import data_layout as _DL
    hd1 = _DL.hero_display(u'46.9 万元', u'')
    hd2 = _DL.hero_display(u'46.9', u'万元')
    hd_ok = (u' ' not in hd1 and u' ' not in hd2 and hd1 == hd2 == u'46.9\u00a0万元')
    notes.append(u"hero_display：val 自带空格=%r，val+unit=%r，两写法一致且无普通空格=%s -> %s" % (
        hd1, hd2, hd1 == hd2, u'ok' if hd_ok else u'FAIL'))
    if not hd_ok:
        fails.append(u"hero_display 没把整串的普通空格换成不间断空格："
                     u"110pt 大数字的单位会单独折行成孤字（G8 实测 51 处）")
    notes.append(u"卡 15/16 装进成品（正证：a:duotone 已写=%s，animScale 已写=%s，声明时安静=%s）"
                 u"｜反证：抠掉 duotone 被拦=%s，抠掉 animScale 被拦=%s -> %s" % (
                     du_emitted, scale_emitted, du_quiet, duo_bites, scale_bites,
                     u'ok' if (du_emitted and scale_emitted and du_quiet
                               and duo_bites and scale_bites) else u'FAIL'))
    if not du_emitted:
        fails.append(u"卡 15 没装进成品：声明 blip-duotone 的页里没有 a:duotone")
    if not scale_emitted:
        fails.append(u"卡 16 没装进成品：声明 emphasis-scale 的页里没有 p:animScale")
    if not duo_bites:
        fails.append(u"G6 不咬：抠掉 a:duotone 后，声明 blip-duotone 的页仍报绿")
    if not scale_bites:
        fails.append(u"G6 不咬：抠掉 p:animScale 后，声明 emphasis-scale 的页仍报绿")
    notes.append(u"技法卡 token 表的广义与边界（声明 mask-dim 时安静=%s，抠掉 gradFill 被拦=%s，"
                 u"手工卡 bool-shape 不误伤=%s）-> %s" % (
                     maskdim_quiet, maskdim_bites, manual_no_false,
                     u'ok' if (maskdim_quiet and maskdim_bites and manual_no_false) else u'FAIL'))
    if not maskdim_quiet:
        fails.append(u"RECIPE_TOKENS 误伤：正常装配的 gradFill 蒙版没被认成 mask-dim 的产出")
    if not maskdim_bites:
        fails.append(u"RECIPE_TOKENS 只对 13/14 生效：mask-dim 声明了、成品里 gradFill 全抠掉却没报错")

    # (5d) 新增四道闸 G16/G17/G18/G19 必须真咬得住：正证（成品合格时安静）+ 反证（破坏后必拦）
    import corpus_tables as CT_
    deck_now = deck_t
    g16_ok = C.g16_palette_lock(deck_now, u'tech_blue')
    g16_quiet = (not g16_ok['offending_pages']) and \
                g16_ok['theme_accent1'] == g16_ok['palette_accent1']
    # 反证 1：往成品里塞一个色板外的饱和色
    patch_deck(deck_now, lambda t: t.replace(
        u'<a:solidFill>', u'<a:solidFill><a:srgbClr val="FF00FF"/>', 1) if u'<a:solidFill>' in t else t)
    rc_c16, t_c16 = run_check_at(tbl_dir)
    c16_bites = (u'G16' in t_c16) or (u'色板' in t_c16)
    g16_after = C.g16_palette_lock(deck_now, u'tech_blue')
    c16_detects = bool(g16_after['offending_pages'])
    tbl_rebuild()
    # 反证 2：把 theme 的 accent1 改回 Office 默认以外的值
    def _bad_theme(t):
        return t
    import zipfile as _zz
    _zin = _zz.ZipFile(deck_now)
    _items = {n: _zin.read(n) for n in _zin.namelist()}
    _zin.close()
    _thn = sorted(n for n in _items if re.match(r'ppt/theme/theme\d+\.xml$', n))[0]
    _th = _items[_thn].decode('utf-8', 'replace')
    _items[_thn] = re.sub(r'(<a:accent1><a:srgbClr val=")[0-9A-Fa-f]{6}(")', r'\g<1>FF0000\g<2>',
                          _th).encode('utf-8')
    _tmp = deck_now + '.th'
    with _zz.ZipFile(_tmp, 'w', _zz.ZIP_DEFLATED) as _zo:
        for _k, _v in _items.items():
            _zo.writestr(_k, _v)
    shutil.move(_tmp, deck_now)
    g16_theme = C.g16_palette_lock(deck_now, u'tech_blue')
    c16_theme_bites = g16_theme['theme_accent1'] != g16_theme['palette_accent1']
    rc_c16t, t_c16t = run_check_at(tbl_dir)
    c16_theme_msg = (u'G16' in t_c16t)
    tbl_rebuild()

    # G17：正证——build_log 声明什么就量什么；反证——把分栏声明套在满幅页上必须报不一致
    blog17 = (json.load(io.open(os.path.join(tbl_dir, 'out', 'build_log.json'),
                                          encoding='utf-8')) or [])
    g17_quiet = not C.g17_layout_lock(deck_now, blog17)['mismatch']
    lied = [dict(r, layout=u'split-centre-image') for r in blog17]
    g17_bites = bool(C.g17_layout_lock(deck_now, lied)['mismatch']) or not blog17
    # 反证 2：把某页图片 xfrm 改成满幅，声明分栏就该被量出来
    if any((r.get('layout') == 'split-centre-image') for r in blog17):
        _sp = [r['page'] for r in blog17 if r.get('layout') == 'split-centre-image'][0]
        patch_deck(deck_now, lambda t: re.sub(
            r'(<p:pic>[\s\S]{0,400}?<a:ext cx=")\d+(" cy=")(\d+)(")',
            lambda m: m.group(1) + u'12192000' + m.group(2) + u'6858000' + m.group(4), t, 1))
        g17_geom = C.g17_layout_lock(deck_now, blog17)
        g17_geom_bites = bool(g17_geom['mismatch'])
        tbl_rebuild()
    else:
        g17_geom_bites = True

    # G18：先让这册真声明一个擦入 preset（否则"抠掉 wipe(left)"是空转，测不出闸咬不咬）
    _shp17 = os.path.join(tbl_dir, 'shots.json')
    _sh17 = json.load(io.open(_shp17, encoding='utf-8'))
    _sh17['pages'][0]['motion_preset'] = u'wipe-left'
    json.dump(_sh17, io.open(_shp17, 'w', encoding='utf-8'), ensure_ascii=False)
    tbl_rebuild()
    blog17 = (json.load(io.open(os.path.join(tbl_dir, 'out', 'build_log.json'),
                                encoding='utf-8')) or [])
    wipe_declared = any((x.get('triple') == 'entr:22:8') for r in blog17
                        for x in (r.get('anim_presets') or []))
    g18_quiet = not C.g18_anim_lock(deck_now, blog17)['missing']
    patch_deck(deck_now, lambda t: re.sub(r'filter="wipe\(left\)"', 'filter="blinds(horizontal)"', t))
    g18_bites = bool(C.g18_anim_lock(deck_now, blog17)['missing'])
    tbl_rebuild()
    # 视频页的点击暂停链：抠掉 togglePause 必须报缺
    vid_pages = [r['page'] for r in blog17 if r.get('video')]
    if vid_pages:
        patch_deck(deck_now, lambda t: t.replace(u'cmd="togglePause"', u'cmd="stop"'))
        g18v = C.g18_anim_lock(deck_now, blog17)
        g18v_bites = any(u'点击暂停链' in str(m.get('why', '')) for m in g18v['missing'])
        tbl_rebuild()
    else:
        g18v_bites = True

    # P17 教程零闸补账：循环播放（repeatCount）与标题"与上一动画同时"（withEffect）。
    # 正证已由上面的 g18_quiet 覆盖（完整成品不缺）；这里做注入/撤销反证。
    g18rep_bites = g18we_bites = True
    if vid_pages:
        patch_deck(deck_now, lambda t: t.replace(u'repeatCount="indefinite"', u'repeatCount="1"'))
        g18rep_bites = any(u'repeatCount' in str(m.get('why', ''))
                           for m in C.g18_anim_lock(deck_now, blog17)['missing'])
        tbl_rebuild()
        # withEffect 判据只咬封面/过渡/结尾视频页：用 cover 角色的 build_log 行对准真视频页，
        # 再把成品里的 withEffect 全改成 afterEffect，必须报"标题排在视频之后"。
        _bl_we = [dict(r, role='cover', anim_shapes=2) if r.get('page') == vid_pages[0] else r
                  for r in blog17]
        patch_deck(deck_now, lambda t: t.replace(u'nodeType="withEffect"', u'nodeType="afterEffect"'))
        g18we_bites = any(u'withEffect' in str(m.get('why', ''))
                          for m in C.g18_anim_lock(deck_now, _bl_we)['missing'])
        tbl_rebuild()

    # G19：正证——自己的成品 0 命中；反证 1——往页里塞广告词必拦；反证 2——语料真广告页必须被判坏
    g19_quiet = not C.g19_bad_exemplar(deck_now, C.deck_motion_facts(deck_now))['n_hits']
    patch_deck(deck_now, lambda t: t.replace(u'</p:sp>', u'</p:sp>', 1))
    patch_deck(deck_now, lambda t: re.sub(r'<a:t>([^<]{0,20})</a:t>',
                                          u'<a:t>更多精品模板下载地址 提取码 G3x9</a:t>', t, count=1))
    g19_after = C.g19_bad_exemplar(deck_now, C.deck_motion_facts(deck_now))
    g19_bites = bool(g19_after['n_hits'])
    rc_c19, t_c19 = run_check_at(tbl_dir)
    g19_msg = (u'G19' in t_c19)
    tbl_rebuild()
    import corpus_path as CP2
    src_corpus = CP2.corpus_dir()
    ad_hits = None
    for _fn in sorted(os.listdir(src_corpus)) if src_corpus and os.path.isdir(src_corpus) else []:
        if not _fn.lower().endswith(u'.pptx'):
            continue
        _p = os.path.join(src_corpus, _fn)
        try:
            _r = C.g19_bad_exemplar(_p, C.deck_motion_facts(_p))
        except Exception:
            continue
        if _r['n_hits'] >= 3:
            ad_hits = (_fn[:30], _r['n_hits'], _r['hits'][0]['why'])
            break
    notes.append(u"G16 配色（正证：theme accent1=%s 且无越界色=%s｜反证：塞 FF00FF 被检出=%s 被拦=%s、"
                 u"改 theme 主色被检出=%s 报告点名=%s）-> %s" % (
                     g16_ok['theme_accent1'], g16_quiet, c16_detects, c16_bites,
                     c16_theme_bites, c16_theme_msg,
                     u'ok' if (g16_quiet and c16_detects and c16_bites and c16_theme_bites
                               and c16_theme_msg) else u'FAIL'))
    notes.append(u"G17 图文版式（正证：声明与量得一致=%s｜反证：谎报分栏被拦=%s、"
                 u"把分栏图改满幅被量出来=%s）-> %s" % (
                     g17_quiet, g17_bites, g17_geom_bites,
                     u'ok' if (g17_quiet and g17_bites and g17_geom_bites) else u'FAIL'))
    notes.append(u"G18 动效链（夹具：这册真声明 entr:22:8=%s；正证：三元组与 filter 都在=%s｜"
                 u"反证：改 filter 被拦=%s、抠掉 togglePause 被拦=%s、删 repeatCount 被拦=%s、"
                 u"withEffect 改 afterEffect 被拦=%s）-> %s" % (
                     wipe_declared, g18_quiet, g18_bites, g18v_bites, g18rep_bites, g18we_bites,
                     u'ok' if (wipe_declared and g18_quiet and g18_bites and g18v_bites
                               and g18rep_bites and g18we_bites) else u'FAIL'))
    notes.append(u"G19 示范质量（正证：自家成品 0 命中=%s｜反证：塞广告词被拦=%s 报告点名=%s、"
                 u"语料真广告页被判坏=%s）-> %s" % (
                     g19_quiet, g19_bites, g19_msg, json.dumps(ad_hits, ensure_ascii=False),
                     u'ok' if (g19_quiet and g19_bites and g19_msg and ad_hits) else u'FAIL'))
    if not g16_quiet:
        fails.append(u"G16 误伤：正常装配的成品被判出色板外的色或主题主色不符")
    if not (c16_detects and c16_bites):
        fails.append(u"G16 不咬：成品里塞了 FF00FF 这种色板外的饱和色却没检出/没拦")
    if not c16_theme_bites:
        fails.append(u"G16 不咬：theme accent1 被改掉却没发现")
    if not g17_bites:
        fails.append(u"G17 不咬：build_log 谎报分栏原型却没量出来")
    if not g17_geom_bites:
        fails.append(u"G17 不咬：把分栏页的图改成满幅后仍报一致")
    if not wipe_declared:
        fails.append(u"夹具没建起来：motion_preset=wipe-left 却没在 build_log 里数到 entr:22:8")
    if not g18_bites:
        fails.append(u"G18 不咬：成品 filter 被改成 blinds 后声明的 wipe(left) 仍算通过")
    if not g18v_bites:
        fails.append(u"G18 不咬：视频页的 togglePause 链被抠掉却没报缺")
    if not g18rep_bites:
        fails.append(u"G18 不咬：视频页 repeatCount=indefinite 被改成 1（播一遍就停）却没报缺")
    if not g18we_bites:
        fails.append(u"G18 不咬：封面视频页标题 withEffect 被改成 afterEffect（排在视频后）却没报缺")
    if not g19_quiet:
        fails.append(u"G19 误伤：自家成品被判命中坏示范")
    if not (g19_bites and g19_msg):
        fails.append(u"G19 不咬：往成品里塞广告词却没拦下/报告没点名")
    if not ad_hits:
        fails.append(u"G19 判据失效：连语料里真实的广告推广册都判不出坏页，说明判据没接上 BAD_RULES")

    # (5e) 多图栅格原型：引擎真能排出来，G17 用同一个分类器复核；谎报原型必须被量出来
    tile_page = next((s_['page'] for s_ in
                      json.load(io.open(os.path.join(tbl_dir, 'shots.json'), encoding='utf-8'))
                      ['pages'] if s_.get('role') in ('data', 'process', 'compare')), 3)
    _shp_t = os.path.join(tbl_dir, 'shots.json')
    _sh_t2 = json.load(io.open(_shp_t, encoding='utf-8'))
    gd = os.path.join(tbl_dir, 'gen', 'p%03d' % tile_page)
    os.makedirs(gd, exist_ok=True)
    from PIL import Image as _Im

    def _mk_tiles(k):
        for old in glob.glob(os.path.join(gd, 'image_*.jpg')):
            os.remove(old)
        for i in range(1, k + 1):
            im = _Im.new('RGB', (2000, 1400),
                         (20 + i * 25, 40 + i * 15, 90 + i * 10))
            im.save(os.path.join(gd, 'image_%d.jpg' % i), quality=90)
    _mk_tiles(6)
    for _s in _sh_t2['pages']:
        if _s['page'] == tile_page:
            _s['layout'] = u'photo-wall'
            _s['assets'] = 6
            _s['asset_kind'] = 'image'
            _s['need_material'] = True
    json.dump(_sh_t2, io.open(_shp_t, 'w', encoding='utf-8'), ensure_ascii=False)
    rc_t1 = tbl_rebuild()
    blog_t = json.load(io.open(os.path.join(tbl_dir, 'out', 'build_log.json'), encoding='utf-8'))
    row_t = next((r for r in blog_t if r.get('page') == tile_page), {})
    tiles_placed = row_t.get('tiles') or 0
    meas_t = DM.measure_deck(deck_now)
    meas_page = next((p for p in meas_t if p['page'] == tile_page), {})
    tile_archetype = meas_page.get('archetype')
    g17_tile_quiet = not [m for m in C.g17_layout_lock(deck_now, blog_t)['mismatch']
                          if m.get('page') == tile_page]
    # 反证：把 build_log 里的原型改成 card-grid（成品几何没变），G17 必须量出来说谎
    lied_t = [dict(r, layout=(u'card-grid' if r.get('page') == tile_page else r.get('layout')))
              for r in blog_t]
    g17_tile_bites = bool([m for m in C.g17_layout_lock(deck_now, lied_t)['mismatch']
                           if m.get('page') == tile_page])
    # 反证 2：只剩 3 张图时照片墙装不满，引擎必须自己降级，不许谎称 photo-wall
    _mk_tiles(3)
    rc_t2 = tbl_rebuild()
    blog_t2 = json.load(io.open(os.path.join(tbl_dir, 'out', 'build_log.json'), encoding='utf-8'))
    row_t2 = next((r for r in blog_t2 if r.get('page') == tile_page), {})
    demoted = row_t2.get('layout') != u'photo-wall'
    g17_demote_quiet = not [m for m in C.g17_layout_lock(deck_now, blog_t2)['mismatch']
                             if m.get('page') == tile_page]
    _mk_tiles(6)
    notes.append(u"多图栅格（正证：photo-wall 装下 %d 块、量得原型=%s、G17 安静=%s、build rc=%s｜"
                 u"反证：谎报 card-grid 被量出=%s｜图不够时自动降级=%s 且降级后 G17 安静=%s）-> %s" % (
                     tiles_placed, tile_archetype, g17_tile_quiet, rc_t1, g17_tile_bites,
                     demoted, g17_demote_quiet,
                     u'ok' if (tiles_placed == 6 and tile_archetype == 'photo-wall'
                               and g17_tile_quiet and rc_t1 == 0 and g17_tile_bites
                               and demoted and g17_demote_quiet) else u'FAIL'))
    if tiles_placed != 6 or tile_archetype != 'photo-wall':
        fails.append(u"多图栅格没真装进成品：装了 %s 块，成品量成 %s（应为 photo-wall）" % (
            tiles_placed, tile_archetype))
    if not g17_tile_bites:
        fails.append(u"G17 不咬：把 photo-wall 成品谎报成 card-grid 却没量出来")
    if not demoted:
        fails.append(u"图不够时没降级：只有 3 张图仍声明 photo-wall")
    if not g17_tile_quiet or not g17_demote_quiet:
        fails.append(u"G17 误伤：栅格页正常装配或降级后仍被判原型不符")
    if not manual_no_false:
        fails.append(u"手工类技法卡被误伤：bool-shape（引擎不产）声明后 G6 报了缺失")
    notes.append(u"技法卡 13/14 装进成品（正证：棋盘转场=%s，立体 sp3d=%s，展台不压行=%s，build rc=%d）"
                 u"｜反证：抠掉 checker 被拦=%s，抠掉 sp3d 被拦=%s，展台压行被拦=%s，"
                 u"spPr 顺序错被拦=%s -> %s" % (
                     tr_seen, sp_seen, collide_quiet, rc_rb0,
                     checker_bites, sp3d_bites, collide_bites, order_bites,
                     u'ok' if (rc_rb0 == 0 and tr_seen and sp_seen and collide_quiet
                               and checker_bites and sp3d_bites and collide_bites
                               and order_bites) else u'FAIL'))
    if not (tr_seen and sp_seen):
        fails.append(u"技法卡 13/14 没真的装进成品：checker 转场=%s / sp3d 立体=%s" % (tr_seen, sp_seen))
    if not checker_bites:
        fails.append(u"G6 不咬：成品里抠掉 p:checker 后，声明 table-motion 的页仍报绿")
    if not sp3d_bites:
        fails.append(u"G6 不咬：成品里抠掉 a:sp3d 后，声明 3d-model-depth 的页仍报绿")
    if not collide_bites:
        fails.append(u"G8 不咬：立体展台拉长压住下一行正文（仍在画布内）没被发现")
    if not order_bites:
        fails.append(u"G8 不咬：spPr 里 fill 排在 ln 后面（PowerPoint 会提示修复）没被发现")
    if not collide_quiet:
        fails.append(u"G8 误伤：正常展台包住了锚点文字，却被判压行")

    # (5b) 技法库完整性：卡里必须有实测 presetID，否则"学完所有设计技巧"是空话


    good_lib = C.tech_library_invariants(C.kb('deck_tech.json'))
    import copy as _cp
    broken_lib = _cp.deepcopy(C.kb('deck_tech.json'))
    broken_lib['cards'] = [c for c in broken_lib['cards'] if c['id'] != 'text-animation-presets']
    broken_lib['stats']['preset_ids'] = {}
    broken_lib['stats']['text_preset_ids'] = {}
    bad_lib = C.tech_library_invariants(broken_lib)
    lib_ok = (not good_lib) and len(bad_lib) >= 3
    # 手法矩阵与提示词页覆盖这两条新不变量必须真的咬得住：把 token 名字从卡里抹掉、
    # 或把"已入库页数"改小，都必须立刻报违规（否则"无遗漏"又变成一句空话）
    broken_matrix = _cp.deepcopy(C.kb('deck_tech.json'))
    for c in broken_matrix['cards']:
        if c['id'] == 'blip-duotone':
            c['ooxml'] = u'（这里不放任何 token 名）'
            c['evidence'] = u'（这里也不放任何 token 名）'
            c['name'] = u'双色调锁色'
            c['params'] = u''
            c['engine'] = u''
            c['steps'] = u''
            c['when'] = u''
            c['gate'] = u''
    bad_matrix = [x for x in C.tech_library_invariants(broken_matrix) if 'token' in x]
    cov_ok = C.kb('coverage.json')
    bad_cov_clean = C.coverage_invariants(cov_ok)
    cov_bad = _cp.deepcopy(cov_ok)
    cov_bad['prompt_like_slides_covered'] = max(0, cov_bad.get('prompt_like_slides', 0) - 3)
    bad_cov = C.coverage_invariants(cov_bad)
    cov_bad2 = _cp.deepcopy(cov_ok)
    cov_bad2['prompt_like_slides_missing'] = [['某模板.pptx', 7, 900]]
    cov_bad2['prompt_like_slides_covered'] = cov_bad2['prompt_like_slides'] - 1
    bad_cov2 = C.coverage_invariants(cov_bad2)
    matrix_ok = bool(bad_matrix) and not bad_cov_clean and bool(bad_cov) and bool(bad_cov2)
    notes.append(u"无遗漏不变量咬得住（抹掉卡里的 token 名=%d 条违规；真 coverage 违规=%d；"
                 u"漏 3 页提示词=%d 条；缺键=%d 条）-> %s" % (
                     len(bad_matrix), len(bad_cov_clean), len(bad_cov), len(bad_cov2),
                     u'ok' if matrix_ok else u'FAIL'))
    if not bad_matrix:
        fails.append(u"手法矩阵不变量不咬：把 duotone/satMod/tint 从卡里抹掉后仍没报"
                     u"\"实测到却没卡提到\"")
    if bad_cov_clean:
        fails.append(u"真 coverage.json 过不了自己的不变量（误伤）：%s" % bad_cov_clean[:2])
    if not bad_cov:
        fails.append(u"提示词页覆盖不变量不咬：已入库页数凭空少 3 页却没报错")
    if not bad_cov2:
        fails.append(u"提示词页覆盖不变量不咬：漏 1 页却没报错")
    notes.append("技法库不变量（共用同一份判定）：真库违规=%d，把 presetID 卡抽掉后违规=%d 条 -> %s" % (
        len(good_lib), len(bad_lib), 'ok' if lib_ok else 'FAIL'))
    if not lib_ok:
        fails.append("技法库不变量不咬或误伤：真库 %s / 坏库 %d 条" % (good_lib, len(bad_lib)))
    # 语料里真学到过具体动效：entr/exit 的 presetID 必须有次数与页数
    tpi = C.kb('deck_tech.json')['stats'].get('text_preset_ids') or {}
    pages = C.kb('deck_tech.json')['stats'].get('pages_with_animation') or 0
    ids_ok = len(tpi) >= 5 and pages >= 500 and all(v > 0 for v in tpi.values())
    notes.append("presetID 实测数据：%d 个文字动效 ID，语料 %d 页带动画，最高频 %s -> %s" % (
        len(tpi), pages, list(tpi.items())[:3], 'ok' if ids_ok else 'FAIL'))
    if not ids_ok:
        fails.append("presetID 数据不足，agent 无法复现逐行浮现/打字机这类文字动效")

    # --- the 30-case title classifier lives in test_safety.py; run it as a suite member ------
    sf = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                      'test_safety.py')],
                        capture_output=True, text=True, encoding='utf8', errors='replace')
    sf_line = (sf.stdout or '').strip().splitlines()
    notes.append("标题分类表（test_safety.py）：退出码 %d，%s" % (
        sf.returncode, sf_line[-1] if sf_line else (sf.stderr or '')[-100:]))
    if sf.returncode != 0:
        fails.append("test_safety.py 不通过：敏感/放行用例有错判（见 caofeng_safety_test.md）")

    # --- G20 素材覆盖率：这是被 v17 那份"19 道闸全绿但 50 页同一套色场"的片子逼出来的 ----
    # 反证 A：整册改成色场（就是 WorkBuddy 那一轮的做法）→ 必须拦
    g20_bad = os.path.join(TMP, 'g20_bad')
    shutil.rmtree(g20_bad, ignore_errors=True)
    shutil.copytree(cli, g20_bad, ignore=shutil.ignore_patterns('__pycache__', 'report'))
    sp_b = os.path.join(g20_bad, 'shots.json')
    sh_b = json.load(io.open(sp_b, encoding='utf-8'))
    for s in sh_b['pages']:
        s['asset_kind'] = 'field'
        s['need_material'] = False
    json.dump(sh_b, io.open(sp_b, 'w', encoding='utf-8'), ensure_ascii=False)
    # 真素材可能留在三个地方：materials/（fetch 抓的）、gen/（投放回来的）、out/graded/（装配缓存）。
    # 只删 materials 的话覆盖率降不到 0，反证就成了假通过——上一版就是这样漏的。
    for sub in ('materials', os.path.join('gen'), os.path.join('out', 'graded'),
                os.path.join('out', 'bg_fallback')):
        d = os.path.join(g20_bad, sub)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    subprocess.run([sys.executable, engine, 'build', '--work', g20_bad],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    # 连排判据按"同母题"计：反证要把母题强制成同一套，证明"整册同一张脸"照样被拦；
    # 母题轮换的正证由真册（ varied motifs ）承担。
    _blp = os.path.join(g20_bad, 'out', 'build_log.json')
    if os.path.exists(_blp):
        _bl = json.load(io.open(_blp, encoding='utf-8'))
        for _r in _bl:
            _r['field_motif'] = 0
        json.dump(_bl, io.open(_blp, 'w', encoding='utf-8'), ensure_ascii=False)
    rc_b, t_b = run_check_at(g20_bad)
    g20_bites = u'G20:' in t_b
    notes.append("G20 反证（整册矢量色场）：报告出现 G20=%s 退出码=%d -> %s" % (
        g20_bites, rc_b, 'ok' if g20_bites else 'FAIL'))
    if not g20_bites:
        fails.append("G20 不咬：全册没有一页真素材也能过（v17 那份 58 页 50 页色场就是靠这个漏洞全绿的）")

    # 正证：过半页面给 1920x1080 的真素材 → 必须放行
    g20_good = os.path.join(TMP, 'g20_good')
    shutil.rmtree(g20_good, ignore_errors=True)
    shutil.copytree(cli, g20_good, ignore=shutil.ignore_patterns('__pycache__', 'report'))
    # 上一支测试留在 cli/gen 里的回收视频会让 1、2 页白捡素材：覆盖率被顶到线以上，
    # 「末尾追加的来源页把分母稀释」这个洞就测不出来了。这份夹具只认自己铺的图。
    shutil.rmtree(os.path.join(g20_good, 'gen'), ignore_errors=True)
    sp_g = os.path.join(g20_good, 'shots.json')
    sh_g = json.load(io.open(sp_g, encoding='utf-8'))
    n_ok = 0
    for _gi, s in enumerate(sh_g['pages']):
        if s['role'] == 'table':
            continue
        n = s['page']
        if _gi % 2:                              # 隔页铺：连排色场≤2 是 G20 的另一条子闸，别在这里误伤
            continue
        if n_ok * 2 < len(sh_g['pages']):        # 铺到过半
            fp = os.path.join(g20_good, 'materials', 'p%03d.jpg' % n)
            im = Image.new('RGB', (1920, 1080))
            # 真照片似的多色内容：让"平涂渐变"和"素材"在指标上分得开
            for yy in range(0, 1080, 8):
                for xx in range(0, 1920, 8):
                    im.putpixel((xx, yy), ((xx * 7 + yy * 13) % 256,
                                           (xx * 3 + yy * 5) % 256, (xx + yy * 11) % 256))
            im.save(fp, quality=88)
            # fetch 出来的素材一定有 manifest 记录：装配只认 manifest 里那条路径，
            # 光把 jpg 扔进 materials/ 是"裸文件"，G3 与装配都不认（这里补记录才等于真取过图）
            man_g = os.path.join(g20_good, 'materials', 'manifest.jsonl')
            io.open(man_g, 'a', encoding='utf-8').write(json.dumps({
                'page': n, 'term': u'office building facade', 'file': os.path.abspath(fp),
                'url': 'https://commons.wikimedia.org/wiki/File:Office_building_%d.jpg' % n,
                'source_page': 'File:Office building %d.jpg' % n,
                'w': 1920, 'h': 1080, 'stored_w': 1920, 'stored_h': 1080,
                'license': 'CC BY 4.0', 'artist': u'测试摄影师 %d' % n,
                'relevance': 88, 'bytes': os.path.getsize(fp), 'sha1': C.sha1(fp),
                'fetched_at': C.now()}, ensure_ascii=False) + chr(10))
            s['asset_kind'] = 'image'
            s['need_material'] = True
            n_ok += 1
    json.dump(sh_g, io.open(sp_g, 'w', encoding='utf-8'), ensure_ascii=False)
    subprocess.run([sys.executable, engine, 'build', '--work', g20_good],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_g20, t_g20 = run_check_at(g20_good)
    pct = re.search(r'"material_pct":\s*([0-9.]+)', t_g20)
    g20_ok = u'G20:' not in t_g20 and pct and float(pct.group(1)) >= 50.0
    notes.append("G20 正证（过半页铺 1920x1080 真素材）：覆盖率=%s 无 G20=%s -> %s" % (
        pct.group(1) if pct else '未测到', u'G20:' not in t_g20, 'ok' if g20_ok else 'FAIL'))
    if not g20_ok:
        fails.append("G20 误伤：过半页有真素材仍被拦（覆盖率 %s）" % (pct.group(1) if pct else '?'))

    # 降级出口：--allow-field 只能把 G20 变成显式声明，不许读成电影级交付
    r_af = subprocess.run([sys.executable, engine, 'check', '--work', g20_bad,
                          '--allow-field'], capture_output=True, text=True,
                         encoding='utf8', errors='replace')
    rep_af = os.path.join(g20_bad, 'report', u'验收报告.md')
    t_af = io.open(rep_af, encoding='utf-8').read() if os.path.exists(rep_af) else ''
    # 断言的是"降级必须留下书面痕迹"，不是"这一版全绿"：同一次 check 里往往还有别的硬伤
    # 报告里的措辞是「**不合格，N 条硬伤：**」，控制台才是「不合格 N 条：」——别拿控制台话术去正则报告
    m_b = re.search(u'不合格，(\d+) 条硬伤', t_b)
    m_a = re.search(u'不合格，(\d+) 条硬伤', t_af)
    n_before = int(m_b.group(1)) if m_b else -1
    n_after = int(m_a.group(1)) if m_a else -1
    declared = (u'降级交付_素材覆盖不足' in t_af and u'--allow-field' in t_af
                and u'suppressed_findings' in t_af and u'G20:' in t_af
                and n_before >= 0 and n_after >= 0 and n_after < n_before)
    notes.append("G20 降级出口（--allow-field）：书面降级声明=%s 不合格条数 %s→%s -> %s" % (
        u'降级交付_素材覆盖不足' in t_af, n_before, n_after, 'ok' if declared else 'FAIL'))
    if not declared:
        fails.append("G20 降级出口不合规：--allow-field 后报告没把「素材覆盖不足」写成显式降级声明，"
                     "或者一条硬伤都没少（那就是没生效）")

    # --- G21 溯源基准锁：v17 那份"抽旧 PPT 当 Word 原稿"的假基准必须被拦 -------------
    # 反证 A：outline 没有 source 指纹块（旧版 plan 跑的）→ 无法证明源没被换，必须拦
    g21_a = os.path.join(TMP, 'g21_nofp')
    shutil.rmtree(g21_a, ignore_errors=True)
    shutil.copytree(cli, g21_a, ignore=shutil.ignore_patterns('__pycache__'))
    o_a = json.load(io.open(os.path.join(g21_a, 'outline.json'), encoding='utf-8'))
    o_a.pop('source', None)
    json.dump(o_a, io.open(os.path.join(g21_a, 'outline.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    rc_a, t_a = run_check_at(g21_a)
    a_bites = u'G21:' in t_a and u'指纹' in t_a
    notes.append("G21 反证A（基准无指纹）：报告出现 G21=%s 退出码=%d -> %s" % (
        a_bites, rc_a, 'ok' if a_bites else 'FAIL'))
    if not a_bites:
        fails.append("G21 不咬：溯源基准没有指纹块也能过（换掉源文件这件事永远查不出来）")

    # 反证 B：plan 之后源文件被换（SHA1 不符）→ 必须拦并点名"被换过"
    g21_b = os.path.join(TMP, 'g21_swapped')
    shutil.rmtree(g21_b, ignore_errors=True)
    shutil.copytree(cli, g21_b, ignore=shutil.ignore_patterns('__pycache__'))
    o_b = json.load(io.open(os.path.join(g21_b, 'outline.json'), encoding='utf-8'))
    o_b['source']['sha1'] = 'f' * 40
    json.dump(o_b, io.open(os.path.join(g21_b, 'outline.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    rc_b2, t_b2 = run_check_at(g21_b)
    b_bites = u'G21:' in t_b2 and u'被换过' in t_b2
    notes.append("G21 反证B（plan 后偷换源）：点名被换过=%s 退出码=%d -> %s" % (
        b_bites, rc_b2, 'ok' if b_bites else 'FAIL'))
    if not b_bites:
        fails.append("G21 不咬：plan 之后把源文件换掉也能过")

    # 反证 C：源里的台账没进成品 → 必须拦并点名缺第几张表（v17 的 16 张表→0 张就是这条）
    g21_c = os.path.join(TMP, 'g21_tables')
    shutil.rmtree(g21_c, ignore_errors=True)
    shutil.copytree(cli, g21_c, ignore=shutil.ignore_patterns('__pycache__'))
    import docx as _dx2
    dp_c = os.path.join(g21_c, 'with_tables.docx')
    _d2 = _dx2.Document()
    _d2.add_heading('带台账的真源', 0)
    _d2.add_heading('一、签约台账', 1)
    _d2.add_paragraph('2026年公司计划在 12 城签约 268 台设备并完成交付验收')
    for hdr, rows in ((['城', '台数', '回款额'], [['济南', '31', '128.6 万']]),
                      (['车型', '单车年收', '投放门槛'], [['顶配', '144.9 万', '136 万']])):
        tb = _d2.add_table(rows=1 + len(rows), cols=3)
        for ci, h in enumerate(hdr):
            tb.rows[0].cells[ci].text = h
        for ri, r in enumerate(rows, 1):
            for ci, v in enumerate(r):
                tb.rows[ri].cells[ci].text = v
    _d2.save(dp_c)
    o_c = json.load(io.open(os.path.join(g21_c, 'outline.json'), encoding='utf-8'))
    o_c['docx'] = os.path.abspath(dp_c)
    # chars/paragraphs/tables 必须按 plan 的同一口径算，否则这条反证测的就不是"换源"而是"算错指纹"
    _seq_c = C.doc_blocks(_dx2.Document(dp_c))
    o_c['source'] = {'path': os.path.abspath(dp_c), 'sha1': C.sha1(dp_c),
                     'bytes': os.path.getsize(dp_c),
                     'chars': sum(len(b['text']) for b in _seq_c if b['kind'] == 'p'),
                     'paragraphs': len([b for b in _seq_c if b['kind'] == 'p']),
                     'tables': len([b for b in _seq_c if b['kind'] == 'tbl'])}
    json.dump(o_c, io.open(os.path.join(g21_c, 'outline.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    rc_c, t_c = run_check_at(g21_c)
    c_bites = u'G21:' in t_c and u'没成建制进成品' in t_c
    notes.append("G21 反证C（源有 2 张表而成品 0 张）：点名缺表=%s 退出码=%d -> %s" % (
        c_bites, rc_c, 'ok' if c_bites else 'FAIL'))
    if not c_bites:
        fails.append("G21 不咬：源里的台账一张都没进成品也能过（v17 的 16→0 就是这条路）")

    # 反证 D：基准是从旧成品抽字的派生件 → 必须拦（v17 的 input.docx 正是这种）
    g21_d = os.path.join(TMP, 'g21_derived')
    shutil.rmtree(g21_d, ignore_errors=True)
    shutil.copytree(cli, g21_d, ignore=shutil.ignore_patterns('__pycache__'))
    old_deck = os.path.join(g21_d, '上一版.pptx')
    shutil.copyfile(os.path.join(g21_d, 'out', 'deck.pptx'), old_deck)
    paras = [x for x in re.split(u'[。！？；\n]', C.pptx_all_text(old_deck))
             if len(x.strip()) >= 12][::2]
    dp_d = os.path.join(g21_d, 'derived.docx')
    _d3 = _dx2.Document()
    _d3.add_heading('抽字生成的假基准', 0)
    for p in paras:
        _d3.add_paragraph(p.strip())
    _d3.save(dp_d)
    o_d = json.load(io.open(os.path.join(g21_d, 'outline.json'), encoding='utf-8'))
    seq_d = C.doc_blocks(_dx2.Document(dp_d))
    o_d['docx'] = os.path.abspath(dp_d)
    o_d['source'] = {'path': os.path.abspath(dp_d), 'sha1': C.sha1(dp_d),
                     'bytes': os.path.getsize(dp_d),
                     'chars': sum(len(b['text']) for b in seq_d if b['kind'] == 'p'),
                     'paragraphs': len([b for b in seq_d if b['kind'] == 'p']),
                     'tables': len([b for b in seq_d if b['kind'] == 'tbl'])}
    json.dump(o_d, io.open(os.path.join(g21_d, 'outline.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    rc_d, t_d = run_check_at(g21_d)
    d_bites = u'G21:' in t_d and u'派生' in t_d
    notes.append("G21 反证D（基准由旧成品抽字派生）：点名派生=%s 退出码=%d -> %s" % (
        d_bites, rc_d, 'ok' if d_bites else 'FAIL'))
    if not d_bites:
        fails.append("G21 不咬：把上一版片子抽字当 Word 原稿也能过，G1/G13 就成了自我循环证明")

    # 正证：指纹一致、表都在、源不是派生件 → G21 一条都不许报
    g21_e = os.path.join(TMP, 'g21_ok')
    shutil.rmtree(g21_e, ignore_errors=True)
    shutil.copytree(cli, g21_e, ignore=shutil.ignore_patterns('__pycache__'))
    rc_e, t_e = run_check_at(g21_e)
    e_ok = u'G21:' not in t_e
    notes.append("G21 正证（真基准不动）：无 G21=%s -> %s" % (e_ok, 'ok' if e_ok else 'FAIL'))
    if not e_ok:
        fails.append("G21 误伤：指纹一致、表格齐备、源非派生的正常作业也被拦")

    # 反证 E：同一张真素材铺满多页（v17 那张渐变铺 28 页的"素材版"）→ 必须拦，
    # 而且 --allow-field 不许把它放行——那不是"没素材"，是"素材被复制粘贴"。
    # 夹具自己造一册 14 页：cli 那册只有 2 个可铺静帧的页，凑不出「同一张铺 >3 页」。
    g20_reuse = os.path.join(TMP, 'g20_reuse')
    shutil.rmtree(g20_reuse, ignore_errors=True)
    os.makedirs(g20_reuse, exist_ok=True)
    d_r = os.path.join(g20_reuse, 'doc.docx')
    _d4 = _dx2.Document()
    _d4.add_heading(u'复用探针册', 0)
    _d4.add_heading(u'一、运营', 1)
    for i in range(12):
        _d4.add_paragraph(u'第%d个城市的门店签约 268 台设备，完成筛查 %d 例，回款 128.6 万元。'
                          % (i + 1, 1200 + i))
    _d4.save(d_r)
    subprocess.run([sys.executable, engine, 'plan', d_r, '--work', g20_reuse,
                    '--per-page', '1', '--auto-pages'], capture_output=True, text=True,
                   encoding='utf8', errors='replace')
    shared = os.path.join(g20_reuse, 'materials', 'shared.jpg')
    im_r = Image.new('RGB', (1920, 1080))
    for yy in range(0, 1080, 8):
        for xx in range(0, 1920, 8):
            im_r.putpixel((xx, yy), ((xx * 5 + yy) % 256, (yy * 3) % 256, (xx * 2 + yy * 7) % 256))
    im_r.save(shared, quality=90)
    sp_r = os.path.join(g20_reuse, 'shots.json')
    sh_r = json.load(io.open(sp_r, encoding='utf-8'))
    picked = [s for s in sh_r['pages']
              if s['role'] not in ('table', 'cover', 'divider', 'closing')][:5]
    if len(picked) < 4:
        fails.append("G20 反证E 夹具不成立：可铺图的静帧页只有 %d 页，凑不出「同一张铺 >3 页」"
                     % len(picked))
    rows_r = []
    for s in picked:
        s['asset_kind'] = 'image'
        s['need_material'] = True
        rows_r.append({'page': s['page'], 'term': 'reuse probe', 'file': shared,
                       'url': 'https://commons.wikimedia.org/wiki/File:Reuse_probe.jpg',
                       'source_page': 'https://commons.wikimedia.org/wiki/File:Reuse_probe.jpg',
                       'license': 'CC BY-SA 4.0', 'artist': 'Probe Author',
                       'w': 3200, 'h': 1800, 'relevance': 88,
                       'stored_w': 1920, 'stored_h': 1080,
                       'bytes': os.path.getsize(shared), 'sha1': C.sha1(shared),
                       'fetched_at': '2026-11-06 00:00', 'proxy_used': 'http://127.0.0.1:7897'})
    json.dump(sh_r, io.open(sp_r, 'w', encoding='utf-8'), ensure_ascii=False)
    with io.open(os.path.join(g20_reuse, 'materials', 'manifest.jsonl'), 'w',
                 encoding='utf-8') as MJ:
        for r in rows_r:
            MJ.write(json.dumps(r, ensure_ascii=False) + '\n')
    subprocess.run([sys.executable, engine, 'build', '--work', g20_reuse],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_r, t_r = run_check_at(g20_reuse)
    r_bites = u'G20:' in t_r and u'铺了' in t_r
    notes.append("G20 反证E（同一张素材铺 %d 页）：点名铺页=%s 退出码=%d -> %s" % (
        len(picked), r_bites, rc_r, 'ok' if r_bites else 'FAIL'))
    if not r_bites:
        fails.append("G20 不咬：一张图铺满多页也能过（v17 同一张渐变铺 28 页就是这个洞）")
    r_rf = subprocess.run([sys.executable, engine, 'check', '--work', g20_reuse,
                           '--allow-field'], capture_output=True, text=True,
                          encoding='utf8', errors='replace')
    rep_rf = os.path.join(g20_reuse, 'report', u'验收报告.md')
    t_rf = io.open(rep_rf, encoding='utf-8').read() if os.path.exists(rep_rf) else ''
    no_escape = u'铺了' in t_rf
    notes.append("G20 反证E 加严（--allow-field 不许赦免铺页）：仍在报告=%s -> %s" % (
        no_escape, 'ok' if no_escape else 'FAIL'))
    if not no_escape:
        fails.append("--allow-field 把「同一张素材铺多页」也赦免了：那是复制粘贴，不是色场兜底")

    # --- fetch 的 429 退避：限流不是"这页没素材"（实测 106 页里 40 页被 429 打成 gaps）------
    import time as _time
    import urllib.error as _uerr
    _real_open, _real_sleep = C._open_once, _time.sleep
    slept = []
    _time.sleep = lambda s: slept.append(s)
    calls = {'n': 0}

    def _flaky(url, proxy, timeout):
        calls['n'] += 1
        if calls['n'] <= 2:
            raise _uerr.HTTPError(url, 429, 'Too Many Requests', {}, None)
        return 'SENTINEL'

    C._open_once = _flaky
    C._get_state['last'] = 0.0
    try:
        got = C.http_get('https://example.invalid/api', tries=4)
        # 只取退避那几档：节流间隔（GET_MIN_INTERVAL 2.2 秒）也在同一个列表里，别混为一谈
        backoffs = [x for x in slept if x >= 5]
    finally:
        C._open_once, _time.sleep = _real_open, _real_sleep
    retry_ok = got == 'SENTINEL' and calls['n'] == 3 and backoffs[:2] == [6, 12]
    notes.append("取图 429 退避（两次限流后成功）：取到=%s 请求次数=%d 退避序列=%s -> %s" % (
        got == 'SENTINEL', calls['n'], backoffs[:2], 'ok' if retry_ok else 'FAIL'))
    if not retry_ok:
        fails.append("http_get 不重试 429：限流会被记成「这页没素材」，覆盖率被误杀")

    # 撤销差分：一直 429 必须抛错，不许静默返回空让 fetch 写一条"没货"的 gap
    calls2 = {'n': 0}

    def _always429(url, proxy, timeout):
        calls2['n'] += 1
        raise _uerr.HTTPError(url, 429, 'Too Many Requests', {}, None)

    _time.sleep = lambda s: None
    C._open_once = _always429
    C._get_state['last'] = 0.0
    raised = False
    try:
        try:
            C.http_get('https://example.invalid/api', tries=3)
        except _uerr.HTTPError:
            raised = True
    finally:
        C._open_once, _time.sleep = _real_open, _real_sleep
    strict_ok = raised and calls2['n'] == 3
    notes.append("取图 429 耗尽仍要抛错（不许当没素材）：抛错=%s 请求次数=%d -> %s" % (
        raised, calls2['n'], 'ok' if strict_ok else 'FAIL'))
    if not strict_ok:
        fails.append("重试耗尽后 http_get 没抛错：限流会被伪装成「这页找不到素材」")

    # --- plan 重跑不许留错位分镜：改了 --per-page 之后 shots 必须与 outline 同页数同内容 ------
    # 旧实现按"页号在不在 shots 里"决定要不要新建，于是 131 页的新 outline 配上了 168 页的旧
    # shots（页号还在、内容已经错位），build 直接 IndexError——v19 这轮真的踩到了。
    plan_re = os.path.join(TMP, 'plan_re')
    shutil.rmtree(plan_re, ignore_errors=True)
    shutil.copytree(cli, plan_re, ignore=shutil.ignore_patterns('__pycache__', 'report'))
    subprocess.run([sys.executable, engine, 'plan', os.path.join(plan_re, 'doc.docx'),
                    '--work', plan_re, '--per-page', '1', '--auto-pages'],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    ol_re = json.load(io.open(os.path.join(plan_re, 'outline.json'), encoding='utf-8'))
    sh_re = json.load(io.open(os.path.join(plan_re, 'shots.json'), encoding='utf-8'))
    same_n = len(sh_re['pages']) == len(ol_re['pages'])
    sig_ok = all(s.get('page_sig') for s in sh_re['pages'])
    notes.append("plan 改分页重跑：outline %d 页 / shots %d 页，页数一致=%s 每条带内容签名=%s -> %s" % (
        len(ol_re['pages']), len(sh_re['pages']), same_n, sig_ok,
        'ok' if (same_n and sig_ok) else 'FAIL'))
    if not (same_n and sig_ok):
        fails.append("plan 重跑留下了错位分镜（shots 与 outline 页数不同或没有内容签名），"
                     "build 会按错页装配或直接崩")

    # --- S1 目录剥离 + 字数分页：这两条都是 v19 干跑时被成品闸抓出来的 ------------------
    toc_dir = os.path.join(TMP, 'toc_drop')
    shutil.rmtree(toc_dir, ignore_errors=True)
    os.makedirs(toc_dir, exist_ok=True)
    d_toc = os.path.join(toc_dir, 'doc.docx')
    _d5 = _dx2.Document()
    _d5.add_heading(u'带目录的运营手册', 0)
    try:
        _toc_style = _d5.styles['TOC 1']
    except KeyError:
        from docx.enum.style import WD_STYLE_TYPE
        _toc_style = _d5.styles.add_style('TOC 1', WD_STYLE_TYPE.PARAGRAPH)
    _p = _d5.add_paragraph(u'第一章 签约进展\t3')
    _p.style = _toc_style
    _d5.add_heading(u'一、签约进展', 1)
    _d5.add_paragraph(u'2026年公司在华东 12 城签约 268 台，实际交付 214 台，回款 79.9%。')
    _d5.add_paragraph(u'第二城长沙的样板期定为十二个月，第六个月是第一个决策点。')
    _d5.save(d_toc)
    subprocess.run([sys.executable, engine, 'plan', d_toc, '--work', toc_dir, '--auto-pages'],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    ol_toc = json.load(io.open(os.path.join(toc_dir, 'outline.json'), encoding='utf-8'))
    dropped_txt = [x.get('text', '') for x in (ol_toc.get('dropped') or [])]
    in_pages = any('签约进展\t' in (p['headline'] + ' '.join(p.get('body') or []))
                   for p in ol_toc['pages'])
    toc_ok = any('签约进展' in x for x in dropped_txt) and not in_pages
    notes.append("S1 目录剥离：dropped=%d 条、目录条目没进分镜=%s -> %s" % (
        len(dropped_txt), not in_pages, 'ok' if toc_ok else 'FAIL'))
    if not toc_ok:
        fails.append("目录条目被当成正文排进分镜（成品会出现「第X章\t页码」这种标题页），"
                     "或剥离没记进 outline['dropped']")

    # 剥掉的句子不许被 G13 判成"丢内容"——否则唯一出路就是造假源
    subprocess.run([sys.executable, engine, 'scaffold', '--work', toc_dir],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    prep_delivery(toc_dir)
    subprocess.run([sys.executable, engine, 'build', '--work', toc_dir],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_toc, t_toc = run_check_at(toc_dir)
    g13_quiet = u'G13:' not in t_toc
    declared = re.search(r'"declared_dropped":\s*(\d+)', t_toc)
    toc_pass_ok = g13_quiet and declared and int(declared.group(1)) >= 1
    notes.append("G13 豁免有账取舍：无 G13=%s 交底条数=%s -> %s" % (
        g13_quiet, declared.group(1) if declared else '未报', 'ok' if toc_pass_ok else 'FAIL'))
    if not toc_pass_ok:
        fails.append("有账的取舍没被豁免（或报告里没写 declared_dropped）："
                     "那 agent 只能靠缩源过关，正是 v17 的造假动因")

    # 字数分页：整章长句不许挤成一页文字墙
    dense_dir = os.path.join(TMP, 'dense_split')
    shutil.rmtree(dense_dir, ignore_errors=True)
    os.makedirs(dense_dir, exist_ok=True)
    d_dn = os.path.join(dense_dir, 'doc.docx')
    _d6 = _dx2.Document()
    _d6.add_heading(u'长句册', 0)
    _d6.add_heading(u'一、长句', 1)
    for i in range(4):
        _d6.add_paragraph(u'第%d条：公司在一期投放六台车，覆盖三座城市，'
                          u'单台车年筛查 12,000 例，回款口径 144.9 万元，'
                          u'剩余部分由合作门店与工厂按协议分配，验收以第三方报告为准。' % (i + 1))
    _d6.save(d_dn)
    subprocess.run([sys.executable, engine, 'plan', d_dn, '--work', dense_dir,
                    '--per-page', '4', '--auto-pages'], capture_output=True, text=True,
                   encoding='utf8', errors='replace')
    ol_dn = json.load(io.open(os.path.join(dense_dir, 'outline.json'), encoding='utf-8'))
    worst = max(len(''.join(pg.get('body') or [])) for pg in ol_dn['pages'])
    cap_ok = worst <= C.TH['page_chars_max'] + 60
    notes.append("S1 字数分页（--per-page 4、每句约 110 字）：最重一页 %d 字（上限 %d+一句）-> %s" % (
        worst, C.TH['page_chars_max'], 'ok' if cap_ok else 'FAIL'))
    if not cap_ok:
        fails.append("分页只数句子不数文字：整页被顶成 %d 字，成品会命中 G19 文字墙并挤出留白区" % worst)

    # 引文页豁免只给"整页一句长话"：多句挤一页仍是文字墙，必须照拦
    mixed_dir = os.path.join(TMP, 'mixed_wall')
    shutil.rmtree(mixed_dir, ignore_errors=True)
    os.makedirs(mixed_dir, exist_ok=True)
    d_m = os.path.join(mixed_dir, 'doc.docx')
    _d7 = _dx2.Document()
    _d7.add_heading(u'长句与多句', 0)
    _d7.add_heading(u'一、多句', 1)
    _d7.add_paragraph(u'第一条：公司在一期投放六台车，覆盖三座城市，单台车年筛查 12,000 例，'
                      u'回款口径 144.9 万元，剩余部分由合作门店与工厂按协议分配；'
                      u'门店端每完成一次复检即计入一次有效履约，工厂端按实际装机数量季度结算，'
                      u'两个口径都在同一张表里核对，差异超过三个百分点就回到合同乙方复核。')
    _d7.add_paragraph(u'第二条：验收以第三方报告为准，样本管在冷链柜里保存七十二小时以内，'
                      u'超时作废的比例计入质控指标，作废样本由工厂补发并承担全部冷链费用，'
                      u'补发记录与运输温度曲线一并归档，作为月度合规检查与年度审计的附件依据。')
    _d7.add_paragraph(u'第三条：护士由合作医院派出，出车前完成建档与知情同意流程培训，'
                      u'培训时长、考核通过率与上岗名单一并归档并每月更新，'
                      u'名单外的人员不得独立上岗，带教期间的责任由派出医院与本项目共同承担。')
    _d7.save(d_m)
    subprocess.run([sys.executable, engine, 'plan', d_m, '--work', mixed_dir,
                    '--per-page', '3', '--max-chars', '900', '--auto-pages'],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    ol_m = json.load(io.open(os.path.join(mixed_dir, 'outline.json'), encoding='utf-8'))
    one = [p_ for p_ in ol_m['pages'] if len(p_.get('body') or []) == 1]
    multi = [p_ for p_ in ol_m['pages'] if len(p_.get('body') or []) > 1]
    role_ok = (all(p_['role'] == 'quote' for p_ in one if len(p_['body'][0]) >= 300)
               and all(p_['role'] != 'quote' for p_ in multi))
    notes.append("S1 引文页判据：单句≥300 字走 quote=%s、多句页不冒充 quote=%s（1 句页 %d 个 / 多句页 %d 个）-> %s" % (
        True, role_ok, len(one), len(multi), 'ok' if role_ok else 'FAIL'))
    if not role_ok:
        fails.append("quote 角色用错了范围：要么单句长话没按引文页排，要么多句挤一页也被当引文页豁免")

    # 取图走 3200 rendition 而不是原图（原图 8–21MB，实测每页多等 60–90 秒）：
    # 不打网络——喂一份假的 API 回包，看解析出来的字段与请求参数
    cap = {}

    class _FakeResp(object):
        def read(self):
            return b'{}'

        def close(self):
            pass

    def _fake_get(url, timeout=40, proxy=None, tries=1):
        cap['url'] = url
        import io as _io
        return _io.BytesIO(b'{"query":{"pages":{"1":{"title":"File:Salon interior.jpg",'
                           b'"imageinfo":[{"url":"https://upload.example/full.jpg",'
                           b'"thumburl":"https://upload.example/3200px-full.jpg",'
                           b'"width":4000,"height":2667,"mime":"image/jpeg",'
                           b'"extmetadata":{"LicenseShortName":{"value":"CC0"}}}]}}}}')

    _real_get = C.http_get
    C.http_get = _fake_get
    try:
        cands, err = C.search_commons('beauty salon', n=8, proxy=None)
    finally:
        C.http_get = _real_get
    rend_ok = (bool(cands) and cands[0].get('rendition', '').endswith('3200px-full.jpg')
               and cands[0]['url'].endswith('/full.jpg') and cands[0]['w'] == 4000
               and ('iiurlwidth=%d' % C.RENDER_LONG_EDGE) in cap.get('url', ''))
    notes.append("取图 rendition 解析：%s -> %s" % (
        'thumburl=%s 原图 url 保留=%s 请求带 iiurlwidth=%s' % (
            bool(cands and cands[0].get('rendition')), bool(cands and cands[0]['url'].endswith('full.jpg')),
            'iiurlwidth' in cap.get('url', '')), 'ok' if rend_ok else 'FAIL'))
    if not rend_ok:
        fails.append("search_commons 没把 3200 rendition 交出来（或请求里没带 iiurlwidth）："
                     "取图会继续搬原图，每页多等一分钟")

    # G19 文字墙的正反对照：台账页天然密（40+ 格），不能按散文的墙判它；
    # 而三句长话挤一页（role=data）必须照判
    tbl_dir = os.path.join(TMP, 'g19_table')
    shutil.rmtree(tbl_dir, ignore_errors=True)
    os.makedirs(tbl_dir, exist_ok=True)
    d_t = os.path.join(tbl_dir, 'doc.docx')
    _d8 = _dx2.Document()
    _d8.add_heading(u'台账册', 0)
    _d8.add_heading(u'一、风险清单', 1)
    _d8.add_paragraph(u'这一页只放台账，正文一句话都没有，用来验证附表页不被当成文字墙。')
    tb8 = _d8.add_table(rows=11, cols=4)
    for ci, h in enumerate([u'风险', u'概率', u'影响', u'我们的动作']):
        tb8.rows[0].cells[ci].text = h
    for ri in range(1, 11):
        tb8.rows[ri].cells[0].text = u'第%d条：转化率实测低于 8%%，城市合作门店签约不足' % ri
        tb8.rows[ri].cells[1].text = u'中'
        tb8.rows[ri].cells[2].text = u'回款推迟一个季度，约 128.6 万元'
        tb8.rows[ri].cells[3].text = u'第六个月复核，低于阈值即停止投放并转入第二城'
    _d8.save(d_t)
    subprocess.run([sys.executable, engine, 'plan', d_t, '--work', tbl_dir, '--auto-pages'],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    subprocess.run([sys.executable, engine, 'scaffold', '--work', tbl_dir],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    prep_delivery(tbl_dir)
    subprocess.run([sys.executable, engine, 'build', '--work', tbl_dir],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_t19, t_t19 = run_check_at(tbl_dir)
    rep_t19 = os.path.join(tbl_dir, 'report', u'验收报告.md')
    body_t19 = io.open(rep_t19, encoding='utf-8').read() if os.path.exists(rep_t19) else ''
    cells = re.search(r'"table_cells":\s*(\d+)', body_t19)
    wall_free = u'dense_text_wall' not in body_t19
    tbl_ok = cells and int(cells.group(1)) >= 40 and wall_free
    notes.append("G19 附表页豁免：台账 %s 格、报告里没有 dense_text_wall=%s -> %s" % (
        cells.group(1) if cells else '?', wall_free, 'ok' if tbl_ok else 'FAIL'))
    if not tbl_ok:
        fails.append("附表页被当成文字墙拦下（或台账根本没装进成品）：结构化数据页不是散文墙")

    # 同一份判据在散文页上必须照样咬。判据管的是"多句挤一页"这种成品形态，
    # 所以这里把三段长话直接写回一页（--verbatim 留档册就是这个形态），
    # 而不是靠"分页故意失效"来造墙——事实预算上线后 plan 已经会把长句拆成小页，
    # 那一半正好是要正向核的好行为，下面分开断言。
    subprocess.run([sys.executable, engine, 'scaffold', '--work', mixed_dir],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    prep_delivery(mixed_dir)
    _olm = json.load(io.open(os.path.join(mixed_dir, 'outline.json'), encoding='utf-8'))
    _wall_lines = [p_.get('body') or [] for p_ in _olm['pages']]
    _plan_quiet = max([sum(len(x) for x in b) for b in _wall_lines if b] or [0])
    _olm['verbatim'] = True
    _wp = _olm['pages'][min(2, len(_olm['pages']) - 1)]
    _wp['role'] = 'data'
    # 墙要用源文档那三段长话本身造：plan 已经把它们按事实预算拆到每页 ≤63 字了，
    # 拿拆好的碎片挤回一页凑不出 300 字，那就不是在测判据而是在测夹具
    _doc_sents = [x.text.strip() for x in _dx2.Document(d_m).paragraphs if x.text.strip()]
    _wall_txt = [x for x in _doc_sents if len(x) >= 80] or _doc_sents
    _wp['body'] = _wall_txt
    _wp['source_span'] = _wall_txt
    json.dump(_olm, io.open(os.path.join(mixed_dir, 'outline.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    bm = subprocess.run([sys.executable, engine, 'build', '--work', mixed_dir],
                        capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_m19, t_m19 = run_check_at(mixed_dir)
    prose_wall = u'dense_text_wall' in t_m19
    notes.append("G19 散文页照咬（三段长话硬挤一页，build rc=%d）：报告出现 dense_text_wall=%s；"
                 u"同一册按新事实预算分页后最重一页 %d 字（墙线 %d）-> %s" % (
                     bm.returncode, 'ok' if prose_wall else 'FAIL', _plan_quiet,
                     C.CT.BAD_RULES['dense_chars_min'],
                     'ok' if (bm.returncode == 0 and prose_wall and _plan_quiet < 300) else 'FAIL'))
    if bm.returncode != 0:
        fails.append("G19 散文页用例没装成型（build rc=%d）：%s" % (
            bm.returncode, (bm.stdout or '')[-160:]))
    if not prose_wall:
        fails.append("多句挤一页却没被判成文字墙：豁免范围扩过头了")
    if _plan_quiet >= 300:
        fails.append("事实预算分页后仍然有 %d 字的一页（等于还会出文字墙）——"
                     "拆页那条路没生效" % _plan_quiet)


    # 档案照/信息图筛子：实测 'data center' 命中 1970 年代氧气厂开关柜黑白照、
    # 'clinic reception' 命中 1940 年代煤矿公司前台、'cold storage' 命中仓库建筑外观
    import safety as _sf
    arch_bad = [
        'File:LOOKING SOUTH AT THE MOTOR CONTROL SWITCHING PANEL FOR BASIC OXYGEN FURNACE No. 2.jpg',
        'File:Data center infrastructure in the United States.jpg',
        'File:Black Mountain Coal Company, 30-31 Mine, Kenvir, Harlan County, Kentucky. Reception desk.jpg',
        'File:Map folder for USGS lunar geologic maps.jpg',
        # 2026-11-08：医学词命中的历史画作/漫画（许可 PD、词面相关度满分，但画不是实景）
        'File:Three doctors in close discussion, their patient being nurse Wellcome V0011035.jpg',
        'File:Oil painting of a village clinic interior.jpg',
        'File:Caricature engraving of physicians, 18th century.jpg']
    arch_good = ['File:Server Room (22397102849).jpg',
                 'File:Doctor office - New Orleans Medical District, Sept 2023 - 1.jpg',
                 'File:Changsha Skyline at night 2021.jpg',
                 'File:Ambulance interior 2019.jpg',
                 'File:Modern medical examination room.jpg']
    caught = sum(1 for x in arch_bad if _sf.is_archival(x))
    spared = sum(1 for x in arch_good if _sf.is_archival(x))
    arch_ok = caught == len(arch_bad) and spared == 0
    notes.append("档案照/画作筛子：%d 张档案/信息图/历史画作拦下 %d，%d 张当代实景误伤 %d -> %s" % (
        len(arch_bad), caught, len(arch_good), spared, 'ok' if arch_ok else 'FAIL'))
    if not arch_ok:
        fails.append("档案照/信息图/历史画作没拦住，或把当代实景误杀了（G3 会放离题图进成品）")

    # 端到端：往 manifest 里塞一条档案照，check 必须复筛出来（fetch 时拦过不算，成品要再核一次）
    arch_dir = os.path.join(TMP, 'archival_manifest')
    shutil.rmtree(arch_dir, ignore_errors=True)
    shutil.copytree(cli, arch_dir, ignore=shutil.ignore_patterns('__pycache__', 'report'))
    mp_a = os.path.join(arch_dir, 'materials', 'manifest.jsonl')
    # 夹具得先真有一张 1920x1080 的图，否则 manifest 记的文件不存在，测的就不是筛子而是缺文件
    arch_img = os.path.join(arch_dir, 'materials', 'p002.jpg')
    _ia = Image.new('RGB', (1920, 1080))
    for _yy in range(0, 1080, 8):
        for _xx in range(0, 1920, 8):
            _ia.putpixel((_xx, _yy), ((_xx * 3 + _yy) % 256, (_yy * 5) % 256, (_xx + _yy) % 256))
    _ia.save(arch_img, quality=90)
    io.open(mp_a, 'w', encoding='utf-8').write(json.dumps(
        {'page': 2, 'term': 'data center',
         'file': arch_img,
         'url': 'https://commons.wikimedia.org/wiki/File:Switchboard',
         'source_page': 'File:LOOKING SOUTH AT THE MOTOR CONTROL SWITCHING PANEL FOR BASIC OXYGEN FURNACE No. 2.jpg',
         'license': 'Public domain', 'w': 4000, 'h': 3000, 'relevance': 90,
         'bytes': os.path.getsize(arch_img),
         'sha1': C.sha1(arch_img),
         'stored_w': 1920, 'stored_h': 1080,
         'proxy_used': 'http://127.0.0.1:7897'}, ensure_ascii=False) + '\n')
    rc_ar, t_ar = run_check_at(arch_dir)
    arch_gate = (u'G3:' in t_ar and u'档案' in t_ar)
    notes.append("G3 复筛档案照（manifest 里塞一条）：报告出现 G3 档案条=%s 退出码=%d -> %s" % (
        arch_gate, rc_ar, 'ok' if arch_gate else 'FAIL'))
    if not arch_gate:
        fails.append("G3 没在验收端复筛档案照/信息图：fetch 时漏掉的离题图会一路进成品")

    # 黑白判据的好坏对照：真消色必须判黑，"色调安静的现代片"不许误杀
    # （旧判据只看平均彩度 <12，实测 104 张里误杀 15 张现代图 = 68% 误杀率）
    import safety as _S
    from PIL import Image as _Im, ImageDraw as _ImDraw
    _bw = os.path.join(TMP, 'gray_bw.jpg')
    _mo = os.path.join(TMP, 'gray_modern.jpg')
    _Im.new('RGB', (480, 270), (122, 122, 126)).save(_bw)
    _im2 = _Im.new('RGB', (480, 270), (24, 26, 30))
    _dr = _ImDraw.Draw(_im2)
    for _by in range(0, 270, 60):            # 夜色里零星霓虹：占比 2.5%，缩样后仍然是饱和的
        for _bx in range(0, 480, 60):
            _dr.rectangle([_bx + 3, _by + 3, _bx + 11, _by + 11], fill=(238, 168, 42))
    _im2.save(_mo, quality=92)
    bw_hit = _S.is_grayscale(_bw)
    mo_quiet = not _S.is_grayscale(_mo)
    _ch = [max(p) - min(p) for p in _im2.getdata()]
    mean_only = sum(_ch) / float(len(_ch))
    sat_share = 100.0 * sum(1 for c in _ch if c > 30) / len(_ch)
    notes.append(u"黑白判据正反对照：纯消色图判黑=%s；暗底霓虹块判黑=%s"
                 u"（这张平均彩度 %.2f<12，旧判据必误杀；但 %.2f%% 的像素是饱和的）-> %s" % (
                     bw_hit, not mo_quiet, mean_only, sat_share,
                     u'ok' if (bw_hit and mo_quiet) else u'FAIL'))
    if not bw_hit:
        fails.append(u"黑白判据连一张纯消色图都判不出来，这道筛等于没有")
    if not mo_quiet:
        fails.append(u"黑白判据误杀现代低饱和图：平均彩度低但画面有饱和像素，"
                     u"必须用『彩度>30 的像素占比』二次确认（实测旧判据误杀 15/22）")


    # ---------------- Openverse 候选映射：不联网也要证明"不会被自己的 image/ 过滤器杀光"
    fake = {'url': 'https://live.staticflickr.com/7193/6890063877_c.jpg',
            'url_type': 'jpg', 'width': 5120, 'height': 3450,
            'title': 'medical clinic reception', 'license': 'by', 'license_version': '2.0',
            'creator': 'Towne Post Network', 'foreign_landing_url': 'https://flic.kr/x',
            'thumbnail': 'https://live.staticflickr.com/7193/6890063877_s.jpg'}
    cand = C.ov_candidate(fake)
    ov_ok = (cand and str(cand['mime']).startswith('image/') and
             cand['license'] == 'BY 2.0' and cand['artist'] == 'Towne Post Network' and
             cand['w'] == 5120 and cand['url'] == fake['url'])
    # 反证：把 url_type 换成裸扩展名以外的怪值也不能漏出非 image/ 的 mime
    weird = C.ov_candidate(dict(fake, url_type='JPG', license=None))
    ov_weird = weird and weird['mime'] == 'image/jpg' and weird['license'] == '许可未知'
    # 下载 URL 必须是能直接下的地址：Openverse 的 thumbnail 字段是 api.openverse.org 的
    # 转发口，实测拿它当 rendition 会整页 HTTP 424（v5 页1 就是这么丢的）
    wik = C.ov_candidate(dict(fake, url='https://upload.wikimedia.org/wikipedia/commons/a/a8/Bus.jpg',
                              thumbnail='https://api.openverse.org/v1/images/x/thumb/'))
    ov_rend = (wik or {}).get('rendition') or ''
    ov_url_ok = ('api.openverse.org' not in ov_rend and ov_rend.startswith(
        'https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/Bus.jpg/3840px-'))
    ov_flickr = 'api.openverse.org' not in cand['rendition'] and \
        cand['rendition'] == fake['url']
    notes.append(u"Openverse 候选映射（纯函数，不联网）：mime=%s 许可=%s 作者=%s；"
                 u"怪值 url_type/无许可时 mime=%s 许可=%s；rendition：wikimedia=%s flickr=%s -> %s" % (
                     (cand or {}).get('mime'), (cand or {}).get('license'),
                     (cand or {}).get('artist'), (weird or {}).get('mime'),
                     (weird or {}).get('license'), ov_rend[:60], cand['rendition'][:40],
                     u'ok' if (ov_ok and ov_weird and ov_url_ok and ov_flickr) else u'FAIL'))
    if not (ov_ok and ov_weird):
        fails.append(u"Openverse 候选映射不成立（mime/许可字段没归一），"
                     u"下游 image/ 过滤器会把整批候选杀成 0（实测踩过）")
    if not (ov_url_ok and ov_flickr):
        fails.append(u"Openverse 的下载 URL 还指向 api.openverse.org 转发口或没按 Commons "
                     u"路径规则拼 3840px 缩样：实测前者 424、后者能让单页下载量从 3.2MB 降到 1.5MB")


    # ---------------- 取图通道路由：每源各自退词（auto 合并下 commons 有货时 openverse 也要重试）
    # 这是 v5 覆盖停在 61/231 的根因反证：旧代码只看合并结果是否为空，
    # commons 一命中就跳过退词，Openverse 的 4 词 AND 长词组永远 0 贡献。
    OV_T = u'intercity coach bus exterior'
    OV_RELAX = u'intercity coach bus'
    notes.append(u"退词序列 _pick_terms：4 词=%s 3 词=%s" % (
        u'、'.join(C._pick_terms(OV_T)), u'、'.join(C._pick_terms(u'bus depot hall'))))
    if C._pick_terms(OV_T) != [OV_T, OV_RELAX] or C._pick_terms(u'bus depot') != [u'bus depot']:
        fails.append(u"_pick_terms 不成立：>3 词要补前 3 词，≤3 词不许乱退")

    _ov_calls = []

    def _fake_ov(t, n=40, proxy=None, size_kind=None):
        _ov_calls.append((t, size_kind))
        return ([{'url': 'https://upload.wikimedia.org/b.jpg', 'page': 'https://ov/x',
                  'title': OV_RELAX, 'w': 4000, 'h': 2600, 'mime': 'image/jpeg',
                  'license': 'CC BY 4.0', 'artist': 'OV', 'rel': 0}]
                if t == OV_RELAX else []), None

    def _fake_cc(t, n=40, proxy=None, size_kind=None):
        return ([{'url': 'https://upload.wikimedia.org/a.jpg', 'page': 'File:Coach bus.jpg',
                  'title': 'Coach bus', 'w': 5000, 'h': 3000, 'mime': 'image/jpeg',
                  'license': 'CC0', 'artist': 'CC', 'rel': 0}], None)

    _old_lanes = C.LANES
    C.LANES = dict(C.LANES, auto=(('openverse', _fake_ov), ('commons', _fake_cc)))
    try:
        outs, errs, rhits = C.fetch_candidates(OV_T, 'auto', None, min_edge=1080)
    finally:
        C.LANES = _old_lanes
    chs = sorted({x.get('channel') for x in outs})
    ov_used = [x.get('term_used') for x in outs if x.get('channel') == 'openverse']
    fc_ok = (chs == ['commons', 'openverse'] and ov_used == [OV_RELAX] and
             rhits == [('openverse', OV_RELAX)] and OV_RELAX in [c[0] for c in _ov_calls] and
             all(k == 'large' for _, k in _ov_calls))
    # 反证 2：openverse 真报错时错误必须留在说明里，但不能把 commons 的货一起判成 0
    def _boom(t, n=40, proxy=None, size_kind=None):
        return [], u'HTTP 429 限流'
    C.LANES = dict(C.LANES, auto=(('openverse', _boom), ('commons', _fake_cc)))
    try:
        outs2, errs2, _ = C.fetch_candidates(OV_T, 'auto', None, min_edge=1080)
    finally:
        C.LANES = _old_lanes
    C.LANES = dict(C.LANES, auto=(('openverse', _fake_ov), ('commons', _fake_cc)))
    try:
        outs3, _, _ = C.fetch_candidates(OV_T, 'auto', None, min_edge=720)
    finally:
        C.LANES = _old_lanes
    # 反证 3：短边门槛低于 1080 时不许强加 large 档（那会把中图池整批丢掉）
    small_kind = [k for _, k in _ov_calls][-1]
    fc_ok = fc_ok and len(outs2) == 1 and '429' in (errs2 or '') and small_kind is None
    notes.append(u"fetch_candidates(auto)：候选源=%s openverse 实际用词=%s 退词命中=%s size 档=%s；"
                 u"openverse 429 时 commons 仍出 %d 张且错误留痕=%s -> %s" % (
                     chs, ov_used, rhits, _ov_calls[-1][1], len(outs2), (errs2 or '')[:24],
                     u'ok' if fc_ok else u'FAIL'))
    if not fc_ok:
        fails.append(u"取图通道路由不成立：commons 命中后 openverse 的长词组没重试（多源等于白装，"
                     u"v5 实测覆盖 61/231），或短边≥1080 没有改用 Openverse 的 large 像素档"
                     u"（实测同一词不带 size 时 19 条里 0 条达标、带 large 时 18 条达标）")

    # --- docs must agree with code (this round really did ship 12/13/14/15 side by side) --
    import check_docs as _cd
    cd_gates = _cd.gates_in_code()
    notes.append("文档对账：代码闸 %d 道，编号 %s%s" % (
        len(cd_gates), cd_gates[:1] and cd_gates[0], '–%d' % cd_gates[-1] if cd_gates else ''))
    if cd_gates != list(range(1, len(cd_gates) + 1)) or len(cd_gates) < 15:
        fails.append("闸编号不连续或少于 15 道：%s" % cd_gates)


    # ---------------- G22-G26 + Unsplash 反向自证：全走真 CLI，不在测试里重算闸
    import docx as _dx9
    dg_dir = os.path.join(TMP, 'delivery_gate')
    shutil.rmtree(dg_dir, ignore_errors=True)
    os.makedirs(dg_dir)
    d_dg = os.path.join(dg_dir, 'doc.docx')
    _dd = _dx9.Document()
    _dd.add_heading(u'门店台账路演', 0)
    _dd.add_heading(u'一、签约与履约', 1)
    _dd.add_paragraph(u'2026 年华东 12 城签约 268 家机构，实际履约 214 家，履约率 79.9%。')
    _dd.add_paragraph(u'潜在池共 412 家，转化 96 家，客单 1.6 万元。')
    _dt = _dd.add_table(rows=3, cols=3)
    for _i, _row in enumerate([(u'城市', u'潜在门店', u'已签约'),
                               (u'上海', u'128', u'41'), (u'杭州', u'96', u'28')]):
        for _j, _v in enumerate(_row):
            _dt.rows[_i].cells[_j].text = _v
    _dd.save(d_dg)

    def dg_run(args):
        return subprocess.run([sys.executable, engine] + args + ['--work', dg_dir],
                              capture_output=True, text=True, encoding='utf8', errors='replace')

    dg_run([u'plan', d_dg, '--auto-pages'])
    dg_run([u'scaffold'])
    r_pk = dg_run([u'pack'])
    card_p = os.path.join(dg_dir, u'promptpack', u'delivery_card.md')
    req_p = os.path.join(dg_dir, u'promptpack', u'delivery_request.json')
    ack_p = os.path.join(dg_dir, u'promptpack', u'delivery_ack.json')
    deck0 = os.path.join(dg_dir, u'out', u'deck.pptx')
    # 1) pack 必须把投放卡全文打到 stdout（用户投诉"全程没弹提示词"这一条）
    stdout_has_card = (u'投放卡全文' in (r_pk.stdout or u'')) and (
        u'AI 作图' in (r_pk.stdout or u'')) and os.path.exists(req_p)
    # 2) 有提示词、没产物、没回执：build 必须停住并只问那一句，且不许留下成品
    if os.path.exists(deck0):
        os.remove(deck0)
    r_b0 = dg_run([u'build'])
    ask_shown = (r_b0.returncode != 0) and (u'视觉资源是否已下载就绪' in (r_b0.stdout or u''))
    no_deck = not os.path.exists(deck0)
    # 3) 卡片没发给用户就登记"已就绪"：ack 必须拒（产物也不在）
    ack_refused = dg_run([u'ack', u'--ready']).returncode != 0
    # 4) declined 放行但 check 要列成豁免项；5) declined 不带用户原话必须拒
    declined_ok = (dg_run([u'ack', u'--declined', u'--note',
                           u'用户回：这些图我不做了，按现状出片']).returncode == 0
                   and dg_run([u'build']).returncode == 0)
    if os.path.exists(ack_p):
        os.remove(ack_p)
    note_required = dg_run([u'ack', u'--declined']).returncode != 0
    # 6) 产物真放回 + ack --ready：装配放行
    req = json.load(io.open(req_p, encoding=u'utf-8'))
    for it in req['items']:
        dd = os.path.dirname(it['return_path'])
        if not os.path.isdir(dd):
            os.makedirs(dd)
        if it['kind'] in ('image', 'first', 'last'):
            _dl_png(it['return_path'])
        elif it['kind'] == 'gif':
            _dl_gif(it['return_path'])
        else:
            _dl_video(it['return_path'])
    ready_ok = (dg_run([u'ack', u'--ready', u'--note', u'用户回：已就绪']).returncode == 0
                and dg_run([u'build']).returncode == 0)
    # 7) 卡片被改过：旧回执立刻不算
    io.open(card_p, 'a', encoding=u'utf-8').write(u'\n<!-- 卡片被改过 -->\n')
    stale_card_refused = dg_run([u'ack', u'--ready']).returncode != 0
    # 8) 手工拼一份"时间倒序"的假回执：build 仍要拦
    dg_run([u'pack'])
    _req2 = json.load(io.open(req_p, encoding=u'utf-8'))
    io.open(ack_p, 'w', encoding=u'utf-8').write(json.dumps(
        {u'shown_to_user': True, u'answer': u'ready', u'note': u'x',
         u'card_sha1': _req2['card_sha1'], u'ack_at': u'2000-01-01T00:00:00',
         u'gen_files': []}, ensure_ascii=False))
    r_b3 = dg_run([u'build'])
    time_order_bites = (r_b3.returncode != 0) and (u'时间' in (r_b3.stdout or u''))
    dg_run([u'ack', u'--ready', u'--note', u'用户回：已就绪'])
    rc_dg, t_dg = run_check_at(dg_dir)
    declined_listed = u'G26_投放回执' in t_dg
    g26_bites = (stdout_has_card and ask_shown and no_deck and ack_refused and declined_ok
                 and note_required and ready_ok and stale_card_refused and time_order_bites)
    notes.append(u"G26 投放回执反证（真 CLI 八步）：卡片全文进 stdout=%s，缺产物无回执 build 停=%s"
                 u"（没留成品=%s），没发卡就 ack 被拒=%s，declined 放行=%s，declined 缺原话被拒=%s，"
                 u"产物齐 ready 装配=%s，改卡后旧回执被拒=%s，回执时间倒序被拒=%s，G26 入报告=%s -> %s" % (
                     stdout_has_card, ask_shown, no_deck, ack_refused, declined_ok, note_required,
                     ready_ok, stale_card_refused, time_order_bites, declined_listed,
                     u'ok' if (g26_bites and declined_listed) else u'FAIL'))
    if not g26_bites:
        fails.append(u"G26 不咬：无回执也能出片或防伪链有断点（stdout=%s 停=%s 无成品=%s ack拒=%s "
                     u"declined=%s 缺原话拒=%s ready=%s 旧卡拒=%s 时间倒序=%s）" % (
                         stdout_has_card, ask_shown, no_deck, ack_refused, declined_ok,
                         note_required, ready_ok, stale_card_refused, time_order_bites))
    if not declined_listed:
        fails.append(u"G26 没写进验收报告：豁免与缺项必须让交付说明能引用")

    # Unsplash（SPEC §14）：检索 URL 可点、没密钥不许假装能下、裸文件不许进成品
    import caofeng as _C2
    u_url = _C2.unsplash_search_url(u'clinic reception', u'black-and-white')
    u_ok = (u'https://unsplash.com/zh-cn/s/photos/' in u_url) and (
        'orientation=landscape' in u_url) and ('color=black-and-white' in u_url)
    r_un = dg_run([u'fetch', u'--source', u'unsplash', u'--term', u'1=clinic reception',
                   u'--only-page', u'1'])
    no_key_refused = (r_un.returncode != 0) and ('UNSPLASH_ACCESS_KEY' in (
        (r_un.stdout or u'') + (r_un.stderr or u'')))
    src_jpg = os.path.join(dg_dir, u'_unsplash_download.jpg')
    # 用户在 Unsplash 点 ↓「原始大小」下来的就是大图（短边 ≥1920 是 SPEC §5 的硬门），
    # 拿 1080 高的图登记会被 adopt 自己拒掉，那测的就不是通道而是尺寸。
    Image.effect_noise((2600, 1950), 45).convert('RGB').save(src_jpg, quality=90)
    r_ad = dg_run([u'fetch', u'--adopt', src_jpg, u'--page', u'1'])
    adopt_needs_url = r_ad.returncode != 0
    _ad2 = dg_run([u'fetch', u'--adopt', src_jpg, u'--page', u'1', u'--url',
                   u'https://unsplash.com/photos/abc123', u'--artist', u'Test Photog'])
    adopt_ok = (_ad2.returncode == 0) and (u'Unsplash License' in (_ad2.stdout or u''))
    unsplash_ok = (u_ok and adopt_needs_url and adopt_ok
                   and (no_key_refused or _C2.unsplash_key()))
    notes.append(u"Unsplash 通道：中文站检索 URL=%s，无密钥自动下载被拒=%s，裸文件 adopt 被拒=%s，"
                 u"带来源登记成功=%s -> %s" % (u_ok, no_key_refused, adopt_needs_url, adopt_ok,
                                              u'ok' if unsplash_ok else u'FAIL'))
    if not unsplash_ok:
        fails.append(u"Unsplash 通道没落地：URL=%s 无密钥被拒=%s 裸文件被拒=%s 登记=%s" % (
            u_ok, no_key_refused, adopt_needs_url, adopt_ok))

    # G22-G25：坏成品必须有读数，缺一条就是"文档写了代码没跑"
    rd = {}
    for key, pat in (('G23', r'"over_pages":\s*(\d+)'), ('G24', r'"missing":\s*(\d+)'),
                     ('G25', r'"bad_tables":\s*(\d+)'), ('G22', r'"score":\s*([0-9.]+)')):
        m = re.search(pat, t_dg)
        rd[key] = m.group(1) if m else None
    small_skip = ('not_applicable' in t_dg) or (
        u'同级分' in t_dg and u'页' in t_dg and rd['G22'] is not None)
    read_ok = all(rd[k] is not None for k in ('G23', 'G24', 'G25', 'G22'))
    notes.append(u"G22-G25 读数（真 CLI 现装的那册）：G22=%s G23 超预算页=%s G24 缺事实=%s "
                 u"G25 坏表=%s 小册同级分豁免=%s -> %s" % (
                     rd['G22'], rd['G23'], rd['G24'], rd['G25'], small_skip,
                     u'ok' if read_ok else u'FAIL'))
    if not read_ok:
        fails.append(u"G22-G25 有闸在报告里没读数（等于没量）：%s" % rd)

    # ---------------- #50 一页一断言：hero-first + 明细进备注（v3.13.0）反向自证
    # dg 那册第一段就是四数句（12 城/268 家/214 家/79.9%），落到一个 metric 页上。
    # 装配后：① 该页要出一个 ≥60pt 主视觉大数（hero-first 没做→该页无 ≥60pt，FAIL）；
    # ② 屏幕筹码收到很少（chip 抑制没做→该页 cards 顶高）；③ 没画上的事实全进备注
    # （detail→notes 没做→facts_undrawn>0，FAIL）。任一回归即被抓。
    try:
        import deck_parity as _DP50
        _r50 = [x for x in _DP50.measure_deck(deck0) if not x['has_tbl'] and x['nums'] >= 2]
        _big50 = [x for x in _r50 if x['hero_pt'] >= 60]
        _bl50 = json.load(io.open(os.path.join(dg_dir, 'out', 'build_log.json'), encoding='utf-8'))
        _fin50 = sum(x.get('facts_in_notes', 0) for x in _bl50)
        _fun50 = sum(len(x.get('facts_undrawn', [])) for x in _bl50)
        _maxcards = max([x['cards'] for x in _big50], default=0)
        _h50 = bool(_big50) and _fun50 == 0
        notes.append(u"hero-first+detail→notes（dg 四数句页）：≥2 数页 %d 个、其中出 ≥60pt hero 的 %d 个、"
                     u"hero 页最大筹码 %d、入备注事实 %d、未画上 %d -> %s" % (
                         len(_r50), len(_big50), _maxcards, _fin50, _fun50, u'ok' if _h50 else u'FAIL'))
        if not _h50:
            fails.append(u"hero-first/明细进备注没落地：dg 的四数 metric 页没出 ≥60pt 主视觉大数，"
                         u"或仍有事实没画上（facts_undrawn>0）——一页一断言回退了")
    except Exception as _e50:
        notes.append(u"hero-first/明细进备注反证没跑成：%s" % str(_e50)[:100])
        fails.append(u"hero-first/明细进备注反证异常：%s" % str(_e50)[:80])

    # ---------------- 显示契约（每页字数那维的根子）：计划侧与装配侧必须是同一档
    import display_refine as _DR
    drift = (_DR.MAX_LINE == C.TH['display_line_max_chars'] and
             _DR.MAX_LINES == C.TH['display_lines_max'])
    old_shape = _DR.page_budget({
        'headline': u'六台合计贡献毛利',
        'display_lines': [u'单车贡献毛利 50.8 万元（不含折旧）',
                          u'回收期 1.5 年与 1.9 年之间',
                          u'当年池子是负的 138.6 万元']}          )
    new_shape = _DR.page_budget({
        'headline': u'六台合计毛利',
        'display_lines': [u'合计 281.4 万元', u'费用 420 万元']})
    shape_ok = (len(old_shape) >= 2 and not new_shape)
    notes.append(u"显示契约：DR(%d字×%d条)/TH(%d字×%d条) 同档=%s；"
                 u"旧口径那页被判 %d 项超标、新口径那页 0 项 -> %s" % (
                     _DR.MAX_LINE, _DR.MAX_LINES, C.TH['display_line_max_chars'],
                     C.TH['display_lines_max'], drift, len(old_shape),
                     u'ok' if (drift and shape_ok) else u'FAIL'))
    if not drift:
        fails.append(u"计划侧 display_refine 与装配侧 TH 的显示行上限不一致："
                     u"refine 提案合格、build 打上去仍超标（或反过来），G23 变成撞运气")
    if not shape_ok:
        fails.append(u"显示契约没真收紧：3 条 × 20 字那一页在 page_budget 下应该超标"
                     u"（v5 实测 p50=43 字、G22 每页字数那维只有 0.442 < 维下限 0.5）")
    # 窗口碎片/区间反证（目检页 217 上屏 '二·1280'）：计划侧提案与装配端都必须丢碎片、整条留区间
    _frag_pg = {'headline': u'二，套包能不能卖动',
                'body': [u'二，套包能不能卖动。三个月交付包 1280 到 1980 元每套是我们自己定的价，还没有发生过真实成交。'],
                'display_lines': [u'二·1280', u'1980 元']}
    _prop = _DR.propose_lines(_frag_pg)[0]
    _frag_ok = (u'二·1280' not in _prop and any(u'1280' in x and u'1980' in x for x in _prop))
    try:
        from pptx import Presentation as _PF
        from pptx.util import Inches as _IF
        _prf = _PF()
        _prf.slide_width, _prf.slide_height = _IF(13.333), _IF(7.5)
        _slf = _prf.slides.add_slide(_prf.slide_layouts[6])
        C.place_text(_slf, _frag_pg, {'role': 'statement', 'style': 'tech_blue'},
                     [0.08, 0.16, 0.84, 0.7], {})
        _allf = u' '.join(
            r.text for sp in _slf.shapes if sp.has_text_frame for p in sp.text_frame.paragraphs for r in p.runs)
        _frag_ok = _frag_ok and u'二·1280' not in _allf and u'1280' in _allf
    except Exception as _ef:
        notes.append(u"碎片反证装配端没跑成：%s" % str(_ef)[:100])
        _frag_ok = False
    notes.append(u"窗口碎片/区间：提案=%s 装配端无'二·1280'且1280在屏或备注=%s -> %s" % (
        _prop, _frag_ok, u'ok' if _frag_ok else u'FAIL'))
    if not _frag_ok:
        fails.append(u"窗口碎片没兜住：'二·1280' 这种章节序号粘数字不许上屏，区间事实 '1280 到 1980 元' 必须整条保留")
    import deck_parity as _DP
    if _DP.PARITY_SCORE_MIN != C.TH['parity_score_min']:
        fails.append(u"同级分下限两套口径：G22 判 %s、deck_parity 单独跑报 %s"
                     u"——同一个指标不能让两个人读出不一样的结论" % (
                         C.TH['parity_score_min'], _DP.PARITY_SCORE_MIN))

    # ---------------- 非模版元素不学习沉淀（2026-11-11 用户指令）：parity 基线只用好示范页校准
    # 五件反证：① 基线自报 good_pages_only 且真剔了坏页/junk 册 ② per_deck 不许混进 junk 册
    # ③ 同一册剔页前后页数差==坏页数（过滤真咬，不是挂个名） ④ 缺台账时 calibrate 拒绝退回含污染抽样
    # ⑤ 尺子仍分得开好坏（好示范达标/junk 不达标）。放松尺子凑分会被 ②③⑤ 一起咬住。
    import deck_parity as _DPc
    _cal_ok = _junk_ok = _excl_ok = _refuse_ok = _disc_ok = False
    _cal_note = u''
    try:
        _b = C.kb('parity_baseline.json')
        _g = C.kb('exemplar_grades.json')
        _bad = {}
        for _row in (_g.get('page_index') or []):
            _bad.setdefault(_row[0], set()).add(int(_row[1]))
        _cal_ok = (_b.get('calibration') == 'good_pages_only'
                   and _b.get('excluded_bad_pages', 0) > 0
                   and _b.get('skipped_junk_decks', 0) > 0)
        import corpus_path as _CPc
        _srcc = _CPc.corpus_dir()
        if _srcc and os.path.isdir(_srcc):
            _junk_in, _excl_bad = [], []
            for _r in (_b.get('per_deck') or []):
                _bn = _r['deck']
                _p = os.path.join(_srcc, _bn)
                if not os.path.exists(_p):
                    continue
                _tot = _DPc._slide_count(_p)
                _nb = len(_bad.get(_bn, set()))
                if _tot and _nb / float(_tot) >= 0.5:
                    _junk_in.append(_bn)
                if len(_DPc.measure_deck(_p)) - len(_DPc.measure_deck(_p, bad_pages=_bad.get(_bn, set()))) != _nb:
                    _excl_bad.append(_bn)
            _junk_ok = not _junk_in
            _excl_ok = not _excl_bad
            _good_pass = _junk_fail = None
            for _fn in sorted(os.listdir(_srcc)):
                if not _fn.lower().endswith('.pptx'):
                    continue
                _p = os.path.join(_srcc, _fn)
                _tot = _DPc._slide_count(_p)
                _nb = len(_bad.get(_fn, set()))
                _isgood = bool(_tot) and _nb / float(_tot) < 0.5 and (_tot - _nb) >= 3
                try:
                    _sc = _DPc.score(_p).get('score')
                except Exception:
                    _sc = None
                if _sc is None:
                    continue
                if _isgood and _good_pass is None and _sc >= _DPc.PARITY_SCORE_MIN:
                    _good_pass = (_fn[:24], _sc)
                if (not _isgood) and _junk_fail is None and _sc < _DPc.PARITY_SCORE_MIN:
                    _junk_fail = (_fn[:24], _sc)
                if _good_pass and _junk_fail:
                    break
            _disc_ok = bool(_good_pass and _junk_fail)
            _cal_note = u"好示范达标例=%s junk不达标例=%s" % (
                json.dumps(_good_pass, ensure_ascii=False), json.dumps(_junk_fail, ensure_ascii=False))
        else:
            _junk_ok = _excl_ok = _disc_ok = True
            _cal_note = u'语料目录不在，跳过 junk/剔页/判别三项（结构件仍验）'
        _orig = _DPc._bad_page_index
        try:
            _DPc._bad_page_index = lambda: None
            try:
                _DPc.calibrate(out=os.path.join(TMP, '_parity_should_not_exist.json'))
                _refuse_ok = False
            except SystemExit as _se:
                _refuse_ok = (u'非模版' in str(_se)) or ('exemplar_grades' in str(_se))
        finally:
            _DPc._bad_page_index = _orig
    except Exception as _ec:
        _cal_note = u'基线自证没跑成：%s' % str(_ec)[:120]
    _cal_all = _cal_ok and _junk_ok and _excl_ok and _refuse_ok and _disc_ok
    notes.append(u"非模版元素不学习沉淀：基线good_pages_only且剔坏页/junk=%s per_deck无junk=%s "
                 u"剔页差==坏页数=%s 缺台账拒绝=%s 好坏仍分开=%s（%s）-> %s" % (
                     _cal_ok, _junk_ok, _excl_ok, _refuse_ok, _disc_ok, _cal_note,
                     u'ok' if _cal_all else u'FAIL'))
    if not _cal_all:
        fails.append(u"parity 基线没把非模版元素剔干净（或尺子失去判别力）：good_only=%s 无junk=%s "
                     u"剔页咬=%s 拒绝退回=%s 判别=%s —— 用户指令'非模版元素不要学习沉淀'没落地" % (
                         _cal_ok, _junk_ok, _excl_ok, _refuse_ok, _disc_ok))

    # ---------------- 提案本身不许产出半截词与"数字·数字"（探针页 5 实测）
    dense = {'headline': u'六台合计贡献毛利 281.4 万元',
             'content_type': 'metric',
             'body': [u'六台合计贡献毛利 281.4 万元，覆盖 12 城 268 家机构，实际履约 214 家']}
    dl_p, ct_p, need_p = _DR.propose_lines(dense)
    tail_frag = [l for l in dl_p if re.match(u'^[一-龥]\\s*\\d', l)]
    dd_pack = [l for l in dl_p if u'·' in l and
               all(re.search(r'\d', seg) for seg in l.split(u'·'))]
    over = [l for l in dl_p if len(l) > _DR.MAX_LINE]
    prop_ok = bool(dl_p) and not tail_frag and not dd_pack and not over
    notes.append(u"提案自检（密集事实页）：%s ｜ 半截词 %d 条、数字·数字 %d 条、超 %d 字 %d 条 -> %s" % (
        json.dumps(dl_p, ensure_ascii=False)[:78], len(tail_frag), len(dd_pack),
        _DR.MAX_LINE, len(over), u'ok' if prop_ok else u'FAIL'))
    if not prop_ok:
        fails.append(u"提案产出了不能上屏的行：窗口裁下来的半截词（'利 281.4 万元'）、"
                     u"'281.4 万元·268' 这种数字拼数字，或超出单行上限")

    # ---------------- 事实这把尺子只能有一把：G24 逐条核成品，提案/分页就必须数同一批事实
    # 实测坑（2026-11-07 自测全绿但成品少 3 个事实）：G24 数"数字+专名"，
    # 提案装箱只数数字，分页预算也只数数字，于是一页"79.9% / 412 家 / 96 家 / 曾合作医美"
    # 被引擎当成合格，成品上 '96' 和 '曾合作医美' 双双没影。
    SRC2 = (u'2026年公司在华东 12 城签约 268 家医美机构，实际履约 214 家，履约率 79.9%。'
            u'第二，把曾合作医美的中小门店纳入潜在池，共 412 家，转化 96 家。')
    ents2 = C.extract_entities([{'text': SRC2, 'level': 0}], [], [])
    seg_after = C._func_split(u'把曾合作医美的中小门店纳入潜在池')
    frag_back = [s for s in seg_after if s == u'中小门店纳入潜']
    unit_cov = _DR.fact_units(SRC2, ents2)
    # 碎片专名（'中小门店纳入潜'）会被 G24 当成"必须上屏的事实"，逼着装配体把原文整段贴上去：
    # 那既是假指标（整段贴也能过），也是坏判据（没人会往屏幕上写这串字）
    no_frag = (u'中小门店纳入潜' not in ents2 and u'城签约' not in ents2 and not frag_back
               and u'中小门店纳入潜在池' in seg_after)
    yard_single = (u'%|％|万元|亿元' not in io.open(engine, encoding='utf-8').read().split(
        'def g24_fact_coverage')[1].split('def ')[0])
    import docx as _DX
    notes.append(u"专名抽取与事实尺子：entities=%s（词内碎片 %d 个）；"
                 u"G24 与提案共用 DR.fact_units=%s，一页事实单元 %d 个 -> %s" % (
                     json.dumps(ents2, ensure_ascii=False)[:70], len(frag_back), yard_single,
                     len(unit_cov), u'ok' if (no_frag and yard_single and len(unit_cov) >= 8) else u'FAIL'))
    if not no_frag:
        fails.append(u"专名抽取还在制造词内碎片（'中小门店纳入潜'/'城签约' 这类砍在词中间的串）："
                     u"G24 会要求屏幕上出现没人会写的字")
    if not yard_single:
        fails.append(u"G24 又自己写了一份数字单位正则：事实单元必须只有 DR.fact_units 这一把尺子")
    if len(unit_cov) < 8:
        fails.append(u"DR.fact_units 漏数事实（这句里至少有 7 个数字和 3 个专名，只读到 %d 个）"
                     % len(unit_cov))

    _s2 = SRC2.split(u'。')[1] + u'。'
    pg_dense = {'headline': u'履约率 79.9%', 'content_type': 'metric', 'role': 'data',
                'entities': [e for e in ents2 if e in _s2],
                'body': [u'履约率 79.9%。', _s2]}
    dl_d, ct_d, need_d = _DR.propose_lines(pg_dense)
    _on_screen = u''.join([pg_dense['headline']] + list(dl_d))
    uncovered = [u for u in _DR.fact_units(u''.join(pg_dense['body']), pg_dense['entities'])
                 if u not in _on_screen]
    honest = (not uncovered) or bool(pg_dense.get('_unpacked_facts'))
    pages_after, n_split = C.split_by_facts([dict(pg_dense, page=1)], ents2)
    still_bad = []
    for q in pages_after:
        dl_q, _, _ = _DR.propose_lines(q)
        scr = u''.join([q['headline']] + list(dl_q))
        still_bad += [u for u in _DR.fact_units(u''.join(q.get('body') or []), ents2)
                      if u not in scr] if dl_q and not q.get('_unpacked_facts') else []
    notes.append(u"专名进装箱与拆页：那页装箱后屏幕文字 %s，没装下的 %s（如实记账=%s）；"
                 u"split_by_facts 拆成 %d 页，拆完仍漏事实 %d 个 -> %s" % (
                     json.dumps(dl_d, ensure_ascii=False)[:60],
                     json.dumps(uncovered, ensure_ascii=False)[:40], honest, len(pages_after),
                     len(still_bad),
                     u'ok' if (honest and len(pages_after) == 2 and not still_bad) else u'FAIL'))
    if not honest:
        fails.append(u"提案装箱漏掉专名却不当成 unpacked（G24 会在成品里点名，屏幕上少一个专名）")
    if len(pages_after) != 2:
        fails.append(u"一页 4 个事实单元装不下却没拆页（拆出 %d 页）——"
                     u"上一版的做法是把装不下的 '96' 和 '曾合作医美' 静默丢掉" % len(pages_after))
    if still_bad:
        fails.append(u"拆完的页仍然漏事实：%s" % json.dumps(still_bad[:3], ensure_ascii=False))

    # ---------------- 大数字不许和同一页的其他文字重影（实测页 3 打了两遍 '2026'）
    hero_dir = os.path.join(TMP, 'hero_dup')
    shutil.rmtree(hero_dir, ignore_errors=True)
    os.makedirs(hero_dir)
    _hd = os.path.join(hero_dir, 'doc.docx')
    _doc = _DX.Document()
    _doc.add_heading(u'季度经营复盘', 0)
    _doc.add_heading(u'一、渠道签约', 1)
    for _p in [x for x in SRC2.split(u'。') if x.strip()]:
        _doc.add_paragraph(_p + u'。')
    _doc.save(_hd)
    subprocess.run([sys.executable, engine, 'plan', _hd, '--work', hero_dir, '--auto-pages'],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    hb = subprocess.run([sys.executable, engine, 'build', '--work', hero_dir],
                        capture_output=True, text=True, encoding='utf8', errors='replace')
    _hpptx = None
    for _f in os.listdir(os.path.join(hero_dir, 'out')):
        if _f.endswith('.pptx'):
            _hpptx = os.path.join(hero_dir, 'out', _f)
    dup_boxes = []
    if _hpptx:
        by_page = {}
        for si, nm, raw, ntxt in C.pptx_boxes(_hpptx):
            by_page.setdefault(si, []).append((nm or '', raw, ntxt))
        for si, items in by_page.items():
            for nm, raw, ntxt in items:
                if nm != 'hero_num' or not ntxt:
                    continue
                for nm2, raw2, ntxt2 in items:
                    if nm2 != 'hero_num' and ntxt2 and ntxt in ntxt2:
                        dup_boxes.append({'page': si, 'hero': raw, 'also_in': nm2})
    notes.append(u"大数字重影（hero_num 与同页其他框同串字）：build rc=%d，命中 %d 处 %s -> %s" % (
        hb.returncode, len(dup_boxes),
        json.dumps(dup_boxes[:2], ensure_ascii=False)[:110],
        u'ok' if (hb.returncode == 0 and not dup_boxes) else u'FAIL'))
    if hb.returncode != 0:
        fails.append(u"hero 重影用例没装成型（build rc=%d）：%s" % (
            hb.returncode, (hb.stdout or '')[-160:]))
    if dup_boxes:
        fails.append(u"超大数字和同一页的标题/正文重复了同一串字（页%s）："
                     u"主视觉只能取正文里的数，标题自己就是大字" % (
                         dup_boxes[0]['page'],))

    # ---------------- 并列数值要画成柱子，而且不许一页打两遍同一个数（图形化率那维的根子）
    sd_dir = os.path.join(TMP, 'series_deck')
    shutil.rmtree(sd_dir, ignore_errors=True)
    os.makedirs(sd_dir)
    _sd = os.path.join(sd_dir, 'doc.docx')
    _sdoc = _DX.Document()
    _sdoc.add_heading(u'华东网络经营复盘', 0)
    _sdoc.add_paragraph(u'一、网络')
    for _p in [u'2025年公司签约 12 城，杭州 9 家、宁波 7 家、温州 4 家、绍兴 3 家。',
               u'单店月均出货 38 万元，杭州 52 万元、宁波 41 万元、温州 29 万元。',
               u'第一季度投放 6 台、第二季度 11 台、第三季度 14 台、第四季度 18 台。']:
        _sdoc.add_paragraph(_p)
    _sdoc.save(_sd)
    sp2 = subprocess.run([sys.executable, engine, 'plan', _sd, '--work', sd_dir, '--auto-pages'],
                         capture_output=True, text=True, encoding='utf8', errors='replace')
    sb2 = subprocess.run([sys.executable, engine, 'build', '--work', sd_dir],
                         capture_output=True, text=True, encoding='utf8', errors='replace')
    _spptx = os.path.join(sd_dir, 'out', [f for f in os.listdir(os.path.join(sd_dir, 'out'))
                                         if f.endswith('.pptx')][0])
    import deck_parity as _DPX
    _m2 = _DPX.measure_one(_spptx) or {}
    _blog2 = json.load(io.open(os.path.join(sd_dir, 'out', 'build_log.json'), encoding='utf-8'))
    n_bars = sum(1 for x in _blog2 if x.get('data_layout') == 'bars')
    # 同一页出现两遍同一个数（'52万元' 既在标题又在条顶）就是重影：数一遍每个页上的数字 token
    _NUMTOK_T = re.compile(u'(?:\\d[\\d,\\.]*\\s*(?:%|％|万元|亿元|万|亿|元|台|例|人|次|天|城|店|个|倍|家))')
    repeat_pages = []
    for si in sorted({b[0] for b in C.pptx_boxes(_spptx)}):
        toks = []
        for _s, _nm, _raw, _nt in C.pptx_boxes(_spptx):
            if _s != si:
                continue
            for m in _NUMTOK_T.finditer(_raw or u''):
                toks.append(re.sub(r'\s', u'', m.group(0)))
        dbl = [x for x in set(toks) if toks.count(x) >= 2]
        if dbl:
            repeat_pages.append({'page': si, 'twice': dbl[:3]})
    over2 = subprocess.run([sys.executable, engine, 'check', '--work', sd_dir],
                           capture_output=True, text=True, encoding='utf8', errors='replace')
    _st = io.open(os.path.join(sd_dir, 'report', u'验收报告.md'), encoding='utf-8').read()
    _g24sd = re.search(r'G24_事实覆盖[\s\S]{0,400}?"missing":\s*(\d+)', _st)
    series_ok = (sp2.returncode == 0 and sb2.returncode == 0 and n_bars >= 2
                 and (_m2.get('graphic_rate') or 0) >= 0.16 and not repeat_pages
                 and _g24sd and _g24sd.group(1) == '0')
    notes.append(u"并列数值画成柱子（语料那种条形组）：%d 页画了柱子，图形化率=%s（引擎画柱下限 0.16），"
                 u"同页重影数字 %d 页，G24 缺失=%s，check rc=%d -> %s" % (
                     n_bars, _m2.get('graphic_rate'), len(repeat_pages),
                     _g24sd.group(1) if _g24sd else u'无读数', over2.returncode,
                     u'ok' if series_ok else u'FAIL'))
    if sp2.returncode != 0 or sb2.returncode != 0:
        fails.append(u"条形组用例没跑成（plan rc=%d build rc=%d）：%s" % (
            sp2.returncode, sb2.returncode, (sb2.stdout or '')[-160:]))
    if n_bars < 2 or (_m2.get('graphic_rate') or 0) < 0.16:
        fails.append(u"三句都是四值并列系列，却只画了 %d 页柱子（图形化率 %s < 引擎画柱下限 0.16）："
                     u"上一版按数字逐个算预算，把一组柱子从中间拆成四页，条形组条件再也不成立" % (
                         n_bars, _m2.get('graphic_rate')))
    if repeat_pages:
        fails.append(u"同一页把同一个数打了两遍（%s）：标题让位给柱子时只剥数字，"
                     u"柱顶已有的数别再出现在正文行里" % json.dumps(repeat_pages[:2], ensure_ascii=False))
    if not (_g24sd and _g24sd.group(1) == '0'):
        fails.append(u"改画条形组后 G24 出现事实缺失（%s）——图形只是载体，一个数字都不许少" % (
            _g24sd.group(1) if _g24sd else u'报告没读数',))

    # 窗口碎片：key_numbers 的 context 是从数字左边固定字数裁的，
    # 实测上屏过 '年公司在华东12'（'2026年公司在华东 12 城' 的中段），
    # 它是原文的真字所以 G1 不叫、它够短所以 G23 不叫，只有版面上看得出来。
    _frag_hits = []
    for _dp in (hero_dir, sd_dir):
        _dol = os.path.join(_dp, 'outline.json')
        if not os.path.exists(_dol):
            continue
        _dtx = C.norm_content(u' '.join(C.blocks_of(json.load(io.open(_dol, encoding='utf-8'))['docx'])))
        for _bx in C.pptx_boxes(os.path.join(_dp, 'out', [f for f in os.listdir(os.path.join(_dp, 'out'))
                                                         if f.endswith('.pptx')][0])):
            _si, _nm, _raw, _nt = _bx
            if _nm in ('chapter_no', 'bar_label_0', 'bar_value_0') or len(_nt) < 4:
                continue
            if _nt[0] not in C.MEASURE_PREFIX:
                continue
            _k = _dtx.find(_nt)
            if _k > 0 and re.search(r'\d\s*$', _dtx[:_k]):
                _frag_hits.append({'page': _si, 'text': _raw[:22]})
    notes.append(u"窗口碎片（文本框以量词开头、而原文里它前面就是一个数字）：%d 处 %s -> %s" % (
        len(_frag_hits), json.dumps(_frag_hits[:2], ensure_ascii=False)[:90],
        u'ok' if not _frag_hits else u'FAIL'))
    if _frag_hits:
        fails.append(u"屏幕上还有从数字后面裁下来的半截话（%s）——"
                     u"装箱只能用整条子句或数字本身，不能用固定窗口" % (
                         json.dumps(_frag_hits[:2], ensure_ascii=False),))

    # 标签只能是原文里连续的一段字：把数字抹掉后不许把两侧文字接起来
    _lab_src = (u'现场另向 30 位“三八红旗手”义诊队伍全年服务。'
                u'单车年收入 469 万元，成本 288 万元。创造 1,200 个岗位。')
    _lab_cases = [u'现场另向 30 位“三八红旗手”义诊队伍全年服务',
                  u'单车年收入 469 万元', u'成本 288 万元', u'创造 1,200 个岗位']
    _bad_lab = []
    for _c in _lab_cases:
        _l = _DL2.clean_label(_c) if '_DL2' in dir() else __import__('data_layout').clean_label(_c)
        if not _l:
            continue
        if _l not in _lab_src:
            _bad_lab.append({'case': _c[:16], 'label': _l})
    notes.append(u"标签取原文连续段（不粘数字两侧）：%s -> %s" % (
        json.dumps([__import__('data_layout').clean_label(x) for x in _lab_cases],
                   ensure_ascii=False)[:96],
        u'ok' if not _bad_lab else u'FAIL'))
    if _bad_lab:
        fails.append(u"柱底标签又拼出原文没有的串（%s）——屏幕上就是谁都没写过的一句话，G1 会判编造" % (
            json.dumps(_bad_lab[:2], ensure_ascii=False),))

    # ---------------- pickup：一页一轮的回收（用户只管下载，路径跟着项目走）
    pk_dir = os.path.join(TMP, 'pickup_proj')
    shutil.rmtree(pk_dir, ignore_errors=True)
    os.makedirs(os.path.join(pk_dir, 'work'))
    _doc_p = os.path.join(pk_dir, 'doc.docx')
    _pkd = _DX.Document()
    _pkd.add_heading(u'素材夹测试', 0)
    _pkd.add_paragraph(u'这一页要一张 16:9 的背景图，正文 2026 年投放 6 台车。')
    _pkd.save(_doc_p)
    subprocess.run([sys.executable, engine, 'plan', _doc_p, '--work',
                    os.path.join(pk_dir, 'work'), '--auto-pages'],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    _ol_pk = json.load(io.open(os.path.join(pk_dir, 'work', 'outline.json'), encoding='utf-8'))
    adir = _ol_pk.get('assets_dir') or ''
    inbox = os.path.join(adir, u'收件') if adir else ''
    made = adir and os.path.isdir(adir) and os.path.isdir(inbox)
    from PIL import Image as _IM
    _hi = os.path.join(inbox, u'jimeng_hi.png') if inbox else ''
    _lo = os.path.join(inbox, u'jimeng_lo.png') if inbox else ''
    if inbox:
        # 噪点图保证 PNG 字节数过 gen_image_bytes_min（纯色图压缩后 <15KB，会被产物扫描
        # 判成"不在"，队列反证的前提就塌了）
        _IM.effect_noise((2048, 1152), 60).convert('RGB').save(_hi)
        _IM.new('RGB', (900, 506), (0, 0, 0)).save(_lo)
    _wp = os.path.join(pk_dir, 'work')
    # 沙箱隔离：pickup 会兜底扫系统下载文件夹，用户 Downloads 里恰好有当天真实产物时
    # 会把外册文件认进沙箱（"同一文件不重复认领"被顶穿）。把 HOME/USERPROFILE 指进沙箱，
    # 下载文件夹解析就落在沙箱内——测试只验自己的回收链。
    _pk_env = dict(os.environ, HOME=pk_dir, USERPROFILE=pk_dir, PUBLIC=pk_dir)
    # 页1 的卡上有没有 image 段：plan 后先 scaffold+pack
    subprocess.run([sys.executable, engine, 'scaffold', '--work', _wp],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    io.open(os.path.join(_wp, 'promptpack', 'p001_image.txt'), 'w', encoding='utf-8').write(u'x')
    r_lo = subprocess.run([sys.executable, engine, 'pickup', '--work', _wp, '--page', '1',
                           '--kind', 'image', '--src', _lo],
                          capture_output=True, text=True, encoding='utf8', errors='replace',
                          env=_pk_env)
    os.remove(_lo)
    r_hi = subprocess.run([sys.executable, engine, 'pickup', '--work', _wp, '--page', '1',
                           '--kind', 'image'],
                          capture_output=True, text=True, encoding='utf8', errors='replace',
                          env=_pk_env)
    r_re = subprocess.run([sys.executable, engine, 'pickup', '--work', _wp, '--page', '2',
                           '--kind', 'image'],
                          capture_output=True, text=True, encoding='utf8', errors='replace',
                          env=_pk_env)
    r_force = subprocess.run([sys.executable, engine, 'pickup', '--work', _wp, '--page', '2',
                              '--kind', 'image', '--src', _hi, '--force', '--min-edge', '4000'],
                             capture_output=True, text=True, encoding='utf8', errors='replace',
                             env=_pk_env)
    # ---------------- 页数契约（SPEC §21）：--pages 精确凑数+压缩台账；无契约 G23 必须拦
    bc_dir = os.path.join(TMP, 'budget_contract')
    shutil.rmtree(bc_dir, ignore_errors=True)
    os.makedirs(bc_dir)
    _bd = _DX.Document()
    _bd.add_heading(u'页数契约测试', 0)
    for _cn in (u'一、市场', u'二、模式', u'三、财务'):
        _bd.add_heading(_cn, 1)
        for _i in range(5):
            _bd.add_paragraph(u'%s 第%d条：2026 年投放 %d 台车，履约率 %d%%。' % (_cn, _i, 8 + _i, 60 + _i))
    _bdoc = os.path.join(bc_dir, 'doc.docx')
    _bd.save(_bdoc)
    bw = os.path.join(bc_dir, 'work')
    rb = subprocess.run([sys.executable, engine, 'plan', _bdoc, '--work', bw, '--pages', '10'],
                        capture_output=True, text=True, encoding='utf8', errors='replace')
    ob = json.load(io.open(os.path.join(bw, 'outline.json'), encoding='utf-8'))
    budget_exact = (rb.returncode == 0 and len(ob['pages']) == 10
                    and ob['brief']['pages'] == [10, 10])
    subprocess.run([sys.executable, engine, 'refine', '--work', bw],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    ob = json.load(io.open(os.path.join(bw, 'outline.json'), encoding='utf-8'))
    led = ob['brief'].get('ledger') or []
    _content_pages = {pg['page'] for pg in ob['pages']
                      if pg['role'] not in ('cover', 'closing', 'divider') and not pg.get('table')}
    ledger_ok = (len(led) == len(ob['pages'])
                 and all((e.get('source') or e.get('kept')) for e in led
                         if e['page'] in _content_pages))
    bw2 = os.path.join(bc_dir, 'work_nobrief')
    subprocess.run([sys.executable, engine, 'plan', _bdoc, '--work', bw2],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_nb, t_nb = run_check_at(bw2)
    nobrief_bites = (rc_nb != 0 and u'没有页数契约' in t_nb)
    rc_b, t_b = run_check_at(bw)
    brief_quiet = u'没有页数契约' not in t_b
    notes.append(u"页数契约反证：--pages 10 精确=%s，refine 台账齐=%s，无契约 G23 拦=%s，有契约不报=%s -> %s" % (
        budget_exact, ledger_ok, nobrief_bites, brief_quiet,
        'ok' if (budget_exact and ledger_ok and nobrief_bites and brief_quiet) else 'FAIL'))
    if not (budget_exact and ledger_ok and nobrief_bites and brief_quiet):
        # 分不清"判据没咬住"还是"这一步根本没跑成"时，这条反证会伪装成真缺陷（老账）：
        # 把退出码与报告字节数一起打出来，报告为空 = check 根本没写出报告。
        _rep_nb = os.path.join(bw2, 'report', u'验收报告.md')
        fails.append(u"页数契约不咬或误伤（精确=%s 台账=%s 无契约拦=%s 有契约安静=%s；"
                     u"无契约那次 check 退出码=%s、报告存在=%s、报告 %d 字节）"
                     % (budget_exact, ledger_ok, nobrief_bites, brief_quiet, rc_nb,
                        os.path.exists(_rep_nb),
                        os.path.getsize(_rep_nb) if os.path.exists(_rep_nb) else 0))
    # G23 数据页定标反证（SPEC §7.1 page_chars_max_data）：普通页 64、数据/卡片页 112。
    # 实测语料 105 张数据页 p50=68——用 64 会把语料自己的 69.5% 好页判掉，本条防回退成误伤。
    _cap_prose = C.g23_display_cap(C.TH, False)
    _cap_data = C.g23_display_cap(C.TH, True)
    _cap_ok = (_cap_prose == C.TH['page_chars_max_display']
               and _cap_data == C.TH.get('page_chars_max_data', 112)
               and _cap_data > _cap_prose)
    notes.append(u"G23 数据页定标：普通页上限=%s、数据页上限=%s、数据页更宽=%s -> %s" % (
        _cap_prose, _cap_data, _cap_data > _cap_prose, 'ok' if _cap_ok else 'FAIL'))
    if not _cap_ok:
        fails.append(u"G23 数据页定标没接上（prose=%s data=%s）" % (_cap_prose, _cap_data))
    landed = os.path.exists(os.path.join(_wp, 'gen', 'p001', 'image.png'))
    keepcopy = os.path.isdir(os.path.join(adir, 'AI产物', 'p001')) if adir else False
    pk_ok = (made and r_lo.returncode != 0 and u'900x506' in (r_lo.stdout + r_lo.stderr)
             and r_hi.returncode == 0 and landed and keepcopy and r_re.returncode != 0
             and r_force.returncode == 0)
    notes.append(u"pickup 一页一轮回收：素材夹=%s（跟文档走=%s，收件夹在 %s）；"
                 u"低清被拒=%s（报 900x506=%s）｜高清认进 gen=%s｜AI产物留档=%s｜"
                 u"同一文件不重复认领=%s｜--force 越线收下=%s -> %s" % (
                     bool(adir), adir.startswith(pk_dir), bool(inbox),
                     r_lo.returncode != 0, u'900x506' in (r_lo.stdout + r_lo.stderr),
                     landed, keepcopy, r_re.returncode != 0, r_force.returncode == 0,
                     u'ok' if pk_ok else u'FAIL'))
    if not pk_ok:
        fails.append(u"pickup 的回收链不完整：素材夹必须跟着 Word 走（不写死、不落 C 盘），"
                     u"低清要拒、高清要认进 gen/pNNN 并在 PPT素材\AI产物 留档、"
                     u"已认领的文件不许被下一页抢走")

    # --force 留痕反证：知情收下低清必须写 lowres_force.json，且 check 报告里有显式声明，
    # 不许静默混过验收（r_force 用 --min-edge 4000 收了 2048x1152 的图进页2）
    subprocess.run([sys.executable, engine, 'build', '--work', _wp],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    subprocess.run([sys.executable, engine, 'check', '--work', _wp],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    _rep_p = os.path.join(_wp, 'report', u'验收报告.md')
    _rep_txt = io.open(_rep_p, encoding='utf-8').read() if os.path.exists(_rep_p) else ''
    _lr_ok = (os.path.exists(os.path.join(_wp, 'gen', 'p002', 'lowres_force.json'))
              and u'低清强制收下声明' in _rep_txt)
    notes.append(u"--force 低清留痕：gen/p002/lowres_force.json=%s、报告显式声明=%s -> %s" % (
        os.path.exists(os.path.join(_wp, 'gen', 'p002', 'lowres_force.json')),
        u'低清强制收下声明' in _rep_txt, u'ok' if _lr_ok else u'FAIL'))
    if not _lr_ok:
        fails.append(u"--force 收低清不留痕：必须写 lowres_force.json 且验收报告显式声明，不许静默通过")

    # 命名反证（用户点名要防"图片视频一多认错页"）：
    # ① 两个未命名候选 → 必须拒收并列出文件名，不许猜；② 页号命名优先于更新的未命名文件
    multi_refused = named_won = False
    if inbox:
        import hashlib as _hl
        import time as _tm
        _n1 = os.path.join(inbox, u'随便的名字A.png')
        _n2 = os.path.join(inbox, u'随便的名字B.png')
        _IM.effect_noise((2048, 1152), 50).convert('RGB').save(_n1)
        _IM.effect_noise((2048, 1152), 70).convert('RGB').save(_n2)
        r_multi = subprocess.run([sys.executable, engine, 'pickup', '--work', _wp, '--page', '2',
                                  '--kind', 'image'],
                                 capture_output=True, text=True, encoding='utf8', errors='replace',
                                 env=_pk_env)
        multi_refused = (r_multi.returncode != 0
                         and u'随便的名字A.png' in (r_multi.stdout + r_multi.stderr))
        _named = os.path.join(inbox, u'p002_image.png')
        _IM.effect_noise((2048, 1152), 90).convert('RGB').save(_named)
        os.utime(_named, (_tm.time() - 3600, _tm.time() - 3600))   # 故意比未命名的旧
        r_named = subprocess.run([sys.executable, engine, 'pickup', '--work', _wp, '--page', '2',
                                  '--kind', 'image'],
                                 capture_output=True, text=True, encoding='utf8', errors='replace',
                                 env=_pk_env)
        _g2 = os.path.join(_wp, 'gen', 'p002', 'image.png')
        named_won = (r_named.returncode == 0 and os.path.exists(_g2)
                     and _hl.sha1(io.open(_g2, 'rb').read()).hexdigest()
                     == _hl.sha1(io.open(_named, 'rb').read()).hexdigest())
        for _f in (_n1, _n2, _named):
            try:
                os.remove(_f)
            except Exception:
                pass
    notes.append(u"pickup 命名反证：多候选无命名拒收并点名=%s，页号命名优先于更新的未命名件=%s -> %s" % (
        multi_refused, named_won, 'ok' if (multi_refused and named_won) else 'FAIL'))
    if not (multi_refused and named_won):
        fails.append(u"pickup 会认错页：多候选无命名没拒收，或页号命名没优先（拒收=%s 命名优先=%s）"
                     % (multi_refused, named_won))

    # ---------------- 取图贴合度闭环（SPEC §22）：画面质检拒带字/历史画 + 目检留痕闸
    from PIL import Image as _IMQ, ImageDraw as _IDQ, ImageFont as _IFQ
    pq_dir = os.path.join(TMP, 'photo_quality')
    os.makedirs(pq_dir, exist_ok=True)
    clean_p = os.path.join(pq_dir, 'clean.png')
    _IMQ.effect_noise((800, 450), 40).convert('RGB').save(clean_p)
    text_p = os.path.join(pq_dir, 'text.png')
    _imq = _IMQ.new('RGB', (800, 450), (20, 30, 50))
    _drq = _IDQ.Draw(_imq)
    for _i in range(6):
        _drq.text((40 + _i * 120, 200), 'SALE 50%', fill=(230, 230, 230))
    _imq.save(text_p)
    qr_text = C.photo_quality_reason(text_p, 'shop poster sale', ['shop street'])
    qr_clean = C.photo_quality_reason(clean_p, 'city street night', ['city street'])
    qr_hist = C.photo_quality_reason(clean_p, 'Historic engraving of ward', ['ambulance interior'])
    notes.append(u"画面质检：带字图拒=%s、干净图放行=%s、历史画拒=%s -> %s" % (
        bool(qr_text), qr_clean is None, bool(qr_hist),
        'ok' if (qr_text and qr_clean is None and qr_hist) else 'FAIL'))
    if not (qr_text and qr_clean is None and qr_hist):
        fails.append(u"photo_quality_reason 不咬或误伤（带字=%s 干净=%s 历史=%s）" % (
            qr_text, qr_clean, qr_hist))
    # 目检留痕闸：manifest 有素材但没 review.json → G3 拦；记 keep + 联系表后放行
    _mq = os.path.join(_wp, 'materials')
    os.makedirs(_mq, exist_ok=True)
    _mf = os.path.join(_wp, 'gen', 'p002', 'image.png')
    io.open(os.path.join(_mq, 'manifest.jsonl'), 'w', encoding='utf-8').write(
        json.dumps({'page': 2, 'file': _mf, 'sha1': hashlib.sha1(io.open(_mf, 'rb').read()).hexdigest(),
                    'bytes': os.path.getsize(_mf), 'url': 'https://example.org/x.jpg',
                    'license': 'CC0', 'w': 2048, 'h': 1152, 'relevance': 80}) + chr(10))
    rc_rv1, t_rv1 = run_check_at(_wp)
    review_bites = u'目检留痕' in t_rv1
    json.dump({'2': {'verdict': 'keep', 'note': '目检对题', 'at': C.now()}},
              io.open(os.path.join(_mq, 'review.json'), 'w', encoding='utf-8'))
    _IMQ.new('RGB', (10, 10)).save(os.path.join(_mq, 'review_sheet.png'))
    rc_rv2, t_rv2 = run_check_at(_wp)
    review_gone = u'目检留痕' not in t_rv2
    notes.append(u"目检留痕闸：无 review.json 拦=%s、记 keep+联系表后放行=%s -> %s" % (
        review_bites, review_gone, 'ok' if (review_bites and review_gone) else 'FAIL'))
    if not (review_bites and review_gone):
        fails.append(u"G3 目检留痕闸不咬或误伤（拦=%s 放行=%s）" % (review_bites, review_gone))

    # ---------------- 联网视频水印零容忍（SPEC §23）：烧字视频拒、干净视频放行
    import subprocess as _spv
    vv_dir = os.path.join(TMP, 'video_wm')
    os.makedirs(os.path.join(vv_dir, 'frames'), exist_ok=True)
    _ffv = C._ff_bin()
    def _mkvid(name, burn):
        import numpy as np
        fr_dir = os.path.join(vv_dir, 'frames', name)
        os.makedirs(fr_dir, exist_ok=True)
        for i in range(24):
            # 平滑暗底（真实 footage 是平滑的）：纯噪声会把文字掩膜整片点亮成一个连通域，
            # 角标判据的"同一基线 >=2 块"就永远不成立——夹具必须像真画面
            gv = np.linspace(18, 46, 360).astype(np.uint8)
            bgv = np.repeat(gv[:, None], 640, axis=1)
            imv = Image.fromarray(np.stack([bgv // 3, bgv // 2, bgv], axis=-1).astype(np.uint8))
            if burn:
                dv = ImageDraw.Draw(imv)
                try:
                    _fnt = _IFQ.truetype('C:/Windows/Fonts/arial.ttf', 28)
                except Exception:
                    _fnt = _IFQ.load_default()
                dv.text((430, 318), 'AI GENERATED', fill=(230, 230, 230), font=_fnt)
            imv.save(os.path.join(fr_dir, 'f%03d.png' % i))
        outv = os.path.join(vv_dir, name + '.mp4')
        _spv.run([_ffv, '-v', 'error', '-y', '-framerate', '12', '-i',
                  os.path.join(fr_dir, 'f%03d.png'), '-pix_fmt', 'yuv420p', '-crf', '20', outv],
                 capture_output=True)
        return outv
    v_burn = _mkvid('burn', True)
    v_clean = _mkvid('clean', False)
    wr_burn = C.video_watermark_reason(v_burn)
    wr_clean = C.video_watermark_reason(v_clean)
    notes.append(u"联网视频水印零容忍：烧字视频拒=%s、干净视频放行=%s -> %s" % (
        bool(wr_burn), wr_clean is None, 'ok' if (wr_burn and wr_clean is None) else 'FAIL'))
    if not (wr_burn and wr_clean is None):
        fails.append(u"video_watermark_reason 不咬或误伤（烧字=%s 干净=%s）" % (wr_burn, wr_clean))

    # ---------------- 背景美学闸（SPEC §24）：黄杂纪实拒、蓝 glow 舞台放行
    st_dir = os.path.join(TMP, 'style_gate')
    os.makedirs(st_dir, exist_ok=True)
    import numpy as _nps
    yy, xx = _nps.mgrid[0:180, 0:320]
    glow = _nps.stack([_nps.full((180, 320), 12), (30 + 40 * _nps.exp(-((xx - 160) ** 2 + (yy - 90) ** 2) / 9000.0)),
                       (60 + 90 * _nps.exp(-((xx - 160) ** 2 + (yy - 90) ** 2) / 9000.0))], axis=-1)
    Image.fromarray(glow.astype(_nps.uint8)).save(os.path.join(st_dir, 'glow.png'))
    busy = _nps.zeros((180, 320, 3), dtype=_nps.uint8)
    busy[:, :, 0] = 200
    busy[:, :, 1] = 170
    busy[::4, ::4] = (40, 40, 40)
    busy[:, 150:170] = (230, 210, 40)
    Image.fromarray(busy).save(os.path.join(st_dir, 'busy.png'))
    sr_busy = C.photo_style_reason(os.path.join(st_dir, 'busy.png'), 'tech_blue')
    sr_glow = C.photo_style_reason(os.path.join(st_dir, 'glow.png'), 'tech_blue')
    notes.append(u"背景美学闸：黄杂纪实拒=%s、蓝 glow 放行=%s -> %s" % (
        bool(sr_busy), sr_glow is None, 'ok' if (sr_busy and sr_glow is None) else 'FAIL'))
    if not (sr_busy and sr_glow is None):
        fails.append(u"photo_style_reason 不咬或误伤（黄杂=%s 蓝glow=%s）" % (sr_busy, sr_glow))

    # 美学闸的两条"静默失效"防线（2026-11-13 干净上下文审计抓出来）：
    # ① 色相窗的键必须是 corpus_tables.PALETTES 的真色板 id——旧键 'neon'/'white_biz'/'warm_gold'
    #    三个都不存在，`.get(style, 蓝窗)` 让另外三种风格被拿 tech_blue 的尺子判，且不报错；
    # ② 闸不许依赖没声明的包——旧写法 import matplotlib（不在 requirements.txt）而整个函数包在
    #    except 里 return None，等于"没装 matplotlib 的机器上这道闸对每张图都放行"。
    import corpus_tables as _CT24
    _win_keys = set(getattr(C, 'STYLE_HUE_WINDOW', {}))
    _pal_keys = set(_CT24.PALETTES)
    _keys_ok = _win_keys == _pal_keys
    _src24 = io.open(engine, encoding='utf-8', errors='replace').read()
    _nodep_ok = 'import matplotlib' not in _src24
    notes.append(u"美学闸不静默失效：色相窗键==真色板 id（%s，差集 %s）、闸不依赖未声明的 matplotlib=%s -> %s" % (
        _keys_ok, sorted(_win_keys ^ _pal_keys), _nodep_ok,
        u'ok' if (_keys_ok and _nodep_ok) else u'FAIL'))
    if not _keys_ok:
        fails.append(u"STYLE_HUE_WINDOW 的键与 PALETTES 不一致（差集 %s）：拼错的颜色名会静默落进"
                     u" .get 的蓝窗默认值，那道闸就拿 tech_blue 的尺子判别的风格" % (
                         sorted(_win_keys ^ _pal_keys),))
    if not _nodep_ok:
        fails.append(u"美学闸又依赖了未声明的 matplotlib：requirements.txt 里没有它，"
                     u"干净安装的机器上 except 会吞掉 ImportError 让这道闸对每张图都放行")

    # ---------------- 提示词主题/象征句清洗（SPEC §25）：半句话/序号拒、完整短语放行
    import styles as _ST
    _ph_cases = [
        (u'空着的是妇幼保健院', True), (u'四个条件今年同时成立·12', True),
        (u'含妇科检查室与市电配电·16', True), (u'是公开成交价拼出来的：', False),
        (u'七', False), (u'第6章那套混编采购口径会把投', False),
        (u'我们照做的方式是·15%', False), (u'下面两层不赚钱·12', True),
        (u'77.9 万', False), (u'18,000', False), (u'1500 到 3000 元', True),
        (u'跨边网络效应不建在车上', True), (u'一句话：车联体是三条网的接口', True),
        (u'上面这套账全程按顶配口径的 136.0 万元投入计', False),
    ]
    _ph_bad = [(t, w) for t, w in _ph_cases
               if _ST.phrase_ok(_ST.clean_phrase(t, 16)) != w]
    _ph_subj = _ST.concrete_subject('data', u'七', [u'件事'],
                                    body=u'新品证号归工厂还是我们自己持有',
                                    display=[u'八·20', u'30 个'])
    _ph_omit = u'象征' not in _ph_subj
    # pack 侧同尺子：往合格提示词里手工塞「主题为「七」」+半句话象征，必须被 validate_prompts 拦下
    _real_img = os.path.join(_wp, 'promptpack', 'p003_image.txt')
    _base_txt = io.open(_real_img, encoding='utf-8').read() if os.path.exists(_real_img) else None
    if _base_txt and u'主体特征' not in _base_txt:
        _base_txt = None
    if not _base_txt:
        # 兜底夹具：语料句式完整 9 段（与 scaffold 同构），保证段数判据不误伤
        _base_txt = (u'生成一张 16:9 横版 PPT 背景图，主题为「空着的床位」，整体采用科技蓝视觉语言。\n\n'
                     u'主体特征：画面中央偏下是一辆白色移动医疗车，远景保持低细节与干净留白，象征空着的床位。'
                     u'主体占画面宽度约 22%%，不得变形。\n\n'
                     u'构图方式：文字区为横向 7%%～49%%、纵向 7%%～84%%，该区域内部必须保持完全空白。\n\n'
                     u'镜头角度：正面平视，等效 24—35mm 广角，透视关系准确。\n\n'
                     u'光线方向：中央蓝色光源、边缘轮廓光，高对比但不过曝。\n\n'
                     u'色彩搭配：深海军蓝、藏蓝、科技蓝为主色，避免其他颜色。\n\n'
                     u'材质细节：半透明玻璃材质、霓虹光纤，物体边缘清晰。\n\n'
                     u'背景元素：下方为未来数字化地面，背景元素保持低对比度。\n\n'
                     u'画面风格：高端未来科技展厅，8K 视觉品质，电影级渲染。\n\n'
                     u'画面参数：16:9 横版，短边不低于 2160 像素，无文字，无水印。\n')
    _vp_dir = os.path.join(TMP, 'vp_prompt')
    os.makedirs(_vp_dir, exist_ok=True)
    _vp_f = os.path.join(_vp_dir, 'p001_image.txt')
    _poison = re.sub(u'主题为「[^」]*」', u'主题为「七」', _base_txt)
    if u'象征' in _poison:
        _poison = re.sub(u'象征([^。]*)。', u'象征\g<1>会把投。', _poison, count=1)
    else:
        _poison = _poison.replace(u'。主体占', u'，象征第6章那套混编采购口径会把投。主体占', 1)
    io.open(_vp_f, 'w', encoding='utf-8').write(_poison)
    _wp_vp = {'promptpack': _vp_dir}
    _vp_shots = {'pages': [{'page': 1, 'role': 'data', 'asset_kind': 'image'}]}
    _vp_ol = {'pages': [{'headline': u'测试页'}]}
    _vp_probs = C.validate_prompts(_wp_vp, _vp_shots, _vp_ol)[1][0]['problems']
    _vp_hit = any(u'主题' in p for p in _vp_probs) and any(u'象征' in p for p in _vp_probs)
    io.open(_vp_f, 'w', encoding='utf-8').write(_base_txt)
    _vp_clean_probs = C.validate_prompts(_wp_vp, _vp_shots, _vp_ol)[1]
    _vp_ok = not any(any(u'主题' in p or u'象征' in p for p in r['problems'])
                     for r in _vp_clean_probs)
    notes.append(u"提示词清洗反证：%d 短语判据错=%d、无合格尾句省略象征=%s、"
                 u"validate_prompts 拦半句话主题/象征=%s、放行完整短语=%s -> %s" % (
                     len(_ph_cases), len(_ph_bad), _ph_omit, _vp_hit, _vp_ok,
                     'ok' if (not _ph_bad and _ph_omit and _vp_hit and _vp_ok) else 'FAIL'))
    if _ph_bad or not _ph_omit or not _vp_hit or not _vp_ok:
        fails.append(u"clean_phrase/phrase_ok/validate_prompts 不咬或误伤：错例=%s 省略象征=%s "
                     u"拦半句=%s 放行=%s" % (_ph_bad, _ph_omit, _vp_hit, _vp_ok))

    # ---------------- P17 首尾帧双附件（G2）：承诺尾帧必须点名两张附件，两步卡不误伤
    _v_two = u'以附件1（gen/p001/image.png）作为首帧生成视频，镜头缓慢推进。'
    _v_tail1 = u'以附件1作为首帧、尾帧由模型自行补全，生成首尾帧视频。'
    _v_tail2 = u'以附件1（gen/p001/image.png）作为首帧、附件2（gen/p001/last.png）作为尾帧。'
    _f_two = C.score_video_prompt(_v_two)[1]['首尾帧双附件']
    _f_tail1 = C.score_video_prompt(_v_tail1)[1]['首尾帧双附件']
    _f_tail2 = C.score_video_prompt(_v_tail2)[1]['首尾帧双附件']
    _g2dual_ok = (_f_two and not _f_tail1 and _f_tail2)
    notes.append(u"首尾帧双附件反证：两步卡放行=%s、只点附件1的尾帧模式被拦=%s、双附件放行=%s -> %s" % (
        _f_two, not _f_tail1, _f_tail2, 'ok' if _g2dual_ok else 'FAIL'))
    if not _g2dual_ok:
        fails.append(u"G2 首尾帧双附件不咬或误伤：两步=%s 尾帧单附件=%s 双附件=%s" % (
            _f_two, _f_tail1, _f_tail2))

    # ---------------- P17 定格收束（G15）：静态片尾放行、递增斜坡片尾必拦
    import numpy as _npt
    from PIL import Image as _Imt
    import imageio_ffmpeg as _iit
    _fft = _iit.get_ffmpeg_exe()
    _tdir = os.path.join(TMP, 'tail_freeze')
    os.makedirs(_tdir, exist_ok=True)

    def _mk_clip(name, ramp):
        d = os.path.join(_tdir, name)
        os.makedirs(d, exist_ok=True)
        for i in range(24):
            base = 30 + (i * 8 if ramp else 0)
            arr = _npt.zeros((90, 160, 3), dtype=_npt.uint8)
            arr[:, :, 2] = min(255, base + 40)
            arr[:, :, 1] = min(255, base)
            _Imt.fromarray(arr).save(os.path.join(d, 'f%03d.png' % i))
        out = os.path.join(_tdir, name + '.mp4')
        subprocess.run([_fft, '-hide_banner', '-loglevel', 'error', '-y', '-framerate', '12',
                        '-i', os.path.join(d, 'f%03d.png'), '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', out], capture_output=True)
        poster = os.path.join(_tdir, name + '_poster.png')
        _pa = _npt.zeros((90, 160, 3), dtype=_npt.uint8)
        _pa[:, :, 1] = 30
        _pa[:, :, 2] = 70
        _Imt.fromarray(_pa).save(poster)
        return out, poster

    _freeze_v, _freeze_p = _mk_clip('freeze', False)
    _ramp_v, _ramp_p = _mk_clip('ramp', True)
    _ft, _fl = C.tail_freeze_metrics(_freeze_v)
    _rt, _rl = C.tail_freeze_metrics(_ramp_v)
    _tail_ok = (_fl is not None and _fl <= C.TH['loop_seam_mad_max']
                and _rl is not None and _rl > C.TH['loop_seam_mad_max'])
    notes.append(u"循环接缝反证：静态片 loop_mad=%s（≤%g 放行）、斜坡片 loop_mad=%s（>%g 必拦）-> %s" % (
        _fl, C.TH['loop_seam_mad_max'], _rl, C.TH['loop_seam_mad_max'],
        'ok' if _tail_ok else 'FAIL'))
    if not _tail_ok:
        fails.append(u"G15 循环接缝不咬或误伤：静态 loop_mad=%s 斜坡 loop_mad=%s" % (_fl, _rl))



    # waive 反证：没用户原话不许豁免；有原话才留证+记决定+队列跳过
    _gv = os.path.join(_wp, 'gen', 'p001', 'video.mp4')
    os.makedirs(os.path.dirname(_gv), exist_ok=True)
    io.open(_gv, 'wb').write(b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 64)
    r_wn = subprocess.run([sys.executable, engine, 'waive', '--work', _wp, '--page', '1'],
                          capture_output=True, text=True, encoding='utf8', errors='replace',
                          env=_pk_env)
    r_w = subprocess.run([sys.executable, engine, 'waive', '--work', _wp, '--page', '1',
                          '--note', u'用户说：这页用静态图加推镜'],
                         capture_output=True, text=True, encoding='utf8', errors='replace',
                         env=_pk_env)
    _wv = json.load(io.open(os.path.join(_wp, 'video_waiver.json'), encoding='utf-8'))
    _wv_rows = _wv if isinstance(_wv, list) else [_wv]
    _w_ok = (r_wn.returncode != 0 and r_w.returncode == 0 and not os.path.exists(_gv)
             and any(r.get('page') == 1 and r.get('user_words') for r in _wv_rows)
             and os.path.exists(os.path.join(adir, 'AI产物', 'p001',
                                             'video_waived_original.mp4')))
    notes.append(u"waive 单页视频豁免：无原话被拒=%s｜有原话留证+记决定=%s -> %s" % (
        r_wn.returncode != 0, _w_ok, u'ok' if _w_ok else u'FAIL'))
    if not _w_ok:
        fails.append(u"waive 不成立：缺用户原话必须拒；豁免必须把原片留证 AI产物 并写 video_waiver.json")

    # G11 豁免感知反证：video_bg 缺视频先被点名；用户原话豁免后不再算"缺产物"
    # （旧 bug：22 页已豁免仍被 G11 列进 missing，把已收口的决定又催一遍）
    _wz = os.path.join(TMP, 'waive_g11')
    shutil.rmtree(_wz, ignore_errors=True)
    shutil.copytree(cli, _wz)
    _wzs = os.path.join(_wz, 'shots.json')
    _wzj = json.load(io.open(_wzs, encoding='utf-8'))
    for _s in _wzj['pages']:
        if _s['page'] == 1:
            _s['asset_kind'] = 'video_bg'
    json.dump(_wzj, io.open(_wzs, 'w', encoding='utf-8'), ensure_ascii=False)
    _wzg = os.path.join(_wz, 'gen', 'p001')
    if os.path.isdir(_wzg):
        shutil.rmtree(_wzg)

    def _g11_lists():
        _rp = os.path.join(_wz, 'report', u'验收报告.md')
        if os.path.exists(_rp):
            os.remove(_rp)
        subprocess.run([sys.executable, engine, 'check', '--work', _wz],
                       capture_output=True, text=True, encoding='utf8', errors='replace')
        _t = io.open(_rp, encoding='utf-8').read() if os.path.exists(_rp) else u''
        _m = re.search(r'"video_pages_missing": (\[[^\]]*\])', _t)
        _w = re.search(r'"video_pages_waived": (\[[^\]]*\])', _t)
        return (json.loads(_m.group(1)) if _m else None,
                json.loads(_w.group(1)) if _w else None)

    _m0, _w0 = _g11_lists()
    subprocess.run([sys.executable, engine, 'waive', '--work', _wz, '--page', '1',
                    '--note', u'用户说：这页不要视频'], capture_output=True, text=True,
                   encoding='utf8', errors='replace')
    _m1, _w1 = _g11_lists()
    _wz_ok = (_m0 is not None and 1 in _m0 and _m1 is not None and 1 not in _m1
              and _w1 is not None and 1 in _w1)
    notes.append(u"G11 豁免感知：豁免前 missing 含页1=%s｜豁免后 missing 不含页1=%s 且 waived 含页1=%s -> %s" % (
        _m0 is not None and 1 in _m0, _m1 is not None and 1 not in _m1,
        _w1 is not None and 1 in _w1, u'ok' if _wz_ok else u'FAIL'))
    if not _wz_ok:
        fails.append(u"G11 不认豁免：用户原话豁免的页必须从 video_pages_missing 移除并进 waived 清单")

    # 队列 freshness 反证：pickup+waive 之后这页已无待回收项，card 不许再拿 pack 时的旧快照
    # 把它排队首（旧 bug：req['items'][present] 是 pack 快照，pickup 不刷新 → 反复发卡）
    for _k in ('first', 'last', 'gif'):
        _fp = os.path.join(_wp, 'promptpack', 'p001_%s.txt' % _k)
        if os.path.exists(_fp):
            os.remove(_fp)
    subprocess.run([sys.executable, engine, 'pack', '--work', _wp],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    r_q = subprocess.run([sys.executable, engine, 'card', '--work', _wp],
                         capture_output=True, text=True, encoding='utf8', errors='replace',
                         env=_pk_env)
    _q_ok = (r_q.returncode == 0 and (r_q.stdout or '').strip()
             and u'第 1 页 ｜' not in (r_q.stdout or ''))
    notes.append(u"card 队列按 gen/ 现状重算：页1 图已回收+视频已豁免后队首不再是页1（队首行=%s）-> %s" % (
        ((r_q.stdout or '').splitlines() or [''])[1][:24], u'ok' if _q_ok else u'FAIL'))
    if not _q_ok:
        fails.append(u"card 队列吃了 pack 旧快照：已 pickup/已豁免的页还被排进队首/重复发卡")

    # ---------------- 散文页不许整段上屏（G23 实测 v10 有 15 页超预算、首页 86 字）
    import collections as _CL
    import display_refine as _DR
    pw_dir = os.path.join(TMP, 'prose_pack')
    shutil.rmtree(pw_dir, ignore_errors=True)
    os.makedirs(pw_dir)
    _pw_doc = os.path.join(pw_dir, 'doc.docx')
    _pwx = _DX.Document()
    _pwx.add_heading(u'三条网的接口层', 0)
    _pwx.add_paragraph(u'一、接口')
    for _pp in [u'行业里已经有三张网。医联体连医院，科联体连科研和工厂，客联体连客户。'
                u'三张网各自存在了很多年，互相不接。谁都不缺主体，缺的是把主体串起来的那一层。',
                u'道理不复杂：先把车交给医院，再由医院排期，最后按次结算，三方各拿各的钱。'
                u'排期表由我们出，司机的工资由我们发，保险由我们统一买。']:
        _pwx.add_paragraph(_pp)
    _pwx.save(_pw_doc)
    r_pwp = subprocess.run([sys.executable, engine, 'plan', _pw_doc, '--work', pw_dir, '--auto-pages'],
                           capture_output=True, text=True, encoding='utf8', errors='replace')
    # 这份夹具测的是"refine 还没来得及写显示行"那一档（v10 实测 238 页里 50 页是空的），
    # plan 自己填的提案会把这条路径盖住——清掉 display_lines 才是引擎装箱的现场。
    _pw_sh = json.load(io.open(os.path.join(pw_dir, 'shots.json'), encoding='utf-8'))
    for _x in _pw_sh['pages']:
        _x['display_lines'] = []
    json.dump(_pw_sh, io.open(os.path.join(pw_dir, 'shots.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    r_pwb = subprocess.run([sys.executable, engine, 'build', '--work', pw_dir],
                           capture_output=True, text=True, encoding='utf8', errors='replace')
    r_pwc = subprocess.run([sys.executable, engine, 'check', '--work', pw_dir],
                           capture_output=True, text=True, encoding='utf8', errors='replace')
    _pw_p = os.path.join(pw_dir, 'report', u'验收报告.md')
    _pw_rep = io.open(_pw_p, encoding='utf-8').read() if os.path.exists(_pw_p) else ''
    _pw_g23 = re.search(r'G23_页面预算[\s\S]{0,600}?"over_pages":\s*(\d+)', _pw_rep)
    _pw_bl = os.path.join(pw_dir, 'out', 'build_log.json')
    _pw_blog = json.load(io.open(_pw_bl, encoding='utf-8')) if os.path.exists(_pw_bl) else []
    _pw_modes = dict(_CL.Counter(x.get('display_lines_source') for x in _pw_blog
                                 if x.get('display_lines_source')))
    _pw_cards = sum(1 for x in _pw_blog if x.get('data_layout') == 'cards')
    # 成品每页字数：>64 就是文字墙（与 G23 同一口径，直接量成品）
    _pw_max_chars = 0
    _pw_pptx = None
    _pw_outdir = os.path.join(pw_dir, 'out')
    if os.path.isdir(_pw_outdir):
        for f in os.listdir(_pw_outdir):
            if f.endswith('.pptx'):
                _pw_pptx = os.path.join(_pw_outdir, f)
    if _pw_pptx:
        _pw_by = {}
        for _si, _nm, _raw, _nt in C.pptx_boxes(_pw_pptx):
            _pw_by.setdefault(_si, []).append(_nt)
        for _si, _ls in _pw_by.items():
            _pw_max_chars = max(_pw_max_chars, len(re.sub(r'\s', u'', u''.join(_ls))))
    # 反证：并列判据不许把普通散文认成卡片；装箱不许把数字裁成半截
    _pw_neg = _DR.parallel_enum(u'道理不复杂，先把车交给医院，再由医院排期，最后按次结算，'
                                u'三方各拿各的钱，中间没有人垫资。')
    _pw_pos = _DR.parallel_enum(u'医联体连医院，科联体连科研和工厂，客联体连客户。')
    _pw_pack = _DR.clause_pack(u'先把车交给医院，再由医院排期，最后按次结算，三方各拿各的钱。')
    _pw_pack_ok = (len(_pw_pack) <= _DR.MAX_LINES
                   and all(len(re.sub(r'\s', u'', x)) <= _DR.MAX_LINE for x in _pw_pack))
    _pw_cut = _DR.clause_pack(u'2026年公司在华东 12 城签约 268 家医美机构，实际履约 214 家。',
                              max_line=14, max_lines=2)
    _pw_num_ok = not any(x and re.search(u'\\d$', x)
                         and not re.search(u'\\d\\s*(?:%|家|台|个|元|万|亿|城)$', x) for x in _pw_cut)
    pw_ok = (r_pwp.returncode == 0 and r_pwb.returncode == 0 and _pw_g23
             and _pw_g23.group(1) == '0' and _pw_max_chars <= 64
             and _pw_cards >= 1 and bool(_pw_pos) and not _pw_neg and _pw_pack_ok and _pw_num_ok)
    notes.append(u"散文页整段上屏收口（并列→卡片、其余→子句装箱）：G23 超预算页=%s "
                 u"成品单页最多 %d 字、卡片页 %d 页、装箱来源 %s；"
                 u"并列判据 正=%s 反=%s、装箱不裁数字=%s -> %s" % (
                     _pw_g23.group(1) if _pw_g23 else u'无读数', _pw_max_chars, _pw_cards,
                     json.dumps(_pw_modes, ensure_ascii=False), bool(_pw_pos), not _pw_neg,
                     _pw_num_ok, u'ok' if pw_ok else u'FAIL'))
    if r_pwp.returncode != 0 or r_pwb.returncode != 0:
        fails.append(u"散文装箱用例没跑成（plan rc=%d build rc=%d）：%s" % (
            r_pwp.returncode, r_pwb.returncode, (r_pwb.stdout or '')[-160:]))
    if not pw_ok:
        fails.append(u"散文页还在整段上屏（G23 超预算页 %s、单页最多 %d 字、卡片页 %d）："
                     u"并列的一串要画成卡片，其余按子句装箱成 ≤2 条 ≤14 字，"
                     u"装不下的事实由末尾安全网补回——不能退回小字墙" % (
                         _pw_g23.group(1) if _pw_g23 else u'无读数', _pw_max_chars, _pw_cards))

    # ---------------- #61 文本体裁要点页：无公因子的并列短句要画成卡片，标题吸收一条也不许归 0
    # 两面单元反证：① 培训/制度/手册那种『先看现场。再找问题。最后定动作。』(各自成句、无共享词)
    # 必须被 parallel_enum 认成 ≥3 条要点——旧版只认公因子，这类页一律退回子句装箱→图形化率 0；
    # ② 连贯散文长句(整句只有一个句末标点)与"只有两条短句"都不许被认成卡片(min_items 守卫)。
    _e61_pos_list = _DR.parallel_enum([u'先看现场。', u'再找问题。', u'最后定动作。'])
    _e61_pos_str = _DR.parallel_enum(u'先看现场。再找问题。最后定动作。')
    _e61_neg_prose = _DR.parallel_enum(u'道理不复杂，先把车交给医院，再由医院排期，最后按次结算，'
                                       u'三方各拿各的钱，中间没有人垫资。')
    _e61_neg_two = _DR.parallel_enum([u'先看现场。', u'再找问题。'])
    _e61_unit_ok = (len(_e61_pos_list) >= 3 and len(_e61_pos_str) >= 3
                    and not _e61_neg_prose and not _e61_neg_two)
    # 端到端回归 guard：造一页"标题就是第一条要点"的培训页，清掉显示行逼装配走卡片路。
    # dedupe 会把与标题同文的那条要点吸收进标题框(body 剩 2 条)，卡片分支必须按 card_items
    # 整组(≥3)画出卡片——这正是 #61 第三处修复(rec['card_items'])要守住的现象，否则图形化率归 0。
    e61_dir = os.path.join(TMP, 'enum61_cards')
    shutil.rmtree(e61_dir, ignore_errors=True)
    os.makedirs(e61_dir)
    _e61_doc = os.path.join(e61_dir, 'doc.docx')
    _e61x = _DX.Document()
    _e61x.add_heading(u'门店晨会三件事', 0)
    _e61x.add_paragraph(u'一、晨会')
    for _ep in [u'先看现场。再找问题。最后定动作。',
                u'晨会不是走过场：值班经理带着大家把昨天留下的问题过一遍，当场定下今天谁去改。']:
        _e61x.add_paragraph(_ep)
    _e61x.save(_e61_doc)
    r_e61p = subprocess.run([sys.executable, engine, 'plan', _e61_doc, '--work', e61_dir, '--auto-pages'],
                            capture_output=True, text=True, encoding='utf8', errors='replace')
    _e61_shp = os.path.join(e61_dir, 'shots.json')
    _e61_sh = json.load(io.open(_e61_shp, encoding='utf-8')) if os.path.exists(_e61_shp) else {'pages': []}
    # plan 把要点提炼进 display_lines、body 留空，所以不能"找一条 body 里已有 ≥3 要点的页"——
    # 直接强制第一个内容页(非封面/过渡/结尾)进入"标题==第一条要点"的吸收现场：headline 设成
    # 要点之一、body 放整组各自成句的短句、显示行清空，逼装配走卡片路并触发 dedupe 吸收。
    _e61_pts = [u'先看现场', u'再找问题', u'最后定动作']
    _e61_target = None
    for _x in _e61_sh.get('pages') or []:
        if _x.get('role') in ('cover', 'divider', 'closing'):
            continue
        _x['headline'] = _e61_pts[0]
        _x['body'] = [p + u'。' for p in _e61_pts]
        _x['display_lines'] = []
        _x['content_type'] = 'list'
        _x['verbatim'] = False
        _e61_target = _x.get('page')
        break
    json.dump(_e61_sh, io.open(_e61_shp, 'w', encoding='utf-8'), ensure_ascii=False)
    r_e61b = subprocess.run([sys.executable, engine, 'build', '--work', e61_dir],
                            capture_output=True, text=True, encoding='utf8', errors='replace')
    _e61_blp = os.path.join(e61_dir, 'out', 'build_log.json')
    _e61_blog = json.load(io.open(_e61_blp, encoding='utf-8')) if os.path.exists(_e61_blp) else []
    _e61_cards = sum(1 for x in _e61_blog if x.get('data_layout') == 'cards')
    e61_ok = (_e61_unit_ok and r_e61p.returncode == 0 and r_e61b.returncode == 0
              and _e61_target is not None and _e61_cards >= 1)
    notes.append(u"#61 文本体裁要点页画卡片：并列判据 短句列表正=%d 字符串正=%d 散文反=%s 两条反=%s；"
                 u"标题吸收一条后端到端卡片页=%d（目标页 %s）-> %s" % (
                     len(_e61_pos_list), len(_e61_pos_str), not _e61_neg_prose, not _e61_neg_two,
                     _e61_cards, _e61_target, u'ok' if e61_ok else u'FAIL'))
    if not _e61_unit_ok:
        fails.append(u"#61 并列判据没认文本体裁的独立短句要点（列表正=%d 字符串正=%d 散文反=%s 两条反=%s）："
                     u"『先看现场。再找问题。最后定动作。』这种各自成句、无公因子的 ≥3 条要点必须认成卡片来源，"
                     u"连贯散文长句与只有两条的都不许误判" % (
                         len(_e61_pos_list), len(_e61_pos_str), not _e61_neg_prose, not _e61_neg_two))
    if r_e61p.returncode != 0 or r_e61b.returncode != 0 or _e61_target is None:
        fails.append(u"#61 要点页用例没跑成（plan rc=%d build rc=%d 目标页=%s）：%s" % (
            r_e61p.returncode, r_e61b.returncode, _e61_target, (r_e61b.stdout or '')[-160:]))
    elif _e61_cards < 1:
        fails.append(u"#61 标题吸收一条要点后卡片页归 0（data_layout=cards 的页=%d）："
                     u"标题==其中一条要点时 dedupe 会把它吸收进标题框、body 只剩 2 条，"
                     u"卡片分支必须按 rec['card_items'] 整组(≥3)画卡，不能退回小字墙把图形化率打成 0" % _e61_cards)

    # ---------------- card：一页一轮把这一页的提示词原样打出来（用户读不动 40 万字的卡）
    _cw = os.path.join(TMP, 'card_page')
    shutil.rmtree(_cw, ignore_errors=True)
    shutil.copytree(gs, _cw, ignore=shutil.ignore_patterns('__pycache__', 'report'))
    # 待回收队列要以"产物不在 gen 里"为准：gs 的 gen 被前面的 G26 正证填过了，
    # 不清掉就没有"待回收的一页"可发，card 会直接报齐活（那也是要测的分支，先测发卡这条）。
    shutil.rmtree(os.path.join(_cw, 'gen'), ignore_errors=True)
    subprocess.run([sys.executable, engine, 'pack', '--work', _cw],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    _cd_req_p = os.path.join(_cw, 'promptpack', 'delivery_request.json')
    _cd_req = json.load(io.open(_cd_req_p, encoding='utf-8')) if os.path.exists(_cd_req_p) else {}
    _cd_miss = [i for i in _cd_req.get('items') or [] if not i['present']]
    _cd_page = min(i['page'] for i in _cd_miss) if _cd_miss else 0
    r_cd = subprocess.run([sys.executable, engine, 'card', '--work', _cw,
                           '--page', str(_cd_page)],
                          capture_output=True, text=True, encoding='utf8', errors='replace')
    r_cd_q = subprocess.run([sys.executable, engine, 'card', '--work', _cw],
                            capture_output=True, text=True, encoding='utf8', errors='replace')
    _cd_txt = u''
    for _k in ('video', 'image', 'gif', 'first', 'last'):
        _f = os.path.join(_cw, 'promptpack', 'p%03d_%s.txt' % (_cd_page, _k))
        if os.path.exists(_f):
            _cd_txt = io.open(_f, encoding='utf-8').read().strip()
            break
    r_cd_bad = subprocess.run([sys.executable, engine, 'card', '--work', _cw, '--page', '9999'],
                              capture_output=True, text=True, encoding='utf8', errors='replace')
    _cd_out = (r_cd.stdout or '') + (r_cd.stderr or '')
    _cd_q_out = (r_cd_q.stdout or '') + (r_cd_q.stderr or '')
    card_ok = (r_cd.returncode == 0 and len(_cd_txt) > 40 and _cd_txt in _cd_out
               and (u'第 %d 页' % _cd_page) in _cd_out
               and u'pickup --work' in _cd_out and u'收件' in _cd_out
               and r_cd_q.returncode == 0 and u'第 ' in _cd_q_out
               and r_cd_bad.returncode != 0)
    notes.append(u"card 一页一轮发卡：--page %s 提示词全文原样打出=%s（%d 字）、带回投指令=%s、"
                 u"收件夹写出来=%s；不给页码走队列=%s（%s）；不存在页被拒=%s -> %s" % (
                     _cd_page, _cd_txt in _cd_out if _cd_txt else False, len(_cd_txt),
                     u'pickup --work' in _cd_out, u'收件' in _cd_out,
                     r_cd_q.returncode == 0, (_cd_q_out.split(u'\n')[1] if _cd_q_out else u'')[:22],
                     r_cd_bad.returncode != 0, u'ok' if card_ok else u'FAIL'))
    if not card_ok:
        fails.append(u"card 命令没能把一页的提示词整段打出来（rc=%d，取到 %d 字）："
                     u"用户要的是「一页一轮」，读不动整册 delivery_card.md" % (
                         r_cd.returncode, len(_cd_txt)))

    # ---------------- 三条新口径的自证：标题里的数提成大数字 / 台账按字预算翻页 /
    #                  G23 不吃引擎自己追加的来源署名页
    _hh_lab, _hh_hero = C.headline_hero(u'盈亏平衡 9.0 台', [u'盈亏平衡 9.0 台'])
    _hh_two = C.headline_hero(u'样板期的合并亏损从 138.6 万元收窄到 36.0 万元',
                              [u'样板期的合并亏损从 138.6 万元收窄到 36.0 万元'])
    _hh_year = C.headline_hero(u'2026 年投放', [u'2026 年投放'])
    _hh_dangling = C.headline_hero(u'成本从 288 万元', [u'成本从 288 万元'])
    _hh_body = C.headline_hero(u'盈亏平衡 9.0 台', [u'另外还有一句结论要上屏，讲的是回本周期'])
    hh_ok = (_hh_hero and _hh_hero['value'] == u'9.0' and _hh_hero['unit'] == u'台'
             and _hh_lab == u'盈亏平衡' and _hh_two[1] is None and _hh_year[1] is None
             and _hh_dangling[1] is None and _hh_body[1] is None)
    notes.append(u"标题里的数提成大数字（标签=「%s」值=%s）：两个数不拆=%s 裸年份不拆=%s "
                 u"残句不拆=%s 正文有别的话不拆=%s -> %s" % (
                     _hh_lab, (_hh_hero or {}).get('value'), _hh_two[1] is None,
                     _hh_year[1] is None, _hh_dangling[1] is None, _hh_body[1] is None,
                     u'ok' if hh_ok else u'FAIL'))
    if not hh_ok:
        fails.append(u"headline_hero 不成立（标签=%r hero=%r）：要么把数从标题摘了却没画出来（G24 丢事实），"
                     u"要么该拆的没拆（同页重影）" % (_hh_lab, _hh_hero))

    _tp_rows = [[u'数字', u'出处', u'级别']] + [
        [u'妇幼筛查车成交 %.2f 万元' % i,
         u'整车含上装取西平县妇幼保健院 2025 年 12 月成交的公开价格，另含改装费三年维保与上牌代办费用，'
         u'同期河南两家医院另有两笔更接近的成交价可对照', u'A 级']
        for i in range(9)]
    _tp_pages = C.table_pages(_tp_rows, u'附录A 数据出处', 9, 0)
    _tp_chars = [sum(len(re.sub(r'\s', u'', c or u'')) for r in p['table']['rows'] for c in r)
                 + sum(len(re.sub(r'\s', u'', c or u'')) for c in p['table']['header'])
                 for p in _tp_pages]
    _tp_rows_in = sum(len(p['table']['rows']) for p in _tp_pages)
    tp_ok = (len(_tp_pages) >= 3 and max(_tp_chars or [0]) <= C.TH['page_chars_max_table']
             and _tp_rows_in == len(_tp_rows) - 1)
    notes.append(u"台账按字预算翻页：%d 页、每页最多 %d 字（上限 %d）、行数不丢=%s（进 %d / 原 %d）-> %s" % (
        len(_tp_pages), max(_tp_chars or [0]), C.TH['page_chars_max_table'],
        _tp_rows_in == len(_tp_rows) - 1, _tp_rows_in, len(_tp_rows) - 1,
        u'ok' if tp_ok else u'FAIL'))
    if not tp_ok:
        fails.append(u"台账页还在整表硬塞（%d 页 / 单页最多 %d 字）：一行 70 字的出处表 8 行就 569 字，"
                     u"必须按字预算翻页，且一格都不许丢" % (len(_tp_pages), max(_tp_chars or [0])))

    # G23 的 chrome 豁免必须"量得出差别"：判据只吃装配页，豁免的页数要能与成品的真实页数对上，
    # 并且关掉装配账后判据照常生效（不是把整闸闭嘴）。
    _g23_deck_p = os.path.join(g20_good, 'out')
    _g23_deck = os.path.join(_g23_deck_p, [f for f in os.listdir(_g23_deck_p)
                                          if f.endswith('.pptx')][0])
    _g23_bl = json.load(io.open(os.path.join(_g23_deck_p, 'build_log.json'), encoding='utf-8'))
    _g23_with = C.g23_page_budget(_g23_deck, C.TH, False, _g23_bl)
    _g23_without = C.g23_page_budget(_g23_deck, C.TH, False, None)
    import zipfile as _ZF
    _g23_slides = len([x for x in _ZF.ZipFile(_g23_deck).namelist()
                       if re.match(r'ppt/slides/slide\d+\.xml$', x)])
    # chrome 含两类：引擎追加的署名页（成品-装配）+ 封面/过渡/结尾角色页（SPEC §17 不吃内容预算）
    _g23_role_chrome = sum(1 for r in _g23_bl if r.get('role') in ('cover', 'divider', 'closing'))
    g23_ok = (_g23_with.get('chrome_pages', 0) == (_g23_slides - len(_g23_bl)) + _g23_role_chrome
              and _g23_with['pages'] == _g23_slides
              and _g23_without['over_pages'] >= _g23_with['over_pages']
              and _g23_without['chrome_pages'] == 0)
    notes.append(u"G23 不吃来源署名页（%s 册）：成品 %d 页 / 装配 %d 页 → 豁免 chrome=%d；"
                 u"带装配账 超预算=%d，不带装配账 超预算=%d（判据照常生效）-> %s" % (
                     os.path.basename(g20_good), _g23_slides, len(_g23_bl),
                     _g23_with.get('chrome_pages', 0), _g23_with['over_pages'],
                     _g23_without['over_pages'], u'ok' if g23_ok else u'FAIL'))
    if not g23_ok:
        fails.append(u"G23 的来源页豁免没落地（chrome_pages=%s，成品 %d 页 / 装配 %d 页）："
                     u"引擎自己追加的署名页一页 2000 字 URL，按设计页判就是假硬伤；"
                     u"同时关掉装配账后必须照常判（不许变成整闸闭嘴）" % (
                         _g23_with.get('chrome_pages'), _g23_slides, len(_g23_bl)))

    # ---------------- 窄框里的短文本不许折出孤字尾行（v10 实测 31 处：'%'、'万元'、'一年'）
    _DLn = __import__('data_layout')
    _narrow_cases = [(u'126.4 万元', 0.09), (u'三级医院 89.6%', 0.11),
                     (u'一台车一年 18,000', 0.11), (u'99 万元', 0.09)]
    _narrow_bad = []
    for _t, _w in _narrow_cases:
        _pt = _DLn.fit_pt(_t, _w, (22, 20, 18, 16, 14, 12))
        _lines = [_t] if _DLn.em_width(_t) <= _w * 13.3333 * 72.0 / 12.0 \
            else _DLn.break_balanced(_t, 2)
        for _ln in _lines:
            _nln, _ntl = C.orphan_tail(_ln, _pt, _w)
            if _nln >= 2 and 0 < _ntl <= C.TH['orphan_tail_max_chars']:
                _narrow_bad.append({'text': _t[:14], 'pt': _pt, 'tail': _ln[-_ntl:]})
    # 真成品那一侧：条形组与筹码框按形状实际宽度再量一遍
    _narrow_shapes = []
    try:
        from pptx import Presentation as _PR2
        _sdp = os.path.join(sd_dir, 'out')
        _sppptx = os.path.join(_sdp, [f for f in os.listdir(_sdp) if f.endswith('.pptx')][0])
        for _si, _sl in enumerate(_PR2(_sppptx).slides, 1):
            for _sh in _sl.shapes:
                if not (_sh.has_text_frame and str(_sh.name or '').startswith(
                        ('bar_value', 'bar_label', 'chip_text'))):
                    continue
                _tx = _sh.text_frame.text.strip()
                _pt2 = 0
                for _p3 in _sh.text_frame.paragraphs:
                    for _r3 in _p3.runs:
                        if _r3.font.size:
                            _pt2 = max(_pt2, int(_r3.font.size.pt))
                _nl, _tl = C.orphan_tail(_tx, float(_pt2 or 12),
                                         _sh.width / 914400.0 / 13.3333)
                if _nl >= 2 and 0 < _tl <= C.TH['orphan_tail_max_chars']:
                    _narrow_shapes.append({'page': _si, 'tail': _tx[-_tl:]})
    except Exception as _e:
        _narrow_shapes.append({'err': str(_e)[:60]})
    narrow_ok = not _narrow_bad and not _narrow_shapes
    # 一把尺子的自证：成品里每个数据形状，挑字号用的模型与判孤字用的模型必须给出同一个答案
    # （上一轮 hero 用 em_units、闸门用 char_em_width，93pt 的 '26 万人' 一边"放得下"一边"末行只剩人"）
    _yard_bad = []
    try:
        for _si, _sl in enumerate(_PR2(_sppptx).slides, 1):
            for _sh in _sl.shapes:
                if not (_sh.has_text_frame and str(_sh.name or '').startswith(
                        ('hero_num', 'chip_text', 'bar_value', 'bar_label'))):
                    continue
                _tx2 = _sh.text_frame.text.strip()
                _p4 = [r.font.size.pt for _pp in _sh.text_frame.paragraphs for r in _pp.runs
                       if r.font.size]
                _pt4 = max(_p4) if _p4 else 18.0
                _w4 = _sh.width / 914400.0 / 13.3333
                _box_pt = _w4 * 13.3333 * 72.0
                if _DLn.em_width(_tx2) * _pt4 > _box_pt * 1.02:
                    _yard_bad.append({'page': _si, 'name': _sh.name, 'pt': _pt4,
                                      'need': round(_DLn.em_width(_tx2) * _pt4, 1),
                                      'box': round(_box_pt, 1)})
    except Exception as _e2:
        _yard_bad.append({'err': str(_e2)[:60]})
    narrow_ok = narrow_ok and not _yard_bad
    notes.append(u"窄框短文本折行（柱顶/筹码）：单元用例孤字=%d 处、条形组册孤字形状=%d 处；"
                 u"'126.4 万元' 在 0.09 宽挑到 %spt；数据形状「挑字号」与「判孤字」同尺不符=%d 个 -> %s" % (
                     len(_narrow_bad), len(_narrow_shapes),
                     _DLn.fit_pt(u'126.4 万元', 0.09, (22, 20, 18, 16, 14, 12)),
                     len(_yard_bad), u'ok' if narrow_ok else u'FAIL'))
    if _yard_bad:
        fails.append(u"排版宽度又分成两把尺子了（%s）：hero/筹码/柱顶挑字号用的模型必须与 "
                     u"orphan_tail（G8）用的 char_em_width 同一个，否则一边「放得下」一边「末行只剩一个字」" % (
                         json.dumps(_yard_bad[:3], ensure_ascii=False),))
    if not narrow_ok:
        fails.append(u"窄框里的数字又被折成孤字尾行（单元 %s / 成品 %s）："
                     u"柱顶与筹码的字号必须按框宽挑（fit_pt），一行放不下就均衡折两行，"
                     u"不许留 '%'、'万元' 这种断掉的数" % (
                         json.dumps(_narrow_bad[:2], ensure_ascii=False),
                         json.dumps(_narrow_shapes[:2], ensure_ascii=False)))

    # ---------------- G13 的"整句被拆到连续几页"豁免：必须能放行真拆句，也必须咬得住真丢句
    sp_dir = os.path.join(TMP, 'split_pages')
    shutil.rmtree(sp_dir, ignore_errors=True)
    os.makedirs(sp_dir)
    _sp_doc = os.path.join(sp_dir, 'doc.docx')
    _spdx = _DX.Document()
    _spdx.add_heading(u'采购口径长句分页测试', 0)
    _spdx.add_paragraph(u'一、投入')
    _spdx.add_paragraph(
        u'采购口径的这两个投入数不是报价单上的数，是公开成交价拼出来的：'
        u'整车含上装取西平县妇幼保健院 2025 年 12 月成交的 44.99 万元（宇通 ZK5117XYL16，'
        u'含妇科检查室与市电配电，公告未列射线机），设备包取 A 级单价加总的 32.87 万元'
        u'（便携彩超 15.00 万、生化 7.40 万、血球 4.50 万、离心机 0.50 万、医用冷藏箱 0.30 万、'
        u'信息化工作站 5.17 万），选装 DR 再加 25.13 万元到 58.00 万元。')
    _spdx.add_paragraph(u'第二句正常内容：单车 128.5 万元起，回收期 2.9 年，盈亏平衡 9.0 台。')
    _spdx.save(_sp_doc)
    subprocess.run([sys.executable, engine, 'plan', _sp_doc, '--work', sp_dir, '--per-page', '2', '--auto-pages'],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    _sp_sh = json.load(io.open(os.path.join(sp_dir, 'shots.json'), encoding='utf-8'))
    for _x in _sp_sh['pages']:
        _x['display_lines'] = []
    json.dump(_sp_sh, io.open(os.path.join(sp_dir, 'shots.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    subprocess.run([sys.executable, engine, 'build', '--work', sp_dir],
                   capture_output=True, text=True, encoding='utf8', errors='replace')
    rc_sp, t_sp = run_check_at(sp_dir)
    _sp_g = re.search(r'"missing_from_pptx":\s*(\d+)', t_sp)
    _sp_split = re.search(r'"split_across_pages":\s*(\d+)', t_sp)
    # 注入差分：抹掉链条中间某一页的一条正文（真丢内容），同一套判据必须重新点名。
    # 注意要改 outline 再重装配——只改成品 pptx 的话 build_log 还说那页是提炼页，
    # 豁免照走，测出来的是"闸门没读我改的字节"而不是"判据不咬"。
    _sp_op = os.path.join(sp_dir, 'outline.json')
    _sp_ol = json.load(io.open(_sp_op, encoding='utf-8'))
    _nuk = None
    for _p in _sp_ol['pages'][4:9]:
        if _p.get('body'):
            _p['body'].pop(0)
            _nuk = _p['page']
            break
    if _nuk:
        json.dump(_sp_ol, io.open(_sp_op, 'w', encoding='utf-8'), ensure_ascii=False)
        subprocess.run([sys.executable, engine, 'build', '--work', sp_dir],
                       capture_output=True, text=True, encoding='utf8', errors='replace')
        rc_nk, t_nk = run_check_at(sp_dir)
        _nk_g = re.search(r'"missing_from_pptx":\s*(\d+)', t_nk)
    else:
        rc_nk, t_nk, _nk_g = rc_sp, t_sp, None
    split_ok = (bool(_sp_g) and int(_sp_g.group(1)) == 0
                and bool(_sp_split) and int(_sp_split.group(1)) >= 1
                and bool(_nk_g) and int(_nk_g.group(1)) >= 1)
    notes.append(u"G13 连续几页拆句豁免：%d 页册子里长句 missing=%s split_across_pages=%s；"
                 u"注入差分（抹掉页 %s 的一条正文再重装配）missing=%s -> %s" % (
                     len(json.load(io.open(os.path.join(sp_dir, 'outline.json'),
                                           encoding='utf-8'))['pages']),
                     _sp_g.group(1) if _sp_g else u'无读数',
                     _sp_split.group(1) if _sp_split else u'无读数',
                     _nuk, _nk_g.group(1) if _nk_g else u'未测',
                     u'ok' if split_ok else u'FAIL'))
    if not split_ok:
        fails.append(u"G13 的跨页拆句判据不成立（missing=%s split=%s 注入后=%s）："
                     u"一句被 plan 拆到连续几页时不能算丢内容（v10 实测 4 句被这么误点名），"
                     u"但抹掉其中一页又必须重新点名，两头都要成立" % (
                         _sp_g.group(1) if _sp_g else u'?',
                         _sp_split.group(1) if _sp_split else u'?',
                         _nk_g.group(1) if _nk_g else u'?'))

    # ---------------- 丢表的**表头**必须进 G13 台账（#51，实测 30 页册 12 句被误判丢内容）
    # 旧写法 dropped_tables 的 cells 只遍历 rows，表头只留在 ' | ' 拼的 header 串里；
    # G13 是按 blocks_of 逐块（每格一块）核的，于是每个表头短语既不在屏也不在账＝丢内容。
    # 反证两头都要成立：① 真丢表时表头必须逐格进 cells；② 这些表头词不能本来就混在 rows 里
    # （否则这条断言是空的，旧写法也能过）。
    try:
        _HDR51 = [u'表头甲独特词', u'表头乙独特词', u'表头丙独特词']
        _ROWS51 = [[u'甲行', u'11', u'22'], [u'乙行', u'33', u'44']]
        _pg51 = [{'role': 'cover', 'headline': u'封面', 'body': []},
                 {'role': 'divider', 'headline': u'第一章', 'body': [u'章看点']},
                 {'role': 'divider', 'headline': u'第二章', 'body': [u'章看点']}]
        # 内容页给足 40 页：表页占比 5/48≈0.10 低于 dense_share，配额走叙事册上限，
        # 真丢表才发生。夹具不许卡在密度阈值上（12 页时占比恰 0.25，全留=断言变空）。
        for _i in range(40):
            _pg51.append({'role': 'content', 'headline': u'内容页%02d' % _i,
                          'body': [u'这是第%02d页的第一句正文内容比较长' % _i,
                                   u'这是第%02d页的第二句正文内容' % _i]})
        for _j in range(5):
            _pg51.append({'role': 'content', 'headline': u'台账%02d' % _j, 'body': [],
                          'table': {'header': list(_HDR51),
                                    'rows': [list(r) for r in _ROWS51]}})
        _o51, _al51 = C.allocate_to_budget(_pg51, (10, 10))
        _dts51 = _al51.get('dropped_tables') or []
        _rowcells51 = set(c for r in _ROWS51 for c in r)
        _lost51 = sorted({h for t in _dts51 for h in _HDR51
                          if h not in set(t.get('cells') or [])})
        _nonvac51 = not any(h in _rowcells51 for h in _HDR51)
        _ok51 = bool(_dts51) and not _lost51 and _nonvac51
        notes.append(u"丢表表头进 G13 台账：真丢了 %d 张表、表头漏账 %s 个、表头词不在 rows里=%s -> %s" % (
            len(_dts51), len(_lost51), _nonvac51, u'ok' if _ok51 else u'FAIL'))
        if not _ok51:
            fails.append(u"页数契约丢弃的表格没把**表头**逐格记进 cells（漏 %s 个，丢了 %d 张表）："
                         u"表头短语既不上屏也不入账，G13 会把它们全报成丢内容（实测 30 页册 12 句）；"
                         u"表头词混进 rows 会让这条断言变空（nonvacuous=%s）" % (
                             len(_lost51), len(_dts51), _nonvac51))
    except Exception as _e51:
        notes.append(u"丢表表头台账反证没跑成：%s" % str(_e51)[:110])
        fails.append(u"丢表表头台账反证异常：%s" % str(_e51)[:90])

    # ---------------- 封面口号跨重跑继承（#52，2026-11-13 实测：改一次分页 G29 就红回去）
    # cover_slogan/cover_subtitle 是 agent 按文档主题手写进 outline 封面页的（SKILL 2d / SPEC §17），
    # 绑的是文档而不是分页；旧 plan 整份重建 outline，重跑一次口号就没了，成品主标题退回文档名。
    # 三条都要成立：① 改了分页旧口号还在；② 夹具前提（分页真的变了）；③ 换源文档就不许继承。
    try:
        import docx as _dx52
        _d52 = os.path.join(TMP, 'cover_carry')
        shutil.rmtree(_d52, ignore_errors=True)
        os.makedirs(_d52)
        _OL52 = os.path.join(_d52, 'outline.json')
        _SLOGAN = u'一张网管住三百家店'
        _CSUB = u'三方共建、按单分账的区域运营体系'

        def _mk52(path, head, n_par=14):
            _D = _dx52.Document()
            _D.add_heading(head, 0)
            _D.add_heading(u'一、门店结构', 1)
            for _i in range(n_par):
                _D.add_paragraph(u'第%d条正文：华东区域共有 %d 家门店，本年营收 %d 万元，'
                                 u'续约率维持在比较稳定的水平上。' % (_i + 1, 10 + _i, 100 + _i * 7))
            _D.add_heading(u'二、下阶段动作', 1)
            for _i in range(6):
                _D.add_paragraph(u'动作%d：把潜在池里的中小门店纳入签约，预计转化 %d 家，'
                                 u'单店月均贡献提升 %d 个百分点。' % (_i + 1, 3 + _i, 1 + _i))
            _D.save(path)
            return path

        def _plan52(docx_path, *extra):
            return subprocess.run(
                [sys.executable, engine, 'plan', docx_path, '--work', _d52,
                 '--auto-pages'] + list(extra),
                capture_output=True, text=True, encoding='utf8', errors='replace')

        _docA = _mk52(os.path.join(_d52, 'a.docx'), u'区域连锁门店经营复盘')
        _p1 = _plan52(_docA, '--per-page', '2')
        _ok1 = _p1.returncode == 0 and os.path.exists(_OL52)
        _o = json.load(io.open(_OL52, encoding='utf-8')) if _ok1 else {'pages': [{}]}
        _o['pages'][0]['cover_slogan'] = _SLOGAN
        _o['pages'][0]['cover_subtitle'] = _CSUB
        json.dump(_o, io.open(_OL52, 'w', encoding='utf-8'), ensure_ascii=False)
        _n_before = len(_o['pages'])
        _p2 = _plan52(_docA, '--per-page', '1')      # 同一份文档、换分页
        _o2 = json.load(io.open(_OL52, encoding='utf-8')) if _p2.returncode == 0 else {'pages': [{}]}
        _kept = (_o2['pages'][0].get('cover_slogan') == _SLOGAN and
                 _o2['pages'][0].get('cover_subtitle') == _CSUB)
        _shifted = len(_o2['pages']) != _n_before
        _docB = _mk52(os.path.join(_d52, 'b.docx'), u'另一份完全不同的文档')
        _p3 = _plan52(_docB, '--per-page', '2')
        _o3 = json.load(io.open(_OL52, encoding='utf-8')) if _p3.returncode == 0 else {'pages': [{}]}
        _wiped = not (_o3['pages'][0].get('cover_slogan') or u'')
        _ok52 = _ok1 and _kept and _shifted and _wiped
        notes.append(u"封面口号跨重跑继承：改分页后仍在=%s（前提：页数 %d→%d 变了=%s）、换文档不继承=%s -> %s" % (
            _kept, _n_before, len(_o2['pages']), _shifted, _wiped, u'ok' if _ok52 else u'FAIL'))
        if not _ok52:
            fails.append(u"重跑 plan 把 agent 手写的 cover_slogan/cover_subtitle 抹掉了（kept=%s "
                         u"前提成立=%s 换文档清除=%s plan rc=%d/%d/%d）：口号绑文档主题不绑分页，"
                         u"抹掉后成品主标题退回文档名、G29 开场契约当场失效" % (
                             _kept, _shifted, _wiped, _p1.returncode, _p2.returncode, _p3.returncode))
    except Exception as _e52:
        notes.append(u"封面口号继承反证没跑成：%s" % str(_e52)[:110])
        fails.append(u"封面口号继承反证异常：%s" % str(_e52)[:90])

    # 柱底标签不许顶着这一串自己的单位（v10 页 220 三根柱子底下都写 '万元…'）。
    # 拆成两断言：①逐条标签口径（这条原来没有）——数字后面的名词若属于**下一个数**，
    # 不许拿来给这个数编标签；②图仍然画得出来（防空咬：把判据改成"一律不画"也算过①）。
    _DL_lab = __import__('data_layout')
    _cl48 = [u'55.0 万元设备投入', u'22.03 万元之后还剩 32.97 万元', u'32.87 万元几乎重合']
    _labs48 = []
    for _s48 in _cl48:
        _m48b = _DL_lab.NUMRE.search(_s48)
        _u48, _e48 = _DL_lab.extend_unit(_s48, _m48b.end(), _m48b.group(2) or u'')
        _labs48.append(_DL_lab.clean_label(_s48, _s48[_m48b.start():_e48].strip()))
    _lab_prefixed = [x for x in _labs48 if x.startswith(u'万元') or x.startswith(u'元')]
    _series48 = _DL_lab.items_from_numbers(
        [u'55.0 万元设备投入', u'44.0 万元车辆折旧', u'32.87 万元保险保障'])
    _unit_bad = [x for x in _series48 if (x[0] or u'').startswith(u'万元')
                 or (x[0] or u'').startswith(u'元')]
    _unit_dup = [l for l in set([x[0] for x in _series48 if x[0]])
                 if [x[0] for x in _series48].count(l) > 1]
    # 第二条子句（'22.03 万元之后还剩 32.97 万元'）的叙述属于 32.97，标签必须为空
    _lab_ok = (not _lab_prefixed and _labs48[0] == u'设备投入' and not _labs48[1]
               and _labs48[2] and len(_series48) == 3
               and all(x[0] for x in _series48) and not _unit_bad)
    notes.append(u"柱底标签不带自己的单位：逐条=%s（顶单位 %d 个）｜合格系列仍出图=%d 条"
                 u"（顶单位 %d、同标签重复 %s）-> %s" % (
                     json.dumps(_labs48, ensure_ascii=False)[:90], len(_lab_prefixed),
                     len(_series48), len(_unit_bad), _unit_dup,
                     u'ok' if _lab_ok else u'FAIL'))
    if not _lab_ok:
        fails.append(u"柱底标签口径不成立（%s / 系列 %d 条）：'55.0 万元设备投入' 的标签应是 "
                     u"'设备投入'（不许顶着 '万元'）；'22.03 万元之后还剩 32.97 万元' 里后面那句"
                     u"属于 32.97，不许给 22.03 编标签；三条都能命名时图必须照画，"
                     u"不能靠「一律不画」绕过这条判据" % (
                         json.dumps(_labs48, ensure_ascii=False)[:120], len(_series48)))

    # ---------------- G27 字号阶梯：正证放行有锚点的册子，反证把字号压扁必须被拦
    _g27_src = os.path.join(sd_dir, 'out', [f for f in os.listdir(os.path.join(sd_dir, 'out'))
                                           if f.endswith('.pptx')][0])
    _g27_bl = json.load(io.open(os.path.join(sd_dir, 'out', 'build_log.json'), encoding='utf-8'))
    _g27_ok = C.g27_type_scale(_g27_src, C.TH, _g27_bl)
    import zipfile as _Z27
    from lxml import etree as _ET27
    _g27_flat = os.path.join(TMP, 'g27_flat.pptx')
    _zin = _Z27.ZipFile(_g27_src)
    _zout = _Z27.ZipFile(_g27_flat, 'w', _Z27.ZIP_DEFLATED)
    for _it in _zin.infolist():
        _data = _zin.read(_it.filename)
        if re.match(r'ppt/slides/slide\d+\.xml$', _it.filename):
            _root = _ET27.fromstring(_data)
            for _rpr in _root.iter('{http://schemas.openxmlformats.org/drawingml/2006/main}rPr'):
                _rpr.set('sz', '1200')
            _data = _ET27.tostring(_root)
        _zout.writestr(_it, _data)
    _zin.close(); _zout.close()
    _g27_bad = C.g27_type_scale(_g27_flat, C.TH, _g27_bl)
    # 注释载体差分：只把 caption 形状压到 12pt 必须仍放行（注释允许 12pt 起），
    # 只把 text_* 正文压到 12pt 必须拦——证明闸量的是正文行而不是误伤注释。
    # 注意：tbl_dir 这个变量在 G19 一节被重新指到 g19_table 夹具，这里必须写死 'tbl' 本尊。
    _tbl27 = os.path.join(TMP, 'tbl', 'out', 'deck.pptx')
    _tbl27_bl = json.load(io.open(os.path.join(TMP, 'tbl', 'out', 'build_log.json'),
                                  encoding='utf-8'))
    _P27 = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
    _A27 = '{http://schemas.openxmlformats.org/drawingml/2006/main}'

    def _g27_mut(dst, pred, sz):
        zi = _Z27.ZipFile(_tbl27)
        zo = _Z27.ZipFile(dst, 'w', _Z27.ZIP_DEFLATED)
        hit = 0
        for it in zi.infolist():
            d = zi.read(it.filename)
            if re.match(r'ppt/slides/slide\d+\.xml$', it.filename):
                rt = _ET27.fromstring(d)
                for sp in rt.iter(_P27 + 'sp'):
                    nv = sp.find('.//' + _P27 + 'cNvPr')
                    nm = (nv.get('name') if nv is not None else '') or ''
                    if not pred(nm):
                        continue
                    for rpr in sp.iter(_A27 + 'rPr'):
                        rpr.set('sz', sz)
                        hit += 1
                d = _ET27.tostring(rt)
            zo.writestr(it, d)
        zi.close(); zo.close()
        return hit

    _cap_p = os.path.join(TMP, 'g27_caption14.pptx')
    _cap_hit = _g27_mut(_cap_p, lambda nm: nm.startswith('caption'), '1200')
    if not _cap_hit:
        # 屏上字数预算会把超预算页的 caption 清空（解释移进备注），夹具不能依赖装配残留：
        # 直接注入一个 12pt 注释框，证明闸放行注释而不是误伤（v25 实测命中 0 就是这个原因）
        from pptx import Presentation as _P27b
        from pptx.util import Inches as _I27b, Pt as _Pt27b
        _pr27 = _P27b(_cap_p)
        _tb27 = _pr27.slides[0].shapes.add_textbox(_I27b(0.6), _I27b(6.6), _I27b(6), _I27b(0.4))
        _tb27.name = 'caption'
        _r27 = _tb27.text_frame.paragraphs[0].add_run()
        _r27.text = u'注释：口径见备注'
        _r27.font.size = _Pt27b(12)
        _pr27.save(_cap_p)
        _cap_hit = 1
    _cap_res = C.g27_type_scale(_cap_p, C.TH, _tbl27_bl) if _cap_hit else {'bad_pages': -1}
    _body_p = os.path.join(TMP, 'g27_body12.pptx')
    _body_hit = _g27_mut(_body_p, lambda nm: nm.startswith('text_'), '1200')
    _body_res = C.g27_type_scale(_body_p, C.TH, _tbl27_bl) if _body_hit else {'bad_pages': -1}
    g27_ok = (_g27_ok['bad_pages'] == 0 and _g27_bad['bad_pages'] > 0
              and _cap_hit > 0 and _cap_res['bad_pages'] == 0
              and _body_hit > 0 and _body_res['bad_pages'] > 0)
    notes.append(u"G27 字号阶梯：正常册 bad=%d；整册压到 12pt 后 bad=%d；注释压 12pt 仍放行=%s（命中 %d）；"
                 u"正文压 12pt 被拦=%s（锚点/正文下限 %d/%dpt）-> %s" % (
                     _g27_ok['bad_pages'], _g27_bad['bad_pages'], _cap_res['bad_pages'] == 0, _cap_hit,
                     _body_res['bad_pages'] > 0, C.TH['title_pt_min'], C.TH['body_pt_min'],
                     u'ok' if g27_ok else u'FAIL'))
    if not g27_ok:
        fails.append(u"G27 不咬或误伤：正常册 bad=%d、压扁册 bad=%d——字号阶梯闸必须放行有视觉锚点的册子、"
                     u"拦住整册小字浮在空底上的册子" % (_g27_ok['bad_pages'], _g27_bad['bad_pages']))

    # ---------------- G25 行高按内容：SPEC §7 那句"不许平均分"以前没有判据（2026-11-13 审计 ②），
    # 现在装配侧 row_heights 摊、成品闸 row_line_need 量，两边同一把尺。反证 = 长句行还原成等额摊法。
    from pptx import Presentation as _P25
    _rh_src = os.path.join(TMP, 'tbl', 'out', 'deck.pptx')
    _rh_bad, _rh_good = os.path.join(TMP, 'rh_equal.pptx'), os.path.join(TMP, 'rh_content.pptx')
    _pr25 = _P25(_rh_src)
    _tb25 = None
    for _sl25 in _pr25.slides:
        for _sh25 in _sl25.shapes:
            if getattr(_sh25, 'has_table', False):
                _tb25 = _sh25.table
                break
        if _tb25 is not None:
            break
    _rh_inj = 0
    if _tb25 is not None and len(_tb25.rows) >= 3:
        _tb25.cell(1, 0).text = u'这一格写了很长一句说明文字，用来验证行高是不是按内容摊开的'
        _rh_inj = 1
        _body25 = list(_tb25.rows)[1:]
        _rows25 = [[c.text for c in r.cells] for r in _body25]
        _colw25 = [w.width / float(C.EMU_W) for w in _tb25.columns]
        _bud25 = sum(int(r.height) for r in _body25) / 914400.0
        _pr25.save(_rh_bad)                       # 行高还相等 → 必须被点名
        _new25 = C.row_heights(_rows25, _colw25, _bud25)
        for _r25, _h25 in zip(_body25, _new25):
            _r25.height = _h25
        _pr25.save(_rh_good)
        _need25 = C.row_line_need(_rows25, _colw25)
        _rh_varied = (max(_need25) >= 2 * max(1, min(_need25)) and len(set(_new25)) > 1)
        _rh_sum_ok = abs(sum(_new25) / 914400.0 - _bud25) <= 0.02
    else:
        _need25, _new25, _bud25 = [], [], 0.0
        _rh_varied = _rh_sum_ok = False
    _rh_why = lambda res: [w for t in res.get('bad') or [] for w in t.get('why') or []
                           if u'行高平均分' in w]
    _rh_bad_w = _rh_why(C.g25_table_design(_rh_bad, C.TH)) if _rh_inj else [u'夹具没做成']
    _rh_good_w = _rh_why(C.g25_table_design(_rh_good, C.TH)) if _rh_inj else []
    g25_rh_ok = bool(_rh_inj and _rh_bad_w and not _rh_good_w and _rh_varied and _rh_sum_ok)
    notes.append(u"G25 行高按内容：夹具换行需求=%s｜等额摊反证被点名=%s｜按内容摊放行=%s｜"
                 u"预算守恒=%s(%.2f→%.2f 英寸) -> %s" % (
                     u'/'.join(map(str, _need25)) or u'-', bool(_rh_bad_w),
                     not _rh_good_w, _rh_sum_ok, _bud25,
                     sum(_new25) / 914400.0, u'ok' if g25_rh_ok else u'FAIL'))
    if not g25_rh_ok:
        fails.append(u"G25 行高判据不成立：把长句行按等额摊回去必须被拦（实测命中=%d，理由 %s），"
                     u"按内容摊必须放行（实测误报=%s），且行高必须真随内容变化（需求 %s）、"
                     u"总高不变（%.2f→%.2f 英寸）——装配或闸有一边是哑的" % (
                         len(_rh_bad_w), (_rh_bad_w or [u''])[0][:60], _rh_good_w[:1],
                         u'/'.join(map(str, _need25)) or u'-', _bud25,
                         sum(_new25) / 914400.0))

    # ---------------- 跨体裁反证（用户 2026-11-15 定死：技能适合所有类型的 PPT，不能只会做路演）
    import deck_parity as DP
    # (a) 表格配额按文档密度：台账类必须全留，叙事类才限额。反证=把配额尺子调回"一律 3 张"必须丢表。
    def _mk_tbl_pages(n, n_content=6):
        ps = [{'page': 1, 'role': 'cover'}, {'page': 2, 'role': 'closing'}]
        for k in range(n):
            ps.append({'page': 3 + k, 'role': 'table', 'table': {'header': [u'项目', u'结果'],
                                                                 'rows': [[u'第%d项' % (i + 1), u'合格']
                                                                            for i in range(4)]}})
        for k in range(n_content):
            ps.append({'page': 3 + n + k, 'role': 'data',
                       'body': [u'第%d条说明文字，用于占位内容页。' % (k + 1)]})
        return ps

    _th_saved = dict(C.TH)
    try:
        _kept_dense, _ = C.allocate_to_budget(_mk_tbl_pages(7, 4), (14, 14))
        _dense_tbl = sum(1 for p in _kept_dense if p.get('table'))
        C.TH['table_quota_dense_share'] = 0.99      # 把"表格即内容"那条线抬到够不着
        _kept_narr, _ = C.allocate_to_budget(_mk_tbl_pages(7, 4), (14, 14))
        _narr_tbl = sum(1 for p in _kept_narr if p.get('table'))
        C.TH['table_quota_narrative'] = 99          # 反向：上限放开必须真的全留
        _kept_open, _ = C.allocate_to_budget(_mk_tbl_pages(7, 4), (14, 14))
        _open_tbl = sum(1 for p in _kept_open if p.get('table'))
    finally:
        C.TH.clear()
        C.TH.update(_th_saved)
    genre_quota_ok = (_dense_tbl == 7 and _narr_tbl == 3 and _open_tbl == 7)
    notes.append(u"跨体裁·表格配额：台账密度册留 %d/7 张｜调回叙事配额上限留 %d 张（旧口径=一律 3）｜"
                 u"上限放开留 %d 张 -> %s" % (_dense_tbl, _narr_tbl, _open_tbl,
                                              'ok' if genre_quota_ok else 'FAIL'))
    if not genre_quota_ok:
        fails.append(u"表格配额不是按文档密度定的（密度册=%d、叙事册=%d、放开=%d，应 7/3/7）："
                    u"制度手册/巡检台账这类'表格就是内容'的体裁会被按路演册的期待砍表" % (
                        _dense_tbl, _narr_tbl, _open_tbl))

    # (b) 同级尺的"体裁不适用"必须分摊权重，不能按 0 分记；反证=把池子清空必须触发 NA
    _orig_measure = DP.measure_one
    try:
        DP.measure_one = lambda *a, **k: {'deck': 'x', 'pages': 6, 'n_non_tbl': 0,
                                          'n_with_num': 0, 'chars_p50': 0, 'chars_p90': 0,
                                          'hero_rate': 0.0, 'picture_rate': 1.0,
                                          'graphic_rate': 1.0}
        _na_sc = DP.score(r'__不存在的册子__')
    finally:
        DP.measure_one = _orig_measure
    _na_live = [d for d in _na_sc['dims'] if not d.get('na')]
    genre_na_ok = (len(_na_sc.get('na_dims') or []) == 3
                   and abs(sum(d['weight'] for d in _na_live) - 1.0) < 1e-6
                   and _na_sc['score'] >= 99.0)
    notes.append(u"跨体裁·同级尺 NA：不适用维=%s｜活维权重和=%.3f｜纯文字+图表册 score=%.1f -> %s" % (
        u'、'.join(_na_sc.get('na_dims') or []), sum(d['weight'] for d in _na_live),
        _na_sc['score'], 'ok' if genre_na_ok else 'FAIL'))
    if not genre_na_ok:
        fails.append(u"同级尺在'没有可量页'的体裁上不是分摊权重而是记 0 分（不适用维=%d 个、"
                     u"活维权重和=%.3f、score=%.1f）——技能会被判成只会做一种 PPT" % (
                         len(_na_sc.get('na_dims') or []),
                         sum(d['weight'] for d in _na_live), _na_sc['score']))

    # (c) 制度手册体裁端到端：长段落、几乎无数字、无表格，必须能出片且报告零硬伤
    gz_dir = os.path.join(TMP, 'genre_zhidu')
    shutil.rmtree(gz_dir, ignore_errors=True)
    os.makedirs(gz_dir)
    import docx as _dx_gz
    _gd = _dx_gz.Document()
    _gd.add_heading(u'岗位管理制度（体裁探针文档）', 0)
    for _sec, _ps in ((u'一、总则', [u'本制度适用于公司全体部门与外包团队，自发布之日起执行，旧版同时废止。'
                                    u'归口管理部门负责解释与修订，修订须经办公会审议通过。',
                                    u'各部门应当在本制度框架内细化操作流程，并把执行情况纳入季度检查范围。'
                                    u'检查发现的问题要限期整改，整改情况作为年度考核依据。']),
                      (u'二、岗位职责', [u'岗位设置坚持因事设岗、以经定责，任何岗位不得承担与本职无关的事务。'
                                       u'人员调整须提前报备，交接期不少于五个工作日，交接清单双方签字确认。',
                                       u'跨部门事项由牵头部门召集相关方协商，协商不成的报分管负责人裁定。']),
                      (u'三、监督与问责', [u'监督采取日常抽查与专项检查相结合的方式，抽查结果通报全公司。',
                                          u'对违反制度的行为按情节轻重给予提醒、通报、绩效扣减或岗位调整处理。'])):
        _gd.add_heading(_sec, 1)
        for _p in _ps:
            _gd.add_paragraph(_p)
    _gdoc = os.path.join(gz_dir, 'zhidu.docx')
    _gd.save(_gdoc)
    _gw = os.path.join(gz_dir, 'work')
    for _ga in (['plan', _gdoc, '--pages', '10', '--work', _gw],
                ['refine', '--work', _gw],
                ['ack', '--work', _gw, '--declined', '--note',
                 u'本机自测夹具(制度体裁探针，非用户交付决定)'],
                ['build', '--work', _gw]):
        subprocess.run([sys.executable, engine] + _ga, capture_output=True,
                       text=True, encoding='utf8', errors='replace')
    _gdeck = os.path.join(_gw, 'out', 'deck.pptx')
    _grep = os.path.join(_gw, 'report', u'验收报告.md')
    _ghard = []
    if os.path.exists(_grep):
        _ghard = [l for l in io.open(_grep, encoding='utf-8').read().split(u'\n')
                  if l.startswith(u'- G')]
    _gsc = DP.score(_gdeck) if os.path.exists(_gdeck) else {'score': None, 'dims': []}
    genre_doc_ok = (os.path.exists(_gdeck) and not _ghard
                    and _gsc.get('score') is not None and _gsc['score'] >= 78)
    notes.append(u"跨体裁·制度手册端到端：出片=%s 同级分=%s 报告硬伤=%d（%s）-> %s" % (
        os.path.exists(_gdeck), _gsc.get('score'), len(_ghard),
        (_ghard[:1] or [u''])[0][:50], 'ok' if genre_doc_ok else 'FAIL'))
    if not genre_doc_ok:
        fails.append(u"纯文字制度类文档跑不出合格成品（出片=%s、同级分=%s、硬伤 %d 条：%s）——"
                    u"技能被绑在数据密集的路演册上" % (
                        os.path.exists(_gdeck), _gsc.get('score'), len(_ghard),
                        u'；'.join(h[:60] for h in _ghard[:2])))


    import dewatermark as DW28
    from PIL import Image as _IM28, ImageDraw as _ID28, ImageFont as _IF28
    _wm_dir = os.path.join(TMP, 'wm_gen', 'gen', 'p001')
    os.makedirs(_wm_dir, exist_ok=True)
    _wm_img = os.path.join(_wm_dir, 'image.png')
    _base28 = _IM28.new('RGB', (1280, 720), (9, 25, 43))
    _d28 = _ID28.Draw(_base28)
    try:
        _f28 = _IF28.truetype('C:/Windows/Fonts/simhei.ttf', 46)
    except Exception:
        _f28 = _IF28.load_default()
    _d28.text((960, 640), u'豆包 AI 生成', font=_f28, fill=(235, 240, 248))
    _base28.save(_wm_img)
    _blog28 = [{'page': 1, 'bg': _wm_img.replace('\\', '/')}]
    _g28_bad = C.g28_watermark({'out': os.path.join(TMP, 'wm_gen', 'out')}, C.TH, _blog28)
    _g28_band = DW28.band_marks_fast(_IM28.open(_wm_img))
    _g28_img = DW28.image_marks(_IM28.open(_wm_img))
    # HUD 假命中反证：投放提示词本来就要求画"半透明信息容器 + 刻度虚线"，实测这种干净图
    # 在满角区扫描下会被判成带标（7325px 假命中），整册回收会被误拦。新尺子只认贴右下角根、
    # 同一基线 ≥2 块的字形簇——同一张 HUD 图必须放行，真角标图必须照样拦。
    _hud28 = _IM28.new('RGB', (2848, 1600), (9, 25, 43))
    _hd28 = _ID28.Draw(_hud28)
    _hd28.rectangle([1990, 1340, 2790, 1434], outline=(70, 150, 220), width=6)
    for _k in range(9):
        _hd28.line([(2010 + _k * 85, 1368), (2055 + _k * 85, 1352)],
                   fill=(120, 200, 240), width=12)
    _hud_img = os.path.join(_wm_dir, 'hud.png')
    _hud28.save(_hud_img)
    _hud_band = DW28.band_marks_fast(_IM28.open(_hud_img))
    _hud_marks = DW28.image_marks(_IM28.open(_hud_img))
    _hud_blog = [{'page': 9, 'bg': _hud_img.replace('\\', '/')}]
    _g28_hud = C.g28_watermark({'out': os.path.join(TMP, 'wm_gen', 'out')}, C.TH, _hud_blog)
    _clean28 = os.path.join(_wm_dir, 'image_clean.png')
    _res28 = DW28.clean_image(_wm_img, _clean28)
    _blog28b = [{'page': 1, 'bg': _clean28.replace('\\', '/')}]
    _g28_ok = C.g28_watermark({'out': os.path.join(TMP, 'wm_gen', 'out')}, C.TH, _blog28b)
    # 调色派生副本逃逸反证：装配把 gen/ 图调色后另存 out/graded/pNNN.jpg，bg 就成了派生路径，
    # 只有 bg_src 还指着投放产物——闸必须按来源认，不然带角标的 AI 底图一调色就漏网
    _graded28 = os.path.join(TMP, 'wm_gen', 'out', 'graded', 'p001.jpg')
    os.makedirs(os.path.dirname(_graded28), exist_ok=True)
    _IM28.open(_wm_img).convert('RGB').save(_graded28)
    _g28_graded = C.g28_watermark(
        {'out': os.path.join(TMP, 'wm_gen', 'out')}, C.TH,
        [{'page': 1, 'bg': _graded28.replace('\\', '/'),
          'bg_src': _wm_img.replace('\\', '/')}])
    g28_ok = (len(_g28_band) > 0 and len(_g28_img) > 0 and _g28_bad['bad_pages'] >= 1
              and _res28.get('clean') and _g28_ok['bad_pages'] == 0
              and _g28_graded['bad_pages'] >= 1
              and len(_hud_band) > 0 and len(_hud_marks) == 0 and _g28_hud['bad_pages'] == 0)
    notes.append(u"G28 水印清除：注入'豆包 AI 生成'角标 → 带检命中=%d、新尺=%d、闸拦 bad_pages=%d；"
                 u"dewatermark 跑过后闸放行 bad_pages=%d（机验 clean=%s）；调色派生副本按 bg_src "
                 u"追认 bad_pages=%d；HUD 容器+刻度虚线 旧尺假命中=%d 新尺=%d 闸放行=%d -> %s" % (
                     len(_g28_band), len(_g28_img), _g28_bad['bad_pages'], _g28_ok['bad_pages'],
                     _res28.get('clean'), _g28_graded['bad_pages'],
                     len(_hud_band), len(_hud_marks), _g28_hud['bad_pages'],
                     u'ok' if g28_ok else u'FAIL'))
    if not g28_ok:
        fails.append(u"G28 不咬或误伤：注入角标带检=%d、新尺=%d、闸拦=%d、去标后闸放行=%d、工具自验=%s、"
                     u"派生副本闸拦=%d、HUD 旧尺=%d/新尺=%d/闸=%d——投放产物带平台角标必须拦"
                     u"（含调色后的派生副本），去净后与 HUD 面板/刻度虚线必须放行" % (
                         len(_g28_band), len(_g28_img), _g28_bad['bad_pages'],
                         _g28_ok['bad_pages'], _res28.get('clean'), _g28_graded['bad_pages'],
                         len(_hud_band), len(_hud_marks), _g28_hud['bad_pages']))

    # 视频侧同一条尺子：合成带漂移角标的短片必须被闸拦，逐帧 TELEA 必须在几十秒内清干净，
    # 且 ProPainter 深度路线只能显式点名——它本机实测跑满 78 分钟还没吐帧，默认跑等于把一页一轮钉死。
    _vv_dir = os.path.join(TMP, 'wm_vid')
    _vv_frames = os.path.join(_vv_dir, 'frames')
    shutil.rmtree(_vv_dir, ignore_errors=True)
    os.makedirs(_vv_frames)
    _vsrc = os.path.join(_vv_dir, 'gen', 'p002', 'video.mp4')
    os.makedirs(os.path.dirname(_vsrc))
    _vclean = os.path.join(_vv_dir, 'video_clean.mp4')
    _g28_vid = _res_vid = _skip = None
    _vid_ok = False
    try:
        import dewatermark as _DWV
        import numpy as np
        _ffv = _DWV._ff()
        for _i in range(24):
            _im = _IM28.new('RGB', (1280, 720), (9, 25, 43))
            _dd = _ID28.Draw(_im)
            _dd.line([(0, 300 + _i * 3), (1280, 240 + _i * 3)], fill=(30, 90, 160), width=4)
            _dd.text((960 + _i * 2, 640), u'豆包 AI 生成', font=_f28, fill=(235, 240, 248))
            _im.save(os.path.join(_vv_frames, '%04d.png' % _i))
        subprocess.run([_ffv, '-v', 'error', '-y', '-framerate', '24',
                        '-i', os.path.join(_vv_frames, '%04d.png'),
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', _vsrc],
                       capture_output=True, check=True)
        _g28_vid = C.g28_watermark({'out': os.path.join(_vv_dir, 'out')}, C.TH,
                                   [{'page': 2, 'video': _vsrc.replace('\\', '/')}])
        _def_pp = _DWV.clean_video_propainter
        _DWV.clean_video_propainter = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError(u'深度路线被默认调起'))
        try:
            _res_vid = _DWV.clean_video(_vsrc, _vclean)
        finally:
            _DWV.clean_video_propainter = _def_pp
        _skip = _DWV.sample_video_marks(_vclean, 12)
        _raw_hits = _DWV.sample_video_marks(_vsrc, 12)
        # 合成夹具上"位置一致簇"这把尺对修复边缘仍会命中（12→12），不能拿它当减量指标；
        # 真正该量的是角区笔画像素被重画掉多少（实测 8604→1036）。要求减过半、如实报、交出 --deep。
        def _stroke_px(_p):
            _png = os.path.join(_vv_dir, 'probe_%s.png' % os.path.basename(_p))
            subprocess.run([_ffv, '-v', 'error', '-y', '-ss', '0.2', '-i', _p,
                            '-frames:v', '1', _png], capture_output=True, check=True)
            _a = np.asarray(_IM28.open(_png).convert('RGB'))[:, :, ::-1].copy()
            _h, _w = _a.shape[:2]
            _m = _DWV.frame_text_mask(_a[int(_h * 0.84):_h, int(_w * 0.70):_w],
                                      lift_min=6, gray_min=60, min_area=60, min_fill=0.08)
            return int((_m > 0).sum())
        _px_raw, _px_clean = _stroke_px(_vsrc), _stroke_px(_vclean)
        _vid_ok = (_g28_vid['bad_pages'] >= 1 and _res_vid.get('method') == 'classic-telea'
                   and _px_clean < _px_raw * 0.5 and bool(_res_vid.get('deep_cmd'))
                   and (not _res_vid.get('clean') or _skip <= 1))
    except Exception as _ev:
        notes.append(u'G28 视频侧没跑成：%s' % str(_ev)[:120])
    if not _vid_ok:
        fails.append(u"G28 视频侧不咬或超范围：闸拦=%s、走的是=%s、角区笔画像素 %s→%s、"
                     u"给了 deep 命令=%s（要求注入必拦、默认逐帧 TELEA 且重画掉过半笔画、"
                     u"未过就如实报并交出 --deep；ProPainter 只许显式点名）" % (
                         _g28_vid and _g28_vid['bad_pages'],
                         _res_vid and _res_vid.get('method'),
                         locals().get('_px_raw'), locals().get('_px_clean'),
                         bool(_res_vid and _res_vid.get('deep_cmd'))))
    else:
        notes.append(u"G28 视频侧：注入漂移角标 → 闸拦 bad_pages=%d；默认路线=%s（%.0f 帧、"
                     u"角区笔画像素 %d→%d、簇命中 %d→%d、机验=%s、已交出 --deep 命令）；"
                     u"深度路线未被动默认调起 -> ok" % (
                         _g28_vid['bad_pages'], _res_vid.get('method'),
                         _res_vid.get('frames') or 0, _px_raw, _px_clean,
                         _raw_hits, _skip, _res_vid.get('clean')))

    # 去标疤夹具：真素材上逐帧 TELEA 把压在霓虹弧上的角标抹成了一块黑（2026-11-08 实测：
    # 原片角区近黑像素中位 0，清完 9470，8 个时间点逐帧目检比水印还扎眼），而"亮笔画还在不在"
    # 那把尺对这种片子是 0 命中——所以必须有第二把尺。夹具直接把"补完只剩黑"的产物造出来：
    # 笔画尺必须放行、疤尺必须拦、闸必须拦；同一把疤尺在上一组的干净素材上必须放行（防误伤）。
    _vb_dir = os.path.join(TMP, 'wm_blob')
    _vb_ok = False
    try:
        from PIL import ImageFilter as _GF
        _vb_frames = os.path.join(_vb_dir, 'frames')
        _vs_frames = os.path.join(_vb_dir, 'scar_frames')
        _vc_frames = os.path.join(_vb_dir, 'clean_frames')
        _vb_src = os.path.join(_vb_dir, 'orig.mp4')
        _vb_out = os.path.join(_vb_dir, 'scar.mp4')
        _vb_clean = os.path.join(_vb_dir, 'clean.mp4')
        shutil.rmtree(_vb_dir, ignore_errors=True)
        os.makedirs(_vb_frames)
        os.makedirs(_vs_frames)
        os.makedirs(_vc_frames)
        for _i in range(24):
            _im = _IM28.new('RGB', (1280, 720), (9, 25, 43))
            _dd = _ID28.Draw(_im)
            _dd.line([(700, 672 - _i), (1280, 612 + _i)], fill=(190, 235, 255), width=26)
            _dd.text((960 + _i * 2, 640), u'豆包 AI 生成', font=_f28, fill=(255, 255, 255))
            _im.save(os.path.join(_vb_frames, '%04d.png' % _i))
            _sc = _IM28.new('RGB', (1280, 720), (9, 25, 43))
            _sd = _ID28.Draw(_sc)
            _sd.line([(700, 672 - _i), (1280, 612 + _i)], fill=(190, 235, 255), width=26)
            _sd.rectangle([880 + _i * 2, 604, 1280, 716], fill=(6, 10, 16))
            _bx = (860, 600, 1280, 720)
            _sc.paste(_sc.crop(_bx).filter(_GF.GaussianBlur(12)), _bx)
            _sc.save(os.path.join(_vs_frames, '%04d.png' % _i))
            _cl = _IM28.new('RGB', (1280, 720), (9, 25, 43))
            _cd = _ID28.Draw(_cl)
            _cd.line([(700, 672 - _i), (1280, 612 + _i)], fill=(190, 235, 255), width=26)
            _cl.save(os.path.join(_vc_frames, '%04d.png' % _i))
        for _fr, _dst in ((_vb_frames, _vb_src), (_vs_frames, _vb_out), (_vc_frames, _vb_clean)):
            subprocess.run([_ffv, '-v', 'error', '-y', '-framerate', '24',
                            '-i', os.path.join(_fr, '%04d.png'),
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', _dst],
                           capture_output=True, check=True)
        _sm_b = _DWV.smear_vs_original(_vb_out, _vb_src) or {}
        _stroke_b = _DWV.sample_video_marks(_vb_out, 12)
        # 闸要拿 AI产物 的原件对照：搭一份 work/outline.json + AI产物/p003
        _docx_b = os.path.join(_vb_dir, '文档.docx')
        _keep_b = os.path.join(C.project_assets_dir(_docx_b, create=False), 'AI产物', 'p003')
        os.makedirs(_keep_b)
        shutil.copy2(_vb_src, os.path.join(_keep_b, 'video.mp4'))
        _work_b = os.path.join(_vb_dir, 'work')
        os.makedirs(os.path.join(_work_b, 'gen', 'p003'))
        _outline_b = os.path.join(_work_b, 'outline.json')
        with io.open(_outline_b, 'w', encoding='utf-8') as _ofb:
            _ofb.write(json.dumps({'docx': _docx_b}))
        _gen_b = os.path.join(_work_b, 'gen', 'p003', 'video.mp4')
        shutil.copy2(_vb_out, _gen_b)
        _wp_b = {'outline': _outline_b, 'out': os.path.join(_work_b, 'out')}
        _gate_b = C.g28_watermark(_wp_b, C.TH,
                                  [{'page': 3, 'video': _gen_b.replace('\\', '/')}])
        # 防误伤对照：同一条亮带、没有角标也没有疤的干净片子，闸与疤尺都必须放行
        _keep_a = os.path.join(C.project_assets_dir(_docx_b, create=False), 'AI产物', 'p002')
        os.makedirs(_keep_a)
        shutil.copy2(_vb_clean, os.path.join(_keep_a, 'video.mp4'))
        os.makedirs(os.path.join(_work_b, 'gen', 'p002'))
        _gen_a = os.path.join(_work_b, 'gen', 'p002', 'video.mp4')
        shutil.copy2(_vb_clean, _gen_a)
        _gate_a = C.g28_watermark(_wp_b, C.TH,
                                  [{'page': 2, 'video': _gen_a.replace('\\', '/')}])
        _sm_a = _DWV.smear_vs_original(_vb_clean, _vb_clean) or {}
        _vb_ok = (bool(_sm_b.get('smear')) and not _sm_a.get('smear')
                  and _gate_b['bad_pages'] >= 1 and _gate_a['bad_pages'] == 0)
        notes.append(u"G28 去标疤：'补完只剩黑'的产物 疤尺 delta=%s（判%s；真素材实测 delta=9470 "
                     u"而亮笔画尺只剩 2/16，靠这把才拦得住）、闸拦 bad_pages=%d（笔画尺命中 %d）；"
                     u"同一把尺在无角标无疤的干净片上 delta=%s 放行 bad_pages=%d -> %s" % (
                         _sm_b.get('delta'), u'有疤' if _sm_b.get('smear') else u'无疤',
                         _gate_b['bad_pages'], _stroke_b,
                         _sm_a.get('delta'), _gate_a['bad_pages'],
                         u'ok' if _vb_ok else u'FAIL'))
    except Exception as _eb:
        notes.append(u'G28 去标疤夹具没跑成：%s' % str(_eb)[:120])
    if not _vb_ok:
        fails.append(u"G28 去标疤不咬或误伤：夹具=%s——'笔画没了但补成黑疤'必须被拦"
                     u"（拿 AI产物 原件比角区近黑像素），classic 清干净的素材必须放行" % _vb_ok)

    # G29 开场解剖反证（SPEC §17）：坏封面（animScale 推镜满铺图 + 文档名主标题 + 无四件套 +
    # 过渡页无看点）必须被咬；好封面（满铺 MEDIA 视频 + 口号主标题 + 四件套 + 过渡页看点）必须放行。
    # 顺带验 push_in_video 真能产出满铺推镜 mp4 + 末帧 poster（取代旧 animScale 兜底）。
    _g29_ok = False
    try:
        from pptx import Presentation as _Prs
        from pptx.util import Inches as _In
        _g29_dir = os.path.join(TMP, 'g29')
        os.makedirs(_g29_dir, exist_ok=True)
        _g29_img = os.path.join(_g29_dir, 'bg.png')
        # 纯色底 zoompan 出来的 mp4 压缩后 <64KB 会被当占位文件拒；夹具用噪点图保证真出片
        import random as _rnd
        _g29_im = Image.new('RGB', (1920, 1080))
        _g29_im.putdata([(_rnd.randint(0, 60), _rnd.randint(20, 90), _rnd.randint(40, 140))
                         for _ in range(1920 * 1080)])
        _g29_im.save(_g29_img)
        _mp4, _poster = C.push_in_video(_g29_img, os.path.join(_g29_dir, 'pi.mp4'),
                                        os.path.join(_g29_dir, 'pi_poster.jpg'), dur_s=2)
        _pi_ok = bool(_mp4 and os.path.getsize(_mp4) > 65536 and _poster and os.path.exists(_poster))

        def _cover_slide(_prs, _good):
            _sl = _prs.slides.add_slide(_prs.slide_layouts[6])
            if _good:
                C.attach_video(_sl, _mp4, _poster, dur_ms=2000)
                _title = u'把空着的产能开到楼下'
            else:
                _pic = C.add_fullbleed_picture(_sl, _g29_img)
                C.inject_emphasis_scale(_sl, _pic.shape_id, by_pct=6, dur_ms=12000)
                _title = u'测试文档名'
            C.add_text(_sl, _title, [0.1, 0.2, 0.8, 0.2], 48, bold=True, name='title')
            if _good:
                C.add_text(_sl, u'测试文档名', [0.1, 0.1, 0.8, 0.08], 16, name='kicker')
                C.add_text(_sl, u'价值主张副标题', [0.1, 0.45, 0.8, 0.08], 20, name='subtitle')
                C.add_text(_sl, u'市场 | 模式 | 财务', [0.1, 0.55, 0.8, 0.06], 14, name='chips')
                C.add_text(_sl, u'编制：项目组', [0.1, 0.65, 0.8, 0.06], 15, name='byline')
            return _sl

        def _divider_slide(_prs, _good):
            _sl = _prs.slides.add_slide(_prs.slide_layouts[6])
            if _good:
                # 教程 3056573a 的过渡页手法：巨型白斜体单行编号 + 取色渐变右块（在文案下层）
                import divider_art as DA
                _n = C.add_text(_sl, u'01', [0.06, 0.10, 0.34, 0.72], 120, bold=True,
                                color='FFFFFF', name='chapter_no')
                _n.text_frame.word_wrap = False
                for _pp in _n.text_frame.paragraphs:
                    for _rr in _pp.runs:
                        _rr.font.italic = True
                DA.add_gradient_block(_sl, [0.36, 0.22, 0.60, 0.60], '255689')
                C.add_text(_sl, u'一句话', [0.40, 0.34, 0.52, 0.16], 36, bold=True, name='title')
                C.add_text(_sl, u'中国的医院分两种', [0.40, 0.60, 0.52, 0.08], 16, name='highlight')
                return _sl
            C.add_text(_sl, u'01', [0.08, 0.3, 0.3, 0.15], 40, bold=True, name='chapter_no')
            C.add_text(_sl, u'一句话', [0.08, 0.45, 0.6, 0.15], 36, bold=True, name='title')
            return _sl

        _outline = {'pages': [{'role': 'cover', 'headline': u'测试文档名'},
                              {'role': 'divider', 'headline': u'一句话'}]}
        _bad_prs = _Prs(); _bad_prs.slide_width = _In(13.333); _bad_prs.slide_height = _In(7.5)
        _cover_slide(_bad_prs, False); _divider_slide(_bad_prs, False)
        _bad_p = os.path.join(_g29_dir, 'bad.pptx'); _bad_prs.save(_bad_p)
        _g29_bad = C.g29_opening(_bad_p, _outline, C.TH)
        _good_prs = _Prs(); _good_prs.slide_width = _In(13.333); _good_prs.slide_height = _In(7.5)
        _cover_slide(_good_prs, True); _divider_slide(_good_prs, True)
        _good_p = os.path.join(_g29_dir, 'good.pptx'); _good_prs.save(_good_p)
        _g29_good = C.g29_opening(_good_p, _outline, C.TH)
        _g29_ok = (_pi_ok and _g29_bad['bad_pages'] >= 2
                   and 1 in _g29_bad['animscale_bg_pages'] and 2 in _g29_bad['divider_no_highlight']
                   and _g29_good['bad_pages'] == 0 and not _g29_good['animscale_bg_pages'])
        notes.append(u"G29 开场解剖：push_in_video 出片=%s；坏封面 bad=%d（animScale页%s/无看点页%s）、"
                     u"好封面 bad=%d -> %s" % (
                         _pi_ok, _g29_bad['bad_pages'], _g29_bad['animscale_bg_pages'],
                         _g29_bad['divider_no_highlight'], _g29_good['bad_pages'],
                         u'ok' if _g29_ok else u'FAIL'))
    except Exception as _e29:
        notes.append(u'G29 开场解剖反证没跑成：%s' % str(_e29)[:160])
    if not _g29_ok:
        fails.append(u"G29 不咬或误伤：animScale 推镜满铺图/文档名主标题/无四件套/过渡页无看点必须被拦，"
                     u"满铺 MEDIA 视频+口号+四件套+看点的封面必须放行；push_in_video 必须真出满铺 mp4+末帧 poster")

    # --- #48 装配五处缺陷：每处都要"注入能拦、撤销能放"，且判据只许有一个定义点 ----------
    # 这五条全是真渲染逐页目检抓出来的，而当时 29 道闸全绿——所以收口方式不是改阈值，
    # 是给每条缺陷配一条能咬的判据：
    #   (1) '26 万人次' 被单位正则截成 '26万'，G24 自己也在数截断值，于是截断成品一路过闸
    #   (2) '第一·100 万'：裸序数当筹码标签（'第三方'/'第一季度' 这类真词不许误杀）
    #   (3) 筹码把标题原句再抄一遍（同页重影）
    #   (4) 图形页叠第二行筹码——安全网早就不许，keep 路径却没同一条判据
    #   (5) 表外结论行被夹回留白区，压在表格最后两行上（只有量几何才看得见）
    import data_layout as _DL48
    import display_refine as _DR48
    _cf48 = io.open(engine, encoding='utf-8').read()
    _dl48 = io.open(os.path.join(HERE, 'data_layout.py'), encoding='utf-8').read()
    _it48 = _DL48.items_from_numbers([u'妇科一年 26 万人次', u'产科一年 3 万人次'])
    _fu48 = _DR48.fact_units(u'妇科一年筛查 26 万人次，成交 3 万人次。')
    ok_c1 = ([i[2] for i in _it48] == [u'26 万人次', u'3 万人次'] and len(_it48) == 2
             and _fu48 == [u'26 万人次', u'3 万人次']
             and _DL48.unit_of(u'46.9 万元') == u'万元'
             and _DL48.items_from_numbers([u'毛利 46.9 万元', u'筛查 18,000 人次']) == [])
    # 反证：拿改动前那份单位正则现算，证明"截断"改前真的存在（不是编出来的对照组）
    _old48 = re.compile(u'([-+]?\\d[\\d,\\.]*\\s*)'
                        u'(%|％|万元|亿元|万|亿|元|台|例|人|次|天|城|店|个|倍|家)?')
    ok_c1r = _old48.search(u'妇科一年 26 万人次').group(0).strip() == u'26 万'
    ok_c2 = (not any(_DR48.is_clean_phrase(x) for x in (u'第一', u'第二', u'第10', u'十二'))
             and all(_DR48.is_clean_phrase(x) for x in (u'第三方', u'第一季度', u'妇科一年'))
             and not _DL48.is_category_label(u'第一') and _DL48.is_category_label(u'第三方'))
    ok_c3 = (C.chip_dupes_head(u'四个条件今年同时成立·12', u'四个条件今年同时成立，去年不是这样')
             and not C.chip_dupes_head(u'成本端还有一条敞口·19 元', u'回收期短到 1.5 年至 2.7 年')
             and u'not chip_dupes_head(_c, head)' in _cf48)
    ok_c4 = (_cf48.count(u"('hero', 'numbers', 'bars', 'deltas')") == 1
             and _cf48.count(u'GRAPHIC_LAYOUTS') >= 4
             and u'drew not in GRAPHIC_LAYOUTS' in _cf48.split(u'_seen = re.sub')[1]
             and _dl48.count(u"MEASURE_CHARS = u'") == 1)

    def _tbl_slide48(concl_top):
        """一页 = 一张 3 行表（行高之和 0.57、表底 0.84）+ 一条结论；结论顶边由参数决定。"""
        from pptx import Presentation as _Pr48
        from pptx.util import Emu as _Em48
        import zipfile as _zl48
        _p = _Pr48()
        _p.slide_width, _p.slide_height = _Em48(C.EMU_W), _Em48(C.EMU_H)
        _s = _p.slides.add_slide(_p.slide_layouts[6])
        gf = _s.shapes.add_table(3, 2, _Em48(int(0.06 * C.EMU_W)), _Em48(int(0.27 * C.EMU_H)),
                                 _Em48(int(0.88 * C.EMU_W)), _Em48(int(0.57 * C.EMU_H)))
        for _r in gf.table.rows:
            _r.height = _Em48(int(0.19 * C.EMU_H))
        C.add_text(_s, u'合计：98.0', [0.06, concl_top, 0.88, 0.075], 16, bold=True,
                   name='table_conclusion')
        _f = os.path.join(TMP, u'ov48_%d.pptx' % int(concl_top * 1000))
        _p.save(_f)
        _z = _zl48.ZipFile(_f)
        _xb = _z.read('ppt/slides/slide1.xml')
        _z.close()
        return _xb

    _ov_bad48 = C.tbl_text_overlaps(_tbl_slide48(0.755))
    _ov_good48 = C.tbl_text_overlaps(_tbl_slide48(0.87))
    ok_c5 = bool(_ov_bad48) and _ov_bad48[0][0] == u'table_conclusion' and not _ov_good48
    # (6) 标题 16 字上限：计划侧 page_budget 必须报，装配侧必须有出口裁剪（真渲染页 17
    # 交下来是 65 字整句，直接打成一个 32pt 标题，把每页字数 p90 从 34 顶到 57）
    _h648 = (u'市场愿意付的价格是每天 1500 到 3000 元——石狮一个社区卫生服务中心'
             u'对移动 DR 体检车的全包日租控制价是 1500 元')
    _pb48 = _DR48.page_budget({'headline': _h648, 'display_lines': []})
    ok_c6 = (any(u'headline' in str(x.get('why', u'')) for x in _pb48)
             and u'DR.HEADLINE_MAX' in _cf48 and u'head_show = _hc' in _cf48)
    _c_ok = ok_c1 and ok_c1r and ok_c2 and ok_c3 and ok_c4 and ok_c5 and ok_c6
    notes.append(u"#48 装配五处缺陷反证：复合量词补齐=%s（旧正则现算确为 '26万'=%s）、"
                 u"裸序数拦/真词放行=%s、筹码抄标题=%s、判据一处定义(布局元组%d处/量词表%d处)=%s、"
                 u"结论压表格 注入=%s 撤销=%s、标题 16 字上限=%s -> %s" % (
                     ok_c1, ok_c1r, ok_c2, ok_c3,
                     _cf48.count(u"('hero', 'numbers', 'bars', 'deltas')"),
                     _dl48.count(u"MEASURE_CHARS = u'"), ok_c4,
                     len(_ov_bad48), len(_ov_good48), ok_c6, u'ok' if _c_ok else u'FAIL'))
    if not _c_ok:
        fails.append(u"#48 五处装配缺陷的判据不咬或误伤：'26 万人次' 必须整串上屏且 G24 数同一串；"
                     u"'第一' 不许当标签而 '第三方' 必须放行；抄标题的筹码必须被扔；"
                     u"图形页判据与量词字表都只许有一份；结论行落进表格行高之和以内必须被拦、"
                     u"落在表下必须放行；超 HEADLINE_MAX 的整句标题必须由计划侧报出并被装配裁掉")

    # --- #55 表格页字号层级：G27 曾把 role='table' 整列豁免，它是全场唯一无字号要求的页型
    # 合成单页做注入/撤销两面，不依赖套件沙箱那册的内部状态（它会被别的改动重排）
    from pptx import Presentation as _Pr55
    from pptx.util import Emu as _Em55

    def _tbl_page55(pt):
        _p = _Pr55()
        _p.slide_width, _p.slide_height = _Em55(C.EMU_W), _Em55(C.EMU_H)
        _s = _p.slides.add_slide(_p.slide_layouts[6])
        C.add_text(_s, u'一台车一年的账（附表 2）', [0.06, 0.12, 0.88, 0.15], pt,
                   bold=True, name='title')
        _f = os.path.join(TMP, u'g27tbl_%d.pptx' % int(pt))
        _p.save(_f)
        return _f, [{'page': 1, 'role': 'table', 'display_mode': 'refined'}]

    _f55lo, _bl55 = _tbl_page55(20)
    _f55hi, _ = _tbl_page55(40)
    _g27lo = C.g27_type_scale(_f55lo, C.TH, _bl55)
    _g27hi = C.g27_type_scale(_f55hi, C.TH, _bl55)
    ok_c7 = (_g27lo.get('bad_pages') or 0) >= 1 and (_g27hi.get('bad_pages') or 0) == 0 \
        and u"'table'" not in _cf48.split(u'def g27_type_scale')[1].split(u'\ndef ')[0] \
        .split(u'skip_roles = ')[1].split(u'\n')[0]
    # G23 新成品判据「标题 ≤HEADLINE_MAX，附录标记不计」：合规句必须放行、超长整句必须拦，
    # 且豁免必须**有界**——'（附表 1）' 可以不计，但正文 19 字仍要被拦（防拿豁免夹带长句）
    def _ttl_deck56(ttl):
        from pptx import Presentation as _Pr56
        from pptx.util import Emu as _Em56
        _p = _Pr56()
        _p.slide_width, _p.slide_height = _Em56(C.EMU_W), _Em56(C.EMU_H)
        _s = _p.slides.add_slide(_p.slide_layouts[6])
        C.add_text(_s, ttl, [0.06, 0.12, 0.88, 0.15], 40, bold=True, name='title')
        _f = os.path.join(TMP, u'g23ttl_%d.pptx' % (abs(hash(ttl)) % 10 ** 8))
        _p.save(_f)
        return _f
    _bl56 = [{'page': 1, 'role': 'data', 'display_mode': 'refined'}]
    def _hit56(ttl):
        r56 = C.g23_page_budget(_ttl_deck56(ttl), C.TH, False, _bl56)
        return any(u'\u6807\u9898' in w for x in r56['over'] for w in x['why'])
    ok_c9 = (_hit56(u'\u8fd9\u6761\u8def\u6700\u76f4\u63a5\u7684\u8bf1\u60d1\u662f\u7701\u4e0b\u4e00\u5f20\u8bc1'
                    u'\u3002\u4ee3\u4ef7\u662f\uff1a') is True
             and _hit56(u'\u4e00\u53f0\u8f66\u4e00\u5e74\u7684\u8d26\uff08\u9644\u8868 2\uff09') is False
             and _hit56(u'\u5341\u4e2a\u6700\u53ef\u80fd\u8ba9\u8fd9\u4ef6\u4e8b\u5931\u8d25\u7684\u539f\u56e0'
                        u'\uff08\u9644\u8868 1\uff09') is False
             and _hit56(u'\u4e2d\u56fd\u7684\u533b\u9662\u5206\u4e24\u79cd\u3002\u4e00\u79cd\u95e8\u53e3\u6392'
                        u'\u961f\u6392\u5230\u8857\u4e0a\uff08\u9644\u8868 3\uff09') is True)
    _g55_ok = ok_c7 and ok_c9
    notes.append(u"G27 表格页不再豁免：20pt 台账页被拦=%s（bad=%s）、40pt 放行=%s（bad=%s）"
                 u"｜G23 标题上限成品闸：19字整句=%s、13字+附表标记=%s、20字+附表标记=%s -> %s" % (
                     bool((_g27lo.get('bad_pages') or 0) >= 1),
                     _g27lo.get('bad_pages'), not _g27hi.get('bad_pages'),
                     _g27hi.get('bad_pages'),
                     _hit56(u'\u8fd9\u6761\u8def\u6700\u76f4\u63a5\u7684\u8bf1\u60d1\u662f\u7701\u4e0b\u4e00'
                            u'\u5f20\u8bc1\u3002\u4ee3\u4ef7\u662f\uff1a'),
                     _hit56(u'\u4e00\u53f0\u8f66\u4e00\u5e74\u7684\u8d26\uff08\u9644\u8868 2\uff09'),
                     _hit56(u'\u4e2d\u56fd\u7684\u533b\u9662\u5206\u4e24\u79cd\u3002\u4e00\u79cd\u95e8\u53e3'
                            u'\u6392\u961f\u6392\u5230\u8857\u4e0a\uff08\u9644\u8868 3\uff09'),
                     u'ok' if _g55_ok else u'FAIL'))
    if not ok_c7:
        fails.append(u"G27 对表格页的豁免没收窄到位：role='table' 必须与封面/隔页/结尾一样守"
                     u"整页最大字 >=32pt（实测三张台账页曾停在 26pt，而语料同类数字密集页 "
                     u"p25=54）；20pt 的合成台账页必须被拦、40pt 必须放行")
    if not ok_c9:
        fails.append(u"G23 的标题上限成品闸不咬或误伤：正文超 HEADLINE_MAX 必须拦"
                     u"（g14 实测有 7 页 17-20 字标题一直在全绿里漂着），"
                     u"句尾 '（附表 N）' 不计入字数但剥完的正文仍要过线——豁免必须有界")

    nd, fr = prune_old_runs()
    if nd:
        print('沙箱收口：删除 %d 个旧 run 目录，回收 %.2f GB（保留最近 1 个；'
              'CAOFENG_SELFTEST_KEEPRUNS 可调）' % (nd, fr / 1024.0 ** 3))
    rep = os.path.join(TMP, 'gate_selftest.md')
    o = io.open(rep, 'w', encoding='utf-8')
    o.write("# 闸的反向自证\n\n")
    for n in notes:
        o.write("- " + n + "\n")
    o.write("\n## 结论\n\n")
    if fails:
        o.write("**有 %d 道闸不咬人或误伤：**\n\n" % len(fails))
        for f in fails:
            o.write("- " + f + "\n")
    else:
        o.write("**全部 %d 项检查通过：每道可测的闸都有反例被拦下、正例被放行。**\n" % len(notes))
    o.close()
    # keep a stable "latest" copy so a human (and check_docs) can find the report without knowing
    # this run's pid sandbox
    try:
        os.makedirs(TMP_ROOT, exist_ok=True)
        shutil.copyfile(rep, os.path.join(TMP_ROOT, 'gate_selftest.md'))
    except Exception:
        pass
    for n in notes:
        print("  " + n)
    print("\n报告：%s" % rep)
    if fails:
        for f in fails:
            print("FAIL " + f)
        sys.exit(1)
    # Run the doc/code reconciliation last, with the real count, so it cannot read a stale
    # self-test report (it did, once, and reported a phantom 28-vs-30 drift).
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     'check_docs.py'), str(len(notes))],
                       capture_output=True, text=True, encoding='utf8', errors='replace')
    if r.returncode != 0:
        print("FAIL 文档与代码口径漂移：")
        print((r.stdout or '')[-500:])
        sys.exit(1)
    print("OK：闸真的咬得住（%d 项），文档与代码口径一致（%d 道闸）" % (len(notes), len(cd_gates)))


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # A half-run suite that leaves no report is how a "green" day turns into a mystery.
        import traceback
        rep = os.path.join(TMP, 'gate_selftest.md')
        os.makedirs(TMP, exist_ok=True)
        io.open(rep, 'w', encoding='utf-8').write(
            u"# 闸的反向自证\n\n## 结论\n\n**自检脚本自身崩了，本次结果不可信：**\n\n```\n" +
            traceback.format_exc() + u"\n```\n")
        try:
            nd, fr = prune_old_runs()
            if nd:
                print('沙箱收口：删除 %d 个旧 run 目录，回收 %.2f GB' % (nd, fr / 1024.0 ** 3))
        except Exception:
            pass
        print("FAIL 自检脚本崩溃，报告见 %s" % rep)
        # 3 = 脚本自己崩了（结果不可信），1 = 闸拦下了不合格成品。混成一个码会让人把
        # "夹具没建起来"读成"成品被闸拒了"。
        sys.exit(3)

