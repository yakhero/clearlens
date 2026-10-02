# ClearLens

**Agentic document intelligence for trade and logistics operations.**

A clearing agent prepares an import declaration from three PDFs — a commercial invoice, a
packing list and a bill of lading — that rarely agree with each other, then picks a tariff
code by hand. ClearLens reads all three, reconciles them line by line, proposes the tariff
code with the heading text behind it, and computes the full duty cascade. A human approves
before any number is used.

Built for the HEC–NCEAC & PEC Generative & Agentic AI Training, Cohort 11 — Final Hackathon.

## The rule this repo is built on

> **The model reads and writes. Code computes and decides.**

Pakistani import levies cascade: customs duty, additional customs duty and regulatory duty
are charged on CIF; sales tax is charged on CIF plus those duties; withholding tax is charged
on all of it. So a wrong tariff code does not cost you the rate difference — it compounds.

On a CIF of Rs 10,000,000, a 3% duty code versus a 20% one:

| | Correct (CD 3%) | Wrong (CD 20%) |
|---|---|---|
| Customs duty | 300,000 | 2,000,000 |
| Additional customs duty | 200,000 | 200,000 |
| Sales tax (18%) | 1,890,000 | 2,196,000 |
| Withholding tax (5.5%) | 681,450 | 791,780 |
| **Total taxes** | **3,071,450** | **5,187,780** |

A 17-point gap in the duty rate becomes a **21.16-point** gap in cost — Rs 2,116,330 on one
container. That arithmetic is in `core/duty.py`, it uses `Decimal` throughout, and it is
covered by tests. No language model is asked to compute money anywhere in this project.

## Pipeline

| # | Stage | Type | What it does |
|---|-------|------|--------------|
| 1 | Ingestion | code | PDF → per-page text with page numbers |
| 2 | Extraction agents (×3) | **LLM** | Each document → `LineItem[]` against the frozen schema |
| 3 | Reconciler | code | Three sets → matched lines + `Discrepancy[]` |
| — | **Human gate 1** | person | Resolve each mismatch, confirm the lines |
| 4 | Classification agent | **LLM** | Line description → `Classification` with verbatim tariff heading |
| — | **Human gate 2** | person | Confirm or override every tariff code |
| 5 | Duty engine | code | `DutyResult` — the full cascade, per line and in total |
| 6 | Filing pack agent | **LLM** | Document checklist and cost summary to edit and export |

## Run it

```bash
pip install -r requirements.txt
python run_tests.py          # no API key needed — currently 10/10
streamlit run app.py         # once the UI lands
```

## Layout

```
core/schemas.py   FROZEN data contract — everything is written against this
core/duty.py      the cascade; Decimal only, unit-tested
config/rates.json levy rates with their legal source and a verified flag
tests/            the numbers the pitch claims
data/tariff/      PCT codes and duty rates parsed from the FBR tariff (not hand-typed)
data/consignments/ anonymised demo document sets
docs/             PRD
```

## Status

- [x] Data contract frozen
- [x] Duty engine + tests
- [ ] Tariff parser (FBR Pakistan Customs Tariff FY 2026-27 → CSV)
- [ ] Extraction agents and reconciler
- [ ] Classification agent with tariff retrieval
- [ ] Streamlit UI with both human gates
- [ ] Filing pack and Excel export

## Honest limits

ClearLens prepares and checks; it does not file. There is no PSW integration. It computes on
the declared CIF value, not the customs valuation database. Regulatory duty and exemptions
move through SROs, so every rate in `config/rates.json` carries its source and a `verified`
flag — **all of them are currently `false`**, and they must be checked against the law before
any of this is shown to a real importer. Demo documents are anonymised and company profiles
are fictional. This is not customs advice.
