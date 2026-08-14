# -*- coding: utf-8 -*-
"""
购买记录与发票匹配模块

规则：
- 用户手动记录购买项
- 匹配时只比较最终价格（含快递费）
- 一笔购买对应一张发票
"""

from __future__ import annotations

import sqlite3
from datetime import datetime



def init_purchase_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            item_price REAL NOT NULL,
            has_shipping INTEGER NOT NULL DEFAULT 0,
            shipping_fee REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            matched_invoice TEXT
        )
        """
    )


def add_purchase(conn, name: str, item_price: float, has_shipping: bool, shipping_fee: float = 0):
    conn.execute(
        """
        INSERT INTO purchases(
            name,item_price,has_shipping,shipping_fee,created_at
        ) VALUES(?,?,?,?,?)
        """,
        (
            name,
            item_price,
            1 if has_shipping else 0,
            shipping_fee if has_shipping else 0,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def delete_purchase(conn, purchase_id: int):
    conn.execute("DELETE FROM purchases WHERE id=?", (purchase_id,))


def clear_purchases(conn):
    conn.execute("DELETE FROM purchases")


def purchase_total(row):
    return round(row["item_price"] + row["shipping_fee"], 2) if row["has_shipping"] else round(row["item_price"], 2)


def match_purchases(conn):
    """
    返回：
    matched: 已找到对应发票的购买记录
    unmatched: 有购买记录但没有发票
    unused_invoice: 有发票但没有对应购买记录
    """
    purchases = conn.execute(
        "SELECT * FROM purchases ORDER BY id"
    ).fetchall()
    invoices = conn.execute(
        "SELECT * FROM invoices WHERE active=1 ORDER BY digest"
    ).fetchall()

    used = set()
    matched = []
    unmatched = []

    for p in purchases:
        target = purchase_total(p)
        found = None
        for inv in invoices:
            if inv["digest"] in used:
                continue
            if inv["total"] is not None and round(inv["total"], 2) == target:
                found = inv
                break

        if found:
            used.add(found["digest"])
            matched.append((p, found))
        else:
            unmatched.append(p)

    unused_invoice = [x for x in invoices if x["digest"] not in used]

    return matched, unmatched, unused_invoice
