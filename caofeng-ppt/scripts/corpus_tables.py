"""Measured corpus tables: colour families, layout archetypes, animation presets.

Every number in here was read out of the 171 template decks by study_style_anim.py and is
reconciled cell-by-cell against kb/color_system.json, kb/layout_archetypes.json and
kb/animation_mechanics.json by check_docs.py. Nothing in this file is a design preference:
'evidence' is a measurement, and 'note' marks the places where the engine deliberately does
something the corpus itself never did (so the docs cannot pass it off as inherited practice).

作者定的取舍：只沉淀好示范页的参数与分布，广告页/文字墙/字压亮底/模板编号主视觉
这类非模版元素一律不学；度量口径保持与语料挖掘一致，便于成品同级打分复用。
"""

# ---------------------------------------------------------------- 配色 -----------------------
# 语料事实：171 册里 155 册"用得多"的彩色是蓝；主题色板却 167 册原封不动沿用 Office 默认
# (accent1=4472C4)。颜色是逐形状直写 srgbClr（35327 次）并由 lumMod/lumOff 从主题色派生
# （text|lumMod 2758 次 + text|lumOff 964 次），不是靠改 theme1.xml。
# 本技能多做的两件事：① 把选定色板写进 theme1.xml 的 accent1..6，让"换一个色板=整片换色"成立；
# ② 用 G16 逐页核对成品里出现的彩色必须落在色板内。这两条都写进文档，不冒充语料传统。
PALETTES = {
    'tech_blue': {
        'name': u'科技蓝青',
        'evidence': {
            'family_decks': 155, 'lead_4472C4_decks': 78, 'lead_086DB5_decks': 64,
            'text_4472C4': 2672, 'text_FFFFFF': 1361, 'text_086DB5': 1120,
            'fill_003069': 84, 'fill_00FFFF': 170, 'fill_009EFA': 117,
            'line_016DFF': 226, 'line_66D9F7': 96,
            'grad_2548FE_0388FE_01F6FD': 327, 'grad_2_stops': 6136, 'grad_ang_5400000': 7111,
        },
        'deep': (9, 25, 43), 'mid': (8, 109, 181), 'hi': (102, 217, 247),
        'base_hex': '4472C4', 'text_hex': 'FFFFFF', 'title_hex': '4472C4',
        'accent_hex': '016DFF', 'cyan_hex': '66D9F7', 'gold_hex': 'FFC000',
        'panel_hex': '003069', 'dark_hex': '09192B',
        'grad': [(0, '2548FE'), (50000, '0388FE'), (100000, '01F6FD')], 'grad_ang': '5400000',
        'theme': {'dk1': '09192B', 'lt1': 'FFFFFF', 'dk2': '191A1B', 'lt2': 'E4E6EA',
                  'accent1': '4472C4', 'accent2': '086DB5', 'accent3': '016DFF',
                  'accent4': '66D9F7', 'accent5': 'FFC000', 'accent6': '003069'},
    },
    'ink_wash': {
        'name': u'宣纸水墨',
        'evidence': {
            'fill_030303': 478, 'fill_202020': 478, 'text_2F2F2F': 322,
            'text_9F070E': 107, 'grad_F8F8F3_F8E8B7': 176, 'line_F5DC46': 54,
            'gold_FCDC4A_decks': 1,
        },
        'deep': (248, 248, 243), 'mid': (248, 232, 183), 'hi': (159, 7, 14),
        'base_hex': 'F8F8F3', 'text_hex': '2F2F2F', 'title_hex': '030303',
        'accent_hex': '9F070E', 'cyan_hex': 'FCDC4A', 'gold_hex': 'F5DC46',
        'panel_hex': '202020', 'dark_hex': '030303',
        'grad': [(0, 'F8F8F3'), (100000, 'F8E8B7')], 'grad_ang': '5400000',
        'theme': {'dk1': '030303', 'lt1': 'F8F8F3', 'dk2': '202020', 'lt2': 'E8E2D2',
                  'accent1': '9F070E', 'accent2': 'FCDC4A', 'accent3': 'F5DC46',
                  'accent4': '2F2F2F', 'accent5': '786E5C', 'accent6': 'A50E13'},
    },
    'photo_real': {
        'name': u'电影写实暖金',
        'evidence': {
            'fill_191A1B': 956, 'fill_000000': 1010, 'text_FFC000': 141,
            'grad_F8F8F3_F8E8B7': 176, 'line_FFFFFF': 55,
        },
        'deep': (10, 12, 18), 'mid': (34, 44, 62), 'hi': (255, 192, 0),
        'base_hex': 'FFC000', 'text_hex': 'FFFFFF', 'title_hex': 'FFFFFF',
        'accent_hex': 'FFC000', 'cyan_hex': '66D9F7', 'gold_hex': 'FCDC4A',
        'panel_hex': '191A1B', 'dark_hex': '030303',
        'grad': [(0, '191A1B'), (100000, 'FFC000')], 'grad_ang': '5400000',
        'theme': {'dk1': '0B0C12', 'lt1': 'FFFFFF', 'dk2': '191A1B', 'lt2': 'D9D9D9',
                  'accent1': 'FFC000', 'accent2': 'F5DC46', 'accent3': '4E6172',
                  'accent4': '66D9F7', 'accent5': '2F2F2F', 'accent6': 'A50E13'},
    },
    'business_light': {
        'name': u'亮白商务',
        'evidence': {
            'grad_FEFEFD_F3F8FE_97D0FE': 692, 'text_4E6172': 416, 'fill_E4E6EA': 478,
            'fill_ADB5BF': 478, 'grad_D1D1D3': 956, 'grad_3_stops': 3018,
        },
        'deep': (254, 254, 253), 'mid': (228, 230, 234), 'hi': (151, 208, 254),
        'base_hex': '4472C4', 'text_hex': '4E6172', 'title_hex': '086DB5',
        'accent_hex': '97D0FE', 'cyan_hex': 'F3F8FE', 'gold_hex': 'FFC000',
        'panel_hex': 'E4E6EA', 'dark_hex': '4E6172',
        'grad': [(0, 'FEFEFD'), (50000, 'F3F8FE'), (100000, '97D0FE')], 'grad_ang': '5400000',
        'theme': {'dk1': '2F2F2F', 'lt1': 'FEFEFD', 'dk2': '4E6172', 'lt2': 'E4E6EA',
                  'accent1': '4472C4', 'accent2': '086DB5', 'accent3': '97D0FE',
                  'accent4': 'ADB5BF', 'accent5': 'FFC000', 'accent6': '6B95F2'},
    },
    'party_red': {
        'name': u'党建红金',
        'evidence': {
            'family_decks': 4, 'text_9F070E': 107, 'fill_A50E13': 42,
            'text_FFC000': 141, 'line_F5DC46': 54, 'red_FF0000_decks': 1,
        },
        'deep': (25, 26, 27), 'mid': (165, 14, 19), 'hi': (255, 192, 0),
        'base_hex': '9F070E', 'text_hex': 'FFFFFF', 'title_hex': 'FFC000',
        'accent_hex': 'F5DC46', 'cyan_hex': 'FFC000', 'gold_hex': 'FFC000',
        'panel_hex': 'A50E13', 'dark_hex': '191A1B',
        'grad': [(0, '9F070E'), (100000, 'A50E13')], 'grad_ang': '5400000',
        'theme': {'dk1': '191A1B', 'lt1': 'FFFFFF', 'dk2': '9F070E', 'lt2': 'F8E8B7',
                  'accent1': '9F070E', 'accent2': 'A50E13', 'accent3': 'FFC000',
                  'accent4': 'F5DC46', 'accent5': 'FCDC4A', 'accent6': '2F2F2F'},
    },
    'neon_cyan': {
        'name': u'霓虹青蓝',
        'evidence': {
            'family_decks': 3, 'grad_00FFFF': 1483, 'fill_00FFFF': 170,
            'fill_009EFA': 117, 'line_00FFFF': 39, 'grad_00FFFF_pair': 423,
        },
        'deep': (3, 3, 3), 'mid': (0, 158, 250), 'hi': (0, 255, 255),
        'base_hex': '009EFA', 'text_hex': 'FFFFFF', 'title_hex': '00FFFF',
        'accent_hex': '016DFF', 'cyan_hex': '66D9F7', 'gold_hex': 'FFC000',
        'panel_hex': '030303', 'dark_hex': '030303',
        'grad': [(0, '00FFFF'), (100000, '009EFA')], 'grad_ang': '0',
        'theme': {'dk1': '030303', 'lt1': 'FFFFFF', 'dk2': '003069', 'lt2': 'E4E6EA',
                  'accent1': '00FFFF', 'accent2': '009EFA', 'accent3': '016DFF',
                  'accent4': '66D9F7', 'accent5': 'FFC000', 'accent6': '4472C4'},
    },
}

