# -*- coding: utf-8 -*-
"""把 Word 的段落提炼成语料那种"一页一意"的显示文字。

为什么要有这个模块：语料好示范页的中位文字量是 19 字（kb/data_design.json），
而逐字上屏做出来的是 100-300 字。差的不是措辞，是"有没有把信息提炼成
一个结论 + 一到三个短句 + 一个大数字"。这里给出可复用的提炼器：
plan 用它给每页打 content_type、抽 key_numbers、生成 display_lines 提案，
agent 在提案上改写（提案一律标 proposal=True，闸门核的是最终成品，不是提案）。
"""
import re

import data_layout as _DL

NUM_UNIT = r'(?:%|％|万元|亿元|万|亿|元|台|例|人|次|天|城|店|个|倍|年|月|日|家|:1|：1)'
NUM = re.compile(r'[-+]?\d[\d,\.]*\s*' + NUM_UNIT + '?')
ENUM = re.compile(u'[、；;，,]')
STEP_MARK = re.compile(u'^(?:第[一二三四五六七八九十]+[步件条个]|[0-9]+[、.)）]|'
                       u'(?:首先|其次|再次|最后|然后))')
TIME_MARK = re.compile(u'(?:20\\d{2}|\\d{1,2}\\s*月|第\\s*\\d+\\s*[季年月]|\\d+\\s*个月内)')
CONTRAST = re.compile(u'相比|高于|低于|对比|而| versus |而不是|从.{0,10}(到|至)')
LISTISH = re.compile(u'[、；].+[、；]')
# 单条显示行 ≤14 字、每页 ≤2 条、整页 ≤64 字、headline ≤16 字。
# 锚点是 2026-11-07 现测的语料：随机 6 套 / 40 页里"单行字数 p50=4、p90=12"，
# "整页字数 p50=29、p75=69"；上一档 20 字 × 3 行 / 90 字把成品顶到 p50=43 字，
# G22 最重的"每页字数 p50"那一维只拿到 0.442（下限 0.5），整册 83.7 < 85。
MAX_LINE = 14
MAX_LINES = 2
HEADLINE_MAX = 16
PAGE_CHARS_MAX = 64
# G24 与提案装箱必须用同一把尺子，否则会出现"引擎以为装下了、闸说没装下"。
# 单位表沿用 G24 现有的那份（没有"家/次"），改成更宽的表会让既有交付物的判据漂移。
UNIT_G24 = r'(?:%|％|万元|亿元|万|亿|元|台|例|人|次|天|城|店|个|倍|年|月|日)'
NUM_G24 = re.compile(r'[-+]?\d[\d,\.]*\s*' + UNIT_G24 + '?')


def _clean(s):
    return re.sub(r'\s+', ' ', (s or '').strip()).strip('，。、；： ,;')


DANGLING = re.compile(u'[的了和与在为把以用对为或及就还也很更但其这那之向于以及是到从按照给让使等]$')
DATEISH_HEAD = re.compile(u'^(?:[\d,\.]{2,}\s*(?:年|月|日)|[-+]?\d)')
# 裸序数/纯数词不是类别名：真渲染页 6 的筹码上屏 '第一·100 万'——'第一' 既没说
# 这一百万是什么，也不是原文里的词，只是枚举计数器。'第三方'/'第一季度' 是真词，
# 所以只否掉"第+数词"整串与纯数词整串，不碰带名词尾巴的。
BARE_ORDINAL = re.compile(u'^(?:第\s*[一二三四五六七八九十百千\d]+|[一二三四五六七八九十百千]+)$')


VERBY = re.compile(u'[的是为了做要按等于约近从到与和及在不给将已被把将让有该等]')


