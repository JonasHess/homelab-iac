#!/usr/bin/env python3
"""Deploy Alexa Smart Home Skill via SMAPI and update Lambda function code.

Reads skill files from /config/ (mounted ConfigMap) and credentials from
environment variables. Idempotent - safe to re-run on every ArgoCD sync.

Environment variables:
  LWA_CLIENT_ID, LWA_CLIENT_SECRET, LWA_REFRESH_TOKEN  - Login with Amazon
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY              - AWS credentials
  SKILL_ID, LAMBDA_ARN, LAMBDA_REGION                   - deployment targets
"""

import io
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import zipfile

CONFIG_DIR = "/config"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_file(name):
    with open(os.path.join(CONFIG_DIR, name)) as f:
        return f.read()


def _json_request(url, data=None, method=None, headers=None):
    """Make an HTTP request and return (status, headers, parsed JSON body)."""
    if headers is None:
        headers = {}
    body = None
    if data is not None:
        if isinstance(data, str):
            body = data.encode("utf-8")
        elif isinstance(data, bytes):
            body = data
        else:
            body = json.dumps(data).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req)
    except urllib.request.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        raise
    resp_body = resp.read().decode()
    return resp.status, dict(resp.headers), json.loads(resp_body) if resp_body else {}


# ---------------------------------------------------------------------------
# 1. Refresh LWA access token
# ---------------------------------------------------------------------------

def get_access_token():
    print("1. Refreshing LWA access token...")
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": os.environ["LWA_REFRESH_TOKEN"],
        "client_id": os.environ["LWA_CLIENT_ID"],
        "client_secret": os.environ["LWA_CLIENT_SECRET"],
    }).encode()
    req = urllib.request.Request(
        "https://api.amazon.com/auth/o2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = urllib.request.urlopen(req)
    token = json.loads(resp.read())["access_token"]
    print("   Access token obtained.")
    return token


# ---------------------------------------------------------------------------
# 2. Update skill manifest
# ---------------------------------------------------------------------------

def update_manifest(token, skill_id):
    print("2. Updating skill manifest...")
    # GET current manifest to obtain ETag
    url = f"https://api.amazonalexa.com/v1/skills/{skill_id}/stages/development/manifest"
    status, resp_headers, _ = _json_request(url, method="GET", headers={
        "Authorization": f"Bearer {token}",
    })
    etag = resp_headers.get("ETag") or resp_headers.get("etag", "")
    print(f"   Current manifest ETag: {etag}")

    # PUT updated manifest
    manifest = json.loads(_read_file("skill.json"))
    _json_request(url, data=manifest, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "If-Match": etag,
    })
    print("   Manifest updated.")


# ---------------------------------------------------------------------------
# 3. Update account linking
# ---------------------------------------------------------------------------

def update_account_linking(token, skill_id, retries=3, delay=5):
    print("3. Updating account linking...")
    url = f"https://api.amazonalexa.com/v1/skills/{skill_id}/stages/development/accountLinkingClient"
    account_linking = json.loads(_read_file("account-linking.json"))

    for attempt in range(1, retries + 1):
        # GET current to obtain ETag
        etag = ""
        try:
            status, resp_headers, current = _json_request(url, method="GET", headers={
                "Authorization": f"Bearer {token}",
            })
            etag = resp_headers.get("ETag") or resp_headers.get("etag", "")
        except urllib.request.HTTPError:
            pass

        # PUT account linking
        headers = {"Authorization": f"Bearer {token}"}
        if etag:
            headers["If-Match"] = etag
        try:
            _json_request(url, data=account_linking, method="PUT", headers=headers)
            print("   Account linking updated.")
            return
        except urllib.request.HTTPError:
            if attempt < retries:
                print(f"   Retry {attempt}/{retries} - waiting {delay}s for skill version to settle...")
                time.sleep(delay)
            else:
                raise


# ---------------------------------------------------------------------------
# 4. Update Lambda function code
# ---------------------------------------------------------------------------

def update_lambda_code():
    print("4. Updating Lambda function code...")
    import boto3

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lambda_function.py", _read_file("lambda_function.py"))
    buf.seek(0)

    client = boto3.client(
        "lambda",
        region_name=os.environ["LAMBDA_REGION"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )
    client.update_function_code(
        FunctionName=os.environ["LAMBDA_ARN"],
        ZipFile=buf.read(),
    )
    print("   Lambda code updated.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Alexa Smart Home Skill Deployment")
    print("=" * 60)

    skill_id = os.environ["SKILL_ID"]
    print(f"Skill ID:      {skill_id}")
    print(f"Lambda ARN:    {os.environ['LAMBDA_ARN']}")
    print(f"Lambda Region: {os.environ['LAMBDA_REGION']}")
    print()

    token = get_access_token()
    update_manifest(token, skill_id)
    update_account_linking(token, skill_id)
    update_lambda_code()

    print()
    print("All done!")


if __name__ == "__main__":
    main()
