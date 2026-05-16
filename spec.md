# 3CX Google Cloud Recording Transcription System — Specification

## 1. Problem Statement

The current Make.com workflow manually routes 3CX call recordings to users for transcription and summarisation. It requires workflow changes every time a new user is added, is fragile under load, and cannot handle retries, queuing, or structured error recovery.

This system replaces Make.com with a self-hosted backend that:
- Automatically receives new 3CX recordings from Google Cloud Storage via Pub/Sub
- Parses the recording filename to extract user identity
- Matches the recording to a registered user by 3CX extension
- Transcribes the audio via AssemblyAI (with speaker diarisation)
- Generates a structured AI summary via OpenAI
- Emails the transcript and summary to the matched user via Postmark
- Provides an admin dashboard for user management and job monitoring

Adding a new user requires only entering their name, email, and 3CX extension in the dashboard. No code or workflow changes are needed.

---

## 2. Architecture Overview

### Hosting
- **Platform:** DigitalOcean Droplet
- **OS:** Ubuntu 24.04 LTS
- **Spec:** 2 vCPU, 4 GB RAM, 80 GB SSD
- **Access:** Public IP address only (no domain). HTTP on port 80 (no SSL for MVP).
- **Orchestration:** Docker Compose

### Services (Docker Compose)
| Service | Purpose |
|---|---|
| `api` | FastAPI backend — webhook receiver, REST API, admin API |
| `worker` | Background worker — transcription, summarisation, email |
| `frontend` | React SPA — admin dashboard (served via nginx) |
| `postgres` | PostgreSQL 16 — primary data store |
| `redis` | Redis 7 — job queue (RQ or Celery) |
| `nginx` | Reverse proxy — routes `/api/*` to FastAPI, `/` to React |

### Data Flow
```
3CX → GCS bucket → Pub/Sub OBJECT_FINALIZE event
  → POST /webhook/gcs (FastAPI)
    → validate OIDC JWT (primary) or shared secret (fallback)
    → decode Pub/Sub envelope (base64 message.data)
    → parse filename
    → deduplicate (gcs_bucket + gcs_object_name + gcs_generation)
    → create recording_job record
    → enqueue job to Redis
    → return 200

Worker picks up job:
  → download .wav from GCS (temp file)
  → upload to AssemblyAI
  → AssemblyAI POSTs callback to /webhook/assemblyai when done
  → worker fetches full transcript
  → send transcript to OpenAI for structured summary
  → send email via Postmark
  → update job status
  → delete temp file
```

---

## 3. Confirmed Technical Decisions

| Decision | Choice | Notes |
|---|---|---|
| Audio format | `.wav` | Confirmed from real GCS event — 3CX saves WAV files |
| Pub/Sub payload | JSON_API_V1 push envelope | `message.data` is base64-encoded GCS notification JSON |
| AssemblyAI completion | Webhook callback | AssemblyAI POSTs to `/webhook/assemblyai` when done |
| AI summary provider | OpenAI | Configurable model via env var |
| Default summary model | `gpt-5.2` | Falls back to `gpt-5`, `gpt-5-mini`, `gpt-4o` in order |
| Admin dashboard | React SPA | Served by nginx, communicates with FastAPI via REST |
| Pub/Sub auth | OIDC JWT (primary) + shared secret (fallback) | |
| API key storage | Environment variables only | Never stored in database |
| Email provider | Postmark | Transactional email for summaries and admin alerts |
| Domain/SSL | IP address only | No SSL for MVP; add domain/SSL post-deployment |

---

## 4. Detailed Requirements

### 4.1 GCS Pub/Sub Webhook (`POST /webhook/gcs`)

**Authentication (in order):**
1. Check `Authorization: Bearer <token>` header — validate as Google-signed OIDC JWT
   - Verify signature against Google's public keys (`https://www.googleapis.com/oauth2/v3/certs`)
   - Verify `iss` = `https://accounts.google.com`
   - Verify `aud` = the webhook URL
   - Verify `email` matches the configured Pub/Sub service account
2. If OIDC fails or is absent, check `X-Webhook-Secret` header against `WEBHOOK_SECRET` env var
3. If both fail, return `401`

**Payload handling:**
- Pub/Sub push envelope format:
  ```json
  {
    "message": {
      "data": "<base64-encoded GCS notification JSON>",
      "messageId": "19543259195844584",
      "publishTime": "2026-05-14T14:15:00Z",
      "attributes": {
        "bucketId": "cachiai-recordings",
        "eventType": "OBJECT_FINALIZE",
        "objectId": "recordings/4166/[Celia Perez]_4166-...(3644).wav",
        "objectGeneration": "1778764523530202",
        "notificationConfig": "projects/_/buckets/cachiai-recordings/notificationConfigs/7",
        "payloadFormat": "JSON_API_V1"
      }
    },
    "subscription": "projects/quickstart-1600776214159/subscriptions/..."
  }
  ```
- Decode `message.data` from base64 to get the GCS notification JSON
- Extract: `bucket`, `name` (object path), `generation`, `size`, `timeCreated`
- Only process `eventType = OBJECT_FINALIZE`
- Ignore all other event types (return `200` immediately)

**Processing:**
1. Parse the object path using the filename parser (§4.2)
2. If parser fails: create job with status `failed_parser`, email admin, return `200`
3. Check for duplicate: `(gcs_bucket, gcs_object_name, gcs_generation)` — if exists, return `200`
4. Create `recording_jobs` record with status `received`
5. Enqueue job ID to Redis queue
6. Update status to `queued`
7. Return `200`

**Never perform transcription inside the webhook handler.**

---

### 4.2 Filename Parser

**Input format:**
```
recordings/{folder_extension}/[{user_name}]_{filename_extension}-{phone_number}_{timestamp}({call_id}).{file_extension}
```

