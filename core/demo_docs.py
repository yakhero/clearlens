"""Render data/consignments/mock.json as three realistic PDFs, so the pipeline runs end to end
without waiting on real documents.

    python -m core.demo_docs        # writes data/consignments/demo_*.pdf

The numbers come straight from mock.json, so the PDFs carry the same two planted mismatches
(packing list: 1,180 bags instead of 1,200 on normal white garlic; 2,950 kg net instead of
3,000 kg on garlic flakes). Every page is footed as a fictional demo document.
"""
import json
from decimal import Decimal
from pathlib import Path
from typing import List, Sequence

ROOT = Path(__file__).resolve().parents[1]
MOCK_PATH = ROOT / "data" / "consignments" / "mock.json"
OUT_DIR = ROOT / "data" / "consignments"
FILES = {"invoice": "demo_invoice.pdf", "packing_list": "demo_packing_list.pdf",
         "bill_of_lading": "demo_bill_of_lading.pdf"}
FOOTER = ("FICTIONAL DEMO DOCUMENT generated for ClearLens testing from "
          "data/consignments/mock.json - not a real shipment, not for any customs use.")
INK = (0.1, 0.1, 0.12)
RULE = (0.35, 0.35, 0.4)
SHADE = (0.92, 0.93, 0.95)


def _num(v, places: int = 0) -> str:
    d = Decimal(str(v))
    return f"{d:,.{places}f}"


class _Page:
    def __init__(self, doc, landscape: bool):
        import pymupdf
        w, h = pymupdf.paper_size("a4-l" if landscape else "a4")
        self.p = doc.new_page(width=w, height=h)
        self.w, self.h = w, h

    def text(self, x, y, s, size=8.5, bold=False, color=INK):
        self.p.insert_text((x, y), str(s), fontsize=size, color=color,
                           fontname="hebo" if bold else "helv")

    def right(self, x_right, y, s, size=8.5, bold=False):
        import pymupdf
        width = pymupdf.get_text_length(str(s), fontname="hebo" if bold else "helv",
                                        fontsize=size)
        self.text(x_right - width, y, s, size, bold)

    def box(self, x0, y0, x1, y1, fill=None, width=0.6):
        import pymupdf
        self.p.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=RULE, fill=fill, width=width)

    def hline(self, x0, x1, y, width=0.6):
        self.p.draw_line((x0, y), (x1, y), color=RULE, width=width)

    def block(self, x, y, title, lines: Sequence[str], size=8.5):
        self.text(x, y, title, 7, bold=True, color=(0.35, 0.35, 0.4))
        for i, line in enumerate(lines):
            self.text(x, y + 12 + i * 11, line, size)

    def footer(self, page_no: int, pages: int):
        self.hline(36, self.w - 36, self.h - 34, 0.4)
        self.text(36, self.h - 22, FOOTER, 6.5, color=(0.45, 0.45, 0.5))
        self.right(self.w - 36, self.h - 22, f"Page {page_no} of {pages}", 6.5)


def _table(pg: _Page, top: float, cols: List[tuple], rows: List[list], totals: list = None):
    """cols: (header, x_left, x_right, align). Each row prints on one text line."""
    x0, x1 = cols[0][1] - 4, cols[-1][2] + 4
    pg.box(x0, top, x1, top + 18, fill=SHADE)
    for head, left, right, align in cols:
        (pg.right(right, top + 12, head, 7.5, True) if align == "r"
         else pg.text(left, top + 12, head, 7.5, True))
    y = top + 18
    for row in rows + ([totals] if totals else []):
        bold = row is totals
        pg.hline(x0, x1, y + 20)
        for (head, left, right, align), cell in zip(cols, row):
            (pg.right(right, y + 13, cell, 8.5, bold) if align == "r"
             else pg.text(left, y + 13, cell, 8.5, bold))
        y += 20
    pg.box(x0, top, x1, y, width=0.8)
    for _, left, _, _ in cols[1:]:
        pg.p.draw_line((left - 5, top), (left - 5, y), color=RULE, width=0.4)
    return y


def _lines(data: dict, doc: str) -> List[dict]:
    return [l for l in data["lines"] if l["source_doc"] == doc]


