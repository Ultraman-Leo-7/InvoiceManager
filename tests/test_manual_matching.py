import sqlite3

import pytest

from purchase_tracker import (
    add_purchase,
    clear_manual_match,
    init_purchase_table,
    match_purchases,
    set_manual_match,
)


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE invoices (
            digest TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            total REAL,
            project TEXT,
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    init_purchase_table(conn)
    return conn


def add_invoice(conn, digest, filename, total):
    conn.execute(
        "INSERT INTO invoices(digest, filename, total, project, active) VALUES(?,?,?,?,1)",
        (digest, filename, total, "测试项目"),
    )


def test_manual_override_reserves_invoice_before_auto_matching():
    conn = make_db()
    first = add_purchase(conn, "数据线A", "7.00", False, 0)
    second = add_purchase(conn, "数据线B", "7.00", False, 0)
    add_invoice(conn, "a", "A.pdf", 7.00)
    add_invoice(conn, "b", "B.pdf", 7.00)

    set_manual_match(conn, second, "商品", "a")
    result = match_purchases(conn)

    by_id = {int(x["purchase"]["id"]): x for x in result["purchase_results"]}
    assert by_id[second]["components"][0]["invoice"]["filename"] == "A.pdf"
    assert by_id[second]["components"][0]["manual"] is True
    assert by_id[first]["components"][0]["invoice"]["filename"] == "B.pdf"
    assert by_id[first]["components"][0]["manual"] is False


def test_manual_override_can_intentionally_use_different_amount():
    conn = make_db()
    purchase_id = add_purchase(conn, "特殊订单", "7.00", False, 0)
    add_invoice(conn, "x", "99.pdf", 99.00)

    set_manual_match(conn, purchase_id, "商品", "x")
    result = match_purchases(conn)

    component = result["purchase_results"][0]["components"][0]
    assert component["matched"] is True
    assert component["manual"] is True
    assert component["invoice"]["filename"] == "99.pdf"


def test_clearing_override_returns_to_automatic_matching():
    conn = make_db()
    purchase_id = add_purchase(conn, "数据线", "7.00", False, 0)
    add_invoice(conn, "a", "7.pdf", 7.00)
    add_invoice(conn, "b", "99.pdf", 99.00)

    set_manual_match(conn, purchase_id, "商品", "b")
    clear_manual_match(conn, purchase_id, "商品")
    result = match_purchases(conn)

    component = result["purchase_results"][0]["components"][0]
    assert component["manual"] is False
    assert component["invoice"]["filename"] == "7.pdf"


def test_one_invoice_cannot_have_two_manual_owners():
    conn = make_db()
    first = add_purchase(conn, "A", "7.00", False, 0)
    second = add_purchase(conn, "B", "7.00", False, 0)
    add_invoice(conn, "a", "A.pdf", 7.00)

    set_manual_match(conn, first, "商品", "a")
    with pytest.raises(ValueError):
        set_manual_match(conn, second, "商品", "a")