def phrase_of(text, maxlen=8):
    """标签用词：只在"连续中文段"里挑，先按常见动词/虚词切段，取长度最像名字的那段。

    逐页目检（v10 真渲染 247 页）实测柱底与标签出现 '主办方的口径是为不少'、
    '等女性代表捐赠健康体'、'现场另向 位“三八红'、'潜在池 412 '：
    前两种是半句话、第三种是把数字抹掉后两侧对接出来的假话、最后一种是数字没剥干净。
    现在候选只从原文的连续中文段来（保证屏幕上每个字都能举证），
    再按长度往"5 个字左右的词"靠：'主办方的口径'、'女性代表'、'三八红旗手'、'潜在池'。
    """
    cands = []
    for run in re.findall(u'[一-龥]{2,12}', text or u''):
        r = re.sub(u'^[位个家台例人次天店条项份种年月日]', u'', run)
        if len(r) >= 2:
            cands.append(r)
        for seg in VERBY.split(r):
            seg = _clean(seg)
            if len(seg) >= 2:
                cands.append(seg)
    cands = [c for c in cands if 2 <= len(c) <= 12 and not re.match(u'^[-+]?\d', c)]
    # 动词开头的整段（'要按原样读'、'年在华东'）只当兜底：它读起来还是半句话
    plain = [c for c in cands if not VERBY.match(c)]
    cands = plain or cands
    if not cands:
        return u''
    cands.sort(key=lambda x: (abs(len(x) - 5), -len(x)))
    return _clean(cands[0])[:maxlen]


def is_clean_phrase(s):
    """标签/筹码上的字要像一句人话：不以数字或断头日期开头、不以虚词收尾、2–12 字。

    逐页目检（v10 真渲染 247 页）抓到的三类垃圾标签：
    '026年10月通'（窗口把 2026 裁掉一位）、'可引证的原文，所'（虚词收尾）、
    '第2项'（编号占位）——它们都过得了字数闸，只有看图才看得见。"""
    s = _clean(s)
    if len(s) < 2 or len(s) > 12:
        return False
    if DATEISH_HEAD.match(s):
        return False
    if BARE_ORDINAL.match(s):
        return False
    if DANGLING.search(s):
        return False
    if u'，' in s or u'。' in s or u'、' in s:
        return False
    return True


def fact_units(text, ents=()):
    """这一串文字里"必须活到成品上"的事实单元：数字（带单位）+ 专名。

    G24 逐条拿这个清单去成品里找，提案装箱与分页预算也拿同一个清单判断
    "这一页装不装得下"。三处共用一个定义是刻意的：上一版 G24 自己写正则、
    提案只数数字，于是一页三个数字加两个专名的页被引擎当成合格，成品少了 3 个事实。
    """
    text = text or u''
    out = []
    for m in NUM_G24.finditer(text):
        # 复合量词跟数字走（'26 万人次' 不是 '26 万'）：尺子自己截断，
        # 成品上屏截断值也能过 G24——真渲染页 6 就是这么漏过去的。
        _mn = re.match(r'[-+]?\d[\d,\.]*\s*', m.group(0))
        _u = m.group(0)[_mn.end():] if _mn else u''
        _u, _end = _DL.extend_unit(text, m.end(), _u)
        tok = _clean(text[m.start():_end])
        core = re.match(r'[-+]?([\d,\.]+)', tok)
        core = (core.group(1).replace(',', '').replace('.', '') if core else u'')
        # '03' '05' 这种是章节序号/列表编号（原文里写作 '—03'），不是要举证的事实
        if core and core[0] == u'0' and len(core) <= 2:
            continue
        if len(tok) >= 2 and tok not in out:
            out.append(tok)
    for e in (ents or ()):
        e = _clean(e)
        # 专名要像个名字：两字的 '现在/壁垒/张网' 是普通词，
        # 把它们算成"必须上屏的事实"只会逼装配贴抽象词筹码（v10 实测 facts_undrawn 里全是这种）
        if e and len(e) >= 3 and e in text and e not in out:
            out.append(e)
    return out


DATE_ONLY = re.compile(u'^(?:(?:19|20)\\d{2}\\s*年(?:\\s*\\d{1,2}\\s*月)?'
                       u'|\\d{1,2}\\s*月(?:\\s*\\d{1,2}\\s*[日号])?'
                       u'|\\d{1,2}\\s*[日号]|\\d{1,2}[:：]\\d{2})$')