def invoice(data: dict) -> bytes:
    import pymupdf
    m, lines = data["_meta"], _lines(data, "invoice")
    doc = pymupdf.open()
    pg = _Page(doc, landscape=True)
    pg.text(36, 48, "COMMERCIAL INVOICE", 16, bold=True)
    pg.right(pg.w - 36, 36, f"Invoice No.: {m['invoice_no']}", 9, True)
    pg.right(pg.w - 36, 52, "Date: 17 September 2026", 9)
    pg.right(pg.w - 36, 68, "L/C No.: DEMO-LC-0442-26", 9)
    pg.hline(36, pg.w - 36, 76, 1)
    pg.block(36, 92, "EXPORTER / SELLER", [m["exporter"], "Jinxiang County, Jining, Shandong",
                                          "Tel +86 000 0000 0000 (demo)"])
    pg.block(300, 92, "CONSIGNEE / BUYER", [m["importer"], "Plot 00, Sector 0, Korangi",
                                           "Karachi, Pakistan  NTN 0000000-0 (demo)"])
    pg.block(564, 92, "SHIPMENT", [f"From: {m['port_of_loading']}",
                                   f"To: {m['port_of_discharge']}",
                                   f"Vessel: {m['vessel_voyage']}",
                                   f"Terms: {lines[0]['incoterm']} QINGDAO   Payment: L/C at sight"])
    cols = [("No.", 40, 60, "l"), ("Description of Goods", 66, 400, "l"),
            ("Quantity", 406, 486, "r"), ("Unit Price (USD)", 492, 572, "r"),
            ("Amount (USD)", 578, 658, "r"), ("N.W. (KG)", 664, 730, "r"),
            ("G.W. (KG)", 736, 802, "r")]
    rows = [[str(l["line_no"]), l["description"], f"{_num(l['quantity'])} {l['unit']}",
             _num(l["unit_price"], 2), _num(l["value"], 2), _num(l["net_weight_kg"]),
             _num(l["gross_weight_kg"])] for l in lines]
    tot = ["", "TOTAL", f"{_num(sum(Decimal(l['quantity']) for l in lines))} PKGS", "",
           _num(sum(Decimal(l["value"]) for l in lines), 2),
           _num(sum(Decimal(l["net_weight_kg"]) for l in lines)),
           _num(sum(Decimal(l["gross_weight_kg"]) for l in lines))]
    y = _table(pg, 168, cols, rows, tot)
    pg.text(40, y + 22, "SAY US DOLLARS TWENTY EIGHT THOUSAND SIX HUNDRED ONLY.", 8.5, True)
    pg.text(40, y + 40, f"Country of origin: {lines[0]['origin']}", 8.5)
    pg.text(40, y + 54, "Shipping marks: IVF / KARACHI / GARLIC / MADE IN CHINA", 8.5)
    pg.text(40, y + 68, "We certify that this invoice is true and correct and that the goods "
                        "are of Chinese origin.", 8.5)
    pg.text(600, y + 100, "For and on behalf of the seller", 8)
    pg.hline(600, 800, y + 130, 0.5)
    pg.text(600, y + 141, "Authorised signature (demo)", 7.5)
    pg.footer(1, 1)
    return doc.tobytes()


def packing_list(data: dict) -> bytes:
    import pymupdf
    m, lines = data["_meta"], _lines(data, "packing_list")
    doc = pymupdf.open()
    pg = _Page(doc, landscape=True)
    pg.text(36, 48, "PACKING LIST", 16, bold=True)
    pg.right(pg.w - 36, 36, f"Ref. Invoice No.: {m['invoice_no']}", 9, True)
    pg.right(pg.w - 36, 52, "Date: 17 September 2026", 9)
    pg.hline(36, pg.w - 36, 64, 1)
    pg.block(36, 80, "SHIPPER", [m["exporter"]])
    pg.block(300, 80, "CONSIGNEE", [m["importer"]])
    pg.block(564, 80, "CONTAINER", [m["container"], "Seal: DEMO-889120",
                                    "Temperature set: -1 C"])
    cols = [("No.", 40, 60, "l"), ("Description of Goods", 66, 430, "l"),
            ("Packages", 436, 520, "r"), ("N.W. (KG)", 526, 610, "r"),
            ("G.W. (KG)", 616, 700, "r"), ("Origin", 712, 800, "l")]
    rows = [[str(l["line_no"]), l["description"], f"{_num(l['quantity'])} {l['unit']}",
             _num(l["net_weight_kg"], 2), _num(l["gross_weight_kg"], 2), l["origin"]]
            for l in lines]
    tot = ["", "TOTAL", f"{_num(sum(Decimal(l['quantity']) for l in lines))} PKGS",
           _num(sum(Decimal(l["net_weight_kg"]) for l in lines), 2),
           _num(sum(Decimal(l["gross_weight_kg"]) for l in lines), 2), ""]
    y = _table(pg, 140, cols, rows, tot)
    pg.text(40, y + 22, "Mesh bags: 10 kg net each. Cartons: 20 kg net each, lined with "
                        "food-grade PE bag.", 8.5)
    pg.text(40, y + 36, "Shipping marks: IVF / KARACHI / GARLIC / MADE IN CHINA", 8.5)
    pg.footer(1, 1)
    return doc.tobytes()


