#!/bin/sh
# Universal entrypoint — Railway, Render, Fly, and local docker all set $PORT
# differently (or not at all). Resolve it once here.
set -e
: "${PORT:=8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
