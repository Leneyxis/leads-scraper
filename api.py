"""
FastAPI backend for testing Reddit and LinkedIn scrapers.
Run with: uvicorn api:app --reload
"""
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
    platform: str = "reddit"  # reddit | linkedin
    keywords: List[str]
    quantity: int = 50
    time_filter: str = "day"  # Reddit: hour, day, week, month, year, all
    since_hours: int = 24     # LinkedIn: 24, 168, 720 (past-24h, past-week, past-month)


class ScrapeResponse(BaseModel):
    posts: list
    count: int
    error: Optional[str] = None


@app.post("/api/scrape", response_model=ScrapeResponse)
def run_scrape(req: ScrapeRequest) -> ScrapeResponse:
    """Run Reddit or LinkedIn scraper with given parameters."""
    if not req.keywords:
        raise HTTPException(status_code=400, detail="At least one keyword is required")
    platform = (req.platform or "reddit").lower()
    if platform not in ("reddit", "linkedin"):
        raise HTTPException(status_code=400, detail="Platform must be reddit or linkedin")

    try:
        if platform == "reddit":
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
        return ScrapeResponse(posts=posts, count=len(posts))
    except Exception as e:
        return ScrapeResponse(posts=[], count=0, error=str(e))


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
