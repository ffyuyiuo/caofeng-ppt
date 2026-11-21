"""caofeng-PPT engine: Word -> cinematic, editable, video-backed deck.

Subcommands
  plan    <docx> --work DIR          parse Word, emit outline.json + shots.json skeleton + tech menu
  fetch   --work DIR                 source high-res watermark-free images for the pages that need them
  pack    --work DIR                 validate the agent's prompt files against the measured grammar,
                                     emit delivery cards for the 即梦/豆包 web workflow
  build   --work DIR                 assemble the editable .pptx (full-bleed video, morph, scrim, text zones)
  check   --work DIR                 run the gates (SPEC section 7)
  shots   --work DIR                 render the built deck to PNG and build labelled contact sheets

The creative content (shot intent, prompt wording) is authored by the agent; this tool enforces
structure, writes the OOXML the corpus actually uses, and refuses to ship when a gate fails.
"""
import os, sys, json, io, re, argparse, hashlib, shutil, subprocess, tempfile
import collections, glob, math
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_tables as CT      # 实测色板 / 版式原型 / 动画 preset 三张表
import display_refine as DR      # 提炼契约：content_type / key_numbers / display_lines
import data_layout as DL        # 大数字 / 条形组 / 卡片栅格（语料那套数据摆法）
import deck_measure as DM       # 图文落位度量（与语料挖掘同一份实现）
import delivery_gate as DG      # 投放回执硬闸（SPEC §13 / G26）

# A GBK console cannot encode every character these scripts print; a cosmetic character must
# never abort a run or decide an exit code.
try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or 'utf-8', errors='backslashreplace')
    sys.stderr.reconfigure(encoding=sys.stderr.encoding or 'utf-8', errors='backslashreplace')
except Exception:
    pass

EMU_W, EMU_H = 12192000, 6858000
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_DIR = os.path.join(SKILL_DIR, 'kb')
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P14 = 'http://schemas.microsoft.com/office/powerpoint/2010/main'
P159 = 'http://schemas.microsoft.com/office/powerpoint/2015/09/main'
MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
REL_EXT = 'http://schemas.openxmlformats.org/package/2006/relationships'

ROLES = ['cover', 'divider', 'statement', 'data', 'process', 'compare', 'quote', 'closing', 'appendix']

# ---------------------------------------------------------------- utilities
def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M')


def jw(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    io.open(path, 'w', encoding='utf-8').write(json.dumps(obj, ensure_ascii=False, indent=1))


def jr(path, default=None):
    if not os.path.exists(path):
        return default
    return json.load(io.open(path, encoding='utf-8'))


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


def kb(name):
    return json.load(io.open(os.path.join(KB_DIR, name), encoding='utf-8'))


def work_paths(w):
    # absolutise once at the top: a --work given as a relative path used to be written into
    # manifest.jsonl verbatim, so the record only resolved from the directory fetch happened to
    # run in, and check reported "file missing" for an asset that was sitting right there
    w = os.path.abspath(w)
    d = {k: os.path.join(w, k) for k in
         ('outline', 'shots', 'materials', 'promptpack', 'gen', 'out', 'report')}
    d['work'] = w
    os.makedirs(w, exist_ok=True)
    for k in ('materials', 'promptpack', 'gen', 'out', 'report'):
        os.makedirs(d[k], exist_ok=True)
    d['outline'] = os.path.join(w, 'outline.json')
    d['shots'] = os.path.join(w, 'shots.json')
    return d

# ---------------------------------------------------------------- S1 plan


def doc_blocks(doc):
    """Paragraphs and tables in *document order*.

    python-docx exposes them as two separate lists, which loses where a table sat. A table that
    is read out of order gets attached to the wrong chapter (or dropped), and a deck that drops
    the 台账 table is dropping the user's numbers.
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    out = []
    for el in doc.element.body.iterchildren():
        if el.tag == W + 'p':
            p = Paragraph(el, doc)
            t = (p.text or '').strip()
            if not t:
                continue
            style = (p.style.name or '').lower()
            lvl = None
            m = re.search(r'heading\s*(\d)', style)
            if m:
                lvl = int(m.group(1))
            elif style.startswith('title'):
                lvl = 0
            out.append({'kind': 'p', 'level': lvl, 'text': t, 'style': p.style.name or ''})
        elif el.tag == W + 'tbl':
            tb = Table(el, doc)
            rows = [[c.text.strip() for c in r.cells] for r in tb.rows]
            if any(any(x for x in r) for r in rows):
                out.append({'kind': 'tbl', 'rows': rows})
    return out


def parse_page_budget(a):
    """页数契约（SPEC §21）：用户说多少页就按多少页重新梳理 Word；没说就必须先问。"""
    raw = (getattr(a, 'pages', None) or '').strip()
    brief = {'pages_raw': raw or None,
             'occasion': getattr(a, 'occasion', None) or None,
             'audience': getattr(a, 'audience', None) or None,
             'user_words': getattr(a, 'note', None) or None,
             'auto_confirmed': bool(getattr(a, 'auto_pages', False))}
    if raw:
        m = re.match(r'^(\d+)\s*[-–~到]\s*(\d+)$', raw)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
        elif raw.isdigit():
            lo = hi = int(raw)
        else:
            die('--pages 只认整数或区间（如 30 或 25-35），收到 %r' % raw)
        if lo < 6 or hi < lo or hi > 400:
            die('--pages 区间不合法（最少 6 页：封面+过渡+内容+结尾；上限 400）')
        brief['pages'] = [lo, hi]
    return brief


def allocate_to_budget(pages, budget):
    """把自然分页合并到用户给的页数：封面/过渡/结尾/表格页是结构页不动，
    内容页按章内相邻合并（正文一句不丢，丢的是"上屏篇幅"——由 refine 的压缩台账留账）。
    自然页数已少于目标时不硬凑（报告如实写实际页数）。"""
    lo, hi = budget
    target = min(hi, max(lo, 6))
    divs = [p for p in pages if p['role'] == 'divider']
    ends = [p for p in pages if p['role'] in ('cover', 'closing')]
    tables = [p for p in pages if p.get('table')]
    # 每章至少留一页内容（章内容不因预算消失），所以先扣"章数下限"，剩下的名额
    # 先给关键表，再给过渡页（从前往后留，附录过渡先减）。
    # 表格配额不按"路演最多 3 张"一刀切：技能要服务所有体裁，制度手册/巡检台账这类文档里
    # 表格就是内容本身，按叙事册的配额会把整批表丢进备注（G21 有账但成品读不到表）。
    # 配额由这份文档自己的密度决定：表页占比 ≥ dense_share 就全留，否则按叙事册上限。
    n_ch = max(1, len(divs))
    remaining = target - len(ends) - n_ch
    if remaining < 0:
        return pages, {'merged': 0, 'natural': len(pages), 'target': len(pages),
                       'infeasible': True, 'dropped_tables': [], 'dropped_dividers': []}
    _tq_cap = (len(tables) if tables and len(tables) / float(max(1, len(pages)))
               >= TH['table_quota_dense_share'] else min(len(tables), TH['table_quota_narrative']))
    tq = min(_tq_cap, remaining)
    keep_div = min(len(divs), max(0, remaining - tq))
    keep_tables = sorted(tables, key=lambda p: -len(p['table'].get('rows') or [])
                         * max(1, len((p['table'].get('header') or []))))[:tq]
    drop_tables = [p for p in tables if p not in keep_tables]
    drop_divs = divs[keep_div:]
    pages = [p for p in pages if p not in drop_tables and p not in drop_divs]
    struct = [p for p in pages if p['role'] in ('cover', 'closing', 'divider')]
    content = [p for p in pages if p not in struct and not p.get('table')]
    want_content = max(n_ch, target - len(struct) - len(keep_tables))
    if len(content) <= want_content:
        return pages, {'merged': 0, 'natural': len(pages), 'target': target}
    # 章内相邻合并：按 chapter 标记分组（divider 之后的内容页属同一章）
    groups, cur = [], []
    for p in pages:
        if p['role'] == 'divider':
            if cur:
                groups.append(cur)
            cur = []
            groups.append([p])
        elif p in struct or p.get('table'):
            # 结构页与**表页**都必须独立成组：表页混进内容组后，合并只保留 chunk[0] 的字段，
            # 表数据会被静默吞掉（实测"成本"台账整张消失，G21/G13 一起报丢）。
            if cur:
                groups.append(cur)
            cur = []
            groups.append([p])
        else:
            cur.append(p)
    if cur:
        groups.append(cur)
    merged = 0
    out = []
    cgroups = [g for g in groups
               if not (g[0]['role'] in ('cover', 'closing', 'divider') or g[0].get('table'))]
    total_c = sum(len(g) for g in cgroups) or 1
    raw = [want_content * len(g) / float(total_c) for g in cgroups]
    base = [max(1, int(x)) for x in raw]
    # 最大余数法把名额精确凑到 want_content（每章至少 1 页；章数比名额还多时凑不到，如实超）
    rem = want_content - sum(base)
    if rem > 0:
        order = sorted(range(len(raw)), key=lambda i: -(raw[i] - int(raw[i])))
        for i in order[:rem]:
            base[i] += 1
    elif rem < 0:
        order = sorted(range(len(base)), key=lambda i: -base[i])
        for i in order:
            if rem == 0:
                break
            if base[i] > 1:
                base[i] -= 1
                rem += 1
    shares = {id(g): b for g, b in zip(cgroups, base)}
    for g in groups:
        if len(g) == 1 or g[0]['role'] in ('cover', 'closing', 'divider') or g[0].get('table'):
            out.extend(g)
            continue
        share = min(shares.get(id(g), 1), len(g))
        per = max(1, int(math.ceil(len(g) / float(share))))
        for i in range(0, len(g), per):
            chunk = g[i:i + per]
            if len(chunk) == 1:
                out.append(chunk[0])
                continue
            base_pg = dict(chunk[0])
            base_pg['body'] = [s for c in chunk for s in (c.get('body') or [])]
            base_pg['source_span'] = base_pg['body']
            base_pg['entities'] = sorted({e for c in chunk for e in (c.get('entities') or [])})[:8]
            base_pg['merged_from'] = [c.get('headline', '') for c in chunk[1:]]
            out.append(base_pg)
            merged += len(chunk) - 1
    # 名额没用完（章内容比名额少）：把正文最长的内容页再拆成两页，精确凑到 target
    guard = 0
    while len(out) < target and guard < 300:
        cand = [p for p in out if p['role'] not in ('cover', 'closing', 'divider')
                and not p.get('table') and len(p.get('body') or []) >= 2]
        if not cand:
            break
        p = max(cand, key=lambda x: len(x['body']))
        i = out.index(p)
        half = len(p['body']) // 2
        a, b = dict(p), dict(p)
        a['body'] = p['body'][:half]
        a['source_span'] = a['body']
        b['body'] = p['body'][half:]
        b['source_span'] = b['body']
        b.pop('merged_from', None)
        out[i:i + 1] = [a, b]
        guard += 1
    return out, {'merged': merged, 'natural': len(pages) + len(drop_tables) + len(drop_divs),
                 'target': len(out),
                 'dropped_dividers': [p.get('headline', '') for p in drop_divs],
                 # 隔页正文也要随账：只记标题会让隔页里的说明句被 G13 当丢内容（实测 30 句）
                 'dropped_divider_bodies': [list(p.get('body') or []) for p in drop_divs],
                 'dropped_tables': [{'headline': p.get('headline', ''),
                                     'header': ' | '.join((c or '').strip()
                                                          for c in (p['table'].get('header')
                                                                    or (p['table'].get('rows') or [[]])[0]))[:60],
                                     'rows': len(p['table'].get('rows') or []),
                                     # 单元格文本要随丢表一起入账：G13 按 <a:t> 逐块核，
                                     # 不记 cells 的丢表会让每个长单元格都被报成"丢句"（实测 30 页册主因）。
                                     # 表头也要进 cells——只记 rows 会让每个表头短语（'在我们这儿干什么'
                                     # '扩张期 30 台' 这类）既没上屏又没入账，被 G13 当成丢内容（实测 12 句）。
                                     'cells': ([(c or '').strip()
                                                for c in (p['table'].get('header') or [])
                                                if (c or '').strip()] +
                                               [(c or '').strip()
                                                for _r in (p['table'].get('rows') or [])
                                                for c in (_r or []) if (c or '').strip()])}
                                    for p in drop_tables]}


def cmd_plan(a):
    from docx import Document
    wp = work_paths(a.work)
    if not str(a.docx).lower().endswith('.docx') or not os.path.exists(a.docx):
        die("输入不是存在的 .docx（%s）——先确认文件路径与扩展名，.doc/.pdf/图片都不能当输入" % a.docx)
    try:
        doc = Document(a.docx)
    except Exception as e:
        die("打不开这份 Word：%s（不是有效的 .docx？WPS 加密或在线占位文件也会这样）" % str(e)[:120])
    # 重跑 plan 会整份重建 outline，但封面口号是 agent 按文档主题手写的（SKILL 2d / SPEC §17），
    # 与分页内容无关：实测改了 --pages 重跑一次，cover_slogan/cover_subtitle 被抹掉、G29 当场红回去。
    # 先留一份旧封面账，下面按"同一份源文档"才继承。
    _old_outline = jr(wp['outline']) or {}
    seq = doc_blocks(doc)
    # Word 的目录（toc 1/2/3 样式）是导航件不是内容：条目自带制表符页码，当成正文排进片子
    # 就会出现「第2章 为什么是现在\t4」这种标题页（车联体实测 21 条）。
    # 剥离必须留账：dropped 进 outline，G13 据此豁免并在报告里逐条列出，不许静默丢内容。
    def is_toc(b):
        return b['kind'] == 'p' and (b.get('style') or '').lower().startswith('toc')

    dropped = [{'text': b['text'], 'why': u'目录条目（正文里已有同名章节）'}
               for b in seq if is_toc(b)]
    seq = [b for b in seq if not is_toc(b)]

    def is_toc_heading(b):
        return b['kind'] == 'p' and re.match(u'^\s*(目\s*录|contents|目录表)\s*$',
                                             (b['text'] or '').strip(), re.I)

    dropped += [{'text': b['text'], 'why': u'目录标题（导航件，不是内容）'}
                for b in seq if is_toc_heading(b)]
    seq = [b for b in seq if not is_toc_heading(b)]
    # 文档抬头（编制：xx / 日期：2025-11-xx）是署名信息不是内容：上一版它变成了一页，
    # 引擎从"日期：2025-11"里抓出 '-11' 当 110pt 主视觉（v10 逐页目检页 3 实测），
    # 屏幕上就是一个巨大的断头负数。现在并进封面副标题，一个字都不丢。
    FM_RE = re.compile(u'^\s*(?:编制|日期|作者|撰写|出品|时间|版本|审核)\s*[:：]')
    first_head = next((i for i, b in enumerate(seq)
                       if b['kind'] == 'p' and b.get('level')), None)
    front_matter = []
    if first_head is not None:
        keep = []
        for i, b in enumerate(seq):
            if b['kind'] == 'p' and i < first_head and FM_RE.match(b['text'] or '') \
                    and len((b['text'] or '').strip()) <= 40:
                front_matter.append((b['text'] or '').strip())
            else:
                keep.append(b)
        seq = keep
    blocks = [b for b in seq if b['kind'] == 'p']
    tables = [b['rows'] for b in seq if b['kind'] == 'tbl']

    title = blocks[0]['text'] if blocks else os.path.splitext(os.path.basename(a.docx))[0]
    title_block = id(blocks[0]) if blocks else None
    chapters = []
    cur = None

    def new_chapter(name, lvl):
        c = {'name': name, 'level': lvl, 'body': [], 'tables': []}
        chapters.append(c)
        return c

    for b in seq:
        if b['kind'] == 'tbl':
            if cur is None:
                cur = new_chapter('内容', 1)
            cur['tables'].append(b['rows'])
            continue
        if id(b) == title_block:
            continue          # the document title becomes the cover, not a chapter
        if b['level'] in (1, 2, 3):
            cur = new_chapter(b['text'], b['level'])
        elif cur is None:
            cur = new_chapter('内容', 1)
            cur['body'].append(b['text'])
        else:
            cur['body'].append(b['text'])
    if not chapters:
        chapters = [{'name': '内容', 'level': 1, 'body': [b['text'] for b in blocks[1:]],
                     'tables': [b['rows'] for b in seq if b['kind'] == 'tbl']}]

    entities = extract_entities(blocks, tables, [b['text'] for b in blocks if b['level']])
    pages = [{'role': 'cover', 'headline': title, 'body': [], 'entities': entities[:6]}]
    # 封面四件套（SPEC §17）：文档名降为 kicker 副题，主标题留给 agent 写的口号 cover_slogan；
    # 信息胶囊取一级章名（语料 NO.126 "工作总结|项目进展|成果展示|未来计划" 同构）。
    pages[0]['cover_kicker'] = title
    _chip_names = [strip_number(c['name']) for c in chapters if c['level'] == 1]
    # 胶囊是导航关键词，不许拦腰硬截：'三方为什么非入这张网不可'[:8] = '三方为什么非入这'
    # （真渲染封面目检）。章名整条放得下就整条放；真超长的宁可少一个胶囊，也不在屏幕上留
    # 半截话（G29 只要求胶囊行非空，不要求恰好四个；章名在各自过渡页上仍是完整的）。
    _chips = [re.sub(r'\s+', u'', x or '') for x in _chip_names]
    pages[0]['cover_chips'] = [x for x in _chips if x and len(x) <= 14][:4]
    if front_matter:
        pages[0]['subtitle'] = u' · '.join(front_matter)[:40]
    pending_sections = []
    for ci, ch in enumerate(chapters, 1):
        # one divider per 一级章, ever. Two defects fixed here: an empty chapter used to also get a
        # "statement" page with the same headline (4 identical neighbouring pairs in a 4-chapter doc),
        # and every 二/三级 heading used to get its own divider, so nested docs opened with a run of
        # near-empty chapter cards. A sub-heading is now a kicker on the page under it.
        first_page_here = len(pages)
        body = [x for x in ch['body'] if x]
        if ch['level'] == 1:
            # 过渡页看点（SPEC §17.5）：取该章首句主张做一条实质短句，不许只留编号+章节名。
            _hl = []
            _ch_name = re.sub(r'\s+', u'', strip_number(ch['name']) or '')
            # 章节名本身已实质（≥6 字）就不必再加看点（避免抬高每页字数）；
            # 只有空弱名（如"一句话"3 字）才补一条首句小句做看点（SPEC §17.5）。
            if body and len(_ch_name) < 6:
                _h0, _ = split_headline(body[:1])
                _h0 = re.split(u'[。！？!?]', re.sub(r'\s+', u'', _h0 or ''))[0][:8]
                if len(_h0) >= 4:
                    _hl.append(_h0)
            pages.append({'role': 'divider', 'headline': strip_number(ch['name']), 'chapter': ci,
                          'body': [], 'entities': [], 'divider_highlights': _hl})
            pending_sections = []
        for chunk in chunk_body(body, a.per_page,
                                getattr(a, 'max_chars', TH['page_chars_display_total'])):
            head, rest = split_headline(chunk)
            pages.append({'role': pick_role(chunk), 'headline': head, 'body': rest,
                          'headline_is_draft': True,
                          'entities': [e for e in entities if any(e in c for c in chunk)]})
        for ti, rows in enumerate(ch.get('tables') or []):
            pages.extend(table_pages(rows, strip_number(ch['name']), ci, ti))
        sec_names = [strip_number(x) for x in pending_sections]
        if ch['level'] > 1:
            sec_names.append(strip_number(ch['name']))
        pending_sections = []
        if len(pages) > first_page_here:
            # a 节 with no text of its own (its content lives in a 小节 under it) still has to reach
            # the deck: its heading rides along as a kicker line on the next page produced
            carry = [x for x in sec_names if x]
            if carry:
                pages[first_page_here]['sections'] = carry
        elif ch['level'] > 1 and strip_number(ch['name']):
            pending_sections = [ch['name']]
    pages.append({'role': 'closing', 'headline': '谢谢', 'body': [], 'entities': []})

    def _propose(pg_list):
        for i, pg in enumerate(pg_list, 1):
            pg['page'] = i
            pg['source_span'] = pg.get('body', [])
            # 提炼契约（SPEC §12）：语料好示范页中位只有 19 字，逐字上屏永远追不上。
            # plan 先把这一页的"该说的事"结构化，显示文字由 agent 在提案上改写。
            dl, ctype, needs = DR.propose_lines(pg)
            pg['content_type'] = ctype
            pg['key_numbers'] = DR.key_numbers(' '.join(pg.get('body') or []), 3)
            pg['display_lines'] = dl
            pg['display_is_proposal'] = True
            pg['display_needs_edit'] = needs

    pages, n_thin = merge_thin_pages(pages)
    if n_thin:
        print(u"  并回 %d 个近乎空的页（源文本 <12 字独占一页，屏幕上就是一张黑底两行字）" % n_thin)
    brief = parse_page_budget(a)
    alloc = None
    if brief.get('pages'):
        pages, alloc = allocate_to_budget(pages, brief['pages'])
        brief['alloc'] = alloc
        # 页数契约丢弃的隔页/表格必须进 outline.dropped 台账：G13 只认这份账，
        # 只记在 brief.alloc 里＝没账。丢表的 cells 随条目一起入账（逐单元格可核）。
        for _i, _t in enumerate(alloc.get('dropped_dividers') or []):
            if _t:
                _db = (alloc.get('dropped_divider_bodies') or [])
                dropped.append({'text': _t,
                                'cells': (_db[_i] if _i < len(_db) else []) or [],
                                'why': u'页数契约下丢弃的章节隔页（导航件，内容已并入正文页）'})
        for _dt in (alloc.get('dropped_tables') or []):
            dropped.append({'text': _dt.get('header') or _dt.get('headline') or u'（丢表）',
                            'cells': _dt.get('cells') or [],
                            'why': u'页数契约下丢弃的表格（配额限制）'})
        print(u"  页数契约 %s：自然分页 %d 页 → 合并 %d 页 → 实际 %d 页（结构页不动，章内内容页相邻合并；"
              u"正文一句不丢，上屏篇幅由 refine 压缩台账留账）" % (
                  brief['pages_raw'], alloc['natural'], alloc['merged'], len(pages)))
    elif not brief['auto_confirmed']:
        print(u"\n【必须先问用户再往下走】这份 Word 自然分页约 %d 页。用户没给页数契约时不许自作主张："
              u"\n  ① 这份 PPT 要多少页（或一个区间，如 25-35）？② 什么场合讲（路演/汇报/评审/留档）？"
              u"\n  ③ 讲给谁（投资人/领导/客户/内部）？④ 用哪套模板风格（科技蓝/水墨/党建红金/亮白商务/霓虹/暖金）？"
              u"\n  ⑤ 要不要逐字留档册（--verbatim）还是发布会提炼册？"
              u"\n  拿到回答后重跑 plan --pages N --occasion … --audience … --note 用户原话；"
              u"用户明确说「页数你定」才许加 --auto-pages。G23 会拦没有契约的册子。\n" % len(pages))
    _propose(pages)
    n_fact_split = 0
    if not brief.get('pages'):
        pages, n_fact_split = split_by_facts(pages, entities)
    if n_fact_split:
        _propose(pages)
        pages, n_again = split_by_facts(pages, entities)
        n_fact_split += n_again
        if n_again:
            _propose(pages)
    if n_fact_split:
        print(u"  事实装不下拆页 %d 页（一页 ≤%d 个事实单元，删数字不算过闸）" % (
            n_fact_split, TH['page_facts_max']))
    # 导航件/前置件也要进丢弃账：G13 逐块核原文，章节标题与封面标题/编制行若没账，
    # 会被当成"丢内容"点名（实测 30 页册余 30 句正是这两类）。声明式丢弃＋报告列出，不许静默。
    _have_drop = set(norm_content(x.get('text')) for x in dropped
                     if isinstance(x, dict) and x.get('text'))
    for _b in blocks:
        _t = (_b.get('text') or '').strip()
        if not _t:
            continue
        if norm_content(_t) in _have_drop:
            continue
        if re.match(u'^第[0-9一二三四五六七八九十]+章', _t):
            dropped.append({'text': _t, 'why': u'章节标题（导航件，上屏在隔页/正文里）'})
        elif _t == title or re.match(u'^(编制|日期|汇报|作者|项目组|版本)[：:]', _t):
            dropped.append({'text': _t, 'why': u'前置件（封面标题/编制信息，不上内容页）'})
    src_chars = sum(len(b['text']) for b in blocks)
    # 封面口号跨重跑继承：口号绑的是文档主题而不是分页，只有"同一份源文档 + 同一页角色"才继承，
    # 文档名换了旧口号就不认（SPEC §17 要求它从本文档提炼）。
    _AUTHORED = ('cover_slogan', 'cover_subtitle')
    _carry = 0
    if str(_old_outline.get('docx') or '') == os.path.abspath(a.docx):
        _opg = _old_outline.get('pages') or []
        for _i, _pg in enumerate(pages[:len(_opg)]):
            _o = _opg[_i] or {}
            if _o.get('role') != _pg.get('role'):
                continue
            for _k in _AUTHORED:
                _v = _o.get(_k)
                if isinstance(_v, str) and _v.strip() and not _pg.get(_k):
                    _pg[_k] = _v
                    _carry += 1
    if _carry:
        print(u"  已从上一版 outline 继承 agent 手写封面字段 %d 个（%s）：改分页/改正文不会抹掉口号"
              % (_carry, u'、'.join(_AUTHORED)))
    # 表格上限按源表实际内容推导：G21 要求源表格每个单元格都上屏，固定 400 会与"全单元格"内生冲突
    # （实测页28 源表 36 格 444 字）。取 max(常量, 1.1×最大源表字数)——由内容定，不是拍脑袋放宽。
    _max_tbl_chars = max([sum(len(c or '') for r in t for c in (r or [])) for t in tables] or [0])
    _table_cap = max(TH['page_chars_max_table'], int(_max_tbl_chars * 1.1))
    outline = {'title': title, 'docx': os.path.abspath(a.docx), 'parsed_at': now(),
               'n_tables': len(tables), 'entities': entities,
               # G21 的指纹：源文件是谁、多大、几张表，在 plan 这一步钉死。
               # 没有这块，check 无从知道"溯源基准"后来被换成了一份 4410 字的派生件。
               'source': {'path': os.path.abspath(a.docx), 'sha1': sha1(a.docx),
                          'bytes': os.path.getsize(a.docx), 'chars': src_chars,
                          'paragraphs': len(blocks), 'tables': len(tables),
                          'table_headers': [' | '.join((c or '') for c in rows[0])[:90]
                                            for rows in tables]},
               'chapters': [c['name'] for c in chapters], 'pages': pages,
               'brief': brief,
               'dropped': dropped, 'table_cap': _table_cap,
               'verbatim': bool(getattr(a, 'verbatim', False))}
    jw(wp['outline'], outline)

    tech = kb('deck_tech.json')['cards']
    shots = jr(wp['shots'])
    # 只有"这一页的内容还是原来那一页"才允许沿用旧分镜。上一版按页号判断（page in have），
    # 于是改 --per-page 重跑 plan 时，131 页的新 outline 配上了 168 页的旧 shots：
    # 页号还在、内容已经错位，build 直接 IndexError，运气不好则是静默装错图。
    def page_sig(pg):
        return hashlib.sha1(json.dumps(
            [pg['role'], pg['headline'], pg.get('body') or [],
             bool(pg.get('table'))], ensure_ascii=False).encode('utf-8')).hexdigest()[:16]

    keep = {}
    if shots is None:
        shots = {'deck': title, 'pages': []}
    else:
        for s in shots['pages']:
            n = s['page']
            if 1 <= n <= len(pages) and s.get('page_sig') == page_sig(pages[n - 1]):
                keep[n] = s
        dropped = len(shots['pages']) - len(keep)
        if dropped:
            print("  分镜里 %d 页与新 outline 对不上（改了分页或正文），这些页重新生成" % dropped)
    menu = {c['id']: c['name'] for c in tech}
    new_pages = []
    for pg in pages:
        if pg['page'] in keep:
            new_pages.append(keep[pg['page']])
            continue
        new_pages.append({
            'page': pg['page'], 'role': pg['role'], 'headline': pg['headline'],
            'page_sig': page_sig(pg),
            'content_type': pg.get('content_type'),
            'key_numbers': pg.get('key_numbers') or [],
            'display_lines': list(pg.get('display_lines') or []),
            'display_is_proposal': pg.get('display_is_proposal', True),
            'shot_intent': '', 'motion': '', 'tech_recipe': '',
            # 附表页不再找图：表格本身就是画面主体，配一张实景只会把数字挤掉
            'asset_kind': 'none' if pg['role'] == 'table' else (
                'video_bg' if pg['role'] in ('cover', 'divider', 'closing') else 'image'),
            'need_material': pg['role'] != 'table', 'reserved_zone': zone_for(pg['role']),
            'fill_me': (['shot_intent', 'motion', 'tech_recipe']
                      if pg['role'] == 'table' else
                      ['display_lines', 'shot_intent', 'motion', 'tech_recipe']),
        })
    shots['pages'] = new_pages
    shots['pages'].sort(key=lambda s: s['page'])
    jw(wp['shots'], shots)
    jw(os.path.join(a.work, 'tech_menu.json'), menu)
    adir = project_assets_dir(os.path.abspath(a.docx))
    outline['assets_dir'] = adir
    jw(wp['outline'], outline)
    print(u"这一册的素材文件夹（每册跟着自己的文档走，不写死路径、不落 C 盘）：%s"
          u"\n  你把网页上下载的图片/视频丢进 %s 就行，之后 pickup 会自己去那里认领"
          % (adir, os.path.join(adir, INBOX_NAME)))
    print("plan ok: %d 页 / %d 章 / %d 实体" % (len(pages), len(chapters), len(entities)))
    print("  outline -> %s" % wp['outline'])
    print("  shots   -> %s  (待填 shot_intent/motion/tech_recipe)" % wp['shots'])
    print("  可用技法: %s" % '、'.join(menu.values()))


FRAG_SPLIT = re.compile(r'[,;.:/|\s，。、；：·｜]+')
NUM_LEAD = re.compile(
    r'^\s*(?:第\s*[一二三四五六七八九十百0-9]+\s*[\章节回]?|'
    r'[一二三四五六七八九十百]+[、）)：:.]|[0-9]+[、）)：:]|[0-9]+\.(?![0-9]))\s*')


def strip_number(t):
    """Drop a chapter-level marker only.

    The old regex ate the first dot of a dotted number, so "1.1 市场口径" became "1 市场口径" and
    "1.1.1 城市分层" became "1.1 城市分层" -- a wrong section number on screen is worse than the
    ornament it removed. Dotted subsection numbers therefore stay; 第一章/一、/2. go away because
    divider pages draw their own ghost numeral. Whatever marker goes, the punctuation it was
    followed by must go too ("第一，把曾合作…" used to become "，把曾合作…").
    """
    s = NUM_LEAD.sub('', t or '').strip().lstrip('，。、；：,.').strip()
    return s or (t or '').strip()


MAX_TABLE_ROWS = 8


def table_pages(rows, chapter_name, ci, ti):
    """Split a Word table into deck pages, header repeated on every continuation page.

    A 40-row 台账 does not fit one slide; silently sampling it would be the same content loss
    that G13 exists to catch, so the rows are paginated instead.
    """
    rows = [r for r in rows if any((c or '').strip() for c in r)]
    if not rows:
        return []
    header = [(c or '').strip() for c in rows[0]]
    body = rows[1:]
    if not body:                      # header-only table: still show what it was
        body = [header]
        header = []
    out = []
    ncol = max(len(header), max(len(r) for r in body))
    header = header + [''] * (ncol - len(header))

    def _mk(chunk, part_no):
        cont = part_no > 1
        return {
            'role': 'table', 'chapter': ci,
            'headline': strip_number(chapter_name) + ('（续表）' if cont else '（附表 %d）' % (ti + 1)),
            'body': [], 'entities': [],
            'table': {'header': header,
                      'rows': [[(c or '').strip() for c in r] + [''] * (ncol - len(r))
                               for r in chunk],
                      'continued': cont, 'part': part_no},
        }

    # 只按行数切是不够的：附录那种"数字｜出处｜级别"表一行就 70 字，8 行 = 569 字，
    # 整页塞满就是 G23 的台账超限（语料那种台账页一页 ≤400 字）。
    # 预算按字给，行数为上限，两个条件任一触发就翻页；一行本身就超预算也只能单独成一页。
    _cap = max(60, int(TH['page_chars_max_table'] * 0.72))
    out, chunk, chars, part = [], [], 0, 1
    for r in body:
        rc = sum(len(re.sub(r'\s', u'', c or u'')) for c in r)
        if chunk and (len(chunk) >= MAX_TABLE_ROWS or chars + rc > _cap):
            out.append(_mk(chunk, part))
            part += 1
            chunk, chars = [], 0
        chunk.append(r)
        chars += rc
    if chunk:
        out.append(_mk(chunk, part))
    return out


def split_headline(chunk):
    """Take a headline at a phrase boundary, never mid-word, and drop that sentence from the body
    so the page does not print the same line twice."""
    first = chunk[0].strip()
    parts = [x for x in re.split(r'(?<=[，。；：、！？,])', first) if x.strip()]
    head = ''
    for p in parts:
        if len(head) + len(p) > 22 and head:
            break
        head += p
    head = head.rstrip('，。；、,')
    # 序号打头的枚举段不能把整页标题缩成一个字：'七，新品证号归工厂…' 里 '七，' 只是列表记号，
    # 旧写法把记号算进 22 字预算，下一句(21 字)放不下就 break，屏幕上剩下一个巨大的"七"
    # （真渲染页 29 目检）。这里把下一句并进来，长度由显示契约（clip_headline / G23）再去收。
    if len(re.sub(u'\\s', u'', head)) <= 2 or DR.BARE_ORDINAL.search(head):
        _nx = next((p for p in parts[1:] if p.strip()), u'')
        if _nx:
            # 用 parts[0]（带原标点）拼，别用 rstrip 后的 '七'——拼出来会变成
            # '七新品证号…' 这种丢了顿号的连串
            head = (parts[0] + _nx).rstrip(u'，。；、,')
    # 枚举清单不许拦腰截断（'样板期十二个月里，第一、第三' 是个没说完的清单，真渲染页 25）。
    # 与显示行 _clip 共用 DR.strip_enum_tail 这一把尺，不另写一份正则。
    head = DR.strip_enum_tail(head)
    if not head:
        head = first[:22]
    # Never drop the tail of the sentence. An earlier version returned chunk[1:], which silently
    # deleted real facts (履约率 79.9%、412 家转化 96 家、88%/1.6 亿元) from the deck. The headline is
    # a draft label; the body keeps every source sentence and place_text only skips exact duplicates.
    # The headline keeps its enumeration marker on purpose. Stripping "第一，" here made the
    # body's dedupe fall to the mid-string branch, which re-ordered the sentence on the page
    # ("…潜在池" + "第一，共 412 家…") and G13 then could not find the source sentence at all.
    return head, list(chunk)


def zone_for(role):
    """Reserved blank area for text, in 0-1 canvas fractions — mirrors the corpus prompts
    that demand '上半部分保持留白，便于后期添加标题'."""
    # 生成类图片常在右下角留下标记（实测见过「AI生成」角标与摄影签名），CC 授权照片的作者签名
    # 也多在边角；所以留白区默认不压右下角。出图一律走即梦/豆包网页版，不调用 ImageGen（消耗用户积分）。
    return {'cover': [0.10, 0.18, 0.80, 0.52], 'divider': [0.08, 0.16, 0.84, 0.70],
            'closing': [0.05, 0.42, 0.72, 0.30], 'statement': [0.10, 0.25, 0.80, 0.50],
            'data': [0.08, 0.16, 0.84, 0.70], 'process': [0.06, 0.18, 0.88, 0.66],
            'compare': [0.06, 0.18, 0.88, 0.66], 'quote': [0.15, 0.30, 0.70, 0.40],
            'appendix': [0.06, 0.12, 0.88, 0.76],
            # 附表页：标题占顶部，表格本体落在下面（add_table 按这个区间放，超出就分页）
            'table': [0.06, 0.12, 0.88, 0.74]}[role]


STOPWORDS = set('的了和与在为个月日年个家万元亿我们他们这个那些以及其中通过进行由于如果但是因为'
                '第一第二第三下一步目标情况数据问题工作项目内容部分要求相关系统平台')


FUNC_CHARS = u'的了和与在把以用对为或及就还也很更但其这那之等等'
MEASURE_PREFIX = u'个家条次位台例人年月日项笔笔份种类款'


def _func_split(run):
    """按虚词切中文串，但不制造词内碎片。

    实测坑：'把曾合作医美的中小门店纳入潜在池' 按 '在' 切会留下 '中小门店纳入潜' + '池'，
    前者被当成专名进了 entities，而 entities 同时是取图检索词和 G24 的必上屏事实清单——
    于是闸要求屏幕上出现 '中小门店纳入潜' 这串没人的片子会写的字。
    规则：切出来只剩 1 个字的那一刀，一定是砍在词中间（'潜在'），把这个字连虚词并回左边。
    """
    cuts = [m.start() for m in re.finditer('[' + FUNC_CHARS + ']', run)]
    segs, prev = [], 0
    seps = []
    for pos in cuts:
        segs.append(run[prev:pos])
        seps.append(run[pos])
        prev = pos + 1
    segs.append(run[prev:])
    out = []
    i = 0
    while i < len(segs):
        seg = segs[i]
        while i + 1 < len(segs) and len(segs[i + 1]) <= 1:
            seg += seps[i] + segs[i + 1]
            i += 1
        out.append(seg)
        i += 1
    return [s for s in out if s]


def extract_entities(blocks, tables, headings=()):
    """Candidate search terms lifted from the doc. Chinese has no spaces, so runs are cut on
    function words and punctuation first; the agent writes the final queries (see --term)."""
    txt = ' '.join(b['text'] for b in blocks) + ' ' + ' '.join(
        ' '.join(' '.join(r) for r in t) for t in tables)
    head_txt = ' '.join(headings)
    cand = collections.Counter()
    for m in re.finditer(u'[一-龥]+', txt):
        run = m.group(0)
        # '12 城签约' 这种量词黏在动词前面的，把量词剥掉再当候选；
        # 只在"紧跟数字"的时候剥，否则 '城市合伙人' 会被剥成 '市合伙人'
        after_num = bool(re.search(u'\\d[\\d,\\.]*\\s*$', txt[:m.start()]))
        for piece in _func_split(run):
            piece = piece.lstrip(MEASURE_PREFIX + (u'城' if after_num else u''))
            if 2 <= len(piece) <= 8 and piece not in STOPWORDS:
                cand[piece] += 1
    for w in re.findall(r'[A-Za-z][A-Za-z0-9\-]{2,}', txt):
        cand[w] += 1
    scored = [(w, c, w in head_txt) for w, c in cand.items() if c >= 2 or w in head_txt or len(w) >= 3]
    scored.sort(key=lambda x: (-int(x[2]), -min(x[1], 3), -len(x[0])))
    out = []
    for w, c, h in scored:
        if any(w in o or o in w for o in out):
            continue
        out.append(w)
        if len(out) >= 30:
            break
    return out


def _facts_over_budget(text, ents, max_facts):
    """这一串文字占的版面名额超不超预算。除个数外还看两条硬事实：
    一页只画得下**一组**条形（bar_group 一次一组、最多 4 根柱子），
    所以两组同单位系列、或一组超过 4 个数值，都不能算"这页装得下"。
    实测把两组系列塞进同一页后，装配只画了第一组，另一组连同 18 个数字一起从屏上消失。
    """
    ser = DR.series_groups(text)
    if len(ser) > 1 or any(len(v) > 4 for v in ser.values()):
        return True
    return len(DR.budget_units(text, ents)) > max_facts


def chunk_body(body, per, max_chars=0, max_facts=None):
    """按句数分页，同时卡字数与"事实预算"。

    只数句子时，一句 200 字的长句照样把整页顶成语料里的文字墙（G19 判据 ≥300 字/页），
    装配还会把句子挤出留白区（实测 26 页命中墙、17 页丢正文、9 页超区）。
    事实预算是新加的那条：一页 ≤2 行 × ≤14 字最多扛 3 个数字，超了就必须在分页时切开，
    否则提炼契约（SPEC §12）与 G24（逐事实覆盖）互相矛盾，唯一"过闸"的办法就只剩删数字。
    这里只数数字（不数年份、不数专名）：那两类由 cmd_plan 的事实拆页那一轮按成品口径处理，
    塞进预算会把每句话都顶成一页（实测 4 事实一页 → 每册页数翻倍）。
    """
    max_facts = TH['page_facts_max'] if max_facts is None else max_facts
    # 一句里挤了 5 个以上数字时按页切不动（一页 ≤2 行 × ≤14 字扛不下），
    # 这种句子先按分句拆成"事实小组"再分页——删数字不是选项（G24 会点名）
    units = []
    for x in body:
        if max_facts and _facts_over_budget(x, (), max_facts) and re.search(u'[，、；;]', x):
            cur_u = u''
            for part in [p.strip() for p in re.split(u'[，、；;]', x) if p.strip()]:
                probe = (cur_u + u'，' + part) if cur_u else part
                # 预算要在"已经攒起来的这一段"上算，不能一段一段单独算：
                # '杭州 9 家、宁波 7 家、温州 4 家' 逐段看是 3 个事实（该拆），
                # 连起来看是一组 ≥3 的同单位数值（该画成一根根柱子，只占一个名额）。
                if cur_u and _facts_over_budget(probe, (), max_facts):
                    units.append(cur_u)
                    cur_u = part
                else:
                    cur_u = probe
            if cur_u:
                units.append(cur_u)
        else:
            units.append(x)
    chunks, cur, chars = [], [], 0
    for x in units:
        if cur and (len(cur) >= per or (max_chars and chars + len(x) > max_chars)
                    or (max_facts and _facts_over_budget(u'，'.join(cur + [x]), (), max_facts))):
            chunks.append(cur)
            cur, chars = [], 0
        cur.append(x)
        chars += len(x)
    if cur:
        chunks.append(cur)
    return chunks



def merge_thin_pages(pages):
    """把"这一页其实没内容"的页并回上一页。

    逐页目检（v10 真渲染）看到不少页只有 2–6 个字：那是事实预算拆页与逐句分页叠出来的
    空壳页，模板里不存在这种东西（语料每页 p50=19 字，但没有一页只剩一个词）。
    并回去的条件保守：这一页的标题+正文合计 <12 字、没有表格、不是封面/隔页/结尾页。
    """
    out, merged = [], 0
    for pg in pages:
        txt = u''.join([(pg.get('headline') or u'')] +
                       [x for x in (pg.get('body') or []) if x])
        thin = (len(re.sub(u'\s', u'', txt)) < 12 and not pg.get('table')
                and pg.get('role') in ('statement', 'data', 'process', 'compare', 'quote'))
        if thin and out and out[-1].get('role') not in ('cover', 'divider', 'closing') \
                and not out[-1].get('table'):
            prev = out[-1]
            for x in [pg.get('headline')] + [y for y in (pg.get('body') or []) if y]:
                x = (x or u'').strip()
                if x and x not in (prev.get('body') or []) and x != prev.get('headline'):
                    prev['body'] = list(prev.get('body') or []) + [x]
            prev['source_span'] = prev.get('body') or []
            prev.pop('_unpacked_facts', None)
            merged += 1
            continue
        out.append(pg)
    return out, merged


def split_by_facts(pages, ents):
    """把"提案装不下这页事实"的页按子句拆开重排，返回新的页表。

    为什么必须拆而不是删：G24 是逐个事实核对成品的。上一版一页有 4 个事实
    （履约率 79.9% / 潜在池 412 家 / 转化 96 家 / 曾合作医美）而显示预算只有 2 行 × 14 字，
    引擎就把装不下的那两个静默丢掉，屏幕上只剩"履约率79.9% 412"——
    对外片子上少一个成交额就是事故，不是排版风格问题。
    一页 ≤2 行 × ≤14 字 + 标题（≤16 字）实测最多稳当扛 3 个事实单元，所以预算取 TH['page_facts_max']。
    """
    out, n_split = [], 0
    for pg in pages:
        body = [x for x in (pg.get('body') or []) if x]
        if not pg.get('_unpacked_facts') or not body or pg.get('table'):
            out.append(pg)
            continue
        clauses = []
        for x in body:
            if len(body) == 1:
                clauses += [c for c in DR.split_clauses(DR._clean(x)) if c] or [x]
            else:
                clauses.append(DR._clean(x))
        groups, cur = [], []
        for c in clauses:
            probe = u'，'.join(cur + [c])
            if cur and _facts_over_budget(probe, ents, TH['page_facts_max']):
                groups.append(cur)
                cur = [c]
            else:
                cur.append(c)
        if cur:
            groups.append(cur)
        if len(groups) < 2:
            out.append(pg)          # 一个子句就装不下：只能交给 agent 重写，不许硬拆成半截话
            continue
        n_split += len(groups) - 1
        for g in groups:
            q = dict(pg)
            h, rest = split_headline(g)
            q['headline'] = h or pg['headline']
            q['body'] = rest or g
            q['role'] = pick_role(g)
            q['headline_is_draft'] = True
            q['entities'] = [e for e in ents if any(e in c for c in g)]
            q.pop('_unpacked_facts', None)
            q.pop('display_lines', None)
            q.pop('hero_number', None)
            out.append(q)
    return out, n_split


def pick_role(chunk):
    j = ' '.join(chunk)
    # 整页只有一句长话（超过语料"文字墙"字数线）时按引文页排：引文页的版式就是大字单栏，
    # 与 G19 的 dense_text_wall 判据同源，不是把墙藏起来——多句挤一页照样判墙。
    if len(chunk) == 1 and len(chunk[0]) >= CT.BAD_RULES['dense_chars_min']:
        return 'quote'
    if re.search(r'\d+(\.\d+)?\s*[%％]|\d{2,}\s*(万|亿|元|台|例|人)', j):
        return 'data'
    if re.search(r'(第一|第二|第三|步骤|流程|阶段|环节)', j):
        return 'process'
    if re.search(r'(对比|相比|高于|低于|优于|落后)', j):
        return 'compare'
    if len(chunk) == 1 and len(chunk[0]) <= 40:
        return 'statement'
    return 'data'

# ---------------------------------------------------------------- S3 fetch


def strip_html(v):
    v = re.sub(r'<[^>]+>', ' ', v or '')
    return re.sub(r'\s+', ' ', v).strip()[:160]


def _open_once(url, proxy, timeout):
    import urllib.request
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    op = urllib.request.build_opener(*handlers)
    op.addheaders = [('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) caofeng-ppt/2.9')]
    return op.open(url, timeout=timeout)


# Commons 对连打会整站限流：实测一口气取 106 页时 40 页吃到 HTTP 429，
# 被当成"这页没素材"写进 gaps，覆盖率直接掉到 13%。限流不是没货，必须退避重试并主动节流。
GET_MIN_INTERVAL = 2.2
_get_state = {'last': 0.0}


def http_get(url, timeout=40, proxy=None, tries=4):
    """带节流与退避的取回：429/503 重试（6/12/18 秒退避），两次请求之间强制拉开 GET_MIN_INTERVAL。"""
    import time as _t
    import urllib.error
    last_err = None
    for k in range(tries):
        gap = _t.time() - _get_state['last']
        if _get_state['last'] and gap < GET_MIN_INTERVAL:
            _t.sleep(GET_MIN_INTERVAL - gap)
        try:
            r = _open_once(url, proxy, timeout)
            _get_state['last'] = _t.time()
            return r
        except urllib.error.HTTPError as e:
            _get_state['last'] = _t.time()
            last_err = e
            if getattr(e, 'code', 0) in (429, 503) and k < tries - 1:
                _t.sleep(6 * (k + 1))
                continue
            raise
        except Exception as e:
            _get_state['last'] = _t.time()
            last_err = e
            if k < tries - 1:
                _t.sleep(3)
                continue
    raise last_err


FOREIGN_HINT = re.compile(r'(wikimedia|wikipedia|unsplash|pexels|pixabay|commons|bing|google|flickr|openverse)', re.I)


def is_foreign(url):
    return bool(FOREIGN_HINT.search(url))


RENDER_LONG_EDGE = 3200      # 与 normalize_asset 的 max_edge 同值：取回就够装配用，不搬原图


def search_commons(term, n=12, proxy=None):
    """Wikimedia Commons: free-licensed, licence is machine-readable -> the defensible source."""
    import urllib.parse
    api = ('https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch='
           + urllib.parse.quote('filetype:bitmap ' + term)
           + '&gsrnamespace=6&gsrlimit=' + str(n)
           + '&prop=imageinfo&iiprop=url|size|extmetadata|mime'
           + '&iiurlwidth=' + str(RENDER_LONG_EDGE) + '&format=json')
    try:
        data = json.load(http_get(api, proxy=proxy))
    except Exception as e:
        return [], 'commons 检索失败: %s' % str(e)[:100]
    pages = (data.get('query') or {}).get('pages') or {}
    out = []
    for pg in pages.values():
        ii = (pg.get('imageinfo') or [{}])[0]
        if not ii.get('url'):
            continue
        em = ii.get('extmetadata') or {}
        lic = ((em.get('LicenseShortName') or {}).get('value')) or ''
        out.append({'url': ii['url'], 'page': pg.get('title'), 'w': ii.get('width', 0),
                    'h': ii.get('height', 0), 'mime': ii.get('mime'), 'license': lic,
                    # 装配只要长边 3200，原图动辄 8–21MB：直接取 Commons 的 3200 rendition，
                    # 单页耗时从约 90 秒降到 20 秒上下。'url' 仍指原文件（许可与来源以它举证），
                    # 判"原图短边≥1920"用的 w/h 也是 API 回的原图尺寸，不是这张缩样。
                    'rendition': ii.get('thumburl') or '',
                    # 美学/带字闸先拿 640px 缩样判（同一缩样链换宽度），过闸才搬 3200/原图
                    'precheck': (ii.get('thumburl') or '').replace(
                        '%dpx-' % RENDER_LONG_EDGE, '640px-'),
                    'artist': strip_html(((em.get('Artist') or {}).get('value')) or ''),
                    'source_api': 'commons.wikimedia.org'})
    return out, None


# Commons is English-indexed and full of news/archival photography, so every candidate title is
# screened before it can reach a page. Content safety and off-domain rejection live in safety.py
# (word-boundary matching, categories, and the deck-subject allow list).
import safety as _safe
is_sensitive = _safe.is_sensitive
is_off_domain = _safe.is_off_domain
reject_reason = _safe.reject_reason
CJK_QUERY = _safe.CJK

def sensitive_allow(outline, cli_arg=None):
    """Which sensitive categories the Word document's own subject justifies showing.

    A hospital ops deck legitimately needs ward imagery; a roadshow deck does not. The list
    comes from outline.json's allow_sensitive (written by plan from the doc) or --allow-sensitive,
    and an unknown category is a hard error so a typo cannot silently disable the whole list.
    """
    raw = [x.strip() for x in re.split(r'[,，、]', (cli_arg or '')) if x.strip()]
    if not raw:
        raw = [x.strip() for x in (outline.get('allow_sensitive') or []) if x.strip()]
    bad = [c for c in raw if c not in _safe.COMPILED]
    if bad:
        die("未知的敏感类别 %s；可选：%s" % (bad, '、'.join(sorted(_safe.COMPILED))))
    return raw


STOPWORD = frozenset(('a an the and or of at in on to for by with from into as is are was be '
                      'this that these those their there here some most very just like such '
                      'each every than then more other others about').split())
# 末尾介词短语的标志：出现这些词就把它后面的内容当成"修饰语"，主体只算它前面的名词块
ADJ_PREP = frozenset(('at in on by with from during near under over against across along '
                      'beside inside outside').split())


def phrase_tokens(term):
    """英文检索词 → (主体 token, 全部实词 token)。

    'bus depot at dawn' 的主体是 bus+depot，'at dawn' 是时间修饰语：
    旧实现把整串最后一个 token 当中心词，于是判成 dawn——照片里根本没 dawn，
    整批判 0%（实测 v5 缺口清一色"最高相关度仅 0%"、覆盖停在 61/231）。
    len>=3 + 停用词表：'bus'/'map'/'car' 这种短主体词不能被长度过滤器杀掉。"""
    raw = [w for w in re.split(r'[^a-z0-9]+', str(term or '').lower()) if w]
    cut = len(raw)
    for i, w in enumerate(raw):
        if i >= 1 and w in ADJ_PREP:
            cut = i
            break
    keep = lambda xs: [w for w in xs if len(w) >= 3 and w not in STOPWORD]
    return keep(raw[:cut]), keep(raw)


def photo_quality_reason(path, title, terms):
    """下载后的画面质检：带字图/历史画/插画/地图/海报一律拒。

    词面相关度（检索词↔标题）给满分也拦不住语义离题：实测渡轮当"medical vehicle fleet"、
    美军握手当"community screening clinic"、病房素描当"ambulance interior"。这里在下载后
    量画面本身：文字像素占比 >2% 判带字图；标题含 engraving/sketch/… 且检索词没主动要它判非实景。
    """
    tl = (title or '').lower()
    for kw in ('engraving', 'sketch', 'drawing', 'illustration', 'painting', 'portrait',
               'poster', 'diagram', 'historic', 'historical', 'vintage'):
        if kw in tl and not any(kw in (t or '').lower() for t in (terms or [])):
            return u'候选是 %s 类非实景素材（标题含该词），换下一张' % kw
    try:
        import numpy as np
        from PIL import Image
        import dewatermark as DW
        im = Image.open(path).convert('RGB')
        im.thumbnail((800, 800))
        arr = np.asarray(im)[:, :, ::-1].copy()
        mask = DW.frame_text_mask(arr, lift_min=10, gray_min=80, min_area=150, min_fill=0.15)
        frac = float((mask > 0).mean())
        if frac > 0.02:
            return u'带字图（文字像素占 %.1f%%）：背景上烧字会和 PPT 文字打架，换下一张' % (frac * 100)
    except Exception:
        return None
    return None


def relevance(title, terms):
    """Commons file titles are the cheapest honest relevance signal. Without this gate the
    picker just takes the biggest match and happily pairs a water-town bridge with a revenue page.

    判据：主体 token 命中率 ≥50%（且至少命中 1 个）；总 token 少于 2 个直接归零
    （单个词等于抛硬币，中文词打英文索引是噪音）。放宽的是"中心词怎么认"，
    不是强度——只命中修饰语的（'Mountain dawn mist' 对 'bus depot at dawn'）仍然 0。"""
    t = (title or '').lower()
    head, all_tok = [], []
    for term in terms:
        h, a = phrase_tokens(term)
        head += h
        all_tok += a
    if len(set(all_tok)) < 2:
        return 0
    if not head:
        head = all_tok
    hit = sum(1 for w in set(head) if w in t)
    if not hit:
        return 0
    ratio = 100.0 * hit / len(set(head))
    return int(round(ratio)) if ratio >= 50.0 else 0


# ---------------------------------------------------------------- 取图通道 2：Unsplash
# 用户指定的通道原文：官网 https://unsplash.com（可切换中文），搜索关键词/颜色/分类，
# hover 点 ↓ 下载；注册可建收藏夹/上传作品（每周限 10 张起）。
# 实测本机 napi/search 与 HTML 两条都回 401（需要登录/令牌），所以全自动下载只在有官方
# 密钥时成立；没有密钥就走"卡上给检索 URL + 用户下载 + fetch --adopt 登记"的合法路径。
UNSPLASH_KEY_ENV = 'UNSPLASH_ACCESS_KEY'
UNSPLASH_LICENSE = 'Unsplash License'


def unsplash_key():
    return (os.environ.get(UNSPLASH_KEY_ENV) or '').strip() or None


def unsplash_search_url(term, color=None):
    import urllib.parse
    u = 'https://unsplash.com/zh-cn/s/photos/' + urllib.parse.quote(term.encode('utf-8'))
    q = ['orientation=landscape']
    if color:
        q.append('color=' + urllib.parse.quote(color.encode('utf-8')))
    return u + '?' + '&'.join(q)


def unsplash_links(pg, s, term=None):
    """这一页该去 Unsplash 搜什么：只给画面里看得见的实物名，不给抽象词。"""
    cands = []
    for t in ([term] if term else []) + ([s.get('subject')] if s.get('subject') else []) + \
             [e for e in (pg.get('entities') or []) if e]:
        if not t:
            continue
        t = str(t).strip()
        if not t or re.search(u'[一-龥]', t):
            continue
        if t.lower() not in [x.lower() for x in cands]:
            cands.append(t)
    out = []
    for t in cands[:3]:
        out.append({'term': t, 'url': unsplash_search_url(t)})
    if cands:
        out.append({'term': cands[0] + ' ｜ 黑白', 'url': unsplash_search_url(cands[0], 'black-and-white')})
    return out


def search_unsplash(term, n=12, proxy=None, key=None):
    """api.unsplash.com：许可固定为 Unsplash License（不强制署名），作者与页面 URL 可举证。"""
    import urllib.parse
    import urllib.request
    key = key or unsplash_key()
    if not key:
        return [], '未设置环境变量 %s：Unsplash 自动下载不可用，走投放卡里的检索 URL + ' \
                    'fetch --adopt 登记（实测 napi 直连与代理都回 401）' % UNSPLASH_KEY_ENV
    api = ('https://api.unsplash.com/search/photos?query='
           + urllib.parse.quote(term.encode('utf-8'))
           + '&orientation=landscape&per_page=' + str(min(int(n), 30)))
    req = urllib.request.Request(api, headers={'Authorization': 'Client-ID ' + key,
                                               'Accept-Version': 'v1',
                                               'User-Agent': 'Mozilla/5.0 caofeng-ppt'})
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    op = urllib.request.build_opener(*handlers)
    try:
        with op.open(req, timeout=40) as r:
            data = json.load(r)
    except Exception as e:
        return [], 'unsplash 检索失败: %s' % str(e)[:120]
    out = []
    for it in (data.get('results') or []):
        urls = it.get('urls') or {}
        w, h = it.get('width') or 0, it.get('height') or 0
        raw = urls.get('raw')
        dl = (raw.split('?')[0] if raw else urls.get('regular')) or ''
        out.append({'url': (raw or urls.get('regular') or '') + ('&w=3200&fit=max&q=85' if raw else ''),
                    'page': (it.get('links') or {}).get('html') or '',
                    'title': ((it.get('alt_description') or it.get('description') or '')
                              or (it.get('user') or {}).get('name') or '')[:120],
                    'w': w, 'h': h, 'mime': 'image/jpeg',
                    'license': UNSPLASH_LICENSE,
                    'artist': ((it.get('user') or {}).get('name') or '').strip(),
                    'rendition': dl + ('?w=3200&fit=max&q=85' if dl.startswith('https://images.') and '?' not in dl else ''),
                    'source_api': 'unsplash.com'})
    return out, None



# ---------------------------------------------------------------- 取图通道 3：Openverse
# 免密钥可用（匿名有速率上限，有 CAOFENG_OPENVERSE_TOKEN 就走 Bearer），
# 许可字段机器可读：license + license_version 直接拼成 "CC BY 2.0" 这种可举证写法。
OPENVERSE_API = 'https://api.openverse.org/v1/images/'
OPENVERSE_TOKEN_ENV = 'CAOFENG_OPENVERSE_TOKEN'


def openverse_token():
    return (os.environ.get(OPENVERSE_TOKEN_ENV) or '').strip() or None


def search_openverse(term, n=20, proxy=None, size_kind=None):
    import urllib.parse
    import urllib.request
    import time as _time
    tok = openverse_token()
    # 逗号分隔的多许可这个版本不认（实测 result_count=0，日志只会显示"缺素材"），
    # 索性不带 license 参数，回来按白名单逐条筛——筛得比参数更明白。
    # 匿名 page_size 上限是 20（实测 >20 直接 401 "page_size may not exceed 20 for
    # anonymous requests"），所以要更大的候选池只能翻页：每页 20，翻到够 n 为止。
    # size=large 是 Openverse 自己的像素档位筛：实测 'doctor consultation room'
    # 不带参数 19 条里短边≥1080 的 0 条，带 large 的 19 条里 18 条达标——
    # 这一档和我们的"短边≥1080"同向，属于在源头就要对题高清图，不是放宽判据。
    cap = 50 if tok else 20
    size = min(max(int(n), 10), cap)
    pages = max(1, int(round(float(max(int(n), size)) / size)))
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    op = urllib.request.build_opener(*handlers)
    out, last_err = [], None
    for p in range(1, pages + 1):
        api = (OPENVERSE_API + '?q=' + urllib.parse.quote(term) +
               '&page_size=' + str(size) + '&page=' + str(p))
        if size_kind:
            api += '&size=' + size_kind
        try:
            req = urllib.request.Request(
                api, headers={'User-Agent': 'Mozilla/5.0 caofeng-ppt/3.0',
                              'Accept': 'application/json'})
            if tok:
                req.add_header('Authorization', 'Bearer ' + tok)
            data = json.load(op.open(req, timeout=40))
        except Exception as e:
            last_err = 'openverse 检索失败: %s' % str(e)[:120]
            break
        got = [c for c in (ov_candidate(it) for it in (data.get('results') or []))
               if c and c['license_ok']]
        out += got
        if len(data.get('results') or []) < size:
            break
        if p < pages:
            _time.sleep(0.8)
    if not out and last_err:
        return [], last_err
    return out, None


OV_LICENSE_OK = ('BY', 'BY-SA', 'CC0', 'PDM', 'CC-BY', 'CC-BY-SA')


OV_THUMB_PX = 3840      # Wikimedia 现在只放行尺寸白名单里的档（实测 3200px 回 400
                        # "Use thumbnail sizes listed on https://w.wiki/GHai"，3840px 可用）


def wikimedia_thumb(url, long_edge=OV_THUMB_PX):
    """upload.wikimedia.org 的原图 URL → 缩样 URL（拿不到就回空串，调用方退回原图）。

    Openverse 回的 thumbnail 字段是 api.openverse.org/v1/images/<id>/thumb 这个转发口，
    实测直接下它会拿 HTTP 424 Failed Dependency（页1 一整轮就是被它吃掉的）；
    原图又动辄 3–7MB。实测按 Commons 的路径规则拼 thumb.wikimedia.org 的 3840px 档，
    同一个文件从 3,182,827 字节降到 1,513,979 字节、分辨率仍然 3840x2880，够 SPEC §5 用。"""
    m = re.match(r'^(https://upload\.wikimedia\.org)/([a-z/]*?commons/)'
                 r'([0-9a-f]{1})/([0-9a-f]{2})/([^?#]+)', url or '')
    if not m:
        return ''
    host, mid, h1, h2, name = m.groups()
    return ('%s/%sthumb/%s/%s/%s/%dpx-%s' % (host, mid, h1, h2, name, long_edge, name))


def ov_candidate(it):
    """Openverse API 条目 → 引擎候选。

    url_type 是 'jpg'/'png' 这种裸扩展名，直接塞进 mime 会被下游
    `str(c['mime']).startswith('image/')` 全量筛掉（实测：整批 0 命中，
    日志只写"缺素材"，看不出是被自己的过滤器杀的），所以这里统一补成 image/*。"""
    url = it.get('url') or ''
    if not url:
        return None
    ut = str(it.get('url_type') or '').strip().lower().lstrip('.')
    mime = ut if ut.startswith('image/') else ('image/' + (ut or 'jpeg'))
    lic_name = (it.get('license') or '').upper().strip()
    lic = ((lic_name + (' ' + str(it.get('license_version'))
                        if it.get('license_version') and lic_name else '')) or '').strip()
    lic_key = lic_name
    return {'url': url, 'page': it.get('foreign_landing_url') or it.get('webapp_url') or url,
            'title': it.get('title') or '', 'w': it.get('width') or 0,
            'h': it.get('height') or 0, 'mime': mime,
            'license': (lic or '').strip() or '许可未知',
            'artist': (it.get('creator') or '').strip(),
            'rendition': wikimedia_thumb(url) or url,
            'license_ok': lic_key in OV_LICENSE_OK,
            'source_api': 'api.openverse.org'}


def _pick_terms(term):
    """检索词序列：全称在前，词数 >3 时补一版"只留前 3 个词"。

    Openverse 是多词 AND，'intercity coach bus exterior' 实测回 0，
    'intercity coach bus' 回 16 张；退词只少限定词、不换主题，
    短边/安全/相关度/铺页上限四道筛一条都没放宽。"""
    parts = term.split()
    seq = [term]
    if len(parts) > 3:
        relaxed = u' '.join(parts[:3])
        if relaxed != term:
            seq.append(relaxed)
    return seq


def _commons_lane(t, n=40, proxy=None, size_kind=None):
    return search_commons(t, n=n, proxy=proxy if is_foreign('https://commons.wikimedia.org') else None)


def _openverse_lane(t, n=40, proxy=None, size_kind=None):
    return search_openverse(t, n=n, proxy=proxy, size_kind=size_kind)


def _unsplash_lane(t, n=40, proxy=None, size_kind=None):
    return search_unsplash(t, n=n, proxy=proxy)


LANES = {'unsplash': (('unsplash', _unsplash_lane),),
         'openverse': (('openverse', _openverse_lane),),
         'commons': (('commons', _commons_lane),),
         'auto': (('openverse', _openverse_lane), ('commons', _commons_lane))}

# 候选池宽度：40。Openverse 匿名单页上限 20（实测 21 就 401），所以靠翻页拿到 40 条：
# 实测 'doctor consultation room' 一页只有 19 条、翻两页拿到 29 条，池子宽度确实有意义；
# 但真正把"短边≥1080 全灭"解决掉的是下面这个 size 档（同一词不带 size 时 19 条里 0 条达标，
# 带 large 时 19 条里 18 条达标）。
FETCH_POOL_N = 40
OV_LARGE_EDGE = 1080     # 引擎的短边门槛到这一档，就改用 Openverse 自己的 large 像素档


def fetch_candidates(term, source, proxy, min_edge=None):
    """按源取候选，每个源各自走完"全称 → 前 3 词"退化后再合并。

    旧写法把两源合并起来只看总结果是否为空：commons 一有货，openverse 那条
    长词组 AND 0 命中的通道就永远轮不到退词重试（实测 v5 覆盖停在 61/231，
    缺口日志清一色"最高相关度仅 0%"）。
    返回 (候选, 错误说明, 实际用了退词命中的源)。每个候选带 term_used，
    相关度与 manifest 里的检索词都按它自己那一路真正用的词算。"""
    seq = _pick_terms(term)
    size_kind = 'large' if (min_edge or 0) >= OV_LARGE_EDGE else None
    out, errs, relaxed_hits = [], [], []
    for name, fn in LANES[source]:
        err = None
        for t in seq:
            c, err = fn(t, n=FETCH_POOL_N, proxy=proxy, size_kind=size_kind)
            if c:
                if t != term:
                    relaxed_hits.append((name, t))
                for x in c:
                    x['term_used'] = t
                    x.setdefault('channel', name)
                out.extend(c)
                break
        if err and not any(x.get('channel') == name for x in out):
            errs.append('%s:%s' % (name, str(err)[:80]))
    return out, (u'; '.join(errs) or None), relaxed_hits


def search_commons_video(term, n=12, proxy=None):
    """Commons 视频通道：filetype:video，许可机读。只收 video/* 候选。"""
    import urllib.parse
    api = ('https://commons.wikimedia.org/w/api.php?action=query&generator=search&gsrsearch='
           + urllib.parse.quote('filetype:video ' + term)
           + '&gsrnamespace=6&gsrlimit=' + str(n)
           + '&prop=imageinfo&iiprop=url|size|extmetadata|mime|metadata&format=json')
    try:
        data = json.load(http_get(api, proxy=proxy))
    except Exception as e:
        return [], 'commons 视频检索失败: %s' % str(e)[:100]
    pages = (data.get('query') or {}).get('pages') or {}
    out = []
    for pg in pages.values():
        ii = (pg.get('imageinfo') or [{}])[0]
        if not ii.get('url') or not str(ii.get('mime') or '').startswith('video/'):
            continue
        em = ii.get('extmetadata') or {}
        out.append({'url': ii['url'], 'page': pg.get('title'), 'w': ii.get('width', 0),
                    'h': ii.get('height', 0), 'mime': ii.get('mime'),
                    'duration': ii.get('duration') or 0,
                    'license': ((em.get('LicenseShortName') or {}).get('value')) or '',
                    'artist': strip_html(((em.get('Artist') or {}).get('value')) or ''),
                    'source_api': 'commons.wikimedia.org'})
    return out, None


# 色板色相窗（deg）：美学闸按所选风格判"离色板饱和像素"占比。电影级底图几乎全在色板内，
# 纪实 stock（黄色出租车/绿色线缆/棕色档案）离色板饱和像素一大片——实测分离度足够做闸。
# 键必须是 corpus_tables.PALETTES 的真色板 id（旧写法写 'neon'/'white_biz'/'warm_gold'，
# 三个都不存在 → 落进 .get 的蓝窗默认值，等于拿 tech_blue 的尺子判另外三种风格）。
# None = 不吃色相窗（水墨近黑低饱和、亮白商务看的是灰白、纪实实景本来就是真地方）。
STYLE_HUE_WINDOW = {'tech_blue': (185, 255), 'neon_cyan': (165, 265),
                    'party_red': (335, 395), 'ink_wash': None,
                    'business_light': None, 'photo_real': None}


def _rgb_hue_sat(a):
    """numpy 版 rgb→HSV 的 H/S（与 matplotlib.colors.rgb_to_hsv 同式）。
    不引 matplotlib：它不在 requirements.txt 里，装了它的机器闸在跑、没装的机器闸静默放行。"""
    import numpy as np
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    d = mx - mn
    sat = np.where(mx > 0, d / np.where(mx > 0, mx, 1.0), 0.0)
    hue = np.zeros_like(mx)
    pos = d > 0
    m_r = pos & (mx == r)
    m_g = pos & (mx == g) & ~m_r
    m_b = pos & (mx == b) & ~m_r & ~m_g
    hue[m_r] = np.mod((g[m_r] - b[m_r]) / d[m_r], 6.0)
    hue[m_g] = (b[m_g] - r[m_g]) / d[m_g] + 2.0
    hue[m_b] = (r[m_b] - g[m_b]) / d[m_b] + 4.0
    return hue * 60.0, sat


def photo_style_reason(path, style='tech_blue'):
    """背景图美学闸（SPEC §24）：离色板饱和像素 >25% / 边缘杂乱 >0.035 / 平均亮度 >0.5 拒。

    实测标定：用户即梦电影级底图 blue 符合度 0.93-0.996、edge 0.015-0.021、lum 0.23-0.37；
    纪实 stock（门诊白墙/黄色出租车/机房彩缆/森林手机）edge 0.024-0.059 或离色板一大片。
    水墨/亮白/纪实不吃色相窗，只查杂乱与过曝。

    只有"图读不出来"才返回 None（那是别的闸的活）；量算本身出错必须**抛出去**——
    闸静默返回"通过"就等于没有这道闸（旧写法把整个函数包在 except 里，缺依赖时全场放行）。
    """
    try:
        import numpy as np
        from PIL import Image
        a = np.asarray(Image.open(path).convert('RGB').resize((320, 180)),
                       dtype=np.float64) / 255.0
    except Exception:
        return None
    lum = a.mean(axis=2)
    hue, sat = _rgb_hue_sat(a)
    win = STYLE_HUE_WINDOW.get(style, (185, 255))
    if win:
        lo, hi = win
        inh = (hue >= lo) & (hue <= hi) if hi <= 360 else ((hue >= lo) | (hue <= hi - 360))
        off = float(((~inh) & (sat > 0.2)).mean())
        if off > 0.25:
            return u'离色板饱和像素占 %.0f%%（>%s 风格色板）：纪实照不是电影级背景，拒收' % (off * 100, style)
    edge = float((np.abs(np.diff(lum, axis=1)).mean() + np.abs(np.diff(lum, axis=0)).mean()) / 2)
    if edge > 0.035:
        return u'画面杂乱（边缘密度 %.3f>0.035）：背景要干净舞台感，拒收' % edge
    if float(lum.mean()) > 0.5:
        return u'平均亮度 %.2f>0.5：白昼纪实照压不住白字，拒收' % float(lum.mean())
    return None


def video_watermark_reason(path):
    """联网视频水印零容忍：时序叠加层 / 角标字形 / 烧字，任一命中即拒（不靠事后清理）。"""
    try:
        import lama_cleaner as LC
        import dewatermark as DW
        import numpy as np
        from PIL import Image
        if LC.detect_overlay_windows(path):
            return u'时序叠加层水印（淡入/漂移角标），拒收'
        ff = LC._ff()
        w, h, fps, dur = LC._dims(ff, path)
        if not w:
            return u'读不出视频流'
        step = max(1, int(fps or 25))
        for i, fr in enumerate(LC._iter_frames(ff, path, w, h)):
            if i % step:
                continue
            im = Image.fromarray(fr[:, :, ::-1])
            if DW.image_marks(im):
                return u'第 %d 帧角区有字形状角标，拒收' % i
            small = np.asarray(Image.fromarray(fr[:, :, ::-1]).resize((800, int(800 * h / w))))
            mask = DW.frame_text_mask(small, lift_min=10, gray_min=80, min_area=150, min_fill=0.15)
            if float((mask > 0).mean()) > 0.02:
                return u'第 %d 帧烧字（字幕/台标）占 %.1f%%，拒收' % (i, float((mask > 0).mean()) * 100)
            if i > step * 8:
                break
    except Exception as e:
        return u'水印检测没跑成（%s）' % str(e)[:60]
    return None


def _ff_bin():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def cmd_fetch_video(a):
    """先联网找无水印 CC 视频，找不到才回豆包/即梦兜底（SPEC §23）。

    候选逐词重试；下载后验规格+水印零容忍；通过才转码满铺 1080p 进 gen/，
    来源/许可/SHA1 记 video_source.json 与 materials/videos.jsonl，G28 仍会再核一遍。
    """
    wp = work_paths(a.work)
    shots = jr(wp['shots']) or die("先跑 plan")
    proxy = a.proxy or os.environ.get('CAOFENG_PROXY') or 'http://127.0.0.1:7897'
    redo = {int(x) for x in (a.only_page or [])}
    todo = [s for s in shots['pages'] if s.get('asset_kind') == 'video_bg'
            and (not find_gen(a.work, s['page'], 'video') or s['page'] in redo)]
    if not todo:
        print('视频页都已有产物，无需联网取')
        return
    got_n = 0
    vlog = os.path.join(wp['materials'], 'videos.jsonl')
    for s in todo:
        terms = list(s.get('search_terms_video') or s.get('search_terms') or [])
        if not terms:
            print(u'  页%d 没写 search_terms_video/search_terms，跳过（检索词必须从该页内容派）' % s['page'])
            continue
        done = False
        for term in terms:
            cands, err = search_commons_video(term, n=20, proxy=proxy)
            cands = [c for c in cands if min(int(c['w'] or 0), int(c['h'] or 0)) >= 1080
                     and 3 <= float(c['duration'] or 0) <= 120]
            for c in cands:
                r = reject_reason(c.get('page') or c.get('url'),
                                  sensitive_allow(jr(wp['outline'], {}) or {}))
                if r:
                    continue
                raw = os.path.join(wp['materials'], 'raw', 'v%03d%s' % (
                    s['page'], os.path.splitext(c['url'])[1].split('?')[0] or '.webm'))
                os.makedirs(os.path.dirname(raw), exist_ok=True)
                try:
                    with http_get(c['url'], proxy=proxy) as resp, open(raw, 'wb') as f:
                        shutil.copyfileobj(resp, f)
                except Exception as e:
                    print(u'  页%d 下载失败 %s：%s' % (s['page'], (c['page'] or '')[:40], str(e)[:60]))
                    _rm_quiet(raw)
                    continue
                wr = video_watermark_reason(raw)
                if wr:
                    print(u'  页%d 拒收 %s：%s' % (s['page'], (c['page'] or '')[:40], wr))
                    _rm_quiet(raw)
                    continue
                dest = os.path.join(wp['gen'], 'p%03d' % s['page'], 'video.mp4')
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                ff = _ff_bin()
                subprocess.run([ff, '-v', 'error', '-y', '-i', raw, '-t', '8',
                                '-vf', 'scale=1920:1080:force_original_aspect_ratio=increase,'
                                       'crop=1920:1080', '-c:v', 'libx264', '-crf', '18',
                                '-pix_fmt', 'yuv420p', '-an', dest], capture_output=True)
                if not os.path.exists(dest):
                    _rm_quiet(raw)
                    continue
                refresh_still_from_video(dest)
                jw(os.path.join(os.path.dirname(dest), 'video_source.json'),
                   {'src': c['url'], 'via': 'fetch-video', 'license': c['license'],
                    'artist': c['artist'], 'sha1': sha1(dest), 'at': now(),
                    'source_page': c.get('page')})
                with io.open(vlog, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({'page': s['page'], 'url': c['url'], 'license': c['license'],
                                        'term': term, 'sha1': sha1(dest), 'at': now()},
                                       ensure_ascii=False) + chr(10))
                print(u'  页%d 联网取到无水印视频：%s [%s]' % (s['page'], (c['page'] or '')[:44], c['license']))
                got_n += 1
                done = True
                break
            if done:
                break
        if not done:
            for _s in shots['pages']:
                if _s['page'] == s['page']:
                    _s['asset_kind'] = 'video_bg'
                    _s['need_material'] = True
            print(u'  页%d 联网没找到贴合的无水印视频 → 回豆包/即梦图生视频兜底（卡照发）' % s['page'])
    print(u'fetch-video 完成：联网取到 %d 条；其余视频页走投放卡兜底' % got_n)


def _rm_quiet(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def cmd_adopt(a):
    """用户手工下载回来的图：登记来源与许可才算数，不许裸文件直接进成品（SPEC §14）。"""
    wp = work_paths(a.work)
    src = os.path.abspath(a.adopt)
    if not os.path.exists(src):
        die("adopt 的文件不存在：%s" % src)
    sd = media_dims(src)
    if not sd:
        die("adopt 的文件无法解码为图片：%s" % src)
    if min(sd) < a.min_edge:
        die("adopt 的图短边只有 %dx%d，低于下限 %d：换高清档重新下载（Unsplash hover 点 ↓ '原始大小'）"
            % (sd[0], sd[1], a.min_edge))
    dest = normalize_asset(src, os.path.join(wp['materials'], 'p%03d.jpg' % a.page),
                           crop169=not a.keep_ratio)
    if not dest:
        die("adopt 归一化失败：%s" % src)
    lic = a.license or UNSPLASH_LICENSE
    if not a.url and lic == UNSPLASH_LICENSE:
        die("adopt 要举证来源：加 --url https://unsplash.com/photos/<id>（卡上写了怎么拿）")
    rec = {'page': a.page, 'term': a.term or '', 'file': dest, 'raw_file': src,
           'url': a.url or '', 'source_page': a.url or '',
           'license': lic, 'artist': a.artist or '',
           'w': sd[0], 'h': sd[1], 'relevance': 100,
           'stored_w': (media_dims(dest) or (0, 0))[0], 'stored_h': (media_dims(dest) or (0, 0))[1],
           'bytes': os.path.getsize(dest), 'sha1': sha1(dest), 'fetched_at': now(),
           'channel': 'unsplash-manual' if lic == UNSPLASH_LICENSE else 'adopted',
           'proxy_used': None}
    man_p = os.path.join(wp['materials'], 'manifest.jsonl')
    rows = []
    if os.path.exists(man_p):
        for ln in io.open(man_p, encoding='utf-8'):
            try:
                if json.loads(ln)['page'] != a.page:
                    rows.append(ln)
            except Exception:
                rows.append(ln)
    rows.append(json.dumps(rec, ensure_ascii=False) + "\n")
    io.open(man_p, 'w', encoding='utf-8').writelines(rows)
    gp = os.path.join(wp['materials'], 'gaps.json')
    prev = jr(gp, [])
    jw(gp, [g for g in prev if g.get('page') != a.page])
    print("页%-3d 已登记 adopt：%dx%d [%s] 作者 %s\n  来源 %s\n  -> %s" % (
        a.page, sd[0], sd[1], lic, rec['artist'] or '未署名', rec['url'], dest))
    print("  manifest 记录已覆盖该页旧条目（同页只留最新一条，SHA1 与像素都按现文件重算）")


def download_dirs(extra=None):
    """用户的下载文件夹候选（只读目录元数据，不碰别的东西）。"""
    cand = []
    bases = [os.environ.get('USERPROFILE') or '', os.path.expanduser('~')]
    pub = os.environ.get('PUBLIC')
    if pub:
        bases.append(pub)
    bases.append('D:\\')
    for base in bases:
        if not base or not os.path.isdir(base):
            continue
        for name in ('Downloads', u'下载'):
            d = os.path.join(base, name)
            if os.path.isdir(d) and d not in cand:
                cand.append(d)
    for d in (extra or []):
        dd = os.path.abspath(d)
        if os.path.isdir(dd) and dd not in cand:
            cand.append(dd)
    return cand


ASSET_DIR_NAME = u'PPT素材'
INBOX_NAME = u'收件'


def project_assets_dir(docx_path, create=True):
    """这一册的素材家：与 Word 文档同级的 PPT素材/ 文件夹。

    为什么写死成"跟着文档走"而不是固定盘符：用户（非程序员）在多个工作区做过多册 PPT，
    素材一旦落在 C:\\Users\\...\\Downloads 或某个绝对路径里，
    下一页、下一册就会串项目，agent 也分不清哪张图属于哪一页。
    所以规则只有一条：文档在哪，素材就在哪。
    """
    base = os.path.dirname(os.path.abspath(docx_path or '')) or os.path.abspath('.')
    d = os.path.join(base, ASSET_DIR_NAME)
    inbox = os.path.join(d, INBOX_NAME)
    if create:
        for sub in (d, inbox, os.path.join(d, '实拍'), os.path.join(d, 'AI产物')):
            if not os.path.isdir(sub):
                os.makedirs(sub)
    return d


def assets_inbox(docx_path):
    return os.path.join(project_assets_dir(docx_path), INBOX_NAME)


def pickup_dirs(docx_path, extra=None):
    """pickup 的搜索顺序：项目收件夹 → 项目素材夹 → 系统下载文件夹（最后兜底）。"""
    out = []
    for d in (assets_inbox(docx_path), project_assets_dir(docx_path)):
        if os.path.isdir(d) and d not in out:
            out.append(d)
    out += download_dirs(extra)
    keep = []
    for d in out:
        if d not in keep:
            keep.append(d)
    return keep


def claimed_sha1(wp):
    return set(jr(os.path.join(wp['work'], 'gen_claims.json'), []) or [])


def dewatermark_originals(wp, gen_path):
    """找 gen/pNNN 这份投放产物在 AI产物\\pNNN 里的原件（去标疤尺子要拿它对照）。

    pickup 的次序是"先把下载原件复制进 AI产物 留证，再在 gen/ 副本上去标"，所以原件
    一定带角标、且与 gen/ 那份同扩展名。同名多个就都返回，由调用方取最有利的一档，
    免得把合法的暗背景冤枉成疤。"""
    try:
        pdir = os.path.basename(os.path.dirname(os.path.abspath(gen_path)))
        ext = os.path.splitext(gen_path)[1].lower()
        docx = (jr(wp.get('outline') or '', {}) or {}).get('docx') or ''
        adir = project_assets_dir(docx, create=False) if docx else ''
        keep = os.path.join(adir, 'AI产物', pdir) if adir else ''
        if not keep or not os.path.isdir(keep):
            return []
        out = [os.path.join(keep, f) for f in sorted(os.listdir(keep), reverse=True)
               if os.path.splitext(f)[1].lower() == ext]
        return out[:3]
    except Exception:
        return []


def still_same_crop(video_path, crop):
    """把视频用的裁切矩形按比例作用于同页静帧，写出干净的首帧参考图（image.png/last.png）。

    裁角后的视频第 0 帧与"没裁的静帧"构图不同，直接比 MAD 会误报；而拿视频第 0 帧自己当
    参考图又是恒等自证。正确尺子 = 用户递来的静帧经过同一裁切（水印带同样被裁掉）。"""
    from PIL import Image
    d = os.path.dirname(video_path)
    src = os.path.join(d, 'image.png')
    if not os.path.exists(src):
        return []
    cx, cy, cw, ch = crop['crop']
    vw, vh = crop.get('src_size') or (1920, 1080)
    try:
        im = Image.open(src).convert('RGB')
        sw, sh = im.size
        box = (int(cx * sw / float(vw)), int(cy * sh / float(vh)),
               int((cx + cw) * sw / float(vw)), int((cy + ch) * sh / float(vh)))
        out = im.crop(box)
        made = []
        for name in ('image.png', 'last.png'):
            out.save(os.path.join(d, name))
            made.append(name)
        return made
    except Exception:
        return []


def refresh_still_from_video(video_path):
    """把这一页的首帧参考图换成"视频自己的第 0 帧"，并留一句可核对的说明。

    为什么必须换：G15 的动因就是"poster 与视频第 0 帧不是同一个画面=图生视频跑偏"。
    实测用户回件的封面视频主体是算力芯片展台，而上一版认领的静帧是卡车——
    两者都对不上（逐帧 MAD 最小 25.4/255）。清洗链裁过段之后，视频起点也不再是
    原片第 0 帧。语料的做法（教程"末帧做封面"+模板封面）是封面用视频自己的帧，
    所以这里以成片为准刷新静帧：页面自洽，蒙版压暗量也按这一帧的真实亮度反推。
    AI产物 里的原件一个字没动，溯源仍按原件核。"""
    d = os.path.dirname(video_path)
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return []
    made = []
    for name in ('image.png', 'last.png'):
        dst = os.path.join(d, name)
        tmp = dst + '.f0.png'
        cmd = [ff, '-v', 'error', '-y', '-i', video_path, '-frames:v', '1', tmp]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120)
            if os.path.exists(tmp) and os.path.getsize(tmp) > 8192:
                if os.path.exists(dst):
                    os.remove(dst)
                os.replace(tmp, dst)
                made.append(name)
            elif os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return made


def dewatermark_landing(dest_path):
    """认领后自动去水印：豆包/即梦免费档产物带平台角标，落在 gen/ 副本上磨掉；
    AI产物 里的原件一个字都不动（G21 溯源要拿原件对指纹）。返回一句人话或 None。

    视频先抽 16 帧快检，本无角标就不重编码；带角标只走逐帧 TELEA（241 帧 720p 实测 24 秒）。
    ProPainter 深度路线本机实测 78 分钟未吐帧，绝不在认领里同步跑，只把命令交给 agent 挂机。"""
    try:
        import dewatermark as DW
    except Exception:
        return None
    ext = os.path.splitext(dest_path)[1].lower()
    tmp = dest_path + '.nowm' + ext
    try:
        if ext in ('.mp4', '.mov', '.webm', '.m4v'):
            # 视频走"时序叠加层"这条：豆包免费档的水印会淡入、漂移、溶解，抽一角检会漏，
            # 逐帧角区 TELEA 又会把场景彩光当水印补成疤。时序检测自己把它分离出来，
            # 清完只保留"强命中"连续段并 ping-pong 成无缝循环——封面满铺背景要的正是这个。
            res = None
            try:
                import lama_cleaner as LC
                if not LC.detect_overlay_windows(dest_path):
                    return u'时序复检：全片没有叠加层，原样收下'
                res = LC.clean_cover_video(dest_path, tmp)
            except Exception:
                res = None
            if res is None:
                if DW.sample_video_marks(dest_path, 16) <= 1:
                    return u'抽帧复检：无角标，原样收下'
                res = DW.clean_video(dest_path, tmp)
        else:
            res = DW.clean_image(dest_path, tmp)
    except Exception as e:
        return u'水印检测没跑成（%s）——这一页要人眼复核' % str(e)[:48]
    if res.get('out') and os.path.exists(res['out']):
        if not res.get('clean'):
            # 没验过就不许把"清坏的版本"顶上去：实测 classic 会把压在霓虹弧上的角标
            # 连弧补成一块黑疤，比原片的水印更扎眼。原件留在 gen/ 让 G28 拦着，
            # 由人决定 --deep 挂机清还是无水印重导。
            os.remove(res['out'])
            note = u'水印没清干净（%s），已放弃这一版、gen/ 里仍是原片——机验不过不许上屏。%s' % (
                u'、'.join(res.get('words') or []) or u'角标形状',
                res.get('note') or u'要 100% 干净用 --deep 或无水印重导')
            if res.get('deep_cmd'):
                note += u'\n     要 100%% 干净就挂机跑这一条（本机实测 78 分钟未吐帧，别在认领里等）：\n     %s' % res['deep_cmd']
            return note
        shutil.move(res['out'], dest_path)
        if str(res.get('method', '')).endswith('pingpong'):
            if res.get('crop'):
                # 视频裁了角，首帧参考图必须用**同一裁切**作用于用户递来的那张静帧：
                # 拿视频自己的第 0 帧当尺子是自证（恒等），拿没裁的静帧当尺子又因裁切跑偏而误报。
                made = still_same_crop(dest_path, res['crop'])
                if not made:
                    made = refresh_still_from_video(dest_path)
            else:
                made = refresh_still_from_video(dest_path)
            return (u'水印已真删除并接成无缝循环（保留 %.2f–%.2fs，循环 %.2fs，段内强命中 %d）；'
                    u'首帧参考图按语料封面规则换成成片第 0 帧（%s）' % (
                        res['kept_span'][0], res['kept_span'][1],
                        res.get('loop_seconds') or 0, res.get('strong_left_after', -1),
                        u'、'.join(made) or u'换帧失败——G15 会拦'))
        note = u'水印已去掉（检出 %s；机验通过）' % (
            u'、'.join(res.get('words') or []) or u'角标形状')
        return note
    return None


def note_claim(wp, digest):
    p_ = os.path.join(wp['work'], 'gen_claims.json')
    rows = jr(p_, []) or []
    if digest not in rows:
        rows.append(digest)
        jw(p_, rows)


def page_named_candidates(dirs, page, kind):
    """收件夹里**按页号命名**的候选（p011_image.png / p011_video.mp4，中文别名也认）。

    用户一次下载好几页的图和视频时，"最近 45 分钟里 newest first" 会认错页——
    实测用户明确要求"命名规则写进卡里，避免 agent 识别错对应关系"。页号命名优先于时间猜测；
    没有页号命名且同类候选多于一个时，pickup 必须停下来问，不许猜。
    """
    kw = {'image': ('image', 'img', '图', '底图', '首帧', 'pic', 'picture'),
          'first': ('first', '首帧', '图'),
          'last': ('last', '尾帧', '图'),
          'video': ('video', 'vid', '视频', 'mp4'),
          'gif': ('gif', '动图', 'anim')}
    exts = {'image': ('.png', '.jpg', '.jpeg', '.webp'),
            'first': ('.png', '.jpg', '.jpeg', '.webp'),
            'last': ('.png', '.jpg', '.jpeg', '.webp'),
            'video': ('.mp4', '.mov', '.m4v'),
            'gif': ('.gif', '.apng', '.webp')}
    keys = kw.get(kind, kw['image'])
    want = exts.get(kind, exts['image'])
    out = []
    for d in dirs or []:
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for f in names:
            fp = os.path.join(d, f)
            if not os.path.isfile(fp) or os.path.splitext(f)[1].lower() not in want:
                continue
            stem = os.path.splitext(f)[0].lower()
            m = re.match(r'^p0*(\d+)(?:[_\-\s](.*))?$', stem)
            if not m or int(m.group(1)) != page:
                continue
            tail = (m.group(2) or '').strip()
            if tail and not any(t in tail for t in keys):
                continue
            out.append(fp)
    out.sort(key=lambda x: -os.path.getmtime(x))
    return out


def scan_downloads(kinds, within_min=45, dirs=None, skip=None):
    """按类型在最近下载的文件里挑，新的在前；skip 是别页已经认领过的 SHA1。"""
    exts = {'image': ('.png', '.jpg', '.jpeg', '.webp'),
            'first': ('.png', '.jpg', '.jpeg', '.webp'),
            'last': ('.png', '.jpg', '.jpeg', '.webp'),
            'video': ('.mp4', '.mov', '.m4v'),
            'gif': ('.gif', '.apng', '.webp')}
    want = set()
    for k in kinds:
        want |= set(exts.get(k, exts['image']))
    since = datetime.now().timestamp() - within_min * 60.0
    out = []
    for d in dirs:
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for f in names:
            fp = os.path.join(d, f)
            if not os.path.isfile(fp):
                continue
            if os.path.splitext(f)[1].lower() not in want:
                continue
            try:
                if os.path.getmtime(fp) < since:
                    continue
            except Exception:
                continue
            out.append(fp)
    out.sort(key=lambda x: -os.path.getmtime(x))
    if skip:
        keep = []
        for fp in out:
            try:
                if sha1(fp) in skip:
                    continue
            except Exception:
                continue
            keep.append(fp)
        out = keep
    return out


def card_kinds(w, page):
    """这一页提示词卡上写了哪些产物类型。"""
    pp = os.path.join(w, 'promptpack')
    got = []
    for k in ('image', 'first', 'last', 'video', 'gif'):
        if os.path.exists(os.path.join(pp, 'p%03d_%s.txt' % (page, k))):
            got.append(k)
    return got


def cmd_pickup(a):
    """一页一轮的回收：把刚下载的那张图/那段视频认进 gen/pNNN/，并当场量分辨率与时长。

    为什么要这条命令：投放卡是一册四十多万字的 md，用户（非程序员）既要读懂
    "放到 gen/p013/image.png"又要手动改名，实测就卡死在这一步。
    现在用户只做一件事——在即梦里点下载——找文件、验规格、改名、登记全在这里做。
    """
    wp = work_paths(a.work)
    w = wp['work']
    sh = jr(wp['shots'])
    if not sh:
        die("pickup 前先跑 scaffold（没有 shots.json）")
    pages = {s['page']: s for s in sh['pages']}
    if a.page not in pages:
        die("页 %s 不在分镜里（1–%d）" % (a.page, len(pages)))
    want = [x.strip() for x in (a.kind or '').split(',') if x.strip()] or \
        card_kinds(w, a.page) or ['image']
    ol = jr(wp['outline'], {}) or {}
    dirs = pickup_dirs(ol.get('docx') or '', a.dir)
    if not dirs:
        die(u"这一册的素材文件夹还没建（先跑 plan，它会在 Word 旁边建 PPT素材\\收件），"
            u"或用 --dir 指一个")
    skip = claimed_sha1(wp)
    picked, rejected = [], []
    for k in want:
        if a.src:
            cands = [os.path.abspath(a.src)]
        else:
            cands = [c for c in page_named_candidates(dirs, a.page, k)
                     if not (skip and sha1(c) in skip)]
            if not cands:
                loose = scan_downloads([k], a.within, dirs, skip)
                if len(loose) > 1:
                    _ex = ('video.mp4' if k == 'video' else
                           ('anim.gif' if k == 'gif' else 'image.png'))
                    die(u"收件里最近 %d 分钟有 %d 个「%s」候选、又都没有页号命名，我不能猜哪个是第 %d 页的：%s。"
                        u"请把这一页的文件改名为 p%03d_%s（视频也可叫 p%03d_视频.mp4）再跑 pickup，"
                        u"或用 --src 直接指文件全路径。命名规则在投放卡上写着。"
                        % (a.within, len(loose), k, a.page,
                           u'、'.join(os.path.basename(x) for x in loose[:6]),
                           a.page, _ex, a.page))
                cands = loose
        if not cands:
            rejected.append((k, u'最近 %d 分钟里没有该类型的文件（查过 %s）'
                             % (a.within, u'、'.join(dirs))))
            continue
        for fp in cands[:8]:
            if k == 'video':
                dm = media_dims(fp)
                dur = probe_duration(fp) or 0
                if not dm:
                    rejected.append((k, u'%s 读不出视频流' % os.path.basename(fp)))
                    continue
                if min(dm) < a.min_edge and not a.force:
                    rejected.append((k, u'%s 只有 %dx%d，短边低于 %d——在即梦换高清/2K 档重下；'
                                     u'实在只有这一档且用户同意用静态：waive --page %d --note 用户原话'
                                     % (os.path.basename(fp), dm[0], dm[1], a.min_edge, a.page)))
                    continue
                if dur < 3:
                    rejected.append((k, u'%s 时长只有 %.1f 秒，不像一段可用的动态背景'
                                     % (os.path.basename(fp), dur)))
                    continue
                dest = os.path.join(w, 'gen', 'p%03d' % a.page,
                                    'video%s' % (os.path.splitext(fp)[1].lower() or '.mp4'))
            else:
                dm = None
                try:
                    from PIL import Image
                    with Image.open(fp) as _im:
                        dm = _im.size
                except Exception:
                    dm = None
                if not dm:
                    rejected.append((k, u'%s 解不开成图片' % os.path.basename(fp)))
                    continue
                if min(dm) < a.min_edge and not a.force:
                    rejected.append((k, u'%s 只有 %dx%d，短边低于 %d——换最高分辨率档重下'
                                     '（真要用这张就加 --force）'
                                     % (os.path.basename(fp), dm[0], dm[1], a.min_edge)))
                    continue
                ext = '.gif' if k == 'gif' else '.png'
                dest = os.path.join(w, 'gen', 'p%03d' % a.page, k + ext)
            dd = os.path.dirname(dest)
            if not os.path.isdir(dd):
                os.makedirs(dd)
            shutil.copy2(fp, dest)
            if dm and min(dm) < a.min_edge:
                # --force 知情收下低清：必须留痕，check 会把它写进报告显式声明，不许静默混过
                jw(os.path.join(dd, 'lowres_force.json'),
                   {'page': a.page, 'kind': k, 'file': os.path.basename(fp),
                    'dims': list(dm), 'min_edge': a.min_edge, 'at': now()})
            elif os.path.exists(os.path.join(dd, 'lowres_force.json')):
                # 后来重导了达标版本：旧的低清留痕必须清掉，不然报告会继续误声明
                os.remove(os.path.join(dd, 'lowres_force.json'))
            _keep = os.path.join(project_assets_dir(ol.get('docx') or ''),
                                 'AI产物', 'p%03d' % a.page)
            if os.path.abspath(_keep) != os.path.abspath(dd) and os.path.isdir(
                    os.path.dirname(_keep) or '.'):
                if not os.path.isdir(_keep):
                    os.makedirs(_keep)
                shutil.copy2(fp, os.path.join(_keep, os.path.basename(fp)))
                dest2 = os.path.join(_keep, os.path.basename(fp))
            dw_note = dewatermark_landing(dest)
            picked.append((k, fp, dest, dm, os.path.getsize(dest), dw_note))
            note_claim(wp, sha1(fp))
            if k == 'video':
                # 用户直接递视频（没走投放卡）时的来源凭证：G15 认"凭证+首帧一致"，
                # 不逼一张不存在的卡；凭证记原文件路径与 SHA1，溯源可查。
                # attachment 记的是**用户当时被要求上传的那张首帧**：封面规则随后会把
                # gen/pNNN/image.png 换成视频自己的第 0 帧（语料"封面用视频自己的帧"），
                # 那时再拿 image.png 当尺子就恒等成立——第 3 页烧进一个巨大"02"正是这样漏掉的。
                _vpc = os.path.join(wp['promptpack'], 'p%03d_video.txt' % a.page)
                _att_rec = None
                for _pk in picked:
                    if _pk[0] in ('image', 'first') and os.path.exists(_pk[1]):
                        _att_rec = os.path.abspath(_pk[1])
                        break
                if not _att_rec and os.path.exists(_vpc):
                    for _x in re.findall(r'([A-Za-z]:\\[^\s，。]+?\.(?:png|jpe?g))',
                                         io.open(_vpc, encoding='utf-8').read()):
                        if os.path.isfile(_x):
                            _att_rec = os.path.abspath(_x)
                            break
                _att_rec = _att_rec or card_first_frame(a.work, a.page)
                jw(os.path.join(os.path.dirname(dest), 'video_source.json'),
                   {'src': os.path.abspath(fp), 'sha1': sha1(fp),
                    'attachment': _att_rec,
                    'at': datetime.now().isoformat(timespec='seconds'),
                    'via': 'pickup --src' if a.src else 'pickup scan',
                    'card': os.path.exists(_vpc)})
            break
        else:
            if not any(x[0] == k for x in picked):
                rejected.append((k, u'%s 的候选都没过规格' % os.path.basename(cands[0])))
    jw(os.path.join(w, 'gen', 'pickup_last.json'),
       {'page': a.page,
        'picked': [{'kind': k, 'from': f, 'to': d, 'dims': list(m), 'bytes': b,
                    'dewatermark': nw}
                   for k, f, d, m, b, nw in picked],
        'rejected': [[kk, vv] for kk, vv in rejected]})
    if not picked:
        die(u"页%s 没认到任何产物：%s\n  文件确实下载好了的话，把它的完整路径贴给 --src，"
            u"或用 --dir 指对下载文件夹" % (
                a.page, u'；'.join(u'%s：%s' % r for r in rejected) or u'无候选'))
    for k, f, d, m, b, nw in picked:
        print(u"页%-3d 认领 %s：%s  %dx%d  %.1f MB\n     -> %s" % (
            a.page, k, os.path.basename(f), m[0], m[1], b / 1024.0 / 1024.0, d))
        if nw:
            print(u"     水印：%s" % nw)
    if rejected:
        print(u"  这一页还缺：%s" % u'；'.join(u'%s（%s）' % r for r in rejected))
    print(u"  下一页继续；这一册都放完后跑 ack --work %s --ready，再 build + check" % a.work)
    print(u"  已经认领过的文件不会在下一页被重复认领（gen_claims.json 记 SHA1）")


CARD_KIND_LABEL = {
    'image': u'文生图（这一页的静态底图）',
    'first': u'首帧（动态背景的第一帧）',
    'last': u'尾帧（动态背景的最后一帧）',
    'video': u'图生视频（把上面那张图当首帧，生成约 8 秒动态背景）',
    'gif': u'图生动图（可叠在页面上的循环动图）',
}


def card_first_frame(work, page):
    """这一页图生视频真正该上传的首帧附件；**公共色场不算专属图**（返回 None）。

    动因（2026-11-09 实测）：20 个过渡页的 out/bg_fallback/pNNN.jpg 两两像素差只有
    0.07–1.7（全是同一张深蓝渐变），拿它当首帧等于让用户花 20 次生成做同一个背景，
    还把 G15 的比对尺子顶平——第 3 页视频里烧进一个巨大"02"就是这样溜过去的。
    """
    for f in (find_gen(work, page, 'image'), find_gen(work, page, 'first')):
        if f and os.path.exists(f):
            return f
    man_p = os.path.join(work, 'materials', 'manifest.jsonl')
    if os.path.exists(man_p):
        for ln in io.open(man_p, encoding='utf-8'):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            fp = r.get('file') or ''
            if r.get('page') == page and fp and os.path.exists(fp) and \
                    'bg_fallback' not in fp.replace('\\', '/').lower():
                return fp
    return None


def cmd_card(a):
    """一页一轮的投放：把这一页的提示词整段打到 stdout，agent 原样贴给用户。

    为什么要有这条命令：delivery_card.md 一册 238 页有 40~90 万字，用户（非程序员）
    既读不动也不知道先做哪页，实测就卡在这一步（2026-11-07 反馈"太不利于我操作了"）。
    现在一次只出一页，发完等回执 → pickup 认领 → 再发下一页。
    """
    wp = work_paths(a.work)
    w = wp['work']
    req = jr(os.path.join(wp['promptpack'], DG.REQ_NAME))
    if not req:
        die(u"还没有投放请求单（%s）——先跑 pack 生成提示词卡" % DG.REQ_NAME)
    # 队列必须按 gen/ 现状重算（req 里的 present 是 pack 那一刻的快照，pickup 之后不刷新，
    # 直接用会把已回收的页再排进队首）；用户已豁免的页（video_waiver.json）也不再催。
    items = DG.required_items_from_req(req, TH)
    _wv = jr(os.path.join(w, 'video_waiver.json'), None)
    _waived = set()
    for _e in (_wv if isinstance(_wv, list) else [_wv] if _wv else []):
        if isinstance(_e, dict) and _e.get('page'):
            _waived.add((_e['page'], _e.get('kind') or 'video'))
    pend = [i for i in items if not i['present'] and (i['page'], i['kind']) not in _waived]
    if a.page:
        want = [i for i in items if i['page'] == a.page]
        if not want:
            die(u"第 %s 页没有投放产物（表页与纯文字页本来就不需要 AI 素材），"
                u"这页直接跳过" % a.page)
    else:
        if not pend:
            print(u"投放产物 %d 项都已回收，可以 ack --ready 再 build" % len(items))
            return
        # 只有视频必须由用户去网页工具生成；图片 fetch 也能补，所以队列先排 video/gif 那几页
        prio = [i for i in pend if i['kind'] in ('video', 'gif', 'first', 'last')] or pend
        a.page = min(i['page'] for i in prio)
        want = [i for i in prio if i['page'] == a.page]
    sh = jr(wp['shots']) or {}
    pg = {s['page']: s for s in (sh.get('pages') or [])}.get(a.page) or {}
    ol = jr(wp['outline'], {}) or {}
    kinds = []
    for i in want:
        if i['kind'] not in kinds:
            kinds.append(i['kind'])
    # 没有这一页专属的静帧时，先催文生图：把公共色场当首帧=20 页同一个背景（card_first_frame）
    _att = card_first_frame(a.work, a.page)
    if _att is None and any(k in ('video', 'gif') for k in kinds) and 'image' not in kinds:
        kinds.insert(0, 'image')
    print(u'═' * 50)
    print(u"第 %d 页 ｜ %s ｜ %s" % (
        a.page, pg.get('role') or u'?', u' '.join(
            [(pg.get('headline') or u'').strip()[:28],
             (pg.get('tech_recipe') or u'').strip()[:24]])
        .strip()))
    print(u"这一页要你还回的文件：%s" % u'、'.join(CARD_KIND_LABEL.get(k, k) for k in kinds))
    for k in kinds:
        f = os.path.join(wp['promptpack'], 'p%03d_%s.txt' % (a.page, k))
        if not os.path.exists(f):
            print(u"  （缺 %s 的提示词文件，重跑 pack 生成）" % k)
            continue
        txt = io.open(f, encoding='utf-8').read().strip()
        print(u"\n┌ %s ｜ 复制下面这一整段，粘进即梦/豆包的「描述」框 ┐" % CARD_KIND_LABEL.get(k, k))
        print(txt)
    neg_f = os.path.join(wp['promptpack'], 'p%03d_negative.txt' % a.page)
    if os.path.exists(neg_f):
        print(u"\n┌ 负向词 ｜ 粘进「不希望看到」框 ┐")
        print(io.open(neg_f, encoding='utf-8').read().strip())
    dirs = pickup_dirs(ol.get('docx') or '', None)
    print(u"\n【下载后怎么命名】一次只下一页时不用改名；**同时下好几页时必须按页号命名**，"
          u"否则我会停下来问、不会猜：")
    if 'image' in kinds or _att is None:
        print(u"  这一页的图 → p%03d_image.png（.jpg 也行；叫 p%03d_图.png 也认）" % (a.page, a.page))
    if 'video' in kinds:
        print(u"  这一页的视频 → p%03d_video.mp4（叫 p%03d_视频.mp4 也认）" % (a.page, a.page))
    if 'gif' in kinds:
        print(u"  这一页的动图 → p%03d_anim.gif" % a.page)
    print(u"  改好名丢进收件夹，然后回我一个「好」。")
    print(u"  我会跑：python %s pickup --work %s --page %d" % (
        os.path.abspath(__file__), a.work, a.page))
    print(u"  认领时看这些文件夹（新→旧）：%s" % (u'、'.join(dirs) or u'（还没建，先跑 plan）'))
    print(u"  页号命名优先；没命名且同类候选只有一个才按时间认，多于一个我列出文件名问你。")
    print(u"  当场验规格：短边 ≥ %d 像素%s，不合格我会让你换高清档重下这一页" % (
        1080, u'、视频时长 ≥ 3 秒' if 'video' in kinds else u''))
    if 'video' in kinds:
        if _att is None:
            print(u"  这一页还没有**专属静帧**：装配现在铺的是公共深蓝渐变底图，"
                  u"20 个过渡页两两像素差只有 0.07–1.7（同一张图），拿它当首帧等于花 20 次生成"
                  u"做同一个背景。所以这一页分两步：先按上面那段「文生图」出图并下载，"
                  u"再把刚下载的那张图当「图生视频」的首帧附件。")
        else:
            print(u"  这一页的首帧附件=%s（这页真正上屏的那张图，只认它）" % _att)
        print(u"  导出请勾「无水印」档（即梦/豆包会员导出可选）：免费档视频右下角烧"
              u"「豆包 AI 生成」且随画面漂，本机两条清理路线实测都不合格——逐帧补帧会把角标"
              u"连霓虹弧一起补成一块黑疤（8 个时间点逐帧目检否决），深度补帧一条片子要跑一个"
              u"多小时。实在只有带角标的版本：这页改用已去净的静态图做底（动效靠缓慢推镜），"
              u"我在报告里如实标「降级交付_无视频背景」。")
    left = sorted({i['page'] for i in pend if i['page'] != a.page})
    if left:
        print(u"这册还差 %d 页（下一页是第 %d 页）" % (len(left), left[0]))
    else:
        print(u"这册就这一页待回收")


def cmd_materials_review(a):
    """素材目检留痕（G3 硬要求）：先拼联系表给 agent 真看，再逐页记 keep/reject。

    词面相关度满分也拦不住语义离题（渡轮当车队/历史版画当救护车实测过），所以"看过"必须留痕：
    review.json 每页 verdict=keep 才许上屏；reject 的摘出 manifest 并移进 materials/rejected/。
    """
    wp = work_paths(a.work)
    man_p = os.path.join(wp['materials'], 'manifest.jsonl')
    man = ([json.loads(l) for l in io.open(man_p, encoding='utf-8') if l.strip()]
           if os.path.exists(man_p) else [])
    if getattr(a, 'set', None):
        rev = jr(os.path.join(wp['materials'], 'review.json'), {}) or {}
        rej = []
        for kv in a.set.split(','):
            if '=' not in kv:
                continue
            k, v = kv.split('=', 1)
            if not k.strip().isdigit():
                continue
            pg = int(k.strip())
            verdict, _, note = v.partition(':')
            rev[str(pg)] = {'verdict': verdict.strip(), 'note': note.strip(), 'at': now()}
            if verdict.strip() == 'reject':
                rej.append(pg)
        jw(os.path.join(wp['materials'], 'review.json'), rev)
        if rej:
            os.makedirs(os.path.join(wp['materials'], 'rejected'), exist_ok=True)
            keep = []
            for m in man:
                if m['page'] in rej:
                    f = m.get('file')
                    if f and os.path.exists(f):
                        shutil.move(f, os.path.join(wp['materials'], 'rejected',
                                                    os.path.basename(f)))
                    continue
                keep.append(m)
            io.open(man_p, 'w', encoding='utf-8').write(
                ''.join(json.dumps(m, ensure_ascii=False) + chr(10) for m in keep))
        print(u'目检台账已记 %d 页；reject %s 已摘出 manifest 并移入 materials/rejected/' % (len(rev), rej))
        return
    from PIL import Image, ImageDraw
    man = [m for m in man if os.path.exists(m.get('file') or '')]
    tw, th, cols = 300, 169, 5
    rows = (len(man) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * (tw + 6), max(1, rows) * (th + 22)), 'black')
    d = ImageDraw.Draw(sheet)
    for i, m in enumerate(man):
        im = Image.open(m['file']).convert('RGB')
        im.thumbnail((tw, th))
        x, y = (i % cols) * (tw + 6), (i // cols) * (th + 22)
        sheet.paste(im, (x, y + 20))
        d.text((x + 2, y + 4), 'p%03d %s' % (m['page'], (m.get('term') or '')[:22]), fill='white')
    out = os.path.join(wp['materials'], 'review_sheet.png')
    sheet.save(out)
    print(u'联系表已生成：%s（%d 张）。agent 必须真看这张图逐张对页面主体，然后跑：' % (out, len(man)))
    print(u'  caofeng.py materials-review --work <目录> --set "3=keep,4=reject:渡轮离题,…"')
    print(u'  没记 keep 的页 G3 会拦；reject 的自动摘出 manifest，缺口走矢量色场或换词重取。')


def cmd_fetch(a):
    wp = work_paths(a.work)
    if getattr(a, 'adopt', None):
        if not getattr(a, 'page', None):
            die("fetch --adopt 需要 --page 页码")
        cmd_adopt(a)
        return
    shots = jr(wp['shots'])
    outline = jr(wp['outline'])
    if not shots or not outline:
        die("先跑 plan")
    # validate the allow list before anything else: a typo like --allow-sensitive everything
    # would otherwise silently disable screening, and must fail even when the queue is empty
    allow = sensitive_allow(outline, getattr(a, 'allow_sensitive', None))
    if allow:
        print("按 Word 主题放行敏感类别：%s（其余类别照常拦截）" % '、'.join(allow))
    proxy = a.proxy or os.environ.get('CAOFENG_PROXY') or 'http://127.0.0.1:7897'
    source = getattr(a, 'source', 'commons') or 'commons'
    if source == 'openverse' and not openverse_token():
        print(u'提醒：Openverse 匿名调用有速率上限（本轮实测可用；被限流时设 '
              + OPENVERSE_TOKEN_ENV + u' 再跑）')
    if source == 'unsplash' and not unsplash_key():
        die("--source unsplash 需要环境变量 %s（实测 napi 与 HTML 抓取都回 401，"
            "没有密钥就走投放卡里的 Unsplash 检索 URL + fetch --adopt 登记）" % UNSPLASH_KEY_ENV)
    print("取图路由：源=%s；国外源走代理 %s；国内源直连" % (source, proxy))
    man_p = os.path.join(wp['materials'], 'manifest.jsonl')
    have = {}
    if os.path.exists(man_p):
        for ln in io.open(man_p, encoding='utf-8'):
            try:
                r = json.loads(ln)
                have[r['page']] = r
            except Exception:
                pass
    redo = {int(x) for x in (a.only_page or [])}
    # --only-page forces the page back into the queue even if the shot plan called it a colour
    # field, and we prune the manifest only for pages we are actually going to re-fetch --
    # pruning first then skipping the fetch once destroyed a perfectly good asset record.
    # 联网优先（SPEC §24，用户 2026-11-10 纠正）：先联网逐词重试搜到过闸为止，
    # 搜尽才回豆包/即梦兜底。source=stock 的举证页豁免美学闸（要真实照片），但仍查水印/相关度。
    todo = [s for s in shots['pages']
            if (s.get('need_material') and s['page'] not in have) or s['page'] in redo
            or (s.get('search_terms') and s['page'] not in have)]
    if redo:
        pruned = redo & {s['page'] for s in todo}
        if pruned and os.path.exists(man_p):
            keep = []
            for ln in io.open(man_p, encoding='utf-8'):
                try:
                    if json.loads(ln)['page'] not in pruned:
                        keep.append(ln)
                except Exception:
                    keep.append(ln)
            io.open(man_p, 'w', encoding='utf-8').writelines(keep)
            for n in pruned:
                have.pop(n, None)
            print("重取页：%s（已摘除这些页的旧 manifest 记录）" % sorted(pruned))
        for n in redo - pruned:
            print("  页%d 不在待取列表（该页 shot 计划是矢量色场），未重取也未删记录" % n)
    if not todo:
        print("素材齐了（%d 页），无需再取" % len(have))
        return
    gaps = []
    override = {}
    for kv in (a.term or []):
        if '=' in kv:
            k, v = kv.split('=', 1)
            if k.strip().isdigit():
                override[int(k.strip())] = v.strip()
    terms = {}
    for s in todo:
        t = override.get(s['page'])
        tl = [t] if t else list(s.get('search_terms') or [])
        if tl:
            terms[s['page']] = tl
    no_term = sorted({s['page'] for s in todo} - set(terms))
    if no_term:
        print(u"  %d 页这轮没给 --term，不动它们：检索词必须由人/agent 判断，"
              u"不自动拿实体词兜底，否则会把不相干的图铺到页上" % len(no_term))
    fetched = 0
    J = io.open(man_p, 'a', encoding='utf-8')
    for s in todo:
        term_list = terms.get(s['page']) or []
        if not term_list:
            continue          # 这轮没给检索词：旧缺口说明原样保留
        for term in term_list:
            pg = outline['pages'][s['page'] - 1]
            cands, err, relaxed_hits = [], None, []
            for _t in term_list:
                _c, _e, _r = fetch_candidates(_t, source, proxy, min_edge=a.min_edge)
                for _x in _c:
                    _x.setdefault('term_used', _t)
                cands += _c
                relaxed_hits += _r
                err = err or _e
            for name, t in relaxed_hits:
                print(u'  页%-3d %s 通道把检索词退到前 3 个词才命中：%s → %s'
                      % (s['page'], name, term, t))
            if re.search(u'[一-龥]', term):
                gaps.append({'page': s['page'], 'term': term,
                             'why': '检索词含中文：Commons 是英文索引，中文抽象词只会捞出无关文件。'
                                    '请用 --term "页码=英文具体实物名"（要搜画面里能看见的东西）'})
                print("  页%-3d 拒用中文检索词：%s（改英文再取）" % (s['page'], term))
                continue
            cands = [c for c in cands if min(int(c['w'] or 0), int(c['h'] or 0)) >= a.min_edge
                     and str(c['mime']).startswith('image/')]
            blocked = []
            kept = []
            for c in cands:
                r = reject_reason(' '.join([str(c.get('title') or ''), str(c.get('page') or '')])
                                  if c.get('title') else c.get('page'), allow)
                if r:
                    blocked.append((r, c.get('page')))
                else:
                    kept.append(c)
            cands = kept
            if blocked:
                gaps.append({'page': s['page'], 'term': term,
                             'why': '拦下 %d 张不合适候选（%s）' % (
                                 len(blocked), '; '.join('%s:%s' % (k, (t or '')[:40]) for k, t in blocked[:3]))})
            for c in cands:
                # 标题文本要一起进相关度：Commons 的'页'就是文件名（含主体词），
                # 而 Openverse/Unsplash 的'页'是落地 URL，只看它会整批判 0%（实测踩过）
                # 相关度按这一路真正用的检索词算（退过词的候选不能拿全称来评）
                c['rel'] = relevance(u' '.join([str(c.get('page') or ''),
                                                str(c.get('title') or '')]),
                                     [c.get('term_used') or term] + (pg.get('entities') or [])[:4])
            cands.sort(key=lambda c: (-c['rel'], -min(c['w'], c['h'])))
            if not cands:
                gaps.append({'page': s['page'], 'term': term, 'why': err or '无符合短边≥%dpx 的自由许可图' % a.min_edge})
                print("  页%-3d 缺素材: %s" % (s['page'], term))
                continue
            if cands[0]['rel'] < a.min_rel:
                gaps.append({'page': s['page'], 'term': term,
                             'why': '最高相关度仅 %d%%（阈值 %d%%），宁可不配图也不放离题的图' % (cands[0]['rel'], a.min_rel),
                             'best_title': cands[0].get('page')})
                print("  页%-3d 相关度不足 %d%%，放弃：%s" % (s['page'], cands[0]['rel'], (cands[0].get('page') or '')[:56]))
                continue
            # 同一个检索词每次都取第一个候选 = 一个词铺几十页同一张图，那是 v17
            # 「一张渐变铺 28 页」的翻版。按已铺次数跳过：同一 URL 到上限就换下一张候选。
            url_used = collections.Counter()
            for r in have.values():
                if r.get('url'):
                    url_used[r['url']] += 1
            # 一张拿不下就换下一张（实测 HTTP 424 Failed Dependency 会让整页素材判死，
            # 而同批候选里后面还有十几张同样过筛的图）：四道筛一条没放宽，只是不让一次
            # 下载/解码失败顺手毁掉这一页。
            tried = []
            got = None
            for cand in cands[:6]:
                if url_used[cand['url']] >= TH['material_reuse_max']:
                    continue
                # 先下 640px 缩样过美学/带字闸，过闸才下原图（实测全尺寸候选每页要 20 分钟）
                _pre = cand.get('precheck') or cand.get('rendition') or cand['url']
                _pr = os.path.join(wp['materials'], 'raw', 'pre%03d.jpg' % s['page'])
                try:
                    with http_get(_pre, proxy=proxy) as resp, open(_pr, 'wb') as f:
                        shutil.copyfileobj(resp, f)
                    _pq = photo_quality_reason(_pr, ' '.join([str(cand.get('title') or ''),
                                                              str(cand.get('page') or '')]), term_list)
                    _ps = (None if s.get('source') == 'stock' else
                           photo_style_reason(_pr, getattr(a, 'style', None) or 'tech_blue'))
                except Exception:
                    _pq = _ps = None
                _rm_quiet(_pr)
                if _pq or _ps:
                    tried.append(_pq or _ps)
                    continue
                ext = os.path.splitext(cand['url'])[1].split('?')[0].lower()
                if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
                    ext = '.jpg'
                raw = os.path.join(wp['materials'], 'raw', 'p%03d%s' % (s['page'], ext))
                os.makedirs(os.path.dirname(raw), exist_ok=True)
                # 先试缩样、缩样下不动再试原图直链：实测 Openverse 回的那个
                # api.openverse.org/.../thumb 转发口会 424，而同一个文件的 upload.wikimedia.org
                # 直链是好的——一次转发口故障不该把整页素材判死。
                urls = []
                for u in (cand.get('rendition'), cand['url']):
                    if u and u not in urls:
                        urls.append(u)
                dl_err = None
                for u in urls:
                    try:
                        # 优先取 3200 rendition（原图动辄 8–21MB，实测每页要多等 60–90 秒）；
                        # 许可与来源仍按 cand['url'] 举证，判"原图短边≥1920"用的是 API 回的原图 w/h
                        with http_get(u, proxy=proxy) as resp, open(raw, 'wb') as f:
                            shutil.copyfileobj(resp, f)
                        dl_err = None
                        break
                    except Exception as e:
                        dl_err = e
                if dl_err:
                    tried.append(u'下载失败 %s（试了 %d 个 URL，最后 %s）' % (
                        str(dl_err)[:50], len(urls), u[:100]))
                    _rm_quiet(raw)
                    continue
                dest = normalize_asset(raw, os.path.join(wp['materials'], 'p%03d.jpg' % s['page']),
                                       crop169=not a.keep_ratio)
                if not dest:
                    tried.append(u'原图无法解码/归一化')
                    _rm_quiet(raw)
                    continue
                if _safe.is_grayscale(dest):
                    # 黑白/低饱和：现代商业册里就是档案照，哪怕标题没写 Historic 也不放
                    tried.append(u'候选是黑白/消色图（平均彩度 <12 且几乎无饱和像素），换下一张')
                    _rm_quiet(raw)
                    _rm_quiet(dest)
                    continue
                _qr = photo_quality_reason(dest, ' '.join([str(cand.get('title') or ''),
                                                           str(cand.get('page') or '')]), term_list)
                if _qr:
                    tried.append(_qr)
                    _rm_quiet(raw)
                    _rm_quiet(dest)
                    continue
                _sr = (None if s.get('source') == 'stock' else
                   photo_style_reason(dest, getattr(a, 'style', None) or 'tech_blue'))
                if _sr:
                    tried.append(_sr)
                    _rm_quiet(raw)
                    _rm_quiet(dest)
                    continue
                got = (cand, dest, raw)
                break
            if not got:
                capped = sum(1 for x in cands[:12]
                             if url_used[x['url']] >= TH['material_reuse_max'])
                why = (u'前 %d 个候选都已铺到 %d 页上限：这一页宁可走矢量色场，也不再重复同一张图'
                       % (capped, TH['material_reuse_max'])
                       if capped == len(cands[:12]) and not tried
                       else u'；'.join(tried[:3]) or u'候选列表为空')
                gaps.append({'page': s['page'], 'term': term, 'why': why})
                print(u'  页%-3d 素材没拿下：%s' % (s['page'], why[:90]))
                continue
            c, dest, raw = got
            sd = media_dims(dest) or (None, None)
            used_term = c.get('term_used') or term
            rec = {'page': s['page'], 'term': used_term, 'file': dest, 'raw_file': raw, 'url': c['url'],
                   'source_page': c.get('page'), 'license': c.get('license'), 'artist': c.get('artist'),
                   'search_channel': c.get('channel') or source,
                   # w/h = 抓到的原图多大（SPEC §5 的短边≥1920 判据看这个）；
                   # stored_w/stored_h = 归一化/裁切后落在磁盘上的实际尺寸（成品内嵌分辨率由 G3b 量文件）
                   'w': c['w'], 'h': c['h'], 'relevance': c['rel'],
                   'stored_w': sd[0], 'stored_h': sd[1],
                   'bytes': os.path.getsize(dest), 'sha1': sha1(dest),
                   'fetched_at': now(), 'proxy_used': proxy if is_foreign(c['url']) else None}
            J.write(json.dumps(rec, ensure_ascii=False) + "\n")
            J.flush()
            fetched += 1
            have[s['page']] = rec      # 本次运行内也要记账：不然同一批 20 页会反复选中同一张候选
            for _s in shots['pages']:
                if _s['page'] == s['page']:
                    _s['asset_kind'] = 'image'
                    _s['need_material'] = True
            # 取到即落 gen：ack/build/G15 都认 gen/pNNN/image.png，只落 materials 会让回执永远不齐
            _gd = os.path.join(wp['gen'], 'p%03d' % s['page'])
            os.makedirs(_gd, exist_ok=True)
            shutil.copyfile(dest, os.path.join(_gd, 'image.png'))
            print("  页%-3d 取到 %dx%d %s [%s]" % (s['page'], c['w'], c['h'], os.path.basename(dest), c.get('license')))
            break   # 这个词拿下了就不再试后面的候选词
    for _s in todo:
        if _s['page'] not in have and _s.get('search_terms'):
            # 联网搜尽（美学闸/水印/相关度全拒）→ 兜底文生图卡，不降色场凑数（SPEC §24）
            _s['asset_kind'] = 'image'
            _s['need_material'] = True
    J.close()
    jw(wp['shots'], shots)   # 取到的页从色场改回 image，build 才会真铺这张图
    gp = os.path.join(wp['materials'], 'gaps.json')
    prev = jr(gp, [])
    # 只有这轮真拿检索词评估过的页才允许改写缺口说明；否则一次针对性重取会把别的页
    # 那条"相关度不足 40%"抹成"没有显式 --term"，把有用的诊断丢掉
    fresh = {x['page'] for x in gaps} & set(terms)
    keep = [g for g in prev if g['page'] not in fresh]
    known = {g['page'] for g in keep} | {r['page'] for r in have.values()}
    for n in no_term:
        if n not in known:
            keep.append({'page': n, 'term': None,
                         'why': u'没有显式 --term：检索词必须由人/agent 判断，不自动拿实体词兜底，'
                                u'否则会把不相干的图铺到页上'})
    jw(gp, keep + gaps)
    print(u"fetch 完成：本轮评估 %d 页，新取到 %d，这轮拦下 %d；"
          u"素材总覆盖 %d/%d 页（缺口明细见 materials/gaps.json，缺页将降级为矢量色场，不放假图）"
          % (len(terms), fetched, len(gaps), len(have), len(shots['pages'])))


def normalize_asset(src, dest, max_edge=3200, quality=88, crop169=False):
    """Downscale before embedding. These 172 source decks ballooned to 106 GB because every
    picture and video was pasted at native size; a 37 MB poster does the same to our output."""
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        im = Image.open(src)
        im = im.convert('RGB')
        if crop169:
            # a full-bleed 16:9 background must be cropped, not stretched: a portrait photo
            # pasted across 12192000x6858000 EMU turns faces into paste and lands off-canvas
            w0, h0 = im.size
            target = 16.0 / 9.0
            if w0 / float(h0) > target:
                nw = int(h0 * target)
                x0 = (w0 - nw) // 2
                im = im.crop((x0, 0, x0 + nw, h0))
            else:
                nh = int(w0 / target)
                y0 = max(0, int((h0 - nh) * 0.38))      # keep the upper-middle, where subjects sit
                im = im.crop((0, y0, w0, y0 + nh))
        w, h = im.size
        if max(w, h) > max_edge:
            sc = max_edge / float(max(w, h))
            im = im.resize((int(w * sc), int(h * sc)), Image.LANCZOS)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        im.save(dest, 'JPEG', quality=quality, optimize=True)
        return dest
    except Exception:
        return None


def blocks_of(docx_path):
    from docx import Document
    out = []
    d = Document(docx_path)
    for p in d.paragraphs:
        if p.text.strip():
            out.append(p.text.strip())
    for t in d.tables:
        for r in t.rows:
            for c in r.cells:
                if c.text.strip():
                    out.append(c.text.strip())
    return out


def die(msg):
    print("停止：" + msg)
    sys.exit(2)

# ---------------------------------------------------------------- S4 pack

IMG_SECTIONS = ['主体特征', '构图方式', '镜头角度', '光线', '色彩', '材质', '画面风格', '背景元素', '画面参数']
VID_SECTIONS = ['参考帧声明', '全局参数', '秒级分镜', '因果顺序', '运动约束', '定格收束']
GIF_SECTIONS = ['参考图声明', '全局参数', '循环声明', '秒级分镜', '运动约束', '背景与叠加']

# 已经用图形承载主数据的版式：这类页的其余事实进演讲者备注，不再叠第二行筹码/正文。
# keep 筹码路径与末尾事实安全网必须共用这一份——各写一份字面量就会一边放行一边拦，
# 真渲染页 6 实测同一页既有数字排又有两行筹码，每页字数 p90 顶到 46（语料 39.5）。
GRAPHIC_LAYOUTS = ('hero', 'numbers', 'bars', 'deltas')

# Single source of truth for every numeric acceptance line. SPEC §7's threshold column is checked
# against THIS table by check_docs.py (audit4 found the docs drifting: G10 documented as 命中=0 while
# the code scored <70, video documented 6/6 while code accepted 5/6, G3b message said <1280 while the
# code used 1080). Change a line here and the docs test tells you which sentence to rewrite.
TH = {
    'img_sections_min': 7,      # 9 段里至少 7 段
    'img_chars_min': 350,
    'vid_sections_min': 6,      # 生成器实测 6/6
    'vid_chars_min': 400,
    'gif_sections_min': 6,
    'gif_chars_min': 300,
    'video_dur_min': 5.0,       # 秒
    'video_dur_max': 10.0,
    'morph_ms_min': 1500,
    'morph_ms_max': 2500,
    'trans_dur_ms': 2500,       # 表格页棋盘/百叶转场时长（语料 PPT7×5神奇表格 实测值）
    'giant_num_sz_min': 10000,  # giant-numeral 卡的"大数字"下限：sz=10000 即 100pt（成品实测口径）
    'title_pt_min': 32,   # G27 整页视觉锚点下限（语料版式最大字中位 66–180，守下限）
    'body_pt_min': 16,    # G27 正文行下限（注释/筹码小字另允许 12pt 起）
    'ar_min': 1.45,             # 素材宽高比下限（16:9=1.778，1.45 放过裁切后的竖构图）
    'ar_max': 1.95,
    'frame0_mad_max': 28.0,     # G15：视频第 0 帧与首帧参考图的平均绝对差上限（0-255）
    # G15：亮度归一差上限（标准差单位）。即梦"起势"暗场与首帧亮图 raw MAD 34 但归一 0.44；
    # 错配 raw 39 归一 0.76。两条都超才判跑偏，单超 raw 不算（暗场不是跑偏）。
    'frame0_norm_max': 0.6,
    # G15：两页视频的首帧附件必须真是两张图（缩到 160x90 的 MAD 下限）。
    # 实测公共色场当首帧时 20 页两两差 0.07–1.7，各自专属静帧两两差 61–190。
    'attach_distinct_min': 12.0,
    # G15 循环接缝（教程 ae56a65b[34-53]）：末帧 vs 同一视频首帧，v10 真交付标定 0.1–0.7
    'loop_seam_mad_max': 2.0,    # 循环接缝末帧vs首帧差上限：超了＝循环播完回起点时跳帧
    'orphan_tail_max_chars': 2,
    'chroma_floor': 0.18,        # G16 配色：低于这个饱和度的算灰阶，白/黑/灰放行（语料主力色）
    'fullbleed_over_pic_min': 60.0,   # G17：声明满幅压字，实测至少六成文字框要落在图上（语料 99.2%）
    'split_over_pic_max': 25.0,       # G17：声明图文分栏，压在图上的文字框占比上限（语料 0.4%）  # G8 孤字：折行后末行只剩 ≤2 个字就判不合格
    'morph_pages_min': 2,
    'motion_ratio_min': 0.30,
    'screen_chars_p50_max': 19,   # SPEC §13：语料好示范页 p50=19 字，超出的解释行进备注
    'embed_short_edge': 1080,   # 整页照片/视频背景内嵌短边
    'fetch_short_edge': 1920,   # 抓到入库的原图短边

    'mp4_bytes_min': 64 * 1024,
    'contrast_body_min': 4.5,
    'contrast_ink_gap': 0.30,       # G4 剔字色时按亮度距离判定（太小会把抗锯齿边当背景）      # G4 正文对比度下限（WCAG AA）；≥28pt 大字用 3.0
    'gen_image_bytes_min': 15000,   # G26：gen/ 里的图小于这个字节数当占位文件，不算产物

    'gif_frames_min': 10,
    'gif_short_edge': 640,
    'ai_score_min': 70,         # G10：ai_tells 得分低于此判不合格
    'relevance_min': 50,
    'material_pages_min_pct': 50.0,  # G20 真素材打底页占全册下限（语料好示范满幅压字 58.3%）
    'field_run_max': 2,              # G20 同一套矢量色场最多连排几页
    'material_reuse_max': 3,         # G20 同一张真素材最多铺几页（v17 同一张渐变铺了 28 页）
    'page_chars_display_total': 180,   # 分页时的单页正文预算（提炼册上屏前还要再压到 64 字）
    'page_chars_max': 200,           # S1 分页：单页正文上限（G19 判文字墙是 300，留 100 字余量）
    # 这一档是 2026-11-07 按语料现测收紧的：随机 6 套 / 40 页里"单行字数 p50=4、p90=12"，
    # "整页字数 p50=29、p75=69"。旧的 20 字 × 3 行 / 90 字把 v5 顶到 p50=43 字，G22 最重的
    # "每页字数 p50"那一维只拿到 0.442（维下限 0.5），整册 83.7 < 85。
    # 收紧的是显示契约：装不下的事实靠分页（page_facts_max）走，不许靠加长行，也不许丢。
    'display_line_max_chars': 14,    # G23 单条显示行上限（= 语料单行 p90 量级）
    'display_lines_max': 2,          # G23 每页显示行数上限
    'page_chars_max_display': 64,    # G23 提炼模式下非表格页整页文字上限（语料整页 p50=29、p75=69）
    # G23 数据/卡片页单独定标：实测语料 105 张条形/卡片页 p50=68、p75=86、p90=209，
    # 用 64 会把语料自己的好页判掉 69.5%；取语料整页 p90=112 作上限（仍严于数据页 p90）。
    'page_chars_max_data': 112,
    'page_chars_max_quote': 140,     # G23 引文/陈述页放宽到这里，再高就是文字墙
    'page_chars_max_table': 400,     # G23 台账页上限（表格另有 G13 逐格与 G25 样式核）
    'page_facts_max': 3,             # 一页最多扛几个数字（≤2 行 × ≤14 字的容量）
    'fact_missing_max': 0,           # G24 源文档事实单元（数字/金额/比例/专名）在成品里的缺失上限
    'parity_score_min': 78,          # G22 同级分及格线（好示范册全量 54 套同尺实测中位 82.8，78 低于它；100 才是完全同等级，kb/parity_baseline.json）
    'parity_dim_min': 0.5,
    'parity_min_pages': 10,      # G22 同级分的样本量下限：语料中位 47 页，几页的样册量不出「每页字数中位数」这类分布指标           # G22 任何一维的达标率下限，防止「总分够但一维全丢」
    'table_body_pt_min': 12,         # G25 表格正文字号下限（语料实测正文 16pt）
    # 页数契约里的表格配额：技能要跨体裁，配额只能按这份文档自己的密度定。
    'table_quota_narrative': 3,      # 叙事为主的文档（表只是佐证）最多留几张原生表
    'table_quota_dense_share': 0.25,  # 表页占全册比例到这条线就按"表格即内容"，全部保留

    'derived_contain_min': 80.0,     # G21 派生源检测：源正文整段被某个旧 PPT 命中的比例下限（%）
    'derived_size_min': 1.2,         # G21 那个旧 PPT 的文字量至少是源的几倍才判派生
}


def score_image_prompt(txt):
    got = [s for s in IMG_SECTIONS if s in txt]
    flags = {
        '画幅': bool(re.search(r'16:9|横版', txt)),
        '留白占位': bool(re.search(r'保持.{0,6}空白|不放任何文字|预留|留白', txt)),
        '禁文字': bool(re.search(r'无文字|不要出现任何文字|文字、数字、字母', txt)),
        '禁水印': bool(re.search(r'水印', txt)),
        '色彩闭集': bool(re.search(r'严格控制在|为主色|避免.{0,8}颜色|主色调', txt)),
        '画质锚': bool(re.search(r'4K|8K|超清|HDR|电影级', txt)),
        '附件点名': bool(re.search(r'附件|参考图|上传', txt)),
    }
    return got, flags


def score_video_prompt(txt):
    got = []
    if re.search(r'附件|作为首帧|尾帧|结尾帧|参考图', txt):
        got.append('参考帧声明')
    if re.search(r'\d+\s*[-–]?\s*\d*\s*秒', txt) and re.search(r'16:9|横版|横屏', txt):
        got.append('全局参数')
    if re.search(r'\d+\s*[-–—－~至]\s*\d*(?:\.\d+)?\s*秒', txt):
        got.append('秒级分镜')
    if re.search(r'必须(先|严格|按照)|之后才|之前不能|依次|不得同时|顺序', txt):
        got.append('因果顺序')
    if re.search(r'镜头(保持|固定|缓慢|逐渐)|不推拉|不旋转|平滑|不要剧烈', txt):
        got.append('运动约束')
    if re.search(r'定格|稳定停留|不再增加|匹配.{0,12}参考', txt):
        got.append('定格收束')
    flags = {
        '禁文字': bool(re.search(r'禁止出现任何文字|不要出现任何文字|无文字|文字、数字、字母', txt)),
        '禁水印': bool(re.search(r'水印', txt)),
        '留白占位': bool(re.search(u'留白|预留|空白区域|保持.{0,12}(干净|空白|完整，不放)', txt)),
        '附件点名': bool(re.search(r'附件\s*\d|参考图\s*\d|首帧|尾帧', txt)),
        # 教程 ae56a65b[15-20] 首尾帧模式：承诺了尾帧就必须同时点名两张附件。
        # 两步卡（先文生图再图生视频）只有附件1、不提尾帧，属合法，不误伤。
        '首尾帧双附件': (not re.search(u'尾帧', txt)) or
                       (bool(re.search(u'附件\\s*1', txt)) and bool(re.search(u'附件\\s*2', txt))),
    }
    return got, flags


def score_gif_prompt(txt):
    """动图提示词的语法判据：视频那套之外，必须写清"无缝循环"和"底怎么叠"。

    语料里 162 个 GIF 全是多帧元素动效（水墨晕开/闪光划过/旋转扇子），它们叠在背景上而不是
    铺满整页；不写循环与底色的 GIF 放进 PPT 就是"播一次就停"或带一块黑边。
    """
    got = []
    if re.search(u'附件|参考图|首帧|以.{0,6}为底图', txt):
        got.append('参考图声明')
    if re.search(u'(?:\\d+\\s*秒|\\d+\\s*帧|\\d+\\s*fps|帧率)', txt) and re.search(u'16:9|横版|尺寸|像素|透明', txt):
        got.append('全局参数')
    if re.search(u'循环|无缝|首尾一致|loop', txt, re.I):
        got.append('循环声明')
    if re.search(r'\d+\s*[-–—－~至]\s*\d*(?:\.\d+)?\s*(?:秒|帧)', txt):
        got.append('秒级分镜')
    if re.search(u'不缩放|不变形|镜头固定|位置不变|只做|仅', txt):
        got.append('运动约束')
    if re.search(u'透明|纯色底|纯黑底|alpha|叠加|背景', txt, re.I):
        got.append('背景与叠加')
    flags = {
        '禁文字': bool(re.search(u'无文字|禁止出现任何文字|不要出现任何文字|不放任何文字', txt)),
        '声明循环': bool(re.search(u'无缝循环|循环播放|首尾帧一致|loop', txt, re.I)),
        '说明底色': bool(re.search(u'透明背景|纯色背景|纯黑背景|alpha 通道|底色为', txt)),
    }
    return got, flags


def prompt_files_for(pp, n):
    files = {}
    for kind in ('image', 'first', 'last', 'video', 'gif', 'negative'):
        fp = os.path.join(pp, 'p%03d_%s.txt' % (n, kind))
        if os.path.exists(fp):
            files[kind] = fp
    return files


def score_prompt_file(kind, txt):
    """One prompt file -> its problems. pack and check call THIS, so a hand-edited prompt can't
    slip past G2 by leaving a stale pack.json behind (audit4 [中]2)."""
    problems = []
    if kind in ('image', 'first'):
        got, flags = score_image_prompt(txt)
        if len(got) < TH['img_sections_min']:
            problems.append("图片提示词缺段：有 %s，需≥%d/%d 段" % (
                got, TH['img_sections_min'], len(IMG_SECTIONS)))
        if len(txt) < TH['img_chars_min']:
            problems.append("图片提示词 %d 字，语料实测均值 809 字，低于 %d 判描述不足" % (
                len(txt), TH['img_chars_min']))
        miss = [k for k, v in flags.items() if not v]
        if '留白占位' in miss:
            problems.append("未声明留白占位——生成的图会自己长字，PPT 就没法编辑")
        if '禁文字' in miss or '禁水印' in miss:
            problems.append("未写文字/水印禁令")
    elif kind == 'video':
        got, flags = score_video_prompt(txt)
        if len(got) < TH['vid_sections_min']:
            problems.append("视频提示词缺段：有 %s，需≥%d/%d 段" % (
                got, TH['vid_sections_min'], len(VID_SECTIONS)))
        if len(txt) < TH['vid_chars_min']:
            problems.append("视频提示词 %d 字，语料实测均值 1011 字，低于 %d 判描述不足" % (
                len(txt), TH['vid_chars_min']))
        if not flags['附件点名']:
            problems.append("视频提示词没点名附件（首帧/尾帧），图生视频会跑偏")
        if not flags['首尾帧双附件']:
            problems.append(u"提示词承诺了尾帧/首尾帧模式，却没同时点名附件1与附件2——"
                            u"上传时只有一张图，模型会自己编尾帧（教程教的是两张图定死首尾）")
        if not flags['禁文字']:
            problems.append("视频提示词未禁文字，成片会烧死字幕，后期改不了")
        if not flags['留白占位']:
            problems.append("视频提示词未声明留白/空白占位区，动态画面会在文字区里长出自带元素")
    elif kind == 'gif':
        got, flags = score_gif_prompt(txt)
        if len(got) < TH['gif_sections_min']:
            problems.append("动图提示词缺段：有 %s，需≥%d/%d 段" % (
                got, TH['gif_sections_min'], len(GIF_SECTIONS)))
        if len(txt) < TH['gif_chars_min']:
            problems.append("动图提示词 %d 字，描述不足以约束一个循环元素" % len(txt))
        if not flags['声明循环']:
            problems.append("动图提示词没写无缝循环，放进 PPT 会播一次就停或跳帧")
        if not flags['说明底色']:
            problems.append("动图提示词没写透明/纯色底，叠加到背景上会带一块方框")
        if not flags['禁文字']:
            problems.append("动图提示词未禁文字")
    return problems


def validate_prompts(wp, shots, outline):
    """Score every prompt file on disk. cmd_pack writes the result to pack.json; cmd_check
    recomputes it from the files themselves (never trusts pack.json)."""
    pp = wp['promptpack']
    bad, ok, cards = [], 0, []
    all_recs = []
    for s in shots['pages']:
        n = s['page']
        pg = outline['pages'][n - 1] if 0 < n <= len(outline['pages']) else {'headline': ''}
        files = prompt_files_for(pp, n)
        rec = {'page': n, 'role': s['role'], 'headline': pg.get('headline', ''),
               'files': files, 'problems': []}
        for kind in ('image', 'first', 'video', 'gif'):
            if kind in files:
                txt = io.open(files[kind], encoding='utf-8').read()
                problems = score_prompt_file(kind, txt)
                for pr in problems:
                    rec['problems'].append(pr if kind == 'image' else '[%s] %s' % (kind, pr))
        # 矢量色场页不需要外部素材（scaffold 不给它写提示词，G11 也豁免它），
        # 所以 pack 不许把"没提示词"记成不合格——三处口径必须一致
        need = s.get('asset_kind') not in (None, 'none', 'field') and s.get('need_material', True)

        if need and not files:
            rec['problems'].append("该页要求出图/出视频，但 promptpack 里没有任何提示词文件——不能空着当合格")
        if rec['problems']:
            bad.append(rec)
        else:
            ok += 1
        all_recs.append(rec)
        if files:
            cards.append(rec)
    # 主体句必须"直接写画面里是什么"。语料原话就是直陈式（"画面中央是一支巨大的向上指引箭头，
    # 由明亮的青蓝色半透明能量光束构成…象征突破、增长"），而旧模板写的是
    # "设置与「章节名」直接相关的核心视觉主体"——这种间接话模型只能猜，实测 20 个过渡页
    # 出同一类抽象芯片图，用户看到的就是"同一段提示词反复发"。两条都做成硬判据：
    # ① 间接句式出现即拦（模板回退就报错）；② 两页主体句逐字相同即拦（两页必然同一张图）。
    _indirect = re.compile(u'与「[^」]{1,24}」直接相关的核心视觉主体')
    # 主题/象征句必须是完整短语（v11 实测：自动句跨轮复用后「主题为「七」」「象征…口径会把投」
    # 这种半句话上过卡，用户看到的就是"一眼坏掉的提示词"）。尺子与 scaffold 同一份，防口径漂移。
    from styles import clean_phrase as _cp, phrase_ok as _po
    _by_sent = {}
    for rec in cards:
        f = rec['files'].get('image')
        if not f or not os.path.exists(f):
            continue
        _ptxt = io.open(f, encoding='utf-8').read()
        m = re.search(u'主体特征：([^\\n]+)', _ptxt)
        if not m:
            continue
        sent = m.group(1).strip()
        if _indirect.search(sent):
            rec['problems'].append(
                u'主体特征是"与「…」直接相关的核心视觉主体"这种间接话——模型只能自己猜，'
                u'20 页就会出同一类抽象图。照语料那样直接写这一页画面上是什么东西')
        mt = re.search(u'主题为「([^」]*)」', _ptxt)
        if mt and not _po(_cp(mt.group(1), 14)):
            rec['problems'].append(
                u'主题「%s」不是完整短语（半句话/单字/纯数字）——重新 scaffold，'
                u'或手工换成一个能当主题名的完整短语；模型照着半句话只能瞎猜' % mt.group(1)[:16])
        msym = re.search(u'象征(.+?)。', sent)
        if msym and not _po(_cp(msym.group(1), 16)):
            rec['problems'].append(
                u'象征句「%s…」是半句话——要么换成本页上屏行/正文里的完整短语，'
                u'要么整句删掉，不许把截断的句子塞给模型' % msym.group(1)[:20])
        _by_sent.setdefault(sent, []).append(rec['page'])
    for sent, pgs in _by_sent.items():
        if len(pgs) < 2:
            continue
        for rec in cards:
            if rec['page'] in pgs:
                rec['problems'].append(
                    u'主体特征与第 %s 页逐字相同（"%s…"）——两页必然出同一张图' % (
                        u'、'.join(str(p) for p in pgs if p != rec['page']), sent[:24]))
    bad = [r for r in all_recs if r['problems']]
    _badp = {r['page'] for r in bad}
    ok = sum(1 for s in shots['pages'] if s['page'] not in _badp)
    return ok, bad, cards


def cmd_refine(a):
    wp = work_paths(a.work)
    outline = jr(wp['outline']) or die("先跑 plan")
    shots = jr(wp['shots']) or die("先跑 plan")
    n_new = 0
    for s in shots['pages']:
        pg = outline['pages'][s['page'] - 1]
        if s.get('display_lines') and not a.force:
            continue
        lines, ctype, needs = DR.propose_lines(pg)
        s['content_type'] = ctype
        s['key_numbers'] = DR.key_numbers(' '.join(pg.get('body') or []), 3)
        s['display_lines'] = lines
        s['display_is_proposal'] = True
        s['display_needs_edit'] = needs
        n_new += 1
    jw(wp['shots'], shots)
    brief = outline.get('brief') or {}
    if brief.get('pages'):
        # 页数契约下的压缩台账（SPEC §21）：上屏的是提炼句，原文逐句留账，
        # G13/G24 据此区分"被压缩留档"与"静默丢内容"。
        led = []
        for s in shots['pages']:
            pg = outline['pages'][s['page'] - 1]
            src = pg.get('source_span') or pg.get('body') or []
            kept = s.get('display_lines') or []
            led.append({'page': s['page'], 'source': src, 'kept': kept})
        brief['ledger'] = led
        outline['brief'] = brief
        jw(wp['outline'], outline)
    todo = [s['page'] for s in shots['pages']
            if s['role'] != 'table' and not (s.get('display_lines') or [])]
    print("refine：写了 %d 页提案；仍空 %d 页 %s" % (n_new, len(todo), todo[:12]))
    print(u"  提案只是起点：请按 SPEC §12 逐页改写 display_lines（≤%d 条、每条 ≤%d 字），"
          u"改完把 display_is_proposal 置 false"
          % (TH['display_lines_max'], TH['display_line_max_chars']))


def cmd_waive(a):
    """单页视频豁免（带角标清不净 / 规格不够而用户选静态兜底）：留证 + 记决定 + 队列跳过。

    必须由用户原话撑着（--note），agent 不能替用户放弃；豁免后 build 给该页静态底图加
    12s/6% 缓慢推镜（asset_kind=video_bg 且无视频的那条兜底），报告按降级交付如实标。"""
    wp = work_paths(a.work)
    if not (a.note or '').strip():
        die(u"waive 必须带 --note 用户原话：「这页不要视频/用静态图」必须由用户说，"
            u"agent 不能替用户放弃")
    kind = a.kind or 'video'
    if kind != 'video':
        die(u"目前只豁免 video：图片缺了可以 fetch 补或降矢量色场，不需要豁免")
    ol = jr(wp['outline'], {}) or {}
    keep = os.path.join(project_assets_dir(ol.get('docx') or '', create=False),
                        'AI产物', 'p%03d' % a.page)
    gd = os.path.join(wp['gen'], 'p%03d' % a.page)
    evidence = ''
    if os.path.isdir(gd):
        for f in sorted(os.listdir(gd)):
            if f.startswith('video') and os.path.splitext(f)[1].lower() in ('.mp4', '.mov', '.m4v'):
                os.makedirs(keep, exist_ok=True)
                dst = os.path.join(keep, 'video_waived_original' + os.path.splitext(f)[1].lower())
                shutil.move(os.path.join(gd, f), dst)
                evidence = dst
    p_ = os.path.join(wp['work'], 'video_waiver.json')
    rows = jr(p_, []) or []
    if isinstance(rows, dict):
        rows = [rows]
    rows = [r for r in rows if not (r.get('page') == a.page and (r.get('kind') or 'video') == kind)]
    rows.append({'page': a.page, 'kind': kind, 'decision': 'static_image_plus_push_in',
                 'user_words': a.note.strip(), 'decided_at': now(), 'evidence': evidence})
    jw(p_, rows)
    print(u"页%d 的 %s 已豁免（用户原话：%s）" % (a.page, kind, a.note.strip()))
    if evidence:
        print(u"  原片留证：%s" % evidence)
    print(u"  build 会给这页静态底图加 12s/6%% 缓慢推镜；出片回执跑："
          u"ack --work %s --no-video --note \"%s\"" % (a.work, a.note.strip()))


def cmd_ack(a):
    wp = work_paths(a.work)
    mode = ('ready' if a.ready else
            DG.VIDEO_ONLY_WAIVER if a.no_video else
            DG.DECLINED if a.declined else None)
    if not mode:
        die("ack 必须带 --ready / --no-video / --declined 之一："
            "--ready=用户回「已就绪」且文件已放回；--no-video=用户明确不要视频背景（只豁免 video 类）；"
            "--declined=用户回「这些我不做了，按现状出片」（要 --note 用户原话）")
    ack, err = DG.record_ack(wp['promptpack'], mode, a.note, TH)
    if err:
        print("回执登记失败：%s" % err)
        print("（回执不是签字章：卡片没发给用户、文件不在、卡片改过，任何一种都不许过）")
        sys.exit(1)
    print("已登记投放回执：%s（%d 个产物已核对 SHA1）-> %s" % (
        ack['answer'], len(ack['gen_files']), os.path.join(wp['promptpack'], DG.ACK_NAME)))
    print("  发卡时间 %s ｜ 回执时间 %s ｜ 卡片 %s" % (
        ack['request_made_at'], ack['ack_at'], (ack['card_sha1'] or '')[:12]))
    print("现在可以跑 build + check")


def sync_card_attachments(work, wp):
    """把视频/动图卡开头的"首帧附件"那一小句改成这一页**现在**该上传的那张（口径同 card_first_frame）。

    为什么在 pack 里做而不是手工改文件：上一轮是手改 promptpack，改一次漂一次——实测 20 个
    过渡页的卡集体指到 out/bg_fallback/pNNN.jpg（同一张深蓝渐变），用户照卡上传就是 20 页同一个
    背景；而 G15 又拿这张被顶平的尺子量，谁都发现不了。pack 里统一重写一次，卡与闸读同一份文本。
    """
    changed = 0
    marker = u'画面唯一的构图'
    for k in ('video',):
        for f in glob.glob(os.path.join(wp['promptpack'], 'p*_%s.txt' % k)):
            try:
                n = int(re.search(r'p(\d{3})_', os.path.basename(f)).group(1))
            except Exception:
                continue
            txt = io.open(f, encoding='utf-8').read()
            i = txt.find(marker)
            if i <= 0:
                continue
            att = card_first_frame(work, n)
            head = (u'以附件1（%s）作为首帧，' % att) if att else u'以本页文生图结果（附件1）作为首帧，'
            new = head + txt[i:]
            if new != txt:
                io.open(f, 'w', encoding='utf-8').write(new)
                changed += 1
    return changed


def cmd_pack(a):
    wp = work_paths(a.work)
    shots = jr(wp['shots']) or die("先跑 plan")
    outline = jr(wp['outline'])
    man = {}
    mp = os.path.join(wp['materials'], 'manifest.jsonl')
    if os.path.exists(mp):
        for ln in io.open(mp, encoding='utf-8'):
            try:
                r = json.loads(ln)
                man[r['page']] = r
            except Exception:
                pass
    _att_fixed = sync_card_attachments(a.work, wp)
    if _att_fixed:
        print(u"首帧附件已按现状重指 %d 张卡（口径=card_first_frame：这一页专属静帧，"
              u"公共色场不算；没有专属静帧的页改成先文生图）" % _att_fixed)
    ok, bad, cards = validate_prompts(wp, shots, outline)
    jw(os.path.join(wp['promptpack'], 'pack.json'), {'ok': ok, 'bad': bad, 'cards': cards,
                                                     'th': TH,
                                                     'grammar': {'image_sections': IMG_SECTIONS,
                                                                 'video_sections': VID_SECTIONS,
                                                                 'gif_sections': GIF_SECTIONS}})
    card_p = os.path.join(wp['promptpack'], 'delivery_card.md')
    write_delivery_cards(cards, man, shots, card_p, outline)
    # G26 第一步：卡片全文进 stdout。以前只打印一个文件路径，agent 完全可以（并且实测就是）
    # 不打开那个文件就继续 build，用户一整轮都看不到提示词。全文打出来，跑了 pack 就一定看过卡。
    req = DG.make_request(wp['promptpack'], cards, wp['gen'], TH)
    if os.path.exists(card_p):
        print("\n" + "=" * 76)
        print("投放卡全文（必须原样转达给用户，不要只说『已生成卡片文件』）")
        print("=" * 76)
        sys.stdout.write(io.open(card_p, encoding='utf-8').read())
        print("=" * 76)
    print("pack：合格 %d 页，不合格 %d 页" % (ok, len(bad)))
    if bad:
        # 只要有一页要素材却没写合格的提示词，这一页就还没做完；以前要等全册都烂才退出码非 0，
        # 于是"9 页合格 1 页空着"也能当成功往下走
        print("不合格页：")
        for b in bad[:12]:
            print("  页%-3d %s" % (b['page'], '；'.join(b['problems'])))
        print("停止：pack 有 %d 页不合格（合格 %d 页），补齐后再 build" % (len(bad), ok))
        sys.exit(1)
    print("  投放卡 -> %s" % card_p)
    print("  投放请求单 -> %s（%d 项产物待用户回收，缺 %d 项）" % (
        os.path.join(wp['promptpack'], DG.REQ_NAME), req['n_items'], req['n_missing']))
    print("  下一步（不可跳过）：把上面卡片全文发给用户，问一句「%s」；"
          "用户回『是』→ ack --work <目录> --ready；回『不要视频背景』→ ack --no-video；"
          "ack 之前 build 会拒绝出片。" % DG.ASK)



def write_delivery_cards(cards, man, shots, out, outline=None):
    import delivery
    pages = (outline or {}).get('pages') or []
    links = {}
    for c in cards:
        sh = next((x for x in shots['pages'] if x['page'] == c['page']), {})
        pg = pages[c['page'] - 1] if 0 < c['page'] <= len(pages) else {}
        u = unsplash_links(pg, sh, (man.get(c['page']) or {}).get('term'))
        if u:
            links[c['page']] = u
    delivery.write(cards, man, shots, out, links)



# ---------------------------------------------------------------- S6 build


def find_gen_all(w, page, kind):
    """这一页放得下的所有同类产物（多图栅格页用：image_1.jpg / image2.png / image.jpg）。"""
    d = os.path.join(w, 'gen', 'p%03d' % page)
    if not os.path.isdir(d):
        return []
    EXT_BY_KIND = {'video': ('.mp4', '.mov', '.m4v'), 'gif': ('.gif', '.apng', '.webp')}
    exts = EXT_BY_KIND.get(kind, ('.png', '.jpg', '.jpeg', '.webp'))
    out = []
    for f in sorted(os.listdir(d)):
        base, ext = os.path.splitext(f)
        if ext.lower() in exts and re.match(r'^%s(\d+|_[0-9]+)?$' % kind, base):
            out.append(os.path.join(d, f))
    return out


def tile_boxes(layout, n):
    """按语料实测的图块尺寸排栅格：照片墙 0.097×0.126、卡片 0.225×0.321、时间轴节点 0.049×0.087。

    排出来的框必须让 deck_measure 的分类器认成同一个原型（G17 就是这么核对的），
    所以照片墙 >=6 块、卡片栅格 >=3 块且尺寸一致、时间轴节点中心共线。
    """
    if n <= 0:
        return []
    if layout == 'photo-wall':
        cols = 3 if n <= 9 else 4
        tw, th, gx, gy, x0, y0 = 0.140, 0.185, 0.018, 0.022, 0.520, 0.150
    elif layout == 'card-grid':
        cols = 3 if n <= 6 else 4
        tw, th, gx, gy, x0, y0 = 0.225, 0.321, 0.030, 0.040, 0.520, 0.160
    else:  # timeline-on-line：节点沿一条左下→右上的线（语料 NO.155 的尾迹排法）
        cols, tw, th = n, 0.075, 0.120
        x0, y0, x1, y1 = 0.100, 0.620, 0.860, 0.300
        step = (x1 - x0) / float(max(1, n - 1)) if n > 1 else 0.0
        stepy = (y1 - y0) / float(max(1, n - 1)) if n > 1 else 0.0
        return [[round(x0 + i * step, 4), round(y0 + i * stepy, 4), tw, th] for i in range(n)]
    rows = int(math.ceil(n / float(cols)))
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        out.append([round(x0 + c * (tw + gx), 4), round(y0 + r * (th + gy), 4), tw, th])
    return out


def add_fullbleed_picture(slide, img_path):
    from pptx.util import Emu
    pic = slide.shapes.add_picture(img_path, 0, 0, width=Emu(EMU_W), height=Emu(EMU_H))
    return pic


def _hex_rgb(h):
    h = (h or 'FFFFFF').lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def scrim_alpha_for(img_path, zone, ink_hex, need=None):
    """按"文字落点那块区域的实测亮度"反推蒙版要多黑，而不是固定 62%%。

    语料那 7864 次 lumMod 不是装饰：背景亮度决定字号能不能读。这里直接解
    (L_text+0.05)/((L_bg*(1-a))+0.05) >= need 里的 a，取 55%%–90%% 之间。"""
    need = need or TH['contrast_body_min']
    if not img_path or not os.path.exists(str(img_path)):
        return 62
    try:
        from PIL import Image
        im = Image.open(str(img_path)).convert('RGB')
        w, h = im.size
        zx, zy, zw, zh = zone or [0.06, 0.16, 0.88, 0.70]
        x0 = max(0, int(zx * w)); y0 = max(0, int(zy * h))
        x1 = min(w, int((zx + zw) * w)); y1 = min(h, int((zy + zh) * h))
        if x1 - x0 < 8 or y1 - y0 < 8:
            return 62
        px = list(im.crop((x0, y0, x1, y1)).resize((48, 27)).getdata())
    except Exception:
        return 62
    if not px:
        return 62
    lum = sum(rel_lum(p[:3]) for p in px) / float(len(px))
    tl = rel_lum(_hex_rgb(ink_hex))
    target = max(0.002, (tl + 0.05) / float(need) - 0.05)
    if lum <= target:
        return 55
    a = 1.0 - target / (lum or 1.0)
    return int(max(0.55, min(0.90, a)) * 100)


def add_scrim(slide, alpha_pct=62, top_dark=True, both_ends=False):
    """Gradient dim layer over the artwork so type survives — the corpus does this with lumMod 7864 times."""
    from pptx.util import Emu
    from lxml import etree
    shp = slide.shapes.add_shape(1, 0, 0, Emu(EMU_W), Emu(EMU_H))  # rectangle
    spPr = shp._element.spPr
    for child in list(spPr):
        if child.tag in ('{%s}solidFill' % A, '{%s}gradFill' % A, '{%s}noFill' % A,
                         '{%s}effectLst' % A, '{%s}scene3d' % A, '{%s}sp3d' % A):
            spPr.remove(child)
    # CT_SpPr 是有序序列（xfrm → 几何 → fill → ln → effectLst → scene3d → sp3d），PowerPoint 对
    # 顺序不符的文件会弹"需要修复"。先拿 ln（python-pptx 会按序插入），再把渐变填色摆到 ln 之前。
    g = spPr.makeelement('{%s}gradFill' % A, {})
    gsLst = etree.SubElement(g, '{%s}gsLst' % A)
    if both_ends:
        # 满幅压字页：标题+正文在上沿暗带、数字排在下沿暗带，中段留给照片本身。
        # 上带要"平"一点（30% 处只减 10），否则正文落在渐变坡上只有 3:1（G4 实测 2.56）。
        stops = ((0, alpha_pct), (30000, max(20, alpha_pct - 10)),
                 (60000, max(8, alpha_pct - 45)), (100000, alpha_pct))
    elif top_dark:
        stops = ((0, alpha_pct), (55000, max(8, alpha_pct - 30)), (100000, 0))
    else:
        stops = ((0, 0), (45000, max(8, alpha_pct - 30)), (100000, alpha_pct))
    for pos, alp in stops:
        gs = etree.SubElement(gsLst, '{%s}gs' % A)
        gs.set('pos', str(pos))
        clr = etree.SubElement(gs, '{%s}srgbClr' % A)
        clr.set('val', '000000')
        etree.SubElement(clr, '{%s}alpha' % A).set('val', str(int(alp * 1000)))
    lin = etree.SubElement(g, '{%s}lin' % A)
    lin.set('ang', '5400000')
    lin.set('scaled', '0')
    ln = shp.line._get_or_add_ln()
    etree.SubElement(ln, '{%s}noFill' % A)
    ln.addprevious(g)
    shp.name = 'scrim'
    return shp


def add_text(slide, text, zone, size_pt, bold=False, color='FFFFFF', align='l', name=None,
             weight='normal'):
    from pptx.util import Emu, Pt
    from pptx.enum.text import PP_ALIGN
    # zone is 0-1 canvas fractions. Guard against EMU being passed in by mistake, which used to
    # push whole pages (the CC-BY attribution sheet) millions of times off-canvas.
    if any(abs(v) > 1.5 for v in zone):
        raise ValueError("add_text 的 zone 必须是 0-1 分数，收到 %s（EMU 会被再乘一次）" % (zone,))
    x, y, w, h = [int(v * u) for v, u in zip(zone, (EMU_W, EMU_H, EMU_W, EMU_H))]
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    lines = text if isinstance(text, (list, tuple)) else [text]
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = {'l': PP_ALIGN.LEFT, 'c': PP_ALIGN.CENTER, 'r': PP_ALIGN.RIGHT}[align]
        r = p.add_run()
        r.text = ln
        f = r.font
        f.size = Pt(size_pt)
        f.bold = bold
        f.name = '微软雅黑'
        f.color.rgb = __import__('pptx.dml.color', fromlist=['RGBColor']).RGBColor.from_string(color)
    tb.name = name or ('text_%s' % (lines[0][:8] if lines else 'x'))
    return tb


def attach_video(slide, mp4_path, poster_path, shape_name='video_bg', autoplay=True, dur_ms=8000):
    """Embed a real playing video, byte-for-byte following the structure found in the corpus:
    two relationships to the media (Type .../video and .../2007/relationships/media),
    a poster blip, a:videoFile r:link, p14:media r:embed, and a timing tree that calls playFrom(0.0).
    """
    from pptx.util import Emu
    from pptx.opc.packuri import PackURI
    from pptx.opc.package import Part
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from lxml import etree

    part = slide.part
    pkg = part.package
    idx = len([p for p in pkg.iter_parts() if p.partname.startswith('/ppt/media/media')]) + 1
    name = PackURI('/ppt/media/media%d.mp4' % idx)
    ctype = 'video/mp4' if mp4_path.lower().endswith(('.mp4', '.m4v')) else 'video/mpeg'
    media_part = Part(name, ctype, package=pkg, blob=open(mp4_path, 'rb').read())
    rid_media = part.relate_to(media_part, RT.MEDIA)
    rid_video = part.relate_to(media_part, RT.VIDEO)

    img_part, rid_poster = part.get_or_add_image_part(poster_path)

    spid = 4000 + (idx * 7) % 900
    pic_xml = (
        '<p:pic xmlns:p="%s" xmlns:a="%s" xmlns:r="%s">'
        '<p:nvPicPr><p:cNvPr id="%d" name="%s">'
        '<a:hlinkClick r:id="" action="ppaction://media"/></p:cNvPr>'
        '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr>'
        '<p:nvPr><a:videoFile r:link="%s"/>'
        '<p:extLst><p:ext uri="{DAA4B4D4-6D71-4841-9C94-3DE7FCFB9230}">'
        '<p14:media xmlns:p14="%s" r:embed="%s"/></p:ext></p:extLst></p:nvPr></p:nvPicPr>'
        '<p:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
        '<p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
    ) % (P, A, R, spid, shape_name, rid_video, P14, rid_media, rid_poster, EMU_W, EMU_H)
    pic_el = etree.fromstring(pic_xml)
    slide.shapes._spTree.append(pic_el)
    if autoplay:
        inject_autoplay_timing(slide, spid, dur_ms)
        inject_video_click(slide, spid)   # 语料每个视频还带 onClick->togglePause
    return spid


def inject_autoplay_timing(slide, spid, dur_ms=8000):
    """Mirror the timing tree the corpus decks use: tmRoot -> mainSeq -> par(onBegin) ->
    mediacall playFrom(0.0), plus the p:video/cMediaNode that carries volume. Written out
    level by level so the tags actually balance."""
    from lxml import etree
    spid = str(spid)
    xml = (
        '<p:timing xmlns:p="%(P)s" xmlns:a="%(A)s" xmlns:r="%(R)s">'
        '<p:tnLst>'
        '<p:par>'
        '<p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot">'
        '<p:childTnLst>'
        '<p:seq concurrent="1" nextAc="seek">'
        '<p:cTn id="2" dur="indefinite" nodeType="mainSeq">'
        '<p:childTnLst>'
        '<p:par><p:cTn id="3" fill="hold">'
        '<p:stCondLst><p:cond delay="indefinite"/>'
        '<p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond></p:stCondLst>'
        '<p:childTnLst>'
        '<p:par><p:cTn id="4" fill="hold">'
        '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
        '<p:childTnLst>'
        '<p:par><p:cTn id="5" presetID="1" presetClass="mediacall" presetSubtype="0" '
        'fill="hold" nodeType="afterEffect">'
        '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
        '<p:childTnLst>'
        '<p:cmd type="call" cmd="playFrom(0.0)">'
        '<p:cBhvr><p:cTn id="6" dur="%(D)s" fill="hold" repeatCount="indefinite"/>'
        '<p:tgtEl><p:spTgt spid="%(S)s"/></p:tgtEl></p:cBhvr>'
        '</p:cmd>'
        '</p:childTnLst></p:cTn></p:par>'
        '</p:childTnLst></p:cTn></p:par>'
        '</p:childTnLst></p:cTn></p:par>'
        '</p:childTnLst>'
        '</p:cTn>'
        '<p:prevCondLst><p:cond evt="onPrev" delay="0">'
        '<p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
        '<p:nextCondLst><p:cond evt="onNext" delay="0">'
        '<p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>'
        '</p:seq>'
        '<p:video><p:cMediaNode vol="80000">'
        '<p:cTn id="10" fill="hold" display="0">'
        '<p:stCondLst><p:cond delay="indefinite"/></p:stCondLst></p:cTn>'
        '<p:tgtEl><p:spTgt spid="%(S)s"/></p:tgtEl>'
        '</p:cMediaNode></p:video>'
        '</p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>'
    ) % {'P': P, 'A': A, 'R': R, 'D': int(dur_ms), 'S': spid}
    el = etree.fromstring(xml)
    slide._element.append(el)


ENTR_GRP_XML = (
    '<p:par xmlns:p="%(P)s"><p:cTn id="%(I1)s" fill="hold">'
    '<p:stCondLst><p:cond delay="indefinite"/>'
    '<p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond></p:stCondLst>'
    '<p:childTnLst><p:par><p:cTn id="%(I2)s" fill="hold">'
    '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
    '<p:childTnLst><p:par><p:cTn id="%(I3)s" presetClass="%(CLS)s" presetID="%(PID)s" '
    'presetSubtype="%(SUB)s" fill="hold" nodeType="%(NT)s">'
    '<p:stCondLst><p:cond delay="%(DL)s"/></p:stCondLst>'
    '<p:childTnLst>%(BODY)s</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>'
    '</p:childTnLst></p:cTn></p:par>'
)

SET_VIS_XML = (
    '<p:set><p:cBhvr><p:cTn id="%(ID)s" dur="1" fill="hold">'
    '<p:stCondLst><p:cond delay="%(DL2)s"/></p:stCondLst></p:cTn>'
    '<p:tgtEl><p:spTgt spid="%(S)s"/></p:tgtEl>'
    '<p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr>'
    '<p:to><p:strVal val="%(VAL)s"/></p:to></p:set>'
)

EFFECT_XML = (
    '<p:animEffect transition="%(TR)s" filter="%(FILTER)s"><p:cBhvr>'
    '<p:cTn id="%(ID)s" dur="%(D)s" fill="hold"/>'
    '<p:tgtEl><p:spTgt spid="%(S)s"/></p:tgtEl>'
    '</p:cBhvr></p:animEffect>'
)

ANIM_POS_XML = (
    '<p:anim calcmode="lin" valueType="num"><p:cBhvr additiveMode="base"><p:cTn id="%(ID)s" '
    'dur="%(D)s" fill="hold"/><p:tgtEl><p:spTgt spid="%(S)s"/></p:tgtEl>'
    '<p:attrNameLst><p:attrName>%(ATTR)s</p:attrName></p:attrNameLst></p:cBhvr>'
    '<p:tavLst><p:tav tm="0"><p:val><p:strVal val="%(FROM)s"/></p:val></p:tav>'
    '<p:tav tm="100000"><p:val><p:strVal val="#%(ATTR2)s"/></p:val></p:tav></p:tavLst></p:anim>'
)


VIDEO_CLICK_XML = (
    '<p:seq xmlns:p="%(P)s" concurrent="1" nextAc="seek">'
    '<p:cTn id="%(A)s" restart="whenNotActive" fill="hold" evtFilter="cancelBubble" '
    'nodeType="interactiveSeq">'
    '<p:stCondLst><p:cond evt="onClick" delay="0"><p:tgtEl><p:spTgt spid="%(S)s"/>'
    '</p:tgtEl></p:cond></p:stCondLst>'
    '<p:endSync evt="end" delay="0"><p:rtn val="all"/></p:endSync>'
    '<p:childTnLst><p:par><p:cTn id="%(B)s" fill="hold">'
    '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
    '<p:childTnLst><p:par><p:cTn id="%(C)s" fill="hold">'
    '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
    '<p:childTnLst><p:par><p:cTn id="%(D)s" presetID="2" presetClass="mediacall" '
    'presetSubtype="0" fill="hold" nodeType="clickEffect">'
    '<p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
    '<p:cmd type="call" cmd="togglePause"><p:cBhvr><p:cTn id="%(E)s" dur="1" fill="hold"/>'
    '<p:tgtEl><p:spTgt spid="%(S)s"/></p:tgtEl></p:cBhvr></p:cmd>'
    '</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>'
    '</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn>'
    '<p:nextCondLst><p:cond evt="onClick" delay="0"><p:tgtEl><p:spTgt spid="%(S)s"/>'
    '</p:tgtEl></p:cond></p:nextCondLst></p:seq>'
)


def inject_video_click(slide, spid):
    """点击画面暂停/继续：语料 165 册、1042 页每个内嵌视频都写了这条 interactiveSeq
    （onClick -> mediacall presetID=2 -> cmd togglePause，共 1036 次）。
    引擎此前只写自动播（playFrom），观众点视频不会停——这条补齐放映时的控制。"""
    from lxml import etree
    P_ = '{%s}' % P
    ids = [int(e.get('id')) for e in slide._element.iter(P_ + 'cTn')
           if (e.get('id') or '').isdigit()]
    base = (max(ids) if ids else 2) + 1
    xml = VIDEO_CLICK_XML % {'P': P, 'S': str(spid), 'A': base, 'B': base + 1,
                             'C': base + 2, 'D': base + 3, 'E': base + 4}
    el = etree.fromstring(xml)
    tm = slide._element.findall('.//' + P_ + 'cTn[@nodeType="tmRoot"]/' + P_ + 'childTnLst')
    if not tm:
        return False
    tm[0].append(el)
    return True


def _spec_for(preset):
    """preset 名 -> 语料实测三元组与签名（kb/animation_mechanics.json 对账，见 G18）。"""
    spec = CT.ANIM.get(preset)
    if spec is None:
        raise KeyError('未知动画 preset：%s（可用：%s）' % (preset, ','.join(sorted(CT.ANIM))))
    return spec


def _behaviours(spec, spid, dur_ms, base, cls):
    """按语料实测签名生成原语：filter 类走 p:animEffect，位置类走 p:anim(#ppt_x/#ppt_y)，
    出现/消失只写 p:set(style.visibility)。entr 851 次全部带 set->visible，exit 108 次全部带
    set->hidden，两者顺序也与成品取样一致。"""
    from lxml import etree
    ids = [base]
    out = []

    def nid():
        ids[0] += 1
        return ids[0]
    tr = spec.get('transition') or 'in'
    if spec.get('filter'):
        out.append(EFFECT_XML % {'TR': tr, 'FILTER': spec['filter'], 'ID': nid(),
                                 'D': int(dur_ms), 'S': str(spid)})
    if spec.get('attr'):
        for attr, frm in ((spec['attr'], spec.get('from') or '0-#ppt_w/2'),):
            out.append(ANIM_POS_XML % {'ID': nid(), 'D': int(dur_ms), 'S': str(spid),
                                       'ATTR': attr.lstrip('#'), 'FROM': frm,
                                       'ATTR2': attr.lstrip('#')})
        # 垂直方向同时给一条，语料的位移入场是两个 p:anim（entr:53/42 实测 2 个 anim）
        out.append(ANIM_POS_XML % {'ID': nid(), 'D': int(dur_ms), 'S': str(spid),
                                   'ATTR': 'ppt_y', 'FROM': '1+#ppt_h/2', 'ATTR2': 'ppt_y'})
    vis = 'hidden' if tr == 'out' else 'visible'
    if cls in ('entr', 'exit'):
        out.append(SET_VIS_XML % {'ID': nid(), 'DL2': max(0, int(dur_ms) - 1) if tr == 'out' else 0,
                                  'S': str(spid), 'VAL': vis})
    return ''.join(out), ids[0]


def _inject_preset(slide, spid, preset, delay_ms, dur_ms, node_type):
    spec = _spec_for(preset)
    cls, pid, sub = spec['triple']
    from lxml import etree
    ids = [int(e.get('id')) for e in slide._element.iter('{%s}cTn' % P)
           if (e.get('id') or '').isdigit()]
    base = (max(ids) if ids else 2) + 1
    body, max_id = _behaviours(spec, spid, dur_ms, base, cls)
    grp = ENTR_GRP_XML % {'P': P, 'I1': base, 'I2': base + 1, 'I3': base + 2,
                          'DL': int(delay_ms), 'CLS': cls, 'PID': pid, 'SUB': sub or '0',
                          'NT': node_type, 'BODY': body}
    el = etree.fromstring(grp)
    main = slide._element.findall('.//{%(P)s}cTn[@nodeType="mainSeq"]/{%(P)s}childTnLst'
                                  % {'P': P})
    if main:
        main[0].append(el)
        return {'triple': '%s:%s:%s' % (cls, pid, sub), 'filter': spec.get('filter'),
                'attr': spec.get('attr')}
    root = etree.fromstring(TIMING_ROOT_XML % {'P': P, 'A': A, 'R': R,
                                               'GRP': etree.tostring(el, encoding='unicode')})
    slide._element.append(root)
    return {'triple': '%s:%s:%s' % (cls, pid, sub), 'filter': spec.get('filter'),
            'attr': spec.get('attr')}


def inject_entrance(slide, spid, delay_ms=0, dur_ms=500, preset_id=10, filter_='fade',
                    preset=None, node_type='afterEffect'):
    """文字入场动效，preset 名取自语料实测三元组表（淡入/擦入四向/飞入/升字…）。

    Why: SPEC §2 要求 ≥30% 页面有运动，而闸以前只看 shots.json 里有没有写 motion 字段——
    成品里其实一页都没动。这条把动效真的写进 OOXML，G6 再按成品 XML 数，G18 按签名核对。
    不传 preset 时沿用旧的 preset_id/filter_（淡入 entr:10:0），保证老 work 目录不变行为。
    """
    if preset:
        return _inject_preset(slide, spid, preset, delay_ms, dur_ms, node_type)
    spec = {'triple': ('entr', str(preset_id), '0'), 'filter': filter_, 'attr': None,
            'transition': 'in'}
    from lxml import etree
    P_ = '{%s}' % P
    ids = [int(e.get('id')) for e in slide._element.iter(P_ + 'cTn')
           if (e.get('id') or '').isdigit()]
    base = (max(ids) if ids else 2) + 1
    body, _m = _behaviours(spec, spid, dur_ms, base, 'entr')
    grp = ENTR_GRP_XML % {'P': P, 'I1': base, 'I2': base + 1, 'I3': base + 2,
                          'DL': int(delay_ms), 'CLS': 'entr', 'PID': int(preset_id),
                          'SUB': '0', 'NT': node_type, 'BODY': body}
    el = etree.fromstring(grp)
    main = slide._element.findall('.//' + P_ + 'cTn[@nodeType="mainSeq"]/' + P_ + 'childTnLst')
    if main:
        main[0].append(el)
        return {'triple': 'entr:%s:0' % int(preset_id), 'filter': filter_, 'attr': None}
    try:
        root = etree.fromstring(TIMING_ROOT_XML % {'P': P, 'A': A, 'R': R,
                                                   'GRP': etree.tostring(el, encoding='unicode')})
    except Exception:
        return None
    slide._element.append(root)
    return {'triple': 'entr:%s:0' % int(preset_id), 'filter': filter_, 'attr': None}


def inject_exit(slide, spid, preset='exit-fade', delay_ms=0, dur_ms=500,
                node_type='withEffect'):
    """出场：语料 exit 108 次全是 animEffect(out)+set->hidden，淡出 61、擦出 12、消失 27。"""
    return _inject_preset(slide, spid, preset, delay_ms, dur_ms, node_type)

TIMING_ROOT_XML = (
    '<p:timing xmlns:p="%(P)s" xmlns:a="%(A)s" xmlns:r="%(R)s"><p:tnLst><p:par>'
    '<p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>'
    '<p:seq concurrent="1" nextAc="seek">'
    '<p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>%(GRP)s</p:childTnLst></p:cTn>'
    '<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
    '<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>'
    '</p:seq></p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>'
)


def ensure_anchor(slide):
    """Morph only animates shapes shared by name with the previous slide. Drop an invisible 1x1
    anchor so there is always something for morph to interpolate."""

    from pptx.util import Emu
    for sp in slide.shapes:
        if sp.name == 'morph_anchor':
            return sp
    a = slide.shapes.add_shape(1, Emu(0), Emu(0), Emu(1), Emu(1))
    a.name = 'morph_anchor'
    a.fill.background()
    a.line.fill.background()
    a.shadow.inherit = False
    return a


def add_morph(slide, dur_ms=2000):
    """Smooth morph as camera move, exactly as the decks do it (p159:morph byObject + fade fallback)."""
    from lxml import etree
    xml = (
        '<mc:AlternateContent xmlns:mc="%s" xmlns:p="%s" xmlns:p14="%s" xmlns:p159="%s">'
        '<mc:Choice Requires="p159">'
        '<p:transition spd="slow" p14:dur="%d"><p159:morph option="byObject"/></p:transition>'
        '</mc:Choice><mc:Fallback>'
        '<p:transition spd="slow"><p:fade/></p:transition>'
        '</mc:Fallback></mc:AlternateContent>'
    ) % (MC, P, P14, P159, int(dur_ms))
    slide._element.append(etree.fromstring(xml))


TRANS_KINDS = {'checker': '<p:checker/>', 'blinds': '<p:blinds dir="vert"/>'}

# 语料把照片"锁进品牌色"靠的不是改图片像素，而是 a:blip 里的 a:duotone（实测 duotone 74 处、
# 其中的 satMod 196 处；文字侧的 tint 30 处）。写法照抄 NO.176 海浪动态封面 / NO.72 创赛级科技风：
#   <a:duotone><a:srgbClr val="016DFF"><a:shade val="45000"/><a:satMod val="135000"/></a:srgbClr>
#   <a:prstClr val="white"/></a:duotone>
DUOTONE = {'tech_blue': '016DFF'}


def add_duotone(pic, style='tech_blue', shade=45000, sat_mod=135000):
    """给整页实景图加双色调锁色。非破坏性：图片本体一个字节没改，改的是 OOXML 渲染指令，
    在 PowerPoint 里可以单独撤掉——这比 grade_asset 烘进 JPG 的做法更符合"可编辑"这条硬要求。

    只有语料实测过色值的风格才做（tech_blue=016DFF）；ink_wash/photo_real 没有实测样本，
    与其编一个颜色，不如退回像素调色。
    """
    from lxml import etree
    A_ = '{%s}' % A
    # 语料只在蓝青族实测过 duotone 色值；色板没有实测值就返回 False，
    # 由调用方写进 build_log 与 G6，而不是编一个颜色（编的颜色会被 G16 判成板外色）
    accent = (CT.PALETTES.get(style) or {}).get('duotone_hex') or DUOTONE.get(style)
    if not accent:
        return False
    blip = pic._element.find('.//' + A_ + 'blip')
    if blip is None or blip.find(A_ + 'duotone') is not None:
        return False
    duo = blip.makeelement(A_ + 'duotone', {})
    clr = etree.SubElement(duo, A_ + 'srgbClr')
    clr.set('val', accent)
    etree.SubElement(clr, A_ + 'shade').set('val', str(int(shade)))
    etree.SubElement(clr, A_ + 'satMod').set('val', str(int(sat_mod)))
    etree.SubElement(duo, A_ + 'prstClr').set('val', 'white')
    # CT_ImageBlip 里 duotone 属于 blip 的变换选择项，必须排在 a:extLst 之前
    ext = blip.find(A_ + 'extLst')
    if ext is not None:
        ext.addprevious(duo)
    else:
        blip.append(duo)
    return True


EMPH_SCALE_XML = (
    '<p:par xmlns:p="%(P)s"><p:cTn id="%(I1)s" fill="hold">'
    '<p:stCondLst><p:cond delay="indefinite"/>'
    '<p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond></p:stCondLst>'
    '<p:childTnLst><p:par><p:cTn id="%(I2)s" fill="hold">'
    '<p:stCondLst><p:cond delay="0"/></p:stCondLst>'
    '<p:childTnLst><p:par><p:cTn id="%(I3)s" presetClass="emph" presetID="6" '
    'presetSubtype="0" accel="50000" decel="50000" fill="hold" nodeType="withEffect">'
    '<p:stCondLst><p:cond delay="%(DL)s"/></p:stCondLst>'
    '<p:childTnLst><p:animScale><p:cBhvr>'
    '<p:cTn id="%(I4)s" dur="%(D)s" fill="hold"/>'
    '<p:tgtEl><p:spTgt spid="%(S)s"/></p:tgtEl>'
    '</p:cBhvr><p:by x="%(BY)s" y="%(BY)s"/></p:animScale>'
    '</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>'
    '</p:childTnLst></p:cTn></p:par>'
)


def inject_emphasis_scale(slide, spid, by_pct=15, dur_ms=1000, delay_ms=0):
    """放大强调。语料实测 animScale 85 处：NO.144 动态数字过渡页把大数字放大 15%%（by=150000），
    NO.136 花瓣开场把主体放大 150%%（by=1500000）；presetClass="emph" presetID="6"、
    accel/decel=50000 都照抄原文，不是编的。"""
    from lxml import etree
    P_ = '{%s}' % P
    ids = [int(e.get('id')) for e in slide._element.iter(P_ + 'cTn')
           if (e.get('id') or '').isdigit()]
    base = (max(ids) if ids else 2) + 1
    grp = EMPH_SCALE_XML % {'P': P, 'I1': base, 'I2': base + 1, 'I3': base + 2, 'I4': base + 3,
                            'DL': int(delay_ms), 'D': int(dur_ms), 'S': str(spid),
                            'BY': int(round(float(by_pct) * 1000))}
    el = etree.fromstring(grp)
    main = slide._element.findall('.//' + P_ + 'cTn[@nodeType="mainSeq"]/' + P_ + 'childTnLst')
    if main:
        main[0].append(el)
        return True
    root = etree.fromstring(TIMING_ROOT_XML % {'P': P, 'A': A, 'R': R,
                                              'GRP': etree.tostring(el, encoding='unicode')})
    slide._element.append(root)
    return True


# 技法卡 → 成品 slide XML 里必须数得到的 token。**只有引擎真能产出的卡才进这张表**：
# bool-shape（布尔/裁切）、motion-path（直线动画）、palette-lock（主题色）、softedge-glow、
# sound-cue（音效）是教程里在 PowerPoint 点的手工活，卡片如实标注，闸不拦（拦了就是误伤）。
# 这张表存在的理由：14 张卡里有 2 张（13/14）曾经只活在文档里，分镜声明了、成品一行没写，
# G2 却只验"卡 id 存不存在"，于是一路绿灯。
RECIPE_TOKENS = {
    'video-fullbleed': ('整页视频（p:video + mediacall）', lambda f: f['has_video']),
    'gif-motion-element': ('内嵌多帧 GIF', lambda f: f['has_gif']),
    'morph-camera': ('p159:morph 平滑转场', lambda f: bool(f['morph_ms'])),
    'mask-dim': ('a:gradFill 压暗蒙版', lambda f: f['has_scrim']),
    'giant-numeral': ('≥100pt 的大数字（a:rPr sz）',
                      lambda f: f['max_font_sz'] >= TH['giant_num_sz_min']),
    'text-animation-presets': ('entr/emph/path 动效节点', lambda f: f['entr_nodes'] > 0),
    'table-motion': ('p:checker 或 p:blinds 转场',
                     lambda f: [k for k in (f.get('trans_kinds') or []) if k in TRANS_KINDS]),
    '3d-model-depth': ('a:sp3d 立体（挤出/棱台）', lambda f: f['has_sp3d']),
    'blip-duotone': ('a:duotone 双色调锁色', lambda f: f['has_duotone']),
    'emphasis-scale': ('p:animScale 放大强调', lambda f: f['anim_scale'] > 0),
    'split-reflow': ('图片按实测栏位框裁切（非满幅），文字落在互补区',
                     lambda f: f['pic_max_frac'] < 0.88 and f['pic_max_w'] >= 0.28),
    'palette-lock': ('theme1.xml accent1 已写入色板主色', lambda f: f['theme_accent1']),
}



def add_page_transition(slide, kind='checker', dur_ms=None):
    """整页转场走棋盘（p:checker）或百叶（p:blinds）——这两条是语料里表格页专用的开场：
    "PPT7×5神奇表格.pptx" 全册 p:checker（实测 10 次）、"产品需求文档.pptx" 用
    p:blinds dir="vert"（35 次），写法照抄其 AlternateContent + p14:dur + Fallback。

    一页只容得下一个 p:transition，所以调用方必须保证这页没挂 morph。
    """
    from lxml import etree
    if kind not in TRANS_KINDS:
        raise ValueError("add_page_transition 只认 %s，收到 %s" % (sorted(TRANS_KINDS), kind))
    dur_ms = int(dur_ms or TH['trans_dur_ms'])
    inner = TRANS_KINDS[kind]
    xml = (
        '<mc:AlternateContent xmlns:mc="%s" xmlns:p="%s" xmlns:p14="%s">'
        '<mc:Choice Requires="p14">'
        '<p:transition spd="slow" p14:dur="%d">%s</p:transition>'
        '</mc:Choice><mc:Fallback>'
        '<p:transition spd="slow">%s</p:transition>'
        '</mc:Fallback></mc:AlternateContent>'
    ) % (MC, P, P14, dur_ms, inner, inner)
    slide._element.append(etree.fromstring(xml))


def add_3d_panel(slide, anchor, pad=0.018, extrusion_h=114300, contour_w=19050,
                 contour_clr=None, style='tech_blue'):
    """立体展台：给大数字/标题垫一块有厚度的深色板（a:sp3d 挤出 + 棱台 + a:scene3d 相机 + 反射），
    然后把它插到文字**下面**——文字仍在最上层，G4 对比度量的还是白字压暗底。

    语料实测 sp3d 170 / scene3d 154 / reflection 348 处，全都出现在形状与图片上，bevelT 只有
    11 次（只当点缀），所以这里只给标题级元素加立体，不给正文形状加。anchor 是 python-pptx 形状。
    """
    from pptx.util import Emu
    from pptx.enum.shapes import MSO_SHAPE
    from lxml import etree
    A_ = '{%s}' % A
    padx, padt, padb = int(pad * EMU_W), int(pad * EMU_H), int(1.6 * pad * EMU_H)
    box = [anchor.left - padx, anchor.top - padt, anchor.width + 2 * padx, 0]
    box[2] = min(box[2], EMU_W)
    box[0] = max(0, min(box[0], EMU_W - box[2]))
    bottom = min(EMU_H, anchor.top + anchor.height + padb)
    # 上边缘不许咬到前一行的下边缘：实测 0.018 的匀等内缩把金框压在了"1.1.1 城市分层"那行上，
    # 越界闸与对比度闸都量不到这种"压住上一行"的碰撞。
    above = 0
    below = EMU_H
    for other in slide.shapes:
        el = getattr(other, '_element', None)
        if el is anchor._element or other.name == 'panel3d' or other.top is None:
            continue
        if other.top + other.height <= anchor.top:
            above = max(above, other.top + other.height)
        elif other.top >= anchor.top + anchor.height:
            below = min(below, other.top)
    box[1] = max(box[1], above + int(0.004 * EMU_H))
    bottom = min(bottom, below - int(0.004 * EMU_H))
    box[3] = max(int(0.03 * EMU_H), bottom - box[1])
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(box[0]), Emu(box[1]),
                                 Emu(box[2]), Emu(box[3]))
    shp.name = 'panel3d'
    spPr = shp._element.spPr
    for child in list(spPr):
        if child.tag in ('{%s}solidFill' % A, '{%s}gradFill' % A, '{%s}noFill' % A,
                         '{%s}effectLst' % A, '{%s}scene3d' % A, '{%s}sp3d' % A):
            spPr.remove(child)
    # CT_SpPr 是**有序**序列：xfrm → 几何 → fill → ln → effectLst → scene3d → sp3d。
    # 手写 SubElement 追加会把 ln 排在 fill 前面，PowerPoint 打开就弹"需要修复"，
    # 所以 fill/ln 走 python-pptx 的 insert-order 感知 API，只手工加后面那三个。
    from pptx.dml.color import RGBColor
    rgb = FIELD_PAL.get(style, FIELD_PAL['tech_blue'])[0]
    shp.fill.solid()
    shp.fill.fore_color.rgb = RGBColor(*rgb)
    clr = spPr.find('{%s}solidFill/{%s}srgbClr' % (A, A))
    etree.SubElement(clr, '{%s}alpha' % A).set('val', '62000')
    shp.line.fill.background()
    # 顺序必须按 CT_SpPr：fill → ln → effectLst → scene3d → sp3d，写反了 PowerPoint 会报修复
    eff = etree.SubElement(spPr, '{%s}effectLst' % A)
    refl = etree.SubElement(eff, '{%s}reflection' % A)
    for k, v in (('blurRad', '101600'), ('stA', '40000'), ('endPos', '90000'), ('dist', '50800'),
                 ('dir', '5400000'), ('sy', '-100000'), ('algn', 'bl'), ('rotWithShape', '0')):
        refl.set(k, v)
    sc = etree.SubElement(spPr, '{%s}scene3d' % A)
    # perspectiveFront 而不是 orthographicFront：正交相机下这块板只显出一圈描边，看不出厚度
    etree.SubElement(sc, '{%s}camera' % A).set('prst', 'perspectiveFront')
    lr = etree.SubElement(sc, '{%s}lightRig' % A)
    lr.set('rig', 'threePt')
    lr.set('dir', 't')
    sp3 = etree.SubElement(spPr, '{%s}sp3d' % A)
    sp3.set('extrusionH', str(int(extrusion_h)))
    sp3.set('contourW', str(int(contour_w)))
    sp3.set('prstMaterial', 'matte')
    # 轮廓色跟全册风格走：写死金色会在 ink_wash/photo_real 页上打架（三套风格的第三色就是强调色）
    if not contour_clr:
        contour_clr = '%02X%02X%02X' % FIELD_PAL.get(style, FIELD_PAL['tech_blue'])[2]
    cc = etree.SubElement(sp3, '{%s}contourClr' % A)
    etree.SubElement(cc, '{%s}srgbClr' % A).set('val', contour_clr)
    bt = etree.SubElement(sp3, '{%s}bevelT' % A)
    bt.set('w', '63500')
    bt.set('h', '25400')
    bt.set('prst', 'circle')
    # z 序：挪到锚点文字之前，否则不透明板盖住文字
    anchor._element.addprevious(shp._element)
    return shp


def split_zone_fits(pg, zone):
    """正文放不进实测栏位时不要硬塞：先估一次行高，放不下就退回满幅原型。

    Why: 分栏原型给文字的是母版占位符那一栏（宽 0.425），Word 的整段正文常放不下；
    之前 place_text 会压框高把溢出记进 build_log，G5 再判不合格——与其让闸门红，
    不如在装配阶段选一个内容真放得下的原型，并把改判写进日志。
    """
    zw = zone[2]
    need = 0.0
    head = pg.get('headline') or ''
    body = [x for x in (pg.get('body') or []) if x]
    tpt = pick_pt(head, zw, 30, 20)
    need += line_height_frac(tpt) * est_lines(head, tpt, zw) + 0.012
    for i, b in enumerate(body):
        pt = 16 if i < 6 else 13
        need += line_height_frac(pt) * est_lines(b, pt, zw) + 0.012
    for sec in (pg.get('sections') or []):
        need += line_height_frac(14) * est_lines(sec, 14, zw) + 0.012
    return need <= zone[3] + 0.002


def palette_for(style, name=None):
    """色板解析：--palette 指名就用指名的，否则跟分镜的出图风格走。"""
    key = name or style
    if key not in CT.PALETTES:
        key = style if style in CT.PALETTES else 'tech_blue'
    return key, CT.PALETTES[key]


LAYOUT_ZONE = {
    # 满幅压字：语料 608 页（27.3%），实测 99.2% 的文字框都落在图上
    'full-bleed': {'pic': [0.0, 0.0, 1.0, 1.0], 'zone': None},
    # 文左图右：母版/版式里 3814 个 body + 203 个 pic 占位符的实测中位框（PLACEHOLDER_SLOTS），
    # 成品按分类器会归到 split-centre-image（语料 504 页，22.7%）——图上一字不压
    'split-centre-image': {'pic': list(CT.PLACEHOLDER_SLOTS['pic']),
                           'zone': [0.0689, 0.0667, 0.4251, 0.77]},
    'centred-statement': {'pic': None, 'zone': [0.10, 0.25, 0.80, 0.50]},
    'text-block': {'pic': None, 'zone': None},
    # 多图栅格：图块坐标由 tile_boxes() 按语料实测图块尺寸现算，文字走左栏
    'photo-wall': {'pic': None, 'zone': [0.060, 0.120, 0.430, 0.740], 'min_tiles': 6},
    'card-grid': {'pic': None, 'zone': [0.060, 0.120, 0.430, 0.740], 'min_tiles': 3},
    'timeline-on-line': {'pic': None, 'zone': [0.060, 0.100, 0.400, 0.760], 'min_tiles': 3},
}
TILE_LAYOUTS = ('photo-wall', 'card-grid', 'timeline-on-line')


def layout_for(role, s, has_hero, pg=None):
    """这一页走哪种图文重排原型（名字与 kb/layout_archetypes.json 的 key 一致，闸门按同名复核）。"""
    want = s.get('layout') or CT.ROLE_LAYOUT.get(role, 'full-bleed')
    if want not in LAYOUT_ZONE:
        want = 'full-bleed'
    # 多图栅格原型不靠单张 hero 图，走 has_hero 之外的分支（装配里按 find_gen_all 判）
    if want in ('split-centre-image', 'centred-statement') and not has_hero:
        # 没有真素材时走矢量色场铺满（满幅），只有附表/附录页才落到语料的纯色底原型
        want = 'text-block' if role in ('table', 'appendix') else 'full-bleed'
    # 语料 60.8% 的页是图占满/出血（pure-visual 33.5 + full-bleed 27.3），分栏只占 22.7%。
    # 旧写法给所有带图的数据页都派分栏：照片缩成右半小框、左半几行小字、下半页全空——
    # 逐页目检被用户直接否掉（"整体布局和目标相差太远"）。正文只有 ≤2 行时根本不需要
    # 一条干净文字栏，让图铺满、字压上去（语料 full-bleed 压字率 99.2%）才是那本书的样子。
    if want == 'split-centre-image' and pg is not None:
        _nb = len([x for x in (pg.get('display_lines') or pg.get('body') or []) if x])
        if _nb <= 2:
            want = 'full-bleed'
    return want


def add_picture_in_box(slide, img, box, name='hero', shape='rect'):
    """把图裁到指定画布比例框内（srcRect 居中裁切，不拉伸变形），返回 shape。

    shape 给 prstGeom 预设（diamond/ellipse/roundRect/snip2DiagRect）：语料技法册的图文页
    不是把照片塞进方框——NO.103 菱形介绍把图切成菱形/三角拼贴，NO.119 图片墙把一组图
    装进圆弧遮罩。方框直贴是"错误示范"里最显眼的一种（用户第八轮点名）。"""
    from pptx.util import Emu
    from PIL import Image
    W_, H_ = float(EMU_W), float(EMU_H)
    x, y, w, h = box
    pic = slide.shapes.add_picture(img, Emu(int(x * W_)), Emu(int(y * H_)),
                                   Emu(int(w * W_)), Emu(int(h * H_)))
    pic.name = name
    if shape and shape != 'rect':
        from lxml import etree
        spPr = pic._element.spPr
        prst = spPr.find('{%s}prstGeom' % A)
        if prst is None:
            prst = etree.SubElement(spPr, '{%s}prstGeom' % A)
            av = etree.SubElement(prst, '{%s}avLst' % A)
        prst.set('prst', shape)
    try:
        iw, ih = Image.open(img).size
    except Exception:
        return pic
    box_ar = w / float(h)
    img_ar = iw / float(ih)
    if img_ar <= 0:
        return pic
    from lxml import etree
    A_ = '{%s}' % A
    blipFill = pic._element.find(A_ + 'blipFill')
    if blipFill is None:
        return pic
    sr = etree.SubElement(blipFill, A_ + 'srcRect')
    if img_ar > box_ar:                       # 图更宽：左右各裁一点
        cut = (1.0 - img_ar / box_ar) / 2.0 if box_ar else 0.0
        sr.set('l', str(int(max(0.0, cut) * 100000)))
        sr.set('r', str(int(max(0.0, cut) * 100000)))
    else:                                     # 图更高：上下各裁一点
        cut = (1.0 - box_ar / img_ar) / 2.0
        sr.set('t', str(int(max(0.0, cut) * 100000)))
        sr.set('b', str(int(max(0.0, cut) * 100000)))
    st = etree.SubElement(blipFill, A_ + 'stretch')
    etree.SubElement(st, A_ + 'fillRect')
    return pic


def put_background(slide, img, box, shape='rect'):
    """满幅还是按框裁切，取决于这一页走哪种实测版式原型。"""
    if box and [round(x, 4) for x in box] != [0.0, 0.0, 1.0, 1.0]:
        return add_picture_in_box(slide, img, box, shape=shape)
    return add_fullbleed_picture(slide, img)


def apply_theme(pptx_path, pal_name, pal):
    """把色板写进 ppt/theme/theme1.xml 的 clrScheme（本技能多做的一步，语料 167/171 册没改主题）。

    为什么要写：不改主题时，换配色只能逐形状重刷；写了 accent1..6，PowerPoint 里改一个主题色
    整片跟着变，成品也才能被 G16 按"主题主色 == 色板主色"核对。
    """
    import zipfile
    from lxml import etree
    tmp = pptx_path + '.themetmp'
    th_name = None
    z = zipfile.ZipFile(pptx_path)
    names = [i.filename for i in z.infolist()]
    cand = sorted(n for n in names if re.match(r'ppt/theme/theme\d+\.xml$', n))
    if not cand:
        z.close()
        return False, '成品里没有 theme XML'
    th_name = cand[0]
    root = etree.fromstring(z.read(th_name))
    A_ = '{%s}' % A
    scheme = root.find('.//' + A_ + 'clrScheme')
    changed = 0
    if scheme is not None:
        want = {'dk1': pal['theme'].get('dk1'), 'lt1': pal['theme'].get('lt1'),
                'dk2': pal['theme'].get('dk2'), 'lt2': pal['theme'].get('lt2')}
        for i in range(1, 7):
            want['accent%d' % i] = pal['theme'].get('accent%d' % i)
        for el in scheme:
            tag = etree.QName(el).localname
            hx = want.get(tag)
            if not hx:
                continue
            for ch in list(el):
                el.remove(ch)
            c = etree.SubElement(el, A_ + 'srgbClr')
            c.set('val', hx)
            changed += 1
    fs = root.find('.//' + A_ + 'fontScheme')
    fonts = 0
    if fs is not None:
        for kind in ('majorFont', 'minorFont'):
            el = fs.find(A_ + kind)
            if el is None:
                continue
            for sub_name, face in (('latin', 'Calibri'), ('ea', u'微软雅黑')):
                t = el.find(A_ + sub_name)
                if t is not None and t.get('typeface') != face:
                    t.set('typeface', face)
                    fonts += 1
    xml = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
    zin = z
    zout = zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED)
    moved = False
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename == th_name:
            data, moved = xml, True
        zout.writestr(item, data)
    zout.close()
    zin.close()
    if not moved:
        os.remove(tmp)
        return False, 'theme 里找不到 clrScheme'
    shutil.move(tmp, pptx_path)
    return True, '主题色 %d 格已按色板 %s 写入（字体 %d 处）' % (changed, pal_name, fonts)


def cmd_build(a):
    from pptx import Presentation
    from pptx.util import Emu
    wp = work_paths(a.work)
    shots = jr(wp['shots']) or die("先跑 plan")
    outline = jr(wp['outline'])
    man = {}
    mp = os.path.join(wp['materials'], 'manifest.jsonl')
    if os.path.exists(mp):
        for ln in io.open(mp, encoding='utf-8'):
            try:
                r = json.loads(ln)
                man[r['page']] = r
            except Exception:
                pass
    # G26 装配前硬闸：有提示词、没产物、又没用户回执 —— 不降级、不蒙混，停下来问
    ok_, bad_, cards_ = validate_prompts(wp, shots, outline)
    g26 = DG.gate(wp['promptpack'], cards_, wp['gen'], TH,
                  allow_still=bool(getattr(a, 'allow_still', False)))
    if g26['blocking']:
        print("=" * 76)
        print("停止装配（G26 投放回执）")
        for r in g26['reasons']:
            print("  · %s" % r)
        if g26['missing']:
            print("\n缺的产物（放回这些精确绝对路径）：")
            print(DG.card_excerpt(g26.get('card') or os.path.join(wp['promptpack'], 'delivery_card.md'),
                                  g26['missing']))
        print("\n请把 promptpack/delivery_card.md 的全文发给用户（跑 pack 时已全文打印过），"
              "然后只问这一句：")
        print("    「%s」" % DG.ASK)
        print("用户回『是』且文件已放回：caofeng.py ack --work <目录> --ready")
        print("用户明确回『不要视频背景』：caofeng.py ack --work <目录> --no-video "
              "（只豁免 video 类）")
        print("=" * 76)
        sys.exit(1)
    if g26['waived']:
        print("G26：用户回执豁免 %d 项 video 产物（%s）——报告里会显式声明这不是视频页" % (
            len(g26['waived']), [i['page'] for i in g26['waived']][:12]))
    prs = Presentation()
    prs.slide_width = Emu(EMU_W)
    prs.slide_height = Emu(EMU_H)
    blank = prs.slide_layouts[6]
    pal_style = collections.Counter(
        [s.get('style', 'tech_blue') for s in shots['pages']] or ['tech_blue']).most_common(1)[0][0]
    pal_name, pal = palette_for(pal_style, getattr(a, 'palette', None))
    print("色板：%s（%s），落字 %s，深底 %s" % (
        pal_name, pal['name'], pal['ink']['text'], '%02X%02X%02X' % tuple(pal['deep'])))
    built = []
    for s in sorted(shots['pages'], key=lambda x: x['page']):
        n = s['page']
        pg = dict(outline['pages'][n - 1])
        # the agent edits headlines in shots.json (pipeline S1 says so); honour them
        if s.get('headline'):
            pg['headline'] = s['headline']
        if s.get('body') is not None:
            pg['body'] = s['body']
        if s.get('display_lines') is not None:
            pg['display_lines'] = s['display_lines']
        if s.get('hero_number') is not None:
            pg['hero_number'] = s['hero_number']
        if s.get('content_type') is not None:
            pg['content_type'] = s['content_type']
        pg['verbatim'] = bool(outline.get('verbatim'))
        slide = prs.slides.add_slide(blank)
        rec = {'page': n, 'role': s['role'], 'bg': None, 'video': None, 'texts': 0, 'notes': []}
        _motif = n % 3   # 按页序轮换：相邻页母题必不同，连排无素材页也不会同一张脸
        v = find_gen(a.work, n, 'video')
        img = find_gen(a.work, n, 'image') or find_gen(a.work, n, 'first') or man.get(n, {}).get('file')
        # 离题图比没图更糟（用户第八轮点名"把错误示范学到了"）：抓来的图相关度不够就不用它
        # 凑覆盖率，改走矢量设计页；用户自己投放回收的图（gen/）永远信，那是按本页提示词生成的。
        if img and not find_gen(a.work, n, 'image') and not find_gen(a.work, n, 'first'):
            _rel = int(man.get(n, {}).get('relevance', 100))
            if _rel < TH['relevance_min']:
                rec['notes'].append(u'抓来的图与本页实体相关度 %d%% < %d%%：不用离题图凑覆盖率，'
                                    u'改矢量设计页（要真图请走投放回收）' % (_rel, TH['relevance_min']))
                img = None
        if s['role'] == 'table':
            # 台账页的任务是"读数字"：满幅实景会把表格压成底纹，而且按成品 bbox 量出来是
            # full-bleed、与声明的 text-block 不符（实测 5 页被 G17 拦）
            img = None
        # 图文重排：先定这一页走哪种实测原型，再决定留白区落点（语料 27.3% 满幅压字、
        # 22.7% 图居中/右、文字在两侧——不是一律满幅）
        has_hero = bool((v and os.path.exists(v)) or (img and os.path.exists(img)))
        lay = layout_for(s['role'], s, has_hero, pg)
        n_tiles = len(find_gen_all(a.work, n, 'image'))
        if lay in TILE_LAYOUTS and n_tiles < (LAYOUT_ZONE[lay].get('min_tiles') or 3):
            # 栅格原型要够块数才成立（照片墙 >=6、卡片/时间轴 >=3）：
            # 不够就在选原型这一步就降级，别等装配完再让 G17 量出原型不符
            need_t = LAYOUT_ZONE[lay]['min_tiles']
            rec['layout_demoted_from'] = lay
            lay = 'split-centre-image' if has_hero else 'full-bleed'
            s['layout'] = lay
            rec['notes'].append(u'栅格原型图不够（photo-wall 要 %d 块，实际 %d 张）：'
                                u'按 %s 装配' % (need_t, n_tiles, lay))
        rec['layout'] = lay
        hbox = LAYOUT_ZONE[lay]['pic']
        # 满幅页必须用满幅的文字区（zone_for 的 0.84 宽），不能沿用分镜为分栏写的窄
        # reserved_zone（0.425）：窄区把标题折成三行小字、正文挤在左上角、图表缩成小块，
        # 半页空暗底——用户第九轮否的就是这个。分栏页才用 reserved_zone。
        zone = LAYOUT_ZONE[lay]['zone'] or (
            zone_for(s['role']) if lay == 'full-bleed' else s.get('reserved_zone')) \
            or zone_for(s['role'])
        if lay == 'split-centre-image' and not split_zone_fits(pg, zone):
            # 内容放不进栏位就换原型，不让溢出走到 G5 才红
            lay = 'full-bleed'
            hbox = [0.0, 0.0, 1.0, 1.0]
            zone = zone_for(s['role'])
            rec['layout'] = lay
            rec['notes'].append('正文放不进实测分栏栏位（宽 0.425 画布）：改按满幅压字原型装配')
        if v and os.path.exists(v):
            poster = find_gen(a.work, n, 'last') or find_gen(a.work, n, 'image') or img
            if not poster or not os.path.exists(poster):
                poster = make_poster(v, os.path.join(wp['gen'], 'p%03d' % n, 'poster.jpg'))
            spid = attach_video(slide, v, poster, dur_ms=int(s.get('video_dur_ms', 8000)))
            rec['video'] = v
            rec['bg'] = 'video'
            rec['poster_path'] = poster
            # 视频页按语料就是整幅（技法卡 1 video-fullbleed），分栏留给静帧页
            lay, hbox = 'full-bleed', [0.0, 0.0, 1.0, 1.0]
            rec['layout'] = lay
            zone = LAYOUT_ZONE[lay]['zone'] or s.get('reserved_zone') or zone_for(s['role'])
        elif s.get('layout') in ('photo-wall', 'card-grid', 'timeline-on-line')                 and len(find_gen_all(a.work, n, 'image')) >= 3:
            # 语料的多图栅格原型（好示范 24/4/5 页）：一页装多张图，按实测图块尺寸排
            tiles = find_gen_all(a.work, n, 'image')
            need = LAYOUT_ZONE[lay]['min_tiles']
            if len(tiles) < need:
                # 上面已按块数降级过，这里只兜底：真走到这一步说明 shots 被人工改过
                rec['notes'].append('原型 %s 至少要 %d 张图，实际 %d 张：本页不装栅格'
                                    % (lay, need, len(tiles)))
                rec['tiles'] = 0
            else:
                boxes = tile_boxes(lay, len(tiles))
                placed, skipped = 0, []
                for k_t, tf in enumerate(tiles):
                    graded = grade_asset(tf, os.path.join(wp['out'], 'graded',
                                                          'p%03d_t%02d.jpg' % (n, k_t + 1)),
                                         s.get('style', 'tech_blue'))
                    use = graded if graded else tf
                    ds = media_dims(use) or ()
                    if len(ds) == 2 and min(ds) < TH['embed_short_edge']:
                        skipped.append({'slot': k_t + 1, 'wh': list(ds)})
                        continue
                    add_picture_in_box(slide, use, boxes[k_t], name='tile%d' % (k_t + 1),
                                       shape='roundRect')
                    placed += 1
                rec['bg'] = tiles[0]
                rec['tiles'] = placed
                rec['tile_boxes'] = boxes[:placed]
                rec['tiles_skipped_lowres'] = skipped
                rec['notes'].append('多图栅格 %s：%d 块（图块尺寸取语料实测中位）' % (lay, placed))
                if placed < need:
                    rec['notes'].append('栅格只装下 %d 块（<%d）：G17 会按实际原型复核' % (placed, need))
        elif img and os.path.exists(img):
            graded = grade_asset(img, os.path.join(wp['out'], 'graded', 'p%03d.jpg' % n),
                                 s.get('style', 'tech_blue'))
            use = graded if graded else img
            # 调色会在 out/graded/ 另存一份，build_log 的 bg 就变成派生路径了——留一条
            # bg_src 记来源，G28 才认得出"这张是投放产物"（投放产物带平台角标必须拦）
            rec['bg_src'] = img
            # A 1024-short-edge AI still stretched across 1920x1080 reads as blur, and the old code
            # embedded it anyway while G3 quoted the manifest's 4000px original. Refuse and fall back.
            dsize = media_dims(use) or ()
            if len(dsize) == 2 and min(dsize) < TH['embed_short_edge'] and s.get('asset_kind') != 'video_bg':
                s['bg_kind'] = 'field'
                fb = field_bg(os.path.join(wp['out'], 'bg_fallback', 'p%03d.jpg' % n),
                              s.get('style', 'tech_blue'), pg, s['role'], zone, motif=_motif)
                rec['field_motif'] = _motif
                if fb:
                    put_background(slide, fb, hbox)
                    rec['bg'] = fb
                    rec['notes'].append('素材只有 %dx%d，铺满 1080p 会糊：改用矢量色场，'
                                        '要这张图请重新出高清档（即梦选 1080p/2K）' % (dsize[0], dsize[1]))
                else:
                    rec['notes'].append('素材分辨率不足且色场生成失败')
            else:
                if s.get('asset_kind') == 'video_bg' and not rec.get('video'):
                    # 视频被豁免（带角标清不净 / 用户选静态）时的语料法兜底：本机 zoompan 把底图
                    # 做成满铺缓慢推镜 mp4（末帧做 poster），走 attach_video——与语料封面同构。
                    # 不再用 inject_emphasis_scale 打满铺图：那条私改动效在 PowerPoint 放映态被
                    # 渲染成灰底小缩略图（用户实测，SPEC §17.1）。
                    mp4, poster = push_in_video(
                        use, os.path.join(wp['out'], 'push_in', 'p%03d.mp4' % n),
                        os.path.join(wp['out'], 'push_in', 'p%03d_poster.jpg' % n))
                    if mp4:
                        attach_video(slide, mp4, poster or use, dur_ms=12000)
                        rec['video'] = mp4
                        rec['bg'] = 'video'
                        rec['bg_push_in'] = True
                        lay, hbox = 'full-bleed', [0.0, 0.0, 1.0, 1.0]
                        rec['layout'] = lay
                        zone = LAYOUT_ZONE[lay]['zone'] or s.get('reserved_zone') or zone_for(s['role'])
                        rec['notes'].append('无可用视频：本机 zoompan 生成 12s 满铺推镜 mp4'
                                            '（末帧 poster，语料法），报告按降级交付_无视频背景 如实标')
                    else:
                        put_background(slide, use, hbox,
                                       shape='diamond' if lay == 'split-centre-image' else 'rect')
                        rec['bg'] = use
                        rec['notes'].append('无可用视频且推镜视频生成失败：退回满铺静态底图（无动效）')
                else:
                    pic = put_background(slide, use, hbox,
                                         shape='diamond' if lay == 'split-centre-image' else 'rect')
                    rec['bg'] = use
                    if graded:
                        rec['notes'].append('实景已按全册色域调色+压暗（否则素材感与片子脱节）')
                    if s.get('duotone'):
                        if add_duotone(pic, pal_name):
                            rec['duotone'] = True
                            rec['notes'].append('双色调锁色 a:duotone（非破坏性：图片本体没动，'
                                                'PowerPoint 里可以单独撤掉）')
                        else:
                            rec['duotone_refused'] = True
                            rec['notes'].append(
                                u'色板 %s 没有实测过的 duotone 色值（语料 74 处全在蓝青族：'
                                u'0058AF/5BA8F1/016DFF/00FFFF）：不编色值，这页改判为压暗蒙版'
                                % pal_name)
        else:
            if lay in ('text-block', 'centred-statement'):
                # 原来这两类按"纯色底+文字"装配（照抄语料 114+6 页），但同一把尺量全量好示范册
                # 得到的**有图率中位是 1.0**——模板每页都有背景图形，纯色底这一维永远差一截。
                # 改铺矢量色场 JPG（不是假图，是本页数值/母题生成的背景），原型声明同步改
                # full-bleed，G17 量的与声明的一致。
                s['bg_kind'] = 'field'
                lay = 'full-bleed'
                hbox = [0.0, 0.0, 1.0, 1.0]
                zone = LAYOUT_ZONE['full-bleed']['zone'] or zone_for(s['role'])
                s['layout'] = lay
                rec['layout'] = lay
                fb = field_bg(os.path.join(wp['out'], 'bg_fallback', 'p%03d.jpg' % n),
                              s.get('style', 'tech_blue'), pg, s['role'], zone, motif=_motif)
                rec['field_motif'] = _motif
                if fb:
                    put_background(slide, fb, hbox)
                    rec['bg'] = fb
                    rec['notes'].append('纯色底改铺矢量色场：语料有图率中位 1.0（每页都有背景图形），'
                                        '原型按 full-bleed 复核')
                else:
                    from pptx.dml.color import RGBColor
                    slide.background.fill.solid()
                    slide.background.fill.fore_color.rgb = RGBColor.from_string(pal['page_bg'])
                    rec['bg_page'] = pal['page_bg']
                    rec['notes'].append('色场生成失败：退回色板底色 %s' % pal['page_bg'])
            else:
                s['bg_kind'] = 'field'
                fb = field_bg(os.path.join(wp['out'], 'bg_fallback', 'p%03d.jpg' % n),
                              s.get('style', 'tech_blue'), pg, s['role'], zone, motif=_motif)
                rec['field_motif'] = _motif
                if fb:
                    put_background(slide, fb, hbox)
                    rec['bg'] = fb
                    rec['notes'].append('无素材：用本页数值的矢量色场兜底（不是黑底，也不是假图）')
                else:
                    from pptx.dml.color import RGBColor
                    slide.background.fill.solid()
                    slide.background.fill.fore_color.rgb = RGBColor.from_string(pal['page_bg'])
                    rec['bg_page'] = pal['page_bg']
                    rec['notes'].append('无素材且色场生成失败：退回色板底色 %s' % pal['page_bg'])
        # scrim must darken the side the text is on; a fixed top-down gradient left bottom-third
        # captions sitting on bright pixels and made page 1's title collide with its subtitle
        # 只要真的有一张铺满画布的背景图，就必须压暗蒙版——原来只在"声明为 full-bleed"时压，
        # 于是分栏原型配上整页实景时白字直接落在亮图上（实测 21 框只有 1.64:1）
        _covers_bg = bool(hbox and hbox[2] >= 0.99 and hbox[3] >= 0.99 and rec.get('bg'))
        if lay == 'full-bleed' or _covers_bg:
            # 蒙版厚度不是拍脑袋：按"文字落点那块区域的实测亮度"反推需要压到多黑，
            # 让白字至少达到 WCAG 4.5:1（大字 3.0:1）。语料那 7864 次 lumMod 就是干这个的。
            _bg_src = None
            for _cand in (rec.get('bg'), find_gen(a.work, n, 'image'),
                          find_gen(a.work, n, 'first')):
                if _cand and _cand not in ('video',) and os.path.exists(str(_cand)):
                    _bg_src = str(_cand)
                    break
            _a = scrim_alpha_for(_bg_src, zone, CT.DARK_GROUND_INK['text'])
            # 实测按落点区中位亮度解出的 a 仍会让个别框落在更亮的像素上（G4 报 3.18–4.42:1）：
            # 整体再压 18 个点（封顶 88），中段仍留 a-45 给照片本身，不至于压成黑页。
            # 封面例外（SPEC §17.4）：语料封面图像明亮、文字落暗部，不加这 18 点，
            # 封面压暗必须 ≤ 内容页，否则把主体闷成黑页（用户实测开场太差劲）。
            if s['role'] != 'cover':
                _a = min(88, _a + 18)
            add_scrim(slide, alpha_pct=_a, top_dark=(zone[1] + zone[3] / 2.0) < 0.55,
                      both_ends=(lay == 'full-bleed'))
            if _a != s.get('scrim', 62):
                rec['scrim_pct'] = _a
                rec['notes'].append(u'蒙版压到 %d%%（按文字落点区实测亮度反推，白字要 ≥4.5:1）'
                                    % _a)
        else:
            # 分栏页整页铺色板底色：不加这层底，白色文字会落在 PPT 默认白底上（实测页 3 看不见字）
            from pptx.dml.color import RGBColor
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = RGBColor.from_string(pal['page_bg'])
            rec['bg_page'] = pal['page_bg']
            rec['notes'].append('分栏页不压蒙版，整页铺色板底色 %s：语料该原型压图率 %.1f%%'
                                '（文字根本不落在图上）' % (pal['page_bg'],
                                                            CT.LAYOUTS.get(lay, {}).get(
                                                                'over_pic_pct', 0.0)))
        # 动图叠在蒙版之上：语料里 GIF 是"元素动效"（水墨晕开/闪光划过），被蒙版压暗就白做了
        gif = find_gen(a.work, n, 'gif')
        if gif and os.path.exists(gif):
            gi = gif_info(gif)
            from pptx.util import Emu as _Emu
            box = s.get('gif_box') or [0.60, 0.20, 0.34, 0.44]
            slide.shapes.add_picture(gif, _Emu(int(box[0] * EMU_W)), _Emu(int(box[1] * EMU_H)),
                                     _Emu(int(box[2] * EMU_W)), _Emu(int(box[3] * EMU_H)))
            rec['gif'] = {'file': gif, 'frames': (gi[2] if gi else 0),
                          'w': (gi[0] if gi else 0), 'h': (gi[1] if gi else 0)}
            rec['notes'].append('动图 %dx%d / %s 帧（GIF 由播放器在放映时循环，不需要 p:timing）' % (
                (gi[0] if gi else 0), (gi[1] if gi else 0), (gi[2] if gi else 0)))
        rec['zone_used'] = [round(v, 4) for v in zone]
        # 满幅压字页与视频页被蒙版压暗了，必须用浅色字；分栏/纯色底页才用色板自己的落字色
        # 只要底图铺满整页（不管声明的是哪种原型），就得用深底字色，
        # 否则浅底图 + 深色正文实测 19 框只有 1.64:1，WCAG 4.5:1 一定不过
        _covers = (lay == 'full-bleed' or rec.get('bg') == 'video' or
                   (hbox and hbox[2] >= 0.99 and hbox[3] >= 0.99 and rec.get('bg')))
        ink_used = CT.DARK_GROUND_INK if _covers else pal['ink']
        rec['ink'] = ink_used['text']
        # 分栏页的文字栏必须停在图片左沿之前：语料 split 原型是"字压在色块上、图在另一半"，
        # 而 LAYOUT_ZONE 给的文字栏右沿（0.069+0.425=0.494）比图位左沿（0.425）还靠右 0.07，
        # v10 页133 实测第三根柱子的 '20%' 直接落在照片上（G17：40% 的字压图）。
        zone_txt = zone
        if lay.startswith('split') and hbox and hbox[0] > zone[0] + 0.12:
            _zw = min(zone[2], hbox[0] - zone[0] - 0.02)
            if _zw >= 0.18:
                rec['notes'].append(u'文字栏收到图片左沿（宽 %.3f→%.3f）：分栏页不许把数字压在照片上'
                                    % (zone[2], _zw))
                zone_txt = [zone[0], zone[1], _zw, zone[3]]
        rec['texts'] += place_text(slide, pg, s, zone_txt, rec, ink=ink_used)
        # 屏上字数预算（SPEC §13 完全同等级第一刀）：解释行移备注，slide 只留标题+大数字+图表标签
        if rec.get('display_mode') == 'refined' and s.get('layout') != 'table' \
                and not outline.get('verbatim'):
            _mv = cap_screen_chars(slide, TH['screen_chars_p50_max'])
            if _mv:
                rec['moved_to_notes'] = _mv
                rec['notes'].append('屏上只留 %d 字内（语料 p50=19）：%d 行解释移入演讲者备注，'
                                    '事实仍在（G24 计备注）' % (TH['screen_chars_p50_max'], _mv))
        recipe = (s.get('tech_recipe') or '').strip()
        if recipe == 'blip-duotone' and rec.get('duotone_refused'):
            # 声明的卡在当前色板下产不出来：改判成这页真的做了的手法，而不是留一个空头声明
            recipe = 'mask-dim' if lay == 'full-bleed' else ''
            s['tech_recipe'] = recipe
        if lay != 'full-bleed' and recipe == 'mask-dim' and not hbox:
            # 纯色底文字页：压暗蒙版与分栏都不成立（没有图可压、也没有栏位可分）
            recipe = 'text-animation-presets' if (rec.get('anim_shapes') or 0) else ''
            s['tech_recipe'] = recipe
            rec['notes'].append('这页是纯色底文字页（原型 %s）：没有图可压暗也没有栏位可分，'
                                '技法改判为 %s' % (lay, recipe or u'（不声明技法）'))
        elif lay != 'full-bleed' and recipe == 'mask-dim':
            # 压暗蒙版是给"字压在图上"用的；分栏页文字根本不落图上，改判图文重排原型
            recipe = 'split-reflow'
            s['tech_recipe'] = recipe
            rec['notes'].append('这一页不压蒙版：技法判给 split-reflow（实测 pic 框 %s，'
                                '语料该原型压图率 %.1f%%）' % (
                                    ('%.2f×%.2f' % (hbox[2], hbox[3])) if hbox else u'无图',
                                    CT.LAYOUTS.get(lay, {}).get('over_pic_pct', 0.0)))
        if recipe in ('video-fullbleed', 'boomerang-loop', 'gif-elastic') and not rec.get('video')                 and not rec.get('gif'):
            # 声明了整页视频/动图却根本没有产物可放（用户还没投放回收）：
            # 技法改判给这页真做了的事，不许留一个成品 XML 里数不到的空头声明
            # （v10 实测 24 页被 G6 点名"声明了技法卡却找不到写法"）
            _alt = ('mask-dim' if lay == 'full-bleed' else
                    ('text-animation-presets' if (rec.get('anim_shapes') or 0) else ''))
            rec['notes'].append(u'这页没有可放的视频/动图（产物未回收）：技法 %s 改判为 %s' % (
                recipe, _alt or u'（不声明技法）'))
            recipe = _alt
            s['tech_recipe'] = recipe
        rec['tech_recipe_final'] = recipe
        # giant-numeral 不是声明了就算数：这一页没真画出 ≥100pt 的数（标题里带两个数、
        # 或留白区放不下 110pt 的数），就把卡改成成品里真在的写法——G6 数的是 slide XML。
        if (recipe == 'giant-numeral'
                and int(rec.get('max_font_pt') or 0) * 100 < TH['giant_num_sz_min']):
            _alt2 = ('mask-dim' if rec.get('scrim_pct') else
                     ('text-animation-presets' if (rec.get('anim_shapes') or 0) else ''))
            recipe = _alt2
            rec['tech_recipe_final'] = recipe
            s['tech_recipe'] = recipe
            rec['notes'].append(u'技法卡改判：这页没画出 ≥%dpt 的大数字（成品最大字号 %spt），'
                                u'改记这页真有的写法 %s' % (
                                    int(TH['giant_num_sz_min'] / 100),
                                    rec.get('max_font_pt'), recipe or u'（不声明技法）'))
        if recipe == '3d-model-depth':
            anchor = None
            for sp in slide.shapes:
                if sp.name in ('hero_num', 'title', 'chapter_no'):
                    anchor = sp
                    break
            if anchor is not None:
                add_3d_panel(slide, anchor, style=s.get('style', 'tech_blue'),
                             **({'extrusion_h': int(s['extrusion_h'])}
                                if s.get('extrusion_h') else {}))
                rec['panel3d'] = True
                rec['notes'].append('立体展台（a:sp3d 挤出+棱台 / a:scene3d 三点光 / 反射），'
                                    '插在文字下层，正文仍可编辑')
            else:
                rec['panel3d'] = False
                rec['notes'].append('3d-model-depth 找不到 hero_num/title 锚点：这页没写立体，G6 会拦')
        if pg.get('table'):
            tz = [zone[0], min(0.86, zone[1] + 0.15), zone[2], max(0.10, zone[3] - 0.17)]
            n_cells = add_table(slide, pg['table'], tz, rec, s.get('style', 'tech_blue'),
                              pal=pal_name)
            rec['notes'].append('附表 %d 格已装成可编辑表格（不是图片）' % n_cells)
        # 表格页开场走棋盘/百叶——语料里 "PPT7×5神奇表格" 整册 p:checker。一页只容得下一个
        # p:transition，所以这些页不吃 morph：台账连续分页靠"表头每页重复"衔接，不靠平滑。
        tmotion = (recipe == 'table-motion' and bool(pg.get('table')))
        if tmotion:
            tk = s.get('transition') if s.get('transition') in TRANS_KINDS else 'checker'
            add_page_transition(slide, tk)
            rec['transition'] = tk
            rec['notes'].append('表格页转场 p:%s（%d ms，与 morph 互斥）' % (
                tk, TH['trans_dur_ms']))
        will_morph = (bool(s.get('morph_from_prev')) or (s.get('role') == 'divider' and n > 1))
        if will_morph and not tmotion:
            # SPEC §2：隔页必带 morph 或视差。光有文字淡入不算——隔页是章节切换，必须整页位移。
            ok_morph = share_anchor(prs, len(prs.slides))
            add_morph(slide, s.get('morph_dur_ms', 2000))
            rec['notes'].append('morph:' + ('anchor_shared' if ok_morph else 'NO_ANCHOR'))
            if not ok_morph:
                rec['notes'].append('MORPH_NO_ANCHOR')

        # 文字入场真的写进 OOXML（语料 entr:10 淡入最高频之一），逐条 120ms 递进。
        # 以前只有 morph 页算"有运动"，G6 却拿 shots.json 的计划字段报 100%——成品里一页不动。
        # 入场动画按语料实测 preset 走：默认淡入（entr:10:0，209 次 / 46 册），
        # shots 里写 "motion_preset": "wipe-left" 就换擦入（entr:22 四向合计 242 次，
        # 是语料第二高频入场）。motion 字段本身是给 agent 看的自由文本意图，不当 preset 解析。
        if rec.get('divider_anim'):
            # 数字过渡页（教程 3056573）：数字自左飞入、标题自右飞入，两者与上一动画同时。
            anim, used = 0, []
            _dmap = rec['divider_anim']
            for sh in slide.shapes:
                _pr = _dmap.get(sh.name)
                if _pr and (sh.has_text_frame or sh.name == 'divider_block'):
                    try:
                        got = inject_entrance(slide, sh.shape_id, preset=_pr,
                                              node_type='withEffect', dur_ms=700)
                        if got:
                            anim += 1
                            used.append(got)
                    except Exception as exc:
                        rec['notes'].append('过渡页飞入写入失败（%s：%s）' % (sh.name, exc))
        else:
            preset = (s.get('motion_preset') or 'fade').strip()
            if preset not in CT.ANIM or CT.ANIM[preset]['triple'][0] != 'entr':
                rec['notes'].append('motion_preset="%s" 不在语料实测入场表里（可用：%s）：按淡入装配'
                                    % (preset, ','.join(sorted(
                                        k for k, v in CT.ANIM.items() if v['triple'][0] == 'entr'))))
                preset = 'fade'
            steps = CT.TIMING['stagger_ms']
            step_ms = steps[1] - steps[0] if len(steps) > 1 else 500
            anim, used = 0, []
            for k_a, sid in enumerate((rec.get('anim_spids') or [])[:6]):
                d = steps[k_a] if k_a < len(steps) else steps[-1] + step_ms * (k_a - len(steps) + 1)
                try:
                    got = inject_entrance(slide, sid, delay_ms=d,
                                          dur_ms=int(s.get('motion_dur_ms',
                                                           CT.TIMING['default_dur_ms'])),
                                          preset=preset,
                                          node_type=('withEffect' if s['role'] in ('cover', 'closing')
                                                     else 'afterEffect'))
                    if got:
                        anim += 1
                        used.append(got)
                except Exception as exc:
                    rec['notes'].append('入场动效写入失败（形状 %s：%s）' % (sid, exc))
        xpreset = (s.get('motion_out') or '').strip()
        if xpreset in CT.ANIM and CT.ANIM[xpreset]['triple'][0] == 'exit' and \
                (rec.get('anim_spids') or []):
            try:
                gx = inject_exit(slide, rec['anim_spids'][0], preset=xpreset,
                                 delay_ms=int(s.get('motion_out_delay_ms', steps[1])),
                                 dur_ms=int(s.get('motion_dur_ms', CT.TIMING['default_dur_ms'])))
                if gx:
                    used.append(gx)
                    rec['notes'].append('出场 %s（语料 exit 108 次全是 animEffect(out)+set->hidden）'
                                        % xpreset)
            except Exception as exc:
                rec['notes'].append('出场动效写入失败：%s' % exc)
        rec['anim_shapes'] = anim
        rec['anim_presets'] = used
        if recipe == 'emphasis-scale':
            sid = (rec.get('anim_spids') or [None])[0]
            if sid and inject_emphasis_scale(slide, sid, by_pct=s.get('scale_pct', 15),
                                             dur_ms=int(s.get('scale_dur_ms', 1000))):
                rec['emphasis_scale'] = True
                rec['notes'].append('放大强调 p:animScale（emph presetID=6，by %s%%）'
                                    % s.get('scale_pct', 15))
            else:
                rec['notes'].append('emphasis-scale 找不到可放大的锚点形状：没写 animScale，G6 会拦')
        built.append(rec)
    n_src = add_source_pages(prs, man, shots)
    if n_src:
        jw(os.path.join(wp['out'], 'sources_used.json'), n_src)
    out = os.path.join(wp['out'], 'deck.pptx')
    prs.save(out)
    # 色板写进主题：换一版配色不用逐形状重刷，G16 也才有"主题主色==色板主色"可对
    ok_th, th_msg = apply_theme(out, pal_name, pal)
    for b in built:
        b.setdefault('palette', pal_name)
    # build_log.json 的形状不许顺手改：G3/G6/G11/G13 六处消费方都按 list 读（改契约会全线漏读）
    jw(os.path.join(wp['out'], 'build_log.json'), built)
    jw(os.path.join(wp['out'], 'build_meta.json'),
       {'palette': pal_name, 'theme_written': ok_th, 'theme_msg': th_msg,
        'style': pal_style, 'layouts': {b['page']: b.get('layout') for b in built}})
    print("build 完成：%d 页 -> %s" % (len(built), out))
    for b in built:
        if b['notes']:
            print("  页%-3d %s" % (b['page'], '；'.join(b['notes'])))


# FIELD_PAL 不再是拍脑袋的三元组：(深底, 中层, 高光) 全部来自 kb/color_system.json 的实测色，
# 逐格由 check_docs.py 对账（G16 的允许色集合同一张表）。
FIELD_PAL = {k: (v['deep'], v['mid'], v['hi']) for k, v in CT.PALETTES.items()}


def magnitudes(pg):
    """The page's real quantities, as (value, unit), for the colour-field motif.

    A bare four-digit year is a label, not a magnitude: plotting 2026 next to 41.2 made the ghost
    bars read as a broken chart, so unit-less 1900-2100 integers are dropped here.
    """
    out = []
    for v, u in NUM_UNIT.findall(' '.join([pg.get('headline') or ''] + list(pg.get('body') or []))):
        if not v:
            continue
        f = float(v.replace(',', ''))
        unit = (u or '').replace('％', '%')
        if not 0 < f <= 1e9:
            continue
        if unit == '' and '.' not in v and 1900 <= f <= 2100:
            continue
        out.append((f, unit))
    return out


def same_unit_groups(raw):
    """Group numbers by unit so a colour field never plots 8742 (万元) next to 41.2 (%) —
    that read as a broken chart, not as design."""
    groups = {}
    for val, unit in raw:
        groups.setdefault(unit or 'num', []).append(val)
    return {u: v for u, v in groups.items() if len(v) >= 3}


NUM_UNIT = re.compile(r'(\d[\d,]*\.?\d*)\s*(万元|亿元|万|亿|元|家|例|台|人|%|％)?')

# 大数字（giant-numeral 卡的判据）：place_text 用它决定这页要不要 150pt 的 hero_num，
# styles.py 用同一个正则决定该页声明哪张卡——两处必须一致，否则 G6 会说"声明了卡却数不到 token"。
HERO_BIG = re.compile(r'(\d[\d,\.]*\s*[%％]|1[6-9]\d{2}|[2-9]\d{3}|[\d,\.]+\s*(?:万|亿)?'
                      r'\s*(?:元|台|例|人|家))')
# 放大数字后面该跟着的量词：光一个 '412' 挂在 110pt 的位置，屏幕上就是"412 什么？"
# （实测拆页后那页只剩 412 与 96 两个裸数字）。place_text 用它把单位捡回大数字那一行。
HERO_UNIT = u'(?:万元|亿元|万|亿|元|台|例|人|次|天|店|个|倍|家)'


def _dim_for_ink(rgb, cap_lum=0.14):
    l = rel_lum(tuple(int(x) for x in rgb)) or 1e-6
    if l <= cap_lum:
        return tuple(int(x) for x in rgb)
    k = cap_lum / l
    return tuple(max(0, int(x * k)) for x in rgb)


def field_bg(dest, style, pg, role, zone=None, motif=0):
    """A page with no asset must not be a black rectangle. Draw a gradient plus a motif built
    from that page's own numbers, and keep the motif out of the reserved text area.

    motif 由页标题哈希派生（0 同心圆居中 / 1 同心圆偏右上 / 2 点阵），让连排的无素材页
    也不是同一张脸——G20 的连排判据按"同母题"计，换母题即断连排。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    W, H = 1920, 1080
    c0, c1, acc = FIELD_PAL.get(style, FIELD_PAL['tech_blue'])
    # 白字压亮底必不过 WCAG（实测 26 框只有 1.64:1，需 4.5:1）：矢量色场先把自己压暗到语料那种深底
    # —— 不靠蒙版赌一把
    def _dark(c):
        lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        if lum <= 62.0:
            return tuple(int(x) for x in c)
        k = 62.0 / (lum or 1.0)
        return tuple(int(x * k * 0.86) for x in c)
    c0, c1 = _dark(c0), _dark(c1)
    im = Image.new('RGB', (W, H))
    dr = ImageDraw.Draw(im)
    for y in range(H):
        t = y / float(H)
        dr.line([(0, y), (W, y)], fill=tuple(int(c0[i] * (1 - t) + c1[i] * t) for i in range(3)))
    zx, zy, zw, zh = zone or [0.06, 0.16, 0.88, 0.70]
    txt_top = int(zy * H)
    txt_bot = int((zy + zh) * H)
    txt_l, txt_r = int(zx * W), int((zx + zw) * W)

    # 母题（青色柱/同心圆）绝不能比白字还亮：实测页 22 的白色正文压在矢量色场自己画的
    # 亮青条上，G4 量到 1.64:1。把母题亮度压到 ≤0.14，形状还在、字还读得清。
    # 母题要"看得见但不抢字"：压到 0.14 时画出来的柱子比底还暗，
    # 逐页目检看到的是三个没有字的黑方块（实测页 18 底部），那是坏版式不是克制。
    # 改成按落点区实测亮度反推：保证母题与底的对比 ≥1.9 倍，字仍然压在干净区域。
    acc = _dim_for_ink(acc, 0.22)
    raw = magnitudes(pg)
    groups = same_unit_groups(raw)
    # 底图不画柱子：色场的"母题"以前按本页数值画一组方块，而这一页的内容层
    # 本来就会把这些数画成大数字/条形/筹码（提炼册）。两套图形叠在一起，实测渲染出来
    # 就是页底一排没有字的黑方块（页 3/10/18/20/21）——语料的底图只做质感（同心圆轨道），
    # 数据图形只归内容层。
    cx, cy = (int(W * 0.60), int(H * 0.54)) if motif != 1 else (int(W * 0.82), int(H * 0.20))
    if motif == 2:
        for gy in range(70, H - 40, 96):
            for gx in range(70, W - 40, 96):
                dr.ellipse([gx, gy, gx + 7, gy + 7], fill=(acc[0], acc[1], acc[2]))
    else:
        for r in range(6 if motif == 0 else 4):
            dr.ellipse([cx - r * 210, cy - r * 105, cx + r * 210, cy + r * 105],
                       outline=(acc[0], acc[1], acc[2]), width=3)
    dr.line([(int(W * 0.06), int(H * 0.86)), (int(W * 0.94), int(H * 0.86))],
            fill=(acc[0], acc[1], acc[2]), width=2)
    # 空渐变页看着像"没做完"（用户第七/八轮都点了）：加两条斜向光带 + 把本页最大的数
    # 做成超大幽灵字压右半——模板册的矢量页就是"渐变+斜切光带+大数字母题"，不是一圈同心圆了事。
    try:
        from PIL import ImageFont
        ov2 = Image.new('L', (W, H), 0)
        d2 = ImageDraw.Draw(ov2)
        d2.polygon([(int(W * 0.62), 0), (int(W * 0.78), 0), (int(W * 0.40), H),
                    (int(W * 0.24), H)], fill=26)
        d2.polygon([(int(W * 0.86), 0), (int(W * 0.94), 0), (int(W * 0.56), H),
                    (int(W * 0.48), H)], fill=18)
        if raw:
            _bv, _bu = max(raw, key=lambda t: float(str(t[0]).replace(',', '') or 0))
            _fnt = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 400)
            d2.text((int(W * 0.72), int(H * 0.52)), str(_bv), fill=30, font=_fnt, anchor='mm')
        ghost2 = Image.new('RGB', (W, H), (acc[0], acc[1], acc[2]))
        im = Image.composite(ghost2, im, ov2)
    except Exception:
        pass
    ch_no = pg.get('chapter')
    if role == 'divider' and ch_no:
        try:
            from PIL import ImageFont
            fnt = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 620)
        except Exception:
            fnt = None
        if fnt is not None:
            tmp = Image.new('L', (W, H), 0)
            ImageDraw.Draw(tmp).text((int(W * 0.72), int(H * 0.46)), '%02d' % int(ch_no),
                                     fill=255, font=fnt, anchor='mm')
            ghost = Image.new('RGB', (W, H), (acc[0], acc[1], acc[2]))
            im = Image.composite(ghost, im, tmp.point(lambda v: int(v * 0.16)))
    ov = Image.new('L', (W, H), 0)
    od = ImageDraw.Draw(ov)
    for y in range(H):
        od.line([(0, y), (W, y)], fill=int(120 * (1 - abs(y / H - 0.72) * 1.6)))
    dark = Image.new('RGB', (W, H), (0, 0, 0))
    im = Image.composite(dark, im, ov.point(lambda v: max(0, v - 40)))
    im.save(dest, 'JPEG', quality=86)
    return dest


def grade_asset(src, dest, style, strength=0.35):
    """Real photos rarely match the deck palette. Push them toward it and darken, so a magenta
    neon shot and a blue tech shot stop reading as two different decks."""
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return None
    c0, c1, acc = FIELD_PAL.get(style, FIELD_PAL['tech_blue'])
    try:
        im = Image.open(src).convert('RGB')
    except Exception:
        return None
    im.thumbnail((2400, 2400))
    a = np.asarray(im).astype(np.float32)
    target = np.array(c1, dtype=np.float32)
    a = a * (1 - strength) + (a.mean(axis=2, keepdims=True) * 0.75 + target * 0.25) * strength
    a = np.clip(a * 0.78, 0, 255).astype(np.uint8)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    Image.fromarray(a).save(dest, 'JPEG', quality=88)
    return dest


def add_source_pages(prs, man, shots):
    """CC BY / BY-SA require attribution. Emit a real appendix page per 8 sources so the deck
    can be shown externally without breaking the licence."""
    need = [m for m in man.values() if re.search(r'by[- ]?sa|by[- ]?4|attribution',
                                                 str(m.get('license', '')), re.I)]
    if not need:
        return 0
    need.sort(key=lambda m: m['page'])
    per = 8
    pages = [need[i:i + per] for i in range(0, len(need), per)]
    for k, grp in enumerate(pages, 1):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        from pptx.dml.color import RGBColor
        # 署名页也要有背景图形：语料好示范册全量的同口径有图率中位是 1.0，纯色底会把这一维拉低
        import tempfile
        _fb = field_bg(os.path.join(tempfile.gettempdir(), 'caofeng_srcattr_%d.jpg' % k),
                       'tech_blue', {'page': 0, 'headline': u'图片来源与许可署名'},
                       'statement', [0.06, 0.16, 0.88, 0.78], motif='rings')
        if _fb:
            add_fullbleed_picture(slide, _fb)
        else:
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = RGBColor(0x0B, 0x09, 0x0E)
        add_text(slide, u'图片来源 %d/%d（署名要求来自 CC BY 系列许可）' % (k, len(pages)),
                 [0.06, 0.06, 0.88, 0.08], 20, bold=True, align='l', name='title')
        lines = []
        for m in grp:
            # CC BY / BY-SA attribution needs the AUTHOR, not just the file name; and slicing the
            # title in the middle produced unreadable credits the last time this was audited.
            title = (m.get('source_page') or os.path.basename(m.get('file', '')) or '').strip()
            artist = (m.get('artist') or '').strip() or '作者未知（许可要求署名，须回源页补）'
            lines.append(u'第%s页｜%s｜作者：%s｜%s｜%s' % (
                m['page'], title, artist, m.get('license') or '许可未知', str(m.get('url', ''))))
        add_text(slide, lines, [0.06, 0.16, 0.88, 0.78], 10, align='l', color='C9C4BC')
    return len(need)


def pptx_boxes(pptx_path):
    """[(slide_index_1based, shape_name, normalized_text)] for every text-bearing box and table cell.

    G1 needs the *box*, not the page: place_text names the editorial boxes ('title', 'section',
    'chapter_no', 'hero_num') and leaves body boxes unnamed, which is what lets the gate allow a
    rewritten headline while still demanding that every body line trace back to the Word file.
    """
    import zipfile
    from xml.etree import ElementTree as ET
    A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
    P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
    try:
        z = zipfile.ZipFile(pptx_path)
    except Exception:
        return []
    out = []
    try:
        names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
        names.sort(key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
        for si, n in enumerate(names, 1):
            try:
                x = ET.fromstring(z.read(n))
            except Exception:
                continue
            for sp in x.iter(P + 'sp'):
                nm = (sp.find('.//' + P + 'nvSpPr/' + P + 'cNvPr').get('name')
                      if sp.find('.//' + P + 'nvSpPr/' + P + 'cNvPr') is not None else '')
                txt = ''.join((t.text or '') for t in sp.iter(A + 't'))
                if txt.strip():
                    out.append((si, nm or '', txt, norm_content(txt)))
            for gf in x.iter(P + 'graphicFrame'):
                for tc in gf.iter(A + 'tc'):
                    txt = ''.join((t.text or '') for t in tc.iter(A + 't'))
                    if txt.strip():
                        out.append((si, 'table_cell', txt, norm_content(txt)))
    finally:
        z.close()
    return out


def norm_content(s):
    """Whitespace-free, punctuation-free comparison key. A sentence the deck splits into a
    headline box and a remainder box must still compare equal to the Word source sentence."""
    return re.sub(u'[\\s，。、；：,;:!！?？“”"\'（）()《》【】\\-—_·\\[\\]]+', '', s or '')


def pptx_slide_texts(pptx_path):
    """One normalized string per slide: text boxes joined in top-to-bottom, left-to-right order.

    Gates that measure the deliverable must read this, not outline/shots JSON. Ordering matters:
    place_text cuts the headline out of body[0] and prints the remainder below it, so a source
    sentence lives in two boxes. Reading-order concatenation lets G13 demand the whole sentence
    instead of silently accepting half of it.
    """
    import zipfile
    from xml.etree import ElementTree as ET
    A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
    P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
    try:
        z = zipfile.ZipFile(pptx_path)
    except Exception:
        return []
    slides = []
    try:
        names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
        names.sort(key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
        for n in names:
            try:
                x = ET.fromstring(z.read(n))
            except Exception:
                continue
            boxes = []
            for sp in x.iter(P + 'sp'):
                off = sp.find('.//' + A + 'off')
                if off is None:
                    top = left = 10 ** 12
                else:
                    top, left = int(off.get('y') or 0), int(off.get('x') or 0)
                txt = ''.join((t.text or '') for t in sp.iter(A + 't'))
                if txt.strip():
                    boxes.append((top, left, norm_content(txt)))
            # a table is a graphicFrame, not a shape: without this the 16 cells of a 台账 read as
            # "no text on the page" and G13 would flag a table that really is on the slide
            for gf in x.iter(P + 'graphicFrame'):
                off = gf.find('.//' + A + 'off')
                top = int(off.get('y') or 0) if off is not None else 10 ** 12
                left = int(off.get('x') or 0) if off is not None else 10 ** 12
                txt = ''.join((t.text or '') for t in gf.iter(A + 't'))
                if txt.strip():
                    boxes.append((top, left, norm_content(txt)))
            boxes.sort()
            slides.append(''.join(b[2] for b in boxes))
    finally:
        z.close()
    return slides


def pptx_table_texts(pptx_path):
    """每页 <a:tbl> 内的文字（表格区），页序与 pptx_slide_texts 对齐。

    表格外还有结论行和大数字，它们会复述单元格里的值；整页文字当核对域的话，
    抠掉表里那一格依然"能找到"，逐格核对就形同虚设。"""
    out = []
    if not pptx_path or not os.path.exists(pptx_path):
        return out
    import zipfile as _zl
    from lxml import etree as _ET
    z = _zl.ZipFile(pptx_path)
    names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    names.sort(key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
    for n in names:
        try:
            root = _ET.fromstring(z.read(n))
        except Exception:
            out.append('')
            continue
        buf = []
        for tbl in root.iter('{%s}tbl' % A):
            for t in tbl.iter('{%s}t' % A):
                buf.append(t.text or '')
            buf.append(' ')
        # 归一化必须与 norm_content 同一个口径，否则"表里明明有这一格"会因为标点差异判成丢格
        out.append(norm_content(''.join(buf)))
    z.close()
    return out


def pptx_all_text(pptx_path):
    """All <a:t> runs in the built deck, whitespace-stripped and concatenated (report dump)."""
    import zipfile
    from xml.etree import ElementTree as ET
    out = []
    try:
        z = zipfile.ZipFile(pptx_path)
    except Exception:
        return ''
    try:
        for n in z.namelist():
            if re.match(r'ppt/(slides|notesSlides)/[^/]+\.xml$', n):
                try:
                    x = ET.fromstring(z.read(n))
                except Exception:
                    continue
                for t in x.iter('{http://schemas.openxmlformats.org/drawingml/2006/main}t'):
                    if t.text:
                        out.append(re.sub(r'\s+', '', t.text))
    finally:
        z.close()
    return '\n'.join(out)


def share_anchor(prs, cur_no):
    """Morph only animates shapes both slides carry under the same name. Put an invisible 1x1
    anchor on the previous slide and on the current one so the camera move is real.
    cur_no is the 1-based index of the slide just added -- getting this off by one made 3 of 5
    morph pages claim `anchor_shared` while nothing was actually paired."""
    n = len(prs.slides)
    if cur_no is None:
        cur_no = n
    if cur_no < 2 or cur_no > n:
        return False
    prev = prs.slides[cur_no - 2]
    cur = prs.slides[cur_no - 1]
    ensure_anchor(prev)
    ensure_anchor(cur)
    return True


def find_gen(w, page, kind):
    d = os.path.join(w, 'gen', 'p%03d' % page)
    if not os.path.isdir(d):
        return None
    EXT_BY_KIND = {'video': ('.mp4', '.mov', '.m4v'), 'gif': ('.gif', '.apng', '.webp')}
    exts = EXT_BY_KIND.get(kind, ('.png', '.jpg', '.jpeg', '.webp'))
    for ext in exts:
        fp = os.path.join(d, '%s%s' % (kind, ext))
        if os.path.exists(fp):
            return fp
    for f in sorted(os.listdir(d)):
        if f.startswith(kind) and os.path.splitext(f)[1].lower() in exts:
            return os.path.join(d, f)
    if kind == 'gif':
        # 即梦/豆包存下来的动图多半叫"水墨晕开.gif"这类名字，扩展名对动图是无歧义的
        for f in sorted(os.listdir(d)):
            if os.path.splitext(f)[1].lower() in ('.gif', '.apng'):
                return os.path.join(d, f)
    return None



def gif_info(path):
    """(width, height, frames) of an animated image as it really is on disk.

    动图 is a first-class corpus technique (162 GIFs across 134 of 172 templates, minimum 24
    frames), and a single-frame GIF looks identical in PowerPoint until someone presses play.
    """
    try:
        from PIL import Image
        im = Image.open(path)
        return int(im.size[0]), int(im.size[1]), int(getattr(im, 'n_frames', 1) or 1)
    except Exception:
        return None


def make_poster(mp4, dest):
    """First frame of the video as the poster, so the slide never shows a black box."""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', mp4,
                        '-frames:v', '1', dest], capture_output=True)
        return dest if os.path.exists(dest) else None
    except Exception:
        return None


def push_in_video(img, dest_mp4, dest_poster, dur_s=12, zoom_pct=6, fps=24):
    """视频被豁免时的语料法兜底：本机 ffmpeg zoompan 把底图做成满铺缓慢推镜 mp4，
    末帧做 poster（教程原话"选择最后一帧作为素材默认显示的封面"），再走 attach_video。
    取代 inject_emphasis_scale 打在满铺图上的私改动效——那条在 PowerPoint 放映态被渲染成
    灰底小缩略图（SPEC §17.1）。返回 (mp4, poster) 或 (None, None)。"""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        os.makedirs(os.path.dirname(dest_mp4), exist_ok=True)
        # 底图不变时复用已生成的推镜视频（复装配不必每页重跑 ffmpeg）
        if (os.path.exists(dest_mp4) and os.path.getsize(dest_mp4) > 65536
                and os.path.exists(dest_poster)):
            return dest_mp4, dest_poster
        n = max(2, int(dur_s * fps))
        zmax = 1.0 + zoom_pct / 100.0
        # 先放大到 4K 再 zoompan，避免逐帧裁剪抖动；输出 1080p 满铺 16:9
        vf = ("scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,"
              "zoompan=z='1+%f*on/%d':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              ":d=%d:s=1920x1080:fps=%d" % (zoom_pct / 100.0, n, n, fps))
        r = subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', img,
                            '-vf', vf, '-frames:v', str(n), '-c:v', 'libx264',
                            '-pix_fmt', 'yuv420p', '-crf', '20', '-preset', 'fast',
                            '-movflags', '+faststart', dest_mp4], capture_output=True)
        if not os.path.exists(dest_mp4) or os.path.getsize(dest_mp4) < 65536:
            return None, None
        # 末帧做 poster（与语料一致），不是首帧
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', dest_mp4,
                        '-vf', 'select=eq(n\\,%d)' % (n - 1), '-frames:v', '1',
                        dest_poster], capture_output=True)
        if not os.path.exists(dest_poster):
            make_poster(dest_mp4, dest_poster)
        return dest_mp4, (dest_poster if os.path.exists(dest_poster) else None)
    except Exception:
        return None, None


def est_lines(text, pt, width_frac):
    """CJK glyphs are roughly square, so a line fits (box_width_in * 72 / pt) characters.
    Without this the 60pt cover title wrapped to two lines and the second line collided with
    the 18pt subtitle -- a real defect found by looking at the rendered PNG."""
    box_in = width_frac * (EMU_W / 914400.0)
    per = max(4, int(box_in * 72.0 / pt))
    n = 0
    for seg in (text if isinstance(text, (list, tuple)) else [text]):
        # 与 fit_pt/orphan_tail 同一把尺子（DL.em_width 已按 WPS 真渲染标定过）：
        # 按"一个字算一个 em"会在边界上误判，实测 17 字 24pt 标题正好算成一行、渲染却折了。
        n += max(1, int(-(-DL.em_width(seg) // per)))
    return n


def line_height_frac(pt):
    return (pt * 1.35) / (EMU_H / 914400.0 * 72.0)


def pick_pt(text, width_frac, want, min_pt):
    """Shrink display type until it fits the reserved width instead of wrapping onto the caption."""
    pt = want
    while pt > min_pt and est_lines(text, pt, width_frac) > 1:
        pt -= 2
    return pt


TABLE_ACCENT = {'tech_blue': (60, 190, 255), 'ink_wash': (200, 178, 130),
                'photo_real': (226, 176, 96)}


def orphan_tail(text, pt, width_frac):
    """孤字尾行：末行只剩 1–2 个字。技法卡早就写着"G8 孤字"，但闸一直没实现——
    e6 封面实测到 34pt 标题折行后第二行只剩"表）"，机器全绿而画面难看。

    宽度按字符种类加权模拟换行（CJK 与全角标点 1 em、半角 0.55、空格 0.35）。
    不能沿用 est_lines 的"全按一个 em 算"：那样 2026/括号里的数字会被算宽，
    实测就把这页漏掉了——闸必须量到真实画面，不是量自己的近似。
    """
    box_in = width_frac * (EMU_W / 914400.0)
    per = max(4.0, box_in * 72.0 / pt)

    def w_of(ch):
        return DL.char_em_width(ch)      # 与 data_layout.fit_pt 同一把尺子

    lines, tail_chars = 1, 0
    for seg in (text if isinstance(text, (list, tuple)) else [text]):
        acc = 0.0
        for ch in (seg or ''):
            w = w_of(ch)
            if acc + w > per:
                lines += 1
                tail_chars = 0
                acc = w
            else:
                acc += w
            tail_chars += 1
    return lines, tail_chars


def balanced_break(text, pt, width_frac, max_orphan=None):
    """长标题折行时断在分句处，别留孤字尾行。

    语料与 human-doc 的同一条经验：字没被裁、读起来是碎的，机器只看几何就永远放行。
    返回行列表（add_text 认列表），断不出标点时按宽度硬切成两条均衡的行。
    """
    limit = max_orphan if max_orphan is not None else TH['orphan_tail_max_chars']
    lines, tail = orphan_tail(text, pt, width_frac)
    if lines < 2 or tail > limit:
        return [text]
    box_in = width_frac * (EMU_W / 914400.0)
    per = max(4.0, box_in * 72.0 / pt)

    def w_of(ch):
        return DL.char_em_width(ch)

    cut = None
    for punct in (u'，', u'、', u'：', u' ', u'（'):
        k = text.rfind(punct, 0, len(text) - limit)
        while k > 0:
            first = sum(w_of(ch) for ch in text[:k + 1])
            rest = sum(w_of(ch) for ch in text[k + 1:])
            # 第一行也不能太短：实测过把"2026"单独甩在首行，比硬切更难看
            if 0.45 * per <= first <= per and rest <= per and len(text) - (k + 1) > limit:
                cut = k + 1
                break
            k = text.rfind(punct, 0, k)
        if cut:
            break
    if not cut:                      # 没标点可断：按宽度中点硬切，保证两行都不孤字
        acc, i = 0.0, 0
        half = per * 0.62
        for ch in text:
            acc += w_of(ch)
            i += 1
            if acc >= half:
                break
        cut = i if 0 < i < len(text) - limit else None
    if not cut:
        return [text]
    # 行尾不留开括号/引号（"…方案（" / "含附表）" 这种断法视觉上像被截断）：
    # 能把左括号挪到下一行开头就挪。
    if text[cut - 1] in u'（《“【([':
        alt = cut - 1
        first = sum(w_of(ch) for ch in text[:alt])
        rest = sum(w_of(ch) for ch in text[alt:])
        if 0.45 * per <= first and rest <= per and len(text) - alt > limit:
            cut = alt
    return [text[:cut].rstrip(), text[cut:].lstrip()]


NUMCOL_RE = re.compile(r'^\s*[-+]?[\d,.]+\s*(?:%|％|万元|亿元|万|亿|元|台|例|人|次|天|城|店|个|倍|:1|：1)?\s*$')

TBL_BODY_PT = 16
TBL_HEAD_PT = 20
TBL_LINE_IN = TBL_BODY_PT * 1.35 / 72.0   # 一行 16pt 正文占多高（与 line_height_frac 同口径）


def row_line_need(rows, col_w_frac, pt=TBL_BODY_PT):
    """这一行要折成几行字：按该行各列文字的换行数取最大值（列宽真值，不是猜的）。

    G25 的"行高按内容"和装配侧的行高摊法共用这一个函数——两边各自再算一遍换行，
    就会出现"布局按 A 尺、验收按 B 尺"的老问题（同一把尺子只能有一个定义点）。
    """
    need = []
    for r in rows:
        n = 1
        for j, v in enumerate(r or []):
            if j < len(col_w_frac) and v:
                n = max(n, est_lines(u'%s' % v, pt, col_w_frac[j]))
        need.append(n)
    return need


def row_heights(rows, col_w_frac, budget_in, pt=TBL_BODY_PT):
    """把正文行的总高度预算按"这一行要几行字"摊给每一行，而不是给每行发同一个数。

    行高在 OOXML 里是**下限**：长句行给的高度不够时，渲染器会自己把行撑高、把后面的行
    顶出表框（实测 4 行台账里那句 40 字说明就这么把结论行压在表格上）；反过来两字行摊
    1.10 英寸就是一整条空带（SPEC §7 G25 拦的就是这种平均分）。
    总和保持等于预算，所以 _true_h（行高之和）与表外结论的落点都不变。
    """
    need = row_line_need(rows, col_w_frac, pt)
    n = len(need)
    if not n:
        return []
    budget_in = max(0.0, float(budget_in))
    floor = min(TBL_LINE_IN, budget_in / n)
    tot = float(sum(need)) or 1.0
    h = [budget_in * x / tot for x in need]
    deficit = sum(max(0.0, floor - x) for x in h)
    h = [max(floor, x) for x in h]
    if deficit > 1e-9:
        # 从最富余（需求量最大）的行按比例扣回来，扣完仍不低于一行高
        give = sum(max(0.0, x - floor) for x in h) or 1.0
        cut = min(deficit, give)
        h = [x - cut * (x - floor) / give if x > floor else x for x in h]
        over = sum(h) - budget_in
        if over > 1e-9:
            h = [max(floor, x - over * (x - floor) / (sum(h) - floor * n or 1.0)) for x in h]
    return [int(x * 914400) for x in h]



def fit_text_lines(text, pt, width_frac):
    """让"末行只剩孤字"改掉：先断行，断完仍孤字就降字号让它整行放下。返回 (lines, pt)。

    为什么还要这一层：旧写法只在 len(text)>24 时才 balanced_break，20 字上下的短句
    直接交给渲染器折行，实测留下 '平均'、'%'、'万元' 三种尾行（G8 报 20 处）。
    判据与 G8 用的是同一个 orphan_tail，不会再出现"布局算的"和"闸门量的"两套口径。
    """
    text = text if isinstance(text, str) else u' '.join(text or [])
    if not text:
        return [text], pt
    lim = TH['orphan_tail_max_chars']

    def bad(p, t=None):
        nl, tl = orphan_tail(t if t is None else text, p, width_frac)
        return nl >= 2 and 0 < tl <= lim

    if not bad(pt):
        return [text], pt
    bb = balanced_break(text, pt, width_frac, max_orphan=lim)
    if len(bb) >= 2 and not any(bad(pt, x) for x in bb):
        return bb, pt
    for cand in (pt - 2, pt - 3, pt - 4, pt - 5):
        if cand >= 12 and not bad(cand):
            return [text], cand
    return (bb or [text]), pt


def add_table(slide, tbl, zone, rec, style='tech_blue', pal=None):
    """Render a Word table as a real, editable pptx table inside the reserved area.

    Word 台账/巡检表 rows are content: putting them in an image, or dropping them, both fail the
    "everything in the document reaches the deck" rule. Column widths follow the longest cell so a
    城市/潜在门店/已签约 grid does not render three columns of equal width with wrapped numbers.
    """
    from pptx.util import Emu, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    _pn, _pp = palette_for(style, pal)
    tcol = _pp['table']
    TBL_RGB = {k: RGBColor.from_string(v) for k, v in tcol.items()}
    header = [c for c in (tbl.get('header') or [])]
    rows = tbl.get('rows') or []
    grid = ([header] if any(header) else []) + rows
    if not grid:
        return 0
    ncol = max(len(r) for r in grid)
    nrow = len(grid)
    weight = []
    for j in range(ncol):
        longest = max([len((r[j] if j < len(r) else '') or '') for r in grid] + [1])
        weight.append(min(6.0, max(1.2, longest / 2.2 + 1.0)))
    tot = float(sum(weight)) or 1.0
    # 行高必须按内容来：python-pptx 会把 graphicFrame 的高度平均分给每一行，4 行的台账被撑成
    # 4 条约 160px 的空带，看着就像排坏了（渲染拼图实测）。显式设行高并把框收到实际高度。
    # 语料台账行高中位 1.10 英寸、表头 20pt/正文 16pt、铺满页宽（kb/table_design.json）；
    # 旧写法 0.31 英寸行高把 6 行表缩成左上角 1.9 英寸高的小块，下面大半页全空——
    # 用户逐页目检直接否掉的就是这种"表格缩在角落"。行高改为按留白区高度摊满。
    head_h = int(0.55 * 914400)
    _head = bool(any(header))
    _avail_in = max(0.12, zone[3] - (0.095 if (tbl.get('conclusion') or '').strip() else 0.0)) \
        * (EMU_H / 914400.0)
    body_h = int(min(1.10, max(0.45, (_avail_in - 0.55) / max(1, len(rows)))) * 914400)
    # 这一份预算不再按行数平分：按"这一行要折成几行字"摊给各行（G25 承诺的"行高按内容"）。
    # 列宽用真值 weight 折算，换行估算走 row_line_need——闸门与装配同一把尺，不各算一遍。
    _colw = [zone[2] * (w / tot) for w in weight]
    _bhs = row_heights(rows, _colw, body_h * len(rows) / 914400.0) or [body_h] * len(rows)
    _body_sum = int(sum(_bhs))
    # 渲染走的是行高之和，不是 graphicFrame 的 extent：0.45 英寸的行高地板会让
    # "行高之和"大于被区高截过的 frame_h（9 行台账正是这种），两者不一致时按 frame_h
    # 摆的下一个元素就会落进表格里。表格的真实底边只认 _true_h 这一份。
    _true_h = ((head_h if _head else 0) + _body_sum) / float(EMU_H)
    # 结论行要在留白区里给它留位置，否则表格铺满整区后结论被挤到区外（G5/G13 就是这么炸的）
    _concl0 = (tbl.get('conclusion') or '').strip()
    avail_h = zone[3] - (0.095 if _concl0 else 0.0)
    frame_h = min(int(max(0.12, avail_h) * EMU_H),
                  (head_h if _head else 0) + _body_sum)
    gf = slide.shapes.add_table(nrow, ncol, Emu(int(zone[0] * EMU_W)), Emu(int(zone[1] * EMU_H)),
                                Emu(int(zone[2] * EMU_W)), Emu(frame_h))
    t = gf.table
    for i in range(nrow):
        try:
            t.rows[i].height = Emu(head_h if (_head and i == 0)
                                   else _bhs[i - (1 if _head else 0)])
        except Exception:
            pass
    t.first_row = bool(header)
    t.horz_banding = False
    for j, w in enumerate(weight):
        try:
            t.columns[j].width = Emu(int(zone[2] * EMU_W * (w / tot)))
        except Exception:
            pass
    cells = 0
    num_cells, col_max, col_max_cell = set(), {}, {}
    for i, r in enumerate(grid):
        for j in range(ncol):
            val = (r[j] if j < len(r) else '') or ''
            c = t.cell(i, j)
            c.text = val
            c.fill.solid()
            if _head and i == 0:
                c.fill.fore_color.rgb = TBL_RGB['head']
            else:
                # 教程 2db7c536 原话+画面：底纹透明度第2格3%、第3格5%、第4格10%、往后依次+10%——
                # 行带色是**递进透明度**，不是两色交替；交替带色是默认模板的错误示范。
                c.fill.fore_color.rgb = TBL_RGB['band_a']
                _alpha = [0, 3, 5, 10, 20, 30, 40, 50, 60][min(i, 8)]
                from pptx.oxml.ns import qn as _qn
                _srgb = c.fill.fore_color._xFill.find(_qn('a:srgbClr'))
                if _srgb is not None and _alpha:
                    _a = _srgb.makeelement(_qn('a:alpha'), {'val': str(int(_alpha * 1000))})
                    _srgb.append(_a)
            # 现代台账去框线靠带色分行（教程：边框调整为无框线）；默认表格线是草稿感来源
            from pptx.oxml.ns import qn as _qn2
            tcPr = c._tc.get_or_add_tcPr()
            # CT_TcPr 有序（lnL→lnR→lnT→lnB→…→fill）：倒序插到最前，保证顺序合规
            for _ln_tag in ('a:lnB', 'a:lnT', 'a:lnR', 'a:lnL'):
                _ln = tcPr.find(_qn2(_ln_tag))
                if _ln is None:
                    _ln = tcPr.makeelement(_qn2(_ln_tag), {})
                    tcPr.insert(0, _ln)
                for _ch in list(_ln):
                    _ln.remove(_ch)
                _ln.append(_ln.makeelement(_qn2('a:noFill'), {}))
            numeric_cell = bool(NUMCOL_RE.match(val.strip())) and not (_head and i == 0)
            for p in c.text_frame.paragraphs:
                if numeric_cell:
                    p.alignment = PP_ALIGN.RIGHT
                elif j == 0:
                    p.alignment = PP_ALIGN.LEFT
                else:
                    p.alignment = PP_ALIGN.CENTER
                for run in p.runs:
                    run.font.size = Pt(TBL_HEAD_PT if (_head and i == 0) else TBL_BODY_PT)
                    run.font.bold = bool(_head and i == 0)
                    run.font.name = '微软雅黑'
                    run.font.color.rgb = TBL_RGB['head_text'] if (_head and i == 0) \
                        else TBL_RGB['body_text']
            if val.strip():
                cells += 1
                if numeric_cell:
                    num_cells.add(j)
                    try:
                        v = float(re.sub(r'[^0-9.\-]', '', val) or '0')
                    except Exception:
                        v = 0.0
                    if v > col_max.get(j, float('-inf')):
                        col_max[j] = v
                        col_max_cell[(i, j)] = v
            if val.strip():
                _emph = None
    # 强调：每个数字列里最大的那一格用亮青色加粗（语料 48% 单元格加粗，实测出来的习惯）
    for (i, j), v in col_max_cell.items():
        try:
            for para in t.cell(i, j).text_frame.paragraphs:
                for run in para.runs:
                    run.font.bold = True
                    run.font.color.rgb = RGBColor.from_string(_pp.get('cyan_hex') or '66D9F7')
        except Exception:
            pass
    rec['table_cells'] = cells
    rec['table_rows'] = nrow
    rec['table_header_band'] = bool(header)
    rec['table_numeric_cols'] = len(num_cells)
    rec['table_emphasis_cells'] = len(col_max_cell)
    # 表外一行结论：台账页最容易犯"只给数不给话"，这里强制留一行读表的话
    concl = (tbl.get('conclusion') or '').strip()
    if not concl and col_max_cell:
        (bi, bj) = max(col_max_cell.items(), key=lambda kv: kv[1])[0] if col_max_cell else (None, None)
        if bi is not None and bi < len(grid):
            lab = (grid[bi][0] if grid[bi] else '') or ''
            val = (grid[bi][bj] if bj < len(grid[bi]) else '') or ''
            if lab and val:
                concl = u'%s：%s' % (lab[:14], val[:16])
    if concl:
        try:
            # 结论要落在表格**下面**，不是"留白区里面"：旧写法 cy = min(cy, 区底-0.085)，
            # 而 9 行台账把留白区吃得一干二净（表底 0.840 = 区底），这一夹就把结论塞回
            # 表格最后两行上——真渲染 19/22 页实测如此，且 0.860 以下的页面一直空着。
            # 底边按 _true_h（行高之和）算，不按 frame_h：两者不等时 frame_h 会把
            # 下一个元素放进表格里。
            cy = zone[1] + _true_h + 0.02
            if cy + 0.075 > 0.94:
                rec['table_conclusion'] = False
                rec['table_conclusion_room'] = round(cy, 3)
                rec['notes'].append(
                    u'表格结论行放不下（表底 %.3f、页底 0.940）：宁可不写也不压表格，%s'
                    % (cy, concl[:40]))
            else:
                add_text(slide, concl[:40], [zone[0], cy, zone[2], 0.075], 16,
                         bold=True, color=(_pp.get('cyan_hex') or '66D9F7'), align='l',
                         name='table_conclusion')
                rec['table_conclusion'] = True
        except Exception as e:
            rec['table_conclusion'] = False
            rec['notes'].append('表格结论行没放进去：%s' % str(e)[:60])
    else:
        rec['table_conclusion'] = False
    return cells


def _clean_num_ctx(s):
    return re.sub(r'\s+', '', (s or '').strip())


hero_number = DL.hero_number
_DR_HEAD_MAX = DR.HEADLINE_MAX
bar_group = DL.bar_group
ring_chart = DL.ring_chart
card_grid = DL.card_grid


def chip_dupes_head(c, head):
    """筹码标签不许是标题的复述（同页重影）：整串比不中时还要比"抹掉数字后的那串词"。

    真渲染页 6 实测：标题 '四个条件今年同时成立，去年不是这样'，筹码却写
    '四个条件今年同时成立·12'——装配里 `_dup_of_head` 比的是整串，尾巴带上数字
    就比不中，于是同一页同一句话出现两遍，字数中位数也被这句顶上去。
    这条是 `_dup_of_head` 的加强版，只喂给筹码；正文短句仍按整串规则判（判据不同处已注明）。
    """
    k, hk = norm_content(c), norm_content(head)
    if k and hk and (k in hk or hk in k):
        return True
    cw = re.sub(u'[^一-龥]', u'', k or u'')
    return len(cw) >= 4 and cw in re.sub(u'[^一-龥]', u'', hk or u'')


APPENDIX_MARK = re.compile(u'（附[表件录]?\\s*[0-9一二三四五六七八九十]+）\\s*$')


def headline_count(text):
    """标题计数字数：句尾的附录交叉引用（'（附表 2）'）不计入显示契约的 16 字。

    这条豁免是有界的：只剥**句尾那一个**标记，剥完剩下的正文仍要完整过 ≤HEADLINE_MAX，
    所以它不可能被用来夹带长句。为什么不干脆裁掉标记：它是源文档附录的指向（台账页
    靠它对回'附表 2'），为了 6 个字符牺牲可追溯性不值得；而真渲染页 28 实测整条 19 字
    = 正文 13 字 + 标记 6 字，正文本来就合规。
    """
    t = re.sub(u'\\s', u'', text or u'')
    return len(APPENDIX_MARK.sub(u'', t))


def clip_headline(text):
    """把超显示契约的标题收成 ≤HEADLINE_MAX，并保证不留下半截数字；返回 (标题, 是否裁过)。

    三条路径共用这一个定义（数据页装配、表格页装配、G23 成品判据）——上一版只在数据页
    分支裁，表格页分支直接打原句，实测页 28 的 19 字标题因此漏网。
    """
    if headline_count(text or u'') <= DR.HEADLINE_MAX:
        return text, False
    _mark = APPENDIX_MARK.search(text or u'')
    _mark = _mark.group(0).strip() if _mark else u''
    _body = APPENDIX_MARK.sub(u'', text or u'') if _mark else (text or u'')

    # 候选必须自己读得通。'七，新品证号归工厂…' 在标点处只能裁出一个 '七'，屏幕上就是
    # 一个巨大的单字标题（真渲染页 29 目检）。判据复用现成的尺子：长度下限 + DR.BARE_ORDINAL
    # 整串 + DR.DANGLING 收尾；不套 is_clean_phrase（那条管 2–12 字的标签/筹码，标题要放到 16 字），
    # 也不接 _short_label（那条会硬截到 limit，'…我们自己持证后' 这种半句话宁可不裁）。
    def _readable(x):
        x = re.sub(u'\\s', u'', x or u'')
        return (len(x) >= 4 and bool(re.search(u'[一-龥]', x))
                and not DR.BARE_ORDINAL.search(x) and not DR.DANGLING.search(x))

    def _cut(s):
        c = DR._clip(s, DR.HEADLINE_MAX)
        if _readable(c):
            return c
        # 句号/问号/叹号也是标题的天然边界（'中国的医院分两种。一种门口排队排到街上' 取前半）。
        # 只在这条标题路上加这一层，不动 _clip 那份与筹码/显示行共用的标点表。
        s0 = re.split(u'[。！？!?]', s or u'')[0].strip()
        if s0 and headline_count(s0) <= DR.HEADLINE_MAX and _readable(s0):
            return s0
        return u''

    _hc = _cut(_body)
    if not _hc:
        # 句首的枚举记号不是内容：剥掉 '七，' 再裁一次
        _b2 = re.sub(u'^[一二三四五六七八九十百千]+\\s*[，、,]\\s*', u'', _body)
        _hc = _cut(_b2) if (_b2 and _b2 != _body) else u''
    # 裁出来的尾巴是数字 = 这个数被拦腰截了（真渲染页 17 实测：65 字整句裁成
    # '市场愿意付的价格是每天 1500'，源文其实是 '1500 到 3000 元' 的区间）。
    # 屏幕上放半截数对外就是一个错数，比标题超长严重得多：宁可短而准，
    # 退回不含数字的名词短语，数字整条留给正文/台账与安全网补上屏或进备注。
    if _hc and re.search(u'\\d$', re.sub(u'\\s', u'', _hc)):
        _pre = re.match(u'^[^0-9]*', _body).group(0)
        _hp = DR.phrase_of(_pre, DR.HEADLINE_MAX)
        _hc = _hp if (_hp and DR.is_clean_phrase(_hp)
                      and not re.search(u'\\d', _hp)) else u''
    if not _readable(_hc) or headline_count(_hc) > DR.HEADLINE_MAX:
        return text, False
    return (_hc + _mark) if _mark else _hc, True


def _short_label(t, limit):
    """数据版式里的标签/短句统一压到语料那种长度：超了就取"数字所在子句"，再超就硬截到标点。

    上一版把 121 字/页的原文整段塞进 hero+条形+卡片三层，G22 的同级分被"每页字数 p50"
    这一维拖死——那不是配色问题，是文字量问题。"""
    t = re.sub(u'\s+', ' ', (t or '').strip()).strip(u'，。、；： ')
    if len(t) <= limit:
        return t
    parts = DR.split_clauses(t, colon=True)   # 千分位逗号不算断点（'1,053 人' 是一句）
    # 先取"带数字的那个子句"（屏上留的是结论），再退回普通短句，最后才硬截
    for c in parts:
        if len(c) <= limit and re.search(r'\d', c):
            return c
    for i, c in enumerate(parts):
        if len(c) <= limit and i + 1 < len(parts) and \
           len(c + parts[i + 1]) <= limit and re.search(r'\d', parts[i + 1]):
            return (c + u'，' + parts[i + 1])[:limit]
    # 断点要在标点之前切：写成 m.end() 会把标点留在串尾，实测 hero 标签上屏成 '18,'
    # ——半截数字加一个逗号，比不写更糟。
    # 断点要在标点之前切（写成 m.end() 会把标点留在串尾，实测标签上屏成 '18,'），
    # 而数字中间的千分位逗号不是断点（'18,000' 不能切成 '18,'）。
    def _num_inner(m):
        i = m.start()
        return (m.group() in (u',', u';') and i >= 1 and i + 1 < len(t)
                and t[i - 1].isdigit() and t[i + 1].isdigit())
    cuts = [m.start() for m in re.finditer(u'[，、；：,;]', t)
            if m.start() >= 2 and m.end() <= limit and not _num_inner(m)]
    if cuts:
        return t[:max(cuts)].strip(u'，、；：,; ')
    # 最后才硬截：截点不许停在数字中间（'一台车一年服务 18,000 名女性' 截到 14 字会留 '18'，
    # 屏上半截数字比不写更糟——实测探针页 6），退回数字起点；退无可退就宁可少打这一行。
    head = t[:limit]
    if limit < len(t) and t[limit] in u'0123456789,.':
        mnum = re.search(u'[-+]?\\d[\\d,.]*$', head)
        if mnum:
            head = head[:mnum.start()]
    if limit < len(t) and re.match(u'[一-龥]', t[limit] or u''):
        # 刀口正好落在中文词中间（实测 '50 多家分娩量太'、'一年服务 18,000 名女'）：
        # 退回这一串里最后一个"数字+单位"，后面的修饰语整个丢掉——宁可少信息，不留断头词
        _mn = None
        for _mo in re.finditer(u'[-+]?\\d[\\d,.]*\\s*(?:%s)?' % DR.NUM_UNIT, head):
            _mn = _mo
        if _mn and _mn.end() >= 2:
            head = head[:_mn.end()]
    # 量词后面只留下一个字 = 断头词（实测筹码上屏 '一年服务 18,000 名女'、'主办方 500 名女性提'）：
    # 收回到量词为止，宁可少一个字也不要在词中间下刀
    _mm = re.search(u'[' + DL.MEASURE_CHARS + u']([一-龥])$', head)
    if _mm and limit < len(t):
        head = head[:_mm.start() + 1]
    return head.strip().rstrip(u'，、；：,; ')


def prose_display(pg, max_line, max_lines):
    """散文页（一个数字都提不出显示行）怎么办：并列的一串画卡片，其余按子句装箱。

    v10 逐页目检 + G23 实测：15 页把整段原文（首页 86 字）打上屏，屏幕上是一坨 13pt 小字。
    语料没有这种做法——并列关系画成 2~3 张卡片，散句压成 ≤2 条 ≤14 字；
    装不下的事实由 place_text 末尾的事实安全网补回，措辞可以改、事实不能少。
    返回 (items, lines, kind)，kind 为 'cards' / 'lines' / ''（空=这页没散文可装）。
    """
    body = [x for x in (pg.get('body') or []) if x]
    if not body:
        return [], [], ''
    txt = u' '.join(body)
    items = DR.parallel_enum(body)
    if len(items) >= 3:
        return items, [], 'cards'
    lines = DR.clause_pack(txt, max_line, max_lines, DR._clean(pg.get('headline') or u''))
    return [], lines, ('lines' if lines else '')


def headline_hero(head, body):
    """标题里就那一个数的陈述页：把数拿下来当大数字，标题留作标签（语料那种 statement 页）。

    只在最安全的形状上做：整页正文与标题同句、标题里恰好一个带单位的数、
    去掉数字后剩下的词还读得通（≥3 字、不挂在介词或连词上）。年份这类裸数不当大数字。
    返回 (label, hero dict)；不符合条件返回 (u'', None)——宁可不画，不许把数丢掉。
    """
    h = DR._clean(head)
    if not h:
        return u'', None
    for b in (body or []):
        bc = norm_content(b)
        if bc and bc != norm_content(h) and bc not in norm_content(h) \
                and norm_content(h) not in bc:
            return u'', None
    ms = [m for m in re.finditer(u'([-+]?\\d[\\d,\\.]*)(\\s*)(' + DR.NUM_UNIT + u')?', h)
          if m.group(1)]
    ms = [m for m in ms
          if not (re.sub(u'[^0-9]', u'', m.group(1)).__len__() == 4
                  and (m.group(3) or u'') in (u'', u'年'))]
    if len(ms) != 1:
        return u'', None
    m = ms[0]
    val, unit = m.group(1), (m.group(3) or u'')
    # 复合量词要跟着数字走：'2.8 亿人次' 的 unit 是 '亿人次' 而不是 '亿'，
    # 否则 '人次' 被留在标题里变成 '全国累计筛查 人次'（真渲染页 87 目检抓出）。
    # 跟排规则只在 DL.extend_unit 一处定义，数字排/条形组/G24 共用同一把尺。
    unit, _end = DL.extend_unit(h, m.end(), unit)
    label = DR._clean(h[:m.start()] + u' ' + h[_end:])
    if len(re.sub(r'\s', u'', label)) < 3 or re.search(
            u'[从到是为把被和与及、，：在约接近]$', label):
        return u'', None
    return label, {'value': val, 'unit': unit, 'label': u''}


def cap_screen_chars(slide, cap=19):
    """屏上字数预算（SPEC §13）：语料好示范页 p50=19 字——标题与大数字之外的解释行移进备注。

    为什么能动这一刀：G24/G13 核的是全簿文本（`pptx_all_text` 含 notesSlides），事实一条不丢；
    而 parity 的 chars 只数 slide 里的形状文字。形状留着只清空文本，不删元素——
    删了会让 timing 里的 spid 悬空（成品在 PowerPoint 里报修复）。
    """
    def _chars():
        tot = 0
        for sp in slide.shapes:
            if not sp.has_text_frame:
                continue
            tot += len(re.sub(r'\s', u'', sp.text_frame.text or u''))
        return tot
    moved = []
    while _chars() > cap:
        cand = [sp for sp in slide.shapes if sp.has_text_frame
                and (sp.name or u'').startswith((u'text_', u'caption'))
                and re.sub(r'\s', u'', sp.text_frame.text or u'')]
        if not cand:
            break
        sp = cand[-1]
        moved.append(sp.text_frame.text.strip())
        sp.text_frame.clear()
    if moved:
        tf = slide.notes_slide.notes_text_frame
        add = u'\n'.join(reversed(moved))
        tf.text = (tf.text + u'\n' + add) if (tf.text or u'').strip() else add
    return len(moved)


def repair_head_number(head, spans):
    """标题末尾的数字若只是原文里更长数字的前缀（'覆盖 82' ← '82,340'），补回整串。

    计划稿（subjects_zh.json）的标题是按字数硬截出来的，截在数字中间就在屏幕上留下了
    一个谁都没写过的数——v10 页 89 的标题是 '这是一篇覆盖 82'，原文是 82,340 例。
    断头数比少一句正文更糟：它是一个错的对外数字，G24 按全簿文本核还核不出来。
    """
    m = re.search(u'([\\d][\\d,\\.\\s]*)\\s*$', head or u'')
    if not m:
        return head
    frag = re.sub(u'\\s', u'', m.group(1))
    if not frag or not frag[-1].isdigit():
        return head
    for s in spans or []:
        for mo in re.finditer(r'\d[\d,\.]*', s or u''):
            tok = mo.group(0)
            if (len(tok) > len(frag) and tok.startswith(frag)
                    and tok[len(frag)] in u',0123456789'):
                # 紧跟其后的量词一起带上：'82,340' 后面原文写着 '例'，标题只留数字仍是半句话
                _u = re.match(u'^\\s*(%s)' % DR.NUM_UNIT, s[mo.end():] or u'')
                if _u:
                    tok = tok + u' ' + _u.group(1)
                cand = (head[:m.start(1)] + tok).strip()
                if len(re.sub(u'\\s', u'', cand)) <= _DR_HEAD_MAX + 6:
                    return cand
    return head


def place_text(slide, pg, s, zone, rec, ink=None):
    """Everything lands inside the reserved blank area; sizes follow the measured type scale
    (kb/visual_study.md: 47.9% of corpus pages carry >=60pt type, 54.6% carry <=30 chars)."""
    role = s['role']
    ink = ink or CT.TEXT_INK.get(s.get('style', 'tech_blue'), CT.TEXT_INK['tech_blue'])
    head = pg['headline']
    # display_mode 要到下面的分派处才赋值，这里必须按"是不是留档册"判：
    # 用 rec['display_mode'] 当门会永远不成立，断头数就修不掉（实测页 89 仍上屏 '82'）
    if not pg.get('verbatim'):
        head = repair_head_number(head, [x for x in (pg.get('source_span')
                                                     or pg.get('body') or []) if x])
    disp = [x if isinstance(x, str) else (x.get('text') or '')
            for x in (pg.get('display_lines') or [])]
    disp = [x.strip() for x in disp if x and x.strip()]
    # 三条分支之外还有一条"path 为空但页面有正文"，body 必须先给默认值，
    # 否则下面的 deduped=[dedupe(x) for x in body] 会 UnboundLocalError（实测 build 崩退码 3）
    body = [x for x in (pg.get('body') or []) if x]
    if not pg.get('verbatim'):
        # 旧计划稿可能留着窗口碎片（实测页 217 上屏 '二·1280'）：章节序号粘数字不是显示行；
        # refine 不覆盖已有提案，装配端兜住：丢碎片、把区间事实整条补回（'1280 到 1980 元'）
        disp = [x for x in disp
                if not re.match(u'^[一二三四五六七八九十]+[·・.]\\s*\\d', x)]
        for _mo in re.finditer(u'(\\d[\\d,.]*)\\s*(?:到|至|~|—)\\s*(\\d[\\d,.]*)\\s*(?:%s)'
                               % DR.NUM_UNIT, u' '.join(body)):
            _rng = re.sub(u'\\s+', u' ', _mo.group(0)).strip()
            if len(re.sub(u'\\s', u'', _rng)) <= TH['display_line_max_chars'] and _rng not in disp:
                disp.append(_rng)
    # 与标题同义的行不再重复上屏：实测探针页 6 三处重影——标题"一台车一年服务 18,000 名女性"、
    # 大数字又画 46.9 万元、正文还打一条"一台车一年服务 18"。字多不等于信息多。
    # 只在提炼册生效：留档册（verbatim）要求一字不删，这里不许动它。
    _hkey = norm_content(head)

    def _dup_of_head(x):
        k = norm_content(x)
        return bool(k) and bool(_hkey) and (k in _hkey or _hkey in k)
    if disp and not pg.get('verbatim'):
        # 提炼模式：屏幕上只放这几条短句 +（可选）一个大数字
        disp = [x for x in disp if not _dup_of_head(x)]
        body = disp
        pg = dict(pg)
        pg['body'] = disp
        rec['display_mode'] = 'refined'
        rec['display_lines'] = len(disp)
    elif not pg.get('verbatim') and [x for x in (pg.get('body') or []) if x]:
        # 提案为空（例如 needs_edit 的长句页）不等于可以退回整段文字墙：
        # 这里直接按"事实装箱"现造 ≤2 条 ≤14 字的短句，剩下的交给 refine/agent 改写
        # 这里不能用 ctype：它在下面的分派处才赋值（上一版直接读它 → UnboundLocalError，
        # build 崩退码 3，check 读的是旧成品，看起来"分数没变"其实是没重装配）
        lines, ctype2, _needs = DR.propose_lines(
            dict(pg, content_type=pg.get('content_type') or ''))
        if not lines:
            packed = []
            _lim = TH['display_line_max_chars']
            for k in DR.key_numbers(' '.join(pg.get('body') or []), 12):
                # 只用"整条子句"或"这个数本身"上屏，绝不用 key_numbers 的固定窗口：
                # 窗口是从数字左边数 14 个字裁的，实测裁出 '年公司在华东12' 这种半截话打上屏
                # （比空着更像排错版，G19/G1 都读不出问题，因为它是原文的真字）。
                v = (k.get('value') or u'').strip()
                one = u''
                for src in (pg.get('body') or []):
                    for c in DR.split_clauses(DR._clean(src)):
                        c = DR._clean(c)
                        if v and v in c and len(c) <= _lim:
                            one = c
                            break
                    if one:
                        break
                if not one and v and len(v) <= _lim:
                    one = v
                if one and one not in packed:
                    packed.append(one)
                if len(packed) >= TH['display_lines_max']:
                    break
            lines = packed
        _prose_kind = ''
        # 并列的一串（医联体/科联体/客联体）优先画卡片：prose 装箱只会取到其中一条子句，
        # 屏幕上剩两条并列信息没处放，而语料那种 statement 页就是 2~3 张卡。
        # 只在"这页没有数字要装箱"时让位，否则条形组/大数字那条路更该走（数字是对外要举证的东西）。
        _pit0, _pln0, _kind0 = prose_display(pg, TH['display_line_max_chars'],
                                             TH['display_lines_max'])
        if (_kind0 == 'cards'
                and not DR.key_numbers(u' '.join([x for x in (pg.get('body') or []) if x]), 3)):
            lines = []
            _prose_kind = 'cards'
        if not lines:
            # 数字装箱提不出行（纯散文页）：并列的一串画卡片，其余按子句装箱成 ≤2 条短句。
            # 不留这条路就会退回"整段原文小字墙"（v10 实测 15 页超 G23 预算）。
            _pit, _pln, _prose_kind = prose_display(pg, TH['display_line_max_chars'],
                                                    TH['display_lines_max'])
            if _prose_kind == 'cards':
                pg = dict(pg)
                pg['content_type'] = 'list'      # 下游卡片分支按 list/process 走
                pg['body'] = _pit
                pg['display_lines'] = []
                body = _pit
                rec['display_mode'] = 'refined'
                rec['display_lines'] = len(_pit)
                rec['display_lines_source'] = 'enum-cards'
                rec['content_type'] = 'list'
                # 整组要点留底：标题常是其中一条（'先看现场'），下面 dedupe 会把它吸收进
                # 标题框，body 剩 2 条就够不到 3 张卡。卡片分支按这份整组画，图形化率才不归 0（#61）。
                rec['card_items'] = list(_pit)
                lines = []
            elif _prose_kind == 'lines':
                lines = _pln
        if lines:
            body = lines
            pg = dict(pg)
            pg['body'] = lines
            pg['display_lines'] = lines
            rec['display_mode'] = 'refined'
            rec['display_lines'] = len(lines)
            rec['display_lines_source'] = ('clause-packed' if _prose_kind == 'lines'
                                           else 'engine-packed')
            # 这里不能用 ctype：它是下面分派处才赋值的局部名（写成 ctype 会在
            # "提案为空→引擎装箱"这条路上 UnboundLocalError，实测崩退码 3）
            rec['content_type'] = pg.get('content_type') or ctype2
    else:
        # 提不出显示行也不退回"整段原话小字墙"：逐页目检看到 v10 有 48 页走这条路，
        # 屏幕上是一坨 13pt 的原文（页 27/30 那种），既不像片子也把字数中位数顶到 29 字。
        # 提炼契约允许改措辞、不允许丢事实：这里按提炼册走，落下的事实由末尾的事实安全网补齐。
        body = [x for x in (pg.get('body') or []) if x]
        rec['display_mode'] = 'refined' if (body and not pg.get('verbatim')
                                             and s.get('role') != 'quote') else 'verbatim'
        if rec['display_mode'] == 'refined':
            rec['display_lines_source'] = 'fact-net-only'
            _it2, _ln2, _kind2 = prose_display(pg, TH['display_line_max_chars'],
                                               TH['display_lines_max'])
            if _kind2 == 'cards':
                pg = dict(pg)
                pg['content_type'] = 'list'
                pg['body'] = _it2
                pg['display_lines'] = []
                body = _it2
                rec['display_lines_source'] = 'enum-cards'
                rec['display_lines'] = len(_it2)
                rec['card_items'] = list(_it2)
            elif _kind2 == 'lines':
                pg = dict(pg)
                pg['body'] = _ln2
                pg['display_lines'] = _ln2
                body = _ln2
                rec['display_lines_source'] = 'clause-packed'
                rec['display_lines'] = len(_ln2)
    # The headline is a shortened label cut from body[0]. Showing both prints the same clause
    # twice, so strip the leading headline text from the body line and keep the remainder --
    # the facts after the comma (履约率 79.9% 等) must survive.
    def dedupe(line):
        h = head.rstrip('，。、；')
        k = line.strip()
        if k == h:
            return ''
        if h and k.startswith(h):
            return k[len(h):].lstrip('，。、；： ,')
        if h and h in k and len(h) >= 8:
            # 从标题之后接着取，别用 replace 把中间切一刀：
            # 实测 '贡献毛利 281.4 万元' 被 replace 掉前半截后上屏成 '利281.4万元·268'，
            # 半个词比少一行糟得多。
            j = k.find(h) + len(h)
            return k[j:].lstrip(u'，。、；： ,·')
        return k
    deduped = [dedupe(x) for x in body]
    # 正文首句被切成标题后，dedupe 会把它清空——这句话仍在页面上（就是标题框），
    # 不能算"装配丢句"。上一版按 placed 计数，14 页被误判成丢内容。
    absorbed = sum(1 for a, b in zip(body, deduped) if a and not b)
    body = [x for x in deduped if x]
    zx, zy, zw, zh = zone
    pal_dl = CT.PALETTES.get(s.get('style') or 'tech_blue', CT.PALETTES['tech_blue'])
    big = None
    # 主视觉取正文里的数，但**标题已经说过的数不能再当主视觉**：
    # 实测 statement 页把标题开头的 '2026' 放大成 150pt，同一页下面又原样打一遍标题，
    # 而且按"这个数字"裁掉正文行的结果是把 268/214 两个真结论挤没了（G24 缺 2 个）。
    for _m in HERO_BIG.finditer(u' '.join(body[:1])):
        _k = norm_content(_m.group(1))
        if _k and _k in _hkey:
            continue
        big = _m
        break
    n = 0
    y = zy
    drew = None          # 版式分流画掉的东西（hero/bars/cards）；表页与色场页不参与，末尾要读它
    anim_ids = []

    def break_head(t, pt, w):
        """标题折行必须断得均衡：末行只剩 1-2 个字就是孤字尾行，G8 会拦。"""
        return balanced_break(t, pt, w)

    def stack(text, pt, **kw):
        nonlocal y, n
        # 孤字尾行在这一点统一处理（G8 与这里共用 orphan_tail，不留两套口径）：
        # 先按分句断行，断完仍只剩 1–2 个字就降一档字号让它整行放下。
        # 调用方给来的是"已经折好的几行"时要逐行处理——把它们用空格拼回一句，
        # 会在词中间留下一个空格（实测 '生美门店的工 作日'），比不断行更糟。
        _in_lines = text if isinstance(text, (list, tuple)) else [text]
        _in_lines = [x for x in _in_lines if (x or u'').strip()]
        _w_box = kw.get('width', zw)
        _pt2 = pt
        for _ln in _in_lines:
            _, _p2 = fit_text_lines(_ln, _pt2, _w_box)
            _pt2 = min(_pt2, _p2)
        _out = []
        for _ln in _in_lines:
            _l2, _ = fit_text_lines(_ln, _pt2, _w_box)
            _out += _l2
        text, pt = (_out or _in_lines), _pt2
        # The old version clamped the box to whatever room was left and said nothing, so a page
        # whose text had grown simply ran past its frame while every gate stayed green.
        need = line_height_frac(pt) * est_lines(text, pt, kw.get('width', zw))
        room = zy + zh - y
        if need > room + 0.002:
            rec.setdefault('overflow_lines', []).append(
                {'pt': pt, 'need': round(need, 4), 'room': round(max(0.0, room), 4),
                 'text': (text or '')[:24]})
        h = need if need <= room else max(0.04, room)
        if text:
            rec.setdefault('shown_texts', []).append(
                u' '.join(text) if isinstance(text, (list, tuple)) else u'%s' % text)
        sh = add_text(slide, text, [zx, y, kw.get('width', zw), h], pt,
                      **{k: v for k, v in kw.items() if k != 'width'})
        if sh is not None:
            anim_ids.append(sh.shape_id)   # G6 wants the motion measured in the deck, not the plan
        y += max(h, need) + 0.012
        n += 1

    if role in ('cover', 'closing'):
        # 封面四件套（SPEC §17，照语料 NO.125/126 封面墙）：kicker 文档名小字 → 口号主标题 →
        # 价值主张副标题 → 信息胶囊行 → 底部落款。主标题用 agent 写的 cover_slogan，
        # 不再拿文档名居中压图（G29 会拦"最大字==文档名"）。
        kicker = pg.get('cover_kicker') or (head if pg.get('cover_slogan') else '')
        if kicker and role == 'cover':
            stack(kicker, 16, align='c', color=ink['section'], name='kicker')
        slogan = pg.get('cover_slogan') or head
        tpt = pick_pt(slogan, zw, 58, 34)
        stack(break_head(slogan, tpt, zw), tpt, bold=True, align='c', name='title',
              color=ink['text'])
        csub = pg.get('cover_subtitle') or ''
        if csub:
            stack(csub, 20, align='c', color=ink['muted'], name='subtitle')
        chips = pg.get('cover_chips') or []
        if chips and role == 'cover':
            stack(u'  |  '.join(chips[:4]), 14, align='c', color=ink['section'], name='chips')
        byline = pg.get('subtitle') or (body[0][:60] if body else '')
        if byline:
            stack(byline, 15, align='c', color=ink['muted'], name='byline')
        for extra in body[1:]:
            stack(extra, 14, align='c', color=ink['muted'])
    elif role == 'divider':
        # 数字过渡页（教程 3056573 麒麟数字内页）：giant 数字居左、标题居右并排（不纵向堆叠，
        # 避免长章名撑爆留白区），数字自左飞入/标题自右飞入同时（build 按 divider_anim 上映）。
        # 教程 [35.3-43.3]：字号拉满、斜体加粗、数字填白。教程原片让数字"稍微溢出画面"，
        # 与本技能 G8 越界闸冲突——按闸保留画布内，尺度/字色/斜体/动效做到同级（取舍记 SPEC §18.2）。
        _num = '%02d' % pg.get('chapter', 1)
        # 编号必须单行：字号走引擎字宽模型（对真渲染标定的同一把尺子），再留斜体余量；
        # 实测拍脑袋 232pt 会把 '02' 折成两行还裁底（渲染页 11）。
        _npt = DL.fit_pt(_num, zw * 0.36 * 0.90, [240, 220, 200, 180, 160, 140, 120, 96])
        _nsh = add_text(slide, _num,
                        [zx, zy, zw * 0.36, zh * 0.72], max(96, _npt), bold=True,
                        align='l', color='FFFFFF', name='chapter_no')
        if _nsh is not None:
            _nsh.text_frame.word_wrap = False
            for _p in _nsh.text_frame.paragraphs:
                for _r in _p.runs:
                    _r.font.italic = True
        # 教程 [60.3-73.3]：右块取色器吸底图颜色→渐变→倾斜角。取色器等价=底图右带主色统计；
        # 右块必须在文案下层（半透明渐变盖在字上会把对比度压垮）。
        import divider_art as DA
        _bg = rec.get('bg_src') or rec.get('bg') or ''
        if not os.path.exists(_bg or ''):
            _bg = rec.get('poster_path') or ''
        _blk = None
        if os.path.exists(_bg or ''):
            _blk = DA.add_gradient_block(
                slide, [zx + zw * 0.36, zy + zh * 0.10, zw * 0.62, zh * 0.72],
                DA.sample_region_color(_bg))
        tp = pick_pt(head, zw * 0.60, 40, 26)
        _tsh = add_text(slide, break_head(head, tp, zw * 0.60),
                        [zx + zw * 0.38, zy + zh * 0.18, zw * 0.60, zh * 0.44], tp,
                        bold=True, align='l', color=ink['text'], name='title')
        # 教程 [82.3-105.9] 的 Ctrl+G 可见后果=文案与右块一起飞入：不建真 grpSp（组内文字会对
        # G1/G4/G23/G24 的 26 处不递归迭代不可见），改用右块与标题同一条 withEffect 飞入等价。
        rec['divider_anim'] = {'chapter_no': 'fly-left', 'title': 'fly-right',
                               'divider_block': 'fly-right'}
        if _tsh is not None:
            anim_ids.append(_tsh.shape_id)
        # 过渡页不许只留编号+两字（SPEC §17.5）：该章看点至少一条实质短句。
        highlights = pg.get('divider_highlights') or body
        for extra in highlights[:1]:
            _hsh = add_text(slide, extra, [zx + zw * 0.38, zy + zh * 0.66, zw * 0.60, zh * 0.24],
                            16, align='l', color=ink['muted'], name='highlight')
            if _hsh is not None:
                anim_ids.append(_hsh.shape_id)
    elif role == 'statement' and big:
        stack(big.group(1), 150, bold=True, align='c', name='hero_num',
              color=ink['section'])
        tp = pick_pt(head, zw, 22, 16)
        stack(break_head(head, tp, zw), tp, align='c', color=ink['text'])
        hv = norm_content(big.group(1))
        for extra in body:
            # 大数字已经把这个数放大在屏幕上了，正文那条就只留"这个数说的是什么"：
            # 实测自测夹具页 3 同一页出现 '214家'（150pt）+'实际履约 214家'（14pt）两遍。
            # 专名/结论词要留着（G24 逐事实核成品），重复的那个数字才去掉。
            if hv and hv in norm_content(extra):
                tail = _short_label(re.sub(re.escape(big.group(1)), u'', extra, count=1),
                                    TH['display_line_max_chars'])
                if len(re.sub(r'\s', u'', tail)) < 2:
                    continue
                extra = tail
            stack(extra, 14, align='c', color=ink['muted'], name='caption')
    elif role == 'table':
        for sec in (pg.get('sections') or []):
            stack(sec, 14, align='l', color=ink['section'], name='section')
        # 表格页标题原来上限写死 26pt：语料同类"数字密集页"整页最大字 p25=54、p50=96，
        # 而这页除了 16pt 表体就没有别的层级（真渲染 19/22/28 实测最大字 26pt），
        # G27 又把 table 整列豁免，所以全场唯一没字号要求的页型就是它。
        # 放到 54 起挑、由 pick_pt 按宽度收到一行放得下（12 字≈54pt、19 字≈40pt）。
        # 为什么不给到 60：60pt 的行高 0.147 正好把 0.120→0.270 的空带填满，标题底边与
        # 表格顶 0.000 间隙贴死——est_lines 是估的行高，不等于 WPS 真实渲染行高，
        # 这种"刚好卡住"必须让开。54 是语料同类数字密集页的 p25（p50=96），留出 0.018 呼吸。
        # 压到表格上的风险由 G25 的 tbl_text_overlaps 兜（它量文字落进行高之和以内）。
        # 表格页这条分支上一版没走标题裁剪，直接打原句：实测页 28 的 19 字标题因此漏网。
        # 现在与数据页共用 clip_headline/headline_count（附录标记 '（附表 N）' 不计入 16 字，
        # 但剥完剩下的正文仍要整条过线），不再有两套口径。
        head, _hc_t = clip_headline(head) if rec.get('display_mode') == 'refined' else (head, False)
        tp = pick_pt(head, zw, 54, 18)
        stack(break_head(head, tp, zw), tp, bold=True, align='l', name='title',
              color=ink['text'])
    else:
        for sec in (pg.get('sections') or []):
            stack(sec, 14, align='l', color=ink['section'], name='section')
        ctype = pg.get('content_type') or ''
        # 装大数字之前先数一遍"这页还有几个事实没地方放"：
        # 实测 v10 有 25 个事实是被 110pt 大数字挤下屏的（筹码行已经没有位置），
        # 大数字是装饰、事实是内容，两者抢地方时装饰让路。
        _pre_src = u' '.join([head] + [x for x in (pg.get('source_span') or body) if x])
        _pre_shown = norm_content(head + u' ' + u' '.join(body))
        _pre_miss = [f for f in DR.fact_units(_pre_src, (pg.get('entities') or [])[:8])
                     if norm_content(f) and norm_content(f) not in _pre_shown]
        # 条形组与标题先分好工：柱顶自己会打数值和标签，标题里就不该再留同一串
        # （实测同一页 '52万元' 出现两遍、'杭州9家' 既在标题又在条顶——同页重影）
        _bar_src = []
        for _x in ([x for x in (pg.get('source_span') or []) if x] or body):
            _bar_src += [c for c in DR.split_clauses(DR._clean(_x))] or [_x]
        _items3 = (DL.items_from_numbers(_bar_src)
                   if rec.get('display_mode') == 'refined' and ctype != 'ledger' else [])
        _ser_cores = DR.series_cores(u' '.join(_bar_src))
        head_show = head
        if len(_items3) >= 3 and _ser_cores:
            _full, _res = [], []
            for _c in DR.split_clauses(DR._clean(head)):
                _cn = re.sub(r'\s', u'', _c)
                _in_ser = any(re.search(u'(?<!\d)' + re.escape(k) + u'(?!\d)', _cn)
                              for k in _ser_cores)
                if _in_ser:
                    # 只剥数字与单位，别用 clean_label：它把 '单车年收入' 剥成 '年收入'，
                    # 专名 '单车年收入' 就再也核不到（G24 逐事实核成品，实测页 14 点名）
                    _w = DR._clean(re.sub(r'[-+]?\d[\d,\.]*\s*' + DR.NUM_UNIT + u'?', u'', _c))
                    if len(re.sub(r'\s', u'', _w)) >= 3:
                        _res.append(_w)
                else:
                    _full.append(_c)
            # 让位给柱子时留哪一句：先留**没进柱子的那一句**（它就是这页剩下的结论），
            # 一句都不剩才退回第一个数字残句。上一版无差别取第一句，
            # 把'履约率 79.9%'连着'实际履约 214 家'一起裁掉，79.9% 从此不在屏幕上（G24 点名）。
            # 也不把残句用逗号拼起来（'第一季度投放，第二季度' 读不通且与柱底标签重复）。
            # 候选要过的是"标题像不像话"，不是标签那套 ≤12 字（is_clean_phrase）：
            # 上一版误用 is_clean_phrase，把 '2025年公司签约 12 城'（16 字）整条否掉，
            # 标题退回原句 → '杭州 9 家' 既在标题又在柱顶，同页重影被反证抓住。
            # DATEISH_HEAD 也不能用在标题上：'2025年公司签约 12 城' 是原文正经开头，
            # 那条正则本来是防窗口把 '2026' 裁成 '026' 的碎片，用在标题上会连好句子一起否掉。
            _hcands = [DR._clean(x) for x in (_full or _res)]
            _hcands = [x for x in _hcands if x and len(x) >= 4 and not DR.DANGLING.search(x)]
            _pick = ([x for x in _hcands if len(x) <= _DR_HEAD_MAX] or _hcands)[:1]
            if _pick:
                _c0 = _pick[0]
                if len(_c0) <= _DR_HEAD_MAX:
                    head_show = _c0
                else:
                    # 只能在标点处收刀：硬截 [:16] 实测把 '2025年公司签约 12 城，杭州 9 家…'
                    # 切成 '…杭州 9 '——屏幕上是一个断掉的数，既过不了 G1（原文没这串）
                    # 也把 '9 家' 与柱顶的 '9 家' 算成同页重影（G8/并列反证一起点名）。
                    _cut = max(_c0.rfind(u'，', 0, _DR_HEAD_MAX),
                               _c0.rfind(u'、', 0, _DR_HEAD_MAX),
                               _c0.rfind(u'：', 0, _DR_HEAD_MAX),
                               _c0.rfind(u' ', 0, _DR_HEAD_MAX))
                    head_show = _c0[:_cut].rstrip(u'，、： ') if _cut >= 4 else head
        # 陈述页那种"标题里就一个数"（盈亏平衡 9.0 台）在语料里是标签 + 超大数字，
        # 不是整句 30pt 标题。提数的前提是留白区放得下 110pt 的数（≥0.52 画布高），
        # 放不下就原样当标题打——先把数从标题里摘掉又画不出 hero，等于把事实弄丢（G24 会点名）。
        _h_hro = None
        if (not _items3 and rec.get('display_mode') == 'refined' and zh >= 0.52
                and not (pg.get('hero_number') or {}).get('value')):
            _hlbl, _hnum = headline_hero(head, [x for x in (pg.get('body') or []) if x])
            if _hnum:
                head_show = _hlbl
                _h_hro = _hnum
        # 两个并列数字要走页底数字排时，标题里不能再留着这两个数（否则同页重影，反证会抓）：
        # 取第一个数字之前的那截当标题（'第一季度投放 6 台、…' → '第一季度投放'）。
        if (rec.get('display_mode') == 'refined' and role != 'compare' and len(_items3) == 2):
            _mnum = re.search(u'[-+]?\\d', head_show)
            if _mnum and _mnum.start() >= 4:
                _pre = DR._clean(head_show[:_mnum.start()]).rstrip(u'，、： ')
                # 摘数剩下的那截必须自己读得通：'回收期短到 1.5 年至 2.7 年' 摘掉数字后剩
                # '回收期短到'——挂在 '到' 上的半句话（真渲染页 21 目检）。与上面标题候选
                # 共用 DR.DANGLING 这一把尺，不另立判据。
                if len(_pre) >= 4 and not DR.DANGLING.search(_pre):
                    head_show = _pre
        # 标题上限 HEADLINE_MAX 是显示契约（SPEC §12），可成品端此前没有任何一处执行它：
        # plan 把整句当 headline 交下来，装配就原样打成一个 57 字的 32pt 标题
        # （真渲染页 17 目检；每页字数 p90 因此从 34 被顶到 57，同级分掉 5 分）。
        # 这里只裁"上屏这一行"：原句整条留在 body/台账里，被裁掉的数字由下面的事实安全网
        # 补回屏幕或备注——G24 仍逐事实核成品，裁剪不等于丢事实。
        # 挑法复用 DR._clip / _short_label 这两把现成的尺，不另立判据。
        if rec.get('display_mode') == 'refined':
            _hc6, _did = clip_headline(head_show)
            if _did:
                head_show = _hc6
            elif headline_count(head_show) > DR.HEADLINE_MAX:
                rec['notes'].append(
                    u'标题 %d 字超显示契约 %d，裁不出读得通的短标题：这页要人工改写'
                    % (headline_count(head_show), DR.HEADLINE_MAX))

        # 稀疏页（整页 ≤28 字）按语料 centred-statement 摆：大字居中压在页面中部，
        # 不再"左上小标题+下半页空"（用户第七/八轮否的就是这种没做完的样子）。
        _sparse = (rec.get('display_mode') == 'refined' and role not in ('table', 'divider')
                   and len(_items3) < 2
                   and len(re.sub(r'\s', u'', head_show + u''.join(body))) <= 28)
        if _sparse:
            y = zy + zh * 0.30
            bpt = 18
            tp = pick_pt(head_show, zw, 60, 40)
            stack(break_head(head_show, tp, zw), tp, bold=True, align='c', name='title',
                  color=ink['text'])
        else:
            tp = pick_pt(head_show, zw, 48, 32)
            stack(break_head(head_show, tp, zw), tp, bold=True, align='l', name='title',
                  color=ink['text'])
        # 正文整句上屏，一句都不许丢。放不下的时候先降字号（16→14→13，仍高于语料"无层级小字"线），
        # 降到 13 还装不下才记 overflow——闸会叫"拆页"，而不是让引擎静默丢句
        # （上一版直接切 body[:4]，10 句进 4 句出，G1/G13 因为只读 outline 而毫无察觉）
        room_now = zy + zh - y
        bpt = 16
        for cand in (20, 18, 16):
            need_all = sum(line_height_frac(cand) * est_lines(x, cand, zw) + 0.012
                           for x in body)
            if need_all <= room_now + 0.002:
                bpt = cand
                break
        kn = pg.get('key_numbers') or []
        hero = pg.get('hero_number')
        drew = None
        drawn_txt = []
        # 语料那套"数据用图形承载"：同一单位 ≥3 个数字就画条形组，而不是排三行文字。
        # 图形化率是同级分的一维（基线见 kb/parity_baseline.json，好示范页口径）；上一版我们只有 0.019，
        # 条形组只在 content_type=='compare' 才画，
        # 于是"各城市 12 家 / 9 家 / 4 家"这种天然该画柱子的页全走成了文字。
        _body_need = sum(line_height_frac(bpt) * est_lines(x, bpt, zw) + 0.012
                         for x in body)
        _bar_room = zy + zh - y - _body_need
        # 语料签名式摆法：两个并列数字画成"页底一排大数字+小单位+小注释"
        # （360°册 p002 的 4,997mm/1,963mm 那排），不再画两根小竖条留半页空。
        # ≥3 个同单位数字仍走条形组（那才是图表），对比页也保留条形组。
        # 教程 s03 帧的指标卡（圆图标+大数字+同比红绿箭头）优先：讲解原话
        # "分别给增长和下降数据设置不同颜色并绘制箭头"，页面里有同比/增长/下降 x% 就用它。
        _deltas = []
        _txt_all = u' '.join([head] + [x for x in body if x])
        for _md in re.finditer(u'(同比|环比|增长|下降|提升|回落|收窄)[^0-9%]{0,8}([\\d.]+)\\s*%',
                               _txt_all):
            _deltas.append((_md.group(1), _md.group(2), _md.group(1) in (u'增长', u'提升')))
        if (not drew and rec.get('display_mode') == 'refined' and _deltas and kn
                and (zy + zh - y) >= 0.30):
            _cards = []
            for _i, (_dl, _dv, _up) in enumerate(_deltas[:3]):
                _mv = kn[min(_i, len(kn) - 1)]
                _cards.append((_dl, _mv.get('value') or u'', u'%s %s%%' % (_dl, _dv), _up))
            if DL.delta_cards(slide, _cards, [zx, zy + zh - 0.20, zw, 0.18],
                              pal_dl, ink, add_text) >= 1:
                drew = 'deltas'
                for _c in _cards:
                    drawn_txt.append(_c[1])
                    drawn_txt.append(_c[2])
                body = [x for x in body
                        if not any(norm_content(c[1]) in norm_content(x) for c in _cards)]
        if (not drew and rec.get('display_mode') == 'refined' and role != 'compare'
                and len(_items3) == 2 and (zy + zh - y) >= 0.36):
            _row_h = 0.26
            _ry = zy + zh - _row_h - 0.02
            if DL.number_row(slide, _items3, [zx, _ry, zw, _row_h], pal_dl, ink, add_text) >= 2:
                drew = 'numbers'
                for _it in _items3:
                    drawn_txt.append(_it[2] if len(_it) > 2 else u'')
                    drawn_txt.append(_it[0] or u'')
                body = [x for x in body
                        if not any(norm_content(i[2]) in norm_content(x) for i in _items3)]
                # 正文留在上沿暗带里（标题下面），别推到中段：中段是留给照片的亮区，
                # 白字压上去只有 2.5:1（G4 实测）。语料也是"字在上、数在下、图在中间"。
        if len(_items3) >= 3 and _bar_room >= 0.34:            # 柱子别把留白区坐满：这页往往还剩一两个"柱子没画掉的事实"，
            # 它们只能走下面那行筹码（要 0.16 高）。v10 实测 14 页把 '706 台'、'23 亿元'
            # 这类要举证的数记进 facts_undrawn，就是因为条形组占满了区高。
            _bar_h = min(0.40, max(0.30, _bar_room - 0.16))
            # 再收一道硬边界：条形组画完（含柱底标签）不许越过留白区下沿。
            # v10 页 143 的 overflow 0.127 就是这么来的——区高 0.5 的 statement 页
            # 先算 room 再算 body_need，两个减项口径不一致时就把文字顶出了区（G5/G13 各叫一次）。
            _bar_h = min(_bar_h, max(0.0, zy + zh - y - 0.03))
            if bar_group(slide, _items3, [zx, y, zw, _bar_h], pal_dl, ink) >= 3:
                drew = 'bars'
                for _it in _items3:
                    drawn_txt.append(_it[2] if len(_it) > 2 else u'')
                    drawn_txt.append(_it[0] or u'')
                _hd_n = norm_content(head_show)
                body = [x for x in body
                        if not any(norm_content(i[2]) in norm_content(x) for i in _items3)
                        and norm_content(x) not in _hd_n and _hd_n not in norm_content(x)]
                y += _bar_h + 0.02
        _hero_room = zy + zh - y
        # metric/compare 页只要有关键数字就先立一个 ≥60pt 主视觉大数；其余事实走备注（见末尾
        # 安全网），不再因为"还有事实没上屏"就压低大数字（detail→notes 落地后装饰与内容不抢地方）。
        # compare 也进来：旧版 compare 页不进 hero 分支，多数字散成筹码行 → hero_rate 与 p50 双差。
        if (not drew and rec.get('display_mode') == 'refined'
                and (ctype in ('metric', 'compare', 'timeline') or _h_hro)
                and (hero or kn or _h_hro)
                and _hero_room >= 0.34):
            h = dict(hero or {})
            if not h.get('value') and _h_hro:
                # 标题里摘下来的那个数就是这页的主视觉（'盈亏平衡 9.0 台' → 9.0 台 + 标签）
                h = dict(_h_hro)
            elif not h.get('value') and kn:
                # 大数字必须画"标题里没说过的那一个"：标题已经讲了 18,000 人，
                # hero 再画 18,000 就是同一页三个地方重影（探针实测页 6/9）
                _cand = [k for k in kn if norm_content(k['value']) not in _hkey]
                if _cand:
                    _v = _cand[0]['value']
                    # 数字后面的量词要跟着一起放大：光秃秃一个 '412' 挂在 110pt 的位置，
                    # 屏幕上就是"412 什么？"（实测拆页后那页只剩 412 与 96 两个裸数字）。
                    _mu = re.search(re.escape(_v) + u'\s*(' + HERO_UNIT + u')',
                                    u' '.join(body) + u' ' + (_cand[0].get('context') or u''))
                    # 标签从"含这个数字的那条子句"里取短语，不再用 14 字窗口：
                    # 窗口实测裁出 '国妇产（科）医院' 这种断头串（v10 页 5）
                    _cl_for_v = u''
                    for _s in ([x for x in (pg.get('source_span') or []) if x] or body):
                        for _c in DR.split_clauses(DR._clean(_s)):
                            if _v in _c and (not _cl_for_v or len(_c) < len(_cl_for_v)):
                                _cl_for_v = _c
                    h = {'value': _v,
                         'unit': (_mu.group(1) if _mu else u''),
                         'label': (DR.phrase_of(_cl_for_v) if _cl_for_v
                                   else _clean_num_ctx(_cand[0].get('context', '')))}
                else:
                    h = {}
            if h.get('label'):
                h['label'] = DR.phrase_of(h['label'], 8)
                # 标签要么是能说明"这个数是什么"的词，要么就别画：
                # 实测画过 '18,'（半截数字）与 '281.4万元'（把标题里的另一个数当标签），
                # 大数字旁边挂这种串，比空着更像排错了版。
                _core = re.sub(u'[\\s0-9.,%％，。、；：·万元亿台个次天家]', u'', h['label'])
                if (len(_core) < 2 or norm_content(h['label']) in _hkey
                        or norm_content(h['label']) in norm_content(str(h.get('value', '')))
                        or not DR.is_clean_phrase(h['label'])
                        or not DL.is_category_label(h['label'])):
                    # '其中按'（挂在 45% 下面，读完不知道按什么）就是这类没主意的串
                    h.pop('label', None)
            if h.get('value') and (zy + zh - y) >= 0.40:
                # 留白区剩不到 0.40 画布高就别画 110pt 大数字：画了就把正文挤出区（G5/G13 实测）
                # 再收一道：画完大数字剩下的地方装不下正文时，砍掉结论短句、数字区收到 0.24
                # （v10 页 217 的 overflow 0.058 就是大数字+结论句+三行正文一起挤出来的）。
                _need_body = sum(line_height_frac(bpt) * est_lines(x, bpt, zw) + 0.012
                                 for x in body)
                _tight = (zy + zh - y - 0.42) < _need_body + 0.06
                if _tight:
                    h.pop('label', None)
                hero_number(slide, add_text, h, [zx, y, zw, 0.24 if _tight else 0.30],
                            pal_dl, ink)
                # 占比类大数字配语料那种圆环：'77.65%' 用环看比裸数字像话，
                # 同级尺的图形化率（ellipses≥1 的页）也靠它补到语料档
                _pv = re.search(u'([-+]?\\d+(?:[.,]\\d+)?)\\s*[%％]',
                                u'%s %s' % (h.get('value', u''), h.get('unit', u'')))
                if _pv and (zy + zh - y) >= 0.34:
                    _frac = float(_pv.group(1).replace(u',', u'')) / 100.0
                    if ring_chart(slide, _frac, [zx + zw - 0.24, y + 0.02, 0.24, 0.24],
                                  pal_dl, ink):
                        rec['ratio_ring'] = True
                # hero 自己占 0.30（数字）+ 0.11（结论短句），只前进 0.34 会让正文压在短句上
                # （实测页 22 的"商品层的毛利接近 99 万元"直接叠在大数字下半截）
                y += 0.26 if _tight else (0.42 if h.get('label') else 0.33)
                drew = 'hero'
                drawn_txt.append(u' '.join([str(h.get('value', '')), str(h.get('unit', '')),
                                            str(h.get('label', ''))]))
                rest = [x for x in body
                        if _clean_num_ctx(x) != _clean_num_ctx(h.get('label', ''))
                        and norm_content(str(h.get('value', ''))) not in norm_content(x)
                        and norm_content(x) not in norm_content(h.get('label', ''))]
                for extra in rest:
                    short = _short_label(extra, TH['display_line_max_chars'])
                    drawn_txt.append(short)
                    stack(balanced_break(short, 17, zw) if len(short) > 24 else short,
                          17, align='l', color=ink['text'])
                body = []
        elif not drew and rec.get('display_mode') == 'refined' and ctype == 'compare':
            pool = [_short_label(x, TH['display_line_max_chars']) for x in body]
            items = DL.items_from_numbers(pool)
            if len(items) >= 2:
                bar_group(slide, items, [zx, y, zw, max(0.0, min(0.52, zy + zh - y))],
                          pal_dl, ink)
                drew = 'bars'
                # 记"条顶真正打上去的那串字"（'46.9 万元'），不要记 float（'46.9'）：
                # 口径不一致时比对判成"没画到"，同一句话既当柱子又当正文再打一遍，
                # G23 那 114 字/页就是这么超的
                drawn_txt.extend((i[2] if isinstance(i, (list, tuple)) and len(i) > 2 else u'')
                                 for i in items)
                body = []
                # 光标只按"这页真的画了多高"前进：写死 0.54 在区高 0.5 的页上
                # 会把后面的正文推到留白区外（v10 页 143 的 overflow 0.127 就是这么来的）；
                # 也要给光标自己留 0.02 的缝，否则 y 正好顶到区下沿再 +0.02 又超出去。
                y += min(0.52, max(0.0, zy + zh - y - 0.02))
        elif not drew and rec.get('display_mode') == 'refined' and ctype in ('list', 'process'):
            # 画"完整的一组并列要点"：标题常是其中一条（'先看现场'），dedupe 已把它吸收进
            # 标题框，body 只剩 2 条够不到 3 张卡 → 图形化率(cards>=3)归 0（#61 培训探针实测）。
            # card_items 是 prose_display 用 parallel_enum（唯一定义尺）取出的整组，优先用它。
            _cards_src = rec.get('card_items') or body
            if len(_cards_src) >= 3:
                cards = [_short_label(x, TH['display_line_max_chars']) for x in _cards_src[:3]]
                if card_grid(slide, cards, [zx, y, zw, max(0.0, min(0.56, zy + zh - y))],
                             pal_dl, ink, add_text):
                    drew = 'cards'
                    drawn_txt.extend(cards)
                    body = []
        if drew:
            # 版式画掉的那几张图里没出现的数字，必须还留在文字里：G24 是逐事实核成品的，
            # 上一版 hero 一画就把 display_lines 整段丢掉，67 个事实从屏上消失
            _seen = re.sub(r'\s', '', u' '.join(drawn_txt))
            keep = []
            for x in list(pg.get('display_lines') or [])[:TH['display_lines_max']]:
                xs = (x if isinstance(x, str) else x.get('text', '')).strip()
                if not xs or _dup_of_head(xs):
                    continue
                vals = [re.sub(r'\s', '', k['value'])
                        for k in DR.key_numbers(xs, 6) if k.get('value')]
                if vals and any(v and v not in _seen for v in vals):
                    keep.append(xs)
            # 图形页（大数字/数字排/条形组/指标卡）不再把没画掉的事实塞回正文，也不叠
            # 第二行筹码：下面安全网对这类页的判据就是"明细进演讲者备注"（G24 计备注，
            # 事实一条不丢）。上一版这里照画，于是同一页既有数字排又有两行筹码——
            # 真渲染页 6 目检抓出，每页字数 p90 被顶到 46（语料好示范 39.5）。
            if drew not in GRAPHIC_LAYOUTS:
                body = (body + [k for k in keep if k not in body])[:TH['display_lines_max']]
        if (drew and keep and drew not in GRAPHIC_LAYOUTS
                and (zy + zh - y) >= 0.16 and len(keep) >= 2):
            # 版式没画掉的事实改走筹码行（每个 ≤12 字），不再用整句占正文预算：
            # 显示契约收到 ≤2 行 × ≤14 字后，句子会把每页字数中位数顶回语料的 2 倍以上，
            # 而筹码 3 个才 ~30 字且一个事实都没少（G24 逐事实核成品）。
            chips = []
            for k in keep:
                # 顺序很要紧：_short_label 会优先取"带数字的那个子句"，
                # phrase_of 却把数字抹掉只留词——先抹数字的写法实测让 '1,053 个' 这类
                # 要举证的事实从筹码上消失（G24 点名，筹码却"看起来补齐了"）。
                _c = _short_label(k, TH['display_line_max_chars'])
                if not re.search(u'\\d', _c or u''):
                    _c = DR.phrase_of(k, TH['display_line_max_chars'] - 4) or _c
                # 筹码标签不许把标题原句再抄一遍（同页重影）：判据只在 chip_dupes_head 一处。
                if _c and not chip_dupes_head(_c, head):
                    chips.append(_c)
            if (zy + zh - y >= 0.16
                    and DL.chip_strip(slide, chips, [zx, min(y, zy + zh - 0.145), zw, 0.14],
                                      pal_dl, ink, add_text)):
                drawn_txt.extend(chips)
                y += 0.16
                body = [x for x in body if x not in keep][:TH['display_lines_max']]
                keep = []
        if drew:
            rec['data_layout'] = drew
        # 提炼册：每条上屏的短句都要过显示契约（≤display_lines_max 条 × ≤line_max 字）。
        # 旧写法把 keep 回来的整句原样打上去，页均 43 字，G22 的 p50 维度就是这么被拖死的；
        # 留档册（--verbatim / 非 refined）不动，那是给人查账用的。
        refined = rec.get('display_mode') == 'refined'
        lim = TH['display_line_max_chars']
        shown = body[:TH['display_lines_max']] if refined else body
        for extra in shown:
            one = _short_label(extra, lim) if (refined and len(extra) > lim) else extra
            stack(balanced_break(one, bpt, zw) if len(one) > 24 else one,
                  bpt, align='c' if _sparse else 'l', color=ink['text'])
    # 最后一道安全网：这一页原文里的事实单元，凡是屏幕上没打出来的，改画成筹码补上。
    # 补不完就如实记进 facts_undrawn 让 G24 点名——上一版是静默丢：
    # v10 实测条形组只画四根、标题让位、正文超区这几条路各自吞掉一批事实，全册漏 26 个
    # （'500人'、'15.00万'、'706台' 这种对外要举证的数字）。
    # 屏幕上到底有什么，以这一页 XML 里的文字为准（条形组的值与柱底标签是 add_textbox
    # 直接写的，不走 stack；只记 stack 会把它们当成"没画"，于是筹码又补一遍同样的数）
    _shown_n = norm_content(u' '.join(
        [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame]))
    _src_txt = u' '.join([head] + [x for x in (pg.get('source_span') or pg.get('body') or []) if x])
    _miss = []
    for _f in DR.fact_units(_src_txt, (pg.get('entities') or [])[:8]):
        _fn = norm_content(_f)
        if _fn and len(_fn) >= 2 and _fn not in _shown_n and _f not in _miss:
            _miss.append(_f)
    _left = list(_miss)
    if _miss and rec.get('display_mode') == 'refined':
        # 位置只有一个筹码行：谁先上屏要按"对外要举证的重量"排，不是按原文先后。
        # v10 页95 实测：'2019 年' 占了格子，'706 台' 反而没地方画（G24 点名、facts_undrawn 记着）。
        def _fact_rank(f):
            s = norm_content(f)
            if re.search(u'(万元|亿元|元)$', s):
                return 0
            if re.search(u'(%|％)$', s):
                return 1
            if re.search(u'(台|个|家|人|次|例|天|城|店|倍|条|辆)$', s):
                return 2
            if re.search(u'(年|月|日)$', s):
                return 5
            return 3
        _miss = sorted(_miss, key=lambda x: (_fact_rank(x), -len(norm_content(x))))
        _left = list(_miss)
        _lab = []
        for _f in _miss:
            # 带这个数的子句往往有好几条：挑"最能说明这数是什么"的那条，不是最短的那条。
            # 旧写法取最短子句，v10 页 20 的 '500' 落到了 '这个 500 要按原样读' 这种元话上，
            # 筹码就成了 '原样读 500'——而正文里那句 '为不少于 500 名女性提供乳腺' 才是事实本体
            _cl, _best = u'', -1.0
            for _s in [x for x in (pg.get('source_span') or pg.get('body') or []) if x] + [head]:
                for _c in DR.split_clauses(DR._clean(_s)):
                    _c = DR._clean(_c)
                    if _f not in _c:
                        continue
                    _sg = _c.split(_f)
                    _sc = (2.0 if DL.is_category_label(DR.phrase_of(_sg[0], 6)) else 0.0) + \
                          (1.0 if len(_sg) > 1 and re.match(u'^\\s*[' + DL.MEASURE_CHARS +
                                                            DL.MEASURE_APPROX + u']',
                                                            _sg[1]) else 0.0) + \
                          min(1.0, len(_c) / 40.0)
                    if _sc > _best:
                        _best, _cl = _sc, _c
            _fit = TH['display_line_max_chars'] - 2
            # 筹码上的字必须带着这个数本身：v10 实测页44 落的是 '一个可预测'、'稳定输出'，
            # 而缺的那个事实是 '1,053 个'——phrase_of 专挑数字前的词，把要举证的数抹掉了，
            # G24 于是永远补不上（补的动作做了，补的内容没上屏）。
            if _cl and len(_cl) <= _fit:
                _one = _cl
            else:
                _seg = _cl.split(_f) if (_cl and _f in _cl) else [u'', u'']
                _pre = DR.phrase_of(_seg[0], 6)
                _pre = _pre if DL.is_category_label(_pre) else u''
                _post = DR.phrase_of(_seg[1] if len(_seg) > 1 else u'', 6)
                if _post and DL.is_category_label(_post):
                    # 数字后面那截必须断在词边界上：'50 多家分娩量太低的机构' 里
                    # '分娩量太' 是硬截出来的半截定语，上屏就是读不通的断头词
                    _pi = (_seg[1] if len(_seg) > 1 else u'').find(_post) + len(_post)
                    _pnxt = (_seg[1] if len(_seg) > 1 else u'')[_pi:_pi + 1]
                    _post = u'' if re.match(u'[一-龥]', _pnxt or u'') else _post
                else:
                    _post = u''
                _bare = not re.search(u'[一-龥%％]', _f[-2:] or u'')
                _txt = _f
                if _bare:
                    # 裸数字先跟原文紧挨的量词：'500 名女性'、'50 多家'。
                    # 约数词（多/余/几）只跟一个字（'多家'），实量词跟两个字（'名女性'）；
                    # 跟多了就会把下一个词的词头切进来（'多家分'）
                    _sp = (_seg[1] if len(_seg) > 1 else u'').strip()
                    _m1 = re.match(u'^([' + DL.MEASURE_CHARS + DL.MEASURE_APPROX + u'])', _sp)
                    _tail = u''
                    if _m1:
                        _take = 1 if _m1.group(1) in DL.MEASURE_APPROX else 2
                        _mt = re.match(u'^[' + DL.MEASURE_CHARS + DL.MEASURE_APPROX +
                                       u']([一-龥]{1,%d})' % _take, _sp)
                        if _mt:
                            _tail = _m1.group(1) + _mt.group(1)
                    if _tail:
                        _txt = u'%s %s' % (_f, _tail)
                    elif _post:
                        _txt = u'%s %s' % (_f, _post)
                _cand = (u'%s %s' % (_pre, _txt)).strip() if _pre else _txt.strip()
                if len(re.sub(r'\s', u'', _cand)) > _fit:
                    _cand = _txt.strip()
                if len(re.sub(r'\s', u'', _cand)) > _fit:
                    _cand = _f
                _one = _cand
                if norm_content(_f) not in norm_content(_one):
                    _one = _f
            if _one not in _lab:
                _lab.append(_one)
        # 一行短句只要 0.10 高；筹码行要 0.14+间距。旧写法统一要 0.16，
        # 于是"就差一点点"的页把 '744 万元' 这类要举证的数记成没画上（G24 点名）。
        _net_room = zy + zh - y
        if _lab and drew in GRAPHIC_LAYOUTS:
            # 这页已经用图形承载了主数据（大数字 / 数字排 / 条形组 / 指标卡）：其余事实不再刷成
            # 筹码行——会顶出 G8 孤字、把每页字数 p50/p90 顶高。语料那种"一个主视觉 + ≤2 短句"
            # 是把没进图形的明细收进演讲者备注（G24 计备注，事实不丢）。
            _left = list(_lab)
        elif _lab and _net_room >= 0.10:
            _cy = min(y, zy + zh - 0.145)
            if len(_lab) == 1:
                # 只落一个事实时 chip_strip 不成立（它要 ≥2 个才成一行），
                # 就按一行短句打上去——画不出来不等于可以悄悄丢
                stack(_lab[0], 17, align='l', color=ink['text'], name='caption')
                _left = []
            else:
                # 一行四枚（语料那种"很多短文本框"）：屏幕上只排这一行；
                # SPEC §16「卡片明细移备注（G24 计备注）」——装不下的事实不再堆第二行筹码，
                # 全部进演讲者备注。G24 按含备注的全簿文本核，事实一条不丢；parity 的 chars
                # 只数屏幕形状，这样数据页才回得到语料那种"一个大数 + ≤2 短句"的稀疏水位。
                _made = DL.chip_strip(slide, _lab, [zx, _cy, zw, 0.14], pal_dl, ink,
                                      add_text, max_chips=4)
                if _made:
                    y = min(y + 0.16, zy + zh)
                    _left = _lab[_made:]
                else:
                    _left = list(_lab)
    if _left and rec.get('display_mode') == 'refined':
        # 屏幕放不下的事实移进演讲者备注（不是藏进不可见形状）：notesSlides 会被 G24/G13 数到，
        # 也会随演讲者视图放映，读者/讲者都看得见，只是不刷屏。
        _tf = slide.notes_slide.notes_text_frame
        _add = u'\n'.join(x[:20] for x in _left)
        _tf.text = (_tf.text + u'\n' + _add) if (_tf.text or u'').strip() else _add
        rec['facts_in_notes'] = len(_left)
    elif _left:
        rec['facts_undrawn'] = [x[:20] for x in _left]
    if 'shown_texts' in rec:
        del rec['shown_texts']
    rec['body_total'] = len([x for x in (pg.get('body') or []) if x])
    rec['body_placed'] = len(body) + absorbed
    rec['body_in_headline'] = absorbed
    rec['anim_spids'] = anim_ids
    if n == 0:
        rec['notes'].append('该页无文字层')
    if y > zy + zh + 0.005:
        rec['overflow'] = round(y - (zy + zh), 4)
        rec['notes'].append('文字超出留白区 %.3f：应拆页而不是缩字号' % rec['overflow'])
    # 技法卡的最后一道诚实账要在 build 里改判（那时才知道这页最终挂的是哪张卡），
    # 这里只把成品这页真实量到的最大字号记下来（pt；TH 用的是 OOXML 的 1/100pt）。
    _mx_sz = 0
    for _sh in slide.shapes:
        if not _sh.has_text_frame:
            continue
        for _pp in _sh.text_frame.paragraphs:
            for _rr in _pp.runs:
                try:
                    _mx_sz = max(_mx_sz, int(round(_rr.font.size.pt))
                                 if _rr.font.size else 0)
                except Exception:
                    pass
    rec['max_font_pt'] = _mx_sz
    rec['max_font_sz'] = _mx_sz * 100        # 与 RECIPE_TOKENS/G6 同一个 1/100pt 口径
    return n


# ---------------------------------------------------------------- render


def render_pptx(pptx_path, outdir, width=1600):
    """WPS is the only renderer that opens these files on this box (PowerPoint COM fails 0x80070570)."""
    import pythoncom
    import win32com.client
    os.makedirs(outdir, exist_ok=True)
    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("KWPP.Application")
    for attr, val in (('Visible', False), ('DisplayAlerts', 0)):
        try:
            setattr(app, attr, val)
        except Exception:
            pass
    n = 0
    pr = None
    try:
        pr = app.Presentations.Open(os.path.abspath(pptx_path), ReadOnly=True, WithWindow=False)
        n = pr.Slides.Count
        for k in range(1, n + 1):
            pr.Slides(k).Export(os.path.abspath(os.path.join(outdir, 'p%03d.png' % k)),
                                "PNG", width, int(width * 9 / 16))
        pr.Close()
    finally:
        try:
            if pr is not None:
                pr.Close()
        except Exception:
            pass
        try:
            app.Quit()
        except Exception:
            pass
    return n


def rel_lum(v):
    """WCAG relative luminance of an 8-bit RGB tuple."""
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in v)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_pair(c1, c2):
    l1, l2 = rel_lum(c1), rel_lum(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)

def shape_kind(sp):
    """python-pptx raises NotImplementedError on shapes whose type it cannot classify
    (a plain <p:sp> without txBox, which WPS and most template decks contain). A gate must not
    die on a user's real file, so unreadable types come back as None instead."""
    try:
        return int(sp.shape_type)
    except Exception:
        return None


def deck_bg_dims(pptx_path):
    """{slide_index: (w, h)} of the picture actually embedded as each slide's background.

    G3 used to trust the numbers in manifest.jsonl, so a 1024px ImageGen still that had been
    re-encoded into the deck passed a "短边≥1920" gate it never met. Measure the deliverable.
    """
    import zipfile
    from xml.etree import ElementTree as ET
    A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
    R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
    try:
        z = zipfile.ZipFile(pptx_path)
    except Exception:
        return {}
    out = {}
    try:
        from PIL import Image
        names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
        for n in sorted(names, key=lambda s: int(re.search(r'(\d+)', os.path.basename(s)).group(1))):
            si = int(re.search(r'(\d+)', os.path.basename(n)).group(1))
            try:
                x = ET.fromstring(z.read(n))
                rels = ET.fromstring(z.read('ppt/slides/_rels/%s.rels' % os.path.basename(n)))
            except Exception:
                continue
            rid_map = {r.get('Id'): r.get('Target') for r in rels}
            best = None
            for blip in x.iter(A + 'blip'):
                rid = blip.get(R + 'embed') or blip.get(R + 'link')
                tgt = rid_map.get(rid)
                if not tgt:
                    continue
                mp = os.path.normpath(os.path.join('ppt/slides', tgt)).replace('\\', '/')
                if mp not in z.namelist():
                    continue
                try:
                    im = Image.open(io.BytesIO(z.read(mp)))
                    dims = im.size
                except Exception:
                    continue
                if best is None or dims[0] * dims[1] > best[0] * best[1]:
                    best = dims
            if best:
                out[si] = best
    finally:
        z.close()
    return out


def frame0_vs_poster(mp4, poster):
    """Mean absolute difference (0-255) between the video's first frame and the poster image.

    G15 previously only asked "is there a png next to the mp4". The audited deck paired a
    cover still of an arrow with a video of a rocket (MAD 59.4, corr 0.455) and still called the
    first-frame chain intact. Image-to-video is only honest if the two are the same picture.
    """
    try:
        import numpy as np
        a, b = _frame0_arrays(mp4, poster)
        if a is None:
            return None
        return round(float(np.abs(a - b).mean()), 2)
    except Exception:
        return None


def _frame0_arrays(mp4, poster):
    import imageio_ffmpeg
    import numpy as np
    from PIL import Image
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    tmp = os.path.join(tempfile.gettempdir(), 'caofeng_frame0.png')
    if os.path.exists(tmp):
        os.remove(tmp)
    subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', mp4,
                    '-frames:v', '1', '-s', '640x360', tmp], capture_output=True)
    if not os.path.exists(tmp):
        return None, None
    a = np.asarray(Image.open(tmp).convert('RGB').resize((320, 180)), dtype=np.float64)
    b = np.asarray(Image.open(poster).convert('RGB').resize((320, 180)), dtype=np.float64)
    return a, b


def frame0_norm_mad(mp4, poster):
    """亮度归一后的首帧差（标准差单位）：即梦图生视频的"起势"是暗场，raw MAD 会把
    同构图的暗/亮两态判成跑偏（实测同页 34.15 / 错配 39.16，分不开）；归一后同页 0.437 /
    错配 0.755 才分得开。与 raw MAD 合用：两者都超阈才判跑偏。"""
    try:
        import numpy as np
        a, b = _frame0_arrays(mp4, poster)
        if a is None:
            return None
        an = (a - a.mean()) / (a.std() + 1e-6)
        bn = (b - b.mean()) / (b.std() + 1e-6)
        return round(float(np.abs(an - bn).mean()), 3)
    except Exception:
        return None


def tail_freeze_metrics(mp4, poster=None):
    """循环背景接缝实测（教程 ae56a65b[34-53]：自动播放＋循环不跳帧）。

    返回 (tail_mad, loop_mad)：
    - tail_mad：末 0.5s 帧间差（信息量，片尾运动强度，不作硬判据——ping-pong 循环的"尾"
      是倒放的开头，本来就在动，但接缝连续不跳）；
    - loop_mad：**末帧 vs 同一视频首帧**的差——这才是 repeatCount 循环接缝会不会跳帧的尺子。
      循环播完回到首帧，末帧≠首帧就在接缝处跳一下；ping-pong（正放+倒放）末帧=首帧→loop_mad≈0。
      不拿 gen/image.png 当尺子：那张静帧可能不是视频真首帧（实测 p005 静帧与视频首帧差 43、
      来源还标错成 p011），拿它量会把"静帧没刷新"误判成"视频不循环"。
    阈值 loop_mad≤2（v10 真交付标定 0.1–0.7）；量不出返回 (None, None)，不许把 None 当通过。
    """
    try:
        import imageio_ffmpeg
        import numpy as np
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        info = subprocess.run([ff, '-hide_banner', '-i', mp4], capture_output=True,
                              text=True, errors='replace')
        fm = re.search(r'([\d.]+)\s*fps', info.stderr or '')
        fps = float(fm.group(1)) if fm else 24.0
        # 全帧解码缩到 320x180 取首末帧——比 -ss/-sseof 在短片尾靠近 EOF 时稳（实测 2s 片
        # 最后一帧 pts 1.917 < dur-0.05=1.95，seek 直接取空）。背景视频 5–10s，解码量很小。
        raw = subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-i', mp4,
                              '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '320x180', '-'],
                             capture_output=True).stdout
        fb = 320 * 180 * 3
        n = len(raw) // fb
        if n < 2:
            return None, None
        frames = np.frombuffer(raw[:n * fb], dtype=np.uint8).reshape(n, 180, 320, 3).astype(np.float64)
        last = frames[-1]
        first = frames[0]
        pre = frames[max(0, n - 1 - int(round(0.5 * fps)))]
        tail_mad = round(float(np.abs(last - pre).mean()), 2)
        loop_mad = round(float(np.abs(last - first).mean()), 2)
        return tail_mad, loop_mad
    except Exception:
        return None, None


MEDIA_VIDEO_EXT = ('.mp4', '.mov', '.m4v', '.avi', '.wmv', '.mkv')
MEDIA_IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tif', '.tiff', '.webp', '.apng')


def deck_media_index(pptx_path):
    """{slide_no: {'video': [(part, sha1, bytes)], 'picture': [(part, sha1, bytes)]}}

    Reads the bytes actually shipped inside the .pptx, per slide (via each slide's .rels).
    Gates compare these with the source files they validated, so replacing an mp4/GIF after build,
    or embedding a picture that is not the one fetched, fails instead of passing on manifest text.
    """
    import zipfile
    from xml.etree import ElementTree as ET
    out = {}
    try:
        z = zipfile.ZipFile(pptx_path)
    except Exception:
        return out
    try:
        names = set(z.namelist())
        cache = {}

        def part_hash(name):
            if name not in cache:
                try:
                    b = z.read(name)
                    cache[name] = (hashlib.sha1(b).hexdigest(), len(b))
                except Exception:
                    cache[name] = (None, 0)
            return cache[name]

        slide_files = sorted([n for n in names if re.match(r'ppt/slides/slide\d+\.xml$', n)],
                             key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
        for si, f in enumerate(slide_files, 1):
            rel = 'ppt/slides/_rels/%s.rels' % os.path.basename(f)
            vids, pics = [], []
            if rel in names:
                try:
                    rx = ET.fromstring(z.read(rel))
                except Exception:
                    rx = None
                for r in (rx or []):
                    t = (r.get('Target') or '').replace('\\', '/')
                    if t.startswith('../'):
                        t = 'ppt/' + t[3:]
                    elif not t.startswith('ppt/'):
                        t = 'ppt/slides/' + t
                    if t not in names:
                        continue
                    ext = os.path.splitext(t)[1].lower()
                    h, nb = part_hash(t)
                    if ext in MEDIA_VIDEO_EXT:
                        vids.append((t, h, nb))
                    elif ext in MEDIA_IMAGE_EXT:
                        pics.append((t, h, nb))
            out[si] = {'video': vids, 'picture': pics}
    finally:
        z.close()
    return out


def spPr_order_defects(pptx_path):
    """CT_SpPr 的子元素顺序必须是 xfrm → 几何 → fill → ln → effectLst → scene3d → sp3d → extLst。

    Why: PowerPoint 对顺序不符的 spPr 直接弹"该内容需要修复"，而 WPS 宽容到能渲染——只用
    WPS 截图验收会一路绿灯，交付给用户却在真 PowerPoint 里坏掉。手写注入（scrim 的 gradFill、
    立体展台的 sp3d）最容易踩这条，所以按成品逐形状量一遍。
    """
    from xml.etree import ElementTree as ET
    import zipfile
    RANK = {'xfrm': 0, 'custGeom': 1, 'prstGeom': 1, 'noFill': 2, 'solidFill': 2, 'gradFill': 2,
            'blipFill': 2, 'pattFill': 2, 'grpFill': 2, 'ln': 3, 'effectLst': 4, 'effectDag': 4,
            'scene3d': 5, 'sp3d': 6, 'extLst': 7}
    bad = []
    try:
        z = zipfile.ZipFile(pptx_path)
    except Exception:
        return bad
    try:
        names = sorted([n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)],
                       key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
        for si, n in enumerate(names, 1):
            try:
                x = ET.fromstring(z.read(n))
            except Exception:
                continue
            for sp in list(x.iter()):
                if not sp.tag.endswith('}sp') and not sp.tag.endswith('}pic'):
                    continue
                spPr = sp.find('{%s}spPr' % P)
                if spPr is None:
                    continue
                ranks, seen = [], {}
                for c in spPr:
                    loc = c.tag.rpartition('}')[2]
                    if loc not in RANK:
                        continue
                    ranks.append((RANK[loc], loc))
                    seen[loc] = seen.get(loc, 0) + 1
                nm_el = sp.find('.//{%s}cNvPr' % P)
                nm = nm_el.get('name') if nm_el is not None else '?'
                dup = [k for k in ('xfrm', 'prstGeom', 'custGeom', 'ln', 'effectLst', 'effectDag',
                                   'scene3d', 'sp3d') if seen.get(k, 0) > 1]
                if sum(seen.get(k, 0) for k in RANK if RANK[k] == 2) > 1:
                    dup.append('fill')
                if dup:
                    bad.append({'slide': si, 'shape': nm, 'why': '重复元素 %s' % dup})
                elif [r for r, _ in ranks] != sorted(r for r, _ in ranks):
                    bad.append({'slide': si, 'shape': nm,
                                'why': '子元素顺序 %s 不符合 CT_SpPr（应为 xfrm→几何→fill→ln→effect→3D）'
                                       % ','.join(l for _, l in ranks)})
    finally:
        z.close()
    return bad


_THEME_CACHE = {}


def theme_accent1_of(pptx_path):
    """成品 theme1.xml 的 accent1（G16/RECIPE_TOKENS 用同一份读数，不各算各的）。"""
    key = (pptx_path, os.path.getmtime(pptx_path) if os.path.exists(pptx_path) else 0)
    if key in _THEME_CACHE:
        return _THEME_CACHE[key]
    import zipfile
    from xml.etree import ElementTree as ET
    val = None
    try:
        z = zipfile.ZipFile(pptx_path)
        th = sorted(n for n in z.namelist() if re.match(r'ppt/theme/theme\d+\.xml$', n))
        if th:
            root = ET.fromstring(z.read(th[0]))
            A_ = '{%s}' % A
            sch = root.find('.//' + A_ + 'clrScheme')
            if sch is not None:
                for c in sch:
                    if c.tag == A_ + 'accent1':
                        srgb = c.find(A_ + 'srgbClr')
                        val = (srgb.get('val') or '').upper() if srgb is not None else None
        z.close()
    except Exception:
        val = None
    _THEME_CACHE[key] = val
    return val


def deck_motion_facts(pptx_path):
    """{slide_no: {transitions, trans_kinds, morph_ms, anim_nodes, entr_nodes, anim_motion,
    has_sp3d, has_reflection, has_video, has_gif}} read from XML.

    G6/G14 measure the deliverable: a plan that says "motion: 平滑推镜" proves nothing until the
    p:transition/p:timing tree is actually in the file.
    """
    from xml.etree import ElementTree as ET
    import zipfile
    P_ = '{%s}' % P
    A_ = '{%s}' % A
    DUR14 = '{%s}dur' % P14
    out = {}
    midx = deck_media_index(pptx_path)
    try:
        z = zipfile.ZipFile(pptx_path)
    except Exception:
        return out
    try:
        names = sorted([n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)],
                       key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
        for si, n in enumerate(names, 1):
            try:
                x = ET.fromstring(z.read(n))
            except Exception:
                continue
            morph_ms = None
            n_tr = 0
            tr_kinds = []
            for tr in x.iter(P_ + 'transition'):
                n_tr += 1
                for c in tr:
                    loc = c.tag.rpartition('}')[2]
                    if loc not in tr_kinds:
                        tr_kinds.append(loc)
                if any(c.tag.endswith('morph') for c in tr.iter()):
                    d = tr.get(DUR14)
                    morph_ms = int(d) if (d or '').isdigit() else 2000
            nodes = [c for c in x.iter(P_ + 'cTn') if c.get('presetClass')]
            entr = [c for c in nodes if c.get('presetClass') in ('entr', 'emph', 'path')]
            emph = [c for c in nodes if c.get('presetClass') == 'emph']
            # 最大内嵌图的画布占比：分栏原型的判据（满幅≈1.0，实测分栏 0.35×0.53）
            pic_frac, pic_w = 0.0, 0.0
            for pic in x.iter(P_ + 'pic'):
                ext = pic.find('.//' + A_ + 'xfrm/' + A_ + 'ext')
                if ext is None:
                    continue
                cx, cy = (v for v in (ext.get('cx'), ext.get('cy')))
                if not (cx or '').isdigit() or not (cy or '').isdigit():
                    continue
                fr = (int(cx) * int(cy)) / float(EMU_W * EMU_H)
                if fr > pic_frac:
                    pic_frac, pic_w = fr, int(cx) / float(EMU_W)
            m = midx.get(si, {})
            out[si] = {'transitions': n_tr, 'trans_kinds': tr_kinds, 'morph_ms': morph_ms,
                       'pic_max_frac': pic_frac, 'pic_max_w': pic_w,
                       'theme_accent1': theme_accent1_of(pptx_path),
                       'anim_nodes': len(nodes), 'entr_nodes': len(entr),
                       'emph_nodes': len(emph),
                       'anim_motion': len(list(x.iter(P_ + 'animMotion'))),
                       'anim_scale': len(list(x.iter(P_ + 'animScale'))),
                       'has_sp3d': any(True for _ in x.iter(A_ + 'sp3d')),
                       'has_duotone': any(True for _ in x.iter(A_ + 'duotone')),
                       'has_reflection': any(True for _ in x.iter(A_ + 'reflection')),
                       'has_video': bool(m.get('video')),
                       'has_scrim': any(True for _ in x.iter(A_ + 'gradFill')),
                       'max_font_sz': max([int(v) for v in
                                           [e.get('sz') for e in x.iter()
                                            if e.tag.endswith('}rPr')
                                            or e.tag.endswith('}defRPr')]
                                           if (v or '').isdigit()] or [0]),
                       'has_gif': any(e[0].lower().endswith(('.gif', '.apng', '.webp'))
                                      for e in (m.get('picture') or []))}
    finally:
        z.close()
    return out


def check_g3_media(path, res, hard, shots, build_log, work):
    """Measure the media that is actually inside the deliverable, per page kind.

    manifest.jsonl only proves how big the original download was; the audited deck embedded
    1024/1080/1350-pixel pictures while G3 reported "短边≥1920 全部合格". Rules split by kind:
      * 照片/静帧背景页：内嵌图短边必须 ≥TH['embed_short_edge']，否则铺满 1080p 就是糊的；
      * 视频背景页：量 mp4 本身的分辨率/时长，并要求内嵌字节与验收过的源文件 SHA1 一致；
      * 动图页：成品里必须真带一个多帧 gif（不是静图换的壳）；
      * 矢量色场（out/bg_fallback 下生成的）本身就是 1920x1080 画的，不参与像素判定。
    """
    dims = deck_bg_dims(path)
    kind = {s['page']: s.get('asset_kind') for s in (shots.get('pages') or [])}
    field_pages = set()
    for r in build_log or []:
        bg = r.get('bg') or ''
        if 'bg_fallback' in bg.replace('\\', '/'):
            field_pages.add(r.get('page'))
    midx = deck_media_index(path) if os.path.exists(path) else {}
    soft = []
    for si, (w, h) in sorted(dims.items()):
        if si in field_pages or kind.get(si) == 'video_bg':
            continue
        if min(w, h) < TH['embed_short_edge']:
            soft.append({'page': si, 'w': w, 'h': h, 'short_edge': min(w, h)})
    low_video = []
    bind_bad = []
    sha_cache = {}

    def fsha(p):
        if p not in sha_cache:
            try:
                sha_cache[p] = sha1(p)
            except Exception:
                sha_cache[p] = None
        return sha_cache[p]

    for s in (shots.get('pages') or []):
        n = s['page']
        if s.get('asset_kind') == 'video_bg':
            vf = find_gen(work, n, 'video')
            if not vf:
                continue
            d = media_dims(vf)
            if d and min(d) < TH['embed_short_edge']:
                low_video.append({'page': n, 'w': d[0], 'h': d[1]})
            emb = midx.get(n, {}).get('video') or []
            if not emb:
                bind_bad.append({'page': n, 'why': '成品这一页里没有视频部件（视频没装进去）'})
            elif fsha(vf) not in [e[1] for e in emb]:
                bind_bad.append({'page': n, 'why': '内嵌 mp4 的字节和验收过的 %s 不是同一份（建完被人换过）'
                                         % os.path.basename(vf)})
        if s.get('asset_kind') == 'gif' or kind.get(n) == 'gif':
            gf = find_gen(work, n, 'gif')
            if not gf:
                continue
            gi = gif_info(gf)
            if not gi or gi[2] < TH['gif_frames_min']:
                continue        # 源文件本身不合格由 G11 报，这里只管绑定
            emb = [e for e in (midx.get(n, {}).get('picture') or [])
                   if e[0].lower().endswith(('.gif', '.apng', '.webp'))]
            if not emb:
                bind_bad.append({'page': n, 'why': '成品这一页里没有动图部件（GIF 没装进去）'})
            elif fsha(gf) not in [e[1] for e in emb]:
                bind_bad.append({'page': n, 'why': '内嵌 GIF 的字节和验收过的动图不是同一份'})
    res['G3b_成品内嵌分辨率'] = {'slides_with_bg': len(dims),
                                 'field_pages_exempt': sorted(field_pages),
                                 'min_short_edge': TH['embed_short_edge'],
                                 'soft_picture_pages': soft, 'low_res_videos': low_video,
                                 'embedded_media_binding': bind_bad,
                                 'embedded_short_edge_min': min([min(v) for v in dims.values()], default=0)}
    if soft:
        hard.append("G3: 成品里 %d 页把短边<%d 的照片当整页背景（页%s，最短边 %s）"
                    "——铺到 1920x1080 就是糊的，manifest 里写多大都没用" % (
                        len(soft), TH['embed_short_edge'], [s['page'] for s in soft][:6],
                        soft[0]['short_edge']))
    if low_video:
        hard.append("G3: %d 页视频背景的分辨率不足短边 %d（%s）——投放时要在即梦里选高清档" % (
            len(low_video), TH['embed_short_edge'], low_video[:3]))
    if bind_bad:
        hard.append("G3: %d 页的视频/动图与成品内嵌字节对不上（%s）——验收过的素材必须真的在片子里，"
                    "建完之后偷换媒体同样算不合格" % (len(bind_bad), bind_bad[:3]))
    return dims



def gif_is_transparent(path):
    """True if the animated image carries an alpha/transparency index (so it can't show a box)."""
    try:
        from PIL import Image
        im = Image.open(path)
        im.seek(0)
        if im.info.get('transparency') is not None:
            return True
        if 'A' in im.mode:
            return True
        p = im.convert('RGBA')
        return any(q[3] < 250 for q in (p.getpixel((1, 1)), p.getpixel((p.size[0] - 2, 1)),
                                        p.getpixel((1, p.size[1] - 2))))
    except Exception:
        return False


def gif_overlay_defect(gif_path, png_path, box):
    """(可见方框风险, 说明) —— 量"框外一圈"与 GIF 边角色（四角，不是全帧均值）的色差。

    这条只作为**报告里的证据**，不当硬闸：实测纯黑底 GIF 叠在深蓝矢量色场上，人眼一看就是
    一块方框，但色差只有 ~5/255（色场本身就很暗）——用这个数当判据既会漏报也会误报，
    所以硬判据改成确定性的两条：底色不透明 + 身后是矢量色场 = 不合格（见 G11）。
    """
    try:
        from PIL import Image
        import numpy as np
        g = Image.open(gif_path)
        g.seek(0)
        gr = g.convert('RGB')
        w, h = gr.size
        corners = np.array([gr.getpixel(p) for p in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3))],
                           dtype=np.float64)
        edge = np.median(corners, axis=0)
        im = Image.open(png_path).convert('RGB')
        W, H = im.size
        x0, y0 = box[0] * W, box[1] * H
        x1, y1 = (box[0] + box[2]) * W, (box[1] + box[3]) * H
        t = max(3, int(0.012 * min(W, H)))
        ring = []
        for (ax, ay, bx, by) in ((x0 - t, y0, x1, y0), (x1, y0, x1 + t, y1),
                                 (x0, y1, x1, y1 + t), (x0 - t, y0, x0, y1)):
            ax, ay = max(0, int(ax)), max(0, int(ay))
            bx, by = min(W, int(bx)), min(H, int(by))
            if bx > ax and by > ay:
                ring.append(np.asarray(im.crop((ax, ay, bx, by)), dtype=np.float64))
        if not ring:
            return 0.0, '框外无可采样像素'
        outside = np.concatenate([r.reshape(-1, 3) for r in ring], axis=0)
        med = np.median(outside, axis=0)
        diff = float(np.abs(med - edge).mean())
        return diff, 'GIF 边色 (%d,%d,%d) vs 框外中位 (%d,%d,%d)，差 %.1f/255' % (
            int(edge[0]), int(edge[1]), int(edge[2]), int(med[0]), int(med[1]), int(med[2]), diff)
    except Exception as exc:
        return 0.0, '读不出来（%s）' % str(exc)[:50]



N_COVERAGE_INVARIANTS = 7   # coverage_invariants 里的判据条数（增删判据时同步这里与 SKILL 的说法）


def coverage_invariants(cov):

    """The study-coverage promises, as one checkable list (G9 and the self-test share it).

    Kept out of cmd_check so a test can feed a broken ledger through the *same* code path the
    gate uses -- asserting the arithmetic in the test file would prove nothing about the gate.

    条数记在 N_COVERAGE_INVARIANTS，G9 报告与 SKILL 的说法都以它为准（曾写死 6 而实装有 7 条）。
    """
    bad = []
    if cov.get('rendered_decks') != cov.get('decks_total'):
        bad.append("渲染模板数 %s≠台账 %s" % (cov.get('rendered_decks'), cov.get('decks_total')))
    if cov.get('rendered_slides') != cov.get('slides_total'):
        bad.append("渲染页数 %s≠台账 %s" % (cov.get('rendered_slides'), cov.get('slides_total')))
    if cov.get('render_matches_ledger') is not True:
        bad.append("render_matches_ledger 不是 true")
    if cov.get('videos_frames_extracted') != cov.get('unique_videos'):
        bad.append("抽帧视频数 %s≠去重视频数 %s（有视频没被看过）" % (
            cov.get('videos_frames_extracted'), cov.get('unique_videos')))
    if cov.get('videos_failed'):
        bad.append("视频处理失败 %s 条" % cov.get('videos_failed'))
    # 需求 2 的"提示词一律学会"必须可核对：以前这里写死 `prompt_blocks != 52`，
    # 删掉一个字节级重复的模板后真实值是 49，闸反而变成"必须保留重复文件"。
    # 现在量的是**页级覆盖**：语料里每一页像提示词的正文（特征≥2 且 >120 字）都必须已入库。
    pl = cov.get('prompt_like_slides')
    pc = cov.get('prompt_like_slides_covered')
    if not pl or pc is None:
        bad.append("coverage.json 缺 prompt_like_slides/covered：无法证明提示词页没漏研")
    elif pc != pl:
        bad.append("语料里 %d 页像提示词，只有 %d 页进了语料库（漏 %s）" % (
            pl, pc, (cov.get('prompt_like_slides_missing') or [])[:3]))
    if not cov.get('prompt_blocks'):
        bad.append("prompt_blocks 为 0：一份真实提示词都没提炼到")
    return bad


def parse_ai_tells(out):
    """Read ai_tells.py's own report. That script documents 'Exit code 0 always', so the score
    has to be parsed here or G10 can never fail."""
    m = re.search(r'SCORE\s+\S+\s*=\s*([\d.]+)', out or '')
    hits = {k: int(v) for k, v in re.findall(r'(\w+)\s+(\d+)\s+\[target', out or '')}
    return (float(m.group(1)) if m else None), hits


def tech_library_invariants(dt):
    """The technique library must contain what the corpus actually measures.

    Requirement 2 was "learn every design technique in the corpus"; until now the cards recorded
    presetClass totals (entr 852) but not a single presetID, so nothing in the skill could
    reproduce 打字机/飞入/淡出 -- while SPEC §9 claimed presetIDs were written down.
    """
    bad = []
    cards = {c['id']: c for c in (dt.get('cards') or [])}
    if len(cards) < 16:
        bad.append("技法卡只有 %d 张（语料实测至少 16 类手法：GIF 动效、3D 模型与立体感、"
                   "表格转场/轮播、双色调锁色、放大强调都要有卡）" % len(cards))
    st = dt.get('stats') or {}
    # 手法矩阵：语料里数得到的 token，必须有一张卡提到它。这一条就是需求 2 的"无遗漏"——
    # 本轮实测到 duotone/satMod/tint/animScale 四个 token 谁都没写卡，靠它兜住下一轮。
    blob = json.dumps(cards, ensure_ascii=False)
    STRUCT = {'cmd', 'set', 'anim', 'video', 'par', 'seq', 'cTn', 'stCondLst'}
    uncov = []
    for grp in ('effects', 'prims', 'trans'):
        for tok, n in (st.get(grp) or {}).items():
            if n < 20 or tok in STRUCT:
                continue
            if tok not in blob:
                uncov.append('%s=%d' % (tok, n))
    if uncov:
        bad.append("语料实测到却没有任何技法卡提到的 token：%s —— 需求 2 说的\"全部手法\""
                   "就漏在这里（要么补卡，要么说明为什么不算手法）" % uncov[:8])
    if 'gif-motion-element' not in cards:
        bad.append('缺 gif-motion-element 卡：语料 134 个模板里 162 个 GIF 的手法没沉淀下来')
    gstat = (dt.get('stats') or {}).get('gif') or {}
    if not gstat.get('total'):
        bad.append('stats.gif 缺失：动图手法没有实测数据支撑')
    if not st.get('preset_ids'):
        bad.append("stats.preset_ids 是空的：presetClass 总数不等于学到过具体动效")
    tpi = st.get('text_preset_ids') or {}
    if len(tpi) < 5:
        bad.append("文字动效 presetID 只记录了 %d 个（语料里 entr/exit 高频就有 10+）" % len(tpi))
    tac = cards.get('text-animation-presets')
    if not tac:
        bad.append("缺 text-animation-presets 卡：agent 没有 presetID 依据就写不出逐行浮现/打字机")
    elif not all(k in json.dumps(tac, ensure_ascii=False) for k in list(tpi)[:3]):
        bad.append("text-animation-presets 卡没写进实测最高频的 presetID（%s）" % list(tpi)[:3])
    if not st.get('pages_with_animation'):
        bad.append("pages_with_animation 缺失：不知道语料有多少页真的带动画")
    return bad


# ---------------------------------------------------------------- S7 check


def _chroma_hex(v):
    if not v or len(v) != 6:
        return 0.0
    try:
        ch = [int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return 0.0
    return max(ch) - min(ch)


def tbl_text_overlaps(xb):
    """这一页上"文字压进表格框内"的形状：返回 [(形状名, 纵向压入深度)]，按页高归一。

    表格的占位高度按**行高之和**算，不是 graphicFrame 声明的 extent——渲染走行高。
    为什么要有这一条：装配里"表外一行结论"的 y 被 `min(cy, 留白区底-0.085)` 夹回区内，
    而 9 行台账早把留白区吃满（表底 = 区底 = 0.840），于是结论行落在表格最后两行上；
    真渲染 19/22 页实测如此，而当时 29 道闸全绿——这类缺陷只有量几何才看得见。
    满幅蒙版/底图不吃这条：蒙版没有文字，底图不是 sp。
    """
    from xml.etree import ElementTree as ET
    A_ = '{%s}' % A
    P_ = '{%s}' % P
    try:
        root = ET.fromstring(xb)
    except Exception:
        return []
    tables = []
    for gf in root.iter(P_ + 'graphicFrame'):
        # a:tbl 不在 graphicFrame 的直接子层（它在 a:graphic/a:graphicData 里面），
        # 用 find('a:tbl') 会恒取 None——第一版就这样把表格数成 0，闸空咬还报全绿。
        tbl = gf.find(u'.//' + A_ + u'tbl')
        xf = gf.find(P_ + 'xfrm')
        if tbl is None or xf is None:
            continue
        off, ext = xf.find(A_ + 'off'), xf.find(A_ + 'ext')
        if off is None or ext is None:
            continue
        ty, th_, tx, tw = (int(off.get('y')), int(ext.get('cy')),
                           int(off.get('x')), int(ext.get('cx')))
        sumh = 0
        for tr in tbl.findall(A_ + 'tr'):
            tp = tr.find(A_ + 'trPr')
            if tp is not None and (tp.get('h') or '').isdigit():
                sumh += int(tp.get('h'))
        tables.append((tx, tw, ty, ty + max(th_, sumh)))
    if not tables:
        return []
    hits = []
    for sp in root.iter(P_ + 'sp'):
        txt = u''.join((t.text or u'') for t in sp.iter(A_ + 't')).strip()
        if not txt:
            continue
        spPr = sp.find(P_ + 'spPr')
        xf = spPr.find(A_ + 'xfrm') if spPr is not None else None
        if xf is None:
            continue
        off, ext = xf.find(A_ + 'off'), xf.find(A_ + 'ext')
        if off is None or ext is None:
            continue
        sy, sh = int(off.get('y')), int(ext.get('cy'))
        sx, sw = int(off.get('x')), int(ext.get('cx'))
        nm = u'?'
        nv = sp.find(P_ + 'nvSpPr')
        if nv is not None and nv.find(P_ + 'cNvPr') is not None:
            nm = nv.find(P_ + 'cNvPr').get('name') or u'?'
        for (tx, tw, ty, ty1) in tables:
            if (min(sy + sh, ty1) - max(sy, ty) > int(0.02 * EMU_H)
                    and min(sx + sw, tx + tw) - max(sx, tx) > int(0.01 * EMU_W)):
                hits.append((nm, round((min(sy + sh, ty1) - max(sy, ty)) / float(EMU_H), 3)))
                break
    return hits


def g25_table_design(pptx_path, th):
    """G25 表格设计：台账可以留，但必须按片子重排，不能是 Word 那张网格的截图。

    语料实测（kb/table_design.json）：真表格页只占 0.7%，表头 20pt、正文 16pt、48% 单元格加粗。
    本技能在此基础上再加两条发布会标准：数字列右对齐、表外一行结论。
    """
    import zipfile
    from xml.etree import ElementTree as ET
    A_ = '{%s}' % A
    P_ = '{%s}' % P
    bad, n_tbl = [], 0
    z = zipfile.ZipFile(pptx_path)
    names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    names.sort(key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
    for si, n in enumerate(names, 1):
        xb = z.read(n)
        if b'<a:tbl>' not in xb:
            continue
        root = ET.fromstring(xb)
        _ov = tbl_text_overlaps(xb)
        for tbl in root.iter(A_ + 'tbl'):
            n_tbl += 1
            trs = tbl.findall(A_ + 'tr')
            if not trs:
                continue
            head_cells = trs[0].findall(A_ + 'tc')
            banded = sum(1 for tc in head_cells
                         if tc.find(A_ + 'tcPr/' + A_ + 'solidFill') is not None) >= max(1, len(head_cells) - 1)
            head_bold = 0
            for tc in head_cells:
                for rPr in tc.iter(A_ + 'rPr'):
                    if rPr.get('b') == '1':
                        head_bold += 1
            body_sizes, right_num, n_numcell = [], 0, 0
            for tr in trs[1:]:
                for tc in tr.findall(A_ + 'tc'):
                    txt = ''.join((x.text or '') for x in tc.iter(A_ + 't')).strip()
                    for rPr in tc.iter(A_ + 'rPr'):
                        v = rPr.get('sz')
                        if v and v.isdigit():
                            body_sizes.append(int(v) / 100.0)
                    if NUMCOL_RE.match(txt):
                        n_numcell += 1
                        algn = [p.get('algn') for p in tc.iter(A_ + 'pPr')]
                        if any(a in ('r', 'end', 'max') for a in algn if a):
                            right_num += 1
            # 结论行/大数字是形状名（cNvPr name=），不在 <a:t> 文本里；原来拿文本拼 bytes 再套
            # 字符串正则，check 直接 TypeError 崩掉，整份验收报告都没写出来。
            xstr = xb.decode('utf8', 'ignore')
            has_concl = ('name="table_conclusion"' in xstr) or ('name="hero_num"' in xstr)
            why = []
            if not banded:
                why.append(u'表头没有色带填充')
            if head_bold == 0:
                why.append(u'表头没有加粗')
            if body_sizes and min(body_sizes) < th['table_body_pt_min']:
                why.append(u'正文字号低于 %dpt（实测最小 %.1f）' % (
                    th['table_body_pt_min'], min(body_sizes)))
            if n_numcell and right_num == 0:
                why.append(u'数字列没有右对齐（%d 个数字格全部左对齐）' % n_numcell)
            # "表外一行结论"是给数字表的要求：语料里那种四栏定性对照表（谁/有什么/干什么/拿什么钱）
            # 没有可结论化的数字，硬造一句结论反而是编；这类表按表头色带 + 字号 + 强调核。
            if n_numcell >= 3 and not has_concl:
                why.append(u'表外没有结论行或大数字（这张表有 %d 个数字格）' % n_numcell)
            # 结论行不许压在表格里：真渲染 19/22 页的 '合计：98.0' 就写在表格自己的
            # '合计 98.0' 那一行上，旧 29 道闸全绿——只有量几何才抓得到。
            if _ov:
                why.append(u'文字压进表格框内 %d 处（%s）：表外结论要落在行高之和以下' % (
                    len(_ov), u'、'.join(u'%s@%.3f' % (nm, dp) for nm, dp in _ov[:3])))
            # SPEC §7 G25 的"行高按内容（不许平均分）"。行高在 OOXML 里是**下限**：长句行给的
            # 高度不够时渲染器自己把行撑高、把下面的行顶出表框（真渲染 19/22 页把结论压进表格
            # 就是这一形态），反过来两字行摊 1.10 英寸就是一整条空带。
            # 换行数与装配共用 row_line_need 这一把尺，列宽取 gridCol 真值，不另立判据。
            _gw = [int(x.get('w')) for x in tbl.findall(A_ + 'tblGrid/' + A_ + 'gridCol')
                   if (x.get('w') or '').isdigit()]
            _need, _ht = [], []
            for tr in trs[1:]:
                _h = tr.get('h')
                if not (_h and _h.isdigit()) or not _gw:
                    _need = []
                    break
                _n = 1
                for _ci, tc in enumerate(tr.findall(A_ + 'tc')):
                    _tx = ''.join((x.text or '') for x in tc.iter(A_ + 't')).strip()
                    if not (_tx and _ci < len(_gw)):
                        continue
                    _sz = [int(x.get('sz')) / 100.0 for x in tc.iter(A_ + 'rPr')
                           if (x.get('sz') or '').isdigit()]
                    _n = max(_n, row_line_need([[_tx]], [_gw[_ci] / float(EMU_W)],
                                               _sz[0] if _sz else TBL_BODY_PT)[0])
                _need.append(_n)
                _ht.append(int(_h))
            if len(_need) >= 2 and min(_ht) and max(_need) >= 2 * max(1, min(_need)) \
                    and max(_ht) < 1.5 * min(_ht):
                why.append(u'行高平均分：%d 个正文行的换行需求是 %s，行高却都在 %.2f–%.2f 英寸'
                           u'（按内容摊，长句行给够、短句行收到一行高）' % (
                               len(_need), u'/'.join(map(str, _need[:6])),
                               min(_ht) / 914400.0, max(_ht) / 914400.0))
            if why:
                bad.append({'page': si, 'why': why,
                            'header_pt': max([int(x.get('sz')) / 100.0 for tc in head_cells
                                              for x in tc.iter(A_ + 'rPr') if (x.get('sz') or '').isdigit()] or [0])})
    z.close()
    return {'tables': n_tbl, 'bad_tables': len(bad), 'bad': bad[:8],
            'basis': u'语料真表格页仅 0.7%%；表头 20pt / 正文 16pt / 48%% 加粗（kb/table_design.json）'}


def _slide_count(pptx_path):
    try:
        import zipfile as _zl
        z = _zl.ZipFile(pptx_path)
        n = len([x for x in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', x)])
        z.close()
        return n
    except Exception:
        return 0


def g27_type_scale(pptx_path, th, blog=None):
    """字号阶梯闸：每页要有视觉锚点（≥title_pt_min），正文行不许低于 body_pt_min。

    用户第九轮否的就是"标题折三行仍 30pt、正文 16pt 细线浮在半页空暗底上"。
    语料每类版式最大字中位 split 80 / full-bleed 66 / 陈述 180（kb/layout_archetypes.json），
    这里只守下限：整页最大字 ≥32pt、正文行 ≥16pt；注释载体（caption/chip_text/
    numrow_label/条形标签）不算正文行，小字允许 12pt 起。
    留档册（display_mode=verbatim）与封面/隔页/结尾/署名页不吃这条。
    表格页 2026-11-15 起**要**吃这条：它曾与上面几类一起整列豁免，结果成了全场唯一
    "没有任何字号要求"的页型（三张台账页整页最大字 26pt，而语料同类数字密集页 p25=54）。
    """
    import zipfile
    from xml.etree import ElementTree as ET
    A_ = '{%s}' % A
    # 表格页以前和封面/隔页/结尾一起整列豁免，结果它是全场唯一"没有任何字号要求"的页型：
    # 实测三张台账页整页最大字 26pt，而语料同类数字密集页 p25=54。封面那三类豁免的理由
    # （口号大字在别的闸核、chrome 导航件不吃一页一意）对表格页不成立，故只保留其余三类。
    skip_roles = ('cover', 'divider', 'closing')
    meta = {}
    for r in (blog or []):
        meta[r.get('page')] = r
    z = zipfile.ZipFile(pptx_path)
    names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    names.sort(key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
    bad = []
    for si, n in enumerate(names, 1):
        r = meta.get(si) or {}
        if r.get('role') in skip_roles or r.get('display_mode') == 'verbatim':
            continue
        if blog and si not in meta:
            continue                      # 片尾署名页等追加件不是设计页
        try:
            root = ET.fromstring(z.read(n))
        except Exception:
            continue
        pts, body_pts = [], []
        for sp in root.iter('{%s}sp' % P):
            nv = sp.find('.//{%s}cNvPr' % P)
            nm = (nv.get('name') if nv is not None else '') or ''
            for rr in sp.iter(A_ + 'r'):
                sz = rr.find(A_ + 'rPr')
                v = int(sz.get('sz')) / 100.0 if sz is not None and sz.get('sz') else 0
                if not v:
                    continue
                pts.append(v)
                if nm.startswith('text_'):
                    body_pts.append(v)
        if not pts:
            continue
        why = []
        if max(pts) < th['title_pt_min']:
            why.append(u'整页最大字 %.0fpt < %dpt（没有视觉锚点）' % (max(pts), th['title_pt_min']))
        if body_pts and min(body_pts) < th['body_pt_min']:
            why.append(u'正文行最小 %.0fpt < %dpt' % (min(body_pts), th['body_pt_min']))
        if why:
            bad.append({'page': si, 'why': why, 'max_pt': max(pts)})
    z.close()
    return {'pages_checked': len(names), 'bad_pages': len(bad), 'bad': bad[:8],
            'title_pt_min': th['title_pt_min'], 'body_pt_min': th['body_pt_min'],
            'basis': u'语料版式最大字中位 split 80 / full-bleed 66 / 陈述 180；本闸守下限 32/16'}


def g28_watermark(wp, th, blog=None):
    """水印闸：装配进成品的投放产物（gen/pNNN 图/视频）不许带平台角标。

    只量 gen/ 里的投放产物：投放提示词本来就要求"无文字、无水印"，横带里出现
    字形状笔画基本只剩平台角标一种解释；网图不吃这条（招牌是画面内容），
    那由 G3 的许可与来源拦。视频抽 12 帧、≥3 帧命中才算——单帧误检不拦。
    动因：用户免费档豆包/即梦回执全带"豆包AI生成"角标，直接上屏等于把平台
    广告打进路演片子；pickup 已自动去标，这条闸保证没有第二条路把带标素材装进成品。
    """
    bad = []
    checked = 0
    try:
        import dewatermark as DW
    except Exception:
        return {'checked': 0, 'bad': [], 'bad_pages': 0,
                'basis': u'dewatermark 模块不在——G28 无法量', 'error': True}
    for rec in (blog or []):
        items = []
        seen = set()
        for bg in (str(rec.get('bg') or ''), str(rec.get('bg_src') or '')):
            if bg in seen:
                continue
            seen.add(bg)
            if '/gen/' in bg.replace('\\', '/').lower() and \
                    bg.lower().endswith(('.png', '.jpg', '.jpeg')):
                items.append((bg, 'image'))
        vd = str(rec.get('video') or '')
        if '/gen/' in vd.replace('\\', '/').lower():
            items.append((vd, 'video'))
        for pth, kind in items:
            if not os.path.exists(pth):
                continue
            checked += 1
            try:
                if kind == 'image':
                    from PIL import Image
                    hits = DW.image_marks(Image.open(pth))
                    if hits:
                        bad.append({'page': rec.get('page'), 'kind': kind,
                                    'file': os.path.basename(pth),
                                    'why': u'右下角角区检出字形状角标（%d px）' % hits[0]['px']})
                else:
                    # 权威判据：拿 AI产物 原件抠出的水印模板逐帧匹配成品。角区启发式在
                    # 裁角后的片子上会把进角区的亮边误报成角标（实测 12/12 假报）。
                    hits = None
                    try:
                        import lama_cleaner as LC
                        import cv2 as _cv
                        for orig in dewatermark_originals(wp, pth):
                            tpl = LC.build_wm_template(orig, LC.detect_overlay_windows(orig))
                            if tpl is None:
                                continue
                            ww, hh, _, _ = LC._dims(LC._ff(), pth)
                            hits = 0
                            for i, fr in enumerate(LC._iter_frames(LC._ff(), pth, ww, hh)):
                                if i % 2:
                                    continue
                                if LC.match_wm_boxes(_cv.cvtColor(fr, _cv.COLOR_BGR2GRAY),
                                                     tpl, thresh=0.45):
                                    hits += 1
                            break
                    except Exception:
                        hits = None
                    if hits is None:
                        hits = DW.sample_video_marks(pth, 12)
                    if hits >= 3:
                        bad.append({'page': rec.get('page'), 'kind': kind,
                                    'file': os.path.basename(pth),
                                    'why': u'水印模板逐帧匹配命中 %d 帧' % hits})
                    else:
                        # 角标没了不等于干净：逐帧 TELEA 会把压在霓虹弧上的字补成黑疤，
                        # 8 点逐帧目检实测比水印还扎眼。拿 AI产物 的原件对照角区"变黑多少"。
                        deltas = []
                        for orig in dewatermark_originals(wp, pth):
                            sm = DW.smear_vs_original(pth, orig)
                            if sm:
                                deltas.append(sm['delta'])
                        if deltas and min(deltas) > 800:
                            bad.append({'page': rec.get('page'), 'kind': kind,
                                        'file': os.path.basename(pth),
                                        'why': u'去标疤：角区近黑像素比原件多 %d 个——'
                                               u'这条素材只能 --deep 深度补帧或无水印重导'
                                               % min(deltas)})
            except Exception:
                continue
    return {'checked': checked, 'bad': bad[:8], 'bad_pages': len(bad),
            'basis': u'投放提示词要求无文字无水印；gen/ 图片只认贴右下角根、同一基线 ≥2 块的字形簇；'
                     u'视频以 AI产物 原件抠出的水印模板逐帧匹配（阈值 0.45、隔帧抽样）为唯一定义点'
                     u'（SPEC §19：模板由时序叠加层检测的确认框并集生成），命中 ≥3 帧才拦；'
                     u'拿不到原件才退回 DW.sample_video_marks 的时序折算；'
                     u'清过的视频再跟原件比角区近黑像素，多出 800 个以上=去标疤'}


def g23_display_cap(th, has_data):
    """G23 非表格页的文字上限。语料实测：普通页 p50=29/p75=69（64 是"一页一意"线）；
    数据/卡片页 105 张 p50=68/p75=86/p90=209——64 会把语料自己的好数据页判掉 69.5%，
    故数据页按语料整页 p90=112 定标（仍严于数据页自身 p90=209）。"""
    return th.get('page_chars_max_data', 112) if has_data else th['page_chars_max_display']


def g23_page_budget(pptx_path, th, verbatim=False, blog=None):
    """G23 页面预算：一页一意的硬约束。

    语料好示范页 p50=19 字、p75=44、p90=112（kb/data_design.json）。逐字上屏做出来的是
    100-300 字/页，密度差一个数量级，配色和字体再怎么调都不会像模板。
    """
    import zipfile
    from xml.etree import ElementTree as ET
    A_ = '{%s}' % A
    P_ = '{%s}' % P
    over = []
    if verbatim:
        # 返回形态必须与正常分支一致：cmd_check 直接读 g23['over_pages']，
        # 少一个键就是 --verbatim 一用 check 就崩（实测 exit 3 + 崩溃.md）
        return {'pages': _slide_count(pptx_path), 'over_pages': 0, 'over': [],
                'chrome_pages': 0, 'verbatim_exempt': True,
                'basis': u'--verbatim 全量留档册豁免（报告必须自报这不是发布会册）'}
    z = zipfile.ZipFile(pptx_path)
    names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    names.sort(key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
    # 与 G20 同一口径：页面预算只量「装配页」。引擎在片尾追加的来源署名页（一页 2000+ 字的
    # URL 清单）是许可要求的法律件、不是版面，按设计页判就会把整闸变成假硬伤（实测 9/11 条）。
    asm = set()
    chrome_roles = set()
    _titles = {}
    for _bx in pptx_boxes(pptx_path):
        try:
            _si, _nm = _bx[0], _bx[1]
            # 必须取**原文**（第 3 项）而不是归一化文本（第 4 项）：归一化已经把
            # '（附表 1）' 的括号抹掉，标记正则匹配不上，句尾附录豁免就静默失效
            # ——新闸第一次跑已知合规的册子就误报了 p28，是两面都验才抓住的。
            _tx = _bx[2] if len(_bx) > 2 else u''
        except Exception:
            continue
        if (_nm or u'') == u'title':
            _titles[_si] = (_tx or u'').strip()
    data_layouts = set()
    for r in (blog or []):
        if r.get('page'):
            asm.add(r['page'])
            if r.get('role') in ('cover', 'divider', 'closing'):
                chrome_roles.add(r['page'])
            if r.get('data_layout'):
                data_layouts.add(r['page'])
    n_chrome = 0
    for si, n in enumerate(names, 1):
        try:
            root = ET.fromstring(z.read(n))
        except Exception:
            continue
        if asm and si not in asm:
            n_chrome += 1
            continue
        if si in chrome_roles:
            # 封面/过渡/结尾是 chrome 导航件（SPEC §17 封面四件套天然 >64 字），不吃"一页一意"
            # 内容预算；语料封面墙也是口号+副标题+胶囊+落款。内容页才量这一维。
            n_chrome += 1
            continue
        has_tbl = b'<a:tbl>' in z.read(n)
        texts = [(x.text or '').strip() for x in root.iter(A_ + 't') if (x.text or '').strip()]
        chars = len(re.sub(r'\s', '', ' '.join(texts)))
        # 数据页结构识别：build_log 不一定记 data_layout（chip 行/条形标签页实测漏记），
        # 画面里 ≥4 个"含数字的短标签"就是数据/图表页 → 按数据页上限判（实测页4 是 1 长标题+8 标签）。
        _short_num = [x for x in texts if len(x) <= 14 and re.search(r'\d', x)]
        has_data = (si in data_layouts) or len(_short_num) >= 4
        # 标题框：语料口径是 ≤18 字的 Action Title
        heads = [x for x in texts if 0 < len(x) <= 40]
        lines = [x for x in texts if x not in heads]
        why = []
        if has_tbl:
            cap = th['page_chars_max_table']
        else:
            _disp = g23_display_cap(th, has_data)
            cap = th['page_chars_max_quote'] if chars > _disp else _disp
            if chars > _disp:
                why.append(u'超提炼预算（%d 字 > %d）' % (chars, _disp))
        if chars > cap:
            why.append(u'整页 %d 字，上限 %d' % (chars, cap))
        long_lines = [x for x in lines if len(x) > th['display_line_max_chars']]
        if not has_tbl and len(long_lines) > 1:
            why.append(u'%d 条显示行超 %d 字' % (len(long_lines), th['display_line_max_chars']))
        # 标题长度是显示契约（SPEC §12 / DR.HEADLINE_MAX），可成品端一直没量过这一维：
        # 整页字数那把尺抓不到它——真渲染页 17 的 65 字整句标题既进不了 heads（≤40 字），
        # 也凑不成"第二条长行"，于是 over_pages 一路报 0，把 p90 从 34 顶到 57 也没人拦。
        # 计数走 headline_count 同一把尺：句尾的 '（附表 N）' 交叉引用不计，
        # 剥掉后的正文仍必须整条过线，所以这条豁免夹带不了长句。
        _ttl = _titles.get(si, u'')
        if _ttl and headline_count(_ttl) > DR.HEADLINE_MAX:
            why.append(u'标题 %d 字超显示契约 %d（附录标记不计）：%s' % (
                headline_count(_ttl), DR.HEADLINE_MAX, _ttl[:24]))
        if why:
            over.append({'page': si, 'chars': chars, 'why': why, 'text': ' '.join(texts)[:40]})
    z.close()
    return {'pages': len(names), 'over_pages': len(over), 'over': over[:10],
            'chrome_pages': n_chrome, 'verbatim_exempt': False,
            'basis': u'语料好示范 p50=19 / p75=44 / p90=112 字每页（kb/data_design.json）；'
                     u'引擎追加的来源署名页 %d 页不计（不是版面）' % n_chrome}


def g24_fact_coverage(outline, pptx_path, th):
    """G24 事实覆盖：措辞可以改，事实不能丢。

    取代旧 G13 的"每句逐字上屏"：提炼契约下允许把一段话压成一句短句，
    但段里的数字、金额、比例、时间、专名必须还在成品里。
    """
    deck = norm_content(pptx_all_text(pptx_path))
    ents = [e for e in (outline.get('entities') or []) if len(e) >= 2]
    brief = outline.get('brief') or {}
    ledger = {e['page']: e for e in (brief.get('ledger') or [])}
    budget = bool(brief.get('pages'))
    missing = []
    units_total = 0
    dropped_units = 0
    for pg in outline['pages']:
        full = ' '.join([pg.get('headline') or ''] + list(pg.get('source_span') or pg.get('body') or []))
        if budget:
            e = ledger.get(pg['page'])
            if e is None:
                missing.append({'page': pg['page'], 'fact': u'压缩台账缺这一页',
                                'where': u'页数契约下每页必须有 refine 台账'})
                continue
            src = ' '.join([pg.get('headline') or ''] + list(e.get('kept') or []))
            _keep = set(norm_content(f) for f in DR.fact_units(src, ents))
            dropped_units += len(set(norm_content(f) for f in DR.fact_units(full, ents)) - _keep)
        else:
            src = full
        if len(re.sub(r'\s', '', src)) < 20:
            continue
        facts = [norm_content(f) for f in DR.fact_units(src, ents)]
        for f in set(facts):
            units_total += 1
            if f not in deck:
                missing.append({'page': pg['page'], 'fact': f[:24],
                                'where': re.sub(r'\s+', '', src)[:40]})
    # 断头数字：屏幕上的数字若是这一页原文里某个更长数字的前缀（'82' ← '82,340'），
    # 上面那种"全簿文本核"看不见它——完整的 '82,340 例' 在别的页出现过就算过了。
    # 逐页目检（v10 页 89）抓到的是另一回事：标题上挂着一个原文没有的数，对外就是错数。
    # 取数必须**逐形状**，不许把整页文字去掉空白连成一串再抽：那样标题上真实的 '2.'
    # （'2.7' 被裁）会与紧随其后的筹码 '46.9 万元' 拼成 '2.46.9'——一个原文里根本不存在的
    # 畸形串，既不是 '2.7' 的前缀，又让判据彻底看不见真截断（2026-11-15 注入实测必漏）。
    # 声明域仍是"本页原文"：跨页截断是另一件事，未验通前不顺手放宽（见 #57 记账）。
    trunc = []
    shown_pg = {}
    for _bx in pptx_boxes(pptx_path):
        try:
            _si, _txt = _bx[0], (_bx[2] if len(_bx) > 2 else _bx[1])
        except Exception:
            continue
        shown_pg.setdefault(_si, set()).update(
            re.findall(r'\d[\d,\.]*', re.sub(r'\s', u'', _txt or u'')))
    for i, pg in enumerate(outline['pages']):
        src = re.sub(r'\s', u'', u' '.join(
            list(pg.get('source_span') or pg.get('body') or [])))
        s_toks = set(re.findall(r'\d[\d,\.]*', src))
        for t in sorted(shown_pg.get(i + 1, set())):
            if len(t) < 2 or t in s_toks:
                continue
            for s in s_toks:
                if len(s) > len(t) and s.startswith(t) and s[len(t)] in u',.0123456789':
                    trunc.append({'page': pg['page'], 'shown': t, 'source': s})
                    break
    return {'fact_units': units_total, 'missing': len(missing),
            'sample': missing[:10], 'entities_checked': len(ents),
            'truncated': len(trunc), 'truncated_sample': trunc[:8],
            'budget_mode': budget, 'dropped_to_ledger': dropped_units,
            'basis': u'提炼契约（SPEC §12）：改写自由，事实单元（数字/金额/比例/时间/专名）不自由；'
                     u'页数契约（SPEC §21）下只核台账 kept 句的事实，被压缩原文的事实进 dropped_to_ledger 留账'}


def g21_source_lock(outline, pptx_path, th):
    """溯源基准锁：G1/G13 的"源"必须是指纹一致的用户原稿，而且原稿的表必须成建制进成品。

    Why: v17 那份 58 页片子把上一版 PPT 抽字生成 4410 字的 input.docx 当溯源基准
    （真源 2.5 万字 / 16 张表），于是 G1「0 差异」、G13「189 句全覆盖」变成新片子对旧片子的
    自我循环证明，16 张台账在成品里一张都不剩。G1–G20 没有任何一道去看源文件是谁。
    三条判据只认磁盘上的文件与成品 XML：① plan 记的 SHA1 与现在一致；② 源里每张非空表的
    表头与数据格都要在成品里数到；③ 源正文整段被同项目某个旧 PPT 命中 ≥80%、而那个 PPT
    文字量还更大，说明基准是从旧成品抽字派生的，不是用户原稿。
    """
    import glob
    from docx import Document
    out = {'source': {'path': outline.get('docx'), 'sha1_plan': None, 'sha1_now': None,
                      'bytes_plan': None, 'chars_plan': None,
                      'tables_plan': None},
           'source_tables': 0, 'deck_tables': 0, 'tables_missing': [],
           'derived_from': [], 'findings': [],
           'basis': u'真源指纹 + 逐表对账 + 整段反派生（v17 的 4410 字假基准：对 v15e 整段命中 91.9%、'
                    u'文字量 1.68 倍；2.5 万字真源对旧片整段命中 0.0%）'}
    src = outline.get('source') or {}
    docx_path = outline.get('docx') or ''
    out['source'].update({'sha1_plan': src.get('sha1'), 'bytes_plan': src.get('bytes'),
                          'chars_plan': src.get('chars'), 'tables_plan': src.get('tables')})
    if not src:
        out['findings'].append({'kind': 'no_fingerprint',
                                'why': u'outline 里没有 source 指纹块（plan 是旧版跑的）——'
                                       u'无法证明溯源基准没被换过，用当前版本重跑 plan'})
        return out
    if not docx_path or not os.path.exists(docx_path):
        out['findings'].append({'kind': 'source_missing',
                                'why': u'溯源基准文件不在了（%s）——G1/G13 无法复核' % docx_path})
        return out
    now_sha = sha1(docx_path)
    out['source']['sha1_now'] = now_sha
    if now_sha != src.get('sha1'):
        out['findings'].append({'kind': 'swapped',
                                'why': u'plan 之后溯源基准被换过（plan=%s… 现在=%s…）——'
                                       u'G1/G13 核的是另一份文件' % (
                                           str(src.get('sha1'))[:10], now_sha[:10])})
    try:
        seq = doc_blocks(Document(docx_path))
    except Exception as e:
        out['findings'].append({'kind': 'source_unreadable',
                                'why': u'溯源基准打不开：%s' % str(e)[:80]})
        return out
    tables = [b['rows'] for b in seq if b['kind'] == 'tbl']
    tables = [t for t in tables if any(any((c or '').strip() for c in r) for r in t)]
    out['source_tables'] = len(tables)
    deck_pages = pptx_slide_texts(pptx_path) if os.path.exists(pptx_path) else []
    deck_all = ''.join(deck_pages)
    try:
        import zipfile
        z = zipfile.ZipFile(pptx_path)
        out['deck_tables'] = sum(z.read(n).decode('utf8', 'ignore').count('<a:tbl>')
                                 for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n))
        z.close()
    except Exception:
        pass
    _b21 = outline.get('brief') or {}
    _drop_hdr = {d.get('header') for d in ((_b21.get('alloc') or {}).get('dropped_tables') or [])}
    n_drop_tables = 0
    for ti, t in enumerate(tables, 1):
        header = [norm_content(c) for c in (t[0] or []) if norm_content(c)]
        body = [norm_content(c) for r in t[1:] for c in r if len(norm_content(c)) >= 2]
        if not header:
            continue
        _hdr_raw = ' | '.join((c or '').strip() for c in (t[0] or []))[:60]
        if _b21.get('pages') and _hdr_raw in _drop_hdr:
            n_drop_tables += 1   # 页数契约取舍留账的表：不核上屏，但必须在台账里
            continue
        hit = [k for k in header if k in deck_all]
        body_hit = any(k in deck_all for k in body[:8])
        if len(hit) < len(header) or not body_hit:
            out['tables_missing'].append({
                'table': ti, 'header': ' | '.join((c or '').strip() for c in (t[0] or []))[:60],
                'header_found': len(hit), 'header_cells': len(header),
                'rows': len(t) - 1})
    out['tables_dropped_to_ledger'] = n_drop_tables
    # 反派生：源正文若整段整段地躺在同项目的某个旧 PPT 里，而那个 PPT 文字量还更大，
    # 说明"源"是从旧成品抽字的派生件，不是用户的原稿。
    # 实测分界（车联体）：4410 字的假基准对 v15e 整段命中 91.9% / 文字量 1.68 倍；
    # 2.5 万字的真 Word 对所有旧片整段命中 0.0% / 0.21—0.35 倍——两条同时吃才既咬得住又不误伤。
    src_paras = [norm_content(b['text']) for b in seq
                 if b['kind'] == 'p' and len(norm_content(b['text'])) >= 12]
    src_chars = sum(len(p) for p in src_paras)
    if src_paras:
        seen = set()
        # 从源文件所在目录逐级上探：作业目录常嵌在 项目/_work/caofeng_vN 这种两三层深的位置，
        # 只看两层时 v17 那份 input.docx 的旧 PPT 就在第三层，漏检过一次。
        base = os.path.abspath(os.path.dirname(docx_path))
        bases = []
        for _ in range(5):
            if not base or base in bases:
                break
            bases.append(base)
            base = os.path.dirname(base)
        for base in bases:
            for cand in glob.glob(os.path.join(base, '*.pptx')):
                cand = os.path.abspath(cand)
                if cand in seen or cand == os.path.abspath(pptx_path):
                    continue
                seen.add(cand)
                try:
                    if os.path.getsize(cand) > 200 * 1024 * 1024:
                        continue
                    ct = norm_content(pptx_all_text(cand))
                except Exception:
                    continue
                if len(ct) < src_chars * th['derived_size_min']:
                    continue
                # 只有"基准比那册成品还新"才谈得上派生：抄旧片子的人是在片子出来之后才生成假基准的。
                # 反过来，按这份 Word 正常装配出来的成品当然比源旧不了、文字量还更大
                # （表格逐格外还多算一遍），不排除就会误伤——实测 v19 交付件被这样误判过一次。
                try:
                    if os.path.getmtime(cand) >= os.path.getmtime(docx_path):
                        continue
                except Exception:
                    continue
                contain = 100.0 * sum(1 for p in src_paras if p in ct) / len(src_paras)
                if contain >= th['derived_contain_min']:
                    out['derived_from'].append({
                        'file': cand, 'contain_pct': round(contain, 1),
                        'ratio': round(len(ct) / max(1, src_chars), 2)})
    return out


def g20_material_coverage(pptx_path, blog, th):
    """全册素材覆盖率与色场连排：矢量色场是「这一页没找到素材」的兜底，不是整册的默认背景。

    只认两件事：build_log 里这页的底图来自真素材（materials/ gen/ graded/ 或整页视频），
    还是 out/bg_fallback（引擎自己画的色场）。shots.json 怎么写不算判据——
    实测 WorkBuddy 把 50 页标成 field 就 19 道闸全绿，这正是本闸要堵的洞。
    """
    import math
    import deck_measure as DM
    pages = DM.measure_deck(pptx_path)
    rec = {}
    for r in (blog or []):
        bg = str(r.get('bg') or '').replace('\\', '/')
        rec[r.get('page')] = ('field' if 'bg_fallback' in bg else ('material' if bg else 'none'))
    # 分母只算「装配页」：成品末尾还会追加来源署名页（CC BY 许可要求，法律件不是设计页），
    # 它按定义永远铺不了素材，混进分母就会把满册真素材的片子顶到线下（实测 3/7=42.9%）。
    # 只在 build_log 有账时剔除，log 缺失或整册无记录时退回全册页数，防止清空 log 绕过本闸。
    content = [p for p in pages if p['page'] in rec] or pages
    n = len(content)
    material = [p['page'] for p in content if rec.get(p['page']) == 'material']
    field = [p['page'] for p in content if rec.get(p['page']) == 'field']
    run = maxrun = 0
    _motif_by_page = {r.get('page'): r.get('field_motif', 0) for r in (blog or [])}
    _last_motif = None
    for p in content:
        if rec.get(p['page']) == 'field':
            _mo = _motif_by_page.get(p['page'], 0)
            # 连排判"同一张脸"：母题换了就不算连排（无素材页靠母题轮换避免模板脸）
            run = run + 1 if _mo == _last_motif else 1
            _last_motif = _mo
        else:
            run = 0
            _last_motif = None
        maxrun = max(maxrun, run)
    need_min = 2 if n < 6 else int(math.ceil(n * th['material_pages_min_pct'] / 100.0))
    pct = round(100.0 * len(material) / n, 1) if n else 0.0
    # 同一张素材刷多页：v17 里那张 131KB 的同心圆渐变被铺了 28 页，覆盖率再高也是模板脸。
    # 按**内容指纹**归组而不是按路径：装配会给每页另存一份 out/graded/pNNN.jpg，
    # 同一张原图铺 5 页就是 5 个文件名相同字节的副本（实测 sha1 一致）。
    # 视频页的 bg 记的是 'video' 这个标记而不是路径，8 段不同 mp4 会被当成"同一张"
    # （重复视频由 G15 按 SHA1 管），色场页由上面的连排规则管。
    from collections import Counter
    bg_files, hcache = {}, {}
    for r in (blog or []):
        bg = str(r.get('bg') or '').replace('\\', '/')
        if bg and 'bg_fallback' not in bg and '/' in bg and re.search(r'\.(jpg|jpeg|png|webp)$', bg, re.I):
            if bg not in hcache:
                try:
                    hcache[bg] = sha1(bg)
                except Exception:
                    hcache[bg] = bg          # 读不到就退回路径，至少不把这一页整个漏掉
            bg_files[r.get('page')] = hcache[bg]
    reuse = Counter(bg_files.values())
    name_by_hash = {}
    for r in (blog or []):
        bg = str(r.get('bg') or '').replace('\\', '/')
        if bg in hcache:
            name_by_hash.setdefault(hcache[bg], os.path.basename(bg))
    top = [{'file': name_by_hash.get(h, h[:10]), 'pages': c} for h, c in reuse.most_common(3)]
    worst = top[0]['pages'] if top else 0
    return {'pages': n, 'material_pages': len(material), 'material_pct': pct,
            'field_pages': len(field), 'longest_field_run': maxrun,
            'need_material_pages_min': need_min, 'threshold_pct': th['material_pages_min_pct'],
            'shortfall': max(0, need_min - len(material)),
            'distinct_material_files': len(reuse), 'top_reuse': top,
            'worst_reuse_pages': worst, 'reuse_max': th['material_reuse_max'],
            'basis': (u'语料好示范子集满幅压字 58.3%（835 页）；色场连排上限借构图节奏实测 ≤2；'
                      u'同一张素材最多铺 ' + str(th['material_reuse_max']) + u' 页'
                      u'（v17 实测同一张渐变铺了 28 页）')}


def g19_bad_exemplar(pptx_path, facts, blog=None):
    """成品逐页不得命中语料坏示范特征（判据与 grade_exemplars.py 同一份：BAD_RULES）。

    Why: 语料 2224 页里只有 835 页能当范本，其余 1389 页是作者自己的推广页（496）、
    无信息空页（560）、字压亮底（346）、文字墙（70）、无层级小字（20）、模板编号当主视觉（16）。
    如果只按"语料里高频"学，技能学到的正是这些毛病；这条把它们逐页拦在成品外。
    """
    import zipfile
    from xml.etree import ElementTree as ET
    A_ = '{%s}' % A
    P_ = '{%s}' % P
    ad = [(re.compile(p), code) for p, code in CT.BAD_RULES['ad_patterns']]
    prod = re.compile(CT.BAD_RULES['product_number'], re.I)
    hits = []
    try:
        z = zipfile.ZipFile(pptx_path)
    except Exception:
        return {'hits': [{'page': 0, 'why': '成品 pptx 读不出来'}], 'pages': 0}
    names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    names.sort(key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
    for si, n in enumerate(names, 1):
        try:
            x = ET.fromstring(z.read(n))
        except Exception:
            continue
        texts = [(t.text or '').strip() for t in x.iter(A_ + 't')]
        texts = [t for t in texts if t]
        joined = ' '.join(texts)
        chars = len(re.sub(r'\s+', '', joined))
        pts = [int(v) // 100 for v in
               [e.get('sz') for e in x.iter() if local_name(e) in ('rPr', 'defRPr')]
               if (v or '').isdigit()]
        max_pt = max(pts) if pts else 0
        f = facts.get(si) or {}
        why = []
        # 来源署名页（图片来源/CC BY 那一页）是合规要求，不是内容页：文字密是应该的，
        # 只豁免"文字墙"这一条，广告词/编号主视觉/空页照样拦
        attribution = bool(re.match(r'^\s*(图片来源|素材来源|版权|CC\s*BY)', joined, re.I))
        # 引文页（整页只有一句原文长话）豁免"文字墙"这一条：句子不许被改写、也不许丢，
        # 版式已按 quote 的大字单栏排；多句挤成一页的 data/process 页照样判墙。
        role_19 = str(((blog or [{}])[:si][-1] or {}).get('role') or '') if len(blog or []) >= si \
            else ''
        # 附表页也不管：台账本来就是结构化数据（8 行 4 列轻松 300+ 字），
        # 语料那条"文字墙"说的是把整段小字硬塞进一页，两者不是一回事；
        # 表格另有 G13 逐格核对与 G8 几何量着
        quote_exempt = role_19 in ('quote', 'table')
        for rx, code in ad:
            if rx.search(joined):
                why.append('ad:' + code)
                break
        if chars and prod.match(texts[0] if texts else ''):
            why.append('product_number_hero')
        if chars >= CT.BAD_RULES['dense_chars_min'] and not attribution and not quote_exempt:
            why.append('dense_text_wall:%d' % chars)
        if chars and max_pt and max_pt < CT.BAD_RULES['tiny_pt_max']:
            why.append('no_type_hierarchy:%dpt' % max_pt)
        if chars == 0 and not f.get('has_video') and not f.get('has_gif') \
                and not f.get('anim_nodes'):
            why.append('empty_page')
        if why:
            hits.append({'page': si, 'why': why, 'chars': chars, 'max_pt': max_pt,
                         'text': joined[:48]})
    z.close()
    return {'hits': hits[:12], 'n_hits': len(hits), 'pages': len(names),
            'rules_from': 'corpus_tables.BAD_RULES（与语料分层同一份判据）'}


def local_name(el):
    return el.tag.rpartition('}')[2] if isinstance(el.tag, str) else ''


def g16_palette_lock(pptx_path, pal_name, extra=()):
    """成品逐页出现的彩色必须落在选定实测色板内，且 theme 的 accent1 就是色板主色。

    语料自己 167/171 册没改主题色（颜色逐形状直写 srgbClr 35327 次 + lumMod 派生 2758 次），
    所以这条不是抄来的规矩，是本技能给"一册一色"加的硬约束：允许灰阶（白/黑/灰是语料主力色），
    带饱和度的彩色必须在色板内。VI 定制色用 --allow-color 显式放行，放行值会打印出来。
    """
    import zipfile
    from lxml import etree
    A_ = '{%s}' % A
    allowed = set(CT.allowed_colours(pal_name, [x.upper() for x in (extra or [])]))
    z = zipfile.ZipFile(pptx_path)
    names = [i.filename for i in z.infolist()]
    pages, offenders = 0, []
    for n in sorted(names, key=lambda s: int(re.findall(r'\d+', s)[0]) if re.search(r'\d+', s) else 0):
        if not re.match(r'ppt/slides/slide\d+\.xml$', n):
            continue
        pages += 1
        try:
            root = etree.fromstring(z.read(n))
        except Exception:
            continue
        bad = set()
        # 取色渐变右块（教程 3056573a 取色器）的颜色来自本页底图，与照片同属画面内容，
        # 不吃设计色板闸；它是否真取自底图由 G29 的 audit_divider_art 单独核。
        P_ = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
        skip = set()
        for sp in root.iter(P_ + 'sp'):
            nv = sp.find('.//' + P_ + 'cNvPr')
            if nv is not None and nv.get('name') == 'divider_block':
                skip.update(sp.iter(A_ + 'srgbClr'))
        for el in root.iter(A_ + 'srgbClr'):
            if el in skip:
                continue
            v = (el.get('val') or '').upper()
            if _chroma_hex(v) < TH['chroma_floor']:
                continue
            if v not in allowed:
                bad.add(v)
        if bad:
            offenders.append({'page': int(re.findall(r'\d+', n)[0]),
                              'colours': sorted(bad)})
    z.close()
    theme_accent1 = theme_accent1_of(pptx_path)
    want = (CT.PALETTES[pal_name]['theme'].get('accent1') or '').upper()
    return {'palette': pal_name, 'pages_checked': pages, 'offending_pages': offenders,
            'theme_accent1': theme_accent1, 'palette_accent1': want,
            'allowed_colours': sorted(allowed),
            'allowed_extra': sorted(set(x.upper() for x in (extra or [])))}


def g17_layout_lock(pptx_path, build_log):
    """声明的图文重排原型必须真是那个原型——用与语料挖掘同一份度量（deck_measure）量成品。"""
    pages = DM.measure_deck(pptx_path)
    declared = {}
    for r in build_log or []:
        declared[r.get('page')] = r.get('layout') or r.get('layout_declared')
    mismatch, over_rate = [], []
    for pg in pages:
        d = declared.get(pg['page'])
        if not d:
            continue
        if d != pg['archetype']:
            mismatch.append({'page': pg['page'], 'declared': d, 'measured': pg['archetype']})
        lay = CT.LAYOUTS.get(d) or {}
        zones = pg.get('zones') or []
        if zones:
            rate = 100.0 * sum(1 for x in zones if x['over_pic']) / len(zones)
            over_rate.append({'page': pg['page'], 'over_pic_pct': round(rate, 1),
                              'corpus_pct': lay.get('over_pic_pct')})
            if d == 'full-bleed' and rate < TH['fullbleed_over_pic_min']:
                mismatch.append({'page': pg['page'], 'why': '声明满幅压字但只有 %.0f%% 的字落在图上'
                                                            % rate})
            if d == 'split-centre-image' and rate > TH['split_over_pic_max']:
                mismatch.append({'page': pg['page'], 'why': '声明图文分栏但 %.0f%% 的字压在图上'
                                                            % rate})
    return {'pages_measured': len(pages), 'mismatch': mismatch,
            'over_pic_rates': over_rate[:12]}


def g18_anim_lock(pptx_path, build_log):
    """声明的动画 preset 必须真在成品 timing 里，且带实测签名（filter / p:anim 属性）。"""
    import zipfile
    from lxml import etree
    P_ = '{%s}' % P
    z = zipfile.ZipFile(pptx_path)
    names = [i.filename for i in z.infolist()]
    by_page = {}
    for n in names:
        m = re.match(r'ppt/slides/slide(\d+)\.xml$', n)
        if not m:
            continue
        try:
            root = etree.fromstring(z.read(n))
        except Exception:
            continue
        triples, filters, cmds, nt = collections.Counter(), collections.Counter(), \
            collections.Counter(), collections.Counter()
        rep = 0
        for ctn in root.iter(P_ + 'cTn'):
            cls = ctn.get('presetClass')
            if cls:
                triples['%s:%s:%s' % (cls, ctn.get('presetID'), ctn.get('presetSubtype'))] += 1
            nt[ctn.get('nodeType') or '?'] += 1
            if (ctn.get('repeatCount') or '').lower() == 'indefinite':
                rep += 1
            for eff in ctn.iter(P_ + 'animEffect'):
                filters[eff.get('filter') or '?'] += 1
            for cmd in ctn.iter(P_ + 'cmd'):
                cmds[(cmd.get('cmd') or '').split('(')[0] or '?'] += 1
        by_page[int(m.group(1))] = {'triples': triples, 'filters': filters,
                                    'cmds': cmds, 'nodeTypes': nt, 'repeat_indef': rep}
    z.close()
    missing, video_click = [], []
    for r in build_log or []:
        n = r.get('page')
        meas = by_page.get(n) or {'triples': collections.Counter(), 'filters': collections.Counter(),
                                  'cmds': collections.Counter(), 'nodeTypes': collections.Counter(),
                                  'repeat_indef': 0}
        for spec in r.get('anim_presets') or []:
            tri = spec.get('triple')
            if tri and not meas['triples'].get(tri):
                missing.append({'page': n, 'triple': tri, 'filter': spec.get('filter')})
            f = spec.get('filter')
            if f and not meas['filters'].get(f):
                missing.append({'page': n, 'triple': tri, 'filter': f,
                                'why': '成品里没有这条 animEffect filter'})
            # attr 判据已如实退役：旧代码 ok_attr 恒 True 永不触发（假闸，干净上下文审计抓出）。
            # 真测 attrNameLst 需按效果读子元素，未实现前不许再写"有判据"（SPEC §18 已记账）。
        if (r.get('anim_shapes') or 0) > 0 and not any(
                k.startswith('entr') for k in meas['triples']):
            missing.append({'page': n, 'why': 'build_log 记了 %d 个入场形状，成品里一个 entr 节点都没有'
                                                % r['anim_shapes']})
        if r.get('video'):
            has_play = meas['cmds'].get('playFrom') or 0
            has_toggle = meas['cmds'].get('togglePause') or 0
            inter = meas['nodeTypes'].get('interactiveSeq') or 0
            video_click.append({'page': n, 'playFrom': has_play, 'togglePause': has_toggle,
                                'interactiveSeq': inter,
                                'repeat_indef': meas.get('repeat_indef', 0),
                                'withEffect': meas['nodeTypes'].get('withEffect', 0)})
            if not (has_play and has_toggle and inter):
                missing.append({'page': n, 'why': '视频页缺点击暂停链（playFrom=%d togglePause=%d '
                                                  'interactiveSeq=%d；语料 1042 页两者都有）'
                                                  % (has_play, has_toggle, inter)})
            # 教程 ae56a65b[40] 循环播放：写了 repeatCount 没人验过（零闸死账，P17 补）。
            # v10 基线 9/9 视频页全带，不变式＝每个内嵌视频页必带。
            if (meas.get('repeat_indef') or 0) < 1:
                missing.append({'page': n, 'why': '视频页没有 repeatCount="indefinite"——放映时播一遍就停'
                                                  '（教程教的循环播放没落到成品 timing 里）'})
            # 教程 ae56a65b[47-53] 标题"与上一动画同时"：SPEC §18 承诺的是
            # 封面/过渡/结尾页标题与视频并跑（数据页走语料 preset 阶梯，不在此列）。
            # 这三个角色有入场动画却一个 withEffect 都没有＝标题排在视频之后才出现。
            if (r.get('role') in ('cover', 'divider', 'closing')
                    and (r.get('anim_shapes') or 0) > 0
                    and not meas['nodeTypes'].get('withEffect')):
                missing.append({'page': n, 'why': '%s 页 %d 个入场形状没有一个是 nodeType="withEffect"'
                                                  '（与上一动画同时）——标题会排在视频之后才出现'
                                                  % (r.get('role'), r['anim_shapes'])})
    return {'missing': missing, 'video_pages': video_click,
            'corpus_reference': {k: CT.ANIM[k]['n'] for k in
                                 ('fade', 'wipe-left', 'wipe-down', 'exit-fade',
                                  'emphasis-scale') if k in CT.ANIM}}


def g29_opening(pptx_path, outline, th, blog=None):
    """开场解剖（SPEC §17）：封面背景须满铺 MEDIA 而非 animScale 推镜满铺图（后者放映态缩成
    小图，用户实测）；封面主标题须是独立口号≠文档名；四件套（kicker/副标题/胶囊/落款）齐全；
    过渡页须有看点短句。逐页核成品 XML，不看计划。"""
    import zipfile
    from pptx import Presentation
    from pptx.util import Emu
    prs = Presentation(pptx_path)
    W, H = prs.slide_width, prs.slide_height
    z = zipfile.ZipFile(pptx_path)
    pages = outline.get('pages') or []
    doc_title = ((pages[0].get('headline') if pages else '') or '').strip()
    bad, animscale_bg, divider_bad = [], [], []
    tol = Emu(91440)
    for i, slide in enumerate(prs.slides, 1):
        try:
            xml = z.read('ppt/slides/slide%d.xml' % i).decode('utf-8', 'ignore')
        except KeyError:
            continue
        full_media, full_pic_id = False, None
        for sh in slide.shapes:
            try:
                full = (abs(sh.left or 0) < tol and abs(sh.top or 0) < tol
                        and abs((sh.width or 0) - W) < tol and abs((sh.height or 0) - H) < tol)
            except Exception:
                full = False
            st = str(sh.shape_type)
            if full and 'MEDIA' in st:
                full_media = True
            if full and 'PICTURE' in st:
                full_pic_id = sh.shape_id
        # animScale 打在满铺背景图上才是缺陷（大数字强调 targeting 文字框不算）
        if full_pic_id is not None and not full_media:
            targets = re.findall(r'<p:animScale>.*?<p:spTgt spid="(\d+)"/>.*?</p:animScale>',
                                 xml, re.S)
            if str(full_pic_id) in targets:
                animscale_bg.append(i)
        op = pages[i - 1] if i - 1 < len(pages) else {}
        role = op.get('role')
        if role == 'cover':
            texts = {sh.name: ' '.join(r.text for pa in sh.text_frame.paragraphs for r in pa.runs)
                     for sh in slide.shapes if sh.has_text_frame}
            why = []
            if not texts.get('title', '').strip() or texts.get('title', '').strip() == doc_title:
                why.append('主标题仍是文档名，无独立口号 cover_slogan')
            for piece, label in (('kicker', 'kicker 文档名副题'), ('subtitle', '价值主张副标题'),
                                 ('chips', '信息胶囊行'), ('byline', '底部落款')):
                if not texts.get(piece, '').strip():
                    why.append('缺' + label)
            if why:
                bad.append({'page': i, 'why': why})
        elif role == 'divider':
            _dtexts = {sh.name: ' '.join(r.text for pa in sh.text_frame.paragraphs for r in pa.runs)
                       for sh in slide.shapes if sh.has_text_frame}
            _hl = [t for nm, t in _dtexts.items() if nm == 'highlight' and t.strip()]
            # 章节名本身 ≥6 字已算实质（如"三方为什么非入这张网不可"）；空弱名才必须补看点
            _substantive = len(re.sub(r'\s', '', _dtexts.get('title', ''))) >= 6
            if not _hl and not _substantive:
                divider_bad.append(i)
            # 教程 3056573a 的四个可见特征（取色渐变右块/白字/溢出/右块在文案下层）逐页核成品
            import divider_art as DA
            _brow = next((r for r in (blog or []) if r.get('page') == i), {})
            _bpath = _brow.get('bg_src') or _brow.get('bg') or ''
            if not os.path.exists(_bpath or ''):
                _bpath = _brow.get('poster_path') or ''
            _art = DA.audit_divider_art(xml, _bpath if os.path.exists(_bpath or '') else None)
            if _art:
                bad.append({'page': i, 'why': ['过渡页缺教程手法：' + '、'.join(_art)]})
    if animscale_bg:
        bad.append({'page': animscale_bg[0],
                    'why': ['背景用 animScale 推镜满铺图（放映态缩成小图）；语料法=满铺 MEDIA 视频，'
                            '豁免页走 push_in_video（共%d页）' % len(animscale_bg)]})
    if divider_bad:
        bad.append({'page': divider_bad[0],
                    'why': ['过渡页只有编号+章节名、无看点短句（共%d页）' % len(divider_bad)]})
    return {'bad': bad, 'bad_pages': len(bad), 'animscale_bg_pages': animscale_bg,
            'divider_no_highlight': divider_bad,
            'basis': 'SPEC §17 开场/封面契约（语料封面墙 _opening_corpus.png + 教程"末帧做封面"）'}


def cmd_check(a):
    wp = work_paths(a.work)
    outline = jr(wp['outline']) or die("先跑 plan")
    shots = jr(wp['shots']) or die("先跑 plan")
    res = {}
    hard = []
    # 只有"这页/这册没有视频"这一类可以被 --allow-still 降级放行。
    # 之前是按关键字（'mp4'/'视频页'/'真视频'）从 hard 里挑，结果连"内嵌 mp4 字节被换过"
    # "视频时长读不出"这种完整性硬伤也一起被吞了——干净复验的 F1 复现了这个假绿。
    video_absent_msgs = []
    field_msgs = []          # 只有 --allow-field 能放行的「整册没素材」类硬伤
    # the deliverable under test; gates that read the deck must resolve it before they run
    path = os.path.join(wp['out'], 'deck.pptx')
    if not os.path.exists(path):
        hard.append("成品 pptx 不存在（%s）——先跑 build，空口验收不算验收" % path)

    # G1 溯源：查的是**成品 pptx 里的每一个文本框**，不是 outline.json。
    # 上一版只读 outline，于是 shots.json 把第4页标题改成原文没有的「收入翻番到 3.2 亿元」、
    # 甚至直接往 slide4.xml 追加一句编造的话，check 照样退出码 0 全绿（干净复验当场复现）。
    # 规则分三层：① 正文框/表格格（非编辑命名框）必须整句能在 Word 里找到，**不设长度豁免**
    #    ——上一版跳过 <8 字的框，于是往正文框塞一句「明年营收翻三倍」（7 字）照样全绿；
    # ② 标题/kicker 允许 agent 改写措辞，但**任何数字事实必须在 Word 里出现**——改写成
    #    "0.87 亿"这种推导数字同样拦，因为对外片子的每个数字都要可举证；
    # ③ 中文数字的倍数/排名断言（翻三倍、行业第一）不吃 ② 的阿拉伯数字正则，单独匹配。
    src_norm = norm_content(' '.join(blocks_of(outline['docx'])))
    src_nocomma = src_norm.replace(',', '')
    EDITORIAL = {'title', 'section', 'chapter_no', 'hero_num',
                 'kicker', 'subtitle', 'chips', 'byline', 'highlight'}
    # 封面四件套与过渡看点是 chrome 编辑件（SPEC §17/§12 允许改写措辞），与 title 同类豁免逐字核；
    # 但上面的 NUM_ANY/CN_CLAIM 数字核对在豁免之前已跑过——任何数字事实仍须 Word 可举证，不松。
    CN_CLAIM = re.compile(u'(?:翻|增长|提升|提高|上升|下降|降低|减少|达到|突破|超过|位列|排名)'
                          u'[\\s,，.。]*[一二三四五六七八九十百千万亿两半]+')
    n_content_pages = len(jr(os.path.join(wp['out'], 'build_log.json'), []) or [])
    boxes = pptx_boxes(path) if os.path.exists(path) else []
    lost = []
    lost_nums = []
    for si, nm, raw, ntxt in boxes:
        if n_content_pages and si > n_content_pages:
            continue          # 署名附录页的来源由 G3 管（url/许可/作者），不归 Word 溯源
        if nm == 'chapter_no':
            continue          # 隔页的幽灵章号是装饰序号，不是可举证数字
        NUM_ANY = re.compile(r'\d[\d,]*\.?\d*\s*(?:%|％|万元|亿元|万|亿|元|家|台|例|人|天|城|店|个)?')
        for tok in NUM_ANY.findall(raw):
            k = norm_content(tok)
            if len(k) >= 2 and k not in src_norm and k.replace(',', '') not in src_nocomma:
                lost_nums.append({'page': si, 'number': tok.strip(), 'box': nm or 'body'})
        for tok in CN_CLAIM.findall(raw):
            k = norm_content(tok)
            if len(k) >= 2 and k not in src_norm:
                lost_nums.append({'page': si, 'number': tok.strip(), 'box': nm or 'body'})
        if nm in EDITORIAL:
            continue
        if ntxt not in src_norm:
            # 提炼契约（SPEC §12）允许把原文里两个子句并到同一行显示（"45.37%，三级医院 89.6%"），
            # 整框逐字比对会把这种合法重排当成编造（实测 v20 报 123 个"框"，其中数字越界 0 个）。
            # 改成按片段核：每个 ≥4 字的片段都要能在原文里找到；数字另有上面的专门核对。
            frags = [re.sub(r'\s', '', f) for f in
                     re.split(FRAG_SPLIT, raw) if f and f.strip()]
            bad_frags = [f for f in frags if len(f) >= 4 and f not in src_norm]
            if bad_frags:
                lost.append({'page': si, 'box': nm or 'body', 'text': raw.strip()[:50],
                             'off_fragments': bad_frags[:3]})
    lost_outline = []
    # 口径与框级检查、G13 一致：都走 norm_content（去空白去标点）。
    # 旧写法自己造了一个"只去空白"的 dnorm，于是引擎把原文的顿号 '、' 写成 '，'
    # （或正文来自表格，而 dnorm 只收了段落）就把忠实转写判成编造：实测 v5 报 10 条，
    # 逐条比对全部是标点/表格来源差异，一个字的内容都没多。
    for pg in outline['pages']:
        for line in (pg.get('body') or []):
            key = norm_content(line)
            if len(key) > 8 and key[:30] not in src_norm:
                lost_outline.append({'page': pg['page'], 'text': line[:50]})
    res['G1_溯源'] = {'off_docx_boxes': len(lost), 'box_sample': lost[:8],
                      'off_docx_numbers': len(lost_nums), 'number_sample': lost_nums[:8],
                      'off_docx_outline_lines': len(lost_outline),
                      'sample': lost_outline[:8], 'boxes_checked': len(boxes)}
    if lost:
        hard.append("G1: 成品里有 %d 个文本框的文字在 Word 原文里找不到（页%s 的「%s」）"
                    "——成品文字必须可溯源，改标题可以，凭空写不行" % (
                        len(lost), lost[0]['page'], lost[0]['text']))
    if lost_nums:
        hard.append("G1: 成品里有 %d 个数字在 Word 原文里找不到（页%s 的「%s」）"
                    "——对外片子的每个数字都要可举证，推导/改写数字同样拦" % (
                        len(lost_nums), lost_nums[0]['page'], lost_nums[0]['number']))
    if lost_outline:
        hard.append("G1: outline 里有 %d 条文字不在 Word 原文（%s）" % (
            len(lost_outline), lost_outline[0]['text']))

    # G13 覆盖：Word 里每句话都必须出现在**成品 pptx 的 XML** 里。
    # 之前它只读 outline.json，于是 place_text 把 body 截成 4 句、成品静默丢 6 句时，
    # G1 和 G13 照样报 0 —— 闸必须查最终交付物，不能查中间计划。
    # 比对按“每页阅读顺序拼接”做：标题是从 body[0] 里切出来的短标签，正文框只留后半句，
    # 一句话拆在两个文本框里，按顺序拼回来才算没丢（去掉标点/空白后比）。
    deck_text = pptx_all_text(path) if path and os.path.exists(path) else ''
    deck_pages = pptx_slide_texts(path) if path and os.path.exists(path) else []
    deck_tbl = pptx_table_texts(path) if path and os.path.exists(path) else []
    in_pages = set()
    for pg in outline['pages']:
        for line in [pg['headline']] + list(pg.get('body') or []):
            in_pages.add(re.sub(r'\s+', '', line))
    lost_src = []
    n_src_sent = 0
    # 有账可查的取舍：plan 记在 outline['dropped'] 里的条目（目录条目这类导航件）不计覆盖，
    # 但数量与原文都要随报告列出——没有这条账，"缩源"就成了唯一的出路（v17 正是这样造假的）
    drop_keys = set()
    for x in (outline.get('dropped') or []):
        k = norm_content(x.get('text'))
        if k:
            drop_keys.add(k)
            drop_keys.add(norm_content(strip_number(x.get('text'))))
        # 丢表的单元格也要进 drop_keys：G13 逐 <a:t> 核，只记表头会让每个单元格都被算成丢句
        for c in (x.get('cells') or []):
            kc = norm_content(c)
            if kc:
                drop_keys.add(kc)
    n_declared_dropped = 0
    # 提炼契约（SPEC §12）下这些页屏幕上只有短句，逐句上屏那条判据换成 G24 的事实单元覆盖；
    # 台账页不吃这条豁免（表格另有逐格核对），没进过任何页的原文也照样算丢内容。
    blog13 = jr(os.path.join(wp['out'], 'build_log.json'), []) or []
    refined_pages = {r['page'] for r in blog13 if r.get('display_mode') == 'refined'}
    sent_page = {}
    page_blob = {}
    for pg in outline['pages']:
        parts = []
        for b in (pg.get('body') or []):
            k1, k2 = norm_content(b), norm_content(strip_number(b))
            parts.append(k1)
            for kk in (k1, k2):
                if len(kk) >= 6:
                    sent_page.setdefault(kk, pg['page'])
        page_blob[pg['page']] = ''.join(parts)
    n_refined_exempt = 0
    n_split_exempt = 0
    split_runs = []
    _brief13 = outline.get('brief') or {}
    budget13 = bool(_brief13.get('pages'))
    ledger13 = {e['page']: set(norm_content(s) for s in (e.get('source') or []))
                for e in (_brief13.get('ledger') or [])}
    n_budget_compressed = 0
    ledger_missing_pages = []
    if budget13:
        ledger_missing_pages = [pg['page'] for pg in outline['pages']
                                if pg['role'] not in ('cover', 'closing', 'divider')
                                and not pg.get('table') and pg['page'] not in ledger13]
    for b in blocks_of(outline['docx']):
        k = norm_content(b)
        if len(k) < 6:
            continue
        if k in drop_keys:
            n_declared_dropped += 1
            continue
        keys = {k, norm_content(strip_number(b))} - {''}
        pg_no = sent_page.get(k) or sent_page.get(norm_content(strip_number(b)))
        if budget13 and pg_no is not None and pg_no in ledger13:
            _ls = ledger13[pg_no]
            if k in _ls or norm_content(strip_number(b)) in _ls:
                # 页数契约：这句原文被压缩留账（上屏的是台账 kept 的提炼句），不算丢内容
                n_budget_compressed += 1
                continue
        if pg_no is None:
            # plan 会把一段长正文按预算切成好几句，逐句键自然对不上整段原文：
            # 这段原文整个落在哪一页，就按那一页的显示模式判定
            for np_, blob in page_blob.items():
                if k and len(k) >= 6 and k in blob:
                    pg_no = np_
                    break
        if pg_no in refined_pages:
            n_refined_exempt += 1
            continue
        if pg_no is None:
            # 还有一种：这段原文被 plan 拆到了**连续几页**（实测 v10 一句 300 字的采购口径
            # 段落被切成页 60~67 共 8 页）。这时候整段当然对不上任何单页 blob——但它没被丢：
            # 从命中的那一页起顺着读序累加，只有"这一段里每一页都是提炼页"且
            # 累加文本真的含这段原文（去标点后逐字比对）才算豁免；断一页、缺一截都照样点名。
            pages_sorted = sorted(page_blob)
            hit_chain = None
            for si, pn in enumerate(pages_sorted):
                blob = page_blob[pn]
                if not blob or len(blob) < 6 or k[:6] not in blob:
                    continue
                acc, run = u'', []
                for w in pages_sorted[si:si + 16]:
                    if w not in refined_pages:
                        break          # 中间夹了一页非提炼页（台账/隔页）：这条链不成立
                    acc += page_blob[w]
                    run.append(w)
                    if k in acc or norm_content(strip_number(b)) in acc:
                        break
                if len(run) >= 2 and (k in acc or norm_content(strip_number(b)) in acc):
                    hit_chain = run
                    break
            if hit_chain:
                n_refined_exempt += 1
                n_split_exempt += 1
                if len(split_runs) < 8:
                    split_runs.append({'start': hit_chain[0], 'pages': len(hit_chain),
                                       'text': b[:36]})
                continue
        n_src_sent += 1
        # 章节页按语料做法会去掉"一、/第X章"这类编号装饰（strip_number），去掉编号后的正文
        # 必须在成品里出现；两种键都找不到才算丢内容
        if not any(any(kk in dp for kk in keys) for dp in deck_pages):
            lost_src.append(b[:60])
    # 表格逐格核对：单元格文字普遍只有 2-5 字，落在上面那条 <6 字的豁免线以下，
    # 于是"整张台账没装进成品"这件事 G13 原本看不见。表格内容单独按格查，不吃长度豁免。
    lost_cells = []
    n_cells = 0
    for pg in outline['pages']:
        tbl = pg.get('table')
        if not tbl:
            continue
        # 只在表格区里核：结论行/大数字常常复述单元格里的值，拿整页文字核会让
        # "把某一格抠掉"这种篡改照样报绿（实测夹具就是这么漏的）
        dp = deck_tbl[pg['page'] - 1] if 0 < pg['page'] <= len(deck_tbl) else ''
        for row in [tbl.get('header') or []] + list(tbl.get('rows') or []):
            for cell in row:
                k = norm_content(cell)
                if not k:
                    continue
                n_cells += 1
                if k not in dp:
                    lost_cells.append({'page': pg['page'], 'cell': (cell or '')[:24]})
    dropped_in_build = []
    if os.path.exists(os.path.join(wp['out'], 'build_log.json')):
        for r in jr(os.path.join(wp['out'], 'build_log.json'), []):
            if (r.get('body_total', 0) != r.get('body_placed', 0)
                    and r.get('display_mode') != 'refined'):
                dropped_in_build.append({'page': r['page'], 'total': r['body_total'],
                                         'placed': r['body_placed']})
    over = [r for r in jr(os.path.join(wp['out'], 'build_log.json'), [])
            if r.get('overflow') or r.get('overflow_lines')]
    res['G13_原文覆盖'] = {'source_sentences': n_src_sent,
                           'declared_dropped': n_declared_dropped,
                           'budget_compressed_to_ledger': n_budget_compressed,
                           'ledger_missing_pages': ledger_missing_pages,
                           'dropped_sample': [x.get('text', '')[:48]
                                              for x in (outline.get('dropped') or [])[:6]],
                           'pptx_slides_read': len(deck_pages),
                           'table_cells': n_cells, 'table_cells_missing': len(lost_cells),
                           'table_sample': lost_cells[:8],
                           'refined_exempt_sentences': n_refined_exempt,
                           'split_across_pages': n_split_exempt,
                           'split_samples': split_runs,
                           'refined_exempt_pages': sorted(refined_pages),
                           'missing_from_pptx': len(lost_src), 'sample': lost_src[:8],
                           'pages_dropping_body': dropped_in_build,
                           'pages_overflowing_zone': [o['page'] for o in over]}
    if lost_cells:
        hard.append("G13: Word 表格里有 %d 个单元格在成品对应页找不到（例：页%s 的「%s」）"
                    "——表格文字短，不吃句子长度豁免，丢一格也算丢内容" % (
                        len(lost_cells), lost_cells[0]['page'], lost_cells[0]['cell']))
    if n_src_sent and not deck_pages:
        hard.append("G13: 成品里一页文本都没读到（%d 句原文无法核对）——先 build" % n_src_sent)
    if lost_src:
        hard.append("G13: Word 里有 %d 句原文在**成品 pptx** 里找不到（例：%s）——丢内容比难看严重" % (
            len(lost_src), lost_src[0]))
    if dropped_in_build:
        hard.append("G13: %d 页在装配时丢了正文句子（%s）" % (
            len(dropped_in_build), dropped_in_build[0]))
    if over:
        hard.append("G13: %d 页文字超出留白区（页 %s）——要拆页，不许缩字号硬塞" % (
            len(over), [o['page'] for o in over][:8]))

    if outline.get('verbatim'):
        wall_hits = [h for h in hard if h.startswith('G19:') and u'dense_text_wall' in h]
        if wall_hits:
            hard = [h for h in hard if h not in wall_hits]
            res['降级交付_全量留档册'] = {
                'declared_by': 'outline.verbatim（plan --verbatim）',
                'suppressed_findings': wall_hits,
                'meaning': u'这一版按 SPEC §12 的例外走"正文逐字上屏"，文字墙与低同级分是这种册的'
                           u'必然结果；它是留档册，不是发布会册，交付说明必须这样写'}
            print('注意：--verbatim 全量留档册，G19 文字墙 %d 条改为显式声明（不是电影级发布会册）'
                  % len(wall_hits))

    # G3 素材
    man = []
    mp = os.path.join(wp['materials'], 'manifest.jsonl')
    if os.path.exists(mp):
        man = [json.loads(l) for l in io.open(mp, encoding='utf-8') if l.strip()]
    # G3 只究"真的铺进成品的那张"：被装配拒用（离题/低清降级成色场）的 manifest 老条目
    # 不算成品硬伤，否则"拒用离题图"这个正确动作反而被记一笔。没有 build_log 时退回全量
    # （反证夹具只摆 manifest 不装配，闸必须照样咬）。
    _blog3 = jr(os.path.join(wp['out'], 'build_log.json'), []) or []
    _used3 = set()
    for _r in _blog3:
        _bg = str(_r.get('bg') or '').replace('\\', '/')
        if _bg and _bg != 'video' and 'bg_fallback' not in _bg:
            _used3.add(_r.get('page'))
    _scope3 = (lambda m: m.get('page') in _used3) if _blog3 else (lambda m: True)
    small = [m for m in man if min(m.get('w', 0), m.get('h', 0)) < TH['fetch_short_edge']
             and _scope3(m)]
    nolic = [m for m in man if not m.get('license') and _scope3(m)]
    missing = [m for m in man if not os.path.exists(m['file'])]
    from PIL import Image
    off_ratio = []
    for m in man:
        try:
            im = Image.open(m['file'])
            r = im.size[0] / float(im.size[1])
            if not (TH['ar_min'] <= r <= TH['ar_max']):
                off_ratio.append({'page': m['page'], 'ratio': round(r, 3), 'size': list(im.size)})
        except Exception:
            off_ratio.append({'page': m['page'], 'ratio': None})
    # 相关度只究"真的铺进成品的那张"（口径与 small/nolic 同一把尺子 _scope3）。
    low_rel = [m for m in man if int(m.get('relevance', 100)) < TH['relevance_min']
               and _scope3(m)]
    # safety is re-checked here, not only at fetch time: a manifest assembled by hand, an older
    # run, or a fetch that skipped the filter must still be rejected before the deck ships.
    allow = sensitive_allow(outline, getattr(a, 'allow_sensitive', None))
    unsafe = [m for m in man if _safe.reject_reason(m.get('source_page') or m.get('url') or '', allow)]
    # 黑白/低饱和要在验收端也量一次像素：fetch 只判当时的文件，
    # 而 manifest 里的老条目、手工 adopt 进来的、被换过的图都可能已经是一张档案照（实测页 5/38）
    gray = [m for m in man if m.get('file') and os.path.exists(m['file'])
            and _safe.is_grayscale(m['file'])]
    no_frame = [m for m in man if not m.get('source_page')]
    # 联网口径（SKILL 硬约束 7）也要机器核对：国内资源直连、国外资源走代理 7897。
    # fetch 会把实际用的代理写进 manifest 的 proxy_used，这里核对"说的是不是做的"。
    net_bad = []
    for m in man:
        url = m.get('url') or ''
        used = m.get('proxy_used')
        if is_foreign(url) and not used:
            net_bad.append({'page': m.get('page'), 'url': url[:70],
                            'why': '国外站点却没记代理（无法证明走的是 7897）'})
        elif (not is_foreign(url)) and used:
            net_bad.append({'page': m.get('page'), 'url': url[:70],
                            'why': '国内资源却挂了代理 %s' % str(used)[:40]})

    # 内容完整性：manifest 记了 sha1/bytes 却从没复算过，于是把 materials/p003.jpg 换成另一张
    # 同尺寸图，G3 的"短边/比例/相关度"全都照旧报绿（audit4 [中]3）。素材"可举证"必须包含
    # "今天躺在目录里的这一份，就是当初核对过许可和内容的那一份"。
    swapped = []
    for m in man:
        f = m.get('file')
        if not f or not os.path.exists(f):
            continue
        try:
            real = sha1(f)
        except Exception:
            swapped.append({'page': m.get('page'), 'why': '无法读取'})
            continue
        if m.get('sha1') and real != m['sha1']:
            swapped.append({'page': m.get('page'), 'why': 'SHA1 与 manifest 不符',
                            'file': os.path.basename(f)})
        elif m.get('bytes') and os.path.getsize(f) != int(m['bytes']):
            swapped.append({'page': m.get('page'), 'why': '字节数与 manifest 不符',
                            'file': os.path.basename(f)})
    style_bad = []
    for m in man:
        if m.get('search_channel') in ('commons', 'openverse', 'unsplash') and m.get('file'):
            sr = photo_style_reason(m['file'], (jr(os.path.join(wp['out'], 'build_meta.json'), {}) or {}).get('palette') or 'tech_blue')
            if sr:
                style_bad.append({'page': m.get('page'), 'why': sr})
    _rev = jr(os.path.join(wp['materials'], 'review.json'), {}) or {}
    _sheet = os.path.join(wp['materials'], 'review_sheet.png')
    review_missing = []
    if man:
        if not os.path.exists(_sheet):
            review_missing.append({'page': None,
                                   'why': '没跑 materials-review 生成目检联系表'})
        for m in man:
            v = _rev.get(str(m['page'])) or _rev.get(m['page']) or {}
            if v.get('verdict') != 'keep':
                review_missing.append({'page': m['page'],
                                       'why': '目检台账没记 keep（agent 必须真看联系表逐张对题再记）'})
    res['G3_素材'] = {'count': len(man), 'under_1920': len(small), 'no_license': len(nolic),
                      'file_missing': len(missing), 'off_169': off_ratio, 'low_relevance': low_rel,
                      'grayscale_files': [m.get('page') for m in gray],
                      'swapped_files': swapped, 'network_rule_bad': net_bad,
                      'review_missing': review_missing,
                      'style_bad': style_bad}

    # manifest 的数字只是"当初抓到了多大的原图"；成品里嵌的可能是被压过的另一张，必须量交付物
    check_g3_media(path, res, hard, shots,
                    jr(os.path.join(wp['out'], 'build_log.json'), []), a.work)
    if small or nolic or missing:
        hard.append("G3: 素材短边不足 %d 张 / 无许可 %d 条 / 文件缺失 %d 条" % (len(small), len(nolic), len(missing)))
    if off_ratio:
        hard.append("G3: %d 张整页背景不是 16:9（%s）——竖图铺满会被拉坏或把主体挤出画布" % (
            len(off_ratio), off_ratio[0]))
    if low_rel:
        hard.append("G3: %d 张素材与页面实体相关度 <%d%%（页%s）——离题图比没图更糟" % (
            len(low_rel), TH['relevance_min'], low_rel[0]['page']))
    if review_missing:
        hard.append("G3: %d 条素材没过目检留痕（%s）——跑 materials-review 看联系表，逐张对题后记 keep/reject；"
                    "词面相关度满分也拦不住语义离题（渡轮当车队/历史版画当救护车实测过）" % (
                        len(review_missing), review_missing[0]))
    if style_bad:
        hard.append("G3: %d 张 stock 背景没过美学闸（页%s：%s）——纪实照没有模板的电影级舞台感；"
                    "改走九段式文生图投放卡，或换过闸的图（SPEC §24）" % (
                        len(style_bad), style_bad[0]['page'], style_bad[0]['why']))
    if swapped:
        hard.append("G3: %d 个素材文件与 manifest 记录的 SHA1/字节数不符（%s）——入库时核对过许可与内容的"
                    "那一份已经被换掉，成品里的图就不再可举证" % (len(swapped), swapped[:3]))
    if net_bad:
        hard.append("G3: %d 条素材的联网口径对不上（%s）——硬约束 7 要求国内直连、国外走代理 7897，"
                    "manifest 的 proxy_used 必须与域名一致" % (len(net_bad), net_bad[:2]))


    if unsafe:
        hard.append("G3: %d 张已入库素材命中敏感/离域黑名单（页%s：%s｜拦下原因：%s）"
                    "——安全判定不能只在 fetch 里做" % (
            len(unsafe), unsafe[0]['page'], unsafe[0].get('source_page'),
            _safe.reject_reason(unsafe[0].get('source_page') or unsafe[0].get('url') or '', allow)))
    if gray:
        hard.append(u"G3: %d 张已入库素材量出来是黑白/低饱和图（页%s），现代业务册不放档案照"
                    u"——fetch 判过一次不算，验收端要重量像素" % (
            len(gray), gray[0].get('page')))
    if no_frame:
        hard.append("G3: %d 条素材没有 source_page 字段，无法复检内容与许可，视为不可举证" % len(no_frame))

    # G2 提示词结构：check **自己重读 promptpack/*.txt 重新打分**，不信 pack.json。
    # 上一版只读 pack.json 的结果，删掉 pack.json 后 G2 变成 bad_pages:0 的空闸——
    # 手改过的提示词、或根本没跑过 pack 的工作目录，都能带着这一闸报全绿（audit4 [中]2）。
    n_ok, pk_bad, _cards = validate_prompts(wp, shots, outline)
    stored = jr(os.path.join(wp['promptpack'], 'pack.json'))
    divergence = None
    if stored is None:
        pack_state = '未跑 pack（G2 已按当前提示词文件重评）'
    else:
        stored_bad = sorted(b['page'] for b in (stored.get('bad') or []))
        now_bad = sorted(b['page'] for b in pk_bad)
        if stored_bad != now_bad:
            divergence = {'pack_said': stored_bad, 'files_now': now_bad}
        pack_state = '一致' if not divergence else 'pack.json 与当前提示词文件不一致'
    res['G2_提示词'] = {'bad_pages': len(pk_bad), 'ok_pages': n_ok,
                        'detail': [b['problems'] for b in pk_bad[:5]],
                        'pack_run': stored is not None, 'pack_state': pack_state,
                        'pack_divergence': divergence}
    if pk_bad:
        hard.append("G2: %d 页提示词不合格，第一页原因：%s" % (
            len(pk_bad), pk_bad[0]['problems'][0]))
    if divergence:
        hard.append("G2: pack.json 记录的不合格页是 %s，按当前文件重评却是 %s —— 提示词被改过却没重跑 pack，"
                    "投放卡上的内容已经不是这一版" % (divergence['pack_said'], divergence['files_now']))
    # 技法卡 ID 必须真存在于 kb/deck_tech.json：写一个不存在的卡等于这页没有技法依据（SPEC 硬约束 2）
    card_ids = {c['id'] for c in kb('deck_tech.json')['cards']}
    bogus = []
    for s in shots['pages']:
        tr = (s.get('tech_recipe') or '').strip()
        if tr and tr not in card_ids:
            bogus.append({'page': s['page'], 'tech_recipe': tr[:40]})
    res['G2_提示词']['unknown_tech_recipe'] = bogus
    res['G2_提示词']['cards_available'] = len(card_ids)
    if bogus:
        hard.append("G2: %d 页的 tech_recipe 不是 kb/deck_tech.json 里的技法卡（%s）"
                    "——参数必须指到实测卡片，不许自己编卡名" % (len(bogus), bogus[:3]))

    # G5 留白占位（几何）：文字框必须落在留白区内
    from pptx import Presentation
    if not os.path.exists(path):
        prs = None
    else:
        prs = Presentation(path)
        final_zone = {}
        for _r in (jr(os.path.join(wp['out'], 'build_log.json'), []) or []):
            if _r.get('zone_used'):
                final_zone[_r.get('page')] = _r['zone_used']
        zone_viol = []
        for idx, slide in enumerate(prs.slides, 1):
            sh = next((x for x in shots['pages'] if x['page'] == idx), None)
            if not sh:
                continue
            # 量的是装配真正用的文字区（build_log.zone_used），不是分镜里那个可能已被
            # 版式改判掉的旧 reserved_zone
            z = (final_zone.get(idx) or sh.get('reserved_zone') or zone_for(sh['role']))
            zx0, zy0, zx1, zy1 = z[0], z[1], z[0] + z[2], z[1] + z[3]
            for sp in slide.shapes:
                if not sp.has_text_frame or not sp.text_frame.text.strip():
                    continue
                x0, y0 = sp.left / EMU_W, sp.top / EMU_H
                x1, y1 = (sp.left + sp.width) / EMU_W, (sp.top + sp.height) / EMU_H
                if x0 < zx0 - 0.01 or y0 < zy0 - 0.01 or x1 > zx1 + 0.01 or y1 > zy1 + 0.01:
                    zone_viol.append({'page': idx, 'shape': sp.name,
                                      'box': [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)],
                                      'zone': [round(zx0, 2), round(zy0, 2), round(zx1, 2), round(zy1, 2)]})
        res['G5_留白占位'] = {'violations': len(zone_viol), 'sample': zone_viol[:6]}
        if zone_viol:
            hard.append("G5: %d 个文本框越出留白区（页%s）" % (len(zone_viol), zone_viol[0]['page']))

        # G7 可编辑
        ntxt = sum(1 for sl in prs.slides for sp in sl.shapes if sp.has_text_frame and sp.text_frame.text.strip())
        pics = sum(1 for sl in prs.slides for sp in sl.shapes if shape_kind(sp) == 13)
        res['G7_可编辑'] = {'text_shapes': ntxt, 'pictures': pics, 'slides': len(list(prs.slides))}
        if ntxt == 0:
            hard.append("G7: 成品没有任何真文本框，等于图片拼贴")

        # G8 版式：越界 + 尺寸
        oob = []
        for idx, slide in enumerate(prs.slides, 1):
            for sp in slide.shapes:
                if sp.left is None:
                    continue
                if sp.name in ('scrim', 'morph_anchor'):
                    continue
                if (sp.left < 0 or sp.top < 0 or sp.left + sp.width > EMU_W + 1
                        or sp.top + sp.height > EMU_H + 1
                        or sp.width > EMU_W * 1.5 or sp.height > EMU_H * 1.5):
                    oob.append({'page': idx, 'shape': sp.name,
                                'box': [sp.left, sp.top, sp.width, sp.height]})
        res['G8_版式'] = {'size': [prs.slide_width, prs.slide_height], 'out_of_bounds': len(oob),
                          'sample': oob[:6]}
        # 手写注入的 XML（scrim 渐变、立体展台的 sp3d）一旦把 fill 排在 ln 后面，真 PowerPoint
        # 会弹"该内容需要修复"，而 WPS 照样能渲染——所以这条按成品逐形状量 CT_SpPr 的子元素顺序。
        spbad = spPr_order_defects(path)
        res['G8_版式']['spPr_order_defects'] = spbad[:8]
        # 立体展台压在文字下层是设计，但它的边咬到**别的**行就是事故——实测第一轮渲染里金框压住了
        # "1.1.1 城市分层"和下一行正文，越界闸与对比度闸都量不到。这条按成品逐页量。
        panel_hits = []
        for idx, slide in enumerate(prs.slides, 1):
            panels = [sp for sp in slide.shapes if sp.name == 'panel3d']
            if not panels:
                continue
            for pn in panels:
                for sp in slide.shapes:
                    if sp is pn or sp.left is None or not sp.has_text_frame:
                        continue
                    if not (sp.text_frame.text or '').strip():
                        continue
                    inside = (pn.left <= sp.left and pn.top <= sp.top
                              and pn.left + pn.width >= sp.left + sp.width
                              and pn.top + pn.height >= sp.top + sp.height)
                    x_ov = min(pn.left + pn.width, sp.left + sp.width) - max(pn.left, sp.left)
                    y_ov = min(pn.top + pn.height, sp.top + sp.height) - max(pn.top, sp.top)
                    if x_ov > 0 and y_ov > 0 and not inside:
                        panel_hits.append({'page': idx, 'text': sp.text_frame.text[:18],
                                           'overlap_y': int(y_ov)})
        res['G8_版式']['panel3d_line_collisions'] = panel_hits[:6]
        # 孤字尾行：卡片早就承诺"G8 孤字"，这条是把它真的兑现（e6 封面实测到末行只剩"表）"）
        orphans = []
        _asm_n = len(shots['pages'])
        for idx, slide in enumerate(prs.slides, 1):
            # 片尾来源署名页是法律件（一页上千字 URL），不是版面：孤字判据不适用（G23 同口径豁免）
            if idx > _asm_n:
                continue
            for sp in slide.shapes:
                if sp.left is None or not sp.has_text_frame or sp.name in ('scrim', 'morph_anchor'):
                    continue
                for para in sp.text_frame.paragraphs:
                    txt = ''.join(r.text for r in para.runs).strip()
                    if not txt:
                        continue
                    pts = [r.font.size.pt for r in para.runs if r.font.size]
                    pt = max(pts) if pts else 18.0
                    nln, tail = orphan_tail(txt, pt, sp.width / float(EMU_W))
                    if nln >= 2 and 0 < tail <= TH['orphan_tail_max_chars']:
                        orphans.append({'page': idx, 'pt': pt, 'tail': txt[-tail:],
                                        'text': txt[:26]})
        res['G8_版式']['orphan_tail_lines'] = orphans[:6]
        # 假图表反证：画了 ≥3 根柱子却没有 3 个柱底类别名，读者无从知道"在比什么"。
        # v10 真渲染页 20/58 就是这样——'500 名女性'、'300 人'、'500 台' 被画成同族三根柱，
        # 数字之间根本没有可比性，图比文字更像假数据。
        naked_charts = []
        for idx, slide in enumerate(prs.slides, 1):
            bars = [sp for sp in slide.shapes
                    if re.match(u'bar_\\d+$', sp.name or u'')]
            if len(bars) < 3:
                continue
            labs = [sp for sp in slide.shapes if (sp.name or u'').startswith(u'bar_label')
                    and sp.has_text_frame
                    and re.sub(u'\\s', u'', sp.text_frame.text or u'')]
            if len(labs) < 3:
                naked_charts.append({'page': idx, 'bars': len(bars), 'labels': len(labs)})
        res['G8_版式']['naked_charts'] = naked_charts[:6]
        if naked_charts:
            hard.append("G8: %d 页画了 ≥3 根柱子但柱底没有类别名（页%s）——"
                        "不同单位的数摆成一组柱子就是假图表，宁可用大数字或短句" % (
                            len(naked_charts), [c['page'] for c in naked_charts[:3]]))
        if naked_charts:
            hard.append("G8: %d 页画了 ≥3 根柱子但柱底没有类别名（页%s）——"
                        "不同单位的数摆成一组柱子就是假图表，宁可用大数字或短句" % (
                            len(naked_charts), [c['page'] for c in naked_charts[:3]]))
        if prs.slide_width != EMU_W or prs.slide_height != EMU_H:
            hard.append("G8: 页幅不是 16:9 的 12192000x6858000")
        if oob:
            hard.append("G8: %d 个形状越出画布" % len(oob))
        if spbad:
            hard.append("G8: %d 个形状的 spPr 子元素顺序/重复不合 CT_SpPr（%s）——"
                        "真 PowerPoint 会提示修复，WPS 能渲染不代表文件合规" % (
                            len(spbad),
                            '；'.join('页%d %s：%s' % (b['slide'], b['shape'], b['why'])
                                      for b in spbad[:2])))
        if panel_hits:
            hard.append("G8: %d 处立体展台的边咬到了别的文字行（页%s）——"
                        "展台只能包住它的锚点文字，压到上一行/下一行就是版式事故" % (
                            len(panel_hits), [(h['page'], h['text']) for h in panel_hits[:3]]))
        if orphans:
            hard.append("G8: %d 处标题/正文折行后末行只剩 %d 个字以内的孤字（%s）——"
                        "要么把标题改短，要么降一档字号让它整行放下，不许留孤字尾行" % (
                            len(orphans), TH['orphan_tail_max_chars'],
                            [(o['page'], o['pt'], o['tail']) for o in orphans[:3]]))

        # G6 节奏：**一律量成品**（slide XML 的 transition/timing + 内嵌媒体字节），不读 shots.json
        # 的计划字段。以前 plan 给每页都写了 tech_recipe，于是 motion_page_ratio 恒为 1.0，
        # 而真实片子里一页都不动（audit4 [中]4）；morph 时长同理改读 p14:dur。
        facts = deck_motion_facts(path)
        content_slides = [k for k in sorted(facts)
                          if not n_content_pages or k <= n_content_pages]
        motion_pages = [k for k in content_slides if facts[k]['morph_ms']
                        or facts[k]['entr_nodes'] or facts[k]['has_video'] or facts[k]['has_gif']]
        motion_ratio = (round(len(motion_pages) / float(len(content_slides)), 3)
                        if content_slides else 0)
        div_still = []
        for s_ in shots['pages']:
            if s_.get('role') != 'divider':
                continue
            f_ = facts.get(s_['page']) or {}
            # 隔页要的是整页位移（平滑/视频/动图），文字淡入不算——SPEC §2 原话是"morph 或视差"
            if not (f_.get('morph_ms') or f_.get('has_video') or f_.get('has_gif')):
                div_still.append(s_['page'])

        morph_durs = {k: facts[k]['morph_ms'] for k in content_slides if facts[k]['morph_ms']}
        bad_morph = [{'slide': k, 'dur': v} for k, v in sorted(morph_durs.items())
                     if not (TH['morph_ms_min'] <= v <= TH['morph_ms_max'])]
        bad_dur = []
        for s_ in shots['pages']:
            if s_.get('asset_kind') != 'video_bg':
                continue
            v = find_gen(a.work, s_['page'], 'video')
            if not (v and os.path.exists(v)):
                continue
            d = probe_duration(v)
            if d is None:
                bad_dur.append({'page': s_['page'], 'dur': None,
                                'why': '读不出时长（文件损坏或不是真视频）——不能当合格'})
            elif not (TH['video_dur_min'] <= d <= TH['video_dur_max']):
                bad_dur.append({'page': s_['page'], 'dur': round(d, 1)})
        # 技法卡不是写完就算数：凡是引擎能产出的卡（RECIPE_TOKENS），分镜声明了就必须能在
        # 这一页的成品 XML 里数到对应 token。14 张卡里曾有 2 张只活在文档里，G2 只验卡 id
        # 存不存在，所以声明与产出脱节一路绿灯。手工类卡（布尔/直线动画/音效）不进这张表；主题色锁（palette-lock）在表里，键名必须与 kb/deck_tech.json 的卡 id 一致——旧键 theme-palette-lock 不是任何卡 id，声明永远查不到，等于这条 token 校验是死代码（2026-11-09 查实）。
        recipe_missing = []
        # 装配时可能按版式原型改判（mask-dim 在分栏页不成立 -> split-reflow），
        # 成品量的是 build_log 的最终技法，不是分镜里那个已被改掉的旧值
        final_recipe = {}
        for _r in (jr(os.path.join(wp['out'], 'build_log.json'), []) or []):
            if 'tech_recipe_final' in _r:
                final_recipe[_r.get('page')] = _r['tech_recipe_final']
        for s_ in shots['pages']:
            # '' 是"装配时判定这页不该挂任何技法卡"，不是"没记"——不能回退去读分镜里那个旧值
            tr_ = (final_recipe[s_['page']] if s_['page'] in final_recipe
                   else (s_.get('tech_recipe') or '').strip())
            tok = RECIPE_TOKENS.get(tr_)
            f_ = facts.get(s_['page']) or {}
            if not tok or not f_:
                continue
            if not tok[1](f_):
                recipe_missing.append({'page': s_['page'], 'tech_recipe': tr_, '要': tok[0]})
        res['G6_节奏'] = {'measured_from': '成品 pptx 的 slide XML 与内嵌媒体',
                           'slides_with_motion': motion_pages,
                           'motion_page_ratio': motion_ratio,
                           'morph_ms_in_deck': morph_durs,
                           'morphs_out_of_range': bad_morph,
                           'videos_out_of_range': bad_dur,
                           'divider_pages_without_motion': div_still,
                           'table_transition_pages': [k for k in content_slides
                                                      if [x_ for x_ in facts[k]['trans_kinds']
                                                          if x_ in TRANS_KINDS]],
                           'pages_with_sp3d': [k for k in content_slides if facts[k]['has_sp3d']],
                           'recipe_xml_missing': recipe_missing,
                           'anim_nodes_total': sum(facts[k]['anim_nodes'] for k in content_slides)}
        if motion_ratio < TH['motion_ratio_min']:
            hard.append("G6: 成品里只有 %d/%d 页真的带动效/转场/视频（%.0f%%，SPEC §2 要求 ≥%.0f%%）"
                        "——这是数 slide XML 得到的，不是分镜计划里写了 motion" % (
                            len(motion_pages), len(content_slides), 100 * motion_ratio,
                            100 * TH['motion_ratio_min']))
        if div_still:
            hard.append("G6: %d 个隔页在成品里既没有平滑也没有入场动效（页%s），SPEC §2 明写隔页必带 morph 或视差" % (
                len(div_still), div_still[:6]))
        if bad_dur or bad_morph:
            hard.append(u"G6: %d 个视频时长不在 %.1f-%.1f 秒或读不出；%d 页平滑不在 %d-%d 毫秒（实测 %s）"
                        % (len(bad_dur), TH['video_dur_min'], TH['video_dur_max'],
                           len(bad_morph), TH['morph_ms_min'], TH['morph_ms_max'], bad_morph[:3]))
        if recipe_missing:
            hard.append(u"G6: %d 页声明了技法卡却在成品 XML 里找不到对应写法（%s）——"
                        u"技法卡只写在分镜里不等于装进了片子" % (
                            len(recipe_missing), recipe_missing[:3]))



        # G14 平滑真的写进成品了吗，而且**逐页**配对锚点。
        # 只比总数（5 转场 / 6 锚点页）会漏掉"锚点全落在别的页"这种情况，实测 3/5 页平滑不动。
        import zipfile as _zf
        _z = _zf.ZipFile(path)
        sl_names = sorted([f for f in _z.namelist()
                           if re.match(r'ppt/slides/slide\d+\.xml$', f)],
                          key=lambda s: int(re.findall(r'\d+', s)[0]))
        per_page = []
        for k, f in enumerate(sl_names, 1):
            raw = _z.read(f)
            has_tr = b'morph option' in raw
            has_an = b'morph_anchor' in raw
            if has_tr:
                prev_raw = _z.read(sl_names[k - 2]) if k >= 2 else b''
                per_page.append({'slide': k, 'anchor_here': has_an,
                                 'anchor_prev': (b'morph_anchor' in prev_raw) if k >= 2 else None})
        unpaired = [p for p in per_page if not (p['anchor_here'] and p['anchor_prev'])]
        want_morph = sum(1 for s in shots['pages'] if s.get('morph_from_prev'))
        res['G14_平滑存在'] = {'planned': want_morph, 'in_xml': len(per_page),
                               'unpaired': unpaired}
        if want_morph and len(per_page) == 0:
            hard.append("G14: 分镜要求 %d 页平滑，成品 XML 里却有 0 页带 morph —— 平滑根本没写进去" % want_morph)
        if unpaired:
            hard.append("G14: %d 页有 morph 转场但锚点没和前一页配对（%s）——转场元素在，画面不会动" % (
                len(unpaired), unpaired[:3]))
        if len(per_page) < TH['morph_pages_min']:
            hard.append("G14: 成品只有 %d 页平滑（SPEC §8 要求 ≥%d）。没有运镜就不算电影级交付" % (
                len(per_page), TH['morph_pages_min']))

    # G15 首帧链：图生视频必须有参考帧、提示词要点名附件，而且**首帧和 poster 得是同一个画面**。
    # 只查"旁边有没有 png"是不够的：复验实测到封面静帧是箭头、视频第 0 帧是火箭（MAD 59.4/255），
    # 链子照样报"完整"。
    vpages = [sh['page'] for sh in shots['pages'] if sh.get('asset_kind') == 'video_bg']
    firstframe_bad = []
    mad_rows = []
    tail_rows = []
    vhash = {}
    attach_wrong = []
    yard_blind = []
    attach_by_page = {}
    have_video = set()

    def _pic_mad(pa, pb, size=(160, 90)):
        """两张静帧的粗 MAD（缩到 160x90）：用来抓"20 页首帧附件其实是同一张公共色场"。"""
        try:
            from PIL import Image
            import numpy as _np
            a = _np.asarray(Image.open(pa).convert('RGB').resize(size), dtype=_np.int16)
            b = _np.asarray(Image.open(pb).convert('RGB').resize(size), dtype=_np.int16)
            return float(_np.abs(a - b).mean())
        except Exception:
            return None
    # 首帧参考图按"装配账里这页真正铺上屏的那张"认，不靠文件名猜顺序：
    # bg_fallback 对某些页只是留痕，实际铺的是 materials 照片（推镜底图），猜错就会误拦卡。
    _bg_by_page = {}
    for _r in (jr(os.path.join(wp['out'], 'build_log.json'), []) or []):
        _p = _r.get('page')
        for _k in ('poster_path', 'bg_src', 'bg'):
            _v = _r.get(_k) or ''
            if _v and os.path.isfile(_v) and _v.lower().endswith(('.jpg', '.jpeg', '.png')):
                _bg_by_page[_p] = _v
                break
    for n in vpages:
        _cands = ([find_gen(a.work, n, 'image'), find_gen(a.work, n, 'first'),
                   _bg_by_page.get(n)]
                  + [os.path.join(wp['out'], sub, 'p%03d.%s' % (n, e))
                     for sub in ('bg_fallback', 'graded')
                     for e in ('jpg', 'jpeg', 'png')]
                  + [os.path.join(wp['materials'], 'p%03d.%s' % (n, e))
                     for e in ('jpg', 'jpeg', 'png')])
        img = next((c for c in _cands if c and os.path.exists(c)), None)
        vfile = find_gen(a.work, n, 'video')
        vp = os.path.join(wp['promptpack'], 'p%03d_video.txt' % n)
        names_ref = False
        _prov = os.path.exists(os.path.join(wp['gen'], 'p%03d' % n, 'video_source.json'))
        _prov_json = jr(os.path.join(wp['gen'], 'p%03d' % n, 'video_source.json'), None) or {}
        named = []
        if os.path.exists(vp):
            vtxt = io.open(vp, encoding='utf-8').read()
            names_ref = bool(re.search(r'附件|作为首帧|首帧|参考图|尾帧', vtxt))
            named = [x for x in re.findall(r'([A-Za-z]:\\[^\s，。]+?\.(?:png|jpe?g))', vtxt)
                     if os.path.isfile(x)]
        # 比对尺子=**用户当时被要求上传的那张附件原件**，不是 gen 里的 poster：
        # 封面规则会把 gen/pNNN/image.png 换成视频自己的第 0 帧（refresh_still_from_video），
        # 拿它当尺子 MAD 恒≈0——第 3 页视频里烧进一个巨大"02"就是这样溜过去的（报告还写 mad 1.0）。
        # 取值顺序：认领凭证里记的 attachment → 卡上点名的路径（历史页没记凭证，卡文本就是证据）
        # → 这一页现在该用的专属静帧。cmd_card 出卡走 card_first_frame，两把尺子各答各的问题：
        # 闸问"这条视频是不是照当时要求的图生成的"，卡问"用户现在该上传哪张"。
        _att = _prov_json.get('attachment') or ''
        if not os.path.isfile(_att):
            _att = (named[0] if named else None) or card_first_frame(a.work, n)
        if _att and os.path.isfile(_att):
            attach_by_page[n] = os.path.abspath(_att)
            # 尺子自己就是被"视频第0帧当 poster"规则写过的那张图 → 这条 MAD 恒≈0，等于没量。
            # 认领凭证里没记 attachment 的老页（第 1/2/3 页）正是这种：必须如实报，不许蒙混。
            _gi = find_gen(a.work, n, 'image')
            if (_gi and os.path.abspath(_att).lower() == os.path.abspath(_gi).lower()
                    and not _prov_json.get('attachment')):
                yard_blind.append({
                    'page': n, 'yardstick': os.path.basename(_att),
                    'why': u'这页的比对尺子就是视频自己的第 0 帧（poster 被封面规则替换过、'
                           u'认领凭证里又没记 attachment）——MAD 恒≈0 等于没量，'
                           u'必须人工目检这一页视频画面与页面主题是否相干'})
        elif named:
            attach_wrong.append({
                'page': n, 'card_names': [os.path.basename(x) for x in named][:3],
                'should_be': u'（这页还没有专属静帧）',
                'why': u'卡把公共色场/不相干的图当首帧附件——这页必须先按主题出一张专属静帧，'
                       u'再拿它当首帧生成视频（20 页共用同一张深蓝渐变实测像素差 0.07–1.7，'
                       u'等于让用户花 20 次生成做同一个背景）'})
        if os.path.exists(vp):
            # 图生视频的附件必须是这一页真正上屏的那张静帧（gen/pNNN/image.png）。
            # 实测第 1 页的卡指到了 materials/p001.jpg（联网抓的红色电动巴士实拍照），
            # 而封面静帧是 gen/p001/image.png（卡车展台）——用户照卡上传，出来的视频
            # 却两者都不是（芯片展台），G15 MAD 32/255 拦下。指错文件=投放白跑一轮。
            _want = card_first_frame(a.work, n)
            if _want and named and not _prov:
                _low = {os.path.abspath(x).lower() for x in named}
                if os.path.abspath(_want).lower() not in _low:
                    attach_wrong.append({
                        'page': n, 'card_names': [os.path.basename(x) for x in named][:3],
                        'should_be': os.path.relpath(_want, wp['work']).replace('\\', '/'),
                        'why': u'卡里点名的首帧附件不是这一页真正该上传的那张静帧'})
        if vfile and (not img or not (names_ref or _prov)):
            firstframe_bad.append({'page': n, 'has_first_frame': bool(img),
                                   'prompt_names_attachment': names_ref,
                                   'user_supplied_provenance': _prov})
        if vfile:
            vhash.setdefault(sha1(vfile), []).append(n)
            _yard = attach_by_page.get(n) or img
            if _yard:
                # 清洗链会裁角+裁段（成片第 0 帧≠首帧图），所以一致性要拿**原件视频**的第 0 帧
                # 对用户递的那张静帧比；拿裁段后的成片比会把"即梦首帧保持"误判成跑偏（实测 MAD 43）。
                _srcv = _prov_json.get('src') or ''
                if not os.path.isfile(_srcv):
                    _srcv = vfile
                mad = frame0_vs_poster(_srcv, _yard)
                nmad = frame0_norm_mad(_srcv, _yard)
                mad_rows.append({'page': n, 'mad': mad, 'norm_mad': nmad,
                                 'yardstick': os.path.basename(_yard),
                                 'video': os.path.basename(_srcv)})
                if (mad is not None and mad > TH['frame0_mad_max']
                        and nmad is not None and nmad > TH['frame0_norm_max']):
                    firstframe_bad.append({'page': n, 'first_frame_mismatch': mad,
                                           'norm_mad': nmad,
                                           'why': '视频第0帧与卡上点名的首帧附件不是同一个画面（平均绝对差 %.1f/255、'
                                                  '亮度归一差 %.2f）——图生视频跑偏了，poster 只是张不相干的图'
                                                  % (mad, nmad)})
            # 循环接缝（P17）：repeatCount 循环背景播完回到首帧，末帧≠首帧就在接缝处跳帧。
            # 尺子=同一视频末帧 vs 首帧（不拿可能没刷新的 gen 静帧当尺子）。ping-pong 末帧=首帧→≈0。
            tmad, lmad = tail_freeze_metrics(vfile)
            tail_rows.append({'page': n, 'tail_mad': tmad, 'loop_mad': lmad})
            if tmad is None or lmad is None:
                firstframe_bad.append({'page': n, 'tail_unmeasurable': True,
                                       'why': u'循环接缝量不出结果（视频读不到）——不许把量不出当通过'})
            elif lmad > TH['loop_seam_mad_max']:
                firstframe_bad.append({'page': n, 'loop_seam_jump': lmad,
                                       'why': '循环接缝会跳帧：末帧与首帧差 %.1f > %g——'
                                              '背景循环播完回起点时画面跳一下；用 ping-pong（正放+倒放）'
                                              '接成无缝循环，或重导首尾一致的视频'
                                              % (lmad, TH['loop_seam_mad_max'])})
    # 首帧附件两两必须不同：公共色场（矢量渐变底图）20 页共用一张，用户照卡上传就等于
    # 花 20 次生成做同一个背景，而且这把尺子再也分不出"视频是不是这一页的"。
    _ap = sorted(attach_by_page.items())
    _same = []
    for _i in range(len(_ap)):
        for _j in range(_i + 1, len(_ap)):
            (p1, f1), (p2, f2) = _ap[_i], _ap[_j]
            if os.path.abspath(f1).lower() == os.path.abspath(f2).lower():
                _same.append((p1, p2, None))
                continue
            _d = _pic_mad(f1, f2)
            if _d is not None and _d < TH['attach_distinct_min']:
                _same.append((p1, p2, round(_d, 2)))
    if _same:
        firstframe_bad.append({'same_attachment_pairs': _same[:8],
                               'why': u'%d 组视频页的首帧附件是同一张图（像素差 <%g）——'
                                      u'过渡页要用公共色场当首帧，等于 20 页同一个背景；'
                                      u'先按这一页的主题出一张专属静帧再拿它当首帧'
                                      % (len(_same), TH['attach_distinct_min'])})
    dup_video = {k: v for k, v in vhash.items() if len(v) > 1}
    if dup_video:
        firstframe_bad.append({'duplicate_videos': list(dup_video.values()),
                               'why': '两个"视频页"用的是同一个 mp4（SHA1 相同），不是两页动效'})
    res['G15_首帧链'] = {'video_pages': len(vpages), 'broken_chain': firstframe_bad,
                         'frame0_vs_poster_mad': mad_rows,
                         'tail_freeze': tail_rows,
                         'attachment_wrong_file': attach_wrong,
                         'yardstick_self_referenced_pages': [x['page'] for x in yard_blind],
                         'duplicate_video_pages': list(dup_video.values())}
    # 三类问题分开报，各自带自己的原因：以前只把 firstframe_bad[0] 塞进一句话，
    # 后面的"重复视频/首帧不符"就被埋在数组里看不见
    chain_missing = [x for x in firstframe_bad if 'has_first_frame' in x]
    mismatch = [x for x in firstframe_bad if 'first_frame_mismatch' in x]
    dupes = [x for x in firstframe_bad if 'duplicate_videos' in x]
    shared_att = [x for x in firstframe_bad if 'same_attachment_pairs' in x]
    if chain_missing:
        hard.append("G15: %d 页有视频但首帧链断了（%s）——图生视频必须有参考帧且提示词点名附件" % (
            len(chain_missing), chain_missing[0]))
    if mismatch:
        hard.append("G15: %d 页的视频第0帧和首帧参考图不是同一个画面（%s）——poster 只是张不相干的图，"
                    "图生视频链是假的" % (len(mismatch), mismatch[0]))
    if dupes:
        hard.append("G15: %d 组视频页用的是同一个 mp4（SHA1 相同，%s）——那不是两页动效，是同一页复制" % (
            len(dupes), dupes[0]['duplicate_videos']))
    if shared_att:
        hard.append(u"G15: %d 组视频页的首帧附件其实是同一张图（%s）——公共色场当首帧，"
                    u"多页就会是同一个背景，而且这条比对尺子被顶平（视频跑偏也量不出来）。"
                    u"改法：先按这一页的主题出专属静帧（卡上的文生图那一段），再拿它当首帧生成视频" % (
                        len(shared_att), shared_att[0]['same_attachment_pairs']))
    if attach_wrong:
        hard.append(u"G15: %d 页的视频卡把首帧附件指错了文件（%s）——照卡上传就会生成与页面无关的视频，"
                    u"投放白跑一轮" % (len(attach_wrong), attach_wrong[:3]))
    if yard_blind:
        hard.append(u"G15: %d 页的首帧一致性**量不出结果**（页 %s）：比对尺子被封面规则换成了视频自己的"
                    u"第 0 帧，MAD 恒≈0。要么从留档原件把当时上传的那张静帧找回来记进 "
                    u"video_source.json['attachment']，要么这页重新按专属静帧生成一次——"
                    u"不许拿一个恒等的数字当验收" % (
                            len(yard_blind), [x['page'] for x in yard_blind]))


    _brief0 = outline.get('brief') or {}
    if not _brief0.get('pages') and not _brief0.get('auto_confirmed') \
            and not outline.get('verbatim'):
        hard.append("G23: 没有页数契约——用户没说要多少页时不许自作主张。先问五件事：页数或区间、"
                    "场合（路演/汇报/评审/留档）、受众、模板风格、要不要逐字留档册；拿到原话重跑 "
                    "plan --pages N --occasion … --audience … --note 原话；用户明确说「页数你定」才许 --auto-pages")
    # G16 配色锁定 / G17 图文重排 / G18 动画 preset 与视频点击链：三条都量成品，不量计划
    if os.path.exists(path):
        blog = jr(os.path.join(wp['out'], 'build_log.json'), []) or []
        meta = jr(os.path.join(wp['out'], 'build_meta.json'), {}) or {}
        pal_g = meta.get('palette') or 'tech_blue'
        g16 = g16_palette_lock(path, pal_g, getattr(a, 'allow_color', None) or [])
        res['G16_配色'] = g16
        if g16['offending_pages']:
            hard.append("G16: %d 页出现了色板 %s 之外的彩色（首页 %s）——一册一色是硬约束："
                        "改回色板色，或 --palette 换实测色板 / --allow-color 放行 VI 色" % (
                            len(g16['offending_pages']), pal_g, g16['offending_pages'][0]))
        if g16['theme_accent1'] != g16['palette_accent1']:
            hard.append("G16: 成品 theme accent1=%s 不等于色板主色 %s——apply_theme 没生效，"
                        "换色板不会带动整片" % (g16['theme_accent1'], g16['palette_accent1']))
        g17 = g17_layout_lock(path, blog)
        res['G17_图文版式'] = g17
        if g17['mismatch']:
            hard.append("G17: %d 页的图文关系与声明的版式原型不符（%s）——"
                        "原型是按成品 bbox 量出来的（与语料挖掘同一个 deck_measure），不是按计划写的" % (
                            len(g17['mismatch']), g17['mismatch'][0]))
        g18 = g18_anim_lock(path, blog)
        res['G18_动效链'] = g18
        if g18['missing']:
            hard.append("G18: %d 处动效声明没装进成品（%s）——技法卡/preset 名写在分镜里不等于"
                        "成品 timing 里有这个节点" % (len(g18['missing']), g18['missing'][0]))
        g19 = g19_bad_exemplar(path, facts, blog)
        res['G19_示范质量'] = g19
        if g19['n_hits']:
            hard.append("G19: %d 页命中语料坏示范特征（%s）——语料 2224 页里只有 835 页能当范本，"
                        "其余是推广页/空页/文字墙/小字页，这些做法不学" % (
                            g19['n_hits'], g19['hits'][0]))
        g20 = g20_material_coverage(path, blog, TH)
        res['G20_素材覆盖'] = g20
        if g20['shortfall']:
            _msg = ("G20: 全册 %d 页只有 %d 页用真素材打底（%.1f%%，下限 %s%%，还差 %d 页），"
                    "%d 页是引擎自画的矢量色场——色场是「这页没找到素材」的兜底，不是整册默认背景。"
                    "要电影级就老实跑 fetch 取高清图，或按 delivery_card 在即梦/豆包投放后放回 gen/ 重跑 build"
                    % (g20['pages'], g20['material_pages'], g20['material_pct'],
                       g20['threshold_pct'], g20['shortfall'], g20['field_pages']))
            hard.append(_msg)
            field_msgs.append(_msg)
        if g20['longest_field_run'] > TH['field_run_max']:
            _msg = ("G20: 同一套矢量色场连排 %d 页（上限 %d 页）——连着看就是模板脸，"
                    "要么补素材，要么换不同母题" % (g20['longest_field_run'], TH['field_run_max']))
            hard.append(_msg)
            field_msgs.append(_msg)
        if g20['worst_reuse_pages'] > TH['material_reuse_max']:
            hard.append("G20: 同一张素材「%s」铺了 %d 页（上限 %d 页，全册真素材只有 %d 个不同文件）"
                        "——铺得多不等于铺得对，连着看还是同一张脸，该页组各自取不同的图" % (
                            g20['top_reuse'][0]['file'], g20['worst_reuse_pages'],
                            TH['material_reuse_max'], g20['distinct_material_files']))
        g21 = g21_source_lock(outline, path, TH)
        res['G21_溯源基准锁'] = g21
        for f in g21['findings']:
            hard.append("G21: 溯源基准不可信（%s）——G1/G13 的「可举证」全建立在「源是谁」上，"
                        "源不可信那两条绿就没有意义" % f['why'])
        if g21['tables_missing']:
            hard.append("G21: 源 Word 的 %d 张表里 %d 张没成建制进成品（成品原生表格 %d 个，缺的首张表头「%s」）"
                        "——台账和测算表就是这册片子最该被看见的数字，压成一行逗号文字等于丢内容" % (
                            g21['source_tables'], len(g21['tables_missing']), g21['deck_tables'],
                            g21['tables_missing'][0]['header']))
        _, _, cards_c = validate_prompts(wp, shots, outline)
        g26c = DG.gate(wp['promptpack'], cards_c, wp['gen'], TH,
                       allow_still=bool(getattr(a, 'allow_still', False)))
        res['G26_投放回执'] = {'items': g26c['items'],
                              'missing': [{'page': m['page'], 'kind': m['kind'],
                                           'return_path': m['return_path']}
                                          for m in g26c['missing']],
                              'waived': [{'page': m['page'], 'kind': m['kind']}
                                         for m in g26c['waived']],
                              'reasons': g26c['reasons'],
                              'ask': g26c['ask'],
                              'card_sha1': (g26c.get('card_sha1') or '')[:12] or None,
                              'ack': (g26c.get('ack') or {}).get('answer'),
                              'ack_at': (g26c.get('ack') or {}).get('ack_at')}
        for r in g26c['reasons']:
            hard.append("G26: %s——用户没看到提示词就拿不到素材，"
                        "上一版片子『全程没弹提示词』就是这么来的" % r)
        if (g26c.get('ack') or {}).get('answer') == DG.VIDEO_ONLY_WAIVER:
            res['G26_投放回执']['must_declare_in_delivery_note'] = (
                "用户回执「不要视频背景」，豁免 %d 项 video 产物（页 %s）；"
                "这些页按矢量色场/静帧装配，交付说明必须写明" % (
                    len(g26c['waived']), sorted({w['page'] for w in g26c['waived']})))
            print("G26：用户回执豁免 %d 项 video 产物（页 %s）——交付说明要写明这是降级项" % (
                len(g26c['waived']), sorted({w['page'] for w in g26c['waived']})))
        # 表格上限按源表推导（plan 记 outline['table_cap']）——G21 要求全单元格上屏，
        # 固定 400 会与它冲突；只抬表格页，非表页仍按 display/data 上限。
        _th23 = dict(TH)
        if outline.get('table_cap'):
            _th23['page_chars_max_table'] = max(TH['page_chars_max_table'],
                                                int(outline['table_cap']))
        g23 = g23_page_budget(path, _th23, bool(outline.get('verbatim')), blog)
        res['G23_页面预算'] = g23
        g27 = g27_type_scale(path, TH, blog)
        res['G27_字号阶梯'] = g27
        if g27['bad_pages']:
            hard.append("G27: %d 页没有字号阶梯（首页 %s：%s）——整页要有 ≥%dpt 的视觉锚点、"
                        "正文行 ≥%dpt；小字浮在半页空底上就是用户否掉的"
                        "\"排版太丑\"" % (
                            g27['bad_pages'], g27['bad'][0]['page'],
                            '；'.join(g27['bad'][0]['why']),
                            TH['title_pt_min'], TH['body_pt_min']))
        g28 = g28_watermark(wp, TH, blog)
        res['G28_水印清除'] = g28
        if g28['bad_pages']:
            hard.append("G28: %d 个投放产物还带着平台角标（首页 %s：%s）——"
                        "重新 pickup 会自动去水印，或手动跑 scripts/dewatermark.py 后再认" % (
                            g28['bad_pages'], g28['bad'][0]['page'],
                            g28['bad'][0]['file'] + ' ' + g28['bad'][0]['why']))
        g29 = g29_opening(path, outline, TH, blog)
        res['G29_开场解剖'] = g29
        if g29['bad_pages']:
            hard.append("G29: 开场/封面不合格（首页 %s：%s）——封面要满铺视频非 animScale 推镜图、"
                        "口号主标题≠文档名、四件套齐全、过渡页有看点（SPEC §17）" % (
                            g29['bad'][0]['page'], '；'.join(g29['bad'][0]['why'])))
        if g23['over_pages']:
            hard.append("G23: %d 页超出页面预算（首页 %s，%s）——语料好示范页中位 19 字，"
                        "一页一段话永远做不出模板的样子；请用 refine 生成提案后改写 display_lines"
                        "（真要全量留档就 plan --verbatim，报告会自动写明这不是发布会册）" % (
                            g23['over_pages'], g23['over'][0]['page'], g23['over'][0]['why']))
        _brief = outline.get('brief') or {}
        if _brief.get('pages'):
            _lo, _hi = _brief['pages']
            if not (_lo <= len(deck_pages) <= _hi):
                hard.append("G23: 页数契约 %d–%d 页，成品实际 %d 页——按契约重跑 plan --pages，"
                            "或拿用户新原话改契约，不许 silently 超/欠" % (_lo, _hi, len(deck_pages)))
            if ledger_missing_pages:
                hard.append("G13: 页数契约下 %d 页没有压缩台账（页 %s）——跑 refine 留账；"
                            "被压缩的原文必须逐句进台账，不许静默丢" % (
                                len(ledger_missing_pages), ledger_missing_pages[:6]))
        g24 = g24_fact_coverage(outline, path, TH)
        res['G24_事实覆盖'] = g24
        g25 = g25_table_design(path, TH)
        res['G25_表格设计'] = g25
        if g25['bad_tables']:
            hard.append("G25: %d 张表格没按片子重排（页%s：%s）——语料里真表格页只占 0.7%%，"
                        "台账要留但必须重画：表头色带、数字右对齐、正文 ≥%dpt、表外一行结论" % (
                            g25['bad_tables'], g25['bad'][0]['page'],
                            '、'.join(g25['bad'][0]['why']), TH['table_body_pt_min']))
        import deck_parity as _dp
        try:
            g22 = _dp.score(path, TH)
        except SystemExit as e:
            g22 = {'score': None, 'error': str(e)[:120]}
        res['G22_同级分'] = g22
        if g22.get('score') is None:
            hard.append("G22: 同级分跑不出来（%s）——先跑 deck_parity.py --calibrate 建立语料基线" %
                        g22.get('error'))
        elif not outline.get('verbatim'):
            if g22.get('pages', 0) < TH['parity_min_pages']:
                g22['not_applicable'] = ('只有 %d 页：少于 %d 页时「每页字数中位数/大数字率」这类'
                                         '分布指标没有意义，同级分不计入硬伤（报告仍会给出分数）'
                                         % (g22.get('pages', 0), TH['parity_min_pages']))
            weak = [d for d in g22['dims'] if d['attain'] < TH['parity_dim_min']]
            if not g22.get('not_applicable') and (
                    g22['score'] < TH['parity_score_min'] or weak):
                hard.append("G22: 同级分 %.1f（及格线 %d，好示范册全量同尺中位 82.8）%s——逐维差距见报告，"
                            "这不是审美意见，是同一把尺子量出来的差距" % (
                                g22['score'], TH['parity_score_min'],
                                ('；短板维度：%s' % '、'.join(w['dim'] for w in weak)) if weak else ''))
        elif g22.get('score') is not None:
            res['G22_同级分']['verbatim_exempt'] = True
        if g24['missing'] > TH['fact_missing_max']:
            hard.append("G24: 源文档 %d 个事实单元（数字/金额/比例/专名）没出现在成品里"
                        "（例如页%s 的「%s」）——提炼可以改措辞，不能把事实提炼掉" % (
                            g24['missing'], g24['sample'][0]['page'], g24['sample'][0]['fact']))
        if g24.get('truncated'):
            hard.append("G24: %d 页屏幕上的数字是原文更长数字的前缀（例如页%s 显示「%s」，原文「%s」）"
                        "——标题被裁在数字中间，屏幕上就是一个对外错的数；全册文本核看不见它，"
                        "因为完整那一串在别的页出现过" % (
                            g24['truncated'], g24['truncated_sample'][0]['page'],
                            g24['truncated_sample'][0]['shown'],
                            g24['truncated_sample'][0]['source']))
        if g21['derived_from']:
            hard.append("G21: 溯源基准疑由旧 PPT 派生（源正文有 %.1f%% 整段能在「%s」里找到，"
                        "而那册文字量是它的 %s 倍）"
                        "——把上一版片子抽字当 Word 原稿，G1/G13 就成了自我循环证明；"
                        "要用用户的原始 Word 重跑 plan" % (
                            g21['derived_from'][0]['contain_pct'],
                            os.path.basename(g21['derived_from'][0]['file']),
                            g21['derived_from'][0]['ratio']))

    # G11 回收：有提示词却没产物
    unrecovered = []
    for s in shots['pages']:
        if s.get('asset_kind') in ('field', 'none', 'table'):
            continue   # 矢量色场/表格页本来就不需要外部产物，不是"缺产物"（scaffold 也会清掉它们的提示词）
        n = s['page']
        has_prompt = any(os.path.exists(os.path.join(wp['promptpack'], 'p%03d_%s.txt' % (n, k)))
                         for k in ('image', 'first', 'video', 'gif'))
        got = (find_gen(a.work, n, 'image') or find_gen(a.work, n, 'video')
               or find_gen(a.work, n, 'first') or find_gen(a.work, n, 'gif'))
        if has_prompt and not got and not os.path.exists(os.path.join(wp['materials'], 'p%03d.jpg' % n)):
            unrecovered.append(n)
    # a page needs an mp4 only when the shot plan says video_bg; role alone must not demand video
    video_pages = [s['page'] for s in shots['pages'] if s.get('asset_kind') == 'video_bg']
    # 用户已豁免的页（video_waiver.json，带原话）是"决定不要视频"，不是"缺产物"：
    # 继续把它们列进 missing 会把已收口的决定又催一遍（实测 22 页豁免后仍被点名）
    _wv_rows = jr(os.path.join(wp['work'], 'video_waiver.json'), []) or []
    if isinstance(_wv_rows, dict):
        _wv_rows = [_wv_rows]
    waived_video = {r.get('page') for r in _wv_rows if (r.get('kind') or 'video') == 'video'}
    missing_video = [n for n in video_pages
                     if not (find_gen(a.work, n, 'video')) and n not in waived_video]
    # 空文件/占位文件不算产物（复验实测 0 字节 mp4 全绿：probe_duration 一失败就当没这页）
    tiny_video = []
    for n in video_pages:
        vf = find_gen(a.work, n, 'video')
        if vf and os.path.getsize(vf) < TH['mp4_bytes_min']:
            tiny_video.append({'page': n, 'bytes': os.path.getsize(vf)})

    if tiny_video:
        missing_video += [t['page'] for t in tiny_video]
    res['G11_回收'] = {'pages_missing_asset': unrecovered,
                       'video_pages_required': len(video_pages),
                       'video_pages_waived': sorted(waived_video & set(video_pages)),
                       'video_pages_missing': missing_video,
                       'tiny_mp4_rejected': tiny_video}
    # 动图页：GIF 必须真的多帧。单帧 GIF 在 PowerPoint 里看起来完全正常，按下放映才发现不动
    gif_pages = [s['page'] for s in shots['pages'] if s.get('asset_kind') == 'gif']
    gif_bad = []
    gif_overlay_rows = []
    field_pages_g11 = set(r.get('page') for r in (jr(os.path.join(wp['out'], 'build_log.json'), []) or [])
                          if 'bg_fallback' in (r.get('bg') or '').replace('\\', '/'))

    for n in gif_pages:
        gf = find_gen(a.work, n, 'gif')
        if not gf:
            gif_bad.append({'page': n, 'why': '没有 gif 产物'})
            continue
        gi = gif_info(gf)
        if not gi:
            gif_bad.append({'page': n, 'why': 'gif 读不出来'})
        else:
            w, h, nf = gi
            if nf < TH['gif_frames_min']:
                gif_bad.append({'page': n, 'frames': nf,
                                'why': '只有 %d 帧（语料 162 个 GIF 最少 24 帧），放出来就是静图' % nf})
            elif min(w, h) < TH['gif_short_edge']:
                gif_bad.append({'page': n, 'w': w, 'h': h,
                                'why': '短边 %d <%d，放大到元素位会糊' % (min(w, h), TH['gif_short_edge'])})
            else:
                # 露框判定：底色不透明的动图叠在矢量色场上必然露出一块方框（实测渲染图确认），
                # 叠在暗色实景图上则没问题——语料的 GIF 都是后一种用法。色差本身只作为证据列出。
                box = next((s.get('gif_box') or [0.60, 0.20, 0.34, 0.44]
                            for s in shots['pages'] if s['page'] == n), [0.60, 0.20, 0.34, 0.44])
                png = os.path.join(wp['report'], 'render', 'p%03d.png' % n)
                trans = gif_is_transparent(gf)
                row = {'page': n, 'transparent': trans, 'on_vector_field': n in field_pages_g11}
                if os.path.exists(png):
                    diff, why = gif_overlay_defect(gf, png, box)
                    row['edge_vs_page'] = why
                gif_overlay_rows.append(row)
                if not trans and n in field_pages_g11:
                    gif_bad.append({'page': n,
                                    'why': '动图底色不透明，而这页背景是矢量色场——放映时必然露出一块方框'
                                           '（%s）。出图时选透明底，或用 scripts/make_gif.py 生成带透明索引的 GIF'
                                           % row.get('edge_vs_page', '')})

    res['G11_回收']['gif_pages_required'] = len(gif_pages)
    res['G11_回收']['gif_pages_bad'] = gif_bad
    res['G11_回收']['gif_overlay_check'] = gif_overlay_rows
    if gif_bad:
        hard.append("G11: %d 页动图不合格（%s）——GIF 必须≥%d 帧、短边≥%d，且不透明底不许叠在矢量色场上" % (
            len(gif_bad), gif_bad[0], TH['gif_frames_min'], TH['gif_short_edge']))

    if unrecovered:
        hard.append("G11: %d 页写了提示词却没有任何产物（页 %s）——这些页等于没做完" % (
            len(unrecovered), unrecovered[:8]))
    if missing_video:
        _msg = ("G11: %d 页要求整页视频背景但 gen/ 里没有 mp4（页 %s）。静帧不算视频页，"
                "不许用爬到的静帧冒充已回收" % (len(missing_video), missing_video[:8]))
        hard.append(_msg)
        video_absent_msgs.append(_msg)
    if not any(find_gen(a.work, s['page'], 'video') for s in shots['pages']):
        _msg = ("G11: 全册 0 个真视频页（%d 页视频背景已由用户原话豁免改静态+推镜，仍待回执 %s）。"
                "SPEC §2 要求至少 1 页整页视频背景，纯静帧 deck 不算电影级交付"
                "（若确实不要视频，请改 SPEC 并说明理由）" % (
                    len(waived_video & set(video_pages)), missing_video[:8]))
        hard.append(_msg)
        video_absent_msgs.append(_msg)


    # G4 对比度（渲染像素实测）
    rd = os.path.join(wp['report'], 'render')
    if a.render and os.path.exists(path):
        if os.path.isdir(rd):
            shutil.rmtree(rd)
        try:
            render_pptx(path, rd)
            png_files = sorted(f for f in os.listdir(rd) if f.endswith('.png'))
            jw(os.path.join(wp['report'], 'render_stamp.json'),
               {'deck_sha1': sha1(path), 'pngs': len(png_files), 'at': now(),
                'png_sha1': {f: sha1(os.path.join(rd, f)) for f in png_files}})
        except Exception as e:
            res['G12_真渲染'] = {'error': str(e)[:160]}
            hard.append("G12: WPS 渲染失败 %s" % str(e)[:80])
    pngs = sorted(f for f in os.listdir(rd) if f.endswith('.png')) if os.path.isdir(rd) else []
    stamp = jr(os.path.join(wp['report'], 'render_stamp.json'))

    res['G12_真渲染'] = {
        'deck_sha1': sha1(path) if os.path.exists(path) else None,
        'stamp': stamp,
        'rendered_pngs': len([f for f in os.listdir(rd) if f.endswith('.png')]) if os.path.isdir(rd) else 0,
        'deck_pages': len(prs.slides) if prs else 0}
    if os.path.exists(path):
        if not stamp:
            hard.append("G12: 没有渲染凭据（render_stamp.json）——check 不加 --render 就等于没做视觉验收，"
                        "上一版这样也能报全绿")
        elif stamp.get('deck_sha1') != sha1(path):
            hard.append("G12: 渲染图不是这一版成品的（渲染时 SHA1 %s，现在 %s）——改完 deck 必须重渲染，"
                        "否则 G4 量的是旧图" % (str(stamp.get('deck_sha1'))[:10], str(sha1(path))[:10]))
        elif stamp.get('pngs') != len(prs.slides if prs else []):
            hard.append("G12: 渲染 %d 页但成品 %d 页，逐页没对上" % (
                stamp.get('pngs', 0), len(prs.slides if prs else [])))
        elif not (os.path.isdir(rd) and [f for f in os.listdir(rd) if f.endswith('.png')]):
            hard.append("G12: 有渲染凭据但 report/render 里没有 PNG——视觉验收的证据被删了")
        elif not stamp.get('png_sha1'):
            hard.append("G12: 渲染凭据里没有逐张 PNG 指纹（旧格式）——截图本身可以被换成另一张而看不出来，"
                        "请重跑 check --render 生成逐张凭据")
        else:
            # 凭据绑定到每一张截图：复验实测把 p003.png 换成另一页、再换成"黑底加一块假白框"，
            # 只记张数的旧凭据照样报全绿（audit4 [中]6）。逐张 SHA1 之后，动过一张就判不合格。
            want = stamp.get('png_sha1') or {}
            cur = {f: sha1(os.path.join(rd, f)) for f in pngs}
            changed = sorted(f for f in cur if f in want and cur[f] != want[f])
            gone = sorted(f for f in want if f not in cur)
            extra = sorted(f for f in cur if f not in want)
            res['G12_真渲染']['png_fingerprints'] = {
                'stamped': len(want), 'changed': changed, 'missing': gone, 'unexpected': extra}
            if changed or gone or extra:
                hard.append("G12: 渲染截图与凭据对不上（被改 %s、缺失 %s、多出 %s）——视觉验收的图必须逐张"
                            "和渲染时那份一致，改一张就得整册重渲染" % (
                                changed[:4], gone[:4], extra[:4]))

    fails = []
    measured = 0
    hidden = []
    skipped_no_stroke = 0
    if pngs and prs:
        from PIL import Image
        for idx, slide in enumerate(prs.slides, 1):
            fp = os.path.join(rd, 'p%03d.png' % idx)
            if not os.path.exists(fp):
                continue
            im = Image.open(fp).convert('RGB')
            # 背景 = 框内"不是任何笔画颜色"的像素。原来只剔这一框自己的字色，
            # 于是同一页上的青色大数字、别的白字都被当成背景（实测页 22 因此永远量到 1.7:1）。
            page_inks = []
            for sp0 in slide.shapes:
                if not sp0.has_text_frame:
                    continue
                for p0 in sp0.text_frame.paragraphs:
                    for r0 in p0.runs:
                        try:
                            c0 = r0.font.color.rgb
                        except Exception:
                            c0 = None
                        if c0 is not None:
                            page_inks.append(rel_lum(tuple(int(str(c0)[i:i + 2], 16)
                                                        for i in (0, 2, 4))))
            page_inks = sorted(set(round(v, 2) for v in page_inks))
            for sp in slide.shapes:
                if not sp.has_text_frame or not sp.text_frame.text.strip():
                    continue
                col = None
                for p in sp.text_frame.paragraphs:
                    for r in p.runs:
                        try:
                            col = r.font.color.rgb
                        except Exception:
                            col = None
                        break
                    break
                if col is None:
                    continue
                rgb = tuple(int(str(col)[i:i + 2], 16) for i in (0, 2, 4))
                bx0 = max(0, int(sp.left / EMU_W * im.width))
                by0 = max(0, int(sp.top / EMU_H * im.height))
                bx1 = min(im.width, int((sp.left + sp.width) / EMU_W * im.width))
                by1 = min(im.height, int((sp.top + sp.height) / EMU_H * im.height))
                if bx1 - bx0 < 8 or by1 - by0 < 8:
                    continue
                crop = im.crop((bx0, by0, bx1, by1))
                if crop.width < 8 or crop.height < 8:
                    continue
                import numpy as np
                # A whole-box median flatters a box that straddles a bright cabinet and a dark wall
                # (audit found page 6 passing while looking grey). Measure where the ink actually is:
                # find stroke pixels, then take the median of their immediate surroundings.
                full = np.asarray(crop).astype(np.int16)
                tr, tg, tb = rgb
                near = ((np.abs(full[..., 0] - tr) < 34) & (np.abs(full[..., 1] - tg) < 34)
                        & (np.abs(full[..., 2] - tb) < 34))
                if near.sum() < 40:
                    skipped_no_stroke += 1
                    hidden.append({'page': idx, 'shape': sp.name, 'pt': 0,
                                   'pixels': int(near.sum()),
                                   'text': sp.text_frame.text[:24],
                                   'why': '框内找不到该字色的笔画，字可能被色块整个盖住'})
                    continue
                measured += 1
                ys, xs = np.nonzero(near)
                pad = 3
                y0 = max(0, int(ys.min()) - pad); y1 = min(full.shape[0], int(ys.max()) + pad + 1)
                x0 = max(0, int(xs.min()) - pad); x1 = min(full.shape[1], int(xs.max()) + pad + 1)
                win = full[y0:y1, x0:x1]
                wnear = near[y0:y1, x0:x1]
                rest = win[~wnear].reshape(-1, 3)
                if rest.shape[0] < 40:
                    rest = win.reshape(-1, 3)
                arr = rest
                lums = [rel_lum((int(r), int(g), int(b_))) for r, g, b_ in arr[::5]]
                textL = rel_lum(rgb)
                # background = the pixels that are NOT the glyph strokes. Strokes carry the text
                # colour, so drop everything within 0.08 of it before taking the median; using the
                # darkest pixels instead would flatter every bright photo and let bad pages pass.
                # 0.08 太窄：大号浅字的抗锯齿边（灰阶）会被当成"背景"，
                # 于是压在深底上的白字被量成 1.64:1（实测页 22 三个框分别 0.010 / 0.335 / 0.592，
                # 差别只在框边挨着多大的浅色笔画）。按亮度距离剔字色，阈值给到 0.30。
                def _is_ink(l):
                    return any(abs(l - ink) <= TH['contrast_ink_gap'] for ink in page_inks)
                rest = [l for l in lums if not _is_ink(l)] or lums
                rest.sort()
                bg = rest[len(rest) // 2]
                ratio = (max(textL, bg) + 0.05) / (min(textL, bg) + 0.05)
                pt = 0
                for p in sp.text_frame.paragraphs:
                    for r in p.runs:
                        if r.font.size:
                            pt = r.font.size.pt
                            break
                    break
                need = 3.0 if pt >= 24 else 4.5
                if ratio < need:
                    fails.append({'page': idx, 'shape': sp.name, 'pt': pt,
                                  'ratio': round(ratio, 2), 'need': need,
                                  'text': sp.text_frame.text[:24]})
        n_text_shapes = sum(1 for sl in prs.slides for sp2 in sl.shapes
                            if sp2.has_text_frame and sp2.text_frame.text.strip())
        res['G4_对比度'] = {'png_pages': len(pngs), 'text_shapes': n_text_shapes,
                            'boxes_measured': measured, 'no_stroke_found': skipped_no_stroke,
                            'below_line': len(fails), 'sample': fails[:8],
                            'ink_not_found': hidden[:8]}
        # 一个只报"0 违规"却不报"量了多少"的闸等于没跑；这里强制它自证覆盖
        if not pngs:
            hard.append("G4: 没有渲染图可量（--render 失败或 out 不存在），对比度闸空转")
        elif n_text_shapes and measured < int(n_text_shapes * 0.5):
            hard.append("G4: 成品有 %d 个文本框却只量到 %d 个，对比度闸覆盖不足，不能算通过" % (
                n_text_shapes, measured))
        if fails:
            hard.append("G4: %d 个文字框不达 WCAG 线（最低 %.2f:1，页%s）" % (
                len(fails), min(f['ratio'] for f in fails), fails[0]['page']))
        if hidden:
            hard.append(u"G4: %d 个文字框在渲染图上量不到该字色的笔画（页%s 的「%s」）——"
                        u"这不是对比度不够，是字被盖住或只剩一个字符，先查上屏内容再查蒙版" % (
                len(hidden), hidden[0]['page'], hidden[0].get('text') or ''))
    else:
        res['G4_对比度'] = {'status': '未渲染，无法量像素（加 --render）'}
        hard.append("G4: 没有渲染像素可量，对比度闸没跑——不带 --render 的 check 不许报合格")

    # G9 覆盖：语料研读台账的不变量必须真核对，之前它只把 coverage.json 原样贴进报告，
    # 结构上不可能失败（干净复验点名的三道"绿灯闸"之一）
    try:
        cov = kb('coverage.json')
        bad_cov = coverage_invariants(cov)
        res['G9_覆盖'] = cov
        res['G9_覆盖']['_自证'] = {'invariants_checked': N_COVERAGE_INVARIANTS,
                                   'violations': bad_cov}
        if bad_cov:
            hard.append("G9: 研读覆盖不变量被破坏：%s" % '；'.join(bad_cov))
        try:
            bad_lib = tech_library_invariants(kb('deck_tech.json'))
        except Exception as e:
            bad_lib = ["技法库读不出来（%s）" % str(e)[:60]]
        res['G9_覆盖']['_技法库自证'] = {'violations': bad_lib}
        if bad_lib:
            hard.append("G9: 技法库不完整：%s" % '；'.join(bad_lib))
    except Exception as e:
        res['G9_覆盖'] = {'error': str(e)[:120]}
        hard.append("G9: 覆盖台账读不出来（%s）——没有覆盖证据就不许声称全量研习过" % str(e)[:60])

    # G10 去 AI 味：ai_tells.py 自己"永远退出 0"（docstring 明写），所以分数必须由这里读出来判，
    # 否则这一闸也结构上不可能失败
    hd = os.path.join(os.path.expandvars(r'%USERPROFILE%'), '.qoder-cn', 'skills', 'human-doc', 'scripts', 'ai_tells.py')
    if os.path.exists(hd) and outline:
        txt = '\n'.join(pg['headline'] + '\n' + '\n'.join(pg.get('body') or []) for pg in outline['pages'])
        tmp = os.path.join(wp['report'], 'deck_text.md')
        io.open(tmp, 'w', encoding='utf-8').write(txt)
        r = subprocess.run([sys.executable, hd, tmp], capture_output=True, text=True,
                           encoding='utf8', errors='replace',
                           env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        sout = (r.stdout or '') + (r.stderr or '')
        score, hits = parse_ai_tells(sout)
        res['G10_去AI味'] = {'score': score,
                             'counters': hits, 'rc': r.returncode,
                             'out': sout[-400:] if score is None else '(已解析)'}
        if score is None:
            hard.append("G10: ai_tells 没给出分数（输出解析失败），去 AI 味这一闸等于没跑")
        elif score < TH['ai_score_min']:
            hard.append("G10: 去 AI 味得分 %.1f < %d（命中 %s）——对外片子不许带明显 AI 痕迹" % (

                score, {k: v for k, v in hits.items() if v}))
    else:
        res['G10_去AI味'] = {'status': 'human-doc 的 ai_tells.py 不在这台机器上，未测'}
        hard.append("G10: 这台机器上没有 human-doc/ai_tells.py，去 AI 味无法验收——"
                    "不许因为工具缺失就报通过")

    # 用户不投放视频时的合法出口：必须显式声明，且把降级写进报告，不许静默当电影级交付。
    # （SPEC §2 要求至少 1 页整页视频背景；这条不会因为"本机没有视频模型"就假装没这条。）
    if getattr(a, 'allow_field', False) and field_msgs:
        moved_f = list(field_msgs)
        hard = [h for h in hard if h not in moved_f]
        res['降级交付_素材覆盖不足'] = {
            'declared_by': '--allow-field',
            'suppressed_findings': moved_f,
            'meaning': u'这一版大部分页是矢量色场，不是真素材打底；可以编辑、可以放映，'
                       u'但不算电影级交付。补素材的正规做法：fetch 取 Commons 高清图，'
                       u'或按 promptpack/delivery_card.md 在即梦/豆包投放后放回 gen/，再重跑 build + check'}
        print(u'注意：--allow-field 已放行 %d 条素材覆盖硬伤，这是**降级交付**，不是电影级交付' % len(moved_f))

    if getattr(a, 'allow_still', False):
        moved = list(video_absent_msgs)
        if moved:
            hard = [h for h in hard if h not in moved]

            res['降级交付_无视频背景'] = {
                'declared_by': '--allow-still',
                'suppressed_findings': moved,
                'meaning': '这一版是静帧背景的可编辑 PPT，不含 SPEC §2 要求的整页视频背景；'
                           '要电影级版本必须按 delivery_card.md 在即梦/豆包投放后重跑 build+check'}
            print("注意：--allow-still 已放行 %d 条视频相关硬伤，这是**降级交付**，不是电影级交付" % len(moved))

    _lowres = []
    if os.path.isdir(wp['gen']):
        for _pd in sorted(os.listdir(wp['gen'])):
            _lr = jr(os.path.join(wp['gen'], _pd, 'lowres_force.json'), None)
            if _lr:
                _lowres.append(_lr)
    if _lowres:
        res['声明_低清强制收下'] = {
            'items': _lowres,
            'meaning': u'这些产物短边低于 %d 下限，由用户 --force 知情收下；铺满 1080p 会糊，'
                       u'不算高清交付' % TH['embed_short_edge']}

    rep = os.path.join(wp['report'], '验收报告.md')
    o = io.open(rep, 'w', encoding='utf-8')
    o.write("# caofeng-PPT 验收报告\n\n生成时间：%s\n\n" % now())
    for k, v in res.items():
        o.write("## %s\n\n```json\n%s\n```\n\n" % (k, json.dumps(v, ensure_ascii=False, indent=1)))
    degraded = res.get('降级交付_无视频背景')
    degraded_f = res.get('降级交付_素材覆盖不足')
    o.write("## 结论\n\n")
    if hard:
        o.write("**不合格，%d 条硬伤：**\n\n" % len(hard))
        for h in hard:
            o.write("- " + h + "\n")
    elif degraded or degraded_f:
        # 放行视频硬伤不等于电影级交付：结论行必须自己说清楚，不许只写在 JSON 小节里
        o.write("**闸全部通过，但这一版是降级交付：%s，不算 SPEC §2 定义的电影级 PPT。**\n\n" % (
            u'、'.join(filter(None, [u'没有整页视频背景' if degraded else u'',
                                     u'大部分页是矢量色场而非真素材' if degraded_f else u''])) or u'有降级项'))
        sup = ((degraded.get('suppressed_findings') or []) if degraded else []) + \
              ((degraded_f.get('suppressed_findings') or []) if degraded_f else [])
        o.write("被 --allow-still / --allow-field 放行的硬伤 %d 条：\n\n" % len(sup))
        for h in sup:
            o.write("- " + h + "\n")
        for dd in (degraded, degraded_f):
            if dd:
                o.write("\n%s\n" % dd.get('meaning', ''))
    else:
        o.write("**全部闸通过。**\n" if not _lowres else
                "**闸全部通过，但含低清强制收下产物（见下声明），不算高清交付。**\n")
    if _lowres:
        o.write("\n**低清强制收下声明（用户 --force 知情）：**\n\n")
        for _lr in _lowres:
            _dm = _lr.get('dims') or [0, 0]
            o.write("- 页%s %s：%s 只有 %dx%d（短边下限 %d）\n" % (
                _lr.get('page'), _lr.get('kind'), _lr.get('file'),
                _dm[0], _dm[1], _lr.get('min_edge')))
    o.close()
    print("check 完成 -> %s" % rep)
    for k, v in res.items():
        print("  %-12s %s" % (k, json.dumps(v, ensure_ascii=False)[:150]))
    if _lowres:
        print("\n低清强制收下声明（用户 --force 知情，不算高清交付）：")
        for _lr in _lowres:
            _dm = _lr.get('dims') or [0, 0]
            print("  - 页%s %s %s %dx%d" % (
                _lr.get('page'), _lr.get('kind'), _lr.get('file'), _dm[0], _dm[1]))
    if hard:
        print("\n不合格 %d 条：" % len(hard))
        for h in hard:
            print("  - " + h)
        sys.exit(1)
    if degraded:
        print("\n闸通过，但这是**降级交付**（无整页视频背景，不算电影级）："
              "被放行 %d 条硬伤，详见报告结论" % len(degraded.get('suppressed_findings') or []))
        return
    print("\n全部闸通过")



def media_dims(path):
    """(w, h) of a video as it really is on disk, parsed from ffmpeg's own stream line."""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        r = subprocess.run([ff, '-hide_banner', '-i', path], capture_output=True, text=True,
                           encoding='utf8', errors='replace')
        m = re.search(r', Video:.*?\n?\s*\S*\s*(\d{3,5})x(\d{3,5})', r.stderr or '')
        if not m:
            m = re.search(r'(\d{3,5})x(\d{3,5})', r.stderr or '')
        if not m:
            return None
        return int(m.group(1)), int(m.group(2))
    except Exception:
        return None


def probe_duration(path):
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        r = subprocess.run([ff, '-hide_banner', '-i', path], capture_output=True, text=True,
                           encoding='utf8', errors='replace')
        m = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.\d+)', r.stderr or '')
        if not m:
            return None
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        return None


def cmd_shots(a):
    wp = work_paths(a.work)
    path = os.path.join(wp['out'], 'deck.pptx')
    if not os.path.exists(path):
        die("先 build")
    rd = os.path.join(wp['report'], 'render')
    n = render_pptx(path, rd)
    print("渲染 %d 页 -> %s" % (n, rd))
    sys.path.insert(0, SKILL_DIR)
    import montage
    sheets = os.path.join(wp['report'], 'sheets')
    files = sorted(os.path.join(rd, f) for f in os.listdir(rd) if f.endswith('.png'))
    per = 12
    for i in range(0, len(files), per):
        montage.build(files[i:i + per], os.path.join(sheets, 's%02d.jpg' % (i // per)), cols=3)
    print("拼图 -> %s（逐页看图用，别只看统计）" % sheets)


def main():
    ap = argparse.ArgumentParser(prog='caofeng', description='电影级 PPT 流水线（Word -> 可编辑动态 deck）')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('plan'); p.add_argument('docx'); p.add_argument('--work', required=True)
    p.add_argument('--per-page', type=int, default=2)
    p.add_argument('--verbatim', action='store_true',
                   help='全量留档册：正文逐字上屏（G22/G23 豁免，报告必须自报"这不是发布会册"）')
    p.add_argument('--max-chars', type=int, default=TH['page_chars_max'],
                   help='单页正文字数上限（默认 %d；语料判文字墙是 %d，长句多的文档要往下调）'
                        % (TH['page_chars_max'], CT.BAD_RULES['dense_chars_min']))
    p.add_argument('--pages', default='',
                   help='页数契约：整数或区间（30 / 25-35）。按它重新梳理 Word：结构页不动、章内内容页合并；'
                        '不给就必须先问用户（G23 拦无契约的册子）')
    p.add_argument('--auto-pages', action='store_true',
                   help='用户明确说"页数你定/不限"才许加；否则缺 --pages 会被 G23 拦')
    p.add_argument('--occasion', default='', help='场合：路演/汇报/评审/留档…（写进 brief，影响版式与语气）')
    p.add_argument('--audience', default='', help='受众：投资人/领导/客户/内部…（写进 brief）')
    p.add_argument('--note', default='', help='用户关于页数/场合的原话（留痕）')
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser('fetch-video'); p.add_argument('--work', required=True)
    p.add_argument('--only-page', action='append', default=[])
    p.add_argument('--proxy', default=None)
    p.set_defaults(fn=cmd_fetch_video)
    p = sub.add_parser('materials-review'); p.add_argument('--work', required=True)
    p.add_argument('--set', default='', help='逐页目检结论：3=keep,4=reject:理由（reject 摘出 manifest）')
    p.set_defaults(fn=cmd_materials_review)
    p = sub.add_parser('fetch'); p.add_argument('--work', required=True)
    p.add_argument('--prefer-stock', action='store_true',
                   help='背景图也优先联网 stock（默认只给 source=stock 举证页用；SPEC §24）')
    p.add_argument('--style', default='tech_blue', help='美学闸按哪套色板判')
    p.add_argument('--min-edge', type=int, default=1920); p.add_argument('--proxy', default=None)
    p.add_argument('--term', action='append', default=[],
                   help='页码=搜索词，可多次；agent 判定用词时优先用它，例：--term 3=华东医美机构')
    p.add_argument('--keep-ratio', action='store_true',
                   help='保留原比例不裁 16:9（当素材是给即梦当首帧参考图时用）')
    p.add_argument('--only-page', action='append', default=[],
                   help='强制重取这些页（覆盖 manifest 里已有的记录）')
    p.add_argument('--min-rel', type=int, default=TH['relevance_min'],
                   help='标题与页面实体的最低相关度百分比，低于它宁可走色场也不放离题图'
                        '（与 G3 复核、装配拒用是同一个定义点 TH["relevance_min"]，不是第二把尺子）')
    p.add_argument('--allow-sensitive', default=None,
                   help='按 Word 主题放行敏感类别（逗号分隔：protest/violence/law-enforcement/'
                        'military/medical-gore/disaster/displacement/sexual）；'
                        '例：医疗运营手册 --allow-sensitive medical-gore')
    p.add_argument('--source', default='commons',
                   choices=('commons', 'unsplash', 'openverse', 'auto'),
                   help='取图通道：commons 免登录可举证；unsplash 需要 %s 密钥' % UNSPLASH_KEY_ENV)
    p.add_argument('--adopt', default=None,
                   help='登记用户手工下载回来的图（配合 --page/--url/--license/--artist）')
    p.add_argument('--page', type=int, default=None, help='adopt 时指定页码')
    p.add_argument('--url', default=None, help='adopt 的来源页 URL（举证用，必填）')
    p.add_argument('--license', default=None, help='adopt 的许可名，默认 Unsplash License')
    p.add_argument('--artist', default=None, help='adopt 的作者')
    p.set_defaults(fn=cmd_fetch)
    import styles as _st
    p = sub.add_parser('scaffold'); p.add_argument('--work', required=True)
    p.add_argument('--style', default='tech_blue', choices=list(_st.STYLES))
    p.add_argument('--secs', type=int, default=8)
    p.add_argument('--subject', action='append', default=[], help='页码=主体词，可多次')
    p.add_argument('--subjects', default=None,
                   help='JSON 文件 {"3":"上海浦东天际线", ...}；中文经 shell 易被编码吃掉，优先用这个')
    p.set_defaults(fn=_st.register())
    p = sub.add_parser('refine'); p.add_argument('--work', required=True)
    p.add_argument('--force', action='store_true', help='连 agent 已改过的页也重新生成提案')
    p.set_defaults(fn=cmd_refine)
    p = sub.add_parser('pack'); p.add_argument('--work', required=True); p.set_defaults(fn=cmd_pack)
    p = sub.add_parser('ack', help='登记投放回执（必须在把投放卡全文发给用户、并收到回答之后）')
    p.add_argument('--work', required=True)
    p.add_argument('--ready', action='store_true', help='用户回「是，已下载就绪」，产物必须齐')
    p.add_argument('--no-video', action='store_true', help='用户明确不要视频背景，只豁免 video 类')
    p.add_argument('--declined', action='store_true',
                   help='用户明确回「这些产物我不做了，按现状出片」（必须同时给 --note 用户原话）')
    p.add_argument('--note', default=None, help='用户原话摘要（只作记录，不参与判定）')
    p.set_defaults(fn=cmd_ack)
    p = sub.add_parser('waive',
                       help='单页视频豁免（用户原话撑腰）：原片留证、队列跳过、build 改静态+推镜')
    p.add_argument('--work', required=True)
    p.add_argument('--page', type=int, required=True)
    p.add_argument('--kind', default='video')
    p.add_argument('--note', required=True, help='用户原话（必填，agent 不能替用户放弃）')
    p.set_defaults(fn=cmd_waive)
    p = sub.add_parser('pickup',
                       help='一页一轮的回收：引擎自己去下载文件夹认领刚生成的图/视频并当场验规格')
    p.add_argument('--work', required=True)
    p.add_argument('--page', type=int, required=True)
    p.add_argument('--kind', default='', help='image,video,gif,first,last；不写就按这页卡上有的类型')
    p.add_argument('--src', default='', help='用户直接给文件全路径；不给就扫下载文件夹')
    p.add_argument('--dir', action='append', default=[], help='额外的下载目录（可重复）')
    p.add_argument('--within', type=int, default=45, help='只认最近多少分钟内下载的文件')
    p.add_argument('--min-edge', type=int, default=1080)
    p.add_argument('--force', action='store_true', help='分辨率不够也收下（报告如实显示降级）')
    p.set_defaults(fn=cmd_pickup)
    p = sub.add_parser('card',
                       help='一页一轮的投放：把下一页（或 --page 指定页）的提示词整段打出来，供原样发给用户')
    p.add_argument('--work', required=True)
    p.add_argument('--page', type=int, help='指定页码；不给就按队列取最早待回收的一页（视频优先）')
    p.set_defaults(fn=cmd_card)
    p = sub.add_parser('build'); p.add_argument('--work', required=True)
    p.add_argument('--allow-still', action='store_true',
                   help='同 check：用户明确不要视频背景时豁免 video 类产物（非 video 不豁免）')
    p.add_argument('--palette', default=None, choices=sorted(CT.PALETTES),
                   help='按实测色板装配（默认跟随 --style）；写进 theme1.xml 并被 G16 逐页核对')
    p.add_argument('--allow-color', action='append', default=[],
                   help='额外放行的彩色（可重复），用于客户 VI 色；G16 会列出它放行过的值')
    p.set_defaults(fn=cmd_build)
    p = sub.add_parser('check'); p.add_argument('--work', required=True)
    p.add_argument('--allow-color', action='append', default=[],
                   help='G16 额外放行的彩色（可重复），放行值会写进验收报告')
    p.add_argument('--render', action='store_true')
    p.add_argument('--allow-sensitive', default=None, help='同 fetch：放行类别，G3 复检按同一口径')
    p.add_argument('--allow-field', action='store_true',
                   help='用户明确接受"大部分页用矢量色场"时，把素材覆盖硬伤降级为报告里的显式声明'
                        '（默认不开；开了就不是电影级交付）')
    p.add_argument('--allow-still', action='store_true',
                   help='用户决定不投放视频时，把"缺 mp4/无视频页"降级为报告里的显式声明（默认不开）')
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser('shots'); p.add_argument('--work', required=True); p.set_defaults(fn=cmd_shots)
    a = ap.parse_args()
    try:
        a.fn(a)
    except SystemExit:
        raise
    except Exception:
        # A crash used to leave exit code 1 with no report at all, which reads exactly like
        # "the gates rejected the deck" -- the third clean-room audit caught that twice.
        # Anything that dies now still writes a verdict that says it died.
        import traceback
        tb = traceback.format_exc()
        try:
            wp = work_paths(a.work)
            rep = os.path.join(wp['report'], u'崩溃.md')
            io.open(rep, 'w', encoding='utf-8').write(
                u'# %s 子命令执行中断\n\n本次结果**不可信**，不是"闸拦下了"，是脚本崩了：\n\n```\n%s```\n'
                % (a.cmd, tb))
            print('停止：%s 崩溃，详情见 %s' % (a.cmd, rep))
        except Exception:
            print('停止：%s 崩溃，且报告也写不出来' % a.cmd)
        print(tb.strip().splitlines()[-1])
        sys.exit(3)


if __name__ == '__main__':
    main()