# 派生色机制（实测）：语料不写死浅色，而是 accent + lumMod/lumOff。
# 最常用的两档：lumMod<25% + lumOff>=75%（2753 / 2711 次）= 主题色的淡色洗底；
# lumMod 50-75%（2004 次）= 主题色压深。蒙版/渐变用低 alpha：alpha<25% 2839 次、25-50% 2443 次。
DERIVE = {
    'wash': {'lumMod': 20000, 'lumOff': 80000},     # 淡色洗底，语料第一档
    'deep': {'lumMod': 65000},                      # 压深，语料第二档
    'scrim_alpha_low': 25000, 'scrim_alpha_mid': 45000,
    'evidence': {'lumMod_lt25': 2753, 'lumMod_50_75': 2004, 'lumOff_75_100': 2711,
                 'alpha_lt25': 2839, 'alpha_25_50': 2443, 'grad_alpha': 3911,
                 'fill_alpha': 743},
}

# ---------------------------------------------------------------- 版式 -----------------------
# 2224 页逐页实测（deck_measure.classify_page 的判定，成品闸门用的是同一个函数）。
# box = [x, y, w, h] 画布比例；pic/zone 取该原型下所有页的中位数。
LAYOUTS = {
    'full-bleed': {
        'pages': 608, 'pct': 27.3, 'good_pages': 487, 'good_pct': 58.3,
        'pic': [0.0, 0.0, 1.0, 1.0],
        'title': [0.04, 0.284, 0.323, 0.162],
        'over_pic_pct': 99.2, 'max_pt_median': 66.0,
        'use': u'封面/章片/金句：整幅图压满，文字直接压在图上（语料 99.2% 的字都在图上）',
    },
    'split-centre-image': {
        'pages': 504, 'pct': 22.7, 'good_pages': 49, 'good_pct': 5.9,
        'pic': [0.325, 0.172, 0.352, 0.535],
        'title': [0.071, 0.067, 0.446, 0.233],
        'over_pic_pct': 0.4, 'max_pt_median': 80.0,
        'use': u'主体图居中、标题与大段文字留在两侧四角（图上不压字，读数据页首选）',
    },
    'split-left-image': {
        'pages': 50, 'pct': 2.2, 'good_pages': 5, 'good_pct': 0.6,
        'pic': [0.0, 0.15, 0.45, 0.70],
        'title': [0.50, 0.12, 0.44, 0.16],
        'over_pic_pct': 21.2, 'max_pt_median': 40.0,
        'use': u'图靠左、文字靠右列（章节人物/产品页）',
    },
    'photo-wall': {
        'pages': 51, 'pct': 2.3, 'good_pages': 24, 'good_pct': 2.9,
        'pic': [0.201, 0.281, 0.097, 0.126],
        'title': [0.252, 0.386, 0.159, 0.063],
        'over_pic_pct': 23.2, 'max_pt_median': 44,
        'use': u'照片墙：>=6 张小图拼贴，说明文字 14pt 量级贴在格间',
    },
    'card-grid': {
        'pages': 8, 'pct': 0.4, 'good_pages': 4, 'good_pct': 0.5,
        'pic': [0.188, 0.221, 0.225, 0.321],
        'title': [0.064, 0.536, 0.235, 0.076],
        'over_pic_pct': 21.4, 'max_pt_median': 26.0,
        'use': u'等尺寸卡片栅格，文字统一落在卡片下方',
    },
    'timeline-on-line': {
        'pages': 7, 'pct': 0.3, 'good_pages': 5, 'good_pct': 0.6,
        'pic': [0.36, 0.273, 0.049, 0.087],
        'title': [0.095, 0.378, 0.132, 0.157],
        'over_pic_pct': 57.1, 'max_pt_median': 96,
        'use': u'>=3 个等大节点排成一条直线（引导线时间轴，节点沿图里的尾迹/公路/光带）',
    },
    'icon-accent': {
        'pages': 58, 'pct': 2.6, 'good_pages': 13, 'good_pct': 1.6,
        'pic': [0.475, 0.217, 0.05, 0.089],
        'title': [0.303, 0.39, 0.258, 0.103],
        'over_pic_pct': 4.7, 'max_pt_median': 20,
        'use': u'小图标点缀（<0.22 画布），正文占主区',
    },
    'centred-statement': {
        'pages': 6, 'pct': 0.3, 'good_pages': 6, 'good_pct': 0.7,
        'pic': None,
        'title': [0.10, 0.30, 0.80, 0.40],
        'over_pic_pct': 0.0, 'max_pt_median': 180.0,
        'use': u'纯文字大字号居中页，实测中位 180pt——巨型数字的依据',
    },
    'text-block': {
        'pages': 114, 'pct': 5.1, 'good_pages': 19, 'good_pct': 2.3,
        'pic': None,
        'title': [0.09, 0.245, 0.459, 0.094],
        'over_pic_pct': 0.0, 'max_pt_median': 30.0,
        'use': u'无图正文块/附表页：文字左起 0.09，行宽约 0.46 画布',
    },
}

