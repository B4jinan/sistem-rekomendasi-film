"""Konfigurasi aplikasi: path artifact & secret key."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "model")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(BASE_DIR, "database.db")

SECRET_KEY = "ganti-dengan-string-rahasia-anda"
