"""Style library + prompt scaffolding, derived from the measured corpus grammar.

The three presets are transcriptions of what the decks actually ask for:
  tech_blue  <- NO.145 / NO.171 / NO.172 / NO.173 prompt pages
  ink_wash   <- NO.150 / NO.154 / NO.142
  photo_real <- NO.148 / NO.160 / NO.165 / NO.167 / NO.174
Section order is the nine-label image form and the six-part video form measured in
kb/prompt_stats.json. Drafts are a starting point: the agent must replace the subject
slots with concrete things from the document before shipping.
"""
import os, io, json, re

STYLES = {
    'tech_blue': {
        'name': '科技蓝',
        'src': 'NO.145 / NO.171 / NO.172 / NO.173 提示词页',
        'palette': '深海军蓝、藏蓝、科技蓝为主色，电光蓝、青蓝、冰蓝与少量纯白高光为辅',
        'material': '半透明玻璃材质、霓虹光纤、全息投影、数字粒子、能量光轨，边缘带明亮冰蓝霓虹描边与柔和外发光',
        'light': '中央蓝色光源、边缘轮廓光、地面反射光与体积光，形成明显明暗对比，高对比但不过曝',
        'space': '下方为未来数字化地面，带同心圆轨道、蓝色发光线条与镜面反射；顶部保持大面积干净暗色留白',
        'anchor': '高端未来科技展厅、人工智能控制中心、全息 HUD 界面，苹果发布会与企业战略发布会的高级感',
        'motion': '多层光带由细到粗、由暗到亮逐渐形成，细小粒子沿光线方向快速穿梭',
    },
    'ink_wash': {
        'name': '水墨国风',
        'src': 'NO.150 / NO.154 / NO.142',
        'palette': '墨色浓淡、宣纸米黄、朱红与描金为主色，严格控制在水墨与留白范围内，避免高饱和彩色',
        'material': '生宣纸纤维纹理，墨迹沿纸纹自然渗透晕染，丝绸飘动，金色细线描边',
        'light': '柔和顶部漫射光，墨色浓淡自然过渡，边缘留白透光，无硬阴影',
        'space': '前景大面积留白、中景主体、远景淡墨山形，三层空气透视明确',
        'anchor': '东方美学、国家级节庆宣传片、史诗级概念设计，庄重而不喧闹',
        'motion': '墨迹扩散到哪里，画面就从哪里显现出来，两层同步进行而不是先墨后画',
    },
    'photo_real': {
        'name': '电影级写实',
        'src': 'NO.148 / NO.160 / NO.165 / NO.167 / NO.174',
        'palette': '以场景自然色为主，冷色阴影与暖色高光形成电影级调色，黑位深沉、亮部通透',
        'material': '真实物理材质与表面细节，金属漆面反射环境、沥青颗粒、水面波纹与细碎高光',
        'light': '低角度自然光（日出或日落黄金时刻）产生体积光与长阴影，远山带空气透视，HDR 高动态范围',
        'space': '前景、中景、远景层次分明，强烈单点透视与空间纵深',
        'anchor': '顶级商业广告摄影与高端城市宣传片质感，超写实、8K、真实镜头感',
        'motion': '主体沿真实路径持续前进，路边景物产生适度而真实的运动视差',
    },
}


def zone_text(z):
    x0, y0, x1, y1 = z[0], z[1], z[0] + z[2], z[1] + z[3]
    return ('画面上方 0%%～%.0f%% 与下方 %.0f%%～100%% 保持低细节的干净区域；'
            '文字区为横向 %.0f%%～%.0f%%、纵向 %.0f%%～%.0f%%，'
            '该区域内部必须保持完全空白，不放任何文字、数字、字母、图标或数据'
            % (y0 * 100, y1 * 100, x0 * 100, x1 * 100, y0 * 100, y1 * 100))


