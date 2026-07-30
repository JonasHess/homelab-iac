#!/usr/bin/env python3
"""Recover blocked Radarr imports and clean stale SABnzbd orphan folders."""

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
    body: Any | None = None,
    method: str | None = None,
) -> Any:
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    request_headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        content = response.read()
        return json.loads(content) if content else {}


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


def delete_orphan(
    *,
    candidate: dict[str, Any],
    download_root: pathlib.Path,
    sabnzbd_url: str,
    sabnzbd_key: str,
    minimum_age: int,
) -> tuple[bool, int]:
    folder = candidate["folder"]
    current_status = sabnzbd_api(sabnzbd_url, sabnzbd_key, "status").get("status", {})
    if folder not in {str(item) for item in current_status.get("folders", [])}:
        return False, 0

    path = safe_orphan_path(download_root, folder)
    if path is None:
        return False, 0
    age_hours, total_bytes = directory_stats(path, time.time())
    if age_hours < minimum_age:
        return False, 0

    response = sabnzbd_api(
        sabnzbd_url,
        sabnzbd_key,
        "status",
        name="delete_orphan",
        value=folder,
    )
    if response.get("status") is not True:
        return False, 0
    return True, total_bytes


def active_download_ids(records: Iterable[dict[str, Any]]) -> set[str]:
    return {
        str(record.get("downloadId", "")).lower()
        for record in records
        if record.get("downloadId")
    }


def import_blocked_download(
    radarr_url: str,
    radarr_key: str,
    record: dict[str, Any],
) -> int:
    movie_id = record.get("movieId")
    download_id = record.get("downloadId")
    output_path = record.get("outputPath")
    if not movie_id or not download_id or not output_path:
        return 0

    headers = {"X-Api-Key": radarr_key}
    candidates = api_json(
        f"{radarr_url.rstrip('/')}/api/v3/manualimport",
        query={
            "folder": str(output_path),
            "downloadId": str(download_id),
            "filterExistingFiles": "true",
        },
        headers=headers,
    )
    files = [
        {
            "path": candidate["path"],
            "folderName": candidate.get("folderName"),
            "movieId": movie_id,
            "quality": candidate.get("quality"),
            "languages": candidate.get("languages", []),
            "releaseGroup": candidate.get("releaseGroup"),
            "indexerFlags": candidate.get("indexerFlags", 0),
            "downloadId": download_id,
            "movieFileId": candidate.get("movieFileId", 0),
        }
        for candidate in candidates
        if candidate.get("id")
        and candidate.get("path")
        and not candidate.get("rejections")
    ]
    if not files:
        return 0

    api_json(
        f"{radarr_url.rstrip('/')}/api/v3/command",
        headers=headers,
        body={"name": "ManualImport", "files": files, "importMode": "move"},
    )
    return len(files)


def remove_paused_encrypted(
    *,
    candidate: dict[str, Any],
    download_root: pathlib.Path,
    sabnzbd_url: str,
    sabnzbd_key: str,
    radarr_url: str,
    radarr_key: str,
    minimum_age: int,
) -> tuple[bool, int]:
    download_id = candidate["download_id"]
    current_queue = sabnzbd_api(sabnzbd_url, sabnzbd_key, "queue").get("queue", {})
    slot = next(
        (
            item
            for item in current_queue.get("slots", [])
            if str(item.get("nzo_id", "")).lower() == download_id
        ),
        None,
    )
    if (
        slot is None
        or str(slot.get("status", "")).lower() != "paused"
        or "ENCRYPTED" not in {str(label).upper() for label in slot.get("labels", [])}
    ):
        return False, 0

    path = safe_orphan_path(download_root, str(slot.get("filename", "")))
    if path is None:
        return False, 0
    age_hours, total_bytes = directory_stats(path, time.time())
    if age_hours < minimum_age:
        return False, 0

    radarr_queue = api_json(
        f"{radarr_url.rstrip('/')}/api/v3/queue",
        query={"page": "1", "pageSize": "1000", "includeUnknownMovieItems": "true"},
        headers={"X-Api-Key": radarr_key},
    )
    record = next(
        (
            item
            for item in radarr_queue.get("records", [])
            if str(item.get("downloadId", "")).lower() == download_id
        ),
        None,
    )
    if record is None or not record.get("id"):
        return False, 0

    api_json(
        f"{radarr_url.rstrip('/')}/api/v3/queue/{record['id']}",
        query={"removeFromClient": "true", "blocklist": "true"},
        headers={"X-Api-Key": radarr_key},
        method="DELETE",
    )
    return True, total_bytes


