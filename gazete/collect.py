"""Toplayıcı.

  python -m gazete.collect gunluk           # bugünün sayısı (+ mükerrer); yoksa çıkış kodu 75
  python -m gazete.collect tarih 2026-10-31 # belirli gün
  python -m gazete.collect geriye           # geçmişi doldur (BACKFILL_PER_RUN gün)
  python -m gazete.collect bagla            # değişiklik bağlantılarını yeniden kur
  python -m gazete.collect ocr              # bekleyen taranmış PDF'leri OCR'la (süre sınırlı)
  python -m gazete.collect kontrol          # insan onayı bekleyen kalemleri listele
  python -m gazete.collect onayla <id>      # kontrol edildi, özet yayımlanabilir
"""
import hashlib
import json
import logging
import shutil
import sys
from datetime import date, datetime, timedelta

from . import amend, archive, config, db, ocr, parse
from .fetch import BudgetExceeded, Fetcher

log = logging.getLogger("gazete")

EX_TEMPFAIL = 75   # systemd/cron için "henüz yok, sonra tekrar dene"
MAX_DENEME = 3     # bir kalem bu kadar başarısız olursa bırakılır (hata kayıtta kalır)


def now():
    return datetime.now(config.TZ).isoformat(timespec="seconds")


def day_url(tarih):
    y, m, d = tarih.split("-")
    return f"{config.BASE_URL}/{d}.{m}.{y}"


def muk_url(tarih, n):
    return f"{config.BASE_URL}/fihrist?tarih={tarih}&mukerrer={n}"


def fetch_fihrist(f, con, tarih, mukerrer):
    """Bir sayının fihristini çeker. Dönüş: Fihrist, ya da yayımlanmadıysa None."""
    url = day_url(tarih) if mukerrer == 0 else muk_url(tarih, mukerrer)
    status, body, _ = f.get(url)
    if status != 200:
        log.info("%s: HTTP %s", url, status)
        return None
    text = body.decode("utf-8", "replace")
    fh = parse.parse_fihrist(text)
    if fh.yayim_yok and tarih == datetime.now(config.TZ).date().isoformat() and datetime.now(config.TZ).hour < 3:
        # Gece yarısından hemen sonra "yayımlanmamaktadır" yayımdan önceki ara durum olabilir
        log.info("%s: 'yayımlanmamaktadır' görünüyor ama erken; sonra tekrar bakılacak", tarih)
        return None
    if fh.yayim_yok:
        con.execute("INSERT OR REPLACE INTO sayilar VALUES (?,?,?,?,?,?)",
                    (tarih, mukerrer, None, "yok", url, now()))
        con.commit()
        log.info("%s: bu gün Resmî Gazete yayımlanmamış", tarih)
        return fh
    if fh.tarih != tarih or (mukerrer and fh.mukerrer != mukerrer):
        # Henüz yayımlanmadı: site başka bir günü gösteriyor
        log.info("%s (m%s): sayfa %s/m%s gösteriyor, henüz yok", tarih, mukerrer, fh.tarih, fh.mukerrer)
        return None
    archive.write_bytes(archive.fihrist_raw_path(tarih, mukerrer), body)
    return fh


