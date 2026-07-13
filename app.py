"""
app.py — Aplikasi Web Flask
Sistem Rekomendasi Film Hybrid CF-CBF-XGBoost

Fitur:
- Auth: nama + email + PASSWORD (di-hash pakai werkzeug) via SQLite
- Alur login-first: masuk web -> login; belum punya akun -> daftar
- Halaman rating awal (20 film populer) untuk cold-start
- Rekomendasi Top-10 hasil pipeline CF -> CBF -> XGBoost
- Halaman profil (riwayat rating + genre favorit)
- Edit akun: ganti nama & ganti password (verifikasi password lama)
- Riwayat rating user tersimpan di database
"""

import os
import sqlite3
import json
from collections import Counter
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from engine import RecommenderEngine

# =============================================================================
# KONFIGURASI
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "model")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(BASE_DIR, "database.db")

app = Flask(__name__)
app.secret_key = "ganti-dengan-string-rahasia-anda"  # untuk session

# Load mesin rekomendasi SEKALI saat startup (bukan tiap request)
print("Memuat artifact mesin rekomendasi...")
engine = RecommenderEngine(model_dir=MODEL_DIR, data_dir=DATA_DIR)
print("Mesin rekomendasi siap.")


# =============================================================================
# DATABASE (SQLite)
# =============================================================================
def get_db():
    conn = sqlite3.connect(DB_PATH)
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


init_db()


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


