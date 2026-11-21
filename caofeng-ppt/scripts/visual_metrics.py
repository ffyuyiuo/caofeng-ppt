"""Per-page visual metrics over all 2237 rendered slides, joined with the OOXML ledger.

Sampling montages is how a human skims; this is how we claim "no page left unread".
For every exported PNG we measure what the eye would notice, and join it to the shape
positions from the XML so we can state where text actually sits and how dark it is there.

Outputs:
  visual_metrics.jsonl   one row per slide
  visual_study.md        aggregated findings with the numbers the skill cites
"""
import os, io, json, glob, re, collections, statistics, sys


def require_work(w):
    """These study scripts write their products into the work dir. Refuse to invent one: on a
    fresh machine the default may sit on a drive that does not exist, and silently creating
    CAOFENG_WORK there is how a re-run becomes a mystery. Set CAOFENG_WORK to an existing dir.
    """
    if not os.path.isdir(w):
        print("停止：工作目录不存在 %s；先建好它，或用 CAOFENG_WORK 指一个已存在的目录" % w, file=sys.stderr)
        sys.exit(2)
    return os.path.abspath(w)


import corpus_path as CP
SRC = CP.corpus_dir(strict=True)
W = require_work(os.environ.get("CAOFENG_WORK", "CAOFENG_WORK"))
RENDER = os.path.join(W, "render")
EMU_W, EMU_H = 12192000, 6858000

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None



def q(x, lo, hi):
    return max(lo, min(hi, x))


def lum_arr(a):
    r, g, b = a[..., 0] / 255.0, a[..., 1] / 255.0, a[..., 2] / 255.0
    def f(c):
        return np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def colourfulness(a):
    b_gr = a[..., 2].astype(int) - a[..., 1].astype(int)
    rg = (a[..., 0].astype(int) + a[..., 1].astype(int)) / 2.0 - a[..., 2].astype(int)
    return round(float(np.sqrt(b_gr.std() ** 2 + rg.std() ** 2) + 0.3 *
                       np.sqrt(b_gr.mean() ** 2 + rg.mean() ** 2)), 1)


def edges(gray01):
    """Mean absolute gradient on a 0-255 scale. Feeding 0-1 here made every page look 'minimal'."""
    g = gray01 * 255.0
    gx = np.abs(np.diff(g, axis=1)).mean()
    gy = np.abs(np.diff(g, axis=0)).mean()
    return round(float(gx + gy), 2)


def grid_lum(lum):
    """3x3 mean luminance grid — where the dark room for text is."""
    h, w = lum.shape
    out = []
    for i in range(3):
        row = []
        for j in range(3):
            blk = lum[int(h * i / 3):int(h * (i + 1) / 3), int(w * j / 3):int(w * (j + 1) / 3)]
            row.append(round(float(blk.mean()), 3))
        out.append(row)
    return out


