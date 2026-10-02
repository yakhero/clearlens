"""Gemini client. Python standard library only: urllib and json, no SDK, no requests.

    data, model = generate_json(prompt, schema, pdf_bytes=None, system="...")

* Structured output: every call sends generationConfig.responseSchema and asks for
  application/json, and the reply is parsed with Decimal for every number — never float.
* Model choice: GEMINI_MODEL if set, otherwise the newest stable Flash model the key can call
  (ListModels, filtered: no preview / exp / lite / thinking / tts / image / live variants).
* Retired models: a 404 that says the model is no longer available usually names the
  replacement ("... Please use gemini-2.5-flash instead"). We read that name out of the
  message and retry with it; if none is named, we fall back to ListModels. Bounded hops.
* The key, two ways:
    - in code: GEMINI_API_KEY from the environment, else st.secrets["GEMINI_API_KEY"] (e.g.
      Streamlit Cloud). Sent in the x-goog-api-key header — never in a URL, a log line, an
      error message or a widget.
    - not in code: the request goes out with no key header, and an environment that holds the
      credential (a proxy injecting x-goog-api-key) adds it. If nothing adds it, Google answers
      403 and we say so. available() asks ListModels once to find out which case we are in.
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_S = 120
MAX_MODEL_HOPS = 2              # replacement-model retries per call
MAX_TRANSIENT_RETRIES = 2       # 429 / 500 / 503
MAX_RETRY_WAIT_S = 60           # longest wait we accept from Google's "retry in Ns"
_UNSTABLE = re.compile(r"preview|exp|experimental|lite|thinking|tts|image|live|audio|"
                       r"latest|native|embedding|8b", re.I)
_FLASH = re.compile(r"^gemini-(\d+(?:\.\d+)?)-flash(?:-(\d{3}))?$")
_MODEL_ID = re.compile(r"\b(?:models/)?(gemini-[a-z0-9][a-z0-9.\-]*[a-z0-9])\b", re.I)
_REPLACEMENT_HINT = re.compile(r"(?:use|switch(?:ing)? to|migrate to|replaced by|"
                               r"replacement(?: model)?(?: is)?|instead,? (?:use|try)|"
                               r"move to|upgrade to)\W+(?:the\s+)?(?:model\s+)?[`'\"]?"
                               r"(?:models/)?(gemini-[a-z0-9][a-z0-9.\-]*[a-z0-9])", re.I)

_chosen_model: Optional[str] = None     # cached per process once a model has worked
_reachable: Optional[bool] = None       # cached answer of available()


class LLMError(RuntimeError):
    """Anything that stops a model call. The message never contains the API key."""


class NoAPIKey(LLMError):
    """Neither the code nor the environment supplied a key: Google refused the call."""


class ModelUnavailable(LLMError):
    def __init__(self, model: str, message: str):
        super().__init__(message)
        self.model, self.message = model, message


# ---------------------------------------------------------------- key
def api_key() -> Optional[str]:
    """GEMINI_API_KEY from the environment, else from Streamlit secrets. None if neither."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    try:
        import streamlit as st
        key = str(st.secrets.get("GEMINI_API_KEY", "")).strip()
    except Exception:  # noqa: BLE001 - no streamlit, no secrets.toml: simply no key
        key = ""
    return key or None


def key_source() -> str:
    """Where the key comes from — for the UI to say *that* there is one, never what it is."""
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "environment (GEMINI_API_KEY)"
    if api_key():
        return "Streamlit secrets"
    return "a credential the environment adds to each request" if available() else ""


def available() -> bool:
    """Can we call Gemini? True with a key in code; otherwise one ListModels call decides."""
    global _reachable
    if api_key():
        return True
    if _reachable is None:
        try:
            list_models(None)
            _reachable = True
        except LLMError:
            _reachable = False
    return _reachable


# ---------------------------------------------------------------- transport
def _http(method: str, url: str, headers: Dict[str, str], body: Optional[bytes],
          timeout: float) -> Tuple[int, Dict[str, Any]]:
    """One HTTP exchange -> (status, parsed JSON). Tests replace this function."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "replace")
        try:
            return err.code, json.loads(raw or "{}")
        except ValueError:
            return err.code, {"error": {"code": err.code, "message": raw[:500]}}
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise LLMError(f"could not reach the Gemini API: {getattr(err, 'reason', err)}") from None


def _call(method: str, path: str, key: Optional[str],
          payload: Optional[dict] = None) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if key:                      # otherwise the environment injects x-goog-api-key
        headers["x-goog-api-key"] = key
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    for attempt in range(MAX_TRANSIENT_RETRIES + 1):
        status, data = _http(method, f"{API_ROOT}/{path}", headers, body, TIMEOUT_S)
        if status < 400:
            return data
        message = _redact(str((data.get("error") or {}).get("message") or data), key)
        if status in (429, 500, 503) and attempt < MAX_TRANSIENT_RETRIES:
            time.sleep(_retry_wait(data, message, attempt))
            continue
        if status == 404 and path.startswith("models/") and ":" in path:
            raise ModelUnavailable(path.split("/", 1)[1].split(":", 1)[0], message)
        if status in (401, 403) and not key and ("API key" in message
                                                 or "unregistered callers" in message):
            raise NoAPIKey("no Gemini API key: set GEMINI_API_KEY, add it to "
                           ".streamlit/secrets.toml, or configure it as an environment "
                           "credential")
        if status in (401, 403) and "API key" in message:
            raise LLMError("the Gemini API rejected the key (check GEMINI_API_KEY)")
        raise LLMError(f"Gemini API {status}: {message}")
    raise LLMError("Gemini API kept failing")      # pragma: no cover - loop always returns


def _retry_wait(data: Dict[str, Any], message: str, attempt: int) -> float:
    """Google's own retry hint (RetryInfo.retryDelay, or 'retry in 26.3s'), else 2s, 4s."""
    hint = None
    for detail in (data.get("error") or {}).get("details") or []:
        delay = str(detail.get("retryDelay", "")) if isinstance(detail, dict) else ""
        if delay.endswith("s"):
            try:
                hint = float(delay[:-1])
            except ValueError:
                pass
    if hint is None:
        found = re.search(r"retry in ([\d.]+)\s*s", message, re.I)
        hint = float(found.group(1)) if found else None
    if hint is None:
        return 2.0 * (attempt + 1)
    return min(hint + 1.0, MAX_RETRY_WAIT_S)


