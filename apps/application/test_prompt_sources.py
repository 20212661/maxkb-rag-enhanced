# coding=utf-8
"""
    @project: MaxKB
    @file: test_prompt_sources.py
    @desc: Knowledge source formatting through the SIMPLE prompt entry point.
"""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from application.chat_pipeline.step.generate_human_message_step.impl.base_generate_human_message_step import (
    BaseGenerateHumanMessageStep,
)
from application.services.prompt_sources import format_paragraphs, source_format_settings
from maxkb.const import CONFIG


class PromptSourcesTests(SimpleTestCase):
    def setUp(self):
        config = patch.dict(CONFIG, {"PROMPT_INCLUDE_DOCUMENT_NAME": True})
        config.start()
        self.addCleanup(config.stop)
        self.row = {"document_name": "中卫市.pdf", "title": "机房", "content": "授权区人员陪同"}

    def test_document_name_and_simple_entrypoint(self):
        paragraph = SimpleNamespace(to_dict=lambda: self.row)
        message = BaseGenerateHumanMessageStep.to_human_message(
            "资料{data}问题{question}", "谁陪同", 100, [paragraph], {})
        self.assertEqual(message.content, "资料<data>【来源：中卫市.pdf】机房：授权区人员陪同</data>问题谁陪同")

    def test_empty_names_and_switch_off_keep_legacy_text(self):
        for name in (None, "", "  "):
            with self.subTest(name=name):
                self.assertEqual(format_paragraphs([{**self.row, "document_name": name}], 100),
                                 "机房:授权区人员陪同")
        for value in (False, "false", "0"):
            with self.subTest(value=value), patch.dict(CONFIG, {"PROMPT_INCLUDE_DOCUMENT_NAME": value}):
                self.assertEqual(format_paragraphs([self.row], 100), "机房:授权区人员陪同")
                self.assertFalse(source_format_settings()["include_document_name"])

    def test_document_name_is_limited_to_64_characters(self):
        self.row["document_name"] = "文" * 65
        self.assertEqual(format_paragraphs([self.row], 200), "【来源：" + "文" * 64 + "】机房：授权区人员陪同")

    def test_budget_counts_prefix_and_stops_at_exact_boundary(self):
        full = format_paragraphs([self.row], 100)
        for budget in (len(full), len(full) - 2):
            with self.subTest(budget=budget):
                result = format_paragraphs([self.row, self.row], budget, wrap_data=True)
                self.assertEqual(result, f"<data>{full[:budget]}</data>")
        self.assertEqual(format_paragraphs([self.row], 0), "")
        self.assertEqual(format_paragraphs([self.row], len("【来源：中卫市.pdf】")), "")

    def test_multiple_paragraphs_use_only_remaining_budget(self):
        full = format_paragraphs([self.row], 100)
        second = {"document_name": "", "title": "标题", "content": "正文很长"}
        self.assertEqual(format_paragraphs([self.row, second], len(full) + 4), full + "\n标题:")
        self.assertEqual(format_paragraphs([self.row, second], len(full) + 4, wrap_data=True),
                         f"<data>{full}</data>\n<data>标题:正</data>")
