"""OpenAI AI pipeline — four calls per recording.

Call 1: generate_subject_line()     — gpt-4o-mini, cheapest
Call 2: classify_speakers()         — configurable (OPENAI_SPEAKER_MODEL)
Call 3: generate_html_summary()     — configurable (OPENAI_SUMMARY_MODEL) with fallback chain
Call 4: generate_transcript_html()  — gpt-4o-mini, formatting only
"""
import json
import logging
import re
from typing import Optional

from openai import OpenAI, APIError, AuthenticationError

from api.config import settings

logger = logging.getLogger(__name__)

_PRIMARY = "#2698ff"
_PAGE_BG = "#f0f4f8"
_SECTION_BG = "#f7faff"
_CHEAP_MODEL = "gpt-4o-mini"
_NON_RETRYABLE_ERRORS = (AuthenticationError,)


def _openai_client() -> OpenAI:
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def _is_model_error(exc: APIError) -> bool:
    msg = str(exc).lower()
    code = getattr(exc, "code", "") or ""
    return any(p in msg for p in ["model_not_found", "does not exist", "invalid model", "permission"]) \
        or code in ("model_not_found", "invalid_request_error")


def _call_with_fallback(
    client: OpenAI,
    models: list,
    messages: list,
    response_format: dict = None,
    temperature: float = 0.2,
) -> str:
    """Try each model in order. Returns the raw content string."""
    for model in models:
        try:
            kwargs = dict(model=model, messages=messages, temperature=temperature)
            if response_format:
                kwargs["response_format"] = response_format
            resp = client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            logger.info("OpenAI call succeeded with model=%s", model)
            return content
        except _NON_RETRYABLE_ERRORS as exc:
            logger.error("Non-retryable OpenAI error model=%s: %s", model, exc)
            raise
        except APIError as exc:
            if _is_model_error(exc):
                logger.warning("Model %s unavailable, trying next: %s", model, exc)
                continue
            logger.warning("Transient OpenAI error model=%s, retrying once: %s", model, exc)
            try:
                kwargs2 = dict(model=model, messages=messages, temperature=temperature)
                if response_format:
                    kwargs2["response_format"] = response_format
                resp = client.chat.completions.create(**kwargs2)
                return resp.choices[0].message.content or ""
            except Exception:
                continue
        except Exception as exc:
            logger.error("Unexpected error model=%s: %s", model, exc)
            continue
    raise RuntimeError(f"All models exhausted: {models}")


# ── Call 1: Subject Line ──────────────────────────────────────────────────────

_SUBJECT_SYSTEM = """You are an assistant that reads a conversation transcript and generates a short, relevant subject line.

The subject line must:
\u2022 Be no more than 5 words
\u2022 Include the people in the conversation if known; if not, say "Call Summary"
\u2022 Clearly relate to the conversation's key topic
\u2022 Remain in English
\u2022 At the end of the subject line, add the detected language of the transcript in parentheses. Example: (Spanish)

Identify the language of the transcript automatically.

Output only the final subject line. No explanations."""


def generate_subject_line(transcript_text: str) -> tuple:
    """
    Generate a short email subject line using gpt-4o-mini.
    Returns: (subject_line: str, detected_language: str)
    """
    client = _openai_client()
    try:
        subject = _call_with_fallback(
            client,
            [_CHEAP_MODEL],
            [
                {"role": "system", "content": _SUBJECT_SYSTEM},
                {"role": "user", "content": f"Transcript:\n\n{transcript_text}"},
            ],
            temperature=0.1,
        ).strip()
    except Exception as exc:
        logger.error("Subject line generation failed: %s", exc)
        return "Call Summary", "Unknown"

    detected_language = "Unknown"
    match = re.search(r"\(([^)]+)\)\s*$", subject)
    if match:
        detected_language = match.group(1).strip()

    logger.info("Subject: %r  language: %r", subject, detected_language)
    return subject, detected_language


# ── Call 2: Speaker Re-Classification ─────────────────────────────────────────

