# Beszel

Lightweight server monitoring. Hub (web UI + PocketBase) + per-node agent. Agents self-register over WebSocket using a Universal Token — no per-host UI clicks ever — and use a pre-provisioned SSH keypair for message signing.

## Architecture

| Component | Resource | Image | Notes |
|---|---|---|---|
| Hub | Deployment | `henrygd/beszel` | PocketBase + SQLite at `/beszel_data`. Exposed via Envoy Gateway at `beszel.<domain>`, gated by Cognito OIDC. Init container restores the SSH private key from Akeyless on first boot. |
| Agent | DaemonSet | `henrygd/beszel-agent` | One pod per node. `hostNetwork: true`, `hostPID: true`. Listens on `:45876`, dials hub on cluster DNS via `dnsPolicy: ClusterFirstWithHostNet`. `envFrom` pulls `KEY` + `TOKEN` from the same Secret. |

## Why both SSH key *and* Universal Token

Beszel uses the SSH keypair for **message signing** in both transports — even when the agent connects via WebSocket, every message from the hub is signed with the hub's SSH private key, and the agent verifies it with `KEY`. So the agent always needs the hub's *public* key, and any placeholder will fail with `invalid signature - check KEY value`.

The Universal Token is purely about **registration**: it lets the agent register a new System entry in the hub's PocketBase without anyone clicking "+ Add System" in the UI.

Together: pre-provisioned keypair = signed comms work, Universal Token = registration is automatic.

## Per-environment configuration

In `homelab-environments/<env>/values.yaml`:

```yaml
apps:
  beszel:
    enabled: true
    argocd:
      targetRevision: ~
      helm:
        values:
          generic:
            deployment:
              pvcMounts:
                data:
                  hostPath: /mnt/<storage>/apps/beszel/data
          agent:
            enabled: true
            # Container runtime socket. Optional — host CPU/RAM/disk work
            # regardless; this only enables container-level stats.
            containerSocket: /var/snap/microk8s/common/run/containerd.sock  # microk8s
            # containerSocket: /var/run/docker.sock                          # Docker
            # containerSocket: ~                                             # host-only
```

Defaults that you usually don't override:

- `agent.hubUrl: http://beszel-service.argocd.svc.cluster.local:8090` — in-cluster Service URL.
- `agent.envSecret: beszel-agent-env` — name of the K8s Secret produced by ESO (carries both `KEY` and `TOKEN`).
- `agent.listen: "45876"` — agent listen port.

## Akeyless paths

Three values per env, under `<global.akeyless.path>/beszel/`:

| Path | Used by | How it's produced |
|---|---|---|
| `HUB_SSH_PRIVATE_KEY` | Hub init container → `/beszel_data/id_ed25519` | `ssh-keygen -t ed25519 -N "" -f beszel_hub` (local), upload the private side |
| `HUB_SSH_PUBLIC_KEY` | Agent (`KEY` env) | Public side of the same keypair, single line `ssh-ed25519 AAAA…` |
| `UNIVERSAL_TOKEN` | Agent (`TOKEN` env) | Generated server-side by the hub UI at `/settings/tokens` after first boot |

## First-time setup

Per cluster. ~5 minutes once, then GitOps forever.

### 1. Generate the SSH keypair locally

```bash
ssh-keygen -t ed25519 -N "" -C "beszel-hub-<env>" -f /tmp/beszel_hub
cat /tmp/beszel_hub      # private — copy into Akeyless HUB_SSH_PRIVATE_KEY
cat /tmp/beszel_hub.pub  # public — copy into Akeyless HUB_SSH_PUBLIC_KEY
rm -P /tmp/beszel_hub /tmp/beszel_hub.pub
```

### 2. Put them in Akeyless

- `<global.akeyless.path>/beszel/HUB_SSH_PRIVATE_KEY`
- `<global.akeyless.path>/beszel/HUB_SSH_PUBLIC_KEY`

### 3. Create the host directory

```bash
ssh <user>@<node> 'sudo mkdir -p /mnt/<storage>/apps/beszel/data \
  && sudo chown 1000:1000 /mnt/<storage>/apps/beszel/data'
```

Path must match `pvcMounts.data.hostPath`.

### 4. Let ArgoCD sync

