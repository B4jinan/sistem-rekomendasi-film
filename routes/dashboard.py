"""Blueprint dashboard: halaman utama setelah login.

CATATAN: isi dashboard BUKAN rekomendasi personal — murni daftar berbasis
popularitas/penilaian yang sama untuk semua pengguna. Rekomendasi personal
(pipeline CF -> CBF -> XGBoost) ada di blueprint recommend.
"""
from flask import Blueprint, render_template, redirect, url_for, session
from db import load_user_ratings
from extensions import engine

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    # Dipakai untuk mengarahkan pengguna baru agar memberi rating dulu
    n_ratings = len(load_user_ratings(session["user_id"]))

    return render_template("dashboard.html",
                          rows=engine.dashboard_rows,
                          nama=session.get("nama", "User"),
                          n_ratings=n_ratings)
