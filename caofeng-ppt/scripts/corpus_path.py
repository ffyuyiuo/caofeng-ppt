# -*- coding: utf-8 -*-
"""语料目录的唯一解析处。

改名或挪盘不该让技能失效：以前 9 个脚本各自写死 `CORPUS_DIR（双击打开可单独下载某个模版）`，
目录一搬走，研习脚本和 79 项闸门自证就一起报错。现在按这个顺序找：

  1. 环境变量 CAOFENG_SRC
  2. kb/corpus_src.json 里上一次成功用过的目录
  3. 历史默认目录
  4. 自动定位：在各本地盘符与用户目录下（含一层子目录）找 .pptx 最多的那个目录，且不少于 MIN_DECKS 个

找到后写回 kb/corpus_src.json，下次不用再扫。
"""
import io
import json
import os

LEGACY = u'CORPUS_DIR（双击打开可单独下载某个模版）'
MIN_DECKS = 100          # 语料是 171 个模板；低于这个数就不是它
DEPTH = 2                # 挪到 CORPUS_DIR 就是两层，只扫一层会找不到
SKIP = {'$recycle.bin', 'system volume information', 'program files', 'program files (x86)',
        'programdata', 'windows', 'node_modules', 'software', '.workbuddy', '__pycache__'}
def _drive_roots():
    """Windows 下枚举所有可用盘符，避免假定某一个特定盘。"""
    roots = []
    for c in 'CDEFGHIJKLMNOPQRSTUVWXYZAB':
        d = c + ':/'
        if os.path.isdir(d):
            roots.append(d)
    return roots


ROOTS = _drive_roots() + [os.path.join(os.path.expanduser('~'), 'Desktop'),
                          os.path.join(os.path.expanduser('~'), 'Documents')]
HERE = os.path.dirname(os.path.abspath(__file__))
REMEMBER = os.path.join(os.path.dirname(HERE), 'kb', 'corpus_src.json')
_cached = []


def _count_pptx(d):
    try:
        return len([f for f in os.listdir(d) if f.lower().endswith('.pptx')])
    except OSError:
        return 0


def _remembered():
    try:
        return json.load(io.open(REMEMBER, encoding='utf-8')).get('src_dir') or ''
    except Exception:
        return ''


def _save(d):
    try:
        json.dump({'src_dir': d, 'decks': _count_pptx(d)},
                  io.open(REMEMBER, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    except Exception:
        pass                       # 记住而已，写不进去不影响本次


def _auto_locate():
    """在各本地盘符与用户目录里找 .pptx 最多的目录（下探 DEPTH 层，跳过系统目录）。"""
    global _cached
    if _cached:
        return _cached[0]
    best, best_n = None, 0
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        frontier = [root]
        for _ in range(DEPTH + 1):
            nxt = []
            for d in frontier:
                n = _count_pptx(d)
                if n > best_n:
                    best, best_n = d, n
                if n >= MIN_DECKS:      # 已经找到语料本体，不用再钻它的子目录
                    continue
                try:
                    for x in sorted(os.listdir(d)):
                        p = os.path.join(d, x)
                        if x.startswith('.') or x.lower() in SKIP or not os.path.isdir(p):
                            continue
                        nxt.append(p)
                except OSError:
                    pass
            frontier = nxt
            if not frontier or best_n >= MIN_DECKS:
                break
    if best and best_n >= MIN_DECKS:
        _cached = [best.replace('\\', '/')]
        _save(_cached[0])
        return _cached[0]
    _cached = [None]
    return None


def corpus_dir(strict=False):
    """返回可用的语料目录；strict=True 时找不到就退出码 2 并说清怎么办。"""
    for cand in (os.environ.get('CAOFENG_SRC'), _remembered(), LEGACY):
        if cand and os.path.isdir(cand) and _count_pptx(cand) >= MIN_DECKS:
            norm = cand.replace('\\', '/')
            if norm != _remembered():
                _save(norm)
            return norm
    found = _auto_locate()
    if found:
        return found
    if strict:
        import sys
        sys.stderr.write(
            u'STOP: 找不到语料目录（要一个装着 ≥%d 个 .pptx 的文件夹）。\n'
            u'      改过名或挪过盘的话，二选一：\n'
            u'        set CAOFENG_SRC=CORPUS_DIR   （当前命令行）\n'
            u'        或把该路径写进 kb/corpus_src.json 的 src_dir\n' % MIN_DECKS)
        sys.exit(2)
    return None


if __name__ == '__main__':
    print(corpus_dir(strict=True))
