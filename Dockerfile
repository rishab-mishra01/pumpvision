# ---------------------------------------------------------------------------
# Pumpvision — Production Docker image
# ---------------------------------------------------------------------------
#
# Base image: mcr.microsoft.com/playwright/python:v1.58.0-noble
#
#   - Ubuntu 24.04 LTS (Noble Numbat)
#   - System Python: 3.12  (matches current Railway Python version)
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

# Shared working directory for all Railway services
WORKDIR /app

# Runtime defaults — no secrets baked in
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

# Ubuntu 24.04 ships `python3` but not `python`.
# The Playwright base image usually creates the alias; add it explicitly as a
# safety net so `python -X utf8 ...` works in railway.json startCommand.
RUN ln -sf "$(which python3)" /usr/local/bin/python 2>/dev/null || true

# Install Python dependencies first — Docker layer is cached until
# requirements.txt changes, regardless of app-code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source.
# See .dockerignore for exclusions (secrets, data/, venvs, session state, etc.)
COPY . .

# Port hint for web service.
# Railway injects the actual PORT env var at runtime; this is documentation only.
EXPOSE 8080

# Default start command — Railway overrides this via railway.json startCommand,
# but keeping it here makes the image usable standalone for local testing.
CMD ["python", "-X", "utf8", "scripts/railway_entrypoint.py"]
