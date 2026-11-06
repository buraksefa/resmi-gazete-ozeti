"""Özet, yürürlük ve etiket üretimi (yerel LLM: Ollama).

İlkeler:
- Özet yalnız metne dayanır; yorum/değerlendirme/tahmin yok.
- Yürürlük cümlesi LLM'den değil, metindeki "yürürlüğe girer" maddesinden kuralla çıkarılır.
- Özetteki her sayı/tarih ve özel ad kaynakta geçmeli; geçmiyorsa özet "kontrol" durumunda kalır.
- Atama/vekâlet: serbest özet yok; yalnız olgu listesi (işlem, ad, görev), her ad metinde birebir aranır.
"""
import json
import re
import time
import urllib.request

from . import amend, config, dogrula

try:
    import tomllib
except ImportError:  # Python < 3.11 (yalnız geliştirme makinesi)
    tomllib = None

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
         "Kasım", "Aralık"]

# Yorum/değerlendirme/tahmin bildiren ifadeler: özette geçerse kontrol
YORUM = ["önemli", "olumlu", "olumsuz", "maalesef", "ne yazık", "muhtemelen", "büyük ihtimalle", "beklenmekte",
         "öngörülmekte", "kritik", "tartışmalı", "eleştiri", "başarılı", "yerinde bir", "nihayet", "dikkat çek",
         "sevindirici", "endişe", "radikal", "köklü", "tarihi bir"]

KURALLAR = """Kurallar:
- Yalnızca aşağıdaki metinde açıkça yazanları kullan. Metinde olmayan bilgi, yorum, değerlendirme, tahmin ekleme.
- Sade Türkçe yaz. Siyasi ya da değerlendirici sıfat kullanma.
- Tarih, sayı, kurum ve kişi adlarını metindeki gibi aynen yaz; yuvarlama, çevirme, tahmin yapma.
- Yürürlük tarihini yazma (ayrıca eklenecek)."""

OZET_ISTEM = """Aşağıdaki Resmî Gazete düzenlemesini özetle.
{kurallar}

JSON döndür:
{{"ozet": "En fazla 2 cümle: düzenleme kimi/neyi etkiliyor ve ne değişiyor ya da ne getiriyor.",
  "etiketler": ["aşağıdaki listeden 0-3 anahtar; uygun yoksa boş liste"]}}

Etiket listesi (anahtar: tanım):
{etiketler}

BAŞLIK: {baslik}
KURUM: {kurum}
METİN{kisa}:
{metin}"""

OLGU_ISTEM = """Aşağıdaki Resmî Gazete atama/görevlendirme kararlarındaki olguları çıkar.
Yalnızca metinde yazanı aynen al. Kişi hakkında hiçbir yorum, bağlam ya da değerlendirme ekleme.

JSON döndür:
{{"kararlar": [{{"islem": "atama | görevden alma | vekâlet | diğer",
                "ad": "kişinin adı soyadı, metindeki gibi",
                "gorev": "kurum ve görev, metindeki gibi"}}]}}

METİN:
{metin}"""


def kucuk(s):
    return s.replace("I", "ı").replace("İ", "i").lower()


def tarih_tr(iso):
    y, a, g = iso.split("-")
    return f"{int(g)} {AYLAR[int(a) - 1]} {y}"


# --- LLM ------------------------------------------------------------------------------------

def llm_json(istem, num_predict=400, model=None):
    secenek = {"temperature": 0, "num_ctx": config.LLM_NUM_CTX, "num_predict": num_predict}
    if config.LLM_NUM_THREAD:
        secenek["num_thread"] = config.LLM_NUM_THREAD
    body = json.dumps({"model": model or config.OLLAMA_MODEL, "prompt": istem, "stream": False, "format": "json",
                       "options": secenek}).encode()
    req = urllib.request.Request(f"{config.OLLAMA_URL}/api/generate", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1200) as r:
        cevap = json.load(r)
    try:
        return json.loads(cevap["response"])
    except json.JSONDecodeError as e:
        raise RuntimeError(f"model geçerli JSON döndürmedi: {cevap['response'][:200]}") from e


# --- girdi ----------------------------------------------------------------------------------