# 母版与版式里的占位符几何（3814 个 body + 1712 个 title + 498 ctrTitle 实测中位框）。
# 这是语料自己的"图文重排模板"：正文在左 45%，图在右 50%，两者不重叠。
PLACEHOLDER_SLOTS = {
    'title': [0.0689, 0.0667, 0.4458, 0.2333],
    'body': [0.0689, 0.3, 0.4251, 0.5373],
    'pic': [0.4251, 0.144, 0.5062, 0.7106],
    'ctrTitle': [0.125, 0.1637, 0.75, 0.3481],
    'subTitle': [0.125, 0.5252, 0.75, 0.2414],
    'counts': {'body': 3814, 'title': 1712, 'ctrTitle': 498, 'subTitle': 498, 'pic': 203},
}

# 分镜角色 -> 默认版式原型。封面/隔页/金句走语料占比最高的 full-bleed，
# 读数据页走 split-centre-image（图上不压字），附表页走 text-block。
ROLE_LAYOUT = {'cover': 'full-bleed', 'divider': 'full-bleed', 'closing': 'full-bleed',
               'statement': 'full-bleed', 'data': 'split-centre-image',
               'process': 'split-centre-image', 'compare': 'split-centre-image',
               'quote': 'centred-statement', 'table': 'text-block',
               'appendix': 'text-block'}

