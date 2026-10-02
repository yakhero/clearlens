"""Tests for the citation verifier and the extraction agents. No network, no API key: the
model is replaced by a stand-in, and the verifier is pure code."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import demo_docs, extract, llm, reconcile  # noqa: E402
from core.schemas import LineItem  # noqa: E402

PDFS = demo_docs.load_demo_pdfs()
PAGES = {d: extract.page_texts(b) for d, b in PDFS.items()}


def row_on(doc: str, needle: str, page: int = 1) -> str:
    """The goods row exactly as the PDF's text layer prints it."""
    return next(l.strip() for l in PAGES[doc][page - 1].splitlines() if needle in l)


def item(quote, page=1, doc="invoice", desc="Fresh garlic"):
    return LineItem(source_doc=doc, line_no=1, description=desc, quote=quote, page=page)


# ---------------------------------------------------------------- the verifier
def test_real_quote_on_the_cited_page_is_verified():
    quote = row_on("invoice", "Normal White")
    [out] = extract.verify_citations([item(quote)], PAGES["invoice"])
    assert out.verified and out.page == 1
    assert out.verify_note == "found on the cited page"


def test_invented_quote_is_rejected():
    """Plausible, well-formed, and not in the document: the model made it up."""
    invented = "Fresh Garlic, Normal White, size 6.0cm & up, packed in 20kg mesh bags 1,250 BAGS"
    [out] = extract.verify_citations([item(invented)], PAGES["invoice"])
    assert out.verified is False
    assert "not found" in out.verify_note


def test_quote_with_one_number_changed_is_rejected():
    """The packing list says 1,180 bags. A quote 'correcting' it to 1,200 must fail."""
    real = row_on("packing_list", "NORMAL WHITE")
    assert "1,180 BAGS" in real
    [out] = extract.verify_citations([item(real.replace("1,180", "1,200"), doc="packing_list")],
                                     PAGES["packing_list"])
    assert out.verified is False


def test_quote_from_another_document_is_rejected():
    quote = row_on("bill_of_lading", "PURE WHITE")
    [out] = extract.verify_citations([item(quote)], PAGES["invoice"])
    assert out.verified is False


def test_wrong_page_within_two_pages_is_corrected():
    """The B/L goods are on page 1; a citation of page 2 is fixed, not flagged."""
    quote = row_on("bill_of_lading", "PURE WHITE")
    [out] = extract.verify_citations([item(quote, page=2, doc="bill_of_lading")],
                                     PAGES["bill_of_lading"])
    assert out.verified and out.page == 1
    assert "page corrected" in out.verify_note


def test_page_past_the_end_but_within_two_is_corrected():
    quote = row_on("invoice", "Pure White")
    [out] = extract.verify_citations([item(quote, page=3)], PAGES["invoice"])
    assert out.verified and out.page == 1


def test_quote_more_than_two_pages_away_is_flagged_not_moved():
    pages = ["cover sheet", "index", "notes", "annex", "1 Fresh garlic normal white 1,200 BAGS"]
    [out] = extract.verify_citations([item("Fresh garlic normal white 1,200 BAGS", page=1)],
                                     pages)
    assert out.verified is False and out.page == 1
    assert "page 5, more than 2 pages" in out.verify_note


def test_nearest_page_wins_when_the_quote_repeats():
    pages = ["Fresh garlic normal white", "x", "y", "Fresh garlic normal white"]
    [out] = extract.verify_citations([item("Fresh garlic normal white", page=3)], pages)
    assert out.verified and out.page == 4


def test_whitespace_case_and_typography_do_not_matter():
    pages = ["1   Fresh Garlic – Normal\nWhite,  “A” grade   1,200 BAGS"]
    [out] = extract.verify_citations(
        [item('1 fresh garlic - normal white, "a" grade 1,200 bags')], pages)
    assert out.verified


def test_empty_or_trivial_quote_is_flagged():
    for quote in ("", "1,200", "   "):
        [out] = extract.verify_citations([item(quote)], PAGES["invoice"])
        assert out.verified is False and "too short" in out.verify_note


def test_pdf_without_text_layer_is_flagged():
    [out] = extract.verify_citations([item("Fresh Garlic, Normal White")], ["", "  "])
    assert out.verified is False and "no text layer" in out.verify_note


