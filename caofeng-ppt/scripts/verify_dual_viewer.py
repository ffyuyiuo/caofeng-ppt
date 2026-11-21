# -*- coding: utf-8 -*-
"""双放映器真打开验收（SPEC §17）：WPS 与 PowerPoint 各自打开成品、导出指定页，
再比两张图的平均绝对差。只读打开（ReadOnly=True），不改成品一个字。

为什么必须两个放映器：WPS 能渲染不等于 PowerPoint 能渲染——animScale 打满铺图
在 PowerPoint 放映态会缩成灰底小缩略图，而静态导出正常，单放映器验收是盲区。
中文路径会让 PowerPoint 的 Export 失败，所以先复制到纯 ASCII 临时路径再开。
"""
import argparse
import os
import shutil
import sys
import time
import zipfile
import re


def slide_video_facts(pptx, page):
    with zipfile.ZipFile(pptx) as z:
        raw = z.read('ppt/slides/slide%d.xml' % page).decode('utf8', 'replace')
        rels = z.read('ppt/slides/_rels/slide%d.xml.rels' % page).decode('utf8', 'replace')
    return {
        'has_videoFile': 'videoFile' in rels,
        'has_p14_media': 'p14:media' in raw or 'p14:media' in rels,
        'repeat_indefinite': 'repeatCount="indefinite"' in raw,
        'playFrom': raw.count('playFrom'),
        'togglePause': raw.count('togglePause'),
        'autoplay': 'auto' in raw or 'playFrom' in raw,
        'animScale_bg': bool(re.search(r'animScale', raw)),
        'media_files': [n for n in z.namelist()
                        if n.startswith('ppt/media/') and n.lower().endswith(('.mp4', '.mov', '.webm'))],
    }


def export_with(appid, ascii_pptx, pages, outdir, tag):
    import win32com.client as wc
    import pythoncom
    pythoncom.CoInitialize()
    try:
        app = wc.Dispatch(appid)
    except Exception as e:
        # 这台机器根本没装这个放映器：不是成品的缺陷，但也不能当成"验过了"
        raise NoViewer(str(e)[:120])
    made = []
    try:
        app.Visible = True
        # WithWindow=False 时 PowerPoint 的 Slide.Export 会报"无法将 ^0 保存到 ^1"
        # （实测同一份成品 WithWindow=True 一次成功）；WPS 两种都行，统一开窗口。
        pres = app.Presentations.Open(ascii_pptx, ReadOnly=True, WithWindow=True)
        try:
            for p in pages:
                dst = os.path.join(outdir, '%s_p%03d.png' % (tag, p))
                pres.Slides(p).Export(dst, 'PNG', 1920, 1080)
                made.append(dst if os.path.exists(dst) else None)
        finally:
            pres.Close()
    finally:
        try:
            app.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()
    return made


