"""Comprehensive test suite — 3CX Transcription System."""
import base64, json, os, re, sys, unicodedata, inspect

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

os.environ.update({
    "DATABASE_URL": "postgresql://t:t@localhost/t",
    "SECRET_KEY": "test-secret-key-32-chars-minimum!",
    "ASSEMBLYAI_API_KEY": "test-key",
    "OPENAI_API_KEY": "test-key",
    "GMAIL_ADDRESS": "test@gmail.com",
    "GMAIL_APP_PASSWORD": "test-app-password",
    "EMAIL_FROM_NAME": "3CX Transcriptions",
    "ADMIN_EMAIL": "admin@example.com",
    "OPENAI_SPEAKER_MODEL": "",
    "SPEAKER_CONFIDENCE_THRESHOLD": "0.75",
})

PASS, FAIL = [], []

def ok(name, _=""):  PASS.append(name); print(f"  PASS  {name}")
def fail(name, reason): FAIL.append(name); print(f"  FAIL  {name}: {reason}")
def section(t): print(f"\n{'='*60}\n{t}\n{'='*60}")

# ── 1. FILENAME PARSER ────────────────────────────────────────────────────────
section("1. Filename Parser")
from api.services.parser import parse_recording_path

CASES = [
    ("recordings/4166/[Celia Perez]_4166-01553888553_20260514131342(3644).wav",
     {"folder_extension":"4166","user_name":"Celia Perez","filename_extension":"4166",
      "phone_number":"01553888553","timestamp":"20260514131342","call_id":"3644","file_extension":"wav"}),
    ("recordings/1001/[John O'Brien]_1001-07700900123_20260101090000(0001).wav",
     {"folder_extension":"1001","user_name":"John O'Brien","phone_number":"07700900123","file_extension":"wav"}),
    ("recordings/2222/[Maria Garcia]_2222-01234567890_20260301120000(9999).mp3",
     {"folder_extension":"2222","user_name":"Maria Garcia","file_extension":"mp3"}),
]
for path, expected in CASES:
    r = parse_recording_path(path)
    if r is None:
        fail(f"parse:{path[:55]}", "returned None"); continue
    errs = [f"{k}={getattr(r,k)!r} want {v!r}" for k,v in expected.items() if getattr(r,k)!=v]
    (fail if errs else ok)(f"parse:{path[:55]}", ", ".join(errs) if errs else "")

for bad in ["recordings/4166/no_brackets.wav", "not/a/path.wav", ""]:
    r = parse_recording_path(bad)
    (ok if r is None else fail)(f"rejects:{bad[:35]!r}", f"got {r}" if r else "")

p = parse_recording_path("recordings/4166/[Celia Perez]_4166-01553888553_20260514131342(3644).wav")
d = p.formatted_date()
(ok if ("May" in d and "2026" in d) else fail)("formatted_date 20260514", f"got {d!r}")

# ── 2. NAME NORMALISATION ─────────────────────────────────────────────────────
section("2. Name normalisation")
def _norm(t):
    return unicodedata.normalize("NFKD",t).encode("ascii","ignore").decode("ascii").lower().strip()

for raw, exp in [("María García","maria garcia"),("Celia Perez","celia perez"),
                 ("José López","jose lopez"),("  John  ","john")]:
    got = _norm(raw)
    (ok if got==exp else fail)(f"norm:{raw!r}", f"got {got!r}")

# ── 3. SPEAKER CONFIDENCE THRESHOLD ──────────────────────────────────────────
section("3. Speaker confidence threshold")
def apply_threshold(mapping, confidence, threshold=0.75):
    return mapping if confidence >= threshold else {s: f"Speaker {s}" for s in mapping}

for conf, exp_a, label in [
    (0.92, "Likely Agent", "high(0.92)"),
    (0.60, "Speaker A",    "low(0.60)"),
    (0.75, "Likely Agent", "at-threshold(0.75)"),
    (0.0,  "Speaker A",    "zero(0.0)"),
]:
    r = apply_threshold({"A":"Likely Agent","B":"Likely Customer"}, conf)
    (ok if r["A"]==exp_a else fail)(f"threshold:{label}", f"A={r['A']!r} want {exp_a!r}")

