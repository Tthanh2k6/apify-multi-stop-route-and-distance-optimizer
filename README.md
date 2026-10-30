# Multi-Stop Route, Geocoder & 2-Opt TSP Distance Optimizer (Apify Actor)

Lightweight, zero-API-key **Python Async (`httpx` + `2-Opt TSP`) Apify Actor** that accepts street addresses or `lat, lng` coordinates from **Google Sheets, HubSpot, Airtable, Pipedrive, or CSV**, geocodes via **OpenStreetMap Nominatim** (with compliant `1 req/s` throttling and coordinate fallback), builds road-adjusted **Haversine distance matrices**, solves **Nearest-Neighbor + 2-Opt Travelling Salesperson (TSP) Route Optimization**, enforces **shift time-window budgets (`maxMinutes`)**, and outputs **1-click Multi-Stop Google Maps turn-by-turn navigation URLs**.

Built specifically for **field sales teams, real estate wholesalers/inspectors, HVAC/home-service dispatchers, medical couriers, and n8n / Make.com logistics automations** who want fast multi-stop route optimization without paying enterprise Google Routes Matrix fees.

---

## ✨ Key Engineering Capabilities

- **Zero External API Keys Required:** Resolves coordinates out of the box via inline `"lat, lng"` detection, explicit `{lat, lng}` fields, and polite **OpenStreetMap Nominatim** geocoding (`1 req/sec` compliant `User-Agent`).
- **Nearest-Neighbor + 2-Opt TSP Solver:** Eliminates zig-zag route crossings across both **Closed Round-Trips** (`Origin -> Stops -> Origin`) and **Open-Ended Routes** (`Origin -> Stops`), reporting exact before-and-after mileage savings (`naive_route_total_miles` vs `optimized_route_total_miles` and `distance_saved_pct`).
- **Shift Time-Window Budgeting (`maxMinutes`):** Dynamically accumulates leg drive minutes + per-stop service/dwell duration (`serviceMinutesPerStop`) and flags stops as `Scheduled` vs `Deferred (Over Time Window)`.
- **Road Circuity Calibration (`roadCircuityPercent`):** Applies configurable street-grid circuity multipliers (`128%` = `1.28x` great-circle Haversine distance) and urban speed profiles (`averageSpeedMph`) for realistic ETA estimation.
- **1-Click Multi-Stop Google Maps URLs:** Generates mobile-ready `https://www.google.com/maps/dir/?api=1&origin=...&waypoints=...` links ready to send via SMS, Slack, Telegram, or Google Sheets.
- **Ultra-Low Memory Footprint (`256 MB RAM`):** Pure Python math + async `httpx` — runs in `< 1 second` when coordinates are supplied.

---

## 📥 Input Configuration

```json
{
  "origin": "Union Station, 30 W Pershing Rd, Kansas City, MO 64108",
  "destination": "Union Station, 30 W Pershing Rd, Kansas City, MO 64108",
  "stops": [
    {
      "label": "Stop A - River Market",
      "address": "20 E 5th St, Kansas City, MO 64106",
      "lat": 39.1088,
      "lng": -94.5818,
      "priority": 90,
      "serviceMinutes": 12
    },
    {
      "label": "Stop B - Country Club Plaza",
      "address": "4706 Broadway Blvd, Kansas City, MO 64112",
      "lat": 39.0419,
      "lng": -94.5908,
      "priority": 100,
      "serviceMinutes": 15
    }
  ],
  "maxMinutes": 120,
  "serviceMinutesPerStop": 12,
  "averageSpeedMph": 28,
  "roadCircuityPercent": 128
}
```

> **Tip:** `stops` also accepts plain address strings (`["20 E 5th St, Kansas City, MO", "4706 Broadway Blvd, Kansas City, MO"]`) or coordinate strings (`["39.1088, -94.5818", "39.0419, -94.5908"]`)!

---

## 📤 Sample Output Record

