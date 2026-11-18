import io
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContainerClient
from dotenv import load_dotenv
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
OUTPUT_DIR = BASE_DIR / "data_cleaned"
OUTPUT_FILE_NAME = "sl_paddy_rainfall_master.csv"
LOCAL_OUTPUT_PATH = OUTPUT_DIR / OUTPUT_FILE_NAME

RAW_CONTAINER_NAME = "raw-data"
CLEANED_CONTAINER_NAME = "cleaned-data"

# Standardizing historical spelling variants across DCS Excel sheets
DISTRICT_SYNONYMS: Dict[str, str] = {
    "COLOMBO": "Colombo",
    "GAMPAHA": "Gampaha",
    "KALUTARA": "Kalutara",
    "KANDY": "Kandy",
    "MATALE": "Matale",
    "NUWARA ELIYA": "Nuwara Eliya",
    "GALLE": "Galle",
    "MATARA": "Matara",
    "HAMBANTOTA": "Hambantota",
    "JAFFNA": "Jaffna",
    "MANNAR": "Mannar",
    "VAVUNIYA": "Vavuniya",
    "MULLATIVU": "Mullaitivu",
    "MULLAITIVU": "Mullaitivu",
    "KILINOCHCHI": "Kilinochchi",
    "KILINOCHCHIYA": "Kilinochchi",
    "KILLINOCHCHI": "Kilinochchi",
    "BATTICALOA": "Batticaloa",
    "AMPARA": "Ampara",
    "TRINCOMALEE": "Trincomalee",
    "KURUNEGALA": "Kurunegala",
    "PUTTALAM": "Puttalam",
    "ANURADHAPURA": "Anuradhapura",
    "POLONNARUWA": "Polonnaruwa",
    "BADULLA": "Badulla",
    "MONERAGALA": "Monaragala",
    "MONARAGALA": "Monaragala",
    "RATNAPURA": "Ratnapura",
    "KEGALLE": "Kegalle",
}

# CHIRPS district PCODE mapping (adm_level == 2)
PCODE_TO_DISTRICT: Dict[str, str] = {
    "LK11": "Colombo",
    "LK12": "Gampaha",
    "LK13": "Kalutara",
    "LK21": "Kandy",
    "LK22": "Matale",
    "LK23": "Nuwara Eliya",
    "LK31": "Galle",
    "LK32": "Matara",
    "LK33": "Hambantota",
    "LK41": "Jaffna",
    "LK42": "Mannar",
    "LK43": "Vavuniya",
    "LK44": "Mullaitivu",
    "LK45": "Kilinochchi",
    "LK51": "Batticaloa",
    "LK52": "Ampara",
    "LK53": "Trincomalee",
    "LK61": "Kurunegala",
    "LK62": "Puttalam",
    "LK71": "Anuradhapura",
    "LK72": "Polonnaruwa",
    "LK81": "Badulla",
    "LK82": "Monaragala",
    "LK91": "Ratnapura",
    "LK92": "Kegalle",
}

DISTRICT_TO_PROVINCE: Dict[str, str] = {
    "Colombo": "Western",
    "Gampaha": "Western",
    "Kalutara": "Western",
    "Kandy": "Central",
    "Matale": "Central",
    "Nuwara Eliya": "Central",
    "Galle": "Southern",
    "Matara": "Southern",
    "Hambantota": "Southern",
    "Jaffna": "Northern",
    "Mannar": "Northern",
    "Vavuniya": "Northern",
    "Mullaitivu": "Northern",
    "Kilinochchi": "Northern",
    "Batticaloa": "Eastern",
    "Ampara": "Eastern",
    "Trincomalee": "Eastern",
    "Kurunegala": "North Western",
    "Puttalam": "North Western",
    "Anuradhapura": "North Central",
    "Polonnaruwa": "North Central",
    "Badulla": "Uva",
    "Monaragala": "Uva",
    "Ratnapura": "Sabaragamuwa",
    "Kegalle": "Sabaragamuwa",
}

DISTRICT_TO_ZONE: Dict[str, str] = {
    "Colombo": "Wet Zone",
    "Gampaha": "Wet Zone",
    "Kalutara": "Wet Zone",
    "Kandy": "Wet Zone",
    "Matale": "Intermediate Zone",
    "Nuwara Eliya": "Wet Zone",
    "Galle": "Wet Zone",
    "Matara": "Wet Zone",
    "Hambantota": "Dry Zone",
    "Jaffna": "Dry Zone",
    "Mannar": "Dry Zone",
    "Vavuniya": "Dry Zone",
    "Mullaitivu": "Dry Zone",
    "Kilinochchi": "Dry Zone",
    "Batticaloa": "Dry Zone",
    "Ampara": "Dry Zone",
    "Trincomalee": "Dry Zone",
    "Kurunegala": "Intermediate Zone",
    "Puttalam": "Dry Zone",
    "Anuradhapura": "Dry Zone",
    "Polonnaruwa": "Dry Zone",
    "Badulla": "Intermediate Zone",
    "Monaragala": "Intermediate Zone",
    "Ratnapura": "Wet Zone",
    "Kegalle": "Wet Zone",
}


