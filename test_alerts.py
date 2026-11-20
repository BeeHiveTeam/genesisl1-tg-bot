"""Offline alert regression checks; no config secrets, RPC, Telegram or validator keys."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).with_name('bot.py')
TREE = ast.parse(SOURCE.read_text())
TEXT = next(ast.literal_eval(n.value) for n in TREE.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'T' for t in n.targets))

class Alerts(unittest.TestCase):
    def setUp(self):
        self.clock = 10000
        self.missed = 499
        self.jailed = False
        self.available = True
        self.height = 100
        self.messages = []
        self.saved = None
        self.ns = dict(time=SimpleNamespace(time=lambda: self.clock),
                       MISSED_WARN=500, MISSED_CRIT=1500, MISSED_WARN_HYSTERESIS=50,
                       MISSED_WARN_STEP=100, MISSED_WARN_REMINDER_SEC=3600,
                       JAIL_THRESHOLD=1728, CHECK_INTERVAL=60, STALL_TICKS=3,
                       MISSED_ALERT_MIN_GAP=900, SIGNING_FAIL_ALERT_TICKS=3,
                       PEERS_FAIL_ALERT_TICKS=5, PEERS_MIN=3, DISK_WARN_PCT=85,
                       LAG_WARN=30, tr=lambda key: TEXT[key]['en'],
                       svc_active=lambda: True, svc_start=lambda: 'unchanged',
                       get_status=lambda: dict(height=self.height, lag=0, catching_up=False, vp=1),
                       get_signing=lambda: dict(missed=self.missed, jailed=self.jailed, jailed_until='test', tombstoned=False) if self.available else None,
                       get_peers=lambda: 12, get_disk=lambda: dict(pct=44, avail_gb=400),
                       broadcast=lambda text, st: self.messages.append(text),
                       retry_pending=lambda st: None, save_state=lambda st: setattr(self, 'saved', json.loads(json.dumps(st))))
        funcs = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in ('monitor', 'missed_warning_due')]
        exec(compile(ast.Module(body=funcs, type_ignores=[]), str(SOURCE), 'exec'), self.ns)
        self.state = {}

    def tick(self, missed, seconds=60):
        self.missed = missed
        self.clock += seconds
        self.height += 1
        self.messages.clear()
        self.ns['monitor'](self.state)
        return '\n'.join(self.messages)

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
        self.assertIn('Actively missing blocks: +20', self.tick(520, 1000))
        self.assertEqual(self.state['signing_fail_ticks'], 0)

    def test_translations_have_correct_boundary_and_network(self):
        for language in ('en', 'ru', 'de'):
            self.assertIn('GenesisL1', TEXT['a_missed_warn'][language])
            self.assertIn('≥500', TEXT['a_missed_warn'][language] % (500, 500))
            self.assertIn('≥1500', TEXT['a_missed'][language] % (1500, 1500, 1728))

if __name__ == '__main__':
    unittest.main()
