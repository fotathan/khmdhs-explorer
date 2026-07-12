# ---------------------------------------------------------------------------- #
# Dockerfile — runs the FastAPI app under uvicorn.
# Works on Render, Railway, Fly.io, or any container host. The app reads these
# environment variables at runtime (see render.yaml for the deploy-time set):
#   DATABASE_URL  — your Supabase (or other) Postgres connection string
#   SECRET_KEY    — signs the session cookies for the accounts/auth system;
#                   MANDATORY in production (the app refuses to start without it)
# Auth is per-user accounts with server-side sessions — there is no shared
# APP_PASSWORD gate any more. See render.yaml and app/main.py for the full env.
# ---------------------------------------------------------------------------- #
FROM python:3.12-slim

# System deps kept minimal; psycopg[binary] ships its own libpq.
WORKDIR /app

# Run as an unprivileged user (defence-in-depth: a compromised app process can't
# act as root inside the container). Created here so the COPY steps below can
# hand it ownership; the actual USER switch happens at the end, after all
# root-only build steps (apt, pip). Fixed UID so bind-mounted volumes have stable
# ownership.
RUN useradd --system --create-home --home-dir /home/appuser --uid 10001 appuser

# Tesseract (+ Greek data) powers the local OCR tier in local_ocr.py — the middle
# step between pdfplumber and the Anthropic API for scanned / broken-font PDFs.
# NON-FATAL: the OCR tier self-disables gracefully (local_ocr.available()) if the
# binary is missing, so a transient apt failure must never fail the whole deploy.
# Retries guard against flaky package mirrors on the build host.
RUN (apt-get update -o Acquire::Retries=5 \
     && apt-get install -y --no-install-recommends -o Acquire::Retries=5 \
        tesseract-ocr tesseract-ocr-ell \
     && rm -rf /var/lib/apt/lists/*) \
    || echo "WARNING: tesseract install failed — local OCR tier disabled at runtime"

# Install Python deps first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code and templates (owned by the runtime user).
COPY --chown=appuser:appuser app/ ./app/
# The local OCR tier lives at the repo root; the interactive full-text editor
# (app/tables.py) imports it, so it must be in the image alongside app/.
COPY --chown=appuser:appuser local_ocr.py ./

# Ingestion + ops CLIs. db.py is spawned as a subprocess by the job worker
# (worker.py — the background service that drains admin-launched jobs) and by
# cron_catchup.py (the scheduled catchup cron); the web app also runs worker.py
# inline in local dev. They must all be in the image.
COPY --chown=appuser:appuser db.py khmdhs_ingest.py diavgeia_ingest.py ted_ingest.py cron_catchup.py worker.py ./

# Hand the working dir to appuser too, so runtime writes succeed as non-root:
# local_fs attachments (ATTACHMENTS_DIR defaults to /app/attachment_store) and
# Python's .pyc cache. Then drop privileges for everything that follows.
RUN chown appuser:appuser /app
USER appuser

# Render (and most hosts) inject $PORT. Default to 8000 for local `docker run`.
ENV PORT=8000

# Shell form so $PORT expands. Single worker is plenty for a read-mostly,
# few-users private app; bump --workers later if needed. --no-access-log because
# the app emits its own structured (JSON) request log (see app/obs.py).
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log
