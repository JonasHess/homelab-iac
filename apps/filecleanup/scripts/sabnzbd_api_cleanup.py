#!/usr/bin/env python3
"""Report stale SABnzbd jobs and orphan folders without exposing release names."""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable
from typing import Any


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is empty")
    return value


def integer_env(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


def api_json(
    url: str,
    *,
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def sabnzbd_api(base_url: str, api_key: str, mode: str, **query: str) -> dict[str, Any]:
    return api_json(
        f"{base_url.rstrip('/')}/api",
        query={"mode": mode, "output": "json", "apikey": api_key, **query},
    )


def directory_stats(path: pathlib.Path, now: float) -> tuple[int, int]:
    newest_mtime = path.stat().st_mtime
    total_bytes = 0
    for root, _, files in os.walk(path, followlinks=False):
        root_path = pathlib.Path(root)
        newest_mtime = max(newest_mtime, root_path.stat().st_mtime)
        for filename in files:
            file_path = root_path / filename
            try:
                stat_result = file_path.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            newest_mtime = max(newest_mtime, stat_result.st_mtime)
            total_bytes += stat_result.st_size
    age_hours = max(0, int((now - newest_mtime) // 3600))
    return age_hours, total_bytes


def safe_orphan_path(download_root: pathlib.Path, folder: str) -> pathlib.Path | None:
    if not folder or pathlib.PurePath(folder).name != folder:
        return None
    candidate = download_root / folder
    if candidate.is_symlink():
        return None
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(download_root)
    except (FileNotFoundError, RuntimeError, ValueError):
        return None
    if not resolved.is_dir():
        return None
    return resolved


def redact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": candidate["kind"],
        "age_hours": candidate["age_hours"],
        "bytes": candidate["bytes"],
        "tracked_by_radarr": candidate.get("tracked_by_radarr", False),
    }


def active_download_ids(records: Iterable[dict[str, Any]]) -> set[str]:
    return {
        str(record.get("downloadId", "")).lower()
        for record in records
        if record.get("downloadId")
    }


def main() -> int:
    started = time.time()
    sabnzbd_url = required_env("SABNZBD_URL")
    sabnzbd_key = required_env("SABNZBD_API_KEY")
    radarr_url = required_env("RADARR_URL")
    radarr_key = required_env("RADARR_API_KEY")
    download_root = pathlib.Path(required_env("DOWNLOAD_ROOT")).resolve(strict=True)
    minimum_age = integer_env("MINIMUM_AGE_HOURS", 48)
    maximum_candidates = integer_env("MAXIMUM_CANDIDATES_PER_RUN", 100)
    report_only = os.environ.get("REPORT_ONLY", "true").lower() == "true"
    if not report_only:
        raise RuntimeError("Deletion mode is not implemented; REPORT_ONLY must remain true")

    status = sabnzbd_api(sabnzbd_url, sabnzbd_key, "status").get("status", {})
    queue = sabnzbd_api(sabnzbd_url, sabnzbd_key, "queue").get("queue", {})
    history = sabnzbd_api(sabnzbd_url, sabnzbd_key, "history", limit="500").get("history", {})
    radarr_queue = api_json(
        f"{radarr_url.rstrip('/')}/api/v3/queue",
        query={"page": "1", "pageSize": "1000", "includeUnknownMovieItems": "true"},
        headers={"X-Api-Key": radarr_key},
    )

    radarr_ids = active_download_ids(radarr_queue.get("records", []))
    sab_queue_ids = {
        str(slot.get("nzo_id", "")).lower()
        for slot in queue.get("slots", [])
        if slot.get("nzo_id")
    }
    now = time.time()
    candidates: list[dict[str, Any]] = []
    invalid_orphans = 0

    for folder in status.get("folders", []):
        path = safe_orphan_path(download_root, str(folder))
        if path is None:
            invalid_orphans += 1
            continue
        age_hours, total_bytes = directory_stats(path, now)
        if age_hours >= minimum_age:
            candidates.append(
                {
                    "kind": "sabnzbd_orphan",
                    "age_hours": age_hours,
                    "bytes": total_bytes,
                    "tracked_by_radarr": False,
                }
            )

    failed_history_seen = 0
    for slot in history.get("slots", []):
        if str(slot.get("status", "")).lower() != "failed":
            continue
        failed_history_seen += 1
        download_id = str(slot.get("nzo_id", "")).lower()
        if not download_id or download_id in sab_queue_ids:
            continue
        completed = int(slot.get("completed") or 0)
        age_hours = max(0, int((now - completed) // 3600)) if completed else 0
        if age_hours < minimum_age:
            continue
        candidates.append(
            {
                "kind": "failed_history",
                "age_hours": age_hours,
                "bytes": int(slot.get("bytes") or 0),
                "tracked_by_radarr": download_id in radarr_ids,
            }
        )

    candidates.sort(key=lambda item: (item["tracked_by_radarr"], -item["age_hours"]))
    truncated = len(candidates) > maximum_candidates
    candidates = candidates[:maximum_candidates]
    summary = {
        "mode": "report-only",
        "sabnzbd_queue_status": queue.get("status"),
        "sabnzbd_queue_jobs": len(queue.get("slots", [])),
        "sabnzbd_orphans_reported": len(status.get("folders", [])),
        "invalid_orphan_paths": invalid_orphans,
        "failed_history_seen": failed_history_seen,
        "radarr_queue_jobs": int(radarr_queue.get("totalRecords", len(radarr_queue.get("records", [])))),
        "minimum_age_hours": minimum_age,
        "candidate_count": len(candidates),
        "candidate_bytes": sum(candidate["bytes"] for candidate in candidates),
        "candidate_list_truncated": truncated,
        "duration_seconds": round(time.time() - started, 3),
        "candidates": [redact_candidate(candidate) for candidate in candidates],
    }
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps(
                {"mode": "report-only", "status": "error", "error_type": type(error).__name__},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