def get_blob_service_client() -> BlobServiceClient:
    load_dotenv(ENV_PATH)
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise ValueError(f"AZURE_STORAGE_CONNECTION_STRING not found in {ENV_PATH}")
    return BlobServiceClient.from_connection_string(conn_str)


def download_blob_bytes(container_client: ContainerClient, blob_name: str) -> io.BytesIO:
    blob_client = container_client.get_blob_client(blob_name)
    stream = io.BytesIO()
    blob_client.download_blob().readinto(stream)
    stream.seek(0)
    return stream


def clean_numeric_value(val: object) -> float:
    if pd.isna(val):
        return np.nan
    val_str = str(val).strip()
    if val_str in ("-", "", "..", "...", "nan", "None"):
        return np.nan
    try:
        return float(val_str.replace(",", ""))
    except ValueError:
        return np.nan


def parse_season_year(raw_label: object, season: str) -> Optional[int]:
    if pd.isna(raw_label):
        return None
    val_str = str(raw_label).strip()

    try:
        f = float(val_str)
        if f.is_integer():
            val_str = str(int(f))
    except ValueError:
        pass

    if season == "Maha":
        # '1981/1982' or '1981-1982' -> harvest year 1982
        m = re.search(r"(\d{4})[/-](\d{4})", val_str)
        if m:
            return int(m.group(2))
        m2 = re.search(r"(\d{4})[/-](\d{2})", val_str)
        if m2:
            return int(m2.group(1)[:2] + m2.group(2))
        m3 = re.findall(r"\d{4}", val_str)
        if m3:
            return int(m3[-1])
    else:
        # Note: DCS Yala file contains an old header typo '198' for 1980
        if val_str == "198":
            return 1980
        m = re.search(r"(\d{4})", val_str)
        if m:
            return int(m.group(1))

    return None


def locate_district_header(df_raw: pd.DataFrame) -> Tuple[int, int]:
    # Look for 'DISTRICT' cell dynamically in top rows (varies between row 1 and 2 across files)
    for r in range(min(6, df_raw.shape[0])):
        for c in range(min(4, df_raw.shape[1])):
            if str(df_raw.iloc[r, c]).strip().upper() == "DISTRICT":
                return r, c
    raise ValueError("Could not locate 'DISTRICT' header cell in raw sheet.")


def parse_production_excel(stream: io.BytesIO, season: str) -> pd.DataFrame:
    stream.seek(0)
    df_raw = pd.read_excel(stream, header=None)
    d_row, d_col = locate_district_header(df_raw)

    records = []
    season_cols = list(range(d_col + 1, df_raw.shape[1]))

    for r in range(d_row + 1, df_raw.shape[0]):
        raw_district = df_raw.iloc[r, d_col]
        if pd.isna(raw_district):
            continue

        clean_key = str(raw_district).strip().upper()
        if clean_key not in DISTRICT_SYNONYMS:
            continue

        district_name = DISTRICT_SYNONYMS[clean_key]

        for c in season_cols:
            raw_season_label = df_raw.iloc[d_row, c]
            year = parse_season_year(raw_season_label, season)
            if year is None:
                continue

            records.append({
                "district": district_name,
                "year": year,
                "season": season,
                "production": clean_numeric_value(df_raw.iloc[r, c]),
            })

    return pd.DataFrame(records)


def parse_multilevel_excel(stream: io.BytesIO, season: str, metric_name: str) -> pd.DataFrame:
    stream.seek(0)
    df_raw = pd.read_excel(stream, header=None)
    d_row, d_col = locate_district_header(df_raw)

    sub_row = d_row + 1
    # Forward-fill merged season cells across sub-columns (Major, Minor, Rainfed, Total)
    season_row = df_raw.iloc[d_row, d_col + 1:].ffill()
    sub_row_series = df_raw.iloc[sub_row, d_col + 1:]

    # Extract only the aggregate 'Total' column for each season
    total_cols = [
        col_idx
        for col_idx in sub_row_series.index
        if str(sub_row_series[col_idx]).strip().upper() == "TOTAL"
    ]

    records = []
    for r in range(sub_row + 1, df_raw.shape[0]):
        raw_district = df_raw.iloc[r, d_col]
        if pd.isna(raw_district):
            continue

        clean_key = str(raw_district).strip().upper()
        if clean_key not in DISTRICT_SYNONYMS:
            continue

        district_name = DISTRICT_SYNONYMS[clean_key]

        for c in total_cols:
            raw_season_label = season_row[c]
            year = parse_season_year(raw_season_label, season)
            if year is None:
                continue

            records.append({
                "district": district_name,
                "year": year,
                "season": season,
                metric_name: clean_numeric_value(df_raw.iloc[r, c]),
            })

    return pd.DataFrame(records)


