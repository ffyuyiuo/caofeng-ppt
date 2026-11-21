# -*- coding: utf-8 -*-
"""语料那套"数据怎么摆"的产出件：大数字、条形组、卡片栅格。

依据：kb/data_design.json —— 含数字的好示范页里 35.6% 有 ≥60pt 的数字主体，
17.8% 用 ≥3 张卡片，5.8% 用条形组；而真表格页只占 0.7%。
装配器以前只会画 <a:tbl> 与文字框，所以数据页一眼就是"文档"而不是"片子"。
"""
import re

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
EMU_W, EMU_H = 12192000, 6858000


def _rgb(hexc):
    from pptx.dml.color import RGBColor
    return RGBColor.from_string((hexc or 'FFFFFF').lstrip('#').upper()[:6])


def _fill(shape, hexc, alpha=None):
    from pptx.oxml.ns import qn
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(hexc)
    if alpha is not None:
        srgb = shape.fill.fore_color._xFill.find(qn('a:srgbClr'))
        if srgb is not None:
            a = srgb.makeelement(qn('a:alpha'), {'val': str(int(alpha * 1000))})
            srgb.append(a)


def _grad_v(shape, hex_top, hex_bot, alpha=92):
    """柱身竖向渐变（上亮下深）：教程册的柱状图靠这个脱离"平涂色块"的草稿感。"""
    from lxml import etree
    from pptx.oxml.ns import qn
    spPr = shape._element.spPr
    for child in list(spPr):
        if child.tag in (qn('a:solidFill'), qn('a:gradFill'), qn('a:noFill')):
            spPr.remove(child)
    g = spPr.makeelement(qn('a:gradFill'), {})
    lst = etree.SubElement(g, qn('a:gsLst'))
    for pos, hx in ((0, hex_top), (100000, hex_bot)):
        gs = etree.SubElement(lst, qn('a:gs'))
        gs.set('pos', str(pos))
        clr = etree.SubElement(gs, qn('a:srgbClr'))
        clr.set('val', (hx or '016DFF').lstrip('#').upper()[:6])
        etree.SubElement(clr, qn('a:alpha')).set('val', str(int(alpha * 1000)))
    lin = etree.SubElement(g, qn('a:lin'))
    lin.set('ang', '5400000')
    lin.set('scaled', '0')
    ln = shape.line._get_or_add_ln()
    etree.SubElement(ln, qn('a:noFill'))
    ln.addprevious(g)


def _noline(shape):
    shape.line.fill.background()


def hero_display(val, unit=''):
    """大数字上屏的那串字：数值与单位之间不许留普通空格。

    普通空格是 PowerPoint/WPS 的断行点，110pt 时"元"就掉成孤行（G8 实测 51 处）；
    val 本身也可能已经是 '46.9 万元' 这种带空格的写法，所以整串都要换。"""
    return (val + (u'\u00a0' + unit if unit else u'')).strip().replace(u' ', u'\u00a0')


NARROW = {'.': 0.32, ',': 0.30, '，': 1.0}
WIDE_PCT = {u'%': 0.92}


def em_units(s):
    """行宽估算的唯一一把尺子——直接走 em_width（= char_em_width 逐字累加）。

    这里原来自带一套权重（全角 1.0、半角 0.55、'.'0.32、'%'.0.92）：大数字挑字号用它，
    折行判孤字用另一套，结果 93pt 的 '26 万人' 一边"刚好放得下"、一边"末行只剩人"，
    G8 一次点出 25 处。尺子只能有一把，标定见 char_em_width。"""
    return em_width(s)


