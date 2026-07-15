"""Blueprint katalog: detail film, katalog film, rating film."""
import json
from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash)
from db import get_db, load_user_ratings
from engine import POSTER_BESAR
from extensions import engine

katalog_bp = Blueprint("katalog", __name__)


@katalog_bp.route("/film/<int:tmdb_id>")
def film_detail(tmdb_id):
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    info = engine.get_film_info(tmdb_id)
    if info is None:
        flash("Film tidak ditemukan.", "warning")
        return redirect(url_for("recommend.rating"))

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
        "poster_url": engine.get_poster_url(tmdb_id, POSTER_BESAR),
        "trailer_url": engine.get_trailer_url(tmdb_id),
    }

    similar = engine.get_similar_films(tmdb_id, n=6)
    for s in similar:
        g = s.get("genres")
        s["genres_display"] = g.replace("|", ", ") if isinstance(g, str) and g else "-"
        try:
            s["year"] = int(s.get("year"))
        except (TypeError, ValueError):
            s["year"] = "-"

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


@katalog_bp.route("/katalog")
def katalog():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

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


@katalog_bp.route("/rate_film/<int:tmdb_id>", methods=["POST"])
def rate_film(tmdb_id):
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    info = engine.get_film_info(tmdb_id)
    if info is None:
        flash("Film tidak ditemukan.", "warning")
        return redirect(url_for("katalog.katalog"))

    try:
        r = float(request.form.get("rating", ""))
    except ValueError:
        flash("Rating tidak valid.", "danger")
        return redirect(url_for("katalog.film_detail", tmdb_id=tmdb_id))

    if r < 1 or r > 5:
        flash("Rating harus antara 1 sampai 5.", "danger")
        return redirect(url_for("katalog.film_detail", tmdb_id=tmdb_id))

    conn = get_db()
    conn.execute("INSERT INTO ratings (user_id, tmdb_id, rating) VALUES (?, ?, ?)",
                 (session["user_id"], tmdb_id, r))
    conn.commit()
    conn.close()

    session["user_ratings"] = json.dumps(load_user_ratings(session["user_id"]))
    flash("Rating tersimpan. Rekomendasimu akan menyesuaikan.", "success")
    return redirect(url_for("katalog.film_detail", tmdb_id=tmdb_id))


@katalog_bp.route("/hapus_rating/<int:tmdb_id>", methods=["POST"])
def hapus_rating(tmdb_id):
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    # Hapus SEMUA baris rating film ini milik user. Penting: tabel ratings
    # menyimpan riwayat (satu film bisa punya beberapa baris kalau dirating
    # ulang). Kalau hanya baris terakhir yang dihapus, rating lama akan
    # muncul kembali seolah-olah tidak terhapus.
    conn = get_db()
    cur = conn.execute("DELETE FROM ratings WHERE user_id = ? AND tmdb_id = ?",
                       (session["user_id"], tmdb_id))
    conn.commit()
    jml_terhapus = cur.rowcount
    conn.close()

    # Segarkan session supaya rekomendasi tidak memakai rating yang sudah dihapus
    session["user_ratings"] = json.dumps(load_user_ratings(session["user_id"]))

    if jml_terhapus:
        flash("Rating dihapus.", "success")
    else:
        flash("Tidak ada rating untuk dihapus pada film ini.", "warning")

    # Kembali ke halaman asal
    if request.form.get("asal") == "profil":
        return redirect(url_for("profil.profil"))
    return redirect(url_for("katalog.film_detail", tmdb_id=tmdb_id))
