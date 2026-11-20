"""Check GenesisL1 protobuf queries and the public bot entry points offline."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import urllib.request
from unittest.mock import patch

from bot import Bot
from node import protobuf_fields, signing_limits
from settings import Settings


def make_bot(**config):
    return Bot(Settings.from_values({"VALOPER": "operator", "VALCONS": "signer", **config}))


class ChainQueries(unittest.TestCase):
    def signing_queries(self, path, address=None):
        messages = {
            "/cosmos.slashing.v1beta1.Query/SigningInfo": b"\x0a\x06signer\x22\x00",
            "/cosmos.staking.v1beta1.Query/Validator": b"\x0a\x08operator",
            "/cosmos.slashing.v1beta1.Query/Params": b"\x08\x90\x4e\x12\x12500000000000000000",
        }
        message = messages[path]
        if path.endswith("/Validator") and self.jailed:
            message += b"\x18\x01"
        return {1: message}

    def setUp(self):
        self.jailed = False
        self.bot = make_bot()

    def test_live_parameter_shape_and_omitted_zero_fields(self):
        with patch.object(self.bot.node, "abci_query", self.signing_queries):
            signing = self.bot.node.get_signing()
        self.assertEqual(signing["missed"], 0)
        self.assertFalse(signing["tombstoned"])
        self.assertFalse(signing["jailed"])
        self.assertEqual(signing["limits"]["window"], 10000)
        self.assertEqual(signing["limits"]["jail"], 5000)
        self.assertEqual((signing["limits"]["warning"], signing["limits"]["critical"]), (500, 4000))

    def test_expired_jail_timestamp_does_not_hide_staking_jail(self):
        self.jailed = True
        with patch.object(self.bot.node, "abci_query", self.signing_queries):
            signing = self.bot.node.get_signing()
        self.assertTrue(signing["jailed"])
        self.assertTrue(signing["jailed_until"].startswith("1970"))

    def test_slashing_window_minimum_rounding_and_alert_boundaries(self):
        half = b"500000000000000000"
        # LegacyDec rounds a half to the nearest even number.
        self.assertEqual(signing_limits(3, half, None, None)["jail"], 1)
        self.assertEqual(signing_limits(5, half, None, None)["jail"], 3)
        self.assertEqual(signing_limits(20, b"0", 1, 21), {"window":20, "jail":20, "warning":1, "critical":21})
        self.assertEqual(signing_limits(20, b"1000000000000000000", None, None)["critical"], 1)
        for args in ((0, half, None, None), (1,b"-1",None,None), (1,b"1000000000000000001",None,None),
                     (2,b"bad",None,None), (20,b"0",0,2), (20,b"0",3,2), (20,b"0",1,22)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                signing_limits(*args)

    def test_failed_signing_query_cannot_modify_previous_thresholds(self):
        with patch.object(self.bot.node, "abci_query", self.signing_queries):
            observed = self.bot.node.get_signing()
        previous = dict(observed["limits"])
        def wrong_identity(path, address=None):
            if path.endswith("SigningInfo"):
                return {1: b"\x0a\x05other"}
            return self.signing_queries(path, address)
        with patch.object(self.bot.node, "abci_query", wrong_identity):
            self.assertIsNone(self.bot.node.get_signing())
        self.assertEqual(observed["limits"], previous)
        self.assertEqual(self.bot.cfg.default_limits()["jail"], 0)

    def test_protobuf_bounds_and_unknown_fields(self):
        self.assertEqual(protobuf_fields(b"\x08\x01\x21" + bytes(8)), {1: 1})
        for invalid in [b"\x00", b"\x80", b"\x0a\x05abc", b"\x09\x00"]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                protobuf_fields(invalid)

    def test_abci_errors_are_not_healthy_empty_signing_info(self):
        response = {"result": {"response": {"code": 1, "value": ""}}}
        with patch.object(self.bot.node, "http_get", return_value=response), self.assertRaises(ValueError):
            self.bot.node.abci_query("/cosmos.slashing.v1beta1.Query/SigningInfo", "signer")
        with patch.object(self.bot.node, "abci_query", side_effect=ValueError("unavailable")):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(self.bot.node.get_signing())

    def test_wrong_chain_is_rejected(self):
        self.bot = make_bot(RPC_URL="https://rpc.example:3000/service", CHAIN_ID="expected")
        status = {"result": {"node_info": {"network": "lumen"}}}
        with patch.object(self.bot.node, "http_get", return_value=status), patch.object(self.bot, "lang", "en"), \
             patch.object(self.bot.node, "svc_active", return_value=True), patch.object(self.bot.node, "get_disk", return_value={"pct": None}), \
             patch.object(self.bot.node, "get_signing") as signing, patch.object(self.bot.node, "get_delegation") as stake, \
             patch.object(self.bot.node, "get_peers") as peers:
            for report in (self.bot.fmt_status, self.bot.fmt_sync, self.bot.fmt_val, self.bot.fmt_peers):
                self.assertIn("RPC rpc.example:3000 reports chain lumen (expected expected)", report())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(self.bot.check_node(), 1)
            self.assertEqual(json.loads(output.getvalue())["rpc_error"]["actual"], "lumen")
            for query in (signing, stake, peers):
                query.assert_not_called()

    def test_endpoint_in_reports_uses_only_host_and_effective_port(self):
        for url, label in (("http://127.0.0.1:26657", "127.0.0.1:26657"),
                           ("https://{}@rpc.invalid/private?q=x".format("operator:example"), "rpc.invalid:443"),
                           ("http://[::1]:38000/path", "[::1]:38000"), ("http://rpc.invalid", "rpc.invalid:80")):
            self.bot = make_bot(RPC_URL=url)
            with self.subTest(url=url), patch.object(self.bot.node, "get_status", return_value=None):
                for lang in ("en", "ru", "de"):
                    with patch.object(self.bot, "lang", lang):
                        self.assertEqual(self.bot.fmt_sync(), self.bot.tr("a_rpc_down") % label)

    def test_validator_report_uses_configured_chain_and_translated_flags(self):
        self.bot = make_bot(CHAIN_ID="local-chain")
        for value in (False, True):
            signing = {"jailed": value, "tombstoned": value, "missed": 7, "jailed_until": "test", "limits": {"window": 100, "jail": 50}}
            with patch.object(self.bot.node, "get_signing", return_value=signing), \
                 patch.object(self.bot.node, "get_status", return_value={"vp": 1}), patch.object(self.bot, "fmt_delegation", return_value="stake"):
                for lang in ("en", "ru", "de"):
                    with patch.object(self.bot, "lang", lang):
                        text = self.bot.fmt_val()
                        self.assertIn("local-chain", text)
                        mark = self.bot.tr("y_yes" if value else "y_no")
                        self.assertIn("Tombstoned: " + mark, text)
                        self.assertIn("Jailed: " + mark, text)

    def test_disk_usage_failure_is_reported(self):
        with patch("node.shutil.disk_usage", side_effect=OSError), patch.object(self.bot, "lang", "en"):
            self.assertIsNone(self.bot.node.get_disk()["pct"])
            self.assertIn("unavailable", self.bot.fmt_disk())
            self.assertIn("unavailable", self.bot.disk_summary())

    def test_startup_and_poll_persistence_errors_allow_next_monitor(self):
        state = {}
        self.bot = make_bot(BOT_TOKEN="test", CHECK_INTERVAL="1")
        with patch.object(self.bot, "load_state", return_value=state), \
             patch.object(self.bot, "monitor", side_effect=[OSError(), None]) as monitor, patch.object(self.bot, "broadcast"), \
             patch.object(self.bot, "tg", side_effect=[{"ok": True, "result": []}, KeyboardInterrupt]), \
             patch.object(self.bot, "save_state", side_effect=OSError()), \
             patch.object(time, "time", side_effect=[10, 12, 12]), contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises(KeyboardInterrupt):
            self.bot.run_bot()
        self.assertEqual(monitor.call_count, 2)

    def validator_response(self, raw):
        amount = raw.encode('ascii')
        return {1: b'\x0a\x08operator' + b'\x2a' + bytes([len(amount)]) + amount}

    def test_delegation_is_exact_tokens_not_voting_power_or_shares(self):
        # Field 5 is tokens; field 6 is a different shares value.
        response = self.validator_response('326061300000000000000001')
        response[1] += b'\x32\x041.00'
        with patch.object(self.bot.node, "abci_query", return_value=response):
            result = self.bot.node.get_delegation()
        self.assertEqual(result['tokens_el1'], '326061300000000000000001')
        self.assertEqual(result['tokens_L1_formatted'], '326 061.300000000000000001')

    def test_zero_delegation_is_available_but_missing_or_invalid_is_not_zero(self):
        with patch.object(self.bot.node, "abci_query", return_value=self.validator_response('0')):
            self.assertEqual(self.bot.node.get_delegation()['tokens_L1_formatted'], '0')
        for response in [{1: b'\x0a\x08operator'}, self.validator_response('-1'),
                         self.validator_response('NaN'), {1: b'\x0a\x05other\x2a\x010'}]:
            with self.subTest(response=response), patch.object(self.bot.node, "abci_query", return_value=response):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertIsNone(self.bot.node.get_delegation())

    def test_validator_report_keeps_delegation_when_signing_probe_fails(self):
        with patch.object(self.bot.node, "get_signing", return_value=None), \
             patch.object(self.bot.node, "get_status", return_value=None), \
             patch.object(self.bot.node, "get_delegation", return_value={'tokens_L1_formatted': '326 061.3'}), \
             patch.object(self.bot, 'lang', 'ru'):
            self.assertIn('Всего делегировано: 326 061.3 L1', self.bot.fmt_val())

    def test_language_persists_through_main_loop_state_save(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bot = make_bot(STATE_PATH=str(Path(directory)/"state.json"))
            with patch.object(self.bot, "lang", "de"):
                state = {"lang": "en", "offset": 20}
                self.bot.save_state(state)
                self.assertEqual(self.bot.load_state()["lang"], "de")

    def test_pending_retries_are_bounded_and_expired_alerts_are_dropped(self):
        state = {"pending_alerts": [
            {"cid": "1", "text": "expired", "ts": 1},
            *[{"cid": "1", "text": str(index), "ts": 100000} for index in range(5)],
        ]}
        with patch.object(time, "time", return_value=100001):
            with patch.object(self.bot, "send", return_value=False) as send:
                self.bot.retry_pending(state)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(len(state["pending_alerts"]), 5)
        self.assertTrue(all(call.kwargs["timeout"] == 5 for call in send.call_args_list))

    def test_transport_errors_do_not_log_tokens(self):
        secret = os.urandom(16).hex()
        with patch.object(urllib.request, "urlopen", side_effect=ValueError(secret)):
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                self.assertIsNone(self.bot.tg("getMe"))
        self.assertNotIn(secret, output.getvalue())


if __name__ == "__main__":
    unittest.main()
