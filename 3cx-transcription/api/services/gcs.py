"""Google Cloud Storage helpers — download recordings using service account credentials."""
import json
import logging
import os
from pathlib import Path

from google.cloud import storage
from google.oauth2 import service_account

from api.config import settings

logger = logging.getLogger(__name__)

_client: storage.Client | None = None


def _get_client() -> storage.Client:
    global _client
    if _client is not None:
        return _client

    if settings.GCP_SERVICE_ACCOUNT_JSON:
        info = json.loads(settings.GCP_SERVICE_ACCOUNT_JSON)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/devstorage.read_only"],
        )
        _client = storage.Client(project=settings.GCP_PROJECT_ID, credentials=creds)
    else:
        # Falls back to Application Default Credentials (useful in dev)
        _client = storage.Client(project=settings.GCP_PROJECT_ID)

    return _client


def download_recording(bucket_name: str, object_name: str, dest_path: str) -> int:
    """Download a GCS object to dest_path. Returns file size in bytes."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    blob.download_to_filename(dest_path)

    size = os.path.getsize(dest_path)
    logger.info("Downloaded gs://%s/%s → %s (%d bytes)", bucket_name, object_name, dest_path, size)
    return size


def delete_temp_file(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info("Deleted temp file: %s", path)
    except Exception as exc:
        logger.warning("Failed to delete temp file %s: %s", path, exc)


def get_signed_url(bucket_name: str, object_name: str, expiration_seconds: int = 3600) -> str:
    """Generate a signed URL for AssemblyAI to fetch the audio directly."""
    client = _get_client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    url = blob.generate_signed_url(
        expiration=expiration_seconds,
        method="GET",
        version="v4",
    )
    return url
