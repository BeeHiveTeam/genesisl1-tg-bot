"""Exercise alert retries, startup, and interactive reconfiguration on local fixtures."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import configure
from bot import Bot
from settings import Settings


class Recovery(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def bot(self, chats="", **values):
        return Bot(Settings.from_values({
            "STATE_PATH": str(self.root / "state.json"), "ALLOWED_CHAT_IDS": chats,
            "LANG": "en", "BOT_TOKEN": "test", **values,
        }))

    def test_retry_after_restart_drops_revoked_chat_and_persists_remaining_delivery(self):
        previous = self.bot("101,-202,303")
        state = {"offset": 10}
        with patch.object(previous, "send", return_value=False), contextlib.redirect_stderr(io.StringIO()):
            previous.broadcast("queued alert", state)
        previous.save_state(state)
        self.assertCountEqual([entry["cid"] for entry in state["pending_alerts"]], ["101", "-202", "303"])

        current = self.bot("101,-202")
        with patch.object(current.alerts, "collect", return_value=[]), \
             patch.object(current, "send", side_effect=lambda cid, *args, **kwargs: cid == "101") as send:
            current.monitor(current.load_state())
        self.assertCountEqual([call.args[0] for call in send.call_args_list], ["101", "-202"])
        remaining = current.load_state()
        self.assertEqual(remaining["offset"], 10)
        self.assertEqual([(item["cid"], item["text"]) for item in remaining["pending_alerts"]], [("-202", "queued alert")])
        with patch.object(current.alerts, "collect", return_value=[]), patch.object(current, "send", return_value=True) as send:
            current.monitor(remaining)
        send.assert_called_once_with("-202", current.tr("a_repeat") + "queued alert", kb=True, timeout=5)
        self.assertEqual(current.load_state()["pending_alerts"], [])

    def test_revoked_alerts_do_not_use_the_queue_limit_even_when_every_chat_is_revoked(self):
        for chats, expected in (("101", ["101"]), ("", [])):
            with self.subTest(chats=chats):
                bot = self.bot(chats, PENDING_MAX="1")
                state = {"pending_alerts": [
                    {"cid": cid, "text": "alert", "ts": 100} for cid in ("101", "303", "-404")
                ]}
                with patch("bot.time.time", return_value=110), patch.object(bot, "send", return_value=True) as send:
                    bot.retry_pending(state)
                self.assertEqual([call.args[0] for call in send.call_args_list], expected)
                self.assertEqual(state["pending_alerts"], [])

    def test_startup_polls_after_loading_non_object_state_and_preserves_valid_state(self):
        stored = {"offset": 41, "lang": "de", "tombstoned": True}
        for value in (None, [], ["old"], "text", 42, False, stored):
            with self.subTest(value=value):
                bot = self.bot()
                Path(bot.cfg.state_path).write_text(json.dumps(value))
                expected = stored if isinstance(value, dict) else {}
                with patch.object(bot, "monitor_once") as monitor, patch.object(bot, "broadcast"), \
                     patch.object(bot, "tg", side_effect=KeyboardInterrupt) as telegram, self.assertRaises(KeyboardInterrupt):
                    bot.run_bot()
                monitor.assert_called_once_with(expected)
                self.assertEqual(bot.lang, expected.get("lang", "en"))
                telegram.assert_called_once()
                method, params = telegram.call_args.args
                self.assertEqual(method, "getUpdates")
                self.assertEqual(params["offset"], expected.get("offset", 0))

    def test_invalid_queue_type_still_allows_new_alerts_to_be_queued_and_retried(self):
        bot = self.bot("101")
        for old_queue in (None, 5, "old", {"cid": "101"}):
            with self.subTest(old_queue=old_queue):
                bot.save_state({"offset": 10, "pending_alerts": old_queue})
                with patch.object(bot.alerts, "collect", return_value=["new"]), \
                     patch.object(bot, "send", side_effect=[False, True]) as send, contextlib.redirect_stderr(io.StringIO()):
                    bot.monitor(bot.load_state())
                self.assertEqual([call.args for call in send.call_args_list],
                                 [("101", "new"), ("101", bot.tr("a_repeat") + "new")])
                self.assertEqual(bot.load_state()["offset"], 10)
                self.assertEqual(bot.load_state()["pending_alerts"], [])

    def test_bad_pending_entries_do_not_block_an_allowed_legacy_numeric_recipient(self):
        bot = self.bot("101")
        entries = [None, 1, {"cid": "101", "ts": 99}, {"cid": "101", "text": "missing time"},
                   {"cid": "101", "text": "wrong time", "ts": "bad"}, {"cid": "101", "text": "infinite", "ts": "inf"},
                   {"cid": "101", "text": None, "ts": 100}, {"cid": 101, "text": "deliver", "ts": "100"}]
        bot.save_state({"pending_alerts": entries, "offset": 11})
        with patch("bot.time.time", return_value=110), patch.object(bot.alerts, "collect", return_value=[]), \
             patch.object(bot, "send", return_value=True) as send:
            bot.monitor(bot.load_state())
        send.assert_called_once_with(101, bot.tr("a_repeat") + "deliver", kb=True, timeout=5)
        self.assertEqual(bot.load_state(), {"pending_alerts": [], "offset": 11, "lang": "en"})

    def test_queue_capacity_caps_only_retries_and_keeps_the_newest_pending_alert(self):
        for limit, retries in (("0", []), ("-1", []), ("1", ["newest"])):
            with self.subTest(limit=limit):
                bot = self.bot("101", PENDING_MAX=limit)
                pending = [{"cid": "101", "text": content, "ts": 100} for content in ("old", "mid", "newest")]
                with patch("bot.time.time", return_value=110), patch.object(bot.alerts, "collect", return_value=["live"]), \
                     patch.object(bot, "send", return_value=True) as send, contextlib.redirect_stderr(io.StringIO()):
                    bot.monitor({"pending_alerts": pending})
                self.assertEqual([call.args[1] for call in send.call_args_list], ["live", *[bot.tr("a_repeat") + r for r in retries]])
                self.assertEqual(bot.load_state()["pending_alerts"], [])

    def test_disabled_retries_attempt_each_new_alert_once(self):
        bot = self.bot("101", PENDING_MAX="0")
        state = {}
        with patch.object(bot, "send", return_value=False) as send, contextlib.redirect_stderr(io.StringIO()):
            bot.broadcast("new alert", state)
            self.assertFalse(state.get("pending_alerts"))
            bot.retry_pending(state)
        send.assert_called_once_with("101", "new alert", kb=True)

    def test_configure_replaces_each_setting_once_and_keeps_effective_values_on_enter(self):
        path = self.root / "config.env"
        old = "12345:" + "a" * 24
        last = "54321:" + "b" * 24
        new = "15243:" + "c" * 24
        unrelated = ["# ALLOWED_CHAT_IDS is configured below", "LANG=de", "CHECK_INTERVAL=75"]
        duplicates = [f" BOT_TOKEN = {old}", "ALLOWED_CHAT_IDS=404", f"BOT_TOKEN={last}", "ALLOWED_CHAT_IDS = -100"]
        for label, initial, token_input, chat_input, token, chats in (
            ("duplicates, replace", duplicates, new, "201, -202", new, "201,-202"),
            ("duplicates, keep", duplicates, "", "", last, "-100"),
            ("single, keep", [f"BOT_TOKEN={last}", "ALLOWED_CHAT_IDS=3"], "", "", last, "3"),
            ("append missing", [], new, "101", new, "101"),
        ):
            with self.subTest(label=label):
                path.write_text("\n".join([*unrelated, *initial]) + "\n")
                output = io.StringIO()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(configure, "CONFIG", path))
                    stack.enter_context(patch.object(configure.os, "geteuid", return_value=0))
                    stack.enter_context(patch.object(configure.sys.stdin, "isatty", return_value=True))
                    stack.enter_context(patch.object(configure.getpass, "getpass", return_value=token_input))
                    stack.enter_context(patch("builtins.input", return_value=chat_input))
                    contact = stack.enter_context(patch.object(configure, "telegram", return_value={"username": "fixture"}))
                    stack.enter_context(patch.object(configure.os, "chown"))
                    stack.enter_context(patch.object(configure.grp, "getgrnam"))
                    systemctl = stack.enter_context(patch.object(configure.subprocess, "run"))
                    stack.enter_context(contextlib.redirect_stdout(output))
                    stack.enter_context(contextlib.redirect_stderr(output))
                    configure.main()
                contact.assert_called_once_with(token, "getMe")
                systemctl.assert_any_call(["systemctl", "restart", configure.SERVICE], check=True)
                self.assertCountEqual(path.read_text().splitlines(), [*unrelated, f"BOT_TOKEN={token}", f"ALLOWED_CHAT_IDS={chats}"])
                running = Settings.from_file(path)
                self.assertEqual(running.allowed, frozenset(chats.split(",")))
                self.assertEqual(running.token, token)
                self.assertEqual(path.stat().st_mode & 0o777, 0o640)
                for private in (old, last, new):
                    self.assertNotIn(private, output.getvalue())


if __name__ == "__main__":
    unittest.main()
