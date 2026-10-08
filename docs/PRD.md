<!-- Converted from ClearLens_PRD.docx (v1.1, 4 October 2026) so GitHub can display it.
     Sections 1-10 were written before the build; Section 11 holds the measured results. -->

# ClearLens — Product Requirements Document

AI import clearance and duty copilot for Pakistani clearing agents and importers

|              |                                                                                                                          |
|--------------|--------------------------------------------------------------------------------------------------------------------------|
| Event        | HEC–NCEAC & PEC Generative & Agentic AI Training, Cohort 11 — Final Hackathon (Pak Angels · iCodeGuru · Aspire Pakistan) |
| Build window | Friday 9:00 PM PKT → Sunday 11:59 PM PKT. Team and project registration closes Saturday 12:00 noon.                      |
| Team Lead    | Sami Ur Rehman                                                                                                           |
| Team         | Sami Ur Rehman (lead) · Ibrar Ali · Abrar Ali · Adnan Sajjad · Ayesha Umar Daraz · Muhammad Ibrahim Khan                 |
| Version      | v1.1 · 4 October 2026 · Sections 1–10 written before the build; Section 11 holds the measured results                    |

> **The rule that governs this product:** the model reads documents and proposes; **Python computes every rupee and every verdict**, and a human approves the classification before any number is treated as final. Duty arithmetic is a cascade — an LLM that is 95% right on one line is wrong on the total.

## 1. Summary

|                |                                                                                                                                                                                                                                                                                                                 |
|----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Problem        | Import clearance in Pakistan is prepared from three PDFs — commercial invoice, packing list, bill of lading — that frequently disagree with each other, and from an HS/PCT code chosen by hand. A mismatch or a wrong code means a held container, re-assessment, demurrage and penalties.                      |
| Product        | Upload the three documents. Agents extract and reconcile them line by line, propose the PCT code with the tariff heading text and reasoning, compute the full duty cascade from FBR’s published tariff, and output a pre-filing checklist with estimated landed cost. A human approves before anything is used. |
| Primary user   | The clearing agent or import clerk who prepares goods declarations for a living and is paid per consignment.                                                                                                                                                                                                    |
| Secondary user | The SME importer who wants to know the landed cost and the risk of a hold before the container sails.                                                                                                                                                                                                           |
| Why agentic    | Four specialised agents, deterministic checks between them, two human approval gates, and an automated business process that is today done by hand in a spreadsheet.                                                                                                                                            |
| Skills applied | Multi-agent systems · agentic AI · AI workflows · business process automation · generative AI                                                                                                                                                                                                                   |

## 2. Problem and context

Every commercial import into Pakistan is declared through the Pakistan Single Window (PSW) as a goods declaration. Before that declaration can be filed, somebody reconciles three documents by hand and picks a tariff code. Both steps are error-prone, and both errors are expensive.

### 2.1 Where the errors come from

- **Document disagreement.** The invoice says 500 cartons, the packing list says 496, the bill of lading says a different gross weight. Whichever number is wrong, the declaration is wrong.

- **Description drift.** The supplier’s product description is written for a buyer, not for a tariff. "LED driver module" has to become an eight-digit PCT code with a legal heading behind it.

- **Classification by habit.** Codes are reused from the last similar consignment, which is how an error repeats for years until an audit finds it.

- **Rules that move.** Regulatory duty and exemptions change through SROs mid-year. A code that was right last season may not be right now.

### 2.2 Why a small error becomes a large bill

Pakistani import taxes stack, and each layer is charged on the layer below it:

| **Levy**                      | **Charged on**                            | **Note**                                                                   |
|-------------------------------|-------------------------------------------|----------------------------------------------------------------------------|
| Customs duty (CD)             | CIF value                                 | Rate comes from the eight-digit PCT code in the First Schedule             |
| Additional customs duty (ACD) | CIF value                                 | Flat rate                                                                  |
| Regulatory duty (RD)          | CIF value                                 | Only on goods named in an SRO; often zero                                  |
| Sales tax                     | CIF + CD + ACD + RD (the duty-paid value) | Standard rate unless an SRO reduces it                                     |
| Withholding income tax        | Duty-paid value + sales tax               | Rate depends on importer status (commercial, industrial, filer, non-filer) |

