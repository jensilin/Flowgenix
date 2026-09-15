# NiFi Flow Studio — containerized FastAPI UI.
#
# This image runs only Flow Studio (the web UI + Cursor SDK agent runner).
# It does NOT run Apache NiFi itself — point Flow Studio at an existing NiFi
# instance (e.g. running on your host machine) via the "NiFi URL" field in
# the UI. From inside the container, reach a host-machine NiFi at
# https://host.docker.internal:8443 (see script.sh, which wires this up).

FROM python:3.11-slim

WORKDIR /app

# Install Python deps first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now bring in the application code.
COPY . .

RUN mkdir -p /app/flows

ENV PYTHONUNBUFFERED=1 \
    FLOW_STUDIO_HOST=0.0.0.0 \
    FLOW_STUDIO_PORT=7860

EXPOSE 7860

CMD ["python", "-m", "ui.app"]
