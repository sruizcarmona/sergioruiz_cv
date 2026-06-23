#!/usr/bin/env python3
"""Step 2: Read Google Sheet Excel → data/career_full.json"""

import json
import sys
from pathlib import Path
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"


def parse_entries(df: pd.DataFrame) -> list:
    """Convert entries sheet rows to a clean list of dicts."""
    entries = []
    for _, row in df.iterrows():
        entry = {
            "section": str(row.get("section", "") or "").strip(),
            "title": str(row.get("title", "") or "").strip(),
            "location": str(row.get("loc", "") or "").strip(),
            "institution": str(row.get("institution", "") or "").strip(),
            "start": str(row.get("start", "") or "").strip(),
            "end": str(row.get("end", "") or "").strip(),
            "bullets": [],
        }
        for col in df.columns:
            if col.startswith("description") and pd.notna(row[col]):
                b = str(row[col]).strip()
                if b:
                    entry["bullets"].append(b)
        entries.append(entry)
    return entries


def extract_sheets(xlsx_path: str) -> dict:
    xls = pd.ExcelFile(xlsx_path)

    sheets = xls.sheet_names
    expected = {"entries", "language_skills", "text_blocks", "contact_info"}
    missing = expected - set(sheets)
    if missing:
        print(f"Missing sheets in workbook: {missing}")
        sys.exit(1)

    # First row is the column header
    entries_df = pd.read_excel(xls, "entries", header=0)
    skills_df = pd.read_excel(xls, "language_skills", header=0)
    text_df = pd.read_excel(xls, "text_blocks", header=0)
    contact_df = pd.read_excel(xls, "contact_info", header=0)

    # Clean column names
    entries_df.columns = [str(c).strip() for c in entries_df.columns]
    skills_df.columns = [str(c).strip() for c in skills_df.columns]
    text_df.columns = [str(c).strip() for c in text_df.columns]
    contact_df.columns = [str(c).strip() for c in contact_df.columns]

    # Drop rows where key cols are NaN
    entries_df = entries_df.dropna(subset=["section", "title"], how="all")
    skills_df = skills_df.dropna(subset=[skills_df.columns[0]], how="all")
    text_df = text_df.dropna(subset=["loc"], how="all")
    contact_df = contact_df.dropna(subset=["loc"], how="all")

    career = {
        "entries": parse_entries(entries_df),
        "skills": [],
        "text_blocks": {},
        "contact": {},
    }

    # Skills
    for _, row in skills_df.iterrows():
        cols = list(skills_df.columns)
        if len(cols) >= 2:
            name = str(row[cols[0]] or "").strip()
            level = str(row[cols[1]] or "").strip()
            if name:
                career["skills"].append({"name": name, "level": level})

    # Text blocks
    for _, row in text_df.iterrows():
        loc = str(row.get("loc", "") or "").strip()
        text = str(row.get("text", "") or "").strip()
        if loc and text:
            career["text_blocks"][loc] = text

    # Contact info
    for _, row in contact_df.iterrows():
        loc = str(row.get("loc", "") or "").strip()
        val = str(row.get("contact", "") or "").strip()
        if loc and val:
            career["contact"][loc] = val

    return career


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract Google Sheet data to JSON")
    parser.add_argument("--xlsx", default=str(DATA_DIR / "career_data.xlsx"),
                        help="Path to Excel workbook")
    args = parser.parse_args()

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        # Try auto-download
        print("Excel file not found. Try running: python download_data.py")
        sys.exit(1)

    DATA_DIR.mkdir(exist_ok=True)
    career = extract_sheets(str(xlsx_path))

    dst = DATA_DIR / "career_full.json"
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(career, f, indent=2, ensure_ascii=False)

    print(f"Career data saved: {dst}")
    print(f"  Entries: {len(career['entries'])}")
    print(f"  Skills: {len(career['skills'])}")
    print(f"  Text blocks: {len(career['text_blocks'])}")
    print(f"  Contact fields: {len(career['contact'])}")


if __name__ == "__main__":
    main()