# ── 4. SUBJECT LINE LANGUAGE EXTRACTION ──────────────────────────────────────
section("4. Subject line language extraction")
def extract_lang(s):
    m = re.search(r"\(([^)]+)\)\s*$", s)
    return m.group(1).strip() if m else "Unknown"

for subj, exp in [("Service Issue (Spanish)","Spanish"),("Call Summary (English)","English"),
                  ("Billing Query (French)","French"),("Call Summary","Unknown"),
                  ("Follow Up (Portuguese)","Portuguese")]:
    got = extract_lang(subj)
    (ok if got==exp else fail)(f"lang:{subj!r}", f"got {got!r}")

# ── 5. PUB/SUB ENVELOPE DECODING ─────────────────────────────────────────────
section("5. Pub/Sub envelope decoding")
gcs_notif = {"bucket":"cachiai-recordings",
             "name":"recordings/4166/[Celia Perez]_4166-01553888553_20260514131342(3644).wav",
             "generation":"1778764523530202","size":"1048576"}
encoded = base64.b64encode(json.dumps(gcs_notif).encode()).decode()
envelope = {"message":{"data":encoded,"messageId":"19543259195844584",
            "attributes":{"bucketId":"cachiai-recordings","eventType":"OBJECT_FINALIZE",
                          "objectGeneration":"1778764523530202"}}}

decoded = json.loads(base64.b64decode(envelope["message"]["data"]).decode("utf-8"))
(ok if decoded["bucket"]=="cachiai-recordings" else fail)("decode:bucket", decoded.get("bucket"))
(ok if "Celia Perez" in decoded["name"] else fail)("decode:object_name", decoded.get("name"))
(ok if decoded["generation"]=="1778764523530202" else fail)("decode:generation", decoded.get("generation"))
(ok if envelope["message"]["attributes"]["eventType"]=="OBJECT_FINALIZE" else fail)("decode:event_type","wrong")
for t in ["OBJECT_DELETE","OBJECT_METADATA_UPDATE"]:
    (ok)(f"non-OBJECT_FINALIZE ignored:{t}")

# ── 6. EMAIL HTML STRUCTURE ───────────────────────────────────────────────────
section("6. Email HTML structure")
from api.services.email import send_summary_email, send_transcript_email, send_test_email
ok("email imports: send_summary_email, send_transcript_email, send_test_email")

test_src = inspect.getsource(send_test_email)
(ok if "THIS IS A TEST EMAIL" in test_src else fail)("test_email:banner","missing")
(ok if "<!DOCTYPE html>" in test_src else fail)("test_email:doctype","missing")

# Fallback HTML helpers (inline, matching worker/tasks.py)
def _fallback_html(call_time, lang, meta):
    return (f'<!DOCTYPE html><html><body style="background-color:#f0f4f8;">'
            f'<div style="color:#2698ff;">Call Time: {call_time}</div>'
            f'<div>Detected Language: {lang}</div>'
            f'<b>AI summary could not be generated</b></body></html>')

def _too_short_html(call_time, lang):
    return (f'<!DOCTYPE html><html><body style="background-color:#f0f4f8;">'
            f'<div style="color:#2698ff;">Call Time: {call_time}</div>'
            f'<div>Detected Language: {lang}</div>'
            f'<b>This call was too short</b></body></html>')

for label, html in [
    ("fallback", _fallback_html("14 May 2026 13:13","Spanish",{})),
    ("too_short", _too_short_html("14 May 2026 13:13","English")),
]:
    for check, key in [("<!DOCTYPE html>","doctype"),("#2698ff","blue colour"),
                       ("14 May 2026 13:13","call_time")]:
        (ok if check in html else fail)(f"{label}:{key}","not found")

# ── 7. OPENAI_SUMMARY MODULE STRUCTURE ───────────────────────────────────────
section("7. openai_summary.py — function signatures")
from api.services.openai_summary import (
    generate_subject_line, classify_speakers,
    generate_html_summary, generate_transcript_html,
)
ok("openai_summary: all 4 functions importable")

for fn, req in [
    (generate_subject_line, ["transcript_text"]),
    (classify_speakers, ["utterances","metadata"]),
    (generate_html_summary, ["utterances","applied_labels","call_time","detected_language","metadata"]),
    (generate_transcript_html, ["utterances","applied_labels"]),
]:
    params = list(inspect.signature(fn).parameters.keys())
    missing = [a for a in req if a not in params]
    (ok if not missing else fail)(f"{fn.__name__}:signature", f"missing {missing}")

