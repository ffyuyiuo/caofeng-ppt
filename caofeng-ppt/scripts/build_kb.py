"""Compile the studied corpus into the skill's built-in knowledge base.

Inputs (produced by the study scripts under CAOFENG_WORK):
  ooxml_ledger.json     per-deck OOXML technique facts (172 decks)
  prompt_blocks.json    52 real AI prompt blocks from 29 decks
  videos_s*.jsonl       per-unique-video facts (ASR + duration + resolution)
  transcripts/*.txt     narration text

Outputs (into <SKILL_DIR>/kb/):
  deck_tech.json    technique cards with measured parameters and OOXML recipes
  shot_menu.md      the menu an agent picks shot_intent from
  prompt_corpus.jsonl  the 52 prompts, labelled and slot-annotated
  video_index.json  every tutorial video: which decks, duration, transcript file
  coverage.json     what was actually read, and what could not be read
"""
import os, io, json, re, glob, collections, hashlib, sys, shutil

def require_work(w):
    """These study scripts write their products into the work dir. Refuse to invent one: on a
    fresh machine the default may sit on a drive that does not exist, and silently creating
    CAOFENG_WORK there is how a re-run becomes a mystery. Set CAOFENG_WORK to an existing dir.
    """
    if not os.path.isdir(w):
        print("停止：工作目录不存在 %s；先建好它，或用 CAOFENG_WORK 指一个已存在的目录" % w, file=sys.stderr)
        sys.exit(2)
    return os.path.abspath(w)

W = require_work(os.environ.get('CAOFENG_WORK', 'CAOFENG_WORK'))
SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(SKILL, 'kb')
import corpus_path as CP
SRC = CP.corpus_dir(strict=True)


def load_ledger():
    return json.load(io.open(os.path.join(W, 'ooxml_ledger.json'), encoding='utf-8'))


def prompt_slots(body):
    """Slot annotation matching the grammar measured in extract_prompt_grammar.py."""
    S = {}
    S['画幅'] = bool(re.search(r'16:9|横版|横屏', body))
    S['时长'] = bool(re.search(r'\d+\s*[-–]?\s*\d*\s*秒', body))
    S['画质锚'] = bool(re.search(r'4K|8K|超高清|超清|HDR', body))
    S['电影级'] = bool(re.search(r'电影级|电影感|宣传片', body))
    S['位置量化'] = bool(re.search(r'占画面(宽度|高度)|约\s*\d+%', body))
    S['视角'] = bool(re.search(r'俯视|平视|仰视|广角|\d+mm|轴测|等距|机位', body))
    S['镜头运动'] = bool(re.search(r'镜头(缓慢|逐渐|保持|固定|向前|向后)|推进|拉远|环绕|跟拍|下降', body))
    S['光线'] = bool(re.search(r'体积光|轮廓光|反射光|逆光|漫射|辉光|光晕|光源', body))
    S['色彩闭集'] = bool(re.search(r'严格控制在|为主色|避免.{0,8}颜色|主色调', body))
    S['三段纵深'] = bool(re.search(r'前景', body) and re.search(r'中景|远景|纵深', body))
    S['留白占位'] = bool(re.search(r'保持(完全)?空白|不放任何文字|预留|留白', body))
    S['禁文字'] = bool(re.search(r'不要出现任何文字|无文字|文字、数字、字母|禁止出现任何文字', body))
    S['禁水印'] = bool(re.search(r'水印', body))
    S['首尾帧'] = bool(re.search(r'附件|作为首帧|尾帧|结尾帧|参考图', body))
    S['秒级分镜'] = bool(re.search(r'\d+\s*[-–]\s*\d*(\.\d+)?\s*秒', body))
    S['定格'] = bool(re.search(r'定格|稳定停留|不再增加|匹配.{0,10}参考', body))
    S['因果顺序'] = bool(re.search(r'必须(先|严格|按照)|之后才|之前不能|依次|不得同时', body))
    S['用途'] = bool(re.search(r'PPT', body))
    return S