def _unit_of(tok):
    m = re.match(r'[-+]?\d[\d,\.]*', tok or u'')
    return (tok[m.end():].strip() if m else u'')


def _num_core(tok):
    """数字本体（不带单位）：G24 的 token 是 '268'，series 的 token 是 '268 家'，
    两边不在同一写法上就没法比对（实测因此每组柱子既没合并、又把分页顶成一页一个数）。"""
    m = re.match(r'[-+]?\d[\d,\.]*', (tok or u'').strip())
    return m.group(0) if m else (tok or u'').strip()


def series_groups(text, min_len=3):
    """同一单位的并列数值 ≥3 个 = 一组条形，不是三个显示行。

    语料里 '杭州 9 家、宁波 7 家、温州 4 家、绍兴 3 家' 画的是四根柱子；
    按数字逐个算版面预算时，分页会把这组从中间砍断（实测成品只剩两根，
    条形组条件 ≥3 永远不成立，图形化率停在 0.045，远低于好示范基线 kb/parity_baseline.json）。
    """
    by = {}
    for m in NUM.finditer(text or u''):
        tok = _clean(m.group(0))
        if len(tok) < 2 or DATE_ONLY.match(tok) or re.match(r'^(19|20)\d{2}$', tok):
            continue
        by.setdefault(_unit_of(tok), []).append(tok)
    return {u: v for u, v in by.items() if len(v) >= min_len}


def series_cores(text, min_len=3):
    return set(_num_core(t) for v in series_groups(text, min_len).values() for t in v)


def series_clause_map(text, min_len=3):
    """条形组这一族载体：它盖住哪些数字，以及哪些子句整段会被画成柱子。

    同一子句里的地名/机构名会变成柱顶标签（'杭州 9 家' 画出来就是 杭州 + 9 家），
    所以那些专名不该再被算成"要挤进显示行的事实"。上一版没算这条，
    于是一页四城系列被当成 8 个事实超载，split_by_facts 把这组柱子从中间砍成四页，
    图形化率永远上不去（实测每页只剩一根柱子，bar_group 的 ≥3 条件再也不成立）。
    """
    cores = series_cores(text, min_len)
    cov = u''
    if cores:
        pat = u'(?<!\\d)(%s)(?!\\d)' % u'|'.join(re.escape(c) for c in sorted(cores))
        cov = u' '.join(c for c in split_clauses(text)
                        if re.search(pat, re.sub(r'\s', u'', c)))
    return cores, cov


def bar_covered(text):
    """装配侧 bar_group 真会画出来的那几个数（同一份选择逻辑，不再各算各的）。

    只认"整串带单位的数"：柱子画 '500'（无单位）不等于把原文的 '500 人' 也带上了——
    v10 页 21 就是这么漏掉一个成交额级别的事实的（同一句里既有"500 名女性"又有"500 人"，
    柱子挑了无单位那一组，计划侧却按数字核判定"已覆盖"）。
    """
    try:
        import data_layout as _DL
    except Exception:
        return set(), set(), u''
    clauses = [c for c in split_clauses(_clean(text)) if c]
    items = _DL.items_from_numbers(clauses) or []
    toks = set(_clean(i[2]) for i in items if len(i) > 2 and i[2])
    bare = set(_num_core(x) for x in toks if _unit_of(x) == u'')
    cov = u' '.join(c for c in clauses
                    if any(k and k in re.sub(r'\s', u'', c) for k in toks))
    return toks | bare, set(), cov


def carrier_units(text, ents=()):
    """必须靠标题/显示行/筹码上屏的事实（条形组自己会画的数字与同子句专名不在这里）。"""
    toks, _x, cov = bar_covered(text)
    if not toks:
        toks, cov = series_clause_map(text)
    out = []
    for u in fact_units(text, ents):
        if u in toks or _clean(u) in toks:
            continue
        # 同子句只豁免"专名"（它们会变成柱底标签）；数字必须整串对上才算覆盖
        if u in cov and u and not re.match(u'^[-+]?\d', u):
            continue
        if u not in out:
            out.append(u)
    return out


