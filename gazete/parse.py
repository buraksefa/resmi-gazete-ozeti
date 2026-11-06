"""Fihrist sayfası ve kalem metinleri için ayrıştırıcılar."""
import html
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, parse_qs

from . import config

AYLAR = {"Ocak": 1, "Şubat": 2, "Mart": 3, "Nisan": 4, "Mayıs": 5, "Haziran": 6, "Temmuz": 7,
         "Ağustos": 8, "Eylül": 9, "Ekim": 10, "Kasım": 11, "Aralık": 12}

YAYIMLANMADI = "Resmî Gazete yayımlanmamaktadır"


@dataclass
class FihristItem:
    sira: int
    bolum: str
    alt_baslik: str
    baslik: str
    url: str


@dataclass
class Fihrist:
    tarih: str = ""            # YYYY-MM-DD (başlıktan)
    sayi: int = 0
    mukerrer: int = 0
    yayim_yok: bool = False
    items: list = field(default_factory=list)
    mukerrer_links: list = field(default_factory=list)   # [(tarih, n)]


def _clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def _baslik_tarih(text):
    m = re.search(r"(\d{1,2}) (\w+) (\d{4}) Tarihli ve (\d+) Sayılı Resmî Gazete(?: (\d+)\. Mükerrer)?", text)
    if not m:
        return None
    gun, ay, yil, sayi, muk = m.groups()
    if ay not in AYLAR:
        return None
    return f"{int(yil):04d}-{AYLAR[ay]:02d}-{int(gun):02d}", int(sayi), int(muk or 0)


