import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.main import (
    DEFAULT_ORIGIN,
    DEFAULT_STOPS,
    _coerce_float,
    _coerce_int,
    build_google_maps_url,
    compute_sequence_miles,
    optimize_route_pipeline,
    parse_inline_coords,
    solve_nearest_neighbor_and_2opt,
)


def test_coerce_helpers():
    assert _coerce_int(None, 25, minimum=1) == 25
    assert _coerce_int("", 25, minimum=1) == 25
    assert _coerce_int(True, 25, minimum=1) == 25
    assert _coerce_int(0, 25, minimum=1) == 25
    assert _coerce_int("15", 25, minimum=1) == 15
    assert _coerce_int(0, 12, minimum=0) == 0

    assert _coerce_float(None) is None
    assert _coerce_float("") is None
    assert _coerce_float("nan") is None
    assert _coerce_float("39.0853") == 39.0853


def test_parse_inline_coords():
    assert parse_inline_coords("39.0853, -94.5852") == (39.0853, -94.5852)
    assert parse_inline_coords("95.0, 10.0") is None
    assert parse_inline_coords("20 E 5th St, Kansas City, MO") is None


def test_solve_2opt_improves_crossing_route():
    origin = (39.0, -94.6)
    pts = [
        (39.0, -94.5),
        (39.1, -94.5),
        (39.1, -94.6),
    ]
    tour = solve_nearest_neighbor_and_2opt(pts, origin, origin, road_factor=1.28)
    assert sorted(tour) == [0, 1, 2]
    naive = compute_sequence_miles(pts, [1, 0, 2], origin, origin, 1.28)
    opt = compute_sequence_miles(pts, tour, origin, origin, 1.28)
    assert opt <= naive


def test_build_google_maps_url_caps_waypoints():
    wps = [f"Stop {i}" for i in range(25)]
    url = build_google_maps_url("Origin", "Dest", wps)
    assert "destination=Dest" in url
    assert url.count("Stop+") == 20

    single = build_google_maps_url("Union Station", None, [])
    assert "maps/search/" in single


def test_optimize_route_pipeline_handles_nulls_and_time_window():
    raw_stops = [
        {
            "label": "Blank Optional Fields",
            "address": "20 E 5th St, Kansas City, MO 64106",
            "lat": "",
            "lng": "",
            "priority": None,
            "serviceMinutes": None,
        },
        {
            "label": "Far Inline",
            "address": "39.0419, -94.5908",
            "serviceMinutes": 200,
        },
        {"address": ""},
    ]
    rows = asyncio.run(
        optimize_route_pipeline(
            origin_raw=DEFAULT_ORIGIN,
            destination_raw="",
            raw_stops=raw_stops,
            max_minutes=30,
            default_service_minutes=12,
        )
    )
    assert len(rows) == 2
    assert rows[0]["within_time_window"] is True
    assert any(r["schedule_status"].startswith("Deferred") for r in rows)


def test_default_stops_pipeline():
    rows = asyncio.run(
        optimize_route_pipeline(
            origin_raw=DEFAULT_ORIGIN,
            destination_raw=DEFAULT_ORIGIN,
            raw_stops=DEFAULT_STOPS,
            max_minutes=120,
        )
    )
    assert len(rows) == len(DEFAULT_STOPS)
    assert rows[0]["optimized_route_total_miles"] <= rows[0]["naive_route_total_miles"]
