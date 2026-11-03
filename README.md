# Vinted UK Sales & Price Tracker 🇬🇧

A full-stack, multi-tenant intelligence and deal sniping platform designed for Vinted UK (`vinted.co.uk`). Features real-time deal sniping, AI profit gating, hierarchical vision classification, anti-replica fraud detection, Cloudflare session harvesting, and an Apple Fluid Web UI.

---

## Key Features

### ⚡ Real-Time Deal Sniper
- **Sub-Second Deal Capture**: Continuously monitors target searches on Vinted UK with sub-second poll cycles and polite jitter.
- **Granular Price Thresholds**: Set global default max buy prices, with per-item and per-variant price ceiling overrides.
- **Listing Freshness Filter**: Specify maximum listing age in minutes (e.g. only listings posted within the last 15 minutes).
- **Deal Management**: Dismiss individual deals or clear the live deal feed with per-user persistent state.
- **Live Sound & Desktop Alerts**: Instant audio and visual toast alerts when an underpriced item surfaces.

### 🛡️ Anti-Rep & Fraud Shield
- **Replica & Counterfeit Gate**: Blocks high-risk listings based on multi-layered heuristic patterns.
- **Condition & Tag Rules**: Filter out suspicious "New With Tags" items from unverified sellers.
- **Seller Reputation Protection**: Excludes zero-review or low-feedback sellers and flags accounts with suspicious bulk duplicate listings.
- **Perceptual Image Hashing (`dHash`)**: Detects stock photos and recycled listing images across searches using image hashing.
- **Child & Youth Size Filtering**: Automatically filters out youth/infant sizes that skew adult resale market data.
- **Retroactive Database Auditing**: Run retroactive scans across historical database items to purge newly identified counterfeit patterns.

### 🤖 AI Resale Profitability Gate & Multi-Provider LLM
- **Intelligent Hurdle Rates**: Enforces minimum projected net profit (£) and return on investment (ROI %) thresholds before qualifying an item as a snipeable deal.
- **Resale Tiers**: Automatically categorizes items into `GRAIL`, `PREMIUM`, `HIGH`, `STANDARD`, or `BUDGET` tiers with estimated market valuations.
- **Multi-Provider LLM Router**: Seamlessly integrates with:
  - Google Gemini API (`gemini-2.0-flash`, `gemini-1.5-pro`, etc.)
  - OpenAI (`gpt-4o`, `gpt-4o-mini`, etc.)
  - Anthropic Claude (`claude-3-5-sonnet`, etc.)
  - Groq (`llama-3.3-70b-versatile`)
  - Local Ollama & DeepSeek endpoints

### 👁️ Hierarchical Models, Exclusive Styles & Local Vision
- **Taxonomy Deep Research**: Automatically discovers product models and exclusive sub-styles (e.g. *Carhartt Detroit Jacket* → *Blanket Lined*, *Duck Canvas*, *Aztec Print*).
- **Multimodal Local Vision**:
  - **Visual Embeddings**: CLIP ViT-B/32 prototype centroids powered locally by `fastembed`.
  - **Badge & Tag OCR**: Text and signature extraction via `pytesseract` and native macOS Vision OCR.
  - **Fuzzy Token Matching**: Fast token ratio matching via `rapidfuzz`.
- **Hierarchical Filtering**: Filter charts, tables, and sniper targets by high-level Model or specific exclusive Sub-Style.

### 🌐 Anti-Ban IP Shield & Session Harvester
- **Proxy Pool Management**: Rotate residential or datacenter proxies with automatic failure switching.
- **Cloudflare Session Harvester**: Built-in automated headless browser harvester to capture valid `cf_clearance` and `access_token_web` cookies.
- **Adaptive Circuit Breaker**: Exponential backoff with jitter and cooldown states to protect your IP from rate limits and Cloudflare Turnstile challenges.
- **Quick Status Bar**: One-click proxy status check and connection probe directly in the top navigation bar.

### 📊 Multi-Period Analytics & Price Corridors
- **Time Bucket Aggregation**: Interactive graphs for sales volume and average prices grouped by:
  - **Days** (Daily momentum)
  - **Weeks** (Weekly sales cycle)
  - **Months** (Monthly trends)
  - **Years** (Multi-year macro trends)
- **Price Corridor Bands**: Visualizes average sold price alongside historical minimum and maximum price envelopes.

### 🗄️ Full-Page Database Explorer
- **Live Search & Filters**: Filter captured items by tracked search, clean vs. replica, sold vs. active, brand, model/style, and date.
- **Bulk Actions**: Purge selected items, re-classify tags, retro-scan replicas, and manually assign model/style tags.
- **Replica Analytics**: Visual breakdown of blocked replica reasons and monthly counterfeit rates.

