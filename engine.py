"""
engine.py — Mesin Rekomendasi Cold-Start (CF -> CBF -> XGBoost)
Sistem Rekomendasi Film Hybrid CF-CBF-XGBoost

Dipindah dari notebook cold_start_engine_rebuild.ipynb yang sudah divalidasi.
Dibungkus jadi class RecommenderEngine agar artifact di-load SEKALI saat Flask
start (bukan tiap request) — penting untuk performa serving.
"""

import numpy as np
import pandas as pd
import scipy.sparse as sp
import xgboost as xgb
import joblib
import os
from collections import Counter

# =============================================================================
# PARAMETER PIPELINE (sesuai dokumentasi hasil_cf/cbf/xgb.md)
# =============================================================================
K_NEIGHBOR_CF = 15
TOP_N_CF = 30
TOP_N_CBF = 20
TOP_N_FINAL = 10
RELEVANCE_LIKE_THRESHOLD = 3.0

# Ambang untuk merakit kalimat "Mengapa direkomendasikan?"
CBF_HIGH = 0.10        # cbf_score di atas ini dianggap "konten mirip" (range 0-0.51)
VOTE_AVG_HIGH = 7.0    # vote_average di atas ini dianggap "penilaian tinggi" (skala 10)

# Parameter dashboard (baris film statis, sama untuk semua pengguna)
DASHBOARD_MIN_VOTES = 1000   # syarat minimal jumlah voter untuk baris "Rating Tertinggi"
DASHBOARD_GENRE_ROWS = 6     # jumlah baris genre yang ditampilkan
DASHBOARD_ROW_SIZE = 10      # jumlah film per baris

# Poster TMDB. Yang disimpan di film_posters.csv hanya "poster_path"
# (mis. "/abc123.jpg"); alamat lengkapnya dirakit dengan awalan di bawah.
# Gambar diambil dari CDN gambar TMDB — tidak memakai API key & tanpa rate limit.
POSTER_BASE_URL = "https://image.tmdb.org/t/p/"
POSTER_KECIL = "w92"     # thumbnail katalog
POSTER_SEDANG = "w185"   # kartu dashboard
POSTER_BESAR = "w342"    # halaman detail film

# Trailer. Yang disimpan di film_trailers.csv hanya kode video YouTube
# (mis. "YoHD9XEInc0"); alamat lengkapnya dirakit dengan awalan di bawah.
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v="


