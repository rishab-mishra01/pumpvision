# ---------------------------------------------------------------------------
# Pumpvision — Production Docker image
# ---------------------------------------------------------------------------
#
# Base image: mcr.microsoft.com/playwright/python:v1.58.0-noble
#
#   - Ubuntu 24.04 LTS (Noble Numbat)
#   - System Python: 3.12
#   - Chromium, Firefox, WebKit pre-installed at /ms-playwright
#   - PLAYWRIGHT_BROWSERS_PATH=/ms-playwright  (set by the image)
#   - All system-level browser dependencies already present:
#       libstdc++.so.6, libnss3, libatk1.0, libgbm1, libxcomposite1, etc.
#
#   Version tag v1.58.0 matches requirements.txt `playwright==1.58.0` exactly,
#   so the pre-installed browsers and the Python bindings are always in sync.
#   No separate `python -m playwright install --with-deps chromium` step needed.
#
# ---------------------------------------------------------------------------

FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

# Shared working directory
WORKDIR /app

# Runtime defaults — no secrets baked in
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

# Ubuntu 24.04 ships `python3` but not `python`.
# The Playwright base image usually creates the alias; add it explicitly as a
# safety net so `python -X utf8 ...` works regardless of how it is invoked.
RUN ln -sf "$(which python3)" /usr/local/bin/python 2>/dev/null || true

# Install Python dependencies first — Docker layer is cached until
# requirements.txt changes, regardless of app-code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source.
# See .dockerignore for exclusions (secrets, data/, venvs, session state, etc.)
COPY . .

# Port hint for the web service. Override PORT at run time to change it.
EXPOSE 8080

# NOTE: nothing deploys from this image today. The app is served directly by
# ~/pumpvision-web.sh on the evo, and the scrapers run from a venv on the India
# VPS. It is kept because it pins a known-good Playwright/Python/browser triple
# that matches requirements.txt, which is genuinely hard to reconstruct.
CMD ["sh", "-c", "gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8080} --worker-class gthread --workers 1 --threads 4 --timeout 60"]
