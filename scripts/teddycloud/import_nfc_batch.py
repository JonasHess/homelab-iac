#!/usr/bin/env python3
"""Import every Flipper .nfc tonie dump below a folder into teddyCloud, slowly.

Runs the import of import_nfc.py for each dump, spaced out to about --per-hour
tonies per hour (default 10, with +-25 % jitter), so Boxine sees a trickle of
downloads instead of a burst. The interval runs from the start of one import
to the start of the next, so a long download does not stretch the schedule.

Before the first download one pass over teddyCloud sorts the dumps into
already cached (skipped, no waiting), duplicates of the same tonie (skipped)
and unreadable dumps (reported). Stopping with Ctrl-C and re-running is safe:
everything cached by then is skipped.

Each tonie gets its own kubectl port-forward, since a single one is unlikely
to survive a run of many hours.

Usage:
  import_nfc_batch.py ~/tonies-backup
  import_nfc_batch.py --dry-run ~/tonies-backup   # only show the plan
"""

import argparse
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import import_nfc
from import_nfc import DEFAULT_CONTEXT, DEFAULT_NAMESPACE, DEFAULT_SERVICE, ImportFailed, PortForward, TeddyCloud

JITTER = 0.25


def find_dumps(root):
    return sorted(p for p in Path(root).rglob("*") if p.is_file() and p.suffix.lower() == ".nfc")


@dataclass
class Plan:
    pending: list = field(default_factory=list)     # (path, dump)
    cached: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)  # same rUID as an earlier dump
    invalid: list = field(default_factory=list)     # (path, reason)


def plan(paths, cached_at):
    """cached_at(dump) says where teddyCloud has a tonie cached, empty when it has not."""
    result = Plan()
    seen = set()
    for path in paths:
        try:
            dump = import_nfc.parse_dump(path)
        except (ImportFailed, OSError, ValueError) as err:
            result.invalid.append((path, str(err)))
            continue
        if dump["ruid"] in seen:
            result.duplicates.append(path)
            continue
        seen.add(dump["ruid"])
        if cached_at(dump):
            result.cached.append(path)
        else:
            result.pending.append((path, dump))
    return result


def next_delay(per_hour, rng):
    return 3600 / per_hour * rng.uniform(1 - JITTER, 1 + JITTER)


@dataclass
class Result:
    imported: list = field(default_factory=list)
    failed: list = field(default_factory=list)  # (path, reason)
    aborted: bool = False


def run(pending, import_one, per_hour, max_failures, sleep=time.sleep, now=time.monotonic, rng=None):
    """Imports the pending dumps in order; gives up after max_failures failures in a row.

    A failed import still waits out its interval: it may have reached Boxine.
    """
    rng = rng or random.Random()
    result = Result()
    streak = 0
    for i, (path, dump) in enumerate(pending, 1):
        started = now()
        print(f"\n[{time.strftime('%H:%M:%S')}] {i}/{len(pending)} {path}")
        try:
            import_one(dump)
        except (ImportFailed, OSError) as err:
            print(f"error: {err}", file=sys.stderr)
            result.failed.append((path, str(err)))
            streak += 1
            if streak >= max_failures:
                print(f"{streak} failures in a row, giving up", file=sys.stderr)
                result.aborted = True
                return result
        else:
            result.imported.append(path)
            streak = 0
        if i < len(pending):
            wait = max(0.0, started + next_delay(per_hour, rng) - now())
            print(f"next tonie in {wait / 60:.0f} min")
            sleep(wait)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", help="folder with the .nfc dumps, searched recursively")
    parser.add_argument("--per-hour", type=float, default=10, help="tonies to import per hour (default 10)")
    parser.add_argument("--max-failures", type=int, default=3, help="stop after this many failures in a row (default 3)")
    parser.add_argument("--context", default=DEFAULT_CONTEXT, help=f"kubectl context (default {DEFAULT_CONTEXT})")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help=f"namespace (default {DEFAULT_NAMESPACE})")
    parser.add_argument("--timeout", type=int, default=600, help="seconds allowed per network read (default 600)")
    parser.add_argument("--dry-run", action="store_true", help="only show what would be imported")
    args = parser.parse_args()
    if args.per_hour <= 0 or args.max_failures < 1:
        parser.error("--per-hour must be positive and --max-failures at least 1")
    # Runs for hours, usually with output redirected to a file.
    sys.stdout.reconfigure(line_buffering=True)

    root = Path(args.folder).expanduser()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    def port_forward():
        return PortForward(args.context, args.namespace, DEFAULT_SERVICE)

    try:
        with port_forward() as base_url:
            todo = plan(find_dumps(root), TeddyCloud(base_url).cached_at)
    except (ImportFailed, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    for path, reason in todo.invalid:
        print(f"unreadable: {path.relative_to(root)}: {reason}")
    for path in todo.duplicates:
        print(f"duplicate:  {path.relative_to(root)}")
    print(f"{len(todo.pending)} to import, {len(todo.cached)} already cached, "
          f"{len(todo.duplicates)} duplicates, {len(todo.invalid)} unreadable")
    if not todo.pending:
        return 0
    print(f"about {len(todo.pending) / args.per_hour:.1f} h at {args.per_hour:g} per hour")
    if args.dry_run:
        for path, _ in todo.pending:
            print(f"  {path.relative_to(root)}")
        return 0

    def import_one(dump):
        with port_forward() as base_url:
            import_nfc.import_dump(TeddyCloud(base_url), dump, force=False, dry_run=False, timeout=args.timeout)

    pending = [(path.relative_to(root), dump) for path, dump in todo.pending]
    try:
        result = run(pending, import_one, args.per_hour, args.max_failures)
    except KeyboardInterrupt:
        print("\ninterrupted; re-run to continue, cached tonies are skipped", file=sys.stderr)
        return 130

    print(f"\nimported {len(result.imported)}, failed {len(result.failed)}, "
          f"not attempted {len(pending) - len(result.imported) - len(result.failed)}")
    for path, reason in result.failed:
        print(f"  failed: {path}: {reason}")
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
