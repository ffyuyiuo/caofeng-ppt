"""Mine the three layers the first pass never consumed: colour system, layout geometry,
animation mechanics.

analyze_ooxml.py recorded theme colours and per-slide colour counts, but nothing downstream ever
read them, and picture bounding boxes were never related to text bounding boxes at all. This
script opens the corpus a second time and writes three artifacts:

  color_system.json        theme palettes + colour-by-role + gradients/dimming tokens + fonts
  layout_archetypes.json   picture<->text bbox archetypes with measured shares and geometry
  animation_mechanics.json preset triples with the XML signature that proves each one

Every number is a count or a median of what the XML contains. Effect *names* are only attached
where the XML itself proves them (a:animEffect/@filter, a:prstGeom/@prst, p:animMotion/@path);
a bare presetID is reported as a class:id:subtype triple, never guessed into a name.
"""
import os, re, io, sys, json, zipfile, statistics, collections
import xml.etree.ElementTree as ET

ROOT = os.path.abspath(os.path.dirname(__file__))
SKILL = os.path.abspath(os.path.join(ROOT, '..'))
import corpus_path as CP
SRC = CP.corpus_dir(strict=True)


def require_dir(d, what):
    if not os.path.isdir(d):
        print("STOP: %s missing -> %s (set CAOFENG_WORK/CAOFENG_SRC)" % (what, d), file=sys.stderr)
        sys.exit(2)
    return os.path.abspath(d)


W = require_dir(os.environ.get("CAOFENG_WORK", "CAOFENG_WORK"), "work dir")

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EMU_W, EMU_H = 12192000, 6858000


def local(t):
    """QName -> local name; also accepts an element so call sites stay short."""
    tag = t if isinstance(t, str) else t.tag
    if not isinstance(tag, str):        # comments / processing instructions
        return ''
    return tag.split('}')[-1]


def q(ns, tag):
    return "{%s}%s" % (ns, tag)


def parse(z, name):
    try:
        return ET.fromstring(z.read(name))
    except Exception:
        return None


