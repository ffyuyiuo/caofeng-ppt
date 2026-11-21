# -*- coding: utf-8 -*-
"""Regenerate prompt-grammar.md's two slot-coverage tables straight from kb/prompt_stats.json.

Hand-typed coverage numbers are exactly what kept drifting (one re-baseline made 32 rows stale),
so this script rebuilds both tables from the shipped statistics file. Re-run it after any
corpus change:  python scripts/_regen_slot_tables.py
"""
import io, json, re, os

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, '..', 'kb')
DOC = os.path.join(HERE, '..', 'references', 'prompt-grammar.md')

st = json.load(io.open(os.path.join(KB, 'prompt_stats.json'), encoding='utf-8'))
byk = st['slot_coverage_by_kind']

NOTE = {
 u'镜头运动': u'必写', u'画幅': u'必写', u'首尾帧': u'**有素材时必写**', u'时长': u'必写',
 u'电影级': u'必写', u'光线': u'建议', u'视角': u'建议', u'画质锚': u'必写',
 u'因果顺序': u'**强制**', u'用途': u'建议', u'禁水印': u'**强制**',
 u'秒级分镜': u'**强制**（语料只有三成写了，但写得最像样的都写了它）',
 u'留白占位': u'**强制**（可编辑的前提）', u'禁文字': u'**强制**（语料写得最少，却是成品能不能改的分水岭）',
 u'定格': u'有尾帧时强制', u'三段纵深': u'建议', u'色彩闭集': u'建议', u'位置量化': u'**强制**（见下）',
}


def table(kind, header_note):
    d = byk[kind]
    n = d['n']
    lines = [u'槽位实测覆盖率（**%s块 n=%d**，逐条来自 `kb/prompt_stats.json` 的 '
             u'`slot_coverage_by_kind.%s.slots`；这张表由 `scripts/_regen_slot_tables.py` 从统计文件'
             u'直接生成，改语料后重跑即可，不靠手抄）：' % (header_note, n, kind),
             u'', u'| 槽位（检测器原名） | 命中 | 本技能要求 |', u'|---|---|---|']
    for slot, hit in sorted(d['slots'].items(), key=lambda x: (-x[1], x[0])):
        lines.append(u'| %s | %d/%d | %s |' % (slot, hit, n, NOTE.get(slot, u'建议')))
    return u'\n'.join(lines) + u'\n'


s = io.open(DOC, encoding='utf-8').read()

# video table: from its intro line up to the paragraph that starts with "**这一版删掉了"
i = s.index(u'槽位实测覆盖率（**视频块')
j = s.index(u'**这一版删掉了三条查不到出处的行**', i)
new_video = table('video', u'视频') + u'\n'
s = s[:i] + new_video + s[j:]

# image table: from its intro line up to the "**旧表里" note
i = s.index(u'槽位实测覆盖率（**图片块')
j = s.index(u'**旧表里"材质 13/20"这一行删掉了**', i)
new_image = table('image', u'图片') + u'\n'
s = s[:i] + new_image + s[j:]

io.open(DOC, 'w', encoding='utf-8').write(s)
print('slot tables regenerated from prompt_stats.json')
