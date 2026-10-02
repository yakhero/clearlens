"""Tests for the duty engine. These are the numbers the pitch claims, so they are tested."""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import duty  # noqa: E402
from core.schemas import D, coerce_line_item  # noqa: E402

CIF = Decimal("10000000")
KW = dict(importer_status="commercial_filer", st_rate=Decimal("0.18"), acd_rate=Decimal("0.02"))


def test_correct_code_cascade():
    """CD 3%: the worked example in the PRD, computed to the rupee."""
    r = duty.compute("0703.2000", CIF, Decimal("0.03"), **KW)
    assert r.cd_amount == Decimal("300000.00")
    assert r.acd_amount == Decimal("200000.00")
    assert r.duty_paid_value == Decimal("10500000.00")
    assert r.sales_tax == Decimal("1890000.00")
    assert r.wht == Decimal("681450.00")
    assert r.total_taxes == Decimal("3071450.00")
    assert r.landed_cost == Decimal("13071450.00")


def test_wrong_code_cascade():
    """CD 20% on the same consignment."""
    r = duty.compute("0712.9000", CIF, Decimal("0.20"), **KW)
    assert r.cd_amount == Decimal("2000000.00")
    assert r.duty_paid_value == Decimal("12200000.00")
    assert r.sales_tax == Decimal("2196000.00")
    assert r.wht == Decimal("791780.00")
    assert r.total_taxes == Decimal("5187780.00")


def test_misclassification_compounds():
    """A 17-point rate gap must produce a larger cost gap, because tax sits on tax."""
    cmp = duty.compare_codes(CIF, ("0703.2000", Decimal("0.03")),
                             ("0712.9000", Decimal("0.20")), **KW)
    assert cmp["difference"] == Decimal("2116330.00")
    assert cmp["difference_pct_of_cif"] == Decimal("21.16")
    rate_gap = Decimal("20") - Decimal("3")
    assert cmp["difference_pct_of_cif"] > rate_gap   # the whole point of the product


def test_non_filer_pays_more():
    filer = duty.compute("0703.2000", CIF, Decimal("0.03"), **KW)
    non_filer = duty.compute("0703.2000", CIF, Decimal("0.03"),
                             importer_status="commercial_non_filer",
                             st_rate=Decimal("0.18"), acd_rate=Decimal("0.02"))
    assert non_filer.total_taxes > filer.total_taxes


def test_regulatory_duty_feeds_the_sales_tax_base():
    without_rd = duty.compute("X", CIF, Decimal("0.03"), **KW)
    with_rd = duty.compute("X", CIF, Decimal("0.03"), rd_rate=Decimal("0.10"), **KW)
    extra_rd = Decimal("1000000.00")
    assert with_rd.rd_amount == extra_rd
    # sales tax must rise too, not just the duty line
    assert with_rd.sales_tax > without_rd.sales_tax


def test_customs_value_applies_fx_and_freight():
    # (50,000 + 3,000 + 500) USD x 280 = 53,500 x 280 = 14,980,000 PKR
    cif = duty.customs_value(Decimal("50000"), Decimal("3000"), Decimal("500"), Decimal("280"))
    assert cif == Decimal("14980000.00")


def test_zero_cif_does_not_divide_by_zero():
    r = duty.compute("X", Decimal("0"), Decimal("0.03"), **KW)
    assert r.total_taxes == Decimal("0.00") and r.effective_rate_pct == Decimal("0.00")


def test_money_is_never_float():
    r = duty.compute("X", CIF, Decimal("0.03"), **KW)
    assert all(isinstance(v, Decimal) for v in
               (r.cd_amount, r.sales_tax, r.wht, r.total_taxes, r.landed_cost))


def test_decimal_parser_survives_model_output():
    assert D("1,250.50") == Decimal("1250.50")
    assert D("USD 1250") == Decimal("1250")
    assert D(None) == Decimal("0")
    assert D("not a number") == Decimal("0")


def test_coerce_line_item_rejects_junk_and_clamps():
    assert coerce_line_item({"description": ""}, "invoice", 1) is None
    item = coerce_line_item(
        {"description": "Fresh garlic in 10kg mesh bags", "quantity": "500",
         "unit_price": "18.50", "value": "9,250", "page": "x", "incoterm": "nonsense",
         "source_doc": "hacked"}, "invoice", 3)
    assert item.quantity == Decimal("500") and item.value == Decimal("9250")
    assert item.page == 1 and item.incoterm == "" and item.source_doc == "invoice"
    assert item.verified is False      # only code may set this
