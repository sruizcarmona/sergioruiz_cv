#!/usr/bin/env bash
# run.sh — Monolithic orchestrator for the resume + cover letter pipeline.
#
# Usage:
#   ./run.sh                                # interactive job picker
#   ./run.sh rwe_data_analyst_sanofi.pdf    # explicit job file
#   ./run.sh rwe_data_analyst_sanofi.pdf --skip-llm
#   ./run.sh rwe_data_analyst_sanofi.pdf --cover-only
#   ./run.sh rwe_data_analyst_sanofi.pdf --refresh
#
# Scans job_roles/ for .pdf/.txt files, lets you pick one, then runs:
#   1. build_resumes.py   → tailored resume PDF
#   2. cover_letter.py    → tailored cover letter PDF

set -euo pipefail

# ── Resolve script directory ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Locate venv python (Git Bash on Windows / POSIX) ────────────────────
PY=""
if [[ -x "venv/Scripts/python.exe" ]]; then
  PY="venv/Scripts/python.exe"          # Git Bash on Windows
elif [[ -x "venv/bin/python" ]]; then
  PY="venv/bin/python"                  # linux/mac/WSL
elif [[ -f "venv/Scripts/activate" ]]; then
  # shellcheck disable=SC1091
  source "venv/Scripts/activate"
  PY="python"
elif [[ -f "venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
  PY="python"
else
  echo "ERROR: venv not found at venv/Scripts/ or venv/bin/"
  echo "Create one with: python -m venv venv && pip install -r requirements.txt"
  exit 1
fi

# Windows-safe subprocess defaults (RenderCV Rich crash on Windows terminals)
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
export TERM="${TERM:-dumb}"

# ── Parse args ───────────────────────────────────────────────────────────
JOB_FILE=""
COVER_ONLY=0
SKIP_LLM=0
REFRESH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cover-only) COVER_ONLY=1; shift ;;
    --skip-llm)   SKIP_LLM=1;   shift ;;
    --refresh)    REFRESH=1;    shift ;;
    -h|--help)
      echo "Usage: $0 [JOB_FILE] [--skip-llm] [--cover-only] [--refresh]"
      echo "If JOB_FILE is omitted, an interactive picker is shown."
      exit 0
      ;;
    -*)
      echo "Unknown flag: $1"; exit 1 ;;
    *)
      JOB_FILE="$1"; shift ;;
  esac
done

# ── Discover jobs in job_roles/ ──────────────────────────────────────────
shopt -s nullglob nocaseglob
JOB_FILES=( job_roles/*.pdf job_roles/*.txt )
shopt -u nullglob nocaseglob

if [[ ${#JOB_FILES[@]} -eq 0 ]]; then
  echo "No job description files (.pdf or .txt) found in job_roles/"
  exit 1
fi

# ── Pick job ─────────────────────────────────────────────────────────────
if [[ -z "$JOB_FILE" ]]; then
  echo "Available jobs:"
  for i in "${!JOB_FILES[@]}"; do
    printf "  [%d] %s\n" "$((i+1))" "${JOB_FILES[$i]##*/}"
  done
  echo
  read -rp "Select job # (or 'q' to quit): " pick
  if [[ "$pick" == "q" || "$pick" == "Q" ]]; then
    echo "Aborted."
    exit 0
  fi
  if ! [[ "$pick" =~ ^[0-9]+$ ]] || (( pick < 1 || pick > ${#JOB_FILES[@]} )); then
    echo "Invalid selection: $pick"
    exit 1
  fi
  JOB_FILE="${JOB_FILES[$((pick-1))]}"
else
  # Allow either bare name or full path
  if [[ ! -f "$JOB_FILE" ]]; then
    if [[ -f "job_roles/$JOB_FILE" ]]; then
      JOB_FILE="job_roles/$JOB_FILE"
    else
      echo "Job file not found: $JOB_FILE"
      exit 1
    fi
  fi
fi

JOB_BASE="$(basename "$JOB_FILE")"
JOB_STEM="${JOB_BASE%.*}"

echo
echo "=========================================="
echo " Selected job:  $JOB_BASE"
echo " Mode:          $([[ $COVER_ONLY -eq 1 ]] && echo "cover-letter-only" || echo "full pipeline (resume + cover letter)")"
echo " LLM:           $([[ $SKIP_LLM -eq 1 ]] && echo "skipped (fallback template)" || echo "enabled ($LLM_PROVIDER)")"
echo "=========================================="
echo

BUILD_FLAGS=()
[[ $SKIP_LLM -eq 1 ]] && BUILD_FLAGS+=(--skip-llm)
[[ $REFRESH  -eq 1 ]] && BUILD_FLAGS+=(--refresh)

# ── Step 1: Resume ───────────────────────────────────────────────────────
if [[ $COVER_ONLY -eq 0 ]]; then
  echo "▶ Step 1/2: Building tailored resume..."
  "$PY" build_resumes.py --job "$JOB_FILE" "${BUILD_FLAGS[@]}"
  echo
else
  echo "▶ Skipping resume step (--cover-only)."
  echo
fi

# ── Step 2: Cover letter ─────────────────────────────────────────────────
echo "▶ Step 2/2: Building tailored cover letter..."
if [[ $SKIP_LLM -eq 1 ]]; then
  "$PY" cover_letter.py --job "$JOB_FILE" --skip-llm
else
  "$PY" cover_letter.py --job "$JOB_FILE"
fi
echo

# ── Summary ──────────────────────────────────────────────────────────────
echo "=========================================="
echo " Done. Outputs for '$JOB_STEM':"
echo "=========================================="
ls -lh outputs/ 2>/dev/null | grep -i "$JOB_STEM" || echo "(no matching files in outputs/)"
echo
echo "Tailored resume:  outputs/${JOB_STEM}_tailored*.pdf"
echo "Cover letter:     outputs/${JOB_STEM}_cover*.pdf"