# nbm-dss

Cloud-native NBM 5.0 probability extraction and decision support dashboard for KRNO ground operations weather hazard assessment.

## Overview

This repository automatically extracts probabilistic weather forecast data from the [National Blend of Models (NBM) v5.0](https://vlab.noaa.gov/web/mdl/nbm) GRIB2 files and serves a real-time decision support dashboard for Reno-Tahoe International Airport (KRNO) ground operations.

Data is fetched from AWS S3 (`noaa-nbm-grib2-pds`) using [Herbie](https://herbie.readthedocs.io/) — only the specific byte ranges needed are downloaded, not full GRIB2 files.

## Hazard Table

| Hazard | Impact Levels | NBM Source |
|--------|--------------|------------|
| Wind Gust | <30 / 30-45 / 45-58 / 58-65 / >65 mph | GRIB2 GUST percentiles |
| Snow | None / Trace / 2-4" / 4"+ / >4"/12hr | GRIB2 ASNOW |
| Flash Freeze | Wet bulb thresholds | GRIB2 WETGLBT |
| Lightning | <5% / 5-25% / 25-50% / 50-75% / >75% | GRIB2 TSTM |
| Visibility | >5 / 3-5 / 1-3 / 0.5-1 / <0.5 SM | GRIB2 VIS |
| Cold | ≥40 / 32-40 / 20-32 / 10-20 / <10°F | GRIB2 TMIN |
| Heat | <90 / 90-95 / 95-100 / 100-105 / >105°F | GRIB2 TMAX |
| Rain/Flooding | <0.10 / 0.10-0.25 / 0.25-0.50 / 0.50-1.00 / >1.00"/hr | GRIB2 APCP |
| Freezing Rain | None / Trace / Trace-0.01" / 0.01-0.10" / >0.10" | GRIB2 FICEAC |

## Architecture

```
GitHub Actions (hourly)
  → Herbie fetches byte ranges from noaa-nbm-grib2-pds (AWS S3)
  → Extracts KRNO grid point (39.4986°N, 119.7681°W)
  → Computes probabilities via 5-point percentile interpolation
  → Feeds NOAA 5×5 risk matrix for each hazard
  → Writes docs/threats.json + docs/timeline.json
  → GitHub Pages serves dashboard at docs/index.html
```

## Risk Matrix

Uses the NOAA 5×5 impact-based risk matrix:

| Probability | L1 | L2 | L3 | L4 | L5 |
|-------------|----|----|----|----|-----|
| >90% (Very Likely) | 1 | 2 | 3 | 4 | 5 |
| >66% (Likely) | 1 | 2 | 2 | 3 | 4 |
| 33-66% (As Likely) | 1 | 1 | 2 | 3 | 4 |
| 10-33% (Unlikely) | 1 | 1 | 2 | 2 | 3 |
| <10% (Very Unlikely) | 1 | 1 | 1 | 2 | 2 |

## Development Status

- [x] Repo structure
- [x] NBM field inventory script
- [ ] GRIB2 probability extraction for all hazards
- [ ] Risk matrix computation
- [ ] threats.json / timeline.json output
- [ ] Dashboard (index.html)
- [ ] GitHub Actions hourly automation

## Setup

```bash
pip install -r requirements.txt
python scripts/nbm_inventory.py   # dumps available fields to data/nbm_inventory.json
```

## Data Source

NBM GRIB2 data accessed from [NOAA Open Data Dissemination](https://registry.opendata.aws/noaa-nbm/) via AWS S3.  
No AWS account required.
