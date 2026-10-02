"""Pakistan Customs Tariff FY 2026-27 -> data/tariff/pct_codes.csv, plus lookup and search.

The FBR tariff PDF is an Excel export: every table row is a ruled cell band with three
columns — PCT code, description, CD (%). We read word coordinates with PyMuPDF, cut each page
at its horizontal rules, and assign words to columns by x position. Reading the plain text
stream instead loses the row boundaries, and with them which rate belongs to which code.

An eight-digit line's own text is often just "- - Other". Its full description is the heading
text plus every dash-indented subheading above it, so we keep a stack of open subheadings
keyed by dash depth and join the path:

    Live poultry, ... guinea fowls. > Weighing not more than 185 g: > Turkeys

Rule: never invent a rate. Some lines carry a specific duty — a fixed rupee amount such as
"Rs. 9050/MT" — instead of a percentage. They are kept with duty_type="specific", an empty
cd_rate and the amount in specific_duty_text, so a caller can say why no percentage applies.
In the PDF these amounts are printed rotated in a narrow cell and wrap mid-number
("Rs." / "90" / "50/" / "MT"); the stored text drops those wrap breaks: "Rs. 9050/MT".

A tariff line whose CD cell is empty or unreadable, or whose code does not belong to the
heading it sits under, is left out of the CSV and written to data/tariff/parse_report.txt with
its page and raw text. Chapter 99 (concessions, four-digit codes, prose) is not a tariff
schedule and is reported, not parsed.

    python -m core.tariff        # rebuild the CSV and the report, print coverage
"""
import csv
import re
from collections import defaultdict
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TARIFF_DIR = ROOT / "data" / "tariff"
PDF_PATH = TARIFF_DIR / "pakistan_customs_tariff_2026-27.pdf"
CSV_PATH = TARIFF_DIR / "pct_codes.csv"
REPORT_PATH = TARIFF_DIR / "parse_report.txt"
COLUMNS = ["pct_code", "description", "cd_rate", "chapter", "heading",
           "duty_type", "specific_duty_text"]
AD_VALOREM = "ad_valorem"
SPECIFIC = "specific"
SEP = " > "

# Column rules, in PDF points. They match the vertical rules drawn on every tariff page
# (x = 50, 118, 386, 454).
CODE_COL_RIGHT = 118.0
RATE_COL_LEFT = 386.0

CODE_RE = re.compile(r"^\d{4}\.\d{4}$")
HEADING_RE = re.compile(r"^\d{2}\.\d{2}$")
DELETED_RE = re.compile(r"^\[\d{2}\.\d{2}\]$")
CH99_RE = re.compile(r"^99\d{2}$")
RATE_RE = re.compile(r"^\d{1,3}(\.\d+)?$")
SPECIFIC_RE = re.compile(r"^Rs\. \d+(/(MT|Kg|set)| per meter)$")   # after _specific_text()
SUBCHAPTER_RE = re.compile(r"^[IVX]+\s*[.\-]")       # "II.- INORGANIC ACIDS ..."
LEADER_RE = re.compile(r"^[\s\-–—]+")


# ---------------------------------------------------------------- PDF -> cells
def _lines(words: list) -> List[List[tuple]]:
    """Group words into visual lines (same baseline within 3pt), left to right."""
    out: List[List[tuple]] = []
    for w in sorted(words, key=lambda w: (w[1], w[0])):
        if out and abs(out[-1][0][1] - w[1]) < 3:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: w[0]) for line in out]


def _text(words: list) -> str:
    return " ".join(" ".join(w[4] for w in line) for line in _lines(words)).strip()


