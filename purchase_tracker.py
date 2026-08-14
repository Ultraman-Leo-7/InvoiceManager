# -*- coding: utf-8 -*-
"""
购买记录与发票匹配模块。

匹配规则按用户需求保持简单：只比较价格，精确到分，一模一样就算匹配。

重要：京东商品金额和快递费是分别开发票的，因此：
- 无快递费：一条购买记录需要 1 张“商品价格”发票；
- 有快递费：一条购买记录需要 2 张发票，分别匹配“商品价格”和“快递费”；
- 每张发票最多匹配一次，每个购买组成项也最多匹配一张发票。

购买记录另外维护 append-only 审计历史，用于误删/异常情况下的恢复和排查。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def init_purchase_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            item_price REAL NOT NULL,
            has_shipping INTEGER NOT NULL DEFAULT 0,
            shipping_fee REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )

    # 兼容上一版已经创建的表：如果缺 updated_at，就补上。
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(purchases)").fetchall()
    }
    if "updated_at" not in columns:
        conn.execute(
            "ALTER TABLE purchases ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
        )

    # 购买记录审计历史：正常界面不依赖它，但它为恢复/排查提供第二份证据。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            name TEXT NOT NULL,
            item_price REAL NOT NULL,
            has_shipping INTEGER NOT NULL,
            shipping_fee REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            audited_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchase_audit_purchase_id ON purchase_audit(purchase_id, audit_id)"
    )

    # SQLite triggers ensure history is written even if a future caller changes data
    # without going through the normal Python helper functions.
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS trg_purchases_audit_insert
        AFTER INSERT ON purchases
        BEGIN
            INSERT INTO purchase_audit(
                purchase_id, action, name, item_price, has_shipping,
                shipping_fee, created_at, updated_at
            ) VALUES(
                NEW.id, 'INSERT', NEW.name, NEW.item_price, NEW.has_shipping,
                NEW.shipping_fee, NEW.created_at, NEW.updated_at
            );
        END;

        CREATE TRIGGER IF NOT EXISTS trg_purchases_audit_update
        BEFORE UPDATE ON purchases
        BEGIN
            INSERT INTO purchase_audit(
                purchase_id, action, name, item_price, has_shipping,
                shipping_fee, created_at, updated_at
            ) VALUES(
                OLD.id, 'UPDATE_BEFORE', OLD.name, OLD.item_price, OLD.has_shipping,
                OLD.shipping_fee, OLD.created_at, OLD.updated_at
            );
        END;

        CREATE TRIGGER IF NOT EXISTS trg_purchases_audit_update_after
        AFTER UPDATE ON purchases
        BEGIN
            INSERT INTO purchase_audit(
                purchase_id, action, name, item_price, has_shipping,
                shipping_fee, created_at, updated_at
            ) VALUES(
                NEW.id, 'UPDATE_AFTER', NEW.name, NEW.item_price, NEW.has_shipping,
                NEW.shipping_fee, NEW.created_at, NEW.updated_at
            );
        END;

        CREATE TRIGGER IF NOT EXISTS trg_purchases_audit_delete
        BEFORE DELETE ON purchases
        BEGIN
            INSERT INTO purchase_audit(
                purchase_id, action, name, item_price, has_shipping,
                shipping_fee, created_at, updated_at
            ) VALUES(
                OLD.id, 'DELETE', OLD.name, OLD.item_price, OLD.has_shipping,
                OLD.shipping_fee, OLD.created_at, OLD.updated_at
            );
        END;
        """
    )


def _money_to_cents(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError, TypeError):
        return None
    return int(amount * 100)


def validate_purchase(
    name: str,
    item_price,
    has_shipping: bool,
    shipping_fee=0,
) -> tuple[str, float, bool, float]:
    name = str(name or "").strip()
    if not name:
        raise ValueError("名称不能为空")

    item_cents = _money_to_cents(item_price)
    if item_cents is None or item_cents < 0:
        raise ValueError("商品价格必须是大于等于 0 的数字")

    shipping_cents = _money_to_cents(shipping_fee)
    if has_shipping:
        if shipping_cents is None or shipping_cents < 0:
            raise ValueError("勾选快递费后，快递费必须是大于等于 0 的数字")
    else:
        shipping_cents = 0

    return (
        name,
        item_cents / 100,
        bool(has_shipping),
        shipping_cents / 100,
    )


