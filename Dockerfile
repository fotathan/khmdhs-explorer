# ---------------------------------------------------------------------------- #
# Dockerfile — runs the FastAPI app under uvicorn.
# Works on Render, Railway, Fly.io, or any container host. The app reads two
# environment variables at runtime:
#   DATABASE_URL  — your Supabase (or other) Postgres connection string
#   APP_PASSWORD  — shared login password (enables the auth gate)
#   APP_USERNAME  — optional, defaults to "team"
# ---------------------------------------------------------------------------- #
FROM python:3.12-slim

# System deps kept minimal; psycopg[binary] ships its own libpq.
WORKDIR /app

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

# Copy the application code and templates.
COPY app/ ./app/
# The local OCR tier lives at the repo root; the interactive full-text editor
# (app/tables.py) imports it, so it must be in the image alongside app/.
COPY local_ocr.py ./

# Render (and most hosts) inject $PORT. Default to 8000 for local `docker run`.
ENV PORT=8000

# Shell form so $PORT expands. Single worker is plenty for a read-mostly,
# few-users private app; bump --workers later if needed.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
