# -*- coding: utf-8 -*-
"""音画对齐研习：把"讲了什么"和"画面在做什么"逐句对上，提取操作/参数/设计规则/被否排版。

用户 2026-11-07 的方法论纠正：只截每页 PPT 去学会掺进错误示范且片面；必须同时听讲解、
对照画面看它点了哪个键、改了哪些参数、哪些排版被否。旧 study_tutorials.py 的帧是场景采样
（无时间戳）且 OCR 全是乱码，等于没看画面——本脚本重做：

  1. 按 ASR 逐句的时间中点抽帧（ffmpeg 精确 seek），帧带时间戳；
  2. 帧放大 2 倍 + tesseract chi_sim+eng --psm 6 重 OCR（UI 小字旧参数读不出）；
  3. 每句讲解对齐到它发生时的那一帧 OCR，并分类：
     ops（点击/右键/插入/合并形状/动画/切换…）、params（透明度/字号/磅/秒/厘米/%/延迟…）、
     rules（排版/留白/对齐/对比/层次…）、rejected（不行/不要/太挤/删掉/改回…）；
  4. 产出 kb/video_logic.json（逐句证据链）与 kb/video_logic/<id>.md（人读的操作链）。

用法：python study_video_logic.py --src "CORPUS_DIR/PPT视频教程" --work CAOFENG_WORK/tutorials
"""
import os, sys, io, re, json, glob, argparse, subprocess, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OPS = re.compile(u'(点击|单击|双击|右键|插入|复制|粘贴|剪切|合并形状|结合|剪除|拆分|相交|'
                 u'设置|选择|打开|拖动|拖拽|按住|键盘|Ctrl|Shift|Alt|动画|切换|效果选项|窗格|'
                 u'填充|轮廓|阴影|渐变|透明度|对齐|组合|置于底层|置于顶层|平滑|陀螺旋|放大|缩小|'
                 u'淡化|飞入|路径|轮播|旋转|翻转|裁剪|裁切|取色器|吸管|格式刷|SmartArt|图表|柱状图|'
                 u'数据标签|间隙宽度|图例|网格线|文本框|形状|矩形|圆形|立方体|箭头|图标)')
PARAMS = re.compile(u'(透明度\\s*\\d+|字号\\s*\\d+|\\d+\\s*磅|\\d+\\s*秒|\\d+\\s*厘米|\\d+\\s*%|'
                    u'百分之\\d+|延迟\\s*\\d*|持续时间\\s*\\d*|间隙宽度|半径\\s*\\d+|模糊\\s*\\d+|'
                    u'\\d+\\s*pt|高度\\s*\\d+|宽度\\s*\\d+|不透明度\\s*\\d+)')
RULES = re.compile(u'(排版|留白|对齐|对比|层次|层级|居中|左对齐|右对齐|高级感|简洁|拥挤|太挤|'
                   u'放不下|呼吸|视觉|焦点|强调|突出|统一|一致|比例|字号|字体|颜色|配色|蒙版|遮罩|'
                   u'出血|满幅|背景|底纹| banding |间隔|间距)')
REJECT = re.compile(u'(不行|不要|错误|太挤|放不下|删掉|去掉|改回|不好|杂乱|乱|丑|失败|避免|切忌|'
                    u'不能|别把|千万不要)')


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        w = shutil.which('ffmpeg')
        if w:
            return w
        raise SystemExit('停止：找不到 ffmpeg')


def parse_transcript(path):
    out = []
    for ln in io.open(path, encoding='utf-8'):
        m = re.match(r'\[\s*([\d.]+)-\s*([\d.]+)\]\s*(.*)', ln.strip())
        if m:
            out.append({'t0': float(m.group(1)), 't1': float(m.group(2)), 'said': m.group(3)})
    return out


def grab_frame(ff, video, t, dest):
    if os.path.exists(dest):
        return True
    r = subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-ss', '%.2f' % t,
                        '-i', video, '-frames:v', '1', '-q:v', '2', '-y', dest],
                       capture_output=True)
    return r.returncode == 0 and os.path.exists(dest)


