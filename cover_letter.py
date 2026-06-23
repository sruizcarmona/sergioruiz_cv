#!/usr/bin/env python3
"""
cover_letter.py — Generate a tailored 1-page ATS cover letter PDF.

Architecture: LLM produces plain text sections -> Typst template -> PDF.
This bypasses RenderCV's CV-only data model (which rejected our letter YAML
sections) and gives us clean control over letter formatting.

Pipeline:
  1. Load career_full.json + job_description.json (produced by build_resumes)
  2. Load the tailored resume YAML (data/<job>_optimized.yaml or _raw.yaml)
  3. LLM (or fallback template) -> {recipient, opening, body, closing, signature}
  4. Inject sections into a Typst template -> PDF via `typst.compile`

Usage:
  python cover_letter.py --job rwe_data_analyst_sanofi.pdf
  python cover_letter.py --job rwe_data_analyst_sanofi.pdf --skip-llm
  python cover_letter.py --job rwe_data_analyst_sanofi.pdf --resume-yaml data/<job>_optimized.yaml
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from build_resumes import (
    LLM_PROVIDER,
    BASE_DIR,
    DATA_DIR,
    JOBS_DIR,
    OUTPUTS_DIR,
)

# ── Cover letter LLM prompt ─────────────────────────────────────────────

COVER_LETTER_SYSTEM_PROMPT = """You are writing a professional cover letter. Keep it roughly 250 words.

Below are the candidate's CV summary and the job description. Output ONLY the cover letter text — no labels, no JSON, no markdown, no explanations."""


def _word_count(text: str) -> int:
    return len(text.split())


# ── Step 1: Load tailored resume YAML ────────────────────────────────────


def load_resume_yaml(job_name: str, explicit_path: str | None) -> tuple[dict, Path]:
    """Load the tailored resume YAML. Prefer _optimized.yaml, fall back to _raw.yaml."""
    if explicit_path:
        p = Path(explicit_path)
        if not p.is_absolute():
            p = DATA_DIR / p
    else:
        p = DATA_DIR / f"{job_name}_optimized.yaml"
        if not p.exists():
            p = DATA_DIR / f"{job_name}_raw.yaml"
        if not p.exists():
            p = DATA_DIR / f"{job_name}_pass1.yaml"
    if not p.exists():
        print(f"  Resume YAML not found: {p}")
        print("  Run build_resumes.py first (without --cover-only).")
        sys.exit(1)
    # The LLM-tailored resume YAML may contain bold-marker strings like
    # "*Sergio Ruiz-Carmona*" that YAML parsers mistake for alias refs.
    # Quote any unquoted scalar that begins with `*` to defuse the alias.
    raw = p.read_text(encoding="utf-8")
    raw = re.sub(
        r"(?<=[\s\-:])\*([^*\n]+)\*(?=\s*$|\s*[,\]\}])",
        r'"*\1*"',
        raw,
        flags=re.MULTILINE,
    )
    from ruamel.yaml import YAML
    yaml = YAML(typ="safe")
    try:
        data = yaml.load(raw)
    except Exception as e:
        print(f"  WARNING: ruamel YAML load failed ({e}); falling back to PyYAML.")
        import yaml as pyyaml
        data = pyyaml.safe_load(raw)
    return data, p


# ── Step 2: Generate cover letter sections ───────────────────────────────


def call_llm_cover(career: dict, job_data: dict, resume_yaml: dict) -> dict:
    from build_resumes import _call_chat_with_fallback

    career_text = json.dumps(career, separators=(",", ":"), ensure_ascii=False)
    job_text = job_data.get("description", "")

    user_prompt = f"""Job description:
{job_text}

CV:
{career_text}

Write a professional cover letter based on the job description above."""

    print("  Calling LLM for cover letter...")
    content, _provider = _call_chat_with_fallback(
        system_prompt=COVER_LETTER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.4,
        max_tokens=1500,
        timeout=300,
    )

    # Strip markdown fences if the LLM adds them despite instructions
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("\n", 1)[0].strip()

    return {"body": content}


