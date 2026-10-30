"""
Apify Store Actor: Multi-Stop Route, Geocoder & 2-Opt TSP Distance Optimizer
Accepts street addresses or lat/lng coordinates, geocodes via OpenStreetMap Nominatim
(respecting 1 req/s User-Agent policy with deterministic coordinate fallback), builds
Haversine + road-circuity distance matrices, solves Nearest-Neighbor + 2-Opt TSP route
optimization with shift time-window caps, and generates Google Maps navigation links.
"""
import asyncio
import hashlib
import json
import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import httpx

try:
    from apify import Actor
    HAS_APIFY = True
except ImportError:
    HAS_APIFY = False

COORD_PAIR_REGEX = re.compile(
    r"^\s*([+-]?\d{1,2}(?:\.\d+)?)\s*[,;\s]\s*([+-]?\d{1,3}(?:\.\d+)?)\s*$"
)

# Pre-verified landmark & metro cache for instant (<50ms) zero-network resolution on common depots
KNOWN_GEOCODE_CACHE: Dict[str, Tuple[float, float]] = {
    "union station, 30 w pershing rd, kansas city, mo 64108": (39.0853, -94.5852),
    "downtown kansas city, mo": (39.0997, -94.5786),
    "20 e 5th st, kansas city, mo 64106": (39.1088, -94.5818),
    "4706 broadway blvd, kansas city, mo 64112": (39.0419, -94.5908),
    "6300 brookside plaza, kansas city, mo 64113": (39.0132, -94.5919),
    "4050 pennsylvania ave, kansas city, mo 64111": (39.0531, -94.5905),
    "1901 baltimore ave, kansas city, mo 64108": (39.0902, -94.5844),
    "1616 e 18th st, kansas city, mo 64108": (39.0913, -94.5632),
    "new york, ny": (40.7128, -74.0060),
    "chicago, il": (41.8781, -87.6298),
    "austin, tx": (30.2672, -97.7431),
    "dallas, tx": (32.7767, -96.7970),
    "los angeles, ca": (34.0522, -118.2437),
    "san francisco, ca": (37.7749, -122.4194),
    "seattle, wa": (47.6062, -122.3321),
    "miami, fl": (25.7617, -80.1918),
}

DEFAULT_STOPS: List[Dict[str, Any]] = [
    {
        "label": "Stop A - River Market",
        "address": "20 E 5th St, Kansas City, MO 64106",
        "lat": 39.1088,
        "lng": -94.5818,
        "priority": 90,
        "serviceMinutes": 12,
    },
    {
        "label": "Stop B - Country Club Plaza",
        "address": "4706 Broadway Blvd, Kansas City, MO 64112",
        "lat": 39.0419,
        "lng": -94.5908,
        "priority": 100,
        "serviceMinutes": 15,
    },
    {
        "label": "Stop C - Brookside",
        "address": "6300 Brookside Plaza, Kansas City, MO 64113",
        "lat": 39.0132,
        "lng": -94.5919,
        "priority": 80,
        "serviceMinutes": 12,
    },
    {
        "label": "Stop D - Westport",
        "address": "4050 Pennsylvania Ave, Kansas City, MO 64111",
        "lat": 39.0531,
        "lng": -94.5905,
        "priority": 85,
        "serviceMinutes": 10,
    },
    {
        "label": "Stop E - Crossroads District",
        "address": "1901 Baltimore Ave, Kansas City, MO 64108",
        "lat": 39.0902,
        "lng": -94.5844,
        "priority": 95,
        "serviceMinutes": 12,
    },
    {
        "label": "Stop F - 18th & Vine",
        "address": "1616 E 18th St, Kansas City, MO 64108",
        "lat": 39.0913,
        "lng": -94.5632,
        "priority": 75,
        "serviceMinutes": 10,
    },
]

DEFAULT_ORIGIN = "Union Station, 30 W Pershing Rd, Kansas City, MO 64108"


def parse_inline_coords(text: str) -> Optional[Tuple[float, float]]:
    match = COORD_PAIR_REGEX.match(text or "")
    if not match:
        return None
    lat, lng = float(match.group(1)), float(match.group(2))
    if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0:
        return (lat, lng)
    return None


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float, road_factor: float = 1.28) -> float:
    """Computes road-adjusted Haversine distance in statute miles."""
    r_earth_miles = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)

    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return r_earth_miles * c * road_factor


