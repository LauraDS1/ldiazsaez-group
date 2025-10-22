"""
Generates assets/publications.json for the lab website.

Priority:
1) Semantic Scholar (strict by hard-coded author ID)
2) Writes JSON even if empty (so the site never breaks)
"""

from __future__ import annotations
import json, pathlib, sys, time
from typing import List, Dict
import requests

OUT_PATH = pathlib.Path("assets/publications.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 💡 Your author ID (from Semantic Scholar or linked to your Google Scholar)
# ---------------------------------------------------------------------------
S2_AUTHOR_ID = "9ZrRyxUAAAAJ"  # Laura Díaz Sáez (Google Scholar ID)
UA = {"User-Agent": "lda-lab-pubs/1.0 (GitHub Actions; contact: maintainer)"}


def safe_year(y):
    try:
        return int(str(y).strip())
    except Exception:
        return 0


def normalize(items: List[Dict]) -> List[Dict]:
    seen = set()
    deduped = []
    for it in items:
        key = (it.get("title", "").strip().lower(), str(it.get("year", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    deduped.sort(key=lambda x: (safe_year(x.get("year")), (x.get("title") or "").lower()), reverse=True)
    return deduped


def write_output(items: List[Dict]):
    items = normalize(items or [])
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    print(f"✅ Wrote {len(items)} items -> {OUT_PATH}")


# ---------------------------------------------------------------------------
# Semantic Scholar fetch
# ---------------------------------------------------------------------------
def fetch_semantic_scholar(author_id: str) -> List[Dict]:
    print(f"→ Fetching from Semantic Scholar: authorId={author_id}")
    items: List[Dict] = []
    base = "https://api.semanticscholar.org/graph/v1/author"
    url = f"{base}/{author_id}/papers"
    fields = "title,venue,year,url,openAccessPdf,citationCount,authors"
    limit = 100
    offset = 0

    while True:
        try:
            r = requests.get(
                url,
                params={"fields": fields, "limit": limit, "offset": offset, "sort": "year:desc"},
                headers=UA,
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ! Semantic Scholar fetch failed at offset={offset}: {e}", file=sys.stderr)
            break

        papers = data.get("data") or []
        if not papers:
            break

        for p in papers:
            authors = p.get("authors") or []
            if author_id not in {a.get("authorId") for a in authors if a.get("authorId")}:
                continue

            items.append(
                {
                    "title": p.get("title", ""),
                    "authors": ", ".join(a.get("name", "") for a in authors if a.get("name")),
                    "year": p.get("year", ""),
                    "venue": p.get("venue", ""),
                    "url": (p.get("openAccessPdf") or {}).get("url") or p.get("url") or "",
                    "eprint_url": (p.get("openAccessPdf") or {}).get("url") or "",
                    "cited_by": int(p.get("citationCount") or 0),
                    "source": "semantic_scholar",
                }
            )

        offset += limit
        if len(papers) < limit:
            break
        time.sleep(0.5)  # be polite

    print(f"  • Kept {len(items)} publications after filtering.")
    return items


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    try:
        items = fetch_semantic_scholar(S2_AUTHOR_ID)
    except Exception as e:
        print(f"! Error fetching data: {e}", file=sys.stderr)
        items = []
    write_output(items)


if __name__ == "__main__":
    main()