OBJ_KEYS = [
    (u'一辆白色移动医疗车，侧舱门展开成诊疗窗口',
     (u'医疗车', u'车联', u'车队', u'运力', u'巴士', u'车辆', u'急救', u'120')),
    (u'一座医院门诊楼与楼前广场',
     (u'医院', u'门诊', u'院区', u'床位', u'产能', u'三级', u'二级', u'诊所')),
    (u'一间女性健康关爱诊室，检查床与柔和帘幕',
     (u'女性', u'宫颈', u'筛查', u'私密', u'生殖', u'妇科')),
    (u'一台合上的笔记本电脑旁立着账本与车辆模型',
     (u'成本', u'账', u'定价', u'毛利', u'回本', u'资金', u'投入', u'分成')),
    (u'三根高低不同的发光数据柱',
     (u'市场', u'规模', u'亿元', u'万例', u'渗透', u'增长', u'三个数字')),
    (u'一片城市天际线与街区路网',
     (u'城市', u'长沙', u'上海', u'成都', u'社区', u'街区', u'布点', u'第二城')),
    (u'三个发光节点连成的闭合三角网络',
     (u'三方', u'网络', u'平台', u'生态', u'合作', u'联盟', u'入网')),
    (u'五个发光岗位图标围成的圆环',
     (u'组织', u'团队', u'岗位', u'能力位', u'人才', u'架构')),
    (u'一圈半透明挡板围成的环形屏障',
     (u'风险', u'失败', u'壁垒', u'护城河', u'合规', u'还没搞清')),
    (u'一条发光的年轮刻度带',
     (u'时间表', u'十二个月', u'里程碑', u'节奏', u'计划', u'阶段', u'情景')),
    (u'一辆停在半开卷帘门库房里的闲置体检车，车身蒙灰',
     (u'闲置', u'没做成', u'未跑通', u'低效', u'为什么没')),
    (u'四条岔路口，其中三条被竖直光柱封住',
     (u'不走', u'不做', u'排除', u'拒绝', u'四条路')),
    (u'一本翻开的空白书，四周漂浮发光词块与连接线',
     (u'附录', u'术语', u'出处', u'数据源', u'引用')),
]

ROLE_FRAME = {
    'cover': u'主体后方展开一层环形光带，画面左右各留一块半透明容器',
    'divider': u'主体单独立在画面中央，边缘带冰蓝霓虹描边与柔和外发光',
    'data': u'主体右侧浮着两三条无字发光刻度线',
    'process': u'主体前方是一条分段的发光流程带，节点自左向右依次亮起',
    'compare': u'主体左右各有一块半透明容器，两者之间留一道光缝',
    'quote': u'主体上方垂下一束柔光，四周保持暗部',
    'statement': u'主体被一束顶光照亮，四周沉入暗部',
    'closing': u'主体后方是一条延伸向画面深处的发光道路',
    'table': u'主体旁留出成排的空白格位',
    'appendix': u'主体旁漂浮若干发光小块与连接线',
}


VISUAL_MARKERS = (u'画面', u'一辆', u'一座', u'一间', u'一片', u'一枚', u'一组', u'一本',
                  u'一叠', u'一条', u'一圈', u'一列', u'三个', u'五个', u'十个', u'四条',
                  u'十块', u'三块', u'两块', u'剖视', u'剖面')


def looks_visual(subj):
    """主体句是否已经"能直接画"：要么带画面/量词开头，要么是整句场景描述。"""
    s = (subj or u'').strip()
    return any(k in s for k in VISUAL_MARKERS)


# 主题/象征句必须是"完整短语"。路演体标题常是半句话（"是公开成交价拼出来的："、
# 单字"七"），直接截进提示词就是垃圾。这里统一清洗＋验收：洗不出合格短语就宁可
# 不写象征句、主题退回干净 fallback，也不把半句话塞给模型。
_FUNC_TAIL = u'的了把是会投持让被给向对从和与及或等到'


def clean_phrase(text, maxlen=16):
    t = re.sub(u'^第[0-9一二三四五六七八九十]+章\\s*', u'', str(text or u''))
    t = re.sub(u'^附录[A-Z一二三四五六七八九十]?\\s*', u'', t)
    t = re.sub(u'^[0-9一二三四五六七八九十]{1,3}[.、)）\\s]+', u'', t).strip()
    # "一句话：X" 这类引导冒号：真正的主题是冒号后面那句
    m = re.match(u'^[^，。：:]{1,6}[：:](.+)$', t)
    if m and len(m.group(1).strip()) >= 4:
        t = m.group(1).strip()
    # 去掉"·数字(单位)"这类序号粘数字碎片（refine 契约禁止的上屏残留，如 ·15% / ·72 人）
    t = re.sub(u'[·•]\\s*[0-9][0-9.,]*\\s*(?:%|万元|元|人|倍|个|万)?', u'', t)
    for sep in (u'，', u'。', u'、', u'；', u'：', u'(', u'（'):
        i = t.find(sep)
        if 0 < i:
            t = t[:i]
            break
    t = t.strip(u'，。、；：()（） ·•')
    # 超长整句：截到 maxlen 再砍掉硬截留下的半截数字，虚词尾交给 phrase_ok 滤。
    # 每页仍能从自己的标题拿到一个不同的尾句（G2 反重复靠它），又不会把
    # "…口径的 1"这种半截数字塞进提示词；洗不出合格短语时象征句照样省略。
    if len(t) > maxlen:
        t = re.sub(u'\\s*[0-9][0-9.,]*\\s*$', u'', t[:maxlen]).strip()
    return t


