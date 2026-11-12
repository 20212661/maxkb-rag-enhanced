#!/usr/bin/env python3
"""Check the deployed database, Redis, local embedding and MinerU service contracts."""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
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
                      "embedding_dimension": len(vector), "mineru_blocks": len(result["content_list"])}, indent=2))


if __name__ == "__main__":
    main()
