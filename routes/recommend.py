"""Blueprint recommend: halaman rating awal & hasil rekomendasi."""
import json
from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash)
from db import get_db, load_user_ratings, get_all_film_user_ratings
from ratings_util import enrich_films
from extensions import engine

recommend_bp = Blueprint("recommend", __name__)


@recommend_bp.route("/rating", methods=["GET", "POST"])
def rating():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        user_ratings = {}
        for key, value in request.form.items():
            if key.startswith("rating_") and value:
                tmdb_id = int(key.replace("rating_", ""))
                user_ratings[tmdb_id] = float(value)

        if len(user_ratings) < 3:
            flash("Beri rating minimal 3 film agar rekomendasi akurat.", "warning")
            return redirect(url_for("recommend.rating"))

        conn = get_db()
        for tmdb_id, r in user_ratings.items():
            conn.execute(
                "INSERT INTO ratings (user_id, tmdb_id, rating) VALUES (?, ?, ?)",
                (session["user_id"], tmdb_id, r)
            )
        conn.commit()
        conn.close()

        session["user_ratings"] = json.dumps(user_ratings)
        return redirect(url_for("recommend.hasil"))

    popular_films = engine.get_popular_films(n=20)
    return render_template("rating.html", films=popular_films)


@recommend_bp.route("/hasil")
def hasil():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    # Ambil rating dari session; kalau hilang (mis. diklik dari navbar),
    # bangun ulang dari database supaya link "Rekomendasi" selalu aman.
    if "user_ratings" in session:
        user_ratings = {int(k): v for k, v in json.loads(session["user_ratings"]).items()}
    else:
        user_ratings = load_user_ratings(session["user_id"])
        session["user_ratings"] = json.dumps(user_ratings)

    # Cek minimal 3 film untuk KEDUA jalur di atas. Penting: user bisa
    # menghapus rating sampai tersisa < 3, dan session ikut diperbarui —
    # jadi pengecekan tidak boleh hanya di jalur database.
    if len(user_ratings) < 3:
        flash("Beri rating minimal 3 film dulu untuk melihat rekomendasi.", "warning")
        return redirect(url_for("recommend.rating"))

    try:
        recommendations = engine.recommend(user_ratings)
    except Exception as e:
        flash(f"Terjadi kesalahan saat membuat rekomendasi: {e}", "danger")
        return redirect(url_for("recommend.rating"))

    for rec in recommendations:
        rec["genres_display"] = rec["genres"].replace("|", ", ") if rec["genres"] else "-"
        rec["xgb_score_pct"] = round(rec["xgb_score"] * 100, 1)

    enrich_films(recommendations, get_all_film_user_ratings())

    return render_template("hasil.html",
                          recommendations=recommendations,
                          nama=session.get("nama", "User"))
