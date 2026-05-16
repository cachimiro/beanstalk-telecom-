"""Redis queue helpers using RQ."""
import logging
from datetime import timedelta

import redis
from rq import Queue

from api.config import settings

logger = logging.getLogger(__name__)

_redis_conn: redis.Redis | None = None
_queue: Queue | None = None


def get_redis() -> redis.Redis:
    global _redis_conn
    if _redis_conn is None:
        _redis_conn = redis.from_url(settings.REDIS_URL)
    return _redis_conn


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue("default", connection=get_redis())
    return _queue


def enqueue_job(job_id: str) -> None:
    """Enqueue a new recording job for processing."""
    q = get_queue()
    q.enqueue(
        "worker.tasks.process_recording_job",
        job_id,
        job_timeout=600,
    )
    logger.info("Enqueued process_recording_job for job_id=%s", job_id)


def enqueue_continuation(job_id: str, transcript_id: str) -> None:
    """Enqueue summarise+email step after AssemblyAI callback."""
    q = get_queue()
    q.enqueue(
        "worker.tasks.continue_after_transcription",
        job_id,
        transcript_id,
        job_timeout=300,
    )
    logger.info("Enqueued continue_after_transcription for job_id=%s", job_id)


def enqueue_retry(job_id: str, delay_seconds: int = 0) -> None:
    """Re-enqueue a failed job, optionally with a delay."""
    q = get_queue()
    if delay_seconds > 0:
        q.enqueue_in(
            timedelta(seconds=delay_seconds),
            "worker.tasks.process_recording_job",
            job_id,
            job_timeout=600,
        )
    else:
        q.enqueue("worker.tasks.process_recording_job", job_id, job_timeout=600)
    logger.info("Re-enqueued job_id=%s with delay=%ds", job_id, delay_seconds)
