# coding=utf-8
"""
    @project: MaxKB
    @file: model_profile.py
    @desc: Immutable embedding identity captured when the model is constructed.
"""
import hashlib
import json
from dataclasses import dataclass

from maxkb.const import CONFIG


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def enabled(name):
    return str(CONFIG.get(name, True)).lower() in ("true", "1", "yes", "on")


def public_params(params):
    """Do not fingerprint credentials even if a provider exposes them as parameters."""
    if isinstance(params, dict):
        secrets = {"api_key", "apikey", "access_key", "secret_key", "password", "secret", "token",
                   "access_token", "auth_token", "credential", "credentials", "authorization"}
        return {key: public_params(value) for key, value in params.items() if key.lower() not in secrets}
    if isinstance(params, (tuple, list)):
        return [public_params(value) for value in params]
    return params


@dataclass(frozen=True)
class EmbeddingProfile:
    model_id: str
    workspace_id: str
    documents_hash: str
    query_hash: str

    def fingerprint(self, mode):
        return self.documents_hash if mode == "embed_documents" else self.query_hash


def bind_embedding_profile(instance, model_id, workspace_id, provider, model_name, params):
    config = {"provider": provider, "model_name": model_name, "params": public_params(params),
              "epoch": int(CONFIG.get("EMBEDDING_CACHE_EPOCH", 1))}
    profile = EmbeddingProfile(str(model_id), str(workspace_id),
        digest({**config, "mode": "embed_documents"}), digest({**config, "mode": "embed_query"}))
    object.__setattr__(instance, "_maxkb_embedding_profile", profile)
    return instance


def get_profile(instance):
    profile = getattr(instance, "_maxkb_embedding_profile", None)
    return profile if isinstance(profile, EmbeddingProfile) else None
