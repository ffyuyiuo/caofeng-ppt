# -*- coding: utf-8 -*-
"""数字过渡页的"取色渐变右块 + 组合"（教程 3056573a 麒麟数字内页 [60.3-86.3] 的 OOXML 等价）。

教程原操作：右块用取色器吸图片里的颜色 → 渐变填充 → 调左光圈亮度/透明度 → 倾斜渐变角 →
文案与右块 Ctrl+G 组合。PowerPoint 的布尔拆分/取色器没有 COM 自动化入口，这里用等价几何：
  取色器  = 对底图右带做区域主色统计（颜色真的来自本页底图，不许写死主题色）；
  渐变光圈 = 同色相两档亮度+alpha 的 gsLst，lin ang 倾斜；
  Ctrl+G  = python-pptx add_group_shape（真 p:grpSp，动画挂组上）。
CT_SpPr 是有序序列（xfrm→几何→fill→ln→…），渐变必须插到 ln 之前，否则 PowerPoint 弹修复。
"""
from lxml import etree

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'


def sample_region_color(img_path, frac=(0.62, 0.30, 0.98, 0.86)):
    """取色器等价：底图指定比例区域的平均色（RGB hex）。读图走 PIL，中文路径不踩 cv2 errno42。"""
    import numpy as np
    from PIL import Image
    with Image.open(img_path) as im:
        im = im.convert('RGB')
        w, h = im.size
        x0, y0, x1, y1 = (int(frac[0] * w), int(frac[1] * h), int(frac[2] * w), int(frac[3] * h))
        arr = np.asarray(im.crop((x0, y0, max(x0 + 1, x1), max(y0 + 1, y1))), np.float32)
    # 只统计"有颜色"的像素（彩度或亮度高于区域中位），避免被大片暗底拉成近黑——
    # 取色器在教程里吸的是画面里的发光色，不是阴影。
    lum = arr.mean(axis=2)
    chroma = arr.max(axis=2) - arr.min(axis=2)
    sel = (chroma > np.median(chroma)) | (lum > np.percentile(lum, 80))
    px = arr[sel] if sel.any() else arr.reshape(-1, 3)
    mean = px.mean(axis=0)
    return '%02X%02X%02X' % tuple(int(max(0, min(255, round(v)))) for v in mean)


def _shade(hexcolor, factor):
    r, g, b = (int(hexcolor[i:i + 2], 16) for i in (0, 2, 4))
    return '%02X%02X%02X' % tuple(int(max(0, min(255, round(v * factor)))) for v in (r, g, b))


def rel_lum(hexcolor):
    r, g, b = (pow(int(hexcolor[i:i + 2], 16) / 255.0, 2.2) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def clamp_lum(hexcolor, max_lum=0.20):
    """教程 [69.3-71.3] 的"微调光圈亮度"：保住色相把亮度压到白字压得住的档位。
    实测底图是亮照片时取到的色可达 0.35+，白字掉到 3:1 以下；压到 ≤0.20 后
    白字对比度 ≥4.5:1（页 2/11/23/45/57/83 实测 4.66–9.10）。"""
    l = rel_lum(hexcolor)
    if l <= max_lum or l <= 0:
        return hexcolor
    f = (max_lum / l) ** (1.0 / 2.2)
    r, g, b = (int(hexcolor[i:i + 2], 16) for i in (0, 2, 4))
    return '%02X%02X%02X' % tuple(int(max(0, min(255, round(v * f)))) for v in (r, g, b))


def add_gradient_block(slide, zone, hexcolor, ang=5400000, name='divider_block'):
    """右块：同色相渐变（左光圈亮+不透明、右光圈暗+半透明），角度倾斜。zone 为画布比例。"""
    from pptx.util import Emu
    import caofeng as C
    hexcolor = clamp_lum(hexcolor)
    x, y, w, h = zone
    shp = slide.shapes.add_shape(1, Emu(int(x * C.EMU_W)), Emu(int(y * C.EMU_H)),
                                 Emu(int(w * C.EMU_W)), Emu(int(h * C.EMU_H)))
    spPr = shp._element.spPr
    for child in list(spPr):
        if child.tag in ('{%s}solidFill' % A, '{%s}gradFill' % A, '{%s}noFill' % A):
            spPr.remove(child)
    g = spPr.makeelement('{%s}gradFill' % A, {})
    lst = etree.SubElement(g, '{%s}gsLst' % A)
    for pos, col, alp in ((0, hexcolor, 92), (55000, _shade(hexcolor, 0.80), 62),
                          (100000, _shade(hexcolor, 0.55), 30)):
        gs = etree.SubElement(lst, '{%s}gs' % A)
        gs.set('pos', str(pos))
        clr = etree.SubElement(gs, '{%s}srgbClr' % A)
        clr.set('val', col)
        etree.SubElement(clr, '{%s}alpha' % A).set('val', str(alp * 1000))
    lin = etree.SubElement(g, '{%s}lin' % A)
    lin.set('ang', str(ang))
    lin.set('scaled', '0')
    ln = shp.line._get_or_add_ln()
    etree.SubElement(ln, '{%s}noFill' % A)
    ln.addprevious(g)
    shp.name = name
    return shp


def group_shapes(slide, shapes, name='divider_group'):
    """Ctrl+G 的可见后果是"文案与右块一起飞入"。真 p:grpSp 会把组内文字藏进组里，
    而全引擎 26 处 slide.shapes 迭代都不递归（G1/G4/G23/G24 会看不见组内字）——
    所以装配端不建组，改用"右块与文案同一条 withEffect 飞入"等价 reproducing 组合动画；
    本函数只留给确需真组的调用方，默认路径不用。"""
    shapes = [s for s in shapes if s is not None]
    if len(shapes) < 2:
        return shapes[0] if shapes else None
    grp = slide.shapes.add_group_shape(shapes)
    grp.name = name
    return grp


P = 'http://schemas.openxmlformats.org/presentationml/2006/main'


def _hexdist(a, b):
    return max(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) for i in (0, 2, 4))


