#!/usr/bin/env python3
"""Import one Flipper .nfc tonie dump into teddyCloud and cache its TAF.

Does what the NfcImport web plugin does, from the command line:
  1. parse UID and auth token (Data Content) from the dump,
  2. write the content JSON <FOLDER>/<FILE>.json with cloud_ruid/cloud_auth,
  3. have teddyCloud download the TAF from Boxine, which it caches (and, with
     cloud.cacheToLibrary, moves into the library and links via "source").

teddyCloud is reached through `kubectl port-forward` to its service, not
through the Gateway: the management API has no auth of its own and the
Gateway's OIDC would get in the way. teddyCloud needs cloud.enabled and
cloud.cacheContent switched on, otherwise nothing is cached.

Safe to re-run: a tonie whose JSON already has a "source" is skipped, and an
existing JSON with a cloud_auth is reused rather than overwritten.

Usage:
  import_nfc.py "German/100% Wolf/100% Wolf.nfc"
  import_nfc.py --dry-run some.nfc     # parse and report, write nothing
"""

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_CONTEXT = "home-zimmermann.lat"
DEFAULT_NAMESPACE = "argocd"
DEFAULT_SERVICE = "svc/teddycloud-service"


class ImportFailed(Exception):
    pass


def parse_dump(path):
    values = {}
    with open(path, encoding="utf-8-sig") as dump:
        for line in dump:
            key, sep, value = line.partition(":")
            if sep and key.strip() in ("UID", "Data Content"):
                values[key.strip()] = value.strip()

    uid = values.get("UID")
    auth = values.get("Data Content")
    if not uid or not auth:
        raise ImportFailed("the dump must contain UID and Data Content fields")

    uid_bytes = uid.split()
    if len(uid_bytes) != 8 or any(len(b) != 2 for b in uid_bytes):
        raise ImportFailed("UID must be exactly 8 hexadecimal bytes")
    cloud_auth = "".join(auth.split()).upper()
    try:
        bytes.fromhex("".join(uid_bytes))
        bytes.fromhex(cloud_auth)
    except ValueError:
        raise ImportFailed("UID and Data Content must be hexadecimal")
    if len(cloud_auth) != 64:
        raise ImportFailed("Data Content must be exactly 32 bytes")

    # teddyCloud names content after the reversed UID ("rUID").
    ruid = "".join(reversed(uid_bytes)).upper()
    return {"ruid": ruid, "auth": cloud_auth, "folder": ruid[:8], "file": ruid[8:]}


def free_local_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PortForward:
    def __init__(self, context, namespace, service):
        self.port = free_local_port()
        self.cmd = [
            "kubectl", "--context", context, "-n", namespace,
            "port-forward", service, f"{self.port}:80",
        ]
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            self.cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise ImportFailed(f"kubectl port-forward failed: {self.proc.stderr.read().strip()}")
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                return f"http://127.0.0.1:{self.port}"
            except OSError:
                time.sleep(0.3)
        raise ImportFailed("kubectl port-forward did not come up within 20 s")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=5)