Because sales tax and withholding tax sit on top of the duty, a wrong code does not cost the rate difference — it compounds. On a CIF value of Rs 10,000,000, with ACD 2%, sales tax 18% and withholding 5.5%:

| **Levy**                | **Correct code (CD 3%)** | **Wrong code (CD 20%)** | **Difference** |
|-------------------------|--------------------------|-------------------------|----------------|
| Customs duty            | 300,000                  | 2,000,000               | 1,700,000      |
| Additional customs duty | 200,000                  | 200,000                 | 0              |
| Sales tax               | 1,890,000                | 2,196,000               | 306,000        |
| Withholding income tax  | 681,450                  | 791,780                 | 110,330        |
| **Total**               | **3,071,450**            | **5,187,780**           | **2,116,330**  |

A 17-point gap in the duty rate produces a 21.2-point gap in total cost. That is the number the product exists to protect, and it is also the demo.

> Rates above are illustrative and must be confirmed against the current tariff and finance act before they appear in any customer-facing material. The engine reads real rates from the FBR tariff; these figures are for the worked example only.

## 3. Users

|                    |                                                                                                                                                                                                |
|--------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Primary            | Clearing agent or import clerk at a small clearing agency. Handles several consignments a week, is paid per consignment, and personally absorbs the cost of a mistake in time and credibility. |
| Secondary          | SME importer — food, electronics, machinery, chemicals — who currently learns the landed cost after the container has already arrived.                                                         |
| Job to be done     | "Before I file, tell me whether these documents agree, what code this is, what it will cost me, and what will get me held."                                                                    |
| Where we find them | Clearing agencies around the dry ports and Karachi; importers in wholesale markets. Validation target: five conversations in the first week after the hackathon.                               |

## 4. Scope

| **Feature**                             | **Definition of done**                                                                                                                                                                                                                                                                                        | **Priority**     |
|-----------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------|
| F1 — Document intake and reconciliation | Accepts commercial invoice, packing list and bill of lading as PDFs. Extracts line items (description, quantity, unit, unit price, value, weight, origin, Incoterm) with a page citation each. Produces a line-by-line comparison across the three documents and flags every mismatch with both values shown. | MUST             |
| F2 — Classification with evidence       | For each line item, proposes a PCT/HS code with the verbatim tariff heading text, the reasoning, a confidence level and two alternatives. The user confirms or overrides each one. No code is used downstream until confirmed.                                                                                | MUST             |
| F3 — Duty engine and filing pack        | Computes CD, ACD, RD, sales tax and withholding tax in the correct cascade from the confirmed code; shows a line-by-line and total landed cost; produces the pre-filing checklist of documents and certificates; exports to Excel.                                                                            | MUST             |
| F4 — What-if comparison                 | Shows the cost difference between the proposed code and its alternatives, so the user sees what a misclassification would cost.                                                                                                                                                                               | SHOULD           |
| Stretch                                 | Scanned and photographed documents; SRO/regulatory-duty lookup; Arabic and Gulf tariffs (Fasah, Dubai Trade).                                                                                                                                                                                                 | NOT this weekend |

**Out of scope:** filing anything into PSW, integration with any government system, the customs valuation database, duty drawback, user accounts, payments, and a chat interface.

## 5. User flow

1.  Upload the three documents, or load the bundled demo consignment.

2.  **Extraction agents** read each document and return structured line items with page citations.

3.  **Reconciler (code)** compares the three sets and produces the mismatch table.

4.  **Human gate 1:** the user resolves each mismatch by choosing the correct value, then confirms the line items.

5.  **Classification agent** proposes a PCT code per line with tariff heading text, reasoning, confidence and alternatives.

6.  **Human gate 2:** the user confirms or overrides each code. This is the legally meaningful decision, so it is never automatic.

7.  **Duty engine (code)** computes the full cascade and the landed cost per line and in total.

