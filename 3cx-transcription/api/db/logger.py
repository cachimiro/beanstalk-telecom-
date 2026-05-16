"""Utility to write processing_logs rows synchronously (used by worker) and async (used by API)."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert

from api.models.processing_log import ProcessingLog


async def log_async(
    db: AsyncSession,
    job_id: uuid.UUID | None,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    entry = ProcessingLog(
        recording_job_id=job_id,
        level=level,
        message=message,
        metadata_json=metadata,
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    await db.commit()


def log_sync(session, job_id, level: str, message: str, metadata: dict | None = None) -> None:
    """Synchronous version for use in RQ worker tasks."""
    from api.models.processing_log import ProcessingLog as PL
    entry = PL(
        recording_job_id=job_id,
        level=level,
        message=message,
        metadata_json=metadata,
        created_at=datetime.utcnow(),
    )
    session.add(entry)
    session.commit()
