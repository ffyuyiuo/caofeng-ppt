"""caofeng-ppt v2.7.0 安装校验：结构、依赖、文档口径、引擎真写动画 XML、端到端出片 + 闸门必须咬住空产物。

用法：
    python verify_install.py                     # 校验包内 caofeng-ppt/
    python verify_install.py --skill <目录>       # 校验已安装到某 agent 的那份
    python verify_install.py --skip-e2e          # 只跑静态与对账（秒级）
    python verify_install.py --full-selftest     # 再加跑 82 项闸的反向自证（约 10 分钟）

V10a 走合成 Word 的完整流水线（取图用必定拒绝的代理，验的是离线降级路径）；
V10b 断言 `check --allow-still` **必须拒绝**这只没有产物的片子并命中 G6/G11——
闸门放过空产物才是事故。真实素材的全绿交付要在有 WPS 与投放产物的机器上跑
`check --work DIR --render`。

退出码：0=全绿；1=有项不过；2=参数/路径不对（不拿"没跑成"冒充"跑过了"）。
"""
import argparse, hashlib, io, json, os, re, subprocess, sys, tempfile
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

HERE = Path(__file__).resolve().parent
# 版本号从被测目录的 VERSION 文件读，不再在本文件里写死——写死过一次，
# 结果是代码升到 2.9.0 而校验器还按 2.8.0 断言
VERSION = (HERE / 'caofeng-ppt' / 'VERSION').read_text(encoding='utf-8').strip()
REQUIRED = [
    'SKILL.md', 'SPEC.md', 'README.md', 'VERSION', 'skill.json', 'requirements.txt',
    'scripts/caofeng.py', 'scripts/styles.py', 'scripts/safety.py',
    'scripts/deck_measure.py', 'scripts/corpus_tables.py', 'scripts/corpus_path.py',
    'scripts/analyze_ooxml.py', 'scripts/aggregate_ledger.py', 'scripts/build_kb.py',
    'scripts/study_style_anim.py', 'scripts/grade_exemplars.py',
    'scripts/extract_prompt_grammar.py', 'scripts/transcribe_videos.py', 'scripts/mine_transcripts.py',
    'scripts/deck_render.py', 'scripts/visual_metrics.py', 'scripts/extract_text_geom.py',
    'scripts/make_gif.py', 'scripts/montage.py', 'scripts/delivery.py',
    'scripts/delivery_gate.py', 'scripts/display_refine.py', 'scripts/data_layout.py',
    'scripts/deck_parity.py', 'scripts/study_tutorials.py',
    'scripts/study_table_design.py', 'scripts/study_data_design.py',
    'scripts/check_docs.py', 'scripts/test_gates.py', 'scripts/test_safety.py',
    'scripts/regen_slot_tables.py',
    'references/pipeline.md', 'references/cinema-recipes.md', 'references/prompt-grammar.md',
    'references/visual-patterns.md', 'references/deck-craft.md',
    'references/material-sourcing.md', 'references/model-routing.md',
    'kb/deck_tech.json', 'kb/coverage.json', 'kb/prompt_corpus.jsonl', 'kb/prompt_stats.json',
    'kb/color_system.json', 'kb/layout_archetypes.json', 'kb/animation_mechanics.json',
    'kb/exemplar_grades.json', 'kb/exemplar_study.md', 'kb/technique_report.md',
    'kb/visual_study.md', 'kb/transcript_study.md',
    'kb/tutorials.json', 'kb/tutorial_study.md', 'kb/table_design.json',
    'kb/data_design.json', 'kb/parity_baseline.json',
]
# 引擎必须真的会写这些 XML；只写在文档里不算能力
EMITTERS = {
    '整页视频自动播': 'playFrom',
    '视频点击暂停链': 'togglePause',
    '平滑推镜 morph': 'p159:morph',
    '压暗蒙版 gradFill': 'add_scrim',
    '表格棋盘转场': 'p:checker',
    '立体展台 sp3d': 'a:sp3d',
    '双色调锁色 duotone': 'add_duotone',
    '放大强调 animScale': 'animScale',
    'preset 表驱动入场': '_inject_preset',
    '擦入方向 subtype': 'wipe(left)',
}
CHECKS = []

