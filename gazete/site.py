"""Statik site + RSS (Faz 3). Yayımlanan özetler: elle onaylananlar ve (OTOMATIK_YAYIN açıkken) otomatik
denetimden uyarısız geçenler ("hazir"). İkinciler "henüz incelenmedi" etiketiyle görünür; uyarılı ("kontrol"),
reddedilmiş, OCR kaynaklı ya da kaynağı OCR kontrolü bekleyen özetler yalnız elle onayla yayımlanır.

  python3.12 -m gazete.site          # data/web/site-<zaman> üretir, data/web/current'ı ona çevirir

Sayfalar: / (son günler), /gun/, /gun/<tarih>, /d/<id> (özet + tam metin + önce/sonra + kaynak),
/etiket/, /etiket/<anahtar>, /ara (+ ara.json), /hakkinda, /rss.xml, /rss/<anahtar>.xml.
Dosyalar <yol>.html olarak yazılır, bağlantılar uzantısızdır (Cloudflare ve nginx .html'i kendisi ekler).
Her sayfada: "Bu bir özettir, bağlayıcı olan Resmî Gazete'deki metindir."
"""
import difflib
import html
import json
import os
import pathlib
import re
import shutil
import sys
from datetime import datetime
from email.utils import format_datetime

from . import config, db, ozet, taslak

NOT = "Bu bir özettir, bağlayıcı olan Resmî Gazete'deki metindir."
ACIKLAMA = ("Resmî Gazete'nin her sayısı sade Türkçe: kısa özetler, değişikliklerin önce/sonra hali, "
            "konu etiketleri ve RSS. " + NOT)
e = html.escape

STATIK = pathlib.Path(__file__).resolve().parent / "static"   # pico.min.css (MIT, v2.1.1) + lisansı

