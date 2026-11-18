import os
from pathlib import Path
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
CONTAINER_NAME = "raw-data"
RAW_DATA_DIR = BASE_DIR / "data_raw"


def upload_raw_files():
    if not CONNECTION_STRING:
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING not found in .env")

    print(f"Connecting to Azure container '{CONTAINER_NAME}'...")
    client = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    container = client.get_container_client(CONTAINER_NAME)

    files = [f for f in sorted(RAW_DATA_DIR.iterdir()) if f.is_file() and not f.name.startswith(".")]
    if not files:
        print("No raw data files found in data_raw/")
        return

    print(f"Uploading {len(files)} files to Azure...")
    for path in files:
        blob_client = container.get_blob_client(path.name)
        with open(path, "rb") as f:
            blob_client.upload_blob(f, overwrite=True)
        print(f"  uploaded {path.name}")

    print(f"\nCurrent blobs in '{CONTAINER_NAME}':")
    for blob in container.list_blobs():
        print(f"  - {blob.name} ({blob.size / 1024:.1f} KB)")


if __name__ == "__main__":
    upload_raw_files()
