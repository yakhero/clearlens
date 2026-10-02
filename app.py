"""ClearLens — Streamlit UI. Runs on mock data until the extraction agents land.

    streamlit run app.py

Five steps, two human gates. Nothing here computes money: the cascade comes from core/duty.py,
rates come from the parsed tariff, and every choice that matters is made by the person at a
gate, not by the screen.
"""
import json
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

from core import duty, reconcile, tariff
from core.schemas import (D, DOC_TYPES, IMPORTER_STATUSES, Classification, Consignment,
                          coerce_line_item)

ROOT = Path(__file__).resolve().parent
MOCK_PATH = ROOT / "data" / "consignments" / "mock.json"

STEPS = ["1 · Documents", "2 · Reconcile", "3 · Gate 1: resolve", "4 · Gate 2: classify",
         "5 · Duty and landed cost"]
DOC_TITLES = {"invoice": "Commercial invoice", "packing_list": "Packing list",
              "bill_of_lading": "Bill of lading"}
COLUMNS = {"quantity": "Qty", "unit": "Unit", "unit_price": "Unit price", "currency": "Cur",
           "value": "Value", "net_weight_kg": "N.W. kg", "gross_weight_kg": "G.W. kg",
           "origin": "Origin"}
SEVERITY_STYLE = {"blocking": "background-color: rgba(220, 38, 38, 0.28); font-weight: 600",
                  "advisory": "background-color: rgba(217, 119, 6, 0.25)"}
SEARCH_STOPWORDS = {"size", "packed", "pack", "packing", "bags", "bag", "mesh", "grade",
                    "cartons", "carton", "with", "from", "into", "each", "kgs"}


# ---------------------------------------------------------------- data
def load_consignment(path: Path = MOCK_PATH):
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = [coerce_line_item(raw, raw.get("source_doc", ""), i)
             for i, raw in enumerate(data.get("lines", []), start=1)]
    c = Consignment(reference=data.get("reference", ""),
                    importer_status=data.get("importer_status", "commercial_filer"),
                    fx_rate=D(data.get("fx_rate"), "1"), freight=D(data.get("freight")),
                    insurance=D(data.get("insurance")), lines=[l for l in lines if l])
    return c, data.get("_meta", {}), data.get("_note", "")


def docs_of(c: Consignment) -> dict:
    return {d: [l for l in c.lines if l.source_doc == d] for d in DOC_TYPES}


def fmt(v) -> str:
    if isinstance(v, Decimal):
        return "" if v == 0 else f"{v:,}"
    return str(v or "")


def rupees(v: Decimal) -> str:
    return f"Rs {v:,.2f}"


def start(reset: bool = False):
    s = st.session_state
    if reset or "consignment" not in s:
        for k in list(s.keys()):
            del s[k]
        c, meta, note = load_consignment()
        docs = docs_of(c)
        matched, discrepancies = reconcile.reconcile(docs["invoice"], docs["packing_list"],
                                                     docs["bill_of_lading"])
        c.discrepancies = discrepancies
        s.consignment, s.meta, s.note, s.matched = c, meta, note, matched
        s.gate1_closed = False
        s.gate2 = {}            # line_no -> Classification, only once a person confirms it
        s.step = STEPS[0]


# ---------------------------------------------------------------- steps
def step_documents():
    s = st.session_state
    c, meta = s.consignment, s.meta
    st.subheader("Documents")
    st.caption("Mock data: these lines stand in for what the three extraction agents will read "
               "from the PDFs. " + s.note)
    cols = st.columns(4)
    cols[0].metric("Reference", c.reference)
    cols[1].metric("FX rate (USD → PKR)", fmt(c.fx_rate))
    cols[2].metric("Freight (USD)", fmt(c.freight))
    cols[3].metric("Insurance (USD)", fmt(c.insurance))
    if meta:
        st.dataframe(pd.DataFrame({"": list(meta.keys()), "value": list(meta.values())}),
                     hide_index=True)
    for doc, items in docs_of(c).items():
        st.markdown(f"**{DOC_TITLES[doc]}** — {len(items)} lines")
        rows = [{"#": it.line_no, "Description": it.description,
                 **{label: fmt(getattr(it, f)) for f, label in COLUMNS.items()}} for it in items]
        st.dataframe(pd.DataFrame(rows), hide_index=True)