**Real example:**
```
recordings/4166/[Celia Perez]_4166-01553888553_20260514131342(3644).wav
```

**Regex pattern:**
```
recordings/(?P<folder_extension>\d+)/\[(?P<user_name>[^\]]+)\]_(?P<filename_extension>\d+)-(?P<phone_number>\d+)_(?P<timestamp>\d+)\((?P<call_id>[^)]+)\)\.(?P<file_extension>\w+)
```

**Expected output:**
```json
{
  "folder_extension": "4166",
  "user_name": "Celia Perez",
  "filename_extension": "4166",
  "phone_number": "01553888553",
  "timestamp": "20260514131342",
  "call_id": "3644",
  "file_extension": "wav"
}
```

**On parse failure:**
- Set job status to `failed_parser`
- Log error with raw object path
- Email admin with the raw path and error details
- Do not attempt transcription

---

### 4.3 User Matching (Routing Logic)

Match priority (in order):

1. **Folder extension** → find active user where `extension = folder_extension`
2. **Filename extension** → find active user where `extension = filename_extension`
3. **Bracketed name** → find active user where `LOWER(full_name) = LOWER(user_name)` (normalise accents/spaces)
4. **No match** → set status `unmatched`, email admin alert

On match:
- Set `matched_user_id` and `recipient_email` on the job
- Proceed to transcription

On unmatched:
- Email admin with: file path, extracted name, extracted extension, instruction to add user in dashboard
- Do not attempt transcription

---

### 4.4 Worker — Job Processing

**Queue:** Redis (using RQ or Celery — RQ preferred for simplicity)

**Worker steps:**
1. Pick up job from queue
2. Set status → `processing`
3. Download `.wav` from GCS to `/tmp/{job_id}.wav` using GCS service account credentials
4. Set status → `transcribing`
5. Submit audio to AssemblyAI (see §4.5)
6. Store `assemblyai_transcript_id` on job record
7. Wait for AssemblyAI webhook callback (see §4.6)
8. On callback received: fetch full transcript from AssemblyAI
9. Set status → `summarising`
10. Send transcript to OpenAI for structured summary (see §4.7)
11. Set status → `emailing`
12. Send email via Postmark (see §4.8)
13. Set status → `completed`, set `completed_at`, `emailed_at`
14. Delete `/tmp/{job_id}.wav`

**Retry policy (retryable errors only):**
| Attempt | Delay |
|---|---|
| 1 | Immediate |
| 2 | 2 minutes |
| 3 | 10 minutes |
| 4 | 30 minutes |

After all retries exhausted: set status `failed`, email admin.

**Non-retryable errors** (set `failed` immediately, no retry):
- Unsupported file type
- Empty file (0 bytes)
- Invalid object path
- No matching user
- Invalid API key (401/403 from provider)
- File not found permanently (404 from GCS)

---

### 4.5 AssemblyAI Transcription

**API endpoint:** `POST https://api.assemblyai.com/v2/transcript`

**Request parameters:**
```json
{
  "audio_url": "<signed GCS URL or uploaded URL>",
  "speech_models": ["universal-3-pro", "universal-2"],
  "speaker_labels": true,
  "speakers_expected": 2,
  "punctuate": true,
  "format_text": true,
  "language_detection": true,
  "webhook_url": "http://{DROPLET_IP}/webhook/assemblyai",
  "webhook_auth_header_name": "X-AssemblyAI-Secret",
  "webhook_auth_header_value": "{ASSEMBLYAI_WEBHOOK_SECRET}"
}
```

**Model fallback:** `speech_models` array provides automatic fallback — Universal-3 Pro for supported languages (EN/ES/DE/FR/IT/PT), Universal-2 for all others.

**Speaker diarisation notes:**
- 3CX recordings are mono (mixed audio) — speaker separation is probabilistic, not channel-based
- Use `speakers_expected: 2` for phone calls
- If only 1 speaker detected in utterances: note "Single participant detected" in email
- Never label speakers as "Agent" / "Customer" with certainty — use "Likely Agent" / "Likely Customer"

**Speaker classification heuristic (post-transcript):**
1. Check which speaker's first utterance contains greeting phrases ("Thanks for calling", "How can I help", etc.)
2. That speaker = Likely Agent
3. Other speaker = Likely Customer
4. If no greeting detected: label as "Speaker A" / "Speaker B"

**Configurable via env:**
- `ASSEMBLYAI_API_KEY` (mapped from `assemblyapi` in `.env`)
- `ASSEMBLYAI_MODEL` (default: `universal-3-pro`)
- `ASSEMBLYAI_SPEAKER_DIARIZATION` (default: `true`)

**GCS region note:** The bucket (`cachiai-recordings`) is in the EU region (`GCP_BUCKET_LOCATION=EU`). Use the EU-specific GCS endpoint or ensure the service account and Droplet network can reach EU GCS without restriction. Audio downloads from EU GCS to a non-EU Droplet will incur cross-region egress — acceptable for MVP but worth noting.

---

### 4.6 AssemblyAI Webhook Callback (`POST /webhook/assemblyai`)

**Authentication:**
- Validate `X-AssemblyAI-Secret` header against `ASSEMBLYAI_WEBHOOK_SECRET` env var
- Return `401` if missing or invalid

**Payload:**
```json
{
  "transcript_id": "abc123",
  "status": "completed"
}
```

**Processing:**
1. Look up `recording_jobs` by `assemblyai_transcript_id`
2. If status = `completed`: fetch full transcript from AssemblyAI, continue to summarisation
3. If status = `error`: mark job as `failed`, log error, email admin
4. Return `200` immediately (do not block — enqueue continuation job)

---

### 4.7 OpenAI Structured Summary

**Model selection (in order, with fallback):**
1. `OPENAI_SUMMARY_MODEL` env var (default: `gpt-5.2`)
2. On `model_not_found` / `invalid_request_error` / `permission_denied`: try `gpt-5`
3. Then `gpt-5-mini`
4. Then `gpt-4o`
5. Log each fallback with reason

