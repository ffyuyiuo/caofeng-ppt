"""Turn the 43 prompt-bearing slides into a slot grammar with measured coverage.

Every rule the skill will teach must be backed by a count here (n/total prompt blocks),
so the skill never asserts a convention the corpus does not actually use.
"""
import os, io, re, json, collections, sys


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
import zipfile
import xml.etree.ElementTree as ET
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}t'


P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def slide_texts(path):
    z = zipfile.ZipFile(path)
    names = [i.filename for i in z.infolist()]
    sl = sorted([n for n in names if re.match(r'ppt/slides/slide\d+\.xml$', n)],
                key=lambda s: int(re.findall(r'\d+', s)[0]))
    out = []
    for sn in sl:
        try:
            x = ET.fromstring(z.read(sn))
        except Exception:
            continue
        ts = [(t.text or '').strip() for t in x.iter(A)]
        out.append((int(re.findall(r'\d+', sn)[0]), [t for t in ts if t]))
    z.close()
    return out


# ---- slot detectors: (slot name, regex list) ----
SLOTS = [
    ("画幅比例", [r'16:9', r'横版', r'竖版', r'9:16']),
    ("输出时长", [r'约?\s*\d+\s*[-–~]?\s*\d*\s*秒', r'\d+\s*秒']),
    ("分辨率画质", [r'4K', r'8K', r'超清', r'超高清', r'HDR', r'宽动态']),
    ("渲染质感", [r'电影级', r'写实', r'超写实', r'数字艺术', r'概念设计', r'质感']),
    ("主体场景", [r'画面(中央|左|右|上|下)', r'设置一?根?座?个?张', r'画面以.{1,20}为核心']),
    ("位置百分比", [r'占画面(宽度|高度)约?\d+', r'约\s*\d+%', r'\d+%\s*[～~-]\s*\d+%']),
    ("尺寸量化", [r'宽度约', r'高度约', r'直径', r'半径', r'厚度']),
    ("对称与栅格", [r'对称', r'等距', r'间距', r'均匀分布', r'网格']),
    ("视角", [r'俯视', r'平视', r'仰视', r'透视', r'广角', r'\d+mm', r'正面']),
    ("镜头运动", [r'镜头(缓慢|逐渐|保持|向前|向后|推|拉|摇|移|环绕)', r'推进', r'拉远', r'环绕', r'景深']),
    ("光线", [r'光(源|柱|环|晕|带|束)', r'体积光', r'轮廓光', r'反射光', r'逆光', r'漫射光', r'辉光']),
    ("色彩管控", [r'严格控制在', r'为主色', r'避免其他颜色', r'饱和', r'色相', r'渐变']),
    ("材质", [r'玻璃材质', r'丝绸', r'纸张', r'金属', r'纹理', r'透明']),
    ("层级纵深", [r'前景', r'中景', r'远景', r'纵深', r'层次']),
    ("风格锚点", [r'苹果发布会', r'宣传片', r'展厅', r'驾驶舱', r'控制中心', r'数据中心', r'东方美学', r'史诗']),
    ("动效事件", [r'出现', r'展开', r'生长', r'扩散', r'汇聚', r'旋转', r'漂浮', r'穿梭', r'点亮', r'迸发', r'飘动']),
    ("秒级分镜", [r'\d+\s*[-–]\s*\d*\s*秒', r'[①②③④⑤⑥⑦]', r'第\s*\d+\s*秒']),
    ("首尾帧参考", [r'附件\s*\d', r'作为首帧', r'作为(最终)?结尾?帧', r'尾帧', r'参考(原始)?视频', r'上传的']),
    ("留白占位", [r'保持(完全)?空白', r'不放任何文字', r'内部.{0,6}干净', r'占位区域', r'不要增加内容']),
    ("禁项负面", [r'不要出现', [r'不生成', r'禁止', r'避免'], r'不要增加', r'无水印', r'不是.{1,14}而是']),
    ("文字禁令", [r'不要出现任何文字', r'任何文字、数字、字母', r'Logo', r'字幕']),
    ("运动约束", [r'保持稳定', r'平滑运动', r'不要(突然|一直|大量)', r'克制', r'缓慢']),
    ("定格收束", [r'定格', r'稳定停留', r'不再增加', r'最后.{0,6}秒']),
    ("动作逻辑", [r'动作逻辑', r'触发动作', r'之前不能', r'必须.{0,10}之后才', r'整个过程', r'顺序']),
    ("用途声明", [r'PPT\s*(动态)?背景', r'作为\s*PPT', r'适合作为', r'信息架构']),
]