def phrase_ok(t):
    if not t or len(t) < 4:
        return False
    if t[-1] in _FUNC_TAIL:
        return False
    if re.match(u'^[0-9.,\\s万亿元%个]+$', t):
        return False
    return True


def clean_display_line(ln):
    return clean_phrase(ln, maxlen=16)


def concrete_subject(role, headline, entities, subject=None, body=None, display=None):
    """把一页的要点翻成**能画出来的具体主体**（语料句式：直陈"画面中央是…"＋象征句收尾）。

    为什么要有这一条：旧模板写"设置与「章节名」直接相关的核心视觉主体"，实测 230 张图卡里
    211 张是这种间接话、101 页的主体句逐字相同——模型只能猜，成品就是满册同一类抽象图，
    用户看到的也是"同一段提示词反复发"。这里按页面实体挑具体物件、按角色定构图，
    再用本页标题尾巴做象征句，保证每页句子不同。
    """
    text = u' '.join([(headline or u'')] + [str(e) for e in (entities or [])])
    obj = None
    # 只有"整句场景描述"（agent 逐页写好的）才原样用；章节名/标题/实体词一律当原料，
    # 从词表里挑真正能画出来的物件——实测把"一句话""中国的医院分两种"这种句子直接塞进
    # "画面中央偏下是X"里，模型照样画不出东西。
    if subject and looks_visual(subject):
        return subject if subject.startswith(u'画面') else u'画面中央偏下是%s' % subject
    for name, kws in OBJ_KEYS:
        if any(k in text for k in kws):
            obj = name
            break
    if obj is None:
        obj = u'一座由光线连起来的枢纽塔，四周伸出若干发光支线'
    frame = ROLE_FRAME.get(role) or ROLE_FRAME['statement']
    hl = (headline or u'').strip()
    bd = (body or u'').strip()
    if bd.startswith(hl):
        bd = bd[len(hl):]
    # 象征句只用"验收合格的完整短语"：先取上屏行（refine 已提炼），再退正文首句/标题；
    # 都洗不出合格短语就**不写象征句**——实测把半句话（"象征第6章那套混编采购口径会把"）
    # 塞给模型，比没有象征句更糟。
    tail = None
    for ln in (display or []):
        c = clean_display_line(ln)
        if phrase_ok(c):
            tail = c
            break
    if not tail:
        for src in (bd, hl):
            c = clean_phrase(src, maxlen=14)
            if phrase_ok(c):
                tail = c
                break
    base = u'画面中央偏下是%s，%s，远景保持低细节与干净留白' % (obj, frame)
    return base + (u'，象征%s' % tail if tail else u'')


def image_prompt(subject, style, zone, theme=None):
    st = STYLES[style]
    theme = theme or subject
    return (
        "生成一张 16:9 横版 PPT 背景图，主题为「{theme}」，整体采用{name}视觉语言，"
        "画面严格左右对称，正面平视视角，轻微广角透视。\n\n"
        "主体特征：{subj}。主体占画面宽度约 22%、高度约 34%，轮廓清晰锐利，边缘带柔和高光，"
        "作为画面唯一视觉焦点；主体形态必须符合真实结构，"
        "不得变形、不得重复、不得与背景相互穿插。\n\n"
        "构图方式：{zone}。左右两侧各设置一块半透明信息容器，约占画面宽度 28%、高度 13%，"
        "四角采用切角设计，容器内部保持干净、完整、留白，不放任何文字，仅供后期在 PPT 中叠加内容。\n\n"
        "镜头角度：正面平视，等效 24—35mm 广角，透视关系准确，避免夸张畸变。\n\n"
        "光线方向：{light}。\n\n"
        "色彩搭配：{pal}。整体色彩严格控制在上述范围内，避免其他颜色。\n\n"
        "材质细节：{mat}。物体边缘清晰、结构精确、材质真实、细节丰富。\n\n"
        "背景元素：{space}。背景元素保持低对比度，不抢夺主体视觉，不出现多余建筑、无关物体与杂乱纹理。\n\n"
        "画面风格：{anchor}。超清细节，真实物理材质，柔和全局光照，精细反射，清晰锐利，"
        "8K 视觉品质，电影级渲染。\n\n"
        "画面参数：16:9 横版，短边不低于 2160 像素，无文字，无数字，无字母，无 Logo，"
        "无水印，无字幕，无 UI 界面，无边框。\n"
    ).format(subj=subject, theme=theme, name=st['name'], zone=zone_text(zone), light=st['light'],
             pal=st['palette'], mat=st['material'], space=st['space'], anchor=st['anchor'])