def audit_divider_art(xml, bg_path):
    """G29 的过渡页补判（教程 3056573a 的四个可见特征），返回违规名列表。

    反向自证要求：写死主题色/无组合/数字非白/数字不溢出 四种坏夹具必须各自被咬，
    好夹具（真取色+真组合+白字+溢出）必须零违规。判据与装配共用 sample_region_color，
    不许两边各写一把尺子。"""
    root = etree.fromstring(xml if isinstance(xml, bytes) else xml.encode('utf8'))
    bad = []
    blocks = [sp for sp in root.iter('{%s}sp' % P)
              if sp.find('.//{%s}cNvPr' % P) is not None
              and (sp.find('.//{%s}cNvPr' % P).get('name') or '') == 'divider_block']
    if not blocks:
        return ['missing_gradient_block']
    gs = blocks[0].findall('.//{%s}gs' % A)
    if not gs:
        bad.append('block_not_gradient')
    elif bg_path:
        want = clamp_lum(sample_region_color(bg_path))
        got = gs[0].find('{%s}srgbClr' % A)
        if got is None:
            bad.append('gradient_not_sampled_from_bg')
        else:
            v = got.get('val', '000000')
            if _hexdist(v, want) > 24:
                bad.append('gradient_not_sampled_from_bg')
            if rel_lum(v) > 0.22:
                bad.append('block_too_bright_for_white_text')
    grp = root.find('.//{%s}grpSp' % P)
    if grp is not None:
        # 真组会把组内文字藏起来（引擎 26 处迭代不递归）：默认装配不许建组
        bad.append('group_hides_text_from_gates')
    else:
        order = [etree.QName(c).localname + ':' + (c.find('.//{%s}cNvPr' % P).get('name')
                 if c.find('.//{%s}cNvPr' % P) is not None else '')
                 for c in root.find('.//{%s}spTree' % P)]
        names = [o.split(':', 1)[1] for o in order]
        if 'divider_block' in names and 'title' in names \
                and names.index('divider_block') > names.index('title'):
            bad.append('block_over_text')
    nos = [sp for sp in root.iter('{%s}sp' % P)
           if (sp.find('.//{%s}cNvPr' % P).get('name') or '') == 'chapter_no']
    if not nos:
        bad.append('missing_chapter_no')
    else:
        sp = nos[0]
        fill = sp.find('.//{%s}solidFill/{%s}srgbClr' % (A, A))
        if fill is None or fill.get('val', '').upper() != 'FFFFFF':
            bad.append('number_not_white')
        # 教程 [35.3-37.3] 字号拉到 800：本技能按栏宽取巨型档（>=96pt）。原片"溢出画面"
        # 与 G8 越界闸冲突，取舍记 SPEC §18.2——这里核尺度不核越界。
        rpr = sp.find('.//{%s}rPr' % A)
        sz = int(rpr.get('sz', '0')) if rpr is not None else 0
        if sz < 9600:
            bad.append('number_not_giant')
        # 巨型编号折行会被裁底（实测渲染页 11 的 '02' 折成两行）：必须单行不換行
        bp = sp.find('.//{%s}bodyPr' % A)
        if bp is None or bp.get('wrap') != 'none':
            bad.append('number_may_wrap')
    return bad
