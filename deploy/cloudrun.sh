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

KEY="$(sed -n 's/^GEMINI_API_KEY=//p' .env | head -1 | tr -d '"')"
[ -n "$KEY" ] || { echo "GEMINI_API_KEY missing from .env" >&2; exit 1; }
if gcloud secrets describe "$SECRET" --project "$PROJECT_ID" >/dev/null 2>&1; then
  printf '%s' "$KEY" | gcloud secrets versions add "$SECRET" --data-file=- --project "$PROJECT_ID" >/dev/null
else
  printf '%s' "$KEY" | gcloud secrets create "$SECRET" --data-file=- --project "$PROJECT_ID"
fi
NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT_ID" \
  --member "serviceAccount:${NUMBER}-compute@developer.gserviceaccount.com" \
  --role roles/secretmanager.secretAccessor >/dev/null

# One instance: decisions and the AI rate limit live in process memory (see app/main.py, app/engine.py).
gcloud run deploy "$SERVICE" --source . --project "$PROJECT_ID" --region "$REGION" \
  --allow-unauthenticated --min-instances 0 --max-instances 1 \
  --memory 1Gi --cpu 1 --timeout 120 --cpu-boost \
  --set-env-vars WIQAYA_ENV=production \
  --set-secrets "GEMINI_API_KEY=${SECRET}:latest" --quiet

gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format 'value(status.url)'