def budget_units(text, ents=()):
    """这一页要占几个"版面名额"：每个事实一个，一组柱子（bar_group 一次画完）只占一个。"""
    out = carrier_units(text, ents)
    try:
        import data_layout as _DL
        n = len(_DL.items_from_numbers([c for c in split_clauses(_clean(text)) if c]) or [])
    except Exception:
        n = 0
    if n >= 3:
        out += [u'条形组×%d' % n]
    return out


def key_numbers(text, limit=3):
    """一页里最该被看见的数字：带单位、金额、百分比优先，纯年份最后。"""
    found = []
    _t = text or u''
    for m in NUM.finditer(_t):
        _mn = re.match(r'[-+]?\d[\d,\.]*\s*', m.group(0))
        _u = m.group(0)[_mn.end():] if _mn else u''
        _u, _end = _DL.extend_unit(_t, m.end(), _u)
        tok = _clean(_t[m.start():_end])
        if not tok or len(tok) < 2:
            continue
        ctx = _clean(_t[max(0, m.start() - 14):_end + 4])
        weight = 3 if re.search(NUM_UNIT, tok) and re.search(r'[%％]|\d{2,}', tok) else 2
        if re.match(r'^(19|20)\d{2}$', tok):
            weight = 1
        found.append({'value': tok, 'context': ctx, 'weight': weight, 'at': m.start()})
    # 裸年份（2026 / 2026 年）不是"这页最该被看见的数字"，它是时间标签：
    # 留着它当大数字，成品就会把"2026"打在 110pt 位置，而 412 家这种真结论没影。
    def _is_bare_year(v):
        return bool(re.match(r'^(19|20)\d{2}\s*年?$', (v or '').strip()))

    if any(not _is_bare_year(f['value']) for f in found):
        found = [f for f in found if not _is_bare_year(f['value'])]
    found.sort(key=lambda x: (-x['weight'], x['at']))
    seen, out = set(), []
    for f in found:
        if f['value'] in seen:
            continue
        seen.add(f['value'])
        out.append(f)
        if len(out) >= limit:
            break
    return out


def content_type(paragraphs, has_table=False):
    txt = ' '.join(p for p in paragraphs if p)
    if has_table:
        return 'ledger'
    if not txt.strip():
        return 'section'
    nums = key_numbers(txt, 5)
    if STEP_MARK.search(_clean(paragraphs[0])) and len(paragraphs) >= 2:
        return 'process'
    if TIME_MARK.search(txt) and re.search(u'个月|月|年', txt) and re.search(u'完成|投放|签约|复核|决策', txt):
        return 'timeline'
    if CONTRAST.search(txt) and len(nums) >= 2:
        return 'compare'
    if LISTISH.search(txt) and len(nums) < 2:
        return 'list'
    if nums:
        return 'metric'
    return 'statement'


ENUM_TAIL = re.compile(u'[，、,]\s*第\s*[一二三四五六七八九十百\d]+'
                       u'(?:\s*[、，,]\s*第\s*[一二三四五六七八九十百\d]+)*\s*[、，,]?\s*$')


def strip_enum_tail(s):
    """枚举清单不许拦腰截断：'样板期十二个月里，第一、第三' 屏幕上是个没说完的清单。

    标题（split_headline）与显示行（_clip）共用这一把尺——两处各写一份正则，改一处漏一处
    就是上一版 '…第一·24' 碎片筹码的来路。整段退到清单之前，那一段自己读得通；
    '第三章' 这种带名词的序数不受影响（正则要求序数收尾）。退不动就原样返回，不硬删。

    没有枚举尾巴时**必须原样返回、连空白都不许归一**：实测顺手 _clean 一下会把标题末尾的
    '：' 抹掉，page_sig 跟着变，重跑 plan就把 6 页已写好的分镜（含已获准的 field 降级）全抹了。
    """
    m = ENUM_TAIL.search(s or u'')
    if m and m.start() >= 4:
        return _clean((s or u'')[:m.start()].rstrip(u'，。；、, '))
    return s


