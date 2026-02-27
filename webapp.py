"""Simple FastAPI server for PaperRadar web frontend."""

import json
import os
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", BASE_DIR / "reports"))
JSON_DIR = REPORTS_DIR / "json"
WEB_DIR = BASE_DIR / "web"
PDF_CACHE_DIR = Path(os.getenv("PDF_CACHE_DIR", BASE_DIR / "cache" / "pdfs"))
FAVORITES_FILE = Path(os.getenv("FAVORITES_FILE", BASE_DIR / "cache" / "favorites.json"))

app = FastAPI(title="PaperRadar Web")
_favorites_lock = Lock()


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


class FavoriteUpsertRequest(BaseModel):
    """Payload for creating/updating a favorite paper."""

    paper_id: str
    title: str = ""
    pdf_url: str = ""
    abstract_url: str = ""
    source: str = ""
    primary_category: str = ""
    authors: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    report_date: str = ""


def _empty_favorites_payload() -> dict:
    return {"favorites": {}, "last_updated": None}


def _load_favorites_payload() -> dict:
    if not FAVORITES_FILE.exists():
        return _empty_favorites_payload()

    try:
        data = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _empty_favorites_payload()

    favorites = data.get("favorites", {})
    if not isinstance(favorites, dict):
        favorites = {}

    return {
        "favorites": favorites,
        "last_updated": data.get("last_updated"),
    }


def _save_favorites_payload(payload: dict) -> None:
    FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload["last_updated"] = datetime.utcnow().isoformat()
    FAVORITES_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalize_favorite_item(paper_id: str, raw: dict) -> dict:
    authors = raw.get("authors", [])
    if not isinstance(authors, list):
        authors = []

    matched_keywords = raw.get("matched_keywords", [])
    if not isinstance(matched_keywords, list):
        matched_keywords = []

    return {
        "paper_id": paper_id,
        "title": str(raw.get("title", "")).strip(),
        "pdf_url": str(raw.get("pdf_url", "")).strip(),
        "abstract_url": str(raw.get("abstract_url", "")).strip(),
        "source": str(raw.get("source", "")).strip(),
        "primary_category": str(raw.get("primary_category", "")).strip(),
        "authors": [str(a).strip() for a in authors if str(a).strip()],
        "matched_keywords": [str(k).strip() for k in matched_keywords if str(k).strip()],
        "report_date": str(raw.get("report_date", "")).strip(),
        "favorited_at": str(raw.get("favorited_at", "")).strip(),
        "updated_at": str(raw.get("updated_at", "")).strip(),
    }


def _list_report_files() -> list[Path]:
    if not JSON_DIR.exists():
        return []
    # Support both old (arxiv-daily-) and new (paper-radar-) naming
    files = list(JSON_DIR.glob("paper-radar-*.json"))
    files.extend(JSON_DIR.glob("arxiv-daily-*.json"))
    return sorted(files, reverse=True)


def _date_from_filename(path: Path) -> Optional[str]:
    name = path.stem
    if name.startswith("paper-radar-"):
        return name.replace("paper-radar-", "")
    if name.startswith("arxiv-daily-"):
        return name.replace("arxiv-daily-", "")
    return None


def _load_report(date: Optional[str] = None) -> dict:
    if date:
        # Try new naming first, then old
        target = JSON_DIR / f"paper-radar-{date}.json"
        if not target.exists():
            target = JSON_DIR / f"arxiv-daily-{date}.json"
        if not target.exists():
            raise HTTPException(status_code=404, detail="Report not found")
        return json.loads(target.read_text(encoding="utf-8"))

    files = _list_report_files()
    if not files:
        raise HTTPException(status_code=404, detail="No reports available")
    return json.loads(files[0].read_text(encoding="utf-8"))


def _sanitize_paper_id(paper_id: str) -> str:
    return str(paper_id or "").strip().replace("/", "_").replace(":", "_")


def _sanitize_source(source: Optional[str]) -> str:
    if not source:
        return ""
    return str(source).strip().replace(" ", "_").replace("/", "_").lower()


def _find_cached_pdf(
    paper_id: str,
    date: Optional[str] = None,
    source: Optional[str] = None,
) -> Optional[Path]:
    """Resolve a cached PDF path using known cache layouts."""
    safe_id = _sanitize_paper_id(paper_id)
    if not safe_id or not PDF_CACHE_DIR.exists():
        return None

    safe_source = _sanitize_source(source)
    candidates: list[Path] = []

    # Preferred layout: cache/pdfs/{date}/{source}/{paper_id}.pdf
    if date and safe_source:
        candidates.append(PDF_CACHE_DIR / str(date) / safe_source / f"{safe_id}.pdf")
    # Other supported layouts
    if date:
        candidates.append(PDF_CACHE_DIR / str(date) / f"{safe_id}.pdf")
    if safe_source:
        candidates.append(PDF_CACHE_DIR / safe_source / f"{safe_id}.pdf")
    candidates.append(PDF_CACHE_DIR / f"{safe_id}.pdf")

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists() and candidate.is_file():
            return candidate

    # Fallback: search recursively for legacy/variant layouts.
    # Triggered only when direct candidates miss.
    for matched in PDF_CACHE_DIR.rglob(f"{safe_id}.pdf"):
        if matched.is_file():
            return matched

    return None


