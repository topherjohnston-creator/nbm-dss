"""
nbm_inventory.py
----------------
Fetches the NBM GRIB2 index files for the most recent cycle and dumps
every available field to data/nbm_inventory.json.

Uses direct HTTP requests to fetch the .idx index files from AWS S3,
which avoids any Herbie configuration issues.

Output: data/nbm_inventory.json
"""

import json
import os
import re
import requests
from datetime import datetime, timezone, timedelta

# ── KRNO grid point ─────────────────────────────────────────────────────────
KRNO_LAT = 39.4986
KRNO_LON = -119.7681

# ── NBM AWS S3 base URLs ─────────────────────────────────────────────────────
# Operational NBM
AWS_BASE = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
# Parallel NBM v5.0
PARA_BASE = "https://noaa-nbm-para-pds.s3.amazonaws.com"

# ── Forecast hours to inventory ──────────────────────────────────────────────
FORECAST_HOURS = [1, 6, 12, 24]

# ── Products to check ────────────────────────────────────────────────────────
PRODUCTS = ['core', 'qmd']

# ── Hazard keywords to flag ───────────────────────────────────────────────────
HAZARD_KEYWORDS = [
    'GUST', 'WIND', 'WINDPROB',
    'APCP', 'ASNOW', 'FICEAC', 'ICPRB',
    'TMAX', 'TMIN', 'TMP', 'APTMP',
    'VIS', 'CEIL',
    'TSTM',
    'WETGLBT', 'WBGT',
    'PROB', 'PTYPE', 'PWTHER',
    'SNOD', 'ASNOW', 'SNOWLR',
]

def get_recent_cycles():
    """Return last 6 cycle times to try, newest first."""
    now = datetime.now(timezone.utc)
    cycles = []
    for h in range(2, 8):
        c = now - timedelta(hours=h)
        cycles.append(c.replace(minute=0, second=0, microsecond=0))
    return cycles

def build_idx_url(base, date_str, cycle_str, product, fxx):
    """Build the .idx file URL for a given NBM file."""
    fname = f"blend.t{cycle_str}z.{product}.f{fxx:03d}.co.grib2.idx"
    return f"{base}/blend.{date_str}/{cycle_str}/{product}/{fname}"

def fetch_idx(url):
    """Fetch and parse a .idx file. Returns list of field dicts."""
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code}: {url}")
            return None
        lines = r.text.strip().split('\n')
        fields = []
        for line in lines:
            # .idx format: msgnum:byte_offset:date:var:level:forecast:...
            parts = line.split(':')
            if len(parts) < 6:
                continue
            fields.append({
                'msg':      parts[0].strip(),
                'offset':   parts[1].strip(),
                'date':     parts[2].strip(),
                'var':      parts[3].strip(),
                'level':    parts[4].strip(),
                'forecast': parts[5].strip(),
                'rest':     ':'.join(parts[6:]).strip() if len(parts) > 6 else '',
                # search_this is what Herbie uses — var:level pattern
                'search_this': f":{parts[3].strip()}:{parts[4].strip()}:",
            })
        return fields
    except Exception as e:
        print(f"  ✗ Error fetching {url}: {e}")
        return None

