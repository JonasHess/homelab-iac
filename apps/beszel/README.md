# Beszel

Lightweight per-host monitoring (CPU, RAM, disk, network, optional containers).

Self-bootstrapping: push the chart, no Akeyless paths, no UI clicks. A Sync-hook Job auths against the hub, enables a permanent Universal Token, derives the hub's SSH pubkey from its PV, and writes both into a Secret the agent consumes via `envFrom`.

## Enable for an env

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
            containerSocket: /var/snap/microk8s/common/run/containerd.sock  # microk8s
            # containerSocket: /var/run/docker.sock                          # Docker
            # containerSocket: ~                                             # host metrics only
```

Then on the node:

```bash
ssh <user>@<node> 'sudo mkdir -p /mnt/<storage>/apps/beszel/data \
  && sudo chown 1000:1000 /mnt/<storage>/apps/beszel/data'
```

Push, ArgoCD syncs, agent self-registers within ~30 s. Reachable at `https://beszel.<domain>` behind Cognito OIDC.

## Components

| | Resource | Image | Role |
|---|---|---|---|
| Hub | Deployment | `henrygd/beszel` | Web UI + PocketBase at `/beszel_data`. Auto-creates admin from `USER_EMAIL`/`USER_PASSWORD`/`AUTO_LOGIN` env. |
| Bootstrap | Job (`hook: Sync`) | `alpine:3.20` | Reads/enables Universal Token + writes Secret `beszel-agent-env`. Idempotent. |
| Agent | DaemonSet | `henrygd/beszel-agent` | `hostNetwork`+`hostPID`. `envFrom` Secret. Connects via `dnsPolicy: ClusterFirstWithHostNet` to `http://beszel-service.argocd.svc.cluster.local:8090`. |

The hub's admin password is hardcoded in the chart because external access is OIDC-gated and `AUTO_LOGIN` skips Beszel's login screen — the password is only ever used by the bootstrap Job inside the cluster.

## Reset

```bash
kubectl -n argocd scale deploy beszel-deployment --replicas=0
ssh <user>@<node> 'sudo find /mnt/<storage>/apps/beszel/data -mindepth 1 -delete'
kubectl -n argocd delete secret beszel-agent-env --ignore-not-found
kubectl -n argocd scale deploy beszel-deployment --replicas=1
kubectl -n argocd patch application argocd-app-beszel \
  --type merge -p '{"operation":{"sync":{}}}'
```

Fresh PocketBase, fresh keypair, Job repopulates the Secret, agent re-registers.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Agent `CreateContainerConfigError: secret "beszel-agent-env" not found` | Bootstrap Job hasn't run yet or failed — `kubectl -n argocd logs job/beszel-bootstrap` (or check pods if HookSucceeded already deleted it) |
| Agent `invalid signature - check KEY value` | Hub keypair was regenerated but Secret has the old pubkey — `kubectl -n argocd delete secret beszel-agent-env`, then re-sync |
| Agent `unexpected status code: 401` | Universal Token in Secret no longer matches the hub's — same fix as above |
| Bootstrap Job `auth failed` | data.db has a pre-existing admin that doesn't match the chart's `USER_EMAIL`/`USER_PASSWORD` — wipe per the Reset section |
| No container stats on microk8s | Expected — Beszel reads Docker's API. Host CPU/RAM/disk still work. |

## Versions

Pinned via Renovate; bump hub and agent together.
