"""Content-safety and relevance guards for auto-sourced imagery.

Why this exists: on 2026-11-05 a clean-room run put a 12th-century surgery plate and an anime
pop-up-store photo onto a business deck's revenue/signing pages, and an earlier substring blocklist
killed "software" and "March 2016" while letting "Amputation" through. Both failure modes are here.

Two decisions encoded in this file:
1. Terms are grouped by category so a deck whose *subject* is medical / disaster / defence can
   unblock just that category (--allow-sensitive medical) instead of losing the blocklist whole.
2. Every term is anchored with (?<![a-z]) / (?![a-z]) so the classic substring traps
   (softWARe, MASSachusetts, reWARD, the month March) stay allowed.

Both decisions above were set by the repository author after the two failures described
at the top; they are documented here so the categorisation and the word-boundary rule
are not mistaken for whatever a generic blocklist would have done.
"""
import re

# category -> pattern. Blocked unless the deck declares the category in `allow`.
CATEGORIES = {
    'protest': r"protest(?:s|er|ers|ing)?|demonstrat(?:ion|ions|or|ors)|rall(?:y|ies|ing)|"
               r"march(?:es|ing)?\s+(?:against|for|to|toward)\b|anti[- ]?(?:war|austerity|nuclear)|"
               r"tear ?gas|rubber ?bullet(?:s)?|smoke ?bomb(?:s)?|picket(?:s|line|lines)?|placard(?:s)?|"
               r"civil[- ]disobedience|sit[- ]?in(?:s)?|strikebreak\w*|hunger[- ]strik(?:e|es|ing)|"
               r"riot(?:s|ers?|ing)?|unrest|uprising|insurgen(?:t|ts|cy)|activis(?:t|ts|m)|"
               r"vigil(?:s)?|candlelight (?:march|vigil)|marchers?|protest[- ]?sign(?:s)?",
    'violence': r"war(?:s|fare)?|warfare|war[- ]crime(?:s)?|civil[- ]war|world[- ]war|genocide|holocaust|"
                r"ethnic[- ]cleansing|massacre(?:s)?|battle(?:field|s)?|siege|bomb(?:s|ing|ed)?|blast(?:s)?|"
                r"explosion(?:s)?|shelling|grenade(?:s)?|cluster[- ]bomb(?:s)?|gunfire|weapons?|ammunition|"
                r"artillery|missiles?|killed|wounded|casualt(?:y|ies)|fatality(?:s)?|died|deaths?|"
                r"murder(?:s|er|ed)?|homicide(?:s)?|manslaughter|crime scene|execution(?:s)?|firing[- ]squad|"
                r"torture(?:d)?|guillotine|blood(?:y)?|bleeding|gore",
    'law-enforcement': r"police(?:man|men|woman|women|s)?|gendarmer\w*|sheriff|arrest(?:ed|ment|ments)?|"
                       r"handcuffs?|zip[- ]?tie(?:s)?|counter[- ]?surveillance|prison(?:er|s|ers)?|"
                       r"inmates?|penitentiar\w*|jailhouse|jails?|custody",
    'military': r"military|soldiers?|troops?|veterans?|army|navy|airforce|air[- ]force|marines?|"
                r"drone strike|aircraft[- ]carrier|warship|tank(?:s)?\s+(?:column|convoy)|conscript",
    'medical-gore': r"amputat\w*|prosthetic[- ]?limb|autopsy|morgue|corpse(?:s)?|cadaver(?:s)?|"
                    r"dissect(?:ed|ing|ion)?|surgery|surgeon(?:s)?|surgical(?:ly)?|endoscop\w*|"
                    r"lesion(?:s)?|tumor(?:s)?|tumour(?:s)?|cancer(?:ous)?|oncolog\w*|chemotherap\w*|"
                    r"radiotherap\w*|dialysis|intubat\w*|tracheotom\w*|hemorrhag\w*|haemorrhag\w*|"
                    r"syphilis|tuberculosis|leprosy|gangrene|necros\w*|ulcer(?:s)?|goitre|goiter|"
                    r"fracture(?:s)?|suture(?:s)?|breast[- ]?cancer|mastectomy|hysterectomy|"
                    r"stillbirth|fertility treatment|funeral(?:s)?|coffin(?:s)?|casket(?:s)?|cremat\w*|"
                    r"obituar\w*|bereav\w*",
    'disaster': r"disaster(?:s)?|catastroph\w*|flood(?:s|ing)?|earthquake(?:s)?|tsunami(?:s)?|landslide(?:s)?|"
                r"typhoon|hurricane|car[- ]?crash(?:es)?|aircrash|plane[- ]crash|train[- ]?wreck|derailment|"
                r"wreck(?:age|s)?|collapsed? (?:building|bridge)|building collapse|structural failure|"
                r"plague|epidemic|pandemic|famine|starvation|drought|wildfire|volcanic eruption|house[- ]?fire|"
                r"smoke inhalation|burn(?:s|ward|unit)?(?![a-z])",
    'displacement': r"refugees?|asylum[- ]seekers?|displaced (?:persons|people)|internally displaced|"
                    r"homeless(?:ness)?|beggar(?:s)?|sweatshop|child labour|child labor",
    'sexual': r"nudity|nude(?:s)?|naked|porn\w*|erotic(?:a)?|softcore|hardcore|adult (?:video|film|content)|"
              r"xxx|lingerie|strip[- ]?club(?:s)?|prostitut\w*|sex[- ]?(?:work|worker|doll|toy)s?|"
              r"abortion|sexu(?:al|ally) transmitted",
}