def process_issue(f, con, tarih, mukerrer, fh):
    """Fihristteki kalemleri indirir, metni çıkarır, tabloya yazar."""
    sayi = fh.sayi
    eksik = 0
    for it in fh.items:
        iid = archive.item_id(it.url)
        row = con.execute("SELECT metin, deneme FROM duzenlemeler WHERE id=?", (iid,)).fetchone()
        if row and (row["metin"] is not None or row["deneme"] >= MAX_DENEME):
            continue
        deneme = row["deneme"] if row else 0
        if f.is_skipped(it.url):
            log.info("robots.txt Noindex, atlanıyor: %s", it.url)
            continue
        tur = parse.tur_bul(it.bolum, it.alt_baslik, it.baslik)
        rp = archive.raw_path(tarih, it.url)
        bicim = rp.suffix.lstrip(".").lower()
        metin = hata = sha = None
        taranmis, ocr_durum = 0, None
        try:
            status, body, _ = f.get(it.url)
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            archive.write_bytes(rp, body)
            sha = hashlib.sha256(body).hexdigest()
            if bicim == "pdf":
                sayfalar = parse.pdf_sayfalari(rp)
                metin = parse.normalize_text("\n".join(sayfalar))
                tsay = ocr.taranmis_sayfalar(rp, sayfalar)
                taranmis, ocr_durum = (1, "bekliyor") if tsay else (0, "yok")
                if tsay:
                    log.info("%s: taranmış sayfa %s/%d — OCR kuyruğunda", it.url, tsay, len(sayfalar))
            else:
                metin = parse.html_to_text(body)
            if tur == "ilan":
                # Yalnız dizin: ilan PDF'leri indirilmez, bağlantıları listelenir
                pdfler, gorsel = parse.ilan_dizini(body, it.url)
                if pdfler:
                    metin += "\nİlan dosyaları (indirilmedi):\n" + "\n".join(pdfler) + "\n"
                if gorsel >= 5 and len(metin) < 400:
                    taranmis = 1  # ör. TCMB değerleri: görüntü; ilanlar OCR kapsamı dışında
        except BudgetExceeded:
            raise
        except Exception as e:  # tek kalemin hatası sayıyı durdurmasın
            hata = str(e)[:300]
            deneme += 1
            if deneme < MAX_DENEME:
                eksik += 1
            log.warning("%s: %s", it.url, hata)
        kurum = None if tur == "ilan" else parse.kurum_bul(metin or "", tur, it.baslik)
        con.execute(
            "INSERT OR REPLACE INTO duzenlemeler (id, tarih, sayi, mukerrer, sira, bolum, alt_baslik, kurum, "
            "tur, baslik, metin, kaynak_url, ham_yol, bicim, sha256, cekildi, hata, deneme, ocr_gerekli, "
            "metin_kaynagi, ocr_durum) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, tarih, sayi, mukerrer, it.sira, it.bolum, it.alt_baslik, kurum, tur, it.baslik, metin,
             it.url, str(rp.relative_to(config.DATA)) if sha else None, bicim, sha, now(), hata, deneme,
             taranmis, bicim if metin is not None else None, ocr_durum))
        con.commit()
        if metin and not taranmis:
            link_item(con, iid)
    durum = "eksik" if eksik else "yayimlandi"
    url = day_url(tarih) if mukerrer == 0 else muk_url(tarih, mukerrer)
    con.execute("INSERT OR REPLACE INTO sayilar VALUES (?,?,?,?,?,?)", (tarih, mukerrer, sayi, durum, url, now()))
    con.commit()
    return durum


def link_item(con, iid):
    row = con.execute("SELECT metin FROM duzenlemeler WHERE id=?", (iid,)).fetchone()
    if not row or not row["metin"]:
        return 0
    n = 0
    for d in amend.ayristir(row["metin"]):
        con.execute(
            "INSERT OR IGNORE INTO degisiklikler (kaynak_id, kaynak_madde, hedef_tarih, hedef_sayi, hedef_baslik, "
            "hedef_madde, islem, cumle, yeni_metin, karmasik) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (iid, d.kaynak_madde, d.hedef_tarih, d.hedef_sayi, d.hedef_baslik, d.hedef_madde, d.islem,
             d.cumle, d.yeni_metin, int(d.karmasik)))
        n += 1
    con.commit()
    return n


def resolve_links(con):
    """Hedefi henüz bağlanmamış değişiklikleri arşive karşı yeniden dener."""
    rows = con.execute("SELECT d.*, k.tarih AS kaynak_tarih FROM degisiklikler d "
                       "JOIN duzenlemeler k ON k.id = d.kaynak_id WHERE d.hedef_id IS NULL").fetchall()
    bag = 0
    for r in rows:
        hid = amend.hedef_bul(con, r)
        if not hid:
            continue
        onceki = amend.onceki_madde(con, hid, r["hedef_madde"])
        ara = con.execute(
            "SELECT COUNT(*) FROM degisiklikler d JOIN duzenlemeler k ON k.id=d.kaynak_id "
            "WHERE d.hedef_tarih=? AND d.hedef_sayi=? AND d.hedef_madde IS ? AND k.tarih < ? AND d.id != ?",
            (r["hedef_tarih"], r["hedef_sayi"], r["hedef_madde"], r["kaynak_tarih"], r["id"])).fetchone()[0]
        con.execute("UPDATE degisiklikler SET hedef_id=?, onceki_madde=?, ara_degisiklik=? WHERE id=?",
                    (hid, onceki, ara, r["id"]))
        bag += 1
    con.commit()
    return bag


def export_day(con, tarih, mesaj_ek=""):
    rows = con.execute("SELECT * FROM duzenlemeler WHERE tarih=? ORDER BY mukerrer, sira", (tarih,)).fetchall()
    if not rows:
        return False
    with archive.kilit():
        archive.ensure_repo()
        for r in rows:
            if r["metin"] is not None:
                archive.write_item_text(r)
        archive.write_day_index(tarih, rows)
        return archive.commit_day(tarih, gun_mesaji(con, tarih) + mesaj_ek)


