"""Objek bersama antar-modul: mesin rekomendasi (dimuat SEKALI saat startup)."""
import config
from engine import RecommenderEngine

print("Memuat artifact mesin rekomendasi...")
engine = RecommenderEngine(model_dir=config.MODEL_DIR, data_dir=config.DATA_DIR)
print("Mesin rekomendasi siap.")
