"""Utilitas rating gabungan (TMDB + pengguna aplikasi).

Dipisah ke modul sendiri agar dipakai bersama oleh blueprint dashboard,
katalog, dan recommend tanpa saling impor.
"""


def hitung_rating_gabungan(vote_average, vote_count, ratings_user):
    """Gabungkan rating TMDB dengan rating pengguna aplikasi (rata-rata berbobot).

    - vote_average, vote_count : nilai & jumlah penilai TMDB (skala 0-10).
    - ratings_user             : daftar rating pengguna aplikasi (skala 1-5),
                                 dikonversi ke skala 10 dengan dikali 2.

    Mengembalikan (rating_gabungan_bulat2, jumlah_pengguna_aplikasi).
    Rumus: (va*vc + Σ(rating_user*2)) / (vc + n) — sama seperti IMDb.
    Film dengan vc besar nyaris tak bergeser; efek terlihat pada vc kecil.
    """
    try:
        va = float(vote_average)
    except (TypeError, ValueError):
        va = 0.0
    try:
        vc = float(vote_count)
        if vc != vc:            # NaN
            vc = 0.0
    except (TypeError, ValueError):
        vc = 0.0

    n = len(ratings_user)
    if n == 0:
        return round(va, 2), 0

    total_user_skala10 = sum(float(r) * 2 for r in ratings_user)
    gabungan = (va * vc + total_user_skala10) / (vc + n)
    return round(gabungan, 2), n


def enrich_films(films, ratings_map):
    """Tambahkan rating_gabungan, jumlah_user_app, total_penilai ke tiap film.

    films       : list dict film (masing-masing punya tmdbId, vote_average,
                  vote_count).
    ratings_map : {tmdb_id: [rating pengguna]} dari get_all_film_user_ratings().
    Mengubah dict di tempat lalu mengembalikannya.
    """
    for f in films:
        fid = f.get("tmdbId")
        ratings_user = ratings_map.get(int(fid), []) if fid is not None else []
        va = f.get("vote_average")
        vc = f.get("vote_count")
        gab, n = hitung_rating_gabungan(va, vc, ratings_user)
        try:
            vc_int = int(vc)
        except (TypeError, ValueError):
            vc_int = 0
        f["rating_gabungan"] = gab
        f["jumlah_user_app"] = n
        f["total_penilai"] = vc_int + n
    return films
