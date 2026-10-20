# UX Research Report: Vinted UK Sales Tracker & Deal Sniper Dashboard

**Date:** October 20, 2026  
**Role:** Senior UX & Product Researcher  
**Target Product:** Vinted UK Sales Tracker & Deal Sniper (v2.4 PRO)  
**Evaluation Scope:** Visual ergonomics, information architecture, cognitive load, mobile usability, security/compliance perception, and onboarding friction.

---

## 1. Executive Summary

The Vinted Tracker & Deal Sniper has evolved into a feature-dense, aesthetically refined "cyber-fintech" command center. While visual hierarchy, dark mode contrast, and telemetry feedback have improved significantly, the interface suffers from **cognitive density**, **overlapping scraping concepts**, **jargon opacity**, and **mobile ergonomics bottlenecks**.

```
[Strengths]                                 [Friction Areas]
• High visual polish & cyber aesthetic       • 3 overlapping automated engines (Sniper vs Poll vs Constant Scrape)
• Dense telemetry & real-time live terminal   • Desktop-first layout with mobile touch target hazards
• Sophisticated counterfeit heuristic filters • Opaque security jargon (TLS impersonation, CF clearance)
• Instant client-side search & filtering     • Lack of profit/fee math for actual reseller decisions
```

---

## 2. Persona Simulations

---

### Persona 1: "The Rushed Commuter" (Mobile, Distracted, 4G Subway)
* **Profile:** Jordan, 24, side-hustle vintage flipper. Checks phone with one hand on a moving bus/train between stops.
* **Goal:** Quickly spot underpriced deals, verify resale margins, and tap "Buy" before someone else grabs it.
* **Scenario:** Receives a notification or opens the URL on iPhone Safari over spotty 4G.

#### Where They Get Stuck:
1. **Vertical Stacking & Pushed Content:** On viewports `< 768px`, the left navigation sidebar stacks vertically above the main content. The user has to scroll past navigation pills and tracked item chips before reaching active deal cards.
2. **Tiny Touch Targets (Fat-Finger Hazards):** Quick action buttons (like the `verify` button `p-0.5`, `edit price` icon, and table action links) have target areas under 28×28px. The Apple Human Interface Guidelines mandate at least 44×44px. On a shaky commute, the user accidentally triggers "Flag Replica" instead of "View Listing".
3. **Drawer Domination:** If the "Live Terminal" drawer opens or is active, its fixed 380px height consumes >70% of a mobile screen, hiding deals underneath.
4. **Bandwidth & Battery Drain:** Continuous 2.5-second polling keeps the radio active and causes micro-stutters over fluctuating cellular connections.

#### What They Misunderstand:
* *"Is Deal Sniper buying this for me automatically?"* The term "Sniper" creates an expectation of automated checkout/purchase botting, whereas it is actually an instant alert/discovery feed with an external link to Vinted UK.
* *"Are these prices the final cost?"* Commuters see an £18 Margiela piece, but forget Vinted adds the Buyer Protection Fee (£0.70 + 5%) and shipping (~£3.20), turning an £18 snipe into £22.60.

#### Jargon / Buzzwords Tripping Them Up:
* `RADAR IDLE` / `SNIPING ACTIVE`: Reads like military/gaming jargon rather than "Live Scanner: Off / On".
* `Gross Volume Value`: Reseller wants "Total Market Value" or "Estimated Inventory Value".

---

### Persona 2: "The Skeptical Enterprise Buyer / Compliance Auditor"
* **Profile:** Helena, 42, VP of E-Commerce Operations / Legal & IP Counsel evaluating secondary marketplace tracking tools.
* **Goal:** Verify compliance with Vinted Terms of Service, GDPR, API ethics, data integrity, and network security.
* **Scenario:** Reviews dashboard modals, anti-ban panel, and database storage architecture.

#### Where They Get Stuck:
1. **Direct Exposure of Plaintext Secrets:** Opening the *Anti-Ban & Cloudflare Shield* modal reveals raw proxy strings (`http://user:pass@host:port`) and session cookies (`refresh_token_web`, `access_token_web`, `cf_clearance`) directly in editable textareas rendered in browser DOM.
2. **Lack of Role-Based Access Control (RBAC):** Anyone accessing the URL can permanently delete monthly records, prune the database, or alter scraping intervals without authentication or audit logging.
3. **No Terms / Policy Disclosure:** No banner or disclaimer clarifying whether the tool operates within fair-use rate limits or uses reverse-engineered private mobile/web APIs.

