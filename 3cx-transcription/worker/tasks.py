"""RQ worker tasks — full recording processing pipeline.

process_recording_job()        — Phase 1: download + submit to AssemblyAI
continue_after_transcription() — Phase 2: 10-step pipeline after AssemblyAI callback
retry_email_only()             — Re-send emails for email_failed jobs without re-running pipeline
"""
import logging
import os
import smtplib
import sys
import uuid
from datetime import datetime, timedelta

import redis
import redis.exceptions
from rq import Queue

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.api_core import exceptions as gcs_exceptions
import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from api.config import settings
from api.models.recording_job import RecordingJob
from api.models.processing_log import ProcessingLog
from api.models.user import User
from api.services.gcs import download_recording, delete_temp_file
from api.services.assemblyai import fetch_transcript, upload_audio, submit_transcription
from api.services.openai_summary import (
    classify_speakers,
    generate_subject_line,
    generate_html_summary,
    generate_transcript_html,
)
from api.services.email import (
    send_summary_email,
    send_transcript_email,
    send_admin_alert_bad_recipient,
    _send_plain,
)
from api.services.parser import parse_recording_path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

_engine = None
_SessionLocal = None


def _get_session():
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine)
    return _SessionLocal()

RETRY_DELAYS = [0, 120, 600, 1800]  # immediate, 2m, 10m, 30m

NON_RETRYABLE = [
    "unsupported file", "empty file", "invalid object",
    "no matching user", "invalid api key", "401", "403", "404", "file not found",
]


def _is_retryable(error_msg: str) -> bool:
    return not any(p in error_msg.lower() for p in NON_RETRYABLE)


def _log(session: Session, job_id, level: str, message: str, metadata: dict = None):
    entry = ProcessingLog(
        recording_job_id=job_id,
        level=level,
        message=message,
        metadata_json=metadata,
        created_at=datetime.utcnow(),
    )
    session.add(entry)
    session.commit()


def _set_status(session: Session, job: RecordingJob, status: str, error: str = None):
    job.status = status
    if error:
        job.error_message = error
    if status == "processing":
        job.started_at = datetime.utcnow()
    elif status in ("completed", "failed", "email_failed"):
        job.completed_at = datetime.utcnow()
    session.commit()


def _alert_admin_failure(job: RecordingJob, reason: str) -> None:
    """Send a plain-text admin alert synchronously. Never raises."""
    if not settings.ADMIN_EMAIL:
        return
    subject = f"3CX Recording Job Failed — {job.gcs_object_name}"
    body = (
        f"A recording job has failed.\n\n"
        f"Job ID:       {job.id}\n"
        f"File:         {job.gcs_object_name}\n"
        f"Status:       {job.status}\n"
        f"Retry Count:  {job.retry_count}\n"
        f"Reason:       {reason}\n\n"
        f"Dashboard: {settings.APP_URL}/admin/jobs/{job.id}\n"
    )
    try:
        _send_plain(settings.ADMIN_EMAIL, subject, body)
    except Exception as exc:
        logger.error("Failed to send admin failure alert: %s", exc)


def _handle_failure(session: Session, job: RecordingJob, error_msg: str):
    job.retry_count = (job.retry_count or 0) + 1
    max_retries = settings.MAX_RETRIES

    # If Phase 1 already completed (transcript_id exists), retry Phase 2 only —
    # avoids paying for a second AssemblyAI transcription on OpenAI/email failures.
    if job.assemblyai_transcript_id and _is_retryable(error_msg):
        delay = RETRY_DELAYS[min(job.retry_count - 1, len(RETRY_DELAYS) - 1)]
        if job.retry_count <= max_retries:
            job.status = "queued"
            job.error_message = f"Retry {job.retry_count}/{max_retries} (phase2): {error_msg}"
            session.commit()
            _log(session, job.id, "warning", f"Retrying Phase 2 in {delay}s (attempt {job.retry_count})")
            r = redis.from_url(settings.REDIS_URL)
            q = Queue("default", connection=r)
            if delay > 0:
                q.enqueue_in(
                    timedelta(seconds=delay),
                    "worker.tasks.continue_after_transcription",
                    str(job.id),
                    job.assemblyai_transcript_id,
                )
            else:
                q.enqueue(
                    "worker.tasks.continue_after_transcription",
                    str(job.id),
                    job.assemblyai_transcript_id,
                )
            return

    if _is_retryable(error_msg) and job.retry_count <= max_retries:
        delay = RETRY_DELAYS[min(job.retry_count - 1, len(RETRY_DELAYS) - 1)]
        job.status = "queued"
        job.error_message = f"Retry {job.retry_count}/{max_retries}: {error_msg}"
        session.commit()
        _log(session, job.id, "warning", f"Retrying in {delay}s (attempt {job.retry_count})")
        r = redis.from_url(settings.REDIS_URL)
        q = Queue("default", connection=r)
        if delay > 0:
            q.enqueue_in(timedelta(seconds=delay), "worker.tasks.process_recording_job", str(job.id))
        else:
            q.enqueue("worker.tasks.process_recording_job", str(job.id))
    else:
        _set_status(session, job, "failed", error_msg)
        _log(session, job.id, "error", f"Permanently failed after {job.retry_count} attempts: {error_msg}")
        _alert_admin_failure(job, error_msg)


