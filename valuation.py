import csv
from pathlib import Path

COMPS_PATH = Path(__file__).parent / "comps.csv"

def load_zip_ppsqft():
    data = {}
    if COMPS_PATH.exists():
        with open(COMPS_PATH, newline="") as f:
            for row in csv.DictReader(f):
                z = row.get("zip")
                try:
                    ppsf = float(row.get("price_per_sqft", "0") or 0)
                    if z:
                        data[z] = ppsf
                except:
                    continue
    return data

ZIP_PPSF = load_zip_ppsqft()
DEFAULT_PPSF = 220.0  # national-ish fallback for mock

def estimate_value(zip_code: str | None, beds: int | None, baths: float | None, sqft: int | None) -> dict:
    ppsf = ZIP_PPSF.get(zip_code or "", DEFAULT_PPSF)
    sqft = max(600, (sqft or 1800))
    adj = 1.0
    if beds:
        if beds >= 5: adj += 0.08
        elif beds == 4: adj += 0.05
        elif beds == 3: adj += 0.02
    if baths:
        if baths >= 3: adj += 0.05
        elif baths >= 2: adj += 0.03

    est = round(ppsf * sqft * adj)
    low, high = int(est * 0.93), int(est * 1.07)
    return {
        "estimate": est,
        "low": low,
        "high": high,
        "ppsf_used": ppsf,
        "sqft_used": sqft,
        "adjustment": adj
    }
