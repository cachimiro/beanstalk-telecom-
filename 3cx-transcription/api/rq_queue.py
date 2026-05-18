"""Redis queue helpers using RQ."""
import logging
from datetime import timedelta

import redis
import redis.exceptions
from rq import Queue

from api.config import settings

logger = logging.getLogger(__name__)


class QueueUnavailableError(RuntimeError):
    """Raised when Redis is unreachable and a job cannot be enqueued."""


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
    """Enqueue a new recording job for processing.

    Raises QueueUnavailableError if Redis is unreachable.
    """
    try:
        q = get_queue()
        q.enqueue(
            "worker.tasks.process_recording_job",
            job_id,
            job_timeout=600,
        )
        logger.info("Enqueued process_recording_job for job_id=%s", job_id)
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        logger.error("Redis unavailable when enqueuing job_id=%s: %s", job_id, exc)
        # Reset cached connection so the next request gets a fresh attempt
        global _redis_conn, _queue
        _redis_conn = None
        _queue = None
        raise QueueUnavailableError(str(exc)) from exc


def enqueue_continuation(job_id: str, transcript_id: str) -> None:
    """Enqueue summarise+email step after AssemblyAI callback."""
    try:
        q = get_queue()
        q.enqueue(
            "worker.tasks.continue_after_transcription",
            job_id,
            transcript_id,
            job_timeout=300,
        )
        logger.info("Enqueued continue_after_transcription for job_id=%s", job_id)
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        logger.error("Redis unavailable when enqueuing continuation for job_id=%s: %s", job_id, exc)
        global _redis_conn, _queue
        _redis_conn = None
        _queue = None
        raise QueueUnavailableError(str(exc)) from exc


def enqueue_retry(job_id: str, delay_seconds: int = 0) -> None:
    """Re-enqueue a failed job, optionally with a delay."""
    try:
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
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        logger.error("Redis unavailable when re-enqueuing job_id=%s: %s", job_id, exc)
        global _redis_conn, _queue
        _redis_conn = None
        _queue = None
        raise QueueUnavailableError(str(exc)) from exc


def enqueue_email_retry(job_id: str) -> None:
    """Enqueue an email-only retry for a job parked as email_failed."""
    try:
        q = get_queue()
        q.enqueue(
            "worker.tasks.retry_email_only",
            job_id,
            job_timeout=120,
        )
        logger.info("Enqueued retry_email_only for job_id=%s", job_id)
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
        logger.error("Redis unavailable when enqueuing email retry for job_id=%s: %s", job_id, exc)
        global _redis_conn, _queue
        _redis_conn = None
        _queue = None
        raise QueueUnavailableError(str(exc)) from exc
