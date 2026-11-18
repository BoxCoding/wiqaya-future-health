#!/usr/bin/env bash
# Deploy Wiqaya to Cloud Run (re-runnable). Usage: PROJECT_ID=<id> deploy/cloudrun.sh
# The Gemini key is read from .env into Secret Manager, never baked into the image.
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-me-central1}"
SERVICE="${SERVICE:-wiqaya}"
SECRET="wiqaya-gemini-api-key"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com --project "$PROJECT_ID"

NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
SECRETS=()
# Each .env value below goes to Secret Manager and is mounted as that env var; never baked into the image.
for VAR in GEMINI_API_KEY WIQAYA_INGEST_TOKEN; do
  VALUE="$(sed -n "s/^${VAR}=//p" .env | head -1 | tr -d '"')"
  if [ -z "$VALUE" ]; then
    [ "$VAR" = GEMINI_API_KEY ] && { echo "GEMINI_API_KEY missing from .env" >&2; exit 1; }
    continue  # no ingest token: EHR ingest stays off (503)
  fi
  NAME="$([ "$VAR" = GEMINI_API_KEY ] && echo "$SECRET" || echo "wiqaya-ingest-token")"
  if gcloud secrets describe "$NAME" --project "$PROJECT_ID" >/dev/null 2>&1; then
    printf '%s' "$VALUE" | gcloud secrets versions add "$NAME" --data-file=- --project "$PROJECT_ID" >/dev/null
  else
    printf '%s' "$VALUE" | gcloud secrets create "$NAME" --data-file=- --project "$PROJECT_ID"
  fi
  gcloud secrets add-iam-policy-binding "$NAME" --project "$PROJECT_ID" \
    --member "serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" \
    --role roles/secretmanager.secretAccessor >/dev/null
  SECRETS+=("${VAR}=${NAME}:latest")
done

# One instance: decisions and the AI rate limit live in process memory (see app/main.py, app/engine.py).
gcloud run deploy "$SERVICE" --source . --project "$PROJECT_ID" --region "$REGION" \
  --allow-unauthenticated --min-instances 0 --max-instances 1 \
  --memory 1Gi --cpu 1 --timeout 120 --cpu-boost \
  --set-env-vars WIQAYA_ENV=production \
  --set-secrets "$(IFS=,; echo "${SECRETS[*]}")" --quiet

gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format 'value(status.url)'
