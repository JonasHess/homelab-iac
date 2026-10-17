from pathlib import Path

from conftest import _load

names = _load("name_tonies")

OFFICIAL = [
    {"model": "10000581", "series": "100% Wolf", "episodes": "100% Wolf - Das Original-Hörspiel zum Kinofilm",
     "title": "100% Wolf - 100% Wolf - Das Original-Hörspiel zum Kinofilm", "language": "de-de"},
    {"model": "11004871", "series": "100 Wörter in den Sprachen dieser Welt",
     "episodes": "100 Wörter in den Sprachen dieser Welt - Set", "language": "de-de",
     "category": "audio-play-educational", "pic": "https://img/11004871.png"},
    {"model": "10001234", "series": "GEOlino", "episodes": "Alles über Vulkane", "language": "de-de"},
    {"model": "20001234", "series": "GEOlino", "episodes": "Alles über Vulkane", "language": "en-gb"},
    {"model": "10005678", "series": "Bobo", "episodes": "Folge 1", "language": "de-de",
     "category": "audio-play", "pic": "https://img/10005678.png"},
    {"model": "10005679", "series": "Bobo", "episodes": "Folge 1", "language": "de-de",
     "category": "audio-play", "pic": "https://img/10005679.png"},
    {"model": "10009001", "series": "Mixed", "episodes": "A", "language": "de-de", "category": "music"},
    {"model": "10009002", "series": "Mixed", "episodes": "B", "language": "de-de", "category": "audio-book"},
]


def test_norm_folds_umlauts_the_way_the_backup_folders_spell_them():
    assert names.norm("Alles über Vulkane!") == names.norm("Alles ueber Vulkane")
    assert names.norm("Die Glücksfüchse") == "diegluecksfuechse"


class TestDescribe:
    catalog = names.Catalog(OFFICIAL)

    def test_exact_episode_match_uses_the_official_model(self):
        name = self.catalog.describe(Path("Clever Tonies/GEOlino/Alles ueber Vulkane.nfc"))
        assert name.model == "10001234"

    def test_series_only_match_takes_the_official_spelling(self):
        name = self.catalog.describe(Path("100 Woerter in den Sprachen dieser Welt/Englisch.nfc"))
        assert name.model is None
        assert (name.series, name.episode) == ("100 Wörter in den Sprachen dieser Welt", "Englisch")

    def test_single_entry_series_lends_its_category_and_cover(self):
        name = self.catalog.describe(Path("100 Woerter in den Sprachen dieser Welt/Englisch.nfc"))
        assert (name.category, name.pic) == ("audio-play-educational", "https://img/11004871.png")

    def test_series_with_several_entries_lends_its_category_but_no_cover(self):
        name = self.catalog.describe(Path("Bobo/Folge 2.nfc"))
        assert (name.category, name.pic) == ("audio-play", "")

    def test_series_with_mixed_categories_lends_none(self):
        name = self.catalog.describe(Path("Mixed/C.nfc"))
        assert (name.category, name.pic) == ("", "")

    def test_unknown_series_keeps_the_folder_and_file_names(self):
        name = self.catalog.describe(Path("DC/Batman.nfc"))
        assert (name.model, name.series, name.episode) == (None, "DC", "Batman")

    def test_ambiguous_episode_is_not_guessed(self):
        name = self.catalog.describe(Path("Bobo/Folge 1.nfc"))
        assert name.model is None and name.series == "Bobo"


class TestCustomModel:
    header = {"audioId": 1786524025, "sha1Hash": "5ac2643139f9538e", "trackSeconds": [0, 410, 774]}

    def test_carries_audio_id_and_hash_so_teddycloud_matches_the_taf(self):
        model = names.custom_model("NFC-0001", names.Name(None, "Series", "Episode"), self.header)
        assert model["model"] == "NFC-0001"
        assert model["audio_id"] == ["1786524025"]
        assert model["hash"] == ["5AC2643139F9538E"]
        assert (model["series"], model["episodes"]) == ("Series", "Episode")
        assert "category" not in model and "pic" not in model

    def test_carries_category_and_cover_when_known(self):
        name = names.Name(None, "Series", "Episode", category="music", pic="https://img/1.png")
        model = names.custom_model("NFC-0001", name, self.header)
        assert (model["category"], model["pic"]) == ("music", "https://img/1.png")

    def test_next_id_continues_after_the_highest_in_the_namespace(self):
        existing = [{"model": "TS-0009"}, {"model": "NFC-0002"}, {"model": "NFC-0010"}]
        assert names.next_model_id(existing) == "NFC-0011"
        assert names.next_model_id([]) == "NFC-0001"

    def test_existing_custom_model_with_the_same_audio_is_reused(self):
        existing = [{"model": "NFC-0003", "audio_id": ["1786524025"], "hash": ["5AC2643139F9538E"]}]
        assert names.custom_for_audio(existing, self.header) == "NFC-0003"
        assert names.custom_for_audio(existing, {"audioId": 1, "sha1Hash": "5ac2643139f9538e"}) is None


