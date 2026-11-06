"""Taranmış PDF sayfaları için OCR (tesseract, Türkçe) ve güvenlik denetimleri.

Kurallar:
- Yalnız gövdesi görüntü olan sayfalar okunur; yazı katmanı olan sayfalar aynen kalır.
- OCR metni otomatik değişiklik eşleştirmesine (diff) girmez (bkz. collect.cmd_ocr).
- Andlaşmalarda yalnız Türkçe metin okunur; Türkçe'den sonra gelen ilk başka dil bölümünde durulur.
- Ortalama güveni çok düşük sayfalar (harita, tablo, imza) metne alınmaz, yerine not düşülür.
- Atamada düşük güvenli ad/yer, il adına benzeyen ama il olmayan sözcük, "(0) sayılı" gibi
  şüpheli numaralar → kontrol_gerekli (Faz 2 özeti onaylanmadan yayımlamaz).
"""
import csv
import io
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from . import config, dogrula, parse

DPI = 400
SAYFA_YAZI_ESIK = 400   # yazı katmanı bundan kısa + büyük görüntü = taranmış sayfa
GORSEL_ESIK = 100_000   # piksel; Cumhurbaşkanlığı amblemi (354x236) bunun altında kalır
OKUNAMAZ_GUVEN = 60     # sayfa ortalaması bunun altındaysa metne alınmaz
HARITA_GUVEN, HARITA_KELIME = 75, 200  # az sözcük + orta güven: kroki/harita/imza sayfası
DUSUK_GUVEN = 85        # atamalarda ad/yer için eşik
TR_ORAN = 0.10          # andlaşma dil ayrımı: Türkçe işlev sözcüğü oranı
DIL_SAYFA_KELIME = 150  # bu kadar sözcükten kısa sayfalar dil kararına katılmaz

ARACLAR = ("pdftoppm", "pdfimages", "tesseract")

_TR_SOZ = set("ve ile bu için olarak veya bir olan her ilgili göre taraflar madde tarafından "
              "şekilde dair hakkında kadar".split())

ILLER = set("""Adana Adıyaman Afyonkarahisar Ağrı Amasya Ankara Antalya Artvin Aydın Balıkesir Bilecik
Bingöl Bitlis Bolu Burdur Bursa Çanakkale Çankırı Çorum Denizli Diyarbakır Edirne Elazığ Erzincan
Erzurum Eskişehir Gaziantep Giresun Gümüşhane Hakkari Hatay Isparta Mersin İstanbul İzmir Kars
Kastamonu Kayseri Kırklareli Kırşehir Kocaeli Konya Kütahya Malatya Manisa Kahramanmaraş Mardin Muğla
Muş Nevşehir Niğde Ordu Rize Sakarya Samsun Siirt Sinop Sivas Tekirdağ Tokat Trabzon Tunceli
Şanlıurfa Uşak Van Yozgat Zonguldak Aksaray Bayburt Karaman Kırıkkale Batman Şırnak Bartın Ardahan
Iğdır Yalova Karabük Kilis Osmaniye Düzce""".split())


def eksik_araclar():
    return [a for a in ARACLAR if not shutil.which(a)]


def gorsel_sayfalari(path):
    """Büyük görüntü içeren sayfa numaraları (1'den)."""
    r = subprocess.run(["pdfimages", "-list", str(path)], capture_output=True, text=True, timeout=60)
    out = set()
    for ln in r.stdout.splitlines()[2:]:
        c = ln.split()
        if len(c) > 4 and c[0].isdigit() and c[3].isdigit() and c[4].isdigit() \
                and int(c[3]) * int(c[4]) > GORSEL_ESIK:
            out.add(int(c[0]))
    return out


def taranmis_sayfalar(path, sayfalar=None):
    """Yazı katmanı kısa ve büyük görüntü içeren sayfalar (1'den). Çok sayfalı belgede
    her sayfa ayrı değerlendirilir: başlık/imza yazısı sayfa başına sayılır."""
    if not shutil.which("pdfimages"):
        return []
    sayfalar = parse.pdf_sayfalari(path) if sayfalar is None else sayfalar
    gorsel = gorsel_sayfalari(path)
    return [i for i, t in enumerate(sayfalar, 1)
            if i in gorsel and len(re.sub(r"\s+", "", t)) < SAYFA_YAZI_ESIK]


def tsv_metin(tsv):
    """tesseract TSV çıktısı → (metin, [(sözcük, güven)]). Tek geçiş: metin TSV'den kurulur."""
    out, kelimeler, onceki = [], [], None
    for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        w = (r.get("text") or "").strip()
        if r.get("level") != "5" or not w:
            continue
        key = tuple(int(r[k]) for k in ("page_num", "block_num", "par_num", "line_num"))
        if onceki is not None:
            out.append("\n\n" if key[:3] != onceki[:3] else "\n" if key != onceki else " ")
        out.append(w)
        kelimeler.append((w, float(r["conf"])))
        onceki = key
    return "".join(out), kelimeler