def _cells(page) -> List[Tuple[str, str, str, str]]:
    """One page -> [(code, description, rate, raw)], one tuple per ruled table row."""
    rules: List[float] = []
    for d in page.get_drawings():
        r = d["rect"]
        if r.height < 2 and r.width > 200:
            rules.append(r.y0)
    ys: List[float] = []
    for y in sorted(rules):
        if not ys or y - ys[-1] > 2:
            ys.append(y)

    bands: Dict[int, list] = defaultdict(list)
    for w in page.get_text("words"):
        cy = (w[1] + w[3]) / 2
        bands[sum(1 for y in ys if y < cy)].append(w)

    cells = []
    for key in sorted(bands):
        words = bands[key]
        # A missing rule can leave two tariff lines in one band ("8544.6010 8544.6020").
        # Cut the band at the top of each code so every code keeps its own text and rate.
        starts = sorted(w[1] for w in words if w[2] < CODE_COL_RIGHT and CODE_RE.match(w[4]))
        cuts = [float("-inf")] + starts[1:] + [float("inf")]
        for lo, hi in zip(cuts, cuts[1:]):
            part = [w for w in words if lo - 1 <= w[1] < hi - 1]
            code = _text([w for w in part if w[2] < CODE_COL_RIGHT])
            rate = _text([w for w in part if w[0] > RATE_COL_LEFT])
            desc = _text([w for w in part if w[2] >= CODE_COL_RIGHT and w[0] <= RATE_COL_LEFT])
            raw = " | ".join(x for x in (code, desc, rate) if x)
            cells.append((code, desc, rate, raw))
    return cells


# ---------------------------------------------------------------- cells -> rows
def _depth(desc: str) -> int:
    lead = LEADER_RE.match(desc)
    return lead.group(0).count("-") + lead.group(0).count("–") if lead else 0


def _clean(desc: str) -> str:
    return re.sub(r"\s+", " ", LEADER_RE.sub("", desc)).strip()


def parse(pdf_path: Path = PDF_PATH) -> Tuple[List[dict], List[dict], List[str]]:
    """Parse the tariff. Returns (rows, skipped, notes).

    rows     dicts with COLUMNS, in tariff order
    skipped  {"page", "pct_code", "reason", "raw"} for every tariff line left out
    notes    informational lines for the report (deleted headings, layout oddities)
    """
    import pymupdf  # imported here so lookup()/search() work without PyMuPDF installed

    rows: List[dict] = []
    skipped: List[dict] = []
    notes: List[str] = []

    heading = ""          # "01.05"
    heading_text = ""
    stack: List[Tuple[int, str]] = []     # open subheadings: (dash depth, text)
    last: Optional[dict] = None           # row that continuation lines attach to
    in_ch99 = False

    def skip(page, code, reason, raw):
        skipped.append({"page": page, "pct_code": code, "reason": reason, "raw": raw})

    with pymupdf.open(str(pdf_path)) as doc:
        for pno, page in enumerate(doc, start=1):
            for code, desc, rate, raw in _cells(page):
                if not (code or desc or rate):
                    continue
                if code == "PCT CODE" or desc.startswith("PAKISTAN CUSTOMS TARIFF"):
                    continue                                  # page-1 title and column header
                if CH99_RE.match(code) or (in_ch99 and not CODE_RE.match(code)):
                    in_ch99 = True
                    if CH99_RE.match(code):
                        skip(pno, code, "Chapter 99 concession entry, not an eight-digit PCT line", raw)
                    continue
                if code and not any(ch.isdigit() for ch in code):
                    notes.append(f"page {pno}: stray mark {code!r} in code column ignored: {raw}")
                    code = ""

                if DELETED_RE.match(code):
                    notes.append(f"page {pno}: heading {code} is marked deleted in the tariff")
                    continue

                if HEADING_RE.match(code):
                    heading, heading_text, stack, last = code, _clean(desc), [], None
                    if rate:
                        skip(pno, code, "rate printed against a four-digit heading", raw)
                    continue

                if CODE_RE.match(code):
                    rec = _tariff_line(pno, code, desc, rate, raw, heading, heading_text, stack)
                    if isinstance(rec, str):
                        skip(pno, code, rec, raw)
                        last = None
                    else:
                        if rec.pop("_new_heading", False):
                            heading, heading_text, stack = rec["heading"], _clean(desc), []
                        rows.append(rec)
                        last = rec
                    continue

                if code:
                    skip(pno, code, "unrecognised text in code column", raw)
                    continue

                if rate:
                    skip(pno, "", "rate with no PCT code", raw)
                    continue

                # Code-less row: a dash subheading, a sub-chapter title, or a continuation
                # (enumerated "(1) ..." items that belong to the line above).
                if _depth(desc):
                    d = _depth(desc)
                    stack = [s for s in stack if s[0] < d] + [(d, _clean(desc))]
                    last = None
                elif SUBCHAPTER_RE.match(desc) and desc.upper() == desc:
                    continue
                elif last is not None:
                    last["description"] += " " + _clean(desc)
                elif stack:
                    d, text = stack[-1]
                    stack[-1] = (d, text + " " + _clean(desc))
                else:
                    notes.append(f"page {pno}: orphan text with no owner ignored: {raw}")

    seen = set()
    for r in rows:
        if r["pct_code"] in seen:
            notes.append(f"duplicate PCT code {r['pct_code']} kept twice")
        seen.add(r["pct_code"])
    return rows, skipped, notes


