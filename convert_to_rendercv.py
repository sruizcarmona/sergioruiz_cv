#!/usr/bin/env python3
"""Convert career_full.json to RenderCV YAML format."""

import json
import re
import sys
from pathlib import Path

from ruamel.yaml import YAML

DATA_DIR = Path(__file__).parent / "data"

MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def normalize_date(raw: str) -> str | None:
    if not raw or raw in ("nan", "None", ""):
        return None
    raw = raw.strip()
    if raw.lower() == "present":
        return "present"
    # YYYY-MM-DD HH:MM:SS → YYYY-MM
    m = re.match(r"(\d{4})-(\d{2})-\d{2}", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # "Mon YYYY" → YYYY-MM
    m = re.match(r"([A-Za-z]+)\s+(\d{4})", raw)
    if m:
        mon = m.group(1).lower()[:3]
        if mon in MONTH_MAP:
            return f"{m.group(2)}-{MONTH_MAP[mon]}"
    # Just YYYY
    m = re.match(r"(\d{4})", raw)
    if m:
        return m.group(1)
    return raw


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&")
    text = text.replace("&ast;", "*")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    return text.strip()


def clean_br(text: str) -> str:
    """Replace <br> with comma for inline use."""
    return re.sub(r"\s*<br\s*/?>\s*", ", ", text).strip()


def extract_url(html_text: str) -> str:
    """Extract href URL from an <a> tag, or return the text."""
    m = re.search(r'href=["\'](.*?)["\']', html_text)
    if m:
        return m.group(1)
    return strip_html(html_text)


def _sort_date_key(raw: str) -> str:
    """Convert start date to sortable YYYY-MM key, defaulting to 1970-01 for missing."""
    d = normalize_date(raw)
    if not d:
        return "1970-01"
    if len(d) == 4:
        return d + "-01"
    return d


def build_rendercv_yaml(career: dict) -> dict:
    entries = career.get("entries", [])
    contact = career.get("contact", {})

    # --- Header ---
    linkedin_url = extract_url(contact.get("linkedin", ""))
    github_url = extract_url(contact.get("github", ""))
    website_url = extract_url(contact.get("website", ""))
    linkedin_user = linkedin_url.rstrip("/").split("/")[-1] if linkedin_url else ""
    github_user = github_url.rstrip("/").split("/")[-1] if github_url else ""

    cv_data = {
        "cv": {
            "name": "Sergio Ruiz Carmona",
            "location": contact.get("location", "") or "",
            "email": contact.get("email", ""),
            "phone": None,
            "website": website_url or "",
            "social_networks": [],
            "sections": {},
        },
        "design": {
            "theme": "classic",
            "sections": {
                "show_time_spans_in": [],
            },
        },
    }

    if linkedin_user:
        cv_data["cv"]["social_networks"].append({
            "network": "LinkedIn",
            "username": linkedin_user,
        })
    if github_user:
        cv_data["cv"]["social_networks"].append({
            "network": "GitHub",
            "username": github_user,
        })

    sections: dict = {}

    # ── Page 1: Summary, Skills, Experience ──────────────────────────────────

    # --- Summary ---
    intro = career.get("text_blocks", {}).get("intro", "")
    if intro:
        sections["summary"] = [strip_html(intro)]

    # --- Skills: Core Competencies (from root skills[]) + skills_and_training ---
    skills = []
    core_skills = career.get("skills", [])
    if core_skills:
        skill_names = [s.get("name", "") for s in core_skills if s.get("name")]
        if skill_names:
            skills.append({
                "label": "Core Competencies",
                "details": ", ".join(skill_names),
            })
    skill_entries = [e for e in entries if e["section"] == "skills_and_training"]
    for e in skill_entries:
        skills.append({
            "label": e["title"],
            "details": clean_br(e["location"]),
        })
    if skills:
        sections["skills"] = skills

    # --- Experience (main_positions + research_visits, reverse chronological) ---
    exp_entries = sorted(
        [e for e in entries if e["section"] in ("main_positions", "research_visits")],
        key=lambda e: _sort_date_key(e["start"]),
        reverse=True,
    )
    if exp_entries:
        experience = []
        for e in exp_entries:
            company = clean_br(e["location"])
            pos = {
                "company": company,
                "position": e["title"],
                "start_date": normalize_date(e["start"]),
                "end_date": normalize_date(e["end"]),
                "location": e["institution"] if e["institution"] not in ("nan", "") else "",
                "highlights": [
                    strip_html(b) for b in e.get("bullets", [])
                ] if e.get("bullets") else [],
            }
            experience.append(pos)
        sections["experience"] = experience

    # ── Page 2: Education, Publications, Presentations, Teaching ────────────────

    # --- Education ---
    edu_entries = [e for e in entries if e["section"] == "education"]
    if edu_entries:
        education = []
        for e in edu_entries:
            education.append({
                "institution": clean_br(e["location"]),
                "area": "",
                "degree": e["title"],
                "start_date": normalize_date(e["start"]),
                "end_date": normalize_date(e["end"]),
                "location": e["institution"] if e["institution"] not in ("nan", "") else "",
                "highlights": [
                    strip_html(b) for b in e.get("bullets", [])
                ] if e.get("bullets") else [],
            })
        sections["education"] = education

    # --- Publications (academic_articles) ---
    pub_entries = [e for e in entries if e["section"] == "academic_articles"]
    if pub_entries:
        publications = []
        for e in pub_entries:
            authors = []
            bullets = e.get("bullets", [])
            if bullets:
                first = strip_html(bullets[0])
                first = re.sub(r"\s*&ast;", "*", first)
                first = re.sub(r"\s*&\s*", ", ", first)
                parts = [p.strip().strip(",") for p in first.split(",")]
                for p in parts:
                    p = re.sub(r"^\[\.\.\.\]\s*", "", p).strip()
                    if p and p != "[...]":
                        p = "*" + p.strip("*") + "*" if "Sergio Ruiz" in p and "*" not in p else p
                        authors.append(p)
            publications.append({
                "title": e["title"],
                "authors": authors,
                "journal": clean_br(e["location"]) if e["location"] not in ("nan", "") else "",
                "date": normalize_date(e["end"]) or "",
            })
        sections["publications"] = publications

    # --- Presentations (scientific_comms + patent) ---
    pres_list = []
    # Up to 4 key presentations
    pres = sorted(
        [e for e in entries if e["section"] == "scientific_comms"],
        key=lambda e: normalize_date(e.get("end", "")) or "",
        reverse=True,
    )[:4]
    for e in pres:
        pres_list.append({
            "name": e["title"],
            "date": normalize_date(e.get("end", "")) or "",
            "highlights": [strip_html(b) for b in e.get("bullets", [])[:1]],
        })
    # Patent mentions from experience bullets
    patent_added = False
    for e in entries:
        if e.get("section") in ("main_positions", "research_visits") and e.get("bullets"):
            for b in e["bullets"]:
                if "patent" in b.lower() and not patent_added:
                    pres_list.append({
                        "name": "Compounds for use in the treatment of hyperproliferative disorders",
                        "date": normalize_date(e.get("end", "")) or normalize_date(e.get("start", "")) or "",
                        "highlights": [strip_html(b[:200])],
                    })
                    patent_added = True
                    break
    if pres_list:
        sections["presentations"] = pres_list

    # --- Teaching (teaching_positions, reverse chronological) ---
    teach_entries = sorted(
        [e for e in entries if e["section"] == "teaching_positions"],
        key=lambda e: _sort_date_key(e["start"]),
        reverse=True,
    )
    if teach_entries:
        teaching = []
        for e in teach_entries:
            company = clean_br(e["location"])
            pos = {
                "company": company,
                "position": e["title"],
                "start_date": normalize_date(e["start"]),
                "end_date": normalize_date(e["end"]),
                "location": e["institution"] if e["institution"] not in ("nan", "") else "",
                "highlights": [
                    strip_html(b) for b in e.get("bullets", [])
                ] if e.get("bullets") else [],
            }
            teaching.append(pos)
        sections["teaching"] = teaching

    cv_data["cv"]["sections"] = sections
    return cv_data


def main():
    src = DATA_DIR / "career_full.json"
    if not src.exists():
        print(f"Not found: {src}")
        sys.exit(1)

    with open(src, encoding="utf-8") as f:
        career = json.load(f)

    data = build_rendercv_yaml(career)

    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 120

    dst = DATA_DIR / "sergioruiz_cv.yaml"
    with open(dst, "w", encoding="utf-8") as f:
        yaml.dump(data, f)
    print(f"Written: {dst}")
    print(f"  Sections: {list(data['cv']['sections'].keys())}")


if __name__ == "__main__":
    main()