def ocr_sayfa(path, no, tmp):
    on = Path(tmp) / f"s{no}"
    subprocess.run(["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(no), "-l", str(no),
                    "-singlefile", str(path), str(on)], check=True, capture_output=True, timeout=300)
    png = on.with_suffix(".png")
    r = subprocess.run(["tesseract", str(png), "-", "-l", "tur", "tsv"],
                       capture_output=True, text=True, timeout=900)
    png.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(f"tesseract hata (sayfa {no}): {r.stderr[:200]}")
    return tsv_metin(r.stdout)


def tr_orani(metin):
    w = re.findall(r"\w+", metin.lower())
    return sum(x in _TR_SOZ for x in w) / max(1, len(w))


def ocr_pdf(path, tur):
    """Taranmış sayfaları okur, yazı sayfalarıyla sırasıyla birleştirir.
    Dönüş: (metin, bilgi) — bilgi sayfa başına güven, düşük güvenli sözcükler, atlanan sayfalar."""
    t0 = time.time()
    yazi = parse.pdf_sayfalari(path)
    taranmis = set(taranmis_sayfalar(path, yazi))
    parcalar, sayfa_bilgi, tum_guven = [], [], []
    tr_goruldu, dur = False, None
    with tempfile.TemporaryDirectory(prefix="gazete-ocr-") as tmp:
        for i, t in enumerate(yazi, 1):
            if i not in taranmis:
                parcalar.append(t)
                continue
            s0 = time.time()
            metin, kel = ocr_sayfa(path, i, tmp)
            time.sleep((time.time() - s0) * config.OCR_DINLENME)   # ısınmayı azaltmak için çalış/dinlen
            guven = [c for _, c in kel if c >= 0]
            s = {"no": i, "kelime": len(kel), "guven": round(statistics.mean(guven), 1) if guven else 0}
            if tur == "andlaşma" and len(kel) >= DIL_SAYFA_KELIME:
                if tr_orani(metin) >= TR_ORAN:
                    tr_goruldu = True
                elif tr_goruldu:
                    s["durum"] = "dil-dışı"
                    sayfa_bilgi.append(s)
                    dur = i
                    break
            if not kel:
                s["durum"] = "boş"
            elif s["guven"] < OKUNAMAZ_GUVEN or (s["guven"] < HARITA_GUVEN and len(kel) < HARITA_KELIME):
                s["durum"] = "okunamadı"
                parcalar.append(f"[sayfa {i}: otomatik okunamadı (harita, tablo, imza vb.) — "
                                "kaynaktaki görüntüye bakın]")
            else:
                s["durum"] = "ocr"
                s["dusuk"] = [[w, c] for w, c in kel if 0 <= c < DUSUK_GUVEN][:80]
                tum_guven += guven
                parcalar.append(metin)
            sayfa_bilgi.append(s)
    if dur:
        parcalar.append(f"[sayfa {dur}–{len(yazi)}: andlaşmanın Türkçe dışındaki dil metinleri "
                        "okunmadı — kaynağa bakın]")
    bilgi = {"dpi": DPI, "sayfa": len(yazi), "sayfalar": sayfa_bilgi,
             "guven": round(statistics.mean(tum_guven), 1) if tum_guven else None,
             "sure_s": round(time.time() - t0)}
    tamami = all(i in taranmis for i in range(1, len(yazi) + 1))
    return parse.normalize_text("\n".join(parcalar)), bilgi, ("ocr" if tamami else "pdf+ocr")


def _mesafe(a, b):
    """Levenshtein uzaklığı."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def kontrol_nedenleri(tur, metin, bilgi, baslik=""):
    """Yayımdan önce bir insanın bakması gereken yerler (boşsa sorun görülmedi).
    Başlık fihristteki HTML metnidir (güvenilir): oradaki tarih ve sayılar OCR metninde de geçmeli."""
    neden = [f"başlıktaki sayı/tarih metinde yok: {x}" for x in dogrula.sayi_denetimi(baslik, metin)]
    # Yalnız il bağlamındaki sözcükler: "X İl Müdürlüğü", "X ili", "X Valiliği" ("İl Yolu" değil)
    for w in sorted(set(re.findall(r"\b([A-ZÇĞİÖŞÜ][a-zçğıöşü]{4,}) (?:İl (?!Yolu)|ili\b|İli\b|Valili)", metin))):
        if w not in ILLER and any(len(il) >= 5 and _mesafe(w, il) == 1 for il in ILLER):
            neden.append(f"il adına benziyor, il değil: {w}")
    for m in re.finditer(r"\((0|O|o)\) [Ss]ayılı", metin):
        neden.append(f"şüpheli numara: {m.group(0)}")
    if tur in ("atama", "vekâlet"):
        for s in bilgi["sayfalar"]:
            if s.get("durum") == "okunamadı":
                neden.append(f"sayfa {s['no']} okunamadı")
            for w, c in s.get("dusuk", []):
                if w[:1].isupper() and sum(ch.isalpha() for ch in w) >= 2:
                    neden.append(f"düşük güvenli ad/yer: {w} ({c:.0f})")
    return neden
