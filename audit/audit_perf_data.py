#!/usr/bin/env python3
"""Reproducible inventory and CPU-ranking audit for RoboquestVR perf reports.

The script uses only the Python standard library.  It reads the immutable
artifact index/current-run JSON files plus the historical combat HTML, verifies
download coverage (and optionally SHA-256), and emits small CSVs suitable for
charting or a downstream report.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import statistics
import sys
import urllib.error
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / "downloads"
COMBAT_HTML = ROOT / "reports" / "combat-2026-07-19" / "index.html"


def number(text: str) -> float | None:
    cleaned = html.unescape(text).replace("\u2013", "-").replace(",", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class CombatParser(HTMLParser):
    """Extract tables and embedded chart-series JSON from the combat HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_level: str | None = None
        self.heading_tag: str | None = None
        self.heading_parts: list[str] = []
        self.last_heading = ""
        self.table: dict[str, Any] | None = None
        self.row: list[str] | None = None
        self.cell_parts: list[str] | None = None
        self.tables: list[dict[str, Any]] = []
        self.series: dict[str, dict[str, Any]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "article" and (values.get("id") or "").startswith("level-"):
            self.current_level = (values["id"] or "")[6:]
        if tag in {"h2", "h3"}:
            self.heading_tag = tag
            self.heading_parts = []
        if tag == "table":
            self.table = {"level": self.current_level, "heading": self.last_heading, "rows": []}
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"th", "td"} and self.row is not None:
            self.cell_parts = []
        if tag == "div" and "chart-shell" in (values.get("class") or "").split():
            payload = values.get("data-series")
            if self.current_level and payload:
                self.series[self.current_level] = json.loads(payload)

    def handle_data(self, data: str) -> None:
        if self.heading_tag:
            self.heading_parts.append(data)
        if self.cell_parts is not None:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h2", "h3"} and self.heading_tag == tag:
            self.last_heading = " ".join("".join(self.heading_parts).split())
            self.heading_tag = None
            self.heading_parts = []
        elif tag in {"th", "td"} and self.cell_parts is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell_parts).split()))
            self.cell_parts = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            self.table["rows"].append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None
        elif tag == "article":
            self.current_level = None


def load_json(name: str) -> dict[str, Any]:
    return json.loads((DOWNLOADS / name).read_text(encoding="utf-8"))


