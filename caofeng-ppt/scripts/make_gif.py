"""本机生成"可直接叠在 PPT 上"的循环动图（透明底 GIF）。

为什么要有这个脚本：即梦/豆包网页版的动图档位不一定给透明底，而**不透明底的 GIF 叠在
矢量色场或浅色画面上就是一块方框**（本项目渲染实测）。语料里 162 个 GIF 之所以能用，
是因为它们本来就压在暗底上。这个脚本产出的是"只有元素、背景透明"的动图，
放到任何背景上都不露框。

用法：
    python make_gif.py --out gen/p002/anim.gif --kind arc --frames 32 --size 800
    python make_gif.py --out gen/p006/anim.gif --kind sweep --secs 4 --hue gold
    python make_gif.py --list

判据对应：G11 要求 ≥10 帧、短边 ≥640、且（叠在色场上时）必须带透明索引。
"""
import argparse
import math
import os
import sys

sys.stdout.reconfigure(errors='backslashreplace')

# 固定调色板：索引 0 永远是"透明键色"，其余是元素颜色。
KEY = (255, 0, 255)
HUES = {
    'gold': [(246, 220, 150), (232, 196, 96), (210, 170, 80), (176, 142, 68), (120, 92, 40)],
    'ink': [(228, 226, 220), (188, 186, 180), (130, 132, 134), (78, 82, 88), (40, 44, 50)],
    'cyan': [(198, 240, 246), (120, 208, 224), (64, 164, 196), (32, 110, 148), (16, 62, 88)],
}
KINDS = ('arc', 'sweep', 'bloom', 'spin')


def frames_arc(n, size, pal):
    """金色粒子沿弧线划过（语料 NO.78/80 的"闪光划过"）。"""
    from PIL import Image, ImageDraw
    out = []
    for k in range(n):
        im = Image.new('RGBA', (size, size), KEY + (0,))
        d = ImageDraw.Draw(im)
        t = k / float(n)
        for j in range(9):
            ph = (t + j / 9.0) % 1.0
            ang = math.pi * (0.12 + 0.76 * ph)
            cx = size / 2 + math.cos(ang) * size * 0.36
            cy = size * 0.62 - math.sin(ang) * size * 0.34
            r = size * (0.012 + 0.022 * math.sin(math.pi * ph))
            col = pal[1] if j % 3 else pal[2]
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (255,))
            d.ellipse([cx - r * 2.2, cy - r * 2.2, cx + r * 2.2, cy + r * 2.2],
                      outline=col + (90,), width=max(1, int(size * 0.003)))
        out.append(im)
    return out


def frames_sweep(n, size, pal):
    """一条光带横扫（压住整页的"扫描线"手法）。"""
    from PIL import Image, ImageDraw
    out = []
    for k in range(n):
        im = Image.new('RGBA', (size, size), KEY + (0,))
        d = ImageDraw.Draw(im)
        t = k / float(n)
        x = -size * 0.3 + size * 1.6 * t
        w = int(size * 0.02)
        for i, col in enumerate(pal):
            d.line([x + i * w * 1.6, size * 0.86, x + size * 0.34 + i * w * 1.6, size * 0.14],
                   fill=col + (255 - i * 40,), width=w)
        out.append(im)
    return out


def frames_bloom(n, size, pal):
    """水墨晕开：一圈由内向外扩散的软边（NO.71 水墨开场的手法）。"""
    from PIL import Image, ImageDraw
    out = []
    for k in range(n):
        im = Image.new('RGBA', (size, size), KEY + (0,))
        d = ImageDraw.Draw(im)
        t = k / float(n)
        for ring in range(4):
            ph = (t + ring / 4.0) % 1.0
            r = size * (0.06 + 0.40 * ph)
            alpha = int(220 * (1.0 - ph))
            d.ellipse([size / 2 - r, size / 2 - r, size / 2 + r, size / 2 + r],
                      outline=pal[min(4, ring)] + (alpha,), width=max(2, int(size * 0.012)))
        out.append(im)
    return out


def frames_spin(n, size, pal):
    """折扇/卷轴旋转（NO.109/142 的动态目录）。"""
    from PIL import Image, ImageDraw
    out = []
    for k in range(n):
        im = Image.new('RGBA', (size, size), KEY + (0,))
        d = ImageDraw.Draw(im)
        base = 2 * math.pi * (k / float(n))
        cx, cy = size / 2.0, size * 0.78
        for s in range(7):
            a0 = base + s * 0.34
            a1 = a0 + 0.22
            r = size * 0.62
            d.pieslice([cx - r, cy - r, cx + r, cy + r], math.degrees(a0), math.degrees(a1),
                       fill=pal[s % 5] + (200 - s * 18,))
        out.append(im)
    return out


def save_transparent(frames, path, duration_ms, pal):
    """RGBA 帧 -> 带透明索引的 GIF（索引 0 = 键色 = 透明）。

    本机 Pillow 没有 libimagequant，RGBA 直接 quantize 会报错，所以走"固定调色板 +
    最近色匹配"这条路：颜色可控，透明位一定保住。
    """
    import numpy as np
    from PIL import Image
    flat = []
    for c in [KEY] + list(pal):
        flat.extend(c)
    arr = np.array([KEY] + list(pal), dtype=np.int32)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    out = []
    for im in frames:
        a = np.asarray(im, dtype=np.int32)
        rgb = a[:, :, :3]
        al = a[:, :, 3]
        rgb = np.where(al[..., None] > 110, rgb, arr[0][None, None, :])
        d = ((rgb[:, :, None, :] - arr[None, None, :, :]) ** 2).sum(-1)
        idx = d.argmin(-1).astype(np.uint8)
        p = Image.fromarray(idx, mode='P')
        p.putpalette(flat + [0] * (768 - len(flat)))
        out.append(p)
    out[0].save(path, save_all=True, append_images=out[1:], duration=duration_ms, loop=0,
                transparency=0, disposal=2, optimize=False)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=False)
    ap.add_argument('--kind', default='arc', choices=list(KINDS))
    ap.add_argument('--hue', default='gold', choices=list(HUES))
    ap.add_argument('--frames', type=int, default=32)
    ap.add_argument('--size', type=int, default=800)
    ap.add_argument('--secs', type=float, default=3.0)
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    if a.list:
        print('动效类型：%s' % '、'.join(KINDS))
        print('色相：%s' % '、'.join(HUES))
        return
    if not a.out:
        print('停止：必须给 --out（例：--out gen/p002/anim.gif）')
        sys.exit(2)
    pal = HUES[a.hue]
    fn = {'arc': frames_arc, 'sweep': frames_sweep, 'bloom': frames_bloom, 'spin': frames_spin}[a.kind]
    frames = fn(max(10, a.frames), a.size, pal)
    dur = int(round(1000.0 * a.secs / len(frames)))
    path = save_transparent(frames, a.out, max(20, dur), pal)
    import caofeng as C
    gi = C.gif_info(path)
    print('透明动图 -> %s：%dx%d / %s 帧 / %.1f 秒 / %d 字节，透明索引=%s' % (
        path, gi[0], gi[1], gi[2], a.secs, os.path.getsize(path), C.gif_is_transparent(path)))


if __name__ == '__main__':
    main()
