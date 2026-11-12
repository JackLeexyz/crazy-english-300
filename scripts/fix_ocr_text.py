#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校订 OCR 弄坏的英文句式/例句与中文译文（一次性修复 + 可复跑）。

背景
----
源文档是 20 张照片的双栏 OCR 整理版。除中文释义错行外，还有两类硬伤：
  1. 英文里大量连写/错拼（Iadmire、becaulse、afteroon、thatl、1t…），
     导致单词查不到词典释义、朗读读音也不对；
  2. 少数句子整句 OCR 崩坏（165 / 220 / 227），中英文都读不通。

本脚本按「编号 + 例句下标」精确定位并整句替换，措辞对照原书 docx 与
中文译文还原；不做自由改写，不改动本身正确的句子。可重复执行。

用法：python3 scripts/fix_ocr_text.py [data.json ...]
"""
import json
import re
import sys
from pathlib import Path

# (编号, 例句下标从 0 开始) -> 校订后的英文
EN_FIX = {
    (89, 0): "I suggest (that) you go there by yourself.",
    (99, 0): "I used to go to play basketball with some of my friends every Saturday afternoon.",
    (102, 1): "I would like to recommend a trip to Jiuzhaigou.",
    (112, 1): "I'm considering paying a visit to Beijing next month.",
    (142, 0): "Is it OK if I borrow your notebook tonight?",
    (142, 1): "Is it OK if I ask you a few questions?",
    (148, 1): "It depends on whether I do well on my exam or not.",
    (150, 0): "It doesn't matter if you have made mistakes. Learn from your mistakes and you'll make progress.",
    (153, 0): "It suddenly occurred to me that I knew how to solve that problem.",
    (156, 0): "It happened that I won the football lottery last week.",
    (156, 1): "It happened that I ran into my English teacher at the supermarket.",
    (165, 0): "It is easy enough to copy and paste, but you should write something by yourself.",
    (183, 0): "It is very useful to recite English sentences and passages.",
    (183, 1): "It is very useful to take a deep breath when you feel nervous.",
    (205, 1): "Maybe it's better to send a text message instead of calling.",
    (206, 1): "This dictionary means a great deal to me because I won it in a speech competition.",
    (213, 1): "Not until I began to work did I realize how much time I had wasted.",
    (217, 0): "On the one hand I admire your ability, but on the other hand I distrust your judgment.",
    (217, 1): "On the one hand I want to be a teacher, on the other hand I worry about making money.",
    (220, 2): "Please feel free to call me by my English name, if it is easier for you to remember.",
    (227, 2): "Sorry to bother you. I have a question to ask you.",
    (227, 3): "Sorry to bother you, but the meeting is about to begin.",
    (228, 1): "Thanks to Li Yang Crazy English, I became the top student in my class.",
    (236, 0): "There are signs that you have made a significant improvement.",
    (254, 0): "What do you say we go for a drive one of these days when it clears up?",
    (258, 0): "What's the point of arguing about it?",
    (27, 1): "This bill doesn't make sense. I was overcharged.",
    (28, 0): "Don't be afraid of losing face and making mistakes.",
    (30, 1): "Don't waste time learning a lot of useless words in isolation. The best way of learning English words is to blurt out as many authentic sentences as you can.",
    (45, 0): "How does making our appointment at 8 sound?",
    (59, 0): "I didn't realize how much this meant to you.",
    (104, 3): "I wouldn't feel happy if I felt you weren't comfortable.",
    (172, 2): "I think it is necessary to look before you leap.",
}

# 编号 -> 校订后的句式标题
PATTERN_FIX = {
    99: "I used to ...",
    149: "It doesn't make sense to ...",
    150: "It doesn't matter whether/if ...",
    228: "Thanks to ...",
    256: "What I'm getting at is ...",
}

# (编号, 例句下标) -> 校订后的中文译文
CN_FIX = {
    (149, 1): "花这么多钱没什么意义。",
    (156, 1): "我在超市碰巧遇到了我的英语老师。",
    (165, 0): "抄袭（复制+粘贴）非常容易，但你应该自己写一些东西。",
    (217, 1): "一方面我想做老师，但另一方面我为挣钱而担忧。",
    (220, 2): "如果对你来说更容易记住的话，尽管叫我的英文名。",
    (227, 2): "很抱歉打扰你，可是我有个问题要问你。",
    (227, 3): "很抱歉打扰你，不过会议很快就要开始了。",
    (228, 0): "多亏了父母的鼓励和支持，他终于实现了自己的梦想。",
}


def main():
    paths = sys.argv[1:]
    if not paths:
        paths = [str(Path(__file__).resolve().parent.parent / "data.json")]
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        by_num = {it["num"]: it for it in data}
        ne = nc = npt = 0
        for (num, idx), en in EN_FIX.items():
            assert idx < len(by_num[num]["examples"]), (num, idx)
            if by_num[num]["examples"][idx]["en"] != en:
                by_num[num]["examples"][idx]["en"] = en
                ne += 1
        for (num, idx), cn in CN_FIX.items():
            assert idx < len(by_num[num]["examples"]), (num, idx)
            if by_num[num]["examples"][idx]["cn"] != cn:
                by_num[num]["examples"][idx]["cn"] = cn
                nc += 1
        for num, pat in PATTERN_FIX.items():
            if by_num[num]["pattern"] != pat:
                by_num[num]["pattern"] = pat
                npt += 1
        # 校订后不应再有明显 OCR 垃圾串
        junk = []
        for it in data:
            for fld, txt in [("pattern", it["pattern"])] + [
                (f"ex{i+1}", e["en"]) for i, e in enumerate(it["examples"])
            ]:
                if re.search(r"\b(?:tecoev|dsfe|dxahwe|pestion|becaulse|thatl|afteroon|shouls|Iadmire|iborrow|ibecame|ibegan|iwonit|ithappened|itdepends|yougo|butyou|breathwhen|clearsup|footballottery|significan't|atis|mater|1didnt|1wouldn't|1think|8sound|1s)\b|\b(?:doesn|don)'\s", txt, re.I):
                    junk.append((it["num"], fld, txt[:60]))
        if junk:
            print(f"[FAIL] {p} 仍残留 OCR 垃圾: {junk[:10]}", file=sys.stderr)
            sys.exit(1)
        Path(p).write_text(
            json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(f"[OK] {p}: 校订英文 {ne} 句 / 中文 {nc} 句 / 标题 {npt} 条，共 {len(data)} 个句式")


if __name__ == "__main__":
    main()
