"""Grade the corpus into good exemplars vs bad examples before anything is learned from it.

The corpus is a commercial template dump: a third of its pages are the author's own ads
("下载链接+提取码"), some pages are white-on-white, some are 300-character walls, some put the
product number (NO.173) as the hero. Counting those and calling it "what the corpus does" would
teach the skill to reproduce exactly what the user is asking us NOT to reproduce.

So every corpus number quoted in corpus_tables.py is recomputed here over the GOOD subset, and
the rejection rules are written down as machine-checkable predicates (kb/exemplar_grades.json).
"""
import os, io, re, sys, json, collections, statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_tables as CT

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(ROOT)


def require_dir(d, what):
    if not os.path.isdir(d):
        print("STOP: %s missing -> %s" % (what, d), file=sys.stderr)
        sys.exit(2)
    return d


W = require_dir(os.environ.get("CAOFENG_WORK", "CAOFENG_WORK"), "work dir")
KB = os.path.join(SKILL, 'kb')

# 坏示范判据：每条都是一个能复核的正则/阈值，不靠"感觉不好"
# 判据来自 corpus_tables.BAD_RULES：语料分层与成品闸门（G19）共用同一份，不各写一套
AD_PATTERNS = CT.BAD_RULES['ad_patterns']
PRODUCT_NO = re.compile(CT.BAD_RULES['product_number'], re.I)
DENSE_CHARS = CT.BAD_RULES['dense_chars_min']
TINY_PT = CT.BAD_RULES['tiny_pt_max']
BRIGHT_TEXT_LUM = CT.BAD_RULES['text_zone_lum_max']


def page_bad_reasons(txt, chars, max_pt, med_lum, has_media, n_ctn):
    """一页为什么不能当范本。判据全部来自 corpus_tables.BAD_RULES，
    analyze/闸门/挖掘脚本共用这一份。"""
    why = []
    for pat, code in AD_PATTERNS:
        if re.search(pat, txt):
            why.append('ad:' + code)
            break
    if PRODUCT_NO.match((txt or '').strip()):
        why.append('product_number_hero')
    if chars == 0 and not has_media and n_ctn == 0:
        why.append('empty_page')
    if chars >= DENSE_CHARS:
        why.append('dense_text_wall')
    if chars > 0 and max_pt and max_pt < TINY_PT:
        why.append('no_type_hierarchy')
    if med_lum is not None and med_lum > BRIGHT_TEXT_LUM:
        why.append('text_on_bright_bg')
    return why


def load_json(name):
    p = os.path.join(W, name)
    if not os.path.exists(p):
        return None
    return json.load(io.open(p, encoding='utf-8'))


