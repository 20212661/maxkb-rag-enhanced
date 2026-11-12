# coding=utf-8
"""
    @project: MaxKB
    @file: embedding_cache.py
    @desc: PG document vectors and short-lived Redis query vectors.
"""
import hashlib
import logging
from contextlib import ExitStack

import numpy as np
from django.core.cache import cache
from django.db import transaction

from common.cache.model_profile import enabled, get_profile
from common.cache.single_flight import CacheLease

logger = logging.getLogger("maxkb")


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def encode(vector):
    array = np.asarray(vector, dtype="<f4")
    if array.ndim != 1 or not array.size or not np.isfinite(array).all():
        raise ValueError("Embedding must be a nonempty finite vector")
    return array.tobytes(), int(array.size)


def decode(data, dimension, codec=1):
    array = np.frombuffer(bytes(data), dtype="<f4")
    if codec != 1 or dimension <= 0 or array.size != dimension or not np.isfinite(array).all():
        raise ValueError("Invalid cached embedding")
    return array.tolist()


def _identity(profile, mode):
    return {"workspace_id": profile.workspace_id, "embedding_model_id": profile.model_id,
            "model_profile_hash": profile.fingerprint(mode)}


def _read_documents(identity, hashes):
    from knowledge.models import EmbeddingVectorCache
    try:
        result = {}
        for row in EmbeddingVectorCache.objects.filter(**identity, text_sha256__in=hashes):
            try:
                result[row.text_sha256] = decode(row.vector_data, row.dimension, row.codec_version)
            except ValueError:
                row.delete()
                logger.warning("Removed invalid embedding cache row=%s", row.pk)
        return result
    except Exception:
        logger.warning("Embedding cache read failed", exc_info=True)
        return {}


def cached_documents(embedding, texts, mode="embed_documents"):
    if mode not in ("embed_documents", "embed_query"):
        raise ValueError("Unsupported embedding call mode")
    profile = get_profile(embedding)

    def compute(values):
        if mode == "embed_documents":
            return embedding.embed_documents(values)
        return [embedding.embed_query(value) for value in values]
    if not enabled("EMBEDDING_CACHE_ENABLED") or profile is None or not texts:
        return compute(texts)
    identity = _identity(profile, mode)
    unique = {text_hash(text): text for text in texts}
    values = _read_documents(identity, unique)
    missing = [key for key in unique if key not in values]
    with ExitStack() as stack:
        for key in sorted(missing):
            stack.enter_context(CacheLease(f"{profile.workspace_id}:cache:v1:emb-lock:"
                                          f"{profile.model_id}:{profile.fingerprint(mode)}:{key}", wait=1))
        values.update(_read_documents(identity, missing))
        missing = [key for key in missing if key not in values]
        if missing:
            vectors = compute([unique[key] for key in missing])
            if len(vectors) != len(missing):
                raise ValueError("Embedding result count differs from input count")
            from knowledge.models import EmbeddingVectorCache
            rows = []
            for key, vector in zip(missing, vectors):
                data, dimension = encode(vector)
                values[key] = decode(data, dimension)
                rows.append(EmbeddingVectorCache(**identity, text_sha256=key,
                                                vector_data=data, dimension=dimension))
            if len({row.dimension for row in rows} | {len(value) for value in values.values()}) != 1:
                raise ValueError("Embedding dimensions differ within a batch")
            try:
                with transaction.atomic():
                    EmbeddingVectorCache.objects.bulk_create(rows, ignore_conflicts=True)
            except Exception:
                logger.warning("Embedding cache publish failed", exc_info=True)
    logger.info("Embedding cache inputs=%d unique=%d misses=%d model=%s",
                len(texts), len(unique), len(missing), profile.model_id)
    if len({len(vector) for vector in values.values()}) != 1:
        raise ValueError("Cached embedding dimensions differ within a batch")
    return [values[text_hash(text)] for text in texts]


def cached_query(embedding, text):
    profile = get_profile(embedding)
    if not enabled("QUERY_EMBEDDING_CACHE_ENABLED") or profile is None:
        return embedding.embed_query(text)
    key = (f"{profile.workspace_id}:cache:v1:qemb:{profile.model_id}:"
           f"{profile.query_hash}:{text_hash(text)}")

    def read():
        try:
            value = cache.get(key)
            return decode(value["data"], value["dimension"]) if value is not None else None
        except Exception:
            logger.warning("Query embedding cache read failed", exc_info=True)
            return None

    value = read()
    if value is not None:
        return value
    with CacheLease(key + ":lock"):
        value = read()
        if value is not None:
            return value
        data, dimension = encode(embedding.embed_query(text))
        try:
            cache.set(key, {"data": data, "dimension": dimension}, timeout=600)
        except Exception:
            logger.warning("Query embedding cache publish failed", exc_info=True)
        return decode(data, dimension)