def _specific_text(printed: str) -> str:
    """'Rs. 105 50/ MT' -> 'Rs. 10550/MT'. Drops the cell's wrap breaks, keeps every character."""
    s = re.sub(r"\s+", "", printed)
    s = re.sub(r"^Rs\.", "Rs. ", s)
    return re.sub(r"(\d)per", r"\1 per ", s)


def _tariff_line(pno, code, desc, rate, raw, heading, heading_text, stack):
    """Build one CSV row, or return the reason it cannot be trusted."""
    if not rate.strip():
        return "no CD rate printed"
    if RATE_RE.match(rate.replace(" ", "")):
        duty = {"cd_rate": rate.replace(" ", ""), "duty_type": AD_VALOREM, "specific_duty_text": ""}
    elif SPECIFIC_RE.match(_specific_text(rate)):
        duty = {"cd_rate": "", "duty_type": SPECIFIC, "specific_duty_text": _specific_text(rate)}
    else:
        return f"CD is neither a percentage nor a recognised specific duty ({rate})"
    own = _clean(desc)
    if not own:
        return "no description printed"

    code_heading = f"{code[:2]}.{code[2:4]}"
    rec = {"pct_code": code, "chapter": code[:2], "heading": code_heading, **duty}

    if code_heading != heading:
        # A heading with a single tariff line prints as one row: "0205.0000 Meat of horses".
        if code.endswith("0000") and _depth(desc) == 0:
            rec["description"] = own
            rec["_new_heading"] = True
            return rec
        return (f"heading {code_heading} row is missing from the tariff (line sits under "
                f"{heading or 'no heading'}), so its full description cannot be built")

    depth = _depth(desc)
    parents = [t for d, t in stack if depth == 0 or d < depth]
    rec["description"] = SEP.join([heading_text] + parents + [own])
    return rec


# ---------------------------------------------------------------- build
def build(pdf_path: Path = PDF_PATH, csv_path: Path = CSV_PATH,
          report_path: Path = REPORT_PATH) -> dict:
    rows, skipped, notes = parse(pdf_path)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    chapters = sorted({r["chapter"] for r in rows})
    specific = [r for r in rows if r["duty_type"] == SPECIFIC]
    lines = [
        f"Parse report for {pdf_path.name}",
        f"rows parsed:  {len(rows)}  ({len(rows) - len(specific)} ad valorem, "
        f"{len(specific)} specific)",
        f"rows skipped: {len(skipped)}",
        f"chapters covered: {len(chapters)} ({', '.join(chapters)})",
        "",
        "Skipped rows are NOT in pct_codes.csv. No rate was guessed for any of them.",
        "",
        "== SKIPPED ==",
    ]
    for s in skipped:
        lines.append(f"page {s['page']:>3}  {s['pct_code'] or '-':<10}  {s['reason']}")
        lines.append(f"          raw: {s['raw']}")
    lines += ["", "== SPECIFIC DUTIES (in pct_codes.csv, cd_rate empty) =="]
    lines += [f"{r['pct_code']:<10}  {r['specific_duty_text']}" for r in specific]
    lines += ["", "== NOTES =="] + notes
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    load.cache_clear()
    _index.cache_clear()
    return {"parsed": len(rows), "specific": len(specific), "skipped": len(skipped),
            "chapters": chapters}


