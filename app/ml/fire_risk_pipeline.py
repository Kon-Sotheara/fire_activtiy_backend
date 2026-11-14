"""
Fire risk inference pipeline.

Takes raw MODIS/VIIRS detection records (same shape as a database export / the
cleaned CSVs from the notebook) and produces a fire-risk prediction per 0.1-degree
grid cell for the next HORIZON_DAYS days, using the model trained in
modis_viirs_harmonization_with_outputs (1).ipynb.

Workflow (matches the notebook's training pipeline exactly, so predictions are
consistent with what the model was trained on):

    raw MODIS/VIIRS rows
        -> standardize (common columns, confidence classes)
        -> clean (drop invalid / low-confidence / non-vegetation-fire rows)
        -> harmonize (match same fire seen by both sensors, build one inventory)
        -> build a daily (day, row, col) grid of counts/FRP
        -> compute rolling/neighbor/recency features, "as of" a chosen date
        -> model.predict_proba() -> risk_level (Low/Medium/High)
        -> GeoJSON for the web map

Usage:
    from fire_risk_pipeline import predict_risk, export_geojson, load_model_bundle

    bundle = load_model_bundle("project_output/06_machine_learning/08_best_model.pkl")
    result = predict_risk(modis_df, viirs_df, bundle, as_of_date="2025-12-28")
    export_geojson(result, bundle, "risk_prediction.geojson")

Run directly (`python fire_risk_pipeline.py`) to test against the training CSVs
and confirm predictions match the notebook's own output.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.ndimage import convolve

# ---------------------------------------------------------------- config (must match training)
LAT_MIN, LAT_MAX = 10.3, 14.8
LON_MIN, LON_MAX = 102.2, 107.8
YEAR_MIN, YEAR_MAX = 2020, 2025

MATCH_DIST_KM = 0.75
MATCH_TIME_MIN = 60

HORIZON_DAYS = 3

MODEL_PATH_DEFAULT = str(Path(__file__).resolve().parents[2] / "models" / "08_best_model.pkl")

NEIGHBOR_KERNEL = np.ones((1, 3, 3), dtype=np.float32)
NEIGHBOR_KERNEL[0, 1, 1] = 0  # 8 neighbours, not the cell itself


# ---------------------------------------------------------------- 1. standardize
def standardize(df: pd.DataFrame, product: str) -> pd.DataFrame:
    """Raw FIRMS row -> common schema. `product` is "MODIS" or "VIIRS"."""
    d = df.copy()
    for c in ["latitude", "longitude", "brightness", "frp"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")

    d["acq_time"] = d["acq_time"].astype(int).astype(str).str.zfill(4)
    d["acq_date"] = pd.to_datetime(d["acq_date"]).dt.strftime("%Y-%m-%d")
    d["datetime_utc"] = pd.to_datetime(d["acq_date"] + " " + d["acq_time"].str[:2] + ":" + d["acq_time"].str[2:])
    d["date"] = d["datetime_utc"].dt.normalize()

    if product == "MODIS":
        d["sensor"] = "MODIS_" + d["satellite"].astype(str)
        conf = pd.to_numeric(d["confidence"], errors="coerce")
        d["confidence_class"] = pd.cut(conf, bins=[-1, 29, 79, 100], labels=["Low", "Nominal", "High"]).astype(str)
    else:
        d["sensor"] = "VIIRS_SNPP"
        d["confidence_class"] = d["confidence"].astype(str).str.lower().map({"l": "Low", "n": "Nominal", "h": "High"})

    d["product"] = product
    d["daynight"] = d["daynight"].astype(str)
    d = d.drop(columns=["confidence"])
    d.insert(0, "record_id", [("MOD_" if product == "MODIS" else "VIR_") + str(i).zfill(7) for i in range(len(d))])
    return d


# ---------------------------------------------------------------- 2. clean
def clean(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d = d.dropna(subset=["latitude", "longitude", "datetime_utc", "frp", "confidence_class"])
    d = d[d["frp"] >= 0]
    d = d[d["latitude"].between(LAT_MIN, LAT_MAX) & d["longitude"].between(LON_MIN, LON_MAX)]
    d = d[d["datetime_utc"].dt.year.between(YEAR_MIN, YEAR_MAX)]
    d = d[d["type"] == 0]
    d = d[d["confidence_class"] != "Low"]
    cols = [c for c in d.columns if c != "record_id"]
    d = d.drop_duplicates(subset=cols)
    return d.reset_index(drop=True)


# ---------------------------------------------------------------- 3. harmonize (point matching)
def harmonize(modis_clean: pd.DataFrame, viirs_clean: pd.DataFrame) -> pd.DataFrame:
    """Match same-day, nearby MODIS Aqua <-> VIIRS detections (same fire seen by both
    sensors), keep VIIRS as the representative record for a match, keep unmatched
    MODIS Aqua, keep all MODIS Terra. Mirrors the notebook's Phase 5 exactly."""
    from scipy.spatial import cKDTree

    lat0 = 12.5
    km_per_deg_lat = 110.57
    km_per_deg_lon = 111.32 * np.cos(np.radians(lat0))

    def to_km(df):
        return np.column_stack([df["longitude"].values * km_per_deg_lon, df["latitude"].values * km_per_deg_lat])

    aqua = modis_clean[modis_clean["sensor"] == "MODIS_Aqua"].reset_index(drop=True)
    terra = modis_clean[modis_clean["sensor"] == "MODIS_Terra"].reset_index(drop=True)
    vir = viirs_clean.reset_index(drop=True)

    if len(aqua) == 0 or len(vir) == 0:
        matched = pd.DataFrame(columns=["modis_id", "viirs_id"])
    else:
        day0 = min(aqua["date"].min(), vir["date"].min())

        def day_axis(df):
            return ((df["date"] - day0).dt.days.values * 1000.0)

        xyz_vir = np.column_stack([to_km(vir), day_axis(vir)])
        xyz_aqua = np.column_stack([to_km(aqua), day_axis(aqua)])

        tree = cKDTree(xyz_vir)
        dist, idx = tree.query(xyz_aqua, k=8, distance_upper_bound=MATCH_DIST_KM)

        pairs = []
        for k in range(idx.shape[1]):
            ok = np.isfinite(dist[:, k])
            pairs.append(pd.DataFrame({"aqua_i": np.where(ok)[0], "vir_i": idx[ok, k], "distance_km": dist[ok, k]}))
        pairs = pd.concat(pairs, ignore_index=True) if pairs else pd.DataFrame(columns=["aqua_i", "vir_i", "distance_km"])

        if len(pairs):
            pairs["time_diff_min"] = (
                aqua["datetime_utc"].values[pairs["aqua_i"]] - vir["datetime_utc"].values[pairs["vir_i"]]
            ) / np.timedelta64(1, "m")
            pairs = pairs[pairs["time_diff_min"].abs() <= MATCH_TIME_MIN].copy()
            pairs = pairs.sort_values("distance_km")

        used_aqua, used_vir, keep = set(), set(), []
        for a, v in zip(pairs.get("aqua_i", []), pairs.get("vir_i", [])):
            if a not in used_aqua and v not in used_vir:
                used_aqua.add(a)
                used_vir.add(v)
                keep.append((a, v))
        matches = pd.DataFrame(keep, columns=["aqua_i", "vir_i"]) if keep else pd.DataFrame(columns=["aqua_i", "vir_i"])
        matched = pd.DataFrame({
            "modis_id": aqua["record_id"].values[matches["aqua_i"]] if len(matches) else [],
            "viirs_id": vir["record_id"].values[matches["vir_i"]] if len(matches) else [],
        })

    h_vir = vir.copy()
    h_vir["source_sensor"] = "VIIRS_SNPP"
    h_aq = aqua[~aqua["record_id"].isin(matched["modis_id"])].copy()
    h_aq["source_sensor"] = "MODIS_Aqua"
    h_te = terra.copy()
    h_te["source_sensor"] = "MODIS_Terra"

    keep_cols = ["record_id", "latitude", "longitude", "datetime_utc", "date", "frp", "source_sensor"]
    harmonized = pd.concat([h_vir[keep_cols], h_aq[keep_cols], h_te[keep_cols]], ignore_index=True)
    return harmonized.sort_values("datetime_utc").reset_index(drop=True)


