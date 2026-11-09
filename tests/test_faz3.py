"""Faz 3 testleri: onay, site, RSS, taslaklar. Onaysız özet hiçbir çıktıya girmemeli."""
import io
import json
import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ["GAZETE_YAYIN"] = ""   # testler hiçbir zaman gerçek siteye yükleme yapmasın
os.environ.setdefault("GAZETE_CONTACT", "test@example.invalid")

from gazete import config, db, onay, ozet, site, taslak, yayinla  # noqa: E402

ETIKET = {"enerji": {"ad": "Enerji", "aciklama": "Elektrik, doğal gaz", "kelimeler": ["doğal gaz"]}}
GIZLI = "BU-ÖZET-ONAYSIZ"


def kur(con):
    ins = ("INSERT INTO duzenlemeler (id,tarih,sayi,mukerrer,sira,bolum,alt_baslik,kurum,tur,baslik,metin,kaynak_url,"
           "ocr_durum,kontrol_gerekli) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    con.execute("INSERT INTO sayilar VALUES ('2026-10-31',0,33416,'yayimlandi','u','t')")
    con.execute(ins, ("eskiler/20261031-2", "2026-10-31", 33416, 0, 2, "YÜRÜTME VE İDARE BÖLÜMÜ", "YÖNETMELİKLER",
                      "Enerji Piyasası Düzenleme Kurumu", "yönetmelik", "Doğal Gaz Yönetmeliğinde Değişiklik",
                      "MADDE 1- metin", "https://www.resmigazete.gov.tr/eskiler/2026/10/20261031-2.htm", "yok", 0))
    con.execute(ins, ("eskiler/20261031-6", "2026-10-31", 33416, 0, 6, "YÜRÜTME VE İDARE BÖLÜMÜ", "TEBLİĞLER",
                      "Türk Patent ve Marka Kurumu", "tebliğ", "Ücret Tarifesi Tebliği", "MADDE 1- metin",
                      "https://www.resmigazete.gov.tr/eskiler/2026/10/20261031-6.htm", "yok", 0))
    con.execute(ins, ("eskiler/20261101-6", "2026-11-01", 33417, 0, 6, "YÜRÜTME VE İDARE BÖLÜMÜ", "ATAMA KARARLARI",
                      "Cumhurbaşkanlığı", "atama", "Atama Kararları", "Halit ÇAĞDAŞ görevden alınmıştır.",
                      "https://www.resmigazete.gov.tr/eskiler/2026/11/20261101-6.pdf", "tamam", 1))
    oz = ("INSERT INTO ozetler (duzenleme_id, model, ozet, olgular, durum, kontrol_neden) VALUES (?,?,?,?,?,?)")
    con.execute(oz, ("eskiler/20261031-2", "m", "Doğal gaz piyasasında bir fıkra eklendi. " + "x " * 150, None,
                     "hazir", None))
    con.execute(oz, ("eskiler/20261031-6", "m", GIZLI, None, "kontrol", "kaynakta geçmeyen ad: X"))
    con.execute(oz, ("eskiler/20261101-6", "m", "1 kişiye ilişkin karar: 1 görevden alma.",
                     json.dumps([{"islem": "görevden alma", "ad": "Halit ÇAĞDAŞ", "gorev": "Genel Müdür Yardımcısı"}]),
                     "kontrol", "kaynak metin OCR kontrolü bekliyor"))
    con.execute("INSERT INTO etiketler VALUES ('eskiler/20261031-2','enerji','kelime')")
    con.execute("INSERT INTO degisiklikler (kaynak_id, kaynak_madde, hedef_tarih, hedef_sayi, hedef_baslik, hedef_madde,"
                " islem, cumle, birlestirme, birlestirme_neden) VALUES ('eskiler/20261031-2','1','2017-03-31',30024,"
                "'X Yönetmeliği','13','ekleme','… fıkra eklenmiştir.','otomatik birleştirilemedi','bent düzeyinde')")
    con.commit()


class Ortam(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = Path(self.td.name)
        self.p = [mock.patch.object(config, "DB_PATH", d / "g.sqlite"), mock.patch.object(config, "WEB_DIR", d / "web"),
                  mock.patch.object(config, "TASLAK_DIR", d / "taslak"),
                  mock.patch.object(ozet, "etiketleri_yukle", return_value=ETIKET)]
        for x in self.p:
            x.start()
        self.con = db.connect(config.DB_PATH)
        kur(self.con)

    def tearDown(self):
        for x in self.p:
            x.stop()
        self.td.cleanup()

    def komut(self, *argv):
        f = io.StringIO()
        with redirect_stdout(f):
            onay.main(list(argv))
        return f.getvalue()


class OnayTest(Ortam):
    def test_liste_ve_toplu_onay_yalniz_hazir(self):
        out = self.komut("liste")
        self.assertIn("3 özet onay bekliyor (1 uyarısız, 2 kontrol)", out)
        self.assertIn("! kaynakta geçmeyen ad: X", out)
        self.komut("onayla", "--gun", "2026-10-31")
        durum = dict(self.con.execute("SELECT duzenleme_id, onay FROM ozetler").fetchall())
        self.assertEqual(durum["eskiler/20261031-2"], "onaylandi")
        self.assertIsNone(durum["eskiler/20261031-6"])       # kontrol: toplu onaylanmaz

    def test_ocr_kontrolu_onayi_engeller(self):
        out = self.komut("onayla", "20261101-6")
        self.assertIn("OCR kontrolü bekliyor", out)
        self.assertIsNone(self.con.execute("SELECT onay FROM ozetler WHERE duzenleme_id='eskiler/20261101-6'")
                          .fetchone()[0])

    def test_duzelt_reddet_geri(self):
        self.komut("duzelt", "20261031-6", "Elle", "yazılmış", "özet.")
        r = self.con.execute("SELECT ozet, onay, elle_duzeltildi FROM ozetler WHERE duzenleme_id='eskiler/20261031-6'")
        self.assertEqual(tuple(r.fetchone()), ("Elle yazılmış özet.", "onaylandi", 1))
        self.komut("geri", "20261031-6")
        self.komut("reddet", "20261031-2", "eksik")
        self.assertEqual(dict(self.con.execute("SELECT duzenleme_id, onay FROM ozetler").fetchall()),
                         {"eskiler/20261031-2": "reddedildi", "eskiler/20261031-6": None, "eskiler/20261101-6": None})


class SiteTest(Ortam):
    def test_onaysiz_ozet_hicbir_yerde_yok(self):
        self.komut("onayla", "20261031-2")
        yeni = site.uret(self.con)
        self.assertTrue((config.WEB_DIR / "current").is_symlink())
        metinler = {str(p.relative_to(yeni)): p.read_text(encoding="utf-8") for p in yeni.rglob("*") if p.is_file()}
        for ad, m in metinler.items():
            self.assertNotIn(GIZLI, m, ad)
            self.assertNotIn("Halit ÇAĞDAŞ", m, ad)              # onaysız atama listesi de yok
            if ad.endswith(".html"):
                self.assertIn(site.e(site.NOT), m, ad)
        self.assertIn("Doğal gaz piyasasında", metinler["d/20261031-2.html"])
        self.assertIn("Otomatik birleştirilemedi", metinler["d/20261031-2.html"])
        self.assertIn("Özet henüz onaylanmadı", metinler["gun/2026-10-31.html"])
        self.assertEqual(metinler["gun/2026-10-31.html"].count('class="etiket"'), 1)   # yalnız onaylı kalemin
        self.assertIn("kontrol edilene kadar burada gösterilmiyor", metinler["d/20261101-6.html"])
        kanal = ET.fromstring(metinler["rss.xml"]).find("channel")
        self.assertEqual([i.find("guid").text for i in kanal.findall("item")], ["eskiler/20261031-2"])
        self.assertEqual(len(ET.fromstring(metinler["rss/enerji.xml"]).find("channel").findall("item")), 1)
        self.assertEqual([x["u"] for x in json.loads(metinler["ara.json"])], ["/d/20261031-2"])
        # herkese açık site: kanonik adres, X kartı, site haritası, robots
        self.assertIn(f'<link rel="canonical" href="{config.SITE_URL}/d/20261031-2">', metinler["d/20261031-2.html"])
        self.assertIn(f'href="{config.SITE_URL}/"', metinler["index.html"])
        self.assertIn('name="twitter:card"', metinler["index.html"])
        self.assertNotIn("__YOL__", "".join(metinler.values()))
        locs = [x.text for x in ET.fromstring(metinler["sitemap.xml"]).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        self.assertIn(f"{config.SITE_URL}/d/20261031-2", locs)
        self.assertIn(f"{config.SITE_URL}/etiket/", locs)
        self.assertFalse([u for u in locs if u.endswith(".html") or u.endswith("/404")])
        self.assertIn("Sitemap:", metinler["robots.txt"])

    def test_ic_baglantilar_kirik_degil(self):
        self.komut("onayla", "20261031-2")
        yeni = site.uret(self.con)
        var = {"/" + str(p.relative_to(yeni)) for p in yeni.rglob("*") if p.is_file()}
        for p in yeni.rglob("*.html"):
            for h in re.findall(r'(?:href|action)="(/[^"#?]*)"', p.read_text(encoding="utf-8")):
                self.assertTrue(h.startswith(config.SITE_KOK + "/"), h)
                h = h[len(config.SITE_KOK):]
                self.assertTrue({h, h + ".html", h + "index.html"} & var, f"{p.relative_to(yeni)}: {h}")
        self.assertIn("/pico.min.css", var)
        self.assertIn("/favicon.svg", var)

    def test_testte_yayin_kapali(self):
        self.assertEqual(config.YAYIN_HEDEF, "")
        self.assertFalse(yayinla.yayinla(zorla=True))

    def test_alt_klasor_koku(self):
        with mock.patch.object(config, "SITE_KOK", "/resmi-gazete-ozeti"):
            sayfa = site.kok_ekle('<a href="/d/x">a</a><a href="https://x.org/">b</a><form action="/ara"></form>'
                                  '<link href="//cdn.x/y">')
        self.assertEqual(sayfa, '<a href="/resmi-gazete-ozeti/d/x">a</a><a href="https://x.org/">b</a>'
                                '<form action="/resmi-gazete-ozeti/ara"></form><link href="//cdn.x/y">')

    def test_taslak_280_ve_onaysiz_yok(self):
        self.komut("onayla", "20261031-2")
        site.uret(self.con)
        x = (config.TASLAK_DIR / "2026-10-31" / "x.txt").read_text(encoding="utf-8")
        self.assertTrue(x.startswith("DRY-RUN"))
        self.assertNotIn(GIZLI, x)
        for g in x.split("\n\n---\n\n"):
            g = g.split("] ", 1)[-1].strip()
            self.assertLessEqual(taslak.x_uzunluk(g), 280, g)
        self.assertFalse((config.TASLAK_DIR / "2026-11-01").exists())   # o gün onaylı kalem yok

    def test_kelime_farki(self):
        self.assertEqual(site.kelime_farki("Süre 30 gündür.", "Süre 60 gündür."),
                         "Süre <del>30</del><ins>60</ins> gündür.")


if __name__ == "__main__":
    unittest.main()
