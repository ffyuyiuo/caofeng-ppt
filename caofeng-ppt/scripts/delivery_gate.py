"""投放回执硬闸（SPEC §13 / G26）。

用户的原话投诉是：让 agent 用本技能做片子，全程没把文生图 / 图生视频的提示词发给他，
他拿不到素材，最后收到一版"矢量色场 + 文字"的片子，而 agent 报的是"全部通过"。
这个模块把"发卡 → 等回执 → 核对产物"落成代码，不靠 agent 自觉。

三条口径：
1. 缺产物的页 = 卡片上写了提示词、但 gen/ 里没有对应文件；
2. 只有两种情况可以继续装配：产物真的回来了（--ready 登记），或用户明确回了"不要视频背景"
   （--no-video，只豁免 video 类，不豁免 image/first/last/gif）；
3. 回执必须绑定卡片指纹：卡片一改（页/提示词/路径变了），旧回执立刻失效，必须重新发卡。
"""
import os, io, json, time, hashlib

EXT = {'image': '.png', 'first': '.png', 'last': '.png', 'video': '.mp4', 'gif': '.gif'}
KINDS = ('image', 'first', 'last', 'video', 'gif')
# 与引擎自己的 find_gen/find_gen_all 同口径：装配认的文件名，闸就必须认，
# 否则"图放对了但名字是水墨晕开.gif"这种合法回收会被闸当成没交货。
EXT_BY_KIND = {'video': ('.mp4', '.mov', '.m4v'), 'gif': ('.gif', '.apng', '.webp')}


def _cands(d, kind):
    """这一页目录里可能是该产物候选的文件，按引擎的查找顺序排。"""
    exts = EXT_BY_KIND.get(kind, ('.png', '.jpg', '.jpeg', '.webp'))
    out = []
    exact = os.path.join(d, kind + EXT[kind])
    if os.path.exists(exact):
        out.append(exact)
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            base, ext = os.path.splitext(f)
            if p in out or not os.path.isfile(p):
                continue
            if ext.lower() in exts and (base.startswith(kind) or base.rstrip('0123456789_') == kind):
                out.append(p)
        if kind == 'gif':
            for f in sorted(os.listdir(d)):
                p = os.path.join(d, f)
                if p not in out and os.path.splitext(f)[1].lower() in ('.gif', '.apng'):
                    out.append(p)
    return out


def _scan(d, kind, floor):
    """返回 (找到的文件, 只有占位文件时的说明)。"""
    too_small = None
    for p in _cands(d, kind):
        sz = os.path.getsize(p)
        if sz < floor:
            too_small = '%s 只有 %d 字节（下限 %d），当作占位文件' % (os.path.basename(p), sz, floor)
            continue
        return p, None
    return None, too_small
# 「明确不要视频」只豁免这一类；SPEC §13.5 写死，免得 agent 拿它当万能逃生门
VIDEO_ONLY_WAIVER = 'no_video'
# 用户明确回「这些产物我不做了，按现状出片」：全类豁免，但必须带用户原话摘要，并且只能在卡片真正发给用户之后登记（卡片指纹 + 时间序都要对）。
DECLINED = 'declined'
ASK = '视觉资源是否已下载就绪？就绪请回复 是'
REQ_NAME = 'delivery_request.json'
ACK_NAME = 'delivery_ack.json'


def _sha1(path):
    try:
        h = hashlib.sha1()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _load(p, dflt=None):
    if not os.path.exists(p):
        return dflt
    try:
        return json.load(io.open(p, encoding='utf-8'))
    except Exception:
        return dflt


def _dump(p, obj):
    io.open(p, 'w', encoding='utf-8').write(json.dumps(obj, ensure_ascii=False, indent=1))


def required_items(cards, gen_base, th=None):
    """卡片上每个提示词都对应一个"用户要还给我们的文件"。"""
    th = th or {}
    v_min = int(th.get('mp4_bytes_min') or 65536)
    i_min = int(th.get('gen_image_bytes_min') or 15000)
    out = []
    for c in sorted(cards, key=lambda x: x['page']):
        d = os.path.join(gen_base, 'p%03d' % c['page'])
        for kind in KINDS:
            if kind not in (c.get('files') or {}):
                continue
            floor = v_min if kind == 'video' else i_min
            got, why = _scan(d, kind, floor)
            out.append({'page': c['page'], 'kind': kind,
                        'return_path': os.path.join(d, kind + EXT[kind]),
                        'present': bool(got), 'found': got,
                        'sha1': _sha1(got) if got else None, 'note': why})
    return out