# Pico CSS üstüne sitenin kendi katmanı. Renkler Pico değişkenlerinden; koyu tema Pico'nun
# prefers-color-scheme desteğiyle kendiliğinden gelir.
CSS = """
:root{--genislik:50rem}
.container{max-width:var(--genislik)}
body>header.ust{position:sticky;top:0;z-index:5;padding:0;background:var(--pico-background-color);
 border-bottom:1px solid var(--pico-muted-border-color)}
body>header.ust nav{flex-wrap:wrap;column-gap:1rem}
body>header.ust nav ul:last-child{flex-wrap:wrap}
body>header.ust nav li{padding-block:.6rem}
.marka{display:inline-flex;align-items:center;gap:.55rem;font-weight:700;--pico-color:var(--pico-h1-color);color:var(--pico-h1-color);text-decoration:none}
.marka svg{width:1.6rem;height:1.6rem;flex:none}
body>main{padding-top:1.25rem}
h1{font-size:clamp(1.45rem,4vw,1.9rem);line-height:1.25}
h2{margin-top:2.25rem;font-size:1.25rem}
hgroup>p{color:var(--pico-muted-color)}
.not{font-size:.85rem;color:var(--pico-muted-color);background:var(--pico-card-sectioning-background-color);
 border-left:3px solid var(--pico-primary);padding:.4rem .8rem;margin-bottom:1.5rem;
 border-radius:0 var(--pico-border-radius) var(--pico-border-radius) 0}
.giris{padding:.75rem 0 0}
.giris h1{font-size:clamp(1.9rem,6vw,2.7rem);line-height:1.12;margin-bottom:.7rem;letter-spacing:-.01em}
.giris .lead{font-size:1.1rem;color:var(--pico-muted-color);max-width:38rem}
.sayilar{display:grid;grid-template-columns:repeat(3,1fr);gap:.75rem;margin:1.5rem 0 1rem}
.sayilar div{background:var(--pico-card-sectioning-background-color);border-radius:var(--pico-border-radius);padding:.75rem 1rem}
.sayilar strong{display:block;font-size:1.55rem;line-height:1.1}
.sayilar span{font-size:.8rem;color:var(--pico-muted-color)}
.bolum-baslik{display:flex;justify-content:space-between;align-items:baseline;gap:.5rem 1rem;flex-wrap:wrap}
.bolum-baslik a{font-size:.88rem}
article.kalem{margin:0 0 1rem;padding:1.1rem 1.25rem}
article.kalem h3{font-size:1.05rem;line-height:1.4;margin:.4rem 0 .5rem}
article.kalem h3 a{--pico-color:var(--pico-h3-color);color:var(--pico-h3-color);text-decoration:none}
article.kalem h3 a:hover{color:var(--pico-primary);text-decoration:underline}
article.kalem>:last-child{margin-bottom:0}
article.soluk{background:transparent;box-shadow:none;border:1px dashed var(--pico-muted-border-color)}
.ust-bilgi{display:flex;flex-wrap:wrap;gap:.3rem .6rem;align-items:center;font-size:.8rem;color:var(--pico-muted-color)}
.ust-bilgi a{color:inherit}
.rozet{display:inline-block;font-size:.68rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
 padding:.12rem .55rem;border-radius:99px;color:var(--r,#64748b);background:color-mix(in srgb,var(--r,#64748b) 13%,transparent)}
.r-yonetmelik{--r:#2563eb}.r-teblig{--r:#0f766e}.r-karar{--r:#7c3aed}.r-kararname{--r:#c2410c}
.r-atama,.r-vekalet{--r:#b45309}.r-yargi-karari{--r:#be123c}.r-andlasma{--r:#0369a1}
@media (prefers-color-scheme:dark){.rozet{color:color-mix(in srgb,var(--r,#64748b) 50%,#fff)}}
.ozet{margin-bottom:.6rem}
.oto{margin:-.2rem 0 .5rem;color:var(--pico-muted-color)}
.oto small::before{content:"";display:inline-block;width:.5rem;height:.5rem;border-radius:50%;background:#f59e0b;
 margin-right:.4rem;vertical-align:.05rem}
.etiketler{display:flex;flex-wrap:wrap;gap:.35rem;margin:.3rem 0 0}
a.etiket{font-size:.78rem;padding:.12rem .6rem;border-radius:99px;border:1px solid var(--pico-muted-border-color);
 color:var(--pico-muted-color);text-decoration:none}
a.etiket:hover{border-color:var(--pico-primary);color:var(--pico-primary)}
.meta{color:var(--pico-muted-color);font-size:.9rem}
.bekliyor{color:var(--pico-muted-color);font-style:italic;margin:0}
.uyari{background:color-mix(in srgb,#f59e0b 14%,transparent);border-left:3px solid #f59e0b;padding:.6rem .9rem;
 border-radius:0 var(--pico-border-radius) var(--pico-border-radius) 0;font-size:.93rem}
.ozet-kart{border-top:3px solid var(--pico-primary)}
.ozet-kart .ozet{font-size:1.08rem}
.ozet-kart>footer{font-size:.82rem;color:var(--pico-muted-color)}
.dugmeler{display:flex;flex-wrap:wrap;gap:.6rem;margin:0 0 1rem}
.dugmeler [role=button]{font-size:.9rem;padding:.45rem .95rem}
pre.metin{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:.93rem;line-height:1.65;
 padding:1.1rem;margin:.75rem 0 0;color:var(--pico-color)}
.fark{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--pico-card-sectioning-background-color);
 padding:1rem;border-radius:var(--pico-border-radius);font-size:.95rem;line-height:1.7;margin-bottom:.75rem}
.fark del{background:color-mix(in srgb,#dc2626 18%,transparent);color:inherit}
.fark ins{background:color-mix(in srgb,#16a34a 22%,transparent);color:inherit;text-decoration:none}
.alinti{border-left:3px solid var(--pico-muted-border-color);padding-left:.8rem;color:var(--pico-muted-color);font-size:.93rem}
.gezinti{display:flex;justify-content:space-between;gap:1rem;margin-top:2rem}
.gezinti [role=button]{font-size:.9rem;padding:.45rem .95rem}
ul.izgara{list-style:none;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(14rem,1fr));gap:.6rem}
ul.izgara li{list-style:none;margin:0}
.kart-baglanti{display:flex;flex-direction:column;gap:.15rem;height:100%;padding:.7rem .95rem;border:1px solid var(--pico-muted-border-color);
 border-radius:var(--pico-border-radius);text-decoration:none;--pico-color:var(--pico-h3-color);color:var(--pico-h3-color)}
.kart-baglanti:hover{border-color:var(--pico-primary)}
.kart-baglanti small{color:var(--pico-muted-color);font-size:.8rem;line-height:1.4}
.kart-baglanti .adet{color:var(--pico-primary);font-weight:600}
form[role=search]{margin:1.25rem 0 0}
body>footer.alt{border-top:1px solid var(--pico-muted-border-color);margin-top:3rem;padding:1.5rem 0 2.5rem;
 font-size:.85rem;color:var(--pico-muted-color)}
body>footer.alt p{margin-bottom:.5rem}
table{font-size:.92rem}
@media (max-width:575px){body>header.ust{position:static}.sayilar strong{font-size:1.25rem}article.kalem{padding:1rem}
 body>header.ust nav ul:last-child li{padding-inline:.45rem}}
"""

LOGO = ('<svg viewBox="0 0 32 32" aria-hidden="true"><rect x="5" y="3" width="22" height="26" rx="4" fill="#2060df"/>'
        '<path d="M10 10h12M10 15h12M10 20h7" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/></svg>')
FAVICON = f'<svg xmlns="http://www.w3.org/2000/svg" {LOGO[5:]}'

ARA_JS = """
const q=document.getElementById('q'),out=document.getElementById('sonuc');let veri=[];
const k=s=>s.replace(/I/g,'ı').replace(/İ/g,'i').toLocaleLowerCase('tr');
const ilk=new URLSearchParams(location.search).get('q');if(ilk)q.value=ilk;
const KOK=document.documentElement.dataset.kok||'';
fetch(KOK+'/ara.json').then(r=>r.json()).then(j=>{veri=j;ara()});
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function ara(){const t=k(q.value.trim()).split(/\\s+/).filter(Boolean);out.textContent='';
 history.replaceState(null,'',q.value.trim()?'?q='+encodeURIComponent(q.value.trim()):location.pathname);
 if(!t.length)return;const bul=veri.filter(d=>t.every(w=>d.k.includes(w))).slice(0,100);
 out.append(el('p','meta',bul.length?bul.length+' sonuç':'Sonuç yok. Daha kısa ya da farklı bir sözcük deneyin.'));
 for(const d of bul){const a=el('article','kalem'),u=el('div','ust-bilgi'),h=el('h3'),l=el('a',null,d.b);
  u.append(el('span','rozet r-'+d.c,d.y),el('span',null,d.g+(d.r?' · '+d.r:'')));
  l.href=KOK+d.u;h.append(l);a.append(u,h,el('p','ozet',d.o));out.append(a)}}
q.addEventListener('input',ara);
"""