# ---------------------------------------------------------------- 动画 -----------------------
# 每条都带 XML 自证签名：presetID 只报三元组，能由 filter/attributeName 证明的才写名字。
# n=次数 decks=几册用 pages=几页用。语料 entr 851 / exit 108 / emph 39 / path 70。
ANIM = {
    'fade': {'triple': ('entr', '10', '0'), 'filter': 'fade', 'attr': None,
             'n': 209, 'decks': 46, 'pages': None, 'label': u'淡入', 'set_visible': True},
    'wipe-up': {'triple': ('entr', '22', '1'), 'filter': 'wipe(up)', 'attr': None,
                'n': 35, 'decks': 9, 'label': u'擦入（上）'},
    'wipe-right': {'triple': ('entr', '22', '2'), 'filter': 'wipe(right)', 'attr': None,
                   'n': 32, 'decks': 10, 'label': u'擦入（右）'},
    'wipe-down': {'triple': ('entr', '22', '4'), 'filter': 'wipe(down)', 'attr': None,
                 'n': 66, 'decks': 12, 'label': u'擦入（下）'},
    'wipe-left': {'triple': ('entr', '22', '8'), 'filter': 'wipe(left)', 'attr': None,
                  'n': 109, 'decks': 34, 'label': u'擦入（左）——语料第二高频入场，比淡入只少一倍'},
    'appear': {'triple': ('entr', '1', '0'), 'filter': None, 'attr': None,
               'n': 41, 'decks': 12, 'label': u'出现', 'set_visible': True},
    'fly-left': {'triple': ('entr', '2', '8'), 'filter': None, 'attr': '#ppt_x',
                 'n': 40, 'decks': 17, 'label': u'飞入（自左）：p:anim 改 ppt_x/ppt_y',
                 'from': '0-#ppt_w/2'},
    'fly-right': {'triple': ('entr', '2', '2'), 'filter': None, 'attr': '#ppt_x',
                  'n': 32, 'decks': 15, 'label': u'飞入（自右）', 'from': '1+#ppt_w/2'},
    'fly-up': {'triple': ('entr', '2', '1'), 'filter': None, 'attr': '#ppt_y',
               'n': 25, 'decks': 12, 'label': u'飞入（自下）', 'from': '1+#ppt_h/2'},
    'fly-down': {'triple': ('entr', '2', '4'), 'filter': None, 'attr': '#ppt_y',
                 'n': 19, 'decks': 10, 'label': u'飞入（自上）', 'from': '0-#ppt_h/2'},
    'rise-fade': {'triple': ('entr', '53', '16'), 'filter': 'fade', 'attr': '#ppt_y',
                  'n': 74, 'decks': 28, 'label': u'位移+淡入（升字类：2 个 p:anim 加一个 fade）'},
    'drift-fade': {'triple': ('entr', '42', '0'), 'filter': 'fade', 'attr': '#ppt_x',
                   'n': 32, 'decks': 20, 'label': u'微位移+淡入（2 个 p:anim 加 fade）'},
    'zoom-wipe': {'triple': ('entr', '26', '0'), 'filter': 'wipe(down)', 'attr': '#ppt_w',
                  'n': 7, 'decks': 4, 'label': u'缩放+擦入（56 个 animScale + 35 个 anim）'},
    'wheel': {'triple': ('entr', '21', '1'), 'filter': 'wheel(1)', 'attr': None,
              'n': 7, 'decks': 5, 'label': u'轮子'},
    'barn': {'triple': ('entr', '16', '37'), 'filter': 'barn(outVertical)', 'attr': None,
             'n': 19, 'decks': 12, 'label': u'门扇式（垂直 outward）'},
    'random-bar': {'triple': ('entr', '14', '10'), 'filter': 'randombar(horizontal)',
                   'attr': None, 'n': 14, 'decks': 6, 'label': u'随机线条（横）'},
    'exit-fade': {'triple': ('exit', '10', '0'), 'filter': 'fade', 'attr': None,
                  'n': 61, 'decks': 12, 'label': u'淡出', 'transition': 'out',
                  'set_visible': False},
    'exit-wipe-left': {'triple': ('exit', '22', '8'), 'filter': 'wipe(left)', 'attr': None,
                       'n': 12, 'decks': 2, 'label': u'擦出（左）', 'transition': 'out'},
    'exit-appear': {'triple': ('exit', '1', '0'), 'filter': None, 'attr': None,
                    'n': 27, 'decks': 11, 'label': u'消失（set visible=hidden）',
                    'set_visible': False},
    'emphasis-scale': {'triple': ('emph', '6', '0'), 'filter': None, 'attr': None,
                       'n': 21, 'decks': 10, 'label': u'放大/缩小（p:animScale）'},
    'emphasis-spin': {'triple': ('emph', '8', '0'), 'filter': None, 'attr': None,
                      'n': 12, 'decks': 4, 'label': u'强调（原语在子节点里，dur 250-2750ms 中位 700）'},
    'motion-path': {'triple': ('path', '63', '0'), 'filter': None, 'attr': None,
                    'n': 20, 'decks': 14, 'label': u'自定义运动路径（p:animMotion + rAng + ptsTypes AA）'},
    'motion-arc': {'triple': ('path', '35', '0'), 'filter': None, 'attr': None,
                   'n': 14, 'decks': 8, 'label': u'弧线路径'},
    'motion-loop': {'triple': ('path', '64', '0'), 'filter': None, 'attr': None,
                    'n': 14, 'decks': 6, 'label': u'回路路径'},
}

