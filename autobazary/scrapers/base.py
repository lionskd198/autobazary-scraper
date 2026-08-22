"""Společný základ všech scraperů."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Iterator, Optional

from ..config import config
from ..http import HttpClient
from ..models import Listing

log = logging.getLogger("autobazary.scraper")


class BaseScraper(ABC):
    """Kontrakt scraperu.

    Potomek definuje ``name``, ``base_url`` a implementuje ``iter_page_urls``
    a ``parse_list_page``. Volitelně přepíše ``parse_detail`` pro obohacení
    inzerátu daty z detailní stránky.
    """

    name: str = "base"
    base_url: str = ""

    def __init__(self, http: Optional[HttpClient] = None):
        self.http = http or HttpClient()
        self._own_http = http is None

    # ---- rozhraní, které plní potomek --------------------------------

    @abstractmethod
    def iter_page_urls(self, query: Optional[str], max_pages: int, **kwargs) -> Iterator[str]:
        """Vrací URL stránek s výsledky hledání (stránkování)."""

    @abstractmethod
    def parse_list_page(self, html: str) -> Iterator[Listing]:
        """Z HTML stránky s výsledky vydává jednotlivé Listing objekty."""

    def parse_detail(self, listing: Listing, html: str) -> Listing:
        """Doplní listing o data z jeho detailní stránky. Výchozí = beze změny."""
        return listing

    def fetch_first_page(self, query: Optional[str] = None) -> str:
        """Vrátí syrovou odpověď první stránky (pro `dump` / ladění selektorů)."""
        url = next(self.iter_page_urls(query, 1))
        log.info("GET %s", url)
        return self.http.get_text(url)

    # ---- orchestrace -------------------------------------------------

    def scrape(
        self,
        query: Optional[str] = None,
        max_pages: Optional[int] = None,
        fetch_detail: Optional[bool] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
    ) -> Iterator[Listing]:
        max_pages = max_pages if max_pages is not None else config.max_pages
        fetch_detail = fetch_detail if fetch_detail is not None else config.fetch_detail

        seen: set[str] = set()
        for page_url in self.iter_page_urls(query, max_pages, price_min=price_min, price_max=price_max):
            try:
                html = self.http.get_text(page_url)
            except Exception as exc:  # noqa: BLE001 – jedna vadná stránka neshodí běh
                log.warning("[%s] chyba stránky %s: %s", self.name, page_url, exc)
                continue

            page_count = 0
            for listing in self.parse_list_page(html):
                if listing.source_id in seen:
                    continue
                seen.add(listing.source_id)
                page_count += 1

                if fetch_detail:
                    try:
                        detail_html = self.http.get_text(listing.url)
                        listing = self.parse_detail(listing, detail_html)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("[%s] detail %s: %s", self.name, listing.url, exc)

                yield listing

            log.info("[%s] %s -> %d inzerátů", self.name, page_url, page_count)
            if page_count == 0:
                # prázdná stránka = konec stránkování
                break

    def close(self) -> None:
        if self._own_http:
            self.http.close()
