"""Transcribe + frame-OCR every UNIQUE tutorial video across the corpus.

Videos are read out of the .pptx archives by CRC+size signature (1038 embedded entries -> 250 unique),
plus the standalone .mp4 files sitting directly in the corpus root (3 more, deduped by the same
signature), streamed to a temp file, then deleted, so peak disk stays ~1 video.

Outputs (under OUT):
  videos.jsonl   one line per unique video: id, crc, size, duration, resolution, has_audio,
                 first deck+path, all decks reusing it, asr chars, frame ocr chars
  transcripts/<id>.txt       ASR text with timestamps
  frames/<id>/*.jpg          sampled frames
  frame_ocr/<id>.txt         tesseract chi_sim OCR of those frames
Resumable: a video already present in videos.jsonl is skipped.
"""
import os, sys, json, io, re, time, glob, zipfile, hashlib, subprocess, shutil, collections


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
OUT = require_work(os.environ.get("CAOFENG_WORK", "CAOFENG_WORK"))
TMP = os.path.join(OUT, "_vidtmp")
VDIR = os.path.join(OUT, "transcripts")
FDIR = os.path.join(OUT, "frames")
ODIR = os.path.join(OUT, "frame_ocr")
for d in (TMP, VDIR, FDIR, ODIR, os.path.join(OUT, "sheets")):
    os.makedirs(d, exist_ok=True)

import imageio_ffmpeg

FF = imageio_ffmpeg.get_ffmpeg_exe()

TESS = os.environ.get("TESSERACT", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
VID_EXT = ('.mp4', '.m4v', '.wmv', '.avi', '.mov', '.mpg', '.mpeg')


def registry():
    reg = {}
    for f in sorted(os.listdir(SRC)):
        if not f.lower().endswith('.pptx'):
            continue
        p = os.path.join(SRC, f)
        try:
            z = zipfile.ZipFile(p)
        except Exception:
            continue
        for i in z.infolist():
            if not re.match(r'ppt/media/media\d+\.\w+$', i.filename, re.I):
                continue
            if not i.filename.lower().endswith(VID_EXT):
                continue
            key = (i.CRC, i.file_size)
            e = reg.get(key)
            if e is None:
                reg[key] = {'crc': i.CRC, 'size': i.file_size, 'deck': f, 'path': i.filename,
                            'decks': [f]}
            else:
                if f not in e['decks']:
                    e['decks'].append(f)
        z.close()
    # 语料根目录还直接放着几个教程视频（不在任何 pptx 里）。早期版本只扫 pptx 的 media，
    # 这几个就被"全量研读"漏掉了——需求 3 要的是无遗漏。用 crc32+size 当键，
    # 与 pptx 内嵌的同一份文件会自然去重（同一份字节不会转写两遍）。
    import zlib
    for f in sorted(os.listdir(SRC)):
        if not f.lower().endswith(VID_EXT):
            continue
        p = os.path.join(SRC, f)
        if not os.path.isfile(p):
            continue
        try:
            data = open(p, 'rb').read()
        except Exception:
            continue
        key = (zlib.crc32(data) & 0xffffffff, len(data))
        e = reg.get(key)
        if e is None:
            reg[key] = {'crc': key[0], 'size': len(data), 'deck': '(独立文件)', 'path': f,
                        'decks': ['(独立文件)'], 'standalone': True}
        elif '(独立文件)' not in e['decks']:
            e['decks'].append('(独立文件)')
            e['standalone'] = True
    return reg



def probe(path):
    r = subprocess.run([FF, '-hide_banner', '-i', path], capture_output=True, text=True,
                       encoding='utf8', errors='replace')
    err = r.stderr or ''
    m = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.\d+)', err)
    dur = 0.0
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    res = re.search(r'(\d{3,5})x(\d{3,5})', err)
    return {'dur': dur, 'w': int(res.group(1)) if res else 0, 'h': int(res.group(2)) if res else 0,
            'audio': 'Audio:' in err, 'video': 'Video:' in err}


def extract(z, inner, dest):
    with z.open(inner) as fsrc, open(dest, 'wb') as fdst:
        shutil.copyfileobj(fsrc, fdst, 1024 * 1024)


