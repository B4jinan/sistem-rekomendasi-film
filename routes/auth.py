"""Blueprint auth: index, login, register, logout."""
import json
from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/")
def index():
    # Login-first: kalau sudah login lanjut ke dashboard, kalau belum ke login.
    if "user_id" in session:
        return redirect(url_for("dashboard.dashboard"))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email dan password wajib diisi.", "danger")
            return redirect(url_for("auth.login"))

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user is None:
            conn.close()
            flash("Email belum terdaftar. Silakan daftar dulu.", "warning")
            return redirect(url_for("auth.register"))

        if not check_password_hash(user["password_hash"], password):
            conn.close()
            flash("Password salah. Coba lagi.", "danger")
            return redirect(url_for("auth.login"))

        rows = conn.execute(
            "SELECT tmdb_id, rating FROM ratings WHERE user_id = ?",
            (user["id"],)
        ).fetchall()
        conn.close()

        session["user_id"] = user["id"]
        session["nama"] = user["nama"]

        # Muat rating lama ke session (kalau cukup) supaya halaman rekomendasi
        # siap dipakai. Pendaratan setelah login selalu ke dashboard.
        if len(rows) >= 3:
            user_ratings = {int(row["tmdb_id"]): float(row["rating"]) for row in rows}
            session["user_ratings"] = json.dumps(user_ratings)
        return redirect(url_for("dashboard.dashboard"))

    return render_template("login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        nama = request.form.get("nama", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not nama or not email or not password:
            flash("Nama, email, dan password wajib diisi.", "danger")
            return redirect(url_for("auth.register"))

        if len(password) < 4:
            flash("Password minimal 4 karakter.", "danger")
            return redirect(url_for("auth.register"))

        conn = get_db()
        existing = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if existing:
            conn.close()
            flash("Email sudah terdaftar. Silakan login.", "warning")
            return redirect(url_for("auth.login"))

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
        return redirect(url_for("dashboard.dashboard"))

    return render_template("register.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
