"""One implementation of "where is the ink on this page", shared by the corpus miner and the
deliverable gates.

study_style_anim.py uses it to measure how the 171 templates arrange picture and text;
caofeng.py uses it to check that a finished deck actually matches the layout it declared.
Two copies of that classifier would drift, and the drift would show up as a gate that measures
something other than the number quoted in the docs.
"""
import re, zipfile
import xml.etree.ElementTree as ET

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EMU_W, EMU_H = 12192000, 6858000


def local(t):
    tag = t if isinstance(t, str) else t.tag
    if not isinstance(tag, str):
        return ''
    return tag.split('}')[-1]


def q(ns, tag):
    return "{%s}%s" % (ns, tag)


def num(v):
    return int(v) if v is not None and str(v).lstrip('-').isdigit() else None


def parse(z, name):
    try:
        return ET.fromstring(z.read(name))
    except Exception:
        return None


def slide_names(z):
    names = [i.filename for i in z.infolist()]
    sl = [n for n in names if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    return names, sorted(sl, key=lambda s: int(re.findall(r'\d+', s)[0]))


def slide_size(z, names):
    if 'ppt/presentation.xml' not in names:
        return EMU_W, EMU_H
    px = parse(z, 'ppt/presentation.xml')
    if px is None:
        return EMU_W, EMU_H
    sz = px.find(q(P, 'sldSz'))
    if sz is None or not num(sz.get('cx')) or not num(sz.get('cy')):
        return EMU_W, EMU_H
    return num(sz.get('cx')), num(sz.get('cy'))


def xfrm_raw(el):
    """(off, ext, chOff, chExt) of this element's own a:xfrm, in its parent's space."""
    spPr = el.find(q(P, 'spPr'))
    xf = spPr.find(q(A, 'xfrm')) if spPr is not None else None
    if xf is None:
        return None, None, None, None
    off, ext = xf.find(q(A, 'off')), xf.find(q(A, 'ext'))
    cho, che = xf.find(q(A, 'chOff')), xf.find(q(A, 'chExt'))

    def pt(e):
        return (num(e.get('x')), num(e.get('y'))) if e is not None else None

    def sz(e):
        return (num(e.get('cx')), num(e.get('cy'))) if e is not None else None
    return pt(off), sz(ext), pt(cho), sz(che)


def shape_bbox(el, W_, H_):
    """Own a:xfrm as page fractions (used on slideLayout/slideMaster placeholders)."""
    spPr = el.find(q(P, 'spPr'))
    xf = spPr.find(q(A, 'xfrm')) if spPr is not None else None
    if xf is None:
        return None
    off, ext = xf.find(q(A, 'off')), xf.find(q(A, 'ext'))
    if off is None or ext is None:
        return None
    x, y, cx, cy = num(off.get('x')), num(off.get('y')), num(ext.get('cx')), num(ext.get('cy'))
    if None in (x, y, cx, cy) or cx <= 0 or cy <= 0:
        return None
    return [x / float(W_), y / float(H_), cx / float(W_), cy / float(H_)]


def _local_bbox(el):
    """Own spPr/a:xfrm, else the first a:xfrm below it (tables keep theirs inside graphicData)."""
    spPr = el.find(q(P, 'spPr'))
    xf = spPr.find(q(A, 'xfrm')) if spPr is not None else None
    if xf is None:
        xf = el.find('.//' + q(A, 'xfrm'))
    if xf is None:
        return None
    off, ext = xf.find(q(A, 'off')), xf.find(q(A, 'ext'))
    if off is None or ext is None:
        return None
    x, y, cx, cy = num(off.get('x')), num(off.get('y')), num(ext.get('cx')), num(ext.get('cy'))
    if None in (x, y, cx, cy):
        return None
    return [x, y, max(cx, 1), max(cy, 1)]


def _shape_facts(el, box, depth):
    lt = local(el)
    txt = [(t.text or '').strip() for t in el.iter(q(A, 't'))]
    txt = [t for t in txt if t]
    szs = [num(r.get('sz')) // 100 for r in el.iter(q(A, 'rPr')) if num(r.get('sz'))]
    f = {'kind': lt, 'box': box, 'depth': depth,
         'chars': sum(len(t) for t in txt), 'text': ' '.join(txt)[:80],
         'max_pt': max(szs) if szs else 0}
    if lt == 'pic':
        bl = el.find('.//' + q(A, 'blip'))
        f['pic'] = True
        f['embed'] = bl.get(q(R, 'embed')) if bl is not None else None
        sr = el.find('.//' + q(A, 'srcRect'))
        if sr is not None:
            f['srcRect'] = [num(sr.get(k)) for k in ('l', 't', 'r', 'b')]
        xf = el.find(q(P, 'spPr') + '/' + q(A, 'xfrm'))
        f['rot'] = xf.get('rot') if xf is not None else None
    geom = el.find('.//' + q(A, 'prstGeom'))
    f['prst'] = geom.get('prst') if geom is not None else None
    f['custGeom'] = el.find('.//' + q(A, 'custGeom')) is not None
    eff = el.find('.//' + q(A, 'effectLst'))
    f['effects'] = sorted({local(c) for c in eff}) if eff is not None else []
    f['sp3d'] = el.find('.//' + q(A, 'sp3d')) is not None
    return f


def flatten_tree(el, tf, W_, H_, out, depth=0):
    """Depth-first with an affine map from the current child-space to the page.

    Group children live in a scaled space (off/ext vs chOff/chExt); without the map a picture
    inside a group reports coordinates that have nothing to do with where it is drawn, and the
    corpus has 3312 groups.
    """
    for ch in el:
        if not isinstance(ch.tag, str):
            continue
        lt = local(ch)
        if lt.startswith('nv') or lt in ('extLst', 'txBody'):
            continue
        if lt == 'grpSp':
            off, ext, cho, che = xfrm_raw(ch)
            if None in (off, ext, cho, che) or che[0] <= 0 or che[1] <= 0:
                flatten_tree(ch, tf, W_, H_, out, depth + 1)
                continue
            sx, sy = ext[0] / float(che[0]), ext[1] / float(che[1])

            def inner(x, y, off=off, cho=cho, sx=sx, sy=sy):
                return (off[0] + (x - cho[0]) * sx, off[1] + (y - cho[1]) * sy)

            def compose(x, y, tf=tf, inner=inner):
                return tf(*inner(x, y))
            flatten_tree(ch, compose, W_, H_, out, depth + 1)
            continue
        if lt not in ('sp', 'pic', 'cxnSp', 'graphicFrame'):
            continue
        box = _local_bbox(ch)
        if box is None:
            out.append(_shape_facts(ch, None, depth))
            continue
        x0, y0 = tf(box[0], box[1])
        x1, y1 = tf(box[0] + box[2], box[1] + box[3])
        out.append(_shape_facts(ch, [x0 / float(W_), y0 / float(H_),
                                     max(0.0, x1 - x0) / float(W_),
                                     max(0.0, y1 - y0) / float(H_)], depth))


def page_shapes(sroot, W_, H_):
    cSld = sroot.find(q(P, 'cSld'))
    tree = cSld.find(q(P, 'spTree')) if cSld is not None else None
    if tree is None:
        return []
    out = []
    flatten_tree(tree, lambda x, y: (x, y), W_, H_, out)
    for i, s in enumerate(out):
        s['z'] = i
    return out


def inter_frac(a, b):
    ix = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    iy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    return (ix * iy) / float(a[2] * a[3] or 1e-9)


def text_zones(txts, pics):
    zones = []
    for s in txts:
        b = s.get('box')
        if not b:
            continue
        cx, cy = b[0] + b[2] / 2.0, b[1] + b[3] / 2.0
        ov = max([inter_frac(b, p['box']) for p in pics if p.get('box')], default=0.0)
        zones.append({'box': [round(v, 4) for v in b], 'row': int(min(2, max(0, cy * 3))),
                      'col': int(min(2, max(0, cx * 3))), 'pt': s['max_pt'],
                      'chars': s['chars'], 'over_pic': ov >= 0.35, 'over_frac': round(ov, 3)})
    return zones


def similar(boxes, tol=0.35):
    if len(boxes) < 3:
        return False
    ws, hs = [b[2] for b in boxes], [b[3] for b in boxes]
    mw, mh = _mean(ws), _mean(hs)
    return mw > 0 and mh > 0 and (max(ws) - min(ws)) / mw < tol and (max(hs) - min(hs)) / mh < tol


def _mean(xs):
    return sum(xs) / float(len(xs)) if xs else 0.0


def nodes_on_line(boxes, n=3):
    """>=n same-size tiles whose centres are roughly collinear -> a guide-line sequence."""
    if len(boxes) < n:
        return False
    cs = sorted([(b[0] + b[2] / 2.0, b[1] + b[3] / 2.0) for b in boxes])
    (x0, y0), (x1, y1) = cs[0], cs[-1]
    dx, dy = x1 - x0, y1 - y0
    L = (dx * dx + dy * dy) ** 0.5
    if L < 0.25:
        return False
    return max(abs(dy * (x - x0) - dx * (y - y0)) / L for x, y in cs) < 0.06


def classify_page(shapes, bg_pic=False):
    """Text/image arrangement archetype, decided by measured bboxes only."""
    pics = [s for s in shapes if s.get('pic') and s.get('box')]
    txts = [s for s in shapes if s.get('chars', 0) > 0]
    if not txts:
        return 'pure-visual', []
    if not any(s.get('box') for s in txts):
        return 'text-from-layout-placeholder', []
    bigs = sorted([p for p in pics if p['box'][2] * p['box'][3] >= 0.90], key=lambda s: -s['z'])
    if bigs:
        return 'full-bleed', text_zones(txts, bigs)
    if len(pics) >= 6:
        return 'photo-wall', text_zones(txts, pics)
    if len(pics) >= 3 and similar([p['box'] for p in pics]):
        kind = 'timeline-on-line' if nodes_on_line([p['box'] for p in pics]) else 'card-grid'
        return kind, text_zones(txts, pics)
    if pics:
        hero = sorted(pics, key=lambda s: -(s['box'][2] * s['box'][3]))[0]
        b = hero['box']
        if b[3] >= 0.55 and b[2] >= 0.80:
            side = 'top' if b[1] < 0.30 else ('bottom' if b[1] > 0.40 else 'band')
            return 'stack-%s-image' % side, text_zones(txts, [hero])
        if b[2] >= 0.34 and b[3] >= 0.40:
            if b[0] + b[2] <= 0.55:
                side = 'left'
            elif b[0] >= 0.45:
                side = 'right'
            else:
                side = 'centre'
            return 'split-%s-image' % side, text_zones(txts, [hero])
        if b[2] < 0.22 and b[3] < 0.22:
            return 'icon-accent', text_zones(txts, [hero])
        return 'pic-inset', text_zones(txts, [hero])
    if bg_pic:
        return 'bg-blip-text-only', text_zones(txts, [])
    bigtxt = [s for s in txts if s.get('box') and s['max_pt'] >= 60]
    if len(txts) <= 3 and bigtxt:
        c = bigtxt[0]['box']
        if 0.25 <= c[0] + c[2] / 2.0 <= 0.75:
            return 'centred-statement', text_zones(txts, [])
    if len([s for s in txts if s.get('box')]) >= 4 and \
            similar([s['box'] for s in txts if s.get('box')]):
        return 'text-grid', text_zones(txts, [])
    return 'text-block', text_zones(txts, [])


def bg_is_picture(sroot):
    cSld = sroot.find(q(P, 'cSld'))
    bg = cSld.find(q(P, 'bg')) if cSld is not None else None
    return bg is not None and bg.find('.//' + q(A, 'blip')) is not None


def measure_deck(pptx_path):
    """Per page: shapes, archetype, text zones. Used on the finished deck by the gates."""
    z = zipfile.ZipFile(pptx_path)
    names, sl = slide_names(z)
    W_, H_ = slide_size(z, names)
    out = []
    for sn in sl:
        sroot = parse(z, sn)
        if sroot is None:
            out.append({'page': int(re.findall(r'\d+', sn)[0]), 'error': 'unparsable',
                        'archetype': 'unparsable', 'shapes': [], 'zones': []})
            continue
        shapes = page_shapes(sroot, W_, H_)
        atype, zones = classify_page(shapes, bg_is_picture(sroot))
        out.append({'page': int(re.findall(r'\d+', sn)[0]), 'shapes': shapes,
                    'archetype': atype, 'zones': zones,
                    'bg_pic': bg_is_picture(sroot),
                    'pics': [s['box'] for s in shapes if s.get('pic') and s.get('box')],
                    'max_pt': max([s['max_pt'] for s in shapes] or [0]),
                    'chars': sum(s['chars'] for s in shapes)})
    z.close()
    return out


def median_box(rows):
    if not rows:
        return None
    return [round(sorted(r[i] for r in rows)[len(rows) // 2], 4) for i in range(4)]