# ── Phase 1: Download + Submit to AssemblyAI ──────────────────────────────────

def process_recording_job(job_id: str):
    """Download audio from GCS and submit to AssemblyAI for transcription."""
    session = _get_session()
    temp_path = None

    try:
        job = session.get(RecordingJob, uuid.UUID(job_id))
        if not job:
            logger.error("Job not found: %s", job_id)
            return

        _set_status(session, job, "processing")
        _log(session, job.id, "info", "Worker picked up job")

        if job.file_extension and job.file_extension.lower() not in ("wav", "mp3"):
            _handle_failure(session, job, f"Unsupported file type: {job.file_extension}")
            return

        temp_path = os.path.join(settings.temp_dir, f"{job_id}.{job.file_extension or 'wav'}")
        try:
            file_size = download_recording(job.gcs_bucket, job.gcs_object_name, temp_path)
        except gcs_exceptions.NotFound as exc:
            # File was deleted from GCS — permanent, no point retrying
            msg = f"GCS object not found: {job.gcs_object_name}"
            _set_status(session, job, "failed", msg)
            _log(session, job.id, "error", msg)
            _alert_admin_failure(job, msg)
            return
        except gcs_exceptions.Forbidden as exc:
            # Permission denied — permanent until service account is fixed
            msg = f"GCS permission denied — check service account credentials: {exc}"
            _set_status(session, job, "failed", msg)
            _log(session, job.id, "error", msg)
            _alert_admin_failure(job, msg)
            return
        except Exception as exc:
            # Transient (network, auth token expiry, etc.) — allow retry
            _handle_failure(session, job, f"GCS download failed: {exc}")
            return

        if file_size == 0:
            _handle_failure(session, job, "Empty file (0 bytes)")
            if settings.DELETE_TEMP_FILES:
                delete_temp_file(temp_path)
            return

        _log(session, job.id, "info", f"Downloaded {file_size} bytes from GCS")
        _set_status(session, job, "transcribing")

        try:
            audio_url = upload_audio(temp_path)
            transcript_id = submit_transcription(audio_url, job_id)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in (401, 403):
                # Bad API key — permanent until config is fixed
                msg = f"AssemblyAI auth failed (HTTP {status_code}) — check ASSEMBLYAI_API_KEY"
                _set_status(session, job, "failed", msg)
                _log(session, job.id, "error", msg)
                _alert_admin_failure(job, msg)
            elif status_code == 429:
                # Rate limited — retry with longer backoff
                _handle_failure(session, job, f"AssemblyAI rate limited (429): {exc}")
            else:
                _handle_failure(session, job, f"AssemblyAI submission failed (HTTP {status_code}): {exc}")
            if settings.DELETE_TEMP_FILES:
                delete_temp_file(temp_path)
            return
        except Exception as exc:
            _handle_failure(session, job, f"AssemblyAI submission failed: {exc}")
            if settings.DELETE_TEMP_FILES:
                delete_temp_file(temp_path)
            return

        job.assemblyai_transcript_id = transcript_id
        session.commit()
        _log(session, job.id, "info", f"Submitted to AssemblyAI: transcript_id={transcript_id}")

        if settings.DELETE_TEMP_FILES:
            delete_temp_file(temp_path)

    except Exception as exc:
        logger.exception("Unexpected error in process_recording_job(%s): %s", job_id, exc)
        try:
            job = session.get(RecordingJob, uuid.UUID(job_id))
            if job:
                _handle_failure(session, job, f"Unexpected error: {exc}")
        except Exception:
            pass
        if temp_path and settings.DELETE_TEMP_FILES:
            delete_temp_file(temp_path)
    finally:
        session.close()


# ── Phase 2: 10-step pipeline after AssemblyAI callback ───────────────────────

