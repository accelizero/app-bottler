#!/bin/sh
set -e

DATA_DIR="${BOTTLE_APP_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"

echo "[app-bottler] Starting on port 8080 with data dir $DATA_DIR..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
