# -*- coding: utf-8 -*-
"""逐帧 + 逐句研习 PPT 视频教程。

用法：python study_tutorials.py --src "CORPUS_DIR/PPT视频教程" --out "CAOFENG_WORK/tutorials"

每条视频产出四样东西，全部可续跑（已存在的产物跳过）：
  frames/<id>/f00.jpg …       场景切换采样帧（最多 18 张，画面变了才留）
  frame_ocr/<id>.txt          每张帧的 tesseract chi_sim+eng 文字（教程里屏幕上的字与提示词）
  transcript/<id>.txt         faster-whisper medium(int8) 带时间戳的讲解逐句稿
  videos.jsonl                一行一条：时长、分辨率、是否有音轨、OCR 字数、ASR 字数、提示词候选数

为什么要单独做这个：教程视频是"操作方法"的唯一记录——模板文件只给你结果，
视频给你的是怎么做（哪里点、参数填什么、提示词怎么写）。
"""
import os, sys, json, io, re, glob, time, argparse, hashlib, subprocess, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        w = shutil.which('ffmpeg')
        if w:
            return w
        raise SystemExit('停止：找不到 ffmpeg（需要 imageio-ffmpeg）')


def vid_id(path):
    return hashlib.sha1(os.path.basename(path).encode('utf8')).hexdigest()[:10]


def sample_frames(path, outdir, max_frames=18, min_gap_sec=2.0):
    """场景变化才留帧：相邻样本的 HSV 直方图相关性低于阈值才算"换画面了"。"""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(fps * 0.5)))          # 每 0.5 秒取一个候选
    last_hist, last_t, kept, idx = None, -1e9, 0, 0
    stops = set()
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                t = idx / fps
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
                cv2.normalize(hist, hist)
                if last_hist is None:
                    take = True
                else:
                    corr = cv2.compareHist(last_hist, hist, cv2.HISTCMP_CORREL)
                    take = corr < 0.86 and (t - last_t) >= min_gap_sec
                if take and kept < max_frames:
                    h, w = frame.shape[:2]
                    sw = 1000
                    sh = int(h * sw / float(w)) if w else 500
                    small = cv2.resize(frame, (sw, sh)) if w > sw else frame
                    cv2.imwrite(os.path.join(outdir, 'f%02d.jpg' % kept), small,
                                [cv2.IMWRITE_JPEG_QUALITY, 92])
                    kept += 1
                    last_hist, last_t = hist, t
        idx += 1
    cap.release()
    return kept


def ocr_frames(outdir, txt_out):
    import pytesseract
    from PIL import Image
    buf, chars = [], 0
    for fp in sorted(glob.glob(os.path.join(outdir, '*.jpg'))):
        try:
            t = pytesseract.image_to_string(Image.open(fp), lang='chi_sim+eng')
        except Exception as e:
            buf.append('== %s == OCR失败 %s' % (os.path.basename(fp), str(e)[:60]))
            continue
        t = re.sub(r'\n{3,}', '\n\n', t or '')
        chars += len(re.sub(r'\s', '', t))
        buf.append('== %s ==\n%s' % (os.path.basename(fp), t))
    io.open(txt_out, 'w', encoding='utf-8').write('\n\n'.join(buf))
    return chars


_MODEL = None


def asr(path, txt_out):
    global _MODEL
    from faster_whisper import WhisperModel
    if _MODEL is None:
        _MODEL = WhisperModel('medium', device='cpu', compute_type='int8',
                              download_root=os.path.expanduser('~/.cache/huggingface/hub'))
    wav = path + '.16k.wav'
    ff = ffmpeg_exe()
    subprocess.run([ff, '-y', '-loglevel', 'error', '-i', path, '-ac', '1', '-ar', '16000', wav],
                   check=True)
    try:
        segs, info = _MODEL.transcribe(wav, language='zh', vad_filter=True,
                                       beam_size=5, initial_prompt='PPT 模板 提示词 背景 动画 表格')
        lines, chars = [], 0
        for s in segs:
            txt = (s.text or '').strip()
            if not txt:
                continue
            chars += len(re.sub(r'\s', '', txt))
            lines.append('[%6.1f-%6.1f] %s' % (s.start, s.end, txt))
        io.open(txt_out, 'w', encoding='utf-8').write('\n'.join(lines))
        return chars
    finally:
        if os.path.exists(wav):
            os.remove(wav)