def continue_after_transcription(job_id: str, transcript_id: str):
    """
    10-step pipeline triggered by AssemblyAI webhook callback:

    1.  Fetch full transcript from AssemblyAI
    2.  Run speaker re-classification (LLM)
    3.  Apply confidence threshold → resolve final speaker labels
    4.  Log confidence_score and reason on job record
    5.  Generate subject line (gpt-4o-mini)
    6.  Generate HTML summary (configurable model)
    7.  Handle "0" (too short) or failure
    8.  Generate conversation HTML (gpt-4o-mini)
    9.  Send Email 1: summary
    10. Send Email 2: transcript
    """
    session = _get_session()

    try:
        job = session.get(RecordingJob, uuid.UUID(job_id))
        if not job:
            logger.error("Job not found for continuation: %s", job_id)
            return

        # ── Guard: verify matched user is still active ────────────────────────
        if job.matched_user_id:
            user = session.get(User, job.matched_user_id)
            if not user or not user.active:
                msg = "User deactivated before processing completed — no email sent"
                _set_status(session, job, "unmatched", msg)
                _log(session, job.id, "warning", msg)
                _alert_admin_failure(job, msg)
                return

        # ── Step 1: Fetch transcript ──────────────────────────────────────────
        _log(session, job.id, "info", f"Fetching transcript: {transcript_id}")
        try:
            transcript_data = fetch_transcript(transcript_id)
        except Exception as exc:
            _handle_failure(session, job, f"Failed to fetch transcript: {exc}")
            return

        if transcript_data.get("status") != "completed":
            _handle_failure(session, job, f"Transcript not completed: status={transcript_data.get('status')}")
            return

        utterances = transcript_data.get("utterances") or []
        full_text = transcript_data.get("text", "") or ""

        if not utterances and not full_text.strip():
            _handle_failure(session, job, "AssemblyAI returned empty transcript")
            return

        _log(session, job.id, "info", f"Transcript fetched: {len(utterances)} utterances")

        # ── Step 2: Speaker re-classification ─────────────────────────────────
        _set_status(session, job, "classifying_speakers")
        parsed = parse_recording_path(job.gcs_object_name)

        metadata = {
            "extracted_user_name": job.extracted_name or "",
            "extension": job.folder_extension or "",
            "phone_number": job.phone_number or "",
            "call_timestamp": job.call_timestamp or "",
            "matched_user_full_name": job.extracted_name or "",
        }

        classification = classify_speakers(utterances, metadata)

        # ── Steps 3 & 4: Apply threshold + log ───────────────────────────────
        applied_labels = classification["applied_labels"]
        confidence = classification["confidence_score"]
        reason = classification["reason"]

        job.speaker_confidence_score = confidence
        job.speaker_classification_reason = reason
        session.commit()

        _log(
            session, job.id, "info",
            f"Speaker classification: confidence={confidence:.2f}",
            {"mapping": classification["speaker_mapping"], "applied": applied_labels, "reason": reason},
        )

        # Build flat transcript text for subject line (uses applied labels)
        if utterances:
            flat_lines = [
                f"{applied_labels.get(u.get('speaker', '?'), 'Speaker ?')}: {u.get('text', '').strip()}"
                for u in utterances
            ]
            flat_transcript = "\n".join(flat_lines)
        else:
            flat_transcript = full_text

        # ── Step 5: Generate subject line ─────────────────────────────────────
        _set_status(session, job, "generating_subject")
        subject, detected_language = generate_subject_line(flat_transcript)

        job.detected_language = detected_language
        session.commit()
        _log(session, job.id, "info", f"Subject: {subject!r}  language: {detected_language!r}")

        # ── Step 6: Generate HTML summary ─────────────────────────────────────
        _set_status(session, job, "summarising")

        # Use the actual call timestamp parsed from the filename (YYYYMMDDHHMMSS).
        # Fall back to current time only if the field is missing.
        call_time = datetime.utcnow().strftime("%-d %B %Y %H:%M")
        if job.call_timestamp and len(job.call_timestamp) >= 12:
            try:
                ts = datetime.strptime(job.call_timestamp[:14], "%Y%m%d%H%M%S")
                call_time = ts.strftime("%-d %B %Y %H:%M")
            except ValueError:
                pass  # malformed timestamp — keep utcnow fallback

        summary_html = generate_html_summary(
            utterances=utterances,
            applied_labels=applied_labels,
            call_time=call_time,
            detected_language=detected_language,
            metadata=metadata,
        )

        # ── Step 7: Handle "0" or failure ─────────────────────────────────────
        if summary_html is None:
            job.summary_status = "failed"
            session.commit()
            _log(session, job.id, "warning", "HTML summary failed — will send transcript only")
            summary_html = _fallback_summary_html(call_time, detected_language, metadata)
        elif summary_html == "0":
            job.summary_status = "too_short"
            session.commit()
            _log(session, job.id, "info", "Transcript too short to summarise")
            summary_html = _too_short_html(call_time, detected_language)
        else:
            job.summary_status = "completed"
            session.commit()
            _log(session, job.id, "info", "HTML summary generated successfully")

        # ── Step 8: Generate conversation HTML ────────────────────────────────
        _set_status(session, job, "generating_transcript_html")

        transcript_html = generate_transcript_html(utterances, applied_labels)
        if transcript_html is None:
            _log(session, job.id, "warning", "Transcript HTML generation failed — using plain fallback")
            transcript_html = _plain_transcript_html(utterances, applied_labels)

        _log(session, job.id, "info", "Conversation HTML generated")

        # ── Steps 9 & 10: Send both emails ────────────────────────────────────
        _set_status(session, job, "emailing")
        recipient = job.recipient_email
        if not recipient:
            _handle_failure(session, job, "No recipient email — user may have been deactivated")
            return

        # Store generated HTML so email_failed jobs can be retried without
        # re-running the pipeline.
        job.summary_html = summary_html
        job.transcript_html = transcript_html
        session.commit()

        # Email 1 — Summary
        email1_ok = False
        try:
            msg_id = send_summary_email(recipient, subject, summary_html)
            job.email_message_id = msg_id
            job.emailed_at = datetime.utcnow()
            session.commit()
            _log(session, job.id, "info", f"Email 1 (summary) sent to {recipient} — MessageID={msg_id}")
            email1_ok = True
        except smtplib.SMTPRecipientsRefused:
            # Bad email address — park job, alert admin, keep pipeline running
            msg = f"Recipient email rejected by SMTP server: {recipient}"
            _set_status(session, job, "email_failed", msg)
            _log(session, job.id, "error", msg)
            send_admin_alert_bad_recipient(job, recipient)
            return
        except smtplib.SMTPAuthenticationError as exc:
            # Gmail credentials broken — system-wide problem
            msg = f"Gmail authentication failed — check GMAIL_APP_PASSWORD: {exc}"
            _set_status(session, job, "failed", msg)
            _log(session, job.id, "error", msg)
            _alert_admin_failure(job, msg)
            return
        except Exception as exc:
            _log(session, job.id, "error", f"Email 1 (summary) failed: {exc}")
            # Transient — continue to attempt Email 2

        # Email 2 — Transcript
        try:
            msg_id2 = send_transcript_email(recipient, subject, transcript_html)
            job.email_transcript_message_id = msg_id2
            session.commit()
            _log(session, job.id, "info", f"Email 2 (transcript) sent to {recipient} — MessageID={msg_id2}")
        except smtplib.SMTPRecipientsRefused:
            msg = f"Recipient email rejected by SMTP server: {recipient}"
            if not email1_ok:
                _set_status(session, job, "email_failed", msg)
                _log(session, job.id, "error", msg)
                send_admin_alert_bad_recipient(job, recipient)
                return
            # Email 1 succeeded — log the failure but mark completed
            _log(session, job.id, "error", f"Email 2 rejected: {msg}")
        except smtplib.SMTPAuthenticationError as exc:
            msg = f"Gmail authentication failed — check GMAIL_APP_PASSWORD: {exc}"
            _set_status(session, job, "failed", msg)
            _log(session, job.id, "error", msg)
            _alert_admin_failure(job, msg)
            return
        except Exception as exc:
            _log(session, job.id, "error", f"Email 2 (transcript) failed: {exc}")
            if not email1_ok:
                # Both emails failed with transient errors — trigger retry
                _handle_failure(session, job, f"Both emails failed. Last error: {exc}")
                return

        _set_status(session, job, "completed")
        _log(session, job.id, "info", "Job completed successfully")

    except Exception as exc:
        logger.exception("Unexpected error in continue_after_transcription(%s): %s", job_id, exc)
        try:
            job = session.get(RecordingJob, uuid.UUID(job_id))
            if job:
                _handle_failure(session, job, f"Unexpected error in continuation: {exc}")
        except Exception:
            pass
    finally:
        session.close()