# 节奏：语料的 delay 阶梯实测 0 / 500 / 1000 / 2000 为主（84/63/37 次非零档），
# nodeType 用 afterEffect + withEffect 串接（898 / 819 次），clickEffect 1479 次全是人工点击点。
TIMING = {'stagger_ms': [0, 500, 1000, 1500, 2000], 'default_dur_ms': 500,
          'node_chain': ('afterEffect', 'withEffect'),
          'evidence': {'clickEffect': 1479, 'afterEffect': 898, 'withEffect': 819,
                       'interactiveSeq': 1053, 'tmRoot': 1222, 'mainSeq': 1221}}

# 视频页交互（165 册、1042 页实测）：语料每个内嵌视频都写两条 mediacall——
# onBegin 自动播 playFrom(0.0)（1092 次）+ onClick 暂停/继续 togglePause（1036 次，
# 挂在 nodeType="interactiveSeq" 的 p:seq 上，带 evtFilter="cancelBubble"、endSync evt="end"）。
# 本引擎此前只写自动播，观众点画面不能暂停；VIDEO_CLICK 把第二条补上，G11 逐页数两种 cmd。
VIDEO_CLICK = {'pages': 1042, 'decks': 165, 'playFrom': 1092, 'togglePause': 1036,
               'onClick': 1053, 'onBegin': 785,
               'cTn_attrs': {'restart': 'whenNotActive', 'fill': 'hold',
                             'evtFilter': 'cancelBubble', 'nodeType': 'interactiveSeq'},
               'endSync': 'evt="end" delay="0"'}