AYLAR = ozet.AYLAR
_ASCII = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")


def tarih_tr(iso):
    return ozet.tarih_tr(iso)


def slug(metin):
    return re.sub(r"[^a-z0-9]+", "-", (metin or "").translate(_ASCII).lower()).strip("-")


def sayfa_yolu(did):
    kind, stem = did.split("/")
    return f"/d/{'ilan-' if kind == 'ilanlar' else ''}{stem}"


def url_yolu(dosya):
    """Dosya adından kanonik adres: index.html → /, etiket/index.html → /etiket/, d/x.html → /d/x."""
    if dosya.endswith("index.html"):
        return "/" + dosya[: -len("index.html")]
    return "/" + dosya[: -len(".html")]


def kok_ekle(sayfa):
    """Site alt klasördeyse (config.SITE_KOK) sayfa içi kök bağlantılarının başına onu ekler."""
    if not config.SITE_KOK:
        return sayfa
    return re.sub(r'((?:href|src|action)=")/(?!/)', rf"\1{config.SITE_KOK}/", sayfa)


def sablon(baslik, govde, aciklama=""):
    return f"""<!doctype html>
<html lang="tr" data-kok="{e(config.SITE_KOK)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(baslik if baslik == "Gazete Özeti" else baslik + " — Gazete Özeti")}</title><meta name="description" content="{e(aciklama or NOT)}">
<link rel="canonical" href="{e(config.SITE_URL)}__YOL__"><meta property="og:url" content="{e(config.SITE_URL)}__YOL__">
<meta property="og:type" content="website"><meta property="og:site_name" content="Gazete Özeti">
<meta property="og:title" content="{e(baslik)}"><meta property="og:description" content="{e(aciklama or ACIKLAMA)}">
<meta property="og:locale" content="tr_TR"><meta name="twitter:card" content="summary">
<meta name="color-scheme" content="light dark"><meta name="theme-color" content="#2060df">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/pico.min.css"><link rel="stylesheet" href="/stil.css">
<link rel="alternate" type="application/rss+xml" title="Gazete Özeti" href="/rss.xml">
</head><body>
<header class="ust"><nav class="container">
<ul><li><a class="marka" href="/">{LOGO}<span>Gazete Özeti</span></a></li></ul>
<ul><li><a href="/gun/">Günler</a></li><li><a href="/etiket/">Konular</a></li><li><a href="/ara">Ara</a></li>
<li><a href="/hakkinda">Hakkında</a></li></ul>
</nav></header>
<main class="container">
<p class="not" role="note">{e(NOT)}</p>
{govde}
</main>
<footer class="container alt"><p><strong>{e(NOT)}</strong></p>
<p>Özetler yerelde çalışan bir dil modeliyle üretilir ve otomatik denetimden geçer. Bir kişi tarafından henüz
incelenmemiş özetler “otomatik özet” etiketiyle belirtilir; denetimde uyarı verenler incelenene kadar yayımlanmaz.
Atamalar yalnız olgu olarak (ad, kurum, görev) verilir. Bağımsız bir projedir; Resmî Gazete ile resmî bir bağı yoktur.</p>
<p>Kaynak: <a href="https://www.resmigazete.gov.tr">resmigazete.gov.tr</a> · <a href="/rss.xml">RSS</a> ·
<a href="/hakkinda">Yöntem</a></p></footer></body></html>
"""


def kelime_farki(once, sonra):
    a, b = re.findall(r"\S+|\s+", once or ""), re.findall(r"\S+|\s+", sonra or "")
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("delete", "replace"):
            out.append(f"<del>{e(''.join(a[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins>{e(''.join(b[j1:j2]))}</ins>")
        if op == "equal":
            out.append(e("".join(a[i1:i2])))
    return "".join(out)


class Veri:
    def __init__(self, con, etiketler):
        self.con = con
        self.etiketler = etiketler
        self.duz = {r["id"]: r for r in con.execute("SELECT * FROM duzenlemeler ORDER BY tarih, mukerrer, sira")}
        kosul = "o.onay = 'onaylandi'"
        if config.OTOMATIK_YAYIN:
            # uyarısız özet onay beklemeden yayımlanır; OCR metnine dayananlar hariç (rakam okuma hatası riski)
            kosul += (" OR (o.onay IS NULL AND o.durum = 'hazir' AND COALESCE(d.kontrol_gerekli, 0) = 0"
                      " AND COALESCE(d.metin_kaynagi, '') NOT LIKE '%ocr')")
        self.ozet = {r["duzenleme_id"]: r for r in con.execute(
            f"SELECT o.* FROM ozetler o JOIN duzenlemeler d ON d.id = o.duzenleme_id WHERE {kosul}")}
        self.etk = {}
        for r in con.execute("SELECT * FROM etiketler"):
            if r["etiket"] in etiketler:
                self.etk.setdefault(r["duzenleme_id"], []).append(r["etiket"])
        self.sayilar = {}
        for r in con.execute("SELECT * FROM sayilar WHERE durum != 'yok' ORDER BY tarih, mukerrer"):
            self.sayilar.setdefault(r["tarih"], []).append(r)
        self.gunler = sorted({d["tarih"] for d in self.duz.values()}, reverse=True)

    def onayli(self):
        """Yayımlanan kalemler (onaylı ya da otomatik yayımlanan özeti olanlar), yeniden eskiye."""
        return sorted((self.duz[i] for i in self.ozet if i in self.duz),
                      key=lambda d: (d["tarih"], -d["mukerrer"], -d["sira"]), reverse=True)

    def gun_ozet_sayisi(self, t):
        return sum(1 for d in self.duz.values() if d["tarih"] == t and d["id"] in self.ozet)

    def asil_sayi(self, t):
        return next((x["sayi"] for x in self.sayilar.get(t, []) if x["mukerrer"] == 0), None)