_SPEAKER_SYSTEM = """You are a speaker classification assistant for phone call transcripts.

You will receive:
1. A list of utterances with speaker labels (Speaker A, Speaker B, etc.)
2. Recording metadata including the internal user's name, extension, and phone number.

Your task is to classify each speaker as one of:
- "Likely Agent" \u2014 the internal employee who answered or made the call
- "Likely Customer" \u2014 the external caller
- "Unknown" \u2014 cannot be determined

Use these signals to classify:
- Opening greeting phrases ("Thanks for calling", "How can I help", etc.)
- The matched internal user's name if mentioned
- Conversation structure (who is asking vs answering)
- Wording patterns (professional vs customer language)
- The extension metadata (the matched user is likely the agent)

Return ONLY valid JSON in this exact format:
{
  "speaker_mapping": {
    "A": "Likely Agent",
    "B": "Likely Customer"
  },
  "confidence_score": 0.92,
  "reason": "Speaker A opened with a greeting phrase typical of an agent. Speaker B asked about a service issue."
}

Rules:
- confidence_score must be a float between 0.0 and 1.0
- If confidence_score is below 0.75, set both values to "Speaker A" and "Speaker B" respectively
- Never assert certainty \u2014 always use "Likely Agent" / "Likely Customer", never "Agent" / "Customer"
- If only one speaker is detected, return that speaker as "Unknown"
"""


