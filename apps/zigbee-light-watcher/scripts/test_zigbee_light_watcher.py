import unittest

from zigbee_light_watcher import Tracker, apply_mqtt_credentials, lights_from_devices

BAD = ["bad_decke1", "bad_decke2", "bad_decke3"]


class FakeSink:
    def __init__(self):
        self.events = []
        self.commanded_on = {}
        self.mismatch_on = {}
        self.missed = []
        self.uncommanded = []
        self.read_failures = []
        self.group_commands = []
        self.removed = []

    def light_info(self, bulb, groups): pass
    def light_removed(self, bulb): self.removed.append(bulb)
    def commanded(self, bulb, on): self.commanded_on[bulb] = on
    def read_ok(self, bulb, on, lq, ts): pass
    def reported(self, bulb, on, lq): pass
    def read_failed(self, bulb): self.read_failures.append(bulb)
    def mismatch(self, bulb, flag): self.mismatch_on[bulb] = flag
    def missed_command(self, bulb, source): self.missed.append((bulb, source))
    def uncommanded_change(self, bulb): self.uncommanded.append(bulb)
    def group_command(self, group, state): self.group_commands.append((group, state))
    def announce(self, device): pass
    def event(self, event_type, **fields): self.events.append((event_type, fields))

    def kinds(self):
        return [k for k, _ in self.events]


