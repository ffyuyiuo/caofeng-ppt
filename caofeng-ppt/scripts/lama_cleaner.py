# -*- coding: utf-8 -*-
"""LaMa 图片 inpaint + ProPainter 视频 inpaint 的稳健封装（真删除，不是打码）。

为什么用 TorchScript big-lama.pt 而不走 bin/predict.py：官方 saicinpainting 训练栈钉死
pytorch-lightning==1.2.9 / albumentations==0.5.2，与本机 torch 2.13 / py3.11 / numpy2 不兼容，
load_checkpoint 一路 import 必崩。big-lama.pt 是同一个 big-lama 模型的 TorchScript 导出，
torch.jit.load 直接上 GPU，零额外依赖，是 lama-cleaner/IOPaint 的生产路线。

权重与中间产物一律落 D 盘，不进 C 盘技能目录。任何一步不成立返回 None，交回调用方 TELEA 兜底。
"""
import os
import shutil
import subprocess
import sys

import numpy as np

LAMA_ROOT = os.environ.get('CAOFENG_LAMA', 'TOOLS_DIR')
LAMA_PT = os.path.join(LAMA_ROOT, 'big-lama.pt')
PROPAINER2 = os.environ.get('CAOFENG_PROPAINER2', 'TOOLS_DIR')
TMP_BASE = os.environ.get('CAOFENG_DWM_TMP', 'CAOFENG_WORK/dwm_tmp')

_MODEL = None
_MODEL_DEV = None


def _ff():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def lama_ready():
    if not os.path.exists(LAMA_PT):
        return False
    try:
        import torch  # noqa: F401
    except Exception:
        return False
    return True


def _load_lama():
    global _MODEL, _MODEL_DEV
    import torch
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    if _MODEL is None or _MODEL_DEV != dev:
        # torch.jit.load 走 C++ fopen，中文路径 errno 42；传文件对象绕开。
        with open(LAMA_PT, 'rb') as f:
            _MODEL = torch.jit.load(f, map_location=dev)
        _MODEL.eval()
        _MODEL_DEV = dev
    return _MODEL, dev


def _boxes_to_mask(shape, boxes):
    import numpy as np
    h, w = shape[:2]
    m = np.zeros((h, w), np.uint8)
    for b in boxes or []:
        x, y = int(b['x']), int(b['y'])
        x1, y1 = min(w, x + int(b['w'])), min(h, y + int(b['h']))
        x, y = max(0, x), max(0, y)
        if x1 > x and y1 > y:
            m[y:y1, x:x1] = 255
    return m


def _lama_inpaint_arr(model, dev, rgb, mask):
    import numpy as np
    import torch
    import torch.nn.functional as F
    h, w = mask.shape
    ph, pw = (8 - h % 8) % 8, (8 - w % 8) % 8
    img = torch.from_numpy(rgb.astype(np.float32) / 255.0).permute(2, 0, 1).unsqueeze(0)
    msk = torch.from_numpy((mask > 0).astype(np.float32)).unsqueeze(0).unsqueeze(0)
    if ph or pw:
        img = F.pad(img, (0, pw, 0, ph), mode='reflect')
        msk = F.pad(msk, (0, pw, 0, ph), mode='constant', value=0)
    img, msk = img.to(dev), msk.to(dev)
    # FFC 含复数 FFT，half 会触发 ComplexHalf 不支持；LaMa 必须 fp32。
    with torch.no_grad():
        out = model(img, msk)
    out = out.float()[0, :, :h, :w].permute(1, 2, 0).cpu().numpy()
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


def clean_image_lama(src, dst, mask_boxes):
    """原图+水印掩膜 -> 真修复图（GPU）。失败返回 None 让调用方退 TELEA。"""
    if not lama_ready():
        return None
    try:
        import cv2
        import numpy as np
        from PIL import Image
        # cv2.imread 中文路径 errno 42 读不出（实测）；PIL 读再转 BGR，与 torch.jit.load 同坑同解
        rgb = np.asarray(Image.open(src).convert('RGB'))
        mask = _boxes_to_mask(rgb.shape, mask_boxes)
        if not mask.any():
            return None
        model, dev = _load_lama()
        try:
            res = _lama_inpaint_arr(model, dev, rgb, mask)
        except RuntimeError:
            # 全幅 OOM 时退到大上下文裁剪（不是小窗）：掩膜外扩 >=384px 再补，贴回掩膜区。
            ys, xs = np.where(mask > 0)
            mg = 384
            y0, y1 = max(0, ys.min() - mg), min(rgb.shape[0], ys.max() + mg)
            x0, x1 = max(0, xs.min() - mg), min(rgb.shape[1], xs.max() + mg)
            sub = _lama_inpaint_arr(model, dev, rgb[y0:y1, x0:x1], mask[y0:y1, x0:x1])
            res = rgb.copy()
            mm = mask[y0:y1, x0:x1] > 0
            res[y0:y1, x0:x1][mm] = sub[mm]
        out = cv2.cvtColor(res, cv2.COLOR_RGB2BGR)
        # cv2.imwrite 中文路径同样写不出；imencode + tofile 绕开
        ok, buf = cv2.imencode(os.path.splitext(dst)[1] or '.png', out)
        if ok:
            buf.tofile(dst)
            return dst
        return None
    except Exception:
        return None


