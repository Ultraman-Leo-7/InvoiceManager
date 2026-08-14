import sqlite3
from pathlib import Path

from data_backup import create_portable_snapshot, restore_snapshot, validate_snapshot


def make_db(path: Path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            item_price REAL NOT NULL,
            has_shipping INTEGER NOT NULL DEFAULT 0,
            shipping_fee REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE invoices (
            digest TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            confirmed INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT ''
        );
        """
    )
    conn.executemany(
        "INSERT INTO settings(key, value) VALUES(?, ?)",
        [
            ("selected_fields", '["文件名"]'),
            ("qq_auth_code", "dpapi:secret-qq"),
            ("nutstore_app_password", "dpapi:secret-nutstore"),
            ("nutstore_email", "user@example.com"),
        ],
    )
    conn.execute(
        "INSERT INTO purchases(name,item_price,has_shipping,shipping_fee,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        ("键盘", 99.0, 1, 6.0, "2026-08-14", "2026-08-14"),
    )
    conn.execute(
        "INSERT INTO invoices(digest,filename,confirmed,note) VALUES(?,?,?,?)",
        ("abc", "invoice.pdf", 1, "已核对"),
    )
    conn.commit()
    conn.close()


def test_portable_snapshot_keeps_user_data_but_strips_device_secrets(tmp_path):
    source = tmp_path / "source.db"
    snapshot = tmp_path / "snapshot.db"
    make_db(source)

    create_portable_snapshot(source, snapshot)
    validate_snapshot(snapshot)

    conn = sqlite3.connect(snapshot)
    settings = dict(conn.execute("SELECT key, value FROM settings"))
    purchase = conn.execute("SELECT name,item_price,shipping_fee FROM purchases").fetchone()
    invoice = conn.execute("SELECT confirmed,note FROM invoices WHERE digest='abc'").fetchone()
    conn.close()

    assert settings["selected_fields"] == '["文件名"]'
    assert settings["nutstore_email"] == "user@example.com"
    assert "qq_auth_code" not in settings
    assert "nutstore_app_password" not in settings
    assert purchase == ("键盘", 99.0, 6.0)
    assert invoice == (1, "已核对")


def test_restore_creates_local_safety_copy(tmp_path):
    current = tmp_path / "current.db"
    snapshot = tmp_path / "snapshot.db"
    backup_dir = tmp_path / "safety"
    make_db(current)
    make_db(snapshot)

    conn = sqlite3.connect(snapshot)
    conn.execute("UPDATE purchases SET name='恢复后的记录'")
    conn.commit()
    conn.close()

    before = restore_snapshot(snapshot, current, backup_dir)
    assert before is not None and before.exists()

    conn = sqlite3.connect(current)
    name = conn.execute("SELECT name FROM purchases").fetchone()[0]
    conn.close()
    assert name == "恢复后的记录"
