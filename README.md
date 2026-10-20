# Vinted UK Sales & Price Tracker 🇬🇧

A full-stack web application designed to track, record historical sales, and analyze pricing trends for popular searchable items on Vinted UK (`vinted.co.uk`).

This is a largely vibe coded project made using Google Gemini Flash within Antigravity

## Features

- **Multi-Period Analytics Graphs**:
  - Interactive graphs for **Sales Quantity** and **Average Price Sold For** aggregated across:
    - **Days** (Daily trends)
    - **Weeks** (Weekly trends)
    - **Months** (Monthly trends)
    - **Years** (Annual trends)
  - Price corridors showing **Average**, **Minimum**, and **Maximum** sale prices.
- **Pre-baked Popular Search Items**:
  - `North Face Nuptse 700`
  - `Ralph Lauren quarter zip`
  - `Patagonia Synchilla fleece`
- **Track Any Custom Item**: Easily add/remove any item, brand, or model to monitor via the UI.
- **Background Scheduler**: Automatically polls `vinted.co.uk` periodically (default: 30 mins) to capture newly listed/sold items.
- **Historical Database**: Deduplicated persistent SQLite storage (`vinted_tracker.db`) recording title, price (£), brand, size, photo thumbnails, and URLs.

---

## Installation & Setup

1. **Install Python Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the Application**:
   ```bash
   python run.py
   ```
   Or directly via uvicorn:
   ```bash
   uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```

3. **Open the Dashboard**:
   Navigate to [http://127.0.0.1:8000](http://127.0.0.1:8000) in your web browser.

---

## Architecture

- **Backend**: Python 3.10+ / FastAPI / SQLAlchemy / Pydantic
- **Database**: SQLite (file-based, zero configuration)
- **Scraper / Poller**: `vinted-scraper` + APScheduler background worker
- **Frontend**: HTML5 / Tailwind CSS / Chart.js / Lucide Icons
- **Target Marketplace**: `vinted.co.uk` (GBP / £)

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/searches` | List all tracked search items with recorded count |
| `POST` | `/api/searches` | Add a new item/keyword to track |
| `DELETE` | `/api/searches/{id}` | Delete a tracked item and its history |
| `POST` | `/api/searches/{id}/refresh` | Trigger a live scrape on Vinted UK |
| `GET` | `/api/analytics/{id}/sales-over-time?period=day\|week\|month\|year` | Aggregated volume and price statistics |
| `GET` | `/api/analytics/{id}/summary` | KPI overview (total sold, avg/min/max price, gross value) |
| `GET` | `/api/analytics/{id}/recent-sales` | Paginated list of recent captured items |
| `GET` | `/api/analytics/overview` | Cross-item comparison statistics |

