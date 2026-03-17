# Docker image for Railway/Render - Python + Playwright (LinkedIn scraper)
FROM python:3.11-bookworm

WORKDIR /app

# Install Playwright and Chromium with system dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

# Railway/Render set PORT env var at runtime
EXPOSE 3000

CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-3000}