def slide_order(names):
    sl = [n for n in names if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    return sorted(sl, key=lambda s: int(re.findall(r'\d+', s)[0]))


def num(v):
    return int(v) if v is not None and str(v).lstrip('-').isdigit() else None


def lum(rgb):
    if not rgb or len(rgb) != 6:
        return None
    try:
        ch = [int(rgb[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return None
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(ch[0]) + 0.7152 * f(ch[1]) + 0.0722 * f(ch[2])


def hue(rgb):
    if not rgb or len(rgb) != 6:
        return None
    try:
        r, g, b = [int(rgb[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return None
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == mn:
        return None
    d = mx - mn
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60.0


def chroma(rgb):
    """Max-minus-normalised-channel: 0 for greys, 1 for a fully saturated colour."""
    if not rgb or len(rgb) != 6:
        return 0.0
    try:
        ch = [int(rgb[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return 0.0
    return max(ch) - min(ch)


def hue_band(h):
    """Named bands; boundaries chosen from where the corpus colours actually cluster."""
    if h is None:
        return 'neutral/grey'
    if h < 15 or h >= 345:
        return 'red'
    if h < 40:
        return 'orange/amber'
    if h < 70:
        return 'yellow/gold'
    if h < 165:
        return 'green'
    if h < 200:
        return 'cyan'
    if h < 255:
        return 'blue'
    if h < 290:
        return 'violet'
    return 'magenta'


MODS = ('tint', 'shade', 'sat', 'satMod', 'satOff', 'lum', 'lumMod', 'lumOff', 'alpha',
        'alphaMod', 'alphaOff', 'hue', 'hueMod', 'red', 'green', 'blue')


# =============== 1. colour =====================================================================

def parent_map(root):
    pm = {}
    for el in root.iter():
        for ch in el:
            pm[id(ch)] = el
    return pm


def role_of(el, pm):
    """Which visual role a colour plays, from its real ancestors."""
    chain = []
    cur = el
    while cur is not None and len(chain) < 8:
        chain.append(local(cur.tag))
        cur = pm.get(id(cur))
    s = set(chain)
    if 'ln' in chain[:3]:
        return 'line'
    if 'rPr' in chain[:4] or 'defRPr' in chain[:4] or 'endParaRPr' in chain[:4]:
        return 'text'
    if 'gs' in chain[:3]:
        return 'grad'
    if 'bg' in chain:
        return 'bg'
    if 'duotone' in chain[:4]:
        return 'duotone'
    if 'blip' in chain[:4]:
        return 'piccolor'
    if 'effectLst' in chain[:5]:
        return 'effect'
    if 'sp3d' in chain or 'scene3d' in chain:
        return '3d'
    if 'nvGrpSpPr' in chain or 'grpSpPr' in chain[:2]:
        return 'other'
    return 'fill'


def scan_colours(sroot, acc, deck_c=None):
    """Count every colour by (role, kind) plus every colour-modifier token wrapped around it."""
    pm = parent_map(sroot)
    for el in sroot.iter():
        lt = local(el)
        if lt == 'srgbClr':
            if not el.get('val'):
                continue
            role = role_of(el, pm)
            acc['colors']["%s|%s" % (role, el.get('val'))] += 1
            acc['by_role'][role] += 1
            acc['kind']['srgbClr'] += 1
            if deck_c is not None and role in ('fill', 'text', 'line') and _chromatic(el.get('val')):
                deck_c[el.get('val')] += 1
            for ch in el:
                if local(ch) in MODS:
                    v = num(ch.get('val'))
                    if v is not None:
                        acc['mods']["%s|%s" % (role, local(ch))] += 1
                        acc['mod_vals'][local(ch)].append(v)
                elif local(ch) == 'prstClr':
                    acc['prst'][ch.get('val')] += 1
        elif lt == 'sysClr':
            role = role_of(el, pm)
            v = el.get('lastClr')
            if v:
                acc['colors']["%s|%s" % (role, v)] += 1
                acc['by_role'][role] += 1
        elif lt == 'schemeClr':
            role = role_of(el, pm)
            acc['by_role'][role] += 1
            acc['kind']['schemeClr'] += 1
            acc['scheme']["%s|%s" % (role, el.get('val'))] += 1


def theme_of(z, names):
    out = {'palette': None, 'fonts': None, 'fmt': None}
    for t in sorted(n for n in names if re.match(r'ppt/theme/theme\d+\.xml$', n)):
        x = parse(z, t)
        if x is None:
            continue
        sch = x.find('.//' + q(A, 'clrScheme'))
        if sch is not None and out['palette'] is None:
            pal = {}
            for c in sch:
                v, s = c.find(q(A, 'srgbClr')), c.find(q(A, 'sysClr'))
                pal[local(c)] = v.get('val') if v is not None else (
                    s.get('lastClr') if s is not None else None)
            out['palette'] = pal
        fs = x.find('.//' + q(A, 'fontScheme'))
        if fs is not None and out['fonts'] is None:
            f = {}
            for kind in ('majorFont', 'minorFont'):
                el = fs.find(q(A, kind))
                if el is None:
                    continue
                lat, ea = el.find(q(A, 'latin')), el.find(q(A, 'ea'))
                f[kind] = {'latin': (lat.get('typeface') if lat is not None else None),
                           'ea': (ea.get('typeface') if ea is not None else None)}
            out['fonts'] = f
        fmt = x.find('.//' + q(A, 'fmtScheme'))
        if fmt is not None and out['fmt'] is None:
            gfl, esl = fmt.find(q(A, 'gradFillStyleLst')), fmt.find(q(A, 'effectStyleLst'))
            out['fmt'] = {
                'grad_styles': len(list(gfl)) if gfl is not None else 0,
                'grad_stops_per_style': [len(list(g.find(q(A, 'gsLst')))) for g in gfl
                                         if g.find(q(A, 'gsLst')) is not None]
                                        if gfl is not None else [],
                'grad_style_colors': [[c.get('val') for c in g.iter(q(A, 'srgbClr'))]
                                      for g in (gfl if gfl is not None else [])][:12],
                'effect_styles': len(list(esl)) if esl is not None else 0,
                'effect_style_parts': [[local(c) for c in es if local(c) in
                                        ('softEdge', 'glow', 'reflection', 'shadow')]
                                       for es in (esl if esl is not None else [])],
            }
        break  # theme1 is the deck's master palette
    return out


def scan_gradients(sroot, acc):
    for g in sroot.iter(q(A, 'gradFill')):
        gs = list(g.iter(q(A, 'gs')))
        acc['grad_stops'][len(gs)] += 1
        cols, poss = [], []
        for e in gs:
            c = e.find(q(A, 'srgbClr'))
            if c is not None:
                cols.append(c.get('val'))
            if e.get('pos'):
                poss.append(int(e.get('pos')))
        if len(cols) >= 2:
            acc['grad_pairs'][','.join(cols[:3])] += 1
        if len(poss) >= 2:
            acc['grad_pos'][','.join(str(int(p / 1000)) for p in poss[:3])] += 1
        ang = g.find('.//' + q(A, 'lin'))
        if ang is not None and ang.get('ang'):
            acc['grad_ang'][ang.get('ang')] += 1


# =============== 2. geometry (single implementation lives in deck_measure.py) ===============
from deck_measure import (xfrm_raw, shape_bbox, flatten_tree, page_shapes, inter_frac,
                          text_zones, similar, nodes_on_line, classify_page, _local_bbox,
                          _shape_facts, bg_is_picture)


# =============== 3. animation ==================================================================

def own_prims(ctn):
    """Primitives belonging to THIS preset node: the schema hides them inside p:childTnLst, and a
    nested cTn that carries no presetClass of its own (a plain wrapper PowerPoint writes around
    behaviours) must still be descended into -- otherwise the signature comes out empty."""
    out, stack = [], list(ctn)
    while stack:
        el = stack.pop(0)
        lt = local(el)
        if lt == 'cTn' and el.get('presetClass'):
            continue
        if lt in ('childTnLst', 'subTnLst') or (lt == 'cTn' and not el.get('presetClass')):
            stack.extend(list(el))
            continue
        out.append(el)
    return out


def el_find(el, name):
    for ch in el.iter():
        if local(ch) == name:
            return ch
    return None


def timing_deep(sroot, deck, page, acc):
    tl = sroot.find(q(P, 'timing'))
    if tl is None:
        return 0
    interactive = len([e for e in tl.iter(q(P, 'cTn'))
                       if e.get('nodeType') == 'interactiveSeq'])
    n = 0
    for ctn in tl.iter(q(P, 'cTn')):
        nt = ctn.get('nodeType')
        if nt:
            acc['nt'][nt] += 1
        if interactive:
            acc['nt_in_video_interactive_pages'][nt or '?'] += 1
        st = ctn.find(q(P, 'stCondLst'))
        delay, evt, evt_spid = None, None, None
        if st is not None:
            for cond in st.iter(q(P, 'cond')):
                delay = cond.get('delay') or delay
                if cond.get('evt'):
                    evt = cond.get('evt')
                    tg = cond.find(q(P, 'tgtEl'))
                    if tg is not None:
                        sp = tg.find(q(P, 'spTgt'))
                        if sp is not None:
                            evt_spid = sp.get('spid')
        if evt:
            acc['triggers'][evt] += 1
            if evt_spid:
                acc['trigger_targets'] += 1
        cls = ctn.get('presetClass')
        if not cls:
            continue
        n += 1
        sig = collections.Counter()
        for prim in own_prims(ctn):
            if not isinstance(prim.tag, str):
                continue
            lp = local(prim)
            if lp == 'animEffect':
                acc['filters'][(prim.get('filter') or '?')] += 1
                sig['effect:' + (prim.get('filter') or '?')] += 1
            elif lp == 'animMotion':
                pth = prim.get('path') or ''
                sig['motion'] += 1
                acc['paths'].append(pth)
                if prim.get('rAng'):
                    sig['motion-rAng'] += 1
                if prim.get('ptsTypes'):
                    sig['motion-pts:%s' % prim.get('ptsTypes')] += 1
            elif lp == 'animScale':
                by = prim.find(q(A, 'by'))
                sig['scale:%s,%s' % ((by.get('x'), by.get('y')) if by is not None else ('?', '?'))] += 1
            elif lp == 'animRotate':
                by = prim.find(q(A, 'by'))
                sig['rotate:%s' % (by.get('by') if by is not None else '?')] += 1
            elif lp in ('anim', 'set'):
                # p:anim carries no attrName of its own: the target is p:cBhvr/@attributeName
                cb = prim.find(q(P, 'cBhvr'))
                an = prim.get('attrName') or (cb.get('attributeName') if cb is not None else None)
                if not an and cb is not None:
                    # PowerPoint writes the target as <p:cBhvr><p:attrNameLst><p:attrName>ppt_x
                    lst = cb.find(q(P, 'attrNameLst'))
                    if lst is not None:
                        names = [x.text for x in lst.iter(q(P, 'attrName')) if x.text]
                        an = '+'.join(names)
                an = an or '?'
                sig['%s:%s' % (lp, an)] += 1
                vals = [v.get('val') or v.get('fmla') for v in prim.iter()
                        if local(v) in ('strVal', 'intVal', 'dblVal', 'boolVal')]
                acc['attr_values']["%s|%s" % (lp, an)].update([v for v in vals if v][:3])
            elif lp == 'cmd':
                # measured form: <p:cmd type="call" cmd="playFrom(0.0)"> -- there is no @cmdtype
                cid = prim.get('cmd') or ''
                acc['cmds']["%s|%s" % (prim.get('type') or '?',
                                       re.match(r'[A-Za-z]+', cid).group(0) if cid else '?')] += 1
                sig['cmd:' + (cid.split('(')[0] or prim.get('type') or '?')] += 1
                acc['cmd_strings'][cid[:32]] += 1
            elif lp in ('video', 'audio'):
                sig[lp] += 1
                cmn = el_find(prim, 'cMediaNode')
                if cmn is not None:
                    for k in ('hideWhilePaused', 'rewind', 'volume', 'isNarration'):
                        v = cmn.get(k)
                        if v is not None:
                            acc['media_attrs']["%s(%s)=%s" % (k, lp, v)] += 1
        key = '%s:%s:%s' % (cls, ctn.get('presetID'), ctn.get('presetSubtype'))
        rec = acc['combos'][key]
        rec['n'] += 1
        rec['decks'].add(deck)
        rec['pages'].add((deck, page))
        rec['sig'].update(sig)
        rec['nt'].update([ctn.get('nodeType') or '?'])
        dv = ctn.get('dur')
        if not (dv and str(dv).isdigit()):
            # PowerPoint puts the duration on the inner behaviour node, not on the preset node
            for sub in ctn.iter(q(P, 'cTn')):
                if sub.get('dur') and str(sub.get('dur')).isdigit():
                    dv = sub.get('dur')
                    break
        for label, v in (('dur', dv), ('delay', delay)):
            iv = num(v)
            if iv is not None and str(v).isdigit():
                rec[label].append(iv)
        if evt:
            rec['triggers'] += 1
        spids = sorted({t.get('spid') for t in ctn.iter(q(P, 'spTgt'))})
        if spids:
            rec['n_targets'] += 1
    return n


def chart_and_table_builds(sroot, z, names, page):
    """Are charts / tables animated by series or by cell? Measured from graphicFrame spids."""
    tl = sroot.find(q(P, 'timing'))
    if tl is None:
        return None
    gf = []
    tree = sroot.find(q(P, 'cSld') + '/' + q(P, 'spTree'))
    if tree is None:
        return None
    for el in tree.iter(q(P, 'graphicFrame')):
        nv = el.find(q(P, 'nvGraphicFrame') + '/' + q(P, 'cNvPr'))
        ref = el.find(q(P, 'graphicFrame') + '/' + q(A, 'graphicData'))
        gt = ref.get('uri') if ref is not None else None
        gf.append((nv.get('id') if nv is not None else None, gt))
    if not gf:
        return None
    animated = set()
    for t in tl.iter(q(P, 'spTgt')):
        animated.add(t.get('spid'))
    return [{'id': i, 'uri': (u or '').split('/')[-1], 'animated': str(i) in animated}
            for i, u in gf]


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    files = sorted(f for f in os.listdir(SRC) if f.lower().endswith('.pptx'))
    if limit:
        files = files[:limit]

    acc = {
        'colors': collections.Counter(), 'by_role': collections.Counter(),
        'kind': collections.Counter(),
        'mods': collections.Counter(), 'mod_vals': collections.defaultdict(list),
        'prst': collections.Counter(), 'scheme': collections.Counter(),
        'grad_stops': collections.Counter(), 'grad_pairs': collections.Counter(),
        'grad_pos': collections.Counter(), 'grad_ang': collections.Counter(),
        'combos': collections.defaultdict(lambda: {'n': 0, 'decks': set(), 'pages': set(),
                                                   'sig': collections.Counter(),
                                                   'nt': collections.Counter(), 'dur': [],
                                                   'delay': [], 'n_targets': 0,
                                                   'triggers': 0}),
        'paths': [], 'triggers': collections.Counter(), 'trigger_targets': 0,
        'nt': collections.Counter(), 'nt_in_video_interactive_pages': collections.Counter(),
        'cmds': collections.Counter(), 'cmd_strings': collections.Counter(),
        'media_attrs': collections.Counter(),
        'attr_values': collections.defaultdict(collections.Counter),
        'sub_cmds': collections.Counter(), 'filters': collections.Counter(),
    }
    deck_palette, deck_fonts, deck_fmt, deck_sig = {}, {}, {}, {}
    archetypes = collections.Counter()
    arch_pages = collections.defaultdict(list)
    arch_zone = collections.defaultdict(collections.Counter)
    arch_over = collections.defaultdict(list)
    arch_pt = collections.defaultdict(list)
    ph_types = collections.Counter()
    ph_geo = collections.defaultdict(list)
    layout_count = collections.Counter()
    deck_dark, deck_n = {}, {}
    srcrect_vals, srcrect_pages = collections.Counter(), 0
    prst_hist, geom_stats = collections.Counter(), collections.Counter()
    per_deck_anim, interactive_pages, chart_rows = {}, [], []
    errors = {}
    total_pages = 0
    pages_index = []

    for fi, f in enumerate(files, 1):
        try:
            z = zipfile.ZipFile(os.path.join(SRC, f))
        except Exception as e:
            errors[f] = str(e)[:100]
            continue
        names = [i.filename for i in z.infolist()]
        th = theme_of(z, names)
        if th['palette']:
            deck_palette[f] = th['palette']
        if th['fonts']:
            deck_fonts[f] = th['fonts']
        if th['fmt']:
            deck_fmt[f] = th['fmt']
        W_, H_ = EMU_W, EMU_H
        px = parse(z, 'ppt/presentation.xml') if 'ppt/presentation.xml' in names else None
        if px is not None:
            sz = px.find(q(P, 'sldSz'))
            if sz is not None and num(sz.get('cx')) and num(sz.get('cy')):
                W_, H_ = num(sz.get('cx')), num(sz.get('cy'))
        layout_count[len([n for n in names
                          if re.match(r'ppt/slideLayouts/slideLayout\d+\.xml$', n)])] += 1
        for ln_ in sorted(n for n in names if re.match(
                r'ppt/(slideLayouts/slideLayout|slideMasters/slideMaster)\d+\.xml$', n)):
            lx = parse(z, ln_)
            if lx is None:
                continue
            for sp in lx.iter(q(P, 'sp')):
                # in a layout the placeholder is nvSpPr/nvPr/ph, not a direct child of nvSpPr
                ph = sp.find(q(P, 'nvSpPr') + '/' + q(P, 'nvPr') + '/' + q(P, 'ph'))
                if ph is None:
                    ph = sp.find('.//' + q(P, 'ph'))
                if ph is None:
                    continue
                t = ph.get('type') or 'body'
                b = shape_bbox(sp, W_, H_)
                if b:
                    ph_types[t] += 1
                    ph_geo[t].append(b)

        lum_vals, anim_nodes_deck = [], 0
        deck_c = collections.Counter()
        for sn in slide_order(names):
            sroot = parse(z, sn)
            if sroot is None:
                continue
            total_pages += 1
            page = int(re.findall(r'\d+', sn)[0])
            scan_colours(sroot, acc, deck_c)
            scan_gradients(sroot, acc)
            cSld = sroot.find(q(P, 'cSld'))
            bg = cSld.find(q(P, 'bg')) if cSld is not None else None
            bg_pic = bg is not None and bg.find('.//' + q(A, 'blip')) is not None
            if bg is not None:
                c = bg.find('.//' + q(A, 'srgbClr'))
                if c is not None and lum(c.get('val')) is not None:
                    lum_vals.append(lum(c.get('val')))
            shapes = page_shapes(sroot, W_, H_)
            for s in shapes:
                geom_stats['kind:' + s['kind']] += 1
                if s.get('prst'):
                    prst_hist[s['prst']] += 1
                if s.get('custGeom'):
                    geom_stats['custGeom'] += 1
                if s.get('effects'):
                    for e in s['effects']:
                        geom_stats['effect:' + e] += 1
                if s.get('sp3d'):
                    geom_stats['sp3d'] += 1
                if s.get('srcRect'):
                    srcrect_pages += 1
                    for v in s['srcRect']:
                        if v:
                            srcrect_vals[v] += 1
            atype, zones = classify_page(shapes, bg_pic)
            archetypes[atype] += 1
            pages_index.append([f, page, atype, sum(zx['chars'] for zx in zones),
                                max([zx['pt'] for zx in zones] or [0])])
            arch_zone[atype].update(['%d%d' % (zx['row'], zx['col']) for zx in zones])
            if zones:
                arch_over[atype].append(sum(1 for zx in zones if zx['over_pic']) / float(len(zones)))
                arch_pt[atype].append(max(zx['pt'] for zx in zones))
            arch_pages[atype].append({
                'deck': f, 'page': page, 'bg_pic': bg_pic,
                'pics': [[round(v, 4) for v in s['box']]
                         for s in shapes if s.get('pic') and s.get('box')][:8],
                'txt': [[round(v, 4) for v in zx['box']] for zx in zones][:8],
                'pt': [zx['pt'] for zx in zones][:8],
                'chars': [zx['chars'] for zx in zones][:8],
                'over': [zx['over_frac'] for zx in zones][:8],
                'n_group_deep': max([s['depth'] for s in shapes] or [0]),
            })
            cr = chart_and_table_builds(sroot, z, names, page)
            if cr:
                chart_rows.append({'deck': f, 'page': page, 'frames': cr})
            anim_nodes_deck += timing_deep(sroot, f, page, acc)
            if any(e.get('nodeType') == 'interactiveSeq' for e in sroot.iter(q(P, 'cTn'))):
                interactive_pages.append((f, page))
        deck_dark[f] = statistics.mean(lum_vals) if lum_vals else None
        per_deck_anim[f] = anim_nodes_deck
        deck_sig[f] = deck_c.most_common(6)
        z.close()
        if fi % 25 == 0:
            print("processed %d/%d" % (fi, len(files)), flush=True)

    # ---------------------------------------------------------- write outputs ------------------
    fam = collections.defaultdict(list)
    a1band = collections.Counter()
    for d, sig in deck_sig.items():
        if not sig:
            continue
        # family from the colour the deck actually PAINTS WITH, not from theme accents: 170 of the
        # 171 decks still ship the stock Office theme, so accent1..6 say nothing about the look.
        lead = sig[0][0]
        pal = deck_palette.get(d) or {}
        acc6 = [pal.get('accent%d' % i) for i in range(1, 7)]
        acc6 = [c for c in acc6 if c]
        if acc6:
            a1band[hue_band(hue(acc6[0]))] += 1
        fam[hue_band(hue(lead))].append({
            'deck': d, 'lead': lead, 'lead_uses': sig[0][1], 'palette_used': [c for c, _ in sig],
            'theme_accent1': (acc6[0] if acc6 else None),
            'dk1': pal.get('dk1'), 'lt1': pal.get('lt1'),
            'bg_lum': (round(deck_dark.get(d), 3) if deck_dark.get(d) is not None else None)})

    color_out = {
        'n_decks_with_theme': len(deck_palette),
        'theme_font_pairs': collections.Counter(
            '%s|%s' % ((v.get('majorFont') or {}).get('ea'), (v.get('minorFont') or {}).get('ea'))
            for v in deck_fonts.values()).most_common(20),
        'theme_accent_colors': collections.Counter(
            c for pal in deck_palette.values() for k, c in pal.items()
            if k.startswith('accent') and c).most_common(30),
        'palette_families': {k: {'n_decks': len(v),
                                 'decks': [x['deck'] for x in v],
                                 'leads': collections.Counter(x['lead'] for x in v).most_common(10),
                                 'median_lead_uses': int(statistics.median(
                                     [x['lead_uses'] for x in v])),
                                 'dark_share_pct': round(100.0 * len(
                                     [x for x in v if (x['bg_lum'] or 1) < 0.10]) / len(v), 1),
                                 'examples': v[:12]}
                             for k, v in sorted(fam.items(), key=lambda kv: -len(kv[1]))},
        'accent1_bands': dict(a1band.most_common()),
        'srgb_vs_schemeclr': dict(acc['kind'].most_common()),
        'stock_office_theme_decks': sum(
            1 for pal in deck_palette.values() if (pal.get('accent1') or '').upper() == '4472C4'),
        'colour_role_counts': dict(acc['by_role'].most_common()),
        'colors_by_role': {},
        'scheme_clr_by_role': dict(acc['scheme'].most_common(40)),
        'modifier_tokens': dict(acc['mods'].most_common(40)),
        'modifier_value_bands': {
            k: {b: n for b, n in sorted(collections.Counter(band_of(v) for v in vals).items())}
            for k, vals in acc['mod_vals'].items() if len(vals) >= 20},
        'prst_named_colors': dict(acc['prst'].most_common(12)),
        'gradient_stop_counts': {str(k): v for k, v in sorted(acc['grad_stops'].items())},
        'gradient_top_pairs': acc['grad_pairs'].most_common(30),
        'gradient_top_positions': acc['grad_pos'].most_common(20),
        'gradient_angles': acc['grad_ang'].most_common(24),
        'theme_fmt_scheme_styles': collections.Counter(
            json.dumps(v['effect_style_parts'], ensure_ascii=False) for v in deck_fmt.values()
            if v).most_common(8),
        'theme_fmt_grad_colors': collections.Counter(
            json.dumps(v['grad_style_colors'], ensure_ascii=False) for v in deck_fmt.values() if v
        ).most_common(5),
        'srcRect_pages': srcrect_pages,
        'srcRect_values': dict(srcrect_vals.most_common(25)),
        'deck_bg_lum_median': (round(statistics.median([v for v in deck_dark.values() if v]), 4)
                               if any(deck_dark.values()) else None),
        'dark_decks': sorted([d for d, v in deck_dark.items() if v is not None and v < 0.10]),
        'light_decks': sorted([d for d, v in deck_dark.items() if v is not None and v >= 0.60]),
        'errors': errors,
    }
    for k in acc['colors']:
        role, col = k.split('|', 1)
        color_out['colors_by_role'].setdefault(role, collections.Counter())[col] += acc['colors'][k]
    color_out['colors_by_role'] = {r: dict(c.most_common(30)) for r, c in
                                   color_out['colors_by_role'].items()}

    layout_out = {
        'n_pages': total_pages,
        'n_decks': len(files),
        'geom_counts': dict(geom_stats.most_common(30)),
        'prst_shapes': dict(prst_hist.most_common(30)),
        'layouts_per_deck': {str(k): v for k, v in sorted(layout_count.items())},
        'placeholder_types': dict(ph_types.most_common()),
        'placeholder_median_box': {k: [round(statistics.median([b[i] for b in v]), 4)
                                        for i in range(4)] for k, v in ph_geo.items()},
        'placeholder_box_examples': {k: [[round(x, 3) for x in b] for b in v[:6]]
                                     for k, v in ph_geo.items()},
        'archetype_share': {k: {'pages': v, 'pct': round(100.0 * v / max(1, total_pages), 1)}
                            for k, v in archetypes.most_common()},
        'archetype_zones': {k: dict(v.most_common(9)) for k, v in arch_zone.items()},
        'archetype_text_over_pic_pct': {k: round(100.0 * statistics.mean(v), 1)
                                        for k, v in arch_over.items() if v},
        'archetype_max_pt_median': {k: statistics.median(v) for k, v in arch_pt.items() if v},
        'archetype_examples': {k: v[:60] for k, v in arch_pages.items()},
        'pages_index': pages_index,
    }

    combos_out = {}
    for k, rec in acc['combos'].items():
        combos_out[k] = {
            'n': rec['n'], 'decks': len(rec['decks']), 'pages': len(rec['pages']),
            'signature': dict(rec['sig'].most_common(8)),
            'nodeTypes': dict(rec['nt'].most_common(6)),
            'dur_ms': _stats(rec['dur']), 'delay_ms': _stats(rec['delay']),
            'triggered': rec['triggers'],
            'targeted': rec['n_targets'], 'decks_sample': sorted(rec['decks'])[:8]}
    anim_out = {
        'pages_with_timing': total_pages,
        'combo_signatures': dict(sorted(combos_out.items(), key=lambda kv: -kv[1]['n'])),
        'nodeType_total': dict(acc['nt'].most_common()),
        'nodeType_in_video_interactive_pages': dict(acc['nt_in_video_interactive_pages'].most_common()),
        'trigger_events': dict(acc['triggers'].most_common()),
        'trigger_with_target_spid': acc['trigger_targets'],
        'cmd_type_and_call': dict(acc['cmds'].most_common(20)),
        'cmd_strings': dict(acc['cmd_strings'].most_common(20)),
        'media_node_attrs': dict(acc['media_attrs'].most_common(20)),
        'animEffect_filters': dict(acc['filters'].most_common(40)),
        'cmd_subtypes': dict(acc['sub_cmds'].most_common(20)),
        'attr_values': {k: dict(v.most_common(6)) for k, v in acc['attr_values'].items()
                        if len(v) >= 1},
        'motion_path_samples': acc['paths'][:12],
        'interactive_seq_pages': len(interactive_pages),
        'interactive_seq_decks': sorted({d for d, _ in interactive_pages}),
        'graphicframe_build_pages': len(chart_rows),
        'graphicframe_animated': sum(1 for r in chart_rows for x in r['frames'] if x['animated']),
        'graphicframe_examples': chart_rows[:20],
        'per_deck_anim_nodes': dict(sorted(per_deck_anim.items(), key=lambda kv: -kv[1])[:60]),
    }
    for fn, obj in (('color_system.json', color_out), ('layout_archetypes.json', layout_out),
                    ('animation_mechanics.json', anim_out)):
        json.dump(obj, io.open(os.path.join(W, fn), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)

    print("decks=%d pages=%d combos=%d roles=%d errors=%d" % (
        len(files), total_pages, len(combos_out), len(color_out['colors_by_role']), len(errors)))
    print("ARCH:", json.dumps([[k, v['pages'], v['pct']]
                               for k, v in layout_out['archetype_share'].items()],
                              ensure_ascii=False))
    print("FAM:", json.dumps([[k, v['n_decks'], v['leads'][:3], v['dark_share_pct']]
                              for k, v in color_out['palette_families'].items()], ensure_ascii=False))
    print("THEME:", json.dumps([color_out['stock_office_theme_decks'],
                                color_out['srgb_vs_schemeclr'],
                                color_out['accent1_bands']], ensure_ascii=False))
    print("NT:", json.dumps(anim_out['nodeType_total']))
    print("CMD:", json.dumps(anim_out['cmd_type_and_call']))
    print("TRIG:", json.dumps(anim_out['trigger_events']))
    print("ATTR:", json.dumps(dict(list(anim_out['attr_values'].items())[:10]), ensure_ascii=False))
    print("COMBO:", json.dumps([[k, v['n'], v['signature']] for k, v in
                                list(anim_out['combo_signatures'].items())[:14]], ensure_ascii=False))
    print("INTERACTIVE pages=%d decks=%d" % (anim_out['interactive_seq_pages'],
                                             len(anim_out['interactive_seq_decks'])))


def _chromatic(rgb):
    """A colour that actually carries hue information: not grey, not near-black/white."""
    l = lum(rgb)
    return l is not None and chroma(rgb) >= 0.25 and 0.06 <= l <= 0.95


def band_of(v):
    """Modifier values are 1000ths of a percent; band them so the report reads in %."""
    pct = v / 1000.0
    if pct < 25:
        return '1 <25%'
    if pct < 50:
        return '2 25-50%'
    if pct < 75:
        return '3 50-75%'
    if pct <= 100:
        return '4 75-100%'
    if pct <= 150:
        return '5 100-150%'
    return '6 >150%'


def _stats(vals):
    if not vals:
        return None
    u = sorted(set(vals))
    return {'min': u[0], 'max': u[-1], 'median': u[len(u) // 2], 'distinct': len(u),
            'top': collections.Counter(vals).most_common(5)}


if __name__ == '__main__':
    main()