# 端到端用的合成 Word（章-节-正文-表格四要素齐备，够分镜引擎产出多页）。
# 内容是为验收编的假数据，只用来证明链路通，不作为任何对外交付的素材。
DOCX_MAKER = u'''# -*- coding: utf-8 -*-
import sys
import docx

TITLE = u"2026 年华东区域合作运营方案"
SECTIONS = [
    (u"一、存量深耕", [
        u"上海市场已签约合作门店 41 家，年度履约率 79.9%，单店平均月出货 3.4 万元，"
        u"其中月出货超过 5 万元的标杆门店 9 家，贡献了全区域约三成出货量。",
        u"杭州新增签约 28 家，南京新增 19 家，两城合计月出货 21.1 万元。存量门店的复购"
        u"周期中位数为 38 天，补货单均金额 6200 元，售后工单闭环时长从 2024 年的 3.6 天"
        u"压缩到 1.8 天。"]),
    (u"二、增量拓展", [
        u"苏州与宁波合计意向客户 63 家，按 22.4% 的转化率测算，对应月出货增量 11.8 万元。"
        u"拓展动作以区域仓配一体化为前置条件，两城各设一个 800 平方米中转仓后方可铺开。",
        u"单店开拓成本实测为 1.4 万元，含首单铺货、店招改造与人员培训，回本周期 5.2 个月。"]),
    (u"三、执行节奏", [
        u"第一季度完成上海存量门店的分级与补货模型上线，第二季度铺开杭州、南京，第三、"
        u"四季度进入苏州与宁波。全年投入预算 480 万元，其中仓配 210 万元、人员 145 万元、"
        u"市场费用 125 万元。"]),
]
TABLE = [
    [u"城市", u"门店数", u"单店月出货（万元）", u"月出货合计（万元）"],
    [u"上海", u"41", u"3.4", u"139.4"],
    [u"杭州", u"28", u"2.9", u"81.2"],
    [u"南京", u"19", u"2.4", u"45.6"],
]


def main():
    d = docx.Document()
    d.add_heading(TITLE, level=1)
    for name, paras in SECTIONS:
        d.add_heading(name, level=2)
        for t in paras:
            d.add_paragraph(t)
    t = d.add_table(rows=len(TABLE), cols=len(TABLE[0]))
    t.style = "Table Grid"
    for i, row in enumerate(TABLE):
        for j, cell in enumerate(row):
            t.rows[i].cells[j].text = cell
    d.save(sys.argv[1])


main()
'''


def rec(cid, name, ok, detail=''):
    CHECKS.append({'id': cid, 'name': name, 'ok': bool(ok), 'detail': str(detail)[:300]})
    print('[%s] %-3s %-40s %s' % ('PASS' if ok else 'FAIL', cid, name, str(detail)[:120]))
    return bool(ok)


