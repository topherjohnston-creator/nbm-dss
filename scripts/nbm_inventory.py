"""
nbm_inventory.py
----------------
Fetches the NBM GRIB2 index files for the most recent cycle and dumps
every available field to data/nbm_inventory.json.

This is a one-time diagnostic script to confirm exactly which probability
and percentile fields exist in the NBM v5.0 parallel (noaa-nbm-para-pds)
and operational (noaa-nbm-grib2-pds) datasets before building the full
extraction pipeline.

Output: data/nbm_inventory.json
"""

import json
import os
from datetime import datetime, timezone, timedelta
from herbie import Herbie

# ── KRNO grid point ─────────────────────────────────────────────────────────
KRNO_LAT = 39.4986
KRNO_LON = -119.7681

# ── How many forecast hours to inventory ────────────────────────────────────
# We check f001, f006, f012, f024 to see which fields appear at each interval
FORECAST_HOURS = [1, 6, 12, 24]

# ── NBM products to check ───────────────────────────────────────────────────
# 'co' = CONUS
# We check both core and qmd (quantile-mapped distribution) files
PRODUCTS = ['core', 'qmd']

# ── Sources to check ────────────────────────────────────────────────────────
# 'aws'  = noaa-nbm-grib2-pds (operational)
# 'para' = noaa-nbm-para-pds  (NBM v5.0 parallel)
# Herbie uses 'aws' for operational; for parallel we override the S3 bucket
SOURCES = ['aws']

def get_latest_cycle():
    """Return the most recent NBM cycle (rounds down to nearest hour)."""
    now = datetime.now(timezone.utc)
    # NBM runs every hour; subtract 2h to ensure data is available
    cycle = now - timedelta(hours=2)
    return cycle.replace(minute=0, second=0, microsecond=0)

def inventory_fields(cycle, fxx, product, source):
    """
    Return the full inventory DataFrame for one NBM file.
    Each row is one GRIB2 message with search string, variable, level, etc.
    """
    try:
        H = Herbie(
            cycle.strftime('%Y-%m-%d %H:%M'),
            model='nbm',
            product=product,
            fxx=fxx,
            source=source,
            verbose=False,
            save_dir='/tmp/herbie_cache',
        )
        inv = H.inventory()
        return inv
    except Exception as e:
        print(f"  ✗ {product} f{fxx:03d} [{source}]: {e}")
        return None

def main():
    os.makedirs('data', exist_ok=True)

    cycle = get_latest_cycle()
    print(f"Inventorying NBM cycle: {cycle.strftime('%Y-%m-%d %H:00 UTC')}")

    results = {
        'cycle': cycle.strftime('%Y-%m-%dT%H:00:00Z'),
        'krno_lat': KRNO_LAT,
        'krno_lon': KRNO_LON,
        'products': {}
    }

    for product in PRODUCTS:
        results['products'][product] = {}
        for fxx in FORECAST_HOURS:
            print(f"\n  → {product} f{fxx:03d}")
            inv = inventory_fields(cycle, fxx, product, SOURCES[0])
            if inv is None:
                results['products'][product][f'f{fxx:03d}'] = {'error': 'fetch failed'}
                continue

            # Serialize the inventory rows we care about
            fields = []
            for _, row in inv.iterrows():
                fields.append({
                    'search_this': str(row.get('search_this', '')),
                    'var':         str(row.get('var', '')),
                    'level':       str(row.get('level', '')),
                    'forecast':    str(row.get('forecast', '')),
                    'description': str(row.get('name', row.get('description', ''))),
                })
                print(f"    {row.get('var','?'):20s} {row.get('level','?'):40s} {row.get('name', row.get('description',''))}")

            results['products'][product][f'f{fxx:03d}'] = {
                'field_count': len(fields),
                'fields': fields
            }

    # ── Highlight fields relevant to our hazard table ─────────────────────
    HAZARD_KEYWORDS = [
        # Wind
        'GUST', 'WIND', 'WINDPROB',
        # Precip
        'APCP', 'ASNOW', 'FICEAC', 'ICPRB',
        # Temperature
        'TMAX', 'TMIN', 'TMP', 'APTMP',
        # Visibility
        'VIS', 'CEIL',
        # Lightning
        'TSTM',
        # Wet bulb / flash freeze
        'WETGLBT', 'WBGT',
        # Probability fields
        'PROB', 'PTYPE', 'PWTHER',
    ]

    relevant = {}
    for product, fxx_data in results['products'].items():
        for fxx_key, data in fxx_data.items():
            if 'fields' not in data:
                continue
            for field in data['fields']:
                var = field.get('var', '').upper()
                if any(kw in var for kw in HAZARD_KEYWORDS):
                    key = f"{product}/{fxx_key}/{var}"
                    if key not in relevant:
                        relevant[key] = field

    results['hazard_relevant_fields'] = list(relevant.values())

    print(f"\n\n{'='*60}")
    print(f"HAZARD-RELEVANT FIELDS FOUND ({len(relevant)}):")
    print(f"{'='*60}")
    for key, field in relevant.items():
        print(f"  {key:50s}  {field.get('level','')}")

    # Write output
    with open('data/nbm_inventory.json', 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n✓ Inventory written to data/nbm_inventory.json")
    print(f"  Total fields inventoried: {sum(d.get('field_count',0) for p in results['products'].values() for d in p.values())}")

if __name__ == '__main__':
    main()
