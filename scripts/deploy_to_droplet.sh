#!/usr/bin/env bash
# Deploy the 3CX transcription stack to the DigitalOcean Droplet.
# Run from the workspace root: bash scripts/deploy_to_droplet.sh
#
# Prerequisites (local machine):
#   - ssh, scp, rsync installed
#   - gcloud CLI installed and authenticated (for Pub/Sub update)
#   - All secrets collected (script will prompt for missing ones)

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
DROPLET_IP="138.68.132.0"
DROPLET_USER="root"
REMOTE_DIR="/opt/3cx-transcription"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)/3cx-transcription"
WORKSPACE_ENV="$(cd "$(dirname "$0")/.." && pwd)/.env"
ENV_DROPLET="${PROJECT_DIR}/.env.droplet"
GCP_PROJECT_ID="quickstart-1600776214159"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

step()  { echo -e "\n${CYAN}${BOLD}==> $*${NC}"; }
ok()    { echo -e "${GREEN}✓ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $*${NC}"; }
die()   { echo -e "${RED}✗ ERROR: $*${NC}" >&2; exit 1; }
prompt_secret() {
  local var="$1" label="$2"
  local val
  read -rsp "  Enter ${label}: " val; echo
  [[ -z "$val" ]] && die "${label} cannot be empty"
  eval "${var}='${val}'"
}
prompt_value() {
  local var="$1" label="$2" default="${3:-}"
  local val
  if [[ -n "$default" ]]; then
    read -rp "  Enter ${label} [${default}]: " val
    val="${val:-$default}"
  else
    read -rp "  Enter ${label}: " val
  fi
  [[ -z "$val" ]] && die "${label} cannot be empty"
  eval "${var}='${val}'"
}

