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
            # Docker or Podman only. A containerd node (k3s, microk8s) has
            # no socket that works here, so leave it unset there.
            containerSocket: ~
            # containerSocket: /var/run/docker.sock
            smart:
              # Base controllers, never namespaces or partitions.
              devices:
              - /dev/nvme0
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
| Agent | DaemonSet | `henrygd/beszel-agent:*-alpine` | `hostNetwork`+`hostPID`. `envFrom` Secret. Connects via `dnsPolicy: ClusterFirstWithHostNet` to `http://beszel-service.argocd.svc.cluster.local:8090`. Keeps its fingerprint on a node-local `hostPath` (`agent.dataHostPath`). |

The agent runs the `-alpine` image rather than the default one because that is the only variant shipping `smartctl`. Listing a drive under `agent.smart.devices` passes it into the container and grants `SYS_RAWIO`/`SYS_ADMIN`, which raw pass-through commands to the drive require.

Container metrics work only against a Docker or Podman socket. A node whose only runtime is containerd (k3s, microk8s) cannot supply them at all, since containerd speaks CRI over gRPC and the agent speaks the Docker HTTP API.

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
| Agent `connection closed, code=1000, reason=fingerprint mismatch` | The agent presents a fingerprint the hub has on file for a different one. Delete the system's fingerprint under Settings > Tokens & Fingerprints in the hub, and make sure `agent.dataHostPath` is set so the next one sticks. |
| No container stats on microk8s | Expected, containerd cannot serve them. Host CPU/RAM/disk/SMART still work. |
| SMART panel empty | Agent image is not an `-alpine` variant (no `smartctl`), or the drive is missing from `agent.smart.devices`, or a namespace/partition was listed instead of the controller. |

## Versions

Pinned via Renovate; bump hub and agent together.
