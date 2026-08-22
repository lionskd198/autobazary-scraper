"""Scraper pro auto.bazos.cz – jednoduché server-rendered HTML.

Bazoš je z těch tří nejjednodušší: statické HTML, žádný JS, žádná tvrdá
ochrana proti botům. Ideální referenční implementace.

POZN.: HTML struktura se občas mění. Selektory jsou soustředěné do metody
``parse_list_page`` – když se něco rozbije, upravuje se jen tady. Doporučuju
si nejdřív stáhnout stránku přes ``python -m autobazary.run dump bazos``.
"""
from __future__ import annotations

import re
from typing import Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import (
    Listing,
    clean_text,
    parse_price,
    parse_year,
)
from .base import BaseScraper

_ID_RE = re.compile(r"/inzerat/(\d+)/")
_KM_RE = re.compile(r"(\d[\d\s]{3,})\s*km", re.IGNORECASE)


class BazosScraper(BaseScraper):
    name = "bazos"
    base_url = "https://auto.bazos.cz"

    def iter_page_urls(self, query: Optional[str], max_pages: int,
                       price_min: Optional[int] = None,
                       price_max: Optional[int] = None, **kwargs) -> Iterator[str]:
        # Bazoš stránkuje parametrem ?crz=<offset> po 20 inzerátech.
        # Cenový filtr: cenaod=<min>&cenado=<max>
        price_params = ""
        if price_min:
            price_params += f"&cenaod={price_min}"
        if price_max:
            price_params += f"&cenado={price_max}"
        for page in range(max_pages):
            offset = page * 20
            if query:
                yield f"{self.base_url}/search.php?hledat={query}&rubriky=auto&crz={offset}{price_params}"
            else:
                yield f"{self.base_url}/?crz={offset}{price_params}"

    def parse_list_page(self, html: str) -> Iterator[Listing]:
        soup = BeautifulSoup(html, "lxml")
        for card in soup.select(".inzeraty.inzeratyflex, div.inzeraty"):
            # Pozor: v .inzeratynadpis je první <a> obrázkový (prázdný text);
            # skutečný název je v <h2 class="nadpis"><a>. Bereme textový odkaz.
            title_a = card.select_one("h2.nadpis a, .nadpis a")
            if not title_a or not title_a.get("href"):
                title_a = card.select_one("a[href*='/inzerat/']")
            if not title_a or not title_a.get("href"):
                continue
            url = urljoin(self.base_url, title_a["href"])
            m = _ID_RE.search(url)
            if not m:
                continue
            source_id = m.group(1)
            title = clean_text(title_a.get_text())

            price_el = card.select_one(".inzeratycena, .cena")
            price = parse_price(price_el.get_text() if price_el else None)

            desc_el = card.select_one(".popis, .inzeratypopis")
            description = clean_text(desc_el.get_text() if desc_el else None)

            loc_el = card.select_one(".inzeratylok, .mesto")
            location = clean_text(loc_el.get_text() if loc_el else None)

            mileage = None
            if description:
                km = _KM_RE.search(description)
                if km:
                    mileage = int(re.sub(r"\D", "", km.group(1)))

            yield Listing(
                source=self.name,
                source_id=source_id,
                url=url,
                title=title or "(bez názvu)",
                year=parse_year(title),
                price=price,
                mileage_km=mileage,
                location=location,
                seller_type="private",  # Bazoš = převážně soukromá inzerce
                description=description,
                raw={"title": title, "location": location},
            )
