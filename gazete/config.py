"""Ayarlar: .env + varsayılanlar. Yalnızca standart kütüphane."""
import os
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
BASE_URL = "https://www.resmigazete.gov.tr"

ROOT = Path(os.environ.get("GAZETE_ROOT", Path(__file__).resolve().parent.parent))


def _load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")

DATA = Path(os.environ.get("GAZETE_DATA", ROOT / "data"))
RAW_DIR = DATA / "raw"            # ham HTML/PDF, tarih bazlı
ARSIV_DIR = DATA / "arsiv"        # metin git reposu (git scraping)
DB_PATH = DATA / "gazete.sqlite"
LOG_DIR = DATA / "log"

# İletişim bilgisi User-Agent'ta zorunlu; boşsa toplayıcı çalışmaz.
CONTACT = os.environ.get("GAZETE_CONTACT", "").strip()
USER_AGENT = f"GazeteOzeti/0.1 (+{CONTACT})"

# Nezaket sınırları
REQUEST_DELAY_S = float(os.environ.get("GAZETE_DELAY_S", "5"))
MAX_REQUESTS_PER_RUN = int(os.environ.get("GAZETE_MAX_REQUESTS", "80"))
TIMEOUT_S = 60

# Geçmişi doldurma: her çalıştırmada en fazla bu kadar sayı (gün)
BACKFILL_PER_RUN = int(os.environ.get("GAZETE_BACKFILL_PER_RUN", "2"))
BACKFILL_DAYS = int(os.environ.get("GAZETE_BACKFILL_DAYS", "365"))

# OCR: bir çalıştırmada en fazla bu kadar saniye (belge arasında durur)
OCR_SURE_S = int(os.environ.get("GAZETE_OCR_SURE_S", str(45 * 60)))
# Her sayfadan sonra, sayfanın sürdüğü sürenin bu katı kadar beklenir (1.0 → CPU'nun yarı zamanı boşta).
# Kullanıcı systemd'sinde cpu denetleyicisi açık olmadığından CPUQuota tek başına işlemiyor.
OCR_DINLENME = float(os.environ.get("GAZETE_OCR_DINLENME", "1.0"))

# LLM (yerel Ollama, bsasrv). Özet ve doğrulama bir çalıştırmada en fazla OZET_SURE_S saniye.
OLLAMA_URL = os.environ.get("GAZETE_OLLAMA_URL", "http://bsasrv:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("GAZETE_OLLAMA_MODEL", "gemma3:4b")            # özet
OLLAMA_MODEL_OLGU = os.environ.get("GAZETE_OLLAMA_MODEL_OLGU", "qwen2.5:7b")  # atama olgu çıkarımı
LLM_NUM_CTX = int(os.environ.get("GAZETE_LLM_NUM_CTX", "4096"))
LLM_GIRDI_SINIR = int(os.environ.get("GAZETE_LLM_GIRDI", "6000"))   # karakter
OZET_SURE_S = int(os.environ.get("GAZETE_OZET_SURE_S", str(40 * 60)))
# Isınmayı azaltmak için (3 Kasım 2026): Ollama en fazla bu kadar CPU iş parçacığı kullanır (0 = Ollama'nın
# varsayılanı) ve her düzenlemeden sonra bu kadar saniye beklenir. Daha yavaş, daha serin.
LLM_NUM_THREAD = int(os.environ.get("GAZETE_LLM_NUM_THREAD", "2"))
LLM_ARA_S = float(os.environ.get("GAZETE_LLM_ARA_S", "30"))
ETIKET_DOSYASI = Path(os.environ.get("GAZETE_ETIKETLER", ROOT / "etiketler.toml"))
RAPOR_DIR = DATA / "rapor"

# Faz 3: statik site (yalnız ev ağı; nginx konteyneri data/web/current'ı sunar) ve dry-run taslaklar
WEB_DIR = DATA / "web"
TASLAK_DIR = DATA / "taslak"
SITE_URL = os.environ.get("GAZETE_SITE_URL", "http://rhelsrv:8080").rstrip("/")
# Site bir alt klasörde yayımlanıyorsa (GitHub Pages: /resmi-gazete-ozeti) bağlantılar bu kökle başlar
SITE_KOK = urlsplit(SITE_URL).path.rstrip("/")

# Cloudflare Workers statik varlıkları (herkese açık kopya, *.workers.dev). *.pages.dev Türkiye'den
# erişilemediği için (TLS el sıkışması kesiliyor, 2 Kasım 2026) Pages yerine Workers kullanılır.
# Anahtar .env'de; yoksa yükleme atlanır. Worker adı ve ayarları: deploy/worker/wrangler.jsonc
# Uyarısız ("hazir") özetler onay beklemeden "henüz incelenmedi" etiketiyle yayımlanır (9 Kasım 2026 kararı).
# 0 yapılırsa yalnız elle onaylananlar yayımlanır. Taslaklara (X/e-posta) her durumda yalnız onaylılar girer.
OTOMATIK_YAYIN = os.environ.get("GAZETE_OTOMATIK_YAYIN", "1").strip() not in ("0", "", "false", "hayir")

# Yayın hedefi: "github" (GitHub Pages, gh-pages dalı), "cloudflare" (Workers) ya da boş (yalnız LAN)
YAYIN_HEDEF = os.environ.get("GAZETE_YAYIN", "").strip().lower()
GITHUB_DEPO = os.environ.get("GAZETE_GITHUB_DEPO", "github-gazete:buraksefa/resmi-gazete-ozeti.git")
GH_PAGES_DIR = DATA / "gh-pages"

CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
WORKER_DIR = ROOT / "deploy" / "worker"

GIT_NAME = "Gazete Ozeti Bot"
GIT_EMAIL = "gazete-ozeti@localhost"
