import json
import pytest
from conftest import _load

sc = _load("sync_consent")

OLD = "2026-07-25T16:11:18+02:00"
NEW = "2026-10-25T14:42:12+01:00"


def config(bank="C24", created=OLD, accounts=None, flow="eb", **extra):
    cfg = {"flow": flow, "enable_banking_bank": bank, "created_at": created,
           "accounts": {"old-uuid-1": 236, "old-uuid-2": 237} if accounts is None else accounts,
           "enable_banking_auth_id": "auth-old",
           "enable_banking_sessions": ["session-old"],
           "duplicate_detection_method": "cell", "nordigen_max_days": "30",
           "apply_rules": True, "default_account": 235}
    cfg.update(extra)
    return cfg


def job(bank="C24", created=NEW, accounts=None, status="submission_done",
        flow="eb", new_accounts=None, **extra):
    cfg = {"flow": flow, "enable_banking_bank": bank, "created_at": created,
           "accounts": {"new-uuid-1": 236, "new-uuid-2": 237} if accounts is None else accounts,
           "enable_banking_auth_id": "auth-new",
           "enable_banking_sessions": ["session-new"],
           "new_accounts": new_accounts if new_accounts is not None else [],
           # Settings the wizard answers for itself. Carrying these would undo the
           # deliberate ones in the nightly configuration.
           "duplicate_detection_method": "classic", "nordigen_max_days": "0",
           "apply_rules": None, "default_account": 0}
    cfg.update(extra)
    return {"configuration": cfg, "submission_status": {"status": status}}


class TestCandidates:
    def test_finished_eb_job(self):
        assert sc.finished(job())

    def test_unfinished_job_is_not_a_candidate(self):
        assert not sc.finished(job(status="waiting_to_start"))
        assert not sc.finished(job(status="submission_errored"))

    def test_other_flows_are_not_candidates(self):
        assert not sc.finished(job(flow="nordigen"))
        assert not sc.finished(job(flow="file"))

    def test_string_configuration_is_not_a_candidate(self):
        assert not sc.finished({"configuration": "{}",
                                "submission_status": {"status": "submission_done"}})

    def test_missing_status_is_not_a_candidate(self):
        assert not sc.finished({"configuration": job()["configuration"]})

    def test_bank_name_is_normalised(self):
        assert sc.bank_of({"enable_banking_bank": "  C24 "}) == "c24"
        assert sc.bank_of({}) == ""


class TestCarry:
    def test_carries_the_four_fields_and_nothing_else(self):
        updates, refusals = sc.plan([("j.json", job())], [("c24.json", config())])
        assert refusals == []
        assert len(updates) == 1
        assert set(updates[0]["fields"]) == set(sc.PATCH_FIELDS)
        assert updates[0]["fields"]["enable_banking_sessions"] == ["session-new"]
        assert updates[0]["config"] == "c24.json"

    def test_deliberate_settings_are_never_carried(self):
        updates, _ = sc.plan([("j.json", job())], [("c24.json", config())])
        for field in ("duplicate_detection_method", "nordigen_max_days",
                      "apply_rules", "default_account", "new_accounts"):
            assert field not in updates[0]["fields"]

    def test_matches_on_bank_regardless_of_file_name(self):
        updates, refusals = sc.plan([("whatever-uuid.json", job(bank="Comdirect",
                                                               accounts={"fresh": 99}))],
                                    [("comdirect-haushalt.json",
                                      config(bank="comdirect", accounts={"u": 99}))],)
        assert refusals == []
        assert updates[0]["config"] == "comdirect-haushalt.json"

    def test_newest_finished_job_per_bank_wins(self):
        older = job(created="2026-10-25T10:00:00+01:00", accounts={"a": 236, "b": 237})
        older["configuration"]["enable_banking_sessions"] = ["session-older"]
        updates, refusals = sc.plan([("new.json", job()), ("old.json", older)],
                                    [("c24.json", config())])
        assert refusals == []
        assert len(updates) == 1
        assert updates[0]["fields"]["enable_banking_sessions"] == ["session-new"]

    def test_several_banks_in_one_run(self):
        updates, refusals = sc.plan(
            [("a.json", job()), ("b.json", job(bank="Comdirect", accounts={"u": 99}))],
            [("c24.json", config()), ("comdirect.json", config(bank="Comdirect",
                                                              accounts={"old": 99}))])
        assert refusals == []
        assert sorted(u["config"] for u in updates) == ["c24.json", "comdirect.json"]

    def test_config_without_mapping_yet_is_accepted(self):
        updates, refusals = sc.plan([("j.json", job())], [("c24.json", config(accounts={}))])
        assert refusals == []
        assert len(updates) == 1


