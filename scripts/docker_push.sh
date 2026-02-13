#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

docker build -t fybre/therefore-mcp .
docker push fybre/therefore-mcp