OFFICIAL_AUDIO = [
    {"model": "10000581", "audio_id": ["1642145177", "1642145178"], "hash": ["821CD801", "821CD802"]},
    {"model": "10000999", "audio_id": ["555"], "hash": ["abcdef"]},
]


class TestPromotion:
    by_audio = names.official_by_audio(OFFICIAL_AUDIO)

    def test_audio_index_matches_like_teddycloud_any_id_with_any_hash(self):
        assert self.by_audio[("1642145178", "821CD801")] == "10000581"
        assert self.by_audio[("555", "ABCDEF")] == "10000999"

    def test_custom_model_whose_audio_became_official_is_promoted(self):
        custom = [
            {"model": "NFC-0001", "audio_id": ["555"], "hash": ["ABCDEF"]},
            {"model": "NFC-0002", "audio_id": ["555"], "hash": ["OTHER"]},
            {"model": "TS-0001", "audio_id": ["555"], "hash": ["ABCDEF"]},
        ]
        assert names.promotions(custom, self.by_audio, lambda model: False) == {"NFC-0001": "10000999"}


class FakeTeddyCloud:
    def __init__(self, contents, library):
        self.contents = contents
        self.library = library

    def get_json(self, path):
        if path.startswith("/api/fileIndexV2?"):
            return {"files": [{"name": f"{h['audioId']}.taf", "tafHeader": dict(h, valid=True)}
                              for h in self.library]}
        ruid = path.rsplit("/", 1)[1]
        if ruid not in self.contents:
            raise names.ImportFailed(f"GET {path} failed: HTTP 404 Not Found")
        return self.contents[ruid]


def ruid_of(uid_tail):
    return f"{uid_tail}500304e0"


class TestPlan:
    def test_names_promotes_and_links_by_audio(self, tmp_path):
        def dump(rel, tail):
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            uid = " ".join(["E0", "04", "03", "50"] + [tail[i:i + 2] for i in range(6, -1, -2)])
            path.write_text(f"UID: {uid}\nData Content: {' '.join(['AB'] * 32)}\n")

        dump("DC/Batman.nfc", "00000001")         # unknown everywhere -> new custom model
        dump("DC/Superman.nfc", "00000002")       # its audio is in tonies.json -> official
        dump("DC/Flash.nfc", "00000003")          # linked to NFC-0007, now official -> promote
        dump("DC/Robin.nfc", "00000004")          # already named -> untouched
        dump("DC/Joker.nfc", "00000005")          # not imported yet -> untouched
        lib = lambda audio_id: {"source": f"lib://by/audioID/{audio_id}.taf"}
        tc = FakeTeddyCloud(
            {ruid_of("00000001"): lib(1), ruid_of("00000002"): lib(555),
             ruid_of("00000003"): dict(lib(3), tonie_model="NFC-0007"),
             ruid_of("00000004"): dict(lib(4), tonie_model="10000581"),
             ruid_of("00000005"): {"source": ""}},
            [{"audioId": 1, "sha1Hash": "11"}, {"audioId": 555, "sha1Hash": "abcdef"},
             {"audioId": 3, "sha1Hash": "33"}, {"audioId": 4, "sha1Hash": "44"}],
        )
        custom = [{"model": "NFC-0007", "audio_id": ["3"], "hash": ["33"]}]
        by_audio = names.official_by_audio(OFFICIAL_AUDIO + [{"model": "10003333", "audio_id": ["3"], "hash": ["33"]}])

        steps, skipped, retired = names.plan(tc, tmp_path, names.Catalog([]), custom, by_audio)

        got = {step.rel_path.stem: (step.model_id, step.replaces, bool(step.new_model)) for step in steps}
        assert got == {
            "Batman": ("NFC-0008", None, True),
            "Superman": ("10000999", None, False),
            "Flash": ("10003333", "NFC-0007", False),
        }
        assert retired == ["NFC-0007"] and skipped == []


SETS = [
    {"model": "11000801", "series": "Coole Jobs", "episodes": "Coole Jobs - Set", "title": "Coole Jobs - Coole Jobs - Set",
     "language": "de-de", "category": "audio-play-educational", "pic": "https://img/set.png",
     "tracks": ["Tierfotografie - Wunderbare Wildnis", "Pizzabacken - Grammgenaue Wissenschaft"],
     "audio_id": ["1719490067", "1719490058"], "hash": ["AA", "BB"]},
    {"model": "11004634", "series": "Woozle Goozle", "episodes": "Woozle Goozle Set", "language": "de-de",
     "tracks": ["Insekten & Bienen", "Luft & Elektrizität"]},
    {"model": "10007777", "series": "Sunset", "episodes": "Sunset", "language": "de-de"},
]