**API call:** Use OpenAI Structured Outputs with JSON schema enforcement.

**System prompt:**
```
You are a call analysis assistant. Analyse the following phone call transcript and return a structured JSON summary. The call is between a business agent and a customer. Use "Likely Agent" and "Likely Customer" labels — do not assert certainty about speaker identity.
```

**Required JSON schema (enforced via Structured Outputs):**
```json
{
  "short_summary": "string",
  "customer_intent": "string",
  "products_discussed": ["string"],
  "main_points": ["string"],
  "objections_or_concerns": ["string"],
  "questions_asked": ["string"],
  "action_items": ["string"],
  "agent_promises": ["string"],
  "customer_requests": ["string"],
  "follow_up_required": "boolean",
  "follow_up_reason": "string",
  "sentiment": "positive | neutral | negative",
  "likely_agent_summary": "string",
  "likely_customer_summary": "string"
}
```

**On failure:**
- Retry once
- If still failing: send email with transcript only, set `summary_status = failed` on job
- Log error

---

### 4.8 Email — Gmail SMTP

**Transport:** Python `smtplib` over TLS (port 587, STARTTLS) to `smtp.gmail.com`.  
**Auth:** Gmail App Password (requires 2-Step Verification enabled on the Gmail account).

**Configuration (env vars):**
- `GMAIL_ADDRESS` — the Gmail account address (e.g. `you@gmail.com`)
- `GMAIL_APP_PASSWORD` — 16-character App Password generated in Google Account → Security → App Passwords
- `EMAIL_FROM_NAME` — display name (e.g. `3CX Transcriptions`)
- `REPLY_TO_EMAIL` — optional reply-to
- `ADMIN_EMAIL` — fallback for alerts and unmatched recordings

**From header format:** `"3CX Transcriptions <you@gmail.com>"` (display name + address)

**Removed:** `POSTMARK_API_KEY`, `FROM_EMAIL`, `EMAIL_PROVIDER` env vars are no longer used.

**Transcript summary email:**

*Subject:*
```
Call Summary: {extracted_name} - {phone_number} - {formatted_date}
```
Example: `Call Summary: Celia Perez - 01553888553 - 14 May 2026`

*Body (plain text + HTML):*
```
Call Summary

User:           Celia Perez
Extension:      4166
Phone Number:   01553888553
Date:           14 May 2026
Call ID:        3644

Short Summary:
{short_summary}

Customer Intent:
{customer_intent}

Main Points:
- {main_points[0]}
- {main_points[1]}

Products / Services Discussed:
- {products_discussed}

Objections / Concerns:
- {objections_or_concerns}

Action Items:
- {action_items}

Follow-Up Required: Yes / No
Follow-Up Reason: {follow_up_reason}

Who Said What:
Likely Agent:    {likely_agent_summary}
Likely Customer: {likely_customer_summary}

Full Transcript:
Speaker A:
{utterances[speaker=A]}

Speaker B:
{utterances[speaker=B]}

---
Note: Speaker attribution is based on AI analysis of mono audio and may not be 100% accurate.
```

**Admin alert email (unmatched recording):**

*Subject:* `Unmatched 3CX Recording — Action Required`

*Body:*
```
A new recording could not be matched to a user.

File:               recordings/4166/[Celia Perez]_4166-...wav
Extracted name:     Celia Perez
Extracted extension: 4166

Required action:
Add this user in the dashboard or correct their extension.

Dashboard: http://{DROPLET_IP}/admin
```

**Admin alert email (job failed):**

*Subject:* `3CX Recording Job Failed — {filename}`

*Body:* Job ID, file path, error message, retry count, link to dashboard.

**Delivery logging:**
- Log Postmark message ID on success
- Log error on delivery failure
- Delivery failures are retryable (see §4.4 retry policy)

---

## 5. Database Schema

### `users`
```sql
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  full_name TEXT NOT NULL,
  email TEXT NOT NULL,
  extension TEXT NOT NULL,
  active BOOLEAN DEFAULT true,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX unique_active_extension
ON users(extension)
WHERE active = true;
```

### `recording_jobs`
```sql
CREATE TABLE recording_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  gcs_bucket TEXT NOT NULL,
  gcs_object_name TEXT NOT NULL,
  gcs_generation TEXT,
  file_size BIGINT,
  extracted_name TEXT,
  folder_extension TEXT,
  filename_extension TEXT,
  phone_number TEXT,
  call_timestamp TEXT,
  call_id TEXT,
  file_extension TEXT,
  matched_user_id UUID REFERENCES users(id),
  recipient_email TEXT,
  status TEXT NOT NULL DEFAULT 'received',
  summary_status TEXT,
  assemblyai_transcript_id TEXT,
  email_message_id TEXT,
  email_transcript_message_id TEXT,
  error_message TEXT,
  retry_count INT DEFAULT 0,
  created_at TIMESTAMP DEFAULT NOW(),
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  emailed_at TIMESTAMP
);

CREATE UNIQUE INDEX unique_gcs_recording
ON recording_jobs(gcs_bucket, gcs_object_name, gcs_generation);
```

### `processing_logs`
```sql
CREATE TABLE processing_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  recording_job_id UUID REFERENCES recording_jobs(id),
  level TEXT NOT NULL,  -- info | warning | error
  message TEXT NOT NULL,
  metadata_json JSONB,
  created_at TIMESTAMP DEFAULT NOW()
);
```

### `settings`
```sql
CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TIMESTAMP DEFAULT NOW()
);
```
Settings table stores non-sensitive configuration only (e.g. `debug_mode`, `store_transcripts`, `max_retries`, `default_email_subject`). API keys are never stored in the database — they live in environment variables only.

---

## 6. Job Status State Machine

