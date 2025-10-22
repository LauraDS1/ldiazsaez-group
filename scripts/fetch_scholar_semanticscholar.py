"""
Generates assets/publications.json for the lab website.

How it works
------------
1) Find the correct Semantic Scholar authorId by searching your name and
   scoring candidates by name match + affiliation hints (IQF, CSIC, Warwick).
2) Fetch up to 200 papers for that authorId.
3) KEEP ONLY papers where that exact authorId appears in the author list.
4) Write assets/publications.json. Always exit 0 so the workflow stays green.
"""

from __future__ import annotations
import json, pathlib, sys, re
from typing import List, Dict, Any

OUT_PATH = pathlib.Path("assets/publications.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

# ---- Tune these to your profile ----
NAME_QUERIES = [
    "Laura Díaz Sáez",
    "Laura Diaz Saez",  # ASCII fallback
]
AFFIL_HINTS = [
    "iqf", "csic",
    "warwick", "university of warwick",
]

# Optional: if you discover your exact S2 authorId, put it here to skip searching
PINNED_S2_AUTHOR_ID = ""  # e.g., "2092948527"  (leave empty to auto-detect)

BASE = "https://api.semanticscholar.org/graph/v1"

def log(msg: str):  # simple logger
    print(msg, flush=True)

def norm(s: str) -> str:
    # normalize with optional Unidecode if installed
    try:
        from unidecode import unidecode
        s = unidecode(s or "")
    except Exception:
        s = s or ""
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s

def safe_year(y):
    try:
        return int(y)
    except Exception:
        return 0

def normalize_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # de-dup by (title, year), then newest first, then title
    seen = set()
    dedup = []
    for it in items:
        key = (it.get("title","").strip().lower(), str(it.get("year","")).strip())
        if key in seen:
            continue
        seen.add(key)
        dedup.append(it)
    dedup.sort(key=lambda x: (safe_year(x.get("year")), (x.get("title") or "").lower()), reverse=True)
    return dedup

def write_output(items: List[Dict[str, Any]]):
    items = normalize_items(items or [])
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    log(f"✅ Wrote {len(items)} items -> {OUT_PATH}")

# ---------------- HTTP helpers ----------------
def http_get(path: str, params: Dict[str, Any], timeout=45) -> Any:
    import requests
    url = f"{BASE}{path}"
    r = requests.get(url, params=params, timeout=timeout, headers={
        "User-Agent": "lda-lab-pubs/1.0 (GitHub Actions; contact: maintainer)"
    })
    r.raise_for_status()
    return r.json()

# -------------- Author discovery --------------
def search_author_candidates(queries: List[str]) -> List[Dict[str, Any]]:
    cands = []
    for q in queries:
        try:
            data = http_get(
                "/author/search",
                {"query": q, "limit": 5, "fields": "name,authorId,aliases,paperCount"},
                timeout=45
            )
            got = data.get("data") or []
            log(f"• Search '{q}' → {len(got)} candidate(s)")
            cands.extend(got)
        except Exception as e:
            log(f"! Search error for '{q}': {e}")
    # de-dup by authorId
    uniq, seen = [], set()
    for c in cands:
        aid = c.get("authorId")
        if aid and aid not in seen:
            uniq.append(c)
            seen.add(aid)
    return uniq

def fetch_author_detail(author_id: str) -> Dict[str, Any]:
    try:
        return http_get(f"/author/{author_id}", {"fields": "name,authorId,aliases,affiliations"})
    except Exception as e:
        log(f"! Detail fetch failed for {author_id}: {e}")
        return {}

def score_candidate(c: Dict[str, Any]) -> float:
    score = 0.0
    name = norm(c.get("name",""))
    aliases = [norm(a) for a in (c.get("aliases") or [])]
    # name similarity
    for q in NAME_QUERIES:
        nq = norm(q)
        if nq == name or nq in aliases:
            score += 3.0
        elif nq.split()[-1] in name:
            score += 1.0
    # affiliations hints
    det = fetch_author_detail(c.get("authorId","")) if c.get("authorId") else {}
    affs = norm(" ".join(det.get("affiliations") or []))
    for hint in AFFIL_HINTS:
        if hint in affs:
            score += 2.0
    # light weight on paperCount
    pc = float(c.get("paperCount") or 0)
    score += min(pc / 100.0, 2.0)
    return score

def pick_author_id() -> str:
    if PINNED_S2_AUTHOR_ID:
        log(f"Using pinned S2 authorId: {PINNED_S2_AUTHOR_ID}")
        return PINNED_S2_AUTHOR_ID
    cands = search_author_candidates(NAME_QUERIES)
    if not cands:
        return ""
    scored = [(score_candidate(c), c) for c in cands]
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    aid = best.get("authorId") or ""
    log(f"→ Selected authorId={aid} ({best.get('name')})")
    return aid

# -------------- Fetch papers -----------------
def fetch_papers_for_author(author_id: str) -> List[Dict[str, Any]]:
    fields = "title,venue,year,url,openAccessPdf,citationCount,authors"
    data = http_get(f"/author/{author_id}/papers",
                    {"limit": 200, "offset": 0, "sort": "year:desc", "fields": fields})
    papers = data.get("data") or []
    items: List[Dict[str, Any]] = []
    kept = 0
    for p in papers:
        authors = p.get("authors") or []
        # STRICT: keep only if our author is actually in the author list
        if author_id not in {a.get("authorId") for a in authors if a.get("authorId")}:
            continue
        title = p.get("title") or ""
        venue = p.get("venue") or ""
        year = p.get("year") or ""
        url_pref = (p.get("openAccessPdf") or {}).get("url") or (p.get("url") or "")
        author_names = ", ".join([a.get("name","") for a in authors if a.get("name")])
        cites = int(p.get("citationCount") or 0)
        items.append({
            "title": title,
            "authors": author_names,
            "year": year,
            "venue": venue,
            "url": url_pref,
            "eprint_url": (p.get("openAccessPdf") or {}).get("url") or "",
            "cited_by": cites,
            "source": "semantic_scholar",
        })
        kept += 1
    log(f"• Retrieved {len(papers)} papers; kept {kept} with exact authorId.")
    return items

# --------------------------- main ---------------------------
def main():
    aid = pick_author_id()
    if not aid:
        log("! Could not determine Semantic Scholar authorId — writing empty list.")
        write_output([])
        return
    try:
        items = fetch_papers_for_author(aid)
    except Exception as e:
        log(f"! Paper fetch error: {e}")
        items = []
    write_output(items)

if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except Exception as e:
        log(f"!! Fatal: {e}")
        try: write_output([])
        except: pass
        sys.exit(0)