class TestSets:
    catalog = names.Catalog(SETS)

    def test_a_set_is_an_entry_whose_episode_ends_in_the_word_set(self):
        assert self.catalog.is_set("11000801")
        assert self.catalog.is_set("11004634")
        assert not self.catalog.is_set("10007777")
        assert not self.catalog.is_set("NFC-0001")

    def test_figure_takes_the_track_it_matches_and_the_sets_cover(self):
        name = self.catalog.figure(Path("Coole Jobs/1 Tierfotografie - Wunderbare Wildnis.nfc"), "11000801")
        assert name.model is None
        assert (name.series, name.episode) == ("Coole Jobs", "Tierfotografie - Wunderbare Wildnis")
        assert (name.category, name.pic) == ("audio-play-educational", "https://img/set.png")

    def test_figure_restores_umlauts_from_the_track(self):
        name = self.catalog.figure(Path("Woozle Goozle/Luft & Elektrizitaet.nfc"), "11004634")
        assert name.episode == "Luft & Elektrizität"

    def test_figure_without_a_matching_track_keeps_the_file_name(self):
        name = self.catalog.figure(Path("Coole Jobs/5 Neu.nfc"), "11000801")
        assert name.episode == "5 Neu"

    def test_custom_model_is_never_promoted_to_a_set(self):
        custom = [{"model": "NFC-0001", "audio_id": ["1719490067"], "hash": ["AA"]}]
        by_audio = names.official_by_audio(SETS)
        assert names.promotions(custom, by_audio, self.catalog.is_set) == {}

    def test_set_figures_get_their_own_models(self, tmp_path):
        for stem, tail in (("1 Tierfotografie - Wunderbare Wildnis", "00000001"),
                           ("2 Pizzabacken - Grammgenaue Wissenschaft", "00000002")):
            path = tmp_path / "Coole Jobs" / f"{stem}.nfc"
            path.parent.mkdir(parents=True, exist_ok=True)
            uid = " ".join(["E0", "04", "03", "50"] + [tail[i:i + 2] for i in range(6, -1, -2)])
            path.write_text(f"UID: {uid}\nData Content: {' '.join(['AB'] * 32)}\n")
        tc = FakeTeddyCloud(
            # teddyCloud already linked the first to the set; the second is not named yet
            {ruid_of("00000001"): {"source": "lib://by/audioID/1719490067.taf", "tonie_model": "11000801"},
             ruid_of("00000002"): {"source": "lib://by/audioID/1719490058.taf"}},
            [{"audioId": 1719490067, "sha1Hash": "aa"}, {"audioId": 1719490058, "sha1Hash": "bb"}],
        )

        steps, _, retired = names.plan(tc, tmp_path, self.catalog, [], names.official_by_audio(SETS))

        got = {s.name.episode: (s.model_id, bool(s.new_model), s.replaces) for s in steps}
        assert got == {
            "Tierfotografie - Wunderbare Wildnis": ("NFC-0001", True, "11000801"),
            "Pizzabacken - Grammgenaue Wissenschaft": ("NFC-0002", True, None),
        }
        assert retired == []


STUBS = [
    # tonies.json knows the audio of these but nothing else about them
    {"model": "11003879", "series": None, "episodes": None, "title": "None - None",
     "audio_id": ["1775049960"], "hash": ["96BA"]},
    {"model": "11000356", "series": None, "episodes": None, "audio_id": ["1678897164"], "hash": ["1B6F"]},
    {"model": "10000581", "series": "100% Wolf", "episodes": "100% Wolf", "language": "de-de"},
]


class TestStubs:
    catalog = names.Catalog(STUBS)

    def test_an_entry_without_a_series_is_a_stub(self):
        assert self.catalog.is_stub("11003879")
        assert not self.catalog.is_stub("10000581")
        assert not self.catalog.is_stub("NFC-0001")

    def test_custom_model_is_not_promoted_to_a_stub(self):
        custom = [{"model": "NFC-0001", "audio_id": ["1775049960"], "hash": ["96BA"]}]
        assert names.promotions(custom, names.official_by_audio(STUBS), self.catalog.unsuitable) == {}

    def test_stub_tonies_are_named_from_the_backup(self, tmp_path):
        for stem, tail in (("Cuddle Tonie Stitch", "00000001"), ("Fantasia", "00000002")):
            path = tmp_path / "Disney" / f"{stem}.nfc"
            path.parent.mkdir(parents=True, exist_ok=True)
            uid = " ".join(["E0", "04", "03", "50"] + [tail[i:i + 2] for i in range(6, -1, -2)])
            path.write_text(f"UID: {uid}\nData Content: {' '.join(['AB'] * 32)}\n")
        tc = FakeTeddyCloud(
            # teddyCloud linked the first to the stub; the second is not named yet
            {ruid_of("00000001"): {"source": "lib://by/audioID/1775049960.taf", "tonie_model": "11003879"},
             ruid_of("00000002"): {"source": "lib://by/audioID/1678897164.taf"}},
            [{"audioId": 1775049960, "sha1Hash": "96ba"}, {"audioId": 1678897164, "sha1Hash": "1b6f"}],
        )

        steps, _, _ = names.plan(tc, tmp_path, self.catalog, [], names.official_by_audio(STUBS))

        got = {s.name.episode: (s.name.series, s.model_id, bool(s.new_model), s.replaces) for s in steps}
        assert got == {
            "Cuddle Tonie Stitch": ("Disney", "NFC-0001", True, "11003879"),
            "Fantasia": ("Disney", "NFC-0002", True, None),
        }
