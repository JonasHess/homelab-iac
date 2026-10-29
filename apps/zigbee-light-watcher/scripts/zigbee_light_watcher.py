#!/usr/bin/env python3
"""Zigbee light watcher.

Polls the real on/off state of every Zigbee2MQTT light and compares it with the
state the light was last commanded to. A difference ("ghost light") is exported
as a Prometheus metric and logged as a JSON event.

Why polling: the lights neither report their state nor acknowledge group
commands. After a group command Zigbee2MQTT publishes every member's state
optimistically, so Home Assistant shows what was sent, not what happened. Only an
explicit read (`<base>/<light>/get`) returns the bulb's real state.

Message classification (Zigbee2MQTT publishes a read answer, an optimistic group
copy and a direct command on the same topic):
  * group command: a group publish accompanied, within ADJACENT_S, by optimistic
    copies of its members (at least two copies, or one copy without a pending read
    for single-member groups). A group publish without those copies is a state
    Zigbee2MQTT derived from a member's read answer and is ignored.
  * optimistic copy: a light publish within ADJACENT_S of a group command it is a
    member of. Ignored; the group command already set the commanded state.
  * read answer: a light publish while a read of that light is pending.
  * direct command: any other light publish (e.g. Home Assistant controlling the
    single bulb).
Messages are therefore classified only ADJACENT_S after arrival, once their
neighbours are known. A real group command that coincides with a member's read
answer can be misread as derived; the next command or read corrects the state.

Only stdlib at import time so the tracker can be tested without paho-mqtt or
prometheus_client.
"""

import json
import logging
import os
import queue
import re
import sys
import time

ADJACENT_S = 0.3


class BulbState:
    def __init__(self, name):
        self.name = name
        self.commanded = None
        self.commanded_at = None
        self.command_source = None
        self.command_seq = 0
        self.verified_seq = None
        self.actual = None
        self.mismatch_since = None
        self.mismatch_kind = None
        self.pending_read_since = None
        self.read_snapshot = None


