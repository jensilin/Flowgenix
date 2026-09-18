#!/usr/bin/env bash
# Build and launch Flowgenix in Docker.
#
# Usage:
#   ./script.sh            # build image + run container (default port 7860)
#   FLOWGENIX_PORT=8080 ./script.sh
#   ./script.sh stop
#   ./script.sh logs

set -euo pipefail

IMAGE_NAME="flowgenix"
CONTAINER_NAME="flowgenix"
PORT="${FLOWGENIX_PORT:-${FLOW_STUDIO_PORT:-7860}}"

cd "$(dirname "$0")"

command -v docker >/dev/null 2>&1 || {
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
}

action="${1:-run}"

case "$action" in
  stop)
    echo "==> Stopping ${CONTAINER_NAME}"
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || echo "(not running)"
    exit 0
    ;;
  logs)
    docker logs -f "${CONTAINER_NAME}"
    exit 0
    ;;
  run|"")
    ;;
  *)
    echo "Unknown action: ${action} (expected: run | stop | logs)" >&2
    exit 2
    ;;
esac

echo "==> Building Docker image: ${IMAGE_NAME}"
docker build -t "${IMAGE_NAME}" .

if docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  echo "==> Removing existing container: ${CONTAINER_NAME}"
  docker rm -f "${CONTAINER_NAME}" >/dev/null
fi

mkdir -p "$(pwd)/migrations"

echo "==> Starting Flowgenix on port ${PORT}"
docker run -d \
  --name "${CONTAINER_NAME}" \
  -p "${PORT}:7860" \
  -v "$(pwd)/migrations:/app/migrations" \
  "${IMAGE_NAME}"

echo
echo "==> Flowgenix is starting: http://localhost:${PORT}/"
echo "    Logs:  ./script.sh logs"
echo "    Stop:  ./script.sh stop"
