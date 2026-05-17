"""Admin settings API — read/write non-sensitive settings from DB."""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from api.auth import get_current_admin
from api.config import settings as app_settings
from api.db.base import get_db
from api.models.setting import Setting
from api.models.recording_job import RecordingJob
from api.rq_queue import enqueue_job
from api.services.gcs import list_recent_recordings
from api.services.parser import parse_recording_path

router = APIRouter(dependencies=[Depends(get_current_admin)])

# Keys that can be edited via dashboard (non-sensitive only)
EDITABLE_KEYS = {
    "debug_mode",
    "store_transcripts",
    "max_retries",
    "default_email_subject",
    "admin_email",
}

# Masked env var display (last 4 chars only)
def _mask(value: str) -> str:
    if not value or len(value) < 8:
        return "****"
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


class SettingsUpdate(BaseModel):
    debug_mode: Optional[str] = None
    store_transcripts: Optional[str] = None
    max_retries: Optional[str] = None
    default_email_subject: Optional[str] = None
    admin_email: Optional[str] = None


@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db)):
    # DB settings
    result = await db.execute(select(Setting))
    db_settings = {s.key: s.value for s in result.scalars().all()}

    return {
        "db_settings": db_settings,
        "env_info": {
            "APP_URL": app_settings.APP_URL,
            "GCP_BUCKET_NAME": app_settings.GCP_BUCKET_NAME,
            "GCP_BUCKET_LOCATION": app_settings.GCP_BUCKET_LOCATION,
            "ASSEMBLYAI_MODEL": app_settings.ASSEMBLYAI_MODEL,
            "ASSEMBLYAI_SPEAKER_DIARIZATION": app_settings.ASSEMBLYAI_SPEAKER_DIARIZATION,
            "OPENAI_SUMMARY_MODEL": app_settings.OPENAI_SUMMARY_MODEL,
            "OPENAI_FALLBACK_MODELS": app_settings.OPENAI_FALLBACK_MODELS,
            "GMAIL_ADDRESS": app_settings.GMAIL_ADDRESS,
            "EMAIL_FROM_NAME": app_settings.EMAIL_FROM_NAME,
            "MAX_RETRIES": app_settings.MAX_RETRIES,
            "DELETE_TEMP_FILES": app_settings.DELETE_TEMP_FILES,
            # Masked API keys
            "ASSEMBLYAI_API_KEY": _mask(app_settings.effective_assemblyai_key),
            "OPENAI_API_KEY": _mask(app_settings.OPENAI_API_KEY),
            "GMAIL_APP_PASSWORD": _mask(app_settings.GMAIL_APP_PASSWORD),
        },
    }


class TestPipelineRun(BaseModel):
    gcs_object_name: str


@router.get("/test-pipeline/recordings")
async def list_test_recordings():
    """Return the 10 most recent recordings from GCS for manual pipeline testing."""
    try:
        recordings = list_recent_recordings(
            bucket_name=app_settings.GCP_BUCKET_NAME,
            prefix=app_settings.GCP_RECORDINGS_PREFIX,
            limit=10,
        )
        return {"recordings": recordings}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list GCS recordings: {exc}")


@router.post("/test-pipeline/run")
async def run_test_pipeline(body: TestPipelineRun, db: AsyncSession = Depends(get_db)):
    """Create a test job for the given GCS object and queue it through the full pipeline.

    The result email is always sent to ADMIN_EMAIL regardless of the recording's extension.
    """
    if not app_settings.ADMIN_EMAIL:
        raise HTTPException(status_code=400, detail="ADMIN_EMAIL is not configured")

    # Parse filename best-effort (don't fail if unparseable — it's a test)
    parsed = parse_recording_path(body.gcs_object_name)

    job = RecordingJob(
        gcs_bucket=app_settings.GCP_BUCKET_NAME,
        gcs_object_name=body.gcs_object_name,
        gcs_generation=f"test-{uuid.uuid4().hex[:8]}",
        status="queued",
        recipient_email=app_settings.ADMIN_EMAIL,
        created_at=datetime.utcnow(),
    )

    if parsed:
        job.extracted_name = parsed.user_name
        job.folder_extension = parsed.folder_extension
        job.filename_extension = parsed.filename_extension
        job.phone_number = parsed.phone_number
        job.call_timestamp = parsed.timestamp
        job.call_id = parsed.call_id
        job.file_extension = parsed.file_extension

    db.add(job)
    await db.commit()
    await db.refresh(job)

    enqueue_job(str(job.id))

    return {"job_id": str(job.id), "status": "queued", "recipient_email": app_settings.ADMIN_EMAIL}


@router.put("")
async def update_settings(body: SettingsUpdate, db: AsyncSession = Depends(get_db)):
    updates = body.model_dump(exclude_none=True)

    for key, value in updates.items():
        if key not in EDITABLE_KEYS:
            continue
        result = await db.execute(select(Setting).where(Setting.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = value
            setting.updated_at = datetime.utcnow()
        else:
            db.add(Setting(key=key, value=value))

    await db.commit()
    return {"status": "updated", "keys": list(updates.keys())}
