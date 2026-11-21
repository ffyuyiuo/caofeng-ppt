"""Mine the tutorial transcripts for teachable statements: named effects, concrete parameters,
tool workflows. Output is a study report the skill's references are written from.

Rules:
  - Only sentences that actually contain an instruction or a number are kept.
  - Every excerpt keeps its video id so a claim can be traced back to a recording.
  - Terms with zero hits are reported as zero, not guessed.
"""
import os, io, re, json, glob, collections, sys


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
OUT = io.open(os.path.join(W, 'transcript_study.md'), 'w', encoding='utf-8')

TERM_GROUPS = {
 '平滑/Morph': [r'平滑', r'morph', r'Morph'],
 '动画窗格': [r'动画窗格', r'动画面板', r'窗格'],
 '持续时间': [r'持续时间', r'时长'],
 '延迟': [r'延迟'],
 '重复/循环': [r'重复', r'循环播放', r'直到下次单击'],
 '效果选项': [r'效果选项'],
 '布尔运算': [r'布尔', r'合并形状', r'剪除', r'相交', r'组合'],
 '蒙版': [r'蒙版', r'遮罩'],
 '柔化边缘': [r'柔化边缘', r'柔边'],
 '渐变/透明': [r'渐变', r'透明度', r'不透明'],
 '图片裁剪': [r'裁剪', r'裁切'],
 '参照线/网格': [r'参照线', r'网格线', r'智能参照'],
 '选择窗格': [r'选择窗格'],
 '触发器': [r'触发器'],
 '幻灯片切换': [r'切换', r'幻灯片过渡'],
 '视频自动播放': [r'自动播放', r'单击时播放', r'播放视频'],
 '音频': [r'音频', r'音效', r'配乐'],
 '即梦/AI生视频': [r'即梦', r'豆包', r'可灵', r'AI 生', r'AI生成', r'文生视频', r'图生视频', r'首帧', r'尾帧', r'提示词'],
 '导出视频': [r'导出视频', r'另存为视频', r'生成视频'],
 '字体': [r'字体', r'字号', r'思源', r'微软雅黑'],
 '配色': [r'配色', r'取色', r'色板'],
 '版式/栅格': [r'版式', r'栅格', r'对齐', r'分布'],
 '图表动画': [r'图表', r'按系列', r'按类别'],
 '表格动画': [r'表格'],
 '路径动画': [r'自定义路径', r'动作路径', r'绘制路径'],
 '3D/模型': [r'三维', r'3D', r'模型'],
 '插件': [r'iSlide', r'OneKey', r'插件', r'MotionGo'],
}

NUM_SENT = re.compile(r'(\d+\s*(秒|毫秒|帧|像素|磅|%|％|度|倍)|持续时间|延迟|重复|不透明度|半径|宽度|高度)')


def main():
    idx = json.load(io.open(os.path.join(W, 'videos_index_seed.json'), encoding='utf-8')) if os.path.exists(
        os.path.join(W, 'videos_index_seed.json')) else None
    recs = {}
    for f in glob.glob(os.path.join(W, 'videos_s*.jsonl')):
        for ln in io.open(f, encoding='utf-8'):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            recs[r['id']] = r
    hits = collections.Counter()
    excerpts = collections.defaultdict(list)
    total_chars = 0
    n_files = 0
    param_lines = []
    for vid, r in sorted(recs.items()):
        tp = os.path.join(W, 'transcripts', vid + '.txt')
        if not os.path.exists(tp):
            continue
        txt = io.open(tp, encoding='utf-8').read()
        if len(txt) < 40:
            continue
        n_files += 1
        total_chars += len(txt)
        deck = (r.get('deck') or '')[:38]
        for s in re.split(r'(?<=[。！？])', txt):
            s = s.strip()
            if len(s) < 6:
                continue
            for g, pats in TERM_GROUPS.items():
                if any(re.search(p, s) for p in pats):
                    hits[g] += 1
                    if len(excerpts[g]) < 14:
                        excerpts[g].append((deck, s[:190]))
            if NUM_SENT.search(s) and len(param_lines) < 400 and len(s) <= 120:
                param_lines.append((deck, s))
    OUT.write("# 教程讲解语料挖掘（%d 份逐字稿，共 %d 字）\n\n" % (n_files, total_chars))
    OUT.write("## 主题命中句数（0 就是 0，不猜）\n\n")
    for g in TERM_GROUPS:
        OUT.write("- %-16s %d\n" % (g, hits.get(g, 0)))
    OUT.write("\n## 分组原话摘录（含出处模板）\n\n")
    for g in TERM_GROUPS:
        if not excerpts[g]:
            continue
        OUT.write("\n### %s\n\n" % g)
        for deck, s in excerpts[g]:
            OUT.write("- [%s] %s\n" % (deck, s))
    OUT.write("\n## 带数值的操作指令（前 200 条）\n\n")
    for deck, s in param_lines[:200]:
        OUT.write("- [%s] %s\n" % (deck, s))
    OUT.close()
    print("transcripts mined: %d files, %d chars" % (n_files, total_chars))
    print("top groups:", hits.most_common(10))
    print("param lines:", len(param_lines))


if __name__ == '__main__':
    main()
