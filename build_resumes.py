#!/usr/bin/env python3
"""
build_resumes.py — Automated AI Resume Builder

Pipeline:
  1. Download Google Sheet Excel → data/career_data.xlsx
  2. Extract sheets → data/career_full.json
  3. Extract job description → data/job_description.json
  4. Generate raw RenderCV YAML (fallback converter), then optionally
      tailor via LLM → optimized RenderCV YAML
  5. Render YAML → PDF via RenderCV (Typst-based, ATS-compliant)

Usage:
  python build_resumes.py --job job_roles/rwe_data_analyst_sanofi.pdf
  python build_resumes.py --job job_roles/rwe_data_analyst_sanofi.pdf --skip-llm
"""

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request
import warnings
from pathlib import Path

import certifi
import httpx
import pandas as pd
from dotenv import load_dotenv

from convert_to_rendercv import build_rendercv_yaml

# ── Constants ──────────────────────────────────────────────────────────

FALLBACK_SHEET_ID = "1D_MQxoi1XXoQLWSI3mH1Qo6tkctnENAij0-d3BEohu0"

def get_sheet_id() -> str:
    return os.environ.get("GOOGLE_SHEETS_ID", FALLBACK_SHEET_ID)

# ── LLM Providers ──────────────────────────────────────────────────────

OLLAMA_BASE_URL = "https://ollama.com/api/chat"
OLLAMA_MODEL = "gemma4:31b"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "llama-3.3-70b-versatile"

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL = "gemini-2.5-flash"

# Active LLM — change these to swap providers
LLM_BASE_URL = OLLAMA_BASE_URL
LLM_MODEL = OLLAMA_MODEL
LLM_API_KEY_VAR = ""
LLM_PROVIDER = "ollama"

# Fallback (used by _call_chat_with_fallback if the active provider fails)
OLLAMA_FALLBACK_URL = OLLAMA_BASE_URL
OLLAMA_FALLBACK_MODEL = OLLAMA_MODEL
OLLAMA_FALLBACK_PROVIDER = "ollama"
OLLAMA_FALLBACK_API_KEY_VAR = ""

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
JOBS_DIR = BASE_DIR / "job_roles"
OUTPUTS_DIR = BASE_DIR / "outputs"

DATA_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

warnings.filterwarnings("ignore", category=DeprecationWarning)

# ── Helpers ─────────────────────────────────────────────────────────────


def make_ssl_http_client(no_proxy=False):
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    timeout = httpx.Timeout(180.0, connect=60.0)
    kwargs = {"verify": ctx, "timeout": timeout}
    if not no_proxy:
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy_url:
            kwargs["proxy"] = proxy_url
    return httpx.Client(**kwargs)


# ── Step 1: Download Excel ──────────────────────────────────────────────


def download_excel():
    dst = DATA_DIR / "career_data.xlsx"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy_url:
        proxy_handler = urllib.request.ProxyHandler({
            "http": proxy_url, "https": proxy_url,
        })
        opener = urllib.request.build_opener(proxy_handler)
        urllib.request.install_opener(opener)
    sheet_id = get_sheet_id()
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
        dst.write_bytes(resp.read())
    print(f"  Downloaded: {dst} ({dst.stat().st_size / 1024:.0f} KB)")


# ── Step 2: Extract Sheets → career_full.json ──────────────────────────