class Tracker:
    """Pure state machine. All times are epoch seconds passed in by the caller.

    `sink` receives metric updates and events; see MetricsSink for the contract.
    Single-threaded: call every method from the same thread.
    """

    # read_timeout_s must exceed Zigbee2MQTT's own request timeout (15 s on the Ember
    # adapter): an answer arriving after it would be taken for a direct command and could
    # hide a ghost light.
    def __init__(self, sink, settle_s=3.0, read_timeout_s=30.0, adjacent_s=ADJACENT_S):
        self.sink = sink
        self.settle_s = settle_s
        self.read_timeout_s = read_timeout_s
        self.adjacent_s = adjacent_s
        self.bulbs = {}
        self.groups = {}
        self.bulb_groups = {}
        self._raw_groups = {}
        self._items = []

    # --- topology -----------------------------------------------------------

    def set_lights(self, names):
        """Replaces the watched set. A renamed light is a removal plus an addition, so its
        history (commanded state, metric series) restarts under the new name."""
        names = set(names)
        for name in [n for n in self.bulbs if n not in names]:
            del self.bulbs[name]
            self.sink.light_removed(name)
        for name in names:
            if name not in self.bulbs:
                self.bulbs[name] = BulbState(name)
        self.set_groups(self._raw_groups)

    def set_groups(self, groups):
        """groups: {group_name: [member light names]}; non-light members are dropped."""
        self._raw_groups = groups
        self.groups = {g: [m for m in members if m in self.bulbs] for g, members in groups.items()}
        self.groups = {g: m for g, m in self.groups.items() if m}
        self.bulb_groups = {}
        for g, members in self.groups.items():
            for m in members:
                self.bulb_groups.setdefault(m, []).append(g)
        self._publish_info()

    def _publish_info(self):
        for name in self.bulbs:
            self.sink.light_info(name, ",".join(sorted(self.bulb_groups.get(name, []))))

    # --- input --------------------------------------------------------------

    def on_message(self, name, payload, now):
        """A publish on `<base>/<name>`. Classified later by process()."""
        if not isinstance(payload, dict) or payload.get("state") not in ("ON", "OFF"):
            return
        if name in self.bulbs:
            bulb = self.bulbs[name]
            pending = bulb.pending_read_since is not None
            self._items.append({"ts": now, "kind": "bulb", "name": name, "payload": payload,
                                "pending": pending, "done": False})
        elif name in self.groups:
            self._items.append({"ts": now, "kind": "group", "name": name, "payload": payload,
                                "pending": False, "done": False})

    def on_announce(self, name, now):
        """Returns True when the announced device is a watched light (caller should read it)."""
        self.sink.announce(name)
        self.sink.event("announce", device=name)
        return name in self.bulbs

    def begin_read(self, name, now):
        bulb = self.bulbs[name]
        bulb.pending_read_since = now
        bulb.read_snapshot = (bulb.command_seq, bulb.commanded_at)

    # --- periodic -----------------------------------------------------------

    def process(self, now):
        """Classify buffered messages whose neighbourhood is complete; expire reads."""
        self._items.sort(key=lambda i: i["ts"])
        for item in self._items:
            if not item["done"] and now >= item["ts"] + self.adjacent_s:
                if item["kind"] == "group":
                    self._process_group(item)
                else:
                    self._process_bulb(item)
                item["done"] = True
        # Done items stay a little longer: later items still look them up as neighbours.
        horizon = now - 3 * self.adjacent_s
        self._items = [i for i in self._items if not i["done"] or i["ts"] >= horizon]
        self._expire_reads(now)

    def _expire_reads(self, now):
        for bulb in self.bulbs.values():
            if bulb.pending_read_since is not None and now - bulb.pending_read_since > self.read_timeout_s:
                bulb.pending_read_since = None
                self.sink.read_failed(bulb.name)
                self.sink.event("read_timeout", bulb=bulb.name)

    # --- classification -----------------------------------------------------

    def _neighbours(self, item, kind):
        return [i for i in self._items
                if i is not item and i["kind"] == kind and abs(i["ts"] - item["ts"]) <= self.adjacent_s]

    def _is_group_command(self, group_item):
        members = self.groups.get(group_item["name"], [])
        copies = [i for i in self._neighbours(group_item, "bulb") if i["name"] in members]
        if len(members) >= 2:
            return len(copies) >= 2
        return len(copies) >= 1 and not any(c["pending"] for c in copies)

    def _process_group(self, item):
        if not self._is_group_command(item):
            return
        state = item["payload"]["state"] == "ON"
        self.sink.group_command(item["name"], item["payload"]["state"])
        self.sink.event("group_command", group=item["name"], state=item["payload"]["state"])
        for member in self.groups[item["name"]]:
            self._set_commanded(self.bulbs[member], state, item["ts"], "group:" + item["name"])

    def _process_bulb(self, item):
        name = item["name"]
        if name not in self.bulbs:
            return
        for g in self._neighbours(item, "group"):
            if g["name"] in self.bulb_groups.get(name, []) and self._is_group_command(g):
                return
        if item["pending"]:
            self._read_answer(self.bulbs[name], item["payload"], item["ts"])
        else:
            self._set_commanded(self.bulbs[name], item["payload"]["state"] == "ON", item["ts"], "direct")

    # --- state transitions --------------------------------------------------

    def _set_commanded(self, bulb, state, ts, source):
        bulb.command_seq += 1
        bulb.commanded = state
        bulb.commanded_at = ts
        bulb.command_source = source
        self.sink.commanded(bulb.name, state)
        if bulb.mismatch_since is not None:
            self._end_mismatch(bulb, ts, "command")

    def _read_answer(self, bulb, payload, ts):
        sent_at = bulb.pending_read_since
        bulb.pending_read_since = None
        actual = payload["state"] == "ON"
        bulb.actual = actual
        self.sink.read_ok(bulb.name, actual, payload.get("linkquality"), ts)

        if bulb.commanded is None:
            self._set_commanded(bulb, actual, ts, "baseline")
            bulb.verified_seq = bulb.command_seq
            return

        snapshot_seq, snapshot_at = bulb.read_snapshot or (None, None)
        if snapshot_seq != bulb.command_seq:
            return
        if sent_at is None or snapshot_at is None or sent_at - snapshot_at < self.settle_s:
            return

        if actual == bulb.commanded:
            bulb.verified_seq = bulb.command_seq
            if bulb.mismatch_since is not None:
                self._end_mismatch(bulb, ts, "read")
        elif bulb.mismatch_since is None:
            kind = "uncommanded_change" if bulb.verified_seq == bulb.command_seq else "missed_command"
            bulb.mismatch_since = ts
            bulb.mismatch_kind = kind
            self.sink.mismatch(bulb.name, True)
            if kind == "missed_command":
                self.sink.missed_command(bulb.name, bulb.command_source)
            else:
                self.sink.uncommanded_change(bulb.name)
            self.sink.event("mismatch_start", bulb=bulb.name, kind=kind,
                            actual="ON" if actual else "OFF",
                            commanded="ON" if bulb.commanded else "OFF",
                            command_source=bulb.command_source,
                            command_age_s=round(ts - bulb.commanded_at, 1),
                            linkquality=payload.get("linkquality"))

    def _end_mismatch(self, bulb, ts, reason):
        self.sink.mismatch(bulb.name, False)
        self.sink.event("mismatch_end", bulb=bulb.name, kind=bulb.mismatch_kind, reason=reason,
                        duration_s=round(ts - bulb.mismatch_since, 1))
        bulb.mismatch_since = None
        bulb.mismatch_kind = None


