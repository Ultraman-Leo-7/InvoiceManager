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

    # Manual invoice overrides take precedence over price matching.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS purchase_invoice_overrides (
            purchase_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            invoice_digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(purchase_id, kind),
            UNIQUE(invoice_digest)
        );

        CREATE TABLE IF NOT EXISTS purchase_invoice_override_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            invoice_digest TEXT NOT NULL,
            action TEXT NOT NULL,
            audited_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TRIGGER IF NOT EXISTS trg_override_audit_insert
        AFTER INSERT ON purchase_invoice_overrides
        BEGIN
            INSERT INTO purchase_invoice_override_audit(purchase_id, kind, invoice_digest, action)
            VALUES(NEW.purchase_id, NEW.kind, NEW.invoice_digest, 'SET');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_override_audit_update
        BEFORE UPDATE ON purchase_invoice_overrides
        BEGIN
            INSERT INTO purchase_invoice_override_audit(purchase_id, kind, invoice_digest, action)
            VALUES(OLD.purchase_id, OLD.kind, OLD.invoice_digest, 'REPLACE_OLD');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_override_audit_delete
        BEFORE DELETE ON purchase_invoice_overrides
        BEGIN
            INSERT INTO purchase_invoice_override_audit(purchase_id, kind, invoice_digest, action)
            VALUES(OLD.purchase_id, OLD.kind, OLD.invoice_digest, 'CLEAR');
        END;
        """
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
    if not has_shipping:
        conn.execute(
            "DELETE FROM purchase_invoice_overrides WHERE purchase_id=? AND kind='快递费'",
            (int(purchase_id),),
        )


def delete_purchase(conn: sqlite3.Connection, purchase_id: int) -> None:
    conn.execute("DELETE FROM purchase_invoice_overrides WHERE purchase_id=?", (int(purchase_id),))
    conn.execute("DELETE FROM purchases WHERE id=?", (int(purchase_id),))


def clear_purchases(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM purchase_invoice_overrides")
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


def set_manual_match(
    conn: sqlite3.Connection,
    purchase_id: int,
    kind: str,
    invoice_digest: str,
) -> None:
    purchase = get_purchase(conn, purchase_id)
    if not purchase:
        raise ValueError("购买记录不存在")
    valid_kinds = {x["kind"] for x in required_components(purchase)}
    if kind not in valid_kinds:
        raise ValueError(f"当前购买记录没有“{kind}”这一项")
    invoice = conn.execute(
        "SELECT digest FROM invoices WHERE digest=? AND active=1",
        (str(invoice_digest),),
    ).fetchone()
    if not invoice:
        raise ValueError("所选发票当前不存在或已被移出发票文件夹")
    owner = conn.execute(
        "SELECT purchase_id, kind FROM purchase_invoice_overrides WHERE invoice_digest=?",
        (str(invoice_digest),),
    ).fetchone()
    if owner and (int(owner["purchase_id"]), str(owner["kind"])) != (int(purchase_id), str(kind)):
        raise ValueError(
            f"这张发票已经被手动关联到购买记录 #{owner['purchase_id']} 的“{owner['kind']}”。"
            "请先在原记录中恢复自动匹配，再重新关联。"
        )
    conn.execute(
        """
        INSERT INTO purchase_invoice_overrides(purchase_id, kind, invoice_digest, created_at)
        VALUES(?,?,?,?)
        ON CONFLICT(purchase_id, kind) DO UPDATE SET
            invoice_digest=excluded.invoice_digest,
            created_at=excluded.created_at
        """,
        (
            int(purchase_id),
            str(kind),
            str(invoice_digest),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def clear_manual_match(conn: sqlite3.Connection, purchase_id: int, kind: str) -> None:
    conn.execute(
        "DELETE FROM purchase_invoice_overrides WHERE purchase_id=? AND kind=?",
        (int(purchase_id), str(kind)),
    )


def list_manual_matches(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM purchase_invoice_overrides ORDER BY purchase_id, kind"
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
    """One-to-one matching: manual overrides first, then exact-price automatic matching."""
    purchases = list_purchases(conn)
    invoices = conn.execute(
        """
        SELECT * FROM invoices
        WHERE active=1
        ORDER BY filename COLLATE NOCASE, digest
        """
    ).fetchall()
    invoice_by_digest = {str(inv["digest"]): inv for inv in invoices}

    overrides = {
        (int(row["purchase_id"]), str(row["kind"])): str(row["invoice_digest"])
        for row in conn.execute(
            "SELECT purchase_id, kind, invoice_digest FROM purchase_invoice_overrides"
        ).fetchall()
    }
    reserved_manual = {
        digest for digest in overrides.values() if digest in invoice_by_digest
    }

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
        purchase_id = int(purchase["id"])

        for component in required_components(purchase):
            kind = component["kind"]
            key = (purchase_id, kind)
            manual_digest = overrides.get(key)
            manual = manual_digest is not None
            manual_missing = False
            found = None

            if manual:
                found = invoice_by_digest.get(manual_digest)
                manual_missing = found is None
            else:
                candidates = by_price.get(component["cents"], [])
                while candidates:
                    candidate = candidates.pop(0)
                    digest = str(candidate["digest"])
                    if digest in used_digests or digest in reserved_manual:
                        continue
                    found = candidate
                    break

            component_result = {
                "kind": kind,
                "price": component["price"],
                "invoice": found,
                "matched": found is not None,
                "manual": manual,
                "manual_missing": manual_missing,
            }
            component_results.append(component_result)

            if found is not None:
                digest = str(found["digest"])
                used_digests.add(digest)
                invoice_match_map[digest] = {
                    "purchase_id": purchase_id,
                    "purchase_name": str(purchase["name"]),
                    "kind": kind,
                    "price": component["price"],
                    "manual": manual,
                }
            else:
                missing_components.append(
                    {
                        "purchase_id": purchase_id,
                        "purchase_name": str(purchase["name"]),
                        "kind": kind,
                        "price": component["price"],
                        "manual": manual,
                        "manual_missing": manual_missing,
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
        inv for inv in invoices if str(inv["digest"]) not in used_digests
    ]

    return {
        "purchase_results": purchase_results,
        "invoice_match_map": invoice_match_map,
        "missing_components": missing_components,
        "unused_invoices": unused_invoices,
    }
