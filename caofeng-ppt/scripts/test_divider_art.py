# -*- coding: utf-8 -*-
"""P9 数字过渡页"取色渐变右块+组合"的单元反证（教程 3056573a [60.3-86.3]）。

只测 divider_art 模块本身；装配级差分（坏夹具必须被 G29 咬）在接线后补进 test_gates。
"""
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import divider_art as D
from lxml import etree
from pptx import Presentation
from pptx.util import Emu

A = D.A
FAIL = []


def ck(name, cond):
    print(('OK   ' if cond else 'FAIL ') + name)
    if not cond:
        FAIL.append(name)


def main():
    import caofeng as C
    here = os.path.dirname(os.path.abspath(__file__))
    img = os.path.join(here, '..', 'kb', 'noop.png')
    # 用一张自造的"左暗右亮蓝"图验证取色器真的吸右带的颜色，而不是全图平均/写死色
    import numpy as np
    from PIL import Image
    arr = np.zeros((200, 400, 3), np.uint8)
    arr[:, :200] = (200, 40, 40)
    arr[:, 200:] = (30, 110, 200)
    tmp = os.path.join(os.environ.get('TEMP', '.'), '_p9_swatch.png')
    Image.fromarray(arr).save(tmp)
    col = D.sample_region_color(tmp, frac=(0.62, 0.30, 0.98, 0.86))
    ck('取色器吸右带亮蓝而非全图平均', col == '1E6EC8')
    col_left = D.sample_region_color(tmp, frac=(0.02, 0.30, 0.38, 0.86))
    ck('左带吸到红、右带吸到蓝（证明真的按区域取）', col_left == 'C82828' and col_left != col)

    prs = Presentation()
    prs.slide_width = Emu(C.EMU_W)
    prs.slide_height = Emu(C.EMU_H)
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    blk = D.add_gradient_block(sl, (0.55, 0.20, 0.40, 0.60), col)
    txt = C.add_text(sl, u'文案', (0.58, 0.40, 0.34, 0.20), 24, color='FFFFFF', name='title')
    grp = D.group_shapes(sl, [blk, txt])
    out = os.path.join(os.environ.get('TEMP', '.'), '_p9_test.pptx')
    prs.save(out)
    z = zipfile.ZipFile(out)
    root = etree.fromstring(z.read('ppt/slides/slide1.xml'))
    gs = root.findall('.//{%s}gs' % A)
    ck('渐变三档光圈', len(gs) == 3)
    ck('左光圈=取色器颜色', gs and gs[0].find('{%s}srgbClr' % A).get('val') == col)
    alphas = [int(g.find('{%s}srgbClr/{%s}alpha' % (A, A)).get('val')) for g in gs]
    ck('左光圈不透明右光圈半透明（教程调左光圈亮度/透明度）', alphas[0] > alphas[-1])
    lin = root.find('.//{%s}gradFill/{%s}lin' % (A, A))
    ck('渐变角倾斜', lin is not None and lin.get('ang') not in (None, '0'))
    P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
    spPr = root.find('.//{%s}spPr' % P)
    tags = [etree.QName(c).localname for c in spPr]
    ck('CT_SpPr 顺序：fill 在 ln 之前', 'gradFill' in tags and 'ln' in tags
       and tags.index('gradFill') < tags.index('ln'))
    ck('真组合 p:grpSp', root.find('.//{http://schemas.openxmlformats.org/presentationml/2006/main}grpSp') is not None)
    ck('组里有两个成员', grp is not None and len(grp.shapes) == 2)

    # 装配级差分：好夹具零违规，坏夹具各自被咬（审计器必须自证咬得住）
    def build(number_color='FFFFFF', number_pt=120, number_wrap=False,
              block_color=None, do_group=False, block_after_title=False):
        pr = Presentation()
        pr.slide_width = Emu(C.EMU_W)
        pr.slide_height = Emu(C.EMU_H)
        s = pr.slides.add_slide(pr.slide_layouts[6])
        nsh = C.add_text(s, u'01', (0.06, 0.10, 0.34, 0.72), number_pt, bold=True, color=number_color, name='chapter_no')
        nsh.text_frame.word_wrap = number_wrap
        if not block_after_title:
            b = D.add_gradient_block(s, (0.55, 0.20, 0.40, 0.60), block_color or col)
        t = C.add_text(s, u'文案', (0.58, 0.40, 0.34, 0.20), 24, color='FFFFFF', name='title')
        if block_after_title:
            b = D.add_gradient_block(s, (0.55, 0.20, 0.40, 0.60), block_color or col)
        if do_group:
            D.group_shapes(s, [b, t])
        f = os.path.join(os.environ.get('TEMP', '.'), '_p9_fx.pptx')
        pr.save(f)
        return zipfile.ZipFile(f).read('ppt/slides/slide1.xml')

    good = build()
    ck('好夹具零违规', D.audit_divider_art(good, tmp) == [])
    ck('坏：写死主题色被咬', 'gradient_not_sampled_from_bg' in D.audit_divider_art(build(block_color='4472C4'), tmp))
    ck('坏：右块盖在文案上被咬', 'block_over_text' in D.audit_divider_art(build(block_after_title=True), tmp))
    ck('坏：真组藏文字被咬', 'group_hides_text_from_gates' in D.audit_divider_art(build(do_group=True), tmp))
    ck('坏：数字非白被咬', 'number_not_white' in D.audit_divider_art(build(number_color='FF0000'), tmp))
    ck('坏：数字不够巨型被咬', 'number_not_giant' in D.audit_divider_art(build(number_pt=40), tmp))
    ck('坏：编号允许折行被咬', 'number_may_wrap' in D.audit_divider_art(build(number_wrap=True), tmp))
    _good_xml = build().decode('utf8', 'replace')
    _bright = re.sub(r'(<a:gs pos="0"><a:srgbClr val=")[0-9A-F]{6}', lambda m: m.group(1) + 'FFFFFF',
                     _good_xml, count=1).encode('utf8')
    _tb = D.audit_divider_art(_bright, tmp)
    ck('坏：右块亮度高到白字压不住被咬', 'block_too_bright_for_white_text' in _tb)
    ck('坏：把取到的色换成纯白也被判没取色', 'gradient_not_sampled_from_bg' in _tb)
    ck('取色后自动压亮度（教程调光圈亮度）', D.rel_lum(D.clamp_lum('C8DCFF')) <= 0.21)
    print('FAILS=%d' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())