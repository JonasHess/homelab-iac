#!/usr/bin/env python3
"""Give imported tonies a name in teddyCloud, using the .nfc backup names.

teddyCloud shows a tonie's name and cover only when its content JSON has a
tonie_model, and fills that in by itself only when the cached TAF's audio ID is
in tonies.json. Audio that Boxine re-issued is often missing there, so such
tonies show up as a bare "Tonie/Tag". This walks the same backup folder as
import_nfc_batch.py and, for every cached tonie without a tonie_model:

  - links it to the official tonies.json model when the TAF's audio ID and
    hash are listed there, or when the folder is a known series and the file
    name is exactly one of its episodes (official cover and track names);
  - otherwise creates a custom model NFC-nnnn in tonies.custom.json, named
    "<folder> / <file>" (official series spelling when the series is known),
    carrying the TAF's audio_id and hash, and links the tonie to it. A known
    series also lends its category (when all its entries agree) and its cover
    (only when it is a single entry, such as a set).

Names are matched with umlauts folded to ae/oe/ue/ss, since the backup
folders spell them out that way. An episode that matches more than one model
is not guessed. A figure missing from tonies.json has no cover of its own to
take; add one in tonie-studio, which edits the same tonies.custom.json.

A set (a tonies.json entry whose episode ends in "Set", such as "Coole Jobs -
Set") lists the audio of all its figures under one model, so teddyCloud names
every figure after the set and shows the set's figure list as their tracks.
Each figure of a set gets its own NFC-nnnn model instead: the set's series,
cover and category, and as episode the set track that the file name matches
(the file name when none does).

A stub (a tonies.json entry that has an audio ID but no series, name or
picture) makes teddyCloud show the bare article number. Its tonies are named
from the backup like unknown ones.

An NFC-nnnn model whose audio has since been added to tonies.json is replaced
by the official one, unless that is a set or a stub: its tonies are relinked
and the custom model is deleted.
teddyCloud would never do that itself, because it looks up custom models first
and does not touch a tonie_model that is already set. An official model always
describes the same recording at least as well, so this needs no confirmation.

Safe to re-run: tonies that already have a tonie_model are skipped, and a
custom model with the same audio is reused.

Usage:
  name_tonies.py ~/tonies-backup
  name_tonies.py --dry-run ~/tonies-backup   # only show the plan
"""

import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import import_nfc
from import_nfc import DEFAULT_CONTEXT, DEFAULT_NAMESPACE, DEFAULT_SERVICE, ImportFailed, PortForward, TeddyCloud
from import_nfc_batch import find_dumps

MODEL_PREFIX = "NFC-"
LANGUAGE = "de-de"
LIBRARY_PREFIX = "lib://by/audioID/"
# "Coole Jobs - Set", "Woozle Goozle Set", "Lernen mit LIKI und LUMI \u2013 Set"; not "Sunset".
SET_SUFFIX = re.compile(r"(^|[\s\-\u2013])set\s*$", re.IGNORECASE)


def norm(text):
    text = text.lower()
    for umlaut, spelled in (("\u00e4", "ae"), ("\u00f6", "oe"), ("\u00fc", "ue"), ("\u00df", "ss")):
        text = text.replace(umlaut, spelled)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text)


@dataclass
class Name:
    model: Optional[str]  # official model id, None when a custom model is needed
    series: str
    episode: str
    category: str = ""  # only when every official entry of the series agrees
    pic: str = ""       # only when the series has a single official entry


class Catalog:
    """The official tonies.json entries of one language, looked up by normalized name."""

    def __init__(self, entries, language=LANGUAGE):
        self.by_series = {}
        self.by_model = {entry["model"]: entry for entry in entries if entry.get("model")}
        for entry in entries:
            if entry.get("language") == language and entry.get("model") and entry.get("series"):
                self.by_series.setdefault(norm(entry["series"]), []).append(entry)

    def describe(self, rel_path):
        """rel_path is the dump's path below the backup folder: <...>/<series>/<episode>.nfc."""
        series, episode = rel_path.parent.name, rel_path.stem
        entries = self.by_series.get(norm(series))
        if not entries:
            return Name(None, series, episode)
        wanted = norm(episode)
        models = {e["model"] for e in entries
                  if wanted in (norm(e.get("episodes") or ""), norm(e.get("title") or ""))}
        official_series = entries[0]["series"]
        if len(models) == 1:
            return Name(models.pop(), official_series, episode)
        categories = {e.get("category") or "" for e in entries}
        category = categories.pop() if len(categories) == 1 else ""
        # Another episode's cover would be the wrong picture; a series that is
        # one product (a set, a single tonie) shows what this tonie belongs to.
        pic = (entries[0].get("pic") or "") if len(entries) == 1 else ""
        return Name(None, official_series, episode, category, pic)

    def is_set(self, model):
        entry = self.by_model.get(model) or {}
        return any(SET_SUFFIX.search(entry.get(key) or "") for key in ("episodes", "title"))

    def is_stub(self, model):
        return model in self.by_model and not self.by_model[model].get("series")

    def unsuitable(self, model):
        """Whether a tonie linked to this official model is better off with its own NFC model."""
        return self.is_set(model) or self.is_stub(model)

    def figure(self, rel_path, set_model):
        """The name of one figure of a set, from the set entry and the dump's file name."""
        entry = self.by_model[set_model]
        stem = norm(rel_path.stem)
        tracks = [t for t in entry.get("tracks") or [] if norm(t) and (norm(t) in stem or stem in norm(t))]
        episode = tracks[0] if len(tracks) == 1 else rel_path.stem
        return Name(None, entry.get("series") or rel_path.parent.name, episode,
                    entry.get("category") or "", entry.get("pic") or "")


