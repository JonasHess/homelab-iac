from conftest import _load

single = _load("import_nfc")

DUMP = {"ruid": "DEAA291F500304E0", "folder": "DEAA291F", "file": "500304E0"}


class FakeTeddyCloud(single.TeddyCloud):
    """Answers the content API from a dict of {path: response}; a missing path is a 404."""

    def __init__(self, responses):
        super().__init__("http://teddycloud.invalid")
        self.responses = responses

    def get_json(self, path):
        if path not in self.responses:
            raise single.ImportFailed(f"GET {path} failed: HTTP 404 Not Found")
        return self.responses[path]


def responses(source="", folder_files=None):
    out = {"/content/json/get/deaa291f500304e0": {"source": source}}
    out["/api/fileIndex?special=content&path=%2F"] = {"files": [{"name": "DEAA291F", "isDirectory": True}]}
    out["/api/fileIndex?special=content&path=%2FDEAA291F"] = {"files": folder_files or []}
    return out


JSON = {"name": "500304E0.json", "isDirectory": False, "size": 312, "has_cloud_auth": True}


class TestCachedAt:
    def test_the_library_source_wins(self):
        tc = FakeTeddyCloud(responses(source="lib://by/audioID/1.taf"))
        assert tc.cached_at(DUMP) == "lib://by/audioID/1.taf"

    def test_a_taf_next_to_the_json_counts_as_cached(self):
        taf = {"name": "500304E0", "isDirectory": False, "size": 57636349}
        tc = FakeTeddyCloud(responses(folder_files=[JSON, taf]))
        assert tc.cached_at(DUMP) == "content/DEAA291F/500304E0"

    def test_an_empty_file_next_to_the_json_does_not(self):
        taf = {"name": "500304E0", "isDirectory": False, "size": 0}
        tc = FakeTeddyCloud(responses(folder_files=[JSON, taf]))
        assert tc.cached_at(DUMP) == ""

    def test_a_tonie_teddycloud_has_never_seen_is_not_cached(self):
        tc = FakeTeddyCloud({"/api/fileIndex?special=content&path=%2F": {"files": []}})
        assert tc.cached_at(DUMP) == ""
