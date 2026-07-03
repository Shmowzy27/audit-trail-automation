"""Regression tests for chronological ordering of standardized packet names.

Guards BUG-016: MM-YY filenames must be ordered by real (year, month), not by
their month-first text form.
"""

from app.pdf_order import pdf_sort_key


def _order(names, folder="keyrenter history"):
    paths = [f"D:/x/{folder}/{n}" for n in names]
    return [p.split("/")[-1] for p in sorted(paths, key=pdf_sort_key)]


def test_mm_yy_sorts_chronologically_not_month_first():
    # As plain text these sort 04-22, 04-23, 05-21 (month-first). By date the
    # correct order is 05-21, 04-22, 04-23.
    names = [
        "Owner Packet - Keyrenter - 04-23 - 1426.39.pdf",
        "Owner Packet - Keyrenter - 05-21 - 1270.05.pdf",
        "Owner Packet - Keyrenter - 04-22 - 2945.00.pdf",
    ]
    assert _order(names) == [
        "Owner Packet - Keyrenter - 05-21 - 1270.05.pdf",
        "Owner Packet - Keyrenter - 04-22 - 2945.00.pdf",
        "Owner Packet - Keyrenter - 04-23 - 1426.39.pdf",
    ]


def test_year_rolls_over_correctly():
    names = [
        "Owner Packet - Keyrenter - 01-22 - 3105.00.pdf",
        "Owner Packet - Keyrenter - 12-21 - 3105.00.pdf",
        "Owner Packet - Keyrenter - 02-22 - 3005.00.pdf",
    ]
    assert _order(names) == [
        "Owner Packet - Keyrenter - 12-21 - 3105.00.pdf",
        "Owner Packet - Keyrenter - 01-22 - 3105.00.pdf",
        "Owner Packet - Keyrenter - 02-22 - 3005.00.pdf",
    ]


def test_same_month_breaks_by_amount():
    names = [
        "Owner Packet - Keyrenter - 09-21 - 1187.55.pdf",
        "Owner Packet - Keyrenter - 09-21 - 709.47.pdf",
    ]
    assert _order(names) == [
        "Owner Packet - Keyrenter - 09-21 - 709.47.pdf",
        "Owner Packet - Keyrenter - 09-21 - 1187.55.pdf",
    ]


def test_unstandardized_name_sorts_last():
    names = [
        "random scan.pdf",
        "Owner Packet - Keyrenter - 04-21 - 1012.50.pdf",
    ]
    assert _order(names)[-1] == "random scan.pdf"


def test_grouped_by_folder():
    key_kr = pdf_sort_key("D:/x/keyrenter history/Owner Packet - Keyrenter - 04-21 - 1.00.pdf")
    key_nv = pdf_sort_key("D:/x/nashville history/Owner Packet - Nashville - 04-21 - 1.00.pdf")
    # Folder is the primary sort field, so all of one folder precedes the other.
    assert key_kr[0] != key_nv[0]