def hero_number(slide, add_text, item, zone, pal, ink):
    """一个大数字 + 单位 + 一句结论。语料口径：数字 ≥60pt（本技能默认 110pt）。"""
    val = (item.get('value') or '').strip()
    unit = (item.get('unit') or '').strip()
    label = (item.get('label') or '').strip()
    if not val:
        return None
    x, y, w, h = zone
    num_zone = [x, y, w, min(0.30, h)]
    # 数值与单位必须同一行：把它们当两段打，单位就单独占一行（实测 110pt 的"元"成了孤行，
    # G8 一次点了 35 处）。字号还要按栏宽收，收到一行放得下为止（语料大字 ≥60pt 就够看）。
    # 不间断空格：普通空格会被 PowerPoint/WPS 当成断行点，110pt 的"元"就掉到下一行。
    # 注意 val 自己就可能带空格（key_numbers 的 context 里是 '46.9 万元' 这种写法），
    # 所以只拼 unit 那一段不够——成品实测 51 处孤字尾行全是这么来的，这里把整串的空格都换掉。
    disp = hero_display(val, unit)
    n_chars = max(1, len(disp.replace(u' ', u'').replace(u'\u00a0', u'')))
    # 栏宽（画布比例 13.333 英寸）→ 磅值：留 6% 余量，别贴着边界算
    # 实测页 5 把 '45.37%' 的 % 折到下一行：'%' 看着是半角其实接近全宽，
    # 旧权重按 0.55 em 算，110pt 刚好超出框宽 1%，PowerPoint 就把单位甩到第二行。
    box_pt = w * (12192000 / 914400.0) * 72.0
    em_w = em_units(disp) * 1.15
    fit_pt = int(box_pt / max(0.8, em_w))
    pt = max(56, min(int(item.get('pt') or 110), fit_pt,
                     int((w * 1920.0) / (n_chars * 1.12))))
    while pt > 56 and pt * em_units(disp) > box_pt:
        pt -= 6
    if pt * em_units(disp) > box_pt and unit:
        # 56pt 还放不下（v10 页199 的 '9,000 人次' 把 '人' 甩成孤行）：
        # 单位挪到结论行去（大数字只留数值），别为了保单位把字折成两行。
        label = (unit + (u' ' + label if label else u'')).strip()
        disp = hero_display(val, u'')
        n_chars = max(1, len(disp.replace(u' ', u'').replace(u'\u00a0', u'')))
        em_w = em_units(disp) * 1.15
        fit_pt = int(box_pt / max(0.8, em_w))
        pt = max(56, min(int(item.get('pt') or 110), fit_pt,
                         int((w * 1920.0) / (n_chars * 1.12))))
        while pt > 56 and pt * em_units(disp) > box_pt:
            pt -= 6
    tb = add_text(slide, disp, num_zone, pt, bold=True,
                  color=pal.get('cyan_hex') or '66D9F7', align='l', name='hero_num')
    if label:
        add_text(slide, label[:8], [x, y + min(0.30, h) + 0.01, w, 0.10], 16,
                 color=ink['text'], align='l', name='hero_label')
    return tb


def ring_chart(slide, frac, zone, pal, ink):
    """占比圆环（语料那种 donut 占比图）：frac 是 0–1 的真实比例，画不出比例就不画。

    为什么要有它：同级尺的"图形化率"数的是 cards≥3/bars≥3/ellipses≥1 的页（基线 kb/parity_baseline.json，
    好示范页口径），我们那种"一个百分比 + 一句结论"的页只画了大数字、占比没用环承载。
    占比本来就该用环看，不是装饰：比例不在 0.02–0.98 区间、或区高放不下，就返回 0。
    """
    from pptx.util import Emu
    from pptx.enum.shapes import MSO_SHAPE
    try:
        f = float(frac)
    except (TypeError, ValueError):
        return 0
    if not (0.02 <= f <= 0.98):
        return 0
    x, y, w, h = zone
    d = min(w, h)
    if d < 0.10:
        return 0
    ring = slide.shapes.add_shape(MSO_SHAPE.DONUT, Emu(int(x * EMU_W)), Emu(int(y * EMU_H)),
                                  Emu(int(d * EMU_W)), Emu(int(d * EMU_H)))
    _fill(ring, pal.get('accent_hex') or '016DFF', alpha=int(max(15, min(95, f * 100))))
    _noline(ring)
    ring.name = 'ratio_ring'
    return 1


