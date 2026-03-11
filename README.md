# LinkedIn & Reddit Scrapers

Isolated Python scrapers for LinkedIn and Reddit. Fully self-contained — move this folder anywhere and work on it independently.

## Setup

```bash
cd scrapers-linkedin-reddit
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
```

## Run

```bash
# LinkedIn (requires Chrome/Chromium + LINKEDIN_EMAIL/LINKEDIN_PASSWORD in .env)
python main.py linkedin "web developer designer"

# Reddit (requires REDDIT_CLIENT_ID in .env)
python main.py reddit "graphic designer"

# Optional: time window in hours (default 24)
python main.py reddit "marketing" --since 48
```

Output is JSON to stdout: `{"posts": [...]}`.

## Environment Variables

| Variable | Platform | Description |
|----------|----------|-------------|
| `LINKEDIN_EMAIL` | LinkedIn | Login email |
| `LINKEDIN_PASSWORD` | LinkedIn | Login password |
| `LINKEDIN_HEADLESS` | LinkedIn | `true` (default) or `false` for visible browser |
| `REDDIT_CLIENT_ID` | Reddit | Required. Create at reddit.com/prefs/apps |
| `REDDIT_CLIENT_SECRET` | Reddit | Optional (script app) |
| `REDDIT_USERNAME` | Reddit | Optional (script app) |
| `REDDIT_PASSWORD` | Reddit | Optional (script app) |
| `REDDIT_RATE_LIMIT_DELAY` | Reddit | Seconds between requests (default 2.5) |

## Syncing Back to Main Project

When done improving logic, copy the updated scrapers back:

```bash
cp linkedin.py ../scraper/scrapers/linkedin.py
cp reddit.py ../scraper/scrapers/reddit.py
```

Or update the main project's `scraper/main.py` and `backend` to import from this folder instead.
