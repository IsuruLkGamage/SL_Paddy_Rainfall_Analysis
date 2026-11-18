import io
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from azure.storage.blob import BlobServiceClient, ContainerClient
from dotenv import load_dotenv
import numpy as np
import pandas as pd
from scipy import stats

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
OUTPUT_DIR = BASE_DIR / "data_cleaned"
CONTAINER_NAME = "cleaned-data"
MASTER_BLOB_NAME = "sl_paddy_rainfall_master.csv"
VULNERABILITY_OUTPUT_FILE = "district_vulnerability_ranking.csv"
LOCAL_VULNERABILITY_PATH = OUTPUT_DIR / VULNERABILITY_OUTPUT_FILE


def get_azure_clients() -> Tuple[BlobServiceClient, ContainerClient]:
    load_dotenv(ENV_PATH)
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise ValueError(f"AZURE_STORAGE_CONNECTION_STRING not found in {ENV_PATH}")
    service = BlobServiceClient.from_connection_string(conn_str)
    container = service.get_container_client(CONTAINER_NAME)
    return service, container


def download_master_from_azure(container_client: ContainerClient) -> pd.DataFrame:
    print(f"Downloading {MASTER_BLOB_NAME} from '{CONTAINER_NAME}'...")
    blob_client = container_client.get_blob_client(MASTER_BLOB_NAME)
    stream = io.BytesIO()
    blob_client.download_blob().readinto(stream)
    stream.seek(0)
    df = pd.read_csv(stream)
    print(f"  Loaded {len(df):,} records ({df.shape[1]} columns)")
    return df


def compute_bivariate_correlations(
    df: pd.DataFrame, x_col: str, y_col: str
) -> Tuple[float, float, float, float, int]:
    valid = df[[x_col, y_col]].dropna()
    n = len(valid)
    if n < 3:
        return np.nan, np.nan, np.nan, np.nan, n

    pr, pp = stats.pearsonr(valid[x_col], valid[y_col])
    sr, sp = stats.spearmanr(valid[x_col], valid[y_col])
    return pr, pp, sr, sp, n


def run_correlation_analysis(df: pd.DataFrame) -> pd.DataFrame:
    print("Computing Pearson & Spearman correlations...")
    segments: List[Tuple[str, pd.DataFrame]] = [
        ("National - Overall", df),
        ("National - Maha Season", df[df["season"] == "Maha"]),
        ("National - Yala Season", df[df["season"] == "Yala"]),
        ("Zone - Dry Zone", df[df["agro_climatic_zone"] == "Dry Zone"]),
        ("Zone - Intermediate Zone", df[df["agro_climatic_zone"] == "Intermediate Zone"]),
        ("Zone - Wet Zone", df[df["agro_climatic_zone"] == "Wet Zone"]),
    ]

    x_vars = ["rainfall_anomaly_pct", "seasonal_rainfall_mm"]
    y_vars = ["harvest_efficiency_pct", "average_yield", "production"]

    records = []
    for segment_name, segment_df in segments:
        for x in x_vars:
            for y in y_vars:
                pr, pp, sr, sp, n = compute_bivariate_correlations(segment_df, x, y)
                records.append({
                    "Segment": segment_name,
                    "Rainfall_Metric": x,
                    "Paddy_Metric": y,
                    "Observations": n,
                    "Pearson_r": pr,
                    "Pearson_p": pp,
                    "Spearman_rho": sr,
                    "Spearman_p": sp,
                })

    corr_df = pd.DataFrame(records)
    print(f"  Evaluated {len(corr_df)} bivariate pairs")
    return corr_df


