import datetime, json, os, shutil, smtplib, ssl
from email.message import EmailMessage

# A bank consent is restored interactively: somebody completes the bank's SCA in the
# importer's web wizard, and the wizard records the resulting authorisation in its own
# job file. The nightly import does not read job files - it reads one configuration per
# bank from a directory - and nothing carries the new authorisation from the one to the
# other. Until something does, a re-authorisation that visibly worked in the browser
# leaves the nightly import failing exactly as before.
#
# This job is that carry. It copies four fields and nothing else, because every other
# field is a deliberate setting that the wizard may have answered differently: the
# detection method, the day window, the default account, whether rules run.
PATCH_FIELDS = ("created_at", "accounts",
                "enable_banking_auth_id", "enable_banking_sessions")


def finished(job):
    """Is this job file a completed Enable Banking run worth carrying over?

    Only the submission status can say so. A job sitting at waiting_to_start looks
    identical in every other respect, and the wizard leaves such jobs behind whenever
    somebody opens the flow and walks away.
    """
    cfg = job.get("configuration") or {}
    if isinstance(cfg, str):
        return False
    status = (job.get("submission_status") or {}).get("status")
    return cfg.get("flow") == "eb" and status == "submission_done"


def bank_of(cfg):
    """Normalised bank name, which is the only thing a job and a config share.

    The account UUIDs cannot be used: a re-authorisation issues new ones, which is
    precisely why the carry is needed.
    """
    return (cfg.get("enable_banking_bank") or "").strip().lower()


def plan(jobs, configs):
    """Match finished jobs to the configs they should update.

    jobs and configs are both (name, dict) pairs - the caller does the file reading, so
    the decisions here can be tested without a filesystem.

    Returns (updates, refusals). A refusal is a case that looks like it wants carrying
    but is not safe to carry unattended; it is reported rather than applied, because
    the alternative is writing a wrong mapping into the one file the nightly import
    trusts. Anything that is simply not a candidate is left out of both silently - with
    a nightly schedule, every already-carried authorisation would otherwise be reported
    again every single night.
    """
    by_bank = {}
    for name, job in jobs:
        if not finished(job):
            continue
        cfg = job["configuration"]
        bank = bank_of(cfg)
        if not bank:
            continue
        # Several finished runs for one bank are normal: a failed attempt followed by a
        # successful one. The newest authorisation is the live one.
        prev = by_bank.get(bank)
        if prev is None or cfg.get("created_at", "") > prev[1].get("created_at", ""):
            by_bank[bank] = (name, cfg)

    cfgs_by_bank = {}
    for name, cfg in configs:
        if cfg.get("flow") != "eb":
            continue
        cfgs_by_bank.setdefault(bank_of(cfg), []).append((name, cfg))

    updates, refusals = [], []
    for bank, (job_name, job_cfg) in sorted(by_bank.items()):
        targets = cfgs_by_bank.get(bank, [])
        if not targets:
            refusals.append(f"{job_name}: finished run for \"{bank}\" has no configuration "
                            f"to update - carry it by hand or the import stays broken")
            continue
        if len(targets) > 1:
            refusals.append(f"{job_name}: \"{bank}\" matches {len(targets)} configurations "
                            f"({', '.join(n for n, _ in targets)}) - too ambiguous to pick one")
            continue
        cfg_name, cfg = targets[0]

        if not job_cfg.get("accounts"):
            refusals.append(f"{cfg_name}: the finished run mapped no account at all, so it "
                            f"would import nothing")
            continue
        # The wizard offers to create a Firefly account for every account the bank
        # returns, and that is its default for each one. Carrying such a run over would
        # make the nightly import feed accounts nobody chose.
        if job_cfg.get("new_accounts"):
            refusals.append(f"{cfg_name}: the finished run created {len(job_cfg['new_accounts'])} "
                            f"new account(s) instead of mapping to existing ones")
            continue
        # Which bank account feeds which Firefly account is a decision, not a detail,
        # and on the wizard's mapping form every account carries the same holder name.
        # A changed set means either a misclick or a deliberate addition; both deserve
        # a human, so this job reports and leaves the configuration alone.
        was = {str(v) for v in cfg.get("accounts", {}).values()}
        now = {str(v) for v in job_cfg["accounts"].values()}
        if was and was != now:
            refusals.append(f"{cfg_name}: the finished run maps Firefly accounts "
                            f"{', '.join(sorted(now))} where the configuration has "
                            f"{', '.join(sorted(was))}")
            continue
        # Nothing to do once carried. Not a refusal: it is the normal state on every
        # night between two re-authorisations.
        if job_cfg.get("created_at", "") <= (cfg.get("created_at") or ""):
            continue

        updates.append({"config": cfg_name, "job": job_name, "bank": bank,
                        "fields": {f: job_cfg[f] for f in PATCH_FIELDS if f in job_cfg},
                        "was": cfg.get("created_at"), "now": job_cfg.get("created_at")})
    return updates, refusals


