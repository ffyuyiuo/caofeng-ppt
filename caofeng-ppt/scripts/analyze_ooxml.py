"""Extract the technique ledger from every .pptx: transitions, animation timing trees,
motion paths, backgrounds, shape/effect inventory, typography, theme tokens."""
import os, re, json, io, sys, zipfile, collections
import xml.etree.ElementTree as ET


def require_work(w):
    """These study scripts write their products into the work dir. Refuse to invent one: on a
    fresh machine the default may sit on a drive that does not exist, and silently creating
    CAOFENG_WORK there is how a re-run becomes a mystery. Set CAOFENG_WORK to an existing dir.
    """
    if not os.path.isdir(w):
        print("停止：工作目录不存在 %s；先建好它，或用 CAOFENG_WORK 指一个已存在的目录" % w, file=sys.stderr)
        sys.exit(2)
    return os.path.abspath(w)

ROOT = os.path.abspath(os.path.dirname(__file__))
import corpus_path as CP
SRC = CP.corpus_dir(strict=True)
W = require_work(os.environ.get("CAOFENG_WORK", "CAOFENG_WORK"))
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P14 = "http://schemas.microsoft.com/office/powerpoint/2010/main"


def q(ns, tag):
    return "{%s}%s" % (ns, tag)


def local(tag):
    return tag.split('}')[-1]


def walk_names(z):
    return [i.filename for i in z.infolist()]


