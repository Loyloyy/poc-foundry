"""Scaffold smoke test — GREEN the moment the template is stamped, BEFORE any tool-calling logic and
WITHOUT the tool sibling or the model. It exercises the offline-safe surface: the `CATALOG_PRODUCTS`
shape and a cheap model-client construct guard, and confirms the editable `core.generate_reply`
interface exists. The real `call_tool` round-trip + the `llm` generation are covered by the harness's
red-first criterion tests, which run with the tool sibling + the model endpoint up.

`toolkit` is the non-editable primitives library, so these imports stay valid no matter what the build
writes in `core.py` — the clean-room cannot break by the editable file dropping an export.
"""
from toolkit import CATALOG_PRODUCTS
from core import generate_reply


def test_catalog_products_is_a_nonempty_list_of_names():
    assert CATALOG_PRODUCTS and all(isinstance(p, str) and p for p in CATALOG_PRODUCTS)


def test_generate_reply_interface_exists():
    assert callable(generate_reply)               # the editable glue keeps the interface


def test_model_client_constructs_when_configured():
    """Guard the model-calling contract CHEAPLY + offline: when an endpoint is configured the OpenAI
    client must CONSTRUCT — catches a broken client (e.g. the openai/httpx `proxies` mismatch). NO
    round-trip, so the scaffold stays fast and does not depend on the model being reachable; the actual
    answer round-trip is verified by the criterion tests. Skipped when no endpoint is set (offline)."""
    import os

    import pytest

    if not os.environ.get("PF_SANDBOX_MODEL_BASE_URL"):
        pytest.skip("no PF_SANDBOX_MODEL_BASE_URL configured — offline")
    from openai import OpenAI
    OpenAI(base_url=os.environ["PF_SANDBOX_MODEL_BASE_URL"],
           api_key=os.environ.get("PF_SANDBOX_VLLM_KEY", "not-needed"))   # raises on the proxies break
