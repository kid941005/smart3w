"""正文相关性筛选：按查询词挑选最相关的段落，控制返回给 Agent 的上下文长度。

不引入额外依赖，使用简单的词频打分，支持中文/英文混排。仅用于已提取的 Markdown/纯文本。
"""

from __future__ import annotations

import math
import re
from typing import Iterable

_ASCII_WORD = re.compile(r"[a-z0-9_]+")
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")


def split_blocks(text: str) -> list[str]:
    """按空行切分文本块，保留 Markdown 标题与列表段落。"""
    if not text:
        return []
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line)
        elif current:
            blocks.append("\n".join(current).strip())
            current = []
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def terms(text: str) -> set[str]:
    """提取查询词：英文按词，中文按单字（避免依赖分词库）。"""
    lowered = (text or "").lower()
    result = set(_ASCII_WORD.findall(lowered))
    result.update(_CJK_CHAR.findall(lowered))
    return {term for term in result if term}


def score_block(block: str, query_terms: Iterable[str]) -> float:
    """词频打分：命中词数加权，按块长度做轻微归一化，标题略加权。"""
    if not block:
        return 0.0
    lowered = block.lower()
    score = 0.0
    for term in query_terms:
        count = lowered.count(term)
        if count:
            score += 1.0 + math.log1p(count)
    if _HEADING.match(block):
        score *= 1.15
    return score / math.sqrt(max(len(block), 1) / 200.0 + 1.0)


def filter_content(
    text: str,
    query: str,
    *,
    max_chars: int = 0,
    include_headings: bool = True,
) -> tuple[str, dict]:
    """按查询词筛选正文，返回 (筛选后文本, 统计信息)。

    - 无查询词或无匹配时原样返回，避免把正文清空。
    - 有 max_chars 时按得分从高到低取块，再按原文顺序拼接；包含最近的上文标题。
    """
    blocks = split_blocks(text)
    query_terms = terms(query)
    stats = {"total_blocks": len(blocks), "matched_blocks": 0, "returned_chars": len(text or "")}
    if not blocks or not query_terms:
        return text, stats

    scored: list[tuple[int, float, str]] = []
    for index, block in enumerate(blocks):
        score = score_block(block, query_terms)
        if score > 0:
            scored.append((index, score, block))
    if not scored:
        return text, stats
    stats["matched_blocks"] = len(scored)

    chosen: set[int] = {index for index, _, _ in scored}
    if include_headings:
        for index, _, _ in scored:
            previous = index - 1
            if previous >= 0 and _HEADING.match(blocks[previous]):
                chosen.add(previous)

    if max_chars > 0:
        ranked = sorted(scored, key=lambda item: item[1], reverse=True)
        selected: list[int] = []
        used = 0
        for index, _, block in ranked:
            candidate = {index}
            if include_headings and index - 1 >= 0 and _HEADING.match(blocks[index - 1]):
                candidate.add(index - 1)
            extra = sum(len(blocks[i]) + 2 for i in candidate if i not in selected)
            if selected and used + extra > max_chars:
                continue
            for i in candidate:
                if i not in selected:
                    selected.append(i)
                    used += len(blocks[i]) + 2
        chosen = set(selected)

    selected_blocks = [blocks[i] for i in sorted(chosen)]
    output = "\n\n".join(selected_blocks)
    if max_chars > 0 and len(output) > max_chars:
        output = output[:max_chars]
    stats["returned_chars"] = len(output)
    return output, stats