def extract_sheets(xlsx_path: str) -> dict:
    xls = pd.ExcelFile(xlsx_path)

    def read(name):
        df = pd.read_excel(xls, name, header=0)
        df.columns = [str(c).strip() for c in df.columns]
        return df.dropna(subset=[df.columns[0]], how="all")

    entries_df = read("entries")
    skills_df = read("language_skills")
    text_df = read("text_blocks")
    contact_df = read("contact_info")

    career = {"entries": [], "skills": [], "text_blocks": {}, "contact": {}}

    for _, row in entries_df.iterrows():
        entry = {
            "section": str(row.get("section", "") or "").strip(),
            "title": str(row.get("title", "") or "").strip(),
            "location": str(row.get("loc", "") or "").strip(),
            "institution": str(row.get("institution", "") or "").strip(),
            "start": str(row.get("start", "") or "").strip(),
            "end": str(row.get("end", "") or "").strip(),
            "bullets": [],
        }
        for col in entries_df.columns:
            if col.startswith("description") and pd.notna(row[col]):
                b = str(row[col]).strip()
                if b:
                    entry["bullets"].append(b)
        career["entries"].append(entry)

    cols = list(skills_df.columns)
    if len(cols) >= 2:
        for _, row in skills_df.iterrows():
            name = str(row[cols[0]] or "").strip()
            level = str(row[cols[1]] or "").strip()
            if name:
                career["skills"].append({"name": name, "level": level})

    for _, row in text_df.iterrows():
        loc = str(row.get("loc", "") or "").strip()
        text = str(row.get("text", "") or "").strip()
        if loc and text:
            career["text_blocks"][loc] = text

    for _, row in contact_df.iterrows():
        loc = str(row.get("loc", "") or "").strip()
        val = str(row.get("contact", "") or "").strip()
        if loc and val:
            career["contact"][loc] = val

    return career


# ── Step 3: Extract Job Description ─────────────────────────────────────


def extract_job(filepath: str) -> dict:
    path = Path(filepath)
    ext = path.suffix.lower()
    if ext == ".txt":
        text = path.read_text(encoding="utf-8", errors="replace")
    elif ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        text = "\n".join(pages)
    else:
        print(f"Unsupported format: {ext}")
        sys.exit(1)

    return {
        "source": str(path),
        "filename": path.name,
        "job_name": path.stem,
        "description": text.strip(),
    }


# ── Step 4: LLM Tailoring ───────────────────────────────────────────────


LLM_SYSTEM_PROMPT = """You are an expert ATS resume writer. Tailor a candidate's career history to a job description, producing a JSON data structure for a 2-page A4 RenderCV PDF.

RULES:
1. Output ONLY valid JSON. No markdown code fences, no explanations, no trailing commas.
2. Select and re-rank the most relevant experiences for the target job (max 6 positions total: main_positions must be included, research_visits are optional).
3. ALWAYS include the candidate's most recent main_position (by start_date). It must be in the experience array.
4. Rewrite bullet points to use keywords from the job description (keep truthful).
5. Summary: write exactly 500-600 characters tailored to the role (as a string array with 1 item).
6. Skills: 5 categories max, 1 line per category, single-concept labels (e.g. "RWE Analytics" not "RWE & Clinical Analytics"). No '&' in labels. Let the LLM decide how many skills per category.
7. Keep total content to 2 A4 pages.
8. Use null for missing fields, empty arrays for empty highlights.
9. Experience: max 2 bullet points per entry, each bullet max 2 lines (~200 chars). The company field is the organization name; position is the job title. Bold exactly 2 keywords in each bullet using **double asterisks**.
10. Output sections in this order: summary, skills, experience, education, then optionally publications and presentations. Only include sections that add value.
11. Include a "design" section in the JSON with theme "classic" and show_time_spans_in set to empty.
12. Create a "publications" section after education with up to 6 key publications. Use PublicationEntry format: title, authors (array of strings), journal, date. Each entry must have title, authors array (e.g. ["Author1", "Author2"]), journal, and year-only date (e.g. "2020").
13. Create a "presentations" section after publications with up to 4 key presentations and any patent. For the patent use the exact title "Compounds for use in the treatment of hyperproliferative disorders". Use NormalEntry format: name + date + 1-2 highlight lines each.
14. Education: preserve ALL highlights for each entry — especially awards, honors, distinctions (cum laude, thesis prizes, etc.). Do not truncate education bullets.

The JSON must follow this structure:
{
  "cv": {
    "name": "Sergio Ruiz Carmona",
    "location": "{LOCATION}",
    "email": "{EMAIL}",
    "phone": null,
    "website": "{WEBSITE}",
    "social_networks": [
      {"network": "LinkedIn", "username": "ruizcsergio"},
      {"network": "GitHub", "username": "sruizcarmona"}
    ],
    "sections": {
      "summary": ["Professional summary text here..."],
      "skills": [
        {"label": "Single-concept label", "details": "Comma-separated skills"}
      ],
      "experience": [
        {
          "company": "Organization Name",
          "position": "Job Title",
          "start_date": "YYYY-MM",
          "end_date": "YYYY-MM or present",
          "location": "City, Country",
          "highlights": ["Concise bullet with **bold** keywords (max 2 lines)"]
        }
      ],
      "education": [
        {
          "institution": "University Name",
          "area": "Field of Study",
          "degree": "Degree Type",
          "start_date": "YYYY-MM",
          "end_date": "YYYY-MM",
          "location": "City, Country",
          "highlights": ["Summary sentence about PhD research relevant to role"]
        }
      ],
      "publications": [
        {
          "title": "Full Publication Title",
          "authors": ["Author1", "Author2", "Sergio Ruiz Carmona", "Author4"],
          "journal": "Journal Name",
          "date": "2023"
        }
      ],
      "presentations": [
        {
          "name": "Presentation or Patent Title",
          "date": "2023",
          "highlights": ["Brief one-line relevance to role"]
        }
      ]
    }
  },
  "design": {
    "theme": "classic",
    "sections": {
      "show_time_spans_in": []
    }
  }
}

Only include publications and presentations if the items are genuinely relevant to the role. Do not pad with irrelevant academic items."""