def ocr_frame(path):
    try:
        import pytesseract
        from PIL import Image
        im = Image.open(path).convert('RGB')
        im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
        txt = pytesseract.image_to_string(im, lang='chi_sim+eng', config='--psm 6')
        return re.sub(r'\s+', ' ', txt).strip()
    except Exception as e:
        return u'(OCR 失败：%s)' % str(e)[:60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--work', required=True, help='study_tutorials.py 的产物目录')
    ap.add_argument('--kb', default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'kb'))
    ap.add_argument('--only', default='', help='只跑这些视频 id（逗号分隔）')
    a = ap.parse_args()
    ff = ffmpeg_exe()
    only = set(x for x in a.only.split(',') if x)
    vmeta = {}
    for ln in io.open(os.path.join(a.work, 'videos.jsonl'), encoding='utf-8'):
        r = json.loads(ln)
        vmeta[r['id']] = r
    outdir = os.path.join(a.work, 'aligned_frames')
    os.makedirs(outdir, exist_ok=True)
    logic = []
    for tp in sorted(glob.glob(os.path.join(a.work, 'transcript', '*.txt'))):
        vid = os.path.basename(tp)[:-4]
        if only and vid not in only:
            continue
        meta = vmeta.get(vid) or {}
        video = meta.get('path') or ''
        if not os.path.exists(video):
            cand = glob.glob(os.path.join(a.src, '*.mp4'))
            video = next((c for c in cand if os.path.basename(c).startswith(vid)), '')
        segs = parse_transcript(tp)
        d = os.path.join(outdir, vid)
        os.makedirs(d, exist_ok=True)
        steps = []
        for i, s in enumerate(segs):
            tm = (s['t0'] + s['t1']) / 2.0
            fp = os.path.join(d, 's%02d_%.1f.jpg' % (i, tm))
            ok = grab_frame(ff, video, tm, fp) if video else False
            shown = ocr_frame(fp) if ok else u''
            said = s['said']
            steps.append({
                't0': s['t0'], 't1': s['t1'], 'said': said,
                'frame': os.path.basename(fp) if ok else None,
                'shown': shown[:400],
                'ops': sorted(set(OPS.findall(said))),
                'params': sorted(set(PARAMS.findall(said))),
                'rules': sorted(set(RULES.findall(said))),
                'rejected': bool(REJECT.search(said)),
            })
        logic.append({'id': vid, 'file': meta.get('file'), 'sec': meta.get('sec'),
                      'steps': steps})
        md = [u'# %s（%s，%.0fs）音画对齐操作链' % (vid, meta.get('file'), meta.get('sec') or 0), u'']
        for st in steps:
            md.append(u'- [%.1f–%.1f] 讲：%s' % (st['t0'], st['t1'], st['said']))
            if st['ops']:
                md.append(u'  - 操作：%s' % u'、'.join(st['ops']))
            if st['params']:
                md.append(u'  - 参数：%s' % u'、'.join(st['params']))
            if st['rules']:
                md.append(u'  - 设计规则：%s' % u'、'.join(st['rules']))
            if st['rejected']:
                md.append(u'  - ⚠ 被否/警示：%s' % st['said'])
            if st['shown']:
                md.append(u'  - 画面文字：%s' % st['shown'][:160])
        io.open(os.path.join(a.kb, 'video_logic', vid + '.md'), 'w', encoding='utf-8').write(u'\n'.join(md))
        print('aligned', vid, len(steps), 'steps')
    os.makedirs(os.path.join(a.kb, 'video_logic'), exist_ok=True)
    io.open(os.path.join(a.kb, 'video_logic.json'), 'w', encoding='utf-8').write(
        json.dumps(logic, ensure_ascii=False, indent=1))
    print('videos', len(logic), 'steps', sum(len(v['steps']) for v in logic))


if __name__ == '__main__':
    os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'kb', 'video_logic'), exist_ok=True)
    main()