def bar_group(slide, items, zone, pal, ink):
    """条形组：等高宽的竖条 + 顶部数值 + 底部标签。items=[(label, value_float, text)]"""
    from pptx.util import Emu
    from pptx.enum.shapes import MSO_SHAPE
    items = items[:4]
    vals = [i[1] for i in items if i[1] is not None]
    if len(vals) < 2:
        return 0
    vmax = max(vals) or 1.0
    x, y, w, h = zone
    n = len(items)
    gap = 0.022
    slot = (w - gap * (n - 1)) / float(n)
    max_h = h - 0.20
    # 区高不够就别画：条形本身要 0.12 值 + 0.20 基线与标签的空间，
    # 硬塞会越出留白区甚至出画布（实测探针页 5：4 个形状出画布、G5 报 4 框越区）
    if max_h < 0.10:
        return 0
    # 语料的数据柱是"细柱 + 轨道"（科技风数据展示册 p001：一排等高细柱坐在轨道上），
    # 不是我们上一版那种把页宽三等分、又胖又矮的色块——量出来的后果很实在：
    # 柱高/柱宽 < 1.6 时，同级尺（与语料挖掘同一把 analyze_slide）一根柱子都不认，
    # 24 页画了条形组的页在"图形化率"上全部归零。柱宽按槽位收，轨道给满槽高。
    colw = max(0.030, min(slot * 0.55, 0.085, max_h / 2.95))
    # 教程册"绘制矩形形状并设置阴影用来承载数据"：先垫一块半透明圆角面板，
    # 柱子与数字坐在面板上，而不是一排色块浮在空背景上。
    panel = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                   Emu(int((x - 0.02) * EMU_W)), Emu(int((y - 0.03) * EMU_H)),
                                   Emu(int((w + 0.04) * EMU_W)), Emu(int((h + 0.04) * EMU_H)))
    _fill(panel, pal.get('panel_hex') or '003069', alpha=35)
    _noline(panel)
    panel.name = 'chart_panel'
    # 教程 1ea8b415 / NO.147 中央英雄图：柱后一道上升箭头（SmartArt 箭头向上那步），
    # 半透明强调色斜跨柱区，数据页有"增长英雄图"而非一排平柱；垫在柱后不压数值。
    _trend = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW,
                                    Emu(int((x + w * 0.04) * EMU_W)),
                                    Emu(int((y + 0.10) * EMU_H)),
                                    Emu(int(w * 0.92 * EMU_W)),
                                    Emu(int(max(0.06, max_h * 0.5) * EMU_H)))
    _trend.rotation = -20.0
    _fill(_trend, pal.get('cyan_hex') or '66D9F7', alpha=30)
    _noline(_trend)
    _trend.name = 'chart_trend_arrow'
    made = 0
    for i, it in enumerate(items):
        label, v, txt = it[0], it[1], (it[2] if len(it) > 2 else '')
        bx = x + i * (slot + gap)
        cx = bx + (slot - colw) / 2.0
        # 轨道：满槽高的浅底柱，读者一眼看出"满格是多少"，比例才读得出来
        tr = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(int(cx * EMU_W)),
                                    Emu(int((y + 0.12) * EMU_H)),
                                    Emu(int(colw * EMU_W)), Emu(int(max_h * EMU_H)))
        _fill(tr, pal.get('panel_hex') or '003069', alpha=30)
        _noline(tr)
        tr.name = 'bar_track_%d' % i
        bh = max(0.12, max_h * (float(v) / vmax)) if v is not None else 0.12
        bh = min(bh, max_h)
        sh = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(int(cx * EMU_W)),
                                    Emu(int((y + 0.12 + (max_h - bh)) * EMU_H)),
                                    Emu(int(colw * EMU_W)), Emu(int(bh * EMU_H)))
        # 教程册的柱状图不是平涂色块：柱身渐变（上亮下深）、最大那根换强调色，
        # 外面再垫一块带阴影的圆角面板"承载数据"（NO.系列教程：绘制矩形设置阴影用来承载数据）。
        _top = (pal.get('cyan_hex') or '66D9F7') if v == vmax else (pal.get('accent_hex') or '016DFF')
        _grad_v(sh, _top, pal.get('page_bg') or '09192B', alpha=92)
        _noline(sh)
        sh.name = 'bar_%d' % i
        # 教程 s04/s05 帧实测：柱顶加一个立方体帽（复制立方体→粘贴为图片→填充进柱状图），
        # 平面柱立刻有"立方体效果"。原生 CUBE 预设拉伸成帽即可，保持可编辑。
        cap = slide.shapes.add_shape(MSO_SHAPE.CUBE, Emu(int(cx * EMU_W)),
                                     Emu(int((y + 0.12 + (max_h - bh) - 0.028) * EMU_H)),
                                     Emu(int(colw * EMU_W)), Emu(int(0.055 * EMU_H)))
        _fill(cap, _top, alpha=95)
        _noline(cap)
        cap.name = 'bar_cap_%d' % i
        # 数值条顶：白字加粗（青字压亮柱只有 2.4:1），字号按柱宽挑到 26pt
        from pptx.util import Pt
        vy = max(y, y + 0.12 + (max_h - bh) - 0.115)
        tb = slide.shapes.add_textbox(Emu(int(bx * EMU_W)), Emu(int(vy * EMU_H)),
                                      Emu(int(slot * EMU_W)), Emu(int(0.11 * EMU_H)))
        _vtxt = (txt or str(v))
        _vpt = fit_pt(_vtxt, slot, (32, 28, 26, 24, 22, 20, 18))
        _vlines = ([_vtxt] if em_width(_vtxt) <= max(0.05, slot) * 13.3333 * 72.0 / 14.0
                   else break_balanced(_vtxt, 2))
        for _j, _ln in enumerate(_vlines):
            p = tb.text_frame.paragraphs[0] if _j == 0 else tb.text_frame.add_paragraph()
            r = p.add_run()
            r.text = _ln
            r.font.size = Pt(_vpt)
            r.font.bold = True
            r.font.name = '微软雅黑'
            r.font.color.rgb = _rgb(ink.get('text') or 'FFFFFF')
        tb.name = 'bar_value_%d' % i
        ly = min(y + 0.12 + max_h + 0.012, y + h - 0.075)
        lb = slide.shapes.add_textbox(Emu(int(bx * EMU_W)), Emu(int(ly * EMU_H)),
                                      Emu(int(slot * EMU_W)), Emu(int(0.07 * EMU_H)))
        _lab = (label or u'')[:10]
        _lpt = fit_pt(_lab, slot, (13, 12, 11, 10))
        _llines = ([_lab] if em_width(_lab) <= max(0.05, slot) * 13.3333 * 72.0 / 10.0
                   else break_balanced(_lab, 2))
        for _j, _ln in enumerate(_llines):
            p2 = lb.text_frame.paragraphs[0] if _j == 0 else lb.text_frame.add_paragraph()
            rp = p2.add_run()
            rp.text = _ln
            rp.font.size = Pt(_lpt)
            rp.font.name = '微软雅黑'
            rp.font.color.rgb = _rgb(ink['muted'])
        lb.name = 'bar_label_%d' % i
        made += 1
    base = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(int(x * EMU_W)),
                                  Emu(int((y + 0.12 + max_h) * EMU_H)),
                                  Emu(int(w * EMU_W)), Emu(19050))
    _fill(base, pal.get('cyan_hex') or '66D9F7', alpha=45)
    _noline(base)
    base.name = 'bar_baseline'
    return made


