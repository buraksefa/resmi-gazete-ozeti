"""Özet inceleme ve onayı. Uyarısız ("hazir") özetler onay beklemeden "otomatik özet · henüz incelenmedi"
etiketiyle sitede ve RSS'te yayımlanır (config.OTOMATIK_YAYIN); onaylayınca etiket kalkar, reddedince siteden
iner. Uyarılı ("kontrol") özetler ve X/e-posta taslakları yalnız onaydan sonra yayımlanır.

  python3.12 -m gazete.onay liste [YYYY-MM-DD]      # onay bekleyenler (uyarılarıyla)
  python3.12 -m gazete.onay goster 20261031-2       # özet + kaynak metnin başı
  python3.12 -m gazete.onay onayla 20261031-2 20261031-5 …
  python3.12 -m gazete.onay onayla --gun 2026-10-31 # o günün uyarısız ("hazir") özetlerinin hepsi
  python3.12 -m gazete.onay reddet 20261031-14 "liste adları eksik"
  python3.12 -m gazete.onay duzelt 20261031-15 "Yeni özet metni."   # elle düzeltip onayla
  python3.12 -m gazete.onay gozden                  # bekleyenleri tek tek sor: o/r/d/a/q
  python3.12 -m gazete.onay geri 20261031-2         # onayı geri al (siteden kalkar)

Kısa kimlik (20261031-2) "eskiler/20261031-2" demektir; ilan özetlenmez.
"Kontrol" durumundaki özetler toplu onaylanmaz; tek tek bakıp onaylarsın. Kaynak metin OCR
kontrolü bekliyorsa (ör. "Kurşehir") önce `python3.12 -m gazete.collect onayla <id>` gerekir.
Her değişiklikten sonra site yeniden üretilir.
"""
import json
import re
import sys
import textwrap
from datetime import datetime

from . import config, db

BEKLEYEN = ("SELECT o.*, d.tarih, d.tur, d.baslik, d.kaynak_url, d.metin_kaynagi, d.kontrol_gerekli AS ocr_kontrol "
            "FROM ozetler o JOIN duzenlemeler d ON d.id = o.duzenleme_id "
            "WHERE o.onay IS NULL AND o.durum IN ('hazir', 'kontrol') {kosul} ORDER BY d.tarih DESC, d.mukerrer, d.sira")


def now():
    return datetime.now(config.TZ).isoformat(timespec="seconds")


def tam_id(k):
    return k if "/" in k else f"eskiler/{k}"


def kisa_id(i):
    return i.split("/", 1)[1]


def satir(r, ayrinti=False):
    isaret = "⚠ KONTROL" if r["durum"] == "kontrol" else "hazır"
    out = [f"[{kisa_id(r['duzenleme_id'])}] {r['tarih']} · {r['tur']} · {isaret}"
           + (" · OCR" if (r["metin_kaynagi"] or "").endswith("ocr") else ""),
           textwrap.fill(r["baslik"], 100, initial_indent="  ", subsequent_indent="  ")]
    if r["olgular"]:
        olg = json.loads(r["olgular"])
        out += [f"  {r['ozet']}"] + [f"   - {o['islem']}: {o['ad']} — {o['gorev']}" for o in olg[: None if ayrinti else 6]]
        if not ayrinti and len(olg) > 6:
            out.append(f"   … {len(olg) - 6} kişi daha (goster ile tamamı)")
    else:
        out.append(textwrap.fill(r["ozet"] or "", 100, initial_indent="  » ", subsequent_indent="    "))
    for n in (r["kontrol_neden"] or "").splitlines():
        out.append(f"  ! {n}")
    out.append(f"  kaynak: {r['kaynak_url']}")
    return "\n".join(out)


def site_yenile():
    from . import site, yayinla
    site.uret()
    yayinla.yayinla()   # .env'de Cloudflare anahtarı yoksa sessizce atlanır


