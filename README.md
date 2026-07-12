# Sistem Rekomendasi Film — Web App (Flask)

Aplikasi web skripsi: Hybrid CF-CBF-XGBoost untuk rekomendasi film cold-start.

## Struktur Folder

```
skripsi_webapp/
├── app.py                # Aplikasi Flask (routes, SQLite, auth)
├── engine.py             # Mesin rekomendasi CF -> CBF -> XGBoost
├── requirements.txt      # Dependency
├── database.db           # (otomatis dibuat saat pertama run)
├── model/                # >>> ISI MANUAL: copy 7 artifact dari Google Drive <<<
│   ├── cf/
│   │   ├── item_sim_sp.pkl
│   │   └── item_enc.pkl
│   ├── tfidf_matrix.npz
│   ├── fid_to_idx.pkl
│   ├── xgb_model.json
│   └── feature_cols.pkl
├── data/                 # >>> ISI MANUAL <<<
│   └── film_content_clean.csv
├── templates/            # HTML (Jinja2 + Bootstrap 5)
└── static/               # (kosong, untuk CSS/gambar tambahan)
```

## PENTING — Sebelum Menjalankan

Folder `model/` dan `data/` **masih kosong**. Kamu harus copy artifact dari
Google Drive ke folder ini dulu:

Dari `/content/drive/MyDrive/skripsi/model/`:
- `cf/item_sim_sp.pkl`   -> `model/cf/item_sim_sp.pkl`
- `cf/item_enc.pkl`      -> `model/cf/item_enc.pkl`
- `tfidf_matrix.npz`     -> `model/tfidf_matrix.npz`
- `fid_to_idx.pkl`       -> `model/fid_to_idx.pkl`
- `xgb_model.json`       -> `model/xgb_model.json`  (WAJIB .json, bukan .pkl!)
- `feature_cols.pkl`     -> `model/feature_cols.pkl`

Dari `/content/drive/MyDrive/skripsi/dataset_movie/processed/`:
- `film_content_clean.csv` -> `data/film_content_clean.csv`

## Menjalankan di Lokal (Laptop)

```bash
# 1. Buat virtual environment (opsional tapi disarankan)
python -m venv venv
source venv/bin/activate       # Linux/Mac
venv\Scripts\activate          # Windows

# 2. Install dependency
pip install -r requirements.txt

# 3. Jalankan
python app.py
```

Buka browser ke: http://localhost:5000

## Deploy ke Render (Hosting Gratis)

1. Push folder ini ke repository GitHub (JANGAN commit `database.db`)
2. Di https://render.com, buat "New Web Service", connect ke repo GitHub
3. Setting:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Instance Type:** Free
4. Deploy. Render akan kasih URL publik.

Catatan: file model total hanya ~5.3 MB, aman untuk Render free tier (512MB RAM).

### File .gitignore yang Disarankan
```
database.db
venv/
__pycache__/
*.pyc
```

## Alur Aplikasi

1. Beranda -> klik "Mulai"
2. Register (nama + email) -> otomatis login kalau email sudah ada
3. Beri rating minimal 3 dari 20 film populer
4. Sistem jalankan pipeline: CF (Top-30) -> CBF (Top-20) -> XGBoost (Top-10)
5. Tampilkan 10 rekomendasi dengan skor kecocokan