def build_tech_cards(led):
    # 字号与文本密度由 aggregate_ledger.py 算好写进 corpus_stats.json，这里只读不算：
    # 上一版把"80pt 2908 次、每页中位 23 字"手抄进卡里，删掉一个字节级重复的模板后就成了过期数。
    cs = json.load(io.open(os.path.join(W, 'corpus_stats.json'), encoding='utf-8'))
    CST = {'n_decks': cs['n_decks'], 'n_slides': cs['n_slides'],
           'text_median': cs['text_median'], 'empty_ratio': cs['empty_page_ratio'],
           'big_pt': [[pt, n] for pt, n in cs['pt_sizes_top'] if pt >= 60]}
    """Technique cards, each carrying measured counts + OOXML recipe + the gate that checks it."""
    tt = collections.Counter()
    tc = collections.Counter()
    pr = collections.Counter()
    eff = collections.Counter()
    morph_dur = collections.Counter()
    delays = collections.Counter()
    bg = collections.Counter()
    custgeom_pages = 0
    lum_pages = 0
    combos = collections.Counter()
    combo_pages = collections.Counter()
    for d, v in led.items():
        if 'slides' not in v:
            continue
        for s in v['slides']:
            for dec in s['trans']['decls']:
                for k in dec['kids']:
                    tt[k['type']] += 1
                    if k['type'] == 'morph':
                        morph_dur[dec['attrs'].get('dur', '?')] += 1
            for k, n in s['timing']['classes'].items():
                tc[k] += n
            # timing.combos keys look like "entr:22:8" = presetClass:presetID:subtype.
            # These are the actual entrance/exit effects the corpus uses (打字机、飞入、淡出…);
            # without them the library only knew "there were 852 entrance animations".
            for k, n in (s['timing'].get('combos') or {}).items():
                parts = str(k).split(':')
                if len(parts) >= 2:
                    cid = '%s:%s' % (parts[0], parts[1])
                    combos[cid] += int(n or 0)
                    if int(n or 0):
                        combo_pages[cid] += 1
            for k, n in s['timing']['prims'].items():
                pr[k] += n
            for k, n in s['timing'].get('delays', {}).items():
                delays[k] += n
            for k, n in s['inv']['effects'].items():
                eff[k] += n
            if s['inv']['shapes'].get('custGeom'):
                custgeom_pages += 1
            if s['inv']['effects'].get('lumMod'):
                lum_pages += 1
            if s['bg']:
                for b in s['bg']:
                    bg[b] += 1
    n_slides = sum(len(v.get('slides', [])) for v in led.values())
    cards = [
        {"id": "video-fullbleed", "name": "整页视频背景（电影感主力）",
         "evidence": "presetClass mediacall %d 次 / %d 页含内嵌媒体（172 模板实测）" % (tc['mediacall'], sum(1 for d in led.values() for s in d.get('slides', []) if s['media'])),
         "when": "封面、章节隔页、结尾页需要真实运动感时",
         "params": "视频 5–10 秒、16:9、2560×1440 或更高；自动播放 delay=0；点击播放用 interactiveSeq",
         "ooxml": "p:pic + a:blipFill(r:link 指向 media) + p:timing/tmRoot/mainSeq 内 cTn presetClass=\"mediacall\" presetID=1(播放)/2(停止)，stCondLst cond evt 或 delay=\"0\"",
         "gate": "G6 时长 / G7 可编辑"},
        {"id": "morph-camera", "name": "平滑（Morph）当运镜用",
         "evidence": "morph 192 次，option 全部 byObject，dur=2000ms 占 218 次显式时长",
         "when": "同一主体在相邻页改变大小/位置，制造推镜、拉镜、横移",
         "params": "时长 2000ms（快 1250 / 慢 2500）；option=byObject；两页必须有同名同 id 的形状",
         "ooxml": "mc:AlternateContent/mc:Choice Requires=\"p159\" 内 p:transition spd=\"slow\" p14:dur=\"2000\" &lt;p159:morph option=\"byObject\"/&gt;，mc:Fallback 内放 p:fade 兜底",
         "gate": "G6 节奏 / G8 越界"},
        {"id": "mask-dim", "name": "压暗蒙版让字站得住",
         "evidence": "lumMod 7864 次（比 glow 88 高两个数量级）；blipFill 整页背景 689 页",
         "when": "任何文字压在图片/视频上",
         "params": "小字区压暗到背景亮度 ≤35%；渐变蒙版从 80% 到 0% alpha",
         "ooxml": "图片之上叠 a:gradFill，gs  stops 带 a:alpha val=\"80000\"→\"0\"；或 a:solidFill 000000 + a:alpha",
         "gate": "G4 对比度（渲染像素实测）"},
        {"id": "bool-shape", "name": "布尔运算造异形容器",
         "evidence": "custGeom 7785 个，出现在 %d 页（占 %.0f%%）" % (custgeom_pages, 100.0 * custgeom_pages / n_slides),
         "when": "图片需要非矩形裁切、撕纸边、笔刷擦除、扇形/花瓣阵列、封面标题「变脸」",
         "steps": "封面变脸（独立教程逐字稿 64fa5cec…txt，44.7 秒，点击级）："
                  "① 做好标题文字并全选 → 形状格式「组合」；② 复制到准备好的视频上；"
                  "③ 依次选中视频与文字 → 形状格式「相交」；④ 得到的效果字放回原视频上，"
                  "边框改 1.5 磅白色；⑤ 标题视频与背景视频的播放都设为自动；"
                  "⑥ 打开动画窗格，把「开始」全部改为与上一动画同时",
         "params": "先复制形状再合并/剪除/相交；成品是 custGeom，不可再当普通矩形改",
         "ooxml": "p:sp/p:spPr/a:custGeom/a:pathLst 内 a:path a:moveTo/a:lnTo/a:cubicBezTo",
         "gate": "G8 版式"},
        {"id": "click-gated-motion", "name": "点击才动的因果节奏",
         "evidence": "nodeType clickEffect 1486 次、interactiveSeq 1059 次；delay=0 出现 3778 次",
         "when": "讲解型页面：先出图，讲到再动",
         "params": "首屏元素 withEffect（delay 0），后续 clickEffect 逐个；同组元素用 afterEffect 串",
         "ooxml": "p:timing/p:tnLst/p:par/mainSeq 内 cTn nodeType=\"clickEffect|withEffect|afterEffect\"",
         "gate": "G6 节奏"},
        {"id": "motion-path", "name": "自定义运动路径",
         "evidence": "presetClass path 70 次，animMotion 71 个，路径串形如 \"M 0 0 L -0.38932 0\"",
         "when": "飞机划过、列车行驶、花瓣旋转、文字跟随滑动",
         "params": "路径坐标是相对画布的比例值（0–1 区间，可负）；配合 smooth=\"1\" 去折角",
         "ooxml": "p:animMotion path=\"M x y L ...\" pathEditMode=\"relative\" cornerSmooth=\"0\" repeatCount",
         "gate": "G8 越界"},
        {"id": "giant-numeral", "name": "超大数字当主视觉",
         "evidence": "字号分布实测：%s；每页中位 %s 字、%.1f%% 页几乎无字（%d 模板 %d 页 OOXML 实测）" % (
             '、'.join('%dpt %d 次' % (pt, n) for pt, n in CST['big_pt'][:6]),
             CST['text_median'], CST['empty_ratio'], CST['n_decks'], CST['n_slides']),
         "when": "年份、金额、百分比、里程碑——一页只讲一个数",
         "params": "主数字 120–250pt（封面/里程碑）、60–80pt（隔页）；说明文字 14–18pt；一页正文 ≤60 字",
         "ooxml": "a:rPr sz=\"24000\"（百分_pt×100）+ 单独文本框居中",
         "gate": "G10 去 AI 味 / G8 孤字"},
        {"id": "sound-cue", "name": "音效点一下",
         "evidence": "转场里 sndAc 104 次；audio 原语 58 个",
         "when": "揭幕、破土、升空这类瞬间；一场 deck 用 ≤3 次",
         "params": "与视屏同帧触发，音量淡出",
         "ooxml": "p:timing 内 p:audio order=\"1\" + a:blip r:embed 指向 mediaN.mp3/wav",
         "gate": "无闸——引擎不嵌音频（G11 只核 mp4 与 GIF 的字节，从没管过音频），"
                          "要音效得在 PowerPoint 里插入并自己确认文件在包内"},
        {"id": "softedge-glow", "name": "柔边与霓虹描边",
         "evidence": "softEdge 1051、glow 88、reflection 348、sp3d 170、scene3d 154",
         "when": "科技风玻璃面板边缘、发光箭头、地面倒影",
         "params": "半径一律实测（EMU，12700 EMU≈1pt）：glow 最常见 __GLOW__；"
                   "softEdge 最常见 __SOFT__。旧文档这里写过 6350–12700、别处写过 63500–127000，"
                   "差 10 倍且两边都复现不出来——这两个串由 build_kb 扫 172 模板算出后回填，"
                   "改语料就改数，不靠人记",
         "ooxml": "a:effectLst/a:softEdge rad=\"12700|76200\"、"
                  "a:glow rad=\"228600|38100|127000\" + a:srgbClr + a:alpha（值取自实测分布）",
         "gate": "G4 对比度（发光会抬亮邻域像素）；引擎不产 a:softEdge/a:glow，见 engine 字段"},
        {"id": "palette-lock", "name": "一册一色域（实测色板 + theme 写入）",
         "evidence": "好示范册 45/54 的主用彩色是蓝（4472C4 2672 次 / 086DB5 1120 次），"
                     "金 FFC000 141 次、党建红 9F070E 107 次、霓虹青 00FFFF 1483 次（渐变内）；"
                     "文字色派生实测 text|lumMod 2758 次 + text|lumOff 964 次",
         "when": "全册统一，禁跨页换色；换风格只换 --palette，六个实测色板任选",
         "params": "主色 1 + 辅色 1 + 强调 1（6:3:1），强调色每页 ≤1 处大面积；"
                   "表格三档底色由色板按 lumMod 同档线性混合得到，不额外引新色",
         "ooxml": "ppt/theme/theme1.xml 的 dk1/lt1/dk2/lt2/accent1..6 按色板写死，"
                  "页面文字/面板直写同值 srgbClr（语料自己 167/171 册没改主题，这是本技能多做的一步）",
         "gate": "G16 配色（逐页彩色必须在色板内 + theme accent1 必须等于色板主色）/ G2 色彩闭集"},
    ]
    n_anim_pages = sum(1 for d in led.values() for s in d.get('slides', [])
                       if (s['timing'].get('combos') or {}))
    # mediacall is video play/stop, not a text effect: this card ranks only entr/exit/emph/path
    text_ids = [(k, v) for k, v in combos.most_common() if not k.startswith('mediacall')]
    top_text = '、'.join('%s %d次/%d页' % (k, v, combo_pages[k]) for k, v in text_ids[:6])
    cards.append(
        {"id": "text-animation-presets", "name": "文字入场/退场动效（按 presetID 实测排序）",
         "evidence": "timing.combos 实测：%s（172 模板 / %d 页带动画）" % (top_text, n_anim_pages),
         "when": "逐行浮现、打字机、飞入、淡出、擦除——讲解型页面上文字不能一次性全冒出来",
         "params": "选 presetID 优先按语料高频（%s）；同一段落内多个元素用 delay 递进 100–300ms" %
                   '、'.join(k for k, _ in text_ids[:5]),
         "ooxml": "p:timing/p:tnLst/p:par/p:cTn[@presetClass=\"entr|exit|emph\" @presetID=\"N\"]"
                  " + p:set/p:animEffect transition=\"in\" filter=\"fade|wipe|blend\"；"
                  "官方 presetID→中文名对照表本机取不到（链接实测跳别的页面），"
                  "所以这里只给原始 ID 与次数，不挂中文名，照抄 ID 即可复现效果",
         "gate": "G6 节奏 / G7 可编辑"})
    for c in cards:
        c.setdefault('source', 'CORPUS_DIR 172 模板 OOXML 实测')
    return cards, {'n_slides': n_slides, 'trans': dict(tt), 'classes': dict(tc),
                   'preset_ids': dict(combos.most_common(30)),
                   'text_preset_ids': dict(text_ids[:20]),
                   'pages_with_animation': n_anim_pages,
                   'preset_id_pages': {k: combo_pages[k] for k, _ in combos.most_common(30)},
                   'prims': dict(pr), 'morph_dur': dict(morph_dur), 'effects': dict(eff),
                   'bg': dict(bg), 'custgeom_pages': custgeom_pages, 'lum_pages': lum_pages}


