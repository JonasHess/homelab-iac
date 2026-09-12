# Watchdog heartbeat (dead man's switch)

## The problem this solves

kube-prometheus-stack ships an alert called `Watchdog` that fires permanently, on purpose.
It is not about anything being wrong. Its only job is to be delivered somewhere outside the
cluster, so that **the absence of it** can be alerted on.

Today `Watchdog` is routed to the `"null"` receiver in
`apps/prometheus/templates/prometheus-application.yaml`, which throws it away. The
consequence is that the monitoring stack fails silent: if Prometheus crashloops, Alertmanager
wedges, the node loses power or the internet drops, PagerDuty simply goes quiet. That is
indistinguishable from a healthy homelab with nothing firing.

Every other alert in this repository depends on the alerting pipeline working. This is the
only one that tells you when it does not.

## Manual steps

Everything in this section has to be done by a person. The repository change that consumes
the result is in the next section.

### 1. Create the check

healthchecks.io is the recommended destination. The free tier covers 20 checks, and it is a
different provider from PagerDuty, which matters: if the heartbeat lived in the same
PagerDuty account being monitored, a PagerDuty outage would take out both the alerting and
the thing watching the alerting.

1. Sign up at https://healthchecks.io
2. Click **Add Check**
3. Name it after the cluster, for example `zimmermann.lat Alertmanager Watchdog`
4. Set **Period** to `5 minutes` and **Grace Time** to `15 minutes`

   The period must be longer than how often Alertmanager pings (see `repeat_interval` below)
   and the grace time absorbs a missed ping or two, so a single blip does not wake you.
5. Save, and copy the ping URL from the check's page. It looks like
   `https://hc-ping.com/<uuid>`.

### 2. Make sure the check can actually reach you

This is the step that is easy to skip and makes the whole exercise pointless. A check with no
integration notifies nobody when it goes red.

Open **Settings > Integrations** on healthchecks.io and confirm at least one is enabled.
Email to your account address is on by default; a phone push integration is better if you
want to be woken up.

### 3. Store the URL in Akeyless

The ping URL is a capability URL: anyone holding it can keep the check green, which would
mask a real outage. It does not belong in Git.

```bash
akeyless create-secret \
  --name "/<your-akeyless-path>/o11y/alertmanager/watchdog_heartbeat_url" \
  --value "https://hc-ping.com/<uuid>"
```

`<your-akeyless-path>` is `global.akeyless.path` for the environment, so for
`zimmermann.lat` the full name is
`/zimmermann.lat/o11y/alertmanager/watchdog_heartbeat_url`. This mirrors where the PagerDuty
routing key already lives (`/<path>/o11y/alertmanager/pagerduty_service_key`).

Each cluster needs **its own check and its own URL**. Two clusters sharing one heartbeat means
either one alone keeps it green, so the other could be dead for weeks without anyone noticing.

### 4. Record it

Add the new path to the environment's secret inventory, for example
`homelab_environments/<env>/akeyless-secrets.md`, and to `create-akeyless-secrets.sh` if that
environment uses it. A secret created by hand and documented nowhere is how the PagerDuty key
ended up undocumented.

## The repository change

Once the secret exists, three edits wire it up. Nothing here works before step 3 above.

### `apps/prometheus/values.yaml`

```yaml
generic:
  externalSecrets:
    watchdog-heartbeat:
      - watchdog_heartbeat_url: "/o11y/alertmanager/watchdog_heartbeat_url"
```

The path is relative; `apps/generic/templates/external-secrets.yaml` prefixes it with
`global.akeyless.path`.

### `apps/prometheus/templates/prometheus-application.yaml`

Mount the secret next to the PagerDuty one:

```yaml
        alertmanager:
          alertmanagerSpec:
            secrets:
              - pagerduty-secret
              - watchdog-heartbeat
```

Add the receiver:

```yaml
            receivers:
              - name: 'watchdog-heartbeat'
                webhook_configs:
                  - url_file: "/etc/alertmanager/secrets/watchdog-heartbeat/watchdog_heartbeat_url"
                    send_resolved: false
```

And point the existing `Watchdog` route at it instead of `"null"`:

```yaml
                - matchers:
                    - alertname = "Watchdog"
                  receiver: 'watchdog-heartbeat'
                  group_wait: 0s
                  group_interval: 1m
                  repeat_interval: 1m
```

Three details that are not obvious:

- **The two intervals set the ping frequency, and you get about half the rate you ask for.**
  Alertmanager re-notifies only on a group flush tick (`group_interval`), and only once
  `repeat_interval` has fully elapsed since the last notification. With both set to the same
  value, the first tick falls a hair short and it waits for the next one, so `5m`/`5m` pings
  every 10 minutes, not 5. Measured on this cluster before the values were lowered.
  Pick a rate far below the far end's alert threshold (period + grace), so that a single
  dropped ping cannot raise a false alarm. A dead man's switch that cries wolf is worse than
  none, because it trains you to ignore it.
- **`send_resolved: false`** because a resolved Watchdog is precisely the silence the far end
  is watching for. Sending it would be pointless, and on some receivers actively confusing.
- **`url_file` rather than `url`** keeps the capability URL out of Git. It needs Alertmanager
  0.26 or newer, which the pinned chart satisfies.

### Keep it optional for other environments

This repository is shared between clusters. Guard the receiver and the route so an
environment without a heartbeat URL renders neither, using the `dig` pattern already used
elsewhere for optional globals, rather than making every consumer create a check.

## Verifying it

1. After the sync, the check on healthchecks.io should turn green within about 5 minutes.
2. Confirm Alertmanager is not failing the send:

   ```bash
   kubectl --context <ctx> -n argocd exec alertmanager-kube-prometheus-stack-alertmanager-0 \
     -c alertmanager -- wget -qO- http://localhost:9093/metrics \
     | grep 'alertmanager_notifications_failed_total{integration="webhook"'
   ```

   It should stay at 0. A climbing `serverError` here means the URL is wrong.

3. **Prove the switch actually works.** A heartbeat nobody has tested is not a safety net:

   ```bash
   kubectl --context <ctx> -n argocd scale statefulset \
     alertmanager-kube-prometheus-stack-alertmanager --replicas=0
   ```

   Within the grace time the check goes red and notifies you. Scale back to 1 afterwards.
   Do this deliberately, while watching, because alerting is down for the duration.

## Alternative destination

A second PagerDuty service with a heartbeat-style integration also works and keeps everything
in one place. The tradeoff is the one named at the top: it shares a failure domain with the
system it is supposed to watch. Prefer it only if having a single pane of glass matters more
than independence.
