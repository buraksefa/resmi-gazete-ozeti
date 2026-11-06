"""Rastgele N özeti kaynak metinle karşılaştırır ve rapor yazar.

  python -m gazete.dogrulama            # 20 özet
  python -m gazete.dogrulama 20 --tohum 7 [--hakem]

Her özet için:
  1. sayı/tarih: özetteki her tarih ve sayı kaynakta birebir geçiyor mu
  2. özel adlar: özetteki büyük harfli adlar kaynakta geçiyor mu
  3. yorum: değerlendirme/tahmin ifadesi var mı
  4. yürürlük: özetteki yürürlük cümlesi metindeki yürürlük hükmüyle aynı mı (yeniden hesaplanır)
  5. konu: başlıktaki anahtar sözcüklerden en az biri özette geçiyor mu
  6. iddia denetimi (LLM, yalnız --hakem ile): 2 Kasım 2026 denemesinde gemma3:4b hakem doğru
     iddiaların çoğunu "sorunlu" saydı, gerçek hataların üçünü de ayırt edemedi; varsayılan kapalı
  7. atama/vekâlet: her ad metinde birebir geçiyor mu
Rapor: data/rapor/dogrulama-<zaman>.md — insan okuması için kaynak alıntısıyla birlikte.
"""
import json
import random
import re
import sys
from datetime import datetime

from . import config, db, ozet

IDDIA_ISTEM = """Bir özetin kaynak metne sadık olup olmadığını denetliyorsun.
Özetteki her iddiayı ayrı ayrı kaynak metinle karşılaştır. Metinde açıkça desteklenmeyen,
metinle çelişen ya da yorum/değerlendirme içeren iddiaları listele. Emin değilsen listeye ekle.

JSON döndür: {{"desteklenmeyen": ["iddia — neden"], "karar": "sadık | sorunlu"}}

ÖZET:
{ozet}

KAYNAK METİN{kisa}:
{metin}"""

_DURMA = set("ve ile bir bu için dair ilişkin hakkında yapılmasına değişiklik yönetmelik yönetmeliği tebliğ "
             "karar kararı sayılı tarihli ile ilgili".split())


def konu_ortusmesi(baslik, ozet_metni):
    bt = {w for w in re.findall(r"\w+", ozet.kucuk(baslik)) if len(w) > 3 and w not in _DURMA}
    ot = ozet.kucuk(ozet_metni)
    return [w for w in bt if w[:6] in ot]


def denetle(con, r, hakem_ac=False):
    d = con.execute("SELECT * FROM duzenlemeler WHERE id=?", (r["duzenleme_id"],)).fetchone()
    kaynak = f"{d['baslik']}\n{d['metin'] or ''}"
    kaynak_d = re.sub(r"\s+", " ", kaynak)
    bulgu = []
    hakem = None
    yur_cumle, yur_tarih = ozet.yururluk_bul(d["metin"] or "", d["tarih"])
    if d["tur"] in ("atama", "vekâlet"):
        for o in json.loads(r["olgular"] or "[]"):
            if o["ad"] not in kaynak_d:
                bulgu.append(f"ad metinde yok: {o['ad']}")
    else:
        beklenen = ozet.yururluk_cumlesi(yur_cumle, yur_tarih, d["tarih"])
        govde = r["ozet"][: -len(beklenen)].strip() if r["ozet"].endswith(beklenen) else None
        if govde is None:
            bulgu.append("yürürlük cümlesi metindeki hükümle uyuşmuyor")
            govde = r["ozet"]
        bulgu += ozet.ozet_denetle(govde, kaynak)
        if not konu_ortusmesi(d["baslik"], govde):
            bulgu.append("başlıktaki konu sözcüklerinden hiçbiri özette yok")
        if hakem_ac:
            girdi, kisa = ozet.girdi_hazirla(d["metin"] or "")
            try:
                hakem = ozet.llm_json(IDDIA_ISTEM.format(ozet=govde, metin=girdi,
                                                         kisa=" (uzun; seçilmiş bölümler)" if kisa else ""), 1200)
            except Exception as e:
                hakem = {"karar": "hata", "desteklenmeyen": [str(e)[:200]]}
    return d, bulgu, hakem


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    n = int(argv[0]) if argv and argv[0].isdigit() else 20
    tohum = int(argv[argv.index("--tohum") + 1]) if "--tohum" in argv else random.randrange(10**6)
    con = db.connect(config.DB_PATH)
    rows = con.execute("SELECT * FROM ozetler WHERE durum IN ('hazir', 'kontrol') ORDER BY duzenleme_id").fetchall()
    secim = random.Random(tohum).sample(rows, min(n, len(rows)))
    zaman = datetime.now(config.TZ).strftime("%Y-%m-%d-%H%M")
    satirlar, ayrinti, temiz = [], [], 0
    for i, r in enumerate(secim, 1):
        d, bulgu, hakem = denetle(con, r, "--hakem" in argv)
        sorunlu_hakem = hakem and hakem.get("karar") != "sadık"
        if not bulgu and not sorunlu_hakem:
            temiz += 1
        satirlar.append(f"| {i} | {d['id']} | {d['tur']} | {r['durum']} | {len(bulgu)} | "
                        f"{(hakem or {}).get('karar', '—')} |")
        ayrinti += [f"## {i}. {d['baslik']}", "",
                    f"- id: `{d['id']}` · tür: {d['tur']} · metin: {d['metin_kaynagi'] or d['bicim']}"
                    f"{' · girdi kısaltıldı' if r['girdi_kisaltildi'] else ''} · durum: {r['durum']}",
                    f"- kaynak: {d['kaynak_url']}", "", f"> {r['ozet']}", ""]
        if r["olgular"]:
            ayrinti += [f"- {o['islem']}: {o['ad']} — {o['gorev']}" for o in json.loads(r["olgular"])] + [""]
        ayrinti += ["**Otomatik bulgular:** " + ("; ".join(bulgu) if bulgu else "yok")]
        if hakem:
            ayrinti += [f"**İddia denetimi (LLM, {config.OLLAMA_MODEL}):** {hakem.get('karar')}"
                        + "".join(f"\n  - {x}" for x in hakem.get("desteklenmeyen") or [])]
        alinti = re.sub(r"\n{2,}", "\n", (d["metin"] or "")[:1500])
        ayrinti += ["", "<details><summary>Kaynak metnin başı</summary>", "", "```", alinti, "```",
                    "</details>", ""]
        print(f"{i}/{len(secim)} {d['id']}: {len(bulgu)} bulgu, hakem {(hakem or {}).get('karar', '—')}", flush=True)
    rapor = [f"# Özet doğrulama — {zaman}", "",
             f"Örneklem: {len(secim)} / {len(rows)} özet (tohum {tohum}, model {config.OLLAMA_MODEL}).",
             f"Hiç bulgu ve hakem itirazı olmayan: {temiz}/{len(secim)}.", "",
             "Not: otomatik denetimler yalnız işarettir; son karar kaynak metni okuyan insanındır.", "",
             "| # | id | tür | durum | bulgu | hakem |", "|---|---|---|---|---|---|", *satirlar, "", *ayrinti]
    config.RAPOR_DIR.mkdir(parents=True, exist_ok=True)
    yol = config.RAPOR_DIR / f"dogrulama-{zaman}.md"
    yol.write_text("\n".join(rapor) + "\n", encoding="utf-8")
    print(f"rapor: {yol}  ({temiz}/{len(secim)} temiz)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
