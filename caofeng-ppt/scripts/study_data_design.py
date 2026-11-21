# -*- coding: utf-8 -*-
"""挖掘语料"数字与数据到底怎么摆"，产出 kb/data_design.json。

起因：语料 171 套里只有 4 套共 22 页真的用了 <a:tbl>。也就是说模板的数据表现力
根本不靠表格网格，而是靠大数字、卡片、条形、时间轴、对比。装配器只会画 <a:tbl>，
做出来的东西当然和语料不是一个水平。这里把这些手法的实测占比量出来，
变成引擎可以照着做的参数与闸可以照着核的阈值。
"""
import os, sys, json, io, re, glob, zipfile, collections, statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_path as CP
import grade_exemplars as GE

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
EMU_W, EMU_H = 12192000, 6858000
NUMTOK = re.compile(r'\d[\d,\.]*\s*(?:%|％|万|亿|元|台|例|人|天|城|店|个|倍|年|月|日)?')


def box_of(el):
    x = el.find('.//' + A + 'xfrm')
    if x is None:
        return None
    off, ext = x.find(A + 'off'), x.find(A + 'ext')
    if off is None or ext is None:
        return None
    return (int(off.get('x') or 0), int(off.get('y') or 0),
            int(ext.get('cx') or 0), int(ext.get('cy') or 0))


def prst_of(el):
    g = el.find('.//' + A + 'prstGeom')
    return g.get('prst') if g is not None else ''


def max_pt_in(el):
    out = []
    for e in el.iter():
        if e.tag.endswith('}rPr') or e.tag.endswith('}defRPr'):
            v = e.get('sz')
            if v and v.isdigit():
                out.append(int(v) / 100.0)
    return max(out) if out else 0


def analyze_slide(xml_bytes):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_bytes)
    spTree = root.find('.//' + P + 'cSld/' + P + 'spTree')
    if spTree is None:
        return None
    txt = ''.join((t.text or '') for t in root.iter(A + 't'))
    nums = NUMTOK.findall(txt)
    hero = 0
    hero_text = ''
    for sp in root.iter(P + 'sp'):
        t = ''.join((x.text or '') for x in sp.iter(A + 't')).strip()
        if not t or len(t) > 24:
            continue
        if NUMTOK.search(t) and max_pt_in(sp) >= 60:
            if max_pt_in(sp) > hero:
                hero, hero_text = int(max_pt_in(sp)), t
    boxes = [box_of(sp) for sp in root.iter(P + 'sp')]
    boxes = [b for b in boxes if b]
    # 条形：宽度相近、高度不等的竖条 ≥3 根
    tall = [b for b in boxes if b[2] > 0 and b[3] / float(b[2]) >= 1.6 and b[3] < EMU_H * 0.7]
    bars = 0
    if len(tall) >= 3:
        ws = collections.Counter(int(b[2] / 100000) for b in tall)
        common_w, cnt = ws.most_common(1)[0]
        bars = cnt if cnt >= 3 else 0
    cards = sum(1 for b in boxes if 0.08 <= b[2] / EMU_W <= 0.35 and 0.08 <= b[3] / EMU_H <= 0.45)
    round_recs = sum(1 for sp in root.iter(P + 'sp')
                     if prst_of(sp) in ('roundRect', 'rect', 'snipRoundRect'))
    ellipses = sum(1 for sp in root.iter(P + 'sp') if prst_of(sp) in
                   ('ellipse', 'pie', 'donut', 'blockArc', 'arc'))
    cxn = len(list(root.iter(P + 'cxnSp')))
    pics = len(list(root.iter(P + 'pic')))
    tbl = xml_bytes.count(b'<a:tbl>')
    return {'chars': len(re.sub(r'\s', '', txt)), 'nums': len(nums),
            'hero_pt': hero, 'hero_text': hero_text[:24],
            'bars': bars, 'cards': cards, 'round_recs': round_recs,
            'ellipses': ellipses, 'connectors': cxn, 'pics': pics, 'tbl': tbl}