class TeddyCloud:
    def __init__(self, base_url):
        self.base = base_url

    def request(self, method, path, body=None, headers=None, timeout=30):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as err:
            raise ImportFailed(f"{method} {path} failed: HTTP {err.code} {err.reason}")

    def get_json(self, path):
        with self.request("GET", path) as resp:
            return json.load(resp)

    def list_content(self, path):
        query = urllib.parse.urlencode({"special": "content", "path": path})
        return self.get_json(f"/api/fileIndex?{query}").get("files", [])

    def content_source(self, ruid):
        # Unknown tonie: 404 when its folder is missing, otherwise a 200 with
        # an empty "source". Both mean "not cached".
        try:
            return self.get_json(f"/content/json/get/{ruid.lower()}").get("source", "")
        except ImportFailed as err:
            if "HTTP 404" in str(err):
                return ""
            raise

    def find_json_entry(self, dump):
        folders = self.list_content("/")
        if not any(f["name"] == dump["folder"] and f["isDirectory"] for f in folders):
            return None
        for entry in self.list_content(f"/{dump['folder']}"):
            if entry["name"] == f"{dump['file']}.json" and not entry["isDirectory"]:
                return entry
        return None

    def ensure_folder(self, folder):
        for entry in self.list_content("/"):
            if entry["name"] == folder:
                if entry["isDirectory"]:
                    return
                raise ImportFailed(f"content path {folder} exists but is not a directory")
        self.request(
            "POST", "/api/dirCreate?special=content", body=f"/{folder}".encode(),
            headers={"Content-Type": "text/plain;charset=UTF-8"},
        ).close()

    def upload_json(self, dump):
        content = {
            "live": False, "nocloud": False, "source": "", "skip_seconds": 0,
            "cache": False, "cloud_ruid": dump["ruid"], "cloud_auth": dump["auth"],
            "cloud_override": False, "tonie_model": "", "hide": False,
            "claimed": False, "_version": 5,
        }
        name = f"{dump['file']}.json"
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{name}"\r\n'
            "Content-Type: application/json\r\n\r\n"
            f"{json.dumps(content, indent=4)}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        query = urllib.parse.urlencode({"special": "content", "path": dump["folder"]})
        self.request(
            "POST", f"/api/fileUpload?{query}", body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        ).close()

    def download_taf(self, dump, timeout):
        """Reads the whole TAF stream and discards it; teddyCloud caches it on the way."""
        path = f"/content/download/{dump['folder']}/{dump['file']}.json"
        received = 0
        with self.request("GET", path, timeout=timeout) as resp:
            while chunk := resp.read(1 << 20):
                received += len(chunk)
        return received


def import_dump(tc, dump, force, dry_run, timeout):
    source = tc.content_source(dump["ruid"])
    if source and not force:
        print(f"already cached: {source}")
        return

    entry = tc.find_json_entry(dump)
    if entry and not entry.get("has_cloud_auth") and not force:
        raise ImportFailed(
            f"{dump['folder']}/{dump['file']}.json exists without cloud_auth; "
            "re-run with --force to overwrite it"
        )
    write_json = entry is None or force

    if dry_run:
        print(f"dry run: would {'write' if write_json else 'reuse'} the JSON and download the TAF")
        return

    if write_json:
        tc.ensure_folder(dump["folder"])
        tc.upload_json(dump)
        print("content JSON written")
    else:
        print("reusing existing content JSON")

    print("downloading TAF from Boxine through teddyCloud ...")
    received = tc.download_taf(dump, timeout)
    if received == 0:
        raise ImportFailed("teddyCloud returned no TAF data; are cloud.enabled and cloud.cacheContent on?")

    # The move into the library finishes right after the stream ends.
    deadline = time.time() + 30
    while time.time() < deadline:
        source = tc.content_source(dump["ruid"])
        if source:
            print(f"cached {received / 1e6:.1f} MB: {source}")
            return
        time.sleep(1)
    print(f"downloaded {received / 1e6:.1f} MB, but the JSON has no source yet; "
          "check the teddyCloud log (with cloud.cacheToLibrary off the TAF stays next to the JSON)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("nfc_file", help="Flipper .nfc dump of the tonie")
    parser.add_argument("--context", default=DEFAULT_CONTEXT, help=f"kubectl context (default {DEFAULT_CONTEXT})")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help=f"namespace (default {DEFAULT_NAMESPACE})")
    parser.add_argument("--force", action="store_true", help="overwrite the JSON and download again even if cached")
    parser.add_argument("--dry-run", action="store_true", help="parse and check state, write nothing")
    parser.add_argument("--timeout", type=int, default=600, help="seconds allowed per network read (default 600)")
    args = parser.parse_args()

    try:
        dump = parse_dump(args.nfc_file)
        print(f"{args.nfc_file}\n  rUID {dump['ruid']} -> {dump['folder']}/{dump['file']}.json")
        with PortForward(args.context, args.namespace, DEFAULT_SERVICE) as base_url:
            import_dump(TeddyCloud(base_url), dump, args.force, args.dry_run, args.timeout)
    except (ImportFailed, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