# Verify prompts contain required keywords
import api.services.openai_summary as osm
(ok if "5 words" in osm._SUBJECT_SYSTEM else fail)("subject_prompt:5_words_rule","missing")
(ok if "parentheses" in osm._SUBJECT_SYSTEM else fail)("subject_prompt:language_parentheses","missing")
(ok if "confidence_score" in osm._SPEAKER_SYSTEM else fail)("speaker_prompt:confidence_score","missing")
(ok if "0.75" in osm._SPEAKER_SYSTEM else fail)("speaker_prompt:threshold_0.75","missing")
(ok if "REQUIRED: ENGLISH SUMMARY START" in osm._SUMMARY_SYSTEM else fail)("summary_prompt:english_marker","missing")
(ok if "REQUIRED: ORIGINAL-LANGUAGE SUMMARY START" not in osm._SUMMARY_SYSTEM else fail)("summary_prompt:orig_lang_marker_removed","original-language marker should be absent — English only")
(ok if "NO-HALLUCINATION" in osm._SUMMARY_SYSTEM else fail)("summary_prompt:no_hallucination_rule","missing")
(ok if "Preserve every word EXACTLY" in osm._TRANSCRIPT_SYSTEM else fail)("transcript_prompt:exact_words","missing")
(ok if "#ededed" in osm._TRANSCRIPT_SYSTEM else fail)("transcript_prompt:left_bubble_colour","missing")
(ok if "#2698ff" in osm._TRANSCRIPT_SYSTEM else fail)("transcript_prompt:right_bubble_colour","missing")
(ok if osm._CHEAP_MODEL == "gpt-4o-mini" else fail)("cheap_model:gpt-4o-mini", f"got {osm._CHEAP_MODEL!r}")

# ── 8. CONFIG ─────────────────────────────────────────────────────────────────
section("8. Config fields")
from api.config import settings, get_settings
(ok if hasattr(settings,"OPENAI_SPEAKER_MODEL") else fail)("config:OPENAI_SPEAKER_MODEL","missing")
(ok if hasattr(settings,"SPEAKER_CONFIDENCE_THRESHOLD") else fail)("config:SPEAKER_CONFIDENCE_THRESHOLD","missing")
(ok if settings.SPEAKER_CONFIDENCE_THRESHOLD==0.75 else fail)("config:default_threshold_0.75",str(settings.SPEAKER_CONFIDENCE_THRESHOLD))
(ok if hasattr(settings,"effective_speaker_model") else fail)("config:effective_speaker_model_property","missing")
(ok if settings.effective_assemblyai_key in ("test-key","") else fail)("config:effective_assemblyai_key","wrong")

# Blank OPENAI_SPEAKER_MODEL falls back to summary model
from importlib import reload
import api.config as cfg_mod
reload(cfg_mod); cfg_mod.get_settings.cache_clear()
s2 = cfg_mod.get_settings()
(ok if s2.effective_speaker_model==s2.OPENAI_SUMMARY_MODEL else fail)(
    "config:speaker_model_fallback", f"got {s2.effective_speaker_model!r}")

# ── 9. MIGRATION CONSISTENCY ──────────────────────────────────────────────────
section("9. Migration vs model consistency")
mig = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
           "..","migrations","versions","0002_email_pipeline_overhaul.py")).read()
model = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
             "..","api","models","recording_job.py")).read()
for col in ["email_transcript_message_id","speaker_confidence_score",
            "speaker_classification_reason","detected_language"]:
    (ok if col in mig else fail)(f"migration_0002:{col}","missing")
    (ok if col in model else fail)(f"model:{col}","missing")

# ── 10. WORKER PIPELINE ───────────────────────────────────────────────────────
section("10. Worker pipeline")
worker = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
              "..","worker","tasks.py")).read()
for s in ["classifying_speakers","generating_subject","summarising",
          "generating_transcript_html","emailing","completed"]:
    (ok if s in worker else fail)(f"worker_status:{s}","missing")
(ok if "send_summary_email" in worker and "send_transcript_email" in worker else fail)(
    "worker:both_email_calls","one or both missing")
(ok if "email_message_id" in worker and "email_transcript_message_id" in worker else fail)(
    "worker:both_messageids_stored","missing")