def custom_model(model_id, name, header):
    model = {
        "model": model_id, "series": name.series, "episodes": name.episode, "tracks": [],
        "language": LANGUAGE, "audio_id": [str(header["audioId"])], "hash": [header["sha1Hash"].upper()],
    }
    if name.category:
        model["category"] = name.category
    if name.pic:
        model["pic"] = name.pic
    return model


def next_model_id(custom):
    used = [int(m["model"][len(MODEL_PREFIX):]) for m in custom
            if m.get("model", "").startswith(MODEL_PREFIX) and m["model"][len(MODEL_PREFIX):].isdigit()]
    return f"{MODEL_PREFIX}{max(used, default=0) + 1:04d}"


def custom_for_audio(custom, header):
    """The custom model that already carries this TAF (same audio_id and hash), or None."""
    audio_id, sha1 = str(header["audioId"]), header["sha1Hash"].upper()
    for model in custom:
        if (audio_id in [str(a) for a in model.get("audio_id") or []]
                and sha1 in [h.upper() for h in model.get("hash") or []]):
            return model["model"]
    return None


def official_by_audio(entries):
    """{(audio_id, HASH): model} over tonies.json.

    teddyCloud accepts any of an entry's audio IDs together with any of its
    hashes, so every combination is a key.
    """
    index = {}
    for entry in entries:
        if not entry.get("model"):
            continue
        for audio_id in entry.get("audio_id") or []:
            for sha1 in entry.get("hash") or []:
                index[(str(audio_id), sha1.upper())] = entry["model"]
    return index


def promotions(custom, by_audio, unsuitable):
    """{NFC model: official model} for the custom models whose audio tonies.json now lists.

    A set or a stub is never a promotion target: it is what the custom model was
    made to replace. A stub that upstream fills in later stops being one.
    """
    found = {}
    for model in custom:
        if not model.get("model", "").startswith(MODEL_PREFIX):
            continue
        for audio_id in model.get("audio_id") or []:
            for sha1 in model.get("hash") or []:
                official = by_audio.get((str(audio_id), sha1.upper()))
                if official and not unsuitable(official):
                    found[model["model"]] = official
    return found


def expect_ok(resp, what):
    # These endpoints report a refusal as HTTP 200 with an error text.
    body = resp.read().decode("utf-8", "replace").strip()
    if body != "OK":
        raise ImportFailed(f"teddyCloud refused {what}: {body[:200] or 'empty answer'}")


def upsert_custom(tc, model):
    with tc.request("POST", "/api/toniesCustomJsonUpsert", body=json.dumps(model).encode(),
                    headers={"Content-Type": "application/json"}) as resp:
        expect_ok(resp, f"custom model {model['model']}")


def set_tonie_model(tc, ruid, model_id):
    body = urllib.parse.urlencode({"tonie_model": model_id}).encode()
    with tc.request("POST", f"/content/json/set/{ruid.lower()}", body=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}) as resp:
        expect_ok(resp, f"tonie_model of {ruid}")


def delete_custom(tc, model_ids):
    with tc.request("POST", "/api/toniesCustomJsonDelete", body=json.dumps({"models": model_ids}).encode(),
                    headers={"Content-Type": "application/json"}) as resp:
        expect_ok(resp, f"deleting {', '.join(model_ids)}")


def content_json(tc, ruid):
    # A tonie teddyCloud has never seen answers 404 when its folder is missing.
    try:
        return tc.get_json(f"/content/json/get/{ruid.lower()}")
    except ImportFailed as err:
        if "HTTP 404" in str(err):
            return {}
        raise


def library_headers(tc):
    """TAF headers of the cached library files, by file name (<audioId>.taf)."""
    query = urllib.parse.urlencode({"special": "library", "path": "/by/audioID"})
    files = tc.get_json(f"/api/fileIndexV2?{query}").get("files", [])
    return {f["name"]: f["tafHeader"] for f in files if (f.get("tafHeader") or {}).get("valid")}


