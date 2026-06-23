# AGENTS.md — Automated AI Resume Pipeline

This file provides necessary context for AI coding agents to maintain, debug, and execute this project.

## Project Overview

Automated pipeline: Google Sheet CV → structured JSON → LLM-tailored → RenderCV YAML → ATS-compatible PDF.

- **Data source:** Google Sheet `src_cv_data` (ID `1D_MQxoi1XXoQLWSI3mH1Qo6tkctnENAij0-d3BEohu0`) with 4 sheets (entries, language_skills, text_blocks, contact_info)
- **PDF engine:** RenderCV 2.8 (Typst-based, ATS-friendly, 10+ themes)
- **LLM:** `nvidia/nemotron-3-ultra-550b-a55b` via OpenAI-compatible endpoint (`https://integrate.api.nvidia.com/v1`)
- **Proxy:** Corporate proxy at `http://ruizcase:Decent9-shrink9@proxy.gsdnet.ch:8080`

## Session Summary (2026-06-18)

### Goal
Build an automated pipeline to convert Google Sheet CV data into an ATS-compatible 2-page PDF with LLM tailoring to specific job roles.

### What Changed (Major Refactor)
- **Removed** `create_resume.py` — custom fpdf2 PDF generation (replaced by RenderCV)
- **Added** `convert_to_rendercv.py` — converts `career_full.json` → RenderCV YAML (fallback for `--skip-llm`)
- **Rewrote** `build_resumes.py` — LLM prompt now outputs RenderCV YAML; step 5 calls `rendercv render` via subprocess
- **Updated** AGENTS.md to reflect new architecture

### Architecture

```
download_data.py → data/career_data.xlsx
                          ↓
extract_sheets.py  → data/career_full.json
    (or inline in build_resumes.py)         ↘
                                              build_resumes.py
extract_job.py     → data/job_description.json   ↗
                                              │
                                    ┌─────────┴──────────┐
                                    ▼                    ▼
                              LLM tailors         convert_to_rendercv.py
                              (RenderCV YAML)     (fallback YAML)
                                    │                    │
                                    └────────┬───────────┘
                                             ▼
                                       rendercv render
                                             │
                                             ▼
                                     outputs/*_tailored.pdf
```

### Known Issues
- **SSL/Proxy:** httpx cannot reach `docs.google.com` through corporate proxy (SSL handshake timeout). Excel download uses urllib with unverified SSL context.
- **LLM API:** Works when proxy is configured explicitly via `httpx.Client(proxy=proxy_url, verify=False)`
- **httpx v0.28.1:** Use `proxy=` parameter (singular), not `proxies=` dict
- **RenderCV on Windows:** Terminal encoding crashes in Rich library — use `TERM=dumb` and `PYTHONIOENCODING=utf-8` in subprocess env
- **LLM inference:** Slow on NVIDIA endpoint (may need timeout > 180s)

### Testing Status
- ✅ `extract_sheets.py` — reads xlsx, writes json
- ✅ `extract_job.py` — reads PDF/TXT, extracts text
- ✅ `convert_to_rendercv.py` — career JSON → valid RenderCV YAML
- ✅ `build_resumes.py --skip-llm` — end-to-end fallback path (verified, 113 KB PDF)
- ⏳ `build_resumes.py` full pipeline with LLM — depends on inference time

## Environment & Stack

- **Runtime:** Python 3.12+ (RenderCV 2.8 requirement)
- **Venv:** `venv/` with rendercv[full], openpyxl, pandas, pdfplumber, openai, httpx, certifi
- **API keys:** `NVIDIA_API_KEY` (set in environment, not `.env`)
- **PDF engine:** RenderCV (Typst-based, bundles its own Typst binary)
- **YAML library:** `ruamel.yaml` (installed as RenderCV dependency)

## Directory Structure

```text
./
 AGENTS.md
 build_resumes.py            # Combined 5-step pipeline
 convert_to_rendercv.py      # career JSON → RenderCV YAML
 download_data.py            # Excel download (urllib)
 extract_sheets.py           # Excel → career JSON
 extract_job.py              # PDF/TXT → job JSON
 data/                       # career_data.xlsx, career_full.json, job_description.json, *cv.yaml
 outputs/                    # Generated PDFs
 job_roles/                  # Job description files (.pdf, .txt)
 venv/                       # Python virtual env
 docs/                       # GitHub Pages website (legacy)
```

## Usage

```bash
# Full pipeline (uses cached Excel, runs LLM)
python build_resumes.py --job rwe_data_analyst_sanofi.pdf

# Skip LLM (quick test, uses fallback converter → RenderCV)
python build_resumes.py --job rwe_data_analyst_sanofi.pdf --skip-llm

# Force re-download Excel
python build_resumes.py --job rwe_data_analyst_sanofi.pdf --refresh

# Custom output path
python build_resumes.py --job rwe_data_analyst_sanofi.pdf --output my_resume.pdf

# Convert career data to RenderCV YAML standalone
python -c "from convert_to_rendercv import build_rendercv_yaml; import json; d=json.load(open('data/career_full.json')); ..."
```

## RenderCV Entry Type Mapping

| Our Section | RenderCV Entry Type | Fields |
|-------------|--------------------|--------|
| summary | TextEntry | Plain text string |
| experience | ExperienceEntry | company, position, start/end_date, location, highlights |
| teaching | ExperienceEntry | company, position, start/end_date, location, highlights |
| education | EducationEntry | institution, area, degree, start/end_date, location, highlights |
| publications | PublicationEntry | title, authors, journal, date |
| presentations | NormalEntry | name, date, highlights |
| skills | OneLineEntry | label, details |
| projects | NormalEntry | name, date, highlights |

## Next Steps

1. Run full LLM pipeline to completion (inference is slow on NVIDIA endpoint)
2. Test with additional job descriptions
3. Verify 2-page A4 constraint on tailored RenderCV output
4. Consider switching to a faster/cheaper LLM model
