"""Gece özeti taslakları — DRY-RUN: yalnız dosya üretir, hiçbir yere göndermez.

data/taslak/<tarih>/x.txt        X için başlık (≤280) + dizi; her gönderi ≤280 karakter (bağlantı 23 sayılır)
data/taslak/<tarih>/eposta.html  e-posta bülteni (HTML) ve eposta.txt (düz metin)

Yalnız elle onaylanmış özetler girer (otomatik yayımlanan, incelenmemiş özetler girmez). Atamalarda kişi adı ve yorum yok: yalnız kurum/görev sayısı ve kaynak.
Bu modülde X API'si ya da SMTP kodu bulunmaz; paylaşım ayrı ve senin onayınla eklenecek bir adımdır.
"""
import html
import json
import re

from . import config, ozet

NOT = "Bu bir özettir, bağlayıcı olan Resmî Gazete'deki metindir."
SINIR = 280
URL_UZUNLUK = 23   # X her bağlantıyı 23 karakter sayar
e = html.escape


def x_uzunluk(metin):
    return len(re.sub(r"https?://\S+", "x" * URL_UZUNLUK, metin))


def kisalt(metin, sinir):
    if len(metin) <= sinir:
        return metin
    kes = metin[: sinir - 1].rsplit(" ", 1)[0]
    return kes.rstrip(" ,;:") + "…"


def ilk_cumle(metin):
    return re.split(r"(?<=[.!?])\s+", metin.strip(), 1)[0]


def gonderi(no, toplam, d, o):
    if o["olgular"]:
        govde = f"{len(json.loads(o['olgular']))} kişiye ilişkin atama/görev kararı (liste kaynakta)."
    else:
        govde = ilk_cumle(o["ozet"])
    bas = f"{no}/{toplam} "
    url = d["kaynak_url"]
    # baslık ve gövdeye kalan yer: 280 − numara − bağlantı − ayırıcılar
    yer = SINIR - len(bas) - URL_UZUNLUK - 4
    baslik = kisalt(d["baslik"], min(110, yer // 2))
    govde = kisalt(govde, yer - len(baslik))
    metin = f"{bas}{baslik}: {govde} {url}"
    assert x_uzunluk(metin) <= SINIR, metin
    return metin


def x_dizisi(tarih, sayi, kalemler):
    bas = (f"Resmî Gazete, {ozet.tarih_tr(tarih)}" + (f" (sayı {sayi})" if sayi else "")
           + f": {len(kalemler)} düzenlemenin kısa özeti aşağıda. {NOT}")
    out = [kisalt(bas, SINIR)]
    out += [gonderi(i, len(kalemler), d, o) for i, (d, o) in enumerate(kalemler, 1)]
    return out


def eposta(tarih, sayi, kalemler):
    baslik = f"Resmî Gazete özeti — {ozet.tarih_tr(tarih)}" + (f", sayı {sayi}" if sayi else "")
    duz = [baslik, "=" * len(baslik), "", NOT, ""]
    h = [f"<!doctype html><html lang='tr'><meta charset='utf-8'><title>{e(baslik)}</title>",
         "<body style='font-family:system-ui,sans-serif;max-width:40rem;margin:auto;line-height:1.55'>",
         f"<h1 style='font-size:1.3rem'>{e(baslik)}</h1><p style='background:#f1efe8;padding:.5rem'>{e(NOT)}</p>"]
    for d, o in kalemler:
        url = config.SITE_URL + _sayfa(d["id"])
        duz += [d["baslik"], f"  {o['ozet']}", f"  Ayrıntı: {url}", f"  Kaynak: {d['kaynak_url']}", ""]
        h.append(f"<h2 style='font-size:1rem'>{e(d['baslik'])}</h2><p>{e(o['ozet'])}</p>")
        if o["olgular"]:
            h.append("<ul>" + "".join(f"<li>{e(x['ad'])} — {e(x['gorev'])} ({e(x['islem'])})</li>"
                                      for x in json.loads(o["olgular"])) + "</ul>")
        h.append(f"<p style='font-size:.9rem'><a href='{e(url)}'>Ayrıntı</a> · "
                 f"<a href='{e(d['kaynak_url'])}'>Resmî Gazete'deki metin</a></p>")
    duz += ["--", NOT]
    h.append(f"<hr><p style='font-size:.85rem;color:#666'>{e(NOT)}</p></body></html>")
    return "\n".join(h), "\n".join(duz)


def _sayfa(did):
    kind, stem = did.split("/")
    return f"/d/{'ilan-' if kind == 'ilanlar' else ''}{stem}"


def uret(v, gun_sayisi=7):
    """site.Veri ile son günlerin taslaklarını yazar (onaylı kalemi olan günler)."""
    for tarih in v.gunler[:gun_sayisi]:
        kalemler = [(d, v.ozet[d["id"]]) for d in v.duz.values()
                    if d["tarih"] == tarih and d["id"] in v.ozet and v.ozet[d["id"]]["onay"] == "onaylandi"]
        kalemler.sort(key=lambda x: (x[0]["mukerrer"], x[0]["sira"]))
        klasor = config.TASLAK_DIR / tarih
        if not kalemler:
            continue
        sayi = next((x["sayi"] for x in v.sayilar.get(tarih, []) if x["mukerrer"] == 0), None)
        klasor.mkdir(parents=True, exist_ok=True)
        dizi = x_dizisi(tarih, sayi, kalemler)
        (klasor / "x.txt").write_text(
            "DRY-RUN — gönderilmedi. Her gönderi ≤280 karakter (bağlantılar 23 sayılır).\n\n"
            + "\n\n---\n\n".join(f"[{x_uzunluk(g)}] {g}" for g in dizi) + "\n", encoding="utf-8")
        h, duz = eposta(tarih, sayi, kalemler)
        (klasor / "eposta.html").write_text(h, encoding="utf-8")
        (klasor / "eposta.txt").write_text("DRY-RUN — gönderilmedi.\n\n" + duz + "\n", encoding="utf-8")