def reconciliation_frame():
    s = st.session_state
    flagged = {}
    for d in s.consignment.discrepancies:
        n, name = reconcile.split_field(d.field_name)
        flagged[(n, name)] = d.severity
    rows, styles = [], []
    for m in s.matched:
        for doc in DOC_TYPES:
            it = getattr(m, doc)
            row = {"Line": m.line_no, "Document": DOC_TITLES[doc],
                   "Description": it.description if it else "— not on this document —"}
            style = {"Line": "", "Document": "", "Description": ""}
            if it is None and (m.line_no, "presence") in flagged:
                style["Description"] = SEVERITY_STYLE[flagged[(m.line_no, "presence")]]
            for f, label in COLUMNS.items():
                row[label] = fmt(getattr(it, f)) if it else ""
                sev = flagged.get((m.line_no, f))
                style[label] = SEVERITY_STYLE[sev] if sev and it and row[label] else ""
            rows.append(row)
            styles.append(style)
    df = pd.DataFrame(rows)
    css = pd.DataFrame(styles, columns=df.columns)
    return df.style.apply(lambda _: css, axis=None)


def step_reconcile():
    s = st.session_state
    ds = s.consignment.discrepancies
    blocking = [d for d in ds if d.severity == "blocking"]
    st.subheader("Reconciliation")
    st.caption("Lines are matched across documents by what they describe and roughly where "
               "they sit — not by line number. Red cells block; amber cells are advisory "
               "(weights within 2%).")
    cols = st.columns(3)
    cols[0].metric("Matched lines", len(s.matched))
    cols[1].metric("Blocking", len(blocking))
    cols[2].metric("Advisory", len(ds) - len(blocking))
    st.dataframe(reconciliation_frame(), hide_index=True)
    for d in ds:
        (st.error if d.severity == "blocking" else st.warning)(d.note)
    if not ds:
        st.success("All three documents agree.")


def step_gate1():
    s = st.session_state
    ds = s.consignment.discrepancies
    st.subheader("Human gate 1 — resolve each mismatch")
    st.caption("Pick the value that is right, or type the correct one. Blocking items must be "
               "settled before classification; advisory items may stay as the invoice has them.")
    if not ds:
        st.success("Nothing to resolve.")
    for d in ds:
        n, name = reconcile.split_field(d.field_name)
        label = reconcile.FIELD_LABELS.get(name, name)
        with st.container(border=True):
            st.markdown(f"**Line {n} — {label}** · `{d.severity}`")
            st.caption(d.note)
            if name == "presence":
                options = ["Keep the line", "Drop the line"]
                pick = st.radio("Decision", options, index=None, key=f"g1_{d.field_name}",
                                horizontal=True)
                d.resolved_value = pick
                continue
            by_value = {}
            for doc, v in d.values.items():
                by_value.setdefault(v, []).append(reconcile.DOC_LABELS[doc])
            options = [f"{v}  ({', '.join(docs)})" for v, docs in by_value.items()]
            options.append("Other value")
            pick = st.radio("Correct value", options, index=None, key=f"g1_{d.field_name}",
                            horizontal=True)
            if pick is None:
                d.resolved_value = None
            elif pick == "Other value":
                typed = st.text_input("Correct value", key=f"g1_other_{d.field_name}").strip()
                numeric = name in reconcile.NUMERIC_FIELDS
                if typed and numeric and D(typed) == 0:
                    st.error("Enter a number.")
                    typed = ""
                d.resolved_value = str(D(typed)) if typed and numeric else (typed or None)
            else:
                d.resolved_value = list(by_value)[options.index(pick)]

    left = reconcile.unresolved_blocking(ds)
    if left:
        s.gate1_closed = False          # reopening an item reopens the gate
        st.info(f"{len(left)} blocking item(s) still open.")
    if st.button("Confirm lines and close gate 1", type="primary", disabled=bool(left)):
        s.gate1_closed = True
    if s.gate1_closed:
        dropped = dropped_lines()
        lines = confirmed_lines()
        st.success(f"Gate 1 closed — {len(lines)} confirmed line(s)"
                   + (f", {len(dropped)} dropped." if dropped else "."))
        st.dataframe(pd.DataFrame([{"Line": l.line_no, "Description": l.description,
                                    **{lab: fmt(getattr(l, f)) for f, lab in COLUMNS.items()}}
                                   for l in lines]), hide_index=True)


def dropped_lines() -> set:
    return {reconcile.split_field(d.field_name)[0]
            for d in st.session_state.consignment.discrepancies
            if d.field_name.endswith(".presence") and d.resolved_value == "Drop the line"}


