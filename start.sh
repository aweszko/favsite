#!/bin/sh
cd "$(dirname "$0")"
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
echo "Uruchamiam ranking faworytów na http://localhost:8000 ..."
exec $PY app.py --open
