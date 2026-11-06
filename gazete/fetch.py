"""Nazik HTTP istemcisi: sabit User-Agent, istekler arası bekleme, istek tavanı,
robots.txt'teki Noindex yollarını atlama."""
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class BudgetExceeded(RuntimeError):
    pass


class Fetcher:
    def __init__(self, delay=None, max_requests=None):
        if not config.CONTACT:
            raise SystemExit("GAZETE_CONTACT .env içinde tanımlı değil; User-Agent'ta iletişim bilgisi zorunlu.")
        self.delay = config.REQUEST_DELAY_S if delay is None else delay
        self.max_requests = config.MAX_REQUESTS_PER_RUN if max_requests is None else max_requests
        self.count = 0
        self._last = 0.0
        self.skip_paths = set()

    def get(self, url):
        """(status, body_bytes, headers) döndürür. 404 hata değil, status olarak döner."""
        if self.count >= self.max_requests:
            raise BudgetExceeded(f"istek tavanı ({self.max_requests}) doldu")
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.count += 1
        req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=config.TIMEOUT_S) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)
        finally:
            self._last = time.monotonic()

    def load_robots(self):
        status, body, _ = self.get(config.BASE_URL + "/robots.txt")
        if status == 200:
            self.skip_paths = parse_robots_noindex(body.decode("utf-8", "replace"))

    def is_skipped(self, url):
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
        return path in self.skip_paths


def parse_robots_noindex(text):
    """Herhangi bir User-agent grubundaki Disallow/Noindex yollarını toplar.
    Site arama motorlarından gizlemek istediği dosyaları biz de arşivlemeyiz."""
    paths = set()
    for line in text.splitlines():
        key, _, val = line.partition(":")
        if key.strip().lower() in ("noindex", "disallow") and val.strip():
            p = urllib.parse.unquote(val.strip().split("#")[0])
            paths.add(p)
    return paths