def delta_cards(slide, cards, zone, pal, ink, add_text, max_n=3):
    """教程 s03 帧的指标卡：圆角面板 + 圆形图标 + 大数字(小单位) + 同比/环比带红绿箭头。

    画面实测（1e2c66eafa s03）：左列三张卡『物流总成本 8888万元 同比下降5.1%↓绿』，
    右列才是柱状图。增长/下降用不同颜色并绘箭头是讲解原话，不是可选装饰。
    cards=[(label, value_txt, delta_txt, up_bool)]，返回画了几张。"""
    from pptx.util import Emu, Pt
    cards = [c for c in (cards or []) if c and c[1]][:max_n]
    if not cards:
        return 0
    x, y, w, h = zone
    n = len(cards)
    gap = 0.02
    cw = (w - gap * (n - 1)) / float(n)
    ch = min(0.16, h)
    made = 0
    for i, (label, val, delta, up) in enumerate(cards):
        bx = x + i * (cw + gap)
        panel = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(int(bx * EMU_W)),
                                       Emu(int(y * EMU_H)), Emu(int(cw * EMU_W)),
                                       Emu(int(ch * EMU_H)))
        _fill(panel, pal.get('panel_hex') or '003069', alpha=55)
        _noline(panel)
        panel.name = 'delta_card_%d' % i
        ic = slide.shapes.add_shape(MSO_SHAPE.OVAL, Emu(int((bx + 0.012) * EMU_W)),
                                    Emu(int((y + ch / 2 - 0.028) * EMU_H)),
                                    Emu(int(0.056 * EMU_W)), Emu(int(0.056 * EMU_H)))
        _fill(ic, pal.get('accent_hex') or '016DFF', alpha=80)
        _noline(ic)
        ic.name = 'delta_icon_%d' % i
        add_text(slide, label[:6], [bx + 0.08, y + 0.012, cw - 0.10, 0.04],
                 12, color=ink['muted'], align='l', name='delta_label_%d' % i)
        add_text(slide, val[:12], [bx + 0.08, y + 0.052, cw - 0.10, 0.07],
                 26, bold=True, color=ink['text'], align='l', name='delta_value_%d' % i)
        if delta:
            col = 'E05A4E' if up else '37B26C'
            add_text(slide, delta[:10], [bx + 0.08, y + ch - 0.045, cw - 0.13, 0.04],
                     13, bold=True, color=col, align='l', name='delta_txt_%d' % i)
            ar = slide.shapes.add_shape(MSO_SHAPE.UP_ARROW if up else MSO_SHAPE.DOWN_ARROW,
                                        Emu(int((bx + cw - 0.045) * EMU_W)),
                                        Emu(int((y + ch - 0.05) * EMU_H)),
                                        Emu(int(0.028 * EMU_W)), Emu(int(0.042 * EMU_H)))
            _fill(ar, col, alpha=95)
            _noline(ar)
            ar.name = 'delta_arrow_%d' % i
        made += 1
    return made