def rozet(tur):
    return f'<span class="rozet r-{slug(tur)}">{e(tur)}</span>'


def etiket_html(v, did):
    if did not in v.ozet:   # etiketler de (kısmen model önerisi) özet yayımlanınca görünür
        return ""
    etk = "".join(f'<a class="etiket" href="/etiket/{e(t)}">{e(v.etiketler[t]["ad"])}</a>'
                  for t in v.etk.get(did, []))
    return f'<p class="etiketler">{etk}</p>' if etk else ""


def incelendi(o):
    return o["onay"] == "onaylandi"


OTO_ETIKET = '<p class="oto"><small>Otomatik özet · henüz bir kişi tarafından incelenmedi</small></p>'


def ozet_html(v, d, tam=False):
    o = v.ozet.get(d["id"])
    if d["tur"] == "ilan":
        return '<p class="meta">İlan — yalnız dizin; metin kaynakta.</p>'
    if not o:
        return '<p class="bekliyor">Özet henüz onaylanmadı.</p>'
    if o["olgular"]:
        olg = json.loads(o["olgular"])
        satirlar = "".join(f"<tr><td>{e(x['ad'])}</td><td>{e(x['gorev'])}</td><td>{e(x['islem'])}</td></tr>"
                           for x in (olg if tam else olg[:5]))
        devam = "" if tam or len(olg) <= 5 else f'<p class="meta">… ve {len(olg) - 5} kişi daha.</p>'
        return (f'<p class="ozet">{e(o["ozet"])}</p><div class="overflow-auto"><table class="striped"><thead><tr>'
                f'<th>Ad</th><th>Kurum ve görev</th><th>İşlem</th></tr></thead><tbody>{satirlar}</tbody></table></div>'
                f'{devam}{"" if incelendi(o) else OTO_ETIKET}')
    return f'<p class="ozet">{e(o["ozet"])}</p>{"" if incelendi(o) else OTO_ETIKET}'


def kalem_html(v, d, tarih=False):
    ust = [rozet(d["tur"])]
    if tarih:
        ust.append(f'<a href="/gun/{d["tarih"]}">{e(tarih_tr(d["tarih"]))}</a>')
    if d["kurum"]:
        ust.append(f'<span>{e(d["kurum"])}</span>')
    sinif = "kalem" if d["id"] in v.ozet else "kalem soluk"
    return (f'<article class="{sinif}"><div class="ust-bilgi">{"".join(ust)}</div>'
            f'<h3><a href="{sayfa_yolu(d["id"])}">{e(d["baslik"])}</a></h3>'
            f'{ozet_html(v, d)}{etiket_html(v, d["id"])}</article>')


def gun_basligi(v, t):
    s = v.sayilar.get(t, [])
    asil = v.asil_sayi(t)
    muk = [x["mukerrer"] for x in s if x["mukerrer"]]
    return (f"{tarih_tr(t)} — Resmî Gazete" + (f" sayı {asil}" if asil else "")
            + (f" (+{', '.join(f'{m}. mükerrer' for m in muk)})" if muk else ""))


def gun_alt_baslik(v, t):
    s = v.sayilar.get(t, [])
    asil = v.asil_sayi(t)
    muk = [x["mukerrer"] for x in s if x["mukerrer"]]
    kalem = sum(1 for d in v.duz.values() if d["tarih"] == t and d["tur"] != "ilan")
    parca = [f"Resmî Gazete sayı {asil}" if asil else "Resmî Gazete"]
    if muk:
        parca.append(", ".join(f"{m}. mükerrer" for m in muk))
    parca.append(f"{kalem} düzenleme, {v.gun_ozet_sayisi(t)} özet")
    return " · ".join(parca)


def gun_karti(v, t):
    return (f'<li><a class="kart-baglanti" href="/gun/{t}"><strong>{e(tarih_tr(t))}</strong>'
            f'<small>{e(gun_alt_baslik(v, t))}</small></a></li>')


