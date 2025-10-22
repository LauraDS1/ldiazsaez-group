# scripts/fetch_scholar.py
"""
Fetch publications using ORCID and write to assets/publications.json.

Priority:
1) Crossref (filtered by ORCID) → richer metadata + cited-by counts
2) ORCID public API (fallback)  → ensures coverage

Output schema per item:
  title, authors, year, venue, url, eprint_url, cited_by, source
"""
from __future__ import annotations
import json, pathlib, sys, time
from typing import List, Dict, Any
import requests

# ---- Your ORCID -------------------------------------------------------------
ORCID_ID = "0000-0001-5814-7150"  # Laura Díaz Sáez
# -----------------------------------------------------------------------------

OUT_PATH = pathlib.Path("assets/publications.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "ldiazsaez-group/1.0 (GitHub Actions; contact: maintainer)"}


def safe_int(x, default=0):
    try:
        return int(str(x).strip())
    except Exception:
        return default


def norm_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate and sort publications."""
    seen = set()
    deduped = []
    for it in items:
        doi = (it.get("doi") or "").lower().strip()
        key = ("doi", doi) if doi else (
            "ty", (it.get("title", "").lower().strip(), str(it.get("year") or "").strip())
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    deduped.sort(
        key=lambda r: (safe_int(r.get("year")), (r.get("title") or "").lower()), reverse=True
    )
    return deduped


def write_output(items: List[Dict[str, Any]]):
    items = norm_items(items or [])
    for it in items:
        it.pop("doi", None)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    print(f"✅ Wrote {len(items)} items → {OUT_PATH}")


# --------------------------- Crossref via ORCID -------------------------------
def fetch_crossref_by_orcid(orcid: str) -> List[Dict[str, Any]]:
    print(f"→ Crossref (ORCID={orcid})")
    url = "https://api.crossref.org/works"
    cursor = "*"
    rows = 200
    items: List[Dict[str, Any]] = []

    while True:
        try:
            r = requests.get(
                url,
                params={
                    "filter": f"orcid:{orcid}",
                    "rows": rows,
                    "cursor": cursor,
                    "select": "title,author,issued,container-title,URL,DOI,is-referenced-by-count,link",
                },
                headers=UA,
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ! Crossref error: {e}", file=sys.stderr)
            break

        recs = (data.get("message") or {}).get("items") or []
        next_cursor = (data.get("message") or {}).get("next-cursor")

        for it in recs:
            title = " ".join(it.get("title") or []).strip()
            venue = " ".join(it.get("container-title") or []).strip()
            issued = (it.get("issued") or {}).get("date-parts") or []
            year = (
                issued[0][0]
                if issued and isinstance(issued[0], list) and len(issued[0]) > 0
                else ""
            )
            url_pref = it.get("URL") or ""
            doi = (it.get("DOI") or "").lower()
            cited_by = safe_int(it.get("is-referenced-by-count"), 0)

            # Prefer open-access link if present
            links = it.get("link") or []
            oa_url = ""
            for L in links:
                if str(L.get("content-version", "")).lower() == "vor" and L.get("URL"):
                    oa_url = L["URL"]
                    break

            authors = it.get("author") or []
            author_names = []
            for a in authors:
                given = a.get("given", "").strip()
                family = a.get("family", "").strip()
                nm = " ".join([given, family]).strip() or a.get("name", "").strip()
                if nm:
                    author_names.append(nm)
            authors_str = ", ".join(author_names)

            items.append(
                {
                    "title": title or "Untitled",
                    "authors": authors_str,
                    "year": year,
                    "venue": venue,
                    "url": oa_url or url_pref or (f"https://doi.org/{doi}" if doi else ""),
                    "eprint_url": oa_url,
                    "cited_by": cited_by,
                    "source": "crossref_orcid",
                    "doi": doi,
                }
            )

        if not next_cursor or not recs:
            break
        cursor = next_cursor
        time.sleep(0.3)

    print(f"  • Crossref collected {len(items)} items")
    return items


# --------------------------- ORCID public API --------------------------------
def fetch_orcid_public(orcid: str) -> List[Dict[str, Any]]:
    print(f"→ ORCID public API (fallback) ORCID={orcid}")
    url = f"https://pub.orcid.org/v3.0/{orcid}/works"
    headers = {"Accept": "application/json", **UA}
    items: List[Dict[str, Any]] = []

    try:
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ! ORCID public API error: {e}", file=sys.stderr)
        return items

    groups = data.get("group") or []
    for g in groups:
        summaries = g.get("work-summary") or []
        for ws in summaries:
            title_obj = (ws.get("title") or {})
            title = (title_obj.get("title") or {}).get("value") or "Untitled"
            journal_title = (ws.get("journal-title") or {}).get("value") or ""
            pub_date = ws.get("publication-date") or {}
            year = ""
            if "year" in pub_date and pub_date["year"] and pub_date["year"].get("value"):
                year = pub_date["year"]["value"]

            doi = ""
            exids = (ws.get("external-ids") or {}).get("external-id") or []
            for ex in exids:
                if str(ex.get("external-id-type", "")).lower() == "doi":
                    doi = str(ex.get("external-id-value", "")).lower().strip()
                    break

            items.append(
                {
                    "title": title,
                    "authors": "",
                    "year": year,
                    "venue": journal_title,
                    "url": f"https://doi.org/{doi}" if doi else "",
                    "eprint_url": "",
                    "cited_by": 0,
                    "source": "orcid_public",
                    "doi": doi,
                }
            )

    print(f"  • ORCID fallback collected {len(items)} items")
    return items


# --------------------------------- main --------------------------------------
def main():
    if not ORCID_ID or ORCID_ID.count("-") != 3:
        print("⚠️ Please set ORCID_ID at the top of scripts/fetch_scholar.py", file=sys.stderr)

    all_items: List[Dict[str, Any]] = []

    # Prefer Crossref (richer data)
    try:
        all_items.extend(fetch_crossref_by_orcid(ORCID_ID))
    except Exception as e:
        print(f"! Crossref fetch failed: {e}", file=sys.stderr)

    # Fallback to ORCID public if Crossref gave few/none
    if len(all_items) < 5:
        try:
            all_items.extend(fetch_orcid_public(ORCID_ID))
        except Exception as e:
            print(f"! ORCID fallback failed: {e}", file=sys.stderr)

    write_output(all_items)


if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except Exception as e:
        print(f"!! Fatal: {e}", file=sys.stderr)
        try:
            write_output([])
        except Exception:
            pass
        sys.exit(0)
