"""Admin jobs API — list, detail, retry."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload

from api.auth import get_current_admin
from api.db.base import get_db
from api.models.recording_job import RecordingJob
from api.models.processing_log import ProcessingLog
from api.models.user import User
from api.rq_queue import enqueue_retry, enqueue_email_retry

router = APIRouter(dependencies=[Depends(get_current_admin)])


def _job_out(job: RecordingJob, user: Optional[User] = None) -> dict:
    return {
        "id": str(job.id),
        "gcs_bucket": job.gcs_bucket,
        "gcs_object_name": job.gcs_object_name,
        "gcs_generation": job.gcs_generation,
        "file_size": job.file_size,
        "extracted_name": job.extracted_name,
        "folder_extension": job.folder_extension,
        "filename_extension": job.filename_extension,
        "phone_number": job.phone_number,
        "call_timestamp": job.call_timestamp,
        "call_id": job.call_id,
        "file_extension": job.file_extension,
        "matched_user_id": str(job.matched_user_id) if job.matched_user_id else None,
        "matched_user_name": user.full_name if user else None,
        "recipient_email": job.recipient_email,
        "status": job.status,
        "summary_status": job.summary_status,
        "assemblyai_transcript_id": job.assemblyai_transcript_id,
        "email_message_id": job.email_message_id,
        "email_transcript_message_id": job.email_transcript_message_id,
        "speaker_confidence_score": job.speaker_confidence_score,
        "speaker_classification_reason": job.speaker_classification_reason,
        "detected_language": job.detected_language,
        "error_message": job.error_message,
        "retry_count": job.retry_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "emailed_at": job.emailed_at.isoformat() if job.emailed_at else None,
    }


@router.get("")
async def list_jobs(
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = select(RecordingJob).order_by(RecordingJob.created_at.desc())

    if status:
        q = q.where(RecordingJob.status == status)
    if search:
        term = f"%{search}%"
        q = q.where(
            or_(
                RecordingJob.gcs_object_name.ilike(term),
                RecordingJob.extracted_name.ilike(term),
                RecordingJob.phone_number.ilike(term),
                RecordingJob.recipient_email.ilike(term),
            )
        )

    count_q = select(func.count()).select_from(RecordingJob)
    if status:
        count_q = count_q.where(RecordingJob.status == status)
    total = (await db.execute(count_q)).scalar()

    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    jobs = result.scalars().all()

    # Fetch matched users in bulk
    user_ids = [j.matched_user_id for j in jobs if j.matched_user_id]
    users_map = {}
    if user_ids:
        users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
        for u in users_result.scalars().all():
            users_map[u.id] = u

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_job_out(j, users_map.get(j.matched_user_id)) for j in jobs],
    }


@router.get("/{job_id}")
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RecordingJob)
        .where(RecordingJob.id == uuid.UUID(job_id))
        .options(selectinload(RecordingJob.logs))
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    user = None
    if job.matched_user_id:
        u_result = await db.execute(select(User).where(User.id == job.matched_user_id))
        user = u_result.scalar_one_or_none()

    logs = [
        {
            "id": str(log.id),
            "level": log.level,
            "message": log.message,
            "metadata": log.metadata_json,
            "created_at": log.created_at.isoformat(),
        }
        for log in sorted(job.logs, key=lambda l: l.created_at)
    ]

    return {**_job_out(job, user), "logs": logs}


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RecordingJob).where(RecordingJob.id == uuid.UUID(job_id)))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in ("failed", "unmatched", "failed_parser", "email_failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot retry job with status '{job.status}'. Only failed/unmatched/failed_parser/email_failed jobs can be retried.",
        )

    if job.status == "email_failed":
        # Email-only retry: the pipeline already completed — just re-send the
        # stored emails without re-downloading from GCS or re-transcribing.
        job.status = "queued"
        job.error_message = None
        await db.commit()
        enqueue_email_retry(job_id)
        return {"status": "queued", "job_id": job_id, "mode": "email_only"}

    job.status = "queued"
    job.error_message = None
    job.retry_count = 0
    await db.commit()

    enqueue_retry(job_id)
    return {"status": "queued", "job_id": job_id}