def gun_sayfasi(v, t, onceki, sonraki):
    kalemler = [d for d in v.duz.values() if d["tarih"] == t]
    parca, son_grup = [f"<hgroup><h1>{e(tarih_tr(t))}</h1><p>{e(gun_alt_baslik(v, t))}</p></hgroup>"], None
    for d in kalemler:
        if d["tur"] == "ilan":
            continue
        grup = (d["mukerrer"], d["bolum"], d["alt_baslik"])
        if grup != son_grup:
            parca.append(f"<h2>{e(((str(d['mukerrer']) + '. mükerrer — ') if d['mukerrer'] else '') + (d['alt_baslik'] or d['bolum'] or ''))}</h2>")
            son_grup = grup
        parca.append(kalem_html(v, d))
    ilanlar = [d for d in kalemler if d["tur"] == "ilan"]
    if ilanlar:
        parca.append(f"<h2>İlanlar</h2><details><summary>{len(ilanlar)} ilan (yalnız dizin, metin kaynakta)</summary><ul>"
                     + "".join(f'<li><a href="{e(d["kaynak_url"])}">{e(d["baslik"])}</a></li>' for d in ilanlar)
                     + "</ul></details>")
    sol = (f'<a role="button" class="outline secondary" href="/gun/{onceki}">← {e(tarih_tr(onceki))}</a>'
           if onceki else "<span></span>")
    sag = (f'<a role="button" class="outline secondary" href="/gun/{sonraki}">{e(tarih_tr(sonraki))} →</a>'
           if sonraki else "<span></span>")
    parca.append(f'<nav class="gezinti" aria-label="Günler arasında gezinti">{sol}{sag}</nav>')
    return sablon(gun_basligi(v, t), "\n".join(parca), f"{tarih_tr(t)} tarihli Resmî Gazete'deki düzenlemelerin kısa özetleri. {NOT}")


def degisiklik_html(v, d):
    out = []
    kaynaklar = v.con.execute("SELECT * FROM degisiklikler WHERE kaynak_id=? ORDER BY id", (d["id"],)).fetchall()
    if kaynaklar:
        out.append("<h2>Değiştirdiği düzenlemeler (önce / sonra)</h2>")
        for r in kaynaklar:
            hedef = f"{tarih_tr(r['hedef_tarih'])} tarihli ve {r['hedef_sayi']} sayılı RG — {r['hedef_baslik']}"
            if r["hedef_id"] in v.duz:
                hedef = f'<a href="{sayfa_yolu(r["hedef_id"])}">{e(hedef)}</a>'
            else:
                hedef = e(hedef)
            out.append(f'<article class="kalem"><div class="ust-bilgi"><span>Madde {e(r["kaynak_madde"] or "?")} → '
                       f'{hedef}{" · madde " + e(r["hedef_madde"]) if r["hedef_madde"] else ""}</span>'
                       f'<span class="rozet">{e(r["islem"])}</span></div>'
                       f'<p class="alinti">“{e(r["cumle"])}”</p>')
            if r["birlestirme"] == "uygulandı":
                out.append(f'<div class="fark">{kelime_farki(r["onceki_madde"], r["sonraki_madde"])}</div>'
                           '<p class="meta"><small>Önceki hal, değiştirilen düzenlemenin arşivimizdeki ilk metninden '
                           'alınmıştır.</small></p>')
            elif r["birlestirme"] == "otomatik birleştirilemedi":
                out.append(f'<p class="uyari"><strong>Otomatik birleştirilemedi</strong> — '
                           f'{e(r["birlestirme_neden"] or "")}. Önce/sonra gösterilmiyor; tahmin edilmez.</p>')
            else:
                out.append('<p class="meta"><small>Değiştirilen düzenleme arşivimizde yok; önce/sonra '
                           'gösterilemiyor.</small></p>')
            out.append("</article>")
    hedefler = v.con.execute("SELECT * FROM degisiklikler WHERE hedef_id=? ORDER BY id", (d["id"],)).fetchall()
    if hedefler:
        out.append("<h2>Bu düzenlemeyi değiştirenler</h2><ul>")
        for r in hedefler:
            k = v.duz.get(r["kaynak_id"])
            if k:
                out.append(f'<li><a href="{sayfa_yolu(k["id"])}">{e(k["baslik"])}</a> '
                           f'<small class="meta">({tarih_tr(k["tarih"])}, madde {e(r["hedef_madde"] or "?")})</small></li>')
        out.append("</ul>")
    return "\n".join(out)