def skip_llm_cover(career: dict, job_data: dict, resume_yaml: dict) -> dict:
    """Simple cover letter for --skip-llm: extract up to 3 resume bullet points as paragraphs."""
    bullets = []
    for entry in resume_yaml.get("cv", {}).get("sections", {}).get("experience", []):
        for h in entry.get("highlights", []) or []:
            clean = re.sub(r"\*\*(.+?)\*\*", r"\1", h)
            bullets.append(clean)
            if len(bullets) >= 3:
                break
        if len(bullets) >= 3:
            break
    body = "\n\n".join(bullets) if bullets else "Cover letter content not available."
    return {"body": body}


# ── Step 3: Build Typst template ─────────────────────────────────────────

# RenderCV classic theme tokens (kept in sync with the resume PDF):
#   font:        Source Sans 3 (sans-serif, bundled with rendercv)
#   body size:   10pt
#   name size:   18pt (cover letter header — smaller than CV's 30pt)
#   color:       rgb(0, 79, 144) deep blue for name / links
#   alignment:   justified
#   line spacing: 0.6em
#   margins:     0.7in (~1.78cm) all sides
#   paper:       a4

TYPST_TEMPLATE = r'''#set document(
  title: "Cover Letter",
  author: "Sergio Ruiz Carmona",
)

#set page(
  paper: "a4",
  margin: (x: 1.78cm, y: 1.78cm),
)

#set text(
  font: ("Source Sans 3", "Liberation Sans", "DejaVu Sans", "Arial"),
  size: 10pt,
  fill: rgb(0, 0, 0),
  lang: "en",
)

// Justified body alignment + line spacing (matches RenderCV classic theme).
#set par(
  justify: true,
  leading: 0.6em,
)

// Variables injected from cover_letter.py
#let contact-location = "__CONTACT_LOCATION__"
#let contact-email = "__CONTACT_EMAIL__"
#let contact-website = "__CONTACT_WEBSITE__"
#let contact-website-short = "__CONTACT_WEBSITE_SHORT__"
#let body = "===BODY==="

#let date-text = "__DATE__"

#let brand-color = rgb(0, 79, 144)

// ── Header ──
#align(center)[
  #text(size: 18pt, weight: "bold", fill: brand-color)[Sergio Ruiz Carmona]
]

#v(0.25cm)

#align(center)[
  #text(size: 9pt, fill: brand-color)[
    #contact-location #h(0.4em) #sym.dot.op #h(0.4em)
    #link("mailto:" + contact-email)[#contact-email] #h(0.4em) #sym.dot.op #h(0.4em)
    #link(contact-website)[#contact-website-short]
  ]
]

#v(0.6cm)

// ── Date ──
#align(right)[#text(size: 10pt)[#date-text]]

#v(0.4cm)

// ── Body ──
#text(size: 10pt)[#body]
'''


def _shorten_website(url: str) -> str:
    """Convert https://www.ruizsergio.com -> ruizsergio.com (also strips HTML)."""
    from convert_to_rendercv import strip_html, extract_url
    url = strip_html(url)
    # Try to find href inside <a> first, otherwise treat as raw URL
    extracted = extract_url(url)
    if extracted.startswith("http"):
        return re.sub(r"^https?://(www\.)?", "", extracted).rstrip("/")
    return extracted or url


