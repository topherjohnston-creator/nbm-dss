"""
nbm_qmd_inventory.py
--------------------
Fetches NBM QMD GRIB2 index files from AWS S3 and produces a complete
inventory of every field available across all QMD cycles (00/06/12/18Z)
and forecast hours (f001-f048 hourly, then f049-f276 every 3h).

Primary goal: confirm exact VAR:level:ftime strings for the DSS pipeline,
especially for TMP (max/min windows), GUST, WIND, APCP, DPT, RH, APTMP.

Output: data/nbm_inventory.json
"""

import json
import os
import requests
from datetime import datetime, timezone, timedelta

# -- KRNO grid point ----------------------------------------------------------
KRNO_LAT = 39.4986
KRNO_LON = -119.7681

# -- NBM AWS S3 base ----------------------------------------------------------
AWS_BASE = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"

# -- QMD runs at 00/06/12/18Z only -------------------------------------------
QMD_CYCLE_HOURS = (0, 6, 12, 18)

# -- Fields we care about for the DSS pipeline --------------------------------
# Present in QMD (expected)
QMD_EXPECTED = ['GUST', 'WIND', 'TMP', 'APTMP', 'APCP', 'DPT', 'RH', 'JFWPRB', 'HTSGW']
# Absent from QMD (core-only -- confirm they are missing)
CORE_ONLY = ['ASNOW', 'FICEAC', 'TSTM', 'HAILPROB', 'VIS', 'CEIL', 'PTYPE', 'SNOWLR']

ALL_TARGET_VARS = set(QMD_EXPECTED + CORE_ONLY)


# -- Helpers ------------------------------------------------------------------

def get_qmd_cycles_to_try():
    """Return recent 00/06/12/18Z cycle datetimes to try, newest first."""
    now = datetime.now(timezone.utc)
    cycles = []
    for h in range(1, 36):
        c = (now - timedelta(hours=h)).replace(minute=0, second=0, microsecond=0)
        if c.hour in QMD_CYCLE_HOURS and c not in cycles:
            cycles.append(c)
        if len(cycles) >= 6:
            break
    return cycles


def build_qmd_url(date_str, cycle_str, fxx):
    fname = f"blend.t{cycle_str}z.qmd.f{fxx:03d}.co.grib2.idx"
    return f"{AWS_BASE}/blend.{date_str}/{cycle_str}/qmd/{fname}"


def fetch_idx(url):
    """Fetch and parse a .idx file. Returns (fields list, http_status)."""
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None, r.status_code
        lines = r.text.strip().split('\n')
        fields = []
        for line in lines:
            parts = line.split(':')
            if len(parts) < 6:
                continue
            fields.append({
                'msg':        parts[0].strip(),
                'offset':     parts[1].strip(),
                'date':       parts[2].strip(),
                'var':        parts[3].strip(),
                'level':      parts[4].strip(),
                'forecast':   parts[5].strip(),
                'rest':       ':'.join(parts[6:]).strip() if len(parts) > 6 else '',
                # Herbie-compatible search string
                'search_this': f":{parts[3].strip()}:{parts[4].strip()}:",
            })
        return fields, 200
    except Exception as e:
        print(f"  Error: {e}")
        return None, -1


def find_latest_qmd_cycle():
    """Find the most recent 00/06/12/18Z cycle that has QMD f024."""
    for cycle in get_qmd_cycles_to_try():
        date_str  = cycle.strftime('%Y%m%d')
        cycle_str = cycle.strftime('%H')
        url = build_qmd_url(date_str, cycle_str, 24)
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            print(f"  + Latest QMD cycle: {cycle.strftime('%Y-%m-%d %H:00 UTC')}")
            return cycle, date_str, cycle_str
        else:
            print(f"  x {cycle.strftime('%Y-%m-%d %H:00 UTC')} -- HTTP {r.status_code}")
    return None, None, None


# -- Main ---------------------------------------------------------------------

