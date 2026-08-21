"""Jednorázová migrace: přidá tabulku listings_price_history.

Spusť jednou na existující DB:
    python migrate_add_price_history.py

Skript je idempotentní – pokud tabulka už existuje, nic neprovede.
Navíc naplní historii aktuálními cenami ze stávajících listings,
takže od prvního scrape po migraci se hned zachytí změny.
"""
from __future__ import annotations

import logging
import sys

from sqlalchemy import inspect, text

from autobazary.db import get_engine, init_db, listings, listings_price_history
from autobazary.config import config

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def run() -> None:
    log.info("Připojuji se k %s", _safe_dsn())
    engine = get_engine()

    # init_db zavolá metadata.create_all – vytvoří jen tabulky, které chybí
    init_db(engine)
    log.info("Schéma OK (listings_price_history vytvořena nebo už existovala).")

    # Naplň historii aktuálními cenami ze stávajících listings,
    # ale jen pro ty, které v historii ještě žádný záznam nemají.
    with engine.begin() as conn:
        # Zjisti, kolik listings nemá žádný záznam v historii
        result = conn.execute(text("""
            SELECT COUNT(*) FROM autobazary.listings l
            WHERE NOT EXISTS (
                SELECT 1 FROM autobazary.listings_price_history h
                WHERE h.listing_id = l.id
            )
        """)).scalar_one()

        if result == 0:
            log.info("Historie je již naplněna, nic k doplnění.")
            return

        log.info("Doplňuji počáteční ceny pro %d inzerátů bez záznamu v historii...", result)

        conn.execute(text("""
            INSERT INTO autobazary.listings_price_history (listing_id, price, price_currency, recorded_at)
            SELECT
                l.id,
                l.price,
                l.price_currency,
                l.first_seen
            FROM autobazary.listings l
            WHERE NOT EXISTS (
                SELECT 1 FROM autobazary.listings_price_history h
                WHERE h.listing_id = l.id
            )
        """))

        log.info("Hotovo. Vloženo %d počátečních záznamů.", result)


def _safe_dsn() -> str:
    dsn = config.database_url
    if "@" in dsn:
        return dsn.split("@", 1)[0].rsplit(":", 1)[0] + ":***@" + dsn.split("@", 1)[1]
    return dsn


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        log.error("Migrace selhala: %s", exc, exc_info=True)
        sys.exit(1)