def make_request(promptpack, cards, gen_base, th=None):
    """写 delivery_request.json：谁发的卡、指纹是什么、要还哪些文件。"""
    card_path = os.path.join(promptpack, 'delivery_card.md')
    items = required_items(cards, gen_base, th)
    sha = _sha1(card_path)
    req = {'card': os.path.abspath(card_path), 'card_sha1': sha,
           'made_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'ask': ASK,
           'items': items,
           'missing': [i for i in items if not i['present']],
           'n_items': len(items), 'n_missing': sum(1 for i in items if not i['present'])}
    _dump(os.path.join(promptpack, REQ_NAME), req)
    return req


def record_ack(promptpack, mode, note=None, th=None):
    """agent 拿到用户回答后登记回执。mode='ready' 要求产物齐；'no_video' 只豁免 video 类。"""
    req = _load(os.path.join(promptpack, REQ_NAME))
    if not req:
        return None, '没有 delivery_request.json：先跑 pack 发卡，才有东西可回执'
    card_sha = _sha1(req['card'])
    if card_sha != req['card_sha1']:
        return None, ('卡片在 pack 之后被改过（旧 sha1 %s，现在 %s）：'
                      '重跑 pack 重新发卡，旧回执不算数' % (
                          (req['card_sha1'] or '')[:12], (card_sha or '')[:12]))
    items = required_items_from_req(req, th)
    missing = [i for i in items if not i['present']]
    non_video_missing = [i for i in missing if i['kind'] != 'video']
    if mode == 'ready':
        if missing:
            return None, ('用户回的是"已就绪"，但这些文件还是不在：\n%s\n'
                          '（放回卡片列出的精确绝对路径后再登记）' % '\n'.join(
                              '  页%-3d %-5s %s' % (i['page'], i['kind'], i['return_path'])
                              for i in missing[:20]))
    elif mode == DECLINED:
        if not (note or '').strip():
            return None, ('--declined 必须带 --note 用户原话摘要：'
                          '「不做了」这个决定只能是用户做的，agent 不能替他说')
    elif mode == VIDEO_ONLY_WAIVER:
        if non_video_missing:
            return None, ('"不要视频背景"只豁免 video 类，这些 %s 页还缺文生图/首帧/动图：\n%s\n'
                          '要豁免它们只能真把图做回来，或用 fetch 取真素材' % (
                              len(non_video_missing), '\n'.join(
                                  '  页%-3d %-5s' % (i['page'], i['kind'])
                                  for i in non_video_missing[:20])))
    else:
        return None, '未知回执模式 %r：只接受 ready / no_video / declined' % mode
    ack = {'shown_to_user': True, 'answer': mode, 'note': note or '',
           'card_sha1': card_sha, 'request_made_at': req['made_at'],
           'ack_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'gen_files': [{'page': i['page'], 'kind': i['kind'], 'path': i['found'],
                          'sha1': i['sha1']} for i in items if i['present']]}
    _dump(os.path.join(promptpack, ACK_NAME), ack)
    return ack, None


def required_items_from_req(req, th=None):
    """按请求单重算产物状态（不重读卡片，避免 pack 之后文件变动导致 sha1 之外的漂移）。"""
    th = th or {}
    v_min = int(th.get('mp4_bytes_min') or 65536)
    i_min = int(th.get('gen_image_bytes_min') or 15000)
    out = []
    for i in req.get('items') or []:
        kind = i['kind']
        d = os.path.dirname(i['return_path'])
        got, why = _scan(d, kind, v_min if kind == 'video' else i_min)
        out.append({'page': i['page'], 'kind': kind, 'return_path': i['return_path'],
                    'present': bool(got), 'found': got,
                    'sha1': _sha1(got) if got else None, 'note': why})
    return out