def _clip(s, limit=MAX_LINE):
    s = _clean(s)
    if len(s) <= limit:
        # 窗口裁下来的"上下文"常常正好断在数字中间（'…签约 2'），屏幕上就是半个假数字：
        # 结尾这一段如果在本串之后还有数字，说明被切断了，去掉它。
        m = re.search(r'(\d[\d,\.]{0,})$', s)
        if m:
            tail = s[max(0, m.start() - 1):]
            if re.match(u'^[\d,\.]+\s*(?:%|％|万元|亿元|万|亿|元|台|例|人|次|天|城|店|个|倍|年|月|日)?'
                        u'(?![\d])', tail) is None:
                s = _clean(s[:m.start()])
        return strip_enum_tail(s)
    # 只能在标点处断，绝不能把数字或单位切成半截
    cuts = [m.end() for m in re.finditer(u'[，、；：,;:]', s) if m.end() <= limit]
    if cuts:
        return strip_enum_tail(_clean(s[:max(cuts)]))
    # 20 字里找不到标点断点就别硬裁：以前这里会 (head+数字)[:limit] 把 "268" 切成 "2"，
    # 屏幕上出现半截数字，比不写更糟。交给上层的事实装箱去另找短句。
    return ''


def _num_only(s, limit=MAX_LINE):
    """子句太长装不下时，宁可只上屏"数字 + 单位"，也不许拿 key_numbers 的窗口裁下来的
    半截上下文：窗口是从数字左边固定字数裁的，实测裁出 '利 281.4 万元'（'毛利' 的前一个字
    被留在了窗口外）这种上屏碎片。"""
    s = _clean(s)
    m = re.search(NUM_UNIT, s)
    if not m:
        return ''
    out = _clean(m.group(0))
    return out if len(out) <= limit else ''


