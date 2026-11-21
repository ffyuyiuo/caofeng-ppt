"""Extract per-slide text-box geometry + font size + run colour from the 172 decks.

The main ledger records counts only; the visual metrics need WHERE the text is so we can sample
the rendered pixels underneath it. Written as a separate light pass so it can be rerun cheaply.
Output: CAOFENG_WORK/text_geom.json  {deck: [{page, boxes:[{x,y,w,h,pt,color,chars}]}]}
"""
import os, io, json, re, zipfile, sys
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

import corpus_path as CP
SRC = CP.corpus_dir(strict=True)
W = require_work(os.environ.get("CAOFENG_WORK", "CAOFENG_WORK"))
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'


def main():
    out = {}
    n_box = 0
    for f in sorted(os.listdir(SRC)):
        if not f.lower().endswith('.pptx'):
            continue
        try:
            z = zipfile.ZipFile(os.path.join(SRC, f))
        except Exception:
            continue
        names = [i.filename for i in z.infolist()]
        sl = sorted([n for n in names if re.match(r'ppt/slides/slide\d+\.xml$', n)],
                    key=lambda s: int(re.findall(r'\d+', s)[0]))
        pages = []
        for sn in sl:
            try:
                x = ET.fromstring(z.read(sn))
            except Exception:
                pages.append({'page': int(re.findall(r'\d+', sn)[0]), 'boxes': []})
                continue
            boxes = []
            for sp in x.iter():
                if sp.tag != P + 'sp':
                    continue
                txts = [t.text or '' for t in sp.iter(A + 't')]
                body = ''.join(txts).strip()
                if not body:
                    continue
                xfrm = sp.find(P + 'spPr/' + A + 'xfrm')
                if xfrm is None:
                    continue
                off = xfrm.find(A + 'off')
                ext = xfrm.find(A + 'ext')
                if off is None or ext is None:
                    continue
                try:
                    bx = {'x': int(off.get('x')), 'y': int(off.get('y')),
                          'w': int(ext.get('cx')), 'h': int(ext.get('cy')),
                          'chars': len(body)}
                except (TypeError, ValueError):
                    continue
                szs = [int(r.get('sz')) // 100 for r in sp.iter(A + 'rPr') if r.get('sz')]
                bx['pt'] = max(szs) if szs else 0
                col = None
                for r in sp.iter(A + 'rPr'):
                    c = r.find(A + 'solidFill/' + A + 'srgbClr')
                    if c is not None:
                        col = c.get('val')
                        break
                    s = r.find(A + 'solidFill/' + A + 'schemeClr')
                    if s is not None:
                        col = 'sch:' + (s.get('val') or '')
                        break
                bx['color'] = col
                boxes.append(bx)
                n_box += 1
            pages.append({'page': int(re.findall(r'\d+', sn)[0]), 'boxes': boxes})
        out[f] = pages
        z.close()
    json.dump(out, io.open(os.path.join(W, 'text_geom.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    print("decks=%d slides=%d text boxes=%d" % (
        len(out), sum(len(v) for v in out.values()), n_box))


if __name__ == '__main__':
    main()
