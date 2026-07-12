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

    # -------------------------------------------------------------------------
    # LOOKUP — ambil info 1 film berdasarkan tmdbId (untuk profil & detail film)
    # -------------------------------------------------------------------------
    def get_film_info(self, tmdb_id):
        """Kembalikan dict info film berdasarkan tmdbId, atau None kalau tidak ada."""
        return self.film_by_id.get(int(tmdb_id))

    def get_overview(self, tmdb_id):
        """Sinopsis film (string). Kosong kalau tidak tersedia."""
        return self.overview_by_id.get(int(tmdb_id), "")

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
            })
        return hasil

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

        # Rakit alasan tiap film
        liked_genres = self._user_liked_genres(user_ratings)
        for rec in recs:
            rec["reasons"] = self._build_reasons(rec, liked_genres)

        return recs