def propose_lines(pg):
    """给出 ≤MAX_LINES 条、每条 ≤MAX_LINE 字的显示行提案；提不出合格短句就返回空，让 agent 自己写。"""
    body = [x for x in (pg.get('body') or []) if x]
    head = _clean(pg.get('headline'))
    ctype = pg.get('content_type') or content_type(body)
    lines = []
    # ≥3 个同单位数值交给条形组一次画完（装配里 bar_group 会把数字打在条顶），
    # 它们既不占显示行预算，也不能被算成"装不下的事实"
    _ser_cores = series_cores(u' '.join(body))
    if not body:
        return [], ctype, []
    # 文本体裁的并列要点（培训/制度/手册：『先看现场。再找问题。最后定动作。』）：
    # ≥3 条各自成句的短句、又没有要举证的数字 → 返回空提案 + content_type='list'，
    # 让装配走卡片栅格（prose_display 同一把 parallel_enum 尺），而不是压成 ≤2 行短句——
    # 压行会丢掉并列结构、把这类页的图形化率打成 0（#61 实测培训探针 6 页 graphic_rate=0）。
    # 守卫：有数字的页不让位（条形组/大数字更该走），散文长句凑不够 3 条短句天然落在判据外。
    if not key_numbers(u' '.join(body), 3):
        _pts = parallel_enum(body)
        if len(_pts) >= 3:
            return [], 'list', []
    if ctype == 'metric':
        kn = [k for k in key_numbers(' '.join(body), 2)
              if _num_core(k['value']) not in _ser_cores] or key_numbers(' '.join(body), 2)
        # 区间是一个整体事实（'1280 到 1980 元'）：整条上屏，别拆成两个裸数
        ranges = []
        for m in re.finditer(u'(\\d[\\d,.]*)\\s*(?:到|至|~|—)\\s*(\\d[\\d,.]*)\\s*(?:%s)' % NUM_UNIT,
                             u' '.join(body)):
            rng = re.sub(u'\\s+', u' ', m.group(0)).strip()
            if len(re.sub(u'\\s', u'', rng)) <= MAX_LINE and rng not in ranges:
                ranges.append(rng)
        lines += ranges
        for k in kn:
            if any(k['value'] in r for r in ranges):
                continue
            # 用"含这个数字的那个子句"，不要用窗口裁出来的半截话
            one = ''
            for src in body:
                for c in split_clauses(_clean(src)):
                    c = _clean(c)
                    if k['value'] in c and len(c) <= MAX_LINE:
                        one = c
                        break
                if one:
                    break
            one = one or _num_only(k['context'])
            if one and NUM.search(one) and one not in lines:
                # 目检实测 '二·1280'：章节序号粘着数字的窗口碎片不是显示行；
                # 要么是干净数字+单位，要么至少 3 个汉字读得通
                cjk = len(re.findall(u'[一-龥]', one))
                pure = re.match(u'^[-+]?\\d[\\d,.]*\\s*(?:%s)?$' % NUM_UNIT, one.strip())
                if cjk >= 3 or pure:
                    lines.append(one)
        if not lines:
            for b in body[:2]:
                one = _clip(b)
                if one:
                    lines.append(one)
    elif ctype in ('list', 'process', 'timeline'):
        parts = []
        for b in body:
            parts += [x for x in ENUM.split(b) if x.strip()] if len(body) <= 2 else [b]
        for p in parts:
            one = _clip(STEP_MARK.sub('', p))
            if one and one not in lines:
                lines.append(one)
    elif ctype == 'compare':
        for b in body[:3]:
            one = _clip(b)
            if one:
                lines.append(one)
    else:
        for b in sorted(body, key=len):
            one = _clip(b)
            if one:
                lines.append(one)
                break
    lines = [l for l in lines if l][:MAX_LINES]
    if head:
        lines = [l for l in lines if l != head][:MAX_LINES]
    # 提案里最坏的一种是"两条互相包含"（同一段落裁出来的重叠碎片）：屏幕上就是两行半截话。
    dedup = []
    for l in lines:
        others = [o for o in lines if o != l]
        if any(l in o for o in others):
            continue
        if l not in dedup:
            dedup.append(l)
    lines = dedup[:MAX_LINES]
    # 事实装箱：G24 逐个核对"事实单元在不在屏上"，所以提案必须先把这页的数字**和专名**全装下；
    # 装不下就是要拆页 —— 那种页 needs_edit 为真，agent 必须拆页而不是删数字。
    # 尺子与 G24、分页预算共用 carrier_units（条形组会自己画的那些不占这里的名额）。
    vals = carrier_units(u' '.join(body), pg.get('entities') or ())
    unpacked = []

    def _tighten(cl, v):
        """把子句压到一屏一行放得下：先剥掉开头的年份/介词，再退到"数字+单位"的边界收尾。"""
        cl = _clean(cl)
        if v not in cl:
            return ''
        cl = re.sub(u'^(19|20)\d{2}\s*年(?:初|度|前)?[，,]?', '', cl)
        cl = re.sub(u'^(?:公司|集团|我们|平台)?在', '', cl)
        cl = re.sub(u'^(?:于|把|将|其中|以及|并|和)', '', cl)
        cl = _clean(cl)
        if len(cl) <= MAX_LINE:
            return cl if v in cl else ''
        ends = [m.end() for m in re.finditer(NUM_UNIT, cl) if m.end() <= MAX_LINE and v in cl[:m.end()]]
        return _clean(cl[:max(ends)]) if ends else ''

    def _clause_for(v):
        cands = []
        for src in body:
            for c in split_clauses(_clean(src)):
                c = _clean(c)
                if v in c:
                    cands.append(c)
        if not cands:
            return ''
        best = min(cands, key=len)
        return best if len(best) <= MAX_LINE else _tighten(best, v)

    for v in vals:
        # 标题框本身就在屏幕上：已经写进 headline 的事实不再占显示行的名额
        cv = _clean(v)
        if any(v in l for l in lines) or (cv and cv in head):
            continue
        cl = _clause_for(v) or v
        ccl = _clean(cl)
        if ccl and (ccl == head or ccl in head):
            continue
        put = False
        for i, l in enumerate(lines):
            # 只允许"标签·数字"这一种拼法：两边都带数字的行拼起来变成 '281.4 万元·268' 这种读不通的串。
            # 标签那半截还必须是干净短语：'样板期十二个月里，第一·24' 是逗号残句粘了个文号数字
            # （真渲染页 25 目检），与 hero 标签共用 is_clean_phrase 这一把尺。
            if (u'·' in l or len(_clean(cl)) > 6
                    or re.search(r'\d', l) or not re.search(r'\d', cl)
                    or not is_clean_phrase(l)):
                continue
            cand = _clean(l + u'·' + cl)
            if len(cand) <= MAX_LINE:
                lines[i] = cand
                put = True
                break
        if not put and len(lines) < MAX_LINES and len(_clean(cl)) <= MAX_LINE:
            lines.append(_clean(cl))
            put = True
        if not put:
            unpacked.append(v)
    needs_edit = (not lines) or bool(unpacked) or any(len(re.sub(r'\s', '', b)) > 60 for b in body)
    if unpacked:
        pg['_unpacked_facts'] = unpacked      # 给拆页与 refine 用：这页必须拆，不许删数字
    return lines, ctype, needs_edit


