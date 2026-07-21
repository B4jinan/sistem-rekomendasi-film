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


def get_film_user_ratings(tmdb_id):
    """Rating TERBARU tiap pengguna aplikasi untuk satu film (skala 1-5).

    Dipakai untuk menghitung rating gabungan komunitas. Satu pengguna dihitung
    sekali (rating terbarunya), walau tabel menyimpan riwayat berlapis.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT user_id, rating FROM ratings WHERE tmdb_id = ? "
        "ORDER BY created_at DESC, id DESC",
        (tmdb_id,)
    ).fetchall()
    conn.close()
    seen = set()
    hasil = []
    for r in rows:
        uid = r["user_id"]
        if uid in seen:
            continue
        seen.add(uid)
        hasil.append(float(r["rating"]))
    return hasil


def get_all_film_user_ratings():
    """Peta {tmdb_id: [rating terbaru tiap pengguna]} untuk SEMUA film yang
    punya rating pengguna aplikasi. Satu query — dipakai menghitung rating
    gabungan banyak film sekaligus (dashboard, katalog, hasil)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT user_id, tmdb_id, rating FROM ratings "
        "ORDER BY created_at DESC, id DESC"
    ).fetchall()
    conn.close()
    seen = set()        # (user_id, tmdb_id) yang sudah diambil (terbaru menang)
    peta = {}
    for r in rows:
        key = (r["user_id"], r["tmdb_id"])
        if key in seen:
            continue
        seen.add(key)
        peta.setdefault(int(r["tmdb_id"]), []).append(float(r["rating"]))
    return peta