# --- Prometheus / MQTT adapters -------------------------------------------------

class MetricsSink:
    def __init__(self):
        from prometheus_client import Counter, Gauge
        self.actual_on = Gauge("zigbee_light_actual_on", "Real on/off state from the last read (1 = on)", ["bulb"])
        self.commanded_on = Gauge("zigbee_light_commanded_on", "State the light was last commanded to (1 = on)", ["bulb"])
        self.mismatch_g = Gauge("zigbee_light_mismatch", "1 while the real state differs from the commanded state", ["bulb"])
        self.reachable = Gauge("zigbee_light_reachable", "0 after a read timed out, 1 after a read answered", ["bulb"])
        self.linkquality = Gauge("zigbee_light_linkquality", "Linkquality of the last read answer", ["bulb"])
        self.last_read = Gauge("zigbee_light_last_read_timestamp_seconds", "Time of the last answered read", ["bulb"])
        self.info = Gauge("zigbee_light_info", "Static light info; groups is a comma-separated list", ["bulb", "groups"])
        self.reads = Counter("zigbee_light_reads", "Answered reads", ["bulb"])
        self.read_failures = Counter("zigbee_light_read_failures", "Reads without an answer", ["bulb"])
        self.missed = Counter("zigbee_light_missed_commands", "Commands the light did not follow", ["bulb", "source"])
        self.uncommanded = Counter("zigbee_light_uncommanded_changes",
                                   "Light changed state after following its last command, without a new command", ["bulb"])
        self.group_commands = Counter("zigbee_group_commands", "Group commands seen", ["group", "state"])
        self.announces = Counter("zigbee_device_announces", "device_announce events (power loss or rejoin)", ["device"])
        self._info_labels = {}

    def light_info(self, bulb, groups):
        old = self._info_labels.get(bulb)
        if old is not None and old != groups:
            self.info.remove(bulb, old)
        self._info_labels[bulb] = groups
        self.info.labels(bulb, groups).set(1)
        # Export 0 from the start so "no ghost" is a value, not a missing series.
        self.mismatch_g.labels(bulb)

    def light_removed(self, bulb):
        for metric in (self.actual_on, self.commanded_on, self.mismatch_g, self.reachable,
                       self.linkquality, self.last_read, self.info, self.reads,
                       self.read_failures, self.missed, self.uncommanded):
            label_sets = {tuple(s.labels[n] for n in metric._labelnames)
                          for family in metric.collect() for s in family.samples
                          if s.labels.get("bulb") == bulb}
            for values in label_sets:
                metric.remove(*values)
        self._info_labels.pop(bulb, None)

    def commanded(self, bulb, on):
        self.commanded_on.labels(bulb).set(1 if on else 0)

    def read_ok(self, bulb, on, linkquality, ts):
        self.actual_on.labels(bulb).set(1 if on else 0)
        self.reachable.labels(bulb).set(1)
        self.last_read.labels(bulb).set(ts)
        self.reads.labels(bulb).inc()
        if linkquality is not None:
            self.linkquality.labels(bulb).set(linkquality)

    def read_failed(self, bulb):
        self.reachable.labels(bulb).set(0)
        self.read_failures.labels(bulb).inc()

    def mismatch(self, bulb, flag):
        self.mismatch_g.labels(bulb).set(1 if flag else 0)

    def missed_command(self, bulb, source):
        self.missed.labels(bulb, source).inc()

    def uncommanded_change(self, bulb):
        self.uncommanded.labels(bulb).inc()

    def group_command(self, group, state):
        self.group_commands.labels(group, state).inc()

    def announce(self, device):
        self.announces.labels(device).inc()

    def event(self, event_type, **fields):
        log_event(event_type, **fields)