class NominatimGeocoder:
    """Polite OpenStreetMap Nominatim geocoder enforcing >= 1.05s between external queries."""

    def __init__(self) -> None:
        self._memory_cache: Dict[str, Tuple[float, float, str]] = {
            k: (v[0], v[1], "verified_cache") for k, v in KNOWN_GEOCODE_CACHE.items()
        }
        self._last_request_ts: float = 0.0

    async def geocode(
        self,
        client: httpx.AsyncClient,
        address: str,
        explicit_lat: Optional[float] = None,
        explicit_lng: Optional[float] = None,
        fallback_anchor: Tuple[float, float] = (39.0853, -94.5852),
    ) -> Tuple[float, float, str]:
        if explicit_lat is not None and explicit_lng is not None:
            return (float(explicit_lat), float(explicit_lng), "input_coordinates")

        inline = parse_inline_coords(address)
        if inline:
            return (inline[0], inline[1], "inline_coordinates")

        norm_key = " ".join((address or "").strip().lower().split())
        if norm_key in self._memory_cache:
            return self._memory_cache[norm_key]

        # Enforce strict 1 req/sec Nominatim policy
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < 1.05 and self._last_request_ts > 0:
            await asyncio.sleep(1.05 - elapsed)

        try:
            self._last_request_ts = time.monotonic()
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": address, "format": "jsonv2", "limit": 1},
            )
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, list) and len(payload) > 0:
                    lat = float(payload[0]["lat"])
                    lng = float(payload[0]["lon"])
                    result = (lat, lng, "osm_nominatim")
                    self._memory_cache[norm_key] = result
                    return result
        except Exception:
            pass

        # Deterministic coordinate fallback around anchor so batch optimization never crashes
        digest = hashlib.sha256(norm_key.encode("utf-8")).digest()
        d_lat = ((digest[0] / 255.0) - 0.5) * 0.08
        d_lng = ((digest[1] / 255.0) - 0.5) * 0.08
        fallback = (round(fallback_anchor[0] + d_lat, 6), round(fallback_anchor[1] + d_lng, 6), "fallback_centroid")
        self._memory_cache[norm_key] = fallback
        return fallback


def compute_sequence_miles(
    points: List[Tuple[float, float]],
    order: List[int],
    origin_pt: Tuple[float, float],
    dest_pt: Optional[Tuple[float, float]],
    road_factor: float,
) -> float:
    if not order:
        return 0.0
    total = haversine_miles(origin_pt[0], origin_pt[1], points[order[0]][0], points[order[0]][1], road_factor)
    for idx in range(len(order) - 1):
        p_a = points[order[idx]]
        p_b = points[order[idx + 1]]
        total += haversine_miles(p_a[0], p_a[1], p_b[0], p_b[1], road_factor)
    if dest_pt is not None:
        last_pt = points[order[-1]]
        total += haversine_miles(last_pt[0], last_pt[1], dest_pt[0], dest_pt[1], road_factor)
    return total


def solve_nearest_neighbor_and_2opt(
    points: List[Tuple[float, float]],
    origin_pt: Tuple[float, float],
    dest_pt: Optional[Tuple[float, float]],
    road_factor: float = 1.28,
) -> List[int]:
    """
    Constructs a Nearest-Neighbor tour from origin_pt and refines it via 2-Opt edge swaps
    to eliminate route crossings while keeping origin (and optional destination) fixed.
    """
    n = len(points)
    if n <= 1:
        return list(range(n))

    # Phase 1: Nearest-Neighbor Greedy Initialization
    unvisited = set(range(n))
    tour: List[int] = []
    curr_lat, curr_lng = origin_pt

    while unvisited:
        nxt = min(
            unvisited,
            key=lambda i: haversine_miles(curr_lat, curr_lng, points[i][0], points[i][1], road_factor),
        )
        tour.append(nxt)
        unvisited.remove(nxt)
        curr_lat, curr_lng = points[nxt]

    if n == 2:
        rev_tour = [tour[1], tour[0]]
        if compute_sequence_miles(points, rev_tour, origin_pt, dest_pt, road_factor) < compute_sequence_miles(
            points, tour, origin_pt, dest_pt, road_factor
        ):
            return rev_tour
        return tour

    # Phase 2: 2-Opt Local Search Refinement
    best_miles = compute_sequence_miles(points, tour, origin_pt, dest_pt, road_factor)
    improved = True
    max_passes = 60
    passes = 0

    while improved and passes < max_passes:
        improved = False
        passes += 1
        for i in range(n - 1):
            for k in range(i + 1, n):
                candidate = tour[:i] + list(reversed(tour[i : k + 1])) + tour[k + 1 :]
                cand_miles = compute_sequence_miles(points, candidate, origin_pt, dest_pt, road_factor)
                if cand_miles + 1e-6 < best_miles:
                    tour = candidate
                    best_miles = cand_miles
                    improved = True
    return tour


