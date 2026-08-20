"""PostgreSQL vrstva – schéma tabulky ``listings`` a idempotentní upsert."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.engine import Engine

from .config import config
from .models import Listing

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


def get_engine() -> Engine:
    return create_engine(config.database_url, future=True, pool_pre_ping=True)


def init_db(engine: Engine | None = None) -> None:
    """Vytvoří tabulku, pokud neexistuje."""
    engine = engine or get_engine()
    metadata.create_all(engine)


def upsert_listings(rows: Iterable[Listing], engine: Engine | None = None) -> int:
    """Vloží/aktualizuje inzeráty. Konflikt na (source, source_id) => update.

    Vrací počet zpracovaných řádků. ``first_seen`` se při updatu nemění,
    ``last_seen`` a měnitelná pole (cena, nájezd, …) se aktualizují.
    """
    engine = engine or get_engine()
    payload = [_to_db_row(r) for r in rows]
    if not payload:
        return 0

    now = datetime.now(timezone.utc)
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
    )
    with engine.begin() as conn:
        conn.execute(stmt)
    return len(payload)


def _to_db_row(listing: Listing) -> dict:
    row = listing.as_row()
    # scraped_at drž jako aware datetime; ostatní pole jdou 1:1
    return row
