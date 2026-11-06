"""Özet doğrulama yardımcıları (Faz 2 özetleri yayımlamadan önce çağırır)."""
import re

AYLAR = {"ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6, "temmuz": 7,
         "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12}

_TARIH_SAYISAL = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
_TARIH_YAZILI = re.compile(r"\b(\d{1,2}) ([A-Za-zÇĞİÖŞÜçğıöşü]+) (\d{4})\b")
_SAYI = re.compile(r"\d+(?:[.,]\d+)*")


def _sayi_norm(s):
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", s):  # 1.250.000 → 1250000
        s = s.replace(".", "")
    return s.lstrip("0") or "0"


def ogeler(metin):
    """Metindeki tarihler (ISO) ve tarih dışı sayılar."""
    tarihler, kalan = set(), metin
    for rx, ay in ((_TARIH_SAYISAL, lambda g: int(g)), (_TARIH_YAZILI, lambda g: AYLAR.get(g.lower()))):
        for m in rx.finditer(metin):
            a = ay(m.group(2))
            if a and 1 <= a <= 12:
                tarihler.add(f"{m.group(3)}-{a:02d}-{int(m.group(1)):02d}")
                kalan = kalan.replace(m.group(0), " ")
    return tarihler, {_sayi_norm(s) for s in _SAYI.findall(kalan)}


def sayi_denetimi(ozet, kaynak):
    """Özetteki her tarih ve sayı kaynakta birebir geçmeli. Dönüş: kaynakta bulunamayanlar."""
    o_tarih, o_sayi = ogeler(ozet)
    k_tarih, k_sayi = ogeler(kaynak)
    k_sayi |= {_sayi_norm(s) for s in _SAYI.findall(kaynak)}  # "2026 yılı" gibi tarih parçaları
    for t in k_tarih:  # özette "Ekim 2026" yazılırsa yıl kaynaktaki tarihten de gelebilir
        y, a, g = t.split("-")
        k_sayi |= {y, str(int(a)), str(int(g))}
    return sorted(o_tarih - k_tarih) + sorted(o_sayi - k_sayi)
