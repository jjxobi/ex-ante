"""Rebuild the local measurement cache from GeoNet.

The cached catalogues under this directory are gitignored: 97 MB of raw CSV has
no business in a repository whose premise is that anyone can clone it. That
means they are not recoverable from git, and a script that needs them has to be
able to rebuild them.

That is not hypothetical. The cache was destroyed once by a botched cleanup, and
the only reason nothing was permanently lost is that this project's rule is that
every committed number is regenerable by a committed script. This is that
script for the raw inputs.

Run it when the cache is missing and you need to re-run a measurement that
depends on bulk data, for example region_rule_regeneration.py.
"""

import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "src")
from eq import geonet, paths  # noqa: E402

# The region rule (D1) is measured on 2021 to 2026 at M1.5 and above. Other
# scripts pull wider spans; those fetch on demand through their own caching.
YEARS = range(2021, 2026)
MIN_MAGNITUDE = 1.5


def fetch_year(year: int, destination: Path) -> int:
    from datetime import date

    url = geonet.build_url(MIN_MAGNITUDE, date(year, 1, 1), date(year + 1, 1, 1))
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=600) as response:
                body = response.read().decode("utf-8")
            break
        except Exception as error:
            wait = 5 * 2**attempt
            print(f"    retry {attempt + 1} after {type(error).__name__}, {wait}s")
            time.sleep(wait)
    else:
        raise RuntimeError(f"could not fetch {year}")

    destination.write_text(body, encoding="utf-8", newline="")
    return len(body.strip().splitlines()) - 1


def main() -> int:
    paths.MEASUREMENTS_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for year in YEARS:
        target = paths.MEASUREMENTS_DIR / f"cat_1.5_{year}_{year + 1}.csv"
        if target.exists():
            print(f"{year}: already cached")
            continue
        count = fetch_year(year, target)
        total += count
        print(f"{year}: {count:,} events")
        time.sleep(3)
    print(f"\ncache rebuilt, {total:,} events fetched")
    print("Now run region_rule_regeneration.py to re-verify the frozen grid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
