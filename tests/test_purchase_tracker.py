import sqlite3

from purchase_tracker import add_purchase, init_purchase_table, match_purchases


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE invoices (
            digest TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            total REAL,
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    init_purchase_table(conn)
    return conn


def add_invoice(conn, digest, filename, total):
    conn.execute(
        "INSERT INTO invoices(digest, filename, total, active) VALUES(?,?,?,1)",
        (digest, filename, total),
    )


def test_product_and_shipping_are_matched_separately():
    conn = make_db()
    add_purchase(conn, "背包", "74.00", True, "6.00")
    add_invoice(conn, "a", "商品.pdf", 74.00)
    add_invoice(conn, "b", "快递费.pdf", 6.00)

    result = match_purchases(conn)

    purchase = result["purchase_results"][0]
    assert purchase["complete"] is True
    matched = {x["kind"]: x["invoice"]["filename"] for x in purchase["components"]}
    assert matched == {"商品": "商品.pdf", "快递费": "快递费.pdf"}


def test_one_invoice_cannot_match_two_purchases():
    conn = make_db()
    add_purchase(conn, "数据线A", "7.00", False, 0)
    add_purchase(conn, "数据线B", "7.00", False, 0)
    add_invoice(conn, "a", "唯一7元发票.pdf", 7.00)

    result = match_purchases(conn)

    complete_count = sum(1 for x in result["purchase_results"] if x["complete"])
    assert complete_count == 1
    assert len(result["missing_components"]) == 1


def test_unused_invoice_is_reported():
    conn = make_db()
    add_purchase(conn, "数据线", "7.00", False, 0)
    add_invoice(conn, "a", "7元.pdf", 7.00)
    add_invoice(conn, "b", "99元.pdf", 99.00)

    result = match_purchases(conn)

    assert [row["filename"] for row in result["unused_invoices"]] == ["99元.pdf"]
