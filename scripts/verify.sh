#!/usr/bin/env bash
# One-entry verification baseline for TripCanvas.
#
# Usage:  bash scripts/verify.sh [--quick]
#   --quick  skip the frontend suite and production build (backend only)
#
# Notes:
#   * pytest needs an explicit --basetemp on this machine: the sandbox proxies
#     mkdir under /private/var and pytest's default tmp_path otherwise hits
#     EEXIST. This is an environment quirk, not a product defect; CI does not
#     need it, but passing it everywhere keeps local and CI behaviour aligned.
#   * Evaluation suites are reported SEPARATELY on purpose. Never collapse
#     them into a single "AI success rate".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="python3"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
STAMP="$(date +%s)"
BASETEMP="/tmp/tc-verify-${STAMP}"

step() { printf '\n=== %s ===\n' "$1"; }

step "Backend lint (ruff)"
"$PY" -m ruff check backend scripts

step "Backend tests (full)"
"$PY" -m pytest backend/tests -q --basetemp="${BASETEMP}-backend"

if [ "${1:-}" = "--quick" ]; then
  step "Offline evaluation (30 fixed tasks)"
  "$PY" scripts/evaluate.py --output-dir "/tmp/tc-eval-${STAMP}"
  step "Trajectory evaluation (14 fixed traces)"
  "$PY" scripts/evaluate_trajectory.py
  step "Agent evaluation (3 fixed cases)"
  "$PY" scripts/evaluate_agent.py >/dev/null && echo "agent cases passed"
  step "Real task suite (coverage + four quadrants)"
  "$PY" scripts/evaluate_real_tasks.py
  step "Migration from empty database"
  DATABASE_URL="sqlite:////tmp/tc-migration-${STAMP}.db" "$PY" -m alembic upgrade head
  step "Whitespace check"
  git diff --check
  printf '\nBackend baseline checks finished (--quick)\n'
  exit 0
fi

step "Frontend tests"
npm test

step "Frontend type check + production build"
npm run typecheck
npm run build

step "Offline evaluation (30 fixed tasks)"
"$PY" scripts/evaluate.py --output-dir "/tmp/tc-eval-${STAMP}"

step "Trajectory evaluation (14 fixed traces)"
"$PY" scripts/evaluate_trajectory.py

step "Agent evaluation (3 fixed cases)"
"$PY" scripts/evaluate_agent.py >/dev/null && echo "agent cases passed"

step "Real task suite (coverage + four quadrants)"
"$PY" scripts/evaluate_real_tasks.py

step "Migration from empty database"
DATABASE_URL="sqlite:////tmp/tc-migration-${STAMP}.db" "$PY" -m alembic upgrade head

step "Whitespace check"
git diff --check

printf '\nFull baseline checks finished\n'
printf 'Observed task coverage and per-task verdicts are listed above.\n'
