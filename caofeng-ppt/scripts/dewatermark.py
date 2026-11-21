# -*- coding: utf-8 -*-
"""回执素材去水印（真删除，不是打码）：投放产物（豆包/即梦网页免费版）带平台角标。

为什么不是整块 inpaint：整框 TELEA 会留下一块糊——用户一眼认出"马赛克"。
角标是半透明白色叠加：obs = (1-a)·原图 + a·255。所以主路线是**按像素估 alpha 再反解**
（un-blend），把底下的原像素算回来，一个点都不糊：
  · 图片：局部背景用中值滤波估（笔画细，不进中值），a = 抬亮量/(255−背景)。
  · 视频：角标是唯一"跨帧稳定"的抬亮——把框内逐帧堆起来取时域中值，
    稳定抬亮就是角标；静态背景区直接反解还原（零损失），
    只有角标正好压在大幅运动的画面上时，才退化为紧贴笔画的窄掩膜 NS 修复。
原始文件永远保留（--out 默认写 *_nowm.*）——G21 溯源要拿原文件对指纹。

作者定的验收口径：平台角标必须按像素反解真删除，不许打码遮掩；
成品由 G28 拿 AI 产物原件逐帧比对复核，清完留黑疤同样判不合格。
本工具只处理你自己生成、有权使用的产物，不用于去除他人作品的水印。

用法：
    python dewatermark.py --in "PROJECT_DIR" [--box x,y,w,h]
    python dewatermark.py --in "PROJECT_DIR" [--box x,y,w,h]
退出码 0=验过去净；1=没验过（残留或框没对准）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

MARK_RE = re.compile(u'(豆包|即梦|jimeng|dreamina|通义|千问|文心|可灵|kling|sora|AI ?生成|AI-?generated)',
                     re.I)
BANDS = (('bottom', 0.84, 1.0), ('top', 0.0, 0.10))


def _ff():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def video_dims(ff, src):
    r = subprocess.run([ff, '-i', src], capture_output=True, text=True,
                       encoding='utf8', errors='replace')
    err = r.stderr or ''
    m = re.search(r'Stream.*Video.*?(\d{3,5})x(\d{3,5})', err)
    fps = re.search(r'([\d.]+) fps', err)
    dur = re.search(r'Duration: (\d+):(\d+):([\d.]+)', err)
    if not m:
        return 0, 0, 24.0, 0.0
    d = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)) if dur else 0.0
    return int(m.group(1)), int(m.group(2)), float(fps.group(1)) if fps else 24.0, d


def _ocr_words(img):
    from PIL import Image
    import pytesseract
    w, h = img.size
    big = img.resize((w * 2, h * 2), Image.LANCZOS) if w < 2200 else img
    data = pytesseract.image_to_data(big, lang='chi_sim+eng',
                                     config='--psm 6', output_type=pytesseract.Output.DICT)
    scale = 2.0 if big is not img else 1.0
    return data, scale


def find_marks(img):
    """OCR 词框命中平台角标词才认；读不出交给像素兜底或 --box。"""
    data, scale = _ocr_words(img)
    w, h = img.size
    hits = []
    for i in range(len(data['text'])):
        t = (data['text'][i] or '').strip()
        if not t or not MARK_RE.search(t):
            continue
        x, y = data['left'][i] / scale, data['top'][i] / scale
        bw, bh = data['width'][i] / scale, data['height'][i] / scale
        cy = (y + bh / 2.0) / h
        band = None
        for name, lo, hi in BANDS:
            if lo <= cy < hi:
                band = name
                break
        if band is None or bh > h * 0.08:
            continue
        hits.append((band, x, y, bw, bh, t))
    boxes = []
    for name, _, _ in BANDS:
        hs = [v for v in hits if v[0] == name]
        if not hs:
            continue
        x0 = min(v[1] for v in hs); y0 = min(v[2] for v in hs)
        x1 = max(v[1] + v[3] for v in hs); y1 = max(v[2] + v[4] for v in hs)
        pad = max(6, (y1 - y0) * 0.6)
        boxes.append({'band': name,
                      'x': max(0, x0 - pad * 2), 'y': max(0, y0 - pad),
                      'w': min(w, x1 + pad * 2) - max(0, x0 - pad * 2),
                      'h': min(h, y1 + pad) - max(0, y0 - pad),
                      'words': sorted(set(v[5] for v in hs))})
    return boxes, [v[5] for v in hits]


def find_marks_pixels(bgr, x0f=0.70, y0f=0.84):
    """下沿右侧带里找"比局部背景亮出一截、排成一条水平线"的笔画团（角标兜底检出）。"""
    import cv2
    import numpy as np
    h, w = bgr.shape[:2]
    reg_y0, reg_x0 = int(h * y0f), int(w * x0f)
    reg = bgr[reg_y0:h, reg_x0:w]
    gray = cv2.cvtColor(reg, cv2.COLOR_BGR2GRAY).astype(np.int32)
    bg = cv2.medianBlur(gray.astype(np.uint8), 15).astype(np.int32)
    mask = ((gray - bg > 38) & (gray > 120)).astype(np.uint8) * 255
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    cands = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if not (bh >= h * 0.008 and bh <= h * 0.085 and bw <= w * 0.075 and area >= 24):
            continue
        cands.append((x, y, bw, bh, cent[i][1]))
    if len(cands) < 2:
        return []
    ys = sorted(c[4] for c in cands)
    med = ys[len(ys) // 2]
    line = [c for c in cands if abs(c[4] - med) <= max(6.0, h * 0.012)]
    if len(line) < 2:
        return []
    x0 = min(c[0] for c in line) + reg_x0
    x1 = max(c[0] + c[2] for c in line) + reg_x0
    y0 = min(c[1] for c in line) + reg_y0
    y1 = max(c[1] + c[3] for c in line) + reg_y0
    if (x1 - x0) > w * 0.28 or (y1 - y0) > h * 0.10:
        return []
    pad = max(6, (y1 - y0) * 0.5)
    return [{'band': 'bottom', 'x': max(0, x0 - pad), 'y': max(0, y0 - pad * 0.7),
             'w': min(w, x1 + pad) - max(0, x0 - pad),
             'h': min(h, y1 + pad * 0.7) - max(0, y0 - pad * 0.7),
             'words': [u'像素兜底检出（OCR 没读出平台词）']}]


def _detect(img):
    import cv2
    import numpy as np
    boxes, words = find_marks(img)
    if not boxes:
        arr = cv2.cvtColor(np.asarray(img.convert('RGB')), cv2.COLOR_RGB2BGR)
        boxes = find_marks_pixels(arr)
    return boxes, words



def frame_text_mask(box_bgr, lift_min=15, gray_min=85, min_area=150, min_fill=0.15):
    """逐帧找"字形状"的抬亮团：角标会随画面漂，静态框掩膜盖不住——只能一帧一帧认。

    两把尺取并集（实测各有各漏）：
      · 未腐蚀连通域——角标压在暗地面/干净背景上时整行字是一个团（宽 ≤320、
        高 8–60、框填充率 ≥0.1），霓虹弧要么宽爆表要么填充率太低进不来；
      · 腐蚀 5×5 后的团——角标和弧焊死时未腐蚀团会并成巨块被毙，腐蚀把 3–5px
        的弧打断、留下 8–10px 的字笔画碎团。但细笔画（46px simhei 只有 4–6px 粗）
        会被腐蚀吃掉，所以两把尺必须并，不能只用腐蚀那把。"""
    import cv2
    import numpy as np
    gray = cv2.cvtColor(box_bgr, cv2.COLOR_BGR2GRAY)
    bg = cv2.morphologyEx(gray, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
    lift = gray.astype(np.int32) - bg.astype(np.int32)
    m = ((lift > lift_min) & (gray > gray_min)).astype(np.uint8) * 255
    me = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), 1)
    keep = np.zeros_like(m)
    for mm in (m, me):
        n, lab, st, ce = cv2.connectedComponentsWithStats(mm, 8)
        for i in range(1, n):
            x, y, bw, bh, area = [int(v) for v in st[i]]
            if not (8 <= bh <= 60 and 6 <= bw <= 320 and area >= min_area):
                continue
            if bw / float(bh) < 0.4 or bw / float(bh) > 14:
                continue
            if area / float(bw * bh) < min_fill:
                continue
            keep[lab == i] = 255
    return cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)), 1)



def crop_image_corner(src, dst, boxes):
    """图片也裁角优先：补帧会在豆包图标处留白块、字行处留暗疤（p011 目检实测），
    物理裁掉水印带零残留零补痕；只裁底/右两条带再中心裁 16:9 回原尺寸，放大倍率如实返回。"""
    from PIL import Image
    img = Image.open(src).convert('RGB')
    w, h = img.size
    bot = max([h - int(b['y']) for b in boxes if int(b['y']) >= h * 0.6] or [0])
    right = max([w - int(b['x']) for b in boxes if int(b['x']) >= w * 0.6] or [0])
    if bot <= 0 and right <= 0:
        return None
    if max(bot, right) + 12 > h // 3:
        return None
    cw = w - (right + 12 if right else 0)
    ch = h - (bot + 12 if bot else 0)
    target = 16 / 9.0
    if cw / float(ch) > target:
        nw = int(ch * target)
        x0 = (cw - nw) // 2
        box = (x0, 0, x0 + nw, ch)
    else:
        nh = int(cw / target)
        y0 = (ch - nh) // 2
        box = (0, y0, cw, y0 + nh)
    out = img.crop(box).resize((w, h), Image.LANCZOS)
    out.save(dst)
    if image_marks(out):
        return None
    return {'clean': True, 'method': 'corner-crop', 'boxes': [], 'words': [],
            'out': dst, 'upscale': round(w / float(box[2] - box[0]), 2)}


def clean_image(src, dst, boxes=None):
    import cv2
    import numpy as np
    from PIL import Image
    img = Image.open(src).convert('RGB')
    words = []
    if boxes is None:
        boxes, words = _detect(img)
    if not boxes:
        return {'clean': True, 'boxes': [], 'words': [], 'out': None}
    try:
        import lama_cleaner
        if lama_cleaner.clean_image_lama(src, dst, boxes):
            out = cv2.imread(dst)
            report = []
            for b in boxes:
                x, y, w_, h_ = int(b['x']), int(b['y']), int(b['w']), int(b['h'])
                x1, y1 = min(out.shape[1], x + w_), min(out.shape[0], y + h_)
                resid = frame_text_mask(out[y:y1, x:x1], lift_min=10, gray_min=80,
                                        min_area=150, min_fill=0.15)
                report.append({'box': [x, y, w_, h_], 'mask_px': w_ * h_,
                               'residual_px': int((resid > 0).sum())})
            ok = all(r['residual_px'] <= max(40, r['mask_px'] * 0.05) for r in report)
            if ok:
                return {'clean': True, 'method': 'lama', 'boxes': report, 'words': words, 'out': dst}
            return {'clean': False, 'method': 'lama', 'boxes': report, 'words': words, 'out': dst}
    except Exception:
        pass
    # 补帧不行（或 LaMa 没跑成）就裁角优先：物理裁掉水印带零残留，实测比补帧的白块/暗疤干净
    crop = crop_image_corner(src, dst, boxes)
    if crop:
        return crop
    arr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    out = arr.copy()
    report = []
    for b in boxes:
        x, y, w_, h_ = int(b['x']), int(b['y']), int(b['w']), int(b['h'])
        x1, y1 = min(arr.shape[1], x + w_), min(arr.shape[0], y + h_)
        sub = arr[y:y1, x:x1]
        # 单帧没有时域可分：检出框本身就是角标行包络（贴字收紧），整框 TELEA 推平。
        # 实测只补笔画会在字缘留 lift>10 残影（残差 1357px 被闸再拦），整框补在
        # 暗底/平滑底上看不出来；网图不走这条路（G28 只量 gen/ 投放产物）。
        # 单帧没有时域可分：检出框就是角标行包络，整框 TELEA 推平。注意 inpaint
        # 只喂框的话全掩膜没有已知像素可传播（实测原样吐回），必须带 40px 边框余量
        # 一起补，再贴回框内；网图不走这条路（G28 只量 gen/ 投放产物）。
        mx, my = 40, 40
        ex0, ey0 = max(0, x - mx), max(0, y - my)
        ex1, ey1 = min(arr.shape[1], x1 + mx), min(arr.shape[0], y1 + my)
        ext = arr[ey0:ey1, ex0:ex1]
        emask = np.zeros(ext.shape[:2], np.uint8)
        emask[y - ey0:y1 - ey0, x - ex0:x1 - ex0] = 255
        mask = frame_text_mask(sub, lift_min=6, gray_min=60, min_area=60, min_fill=0.08)
        if not mask.any():
            report.append({'box': [x, y, w_, h_], 'mask_px': 0})
            continue
        rec_ext = cv2.inpaint(ext, emask, 12, cv2.INPAINT_TELEA)
        rec = rec_ext[y - ey0:y1 - ey0, x - ex0:x1 - ex0]
        resid = frame_text_mask(rec, lift_min=10, gray_min=80, min_area=150,
                                min_fill=0.15)
        out[y:y1, x:x1] = rec
        report.append({'box': [x, y, w_, h_], 'mask_px': int((mask > 0).sum()),
                       'residual_px': int((resid > 0).sum())})
    Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB)).save(dst)
    acted = [r for r in report if r.get('mask_px')]
    ok = bool(acted) and all(r['residual_px'] <= max(40, r['mask_px'] * 0.05) for r in acted)
    return {'clean': ok, 'method': 'telea', 'boxes': report, 'words': words, 'out': dst}




PROPAINER = os.environ.get('CAOFENG_PROPAINER', 'CAOFENG_WORK/ProPainter')


def band_marks_fast(img):
    """单帧角标快检（不走 OCR，引擎 G28 用）：只量右下角角标区的字形状笔画团。

    平台角标永远钉在右下角（少数左下），不在整幅下沿乱扫——实测满幅霓虹弧会被
    宽域扫描误判成"字形状"（17/16 帧假命中）。区取右 30% 宽 × 下 16% 高。"""
    import cv2
    import numpy as np
    a = np.asarray(img.convert('RGB'))
    h, w = a.shape[:2]
    x0 = int(w * 0.70)
    y0 = int(h * 0.84)
    sub = cv2.cvtColor(a[y0:h, x0:w], cv2.COLOR_RGB2BGR)
    m = frame_text_mask(sub, lift_min=10, gray_min=80, min_area=120, min_fill=0.12)
    if not m.any():
        return []
    ys, xs = np.where(m > 0)
    return [{'band': 'corner', 'x': int(xs.min()) + x0, 'y': int(ys.min()) + y0,
             'w': int(xs.max() - xs.min()), 'h': int(ys.max() - ys.min()),
             'px': int((m > 0).sum())}]


def image_marks(img):
    """图片侧角标判据：只认"贴着右下角根"的一排字形笔画。

    实测（2848×1600 的即梦底图）：'豆包 AI 生成' 的字形簇 bottom=0.981~0.983、
    right=0.933~0.989，且同一基线上 ≥3 块；而提示词本来就要求画的 HUD 面板边、
    刻度虚线落在 bottom=0.878~0.894——单用 band_marks_fast 会把这种干净图判成带标
    （实测假命中 7325px），整册投放回收会被误拦。"""
    import cv2
    import numpy as np
    a = np.asarray(img.convert('RGB'))
    h, w = a.shape[:2]
    x0 = int(w * 0.70)
    y0 = int(h * 0.84)
    sub = cv2.cvtColor(a[y0:h, x0:w], cv2.COLOR_RGB2BGR)
    m = frame_text_mask(sub, lift_min=10, gray_min=80, min_area=120, min_fill=0.12)
    if not m.any():
        return []
    retval, _lab, st, _cent = cv2.connectedComponentsWithStats((m > 0).astype(np.uint8), 8)
    keep = []
    for i in range(1, retval):
        x, y, cw, chh, ar = st[i]
        bottom = (y + y0 + chh) / float(h)
        right = (x + x0 + cw) / float(w)
        if bottom >= 0.93 and right >= 0.85:
            keep.append({'band': 'corner', 'x': int(x + x0), 'y': int(y + y0),
                         'w': int(cw), 'h': int(chh), 'px': int(ar),
                         'bottom': round(bottom, 3), 'right': round(right, 3)})
    if len(keep) < 2:
        return []
    ys = [k['y'] + k['h'] / 2.0 for k in keep]
    if max(ys) - min(ys) > h * 0.05:
        return []
    return keep


def sample_video_marks(path, n=16):
    """视频抽 n 帧跑角区检测，返回"位置一致"的最大命中簇。

    角标跨帧钉在同一角（±8% 容差内漂移），霓虹弧位置满幅乱跳——只数落在同一
    角区簇里的帧，孤立弧假命中不计。

    判据只有一个定义点：能跑时序检测时一律以它为准（单帧大核中值残差 + 窗口
    持续性 + 中性亮色）。老的角区启发式在"相机几乎不动 + 角落有发光结构"上会
    成片误报——实测清洗后的封面视频仍有 13/14 帧被右下角的电路辉光判成角标，
    而时序检测在同一份产物上报 0。角区启发式只在时序模块不可用时兜底。"""
    try:
        import lama_cleaner as LC
        ff = _ff()
        w, h, fps, dur = video_dims(ff, path)
        wins = LC.detect_overlay_windows(path, ff)
        if w and dur:
            total = max(1, int(dur * fps))
            cov = set()
            for (s, e, bx) in wins:
                for i in range(s, min(e, total)):
                    cov.add(i)
            return int(round(len(cov) / float(total) * n))
    except Exception:
        pass
    import subprocess
    import numpy as np
    from PIL import Image
    ff = _ff()
    w, h, fps, dur = video_dims(ff, path)
    if not w or not dur:
        return 0
    step = max(1, int(round(dur * fps / max(n, 1))))
    # ffmpeg 5+ 移除了 -vsync，改用 -fps_mode passthrough；旧版仍认 -vsync 0。
    # 探测一次并缓存，保持"不复制帧"的抽帧语义跨版本一致。
    global _FRAME_MODE
    if '_FRAME_MODE' not in globals():
        _probe = subprocess.run([ff, '-hide_banner', '-h', 'full'],
                                capture_output=True, text=True, errors='replace')
        _FRAME_MODE = (['-fps_mode', 'passthrough']
                       if '-fps_mode' in (_probe.stdout or '') else ['-vsync', '0'])
    p_ = subprocess.Popen([ff, '-v', 'error', '-i', path, '-vf',
                           "select='not(mod(n,%d))'" % step] + _FRAME_MODE +
                          ['-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
                          stdout=subprocess.PIPE)
    fb = w * h * 3
    centers = []
    tot = 0
    while tot < n + 2:
        buf = p_.stdout.read(fb)
        if not buf or len(buf) < fb:
            break
        arr = np.frombuffer(buf, np.uint8).reshape((h, w, 3))
        hits = band_marks_fast(Image.fromarray(arr[:, :, ::-1].copy()))
        if hits:
            b = hits[0]
            centers.append((b['x'] + b['w'] / 2.0, b['y'] + b['h'] / 2.0))
        tot += 1
    try:
        p_.stdout.close()
    except Exception:
        pass
    p_.kill()
    p_.wait()
    if not centers:
        return 0
    best = 0
    for cx, cy in centers:
        c = sum(1 for ox, oy in centers if abs(ox - cx) < w * 0.08
                and abs(oy - cy) < h * 0.08)
        best = max(best, c)
    return best


def sample_dark_corner(path, n=12, luma_max=12):
    """抽 n 帧，数右下角角区里"近黑"像素个数，返回逐帧列表（拿不到帧返回 []）。

    动因（2026-11-08 实测）：逐帧 TELEA 把压在霓虹弧上的角标补成了黑疤——同一条片子
    原片角区近黑像素 0 个，classic 清完变 3000~11000 个，8 个时间点逐帧目检比水印还扎眼。
    只量"亮笔画还在不在"的那把尺会放这种片子过闸，所以补这把：跟原件比角区变黑了多少。"""
    import subprocess
    import numpy as np
    import cv2
    ff = _ff()
    w, h, fps, dur = video_dims(ff, path)
    if not w or not dur:
        return []
    step = max(1, int(round(dur * fps / max(n, 1))))
    # ffmpeg 5+ 移除了 -vsync，改用 -fps_mode passthrough；旧版仍认 -vsync 0。
    # 探测一次并缓存，保持"不复制帧"的抽帧语义跨版本一致。
    global _FRAME_MODE
    if '_FRAME_MODE' not in globals():
        _probe = subprocess.run([ff, '-hide_banner', '-h', 'full'],
                                capture_output=True, text=True, errors='replace')
        _FRAME_MODE = (['-fps_mode', 'passthrough']
                       if '-fps_mode' in (_probe.stdout or '') else ['-vsync', '0'])
    p_ = subprocess.Popen([ff, '-v', 'error', '-i', path, '-vf',
                           "select='not(mod(n,%d))'" % step] + _FRAME_MODE +
                          ['-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
                          stdout=subprocess.PIPE)
    fb = w * h * 3
    x0, y0 = int(w * 0.70), int(h * 0.84)
    counts = []
    while len(counts) < n + 2:
        buf = p_.stdout.read(fb)
        if not buf or len(buf) < fb:
            break
        arr = np.frombuffer(buf, np.uint8).reshape((h, w, 3))
        g = cv2.cvtColor(arr[y0:h, x0:w], cv2.COLOR_BGR2GRAY)
        counts.append(int((g < luma_max).sum()))
    try:
        p_.stdout.close()
    except Exception:
        pass
    p_.kill()
    p_.wait()
    return counts


def smear_vs_original(clean_path, orig_path, n=12, thresh=800):
    """去标疤尺子：清过的片子角区比原件黑了多少（逐帧中位数差）。

    >thresh 判"补出来的是疤不是背景"。没有原件对照时返回 None——这条只在
    有 G21 原件（AI产物\\pNNN）时才许下结论，不然合法的暗背景会被冤枉。"""
    import statistics
    a = sample_dark_corner(clean_path, n)
    b = sample_dark_corner(orig_path, n)
    if not a or not b:
        return None
    d = int(statistics.median(a) - statistics.median(b))
    return {'delta': d, 'clean_median': int(statistics.median(a)),
            'orig_median': int(statistics.median(b)), 'smear': d > thresh}


def propainter_ready():
    if not (os.path.isdir(PROPAINER)
            and os.path.exists(os.path.join(PROPAINER, 'inference_propainter.py'))
            and os.path.exists(os.path.join(PROPAINER, 'weights', 'ProPainter.pth'))):
        return False
    try:
        import einops  # noqa: F401  ProPainter 模型依赖，缺了推理必崩（实测 ModuleNotFoundError）
    except Exception:
        return False
    return True


def clean_video_propainter(src, dst, boxes):
    """深度视频补帧路线（首选）：全帧上下文 + 只圈右下角角标区的掩膜。

    实测教训：小窗（300×125 / 232×80）喂 ProPainter 会把洞填成黑疤或灰疤——
    模型需要整幅上下文才补得像原背景；经典 TELEA 的天花板是"8 成帧干净、
    个别帧淡残影"，G28 机检不放。所以默认走全帧，241 帧 720p 在 4060 上
    一小时以上还没吐帧（实测 241 帧 720p 在 8GB 显存上跑满 78 分钟无输出）。任何一步不成立返回 None，交回经典路线兜底。"""
    import cv2
    import numpy as np
    import tempfile
    ff = _ff()
    w, h, fps, dur = video_dims(ff, src)
    if not w or not dur:
        return None
    tmp = os.path.join(tempfile.gettempdir(), 'dwm_propainter_%d' % os.getpid())
    shutil.rmtree(tmp, ignore_errors=True)
    fdir = os.path.join(tmp, 'frames')
    mdir = os.path.join(tmp, 'masks')
    os.makedirs(fdir)
    os.makedirs(mdir)
    x0, y0 = int(w * 0.70), int(h * 0.84)
    p = subprocess.Popen([ff, '-v', 'error', '-i', src, '-f', 'rawvideo',
                          '-pix_fmt', 'bgr24', '-'], stdout=subprocess.PIPE)
    fb = w * h * 3
    i = 0
    hits = 0
    while True:
        buf = p.stdout.read(fb)
        if not buf or len(buf) < fb:
            break
        f = np.frombuffer(buf, np.uint8).reshape((h, w, 3)).copy()
        cv2.imwrite(os.path.join(fdir, '%05d.png' % i), f)
        m = frame_text_mask(f[y0:h, x0:w], lift_min=6, gray_min=60,
                            min_area=60, min_fill=0.08)
        if m.any():
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)), 1)
            hits += 1
        full = np.zeros((h, w), np.uint8)
        full[y0:h, x0:w] = m
        cv2.imwrite(os.path.join(mdir, '%05d.png' % i), full)
        i += 1
    p.wait()
    if i == 0:
        return None
    if hits < max(2, int(i * 0.1)):
        shutil.rmtree(tmp, ignore_errors=True)
        return {'clean': True, 'boxes': [], 'words': [], 'out': None,
                'note': u'角区逐帧检出的笔画团不足，可能本无角标'}
    outdir = os.path.join(tmp, 'out')
    r = subprocess.run([sys.executable, os.path.join(PROPAINER, 'inference_propainter.py'),
                        '-i', fdir, '-m', mdir, '-o', outdir,
                        '--save_frames', '--fp16', '--subvideo_length', '60'],
                       capture_output=True, text=True, encoding='utf8', errors='replace',
                       cwd=PROPAINER, timeout=14400)
    fixdir = os.path.join(outdir, 'frames', 'frames')
    if not os.path.isdir(fixdir) or not os.path.exists(os.path.join(fixdir, '0000.png')):
        sys.stderr.write(u'ProPainter 没出结果，退回经典路线：%s' %
                         ((r.stdout or '') + (r.stderr or ''))[-400:])
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    enc = subprocess.Popen([ff, '-v', 'error', '-y', '-framerate', str(fps),
                            '-start_number', '0', '-i', os.path.join(fixdir, '%04d.png'),
                            '-i', src, '-map', '0:v', '-map', '1:a?',
                            '-c:v', 'libx264', '-crf', '16', '-preset', 'medium',
                            '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-shortest', dst],
                           capture_output=True)
    enc.wait()
    resid = sample_video_marks(dst, 16)
    sm = smear_vs_original(dst, src)
    shutil.rmtree(tmp, ignore_errors=True)
    return {'clean': (resid <= 1 and not (sm or {}).get('smear')),
            'method': 'propainter-fullframe',
            'frames': i, 'masked_frames': hits, 'residual_cluster_hits': resid,
            'smear': sm, 'boxes': [], 'words': [], 'out': dst}


def clean_video(src, dst, boxes=None, deep=False):
    """视频去角标：默认逐帧窄掩膜 TELEA（241 帧 720p 实测 24 秒，认领环节用这条）；
    deep=True 才上 ProPainter 全帧深度补帧（本机实测 78 分钟未吐帧，只给重点页挂机试）。

    逐帧 TELEA 只重画检出的笔画团（13px 膨胀盖羽化圈），运动背景帧帧保留；
    实测天花板是"约 8 成帧干净、个别帧淡残影"，机验不放行时要如实报，
    由人决定用静态图底还是在即梦付费档重导一版无水印视频——
    整行横带推平那种"马赛克"已删，不许再作为交付。"""
    import cv2
    import numpy as np
    from PIL import Image
    ff = _ff()
    w, h, fps, dur = video_dims(ff, src)
    if not w:
        return {'clean': False, 'why': u'读不到视频规格'}
    probe = os.path.join(os.path.dirname(dst) or '.', '_dwm_probe.png')
    subprocess.run([ff, '-v', 'error', '-y', '-i', src, '-frames:v', '1', probe],
                   capture_output=True)
    words = []
    if boxes is None:
        boxes, words = _detect(Image.open(probe).convert('RGB'))
    # 首帧检不出不等于全片没有：角标会淡入淡出、漂移，故深度路线自己逐帧角区检测。
    # 但深度路线是"一小时以上还不一定出帧"的量级（实测 241 帧 720p 在 4060 8GB 上跑满 78 分钟未吐一帧），
    # 绝不能作为认领的默认动作——一页一轮会被它钉死。只有显式 deep=True（CLI --deep）
    # 或 CAOFENG_DEEP=1 才走；默认逐帧 TELEA（241 帧 720p 实测 24 秒），到不了的就如实报。
    if deep or os.environ.get('CAOFENG_DEEP') == '1':
        try:
            import lama_cleaner
            if lama_cleaner.propainter2_ready():
                res = lama_cleaner.clean_video_propainter2(src, dst, boxes)
                if res is not None:
                    os.path.exists(probe) and os.remove(probe)
                    return res
        except Exception:
            pass
    # 自动路线先走"时序叠加层检测 + 逐帧精修"：水印会淡入淡出、换位，单帧角区检测既漏
    # 又把场景彩光当水印打成黑疤。时序判据（持续性 + 中性亮色）能自己把它从画面里分出来。
    if boxes is None:
        try:
            import lama_cleaner
            res = lama_cleaner.clean_video_overlay(src, dst)
            if res is not None:
                res.update({'boxes': [], 'words': words, 'out': dst,
                            'deep_cmd': u'python "%s" --in "%s" --out "%s" --deep' % (
                                os.path.abspath(__file__), src, dst)})
                os.path.exists(probe) and os.remove(probe)
                return res
        except Exception:
            pass
    os.path.exists(probe) and os.remove(probe)
    x0, y0 = int(w * 0.70), int(h * 0.84)
    dec = subprocess.Popen([ff, '-v', 'error', '-i', src, '-f', 'rawvideo',
                            '-pix_fmt', 'bgr24', '-'], stdout=subprocess.PIPE)
    enc = subprocess.Popen([ff, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                            '-s', '%dx%d' % (w, h), '-r', str(fps), '-i', '-',
                            '-i', src, '-map', '0:v', '-map', '1:a?',
                            '-c:v', 'libx264', '-crf', '16', '-preset', 'medium',
                            '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-shortest', dst],
                           stdin=subprocess.PIPE)
    fb = w * h * 3
    n = 0
    masked = 0
    hard = 0
    while True:
        buf = dec.stdout.read(fb)
        if not buf or len(buf) < fb:
            break
        arr = np.frombuffer(buf, np.uint8).reshape((h, w, 3)).copy()
        m = frame_text_mask(arr[y0:h, x0:w], lift_min=6, gray_min=60,
                            min_area=60, min_fill=0.08)
        if m.any():
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)), 1)
            arr[y0:h, x0:w] = cv2.inpaint(arr[y0:h, x0:w], m, 10, cv2.INPAINT_TELEA)
            masked += 1
            if frame_text_mask(arr[y0:h, x0:w], lift_min=8, gray_min=65,
                               min_area=50, min_fill=0.08).any():
                hard += 1
        enc.stdin.write(arr.tobytes())
        n += 1
    enc.stdin.close(); enc.wait(); dec.wait()
    resid = sample_video_marks(dst, 16)
    sm = smear_vs_original(dst, src)
    ok = (n > 0 and resid <= 1 and not (sm or {}).get('smear'))
    return {'clean': bool(ok), 'method': 'classic-telea', 'frames': n,
            'masked_frames': masked, 'residual_cluster_hits': resid, 'smear': sm,
            'note': (u'补出来的是黑疤不是背景（角区近黑像素比原件多 %d 个）——这条素材逐帧目检'
                     u'已否决，只能 --deep 深度补帧或无水印重导' % sm['delta']) if (sm or {}).get('smear')
            else (u'经典路线天花板：个别帧可能留淡残影；要 100% 干净跑 --deep（ProPainter 全帧，本机实测 78 分钟未吐帧，只能挂着试）'
                  if ok else u'机验未过——残字簇仍能检出，不许直接上屏；重点页用 --deep 挂机清，或在即梦无水印档重导'),
            'deep_cmd': u'python "%s" --in "%s" --out "%s" --deep' % (
                os.path.abspath(__file__), src, dst),
            'boxes': [], 'words': words, 'out': dst}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='src', required=True)
    ap.add_argument('--out', default='')
    ap.add_argument('--box', default='', help='x,y,w,h 像素；agent 目检点名角标位置时手工给')
    ap.add_argument('--deep', action='store_true',
                    help='视频用 ProPainter 全帧深度补帧（本机实测 78 分钟未吐帧，认领默认不走这条）')
    a = ap.parse_args()
    src = os.path.abspath(a.src)
    stem, ext = os.path.splitext(src)
    dst = os.path.abspath(a.out) if a.out else stem + '_nowm' + ext
    boxes = None
    if a.box:
        x, y, w_, h_ = [float(v) for v in a.box.split(',')]
        boxes = [{'band': 'manual', 'x': x, 'y': y, 'w': w_, 'h': h_,
                  'words': [u'手工框（agent 目检）']}]
    if ext.lower() in ('.mp4', '.mov', '.webm', '.m4v'):
        res = clean_video(src, dst, boxes=boxes, deep=a.deep)
    else:
        res = clean_image(src, dst, boxes=boxes)
    res['src'] = src
    print(json.dumps(res, ensure_ascii=False)[:600])
    if res.get('clean'):
        if res.get('out'):
            print(u'水印已删除（只重画笔画像素，不是整块打码）：%s' % res['out'])
        else:
            print(u'没检出平台角标，原件无需处理。')
        sys.exit(0)
    if res.get('deep_cmd'):
        print(u'机验未过，下一刀（挂机试，本机 78 分钟未吐帧，别在认领里等）：')
        print(u'  ' + res['deep_cmd'])
    print(u'去水印没验过：%s' % json.dumps(res, ensure_ascii=False)[:300])
    sys.exit(1)


if __name__ == '__main__':
    main()
