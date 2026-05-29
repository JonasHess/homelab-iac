# Beszel

Lightweight server monitoring. Hub (web UI + PocketBase) + per-node agent. **Fully self-bootstrapping** — no Akeyless paths to populate, no UI clicks per system. Push the chart, sync ArgoCD, done.

## Architecture

| Component | Resource | Image | Notes |
|---|---|---|---|
| Hub | Deployment | `henrygd/beszel` | PocketBase + SQLite at `/beszel_data`. Exposed via Envoy Gateway at `beszel.<domain>`, gated by Cognito OIDC. Auto-creates its admin user from env vars on first start. |
| Bootstrap | Sync-hook Job | `alpine:3.20` | Runs during sync. Auths against the hub, enables a permanent Universal Token, derives the hub's SSH pubkey from the shared PV, and writes both into a K8s Secret. Idempotent. |
| Agent | DaemonSet | `henrygd/beszel-agent` | One pod per node. `hostNetwork: true`, `hostPID: true`. Listens on `:45876`, dials hub on cluster DNS via `dnsPolicy: ClusterFirstWithHostNet`. `envFrom` pulls `KEY` + `TOKEN` from the bootstrap-written Secret. |

## How the bootstrap works

```
            ┌──────────────────────────────────────────────┐
            │  hub Deployment                              │
            │  env: USER_EMAIL, USER_PASSWORD, AUTO_LOGIN  │
            │  → first boot: creates admin user            │
            │  → generates its own SSH keypair             │
            └─────────────────┬────────────────────────────┘
                              │ mounts /beszel_data
                              ▼
            ┌──────────────────────────────────────────────┐
            │  beszel-bootstrap Job (Sync hook)            │
            │                                              │
            │  1. wait for hub /api/health                 │
            │  2. POST users/auth-with-password            │
            │  3. GET universal-token → read state         │
            │     • if active: reuse                       │
            │     • else: enable=1&permanent=1             │
            │  4. ssh-keygen -y -f /beszel_data/id_ed25519 │
            │  5. kubectl apply Secret beszel-agent-env    │
            │     keys: KEY (pubkey), TOKEN                │
            │  6. exit 0  (deleted by HookSucceeded)       │
            └─────────────────┬────────────────────────────┘
                              │ Secret beszel-agent-env
                              ▼
            ┌──────────────────────────────────────────────┐
            │  agent DaemonSet                             │
            │  envFrom: beszel-agent-env                   │
            │  → KEY validates hub signatures              │
            │  → TOKEN registers the system on first dial  │
            └──────────────────────────────────────────────┘
```

**Why the hub admin password is fine to ship in the chart**

The PocketBase admin user is only used internally by the bootstrap Job. External access to the hub is gated by Cognito OIDC at the Envoy Gateway, so the password value never matters for end-user auth. `AUTO_LOGIN` ensures any OIDC-authenticated visitor lands directly in the UI without seeing Beszel's own login form.

**Why we can't seed the Universal Token from outside**

Beszel generates the token in PocketBase server-side and exposes a REST endpoint to enable/read it. There's no env var to seed it. The bootstrap Job calls that endpoint after the hub is up. The `permanent=1` flag prevents the hub from auto-rotating it ([Beszel #1479](https://github.com/henrygd/beszel/issues/1479)).

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
- `agent.envSecret: beszel-agent-env` — name of the Secret the bootstrap Job writes.
- `agent.listen: "45876"` — agent listen port.
- `bootstrap.enabled: true` — turn off only if you're managing the Secret out of band.

## First-time setup

1. Set `apps.beszel.enabled: true` in the env values (see above).
2. Create the host directory matching `pvcMounts.data.hostPath`:
   ```bash
   ssh <user>@<node> 'sudo mkdir -p /mnt/<storage>/apps/beszel/data \
     && sudo chown 1000:1000 /mnt/<storage>/apps/beszel/data'
   ```
3. Commit + push. ArgoCD syncs.

No Akeyless paths to populate, no UI to visit, no clicks. Within ~30 s the agent registers itself.

## Adding another cluster

Same three steps. Each hub has its own PocketBase → its own admin user (auto-created), its own Universal Token (auto-enabled), its own agent Secret (auto-written). No values are shared across clusters.

## Operational notes

### What's on the PV

`/beszel_data/`:

- `id_ed25519` — hub's SSH private key (auto-generated on first boot).
- `data.db`, `data.db-shm`, `data.db-wal` — PocketBase SQLite (users, system entries, tokens, fingerprints, ~30 days of telemetry).
- `auxiliary.db` — secondary state.

Restic backs up the whole directory (`pvcMounts.data.backup.enabled: true`).

### Rotating the Universal Token

```bash
# Force the bootstrap Job to regenerate by deleting the Secret and re-syncing.
kubectl -n argocd delete secret beszel-agent-env
kubectl -n argocd patch application argocd-app-beszel \
  --type merge -p '{"operation":{"sync":{}}}'
```

The Job's idempotency check (Secret exists?) trips, it skips. Deleting the Secret forces it to re-enable + regenerate. Already-registered systems keep working — they switched to per-system fingerprints at registration time.

### Resetting everything

```bash
kubectl -n argocd scale deploy beszel-deployment --replicas=0
ssh <user>@<node> 'sudo find /mnt/<storage>/apps/beszel/data -mindepth 1 -delete'
kubectl -n argocd delete secret beszel-agent-env
kubectl -n argocd scale deploy beszel-deployment --replicas=1
kubectl -n argocd patch application argocd-app-beszel \
  --type merge -p '{"operation":{"sync":{}}}'
```

Hub regenerates keypair, recreates admin from env vars, Job re-enables Universal Token, Secret repopulated, agent re-registers as a fresh system. ~30 s end-to-end.

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Agent in `CreateContainerConfigError` with `secret "beszel-agent-env" not found` | Bootstrap Job hasn't completed yet, or failed | `kubectl -n argocd get pods` for `beszel-bootstrap-*`; check its logs |
| Agent `invalid signature - check KEY value` | Hub keypair was regenerated but Secret has stale KEY (e.g. PV was wiped without deleting the Secret) | `kubectl -n argocd delete secret beszel-agent-env` + re-sync |
| Agent `unexpected status code: 401` | Universal Token in Secret doesn't match hub's current token | Same as above — delete Secret, re-sync |
| Bootstrap Job logs `auth failed` | Hub `USER_EMAIL/USER_PASSWORD` env vars don't match data.db state (e.g. an old admin user pre-exists) | Reset everything (above) |
| Bootstrap Job never runs | ArgoCD's previous sync is still "Running" waiting for healthy state | Terminate the stuck operation, re-sync |
| No container stats on microk8s | Expected — Beszel reads Docker's API; containerd socket only provides host-level metrics | Host CPU/RAM/disk still work |
| Agent can't resolve `HUB_URL` | `dnsPolicy: ClusterFirstWithHostNet` missing on the DaemonSet, or in-cluster DNS broken | Inspect DS spec |

## Versions

Pinned via Renovate:

- Hub: `henrygd/beszel`
- Agent: `henrygd/beszel-agent`

Bump together — the hub's wire protocol and the agent's expectations move in lockstep.
