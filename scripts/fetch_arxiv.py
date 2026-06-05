"""Fetch a corpus of arXiv PDFs from figure-heavy categories.

Queries the arXiv API across a few categories (cs.LG, stat.ML, cs.CV, eess.SP),
collects ids (over-fetching so >=150 survive download), downloads each PDF with
``curl -L --fail`` into a target dir (polite: small parallelism + sleep), keeps
only PDFs that open in ``fitz``, and writes a ``SOURCES.md`` (id->title).

Run: uv run python scripts/fetch_arxiv.py --out "$SCRATCH/pdf_chart2table/pdfs"
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time

import fitz

_API = "https://export.arxiv.org/api/query"
_UA = "pdf-chart2table-validation/0.1 (damien.scieur@gmail.com)"
_CATS = ["cs.LG", "stat.ML", "cs.CV", "eess.SP"]
_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
_ID = re.compile(r"<id>http://arxiv.org/abs/([^<]+)</id>")
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
# The metadata API (export.arxiv.org) is aggressively rate-limited; the HTML
# listing pages on arxiv.org are not, so we gather ids from those instead.
_ABS = re.compile(r"arXiv:(\d{4}\.\d{4,5})")


def _recent_months(n: int) -> list[str]:
    """Last ``n`` year-months as arXiv listing tokens, e.g. '2026-06'."""
    import datetime
    out, d = [], datetime.date.today().replace(day=1)
    for _ in range(n):
        out.append(f"{d.year:04d}-{d.month:02d}")
        d = (d.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    return out


def _listing_ids(cat: str, ym: str, show: int = 2000) -> list[str]:
    url = f"https://arxiv.org/list/{cat}/{ym}?skip=0&show={show}"
    for attempt in range(4):
        p = subprocess.run(["curl", "-sS", "-m", "90", "-A", _UA, url],
                           capture_output=True, text=True)
        ids = _ABS.findall(p.stdout or "")
        if ids:
            # de-dup preserving order
            seen, uniq = set(), []
            for a in ids:
                if a not in seen:
                    seen.add(a); uniq.append(a)
            return uniq
        time.sleep(10.0 * (attempt + 1))
    return []


def gather_ids_listing(target: int, cats: list[str], months: list[str]) -> dict[str, str]:
    """Gather ids from arxiv.org HTML listing pages (avoids the rate-limited API)."""
    ids: dict[str, str] = {}
    for ym in months:
        for cat in cats:
            if len(ids) >= target:
                return ids
            for aid in _listing_ids(cat, ym):
                ids.setdefault(aid, "")
            print(f"  gathered {len(ids)} ids ({cat} {ym})", flush=True)
            time.sleep(3.0)
    return ids


def _query(cat: str, start: int, n: int) -> list[tuple[str, str]]:
    url = (f"{_API}?search_query=cat:{cat}&start={start}&max_results={n}"
           "&sortBy=submittedDate&sortOrder=descending")
    xml = ""
    for attempt in range(6):
        p = subprocess.run(
            ["curl", "-sS", "-m", "60", "-A", _UA, "-w", "\n%{http_code}", url],
            capture_output=True, text=True,
        )
        body = p.stdout or ""
        code = body.rsplit("\n", 1)[-1].strip()
        if code == "200" and "<entry>" in body:
            xml = body
            break
        # HTTP 429 (rate-limited) / 000 (timeout) / empty -> long backoff.
        wait = 60.0 if code in ("429", "000", "") else 20.0
        print(f"    retry {cat}@{start} (HTTP {code or '?'}), wait {wait:.0f}s",
              flush=True)
        time.sleep(wait * (1 + 0.5 * attempt))
    out = []
    for block in _ENTRY.findall(xml):
        mid = _ID.search(block)
        mt = _TITLE.search(block)
        if not mid:
            continue
        arxiv_id = mid.group(1).split("v")[0]  # strip version suffix
        title = re.sub(r"\s+", " ", mt.group(1)).strip() if mt else ""
        out.append((arxiv_id, title))
    return out


def gather_ids(target: int, cats: list[str] | None = None) -> dict[str, str]:
    cats = cats or _CATS
    ids: dict[str, str] = {}
    # Paginate each category up to 2 pages; round-robin keeps the mix balanced.
    # Few queries + wide spacing keeps us under arXiv's rate limit (HTTP 429).
    for start in (0, 100):
        for cat in cats:
            if len(ids) >= target:
                return ids
            batch = _query(cat, start, 100)
            for aid, title in batch:
                ids.setdefault(aid, title)
            print(f"  gathered {len(ids)} ids so far ({cat}@{start})", flush=True)
            time.sleep(15.0)  # wide spacing to respect the API rate limit
    return ids


def _download(aid: str, out: str) -> bool:
    dest = os.path.join(out, f"{aid}.pdf")
    if os.path.exists(dest) and _valid(dest):
        return True
    url = f"https://arxiv.org/pdf/{aid}"
    rc = subprocess.run(
        ["curl", "-sL", "--fail", "--max-time", "120", "-A", _UA,
         "-o", dest, url],
        capture_output=True,
    ).returncode
    if rc != 0 or not _valid(dest):
        if os.path.exists(dest):
            os.remove(dest)
        return False
    return True


def _valid(path: str) -> bool:
    try:
        with fitz.open(path) as d:
            return d.page_count > 0
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--target", type=int, default=190)
    ap.add_argument("--need", type=int, default=150)
    ap.add_argument("--cats", default=",".join(_CATS),
                    help="comma-separated arXiv categories")
    ap.add_argument("--months", type=int, default=4,
                    help="how many recent months of listings to scan")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cats = [c.strip() for c in args.cats.split(",") if c.strip()]
    months = _recent_months(args.months)

    print(f"gathering ~{args.target} ids from {cats} over {months} (listing pages) ...")
    ids = gather_ids_listing(args.target, cats, months)
    print(f"got {len(ids)} unique ids; downloading ...")

    ok: dict[str, str] = {}
    for i, (aid, title) in enumerate(ids.items(), 1):
        if _download(aid, args.out):
            ok[aid] = title
        if i % 10 == 0:
            print(f"  {i}/{len(ids)} tried, {len(ok)} valid")
        time.sleep(0.5)  # polite spacing between downloads

    print(f"DONE: {len(ok)} valid PDFs in {args.out}")
    with open(os.path.join(args.out, "SOURCES.md"), "w") as f:
        f.write("# arXiv validation corpus\n\n")
        f.write(f"Categories: {', '.join(cats)}. Fetched via arXiv API "
                "(submittedDate desc). PDFs: https://arxiv.org/pdf/<id>.\n\n")
        f.write(f"Valid PDFs: {len(ok)}\n\n| arXiv id | title |\n|---|---|\n")
        for aid, title in sorted(ok.items()):
            f.write(f"| {aid} | {title.replace('|', '/')} |\n")

    if len(ok) < args.need:
        raise SystemExit(f"ERROR: only {len(ok)} valid PDFs (< {args.need})")


if __name__ == "__main__":
    main()
