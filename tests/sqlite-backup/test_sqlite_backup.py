import importlib.util
import pathlib
import sqlite3

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "apps" / "generic" / "files" / "sqlite-backup.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("sqlite_backup", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_db(path, rows, wal=True):
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (n INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    return con


def count(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT count(*) FROM t").fetchone()[0]
    finally:
        con.close()


def test_copy_includes_rows_still_in_the_wal(mod, tmp_path):
    db = tmp_path / "app.db"
    writer = make_db(db, 100)  # stays open: the rows live in app.db-wal, not app.db yet
    try:
        mod.backup(str(db))
    finally:
        writer.close()
    assert count(f"{db}.backup") == 100


def test_copy_while_another_connection_holds_a_write_transaction(mod, tmp_path):
    db = tmp_path / "app.db"
    make_db(db, 10).close()
    writer = sqlite3.connect(db)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO t VALUES (999)")
    try:
        mod.backup(str(db))  # WAL readers are not blocked by a writer
    finally:
        writer.rollback()
        writer.close()
    assert count(f"{db}.backup") == 10


def test_missing_database_fails_and_creates_nothing(mod, tmp_path):
    db = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        mod.backup(str(db))
    assert not db.exists()
    assert not (tmp_path / "missing.db.backup").exists()
    assert not (tmp_path / "missing.db.backup.tmp").exists()


def test_failed_run_keeps_the_previous_copy(mod, tmp_path, monkeypatch, capsys):
    good = tmp_path / "good.db"
    make_db(good, 5).close()
    mod.backup(str(good))
    good.unlink()  # next run fails for this database
    monkeypatch.setenv("DATABASES", f"{good}\n{tmp_path / 'other.db'}")
    make_db(tmp_path / "other.db", 3).close()
    assert mod.main() == 1
    assert count(f"{good}.backup") == 5
    assert count(f"{tmp_path / 'other.db'}.backup") == 3
    assert "FAILED" in capsys.readouterr().err
