"""Siteyi herkese açık kopyaya yükler. Hedef .env'deki GAZETE_YAYIN ile seçilir:

  github      GitHub Pages: data/web/current, GAZETE_GITHUB_DEPO deposunun gh-pages dalına tek
              commit olarak zorla itilir (geçmiş birikmez). Kimlik: sunucudaki deploy key
              (~/.ssh/gazete_github, ~/.ssh/config'de "github-gazete" takma adı).
  cloudflare  Cloudflare Workers statik varlıkları (aşağıda).
  (boş)       yükleme yapılmaz; site yalnız ev ağında.

  python3.12 -m gazete.yayinla           # değişmediyse atlar
  python3.12 -m gazete.yayinla --zorla   # değişmemiş olsa da yükle

Cloudflare:
*.pages.dev Türkiye'den açılmadığı için Pages yerine Workers kullanılır; Worker kodu yoktur, yalnız
dosyalar sunulur (_headers ve 404.html dahil). Ayarlar: deploy/worker/wrangler.jsonc.
wrangler, rootless Podman'daki node konteynerinde çalışır (sunucuya Node kurulmaz).
.env: CLOUDFLARE_API_TOKEN (yalnız "Workers Scripts: Edit" yetkili), CLOUDFLARE_ACCOUNT_ID.
Yalnız onaylı özetler sitede olduğu için yüklenen de yalnız onaylı içeriktir.
"""
import hashlib
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime

from . import config

log = logging.getLogger("gazete")
NODE = "docker.io/library/node:22-alpine"
WRANGLER = "wrangler@4"


def ozet_hash(klasor):
    h = hashlib.sha256()
    for p in sorted(klasor.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(klasor)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def wrangler(*args, site=None):
    if not (config.CF_API_TOKEN and config.CF_ACCOUNT_ID):
        raise SystemExit(".env'de CLOUDFLARE_API_TOKEN ve CLOUDFLARE_ACCOUNT_ID yok; yükleme yapılmadı")
    cmd = ["podman", "run", "--rm", "-e", "CLOUDFLARE_API_TOKEN", "-e", "CLOUDFLARE_ACCOUNT_ID",
           "-v", "gazete-npm:/root/.npm"]
    if site:
        # wrangler.jsonc'de assets.directory = /site; .wrangler/tmp yazılabilmesi için yapılandırma rw bağlanır
        cmd += ["-v", f"{site}:/site:ro,z", "-v", f"{config.WORKER_DIR}:/cfg:z", "-w", "/cfg"]
    cmd += [NODE, "npx", "--yes", WRANGLER, *args]
    env = dict(os.environ, CLOUDFLARE_API_TOKEN=config.CF_API_TOKEN, CLOUDFLARE_ACCOUNT_ID=config.CF_ACCOUNT_ID)
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)


def git(*args, cwd):
    return subprocess.run(["git", "-c", f"user.name={config.GIT_NAME}", "-c", f"user.email={config.GIT_EMAIL}", *args],
                          cwd=cwd, capture_output=True, text=True, timeout=600)


def github(site):
    """site klasörünü gh-pages dalına tek commit olarak zorla iter. Dönüş: hata metni ya da None."""
    d = config.GH_PAGES_DIR
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(site, d)
    (d / "_headers").unlink(missing_ok=True)   # yalnız Cloudflare'de anlamlı
    adimlar = [("init", "-q", "-b", "gh-pages"), ("add", "-A"),
               ("commit", "-q", "-m", f"site {datetime.now(config.TZ).isoformat(timespec='minutes')}"),
               ("push", "-q", "-f", config.GITHUB_DEPO, "gh-pages")]
    for a in adimlar:
        r = git(*a, cwd=d)
        if r.returncode != 0:
            return f"git {a[0]}: {(r.stderr or r.stdout)[-800:]}"
    return None


def cloudflare(site):
    if not (config.CF_API_TOKEN and config.CF_ACCOUNT_ID):
        return ".env'de Cloudflare anahtarı yok"
    r = wrangler("deploy", site=site)
    if r.returncode != 0:
        return (r.stderr or r.stdout)[-800:]
    return None


def yayinla(zorla=False):
    if config.YAYIN_HEDEF not in ("github", "cloudflare"):
        log.info("Yükleme atlandı: GAZETE_YAYIN ayarlı değil (site yalnız ev ağında)")
        return False
    site = (config.WEB_DIR / "current").resolve()
    h = config.YAYIN_HEDEF + ":" + ozet_hash(site)
    son = config.WEB_DIR / ".son-yayin"
    if not zorla and son.exists() and son.read_text().strip() == h:
        log.info("Yayın: değişiklik yok")
        return False
    hata = (github if config.YAYIN_HEDEF == "github" else cloudflare)(site)
    if hata:
        log.error("Yükleme başarısız (%s): %s", config.YAYIN_HEDEF, hata)
        return False
    son.write_text(h)
    log.info("Yayın: yüklendi (%s) %s", config.YAYIN_HEDEF, config.SITE_URL)
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(config.LOG_DIR / "yayin.log", encoding="utf-8")])
    yayinla("--zorla" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
