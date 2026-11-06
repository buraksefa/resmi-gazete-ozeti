"""Ham dosya arşivi ve metin git reposu (git scraping)."""
import fcntl
import json
import os
from contextlib import contextmanager
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from . import config


def item_id(url):
    """/eskiler/2026/10/20261031-2.htm -> eskiler/20261031-2 ; ilanlar -> ilanlar/..."""
    p = urlparse(url).path
    stem = Path(p).stem
    return ("ilanlar/" if p.startswith("/ilanlar/") else "eskiler/") + stem


def gun_dir(base, tarih):
    y, m, d = tarih.split("-")
    return base / y / m / d


def raw_path(tarih, url):
    p = urlparse(url).path
    sub = "ilanlar" if p.startswith("/ilanlar/") else "eskiler"
    return gun_dir(config.RAW_DIR, tarih) / sub / Path(p).name


def fihrist_raw_path(tarih, mukerrer):
    name = "fihrist.html" if mukerrer == 0 else f"fihrist-m{mukerrer}.html"
    return gun_dir(config.RAW_DIR, tarih) / name


def write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# --- metin reposu -------------------------------------------------------------

def _git(*args, check=True):
    return subprocess.run(["git", "-C", str(config.ARSIV_DIR), *args],
                          capture_output=True, text=True, check=check)


def ensure_repo():
    config.ARSIV_DIR.mkdir(parents=True, exist_ok=True)
    if not (config.ARSIV_DIR / ".git").exists():
        _git("init", "-q", "-b", "main")
        _git("config", "user.name", config.GIT_NAME)
        _git("config", "user.email", config.GIT_EMAIL)
        (config.ARSIV_DIR / "README.md").write_text(
            "# Resmî Gazete metin arşivi\n\nKaynak: https://www.resmigazete.gov.tr — "
            "bağlayıcı olan Resmî Gazete'deki metindir.\nHer gün bir commit; dosya düzeni "
            "`YYYY/MM/DD/<id>.txt` + `YYYY/MM/DD/fihrist.json`.\n", encoding="utf-8")


def write_item_text(row):
    d = gun_dir(config.ARSIV_DIR, row["tarih"])
    kind, stem = row["id"].split("/")
    name = (f"ilan-{stem}" if kind == "ilanlar" else stem) + ".txt"
    head = [f"başlık: {row['baslik']}", f"tür: {row['tur']}", f"kurum: {row['kurum'] or ''}",
            f"bölüm: {row['bolum']} / {row['alt_baslik']}",
            f"sayı: {row['sayi']}" + (f" ({row['mukerrer']}. mükerrer)" if row["mukerrer"] else ""),
            f"kaynak: {row['kaynak_url']}"]
    if row["ocr_durum"] == "tamam":
        g = json.loads(row["ocr_bilgi"] or "{}").get("guven")
        head.append("not: taranmış PDF — metin görüntüden otomatik okundu (OCR"
                    + (f", ortalama güven {g}" if g is not None else "") + "); rakam ve adlar hatalı "
                    "olabilir, bağlayıcı olan kaynaktaki metindir")
    elif row["ocr_gerekli"]:
        head.append("not: taranmış PDF — aşağıdaki metin yalnızca yazı katmanı (başlık/imza), karar metni görüntüde")
    if row["kontrol_gerekli"]:
        head += ["kontrol gerekiyor: " + n for n in (row["kontrol_neden"] or "").splitlines()]
    head += ["---", ""]
    path = d / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(head) + (row["metin"] or ""), encoding="utf-8")


def write_day_index(tarih, rows):
    d = gun_dir(config.ARSIV_DIR, tarih)
    d.mkdir(parents=True, exist_ok=True)
    data = [{k: r[k] for k in ("id", "sayi", "mukerrer", "sira", "bolum", "alt_baslik", "kurum",
                               "tur", "baslik", "kaynak_url", "ocr_gerekli", "metin_kaynagi",
                               "ocr_durum", "kontrol_gerekli")} | {"metin_var": r["metin"] is not None}
            for r in rows]
    (d / "fihrist.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


@contextmanager
def kilit():
    """Toplayıcı ile OCR aynı anda arşive yazmasın."""
    config.DATA.mkdir(parents=True, exist_ok=True)
    with open(config.DATA / ".arsiv.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def commit_day(tarih, mesaj):
    y, m, d = tarih.split("-")
    _git("add", "-A", "--", f"{y}/{m}/{d}", "README.md")
    if _git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    _git("commit", "-q", "-m", mesaj)
    return True
