# Deployment Guide — Docker & Portainer

This guide walks you through deploying the LinkedIn & Reddit Scraper to a server with Docker and Portainer.

---

## Prerequisites

- Server with Docker and Portainer installed
- GitHub repo (or other way to get code onto the server)
- Credentials ready: LinkedIn `li_at` cookie, Reddit API credentials

---

## Option A: Deploy via Portainer (recommended)

### 1. Get the code on the server

**Option A1 — Git clone (if you have SSH access):**
```bash
cd /opt  # or your preferred directory
git clone https://github.com/YOUR_ORG/Jeevonix-scrapers-linkedin-reddit.git
cd Jeevonix-scrapers-linkedin-reddit
```

**Option A2 — Portainer “Add stack” from Git:**
- In Portainer: **Stacks** → **Add stack**
- Name: `scraper`
- Build method: **Git repository**
- Repository URL: `https://github.com/YOUR_ORG/Jeevonix-scrapers-linkedin-reddit`
- Compose path: `docker-compose.yml`
- Add environment variables (see step 2 below) in the **Environment variables** section

### 2. Create `.env` on the server

Create a `.env` file in the project root (same folder as `docker-compose.yml`):

```bash
cp .env.example .env
nano .env   # or vim, etc.
```

Fill in your credentials (minimum for Reddit: `REDDIT_CLIENT_ID`; for LinkedIn: `LINKEDIN_LI_AT`):

```
LINKEDIN_LI_AT=your_li_at_cookie_here
LINKEDIN_HEADLESS=true

REDDIT_CLIENT_ID=your_reddit_client_id
REDDIT_CLIENT_SECRET=your_reddit_client_secret
REDDIT_USERNAME=your_reddit_username
REDDIT_PASSWORD=your_reddit_password
```

### 3. Deploy the stack in Portainer

**If you cloned via SSH:**
- **Stacks** → **Add stack**
- Name: `scraper`
- Build method: **Web editor**
- Paste the contents of `docker-compose.yml`
- Under **Environment variables**, add each variable from `.env` (or leave empty and rely on `.env` if the stack runs from the server filesystem)

**If using Git repository:**
- Ensure the repo URL and compose path are correct
- Add environment variables in the stack form
- Click **Deploy the stack**

### 4. Expose the port

- The app listens on **port 3000**
- Ensure port 3000 is open in the server firewall (or use Cloudflare Tunnel — no ports needed)
- If using a reverse proxy (Nginx, Traefik, Caddy), point it to `http://localhost:3000`

### 5. Create the cft-network (if it doesn't exist)

```bash
docker network create cft-network
```

### 6. Cloudflare Tunnel (optional)

If using the included `cloudflared` container:

1. Go to Cloudflare Zero Trust → Access → Tunnels → Create a tunnel
2. Choose **Docker** and copy the tunnel token
3. Add to `.env`: `CLOUDFLARE_TUNNEL_TOKEN=your_token_here`
4. In the tunnel config, add a public hostname pointing to `http://scraper-api:3000`

### 7. Verify

- Open `http://YOUR_SERVER_IP:3000` — you should see the scraper UI
- Health check: `http://YOUR_SERVER_IP:3000/api/health`
- API docs: `http://YOUR_SERVER_IP:3000/docs`

---

## Option B: Deploy via command line (no Portainer)

```bash
cd /path/to/Jeevonix-scrapers-linkedin-reddit
cp .env.example .env
# Edit .env with your credentials
docker compose up -d --build
```

The app will be available at `http://localhost:3000`.

---

## Reverse proxy (optional)

To use a domain and HTTPS (e.g. `https://scraper.yourdomain.com`), put Nginx, Caddy, or Traefik in front:

**Nginx example:**
```nginx
server {
    listen 80;
    server_name scraper.yourdomain.com;
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 180s;  # LinkedIn scrapes can take 1–2 min
    }
}
```

Then use Let’s Encrypt (Certbot) for SSL.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Build fails (Playwright) | Ensure enough RAM (2GB+). Playwright installs Chromium. |
| LinkedIn “not configured” | Set `LINKEDIN_LI_AT` in `.env` (from DevTools → Application → Cookies). |
| Reddit rate limits | Add `REDDIT_RATE_LIMIT_DELAY=3` or use a proxy. |
| Request timeout | Increase proxy/load balancer timeout to 180s for LinkedIn. |
| Port 3000 not reachable | Open port in firewall: `ufw allow 3000` (Ubuntu). |

---

## Updating

```bash
cd /path/to/Jeevonix-scrapers-linkedin-reddit
git pull
docker compose up -d --build
```

In Portainer: **Stacks** → select `scraper` → **Pull and redeploy** (if using Git).
