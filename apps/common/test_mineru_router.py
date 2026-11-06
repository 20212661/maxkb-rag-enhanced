# coding=utf-8
"""
    @project: MaxKB
    @file: test_mineru_router.py
    @desc: Routing, real PDF text layers, sampling and soft-budget regression tests.
"""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from common.utils.mineru_router import PDFProbeError, decide, probe_pdf


def pdf_file(counts, *, encrypted=False):
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    for count in counts:
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        })
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 10 Tf 10 700 Td (" + b"x" * count + b") Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("")  # Even empty-password encryption must take the native route.
    file = io.BytesIO()
    writer.write(file)
    file.name = "sample.pdf"
    file.seek(7)
    return file


class MinerURouterTests(unittest.TestCase):
    def test_extensions_and_case_insensitivity(self):
        expected = {"png": ("basic", "image_ext"), "jpg": ("basic", "image_ext"),
                    "jpeg": ("basic", "image_ext"), "webp": ("basic", "image_ext"),
                    "bmp": ("basic", "image_ext"), "ppt": ("flash", "ppt"), "pptx": ("flash", "pptx"),
                    "txt": (None, "native_default"), "md": (None, "native_default"),
                    "csv": (None, "native_default"), "docx": (None, "native_default"),
                    "xlsx": (None, "native_default"), "zip": (None, "zip_native_only")}
        for suffix, (tier, reason) in expected.items():
            for extension in (suffix, suffix.upper(), suffix.title()):
                with self.subTest(extension=extension):
                    file = io.BytesIO(b"data")
                    file.name = "file." + extension
                    file.seek(2)
                    route = decide(file, {}, config={})
                    self.assertEqual((route["tier"], route["reason"]), (tier, reason))
                    self.assertEqual(route["use_mineru"], tier is not None)
                    self.assertEqual(file.tell(), 2)
                    self.assertEqual(route["metrics"]["probed_pages"], [])

    def test_average_and_ratio_boundaries_with_real_pdfs(self):
        cases = [([99], "low_text_avg"), ([100], "native_default"),
                 ([50] * 3 + [200] * 7, "native_default"),
                 ([49] * 3 + [200] * 7, "low_text_ratio"),
                 ([0] * 2 + [500] * 8, "native_default"),
                 ([0] * 3 + [500] * 7, "low_text_ratio"), ([0], "low_text_avg")]
        for counts, reason in cases:
            with self.subTest(counts=counts):
                file = pdf_file(counts)
                route = decide(file, None, config={})
                self.assertEqual(route["reason"], reason)
                self.assertEqual(route["metrics"]["avg_chars_per_page"], sum(counts) / len(counts))
                self.assertEqual(file.tell(), 7)

    def test_thresholds_are_configurable(self):
        config = {"MINERU_SCAN_AVG_CHAR_THRESHOLD": "0", "MINERU_SCAN_LOW_PAGE_CHAR_THRESHOLD": "100",
                  "MINERU_SCAN_LOW_PAGE_RATIO_THRESHOLD": "0.5"}
        route = decide(pdf_file([99, 200]), {}, config=config)
        self.assertEqual(route["reason"], "low_text_ratio")

    def test_forcing_modes_and_supported_office(self):
        file = pdf_file([500])
        self.assertFalse(decide(file, {"mineru_route": "auto"}, config={})["use_mineru"])
        forced = decide(file, {"mineru_route": "force_mineru"}, config={})
        self.assertEqual((forced["reason"], forced["tier"]), ("forced", "basic"))
        self.assertEqual(forced["metrics"]["page_count"], 1)
        self.assertEqual(forced["metrics"]["probed_pages"], [])
        for suffix in ("PDF", "PNG", "PPTX", "DOCX", "XLSX", "XLS"):
            file = pdf_file([500])
            file.name = "sample." + suffix
            self.assertEqual(decide(file, {"mineru_route": "force_mineru"}, config={})["reason"], "forced")
            native = decide(file, {"mineru_route": "force_native"}, config={})
            self.assertEqual((native["use_mineru"], native["reason"]), (False, "forced"))
            self.assertEqual(file.tell(), 7)

    def test_forced_unsupported_and_zip(self):
        for suffix in ("txt", "MD", "CSV", "unknown"):
            file = io.BytesIO(b"source")
            file.name = "sample." + suffix
            route = decide(file, {"mineru_route": "force_mineru"}, config={})
            self.assertEqual((route["use_mineru"], route["reason"]), (False, "forced_unsupported"))
        file.name = "sample.ZIP"
        self.assertEqual(decide(file, {"mineru_route": "force_mineru"}, config={})["reason"], "zip_native_only")

    def test_encrypted_corrupt_and_empty_pdf_restore_position(self):
        bad = io.BytesIO(b"invalid PDF")
        bad.name = "broken.pdf"
        for file in (pdf_file([300], encrypted=True), pdf_file([]), bad):
            position = file.tell()
            for mode in ("auto", "force_mineru"):
                with self.subTest(file=file.name, mode=mode), self.assertLogs(
                    "common.utils.mineru_router", level="WARNING",
                ) as logs:
                    route = decide(file, {"mineru_route": mode}, config={})
                self.assertEqual((route["use_mineru"], route["reason"]), (False, "probe_failed"))
                self.assertIn("probe failed", logs.output[0])
                self.assertEqual(file.tell(), position)

    def test_sampling_is_unique_uniform_and_includes_last_page(self):
        for count in (30, 31, 101):
            with self.subTest(count=count):
                file = pdf_file([200] * count)
                metrics = probe_pdf(file, config={})
                pages = metrics["probed_pages"]
                self.assertEqual(metrics["page_count"], count)
                self.assertEqual(len(pages), 30)
                self.assertEqual(pages[:10], list(range(10)))
                self.assertEqual(pages[-1], count - 1)
                self.assertEqual(pages, sorted(set(pages)))
                gaps = [right - left for left, right in zip(pages[10:], pages[11:])]
                self.assertLessEqual(max(gaps) - min(gaps), 1)
                self.assertEqual(metrics["avg_chars_per_page"], 200)
                self.assertEqual(file.tell(), 7)

    def test_unicode_whitespace_and_nul_are_excluded(self):
        page = SimpleNamespace(extract_text=lambda: "甲 \n\t\u3000乙\0")
        reader = SimpleNamespace(is_encrypted=False, pages=[page])
        with patch("common.utils.mineru_router.PdfReader", return_value=reader):
            metrics = probe_pdf(pdf_file([1]), config={})
        self.assertEqual(metrics["avg_chars_per_page"], 2)

    def test_soft_budget_returns_completed_pages(self):
        clock = [0]

        def extract():
            clock[0] += 6
            return "x" * 200

        reader = SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=extract)] * 5)
        with patch("common.utils.mineru_router.PdfReader", return_value=reader), \
                patch("common.utils.mineru_router.time.monotonic", side_effect=lambda: clock[0]):
            file = pdf_file([1])
            metrics = probe_pdf(file, config={})
        self.assertEqual(metrics["probed_pages"], [0])
        self.assertEqual(metrics["page_count"], 5)
        self.assertEqual(metrics["avg_chars_per_page"], 200)
        self.assertEqual(file.tell(), 7)

    def test_budget_exhausted_before_any_page(self):
        clock = [0]

        def open_reader(file):
            clock[0] = 6
            return SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: "x")])

        file = pdf_file([1])
        with patch("common.utils.mineru_router.PdfReader", side_effect=open_reader), \
                patch("common.utils.mineru_router.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(PDFProbeError):
                probe_pdf(file, config={})
        self.assertEqual(file.tell(), 7)

    def test_extraction_error_and_invalid_config_fall_back(self):
        page = SimpleNamespace(extract_text=unittest.mock.Mock(side_effect=RuntimeError("bad page")))
        reader = SimpleNamespace(is_encrypted=False, pages=[page])
        with patch("common.utils.mineru_router.PdfReader", return_value=reader), \
                self.assertLogs("common.utils.mineru_router", level="WARNING"):
            file = pdf_file([1])
            self.assertEqual(decide(file, {}, config={})["reason"], "probe_failed")
            self.assertEqual(file.tell(), 7)
        with self.assertLogs("common.utils.mineru_router", level="WARNING"):
            self.assertEqual(decide(file, {}, config={"MINERU_PDF_PROBE_TIMEOUT_SECONDS": 0})["reason"], "probe_failed")

    def test_calibration_report_and_failed_file_continuation(self):
        from scripts.calibrate_router import calibrate
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("sys.modules", {"maxkb.const": SimpleNamespace(CONFIG={})}):
            root = Path(directory)
            (root / "电子.PDF").write_bytes(pdf_file([200]).getvalue())
            (root / "扫描.pdf").write_bytes(pdf_file([0]).getvalue())
            (root / "坏文件.pdf").write_bytes(b"bad pdf")
            (root / "ignore.txt").write_text("text")
            output = root / "report.md"
            with self.assertLogs("common.utils.mineru_router", level="WARNING"):
                self.assertEqual(calibrate([root], output), 3)
            report = output.read_text("utf-8")
            for expected in ("电子.PDF", "200.00", "low_text_avg", "probe_failed", "坏文件.pdf", "实际探测页"):
                self.assertIn(expected, report)

    def test_multiple_directories_expectations_and_both_error_lists(self):
        from scripts.calibrate_router import calibrate
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("sys.modules", {"maxkb.const": SimpleNamespace(CONFIG={})}):
            first, second = Path(directory) / "first", Path(directory) / "second"
            first.mkdir()
            second.mkdir()
            (first / "电子版.pdf").write_bytes(pdf_file([0]).getvalue())
            (second / "scan_pages.pdf").write_bytes(pdf_file([200]).getvalue())
            (second / "未知.pdf").write_bytes(pdf_file([200]).getvalue())
            output = Path(directory) / "report.md"
            self.assertEqual(calibrate([first, second], output), 3)
            report = output.read_text("utf-8")
            self.assertLess(report.index("## " + first.as_posix()), report.index("## " + second.as_posix()))
            self.assertIn("3 份 PDF，2 份有预期标签，1 份留空", report)
            summary = report.split("## 误判清单")[1]
            positives, negatives = summary.split("### 扫描件或图片 PDF 被判成原生")
            self.assertIn("电子版.pdf", positives)
            self.assertIn("scan_pages.pdf", negatives)
            self.assertNotIn("未知.pdf", summary)


if __name__ == "__main__":
    unittest.main()
