# Flowgenix — containerized NiFi flow migration UI.
#
# This image runs only the migration web UI. It does not run Apache NiFi.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/migrations

ENV PYTHONUNBUFFERED=1 \
    FLOWGENIX_HOST=0.0.0.0 \
    FLOWGENIX_PORT=7860 \
    FLOWGENIX_ARCHIVE_DIR=/app/migrations

EXPOSE 7860

CMD ["python", "-m", "ui.app"]
