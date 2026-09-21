#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build index.html for 李阳疯狂英语300句 interactive site."""
import csv, json, re, sys

BASE = "/Users/Macxpro/WorkBuddy/2026-09-20-04-17-56/crazy300"
data = json.load(open(f"{BASE}/data.json"))

# ---- collect words ----
word_re = re.compile(r"[A-Za-z]+(?:['’][a-z]+)?")
all_words = set()
word_order = {}  # first occurrence order
order_counter = 0
sent_words = []  # per (pi,ei): list of raw lowercase words
for pi, p in enumerate(data):
    for ei, e in enumerate(p["examples"]):
        ws = []
        for w in word_re.findall(e["en"]):
            wl = w.lower().replace("’", "'").strip("'")
            if wl:
                ws.append(wl)
                if wl not in all_words:
                    all_words.add(wl)
                    word_order[wl] = order_counter
                    order_counter += 1
        sent_words.append(((pi, ei), ws))

# ---- load ecdict ----
dictmap = {}   # word -> dict(phonetic, translation, collins, oxford, tag, frq, bnc, exchange)
with open(f"{BASE}/ecdict.csv", newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        w = (row.get("word") or "").strip().lower()
        if not w:
            continue
        dictmap[w] = row

# ---- lemma reverse map from exchange ----
lemma_of = {}
for w, row in dictmap.items():
    ex = row.get("exchange") or ""
    for part in ex.split("/"):
        if ":" in part:
            k, v = part.split(":", 1)
            if k in ("d", "p", "i", "3", "s") and v:
                for form in v.split(","):
                    form = form.strip().lower()
                    if form and form not in dictmap and form not in lemma_of:
                        lemma_of[form] = w

def lookup(w):
    if w in dictmap:
        return w, dictmap[w]
    if w in lemma_of and lemma_of[w] in dictmap:
        return lemma_of[w], dictmap[lemma_of[w]]
    # simple suffix fallbacks
    cands = []
    if w.endswith("'s"): cands.append(w[:-2])
    if w.endswith("ies"): cands.append(w[:-3] + "y")
    if w.endswith("ied"): cands.append(w[:-3] + "y")
    if w.endswith("es"): cands.append(w[:-2])
    if w.endswith("s"): cands.append(w[:-1])
    if w.endswith("ing"): cands += [w[:-3], w[:-3] + "e", w[:-4] + w[-4] if len(w) > 4 else w]
    if w.endswith("ed"): cands += [w[:-2], w[:-1], w[:-3] + w[-3] if len(w) > 3 else w]
    if w.endswith("er"): cands.append(w[:-2])
    if w.endswith("est"): cands.append(w[:-3])
    if w.endswith("ly"): cands.append(w[:-2])
    for c in cands:
        if c in dictmap:
            return c, dictmap[c]
    return None, None

def clean_trans(t, maxlen=120):
    if not t:
        return ""
    t = t.replace("\\n", "；").replace("\n", "；")
    t = re.sub(r"；+", "；", t).strip("； ")
    parts = [p.strip() for p in t.split("；") if p.strip()]
    out = []
    total = 0
    for p in parts:
        if total + len(p) > maxlen:
            break
        out.append(p)
        total += len(p)
    return "；".join(out)

DICT = {}
missed = []
for w in sorted(all_words):
    lemma, row = lookup(w)
    if row:
        DICT[w] = {
            "p": (row.get("phonetic") or "").strip(),
            "t": clean_trans(row.get("translation") or row.get("definition") or ""),
        }
        if not DICT[w]["t"]:
            DICT[w]["t"] = clean_trans(row.get("definition") or "")
    else:
        missed.append(w)

# ---- contextual sense per (sentence, word) ----
def senses_of(t):
    if not t:
        return []
    raw = re.split(r"[；;\n]|\\n", t)
    out = []
    for s in raw:
        s = s.strip()
        if s:
            out.append(s)
    return out

def han(s):
    return "".join(re.findall(r"[一-鿿]", s))

CTX = {}
for (pi, ei), ws in sent_words:
    cn = data[pi]["examples"][ei]["cn"] or ""
    cn_han = han(cn)
    if not cn_han:
        continue
    key = f"{pi}-{ei}"
    for w in set(ws):
        if w not in DICT:
            continue
        best, best_score = None, 0
        for s in senses_of(DICT[w]["t"]):
            s_clean = re.sub(r"^[a-z&\. ]+", "", s)  # strip pos prefix like "n. "
            h = han(s_clean)
            if len(h) < 2:
                continue
            hit = sum(1 for ch in set(h) if ch in cn_han)
            score = hit / len(set(h))
            if hit >= 2 and score > best_score:
                best, best_score = s_clean, score
        if best:
            CTX.setdefault(key, {})[w] = best

# ---- word root / affix segmentation (wordroot.txt) ----
import json as _json
try:
    WR = _json.loads(open(f"{BASE}/wordroot.txt", encoding="utf-8").read())
except Exception:
    WR = {}
    print("WARN: wordroot.txt parse failed")

def cls_zh(cls):
    c = (cls or "").lower()
    if "suffix" in c:
        if "noun" in c: return "名词后缀"
        if "adjective" in c: return "形容词后缀"
        if "verb" in c: return "动词后缀"
        if "adverb" in c: return "副词后缀"
        return "后缀"
    if "prefix" in c: return "前缀"
    if "combining" in c: return "组合词素"
    return "词根"

root_map = {}  # variant -> entry  (e.g. 'act' -> entry of "act, ag")
pre_map = {}
suf_map = {}
for k, v in WR.items():
    for variant in k.split(","):
        variant = re.sub(r"\d+$", "", variant.strip())
        if not variant:
            continue
        if variant.startswith("-"):
            suf_map.setdefault(variant[1:], v)
        elif variant.endswith("-"):
            pre_map.setdefault(variant[:-1], v)
        else:
            root_map.setdefault(variant, v)

suf_list = sorted(suf_map.keys(), key=len, reverse=True)
pre_list = sorted(pre_map.keys(), key=len, reverse=True)

def core_restore(core):
    """spelling-restoration candidates for a stripped core"""
    cands = [core]
    if core.endswith("i") and len(core) > 2: cands.append(core[:-1] + "y")  # busi -> busy
    if len(core) > 2 and core[-1] == core[-2]: cands.append(core[:-1])       # stopp -> stop
    cands.append(core + "e")                                                  # creat -> create? (rarely needed)
    return cands

def lookup_core(core):
    """return ('root', entry, form) or ('base', row, form) or None"""
    for c in core_restore(core):
        if c in root_map:
            return ("root", root_map[c], c)
    for c in core_restore(core):
        row = dictmap.get(c)
        if row:
            frq = int(row.get("frq") or 0)
            bnc = int(row.get("bnc") or 0)
            if (frq and frq <= 2500) or (bnc and bnc <= 2500):
                return ("base", row, c)
    return None

# manually reviewed: splits that are etymologically misleading -> keep whole
SEG_BLACKLIST = {
    "able", "agree", "alone", "come", "cook", "delay", "early", "ever",
    "improve", "letter", "moment", "money", "opinion", "order", "party",
    "read", "recent", "rest", "search", "seat", "several",
    "suit", "summer", "unless",
}

# manually verified splits the algorithm cannot derive (missing affixes in
# wordroot, assimilated spellings, or compound words). parts: text, kind, gloss, origin
MANUAL_SEG = {
    # -ing 动名词/名词（wordroot 未收录 -ing）
    "building":   [["build", "词干", "v. 建造", ""], ["ing", "名词后缀", "the action or result of", ""]],
    "meeting":    [["meet", "词干", "v. 会面", ""], ["ing", "名词后缀", "the action or result of", ""]],
    "working":    [["work", "词干", "v./n. 工作", ""], ["ing", "名词后缀", "the action or result of", ""]],
    "feeling":    [["feel", "词干", "v. 感觉", ""], ["ing", "名词后缀", "the action or result of", ""]],
    "thinking":   [["think", "词干", "v. 思考", ""], ["ing", "名词后缀", "the action or result of", ""]],
    # 其他后缀
    "relationship": [["relation", "词干", "n. 关系", ""], ["ship", "名词后缀", "state, condition or quality of", ""]],
    "otherwise":  [["other", "词干", "a. 其他的", ""], ["wise", "副词后缀", "in the manner or way of", ""]],
    "really":     [["real", "词干", "a. 真实的", ""], ["ly", "副词后缀", "in the manner of", ""]],
    "truth":      [["tru", "词干", "true a. 真实的", ""], ["th", "名词后缀", "state or quality of", ""]],
    "health":     [["heal", "词干", "v. 治愈", ""], ["th", "名词后缀", "state or quality of", ""]],
    "available":  [["avail", "词干", "v. 有用；有益", ""], ["able", "形容词后缀", "capable of being", ""]],
    "before":     [["be", "前缀", "by; around", ""], ["fore", "词根", "before, front", "Old English"]],
    "forget":     [["for", "前缀", "away, off", "Old English"], ["get", "词干", "v. 得到", ""]],
    # 合成词
    "afternoon":  [["after", "词干", "prep. 在…之后", ""], ["noon", "词干", "n. 中午", ""]],
    "anyone":     [["any", "词干", "a. 任何", ""], ["one", "词干", "pron. 人；一个", ""]],
    "everyone":   [["every", "词干", "a. 每个", ""], ["one", "词干", "pron. 人；一个", ""]],
    "everything": [["every", "词干", "a. 每个", ""], ["thing", "词干", "n. 事物", ""]],
    "anything":   [["any", "词干", "a. 任何", ""], ["thing", "词干", "n. 事物", ""]],
    "nothing":    [["no", "词干", "a. 没有", ""], ["thing", "词干", "n. 事物", ""]],
    "football":   [["foot", "词干", "n. 脚", ""], ["ball", "词干", "n. 球", ""]],
    "weekend":    [["week", "词干", "n. 周", ""], ["end", "词干", "n. 末尾", ""]],
    "himself":    [["him", "词干", "pron. 他(宾格)", ""], ["self", "词干", "n. 自己", ""]],
    "tonight":    [["to", "词干", "prep. 到", ""], ["night", "词干", "n. 夜晚", ""]],
    "tomorrow":   [["to", "词干", "prep. 到", ""], ["morrow", "词干", "n. 次日；早晨(古语)", ""]],
    "welcome":    [["wel", "词干", "well adv. 好", ""], ["come", "词干", "v. 来", ""]],
}

def segment_word(w):
    """Return list of parts [text, kind_zh, gloss, origin] or None."""
    if w in MANUAL_SEG:
        return MANUAL_SEG[w]
    if w in SEG_BLACKLIST:
        return None
    best = None
    for suf in [""] + suf_list:
        if suf:
            if len(suf) < 2 or not w.endswith(suf) or len(w) - len(suf) < 3:
                continue
            stem = w[:-len(suf)]
        else:
            stem = w
        for pre in [""] + pre_list:
            if not pre and not suf:
                continue
            if pre:
                if not stem.startswith(pre) or len(stem) - len(pre) < 2:
                    continue
                core = stem[len(pre):]
            else:
                core = stem
            hit = lookup_core(core)
            if not hit:
                continue
            kind, entry, form = hit
            # 1-letter affixes only allowed with a documented root core
            if kind != "root":
                if (pre and len(pre) < 2) or (suf and len(suf) < 2):
                    continue
            parts = []
            if pre:
                e = pre_map[pre]
                parts.append([pre, "前缀", e.get("meaning", ""), e.get("origin", "")])
            if kind == "root":
                parts.append([core, "词根", entry.get("meaning", ""), entry.get("origin", "")])
            else:
                parts.append([core, "词干", clean_trans(entry.get("translation") or "", 30), ""])
            if suf:
                e = suf_map[suf]
                parts.append([suf, cls_zh(e.get("class")), e.get("meaning", ""), e.get("origin", "")])
            # score: root core > base core; longer core; more parts
            score = (1000 if kind == "root" else 0) + len(core) * 10 + len(parts) * 3 + len(pre) + len(suf)
            if best is None or score > best[0]:
                best = (score, parts)
    if best and len(best[1]) >= 2:
        return best[1]
    return None

# Chinese glosses for every affix/root/stem used in splits (hand-written).
# key: "词性:text" -> short Chinese meaning
CN_PART = {
    # 前缀
    "前缀:a": "不；无", "前缀:an": "不；无", "前缀:ac": "向；到", "前缀:ad": "向；到",
    "前缀:al": "向；到", "前缀:at": "向；到", "前缀:be": "在…周围；使成为",
    "前缀:com": "共同；一起", "前缀:con": "共同；一起", "前缀:cor": "共同；一起",
    "前缀:dif": "不；分开", "前缀:e": "出；向外", "前缀:ef": "出；向外",
    "前缀:es": "出；向外", "前缀:ex": "出；向外", "前缀:for": "离开；脱去",
    "前缀:im": "不；无", "前缀:in": "入；向内", "前缀:inter": "在…之间",
    "前缀:mis": "错；坏", "前缀:oc": "向；对面", "前缀:of": "向；对面",
    "前缀:out": "出；超过", "前缀:per": "贯穿；彻底", "前缀:pre": "在…之前",
    "前缀:pro": "在…之前", "前缀:re": "再；回", "前缀:suc": "在下；次于",
    "前缀:sup": "在下；次于", "前缀:under": "在下；不足", "前缀:with": "向后；离开",
    # 后缀
    "副词后缀:ly": "以…方式", "副词后缀:wise": "以…方式/方向",
    "动词后缀:ibly": "可…地", "动词后缀:ize": "使成为",
    "名词后缀:al": "行为；结果", "名词后缀:ance": "状态；性质；行为",
    "名词后缀:ant": "…的人/物", "名词后缀:ation": "行为；过程；结果",
    "名词后缀:ence": "状态；性质；行为", "名词后缀:ent": "…的人/物",
    "名词后缀:er": "做…的人/物", "名词后缀:ful": "充满…的",
    "名词后缀:ing": "动作；结果", "名词后缀:ion": "行为；过程；结果",
    "名词后缀:ism": "主义；行为；状态", "名词后缀:ity": "性质；状态",
    "名词后缀:ness": "性质；状态", "名词后缀:or": "做…的人/物",
    "名词后缀:ship": "状态；关系", "名词后缀:th": "性质；状态",
    "名词后缀:ty": "性质；状态", "名词后缀:ure": "行为；过程",
    "形容词后缀:able": "可…的", "形容词后缀:ial": "与…有关的",
    "形容词后缀:ible": "可…的", "形容词后缀:il": "可…的；与…有关",
    "形容词后缀:ish": "像…的；稍…的", "形容词后缀:ive": "有…倾向的",
    "形容词后缀:ly": "像…的；每…的", "形容词后缀:ous": "充满…的",
    # 词根
    "词根:act": "做；行动", "词根:be": "生命", "词根:cept": "拿；取",
    "词根:cess": "走；让步", "词根:civ": "公民", "词根:cur": "关心；照料",
    "词根:curr": "跑", "词根:doct": "教", "词根:duc": "引导",
    "词根:duct": "引导", "词根:eco": "住所；生态", "词根:equ": "相等",
    "词根:fect": "做；制作", "词根:fer": "携带；带来", "词根:fic": "做；制作",
    "词根:fid": "信任", "词根:fin": "结束；界限", "词根:firm": "坚固；支撑",
    "词根:fore": "前面", "词根:form": "形状；形成", "词根:fort": "强",
    "词根:gener": "出生；种类", "词根:gram": "写", "词根:gress": "行走",
    "词根:ject": "投掷", "词根:loc": "地方", "词根:maj": "大",
    "词根:member": "记忆", "词根:nat": "出生", "词根:nomy": "法则；学科",
    "词根:pens": "悬挂；衡量；花费", "词根:peri": "周围", "词根:popul": "人民",
    "词根:port": "携带；搬运", "词根:poss": "能够；力量", "词根:prise": "抓取",
    "词根:put": "计算；认为", "词根:quest": "问；寻求", "词根:rect": "直；引导",
    "词根:sci": "知道", "词根:sent": "感觉", "词根:sist": "站立",
    "词根:son": "声音", "词根:spec": "看", "词根:tact": "触",
    "词根:tend": "伸展", "词根:tent": "伸展", "词根:vent": "来", "词根:void": "空",
    # 词干（覆盖/修正词典里不合适或缺失的释义）
    "词干:absolute": "绝对的", "词干:after": "在…之后", "词干:any": "任何",
    "词干:avail": "有用；有益", "词干:ball": "球", "词干:build": "建造",
    "词干:busi": "忙碌的(busy)", "词干:cause": "原因；引起", "词干:cell": "升高(源自celsus)",
    "词干:certain": "确定的", "词干:change": "变化", "词干:come": "来",
    "词干:corn": "角(源自horn)", "词干:critic": "批评家", "词干:danger": "危险",
    "词干:dress": "引导；使直", "词干:end": "末尾", "词干:every": "每个",
    "词干:feel": "感觉", "词干:final": "最后的；决赛", "词干:financ": "财政",
    "词干:foot": "脚", "词干:general": "一般的；总体", "词干:get": "得到",
    "词干:hard": "努力地；硬的", "词干:heal": "治愈", "词干:him": "他(宾格)",
    "词干:increasing": "增长的", "词干:like": "喜欢；像", "词干:manag": "处理；经营",
    "词干:may": "可以；可能", "词干:meet": "会面", "词干:morrow": "次日(古语)",
    "词干:national": "国家的", "词干:night": "夜晚", "词干:no": "没有",
    "词干:noon": "中午", "词干:one": "一个；人", "词干:other": "其他的",
    "词干:out": "外面", "词干:play": "玩；打(球)", "词干:pose": "放；置",
    "词干:press": "压；按", "词干:ready": "准备好的", "词干:real": "真实的",
    "词干:relation": "关系", "词干:safe": "安全的", "词干:self": "自己",
    "词干:side": "边；侧", "词干:stand": "站立", "词干:sudden": "突然的",
    "词干:sur": "在上；超过", "词干:take": "拿；取", "词干:thing": "事物",
    "词干:think": "思考", "词干:to": "到", "词干:tru": "真实的(true)",
    "词干:turn": "转", "词干:univers": "宇宙", "词干:use": "使用",
    "词干:week": "周", "词干:wel": "好(well)", "词干:work": "工作",
}

def apply_cn(seg):
    """replace part glosses with hand-written Chinese where available"""
    if not seg:
        return seg
    for p in seg:
        cn = CN_PART.get(f"{p[1]}:{p[0]}")
        if cn:
            p[2] = cn
    return seg

# ---- syllable splitting (pronunciation-based, like paper dictionaries) ----
import pyphen
_HY = pyphen.Pyphen(lang="en_US")
def syl_split(w):
    parts = _HY.inserted(w).split("-")
    return parts if len(parts) >= 2 else None

# ---- key vocabulary ----
STOP = set("""a an the and or but if of to in on at for with by from as is are was were be been being am do does did done have has had having i you he she it we they me him her us them my your his its our their this that these those there here what which who whom whose when where why how not no yes can could will would shall should may might must than then so such too very just about into over under again once also only own same each other more most some any all both few many much""".split())

vocab = []
for w in sorted(all_words):
    if len(w) < 4 or w in STOP or "'" in w:
        continue
    lemma, row = lookup(w)
    if not row:
        continue
    t = clean_trans(row.get("translation") or "", 90)
    if not t:
        continue
    collins = int(row.get("collins") or 0)
    oxford = int(row.get("oxford") or 0)
    tag = row.get("tag") or ""
    bnc = int(row.get("bnc") or 0)
    frq = int(row.get("frq") or 0)
    score = collins * 3 + oxford * 2 + (2 if tag else 0)
    if frq and frq < 4000: score += 2
    if bnc and bnc < 4000: score += 1
    if score < 3:
        continue
    # find first example containing the word
    ex = None
    for (pi, ei), ws in sent_words:
        if w in ws or lemma in ws:
            e = data[pi]["examples"][ei]
            ex = {"pi": pi, "ei": ei, "en": e["en"], "cn": e["cn"]}
            break
    seg = apply_cn(segment_word(w))
    # per-word part gloss overrides (e.g. im- means "into" here, not "not")
    PART_OVERRIDE = {"important": {0: "入；向内"}, "impose": {0: "入；向内"}}
    if seg and w in PART_OVERRIDE:
        for idx, gloss in PART_OVERRIDE[w].items():
            seg[idx][2] = gloss
    vocab.append({
        "w": w,
        "p": (row.get("phonetic") or "").strip(),
        "t": t,
        "s": score,
        "o": word_order.get(w, 99999),
        "ex": ex,
        "seg": seg,
        "syl": None if seg else syl_split(w),
    })

# cap vocab
vocab.sort(key=lambda v: (-v["s"], v["w"]))
vocab = vocab[:450]

print("patterns:", len(data), "examples:", sum(len(p['examples']) for p in data))
print("words:", len(all_words), "dict hits:", len(DICT), "missed:", len(missed))
print("ctx entries:", sum(len(v) for v in CTX.values()))
print("vocab:", len(vocab))
print("missed sample:", missed[:40])

# ---- inject into template ----
tpl = open(f"{BASE}/template.html", encoding="utf-8").read()
tpl = tpl.replace("__DATA__", json.dumps(data, ensure_ascii=False))
tpl = tpl.replace("__DICT__", json.dumps(DICT, ensure_ascii=False))
tpl = tpl.replace("__CTX__", json.dumps(CTX, ensure_ascii=False))
tpl = tpl.replace("__VOCAB__", json.dumps(vocab, ensure_ascii=False))
tpl = tpl.replace("__PATCOUNT__", str(len(data)))
tpl = tpl.replace("__EXCOUNT__", str(sum(len(p['examples']) for p in data)))
open(f"{BASE}/index.html", "w", encoding="utf-8").write(tpl)
print("index.html written, size:", len(tpl))
