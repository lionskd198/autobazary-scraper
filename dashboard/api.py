"""FastAPI dashboard API – čte z autobazary.listings + listings_price_history."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, text

# ── DB ────────────────────────────────────────────────────────────────────────
_raw_url = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URI", "postgresql+psycopg://autobazary:autobazary@localhost:5433/autobazary"),
)
# Oprava prefixu pro psycopg3
if _raw_url.startswith("postgresql://") or _raw_url.startswith("postgres://"):
    _raw_url = _raw_url.replace("://", "+psycopg://", 1)

engine = create_engine(_raw_url, pool_pre_ping=True, future=True)

app = FastAPI(title="Autobazary dashboard API", docs_url="/api/docs")

# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/listings")
def get_listings(
    price_min: int | None = None,
    price_max: int | None = None,
    km_min: int | None = None,
    km_max: int | None = None,
    body_type: str | None = None,
    fuel: str | None = None,
    source: str | None = None,          # comma-separated: sauto,tipcars,bazos
    seller_type: str | None = None,
    age: str | None = None,             # fresh|aging|stale|updated
    sort: str = "last_seen_desc",
    limit: int = Query(default=50, le=200),
    offset: int = 0,
) -> JSONResponse:

    conditions = ["1=1"]
    params: dict[str, Any] = {}

    if price_min is not None:
        conditions.append("price >= :price_min")
        params["price_min"] = price_min
    if price_max is not None:
        conditions.append("price <= :price_max")
        params["price_max"] = price_max
    if km_min is not None:
        conditions.append("mileage_km >= :km_min")
        params["km_min"] = km_min
    if km_max is not None:
        conditions.append("mileage_km <= :km_max")
        params["km_max"] = km_max
    if body_type:
        conditions.append("body_type = :body_type")
        params["body_type"] = body_type
    if fuel:
        conditions.append("fuel = :fuel")
        params["fuel"] = fuel
    if source:
        sources = [s.strip() for s in source.split(",") if s.strip()]
        conditions.append(f"source = ANY(:sources)")
        params["sources"] = sources
    if seller_type:
        conditions.append("seller_type = :seller_type")
        params["seller_type"] = seller_type

    # Stáří inzerátu
    if age == "fresh":
        conditions.append("first_seen >= NOW() - INTERVAL '7 days'")
    elif age == "aging":
        conditions.append("first_seen BETWEEN NOW() - INTERVAL '29 days' AND NOW() - INTERVAL '8 days'")
    elif age == "stale":
        conditions.append("first_seen <= NOW() - INTERVAL '30 days'")
    elif age == "updated":
        conditions.append("EXTRACT(EPOCH FROM (last_seen - first_seen)) > 172800")  # >2 dny rozdíl

    if q:
        conditions.append("title ILIKE :q")
        params["q"] = f"%{q}%"

    sort_map = {
        "price_asc": "price ASC NULLS LAST",
        "price_desc": "price DESC NULLS LAST",
        "km_asc": "mileage_km ASC NULLS LAST",
        "year_desc": "year DESC NULLS LAST",
        "year_asc": "year ASC NULLS LAST",
        "last_seen_desc": "last_seen DESC",
        "first_seen_desc": "first_seen DESC",
    }
    order = sort_map.get(sort, "last_seen DESC")
    where = " AND ".join(conditions)

    sql = text(f"""
        SELECT
            l.id, l.source, l.source_id, l.url, l.title,
            l.brand, l.model, l.year, l.price, l.price_currency,
            l.mileage_km, l.fuel, l.transmission, l.power_kw,
            l.body_type, l.location, l.seller_type,
            l.first_seen, l.last_seen,
            -- Předchozí cena z price_history (předposlední záznam)
            (
                SELECT ph.price FROM autobazary.listings_price_history ph
                WHERE ph.listing_id = l.id
                ORDER BY ph.id DESC
                LIMIT 1 OFFSET 1
            ) AS prev_price,
            -- Počet změn ceny
            (
                SELECT COUNT(*) FROM autobazary.listings_price_history ph
                WHERE ph.listing_id = l.id
            ) AS price_changes,
            -- Počet dní na inzertním trhu
            EXTRACT(DAY FROM NOW() - l.first_seen)::int AS days_listed,
            -- Byl aktualizován (last_seen výrazně novější než first_seen)
            EXTRACT(EPOCH FROM (l.last_seen - l.first_seen)) > 172800 AS was_updated
        FROM autobazary.listings l
        WHERE {where}
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
    """)

    count_sql = text(f"""
        SELECT COUNT(*) FROM autobazary.listings l WHERE {where}
    """)

    params["limit"] = limit
    params["offset"] = offset

    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
        total = conn.execute(count_sql, {k: v for k, v in params.items() if k not in ("limit", "offset")}).scalar_one()

    return JSONResponse({
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_serialize(r) for r in rows],
    })


@app.get("/api/stats")
def get_stats() -> JSONResponse:
    sql = text("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE first_seen >= NOW() - INTERVAL '7 days') AS fresh,
            COUNT(*) FILTER (WHERE first_seen <= NOW() - INTERVAL '30 days') AS stale,
            COUNT(*) FILTER (WHERE source = 'sauto') AS sauto,
            COUNT(*) FILTER (WHERE source = 'tipcars') AS tipcars,
            COUNT(*) FILTER (WHERE source = 'bazos') AS bazos,
            ROUND(AVG(price)) FILTER (WHERE price IS NOT NULL) AS avg_price,
            MIN(price) FILTER (WHERE price IS NOT NULL) AS min_price,
            MAX(price) FILTER (WHERE price IS NOT NULL) AS max_price,
            MAX(last_seen) AS last_scraped
        FROM autobazary.listings
    """)
    with engine.connect() as conn:
        row = conn.execute(sql).mappings().one()
    return JSONResponse(dict(row))


def _serialize(row: Any) -> dict:
    d = dict(row)
    for k in ("first_seen", "last_seen", "last_scraped"):
        if k in d and d[k] is not None:
            d[k] = d[k].isoformat()
    for k in ("price_changes", "days_listed"):
        if k in d and d[k] is not None:
            d[k] = int(d[k])
    return d


# ── Statický HTML dashboard ───────────────────────────────────────────────────
_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(_HTML)