8.  **Filing pack agent** drafts the document checklist and an importer-facing cost summary; the user edits and exports.

## 6. Architecture

| **\#** | **Component**                             | **Type**               | **Input → Output**                                                             |
|--------|-------------------------------------------|------------------------|--------------------------------------------------------------------------------|
| 1      | Ingestion                                 | Code                   | PDF → per-page text with page numbers (PyMuPDF)                                |
| 2      | Invoice / Packing / B-L extraction agents | LLM ×3                 | Page text → structured line items against a fixed JSON schema                  |
| 3      | Reconciler                                | Code                   | Three line-item sets → matched lines + discrepancy list                        |
| —      | Human gate 1                              | Human                  | Resolve mismatches → confirmed line items                                      |
| 4      | Classification agent                      | LLM + tariff retrieval | Line description → PCT code, heading text, reasoning, confidence, alternatives |
| —      | Human gate 2                              | Human                  | Confirm or override each code                                                  |
| 5      | Duty engine                               | Code                   | Code + CIF + importer status → CD, ACD, RD, sales tax, WHT, landed cost        |
| 6      | Filing pack agent                         | LLM                    | Results → document checklist and cost summary text                             |

Three separate extraction agents rather than one is deliberate: each document type has its own shape, and keeping them separate is what makes the reconciliation step meaningful instead of a single model quietly averaging contradictions away.

## 7. Data models

LineItem

| **Field**                       | **Type**                                    | **Example**                                   |
|---------------------------------|---------------------------------------------|-----------------------------------------------|
| source_doc                      | enum: invoice, packing_list, bill_of_lading | invoice                                       |
| line_no / description           | int / str                                   | 3 / "Fresh garlic, dried, in 10 kg mesh bags" |
| quantity / unit                 | float / str                                 | 500 / cartons                                 |
| unit_price / currency / value   | float / str / float                         | 18.50 / USD / 9,250.00                        |
| net_weight_kg / gross_weight_kg | float / float                               | 5,000 / 5,240                                 |
| origin / incoterm               | str / str                                   | CN / CFR                                      |
| page / quote                    | int / str                                   | 1 / verbatim line from the document           |

Discrepancy, Classification, DutyResult

- **Discrepancy** — field, value in each document, severity (blocking or advisory), suggested resolution.

- **Classification** — pct_code, heading_text (verbatim from the tariff), reasoning, confidence, alternatives\[\], confirmed_by_human (bool).

- **DutyResult** — cif_pkr, cd_rate, cd_amount, acd_amount, rd_amount, duty_paid_value, sales_tax, wht, total_taxes, landed_cost. Every field computed, none generated.

## 8. Duty engine specification

> cif_pkr = (invoice_value + freight + insurance) \* fx_rate
>
> cd = cif_pkr \* cd_rate \# rate from the confirmed PCT code
>
> acd = cif_pkr \* acd_rate
>
> rd = cif_pkr \* rd_rate \# 0 unless the code appears in an RD SRO
>
> dpv = cif_pkr + cd + acd + rd \# duty-paid value
>
> sales_tax = dpv \* st_rate
>
> wht = (dpv + sales_tax) \* wht_rate
>
> total_taxes = cd + acd + rd + sales_tax + wht
>
> landed_cost = cif_pkr + total_taxes

Every rate is read from a configuration table with its source recorded, never hard-coded in the UI. The engine is unit-tested against hand calculations, including the worked example in Section 2.2.

## 9. Tariff data

- The duty table is parsed from the FBR publication **Pakistan Customs Tariff FY 2026-27** (download1.fbr.gov.pk) rather than typed by hand, so the demo shows the government’s own rates.

- Parsed fields: PCT code, description, customs duty rate. Stored as CSV in the repository with the source file name and date.

- ACD, sales tax and withholding rates are configuration values with their legal source noted beside them.

- For the hackathon, parsing is limited to the chapters that cover the demo consignments. The parser is written to run over the whole document afterwards.

## 10. Technology

