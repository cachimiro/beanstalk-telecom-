"""GCS Pub/Sub push webhook and AssemblyAI callback webhook."""
import base64
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.config import settings
from api.db.base import get_db
from api.db.logger import log_async
from api.models.recording_job import RecordingJob
from api.services.parser import parse_recording_path
from api.services.matcher import match_user
from api.services.pubsub_auth import validate_oidc_token, validate_shared_secret
from api.rq_queue import enqueue_job, enqueue_continuation

router = APIRouter()
logger = logging.getLogger(__name__)


# ── GCS / Pub/Sub webhook ──────────────────────────────────────────────────────

@router.post("/gcs")
async def gcs_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
    x_webhook_secret: Optional[str] = Header(default=None),
):
    # ── Auth ──────────────────────────────────────────────────────────────────
    authenticated = False

    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
        audience = str(request.url)
        authenticated = await validate_oidc_token(token, audience)

    if not authenticated:
        authenticated = validate_shared_secret(x_webhook_secret)

    if not authenticated:
        logger.warning("Rejected unauthenticated GCS webhook request from %s", request.client.host)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    # ── Parse Pub/Sub envelope ────────────────────────────────────────────────
    body = await request.json()
    message = body.get("message", {})
    attributes = message.get("attributes", {})

    event_type = attributes.get("eventType", "")
    if event_type != "OBJECT_FINALIZE":
        logger.debug("Ignoring Pub/Sub event type: %s", event_type)
        return {"status": "ignored", "reason": f"event_type={event_type}"}

    # Decode base64 message.data → GCS notification JSON
    raw_data = message.get("data", "")
    try:
        gcs_notification = json.loads(base64.b64decode(raw_data).decode("utf-8"))
    except Exception as exc:
        logger.error("Failed to decode Pub/Sub message.data: %s", exc)
        return {"status": "ignored", "reason": "invalid_data"}

    bucket = gcs_notification.get("bucket") or attributes.get("bucketId", "")
    object_name = gcs_notification.get("name") or attributes.get("objectId", "")
    generation = gcs_notification.get("generation") or attributes.get("objectGeneration", "")
    file_size = gcs_notification.get("size")
    message_id = message.get("messageId", "")

    if not object_name:
        logger.error("No object name in GCS notification")
        return {"status": "ignored", "reason": "no_object_name"}

    logger.info("GCS OBJECT_FINALIZE: bucket=%s object=%s generation=%s", bucket, object_name, generation)

    # ── Parse filename ────────────────────────────────────────────────────────
    parsed = parse_recording_path(object_name)

    # ── Deduplicate ───────────────────────────────────────────────────────────
    existing = await db.execute(
        select(RecordingJob).where(
            RecordingJob.gcs_bucket == bucket,
            RecordingJob.gcs_object_name == object_name,
            RecordingJob.gcs_generation == generation,
        )
    )
    if existing.scalar_one_or_none():
        logger.info("Duplicate GCS event ignored: %s", object_name)
        return {"status": "ignored", "reason": "duplicate"}

    # ── Create job ────────────────────────────────────────────────────────────
    job = RecordingJob(
        gcs_bucket=bucket,
        gcs_object_name=object_name,
        gcs_generation=generation,
        file_size=int(file_size) if file_size else None,
        status="received",
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

        # Match user
        matched = await match_user(db, parsed)
        if matched:
            job.matched_user_id = matched.id
            job.recipient_email = matched.email
        else:
            job.status = "unmatched"
    else:
        job.status = "failed_parser"

    try:
        db.add(job)
        await db.flush()  # get job.id before commit
        await log_async(db, job.id, "info", f"Job created from GCS event messageId={message_id}")
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.info("Race-condition duplicate ignored: %s", object_name)
        return {"status": "ignored", "reason": "duplicate"}

    # ── Post-creation actions ─────────────────────────────────────────────────
    if job.status == "failed_parser":
        from api.services.email import send_admin_alert_parser_failure
        background_tasks.add_task(send_admin_alert_parser_failure, object_name)
        logger.error("Parser failure for object: %s", object_name)
        return {"status": "ok", "job_id": str(job.id), "note": "parser_failed"}

    if job.status == "unmatched":
        from api.services.email import send_admin_alert_unmatched
        background_tasks.add_task(send_admin_alert_unmatched, object_name, parsed)
        logger.warning("Unmatched recording: %s", object_name)
        return {"status": "ok", "job_id": str(job.id), "note": "unmatched"}

    # Enqueue for processing
    enqueue_job(str(job.id))
    await db.execute(
        RecordingJob.__table__.update()
        .where(RecordingJob.id == job.id)
        .values(status="queued")
    )
    await db.commit()

    logger.info("Job %s queued for processing", job.id)
    return {"status": "ok", "job_id": str(job.id)}


# ── AssemblyAI callback webhook ────────────────────────────────────────────────

@router.post("/assemblyai")
async def assemblyai_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    x_assemblyai_secret: Optional[str] = Header(default=None),
):
    # Validate shared secret
    if settings.ASSEMBLYAI_WEBHOOK_SECRET:
        if x_assemblyai_secret != settings.ASSEMBLYAI_WEBHOOK_SECRET:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    body = await request.json()
    transcript_id = body.get("transcript_id")
    transcript_status = body.get("status")

    if not transcript_id:
        return {"status": "ignored"}

    logger.info("AssemblyAI callback: transcript_id=%s status=%s", transcript_id, transcript_status)

    # Find the job
    result = await db.execute(
        select(RecordingJob).where(RecordingJob.assemblyai_transcript_id == transcript_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        logger.warning("No job found for transcript_id=%s", transcript_id)
        return {"status": "not_found"}

    if transcript_status == "error":
        job.status = "failed"
        job.error_message = f"AssemblyAI transcription error for transcript_id={transcript_id}"
        await log_async(db, job.id, "error", job.error_message)
        await db.commit()
        from api.services.email import send_admin_alert_job_failed
        background_tasks.add_task(send_admin_alert_job_failed, job)
        return {"status": "ok"}

    if transcript_status == "completed":
        # Enqueue continuation (summarise + email) in worker
        enqueue_continuation(str(job.id), transcript_id)
        await log_async(db, job.id, "info", f"AssemblyAI transcript ready, enqueued continuation")

    return {"status": "ok"}
