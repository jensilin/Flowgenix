#!/usr/bin/env bash
# Build and launch NiFi Flow Studio in Docker.
#
# Usage:
#   ./script.sh            # build image + run container (default port 7860)
#   FLOW_STUDIO_PORT=8080 ./script.sh
#   ./script.sh stop       # stop and remove the running container
#   ./script.sh logs       # tail the container logs
#
# NiFi itself is NOT started by this script. Point Flow Studio at your
# existing NiFi instance via the UI's "NiFi URL" field. If NiFi runs on your
# host machine (the usual local setup), use:
#     https://host.docker.internal:8443
# instead of https://127.0.0.1:8443, since 127.0.0.1 inside the container
# refers to the container itself, not your host machine.

set -euo pipefail

IMAGE_NAME="nifi-flow-studio"
CONTAINER_NAME="nifi-flow-studio"
PORT="${FLOW_STUDIO_PORT:-7860}"

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

mkdir -p "$(pwd)/flows"

echo "==> Starting Flow Studio container on port ${PORT}"
docker run -d \
  --name "${CONTAINER_NAME}" \
  --add-host host.docker.internal:host-gateway \
  -p "${PORT}:7860" \
  -v "$(pwd)/flows:/app/flows" \
  "${IMAGE_NAME}"

echo
echo "==> Flow Studio is starting: http://localhost:${PORT}/"
echo "    If NiFi runs on your host machine, use https://host.docker.internal:8443"
echo "    as the NiFi URL inside the UI."
echo
echo "    Logs:  ./script.sh logs"
echo "    Stop:  ./script.sh stop"