def propainter2_ready():
    if not (os.path.isdir(PROPAINER2)
            and os.path.exists(os.path.join(PROPAINER2, 'inference_propainter.py'))):
        return False
    for w in ('ProPainter.pth', 'raft-things.pth', 'recurrent_flow_completion.pth'):
        if not os.path.exists(os.path.join(PROPAINER2, 'weights', w)):
            return False
    try:
        import einops  # noqa: F401
    except Exception:
        return False
    return True


def _dims(ff, src):
    import dewatermark as DW
    return DW.video_dims(ff, src)


def _iter_frames(ff, src, w, h, pix='bgr24'):
    """流式逐帧解码，绝不把整片堆进内存（193 帧 1080p BGR 是 1.2GB，实测会把进程顶死）。
    调用方提前 break 时要把管道关干净，否则 ffmpeg 会在日志里刷一片 Broken pipe。"""
    fb = w * h * (3 if pix == 'bgr24' else 1)
    p = subprocess.Popen([ff, '-v', 'error', '-i', src, '-f', 'rawvideo', '-pix_fmt', pix, '-'],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    shape = (h, w, 3) if pix == 'bgr24' else (h, w)
    try:
        while True:
            buf = p.stdout.read(fb)
            if not buf or len(buf) < fb:
                break
            yield np.frombuffer(buf, np.uint8).reshape(shape).copy()
    finally:
        try:
            p.stdout.close()
        except Exception:
            pass
        p.kill()
        p.wait()


def detect_overlay_windows(src, ff=None, win=15, min_pers=12.0, vote_frac=0.3,
                           max_chroma=45.0, min_gray=85.0):
    """滑窗找"叠加层"（水印/角标）候选框——不是扫角落猜，而是让它自己暴露。

    三条判据缺一不可，实测各自挡掉一类误报：
      1) 单帧大核(31)空间中值估背景后取正残差：细笔画被中值抹掉、宽辉光保留，
         于是"叠加层"是残差，场景边缘几乎不剩；
      2) 窗口内持续性（>=30% 帧残差>=10）：镜头运动带来的高光边是瞬时的，会被平均掉；
      3) 中性亮色（色度 max-min<=45 且灰度>=85）：豆包/即梦水印是近白半透明文字，
         而场景里的蓝色灯点/青色电路残差量级更高（实测 53~113 vs 水印 16~36），
         只靠量级分不开，必须上色度。
    返回 [(start, end, [box])]；box={'x','y','w','h'}。水印漂移=多个窗口各自命中。
    """
    import cv2
    ff = ff or _ff()
    w, h, fps, dur = _dims(ff, src)
    if not w:
        return []
    out = []
    buf = []
    start = 0

    def flush(buf, start):
        if not buf:
            return []
        stacked = np.stack(buf)
        g = np.stack([cv2.cvtColor(b, cv2.COLOR_BGR2GRAY) for b in buf]).astype(np.int16)
        res = np.stack([(f - cv2.medianBlur(f.astype(np.uint8), 31).astype(np.int16)).clip(0)
                        for f in g]).astype(np.float32)
        pers = res.mean(axis=0)
        votes = (res >= 10).sum(axis=0)
        m = ((pers >= min_pers) & (votes >= max(2, int(vote_frac * len(buf))))).astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        num, _, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        boxes = []
        for k in range(1, num):
            x, y, cw, ch, area = [int(v) for v in stats[k]]
            if not (300 <= area <= 0.02 * w * h):
                continue
            if not (40 <= cw <= 0.6 * w and 14 <= ch <= 0.16 * h):
                continue
            if area / float(cw * ch) < 0.12:
                continue
            sub = stacked[:, y:y + ch, x:x + cw].astype(np.float32)
            hi = sub.max(3)
            sel = hi > np.percentile(hi, 60)
            chroma = float((hi - sub.min(3))[sel].mean())
            gray = float(hi[sel].mean())
            if chroma > max_chroma or gray < min_gray:
                continue
            boxes.append({'x': x, 'y': y, 'w': cw, 'h': ch,
                          'pers': round(float(pers[y:y + ch, x:x + cw].mean()), 1),
                          'chroma': round(chroma, 1)})
        return boxes

    for fr in _iter_frames(ff, src, w, h):
        buf.append(fr)
        if len(buf) == win:
            bx = flush(buf, start)
            if bx:
                out.append((start, start + len(buf), bx))
            buf = []
            start += win
    tail = flush(buf, start)
    if tail:
        out.append((start, start + len(buf), tail))
    return out


def _frame_overlay_mask(frame, boxes, res_min=8.0, max_chroma=48.0, min_gray=80.0,
                        grow=28, dil=15):
    """窗口框只回答"往哪看"；这一层回答"这一帧到底哪几个像素是水印"。
    框内逐像素再走 残差/色度/亮度 三道门，淡入淡出与慢漂移都能自适应。

    门限不能放宽：实测把框芯降到 res>=5、色度<=110 后，水印淡出的彩色溶解帧确实被补掉了，
    但同时把 t=4.5s 左上的干净背景补成一块可见灰疤（残差扫从 17 万像素涨到 121 万）。
    彩色溶解帧与场景彩光在像素层面不可分，只能靠裁掉那几帧解决（见 trim_ghost_spans）。"""
    import cv2
    h, w = frame.shape[:2]
    reg = np.zeros((h, w), np.uint8)
    for b in boxes or []:
        x0 = max(0, int(b['x']) - grow)
        y0 = max(0, int(b['y']) - grow)
        x1 = min(w, int(b['x']) + int(b['w']) + grow)
        y1 = min(h, int(b['y']) + int(b['h']) + grow)
        reg[y0:y1, x0:x1] = 255
    if not reg.any():
        return reg
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.int16)
    res = (g - cv2.medianBlur(g.astype(np.uint8), 31).astype(np.int16)).clip(0)
    hi = frame.max(2).astype(np.int16)
    chroma = (hi - frame.min(2)).astype(np.int16)
    m = ((res >= res_min) & (chroma <= max_chroma) & (hi >= min_gray)).astype(np.uint8) * 255
    m = cv2.bitwise_and(m, reg)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    num, _, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    keep = np.zeros((h, w), np.uint8)
    for k in range(1, num):
        if int(stats[k][4]) >= 120:
            keep[stats[k][1]:stats[k][1] + stats[k][3], stats[k][0]:stats[k][0] + stats[k][2]] = 255
    if not keep.any():
        return keep
    return cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dil, dil)), 1)


