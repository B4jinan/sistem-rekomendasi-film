"""
app.py — Entry point aplikasi Flask.
Sistem Rekomendasi Film Hybrid CF-CBF-XGBoost.

Struktur modular (Blueprint):
- config.py       : path & secret key
- db.py           : akses SQLite + helper
- extensions.py   : mesin rekomendasi (dimuat sekali)
- engine.py       : class RecommenderEngine (pipeline CF-CBF-XGBoost)
- routes/auth.py      : index, login, register, logout
- routes/dashboard.py : dashboard (baris film populer, non-personal)
- routes/recommend.py : rating, hasil
- routes/profil.py    : profil, edit akun, ganti nama/password
- routes/katalog.py   : katalog, detail film, rating film
"""

import os

from flask import Flask
import config
from db import init_db
from routes.auth import auth_bp
from routes.dashboard import dashboard_bp
from routes.recommend import recommend_bp
from routes.profil import profil_bp
from routes.katalog import katalog_bp

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Pastikan tabel database ada
init_db()


@app.template_filter("angka")
def format_angka(n):
    """Format bilangan ala Indonesia: 14523 -> 14.523. Kosong -> '-'."""
    try:
        return f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "-"

# Daftarkan semua blueprint
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(recommend_bp)
app.register_blueprint(profil_bp)
app.register_blueprint(katalog_bp)


if __name__ == "__main__":
    # Blok ini HANYA jalan saat `python app.py` di komputer sendiri.
    # Di Render, aplikasi dijalankan gunicorn yang mengimpor objek `app`
    # langsung, sehingga blok ini dilewati.
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=config.DEBUG, host="0.0.0.0", port=port)