def artifact_audit(verify_sha: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index = load_json("artifact_index.json")
    rows: list[dict[str, Any]] = []
    for item in index["assets"]:
        path = DOWNLOADS / item["name"]
        actual = path.stat().st_size if path.exists() else -1
        sha = None
        sha_ok = None
        if verify_sha and path.exists() and actual == int(item["bytes"]):
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
            sha = digest.hexdigest()
            sha_ok = sha == item["sha256"]
        rows.append(
            {
                "name": item["name"],
                "extension": path.suffix.lower() or "(none)",
                "content_type": item["content_type"],
                "expected_bytes": int(item["bytes"]),
                "actual_bytes": actual,
                "size_ok": actual == int(item["bytes"]),
                "expected_sha256": item["sha256"],
                "actual_sha256": sha,
                "sha256_ok": sha_ok,
                "url": item["url"],
            }
        )
    return rows, index


def normal_spot_rows() -> list[dict[str, Any]]:
    summary = load_json("summary.json")
    manifest = load_json("manifest.json")
    renderdoc = load_json("renderdoc_analysis.json").get("captures", {})
    rows: list[dict[str, Any]] = []
    for level, level_data in summary["levels"].items():
        for spot, sample in level_data["spots"].items():
            vrapi = sample.get("vrapi") or {}
            gt = sample.get("gt") or {}
            perfetto = sample.get("perfetto") or {}
            threads = perfetto.get("threads_pct") or {}
            stage = sample.get("gpu_stages") or {}
            tick = (gt.get("FEngineLoop::Tick") or {}).get("per_frame_ms")
            wait = (gt.get("OpenXrWaitFrame") or {}).get("per_frame_ms")
            active = round(max(0.0, tick - wait), 3) if tick is not None and wait is not None else None
            capture = renderdoc.get(f"{level}_spot{spot}") or {}
            metadata = manifest["levels"][level]["spots"][spot]
            rows.append(
                {
                    "level": level,
                    "spot": spot,
                    "map": level_data.get("map"),
                    "seed": level_data.get("seed"),
                    "position": sample.get("pos"),
                    "yaw": sample.get("yaw"),
                    "trace_window": "-".join((metadata.get("insights") or {}).get("window") or []),
                    "fps_avg": vrapi.get("fps_avg"),
                    "fps_min": vrapi.get("fps_min"),
                    "app_gpu_ms_avg": vrapi.get("app_gpu_ms_avg"),
                    "app_gpu_ms_max": vrapi.get("app_gpu_ms_max"),
                    "vrapi_cpu_util": vrapi.get("cpu_util_avg"),
                    "perfetto_span_ms": perfetto.get("span_ms"),
                    "process_top20_thread_names_pct": perfetto.get("process_pct"),
                    "game_thread_core_pct": threads.get("GameThread"),
                    "render_thread_core_pct": threads.get("RenderThread"),
                    "rhi_thread_core_pct": threads.get("RHIThread"),
                    "foreground_worker_pct": threads.get("Foreground Work"),
                    "engine_tick_ms": tick,
                    "openxr_wait_ms": wait,
                    "active_gt_ms": active,
                    "world_tick_ms": (gt.get("UWorld_Tick") or {}).get("per_frame_ms"),
                    "component_tick_ms": (gt.get("FActorComponentTickFunction::ExecuteTick") or {}).get("per_frame_ms"),
                    "component_calls_per_frame": (gt.get("FActorComponentTickFunction::ExecuteTick") or {}).get("calls_per_frame"),
                    "receive_tick_ms": (gt.get("ReceiveTick") or {}).get("per_frame_ms"),
                    "skinned_mesh_tick_ms": (gt.get("USkinnedMeshComponent_TickComponent") or {}).get("per_frame_ms"),
                    "eye_gpu_ms": stage.get("eye_total_ms"),
                    "eye_binning_ms": stage.get("binning_ms"),
                    "eye_render_ms": stage.get("render_ms"),
                    "drawcalls": capture.get("drawcalls"),
                    "submitted_indices": capture.get("indices"),
                    "texture_bytes": capture.get("texture_bytes"),
                    "buffer_bytes": capture.get("buffer_bytes"),
                }
            )
    return rows


def normal_level_rankings(spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    levels = sorted({row["level"] for row in spots})
    for level in levels:
        samples = [row for row in spots if row["level"] == level]
        process = max(samples, key=lambda row: row["process_top20_thread_names_pct"] or -1)
        active = max(samples, key=lambda row: row["active_gt_ms"] or -1)
        rows.append(
            {
                "level": level,
                "max_process_spot": process["spot"],
                "max_process_top20_thread_names_pct": process["process_top20_thread_names_pct"],
                "process_spot_game_thread_pct": process["game_thread_core_pct"],
                "process_spot_render_thread_pct": process["render_thread_core_pct"],
                "process_spot_rhi_thread_pct": process["rhi_thread_core_pct"],
                "process_spot_active_gt_ms": process["active_gt_ms"],
                "max_active_gt_spot": active["spot"],
                "max_active_gt_ms": active["active_gt_ms"],
                "active_spot_fps": active["fps_avg"],
                "active_spot_gpu_ms": active["app_gpu_ms_avg"],
            }
        )
    return sorted(rows, key=lambda row: row["max_process_top20_thread_names_pct"], reverse=True)


def combat_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    parser = CombatParser()
    parser.feed(COMBAT_HTML.read_text(encoding="utf-8"))
    global_table = next(
        table for table in parser.tables
        if table["rows"] and table["rows"][0][:3] == ["level", "FPS", "min"]
    )
    headers = global_table["rows"][0]
    summary: list[dict[str, Any]] = []
    for values in global_table["rows"][1:]:
        raw = dict(zip(headers, values))
        summary.append(
            {
                "level": raw["level"],
                "fps_avg": number(raw["FPS"]),
                "fps_min": number(raw["min"]),
                "gpu_avg_ms": number(raw["GPU avg"]),
                "gpu_max_ms": number(raw["GPU max"]),
                "gt_avg_ms": number(raw["GT avg"]),
                "active_gt_ms": number(raw["active GT"]),
                "gt_p99_ms": number(raw["p99"]),
                "gt_worst_ms": number(raw["worst"]),
                "budget_miss_pct": number(raw["miss 72Hz"]),
                "kills": number(raw["kills"]),
                "teleports": number(raw["teleports"]),
                "trace_mb_reported": number(raw["trace"]),
            }
        )
    timers: list[dict[str, Any]] = []
    for table in parser.tables:
        if not table["level"] or not table["rows"]:
            continue
        header = table["rows"][0]
        if header[:2] != ["timer", "avg ms"]:
            continue
        for values in table["rows"][1:]:
            raw = dict(zip(header, values))
            timers.append(
                {
                    "level": table["level"],
                    "thread_group": "GameThread" if "GameThread" in table["heading"] else "RenderThread",
                    "timer": raw["timer"],
                    "avg_ms_per_call": number(raw["avg ms"]),
                    "inclusive_ms_per_frame": number(raw["ms/frame"]),
                    "median_ms_per_call": number(raw["median"]),
                    "max_ms_per_call": number(raw["max"]),
                    "calls_per_frame": number(raw["calls/frame"]),
                }
            )
    series_inventory: list[dict[str, Any]] = []
    for level, payload in parser.series.items():
        vrapi = payload.get("vrapi") or []
        events = payload.get("events") or []
        sf_098 = [point for point in vrapi if len(point) > 4 and point[4] == 0.98]
        sf_100 = [point for point in vrapi if len(point) > 4 and point[4] == 1.0]
        duplicate_timestamps = sum(
            current[0] == previous[0] for previous, current in zip(vrapi, vrapi[1:])
        )
        event_signatures = [json.dumps(event, sort_keys=True, separators=(",", ":")) for event in events]
        unique_event_signatures = set(event_signatures)
        kill_events = [event for event in events if event.get("kind") == "kill"]
        kill_signatures = {
            json.dumps(event, sort_keys=True, separators=(",", ":")) for event in kill_events
        }
        series_inventory.append(
            {
                "level": level,
                "bucketed_frame_points": len(payload.get("frame") or []),
                "vrapi_points": len(vrapi),
                "vrapi_duplicate_timestamps": duplicate_timestamps,
                "vrapi_sf_values": ";".join(str(value) for value in sorted({point[4] for point in vrapi})),
                "vrapi_all_rows_fps_mean": round(statistics.fmean(point[1] for point in vrapi), 2),
                "vrapi_all_rows_gpu_ms_mean": round(statistics.fmean(point[2] for point in vrapi), 2),
                "vrapi_sf_0_98_points": len(sf_098),
                "vrapi_sf_0_98_fps_mean": round(statistics.fmean(point[1] for point in sf_098), 2),
                "vrapi_sf_0_98_gpu_ms_mean": round(statistics.fmean(point[2] for point in sf_098), 2),
                "vrapi_sf_1_00_points": len(sf_100),
                "vrapi_sf_1_00_fps_mean": round(statistics.fmean(point[1] for point in sf_100), 2) if sf_100 else None,
                "vrapi_sf_1_00_gpu_ms_mean": round(statistics.fmean(point[2] for point in sf_100), 2) if sf_100 else None,
                "event_points": len(events),
                "event_exact_duplicate_rows": len(event_signatures) - len(unique_event_signatures),
                "kill_event_points": len(kill_events),
                "kill_event_unique_signatures": len(kill_signatures),
                "exact_spike_points": len(payload.get("spikes") or []),
                "series_end_s": (payload.get("frame") or payload.get("vrapi") or [[None]])[-1][0],
            }
        )
    return summary, timers, series_inventory


def combat_trace_links(check_http: bool) -> list[dict[str, Any]]:
    text = COMBAT_HTML.read_text(encoding="utf-8")
    urls = sorted(set(re.findall(r'href="(https://github\.com/[^"]+\.utrace)"', text)))
    rows: list[dict[str, Any]] = []
    for url in urls:
        status: int | str = "unchecked"
        content_length: int | None = None
        if check_http:
            request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    status = response.status
                    content_length = int(response.headers.get("Content-Length") or 0)
            except urllib.error.HTTPError as error:
                status = error.code
                content_length = int(error.headers.get("Content-Length") or 0)
            except Exception as error:  # Preserve the failure without aborting the local audit.
                status = f"error:{type(error).__name__}"
        rows.append(
            {
                "level": Path(url).stem,
                "url": url,
                "http_status": status,
                "content_length": content_length,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-sha", action="store_true", help="hash every indexed artifact (about 9.3 GB)")
    parser.add_argument("--check-combat-links", action="store_true", help="HEAD-check the 12 historical .utrace links")
    args = parser.parse_args()
    out = Path(__file__).resolve().parent / "out"
    out.mkdir(parents=True, exist_ok=True)

    artifacts, index = artifact_audit(args.verify_sha)
    normal = normal_spot_rows()
    normal_rank = normal_level_rankings(normal)
    combat, combat_timers, combat_series = combat_rows()
    combat_links = combat_trace_links(args.check_combat_links)

    write_csv(out / "artifact_inventory.csv", artifacts)
    write_csv(out / "normal_spots.csv", normal)
    write_csv(out / "normal_level_cpu_rankings.csv", normal_rank)
    write_csv(out / "combat_levels.csv", sorted(combat, key=lambda row: row["active_gt_ms"], reverse=True))
    write_csv(out / "combat_timer_breakdown.csv", combat_timers)
    write_csv(out / "combat_series_inventory.csv", combat_series)
    write_csv(out / "combat_trace_link_status.csv", combat_links)

    ext_counts = Counter(row["extension"] for row in artifacts)
    missing = [row for row in artifacts if not row["size_ok"]]
    sha_bad = [row for row in artifacts if row["sha256_ok"] is False]
    stage_coverage = sum(row["eye_gpu_ms"] is not None for row in normal)
    duplicate_combat_levels = [row["level"] for row in combat_series if row["vrapi_duplicate_timestamps"]]
    duplicate_event_levels = [row["level"] for row in combat_series if row["event_exact_duplicate_rows"]]
    digest = {
        "artifact_index_schema": index.get("schema"),
        "artifact_count": len(artifacts),
        "artifact_bytes": sum(row["expected_bytes"] for row in artifacts),
        "extensions": dict(sorted(ext_counts.items())),
        "size_mismatches": len(missing),
        "sha256_mismatches": len(sha_bad),
        "normal_levels": len({row["level"] for row in normal}),
        "normal_spots": len(normal),
        "normal_gpu_stage_coverage": f"{stage_coverage}/{len(normal)}",
        "combat_levels": len(combat),
        "combat_raw_trace_links": len(combat_links),
        "combat_raw_trace_links_non_200": sum(row["http_status"] != 200 for row in combat_links) if args.check_combat_links else None,
        "note": "process_pct is the sum of only the 20 highest thread-name groups in the bundled Perfetto query",
        "combat_vrapi_duplicate_timestamp_levels": duplicate_combat_levels,
        "combat_vrapi_note": "City, Energy_Electrical, and Mines contain extensive same-second rows from distinct SF branches; report FPS/GPU means average all rows",
        "combat_exact_duplicate_event_levels": duplicate_event_levels,
        "combat_event_note": "exact duplicate event rows are present in 11/12 levels; Purification_Water_0 duplicates every embedded kill row",
    }
    (out / "audit_digest.json").write_text(json.dumps(digest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(digest, indent=2))
    if missing or sha_bad:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