def main():
    ledger = load_json('ooxml_ledger.json') or die('ooxml_ledger.json（先跑 analyze_ooxml.py）')
    layout = load_json('layout_archetypes.json') or die('layout_archetypes.json（先跑 study_style_anim.py）')
    vm_path = os.path.join(W, 'visual_metrics.jsonl')
    if not os.path.exists(vm_path):
        die('visual_metrics.jsonl（先跑 visual_metrics.py）')
    vm = {}
    for ln in io.open(vm_path, encoding='utf-8'):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        vm[(r['deck'], r['page'])] = r

    pages = []
    reasons = collections.Counter()
    arch_all = collections.Counter()
    arch_good = collections.Counter()
    ad_text_samples = []
    for deck, v in ledger.items():
        if 'slides' not in v:
            continue
        for s in v['slides']:
            txt = ' '.join(s.get('text') or [])
            chars = len(txt.replace(' ', ''))
            n_ctn = (s.get('timing') or {}).get('n_ctn', 0)
            m = vm.get((deck, s['n'])) or {}
            zone_lum = m.get('text_zone_lum') or []
            med_lum = (statistics.median(zone_lum) if zone_lum else None)
            max_pt = m.get('max_pt') or (max([int(k) for k in
                                              (s.get('inv') or {}).get('pt_sizes', {})] or [0]))
            why = page_bad_reasons(txt, chars, max_pt, med_lum,
                                   bool(s.get('media')), n_ctn)
            if why and any(w.startswith('ad:') for w in why) and len(ad_text_samples) < 12:
                ad_text_samples.append({'deck': deck[:40], 'page': s['n'], 'text': txt[:70]})
            atype = None
            reasons.update(why)
            pages.append({'deck': deck, 'page': s['n'], 'chars': chars, 'max_pt': max_pt,
                          'why': why, 'zone_lum': med_lum, 'has_media': bool(s.get('media')),
                          'anim_nodes': n_ctn})
    # archetype 归属来自 pages_index（study_style_anim 用与成品闸门同一份分类器量的）
    aidx = {}
    for row in layout.get('pages_index') or []:
        aidx[(row[0], row[1])] = row[2]
    for p in pages:
        at = aidx.get((p['deck'], p['page']))
        p['archetype'] = at
        if at:
            arch_all[at] += 1
            if not p['why']:
                arch_good[at] += 1

    good = [p for p in pages if not p['why']]
    bad = [p for p in pages if p['why']]
    per_deck = collections.defaultdict(lambda: {'pages': 0, 'bad': 0})
    for p in pages:
        per_deck[p['deck']]['pages'] += 1
        per_deck[p['deck']]['bad'] += 1 if p['why'] else 0
    junk_decks = sorted([(d, v['bad'], v['pages']) for d, v in per_deck.items()
                         if v['pages'] and v['bad'] / float(v['pages']) >= 0.5],
                        key=lambda x: -x[1])
    good_deck_set = set(d for d, v in per_deck.items() if v['pages'] and
                        v['bad'] / float(v['pages']) < 0.5)

    cs = load_json('color_system.json') or {}
    fam_all, fam_good = {}, {}
    for k, v in (cs.get('palette_families') or {}).items():
        fam_all[k] = v['n_decks']
        fam_good[k] = len([d for d in v['decks'] if d in good_deck_set])

    out = {
        'n_pages': len(pages), 'n_good': len(good), 'n_bad': len(bad),
        'bad_share_pct': round(100.0 * len(bad) / max(1, len(pages)), 1),
        'reasons': dict(reasons.most_common()),
        'rules': {'ad_patterns': [p for p, _ in AD_PATTERNS], 'dense_chars_min': DENSE_CHARS,
                  'tiny_pt_max': TINY_PT, 'text_zone_lum_max': BRIGHT_TEXT_LUM,
                  'product_number_hero': PRODUCT_NO.pattern},
        'archetype_share_all': {k: {'pages': v, 'pct': round(100.0 * v / max(1, len(pages)), 1)}
                                for k, v in arch_all.most_common()},
        'archetype_share_good': {k: {'pages': v, 'pct': round(100.0 * v / max(1, len(good)), 1)}
                                 for k, v in arch_good.most_common()},
        'good_pages': {'chars_median': statistics.median([p['chars'] for p in good] or [0]),
                       'max_pt_median': statistics.median([p['max_pt'] for p in good] or [0]),
                       'anim_pages': len([p for p in good if p['anim_nodes']]),
                       'media_pages': len([p for p in good if p['has_media']])},
        'bad_pages': {'chars_median': statistics.median([p['chars'] for p in bad] or [0]),
                      'max_pt_median': statistics.median([p['max_pt'] for p in bad] or [0])},
        'junk_decks': [{'deck': d, 'bad': b, 'pages': n} for d, b, n in junk_decks[:40]],
        'n_junk_decks': len(junk_decks), 'n_good_decks': len(good_deck_set),
        'colour_family_all': fam_all, 'colour_family_good_decks': fam_good,
        'ad_text_samples': ad_text_samples,
        'page_index': [[p['deck'], p['page'], p['archetype'], ','.join(p['why'])]
                       for p in pages if p['why']],
    }
    json.dump(out, io.open(os.path.join(W, 'exemplar_grades.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    # 报告写在工作目录，由 build_kb.py 复制进 kb/：kb 里的东西只能有一个来源
    md = io.open(os.path.join(W, 'exemplar_study.md'), 'w', encoding='utf-8')
    md.write(u"# 语料示范质量分层（%d 页全量，非抽样）\n\n" % out['n_pages'])
    md.write(u"好示范 %d 页（%.1f%%）｜坏示范 %d 页（%.1f%%）｜整册过半是坏示范的模板 %d 个\n\n" % (
        out['n_good'], 100.0 * out['n_good'] / out['n_pages'], out['n_bad'],
        out['bad_share_pct'], out['n_junk_decks']))
    md.write(u"## 1 判据（每条都是可复核的正则或阈值）\n\n")
    md.write(u"- 广告推广页：%s\n" % '｜'.join(r[1] for r in AD_PATTERNS))
    md.write(u"- 商品编号当主视觉：`%s`\n" % PRODUCT_NO.pattern)
    md.write(u"- 空页（无字、无媒体、无动画）\n- 密集文字墙：单页 >=%d 字\n"
             u"- 没有视觉层级：整页最大字号 <%dpt\n- 字压亮底：文字区背景亮度中位 >%.2f\n\n" % (
                 DENSE_CHARS, TINY_PT, BRIGHT_TEXT_LUM))
    md.write(u"## 2 坏示范构成（命中次数）\n\n")
    for k, v in sorted(out['reasons'].items(), key=lambda kv: -kv[1]):
        md.write(u"- %s: %d\n" % (k, v))
    md.write(u"\n## 3 版式原型占比：原始 vs 只算好示范\n\n")
    md.write(u"| 原型 | 原始页 | 原始占比 | 好示范页 | 好示范占比 |\n|---|---|---|---|---|\n")
    for k, v in out['archetype_share_all'].items():
        g = out['archetype_share_good'].get(k, {'pages': 0, 'pct': 0})
        md.write(u"| %s | %d | %.1f%% | %d | %.1f%% |\n" % (
            k, v['pages'], v['pct'], g['pages'], g['pct']))
    md.write(u"\n## 4 色板家族：全部册 vs 好示范册\n\n")
    for k, v in sorted(fam_all.items(), key=lambda kv: -kv[1]):
        md.write(u"- %s: %d 册 -> 好示范册 %d\n" % (k, v, fam_good.get(k, 0)))
    md.write(u"\n## 5 好示范页自己的量纲\n\n")
    for k, v in out['good_pages'].items():
        md.write(u"- %s: %s\n" % (k, v))
    md.write(u"\n坏示范页对照：字数中位 %s，最大字号中位 %s\n\n" % (
        out['bad_pages']['chars_median'], out['bad_pages']['max_pt_median']))
    md.write(u"## 6 过半是坏示范的模板（做片子时不要参考这几册）\n\n")
    for r in out['junk_decks'][:25]:
        md.write(u"- %s：%d/%d 页坏示范\n" % (r['deck'], r['bad'], r['pages']))
    md.write(u"\n## 7 广告页文本样本（判据取证）\n\n")
    for r in out['ad_text_samples']:
        md.write(u"- %s p%03d：%s\n" % (r['deck'], r['page'], r['text']))
    md.close()
    print("pages=%d good=%d bad=%d reasons=%d junk_decks=%d" % (
        out['n_pages'], out['n_good'], out['n_bad'], len(out['reasons']), out['n_junk_decks']))
    print("REASONS:", json.dumps(out['reasons'], ensure_ascii=False))
    print("ARCH_GOOD:", json.dumps([[k, v['pages'], v['pct']]
                                   for k, v in out['archetype_share_good'].items()],
                                   ensure_ascii=False))
    print("FAM:", json.dumps([fam_all, fam_good], ensure_ascii=False))


def die(msg):
    print("STOP: 缺少 %s" % msg, file=sys.stderr)
    sys.exit(2)


if __name__ == '__main__':
    main()
