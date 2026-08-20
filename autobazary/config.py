"""Centrální konfigurace načtená z prostředí / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Config:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://autobazary:autobazary@localhost:5433/autobazary",
    )
    delay: float = float(os.getenv("SCRAPE_DELAY", "1.5"))
    max_pages: int = int(os.getenv("SCRAPE_MAX_PAGES", "5"))
    timeout: float = float(os.getenv("SCRAPE_TIMEOUT", "20"))
    fetch_detail: bool = _get_bool("SCRAPE_DETAIL", False)
    user_agent: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    )


config = Config()