FILTER_PROMPT = """You are an expert ATS resume reviewer. Given a JSON resume for a non-academic industry RWE Data Analyst role, decide which section names to KEEP.

Return ONLY a JSON array of section names to keep. No explanations, no markdown.

Rules:
- summary: KEEP always (essential)
- skills: KEEP always (essential)
- experience: KEEP always (essential)
- education: KEEP always (essential)
- All other sections (e.g. publications, presentations, teaching, projects): KEEP if they add value. publications and presentations are particularly useful for filling page 2. Be selective but prefer keeping relevant content over cutting too aggressively.

Example output: ["summary", "skills", "experience", "education"]
Example with outreach: ["summary", "skills", "experience", "education", "publications", "presentations"]"""

# Keep only the YAML/JSON content (strip markdown fences if LLM adds them)
YAML_FENCE_PATTERN = re.compile(r"^```(?:yaml|json)?\s*\n(.*?)\n\s*```", re.DOTALL)


def _normalize_present(data):
    """Recursively normalize 'Present' → 'present' in end_date fields."""
    if isinstance(data, dict):
        for k, v in list(data.items()):
            if k in ("end_date",) and isinstance(v, str) and v.lower() == "present":
                data[k] = "present"
            else:
                _normalize_present(v)
    elif isinstance(data, list):
        for item in data:
            _normalize_present(item)


