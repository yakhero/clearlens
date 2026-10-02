"""Tests for the Gemini client against a fake transport. No network and no real key."""
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import llm  # noqa: E402

KEY = "test-key-not-real-0123456789"
SCHEMA = {"type": "OBJECT", "properties": {"n": {"type": "NUMBER"}}}

MODELS = [
    {"name": "models/gemini-1.5-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.0-flash-001", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.0-flash-preview-09-2026",
     "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
]


def reply(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


class FakeAPI:
    """Stands in for llm._http. `gone` maps a model to the 404 message Google would send."""

    def __init__(self, models=MODELS, gone=None, answer='{"n": 1.10}'):
        self.models, self.gone, self.answer, self.calls = models, gone or {}, answer, []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": json.loads(body) if body else None})
        if "/models?" in url:
            return 200, {"models": self.models}
        model = url.split("/models/")[1].split(":")[0]
        if model in self.gone:
            return 404, {"error": {"code": 404, "status": "NOT_FOUND",
                                   "message": self.gone[model]}}
        return 200, reply(self.answer)


def run(fake, fn, env=None):
    saved_http, saved_env = llm._http, dict(os.environ)
    llm._http, llm._chosen_model = fake, None
    os.environ.pop("GEMINI_MODEL", None)
    os.environ["GEMINI_API_KEY"] = KEY
    os.environ.update(env or {})
    try:
        return fn()
    finally:
        llm._http, llm._chosen_model = saved_http, None
        os.environ.clear()
        os.environ.update(saved_env)


# ---------------------------------------------------------------- model choice
def test_picks_newest_stable_flash():
    assert llm.pick_model(MODELS) == "gemini-2.5-flash"


def test_bare_alias_beats_pinned_version_at_same_release():
    models = [m for m in MODELS if "2.0" in m["name"]]
    assert llm.pick_model(models) == "gemini-2.0-flash"


def test_preview_lite_pro_image_and_embedding_models_are_never_picked():
    for bad in ("gemini-2.5-flash-lite", "gemini-3.0-flash-preview-09-2026",
                "gemini-2.5-pro", "gemini-2.5-flash-image", "gemini-embedding-001"):
        assert llm.pick_model(MODELS) != bad
    try:
        llm.pick_model([MODELS[4], MODELS[5], MODELS[6]])
    except llm.LLMError:
        return
    raise AssertionError("no stable flash model must be an error, not a guess")


def test_model_is_chosen_from_list_models_not_hard_coded():
    fake = FakeAPI()
    _, model = run(fake, lambda: llm.generate_json("hi", SCHEMA))
    assert model == "gemini-2.5-flash"
    assert fake.calls[0]["url"].startswith(llm.API_ROOT + "/models?")
    assert fake.calls[1]["url"] == f"{llm.API_ROOT}/models/gemini-2.5-flash:generateContent"


def test_gemini_model_env_overrides_the_choice():
    fake = FakeAPI()
    _, model = run(fake, lambda: llm.generate_json("hi", SCHEMA),
                   env={"GEMINI_MODEL": "gemini-2.0-flash"})
    assert model == "gemini-2.0-flash" and len(fake.calls) == 1


# ---------------------------------------------------------------- retired models
def test_retired_model_is_replaced_by_the_one_named_in_the_error():
    fake = FakeAPI(gone={"gemini-2.5-flash": "Gemini 2.5 Flash is no longer available. "
                                             "Please use gemini-3.0-flash instead."})
    _, model = run(fake, lambda: llm.generate_json("hi", SCHEMA))
    assert model == "gemini-3.0-flash"
    urls = [c["url"] for c in fake.calls]
    assert urls[-2].endswith("/models/gemini-2.5-flash:generateContent")
    assert urls[-1].endswith("/models/gemini-3.0-flash:generateContent")


def test_replacement_parsing_handles_common_phrasings():
    cases = {
        "models/gemini-2.0-flash is no longer available. Please switch to "
        "models/gemini-2.5-flash.": "gemini-2.5-flash",
        "The model `gemini-1.5-flash` has been retired; use `gemini-2.5-flash` "
        "instead.": "gemini-2.5-flash",
        "gemini-2.0-flash-001 was deprecated and replaced by gemini-2.5-flash": "gemini-2.5-flash",
        "Model gemini-1.5-flash is no longer available, migrate to gemini-2.0-flash or "
        "later.": "gemini-2.0-flash",
    }
    for message, expected in cases.items():
        failed = llm._MODEL_ID.search(message).group(1)
        assert llm.replacement_from_message(message, failed) == expected, message


def test_404_without_a_named_replacement_falls_back_to_list_models():
    fake = FakeAPI(gone={"gemini-2.5-flash": "models/gemini-2.5-flash is not found for API "
                                             "version v1beta. Call ListModels."})
    _, model = run(fake, lambda: llm.generate_json("hi", SCHEMA))
    assert model == "gemini-2.0-flash"          # next newest stable flash, the dead one excluded


def test_replacement_hops_are_bounded():
    gone = {"gemini-2.5-flash": "no longer available, use gemini-2.6-flash",
            "gemini-2.6-flash": "no longer available, use gemini-2.7-flash",
            "gemini-2.7-flash": "no longer available, use gemini-2.8-flash",
            "gemini-2.8-flash": "no longer available, use gemini-2.9-flash"}
    try:
        run(FakeAPI(gone=gone), lambda: llm.generate_json("hi", SCHEMA))
    except llm.LLMError as err:
        assert "no working Gemini model" in str(err)
        return
    raise AssertionError("an endless chain of retirements must stop")


def test_a_working_replacement_is_remembered():
    fake = FakeAPI(gone={"gemini-2.5-flash": "no longer available. Please use gemini-3.0-flash"})

    def twice():
        llm.generate_json("a", SCHEMA)
        return llm.generate_json("b", SCHEMA)
    _, model = run(fake, twice)
    assert model == "gemini-3.0-flash"
    gen = [c["url"] for c in fake.calls if "generateContent" in c["url"]]
    assert gen[-1].endswith("gemini-3.0-flash:generateContent")      # no second 404 detour


# ---------------------------------------------------------------- request and reply
def test_request_carries_response_schema_and_json_mime_type():
    fake = FakeAPI()
    run(fake, lambda: llm.generate_json("hi", SCHEMA, system="be exact", pdf_bytes=b"%PDF-1.7"))
    body = fake.calls[-1]["body"]
    assert body["generationConfig"]["responseSchema"] == SCHEMA
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "be exact"
    inline = body["contents"][0]["parts"][1]["inline_data"]
    assert inline["mime_type"] == "application/pdf" and inline["data"] == "JVBERi0xLjc="


def test_numbers_come_back_as_decimal_never_float():
    data, _ = run(FakeAPI(answer='{"n": 1.10, "m": [2.5]}'),
                  lambda: llm.generate_json("hi", SCHEMA))
    assert data["n"] == Decimal("1.10") and isinstance(data["m"][0], Decimal)


def test_bad_json_from_the_model_is_an_error():
    try:
        run(FakeAPI(answer="not json"), lambda: llm.generate_json("hi", SCHEMA))
    except llm.LLMError:
        return
    raise AssertionError


# ---------------------------------------------------------------- the key
def test_key_goes_in_a_header_never_in_a_url_or_body():
    fake = FakeAPI()
    run(fake, lambda: llm.generate_json("hi", SCHEMA))
    for call in fake.calls:
        assert call["headers"]["x-goog-api-key"] == KEY
        assert KEY not in call["url"]
        assert KEY not in json.dumps(call["body"] or {})


def test_key_is_redacted_from_error_messages():
    def leaky(method, url, headers, body, timeout):
        return 400, {"error": {"message": f"bad request for key {KEY}"}}
    try:
        run(leaky, lambda: llm.generate_json("hi", SCHEMA, model="gemini-2.5-flash"))
    except llm.LLMError as err:
        assert KEY not in str(err) and "[redacted]" in str(err)
        return
    raise AssertionError


def test_no_key_is_a_clear_error_and_makes_no_request():
    fake = FakeAPI()

    def call():
        os.environ.pop("GEMINI_API_KEY", None)
        return llm.generate_json("hi", SCHEMA)
    try:
        run(fake, call)
    except llm.NoAPIKey as err:
        assert "GEMINI_API_KEY" in str(err)
        assert fake.calls == []
        return
    raise AssertionError


def test_key_source_names_the_source_not_the_key():
    source = run(FakeAPI(), llm.key_source)
    assert source == "environment (GEMINI_API_KEY)" and KEY not in source


def test_client_uses_only_the_standard_library():
    src = (Path(__file__).resolve().parents[1] / "core" / "llm.py").read_text(encoding="utf-8")
    for banned in ("import requests", "google.generativeai", "google.genai", "httpx"):
        assert banned not in src