### 🔒 Multi-Tenant User Isolation
- **Zero Cross-Talk**: Searches, items, sniper targets, proxy configs, session cookies, AI keys, and dismissed deals are strictly isolated per user profile.
- **Hardened Authentication**: Constant-time password verification (`scrypt`), brute-force login rate limiting, and 30-day session tokens.
- **Default Admin Account**: Auto-seeds `ADMIN` / `ADMIN` on the first launch.

### 🍏 Apple Fluid Web UI
- **Analytical Spring Physics**: Custom damped harmonic oscillator (`FluidSpring`) delivering velocity handoff, momentum projection, and interruptible transitions.
- **Low-Latency Press Physics**: `touch-action: manipulation` with instant scale feedback on pointerdown.
- **Swipe-to-Dismiss Gestures**: Direct touch/pointer manipulation with spring recovery and rubber-banding.
- **Dark Glass Vibrancy**: Multi-tiered translucent materials (`backdrop-filter: blur(28px)`), subtle borders, and focus scrims.
- **Accessibility**: Full `prefers-reduced-motion` compliance.

---

## Architecture & Tech Stack

```
┌─────────────────────────────────────────────────────────────┐
│                      Apple Fluid UI                         │
│       Vanilla JS • Tailwind CSS • Chart.js • Lucide         │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP / JSON (Bearer Token)
┌──────────────────────────────▼──────────────────────────────┐
│                    FastAPI Backend (app/)                    │
│  ├─ Auth & Sessions         ├─ Deal Sniper Engine           │
│  ├─ Analytics & Charts      ├─ Anti-Ban & Session Harvester │
│  ├─ Database Explorer       ├─ Hierarchical Variants        │
│  └─ Anti-Rep Fraud Shield   └─ LLM Router & Vision Engine   │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
┌──────────────▼──────────────┐┌──────────────▼───────────────┐
│     SQLite Database         ││   Vinted UK & External APIs  │
│ (vinted_tracker.db, WAL)    ││ • vinted.co.uk (Scraper/TLS) │
│ • Multi-tenant Users        ││ • Proxies (Rotating Pool)    │
│ • Searches & Variants       ││ • LLMs (Gemini/OpenAI/Claude)│
│ • Sold Items & Photo Hashes ││ • Cloudflare Turnstile Bypass│
└─────────────────────────────┘└──────────────────────────────┘
```

- **Backend**: Python 3.10+ / FastAPI / SQLAlchemy / APScheduler / Pydantic v2
- **Vision & ML**: FastEmbed (CLIP ViT-B/32), PyTesseract / macOS Vision OCR, OpenCV Headless, RapidFuzz
- **Scraping & Camouflage**: `vinted-scraper`, HTTPX, Headless Chrome Session Harvester
- **Database**: SQLite (file-based `./vinted_tracker.db` with automated migrations)
- **Frontend**: HTML5, Tailwind CSS, Chart.js, Lucide Icons, FluidSpring solver

---

## Installation & Setup

### 1. Prerequisites
- **Python 3.10 or higher**
- *(Optional)* **Tesseract OCR**: Required for local badge/tag reading (`brew install tesseract` on macOS, `apt install tesseract-ocr` on Ubuntu/Debian).
- *(Optional)* **Google Chrome**: Required if using the automated Cloudflare session harvester.

### 2. Clone & Install Dependencies
```bash
git clone <repo-url>
cd "Vinted tracker"

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install required packages
pip install -r requirements.txt
```

### 3. Run the Application
Start the development server:
```bash
python run.py
```
Or directly via `uvicorn`:
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 4. Access the Dashboard
1. Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.
2. Sign in with the pre-seeded administrator credentials:
   - **Username**: `ADMIN`
   - **Password**: `ADMIN`
   *(Or register a new private profile from the login screen).*

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./vinted_tracker.db` | SQLAlchemy connection string |
| `DB_PATH` | `vinted_tracker.db` | Relative or absolute path to SQLite database |
| `POLL_INTERVAL_MINUTES` | `30` | Default periodic poller interval |
| `VINTED_PROXIES` | `""` | Comma- or newline-separated proxy URLs (`http://user:pass@host:port`) |
| `VINTED_COOKIE` | `""` | Fallback browser session cookies |

*(Note: Proxies, cookies, AI keys, and scheduler preferences can also be configured directly per-user in the web UI).*

---

## API Reference

All protected endpoints require an `Authorization: Bearer <token>` header or active session cookie.

### 🔑 Authentication (`/api/auth`)
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Authenticate with username and password |
| `POST` | `/api/auth/register` | Register a new user profile |
| `GET` | `/api/auth/me` | Fetch current authenticated user profile & preferences |
| `POST` | `/api/auth/logout` | Invalidate current session |
| `POST` | `/api/auth/logout-all` | Invalidate all active sessions for current user |
| `POST` | `/api/auth/change-password` | Update current account password |
| `GET` | `/api/auth/preferences` | Retrieve user preferences |
| `POST` | `/api/auth/preferences` | Update user preferences |

