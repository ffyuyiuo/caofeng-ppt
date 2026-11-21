"""Render the delivery card: click-level steps for the 即梦/豆包 web workflow.

The card has to be enough on its own -- an earlier version only pasted prompts and used a
relative path with a `video.png.mp4` filename bug, so the user still had to go read another file
to know which mode/aspect/duration to pick in the web UI.
"""
import os, io, json
import delivery_gate as DG

COMMON_STEPS = [
    "浏览器打开即梦 `jimeng.jianying.com` 并登录（免费额度）；豆包图像在 `doubao.com` 的「图像生成」。",
    "生图：左侧选 **「AI 作图 / 图片生成」** → 比例选 **16:9** → 分辨率选**最高档** → "
    "把该页 `image` 段**整段**粘进提示词框。",
    "若该页列了「参考素材」：点提示词框旁的 **「上传参考图」**，选给出的绝对路径文件。",
    "生视频：左侧选 **「AI 视频 / 视频生成」** → 模式：只有一张参考图选 **「图生视频」**，"
    "有首帧+尾帧两张选 **「首尾帧」** → 比例 **16:9** → 时长按该页写的秒数选（5–10 秒）→ "
    "粘贴该页 `video` 段 → 上传首帧（和尾帧）→ 生成 → 下载。",
    "实拍图通道（用户指定）：**Unsplash 官网 `https://unsplash.com`，页面右上角可切换中文** → "
    "用下面每页给的检索 URL 搜「关键词 / 颜色 / 分类」→ 鼠标移到图上 hover 出现 ↓ → 点 ↓ 选「原始大小」下载。"
    "注册可建收藏夹（上传作品每周限 10 张起）。Unsplash License 不强制署名，但来源页 URL 与作者仍按卡上格式登记。",
    "下载后**改名放回该页列出的精确绝对路径**，再跑 `ack --work <目录> --ready`，然后 `build` 和 `check`。",
    "铁律：**生成的画面里不许有文字**。模型自己写了字这张就不能用——重跑并在开头加一句"
    "「画面中绝对不要出现任何文字」。带字的图会让成品不可编辑。",
    "出图出视频都走本文件的网页版步骤：**不要用 Qoder 自带 ImageGen**——它消耗你的模型积分，"
    "右下角还会烧「AI生成」水印。整页背景在即梦里选 16:9 + 高清/2K 档（短边要 ≥1080，"
    "低档位会被装配降级成矢量色场）。",
]

EXT = {'image': '.png', 'first': '.png', 'last': '.png', 'video': '.mp4', 'gif': '.gif'}


def _card_status():
    """技法卡"由谁产出"要写到投放卡上：手工类（布尔裁切、直线动画、发光、音效）装配不会替
    用户做出效果，卡上不说清，用户就会以为成品里已经有了、验收时才发现缺。"""
    kb = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kb', 'deck_tech.json')
    try:
        d = json.load(io.open(kb, encoding='utf-8'))
        return dict((c['id'], (c.get('engine') or '').strip()) for c in d.get('cards') or [])
    except Exception:
        return {}


