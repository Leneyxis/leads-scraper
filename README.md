# LinkedIn & Reddit Scrapers

Isolated Python scrapers for LinkedIn and Reddit. Fully self-contained — move this folder anywhere and work on it independently.

## Setup

```bash
cd scrapers-linkedin-reddit
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# Edit .env with your credentials
```

## Run

```bash
# LinkedIn (requires Playwright Chromium: `playwright install chromium` + LINKEDIN_EMAIL/LINKEDIN_PASSWORD in .env)
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
| `LINKEDIN_LI_AT` | LinkedIn | Session cookie (paste from DevTools → Application → Cookies) — easiest |
| `LINKEDIN_COOKIES` | LinkedIn | JSON array of cookies: `[{"name":"li_at","value":"..."}]` |
| `LINKEDIN_EMAIL` | LinkedIn | Login email (fallback if no cookies) |
| `LINKEDIN_PASSWORD` | LinkedIn | Login password (fallback if no cookies) |
| `LINKEDIN_HEADLESS` | LinkedIn | `true` (default) or `false` for visible browser |
| `REDDIT_CLIENT_ID` | Reddit | Required. Create at reddit.com/prefs/apps |
| `REDDIT_CLIENT_SECRET` | Reddit | Optional (script app) |
| `REDDIT_USERNAME` | Reddit | Optional (script app) |
| `REDDIT_PASSWORD` | Reddit | Optional (script app) |
| `REDDIT_RATE_LIMIT_DELAY` | Reddit | Seconds between requests (default 2.5) |

## Deploy to Railway

1. **Push to GitHub** (if not already):
   ```bash
   git add . && git commit -m "Add Railway deploy" && git push
   ```

2. **Create Railway project**: Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub repo → select this repo.

3. **Add environment variables** in Railway dashboard → Variables:
   - `REDDIT_CLIENT_ID` (required for Reddit)
   - `LINKEDIN_LI_AT` or `LINKEDIN_EMAIL` + `LINKEDIN_PASSWORD` (for LinkedIn)
   - Optional: `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`, `LINKEDIN_HEADLESS=true`

4. **Deploy**: Railway auto-detects the Dockerfile and deploys. It sets `PORT` automatically.

5. **Get URL**: Settings → Generate Domain to get your public API URL (e.g. `https://your-app.up.railway.app`).

**API endpoints:**
- `GET /api/health` — health check
- `GET /api/linkedin?keywords=developer&quantity=20`
- `GET /api/reddit?keywords=hiring&quantity=20`
- `POST /api/scrape` — see `/docs` for schema

---

## Syncing Back to Main Project

When done improving logic, copy the updated scrapers back:

```bash
cp linkedin.py ../scraper/scrapers/linkedin.py
cp reddit.py ../scraper/scrapers/reddit.py
```

Or update the main project's `scraper/main.py` and `backend` to import from this folder instead.