def girdi_hazirla(metin, sinir=None):
    """Modelin bağlamına sığacak metin. Uzun düzenlemede: ilk iki madde (amaç, kapsam),
    madde başları, son iki madde (yürürlük, yürütme). Maddesiz metinde: baş ve son kısım
    (yargı kararlarında hüküm sondadır). Dönüş: (girdi, kisaltildi_mi)."""
    sinir = sinir or config.LLM_GIRDI_SINIR
    if len(metin) <= sinir:
        return metin, False
    md = amend.maddeler(metin)
    if len(md) >= 5:
        bas = [g[:1200] for _, _, g in md[:2]]
        son = [g[:800] for _, _, g in md[-2:]]
        orta = [re.sub(r"\s+", " ", g)[:110] + " …" for _, _, g in md[2:-2]]
        parca = bas + ["[Diğer maddelerin yalnız başı:]"] + orta + ["[Son maddeler:]"] + son
        girdi = "\n".join(parca)
        if len(girdi) > sinir:  # çok maddeli: orta kısmı kırp
            fazla = len(girdi) - sinir
            orta = orta[: max(0, len(orta) - fazla // 100 - 1)] + ["[… madde başları kısaltıldı]"]
            girdi = "\n".join(bas + ["[Diğer maddelerin yalnız başı:]"] + orta + ["[Son maddeler:]"] + son)
        return girdi[:sinir], True
    on, arka = int(sinir * 0.6), int(sinir * 0.4)
    return metin[:on] + "\n[… metnin ortası kısaltıldı …]\n" + metin[-arka:], True


# --- yürürlük (kural) -----------------------------------------------------------------------

_YURURLUK_RE = re.compile(r"[^.\n]*yürürlüğe gir(?:er|ecektir)[^\n]*?(?:\.|$)", re.M)
_BELGE = r"Bu (?:Yönetmelik|Tebliğ|Karar|Kanun|Kararname|Cumhurbaşkanlığı Kararnamesi|Kanun Hükmünde Kararname|Genelge|Usul ve Esaslar|Ana Statü|Esaslar|Yönerge)"
_BASIT_YAYIM_RE = re.compile(_BELGE + r" yayımı tarihinde yürürlüğe girer\.")
_BASIT_TARIH_RE = re.compile(_BELGE + r" (\d{1,2})/(\d{1,2})/(\d{4}) tarihinde yürürlüğe girer\.")


def yururluk_bul(metin, yayim_tarihi):
    """Dönüş: (cümle_aynen | None, tarih_iso | None). Birden çok/koşullu yürürlükte tarih None."""
    cumleler = []
    # Önce madde bütünü: "MADDE 5- (1) Bu Tebliğin; a) … yayımı tarihinde, b) diğer hükümleri … yürürlüğe girer."
    for _, _, govde in amend.maddeler(metin):
        g = re.sub(r"\s+", " ", govde)
        m = re.search(r"yürürlüğe gir(?:er|ecektir)[^.]*\.", g)
        if m and not re.search(r"[“”\"]", g[: m.end()]):
            c = re.sub(r"^(?:(?:GEÇİCİ |EK )?MADDE \d+\s*[-–—]\s*)?(?:\(\d+\)\s*)?", "", g[: m.end()]).strip()
            if c not in cumleler:
                cumleler.append(c)
    if not cumleler:  # maddesiz metin (ör. Cumhurbaşkanı kararı)
        for m in _YURURLUK_RE.finditer(metin):
            c = re.sub(r"\s+", " ", m.group(0)).strip()
            if c and c not in cumleler and not re.search(r"[“”\"]", c):
                cumleler.append(c)
    if not cumleler:
        return None, None
    cumle = " ".join(cumleler)[:600]
    # Yalnız kalıba birebir uyan tek cümle sadeleştirilir; ek koşul, istisna, "geçerli olmak üzere"
    # gibi her ayrıntıda hüküm aynen alıntılanır.
    if len(cumleler) == 1:
        if _BASIT_YAYIM_RE.fullmatch(cumleler[0]):
            return cumle, yayim_tarihi
        t = _BASIT_TARIH_RE.fullmatch(cumleler[0])
        if t:
            return cumle, f"{t.group(3)}-{int(t.group(2)):02d}-{int(t.group(1)):02d}"
    return cumle, None


def yururluk_cumlesi(cumle, tarih, yayim_tarihi):
    if tarih:
        ek = " (yayımı tarihinde)" if tarih == yayim_tarihi else ""
        return f"{tarih_tr(tarih)} tarihinde yürürlüğe girer{ek}."
    if cumle:
        return f"Yürürlük: “{cumle}”"
    return "Metinde ayrı bir yürürlük hükmü yok."


# --- etiketler ------------------------------------------------------------------------------

def etiketleri_yukle(yol=None):
    if tomllib is None:
        raise RuntimeError("etiketler.toml okumak için Python ≥ 3.11 gerekir")
    with open(yol or config.ETIKET_DOSYASI, "rb") as f:
        return tomllib.load(f)


def kelime_etiketleri(etiketler, baslik, kurum):
    hedef = kucuk(f"{baslik} {kurum or ''}")
    return [a for a, e in etiketler.items() if _kelime_var(e.get("kelimeler", []), hedef)]


def _kelime_var(kelimeler, metin_k):
    return any(re.search(r"(?<!\w)" + re.escape(kucuk(k)), metin_k) for k in kelimeler)


# --- atama olguları (kural) -----------------------------------------------------------------

_ISLEM_RE = re.compile(r"görev(?:in)?den alınarak|görev(?:in)?den alınmıştır|atanmıştır|görevlendirilmiştir|"
                       r"vekâlet etme|vekâlet edecek")
_AD_RE = re.compile(r"(?:[A-ZÇĞİÖŞÜ][a-zçğıöşü]+ ){1,6}[A-ZÇĞİÖŞÜ]{2,}(?: [A-ZÇĞİÖŞÜ]{2,})?(?![a-zçğıöşü])")
IMZA = ("Recep Tayyip ERDOĞAN",)


def islem_bul(metin_d, ad):
    """Adın geçtiği yerden sonraki ilk işlem fiili (metin_d: boşlukları tekleştirilmiş metin)."""
    i = metin_d.find(ad)
    if i < 0:
        return None
    for m in _ISLEM_RE.finditer(metin_d, i):
        f = m.group(0)
        if "alınarak" in f:      # "görevinden alınarak ... atanmıştır" → atama
            continue
        return {"atanmıştır": "atama", "görevlendirilmiştir": "görevlendirme"}.get(
            f, "vekâlet" if f.startswith("vekâlet") else "görevden alma")
    return None


def eksik_adlar(metin_d, adlar):
    """Metinde ad-soyad kalıbında geçip olgu listesinde olmayanlar."""
    out = []
    for m in _AD_RE.finditer(metin_d):
        bulunan = m.group(0)
        if any(i in bulunan for i in IMZA) or any(a in bulunan for a in adlar):
            continue
        out.append(" ".join(bulunan.split()[-3:]))
    return list(dict.fromkeys(out))


# --- denetim --------------------------------------------------------------------------------

def ozet_denetle(ozet, kaynak):
    """Özetin kaynağa dayanıp dayanmadığına dair otomatik bulgular (boşsa sorun yok)."""
    neden = [f"kaynakta geçmeyen sayı/tarih: {x}" for x in dogrula.sayi_denetimi(ozet, kaynak)]
    k = kucuk(kaynak)
    # Cümle başı olmayan büyük harfli sözcükler (özel adlar): son 4 harfi (çekim eki payı)
    # dışındaki kökü kaynakta geçmeli
    for m in re.finditer(r"(?<=[^.!?:\s] )([A-ZÇĞİÖŞÜ][\wçğıöşüâîû’'-]{3,})", ozet):
        w = re.split(r"[’']", m.group(1))[0]
        if kucuk(w)[:max(5, len(w) - 4)] not in k:
            neden.append(f"kaynakta geçmeyen ad: {w}")
    ok = kucuk(ozet)
    neden += [f"yorum/değerlendirme ifadesi: {y}" for y in YORUM if y in ok and y not in k]
    if len(re.findall(r"[.!?](?:\s|$)", ozet)) > 3:
        neden.append("2 cümleden uzun")
    return neden


# --- ana işlevler ---------------------------------------------------------------------------

def duzenleme_ozetle(row, etiketler):
    """row: duzenlemeler satırı. Dönüş: (ozetler kaydı dict, [(etiket, kaynak)])."""
    t0 = time.time()
    metin = row["metin"] or ""
    kaynak = f"{row['baslik']}\n{metin}"
    yur_cumle, yur_tarih = yururluk_bul(metin, row["tarih"])
    metin_d = re.sub(r"\s+", " ", metin)   # satır sonuna bölünen adlar için
    kayit = {"duzenleme_id": row["id"], "model": config.OLLAMA_MODEL, "yururluk": yur_cumle,
             "yururluk_tarih": yur_tarih, "olgular": None, "girdi_kisaltildi": 0}
    neden = []
    etk = [(e, "kelime") for e in kelime_etiketleri(etiketler, row["baslik"], row["kurum"])]

    if row["tur"] in ("atama", "vekâlet"):
        girdi, kisa = girdi_hazirla(metin)
        cevap = llm_json(OLGU_ISTEM.format(metin=girdi), num_predict=1500, model=config.OLLAMA_MODEL_OLGU)
        kayit["model"] = config.OLLAMA_MODEL_OLGU
        olgular = []
        for k in cevap.get("kararlar") or []:
            ad, gorev = (k.get("ad") or "").strip(), (k.get("gorev") or "").strip()
            if not ad:
                continue
            ad = re.sub(r"\s+", " ", ad)
            if ad not in metin_d:
                neden.append(f"ad metinde birebir yok: {ad}")
            # görev sözcükleri metinde geçmeli (çekim ekleri için ilk 6 harf)
            gt = {w[:6] for w in re.findall(r"\w+", kucuk(gorev)) if len(w) > 3}
            mt = {w[:6] for w in re.findall(r"\w+", kucuk(metin))}
            if gt and len(gt & mt) / len(gt) < 0.8:
                neden.append(f"görev metinle uyuşmuyor: {gorev[:80]}")
            # işlem türü modelden değil metinden: adın ardından gelen ilk fiil
            islem = islem_bul(metin_d, ad)
            if islem is None:
                islem = k.get("islem") or "diğer"
                neden.append(f"işlem metinden doğrulanamadı: {ad}")
            olgular.append({"islem": islem, "ad": ad, "gorev": gorev})
        if not olgular:
            neden.append("olgu çıkarılamadı")
        neden += [f"metinde geçen ama listede olmayan ad: {a}"
                  for a in eksik_adlar(metin_d, [o["ad"] for o in olgular])]
        sayac = {}
        for o in olgular:
            sayac[o["islem"]] = sayac.get(o["islem"], 0) + 1
        ozet = (f"{len(olgular)} kişiye ilişkin karar: "
                + ", ".join(f"{n} {i}" for i, n in sorted(sayac.items())) + ".") if olgular else ""
        kayit.update(olgular=json.dumps(olgular, ensure_ascii=False), girdi_kisaltildi=int(kisa))
    else:
        girdi, kisa = girdi_hazirla(metin)
        liste = "\n".join(f"- {a}: {e.get('aciklama', e.get('ad', a))}" for a, e in etiketler.items())
        cevap = llm_json(OZET_ISTEM.format(kurallar=KURALLAR, etiketler=liste, baslik=row["baslik"],
                                           kurum=row["kurum"] or "-", metin=girdi,
                                           kisa=" (uzun metin; seçilmiş bölümler)" if kisa else ""))
        govde = re.sub(r"\s+", " ", str(cevap.get("ozet") or "")).strip()
        # yürürlük/yürütme kuraldan eklenir; model yazdıysa o cümleyi at
        govde = " ".join(c for c in re.split(r"(?<=[.!?])\s+", govde)
                         if not re.search(r"yürürlüğe gir|yürütü", c)).strip()
        if not govde:
            neden.append("model özet döndürmedi")
        neden += ozet_denetle(govde, kaynak)
        for e in cevap.get("etiketler") or []:
            # model etiketi ancak o etiketin bir anahtar kelimesi metinde de geçiyorsa alınır
            if isinstance(e, str) and e in etiketler and e not in [x for x, _ in etk] \
                    and _kelime_var(etiketler[e].get("kelimeler", []), kucuk(kaynak)):
                etk.append((e, "llm"))
        ozet = (govde + " " if govde else "") + yururluk_cumlesi(yur_cumle, yur_tarih, row["tarih"])
        kayit["girdi_kisaltildi"] = int(kisa)

    if row["kontrol_gerekli"]:
        neden.append("kaynak metin OCR kontrolü bekliyor")
    kayit.update(ozet=ozet, durum="kontrol" if neden else "hazir",
                 kontrol_neden="\n".join(neden) or None, sure_s=round(time.time() - t0, 1))
    return kayit, etk