def state(s, lq=212):
    return {"state": s, "brightness": 254, "linkquality": lq}


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.sink = FakeSink()
        self.t = Tracker(self.sink, settle_s=3.0, read_timeout_s=10.0)
        self.t.set_lights(BAD + ["flur_decke"])
        self.t.set_groups({"Bad": BAD, "Flur": ["flur_decke"], "Sensors": ["not_a_light"]})

    def group_command(self, s, ts):
        # As logged by Z2M: first member, group, remaining members, all within a millisecond.
        self.t.on_message("bad_decke1", state(s), ts)
        self.t.on_message("Bad", {"state": s}, ts + 0.0002)
        self.t.on_message("bad_decke2", state(s), ts + 0.0004)
        self.t.on_message("bad_decke3", state(s, 148), ts + 0.0006)
        self.t.process(ts + 1)

    def read(self, bulb, answer, ts, lq=88):
        self.t.begin_read(bulb, ts)
        self.t.on_message(bulb, state(answer, lq), ts + 0.2)
        self.t.process(ts + 1)

    def baseline_all_off(self, ts=0):
        for i, b in enumerate(BAD):
            self.read(b, "OFF", ts + i)

    def test_first_read_sets_baseline_without_mismatch(self):
        self.read("bad_decke1", "ON", 0)
        self.assertTrue(self.sink.commanded_on["bad_decke1"])
        self.assertNotIn("mismatch_start", self.sink.kinds())

    def test_group_command_sets_commanded_for_all_members_and_counts_once(self):
        self.baseline_all_off()
        self.group_command("ON", 100)
        self.assertEqual(self.sink.group_commands, [("Bad", "ON")])
        self.assertTrue(all(self.sink.commanded_on[b] for b in BAD))

    def test_missed_group_off_is_detected_as_missed_command(self):
        self.baseline_all_off()
        self.group_command("ON", 100)
        self.group_command("OFF", 200)
        self.read("bad_decke2", "ON", 260)
        self.assertEqual(self.sink.missed, [("bad_decke2", "group:Bad")])
        self.assertTrue(self.sink.mismatch_on["bad_decke2"])
        kind, fields = self.sink.events[-1]
        self.assertEqual((kind, fields["kind"], fields["command_age_s"]), ("mismatch_start", "missed_command", 60.2))

    def test_followed_command_then_on_without_command_is_uncommanded_change(self):
        self.baseline_all_off()
        self.group_command("OFF", 100)
        self.read("bad_decke1", "OFF", 160)
        self.read("bad_decke1", "ON", 220)
        self.assertEqual(self.sink.uncommanded, ["bad_decke1"])
        self.assertEqual(self.sink.missed, [])

    def test_mismatch_is_counted_once_while_it_persists(self):
        self.baseline_all_off()
        self.group_command("OFF", 100)
        self.read("bad_decke1", "ON", 160)
        self.read("bad_decke1", "ON", 220)
        self.assertEqual(len(self.sink.missed), 1)

    def test_next_group_command_ends_mismatch(self):
        self.baseline_all_off()
        self.group_command("OFF", 100)
        self.read("bad_decke1", "ON", 160)
        self.group_command("OFF", 300)
        self.assertFalse(self.sink.mismatch_on["bad_decke1"])
        self.assertIn(("mismatch_end", {"bulb": "bad_decke1", "kind": "missed_command", "reason": "command",
                                        "duration_s": 139.8}), self.sink.events)

    def test_read_answer_triggering_derived_group_publish_is_not_a_command(self):
        # Z2M publishes the group again when a member's read answer changes the group state.
        self.baseline_all_off()
        self.group_command("OFF", 100)
        self.t.begin_read("bad_decke1", 160)
        self.t.on_message("bad_decke1", state("ON"), 160.2)
        self.t.on_message("Bad", {"state": "ON"}, 160.201)
        self.t.process(161)
        self.assertEqual(self.sink.group_commands, [("Bad", "OFF")])
        self.assertFalse(self.sink.commanded_on["bad_decke2"])
        self.assertEqual(self.sink.missed, [("bad_decke1", "group:Bad")])

    def test_read_shortly_after_command_is_not_judged(self):
        self.baseline_all_off()
        self.group_command("ON", 100)
        self.read("bad_decke1", "OFF", 101)
        self.assertEqual(self.sink.missed, [])

    def test_command_arriving_while_read_pending_discards_that_read(self):
        self.baseline_all_off()
        self.t.begin_read("bad_decke1", 99.9)
        self.group_command("ON", 100)
        self.t.on_message("bad_decke1", state("OFF"), 100.5)
        self.t.process(102)
        self.assertEqual(self.sink.missed, [])

    def test_direct_command_to_single_bulb(self):
        # Z2M publishes the new state once the bulb acknowledged, with a fresh linkquality.
        self.baseline_all_off()
        self.t.on_set("bad_decke3", 99.5)
        self.t.on_message("bad_decke3", state("ON", 84), 100)
        self.t.process(101)
        self.assertTrue(self.sink.commanded_on["bad_decke3"])
        self.assertFalse(self.sink.commanded_on["bad_decke1"])
        self.assertNotIn("mismatch_start", self.sink.kinds())

    def test_direct_command_ends_mismatch(self):
        self.baseline_all_off()
        self.group_command("OFF", 100)
        self.read("bad_decke1", "OFF", 160)
        self.read("bad_decke1", "ON", 220, lq=52)
        self.t.on_set("bad_decke1", 299)
        self.t.on_message("bad_decke1", state("OFF", 84), 300)
        self.t.process(301)
        self.assertFalse(self.sink.mismatch_on["bad_decke1"])
        self.assertEqual(self.sink.events[-1][1]["reason"], "command")

    def test_publish_long_after_a_set_is_a_device_report(self):
        self.baseline_all_off()
        self.group_command("OFF", 100)
        self.read("bad_decke1", "OFF", 160)
        self.t.on_set("bad_decke1", 170)
        self.t.on_message("bad_decke1", state("OFF"), 170.5)
        self.t.process(171)
        self.t.on_message("bad_decke1", state("ON"), 300)
        self.t.process(301)
        self.assertTrue(self.sink.mismatch_on["bad_decke1"])
        self.assertFalse(self.sink.commanded_on["bad_decke1"])

    def test_set_for_unknown_device_is_ignored(self):
        self.t.on_set("S99Lidl", 0)

    def test_unsolicited_report_does_not_end_mismatch(self):
        self.baseline_all_off()
        self.group_command("OFF", 100)
        self.read("bad_decke1", "OFF", 160)
        self.read("bad_decke1", "ON", 220, lq=52)
        self.t.on_message("bad_decke1", state("ON", 136), 400)
        self.t.process(401)
        self.assertTrue(self.sink.mismatch_on["bad_decke1"])
        self.assertFalse(self.sink.commanded_on["bad_decke1"])
        self.assertNotIn("mismatch_end", self.sink.kinds())

    def test_unsolicited_report_reveals_uncommanded_change(self):
        self.baseline_all_off()
        self.group_command("OFF", 100)
        self.read("bad_decke1", "OFF", 160)
        self.t.on_message("bad_decke1", state("ON", 132), 200)
        self.t.process(201)
        self.assertEqual(self.sink.uncommanded, ["bad_decke1"])
        self.assertFalse(self.sink.commanded_on["bad_decke1"])

    def test_unsolicited_report_matching_command_ends_mismatch(self):
        self.baseline_all_off()
        self.group_command("OFF", 100)
        self.read("bad_decke1", "ON", 160, lq=52)
        self.t.on_message("bad_decke1", state("OFF", 132), 200)
        self.t.process(201)
        self.assertFalse(self.sink.mismatch_on["bad_decke1"])
        self.assertEqual(self.sink.events[-1][1]["reason"], "report")

    def test_single_member_group_command(self):
        self.read("flur_decke", "OFF", 0)
        self.t.on_message("Flur", {"state": "ON"}, 100)
        self.t.on_message("flur_decke", state("ON"), 100.0003)
        self.t.process(101)
        self.assertEqual(self.sink.group_commands, [("Flur", "ON")])
        self.read("flur_decke", "OFF", 160)
        self.assertEqual(self.sink.missed, [("flur_decke", "group:Flur")])

    def test_slow_read_answer_still_counts_as_read_with_default_timeout(self):
        # Z2M's Ember adapter waits up to 15 s per request, so answers can arrive that late.
        t = Tracker(self.sink)
        t.set_lights(BAD)
        t.set_groups({"Bad": BAD})
        t.begin_read("bad_decke1", 0)
        t.on_message("bad_decke1", state("OFF"), 0.2)
        t.process(1)
        t.on_message("bad_decke1", state("ON"), 100)
        t.on_message("Bad", {"state": "ON"}, 100.0001)
        t.on_message("bad_decke2", state("ON"), 100.0002)
        t.on_message("bad_decke3", state("ON"), 100.0003)
        t.process(101)
        t.on_message("bad_decke1", state("OFF"), 200)
        t.on_message("Bad", {"state": "OFF"}, 200.0001)
        t.on_message("bad_decke2", state("OFF"), 200.0002)
        t.on_message("bad_decke3", state("OFF"), 200.0003)
        t.process(201)
        t.begin_read("bad_decke1", 260)
        t.process(272)
        t.on_message("bad_decke1", state("ON"), 274)
        t.process(275)
        self.assertEqual(self.sink.missed, [("bad_decke1", "group:Bad")])
        self.assertFalse(self.sink.commanded_on["bad_decke1"])

    def test_unanswered_read_counts_failure(self):
        self.t.begin_read("bad_decke1", 0)
        self.t.process(11)
        self.assertEqual(self.sink.read_failures, ["bad_decke1"])
        self.assertIsNone(self.t.bulbs["bad_decke1"].pending_read_since)

    def test_announce_of_light_requests_read(self):
        self.assertTrue(self.t.on_announce("bad_decke3", 0))
        self.assertFalse(self.t.on_announce("S99Lidl", 0))

    def test_payload_without_state_is_ignored(self):
        self.t.on_message("bad_decke1", {"linkquality": 3}, 0)
        self.t.process(1)
        self.assertNotIn("bad_decke1", self.sink.commanded_on)

    def test_non_light_group_members_are_dropped(self):
        self.assertNotIn("Sensors", self.t.groups)

    def test_removed_or_renamed_light_is_dropped_with_its_metrics(self):
        self.t.set_lights(["bad_decke1", "bad_decke2", "bad_ceiling3", "flur_decke"])
        self.assertNotIn("bad_decke3", self.t.bulbs)
        self.assertIn("bad_ceiling3", self.t.bulbs)
        self.assertEqual(self.sink.removed, ["bad_decke3"])
        self.assertEqual(self.t.groups["Bad"], ["bad_decke1", "bad_decke2"])

    def test_messages_for_a_removed_light_are_ignored(self):
        self.t.begin_read("bad_decke3", 0)
        self.t.set_lights(["bad_decke1", "bad_decke2", "flur_decke"])
        self.t.on_message("bad_decke3", state("ON"), 0.2)
        self.t.process(11)
        self.assertEqual(self.sink.read_failures, [])
        self.assertNotIn("bad_decke3", self.sink.commanded_on)


