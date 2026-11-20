import contextlib
import getpass
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

import configure
from bot import Bot
from settings import Settings


class PrivacyTests(unittest.TestCase):
    def test_setup_stops_if_terminal_would_echo(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.env"
            config.write_text("BOT_TOKEN=\nALLOWED_CHAT_IDS=\n")
            original = config.read_bytes()
            def no_echo_control(prompt):
                warnings.warn("Hidden input unavailable", getpass.GetPassWarning)
                self.fail("Token input must be stopped")
            with patch.object(configure, "CONFIG", config), patch.object(configure.os, "geteuid", return_value=0), \
                 patch.object(configure.sys.stdin, "isatty", return_value=True), \
                 patch.object(configure.getpass, "getpass", side_effect=no_echo_control), \
                 patch.object(configure, "telegram") as contact, self.assertRaises(SystemExit):
                configure.main()
            contact.assert_not_called()
            self.assertEqual(config.read_bytes(), original)

    def test_unlisted_chat_cannot_get_node_reports(self):
        bot = Bot(Settings.from_values({"ALLOWED_CHAT_IDS": "1"}))
        with patch.object(bot, "dispatch") as report, patch.object(bot, "send") as send, \
             patch.object(bot, "tg", return_value={"ok": True}), contextlib.redirect_stderr(io.StringIO()):
            bot.handle({"chat":{"id":2}, "text":"/status"})
            bot.handle({"chat":{"id":2}, "text":"/start"})
            bot.handle_callback({"id":"test", "message":{"chat":{"id":2}}, "data":"status"})
            report.assert_not_called()
            self.assertEqual(send.call_count, 3)
            for call in send.call_args_list:
                self.assertNotIn(bot.cfg.host, call.args[1])
            bot.handle({"chat":{"id":1}, "text":"/status"})
            report.assert_called_once_with("1", "status")


if __name__ == "__main__":
    unittest.main()