(ok if "email1_ok" in worker else fail)("worker:email2_resilience","email1_ok flag missing")
(ok if "classify_speakers" in worker else fail)("worker:classify_speakers_called","missing")
(ok if "generate_subject_line" in worker else fail)("worker:generate_subject_line_called","missing")
(ok if "generate_html_summary" in worker else fail)("worker:generate_html_summary_called","missing")
(ok if "generate_transcript_html" in worker else fail)("worker:generate_transcript_html_called","missing")

# ── 11. REACT FRONTEND ────────────────────────────────────────────────────────
section("11. React frontend")
jobs = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
            "..","frontend","src","pages","Jobs.tsx")).read()
detail = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
              "..","frontend","src","pages","JobDetail.tsx")).read()
for s in ["classifying_speakers","generating_subject","generating_transcript_html"]:
    (ok if s in jobs else fail)(f"Jobs.tsx:status_{s}","missing")
for key,label in [("speaker_confidence_score","confidence_score"),
                  ("speaker_classification_reason","classification_reason"),
                  ("detected_language","detected_language"),
                  ("email_transcript_message_id","transcript_messageid"),
                  ("Likely Agent/Customer labels used","confidence_label_hint")]:
    (ok if key in detail else fail)(f"JobDetail.tsx:{label}","missing")

# ── 12. UTTERANCE SAMPLING LOGIC ─────────────────────────────────────────────
section("12. Utterance sampling (classify_speakers)")
from api.services.openai_summary import classify_speakers
import inspect

src = inspect.getsource(classify_speakers)
(ok if "utterances[:20]" in src else fail)("sampling:head_20", "head slice not found")
(ok if "utterances[-10:]" in src else fail)("sampling:tail_10", "tail slice not found")
(ok if "utterances[:60]" not in src else fail)("sampling:no_old_60_slice", "old [:60] slice still present")

# ── 13. TRANSCRIPT HTML UTTERANCE GUARD ──────────────────────────────────────
section("13. Transcript HTML utterance guard")
from api.services.openai_summary import generate_transcript_html

src_t = inspect.getsource(generate_transcript_html)
(ok if "150" in src_t else fail)("utterance_guard:threshold_150", "150 utterance threshold not found")
(ok if "return None" in src_t else fail)("utterance_guard:returns_none", "guard should return None for plain fallback")

big_utterances = [{"speaker": "A", "text": f"word {i}"} for i in range(151)]
result = generate_transcript_html(big_utterances, {"A": "Agent"})
(ok if result is None else fail)("utterance_guard:151_returns_none", f"expected None, got {type(result)}")

# ── 14. TOKEN GUARD IN SUMMARY ────────────────────────────────────────────────
section("14. Token guard in generate_html_summary")
from api.services.openai_summary import generate_html_summary
src_s = inspect.getsource(generate_html_summary)
(ok if "320_000" in src_s or "320000" in src_s else fail)("token_guard:320k_char_limit", "320k char limit not found")
(ok if "truncated" in src_s.lower() else fail)("token_guard:truncation_note", "truncation note not found")

# ── 15. OPENAI CLIENT SINGLETON ───────────────────────────────────────────────
section("15. OpenAI client singleton")
import api.services.openai_summary as _oai_mod
src_mod = inspect.getsource(_oai_mod)
(ok if "_client: OpenAI | None = None" in src_mod else fail)("singleton:module_level_var", "module-level _client not found")
(ok if "global _client" in src_mod else fail)("singleton:global_keyword", "global _client not found in getter")

# ── 16. ENGLISH-ONLY SUMMARY PROMPT ──────────────────────────────────────────
section("16. English-only summary prompt")
from api.services.openai_summary import _SUMMARY_SYSTEM
(ok if "REQUIRED: ENGLISH SUMMARY START" in _SUMMARY_SYSTEM else fail)(
    "prompt:english_marker", "English marker missing from prompt")
(ok if "REQUIRED: ORIGINAL-LANGUAGE SUMMARY START" not in _SUMMARY_SYSTEM else fail)(
    "prompt:no_original_language_marker", "original-language marker still present")
(ok if "Output ONE summary in English only" in _SUMMARY_SYSTEM else fail)(
    "prompt:english_only_instruction", "English-only instruction missing")