class LightsFromDevicesTest(unittest.TestCase):
    def test_selects_router_lights_and_applies_exclude(self):
        devices = [
            {"type": "Router", "friendly_name": "bad_decke1", "definition": {"exposes": [{"type": "light"}]}},
            {"type": "Router", "friendly_name": "P01", "definition": {"exposes": [{"type": "switch"}]}},
            {"type": "EndDevice", "friendly_name": "S99Lidl", "definition": {"exposes": []}},
            {"type": "Router", "friendly_name": "Stimmungsleuchte", "definition": {"exposes": [{"type": "light"}]}},
            {"type": "Coordinator", "friendly_name": "Coordinator", "definition": None},
        ]
        self.assertEqual(lights_from_devices(devices, "^Stimmung"), ["bad_decke1"])

    def test_skips_lights_with_per_endpoint_state(self):
        devices = [
            {"type": "Router", "friendly_name": "dual_dimmer",
             "definition": {"exposes": [{"type": "light", "endpoint": "l1"}, {"type": "light", "endpoint": "l2"}]}},
            {"type": "Router", "friendly_name": "bulb", "definition": {"exposes": [{"type": "light"}]}},
        ]
        self.assertEqual(lights_from_devices(devices, ""), ["bulb"])



class FakeMqttClient:
    def __init__(self):
        self.credentials = None

    def username_pw_set(self, username, password=None):
        self.credentials = (username, password)


class MqttCredentialsTest(unittest.TestCase):
    def test_sets_username_and_password_when_given(self):
        client = FakeMqttClient()
        apply_mqtt_credentials(client, {"MQTT_USERNAME": "watcher", "MQTT_PASSWORD": "secret"})
        self.assertEqual(client.credentials, ("watcher", "secret"))

    def test_stays_anonymous_without_username(self):
        client = FakeMqttClient()
        apply_mqtt_credentials(client, {})
        self.assertIsNone(client.credentials)

    def test_empty_username_counts_as_none(self):
        client = FakeMqttClient()
        apply_mqtt_credentials(client, {"MQTT_USERNAME": "", "MQTT_PASSWORD": "x"})
        self.assertIsNone(client.credentials)



if __name__ == "__main__":
    unittest.main()
