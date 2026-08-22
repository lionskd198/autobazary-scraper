"""CLI: spouštění scraperů a správa databáze.

Použití:
    python -m autobazary.run init-db
    python -m autobazary.run scrape --sources sauto,tipcars,bazos --query octavia --pages 3
    python -m autobazary.run scrape --detail
    python -m autobazary.run dump bazos        # uloží HTML/JSON první stránky k inspekci
    python -m autobazary.run stats
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import func, select

from .config import config
from .db import get_engine, init_db, listings, upsert_listings
from .scrapers import SCRAPERS

log = logging.getLogger("autobazary")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_init_db(_args) -> int:
    init_db()
    log.info("Tabulka 'listings' připravena v %s", _safe_dsn())
    return 0


def cmd_scrape(args) -> int:
    sources = _resolve_sources(args.sources)
    if not sources:
        return 2

    engine = get_engine()
    init_db(engine)  # jistota, že schéma existuje

    total = 0
    batch = []
    BATCH_SIZE = 50

    for name in sources:
        scraper = SCRAPERS[name]()
        log.info("=== Scrapuji %s (pages=%s, detail=%s) ===",
                 name, args.pages or config.max_pages, args.detail or config.fetch_detail)
        try:
            for listing in scraper.scrape(
                query=args.query,
                max_pages=args.pages,
                fetch_detail=args.detail or None,
                price_min=args.price_min if hasattr(args, "price_min") else None,
                price_max=args.price_max if hasattr(args, "price_max") else None,
            ):
                batch.append(listing)
                total += 1
                if len(batch) >= BATCH_SIZE:
                    upsert_listings(batch, engine)
                    batch.clear()
        except KeyboardInterrupt:
            log.warning("Přerušeno uživatelem – ukládám rozpracovanou dávku.")
            break
        finally:
            scraper.close()

    if batch:
        upsert_listings(batch, engine)

    log.info("Hotovo. Zpracováno %d inzerátů (upsert do DB).", total)
    return 0


def cmd_dump(args) -> int:
    """Stáhne první stránku daného zdroje a uloží pro ladění selektorů."""
    name = args.source
    if name not in SCRAPERS:
        log.error("Neznámý zdroj '%s'. Dostupné: %s", name, ", ".join(SCRAPERS))
        return 2
    scraper = SCRAPERS[name]()
    try:
        text = scraper.fetch_first_page(args.query)
    finally:
        scraper.close()

    out_dir = Path("dumps")
    out_dir.mkdir(exist_ok=True)
    ext = "json" if name in ("sauto", "tipcars") else "html"
    out = out_dir / f"{name}.{ext}"
    out.write_text(text, encoding="utf-8")
    log.info("Uloženo %d znaků -> %s", len(text), out)
    return 0


def cmd_stats(_args) -> int:
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(listings)).scalar_one()
        print(f"Celkem inzerátů: {total}")
        rows = conn.execute(
            select(listings.c.source, func.count(), func.avg(listings.c.price))
            .group_by(listings.c.source)
        ).all()
        for src, cnt, avg in rows:
            avg_str = f"{int(avg):,} Kč".replace(",", " ") if avg else "-"
            print(f"  {src:10s} {cnt:6d} inzerátů, prům. cena {avg_str}")
    return 0


def _resolve_sources(raw: str | None) -> list[str]:
    if not raw or raw == "all":
        return list(SCRAPERS)
    wanted = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in SCRAPERS]
    if unknown:
        log.error("Neznámé zdroje: %s. Dostupné: %s", ", ".join(unknown), ", ".join(SCRAPERS))
        return []
    return wanted


def _safe_dsn() -> str:
    # neloguj heslo
    dsn = config.database_url
    if "@" in dsn:
        return dsn.split("@", 1)[0].rsplit(":", 1)[0] + ":***@" + dsn.split("@", 1)[1]
    return dsn


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autobazary", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true", help="debug logy")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="vytvoří tabulku listings").set_defaults(func=cmd_init_db)

    sp = sub.add_parser("scrape", help="spustí scraping a uloží do DB")
    sp.add_argument("--sources", default="all", help="čárkou oddělené: sauto,tipcars,bazos nebo 'all'")
    sp.add_argument("--query", default=None, help="fulltext dotaz (kde portál podporuje)")
    sp.add_argument("--pages", type=int, default=None, help="max stránek na portál")
    sp.add_argument("--detail", action="store_true", help="stahovat i detailní stránky")
    sp.add_argument("--price-min", type=int, default=None, dest="price_min",
                    help="minimální cena v Kč (kde portál podporuje)")
    sp.add_argument("--price-max", type=int, default=None, dest="price_max",
                    help="maximální cena v Kč (kde portál podporuje)")
    sp.set_defaults(func=cmd_scrape)

    dp = sub.add_parser("dump", help="uloží první stránku zdroje k inspekci")
    dp.add_argument("source", choices=list(SCRAPERS))
    dp.add_argument("--query", default=None)
    dp.set_defaults(func=cmd_dump)

    sub.add_parser("stats", help="souhrn uložených dat").set_defaults(func=cmd_stats)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        log.error("Chyba: %s", exc, exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
