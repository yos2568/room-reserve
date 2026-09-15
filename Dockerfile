# syntax=docker/dockerfile:1

# --- Stage 1: locally compile Tailwind CSS -----------------------------------
# V3 section 2 requires locally compiled Tailwind rather than a CDN script.
FROM node:22.11.0-bookworm-slim AS css
WORKDIR /build
COPY package.json package-lock.json* ./
# --include=dev is explicit: tailwindcss is a devDependency, and an inherited
# NODE_ENV=production would otherwise make npm omit it and build nothing.
RUN if [ -f package-lock.json ]; then npm ci --include=dev; else npm install --include=dev; fi
COPY tailwind.config.js ./
COPY tailwind/ ./tailwind/
COPY core/templates/ ./core/templates/
RUN npm run build:css

# --- Stage 2: application ----------------------------------------------------
FROM python:3.12.11-slim-bookworm AS app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=roomreserve.settings.prod

WORKDIR /app

# libpq is required by psycopg at runtime; curl backs the health check.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ ./requirements/
RUN pip install --no-cache-dir -r requirements/prod.txt

COPY . .
COPY --from=css /build/core/static/css/tailwind.css ./core/static/css/tailwind.css

# Collect static at build time so the image is immutable and self-contained.
# The variable names are the ones roomreserve/settings/prod.py reads with
# required=True; they must stay in step with that file or the build stops here
# with "DJANGO_SECRET_KEY is required but was not set". These are throwaway
# build-time values, never deployed secrets.
RUN DJANGO_SECRET_KEY=build-only DJANGO_ALLOWED_HOSTS=localhost \
    DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost \
    POSTGRES_PASSWORD=build-only EMAIL_HOST=localhost EMAIL_HOST_USER=x \
    EMAIL_HOST_PASSWORD=x DEFAULT_FROM_EMAIL=build@localhost.test \
    python manage.py collectstatic --noinput

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["gunicorn", "roomreserve.wsgi:application", "--config", "gunicorn.conf.py"]