def classify_speakers(utterances: list, metadata: dict, confidence_threshold: float = None) -> dict:
    """
    Re-classify AssemblyAI speaker labels using LLM context.

    Returns dict with keys:
      speaker_mapping, confidence_score, reason, applied_labels
    applied_labels uses neutral "Speaker X" if confidence below threshold.
    """
    threshold = confidence_threshold if confidence_threshold is not None else settings.SPEAKER_CONFIDENCE_THRESHOLD
    client = _openai_client()
    model = settings.effective_speaker_model

    sample = utterances[:60]
    payload = {
        "utterances": [
            {"speaker": u.get("speaker"), "text": u.get("text", ""), "start": u.get("start"), "end": u.get("end")}
            for u in sample
        ],
        "metadata": metadata,
    }

    try:
        raw = _call_with_fallback(
            client,
            [model] + [m for m in settings.openai_fallback_list if m != model],
            [
                {"role": "system", "content": _SPEAKER_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.1,
        )
        result = json.loads(raw)
    except Exception as exc:
        logger.error("Speaker classification failed: %s \u2014 using neutral labels", exc)
        speakers = list({u.get("speaker") for u in utterances if u.get("speaker")})
        neutral = {s: f"Speaker {s}" for s in speakers}
        return {"speaker_mapping": neutral, "confidence_score": 0.0, "reason": str(exc), "applied_labels": neutral}

    mapping = result.get("speaker_mapping", {})
    confidence = float(result.get("confidence_score", 0.0))
    reason = result.get("reason", "")

    if confidence >= threshold:
        applied = mapping
    else:
        logger.info("Confidence %.2f below threshold %.2f \u2014 using neutral labels", confidence, threshold)
        applied = {s: f"Speaker {s}" for s in mapping}

    return {"speaker_mapping": mapping, "confidence_score": confidence, "reason": reason, "applied_labels": applied}


# ── Call 3: HTML Summary ──────────────────────────────────────────────────────

_SUMMARY_SYSTEM = """You are an expert call-analysis assistant for telecommunications companies. Your ONLY task is to convert a phone call transcript into a complete, accurate meeting summary in strict HTML format.

\U0001f6a8 NON-NEGOTIABLE RULES

You MUST follow every rule below EXACTLY:

1. Output TWO summaries:
   - English Summary
   - Original-Language Summary
   Do NOT skip either.

2. STRICT NO-HALLUCINATION POLICY
   You MUST NOT invent, guess, infer, or assume ANY of the following:
   - Names, Roles, Company names, Decisions, Tasks, Background context
   - Information not explicitly stated in the transcript
   If information is missing, use:
   - [Name Not Provided], [Role Not Provided], [Company Not Provided], [Not Mentioned]

3. LANGUAGE
   Auto-detect the language (e.g., "Spanish (es)").
   English summary must ALWAYS be in English.
   Original-language summary MUST be written in the exact detected language.

4. STRUCTURE (MUST ALWAYS match EXACT format)
   Each summary MUST contain these SIX sections in this order:
   Title, Context, Agenda (UL list), Key Figures (UL list),
   Detailed Topics (OL list with nested UL), Follow-up Tasks (UL list)
   If a section has no data, output: None stated.

5. STRICT KEY FIGURES RULE
   Only include people explicitly mentioned.
   If role not stated: [Role Not Provided]
   If company not stated: [Company Not Provided]
   NEVER create additional people or information.

6. STYLING (HTML only)
   Full HTML document, inline CSS only, no Markdown.
   Use <b> for headers, <br><br> for spacing.
   One centered container, max-width 680px.
   Must contain these HTML comments exactly:
   <!-- REQUIRED: ENGLISH SUMMARY START -->
   <!-- REQUIRED: ORIGINAL-LANGUAGE SUMMARY START -->
   Use these exact colour values in all inline styles:
   PRIMARY = #2698ff
   PAGE_BG = #f0f4f8
   SECTION_BG = #f7faff

7. NOT ENOUGH INFORMATION
   If the transcript contains too little to summarize, output ONLY the number: 0
   Nothing else."""


def generate_html_summary(
    utterances: list,
    applied_labels: dict,
    call_time: str,
    detected_language: str,
    metadata: dict,
) -> Optional[str]:
    """
    Generate the full HTML call summary (English + original language).
    Returns HTML string, "0" if too short, or None on failure.
    """
    client = _openai_client()
    models = [settings.OPENAI_SUMMARY_MODEL] + settings.openai_fallback_list

    lines = []
    for u in utterances:
        label = applied_labels.get(u.get("speaker", "?"), f"Speaker {u.get('speaker', '?')}")
        lines.append(f"{label}: {u.get('text', '').strip()}")
    transcript_block = "\n".join(lines)

    user_msg = (
        f"Call Time: {call_time}\n"
        f"Detected Language: {detected_language}\n"
        f"Extension: {metadata.get('extension', '[Not Provided]')}\n"
        f"Phone Number: {metadata.get('phone_number', '[Not Provided]')}\n"
        f"Matched User: {metadata.get('matched_user_full_name', '[Not Provided]')}\n\n"
        f"Transcript:\n{transcript_block}"
    )

    try:
        html = _call_with_fallback(
            client, models,
            [{"role": "system", "content": _SUMMARY_SYSTEM}, {"role": "user", "content": user_msg}],
            temperature=0.2,
        ).strip()
    except Exception as exc:
        logger.error("HTML summary generation failed: %s", exc)
        return None

    if html == "0":
        logger.info("Summary model returned 0 \u2014 transcript too short")
        return "0"

    if "REQUIRED: ENGLISH SUMMARY START" not in html:
        logger.warning("Summary HTML missing required markers \u2014 retrying once")
        try:
            html = _call_with_fallback(
                client, models,
                [{"role": "system", "content": _SUMMARY_SYSTEM}, {"role": "user", "content": user_msg}],
                temperature=0.1,
            ).strip()
        except Exception as exc:
            logger.error("Summary retry failed: %s", exc)
            return None

    return html


# ── Call 4: Conversation Transcript HTML ──────────────────────────────────────

_TRANSCRIPT_SYSTEM = """Output the entire conversation as a single HTML email body (no markdown, no explanations, no extra text).

Use a table layout to alternate chat bubbles for each speaker:
- Left-aligned light gray bubbles (#ededed) for the first speaker (Likely Agent or Speaker A).
- Right-aligned blue bubbles (#2698ff) for the second speaker (Likely Customer or Speaker B).

Use inline styles only (no CSS classes or external stylesheets).
Each bubble must show the speaker's name in bold, followed by their message.
Use border-radius to give bubbles a modern, rounded look.
Each turn is its own row in the table.
Max width for each bubble: 70% of table width.
Always use the same name for each speaker throughout, even if only mentioned once.
If a name isn't provided, use the speaker label exactly as given (e.g. "Likely Agent", "Speaker A").

Do not add explanations, summaries, timestamps, greetings, or any text outside the conversation.
The output should be complete, valid HTML for use in emails.

CRITICAL:
- Preserve every word EXACTLY as in the transcript.
- Do NOT rephrase, correct grammar, or clean text.
- Do NOT merge or split sentences."""


def generate_transcript_html(utterances: list, applied_labels: dict) -> Optional[str]:
    """
    Format utterances as HTML chat-bubble email body using gpt-4o-mini.
    Returns HTML string or None on failure.
    """
    if not utterances:
        return "<p>No transcript available.</p>"

    client = _openai_client()

    lines = []
    for u in utterances:
        label = applied_labels.get(u.get("speaker", "?"), f"Speaker {u.get('speaker', '?')}")
        lines.append(f"{label}: {u.get('text', '').strip()}")
    transcript_block = "\n".join(lines)

    try:
        html = _call_with_fallback(
            client,
            [_CHEAP_MODEL],
            [
                {"role": "system", "content": _TRANSCRIPT_SYSTEM},
                {"role": "user", "content": f"Conversation:\n\n{transcript_block}"},
            ],
            temperature=0.0,
        ).strip()
    except Exception as exc:
        logger.error("Transcript HTML generation failed: %s", exc)
        return None

    return html
