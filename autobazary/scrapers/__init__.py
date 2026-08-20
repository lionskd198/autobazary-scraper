"""Registr dostupných scraperů."""
from __future__ import annotations

from .base import BaseScraper
from .sauto import SautoScraper
from .tipcars import TipCarsScraper
from .bazos import BazosScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    SautoScraper.name: SautoScraper,
    TipCarsScraper.name: TipCarsScraper,
    BazosScraper.name: BazosScraper,
}

__all__ = ["BaseScraper", "SCRAPERS", "SautoScraper", "TipCarsScraper", "BazosScraper"]