@dataclass
class Step:
    rel_path: Path
    ruid: str
    name: Name
    model_id: str
    new_model: Optional[dict] = None  # set when a custom model has to be written first
    replaces: Optional[str] = None    # what this tonie is relinked away from: a retired NFC model or a set


def plan(tc, root, catalog, custom, by_audio):
    """Returns the steps, the skipped dumps and the NFC models to delete once the steps ran."""
    headers = library_headers(tc)
    promoted = promotions(custom, by_audio, catalog.unsuitable)
    # Retired models stay in the numbering: a new model taking one of their ids
    # would be deleted again together with them at the end of the run.
    numbered = list(custom)
    custom = [m for m in custom if m.get("model") not in promoted]
    steps, skipped = [], []
    seen = set()
    for path in find_dumps(root):
        rel = path.relative_to(root)
        try:
            dump = import_nfc.parse_dump(path)
        except (ImportFailed, OSError, ValueError):
            continue
        if dump["ruid"] in seen:
            continue
        seen.add(dump["ruid"])

        content = content_json(tc, dump["ruid"])
        source = content.get("source") or ""
        current = content.get("tonie_model") or ""
        if current in promoted:
            steps.append(Step(rel, dump["ruid"], catalog.describe(rel), promoted[current], replaces=current))
            continue
        if not source or (current and not catalog.unsuitable(current)):
            continue
        header = headers.get(source[len(LIBRARY_PREFIX):]) if source.startswith(LIBRARY_PREFIX) else None
        if not header:
            skipped.append((rel, f"source {source} is not a TAF in the library"))
            continue

        official = current or by_audio.get((str(header["audioId"]), header["sha1Hash"].upper()))
        if official and catalog.is_stub(official):
            official = None
        if official and catalog.is_set(official):
            name = catalog.figure(rel, official)
        elif official:
            steps.append(Step(rel, dump["ruid"], catalog.describe(rel), official))
            continue
        else:
            name = catalog.describe(rel)
            if name.model:
                steps.append(Step(rel, dump["ruid"], name, name.model))
                continue
        replaces = current or None
        existing = custom_for_audio(custom, header)
        if existing:
            steps.append(Step(rel, dump["ruid"], name, existing, replaces=replaces))
            continue
        model = custom_model(next_model_id(numbered), name, header)
        custom.append(model)
        numbered.append(model)
        steps.append(Step(rel, dump["ruid"], name, model["model"], model, replaces=replaces))
    return steps, skipped, sorted(promoted)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", help="folder with the .nfc dumps, searched recursively")
    parser.add_argument("--dry-run", action="store_true", help="only show the plan, write nothing")
    parser.add_argument("--context", default=DEFAULT_CONTEXT, help=f"kubectl context (default {DEFAULT_CONTEXT})")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help=f"namespace (default {DEFAULT_NAMESPACE})")
    args = parser.parse_args()

    root = Path(args.folder).expanduser()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    try:
        with PortForward(args.context, args.namespace, DEFAULT_SERVICE) as base_url:
            tc = TeddyCloud(base_url)
            official = tc.get_json("/api/toniesJson")
            catalog = Catalog(official)
            steps, skipped, retired = plan(tc, root, catalog, tc.get_json("/api/toniesCustomJson"),
                                           official_by_audio(official))

            for rel, reason in skipped:
                print(f"skip      {rel}: {reason}")
            for step in steps:
                if step.replaces and step.replaces.startswith(MODEL_PREFIX):
                    kind = "promote"
                elif step.replaces:
                    kind = "split" if catalog.is_set(step.replaces) else "replace"
                elif step.new_model:
                    kind = "new"
                else:
                    kind = "custom" if step.model_id.startswith(MODEL_PREFIX) else "official"
                extras = ", ".join(x for x in (step.name.category, "cover" if step.name.pic else "") if x)
                extras = f"  [{extras}]" if step.new_model and extras else ""
                if step.replaces and not step.replaces.startswith(MODEL_PREFIX):
                    what = "set" if catalog.is_set(step.replaces) else "empty entry"
                    extras += f"  (was {what} {step.replaces})"
                print(f"{kind:9} {step.model_id:10} {step.rel_path}  ->  "
                      f"{step.name.series} / {step.name.episode}{extras}")
            official_steps = sum(1 for s in steps if not s.model_id.startswith(MODEL_PREFIX))
            print(f"\n{len(steps)} to link: {official_steps} official, {len(steps) - official_steps} custom "
                  f"({sum(1 for s in steps if s.new_model)} new models); "
                  f"{len(retired)} custom models to delete; {len(skipped)} skipped")
            if args.dry_run or not (steps or retired):
                return 0

            for step in steps:
                if step.new_model:
                    upsert_custom(tc, step.new_model)
                set_tonie_model(tc, step.ruid, step.model_id)
            # Only after every tonie points away from them.
            if retired:
                delete_custom(tc, retired)
            print(f"linked {len(steps)} tonies, deleted {len(retired)} custom models")
    except (ImportFailed, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
