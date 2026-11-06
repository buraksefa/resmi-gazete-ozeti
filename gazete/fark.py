"""Önce / sonra: değişiklik cümlesini hedef maddenin önceki haline uygular.

İlke: yalnız kalıbı kesin tanınan işlemler uygulanır (madde ya da fıkra değiştirme,
madde sonuna fıkra ekleme, madde/fıkra yürürlükten kaldırma). Geri kalan her durum
"otomatik birleştirilemedi" olarak nedeniyle işaretlenir; tahmin edilmez.
"""
import difflib
import re

from .amend import ORD

UYGULANDI = "uygulandı"
ARSIVDE_YOK = "hedef arşivde yok"
BIRLESTIRILEMEDI = "otomatik birleştirilemedi"

SIRA = ["birinci", "ikinci", "üçüncü", "dördüncü", "beşinci", "altıncı", "yedinci", "sekizinci",
        "dokuzuncu", "onuncu", "on birinci", "on ikinci", "on üçüncü", "on dördüncü", "on beşinci"]
_SIRA_RE = "|".join(sorted(SIRA, key=len, reverse=True))

_FIKRA_RE = re.compile(r"(?m)^\((\d+)\)\s")          # satır başında "(3) "
_MADDE_BAS_RE = re.compile(r"^((?:GEÇİCİ |EK )?MADDE\s+\d+\s*[-–—]\s*)")


def fikralar(madde):
    """Madde metni → (giriş, [(no, metin)]). Fıkralar satır başında "(1) " ile başlar; ilk fıkra
    "MADDE 5- (1) ..." biçiminde madde başlığıyla aynı satırda olabilir."""
    m = _MADDE_BAS_RE.match(madde)
    bas = m.group(1).rstrip() if m else ""
    parcalar = _FIKRA_RE.split("\n" + madde[len(m.group(1)) if m else 0:].lstrip())
    giris = " ".join(x for x in (bas, parcalar[0].strip()) if x)
    return giris, [(int(parcalar[i]), parcalar[i + 1].strip()) for i in range(1, len(parcalar) - 1, 2)]


def _birlestir(giris, fs):
    satirlar = [f"({n}) {t}" for n, t in fs]
    if not satirlar:
        return giris
    if _MADDE_BAS_RE.match(giris + " ") and giris.rstrip()[-1:] in "-–—":
        return giris + " " + "\n".join(satirlar)   # "MADDE 5- (1) ..."
    return "\n".join([giris] + satirlar) if giris else "\n".join(satirlar)


def _fikra_no(cumle):
    m = re.search(r"\b(" + _SIRA_RE + r") fıkras", cumle)
    return SIRA.index(m.group(1)) + 1 if m else None


