"""Normalizovaný datový model jednoho inzerátu + pomocné parsery."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class Listing:
    """Jednotný tvar inzerátu napříč všemi portály.

    Každý scraper plní tento objekt; do DB se ukládá přes ``db.upsert_listings``.
    """

    source: str                     # "sauto" | "tipcars" | "bazos"
    source_id: str                  # ID inzerátu v rámci portálu (z URL)
    url: str
    title: str
    brand: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    price: Optional[int] = None
    price_currency: str = "CZK"
    mileage_km: Optional[int] = None
    fuel: Optional[str] = None
    transmission: Optional[str] = None
    power_kw: Optional[int] = None
    body_type: Optional[str] = None
    location: Optional[str] = None
    seller_type: Optional[str] = None   # "dealer" | "private" | None
    description: Optional[str] = None
    scraped_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Parsery – čistí surový text z HTML na typované hodnoty. Vše je odolné vůči
# None a nečekaným formátům (vrací None místo výjimky).
# ---------------------------------------------------------------------------

def clean_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text or None


def parse_int(value: Optional[str]) -> Optional[int]:
    """Vytáhne celé číslo z textu jako '1 250 000 Kč' -> 1250000."""
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def parse_price(value: Optional[str]) -> Optional[int]:
    n = parse_int(value)
    # ceny typu "Info o ceně" nebo 1 => ignoruj nesmyslně nízké
    if n is not None and n < 1000:
        return None
    return n


def parse_year(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    m = re.search(r"(19|20)\d{2}", str(value))
    return int(m.group(0)) if m else None


def parse_mileage(value: Optional[str]) -> Optional[int]:
    return parse_int(value)


def parse_power_kw(value: Optional[str]) -> Optional[int]:
    """'110 kW' -> 110. Pokud je hodnota v koních (k/HP/PS), převede na kW."""
    if value is None:
        return None
    text = str(value).lower()
    n = parse_int(text)
    if n is None:
        return None
    if any(unit in text for unit in ("kw",)):
        return n
    if any(unit in text for unit in (" k", "hp", "ps", "koň", "kon")):
        return round(n * 0.7355)
    return n


_FUEL_MAP = {
    "benzín": "benzín", "benzin": "benzín", "petrol": "benzín",
    "nafta": "nafta", "diesel": "nafta",
    "elektro": "elektro", "electric": "elektro",
    "hybrid": "hybrid", "plug-in": "hybrid",
    "cng": "cng", "lpg": "lpg",
}


def normalize_fuel(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    low = value.lower()
    for key, norm in _FUEL_MAP.items():
        if key in low:
            return norm
    return clean_text(value)
