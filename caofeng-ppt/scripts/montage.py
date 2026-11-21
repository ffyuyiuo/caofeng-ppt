"""Build labelled contact sheets from rendered slides so the visual study costs little context.

Usage: python montage.py <glob of pngs> <out.jpg> [cols] [cellw]
Tiles are labelled with their file name so findings can be cited back to a deck+page.
"""
import os, sys, glob
from PIL import Image, ImageDraw, ImageFont


def label_for(path):
    d = os.path.basename(os.path.dirname(path))
    p = os.path.basename(path)
    return "%s %s" % (d[:26], p)


def build(files, out, cols=4, cellw=460, cap=34):
    if not files:
        return 0
    d = os.path.dirname(os.path.abspath(out))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    cellh = int(cellw * 9 / 16)
    rows = (len(files) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * cellw, rows * (cellh + cap)), (16, 16, 18))
    dr = ImageDraw.Draw(sheet)
    fnt = None
    for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            fnt = ImageFont.truetype(fp, 17)
            break
        except Exception:
            continue
    for i, fp in enumerate(files):
        try:
            im = Image.open(fp).convert('RGB')
        except Exception:
            continue
        im.thumbnail((cellw, cellh))
        x = (i % cols) * cellw
        y = (i // cols) * (cellh + cap)
        sheet.paste(im, (x + (cellw - im.width) // 2, y + cap))
        dr.text((x + 6, y + 8), label_for(fp), fill=(220, 220, 220), font=fnt)
    sheet.save(out, quality=80)
    return len(files)


def main():
    if len(sys.argv) < 3:
        # 非程序员会直接双击/裸跑这个脚本；裸 IndexError 看起来像脚本坏了
        print(u"用法：python montage.py \"<图片通配符>\" <输出png> [列数=4] [每张张数=12]")
        print(u"例：python montage.py \"CAOFENG_WORK/frames/*/*.jpg\" CAOFENG_WORK/montage.jpg")
        sys.exit(2)
    pat, out = sys.argv[1], sys.argv[2]

    cols = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    per = int(sys.argv[4]) if len(sys.argv) > 4 else 12
    files = sorted(glob.glob(pat))
    if not files:
        print("no files for", pat)
        return
    if per <= 0:
        print("placed", build(files, out, cols), "->", out)
        return
    base, ext = os.path.splitext(out)
    k = 0
    for i in range(0, len(files), per):
        chunk = files[i:i + per]
        o = "%s_%02d%s" % (base, k, ext)
        n = build(chunk, o, cols)
        print("sheet", o, n, "pages")
        k += 1
    print("total files", len(files), "sheets", k)


if __name__ == '__main__':
    main()
