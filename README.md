# Multi-Stop Route Planner with Nominatim Geocoding and 2-Opt (Apify Actor)

An Apify actor in Python (`httpx` + plain math). It takes an origin, an optional destination and a list of stops (addresses or coordinates), orders the stops to shorten the trip, estimates drive time, marks stops that do not fit in a time budget, and builds a Google Maps directions link for the ordered route. No API key is needed.

Distances are estimates: straight-line (Haversine) distance multiplied by a "road circuity" factor (default 1.28). The actor does not call a routing engine, so it knows nothing about real roads, one-way streets, traffic or turn restrictions.

## What it does

1. **Resolves each location** in this order:
   - explicit `lat`/`lng` fields on the stop (`geocode_source: "input_coordinates"`);
   - an address string that is itself `"lat, lng"` (`"inline_coordinates"`);
   - a small built-in table of about 16 addresses, mostly the Kansas City demo addresses plus a few US city names (`"verified_cache"`);
   - OpenStreetMap Nominatim search, throttled to about 1 request per second (`"osm_nominatim"`);
   - **if all of the above fail, an invented point** (see below).
2. **Orders the stops** with a nearest-neighbour tour from the origin, then 2-opt swaps (up to 60 passes), keeping the origin and the optional destination fixed. It reports the input-order distance (`naive_route_total_miles`), the optimized distance and the saving. The `priority` field is copied to the output but is not used when ordering.
3. **Applies the time budget.** It walks the ordered stops, adding drive time at `averageSpeedMph` plus each stop's service minutes. Stops past `maxMinutes` are marked `Deferred (Over Time Window)`. The first stop is always scheduled. The return leg to the destination is not counted in the budget.
4. **Builds one Google Maps link** (`/maps/dir/?api=1`) with the scheduled stops as waypoints (Google allows at most 20 intermediate waypoints, so extra ones are dropped from the link).

## Geocoding fallback: read this before using the output

When an address cannot be resolved (Nominatim returns nothing, is rate-limited, or the request fails), the actor does **not** stop. It makes up a coordinate: a point within about ±0.04° (roughly 4 km) of the origin, derived from a SHA-256 hash of the address text. The record is marked `geocode_source: "fallback_centroid"`.

These points are not real locations, so distances, ETAs and the stop order involving them are wrong. If the **origin** itself cannot be resolved, it falls back around Union Station, Kansas City (the built-in default), which makes the whole route meaningless for any other city. Always check `geocode_source` and `origin_geocode_source`, and drop or fix rows with `fallback_centroid` before you use the route. Supplying `lat`/`lng` yourself avoids the problem.

## Input

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
      "serviceMinutes": 12
    },
    {
      "label": "Stop B - Country Club Plaza",
      "address": "4706 Broadway Blvd, Kansas City, MO 64112",
      "lat": 39.0419,
      "lng": -94.5908,
      "serviceMinutes": 15
    }
  ],
  "maxMinutes": 120,
  "serviceMinutesPerStop": 12,
  "averageSpeedMph": 28,
  "roadCircuityPercent": 128
}
```

`stops` also accepts plain address strings or `"lat, lng"` strings. Leave `destination` empty for a one-way route. Runs on the Apify free tier are capped at 25 stops.

## Output record (one per stop)

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
  "origin_address": "Union Station, 30 W Pershing Rd, Kansas City, MO 64108",
  "origin_geocode_source": "verified_cache",
  "destination_address": "Union Station, 30 W Pershing Rd, Kansas City, MO 64108",
  "naive_route_total_miles": 22.86,
  "optimized_route_total_miles": 16.41,
  "distance_saved_miles": 6.45,
  "distance_saved_pct": 28.22,
  "google_maps_nav_url": "https://www.google.com/maps/dir/?api=1&origin=Union+Station..."
}
```

## Calling it from Python

```python
from apify_client import ApifyClient

client = ApifyClient("YOUR_APIFY_TOKEN")
run = client.actor("thanh81646/apify-multi-stop-route-and-distance-optimizer").call(
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
    if stop["geocode_source"] == "fallback_centroid":
        print("WARNING: address not geocoded:", stop["address"])
    print(f"#{stop['stop_sequence']} {stop['label']} ({stop['cumulative_minutes']} min) -> {stop['address']}")
```

In n8n or Make.com, use the Apify "Run an Actor" node/module with the actor ID `thanh81646/apify-multi-stop-route-and-distance-optimizer`, pass your addresses into `stops`, and send `google_maps_nav_url` to whoever drives the route.

## Author

Trung Thanh Tran: [Upwork](https://www.upwork.com/freelancers/trungthanhtrandev) · [GitHub](https://github.com/Tthanh2k6) · [Apify](https://apify.com/thanh81646)

## Legal note

The actor processes addresses and coordinates you supply and queries the public OpenStreetMap Nominatim service (© OpenStreetMap contributors, ODbL) within its usage policy. Google Maps and other product names are trademarks of their owners and are used only to build links or identify them. You are responsible for checking real road conditions and for complying with applicable data protection law (including GDPR and CCPA).