# ---------------------------------------------------------------- 4. feature engineering
def rolling_sum(a: np.ndarray, n: int) -> np.ndarray:
    cs = np.cumsum(a, axis=0)
    out = cs.copy()
    out[n:] = cs[n:] - cs[:-n]
    return out


def days_since_last(count: np.ndarray, cap: int = 60) -> np.ndarray:
    res = np.zeros_like(count)
    prev = np.full(count.shape[1:], cap, dtype=np.float32)
    for t in range(count.shape[0]):
        prev = np.where(count[t] > 0, 0, np.minimum(prev + 1, cap))
        res[t] = prev
    return res


def make_cubes(df: pd.DataFrame, all_days: pd.DatetimeIndex, lat0: float, lon0: float,
                n_rows: int, n_cols: int, cell_size: float):
    """Daily (day, row, col) count/FRP arrays, using a FIXED grid origin (from the
    saved model bundle) - not recomputed from this batch's own min/max, so a new
    batch of detections always lands on the exact same grid the model was trained on."""
    d = ((df["date"] - all_days[0]).dt.days).values
    r = np.floor((df["latitude"].values - lat0) / cell_size).astype(int)
    c = np.floor((df["longitude"].values - lon0) / cell_size).astype(int)

    in_bounds = (d >= 0) & (d < len(all_days)) & (r >= 0) & (r < n_rows) & (c >= 0) & (c < n_cols)
    d, r, c, frp = d[in_bounds], r[in_bounds], c[in_bounds], df["frp"].values[in_bounds]

    count = np.zeros((len(all_days), n_rows, n_cols), dtype=np.float32)
    frp_sum = np.zeros((len(all_days), n_rows, n_cols), dtype=np.float32)
    np.add.at(count, (d, r, c), 1)
    np.add.at(frp_sum, (d, r, c), frp)
    return count, frp_sum


