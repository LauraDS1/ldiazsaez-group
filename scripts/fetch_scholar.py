# scripts/fetch_scholar.py
"""
Build assets/publications.json from OpenAlex (+ Crossref, + optional Europe PMC).

Priority (merge & dedupe by DOI or title+year):
1) OpenAlex via ORCID  → comprehensive works list + cited_by + OA links
2) Crossref via ORCID  → extra coverage/metadata
3) Europe PMC by DOI   → fill missing authors/venue/PMCID/open access link

Output fields per item:
  title, authors, year, venue, url, eprint_url, cited_by, source
"""
from __future__ import annotations
import json, pathlib, sys, time, re
from typing import List, Dict, Any
import requests

# ---- Your ORCID -------------------------------------------------------------
ORCID_ID = "0000-0001-5814-7150"  # Laura Díaz Sáez
# -----------------------------------------------------------------------------

OUT_PATH = pathlib.Path("assets/publications.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "ldiazsaez-group/1.0 (GitHub Actions; contact: maintainer)"}
TIMEOUT = 60

def safe_int(x, default=0):
    try:
        return int(str(x).strip())
    except Exception:
        return default

def norm_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def norm_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate and sort publications."""
    seen = set()
    deduped = []
    for it in items:
        doi = (it.get("doi") or "").lower().strip()
        key = ("doi", doi) if doi else (
            "ty", (norm_whitespace(it.get("title") or "").lower(), str(it.get("year") or "").strip())
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    deduped.sort(
        key=lambda r: (safe_int(r.get("year")), (r.get("title") or "").lower()),
        reverse=True
    )
    return deduped

def write_output(items: List[Dict[str, Any]]):
    items = norm_items(items or [])
    for it in items:
        it.pop("doi", None)  # keep DOI only internally for de-dupe
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    print(f"✅ Wrote {len(items)} items → {OUT_PATH}")

# --------------------------- OpenAlex via ORCID -------------------------------
# Docs: authors expose works_api_url; also works can be filtered by authorships.author.orcid
# https://docs.openalex.org/api-entities/authors/author-object
# https://docs.openalex.org/api-entities/works/filter-works
def fetch_openalex_by_orcid(orcid: str) -> List[Dict[str, Any]]:
    print(f"→ OpenAlex (ORCID={orcid})")
    items: List[Dict[str, Any]] = []
    base = "https://api.openalex.org/works"
    cursor = "*"

    params = {
        "filter": f"authorships.author.orcid:{orcid}",
        "per-page": 200,
        "cursor": cursor,
        "select": ",".join([
            "title",
            "authorships",
            "publication_year",
            "host_venue",
            "doi",
            "open_access",
            "cited_by_count",
            "primary_location",
            "best_oa_location",
        ]),
        "sort": "publication_year:desc",
    }

    while True:
        try:
            r = requests.get(base, params=params, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ! OpenAlex error: {e}", file=sys.stderr)
            break

        recs = data.get("results") or []
        for it in recs:
            title = norm_whitespace(it.get("title") or "")
            year = it.get("publication_year") or ""
            doi = (it.get("doi") or "").replace("https://doi.org/", "").lower()

            # authors
            authorships = it.get("authorships") or []
            names = []
            for au in authorships:
                author = (au or {}).get("author") or {}
                nm = author.get("display_name") or ""
                if nm:
                    names.append(nm)
            authors_str = ", ".join(names)

            # venue
            hv = it.get("host_venue") or {}
            venue = hv.get("display_name") or ""

            # links
            url = f"https://doi.org/{doi}" if doi else (hv.get("url") or "")
            eprint_url = ""
            bol = it.get("best_oa_location") or {}
            if bol.get("url_for_pdf"):
                eprint_url = bol["url_for_pdf"]
            elif bol.get("url"):
                eprint_url = bol["url"]
            elif (it.get("primary_location") or {}).get("source") and (it.get("primary_location") or {}).get("landing_page_url"):
                eprint_url = it["primary_location"]["landing_page_url"] or ""

            cited_by = safe_int(it.get("cited_by_count"), 0)

            items.append({
                "title": title,
                "authors": authors_str,
                "year": year,
                "venue": venue,
                "url": url,
                "eprint_url": eprint_url,
                "cited_by": cited_by,
                "source": "openalex",
                "doi": doi,
            })

        next_cursor = (data.get("meta") or {}).get("next_cursor")
        if not next_cursor or not recs:
            break
        params["cursor"] = next_cursor
        time.sleep(0.2)

    print(f"  • OpenAlex collected {len(items)} items")
    return items

# --------------------------- Crossref via ORCID -------------------------------
# https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/
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
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ! Crossref error: {e}", file=sys.stderr)
            break

        recs = (data.get("message") or {}).get("items") or []
        next_cursor = (data.get("message") or {}).get("next-cursor")

        for it in recs:
            title = norm_whitespace(" ".join(it.get("title") or []))
            venue = norm_whitespace(" ".join(it.get("container-title") or []))
            issued = (it.get("issued") or {}).get("date-parts") or []
            year = issued[0][0] if issued and isinstance(issued[0], list) and issued[0] else ""

            url_pref = it.get("URL") or ""
            doi = (it.get("DOI") or "").lower().strip()
            url = f"https://doi.org/{doi}" if doi else url_pref

            # authors
            author_names = []
            for a in (it.get("author") or []):
                nm = " ".join([a.get("given", "").strip(), a.get("family", "").strip()]).strip() or a.get("name", "")
                if nm:
                    author_names.append(nm)
            authors_str = ", ".join(author_names)

            # prefer VOR or any OA link
            eprint_url = ""
            for L in (it.get("link") or []):
                if L.get("URL"):
                    eprint_url = L["URL"]
                    if str(L.get("content-version", "")).lower() == "vor":
                        break

            items.append({
                "title": title,
                "authors": authors_str,
                "year": year,
                "venue": venue,
                "url": url,
                "eprint_url": eprint_url,
                "cited_by": safe_int(it.get("is-referenced-by-count"), 0),
                "source": "crossref",
                "doi": doi,
            })

        if not next_cursor or not recs:
            break
        cursor = next_cursor
        time.sleep(0.3)

    print(f"  • Crossref collected {len(items)} items")
    return items

# --------------------------- Europe PMC (optional) ----------------------------
# https://europepmc.org/RestfulWebService
def enrich_with_europe_pmc(items: List[Dict[str, Any]]) -> None:
    """In-place enrichment by DOI: fill authors/venue/eprint_url if missing."""
    print("→ Europe PMC enrichment (by DOI)")
    base = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    for it in items:
        if it.get("doi") and (not it.get("authors") or not it.get("venue") or not it.get("eprint_url")):
            q = f"doi:{it['doi']}"
            try:
                r = requests.get(base, params={"query": q, "format": "json", "pageSize": 1}, headers=UA, timeout=TIMEOUT)
                r.raise_for_status()
                data = r.json()
                recs = (data.get("resultList") or {}).get("result") or []
                if recs:
                    rec = recs[0]
                    # authors as "Last F; Last F"
                    auths = rec.get("authorString") or ""
                    venue = rec.get("journalTitle") or rec.get("bookOrReportDetails") or it.get("venue")
                    # OA / full text link (if pmcid exists)
                    pmcid = rec.get("pmcid")
                    if not it.get("eprint_url"):
                        if pmcid:
                            it["eprint_url"] = f"https://europepmc.org/article/pmc/{pmcid}"
                        elif rec.get("fullTextUrlList"):
                            # heuristic: take first fullTextUrl
                            urls = (rec["fullTextUrlList"] or {}).get("fullTextUrl") or []
                            for u in urls:
                                if u.get("url"):
                                    it["eprint_url"] = u["url"]
                                    break
                    if auths and not it.get("authors"):
                        it["authors"] = auths
                    if venue and not it.get("venue"):
                        it["venue"] = venue
            except Exception as e:
                print(f"  ! Europe PMC error for {q}: {e}", file=sys.stderr)
            time.sleep(0.15)

# --------------------------------- main --------------------------------------
def main():
    if not ORCID_ID or ORCID_ID.count("-") != 3:
        print("⚠️ Please set ORCID_ID at the top of scripts/fetch_scholar.py", file=sys.stderr)

    all_items: List[Dict[str, Any]] = []

    # 1) OpenAlex first (broadest, good OA links & cited-by counts)
    try:
        all_items.extend(fetch_openalex_by_orcid(ORCID_ID))
    except Exception as e:
        print(f"! OpenAlex fetch failed: {e}", file=sys.stderr)

    # 2) Crossref for additional coverage
    try:
        all_items.extend(fetch_crossref_by_orcid(ORCID_ID))
    except Exception as e:
        print(f"! Crossref fetch failed: {e}", file=sys.stderr)

    # Deduplicate before enrichment
    all_items = norm_items(all_items)

    # 3) Optional enrichment with Europe PMC (fills small gaps)
    try:
        enrich_with_europe_pmc(all_items)
    except Exception as e:
        print(f"! Europe PMC enrichment failed: {e}", file=sys.stderr)

    # Finalize
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
