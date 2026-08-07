#!/usr/bin/env python3
"""Real tests for capability_semantic_search.py (UMR-20260806-124055-bc80).

Unit-level only -- cosine math, content-hash determinism, and the cache's
hit/miss routing (mocking the real OpenRouter call so these never spend
money or need network access; the real end-to-end call is exercised and its
output pasted in PROGRESS.md/the capability record's test_evidence field,
per this UMR's own "real boolean evidence" requirement, not re-asserted here
as a flaky network-dependent unit test).
"""
import json
import os
import tempfile

import capability_semantic_search as css


def test_cosine_similarity_identical_vectors_is_one():
    v = [0.1, 0.2, 0.3, -0.4]
    assert abs(css.cosine_similarity(v, v) - 1.0) < 1e-9


def test_cosine_similarity_orthogonal_vectors_is_zero():
    assert abs(css.cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_cosine_similarity_zero_vector_never_divides_by_zero():
    assert css.cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_content_hash_is_deterministic_and_distinguishes_text():
    assert css._content_hash("same text") == css._content_hash("same text")
    assert css._content_hash("text a") != css._content_hash("text b")


def test_capability_row_text_includes_name_and_owner():
    row = {
        "capability_name": "widget_search",
        "owner": "test-owner",
        "apis": ["scripts/widget.py search"],
        "workflow": "scripts/widget.py",
        "permissions": "internal",
        "business_rules": ["rule one"],
    }
    text = css._capability_row_text(row)
    assert "widget_search" in text
    assert "test-owner" in text
    assert "scripts/widget.py search" in text


def test_wiring_script_row_text_includes_path_and_purpose():
    row = {
        "entity_id": "script-widget_py",
        "path": "/opt/veridian/scripts/widget.py",
        "metadata_json": json.dumps({"purpose": "does widget things"}),
    }
    text = css._wiring_script_row_text(row)
    assert "/opt/veridian/scripts/widget.py" in text
    assert "does widget things" in text


def test_embed_texts_reuses_cache_and_skips_api_for_hits(monkeypatch):
    calls = []

    def fake_fetch(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts], None

    monkeypatch.setattr(css, "_fetch_embeddings_real", fake_fetch)

    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "cache.sqlite")
        monkeypatch.setattr(css, "CACHE_DB_PATH", db_path)
        conn = css._cache_connect()

        vectors1, err1 = css.embed_texts(["alpha", "beta"], conn)
        assert err1 is None
        assert vectors1["alpha"] == [1.0, 0.0]
        assert len(calls) == 1 and set(calls[0]) == {"alpha", "beta"}

        # Second call for the same texts must hit the cache -- no new API call.
        vectors2, err2 = css.embed_texts(["alpha", "beta"], conn)
        assert err2 is None
        assert vectors2 == vectors1
        assert len(calls) == 1  # unchanged -- cache satisfied both

        conn.close()


def test_embed_texts_reports_error_without_fabricating_a_score(monkeypatch):
    def failing_fetch(texts):
        return None, "simulated failure"

    monkeypatch.setattr(css, "_fetch_embeddings_real", failing_fetch)

    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "cache.sqlite")
        monkeypatch.setattr(css, "CACHE_DB_PATH", db_path)
        conn = css._cache_connect()

        vectors, err = css.embed_texts(["never cached"], conn)
        assert err == "simulated failure"
        assert "never cached" not in vectors
        conn.close()
