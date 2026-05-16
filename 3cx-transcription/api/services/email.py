"""Gmail SMTP email service.

Two main emails per call:
  send_summary_email()    — Email 1: HTML call summary
  send_transcript_email() — Email 2: HTML conversation bubbles

Plus admin alert helpers.

Note: _send() and _send_plain() are synchronous (smtplib).
      The async admin alert helpers run them in a thread executor
      so they don't block the FastAPI event loop.
"""
import asyncio
import logging
import smtplib
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from api.config import settings
from api.services.parser import ParsedRecording

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _send(to: str, subject: str, html_body: str, text_fallback: str = "") -> str:
    """Send via Gmail SMTP. Returns a synthetic message ID for logging."""
    from_header = f"{settings.EMAIL_FROM_NAME} <{settings.GMAIL_ADDRESS}>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to
    if settings.REPLY_TO_EMAIL:
        msg["Reply-To"] = settings.REPLY_TO_EMAIL

    if text_fallback:
        msg.attach(MIMEText(text_fallback, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(settings.GMAIL_ADDRESS, settings.GMAIL_APP_PASSWORD)
            smtp.sendmail(settings.GMAIL_ADDRESS, to, msg.as_string())

        # Synthetic ID — Gmail SMTP doesn't return a message ID
        message_id = f"gmail-{uuid.uuid4().hex[:12]}"
        logger.info("Email sent to %s — MessageID=%s subject=%r", to, message_id, subject)
        return message_id
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("Gmail SMTP auth failed: %s", exc)
        raise
    except smtplib.SMTPException as exc:
        logger.error("Gmail SMTP error sending to %s: %s", to, exc)
        raise
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to, exc)
        raise


def _send_plain(to: str, subject: str, text_body: str) -> str:
    """Send plain-text email (used for admin alerts)."""
    from_header = f"{settings.EMAIL_FROM_NAME} <{settings.GMAIL_ADDRESS}>"

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to
    if settings.REPLY_TO_EMAIL:
        msg["Reply-To"] = settings.REPLY_TO_EMAIL
    msg.attach(MIMEText(text_body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(settings.GMAIL_ADDRESS, settings.GMAIL_APP_PASSWORD)
            smtp.sendmail(settings.GMAIL_ADDRESS, to, msg.as_string())
        return f"gmail-{uuid.uuid4().hex[:12]}"
    except Exception as exc:
        logger.error("Failed to send plain email to %s: %s", to, exc)
        raise


# ── Email 1: HTML Summary ─────────────────────────────────────────────────────

def send_summary_email(recipient_email: str, subject: str, html_body: str) -> str:
    """
    Send Email 1 — the HTML call summary.

    Args:
        recipient_email: matched user's email
        subject: output of generate_subject_line()
        html_body: output of generate_html_summary() — full HTML document

    Returns:
        Synthetic message ID string for logging
    """
    text_fallback = (
        "Your call summary is attached as HTML. "
        "Please view this email in an HTML-capable email client."
    )
    return _send(recipient_email, subject, html_body, text_fallback)


# ── Email 2: HTML Conversation Transcript ─────────────────────────────────────

def send_transcript_email(recipient_email: str, subject: str, html_body: str) -> str:
    """
    Send Email 2 — the HTML conversation transcript (chat bubbles).

    Args:
        recipient_email: matched user's email
        subject: same subject as Email 1
        html_body: output of generate_transcript_html()

    Returns:
        Synthetic message ID string for logging
    """
    text_fallback = (
        "Your call transcript is attached as HTML. "
        "Please view this email in an HTML-capable email client."
    )
    return _send(recipient_email, subject, html_body, text_fallback)


# ── Admin Alerts ──────────────────────────────────────────────────────────────

async def send_admin_alert_unmatched(object_name: str, parsed: Optional[ParsedRecording]) -> None:
    """Alert admin when a recording cannot be matched to a user."""
    if not settings.ADMIN_EMAIL:
        logger.warning("ADMIN_EMAIL not configured — cannot send unmatched alert")
        return

    name = parsed.user_name if parsed else "Unknown"
    ext = parsed.folder_extension if parsed else "Unknown"

    subject = "Unmatched 3CX Recording — Action Required"
    body = (
        f"A new recording could not be matched to a user.\n\n"
        f"File:\n  {object_name}\n\n"
        f"Extracted Name:\n  {name}\n\n"
        f"Extracted Extension:\n  {ext}\n\n"
        f"Required Action:\n"
        f"  Add this user in the admin dashboard or correct their extension.\n\n"
        f"Dashboard: {settings.APP_URL}/admin/users\n"
    )
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send_plain, settings.ADMIN_EMAIL, subject, body)
    except Exception as exc:
        logger.error("Failed to send unmatched alert to admin: %s", exc)


async def send_admin_alert_parser_failure(object_name: str) -> None:
    """Alert admin when filename parsing fails."""
    if not settings.ADMIN_EMAIL:
        return
    subject = "3CX Recording Parser Failure"
    body = (
        f"A recording filename could not be parsed.\n\n"
        f"File:\n  {object_name}\n\n"
        f"The system could not extract extension, name, or phone number from this path.\n"
        f"Please check the recording filename format.\n\n"
        f"Dashboard: {settings.APP_URL}/admin/jobs\n"
    )
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send_plain, settings.ADMIN_EMAIL, subject, body)
    except Exception as exc:
        logger.error("Failed to send parser failure alert: %s", exc)


async def send_admin_alert_job_failed(job) -> None:
    """Alert admin when a job fails after all retries."""
    if not settings.ADMIN_EMAIL:
        return
    subject = f"3CX Recording Job Failed — {job.gcs_object_name}"
    body = (
        f"A recording job has failed after all retry attempts.\n\n"
        f"Job ID:       {job.id}\n"
        f"File:         {job.gcs_object_name}\n"
        f"Status:       {job.status}\n"
        f"Retry Count:  {job.retry_count}\n"
        f"Error:        {job.error_message or 'No error message recorded'}\n\n"
        f"Dashboard: {settings.APP_URL}/admin/jobs\n"
    )
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send_plain, settings.ADMIN_EMAIL, subject, body)
    except Exception as exc:
        logger.error("Failed to send job failed alert: %s", exc)


