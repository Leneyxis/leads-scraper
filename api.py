"""
FastAPI backend for testing Reddit and LinkedIn scrapers.
Run with: uvicorn api:app --reload
"""
import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from io import BytesIO

# Load .env before importing scrapers
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from reddit import scrape_reddit
from linkedin import scrape_linkedin

app = FastAPI(title="Scraper Test API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ScrapeRequest(BaseModel):
    platform: str = "reddit"  # reddit | linkedin | both
    keywords: List[str]
    quantity: int = 50
    time_filter: str = "day"  # Reddit: hour, day, week, month, year, all
    since_hours: int = 24     # LinkedIn: 24, 168, 720 (past-24h, past-week, past-month)


class ScrapeResponse(BaseModel):
    posts: list
    count: int
    error: Optional[str] = None


class ExportExcelRequest(BaseModel):
    posts: list


def _normalize_for_excel(posts: list) -> list:
    """Normalize posts to a unified schema for Excel."""
    rows = []
    for p in posts:
        row = {
            "platform": p.get("platform", ""),
            "username": p.get("username", ""),
            "post_url": p.get("post_url", ""),
            "post_content": p.get("post_content", ""),
            "scraped_at": p.get("scraped_at", ""),
            "title": p.get("title", ""),
            "subreddit": p.get("subreddit", ""),
            "created_utc": p.get("created_utc", ""),
        }
        rows.append(row)
    return rows


def _build_excel_bytes(posts: list) -> bytes:
    """Build Excel file from posts and return as bytes."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    rows = _normalize_for_excel(posts)
    wb = Workbook()
    ws = wb.active
    ws.title = "Posts"

    headers = ["platform", "username", "post_url", "post_content", "scraped_at", "title", "subreddit", "created_utc"]
    for col, h in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=h)

    for row_idx, row_data in enumerate(rows, 2):
        for col_idx, key in enumerate(headers, 1):
            val = row_data.get(key, "")
            if val is None:
                val = ""
            ws.cell(row=row_idx, column=col_idx, value=str(val))

    for col_idx, key in enumerate(headers, 1):
        max_len = len(key)
        for row_data in rows:
            val = row_data.get(key, "")
            max_len = min(max(max_len, len(str(val))), 80)
        ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 2

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@app.post("/api/scrape", response_model=ScrapeResponse)
def run_scrape(req: ScrapeRequest) -> ScrapeResponse:
    """Run Reddit and/or LinkedIn scraper with given parameters."""
    if not req.keywords:
        raise HTTPException(status_code=400, detail="At least one keyword is required")
    platform = (req.platform or "reddit").lower()
    if platform not in ("reddit", "linkedin", "both"):
        raise HTTPException(status_code=400, detail="Platform must be reddit, linkedin, or both")

    try:
        all_posts = []
        platforms_to_run = ["linkedin", "reddit"] if platform == "both" else [platform]

        for p in platforms_to_run:
            if p == "reddit":
                posts = scrape_reddit(
                    keywords=req.keywords,
                    quantity=req.quantity,
                    time_filter=req.time_filter,
                )
            else:
                posts = scrape_linkedin(
                    keywords=req.keywords,
                    quantity=req.quantity,
                    since_hours=req.since_hours,
                )
            all_posts.extend(posts)

        return ScrapeResponse(posts=all_posts, count=len(all_posts))
    except Exception as e:
        return ScrapeResponse(posts=[], count=0, error=str(e))


@app.post("/api/export-excel")
def export_excel(req: ExportExcelRequest):
    """Export posts to Excel file."""
    try:
        data = _build_excel_bytes(req.posts or [])
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"scraped_{ts}.xlsx"
        return StreamingResponse(
            BytesIO(data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed. Run: pip install openpyxl")


@app.get("/api/health")
def health():
    """Health check."""
    return {
        "status": "ok",
        "reddit_configured": bool(os.getenv("REDDIT_CLIENT_ID", "").strip()),
        "linkedin_configured": bool(os.getenv("LINKEDIN_EMAIL", "").strip() and os.getenv("LINKEDIN_PASSWORD", "").strip()),
    }


# Serve frontend (API routes above take precedence)
frontend_path = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