def _ensure_essential_entries(data: dict, career_json: dict):
    """Guardrails: ensure most recent experience and at least 1 education entry."""
    sections = data.get("cv", {}).get("sections", {})
    raw_entries = career_json.get("entries", [])
    from datetime import datetime

    def _parse_date(d):
        try:
            return datetime.strptime(d.strip(), "%b %Y")
        except (ValueError, AttributeError):
            return datetime.min

    # --- Guardrail 1: Most recent experience ---
    # Find all main_positions from raw data (always include)
    research = [e for e in raw_entries if e.get("section") == "main_positions"]
    # Sort by start_date descending; entries with "Present" end_date rank first
    research.sort(key=lambda e: (
        0 if str(e.get("end", "")).lower() in ("present", "") else 1,
        _parse_date(e.get("start", "")),
    ), reverse=True)

    if research:
        most_recent_raw = research[0]
        title = most_recent_raw.get("title", "").strip()
        company = most_recent_raw.get("institution", "").strip()
        location = most_recent_raw.get("location", "").strip()
        start = most_recent_raw.get("start", "").strip()
        end = most_recent_raw.get("end", "").strip()

        # Check if this title already exists in experience
        existing = sections.get("experience", [])
        if not any(e.get("position") == title for e in existing):
            print(f"  GUARDRAIL: Adding missing recent role '{title}'")
            # Parse dates to YYYY-MM format
            def _to_ym(d):
                try:
                    return datetime.strptime(d.strip(), "%b %Y").strftime("%Y-%m")
                except (ValueError, AttributeError):
                    return d
            entry = {
                "company": company or location,
                "position": title,
                "start_date": _to_ym(start),
                "end_date": _to_ym(end) if end.lower() != "present" else "present",
                "location": location,
                "highlights": most_recent_raw.get("bullets", []),
            }
            existing.insert(0, entry)

    # --- Guardrail 2: At least 1 education entry ---
    education = sections.get("education", [])
    if not education:
        edu_raw = [e for e in raw_entries if e.get("section") == "education"]
        if edu_raw:
            first = edu_raw[0]
            print(f"  GUARDRAIL: Adding missing education entry")
            sections["education"] = [{
                "institution": first.get("institution", ""),
                "area": first.get("title", ""),
                "degree": "",
                "start_date": first.get("start", ""),
                "end_date": "present" if first.get("end", "").lower() == "present" else first.get("end", ""),
                "location": first.get("location", ""),
                "highlights": first.get("bullets", []),
            }]


def _start_heartbeat(label: str, prompt_chars: int) -> tuple:
    """Start a daemon thread that prints a heartbeat every 30s.

    Returns (stop_event, thread). Caller MUST call stop_event.set() to stop it.
    """
    import threading
    import time

    stop = threading.Event()
    start = time.time()

    def _run():
        while not stop.wait(30):
            elapsed = int(time.time() - start)
            print(f"  ...{label} still generating ({elapsed}s elapsed, ~{prompt_chars//1000}k prompt chars)")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return stop, t