def main():
    os.makedirs('data', exist_ok=True)

    cycles = get_recent_cycles()
    results = {
        'krno_lat': KRNO_LAT,
        'krno_lon': KRNO_LON,
        'products': {'operational': {}, 'parallel': {}},
        'errors': []
    }

    # Try operational first, then parallel
    sources = [
        ('operational', AWS_BASE),
        ('parallel',    PARA_BASE),
    ]

    for source_name, base_url in sources:
        print(f"\n{'='*60}")
        print(f"Source: {source_name} ({base_url})")
        print(f"{'='*60}")

        found_cycle = None
        for cycle in cycles:
            date_str  = cycle.strftime('%Y%m%d')
            cycle_str = cycle.strftime('%H')
            # Quick check with f001 core
            test_url = build_idx_url(base_url, date_str, cycle_str, 'core', 1)
            r = requests.get(test_url, timeout=10)
            if r.status_code == 200:
                found_cycle = cycle
                print(f"  ✓ Found cycle: {cycle.strftime('%Y-%m-%d %H:00 UTC')}")
                break
            else:
                print(f"  ✗ {cycle.strftime('%Y-%m-%d %H:00 UTC')} not available")

        if found_cycle is None:
            print(f"  ✗ No available cycle found for {source_name}")
            results['errors'].append(f"No cycle found for {source_name}")
            continue

        results['cycle'] = found_cycle.strftime('%Y-%m-%dT%H:00:00Z')
        date_str  = found_cycle.strftime('%Y%m%d')
        cycle_str = found_cycle.strftime('%H')

        for product in PRODUCTS:
            results['products'][source_name][product] = {}
            for fxx in FORECAST_HOURS:
                url = build_idx_url(base_url, date_str, cycle_str, product, fxx)
                print(f"\n  → {product} f{fxx:03d}")
                print(f"    URL: {url}")

                fields = fetch_idx(url)
                if fields is None:
                    results['products'][source_name][product][f'f{fxx:03d}'] = {
                        'error': 'fetch failed',
                        'url': url
                    }
                    continue

                print(f"    ✓ {len(fields)} fields found")
                for f in fields[:5]:
                    print(f"      {f['var']:20s} {f['level']:35s} {f['forecast']}")
                if len(fields) > 5:
                    print(f"      ... and {len(fields)-5} more")

                results['products'][source_name][product][f'f{fxx:03d}'] = {
                    'field_count': len(fields),
                    'url': url,
                    'fields': fields,
                }

    # ── QMD files — only generated at 00/06/12/18Z cycles ────────────────────
    # QMD provides temperature percentiles (TMAX/TMIN) and QPF percentiles
    # Try the most recent 00/06/12/18Z cycle
    print(f"\n{'='*60}")
    print(f"Checking QMD files (operational, 00/06/12/18Z cycles only)")
    print(f"{'='*60}")

    qmd_cycles = []
    now_utc = datetime.now(timezone.utc)
    for h in range(2, 30):
        c = now_utc - timedelta(hours=h)
        c = c.replace(minute=0, second=0, microsecond=0)
        if c.hour in (0, 6, 12, 18):
            qmd_cycles.append(c)
        if len(qmd_cycles) >= 4:
            break

    # Find the most recent 00/06/12/18Z cycle that has QMD data
    qmd_base_cycle = None
    qmd_date_str = None
    qmd_cycle_str = None
    for qmd_cycle in qmd_cycles:
        date_str  = qmd_cycle.strftime('%Y%m%d')
        cycle_str = qmd_cycle.strftime('%H')
        test_url = f"{AWS_BASE}/blend.{date_str}/{cycle_str}/qmd/blend.t{cycle_str}z.qmd.f024.co.grib2.idx"
        r = requests.get(test_url, timeout=10)
        if r.status_code == 200:
            qmd_base_cycle = qmd_cycle
            qmd_date_str = date_str
            qmd_cycle_str = cycle_str
            print(f"  ✓ QMD cycle found: {qmd_cycle.strftime('%Y-%m-%d %H:00 UTC')}")
            break
        else:
            print(f"  ✗ {qmd_cycle.strftime('%Y-%m-%d %H:00 UTC')} no QMD")

    if qmd_base_cycle:
        # Probe every possible forecast hour to see what QMD publishes
        # Try f001-f048 hourly, then f049-f264 every 3 hours
        probe_hours = list(range(1, 49)) + list(range(51, 265, 3))
        print(f"\n  Probing all QMD forecast hours...")
        available_fxx = []
        for fxx in probe_hours:
            url = f"{AWS_BASE}/blend.{qmd_date_str}/{qmd_cycle_str}/qmd/blend.t{qmd_cycle_str}z.qmd.f{fxx:03d}.co.grib2.idx"
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                available_fxx.append(fxx)
                print(f"    ✓ f{fxx:03d}")
            else:
                print(f"    ✗ f{fxx:03d}")

        print(f"\n  QMD available at: {available_fxx}")
        results['products']['operational']['qmd_available_hours'] = available_fxx

        # Fetch full inventory for a sample of available hours
        results['products']['operational']['qmd'] = {}
        for fxx in available_fxx[:4]:  # first 4 to keep output manageable
            url = f"{AWS_BASE}/blend.{qmd_date_str}/{qmd_cycle_str}/qmd/blend.t{qmd_cycle_str}z.qmd.f{fxx:03d}.co.grib2.idx"
            fields = fetch_idx(url)
            if fields:
                results['products']['operational']['qmd'][f'f{fxx:03d}'] = {
                    'field_count': len(fields),
                    'url': url,
                    'cycle': qmd_base_cycle.strftime('%Y-%m-%dT%H:00:00Z'),
                    'fields': fields
                }
                print(f"\n  f{fxx:03d}: {len(fields)} fields")
                for f in fields[:5]:
                    print(f"    {f['var']:25s} {f['level']:35s} {f.get('rest','')[:40]}")

    # ── Check parallel bucket for WETGLBT specifically ────────────────────────
    print(f"\n{'='*60}")
    print(f"Checking parallel bucket for WETGLBT (wet bulb globe temp)")
    print(f"{'='*60}")
    results['products']['parallel'] = {}
    for h in range(2, 8):
        c = now_utc - timedelta(hours=h)
        c = c.replace(minute=0, second=0, microsecond=0)
        date_str  = c.strftime('%Y%m%d')
        cycle_str = c.strftime('%H')
        url = f"{PARA_BASE}/blend.{date_str}/{cycle_str}/core/blend.t{cycle_str}z.core.f001.co.grib2.idx"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            print(f"  ✓ Parallel cycle found: {c.strftime('%Y-%m-%d %H:00 UTC')}")
            fields = fetch_idx(url)
            if fields:
                wetglbt = [f for f in fields if 'WETGLBT' in f.get('var','').upper() or 'WBGT' in f.get('var','').upper()]
                aptmp   = [f for f in fields if 'APTMP'   in f.get('var','').upper()]
                print(f"    WETGLBT fields: {len(wetglbt)}")
                print(f"    APTMP fields:   {len(aptmp)}")
                results['products']['parallel']['f001_sample'] = {
                    'url': url,
                    'cycle': c.strftime('%Y-%m-%dT%H:00:00Z'),
                    'field_count': len(fields),
                    'wetglbt_fields': wetglbt,
                    'aptmp_fields': aptmp,
                }
                for f in wetglbt + aptmp:
                    print(f"      {f['var']:25s} {f['level']:35s} {f.get('rest','')}")
            break
        else:
            print(f"  ✗ {c.strftime('%Y-%m-%d %H:00 UTC')} not available in parallel")
    relevant = {}
    for source_name, products in results['products'].items():
        if not isinstance(products, dict):
            continue
        for product, fxx_data in products.items():
            if not isinstance(fxx_data, dict):
                continue
            for fxx_key, data in fxx_data.items():
                if not isinstance(data, dict) or 'fields' not in data:
                    continue
                for field in data['fields']:
                    var = field.get('var', '').upper()
                    if any(kw in var for kw in HAZARD_KEYWORDS):
                        key = f"{source_name}/{product}/{fxx_key}/{var}/{field.get('level','')}"
                        if key not in relevant:
                            relevant[key] = {
                                **field,
                                'source': source_name,
                                'product': product,
                                'fxx': fxx_key,
                            }

    results['hazard_relevant_fields'] = list(relevant.values())

    print(f"\n\n{'='*60}")
    print(f"HAZARD-RELEVANT FIELDS ({len(relevant)}):")
    print(f"{'='*60}")
    for key, field in relevant.items():
        print(f"  [{field['source']}/{field['product']}/{field['fxx']}] "
              f"{field['var']:20s} {field['level']:35s} {field['forecast']}")

    with open('data/nbm_inventory.json', 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n✓ Written to data/nbm_inventory.json")

if __name__ == '__main__':
    main()