def test_verifier_overrides_a_model_that_claims_verified():
    claimed = LineItem(source_doc="invoice", line_no=1, description="x", page=1,
                       quote="nothing like this is printed anywhere", verified=True)
    [out] = extract.verify_citations([claimed], PAGES["invoice"])
    assert out.verified is False


# ---------------------------------------------------------------- the generated PDFs
def test_generated_pdfs_carry_the_two_planted_mismatches():
    inv, pl, bl = (PAGES[d][0] for d in ("invoice", "packing_list", "bill_of_lading"))
    normal_inv, normal_pl = row_on("invoice", "Normal White"), row_on("packing_list", "NORMAL")
    assert "1,200 BAGS" in normal_inv and "1,180 BAGS" in normal_pl
    flakes_inv, flakes_pl = row_on("invoice", "Flakes"), row_on("packing_list", "FLAKES")
    assert "3,000" in flakes_inv and "2,950.00" in flakes_pl
    assert "1,200 BAGS" in row_on("bill_of_lading", "NORMAL WHITE")      # B/L sides with invoice
    assert "FICTIONAL DEMO DOCUMENT" in inv and "FICTIONAL DEMO DOCUMENT" in pl
    assert "FICTIONAL DEMO DOCUMENT" in bl
    assert len(PAGES["bill_of_lading"]) == 2                            # terms on the back


def test_generated_pdfs_match_mock_json():
    """Regenerating from mock.json gives the same text as the committed PDFs."""
    data = __import__("json").loads(demo_docs.MOCK_PATH.read_text(encoding="utf-8"))
    for doc, build in demo_docs.BUILDERS.items():
        assert extract.page_texts(build(data)) == PAGES[doc], doc


# ---------------------------------------------------------------- agents, with a stand-in model
def fake_model(answers):
    """Replace llm.generate_json: return canned JSON per document type, record the calls."""
    calls = []

    def generate_json(prompt, schema, **kw):
        calls.append({"prompt": prompt, "schema": schema, **kw})
        doc = next(d for d, intro in extract.AGENTS.items() if prompt.startswith(intro))
        return {"lines": answers[doc]}, "gemini-test-flash"
    return generate_json, calls


def _rows(doc, specs):
    """specs: (needle, fields) -> model-style answer quoting the real row."""
    return [{"line_no": i, "page": 1, "quote": row_on(doc, needle), **fields}
            for i, (needle, fields) in enumerate(specs, 1)]


ANSWERS = {
    "invoice": _rows("invoice", [
        ("Normal White", dict(description="Fresh Garlic, Normal White, size 5.0cm & up, packed "
                              "in 10kg mesh bags", quantity="1,200", unit="BAGS",
                              unit_price="11.00", currency="USD", value="13,200.00",
                              net_weight_kg="12,000", gross_weight_kg="12,360",
                              origin="China", incoterm="FOB")),
        ("Pure White", dict(description="Fresh Garlic, Pure White, size 5.5cm & up, packed in "
                            "10kg mesh bags", quantity="800", unit="BAGS", unit_price="12.50",
                            currency="USD", value="10,000.00", net_weight_kg="8,000",
                            gross_weight_kg="8,240", origin="China", incoterm="FOB")),
        ("Flakes", dict(description="Dehydrated Garlic Flakes, Grade A, 20kg cartons",
                        quantity="150", unit="CTNS", unit_price="36.00", currency="USD",
                        value="5,400.00", net_weight_kg="3,000", gross_weight_kg="3,180",
                        origin="China", incoterm="FOB")),
    ]),
    "packing_list": _rows("packing_list", [
        ("PURE WHITE", dict(description="GARLIC FRESH PURE WHITE 5.5CM UP (10KG MESH BAGS)",
                            quantity="800", unit="BAGS", net_weight_kg="8,000.00",
                            gross_weight_kg="8,240.00", origin="CHINA")),
        ("FLAKES", dict(description="GARLIC FLAKES DEHYDRATED GRADE A (20KG CTN)",
                        quantity="150", unit="CTNS", net_weight_kg="2,950.00",
                        gross_weight_kg="3,180.00", origin="CHINA")),
        ("NORMAL WHITE", dict(description="GARLIC FRESH NORMAL WHITE 5.0CM UP (10KG MESH BAGS)",
                              quantity="1,180", unit="BAGS", net_weight_kg="12,000.00",
                              gross_weight_kg="12,360.00", origin="CHINA")),
    ]),
    "bill_of_lading": _rows("bill_of_lading", [
        ("NORMAL WHITE", dict(description="FRESH GARLIC NORMAL WHITE IN MESH BAGS",
                              quantity="1,200", unit="BAGS", gross_weight_kg="12,360.000",
                              origin="P.R. CHINA")),
        ("PURE WHITE", dict(description="FRESH GARLIC PURE WHITE IN MESH BAGS", quantity="800",
                            unit="BAGS", gross_weight_kg="8,240.000", origin="P.R. CHINA")),
        ("DEHYDRATED", dict(description="DEHYDRATED GARLIC FLAKES IN CARTONS", quantity="150",
                            unit="CARTONS", gross_weight_kg="3,180.000", origin="P.R. CHINA")),
    ]),
}


