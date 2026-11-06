# coding=utf-8
"""
    @project: MaxKB
    @file: test_mineru_converter.py
    @desc: Database-free regression tests for Content List conversion and section splitting.
"""
import copy
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from common.utils.mineru_converter import (
    MAX_CONTENT_LENGTH,
    MinerUConversionError,
    blocks_to_paragraphs,
    content_list_to_blocks,
)


class MinerUConverterTests(unittest.TestCase):
    @staticmethod
    def text(text, *, page=0, level=None):
        item = {"type": "text", "text": text, "page_idx": page}
        if level is not None:
            item["text_level"] = level
        return item

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]
        cls.fixtures = json.loads((Path(__file__).parent / "fixtures/mineru_content_lists.json").read_text("utf-8"))
        documentation = (cls.root / "docs/mineru-fields.md").read_text("utf-8")
        cls.examples = [json.loads(body) for body in re.findall(r"```json\n(.*?)\n```", documentation, re.DOTALL)]

    def test_four_documentation_examples(self):
        self.assertEqual(len(self.examples), 4)
        receipt, result, expected, pending = self.examples
        blocks = content_list_to_blocks(result["content_list"], page_count=result["page_count"])
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[2]["image_ref"], next(iter(result["images"])))
        paragraphs = blocks_to_paragraphs(blocks[:2])
        public = [{key: row[key] for key in ("title", "content", "problem_list")} for row in paragraphs]
        self.assertEqual(public, expected["content"])
        for invalid in (receipt, result, pending, pending["content"]):
            with self.subTest(invalid=invalid), self.assertRaises(MinerUConversionError):
                content_list_to_blocks(invalid)

    def test_real_samples_preserve_text_types_pages_and_order(self):
        for name, fixture in self.fixtures.items():
            with self.subTest(sample=name):
                items = fixture["content_list"]
                original = copy.deepcopy(items)
                blocks = content_list_to_blocks(items, page_count=fixture["page_count"])
                self.assertEqual(len(blocks), len(items))
                self.assertEqual([b["type"] for b in blocks], [b["type"] for b in items])
                self.assertEqual([b["page"] for b in blocks], [b["page_idx"] for b in items])
                for item, block in zip(items, blocks):
                    if "text" in item:
                        self.assertEqual(block["text"], item["text"].replace("\0", ""))
                    if "table_body" in item:
                        self.assertIn(item["table_body"], block["text"])
                    self.assertEqual(block["image_ref"], item.get("img_path"))
                blocks_to_paragraphs(blocks)
                self.assertEqual(items, original)

    def test_empty_document(self):
        self.assertEqual(content_list_to_blocks([], page_count=0), [])
        self.assertEqual(blocks_to_paragraphs([]), [])

    def test_conversion_and_splitting_have_no_file_or_network_access(self):
        items = [self.text("小节", level=1), self.text("正文"),
                 {"type": "image", "img_path": "images/example.jpg", "page_idx": 0}]
        with patch("builtins.open", side_effect=AssertionError("File access")), \
                patch("socket.socket", side_effect=AssertionError("Network access")):
            paragraphs = blocks_to_paragraphs(content_list_to_blocks(items))
        self.assertEqual(len(paragraphs), 1)

    def test_only_images_and_caller_replaces_references(self):
        items = [
            {"type": "image", "img_path": "images/page 1.jpg", "page_idx": 0},
            {"type": "image", "img_path": "images/page2.jpg", "page_idx": 1,
             "image_caption": ["流程图"], "image_footnote": ["备注"]},
        ]
        blocks = content_list_to_blocks(items, page_count=2)
        self.assertEqual(blocks[0]["text"], "")
        self.assertIn("![](<images/page 1.jpg>)", blocks_to_paragraphs(blocks)[0]["content"])
        materialized = [{**block, "image_ref": f"./oss/file/{index}"} for index, block in enumerate(blocks)]
        paragraphs = blocks_to_paragraphs(materialized)
        self.assertEqual(len(paragraphs), 1)
        self.assertIn("![](<./oss/file/0>)", paragraphs[0]["content"])
        self.assertIn("流程图\n\n备注", paragraphs[0]["content"])
        self.assertEqual(paragraphs[0]["page_indices"], [0, 1])
        self.assertEqual(paragraphs[0]["source_block_indices"], [0, 1])
        self.assertEqual(blocks[0]["image_ref"], "images/page 1.jpg")

    def test_json_round_trip_and_input_independence(self):
        items = [self.text("文字") | {"bbox": [0, 0.5, 900, 1000]}, self.text("缺省坐标", page=1)]
        blocks = content_list_to_blocks(items)
        restored = json.loads(json.dumps(blocks))
        self.assertEqual(restored, blocks)
        self.assertIsInstance(blocks[0]["bbox"], list)
        self.assertTrue(all(type(value) is float for value in blocks[0]["bbox"]))
        self.assertIsNone(blocks[1]["bbox"])
        self.assertEqual(blocks_to_paragraphs(restored), blocks_to_paragraphs(blocks))
        restored[0]["bbox"][0] = 100
        blocks[0]["bbox"][1] = 200
        self.assertEqual(items[0]["bbox"], [0, 0.5, 900, 1000])

    def test_page_count_optional_and_zero_based_boundaries(self):
        items = [self.text("正文", page=9)]
        self.assertEqual(content_list_to_blocks(items, page_count=None)[0]["page"], 9)
        self.assertEqual(content_list_to_blocks(items, page_count=10)[0]["page"], 9)
        with self.assertRaises(MinerUConversionError):
            content_list_to_blocks(items, page_count=9)
        for page in (-1, True, 1.5, "0", None):
            with self.subTest(page=page), self.assertRaises(MinerUConversionError):
                content_list_to_blocks([self.text("正文", page=page)])
        for count in (-1, True, "10", 1.5):
            with self.subTest(count=count), self.assertRaises(MinerUConversionError):
                content_list_to_blocks([], page_count=count)

    def test_invalid_bbox(self):
        for bbox in ([0, 1], [0, 0, 1001, 5], [5, 0, 1, 5], [0, 5, 1, 4],
                     [0, 0, float("nan"), 1], [0, 0, float("inf"), 1],
                     [False, 0, 1, 1], ["0", 0, 1, 1], [0, 0, 10 ** 400, 1], "0,0,1,1"):
            with self.subTest(bbox=bbox), self.assertRaises(MinerUConversionError):
                content_list_to_blocks([self.text("正文") | {"bbox": bbox}])

    def test_no_conversion_filter_and_explicit_drop_types(self):
        items = [{"type": kind, "text": kind, "page_idx": 0} for kind in
                 ("header", "text", "footer", "page_number", "future_type")]
        blocks = content_list_to_blocks(items)
        self.assertEqual([b["type"] for b in blocks], [b["type"] for b in items])
        self.assertEqual(blocks_to_paragraphs(blocks)[0]["content"], "text\n\nfuture_type")
        unfiltered = blocks_to_paragraphs(blocks, drop_types=())
        self.assertEqual(unfiltered[0]["content"], "\n\n".join(item["text"] for item in items))
        custom = blocks_to_paragraphs(blocks, drop_types=("text", "future_type"))
        self.assertEqual(custom[0]["source_block_indices"], [0, 2, 3])

    def test_multiple_heading_levels_and_section_granularity(self):
        items = [self.text("总则", level=1), self.text("范围", level=2), self.text("第一块"),
                 self.text("第二块", page=1), self.text("细则", level=3), self.text("细则正文"),
                 self.text("责任", level=2), self.text("责任正文"), self.text("附录", level=1), self.text("附录正文")]
        blocks = content_list_to_blocks(items)
        self.assertEqual(blocks[0]["type"], "text")
        paragraphs = blocks_to_paragraphs(blocks)
        self.assertEqual([p["title"] for p in paragraphs], ["总则 / 范围", "总则 / 范围 / 细则", "总则 / 责任", "附录"])
        self.assertEqual(paragraphs[0]["content"], "第一块\n\n第二块")
        self.assertEqual(paragraphs[0]["source_block_indices"], [0, 1, 2, 3])
        self.assertEqual(paragraphs[0]["page_indices"], [0, 1])

    def test_heading_only_sections_and_preamble(self):
        blocks = content_list_to_blocks([self.text("前言"), self.text("空标题", level=1),
                                         self.text("另一标题", level=1), self.text("子标题", level=2)])
        paragraphs = blocks_to_paragraphs(blocks)
        self.assertEqual([p["content"] for p in paragraphs], ["前言", "空标题", "另一标题 / 子标题"])

    def test_invalid_heading_levels_stay_body(self):
        for level in (0, 7, True, "2", None):
            with self.subTest(level=level):
                block = content_list_to_blocks([self.text("正文", level=level)])[0]
                self.assertIsNone(block["level"])
                self.assertEqual(blocks_to_paragraphs([block])[0]["title"], "")

    def test_title_truncation_uses_256_characters_and_retains_overflow(self):
        for length in (255, 256, 257, 600):
            with self.subTest(length=length):
                title = "题" * length
                paragraphs = blocks_to_paragraphs(content_list_to_blocks([
                    self.text(title, level=1), self.text("正文"),
                ]))
                self.assertEqual(paragraphs[0]["title"], title[:256])
                expected = title[256:] + "\n\n正文" if length > 256 else "正文"
                self.assertEqual(paragraphs[0]["content"], expected)

    def test_long_heading_only_and_hierarchical_title_truncation(self):
        title = "题" * 257
        paragraph = blocks_to_paragraphs(content_list_to_blocks([self.text(title, level=1)]))[0]
        self.assertEqual(paragraph["title"], title[:256])
        self.assertEqual(paragraph["content"], title)
        paragraphs = blocks_to_paragraphs(content_list_to_blocks([
            self.text("父" * 250, level=1), self.text("子" * 10, level=2), self.text("正文"),
        ]))
        full_title = "父" * 250 + " / " + "子" * 10
        self.assertEqual(paragraphs[0]["title"], full_title[:256])
        self.assertEqual(paragraphs[0]["content"], full_title[256:] + "\n\n正文")

    def test_default_limit_is_4096_and_blocks_are_grouped(self):
        blocks = content_list_to_blocks([self.text("小节", level=1)] + [self.text("文" * 1200) for _ in range(3)])
        self.assertEqual(len(blocks_to_paragraphs(blocks)), 1)
        blocks = content_list_to_blocks([self.text("小节", level=1), self.text("文" * 4097)])
        paragraphs = blocks_to_paragraphs(blocks)
        self.assertEqual([len(p["content"]) for p in paragraphs], [2049, 2048])
        self.assertEqual([p["title"] for p in paragraphs], ["小节", "小节"])
        self.assertTrue(all(p["source_block_indices"] == [0, 1] for p in paragraphs))

    def test_custom_limit_keeps_text_and_continuation_provenance(self):
        text = "甲" * 60 + "\n" + "乙" * 80 + "\n" + "丙" * 90
        blocks = content_list_to_blocks([self.text("章节", level=1), self.text(text, page=1)])
        original = copy.deepcopy(blocks)
        paragraphs = blocks_to_paragraphs(blocks, limit=100)
        self.assertEqual("".join(p["content"] for p in paragraphs), text)
        self.assertTrue(all(len(p["content"]) <= 100 for p in paragraphs))
        self.assertTrue(all(p["page_indices"] == [0, 1] for p in paragraphs))
        self.assertEqual(blocks, original)

    def test_cross_page_tables_keep_headers_and_source_pages(self):
        header = "<thead><tr><th rowspan='2'>项目</th><th>金额</th></tr><tr><th>元</th></tr></thead>"
        tables = [f"<table>{header}<tbody><tr><td>{label}</td><td>10</td></tr></tbody></table>" for label in ("甲", "乙")]
        items = [self.text("费用", level=1)] + [
            {"type": "table", "table_body": table, "page_idx": page} for page, table in enumerate(tables)
        ]
        blocks = content_list_to_blocks(items, page_count=2)
        self.assertEqual([b["table_header"] for b in blocks[1:]], [header, header])
        paragraphs = blocks_to_paragraphs(blocks)
        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(paragraphs[0]["content"], "\n\n".join(tables))
        self.assertEqual(paragraphs[0]["page_indices"], [0, 1])
        self.assertEqual(paragraphs[0]["source_block_indices"], [0, 1, 2])

    def test_markdown_table_headers_and_caption_footnote(self):
        table = "|名称|值|\n|:---|---:|\n|甲|1|\n"
        blocks = content_list_to_blocks([{"type": "table", "table_body": table, "page_idx": 0,
                                          "table_caption": ["表一"], "table_footnote": ["注一"]}])
        self.assertEqual(blocks[0]["table_header"], "|名称|值|\n|:---|---:|\n")
        self.assertEqual(blocks[0]["text"], f"表一\n\n{table}\n\n注一")

    def test_html_header_rows_and_no_invented_header(self):
        for body, header in (("<table><tr><th>名称</th></tr><tr><td>甲</td></tr></table>", "<tr><th>名称</th></tr>"),
                             ("<table><tr><td>名称</td></tr></table>", None)):
            with self.subTest(body=body):
                block = content_list_to_blocks([{"type": "table", "page_idx": 0, "table_body": body}])[0]
                self.assertEqual(block["text"], body)
                self.assertEqual(block["table_header"], header)

    def test_atomic_tables_and_images_can_exceed_soft_limit(self):
        items = [self.text("前文"), {"type": "table", "page_idx": 0,
                 "table_body": "<table><tr><td>完整表格</td></tr></table>"},
                 {"type": "image", "img_path": "images/example.jpg", "page_idx": 1}, self.text("后文")]
        paragraphs = blocks_to_paragraphs(content_list_to_blocks(items), limit=10)
        self.assertEqual(len(paragraphs), 4)
        self.assertEqual(paragraphs[1]["content"], items[1]["table_body"])
        self.assertEqual(paragraphs[2]["content"], "![](<images/example.jpg>)")

    def test_atomic_storage_limit_is_explicit_error(self):
        block = content_list_to_blocks([{"type": "table", "page_idx": 0, "table_body": "文" * (MAX_CONTENT_LENGTH + 1)}])
        with self.assertRaisesRegex(MinerUConversionError, "storage limit"):
            blocks_to_paragraphs(block)

    def test_limit_boundaries(self):
        for limit in (0, -1, True, "4096", 1.5, MAX_CONTENT_LENGTH + 1):
            with self.subTest(limit=limit), self.assertRaises(MinerUConversionError):
                blocks_to_paragraphs([], limit=limit)
        paragraphs = blocks_to_paragraphs(content_list_to_blocks([self.text("甲乙")]), limit=1)
        self.assertEqual([p["content"] for p in paragraphs], ["甲", "乙"])
        paragraphs = blocks_to_paragraphs(content_list_to_blocks([self.text("文" * MAX_CONTENT_LENGTH)]),
                                         limit=MAX_CONTENT_LENGTH)
        self.assertEqual(len(paragraphs[0]["content"]), MAX_CONTENT_LENGTH)

    def test_equations_lists_and_nul(self):
        blocks = content_list_to_blocks([
            {"type": "equation", "text": "E=mc^2", "text_format": "latex", "img_path": "eq.jpg", "page_idx": 0},
            {"type": "equation", "img_path": "eq-only.jpg", "page_idx": 0},
            {"type": "list", "list_items": ["甲\0", "乙"], "page_idx": 0},
        ])
        content = blocks_to_paragraphs(blocks)[0]["content"]
        self.assertIn("E=mc^2", content)
        self.assertNotIn("![](<eq.jpg>)", content)
        self.assertIn("![](<eq-only.jpg>)", content)
        self.assertEqual(blocks[2]["text"], "甲\n\n乙")

    def test_invalid_shapes_and_missing_image(self):
        for content in ("text", {}, None, [None], [{"page_idx": 0}],
                        [{"type": "image", "page_idx": 0}],
                        [{"type": "text", "page_idx": 0}],
                        [{"type": "table", "page_idx": 0}],
                        [{"type": "list", "page_idx": 0}],
                        [{"type": "text", "page_idx": 0, "text": 123}],
                        [{"type": "list", "page_idx": 0, "list_items": [123]}]):
            with self.subTest(content=content), self.assertRaises(MinerUConversionError):
                content_list_to_blocks(content)
        for drop_types in ("header", None, [123]):
            with self.subTest(drop_types=drop_types), self.assertRaises(MinerUConversionError):
                blocks_to_paragraphs([], drop_types=drop_types)


if __name__ == "__main__":
    unittest.main()