# ── Load workspace .env ───────────────────────────────────────────────────────
load_workspace_env() {
  if [[ -f "$WORKSPACE_ENV" ]]; then
    # Export key=value pairs, stripping quotes
    while IFS= read -r line; do
      [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
      # Strip surrounding quotes from value
      key="${line%%=*}"
      val="${line#*=}"
      val="${val%\"}"
      val="${val#\"}"
      val="${val%\'}"
      val="${val#\'}"
      export "${key}=${val}" 2>/dev/null || true
    done < "$WORKSPACE_ENV"
  fi
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║   3CX Transcription — Droplet Deployment             ║${NC}"
echo -e "${BOLD}║   Target: ${DROPLET_USER}@${DROPLET_IP}                    ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Step 0: Check local tools ─────────────────────────────────────────────────
step "Checking local prerequisites"
for cmd in ssh scp rsync; do
  command -v "$cmd" &>/dev/null || die "'$cmd' is not installed. Install openssh-client and rsync."
done
ok "ssh, scp, rsync available"

# ── Step 1: Collect secrets ───────────────────────────────────────────────────
step "Collecting configuration values"
load_workspace_env

# Load all known values from workspace .env
ASSEMBLYAI_API_KEY="${assemblyapi:-${ASSEMBLYAI_API_KEY:-}}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
GCP_BUCKET_NAME="${GCP_BUCKET_NAME:-cachiai-recordings}"
GCP_RECORDINGS_PREFIX="${GCP_RECORDINGS_PREFIX:-recordings/}"
GCP_BUCKET_LOCATION="${GCP_BUCKET_LOCATION:-EU}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
SECRET_KEY="${SECRET_KEY:-}"
WEBHOOK_SECRET="${WEBHOOK_SECRET:-}"
ASSEMBLYAI_WEBHOOK_SECRET="${ASSEMBLYAI_WEBHOOK_SECRET:-}"
GMAIL_ADDRESS="${GMAIL_ADDRESS:-}"
GMAIL_APP_PASSWORD="${GMAIL_APP_PASSWORD:-}"
EMAIL_FROM_NAME="${EMAIL_FROM_NAME:-3CX Transcriptions}"
REPLY_TO_EMAIL="${REPLY_TO_EMAIL:-}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
GCP_SERVICE_ACCOUNT_JSON="${GCP_SERVICE_ACCOUNT_JSON:-}"
PUBSUB_SERVICE_ACCOUNT_EMAIL="${PUBSUB_SERVICE_ACCOUNT_EMAIL:-}"

echo ""
echo "  Values loaded from workspace .env:"
echo "    ASSEMBLYAI_API_KEY           = ${ASSEMBLYAI_API_KEY:0:8}..."
echo "    OPENAI_API_KEY               = ${OPENAI_API_KEY:0:8}..."
echo "    GCP_PROJECT_ID               = ${GCP_PROJECT_ID}"
echo "    GCP_BUCKET_NAME              = ${GCP_BUCKET_NAME}"
echo "    POSTGRES_PASSWORD            = ${POSTGRES_PASSWORD:0:8}..."
echo "    SECRET_KEY                   = ${SECRET_KEY:0:8}..."
echo "    WEBHOOK_SECRET               = ${WEBHOOK_SECRET:0:8}..."
echo "    ASSEMBLYAI_WEBHOOK_SECRET    = ${ASSEMBLYAI_WEBHOOK_SECRET:0:8}..."
echo "    GMAIL_ADDRESS                = ${GMAIL_ADDRESS}"
echo "    GMAIL_APP_PASSWORD           = ****"
echo "    ADMIN_EMAIL                  = ${ADMIN_EMAIL}"
echo "    PUBSUB_SERVICE_ACCOUNT_EMAIL = ${PUBSUB_SERVICE_ACCOUNT_EMAIL}"
echo "    GCP_SERVICE_ACCOUNT_JSON     = $([ -n "$GCP_SERVICE_ACCOUNT_JSON" ] && echo 'set' || echo 'MISSING')"
echo ""

# Validate all required values are present
missing=()
[[ -z "$POSTGRES_PASSWORD" ]]            && missing+=("POSTGRES_PASSWORD")
[[ -z "$SECRET_KEY" ]]                   && missing+=("SECRET_KEY")
[[ -z "$WEBHOOK_SECRET" ]]               && missing+=("WEBHOOK_SECRET")
[[ -z "$ASSEMBLYAI_WEBHOOK_SECRET" ]]    && missing+=("ASSEMBLYAI_WEBHOOK_SECRET")
[[ -z "$GMAIL_ADDRESS" ]]                && missing+=("GMAIL_ADDRESS")
[[ -z "$GMAIL_APP_PASSWORD" ]]           && missing+=("GMAIL_APP_PASSWORD")
[[ -z "$ADMIN_EMAIL" ]]                  && missing+=("ADMIN_EMAIL")
[[ -z "$GCP_SERVICE_ACCOUNT_JSON" ]]     && missing+=("GCP_SERVICE_ACCOUNT_JSON")
[[ -z "$PUBSUB_SERVICE_ACCOUNT_EMAIL" ]] && missing+=("PUBSUB_SERVICE_ACCOUNT_EMAIL")
[[ -z "$ASSEMBLYAI_API_KEY" ]]           && missing+=("ASSEMBLYAI_API_KEY (assemblyapi)")
[[ -z "$OPENAI_API_KEY" ]]               && missing+=("OPENAI_API_KEY")

if [[ ${#missing[@]} -gt 0 ]]; then
  die "Missing required values in workspace .env: ${missing[*]}"
fi
ok "All required values present"

# Admin user for the dashboard
echo ""
prompt_value  ADMIN_USER_EMAIL    "Admin dashboard email (for first login)"
prompt_secret ADMIN_USER_PASSWORD "Admin dashboard password"

# Pub/Sub subscription name
echo ""
echo "  Find your Pub/Sub subscription name in GCP Console → Pub/Sub → Subscriptions."
prompt_value PUBSUB_SUBSCRIPTION "Pub/Sub subscription name (e.g. gcs-recordings-sub)"

ok "All values collected"

# ── Step 2: Assemble .env.droplet ─────────────────────────────────────────────
step "Assembling .env.droplet"

DATABASE_URL="postgresql://transcriptions:${POSTGRES_PASSWORD}@postgres:5432/transcriptions"

cat > "$ENV_DROPLET" <<EOF
# Generated by deploy_to_droplet.sh — do not commit this file
APP_ENV=production
APP_URL=http://${DROPLET_IP}
SECRET_KEY=${SECRET_KEY}

POSTGRES_USER=transcriptions
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=transcriptions
DATABASE_URL=${DATABASE_URL}

REDIS_URL=redis://redis:6379/0

GCP_PROJECT_ID=${GCP_PROJECT_ID}
GCP_BUCKET_NAME=${GCP_BUCKET_NAME}
GCP_RECORDINGS_PREFIX=${GCP_RECORDINGS_PREFIX}
GCP_BUCKET_LOCATION=${GCP_BUCKET_LOCATION}
GCP_SERVICE_ACCOUNT_JSON=${GCP_SERVICE_ACCOUNT_JSON}

PUBSUB_SERVICE_ACCOUNT_EMAIL=${PUBSUB_SERVICE_ACCOUNT_EMAIL}
WEBHOOK_SECRET=${WEBHOOK_SECRET}

ASSEMBLYAI_API_KEY=${ASSEMBLYAI_API_KEY}
ASSEMBLYAI_MODEL=universal-3-pro
ASSEMBLYAI_SPEAKER_DIARIZATION=true
ASSEMBLYAI_WEBHOOK_SECRET=${ASSEMBLYAI_WEBHOOK_SECRET}

OPENAI_API_KEY=${OPENAI_API_KEY}
OPENAI_SUMMARY_MODEL=gpt-5.2
OPENAI_FALLBACK_MODELS=gpt-5,gpt-5-mini,gpt-4o
OPENAI_SPEAKER_MODEL=
SPEAKER_CONFIDENCE_THRESHOLD=0.75

GMAIL_ADDRESS=${GMAIL_ADDRESS}
GMAIL_APP_PASSWORD=${GMAIL_APP_PASSWORD}
EMAIL_FROM_NAME=${EMAIL_FROM_NAME}
REPLY_TO_EMAIL=${REPLY_TO_EMAIL}
ADMIN_EMAIL=${ADMIN_EMAIL}

MAX_RETRIES=4
DELETE_TEMP_FILES=true
STORE_TRANSCRIPTS=false
DEBUG_MODE=false
EOF

# Validate — no FILL_IN placeholders
if grep -q "FILL_IN" "$ENV_DROPLET"; then
  die ".env.droplet still contains FILL_IN placeholders. Aborting."
fi

ok ".env.droplet written to ${ENV_DROPLET}"

# ── Step 3: rsync project files ───────────────────────────────────────────────
step "Copying project files to Droplet (you will be prompted for the SSH password)"
echo "  rsync → ${DROPLET_USER}@${DROPLET_IP}:${REMOTE_DIR}/"
echo ""

rsync -avz --progress \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'node_modules' \
  --exclude '.env' \
  --exclude '.env.droplet' \
  --exclude 'dist' \
  --exclude '.pytest_cache' \
  "${PROJECT_DIR}/" \
  "${DROPLET_USER}@${DROPLET_IP}:${REMOTE_DIR}/"

ok "Files synced"

# ── Step 4: Run deploy.sh on Droplet ─────────────────────────────────────────
step "Running deploy.sh on Droplet (installs Docker, configures firewall)"
echo "  You may be prompted for the SSH password again."
echo ""

ssh "${DROPLET_USER}@${DROPLET_IP}" "bash ${REMOTE_DIR}/scripts/deploy.sh"

ok "deploy.sh complete"

# ── Step 5: Copy .env to Droplet ─────────────────────────────────────────────
step "Copying .env to Droplet"
scp "$ENV_DROPLET" "${DROPLET_USER}@${DROPLET_IP}:${REMOTE_DIR}/.env"
ok ".env copied"

# ── Step 6: Run start.sh on Droplet ──────────────────────────────────────────
step "Starting the stack (builds frontend, runs migrations, starts containers)"
echo "  This will take a few minutes on first run."
echo ""

ssh "${DROPLET_USER}@${DROPLET_IP}" "cd ${REMOTE_DIR} && bash scripts/start.sh"

ok "Stack started"

# ── Step 7: Health check ──────────────────────────────────────────────────────
step "Waiting for API health check"
HEALTH_URL="http://${DROPLET_IP}/api/health"
for i in $(seq 1 20); do
  if curl -sf "$HEALTH_URL" | grep -q '"ok"'; then
    ok "API is healthy at ${HEALTH_URL}"
    break
  fi
  echo "  Waiting… ($i/20)"
  sleep 5
  if [[ $i -eq 20 ]]; then
    warn "API did not respond healthy after 100s. Check: ssh ${DROPLET_USER}@${DROPLET_IP} 'docker compose -f ${REMOTE_DIR}/docker-compose.yml logs api'"
  fi
done

# ── Step 8: Create admin user ─────────────────────────────────────────────────
step "Creating first admin user"
ssh "${DROPLET_USER}@${DROPLET_IP}" \
  "docker compose -f ${REMOTE_DIR}/docker-compose.yml exec -T api \
   python /app/../scripts/create_admin.py '${ADMIN_USER_EMAIL}' '${ADMIN_USER_PASSWORD}'"
ok "Admin user created: ${ADMIN_USER_EMAIL}"

# ── Step 9: Update Pub/Sub push subscription ──────────────────────────────────
step "Updating GCP Pub/Sub push subscription"
WEBHOOK_URL="http://${DROPLET_IP}/webhook/gcs"

if command -v gcloud &>/dev/null; then
  gcloud pubsub subscriptions modify-push-config "${PUBSUB_SUBSCRIPTION}" \
    --push-endpoint="${WEBHOOK_URL}" \
    --project="${GCP_PROJECT_ID}"
  ok "Pub/Sub subscription '${PUBSUB_SUBSCRIPTION}' now pushes to ${WEBHOOK_URL}"
else
  warn "gcloud CLI not found — skipping automatic Pub/Sub update."
  echo ""
  echo "  Run this manually:"
  echo "    gcloud pubsub subscriptions modify-push-config ${PUBSUB_SUBSCRIPTION} \\"
  echo "      --push-endpoint=${WEBHOOK_URL} \\"
  echo "      --project=${GCP_PROJECT_ID}"
fi

# ── Step 10: Clean up local .env.droplet ─────────────────────────────────────
step "Cleaning up local .env.droplet"
rm -f "$ENV_DROPLET"
ok ".env.droplet removed from local filesystem"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║   Deployment complete!                               ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Frontend:${NC}        http://${DROPLET_IP}/"
echo -e "  ${BOLD}Admin dashboard:${NC} http://${DROPLET_IP}/admin/users"
echo -e "  ${BOLD}API health:${NC}      http://${DROPLET_IP}/api/health"
echo -e "  ${BOLD}Webhook URL:${NC}     http://${DROPLET_IP}/webhook/gcs"
echo ""
echo -e "  ${BOLD}Admin login:${NC}     ${ADMIN_USER_EMAIL}"
echo ""
echo "  Verify containers are running:"
echo "    ssh ${DROPLET_USER}@${DROPLET_IP} 'docker compose -f ${REMOTE_DIR}/docker-compose.yml ps'"
echo ""