def main():
    os.makedirs('data', exist_ok=True)

    results = {
        'generated': datetime.now(timezone.utc).isoformat(),
        'krno_lat':  KRNO_LAT,
        'krno_lon':  KRNO_LON,
        'cycles':    {},
    }

    # Step 1: find latest available QMD cycle
    print(f"\n{'='*60}")
    print("Finding latest QMD cycle...")
    print(f"{'='*60}")

    latest_cycle, latest_date, latest_cycle_str = find_latest_qmd_cycle()
    if latest_cycle is None:
        print("No QMD cycle found. Exiting.")
        return

    # Step 2: probe ALL forecast hours for that cycle
    # f001-f048 hourly, then every 3h out to f276
    probe_hours = list(range(1, 49)) + list(range(51, 277, 3))

    print(f"\n{'='*60}")
    print(f"Probing QMD forecast hour availability ({latest_cycle_str}Z cycle)")
    print(f"{'='*60}")

    available_fxx = []
    for fxx in probe_hours:
        url = build_qmd_url(latest_date, latest_cycle_str, fxx)
        r = requests.get(url, timeout=8)
        status = r.status_code
        marker = '+' if status == 200 else 'x'
        print(f"  {marker} f{fxx:03d}  HTTP {status}")
        if status == 200:
            available_fxx.append(fxx)

    print(f"\n  Available fhrs: {available_fxx}")
    results['cycles'][latest_cycle_str] = {
        'cycle':           latest_cycle.isoformat(),
        'date':            latest_date,
        'available_fhrs':  available_fxx,
        'field_inventory': {},
    }

    # Step 3: fetch FULL field inventory for key hours
    # f001 -- shortest range; what arrives immediately
    # f006 -- pick up any 6hr window fields not in f001
    # f024 -- 24hr accum fields (QPF P0-P100, MaxT/MinT windows)
    # f048 -- confirm fields still present at 48h
    key_fhrs = [fxx for fxx in [1, 6, 24, 48] if fxx in available_fxx]

    print(f"\n{'='*60}")
    print(f"Fetching full field inventory for key hours: {key_fhrs}")
    print(f"{'='*60}")

    for fxx in key_fhrs:
        url = build_qmd_url(latest_date, latest_cycle_str, fxx)
        print(f"\n-- f{fxx:03d} --")
        print(f"   {url}")

        fields, status = fetch_idx(url)
        if fields is None:
            print(f"   Fetch failed (HTTP {status})")
            results['cycles'][latest_cycle_str]['field_inventory'][f'f{fxx:03d}'] = {
                'error': f'HTTP {status}', 'url': url
            }
            continue

        print(f"   {len(fields)} fields found")

        # Print all unique var+level+forecast combinations
        seen = set()
        for f in fields:
            key = f"{f['var']}|{f['level']}|{f['forecast']}"
            if key not in seen:
                seen.add(key)
                flag = ' <-- TARGET' if f['var'] in ALL_TARGET_VARS else ''
                print(f"   {f['var']:30s} | {f['level']:40s} | {f['forecast']}{flag}")

        results['cycles'][latest_cycle_str]['field_inventory'][f'f{fxx:03d}'] = {
            'field_count': len(fields),
            'url':         url,
            'fields':      fields,
        }

    # Step 4: sample the OTHER three QMD cycles at f024
    # Confirms all four of 00/06/12/18Z are actually publishing
    print(f"\n{'='*60}")
    print("Sampling other QMD cycles at f024 (confirm 00/06/12/18Z availability)")
    print(f"{'='*60}")

    all_qmd_cycles = get_qmd_cycles_to_try()
    sampled = 0
    for cycle in all_qmd_cycles:
        cycle_str = cycle.strftime('%H')
        if cycle_str == latest_cycle_str:
            continue
        if sampled >= 3:
            break

        date_str = cycle.strftime('%Y%m%d')
        url = build_qmd_url(date_str, cycle_str, 24)
        print(f"\n  {cycle.strftime('%Y-%m-%d %H:00 UTC')} -- f024")
        r = requests.get(url, timeout=10)
        status = r.status_code
        print(f"  HTTP {status}: {url}")

        if status == 200:
            fields, _ = fetch_idx(url)
            if fields:
                results['cycles'][cycle_str] = {
                    'cycle': cycle.isoformat(),
                    'date':  date_str,
                    'note':  'f024 sample only',
                    'field_inventory': {
                        'f024': {
                            'field_count': len(fields),
                            'url':         url,
                            'fields':      fields,
                        }
                    }
                }
                seen = set()
                for f in fields:
                    key = f"{f['var']}|{f['level']}|{f['forecast']}"
                    if key not in seen:
                        seen.add(key)
                        flag = ' <-- TARGET' if f['var'] in ALL_TARGET_VARS else ''
                        print(f"    {f['var']:30s} | {f['level']:40s} | {f['forecast']}{flag}")
            sampled += 1
        else:
            print(f"  Not available")

    # Step 5: build target field summary
    print(f"\n{'='*60}")
    print("TARGET FIELD SUMMARY")
    print(f"{'='*60}")

    # Collect all unique (var, level, forecast) seen across key hours
    all_fields_seen = {}
    inv = results['cycles'][latest_cycle_str]['field_inventory']
    for fhr_key, data in inv.items():
        if 'fields' not in data:
            continue
        for f in data['fields']:
            if f['var'] in ALL_TARGET_VARS:
                key = f"{f['var']}:{f['level']}:{f['forecast']}"
                if key not in all_fields_seen:
                    all_fields_seen[key] = {**f, 'seen_at_fhrs': []}
                if fhr_key not in all_fields_seen[key]['seen_at_fhrs']:
                    all_fields_seen[key]['seen_at_fhrs'].append(fhr_key)

    present_vars = set()
    absent_vars  = set()

    for var in sorted(ALL_TARGET_VARS):
        matching = {k: v for k, v in all_fields_seen.items() if k.startswith(f"{var}:")}
        if matching:
            present_vars.add(var)
            label = '(expected in QMD)' if var in QMD_EXPECTED else ''
            print(f"\n  + {var} {label}")
            for key, info in sorted(matching.items()):
                print(f"      level    = '{info['level']}'")
                print(f"      forecast = '{info['forecast']}'")
                print(f"      seen at  = {info['seen_at_fhrs']}")
                print(f"      search   = '{info['search_this']}'")
        else:
            absent_vars.add(var)
            label = '(core-only -- expected absent)' if var in CORE_ONLY else '(UNEXPECTED -- check pipeline)'
            print(f"\n  x {var} -- NOT FOUND {label}")

    results['target_summary'] = {
        'present':       sorted(present_vars),
        'absent':        sorted(absent_vars),
        'field_details': all_fields_seen,
    }

    # Write output
    out_path = 'data/nbm_inventory.json'
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n\n{'='*60}")
    print(f"Written to {out_path}")
    print(f"  Present target vars: {sorted(present_vars)}")
    print(f"  Absent target vars:  {sorted(absent_vars)}")


if __name__ == '__main__':
    main()
