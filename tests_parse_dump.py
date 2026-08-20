"""Ověří parse_list_page proti reálně staženému HTML/JSON v dumps/."""
import sys
from pathlib import Path

from autobazary.scrapers import SCRAPERS

name = sys.argv[1] if len(sys.argv) > 1 else "bazos"
ext = "json" if name in ("sauto", "tipcars") else "html"
path = Path("dumps") / f"{name}.{ext}"
if not path.exists():
    print(f"Nejdřív spusť: python -m autobazary.run dump {name}")
    raise SystemExit(2)

html = path.read_text(encoding="utf-8")
scraper = SCRAPERS[name]()
items = list(scraper.parse_list_page(html))
scraper.close()

print(f"[{name}] parse_list_page -> {len(items)} inzerátů\n")
for it in items[:5]:
    print(f"  #{it.source_id} | {it.title[:55]:55s} | "
          f"cena={it.price} | rok={it.year} | km={it.mileage_km} | {it.url}")
print(f"\n{'OK' if items else 'ŽÁDNÉ INZERÁTY – selektory potřebují úpravu'}")
raise SystemExit(0 if items else 1)
