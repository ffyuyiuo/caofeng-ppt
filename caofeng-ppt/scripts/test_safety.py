# -*- coding: utf-8 -*-
"""Classify a fixed list of Wikimedia-style file titles through safety.reject_reason.

Both directions matter: missing a protest/gore title is a delivery-blocking embarrassment,
and killing "software" / the month "March" / "Massachusetts" throws away good material.
Run:  python test_safety.py   (writes test_safety.md, exits 1 on any mismatch)
"""
import io
import os
import sys

# A GBK console cannot encode every character these scripts print; a cosmetic character must
# never abort a run or decide an exit code.
try:
    sys.stdout.reconfigure(encoding=sys.stdout.encoding or 'utf-8', errors='backslashreplace')
    sys.stderr.reconfigure(encoding=sys.stderr.encoding or 'utf-8', errors='backslashreplace')
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import safety as S

# (title, expect_block, why)
CASES = [
    # must BLOCK - protest vocabulary the old list missed
    (u"File:01.05.2023 Anti-war rally 01.jpg", True, "anti-war rally"),
    (u"File:Public demonstration in square.png", True, "demonstration"),
    (u"File:Tear gas deployed at picket line.jpg", True, "tear gas / picket"),
    (u"File:March against war in capital.jpg", True, "march against"),
    (u"File:Protesters carrying placards.webm", True, "placard"),
    (u"File:Activist chained to fence.jpg", True, "activist"),
    (u"File:Candlelight vigil at dusk.png", True, "vigil"),
    (u"File:Riot police line outside parliament.jpg", True, "riot/police"),
    # must BLOCK - gore / medical / death (the 2026-11-05 surgery-plate incident)
    (u"File:Amputation of a leg, 19th c..jpg", True, "amputation"),
    (u"File:12th century surgery plate.jpg", True, "surgery"),
    (u"File:Autopsy table in morgue.jpg", True, "autopsy/morgue"),
    (u"File:Breast cancer awareness ribbon.png", True, "cancer"),
    (u"File:Earthquake collapsed building.jpg", True, "earthquake collapse"),
    (u"File:Refugee camp rows of tents.jpg", True, "refugee"),
    (u"File:Nude study, academic.jpg", True, "nudity"),
    (u"File:Car crash wreckage on highway.jpg", True, "car crash"),
    # must BLOCK - 美国国家档案馆的二战老照片：许可合法、相关度能到 100%，但根本不是当代实景
    # （2026-11-07 v5 实测漏网两条进了成品：页 5 / 页 38 的 Minidoka 关押营化验员黑白照）
    (u"File:Minidoka Relocation Center. Miss May Kumasaka. Laboratory technician. "
     u"Hospital Series. Laboratory. - NARA - 536550.jpg", True, "NARA 关押营档案照"),
    (u"File:Medical Department - Sanitary Service - Screen porch for case at "
     u"Camp Devens, Massachusetts - NARA - 45499049.jpg", True, "NARA 军营档案照"),
    (u"File:Shipyard workers, 1943.jpg", True, "逗号年份档案照"),
    (u"File:WWII victory garden poster.jpg", True, "二战海报"),
    # must PASS - substring traps and ordinary corporate subject matter
    (u"File:Software architecture diagram.png", False, "software contains war"),
    (u"File:March 2016 city council vote.jpg", False, "March is a month"),
    (u"File:Massachusetts state house.jpg", False, "Massachusetts contains assault-ish"),
    (u"File:Employee reward ceremony 2024.jpg", False, "reward contains war"),
    (u"File:Warehouse logistics conveyor.jpg", False, "warehouse"),
    (u"File:Hospital ward with nurses.jpg", False, "a clean ward is not gore"),
    (u"File:Urban skyline at golden hour.jpg", False, "urban must not be blocked"),
    (u"File:Manufacturing plant assembly line.jpg", False, "manufacturing"),
    (u"File:Financial dashboard on screen.jpg", False, "dashboard"),
    (u"File:Firefighter training exercise.jpg", False, "training, not a fire disaster"),
]

# allow-list must switch a category off
ALLOW_CASES = [
    (u"File:12th century surgery plate.jpg", ['medical-gore'], False),
    (u"File:12th century surgery plate.jpg", [], True),
    (u"File:Public demonstration in square.png", ['medical-gore'], True),
]


def main():
    out = []
    bad = 0
    for title, expect_block, why in CASES:
        reason = S.reject_reason(title)
        got_block = reason is not None
        ok = (got_block == expect_block)
        if not ok:
            bad += 1
        out.append(u"%s expect=%s got=%-6s %s  [%s]%s" % (
            u'OK  ' if ok else u'FAIL',
            u'BLOCK' if expect_block else u'PASS',
            u'BLOCK' if got_block else u'PASS',
            title, why, u'' if ok else u'  <<< MISMATCH'))
    for title, allow, expect_block in ALLOW_CASES:
        reason = S.reject_reason(title, allow)
        got_block = reason is not None
        ok = (got_block == expect_block)
        if not ok:
            bad += 1
        out.append(u"%s allow=%s expect=%s got=%s %s%s" % (
            u'OK  ' if ok else u'FAIL', allow,
            u'BLOCK' if expect_block else u'PASS',
            u'BLOCK' if got_block else u'PASS', title, u'' if ok else u'  <<< MISMATCH'))
    # CJK titles must never be silently let through by an English-only list:
    cjk = u"File:抗议人群与防暴警察.jpg"
    if not S.looks_like_cjk(cjk):
        bad += 1
        out.append(u"FAIL CJK detector missed a Chinese title")
    else:
        out.append(u"OK   CJK titles are detected (fetch must reject them unless source_page is trusted)")

    out.append(u"")
    out.append(u"cases=%d mismatches=%d" % (len(CASES) + len(ALLOW_CASES) + 1, bad))
    path = os.path.join(os.environ.get('TEMP', '/tmp'), 'caofeng_safety_test.md')
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(u"# safety.py classification test\n\n" + u"\n".join(out) + u"\n")
    print("mismatches=%d report=%s" % (bad, path))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
