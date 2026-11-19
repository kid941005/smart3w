"""smart3w_content 正文相关性筛选测试（无网络）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart3w_content import filter_content, score_block, split_blocks, terms  # noqa: E402

SAMPLE = "# Apple pie\n\nApple pie is a fruit dessert.\n\n# Car engine\n\nA car engine burns fuel."


def test_split_blocks_keeps_headings_and_paragraphs():
    blocks = split_blocks(SAMPLE)
    assert blocks[0] == "# Apple pie"
    assert "fruit dessert" in blocks[1]


def test_terms_mixes_ascii_and_cjk():
    result = terms("Apple 苹果 pie")
    assert "apple" in result
    assert "苹" in result and "果" in result


def test_filter_content_keeps_matching_block_and_heading():
    filtered, stats = filter_content(SAMPLE, "apple")
    assert "fruit dessert" in filtered
    assert "car engine" not in filtered.lower()
    assert stats["matched_blocks"] == 2


def test_filter_content_returns_original_when_no_match():
    filtered, stats = filter_content(SAMPLE, "banana")
    assert filtered == SAMPLE
    assert stats["matched_blocks"] == 0


def test_filter_content_respects_max_chars():
    filtered, stats = filter_content(SAMPLE, "apple pie fruit dessert car engine", max_chars=40)
    assert len(filtered) <= 40
    assert stats["returned_chars"] == len(filtered)


def test_score_block_prefers_query_hits():
    hit = score_block("apple apple apple", {"apple"})
    miss = score_block("banana", {"apple"})
    assert hit > miss
