# scripts/fetch_scholar.py
"""
Fetches publications from Google Scholar and writes them to assets/publications.json
Author: Laura Díaz Sáez Lab
"""

import json
import time
import pathlib
import sys
from scholarly import scholarly

# Your Google Scholar user ID
SCHOLAR_ID = "9ZrRyxUAAAAJ"

# Output path (relative to repo root)
OUT_PATH = pathlib.Path("assets/publications.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

print(f"Fetching publications for Scholar user ID: {SCHOLAR_ID}")

try:
    author = scholarly.search_author_id(SCHOLAR_ID)
    author = scholarly.fill(author, sections=["publications"])
except Exception as e:
    print(f"Error fetching author: {e}", file=sys.stderr)
    sys.exit(1)

items = []

# Loop through all publications
for i, pub in enumerate(author.get("publications", []), start=1):
    try:
        pub_filled = scholarly.fill(pub)
        bib = pub_filled.get("bib", {})
        items.append({
            "title": bib.get("title", ""),
            "authors": bib.get("author", ""),
            "year": bib.get("pub_year", ""),
            "venue": bib.get("venue", ""),
            "url": pub_filled.get("pub_url", ""),
            "eprint_url": pub_filled.get("eprint_url", ""),
            "cited_by": pub_filled.get("num_citations", 0)
        })
        print(f"✓ {i}: {bib.get('title', '')[:70]}")
        time.sleep(1.0)  # pause to avoid hitting rate limits
    except Exception as e:
        print(f"! Failed to process publication #{i}: {e}", file=sys.stderr)

# Sort publications by year (descending), then title
def safe_year(x):
    try:
        return int(x.get("year") or 0)
    except Exception:
        return 0

items.sort(key=lambda x: (safe_year(x), (x.get("title") or "").lower()), reverse=True)

# Write output JSON
with OUT_PATH.open("w", encoding="utf-8") as f:
    json.dump(items, f, indent=2, ensure_ascii=False)

print(f"\n✅ Wrote {len(items)} publications to {OUT_PATH}")
