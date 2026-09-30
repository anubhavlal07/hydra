#!/usr/bin/env bash

set -euo pipefail

TARGET="${1:-all}"
PY="${PYTHON:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="${PYTHON:-.venv/Scripts/python}"
[[ -x "$PY" ]] || PY=python3

step() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

resolver() {
    step "Lint"
    "$PY" -m ruff check src tests

    step "Format"
    "$PY" -m ruff format --check src tests

    step "Tests"
    "$PY" -m pytest -q
}

dashboard() {
    cd dashboard

    step "Types"
    npx tsc --noEmit

    step "Lint"
    npm run lint

    step "Tests"
    npm run test

    step "Build"
    npm run build
}

case "$TARGET" in
    resolver) resolver ;;
    dashboard) dashboard ;;
    all)
        resolver
        (dashboard)
        ;;
    *)
        echo "usage: scripts/check.sh [resolver|dashboard|all]" >&2
        exit 2
        ;;
esac

printf '\n\033[1;32mAll checks passed.\033[0m\n'