```json
{
  "stop_sequence": 1,
  "original_input_index": 5,
  "schedule_status": "Scheduled",
  "within_time_window": true,
  "label": "Stop E - Crossroads District",
  "address": "1901 Baltimore Ave, Kansas City, MO 64108",
  "latitude": 39.0902,
  "longitude": -94.5844,
  "geocode_source": "input_coordinates",
  "priority": 95,
  "leg_distance_miles": 0.44,
  "leg_distance_km": 0.71,
  "leg_drive_minutes": 0.9,
  "service_minutes": 12,
  "cumulative_miles": 0.44,
  "cumulative_minutes": 12.9,
  "naive_route_total_miles": 22.86,
  "optimized_route_total_miles": 16.41,
  "distance_saved_miles": 6.45,
  "distance_saved_pct": 28.22,
  "google_maps_nav_url": "https://www.google.com/maps/dir/?api=1&origin=Union+Station..."
}
```

---

## 🛠️ Integration with n8n, Make.com & Python

### 1. Python (`apify-client`)
```python
from apify_client import ApifyClient

client = ApifyClient("YOUR_APIFY_TOKEN")
run = client.actor("thanh81646/multi-stop-route-and-distance-optimizer").call(
    run_input={
        "origin": "Union Station, 30 W Pershing Rd, Kansas City, MO 64108",
        "destination": "Union Station, 30 W Pershing Rd, Kansas City, MO 64108",
        "stops": [
            "20 E 5th St, Kansas City, MO 64106",
            "6300 Brookside Plaza, Kansas City, MO 64113",
            "1901 Baltimore Ave, Kansas City, MO 64108",
        ],
        "maxMinutes": 120,
    }
)

for stop in client.dataset(run["defaultDatasetId"]).iterate_items():
    print(f"#{stop['stop_sequence']} {stop['label']} ({stop['cumulative_minutes']}m) -> {stop['address']}")
```

### 2. n8n Workflow (`Google Sheets / CRM -> Apify -> Slack / SMS`)
1. Add a **Google Sheets** (`Get Rows`) or **HubSpot** (`Get Deals`) node to pull unvisited field addresses.
2. Add the **Apify** node (`Run an Actor and Get Dataset`) pointing to `multi-stop-route-and-distance-optimizer` and pass `{{ $json.addresses }}` into `stops`.
3. Send `{{ $json.google_maps_nav_url }}` and the ordered itinerary to your field reps via **Slack**, **Twilio SMS**, or **Gmail**!

### 3. Make.com Scenario
1. Connect **Airtable / Google Sheets -> Watch Records** to **Apify -> Run an Actor** (`sync=true`).
2. Iterate returned dataset items and write `stop_sequence`, `cumulative_minutes`, and `google_maps_nav_url` back to your dispatch sheet.

---

## 💼 Custom Logistics Automation, Scrapers & AI Workflows

Built and maintained by **Trung Thanh Tran** (*Python, Playwright, n8n & AI Automation Engineer*):

- 🟢 **Hire me on Upwork (Custom Scrapers, Route Planners & AI Pipelines):** [upwork.com/freelancers/trungthanhtrandev](https://www.upwork.com/freelancers/trungthanhtrandev)
- ⚡ **Order Fast-Turnaround Automation Gigs on Fiverr:** [fiverr.com/trungthnhtrn244](https://www.fiverr.com/trungthnhtrn244)
- 📦 **Download the Complete Python, AI & n8n Scraper Toolkit Pro (6 CLI Engines + 3 Blueprints):** [trungthanh16.gumroad.com/l/python-ai-automation-n8n-scraper-toolkit-pro](https://trungthanh16.gumroad.com/l/python-ai-automation-n8n-scraper-toolkit-pro)
- 💻 **GitHub Portfolio:** [github.com/Tthanh2k6](https://github.com/Tthanh2k6)

---

## ⚖️ Legal & Independence Disclaimer

**Nominative Fair Use — Public Non-Authenticated Factual Data Only:** This Actor is an independent route-optimization and geocoding utility that processes exclusively user-supplied addresses and coordinates alongside public OpenStreetMap Nominatim geocoding data (© OpenStreetMap contributors, licensed under ODbL, respecting the 1 req/sec Nominatim Usage Policy). It does not bypass authentication barriers, login walls, or proprietary paid mapping APIs. Any third-party platform names referenced (such as Google Maps, HubSpot, Airtable, Make.com, or n8n) are trademarks of their respective owners and are used strictly for descriptive identification and deep-link navigation purposes (*nominative fair use*). Users act as independent Data Controllers and are solely responsible for verifying road conditions, turn restrictions, and compliance with applicable data protection laws (including GDPR and CCPA).
