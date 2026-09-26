"""方案 3.5.1 的"意图识别 → 选择检索策略"：只在客户端把 search_type 交给服务端决定时替它选。

规则来自方案 3.5.2 的模式表，刻意保持可解释：
- 明确的英文术语 / 标识符为主体 → keyword（词面命中优先，还能省一次远程向量调用）；
- 自然语言叙述或提问 → hybrid（方案的默认推荐，Dense 与精排负责语义覆盖）；
- fuzzy 不由意图自动选择：没有词表参照就无法判断"用户拼写不确定"，它保留为客户端显式模式。
"""

import re
from typing import Literal

from app.services.retrieval.bm25_encoder import terms
from app.services.retrieval.normalize import to_simplified

Strategy = Literal["keyword", "hybrid"]
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_+.#-]*")
CJK = re.compile(r"[\u4e00-\u9fff]")
QUESTION_MARKERS = ("什么", "怎么", "怎样", "如何", "为什么", "哪些", "哪个", "是否", "区别", "多少")
END_MARKERS = ("？", "?", "吗", "呢")


def detect_strategy(query: str) -> Strategy:
    text = to_simplified(query).strip()
    if not text:
        return "hybrid"
    narrative = len(CJK.findall(text)) >= 8 or any(marker in text for marker in QUESTION_MARKERS)
    if narrative or text.endswith(END_MARKERS):
        return "hybrid"
    tokens = terms(text)
    if any(IDENTIFIER.fullmatch(token) for token in tokens):
        return "keyword"
    return "hybrid"
