"""
Fetch publications directly from Google Scholar and write them to
assets/publications.json

Uses the 'scholarly' library.
"""

import json, pathlib, sys
from scholarly import scholarly

OUT_PATH = pathlib.Path("assets/publications.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHOLAR_ID = "9ZrRyxUAAAAJ"  # your ID

def main():
    print(f"Fetching publications for Google Scholar ID: {SCHOLAR_ID}")
    try:
        author = scholarly.search_author_id(SCHOLAR_ID)
        author = scholarly.fill(author, sections=["publications"])

        pubs = []
        for pub in author.get("publications", []):
            try:
                filled = scholarly.fill(pub)
                bib = filled.get("bib", {})

                pubs.append({
                    "title": bib.get("title", "Untitled"),
                    "authors": bib.get("author", ""),
                    "year": bib.get("pub_year", ""),
                    "venue": bib.get("venue", ""),
                    "url": filled.get("pub_url", ""),
                    "eprint_url": "",
                    "cited_by": filled.get("num_citations", 0),
                    "source": "google_scholar"
                })
            except Exception as e:
                print(f"  ! Failed on a publication: {e}")
                continue

        pubs.sort(key=lambda x: int(x["year"]) if str(x.get("year")).isdigit() else 0, reverse=True)

        with OUT_PATH.open("w", encoding="utf-8") as f:
            json.dump(pubs, f, indent=2, ensure_ascii=False)

        print(f"✅ Wrote {len(pubs)} publications to {OUT_PATH}")
    except Exception as e:
        print(f"❌ Error fetching from Google Scholar: {e}")
        with OUT_PATH.open("w", encoding="utf-8") as f:
            json.dump([], f)
        sys.exit(0)

if __name__ == "__main__":
    main()