def _static_asset_version(filename: str) -> str:
    """Return a stable cache-busting version derived from file mtime."""
    path = WEB_DIR / filename
    if not path.exists():
        return "0"
    return str(int(path.stat().st_mtime))


@app.get("/")
def index():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css_version = _static_asset_version("styles.css")
    js_version = _static_asset_version("app.js")
    html = html.replace(
        '/static/styles.css"',
        f'/static/styles.css?v={css_version}"',
    )
    html = html.replace(
        '/static/app.js"',
        f'/static/app.js?v={js_version}"',
    )
    return HTMLResponse(html)


@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/dates")
def list_dates():
    dates = []
    for path in _list_report_files():
        date = _date_from_filename(path)
        if date:
            dates.append(date)
    return dates


@app.get("/api/report")
def get_report(date: Optional[str] = None):
    return _load_report(date)


@app.get("/api/favorites")
def list_favorites():
    with _favorites_lock:
        payload = _load_favorites_payload()
        favorites = payload.get("favorites", {})
        items = [
            _normalize_favorite_item(paper_id, raw)
            for paper_id, raw in favorites.items()
            if str(paper_id).strip() and isinstance(raw, dict)
        ]

    items.sort(key=lambda item: item.get("favorited_at", ""), reverse=True)
    paper_ids = [item["paper_id"] for item in items if item.get("paper_id")]

    return {
        "paper_ids": paper_ids,
        "items": items,
    }


@app.put("/api/favorites")
def upsert_favorite(payload: FavoriteUpsertRequest):
    paper_id = str(payload.paper_id or "").strip()
    if not paper_id:
        raise HTTPException(status_code=400, detail="paper_id is required")

    now = datetime.utcnow().isoformat()

    with _favorites_lock:
        favorites_payload = _load_favorites_payload()
        favorites = favorites_payload.get("favorites", {})
        existing = favorites.get(paper_id, {})
        if not isinstance(existing, dict):
            existing = {}

        item = {
            "paper_id": paper_id,
            "title": str(payload.title or "").strip(),
            "pdf_url": str(payload.pdf_url or "").strip(),
            "abstract_url": str(payload.abstract_url or "").strip(),
            "source": str(payload.source or "").strip(),
            "primary_category": str(payload.primary_category or "").strip(),
            "authors": [str(a).strip() for a in payload.authors if str(a).strip()],
            "matched_keywords": [
                str(k).strip() for k in payload.matched_keywords if str(k).strip()
            ],
            "report_date": str(payload.report_date or "").strip(),
            "favorited_at": str(existing.get("favorited_at", now)).strip() or now,
            "updated_at": now,
        }
        favorites[paper_id] = item
        favorites_payload["favorites"] = favorites
        _save_favorites_payload(favorites_payload)

    return {"favorited": True, "item": item}


@app.delete("/api/favorites")
def remove_favorite(paper_id: str):
    safe_id = str(paper_id or "").strip()
    if not safe_id:
        raise HTTPException(status_code=400, detail="paper_id is required")

    removed = False
    with _favorites_lock:
        favorites_payload = _load_favorites_payload()
        favorites = favorites_payload.get("favorites", {})
        if safe_id in favorites:
            favorites.pop(safe_id, None)
            favorites_payload["favorites"] = favorites
            _save_favorites_payload(favorites_payload)
            removed = True

    return {"favorited": False, "paper_id": safe_id, "removed": removed}


@app.get("/api/local-pdf")
def get_local_pdf(
    paper_id: str,
    date: Optional[str] = None,
    source: Optional[str] = None,
    fallback_url: Optional[str] = None,
):
    """
    Open locally cached PDF when available, otherwise redirect to fallback URL.
    """
    pdf_path = _find_cached_pdf(paper_id=paper_id, date=date, source=source)
    if pdf_path:
        return FileResponse(str(pdf_path), media_type="application/pdf")

    if fallback_url:
        safe = fallback_url.strip()
        if safe.startswith("http://") or safe.startswith("https://"):
            return RedirectResponse(url=safe, status_code=307)

    raise HTTPException(status_code=404, detail="Local PDF not found")


@app.get("/favicon.ico")
def favicon():
    icon_path = WEB_DIR / "favicon.ico"
    if icon_path.exists():
        return FileResponse(str(icon_path))
    raise HTTPException(status_code=404, detail="Not found")
