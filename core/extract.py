"""Extraction agents: one PDF in, LineItem[] out, every line carrying a page and a verbatim quote.

    pages = page_texts(pdf_bytes)
    items, model = extract("invoice", pdf_bytes)      # agent + citation check
    items = verify_citations(items, pages)            # the check on its own; pure code

The model reads; code checks. Each agent is shown the PDF's own text layer, page by page, and
must copy its quote from it. verify_citations() then confirms, in code, that the quote is on
the page it cites. If the quote sits within two pages of the citation the page is corrected;
anywhere else, or nowhere, and the line is flagged verified=False. LineItem.verified is set
here and only here — never by a model.

One page per call: each page is its own small request (they are shed less under load), and the
lines are merged in code. The model's answers are cached as JSON in data/consignments/cache/,
keyed by the PDF's SHA-256 and the document type, so a document is only ever sent once and the
demo runs with the API unavailable. The cache holds the model's raw answer; the citation check
runs again on every load, so a cached answer is checked exactly like a fresh one. A cache file
is written only when every page of the document succeeded.
"""
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from core import llm
from core.schemas import DOC_TYPES, LineItem, coerce_line_item

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "consignments" / "cache"
PAGE_WINDOW = 2          # a quote this many pages from its citation is corrected, not flagged
MIN_QUOTE_CHARS = 8      # shorter quotes prove nothing ("1,200" is on many pages)

_LINE_FIELDS = ["line_no", "description", "quantity", "unit", "unit_price", "currency",
                "value", "net_weight_kg", "gross_weight_kg", "origin", "incoterm", "page",
                "quote"]
# Numbers are strings so the digits arrive exactly as printed and never pass through float.
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"lines": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {
            "line_no": {"type": "INTEGER"},
            "description": {"type": "STRING"},
            "quantity": {"type": "STRING"},
            "unit": {"type": "STRING"},
            "unit_price": {"type": "STRING"},
            "currency": {"type": "STRING"},
            "value": {"type": "STRING"},
            "net_weight_kg": {"type": "STRING"},
            "gross_weight_kg": {"type": "STRING"},
            "origin": {"type": "STRING"},
            "incoterm": {"type": "STRING"},
            "page": {"type": "INTEGER"},
            "quote": {"type": "STRING"},
        },
        "required": ["line_no", "description", "page", "quote"],
        "propertyOrdering": _LINE_FIELDS,
    }}},
    "required": ["lines"],
}

SYSTEM = (
    "You extract product lines from one trade document for a customs clearing agent. "
    "Report only what is printed. Never calculate, convert, round or infer a value: if a "
    "field is not printed for a line, return an empty string. Copy numbers exactly as "
    "printed, including thousands separators. For every line, 'page' is the page number "
    "from the === PAGE n === marker, and 'quote' is the text of that product's row copied "
    "character for character from that page — do not retype, reorder or tidy it.")

AGENTS = {
    "invoice": (
        "This is a COMMERCIAL INVOICE. Return one entry per product line of the goods table. "
        "Fill description, quantity, unit, unit_price, currency, value (the line amount), and "
        "net_weight_kg / gross_weight_kg if printed per line. origin is the country of origin "
        "and incoterm the delivery term (FOB, CFR, CIF...), each only if printed. Ignore "
        "totals, freight and insurance rows."),
    "packing_list": (
        "This is a PACKING LIST. Return one entry per product line. Fill description, "
        "quantity and unit (packages as printed), net_weight_kg and gross_weight_kg per line, "
        "and origin if printed. Packing lists normally carry no prices: leave unit_price, "
        "currency and value empty unless printed on the line. Ignore total rows."),
    "bill_of_lading": (
        "This is a BILL OF LADING. Return one entry per goods line in the 'description of "
        "goods' area. Fill description, quantity and unit (number and kind of packages), "
        "gross_weight_kg, net_weight_kg if printed, and origin if printed. Leave prices empty. "
        "Ignore freight charges, container and seal numbers, and the terms and conditions."),
}


# ---------------------------------------------------------------- PDF text
def page_texts(pdf_bytes: bytes) -> List[str]:
    """Text layer of each page, in reading order. Index 0 is page 1."""
    import pymupdf
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [page.get_text("text", sort=True) for page in doc]


def _prompt(doc_type: str, page_no: int, text: str, total: int) -> str:
    return (f"{AGENTS[doc_type]}\n\nThis request carries page {page_no} of {total} only. "
            "Return the product lines printed on this page, or an empty list if there are "
            f"none.\n\n=== PAGE {page_no} ===\n{text.strip()}")


# ---------------------------------------------------------------- citation check (code only)
def normalise(text: str) -> str:
    """Compare text, not typography: NFKC, one kind of quote/dash, single spaces, lowercase."""
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = t.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                                   "–": "-", "—": "-", " ": " "}))
    return re.sub(r"\s+", " ", t).strip().lower()