class RecommenderEngine:
    """Memuat semua artifact sekali, lalu melayani rekomendasi cold-start."""

    def __init__(self, model_dir, data_dir):
        # --- Path artifact ---
        path_item_sim     = os.path.join(model_dir, "cf", "item_sim_sp.pkl")
        path_item_enc     = os.path.join(model_dir, "cf", "item_enc.pkl")
        path_tfidf        = os.path.join(model_dir, "tfidf_matrix.npz")
        path_fid_to_idx   = os.path.join(model_dir, "fid_to_idx.pkl")
        path_xgb_model    = os.path.join(model_dir, "xgb_model.json")   # WAJIB .json
        path_feature_cols = os.path.join(model_dir, "feature_cols.pkl")
        path_film         = os.path.join(data_dir, "film_content_clean.csv")

        # --- Load artifact ---
        self.item_sim = joblib.load(path_item_sim)
        self.item_enc = joblib.load(path_item_enc)
        self.tfidf_matrix = sp.load_npz(path_tfidf)
        self.fid_to_idx = joblib.load(path_fid_to_idx)
        self.feature_cols = joblib.load(path_feature_cols)

        self.xgb_model = xgb.XGBClassifier()
        self.xgb_model.load_model(path_xgb_model)

        film = pd.read_csv(path_film)
        film = film.rename(columns={"id": "tmdbId"})
        film["num_genres"] = film["genres"].fillna("").apply(
            lambda g: len(g.split("|")) if g else 0
        )
        self.film = film
        self.known_films = set(self.item_enc.classes_)

        # Indeks cepat tmdbId -> info film (dipakai halaman profil & detail film)
        self.film_by_id = self.film.set_index("tmdbId").to_dict("index")
        # Peta balik: baris TF-IDF -> tmdbId (untuk fitur "film mirip")
        self.idx_to_fid = {v: k for k, v in self.fid_to_idx.items()}

        # Sinopsis (overview) dari movies_clean.csv — dimuat kalau file tersedia.
        # film_content_clean.csv TIDAK punya overview bersih (kelebur ke soup),
        # jadi sinopsis diambil dari movies_clean.csv secara terpisah.
        self.overview_by_id = {}
        path_movies_clean = os.path.join(data_dir, "movies_clean.csv")
        if os.path.exists(path_movies_clean):
            try:
                mv = pd.read_csv(path_movies_clean)
                mv = mv.rename(columns={"id": "tmdbId"})
                if "overview" in mv.columns:
                    mv["overview"] = mv["overview"].fillna("")
                    self.overview_by_id = dict(
                        zip(mv["tmdbId"].astype(int), mv["overview"])
                    )
                    print(f"Sinopsis dimuat: {len(self.overview_by_id)} film.")
                else:
                    print("[WARN] movies_clean.csv tidak punya kolom 'overview'.")
            except Exception as e:
                print(f"[WARN] Gagal memuat movies_clean.csv: {e}")
        else:
            print("[INFO] movies_clean.csv tidak ditemukan — sinopsis dikosongkan.")

        # Poster: alamat poster tiap film, hasil pengambilan sekali dari TMDB API
        # (lihat script ambil_poster_tmdb.py). Dimuat sebagai peta tmdbId ->
        # poster_path. Kalau file tidak ada, poster dilewati dan aplikasi tetap
        # berjalan dengan gambar placeholder.
        self.poster_by_id = {}
        path_posters = os.path.join(data_dir, "film_posters.csv")
        if os.path.exists(path_posters):
            try:
                pf = pd.read_csv(path_posters)
                pf["poster_path"] = pf["poster_path"].fillna("").astype(str)
                self.poster_by_id = dict(
                    zip(pf["tmdbId"].astype(int), pf["poster_path"])
                )
                _n_total = len(self.poster_by_id)
                _n_ada = sum(1 for v in self.poster_by_id.values() if v)
                _pct = (_n_ada / _n_total * 100) if _n_total else 0
                print(f"Poster dimuat: {_n_ada}/{_n_total} film punya poster "
                      f"({_pct:.1f}%).")
            except Exception as e:
                print(f"[WARN] Gagal memuat film_posters.csv: {e}")
        else:
            print("[INFO] film_posters.csv tidak ditemukan — poster dilewati.")

        # Trailer: kode video YouTube tiap film, hasil pengambilan sekali dari
        # TMDB API (lihat script ambil_trailer_tmdb.py). Film tanpa trailer
        # otomatis tidak menampilkan tombol trailer.
        self.trailer_by_id = {}
        path_trailers = os.path.join(data_dir, "film_trailers.csv")
        if os.path.exists(path_trailers):
            try:
                tf = pd.read_csv(path_trailers)
                tf["trailer_key"] = tf["trailer_key"].fillna("").astype(str)
                self.trailer_by_id = dict(
                    zip(tf["tmdbId"].astype(int), tf["trailer_key"])
                )
                _n_total = len(self.trailer_by_id)
                _n_ada = sum(1 for v in self.trailer_by_id.values() if v)
                _pct = (_n_ada / _n_total * 100) if _n_total else 0
                print(f"Trailer dimuat: {_n_ada}/{_n_total} film punya trailer "
                      f"({_pct:.1f}%).")
            except Exception as e:
                print(f"[WARN] Gagal memuat film_trailers.csv: {e}")
        else:
            print("[INFO] film_trailers.csv tidak ditemukan — trailer dilewati.")

        # Daftar genre unik untuk filter katalog
        _genres = set()
        for g in self.film["genres"].dropna():
            for x in str(g).split("|"):
                if x:
                    _genres.add(x)
        self.all_genres = sorted(_genres)

        # Daftar dekade untuk filter tahun katalog (mis. 2010, 2000, ...)
        _years = self.film["year"].dropna()
        self.decades = sorted({int(y) // 10 * 10 for y in _years}, reverse=True)

        # Dashboard: isinya statis (sama untuk semua pengguna) -> dihitung SEKALI
        # saat startup, bukan tiap request.
        self.dashboard_rows = self._build_dashboard()

    # -------------------------------------------------------------------------
    # LOOKUP — ambil info 1 film berdasarkan tmdbId (untuk profil & detail film)
    # -------------------------------------------------------------------------
    def get_film_info(self, tmdb_id):
        """Kembalikan dict info film berdasarkan tmdbId, atau None kalau tidak ada."""
        return self.film_by_id.get(int(tmdb_id))

    def get_overview(self, tmdb_id):
        """Sinopsis film (string). Kosong kalau tidak tersedia."""
        return self.overview_by_id.get(int(tmdb_id), "")

    def get_poster_url(self, tmdb_id, ukuran=POSTER_SEDANG):
        """Alamat lengkap poster film, atau None kalau film tidak punya poster."""
        path = self.poster_by_id.get(int(tmdb_id), "")
        if not path:
            return None
        return f"{POSTER_BASE_URL}{ukuran}{path}"

    def get_trailer_url(self, tmdb_id):
        """Alamat trailer YouTube, atau None kalau film tidak punya trailer."""
        key = self.trailer_by_id.get(int(tmdb_id), "")
        if not key:
            return None
        return f"{YOUTUBE_WATCH_URL}{key}"

    def get_similar_films(self, tmdb_id, n=6):
        """Film mirip berdasarkan kemiripan konten (TF-IDF cosine similarity)."""
        idx = self.fid_to_idx.get(int(tmdb_id))
        if idx is None:
            return []
        target = self.tfidf_matrix[idx]                          # 1 x V (L2-normalized)
        sims = (self.tfidf_matrix @ target.T).toarray().ravel()  # cosine similarity
        sims[idx] = -1.0                                         # buang diri sendiri
        top_idx = np.argsort(sims)[::-1][:n]

        hasil = []
        for i in top_idx:
            if sims[i] <= 0:
                continue
            fid = self.idx_to_fid.get(int(i))
            if fid is None:
                continue
            info = self.film_by_id.get(fid)
            if not info:
                continue
            hasil.append({
                "tmdbId": int(fid),
                "title": info.get("title"),
                "year": info.get("year"),
                "genres": info.get("genres"),
                "vote_average": info.get("vote_average"),
                "poster_url": self.get_poster_url(int(fid), POSTER_KECIL),
            })
        return hasil

    # -------------------------------------------------------------------------
    # DASHBOARD — baris film per kategori (non-personal, berbasis popularitas)
    # -------------------------------------------------------------------------
    def _film_cards(self, df, ukuran_poster=POSTER_SEDANG):
        """Ubah DataFrame film menjadi list dict siap tampil."""
        cards = []
        for _, row in df.iterrows():
            g = row.get("genres")
            try:
                year = int(row["year"])
            except (TypeError, ValueError):
                year = "-"
            fid = int(row["tmdbId"])
            cards.append({
                "tmdbId": fid,
                "title": row.get("title"),
                "year": year,
                "genres_display": (str(g).replace("|", ", ")
                                   if isinstance(g, str) and g else "-"),
                "vote_average": row.get("vote_average"),
                "poster_url": self.get_poster_url(fid, ukuran_poster),
            })
        return cards

    def _build_dashboard(self, n=DASHBOARD_ROW_SIZE):
        """Rakit baris-baris dashboard. CATATAN: ini BUKAN rekomendasi personal —
        murni popularitas/penilaian, sama untuk semua pengguna. Rekomendasi
        personal ada di method recommend() (pipeline CF -> CBF -> XGBoost)."""
        rows = []

        # 1. Film rating tertinggi. Disaring minimal jumlah voter supaya baris ini
        #    tidak diisi film obscure bernilai 10 dari segelintir penilai.
        top = self.film[self.film["vote_count"] >= DASHBOARD_MIN_VOTES]
        top = top.sort_values("vote_average", ascending=False).head(n)
        rows.append({
            "title": "Film Rating Tertinggi",
            "subtitle": f"Minimal {DASHBOARD_MIN_VOTES} penilaian",
            "genre": None,
            "films": self._film_cards(top),
        })

        # 2. Baris per genre dengan film terbanyak. Urutan dalam baris memakai
        #    vote_count sebagai proksi popularitas (konsisten dengan halaman
        #    rating awal yang juga memakai vote_count).
        gc = Counter()
        for g in self.film["genres"].dropna():
            for x in str(g).split("|"):
                if x:
                    gc[x] += 1

        for genre, _ in gc.most_common(DASHBOARD_GENRE_ROWS):
            sel = self.film[self.film["genres"].fillna("").apply(
                lambda s: genre in s.split("|"))]
            sel = sel.sort_values("vote_count", ascending=False).head(n)
            rows.append({
                "title": f"Film {genre} Terpopuler",
                "subtitle": None,
                "genre": genre,
                "films": self._film_cards(sel),
            })

        return rows

    # -------------------------------------------------------------------------
    # KATALOG — telusuri semua film (search + filter genre + pagination)
    # -------------------------------------------------------------------------
    def browse_films(self, genre=None, query=None, year_decade=None,
                     sort="title", page=1, per_page=50):
        df = self.film

        if query:
            q = query.strip()
            if q:
                df = df[df["title"].str.contains(q, case=False, na=False, regex=False)]

        if genre:
            df = df[df["genres"].fillna("").apply(lambda g: genre in g.split("|"))]

        if year_decade is not None:
            try:
                dec = int(year_decade)
                df = df[(df["year"] // 10 * 10) == dec]
            except (TypeError, ValueError):
                pass

        # Urutkan sesuai pilihan sort
        if sort == "rating":
            df = df.sort_values("vote_average", ascending=False, na_position="last")
        elif sort == "year":
            df = df.sort_values("year", ascending=False, na_position="last")
        else:  # default: judul A-Z
            df = df.sort_values("title", key=lambda s: s.astype(str).str.lower(),
                                na_position="last")

        total = len(df)
        per_page = max(1, int(per_page))
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(int(page), total_pages))
        start = (page - 1) * per_page
        page_df = df.iloc[start:start + per_page]

        films = self._film_cards(page_df, ukuran_poster=POSTER_KECIL)

        return {
            "films": films,
            "total": total,
            "page": page,
            "total_pages": total_pages,
        }

    # -------------------------------------------------------------------------
    # POPULAR FILMS — untuk halaman rating awal (20 film vote_count tertinggi)
    # -------------------------------------------------------------------------
    def get_popular_films(self, n=20):
        pop = self.film.sort_values("vote_count", ascending=False).head(n)
        return pop[["tmdbId", "title", "year", "genres", "vote_average"]].to_dict("records")

    # -------------------------------------------------------------------------
    # CF — prediksi skor untuk user baru
    # -------------------------------------------------------------------------
    def _cf_score_new_user(self, user_ratings, top_n=TOP_N_CF):
        rated_tmdb_ids = list(user_ratings.keys())
        valid_ids = [t for t in rated_tmdb_ids if t in self.known_films]
        if len(valid_ids) == 0:
            raise ValueError("Tidak ada film rating awal yang dikenali sistem.")

        rated_idx = self.item_enc.transform(valid_ids)
        ratings_arr = np.array([user_ratings[t] for t in valid_ids], dtype=float)
        user_mean = ratings_arr.mean()
        centered = ratings_arr - user_mean

        sim_sub = self.item_sim[:, rated_idx].toarray()   # (n_films, n_rated)
        numerator = sim_sub @ centered
        denominator = np.abs(sim_sub).sum(axis=1)

        with np.errstate(divide="ignore", invalid="ignore"):
            pred = np.where(denominator > 0, user_mean + numerator / denominator, user_mean)

        pred[rated_idx] = -np.inf  # mask film yang sudah dirating

        cf_df = pd.DataFrame({
            "tmdbId": self.item_enc.classes_,
            "cf_score": pred,
        })
        cf_df = cf_df[cf_df["cf_score"] > -np.inf]
        cf_df = cf_df.merge(self.film[["tmdbId", "vote_count"]], on="tmdbId", how="left")
        cf_df = cf_df.sort_values(["cf_score", "vote_count"], ascending=[False, False])
        cf_df = cf_df.head(top_n).reset_index(drop=True)
        cf_df["cf_rank"] = np.arange(1, len(cf_df) + 1)

        return cf_df[["tmdbId", "cf_score", "cf_rank"]], user_mean

    # -------------------------------------------------------------------------
    # CBF — re-rank berdasarkan konten
    # -------------------------------------------------------------------------
    def _build_user_profile(self, user_ratings, like_threshold=RELEVANCE_LIKE_THRESHOLD):
        liked_ids = [t for t, r in user_ratings.items() if r >= like_threshold]
        liked_idx = [self.fid_to_idx[t] for t in liked_ids if t in self.fid_to_idx]
        if len(liked_idx) == 0:
            return None
        profile_vec = np.asarray(self.tfidf_matrix[liked_idx].sum(axis=0))
        norm = np.linalg.norm(profile_vec)
        if norm > 0:
            profile_vec = profile_vec / norm
        return profile_vec

    def _cbf_rerank(self, cf_candidates_df, user_ratings, top_n=TOP_N_CBF):
        profile_vec = self._build_user_profile(user_ratings)
        cand_ids = cf_candidates_df["tmdbId"].tolist()

        cbf_scores = []
        for t in cand_ids:
            idx = self.fid_to_idx.get(t, None)
            if profile_vec is None or idx is None:
                cbf_scores.append(0.0)
            else:
                film_vec = self.tfidf_matrix[idx].toarray()
                cbf_scores.append(float((profile_vec @ film_vec.T).ravel()[0]))

        df = cf_candidates_df.copy()
        df["cbf_score"] = cbf_scores
        df = df.sort_values(["cbf_score", "cf_rank"], ascending=[False, True])
        df = df.head(top_n).reset_index(drop=True)
        df["cbf_rank"] = np.arange(1, len(df) + 1)
        return df

    # -------------------------------------------------------------------------
    # FEATURE ENGINEERING — 15 fitur XGBoost
    # -------------------------------------------------------------------------
    def _build_features(self, cbf_top20_df, user_ratings,
                        like_threshold=RELEVANCE_LIKE_THRESHOLD):
        df = cbf_top20_df.merge(self.film, on="tmdbId", how="left")
        df["movie_year"] = df["year"]

        ratings_arr = np.array(list(user_ratings.values()), dtype=float)
        df["user_avg_rating"] = ratings_arr.mean()
        df["user_rating_count"] = len(ratings_arr)
        df["user_rating_std"] = ratings_arr.std()

        liked_ids = [t for t, r in user_ratings.items() if r >= like_threshold]
        liked_films = self.film[self.film["tmdbId"].isin(liked_ids)]

        if len(liked_films) > 0:
            liked_genres = set()
            for g in liked_films["genres"].fillna(""):
                liked_genres.update(g.split("|") if g else [])
            avg_liked_year = liked_films["year"].mean()
        else:
            liked_genres = set()
            avg_liked_year = df["movie_year"].mean()

        def genre_match(genres_str):
            if pd.isna(genres_str) or genres_str == "" or len(liked_genres) == 0:
                return 0.0
            film_genres = set(genres_str.split("|"))
            return len(film_genres & liked_genres) / len(liked_genres)

        df["genre_match_score"] = df["genres"].apply(genre_match)
        df["year_preference"] = np.abs(df["movie_year"] - avg_liked_year)
        return df

    # -------------------------------------------------------------------------
    # "MENGAPA DIREKOMENDASIKAN?" — rakit kalimat alasan (versi simpel)
    # -------------------------------------------------------------------------
    def _user_liked_genres(self, user_ratings, like_threshold=RELEVANCE_LIKE_THRESHOLD):
        """Kumpulan genre dari film yang user beri rating >= threshold (disukai)."""
        liked = set()
        for t, r in user_ratings.items():
            if r >= like_threshold:
                info = self.film_by_id.get(int(t))
                if info and isinstance(info.get("genres"), str):
                    for g in info["genres"].split("|"):
                        if g:
                            liked.add(g)
        return liked

    def _build_reasons(self, rec, liked_genres):
        """Rakit 1-3 kalimat alasan untuk satu film rekomendasi."""
        reasons = []

        # 1. Kecocokan genre (paling personal & mudah dipahami)
        genres_str = rec.get("genres")
        if isinstance(genres_str, str) and genres_str and liked_genres:
            film_genres = [g for g in genres_str.split("|") if g]
            matched = [g for g in film_genres if g in liked_genres]
            if matched:
                reasons.append("Sesuai genre yang kamu sukai: " + ", ".join(matched))

        # 2. Kemiripan konten (dari CBF) — tema/gaya/pemain mirip film favorit
        if rec.get("cbf_score", 0) is not None and rec.get("cbf_score", 0) >= CBF_HIGH:
            reasons.append("Tema dan gaya filmnya mirip dengan film yang kamu sukai")

        # 3. Kualitas / penilaian penonton
        va = rec.get("vote_average")
        if va is not None and va >= VOTE_AVG_HIGH:
            reasons.append("Termasuk film dengan penilaian penonton yang tinggi")

        # Fallback: pastikan selalu ada minimal 1 alasan
        if not reasons:
            reasons.append("Direkomendasikan dari kombinasi selera dan pola ratingmu")

        return reasons

    # -------------------------------------------------------------------------
    # ORKESTRASI LENGKAP
    # -------------------------------------------------------------------------
    def recommend(self, user_ratings, top_n=TOP_N_FINAL):
        """
        user_ratings: dict {tmdbId(int): rating(float)}
        Mengembalikan list of dict: rekomendasi Top-N final, tiap dict berisi
        skor + kunci 'reasons' (list kalimat alasan).
        """
        cf_candidates, _ = self._cf_score_new_user(user_ratings)
        cbf_top20 = self._cbf_rerank(cf_candidates, user_ratings)
        features_df = self._build_features(cbf_top20, user_ratings)

        X = features_df[self.feature_cols].fillna(0)
        features_df = features_df.copy()
        features_df["xgb_score"] = self.xgb_model.predict_proba(X)[:, 1]

        result = features_df.sort_values("xgb_score", ascending=False).head(top_n)
        cols = ["tmdbId", "title", "xgb_score", "cf_score", "cbf_score",
                "vote_average", "vote_count", "genres", "year"]
        recs = result[cols].to_dict("records")

        # Rakit alasan + lampirkan poster & trailer tiap film
        liked_genres = self._user_liked_genres(user_ratings)
        for rec in recs:
            rec["reasons"] = self._build_reasons(rec, liked_genres)
            rec["poster_url"] = self.get_poster_url(rec["tmdbId"], POSTER_SEDANG)
            rec["trailer_url"] = self.get_trailer_url(rec["tmdbId"])

        return recs