def build_google_maps_url(
    origin_str: str,
    dest_str: Optional[str],
    waypoint_strs: List[str],
) -> str:
    if not waypoint_strs and not dest_str:
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(origin_str)}"

    final_dest = dest_str if dest_str else waypoint_strs[-1]
    intermediate = waypoint_strs if dest_str else waypoint_strs[:-1]
    # Cap intermediate waypoints at 20 per Google Maps URL specification
    capped_wp = intermediate[:20]

    url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={quote_plus(origin_str)}"
        f"&destination={quote_plus(final_dest)}"
        f"&travelmode=driving"
    )
    if capped_wp:
        wp_encoded = "|".join(quote_plus(w) for w in capped_wp)
        url += f"&waypoints={wp_encoded}"
    return url


async def safe_charge_event(event_name: str, count: int = 1) -> None:
    if not HAS_APIFY or count <= 0:
        return
    charge_fn = getattr(Actor, "charge", None)
    if callable(charge_fn):
        try:
            await charge_fn(event_name=event_name, count=count)
        except Exception:
            pass


async def optimize_route_pipeline(
    origin_raw: str,
    destination_raw: str,
    raw_stops: List[Any],
    max_minutes: int = 120,
    default_service_minutes: int = 12,
    avg_speed_mph: int = 28,
    road_circuity_percent: int = 128,
) -> List[Dict[str, Any]]:
    road_factor = max(1.0, float(road_circuity_percent) / 100.0)
    speed_mph = max(5.0, float(avg_speed_mph))
    geocoder = NominatimGeocoder()

    headers = {
        "User-Agent": "ApifyMultiStopRouteOptimizer/1.0 (+https://github.com/Tthanh2k6/apify-multi-stop-route-and-distance-optimizer)",
        "Accept": "application/json",
    }

    normalized_stops: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_stops):
        if isinstance(item, str) and item.strip():
            normalized_stops.append(
                {
                    "label": f"Stop #{idx + 1}",
                    "address": item.strip(),
                    "lat": None,
                    "lng": None,
                    "priority": 50,
                    "serviceMinutes": default_service_minutes,
                }
            )
        elif isinstance(item, dict):
            addr = str(item.get("address") or item.get("location") or "").strip()
            lat_val = item.get("lat") if item.get("lat") is not None else item.get("latitude")
            lng_val = item.get("lng") if item.get("lng") is not None else item.get("longitude")
            if not addr and lat_val is not None and lng_val is not None:
                addr = f"{lat_val}, {lng_val}"
            if not addr:
                continue
            normalized_stops.append(
                {
                    "label": str(item.get("label") or item.get("name") or f"Stop #{idx + 1}").strip(),
                    "address": addr,
                    "lat": float(lat_val) if lat_val is not None else None,
                    "lng": float(lng_val) if lng_val is not None else None,
                    "priority": int(item.get("priority", 50)),
                    "serviceMinutes": int(item.get("serviceMinutes", default_service_minutes)),
                }
            )

    if not normalized_stops:
        return []

    async with httpx.AsyncClient(headers=headers, timeout=12.0, follow_redirects=True) as client:
        origin_str = (origin_raw or "").strip() or normalized_stops[0]["address"]
        origin_lat, origin_lng, origin_source = await geocoder.geocode(client, origin_str)
        origin_pt = (origin_lat, origin_lng)

        dest_str = (destination_raw or "").strip()
        dest_pt: Optional[Tuple[float, float]] = None
        if dest_str:
            d_lat, d_lng, _ = await geocoder.geocode(client, dest_str, fallback_anchor=origin_pt)
            dest_pt = (d_lat, d_lng)

        geocoded_points: List[Tuple[float, float]] = []
        for stop in normalized_stops:
            s_lat, s_lng, s_src = await geocoder.geocode(
                client,
                stop["address"],
                explicit_lat=stop["lat"],
                explicit_lng=stop["lng"],
                fallback_anchor=origin_pt,
            )
            stop["lat"] = round(s_lat, 6)
            stop["lng"] = round(s_lng, 6)
            stop["geocode_source"] = s_src
            geocoded_points.append((s_lat, s_lng))

    naive_order = list(range(len(normalized_stops)))
    naive_miles = compute_sequence_miles(geocoded_points, naive_order, origin_pt, dest_pt, road_factor)

    opt_order = solve_nearest_neighbor_and_2opt(geocoded_points, origin_pt, dest_pt, road_factor)
    opt_miles = compute_sequence_miles(geocoded_points, opt_order, origin_pt, dest_pt, road_factor)

    saved_miles = max(0.0, naive_miles - opt_miles)
    saved_pct = round((saved_miles / naive_miles) * 100.0, 2) if naive_miles > 1e-6 else 0.0

    # Walk the optimized sequence and evaluate shift time-window constraints
    scheduled_waypoint_strs: List[str] = []
    prelim_rows: List[Dict[str, Any]] = []
    prev_lat, prev_lng = origin_pt
    cumulative_mins = 0.0
    cumulative_miles = 0.0

    for seq_num, stop_idx in enumerate(opt_order, start=1):
        stop = normalized_stops[stop_idx]
        leg_miles = haversine_miles(prev_lat, prev_lng, stop["lat"], stop["lng"], road_factor)
        leg_drive_mins = (leg_miles / speed_mph) * 60.0
        dwell_mins = float(stop["serviceMinutes"])

        projected_total = cumulative_mins + leg_drive_mins + dwell_mins
        within_window = projected_total <= float(max_minutes) or seq_num == 1

        cumulative_miles += leg_miles
        cumulative_mins = projected_total
        prev_lat, prev_lng = stop["lat"], stop["lng"]

        if within_window:
            scheduled_waypoint_strs.append(stop["address"])

        prelim_rows.append(
            {
                "stop_sequence": seq_num,
                "original_input_index": stop_idx + 1,
                "schedule_status": "Scheduled" if within_window else "Deferred (Over Time Window)",
                "within_time_window": within_window,
                "label": stop["label"],
                "address": stop["address"],
                "latitude": stop["lat"],
                "longitude": stop["lng"],
                "geocode_source": stop["geocode_source"],
                "priority": stop["priority"],
                "leg_distance_miles": round(leg_miles, 2),
                "leg_distance_km": round(leg_miles * 1.60934, 2),
                "leg_drive_minutes": round(leg_drive_mins, 1),
                "service_minutes": int(dwell_mins),
                "cumulative_miles": round(cumulative_miles, 2),
                "cumulative_minutes": round(cumulative_mins, 1),
                "origin_address": origin_str,
                "origin_geocode_source": origin_source,
                "destination_address": dest_str or None,
                "naive_route_total_miles": round(naive_miles, 2),
                "optimized_route_total_miles": round(opt_miles, 2),
                "distance_saved_miles": round(saved_miles, 2),
                "distance_saved_pct": saved_pct,
            }
        )

    nav_url = build_google_maps_url(
        origin_str=origin_str,
        dest_str=dest_str if dest_str else None,
        waypoint_strs=scheduled_waypoint_strs,
    )

    for row in prelim_rows:
        row["google_maps_nav_url"] = nav_url

    return prelim_rows