#### What They Misunderstand:
* *"Is this malware or an exploit kit?"* Seeing features labelled "Chrome 124 TLS Impersonation", "Cloudflare Clearance bypass", and "Turnstile Captcha evasion" sounds like black-hat penetration software rather than a standard modern web scraping client (`curl_cffi`).
* *"Is seller username storage a GDPR violation?"* The database stores `@seller_username` alongside listing IDs without privacy masking or retention policies.

#### Jargon / Buzzwords Tripping Them Up:
* `TLS Fingerprint Spoofing / JA3 / Akamai Impersonation`: Highly technical network terminology that frightens risk-averse executives.
* `Circuit Breaker Cooldown`: Standard microservice architecture term, but opaque to non-engineers (they wonder if the database crashed).

---

### Persona 3: "The Reseller Who Skipped Onboarding" (Zero Context)
* **Profile:** Marcus, 29, casual streetwear seller. Never read docs, opened the app, and clicked around.
* **Goal:** Track 3 items and find bargains to resell.
* **Scenario:** Arrives at dashboard with either empty database or default seeded searches.

#### Where They Get Stuck:
1. **Engine Overlap Confusion:** The dashboard presents three separate automated scanning concepts:
   - *Auto-Poll* in the header (runs on a 30-minute interval).
   - *Deal Sniper* (continuous 5s scan for listings `< 15m old`).
   - *Constant Continuous Scrape* inside the Scheduler modal.  
   Marcus has no idea which one he is supposed to run, whether they run concurrently, or if running one cancels the other.
2. **The "Empty State Cold Start":** When a new tracked item is added, the dashboard displays `£0.00`, empty charts, and "No sales recorded yet". It requires the user to manually discover and click the "Scrape" button.
3. **Category Tree Overload:** When clicking "+ Track New Item", Marcus is confronted with a raw category hierarchy picker with multi-level dropdowns and checkboxes, when all he wanted was to search "Stone Island sweatshirt".

#### What They Misunderstand:
* *"Why is Average Price Sold showing £35 if listings are on sale for £20?"* Confusion between *Active Asking Price* vs. *Verified Sold Historical Price*.
* *"Anti-Rep Filters":* Marcus thinks "Anti-Rep" means anti-reputation or seller rating filters, rather than "Anti-Replica" (counterfeit detection).

#### Jargon / Buzzwords Tripping Them Up:
* `Anti-Rep` instead of `Counterfeit & Fake Detection`.
* `Photo Key / Perceptual Hash`: Technical terms for duplicate stock-image detection.
* `Heuristic Verification`: Unclear automated rule logic.

---

### Persona 4: "The Pro Fashion Power Trader" (Speed & High Volume)
* **Profile:** Sarah, 21, full-time Depop/eBay/Vinted reseller doing 80+ transactions/week.
* **Goal:** Maximum throughput, instant audio notifications, rapid keyboard shortcuts, quick profit spread analysis.

#### Where They Get Stuck:
* **No Keyboard Shortcuts:** Has to reach for the mouse for every single action (no `J`/`K` navigation through deal cards, no `S` to snipe, no `Esc` to close drawer).
* **Missing Net Margin Calculator:** Deals display "35% below average", but don't show:
  $$\text{Net Profit} = \text{Estimated Market Value} - (\text{Asking Price} + \text{Vinted Fees} + \text{Shipping})$$
* **Audio Policy Block:** If the tab was loaded in background without user interaction, browser autoplay blocks the alert chime, causing missed deals.

---

## 3. Top Usability & Heuristic Issues

