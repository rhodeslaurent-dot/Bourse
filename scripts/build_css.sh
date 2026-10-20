#!/usr/bin/env sh
# Recompile app/web/static/app.css after editing templates (classes are extracted from app/web/templates).
# Uses the standalone Tailwind CLI if present next to this script, else npx.
set -e
cd "$(dirname "$0")/.."
BIN="${TAILWIND_BIN:-./scripts/tailwindcss}"
if [ -x "$BIN" ]; then "$BIN" -c tailwind.config.js -i app/web/static/src.css -o app/web/static/app.css --minify
else npx tailwindcss@3.4.17 -c tailwind.config.js -i app/web/static/src.css -o app/web/static/app.css --minify; fi