# ---------------------------------------------------------------- lookup and search
@lru_cache(maxsize=1)
def load(csv_path: Path = CSV_PATH) -> Tuple[dict, ...]:
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} not found — run: python -m core.tariff")
    with open(csv_path, newline="", encoding="utf-8") as fh:
        return tuple(dict(r) for r in csv.DictReader(fh))


@lru_cache(maxsize=1)
def _index() -> Dict[str, dict]:
    return {r["pct_code"]: r for r in load()}


def normalise(pct_code: str) -> str:
    """'07032000', '0703.2000', ' 0703 2000 ' -> '0703.2000'. Anything else -> ''."""
    digits = re.sub(r"\D", "", str(pct_code or ""))
    return f"{digits[:4]}.{digits[4:]}" if len(digits) == 8 else ""


def lookup(pct_code: str) -> dict:
    """The CSV row for one PCT code, or {} if the code is not in the parsed tariff.

    cd_rate is a percentage string as printed ("20" means 20%). Use cd_fraction() before
    handing it to duty.compute, which expects 0.20.

    duty_type is "ad_valorem" or "specific". A specific row has an empty cd_rate and the fixed
    rupee duty in specific_duty_text ("Rs. 9050/MT"); the app has to ask for that amount.
    """
    row = _index().get(normalise(pct_code))
    return dict(row) if row else {}


class SpecificDutyError(ValueError):
    """The code carries a fixed rupee duty, so there is no percentage to compute with."""


def cd_fraction(row: dict) -> Decimal:
    """'20' -> Decimal('0.20'), for duty.compute. Raises SpecificDutyError on a specific row."""
    if row.get("duty_type") == SPECIFIC:
        raise SpecificDutyError(
            f"{row['pct_code']} carries a fixed rupee duty ({row['specific_duty_text']}), "
            "not a percentage — enter the duty amount manually")
    return Decimal(row["cd_rate"]) / Decimal(100)


def search(text: str, limit: int = 20) -> List[dict]:
    """Rows whose description contains every word of `text`, best matches first.

    Ranking: more hits in the line's own text (after the last '>') beat hits that only
    match through the heading. A code or code prefix ("0703", "0703.20") matches by code.
    """
    q = str(text or "").strip().lower()
    if not q:
        return []
    digits = re.sub(r"\D", "", q)
    if digits and re.fullmatch(r"[\d.\s]+", q):
        hits = [r for r in load() if r["pct_code"].replace(".", "").startswith(digits)]
        return [dict(r) for r in hits[:limit]]

    terms = re.findall(r"[a-z0-9]+", q)
    scored = []
    for r in load():
        desc = r["description"].lower()
        if all(t in desc for t in terms):
            own = desc.rsplit(SEP.strip(), 1)[-1]
            scored.append((-sum(t in own for t in terms), len(desc), r["pct_code"], r))
    scored.sort(key=lambda s: s[:3])
    return [dict(s[3]) for s in scored[:limit]]


if __name__ == "__main__":
    result = build()
    print(f"rows parsed:      {result['parsed']}  ({result['specific']} with a specific duty)")
    print(f"rows skipped:     {result['skipped']}  (see {REPORT_PATH.relative_to(ROOT)})")
    print(f"chapters covered: {len(result['chapters'])}")