def gate(promptpack, cards, gen_base, th=None, allow_still=False):
    """build / check 共用的判定。返回 blocking=True 时不许出片。"""
    req_path = os.path.join(promptpack, REQ_NAME)
    req = _load(req_path)
    items = required_items_from_req(req, th) if req else required_items(cards, gen_base, th)
    missing = [i for i in items if not i['present']]
    ack = _load(os.path.join(promptpack, ACK_NAME))
    reasons = []
    if not items:
        return {'blocking': False, 'items': 0, 'missing': [], 'waived': [],
                'ack': ack, 'reasons': [], 'ask': ASK}
    if not req:
        reasons.append('没有 promptpack/delivery_request.json：pack 没发过投放请求单，'
                       'agent 就没资格替用户决定"这页不需要图"')
        return {'blocking': True, 'items': len(items), 'missing': missing, 'waived': [],
                'ack': None, 'reasons': reasons, 'ask': ASK}
    card_sha = _sha1(req['card'])
    # 防伪先看回执本身：产物齐不代表回执可信——时间倒序、绑旧卡片这两种伪造方式
    # 在"产物已放回"的册子上最容易顺手做出来，必须在任何分支都拦。
    if ack:
        if ack.get('card_sha1') != req['card_sha1'] or card_sha != req['card_sha1']:
            reasons.append('回执或卡片指纹对不上（回执 %s / 发卡 %s / 现在 %s）：'
                           '卡片改过就必须重新发卡重新回执' % (
                               (ack.get('card_sha1') or '无')[:12],
                               (req['card_sha1'] or '')[:12], (card_sha or '')[:12]))
        if ack.get('ack_at', '') < req.get('made_at', ''):
            reasons.append('回执时间 %s 早于发卡时间 %s：先有卡才有回执，时间倒序不成立' % (
                ack.get('ack_at'), req.get('made_at')))
        if ack.get('shown_to_user') is not True:
            reasons.append('回执没写 shown_to_user=true：卡片没真的发给用户就不许签字')
    waived = []
    if allow_still:
        waived += [i for i in missing if i['kind'] == 'video']
        missing = [i for i in missing if i['kind'] != 'video']
    if not missing and not reasons:
        return {'blocking': False, 'items': len(items), 'missing': [], 'waived': waived,
                'ack': ack, 'reasons': [], 'ask': ASK,
                'card': os.path.abspath(req['card']), 'card_sha1': card_sha,
                'made_at': req.get('made_at'), 'sha_mismatch': card_sha != req['card_sha1']}
    if missing:
        non_video = [i for i in missing if i['kind'] != 'video']
        if not ack:
            reasons.append('%d 个提示词还没有产物，且没有回执记录：先跑 pack 把卡片全文发给用户，'
                           '收到回答后用 ack 登记' % len(missing))
        elif ack.get('card_sha1') != req['card_sha1'] or card_sha != req['card_sha1']:
            reasons.append('回执或卡片指纹对不上（回执 %s / 发卡 %s / 现在 %s）：'
                           '卡片改过就必须重新发卡重新回执' % (
                               (ack.get('card_sha1') or '无')[:12],
                               (req['card_sha1'] or '')[:12], (card_sha or '')[:12]))
        else:
            if ack.get('ack_at', '') < req.get('made_at', ''):
                reasons.append('回执时间 %s 早于发卡时间 %s：先有卡才有回执，时间倒序不成立' % (
                    ack.get('ack_at'), req.get('made_at')))
            if ack.get('answer') == DECLINED:
                if not (ack.get('note') or '').strip():
                    reasons.append('回执模式 declined 但没写用户原话（--note）：'
                                   '「不做了」必须由用户原话撑着，agent 不能替用户放弃')
                else:
                    waived += missing
                    missing = []
            elif non_video:
                reasons.append('缺 %d 个非 video 产物（文生图/首帧/尾帧/动图）：'
                               '只有 declined（用户原话回「不做了」）这一种回执能豁免这一类'
                               % len(non_video))
            elif ack.get('answer') == 'ready':
                reasons.append('回执写的是"已就绪"，但 %d 个 mp4 确实不在 gen/（'
                               '回执不能代替文件）' % len(missing))
            elif ack.get('answer') == VIDEO_ONLY_WAIVER:
                waived += missing
                missing = []
            else:
                reasons.append('回执模式 %r 不认：只接受 ready / no_video' % ack.get('answer'))
    return {'blocking': bool(reasons), 'items': len(items), 'missing': missing,
            'waived': waived, 'ack': ack, 'reasons': reasons, 'ask': ASK,
            'card': os.path.abspath(req['card']), 'card_sha1': card_sha,
            'made_at': req.get('made_at'),
            'sha_mismatch': card_sha != req['card_sha1']}


def card_excerpt(card_path, items, limit=6):
    """停止时要打的摘要：用户/agent 一眼看到缺哪几页、要还什么。"""
    o = []
    for i in items[:limit]:
        o.append('  页%-3d %-5s -> %s' % (i['page'], i['kind'], i['return_path']))
    if len(items) > limit:
        o.append('  …还有 %d 项，见投放卡全文' % (len(items) - limit))
    return '\n'.join(o)