| Heuristic | Severity | Issue | Recommended Fix |
|:---|:---:|:---|:---|
| **Recognition rather than Recall** | 🔴 High | Three overlapping scraper features (Auto-Poll, Sniper, Constant Scrape). | Consolidate into a unified **"Scanning Modes"** segmented control: `Sniper Mode (Instant Deals)` vs. `Market Tracker (Historical Sync)`. |
| **Error Prevention / Safety** | 🔴 High | Permanent "Prune Data" and "Delete Search" buttons easily misclicked. | Add 2-step typed confirmation or non-destructive soft-archive. |
| **Match Real World Language** | 🟡 Medium | Heavy developer/cyber terminology (`Anti-Rep`, `TLS Spoof`, `Perceptual Hash`, `Circuit Breaker`). | Use consumer-friendly copy: `Counterfeit Filter`, `Secure Connection Shield`, `Duplicate Photo Detector`. |
| **Flexibility & Efficiency** | 🟡 Medium | Lack of keyboard navigation for power users. | Implement hotkeys: `J`/`K` (next/prev deal), `Space` (preview), `S` (snipe/open Vinted), `T` (terminal). |
| **Mobile Ergonomics** | 🔴 High | Sidebar stacks on mobile; tiny touch targets (`<30px`). | Add a sticky bottom navigation bar on mobile (`Sniper` \| `Analytics` \| `Items` \| `Terminal`) with 48px tap targets. |
| **Trust & Feedback** | 🟡 Medium | Audio notifications fail silently if autoplay blocked. | Display visual toast notification: *"Click anywhere to enable deal sound alerts"* on first visit. |

---

## 4. Suggested Product & UX Enhancements

### 1. Unified Engine Architecture (Eliminate Conceptual Overlap)
* **Current Problem:** Users are confused between *Auto-Poll*, *Deal Sniper*, and *Constant Scrape*.
* **Solution:** Replace the separate toggles with a single **Market Engine Selector**:
  ```
  [ 🎯 Deal Sniper (Real-Time) ]  [ 📊 Trend Sync (Periodic 30m) ]  [ ⏸️ Paused ]
  ```
  Under the hood, selecting *Deal Sniper* activates the fast 5s poll for new items; *Trend Sync* manages the background 30m database refresh.

### 2. Profit Margin & Total Cost Card Overlay
* **Current Problem:** Deal cards show list price (e.g., `£15.00`) and discount (`-45%`), but ignore buyer costs.
* **Solution:** Add an interactive **"Reseller Net Calculator"** breakdown on deal card hover:
  - Listed Price: `£15.00`
  - Est. Buyer Fee & Postage: `+£4.15`
  - **Total Cost:** `£19.15`
  - Historical Avg Sold Price: `£38.00`
  - **Estimated Net Resale Margin:** `+£18.85 (~49% ROI)`

### 3. Progressive Disclosure for "Anti-Rep" & Technical Settings
* **Current Problem:** New users are intimidated by complex threshold sliders, brand overrides, and regex negative keywords.
* **Solution:** Provide 3 pre-configured security profiles:
  - 🟢 **Standard (Recommended):** High-confidence replicas and duplicate stock photos filtered automatically.
  - 🟡 **Aggressive:** Excludes listings with no reviews, new accounts under 3 days old, and suspicious keywords.
  - ⚙️ **Custom (Expert Mode):** Unlocks all raw heuristic weights, photo hashes, and negative keyword fields.

### 4. Dedicated Mobile Bottom App Bar
* **Current Problem:** On smartphones, navigation requires scrolling past 20 items.
* **Solution:** When viewport `< 768px`, hide the desktop sidebar into an off-canvas drawer and render a 56px sticky bottom bar:
  - `[🎯 Sniper]` `[📈 Trends]` `[📦 Database]` `[⚡ Terminal]` `[+ New]`

---

## 5. Terminology Translation Table

| Current Jargon | User Friction / Perception | Proposed Replacement |
|:---|:---|:---|
| **Anti-Rep Filters** | Confused with "reputation" or seller feedback. | **Counterfeit Protection** or **Fake Item Filter** |
| **TLS Impersonation / CF Clearance** | Sounds risky, illicit, or like a malware tool. | **Cloudflare Connection Shield** |
| **Photo Key / Perceptual Hash** | Meaningless to non-programmers. | **Duplicate Stock Photo Filter** |
| **RADAR IDLE** | Video game / sci-fi tone. | **Scanner Inactive** |
| **Circuit Breaker Active** | Sounds like electrical system failure. | **Rate Limit Protection (Cooling Down)** |
| **Gross Volume Value** | Academic accounting term. | **Total Tracked Market Value** |
| **Prune Older Data** | Ambiguous whether it affects tracked settings. | **Clean Up Expired Listings** |

---

## 6. Conclusion

The Vinted Tracker has high technical power and exceptional aesthetic appeal. By addressing the **3-way scraper ambiguity**, replacing **niche engineer jargon with plain reseller language**, and adding **reseller financial math (fees + shipping + net profit)**, the platform will transition from a developer tool into an indispensable marketplace trading instrument.

