# 3CX Transcription System

Automatically transcribes 3CX call recordings from Google Cloud Storage, generates AI summaries, and emails results to the matched user.

## Architecture

```
GCS bucket → Pub/Sub → POST /webhook/gcs (FastAPI)
                              ↓
                         Redis queue
                              ↓
                           Worker
                         ↙       ↘
                  AssemblyAI    (callback)
                              ↓
                           OpenAI
                              ↓
                          Postmark → user email
```

**Services:** FastAPI · RQ Worker · PostgreSQL · Redis · Nginx · React SPA

---

## First-time Deployment

### 1. Provision a DigitalOcean Droplet

- Ubuntu 24.04 LTS, 2 vCPU, 4 GB RAM, 80 GB SSD

### 2. Install Docker on the Droplet

```bash
ssh root@YOUR_DROPLET_IP
bash scripts/deploy.sh
```

### 3. Copy project files to the Droplet

```bash
rsync -avz --exclude node_modules --exclude .git \
  ./3cx-transcription/ root@YOUR_DROPLET_IP:/opt/3cx-transcription/
```

### 4. Configure environment

```bash
ssh root@YOUR_DROPLET_IP
cd /opt/3cx-transcription
cp .env.example .env
nano .env   # fill in all values
```

Key values to set:

| Variable | Description |
|---|---|
| `APP_URL` | `http://YOUR_DROPLET_IP` |
| `SECRET_KEY` | Random 32+ char string |
| `POSTGRES_PASSWORD` | Strong password |
| `GCP_SERVICE_ACCOUNT_JSON` | Full JSON of GCS service account key |
| `PUBSUB_SERVICE_ACCOUNT_EMAIL` | Email of the Pub/Sub push SA |
| `WEBHOOK_SECRET` | Random secret for fallback auth |
| `ASSEMBLYAI_API_KEY` | Your AssemblyAI key |
| `ASSEMBLYAI_WEBHOOK_SECRET` | Random secret for AssemblyAI callbacks |
| `OPENAI_API_KEY` | Your OpenAI key |
| `POSTMARK_API_KEY` | Your Postmark server token |
| `FROM_EMAIL` | Verified Postmark sender address |
| `ADMIN_EMAIL` | Where admin alerts are sent |

### 5. Start the stack

```bash
bash scripts/start.sh
```

This will:
- Build the React frontend
- Run database migrations
- Start all services
- Print the admin dashboard URL and Pub/Sub webhook URL

### 6. Create the first admin user

```bash
docker compose exec api python scripts/create_admin.py admin@example.com yourpassword
```

### 7. Configure GCS Pub/Sub

In Google Cloud Console:

1. Go to **Cloud Storage → cachiai-recordings → Notifications**
2. Create a Pub/Sub notification for `OBJECT_FINALIZE` events
3. Create a **push subscription** pointing to:
   ```
   http://YOUR_DROPLET_IP/webhook/gcs
   ```
4. Enable OIDC authentication on the subscription, using a service account
5. Set `PUBSUB_SERVICE_ACCOUNT_EMAIL` in `.env` to that service account's email

---

## Day-to-day Operations

### Add a new user

1. Open `http://YOUR_DROPLET_IP/admin/users`
2. Click **Add User**
3. Enter Full Name, Email, 3CX Extension
4. Click **Save**

All future recordings for that extension will be automatically transcribed and emailed.

### View and retry failed jobs

1. Open `http://YOUR_DROPLET_IP/admin/jobs`
2. Filter by `failed` status
3. Click **Retry** on any failed job

### Update the stack

```bash
cd /opt/3cx-transcription
git pull   # or rsync new files
bash scripts/update.sh
```

### View logs

```bash
docker compose logs -f api       # API logs
docker compose logs -f worker    # Worker logs
docker compose logs -f nginx     # Nginx access logs
```

---

## Recording Filename Format

```
recordings/{extension}/[{name}]_{extension}-{phone}_{timestamp}({call_id}).wav
```

Example:
```
recordings/4166/[Celia Perez]_4166-01553888553_20260514131342(3644).wav
```

**Routing priority:**
1. Folder extension → user extension
2. Filename extension → user extension
3. Bracketed name → user full name (case-insensitive, accent-normalised)
4. No match → admin alert email

---

## Environment Variables Reference

See `.env.example` for the full list with descriptions.

---

## GCS Service Account Permissions

The service account needs only:
- `storage.objects.get` on bucket `cachiai-recordings`
- `storage.objects.list` on bucket `cachiai-recordings`

Do not grant broader permissions.