```
received → queued → processing → transcribing → [awaiting_callback] → summarising → emailing → completed
                                                                                              ↓
                                                                                           failed
                                                                                           unmatched
                                                                                           failed_parser
                                                                                           ignored
```

| Status | Meaning |
|---|---|
| `received` | Webhook received and validated |
| `queued` | Added to Redis queue |
| `processing` | Worker picked up job, downloading file |
| `transcribing` | File submitted to AssemblyAI, waiting for callback |
| `awaiting_callback` | AssemblyAI processing in progress |
| `summarising` | Transcript received, sending to OpenAI |
| `emailing` | Summary ready, sending via Postmark |
| `completed` | Email sent successfully |
| `failed` | Unrecoverable error after retries |
| `unmatched` | No user found for this extension/name |
| `failed_parser` | Filename could not be parsed |
| `ignored` | Duplicate event or unsupported event type |

---

## 7. Admin Dashboard (React SPA)

### Tech stack
- **Frontend:** React 18 + TypeScript + Vite
- **UI library:** shadcn/ui + Tailwind CSS
- **State/data:** React Query (TanStack Query) for API calls
- **Auth:** JWT stored in `httpOnly` cookie, issued by FastAPI on login
- **Routing:** React Router v6

### Pages

#### 7.1 Login (`/login`)
- Email + password fields
- Rate-limited: 5 attempts per 15 minutes per IP
- On success: redirect to `/admin/users`
- Admin accounts managed via environment variable or seeded in DB

#### 7.2 Users (`/admin/users`)
- Table: Full Name, Email, Extension, Status (Active/Inactive), Created, Actions
- Search by name, email, or extension
- Add user button → modal form
- Edit user → modal form (same fields)
- Disable/enable toggle
- Send test email button per user

**User form fields (MVP):**
- Full Name (required)
- Email Address (required)
- 3CX Extension (required, must be unique among active users)
- Active (toggle, default: true)

**Test email:** Sends a sample email to the user's address with placeholder transcript content and a "This is a test email" banner.

#### 7.3 Jobs (`/admin/jobs`)
- Table with columns: Date, Recording File, Extracted Name, Extension, Matched User, Recipient Email, Status, Error, Actions
- Filter by status, date range
- Status badge with colour coding
- Retry button for `failed` jobs (triggers re-queue)
- Click row to expand: full log entries for that job

#### 7.4 Settings (`/admin/settings`)
- Read-only display of current env var configuration (masked API keys — show last 4 chars only)
- Editable non-sensitive settings (stored in `settings` DB table):
  - Default email subject template
  - Store transcripts (yes/no)
  - Debug mode (yes/no)
  - Maximum retry attempts
  - Admin fallback email
- Save button updates `settings` table

#### 7.5 Navigation
- Sidebar: Users, Jobs, Settings, Logout
- Header: system name, current admin email

---

## 8. API Endpoints (FastAPI)

### Public (no auth)
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/webhook/gcs` | GCS Pub/Sub push endpoint |
| `POST` | `/webhook/assemblyai` | AssemblyAI completion callback |

### Auth
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/login` | Admin login, returns JWT cookie |
| `POST` | `/api/auth/logout` | Clear JWT cookie |
| `GET` | `/api/auth/me` | Current admin info |

### Users (requires auth)
| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/users` | List users (search, pagination) |
| `POST` | `/api/users` | Create user |
| `GET` | `/api/users/{id}` | Get user |
| `PUT` | `/api/users/{id}` | Update user |
| `PATCH` | `/api/users/{id}/toggle` | Enable/disable user |
| `POST` | `/api/users/{id}/test-email` | Send test email |

### Jobs (requires auth)
| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/jobs` | List jobs (filter, pagination) |
| `GET` | `/api/jobs/{id}` | Get job detail + logs |
| `POST` | `/api/jobs/{id}/retry` | Re-queue failed job |

### Settings (requires auth)
| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/settings` | Get current settings |
| `PUT` | `/api/settings` | Update settings |

---

## 9. Environment Variables

```env
# Application
APP_ENV=production
APP_URL=http://{DROPLET_IP}
SECRET_KEY=                        # JWT signing key

# Database
DATABASE_URL=postgresql://user:pass@postgres:5432/transcriptions

# Redis
REDIS_URL=redis://redis:6379/0

# Google Cloud
GCP_PROJECT_ID=quickstart-1600776214159
GCP_BUCKET_NAME=cachiai-recordings
GCP_RECORDINGS_PREFIX=recordings/
GCP_BUCKET_LOCATION=EU             # Bucket is in EU region
GCP_SERVICE_ACCOUNT_JSON=          # JSON string of service account key

# Pub/Sub webhook auth
PUBSUB_SERVICE_ACCOUNT_EMAIL=      # Expected email in OIDC JWT
WEBHOOK_SECRET=                    # Shared secret fallback

# AssemblyAI
ASSEMBLYAI_API_KEY=                # env var name; .env file uses 'assemblyapi' — normalise to ASSEMBLYAI_API_KEY in code
ASSEMBLYAI_MODEL=universal-3-pro
ASSEMBLYAI_SPEAKER_DIARIZATION=true
ASSEMBLYAI_WEBHOOK_SECRET=         # Validates AssemblyAI callbacks

# OpenAI
OPENAI_API_KEY=
OPENAI_SUMMARY_MODEL=gpt-5.2
OPENAI_FALLBACK_MODELS=gpt-5,gpt-5-mini,gpt-4o

# Email (Gmail SMTP)
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=
EMAIL_FROM_NAME=3CX Transcriptions
REPLY_TO_EMAIL=
ADMIN_EMAIL=

