"""AssemblyAI transcription service."""
import logging
from typing import Optional

import httpx

from api.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.assemblyai.com/v2"

GREETING_PHRASES = [
    "thanks for calling",
    "thank you for calling",
    "how can i help",
    "how may i help",
    "good morning",
    "good afternoon",
    "good evening",
    "welcome to",
    "you've reached",
    "you have reached",
    "this is",
    "speaking",
]


def _headers() -> dict:
    return {
        "authorization": settings.effective_assemblyai_key,
        "content-type": "application/json",
    }


def submit_transcription(audio_url: str, job_id: str) -> str:
    """Submit audio to AssemblyAI. Returns transcript_id."""
    webhook_url = f"{settings.APP_URL}/webhook/assemblyai"

    payload = {
        "audio_url": audio_url,
        "speech_model": settings.ASSEMBLYAI_MODEL,
        "speaker_labels": settings.ASSEMBLYAI_SPEAKER_DIARIZATION,
        "speakers_expected": 2,
        "punctuate": True,
        "format_text": True,
        "language_detection": True,
        "webhook_url": webhook_url,
        "webhook_auth_header_name": "X-AssemblyAI-Secret",
        "webhook_auth_header_value": settings.ASSEMBLYAI_WEBHOOK_SECRET,
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{_BASE_URL}/transcript", headers=_headers(), json=payload)
        resp.raise_for_status()
        data = resp.json()

    transcript_id = data["id"]
    logger.info("Submitted to AssemblyAI: transcript_id=%s for job_id=%s", transcript_id, job_id)
    return transcript_id


def fetch_transcript(transcript_id: str) -> dict:
    """Fetch the completed transcript from AssemblyAI."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{_BASE_URL}/transcript/{transcript_id}", headers=_headers())
        resp.raise_for_status()
        return resp.json()


def upload_audio(file_path: str) -> str:
    """Upload a local audio file to AssemblyAI and return the upload URL."""
    with open(file_path, "rb") as f:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{_BASE_URL}/upload",
                headers={
                    "authorization": settings.effective_assemblyai_key,
                    "content-type": "application/octet-stream",
                },
                content=f.read(),
            )
            resp.raise_for_status()
            return resp.json()["upload_url"]


def classify_speakers(utterances: list[dict]) -> dict[str, str]:
    """
    Heuristically classify speakers as 'Likely Agent' or 'Likely Customer'.
    Returns mapping: {"A": "Likely Agent", "B": "Likely Customer"} or similar.
    Falls back to "Speaker A" / "Speaker B" if no greeting detected.
    """
    if not utterances:
        return {}

    speakers = list({u.get("speaker") for u in utterances if u.get("speaker")})
    if len(speakers) < 2:
        return {s: f"Speaker {s}" for s in speakers}

    agent_speaker = None
    for utterance in utterances[:5]:  # check first 5 utterances
        text_lower = utterance.get("text", "").lower()
        speaker = utterance.get("speaker")
        if any(phrase in text_lower for phrase in GREETING_PHRASES):
            agent_speaker = speaker
            break

    if agent_speaker:
        mapping = {}
        for s in speakers:
            mapping[s] = "Likely Agent" if s == agent_speaker else "Likely Customer"
        return mapping

    # No greeting detected — use generic labels
    return {s: f"Speaker {s}" for s in sorted(speakers)}


def format_transcript(utterances: list[dict], speaker_map: dict[str, str]) -> str:
    """Format utterances into a readable transcript string."""
    if not utterances:
        return "No transcript available."

    lines = []
    for u in utterances:
        speaker = u.get("speaker", "?")
        label = speaker_map.get(speaker, f"Speaker {speaker}")
        text = u.get("text", "").strip()
        lines.append(f"{label}:\n{text}\n")

    return "\n".join(lines)