def main():
    os.makedirs(KB, exist_ok=True)
    led = load_ledger()
    cards, stats = build_tech_cards(led)
    json.dump({'cards': cards, 'stats': stats},
              io.open(os.path.join(KB, 'deck_tech.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    blocks = json.load(io.open(os.path.join(W, 'prompt_blocks.json'), encoding='utf-8'))
    o = io.open(os.path.join(KB, 'prompt_corpus.jsonl'), 'w', encoding='utf-8')
    slotsum = collections.Counter()
    kindn = collections.Counter()
    # 分种类的槽位命中数：文档里"X/22"这类分数必须能在 prompt_stats.json 里查到同一个分母。
    # 以前 §3 表写"定格收束 6/22"，而全语料含"定格"的只有 4 块（视频内 3 块），根本不可能成立。
    slot_by_kind = collections.defaultdict(collections.Counter)
    for b in blocks:
        sl = prompt_slots(b['body'])
        for k, v in sl.items():
            if v:
                slotsum[k] += 1
                slot_by_kind[b['kind']][k] += 1
        kindn[b['kind']] += 1
        o.write(json.dumps(dict(b, slots=sl, n_slots=sum(1 for v in sl.values() if v)),
                           ensure_ascii=False) + "\n")
    o.close()
    json.dump({'n_blocks': len(blocks), 'kinds': dict(kindn),
               'slot_coverage': {k: [slotsum[k], len(blocks), round(100.0 * slotsum[k] / len(blocks))] for k in slotsum},
               'slot_coverage_by_kind': dict((k, {'n': kindn[k], 'slots': dict(
                   sorted(slot_by_kind[k].items(), key=lambda x: -x[1]))}) for k in sorted(kindn))},
              io.open(os.path.join(KB, 'prompt_stats.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    vids, seen = [], set()
    for f in sorted(glob.glob(os.path.join(W, 'videos_s*.jsonl'))):
        for ln in io.open(f, encoding='utf-8'):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r['id'] in seen:
                continue
            seen.add(r['id'])
            tp = os.path.join(W, 'transcripts', r['id'] + '.txt')
            r['transcript_file'] = 'transcripts/' + r['id'] + '.txt' if os.path.exists(tp) else None
            r['transcript_chars'] = os.path.getsize(tp) if os.path.exists(tp) else 0
            vids.append(r)
    json.dump(vids, io.open(os.path.join(KB, 'video_index.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    decks = sorted(f for f in os.listdir(SRC) if f.lower().endswith('.pptx'))
    ext_link = []
    import zipfile
    import xml.etree.ElementTree as ET
    AT = '{http://schemas.openxmlformats.org/drawingml/2006/main}t'
    gif_total = 0
    rad_glow = collections.Counter()
    rad_soft = collections.Counter()
    gif_decks = set()
    gif_dims = []
    # 提示词页的"零遗漏"证明：(deck, slide) 已入库集合 vs 语料里真的提示词页
    PROM_LIKE = re.compile(u'提示词|画面比例|16:9|镜头|文生图|即梦|可灵|AI生成|首帧|尾帧')
    prompt_covered = set((b['deck'], int(b['slide'])) for b in blocks)
    prompt_like = [0, 0]          # [语料里像提示词的页数, 其中已入库的页数]
    prompt_like_missing = []
    from PIL import Image
    import io as _io
    for f in decks:
        try:
            z = zipfile.ZipFile(os.path.join(SRC, f))
        except Exception:
            continue
        ext_here = False
        for n in z.namelist():
            if n.startswith('ppt/media/') and n.lower().endswith('.gif'):
                try:
                    data = z.read(n)
                    im = Image.open(_io.BytesIO(data))
                    w, h = im.size
                    nf = int(getattr(im, 'n_frames', 1) or 1)
                except Exception:
                    w = h = nf = 0
                gif_total += 1
                gif_decks.add(f)
                gif_dims.append((w, h, nf))
        # 发光/柔边的半径分布必须实测：卡片以前一处写 6350–12700、另一处写 63500–127000，
        # 差 10 倍而两边都复现不出来。现在把实测值写进 stats，卡片从 stats 取。
        for n in z.namelist():
            if re.match(r'ppt/slides/slide\d+\.xml$', n):
                try:
                    raw = z.read(n).decode('utf-8', 'ignore')
                except Exception:
                    continue
                for tag, ctr in (('glow', rad_glow), ('softEdge', rad_soft)):
                    for mm in re.finditer(r'<a:%s rad="(\d+)"' % tag, raw):
                        ctr[int(mm.group(1))] += 1
        for n in z.namelist():
            if re.match(r'ppt/slides/slide\d+\.xml$', n):
                try:
                    txt = ' '.join((t.text or '') for t in ET.fromstring(z.read(n)).iter(AT))
                except Exception:
                    continue
                if 'pan.quark.cn' in txt or 'v.douyin.com' in txt:
                    if not ext_here:
                        ext_link.append(f)
                        ext_here = True
                    # 不能 break：早退会把后面的提示词页漏扫（92 个带外链的模板全在这条上翻车过）
                    continue
                # 需求 2 说"内部大量提示词一律学会"，那就必须能证明**没有漏页**：
                # 凡是提示词特征 ≥2 个且正文 >120 字的页，都必须已经在 prompt_corpus 里。
                # 实测过：提示词只存在于 slide XML（notesSlides/master/layout 一处都没有），
                # 所以只扫 slide 就够；漏扫别的地方会被这条不变量当场抓到。
                hits = PROM_LIKE.findall(txt or '')
                if len(hits) >= 2 and len(txt or '') > 120:
                    si = int(re.findall(r'\d+', n)[0])
                    prompt_like[0] += 1
                    if (f, si) in prompt_covered:
                        prompt_like[1] += 1
                    else:
                        prompt_like_missing.append([f, si, len(txt)])
        z.close()
    big_gif = [d for d in gif_dims if min(d[0], d[1]) >= 640 and d[2] >= 10]
    gif_stat = {'total': gif_total, 'decks': len(gif_decks),
                'min_frames': min([d[2] for d in gif_dims], default=0),
                'median_frames': sorted([d[2] for d in gif_dims])[len(gif_dims) // 2] if gif_dims else 0,
                'element_size_or_bigger': len(big_gif),
                'typical_dims': ['%dx%d' % (wh[0], wh[1]) for wh, _ in
                                 collections.Counter((d[0], d[1]) for d in gif_dims).most_common(4)]}
    stats['effect_radii'] = {'glow': [[k, n] for k, n in rad_glow.most_common(3)],
                             'softEdge': [[k, n] for k, n in rad_soft.most_common(3)]}
    _fmt = lambda lst: '、'.join('%d EMU（%d 次）' % (k, n) for k, n in lst) or '语料里没有'
    for c in cards:
        if c['id'] == 'softedge-glow':
            c['params'] = c['params'].replace('__GLOW__', _fmt(stats['effect_radii']['glow']))                                     .replace('__SOFT__', _fmt(stats['effect_radii']['softEdge']))
    cards.append(
        {"id": "gif-motion-element", "name": "动图（GIF）当开场/元素动效",
         "evidence": "%d 个模板含 GIF，共 %d 个，最少 %d 帧、中位 %d 帧，短边≥640 的 %d 个（172 模板实测）" % (
             gif_stat['decks'], gif_stat['total'], gif_stat['min_frames'],
             gif_stat['median_frames'], gif_stat['element_size_or_bigger']),
         "when": "水墨晕开、飞机/闪光划过、旋转扇子/卷轴、破碎开场——比整页视频轻，又不想只放静帧时",
         "params": "常见尺寸 %s；必须多帧（语料里最少 %d 帧）；循环无缝；**必须透明底**"
                   "（本机 scripts/make_gif.py 出带透明索引的 GIF；不透明底叠在矢量色场上会露出一块方框，"
                   "只有身后是暗色实景时才可退纯黑底）；时长 2–6 秒，落在留白区之外的位置" % (
                       '、'.join(gif_stat['typical_dims']), gif_stat['min_frames']),
         "ooxml": "p:pic + a:blipFill 指向 ppt/media/imageN.gif（PowerPoint/WPS 在放映时自动播放 GIF，"
                  "不需要 p:timing；单帧 GIF 不会动，必须验帧数）",
         "gate": "G11 帧数≥10 且短边≥640 且不透明底不许叠在矢量色场上 / "
                 "G3 成品内嵌 GIF 字节必须与验收过的源文件一致 / G2 动图提示词 6/6 段"})
    # 第五轮复验补的两类：语料里量得到、教程讲得到，但卡片一直没写（需求 2 要的是"全部手法"）
    cards.append(
        {"id": "3d-model-depth", "name": "3D 模型与立体感（导入 + 三维旋转）",
         "evidence": "sp3d %d 处、scene3d %d 处、bevelT %d、reflection %d（172 模板 OOXML 实测）" % (
             stats['effects'].get('sp3d', 0), stats['effects'].get('scene3d', 0),
             stats['effects'].get('bevelT', 0), stats['effects'].get('reflection', 0)),
         "when": "封面大数字要有厚度、产品/城市微缩场景、卷轴展开后露出 3D 建筑",
         "steps": "教程原话（逐字稿里 3D 那几条）：① 先把文字和其他素材摆放好，再导入 3D 素材；"
                  "② 公司有产品模型就直接拖进 PPT（**版本需 2016 及以上**，否则没有 3D 模型选项）；"
                  "③ 没有模型就去 Sketchfab 找类似的；④ 拖动模型中间那个控制点做三维旋转，"
                  "转到合适角度后**复制到其他页面**保持同一机位；"
                  "⑤ 立体柱状图靠形状 + 棱台/渐变，不用真 3D（教程原话「这样具有高级感的立体柱状图就完成了」）",
         "params": "三维旋转用 preset 相机（如 orthogonal/front）+ scene3d；厚度用 bevelT（语料只有 %d 处，"
                   "别到处加）；反射 reflection %d 处，压在暗底上用，亮底会发灰" % (
                       stats['effects'].get('bevelT', 0), stats['effects'].get('reflection', 0)),
         "engine": "**引擎已自动产出**：`tech_recipe=3d-model-depth` 的页会在标题/大数字下层垫一块"
                   "立体展台（`add_3d_panel`：scene3d 透视相机 + sp3d 挤出 114300 + 棱台 + 金色轮廓 + 反射），"
                   "边自动避开上下相邻文字行。**仍需手工**：导入 `.glb` 真 3D 模型（PowerPoint 2016+ 的"
                   "「3D 模型」按钮）与三维旋转控制点调机位——引擎不产 3D 模型文件，也不谎称产出",
         "ooxml": "a:sp3d（extrusionH/contourW/prstMaterial + a:bevelT）+ a:scene3d/a:camera"
                  "（引擎用 perspectiveFront）+ a:lightRig threePt + a:spPr 内 a:effectLst/a:reflection；"
                  "子元素必须按 CT_SpPr 顺序 xfrm→几何→fill→ln→effect→3D 排，写反了真 PowerPoint 弹修复",
         "gate": "G6（分镜声明 3d-model-depth 就必须在这页 XML 里数到 a:sp3d，数不到直接拦）/ "
                 "G8 版式（spPr 顺序与重复、展台边不许咬到锚点以外的文字行）/ "
                 "G4 对比度（棱台与反射会抬亮背景像素）"})
    cards.append(
        {"id": "table-motion", "name": "表格自身的转场与轮播动画",
         "evidence": "转场 checker（棋盘）%d 次、blinds %d 次；animMotion %d 次；"
                     "转写里 30 组提到表格动画（172 模板 + 253 条视频实测）" % (
                         stats['trans'].get('checker', 0), stats['trans'].get('blinds', 0),
                         stats['prims'].get('animMotion', 0)),
         "when": "台账/巡检表要逐行或整块出现；一张表当「轮播窗口」滚动展示多图",
         "steps": "教程原话：A「最神奇的表格」——先插入 7×5 的表格，"
                  "**给它加专属转场「棋盘」**，同一张表还能做多图展示；"
                  "B「表格自动循环轮播」——先插一个表格，再插第二个表格作为轮播主体，"
                  "把数据复制过去，画一个矩形**其大小与位置就是轮播表格显示的区域**，"
                  "把矩形拖到表格下方定位，选中表格**添加直线动画**（需要几个环形就填几个 1）",
         "params": "转场用 checker/blinds 这类分块型；轮播的位移量 = 行高 × 行数，"
                   "动画目标指向 graphicFrame 内的表格；循环次数用「填几个 1」的重复参数控制",
         "engine": "**引擎已自动产出**：`tech_recipe=table-motion` 且有表格的页写 `p:checker`"
                   "（可用 `\"transition\":\"blinds\"` 换百叶），时长取 `TH['trans_dur_ms']`=2500ms；"
                   "一页只容得下一个 `p:transition`，所以这些页不再叠 morph，台账连续分页靠表头每页重复衔接。"
                   "**仍需手工**：教程 B 那种「复制两份表格 + 直线动画」的循环 marquee 是在 PowerPoint 里点的，"
                   "引擎不产（我们的台账按 G13 要求逐格可核对，不做超出画布的滚动）",
         "ooxml": "p:graphicFrame/a:tbl（真表格，不是图片）+ "
                  "mc:AlternateContent/Choice(Requires=p14)>p:transition[p14:dur]>p:checker|p:blinds + Fallback；"
                  "教程里的轮播是 p:timing 内 p:animMotion path=\"M … L …\"（直线 L 路径，"
                  "repeatCount=indefinite + autoRev=1，见 NO.107/NO.121/NO.140）",
         "gate": "G6（声明 table-motion 就必须在这页 XML 里数到 checker/blinds）/ "
                 "G13 表格逐格核对（动画不许改内容）/ G8 版式（轮播框不许越界）/ "
                 "G7 可编辑（表格必须是真 a:tbl，不是图片）"})


    # 第六轮之后又扫了一遍全语料 token 矩阵：effects/prims 里实测到、却没有任何一张卡提到的，
    # 只剩下面这两类（duotone/satMod/tint/biLevel 一族，animScale 一族）。补成卡，需求 2 的
    # "全部手法"才算闭合；G9 的不变量会盯住这个矩阵，以后再有 token 没人写卡就直接判不合格。
    cards.append(
        {"id": "blip-duotone", "name": "双色调锁色（a:duotone / satMod / biLevel / tint）",
         "evidence": "duotone %d 处、satMod %d 处、tint %d 处、biLevel %d 处（%d 模板 OOXML 实测）" % (
             stats['effects'].get('duotone', 0), stats['effects'].get('satMod', 0),
             stats['effects'].get('tint', 0), stats['effects'].get('biLevel', 0), len(led)),
         "when": "整册要用不同来源的实景照片却必须像一整套——语料的做法不是逐张修图，"
                 "而是在图片上挂一层重新着色指令；竞赛/科技风封面最常用",
         "steps": "① 选中图片 → 「图片格式」→「颜色」→「重新着色」里挑一整套双色；"
                  "② 语料实测的这套是主色 016DFF + shade 45000 + satMod 135000 + 副色 prstClr=white；"
                  "③ 要纯黑白用 biLevel（语料 %d 处），要整体压暗用 tint（%d 处）；"
                  "④ 这层是**非破坏**的：原图一个字节没改，随时能撤，所以别把它烘进 JPG" % (
                      stats['effects'].get('biLevel', 0), stats['effects'].get('tint', 0)),
         "params": "色值只在有实测样本的风格上给：tech_blue=016DFF（NO.176 海浪封面 / NO.72 创赛级科技风）。"
                   "ink_wash / photo_real 语料里没有对应样本，引擎就不做，退回 grade_asset 的像素调色",
         'ooxml': 'p:blipFill/a:blip 内 <a:duotone><a:srgbClr val="016DFF">'
                  '<a:shade val="45000"/><a:satMod val="135000"/></a:srgbClr>'
                  '<a:prstClr val="white"/></a:duotone>（必须排在 a:extLst 之前）',
         "engine": "**引擎产出**：`tech_recipe=blip-duotone` 且 shots 里 `\"duotone\": true` 时，"
                   "`add_duotone()` 给整页实景图挂 a:duotone；没有实测色值的风格会明确写进 build_log "
                   "并被 G6 拦下，不静默退化成没做",
         "gate": "G6（声明了就必须在这页 XML 数到 a:duotone）/ G4 对比度（双色调改明度，"
                 "文字压不住要调 scrim）/ G3b（锁色不算降分辨率）"})
    cards.append(
        {"id": "emphasis-scale", "name": "放大强调（p:animScale）",
         "evidence": "animScale %d 处、emph 类节点 %d 处（%d 模板 OOXML 实测）" % (
             stats['prims'].get('animScale', 0), stats['classes'].get('emph', 0), len(led)),
         "when": "大数字落地时轻微顶一下（NO.144 动态数字过渡页放大 15%）、"
                 "花瓣/主体入场后强调（NO.136 放大 150%）；一册 ≤2 处，多了像弹窗广告",
         "steps": "动画窗格 → 添加效果 → 强调效果「放大/缩小」→ 效果选项里填百分比、"
                  "持续时间与「平滑开始/结束」（对应 accel/decel）；"
                  "注意这是**相对量**：形状本体尺寸不变，所以不会把越界闸搞坏",
         "params": "by x/y = 150000（15%）或 1500000（150%）；dur 1000–5000ms；"
                   "accel=decel=50000；presetClass=emph、presetID=6（原文见 ooxml）",
         'ooxml': 'p:timing 内 <p:cTn presetClass="emph" presetID="6" '
                  'accel="50000" decel="50000"><p:animScale><p:cBhvr>'
                  '<p:cTn dur="1000"/><p:tgtEl><p:spTgt spid="N"/></p:tgtEl></p:cBhvr>'
                  '<p:by x="150000" y="150000"/></p:animScale>',
         "engine": "**引擎产出**：`tech_recipe=emphasis-scale` 时 `inject_emphasis_scale()` 把这页第一个"
                   "文字形状放大 `scale_pct`（默认 15%），与已有的入场淡入合并进同一个 mainSeq",
         "gate": "G6（声明了就必须在这页数到 p:animScale，`anim_scale>0`）/ G8 版式（相对缩放不改本体几何，"
                 "所以不该出现越界；真越界说明有人把 by 写成了绝对尺寸）"})

    # 17–20：这一轮把"图文重排、擦入家族、视频点击暂停、坏示范否决"四件事从
    # 只写在文档里，落成引擎产出 + 成品闸门（用户指出：语料里有大量错误示范，不能照学）
    cards.append(
        {"id": "split-reflow", "name": "图文重排（图片按实测栏位裁切，文字落互补区）",
         "evidence": "母版/版式占位符实测：body 3814 个中位框 [0.069,0.300,0.425,0.537]、"
                     "title 1712 个 [0.069,0.067,0.446,0.233]、pic 203 个 [0.425,0.144,0.506,0.711]；"
                     "该原型好示范页 49 页（占好示范 5.9%），压图率实测 0.4%",
         "when": "读数据页/流程页/对比页：字压在照片上会糊（语料 346 页就是这个问题），"
                 "分栏让图上不压字；正文放不进栏位时引擎自动退回满幅原型",
         "params": 'shots.json 写 "layout": "split-centre-image"；图片 srcRect 居中裁切保持比例，'
                   '不拉伸；分栏页不压蒙版，整页铺色板底色',
         "ooxml": "p:pic/p:spPr/a:xfrm(off/ext) 按栏位 + p:blipFill/a:srcRect l/t/r/b 裁切",
         "engine": "**引擎产出**：layout_for() 选原型 -> add_picture_in_box() 按实测框裁切 -> "
                   "place_text() 落互补栏；放不进就 split_zone_fits() 判退回满幅并记进 build_log",
         "gate": "G17 图文版式（用与语料挖掘同一份 deck_measure 量成品 bbox，声明与实测量出的"
                 "原型必须一致；分栏页压图率超 TH[split_over_pic_max] 就拦）/ G5 留白"})
    cards.append(
        {"id": "wipe-family", "name": "擦入家族与位移入场（presetID 由 XML 签名自证）",
         "evidence": "entr:22 四向合计 242 次（左 109/下 66/右 32/上 35，签名就是 "
                     'a:animEffect filter="wipe(left|down|right|up)"，subtype 编码方向）；'
                     "entr:10 淡入 209 次；entr:2 飞入 116 次（p:anim 改 ppt_x/ppt_y，"
                     "取值实测含 0-#ppt_w/2、1+#ppt_w/2）；entr:53 位移+淡入 74 次",
         "when": "标题从画面外推进来、图表逐条擦开、金句升起——比一律淡入更像发布会",
         "params": 'shots.json 写 "motion_preset": "wipe-left|fly-left|rise-fade|barn|wheel|'
                   'random-bar"；"motion_out": "exit-fade"；时长取语料中位 500ms',
         "ooxml": 'p:cTn[@presetClass="entr" @presetID="22" @presetSubtype="8"] + '
                  'p:animEffect transition="in" filter="wipe(left)" + p:set visible',
         "engine": "**引擎产出**：corpus_tables.ANIM 三元组表驱动 _inject_preset()，"
                   "按签名生成 p:animEffect / p:anim(ppt_x,ppt_y) / p:set(visible|hidden)；"
                   "出场走 inject_exit()（语料 exit 108 次全是 animEffect(out)+set->hidden）",
         "gate": "G18 动效链（声明的三元组与 filter 必须在这页成品 timing 里数得到）/ G6 节奏"})
    cards.append(
        {"id": "video-click-pause", "name": "视频点击暂停/继续（interactiveSeq）",
         "evidence": '165 册、1042 页实测：每个内嵌视频除 onBegin 自动播 playFrom(0.0)（1092 次）'
                     '外，还写一条 nodeType="interactiveSeq" 的 p:seq，'
                     'onClick -> mediacall presetID=2 -> cmd="togglePause"（1036 次），'
                     '带 evtFilter="cancelBubble" 与 endSync evt="end"',
         "when": "凡是整页视频：放映时讲者必须能点画面停下",
         "params": "attach_video() 自动带上，无需声明；时长写进 cmd 的 cTn dur",
         "ooxml": 'p:seq[@concurrent="1" @nextAc="seek"] > p:cTn[nodeType="interactiveSeq"]'
                  '[@restart="whenNotActive"] > p:cond[@evt="onClick"] + p:cmd[cmd="togglePause"]',
         "engine": "**引擎产出**：inject_video_click() 在 tmRoot 的 childTnLst 里补这条 seq，"
                   "与自动播同页共存（此前引擎只写自动播，点视频不会停）",
         "gate": "G18 动效链（视频页必须同时数到 playFrom、togglePause、interactiveSeq 三样）"})
    cards.append(
        {"id": "exemplar-policy", "name": "示范质量分层（坏示范不学，且拦在成品外）",
         "evidence": "2224 页逐页判定：好示范 835 页（37.5%），坏示范 1389 页（62.5%）——"
                     "空页 560、广告推广页 504、字压亮底 346、文字墙 70、无层级小字 20、"
                     "模板编号当主视觉 16；171 册里 117 册过半是坏页",
         "when": "任何时候：语料里高频不等于值得学。学之前先过一遍这张卡",
         "params": "判据在 corpus_tables.BAD_RULES（正则+阈值），语料分层与成品闸共用同一份",
         "ooxml": "不适用（这是取舍规则，不是 XML 特性）",
         "engine": "**引擎产出**：plan/pack 不生成广告页与空页；place_text 按 pick_pt 保证字号层级；"
                   "满幅页必带压暗蒙版（语料 346 页糊字的问题不复现）",
         "gate": "G19 示范质量（成品逐页命中广告词/文字墙/无层级/编号主视觉/空页即拦，"
                 "来源署名页只豁免文字墙一条）/ G4 对比度"})


    # 语料基数会随模板库变化（本轮就删掉过一个字节级重复的 NO.173 副本，172→171），
    # 所以卡里所有"172 模板"字样一律按当前台账实际数改写，不留手抄数。
    ndeck = len(led)
    for c in cards:
        c.setdefault('source', u'CORPUS_DIR %d 模板 OOXML 实测' % ndeck)
        for k, v in list(c.items()):
            if isinstance(v, str) and u'172 模板' in v:
                c[k] = v.replace(u'172 模板', u'%d 模板' % ndeck)

    # 每张卡都要写明"引擎到底产不产"。14 张卡以前只有卡名与参数，agent 照着声明了卡，
    # build 却可能一行相关 XML 都不写（13/14 两张就是这么脱节的）。现在：
    #   引擎产出 → caofeng.py 的 RECIPE_TOKENS 会在成品 XML 里逐页数 token，声明了没写就拦；
    #   手工/部分 → 卡片如实写明要在 PowerPoint 里做哪几步，闸不拦（拦了就是误伤）。
    ENGINE_STATUS = {
        'video-fullbleed': (u'引擎产出',
            u'attach_video() + inject_autoplay_timing()：p:pic/a:blipFill + p:video/cMediaNode + '
            u'mainSeq 里 p:cmd playFrom(0.0)。验它的闸：G11（内嵌 mp4 字节与时长）、'
            u'G15（首帧链）、G3b（内嵌分辨率）'),
        'morph-camera': (u'引擎产出',
            u'add_morph() 写 mc:Choice Requires="p159" 的 p159:morph option="byObject" + Fallback fade，'
            u'share_anchor()/ensure_anchor() 保证前后页有同名锚点。验它的闸：G14（逐页配对）、'
            u'G6（p14:dur 必须 1500–2500ms）'),
        'mask-dim': (u'引擎产出',
            u'add_scrim() 在背景之上盖一段 a:gradFill 三段渐变，方向跟着留白区走；'
            u'顺序必须是 fill 在 ln 之前（G8 现在逐形状量 CT_SpPr）。验它的闸：G4（渲染像素实测对比度）、'
            u'G6（数 a:gradFill）'),
        'giant-numeral': (u'引擎产出',
            u'place_text() 在 statement 页把 HERO_BIG 抓到的数排成 150pt（a:rPr sz=15000），'
            u'标题降为 22pt。验它的闸：G6（该页必须有 ≥ TH[giant_num_sz_min] 的字号）、G5（留白）'),
        'gif-motion-element': (u'引擎产出',
            u'build 直接把 gen/pNNN/anim.gif 作为 p:pic 内嵌（放映时自动循环，不需要 p:timing）；'
            u'透明底由 scripts/make_gif.py 出带透明索引的 GIF。验它的闸：G11（帧数≥10、短边≥640、'
            u'不透明底不许叠色场）、G3（内嵌字节必须等于验收过的源文件）'),
        'text-animation-presets': (u'引擎产出',
            u'inject_entrance(preset=...) 按 corpus_tables.ANIM 的实测三元组写：淡入 entr:10:0、'
            u'擦入 entr:22:1/2/4/8（filter=wipe(up|right|down|left)）、飞入 entr:2:*（p:anim 改 '
            u'ppt_x/ppt_y）、升字 entr:53:16、轮子/门扇/随机线条各自的 animEffect filter；'
            u'出场 inject_exit()。验它的闸：G6（数 entr/emph/path 节点）+ G18（三元组与 filter '
            u'必须在这页成品 timing 里数得到）'),
        'click-gated-motion': (u'引擎产出',
            u'inject_entrance(node_type=...) 可写 afterEffect/withEffect/clickEffect 三种 nodeType，'
            u'delay 阶梯取语料实测 0/500/1000/1500/2000ms；视频页的 onClick 由 '
            u'inject_video_click() 自动带（语料 1042 页）。验它的闸：G6/G18'),
        'palette-lock': (u'引擎产出',
            u'corpus_tables.PALETTES 六个实测色板（好示范册 45/54 主用蓝）；build 时 '
            u'apply_theme() 把 dk1/lt1/dk2/lt2/accent1..6 写进 ppt/theme/theme1.xml，'
            u'文字/表格/渐变/描边全部从同一块板取。语料自己 167/171 册没改主题色，'
            u'写主题是本技能多做的一步，文档已标明。验它的闸：G16（逐页彩色必须在色板内 + '
            u'theme accent1 必须等于色板主色）'),
        'split-reflow': (u'引擎产出',
            u'layout_for()/LAYOUT_ZONE + add_picture_in_box()（srcRect 居中裁切保持比例）+ '
            u'split_zone_fits() 放不下自动退回满幅。验它的闸：G17（成品 bbox 用与语料挖掘'
            u'同一份 deck_measure 量，原型必须一致）'),
        'wipe-family': (u'引擎产出',
            u'_inject_preset() 按 ANIM 表生成 p:animEffect/p:anim/p:set 三件套。'
            u'验它的闸：G18'),
        'video-click-pause': (u'引擎产出',
            u'inject_video_click() 补 interactiveSeq + togglePause。验它的闸：G18'
            u'（视频页三样缺一即拦）'),
        'exemplar-policy': (u'引擎产出',
            u'判据集中在 corpus_tables.BAD_RULES，plan/pack/build 不产广告页与空页；'
            u'grade_exemplars.py 用它给语料分层，G19 用同一份拦成品。验它的闸：G19/G4'),
        'bool-shape': (u'手工（PowerPoint）',
            u'布尔运算/裁切出来的 custGeom 路径（语料 7785 处）引擎不生成：要在 PowerPoint 里'
            u'合并形状再另存为图片裁切。封面「变脸」六步同理（见本卡 steps）。'
            u'验它的闸：无——所以别在 shots.json 里声明这张卡指望它自动出现'),
        'motion-path': (u'手工（PowerPoint）',
            u'p:animMotion 直线/路径动画（语料 71 处，NO.107 动态长图、NO.121 手机成果、'
            u'NO.140 胶片轮播）引擎不生成：滚动 marquee 需要复制内容并手设重复次数。'
            u'验它的闸：无——声明了也不会自动出现，别指望'),
        'softedge-glow': (u'手工（PowerPoint）',
            u'a:softEdge / a:glow 效果引擎不写（field_bg 的柔和边是在 JPG 里画的，不是 OOXML 效果）。'
            u'要真发光得在 PowerPoint 形状效果里加。验它的闸：G4 只看对比度，不看发光'),
        'sound-cue': (u'手工（PowerPoint）',
            u'p:audio + a:blip 指 mediaN.mp3（语料 sndAc 104 处）引擎不嵌音频：'
            u'音频版权与体积都要人判断。验它的闸：无'),
    }
    for c in cards:
        if 'engine' not in c and c['id'] in ENGINE_STATUS:
            st, how = ENGINE_STATUS[c['id']]
            c['engine'] = u'**%s**：%s' % (st, how)
    missing = [c['id'] for c in cards if 'engine' not in c]
    if missing:
        raise SystemExit(u'技法卡缺 engine 字段（引擎产不产出必须写明）：%s' % missing)
    stats['gif'] = gif_stat
    # deck_tech.json was dumped before this card existed; rewrite it now that the GIF card is in
    json.dump({'cards': cards, 'stats': stats},
              io.open(os.path.join(KB, 'deck_tech.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    # Render coverage and transcript totals used to be pasted into kb/coverage.json by hand, so a
    # re-run of this script produced a file missing rendered_decks / render_matches_ledger /
    # asr_model -- and G9 reads exactly those. Now they are derived from the work dir.
    def render_ledger(name):
        """render_done.jsonl is ONE LINE PER DECK with a 'slides' count, not one line per slide:
        counting lines reported 172 rendered slides and turned render_matches_ledger false."""
        p = os.path.join(W, name)
        if not os.path.exists(p):
            return set(), 0
        ids, slides = set(), 0
        for ln in io.open(p, encoding='utf-8'):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            ids.add(r.get('deck') or r.get('name') or '')
            slides += int(r.get('slides') or 0)
        return ids, slides
    rdecks, rslides = render_ledger('render_done.jsonl')
    tr_dir = os.path.join(W, 'transcripts')
    tr_files = sorted(glob.glob(os.path.join(tr_dir, '*.txt')))
    cov = {'decks_total': len(decks),
               'slides_total': stats['n_slides'],
               'prompt_blocks': len(blocks),
               'prompt_decks': len({b['deck'] for b in blocks}),
               'unique_videos': len(vids),
               'videos_transcribed': sum(1 for v in vids if v.get('transcript_chars', 0) > 0),
               'videos_no_audio': sum(1 for v in vids if not v.get('audio')),
               'videos_failed': sum(1 for v in vids if v.get('error')),
               'videos_frames_extracted': sum(1 for v in vids if int(v.get('frames') or 0) > 0),
               'videos_with_speech': sum(1 for v in vids if int(v.get('transcript_chars') or 0) >= 20),
               'videos_transcript_thin': sum(1 for v in vids
                                            if 0 < int(v.get('transcript_chars') or 0) < 20),
               'videos_silent_or_music': sum(1 for v in vids
                                           if v.get('audio') and not int(v.get('transcript_chars') or 0)),
               'video_entries_total': sum(len(v.get('decks') or ([v['deck']] if v.get('deck') else [])) for v in vids),
               'decks_with_embedded_video': len({d for v in vids for d in (v.get('decks') or ([v['deck']] if v.get('deck') else []))}),
               'decks_with_external_tutorial_links': len(ext_link),
               'external_link_decks': ext_link,
               'prompt_like_slides': prompt_like[0],
               'prompt_like_slides_covered': prompt_like[1],
               'prompt_like_slides_missing': prompt_like_missing[:20],
    }
    cov.update({
        'rendered_decks': len(rdecks),
        'rendered_slides': rslides,
        'render_matches_ledger': (rslides == stats['n_slides'] and len(rdecks) == len(decks)),
        'transcripts_stored': len(tr_files),
        'transcript_chars': sum(os.path.getsize(f) for f in tr_files),
        'asr_model': 'faster-whisper medium int8 (local cache, offline)',
    })
    json.dump(cov, io.open(os.path.join(KB, 'coverage.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    # the distilled study reports belong in kb/ too: an agent that only has the skill must be able
    # to read the measured technique ledger, not just the JSON cards derived from it
    for name in ('technique_report.md', 'transcript_study.md', 'visual_study.md',
                 'exemplar_study.md',
                 # 三层实测的原始数字必须随 skill 发出去：corpus_tables.py 的每一格都由
                 # check_docs.py 与这几份 JSON 对账，缺一份就等于色板/版式/动画没闸门可核
                 'color_system.json', 'layout_archetypes.json', 'animation_mechanics.json',
                 'exemplar_grades.json', 'corpus_stats.json'):
        src = os.path.join(W, name)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(KB, name))
        elif name in ('color_system.json', 'layout_archetypes.json',
                      'animation_mechanics.json', 'exemplar_grades.json'):
            # G16/G17/G18/G19 与 check_docs 的对账全读这几份，缺了就等于色板/版式/动画
            # 数字没人咬得住——这里必须停，不能让 kb 留一份过期副本
            raise SystemExit("停止：kb 依赖的实测文件缺失 %s（先跑 study_style_anim.py "
                             "与 grade_exemplars.py）" % name)
        else:
            print("  缺 %s（研习脚本没跑或 CAOFENG_WORK 指错）" % name)
    dst_tr = os.path.join(KB, 'transcripts')
    if tr_files:
        os.makedirs(dst_tr, exist_ok=True)
        for f in tr_files:
            shutil.copyfile(f, os.path.join(dst_tr, os.path.basename(f)))
    print("kb written: cards=%d prompts=%d videos=%d rendered=%d/%d reports+transcripts=%d" % (
        len(cards), len(blocks), len(vids), len(rdecks), rslides, len(tr_files)))
    print("slot coverage:", dict(sorted(slotsum.items(), key=lambda x: -x[1])))


if __name__ == '__main__':
    main()