def compute_features(count: np.ndarray, frp: np.ndarray) -> dict:
    c3, c7, c30 = rolling_sum(count, 3), rolling_sum(count, 7), rolling_sum(count, 30)
    f7 = rolling_sum(frp, 7)
    return {
        "fire_count_3d": c3,
        "fire_count_7d": c7,
        "fire_count_30d": c30,
        "frp_sum_7d": f7,
        "frp_mean_7d": f7 / np.maximum(c7, 1),
        "nearby_fire_count_7d": convolve(c7, NEIGHBOR_KERNEL, mode="constant"),
        "days_since_last_fire": days_since_last(count),
    }


# ---------------------------------------------------------------- 5. model bundle + prediction
def load_model_bundle(path: str = MODEL_PATH_DEFAULT) -> dict:
    """Bundle saved by the notebook: {"model", "features", "threshold", "model_name", "grid"}."""
    return joblib.load(path)


def predict_risk(modis_raw: pd.DataFrame, viirs_raw: pd.DataFrame, bundle: dict,
                  as_of_date: str | pd.Timestamp, lookback_days: int = 2200) -> pd.DataFrame:
    """
    modis_raw / viirs_raw: raw FIRMS-format rows (same columns as the training CSVs /
        a database export - latitude, longitude, acq_date, acq_time, satellite,
        instrument, confidence, frp, daynight, type, ...).
    as_of_date: predict risk for the HORIZON_DAYS after this date, using detections
        up to and including this date.
    lookback_days: how much history to pull before as_of_date. This controls TWO
        things: (1) the rolling features (30-day counts etc. need >=30 days of
        history to be accurate), and (2) WHICH grid cells get a prediction at all -
        a cell only appears in the output if it had >=1 fire sometime in this
        window. The default (2200 days, ~6 years) matches the notebook's own
        definition ("any cell that ever had a fire in the full training period")
        and was verified to reproduce the notebook's exact output (1330/147/129
        Low/Medium/High on the same test date). A shorter window (e.g. 35-60 days)
        is cheaper to query from a database and still predicts for all currently-
        relevant cells, but will OMIT long-dormant cells that were active further
        back - decide based on whether your database can cheaply return full
        history, or only a recent slice.

    Returns one row per active grid cell: grid_id, cell_lat, cell_lon, probability,
    risk_level, plus the raw feature values used.
    """
    as_of_date = pd.Timestamp(as_of_date).normalize()
    window_start = as_of_date - pd.Timedelta(days=lookback_days)

    modis_std = standardize(modis_raw, "MODIS")
    viirs_std = standardize(viirs_raw, "VIIRS")
    modis_clean = clean(modis_std)
    viirs_clean = clean(viirs_std)

    modis_clean = modis_clean[modis_clean["date"].between(window_start, as_of_date)]
    viirs_clean = viirs_clean[viirs_clean["date"].between(window_start, as_of_date)]

    harmonized = harmonize(modis_clean, viirs_clean)

    lat0, lon0, cell_size = bundle["grid"]["lat0"], bundle["grid"]["lon0"], bundle["grid"]["cell_size"]
    n_rows = int(np.ceil((LAT_MAX - lat0) / cell_size)) + 1
    n_cols = int(np.ceil((LON_MAX - lon0) / cell_size)) + 1
    all_days = pd.date_range(window_start, as_of_date, freq="D")

    count, frp = make_cubes(harmonized, all_days, lat0, lon0, n_rows, n_cols, cell_size)
    feats = compute_features(count, frp)

    t = len(all_days) - 1  # the as_of_date row
    active = count[: t + 1].sum(axis=0) > 0  # grid cells with any history at all
    cell_r, cell_c = np.where(active)

    table = pd.DataFrame({
        "grid_row": cell_r.astype(np.int16),
        "grid_col": cell_c.astype(np.int16),
    })
    for k, v in feats.items():
        table[k] = v[t, cell_r, cell_c].astype(np.float32)
    table["month"] = as_of_date.month
    table["day_of_year"] = as_of_date.day_of_year
    table["grid_id"] = "G" + table["grid_row"].astype(str).str.zfill(3) + "_" + table["grid_col"].astype(str).str.zfill(3)
    table["cell_lat"] = (lat0 + (table["grid_row"] + 0.5) * cell_size).astype(np.float32)
    table["cell_lon"] = (lon0 + (table["grid_col"] + 0.5) * cell_size).astype(np.float32)

    model = bundle["model"]
    features = bundle["features"]
    threshold = bundle["threshold"]
    table["probability"] = model.predict_proba(table[features])[:, 1]
    medium_thr = min(threshold, 0.5)
    table["risk_level"] = np.where(
        table["probability"] >= 0.5, "High",
        np.where(table["probability"] >= medium_thr, "Medium", "Low"),
    )
    table["prediction_date"] = as_of_date.date().isoformat()
    table["horizon_days"] = HORIZON_DAYS
    return table.reset_index(drop=True)