def sample_frames(src, outdir, dur, maxframes=24):
    os.makedirs(outdir, exist_ok=True)
    for x in os.listdir(outdir):
        try:
            os.remove(os.path.join(outdir, x))
        except Exception:
            pass
    fps = min(1.0, float(maxframes) / max(dur, 1.0))
    r = subprocess.run([FF, '-hide_banner', '-loglevel', 'error', '-y', '-i', src,
                        '-vf', 'fps=%s,scale=1280:-2' % ('%.6f' % fps),
                        '-frames:v', str(maxframes), os.path.join(outdir, 'f%03d.jpg')],
                       capture_output=True, text=True, encoding='utf8', errors='replace')
    if r.returncode != 0:
        raise RuntimeError('ffmpeg frames rc=%d %s' % (r.returncode, (r.stderr or '')[:160]))
    return sorted(os.path.join(outdir, x) for x in os.listdir(outdir) if x.endswith('.jpg'))


def ocr_files(files, dest):
    """Opt-in only: tesseract is unusable on cinematic frames (it emits garbage), kept for
    UI-screenshot-like sources. Callers must gate on needs_ocr."""
    buf = []
    for fp in files:
        try:
            r = subprocess.run([TESS, fp, 'stdout', '-l', 'chi_sim', '--psm', '6'],
                               capture_output=True, text=True, encoding='utf8', errors='replace',
                               timeout=120)
            t = (r.stdout or '').strip()
            t = re.sub(r'[ \t\u3000]+', ' ', t)
            t = '\n'.join(x.strip() for x in t.splitlines() if x.strip())
            if t:
                buf.append('== %s ==\n%s' % (os.path.basename(fp), t))
        except Exception as e:
            buf.append('== %s == OCR失败 %s' % (os.path.basename(fp), str(e)[:60]))
    io.open(dest, 'w', encoding='utf-8').write('\n'.join(buf))
    return sum(len(x) for x in buf)