# =============================================================================
# ROUTES — AUTH
# =============================================================================
@app.route("/")
def index():
    # Login-first: kalau sudah login lanjut ke rating, kalau belum ke login.
    if "user_id" in session:
        return redirect(url_for("rating"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email dan password wajib diisi.", "danger")
            return redirect(url_for("login"))

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user is None:
            conn.close()
            flash("Email belum terdaftar. Silakan daftar dulu.", "warning")
            return redirect(url_for("register"))

        if not check_password_hash(user["password_hash"], password):
            conn.close()
            flash("Password salah. Coba lagi.", "danger")
            return redirect(url_for("login"))

        # Password benar -> ambil rating lama user (kalau ada)
        rows = conn.execute(
            "SELECT tmdb_id, rating FROM ratings WHERE user_id = ?",
            (user["id"],)
        ).fetchall()
        conn.close()

        session["user_id"] = user["id"]
        session["nama"] = user["nama"]

        # Sudah pernah rating >=3 film -> langsung ke hasil rekomendasi.
        # Belum -> minta rating dulu.
        if len(rows) >= 3:
            user_ratings = {int(row["tmdb_id"]): float(row["rating"]) for row in rows}
            session["user_ratings"] = json.dumps(user_ratings)
            return redirect(url_for("hasil"))
        else:
            return redirect(url_for("rating"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        nama = request.form.get("nama", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not nama or not email or not password:
            flash("Nama, email, dan password wajib diisi.", "danger")
            return redirect(url_for("register"))

        if len(password) < 4:
            flash("Password minimal 4 karakter.", "danger")
            return redirect(url_for("register"))

        conn = get_db()
        existing = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if existing:
            # Email sudah terdaftar -> arahkan ke login (jangan bikin akun dobel)
            conn.close()
            flash("Email sudah terdaftar. Silakan login.", "warning")
            return redirect(url_for("login"))

        # Email baru -> buat user, password di-hash (tidak disimpan mentah)
        pw_hash = generate_password_hash(password, method="pbkdf2:sha256")
        cur = conn.execute(
            "INSERT INTO users (nama, email, password_hash) VALUES (?, ?, ?)",
            (nama, email, pw_hash)
        )
        user_id = cur.lastrowid
        conn.commit()
        conn.close()

        session["user_id"] = user_id
        session["nama"] = nama
        return redirect(url_for("rating"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =============================================================================
# ROUTES — REKOMENDASI
# =============================================================================
@app.route("/rating", methods=["GET", "POST"])
def rating():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        # Ambil rating dari form: field bernama "rating_<tmdbId>"
        user_ratings = {}
        for key, value in request.form.items():
            if key.startswith("rating_") and value:
                tmdb_id = int(key.replace("rating_", ""))
                user_ratings[tmdb_id] = float(value)

        if len(user_ratings) < 3:
            flash("Beri rating minimal 3 film agar rekomendasi akurat.", "warning")
            return redirect(url_for("rating"))

        # Simpan rating ke database
        conn = get_db()
        for tmdb_id, r in user_ratings.items():
            conn.execute(
                "INSERT INTO ratings (user_id, tmdb_id, rating) VALUES (?, ?, ?)",
                (session["user_id"], tmdb_id, r)
            )
        conn.commit()
        conn.close()

        # Simpan di session untuk dipakai halaman hasil
        session["user_ratings"] = json.dumps(user_ratings)
        return redirect(url_for("hasil"))

    popular_films = engine.get_popular_films(n=20)
    return render_template("rating.html", films=popular_films)


@app.route("/hasil")
def hasil():
    if "user_id" not in session:
        return redirect(url_for("login"))

    # Ambil rating dari session; kalau hilang (mis. diklik dari navbar),
    # bangun ulang dari database supaya link "Rekomendasi" selalu aman.
    if "user_ratings" in session:
        user_ratings = {int(k): v for k, v in json.loads(session["user_ratings"]).items()}
    else:
        user_ratings = load_user_ratings(session["user_id"])
        if len(user_ratings) < 3:
            flash("Beri rating minimal 3 film dulu untuk melihat rekomendasi.", "warning")
            return redirect(url_for("rating"))
        session["user_ratings"] = json.dumps(user_ratings)

    try:
        recommendations = engine.recommend(user_ratings)
    except Exception as e:
        flash(f"Terjadi kesalahan saat membuat rekomendasi: {e}", "danger")
        return redirect(url_for("rating"))

    # Rapikan tampilan genre (ganti | jadi koma)
    for rec in recommendations:
        rec["genres_display"] = rec["genres"].replace("|", ", ") if rec["genres"] else "-"
        rec["xgb_score_pct"] = round(rec["xgb_score"] * 100, 1)

    return render_template("hasil.html",
                          recommendations=recommendations,
                          nama=session.get("nama", "User"))


# =============================================================================
# ROUTES — PROFIL & EDIT AKUN
# =============================================================================
@app.route("/profil")
def profil():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?",
                        (session["user_id"],)).fetchone()
    # Urut terbaru dulu supaya saat dedup, yang pertama muncul = rating terbaru
    rows = conn.execute(
        "SELECT tmdb_id, rating, created_at FROM ratings "
        "WHERE user_id = ? ORDER BY created_at DESC, id DESC",
        (session["user_id"],)
    ).fetchall()
    conn.close()

    # Dedup per film: ambil hanya rating TERBARU untuk tiap film
    seen = set()
    history = []
    for r in rows:
        tid = int(r["tmdb_id"])
        if tid in seen:
            continue
        seen.add(tid)

        info = engine.get_film_info(tid)
        if info:
            title = info.get("title") or f"(film {tid})"
            genres_raw = info.get("genres")
            genres_disp = (genres_raw.replace("|", ", ")
                           if isinstance(genres_raw, str) and genres_raw else "-")
            try:
                year_disp = int(info.get("year"))
            except (TypeError, ValueError):
                year_disp = "-"
        else:
            title, genres_disp, year_disp = f"(film {tid})", "-", "-"

        history.append({
            "tmdb_id": tid,
            "title": title,
            "genres": genres_disp,
            "year": year_disp,
            "rating": r["rating"],
        })

    # Genre favorit: dihitung dari film yang dirating >=3 (film "disukai"),
    # konsisten dengan cara engine membangun profil user.
    genre_counter = Counter()
    for h in history:
        if h["rating"] >= 3.0 and h["genres"] != "-":
            for g in h["genres"].split(", "):
                if g:
                    genre_counter[g] += 1
    fav_genres = [g for g, _ in genre_counter.most_common(5)]

    return render_template("profil.html",
                          nama=user["nama"],
                          email=user["email"],
                          history=history,
                          fav_genres=fav_genres)


@app.route("/edit_akun")
def edit_akun():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?",
                        (session["user_id"],)).fetchone()
    conn.close()
    return render_template("edit_akun.html", nama=user["nama"], email=user["email"])


@app.route("/ganti_nama", methods=["GET", "POST"])
def ganti_nama():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        nama_baru = request.form.get("nama", "").strip()
        if not nama_baru:
            flash("Nama tidak boleh kosong.", "danger")
            return redirect(url_for("ganti_nama"))

        conn = get_db()
        conn.execute("UPDATE users SET nama = ? WHERE id = ?",
                     (nama_baru, session["user_id"]))
        conn.commit()
        conn.close()

        session["nama"] = nama_baru  # update tampilan nama di session
        flash("Nama berhasil diganti.", "success")
        return redirect(url_for("edit_akun"))

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?",
                        (session["user_id"],)).fetchone()
    conn.close()
    return render_template("ganti_nama.html", nama=user["nama"])


@app.route("/ganti_password", methods=["GET", "POST"])
def ganti_password():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        pw_lama = request.form.get("password_lama", "")
        pw_baru = request.form.get("password_baru", "")
        pw_konfirmasi = request.form.get("password_konfirmasi", "")

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?",
                            (session["user_id"],)).fetchone()

        # 1. Verifikasi password lama
        if not check_password_hash(user["password_hash"], pw_lama):
            conn.close()
            flash("Password lama salah.", "danger")
            return redirect(url_for("ganti_password"))

        # 2. Validasi panjang password baru
        if len(pw_baru) < 4:
            conn.close()
            flash("Password baru minimal 4 karakter.", "danger")
            return redirect(url_for("ganti_password"))

        # 3. Konfirmasi harus cocok
        if pw_baru != pw_konfirmasi:
            conn.close()
            flash("Konfirmasi password tidak cocok.", "danger")
            return redirect(url_for("ganti_password"))

        # 4. Simpan password baru (di-hash)
        new_hash = generate_password_hash(pw_baru, method="pbkdf2:sha256")
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (new_hash, session["user_id"]))
        conn.commit()
        conn.close()

        flash("Password berhasil diganti.", "success")
        return redirect(url_for("edit_akun"))

    return render_template("ganti_password.html")


# =============================================================================
# ROUTES — DETAIL FILM
# =============================================================================
@app.route("/film/<int:tmdb_id>")
def film_detail(tmdb_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    info = engine.get_film_info(tmdb_id)
    if info is None:
        flash("Film tidak ditemukan.", "warning")
        return redirect(url_for("rating"))

    genres_raw = info.get("genres")
    genres_disp = (genres_raw.replace("|", ", ")
                   if isinstance(genres_raw, str) and genres_raw else "-")
    try:
        year_disp = int(info.get("year"))
    except (TypeError, ValueError):
        year_disp = "-"
    try:
        runtime_disp = int(info.get("runtime"))
    except (TypeError, ValueError):
        runtime_disp = None

    overview = engine.get_overview(tmdb_id)

    film = {
        "tmdb_id": tmdb_id,
        "title": info.get("title") or f"(film {tmdb_id})",
        "year": year_disp,
        "genres": genres_disp,
        "vote_average": info.get("vote_average"),
        "runtime": runtime_disp,
        "overview": overview if overview else "Sinopsis tidak tersedia.",
    }

    # Film mirip (konten) — rapikan genre & tahun untuk tampilan
    similar = engine.get_similar_films(tmdb_id, n=6)
    for s in similar:
        g = s.get("genres")
        s["genres_display"] = g.replace("|", ", ") if isinstance(g, str) and g else "-"
        try:
            s["year"] = int(s.get("year"))
        except (TypeError, ValueError):
            s["year"] = "-"

    # Rating user saat ini untuk film ini (kalau sudah pernah)
    conn = get_db()
    row = conn.execute(
        "SELECT rating FROM ratings WHERE user_id = ? AND tmdb_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (session["user_id"], tmdb_id)
    ).fetchone()
    conn.close()
    current_rating = row["rating"] if row else None

    return render_template("film_detail.html", film=film, similar=similar,
                           current_rating=current_rating)


# =============================================================================
# ROUTES — KATALOG & RATING FILM
# =============================================================================
@app.route("/katalog")
def katalog():
    if "user_id" not in session:
        return redirect(url_for("login"))

    genre = request.args.get("genre", "").strip() or None
    query = request.args.get("q", "").strip() or None
    year = request.args.get("year", "").strip() or None
    sort = request.args.get("sort", "title").strip() or "title"
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1

    result = engine.browse_films(genre=genre, query=query, year_decade=year,
                                 sort=sort, page=page, per_page=50)
    return render_template("katalog.html",
                          result=result,
                          genres=engine.all_genres,
                          decades=engine.decades,
                          sel_genre=genre or "",
                          sel_year=year or "",
                          sel_sort=sort,
                          query=query or "")


@app.route("/rate_film/<int:tmdb_id>", methods=["POST"])
def rate_film(tmdb_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    info = engine.get_film_info(tmdb_id)
    if info is None:
        flash("Film tidak ditemukan.", "warning")
        return redirect(url_for("katalog"))

    try:
        r = float(request.form.get("rating", ""))
    except ValueError:
        flash("Rating tidak valid.", "danger")
        return redirect(url_for("film_detail", tmdb_id=tmdb_id))

    if r < 1 or r > 5:
        flash("Rating harus antara 1 sampai 5.", "danger")
        return redirect(url_for("film_detail", tmdb_id=tmdb_id))

    conn = get_db()
    conn.execute("INSERT INTO ratings (user_id, tmdb_id, rating) VALUES (?, ?, ?)",
                 (session["user_id"], tmdb_id, r))
    conn.commit()
    conn.close()

    # Perbarui session supaya rekomendasi memperhitungkan rating baru
    session["user_ratings"] = json.dumps(load_user_ratings(session["user_id"]))
    flash("Rating tersimpan. Rekomendasimu akan menyesuaikan.", "success")
    return redirect(url_for("film_detail", tmdb_id=tmdb_id))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
