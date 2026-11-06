"""SQLite şeması ve yardımcılar."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS sayilar (
    tarih        TEXT NOT NULL,          -- YYYY-MM-DD
    mukerrer     INTEGER NOT NULL,       -- 0 = asıl sayı, 1.. = n. mükerrer
    sayi         INTEGER,
    durum        TEXT NOT NULL,          -- yayimlandi | yok | eksik
    fihrist_url  TEXT NOT NULL,
    son_kontrol  TEXT NOT NULL,          -- ISO zaman (Europe/Istanbul)
    PRIMARY KEY (tarih, mukerrer)
);

CREATE TABLE IF NOT EXISTS duzenlemeler (
    id           TEXT PRIMARY KEY,       -- eskiler/20261031-2, ilanlar/20261031-2
    tarih        TEXT NOT NULL,
    sayi         INTEGER,
    mukerrer     INTEGER NOT NULL,
    sira         INTEGER NOT NULL,       -- fihristteki sırası
    bolum        TEXT,
    alt_baslik   TEXT,
    kurum        TEXT,
    tur          TEXT,                   -- kanun|yönetmelik|tebliğ|karar|kararname|atama|...|ilan
    baslik       TEXT NOT NULL,
    metin        TEXT,                   -- NULL = henüz çıkarılamadı (bkz. hata)
    kaynak_url   TEXT NOT NULL,
    ham_yol      TEXT,
    bicim        TEXT,                   -- htm | pdf
    sha256       TEXT,
    cekildi      TEXT,
    hata         TEXT,
    deneme       INTEGER NOT NULL DEFAULT 0,  -- başarısız indirme/çıkarma sayısı
    ocr_gerekli  INTEGER NOT NULL DEFAULT 0,  -- en az bir sayfanın gövdesi taranmış görüntü
    metin_kaynagi TEXT,                  -- html | pdf | pdf+ocr | ocr
    ocr_durum    TEXT,                   -- NULL = bakılmadı | yok | bekliyor | tamam | hata
    ocr_bilgi    TEXT,                   -- JSON: sayfa başına güven, düşük güvenli sözcükler, atlananlar
    kontrol_gerekli INTEGER NOT NULL DEFAULT 0,  -- 1 = özet insan onayı olmadan yayımlanmaz
    kontrol_neden TEXT                   -- satır satır nedenler
);
CREATE INDEX IF NOT EXISTS ix_duz_tarih ON duzenlemeler(tarih, sayi);

CREATE TABLE IF NOT EXISTS degisiklikler (
    id               INTEGER PRIMARY KEY,
    kaynak_id        TEXT NOT NULL REFERENCES duzenlemeler(id),
    kaynak_madde     TEXT,               -- değiştiren düzenlemenin maddesi ("1", "GEÇİCİ 1")
    hedef_tarih      TEXT,               -- YYYY-MM-DD
    hedef_sayi       INTEGER,
    hedef_baslik     TEXT,
    hedef_madde      TEXT,               -- "13", "GEÇİCİ 2", NULL = madde belirtilmemiş
    islem            TEXT,               -- değiştirme | ekleme | yürürlükten kaldırma | çıkarma | diğer
    cumle            TEXT NOT NULL,      -- ayrıştırılan cümle (aynen)
    yeni_metin       TEXT,               -- tırnak içindeki yeni metin, varsa
    karmasik         INTEGER NOT NULL DEFAULT 0,  -- birden çok madde/atıf: otomatik uygulanmamalı
    hedef_id         TEXT REFERENCES duzenlemeler(id),   -- arşivimizdeyse
    onceki_madde     TEXT,               -- hedefin arşivdeki ilk halinden ilgili madde
    ara_degisiklik   INTEGER,            -- aradaki, bildiğimiz başka değişiklik sayısı
    UNIQUE (kaynak_id, kaynak_madde, hedef_tarih, hedef_sayi, hedef_madde, islem)
);
CREATE INDEX IF NOT EXISTS ix_deg_hedef ON degisiklikler(hedef_tarih, hedef_sayi);

CREATE TABLE IF NOT EXISTS ozetler (
    duzenleme_id   TEXT PRIMARY KEY REFERENCES duzenlemeler(id),
    model          TEXT,
    ozet           TEXT,                -- 2–3 cümle; son cümle yürürlük (kuraldan, LLM'den değil)
    yururluk       TEXT,                -- metindeki yürürlük cümlesi (aynen), yoksa NULL
    yururluk_tarih TEXT,                -- YYYY-MM-DD, kesin çıkarılabildiyse
    olgular        TEXT,                -- JSON; atama/vekâlet: [{islem, ad, gorev}]
    durum          TEXT NOT NULL,       -- hazir | kontrol | hata
    kontrol_neden  TEXT,
    girdi_kisaltildi INTEGER NOT NULL DEFAULT 0,
    sure_s         REAL,
    olusturma      TEXT
);

CREATE TABLE IF NOT EXISTS etiketler (
    duzenleme_id TEXT NOT NULL REFERENCES duzenlemeler(id),
    etiket       TEXT NOT NULL,         -- etiketler.toml anahtarı
    kaynak       TEXT NOT NULL,         -- kelime | llm
    PRIMARY KEY (duzenleme_id, etiket)
);
"""


# Sonradan eklenen sütunlar: eski veritabanına ALTER TABLE ile eklenir
EK_SUTUNLAR = {
    "duzenlemeler": [("metin_kaynagi", "TEXT"), ("ocr_durum", "TEXT"), ("ocr_bilgi", "TEXT"),
                     ("kontrol_gerekli", "INTEGER NOT NULL DEFAULT 0"), ("kontrol_neden", "TEXT")],
    "degisiklikler": [("birlestirme", "TEXT"),        # uygulandı | hedef arşivde yok | otomatik birleştirilemedi
                      ("birlestirme_neden", "TEXT"), ("sonraki_madde", "TEXT"), ("fark", "TEXT")],
    # Yayın onayı: NULL = bekliyor | onaylandi | reddedildi. Özet yeniden üretilince sıfırlanır.
    "ozetler": [("onay", "TEXT"), ("onay_zamani", "TEXT"), ("onay_notu", "TEXT"),
                ("elle_duzeltildi", "INTEGER NOT NULL DEFAULT 0")],
}


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=60)  # OCR ve toplayıcı aynı anda yazabilir
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    for tablo, sutunlar in EK_SUTUNLAR.items():
        var = {r["name"] for r in con.execute(f"PRAGMA table_info({tablo})")}
        for ad, tip in sutunlar:
            if ad not in var:
                con.execute(f"ALTER TABLE {tablo} ADD COLUMN {ad} {tip}")
    con.commit()
    return con
