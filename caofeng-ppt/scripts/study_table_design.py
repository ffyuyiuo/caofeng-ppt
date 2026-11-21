# -*- coding: utf-8 -*-
"""挖掘语料里表格的真实做法，产出 kb/table_design.json 的可执行参数。

为什么要单独挖：装配器过去只是把 Word 的行列原样画进 <a:tbl>，
语料里的表格从来不是这么用的——表头是一条色带、数字列右对齐、
关键数字被抽到表外用超大字号压场、下面还有一行小字说明口径。
这些都是可以量出来的事实，不该靠猜。

用法：python study_table_design.py [--out kb/table_design.json] [--max-decks N]
"""
import os, sys, json, io, re, glob, zipfile, collections, statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_path as CP

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
EMU_W, EMU_H = 12192000, 6858000
NUM = re.compile(r'^\s*[-+]?[\d,.]+\s*(%|％|万|亿|元|台|例|人|天|城|店|个|倍|:1)?\s*$')


def local(tag):
    return tag.rpartition('}')[2] if isinstance(tag, str) else ''


def cell_facts(tc):
    """一个单元格：字号集合、是否加粗、填充色、水平对齐、文本。"""
    sizes, bolds, colors = [], [], []
    aligns = []
    txt = []
    for r in tc.iter(A + 'r'):
        rPr = r.find(A + 'rPr')
        t = (r.find(A + 't').text or '') if r.find(A + 't') is not None else ''
        txt.append(t)
        if rPr is not None:
            sz = rPr.get('sz')
            if sz and sz.isdigit():
                sizes.append(int(sz) / 100.0)
            if rPr.get('b') == '1':
                bolds.append(True)
            fill = rPr.find(A + 'solidFill')
            if fill is not None:
                srgb = fill.find(A + 'srgbClr')
                if srgb is not None:
                    colors.append(srgb.get('val'))
        pPr = r.find('../' + A + 'pPr')
    for pPr in tc.iter(A + 'pPr'):
        if pPr.get('algn'):
            aligns.append(pPr.get('algn'))
    fill = None
    tcPr = tc.find(A + 'tcPr')
    if tcPr is not None:
        sf = tcPr.find(A + 'solidFill')
        if sf is not None and sf.find(A + 'srgbClr') is not None:
            fill = sf.find(A + 'srgbClr').get('val')
        elif sf is not None and sf.find(A + 'schemeClr') is not None:
            fill = 'scheme:' + sf.find(A + 'schemeClr').get('val')
    return {'text': ''.join(txt).strip(), 'sizes': sizes, 'bold': bool(bolds),
            'font_colors': colors, 'fill': fill, 'aligns': aligns}


def analyze_table(tbl, slide_root):
    rows = tbl.findall(A + 'tr')
    if not rows:
        return None
    grid = tbl.find(A + 'tblGrid')
    ncols = len(grid.findall(A + 'gridCol')) if grid is not None else 0
    row_h = [int(r.get('h') or 0) for r in rows]
    cells_rows = []
    for r in rows:
        cells_rows.append([cell_facts(tc) for tc in r.findall(A + 'tc')])
    hdr = cells_rows[0]
    body = [r for r in cells_rows[1:]]
    hdr_fills = [c['fill'] for c in hdr if c['fill']]
    hdr_sizes = [s for c in hdr for s in c['sizes']]
    body_sizes = [s for r in body for c in r for s in c['sizes']]
    body_fills = collections.Counter(c['fill'] for r in body for c in r if c['fill'])
    bold_cells = sum(1 for r in body for c in r if c['bold'])
    # 数字列：按列统计"这列多数单元格是数字"
    num_cols = []
    for ci in range(max(1, ncols)):
        col = [r[ci] for r in body if ci < len(r)]
        if col and sum(1 for c in col if NUM.match(c['text'] or '')) >= len(col) * 0.6:
            num_cols.append(ci)
    align_right = sum(1 for r in body for c in r if 'r' in c['aligns'] or 'end' in c['aligns'])
    align_ctr = sum(1 for r in body for c in r if any(a in ('ctr', 'center') for a in c['aligns']))
    # 表外：这一页有没有大字（≥60pt）与来源小字（≤12pt 且含"来源/数据/口径"）
    big_nums, src_notes = [], []
    for sp in slide_root.iter(P + 'sp'):
        t = ''.join((x.text or '') for x in sp.iter(A + 't')).strip()
        szs = [int(v) / 100.0 for v in
               [e.get('sz') for e in sp.iter() if local(e.tag) in ('rPr', 'defRPr')]
               if (v or '').isdigit()]
        if not t or not szs:
            continue
        mx = max(szs)
        if mx >= 60 and re.search(r'\d', t) and len(t) <= 18:
            big_nums.append({'text': t[:18], 'pt': mx})
        if mx <= 13 and re.search(u'来源|数据|口径|出处|注：', t):
            src_notes.append(t[:60])
    return {'rows': len(rows), 'cols': ncols,
            'header_fills': hdr_fills[:4], 'header_sizes': sorted(hdr_sizes)[:6],
            'header_bold': all(c['bold'] for c in hdr if c['text']),
            'body_sizes': sorted(body_sizes)[:8], 'body_fill_top': body_fills.most_common(4),
            'body_cells': sum(len(r) for r in body), 'bold_cells': bold_cells,
            'num_cols': num_cols, 'align_right_cells': align_right, 'align_ctr_cells': align_ctr,
            'row_h_median': statistics.median(row_h) if row_h else 0,
            'hero_numbers_outside': big_nums[:4], 'source_note': src_notes[:1]}


