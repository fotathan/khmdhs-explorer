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

# Install Python deps first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code and templates.
COPY app/ ./app/

# Render (and most hosts) inject $PORT. Default to 8000 for local `docker run`.
ENV PORT=8000

# Shell form so $PORT expands. Single worker is plenty for a read-mostly,
# few-users private app; bump --workers later if needed.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