NEG_LABELS = {"负面提示词": "negative"}


def classify(header):
    if '负面' in header:
        return 'negative'
    if '首帧' in header and '图' in header:
        return 'image_firstframe'
    if '尾帧' in header and '图' in header:
        return 'image_lastframe'
    if '视频' in header:
        return 'video'
    if '图片' in header or re.search(r'提示词', header):
        return 'image'
    return 'unknown'


def main():
    blocks = []
    for f in sorted(os.listdir(SRC)):
        if not f.lower().endswith('.pptx'):
            continue
        for n, ts in slide_texts(os.path.join(SRC, f)):
            joined = ' '.join(ts)
            if '提示词' not in joined:
                continue
            # split the slide into labelled sub-blocks at each "*提示词" marker
            marks = [(m.start(), m.group(0)) for m in re.finditer(r'[^\s]{0,8}提示词[：:]{0,1}', joined)]
            if not marks:
                continue
            for i, (pos, label) in enumerate(marks):
                end = marks[i + 1][0] if i + 1 < len(marks) else len(joined)
                body = joined[pos + len(label):end]
                if len(body.strip()) < 40:
                    continue
                blocks.append({'deck': f, 'slide': n, 'label': label.strip(),
                               'kind': classify(label), 'body': body.strip()})
    # slot coverage
    cov = collections.Counter()
    bykind = collections.defaultdict(collections.Counter)
    kindn = collections.Counter()
    lens = collections.defaultdict(list)
    for b in blocks:
        kindn[b['kind']] += 1
        lens[b['kind']].append(len(b['body']))
        for slot, pats in SLOTS:
            hit = False
            for pt in pats:
                if isinstance(pt, list):
                    if any(re.search(p, b['body']) for p in pt):
                        hit = True
                elif re.search(pt, b['body']):
                    hit = True
            if hit:
                cov[slot] += 1
                bykind[b['kind']][slot] += 1
    o = io.open(os.path.join(W, 'prompt_grammar.md'), 'w', encoding='utf-8')
    o.write("# 提示词语法槽位统计（语料：%d 个模板 %d 个提示词块）\n\n" % (
        len({b['deck'] for b in blocks}), len(blocks)))
    o.write("块类型分布：%s\n\n" % dict(kindn))
    o.write("平均字符长度：%s\n\n" % {k: int(sum(v) / len(v)) for k, v in lens.items() if v})
    o.write("## 槽位覆盖率（全部块 / 视频块 / 图片块）\n\n")
    nv, ni = kindn['video'], kindn['image']
    o.write("| 槽位 | 全部 | 视频 | 图片 |\n|---|---|---|---|\n")
    for slot, _ in SLOTS:
        o.write("| %s | %d/%d (%.0f%%) | %d/%d | %d/%d |\n" % (
            slot, cov[slot], len(blocks), 100.0 * cov[slot] / len(blocks),
            bykind['video'][slot], nv, bykind['image'][slot], ni))
    o.write("\n## 逐块清单（截断）\n\n")
    for i, b in enumerate(blocks, 1):
        o.write("### %d. [%s] %s s%d 标签=%s 长度=%d\n\n%s\n\n" % (
            i, b['kind'], b['deck'][:40], b['slide'], b['label'], len(b['body']), b['body']))
    o.close()
    json.dump(blocks, io.open(os.path.join(W, 'prompt_blocks.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print("blocks=%d kinds=%s" % (len(blocks), dict(kindn)))
    print("top slots:", cov.most_common(12))


if __name__ == '__main__':
    main()
