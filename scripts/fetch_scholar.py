# scripts/fetch_scholar.py
"""
Build assets/publications.json by:
  1) Listing DOIs from ORCID
  2) For each DOI, fetching rich metadata from OpenAlex (primary),
     Crossref (backup), and enriching with Europe PMC (OA/PMCID).

Year policy (important):
  Prefer the ONLINE "in press" publication date when available:
    OpenAlex.publication_year  OR
    Crossref.published-online  → published-print → issued
  Never use acceptance/created dates.

Output fields per item:
  title, authors, year, venue, url, eprint_url, cited_by, source
"""

from __future__ import annotations
import json
import pathlib
import re
import sys
import time
from typing import Any, Dict, List

import requests

# ------------------------------- CONFIG --------------------------------------

# Your ORCID (Laura Díaz Sáez)
ORCID_ID = "0000-0001-5814-7150"

OUT_PATH = pathlib.Path("assets/publications.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "ldiazsaez-group/1.0 (GitHub Actions; contact: maintainer)"}
TIMEOUT = 60

# ------------------------------- HELPERS -------------------------------------

def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def safe_int(x, default=0):
    try:
        return int(str(x).strip())
    except Exception:
        return default

def dedupe_sort(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate by DOI (or title+year) and sort newest-first."""
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        doi = (it.get("doi") or "").lower().strip()
        key = ("doi", doi) if doi else ("ty", (norm_ws(it.get("title") or "").lower(), str(it.get("year") or "")))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    out.sort(key=lambda r: (safe_int(r.get("year")), (r.get("title") or "").lower()), reverse=True)
    return out

def write_output(items: List[Dict[str, Any]]):
    items = dedupe_sort(items or [])
    for it in items:
        it.pop("doi", None)  # keep DOI internal only
    OUT_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ Wrote {len(items)} items → {OUT_PATH}")

# ---------------------------- ORCID → DOIs -----------------------------------

def list_orcid_dois(orcid: str) -> List[str]:
    """Return unique DOIs from ORCID works (summary + full when needed)."""
    print(f"→ ORCID: listing DOIs for {orcid}")
    base = f"https://pub.orcid.org/v3.0/{orcid}"
    headers = {"Accept": "application/json", **UA}
    dois = set()
    try:
        r = requests.get(f"{base}/works", headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ! ORCID error: {e}", file=sys.stderr)
        return []

    for g in (data.get("group") or []):
        for ws in (g.get("work-summary") or []):
            # 1) Try DOI from summary
            doi = ""
            for ex in ((ws.get("external-ids") or {}).get("external-id") or []):
                if str(ex.get("external-id-type", "")).lower() == "doi":
                    doi = str(ex.get("external-id-value") or "").strip().lower()
                    break
            if doi:
                dois.add(doi)
                continue

            # 2) Otherwise, fetch full record and try again
            put_code = ws.get("put-code")
            if put_code is None:
                continue
            try:
                r2 = requests.get(f"{base}/work/{put_code}", headers=headers, timeout=TIMEOUT)
                r2.raise_for_status()
                full = r2.json()
                for ex in ((full.get("external-ids") or {}).get("external-id") or []):
                    if str(ex.get("external-id-type", "")).lower() == "doi":
                        doi = str(ex.get("external-id-value") or "").strip().lower()
                        if doi:
                            dois.add(doi)
                            break
                time.sleep(0.12)  # be polite
            except Exception as e:
                print(f"  ! ORCID work {put_code} error: {e}", file=sys.stderr)

    print(f"  • ORCID DOIs: {len(dois)} found")
    return sorted(dois)

# --------------------------- OpenAlex by DOI ---------------------------------

def fetch_openalex_by_doi(doi: str) -> Dict[str, Any] | None:
    """Return normalized record from OpenAlex for a DOI, or None."""
    doi_plain = doi.replace("https://doi.org/", "").strip().lower()
    url = f"https://api.openalex.org/works/doi:{doi_plain}"
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        w = r.json()
    except Exception:
        return None

    title = norm_ws(w.get("title") or "")
    year = w.get("publication_year") or ""  # OpenAlex already uses publication (online) year
    # authors
    names = []
    for au in (w.get("authorships") or []):
        nm = ((au or {}).get("author") or {}).get("display_name") or ""
        if nm:
            names.append(nm)
    authors = ", ".join(names)
    # venue
    venue = ((w.get("host_venue") or {}).get("display_name")) or ""
    # urls
    url_main = f"https://doi.org/{doi_plain}"
    eprint = ""
    bol = w.get("best_oa_location") or {}
    if bol.get("url_for_pdf"):
        eprint = bol["url_for_pdf"]
    elif bol.get("url"):
        eprint = bol["url"]
    elif (w.get("primary_location") or {}).get("landing_page_url"):
        eprint = w["primary_location"]["landing_page_url"]
    cited = safe_int(w.get("cited_by_count"), 0)

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "url": url_main,
        "eprint_url": eprint,
        "cited_by": cited,
        "source": "openalex",
        "doi": doi_plain,
    }

# --------------------------- Crossref by DOI ---------------------------------

def xref_year(message: dict) -> str:
    """Prefer Crossref 'published-online' year; then 'published-print'; then 'issued'."""
    def year_from(dp):
        try:
            if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
                return str(dp[0][0])
        except Exception:
            pass
        return ""
    for key in ("published-online", "published-print", "issued"):
        dp = (message.get(key) or {}).get("date-parts")
        y = year_from(dp)
        if y:
            return y
    return ""

def fetch_crossref_by_doi(doi: str) -> Dict[str, Any] | None:
    """Return normalized record from Crossref for a DOI, or None."""
    doi_plain = doi.replace("https://doi.org/", "").strip().lower()
    url = f"https://api.crossref.org/works/{doi_plain}"
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        m = (r.json().get("message") or {})
    except Exception:
        return None

    title = norm_ws(" ".join(m.get("title") or []))
    year = xref_year(m)
    venue = norm_ws(" ".join(m.get("container-title") or []))
    # authors
    names = []
    for a in (m.get("author") or []):
        nm = " ".join([a.get("given", "").strip(), a.get("family", "").strip()]).strip() or a.get("name", "")
        if nm:
            names.append(nm)
    authors = ", ".join(names)
    # links
    url_main = f"https://doi.org/{doi_plain}"
    eprint = ""
    for L in (m.get("link") or []):
        if L.get("URL"):
            eprint = L["URL"]
            if str(L.get("content-version", "")).lower() == "vor":
                break
    cited = safe_int(m.get("is-referenced-by-count"), 0)

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "url": url_main,
        "eprint_url": eprint,
        "cited_by": cited,
        "source": "crossref",
        "doi": doi_plain,
    }

# --------------------------- Europe PMC enrichment ---------------------------

def enrich_epmc(item: Dict[str, Any]) -> None:
    """
    Fill missing authors/venue/eprint_url/year via Europe PMC if available.
    Keeps existing values; only fills gaps.
    """
    doi_plain = item.get("doi")
    if not doi_plain:
        return
    base = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    try:
        r = requests.get(
            base,
            params={"query": f"doi:{doi_plain}", "format": "json", "pageSize": 1},
            headers=UA,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        recs = (r.json().get("resultList") or {}).get("result") or []
        if not recs:
            return
        rec = recs[0]
    except Exception:
        return

    # Authors (Europe PMC gives "Last F; Last F; ...")
    if (not item.get("authors")) and rec.get("authorString"):
        item["authors"] = rec["authorString"]

    # Venue
    if (not item.get("venue")) and (rec.get("journalTitle") or rec.get("bookOrReportDetails")):
        item["venue"] = rec.get("journalTitle") or rec.get("bookOrReportDetails")

    # Year (prefer online/epub date Europe PMC may have)
    if not item.get("year"):
        epub = rec.get("epubDate") or rec.get("firstPublicationDate") or ""
        if isinstance(epub, str) and len(epub) >= 4 and epub[:4].isdigit():
            item["year"] = epub[:4]
        elif rec.get("pubYear"):
            item["year"] = str(rec["pubYear"])

    # Open access / full text link
    if not item.get("eprint_url"):
        pmcid = rec.get("pmcid")
        if pmcid:
            item["eprint_url"] = f"https://europepmc.org/article/pmc/{pmcid}"
        else:
            urls = ((rec.get("fullTextUrlList") or {}).get("fullTextUrl") or [])
            for u in urls:
                if u.get("url"):
                    item["eprint_url"] = u["url"]
                    break

# ------------------------------- Merge logic ---------------------------------

def merge_records(oa: Dict[str, Any] | None, cr: Dict[str, Any] | None) -> Dict[str, Any] | None:
    """
    Prefer OpenAlex for most fields; fill gaps from Crossref.
    Year preference: OA.year (publication_year) else CR.year (published-online → print → issued).
    """
    if not oa and not cr:
        return None
    a, b = oa or {}, cr or {}

    # Year: prefer OpenAlex publication_year; else Crossref's prioritized year
    year = a.get("year") or b.get("year") or ""

    out = {
        "title": a.get("title") or b.get("title") or "",
        "authors": a.get("authors") or b.get("authors") or "",
        "year": year,
        "venue": a.get("venue") or b.get("venue") or "",
        "url": a.get("url") or b.get("url") or "",
        "eprint_url": a.get("eprint_url") or b.get("eprint_url") or "",
        "cited_by": max(safe_int(a.get("cited_by"), 0), safe_int(b.get("cited_by"), 0)),
        "source": a.get("source") or b.get("source") or "",
        "doi": (a.get("doi") or b.get("doi") or ""),
    }
    return out

# ----------------------------------- main ------------------------------------

def main():
    dois = list_orcid_dois(ORCID_ID)
    all_items: List[Dict[str, Any]] = []

    for i, doi in enumerate(dois, 1):
        if not doi:
            continue

        # 1) OpenAlex (primary)
        oa = fetch_openalex_by_doi(doi)

        # 2) Crossref (backup with proper online/print/issued year logic)
        cr = fetch_crossref_by_doi(doi)

        # 3) Merge, preferring OA and correct year policy
        rec = merge_records(oa, cr)
        if not rec:
            continue

        # 4) Europe PMC enrichment to fill any remaining gaps
        try:
            enrich_epmc(rec)
        except Exception as e:
            print(f"  ! Europe PMC enrich error for {doi}: {e}", file=sys.stderr)

        all_items.append(rec)

        # Be polite to APIs
        time.sleep(0.12)
        if i % 20 == 0:
            print(f"  • processed {i}/{len(dois)} DOIs")

    write_output(all_items)

# -----------------------------------------------------------------------------

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