# ── HTML fallback helpers ─────────────────────────────────────────────────────

def _fallback_summary_html(call_time: str, detected_language: str, metadata: dict) -> str:
    """Minimal HTML when summary generation fails."""
    return f"""<!DOCTYPE html>
<html>
<body style="background-color:#f0f4f8;margin:0;padding:0;">
  <div style="background-color:#ffffff;max-width:680px;margin:36px auto;border-radius:12px;
              padding:28px 20px;font-family:Arial,Helvetica,sans-serif;color:#1d1d1f;">
    <div style="color:#2698ff;font-weight:bold;font-size:16px;">Call Time: {call_time}</div>
    <div style="margin-top:6px;color:#555;">Detected Language: {detected_language}</div>
    <br>
    <div style="background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:12px 16px;">
      <b>AI summary could not be generated for this call.</b><br>
      Please refer to the conversation transcript email for the full call content.
    </div>
  </div>
</body>
</html>"""


def _too_short_html(call_time: str, detected_language: str) -> str:
    """HTML notice when transcript is too short to summarise."""
    return f"""<!DOCTYPE html>
<html>
<body style="background-color:#f0f4f8;margin:0;padding:0;">
  <div style="background-color:#ffffff;max-width:680px;margin:36px auto;border-radius:12px;
              padding:28px 20px;font-family:Arial,Helvetica,sans-serif;color:#1d1d1f;">
    <div style="color:#2698ff;font-weight:bold;font-size:16px;">Call Time: {call_time}</div>
    <div style="margin-top:6px;color:#555;">Detected Language: {detected_language}</div>
    <br>
    <div style="background:#e8f4fd;border:1px solid #2698ff;border-radius:8px;padding:12px 16px;">
      <b>This call was too short to generate a summary.</b><br>
      Please refer to the conversation transcript email for the full call content.
    </div>
  </div>
</body>
</html>"""