- Hub init container copies the private key into `/beszel_data/id_ed25519`.
- Hub starts and uses *that* key (no auto-generation).
- Agent DaemonSet starts but will be in `CrashLoopBackoff` because `UNIVERSAL_TOKEN` isn't set yet — expected.

### 5. Generate the Universal Token

1. Open `https://beszel.<domain>`.
2. Create the admin account on first visit.
3. **Settings → Tokens → Enable Universal Token** → copy the value.

### 6. Put the token in Akeyless

`<global.akeyless.path>/beszel/UNIVERSAL_TOKEN`

Force ESO refresh:

```bash
kubectl -n argocd annotate externalsecret beszel-agent-env-external-secret \
  force-sync=$(date +%s) --overwrite
```

Reloader restarts the agent pods. Agent dials the hub via cluster DNS with `TOKEN` + `KEY`, registers a System entry, and starts reporting metrics.

## Adding another cluster

1. Repeat steps 1–6 for the new env (each hub has its own PocketBase → its own Universal Token, so you can't share that one across clusters).
2. The SSH keypair *can* be shared across clusters if you want, or you can generate a fresh one per hub — it's a per-env decision.

Per-agent setup within a cluster: zero. Just `agent.enabled: true` for any new node (or change the DaemonSet's node selector) — Universal Token handles registration.

## Operational notes

### What's on the PV

`/beszel_data/`:

- `id_ed25519` — hub's SSH private key (bootstrapped from Akeyless by the init container, never regenerated).
- `data.db`, `data.db-shm`, `data.db-wal` — PocketBase SQLite (users, system entries, tokens, fingerprints, ~30 days of telemetry).
- `auxiliary.db` — secondary state.

Restic backs up the whole directory (`pvcMounts.data.backup.enabled: true`).

### Rotating the Universal Token

1. Hub UI → **Settings → Tokens → Regenerate**.
2. Overwrite Akeyless `/beszel/UNIVERSAL_TOKEN` with the new value.
3. Force ExternalSecret refresh; reloader restarts agents.

Already-registered systems keep working — their per-system fingerprints in PocketBase don't depend on the universal token after first registration.

### Rotating the SSH keypair

1. Generate a new keypair locally.
2. Overwrite both `HUB_SSH_PRIVATE_KEY` and `HUB_SSH_PUBLIC_KEY` in Akeyless.
3. SSH into the node and `sudo rm /mnt/<storage>/apps/beszel/data/id_ed25519` so the init container will write the new one on next pod start.
4. `kubectl -n argocd rollout restart deploy/beszel-deployment ds/beszel-agent`.

### Resetting everything

```bash
kubectl -n argocd scale deploy beszel-deployment --replicas=0

ssh <user>@<node> 'sudo rm -rf /mnt/<storage>/apps/beszel/data \
  && sudo mkdir -p /mnt/<storage>/apps/beszel/data \
  && sudo chown 1000:1000 /mnt/<storage>/apps/beszel/data'

kubectl -n argocd scale deploy beszel-deployment --replicas=1
```

Then redo "First-time setup" from step 5 (steps 1–4 are persistent in Akeyless).

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Agent in `CrashLoopBackoff`, "must set KEY env var" | `beszel-agent-env` Secret missing — check ESO status and Akeyless paths. |
| Agent logs `invalid signature - check KEY value` | Akeyless `HUB_SSH_PUBLIC_KEY` doesn't match what's actually at `/beszel_data/id_ed25519`. Re-derive: `ssh-keygen -y -f /mnt/<storage>/apps/beszel/data/id_ed25519`. |
| Agent logs `unexpected status code: 401` | `TOKEN` doesn't match the hub's Universal Token. Verify, force-sync ESO, restart agent pods. |
| Hub shows "Keine Systeme gefunden" with agent connected | Token mismatch (same as above) — agent connects but hub refuses to register the system. |
| Hub init container loops | PV not writable (check `chown 1000:1000`) or Akeyless secret missing. |
| Agent can't resolve `HUB_URL` | `dnsPolicy: ClusterFirstWithHostNet` missing on the DaemonSet, or in-cluster DNS broken. |
| No container stats on microk8s | Expected — Beszel reads Docker's API; containerd socket only provides host-level metrics. Host CPU/RAM/disk still work. |

## Versions

Pinned via Renovate:

- Hub: `henrygd/beszel`
- Agent: `henrygd/beszel-agent`

Bump together — the hub's wire protocol and the agent's expectations move in lockstep.