def slide_order(names):
    sl = [n for n in names if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    return sorted(sl, key=lambda s: int(re.findall(r'\d+', s)[0]))


def parse(z, name):
    try:
        return ET.fromstring(z.read(name))
    except Exception:
        return None


def ns_of(tag):
    return tag.split('}')[0][1:] if tag.startswith('{') else ''


def grab_transition(sroot):
    """Every element named 'transition' anywhere in the slide, incl. the p14/p159 morph form."""
    out = []
    if sroot is None:
        return out
    for el in sroot.iter():
        if local(el.tag) != 'transition':
            continue
        attrs = {k.split('}')[-1]: v for k, v in el.attrib.items()}
        kids = []
        for c in el:
            d = {'type': local(c.tag), 'ns': ns_of(c.tag),
                 'attrs': {k.split('}')[-1]: v for k, v in c.attrib.items()}}
            kids.append(d)
        # is this declaration wrapped in mc:AlternateContent (morph + fallback)?
        out.append({'ns': ns_of(el.tag), 'attrs': attrs, 'kids': kids})
    # detect AlternateContent-wrapped transitions and record the fallback pair
    alt = []
    for ac in sroot.iter() if sroot is not None else []:
        if local(ac.tag) != 'AlternateContent':
            continue
        parts = {}
        for ch in ac:
            req = ch.get('Requires') or local(ch.tag)
            inner = [local(e.tag) for e in ch.iter() if local(e.tag) in
                     ('transition', 'morph', 'fade', 'push', 'wipe', 'zoom', 'strips', 'cut', 'cover', 'randomBar', 'flash', 'ripple', 'dissolve', 'newsflash', 'origami', 'morphByObject', 'prst')]
            parts[req] = inner
        if parts:
            alt.append(parts)
    return {'decls': out, 'alternate': alt}


PRIMS = ('animMotion', 'animEffect', 'animScale', 'animRotate', 'anim', 'set', 'video', 'audio', 'cmd')


def walk_timing(node, acc):
    """Full-tree descent over the p:timing subtree; classify every cTn and effect primitive."""
    for el in node:
        lt = local(el.tag)
        if lt == 'cTn':
            info = {'presetClass': el.get('presetClass'), 'presetID': el.get('presetID'),
                    'presetSubtype': el.get('presetSubtype'), 'nodeType': el.get('nodeType'),
                    'dur': el.get('dur'), 'fill': el.get('fill'), 'delay': None, 'autoRev': el.get('autoRev')}
            st = el.find(q(P, 'stCondLst'))
            if st is not None:
                for cs in st.iter(q(P, 'cond')):
                    info['delay'] = cs.get('delay')
            acc.append(info)
        elif lt in PRIMS:
            tgt = [t.get('spid') for t in el.iter(q(P, 'spTgt'))]
            vals = [v.get('fmla') for v in el.iter(q(A, 'stringVal'))] + \
                   [v.get('val') for v in el.iter(q(A, 'intVal'))] + \
                   [v.get('fmla') for v in el.iter(q(A, 'intVal'))]
            acc.append({'_primitive': lt, 'path': (el.get('path') or '')[:160],
                        'transition': el.get('transition'), 'attrName': el.get('attrName'),
                        'cmdtype': el.get('cmdtype'), 'dur': el.get('dur'),
                        'spid': tgt[:4], 'values': vals[:4],
                        'filter': el.get('filter')})
        walk_timing(el, acc)


def timing_summary(sroot):
    acc = []
    tl = sroot.find(q(P, 'timing')) if sroot is not None else None
    if tl is None:
        return {'n_ctn': 0, 'prims': {}, 'classes': {}, 'combos': {}, 'n_targets': 0, 'paths': [],
                'nodeTypes': {}, 'dur_min': None, 'dur_max': None, 'dur_med': None,
                'delays': {}, 'filters': {}, 'cmds': {}}
    walk_timing(tl, acc)
    ctns = [x for x in acc if 'presetClass' in x]
    classes = collections.Counter(x.get('presetClass') for x in ctns if x.get('presetClass'))
    prims = collections.Counter(x['_primitive'] for x in acc if '_primitive' in x)
    durs = [int(x['dur']) for x in acc + ctns if x.get('dur') and str(x['dur']).isdigit()]
    paths = [x['path'] for x in acc if x.get('path')]
    combos = collections.Counter("%s:%s:%s" % (x.get('presetClass'), x.get('presetID'), x.get('presetSubtype'))
                                 for x in ctns if x.get('presetID'))
    nodelay = collections.Counter(x.get('delay') for x in ctns if x.get('nodeType'))
    return {'n_ctn': len(ctns), 'classes': dict(classes), 'prims': dict(prims), 'paths': paths[:6],
            'combos': dict(combos), 'n_targets': len({s for x in acc if '_primitive' in x for s in (x.get('spid') or [])}),
            'nodeTypes': dict(collections.Counter(x.get('nodeType') for x in ctns if x.get('nodeType'))),
            'dur_min': min(durs) if durs else None, 'dur_max': max(durs) if durs else None,
            'dur_med': sorted(durs)[len(durs) // 2] if durs else None,
            'delays': dict(nodelay.most_common(8)),
            'filters': dict(collections.Counter(x['filter'] for x in acc if x.get('filter')).most_common(6)),
            'cmds': dict(collections.Counter(x['cmdtype'] for x in acc if x.get('cmdtype')).most_common(8))}


def shape_inventory(sroot):
    inv = collections.Counter()
    effects = collections.Counter()
    grad_stops = []
    alphas = collections.Counter()
    fonts = collections.Counter()
    sizes = collections.Counter()
    bold = 0
    colors = collections.Counter()
    srcrect = 0
    for el in sroot.iter():
        lt = local(el.tag)
        if lt in ('sp', 'pic', 'grpSp', 'cxnSp', 'graphicFrame', 'tbl'):
            inv[lt] += 1
        elif lt == 'prstGeom':
            inv['prst:' + (el.get('prst') or '?')] += 1
        elif lt == 'custGeom':
            inv['custGeom'] += 1
        elif lt in ('blur', 'softEdge', 'glow', 'reflection', 'bevelT', 'shadow', 'innerShadow',
                    'sp3d', 'scene3d', 'duotone', 'grayscale', 'biLevel', 'tint', 'lumMod', 'satMod'):
            effects[lt] += 1
        elif lt == 'gs':
            try:
                grad_stops.append(int(el.get('pos')))
            except Exception:
                pass
        elif lt == 'alpha':
            alphas[el.get('val')] += 1
        elif lt == 'srcRect':
            srcrect += 1
        elif lt == 'latin':
            if el.get('typeface'):
                fonts[el.get('typeface')] += 1
        elif lt in ('solidFill',) and el.find(q(A, 'srgbClr')) is not None:
            colors[el.find(q(A, 'srgbClr')).get('val')] += 1
        elif lt == 'rPr':
            if el.get('sz'):
                sizes[int(el.get('sz')) // 100] += 1
            if el.get('b') == '1':
                bold += 1
    return {'shapes': dict(inv), 'effects': dict(effects), 'alpha_vals': dict(alphas),
            'grad_stop_count': len(grad_stops), 'srcRect_crops': srcrect,
            'fonts': dict(fonts), 'pt_sizes': dict(sizes), 'bold_runs': bold,
            'top_colors': dict(colors.most_common(8))}


def bg_of(sroot):
    cSld = sroot.find(q(P, 'cSld')) if sroot is not None else None
    if cSld is None:
        return None
    bg = cSld.find(q(P, 'bg'))
    if bg is None:
        return None
    kinds = []
    for pr in bg.iter():
        lt = local(pr.tag)
        if lt in ('blipFill', 'gradFill', 'solidFill', 'patternFill', 'patt'):
            kinds.append(lt)
    return kinds or ['bg']


def theme_of(z, names):
    th = [n for n in names if re.match(r'ppt/theme/theme\d+\.xml$', n)]
    cols, fonts = [], {}
    for t in th[:1]:
        x = parse(z, t)
        if x is None:
            continue
        scheme = x.find('.//' + q(A, 'clrScheme'))
        if scheme is not None:
            for c in scheme:
                v = c.find(q(A, 'srgbClr'))
                sv = c.find(q(A, 'sysClr'))
                cols.append((local(c.tag), v.get('val') if v is not None else (sv.get('lastClr') if sv is not None else None)))
        fs = x.find('.//' + q(A, 'fontScheme'))
        if fs is not None:
            for kind in ('majorFont', 'minorFont'):
                f = fs.find(q(A, kind))
                if f is not None:
                    lat = f.find(q(A, 'latin'))
                    ea = f.find(q(A, 'ea'))
                    fonts[kind] = (lat.get('typeface') if lat is not None else None,
                                   ea.get('typeface') if ea is not None else None)
    return cols, fonts


def main():
    os.makedirs(W, exist_ok=True)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    files = sorted(f for f in os.listdir(SRC) if f.lower().endswith('.pptx'))
    if limit:
        files = files[:limit]
    ledger = {}
    for i, f in enumerate(files, 1):
        p = os.path.join(SRC, f)
        try:
            z = zipfile.ZipFile(p)
        except Exception as e:
            ledger[f] = {'error': str(e)[:120]}
            continue
        names = walk_names(z)
        cols, fonts = theme_of(z, names)
        sldsz = None
        if 'ppt/presentation.xml' in names:
            px = parse(z, 'ppt/presentation.xml')
            if px is not None:
                sz = px.find(q(P, 'sldSz'))
                if sz is not None:
                    sldsz = {'cx': int(sz.get('cx') or 0), 'cy': int(sz.get('cy') or 0),
                             'type': sz.get('type')}
        slides = []
        for sn in slide_order(names):
            sroot = parse(z, sn)
            if sroot is None:
                continue
            txt = []
            for t in sroot.iter(q(A, 't')):
                s = (t.text or '').strip()
                if s:
                    txt.append(s)
            rels = sn.replace('slides/', 'slides/_rels/') + '.rels'
            media = []
            if rels in names:
                rx = parse(z, rels)
                if rx is not None:
                    media = [e.get('Target').split('/')[-1] for e in rx
                             if (e.get('Target') or '').endswith(('.mp4', '.wmv', '.avi', '.gif', '.mp3', '.wav', '.m4a'))]
            slides.append({
                'n': int(re.findall(r'\d+', sn)[0]),
                'text': txt,
                'trans': grab_transition(sroot),
                'timing': timing_summary(sroot),
                'inv': shape_inventory(sroot),
                'bg': bg_of(sroot),
                'media': media,
            })
        ledger[f] = {'slides': slides, 'theme_colors': cols, 'theme_fonts': fonts,
                     'n_layouts': len([n for n in names if n.startswith('ppt/slideLayouts/slideLayout')]),
                     'size': os.path.getsize(p)}
        if i % 20 == 0:
            print("processed %d/%d" % (i, len(files)), flush=True)
        z.close()
    json.dump(ledger, io.open(os.path.join(W, 'ooxml_ledger.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print("ledger written:", len(ledger))


if __name__ == '__main__':
    main()
