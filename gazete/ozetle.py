"""Faz 2: özet, etiket, önce/sonra.

  python -m gazete.ozetle calistir          # özeti olmayan düzenlemeler (yeniden eskiye, süre sınırlı) + fark
  python -m gazete.ozetle yeniden <id>      # bir düzenlemenin özetini yeniden üret
  python -m gazete.ozetle etiketle --hepsi  # etiketler.toml değişince kelime etiketlerini yeniden uygula
  python -m gazete.ozetle fark              # önce/sonra birleştirmesini yeniden hesapla
  python -m gazete.ozetle yururluk          # yürürlük kuralı değişince özetlerin son cümlesini yenile
"""
import logging
import sys
import time
from datetime import datetime

from . import config, db, fark, ozet

log = logging.getLogger("gazete")

# Özeti yapılmayanlar: ilanlar (yalnız dizin), OCR'ı bitmemiş taranmış belgeler
BEKLEYEN = ("SELECT d.* FROM duzenlemeler d LEFT JOIN ozetler o ON o.duzenleme_id = d.id "
            "WHERE d.metin IS NOT NULL AND d.tur != 'ilan' "
            "AND COALESCE(d.ocr_durum, '') NOT IN ('bekliyor', 'hata') "
            "AND (o.duzenleme_id IS NULL OR o.durum = 'hata') ORDER BY d.tarih DESC, d.mukerrer, d.sira")


def now():
    return datetime.now(config.TZ).isoformat(timespec="seconds")


def kaydet(con, kayit, etk):
    kayit = dict(kayit, olusturma=now())
    con.execute(f"INSERT OR REPLACE INTO ozetler ({', '.join(kayit)}) VALUES ({', '.join('?' * len(kayit))})",
                list(kayit.values()))
    con.execute("DELETE FROM etiketler WHERE duzenleme_id=?", (kayit["duzenleme_id"],))
    con.executemany("INSERT INTO etiketler VALUES (?,?,?)", [(kayit["duzenleme_id"], e, k) for e, k in etk])
    con.commit()


def ozetle_bir(con, row, etiketler):
    try:
        kayit, etk = ozet.duzenleme_ozetle(row, etiketler)
    except Exception as e:
        log.warning("%s: özet hatası: %s", row["id"], e)
        kayit, etk = {"duzenleme_id": row["id"], "model": config.OLLAMA_MODEL, "durum": "hata",
                      "kontrol_neden": str(e)[:300]}, []
    kaydet(con, kayit, etk)
    log.info("%s: %s (%s sn)%s", row["id"], kayit["durum"], kayit.get("sure_s"),
             f" — {kayit['kontrol_neden'].splitlines()[0]}" if kayit.get("kontrol_neden") else "")
    return kayit["durum"]


def cmd_calistir(con):
    etiketler = ozet.etiketleri_yukle()
    bas = datetime.now().timestamp()
    rows = con.execute(BEKLEYEN).fetchall()
    sayac = {}
    for k, row in enumerate(rows):
        if datetime.now().timestamp() - bas > config.OZET_SURE_S:
            log.info("özet süre sınırı; kalan %d düzenleme sonraki çalıştırmada", len(rows) - k)
            break
        d = ozetle_bir(con, row, etiketler)
        sayac[d] = sayac.get(d, 0) + 1
        if k + 1 < len(rows):
            time.sleep(config.LLM_ARA_S)   # bsasrv'nin soğuması için
    log.info("özet: %s; fark: %d değişiklik değerlendirildi", sayac or "yeni iş yok", fark.tumunu_uygula(con))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(config.LOG_DIR / "ozet.log", encoding="utf-8")])
    if not argv:
        print(__doc__)
        return 2
    con = db.connect(config.DB_PATH)
    if argv[0] == "calistir":
        return cmd_calistir(con)
    if argv[0] == "yeniden" and len(argv) > 1:
        row = con.execute("SELECT * FROM duzenlemeler WHERE id=?", (argv[1],)).fetchone()
        if not row:
            print("bulunamadı")
            return 1
        ozetle_bir(con, row, ozet.etiketleri_yukle())
        print("\n".join(str(x) for x in con.execute(
            "SELECT ozet, durum, kontrol_neden FROM ozetler WHERE duzenleme_id=?", (argv[1],)).fetchone()))
        return 0
    if argv[0] == "etiketle" and argv[1:] == ["--hepsi"]:
        etiketler = ozet.etiketleri_yukle()
        con.execute("DELETE FROM etiketler WHERE kaynak='kelime' OR etiket NOT IN (%s)"
                    % ",".join("?" * len(etiketler)), list(etiketler))
        n = 0
        for r in con.execute("SELECT d.id, d.baslik, d.kurum FROM duzenlemeler d JOIN ozetler o "
                             "ON o.duzenleme_id=d.id").fetchall():
            for e in ozet.kelime_etiketleri(etiketler, r["baslik"], r["kurum"]):
                n += con.execute("INSERT OR IGNORE INTO etiketler VALUES (?,?,'kelime')", (r["id"], e)).rowcount
        con.commit()
        log.info("kelime etiketi: %d (model etiketleri korunur; yeni etiketler için 'yeniden')", n)
        return 0
    if argv[0] == "yururluk":
        n = 0
        for r in con.execute("SELECT o.*, d.metin, d.tarih FROM ozetler o JOIN duzenlemeler d ON d.id=o.duzenleme_id "
                             "WHERE o.olgular IS NULL AND o.ozet IS NOT NULL").fetchall():
            eski = ozet.yururluk_cumlesi(r["yururluk"], r["yururluk_tarih"], r["tarih"])
            c, t = ozet.yururluk_bul(r["metin"] or "", r["tarih"])
            yeni = ozet.yururluk_cumlesi(c, t, r["tarih"])
            if r["ozet"].endswith(eski) and eski != yeni:
                # metin değişti: onay varsa düşer
                con.execute("UPDATE ozetler SET ozet=?, yururluk=?, yururluk_tarih=?, onay=NULL, onay_zamani=NULL "
                            "WHERE duzenleme_id=?",
                            (r["ozet"][: -len(eski)] + yeni, c, t, r["duzenleme_id"]))
                n += 1
                log.info("%s: %s → %s", r["duzenleme_id"], eski, yeni)
        con.commit()
        log.info("yürürlük cümlesi güncellenen özet: %d", n)
        return 0
    if argv[0] == "fark":
        log.info("fark: %d değişiklik değerlendirildi", fark.tumunu_uygula(con))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