# Behaviour
MAX_RETRIES=4
DELETE_TEMP_FILES=true
STORE_TRANSCRIPTS=false
DEBUG_MODE=false
```

---

## 10. Security Requirements

- Admin login rate-limited (5 attempts / 15 min / IP)
- Passwords hashed with bcrypt
- JWT in `httpOnly` cookie (not localStorage)
- All API keys in environment variables only — never in DB or logs
- GCS service account: read-only access to `cachiai-recordings` bucket only
- Pub/Sub webhook: OIDC JWT validation (primary) + shared secret (fallback)
- AssemblyAI webhook: shared secret header validation
- Temporary audio files deleted immediately after processing
- No audio files, full transcripts, or PII stored by default
- PostgreSQL not exposed outside Docker network
- Redis not exposed outside Docker network
- Nginx is the only public-facing service (ports 80)
- Firewall: allow only ports 22 (SSH), 80 (HTTP)
- Audit log: admin login, user create/edit/disable, manual retry actions

---

## 11. File Handling

- Download audio to `/tmp/{job_id}.wav`
- Process (upload to AssemblyAI via signed URL or direct upload)
- Delete `/tmp/{job_id}.wav` after AssemblyAI confirms receipt
- Never store audio files permanently
- `DELETE_TEMP_FILES=true` by default
- In debug mode (`DEBUG_MODE=true`): retain failed job audio for 24 hours

---

## 12. Error Handling & Admin Alerts

### Retryable errors
- AssemblyAI timeout / rate limit (429)
- OpenAI timeout / rate limit (429)
- Postmark delivery timeout
- GCS download failure (transient 5xx)
- Network errors

### Non-retryable errors
- Unsupported file type
- Empty file (0 bytes)
- Invalid object path / parse failure
- No matching user
- Invalid API key (401/403)
- File not found permanently (GCS 404)

### Admin alert triggers
| Event | Alert |
|---|---|
| Unmatched recording | Immediate email to `ADMIN_EMAIL` |
| Parser failure | Immediate email to `ADMIN_EMAIL` |
| Job failed after all retries | Email to `ADMIN_EMAIL` |
| Transcription failure | Email to `ADMIN_EMAIL` |
| Summary failure | Email to `ADMIN_EMAIL` (transcript still sent to user) |
| Email delivery failure | Email to `ADMIN_EMAIL` |
| Duplicate extension conflict | Blocked at DB level (unique index) |

---

## 13. Docker Compose Structure

```
project/
├── docker-compose.yml
├── docker-compose.override.yml    # local dev overrides
├── .env.example
├── api/
│   ├── Dockerfile
│   ├── main.py
│   ├── routers/
│   │   ├── webhook.py
│   │   ├── users.py
│   │   ├── jobs.py
│   │   ├── settings.py
│   │   └── auth.py
│   ├── services/
│   │   ├── parser.py
│   │   ├── matcher.py
│   │   ├── assemblyai.py
│   │   ├── openai_summary.py
│   │   ├── email.py
│   │   └── gcs.py
│   ├── models/
│   ├── db/
│   └── requirements.txt
├── worker/
│   ├── Dockerfile
│   ├── worker.py
│   └── requirements.txt
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   └── src/
│       ├── pages/
│       │   ├── Login.tsx
│       │   ├── Users.tsx
│       │   ├── Jobs.tsx
│       │   └── Settings.tsx
│       └── components/
└── nginx/
    └── nginx.conf
```

---

## 14. MVP Build Scope

Build in this order:

1. **Project scaffold** — Docker Compose, FastAPI skeleton, PostgreSQL migrations, Redis connection
2. **Database schema** — all four tables with indexes
3. **GCS webhook receiver** — OIDC JWT + shared secret auth, Pub/Sub envelope decoding, job creation
4. **Filename parser** — regex parser, unit tested against real example paths
5. **User matching** — extension → name fallback routing logic
6. **Redis queue + worker** — RQ worker, job state machine
7. **GCS file download** — service account auth, temp file management
8. **AssemblyAI integration** — submit audio, store transcript ID, webhook callback receiver
9. **OpenAI summary** — structured output with model fallback chain
10. **Postmark email** — transcript summary email + admin alert emails
11. **Admin auth** — login endpoint, JWT cookie, rate limiting
12. **Admin API** — users CRUD, jobs list/retry, settings read/write
13. **React SPA** — Login, Users, Jobs, Settings pages with shadcn/ui
14. **Nginx config** — proxy API and serve React build
15. **Deployment scripts** — Droplet setup, Docker Compose production config
16. **End-to-end test** — simulate a real Pub/Sub event through the full pipeline

---

---

## 16. Email & AI Pipeline Overhaul

### 16.1 Problem Statement

The current implementation uses a single OpenAI call that returns a JSON dict, then formats it as plain text in one email. This must be replaced with:

1. Three separate OpenAI calls, each with a dedicated prompt
2. Two separate emails per call (summary email + conversation transcript email)
3. A speaker re-classification step between AssemblyAI diarisation and summarisation
4. HTML-only email bodies (no plain text fallback for the main content emails)

---

### 16.2 Three OpenAI Calls

#### Call 1 — Subject Line Generator
**Model:** `gpt-4o-mini` (hardcoded — cheapest, not configurable)
**Purpose:** Generate the email subject line for both emails
**Input:** Raw transcript text
**Output:** A plain string — the subject line

**Prompt (exact):**
```
You are an assistant that reads a conversation transcript and generates a short, relevant subject line.

The subject line must:
• Be no more than 5 words
• Include the people in the conversation if known; if not, say "Call Summary"
• Clearly relate to the conversation's key topic
• Remain in English
• At the end of the subject line, add the detected language of the transcript in parentheses. Example: (Spanish)

Identify the language of the transcript automatically.