def main():
    src = CP.corpus_dir(strict=True)
    # 好示范的判定必须与语料分层同一份判据（grade_exemplars.page_bad_reasons），
    # 不能在这个脚本里另写一套阈值
    import grade_exemplars as GX
    W = os.environ.get('CAOFENG_WORK', 'CAOFENG_WORK')
    ledger = json.load(io.open(os.path.join(W, 'ooxml_ledger.json'), encoding='utf-8'))
    vm = {}
    vmp = os.path.join(W, 'visual_metrics.jsonl')
    for ln in io.open(vmp, encoding='utf-8'):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        vm[(r['deck'], r['page'])] = r
    good = collections.defaultdict(set)
    for deck, v in ledger.items():
        for s in (v.get('slides') or []):
            txt = ' '.join(s.get('text') or [])
            chars = len(txt.replace(' ', ''))
            m = vm.get((deck, s['n'])) or {}
            zl = m.get('text_zone_lum') or []
            med_lum = (statistics.median(zl) if zl else None)
            max_pt = m.get('max_pt') or 0
            n_ctn = (s.get('timing') or {}).get('n_ctn', 0)
            if not GX.page_bad_reasons(txt, chars, max_pt, med_lum,
                                       bool(s.get('media')), n_ctn):
                good[deck].add(s['n'])
    decks = sorted(glob.glob(os.path.join(src, '*.pptx')))
    rows, scanned = [], 0
    for dp in decks:
        base = os.path.basename(dp)
        want = good.get(base)
        if not want:
            continue
        try:
            z = zipfile.ZipFile(dp)
        except Exception:
            continue
        names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
        for n in names:
            idx = int(re.search(r'(\d+)', n).group(1))
            if idx not in want:
                continue
            try:
                xb = z.read(n)
            except Exception:
                continue
            f = analyze_slide(xb)
            scanned += 1
            if f:
                f.update({'deck': base, 'page': idx})
                rows.append(f)
        z.close()
    if not rows:
        print('停止：好示范页一条都没解析出来', file=sys.stderr)
        sys.exit(1)
    data = [r for r in rows if r['nums'] >= 1]
    n = len(rows), len(data)

    def rate(pred, pool):
        return round(sum(1 for r in pool if pred(r)) / max(1, len(pool)), 4)

    kb = {
        'good_pages_scanned': n[0], 'pages_with_numbers': n[1],
        'hero_number_ge60pt_rate': rate(lambda r: r['hero_pt'] >= 60, rows),
        'hero_number_ge100pt_rate': rate(lambda r: r['hero_pt'] >= 100, rows),
        'hero_pt_median_when_present': (statistics.median([r['hero_pt'] for r in rows if r['hero_pt']])
                                        if any(r['hero_pt'] for r in rows) else 0),
        'bar_group_rate': rate(lambda r: r['bars'] >= 3, rows),
        'cards_ge3_rate': rate(lambda r: r['cards'] >= 3, rows),
        'ellipse_or_donut_rate': rate(lambda r: r['ellipses'] >= 1, rows),
        'connector_rate': rate(lambda r: r['connectors'] >= 1, rows),
        'picture_rate': rate(lambda r: r['pics'] >= 1, rows),
        'real_table_rate': rate(lambda r: r['tbl'] >= 1, rows),
        'chars_p50': statistics.median([r['chars'] for r in rows]),
        'chars_p75': statistics.quantiles([r['chars'] for r in rows], n=4)[2],
        'chars_p90': sorted(r['chars'] for r in rows)[int(len(rows) * 0.9)],
        'examples_hero': [r for r in rows if r['hero_pt'] >= 100][:8],
        'examples_bars': [r for r in rows if r['bars'] >= 3][:8],
        'examples_cards': [r for r in rows if r['cards'] >= 4][:8],
    }
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kb', 'data_design.json')
    json.dump(kb, io.open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('好示范 %d 页（含数字 %d 页）-> %s' % (n[0], n[1], out))
    for k, v in kb.items():
        if not isinstance(v, list):
            print('  %-32s %s' % (k, v))


if __name__ == '__main__':
    main()
