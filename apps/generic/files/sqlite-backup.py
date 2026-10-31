"""Consistent copies of live SQLite databases, written next to each one.

DATABASES holds one database path per line. For each, SQLite's online backup API
copies the database while the app keeps writing, the copy is integrity-checked,
and only then renamed to <path>.backup, so <path>.backup is always the last good
copy. A failure leaves the previous copy in place and makes the exit code 1, so
the Job fails and the backup alerts see it.
"""

import os
import sqlite3
import sys


def backup(path: str) -> int:
    """Copy one database to <path>.backup and return the size of the copy."""
    tmp, dst = path + ".backup.tmp", path + ".backup"
    try:
        # mode=rw: a missing database is an error, not a new empty file.
        src = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=60)
        try:
            out = sqlite3.connect(tmp)
            try:
                src.backup(out)
                result = out.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                out.close()
        finally:
            src.close()
        if result != "ok":
            raise RuntimeError(f"integrity check of the copy failed: {result}")
        os.replace(tmp, dst)
        return os.path.getsize(dst)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def main() -> int:
    failed = False
    for path in os.environ["DATABASES"].split():
        try:
            size = backup(path)
            print(f"{path}: {size} bytes -> {path}.backup")
        except Exception as error:
            failed = True
            print(f"{path}: FAILED: {error}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
