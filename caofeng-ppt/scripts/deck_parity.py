# -*- coding: utf-8 -*-
"""同级评分：用与语料挖掘同一份度量（study_data_design.analyze_slide）量成品，
按六个维度对照 kb/data_design.json 的分布打分。

这不是"再加一个主观审美分"：每一条都是语料实测出来的可数量，
差在哪一维、差多少，报告里直接写出来。指标本身要能区分好坏——
v19 那份逐字上屏的册子必须被判成不达标，语料自己的好示范必须被判成达标，
否则这个分数没有意义（test_gates 里就是拿这两端做正反对照）。
"""
import os, sys, json, io, re, statistics, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 单独跑 CLI 时的下限，必须与 caofeng.TH['parity_score_min'] 同值。78 是及格线：低于好示范册
# 全量（54 套）同尺实测中位 82.8、高于 junk 册中位 57.1；100（六维 attain 全 1.0）才是完全同等级。
# 之前这里硬写 85：G22 判 78、命令行报 85，同一个指标两套口径，看的人不知道信哪个。
# test_gates 里有一条漂移检查，改一处不改另一处就红。
PARITY_SCORE_MIN = 78


def _kb(name):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kb', name)
    return json.load(io.open(p, encoding='utf-8'))


def measure_deck(pptx_path, bad_pages=None):
    """量一套册的每页几何/字数。bad_pages 给定时跳过这些页号——校准时用它剔除非模版元素
    （广告/空页/文字墙等坏页）；量成品时不传，整册全量，密度才如实。"""
    import zipfile
    import study_data_design as SD
    z = zipfile.ZipFile(pptx_path)
    names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    names.sort(key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
    rows = []
    for n in names:
        idx = int(re.search(r'(\d+)', os.path.basename(n)).group(1))
        if bad_pages and idx in bad_pages:
            continue
        xb = z.read(n)
        f = SD.analyze_slide(xb)
        if f:
            f['page'] = idx
            f['has_tbl'] = b'<a:tbl>' in xb
            rows.append(f)
    z.close()
    return rows


def _slide_count(pptx_path):
    import zipfile
    z = zipfile.ZipFile(pptx_path)
    n = len([x for x in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', x)])
    z.close()
    return n


def _bad_page_index():
    """非模版元素（坏页）台账，{deck名: set(坏页号)}。判据不是这里另写的，直接读
    grade_exemplars 用 corpus_tables.BAD_RULES 量出的 page_index——与 study_data_design
    只量好示范页是同一把尺。缺这份台账就没有"干净基线"可言，校准必须拒绝而不是退回含污染抽样。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kb', 'exemplar_grades.json')
    if not os.path.exists(p):
        return None
    g = json.load(io.open(p, encoding='utf-8'))
    bad = {}
    for row in (g.get('page_index') or []):
        bad.setdefault(row[0], set()).add(int(row[1]))
    return bad


def quantile(vals, q):
    if not vals:
        return 0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * q))]


def score(pptx_path, th=None):
    """同级分：与 kb/parity_baseline.json 逐维对比。基线只用语料好示范页校准
    （calibrate 剔除广告/空页/文字墙等非模版元素、整册跳过 junk 册），与 study_data_design
    同一把尺——非模版元素不学习沉淀。量成品时整册全量（不剔页），密度才如实。

    高更好的一维（大数字率/有图率/图形化率）按 mine/median 记满分封顶；
    低更好的一维（每页字数）按 median/mine 记满分封顶。
    满分 100 的含义是"每一维都不低于语料好示范中位数"（完全同等级），78 只是及格线。
    """
    th = th or {}
    bp = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kb', 'parity_baseline.json')
    if not os.path.exists(bp):
        return {'score': None, 'error': '缺 kb/parity_baseline.json（先跑 deck_parity.py --calibrate）'}
    base = json.load(io.open(bp, encoding='utf-8'))
    m = measure_one(pptx_path)
    if not m:
        return {'score': 0.0, 'pages': 0, 'dims': [], 'why': '成品读不出页'}
    dims = []

    def add(name, weight, better, mine, med, na_why=None):
        """na_why 非空 = 这一维在本册**没有可量的页**（体裁不适用），不是不合格。
        权重按比例分给能量化的维，总分仍是 100 分制。
        纯文字制度册没有"含数字页"、整册台账没有"非表格页"——按 0 分记等于宣布
        技能只会做商业计划路演，那是把尺子绑在一种体裁上。"""
        if na_why:
            dims.append({'dim': name, 'weight': 0.0, 'better': better, 'corpus_median': med,
                         'deck': mine, 'attain': 1.0, 'points': 0.0, 'na': na_why})
            return
        if better == 'low':
            got = 1.0 if mine <= med else round(med / float(mine or 1), 3)
        else:
            got = 1.0 if mine >= med else round(mine / float(med or 1e-6), 3)
        dims.append({'dim': name, 'weight': weight, 'better': better, 'corpus_median': med,
                     'deck': mine, 'attain': got, 'points': round(weight * got * 100, 1)})

    _has_text_page = m.get('n_non_tbl', 1) > 0
    _has_num_page = m.get('n_with_num', 1) > 0
    _live_w = float(sum([0.26 if _has_text_page else 0, 0.12 if _has_text_page else 0,
                         0.20 if _has_num_page else 0, 0.16, 0.14, 0.12])) or 1.0
    add(u'每页字数 p50', 0.26 / _live_w, 'low', m['chars_p50'], base['chars_p50']['median'],
        None if _has_text_page else u'整册都是表格页，没有可比的正文页密度')
    add(u'每页字数 p90', 0.12 / _live_w, 'low', m['chars_p90'], base['chars_p90']['median'],
        None if _has_text_page else u'整册都是表格页，没有可比的正文页密度')
    add(u'大数字率', 0.20 / _live_w, 'high', m['hero_rate'],
        max(0.18, base['hero_rate']['median']),
        None if _has_num_page else u'本册没有任何含数字页（制度/条款类体裁），这一维不适用')
    add(u'有图率', 0.16 / _live_w, 'high', m['picture_rate'], base['picture_rate']['median'])
    add(u'图形化率', 0.14 / _live_w, 'high', m['graphic_rate'],
        max(0.10, base['graphic_rate']['median']))
    # 第 6 维留给表格与版式匹配：由 G24/G25 的达标率喂进来（这里没有就按 1 记，
    # 但 G22 会在报告里写明这一维的来源，避免"自己给自己满分"没人发现）
    _lm = round(m.get('layout_match', 1.0), 3)
    dims.append({'dim': u'版式与表格匹配', 'weight': 0.12 / _live_w, 'better': 'high',
                 'corpus_median': 1.0, 'deck': _lm, 'attain': _lm,
                 'points': round(0.12 / _live_w * _lm * 100, 1)})
    total = round(sum(d['points'] for d in dims), 1)
    return {'score': total, 'pages': m['pages'], 'threshold': th.get('parity_score_min',
                                                                     PARITY_SCORE_MIN),
            'dims': dims, 'baseline_decks': base['n_decks'],
            'na_dims': [d['dim'] for d in dims if d.get('na')],
            'chars_p50': m['chars_p50'], 'chars_p90': m['chars_p90'],
            'hero_rate': m['hero_rate'], 'picture_rate': m['picture_rate'],
            'graphic_rate': m['graphic_rate'],
            'basis': 'kb/parity_baseline.json（同一把尺子量的 %d 套语料 / %d 页）'
                     % (base['n_decks'], base['pages_total'])}


def measure_one(pptx_path, bad_pages=None):
    """把一套 deck 压成一组可比指标（与 score 用同一份度量函数）。"""
    rows = measure_deck(pptx_path, bad_pages=bad_pages)
    if not rows:
        return None
    non_tbl = [r for r in rows if not r['has_tbl']]
    # 表格页不吃"正文密度 / 大数字 / 图形化"这三判据，与 p50/p90 已有的 non_tbl 口径同一条尺子：
    # 巡检台账、制度附表这类体裁里，数字就在格子里、结构就是那张表。
    # 按路演页的期待去量它们会把技能判成"只会做商业计划书"（实测 7 表台账册 68.6 分）。
    # 不是放水：表格只能来自源 Word（G21 逐格核），引擎不会为了躲判据把内容造表。
    with_num = [r for r in rows if r['nums'] >= 1 and not r['has_tbl']]

    def rate(pool, pred):
        return round(sum(1 for r in pool if pred(r)) / max(1, len(pool)), 4)
    return {'deck': os.path.basename(pptx_path), 'pages': len(rows),
            'n_non_tbl': len(non_tbl), 'n_with_num': len(with_num),
            'chars_p50': quantile([r['chars'] for r in non_tbl], 0.5),
            'chars_p90': quantile([r['chars'] for r in non_tbl], 0.9),
            'hero_rate': rate(with_num, lambda r: r['hero_pt'] >= 60),
            'picture_rate': rate(rows, lambda r: r['pics'] >= 1),
            'graphic_rate': rate(rows, lambda r: r['cards'] >= 3 or r['bars'] >= 3
                                 or r['ellipses'] >= 1 or r['has_tbl'])}


def calibrate(sample=None, seed=7, out=None):
    """用同一把尺子量语料自己，得到"同级"的真实基线。非模版元素不学习沉淀：只学好好示范页
    （grade_exemplars.page_bad_reasons 判据之外的页），广告/分享页、模板编号主视觉、无字空页、
    文字墙、字压亮底、无层级小字一律剔除；过半坏页的 junk 册整册不参考。与 study_data_design
    只量好示范页同一把尺——上一版拿含坏页的原始抽样校准，是唯一没套过滤的学习产物。

    sample=None（默认）量全部好示范册（与 grade_exemplars 的 good_deck_set 同一批，无抽样噪声）；
    给整数则只取前 N 套（仅供调试，正式基线必须用全量，否则中位随抽样漂移）。"""
    import random
    import glob
    import corpus_path as CP
    src = CP.corpus_dir(strict=True)
    decks = sorted(glob.glob(os.path.join(src, '*.pptx')))
    bad_idx = _bad_page_index()
    if bad_idx is None:
        raise SystemExit('calibrate 需要 kb/exemplar_grades.json（先跑 grade_exemplars.py）：'
                         '非模版元素不学习沉淀，基线必须只用好示范页校准，不许退回含污染抽样')
    random.seed(seed)
    order = decks[:]
    random.shuffle(order)
    rows, skipped_junk, excluded_pages = [], 0, 0
    for d in order:
        if sample is not None and len(rows) >= sample:
            break
        base = os.path.basename(d)
        bad = bad_idx.get(base, set())
        try:
            total = _slide_count(d)
            good_n = total - len(bad)
            # junk 册整册不学：判据与 grade_exemplars 同一份（坏页占比 >=0.5 即 junk，含恰好半数）；
            # 好页太少（<3）量出的分位不稳，也不进基线
            if total and len(bad) / float(total) >= 0.5:
                skipped_junk += 1
                continue
            if good_n < 3:
                skipped_junk += 1
                continue
            m = measure_one(d, bad_pages=bad)
        except Exception:
            m = None
        if m:
            rows.append(m)
            excluded_pages += len(bad)
    if not rows:
        raise SystemExit('calibrate 失败：好示范册一套都没量出来')
    keys = ('chars_p50', 'chars_p90', 'hero_rate', 'picture_rate', 'graphic_rate')
    base = {'n_decks': len(rows), 'pages_total': sum(r['pages'] for r in rows),
            'calibration': 'good_pages_only', 'skipped_junk_decks': skipped_junk,
            'excluded_bad_pages': excluded_pages}
    for k in keys:
        vals = [r[k] for r in rows]
        base[k] = {'median': statistics.median(vals), 'p25': quantile(vals, 0.25),
                   'p75': quantile(vals, 0.75), 'min': min(vals), 'max': max(vals)}
    base['per_deck'] = rows
    out = out or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kb',
                              'parity_baseline.json')
    json.dump(base, io.open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return base


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('deck', nargs='?')
    ap.add_argument('--calibrate', action='store_true')
    ap.add_argument('--sample', type=int, default=None,
                    help='只取前 N 套好示范册（调试用）；默认全量，正式基线必须全量')
    a = ap.parse_args()
    if a.calibrate:
        b = calibrate(sample=a.sample)
        print('基线：%d 套语料 / %d 页' % (b['n_decks'], b['pages_total']))
        for k in ('chars_p50', 'chars_p90', 'hero_rate', 'picture_rate', 'graphic_rate'):
            v = b[k]
            print('  %-13s median=%-7s p25=%-7s p75=%-7s min=%-6s max=%s'
                  % (k, v['median'], v['p25'], v['p75'], v['min'], v['max']))
    else:
        print(json.dumps(score(a.deck), ensure_ascii=False, indent=1))
