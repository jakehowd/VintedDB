# Vinted Sales Tracker

A self-hosted web application for tracking item sales, historical prices, and newly listed deals on Vinted UK (`vinted.co.uk`).

---

## Features

- **Price & Volume Tracking**: Records historical sales data and generates charts across days, weeks, months, or years with minimum, average, and maximum price corridors.
- **Deal Sniper**: Continuously monitors selected searches and alerts you when newly uploaded listings match your price limits. Supports global price caps and per-item overrides.
- **Fake & Spam Filters**: Flags likely replicas and spam listings using configurable rules:
  - Seller review counts and active listing limits
  - "New with tags" flags from low-feedback accounts
  - Duplicate photo detection using image hashing (`dHash`)
  - Youth/child size exclusions
- **Database Explorer**: Search, inspect, filter, and bulk-delete recorded listings. View breakdowns of active vs sold items and blocked replicas.
- **Anti-Ban & Proxy Support**: Built-in rotating proxy pool support, custom user-agent rotation, request throttling, and a headless browser session harvester for Cloudflare cookies.
- **Multi-User Isolation**: Each user profile has its own isolated searches, sniper targets, proxy lists, and settings. Default local login: `ADMIN` / `ADMIN`.
- **Fast Web UI**: Single-page dashboard built with vanilla JavaScript and Tailwind CSS, featuring keyboard navigation, responsive layout, and dark mode.

---

## Tech Stack

- **Backend**: Python 3.10+, FastAPI, SQLAlchemy, APScheduler
- **Database**: SQLite (WAL mode, file-based at `vinted_tracker.db`)
- **Scraper / Network**: `vinted-scraper`, HTTPX, headless Chrome session generator
- **Frontend**: HTML5, Tailwind CSS, Chart.js, Lucide Icons

---

## Getting Started

### 1. Prerequisites

- Python 3.10 or higher
- *(Optional)* Google Chrome (if using the automated session harvester)
- *(Optional)* Tesseract OCR (`brew install tesseract` on macOS / `apt install tesseract-ocr` on Linux)

### 2. Setup

```bash
# Clone the repository
git clone <repo-url>
cd "Vinted tracker"

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Run the App

```bash
python run.py
```

Or run directly with `uvicorn`:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser and navigate to `http://127.0.0.1:8000`.

### 4. Default Login

The application comes pre-seeded with a default local admin account:

- **Username**: `ADMIN`
- **Password**: `ADMIN`

You can also create a new separate account directly from the register tab on the login screen.

---

## Configuration

Settings can be managed directly in the web dashboard or configured via environment variables:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./vinted_tracker.db` | SQLite connection string |
| `DB_PATH` | `vinted_tracker.db` | Path to the SQLite database file |
| `POLL_INTERVAL_MINUTES` | `30` | Interval for the periodic background scraper |
| `VINTED_PROXIES` | `""` | Comma- or newline-separated proxy URLs (`http://user:pass@host:port`) |
| `VINTED_COOKIE` | `""` | Custom Vinted browser cookie string |

---

## API Summary

All endpoints except authentication routes require a session token (`Authorization: Bearer <token>`).

### Auth (`/api/auth`)
- `POST /api/auth/login`: Log in with username and password
- `POST /api/auth/register`: Create a new account
- `GET /api/auth/me`: Get current user info and preferences
- `POST /api/auth/logout`: Log out of current session
- `POST /api/auth/change-password`: Update account password

### Searches (`/api/searches`)
- `GET /api/searches`: List tracked searches and item counts
- `POST /api/searches`: Add a new search term
- `PUT /api/searches/{id}`: Update filters and anti-rep settings
- `DELETE /api/searches/{id}`: Delete a search and its data
- `POST /api/searches/{id}/refresh`: Trigger an immediate live scrape
- `POST /api/searches/{id}/toggle-active`: Pause or resume background tracking

### Analytics (`/api/analytics`)
- `GET /api/analytics/{id}/sales-over-time`: Volume and price averages over `day`, `week`, `month`, or `year`
- `GET /api/analytics/{id}/summary`: Summary statistics (total sold, avg price, gross volume)
- `GET /api/analytics/{id}/recent-sales`: Paginated feed of recorded sales

### Deal Sniper (`/api/sniper`)
- `GET /api/sniper/status`: Check current sniper state, active targets, and deals
- `GET /api/sniper/targets-info`: Target searches with price limits and sold averages
- `POST /api/sniper/start`: Start background deal monitoring
- `POST /api/sniper/stop`: Stop deal monitoring
- `POST /api/sniper/dismiss-deal`: Dismiss a deal from the feed
- `POST /api/sniper/clear-deals`: Clear all deals

### Anti-Ban & Proxies (`/api/antiban`)
- `GET /api/antiban/status`: Current proxy pool and circuit breaker status
- `POST /api/antiban/config`: Save proxy list and session cookies
- `POST /api/antiban/test`: Run a connection probe against Vinted
- `POST /api/antiban/reset-cooldown`: Reset circuit breaker cooldown
- `POST /api/antiban/harvest-session`: Open headless browser to fetch fresh Cloudflare session cookies

### Database Explorer (`/api/database`)
- `GET /api/database/analytics`: Overview counts and clean vs replica stats
- `GET /api/database/items`: Paginated and filterable listings table
- `POST /api/database/purge-item/{id}`: Delete a single listing
- `POST /api/database/bulk-purge`: Delete selected listings in bulk
- `POST /api/database/reclassify-item/{id}`: Toggle clean / replica flag
- `POST /api/database/replicas/purge`: Delete all identified replicas

### Scraper Controls (`/api/scraper`)
- `POST /api/scraper/batch-scrape`: Trigger an immediate scrape on selected items
- `GET /api/scraper/scheduler/status`: Poller schedule status and next run time
- `POST /api/scraper/scheduler/settings`: Set poll intervals and auto-scrape mode

---

## License

MIT