def with_fake(answers, fn):
    real = llm.generate_json
    gen, calls = fake_model(answers)
    llm.generate_json = gen
    try:
        return fn(), calls
    finally:
        llm.generate_json = real


def test_pipeline_from_pdfs_to_reconciler_finds_the_planted_mismatches():
    (lines, model), calls = with_fake(ANSWERS, lambda: extract.extract_all(PDFS))
    assert model == "gemini-test-flash" and len(calls) == 3
    assert all(l.verified for l in lines), [l.verify_note for l in lines if not l.verified]
    by_doc = {d: [l for l in lines if l.source_doc == d] for d in extract.AGENTS}
    _, ds = reconcile.reconcile(by_doc["invoice"], by_doc["packing_list"],
                                by_doc["bill_of_lading"])
    found = {d.field_name: d for d in ds}
    assert sorted(found) == ["line_1.quantity", "line_3.net_weight_kg"]
    assert found["line_1.quantity"].severity == "blocking"
    assert found["line_3.net_weight_kg"].severity == "advisory"
    assert by_doc["invoice"][0].value == Decimal("13200.00")


def test_agent_sends_page_text_and_schema_not_the_pdf_when_text_exists():
    _, calls = with_fake(ANSWERS, lambda: extract.extract("invoice", PDFS["invoice"]))
    call = calls[0]
    assert "=== PAGE 1 ===" in call["prompt"] and "13,200.00" in call["prompt"]
    assert call["schema"] is extract.RESPONSE_SCHEMA
    assert call.get("pdf_bytes") is None
    assert "Never calculate" in call["system"]


def test_agent_flags_the_line_whose_quote_the_model_invented():
    answers = {k: [dict(r) for r in v] for k, v in ANSWERS.items()}
    answers["invoice"][1]["quote"] = "2 Fresh Garlic, Pure White 800 BAGS 12.00 9,600.00"
    (items, _), _ = with_fake(answers, lambda: extract.extract("invoice", PDFS["invoice"]))
    assert [i.verified for i in items] == [True, False, True]
    assert "not found" in items[1].verify_note


def test_agent_output_is_coerced_to_the_frozen_schema():
    answers = {"invoice": [{"line_no": "1", "description": "Fresh Garlic", "quantity": "1,200",
                            "value": "USD 13,200.00", "page": "1", "incoterm": "nonsense",
                            "verified": True, "quote": row_on("invoice", "Normal White")},
                           {"description": "", "page": 1, "quote": "x"}]}
    (items, _), _ = with_fake(answers, lambda: extract.extract("invoice", PDFS["invoice"]))
    assert len(items) == 1                       # the empty description is dropped
    it = items[0]
    assert isinstance(it, LineItem) and it.source_doc == "invoice"
    assert it.quantity == Decimal("1200") and it.value == Decimal("13200.00")
    assert it.incoterm == "" and it.page == 1 and it.verified is True   # set by the verifier


def test_unknown_document_type_is_refused():
    try:
        extract.extract("certificate_of_origin", PDFS["invoice"])
    except ValueError:
        return
    raise AssertionError("an unknown document type must be refused")
