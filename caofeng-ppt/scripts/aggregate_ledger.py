"""Aggregate the per-deck OOXML ledger into a technique report: what the 172 decks actually do."""
import os, io, json, collections, statistics, sys


def require_work(w):
    """These study scripts write their products into the work dir. Refuse to invent one: on a
    fresh machine the default may sit on a drive that does not exist, and silently creating
    CAOFENG_WORK there is how a re-run becomes a mystery. Set CAOFENG_WORK to an existing dir.
    """
    if not os.path.isdir(w):
        print("停止：工作目录不存在 %s；先建好它，或用 CAOFENG_WORK 指一个已存在的目录" % w, file=sys.stderr)
        sys.exit(2)
    return os.path.abspath(w)

W = require_work(os.environ.get("CAOFENG_WORK", "CAOFENG_WORK"))
L = json.load(io.open(os.path.join(W, 'ooxml_ledger.json'), encoding='utf-8'))

trans_types = collections.Counter()
trans_dur = collections.Counter()
morph_opt = collections.Counter()
combos = collections.Counter()
classes = collections.Counter()
prims = collections.Counter()
paths = []
nodeTypes = collections.Counter()
delays = collections.Counter()
effects = collections.Counter()
shapes = collections.Counter()
bgkinds = collections.Counter()
fonts = collections.Counter()
ptsizes = collections.Counter()
colors = collections.Counter()
media_slides = 0
slide_count = 0
per_deck = {}
vid_on_slide = collections.Counter()
textlen = []

for deck, v in L.items():
    if 'slides' not in v:
        continue
    sig = collections.Counter()
    for s in v['slides']:
        slide_count += 1
        t = s['timing']
        classes.update(t['classes'])
        prims.update(t['prims'])
        nodeTypes.update(t['nodeTypes'])
        delays.update(t['delays'])
        for k, n in t['combos'].items():
            combos[k] += n
        paths.extend(t['paths'])
        for d in s['trans']['decls']:
            for kid in d['kids']:
                trans_types["%s/%s" % (d['ns'].split('/')[-2] if '/' in d['ns'] else d['ns'], kid['type'])] += 1
                if kid['type'] == 'morph':
                    morph_opt[kid['attrs'].get('option', '?')] += 1
            if 'dur' in d['attrs']:
                trans_dur[d['attrs']['dur']] += 1
        for k, n in s['inv']['effects'].items():
            effects[k] += n
        for k, n in s['inv']['shapes'].items():
            shapes[k] += n
            sig[k] += n
        if s['bg']:
            for b in s['bg']:
                bgkinds[b] += 1
        for f, n in s['inv']['fonts'].items():
            fonts[f] += n
        for p_, n in s['inv']['pt_sizes'].items():
            ptsizes[str(p_)] += n
        for c, n in s['inv']['top_colors'].items():
            colors[c] += n
        if s['media']:
            media_slides += 1
        for m in s['media']:
            vid_on_slide[m.split('.')[-1]] += 1
        textlen.append(len(' '.join(s['text'])))
        sig['ctn'] += t['n_ctn']
        sig['media'] += len(s['media'])
    per_deck[deck] = sig

o = io.open(os.path.join(W, 'technique_report.md'), 'w', encoding='utf-8')
w = o.write
w("# %d 个模板的技法台账（全部来自 OOXML 实测，非推测）\n\n" % len(L))
w("模板数=%d  幻灯片数=%d  含内嵌媒体的页=%d\n\n" % (len(L), slide_count, media_slides))

w("## 1 转场\n\n")
w("- 类型分布: %s\n" % trans_types.most_common(20))
w("- morph option: %s\n" % morph_opt.most_common())
w("- 显式时长(ms): %s\n" % trans_dur.most_common(15))

w("\n## 2 动画原语与节奏\n\n")
w("- presetClass: %s\n" % classes.most_common())
w("- 时间线元素: %s\n" % prims.most_common(20))
w("- nodeType: %s\n" % nodeTypes.most_common())
w("- 常见 delay(ms): %s\n" % delays.most_common(12))
w("- preset 组合 Top40 (class:id:subtype -> 次数):\n\n")
for k, n in combos.most_common(40):
    w("  - %s -> %d\n" % (k, n))
w("\n- 运动路径样本(前6条,截断):\n")
for p_ in paths[:6]:
    w("  - %s\n" % p_[:180])

w("\n## 3 形状与效果\n\n")
w("- 形状统计: %s\n" % shapes.most_common(30))
w("- 效果统计: %s\n" % effects.most_common(25))
w("- 背景类型: %s\n" % bgkinds.most_common())

w("\n## 4 字体与字号\n\n")
w("- 字体 Top25: %s\n" % fonts.most_common(25))
w("- 字号(pt)分布: %s\n" % sorted(ptsizes.items(), key=lambda x: -x[1])[:30])
w("- 高频色值 Top30: %s\n" % colors.most_common(30))

w("\n## 5 文本密度\n\n")
if textlen:
    w("- 每页字符数: 中位 %.0f, 均值 %.1f, 最大 %d, 空页比例 %.1f%%\n" % (
        statistics.median(textlen), statistics.mean(textlen), max(textlen),
        100.0 * sum(1 for x in textlen if x == 0) / len(textlen)))

# 机器可读的旁路：build_kb 的 giant-numeral 卡从这里取字号与文本密度，
# 不再手抄 technique_report.md 里的数字（本轮重算基线时就抄过期了）。
json.dump({'n_decks': len(L), 'n_slides': slide_count, 'media_slides': media_slides,
           'pt_sizes_top': [[int(k), v] for k, v in sorted(
               ptsizes.items(), key=lambda x: -x[1])[:12]],
           'text_median': (statistics.median(textlen) if textlen else 0),
           'text_mean': (statistics.mean(textlen) if textlen else 0),
           'empty_page_ratio': (100.0 * sum(1 for x in textlen if x == 0) / len(textlen)
                                if textlen else 0)},
          io.open(os.path.join(W, 'corpus_stats.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)

w("\n## 6 每模板指纹（动画量/媒体量）\n\n")
rows = sorted(per_deck.items(), key=lambda kv: -(kv[1].get('ctn', 0) + 3 * kv[1].get('media', 0)))
for d, sig in rows[:40]:
    w("- %s: ctn=%d media=%d custGeom=%d grpSp=%d pic=%d sp=%d\n" % (
        d[:44], sig.get('ctn', 0), sig.get('media', 0), sig.get('custGeom', 0),
        sig.get('grpSp', 0), sig.get('pic', 0), sig.get('sp', 0)))
o.close()
json.dump({'combos': combos.most_common(200), 'trans': dict(trans_types),
           'prims': dict(prims), 'effects': dict(effects)},
          io.open(os.path.join(W, 'technique_agg.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print("report written")
print("decks:", len(L), "slides:", slide_count)
print("top classes:", classes.most_common(6))
print("trans types:", trans_types.most_common(8))