def duzenleme_sayfasi(v, d):
    meta = [f"Resmî Gazete sayı {d['sayi']}" + (f" ({d['mukerrer']}. mükerrer)" if d["mukerrer"] else ""),
            d["kurum"] or ""]
    o = v.ozet.get(d["id"])
    onay = ""
    if o and incelendi(o):
        onay = (f'<footer>Özet {"elle düzeltildi ve " if o["elle_duzeltildi"] else ""}'
                f'{tarih_tr(o["onay_zamani"][:10])} tarihinde bir kişi tarafından incelenip onaylandı.</footer>')
    elif o:
        ne_zaman = f'{tarih_tr(o["olusturma"][:10])} tarihinde ' if o["olusturma"] else ""
        onay = (f'<footer>Özet {ne_zaman}otomatik üretildi ve otomatik denetimlerden uyarısız geçti; '
                'henüz bir kişi tarafından incelenmedi.</footer>')
    parca = [f'<nav aria-label="breadcrumb"><ul><li><a href="/">Ana sayfa</a></li>'
             f'<li><a href="/gun/{d["tarih"]}">{e(tarih_tr(d["tarih"]))}</a></li><li>{e(d["tur"])}</li></ul></nav>',
             f'<hgroup><h1>{e(d["baslik"])}</h1><p>{e(" · ".join(x for x in meta if x))}</p></hgroup>',
             f'<article class="ozet-kart"><header><div class="ust-bilgi">{rozet(d["tur"])}<span>Özet</span></div></header>'
             f'{ozet_html(v, d, tam=True)}{etiket_html(v, d["id"])}{onay}</article>',
             f'<div class="dugmeler"><a role="button" href="{e(d["kaynak_url"])}" rel="noopener">'
             f'Resmî Gazete\'deki metin (kaynak) ↗</a>'
             f'<a role="button" class="outline secondary" href="/gun/{d["tarih"]}">Günün listesi</a></div>']
    parca.append(degisiklik_html(v, d))
    if d["metin"] and d["kontrol_gerekli"]:
        parca += ["<h2>Tam metin</h2>", '<p class="uyari">Bu metin taranmış görüntüden otomatik okundu ve '
                  'okumada şüpheli yerler var; kontrol edilene kadar burada gösterilmiyor. '
                  f'<a href="{e(d["kaynak_url"])}">Kaynaktaki metne bakın.</a></p>']
    elif d["metin"]:
        ocr = ""
        if d["ocr_durum"] == "tamam":
            ocr = ('<p class="uyari">Bu metin taranmış görüntüden otomatik okunmuştur (OCR); rakam ve adlarda '
                   'okuma hatası olabilir. Bağlayıcı olan kaynaktaki metindir.</p>')
        elif d["ocr_gerekli"]:
            ocr = '<p class="uyari">Kaynak PDF taranmış görüntüdür; aşağıda yalnız yazı katmanı var.</p>'
        acik = " open" if len(d["metin"]) <= 4000 else ""
        sozcuk = f"{len(d['metin'].split()):,}".replace(",", ".")
        metin = re.sub(r"\n{2,}", "\n", d["metin"])   # pdftotext satır arası boşlukları ekranda gereksiz
        parca += ["<h2>Tam metin</h2>", ocr,
                  f'<details class="tam-metin"{acik}><summary>Metni göster ({sozcuk} sözcük)</summary>'
                  f'<pre class="metin">{e(metin)}</pre></details>']
    return sablon(d["baslik"], "\n".join(parca), (o["ozet"] if o else "")[:300])


def ana_sayfa(v):
    onayli = v.onayli()
    son = onayli[:20]
    parca = ['<section class="giris"><h1>Resmî Gazete, sade Türkçe</h1>'
             '<p class="lead">Her sayıdaki düzenlemelerin kısa ve tarafsız özeti, değişikliklerin önce/sonra hali, '
             'konu etiketleri ve RSS. İncelenmemiş özetler açıkça belirtilir.</p>'
             '<form role="search" action="/ara"><input type="search" name="q" '
             'placeholder="Başlık, kurum ya da konu ara" aria-label="Özetlerde ara"><input type="submit" value="Ara">'
             '</form>'
             f'<div class="sayilar"><div><strong>{len(onayli)}</strong><span>özet</span></div>'
             f'<div><strong>{len(v.gunler)}</strong><span>gün arşivde</span></div>'
             f'<div><strong>{len(v.etiketler)}</strong><span>konu</span></div></div></section>']
    # Son iki sayı tam liste: özeti yayımlanmamış kalemde yalnız başlık görünür.
    for gun in v.gunler[:2]:
        kalemler = [d for d in v.duz.values() if d["tarih"] == gun and d["tur"] != "ilan"]
        parca.append(f'<div class="bolum-baslik"><h2>{e(tarih_tr(gun))}</h2>'
                     f'<a href="/gun/{gun}">Günün listesi →</a></div>'
                     f'<p class="meta">{e(gun_alt_baslik(v, gun))}</p>')
        parca += [kalem_html(v, d) for d in kalemler]
    eski = [d for d in son if d["tarih"] not in v.gunler[:2]][:10]
    if eski:
        parca.append('<h2>Önceki günlerden özetler</h2>')
        parca += [kalem_html(v, d, tarih=True) for d in eski]
    sayac = {t: sum(1 for d in onayli if t in v.etk.get(d["id"], [])) for t in v.etiketler}
    konular = "".join(f'<a class="etiket" href="/etiket/{e(t)}">{e(x["ad"])} · {sayac[t]}</a>'
                      for t, x in sorted(v.etiketler.items(), key=lambda kv: -sayac[kv[0]]) if sayac[t])
    if konular:
        parca.append(f'<div class="bolum-baslik"><h2>Konular</h2><a href="/etiket/">Tümü →</a></div>'
                     f'<p class="etiketler">{konular}</p>')
    parca.append('<div class="bolum-baslik"><h2>Son günler</h2><a href="/gun/">Arşiv →</a></div>'
                 f'<ul class="izgara">{"".join(gun_karti(v, t) for t in v.gunler[:6])}</ul>')
    return sablon("Gazete Özeti", "\n".join(parca))


def gunler_sayfasi(v):
    return sablon("Günler", '<hgroup><h1>Günler</h1><p>Arşivdeki Resmî Gazete sayıları, yeniden eskiye.</p></hgroup>'
                  f'<ul class="izgara">{"".join(gun_karti(v, t) for t in v.gunler)}</ul>')