# ---------------------------------------------------------------- 6. web export
def export_geojson(result: pd.DataFrame, bundle: dict, out_path: str) -> None:
    cell_size = bundle["grid"]["cell_size"]
    features = []
    for _, row in result.iterrows():
        la, lo = row["cell_lat"] - cell_size / 2, row["cell_lon"] - cell_size / 2
        poly = [[lo, la], [lo + cell_size, la], [lo + cell_size, la + cell_size], [lo, la + cell_size], [lo, la]]
        features.append({
            "type": "Feature",
            "properties": {
                "grid_id": row["grid_id"],
                "prediction_date": row["prediction_date"],
                "horizon_days": int(row["horizon_days"]),
                "probability": round(float(row["probability"]), 4),
                "risk_level": row["risk_level"],
                "fire_count_7d": float(row["fire_count_7d"]),
            },
            "geometry": {"type": "Polygon", "coordinates": [poly]},
        })
    geojson = {"type": "FeatureCollection", "features": features}
    with open(out_path, "w") as f:
        json.dump(geojson, f)
    print(f"Saved {len(features)} grid cells to {out_path}")


# ---------------------------------------------------------------- self-test
if __name__ == "__main__":
    bundle = load_model_bundle()
    print("Loaded model:", bundle["model_name"], "| threshold:", round(bundle["threshold"], 3))

    modis_raw = pd.read_csv("MODIS C6.1-Cambodia-2020-2025.csv")
    viirs_raw = pd.read_csv("SUOMI VIIRS C2-Cambodia-2020-2025.csv")

    result = predict_risk(modis_raw, viirs_raw, bundle, as_of_date="2025-12-28")
    print(result["risk_level"].value_counts())

    export_geojson(result, bundle, "pipeline_test_output.geojson")