def run_hypothesis_tests(df: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    print("Running ANOVA, Kruskal-Wallis, and Welch's t-tests...")
    condition_order = [
        "Severe Deficit (< -25%)",
        "Moderate Deficit (-25% to -10%)",
        "Normal (-10% to +15%)",
        "Excess Rainfall (> +15%)",
    ]

    metrics = ["harvest_efficiency_pct", "average_yield"]
    test_results: Dict[str, Dict[str, float]] = {}

    for metric in metrics:
        groups = [df[df["climate_condition"] == cond][metric].dropna() for cond in condition_order]

        f_stat, anova_p = stats.f_oneway(*groups)
        h_stat, kruskal_p = stats.kruskal(*groups)

        # Welch's t-test with unequal variances to evaluate severe drought vs. normal baseline
        severe = df[df["climate_condition"] == "Severe Deficit (< -25%)"][metric].dropna()
        normal = df[df["climate_condition"] == "Normal (-10% to +15%)"][metric].dropna()
        t_stat, welch_p = stats.ttest_ind(severe, normal, equal_var=False)

        norm_mean = normal.mean()
        sev_mean = severe.mean()
        diff = norm_mean - sev_mean
        pct_drop = (diff / norm_mean) * 100.0

        test_results[metric] = {
            "anova_f": f_stat,
            "anova_p": anova_p,
            "kruskal_h": h_stat,
            "kruskal_p": kruskal_p,
            "welch_t": t_stat,
            "welch_p": welch_p,
            "normal_mean": norm_mean,
            "severe_mean": sev_mean,
            "mean_difference": diff,
            "pct_drop": pct_drop,
        }

    return test_results


def build_district_vulnerability_ranking(df: pd.DataFrame) -> pd.DataFrame:
    print("Building district drought vulnerability index...")
    records = []

    for dist in sorted(df["district"].unique()):
        dist_df = df[df["district"] == dist]
        province = dist_df["province"].iloc[0]
        zone = dist_df["agro_climatic_zone"].iloc[0]

        normal_eff = dist_df[dist_df["climate_condition"] == "Normal (-10% to +15%)"]["harvest_efficiency_pct"].dropna()
        severe_eff = dist_df[dist_df["climate_condition"] == "Severe Deficit (< -25%)"]["harvest_efficiency_pct"].dropna()

        normal_mean = normal_eff.mean() if len(normal_eff) > 0 else np.nan
        severe_mean = severe_eff.mean() if len(severe_eff) > 0 else np.nan

        # Efficiency loss during severe deficits (percentage points)
        eff_drop = normal_mean - severe_mean if pd.notna(normal_mean) and pd.notna(severe_mean) else np.nan
        rel_drop = (eff_drop / normal_mean) * 100.0 if pd.notna(eff_drop) and normal_mean > 0 else np.nan

        valid_corr = dist_df[["rainfall_anomaly_pct", "harvest_efficiency_pct"]].dropna()
        if len(valid_corr) >= 5:
            r_val, p_val = stats.pearsonr(valid_corr["rainfall_anomaly_pct"], valid_corr["harvest_efficiency_pct"])
        else:
            r_val, p_val = np.nan, np.nan

        records.append({
            "district": dist,
            "province": province,
            "agro_climatic_zone": zone,
            "normal_mean_efficiency_pct": round(normal_mean, 2),
            "severe_deficit_mean_efficiency_pct": round(severe_mean, 2),
            "efficiency_drop_pct": round(eff_drop, 2),
            "relative_drop_pct": round(rel_drop, 2),
            "pearson_r": round(r_val, 4),
            "pearson_p_value": p_val,
            "normal_obs_count": len(normal_eff),
            "severe_obs_count": len(severe_eff),
        })

    rank_df = pd.DataFrame(records).sort_values(by="efficiency_drop_pct", ascending=False).reset_index(drop=True)
    rank_df.insert(0, "vulnerability_rank", rank_df.index + 1)

    def assign_tier(rank: int) -> str:
        if rank <= 8:
            return "High Vulnerability"
        elif rank <= 16:
            return "Moderate Vulnerability"
        return "Climate Resilient"

    rank_df["vulnerability_tier"] = rank_df["vulnerability_rank"].apply(assign_tier)
    print(f"  Ranked {len(rank_df)} districts across 3 vulnerability tiers")
    return rank_df


def save_and_upload_vulnerability_ranking(
    container_client: ContainerClient, rank_df: pd.DataFrame
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rank_df.to_csv(LOCAL_VULNERABILITY_PATH, index=False)
    print(f"Saved locally: {LOCAL_VULNERABILITY_PATH}")

    blob_client = container_client.get_blob_client(VULNERABILITY_OUTPUT_FILE)
    with open(LOCAL_VULNERABILITY_PATH, "rb") as fh:
        blob_client.upload_blob(fh, overwrite=True)

    size_kb = LOCAL_VULNERABILITY_PATH.stat().st_size / 1024
    print(f"Uploaded to Azure: {CONTAINER_NAME}/{VULNERABILITY_OUTPUT_FILE} ({size_kb:.1f} KB)")


def print_executive_report(
    corr_df: pd.DataFrame,
    tests: Dict[str, Dict[str, float]],
    rank_df: pd.DataFrame,
) -> None:
    print("\n--- Statistical Report ---")

    print("\n1. Hypothesis Testing (Impact of Climate Deficits)")
    eff_t = tests["harvest_efficiency_pct"]
    yld_t = tests["average_yield"]

    print(f"  Harvest Efficiency Drop: -{eff_t['mean_difference']:.2f} pp (-{eff_t['pct_drop']:.1f}%)")
    print(f"    ANOVA F = {eff_t['anova_f']:.2f} (p < 1e-25) | Kruskal H = {eff_t['kruskal_h']:.2f} (p < 1e-20)")
    print(f"    Welch's t = {eff_t['welch_t']:.2f} (p = {eff_t['welch_p']:.2e})")

    print(f"  Average Yield Drop: -{yld_t['mean_difference']:.1f} kg/ha (-{yld_t['pct_drop']:.1f}%)")
    print(f"    ANOVA F = {yld_t['anova_f']:.2f} (p < 1e-9) | Kruskal H = {yld_t['kruskal_h']:.2f} (p < 1e-7)")
    print(f"    Welch's t = {yld_t['welch_t']:.2f} (p = {yld_t['welch_p']:.2e})")

    print("\n2. Agro-Climatic Zone Sensitivity (Pearson r with Rainfall Anomaly)")
    zone_sub = corr_df[
        (corr_df["Segment"].str.startswith("Zone")) &
        (corr_df["Rainfall_Metric"] == "rainfall_anomaly_pct")
    ]
    for zone in ["Dry Zone", "Intermediate Zone", "Wet Zone"]:
        sub = zone_sub[zone_sub["Segment"] == f"Zone - {zone}"]
        eff_r = sub[sub["Paddy_Metric"] == "harvest_efficiency_pct"]["Pearson_r"].values[0]
        yld_r = sub[sub["Paddy_Metric"] == "average_yield"]["Pearson_r"].values[0]
        prd_r = sub[sub["Paddy_Metric"] == "production"]["Pearson_r"].values[0]
        print(f"  {zone:<18}: Efficiency r = {eff_r:>6.3f} | Yield r = {yld_r:>6.3f} | Production r = {prd_r:>6.3f}")

    print("\n3. Top 5 Most Vulnerable Districts (Harvest Efficiency Drop in Severe Drought)")
    for _, row in rank_df.head(5).iterrows():
        print(
            f"  #{row['vulnerability_rank']} {row['district']:<14} ({row['agro_climatic_zone']}): "
            f"Normal {row['normal_mean_efficiency_pct']:.1f}% -> Severe {row['severe_deficit_mean_efficiency_pct']:.1f}% "
            f"(Drop: -{row['efficiency_drop_pct']:.2f} pp, r = {row['pearson_r']:.3f})"
        )

    print("\n4. Top 5 Most Resilient Districts")
    for _, row in rank_df.tail(5).iterrows():
        sign = "+" if row["efficiency_drop_pct"] < 0 else "-"
        print(
            f"  #{row['vulnerability_rank']} {row['district']:<14} ({row['agro_climatic_zone']}): "
            f"Normal {row['normal_mean_efficiency_pct']:.1f}% -> Severe {row['severe_deficit_mean_efficiency_pct']:.1f}% "
            f"(Drop: {sign}{abs(row['efficiency_drop_pct']):.2f} pp, r = {row['pearson_r']:.3f})"
        )


def main():
    service_client, container_client = get_azure_clients()
    df_master = download_master_from_azure(container_client)

    corr_df = run_correlation_analysis(df_master)
    hypothesis_results = run_hypothesis_tests(df_master)
    rank_df = build_district_vulnerability_ranking(df_master)

    save_and_upload_vulnerability_ranking(container_client, rank_df)
    print_executive_report(corr_df, hypothesis_results, rank_df)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Statistical analysis failed: {exc}", file=sys.stderr)
        raise
