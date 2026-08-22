"""Scraper pro tipcars.com.

TipCars je JS aplikace (Symfony UX/Stimulus) – výpis se nedotahuje ze statického
HTML, ale POST požadavkem na interní API:

    POST /cs/api/web/html/v1/offer/search/sales/personal?offset=<o>&limit=<l>
    tělo: JSON s filtrem (category "O" = osobní auta)
    hlavičky: X-List-JustPaging: 1
    odpověď: JSON {"items": "<HTML výpisu>", "paginator": ..., "header": ...}

Karta inzerátu (uvnitř `items`) nese id v ``data-listing-item-id-value`` a
parametry v pořadí: [rok, nájezd, výkon kW, palivo, převodovka, počet sedadel].

POZN.: Pokud přestane vracet inzeráty, ověř endpoint/tělo přes DevTools ->
Network (POST .../offer/search/...) a uprav ``SEARCH_URL`` / ``_SEARCH_BODY``.
"""
from __future__ import annotations

import json
import re
from typing import Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..config import config
from ..models import (
    Listing,
    clean_text,
    normalize_fuel,
    parse_mileage,
    parse_power_kw,
    parse_price,
    parse_year,
)
from .base import BaseScraper

_PER_PAGE = 20
_ID_IN_URL = re.compile(r"-(\d{5,})\.html")


class TipCarsScraper(BaseScraper):
    name = "tipcars"
    base_url = "https://www.tipcars.com"
    search_url = (
        "https://www.tipcars.com/cs/api/web/html/v1/offer/search/sales/personal"
        "?offset={offset}&limit={limit}&sort=default"
    )
    # Filtr: category "O" = osobní auta. Další filtry (značka, cena, rok) se
    # přidávají do "properties"/"enumerations" podle katalogu TipCars.
    _search_body = {
        "age": None, "query": None,
        "enumerations": {
            "currency": ["A"], "odometer_unit": ["A"],
            "engine_power_unit": ["A"], "category": ["O"],
        },
        "properties": {}, "options": [], "geolocation": None,
    }
    _search_headers = {
        "X-List-NextInf": "0",
        "X-List-JustPaging": "1",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json",
    }

    # iter_page_urls je zde jen kvůli `dump` příkazu (GET vrátí prázdný shell,
    # skutečná data tečou přes POST v ``scrape``).
    def iter_page_urls(self, query: Optional[str], max_pages: int, **kwargs) -> Iterator[str]:
        for page in range(max_pages):
            yield self.search_url.format(offset=page * _PER_PAGE, limit=_PER_PAGE)

    def scrape(
        self,
        query: Optional[str] = None,
        max_pages: Optional[int] = None,
        fetch_detail: Optional[bool] = None,
        price_max: Optional[int] = None,
    ) -> Iterator[Listing]:
        max_pages = max_pages if max_pages is not None else config.max_pages
        seen: set[str] = set()
        for page in range(max_pages):
            url = self.search_url.format(offset=page * _PER_PAGE, limit=_PER_PAGE)
            body = dict(self._search_body)
            body["properties"] = dict(self._search_body["properties"])
            if query:
                body["query"] = query
            if price_max:
                body["properties"]["price_to"] = price_max
            try:
                resp = self.http.post(url, content=json.dumps(body),
                                      headers=self._search_headers)
                text = resp.text
            except Exception as exc:  # noqa: BLE001
                self._log_warn(url, exc)
                break

            count = 0
            for listing in self.parse_list_page(text):
                if listing.source_id in seen:
                    continue
                seen.add(listing.source_id)
                count += 1
                yield listing

            if count == 0:
                break  # konec stránkování

    def fetch_first_page(self, query: Optional[str] = None) -> str:
        url = self.search_url.format(offset=0, limit=_PER_PAGE)
        body = dict(self._search_body)
        if query:
            body["query"] = query
        import logging
        logging.getLogger("autobazary.scraper").info("POST %s", url)
        return self.http.post_text(url, content=json.dumps(body),
                                   headers=self._search_headers)

    def parse_list_page(self, text: str) -> Iterator[Listing]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return
        html = data.get("items") if isinstance(data, dict) else None
        if not html:
            return
        soup = BeautifulSoup(html, "lxml")
        for card in soup.select("div.advertisement[data-listing-item-id-value]"):
            listing = self._card_to_listing(card)
            if listing:
                yield listing

    # ------------------------------------------------------------------

    def _card_to_listing(self, card) -> Optional[Listing]:
        source_id = card.get("data-listing-item-id-value")
        if not source_id:
            return None

        a = card.select_one("a[href]")
        href = a.get("href") if a else None
        url = urljoin(self.base_url, href) if href else \
            f"{self.base_url}/detail/{source_id}.html"

        title_el = card.select_one(".advertisement-name__title")
        title = clean_text(title_el.get_text(" ", strip=True)) if title_el else None

        # Cena bývá "348 000 Kč 287 604 Kč bez DPH" -> bereme jen první částku.
        price_el = card.select_one(".advertisement-name__price")
        price = None
        if price_el:
            price = parse_price(price_el.get_text(" ", strip=True).split("Kč")[0])

        # boxy: [rok, nájezd, výkon, palivo, převodovka, sedadla]
        boxes = [clean_text(b.get_text(" ", strip=True))
                 for b in card.select(".detail-box-S__text")]

        year = mileage = power = fuel = transmission = None
        for box in boxes:
            if not box:
                continue
            low = box.lower()
            if year is None and re.fullmatch(r"(19|20)\d{2}", box.strip()):
                year = parse_year(box)
            elif "km" in low:
                mileage = parse_mileage(box)
            elif "kw" in low:
                power = parse_power_kw(box)
            elif any(f in low for f in ("nafta", "benzin", "benzín", "elektro",
                                        "hybrid", "cng", "lpg")):
                fuel = normalize_fuel(box)
            elif any(t in low for t in ("manuál", "automat")):
                transmission = box

        return Listing(
            source=self.name,
            source_id=str(source_id),
            url=url,
            title=title or "(bez názvu)",
            year=year or parse_year(title or ""),
            price=price,
            mileage_km=mileage,
            fuel=fuel,
            transmission=transmission,
            power_kw=power,
            seller_type="dealer",  # TipCars = převážně autobazary
            raw={"title": title, "boxes": boxes},
        )

    def _log_warn(self, url: str, exc: Exception) -> None:
        import logging
        logging.getLogger("autobazary.scraper").warning(
            "[tipcars] chyba POST %s: %s", url, exc)