def send_test_email(recipient_email: str, recipient_name: str) -> str:
    """Send a test HTML email to verify delivery for a user."""
    subject = "Test Email — 3CX Transcription System"
    html_body = f"""<!DOCTYPE html>
<html>
<body style="background-color:#f0f4f8;margin:0;padding:0;">
  <div style="background-color:#ffffff;max-width:680px;margin:36px auto;border-radius:12px;
              box-shadow:0 2px 12px rgba(0,0,0,0.08);padding:28px 20px;
              font-family:Arial,Helvetica,sans-serif;color:#1d1d1f;line-height:1.45;">
    <div style="background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:12px 16px;margin-bottom:20px;">
      <b style="color:#856404;">&#9888; THIS IS A TEST EMAIL</b>
    </div>
    <p>This is a test email sent from the <b>3CX Transcription System</b> admin dashboard.</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0;">
      <tr><td style="padding:6px 0;color:#555;width:120px;">Recipient:</td><td style="padding:6px 0;"><b>{recipient_name}</b></td></tr>
      <tr><td style="padding:6px 0;color:#555;">Email:</td><td style="padding:6px 0;">{recipient_email}</td></tr>
    </table>
    <p style="color:#555;font-size:14px;">
      If you received this, email delivery is working correctly for this user.<br>
      Real call summaries and transcripts will appear in this format after each call is processed.
    </p>
    <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">
    <p style="color:#aaa;font-size:12px;">3CX Transcription System &mdash; Admin Dashboard</p>
  </div>
</body>
</html>"""
    return _send(recipient_email, subject, html_body, "This is a test email from the 3CX Transcription System.")
