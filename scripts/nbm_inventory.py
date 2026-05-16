"""
nbm_inventory.py
----------------
Fetches NBM GRIB2 index files from AWS S3 for BOTH core and qmd directories.
Produces a complete inventory of every field available across all cycles 
and forecast hours.

Primary goal: Discover what fields are available in core vs qmd.

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
# Expecting to find in QMD
QMD_EXPECTED = ['GUST', 'WIND', 'TMP', 'APTMP', 'APCP', 'DPT', 'RH', 'JFWPRB', 'HTSGW']
# Winter weather (checking if in QMD)
WINTER = ['SnowAmt01', 'SnowAmt06', 'SnowAmt24', 'SnowAmt48', 'SnowAmt72', 
          'IceAccum01', 'IceAccum06', 'IceAccum24', 'IceAccum48', 'IceAccum72',
          'PctSnow01', 'PctSnow06', 'PctSnow24', 'PctSnow48', 'PctSnow72',
          'PctIce06', 'PctIce24', 'PctIce48', 'PctIce72',
          'ProbSnow01', 'ProbSnow06', 'ProbSnow24', 'ProbSnow48', 'ProbSnow72',
          'ProbIce06', 'ProbIce24', 'ProbIce48', 'ProbIce72']
# Expecting in CORE only
CORE_ONLY = ['VIS', 'CEIL', 'TSTM', 'HAILPROB', 'PTYPE', 'SNOWLR']
# Flat probabilities
FLAT_PROB = ['PoT01', 'PoT03', 'PoT06', 'PoT12']

ALL_TARGET_VARS = set(QMD_EXPECTED + WINTER + CORE_ONLY + FLAT_PROB)


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


def build_url(date_str, cycle_str, subdomain, fxx):
    """Build S3 URL for core or qmd file."""
    fname = f"blend.t{cycle_str}z.{subdomain}.f{fxx:03d}.co.grib2.idx"
    return f"{AWS_BASE}/blend.{date_str}/{cycle_str}/{subdomain}/{fname}"


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


def find_latest_cycle():
    """Find the most recent 00/06/12/18Z cycle that has both core and qmd f024."""
    for cycle in get_qmd_cycles_to_try():
        date_str  = cycle.strftime('%Y%m%d')
        cycle_str = cycle.strftime('%H')
        
        # Check both core and qmd
        qmd_url = build_url(date_str, cycle_str, 'qmd', 24)
        core_url = build_url(date_str, cycle_str, 'core', 24)
        
        qmd_status = requests.get(qmd_url, timeout=10).status_code
        core_status = requests.get(core_url, timeout=10).status_code
        
        if qmd_status == 200 and core_status == 200:
            print(f"  + Latest cycle (both core & qmd): {cycle.strftime('%Y-%m-%d %H:00 UTC')}")
            return cycle, date_str, cycle_str
        else:
            status_str = f"QMD:{qmd_status} CORE:{core_status}"
            print(f"  x {cycle.strftime('%Y-%m-%d %H:00 UTC')} -- {status_str}")
    
    print("  No cycle found with both core and qmd")
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

    # Step 1: find latest available cycle (both core & qmd)
    print(f"\n{'='*60}")
    print("Finding latest cycle with both core & qmd...")
    print(f"{'='*60}")

    latest_cycle, latest_date, latest_cycle_str = find_latest_cycle()
    if latest_cycle is None:
        print("No suitable cycle found. Exiting.")
        return

    # Step 2: probe ALL forecast hours for that cycle in BOTH directories
    probe_hours = list(range(1, 49)) + list(range(51, 277, 3))

    for subdomain in ['core', 'qmd']:
        print(f"\n{'='*60}")
        print(f"Probing {subdomain.upper()} forecast hour availability ({latest_cycle_str}Z cycle)")
        print(f"{'='*60}")

        available_fxx = []
        for fxx in probe_hours:
            url = build_url(latest_date, latest_cycle_str, subdomain, fxx)
            r = requests.get(url, timeout=8)
            status = r.status_code
            marker = '+' if status == 200 else 'x'
            if status == 200:
                available_fxx.append(fxx)
        
        # Only print summary for available hours
        if available_fxx:
            print(f"  Available fhrs ({len(available_fxx)} total): {available_fxx[:20]}{'...' if len(available_fxx) > 20 else ''}")
        else:
            print(f"  No files found")
        
        if subdomain not in results['cycles']:
            results['cycles'][subdomain] = {}
        
        results['cycles'][subdomain]['cycle'] = latest_cycle.isoformat()
        results['cycles'][subdomain]['date'] = latest_date
        results['cycles'][subdomain]['available_fhrs'] = available_fxx
        results['cycles'][subdomain]['field_inventory'] = {}

        # Step 3: fetch FULL field inventory for key hours
        key_fhrs = [fxx for fxx in [1, 3, 6, 12, 24, 48] if fxx in available_fxx]

        if not key_fhrs:
            print(f"  No key hours available")
            continue

        print(f"\n  Fetching full field inventory for key hours: {key_fhrs}")

        for fxx in key_fhrs:
            url = build_url(latest_date, latest_cycle_str, subdomain, fxx)
            fields, status = fetch_idx(url)
            if fields is None:
                results['cycles'][subdomain]['field_inventory'][f'f{fxx:03d}'] = {
                    'error': f'HTTP {status}', 'url': url
                }
                continue

            # Print unique var+level+forecast combinations
            seen = set()
            unique_combos = []
            for f in fields:
                key = f"{f['var']}|{f['level']}|{f['forecast']}"
                if key not in seen:
                    seen.add(key)
                    unique_combos.append(key)
                    flag = ' <-- TARGET' if f['var'] in ALL_TARGET_VARS else ''
                    if flag:
                        print(f"    f{fxx:03d}: {f['var']:15s} | {f['level']:40s} | {f['forecast']}{flag}")

            results['cycles'][subdomain]['field_inventory'][f'f{fxx:03d}'] = {
                'field_count': len(fields),
                'url': url,
                'fields': fields,
            }

    # Step 4: build target field summary (core vs qmd)
    print(f"\n{'='*60}")
    print("CORE vs QMD FIELD COMPARISON")
    print(f"{'='*60}")

    for subdomain in ['core', 'qmd']:
        if subdomain not in results['cycles']:
            print(f"\n{subdomain.upper()}: Not available")
            continue
        
        print(f"\n{subdomain.upper()}:")
        
        # Collect all unique (var, level, forecast) seen in this subdomain
        all_fields_seen = {}
        inv = results['cycles'][subdomain].get('field_inventory', {})
        for fhr_key, data in inv.items():
            if 'fields' not in data:
                continue
            for f in data['fields']:
                key = f"{f['var']}:{f['level']}:{f['forecast']}"
                if key not in all_fields_seen:
                    all_fields_seen[key] = {**f, 'seen_at_fhrs': []}
                if fhr_key not in all_fields_seen[key]['seen_at_fhrs']:
                    all_fields_seen[key]['seen_at_fhrs'].append(fhr_key)

        present_vars = set()
        for var in sorted(set(f['var'] for k, f in all_fields_seen.items())):
            matching = {k: v for k, v in all_fields_seen.items() if k.startswith(f"{var}:")}
            if matching:
                present_vars.add(var)
                for key, info in sorted(matching.items()):
                    fhrs_str = ', '.join(info['seen_at_fhrs'][:3]) + ('...' if len(info['seen_at_fhrs']) > 3 else '')
                    print(f"  ✓ {var:15s} | {info['level']:40s} | {info['forecast']:25s} | {fhrs_str}")
        
        results['cycles'][subdomain]['target_summary'] = {
            'present_vars': sorted(present_vars),
            'field_details': all_fields_seen,
        }

    # Write output
    out_path = 'data/nbm_inventory.json'
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n\n{'='*60}")
    print(f"Written to {out_path}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
