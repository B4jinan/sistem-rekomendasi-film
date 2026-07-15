"""Akses database SQLite + helper rating."""
import sqlite3
import config


def get_db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tmdb_id INTEGER NOT NULL,
            rating REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    conn.commit()
    conn.close()


def load_user_ratings(user_id):
    """Ambil rating user dari DB, dedup ambil rating terbaru per film."""
    conn = get_db()
    rows = conn.execute(
        "SELECT tmdb_id, rating FROM ratings WHERE user_id = ? "
        "ORDER BY created_at DESC, id DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    seen = set()
    ur = {}
    for r in rows:
        tid = int(r["tmdb_id"])
        if tid in seen:
            continue
        seen.add(tid)
        ur[tid] = float(r["rating"])
    return ur