def split_clauses(s, colon=False):
    """按标点切子句，但千分位逗号不算断点。

    实测坑：'1,053 人，客单 1200 元' 用 `re.split('[，。；、,;:]')` 会先被 "1,053" 里那个
    逗号切开，_short_label 于是挑中第一段 '1'（它也"含数字且 ≤8 字"），
    结果成品上 hero 大数字下面的结论标签就写着一个孤零零的 '1'（页 113 实测）。"""
    t = re.sub(u'(?<=\\d),(?=\\d)', u'\x00', s or u'')
    pat = u'[，。；、,;:]' if colon else u'[，。；、,;]'
    out = []
    for c in re.split(pat, t):
        c = c.replace(u'\x00', u',').strip()
        if c:
            out.append(c)
    return out


def page_budget(pg, verbatim=False):
    """G23 的判据本体：成品之外的计划侧也先自查一遍，省得装配完才被闸拦。"""
    head = _clean(pg.get('headline'))
    lines = [x if isinstance(x, str) else x.get('text', '') for x in (pg.get('display_lines') or [])]
    bad = []
    if len(head) > HEADLINE_MAX:
        bad.append({'why': 'headline 超 %d 字' % HEADLINE_MAX, 'text': head[:24]})
    if not verbatim:
        if len(lines) > MAX_LINES:
            bad.append({'why': 'display_lines 超过 %d 条' % MAX_LINES, 'text': str(lines[:4])})
        for l in lines:
            if len(l) > MAX_LINE:
                bad.append({'why': '单条显示行超 %d 字' % MAX_LINE, 'text': l[:24]})
        chars = sum(len(re.sub(r'\s', '', l)) for l in lines)
        if chars > PAGE_CHARS_MAX:
            bad.append({'why': '整页显示文字超 %d 字' % PAGE_CHARS_MAX, 'text': str(chars)})
    return bad


def _shared_enum(parts, min_items=3, item_max=12):
    """一串短子句里"并列的一串"：按公因子分组，取覆盖条目最多的那一组。

    同页常常混着一句总起（'行业里已经有三张网'，它就是标题），它不含公因子，
    不该因此否掉整组并列——所以是"分组取最大"，不是"全部条目必须共串"。
    """
    parts = [p for p in [_clean(x) for x in (parts or [])] if p]
    uniq = list(dict.fromkeys(parts))
    best = (0, 0, [])
    for p in uniq:
        for i in range(2, len(p) + 1):
            for j in range(0, len(p) - i + 1):
                g = p[j:j + i]
                grp = [q for q in uniq if g in q]
                # 覆盖条目最多优先，其次串更长（'联体' 比 '体' 更能说明这是同一组东西）
                if (len(grp), i) > (best[0], best[1]):
                    best = (len(grp), i, grp)
    members = [m for m in best[2] if 2 <= len(m) <= item_max]
    return members if len(members) >= min_items else []