def confirmed_lines():
    s = st.session_state
    ds = [d for d in s.consignment.discrepancies if not d.field_name.endswith(".presence")]
    lines = reconcile.resolved_lines(s.matched, ds)
    return [l for l in lines if l.line_no not in dropped_lines()]


def tariff_candidates(description: str, limit: int = 15) -> list:
    """Codes whose text shares the most words with the line. A starting list, not an answer."""
    words = {w for w in description.lower().replace(",", " ").split()
             if w.isalpha() and len(w) >= 4 and w not in SEARCH_STOPWORDS}
    hits = {}
    for w in words:
        for row in tariff.search(w, limit=200):
            h = hits.setdefault(row["pct_code"], [row, 0, 0])
            h[1] += 1                                                   # anywhere in the path
            h[2] += w in row["description"].split(" > ")[-1].lower()    # in the line's own text
    ranked = sorted(hits.values(), key=lambda h: (-h[1], -h[2], h[0]["pct_code"]))
    return [row for row, _, _ in ranked[:limit]]


def rate_label(row: dict) -> str:
    if row.get("duty_type") == "specific":
        return row.get("specific_duty_text") or "specific duty"
    return f"CD {row['cd_rate']}%"


def step_gate2():
    s = st.session_state
    st.subheader("Human gate 2 — confirm a tariff code for every line")
    if not s.gate1_closed:
        st.warning("Close gate 1 first: the lines are not confirmed yet.")
        return
    st.caption("Codes and heading text come from the parsed FBR tariff (data/tariff/"
               "pct_codes.csv). Search matches tariff wording, so try the heading's own words "
               "when the trade name finds nothing (e.g. 'dried vegetables').")
    for line in confirmed_lines():
        n = line.line_no
        with st.container(border=True):
            st.markdown(f"**Line {n}** — {line.description}")
            query = st.text_input("Search the tariff (words or a code prefix)",
                                  key=f"g2_q_{n}", placeholder="leave empty for suggestions")
            rows = tariff.search(query, limit=25) if query.strip() else \
                tariff_candidates(line.description)
            if not rows:
                st.info("No tariff line contains all of those words.")
                continue
            chosen = s.gate2.get(n)
            if chosen and all(r["pct_code"] != chosen.pct_code for r in rows):
                rows = [tariff.lookup(chosen.pct_code)] + rows   # keep the confirmed code visible
            by_label = {f"{r['pct_code']} · {rate_label(r)} · "
                        f"{r['description'].split(' > ')[-1][:70]}": r for r in rows}
            labels = list(by_label)
            codes = [r["pct_code"] for r in by_label.values()]
            picked = st.selectbox(
                "Tariff code", labels, key=f"g2_code_{n}_{query.strip().lower()}",
                index=codes.index(chosen.pct_code) if chosen else 0)
            row = by_label[picked]
            code = row["pct_code"]
            st.markdown(f"> {row['description']}")
            st.caption(f"Chapter {row['chapter']} · heading {row['heading']} · {rate_label(row)}")
            if row.get("duty_type") == "specific":
                st.warning("This code carries a fixed rupee duty — enter it manually at step 5.")
            # Keyed by code: confirming one code never carries over to another.
            ok = st.checkbox(f"I confirm {code} for line {n}", key=f"g2_ok_{n}_{code}",
                             value=bool(chosen and chosen.pct_code == code))
            if ok:
                s.gate2[n] = Classification(
                    line_no=n, pct_code=code, heading_text=row["description"],
                    reasoning="Chosen from the parsed tariff and confirmed by a person at gate 2 "
                              "(mock mode: no classification agent yet).",
                    confidence="high", confirmed_by_human=True)
            else:
                s.gate2.pop(n, None)
    pending = [l.line_no for l in confirmed_lines() if l.line_no not in s.gate2]
    if pending:
        st.info(f"Waiting for a confirmed code on line(s) {', '.join(map(str, pending))}.")
    else:
        st.success("Gate 2 closed — every line has a confirmed code.")