def hakkinda_sayfasi():
    return sablon("Hakkında ve yöntem", f"""<hgroup><h1>Hakkında ve yöntem</h1>
<p>Gazete Özeti, Resmî Gazete'de yayımlanan düzenlemeleri kısa ve sade Türkçeyle özetleyen bağımsız bir projedir.</p></hgroup>
<h2>Kaynak</h2>
<p>Tek kaynak <a href="https://www.resmigazete.gov.tr">resmigazete.gov.tr</a>'dir. Her gün yayımlanan sayı, sunucuyu
yormamak için günde birkaç istekle alınır. Her sayfada Resmî Gazete'deki asıl metne bağlantı vardır.</p>
<h2>Özetler nasıl hazırlanır?</h2>
<ol><li>Düzenlemenin metni PDF ya da HTML'den çıkarılır. Taranmış (görüntü) sayfalar OCR ile okunur ve sitede
bu durum belirtilir; okumada şüpheli yer varsa metin kontrol edilene kadar gösterilmez.</li>
<li>Özet, yerelde çalışan açık ağırlıklı bir dil modeliyle yalnız düzenlemenin metnine dayanarak üretilir.
Yürürlük tarihi modelden değil, metindeki yürürlük maddesinden alınır.</li>
<li>Otomatik denetimler özetteki her tarih, sayı ve özel adın kaynakta geçip geçmediğine, yorum ya da
değerlendirme ifadesi bulunup bulunmadığına bakar.</li>
<li>Denetimlerden uyarısız geçen özet yayımlanır ve bir kişi okuyup onaylayana kadar
<strong>“otomatik özet · henüz incelenmedi”</strong> etiketini taşır. Denetimde uyarı veren, taranmış görüntüden
okunan ya da reddedilen özet, bir kişi inceleyip onaylamadan yayımlanmaz.</li></ol>
<h2>Atamalar</h2>
<p>Atama ve görevden alma kararları yalnız olgu olarak listelenir: ad, kurum ve görev. Yorum yapılmaz.</p>
<h2>Önce / sonra</h2>
<p>Bir düzenleme başka bir düzenlemeyi değiştiriyorsa ve değiştirilen metin arşivimizde varsa, değişiklik
otomatik uygulanıp sözcük düzeyinde gösterilir. Değişiklik belirsizse ya da otomatik uygulanamayacak türdeyse
“otomatik birleştirilemedi” yazılır; tahmin yapılmaz.</p>
<h2>Sınırlar</h2>
<p>{e(NOT)} Özetler kısaltma içerir ve hata barındırabilir; hukuki bir işlem için daima asıl metne bakın.
OCR ile okunan metinlerde rakam ve adlarda okuma hatası olabilir.</p>
<h2>Takip</h2>
<p><a href="/rss.xml">RSS</a> ile tüm özetleri, <a href="/etiket/">konu sayfalarındaki</a> RSS bağlantılarıyla
yalnız ilgilendiğiniz konuları izleyebilirsiniz.</p>""", "Gazete Özeti'nin kaynağı, özetleme yöntemi ve sınırları.")


def etiket_sayfalari(v):
    sayac = {t: [d for d in v.onayli() if t in v.etk.get(d["id"], [])] for t in v.etiketler}
    liste = "".join(f'<li><a class="kart-baglanti" href="/etiket/{e(t)}"><strong>{e(x["ad"])}</strong>'
                    f'<small>{e(x.get("aciklama", ""))}</small><small class="adet">{len(sayac[t])} özet</small></a></li>'
                    for t, x in sorted(v.etiketler.items(), key=lambda kv: -len(sayac[kv[0]])))
    out = {"etiket/index.html": sablon("Konular", '<hgroup><h1>Konular</h1><p>Her konunun kendi sayfası ve RSS '
                                       f'beslemesi vardır.</p></hgroup><ul class="izgara">{liste}</ul>')}
    for t, x in v.etiketler.items():
        govde = (f'<hgroup><h1>{e(x["ad"])}</h1><p>{e(x.get("aciklama", ""))}</p></hgroup>'
                 f'<div class="dugmeler"><a role="button" class="outline secondary" href="/rss/{e(t)}.xml">'
                 f'Bu konunun RSS beslemesi</a></div>'
                 + ("".join(kalem_html(v, d, tarih=True) for d in sayac[t])
                    or '<p class="bekliyor">Bu konuda yayımlanmış özet yok.</p>'))
        out[f"etiket/{t}.html"] = sablon(x["ad"], govde, f"{x['ad']} konusundaki Resmî Gazete düzenlemelerinin özetleri.")
    return out


def rss(v, baslik, kalemler):
    ogeler = []
    for d in kalemler[:50]:
        o = v.ozet[d["id"]]
        z = o["onay_zamani"] or o["olusturma"]
        zaman = datetime.fromisoformat(z) if z else datetime.now(config.TZ)
        oto = "" if incelendi(o) else "[Otomatik özet, henüz incelenmedi] "
        ogeler.append(
            f"<item><title>{e(d['baslik'])}</title><link>{e(config.SITE_URL + sayfa_yolu(d['id']))}</link>"
            f"<guid isPermaLink=\"false\">{e(d['id'])}</guid><pubDate>{format_datetime(zaman)}</pubDate>"
            f"<description>{e(oto + o['ozet'] + ' — ' + NOT + ' Kaynak: ' + d['kaynak_url'])}</description></item>")
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0"><channel><title>{e(baslik)}</title>'
            f"<link>{e(config.SITE_URL)}/</link><description>{e(NOT)}</description><language>tr</language>"
            + "".join(ogeler) + "</channel></rss>\n")