def main():
    led = json.load(io.open(os.path.join(W, 'ooxml_ledger.json'), encoding='utf-8'))
    geom = json.load(io.open(os.path.join(W, 'text_geom.json'), encoding='utf-8'))
    rows = []
    n_ok = n_bad = 0
    for deck, v in sorted(led.items()):
        if 'slides' not in v:
            continue
        d = os.path.join(RENDER, deck[:-5].replace('#', '_'))
        for s in v['slides']:
            fp = os.path.join(d, 'p%03d.png' % s['n'])
            if not os.path.exists(fp):
                n_bad += 1
                continue
            try:
                im = Image.open(fp).convert('RGB')
                im.thumbnail((900, 900))
                a = np.asarray(im)
            except Exception:
                n_bad += 1
                continue
            lum = lum_arr(a)
            gray = np.asarray(im.convert('L')) / 255.0   # 0-1; edges() rescales
            txt = ' '.join(s['text'])
            # where the text actually is, from the XML shape boxes
            zl = []
            gpage = next((g for g in geom.get(deck, []) if g['page'] == s['n']), None)
            for sp in (gpage or {}).get('boxes') or []:
                x0 = int(q(sp['x'] / EMU_W, 0, 0.99) * lum.shape[1])
                y0 = int(q(sp['y'] / EMU_H, 0, 0.99) * lum.shape[0])
                x1 = int(q((sp['x'] + sp['w']) / EMU_W, 0.01, 1.0) * lum.shape[1])
                y1 = int(q((sp['y'] + sp['h']) / EMU_H, 0.01, 1.0) * lum.shape[0])
                if x1 - x0 < 6 or y1 - y0 < 6:
                    continue
                crop = lum[y0:y1, x0:x1]
                zl.append(round(float(np.median(crop)), 3))
            row = {
                'deck': deck, 'page': s['n'],
                'mean_lum': round(float(lum.mean()), 3),
                'dark_frac': round(float((lum < 0.18).mean()), 3),
                'bright_frac': round(float((lum > 0.72).mean()), 3),
                'colourfulness': colourfulness(a),
                'edge_energy': edges(gray),
                'grid_lum': grid_lum(lum),
                'text_chars': len(txt),
                'max_pt': max([int(k) for k in s['inv']['pt_sizes']] or [0]),
                'n_text_boxes': len(zl),
                'text_zone_lum': zl,
                'has_media': bool(s['media']),
                'trans': [k['type'] for dd in s['trans']['decls'] for k in dd['kids']],
                'n_ctn': s['timing']['n_ctn'],
            }
            rows.append(row)
            n_ok += 1
    J = io.open(os.path.join(W, 'visual_metrics.jsonl'), 'w', encoding='utf-8')
    for r in rows:
        J.write(json.dumps(r, ensure_ascii=False) + "\n")
    J.close()

    # ---- aggregate findings
    o = io.open(os.path.join(W, 'visual_study.md'), 'w', encoding='utf-8')
    o.write("# 2237 页逐页视觉度量（无抽样）\n\n")
    o.write("读到 %d 页，缺图 %d 页。指标由 `scripts/visual_metrics.py` 在 WPS 导出的 PNG 上实测。\n\n" % (n_ok, n_bad))

    def stat(key, subset=None, f=statistics.median):
        xs = [r[key] for r in (subset or rows) if isinstance(r.get(key), (int, float))]
        return round(f(xs), 3) if xs else None

    covers = [r for r in rows if r['page'] == 1]
    body = [r for r in rows if r['page'] != 1]
    o.write("## 1 明暗分布\n\n")
    o.write("- 全部页：平均亮度中位 %s，暗像素占比中位 %s，亮像素占比中位 %s\n" % (
        stat('mean_lum'), stat('dark_frac'), stat('bright_frac')))
    o.write("- 封面页(n=%d)：平均亮度 %s，暗像素占比 %s\n" % (len(covers), stat('mean_lum', covers), stat('dark_frac', covers)))
    o.write("- 内容页(n=%d)：平均亮度 %s，暗像素占比 %s\n" % (len(body), stat('mean_lum', body), stat('dark_frac', body)))
    dark_decks = [r for r in rows if r['mean_lum'] < 0.25]
    o.write("- 近黑底页占比：%.1f%%（%d/%d）\n" % (100.0 * len(dark_decks) / len(rows), len(dark_decks), len(rows)))

    o.write("\n## 2 文字落点的背景亮度（这是'压暗'是否真的做了的直接证据）\n\n")
    withtxt = [r for r in rows if r['text_zone_lum']]
    allz = [z for r in withtxt for z in r['text_zone_lum']]
    if allz:
        o.write("- 有文本框的页：%d 页，共测 %d 个文本区域\n" % (len(withtxt), len(allz)))
        o.write("- 文本区域背景亮度中位 %s；<=0.25（够暗）占 %.1f%%；>0.55（字容易糊）占 %.1f%%\n" % (
            round(statistics.median(allz), 3),
            100.0 * sum(1 for z in allz if z <= 0.25) / len(allz),
            100.0 * sum(1 for z in allz if z > 0.55) / len(allz)))
        risky = [r for r in withtxt if any(z > 0.55 for z in r['text_zone_lum'])]
        o.write("- 存在'字压亮底'风险的页：%d 页（%.1f%%）\n" % (len(risky), 100.0 * len(risky) / len(rows)))

    o.write("\n## 3 画面复杂度与留白\n\n")
    o.write("- 边缘能量（细节量）中位 %s，色彩丰富度中位 %s\n" % (stat('edge_energy'), stat('colourfulness')))
    for lo, hi, name in ((0, 8, '极简/大留白'), (8, 20, '中等'), (20, 999, '高细节/繁忙')):
        c = sum(1 for r in rows if lo <= r['edge_energy'] < hi)
        o.write("- %s：%d 页（%.1f%%）\n" % (name, c, 100.0 * c / len(rows)))

    o.write("\n## 4 字号与文字量\n\n")
    big = [r for r in rows if r['max_pt'] >= 60]
    o.write("- 有 >=60pt 大字的页：%d 页（%.1f%%）——大字号是常态不是例外\n" % (len(big), 100.0 * len(big) / len(rows)))
    o.write("- 每页字数中位 %s；<=30 字的页占 %.1f%%\n" % (
        stat('text_chars'), 100.0 * sum(1 for r in rows if r['text_chars'] <= 30) / len(rows)))

    o.write("\n## 5 九宫格亮度：文字该放哪\n\n")
    g = [[[0.0] * 3 for _ in range(3)] for _ in range(2)]
    for r in rows:
        for i in range(3):
            for j in range(3):
                g[0][i][j] += r['grid_lum'][i][j]
    cnt = len(rows)
    o.write("- 全册 3x3 平均亮度（行=上中下，列=左中右）：\n\n")
    o.write("  |  | 左 | 中 | 右 |\n|---|---|---|---|\n")
    for i, lab in enumerate(['上', '中', '下']):
        o.write("| %s | %s |\n" % (lab, ' | '.join('%.3f' % (g[0][i][j] / cnt) for j in range(3))))
    darkest = min(((g[0][i][j], i, j) for i in range(3) for j in range(3)))
    o.write("- 最暗的格子=%s（第%d行第%d列，即%s%s），最亮=%s\n" % (
        round(darkest[0] / cnt, 3), darkest[1] + 1, darkest[2] + 1,
        ['上', '中', '下'][darkest[1]], ['左', '中', '右'][darkest[2]],
        round(max(g[0][i][j] for i in range(3) for j in range(3)) / cnt, 3)))

    o.write("\n## 6 媒体与动效在画面上的表现\n\n")
    o.write("- 含内嵌媒体的页：%d 页（%.1f%%）\n" % (
        sum(1 for r in rows if r['has_media']), 100.0 * sum(1 for r in rows if r['has_media']) / len(rows)))
    tc = collections.Counter(t for r in rows for t in r['trans'])
    o.write("- 页面上出现的转场类型次数：%s\n" % tc.most_common(8))
    o.write("- 无文本框且无媒体的'纯视觉页'：%d 页（%.1f%%）\n" % (
        sum(1 for r in rows if r['n_text_boxes'] == 0 and not r['has_media']),
        100.0 * sum(1 for r in rows if r['n_text_boxes'] == 0 and not r['has_media']) / len(rows)))
    o.close()
    print("visual metrics: rows=%d missing=%d" % (n_ok, n_bad))


if __name__ == '__main__':
    main()
