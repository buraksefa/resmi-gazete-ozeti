"""Faz 2 testleri: önce/sonra, yürürlük, girdi, denetim, etiket. LLM sahte yanıtla değiştirilir."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ["GAZETE_YAYIN"] = ""   # testler hiçbir zaman gerçek siteye yükleme yapmasın
os.environ.setdefault("GAZETE_CONTACT", "test@example.invalid")

from gazete import db, fark, ozet  # noqa: E402

ONCEKI = "MADDE 5- (1) Başvurular yazılı yapılır.\n(2) Süre 30 gündür.\n(3) Ücret alınmaz."


def deg(**k):
    d = {"hedef_id": "eskiler/x", "karmasik": 0, "hedef_madde": "5", "onceki_madde": ONCEKI,
         "ara_degisiklik": 0, "islem": "değiştirme", "cumle": "", "yeni_metin": None}
    d.update(k)
    return d


class FarkTest(unittest.TestCase):
    def test_fikra_degistirme(self):
        durum, sonra, _ = fark.uygula(deg(
            cumle="… Yönetmeliğinin 5 inci maddesinin ikinci fıkrası aşağıdaki şekilde değiştirilmiştir.",
            yeni_metin="(2) Süre 60 gündür."))
        self.assertEqual(durum, fark.UYGULANDI)
        self.assertEqual(sonra, "MADDE 5- (1) Başvurular yazılı yapılır.\n(2) Süre 60 gündür.\n(3) Ücret alınmaz.")
        f = fark.fark_metni(ONCEKI, sonra)
        self.assertIn("-(2) Süre 30 gündür.", f)
        self.assertIn("+(2) Süre 60 gündür.", f)

    def test_madde_sonuna_fikra_ekleme(self):
        durum, sonra, _ = fark.uygula(deg(islem="ekleme", cumle="… 5 inci maddesine aşağıdaki fıkra eklenmiştir.",
                                          yeni_metin="(4) Başvuru elektronik ortamda da yapılabilir."))
        self.assertEqual(durum, fark.UYGULANDI)
        self.assertTrue(sonra.endswith("(3) Ücret alınmaz.\n(4) Başvuru elektronik ortamda da yapılabilir."))

    def test_madde_degistirme_ve_kaldirma(self):
        d, s, _ = fark.uygula(deg(cumle="… 5 inci maddesi aşağıdaki şekilde değiştirilmiştir.",
                                  yeni_metin="MADDE 5- (1) Başvurular e-Devlet üzerinden yapılır."))
        self.assertEqual((d, s), (fark.UYGULANDI, "MADDE 5- (1) Başvurular e-Devlet üzerinden yapılır."))
        d, s, _ = fark.uygula(deg(islem="yürürlükten kaldırma",
                                  cumle="… 5 inci maddesinin üçüncü fıkrası yürürlükten kaldırılmıştır."))
        self.assertEqual(s, "MADDE 5- (1) Başvurular yazılı yapılır.\n(2) Süre 30 gündür.")

    def test_belirsizse_birlestirmez(self):
        durumlar = [
            fark.uygula(deg(hedef_id=None))[0],
            fark.uygula(deg(karmasik=1))[0],
            fark.uygula(deg(ara_degisiklik=1))[0],
            fark.uygula(deg(cumle="… 5 inci maddesinin birinci fıkrasının (a) bendi değiştirilmiştir.",
                            yeni_metin="a) x"))[0],
            fark.uygula(deg(islem="ekleme", cumle="… birinci fıkrasından sonra gelmek üzere aşağıdaki fıkra "
                            "eklenmiştir.", yeni_metin="(2) x"))[0],
            fark.uygula(deg(cumle="… ikinci fıkrası aşağıdaki şekilde değiştirilmiştir.",
                            yeni_metin="(3) numara uyuşmuyor"))[0],
        ]
        self.assertEqual(durumlar, [fark.ARSIVDE_YOK] + [fark.BIRLESTIRILEMEDI] * 5)


class YururlukTest(unittest.TestCase):
    def test_yayim_tarihinde(self):
        m = "MADDE 3- Bu Tebliğ yayımı tarihinde yürürlüğe girer.\nMADDE 4- Bu Tebliğ hükümlerini Başkan yürütür."
        c, t = ozet.yururluk_bul(m, "2026-10-31")
        self.assertEqual((c, t), ("Bu Tebliğ yayımı tarihinde yürürlüğe girer.", "2026-10-31"))
        self.assertEqual(ozet.yururluk_cumlesi(c, t, "2026-10-31"),
                         "31 Ekim 2026 tarihinde yürürlüğe girer (yayımı tarihinde).")

    def test_ileri_tarih_ve_kosullu(self):
        self.assertEqual(ozet.yururluk_bul("MADDE 2- Bu Yönetmelik 1/1/2027 tarihinde yürürlüğe girer.",
                                           "2026-10-31")[1], "2027-01-01")
        c, t = ozet.yururluk_bul("MADDE 9- (1) Bu Yönetmeliğin 3 üncü maddesi 1/1/2027 tarihinde, diğer hükümleri "
                                 "yayımı tarihinde yürürlüğe girer.", "2026-10-31")
        self.assertIsNone(t)
        self.assertTrue(ozet.yururluk_cumlesi(c, t, "2026-10-31").startswith("Yürürlük: “Bu Yönetmeliğin 3"))

    def test_ek_kosul_aynen_alintilanir(self):
        m = ("MADDE 12- Bu Yönetmelik 2026-2027 eğitim-öğretim yılı başından geçerli olmak üzere yayımı "
             "tarihinde yürürlüğe girer.")
        c, t = ozet.yururluk_bul(m, "2026-11-01")
        self.assertIsNone(t)
        self.assertIn("geçerli olmak üzere", ozet.yururluk_cumlesi(c, t, "2026-11-01"))

    def test_bentli_hukum_butun_alinir(self):
        m = ("MADDE 4- Aynı Tebliğe ek madde eklenmiştir.\nMADDE 5- (1) Bu Tebliğin;\na) 4 üncü maddesi yayımı "
             "tarihinde,\nb) Diğer hükümleri 1/1/2027 tarihinde\nyürürlüğe girer.\nMADDE 6- Bu Tebliğ hükümlerini "
             "Bakan yürütür.")
        c, t = ozet.yururluk_bul(m, "2026-10-31")
        self.assertEqual((c, t), ("Bu Tebliğin; a) 4 üncü maddesi yayımı tarihinde, b) Diğer hükümleri 1/1/2027 "
                                  "tarihinde yürürlüğe girer.", None))
        self.assertEqual(ozet.yururluk_bul("MADDE 3- Bu Cumhurbaşkanlığı Kararnamesi yayımı tarihinde yürürlüğe "
                                           "girer.", "2026-10-31")[1], "2026-10-31")

    def test_yok_ve_tirnak_ici(self):
        self.assertEqual(ozet.yururluk_bul("Karar verilmiştir.", "2026-10-31"), (None, None))
        m = ('MADDE 1- … aşağıdaki fıkra eklenmiştir.\n“(4) Bu fıkra 1/1/2030 tarihinde yürürlüğe girer.”\n'
             'MADDE 2- Bu Yönetmelik 1/1/2027 tarihinde yürürlüğe girer.')
        self.assertEqual(ozet.yururluk_bul(m, "2026-10-31")[1], "2027-01-01")


class GirdiTest(unittest.TestCase):
    def test_uzun_yonetmelik(self):
        md = "\n".join(f"MADDE {i}- (1) " + ("uzun hüküm metni " * 60) for i in range(1, 40))
        g, kisa = ozet.girdi_hazirla(md, 6000)
        self.assertTrue(kisa)
        self.assertLessEqual(len(g), 6000)
        self.assertTrue(g.startswith("MADDE 1-"))
        self.assertIn("MADDE 39-", g)          # yürürlük/yürütme maddeleri korunur

    def test_maddesiz_metin_bas_ve_son(self):
        g, kisa = ozet.girdi_hazirla("A" * 5000 + "HÜKÜM: ihlal edildiğine", 3000)
        self.assertTrue(kisa and g.endswith("HÜKÜM: ihlal edildiğine"))


class DenetimTest(unittest.TestCase):
    KAYNAK = ("Türk Patent ve Marka Kurumunca 2026 Yılında Uygulanacak Ücret Tarifesine İlişkin Tebliğ … "
              "TÜRKPATENT tarafından düzenlenen yarışmalar kapsamında yapılan başvurular 1.01.01 ücretten muaftır.")

    def test_temiz(self):
        o = "Türk Patent ve Marka Kurumu yarışmalarına yapılan başvurular 1.01.01 ücretinden muaf tutuluyor."
        self.assertEqual(ozet.ozet_denetle(o, self.KAYNAK), [])

    def test_uydurma_ad_sayi_yorum(self):
        o = "Bu önemli değişiklikle Türkpapatent başvuruları 2027 yılında ücretsiz olacak."
        n = ozet.ozet_denetle(o, self.KAYNAK)
        self.assertIn("kaynakta geçmeyen sayı/tarih: 2027", n)
        self.assertIn("kaynakta geçmeyen ad: Türkpapatent", n)
        self.assertIn("yorum/değerlendirme ifadesi: önemli", n)


@unittest.skipUnless(ozet.tomllib, "tomllib yok (Python < 3.11)")
class EtiketTest(unittest.TestCase):
    def test_config_ve_kelime(self):
        e = ozet.etiketleri_yukle(HERE.parent / "etiketler.toml")
        self.assertIn("kvkk", e)
        self.assertEqual(ozet.kelime_etiketleri(e, "Organize Toptan Doğal Gaz Satış Piyasası Yönetmeliği",
                                                "Enerji Piyasası Düzenleme Kurumu"), ["enerji"])
        self.assertIn("vergi", ozet.kelime_etiketleri(e, "KATMA DEĞER VERGİSİ GENEL UYGULAMA TEBLİĞİ", ""))


class OzetleTest(unittest.TestCase):
    ETIKET = {"enerji": {"ad": "Enerji", "aciklama": "Elektrik, doğal gaz", "kelimeler": ["doğal gaz"]},
              "vergi": {"ad": "Vergi", "aciklama": "Vergi", "kelimeler": ["vergi"]}}

    def row(self, **k):
        r = {"id": "eskiler/20261031-2", "tarih": "2026-10-31", "tur": "yönetmelik", "kurum": "Enerji Piyasası "
             "Düzenleme Kurumu", "baslik": "Organize Toptan Doğal Gaz Satış Piyasası Yönetmeliğinde Değişiklik "
             "Yapılmasına Dair Yönetmelik", "kontrol_gerekli": 0,
             "metin": "MADDE 1- … 13 üncü maddesine aşağıdaki fıkra eklenmiştir.\n“(4) Enerji Piyasalarında "
                      "Şeffaflığa İlişkin Yönetmelik hükümleri saklıdır.”\nMADDE 2- Bu Yönetmelik 1/1/2027 "
                      "tarihinde yürürlüğe girer."}
        r.update(k)
        return r

    def test_ozet_yururluk_kuraldan(self):
        cevap = {"ozet": "Organize toptan doğal gaz piyasasında Enerji Piyasalarında Şeffaflığa İlişkin "
                         "Yönetmelik hükümlerinin saklı olduğu belirtiliyor. Bu Yönetmelik 1/1/2027 tarihinde yürürlüğe girer.",
                 "etiketler": ["enerji", "uydurma", "vergi"]}
        with mock.patch.object(ozet, "llm_json", return_value=cevap):
            k, etk = ozet.duzenleme_ozetle(self.row(), self.ETIKET)
        self.assertEqual(k["durum"], "hazir", k["kontrol_neden"])
        self.assertTrue(k["ozet"].endswith("1 Ocak 2027 tarihinde yürürlüğe girer."))
        self.assertEqual(etk, [("enerji", "kelime")])   # 'uydurma' listede yok; 'vergi' metinde geçmiyor
        self.assertEqual(k["ozet"].count("yürürlüğe girer"), 1)   # modelin yürürlük cümlesi atılır

    def test_atama_olgulari(self):
        metin = ("Aile ve Sosyal Hizmetler Bakanlığı Genel Müdür Yardımcısı Halit\nÇAĞDAŞ, 3 sayılı Cumhurbaşkanlığı "
                 "Kararnamesinin 2 nci maddesi gereğince görevden\nalınmıştır.\n\n31 Ekim 2026\n\nRecep Tayyip ERDOĞAN\n"
                 "— Bolu İl Müdürlüğüne, Bartın İl Müdürü Yavuz KORKMAZ,\n3 sayılı … atanmıştır.")
        cevap = {"kararlar": [{"islem": "atama", "ad": "Halit ÇAĞDAŞ",
                               "gorev": "Aile ve Sosyal Hizmetler Bakanlığı Genel Müdür Yardımcısı"},
                              {"islem": "atama", "ad": "Uydurma KİŞİ", "gorev": "Genel Müdür"}]}
        with mock.patch.object(ozet, "llm_json", return_value=cevap):
            k, _ = ozet.duzenleme_ozetle(self.row(tur="atama", metin=metin, baslik="Atama Kararları"), self.ETIKET)
        self.assertEqual(k["durum"], "kontrol")
        n = k["kontrol_neden"].splitlines()
        self.assertIn("ad metinde birebir yok: Uydurma KİŞİ", n)
        self.assertIn("metinde geçen ama listede olmayan ad: Müdürü Yavuz KORKMAZ", n)
        self.assertNotIn("ad metinde birebir yok: Halit ÇAĞDAŞ", n)      # satır sonu bölünmesi sorun değil
        olg = json.loads(k["olgular"])
        self.assertEqual(olg[0]["islem"], "görevden alma")                 # model "atama" dedi; metin düzeltir
        self.assertEqual(k["ozet"], "2 kişiye ilişkin karar: 1 atama, 1 görevden alma.")

    def test_kaydet_ve_bekleyen(self):
        from gazete import ozetle
        with tempfile.TemporaryDirectory() as td:
            con = db.connect(Path(td) / "t.sqlite")
            con.execute("INSERT INTO duzenlemeler (id,tarih,sayi,mukerrer,sira,tur,baslik,metin,kaynak_url) "
                        "VALUES ('eskiler/a','2026-10-31',1,0,1,'tebliğ','b','m','u')")
            self.assertEqual(len(con.execute(ozetle.BEKLEYEN).fetchall()), 1)
            ozetle.kaydet(con, {"duzenleme_id": "eskiler/a", "model": "m", "ozet": "o", "durum": "hazir"},
                          [("vergi", "llm")])
            self.assertEqual(len(con.execute(ozetle.BEKLEYEN).fetchall()), 0)
            self.assertEqual(con.execute("SELECT etiket FROM etiketler").fetchone()[0], "vergi")


if __name__ == "__main__":
    unittest.main()