# 转场实测：fade 213 / morph 192（全是 option="byObject"）/ blinds 35 / checker 10，
# 显式时长 2000ms 占绝对多数（218/263），2500ms 是第二档。
TRANSITION = {'dur_ms_top': 2000, 'dur_ms_top_count': 218, 'dur_ms_second': 2500,
              'counts': {'fade': 213, 'morph': 192, 'blinds': 35, 'checker': 10,
                         'prstTrans': 10, 'prism': 8, 'reveal': 1, 'sndAc': 104}}


# 每张色板的落字色：深底板用白与浅灰，亮底板用深灰——这是语料自己的分工
# （实测 text 角色 Top: FFFFFF 1361 次压在深底、4E6172 416 次与 2F2F2F 322 次用在亮底）。
TEXT_INK = {
    'tech_blue': {'text': 'FFFFFF', 'muted': 'D8D3CB', 'section': 'FFC000', 'dark': True},
    'ink_wash': {'text': '2F2F2F', 'muted': '4E6172', 'section': '9F070E', 'dark': False},
    'photo_real': {'text': 'FFFFFF', 'muted': 'D8D3CB', 'section': 'FFC000', 'dark': True},
    'business_light': {'text': '4E6172', 'muted': '767C85', 'section': '086DB5', 'dark': False},
    'party_red': {'text': 'FFFFFF', 'muted': 'F8E8B7', 'section': 'FFC000', 'dark': True},
    'neon_cyan': {'text': 'FFFFFF', 'muted': 'B8E6F0', 'section': '00FFFF', 'dark': True},
}
# 分栏页的整页底色：深板用深底，亮板用最浅的那一档实测色（语料的分栏页都是有底的版面，
# 不是白纸——白底配白字会像成品页 3 那样整页看不见字）。
PAGE_BG = {'tech_blue': '09192B', 'ink_wash': 'F8F8F3', 'photo_real': '0B0C12',
           'business_light': 'FEFEFD', 'party_red': '191A1B', 'neon_cyan': '030303'}
PAGE_BG['photo_real'] = '191A1B'
# 双色调锁色：语料 74 处 duotone 全在蓝青族（0058AF 27 次、5BA8F1 23 次、016DFF 22 次、00FFFF 2 次），
# 没有红/金/亮白系的实测样本 —— 这几块板就不假装能锁色（编一个色值会被 G16 判成板外色）。
DUOTONE_HEX = {'tech_blue': '016DFF', 'neon_cyan': '00FFFF', 'business_light': None,
               'ink_wash': None, 'party_red': None, 'photo_real': None}
for _k, _v in DUOTONE_HEX.items():
    PALETTES[_k]['duotone_hex'] = _v
for _k, _v in TEXT_INK.items():
    PALETTES[_k]['ink'] = _v
    PALETTES[_k]['page_bg'] = PAGE_BG[_k]

# 色板分深亮：深底压白字，亮底压深字。语料的明暗判定只能按渲染像素量（XML 里
# 只有 20 页写了 bg solidFill），所以这里用"深底/亮底"标记驱动蒙版方向与落字色。
DARK_STYLES = sorted([k for k, v in TEXT_INK.items() if v['dark']])