def uygula(d):
    """d: degisiklikler satırı (dict benzeri). Dönüş: (birlestirme, sonraki_madde, neden)."""
    if not d["hedef_id"]:
        return ARSIVDE_YOK, None, "değiştirilen düzenleme arşivimizde değil"
    if d["karmasik"]:
        return BIRLESTIRILEMEDI, None, "birden çok madde ya da atıf içeriyor"
    if not d["hedef_madde"]:
        return BIRLESTIRILEMEDI, None, "değiştirilen madde belirtilmemiş (başlık, ek, cetvel vb.)"
    if d["onceki_madde"] is None:
        return BIRLESTIRILEMEDI, None, "hedef madde arşivdeki metinde bulunamadı"
    if d["ara_degisiklik"]:
        return BIRLESTIRILEMEDI, None, f"arada {d['ara_degisiklik']} başka değişiklik var; sıra kesin değil"
    cumle, yeni, onceki = d["cumle"], d["yeni_metin"], d["onceki_madde"]
    # bent, alt bent, cümle, ibare düzeyindeki değişiklikler uygulanmaz
    if re.search(r"\b(bendi|bentleri|alt bendi|cümlesi|cümleleri|ibaresi|ibareleri|tanımı|paragrafı)\b", cumle):
        return BIRLESTIRILEMEDI, None, "bent/cümle/ibare düzeyinde değişiklik"
    if re.search(r"teselsül|sonra gelmek üzere|önce gelmek üzere", cumle):
        return BIRLESTIRILEMEDI, None, "araya ekleme ya da yeniden numaralandırma"
    fno = _fikra_no(cumle)
    islem = d["islem"]

    if islem == "yürürlükten kaldırma":
        if fno is None and re.search(r"maddesi yürürlükten", cumle):
            return UYGULANDI, "", None
        if fno is not None:
            giris, fs = fikralar(onceki)
            if fno not in [n for n, _ in fs]:
                return BIRLESTIRILEMEDI, None, f"{fno}. fıkra önceki metinde bulunamadı"
            return UYGULANDI, _birlestir(giris, [(n, t) for n, t in fs if n != fno]), None
        return BIRLESTIRILEMEDI, None, "kaldırılan kısım tanınamadı"

    if not yeni:
        return BIRLESTIRILEMEDI, None, "yeni metin (tırnak içi) bulunamadı"

    if islem == "değiştirme":
        if fno is None and re.search(r"maddesi aşağıdaki şekilde değiştirilmiştir", cumle):
            if not re.match(r"(?:GEÇİCİ |EK )?MADDE\s+\d+", yeni):
                return BIRLESTIRILEMEDI, None, "yeni metin madde başlığıyla başlamıyor"
            return UYGULANDI, yeni, None
        if fno is not None and re.search(r"fıkrası aşağıdaki şekilde değiştirilmiştir", cumle):
            giris, fs = fikralar(onceki)
            m = re.match(r"\((\d+)\)\s*(.*)", yeni, re.S)
            if fno not in [n for n, _ in fs] or not m or int(m.group(1)) != fno:
                return BIRLESTIRILEMEDI, None, "fıkra numarası önceki metinle uyuşmuyor"
            return UYGULANDI, _birlestir(giris, [(n, m.group(2).strip() if n == fno else t) for n, t in fs]), None
        return BIRLESTIRILEMEDI, None, "değiştirilen kısım tanınamadı"

    if islem == "ekleme":
        if re.search(r"maddesine aşağıdaki fıkra(?:lar)? eklenmiştir", cumle):
            giris, fs = fikralar(onceki)
            yeni_fs = [(int(a), b.strip()) for a, b in re.findall(r"(?m)^\((\d+)\)\s*(.+?)(?=^\(\d+\)|\Z)",
                                                                   yeni, re.S)]
            son = fs[-1][0] if fs else 0
            if not yeni_fs or [n for n, _ in yeni_fs] != list(range(son + 1, son + 1 + len(yeni_fs))):
                return BIRLESTIRILEMEDI, None, "eklenen fıkra numarası madde sonuna uymuyor"
            return UYGULANDI, _birlestir(giris, fs + yeni_fs), None
        return BIRLESTIRILEMEDI, None, "ekleme yeri tanınamadı (yalnız madde sonuna fıkra eklemesi uygulanır)"

    return BIRLESTIRILEMEDI, None, f"işlem türü otomatik uygulanmıyor: {islem}"


def fark_metni(onceki, sonraki):
    """Satır bazlı birleşik diff (Faz 3 sitesi kelime düzeyinde boyar)."""
    return "\n".join(difflib.unified_diff((onceki or "").splitlines(), (sonraki or "").splitlines(),
                                          "önce", "sonra", lineterm="", n=99))


def tumunu_uygula(con):
    """Bağlanmış ya da bağlanamamış her değişiklik için birleştirme durumunu yazar."""
    n = 0
    for d in con.execute("SELECT d.* FROM degisiklikler d JOIN duzenlemeler k ON k.id=d.kaynak_id "
                         "WHERE COALESCE(k.metin_kaynagi,'') NOT LIKE '%ocr'").fetchall():
        durum, sonraki, neden = uygula(d)
        fark = fark_metni(d["onceki_madde"], sonraki) if durum == UYGULANDI else None
        con.execute("UPDATE degisiklikler SET birlestirme=?, birlestirme_neden=?, sonraki_madde=?, fark=? "
                    "WHERE id=?", (durum, neden, sonraki, fark, d["id"]))
        n += 1
    con.commit()
    return n