def read_json_dir(path):
    """Every *.json directly in path, as (filename, parsed). Unreadable files are
    skipped: a half-written or hand-kept copy must not stop the carry."""
    out = []
    if not os.path.isdir(path):
        return out
    for name in sorted(os.listdir(path)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(path, name)) as fh:
                out.append((name, json.load(fh)))
        except Exception:
            continue
    return out


def apply_update(import_dir, upd, today):
    """Patch one configuration in place, keeping the previous version beside it.

    Written whole rather than edited, so a crash cannot leave a truncated file where the
    nightly import expects valid JSON.
    """
    path = os.path.join(import_dir, upd["config"])
    with open(path) as fh:
        cfg = json.load(fh)
    shutil.copy2(path, f"{path}.before-{today.isoformat()}")
    cfg.update(upd["fields"])
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        fh.write(json.dumps(cfg, indent=4) + "\n")
    os.replace(tmp, path)


def send_mail(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{os.environ.get('MAIL_FROM_NAME','Firefly')} <{os.environ['MAIL_FROM_ADDRESS']}>"
    msg["To"] = os.environ["MAIL_DESTINATION"]
    msg.set_content(body)
    with smtplib.SMTP(os.environ["MAIL_HOST"], int(os.environ.get("MAIL_PORT", "587")), timeout=30) as sm:
        if os.environ.get("MAIL_ENCRYPTION", "tls") == "tls":
            sm.starttls(context=ssl.create_default_context())
        sm.login(os.environ["MAIL_USERNAME"], os.environ["MAIL_PASSWORD"])
        sm.send_message(msg)


def main():
    import_dir = os.environ.get("SYNC_CONSENT_IMPORT_DIR", "/import")
    jobs_dir   = os.environ.get("SYNC_CONSENT_JOBS_DIR", "/import-jobs")
    dry_run    = os.environ.get("SYNC_CONSENT_DRY_RUN", "false").lower() == "true"

    jobs    = read_json_dir(jobs_dir)
    configs = read_json_dir(import_dir)
    if not jobs:
        # The wizard's job files are the only source of a new authorisation. An empty
        # directory is normal between re-authorisations, but it is also what a lost
        # mount looks like, so say which it was.
        print(f"no job files under {jobs_dir} - nothing to carry")
        return 0

    updates, refusals = plan(jobs, configs)
    done = []
    for upd in updates:
        label = (f"{upd['config']} ({upd['bank']}) from {upd['job']}: "
                 f"authorised {upd['now']}, was {upd['was']}")
        if dry_run:
            done.append("would carry " + label)
            continue
        try:
            apply_update(import_dir, upd, datetime.date.today())
        except Exception as e:
            refusals.append(f"{upd['config']}: could not write - {e}")
            continue
        done.append("carried " + label)

    for line in done:     print("sync    :", line)
    for line in refusals: print("PROBLEM :", line)

    if not (done or refusals):
        print("every Enable Banking configuration already holds its newest authorisation")
        return 0

    bits = []
    if done:     bits.append(f"{len(done)} carried" if not dry_run else f"{len(done)} dry-run")
    if refusals: bits.append(f"{len(refusals)} needing attention")
    body = ""
    if done and dry_run:
        body += "Dry run, nothing written:\n" + "".join(f"  - {l}\n" for l in done) + "\n"
    elif done:
        body += "Carried into the nightly configuration:\n" + "".join(f"  - {l}\n" for l in done) + "\n"
    if refusals:
        body += ("Needs attention - these were NOT carried, so the nightly import keeps "
                 "using the old authorisation:\n"
                 + "".join(f"  - {l}\n" for l in refusals) + "\n")

    if not os.environ.get("MAIL_HOST"):
        print("no MAIL_HOST configured - not mailing")
    else:
        send_mail("Firefly bank consent sync: " + ", ".join(bits), body)
        print("mail sent")
    return 1 if refusals else 0


if __name__ == "__main__":
    raise SystemExit(main())
