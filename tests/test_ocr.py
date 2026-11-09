"""OCR ve doğrulama testleri. PDF/tesseract gerektirenler araç yoksa atlanır.
Fixture: 20261101-6.pdf — 1 Kasım 2026 atama kararları, 5 sayfa, gövdesi taranmış."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FX = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent))
os.environ["GAZETE_YAYIN"] = ""   # testler hiçbir zaman gerçek siteye yükleme yapmasın
os.environ.setdefault("GAZETE_CONTACT", "test@example.invalid")

from gazete import db, dogrula, ocr  # noqa: E402

ATAMA_PDF = FX / "20261101-6.pdf"
POPPLER = shutil.which("pdftotext") and shutil.which("pdfimages")
TESSERACT = POPPLER and shutil.which("pdftoppm") and shutil.which("tesseract")

TSV = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "1\t1\t0\t0\t0\t0\t0\t0\t10\t10\t-1\t",
    "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t96.1\tKarar:",
    "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t95.0\t2026/364",
    "5\t1\t2\t1\t1\t1\t0\t0\t1\t1\t91.0\t—",
    "5\t1\t2\t1\t1\t2\t0\t0\t1\t1\t72.4\tKurşehir",
    "5\t1\t2\t1\t2\t1\t0\t0\t1\t1\t93.0\tCemal",
    "5\t1\t2\t1\t2\t2\t0\t0\t1\t1\t81.0\tKESKİN,",
])


class TsvTest(unittest.TestCase):
    def test_tek_gecis_metin(self):
        metin, kel = ocr.tsv_metin(TSV)
        self.assertEqual(metin, "Karar: 2026/364\n\n— Kurşehir\nCemal KESKİN,")
        self.assertEqual(kel[3], ("Kurşehir", 72.4))


class KontrolTest(unittest.TestCase):
    def bilgi(self, dusuk):
        return {"sayfalar": [{"no": 1, "durum": "ocr", "dusuk": dusuk}]}

    def test_il_adi_ve_numara(self):
        metin = "— Kurşehir İl Müdürlüğüne, Karabük İl Müdürü ...\neki (0) sayılı Cetvelin"
        n = ocr.kontrol_nedenleri("karar", metin, self.bilgi([]))
        self.assertIn("il adına benziyor, il değil: Kurşehir", n)
        self.assertIn("şüpheli numara: (0) sayılı", n)
        self.assertEqual(len(n), 2)  # Karabük doğru il; karar türünde düşük güven bakılmaz

    def test_dogru_metin_temiz(self):
        metin = "Kırşehir İl Müdürlüğüne, Bolu İl Müdürü Cemal KESKİN; eki (I) sayılı Cetvel; (1) Sayılı Liste"
        self.assertEqual(ocr.kontrol_nedenleri("atama", metin, self.bilgi([["ve", 70.0]])), [])
        # il bağlamı dışındaki benzer sözcükler (Adına~Adana, Tonya İl Yolu~Konya) işaretlenmez
        self.assertEqual(ocr.kontrol_nedenleri("andlaşma", "Hükümeti Adına\nTonya İl Yolu", self.bilgi([])), [])

    def test_basliktaki_sayilar(self):
        baslik = "Tonya İl Yolu Projesi (Km:23+016.00-23+224.00 Arası) Hakkında Karar (Karar Sayısı: 11938)"
        metin = "Karar Sayısı: 11938 ... Tonya İl Yolu Projesi (Km:234-016.00-234224.00 arası)"
        n = ocr.kontrol_nedenleri("karar", metin, self.bilgi([]), baslik)
        self.assertEqual(n, ["başlıktaki sayı/tarih metinde yok: 224.00", "başlıktaki sayı/tarih metinde yok: 23"])

    def test_atamada_dusuk_guvenli_ad(self):
        n = ocr.kontrol_nedenleri("atama", "Cemal KESKİN", self.bilgi([["KESKİN,", 81.0], ["ve", 60.0]]))
        self.assertEqual(n, ["düşük güvenli ad/yer: KESKİN, (81)"])

    def test_dil_orani(self):
        self.assertGreater(ocr.tr_orani("Taraflar bu Anlaşma ile ilgili olarak ve her durumda"), ocr.TR_ORAN)
        # Azerbaycanca sayfanın tesseract (tur) çıktısından
        az = ("4. Taraflar ticarstlo bağlı inzibati sonodloro aid molumatın elektron formada "
              "mübadiləsini tomin etmək üçün öz qanunvericiliyinə uyğun olaraq tədbirlər görür")
        self.assertLess(ocr.tr_orani(az), ocr.TR_ORAN)


class SayiDenetimTest(unittest.TestCase):
    KAYNAK = ("31/10/2026 tarihli ve 7545 sayılı Kanunun eki (I) sayılı Cetvel; "
              "ödenek 1.250.000 TL, Karar: 2026/364")

    def test_dogru_ozet(self):
        ozet = "31 Ekim 2026'da 7545 sayılı Kanunda değişiklik; 1250000 TL ödenek (Karar 2026/364)."
        self.assertEqual(dogrula.sayi_denetimi(ozet, self.KAYNAK), [])

    def test_yanlis_sayi_ve_tarih(self):
        ozet = "30 Ekim 2026 tarihli 7546 sayılı Kanun"
        self.assertEqual(dogrula.sayi_denetimi(ozet, self.KAYNAK), ["2026-10-30", "7546"])


class GocTest(unittest.TestCase):
    def test_eski_veritabanina_sutun_eklenir(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "g.sqlite"
            c = sqlite3.connect(p)
            c.execute("CREATE TABLE duzenlemeler (id TEXT PRIMARY KEY, tarih TEXT NOT NULL, sayi INTEGER, "
                      "mukerrer INTEGER NOT NULL, sira INTEGER NOT NULL, bolum TEXT, alt_baslik TEXT, kurum TEXT, "
                      "tur TEXT, baslik TEXT NOT NULL, metin TEXT, kaynak_url TEXT NOT NULL, ham_yol TEXT, "
                      "bicim TEXT, sha256 TEXT, cekildi TEXT, hata TEXT, deneme INTEGER NOT NULL DEFAULT 0, "
                      "ocr_gerekli INTEGER NOT NULL DEFAULT 0)")
            c.execute("INSERT INTO duzenlemeler (id, tarih, mukerrer, sira, baslik, kaynak_url) "
                      "VALUES ('eskiler/x', '2026-10-31', 0, 1, 'b', 'u')")
            c.commit()
            c.close()
            con = db.connect(p)
            r = con.execute("SELECT ocr_durum, kontrol_gerekli FROM duzenlemeler").fetchone()
            self.assertEqual((r["ocr_durum"], r["kontrol_gerekli"]), (None, 0))
            db.connect(p).close()  # ikinci açılış hata vermemeli


@unittest.skipUnless(POPPLER, "poppler-utils yok")
class TespitTest(unittest.TestCase):
    def test_cok_sayfali_tarama(self):
        # Eski belge düzeyi kontrol 901 karakter yazı nedeniyle bu belgeyi kaçırıyordu
        self.assertEqual(ocr.taranmis_sayfalar(ATAMA_PDF), [1, 2, 3, 4, 5])


@unittest.skipUnless(TESSERACT, "tesseract/poppler yok")
class OcrTest(unittest.TestCase):
    def test_atama_sayfa1(self):
        with tempfile.TemporaryDirectory() as d:
            metin, kel = ocr.ocr_sayfa(ATAMA_PDF, 1, d)
        for ad in ("ÇAĞDAŞ", "BİLGİÇ", "KESKİN", "KORKMAZ", "Karar: 2026/364"):
            self.assertIn(ad, metin)
        self.assertGreater(sum(c for _, c in kel if c >= 0) / len(kel), 85)
        n = ocr.kontrol_nedenleri("atama", metin, {"sayfalar": []})
        if "Kurşehir" in metin:  # bilinen OCR hatası yakalanmalı
            self.assertIn("il adına benziyor, il değil: Kurşehir", n)


if __name__ == "__main__":
    unittest.main()
