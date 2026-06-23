#!/usr/bin/env python3
"""Download Google Sheet as Excel workbook into data/"""

import os
import ssl
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

FALLBACK_SHEET_ID = "1D_MQxoi1XXoQLWSI3mH1Qo6tkctnENAij0-d3BEohu0"


def get_sheet_id() -> str:
    return os.environ.get("GOOGLE_SHEETS_ID", FALLBACK_SHEET_ID)


def download_excel():
    DATA_DIR.mkdir(exist_ok=True)
    dst = DATA_DIR / "career_data.xlsx"

    sheet_id = get_sheet_id()
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"

    # Create unverified SSL context (bypasses cert issues on some networks)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    print(f"Downloading from Google Sheets (ID: {sheet_id})...")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
            data = resp.read()
            dst.write_bytes(data)
        size_kb = len(data) / 1024
        print(f"Saved: {dst} ({size_kb:.1f} KB)")
        return True
    except Exception as e:
        print(f"Download failed: {e}")
        return False


if __name__ == "__main__":
    download_excel()