def find_quote(quote: str, pages: Sequence[str], cited: int) -> Tuple[Optional[int], str]:
    """Where the quote really is: (page, how). page is None when it cannot be accepted."""
    q = normalise(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return None, "quote missing or too short to check"
    norm = [normalise(p) for p in pages]
    if not any(norm):
        return None, "the PDF has no text layer, so the quote cannot be checked"
    if 1 <= cited <= len(norm) and q in norm[cited - 1]:
        return cited, "found on the cited page"
    for dist in range(1, PAGE_WINDOW + 1):                 # nearest page first
        for page in (cited - dist, cited + dist):
            if 1 <= page <= len(norm) and q in norm[page - 1]:
                return page, f"found on page {page}, not the cited page {cited}: page corrected"
    elsewhere = [n for n, text in enumerate(norm, 1) if q in text]
    if elsewhere:
        return None, (f"quote is on page {elsewhere[0]}, more than {PAGE_WINDOW} pages from the "
                      f"cited page {cited}")
    return None, f"quote not found on page {cited} or within {PAGE_WINDOW} pages of it"


def verify_citations(items: Sequence[LineItem], pages: Sequence[str]) -> List[LineItem]:
    """Check every line's quote against the page it cites. Returns new LineItems.

    verified=True  quote is on the cited page, or within two pages (page then corrected)
    verified=False anything else; verify_note says why
    """
    out = []
    for it in items:
        page, how = find_quote(it.quote, pages, it.page)
        if page is None:
            out.append(replace(it, verified=False, verify_note=how))
        else:
            out.append(replace(it, page=page, verified=True, verify_note=how))
    return out


# ---------------------------------------------------------------- cache
_DEFAULT = "default"     # resolved to CACHE_DIR at call time, so tests can repoint it


def _dir(cache_dir) -> Optional[Path]:
    return CACHE_DIR if cache_dir == _DEFAULT else cache_dir


def cache_path(doc_type: str, pdf_bytes: bytes, cache_dir=_DEFAULT) -> Path:
    return _dir(cache_dir) / f"{hashlib.sha256(pdf_bytes).hexdigest()}.{doc_type}.json"


def cached(doc_type: str, pdf_bytes: bytes, cache_dir=_DEFAULT) -> bool:
    return _dir(cache_dir) is not None and cache_path(doc_type, pdf_bytes, cache_dir).exists()


def _read_cache(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data.get("lines"), list) else None


def _write_cache(path: Path, doc_type: str, pdf_bytes: bytes, model: str, pages: int,
                 lines: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"doc_type": doc_type, "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
              "model": model, "pages": pages,
              "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "note": "Raw model output. Citations are re-verified in code on every load.",
              "lines": lines}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- agents
def _ask(doc_type: str, pages: Sequence[str], pdf_bytes: bytes,
         model: Optional[str]) -> Tuple[list, str]:
    """The model's raw lines for one document: one call per page with text, merged in code."""
    if not any(p.strip() for p in pages):          # no text layer: one call with the PDF itself
        prompt = (AGENTS[doc_type] + "\n\nThe document is attached (it has no text layer); "
                  "'page' is the PDF page number.")
        data, model = llm.generate_json(prompt, RESPONSE_SCHEMA, system=SYSTEM, model=model,
                                        pdf_bytes=pdf_bytes)
        return (data.get("lines", []) if isinstance(data, dict) else []), model
    merged, seen = [], set()
    for page_no, text in enumerate(pages, 1):
        if not text.strip():
            continue
        data, model = llm.generate_json(_prompt(doc_type, page_no, text, len(pages)),
                                        RESPONSE_SCHEMA, system=SYSTEM, model=model)
        for raw in (data.get("lines", []) if isinstance(data, dict) else []):
            if not isinstance(raw, dict):
                continue
            raw = dict(raw)
            if not raw.get("page"):
                raw["page"] = page_no
            key = normalise(raw.get("quote", "")) or None
            if key and key in seen:                 # the same row returned twice
                continue
            seen.add(key)
            merged.append(raw)
    for n, raw in enumerate(merged, 1):            # one numbering across the document
        raw["line_no"] = n
    return merged, model or ""


def extract(doc_type: str, pdf_bytes: bytes, model: Optional[str] = None,
            cache_dir=_DEFAULT) -> Tuple[List[LineItem], str]:
    """Run one agent on one PDF. Returns (verified LineItems, model used).

    A cached answer for this exact file is used without calling the API. cache_dir=None
    disables the cache.
    """
    if doc_type not in DOC_TYPES:
        raise ValueError(f"unknown document type {doc_type!r}")
    pages = page_texts(pdf_bytes)
    path = cache_path(doc_type, pdf_bytes, cache_dir) if _dir(cache_dir) else None
    record = _read_cache(path) if path and path.exists() else None
    if record:
        raw_lines, used = record["lines"], record.get("model", "")
    else:
        raw_lines, used = _ask(doc_type, pages, pdf_bytes, model)
        if path:
            _write_cache(path, doc_type, pdf_bytes, used, len(pages), raw_lines)
    items = [coerce_line_item(raw, doc_type, i) for i, raw in enumerate(raw_lines, 1)]
    return verify_citations([it for it in items if it], pages), used


def extract_all(pdfs: dict, model: Optional[str] = None,
                cache_dir=_DEFAULT) -> Tuple[List[LineItem], str]:
    """{'invoice': bytes, 'packing_list': bytes, 'bill_of_lading': bytes} -> all lines.

    One document at a time, one page at a time; llm paces the calls.
    """
    lines: List[LineItem] = []
    used = ""
    for doc_type in DOC_TYPES:
        if pdfs.get(doc_type):
            items, got = extract(doc_type, pdfs[doc_type], model=model or (used or None),
                                 cache_dir=cache_dir)
            used = used or got
            lines += items
    return lines, used
