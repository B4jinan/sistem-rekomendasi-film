"""Blueprint profil: profil, edit akun, ganti nama, ganti password."""
from collections import Counter
from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db
from extensions import engine

profil_bp = Blueprint("profil", __name__)


@profil_bp.route("/profil")
def profil():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?",
                        (session["user_id"],)).fetchone()
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


@profil_bp.route("/edit_akun")
def edit_akun():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?",
                        (session["user_id"],)).fetchone()
    conn.close()
    return render_template("edit_akun.html", nama=user["nama"], email=user["email"])


@profil_bp.route("/ganti_nama", methods=["GET", "POST"])
def ganti_nama():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        nama_baru = request.form.get("nama", "").strip()
        if not nama_baru:
            flash("Nama tidak boleh kosong.", "danger")
            return redirect(url_for("profil.ganti_nama"))

        conn = get_db()
        conn.execute("UPDATE users SET nama = ? WHERE id = ?",
                     (nama_baru, session["user_id"]))
        conn.commit()
        conn.close()

        session["nama"] = nama_baru
        flash("Nama berhasil diganti.", "success")
        return redirect(url_for("profil.edit_akun"))

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?",
                        (session["user_id"],)).fetchone()
    conn.close()
    return render_template("ganti_nama.html", nama=user["nama"])


@profil_bp.route("/ganti_password", methods=["GET", "POST"])
def ganti_password():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        pw_lama = request.form.get("password_lama", "")
        pw_baru = request.form.get("password_baru", "")
        pw_konfirmasi = request.form.get("password_konfirmasi", "")

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?",
                            (session["user_id"],)).fetchone()

        if not check_password_hash(user["password_hash"], pw_lama):
            conn.close()
            flash("Password lama salah.", "danger")
            return redirect(url_for("profil.ganti_password"))

        if len(pw_baru) < 4:
            conn.close()
            flash("Password baru minimal 4 karakter.", "danger")
            return redirect(url_for("profil.ganti_password"))

        if pw_baru != pw_konfirmasi:
            conn.close()
            flash("Konfirmasi password tidak cocok.", "danger")
            return redirect(url_for("profil.ganti_password"))

        new_hash = generate_password_hash(pw_baru, method="pbkdf2:sha256")
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (new_hash, session["user_id"]))
        conn.commit()
        conn.close()

        flash("Password berhasil diganti.", "success")
        return redirect(url_for("profil.edit_akun"))

    return render_template("ganti_password.html")
