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
# druhý běh se změněnou cenou -> update, ne duplicita
rows[0].price = 90_000
db.upsert_listings(rows, eng)
with eng.connect() as conn:
    total = conn.execute(select(func.count()).select_from(db.listings)).scalar_one()
    price1 = conn.execute(
        select(db.listings.c.price).where(db.listings.c.source_id == "1")
    ).scalar_one()
print(f"OK   upsert vložil {n} řádků; po re-runu total={total} (exp 2), cena#1={price1} (exp 90000)")
if total != 2 or price1 != 90_000:
    ok = False

print("\nALL PASS" if ok else "\nSOME FAILED")
raise SystemExit(0 if ok else 1)
