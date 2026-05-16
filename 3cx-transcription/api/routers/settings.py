"""Admin settings API — read/write non-sensitive settings from DB."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from api.auth import get_current_admin
from api.config import settings as app_settings
from api.db.base import get_db
from api.models.setting import Setting

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