Output only the final subject line. No explanations.
```

**Usage:** The generated subject line is used as the `Subject:` header for **both** emails (summary email and transcript email).

---

#### Call 2 — Speaker Re-Classification
**Model:** Configurable — defaults to `OPENAI_SUMMARY_MODEL` env var (same model used for summaries)
**Purpose:** Improve speaker identity accuracy after AssemblyAI mono-audio diarisation
**Input:** Raw AssemblyAI utterances + recording metadata
**Output:** JSON with speaker mapping, confidence score, and reasoning

**Why this step exists:**
AssemblyAI diarisation on mono audio correctly separates speakers into `Speaker A` / `Speaker B` but cannot reliably identify which is the agent and which is the customer. This LLM step uses conversational context, opening phrases, and recording metadata to make that determination.

**Input payload to the model:**
```json
{
  "utterances": [
    {"speaker": "A", "text": "...", "start": 0, "end": 2500},
    {"speaker": "B", "text": "...", "start": 2600, "end": 5000}
  ],
  "metadata": {
    "extracted_user_name": "Celia Perez",
    "extension": "4166",
    "phone_number": "01553888553",
    "call_timestamp": "20260514131342",
    "matched_user_full_name": "Celia Perez"
  }
}
```

**System prompt (exact):**
```
You are a speaker classification assistant for phone call transcripts.

You will receive:
1. A list of utterances with speaker labels (Speaker A, Speaker B, etc.)
2. Recording metadata including the internal user's name, extension, and phone number.

Your task is to classify each speaker as one of:
- "Likely Agent" — the internal employee who answered or made the call
- "Likely Customer" — the external caller
- "Unknown" — cannot be determined

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
- Never assert certainty — always use "Likely Agent" / "Likely Customer", never "Agent" / "Customer"
- If only one speaker is detected, return that speaker as "Unknown"
```

**Confidence threshold logic (applied in code, not by the model):**
- If `confidence_score >= 0.75`: use `Likely Agent` / `Likely Customer` labels
- If `confidence_score < 0.75`: use `Speaker A` / `Speaker B` labels (neutral)
- Log the confidence score and reason on the job record

---

#### Call 3 — HTML Summary Generator
**Model:** Configurable — `OPENAI_SUMMARY_MODEL` env var (with fallback chain)
**Purpose:** Generate the full structured HTML call summary
**Input:** Re-classified transcript (utterances with resolved speaker labels) + call metadata
**Output:** Complete HTML document string

**Prompt (exact):**
```
You are an expert call-analysis assistant for telecommunications companies. Your ONLY task is to convert a phone call transcript into a complete, accurate meeting summary in strict HTML format.

🚨 NON-NEGOTIABLE RULES

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
   Must contain these comments exactly:
   <!-- REQUIRED: ENGLISH SUMMARY START -->
   <!-- REQUIRED: ORIGINAL-LANGUAGE SUMMARY START -->

7. NOT ENOUGH INFORMATION
   If the transcript contains too little to summarize, output ONLY: 0