def step_duty():
    s = st.session_state
    c = s.consignment
    st.subheader("Duty cascade and landed cost")
    lines = confirmed_lines() if s.gate1_closed else []
    if not lines or any(l.line_no not in s.gate2 for l in lines):
        st.warning("Close both gates first.")
        return
    rates_cfg = duty.load_rates()
    st.caption("Computed by core/duty.py in Decimal. Rates in config/rates.json are "
               "**unverified** — check them against the law before showing anyone. "
               "Freight and insurance are shared across lines in proportion to invoice value.")
    c.importer_status = st.selectbox("Importer status", IMPORTER_STATUSES,
                                     index=IMPORTER_STATUSES.index(c.importer_status))

    total_value = sum((l.value for l in lines), Decimal("0"))
    results = []
    for l in lines:
        cls = s.gate2[l.line_no]
        row = tariff.lookup(cls.pct_code)
        share = l.value / total_value if total_value else Decimal("0")
        cif = duty.customs_value(l.value, c.freight * share, c.insurance * share, c.fx_rate)
        with st.container(border=True):
            st.markdown(f"**Line {l.line_no}** — {l.description} · `{cls.pct_code}`")
            cols = st.columns(2)
            rd_pct = cols[0].number_input(
                "Regulatory duty %", min_value=0.0, max_value=200.0, step=1.0,
                value=float(D(rates_cfg["regulatory_duty"]["default_rate"]) * 100),
                key=f"rd_{l.line_no}", help="Applies only to codes listed in the RD SRO.")
            try:
                cd_rate = tariff.cd_fraction(row)
            except tariff.SpecificDutyError as exc:
                cols[1].warning(str(exc))
                amount = cols[1].number_input("Customs duty amount (PKR)", min_value=0.0,
                                              step=1000.0, key=f"cd_amt_{l.line_no}")
                cd_rate = D(str(amount)) / cif if cif else Decimal("0")
            r = duty.compute(cls.pct_code, cif, cd_rate, importer_status=c.importer_status,
                             rd_rate=D(str(rd_pct)) / 100, rates=rates_cfg)
            results.append(r)
            st.dataframe(pd.DataFrame([
                ("CIF (customs value)", "", r.cif_pkr),
                ("Customs duty", f"{r.cd_rate * 100:.2f}%", r.cd_amount),
                ("Additional customs duty", f"{r.acd_rate * 100:.2f}%", r.acd_amount),
                ("Regulatory duty", f"{r.rd_rate * 100:.2f}%", r.rd_amount),
                ("Duty-paid value", "", r.duty_paid_value),
                ("Sales tax", f"{r.st_rate * 100:.2f}%", r.sales_tax),
                ("Withholding income tax", f"{r.wht_rate * 100:.2f}%", r.wht),
                ("Total taxes", "", r.total_taxes),
                ("Landed cost", "", r.landed_cost),
            ], columns=["", "Rate", "PKR"]).assign(PKR=lambda f: f["PKR"].map(rupees)),
                hide_index=True)

    c.classifications = [s.gate2[l.line_no] for l in lines]
    c.duties = results
    cif = sum((r.cif_pkr for r in results), Decimal("0"))
    taxes = sum((r.total_taxes for r in results), Decimal("0"))
    st.markdown("### Consignment total")
    cols = st.columns(3)
    cols[0].metric("CIF", rupees(cif))
    cols[1].metric("Total taxes", rupees(taxes),
                   f"{duty.money(taxes / cif * 100)}% of CIF" if cif else None,
                   delta_color="off")
    cols[2].metric("Landed cost", rupees(cif + taxes))
    st.download_button("Download consignment (JSON)", json.dumps(c.to_dict(), indent=2),
                       file_name=f"{c.reference or 'consignment'}.json",
                       mime="application/json")


# ---------------------------------------------------------------- page
def main():
    st.set_page_config(page_title="ClearLens", page_icon="📦", layout="wide")
    start()
    s = st.session_state
    with st.sidebar:
        st.title("ClearLens")
        st.caption("The model reads and writes. Code computes and decides.")
        st.radio("Step", STEPS, key="step")
        st.divider()
        open_blocking = len(reconcile.unresolved_blocking(s.consignment.discrepancies))
        st.markdown(f"Gate 1: {'✅ closed' if s.gate1_closed else f'⏳ {open_blocking} blocking open'}")
        lines = confirmed_lines() if s.gate1_closed else []
        done = sum(1 for l in lines if l.line_no in s.gate2)
        st.markdown(f"Gate 2: {'✅ closed' if lines and done == len(lines) else f'⏳ {done}/{len(lines)} codes'}")
        st.divider()
        if st.button("Reload mock consignment"):
            start(reset=True)
            st.rerun()
        st.caption("Prepares and checks; does not file. Not customs advice.")

    {STEPS[0]: step_documents, STEPS[1]: step_reconcile, STEPS[2]: step_gate1,
     STEPS[3]: step_gate2, STEPS[4]: step_duty}[s.step]()


if __name__ == "__main__":
    main()