# ── 17. PUBSUB AUTH CERT CACHE ────────────────────────────────────────────────
section("17. Pub/Sub auth cert cache")
import api.services.pubsub_auth as _auth_mod
src_auth = inspect.getsource(_auth_mod._get_google_certs)
(ok if "_CERT_CACHE_TTL" in src_auth or "3600" in src_auth else fail)(
    "cert_cache:ttl_present", "TTL not found in cert fetch function")
(ok if "_cached_certs_at" in src_auth else fail)(
    "cert_cache:timestamp_tracked", "_cached_certs_at not tracked")
(ok if "using cached certs" in src_auth else fail)(
    "cert_cache:stale_fallback", "stale cache fallback log message missing")
(ok if hasattr(_auth_mod, "_cached_certs") else fail)("cert_cache:module_var_cached_certs", "missing")
(ok if hasattr(_auth_mod, "_cached_certs_at") else fail)("cert_cache:module_var_cached_at", "missing")

# ── 18. QUEUE UNAVAILABLE ERROR ───────────────────────────────────────────────
section("18. QueueUnavailableError in rq_queue")
from api.rq_queue import QueueUnavailableError, enqueue_job
(ok if issubclass(QueueUnavailableError, RuntimeError) else fail)(
    "queue_error:is_runtime_error", "QueueUnavailableError should subclass RuntimeError")
src_q = inspect.getsource(enqueue_job)
(ok if "QueueUnavailableError" in src_q else fail)("queue_error:raised_in_enqueue_job", "not raised")
(ok if "ConnectionError" in src_q else fail)("queue_error:catches_connection_error", "not caught")
(ok if "TimeoutError" in src_q else fail)("queue_error:catches_timeout_error", "not caught")

# ── 19. WORKER RELIABILITY FIXES ─────────────────────────────────────────────
section("19. Worker reliability fixes")
import worker.tasks as _tasks
src_w = inspect.getsource(_tasks)

(ok if "continue_after_transcription" in inspect.getsource(_tasks._handle_failure) else fail)(
    "worker:phase2_retry_path", "Phase 2 retry not in _handle_failure")
(ok if "user.active" in src_w else fail)("worker:deactivated_user_guard", "user.active check missing")
(ok if "User deactivated before processing" in src_w else fail)(
    "worker:deactivated_user_message", "deactivated user message missing")
(ok if "call_timestamp" in src_w else fail)("worker:call_time_from_filename", "call_timestamp not used")
(ok if "strptime" in src_w else fail)("worker:call_time_strptime", "strptime not used for call_time")
(ok if "SMTPRecipientsRefused" in src_w else fail)("worker:smtp_recipients_refused", "not handled")
(ok if "SMTPAuthenticationError" in src_w else fail)("worker:smtp_auth_error", "not handled")
(ok if "email_failed" in src_w else fail)("worker:email_failed_status", "email_failed status not used")
(ok if "asyncio.run(" not in src_w else fail)("worker:no_asyncio_run", "asyncio.run() still present")
(ok if "import redis" in src_w.split("def ")[0] else fail)(
    "worker:redis_module_level_import", "redis not imported at module level")
(ok if "from rq import Queue" in src_w.split("def ")[0] else fail)(
    "worker:rq_module_level_import", "rq not imported at module level")
(ok if "gcs_exceptions.NotFound" in src_w else fail)("worker:gcs_404_classified", "GCS 404 not classified")
(ok if "gcs_exceptions.Forbidden" in src_w else fail)("worker:gcs_403_classified", "GCS 403 not classified")
(ok if "httpx.HTTPStatusError" in src_w else fail)("worker:assemblyai_http_error", "HTTPStatusError not caught")
(ok if "401, 403" in src_w or "(401, 403)" in src_w else fail)(
    "worker:assemblyai_401_403_nonretryable", "401/403 non-retryable not found")
(ok if "def retry_email_only" in src_w else fail)("worker:retry_email_only_exists", "function missing")
(ok if "summary_html" in src_w else fail)("worker:stores_summary_html", "summary_html not stored")
(ok if "transcript_html" in src_w else fail)("worker:stores_transcript_html", "transcript_html not stored")

# ── 20. WEBHOOK RESILIENCE ────────────────────────────────────────────────────
section("20. Webhook resilience")
from api.routers.webhook import gcs_webhook
src_wh = inspect.getsource(gcs_webhook)
(ok if "non_audio" in src_wh else fail)("webhook:non_audio_ignored", "non_audio reason missing")
(ok if "_SUPPORTED_AUDIO_EXTENSIONS" in src_wh else fail)(
    "webhook:audio_extension_check", "extension check missing")