def contact_sheet(files, dest, cols=4):
    """Tile sampled frames into one JPEG so a human/model can read the motion progression."""
    from PIL import Image
    if not files:
        return 0
    n = min(len(files), cols * 2)
    ims = []
    for fp in files[:n]:
        try:
            im = Image.open(fp).convert('RGB')
        except Exception:
            continue
        im.thumbnail((480, 270))
        ims.append(im)
    if not ims:
        return 0
    rows = (len(ims) + cols - 1) // cols
    cw = max(i.width for i in ims)
    ch = max(i.height for i in ims)
    sheet = Image.new('RGB', (cw * cols, ch * rows), (12, 12, 14))
    for k, im in enumerate(ims):
        sheet.paste(im, ((k % cols) * cw, (k // cols) * ch))
    sheet.save(dest, quality=82)
    return len(ims)


_model = None


def asr(wavpath):
    global _model
    if _model is None:
        os.environ['HF_HUB_OFFLINE'] = '1'
        from faster_whisper import WhisperModel
        _model = WhisperModel("medium", device="cpu", compute_type="int8",
                              cpu_threads=int(os.environ.get('ASR_THREADS', '8')),
                              download_root=os.path.expanduser("~/.cache/huggingface/hub"))
    segs, info = _model.transcribe(wavpath, language="zh", beam_size=1, vad_filter=True,
                                   initial_prompt="下面是 PowerPoint 动画制作教程的中文讲解，包含平滑、动画窗格、持续时间、蒙版、布尔运算等术语。")
    lines = []
    for s in segs:
        lines.append("[%.1f-%.1f] %s" % (s.start, s.end, s.text.strip()))
    return "\n".join(lines)


def prompt_decks():
    """Decks whose slides literally carry AI prompts (图片/视频提示词); their videos are priority 0."""
    import xml.etree.ElementTree as ET
    AT = '{http://schemas.openxmlformats.org/drawingml/2006/main}t'
    decks = set()
    for f in sorted(os.listdir(SRC)):
        if not f.lower().endswith('.pptx'):
            continue
        try:
            z = zipfile.ZipFile(os.path.join(SRC, f))
        except Exception:
            continue
        hit = False
        for n in z.namelist():
            if re.match(r'ppt/slides/slide\d+\.xml$', n):
                try:
                    x = ET.fromstring(z.read(n))
                except Exception:
                    continue
                if any('提示词' in (t.text or '') for t in x.iter(AT)):
                    hit = True
                    break
        z.close()
        if hit:
            decks.add(f)
    return decks


def _done_ids_all():
    """Every id already recorded by any shard (incl. the priority shards), so shards never redo work."""
    ids = set()
    for f in glob.glob(os.path.join(OUT, 'videos_s*.jsonl')):
        try:
            for ln in io.open(f, encoding='utf-8'):
                try:
                    ids.add(json.loads(ln)['id'])
                except Exception:
                    pass
        except Exception:
            pass
    return ids


def main():
    shard = 0
    nshard = 1
    if len(sys.argv) >= 2:
        shard = int(sys.argv[1]); nshard = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    prio_only = os.environ.get('PRIO_ONLY') == '1'
    pdecks = prompt_decks()
    done = set()
    jl = os.path.join(OUT, 'videos_s%d%s.jsonl' % (shard, 'P' if prio_only else ''))
    if os.path.exists(jl):
        for ln in io.open(jl, encoding='utf-8'):
            try:
                done.add(json.loads(ln)['id'])
            except Exception:
                pass
    done |= _done_ids_all()
    reg = registry()
    items = []
    for key, v in sorted(reg.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        vid = hashlib.md5(('%d_%d' % key).encode()).hexdigest()[:16]
        vv = dict(v)
        vv['id'] = vid
        vv['prio'] = 0 if (set(vv['decks']) & pdecks) else 1
        items.append(vv)
    if prio_only:
        items = [v for v in items if v['prio'] == 0]
    elif os.environ.get('PRIO_EXCLUDE') == '1':
        items = [v for v in items if v['prio'] == 1]
    todo = [v for i, v in enumerate(items) if i % nshard == shard and v['id'] not in done]
    print("pool=%d prio0=%d shard=%d/%d todo=%d done=%d" % (
        len(items), sum(1 for v in items if v['prio'] == 0), shard, nshard, len(todo), len(done)), flush=True)
    with io.open(jl, 'a', encoding='utf-8') as J:
        for n, v in enumerate(todo, 1):
            vid = v['id']
            t0 = time.time()
            rec = dict(v)
            rec.pop('crc', None)
            try:
                mp4 = os.path.join(TMP, vid + '.mp4')
                if v.get('standalone'):
                    # 独立存放的教程视频：直接从语料根目录拿，不经过 zip
                    import shutil
                    shutil.copyfile(os.path.join(SRC, v['path']), mp4)
                else:
                    z = zipfile.ZipFile(os.path.join(SRC, v['deck']))
                    extract(z, v['path'], mp4)
                    z.close()
                pr = probe(mp4)

                rec.update(pr)
                wav = os.path.join(TMP, vid + '.wav')
                if pr['audio'] and pr['dur'] > 1.5:
                    subprocess.run([FF, '-hide_banner', '-loglevel', 'error', '-y', '-i', mp4,
                                    '-ac', '1', '-ar', '16000', wav], capture_output=True)
                    if os.path.exists(wav) and os.path.getsize(wav) > 4000:
                        txt = asr(wav)
                        io.open(os.path.join(VDIR, vid + '.txt'), 'w', encoding='utf-8').write(txt)
                        rec['asr_chars'] = len(txt)
                        os.remove(wav)
                if pr['video']:
                    fd = os.path.join(FDIR, vid)
                    fr = sample_frames(mp4, fd, pr['dur'], maxframes=int(os.environ.get('MAXFR', '8')))
                    rec['frames'] = len(fr)
                    rec['sheet'] = contact_sheet(fr, os.path.join(OUT, 'sheets', vid + '.jpg'))
                    if os.environ.get('NEEDS_OCR') == '1':
                        rec['ocr_chars'] = ocr_files(fr, os.path.join(ODIR, vid + '.txt'))
                os.remove(mp4)
            except Exception as e:
                rec['error'] = str(e)[:200]
            rec['secs'] = round(time.time() - t0, 1)
            J.write(json.dumps(rec, ensure_ascii=False) + "\n")
            J.flush()
            if n % 10 == 0 or n == len(todo):
                print("  [%d/%d] %s dur=%.1fs audio=%s asr=%s ocr=%s (%.0fs)" % (
                    n, len(todo), vid[:8], rec.get('dur', 0), rec.get('audio'),
                    rec.get('asr_chars', 0), rec.get('ocr_chars', 0), time.time() - t0), flush=True)
    print("shard %d complete" % shard, flush=True)


if __name__ == '__main__':
    main()