### 🔍 Searches (`/api/searches`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/searches` | List tracked items with item counts and variant metadata |
| `POST` | `/api/searches` | Add a new search term to monitor |
| `PUT` | `/api/searches/{id}` | Update search filters (brand, category, exclusion rules) |
| `DELETE` | `/api/searches/{id}` | Delete a tracked item and all its associated data |
| `POST` | `/api/searches/{id}/refresh` | Trigger an immediate live scrape on Vinted UK |
| `POST` | `/api/searches/{id}/toggle-active` | Toggle active/paused status for an item |
| `GET` | `/api/searches/categories` | Fetch available Vinted category taxonomy |
| `POST` | `/api/searches/suggest-classification` | Auto-detect brand and category suggestions from query |
| `POST` | `/api/searches/cleanup-all-false-positives` | Purge false positives across all user searches |

### 📈 Analytics (`/api/analytics`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/analytics/{id}/sales-over-time` | Aggregated volume and price envelopes (`day`, `week`, `month`, `year`) |
| `GET` | `/api/analytics/{id}/summary` | KPI overview (total sold, clean average, gross value, price range) |
| `GET` | `/api/analytics/{id}/recent-sales` | Paginated recent sales feed with thumbnail images |

### 🎯 Deal Sniper (`/api/sniper`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/sniper/status` | Live sniper status, active target IDs, and deal feed |
| `GET` | `/api/sniper/targets-info` | Tracked targets with recommended buy prices and sold averages |
| `POST` | `/api/sniper/start` | Launch continuous deal sniper with price and profit thresholds |
| `POST` | `/api/sniper/stop` | Stop the deal sniper background worker |
| `POST` | `/api/sniper/clear` | Clear the in-memory deal queue |
| `POST` | `/api/sniper/dismiss-deal` | Dismiss a deal card and persist to cleared deals |
| `POST` | `/api/sniper/clear-deals` | Dismiss all current deals in bulk |

### 🛡️ Anti-Ban & IP Shield (`/api/antiban`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/antiban/status` | Current proxy pool health, circuit breaker state, and cookies |
| `POST` | `/api/antiban/config` | Update proxy list and session cookies for the active profile |
| `POST` | `/api/antiban/test` | Run a live connection probe against Vinted UK |
| `POST` | `/api/antiban/reset-cooldown` | Manually reset circuit breaker cooldown |
| `POST` | `/api/antiban/harvest-session` | Launch headless browser to harvest Cloudflare session cookies |

### 🧬 Hierarchical Models & Vision (`/api`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/searches/{id}/variants` | List hierarchical models and exclusive sub-styles |
| `POST` | `/api/searches/{id}/variants` | Create or update a model or exclusive sub-style |
| `DELETE` | `/api/searches/{id}/variants/{v_id}` | Remove a variant model or sub-style |
| `POST` | `/api/searches/{id}/variants/discover` | AI-assisted taxonomy research for models and sub-styles |
| `POST` | `/api/items/{item_id}/variant` | Assign or remove model/style tags on a specific item |
| `POST` | `/api/searches/{id}/variants/tag-all` | Batch-classify stored items using local vision & OCR |
| `GET` | `/api/ai/status` | Current LLM provider status and configured models |
| `POST` | `/api/ai/config` | Save LLM API keys and model preferences |
| `POST` | `/api/ai/test` | Test LLM API connectivity |

### 🗄️ Database Management (`/api/database`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/database/analytics` | High-resolution analytics (clean vs replica counts, volumes, trends) |
| `GET` | `/api/database/items` | Filtered, sorted, and paginated item table |
| `GET` | `/api/database/filter-options` | Dynamic filter values (brands, variants, conditions, sizes) |
| `POST` | `/api/database/purge-item/{id}` | Permanently delete an item from the database |
| `POST` | `/api/database/bulk-purge` | Bulk delete selected items |
| `POST` | `/api/database/reclassify-item/{id}` | Manually toggle clean vs replica status |
| `POST` | `/api/database/batch-retro-antirep` | Run retroactive anti-rep scan across database items |
| `POST` | `/api/database/assign-variant` | Batch assign or clear model/style tags |
| `GET` | `/api/database/replica-breakdown` | Counterfeit breakdown statistics and rates |
| `POST` | `/api/database/replicas/purge` | Purge all identified replica listings |
| `POST` | `/api/database/replicas/delete-month` | Purge replica items captured in a specific `YYYY-MM` month |
| `POST` | `/api/database/replicas/retro-scan` | Trigger retroactive replica audit scan |

### ⚙️ Scraper & Scheduler Controls (`/api/scraper` & `/api/logs`)
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/scraper/batch-scrape` | Trigger batch scrape across selected or all active searches |
| `GET` | `/api/scraper/scheduler/status` | Background poller status and next scheduled run |
| `POST` | `/api/scraper/scheduler/settings` | Configure polling interval and continuous scraping |
| `GET` | `/api/logs` | Fetch real-time system scrape and sniper logs |

---

## License

This project is licensed under the MIT License.
