# coding=utf-8
"""Rebuild ignored samples from the versioned public evaluation corpus."""

import hashlib
import io
import json
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "samples/manifest.json").read_text(encoding="utf-8"))
    fixture = PdfReader(root.parents[1] / "eval/assets/corpus/image_panels.pdf")
    poster = fixture.pages[0].images[0].indirect_reference.get_object().get_data()
    expected_poster = next(item["sha256"] for item in manifest["cases"] if item["id"] == "image")
    if hashlib.sha256(poster).hexdigest() != expected_poster:
        raise ValueError("Embedded poster hash mismatch")
    (root / "samples/security-poster.jpg").write_bytes(poster)
    source = root.parents[1] / "eval/assets/corpus/中卫市公开信息安全管理制度.pdf"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_hash != "adeb7dcecb2f26d1c76645439b7f0bbd0928a577a87cfbcee51a5ba6a444a240":
        raise ValueError("Business policy source hash mismatch")
    output = root / "samples/business_policy_scan.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    with pdfium.PdfDocument(source) as document:
        writer = canvas.Canvas(str(output), pageCompression=1, invariant=1)
        for page in document:
            width, height = page.get_size()
            bitmap = page.render(scale=1800 / max(width, height))
            picture = bitmap.to_pil().convert("RGB")
            buffer = io.BytesIO()
            picture.save(buffer, format="JPEG", quality=90)
            buffer.seek(0)
            writer.setPageSize((width, height))
            writer.drawImage(ImageReader(buffer), 0, 0, width=width, height=height)
            writer.showPage()
            bitmap.close()
            page.close()
        writer.save()
    parsed = PdfReader(output)
    if len(parsed.pages) != 7 or any(page.extract_text().strip() for page in parsed.pages):
        raise ValueError("Expected seven image-only pages")
    provenance = {"source_file": str(source.relative_to(root.parents[1])), "source_sha256": source_hash,
                  "output_file": "samples/business_policy_scan.pdf", "pages": 7,
                  "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                  "kind": "simulated_scan", "raster_long_edge_pixels": 1800, "text_layer": False}
    (root / "samples/business_policy_scan.provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(provenance, ensure_ascii=False))


if __name__ == "__main__":
    main()