def clean_dcs_paddy_datasets(blob_streams: Dict[str, io.BytesIO]) -> pd.DataFrame:
    print("Parsing DCS paddy datasets...")

    prod_maha = parse_production_excel(blob_streams["Production-Maha_Season-1979-2025.xlsx"], "Maha")
    prod_yala = parse_production_excel(blob_streams["Production-Yala_Season-1979-2025.xlsx"], "Yala")
    df_prod = pd.concat([prod_maha, prod_yala], ignore_index=True)

    yield_maha = parse_multilevel_excel(blob_streams["Average_Yeild-Maha_Season-1979-2025.xlsx"], "Maha", "average_yield")
    yield_yala = parse_multilevel_excel(blob_streams["Average_Yeild-Yala_Season-1979-2025.xlsx"], "Yala", "average_yield")
    df_yield = pd.concat([yield_maha, yield_yala], ignore_index=True)

    harv_maha = parse_multilevel_excel(blob_streams["Harvested_Extent-Maha_Seasons-1979-2025.xlsx"], "Maha", "harvested_extent")
    harv_yala = parse_multilevel_excel(blob_streams["Harvested_Extent-Yala_Seasons-1979-2025.xlsx"], "Yala", "harvested_extent")
    df_harv = pd.concat([harv_maha, harv_yala], ignore_index=True)

    sown_maha = parse_multilevel_excel(blob_streams["Sown_Extent-Maha_Seasons-1979-2025.xlsx"], "Maha", "sown_extent")
    sown_yala = parse_multilevel_excel(blob_streams["Sown_Extent-Yala_Seasons-1979-2025.xlsx"], "Yala", "sown_extent")
    df_sown = pd.concat([sown_maha, sown_yala], ignore_index=True)

    paddy_df = df_sown.merge(df_harv, on=["district", "year", "season"], how="outer")
    paddy_df = paddy_df.merge(df_yield, on=["district", "year", "season"], how="outer")
    paddy_df = paddy_df.merge(df_prod, on=["district", "year", "season"], how="outer")

    print(f"  Paddy records processed: {len(paddy_df):,} rows ({paddy_df['year'].min()}-{paddy_df['year'].max()})")
    return paddy_df


def map_date_to_agricultural_season(date: pd.Timestamp) -> Tuple[str, int]:
    # Maha: Oct-Dec (year Y) + Jan-Mar (year Y+1) -> harvest year Y+1
    # Yala: Apr-Sep (year Y) -> harvest year Y
    month = date.month
    year = date.year
    if month in (10, 11, 12):
        return "Maha", year + 1
    elif month in (1, 2, 3):
        return "Maha", year
    else:
        return "Yala", year


def clean_rainfall_dataset(stream: io.BytesIO) -> pd.DataFrame:
    print("Parsing CHIRPS rainfall dataset...")
    stream.seek(0)
    df_rf = pd.read_csv(stream)

    df_rf = df_rf[df_rf["adm_level"] == 2].copy()
    df_rf["district"] = df_rf["PCODE"].map(PCODE_TO_DISTRICT)
    df_rf = df_rf.dropna(subset=["district"]).copy()

    df_rf["date"] = pd.to_datetime(df_rf["date"])
    season_info = df_rf["date"].apply(map_date_to_agricultural_season)
    df_rf["season"] = [s[0] for s in season_info]
    df_rf["year"] = [s[1] for s in season_info]

    agg_rf = (
        df_rf.groupby(["district", "year", "season"], as_index=False)
        .agg(
            dekad_count=("rfh", "count"),
            seasonal_rainfall_mm=("rfh", "sum"),
            normal_rainfall_mm=("rfh_avg", "sum"),
        )
    )

    # Filter out boundary seasons with fewer than 16 dekads (standard season has 18)
    dropped = (agg_rf["dekad_count"] < 16).sum()
    agg_rf = agg_rf[agg_rf["dekad_count"] >= 16].drop(columns=["dekad_count"])

    print(f"  Rainfall seasonal records: {len(agg_rf):,} (filtered {dropped} incomplete seasons)")
    return agg_rf


def classify_climate_condition(anomaly_pct: float) -> str:
    if pd.isna(anomaly_pct):
        return np.nan
    if anomaly_pct < -25.0:
        return "Severe Deficit (< -25%)"
    elif -25.0 <= anomaly_pct < -10.0:
        return "Moderate Deficit (-25% to -10%)"
    elif -10.0 <= anomaly_pct <= 15.0:
        return "Normal (-10% to +15%)"
    else:
        return "Excess Rainfall (> +15%)"