def main() -> int:
    started = time.time()
    sabnzbd_url = required_env("SABNZBD_URL")
    sabnzbd_key = required_env("SABNZBD_API_KEY")
    radarr_url = required_env("RADARR_URL")
    radarr_key = required_env("RADARR_API_KEY")
    download_root = pathlib.Path(required_env("DOWNLOAD_ROOT")).resolve(strict=True)
    minimum_age = integer_env("MINIMUM_AGE_HOURS", 48)
    maximum_candidates = integer_env("MAXIMUM_CANDIDATES_PER_RUN", 100)
    maximum_deletions = integer_env("MAXIMUM_DELETIONS_PER_RUN", 10)
    report_only = os.environ.get("REPORT_ONLY", "true").lower() == "true"

    # Complete every dependency check before considering any deletion.
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
    import_blocked_seen = 0
    manual_import_jobs = 0
    manual_import_files = 0
    manual_import_failures = 0
    encrypted_paused_seen = 0

    for record in radarr_queue.get("records", []):
        if (
            str(record.get("status", "")).lower() != "completed"
            or record.get("trackedDownloadState") != "importBlocked"
        ):
            continue
        import_blocked_seen += 1
        if report_only:
            continue
        try:
            imported_files = import_blocked_download(radarr_url, radarr_key, record)
        except Exception:
            manual_import_failures += 1
            continue
        if imported_files:
            manual_import_jobs += 1
            manual_import_files += imported_files

    for slot in queue.get("slots", []):
        if (
            str(slot.get("status", "")).lower() != "paused"
            or "ENCRYPTED" not in {str(label).upper() for label in slot.get("labels", [])}
        ):
            continue
        encrypted_paused_seen += 1
        path = safe_orphan_path(download_root, str(slot.get("filename", "")))
        if path is None:
            invalid_orphans += 1
            continue
        age_hours, total_bytes = directory_stats(path, now)
        if age_hours >= minimum_age:
            download_id = str(slot.get("nzo_id", "")).lower()
            candidates.append(
                {
                    "kind": "paused_encrypted",
                    "download_id": download_id,
                    "age_hours": age_hours,
                    "bytes": total_bytes,
                    "tracked_by_radarr": download_id in radarr_ids,
                }
            )

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
                    "folder": str(folder),
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

    candidates.sort(
        key=lambda item: (
            item["kind"] != "sabnzbd_orphan",
            item["tracked_by_radarr"],
            -item["age_hours"],
        )
    )
    truncated = len(candidates) > maximum_candidates
    candidates = candidates[:maximum_candidates]
    deleted_count = 0
    deleted_bytes = 0
    encrypted_removed_count = 0
    skipped_after_recheck = 0
    if not report_only:
        for candidate in candidates:
            if deleted_count >= maximum_deletions:
                break
            if candidate["kind"] == "sabnzbd_orphan":
                deleted, reclaimed = delete_orphan(
                    candidate=candidate,
                    download_root=download_root,
                    sabnzbd_url=sabnzbd_url,
                    sabnzbd_key=sabnzbd_key,
                    minimum_age=minimum_age,
                )
            elif candidate["kind"] == "paused_encrypted":
                deleted, reclaimed = remove_paused_encrypted(
                    candidate=candidate,
                    download_root=download_root,
                    sabnzbd_url=sabnzbd_url,
                    sabnzbd_key=sabnzbd_key,
                    radarr_url=radarr_url,
                    radarr_key=radarr_key,
                    minimum_age=minimum_age,
                )
            else:
                continue
            if deleted:
                deleted_count += 1
                deleted_bytes += reclaimed
                if candidate["kind"] == "paused_encrypted":
                    encrypted_removed_count += 1
            else:
                skipped_after_recheck += 1

    summary = {
        "mode": (
            "report-only"
            if report_only
            else "recover-blocked-imports-and-delete-confirmed-orphans"
        ),
        "sabnzbd_queue_status": queue.get("status"),
        "sabnzbd_queue_jobs": len(queue.get("slots", [])),
        "sabnzbd_orphans_reported": len(status.get("folders", [])),
        "invalid_orphan_paths": invalid_orphans,
        "failed_history_seen": failed_history_seen,
        "radarr_queue_jobs": int(radarr_queue.get("totalRecords", len(radarr_queue.get("records", [])))),
        "radarr_import_blocked_seen": import_blocked_seen,
        "manual_import_jobs": manual_import_jobs,
        "manual_import_files": manual_import_files,
        "manual_import_failures": manual_import_failures,
        "encrypted_paused_seen": encrypted_paused_seen,
        "encrypted_removed_count": encrypted_removed_count,
        "minimum_age_hours": minimum_age,
        "candidate_count": len(candidates),
        "candidate_bytes": sum(candidate["bytes"] for candidate in candidates),
        "candidate_list_truncated": truncated,
        "maximum_deletions_per_run": maximum_deletions,
        "deleted_count": deleted_count,
        "deleted_bytes": deleted_bytes,
        "skipped_after_recheck": skipped_after_recheck,
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
                {"mode": "sabnzbd-orphan-cleanup", "status": "error", "error_type": type(error).__name__},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