| **Layer**     | **Choice**                                          | **Reason**                                                                            |
|---------------|-----------------------------------------------------|---------------------------------------------------------------------------------------|
| Language / UI | Python 3.11 + Streamlit                             | One file for the interface; editable tables are exactly what the two human gates need |
| Model         | Gemini Flash tier, free key, structured JSON output | Long context, cheap, and it auto-recovers when a model name is retired                |
| PDF           | PyMuPDF                                             | Page-accurate text, which is what makes citations possible                            |
| API client    | Python standard library (urllib)                    | One less dependency that can break a Sunday deployment                                |
| Calculation   | Pure Python + pytest                                | Every rupee is tested; nothing numeric comes from the model                           |
| Hosting       | GitHub + Streamlit Community Cloud                  | Free, and proven on our previous build                                                |

## 11. Results — measured, not estimated

Measured on 4 October 2026 against the three generated demo documents (commercial invoice, packing list, two-page bill of lading), extracted live on Gemini 3.5 Flash-Lite and then cached.

| **Measure**           | **Result**                                  | **How**                                                                                                                                                                                  |
|-----------------------|---------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Tariff coverage       | 7,598 PCT codes across 96 chapters          | Parsed from FBR’s Pakistan Customs Tariff FY 2026-27 (345 pages). 103 rows skipped (1.3%), each logged with its page in parse_report.txt rather than guessed.                            |
| Specific-duty rows    | 54 identified and preserved                 | Fixed rupee duties (Rs/MT, Rs/Kg, Rs/set) are stored with their printed amount; cd_fraction() raises rather than returning a number.                                                     |
| Live extraction       | 9 line items across 4 pages                 | Invoice 3, packing list 3, bill of lading 3. One call per page, paced to the free tier.                                                                                                  |
| Citation verification | 9 of 9 verified, 0 flagged                  | Each quote located on its cited page by code, never by the model.                                                                                                                        |
| Reconciliation        | Exactly 2 discrepancies found               | Line 1 quantity: invoice and B/L say 1,200, packing list says 1,180 — blocking. Line 3 net weight: 3,000 vs 2,950 kg, 1.67% apart — advisory, under the 2% tolerance. Both were planted. |
| Test suite            | 103 tests passing                           | Rules engine, tariff parser, citation verifier, LLM client, reconciler.                                                                                                                  |
| Adversarial check     | 9 tests fail when the verifier is sabotaged | The citation verifier was deliberately replaced with one that approves everything, to confirm the tests catch it.                                                                        |
| Offline operation     | Full pipeline, zero API calls               | Extraction cached by document hash and committed, so the demo does not depend on upstream capacity.                                                                                      |

Worked duty example, computed by the engine and verified by hand: a Rs 10,000,000 CIF consignment classified 0703.2000 (garlic, 0% CD) incurs Rs 2,697,980 in taxes; the same consignment misclassified 0703.9000 (leeks, 10% CD) incurs Rs 3,942,880 — a difference of Rs 1,244,900, or 12.45% of CIF, from a 10-point rate gap.

## 12. Team: the five skills we need

One person per role. Overlap is fine; absence is not. Recruit in this order.

| **\#** | **Role**                    | **Must have**                                                                                                  | **Owns**                                                                     |
|--------|-----------------------------|----------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| 1      | AI / backend engineer       | Python; has called an LLM API from code; structured JSON output; prompt writing                                | The three extraction agents, the classification agent, the prompts           |
| 2      | Full-stack / deployment     | Python + Streamlit or Gradio; Git; has deployed something before                                               | The interface, both human gates, deployment, repo hygiene                    |
| 3      | Data / calculation engineer | Python + pandas; careful with numbers; can write tests                                                         | The tariff parser, the duty engine, the test suite                           |
| 4      | Domain advisor              | Works in import/export, logistics, freight forwarding, customs clearing or supply chain — or has family who do | Realism: real documents, how agents actually work, what gets containers held |
| 5      | Design and presentation     | Slides and video editing; clear writing                                                                        | The deck, the demo video, the submission pack                                |