def number_row(slide, items, zone, pal, ink, add_text, max_n=4):
    """语料签名式摆法：一排大数字 + 小一号的单位 + 数字下一行小注释，压在页底/页边。

    对照实测（360°产品介绍册 p002）：'4,997mm / 1,963mm / 1,455mm / 3,000mm' 一排
    40pt 上下的数字横在页面下沿，单位缩到一半字号跟在后面，注释 14pt 挂在数字下面。
    竖条柱状图只留给"对比"页；数据页用这一排，页面才不像没做完的草稿。
    items=[(label, value_float, 上屏原文, unit)]，返回画了几列。"""
    from pptx.util import Emu, Pt
    from pptx.enum.shapes import MSO_SHAPE
    items = [it for it in (items or []) if it and len(it) > 2][:max_n]
    if len(items) < 2:
        return 0
    x, y, w, h = zone
    n = len(items)
    gap = 0.03
    # 语料的数字卡是"一格一卡"（360°产品介绍册 p002：4 张 0.225×0.32 的承载面板），
    # 不是把页宽两等分的大色块。列宽收到 ≤0.32，整排居中，数字坐在自己的卡上。
    cw = min((w - gap * (n - 1)) / float(n), 0.32)
    x = x + max(0.0, (w - (cw * n + gap * (n - 1))) / 2.0)
    card_h = min(h, 0.26)
    made = 0
    for i, it in enumerate(items):
        label, v, txt = it[0], it[1], it[2]
        unit = it[3] if len(it) > 3 else u''
        bx = x + i * (cw + gap)
        pn = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(int(bx * EMU_W)),
                                    Emu(int(y * EMU_H)), Emu(int(cw * EMU_W)),
                                    Emu(int(card_h * EMU_H)))
        _fill(pn, pal.get('panel_hex') or '003069', alpha=58)
        _noline(pn)
        pn.name = 'num_card_%d' % i
        # 数字本体：把单位从串里剥出来单独用小字号跟排（'4,997' + 'mm'）
        val_txt = txt or u''
        if unit and val_txt.endswith(unit):
            val_txt = val_txt[:-len(unit)].strip()
        # 字号按卡片内宽挑，最大 60pt：上一版给到 72pt，72pt 的行高 0.18 已经超出
        # 0.12 的文本框，数字下半截压在注释上（真渲染页 3/48 目检：'77.65%' 与 '同期专科' 叠字）
        pt = fit_pt(val_txt + unit, cw - 0.028, (60, 52, 44, 36, 32))
        vh = min(card_h - 0.07, max(0.10, pt * 1.32 / 540.0))
        tb = slide.shapes.add_textbox(Emu(int((bx + 0.014) * EMU_W)), Emu(int(y * EMU_H)),
                                      Emu(int((cw - 0.028) * EMU_W)), Emu(int(vh * EMU_H)))
        p = tb.text_frame.paragraphs[0]
        r = p.add_run()
        r.text = val_txt
        r.font.size = Pt(pt)
        r.font.bold = True
        r.font.name = '微软雅黑'
        # 语料那排数字是白字压在图上（4,997mm 那排）；青色在亮照片上只有 2.46:1，G4 会拦
        r.font.color.rgb = _rgb(ink.get('text') or 'FFFFFF')
        if unit:
            r2 = p.add_run()
            r2.text = unit
            r2.font.size = Pt(max(14, int(pt * 0.45)))
            r2.font.bold = True
            r2.font.name = '微软雅黑'
            r2.font.color.rgb = _rgb(ink.get('text') or 'FFFFFF')
        tb.name = 'numrow_value_%d' % i
        if label and is_category_label(label):
            # 注释写在数字行下面（不是文本框下沿）：0.12 的框配 60pt 字，
            # 按框高摆注释就会叠到数字下半截上
            _lab = label[:6]
            add_text(slide, _lab, [bx + 0.014, y + vh + 0.004, cw - 0.028, 0.055],
                     fit_pt(_lab, cw - 0.028, (15, 14, 13)),
                     color=ink['muted'], align='l', name='numrow_label_%d' % i)
        made += 1
    return made


def card_grid(slide, lines, zone, pal, ink, add_text):
    """并列要点 → 卡片栅格（语料实测卡片 0.225×0.321，三列）。"""
    from pptx.util import Emu, Pt
    from pptx.enum.shapes import MSO_SHAPE
    n = len([l for l in lines if l])
    if n < 2:
        return 0
    x, y, w, h = zone
    cols = 3 if n <= 6 else 2
    rows = (n + cols - 1) // cols
    gx, gy = 0.022, 0.030
    cw = (w - gx * (cols - 1)) / float(cols)
    ch = (h - gy * (rows - 1)) / float(rows)
    made = 0
    for i, ln in enumerate([l for l in lines if l]):
        r, c = divmod(i, cols)
        bx, by = x + c * (cw + gx), y + r * (ch + gy)
        sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(int(bx * EMU_W)),
                                    Emu(int(by * EMU_H)), Emu(int(cw * EMU_W)),
                                    Emu(int(ch * EMU_H)))
        _fill(sh, pal.get('panel_hex') or '003069', alpha=72)
        _noline(sh)
        sh.name = 'card_%d' % i
        tb = add_text(slide, ln[:20], [bx + 0.012, by + 0.02, cw - 0.024, ch - 0.04],
                      16, color=ink['text'], align='l', name='card_text_%d' % i)
        made += 1
    return made


