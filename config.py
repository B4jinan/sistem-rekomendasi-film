"""Konfigurasi aplikasi: path artifact & secret key."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "model")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(BASE_DIR, "database.db")

# Secret key diambil dari environment saat di server (Render mengisinya
# otomatis lewat render.yaml). Nilai cadangan hanya dipakai saat jalan lokal —
# jangan andalkan itu di server publik.
SECRET_KEY = os.environ.get("SECRET_KEY", "kunci-lokal-untuk-pengembangan-saja")

# DEBUG hanya menyala kalau dijalankan langsung di komputer sendiri.
# Di server, error detail tidak boleh tampil ke publik.
DEBUG = os.environ.get("FLASK_DEBUG", "1") == "1"
