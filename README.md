# Gazete Özeti

Resmî Gazete'nin her sayısını alır, arşivler, metni git'e yazar. Bağlayıcı olan
Resmî Gazete'deki metindir. Birincil kaynak yalnızca resmigazete.gov.tr.

Bağımlılık: Python ≥ 3.9 (yalnız standart kütüphane), `git`, poppler-utils (`pdftotext`,
`pdfimages`, `pdftoppm`), OCR için `tesseract` + Türkçe dil paketi (`tesseract-langpack-tur`).

## Faz 1 — toplayıcı

```
python3.12 -m gazete.collect gunluk            # bugün (+ mükerrer), yoksa çıkış 75
python3.12 -m gazete.collect tarih 2026-10-31  # belirli gün
python3.12 -m gazete.collect geriye            # son 12 ayı yavaşça doldur
python3.12 -m gazete.collect bagla             # değişiklik bağlantılarını yeniden kur
python3.12 -m gazete.collect ocr               # taranmış sayfaları OCR'la (45 dk sınırlı)
python3.12 -m gazete.collect kontrol           # insan onayı bekleyenler
python3.12 -m gazete.collect onayla eskiler/20261101-6
```

Veri (`data/`):
- `raw/YYYY/MM/DD/` — ham fihrist, `eskiler/*.htm|pdf`, `ilanlar/*.htm`
- `arsiv/` — git reposu; `YYYY/MM/DD/<id>.txt` + `fihrist.json`, her gün bir commit
- `gazete.sqlite` — `sayilar`, `duzenlemeler`, `degisiklikler`
- `log/collect.log`

Nezaket: istekler arası 5 sn, çalıştırma başına en çok 80 istek, User-Agent'ta
iletişim bilgisi (`.env` → `GAZETE_CONTACT`), robots.txt'teki Noindex dosyaları atlanır.

Zamanlama (systemd kullanıcı birimleri, `deploy/`):
- `gazete-gunluk.timer`: 00:05, sonra 30 dk arayla 02:35'e kadar; 12:00, 18:00, 23:45 mükerrer kontrolü
- `gazete-geriye.timer`: 03:30 ve 15:30, her seferinde 2 gün (günde ~4 sayı)
- `gazete-ocr.timer`: saatte bir (:50), OCR kuyruğu

## Taranmış PDF'ler (OCR)

Cumhurbaşkanı kararları, atamalar, EPDK kararları gibi belgelerde gövde görüntüdür.
Bir sayfa, yazı katmanı 400 karakterden kısaysa ve büyük bir görüntü içeriyorsa taranmış
sayılır (sayfa sayfa; çok sayfalı belgelerde başlık/imza yazısı tespiti bozmaz). Bu belgeler
`ocr_durum='bekliyor'` olur, `ocr` komutu 400 dpi + tesseract (tur) ile tek geçişte okur.

Güvenlik kuralları:
- Metin kaynağı `metin_kaynagi` (`ocr` / `pdf+ocr`) ve arşiv dosyasında not olarak yazılır.
- OCR metni değişiklik eşleştirmesine (önce/sonra diff) girmez.
- Ortalama güveni 60'ın altındaki sayfalar (harita, tablo, imza) metne alınmaz, not düşülür.
- Andlaşmalarda yalnız Türkçe metin okunur; ilk başka dil bölümünde durulur.
- `kontrol_gerekli=1`: il adına benzeyen ama il olmayan sözcük (ör. "Kurşehir"), "(0) sayılı"
  gibi şüpheli numara; atama/vekâlette ayrıca güveni 85'in altındaki büyük harfli sözcükler.
  Faz 2 bu kalemlerin özetini `onayla` olmadan yayımlamaz.
- `gazete/dogrula.py` → `sayi_denetimi(ozet, kaynak)`: özetteki her tarih/sayı kaynakta
  bulunmalı; bulunmazsa özet yayımlanmaz (Faz 2).

Değişiklik ilişkisi: "X tarihli ve Y sayılı Resmî Gazete'de yayımlanan Z … N inci maddesi"
ve "Aynı Yönetmeliğin …" kalıpları. Birden çok madde/atıf içerenler `karmasik=1` —
Faz 2'de otomatik uygulanmaz. Hedef arşivdeyse `hedef_id` ve maddenin ilk hali
(`onceki_madde`) doldurulur; aradaki bilinen değişiklik sayısı `ara_degisiklik`.

Testler: `python3 -m unittest discover -s tests`

## Faz 2 — özet, etiket, önce/sonra

```
python3.12 -m gazete.ozetle calistir          # özeti olmayanlar (yeniden eskiye, 40 dk sınırlı) + fark
python3.12 -m gazete.ozetle yeniden eskiler/20261031-2
python3.12 -m gazete.ozetle etiketle --hepsi  # etiketler.toml değişince
python3.12 -m gazete.dogrulama 20             # rastgele 20 özeti metinle karşılaştır → data/rapor/
```