def merge_and_engineer_features(paddy_df: pd.DataFrame, rainfall_df: pd.DataFrame) -> pd.DataFrame:
    print("Joining datasets and engineering domain features...")
    master = paddy_df.merge(rainfall_df, on=["district", "year", "season"], how="inner")

    master["province"] = master["district"].map(DISTRICT_TO_PROVINCE)
    master["agro_climatic_zone"] = master["district"].map(DISTRICT_TO_ZONE)

    master["rainfall_anomaly_pct"] = (
        (master["seasonal_rainfall_mm"] - master["normal_rainfall_mm"])
        / master["normal_rainfall_mm"]
    ) * 100.0

    master["harvest_efficiency_pct"] = (
        (master["harvested_extent"] / master["sown_extent"]) * 100.0
    ).clip(lower=0.0, upper=100.0)

    master["climate_condition"] = master["rainfall_anomaly_pct"].apply(classify_climate_condition)

    ordered_columns = [
        "district",
        "province",
        "agro_climatic_zone",
        "year",
        "season",
        "sown_extent",
        "harvested_extent",
        "harvest_efficiency_pct",
        "average_yield",
        "production",
        "seasonal_rainfall_mm",
        "normal_rainfall_mm",
        "rainfall_anomaly_pct",
        "climate_condition",
    ]
    master = master[ordered_columns].sort_values(
        by=["district", "year", "season"], ascending=[True, True, True]
    ).reset_index(drop=True)

    print(f"  Master dataset assembled: {master.shape[0]:,} rows x {master.shape[1]} columns")
    return master


def upload_master_to_azure(blob_service_client: BlobServiceClient, local_csv_path: Path) -> None:
    container_client = blob_service_client.get_container_client(CLEANED_CONTAINER_NAME)
    try:
        container_client.create_container()
    except ResourceExistsError:
        pass
    except Exception:
        pass

    blob_client = container_client.get_blob_client(OUTPUT_FILE_NAME)
    with open(local_csv_path, "rb") as fh:
        blob_client.upload_blob(fh, overwrite=True)

    size_kb = local_csv_path.stat().st_size / 1024
    print(f"Uploaded to Azure: {CLEANED_CONTAINER_NAME}/{OUTPUT_FILE_NAME} ({size_kb:.1f} KB)")


def print_summary_report(master: pd.DataFrame) -> None:
    print("\n--- Pipeline Summary ---")
    print(f"Total Rows: {master.shape[0]:,} | Columns: {master.shape[1]}")
    print(f"Year Range: {master['year'].min()} - {master['year'].max()}")
    print(f"Districts: {master['district'].nunique()} | Provinces: {master['province'].nunique()}")
    print(f"Agro-Climatic Zones: {', '.join(sorted(master['agro_climatic_zone'].unique()))}")

    print("\nMissing Values:")
    missing = master.isnull().sum()
    for col, count in missing[missing > 0].items():
        pct = (count / len(master)) * 100
        print(f"  {col:<24}: {count} ({pct:.1f}%)")

    print("\nClimate Condition Breakdown:")
    print(master["climate_condition"].value_counts().to_string())

    print("\nSample Data:")
    print(master.head(5).to_string(index=False))


def main():
    print(f"Connecting to Azure container '{RAW_CONTAINER_NAME}'...")
    client = get_blob_service_client()
    raw_container = client.get_container_client(RAW_CONTAINER_NAME)

    raw_files = [
        "sl_rainfall_raw.csv",
        "Production-Maha_Season-1979-2025.xlsx",
        "Production-Yala_Season-1979-2025.xlsx",
        "Average_Yeild-Maha_Season-1979-2025.xlsx",
        "Average_Yeild-Yala_Season-1979-2025.xlsx",
        "Harvested_Extent-Maha_Seasons-1979-2025.xlsx",
        "Harvested_Extent-Yala_Seasons-1979-2025.xlsx",
        "Sown_Extent-Maha_Seasons-1979-2025.xlsx",
        "Sown_Extent-Yala_Seasons-1979-2025.xlsx",
    ]

    streams: Dict[str, io.BytesIO] = {}
    for fname in raw_files:
        streams[fname] = download_blob_bytes(raw_container, fname)
    print(f"Loaded {len(streams)} raw files into memory.")

    paddy_df = clean_dcs_paddy_datasets(streams)
    rainfall_df = clean_rainfall_dataset(streams["sl_rainfall_raw.csv"])
    master_df = merge_and_engineer_features(paddy_df, rainfall_df)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    master_df.to_csv(LOCAL_OUTPUT_PATH, index=False)
    print(f"Saved locally: {LOCAL_OUTPUT_PATH}")

    upload_master_to_azure(client, LOCAL_OUTPUT_PATH)
    print_summary_report(master_df)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        raise
