"""Rychlý offline smoke test parserů a DB upsertu (SQLite in-memory)."""
from autobazary.models import (
    Listing, parse_price, parse_year, parse_mileage, parse_power_kw,
    normalize_fuel, clean_text,
)

cases = [
    ("parse_price '1 250 000 Kč'", parse_price("1 250 000 Kč"), 1_250_000),
    ("parse_price 'Dohodou'", parse_price("Dohodou"), None),
    ("parse_year 'Octavia 2018 TDI'", parse_year("Škoda Octavia 2018 1.6 TDI"), 2018),
    ("parse_mileage '145 000 km'", parse_mileage("145 000 km"), 145_000),
    ("parse_power_kw '110 kW'", parse_power_kw("110 kW"), 110),
    ("parse_power_kw '150 k' (HP->kW)", parse_power_kw("150 k"), 110),
    ("normalize_fuel 'Diesel'", normalize_fuel("Diesel"), "nafta"),
    ("clean_text whitespace", clean_text("  a   b\n c "), "a b c"),
]

ok = True
for name, got, exp in cases:
    status = "OK  " if got == exp else "FAIL"
    if got != exp:
        ok = False
    print(f"{status} {name}: {got!r} (exp {exp!r})")

# DB vrstva proti SQLite (bez potřeby Postgresu) – ověří schéma + upsert logiku.
from sqlalchemy import create_engine, select, func
from autobazary import db

eng = create_engine("sqlite://")
db.init_db(eng)
rows = [
    Listing(source="bazos", source_id="1", url="http://x/1", title="Auto 1", price=100_000),
    Listing(source="bazos", source_id="2", url="http://x/2", title="Auto 2", price=200_000),
]
n = db.upsert_listings(rows, eng)

# --- price history: první scrape -> 1 záznam na inzerát ---
with eng.connect() as conn:
    hist_count = conn.execute(
        select(func.count()).select_from(db.listings_price_history)
    ).scalar_one()
status = "OK  " if hist_count == 2 else "FAIL"
if hist_count != 2:
    ok = False
print(f"{status} price_history: po prvním scrape {hist_count} záznamů (exp 2)")

# --- druhý běh: cena #1 klesla, cena #2 beze změny ---
rows[0].price = 90_000
db.upsert_listings(rows, eng)

with eng.connect() as conn:
    total = conn.execute(select(func.count()).select_from(db.listings)).scalar_one()
    price1 = conn.execute(
        select(db.listings.c.price).where(db.listings.c.source_id == "1")
    ).scalar_one()
    # listings #1 má mít 2 záznamy v historii (100k -> 90k), #2 stále jen 1
    listing1_id = conn.execute(
        select(db.listings.c.id).where(db.listings.c.source_id == "1")
    ).scalar_one()
    listing2_id = conn.execute(
        select(db.listings.c.id).where(db.listings.c.source_id == "2")
    ).scalar_one()
    hist1 = conn.execute(
        select(func.count()).select_from(db.listings_price_history)
        .where(db.listings_price_history.c.listing_id == listing1_id)
    ).scalar_one()
    hist2 = conn.execute(
        select(func.count()).select_from(db.listings_price_history)
        .where(db.listings_price_history.c.listing_id == listing2_id)
    ).scalar_one()
    # ceny v historii pro #1: nejnovější musí být 90_000
    prices1 = conn.execute(
        select(db.listings_price_history.c.price)
        .where(db.listings_price_history.c.listing_id == listing1_id)
        .order_by(db.listings_price_history.c.id)
    ).scalars().all()

print(f"{'OK  ' if total == 2 and price1 == 90_000 else 'FAIL'} upsert: total={total} (exp 2), cena#1={price1} (exp 90000)")
if total != 2 or price1 != 90_000:
    ok = False

print(f"{'OK  ' if hist1 == 2 else 'FAIL'} price_history #1: {hist1} záznamy (exp 2) – ceny: {prices1} (exp [100000, 90000])")
if hist1 != 2 or list(prices1) != [100_000, 90_000]:
    ok = False

print(f"{'OK  ' if hist2 == 1 else 'FAIL'} price_history #2: {hist2} záznam (exp 1) – cena beze změny, žádný nový řádek")
if hist2 != 1:
    ok = False

# --- třetí běh: stejné ceny -> žádný nový záznam v historii ---
db.upsert_listings(rows, eng)
with eng.connect() as conn:
    hist_total = conn.execute(
        select(func.count()).select_from(db.listings_price_history)
    ).scalar_one()
print(f"{'OK  ' if hist_total == 3 else 'FAIL'} price_history: po třetím scrape (ceny beze změny) {hist_total} celkem (exp 3)")
if hist_total != 3:
    ok = False

print("\nALL PASS" if ok else "\nSOME FAILED")
raise SystemExit(0 if ok else 1)