```

**Template variables to substitute before sending:**
- `PRIMARY` → `#2698ff`
- `PAGE_BG` → `#f0f4f8`
- `SECTION_BG` → `#f7faff`
- `CALL_TIME` → formatted from the job's `created_at` timestamp (time the transcription was processed), e.g. `14 May 2026 13:13`
- `LANGUAGE_NAME` → detected language name (extracted from the model's output)

**Failure handling:**
- If the model returns `0` (not enough information): send both emails with a "Transcript too short to summarise" notice
- If HTML parsing fails or output is malformed: retry once, then send transcript-only email and set `summary_status = failed`

---

#### Call 4 — Conversation Transcript HTML Generator
**Model:** `gpt-4o-mini` (cheapest — this is formatting only, not analysis)
**Purpose:** Format the raw utterances as an HTML chat-bubble email
**Input:** Re-classified utterances with resolved speaker labels
**Output:** Complete HTML `<table>` string

**Prompt (exact):**
```
Output the entire conversation as a single HTML email body (no markdown, no explanations, no extra text).

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
- Do NOT merge or split sentences.
```

---

### 16.3 Two Emails Per Call

#### Email 1 — Summary Email
- **Subject:** Output of Call 1 (subject line generator)
- **HTML Body:** Output of Call 3 (HTML summary)
- **Recipient:** Matched user's email address
- **Sent via:** Postmark

#### Email 2 — Conversation Transcript Email
- **Subject:** Same subject line as Email 1 (reuse Call 1 output)
- **HTML Body:** Output of Call 4 (conversation bubble HTML)
- **Recipient:** Same matched user email
- **Sent via:** Postmark (second separate API call)

Both emails are sent in sequence. If Email 1 fails, Email 2 is still attempted. Each has its own Postmark MessageID logged separately on the job record.

**DB schema:** `recording_jobs` has `email_message_id TEXT` (summary email) and `email_transcript_message_id TEXT` (transcript email).

---

### 16.4 Updated Worker Pipeline

Replace the current `continue_after_transcription` task with this sequence:

```
1. Fetch full transcript from AssemblyAI (utterances + text)
2. Run speaker re-classification (Call 2) → get speaker_map + confidence_score
3. Apply confidence threshold → resolve final speaker labels
4. Log confidence_score and reason on job record
5. Generate subject line (Call 1, gpt-4o-mini) → subject string
6. Generate HTML summary (Call 3, configurable model) → summary_html string
7. If summary_html == "0": set summary_status = "too_short", use fallback notice
8. Generate conversation HTML (Call 4, gpt-4o-mini) → transcript_html string
9. Send Email 1: subject + summary_html → log email_message_id
10. Send Email 2: subject + transcript_html → log email_transcript_message_id
11. Set job status = "completed"
12. Delete temp file
```

**Status additions:**
- `classifying_speakers` — new status between `transcribing` and `summarising`
- `generating_subject` — new status
- `generating_transcript_html` — new status

---

### 16.5 DB Schema Changes

```sql
-- Add to recording_jobs table
ALTER TABLE recording_jobs
  RENAME COLUMN postmark_message_id TO email_message_id;
ALTER TABLE recording_jobs
  ADD COLUMN IF NOT EXISTS email_transcript_message_id TEXT;
ALTER TABLE recording_jobs ADD COLUMN speaker_confidence_score FLOAT;
ALTER TABLE recording_jobs ADD COLUMN speaker_classification_reason TEXT;
ALTER TABLE recording_jobs ADD COLUMN detected_language TEXT;
```

New Alembic migration: `0002_email_pipeline_overhaul.py`

---

### 16.6 Config Changes

New env vars:
```env
# Speaker re-classification model (defaults to OPENAI_SUMMARY_MODEL)
OPENAI_SPEAKER_MODEL=          # leave blank to use OPENAI_SUMMARY_MODEL
SPEAKER_CONFIDENCE_THRESHOLD=0.75
```

---

### 16.7 Accuracy Improvement Notes

Ranked improvements already incorporated:
1. AssemblyAI diarisation as first pass (existing)
2. LLM speaker re-classification with confidence scoring (new — Call 2)
3. Raw utterances + timestamps passed to summary prompt (new — not flat text)
4. Extension and filename metadata passed to re-classification (new)
5. Confidence threshold — neutral labels if below 0.75 (new)
6. Custom vocabulary support via AssemblyAI `word_boost` parameter (future)
7. Stereo/dual-channel recording in 3CX would eliminate the need for re-classification entirely (operational recommendation, not code)

**Not in scope for this change:**
- Stereo recording configuration (3CX admin setting, outside this system)
- PII redaction
- Custom vocabulary / word_boost (post-MVP)

---

### 16.8 Implementation Steps

1. Add Alembic migration `0002` with new columns
2. Rewrite `api/services/openai_summary.py`:
   - `generate_subject_line(transcript_text) → str`
   - `classify_speakers(utterances, metadata) → dict` (replaces heuristic in assemblyai.py)
   - `generate_html_summary(utterances, speaker_map, metadata) → str`
   - `generate_transcript_html(utterances, speaker_map) → str`
3. Replace `api/services/email.py` — swap Postmark HTTP calls for Gmail SMTP via `smtplib`:
   - `send_summary_email(recipient, subject, html_body) → str`
   - `send_transcript_email(recipient, subject, html_body) → str`
   - `send_test_email(recipient, name) → str`
   - Admin alert helpers unchanged in signature
   - Remove `postmarker` dependency from both `api/requirements.txt` and `worker/requirements.txt`
4. Update `worker/tasks.py` — `continue_after_transcription` with new 10-step pipeline
5. Update `api/config.py` — add `OPENAI_SPEAKER_MODEL` and `SPEAKER_CONFIDENCE_THRESHOLD`
6. Update `api/models/recording_job.py` — add new columns
7. Update Jobs page in React SPA — show `speaker_confidence_score` and `detected_language` in job detail

---

## 15. Gmail SMTP Integration

### 15.1 How it works

All outbound email is sent via `smtp.gmail.com:587` using STARTTLS. The worker and API both import `api/services/email.py` which opens a fresh SMTP connection per send (no persistent connection — Docker containers restart independently).

### 15.2 Getting a Gmail App Password (one-time setup)

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. Security → **2-Step Verification** — enable it if not already on
3. Security → **App Passwords** (appears only after 2FA is enabled)
4. Select app: **Mail** / device: **Other** → type `3CX Transcription` → click **Generate**
5. Copy the 16-character password (shown once — save it immediately)
6. Set `GMAIL_APP_PASSWORD=<16-char-password>` in `.env` (no spaces)

### 15.3 Implementation — `api/services/email.py`

Replace the Postmark `httpx` calls with `smtplib`:

```python
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

def _send(to, subject, html_body, text_fallback=""):
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

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.GMAIL_ADDRESS, settings.GMAIL_APP_PASSWORD)
        smtp.sendmail(settings.GMAIL_ADDRESS, to, msg.as_string())

    # Return a synthetic message ID (no Postmark ID available)
    return f"gmail-{subject[:20]}-{to}"
```

Return value is a synthetic string used only for logging — stored in `job.email_message_id`.

### 15.4 Config changes (`api/config.py`)

Remove:
- `EMAIL_PROVIDER`
- `POSTMARK_API_KEY`
- `FROM_EMAIL`

Add:
- `GMAIL_ADDRESS: str = ""`
- `GMAIL_APP_PASSWORD: str = ""`
- `EMAIL_FROM_NAME: str = "3CX Transcriptions"`

### 15.5 Dependency changes

Remove `postmarker==1.0` from:
- `api/requirements.txt`
- `worker/requirements.txt`

`smtplib` is part of the Python standard library — no new package needed.

### 15.6 Database migration

Rename column `postmark_message_id` → `email_message_id` and `postmark_transcript_message_id` → `email_transcript_message_id` on `recording_jobs`.

---

## 16b. Out of Scope (Post-MVP)

- Multi-company / multi-tenant support
- Manager CC rules
- Language preference per user
- Custom email templates per client
- CRM push integrations
- Dashboard transcript viewer
- PII redaction
- Agent performance scoring
- Sales reports
- Compliance flags
- Monthly usage reports
- Billing per client
- SSL / HTTPS (add after domain is configured)

---

## 17. Deployment to DigitalOcean Droplet

### Target

| Property | Value |
|---|---|
| IP | `138.68.132.0` |
| OS | Ubuntu 24.04 LTS |
| Region | LON1 (London) |
| Cost | $18/mo (2 vCPU, 2 GB RAM, 60 GB SSD) |
| Auth | Password (interactive SSH prompt) |

---

### 16.1 Pre-Deployment: Values You Must Supply

Before running the deploy script, you need to provide the following. Run these commands locally to generate secure random values:

```bash
# Generate all secrets at once
echo "POSTGRES_PASSWORD=$(openssl rand -hex 20)"
echo "SECRET_KEY=$(openssl rand -hex 32)"
echo "WEBHOOK_SECRET=$(openssl rand -hex 16)"
echo "ASSEMBLYAI_WEBHOOK_SECRET=$(openssl rand -hex 16)"
```

| Variable | What it is | Source |
|---|---|---|
| `POSTGRES_PASSWORD` | Password for the local Postgres container | Generate with `openssl rand -hex 20` |
| `SECRET_KEY` | JWT signing key for the API | Generate with `openssl rand -hex 32` |
| `WEBHOOK_SECRET` | Shared secret for GCS Pub/Sub webhook | Generate with `openssl rand -hex 16` |
| `ASSEMBLYAI_WEBHOOK_SECRET` | Secret AssemblyAI sends on callbacks | Generate with `openssl rand -hex 16` |
| `GMAIL_ADDRESS` | Gmail account that sends emails | Your Gmail address (e.g. `you@gmail.com`) |
| `GMAIL_APP_PASSWORD` | Gmail App Password | Google Account → Security → 2-Step Verification → App Passwords |
| `EMAIL_FROM_NAME` | Display name in From header | e.g. `3CX Transcriptions` |
| `ADMIN_EMAIL` | Email for admin notifications | Your email address |
| `GCP_SERVICE_ACCOUNT_JSON` | Full JSON key for the worker SA | Created in §16.2 below |
| `PUBSUB_SERVICE_ACCOUNT_EMAIL` | GCP SA email that signs Pub/Sub OIDC tokens | Created in §16.2 below |

**Values already known (from workspace `.env`):**

| Variable | Value |
|---|---|
| `ASSEMBLYAI_API_KEY` | `141f002a3aaf4c42b9820b09038f0985` |
| `OPENAI_API_KEY` | In workspace `.env` |
| `GCP_PROJECT_ID` | `quickstart-1600776214159` |
| `GCP_BUCKET_NAME` | `cachiai-recordings` |
| `GCP_RECORDINGS_PREFIX` | `recordings/` |
| `GCP_BUCKET_LOCATION` | `EU` |
| `APP_URL` | `http://138.68.132.0` |

---

### 16.2 Creating the GCP Service Account (run locally, requires `gcloud` CLI)

```bash
# 1. Create the service account
gcloud iam service-accounts create 3cx-transcription-worker \
  --display-name="3CX Transcription Worker" \
  --project=quickstart-1600776214159

# 2. Grant read access to the GCS bucket
gcloud storage buckets add-iam-policy-binding gs://cachiai-recordings \
  --member="serviceAccount:3cx-transcription-worker@quickstart-1600776214159.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"

# 3. Grant Pub/Sub subscriber permission
gcloud projects add-iam-policy-binding quickstart-1600776214159 \
  --member="serviceAccount:3cx-transcription-worker@quickstart-1600776214159.iam.gserviceaccount.com" \
  --role="roles/pubsub.subscriber"

# 4. Download the JSON key
gcloud iam service-accounts keys create ~/3cx-sa-key.json \
  --iam-account=3cx-transcription-worker@quickstart-1600776214159.iam.gserviceaccount.com

# 5. Convert to single-line JSON (required for .env)
python3 -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))" ~/3cx-sa-key.json
```

Copy the output of step 5 — that is the value for `GCP_SERVICE_ACCOUNT_JSON`.

`PUBSUB_SERVICE_ACCOUNT_EMAIL` = `3cx-transcription-worker@quickstart-1600776214159.iam.gserviceaccount.com`

---

### 16.3 Deployment Script (`scripts/deploy_to_droplet.sh`)

A single script run from this workspace that performs all steps end-to-end.

**What it does:**

1. **Validate env** — checks all required variables are set (no `FILL_IN` placeholders remain)
2. **Assemble `.env.droplet`** — merges known values from workspace `.env` with user-supplied secrets
3. **rsync project files** to `root@138.68.132.0:/opt/3cx-transcription/` (password prompt)
4. **SSH: run `deploy.sh`** on the Droplet — installs Docker, configures UFW (ports 22 + 80)
5. **scp `.env.droplet`** to `/opt/3cx-transcription/.env` on the Droplet
6. **SSH: run `start.sh`** — builds React frontend, runs Alembic migrations, starts all containers
7. **SSH: health check** — polls `http://138.68.132.0/api/health` until `{"status":"ok"}`
8. **SSH: create admin user** — runs `scripts/create_admin.py` with email/password (prompted interactively)
9. **Update Pub/Sub subscription** — runs `gcloud pubsub subscriptions modify-push-config` (prompted for subscription name)
10. **Print summary** — Droplet URL, admin dashboard URL, webhook URL to verify in GCP

**Script location:** `scripts/deploy_to_droplet.sh` (run from workspace root)

**Usage:**
```bash
cd /workspaces/workspaces
bash scripts/deploy_to_droplet.sh
```

The script will prompt for any missing values before proceeding.

---

### 16.4 Acceptance Criteria

- [ ] `curl http://138.68.132.0/api/health` returns `{"status":"ok"}`
- [ ] `curl http://138.68.132.0/` returns the React frontend (HTTP 200)
- [ ] Admin login works at `http://138.68.132.0/admin/users`
- [ ] `docker compose ps` on the Droplet shows postgres, redis, api, worker, nginx all `Up`
- [ ] No `FILL_IN` placeholders remain in `/opt/3cx-transcription/.env` on the Droplet
- [ ] GCP Pub/Sub push subscription endpoint is set to `http://138.68.132.0/webhook/gcs`

---

### 16.5 Files Created by This Task

| File | Action |
|---|---|
| `scripts/deploy_to_droplet.sh` | **Create** — master deploy script, run from workspace |

The existing `3cx-transcription/scripts/deploy.sh` and `scripts/start.sh` are **not modified** — they run on the Droplet as-is.

`.env.droplet` is generated at runtime and **not committed to git**.
