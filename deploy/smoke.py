#!/usr/bin/env python3
"""Check the deployed database, Redis, local embedding and MinerU service contracts."""
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "maxkb.settings")
os.environ.setdefault("SERVER_NAME", "web")


def main():
    import django
    django.setup()
    from django.core.cache import cache
    from django.db import connection
    from PIL import Image, ImageDraw
    from common.utils.mineru_client import MinerUClient
    from models_provider.tools import get_model_instance_by_model_workspace_id

    static_assets = []
    for surface in ("admin", "chat"):
        page_url = f"http://127.0.0.1:8080/{surface}/"
        with urllib.request.urlopen(page_url, timeout=10) as response:
            page = response.read().decode("utf-8")
        scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', page)
        assert scripts, f"No frontend script in {surface} HTML"
        asset_url = urllib.parse.urljoin(page_url, scripts[0])
        with urllib.request.urlopen(asset_url, timeout=10) as response:
            assert "javascript" in response.headers.get("Content-Type", ""), asset_url
            assert response.read(100), f"Empty static asset for {surface}"
        static_assets.append(surface)
    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')")
        extensions = sorted(row[0] for row in cursor.fetchall())
        assert extensions == ["pg_trgm", "vector"], extensions
        for table in ("mineru_parse_job", "mineru_document_build", "embedding_vector_cache", "knowledge_cache_state"):
            cursor.execute("SELECT to_regclass(%s)", [table])
            assert cursor.fetchone()[0] is not None, table
    key = "deploy-smoke:" + uuid.uuid4().hex
    cache.set(key, "ready", timeout=30)
    assert cache.get(key) == "ready"
    cache.delete(key)
    model = get_model_instance_by_model_workspace_id("42f63a3d-427e-11ef-b3ec-a8a1595801ab", "default")
    vector = model.embed_query("Deployment local embedding smoke test")
    assert len(vector) == 768 and all(math.isfinite(value) for value in vector)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "business-scan.png"
        image = Image.new("RGB", (1400, 600), "white")
        draw = ImageDraw.Draw(image)
        draw.text((70, 100), "ASSET REGISTER", fill="black", font_size=55)
        draw.text((70, 220), "Weekly security inspection", fill="black", font_size=45)
        image.save(path)
        with MinerUClient() as client:
            result = client.parse(path, request_id="deploy-" + uuid.uuid4().hex)
        assert result["content_list"], "MinerU returned an empty Content List"
    print(json.dumps({"database_extensions": extensions, "migrations": "ready", "redis": "ready",
                      "static_assets": static_assets, "embedding_dimension": len(vector),
                      "mineru_blocks": len(result["content_list"])}, indent=2))


if __name__ == "__main__":
    main()