PROMPT_HINT = re.compile(u'(提示词|主体特征|构图方式|镜头角度|光线|色彩|材质|画面风格|背景元素|'
                         u'画面参数|参考帧|首帧|尾帧|运镜|分镜|生成一张|生成一段|即梦|豆包|可灵)')


def harvest_prompts(ocr_txt_path, tr_path):
    hits = []
    for p, tag in ((ocr_txt_path, '屏幕'), (tr_path, '口播')):
        if not os.path.exists(p):
            continue
        for ln in io.open(p, encoding='utf-8').read().splitlines():
            ln = ln.strip()
            if len(ln) >= 12 and PROMPT_HINT.search(ln):
                hits.append({'from': tag, 'text': ln[:300]})
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    for d in (a.src, a.out):
        if not os.path.isdir(d):
            print('停止：目录不存在 %s' % d, file=sys.stderr)
            sys.exit(2)
    for sub in ('frames', 'frame_ocr', 'transcript'):
        os.makedirs(os.path.join(a.out, sub), exist_ok=True)
    jl = os.path.join(a.out, 'videos.jsonl')
    done = set()
    if os.path.exists(jl):
        for ln in io.open(jl, encoding='utf-8'):
            try:
                done.add(json.loads(ln)['file'])
            except Exception:
                pass
    vids = sorted(glob.glob(os.path.join(a.src, '*.mp4')))
    if a.limit:
        vids = vids[:a.limit]
    J = io.open(jl, 'a', encoding='utf-8')
    for i, vp in enumerate(vids, 1):
        base = os.path.basename(vp)
        if base in done:
            continue
        vid = vid_id(vp)
        fdir = os.path.join(a.out, 'frames', vid)
        ocr_p = os.path.join(a.out, 'frame_ocr', vid + '.txt')
        tr_p = os.path.join(a.out, 'transcript', vid + '.txt')
        os.makedirs(fdir, exist_ok=True)
        t0 = time.time()
        rec = {'file': base, 'id': vid, 'path': os.path.abspath(vp),
               'bytes': os.path.getsize(vp)}
        try:
            nfr = len(glob.glob(os.path.join(fdir, '*.jpg')))
            if not nfr:
                nfr = sample_frames(vp, fdir)
            rec['frames'] = nfr
            if not os.path.exists(ocr_p):
                rec['ocr_chars'] = ocr_frames(fdir, ocr_p)
            else:
                rec['ocr_chars'] = len(re.sub(r'\s', '', io.open(ocr_p, encoding='utf-8').read()))
            if not os.path.exists(tr_p):
                rec['asr_chars'] = asr(vp, tr_p)
            else:
                rec['asr_chars'] = len(re.sub(r'\s', '', io.open(tr_p, encoding='utf-8').read()))
            rec['prompt_hits'] = len(harvest_prompts(ocr_p, tr_p))
            rec['sec'] = round(time.time() - t0, 1)
            rec['status'] = 'ok'
        except Exception as e:
            rec['status'] = 'FAIL: %s' % str(e)[:160]
        J.write(json.dumps(rec, ensure_ascii=False) + '\n')
        J.flush()
        print('%2d/%d %-44s frames=%-3s ocr=%-6s asr=%-6s prompts=%-3s %s (%.0fs)'
              % (i, len(vids), base[:44], rec.get('frames'), rec.get('ocr_chars'),
                 rec.get('asr_chars'), rec.get('prompt_hits'), rec.get('status'),
                 rec.get('sec', 0)), flush=True)
    J.close()
    print('完成 -> %s' % jl)


if __name__ == '__main__':
    main()