def _typst_escape(s: str) -> str:
    """Escape a Python string for safe inclusion as a Typst string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def build_typst_source(letter: dict, contact: dict) -> str:
    from convert_to_rendercv import strip_html, extract_url
    today = datetime.now().strftime("%B %d, %Y")
    raw_website = contact.get("website", "")
    website_url = extract_url(raw_website) if "<a" in raw_website else raw_website
    website_url = website_url.strip()
    website_short = _shorten_website(raw_website)
    email = strip_html(contact.get("email", ""))
    location = strip_html(contact.get("location", ""))
    src = TYPST_TEMPLATE
    src = src.replace("__DATE__", _typst_escape(today))
    src = src.replace("===BODY===", _typst_escape(letter.get("body", "")))
    src = src.replace("__CONTACT_LOCATION__", _typst_escape(location))
    src = src.replace("__CONTACT_EMAIL__", _typst_escape(email))
    src = src.replace("__CONTACT_WEBSITE__", _typst_escape(website_url))
    src = src.replace("__CONTACT_WEBSITE_SHORT__", _typst_escape(website_short))
    return src


# ── Step 4: Render PDF via Typst ─────────────────────────────────────────


def render_letter_pdf(typ_path: Path, output_pdf: Path) -> None:
    """Compile Typst source to PDF using the typst Python package."""
    import typst

    if output_pdf.exists():
        try:
            output_pdf.unlink()
        except PermissionError:
            pass

    # Point Typst at the Source Sans 3 font bundled with rendercv_fonts so the
    # cover letter visually matches the RenderCV classic-theme resume.
    from importlib import resources
    font_paths = None
    try:
        fonts_pkg = resources.files("rendercv_fonts") / "Source Sans 3"
        if fonts_pkg.is_dir():
            font_paths = [str(fonts_pkg)]
    except (ModuleNotFoundError, FileNotFoundError):
        font_paths = None

    print(f"  Typst source: {typ_path}")
    try:
        compile_kwargs = {"font_paths": font_paths} if font_paths else {}
        typst.compile(str(typ_path), output=str(output_pdf), **compile_kwargs)
    except Exception as e:
        print(f"  Typst compile failed: {e}")
        sys.exit(1)

    if output_pdf.exists():
        print(f"  Cover letter PDF: {output_pdf} ({output_pdf.stat().st_size / 1024:.0f} KB)")
    else:
        print(f"  Cover letter PDF not generated.")
        sys.exit(1)


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Generate a tailored 1-page cover letter PDF."
    )
    parser.add_argument("--job", "-j", required=True, help="Job description file (PDF or TXT)")
    parser.add_argument("--resume-yaml", "-y", default=None,
                        help="Path to tailored resume YAML (default: data/<job>_optimized.yaml)")
    parser.add_argument("--output", "-o", default="", help="Output PDF path")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip LLM, use templated fallback")
    args = parser.parse_args()

    dotenv_path = BASE_DIR / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path)

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

    # Load career + job data
    career_path = DATA_DIR / "career_full.json"
    job_desc_path = DATA_DIR / "job_description.json"
    if not career_path.exists() or not job_desc_path.exists():
        print(f"Missing {career_path} or {job_desc_path}. Run build_resumes.py first.")
        sys.exit(1)
    career = json.loads(career_path.read_text(encoding="utf-8"))
    job_data = json.loads(job_desc_path.read_text(encoding="utf-8"))

    # Load tailored resume YAML
    resume_yaml, resume_yaml_path = load_resume_yaml(job_name, args.resume_yaml)
    print(f"Resume YAML: {resume_yaml_path}")

    # Output path
    if args.output:
        output_path = Path(args.output)
    else:
        ts = datetime.now().strftime("_%Y-%m-%d_%H%M")
        provider_slug = f"_{LLM_PROVIDER}" if not args.skip_llm else ""
        output_path = OUTPUTS_DIR / f"{job_name}_cover{provider_slug}{ts}.pdf"

    print(f"Output: {output_path}")

    # Step 1: Draft sections
    print("\nDrafting cover letter sections...")
    if args.skip_llm:
        letter = skip_llm_cover(career, job_data, resume_yaml)
    else:
        letter = call_llm_cover(career, job_data, resume_yaml)

    # Word-count info
    body = letter.get("body", "")
    wc = _word_count(body)
    print(f"  Word count: {wc}")

    # Step 2: Build Typst source
    print("Building Typst source...")
    contact = career.get("contact", {})
    typ_source = build_typst_source(letter, contact)
    typ_path = DATA_DIR / f"{job_name}_cover_letter.typ"
    typ_path.write_text(typ_source, encoding="utf-8")
    print(f"  Typst: {typ_path}")

    # Step 3: Render PDF
    print("\nRendering cover letter PDF via Typst...")
    render_letter_pdf(typ_path, output_path)
    print(f"\nDone! Cover letter PDF: {output_path}")


if __name__ == "__main__":
    main()