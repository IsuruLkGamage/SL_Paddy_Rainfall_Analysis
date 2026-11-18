import io
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
from azure.storage.blob import BlobServiceClient

pd.set_option("display.max_columns", 15)
pd.set_option("display.width", 1000)
pd.set_option("display.max_rows", 20)

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
CONTAINER_NAME = "raw-data"


def load_azure_client() -> BlobServiceClient:
    load_dotenv(ENV_PATH)
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise ValueError(f"AZURE_STORAGE_CONNECTION_STRING not found in {ENV_PATH}")
    return BlobServiceClient.from_connection_string(conn_str)


def download_blob_to_memory(container_client, blob_name: str) -> io.BytesIO:
    blob_client = container_client.get_blob_client(blob=blob_name)
    stream = io.BytesIO()
    blob_client.download_blob().readinto(stream)
    stream.seek(0)
    return stream


def inspect_rainfall(container_client, blob_name: str) -> None:
    print(f"\n--- Rainfall Dataset: {blob_name} ---")
    stream = download_blob_to_memory(container_client, blob_name)
    df = pd.read_csv(stream)

    print(f"Shape: {df.shape[0]:,} rows x {df.shape[1]} columns")
    print(f"Columns: {', '.join(df.columns)}")
    print("\nSample rows:")
    print(df.head(5))


def inspect_excel_header(container_client, blob_name: str, category_label: str) -> None:
    print(f"\n--- DCS {category_label} Dataset: {blob_name} ---")
    stream = download_blob_to_memory(container_client, blob_name)
    excel_file = pd.ExcelFile(stream)
    print(f"Sheets: {excel_file.sheet_names}")

    for sheet in excel_file.sheet_names:
        stream.seek(0)
        df_raw = pd.read_excel(stream, sheet_name=sheet, header=None, nrows=12)
        cols = min(12, df_raw.shape[1])
        print(f"\nFirst 12 raw rows of '{sheet}' (showing {cols}/{df_raw.shape[1]} cols):")
        print(df_raw.iloc[:12, :cols])


def main():
    print(f"Connecting to Azure container '{CONTAINER_NAME}'...")
    client = load_azure_client()
    container = client.get_container_client(CONTAINER_NAME)

    blobs = list(container.list_blobs())
    if not blobs:
        print("No blobs found.")
        return

    print(f"Found {len(blobs)} blobs:")
    for b in sorted(blobs, key=lambda x: x.name):
        print(f"  - {b.name:<52} ({b.size / 1024:>7.1f} KB)")

    blob_names = [b.name for b in blobs]

    rainfall_blob = next((b for b in blob_names if b.endswith(".csv")), None)
    production_blob = next((b for b in blob_names if "production" in b.lower() and b.endswith(".xlsx")), None)
    # Handle historical typo 'Yeild' in raw DCS filenames
    yield_blob = next((b for b in blob_names if ("yield" in b.lower() or "yeild" in b.lower()) and b.endswith(".xlsx")), None)
    extent_blob = next((b for b in blob_names if "extent" in b.lower() and b.endswith(".xlsx")), None)

    if rainfall_blob:
        inspect_rainfall(container, rainfall_blob)

    dcs_targets = [
        (production_blob, "Production"),
        (yield_blob, "Average Yield"),
        (extent_blob, "Extent"),
    ]
    for name, label in dcs_targets:
        if name:
            inspect_excel_header(container, name, label)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error during inspection: {exc}", file=sys.stderr)
        raise