def gun_mesaji(con, tarih):
    sayilar = con.execute("SELECT mukerrer, sayi FROM sayilar WHERE tarih=? AND durum!='yok' ORDER BY mukerrer",
                          (tarih,)).fetchall()
    sayi = sayilar[0]["sayi"] if sayilar else "?"
    muk = [s["mukerrer"] for s in sayilar if s["mukerrer"]]
    return f"RG {tarih} sayı {sayi}" + (f" (+{', '.join(f'{m}. mükerrer' for m in muk)})" if muk else "")


def collect_date(f, con, tarih, mesaj_ek=""):
    """Bir günün asıl sayısını ve bildiği/yeni bulduğu mükerrerleri işler.
    Dönüş: 'yok' | 'bekle' | 'yayimlandi' | 'eksik'."""
    fh = fetch_fihrist(f, con, tarih, 0)
    if fh is None:
        return "bekle"
    if fh.yayim_yok:
        return "yok"
    durumlar = [process_issue(f, con, tarih, 0, fh)]
    for (t, n) in sorted(set(fh.mukerrer_links)):
        if t != tarih:
            continue
        bilinen = con.execute("SELECT durum FROM sayilar WHERE tarih=? AND mukerrer=?", (t, n)).fetchone()
        if bilinen and bilinen["durum"] == "yayimlandi":
            continue
        mf = fetch_fihrist(f, con, t, n)
        if mf is not None and not mf.yayim_yok:
            mf.sayi = mf.sayi or fh.sayi
            durumlar.append(process_issue(f, con, t, n, mf))
    resolve_links(con)
    export_day(con, tarih, mesaj_ek)
    return "eksik" if "eksik" in durumlar else "yayimlandi"


def cmd_gunluk(f, con):
    today = datetime.now(config.TZ).date()
    dun = (today - timedelta(days=1)).isoformat()
    # Dünün son kontrolü gün bitmeden yapıldıysa geç gelen mükerrer için bir kez daha bak
    r = con.execute("SELECT MAX(son_kontrol) AS s FROM sayilar WHERE tarih=?", (dun,)).fetchone()
    if r["s"] and r["s"] < f"{dun}T23:30":
        collect_date(f, con, dun)
    durum = collect_date(f, con, today.isoformat())
    log.info("bugün (%s): %s; istek sayısı %d", today, durum, f.count)
    return EX_TEMPFAIL if durum in ("bekle", "eksik") else 0


def cmd_geriye(f, con):
    """Dünden geriye BACKFILL_DAYS içinde işlenmemiş ilk günleri doldurur."""
    today = datetime.now(config.TZ).date()
    yapilan = 0
    for i in range(1, config.BACKFILL_DAYS + 1):
        if yapilan >= config.BACKFILL_PER_RUN:
            break
        t = (today - timedelta(days=i)).isoformat()
        r = con.execute("SELECT durum FROM sayilar WHERE tarih=? AND mukerrer=0", (t,)).fetchone()
        if r and r["durum"] in ("yayimlandi", "yok"):
            continue
        durum = collect_date(f, con, t, " (geriye doldurma)")
        log.info("geriye %s: %s", t, durum)
        yapilan += 1
    if yapilan == 0:
        log.info("geriye doldurma tamam (%d gün)", config.BACKFILL_DAYS)
    return 0


def ocr_tespit(con):
    """Sayfa bazlı tespitten önce toplanmış PDF'leri bir kez tarar (ocr_durum NULL olanlar)."""
    rows = con.execute("SELECT id, ham_yol FROM duzenlemeler WHERE bicim='pdf' AND ocr_durum IS NULL "
                       "AND ham_yol IS NOT NULL").fetchall()
    for r in rows:
        p = config.DATA / r["ham_yol"]
        tsay = ocr.taranmis_sayfalar(p) if p.exists() else []
        con.execute("UPDATE duzenlemeler SET ocr_gerekli=?, ocr_durum=? WHERE id=?",
                    (1 if tsay else 0, "bekliyor" if tsay else "yok", r["id"]))
        if tsay:
            log.info("%s: taranmış sayfa %s — OCR kuyruğuna alındı", r["id"], tsay)
    con.commit()