> Role 4 is the one most teams will not have, and it is worth more than another engineer. One person who has stood in a clearing agent’s office will keep this product honest.

Working agreement

- 12–15 hours between Friday 9 PM and Sunday 11:59 PM, including the Friday kickoff call.

- A two-line status update at 11 PM Friday, 1 PM and 11 PM Saturday, 1 PM Sunday.

- Push to GitHub at least every three hours. No "I will push everything at the end".

- Say early if you have to drop out, so the work can be rebalanced rather than discovered missing on Sunday.

## 13. Plan

| **When (PKT)**    | **Goal**                                                                                                          |
|-------------------|-------------------------------------------------------------------------------------------------------------------|
| Fri before 9 PM   | Team registered; FBR tariff downloaded; one real (anonymised) document set in hand                                |
| Fri 9–10:30 PM    | Kickoff: walk through this PRD; freeze the schemas; assign branches                                               |
| Fri 10:30 PM–2 AM | Ingestion + invoice extraction agent; UI skeleton on mock JSON; tariff parser started                             |
| Sat 9 AM–2 PM     | All three extraction agents; reconciler; duty engine with tests; walking skeleton deployed                        |
| Sat 12:00 noon    | **Team and project details complete in the registration form** (hard deadline)                                    |
| Sat 2–10 PM       | Classification agent + tariff retrieval; both human gates; landed-cost view; measurement on the real document set |
| Sat 10 PM         | Checkpoint: F1 + F2 + F3 working end to end on the live link, or cut F4 and fix                                   |
| Sun 9 AM–3 PM     | Filing pack, Excel export, what-if comparison, polish, README with screenshots                                    |
| Sun 3–6 PM        | Bug bash on every demo consignment; PRD results section filled in                                                 |
| Sun 6 PM          | **Feature freeze.** Record the video twice; finish slides                                                         |
| Sun 9–10:30 PM    | Submit all links; test each in a private window                                                                   |

## 14. Risks

| **Risk**                                                  | **Mitigation**                                                                                                                     |
|-----------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| The product is advisory and someone treats it as a filing | Explicit disclaimer in the interface and in the export: estimates for preparation, not a declaration. Human confirms every code.   |
| Customs valuation differs from invoice value              | Out of scope and stated plainly; the engine computes on declared CIF and labels it as such.                                        |
| Regulatory duty and exemptions change through SROs        | Rates live in a configuration table with sources, not in code. Roadmap item for automatic SRO tracking.                            |
| Commercial documents are confidential                     | Demo uses anonymised documents. Free-tier APIs may use inputs for training, so no client document goes through them in production. |
| Teammate drops out                                        | Six-person team, named backup for each module, status check-ins.                                                                   |
| Classification is wrong and the user trusts it            | Confidence shown, alternatives shown, what-if cost comparison shown, human confirmation required.                                  |

## 15. Beyond the weekend

**Who pays:** clearing agents, per consignment or per seat. They already pay for this labour, which is the part that makes this a business rather than a demo. **Then:** SME importers who want landed cost before they commit to a purchase order.

**Why it travels:** every emerging-market port has the same three documents, the same classification problem and the same cascade of levies. The Gulf runs Fasah in Saudi Arabia and Dubai Trade in the UAE with an identical shape, which is where this goes after Pakistan.

**What we are not pretending:** AI tariff classification is already a crowded category globally — Digicust, iCustoms, Zonos and others sell it to US and EU brokers. None of them model Pakistan’s tariff, PSW’s declaration shape or the local levy cascade. The wedge is local depth, and the moat is the relationship with clearing agents, not the model.

Appendix — sources

- Pakistan Customs Tariff FY 2026-27, FBR: download1.fbr.gov.pk/Docs/20268251181330180Tariff-2026-27.pdf

- Pakistan Single Window, single declaration (imports) user manual: psw.gov.pk/media/Manuals/PSW_User_Manual-SD_Imports_AD.pdf

- Import duty structure and cascade: olympic.com.pk/blog/trade-guides/pakistan-import-duty-structure-guide
