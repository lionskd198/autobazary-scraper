# Autobazary scraper

Scraper inzerátů ojetých aut ze tří českých portálů do **PostgreSQL**:

| Portál | Zdroj (`source`) | Metoda | Poznámka |
|--------|------------------|--------|----------|
| [Sauto.cz](https://www.sauto.cz) | `sauto` | JSON API (GET) | SPA – čte se interní `/api/v1/items/search` |
| [TipCars.com](https://www.tipcars.com) | `tipcars` | JSON API (POST) | SPA – POST na `/offer/search/...`, odpověď `{items: HTML}` |
| [auto.Bazoš.cz](https://auto.bazos.cz) | `bazos` | HTML | nejjednodušší, referenční |

Ověřeno živě (srpen 2026): jeden běh `--pages 1` = 20 + 100 + 20 = 140 inzerátů,
opakovaný běh díky upsertu neduplikuje.

Data se ukládají do jedné tabulky `listings` s deduplikací na `(source, source_id)`
– opakovaný běh existující inzeráty **aktualizuje** (cena, nájezd, `last_seen`),
nové přidá.

## Rychlý start

```bash
# 1) závislosti (doporučen virtuální env)
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell
pip install -r requirements.txt

# 2) databáze – buď přes Docker (publikuje na host port 5433)...
docker compose up -d
# ...nebo použij vlastní Postgres a uprav DATABASE_URL v .env

# 3) konfigurace
copy .env.example .env         # Windows  (Linux/macOS: cp)

# 4) inicializace schématu
python -m autobazary.run init-db

# 5) scrape
python -m autobazary.run scrape --sources bazos --pages 3
python -m autobazary.run scrape --sources all --query octavia --pages 5

# 6) souhrn
python -m autobazary.run stats
```

## CLI

| Příkaz | Co dělá |
|--------|---------|
| `init-db` | vytvoří tabulku `listings` |
| `scrape` | stáhne inzeráty a upsertne do DB |
| `dump <source>` | uloží první stránku zdroje do `dumps/` pro ladění selektorů |
| `stats` | počty a průměrné ceny podle portálu |

Volby `scrape`: `--sources sauto,tipcars,bazos|all`, `--query <text>`,
`--pages <n>`, `--detail` (stahovat i detailní stránky – pomalejší).

Chování jde nastavit i v `.env` (`SCRAPE_DELAY`, `SCRAPE_MAX_PAGES`,
`SCRAPE_DETAIL`, `USER_AGENT`, …).

## Testy

```bash
python tests_smoke.py            # offline: parsery + upsert/dedup (SQLite in-memory)
python tests_parse_dump.py sauto # ověří parser proti staženému dumpu v dumps/
```

## Struktura

```
autobazary/
├─ config.py          # konfigurace z .env
├─ models.py          # dataclass Listing + parsery (cena, rok, nájezd…)
├─ db.py              # SQLAlchemy schéma + upsert (ON CONFLICT)
├─ http.py            # HTTP klient: rate-limit + retry
├─ run.py             # CLI
└─ scrapers/
   ├─ base.py         # BaseScraper (orchestrace, stránkování, dedup)
   ├─ sauto.py
   ├─ tipcars.py
   └─ bazos.py
```

## Když scraper vrátí 0 inzerátů

Portály občas mění HTML/API. Postup:

1. `python -m autobazary.run dump <source>` – uloží syrovou odpověď do `dumps/`
   (u `sauto`/`tipcars` jako `.json`, u `bazos` jako `.html`).
2. Otevři soubor a najdi aktuální selektory (u Sauta/TipCars tvar JSON).
3. Uprav **jen** `parse_list_page` / `_card_to_listing` (HTML) resp.
   `_item_to_listing` (Sauto). Selektory jsou schválně na jednom místě.

### TLS za firemní proxy

Klient používá balíček `truststore`, takže bere kořenové certifikáty ze
systémového úložiště OS (řeší „CERTIFICATE_VERIFY_FAILED" za proxy s TLS
inspekcí). Pokud `truststore` chybí, spadne se na výchozí certifi.

## Právní / etické okénko

Scrapuj s rozumem: dodržuj `robots.txt`, drž nízkou frekvenci (výchozí prodleva
1,5 s), nestahuj osobní údaje a data používej v souladu s podmínkami portálů.
Výchozí nastavení je záměrně šetrné k serverům.
```