def build_wm_template(src, wins=None, ff=None):
    """从源片最清晰的一帧里抠出水印字形当模板（二值图）。

    为什么必须有模板：窗口检测器对"淡入中的半透明水印"会漏报，而"没报=干净"是假绿灯
    （用户实测第 2/3 页内嵌视频仍带"豆包 AI 生成"）。水印位图是固定的，模板匹配逐帧
    都能咬住，淡的也咬得住；匹配到才补，补完再匹配分数掉到干净基线才算真删掉。"""
    import cv2
    ff = ff or _ff()
    w, h, fps, dur = _dims(ff, src)
    if not wins:
        wins = detect_overlay_windows(src, ff)
    if not wins:
        return None
    s, e, bx = max(wins, key=lambda t: max(b['pers'] for b in t[2]))
    mid = (s + e) // 2
    frame = None
    for i, f in enumerate(_iter_frames(ff, src, w, h)):
        if i == mid:
            frame = f
            break
    if frame is None:
        return None
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # 模板取整行水印：窗口内所有分量框的并集（"豆包 AI 生成"是一行，只取一个分量会匹配不稳）；
    # 且只在并集框内抠残差，不把角区里移动的光条混进模板（混进去模板就废了，实测匹配失效）。
    ux0 = min(b['x'] for b in bx)
    uy0 = min(b['y'] for b in bx)
    ux1 = max(b['x'] + b['w'] for b in bx)
    uy1 = max(b['y'] + b['h'] for b in bx)
    sub = g[uy0:uy1, ux0:ux1]
    res = cv2.subtract(sub, cv2.medianBlur(sub, 31))
    _, tm = cv2.threshold(res, 10, 255, cv2.THRESH_BINARY)
    if int((tm > 0).sum()) < 200:
        return None
    return tm


