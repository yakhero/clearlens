"""Tests for the reconciler: matching across documents, and what counts as blocking."""
import itertools
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import reconcile as rc  # noqa: E402
from core.schemas import Discrepancy, LineItem, coerce_line_item  # noqa: E402

MOCK = Path(__file__).resolve().parents[1] / "data" / "consignments" / "mock.json"


def item(doc, n, desc, **kw):
    kw = {k: (Decimal(v) if k in rc.NUMERIC_FIELDS else v) for k, v in kw.items()}
    return LineItem(source_doc=doc, line_no=n, description=desc, **kw)


def mock_docs():
    data = json.loads(MOCK.read_text(encoding="utf-8"))
    items = [coerce_line_item(raw, raw["source_doc"], i) for i, raw in enumerate(data["lines"], 1)]
    return [[x for x in items if x.source_doc == d] for d in ("invoice", "packing_list",
                                                              "bill_of_lading")]


def by_field(discrepancies):
    return {d.field_name: d for d in discrepancies}


def weights(inv_kg, pl_kg, field="net_weight_kg"):
    inv = [item("invoice", 1, "Fresh garlic", **{field: inv_kg})]
    pl = [item("packing_list", 1, "Fresh garlic", **{field: pl_kg})]
    return by_field(rc.reconcile(inv, pl, [])[1])


# ---------------------------------------------------------------- the demo consignment
def test_mock_has_exactly_the_two_planted_mismatches():
    matched, ds = rc.reconcile(*mock_docs())
    assert len(matched) == 3
    found = by_field(ds)
    assert sorted(found) == ["line_1.quantity", "line_3.net_weight_kg"]
    qty = found["line_1.quantity"]
    assert qty.severity == "blocking"
    assert qty.values == {"invoice": "1200", "packing_list": "1180", "bill_of_lading": "1200"}
    nw = found["line_3.net_weight_kg"]
    assert nw.severity == "advisory"                      # 50 / 3000 = 1.67%
    assert nw.values == {"invoice": "3000", "packing_list": "2950"}
    assert "1.67%" in nw.note
    assert all(isinstance(d, Discrepancy) and d.resolved_value is None for d in ds)


def test_mock_matches_by_description_not_order():
    """The packing list lists the goods in a different order and different words."""
    matched, _ = rc.reconcile(*mock_docs())
    pairs = {m.invoice.line_no: m.packing_list.line_no for m in matched}
    assert pairs == {1: 3, 2: 1, 3: 2}
    assert all(m.bill_of_lading.line_no == m.invoice.line_no for m in matched)


def test_any_order_of_packing_list_and_bl_gives_the_same_answer():
    inv, pl, bl = mock_docs()
    for pl_order in itertools.permutations(pl):
        for bl_order in itertools.permutations(bl):
            matched, ds = rc.reconcile(inv, list(pl_order), list(bl_order))
            assert sorted(d.field_name for d in ds) == ["line_1.quantity",
                                                        "line_3.net_weight_kg"]
            for m in matched:
                assert m.bill_of_lading.quantity == m.invoice.quantity
                assert m.packing_list.net_weight_kg in (m.invoice.net_weight_kg, Decimal("2950"))


def test_near_identical_descriptions_are_told_apart():
    """Only 'normal' vs 'pure' and the size separate these; position is reversed."""
    inv = [item("invoice", 1, "Fresh garlic normal white 5.0cm mesh bags"),
           item("invoice", 2, "Fresh garlic pure white 5.5cm mesh bags")]
    pl = [item("packing_list", 1, "GARLIC FRESH PURE WHITE 5.5CM MESH BAGS"),
          item("packing_list", 2, "GARLIC FRESH NORMAL WHITE 5.0CM MESH BAGS")]
    matched, _ = rc.reconcile(inv, pl, [])
    assert {m.invoice.line_no: m.packing_list.line_no for m in matched} == {1: 2, 2: 1}


# ---------------------------------------------------------------- the 2% weight tolerance
def test_weight_difference_under_two_percent_is_advisory():
    d = weights("1000", "981")["line_1.net_weight_kg"]       # 1.90%
    assert d.severity == "advisory"


def test_weight_difference_just_under_two_percent_is_advisory():
    d = weights("10000", "9801")["line_1.net_weight_kg"]     # 1.99%
    assert d.severity == "advisory"


def test_weight_difference_of_exactly_two_percent_is_blocking():
    d = weights("1000", "980")["line_1.net_weight_kg"]       # 2.00%: not "under 2%"
    assert d.severity == "blocking"


def test_weight_difference_over_two_percent_is_blocking():
    d = weights("1000", "970")["line_1.net_weight_kg"]       # 3.00%
    assert d.severity == "blocking"


def test_tolerance_applies_to_gross_weight_and_is_relative_to_the_larger_value():
    assert weights("12360", "12200", "gross_weight_kg")[
        "line_1.gross_weight_kg"].severity == "advisory"       # 1.29%
    assert weights("980", "1000", "gross_weight_kg")[
        "line_1.gross_weight_kg"].severity == "blocking"       # 20 / 1000, either way round


def test_equal_weights_raise_nothing():
    assert weights("3000", "3000.00") == {}


# ---------------------------------------------------------------- other fields
def test_quantity_and_value_mismatches_are_blocking():
    inv = [item("invoice", 1, "Garlic flakes", quantity="150", unit="CTN", value="5400",
                currency="USD")]
    pl = [item("packing_list", 1, "Garlic flakes", quantity="149", unit="CTN", value="5364",
               currency="USD")]
    found = by_field(rc.reconcile(inv, pl, [])[1])
    assert found["line_1.quantity"].severity == "blocking"
    assert found["line_1.value"].severity == "blocking"


