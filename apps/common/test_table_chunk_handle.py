# coding=utf-8
"""
    @project: MaxKB
    @file: test_table_chunk_handle.py
    @desc: Table structure, native prose delegation and feature switch regressions.
"""
import importlib
import unittest
from unittest.mock import patch

import common.chunk as chunking
from common.chunk.impl.mark_chunk_handle import MarkChunkHandle
from common.chunk.impl.table_chunk_handle import TableChunkHandle
from maxkb.const import CONFIG


class TableChunkTests(unittest.TestCase):
    def setUp(self):
        self.handle = TableChunkHandle()

    def test_html_rows_repeat_headers(self):
        table = '<table><caption>岗位表</caption><tr><th>姓名</th><th>职责</th></tr>' + ''.join(
            f'<tr><td>人员{i}</td><td>负责备份与恢复工作</td></tr>' for i in range(10)) + '</table>'
        chunks = self.handle.handle([table], 100)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.startswith('岗位表\n| 姓名 | 职责 |\n| --- | --- |') for c in chunks))
        self.assertTrue(all(len(c) <= 100 for c in chunks))
        self.assertTrue(all(sum(f'人员{i}' in c for c in chunks) == 1 for i in range(10)))
        self.assertNotIn('<td', ''.join(chunks))

    def test_markdown_alignment_escaped_pipes_and_prose_order(self):
        text = '前文。\n\n| 项目 | 要求 |\n| :--- | ---: |\n| A | 保留\\|字符 |\n\n后文。'
        chunks = self.handle.handle([text], 100)
        self.assertEqual(chunks[0], '前文。')
        self.assertIn('保留\\|字符', chunks[1])
        self.assertEqual(chunks[-1], '后文。')

    def test_html_in_prose_and_headerless_table_preserve_first_row(self):
        text = 'before\n<table><tr><td>甲</td><td>乙</td></tr><tr><td>丙</td><td>丁</td></tr></table>\nafter'
        chunks = self.handle.handle([text], 100)
        self.assertEqual((chunks[0], chunks[-1]), ('before', 'after'))
        self.assertIn('| 列1 | 列2 |', chunks[1])
        self.assertIn('| 甲 | 乙 |', chunks[1])
        self.assertIn('| 丙 | 丁 |', chunks[1])

    def test_long_row_is_split_without_losing_cell_content(self):
        text = '| key | value |\n| --- | --- |\n| A | ' + 'x' * 1000 + ' |'
        chunks = self.handle.handle([text], 100)
        self.assertGreater(len(chunks), 10)
        self.assertEqual(sum(c.count('x') for c in chunks), 1000)
        self.assertTrue(all(c.startswith('| key | value |\n| --- | --- |') for c in chunks))
        self.assertTrue(all(len(c) <= 100 for c in chunks))

    def test_spanning_cells_and_oversized_headers(self):
        table = ('<table><thead><tr><th colspan="2">人员</th></tr>'
                 '<tr><th>姓名</th><th>职责</th></tr></thead>'
                 '<tr><td rowspan="2">甲</td><td>备份</td></tr><tr><td>恢复</td></tr></table>')
        result = '\n'.join(self.handle.handle([table], 100))
        self.assertIn('人员 / 姓名', result)
        self.assertIn('| 甲 | 备份 |', result)
        self.assertIn('| 甲 | 恢复 |', result)
        long_header = 'h' * 200
        chunks = self.handle.handle([f'| {long_header} |\n| --- |\n| xyz |'], 50)
        self.assertTrue(all(long_header in c for c in chunks))
        self.assertEqual(sum(c.count('x') + c.count('y') + c.count('z') for c in chunks), 3)

    def test_plain_text_and_fenced_tables_use_native_handler(self):
        for text in ['', 'ordinary ' * 100, '```html\n<table><tr><td>code</td></tr></table>\n```',
                     '```md\n| A | B |\n| --- | --- |\n| 1 | 2 |\n```']:
            with self.subTest(text=text[:20]):
                self.assertFalse(self.handle.support(text))
                self.assertEqual(self.handle.handle([text], 100), MarkChunkHandle().handle([text], 100))

    def test_dispatch_and_disabled_switch(self):
        table = '| A | B |\n| --- | --- |\n' + ('| long | ' + 'x' * 90 + ' |\n') * 2
        try:
            with patch.dict(CONFIG, {'TABLE_CHUNK_ENABLED': True}):
                importlib.reload(chunking)
                self.assertIsInstance(chunking.handles[0], TableChunkHandle)
                chunks = chunking.text_to_chunk(table, 100)
                self.assertTrue(all(c.startswith('| A | B |') for c in chunks))
            with patch.dict(CONFIG, {'TABLE_CHUNK_ENABLED': 'false'}):
                importlib.reload(chunking)
                self.assertEqual(len(chunking.handles), 1)
                self.assertIsInstance(chunking.handles[0], MarkChunkHandle)
                self.assertEqual(chunking.text_to_chunk(table, 100), MarkChunkHandle().handle([table], 100))
        finally:
            importlib.reload(chunking)
