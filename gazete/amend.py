"""Değişiklik ilişkisi: "X tarihli ve Y sayılı Resmî Gazete'de yayımlanan Z
Yönetmeliğinin N inci maddesi ..." kalıplarını ayrıştırır ve hedefe bağlar.

İlke: emin olamadığımızda tahmin etmeyiz; 'karmasik' işaretleriz."""
import re
from dataclasses import dataclass

ORD = r"(?:inci|ıncı|uncu|üncü|nci|ncı|ncu|ncü)"

# Madde başlangıcı: "MADDE 1-", "MADDE 13 –", "GEÇİCİ MADDE 2-", "EK MADDE 1-"
MADDE_RE = re.compile(r"^((?:GEÇİCİ |EK )?MADDE)\s+(\d+)\s*[-–—]", re.M)

REF_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4}) tarihli ve (\d+) sayılı Resm[iî] Gazete[’'`]?de yayımlanan\s+"
    r"(?P<g>.+?)\s+"
    r"(?=(?:(?:geçici |ek )?\d+ ?" + ORD + r" madde|geçici madde|ek madde|aşağıdaki|ekl?i(?:nde)?\s|"
    r"ek(?:-\d+)?['’]|başlığı|adı|yürürlükten|\(\w+\) (?:bendi|alt bendi)|birinci|ikinci|üçüncü))",
    re.S)

AYNI_RE = re.compile(r"\bAynı (?:Yönetmeli|Tebli|Kanun|Kararname|Karar|Esas|Usul|Yönerge|Genelge|Statü)\w*\s+")

HEDEF_MADDE_RE = re.compile(r"^(?:(geçici|ek) )?(\d+) ?" + ORD + r"\b", re.I)

ISLEMLER = [
    ("yürürlükten kaldırılmıştır", "yürürlükten kaldırma"),
    ("değiştirilmiştir", "değiştirme"),
    ("eklenmiştir", "ekleme"),
    ("çıkarılmıştır", "çıkarma"),
    ("yeniden düzenlenmiştir", "değiştirme"),
    ("teselsül ettirilmiştir", "numara değişikliği"),
]

GENITIF = ("nin", "nın", "nun", "nün", "in", "ın", "un", "ün", "ne", "na", "ye", "ya", "e", "a")


@dataclass
class Degisiklik:
    kaynak_madde: str
    hedef_tarih: str
    hedef_sayi: int
    hedef_baslik: str
    hedef_madde: str
    islem: str
    cumle: str
    yeni_metin: str
    karmasik: bool


def maddeler(metin):
    """[(etiket, no, metin)] — etiket 'MADDE', 'GEÇİCİ MADDE', 'EK MADDE'."""
    out = []
    ms = list(MADDE_RE.finditer(metin))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(metin)
        out.append((m.group(1), m.group(2), metin[m.start():end].strip()))
    return out


def madde_anahtar(etiket, no):
    pre = etiket.replace("MADDE", "").strip()
    return f"{pre} {no}".strip()


def baslik_yalin(g):
    """'Organize ... Piyasası Yönetmeliğinin' -> '... Yönetmeliği' (iyelik/yönelme ekini atar)."""
    g = re.sub(r"\s+", " ", g).strip().strip("“”\"")
    words = g.split(" ")
    last = words[-1]
    for suf in GENITIF:
        if last.endswith(suf) and len(last) - len(suf) >= 3:
            stem = last[: -len(suf)]
            # 'Yönetmeliğinin' -> 'Yönetmeliği' ; 'Tebliğin' -> 'Tebliğ'
            if suf in ("nin", "nın", "nun", "nün", "ne", "na", "ye", "ya") and stem[-1] not in "aeıioöuüâî":
                continue
            words[-1] = stem
            break
    return " ".join(words)


def _islem(cumle):
    for k, v in ISLEMLER:
        if k in cumle:
            return v
    return "diğer"


def _cumle_sonu(text, start):
    """Atıftan sonraki ilk '…mıştır.' / '…miştir.' sonuna kadar (tırnak içini atlayarak)."""
    m = re.compile(r"(?:mıştır|miştir|muştur|müştür)\.").search(text, start)
    return m.end() if m else min(len(text), start + 600)


def ayristir(metin):
    """Bir düzenlemenin metninden değişiklik kayıtlarını çıkarır."""
    sonuc = []
    son_hedef = None   # 'Aynı Yönetmeliğin' için
    for etiket, no, govde in maddeler(metin):
        kaynak = madde_anahtar(etiket, no)
        refs = list(REF_RE.finditer(govde))
        pozisyonlar = [(m.start(), m.end(), m) for m in refs]
        if not refs:
            a = AYNI_RE.search(govde)
            if a and son_hedef:
                pozisyonlar = [(a.start(), a.end(), None)]
        for start, end, m in pozisyonlar:
            if m is not None:
                g, a, y, sayi = m.group(1), m.group(2), m.group(3), m.group(4)
                son_hedef = (f"{y}-{int(a):02d}-{int(g):02d}", int(sayi), baslik_yalin(m.group("g")))
            if not son_hedef:
                continue
            sonu = _cumle_sonu(govde, end)
            cumle = re.sub(r"\s+", " ", govde[start:sonu]).strip()
            kalan = govde[end:sonu]
            hm = HEDEF_MADDE_RE.match(kalan.strip())
            hedef_madde = None
            if hm:
                pre = {"geçici": "GEÇİCİ", "ek": "EK"}.get((hm.group(1) or "").lower(), "")
                hedef_madde = f"{pre} {hm.group(2)}".strip()
            # birden çok madde, "maddeleri", ya da başka atıf zinciri -> karmaşık
            karmasik = bool(re.search(r"\bmaddeleri\b", kalan)) or \
                len(re.findall(r"\d+ ?" + ORD + r" madde", kalan)) > 1 or \
                len(REF_RE.findall(kalan)) > 0
            yeni = None
            q = re.search(r"[“\"](.+?)[”\"]\s*(?:$|MADDE)", govde[sonu:] + "\n", re.S)
            if q:
                yeni = q.group(1).strip()
            sonuc.append(Degisiklik(kaynak, son_hedef[0], son_hedef[1], son_hedef[2],
                                    hedef_madde, _islem(cumle), cumle, yeni, karmasik))
    return sonuc


def _tokens(s):
    return {w for w in re.findall(r"\w+", s.lower()) if len(w) > 2}


def hedef_bul(con, d):
    """Arşivde hedef düzenlemeyi (tarih + sayı + başlık benzerliği) bul. d: degisiklikler satırı."""
    rows = con.execute("SELECT id, baslik FROM duzenlemeler WHERE tarih=? AND (sayi=? OR sayi IS NULL) "
                       "AND tur != 'ilan'", (d["hedef_tarih"], d["hedef_sayi"])).fetchall()
    if not rows:
        return None
    hedef = _tokens(d["hedef_baslik"] or "")
    best, score = None, 0.0
    for r in rows:
        t = _tokens(r["baslik"])
        s = len(hedef & t) / max(1, len(hedef))
        if s > score:
            best, score = r["id"], s
    return best if score >= 0.6 else None


def onceki_madde(con, hedef_id, hedef_madde):
    if not hedef_madde:
        return None
    row = con.execute("SELECT metin FROM duzenlemeler WHERE id=?", (hedef_id,)).fetchone()
    if not row or not row["metin"]:
        return None
    for etiket, no, govde in maddeler(row["metin"]):
        if madde_anahtar(etiket, no) == hedef_madde:
            return govde
    return None
