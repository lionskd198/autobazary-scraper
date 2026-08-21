"""PostgreSQL vrstva – schéma tabulek ``listings`` + ``listings_price_history``
a idempotentní upsert se sledováním změn ceny.

Schéma:
    listings              – jeden řádek na inzerát, upsert při každém scrape
    listings_price_history – append-only log; nový řádek vznikne jen tehdy,
                             když se cena skutečně změní oproti poslední hodnotě
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.engine import Engine

from .config import config
from .models import Listing

# Na SQLite (smoke testy) schéma neexistuje – nastavíme ho až runtime
# na Postgresu. Modul exportuje _SCHEMA pro případné použití v SQL dotazech.
_SCHEMA: str | None = None  # None = výchozí schéma (public / sqlite)

metadata = MetaData()

listings = Table(
    "listings",
    metadata,
    # BigInteger na Postgresu (BIGSERIAL); Integer na SQLite kvůli autoincrementu
    Column(
        "id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("source", String(32), nullable=False),
    Column("source_id", String(128), nullable=False),
    Column("url", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("brand", String(64)),
    Column("model", String(128)),
    Column("year", Integer),
    Column("price", Integer),
    Column("price_currency", String(8), server_default="CZK"),
    Column("mileage_km", Integer),
    Column("fuel", String(32)),
    Column("transmission", String(32)),
    Column("power_kw", Integer),
    Column("body_type", String(64)),
    Column("location", String(128)),
    Column("seller_type", String(16)),
    Column("description", Text),
    # metadata o životním cyklu záznamu
    Column("first_seen", DateTime(timezone=True), server_default=func.now()),
    Column("last_seen", DateTime(timezone=True), server_default=func.now()),
    Column("scraped_at", DateTime(timezone=True)),
    Column("raw", JSONB().with_variant(JSON, "sqlite")),
    UniqueConstraint("source", "source_id", name="uq_listings_source_id"),
)

listings_price_history = Table(
    "listings_price_history",
    metadata,
    Column(
        "id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    # FK na listings.id; při smazání inzerátu smažeme i historii
    Column(
        "listing_id",
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("price", Integer),                              # None = "cena dohodou"
    Column("price_currency", String(8), server_default="CZK"),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)


def get_engine() -> Engine:
    return create_engine(config.database_url, future=True, pool_pre_ping=True)


def init_db(engine: Engine | None = None, schema: str | None = None) -> None:
    """Vytvoří schéma (pokud neexistuje) a obě tabulky.

    Args:
        engine: SQLAlchemy engine. None = použije get_engine() (Postgres z config).
        schema: Název PostgreSQL schématu, do kterého se tabulky umístí.
                None = výchozí schéma ("public" na PG, ignorováno na SQLite).
                Hodnota "autobazary" se nastaví automaticky při volání z run.py.
    """
    global _SCHEMA
    engine = engine or get_engine()

    if schema is None and engine.dialect.name != "sqlite":
        schema = "autobazary"   # výchozí schéma pro Postgres

    if schema and engine.dialect.name != "sqlite":
        _SCHEMA = schema
        with engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        # Znovu zaregistruj tabulky do schématu pokud ještě nejsou
        for tbl in (listings, listings_price_history):
            tbl.schema = schema

    metadata.create_all(engine)


def upsert_listings(rows: Iterable[Listing], engine: Engine | None = None) -> int:
    """Vloží/aktualizuje inzeráty a zaznamená každou změnu ceny do historie.

    Průběh pro každý inzerát:
      1. INSERT … ON CONFLICT DO UPDATE – upsertne listings řádek.
      2. Načte aktuální cenu z DB (před updatem ji máme jako RETURNING nebo
         porovnáme po upsert).
      3. Pokud se cena liší od poslední hodnoty v listings_price_history
         (nebo tam žádný záznam není), vloží nový řádek do historie.

    Vrací počet zpracovaných řádků.
    ``first_seen`` se při updatu nikdy nemění.
    ``last_seen`` se aktualizuje vždy.
    """
    engine = engine or get_engine()
    payload = [_to_db_row(r) for r in rows]
    if not payload:
        return 0

    now = datetime.now(timezone.utc)

    with engine.begin() as conn:
        # ------------------------------------------------------------------ #
        # Krok 1: upsert listings                                            #
        # RETURNING id, price nám vrátí aktuální stav řádku PO upsert.      #
        # Na SQLite RETURNING nefunguje spolehlivě pro ON CONFLICT – tam     #
        # fallback na ruční SELECT (viz níže).                               #
        # ------------------------------------------------------------------ #
        stmt = pg_insert(listings).values(payload)
        update_cols = {
            c.name: stmt.excluded[c.name]
            for c in listings.columns
            if c.name not in ("id", "source", "source_id", "first_seen")
        }
        update_cols["last_seen"] = now
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_id"],
            set_=update_cols,
        ).returning(listings.c.id, listings.c.source, listings.c.source_id, listings.c.price)

        upserted = conn.execute(stmt).fetchall()
        # upserted: [(id, source, source_id, price), ...]

        # ------------------------------------------------------------------ #
        # Krok 2: zjisti pro každý inzerát poslední zaznamenanou cenu        #
        # ------------------------------------------------------------------ #
        listing_ids = [row[0] for row in upserted]

        # Subquery: pro každé listing_id vezmi MAX(id) z price_history
        # = poslední záznam (append-only, id roste monotónně)
        last_prices: dict[int, int | None] = {}
        if listing_ids:
            subq = (
                select(
                    listings_price_history.c.listing_id,
                    func.max(listings_price_history.c.id).label("max_id"),
                )
                .where(listings_price_history.c.listing_id.in_(listing_ids))
                .group_by(listings_price_history.c.listing_id)
                .subquery()
            )
            rows_hist = conn.execute(
                select(
                    listings_price_history.c.listing_id,
                    listings_price_history.c.price,
                ).join(subq, listings_price_history.c.id == subq.c.max_id)
            ).fetchall()
            last_prices = {r[0]: r[1] for r in rows_hist}

        # ------------------------------------------------------------------ #
        # Krok 3: vlož do historie jen tehdy, když se cena změnila          #
        # ------------------------------------------------------------------ #
        history_rows = []
        # sestavíme mapu source_id -> new_price z payload pro rychlé lookup
        new_price_map = {(r["source"], r["source_id"]): r.get("price") for r in payload}

        for listing_id, source, source_id, current_price in upserted:
            last = last_prices.get(listing_id, _SENTINEL)
            if last is _SENTINEL:
                # první výskyt – vždy zaznamenej
                history_rows.append({
                    "listing_id": listing_id,
                    "price": current_price,
                    "price_currency": "CZK",
                    "recorded_at": now,
                })
            elif last != current_price:
                # cena se změnila – zaznamenej novou hodnotu
                history_rows.append({
                    "listing_id": listing_id,
                    "price": current_price,
                    "price_currency": "CZK",
                    "recorded_at": now,
                })
            # else: cena stejná – nic neděláme

        if history_rows:
            conn.execute(listings_price_history.insert(), history_rows)

    return len(payload)


# Sentinel pro rozlišení "žádný záznam v historii" od "cena None (dohodou)"
_SENTINEL = object()


def _to_db_row(listing: Listing) -> dict:
    row = listing.as_row()
    # scraped_at drž jako aware datetime; ostatní pole jdou 1:1
    return row