def video_prompt(subject, style, zone, ref_note, secs=8, theme=None):
    st = STYLES[style]
    theme = theme or subject
    seg = max(1, int(secs) // 4)
    # ref_note is a clause the caller writes ("以附件1作为首帧"), and templates used to glue
    # another 以…作为 onto it, producing "以以附件1作为首帧作为视频首帧" in the delivery card.
    ref = (ref_note or u'以附件1作为首帧').strip()
    if not ref.startswith((u'以', u'用', u'将')):
        ref = u'以' + ref
    if u'首帧' not in ref:
        ref = ref + u'作为首帧'
    return (
        "{ref}，画面唯一的构图、透视、配色依据就是它。生成一段约 {secs} 秒、16:9 横版、超高清的"
        "「{theme}」主题 PPT 动态背景视频。整体延续首帧的{name}风格，严格保持首帧画面的构图、"
        "主体位置与色彩不变，只允许下列指定元素运动。\n\n"
        "【0—{a} 秒：起势】镜头固定，画面处于低亮度待机状态，顶部光带从两侧向中央逐渐点亮，"
        "背景中的细小粒子开始缓慢漂浮，主体边缘的霓虹描边逐段激活。\n\n"
        "【{a}—{b} 秒：展开】以主体为中心，光线沿水平方向匀速向外扫过，依次激活两侧信息容器的边框；"
        "容器内部必须始终保持完全空白，不生成任何文字、数字、字母、图标、数据或图表，"
        "它们只作为后期 PPT 文字的占位区域。\n\n"
        "【{b}—{c} 秒：成形】{motion}；同时光线与粒子沿透视方向向画面中心汇聚，形成明显的空间纵深感，"
        "画面从静态背景转为有呼吸感的信息舞台。\n\n"
        "【{c}—{secs} 秒：定格收束】所有动态逐渐平息，最后约 0.5 秒画面稳定停留，不再增加任何新元素，"
        "最终画面必须与首帧参考图的构图和元素位置精准匹配，适合直接作为 PPT 页面背景长时间停留。\n\n"
        "【动作与顺序的硬性要求】以上四个阶段必须严格按时间顺序依次执行，不得同时发生、"
        "不得跳过任何阶段、不得提前点亮后续元素；前一阶段完成后才能进入下一阶段。\n\n"
        "【镜头与运动约束】镜头全程保持稳定平滑，只做极缓慢的推进（幅度不超过画面的 5%），"
        "不推拉突变、不旋转、不变焦、不摇移、不穿模；运动克制、高级、干净，"
        "避免剧烈抖动、画面跳切与快速闪白。\n\n"
        "【严格限制】全片禁止出现任何文字、数字、字母、Logo、水印、字幕、标题与装饰性字符，"
        "也不要生成文字占位符；不要新增人物、车辆、建筑等首帧中不存在的物体；"
        "不要改变主体形状与位置。\n\n"
        "【负面提示词】低清晰度，模糊，噪点，过度曝光，色彩脏乱，塑料质感，卡通风格，手绘插画，"
        "文字乱码，水印，Logo，边框，主体变形，结构断裂，透视错误，元素重复，相互穿插，"
        "画面闪烁，镜头剧烈抖动，跳切，多余物体。\n\n"
        "画面比例：16:9 横版；视频时长：{secs} 秒；画质：4K 超清，电影级渲染。\n"
    ).format(ref=ref, secs=secs, a=seg, b=seg * 2, c=seg * 3, theme=theme,
             name=st['name'], motion=st['motion'])


def gif_prompt(subject, style, zone, ref_note, secs=4):
    """动图（GIF）提示词：语料里 162 个 GIF 全是"元素动效"，不是整页背景。

    与视频段的两处关键差别必须写死：① 无缝循环（否则放映时播一次就停）；
    ② **透明底**（G11 实测：不透明底叠在矢量色场上就是一块方框；只有身后是暗色实景时才可退纯黑底）。
    """
    st = STYLES[style]
    seg = max(1, int(secs) // 3)
    ref = (ref_note or u'以附件1为底图').strip()
    return (
        "{ref}，生成一段 {secs} 秒、可无缝循环的「{subj}」动效元素 GIF，"
        "尺寸 1138x640 或 800x800，帧率 25fps，共约 {frames} 帧。"
        "该元素将叠加在 PPT 背景之上，因此除主体外必须是**透明背景**（带 alpha 或 GIF 透明索引），"
        "边缘干净，不带边框、不带投影残留；只有当本页背景是暗色实景照片时才允许退化为纯黑 #000000 底。\n\n"

        "【0—{a} 秒】{motion}从画面外向内进入，起始状态几乎不可见（不透明度 0→35%），"
        "细节与首帧参考图完全一致，不改变主体形状。\n\n"
        "【{a}—{b} 秒】运动到达最饱满状态：粒子、光迹或墨迹扩散到最大范围，"
        "亮度与色彩沿用{name}的配色闭集，不出现其他颜色。\n\n"
        "【{b}—{secs} 秒】运动逐渐回收，最后约 0.4 秒画面必须回到与第 0 帧几乎相同的状态，"
        "保证循环播放时看不到接缝；不得在结尾留下与开头不同的元素位置。\n\n"
        "【运动约束】镜头固定不移动，元素整体位移幅度不超过画面的 12%；"
        "不变焦、不旋转整幅画面、不出现画面跳切与快速闪白；运动克制、连续、可循环。\n\n"
        "【严格限制】禁止出现任何文字、数字、字母、Logo、水印、字幕与装饰性字符；"
        "不要生成文字占位框；不要新增参考图中没有的人物、车辆、建筑；不要改变主体形状。\n\n"
        "【负面提示词】低清晰度，模糊，噪点，色彩脏乱，塑料质感，卡通贴纸感，文字乱码，水印，"
        "白边黑边，边缘锯齿，循环接缝，跳帧，画面闪烁，镜头抖动，主体变形，元素重复。\n\n"
        "【画面参数】透明背景 GIF；时长 {secs} 秒；帧率 25fps；短边不低于 640；"
        "首尾帧一致以便无缝循环。\n"
    ).format(ref=ref, secs=secs, frames=secs * 25, a=seg, b=seg * 2,
             subj=subject, name=st['name'], motion=st['motion'])


def negative_prompt(style):
    st = STYLES[style]
    return ("低清晰度，模糊，噪点，过度曝光，欠曝，色彩脏乱，塑料质感，卡通风格，扁平插画，"
            "手绘感，文字乱码，字幕，Logo，水印，边框，主体变形，结构断裂，透视错误，"
            "元素重复，相互穿插，画面闪烁，镜头剧烈抖动，跳切，多余物体，杂乱背景，"
            "与{pal}冲突的色相。\n").format(pal=st['palette'].split('为主色')[0])


ABSTRACT = ('份额', '大盘', '路径', '目标', '情况', '下一步', '问题', '要求', '内容', '部分',
            '数据', '工作', '项目', '相关', '系统', '平台', '增长', '提升', '落地', '复盘',
            '战略', '规划', '阶段', '环节', '要点', '成果', '能力', '机制', '体系', '流程')

# A subject is only unusable when the abstract word IS the head noun. "全息数据立方体" is
# perfectly drawable; judging it abstract because it contains "数据" was a false positive that
# silently left page 2 with no prompt at all.
CONCREET_HEAD = re.compile(
    u'(立方体|建筑|大楼|楼宇|城市|天际线|门店|诊所|医院|工厂|仓库|车间|公路|道路|铁路|列车|'
    u'船舶|港口|机场|飞机|火箭|风车|热气球|帆船|山脉|河流|海洋|沙漠|森林|花园|公园|广场|'
    u'桌面|屏幕|显示器|手机|平板|键盘|服务器|机房|实验室|仪器|器械|图表|曲线|柱状|折线|饼图|'
    u'光带|光轨|光环|粒子|箭头|路径图|卷轴|扇子|花瓣|水墨|书法|印章|灯笼|红旗|桥梁|塔|'
    u'cube|skyline|building|clinic|factory|warehouse|highway|rail|ship|harbor|airport|rocket|'
    u'windmill|balloon|sailboat|mountain|river|ocean|forest|garden|plaza|screen|laptop|server|'
    u'laboratory|instrument|chart|curve|ribbon|arrow|scroll|fan|petal|ink|seal|lantern|bridge|tower)')


def is_abstract(subj):
    subj = (subj or '').strip()
    if not subj:
        return True
    if CONCREET_HEAD.search(subj):
        return False
    if len(subj) <= 6 and any(x in subj for x in ABSTRACT):
        return True
    # 指标名（率/额/值/比/度）本身画不出东西：履约率、毛利率、转化额都属于这类
    if len(subj) <= 6 and subj[-1] in '率额值比度':
        return True
    # no concrete noun and the tail is an abstract word -> nothing to draw
    tail = subj[-2:]
    return tail in ABSTRACT or any(tail in x for x in ABSTRACT)


def register():
    """Return the scaffold command. caofeng is imported lazily so there is no import cycle."""
    def cmd_scaffold(a):
        import caofeng as C
        wp = C.work_paths(a.work)
        outline = C.jr(wp['outline']) or C.die("先跑 plan")
        shots = C.jr(wp['shots']) or C.die("先跑 plan")
        style = a.style
        pp = wp['promptpack']
        man = {}
        mp = os.path.join(wp['materials'], 'manifest.jsonl')
        if os.path.exists(mp):
            for ln in io.open(mp, encoding='utf-8'):
                try:
                    r = json.loads(ln)
                    man[r['page']] = r
                except Exception:
                    pass
        override = {}
        sf = getattr(a, 'subjects', None)
        if sf and os.path.exists(sf):
            for k, v in json.load(io.open(sf, encoding='utf-8')).items():
                if str(k).isdigit():
                    override[int(k)] = v
        for kv in (a.subject or []):
            if '=' in kv:
                k, v = kv.split('=', 1)
                if k.strip().isdigit():
                    override[int(k.strip())] = v.strip()
        made = 0
        pruned_no_material = 0
        pruned_kind = 0
        auto_visual = 0

        for s in shots['pages']:
            n = s['page']
            pg = outline['pages'][n - 1]
            # 分镜里 agent 改过的 subject 必须被尊重：SKILL 的流程就是"逐页改写 shots.json 的主体"，
            # 以前 pack 只认 --subject/--subjects，把 shots.json 里写好的主体当不存在，
            # 于是 agent 按提示改完仍被"主体是抽象词"打回（本条是修链路，不是加功能）
            subj = override.get(n)
            if not subj:
                _saved = s.get('subject')
                # subject_auto 标记的是上一轮自动生成的句子：原料（标题/上屏行）可能已被
                # refine 改过，每轮重算才能去掉旧的半句话尾巴；agent 手写的主体（无此标记）
                # 必须原样尊重。
                if _saved and not s.get('subject_auto'):
                    subj = _saved
            if not subj:
                if s['role'] in ('cover', 'closing'):
                    # the cover's subject is the deck itself; entities[0] is whatever word
                    # happened to rank first and produced "三条增长路径" on a title page
                    subj = outline['title']
                else:
                    ents = pg.get('entities') or []
                    subj = ents[0] if ents else pg['headline']
            # 主体不"能直接画"就按页面要点自动翻成具体画面（语料句式）；
            # 抽象主体一律不许进提示词——实测那样 100 多页会出同一类抽象图
            if not looks_visual(subj):
                subj = concrete_subject(s['role'], pg.get('headline'), pg.get('entities'),
                                        subject=subj, body=(pg.get('body') or [u''])[0],
                                        display=s.get('display_lines'))
                s['subject_auto'] = True
                auto_visual += 1
            # 图文重排在分镜阶段就定下来：静帧素材的读数据页按语料实测原型分栏
            # （图落右栏 0.425~0.931，文字落母版占位符那一栏），提示词的留白契约与装配
            # 用同一个栏位，G5/G17 量的才是同一件事
            if s.get('layout') is None and s['role'] in ('statement', 'quote') \
                    and s.get('asset_kind') in ('image', 'video_bg'):
                # 陈述页/引文页配了整页底图，装配就是满幅压字（deck_measure 实测判 full-bleed）；
                # 继续声明 centred-statement 会被 G17 判成"计划与成品不一致"
                s['layout'] = 'full-bleed'
            elif s.get('layout') is None and s.get('asset_kind') == 'image' \
                    and s['role'] in ('data', 'process', 'compare'):
                s['layout'] = 'split-centre-image'
                s['reserved_zone'] = list(C.LAYOUT_ZONE['split-centre-image']['zone'])
            z = s.get('reserved_zone') or C.zone_for(s['role'])
            # 提示词种类必须与 asset_kind 一致（SPEC §2 S4）：按角色再 OR 一次会让
            # 已经改判静帧的隔页也拿到 video 提示词，G11 就报「写了提示词却没产物」
            is_motion = s.get('asset_kind') == 'video_bg'
            s['subject'] = subj          # record it even when rejected, so the fix is actionable
            needs_material = s.get('asset_kind') in ('image', 'video_bg', 'gif')
            if needs_material and is_abstract(subj):
                s['subject_needs_review'] = True
                print('  页%-3d 主体「%s」是抽象词，画不出东西：请用 --subject "%d=具体场景词" 重跑'
                      % (n, subj, n))
                continue
            if not needs_material:
                # 矢量色场页/表格页不需要外部素材：留着提示词会让投放卡叫人白跑一次模型，
                # 也会让 G11 把这页判成"写了提示词却没产物"。这里直接清掉旧文件。
                s['need_material'] = False
                for kind in ('image', 'first', 'last', 'video', 'gif', 'negative'):
                    fp = os.path.join(pp, 'p%03d_%s.txt' % (n, kind))
                    if os.path.exists(fp):
                        os.remove(fp)
                        pruned_no_material += 1
            else:
                _chapters = outline.get('chapters') or []
                _ch_name = (_chapters[s['chapter'] - 1]
                            if s.get('chapter') and s['chapter'] <= len(_chapters) else None)
                # 主题 likewise 只要完整短语：上屏行→章节名→标题→deck 标题，逐级清洗验收；
                # 路演体半句话标题（"是公开成交价拼出来的："、单字"七"）洗不出就退回上一级，
                # 绝不把半句话写进「主题为…」
                _theme = None
                _srcs = list(s.get('display_lines') or pg.get('display_lines') or [])
                _srcs += [_ch_name, pg.get('headline'), outline.get('title')]
                for _src in _srcs:
                    _c = clean_phrase(_src, maxlen=14)
                    if phrase_ok(_c):
                        _theme = _c
                        break
                if not _theme:
                    _theme = clean_phrase(outline.get('title'), maxlen=14) or u'本页要点'
                body_txt = image_prompt(subj, style, z, theme=_theme)
                # 多图栅格页：一张提示词出 N 张图不够，得逐块给主体词。
                # 主体取本页的分句/实体，缺够数就用"同一主题的 N 个侧面"占位并要求人工替换。
                if s.get('layout') in ('photo-wall', 'card-grid', 'timeline-on-line'):
                    n_t = int(s.get('assets') or (6 if s['layout'] == 'photo-wall' else 3))
                    subs = [x for x in (pg.get('body') or []) if x][:n_t]
                    if not subs:
                        subs = [subj] * n_t
                    while len(subs) < n_t:
                        subs.append(subs[-1])
                    lines = []
                    for i, sb in enumerate(subs, 1):
                        lines.append('%d. %s' % (i, str(sb)[:40]))
                    body_txt += ('\n\n本组共 %d 张图（%s 原型，图块尺寸按语料实测中位），'
                                 '逐张主体如下，装配时按左到右、上到下的顺序放进 gen/p%03d/'
                                 'image_1.jpg ... image_%d.jpg：\n%s' % (
                                     n_t, s['layout'], n, n_t, '\n'.join(lines)))
                io.open(os.path.join(pp, 'p%03d_image.txt' % n), 'w', encoding='utf-8').write(
                    body_txt)

                io.open(os.path.join(pp, 'p%03d_negative.txt' % n), 'w', encoding='utf-8').write(
                    negative_prompt(style))
                made += 2
            if s.get('asset_kind') == 'gif':
                ref = (('已取到的素材 %s' % man[n]['file']) if n in man
                       else '本页文生图结果（作为附件1）')
                io.open(os.path.join(pp, 'p%03d_gif.txt' % n), 'w', encoding='utf-8').write(
                    gif_prompt(subj, style, z, ref, secs=max(3, int(a.secs) // 2)))
                made += 1
            elif needs_material and is_motion:

                ref = ('已取到的素材 %s' % man[n]['file']) if n in man else '本页文生图结果（作为附件1）'
                # 视频提示词里嵌的主题与静帧共用 _theme（同一把尺，SPEC §25）：
                # 旧代码 (headline)[:14] 硬截，实测会写出「「七」主题 PPT 动态背景视频」
                # 这种半句话让模型瞎猜
                io.open(os.path.join(pp, 'p%03d_video.txt' % n), 'w', encoding='utf-8').write(
                    video_prompt(subj, style, z, ref, secs=a.secs, theme=_theme))
                made += 1
            s['subject'] = subj
            s['style'] = style
            if not s.get('shot_intent'):
                s['shot_intent'] = ('%s 页：%s背景铺满画布，文字只落在预留留白区；'
                                    '开场用平滑推镜，主体运动交给视频' % (s['role'], STYLES[style]['name']))
            if not s.get('motion'):
                s['motion'] = ('mediacall playFrom(0.0)，文字 delay 6000ms 后 fade 进来'
                               if is_motion else 'morph byObject 2000ms')
            if is_motion and n not in man:
                s['first_frame_from'] = 'image'
            # 提示词的"种类"必须和这页真正要产的东西一致。实测坑：把页 1 从 video_bg 改成 image 之后，
            # 旧的 p001_video.txt 还躺在 promptpack 里，投放卡就叫人去跑一段根本不会嵌进成片的视频。
            keep_kinds = {'image': ('image', 'negative'),
                          'video_bg': ('image', 'first', 'last', 'video', 'negative'),
                          'gif': ('image', 'negative', 'gif')}.get(s.get('asset_kind'), ())
            for kind in ('image', 'first', 'last', 'video', 'gif', 'negative'):
                fp = os.path.join(pp, 'p%03d_%s.txt' % (n, kind))
                if os.path.exists(fp) and kind not in keep_kinds:
                    os.remove(fp)
                    pruned_kind += 1

            # 平滑是这套语料的第二主力（morph 192 次）：隔页与紧随其后的内容页默认给运镜，
            # 同一素材的相邻页也连上。不给 morph 的片子会退化成静帧拼贴。
            # 表格页除外：一页只容得下一个 p:transition，这些页的开场交给 p:checker（table-motion），
            # 台账连续分页靠"表头每页重复"衔接。
            if not s.get('morph_from_prev') and s['role'] != 'table':
                prev = shots['pages'][shots['pages'].index(s) - 1] if shots['pages'].index(s) else None
                if s['role'] == 'divider' or (prev and prev.get('reserved_zone') == z
                                              and s.get('asset_kind') == prev.get('asset_kind')):
                    s['morph_from_prev'] = True
                    s.setdefault('morph_dur_ms', 2000)
                    s['needs_anchor'] = True

            # 技法卡必须按 build 真会写的东西来声明：G6 会拿 RECIPE_TOKENS 在这页的成品 XML 里
            # 数 token。以前这里按"角色"猜（is_motion 含 cover/divider/closing），于是给根本没视频的
            # 页发了 video-fullbleed 卡、给没有大数字的色场页发了 giant-numeral 卡——文档与成品脱节。
            # giant-numeral 的判据与 place_text 用同一个正则（C.HERO_BIG）：正文首句里那个数
            # 就是会被排成 150pt 的数，所以两处结论一致。
            if not s.get('tech_recipe'):
                if s.get('asset_kind') == 'gif':
                    s['tech_recipe'] = 'gif-motion-element'
                elif s.get('asset_kind') == 'video_bg':
                    s['tech_recipe'] = 'video-fullbleed'
                elif s['role'] == 'table':
                    s['tech_recipe'] = 'table-motion'
                elif s['role'] == 'statement' and C.HERO_BIG.search(
                        pg['headline'] + ' ' + ' '.join([x for x in (pg.get('body') or []) if x][:1])):
                    s['tech_recipe'] = 'giant-numeral'
                elif s.get('morph_from_prev'):
                    s['tech_recipe'] = 'morph-camera'
                else:
                    s['tech_recipe'] = 'mask-dim'
        C.jw(wp['shots'], shots)
        print("scaffold：写了 %d 份提示词草稿（风格=%s，源自 %s）" % (
            made, style, STYLES[style]['src']))
        if auto_visual:
            print(u"  其中 %d 页的主体原本画不出东西（章节名/抽象词），已按语料句式自动翻成"
                  u"具体画面（shots.json 里标 subject_auto=true）；agent 要在这基础上逐页改得"
                  u"更贴文档实体" % auto_visual)
        if pruned_no_material:
            print("  清掉 %d 份提示词：这些页是矢量色场/表格页，本来就不需要外部素材" % pruned_no_material)
        if pruned_kind:
            print("  清掉 %d 份与页面素材类型不符的提示词（例如把视频页改成图片页后留下的 video 段）"
                  "——留着会让投放卡叫人跑不会嵌进成片的产物" % pruned_kind)


        print("  草稿里的「主体」槽位必须逐页换成文档里的具体事物，再交给 pack 校验。")
        print("  留白区已按 shots.json 的 reserved_zone 写进提示词，装配时文字层不许越出。")
    return cmd_scaffold