def parse_fihrist(page_html):
    f = Fihrist()
    if YAYIMLANMADI in _clean(page_html[page_html.find('id="gunluk-akis"'):][:5000]):
        f.yayim_yok = True
        return f
    # Günlük sayfada span#spanGazeteTarih; mükerrer sayfasında .preview-title
    head = re.search(r'id="spanGazeteTarih">(.*?)</span>', page_html, re.S) \
        or re.search(r'class="preview-title[^"]*">(.*?)<br', page_html, re.S)
    if head:
        bt = _baslik_tarih(_clean(head.group(1)))
        if bt:
            f.tarih, f.sayi, f.mukerrer = bt
    i = page_html.find('id="html-content"')
    j = page_html.find('id="filter-content"', i)
    seg = page_html[i:j if j > 0 else None] if i >= 0 else ""
    bolum = alt = ""
    sira = 0
    tok = re.compile(r'class="card-title html-title"[^>]*>(.*?)</div>'
                     r'|class="html-subtitle"[^>]*>(.*?)</div>'
                     r'|<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    for m in tok.finditer(seg):
        if m.group(1) is not None:
            bolum, alt = _clean(m.group(1)), ""
        elif m.group(2) is not None:
            alt = _clean(m.group(2))
        else:
            href, text = html.unescape(m.group(3)), _clean(m.group(4))
            if "fihrist?" in href:
                q = parse_qs(urlparse(href).query)
                if "tarih" in q and "mukerrer" in q:
                    f.mukerrer_links.append((q["tarih"][0], int(q["mukerrer"][0])))
                continue
            sira += 1
            baslik = re.sub(r"^[–—-]+\s*", "", text).strip()
            f.items.append(FihristItem(sira, bolum, alt, baslik, urljoin(config.BASE_URL, href)))
    return f


# --- Tür ---------------------------------------------------------------------

def tur_bul(bolum, alt_baslik, baslik):
    b, a, t = bolum.upper(), alt_baslik.upper(), baslik.upper()
    if "İLÂN" in b or "İLAN" in b:
        return "ilan"
    if "YARGI" in b:
        return "yargı kararı"
    if "ATAMA" in a or "GÖREVDEN ALMA" in a:
        return "atama"
    if "VEKÂLET" in a:
        return "vekâlet"
    if "KANUN" in a and "KARARNAME" not in a:
        return "kanun"
    if "KARARNAME" in a:
        return "kararname"
    if "ANDLAŞMA" in a or "ANTLAŞMA" in a:
        return "andlaşma"
    if "YÖNETMELİK" in a:
        return "yönetmelik"
    if "TEBLİĞ" in a:
        return "tebliğ"
    if "GENELGE" in a:
        return "genelge"
    if "KARAR" in a:
        return "karar"
    if "YÖNETMELİK" in t:
        return "yönetmelik"
    if "TEBLİĞ" in t:
        return "tebliğ"
    return "diğer"


# --- Metin çıkarma ------------------------------------------------------------

class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "xml"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag == "td":
            self.out.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "xml"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        # HTML'de kaynak satır sonları boşluktur; satırı yalnızca blok etiketleri böler
        if not self._skip:
            self.out.append(re.sub(r"\s+", " ", data))


def decode_html(raw: bytes):
    m = re.search(rb'charset=["\']?([A-Za-z0-9_-]+)', raw[:4000])
    enc = m.group(1).decode() if m else None
    for e in filter(None, [enc, "utf-8", "cp1254"]):
        try:
            return raw.decode(e)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("cp1254", "replace")


def normalize_text(t):
    t = t.replace("\xa0", " ").replace("\r", "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.split("\n")]
    out, blank = [], False
    for ln in lines:
        if not ln:
            if out and not blank:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out).strip() + "\n"


def ilan_dizini(raw: bytes, sayfa_url):
    """İlan sayfası bir dizindir: her ilan ayrı PDF. (PDF URL'leri, görüntü sayısı)."""
    h = decode_html(raw)
    pdfler = []
    for m in re.finditer(r'href="([^"]+\.pdf)"', h, re.I):
        u = urljoin(sayfa_url, html.unescape(m.group(1)))
        if u not in pdfler:
            pdfler.append(u)
    gorsel = len(set(re.findall(r'src="([^"]+\.(?:jpg|jpeg|png|gif))"', h, re.I)))
    return pdfler, gorsel


def html_to_text(raw: bytes):
    p = _TextExtractor()
    p.feed(decode_html(raw))
    return normalize_text("".join(p.out))


def pdf_sayfalari(path):
    """PDF'in yazı katmanı, sayfa sayfa (pdftotext form-feed ile ayırır)."""
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext yok (poppler-utils kurulmalı)")
    r = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"],
                       capture_output=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"pdftotext hata: {r.stderr.decode('utf-8', 'replace')[:200]}")
    sayfalar = r.stdout.decode("utf-8", "replace").split("\f")
    if sayfalar and not sayfalar[-1].strip():
        sayfalar.pop()  # son form-feed'den sonraki boşluk
    return sayfalar


def pdf_to_text(path):
    return normalize_text("\n".join(pdf_sayfalari(path)))


# --- Kurum --------------------------------------------------------------------

_KURUM_RE = re.compile(r"^([A-ZÇĞİÖŞÜÂÎÛ][^\n:]{2,150}?(?:ndan|nden|dan|den|tan|ten)):\s*$", re.M)


def kurum_bul(metin, tur, baslik=""):
    """Yönetmelik/tebliğlerde başlık üstündeki 'X Kurumundan:' satırı; kurul ve
    yargı kararlarında başlıktaki 'X Kurulunun 15/10/2026 Tarihli...' kalıbı."""
    m = _KURUM_RE.search(metin[:3000])
    if m:
        k = m.group(1)
        return re.sub(r"(?:ndan|nden|dan|den|tan|ten)$", "", k).rstrip("'’ ")
    m = re.match(r"^(.+?)(?:nun|nın|nin|nün) \d{1,2}/\d{1,2}/\d{4} Tarihli", baslik)
    if m:
        return m.group(1)
    if tur in ("karar", "kararname", "atama", "vekâlet", "andlaşma"):
        return "Cumhurbaşkanlığı"
    return None
