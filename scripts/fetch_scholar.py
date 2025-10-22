# scripts/fetch_scholar.py
"""
Fetch publications for the Laura Díaz Sáez Lab.

Strategy:
1) Try Google Scholar via `scholarly` (best match to the Scholar profile).
2) If that fails (blocked, CAPTCHA, etc.), fall back to Semantic Scholar API.
3) Always write assets/publications.json so the workflow doesn't fail.

Output schema (list of dicts):
[
  {
    "title": str,
    "authors": str,                # "A. Author, B. Author, ..."
    "year": int|str,
    "venue": str,
    "url": str,                    # preferred link (publisher/open access)
    "eprint_url": str,             # secondary link (e.g., PDF)
    "cited_by": int,
    "source": "google_scholar"|"semantic_scholar"
  },
  ...
]
"""

from __future__ import annotations
import json
import time
import pathlib
import sys
from typing import List, Dict

OUT_PATH = pathlib.Path("assets/publications.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHOLAR_ID = "9ZrRyxUAAAAJ"  # your Google Scholar user id
AUTHOR_QUERIES = [
    "Laura Díaz Sáez",
    "Laura Diaz Saez",   # ASCII fallback (helps S2 search)
]

# ----------------------------- helpers ---------------------------------
def safe_year(y):
    try:
        return int(y)
    except Exception:
        return 0

def normalize(items: List[Dict]) -> List[Dict]:
    # sort newest first, then title
    items.sort(key=lambda x: (safe_year(x.get("year") or 0), (x.get("title") or "").lower()), reverse=True)
    return items

def write_output(items: List[Dict]):
    items = normalize(items)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    print(f"✅ Wrote {len(items)} items -> {OUT_PATH}")

# ----------------------- Google Scholar branch -------------------------
def fetch_from_scholar() -> List[Dict]:
    print("→ Trying Google Scholar via 'scholarly'…")
    try:
        from scholarly import scholarly
    except Exception as e:
        print(f"  ! scholarly not available: {e}", file=sys.stderr)
        return []

    try:
        author = scholarly.search_author_id(SCHOLAR_ID)
        author = scholarly.fill(author, sections=["publications"])
    except Exception as e:
        print(f"  ! Could not fetch author from Scholar: {e}", file=sys.stderr)
        return []

    pubs = author.get("publications", []) or []
    print(f"  • Found {len(pubs)} publications on Scholar. Filling details…")

    items: List[Dict] = []
    for i, pub in enumerate(pubs, start=1):
        try:
            pub_filled = scholarly.fill(pub)
            bib = pub_filled.get("bib", {}) or {}
            items.append({
                "title": bib.get("title", "") or "",
                "authors": bib.get("author", "") or "",
                "year": bib.get("pub_year", "") or "",
                "venue": bib.get("venue", "") or "",
                "url": (pub_filled.get("pub_url") or pub_filled.get("eprint_url") or "") or "",
                "eprint_url": pub_filled.get("eprint_url", "") or "",
                "cited_by": int(pub_filled.get("num_citations") or 0),
                "source": "google_scholar",
            })
            print(f"    ✓ {i}: {bib.get('title','')[:70]}")
            time.sleep(1.0)  # polite throttle
        except Exception as e:
            print(f"    ! Failed on pub {i}: {e}", file=sys.stderr)
            continue

    return items

# --------------------- Semantic Scholar fallback -----------------------
def fetch_from_semantic_scholar() -> List[Dict]:
    print("→ Falling back to Semantic Scholar API…")
    try:
        import requests
    except Exception as e:
        print(f"  ! requests not available: {e}", file=sys.stderr)
        return []

    # 1) Find an authorId
    author_id = None
    for q in AUTHOR_QUERIES:
        try:
            r = requests.get(
                "https://api.semanticscholar.org/graph/v1/author/search",
                params={"query": q, "limit": 1, "fields": "name,authorId"},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("data"):
                author_id = data["data"][0]["authorId"]
                print(f"  • Using authorId={author_id} for query '{q}'")
                break
        except Exception as e:
            print(f"  ! search '{q}' failed: {e}", file=sys.stderr)

    if not author_id:
        print("  ! No author found on Semantic Scholar.", file=sys.stderr)
        return []

    # 2) Fetch papers for that author
    items: List[Dict] = []
    fields = "title,venue,year,url,openAccessPdf,citationCount,authors"
    try:
        r = requests.get(
            f"https://api.semanticscholar.org/graph/v1/author/{author_id}/papers",
            params={"limit": 200, "fields": fields, "offset": 0, "sort": "year:desc"},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        papers = data.get("data") or []
        print(f"  • Retrieved {len(papers)} papers from Semantic Scholar.")

        for i, p in enumerate(papers, start=1):
            title = p.get("title") or ""
            venue = p.get("venue") or ""
            year = p.get("year") or ""
            url = p.get("url") or ""
            # Prefer open access PDF if present
            oa = p.get("openAccessPdf") or {}
            pdf_url = oa.get("url") or ""
            # Build authors string "A. Last, B. Last, …"
            authors = p.get("authors") or []
            author_names = ", ".join([a.get("name", "") for a in authors if a.get("name")])
            cited_by = int(p.get("citationCount") or 0)

            items.append({
                "title": title,
                "authors": author_names,
                "year": year,
                "venue": venue,
                "url": pdf_url or url,
                "eprint_url": pdf_url,
                "cited_by": cited_by,
                "source": "semantic_scholar",
            })

            if i % 25 == 0:
                print(f"    • processed {i} papers…")

    except Exception as e:
        print(f"  ! Failed to fetch papers from Semantic Scholar: {e}", file=sys.stderr)

    return items

# ------------------------------- main ----------------------------------
def main():
    items: List[Dict] = []

    # Try Scholar first
    try:
        items = fetch_from_scholar()
    except Exception as e:
        print(f"! Unexpected Scholar error: {e}", file=sys.stderr)

    # Fallback if needed
    if not items:
        try:
            items = fetch_from_semantic_scholar()
        except Exception as e:
            print(f"! Unexpected Semantic Scholar error: {e}", file=sys.stderr)

    # Always write something (even empty list) so site doesn't break
    write_output(items or [])

if __name__ == "__main__":
    # Never raise a non-zero exit on network issues; keep CI green.
    try:
        main()
        sys.exit(0)
    except Exception as e:
        print(f"!! Fatal error: {e}", file=sys.stderr)
        # Still write an empty file to avoid breaking the site
        try:
            write_output([])
        except Exception:
            pass
        sys.exit(0)