def write(cards, man, shots, out, links=None):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(out), '..', 'gen'))
    bypage = {s['page']: s for s in shots['pages']}
    eng = _card_status()
    o = io.open(out, 'w', encoding='utf-8')
    o.write("# 投放卡（即梦 / 豆包 网页版 + Unsplash 实拍）\n\n")
    o.write("## 通用步骤（每页一样，下面每页只写差异）\n\n")
    for i, step in enumerate(COMMON_STEPS, 1):
        o.write("%d. %s\n" % (i, step))
    n_pending = 0
    for c in cards:
        sh = bypage.get(c['page'], {})
        # build lets shots.json override the page title, so the card must show the same title the
        # deck will carry, not the outline draft pack happened to read
        head = sh.get('headline') or c['headline']
        o.write("\n---\n\n## 页 %d ｜ %s ｜ %s\n\n" % (c['page'], sh.get('role', ''), head))
        if sh.get('shot_intent'):
            o.write("**镜头意图**：%s\n\n" % sh['shot_intent'])
        if sh.get('tech_recipe'):
            o.write("**技法卡**：`%s`（参数与 OOXML 写法见 references/cinema-recipes.md）\n\n"
                    % sh['tech_recipe'])
            st = eng.get(sh['tech_recipe'], '')
            if st:
                o.write("  这张卡由谁产出：%s\n\n" % st)
        if sh.get('motion'):
            o.write("**动效参数**：%s\n\n" % sh['motion'])
        z = sh.get('reserved_zone')
        if z:
            o.write("**留白区（文字只能落在这里）**：x %.2f–%.2f, y %.2f–%.2f\n\n"
                    % (z[0], z[0] + z[2], z[1], z[1] + z[3]))
        m = man.get(c['page'])
        if m:
            o.write("**参考素材（上传这张，绝对路径）**\n\n    %s\n\n" % os.path.abspath(m['file']))
            o.write("  来源 %s\n  %dx%d ｜ 许可 %s ｜ 相关度 %s%%\n\n" % (
                str(m.get('url', ''))[:100], m.get('w', 0), m.get('h', 0),
                m.get('license') or '许可未知', m.get('relevance', '?')))
        u = (links or {}).get(c['page']) or []
        if u:
            o.write("### 免版权实拍图（Unsplash 中文站，hover 点 ↓ 选「原始大小」下载）\n\n")
            for it in u:
                o.write("- %s：%s\n" % (it['term'], it['url']))
            o.write("\n  下到合适的图后登记来源，裸图不许直接进成品：\n\n")
            o.write("  `python caofeng.py fetch --work <目录> --adopt <下载的文件> --page %d "
                    "--url <照片页地址> --artist <作者>`\n\n" % c['page'])
            o.write("  这条通道和下面的文生图二选一：实拍更稳，文生图更贴题。\n\n")
        for kind, fp in c['files'].items():
            o.write("### %s 提示词（复制整段）\n\n```\n%s\n```\n\n" % (
                kind, io.open(fp, encoding='utf-8').read().strip()))
        pdir = os.path.join(base_dir, 'p%03d' % c['page'])
        # 需求 4 要求"附带上传的图片"必须写清楚是哪一张：素材可能是爬到的真实图，也可能是
        # 本页已经生成的文生图结果。只说"本页文生图结果"等于没交代，用户会传错文件或什么都不传。
        att = []
        if m and m.get('file') and os.path.exists(m['file']):
            att.append((u'附件1（爬到的真实素材，优先用这张）', os.path.abspath(m['file'])))
        for k_att, label in (('first', u'附件1（首帧）'), ('last', u'附件2（尾帧）'),
                             ('image', u'附件1（本页文生图结果）')):
            p_att = os.path.join(pdir, k_att + EXT[k_att])
            if os.path.exists(p_att) and not (m and k_att == 'image'):
                att.append((label, os.path.abspath(p_att)))
        if att and any(k in c['files'] for k in ('video', 'gif', 'first')):
            o.write("### 要上传的附件（图生视频/图生动图必须传，绝对路径）\n\n")
            for label, p_att in att:
                o.write("- %s：`%s`\n" % (label, p_att))
            o.write(u"\n  不传附件的纯文生视频在语料里只有 6/22 段用到，且最容易跑偏；"
                    u"G15 会把「有视频却没有首帧参考图」判成硬伤。\n\n")

        o.write("### 回收文件（放回这些精确绝对路径）\n\n")

        got = 0
        for kind in ('image', 'first', 'last', 'video', 'gif'):
            if kind in c['files']:
                p = os.path.join(pdir, kind + EXT[kind])
                extra = ''
                if kind == 'video':
                    extra = "（时长 5–10 秒，本页建议 %s 秒）" % sh.get('video_secs', 8)
                o.write("- `%s`\n" % p)
                got += 1
        if not got:
            n_pending += 1
            o.write("- 本页没有提示词。要么补 `--term` 后重跑 `fetch`，要么由 `scaffold` 生成提示词；"
                    "否则装配时只会走矢量色场，**不算做完了这页**。\n")
    o.write("\n---\n\n## 汇总\n\n- 卡片页数：%d\n- 需要人工投放的产物：%d 个\n"
            "- 无提示词的页：%d 页（这些页 check 会按 G2/G11 拦）\n"
            "- Unsplash 检索条目：%d 条\n\n" % (
                len(cards), sum(len(c['files']) for c in cards), n_pending,
                sum(len(v) for v in (links or {}).values())))
    o.write("## 回执（硬闸 G26：不做这一步出不了片）\n\n"
            "文件全部放回后，agent 必须问用户这一句：**「%s」**\n\n"
            "- 用户回「是」且文件已放回：`python caofeng.py ack --work <目录> --ready`\n"
            "- 用户明确回「不要视频背景」：`python caofeng.py ack --work <目录> --no-video`"
            "（只豁免 video 类，文生图/首帧/动图不豁免）\n"
            "- 卡片一旦改动（页、提示词、路径任一变化），旧回执自动失效，必须重新 pack 重新发卡\n"
            % DG.ASK)
    o.close()
    return n_pending