def retry_email_only(job_id: str):
    """Re-send already-generated emails for a job parked as email_failed.

    Does not re-download from GCS or re-transcribe — uses stored HTML.
    """
    session = _get_session()
    try:
        job = session.get(RecordingJob, uuid.UUID(job_id))
        if not job:
            logger.error("retry_email_only: job not found: %s", job_id)
            return

        if not job.summary_html or not job.transcript_html:
            # HTML was not stored (old job) — fall back to full pipeline retry
            logger.warning("retry_email_only: no stored HTML for job %s — falling back to full retry", job_id)
            _handle_failure(session, job, "Email retry: no stored HTML, re-queuing full pipeline")
            return

        recipient = job.recipient_email
        if not recipient:
            _set_status(session, job, "failed", "No recipient email on retry")
            _alert_admin_failure(job, "No recipient email on email retry")
            return

        _set_status(session, job, "emailing")
        subject = f"Voicemail — {job.phone_number or job.folder_extension or 'Unknown'}"

        try:
            msg_id = send_summary_email(recipient, job.detected_language or subject, job.summary_html)
            job.email_message_id = msg_id
            job.emailed_at = datetime.utcnow()
            session.commit()
            _log(session, job.id, "info", f"Email retry: summary sent to {recipient}")
        except smtplib.SMTPRecipientsRefused:
            msg = f"Recipient still rejected on retry: {recipient}"
            _set_status(session, job, "email_failed", msg)
            _log(session, job.id, "error", msg)
            send_admin_alert_bad_recipient(job, recipient)
            return
        except Exception as exc:
            _handle_failure(session, job, f"Email retry failed: {exc}")
            return

        try:
            msg_id2 = send_transcript_email(recipient, job.detected_language or subject, job.transcript_html)
            job.email_transcript_message_id = msg_id2
            session.commit()
            _log(session, job.id, "info", f"Email retry: transcript sent to {recipient}")
        except Exception as exc:
            _log(session, job.id, "error", f"Email retry: transcript send failed: {exc}")

        _set_status(session, job, "completed")
        _log(session, job.id, "info", "Job completed via email retry")

    except Exception as exc:
        logger.exception("Unexpected error in retry_email_only(%s): %s", job_id, exc)
        try:
            job = session.get(RecordingJob, uuid.UUID(job_id))
            if job:
                _handle_failure(session, job, f"Unexpected error in email retry: {exc}")
        except Exception:
            pass
    finally:
        session.close()


def _plain_transcript_html(utterances: list, applied_labels: dict) -> str:
    """Plain HTML fallback when gpt-4o-mini transcript formatting fails."""
    rows = ""
    for u in utterances:
        label = applied_labels.get(u.get("speaker", "?"), f"Speaker {u.get('speaker', '?')}")
        text = u.get("text", "").strip()
        rows += f"<tr><td style='padding:4px 8px;'><b>{label}:</b> {text}</td></tr>\n"
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,Helvetica,sans-serif;padding:20px;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    {rows}
  </table>
</body>
</html>"""
