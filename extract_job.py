#!/usr/bin/env python3
"""Step 1: Read job description (PDF or TXT) → data/job_description.json"""

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def extract_job(filepath: str) -> dict:
    path = Path(filepath)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    ext = path.suffix.lower()
    if ext == ".txt":
        text = path.read_text(encoding="utf-8", errors="replace")
    elif ext == ".pdf":
        try:
            import pdfplumber
        except ImportError:
            print("Install pdfplumber: pip install pdfplumber")
            sys.exit(1)
        with pdfplumber.open(path) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        text = "\n".join(pages)
    else:
        print(f"Unsupported: {ext}. Use .txt or .pdf")
        sys.exit(1)

    if not text.strip():
        print("No text extracted from file")
        sys.exit(1)

    return {
        "source": str(path),
        "filename": path.name,
        "job_name": path.stem,
        "description": text.strip(),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract job description to JSON")
    parser.add_argument("--job", "-j", required=True, help="Path to job description file")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    data = extract_job(args.job)
    dst = DATA_DIR / "job_description.json"
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Job description saved: {dst} ({len(data['description'])} chars)")


if __name__ == "__main__":
    main()