- Model: yerel Ollama (`.env` → `GAZETE_OLLAMA_URL`, `GAZETE_OLLAMA_MODEL`), sıcaklık 0, JSON çıktı.
- Özet: model en fazla 2 cümle yazar (kimi etkiliyor, ne değişiyor). Yürürlük cümlesi modelden değil,
  metindeki "yürürlüğe girer" hükmünden kuralla eklenir; koşullu/çoklu yürürlükte hüküm aynen alıntılanır.
- Uzun metin: ilk iki madde, diğer maddelerin başı, son iki madde (yargı kararlarında baş + son).
- Denetim: özetteki her sayı/tarih ve özel ad kaynakta geçmeli; yorum ifadeleri (önemli, olumlu,
  muhtemelen…) yasak; kaynak OCR kontrolü bekliyorsa özet de bekler. Bulgu varsa `durum='kontrol'`.
- Atama/vekâlet: serbest özet yok; olgu listesi (işlem, ad, görev), her ad metinde birebir aranır.
- Etiketler: `etiketler.toml` (sen düzenlersin). Başlık/kurum kelime eşleşmesi + modelin listeden seçimi.
- Önce/sonra (`degisiklikler.birlestirme`): madde/fıkra değiştirme, madde sonuna fıkra ekleme,
  madde/fıkra kaldırma uygulanır. Bent/ibare düzeyi, araya ekleme, numara kaydırma, karmaşık atıf,
  arada başka değişiklik → **otomatik birleştirilemedi** (nedeniyle). Hedef arşivde yoksa
  "hedef arşivde yok". OCR metinlerine uygulanmaz.
- `gazete-ozet.timer`: saatte bir (:20).

## Faz 3 — site, RSS, taslaklar (paylaşım yok)

Özetler **yalnız onaylandıktan sonra** sitede, RSS'te ve taslaklarda görünür:

```
python3.12 -m gazete.onay gozden                  # bekleyenleri tek tek: o=onayla r=reddet d=düzelt a=atla q=çık
python3.12 -m gazete.onay liste [2026-10-31]      # bekleyenler ve uyarıları
python3.12 -m gazete.onay onayla --gun 2026-10-31 # o günün uyarısız özetleri
python3.12 -m gazete.onay onayla|reddet|geri|goster 20261031-2
python3.12 -m gazete.onay duzelt 20261031-15 "Elle yazılmış özet."
```

- Özet yeniden üretilirse ya da yürürlük cümlesi değişirse onay düşer.
- Kaynak metni OCR kontrolü bekleyen kalem onaylanamaz (önce `gazete.collect onayla`); tam metni de
  sitede gösterilmez.
- Site: `python3.12 -m gazete.site` → `data/web/site-<zaman>`, `data/web/current` sembolik bağı ona
  çevrilir (atomik). Onay komutları ve `gazete-ozet.service` sonrası otomatik çalışır.
- Sunucu: rootless Podman quadlet `deploy/gazete-web.container` (nginx:1.27-alpine, port 8080,
  `X-Robots-Tag: noindex`). LAN/tailnet: http://rhelsrv:8080 (firewalld'de 8080/tcp açık olmalı).
- Herkese açık kopya: https://buraksefa.github.io/resmi-gazete-ozeti/ — GitHub Pages, deponun
  `gh-pages` dalı (`python3.12 -m gazete.yayinla`). Site her üretildiğinde içerik değiştiyse dal tek
  commit olarak zorla itilir. Sunucu deploy key ile yazar (`~/.ssh/gazete_github`, `~/.ssh/config`'de
  `Host github-gazete`). Hedef `.env`'de `GAZETE_YAYIN` (github | cloudflare | boş). Bağlantılar
  `GAZETE_SITE_URL`'deki alt klasörle (`/resmi-gazete-ozeti`) üretilir; LAN nginx aynı yolu sunar.
  `*.pages.dev` Türkiye'den açılmadığı için Cloudflare Pages kullanılmıyor; Workers seçeneği
  (`deploy/worker/wrangler.jsonc`) yedek olarak duruyor. Arama motorlarına açıktır (sitemap.xml).
- Sayfalar: günler, düzenleme (özet, atama olgu tablosu, önce/sonra kelime farkı ya da
  "otomatik birleştirilemedi", tam metin, kaynak), etiketler, arama (istemci tarafı), RSS genel ve
  etiket bazlı. Her sayfada: "Bu bir özettir, bağlayıcı olan Resmî Gazete'deki metindir."
- Taslaklar (DRY-RUN): `data/taslak/<tarih>/x.txt` (başlık + dizi, her gönderi ≤280, bağlantı 23
  sayılır), `eposta.html` / `eposta.txt`. Kodda gönderme yok; paylaşım ayrı bir adım olarak senin
  onayınla eklenir.