def log_event(event_type, **fields):
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event_type}
    record.update(fields)
    print(json.dumps(record, ensure_ascii=False), flush=True)


def lights_from_devices(devices, exclude):
    """Mains-powered (router) devices exposing a light, minus names matching `exclude`.

    Lights exposed per endpoint (multi-channel dimmers) publish `state_<endpoint>`
    instead of `state`; the tracker only understands `state`, so they are skipped
    and logged instead of showing up as permanently unreachable.
    """
    names = []
    for d in devices:
        definition = d.get("definition") or {}
        if d.get("type") != "Router":
            continue
        lights = [e for e in definition.get("exposes", []) if e.get("type") == "light"]
        if not lights:
            continue
        name = d.get("friendly_name")
        if any(e.get("endpoint") for e in lights):
            log_event("light_skipped", bulb=name, reason="per-endpoint state not supported")
            continue
        if exclude and re.search(exclude, name):
            continue
        names.append(name)
    return sorted(names)


def groups_from_bridge(groups, ieee_to_name):
    return {g["friendly_name"]: [ieee_to_name.get(m["ieee_address"]) for m in g.get("members", [])]
            for g in groups}


def main():
    import paho.mqtt.client as mqtt
    from prometheus_client import start_http_server

    host = os.environ.get("MQTT_HOST", "mosquitto-service.argocd.svc.cluster.local")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    base = os.environ.get("Z2M_BASE_TOPIC", "zigbee2mqtt")
    interval = float(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
    exclude = os.environ.get("EXCLUDE_REGEX", "")
    metrics_port = int(os.environ.get("METRICS_PORT", "9105"))

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    start_http_server(metrics_port)
    tracker = Tracker(MetricsSink())
    inbox = queue.Queue()

    def on_connect(client, userdata, flags, reason_code, properties):
        log_event("mqtt_connected", reason=str(reason_code))
        client.subscribe([(f"{base}/bridge/devices", 0), (f"{base}/bridge/groups", 0),
                          (f"{base}/bridge/event", 0), (f"{base}/+", 0)])

    def on_message(client, userdata, msg):
        inbox.put((msg.topic, msg.payload, time.time()))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="zigbee-light-watcher")
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(1, 60)
    client.connect_async(host, port, keepalive=30)
    client.loop_start()

    ieee_to_name = {}
    raw_groups = None
    order = []
    next_index = 0
    next_read_at = time.time() + 5

    def request_read(name, now):
        tracker.begin_read(name, now)
        client.publish(f"{base}/{name}/get", json.dumps({"state": ""}))

    while True:
        try:
            topic, payload, ts = inbox.get(timeout=0.1)
            try:
                data = json.loads(payload)
            except ValueError:
                data = None
            if topic == f"{base}/bridge/devices" and isinstance(data, list):
                ieee_to_name = {d["ieee_address"]: d.get("friendly_name") for d in data}
                tracker.set_lights(lights_from_devices(data, exclude))
                if raw_groups is not None:
                    tracker.set_groups(groups_from_bridge(raw_groups, ieee_to_name))
                order = sorted(tracker.bulbs)
            elif topic == f"{base}/bridge/groups" and isinstance(data, list):
                raw_groups = data
                tracker.set_groups(groups_from_bridge(raw_groups, ieee_to_name))
            elif topic == f"{base}/bridge/event" and isinstance(data, dict):
                if data.get("type") == "device_announce":
                    name = (data.get("data") or {}).get("friendly_name")
                    if tracker.on_announce(name, ts) and tracker.bulbs[name].pending_read_since is None:
                        request_read(name, ts)
            elif topic.count("/") == 1:
                tracker.on_message(topic.split("/", 1)[1], data, ts)
        except queue.Empty:
            pass

        now = time.time()
        tracker.process(now)
        if order and now >= next_read_at:
            name = order[next_index % len(order)]
            next_index += 1
            if tracker.bulbs[name].pending_read_since is None:
                request_read(name, now)
            next_read_at = now + interval / len(order)


if __name__ == "__main__":
    main()