# 量词字表的唯一定义点：柱底/筹码/断头词回退/复合单位四处共用，改一处不许漏另三处。
# 约数词（多余几）单列：它们能跟数字（'50 多家'），但不能当"词头量词"去剥标签——
# 否则 '几乎重合' 这种正常词会被 LEAD_MEASURE 误杀（自测 v27 实测）
MEASURE_CHARS = u'位个家台例人次天店条项份种名'
MEASURE_APPROX = u'多余几'
LEAD_MEASURE = re.compile(u'^[' + MEASURE_CHARS + u']')
NUMRE = re.compile(r'([-+]?\d[\d,\.]*)\s*(%|％|万元|亿元|万|亿|元|台|例|人|次|天|城|店|个|倍|家)?')
VERB_PREFIX = re.compile(u'^(?:创造|带动|只拿|接近|达到|约合|换算到|服务|签约|履约|覆盖|投放|投入|'
                         u'成本|单车|每台|全年|合计|约|为|是|到|从|占)')
UNIT_RE = re.compile(r'(%|％|万元|亿元|万|亿|元|台|例|人|次|天|城|店|个|倍|家)'
                     u'([' + MEASURE_CHARS + u']{0,2})$')
DATEISH = re.compile(u'^(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月)?|\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?$')


def extend_unit(text, end, unit):
    """复合量词跟着数字走：'26 万' 后面紧跟 '人次'，单位就是 '万人次'。

    单位表里只有单字量词，正则匹配到 '万' 就停，'人次' 被留在原文里。真渲染页 6
    目检：数字排上屏 '26万'（源文 '26 万人次'），而 G24 的尺子也数 '26万'——
    截断值一路过闸，没人拦。跟排规则只有这一份，数字排/条形组/G24/hero 共用。
    unit 为空时不动：裸数字后面跟的是名词（'12 城签约'），并进去会吃掉标签词头。"""
    if not unit:
        return unit, end
    m = re.match(u'([' + MEASURE_CHARS + u']{1,2})', (text or u'')[end:])
    if m:
        return unit + m.group(1), end + m.end()
    return unit, end


def char_em_width(ch):
    """单个字符占多少 em。权重是对着 WPS 真渲染量出来的，不是"一个字算一个"：
    17 字的 24pt 标题在 0.425 画布宽的框里，模型按 1.0 em 算正好放得下（17.0），
    实际渲染却把最后一个字甩到第二行（实测页 12 的 '…去年不是这样'）。
    反推：16 字放得下 → 每个 CJK ≈ 1.06 em，留一点余量取 1.08。
    排版挑字号（fit_pt）、折行判孤字（orphan_tail）与行高（est_lines）都走这一个函数。"""
    o = ord(ch)
    if ch in (' ', u'\u00a0'):
        return 0.38
    if ch in u'.，,、；;:':
        return 0.32
    if ch in u'%':
        return 0.92          # % 看着是半角其实接近全宽（实测 110pt 的 '45.37%' 就是这么折行的）
    if o >= 0x2E80 or ch in u'（）％　':
        return 1.08
    return 0.60


def em_width(s):
    return sum(char_em_width(c) for c in (s or u''))


def fit_pt(text, box_w_frac, pts):
    """窄框（柱顶、筹码）里的短文本：挑一个能一行放下的最大字号；都放不下返回最小档。

    写死 22pt/16pt 的旧写法实测把 '126.4 万元'、'三级医院 89.6%' 顶成 3 行，
    末行只剩 '%'——G8 判孤字尾行，肉眼看到的就是一截断掉的数。"""
    t = (text or u'').strip()
    box_in = max(0.05, float(box_w_frac)) * 13.3333
    if not t:
        return pts[-1]
    for pt in pts:
        if em_width(t) <= box_in * 72.0 / pt:
            return pt
    return pts[-1]


def break_balanced(text, min_tail=3):
    """实在一行放不下：折成两行，尾行不许只剩 1–2 个字（与 G8 同一口径）。"""
    t = (text or u'').strip()
    if len(t) < min_tail * 2 + 1:
        return [t]
    k = len(t) // 2
    for off in (0, 1, 2, 3):
        for kk in (k + off, k - off):
            if min_tail <= kk <= len(t) - min_tail:
                return [t[:kk].rstrip(), t[kk:].lstrip()]
    return [t]


