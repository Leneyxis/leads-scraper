# API Testing

**Base URL:** `https://scraper-api-production-815e.up.railway.app`

## Quick test

**Health check**
```
GET /api/health
```

**Reddit** (keywords, quantity, time: hour/day/week/month/year/all)
```
GET /api/reddit?keywords=hiring&quantity=5&time_filter=day
```

**LinkedIn**
```
GET /api/linkedin?keywords=developer&quantity=5
```

**Both**
```
POST /api/scrape
Body: {"platform":"both","keywords":["hiring","developer"],"quantity":10}
```

## cURL examples

```bash
# Reddit
curl "https://scraper-api-production-815e.up.railway.app/api/reddit?keywords=hiring&quantity=5"

# LinkedIn
curl "https://scraper-api-production-815e.up.railway.app/api/linkedin?keywords=developer&quantity=5"

# Health
curl "https://scraper-api-production-815e.up.railway.app/api/health"
```

## Docs

Interactive docs: `https://scraper-api-production-815e.up.railway.app/docs`