def allowed_colours(name, extra=()):
    """色板允许出现的彩色集合：G16 用它逐页核对成品 XML 里出现的 srgbClr。"""
    p = PALETTES[name]
    out = set()
    for k in ('base_hex', 'text_hex', 'title_hex', 'accent_hex', 'cyan_hex', 'gold_hex',
              'panel_hex', 'dark_hex'):
        if p.get(k):
            out.add(p[k].upper())
    for _pos, hx in p.get('grad') or []:
        out.add(hx.upper())
    for _role, hx in (p.get('theme') or {}).items():
        if hx:
            out.add(hx.upper())
    for v in p.get('ink') or {}:
        if isinstance(v, str) and len(v) == 6:
            out.add(v.upper())
    tb = p.get('table') or {}
    for hx in tb.values():
        if isinstance(hx, str) and len(hx) == 6:
            out.add(hx.upper())
    if p.get('duotone_hex'):
        out.add(p['duotone_hex'].upper())
    for t in ('deep', 'mid', 'hi'):
        if p.get(t):
            out.add('%02X%02X%02X' % tuple(p[t]))
    out.update(x.upper() for x in extra)
    return out


def _mix(c1, c2, t):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * t)) for i in range(3))


def _hx(c):
    return '%02X%02X%02X' % tuple(max(0, min(255, v)) for v in c)


# 表格用的三档底色：由色板的深底/中层/高光按语料的派生比例混合得到
# （语料文字色 87% 走 lumMod<25% + lumOff>=75%，即"主题色淡色洗底"，这里用同档线性混合复现）。
# 这三格同时进 G16 的允许色集合，所以表格配色既跟着色板走又不会被自己的闸拦下。
for _k, _p in PALETTES.items():
    _dark = _p['ink']['dark']
    _deep, _mid, _hi = _p['deep'], _p['mid'], _p['hi']
    _panel = tuple(int(h, 16) for h in (_p['panel_hex'][i:i + 2] for i in (0, 2, 4)))
    if _dark:
        _p['table'] = {'head': _hx(_mix(_hi, _deep, 0.72)),
                       'band_a': _hx(_mix(_deep, _mid, 0.18)),
                       'band_b': _hx(_mix(_deep, _mid, 0.07)),
                       'head_text': _p['ink']['text'], 'body_text': _p['ink']['muted']}
    else:
        _p['table'] = {'head': _p['accent_hex'],
                       'band_a': _p['panel_hex'],
                       'band_b': _hx(_mix(_panel, (255, 255, 255), 0.62)),
                       'head_text': _p['ink']['text'], 'body_text': _p['ink']['text']}


# ---------------------------------------------------------------- 示范质量分层 ----------------
# 语料是商品模板包，六成页面不能当范本。grade_exemplars.py 把 2224 页逐页判过一遍，
# 下面这组数字是判据本身（kb/exemplar_grades.json 可复算），凡是"语料里高频"的做法，
# 先问它在好示范子集里还高不高：满幅压字从 27.3% 升到 58.3%，纯视觉页从 33.5% 掉到 22%，
# 说明原来的"三成页面没字"根本不是设计选择，而是作者自己的推广页。
EXEMPLAR = {
    'pages': 2224, 'good_pages': 835, 'bad_pages': 1389, 'bad_share_pct': 62.5,
    'junk_decks': 117, 'good_decks': 54,
    'reasons': {'empty_page': 560, 'ad:share_channel': 496, 'text_on_bright_bg': 346,
                'dense_text_wall': 70, 'no_type_hierarchy': 20, 'product_number_hero': 16,
                'ad:more_templates': 7, 'ad:closing_cliche': 1, 'ad:shop_ad': 1},
    'good_page_metrics': {'chars_median': 19, 'max_pt_median': 66,
                           'anim_pages': 668, 'media_pages': 660},
    'bad_page_metrics': {'chars_median': 41, 'max_pt_median': 28},
}