def chip_strip(slide, texts, zone, pal, ink, add_text, max_chips=3, chip_h=0.13):
    """把"版式没画掉的几个事实"排成一行小筹码：语料那种"很多短文本框"的写法。

    为什么不用整句：显示契约收到 ≤2 行 × ≤14 字之后，剩下的事实若按句子打上去，
    每页字数中位数就压不到语料水平（实测 v5 p50=43 字 / 语料 19 字），
    而筹码只写"数字+单位+两三个字的尾巴"，3 个事实大约 24 字，比一句话还短。
    事实本身一个没丢——G24 是逐事实核成品的，这里只是换了个更便宜的载体。"""
    from pptx.util import Emu
    from pptx.enum.shapes import MSO_SHAPE
    items = [t for t in (texts or []) if (t or '').strip()][:max_chips]
    if len(items) < 2:
        return 0
    x, y, w, h = zone
    gx = 0.018
    # 筹码按语料卡片尺寸收口（≤0.32 页宽、整排居中）：两枚各占半页宽的写法
    # 在语料里不存在，量出来 cards=0（承载面板带宽 0.08–0.35），页面读起来也是两块大色块
    cw = min((w - gx * (len(items) - 1)) / float(len(items)), 0.32)
    x = x + max(0.0, (w - (cw * len(items) + gx * (len(items) - 1))) / 2.0)
    made = 0
    for i, t in enumerate(items):
        bx, by = x + i * (cw + gx), y
        sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(int(bx * EMU_W)),
                                    Emu(int(by * EMU_H)), Emu(int(cw * EMU_W)),
                                    Emu(int(min(chip_h, h) * EMU_H)))
        _fill(sh, pal.get('panel_hex') or '003069', alpha=72)
        _noline(sh)
        sh.name = 'chip_%d' % i
        _cwt = max(0.05, cw - 0.020)
        _cpt = fit_pt(t, _cwt, (17, 16, 15, 14))
        _fits = em_width(t) <= _cwt * 13.3333 * 72.0 / _cpt
        _ch = min(chip_h, h)
        _tb = add_text(slide, t if _fits else break_balanced(t, 3),
                       [bx + 0.010, by, _cwt, _ch],
                       _cpt, color=ink['text'], align='l', name='chip_text_%d' % i)
        # 文字在卡里垂直居中：上一版贴顶，短事实（'全国妇产 45.37%'）挂在 0.13 高的
        # 面板上半截，下面一片空——看着像没排完的草稿，不是语料那种筹码
        try:
            from pptx.enum.text import MSO_ANCHOR
            _tb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        except Exception:
            pass
        made += 1
    return made


def _is_ok_lab(lab):
    try:
        import display_refine as _DR
    except Exception:
        return bool(lab)
    return bool(lab) and _DR.is_clean_phrase(lab)


# 柱底与卡片上的是"类别名"，不是句子：真渲染逐页目检抓到过 '名女性提供乳腺'、
# '女性代表捐赠健康'、'米大客是效果图'、'司机只有' 这类标签——它们是 clean_label
# 从子句里挑出来的连续中文段，字数与虚词收尾都过不了的问题它没有，但读起来是半句话，
# 挂在柱子底下就成了"这三根柱子在比三个不相干的动作"。类别名只能是短名词短语。
CATEGORY_BAD = re.compile(u'[的是为了做要按等于约近从到与和及在不给将已被把让有该等这那些其中]')
# 结尾是副词/介词/时间词的串是半句话，不是类别名（真渲染页 48 的卡上写着 '滴滴上线时只'）
CATEGORY_TAIL = u'只时都就才又还且但而或虽如若使让被把给从向于以了着过'


def is_category_label(s):
    """2–6 字、不含动词虚词、不以副词收尾的短名词短语才配当柱底/卡片类别名。"""
    s = (s or u'').strip()
    if not (2 <= len(s) <= 6):
        return False
    if CATEGORY_BAD.search(s):
        return False
    if s[-1] in CATEGORY_TAIL:
        return False
    if LEAD_MEASURE.match(s):
        return False
    return _is_ok_lab(s)


def unit_of(raw_text):
    m = UNIT_RE.search((raw_text or '').strip())
    return (m.group(1) + m.group(2)) if m else ''


def clean_label(p, drop_text=''):
    """柱底与筹码的标签只能是原文里**连续的一段字**，不许把数字两侧的文字粘起来。

    实测坑：'现场另向 30 位“三八红旗手…”抹掉那个 30 后两侧对接成
    '现场另向 位“三八红'（v10 页 24 就是这么上屏的）——屏幕上出现一句谁都没写过的话，
    G1 判"成品文字在 Word 原文里找不到"是对的，这种串比不写标签糟得多。
    现在只在数字两侧的连续中文段里挑最长的一段（2–10 字），挑不出就不画标签。

    给了 drop_text（就是这个数自己的上屏串）时先取**数字前面**那一段：标签是"这个数是什么"
    的定语。'35,000 人次配 20% 成交率' 里 '成交率' 属于 20%，不属于 35,000——旧版在整句里
    挑最长中文段，于是三根人数柱底下都挂着 '成交率'（真渲染页 20 目检）。
    数字领句、前面没有定语时，才回头看数字后面那一段，且**只许在整段不含另一个数时**用它
    （'55.0 万元设备投入' → '设备投入'；'22.03 万元之后还剩 32.97 万元' → 后面那个名词属于
    下一个数，宁可不写）。"""
    p = p or u''
    _pre, _tail = p, u''
    if drop_text:
        _i = p.find(drop_text)
        if _i == 0:
            _pre, _tail = u'', p[len(drop_text):]
        elif _i > 0:
            _pre, _tail = p[:_i], p[_i + len(drop_text):]

    def _pick(s):
        cand = []
        for run in re.findall(u'[一-龥]{2,10}', s or u''):
            r = LEAD_MEASURE.sub(u'', run)
            if len(r) >= 2:
                cand.append(r)
        if not cand:
            return u''
        # 先按动词/虚词切段再挑：整段最长的那个往往是半句话
        import display_refine as _DR
        pool = [x for x in (_DR.phrase_of(c) for c in cand) if x]
        return (max(pool, key=len) if pool else max(cand, key=len))[:10].strip(u' ·')

    lab = _pick(_pre)
    # 数字领句时前面没有定语，可以回头看数字后面那段——但**只许在不含另一个数时**：
    # '35,000 人次配 20% 成交率' 的 '成交率' 属于 20%，属于 35,000 的是 '人次'。
    # 上一轮的过度修正是"数字领句一律空标签"，把 '55.0 万元设备投入' 这种合法标签
    # 也一起抹了（三根柱子全没名字，整组图被判不出）。
    if not lab and _tail and not re.search(u'[0-9０-９]', _tail):
        lab = _pick(_tail)
    if drop_text:
        lab = lab.replace(drop_text, u'').strip()
    return lab