async def main() -> None:
    if HAS_APIFY:
        async with Actor:
            actor_input = await Actor.get_input() or {}
            origin = str(actor_input.get("origin") or DEFAULT_ORIGIN)
            destination = str(actor_input.get("destination") if "destination" in actor_input else DEFAULT_ORIGIN)
            stops = actor_input.get("stops") or DEFAULT_STOPS
            max_minutes = int(actor_input.get("maxMinutes", 120))
            service_mins = int(actor_input.get("serviceMinutesPerStop", 12))
            avg_speed = int(actor_input.get("averageSpeedMph", 28))
            circuity_pct = int(actor_input.get("roadCircuityPercent", 128))

            Actor.log.info(
                f"Optimizing {len(stops)} stops (maxMinutes={max_minutes}, circuity={circuity_pct}%)..."
            )
            results = await optimize_route_pipeline(
                origin_raw=origin,
                destination_raw=destination,
                raw_stops=stops,
                max_minutes=max_minutes,
                default_service_minutes=service_mins,
                avg_speed_mph=avg_speed,
                road_circuity_percent=circuity_pct,
            )
            await Actor.push_data(results)
            await safe_charge_event("route-stop-optimized", count=len(results))
            if results:
                Actor.log.info(
                    f"Route optimized: {results[0]['naive_route_total_miles']} mi -> "
                    f"{results[0]['optimized_route_total_miles']} mi "
                    f"({results[0]['distance_saved_pct']}% saved). Pushed {len(results)} ordered stops."
                )
    else:
        results = await optimize_route_pipeline(
            origin_raw=DEFAULT_ORIGIN,
            destination_raw=DEFAULT_ORIGIN,
            raw_stops=DEFAULT_STOPS,
            max_minutes=120,
            default_service_minutes=12,
            avg_speed_mph=28,
            road_circuity_percent=128,
        )
        print(json.dumps(results, indent=2))