def _call_chat_with_fallback(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_tokens: int,
    timeout: int = 300,
) -> tuple:
    """Call the Ollama Cloud API directly via HTTP."""
    import httpx
    import os

    api_key = os.environ.get("OLLAMA_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    
    prompt_chars = len(system_prompt) + len(user_prompt)
    print(f"  Calling LLM ({LLM_PROVIDER}/{LLM_MODEL}, prompt~{prompt_chars//1000}k chars, max_tokens={max_tokens})...")

    def _run_request():
        # Use the existing helper to handle SSL and Proxy settings
        with make_ssl_http_client() as client:
            payload = {
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
                "stream": False,
            }
            resp = client.post(LLM_BASE_URL, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()

    try:
        result = _run_request()
        content = result["message"]["content"].strip()
        print(f"  OK from {LLM_PROVIDER}")
        return content, LLM_PROVIDER
    except Exception as e:
        print(f"  {LLM_PROVIDER} failed: {e}")
        raise e



def call_llm(career_json: dict, job_data: dict) -> str:
    from convert_to_rendercv import extract_url
    contact = career_json.get("contact", {})
    location = contact.get("location", "")
    email = contact.get("email", "")
    website = extract_url(contact.get("website", ""))

    system_prompt = (
        LLM_SYSTEM_PROMPT
        .replace("{LOCATION}", location or "")
        .replace("{EMAIL}", email or "")
        .replace("{WEBSITE}", website or "")
    )

    # Send compact JSON, omit text_blocks (pre-written snippets) and
    # contact (already injected into system prompt via {LOCATION} etc.)
    career_for_llm = {k: v for k, v in career_json.items()
                      if k not in ("text_blocks", "contact")}
    career_text = json.dumps(career_for_llm, separators=(",", ":"))
    job_text = job_data.get("description", "")
    job_title = job_data.get("job_name", "")

    user_prompt = f"""Target job: {job_title}

Full career history (JSON):
{career_text}

Target job description:
{job_text}

Generate the RenderCV YAML resume tailored for this role."""

    content, _provider = _call_chat_with_fallback(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.3,
        max_tokens=8192,
        timeout=300,
    )

    # Strip markdown fences if present
    m = YAML_FENCE_PATTERN.search(content)
    if m:
        content = m.group(1).strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("\n", 1)[0].strip()

    # Parse JSON and convert to proper YAML
    # Try to find JSON object in the response
    import re as _re
    json_match = _re.search(r'\{.*\}', content, _re.DOTALL)
    if json_match:
        json_str = json_match.group()
    else:
        json_str = content

    # Repair common JSON issues from LLM output
    def _repair_json(s: str) -> str:
        """Fix trailing commas and unbalanced brackets."""
        s = _re.sub(r',\s*([}\]])', r'\1', s)
        opens = s.count('{') + s.count('[')
        closes = s.count('}') + s.count(']')
        if opens > closes:
            s += ']' * (opens - closes) + '}' * (opens - closes)
        return s

    try:
        data = json.loads(_repair_json(json_str))
        # Normalize "Present" → "present" in all end_date fields
        _normalize_present(data)
        # Guardrails: ensure most recent experience and education
        _ensure_essential_entries(data, career_json)
    except json.JSONDecodeError as e:
        print(f"  WARNING: LLM returned invalid JSON ({e}). Using raw response as-is.")
        return content

    # Convert JSON to proper YAML using ruamel.yaml
    from io import StringIO
    from ruamel.yaml import YAML as YAMLDumper
    yaml_dumper = YAMLDumper()
    yaml_dumper.indent(mapping=2, sequence=4, offset=2)
    yaml_dumper.width = 120
    buf = StringIO()
    yaml_dumper.dump(data, buf)
    return buf.getvalue()


def call_llm_filter(yaml_text: str, job_data: dict) -> str:
    """Second LLM pass: decide which sections to keep, filter programmatically."""
    job_title = job_data.get("job_name", "")

    user_prompt = f"""Target role: {job_title}

Resume JSON sections: summary, skills, experience, education, publications, presentations, teaching, projects

Which sections should be kept for this non-academic industry role?"""

    print("  Reviewing section relevance with LLM...")
    try:
        content, _provider = _call_chat_with_fallback(
            system_prompt=FILTER_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=1024,
            timeout=60,
        )
    except Exception as e:
        print(f"  Filter failed: {e}. Using pass-1 output as-is.")
        return yaml_text

    import re as _re
    # Extract JSON array from response
    arr_match = _re.search(r'\[.*?\]', content, _re.DOTALL)
    if not arr_match:
        print("  WARNING: Could not parse filter response. Using original.")
        return yaml_text

    try:
        keep_sections = json.loads(arr_match.group())
        if not isinstance(keep_sections, list):
            return yaml_text
    except json.JSONDecodeError:
        print("  WARNING: Filter returned invalid JSON. Using original.")
        return yaml_text

    # Parse the pass-1 YAML back to JSON/dict, filter sections, re-serialize
    from io import StringIO
    from ruamel.yaml import YAML as YAMLDumper
    import json as _json
    data = None
    # Try JSON first (pass1 may be raw JSON if call_llm parsing failed)
    try:
        data = _json.loads(yaml_text)
    except _json.JSONDecodeError:
        try:
            yaml_parser = YAMLDumper()
            data = yaml_parser.load(yaml_text)
        except Exception:
            print("  WARNING: Could not parse pass-1 YAML/JSON. Using original.")
            return yaml_text

    if "cv" in data and "sections" in data["cv"]:
        all_sections = data["cv"]["sections"]
        removed = [k for k in all_sections if k not in keep_sections]
        if removed:
            print(f"  Removed sections: {', '.join(removed)}")
        data["cv"]["sections"] = {k: v for k, v in all_sections.items() if k in keep_sections}

    yaml_dumper = YAMLDumper()
    yaml_dumper.indent(mapping=2, sequence=4, offset=2)
    yaml_dumper.width = 120
    buf = StringIO()
    yaml_dumper.dump(data, buf)
    return buf.getvalue()


# ── Step 5: Render PDF via RenderCV ─────────────────────────────────────


def render_pdf(yaml_path: Path, output_pdf: Path):
    """Call `rendercv render` via subprocess to generate PDF."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["TERM"] = "dumb"

    # Remove old output and stale RenderCV files so we can detect the new one
    if output_pdf.exists():
        try:
            output_pdf.unlink()
        except PermissionError:
            # File may be held by pdfplumber from balance check on Windows
            pass
    for old in OUTPUTS_DIR.glob("*_CV.pdf"):
        try:
            old.unlink()
        except OSError:
            pass

    cmd = [
        sys.executable, "-m", "rendercv", "render",
        str(yaml_path),
        "--output-folder", str(OUTPUTS_DIR),
        "--dont-generate-markdown",
        "--dont-generate-html",
        "--dont-generate-png",
        "--quiet",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=BASE_DIR,
            env=env,
        )
    except subprocess.TimeoutExpired:
        print("  RenderCV timed out")
        sys.exit(1)

    # RenderCV puts output in OUTPUTS_DIR/NAME_IN_SNAKE_CASE_CV.pdf
    candidates = sorted(OUTPUTS_DIR.glob("*_CV.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    generated = candidates[0] if candidates else None

    if generated and generated.exists():
        import shutil
        try:
            shutil.copy2(str(generated), str(output_pdf))
            print(f"  PDF generated: {output_pdf} ({output_pdf.stat().st_size / 1024:.0f} KB)")
        except Exception:
            print(f"  PDF generated: {generated}")
            print(f"  Copy manually: {generated} -> {output_pdf}")
    else:
        print(f"  RenderCV output not found in {OUTPUTS_DIR}")
        print(f"  Stderr: {result.stderr[:500]}")
        sys.exit(1)


def _rebalance_sections(yaml_text: str, career_json: dict) -> str:
    """Add teaching section from raw data if page 2 is too sparse."""
    from io import StringIO
    from ruamel.yaml import YAML as YAMLDumper, YAMLError

    try:
        yaml_parser = YAMLDumper(typ="safe")
        data = yaml_parser.load(yaml_text)
    except YAMLError:
        return yaml_text

    sections = data.get("cv", {}).get("sections", {})
    raw_entries = career_json.get("entries", [])
    from datetime import datetime

    def _to_ym(d):
        try:
            d = d.strip()
            if d.lower() in ("nan", "n/a", "", "none"):
                return ""
            return datetime.strptime(d, "%b %Y").strftime("%Y-%m")
        except (ValueError, AttributeError):
            return str(d).strip() if d else ""

    # Add teaching section if not present
    if "teaching" not in sections:
        teaching_raw = [e for e in raw_entries if e.get("section") == "teaching_positions"]
        if teaching_raw:
            teaching = []
            for e in teaching_raw:
                sd = _to_ym(e.get("start", ""))
                ed = "present" if str(e.get("end", "")).lower() == "present" else _to_ym(e.get("end", ""))
                if not sd:
                    continue  # skip entries without valid start date
                entry = {
                    "company": e.get("institution", ""),
                    "position": e.get("title", ""),
                    "start_date": sd,
                    "end_date": ed,
                    "location": e.get("location", ""),
                    "highlights": e.get("bullets", []),
                }
                teaching.append(entry)
            sections["teaching"] = teaching
            print(f"  REBALANCE: Added {len(teaching)} teaching entries to fill page 2")

    # Also add more education entries if only 1 present
    education = sections.get("education", [])
    if len(education) <= 1:
        edu_raw = [e for e in raw_entries if e.get("section") == "education"]
        for e in edu_raw:
            inst = e.get("institution", "")
            if not any(ed.get("institution") == inst for ed in education):
                education.append({
                    "institution": inst,
                    "area": e.get("title", ""),
                    "degree": "",
                    "start_date": _to_ym(e.get("start", "")),
                    "end_date": "present" if str(e.get("end", "")).lower() == "present" else _to_ym(e.get("end", "")),
                    "location": e.get("location", ""),
                    "highlights": e.get("bullets", []),
                })
                print(f"  REBALANCE: Added education entry '{inst}'")

    # Re-serialize
    yaml_dumper = YAMLDumper()
    yaml_dumper.indent(mapping=2, sequence=4, offset=2)
    yaml_dumper.width = 120
    buf = StringIO()
    yaml_dumper.dump(data, buf)
    return buf.getvalue()


# ── Main ────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Build tailored ATS-compatible resume PDF via RenderCV"
    )
    parser.add_argument("--job", "-j",
                        help="Job description file (PDF or TXT)")
    parser.add_argument("--from-yaml", "-y",
                        help="Skip data pipeline, render directly from existing YAML file")
    parser.add_argument("--output", "-o", default="",
                        help="Output PDF path")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip LLM, use fallback converter")
    parser.add_argument("--refresh", action="store_true",
                        help="Force re-download of Excel data")
    args = parser.parse_args()

    # Load .env
    dotenv_path = BASE_DIR / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path)

    # ── --from-yaml mode: skip data pipeline, render YAML directly ───────
    if args.from_yaml:
        yaml_path = Path(args.from_yaml)
        if not yaml_path.is_absolute():
            candidate = DATA_DIR / yaml_path
            if candidate.exists():
                yaml_path = candidate
        if not yaml_path.exists():
            print(f"YAML file not found: {yaml_path}")
            sys.exit(1)
        job_name = yaml_path.stem
        output_path = Path(args.output or str(OUTPUTS_DIR / f"{job_name}_rendered.pdf"))
        if not args.output:
            from datetime import datetime
            ts = datetime.now().strftime("_%Y-%m-%d_%H%M")
            output_path = OUTPUTS_DIR / f"{job_name}_rendered{ts}.pdf"
        print(f"YAML: {yaml_path}")
        print(f"Output: {output_path}")
        print("\nStep 5/5: Rendering PDF via RenderCV...")
        render_pdf(yaml_path, output_path)
        print(f"\nDone! PDF: {output_path}")
        return

    if not args.job:
        parser.error("--job is required (or use --from-yaml to render an existing YAML)")

    # Resolve job path
    job_path = Path(args.job)
    if not job_path.is_absolute():
        candidate = JOBS_DIR / job_path
        if candidate.exists():
            job_path = candidate
    if not job_path.exists():
        print(f"Job file not found: {job_path}")
        sys.exit(1)

    job_name = job_path.stem
    output_path = Path(args.output or str(OUTPUTS_DIR / f"{job_name}_tailored.pdf"))
    from datetime import datetime
    ts = datetime.now().strftime("_%Y-%m-%d_%H%M")
    if not args.output:
        provider_slug = f"_{LLM_PROVIDER}" if not args.skip_llm else ""
        output_path = OUTPUTS_DIR / f"{job_name}_tailored{provider_slug}{ts}.pdf"
    print(f"Job: {job_path}")
    print(f"Output: {output_path}")

    # Step 1: Download Excel
    xlsx_path = DATA_DIR / "career_data.xlsx"
    if args.refresh or not xlsx_path.exists():
        print("\nStep 1/5: Downloading Google Sheet...")
        download_excel()
    else:
        print(f"\nStep 1/5: Using cached {xlsx_path}")

    # Step 2: Extract sheets
    print("Step 2/5: Extracting sheet data...")
    career = extract_sheets(str(xlsx_path))
    career_dst = DATA_DIR / "career_full.json"
    with open(career_dst, "w", encoding="utf-8") as f:
        json.dump(career, f, indent=2, ensure_ascii=False)
    print(f"  {len(career['entries'])} entries, {len(career['skills'])} skills")

    # Step 3: Extract job
    print("Step 3/5: Extracting job description...")
    job_data = extract_job(str(job_path))
    job_dst = DATA_DIR / "job_description.json"
    with open(job_dst, "w", encoding="utf-8") as f:
        json.dump(job_data, f, indent=2, ensure_ascii=False)
    print(f"  {len(job_data['description'])} chars")

    # Step 4: Generate YAML (always build raw, optionally optimize with LLM)
    print("Step 4/5: Generating RenderCV YAML...")
    raw_data = build_rendercv_yaml(career)
    from io import StringIO
    from ruamel.yaml import YAML as YAMLDumper
    yaml_dumper = YAMLDumper()
    yaml_dumper.indent(mapping=2, sequence=4, offset=2)
    yaml_dumper.width = 120
    buf = StringIO()
    yaml_dumper.dump(raw_data, buf)
    raw_yaml = buf.getvalue()

    raw_path = DATA_DIR / f"{job_name}_raw.yaml"
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(raw_yaml)
    print(f"  Raw YAML: {raw_path}")
    print("  " + "-" * 60)
    print(raw_yaml[:2000] + ("\n  ... (truncated)" if len(raw_yaml) > 2000 else ""))
    print("  " + "-" * 60)

    if args.skip_llm:
        yaml_text = raw_yaml
        yaml_path = raw_path
    else:
        print("\n  Pass 1/2: Tailoring content with LLM...")
        yaml_text = call_llm(career, job_data)
        pass1_path = DATA_DIR / f"{job_name}_pass1.yaml"
        with open(pass1_path, "w", encoding="utf-8") as f:
            f.write(yaml_text)
        print(f"  Pass 1 saved: {pass1_path}")
        print("  " + "-" * 60)
        print(yaml_text[:2000] + ("\n  ... (truncated)" if len(yaml_text) > 2000 else ""))
        print("  " + "-" * 60)

        print("\n  Pass 2/2: Filtering sections for relevance...")
        yaml_text = call_llm_filter(yaml_text, job_data)
        yaml_path = DATA_DIR / f"{job_name}_optimized.yaml"
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(yaml_text)
        print(f"  Optimized YAML: {yaml_path}")
        print("  " + "-" * 60)
        print(yaml_text[:2000] + ("\n  ... (truncated)" if len(yaml_text) > 2000 else ""))
        print("  " + "-" * 60)

    # Step 5: Render PDF (with page balancing)
    print("Step 5/5: Rendering PDF via RenderCV...")
    render_pdf(yaml_path, output_path)

    if not args.skip_llm:
        try:
            import pdfplumber
            import time
            needs_rebalance = False
            with pdfplumber.open(output_path) as pdf:
                if len(pdf.pages) == 1:
                    needs_rebalance = True
                elif len(pdf.pages) == 2:
                    p1_chars = len(pdf.pages[0].extract_text() or "")
                    p2_chars = len(pdf.pages[1].extract_text() or "")
                    if p2_chars > 0 and p2_chars < p1_chars * 0.8:
                        needs_rebalance = True
            if needs_rebalance:
                time.sleep(0.5)  # let Windows release file lock
                print(f"  Content too compact ({len(pdf.pages)} page(s)), rebalancing...")
                new_yaml = _rebalance_sections(yaml_text, career)
                with open(yaml_path, "w", encoding="utf-8") as f:
                    f.write(new_yaml)
                print("  Re-rendering with balanced content...")
                render_pdf(yaml_path, output_path)
        except ImportError:
            pass

    print(f"\nDone! PDF: {output_path}")


if __name__ == "__main__":
    main()
