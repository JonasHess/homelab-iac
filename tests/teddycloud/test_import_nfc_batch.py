import random

import pytest
from conftest import _load

batch = _load("import_nfc_batch")
single = batch.import_nfc

AUTH = " ".join(["AB"] * 32)


def write_dump(path, uid="E0 04 03 50 12 34 56 78"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"Filetype: Flipper NFC device\nUID: {uid}\nData Content: {AUTH}\n")
    return path


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


class TestFindDumps:
    def test_walks_subfolders_sorted_and_ignores_other_files(self, tmp_path):
        b = write_dump(tmp_path / "German" / "B" / "B.nfc")
        a = write_dump(tmp_path / "German" / "A" / "A.NFC")
        (tmp_path / "German" / "A" / "cover.png").write_bytes(b"")
        assert batch.find_dumps(tmp_path) == [a, b]


class TestPlan:
    def test_splits_pending_cached_invalid_and_duplicates(self, tmp_path):
        new = write_dump(tmp_path / "a.nfc", uid="E0 04 03 50 00 00 00 01")
        cached = write_dump(tmp_path / "b.nfc", uid="E0 04 03 50 00 00 00 02")
        dupe = write_dump(tmp_path / "c.nfc", uid="E0 04 03 50 00 00 00 01")
        broken = tmp_path / "d.nfc"
        broken.write_text("UID: E0 04\n")
        sources = {"02000000500304E0": "lib://by/audioID/1.taf"}

        plan = batch.plan([new, cached, dupe, broken], lambda ruid: sources.get(ruid, ""))

        assert [path for path, _ in plan.pending] == [new]
        assert plan.cached == [cached]
        assert plan.duplicates == [dupe]
        assert [path for path, _ in plan.invalid] == [broken]


class TestRun:
    def run(self, outcomes, per_hour=10, max_failures=3):
        clock = FakeClock()
        pending = [(f"t{i}.nfc", {"ruid": str(i)}) for i in range(len(outcomes))]
        calls = []

        def import_one(dump):
            calls.append(dump["ruid"])
            clock.t += 60  # every import takes a minute
            outcome = outcomes[int(dump["ruid"])]
            if outcome == "fail":
                raise single.ImportFailed("boom")

        result = batch.run(pending, import_one, per_hour, max_failures,
                           sleep=clock.sleep, now=clock.now, rng=random.Random(1))
        return result, calls, clock

    def test_paces_start_to_start_and_never_sleeps_after_the_last(self):
        result, calls, clock = self.run(["ok", "ok", "ok"])
        assert calls == ["0", "1", "2"]
        assert len(clock.sleeps) == 2
        for slept in clock.sleeps:
            # 360 s interval +-25 % jitter, minus the minute the import took
            assert 270 - 60 <= slept <= 450 - 60
        assert result.imported == ["t0.nfc", "t1.nfc", "t2.nfc"]
        assert result.failed == [] and not result.aborted

    def test_about_ten_per_hour_on_average(self):
        rng = random.Random(7)
        delays = [batch.next_delay(10, rng) for _ in range(1000)]
        assert 340 < sum(delays) / len(delays) < 380

    def test_a_failure_is_recorded_and_the_run_continues(self):
        result, calls, _ = self.run(["ok", "fail", "ok"])
        assert calls == ["0", "1", "2"]
        assert result.imported == ["t0.nfc", "t2.nfc"]
        assert [path for path, _ in result.failed] == ["t1.nfc"]
        assert not result.aborted

    def test_aborts_after_consecutive_failures(self):
        result, calls, _ = self.run(["fail", "fail", "ok", "ok"], max_failures=2)
        assert calls == ["0", "1"]
        assert result.aborted

    def test_success_resets_the_failure_streak(self):
        result, calls, _ = self.run(["fail", "ok", "fail", "ok"], max_failures=2)
        assert calls == ["0", "1", "2", "3"]
        assert not result.aborted
