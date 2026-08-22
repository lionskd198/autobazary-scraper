"""Sdílený HTTP klient s rate-limitingem, retry a rozumnými hlavičkami."""
from __future__ import annotations

import logging
import ssl
import time
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import config

log = logging.getLogger("autobazary.http")


def _build_ssl_context() -> ssl.SSLContext | bool:
    """Ověřování TLS přes systémový trust store (Windows/macOS/Linux).

    Řeší firemní proxy s TLS inspekcí, jejíž kořenová CA je v systémovém
    úložišti, ale ne v certifi bundlu. Když truststore není k dispozici,
    spadneme na výchozí certifi ověřování (verify=True).
    """
    try:
        import truststore  # type: ignore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        return True


class HttpClient:
    """Tenký wrapper nad httpx.Client – jedna instance na scraper.

    Zajišťuje minimální prodlevu mezi requesty (slušnost k serveru) a
    exponenciální retry na síťové chyby a 429/5xx.
    """

    def __init__(self, delay: Optional[float] = None):
        self.delay = config.delay if delay is None else delay
        self._last_request = 0.0
        self._client = httpx.Client(
            headers={
                "User-Agent": config.user_agent,
                "Accept-Language": "cs,sk;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            timeout=config.timeout,
            follow_redirects=True,
            http2=True,
            verify=_build_ssl_context(),
        )

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def get(self, url: str, **kwargs) -> httpx.Response:
        self._throttle()
        log.debug("GET %s", url)
        resp = self._client.get(url, **kwargs)
        if resp.status_code in (429, 500, 502, 503, 504):
            resp.raise_for_status()
        return resp

    def get_text(self, url: str, **kwargs) -> str:
        return self.get(url, **kwargs).text

    def get_json(self, url: str, **kwargs):
        return self.get(url, **kwargs).json()

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def post(self, url: str, **kwargs) -> httpx.Response:
        self._throttle()
        log.debug("POST %s", url)
        resp = self._client.post(url, **kwargs)
        if resp.status_code in (429, 500, 502, 503, 504):
            resp.raise_for_status()
        return resp

    def post_text(self, url: str, **kwargs) -> str:
        return self.post(url, **kwargs).text

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