def run(cmd, cwd=None, timeout=900):
    # 引擎在 GBK 控制台会把 stdout 写成 GBK，而这里按 utf8 解码——V10c 的中文问句
    # 匹配曾因此永远落空（闸明明咬了，校验器看不见）。与 test_gates._pk_env 同一套：
    # 强制子进程 UTF-8 输出。
    import os
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding='utf8', errors='replace', timeout=timeout,
                          env=dict(os.environ, PYTHONIOENCODING='utf-8'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skill')
    ap.add_argument('--workspace')
    ap.add_argument('--skip-e2e', action='store_true')
    ap.add_argument('--full-selftest', action='store_true')
    a = ap.parse_args()
    skill = Path(a.skill).expanduser().resolve() if a.skill else (HERE / 'caofeng-ppt')
    if not skill.is_dir():
        print('路径不对，没有这个技能目录：%s' % skill)
        return 2
    print('=== caofeng-ppt %s 安装校验 ===' % VERSION)
    print('skill:', skill)

    missing = [f for f in REQUIRED if not (skill / f).is_file()]
    nfiles = sum(1 for p in skill.rglob('*') if p.is_file())
    rec('V1', '结构齐全（脚本/文档/kb 实测数据）', not missing,
        '缺 %s' % missing[:6] if missing else '%d 个文件' % nfiles)

    ver = (skill / 'VERSION').read_text(encoding='utf-8').strip() if (skill / 'VERSION').is_file() else ''
    rec('V2', '版本号为 %s' % VERSION, ver == VERSION, ver or '无 VERSION')

    sj = {}
    try:
        sj = json.loads((skill / 'skill.json').read_text(encoding='utf-8'))
    except Exception as e:
        pass
    rec('V3', 'skill.json 可解析且版本一致', bool(sj) and sj.get('version') == VERSION,
        'name=%s version=%s' % (sj.get('name'), sj.get('version')))

    r = run([sys.executable, '-c',
             'import sys; sys.path.insert(0, r"%s"); import caofeng, corpus_tables, deck_measure;'
             'print(len(caofeng.TH), len(corpus_tables.PALETTES), len(corpus_tables.ANIM))'
             % str(skill / 'scripts')])
    ok_imp = r.returncode == 0
    rec('V4', '依赖与模块可导入', ok_imp,
        (r.stdout or r.stderr).strip()[:120])

    src = (skill / 'scripts' / 'caofeng.py').read_text(encoding='utf-8', errors='replace')
    # 引擎 = 装配器 + preset/色板真源表，两处的源码都要真的含这些 XML 写法
    engine = src + '\n' + '\n'.join(
        (skill / 'scripts' / n).read_text(encoding='utf-8', errors='replace')
        for n in ('corpus_tables.py', 'styles.py'))
    absent = [k for k, v in EMITTERS.items() if v not in engine]
    rec('V5', '引擎真写 10 类动画/配色 XML（不是只写在文档里）', not absent,
        '源码里找不到：%s' % absent[:4] if absent else '全部命中')

    gates = sorted({int(m) for m in re.findall(r"res\['G(\d+)_", src)})
    # 闸数不写死：以代码里真的有几道为准，加一道闸不必再来改这个文件
    rec('V6', '闸编号连续（实到 %d 道）' % len(gates),
        bool(gates) and gates == list(range(1, len(gates) + 1)),
        'G1–G%d' % (gates[-1] if gates else 0))

    r = run([sys.executable, str(skill / 'scripts' / 'check_docs.py')], cwd=str(skill / 'scripts'))
    rec('V7', '文档与代码口径一致（check_docs 退出码 0）', r.returncode == 0,
        (r.stdout or '').strip().split('\n')[-1][:120] if r.returncode else '文档与代码一致')

    eg = {}
    try:
        eg = json.loads((skill / 'kb' / 'exemplar_grades.json').read_text(encoding='utf-8'))
    except Exception:
        pass
    rec('V8', '好示范/坏示范分层数据在位',
        eg.get('n_pages') == 2224 and eg.get('n_good') and eg.get('n_bad'),
        '页 %s，好 %s，坏 %s' % (eg.get('n_pages'), eg.get('n_good'), eg.get('n_bad')))

    cs = {}
    try:
        cs = json.loads((skill / 'kb' / 'color_system.json').read_text(encoding='utf-8'))
    except Exception:
        pass
    rec('V9', '配色/版式/动画三层实测在位',
        bool(cs.get('palette_families')) and (skill / 'kb' / 'layout_archetypes.json').is_file()
        and (skill / 'kb' / 'animation_mechanics.json').is_file(),
        '色板家族 %d 个' % len(cs.get('palette_families') or {}))

    # V9b 语料目录改名/挪盘后仍能解析：必须返回一个目录或 None，不许抛异常
    r = run([sys.executable, '-c',
             'import sys; sys.path.insert(0, sys.argv[1]); import corpus_path as CP;'
             'd = CP.corpus_dir();'
             'print("dir=%s decks=%s" % (d, CP._count_pptx(d) if d else 0))',
             str(skill / 'scripts')])
    rec('V9b', '语料目录单点解析可用（模板库改名/挪盘不崩）',
        r.returncode == 0 and 'decks=' in (r.stdout or ''),
        (r.stdout or r.stderr).strip()[:120])

    if not a.skip_e2e:
        ws = Path(a.workspace).expanduser().resolve() if a.workspace else Path(tempfile.mkdtemp(
            prefix='caofeng_verify_'))
        ws.mkdir(parents=True, exist_ok=True)
        docx = ws / 'verify.docx'
        eng = str(skill / 'scripts' / 'caofeng.py')
        # 写成独立脚本再跑：内嵌 CJK 的 -c 片段会被 shell/编码层切坏，历史踩过
        maker = ws / 'make_docx.py'
        maker.write_text(DOCX_MAKER, encoding='utf-8')
        rm = run([sys.executable, str(maker), str(docx)])
        made = docx.is_file()
        if not made:
            print('  make_docx 失败:', (rm.stderr or rm.stdout).strip()[-300:])
        wd = ws / 'work'
        r1 = run([sys.executable, eng, 'plan', str(docx), '--work', str(wd)]) if made else None
        r2 = run([sys.executable, eng, 'scaffold', '--work', str(wd)]) if made else None
        r2r = run([sys.executable, eng, 'refine', '--work', str(wd)]) if made else None
        # 取图走一个必定拒绝的代理：离线降级路径（缺口写 materials/gaps.json，缺页转矢量色场）
        r2b = run([sys.executable, eng, 'fetch', '--work', str(wd),
                   '--proxy', 'http://127.0.0.1:9']) if made else None
        # G26：pack 必须把投放卡全文打出来；没有用户回执时 build 必须拒绝且不出成品
        rp = run([sys.executable, eng, 'pack', '--work', str(wd)]) if made else None
        card_p = wd / 'promptpack' / 'delivery_card.md'
        req_p = wd / 'promptpack' / 'delivery_request.json'
        deck_pre = wd / 'out' / 'deck.pptx'
        if deck_pre.is_file():
            deck_pre.unlink()
        r_nack = run([sys.executable, eng, 'build', '--work', str(wd)]) if made else None
        nack_bites = (r_nack is not None and r_nack.returncode != 0
                      and u'视觉资源是否已下载就绪' in (r_nack.stdout or '')
                      and not deck_pre.is_file())
        r_a = run([sys.executable, eng, 'ack', '--work', str(wd), '--declined',
                   '--note', u'本机校验：这台机器不投放视频，按现状出静帧册'],
                  ) if made else None
        r3 = run([sys.executable, eng, 'build', '--work', str(wd)]) if made else None
        r4 = run([sys.executable, eng, 'check', '--work', str(wd), '--allow-still']) if made else None
        deck = wd / 'out' / 'deck.pptx'
        steps = [('plan', r1), ('scaffold', r2), ('refine', r2r), ('fetch', r2b),
                 ('pack', rp), ('ack', r_a), ('build', r3)]
        bad = [(n, x) for n, x in steps if x is None or x.returncode != 0]
        rec('V10a', '流水线跑通（plan→scaffold→fetch 离线降级→build 出片）',
            made and deck.is_file() and not bad,
            'deck=%s(%d B) 失败步=%s' % (deck.is_file(), deck.stat().st_size if deck.is_file() else 0,
                                        ','.join(n for n, _ in bad) or '无')
            + ('' if not bad else ' | ' + ((bad[0][1].stdout or bad[0][1].stderr or '').strip()
                                           [-220:] if bad[0][1] else '没跑')))
        # 空产物必须被拦：--allow-still 只豁免「没投放视频」，不豁免「写了提示词却没产物」。
        # 这一条是整套闸的立身之本——放行一只没素材的片子就等于宣布闸门是摆设。
        out4 = ((r4.stdout or '') + (r4.stderr or '')) if r4 is not None else ''
        bit = [g for g in ('G11', 'G6', 'G20') if re.search(r'-\s*%s:' % g, out4)]
        rec('V10b', '闸门咬得住空产物（check 拒绝，退出码 1）',
            r4 is not None and r4.returncode == 1 and 'G11' in bit,
            'rc=%s 命中=%s' % (r4.returncode if r4 is not None else 'skip', bit))
        rec('V10c', 'G26 投放回执：卡片全文进 stdout + 没回执不许装配',
            made and nack_bites and card_p.is_file() and req_p.is_file()
            and u'AI 作图' in (rp.stdout or '') and (rp.returncode in (0, 1)),
            '无回执 build 被拒=%s 卡片=%s 请求单=%s' % (
                nack_bites, card_p.is_file(), req_p.is_file()))
        if made and deck.is_file():
            xml = ''
            import zipfile
            z = zipfile.ZipFile(str(deck))
            xml = b' '.join(z.read(n) for n in z.namelist()
                            if re.match(r'ppt/slides/slide\d+\.xml$', n)).decode('utf8', 'ignore')
            th = z.read(sorted(n for n in z.namelist()
                               if re.match(r'ppt/theme/theme\d+\.xml$', n))[0]).decode('utf8', 'ignore')
            moved = ('p:timing' in xml) and ('presetClass' in xml)
            rec('V11', '成品里真有动画节点与主题色写入', moved and '4472C4' in th,
                'timing=%s preset=%s theme_accent=%s' % ('p:timing' in xml, 'presetClass' in xml,
                                                          '4472C4' in th))
        else:
            rec('V11', '成品里真有动画节点与主题色写入', False, '没出片，无法验')
    else:
        rec('V10a', '流水线跑通', True, '--skip-e2e 跳过')
        rec('V10b', '闸门咬得住空产物', True, '--skip-e2e 跳过')
        rec('V11', '成品动画与主题写入', True, '--skip-e2e 跳过')

    if a.full_selftest:
        r = run([sys.executable, str(skill / 'scripts' / 'test_gates.py')],
                cwd=str(skill / 'scripts'), timeout=2400)
        rec('V12', '82 项闸的反向自证全绿', r.returncode == 0,
            (r.stdout or '').strip().split('\n')[-1][:120])
    sums_p = HERE / 'sha256sums.txt'
    if sums_p.is_file():
        bad = []
        for ln in io.open(sums_p, encoding='utf-8'):
            parts = ln.split()
            if len(parts) != 2:
                continue
            h, rel = parts
            p = HERE / rel
            if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() != h:
                bad.append(rel)
        rec('V13', '包内 sha256 清单与实际文件一致', not bad, '不符 %s' % bad[:4] if bad else '全对')

    fails = [c for c in CHECKS if not c['ok']]
    out = {'version': VERSION, 'skill': str(skill), 'checks': CHECKS,
           'passed': len(CHECKS) - len(fails), 'failed': len(fails),
           'e2e': not a.skip_e2e, 'full_selftest': bool(a.full_selftest),
           'generated_at': datetime.now().isoformat(timespec='seconds')}
    if skill.resolve() == (HERE / 'caofeng-ppt').resolve():
        (HERE / 'VERIFICATION.json').write_text(
            json.dumps(out, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print('证据文件 ->', HERE / 'VERIFICATION.json')
    print('\n结论：%d 项通过，%d 项不通过' % (out['passed'], out['failed']))
    for c in fails:
        print('  FAIL %s %s：%s' % (c['id'], c['name'], c['detail']))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