def match_wm_boxes(gray, tpl, thresh=0.30, min_neutral=150):
    """四角模板匹配；命中且框内确有"中性亮笔画"才算水印（场景蓝色光条匹配分低且彩度高，双门挡掉）。
    返回 [(score, x, y, w, h)]。掩膜使用时向右多扩 100px：豆包图标跟在文字右侧，只框字会漏图标。"""
    import cv2
    h, w = gray.shape
    th, tw = tpl.shape
    out = []
    for (y0, y1, x0, x1) in ((0, 220, 0, 560), (0, 220, w - 560, w),
                             (h - 220, h, 0, 560), (h - 220, h, w - 560, w)):
        sub = gray[y0:y1, x0:x1]
        if sub.shape[0] < th or sub.shape[1] < tw:
            continue
        r = cv2.matchTemplate(sub, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, ml = cv2.minMaxLoc(r)
        if mx < thresh:
            continue
        bx, by = x0 + ml[0], y0 + ml[1]
        box = gray[by:by + th, bx:bx + tw]
        neutral = int(((box > 90) & (box < 250)).sum())
        if neutral < min_neutral:
            continue
        out.append((float(mx), bx, by, tw, th))
    return out


def clean_video_overlay(src, dst, timeout=1800, progress_cb=None):
    """漂移水印的默认路线：时序定位 + 逐帧精修掩膜 + LaMa 真补（无 LaMa 退 TELEA）。

    比 ProPainter 快两个量级（193 帧 1080p 实测分钟级 vs 78 分钟未吐帧），
    且只重画水印笔画、运动背景逐帧原样保留。出片后拿同一个检测器复验：
    仍能检出叠加层就不算干净，返回 clean=False 交回上层如实上报。"""
    import cv2
    ff = _ff()
    w, h, fps, dur = _dims(ff, src)
    if not w:
        return None
    wins = detect_overlay_windows(src, ff)
    if not wins:
        return {'clean': True, 'method': 'overlay', 'frames': 0, 'boxes': 0,
                'note': u'时序检测没有发现任何叠加层'}
    # 复验只回看源片里"强命中"（pers>=15，实测水印 16~46、场景杂项 9~13）的那几块，
    # 否则场景里恰好过门的杂块会在成片上再报一次，把真删干净误判成失败。
    strong = [b for (_, _, bx) in wins for b in bx if b['pers'] >= 15.0]
    per = per_frame_boxes(wins, int(dur * fps) + 4)
    tpl = build_wm_template(src, wins, ff)
    model = dev = None
    if lama_ready():
        try:
            model, dev = _load_lama()
        except Exception:
            model = dev = None
    enc = subprocess.Popen([ff, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                            '-s', '%dx%d' % (w, h), '-r', repr(fps), '-i', '-',
                            '-i', src, '-map', '0:v', '-map', '1:a?',
                            '-c:v', 'libx264', '-crf', '16', '-preset', 'medium',
                            '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-shortest', dst],
                           stdin=subprocess.PIPE)
    n = hit = 0
    for frame in _iter_frames(ff, src, w, h):
        m = _frame_overlay_mask(frame, per[n] if n < len(per) else [])
        if tpl is not None:
            # 模板匹配补窗口检测器的漏：淡入中的半透明水印窗口检测器不报，但模板咬得住。
            g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            hits = match_wm_boxes(g, tpl)
            for (sc, bx, by, bw, bh) in hits:
                x0, x1 = max(0, bx - 20), min(w, bx + bw + 100)   # 右扩吞掉跟在字后的豆包图标
                y0, y1 = max(0, by - 20), min(h, by + bh + 20)
                m[y0:y1, x0:x1] = 255
            if hits:
                # 匹配位置可能有偏移（实测 t=2.0 只盖到左半行）：把命中角区里所有
                # "中性亮残差"像素都掩掉，整行自适应；蓝色光条彩度高，被色度门挡在外面。
                corners = set()
                for (sc, bx, by, bw, bh) in hits:
                    corners.add((0 if by < h // 2 else h - 220, 0 if bx < w // 2 else w - 560))
                hi = frame.max(2).astype(np.int16)
                chroma = (hi - frame.min(2)).astype(np.int16)
                res = (g.astype(np.int16) - cv2.medianBlur(g, 31).astype(np.int16)).clip(0)
                neutral = (res >= 8) & (chroma <= 70) & (hi >= 80)
                for (cy, cx) in corners:
                    reg = np.zeros((h, w), bool)
                    reg[cy:cy + 220, cx:cx + 560] = True
                    m[neutral & reg] = 255
        if m.any():
            ys, xs = np.where(m > 0)
            y0, y1 = max(0, ys.min() - 96), min(h, ys.max() + 96)
            x0, x1 = max(0, xs.min() - 96), min(w, xs.max() + 96)
            sub, sm = frame[y0:y1, x0:x1], m[y0:y1, x0:x1]
            if model is not None:
                rgb = cv2.cvtColor(sub, cv2.COLOR_BGR2RGB)
                try:
                    fill = cv2.cvtColor(_lama_inpaint_arr(model, dev, rgb, sm), cv2.COLOR_RGB2BGR)
                except Exception:
                    fill = cv2.inpaint(sub, sm, 8, cv2.INPAINT_TELEA)
            else:
                fill = cv2.inpaint(sub, sm, 8, cv2.INPAINT_TELEA)
            mm = sm > 0
            frame[y0:y1, x0:x1][mm] = fill[mm]
            hit += 1
        enc.stdin.write(frame.tobytes())
        n += 1
    enc.stdin.close()
    enc.wait()
    # 复验用模板逐帧匹配成片：匹配分仍高=水印还在。"窗口检测器没报"不算干净（实测假绿灯）。
    resid_frames = []
    if tpl is not None:
        for i, fr in enumerate(_iter_frames(ff, dst, w, h)):
            g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            if match_wm_boxes(g, tpl):
                resid_frames.append(i)
    else:
        resid = detect_overlay_windows(dst, ff)
        reg = _boxes_to_mask((h, w), strong)
        reg = cv2.dilate(reg, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)), 1)
        res_px = 0
        for (_, _, bx) in resid:
            for b in bx:
                one = _boxes_to_mask((h, w), [b])
                res_px += int(cv2.bitwise_and(one, reg).sum() // 255)
        if res_px > 0.05 * max(1, int(reg.sum() // 255)):
            resid_frames = [-1]
    ok = (n > 0 and hit > 0 and not resid_frames)
    return {'clean': bool(ok), 'method': 'lama-overlay' if model is not None else 'telea-overlay',
            'frames': n, 'masked_frames': hit, 'windows_hit': len(wins),
            'strong_regions': len(strong), 'residual_frames': len(resid_frames),
            'note': (u'水印已真删除：成片逐帧模板匹配零命中'
                     if ok else u'复验仍有 %d 帧匹配到水印模板，不许直接上屏' % len(resid_frames))}


def per_frame_boxes(wins, total, slack=4):
    """把窗口框落到"这一帧自己的水印脚印"上。
    绝不能用全部窗口的并集：水印会从右下漂到左上，拿并集去量某帧，会把当时
    并没有水印的那半边场景（玻璃面板亮棱）当成残字，既误报坏帧，也会在激进
    门限下把真场景补成灰疤（实测 t=4.5s 左上被补出一块可见灰矩形）。"""
    per = [[] for _ in range(total)]
    for (s, e, bx) in wins:
        for i in range(max(0, s - slack), min(total, e + slack)):
            for b in bx:
                if b not in per[i]:
                    per[i].append(b)
    return per


def ping_pong(src, dst, timeout=600):
    """正放+倒放拼成无缝循环：背景视频要挂在封面整页循环播放，硬剪回起点会在
    接缝处"跳帧"，倒放接回去则首尾同帧、循环处零跳。"""
    ff = _ff()
    cmd = [ff, '-v', 'error', '-y', '-i', src, '-filter_complex',
           '[0:v]split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0[v]',
           '-map', '[v]', '-c:v', 'libx264', '-crf', '16', '-preset', 'medium',
           '-pix_fmt', 'yuv420p', '-an', dst]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf8',
                       errors='replace', timeout=timeout)
    return dst if (os.path.exists(dst) and os.path.getsize(dst) > 64 * 1024 and r.returncode == 0) else None


def _span_motion(src, spans, samples=8):
    """按"看得出在动"给候选段打分：段内均匀抽样相邻帧的平均灰度差。
    满铺循环背景的价值就是让封面呼吸，几段都干净时选动作最大的一段；
    顺带记录平均亮度，太暗的段压不住白字对比度时人还能从数字里看出来。"""
    import cv2
    ff = _ff()
    w, h, fps, dur = _dims(ff, src)
    scores = []
    for (a, b) in spans:
        idx = [int(round(x)) for x in np.linspace(a, b - 2, samples)]
        want = set(idx) | {i + 1 for i in idx}
        got = {}
        for i, fr in enumerate(_iter_frames(ff, src, w, h)):
            if i in want:
                got[i] = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            if i > max(want):
                break
        d = [float(np.abs(got[i + 1].astype(np.float32) - got[i].astype(np.float32)).mean())
             for i in idx if i in got and i + 1 in got]
        lum = float(np.mean([got[i].mean() for i in idx if i in got])) if got else 0.0
        scores.append((round(np.mean(d), 2) if d else 0.0, round(lum, 1), a, b))
    return scores


def crop_out_corner_watermark(src, dst, band=220, timeout=600):
    """物理裁掉四角水印带：豆包/即梦水印只落在距上/下边缘 band 像素内，裁中央 16:9 再放大，
    水印必然消失且零补痕。代价是分辨率放大（如实报告倍率）；抽象光效背景放大后观感可接受。
    补不干净时的兜底——比留半行水印或补一块疤都诚实。"""
    ff = _ff()
    w, h, fps, dur = _dims(ff, src)
    ch = h - 2 * band
    cw = int(round(ch * 16.0 / 9.0))
    cw = min(cw, w)
    cx = (w - cw) // 2
    cmd = [ff, '-v', 'error', '-y', '-i', src, '-vf',
           'crop=%d:%d:%d:%d,scale=1920:1080:flags=lanczos' % (cw, ch, cx, band),
           '-c:v', 'libx264', '-crf', '16', '-preset', 'medium', '-pix_fmt', 'yuv420p',
           '-an', dst]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf8',
                       errors='replace', timeout=timeout)
    if r.returncode != 0 or not os.path.exists(dst):
        return None
    return {'upscale': round(1920.0 / cw, 2), 'crop': [cx, band, cw, ch]}


def clean_cover_video(src, dst, min_span_s=2.5, keep_tmp=False, span=None):
    """封面/过渡背景真视频的完整清洗链：时序去水印 -> 只取强命中连续段 -> ping-pong 无缝循环。

    产出可直接满铺循环的干净片段。机验 = 段内时序检测再无强命中（水印笔画没残），
    且循环时长达标；任一步不成立 clean=False，交回上层如实上报（不许拿带残影的片段上屏）。"""
    ff = _ff()
    w, h, fps, dur = _dims(ff, src)
    if not w:
        return {'clean': False, 'why': u'读不到视频规格'}
    n = int(dur * fps)
    # 裁角优先：免费档漂移水印压在亮辉光上时残差太低，逐帧补会留半行残字，而"补后再匹配"
    # 对残行匹配分又低于阈值→假绿灯（用户实测第 2/3 页仍带水印）。物理裁掉四角水印带
    # 零残留零补痕，验证不可能再假绿；代价是放大倍率，如实报告。
    wins0 = detect_overlay_windows(src, ff)
    tpl = build_wm_template(src, wins0, ff) if wins0 else None
    # "确认是水印"必须以**模板真匹配到的位置**为准：chroma+pers 双门在这条片子上仍会把
    # 画面中部的中性亮辉光认成水印（实测 (878,719)/(685,565) 两个假框把裁切带顶到封顶 320px，
    # 放大 2.46x 且几何证明自相矛盾→clean=False）。模板匹配只搜四角水印带，中部假框不可能命中。
    match_pos = []
    if tpl is not None:
        import cv2
        for i, fr in enumerate(_iter_frames(ff, src, w, h)):
            if i % 8:
                continue
            match_pos += [(x, y, ww, hh) for (_s, x, y, ww, hh) in
                          match_wm_boxes(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), tpl, thresh=0.45)]

    def _is_wm(b):
        if match_pos:
            return any(max(b['x'], x) < min(b['x'] + b['w'], x + ww)
                       and max(b['y'], y) < min(b['y'] + b['h'], y + hh)
                       for (x, y, ww, hh) in match_pos)
        return b.get('chroma', 99) <= 45 and b['pers'] >= 15

    conf = [b for (_, _, bx) in wins0 for b in bx if _is_wm(b)]
    crop_info = None
    base = src
    tmpc = dst + '.p0.mp4'
    if tpl is not None:
        # 裁切带按"确认是水印"的框自适应（中性色+强残差；场景辉光框彩度高，不算），
        # 保证每个真水印框都落在裁切矩形外；封顶 320px 防止误检框把画面裁到放大好几倍。
        top = max([b['y'] + b['h'] for b in conf if b['y'] < h / 2] or [0])
        bot = max([h - b['y'] for b in conf if b['y'] >= h / 2] or [0])
        band = int(max(220, top + 12, bot + 12))
        band = min(band, 320, h // 2 - 60)
        ci = crop_out_corner_watermark(src, tmpc, band=band)
        if ci:
            ci['src_size'] = (w, h)
            base, crop_info = tmpc, ci
    tmp1 = base
    res = {'clean': True, 'method': 'corner-crop' if crop_info else 'no-watermark',
           'frames': n, 'masked_frames': 0, 'windows_hit': len(wins0)}
    # 在（已裁干净的）片子里挑一个"看得见主体、看得出在动"的 5s 窗（G6 循环 5–10s，ping-pong 翻倍）。
    cap = int(5.0 * fps)
    cands = [(0, min(n, cap))]
    if n > cap:
        cands.append((n - cap, n))
        cands.append((max(0, (n - cap) // 2), max(0, (n - cap) // 2) + cap))
    cands = [c for c in cands if c[1] - c[0] >= int(min_span_s * fps)]
    if not cands:
        return dict(res, clean=False, why=u'干净片不足 %.1fs（循环后达不到 G6 下限 5s）' % min_span_s,
                    out=None)
    ranked = _span_motion(tmp1, cands)
    best_motion = max([r[0] for r in ranked] or [0.0])
    s0, s1 = max(ranked, key=lambda r: (r[0] >= 0.5 * best_motion, r[1]))[2:]
    span_motion = [(r[0], r[1], round(r[2] / fps, 2), round(r[3] / fps, 2)) for r in ranked]
    if span:
        s0, s1 = int(round(span[0] * fps)), int(round(span[1] * fps))
    s1 = min(s1, s0 + cap, n)
    if s1 - s0 < int(2.5 * fps):
        return dict(res, clean=False, why=u'干净连续段不足 2.5s（循环后达不到 G6 下限 5s）', out=None)
    tmp2 = dst + '.p2.mp4'
    # -ss 作输入选项 + -t 输出时长：输入 seek 后 -to 会被当成"从 0 起的输出时刻"，段长算错。
    subprocess.run([ff, '-v', 'error', '-y', '-ss', '%.4f' % (s0 / float(fps)), '-i', tmp1,
                    '-t', '%.4f' % ((s1 - s0) / float(fps)),
                    '-c:v', 'libx264', '-crf', '16', '-preset', 'medium',
                    '-pix_fmt', 'yuv420p', '-an', tmp2],
                   capture_output=True, text=True, encoding='utf8', errors='replace', timeout=900)
    if not os.path.exists(tmp2):
        return dict(res, clean=False, why=u'裁段失败', out=None)
    pp = ping_pong(tmp2, dst)
    if not pp:
        os.replace(tmp2, dst)
    w2, h2, f2, d2 = _dims(ff, dst)
    # 复验双保险：① 几何证明——源片检出的每个水印框都必须落在裁切矩形之外（物理裁除，
    #    不可能残留）；② 高阈值模板匹配成品（低阈值会把场景六边形亮边误匹配，实测假报 2 帧）。
    strong_after = []
    geom_ok = True
    if tpl is not None:
        if crop_info:
            cx, cy, cw, ch = crop_info['crop']
            for b in conf:
                    ix = max(cx, b['x']) < min(cx + cw, b['x'] + b['w'])
                    iy = max(cy, b['y']) < min(cy + ch, b['y'] + b['h'])
                    if ix and iy:
                        geom_ok = False
        import cv2
        for i, fr in enumerate(_iter_frames(ff, dst, w2, h2)):
            if match_wm_boxes(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), tpl, thresh=0.45):
                strong_after.append(i)
    method = 'corner-crop+pingpong' if crop_info else 'trim+pingpong'
    if not keep_tmp:
        for p in (tmpc, tmp1, tmp2):
            if os.path.exists(p) and p != dst and p != src:
                os.remove(p)
    ok = ((not strong_after) and geom_ok and os.path.getsize(dst) > 64 * 1024
          and d2 and d2 >= min_span_s)
    return {'clean': bool(ok), 'method': method,
            'src_frames': n, 'kept_span': [round(s0 / fps, 2), round(s1 / fps, 2)],
            'span_motion_luma': span_motion,
            'dropped_frames': n - (s1 - s0),
            'loop_seconds': round(d2 or 0, 2), 'strong_left_after': len(strong_after),
            'crop': crop_info,
            'out': dst,
            'note': (u'封面背景已备好：逐帧模板匹配零命中、循环处零跳帧'
                     + (u'；水印带已物理裁除（放大 %.2fx）' % crop_info['upscale'] if crop_info else u'')
                     if ok else
                     u'机验未过——%s，不许上屏' % (
                         u'段内仍匹配到 %d 帧水印' % len(strong_after) if strong_after else
                         u'几何证明未过：有确认水印框落在裁切矩形内（裁切带不够）' if not geom_ok else
                         u'产物太小或循环时长不足'))}


def clean_video_propainter2(src, dst, boxes, resize_ratio=None, timeout=3600):
    """整幅上下文 ProPainter 视频补帧（新克隆权重）。失败/超时返回 None 退 TELEA。
    只圈掩膜区、喂整幅画面——小窗会填黑疤。8GB 显存跑 720p 全帧已知会 thrash，
    故默认按显存自动整幅降采样（resize_ratio，保全上下文，非小窗），输出再升回原尺寸。
    """
    if not propainter2_ready():
        return None
    try:
        import cv2
        import numpy as np
        import dewatermark as DW
        ff = _ff()
        w, h, fps, dur = DW.video_dims(ff, src)
        if not w or not dur:
            return None
        if resize_ratio is None:
            # 整幅张量显存估算。实测 0.7 缩放仍钉满 7.7GB 边界 thrash（14 分钟无帧），
            # 8GB 卡要把整幅预算压到 ~1.5GB（约 0.5 缩放）才跑得动；保全上下文，非小窗。
            px = w * h
            n = max(1, int(dur * fps))
            budget = 1.5e9
            r = min(1.0, (budget / max(1.0, n * px * 28.0)) ** 0.5)
            resize_ratio = round(max(0.35, r), 2)
        tmp = os.path.join(TMP_BASE, 'pp2_%d' % os.getpid())
        shutil.rmtree(tmp, ignore_errors=True)
        fdir = os.path.join(tmp, 'frames')
        mdir = os.path.join(tmp, 'masks')
        os.makedirs(fdir)
        os.makedirs(mdir)
        p = subprocess.Popen([ff, '-v', 'error', '-i', src, '-f', 'rawvideo',
                              '-pix_fmt', 'bgr24', '-'], stdout=subprocess.PIPE)
        fb = w * h * 3
        i = 0
        while True:
            buf = p.stdout.read(fb)
            if not buf or len(buf) < fb:
                break
            f = np.frombuffer(buf, np.uint8).reshape((h, w, 3)).copy()
            cv2.imwrite(os.path.join(fdir, '%05d.png' % i), f)
            if boxes:
                m = _boxes_to_mask((h, w), boxes)
                m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)), 1)
            else:
                # 豆包/即梦免费档水印会漂移（右下→左上→…），只扫右下角会漏。逐帧扫四角并集，
                # 文字状笔画（lift/gray/min_fill）才认，场景亮边大多过不了 min_fill。
                m = np.zeros((h, w), np.uint8)
                _corners = [(int(w * 0.62), int(h * 0.80), w, h),       # 右下
                            (0, 0, int(w * 0.40), int(h * 0.20)),       # 左上
                            (int(w * 0.60), 0, w, int(h * 0.20)),       # 右上
                            (0, int(h * 0.80), int(w * 0.40), h)]       # 左下
                _kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
                for (cx0, cy0, cx1, cy1) in _corners:
                    mm = DW.frame_text_mask(f[cy0:cy1, cx0:cx1], lift_min=6, gray_min=60,
                                            min_area=60, min_fill=0.10)
                    if mm.any():
                        m[cy0:cy1, cx0:cx1] = np.maximum(
                            m[cy0:cy1, cx0:cx1], cv2.dilate(mm, _kern, 1))
            cv2.imwrite(os.path.join(mdir, '%05d.png' % i), m)
            i += 1
        p.wait()
        if i == 0:
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        outdir = os.path.join(tmp, 'out')
        cmd = [sys.executable, os.path.join(PROPAINER2, 'inference_propainter.py'),
               '-i', fdir, '-m', mdir, '-o', outdir, '--save_frames', '--fp16',
               '--subvideo_length', '60']
        if resize_ratio and resize_ratio < 1.0:
            cmd += ['--resize_ratio', str(resize_ratio)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf8',
                           errors='replace', cwd=PROPAINER2, timeout=timeout)
        fixdir = os.path.join(outdir, 'frames', 'frames')
        if not os.path.isdir(fixdir) or not os.path.exists(os.path.join(fixdir, '0000.png')):
            sys.stderr.write(u'ProPainter2 没出结果，退回经典路线：%s' %
                             ((r.stdout or '') + (r.stderr or ''))[-400:])
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        # 降采样跑整幅时输出只有缩放尺寸：掩膜区升回原尺寸贴回，掩膜外保留原帧像素，
        # 避免全片二次重采样发糊；alpha 外扩+羽化盖住 ProPainter 内部再膨胀的边界。
        dec = subprocess.Popen([ff, '-v', 'error', '-i', src, '-f', 'rawvideo',
                                '-pix_fmt', 'bgr24', '-'], stdout=subprocess.PIPE)
        enc = subprocess.Popen([ff, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                                '-s', '%dx%d' % (w, h), '-r', str(fps), '-i', '-',
                                '-i', src, '-map', '0:v', '-map', '1:a?',
                                '-c:v', 'libx264', '-crf', '16', '-preset', 'medium',
                                '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-shortest', dst],
                               stdin=subprocess.PIPE)
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
        written = 0
        for k in range(i):
            buf = dec.stdout.read(fb)
            if not buf or len(buf) < fb:
                break
            orig = np.frombuffer(buf, np.uint8).reshape((h, w, 3)).copy()
            fix = cv2.imread(os.path.join(fixdir, '%04d.png' % k))
            if fix is None:
                break
            if fix.shape[:2] != (h, w):
                fix = cv2.resize(fix, (w, h), interpolation=cv2.INTER_CUBIC)
            mk = cv2.imread(os.path.join(mdir, '%05d.png' % k), cv2.IMREAD_GRAYSCALE)
            alpha = cv2.dilate(mk, kern, 1).astype(np.float32) / 255.0
            alpha = cv2.GaussianBlur(alpha, (21, 21), 0)[:, :, None]
            enc.stdin.write((fix * alpha + orig * (1 - alpha)).astype(np.uint8).tobytes())
            written += 1
        enc.stdin.close()
        enc.wait()
        dec.kill()
        dec.wait()
        if written != i or enc.returncode != 0 or not os.path.exists(dst):
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        resid = DW.sample_video_marks(dst, 16)
        sm = DW.smear_vs_original(dst, src)
        shutil.rmtree(tmp, ignore_errors=True)
        return {'clean': (resid <= 1 and not (sm or {}).get('smear')),
                'method': 'propainter2-fullframe', 'frames': i,
                'resize_ratio': resize_ratio, 'residual_cluster_hits': resid,
                'smear': sm, 'boxes': [], 'words': [], 'out': dst}
    except Exception:
        shutil.rmtree(os.path.join(TMP_BASE, 'pp2_%d' % os.getpid()), ignore_errors=True)
        return None