# 句末标点：培训/制度/手册的要点是"一条条独立短句"（先看现场。再找问题。最后定动作。），
# 不像路演那样共享一个词（医联体/科联体/客联体 共享「联体」）。判据用句子边界而不是公因子。
_SENT_END = re.compile(u'[。！？!?]$')


def _sentence_points(parts, min_items=3, item_max=12):
    """独立短句要点：每条原始子句自带句末标点（。！？）且清理后 2..item_max 字。

    与 _shared_enum 互补——那条认"共享词的一组东西"，这条认"各自成句的并列动作/要点"，
    后者才是培训/制度/手册这类文本体裁的常态（没有公因子，旧版一律退回子句装箱→图形化率 0）。
    守卫只认"真的各自成句"的条目：散文被 plan 切成逗号子句时只有句尾一条带 。，凑不够
    min_items，不会被误判成卡片（test_gates 的连贯叙述反证『道理不复杂，先把车交给医院…』
    正是"一个逗号长句"形状，句末标点只有一处，天然落在守卫外）。"""
    out = []
    for raw in (parts or []):
        raw = (raw or u'').strip()
        if not _SENT_END.search(raw):
            continue
        c = _clean(raw)
        if 2 <= len(c) <= item_max and c not in out:
            out.append(c)
    return out if len(out) >= min_items else []


def parallel_enum(text, min_items=3, item_max=12):
    """找出"并列的一串"——语料那种 2~3 张卡片的来源。

    三种形状都要认：① 一整句里的并列（'医联体连医院，科联体连科研和工厂，客联体连客户'，
    共享「联体」）；② plan 按子句切好后落在同一页上、共享词的条目；③ 各自成句的独立短句
    要点（'先看现场。再找问题。最后定动作。'，无公因子但每条自成一句）——③ 是文本体裁的常态。
    判据是结构不是词数：①② 要 ≥min_items 条短子句且有 ≥2 字公因子；③ 要 ≥min_items 条
    各自带句末标点的短句。散文『谁都不缺主体，缺的是把主体串起来的那一层』既无公因子、
    也不是多条独立短句，不会被误判成卡片。"""
    if isinstance(text, (list, tuple)):
        got = _shared_enum(list(text), min_items, item_max)
        return got or _sentence_points(list(text), min_items, item_max)
    # 字符串：先认"多条各自成句的短句"（句号边界），再认"一句里的并列"（公因子）。
    sents = [s.strip() for s in re.split(u'[。！？!?；;\n]', _clean(text or u'')) if s.strip()]
    pts = [s for s in dict.fromkeys(sents) if 2 <= len(s) <= item_max]
    if len(pts) >= min_items:
        return pts
    for sent in re.split(u'[。；;\n]', _clean(text or u'')):
        got = _shared_enum(re.split(u'[，、,]', sent or u''), min_items, item_max)
        if got:
            return got
    return []


def clause_pack(text, max_line=MAX_LINE, max_lines=MAX_LINES, drop=u''):
    """把散文按**子句**装箱成 ≤max_lines 条 ≤max_line 字的显示行。

    只在子句边界（，、；）断，绝不裁半截话——窗口裁出来的『…签约 2』这种比空着更像排错版。
    单条子句本身就超长的（>max_line）整条不 상屏，它里面的事实由装配末尾的事实安全网补回。
    """
    drop = _clean(drop)
    lines, cur = [], u''
    for c in split_clauses(_clean(text or u'')):
        c = _clean(c)
        if not c or c == drop or (drop and (c in drop or drop in c)):
            continue
        probe = (cur + u'，' + c) if cur else c
        if len(re.sub(u'\s', u'', probe)) <= max_line:
            cur = probe
            continue
        if cur:
            lines.append(cur)
            cur = u''
            if len(lines) >= max_lines:
                break
        if len(c) <= max_line:
            cur = c
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return [l for l in lines if l][:max_lines]
