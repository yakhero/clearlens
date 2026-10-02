"""The reconciler. Three documents in, matched lines and discrepancies out. No model involved.

The invoice, packing list and bill of lading describe the same goods, but each in its own
words and its own order, and a B/L usually leaves out prices. So lines are matched by what
they say (description similarity) and roughly where they sit (relative position), with one
best assignment across all lines — never by assuming line 1 is line 1 everywhere.

A field is compared only when at least two documents state it. Zero and "" mean "not stated"
on that document, not a mismatch.

Severity — what a human must settle before the line can go further:
    quantity, value, origin                      blocking
    net / gross weight, spread under 2%          advisory
    net / gross weight, spread 2% or more        blocking
    unit price                                   advisory (a real price gap also shows up in
                                                 value or quantity, which block)
    unit or currency differs                     blocking (quantities/values can't be compared)
    line missing from invoice or packing list    blocking
    line missing from a B/L that lists lines     advisory (B/Ls often group goods)

Discrepancy.field_name is "line_<n>.<field>", e.g. "line_2.quantity"; split_field() reads it.
"""
import math
import re
import textwrap
from dataclasses import dataclass, field, replace
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from core.schemas import D, DOC_TYPES, Discrepancy, LineItem

WEIGHT_TOLERANCE = Decimal("0.02")          # spread / largest value, strictly below -> advisory
MIN_DESCRIPTION_SIMILARITY = 0.30           # below this two lines are never the same goods
DESCRIPTION_WEIGHT = 0.85                   # the rest of the match score is position (a tie-breaker)

DOC_LABELS = {"invoice": "invoice", "packing_list": "packing list",
              "bill_of_lading": "bill of lading"}

NUMERIC_FIELDS = ["quantity", "unit_price", "value", "net_weight_kg", "gross_weight_kg"]
FIELD_LABELS = {"quantity": "quantity", "unit_price": "unit price", "value": "value",
                "net_weight_kg": "net weight (kg)", "gross_weight_kg": "gross weight (kg)",
                "origin": "origin", "unit": "unit", "currency": "currency",
                "presence": "line present"}

_UNIT_ALIASES = {"bags": "bag", "ctn": "carton", "ctns": "carton", "cartons": "carton",
                 "kgs": "kg", "kilogram": "kg", "kilograms": "kg", "pcs": "pc", "pieces": "pc",
                 "piece": "pc", "mt": "mt", "ton": "mt", "tons": "mt", "tonne": "mt",
                 "tonnes": "mt"}
_COUNTRY_ALIASES = {"prc": "china", "p r china": "china", "pr china": "china",
                    "peoples republic of china": "china", "people s republic of china": "china",
                    "uae": "united arab emirates", "usa": "united states", "us": "united states",
                    "uk": "united kingdom"}
_STOPWORDS = {"a", "an", "and", "the", "of", "in", "for", "with", "x", "per"}


@dataclass
class MatchedLine:
    """One product as it appears across the three documents. A missing document is None."""
    line_no: int
    invoice: Optional[LineItem] = None
    packing_list: Optional[LineItem] = None
    bill_of_lading: Optional[LineItem] = None
    scores: Dict[str, float] = field(default_factory=dict)   # match score per document

    def items(self) -> Dict[str, LineItem]:
        return {d: getattr(self, d) for d in DOC_TYPES if getattr(self, d) is not None}

    @property
    def description(self) -> str:
        return next(iter(self.items().values())).description


# ---------------------------------------------------------------- text and number helpers
def _tokens(text: str) -> set:
    words = re.findall(r"\d+(?:\.\d+)?|[a-z]+", str(text).lower())
    return {w for w in words if w not in _STOPWORDS}


