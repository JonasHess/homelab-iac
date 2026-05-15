#!/usr/bin/env bash
#
# setup-collections.sh — create Radicale collections (addressbooks / calendars)
# at explicit URL slugs via WebDAV MKCOL.
#
# Why this exists:
#   Most DAV clients (DAVx⁵, iOS, etc.) assign a random UUID as the URL slug
#   when they create a new collection. If your rights file references specific
#   slugs (e.g. user1/shared-contacts), those auto-generated paths won't match.
#   This script lets you create collections at the slugs you want.
#
# Re-running is safe: existing collections return HTTP 405 and are skipped.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  setup-collections.sh --server URL --user USERNAME --collections LIST [--password PW]

Required:
  --server URL          Radicale base URL, e.g. https://dav.example.com
  --user USERNAME       Username for Basic Auth
  --collections LIST    Comma-separated list of slug:type pairs.
                        Types: addressbook | calendar
                        Example: contacts:addressbook,calendar:calendar

Optional:
  --password PW         Password. If omitted, read from $RADICALE_PASSWORD
                        or prompted interactively (preferred — keeps it out
                        of shell history).

Examples:
  # User1 — private + shared collections
  setup-collections.sh --server https://dav.example.com --user user1 \
    --collections contacts:addressbook,calendar:calendar,shared-contacts:addressbook,shared-calendar:calendar

  # User2 — private collections only
  setup-collections.sh --server https://dav.example.com --user user2 \
    --collections contacts:addressbook,calendar:calendar
EOF
    exit 1
}

SERVER=""
USERNAME=""
PASSWORD="${RADICALE_PASSWORD:-}"
COLLECTIONS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --server)      SERVER="$2";      shift 2 ;;
        --user)        USERNAME="$2";    shift 2 ;;
        --password)    PASSWORD="$2";    shift 2 ;;
        --collections) COLLECTIONS="$2"; shift 2 ;;
        -h|--help)     usage ;;
        *) echo "Unknown argument: $1" >&2; usage ;;
    esac
done

[[ -z "$SERVER"      ]] && { echo "--server is required"      >&2; usage; }
[[ -z "$USERNAME"    ]] && { echo "--user is required"        >&2; usage; }
[[ -z "$COLLECTIONS" ]] && { echo "--collections is required" >&2; usage; }

if [[ -z "$PASSWORD" ]]; then
    read -r -s -p "Password for ${USERNAME}: " PASSWORD
    echo
fi

SERVER="${SERVER%/}"
TMP_OUT="$(mktemp)"
trap 'rm -f "$TMP_OUT"' EXIT

mkcol_body() {
    local kind="$1"      # addressbook | calendar
    local displayname="$2"
    case "$kind" in
        addressbook)
            cat <<EOF
<?xml version="1.0" encoding="utf-8" ?>
<mkcol xmlns="DAV:" xmlns:CR="urn:ietf:params:xml:ns:carddav">
  <set>
    <prop>
      <resourcetype>
        <collection/>
        <CR:addressbook/>
      </resourcetype>
      <displayname>${displayname}</displayname>
    </prop>
  </set>
</mkcol>
EOF
            ;;
        calendar)
            cat <<EOF
<?xml version="1.0" encoding="utf-8" ?>
<mkcol xmlns="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <set>
    <prop>
      <resourcetype>
        <collection/>
        <C:calendar/>
      </resourcetype>
      <displayname>${displayname}</displayname>
    </prop>
  </set>
</mkcol>
EOF
            ;;
        *)
            echo "ERROR: unknown collection type '$kind' (use addressbook or calendar)" >&2
            exit 1
            ;;
    esac
}

create_collection() {
    local slug="$1" kind="$2"
    local url="${SERVER}/${USERNAME}/${slug}/"
    local body status
    body="$(mkcol_body "$kind" "$slug")"

    echo "→ ${kind} at ${url}"
    status="$(curl -sS -o "$TMP_OUT" -w "%{http_code}" \
        -X MKCOL \
        --user "${USERNAME}:${PASSWORD}" \
        -H "Content-Type: application/xml; charset=utf-8" \
        --data "$body" \
        "$url")"

    case "$status" in
        201) echo "  created" ;;
        405) echo "  already exists — skipping" ;;
        401) echo "  auth failed (401) — wrong username or password" >&2; exit 1 ;;
        403) echo "  forbidden (403) — check the rights file allows this user to create at this path" >&2; exit 1 ;;
        *)   echo "  failed (HTTP $status):" >&2; cat "$TMP_OUT" >&2; exit 1 ;;
    esac
}

IFS=',' read -ra ITEMS <<< "$COLLECTIONS"
for item in "${ITEMS[@]}"; do
    if [[ "$item" != *:* ]]; then
        echo "ERROR: malformed collection spec '$item' — expected slug:type" >&2
        exit 1
    fi
    slug="${item%%:*}"
    kind="${item##*:}"
    if [[ -z "$slug" || -z "$kind" ]]; then
        echo "ERROR: malformed collection spec '$item' — slug or type is empty" >&2
        exit 1
    fi
    create_collection "$slug" "$kind"
done

echo
echo "Done. Collections for '${USERNAME}' are ready."
