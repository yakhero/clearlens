"""Tests for the tariff parser. Every expected value below was read by eye off the PDF page
named in the comment (data/tariff/pakistan_customs_tariff_2026-27.pdf), not off the CSV."""
import csv
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import tariff  # noqa: E402

# (pct_code, CD %, the line's own text as printed, PDF page)
VERIFIED = [
    ("0101.2100", "0", "Pure-bred breeding animals", 1),
    ("0105.9400", "5", "Fowls of the species Gallus domesticus (chicken)", 1),
    ("0303.1200", "20", "excluding livers and roes:", 6),
    ("0802.8000", "20", "Areca nuts", 21),
    ("0802.5200", "0", "Shelled", 21),
    ("0805.2910", "20", "Kino (fresh)", 21),
    ("2528.0000", "0", "calculated on the dry weight.", 45),
    ("2529.2200", "0", "Containing by weight more than 97 % of calcium fluoride", 45),
    ("3004.9092", "10", "Paracetamol", 80),
    ("3006.1010", "0", "Vascular grafts", 80),
    ("6205.2010", "20", "Baluchi kameez", 159),
    ("7211.2920", "0", "Cold rolled steel strips of thickness below 0.5 mm and upto 100 mm wide", 183),
    ("7212.1000", "20", "Plated or coated with tin", 183),
    ("8544.6010", "20", "For a voltage exceeding 1,000 V but not exceeding 72,000 V", 261),
    ("8544.6020", "20", "Photovoltaic power cable having single conductor of kind used for solar", 261),
    ("8545.9020", "0", "For dry battery cells", 261),
    ("8703.2113", "30", "Mini Vans (CBU)", 265),
    ("8703.2191", "35", "in any kit form excluding those of heading 8703.2193 and 8703.2195", 265),
    ("8802.4000", "0", "Aeroplanes and other aircraft, of an unladen weight exceeding 15,000 kg", 304),
    ("8806.9200", "0", "With maximum take-off weight more than 250 g but not more than 7 kg", 304),
    ("9612.1010", "20", "For dot matrix printer", 326),
    ("9613.9000", "0", "Parts", 326),
    ("9619.0010", "10", "Napkins (diapers) for adults (patients) of weight exceeding 25 kg", 326),
    ("9620.0000", "15", "Monopods, bipods, tripods and similar articles", 326),
]


def test_verified_codes_rate_and_text():
    for code, rate, own_text, page in VERIFIED:
        row = tariff.lookup(code)
        assert row, f"{code} (page {page}) missing from pct_codes.csv"
        assert row["cd_rate"] == rate, f"{code} page {page}: {row['cd_rate']} != {rate}"
        assert row["description"].endswith(own_text), f"{code} page {page}: {row['description']!r}"
        assert row["chapter"] == code[:2]
        assert row["heading"] == f"{code[:2]}.{code[2:4]}"


def test_verified_codes_span_many_chapters():
    assert len({c[:2] for c, *_ in VERIFIED}) >= 10


def test_multiline_description_whales():
    """Page 1: 0106.1200 wraps over four printed lines."""
    d = tariff.lookup("0106.1200")["description"]
    assert d == ("Other live animals. > Mammals: > Whales, dolphins and porpoises (mammals of the "
                 "order Cetacea); manatees and dugongs (mammals of the order Sirenia); seals,sea "
                 "lions and walruses (mammals of the suborder Pinnipedia)")


def test_multiline_description_pacific_salmon():
    """Page 6: 0303.1200 wraps over five printed lines, under a two-line subheading."""
    d = tariff.lookup("0303.1200")["description"]
    assert d.startswith("Fish, frozen, excluding fish fillets and other fish meat of heading 03.04."
                        " > Salmonidae, excluding edible fish offal of subheading 0303.91 to 0303.99:"
                        " > Other Pacific salmon (Oncorhynchus gorbusha,")
    assert "Oncorhynchus masou and Oncorhynchus rhodurus )" in d
    assert tariff.lookup("0303.1200")["cd_rate"] == "20"


