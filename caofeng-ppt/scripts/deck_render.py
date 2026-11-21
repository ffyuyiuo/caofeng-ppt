"""Render every slide of the 172 decks to PNG through WPS (KWPP.Application).

PowerPoint COM cannot open WPS-authored pptx (0x80070570), so KWPP is the only working channel here.
Export paths must be absolute. Never call SaveAs (it raises a modal dialog and hangs).

Resilience: these decks are 0.6-1.2 GB each and WPS's automation server does die mid-run
(RPC 0x800401FD / -2147023174 "RPC 服务器不可用"). One crash used to poison every remaining
deck. Now the app is relaunched and the deck retried, and the app is recycled proactively.
"""
import os, sys, json, io, time, glob
import pythoncom
import win32com.client


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
WORK = require_work(os.environ.get("CAOFENG_WORK", "CAOFENG_WORK"))
OUT = os.path.join(WORK, "render")
DONE = os.path.join(WORK, "render_done.jsonl")
FAIL = os.path.join(WORK, "render_fail.jsonl")
W_PX = int(os.environ.get('RW', '1600'))
RECYCLE_EVERY = int(os.environ.get('RECYCLE', '8'))
RPC_DEAD = ('-2147023174', '0x800706ba', 'RPC', '服务器不可用', 'not registered', '-2147221164',
             'com_error(-2147023174', '0x800401fd', '调用被取消', '对象与客户端断开')


def already():
    s = set()
    for f in (DONE,):
        if os.path.exists(f):
            for ln in io.open(f, encoding='utf-8'):
                try:
                    s.add(json.loads(ln)['deck'])
                except Exception:
                    pass
    return s


def new_app():
    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("KWPP.Application")
    for attr, val in (('Visible', False), ('DisplayAlerts', 0)):
        try:
            setattr(app, attr, val)
        except Exception:
            pass
    return app


def close_app(app):
    try:
        app.Quit()
    except Exception:
        pass
    time.sleep(2.0)


def is_rpc_dead(e):
    t = str(e)
    return any(k in t for k in RPC_DEAD)


def render_one(app, f):
    p = os.path.join(SRC, f)
    od = os.path.join(OUT, f[:-5].replace('#', '_'))
    os.makedirs(od, exist_ok=True)
    pr = app.Presentations.Open(p, ReadOnly=True, WithWindow=False)
    try:
        n = pr.Slides.Count
        for k in range(1, n + 1):
            dest = os.path.abspath(os.path.join(od, 'p%03d.png' % k))
            if not os.path.exists(dest):
                pr.Slides(k).Export(dest, "PNG", W_PX, int(W_PX * 9 / 16))
    finally:
        try:
            pr.Close()
        except Exception:
            pass
    return n


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ''
    files = sorted(f for f in os.listdir(SRC) if f.lower().endswith('.pptx'))
    if only:
        files = [f for f in files if only in f]
    skip = already()
    todo = [f for f in files if f not in skip]
    print("todo=%d (skip %d already done)" % (len(todo), len(files) - len(skip)), flush=True)
    app = new_app()
    ok = fail = since = 0
    for i, f in enumerate(todo, 1):
        t0 = time.time()
        done_this = False
        for attempt in (1, 2):
            try:
                n = render_one(app, f)
                io.open(DONE, 'a', encoding='utf-8').write(json.dumps(
                    {'deck': f, 'slides': n, 'secs': round(time.time() - t0, 1),
                     'dir': os.path.join(OUT, f[:-5])}, ensure_ascii=False) + "\n")
                ok += 1
                done_this = True
                print("[%d/%d] ok %s slides=%d %.0fs" % (i, len(todo), f[:40], n, time.time() - t0), flush=True)
                break
            except Exception as e:
                if is_rpc_dead(e) and attempt == 1:
                    print("   WPS 实例失联，重启后重试该文件：%s" % str(e)[:70], flush=True)
                    close_app(app)
                    app = new_app()
                    continue
                io.open(FAIL, 'a', encoding='utf-8').write(json.dumps(
                    {'deck': f, 'error': str(e)[:200]}, ensure_ascii=False) + "\n")
                fail += 1
                print("[%d/%d] FAIL %s %s" % (i, len(todo), f[:36], str(e)[:90]), flush=True)
                break
        since += 1
        if since >= RECYCLE_EVERY and done_this:
            since = 0
            close_app(app)
            app = new_app()
    close_app(app)
    print("render pass done ok=%d fail=%d" % (ok, fail), flush=True)


if __name__ == '__main__':
    main()