# Safe but off-subject: these never belong on a corporate ops deck.
OFF_DOMAIN = re.compile(
    r"(?<![a-z])(anime|manga|cosplay|figurines?|pop[- ]?up store|idol|vtuber|"
    r"concert|band|singer|album|festival|airshow|fireworks|"
    r"wedding|bride|groom|birthday party|graduation ceremony|baptism|"
    r"video ?games?|game console|esports|board ?games?|lego|"
    r"recipe|cocktails?|buffet|cake decorating|"
    r"zoo|aquarium|wildlife safari|pet (?:shop|cafe)|"
    r"shipwreck|graveyard|cemetery|mausoleum)(?![a-z])", re.I)

# 档案照/信息图/全大写标题：这类文件在 Commons 上是历史调查照或统计图，
# 标题里常见 ALL CAPS、"United States"、"Historic American"、年代、县名，
# 与"移动医疗车"这类当代实景主题不是一回事（实测 'data center' 命中的是 1970 年代
# 氧气厂开关柜黑白照，'clinic reception' 命中的是 1940 年代煤矿公司前台）。
ARCHIVAL = re.compile(
    r"(historic american|united states|usgs|geologic|bureau of|"
    r"company,[ ]+[A-Z][a-z]+|,[ ]+[A-Z][a-z]+ county|"
    # 不写 diagram/infographic：软件架构图、组织流程图本来就是合规的技术插图
    # （safety 用例里 "Software architecture diagram" 必须放行），
    # 统计信息图靠上面的 United States / USGS / geologic 这些来源特征拦
    r"(?:18|19)[0-9]{2}s|"
    # 2026-11-07 实测漏网：'File:Minidoka Relocation Center. Miss May Kumasaka. Laboratory
    # technician. Hospital Series. - NARA - 536550.jpg' 与 'Camp Devens, Massachusetts - NARA'
    # 两条都是美国国家档案馆的二战老照片，标题没全大写、年代写法也不是 "1940s"，
    # 于是相关度 100%、许可 Public domain 全都合法地进了成品——合法不等于能上屏。
    r"nara|national archives|relocation center|internment camp|"
    r"world war|(?<![a-z])ww(?:i{1,2}|[12])(?![a-z])|"
    # 2026-11-08 实测漏网：Commons 的医学词反复命中历史画作/漫画（'medical van interior'
    # 回 17 世纪油画、'nurse patient consultation' 回 Wellcome 讽刺漫画 V0011035）。
    # 许可 PD、词面相关度满分，但画不是实景——按来源与画种特征拦。
    r"wellcome|caricature|oil painting|engraving|etching|lithograph|"
    r"watercolour|watercolor|fresco|oil on canvas|"
    r",[ ]*(?:18|19)[0-9]{2}(?![0-9]))", re.I)


def is_grayscale(path, max_chroma=None, max_sat_share=None):
    """黑白档案照 = 平均彩度低 **且** 画面里几乎找不到饱和像素。

    只看平均彩度会误杀现代片：实测夜景车流图 mean=11.8、牙科前台 mean=6.2，都低于 12，
    但车灯/霓虹/木色那些饱和像素还在（彩度>30 的占比 2.1%–5.3%）；
    真档案照（Minidoka 关押营、消色中庭、Bar Beauty 前台）这一占比是 0.00%–0.019%，
    中间有 40 倍的空档，所以取 0.15% 当刀口。
    旧判据（只看 mean<12）在 104 张里误杀了 15 张现代图，占它报警数的 68%。"""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    try:
        im = Image.open(str(path)).convert('RGB')
    except Exception:
        return False
    im.thumbnail((240, 135))
    px = list(im.getdata())
    if not px:
        return False
    chroma = [max(p) - min(p) for p in px]
    mean_c = sum(chroma) / float(len(chroma))
    share = sum(1 for c in chroma if c > 30) / float(len(chroma))
    lim = 12.0 if max_chroma is None else float(max_chroma)
    share_lim = 0.0015 if max_sat_share is None else float(max_sat_share)
    return mean_c < lim and share < share_lim


def is_archival(title):
    """全大写标题或档案/信息图特征——这类候选不能当实景。"""
    t = title or ''
    if ARCHIVAL.search(t):
        return True
    letters = [c for c in t if c.isalpha()]
    return len(letters) >= 24 and sum(1 for c in letters if c.isupper()) / len(letters) >= 0.6


BOUND = r"(?<![a-z])(?:%s)(?![a-z])"
COMPILED = {k: re.compile(BOUND % p, re.I) for k, p in CATEGORIES.items()}
OFF_RE = OFF_DOMAIN
CJK = re.compile(u'[\\u4e00-\\u9fa5]')


def sensitive_hit(title, allow=None):
    """Return (category, matched_text) for the first sensitive hit outside `allow`, else (None, None)."""
    t = title or ''
    allow = set(a.strip().lower() for a in (allow or []) if a.strip())
    for cat, rx in COMPILED.items():
        if cat in allow:
            continue
        m = rx.search(t)
        if m:
            return cat, m.group(0).strip()
    return None, None


def is_sensitive(title, allow=None):
    return sensitive_hit(title, allow)[0] is not None


def is_off_domain(title):
    return bool(OFF_RE.search(title or ''))


def reject_reason(title, allow=None):
    cat, hit = sensitive_hit(title, allow)
    if cat:
        return '敏感题材(%s:%s)' % (cat, hit)
    if is_off_domain(title):
        return '领域不符'
    if is_archival(title):
        return '档案照或信息图(全大写标题/年代/调查档案特征)，不是当代实景'
    return None


def looks_like_cjk(text):
    return bool(CJK.search(text or ''))