def bill_of_lading(data: dict) -> bytes:
    import pymupdf
    m, lines = data["_meta"], _lines(data, "bill_of_lading")
    doc = pymupdf.open()
    pg = _Page(doc, landscape=False)
    pg.text(36, 46, "BILL OF LADING", 16, bold=True)
    pg.text(36, 60, "FOR PORT-TO-PORT OR COMBINED TRANSPORT  -  ORIGINAL", 8)
    pg.right(pg.w - 36, 46, f"B/L No.: {m['bl_no']}", 10, True)
    pg.right(pg.w - 36, 60, "DEMO OCEAN LINES (fictional carrier)", 8)
    boxes = [
        (36, 72, 300, 132, "SHIPPER", [m["exporter"], "Jinxiang County, Shandong, China"]),
        (300, 72, 559, 132, "BOOKING / EXPORT REFERENCES", [f"Invoice {m['invoice_no']}",
                                                            "Booking DEMO-QD-77102"]),
        (36, 132, 300, 192, "CONSIGNEE", ["TO THE ORDER OF DEMO BANK LIMITED, KARACHI"]),
        (300, 132, 559, 192, "NOTIFY PARTY", [m["importer"]]),
        (36, 192, 300, 222, "VESSEL / VOYAGE", [m["vessel_voyage"]]),
        (300, 192, 559, 222, "PORT OF LOADING", [m["port_of_loading"]]),
        (36, 222, 300, 252, "PORT OF DISCHARGE", [m["port_of_discharge"]]),
        (300, 222, 559, 252, "PLACE OF DELIVERY", ["Karachi CY"]),
    ]
    for x0, y0, x1, y1, title, body in boxes:
        pg.box(x0, y0, x1, y1)
        pg.block(x0 + 5, y0 + 10, title, body, 8)
    pg.text(40, 270, "PARTICULARS FURNISHED BY SHIPPER", 7, True)
    cols = [("Packages", 40, 110, "r"), ("Description of Goods", 118, 400, "l"),
            ("Gross Weight (KG)", 406, 486, "r"), ("Measurement", 492, 555, "r")]
    cbm = {"BAGS": Decimal("0.032"), "CARTONS": Decimal("0.045")}
    rows = [[f"{_num(l['quantity'])} {l['unit']}", l["description"],
             _num(l["gross_weight_kg"], 3),
             f"{_num(Decimal(l['quantity']) * cbm.get(l['unit'], 0), 3)} CBM"] for l in lines]
    tot = [f"{_num(sum(Decimal(l['quantity']) for l in lines))} PKGS", "TOTAL",
           _num(sum(Decimal(l["gross_weight_kg"]) for l in lines), 3), ""]
    y = _table(pg, 278, cols, rows, tot)
    notes = [f"ORIGIN: {lines[0]['origin']}",
             f"CONTAINER / SEAL: {m['container']} / DEMO-889120",
             "SHIPPER'S LOAD, STOW AND COUNT - SAID TO CONTAIN",
             "REEFER CARGO, CARRYING TEMPERATURE -1 C",
             "FREIGHT COLLECT", "SHIPPED ON BOARD 19 SEPTEMBER 2026, QINGDAO"]
    for i, note in enumerate(notes):
        pg.text(118, y + 20 + i * 12, note, 8.5)
    pg.text(40, pg.h - 110, "Number of original B/Ls: THREE (3)", 8)
    pg.text(330, pg.h - 110, "Place and date of issue: Qingdao, 19 September 2026", 8)
    pg.hline(330, 559, pg.h - 70, 0.5)
    pg.text(330, pg.h - 60, "Signed for the carrier, as agent (demo)", 7.5)
    pg.footer(1, 2)

    terms = _Page(doc, landscape=False)
    terms.text(36, 48, "TERMS AND CONDITIONS OF CARRIAGE (abridged, fictional)", 11, True)
    clauses = [
        "1. Definitions. 'Carrier' means Demo Ocean Lines. 'Merchant' includes the shipper, "
        "consignee, holder of this B/L and owner of the goods.",
        "2. Carrier's tariff. The terms of the carrier's applicable tariff are incorporated.",
        "3. Shipper's load, stow and count. Where the container was packed by the shipper the "
        "carrier is not liable for the condition or quantity of contents.",
        "4. Reefer cargo. The carrier is to keep the temperature set as requested by the "
        "shipper within the tolerance of the equipment.",
        "5. Freight. Freight shall be deemed earned on receipt of the goods and is payable as "
        "stated on the face of this B/L.",
        "6. Law and jurisdiction. These terms are fictional and for software testing only.",
    ]
    y = 74
    for clause in clauses:
        words, line = clause.split(), ""
        for w in words:
            if len(line) + len(w) > 105:
                terms.text(36, y, line, 8)
                y, line = y + 11, ""
            line = f"{line} {w}".strip()
        terms.text(36, y, line, 8)
        y += 18
    terms.footer(2, 2)
    return doc.tobytes()


BUILDERS = {"invoice": invoice, "packing_list": packing_list, "bill_of_lading": bill_of_lading}


def build_all(out_dir: Path = OUT_DIR, mock_path: Path = MOCK_PATH) -> dict:
    data = json.loads(mock_path.read_text(encoding="utf-8"))
    paths = {}
    for doc_type, build in BUILDERS.items():
        path = out_dir / FILES[doc_type]
        path.write_bytes(build(data))
        paths[doc_type] = path
    return paths


def load_demo_pdfs(out_dir: Path = OUT_DIR) -> dict:
    """{'invoice': bytes, ...} from the committed demo PDFs."""
    return {d: (out_dir / name).read_bytes() for d, name in FILES.items()}


if __name__ == "__main__":
    for doc_type, path in build_all().items():
        print(f"{doc_type:<15} {path.relative_to(ROOT)}")