# 明确否决的语料做法：这些在语料里都是真实存在的，但方向是反的，引擎与闸都不许跟
REJECTED = [
    {'practice': u'推广页/下载页（提取码、网盘、公众号、二维码、更多模板）',
     'corpus_pages': 504,
     'why': u'是作者卖货页，不是设计；混进语料会把"三成页面没内容"当成风格',
     'engine': u'不生成任何页面带这类文字；G19 逐页正则拦截'},
    {'practice': u'整页 0 字 0 信息的纯装饰页', 'corpus_pages': 560,
     'why': u'好示范子集里这类页从 33.5% 掉到 22%，剩下的都是有视频/动画的展示页',
     'engine': u'成品里无字页必须带媒体或动效，否则 G19 拦'},
    {'practice': u'字压在亮底上（文字区背景亮度 >0.55）', 'corpus_pages': 346,
     'why': u'语料自己 15.5% 的页糊了字；这是缺陷不是手法',
     'engine': u'满幅页必加压暗蒙版（mask-dim）+ G4 逐框量渲染像素对比度 + G19 复核文字区亮度'},
    {'practice': u'一页 300 字以上的文字墙', 'corpus_pages': 70,
     'why': u'与"发布会风格一页一意"相反，用户明确否过密集版式',
     'engine': u'plan 阶段按章拆页；G19 拦成品单页字数上限'},
    {'practice': u'整页最大字号不到 14pt（没有视觉层级）', 'corpus_pages': 20,
     'why': u'好示范页的字号中位是 40pt，语料 48% 页面有 >=60pt 大字',
     'engine': u'place_text 按 pick_pt 抬字号；G19 拦无层级页'},
    {'practice': u'把模板编号（NO.173）当主视觉', 'corpus_pages': 16,
     'why': u'那是商品图不是汇报图',
     'engine': u'标题取 Word 内容；G19 拦编号开头的标题'},
    {'practice': u'沿用 Office 默认主题色不改（167/171 册）', 'corpus_pages': None,
     'why': u'语料的配色一致性靠逐形状直写 srgbClr + lumMod 派生，主题色是空的',
     'engine': u'本技能多做一步：把实测色板写进 theme1.xml，G16 逐页核对成品彩色'},
]

# 好示范子集重算后仍然成立的结论（这些才进引擎默认值）
ADOPTED = [
    u'满幅图 + 压字 + 压暗蒙版是好示范页的主流版式（487/835 页，58.3%）',
    u'蓝色系主导：好示范册 45/54 的主用彩色是蓝（4472C4 / 086DB5），金红青是点缀',
    u'文字区必须有暗底：好示范页判据之一就是文字区背景亮度 <=0.55',
    u'大字优先：好示范页最大字号中位 40pt，语料 48% 页面有 >=60pt',
    u'视频页自动播 + 点击暂停是语料统一做法（1042 页都写了 togglePause）',
]


# 坏示范判据的唯一来源：grade_exemplars.py 用它给语料分层，G19 用同一份判据拦成品。
# 两处各写一套正则的话，"语料里拦住了"与"成品里拦住了"就会慢慢不是同一件事。
BAD_RULES = {
    'ad_patterns': [
        (r'提取码|验证码|下载链接|网盘|百度云|加群|客服|微信号|公众号|二维码', 'share_channel'),
        (r'更多(精品|优质|热门)?(模板|模版|素材|资源)|免费(领取|下载|模板|素材)|模板(大全|合集)',
         'more_templates'),
        (r'(淘宝|店铺|网店|商务|定制).{0,8}(PPT|ppt|设计|制作)', 'shop_ad'),
        (r'(教程|课程).{0,10}(推荐|相关|配套|点击查看)|相关推荐', 'tutorial_promo'),
        # 套话只有在行首或独立成行时才是模板尾巴；正文里一句「这一章不是免责声明」
        # 是内容本身，判成广告就是误伤（实测车联体 8 页被这样拦下）
        (r'^\s*(免责声明|侵权必究|版权所有|版权归\S*所有)', 'legal_boilerplate'),
        (r'^(THANKS|谢谢观看|感谢聆听)\s*$', 'closing_cliche'),
    ],
    'product_number': r'^(NO\.?\s?\d+|第?\s*\d+\s*套)\b',
    'dense_chars_min': 300,
    'tiny_pt_max': 14,
    'text_zone_lum_max': 0.55,
}


# 深底落字：满幅压字页与视频页都被蒙版压暗了，这时必须用浅色字——与色板本身是亮是暗无关。
# （实测踩过的坑：亮白商务板把 4E6172 深字放到压暗的封面上，渲染出来对比度 1.01:1，等于看不见。）
DARK_GROUND_INK = {'text': 'FFFFFF', 'muted': 'E4E6EA', 'section': 'FFC000', 'dark': True}