def _redact(text: str, key: str) -> str:
    return text.replace(key, "[redacted]") if key else text


# ---------------------------------------------------------------- models
def list_models(key: Optional[str]) -> List[dict]:
    out, token = [], ""
    while True:
        data = _call("GET", "models?pageSize=1000" + (f"&pageToken={token}" if token else ""),
                     key)
        out += data.get("models", [])
        token = data.get("nextPageToken", "")
        if not token:
            return out


def pick_model(models: List[dict], exclude: Tuple[str, ...] = ()) -> str:
    """Newest stable Flash model that supports generateContent.

    'gemini-2.5-flash' beats 'gemini-2.0-flash-001'; at the same version the bare alias wins
    over a pinned -00N, then the higher pin.
    """
    best = None
    for m in models:
        name = str(m.get("name", "")).split("/")[-1]
        if name in exclude or _UNSTABLE.search(name):
            continue
        if "generateContent" not in m.get("supportedGenerationMethods", ["generateContent"]):
            continue
        hit = _FLASH.match(name)
        if not hit:
            continue
        rank = (tuple(int(x) for x in hit.group(1).split(".")), hit.group(2) is None,
                int(hit.group(2) or 0))
        if best is None or rank > best[0]:
            best = (rank, name)
    if best is None:
        raise LLMError("no stable Gemini Flash model is available to this key")
    return best[1]


def replacement_from_message(message: str, failed: str) -> Optional[str]:
    """Read the replacement model out of a 'no longer available' message, if it names one."""
    hint = _REPLACEMENT_HINT.search(message)
    if hint and hint.group(1).lower() != failed.lower():
        return hint.group(1).lower()
    others = [m.lower() for m in _MODEL_ID.findall(message) if m.lower() != failed.lower()]
    return others[-1] if others else None


def current_model(key: Optional[str] = None) -> str:
    global _chosen_model
    if os.environ.get("GEMINI_MODEL", "").strip():
        return os.environ["GEMINI_MODEL"].strip()
    if _chosen_model is None:
        _chosen_model = pick_model(list_models(key if key is not None else api_key()))
    return _chosen_model


# ---------------------------------------------------------------- generation
def generate_json(prompt: str, schema: dict, *, pdf_bytes: Optional[bytes] = None,
                  system: Optional[str] = None, model: Optional[str] = None,
                  temperature: float = 0.0) -> Tuple[Any, str]:
    """One structured call. Returns (parsed JSON with Decimal numbers, model actually used)."""
    global _chosen_model
    key = api_key()             # None: the environment adds the key header
    parts: List[dict] = [{"text": prompt}]
    if pdf_bytes:
        parts.append({"inline_data": {"mime_type": "application/pdf",
                                      "data": base64.b64encode(pdf_bytes).decode("ascii")}})
    payload: Dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": temperature,
                             "responseMimeType": "application/json",
                             "responseSchema": schema},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    name = model or current_model(key)
    tried: List[str] = []
    for _ in range(MAX_MODEL_HOPS + 1):
        tried.append(name)
        try:
            data = _call("POST", f"models/{name}:generateContent", key, payload)
        except ModelUnavailable as gone:
            nxt = replacement_from_message(gone.message, name)
            if not nxt or nxt in tried:
                nxt = pick_model(list_models(key), exclude=tuple(tried))
            name = nxt
            continue
        if model is None:
            _chosen_model = name               # remember what worked
        return _parse_reply(data), name
    raise LLMError(f"no working Gemini model after trying {', '.join(tried)}")


def _parse_reply(data: Dict[str, Any]) -> Any:
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
        raise LLMError(f"Gemini returned nothing ({reason})")
    first = candidates[0]
    text = "".join(p.get("text", "") for p in (first.get("content") or {}).get("parts", []))
    if not text.strip():
        raise LLMError(f"Gemini returned no text (finishReason {first.get('finishReason')})")
    try:
        return json.loads(text, parse_float=Decimal)
    except ValueError as err:
        raise LLMError(f"Gemini did not return valid JSON: {err}") from None