class NoViewer(Exception):
    """这台机器没有这个放映器（COM 类没注册）——与"成品打不开"是两回事。"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pptx', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--pages', default='1')
    a = ap.parse_args()
    pages = [int(x) for x in a.pages.split(',') if x.strip()]
    # PowerPoint 的 Slide.Export 不吃混合分隔符（TMP_DIR\y.png 会报"无法将 ^0 保存到 ^1"），
    # 统一成 Windows 反斜杠绝对路径。
    a.out = os.path.abspath(a.out).replace('/', '\\')
    os.makedirs(a.out, exist_ok=True)
    ascii_dir = os.path.join(os.environ.get('TEMP', 'CAOFENG_WORK'), 'caofeng_dv')
    os.makedirs(ascii_dir, exist_ok=True)
    ascii_pptx = os.path.join(ascii_dir, 'deck.pptx')
    shutil.copyfile(a.pptx, ascii_pptx)
    import numpy as np
    from PIL import Image
    results = {}
    absent = []
    rejected = []
    # 放映器列表可用环境变量覆盖（只给自证用：拿一个不存在的 COM 类名跑，必须非零退出，
    # 否则"一个都没验到"又会伪装成通过——这条脚本历史上就是无条件 return 0）。
    apps = [('KWPP.Application', 'wps'), ('PowerPoint.Application', 'ppt')]
    if os.environ.get('CAOFENG_DV_APPS'):
        apps = [(x.strip(), x.strip()) for x in os.environ['CAOFENG_DV_APPS'].split(',') if x.strip()]
    for appid, tag in apps:
        made, err, is_absent = None, None, False
        for _try in range(2):
            try:
                made = export_with(appid, ascii_pptx, pages, a.out, tag)
                err = None
                break
            except NoViewer as e:
                is_absent, err = True, str(e)
                break          # 没装就是没装，重试没意义
            except Exception as e:
                # 实测 PowerPoint 的 COM 会在上一个实例还没退干净时抢进来报 "Open.Close"，
                # 一次成功一次失败：重试一次才判"这份成品打不开"，否则就是把环境抖动算到成品头上
                err = str(e)[:160]
                time.sleep(4)
        results[tag] = made
        if made:
            print('%s: %s' % (tag, ['ok' if m else 'FAIL' for m in made]))
        elif is_absent:
            absent.append(tag)
            print('%s: 本机没装这个放映器（%s）——这一路没验，不算通过也不算成品缺陷' % (tag, err[:90]))
        else:
            rejected.append(tag)
            print('%s: 装了却打不开/导不出这份成品（重试一次仍失败）—— %s' % (tag, err))
    bad = []
    for p in pages:
        w = results.get('wps') or []
        q = results.get('ppt') or []
        iw = w[pages.index(p)] if len(w) > pages.index(p) else None
        iq = q[pages.index(p)] if len(q) > pages.index(p) else None
        if iw and iq:
            x = np.asarray(Image.open(iw).convert('RGB').resize((960, 540)), np.float32)
            y = np.asarray(Image.open(iq).convert('RGB').resize((960, 540)), np.float32)
            mad = float(np.abs(x - y).mean())
            print(u'页%d 双放映器平均绝对差 %.2f/255（<=6 算同一画面）' % (p, mad))
            if mad > 6:
                bad.append(u'页%d 两个放映器画出来的不是同一张画面（MAD %.1f>6）——'
                           u'多半是某一家不认这个动效/媒体写法' % (p, mad))
        elif iw is None and w and pages.index(p) < len(w):
            bad.append(u'页%d wps 导出失败' % p)
        elif iq is None and q and pages.index(p) < len(q):
            bad.append(u'页%d ppt 导出失败' % p)
        print(u'页%d 视频事实：%s' % (p, slide_video_facts(a.pptx, p)))
    if rejected:
        bad.append(u'放映器 %s 装着却打不开/导不出这份成品（已重试一次）——'
                   u'要么这份片子它不认，要么它的 COM 实例被占用，关掉所有 Office 窗口重跑一次再判'
                   % u'、'.join(rejected))
    # 结论必须进退出码：以前两个放映器都打不开也照样 exit 0，"验收跑过了"是假的
    if not any(results.values()):
        print(u'结论：两个放映器一个都没跑起来（absent=%s）——本次没验任何东西，不算通过'
              % (u'、'.join(absent) or u'都装了但都打不开这份成品'))
        return 1 if not absent else 2
    if bad:
        print(u'结论：%d 条不一致/导出失败 -> 不通过' % len(bad))
        for x in bad:
            print(u'  - ' + x)
        return 1
    ok_tags = [t for t in ('wps', 'ppt') if results.get(t)]
    print(u'结论：已真打开并逐页导出的放映器=%s；%s' % (
        u'、'.join(ok_tags),
        u'两路比对通过' if len(ok_tags) == 2 else
        u'只验到一路（另一路 %s），报告里不许写"双放映器都通过"' % (
            u'、'.join(absent) or u'打不开这份成品')))
    return 0


if __name__ == '__main__':
    sys.exit(main())