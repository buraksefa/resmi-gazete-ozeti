"""Faz 1 çevrimdışı testleri: gerçek fihrist/HTML örnekleri (27 Ekim – 2 Kasım 2026).
Çalıştır: python3 -m unittest discover -s tests"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FX = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("GAZETE_CONTACT", "test@example.invalid")

from gazete import amend, db, parse  # noqa: E402
from gazete.fetch import parse_robots_noindex  # noqa: E402


def fx(name):
    return (FX / name).read_text(encoding="utf-8")


class FihristTest(unittest.TestCase):
    def test_gunluk_sayi(self):
        f = parse.parse_fihrist(fx("gun-2026-10-31.html"))
        self.assertEqual((f.tarih, f.sayi, f.mukerrer), ("2026-10-31", 33416, 0))
        mevzuat = [i for i in f.items if "İLÂN" not in i.bolum]
        ilan = [i for i in f.items if "İLÂN" in i.bolum]
        self.assertEqual(len(mevzuat), 20)
        self.assertEqual(len(ilan), 4)
        self.assertTrue(all(i.url.startswith("https://www.resmigazete.gov.tr/") for i in f.items))
        epdk = [i for i in f.items if i.url.endswith("20261031-2.htm")]
        self.assertEqual(len(epdk), 2)  # mevzuat + ilan aynı dosya adı
        self.assertEqual({i.bolum for i in epdk}, {"YÜRÜTME VE İDARE BÖLÜMÜ", "İLÂN BÖLÜMÜ"})

    def test_ilan_alt_basligi_sifirlanir(self):
        f = parse.parse_fihrist(fx("gun-2026-10-31.html"))
        for i in f.items:
            if "İLÂN" in i.bolum:
                self.assertEqual(i.alt_baslik, "")
                self.assertEqual(parse.tur_bul(i.bolum, i.alt_baslik, i.baslik), "ilan")

    def test_mukerrer_baglantisi_ve_sayfasi(self):
        f = parse.parse_fihrist(fx("gun-2026-10-27.html"))
        self.assertEqual(f.mukerrer_links, [("2026-10-27", 1)])
        self.assertFalse(any("fihrist?" in i.url for i in f.items))
        m = parse.parse_fihrist(fx("mukerrer-2026-10-27-1.html"))
        self.assertEqual((m.tarih, m.sayi, m.mukerrer), ("2026-10-27", 33413, 1))
        self.assertEqual(len(m.items), 1)
        self.assertTrue(m.items[0].url.endswith("20261027M1-1.pdf"))

    def test_yayim_yok(self):
        f = parse.parse_fihrist(fx("gun-2026-10-29.html"))
        self.assertTrue(f.yayim_yok)
        self.assertEqual(f.items, [])

    def test_turler(self):
        f = parse.parse_fihrist(fx("gun-2026-10-31.html"))
        turler = {parse.tur_bul(i.bolum, i.alt_baslik, i.baslik) for i in f.items}
        self.assertEqual(turler, {"kararname", "andlaşma", "karar", "yönetmelik", "tebliğ", "ilan"})
        f = parse.parse_fihrist(fx("gun-2026-10-27.html"))
        self.assertEqual(parse.tur_bul(f.items[0].bolum, f.items[0].alt_baslik, f.items[0].baslik), "kanun")


class MetinTest(unittest.TestCase):
    def setUp(self):
        self.metin = parse.html_to_text((FX / "20261031-2.htm").read_bytes())

    def test_cp1254_ve_temizlik(self):
        self.assertIn("ORGANİZE TOPTAN DOĞAL GAZ SATIŞ PİYASASI", self.metin)
        self.assertNotIn("MicrosoftInternetExplorer4", self.metin)
        self.assertIn("MADDE 2- Bu Yönetmelik 1/1/2027 tarihinde yürürlüğe girer.", self.metin)

    def test_kurum(self):
        self.assertEqual(parse.kurum_bul(self.metin, "yönetmelik"), "Enerji Piyasası Düzenleme Kurumu")
        self.assertEqual(parse.kurum_bul("", "karar",
                         "Enerji Piyasası Düzenleme Kurulunun 15/10/2026 Tarihli ve 14958 Sayılı Kararı"),
                         "Enerji Piyasası Düzenleme Kurulu")
        self.assertEqual(parse.kurum_bul("", "yargı kararı",
                         "Anayasa Mahkemesinin 5/5/2026 Tarihli ve 2021/32883 Başvuru Numaralı Kararı"),
                         "Anayasa Mahkemesi")

    def test_degisiklik_gercek_ornek(self):
        ds = amend.ayristir(self.metin)
        self.assertEqual(len(ds), 1)
        d = ds[0]
        self.assertEqual((d.kaynak_madde, d.hedef_tarih, d.hedef_sayi), ("1", "2017-03-31", 30024))
        self.assertEqual(d.hedef_baslik, "Organize Toptan Doğal Gaz Satış Piyasası Yönetmeliği")
        self.assertEqual((d.hedef_madde, d.islem, d.karmasik), ("13", "ekleme", False))
        self.assertTrue(d.yeni_metin.startswith("(4) 14/2/2026 tarihli"))


SENTETIK = """YÖNETMELİK
Örnek Kurumundan:
ÖRNEK YÖNETMELİKTE DEĞİŞİKLİK YAPILMASINA DAİR YÖNETMELİK
MADDE 1- 5/1/2026 tarihli ve 33130 sayılı Resmî Gazete’de yayımlanan Örnek Hizmetler Yönetmeliğinin 5 inci maddesinin birinci fıkrası aşağıdaki şekilde değiştirilmiştir.
“(1) Başvurular elektronik ortamda yapılır.”
MADDE 2- Aynı Yönetmeliğin 7 nci maddesi yürürlükten kaldırılmıştır.
MADDE 3- Aynı Yönetmeliğin 9 uncu ve 10 uncu maddeleri yürürlükten kaldırılmıştır.
MADDE 4- 2/2/2026 tarihli ve 33150 sayılı Resmî Gazete’de yayımlanan Ücret Tarifesi Tebliğine aşağıdaki geçici madde eklenmiştir.
“GEÇİCİ MADDE 1- (1) Geçiş hükmü.”
MADDE 5- Bu Yönetmelik yayımı tarihinde yürürlüğe girer.
"""

HEDEF = """YÖNETMELİK
Örnek Kurumundan:
ÖRNEK HİZMETLER YÖNETMELİĞİ
MADDE 5- (1) Başvurular yazılı olarak yapılır.
(2) Ek belge istenebilir.
MADDE 6- Diğer hükümler.
"""


class DegisiklikTest(unittest.TestCase):
    def test_sentetik(self):
        ds = amend.ayristir(SENTETIK)
        ozet = [(d.kaynak_madde, d.hedef_tarih, d.hedef_madde, d.islem, d.karmasik) for d in ds]
        self.assertEqual(ozet, [
            ("1", "2026-01-05", "5", "değiştirme", False),
            ("2", "2026-01-05", "7", "yürürlükten kaldırma", False),
            ("3", "2026-01-05", "9", "yürürlükten kaldırma", True),
            ("4", "2026-02-02", None, "ekleme", False),
        ])
        self.assertEqual(ds[0].hedef_baslik, "Örnek Hizmetler Yönetmeliği")
        self.assertEqual(ds[3].hedef_baslik, "Ücret Tarifesi Tebliği")
        self.assertEqual(ds[0].yeni_metin, "(1) Başvurular elektronik ortamda yapılır.")

    def test_baslik_yalin(self):
        self.assertEqual(amend.baslik_yalin("X Yönetmeliğinin"), "X Yönetmeliği")
        self.assertEqual(amend.baslik_yalin("Y Tebliğin"), "Y Tebliğ")
        self.assertEqual(amend.baslik_yalin("Z Kanununun"), "Z Kanunu")
        self.assertEqual(amend.baslik_yalin("Q Kararnamesinin"), "Q Kararnamesi")

    def test_hedefe_baglama_ve_onceki_hal(self):
        with tempfile.TemporaryDirectory() as td:
            con = db.connect(Path(td) / "t.sqlite")
            ins = ("INSERT INTO duzenlemeler (id,tarih,sayi,mukerrer,sira,bolum,alt_baslik,kurum,tur,baslik,metin,"
                   "kaynak_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
            con.execute(ins, ("eskiler/20260105-3", "2026-01-05", 33130, 0, 3, "YÜRÜTME VE İDARE BÖLÜMÜ",
                              "YÖNETMELİKLER", "Örnek Kurumu", "yönetmelik", "Örnek Hizmetler Yönetmeliği", HEDEF, "u1"))
            con.execute(ins, ("eskiler/20261101-1", "2026-11-01", 33417, 0, 1, "YÜRÜTME VE İDARE BÖLÜMÜ",
                              "YÖNETMELİKLER", "Örnek Kurumu", "yönetmelik",
                              "Örnek Hizmetler Yönetmeliğinde Değişiklik Yapılmasına Dair Yönetmelik", SENTETIK, "u2"))
            from gazete import collect
            self.assertEqual(collect.link_item(con, "eskiler/20261101-1"), 4)
            self.assertEqual(collect.resolve_links(con), 3)   # 4. madde başka hedef, arşivde yok
            r = con.execute("SELECT * FROM degisiklikler WHERE kaynak_madde='1'").fetchone()
            self.assertEqual(r["hedef_id"], "eskiler/20260105-3")
            self.assertTrue(r["onceki_madde"].startswith("MADDE 5- (1) Başvurular yazılı"))
            self.assertEqual(r["ara_degisiklik"], 0)
            r = con.execute("SELECT * FROM degisiklikler WHERE kaynak_madde='4'").fetchone()
            self.assertIsNone(r["hedef_id"])


class IlanTest(unittest.TestCase):
    def test_dizin(self):
        raw = ('<html><head><meta charset="windows-1254"></head><body><p>Bodrum Mahkemesinden:</p>'
               '<a href="20261102-2-1.pdf">x</a><a href="20261102-2-1.pdf">x</a><a href="20261102-2-2.pdf">y</a>'
               '<img src="a_dosyalar/image001.jpg"></body></html>').encode("cp1254")
        pdfler, gorsel = parse.ilan_dizini(raw, "https://www.resmigazete.gov.tr/ilanlar/eskiilanlar/2026/11/20261102-2.htm")
        self.assertEqual(pdfler, ["https://www.resmigazete.gov.tr/ilanlar/eskiilanlar/2026/11/20261102-2-1.pdf",
                                  "https://www.resmigazete.gov.tr/ilanlar/eskiilanlar/2026/11/20261102-2-2.pdf"])
        self.assertEqual(gorsel, 1)


class RobotsTest(unittest.TestCase):
    def test_noindex(self):
        p = parse_robots_noindex(fx("robots.txt"))
        self.assertIn("/eskiler/2023/11/20231103-11.pdf", p)
        self.assertIn("/ilanlar/eskiilanlar/2018/11/20181104-2 (7).pdf", p)


if __name__ == "__main__":
    unittest.main()