def onay_ver(con, ids, deger, not_=None):
    n = 0
    for i in ids:
        r = con.execute("SELECT o.durum, d.kontrol_gerekli FROM ozetler o JOIN duzenlemeler d ON d.id=o.duzenleme_id "
                        "WHERE o.duzenleme_id=?", (i,)).fetchone()
        if not r:
            print(f"{kisa_id(i)}: özet yok")
            continue
        if deger == "onaylandi" and r["kontrol_gerekli"]:
            print(f"{kisa_id(i)}: kaynak metin OCR kontrolü bekliyor — önce: python3.12 -m gazete.collect onayla {i}")
            continue
        con.execute("UPDATE ozetler SET onay=?, onay_zamani=?, onay_notu=? WHERE duzenleme_id=?",
                    (deger, now() if deger else None, not_, i))
        n += 1
        print(f"{kisa_id(i)}: {deger or 'onay geri alındı'}")
    con.commit()
    return n


def gozden(con):
    rows = con.execute(BEKLEYEN.format(kosul="")).fetchall()
    print(f"{len(rows)} özet onay bekliyor. o=onayla  r=reddet  d=düzeltip onayla  a=atla  q=çık\n")
    degisti = 0
    for r in rows:
        print(satir(r, ayrinti=True))
        while True:
            c = input("  [o/r/d/a/q] > ").strip().lower()
            if c in ("o", "r", "d", "a", "q"):
                break
        if c == "q":
            break
        if c == "o":
            degisti += onay_ver(con, [r["duzenleme_id"]], "onaylandi")
        elif c == "r":
            degisti += onay_ver(con, [r["duzenleme_id"]], "reddedildi", input("  neden (isteğe bağlı) > ").strip() or None)
        elif c == "d":
            yeni = input("  yeni özet > ").strip()
            if yeni:
                duzelt(con, r["duzenleme_id"], yeni)
                degisti += 1
        print()
    return degisti


def duzelt(con, i, metin):
    con.execute("UPDATE ozetler SET ozet=?, elle_duzeltildi=1 WHERE duzenleme_id=?", (metin, i))
    con.commit()
    return onay_ver(con, [i], "onaylandi", "elle düzeltildi")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    con = db.connect(config.DB_PATH)
    k, args = argv[0], argv[1:]
    if k == "liste":
        kosul, par = ("AND d.tarih=?", args[:1]) if args else ("", [])
        rows = con.execute(BEKLEYEN.format(kosul=kosul), par).fetchall()
        for r in rows:
            print(satir(r) + "\n")
        hazir = sum(r["durum"] == "hazir" for r in rows)
        print(f"{len(rows)} özet onay bekliyor ({hazir} uyarısız, {len(rows) - hazir} kontrol).")
        return 0
    if k == "goster" and args:
        r = con.execute(BEKLEYEN.replace("o.onay IS NULL AND ", "").format(kosul="AND o.duzenleme_id=?"),
                        (tam_id(args[0]),)).fetchone()
        if not r:
            print("bulunamadı")
            return 1
        metin = con.execute("SELECT metin FROM duzenlemeler WHERE id=?", (r["duzenleme_id"],)).fetchone()[0] or ""
        print(satir(r, ayrinti=True) + f"\n  onay: {r['onay'] or 'bekliyor'}\n\n--- kaynak metnin başı ---\n"
              + re.sub(r"\n{2,}", "\n", metin)[:3000])
        return 0
    if k in ("onayla", "reddet", "geri", "duzelt"):
        if k == "onayla" and args[:1] == ["--gun"] and len(args) > 1:
            ids = [r["duzenleme_id"] for r in con.execute(BEKLEYEN.format(kosul="AND d.tarih=? AND o.durum='hazir'"),
                                                          (args[1],))]
            n = onay_ver(con, ids, "onaylandi")
        elif k == "onayla" and args:
            n = onay_ver(con, [tam_id(a) for a in args], "onaylandi")
        elif k == "reddet" and args:
            n = onay_ver(con, [tam_id(args[0])], "reddedildi", " ".join(args[1:]) or None)
        elif k == "geri" and args:
            n = onay_ver(con, [tam_id(a) for a in args], None)
        elif k == "duzelt" and len(args) >= 2:
            n = duzelt(con, tam_id(args[0]), " ".join(args[1:]))
        else:
            print(__doc__)
            return 2
        if n:
            site_yenile()
        return 0
    if k == "gozden":
        if gozden(con):
            site_yenile()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