def test_unit_price_mismatch_alone_is_advisory():
    inv = [item("invoice", 1, "Garlic flakes", unit_price="36.00", currency="USD")]
    pl = [item("packing_list", 1, "Garlic flakes", unit_price="36.50", currency="USD")]
    assert by_field(rc.reconcile(inv, pl, [])[1])["line_1.unit_price"].severity == "advisory"


def test_origin_mismatch_is_blocking_but_aliases_agree():
    inv = [item("invoice", 1, "Garlic", origin="China")]
    pl = [item("packing_list", 1, "Garlic", origin="Vietnam")]
    bl = [item("bill_of_lading", 1, "Garlic", origin="P.R. CHINA")]
    found = by_field(rc.reconcile(inv, pl, bl)[1])
    assert found["line_1.origin"].severity == "blocking"
    assert found["line_1.origin"].values == {"invoice": "China", "packing_list": "Vietnam",
                                             "bill_of_lading": "P.R. CHINA"}
    pl[0].origin = "CHINA"
    assert rc.reconcile(inv, pl, bl)[1] == []


def test_fields_a_document_leaves_blank_are_not_mismatches():
    """A B/L carries no prices; zero means 'not stated', not 'free'."""
    inv = [item("invoice", 1, "Garlic", quantity="10", unit_price="11", value="110",
                currency="USD")]
    bl = [item("bill_of_lading", 1, "Garlic", quantity="10")]
    pl = [item("packing_list", 1, "Garlic", quantity="10")]
    assert rc.reconcile(inv, pl, bl)[1] == []


def test_unit_spellings_agree_but_different_units_block_the_quantity():
    inv = [item("invoice", 1, "Garlic", quantity="1200", unit="BAGS")]
    pl = [item("packing_list", 1, "Garlic", quantity="1200", unit="bag")]
    assert rc.reconcile(inv, pl, [])[1] == []
    pl = [item("packing_list", 1, "Garlic", quantity="12000", unit="KGS")]
    found = by_field(rc.reconcile(inv, pl, [])[1])
    assert found["line_1.unit"].severity == "blocking"
    assert "line_1.quantity" not in found            # 1200 bags vs 12000 kg is not comparable


def test_different_currencies_block_and_skip_value_comparison():
    inv = [item("invoice", 1, "Garlic", value="100", currency="USD")]
    pl = [item("packing_list", 1, "Garlic", value="28150", currency="PKR")]
    found = by_field(rc.reconcile(inv, pl, [])[1])
    assert found["line_1.currency"].severity == "blocking"
    assert "line_1.value" not in found


# ---------------------------------------------------------------- lines on one document only
def test_line_missing_from_packing_list_is_blocking():
    inv = [item("invoice", 1, "Fresh garlic normal white"),
           item("invoice", 2, "Dehydrated garlic flakes")]
    pl = [item("packing_list", 1, "Fresh garlic normal white")]
    matched, ds = rc.reconcile(inv, pl, [])
    assert matched[1].packing_list is None
    assert by_field(ds)["line_2.presence"].severity == "blocking"


def test_unrelated_goods_are_not_forced_together():
    inv = [item("invoice", 1, "Fresh garlic normal white mesh bags")]
    pl = [item("packing_list", 1, "Stainless steel kitchen sinks")]
    matched, ds = rc.reconcile(inv, pl, [])
    assert len(matched) == 2
    assert {d.field_name for d in ds} == {"line_1.presence", "line_2.presence"}
    assert all(d.severity == "blocking" for d in ds)


def test_line_missing_from_bl_is_advisory_and_an_empty_bl_is_not_flagged():
    inv = [item("invoice", 1, "Fresh garlic"), item("invoice", 2, "Garlic flakes")]
    pl = [item("packing_list", 1, "Fresh garlic"), item("packing_list", 2, "Garlic flakes")]
    bl = [item("bill_of_lading", 1, "Fresh garlic")]
    found = by_field(rc.reconcile(inv, pl, bl)[1])
    assert found["line_2.presence"].severity == "advisory"
    assert rc.reconcile(inv, pl, [])[1] == []


# ---------------------------------------------------------------- after human gate 1
def test_split_field():
    assert rc.split_field("line_12.net_weight_kg") == (12, "net_weight_kg")


def test_gate_one_closes_only_when_every_blocking_item_is_resolved():
    matched, ds = rc.reconcile(*mock_docs())
    assert [d.field_name for d in rc.unresolved_blocking(ds)] == ["line_1.quantity"]
    by_field(ds)["line_1.quantity"].resolved_value = "1200"
    assert rc.unresolved_blocking(ds) == []          # the advisory one does not hold the gate


def test_resolved_lines_apply_the_human_choice():
    matched, ds = rc.reconcile(*mock_docs())
    found = by_field(ds)
    found["line_1.quantity"].resolved_value = "1180"
    found["line_3.net_weight_kg"].resolved_value = "2950"
    lines = rc.resolved_lines(matched, ds)
    assert [l.line_no for l in lines] == [1, 2, 3]
    assert lines[0].quantity == Decimal("1180")
    assert lines[0].value == Decimal("13200.00")     # value is not silently recomputed
    assert lines[2].net_weight_kg == Decimal("2950")
    assert lines[1].description.startswith("Fresh Garlic, Pure White")   # invoice wording
    assert all(l.source_doc == "invoice" and not l.verified for l in lines)


def test_resolved_lines_fill_blanks_from_other_documents():
    inv = [item("invoice", 1, "Garlic", value="110", currency="USD")]
    pl = [item("packing_list", 1, "Garlic", net_weight_kg="100", origin="China")]
    line = rc.resolved_lines(*rc.reconcile(inv, pl, []))[0]
    assert line.net_weight_kg == Decimal("100") and line.origin == "China"
    assert line.value == Decimal("110")