def add_purchase(
    conn: sqlite3.Connection,
    name: str,
    item_price,
    has_shipping: bool,
    shipping_fee=0,
) -> int:
    name, item_price, has_shipping, shipping_fee = validate_purchase(
        name, item_price, has_shipping, shipping_fee
    )
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """
        INSERT INTO purchases(
            name, item_price, has_shipping, shipping_fee, created_at, updated_at
        ) VALUES(?,?,?,?,?,?)
        """,
        (
            name,
            item_price,
            1 if has_shipping else 0,
            shipping_fee,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def update_purchase(
    conn: sqlite3.Connection,
    purchase_id: int,
    name: str,
    item_price,
    has_shipping: bool,
    shipping_fee=0,
) -> None:
    name, item_price, has_shipping, shipping_fee = validate_purchase(
        name, item_price, has_shipping, shipping_fee
    )
    conn.execute(
        """
        UPDATE purchases
        SET name=?, item_price=?, has_shipping=?, shipping_fee=?, updated_at=?
        WHERE id=?
        """,
        (
            name,
            item_price,
            1 if has_shipping else 0,
            shipping_fee,
            datetime.now().isoformat(timespec="seconds"),
            int(purchase_id),
        ),
    )


def delete_purchase(conn: sqlite3.Connection, purchase_id: int) -> None:
    conn.execute("DELETE FROM purchases WHERE id=?", (int(purchase_id),))


def clear_purchases(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM purchases")


def list_purchases(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM purchases ORDER BY id"
    ).fetchall()


def get_purchase(conn: sqlite3.Connection, purchase_id: int):
    return conn.execute(
        "SELECT * FROM purchases WHERE id=?", (int(purchase_id),)
    ).fetchone()


def list_purchase_audit(conn: sqlite3.Connection, purchase_id: int | None = None):
    """Return append-only purchase history for recovery/diagnostics."""
    if purchase_id is None:
        return conn.execute(
            "SELECT * FROM purchase_audit ORDER BY audit_id"
        ).fetchall()
    return conn.execute(
        "SELECT * FROM purchase_audit WHERE purchase_id=? ORDER BY audit_id",
        (int(purchase_id),),
    ).fetchall()


def required_components(purchase) -> list[dict]:
    """把一条购买记录拆成需要匹配的发票组成项。"""
    components = [
        {
            "kind": "商品",
            "price": float(purchase["item_price"]),
            "cents": _money_to_cents(purchase["item_price"]),
        }
    ]
    if int(purchase["has_shipping"] or 0):
        components.append(
            {
                "kind": "快递费",
                "price": float(purchase["shipping_fee"]),
                "cents": _money_to_cents(purchase["shipping_fee"]),
            }
        )
    return components


def match_purchases(conn: sqlite3.Connection) -> dict:
    """
    一对一价格匹配。

    返回：
      purchase_results: 每条购买记录及其商品/快递费匹配情况
      invoice_match_map: digest -> 匹配信息，用于发票表显示“✓”
      missing_components: 记录了购买，但还缺发票的组成项
      unused_invoices: 有发票，但没有对应购买组成项

    当存在多个完全相同价格时，按购买记录 id、发票文件名顺序依次配对。
    """
    purchases = list_purchases(conn)
    invoices = conn.execute(
        """
        SELECT * FROM invoices
        WHERE active=1
        ORDER BY filename COLLATE NOCASE, digest
        """
    ).fetchall()

    by_price: dict[int, list] = {}
    for inv in invoices:
        cents = _money_to_cents(inv["total"])
        if cents is None:
            continue
        by_price.setdefault(cents, []).append(inv)

    used_digests: set[str] = set()
    invoice_match_map: dict[str, dict] = {}
    purchase_results: list[dict] = []
    missing_components: list[dict] = []

    for purchase in purchases:
        component_results = []

        for component in required_components(purchase):
            found = None
            candidates = by_price.get(component["cents"], [])
            while candidates:
                candidate = candidates.pop(0)
                digest = str(candidate["digest"])
                if digest not in used_digests:
                    found = candidate
                    break

            component_result = {
                "kind": component["kind"],
                "price": component["price"],
                "invoice": found,
                "matched": found is not None,
            }
            component_results.append(component_result)

            if found is not None:
                digest = str(found["digest"])
                used_digests.add(digest)
                invoice_match_map[digest] = {
                    "purchase_id": int(purchase["id"]),
                    "purchase_name": str(purchase["name"]),
                    "kind": component["kind"],
                    "price": component["price"],
                }
            else:
                missing_components.append(
                    {
                        "purchase_id": int(purchase["id"]),
                        "purchase_name": str(purchase["name"]),
                        "kind": component["kind"],
                        "price": component["price"],
                    }
                )

        purchase_results.append(
            {
                "purchase": purchase,
                "components": component_results,
                "complete": all(x["matched"] for x in component_results),
            }
        )

    unused_invoices = [
        inv for inv in invoices
        if str(inv["digest"]) not in used_digests
    ]

    return {
        "purchase_results": purchase_results,
        "invoice_match_map": invoice_match_map,
        "missing_components": missing_components,
        "unused_invoices": unused_invoices,
    }