def cmd_ocr(con):
    ocr_tespit(con)
    bas = datetime.now().timestamp()
    gunler = set()
    rows = con.execute("SELECT id, tarih, tur, baslik, ham_yol FROM duzenlemeler WHERE ocr_durum='bekliyor' "
                       "ORDER BY tarih DESC, mukerrer, sira").fetchall()
    for k, r in enumerate(rows):
        if datetime.now().timestamp() - bas > config.OCR_SURE_S:
            log.info("OCR süre sınırı; kalan %d belge sonraki çalıştırmada", len(rows) - k)
            break
        try:
            metin, bilgi, kaynak = ocr.ocr_pdf(config.DATA / r["ham_yol"], r["tur"])
        except Exception as e:
            log.warning("%s: OCR başarısız: %s", r["id"], e)
            con.execute("UPDATE duzenlemeler SET ocr_durum='hata', hata=? WHERE id=?", (f"OCR: {e}"[:300], r["id"]))
            con.commit()
            continue
        neden = ocr.kontrol_nedenleri(r["tur"], metin, bilgi, r["baslik"])
        con.execute("UPDATE duzenlemeler SET metin=?, metin_kaynagi=?, ocr_durum='tamam', ocr_bilgi=?, "
                    "kurum=?, kontrol_gerekli=?, kontrol_neden=? WHERE id=?",
                    (metin, kaynak, json.dumps(bilgi, ensure_ascii=False), parse.kurum_bul(metin, r["tur"], r["baslik"]),
                     1 if neden else 0, "\n".join(neden) or None, r["id"]))
        # OCR metni otomatik değişiklik eşleştirmesine girmez (rakam hatası diff'i bozar)
        con.execute("DELETE FROM degisiklikler WHERE kaynak_id=?", (r["id"],))
        con.commit()
        gunler.add(r["tarih"])
        log.info("%s: OCR tamam, %d sayfa, güven %s, %d sn%s", r["id"], bilgi["sayfa"], bilgi["guven"],
                 bilgi["sure_s"], f", kontrol: {len(neden)} neden" if neden else "")
    for t in sorted(gunler):
        export_day(con, t, " (OCR)")
    return 0


def cmd_kontrol(con):
    for r in con.execute("SELECT id, baslik, kontrol_neden FROM duzenlemeler WHERE kontrol_gerekli=1 "
                         "ORDER BY tarih, sira"):
        print(f"{r['id']}  {r['baslik'][:90]}")
        for n in (r["kontrol_neden"] or "").splitlines():
            print(f"    - {n}")
    return 0


def cmd_onayla(con, iid):
    n = con.execute("UPDATE duzenlemeler SET kontrol_gerekli=0, kontrol_neden=kontrol_neden || ? "
                    "WHERE id=? AND kontrol_gerekli=1", (f"\n(onaylandı {now()})", iid)).rowcount
    con.commit()
    print("onaylandı" if n else "bulunamadı ya da kontrol beklemiyor")
    return 0 if n else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(config.LOG_DIR / "collect.log", encoding="utf-8")])
    if not argv:
        print(__doc__)
        return 2
    con = db.connect(config.DB_PATH)
    if argv[0] == "bagla":
        ids = [r["id"] for r in con.execute("SELECT id FROM duzenlemeler WHERE metin IS NOT NULL")]
        n = sum(link_item(con, i) for i in ids)
        log.info("ayrıştırılan değişiklik: %d, bağlanan: %d", n, resolve_links(con))
        return 0
    if argv[0] == "kontrol":
        return cmd_kontrol(con)
    if argv[0] == "onayla" and len(argv) > 1:
        return cmd_onayla(con, argv[1])
    if argv[0] == "ocr":
        eksik = [t for t in ("git", "pdftotext") if not shutil.which(t)] + ocr.eksik_araclar()
        if eksik:
            log.error("eksik araç: %s", ", ".join(eksik))
            return 1
        return cmd_ocr(con)
    eksik = [t for t in ("git", "pdftotext", "pdfimages") if not shutil.which(t)]
    if eksik:
        # Yoksa PDF kalemleri boşuna deneme hakkını tüketir
        log.error("eksik araç: %s (git-core / poppler-utils kurulmalı)", ", ".join(eksik))
        return 1
    f = Fetcher()
    try:
        f.load_robots()
        if argv[0] == "gunluk":
            return cmd_gunluk(f, con)
        if argv[0] == "tarih":
            date.fromisoformat(argv[1])
            durum = collect_date(f, con, argv[1])
            log.info("%s: %s; istek sayısı %d", argv[1], durum, f.count)
            return 0 if durum in ("yayimlandi", "yok") else EX_TEMPFAIL
        if argv[0] == "geriye":
            return cmd_geriye(f, con)
    except BudgetExceeded as e:
        log.warning("durduruldu: %s — kalan iş sonraki çalıştırmada", e)
        return EX_TEMPFAIL
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