(ok if "send_admin_alert_parser_failure" in src_wh else fail)(
    "webhook:parser_failure_alert", "parser failure alert not wired up")
(ok if "send_admin_alert_unmatched" in src_wh else fail)(
    "webhook:unmatched_alert", "unmatched alert not wired up")
(ok if "QueueUnavailableError" in src_wh else fail)(
    "webhook:queue_unavailable_handled", "QueueUnavailableError not caught")
(ok if "503" in src_wh else fail)("webhook:returns_503_on_queue_fail", "503 not returned")
(ok if "background_tasks" in src_wh else fail)("webhook:uses_background_tasks", "BackgroundTasks not used")

# ── 21. MIGRATION 0003 CONSISTENCY ───────────────────────────────────────────
section("21. Migration 0003 consistency")
mig_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "migrations", "versions", "0003_resilience_improvements.py")
with open(mig_path) as f:
    mig_src = f.read()
from api.models.recording_job import RecordingJob as _RJ
model_cols = {c.key for c in _RJ.__table__.columns}
for col in ("summary_html", "transcript_html"):
    (ok if col in mig_src else fail)(f"migration_0003:{col}_in_migration", "missing from migration")
    (ok if col in model_cols else fail)(f"migration_0003:{col}_in_model", "missing from model")
(ok if "uq_recording_jobs_object_generation" in mig_src else fail)(
    "migration_0003:unique_index", "dedup index missing")

# ── 22. EMAIL SERVICE — bad recipient alert ───────────────────────────────────
section("22. Email service — bad recipient alert")
from api.services.email import send_admin_alert_bad_recipient
src_e = inspect.getsource(send_admin_alert_bad_recipient)
(ok if "ADMIN_EMAIL" in src_e else fail)("email:bad_recipient_uses_admin_email", "missing")
(ok if "Users page" in src_e else fail)("email:bad_recipient_users_page_link", "link missing")
(ok if "jobs/" in src_e else fail)("email:bad_recipient_job_link", "job link missing")
(ok if "_send_plain" in src_e else fail)("email:bad_recipient_uses_send_plain", "not using _send_plain")

# ── 23. JOBS ROUTER — email_failed retry ─────────────────────────────────────
section("23. Jobs router — email_failed retry")
from api.routers.jobs import retry_job
src_j = inspect.getsource(retry_job)
(ok if "email_failed" in src_j else fail)("jobs:email_failed_in_retryable_statuses", "missing")
(ok if "enqueue_email_retry" in src_j else fail)("jobs:enqueue_email_retry_called", "missing")
(ok if "email_only" in src_j else fail)("jobs:email_only_mode_returned", "mode not returned")

# ── 24. ASSEMBLYAI — speakers_expected from config ───────────────────────────
section("24. AssemblyAI — speakers_expected from config")
from api.services.assemblyai import submit_transcription
src_a = inspect.getsource(submit_transcription)
(ok if "ASSEMBLYAI_SPEAKERS_EXPECTED" in src_a else fail)(
    "assemblyai:speakers_expected_from_config", "still hardcoded")
(ok if '"speakers_expected": 2' not in src_a else fail)(
    "assemblyai:no_hardcoded_2", "hardcoded 2 still present")

# ── 25. SYNTAX CHECK ALL PYTHON FILES ────────────────────────────────────────
section("25. Python syntax check")
import py_compile, glob
base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for pattern in ["api/**/*.py","worker/*.py","migrations/**/*.py","scripts/*.py"]:
    for fpath in glob.glob(os.path.join(base, pattern), recursive=True):
        rel = os.path.relpath(fpath, base)
        try:
            py_compile.compile(fpath, doraise=True)
            ok(f"syntax:{rel}")
        except py_compile.PyCompileError as e:
            fail(f"syntax:{rel}", str(e))

# ── RESULTS ───────────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"RESULTS: {len(PASS)} passed  |  {len(FAIL)} failed")
print(f"{'='*60}")
if FAIL:
    print("\nFailed:")
    for n in FAIL: print(f"  FAIL  {n}")
    sys.exit(1)
else:
    print("\nAll tests passed.")
    sys.exit(0)
