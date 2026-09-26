"""方案 3.5.1 的 Query 预处理：繁简归一与停用词剔除，两条都只作用在查询侧。

文档侧保持原形：BM25 稀疏向量与 dense 向量都已写进 Qdrant，改文档侧词形会要求一次全量重建才能对齐，
而当前 31 篇语料本身是简体的，简体语料 + 归一查询已经能对上。等下一次全量重建时再把文档侧一起归一。
"""

from zhconv import convert

ZH_CN = "zh-cn"
STOPWORDS = frozenset(
    """
    的 了 是 在 有 和 与 及 或 也 就 都 而 被 把 让 对 从 向 由 以 于 之 其 该 此 这 那
    吗 呢 吧 啊 呀 么 什么 怎么 怎样 如何 为什么 哪些 哪个 哪里 是否 可以 能不能 有没有
    一下 一个 我们 你们 他们 以及 通过 进行 相关 时候 由于 但是 可是 然后 那么 如果 因为 所以
    """.split()
)


def to_simplified(text: str) -> str:
    return convert(text, ZH_CN)


def warm_normalizer() -> None:
    """zhconv 第一次调用要在事件循环上解析内置词典 JSON，这笔开销挪到启动时付。"""
    to_simplified("緩存")


def drop_stopwords(tokens: list[str]) -> list[str]:
    """全被剔除时退回原词，避免编码出一个匹配不到任何东西的空稀疏向量。"""
    kept = [token for token in tokens if token not in STOPWORDS]
    return kept or tokens