def items_from_numbers(phrases):
    """并列数字变条形组条目：(标签, 数值, 上屏原文, 单位)，只留最大同单位组。

    把 46.9 万元和 18,000 人次放同一张图里，柱高差 380 倍，小值会缩成一条线——
    那不是图表是误导（实测成品就这样），所以跨单位的数字宁可不画。"""
    out = []
    for p in phrases:
        _p = p or u''
        m = NUMRE.search(_p)
        if not m:
            continue
        _u, _end = extend_unit(_p, m.end(), m.group(2) or u'')
        disp = _p[m.start():_end].strip()
        if DATEISH.match(disp) or re.match(r'^(19|20)\d{2}$', disp):
            continue
        try:
            v = float(m.group(1).replace(',', ''))
        except Exception:
            continue
        lab = clean_label(_p, disp)
        # 柱底标签不许以这一串自己的单位开头：v10 实测柱底写着 '万元'、'万元设备投入'、
        # '万元之后剩'——clean_label 只剥数字（'55.0'），把紧跟其后的 '万元' 留在了词头，
        # 读起来像半句话，而且三根柱子底下都挂着同一个 '万元'。
        if _u and lab.startswith(_u):
            lab = lab[len(_u):].strip()
        # 标签不干净就留空，绝不写编号占位：逐页目检实测柱底出现 '第2项'、
        # '主办方的口径为不少' 这种半截话，比没有标签更难看
        if not is_category_label(lab):
            lab = u''
        out.append((lab, v, disp, _u))
    # 同一个数在同一页画两次就是重影：源文 '一个城市公司 12 到 15 人' 与 '一家城市公司
    # 12 到 15 人' 是同一件事说了两遍，旧版两行大数字都写 12（真渲染页 25 目检）。
    # 按（数值, 单位）去重，保留先出现的那条；后来那条若带标签而先前的没有，就补标签。
    _uniq = {}
    for it in out:
        _k = (round(it[1], 6), it[3])
        if _k not in _uniq:
            _uniq[_k] = it
        elif not _uniq[_k][0] and it[0]:
            _uniq[_k] = (it[0], _uniq[_k][1], _uniq[_k][2], _uniq[_k][3])
    out = list(_uniq.values())
    groups = {}
    for it in out:
        groups.setdefault(it[3] or u'无单位', []).append(it)
    # '无单位' 这一组最会骗人：'7 米'、'12 座'、'5070mm'、'385mm' 都没带单位，
    # 被并成同族四根柱子（v10 页 58 实测）——那是公告参数，不是可比数据。
    # 所以这一组要求每条都有类别名，且最大/最小不超过 25 倍（超过就是误导）。
    def _sane(g):
        if len(g) < 2:
            return False
        # 本函数自己的注释写的就是"这一组要求每条都有类别名"，代码却只在 ≥3 条时才查，
        # 于是 '500'、'300' 这种既无单位又无类别名的裸数字两条就能成一组柱子。
        # 按（数值, 单位）去重后条数会从 3 掉到 2，这个洞更容易踩到（159 项自测里
        # "假图表"那条反证就是这样红的）。
        if any(not it[3] for it in g) and any(not it[0] for it in g):
            return False
        if len(g) >= 3 and (sum(1 for it in g if it[0]) < len(g)):
            return False
        vs = [abs(it[1]) for it in g if it[1]]
        if vs and max(vs) / float(min(vs) or 1e-9) > 25:
            return False
        return True
    cand = [g for g in groups.values() if _sane(g)]
    best = max(cand, key=len) if cand else []
    return best if len(best) >= 2 else []
