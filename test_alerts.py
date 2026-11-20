"""Exercise complete alert cycles against probe fixtures, including persisted state."""
import json
import unittest
from unittest.mock import Mock

from alerts import Monitor
from messages import T as TEXT
from node import Node
from settings import Settings


class Alerts(unittest.TestCase):
    def setUp(self):
        self.clock = 10000
        self.jailed = self.tombstoned = self.catching = False
        self.vp = 1
        self.available = True
        self.height, self.lag = 100, 0
        self.node = Mock(spec=Node)
        self.node.svc_active.return_value = True
        self.node.svc_start.return_value = "unchanged"
        self.node.rpc_endpoint.return_value = "localhost:27777"
        self.node.get_status.side_effect = lambda: dict(height=self.height, lag=self.lag, catching_up=self.catching, vp=self.vp)
        self.limits = dict(window=2000, jail=1728, warning=500, critical=1500)
        self.node.get_signing.side_effect = lambda: dict(missed=self.missed, jailed=self.jailed, jailed_until="test",
                                 tombstoned=self.tombstoned, limits=self.limits) if self.available else None
        self.node.get_peers.return_value = 12
        self.node.get_disk.return_value = dict(pct=44, avail_gb=400)
        config = Settings.from_values({"CHAIN_ID":"test-chain", "RPC_URL":"http://localhost:27777"})
        self.monitor = Monitor(config, self.node, lambda key: TEXT[key]["en"])
        self.state = {}

    def tick(self, missed=100, seconds=60, *, advance=1):
        self.missed = missed
        self.clock += seconds
        self.height += advance
        messages = self.monitor.collect(self.state, self.clock)
        self.saved = json.loads(json.dumps(self.state))
        return "\n".join(messages)

    def test_boundary_flapping_and_hourly_reminder(self):
        self.tick(499)
        self.assertIn('GenesisL1: MISSED BLOCKS = 500 (≥500)', self.tick(500))
        for value in (499, 500, 498, 500, 501, 499, 500):
            self.assertNotIn('🟡', self.tick(value))
        self.assertIn('🟡', self.tick(500, 3600))
        self.assertNotIn('🟡', self.tick(500))

    def test_recovery_rearms(self):
        self.tick(500)
        self.tick(451)
        self.assertNotIn('MISSED BLOCKS =', self.tick(500))
        self.tick(450)
        self.tick(499)
        self.assertIn('🟡', self.tick(500))

    def test_growth_critical_and_jail_never_suppressed(self):
        self.tick(500)
        self.assertIn('🟡', self.tick(600))
        self.assertIn('🔴', self.tick(1500))
        self.assertIn('≥1500', self.tick(1499) + self.tick(1500))
        self.jailed = True
        self.assertIn('JAILED', self.tick(1501))
        self.assertIn('MISSED BLOCKS still', self.tick(1501, 1800))

    def test_restart_migration_and_persistence(self):
        self.state['missed'] = 500
        self.assertNotIn('🟡', self.tick(500))
        self.state = self.saved
        self.assertTrue(self.state['missed_warn_active'])
        self.assertNotIn('🟡', self.tick(499))
        self.state = self.saved
        self.assertNotIn('🟡', self.tick(500))
        self.assertIn('🟡', self.tick(600))

    def test_signing_outage_and_rapid_misses_still_alert(self):
        self.tick(500)
        self.available = False
        self.tick(500)
        self.tick(500)
        self.assertIn('signing-info unavailable', self.tick(500))
        self.available = True
        recovered = self.tick(520, 1000)
        self.assertIn('Actively missing blocks: +20', recovered)
        self.assertIn('signing-info available again', recovered)
        self.assertNotIn('available again', self.tick(521))
        self.assertEqual(self.state['signing_fail_ticks'], 0)

    def test_short_signing_outage_is_silent(self):
        self.available = False
        self.tick(100)
        self.available = True
        self.assertEqual(self.tick(100), '')

    def test_tombstone_is_terminal_across_restart_and_signing_outage(self):
        self.tick(100)
        self.tombstoned = self.jailed = True
        self.vp = 0
        self.assertEqual(self.tick(1700), TEXT['a_tomb']['en'])
        self.state = self.saved
        self.assertEqual(self.tick(1710, 3600), '')
        self.available = False
        for _ in range(4):
            self.assertEqual(self.tick(1710, 1800), '')
        self.node.svc_active.return_value = False
        self.assertIn('SERVICE DOWN', self.tick(1710))

    def test_wrong_chain_outage_and_recovery_are_separate(self):
        correct = self.node.get_status.side_effect
        self.tick(100)
        self.node.get_status.side_effect = lambda: {'wrong_chain': 'other'}
        self.assertIn('other (expected test-chain)', self.tick(100))
        self.assertEqual(self.tick(100, 3600), '')
        self.assertFalse(self.state['rpc_dead'])
        self.node.get_status.side_effect = lambda: None
        self.assertIn('not responding', self.tick(100))
        self.assertEqual(self.state['wrong_chain'], 'other')
        self.assertEqual(self.state['signing_fail_ticks'], 0)
        self.node.get_status.side_effect = correct
        self.assertIn('expected chain test-chain again', self.tick(100))
        self.node.get_status.side_effect = lambda: None
        self.assertIn('RPC localhost:27777 not responding', self.tick(100))
        self.node.get_status.side_effect = lambda: {'wrong_chain': 'other'}
        self.assertNotIn('responding again', self.tick(100))

    def test_service_outage_reminder_recovery_and_restart(self):
        self.assertEqual(self.tick(), "")
        self.node.svc_active.return_value = False
        down = self.tick()
        self.assertEqual(down.count("SERVICE DOWN"), 1)
        self.assertIn("still down", down)
        self.assertEqual(self.tick(seconds=1799), "")
        self.assertIn("still down", self.tick(seconds=1))
        self.node.svc_active.return_value = True
        self.node.svc_start.return_value = "new start"
        up = self.tick()
        self.assertIn("active again", up)
        self.assertIn("RESTART", up)
        self.assertIn("new start", up)
        self.assertEqual(self.tick(), "")
        self.node.svc_start.return_value = "another start"
        self.assertIn("RESTART", self.tick())
        self.node.svc_start.return_value = ""
        self.assertEqual(self.tick(), "")

    def test_rpc_outage_recovery_is_reported_once_at_configured_endpoint(self):
        self.tick()
        healthy = self.node.get_status.side_effect
        self.node.get_status.side_effect = lambda: None
        first = self.tick()
        self.assertIn("RPC localhost:27777 not responding", first)
        self.assertIn("RPC localhost:27777 still not responding", first)
        self.assertEqual(self.tick(seconds=1799), "")
        self.assertIn("still not responding", self.tick(seconds=1))
        self.node.get_status.side_effect = healthy
        self.assertEqual(self.tick(), "✅ RPC localhost:27777 responding again")
        self.assertEqual(self.tick(), "")

    def test_tombstone_survives_stale_samples_until_another_identity_is_configured(self):
        self.tick()
        self.jailed = self.tombstoned = True
        self.assertEqual(self.tick(), TEXT["a_tomb"]["en"])
        self.tombstoned = False
        self.state = self.saved
        self.assertEqual(self.tick(seconds=3600), "")
        self.assertTrue(self.state["tombstoned"])
        other = Settings.from_values({"CHAIN_ID":"test-chain","VALCONS":"other"})
        self.monitor = Monitor(other, self.node, lambda key: TEXT[key]["en"])
        self.assertIn("VALIDATOR IS JAILED", self.tick())
        self.assertFalse(self.state["tombstoned"])

    def test_sync_height_peers_and_disk_transitions_at_their_boundaries(self):
        self.lag = 30
        self.node.get_peers.return_value = 3
        self.node.get_disk.return_value = {"pct":84, "avail_gb":20}
        self.assertEqual(self.tick(), "")
        self.catching, self.lag = True, 31
        self.node.get_peers.return_value = 2
        self.node.get_disk.return_value["pct"] = 85
        down = self.tick()
        for message in ("catching_up=true","Block lag: 31s","Few peers: 2","DISK /: 85%"):
            self.assertIn(message, down)
        self.assertEqual(self.tick(advance=0), "")
        self.assertEqual(self.tick(advance=0), "")
        self.assertIn("BLOCKS NOT ADVANCING", self.tick(advance=0))
        self.catching, self.lag = False, 30
        self.node.get_peers.return_value = 3
        self.node.get_disk.return_value["pct"] = 84
        up = self.tick()
        for message in ("Sync recovered","lag is normal (30s)","Blocks advancing again","Peers normal: 3","Disk ok"):
            self.assertIn(message, up)
        self.assertEqual(self.tick(), "")

    def test_reloaded_thresholds_apply_to_reminders_when_signing_times_out(self):
        self.limits = dict(window=100,jail=13,warning=2,critical=10)
        self.tick(10)
        self.available = False
        self.state = self.saved
        self.assertIn("MISSED BLOCKS still 10 (jail threshold 13)",self.tick(10,1800))

    def test_unknown_height_does_not_clear_a_stall(self):
        self.state.update(stalled=True, height=100, stall_ticks=4)
        self.node.get_status.side_effect = lambda: dict(height=None, vp=None, lag=None, catching_up=False)
        self.tick()
        self.assertEqual(self.state["height"],100)
        self.assertTrue(self.state["stalled"])

    def test_translations_have_correct_boundary_and_network(self):
        for language in ('en', 'ru', 'de'):
            self.assertIn('GenesisL1', TEXT['a_missed_warn'][language])
            self.assertIn('≥500', TEXT['a_missed_warn'][language] % (500, 500))
            self.assertIn('≥1500', TEXT['a_missed'][language] % (1500, 1500, 1728))

if __name__ == '__main__':
    unittest.main()