def description_similarity(a: str, b: str, weights: Optional[Dict[str, float]] = None) -> float:
    """0..1. Weighted token overlap, plus a little character similarity for spelling variants.

    `weights` makes words that every line shares ("fresh", "garlic") count for less than the
    words that tell lines apart ("normal" vs "pure", "5.0" vs "5.5"); see _token_weights.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    w = (lambda t: weights.get(t, 1.0)) if weights else (lambda t: 1.0)
    overlap = sum(w(t) for t in ta & tb) / sum(w(t) for t in ta | tb)
    chars = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return 0.8 * overlap + 0.2 * chars


def _token_weights(descriptions: Sequence[str]) -> Dict[str, float]:
    """Inverse document frequency over the lines in play: rare words weigh more."""
    sets = [_tokens(d) for d in descriptions]
    n = len(sets)
    df: Dict[str, int] = {}
    for s in sets:
        for t in s:
            df[t] = df.get(t, 0) + 1
    return {t: math.log(1 + n / c) for t, c in df.items()}


def _position(i: int, n: int) -> float:
    return i / (n - 1) if n > 1 else 0.0


def _norm_unit(unit: str) -> str:
    u = re.sub(r"[^a-z]", "", str(unit).lower())
    return _UNIT_ALIASES.get(u, u)


def _norm_country(origin: str) -> str:
    o = re.sub(r"[^a-z]+", " ", str(origin).lower()).strip()
    return _COUNTRY_ALIASES.get(o, o)


def split_field(field_name: str) -> Tuple[int, str]:
    """'line_2.quantity' -> (2, 'quantity')."""
    head, _, name = field_name.partition(".")
    return int(head.replace("line_", "")), name


# ---------------------------------------------------------------- assignment
def _assign(score: List[List[float]]) -> List[Tuple[int, int]]:
    """Rows to columns, one each, maximising total score (Hungarian algorithm, O(n^3))."""
    n_rows = len(score)
    n_cols = len(score[0]) if n_rows else 0
    if not n_rows or not n_cols:
        return []
    transpose = n_rows > n_cols
    if transpose:
        score = [list(col) for col in zip(*score)]
        n_rows, n_cols = n_cols, n_rows
    cost = [[-s for s in row] for row in score]
    INF = float("inf")
    u, v = [0.0] * (n_rows + 1), [0.0] * (n_cols + 1)
    p, way = [0] * (n_cols + 1), [0] * (n_cols + 1)
    for i in range(1, n_rows + 1):
        p[0], j0 = i, 0
        minv, used = [INF] * (n_cols + 1), [False] * (n_cols + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, n_cols + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n_cols + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    pairs = [(p[j] - 1, j - 1) for j in range(1, n_cols + 1) if p[j]]
    return [(c, r) for r, c in pairs] if transpose else pairs


def _match(groups: List[dict], items: Sequence[LineItem], doc: str) -> None:
    """Attach each line of `doc` to the group it best fits; unmatched lines start new groups."""
    if not items:
        return
    if not groups:
        for i, it in enumerate(items):
            groups.append({"pos": _position(i, len(items)), "items": {doc: it}, "scores": {}})
        return

    pool = [o.description for g in groups for o in g["items"].values()]
    weights = _token_weights(pool + [it.description for it in items])

    def desc_sim(g, it):
        return max(description_similarity(o.description, it.description, weights)
                   for o in g["items"].values())

    sims = [[desc_sim(g, it) for it in items] for g in groups]
    score = [[DESCRIPTION_WEIGHT * sims[gi][ii]
              + (1 - DESCRIPTION_WEIGHT) * (1 - abs(g["pos"] - _position(ii, len(items))))
              for ii in range(len(items))] for gi, g in enumerate(groups)]
    taken = set()
    for gi, ii in _assign(score):
        if sims[gi][ii] >= MIN_DESCRIPTION_SIMILARITY:
            groups[gi]["items"][doc] = items[ii]
            groups[gi]["scores"][doc] = round(score[gi][ii], 3)
            taken.add(ii)
    for ii, it in enumerate(items):
        if ii not in taken:
            groups.append({"pos": _position(ii, len(items)), "items": {doc: it}, "scores": {}})


# ---------------------------------------------------------------- comparison
def _compare(line: MatchedLine, bl_lists_lines: bool) -> List[Discrepancy]:
    out: List[Discrepancy] = []
    items = line.items()
    label = f"Line {line.line_no} ({textwrap.shorten(line.description, 60, placeholder='…')})"

    def add(name, values, severity, note):
        out.append(Discrepancy(field_name=f"line_{line.line_no}.{name}",
                               values={d: str(v) for d, v in values.items()},
                               severity=severity, note=f"{label}: {note}"))

    for doc in ("invoice", "packing_list"):
        if doc not in items:
            add("presence", {d: "yes" if d in items else "no" for d in DOC_TYPES}, "blocking",
                f"not found on the {DOC_LABELS[doc]}.")
    if bl_lists_lines and "bill_of_lading" not in items:
        add("presence", {d: "yes" if d in items else "no" for d in DOC_TYPES}, "advisory",
            "not listed separately on the bill of lading.")

    units = {d: it.unit for d, it in items.items() if it.unit.strip()}
    units_agree = len({_norm_unit(u) for u in units.values()}) <= 1
    if not units_agree:
        add("unit", units, "blocking", "documents count in different units, so quantities "
            "cannot be compared until the unit is settled.")
    currencies = {d: it.currency for d, it in items.items() if it.currency.strip()}
    currencies_agree = len({c.upper() for c in currencies.values()}) <= 1
    if not currencies_agree:
        add("currency", currencies, "blocking", "documents use different currencies.")

    for name in NUMERIC_FIELDS:
        if name == "quantity" and not units_agree:
            continue
        if name in ("unit_price", "value") and not currencies_agree:
            continue
        stated = {d: getattr(it, name) for d, it in items.items() if getattr(it, name) != 0}
        if len(stated) < 2 or len(set(stated.values())) == 1:
            continue
        hi, lo = max(stated.values()), min(stated.values())
        if name in ("net_weight_kg", "gross_weight_kg"):
            spread = (hi - lo) / hi
            pct = (spread * 100).quantize(Decimal("0.01"))
            if spread < WEIGHT_TOLERANCE:
                severity, why = "advisory", f"{FIELD_LABELS[name]} {pct}% apart, under the 2% tolerance"
            else:
                severity, why = "blocking", f"{FIELD_LABELS[name]} {pct}% apart, at or over the 2% tolerance"
        else:
            severity = "advisory" if name == "unit_price" else "blocking"
            why = f"{FIELD_LABELS[name]} differs"
        add(name, stated, severity, f"{why} — {_who_says(stated)}")

    origins = {d: it.origin for d, it in items.items() if it.origin.strip()}
    if len({_norm_country(o) for o in origins.values()}) > 1:
        add("origin", origins, "blocking", f"country of origin differs — {_who_says(origins)}")
    return out


def _who_says(values: Dict[str, object]) -> str:
    parts = [f"{DOC_LABELS[d]} says {v}" for d, v in values.items()]
    return "; ".join(parts) + "."


# ---------------------------------------------------------------- public
def reconcile(invoice: Sequence[LineItem], packing_list: Sequence[LineItem],
              bill_of_lading: Sequence[LineItem]) -> Tuple[List[MatchedLine], List[Discrepancy]]:
    """Match lines across the three documents and list every field they disagree on.

    Matched lines are numbered in invoice order, then any lines only the other documents
    carry. Every Discrepancy starts unresolved; resolved_value is filled at human gate 1.
    """
    groups: List[dict] = []
    for doc, items in (("invoice", invoice), ("packing_list", packing_list),
                       ("bill_of_lading", bill_of_lading)):
        _match(groups, list(items), doc)

    matched = [MatchedLine(line_no=n, scores=g["scores"], **g["items"])
               for n, g in enumerate(groups, start=1)]
    bl_lists_lines = len(bill_of_lading) > 0
    discrepancies = [d for line in matched for d in _compare(line, bl_lists_lines)]
    return matched, discrepancies


def unresolved_blocking(discrepancies: Sequence[Discrepancy]) -> List[Discrepancy]:
    """What still stops human gate 1 from closing."""
    return [d for d in discrepancies if d.severity == "blocking" and d.resolved_value is None]


def resolved_lines(matched: Sequence[MatchedLine],
                   discrepancies: Sequence[Discrepancy]) -> List[LineItem]:
    """One confirmed LineItem per matched line, for classification and duty.

    Starts from the invoice line (the packing list, then the B/L, if the invoice lacks it),
    fills fields the base document leaves blank from the others, then applies every
    resolved_value a person chose at gate 1. Unresolved discrepancies keep the base value.
    """
    resolutions = {split_field(d.field_name): d.resolved_value
                   for d in discrepancies if d.resolved_value is not None}
    out = []
    for line in matched:
        items = line.items()
        base = items.get("invoice") or items.get("packing_list") or items["bill_of_lading"]
        merged = replace(base, line_no=line.line_no, verified=False, verify_note="")
        for name in NUMERIC_FIELDS + ["unit", "currency", "origin"]:
            if getattr(merged, name) in (0, ""):
                for it in items.values():
                    if getattr(it, name) not in (0, ""):
                        setattr(merged, name, getattr(it, name))
                        break
            chosen = resolutions.get((line.line_no, name))
            if chosen is not None:
                setattr(merged, name, D(chosen) if name in NUMERIC_FIELDS else chosen)
        out.append(merged)
    return out