def arama(v):
    out = []
    for d in v.onayli():
        o = v.ozet[d["id"]]
        etk = " ".join(v.etiketler[t]["ad"] for t in v.etk.get(d["id"], []))
        out.append({"u": sayfa_yolu(d["id"]), "b": d["baslik"], "t": d["tarih"], "g": tarih_tr(d["tarih"]),
                    "r": d["kurum"] or "", "y": d["tur"], "c": slug(d["tur"]),
                    "o": o["ozet"], "k": ozet.kucuk(" ".join([d["baslik"], d["kurum"] or "", o["ozet"], etk]))})
    return json.dumps(out, ensure_ascii=False)


def dosyalar(v):
    f = {"stil.css": CSS, "favicon.svg": FAVICON, "index.html": ana_sayfa(v), "ara.json": arama(v),
         "ara.html": sablon("Ara", '<hgroup><h1>Ara</h1><p>Onaylı özetlerde başlık, kurum, özet ve konuya göre arar.</p>'
                                   '</hgroup><form role="search" onsubmit="return false"><input id="q" type="search" '
                                   'placeholder="ör. doğal gaz, vergi, yönetmelik" aria-label="Ara" autofocus></form>'
                                   '<div id="sonuc"></div><script>' + ARA_JS + "</script>"),
         "gun/index.html": gunler_sayfasi(v), "hakkinda.html": hakkinda_sayfasi(),
         "rss.xml": rss(v, "Gazete Özeti", v.onayli())}
    for dosya in ("pico.min.css", "pico-LICENSE.md"):
        f[dosya] = (STATIK / dosya).read_text(encoding="utf-8")
    for i, t in enumerate(v.gunler):
        f[f"gun/{t}.html"] = gun_sayfasi(v, t, v.gunler[i + 1] if i + 1 < len(v.gunler) else None,
                                         v.gunler[i - 1] if i else None)
    for d in v.duz.values():
        if d["tur"] != "ilan":
            f[sayfa_yolu(d["id"]).lstrip("/") + ".html"] = duzenleme_sayfasi(v, d)
    f.update(etiket_sayfalari(v))
    for t, x in v.etiketler.items():
        f[f"rss/{t}.xml"] = rss(v, f"Gazete Özeti — {x['ad']}",
                                [d for d in v.onayli() if t in v.etk.get(d["id"], [])])
    f["404.html"] = sablon("Sayfa bulunamadı", '<h1>Sayfa bulunamadı</h1><p><a href="/">Ana sayfaya dön</a></p>')
    # kanonik adres: index.html → /, etiket/index.html → /etiket/, d/x.html → /d/x
    for yol in [y for y in f if y.endswith(".html")]:
        f[yol] = kok_ekle(f[yol].replace("__YOL__", url_yolu(yol)))
    sayfalar = sorted(url_yolu(y) for y in f if y.endswith(".html") and y != "404.html")
    f["sitemap.xml"] = ('<?xml version="1.0" encoding="utf-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                        + "".join(f"<url><loc>{e(config.SITE_URL)}{e(u)}</loc></url>" for u in sayfalar) + "</urlset>\n")
    f[".nojekyll"] = ""   # GitHub Pages dosyaları Jekyll'den geçirmeden sunar
    f["robots.txt"] = f"User-agent: *\nAllow: /\nSitemap: {config.SITE_URL}/sitemap.xml\n"
    # Cloudflare Workers statik varlık başlıkları (nginx bunu sunmaz, zararsız)
    f["_headers"] = ("/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n"
                     "  Permissions-Policy: interest-cohort=()\n/rss.xml\n  Content-Type: application/rss+xml; charset=utf-8\n"
                     "/rss/*\n  Content-Type: application/rss+xml; charset=utf-8\n")
    return f


def uret(con=None):
    con = con or db.connect(config.DB_PATH)
    v = Veri(con, ozet.etiketleri_yukle())
    web = config.WEB_DIR
    yeni = web / f"site-{datetime.now(config.TZ).strftime('%Y%m%d-%H%M%S')}"
    for yol, icerik in dosyalar(v).items():
        p = yeni / yol
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(icerik, encoding="utf-8")
    # atomik geçiş: current → yeni klasör (nginx current'ı sunar)
    gecici = web / "current.tmp"
    if gecici.is_symlink():
        gecici.unlink()
    os.symlink(yeni.name, gecici)
    os.replace(gecici, web / "current")
    for eski in sorted(web.glob("site-*"))[:-2]:
        shutil.rmtree(eski)
    taslak.uret(v)
    n_onay = sum(incelendi(o) for o in v.ozet.values())
    print(f"site: {yeni} ({len(v.ozet)} özet, {n_onay} incelenmiş, {len(v.gunler)} gün)")
    return yeni


def main(argv=None):
    uret()
    return 0


if __name__ == "__main__":
    sys.exit(main())
