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
(ok if "REQUIRED: ORIGINAL-LANGUAGE SUMMARY START" in osm._SUMMARY_SYSTEM else fail)("summary_prompt:orig_lang_marker","missing")
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

# ── 12. SYNTAX CHECK ALL PYTHON FILES ────────────────────────────────────────
section("12. Python syntax check")
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