def test_multiline_parent_subheading():
    """Page 21: 'Kino (fresh)' only makes sense under a subheading that wraps two lines."""
    assert tariff.lookup("0805.2910")["description"] == (
        "Citrus fruit, fresh or dried. > Mandarins (including tangerines and satsumas); "
        "clementines,wilkings and similar citrus hybrids: > Other: > Kino (fresh)")


def test_other_inherits_its_parents():
    """'- - Other' is meaningless alone; the path says which Other."""
    assert tariff.lookup("0101.2900")["description"] == \
        "Live horses, asses, mules and hinnies. > Horses: > Other"
    assert tariff.lookup("0102.2910")["description"] == \
        "Live bovine animals. > Cattle: > Other: > Bulls"


def test_deep_hierarchy_vehicles():
    """Page 265: four levels of subheading between 87.03 and the tariff line."""
    d = tariff.lookup("8703.2113")["description"]
    assert d.split(" > ")[1:] == [
        "Other vehicles, with only spark-ignition internal combustion piston engine:",
        "Of a cylinder capacity not exceeding 1,000cc:",
        "Of a cylinder capacity not exceeding 850cc:",
        "Mini Vans (CBU)",
    ]


def test_single_line_heading_is_its_own_description():
    """Page 2: 0205.0000 has no 02.05 heading row; the code row is the heading."""
    row = tariff.lookup("0205.0000")
    assert row["cd_rate"] == "20"
    assert row["description"] == "Meat of horses, asses, mules or hinnies, fresh, chilled or frozen."


def test_specific_duty_is_not_a_percentage():
    """Page 30: CD on 1507.1000 is 'Rs. 10550/MT'. It must not appear as a percentage."""
    assert tariff.lookup("1507.1000") == {}
    report = tariff.REPORT_PATH.read_text(encoding="utf-8")
    assert "1507.1000" in report and "not an ad valorem percentage" in report


def test_line_without_its_heading_is_skipped_not_guessed():
    """Page 25: the 11.03 heading row is missing from the PDF, so 1103.1100 is reported."""
    assert tariff.lookup("1103.1100") == {}
    assert "1103.1100" in tariff.REPORT_PATH.read_text(encoding="utf-8")


def test_chapter_99_is_not_in_the_csv():
    assert not any(r["chapter"] == "99" for r in tariff.load())


def test_lookup_normalises_input():
    assert tariff.lookup("08028000")["pct_code"] == "0802.8000"
    assert tariff.lookup(" 0802 8000 ")["pct_code"] == "0802.8000"
    assert tariff.lookup("0802.80") == {}
    assert tariff.lookup("9999.9999") == {}


def test_cd_fraction_feeds_duty_engine():
    assert tariff.cd_fraction(tariff.lookup("0802.8000")) == Decimal("0.2")


def test_search_text_and_code_prefix():
    assert tariff.search("areca nuts")[0]["pct_code"] == "0802.8000"
    assert "8703.2113" in [r["pct_code"] for r in tariff.search("mini vans cbu")]
    assert [r["pct_code"] for r in tariff.search("0804.50")] == [
        "0804.5010", "0804.5020", "0804.5030", "0804.5040", "0804.5050", "0804.5090"]
    assert tariff.search("") == []


def test_csv_shape_and_every_rate_is_a_number():
    with open(tariff.CSV_PATH, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == tariff.COLUMNS
        rows = list(reader)
    assert len(rows) > 7000
    codes = [r["pct_code"] for r in rows]
    assert len(codes) == len(set(codes))
    for r in rows:
        Decimal(r["cd_rate"])                       # raises if a rate is not numeric
        assert r["description"]


def test_committed_csv_matches_a_fresh_parse():
    """The CSV in the repo must be exactly what the parser produces from the PDF."""
    rows, _, _ = tariff.parse()
    assert [dict(r) for r in tariff.load()] == rows
