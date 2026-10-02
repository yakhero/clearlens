"""The duty engine. Pure arithmetic, Decimal only, no model anywhere near it.

Pakistani import levies cascade: sales tax is charged on top of the duties, and withholding
tax on top of that. So a wrong tariff code does not cost the rate difference — it compounds.
That cascade is the reason this file exists and the reason no LLM is allowed to compute it.
"""
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

from core.schemas import D, DutyResult

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "rates.json"
TWO = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Round to paisa, half up. Banks round this way and so do customs."""
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


def load_rates(path: Optional[Path] = None) -> dict:
    return json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))


def customs_value(invoice_value: Decimal, freight: Decimal, insurance: Decimal,
                  fx_rate: Decimal) -> Decimal:
    """CIF in PKR. Everything downstream hangs off this one number."""
    return money((D(invoice_value) + D(freight) + D(insurance)) * D(fx_rate))


def compute(pct_code: str, cif_pkr: Decimal, cd_rate: Decimal, *,
            importer_status: str = "commercial_filer",
            rd_rate: Decimal = Decimal("0"),
            st_rate: Optional[Decimal] = None,
            acd_rate: Optional[Decimal] = None,
            rates: Optional[dict] = None) -> DutyResult:
    """The full cascade for one line.

    cd, acd, rd   are charged on CIF
    sales tax     is charged on CIF + cd + acd + rd   (the duty-paid value)
    withholding   is charged on duty-paid value + sales tax
    """
    cfg = rates or load_rates()
    cif = money(D(cif_pkr))
    cd_r, rd_r = D(cd_rate), D(rd_rate)
    acd_r = D(acd_rate) if acd_rate is not None else D(cfg["additional_customs_duty"]["rate"])
    st_r = D(st_rate) if st_rate is not None else D(cfg["sales_tax"]["standard_rate"])
    wht_table = cfg["withholding_income_tax"]["rates_by_importer_status"]
    wht_r = D(wht_table.get(importer_status, wht_table["commercial_filer"]))

    cd = money(cif * cd_r)
    acd = money(cif * acd_r)
    rd = money(cif * rd_r)
    dpv = money(cif + cd + acd + rd)
    sales_tax = money(dpv * st_r)
    wht = money((dpv + sales_tax) * wht_r)
    total = money(cd + acd + rd + sales_tax + wht)

    return DutyResult(
        pct_code=pct_code, cif_pkr=cif,
        cd_rate=cd_r, acd_rate=acd_r, rd_rate=rd_r, st_rate=st_r, wht_rate=wht_r,
        cd_amount=cd, acd_amount=acd, rd_amount=rd,
        duty_paid_value=dpv, sales_tax=sales_tax, wht=wht,
        total_taxes=total, landed_cost=money(cif + total),
        effective_rate_pct=money(total / cif * 100) if cif else Decimal("0.00"),
    )


def compare_codes(cif_pkr: Decimal, option_a: tuple, option_b: tuple, **kwargs) -> dict:
    """What the alternative tariff code would cost. (code, cd_rate) in, difference out.

    This is the feature that makes the risk visible instead of theoretical.
    """
    a = compute(option_a[0], cif_pkr, option_a[1], **kwargs)
    b = compute(option_b[0], cif_pkr, option_b[1], **kwargs)
    return {
        "a": a, "b": b,
        "difference": money(b.total_taxes - a.total_taxes),
        "difference_pct_of_cif": money((b.total_taxes - a.total_taxes) / D(cif_pkr) * 100)
        if D(cif_pkr) else Decimal("0.00"),
    }
