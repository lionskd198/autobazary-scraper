"""Scraper pro sauto.cz.

Sauto je React SPA – ze statického HTML se inzeráty nedají vytáhnout, obsah
se dotahuje JSON API (stejným, které používá web). Proto tady místo HTML
parsujeme JSON z ``/api/v1/items/search``.

DŮLEŽITÉ: Sauto svoje API čas od času upraví (názvy polí, parametry). Pokud
scraper vrací 0 inzerátů:
  1) otevři v prohlížeči DevTools -> Network -> filtr "search" na sauto.cz,
  2) zkopíruj aktuální endpoint a tvar odpovědi,
  3) uprav ``API_URL``, ``iter_page_urls`` a mapování v ``_item_to_listing``.
Mapování je schválně napsané obranně (``_dig`` toleruje chybějící klíče).
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator, Optional

from ..models import (
    Listing,
    clean_text,
    normalize_fuel,
    parse_power_kw,
    parse_price,
)
from .base import BaseScraper

API_URL = "https://www.sauto.cz/api/v1/items/search"
_KW_RE = re.compile(r"(\d{2,3})\s*kW", re.IGNORECASE)
# category_id 838 = osobní automobily; per_page 40 je max, který API běžně vrací
_PER_PAGE = 40


class SautoScraper(BaseScraper):
    name = "sauto"
    base_url = "https://www.sauto.cz"

    def iter_page_urls(self, query: Optional[str], max_pages: int,
                       price_max: Optional[int] = None) -> Iterator[str]:
        for page in range(1, max_pages + 1):
            params = [
                "category_id=838",
                "condition_seek=1",
                f"per_page={_PER_PAGE}",
                f"page={page}",
            ]
            if query:
                params.append(f"query={query}")
            if price_max:
                params.append(f"price_to={price_max}")
            yield f"{API_URL}?{'&'.join(params)}"

    def parse_list_page(self, html: str) -> Iterator[Listing]:
        try:
            data = json.loads(html)
        except json.JSONDecodeError:
            return
        # Odpověď bývá {"results": [...]} nebo {"items": [...]}.
        items = data.get("results") or data.get("items") or []
        for item in items:
            listing = self._item_to_listing(item)
            if listing:
                yield listing

    # ------------------------------------------------------------------

    def _item_to_listing(self, item: dict[str, Any]) -> Optional[Listing]:
        source_id = str(item.get("id") or "").strip()
        if not source_id:
            return None

        brand = _dig(item, "manufacturer_cb", "name")
        model = _dig(item, "model_cb", "name")
        name = clean_text(item.get("name")) or " ".join(
            x for x in (clean_text(brand), clean_text(model)) if x
        ) or "(bez názvu)"

        # URL se v odpovědi nevrací -> složíme z seo_name značky/modelu + id.
        man_seo = _dig(item, "manufacturer_cb", "seo_name") or _slug(brand)
        mod_seo = _dig(item, "model_cb", "seo_name") or _slug(model)
        url = f"{self.base_url}/osobni/detail/{man_seo}/{mod_seo}/{source_id}"

        # Rok výroby: manufacturing_date "YYYY-MM-DD", fallback in_operation_date.
        year = None
        for key in ("manufacturing_date", "in_operation_date"):
            raw_year = item.get(key)
            if raw_year:
                try:
                    year = int(str(raw_year)[:4])
                    break
                except (ValueError, TypeError):
                    pass

        # Cena: 0 nebo price_by_agreement => cena dohodou (None).
        price = item.get("price")
        price = int(price) if isinstance(price, (int, float)) and price >= 1000 else None

        # Výkon [kW] není samostatné pole – vytáhneme z názvu / doplňku modelu.
        power_src = f"{item.get('additional_model_name') or ''} {name}"
        power = parse_power_kw(_KW_RE.search(power_src).group(0)) if _KW_RE.search(power_src) else None

        # Prodejce: přítomný objekt premise => autobazar/dealer, jinak soukromník.
        seller_type = "dealer" if item.get("premise") else "private"

        loc = item.get("locality") or {}
        location = clean_text(loc.get("district") or loc.get("citypart") or loc.get("address")) \
            if isinstance(loc, dict) else clean_text(loc)

        return Listing(
            source=self.name,
            source_id=source_id,
            url=url,
            title=name,
            brand=clean_text(brand),
            model=clean_text(model),
            year=year,
            price=price,
            mileage_km=_to_int(item.get("tachometer")),
            fuel=normalize_fuel(_dig(item, "fuel_cb", "name")),
            transmission=clean_text(_dig(item, "gearbox_cb", "name")),
            power_kw=power,
            body_type=clean_text(_dig(item, "vehicle_body_cb", "name")),
            location=location,
            seller_type=seller_type,
            raw=item,
        )


def _dig(obj: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slug(value: Optional[str]) -> str:
    if not value:
        return "auto"
    return "".join(c.lower() if c.isalnum() else "-" for c in value).strip("-")