def main():
    ap = sys.argv
    src = CP.corpus_dir(strict=True)
    decks = sorted(glob.glob(os.path.join(src, '*.pptx')))
    out_pages, per_deck = [], []
    for dp in decks:
        try:
            z = zipfile.ZipFile(dp)
        except Exception:
            continue
        names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
        found = 0
        for n in names:
            try:
                x = z.read(n)
            except Exception:
                continue
            if b'<a:tbl>' not in x and b'{%s}tbl' % A.encode('utf8') not in x:
                continue
            try:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(x)
            except Exception:
                continue
            for tbl in root.iter(A + 'tbl'):
                f = analyze_table(tbl, root)
                if f:
                    f['deck'] = os.path.basename(dp)
                    f['slide'] = int(re.search(r'(\d+)', n).group(1))
                    out_pages.append(f)
                    found += 1
        if found:
            per_deck.append(os.path.basename(dp))
        z.close()
    n = len(out_pages)
    if not n:
        print('停止：语料里一张表格页都没找到（路径或解析有问题）', file=sys.stderr)
        sys.exit(1)
    hdr_fill_rate = sum(1 for p in out_pages if p['header_fills']) / n
    hdr_bold_rate = sum(1 for p in out_pages if p['header_bold']) / n
    band_rate = sum(1 for p in out_pages if len(p['body_fill_top']) >= 2) / n
    hero_rate = sum(1 for p in out_pages if p['hero_numbers_outside']) / n
    src_rate = sum(1 for p in out_pages if p['source_note']) / n
    num_col_rate = sum(1 for p in out_pages if p['num_cols']) / n
    kb = {
        'n_table_pages': n, 'n_decks_with_tables': len(per_deck),
        'share_of_good_exemplars_pct': None,
        'header_fill_rate': round(hdr_fill_rate, 3),
        'header_bold_rate': round(hdr_bold_rate, 3),
        'row_banding_rate': round(band_rate, 3),
        'numeric_column_rate': round(num_col_rate, 3),
        'hero_number_outside_table_rate': round(hero_rate, 3),
        'source_note_rate': round(src_rate, 3),
        'header_pt_median': statistics.median([max(p['header_sizes'] or [0]) for p in out_pages]),
        'body_pt_median': statistics.median([statistics.median(p['body_sizes'] or [0])
                                             for p in out_pages if p['body_sizes']]),
        'rows_median': statistics.median([p['rows'] for p in out_pages]),
        'cols_median': statistics.median([p['cols'] for p in out_pages]),
        'row_h_emu_median': statistics.median([p['row_h_median'] for p in out_pages]),
        'bold_cell_share': round(sum(p['bold_cells'] for p in out_pages) /
                                 max(1, sum(p['body_cells'] for p in out_pages)), 4),
        'right_align_cells_per_page': round(sum(p['align_right_cells'] for p in out_pages) / n, 2),
        'center_align_cells_per_page': round(sum(p['align_ctr_cells'] for p in out_pages) / n, 2),
        'examples': out_pages[:12],
        'decks_with_tables_sample': per_deck[:20],
    }
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kb', 'table_design.json')
    json.dump(kb, io.open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kb',
                         'table_pages_full.json'), 'w', encoding='utf-8').write(
        '\n'.join(json.dumps(p, ensure_ascii=False) for p in out_pages))
    print('表格页 %d 张（来自 %d 套模板）-> %s' % (n, len(per_deck), out))
    for k in ('header_fill_rate', 'header_bold_rate', 'row_banding_rate', 'numeric_column_rate',
              'hero_number_outside_table_rate', 'source_note_rate', 'header_pt_median',
              'body_pt_median', 'rows_median', 'cols_median', 'bold_cell_share',
              'right_align_cells_per_page'):
        print('  %-32s %s' % (k, kb[k]))


if __name__ == '__main__':
    main()
