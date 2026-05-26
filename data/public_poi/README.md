# Public POI Candidate Data

This folder contains a public POI candidate table for the address-normalizer demo.
It is not an authoritative government standard-address table.

## Current Sample

- `urumqi_overture_poi_sample.csv`
- Source: Overture Maps Places and Divisions
- Release: `2026-05-20.0`
- Area: Urumqi bbox `87.35,43.65,88.05,44.05`
- Rows: 173

The script joins Overture Places with Overture Divisions, so each row has a POI
plus inferred province, city, district, and town/street where available.

Important fields:

- `standard_id`: local demo candidate id
- `source_id`: Overture feature id
- `name`: POI name
- `province`, `city`, `district`, `town`: administrative components inferred from public division polygons
- `freeform_address`: raw Overture address text
- `clean_address`: lightly cleaned address text
- `full_address`: concatenated address candidate for matching
- `lon`, `lat`: point coordinate
- `confidence`: Overture confidence score

## Rebuild

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Fetch the default Urumqi sample:

```powershell
python scripts\fetch_public_poi.py --limit 500
```

Fetch another bbox:

```powershell
python scripts\fetch_public_poi.py --bbox 121.10 30.90 121.90 31.45 --limit 500 --output data\public_poi\shanghai_overture_poi_sample.csv --id-prefix OVT_SHA
```

## Notes

The Peking University Open Research Data Platform has a Gaode/AMap POI dataset
with stronger address fields (`name`, `type`, `address`, `location`, `pname`,
`cityname`, `adname`, `adcode`), but the data files are restricted and require
login/access approval. Overture is used here because it can be queried
anonymously and reproducibly.