class TestQuietCases:
    def test_already_carried_is_silent(self):
        updates, refusals = sc.plan([("j.json", job())], [("c24.json", config(created=NEW))])
        assert (updates, refusals) == ([], [])

    def test_older_job_than_config_is_silent(self):
        updates, refusals = sc.plan([("j.json", job(created=OLD))],
                                    [("c24.json", config(created=NEW))])
        assert (updates, refusals) == ([], [])

    def test_unfinished_job_is_silent_not_refused(self):
        updates, refusals = sc.plan([("j.json", job(status="waiting_to_start"))],
                                    [("c24.json", config())])
        assert (updates, refusals) == ([], [])

    def test_non_eb_configs_are_ignored(self):
        updates, refusals = sc.plan([("j.json", job())],
                                    [("file.json", config(flow="file"))])
        assert updates == []
        assert len(refusals) == 1 and "no configuration" in refusals[0]


class TestRefusals:
    def test_new_accounts_in_the_run(self):
        updates, refusals = sc.plan([("j.json", job(new_accounts=[{"name": "Michael"}]))],
                                    [("c24.json", config())])
        assert updates == []
        assert "new account" in refusals[0]

    def test_run_mapped_nothing(self):
        updates, refusals = sc.plan([("j.json", job(accounts={}))], [("c24.json", config())])
        assert updates == []
        assert "mapped no account" in refusals[0]

    def test_changed_mapping_is_refused(self):
        updates, refusals = sc.plan([("j.json", job(accounts={"x": 236, "y": 999}))],
                                    [("c24.json", config())])
        assert updates == []
        assert "999" in refusals[0] and "237" in refusals[0]

    def test_an_added_account_is_refused_too(self):
        updates, refusals = sc.plan([("j.json", job(accounts={"x": 236, "y": 237, "z": 235}))],
                                    [("c24.json", config())])
        assert updates == []
        assert "235" in refusals[0]

    def test_same_mapping_in_a_different_order_is_not_a_change(self):
        updates, refusals = sc.plan(
            [("j.json", job(accounts={"z": "237", "a": 236}))],
            [("c24.json", config(accounts={"old-2": 237, "old-1": "236"}))])
        assert refusals == []
        assert len(updates) == 1

    def test_no_config_for_the_bank(self):
        updates, refusals = sc.plan([("j.json", job(bank="Sparkasse"))],
                                    [("c24.json", config())])
        assert updates == []
        assert "Sparkasse".lower() in refusals[0]

    def test_two_configs_for_one_bank(self):
        updates, refusals = sc.plan(
            [("j.json", job())],
            [("c24-a.json", config()), ("c24-b.json", config())])
        assert updates == []
        assert "ambiguous" in refusals[0]
        assert "c24-a.json" in refusals[0] and "c24-b.json" in refusals[0]


class TestFiles:
    def test_reads_only_json_and_tolerates_junk(self, tmp_path):
        (tmp_path / "good.json").write_text(json.dumps(config()))
        (tmp_path / "broken.json").write_text("{not json")
        (tmp_path / "ignored.json.bak-20260911").write_text(json.dumps(config()))
        (tmp_path / "notes.txt").write_text("hello")
        assert [n for n, _ in sc.read_json_dir(str(tmp_path))] == ["good.json"]

    def test_missing_directory_is_empty_not_an_error(self, tmp_path):
        assert sc.read_json_dir(str(tmp_path / "nope")) == []

    def test_apply_keeps_a_backup_and_only_touches_the_four_fields(self, tmp_path):
        import datetime
        before = config()
        (tmp_path / "c24.json").write_text(json.dumps(before, indent=4))
        upd = {"config": "c24.json",
               "fields": {"created_at": NEW, "accounts": {"new": 236},
                          "enable_banking_auth_id": "auth-new",
                          "enable_banking_sessions": ["session-new"]}}
        sc.apply_update(str(tmp_path), upd, datetime.date(2026, 10, 25))

        after = json.loads((tmp_path / "c24.json").read_text())
        assert after["enable_banking_sessions"] == ["session-new"]
        assert after["created_at"] == NEW
        # Everything the wizard might have answered differently survives untouched.
        for field in ("duplicate_detection_method", "nordigen_max_days",
                      "apply_rules", "default_account"):
            assert after[field] == before[field]

        kept = json.loads((tmp_path / "c24.json.before-2026-10-25").read_text())
        assert kept == before

    def test_apply_leaves_no_temp_file_behind(self, tmp_path):
        import datetime
        (tmp_path / "c24.json").write_text(json.dumps(config(), indent=4))
        sc.apply_update(str(tmp_path), {"config": "c24.json", "fields": {"created_at": NEW}},
                        datetime.date(2026, 10, 25))
        assert not (tmp_path / "c24.json.tmp").exists()
