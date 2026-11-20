import contextlib
import io
import json
import os
from pathlib import Path
import socket
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from bot import Bot, prefer_ipv4
from settings import Settings


class EntryPoints(unittest.TestCase):
    def test_import_neither_opens_the_config_nor_changes_dns_resolution(self):
        code = ("import socket; before=socket.getaddrinfo; "
                "import bot, alerts, node, messages, settings; assert socket.getaddrinfo is before")
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable,"-B","-c",code], cwd=Path(__file__).resolve().parent,
                                    env=dict(os.environ, GENESISL1_BOT_CONFIG=directory, PREFER_IPV4="1"),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_layout_runs_check_with_custom_config(self):
        repo = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            # Exercise exactly the runtime files that install.sh ships.
            declaration = next(line for line in (repo/"install.sh").read_text().splitlines() if line.startswith("runtime=("))
            for name in shlex.split(declaration[len("runtime=("):-1]):
                shutil.copyfile(repo/name, stage/name)
            config = stage/"test.env"
            config.write_text("RPC_URL=http://127.0.0.1:35777\nCHAIN_ID=expected\nLANG=en\n")
            # Only the transport and systemctl are substituted; run the real CLI.
            (stage/"sitecustomize.py").write_text(
                "import io, urllib.request\n"
                "def status(url, **kwargs):\n"
                "    assert url == 'http://127.0.0.1:35777/status'\n"
                "    return io.BytesIO(b'{\"result\":{\"node_info\":{\"network\":\"foreign\"}}}')\n"
                "urllib.request.urlopen = status\n")
            command = stage/"systemctl"
            command.write_text("#!/bin/sh\nprintf 'active\\n'\n")
            command.chmod(0o700)
            run = subprocess.run([sys.executable, "-B", str(stage/"bot.py"), "--check"], cwd=stage,
                                 env=dict(os.environ, GENESISL1_BOT_CONFIG=str(config), PYTHONPATH=str(stage),
                                          PATH=str(stage)+os.pathsep+os.environ["PATH"], PREFER_IPV4="0"),
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 1, run.stderr)
            self.assertEqual(json.loads(run.stdout)["rpc_error"], {"kind":"chain_id_mismatch",
                             "endpoint":"127.0.0.1:35777", "expected":"expected","actual":"foreign"})

    def test_ipv4_preference_retains_ipv6_and_order_within_families(self):
        answers = [(socket.AF_INET6, "v6a"), (socket.AF_INET, "v4a"),
                   (socket.AF_INET6,"v6b"), (socket.AF_INET,"v4b")]
        with patch.object(socket, "getaddrinfo", return_value=answers) as resolver:
            prefer_ipv4()
            self.assertEqual(socket.getaddrinfo("example",443),[answers[1],answers[3],answers[0],answers[2]])
            resolver.assert_called_once_with("example",443)

    def test_language_command_persists_offset_and_does_not_change_other_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"state.json")
            first = Bot(Settings.from_values({"STATE_PATH":path,"LANG":"de"}))
            second = Bot(Settings.from_values({"LANG":"ru"}))
            first.state = {"offset":42, "tombstoned":True}
            with patch.object(first, "send"):
                first.dispatch("1","lang")
            self.assertEqual(first.lang, "en")
            self.assertEqual(second.lang, "ru")
            self.assertEqual(json.loads(Path(path).read_bytes()), {"offset":42,"lang":"en","tombstoned":True})

    def test_monitor_delivers_retries_and_persists_in_one_cycle(self):
        bot = Bot(Settings.from_values({}))
        state = {}
        actions = []
        with patch.object(bot.alerts, "collect", return_value=["one","two"]), \
             patch.object(bot, "broadcast", side_effect=lambda msg, st:actions.append((msg,st))), \
             patch.object(bot, "retry_pending", side_effect=lambda st:actions.append(("retry",st))), \
             patch.object(bot, "save_state", side_effect=lambda st:actions.append(("save",st))):
            bot.monitor(state)
        self.assertEqual(actions,[("one\n\ntwo",state),("retry",state),("save",state)])

    def test_public_check_schema_still_exposes_thresholds_at_top_level(self):
        bot = Bot(Settings.from_values({}))
        limits = dict(window=100,jail=50,warning=5,critical=40)
        signing = dict(jailed=False,tombstoned=False,missed=0,jailed_until="test")
        with patch.multiple(bot.node, get_status=lambda:{"height":4}, get_signing=lambda:dict(signing,limits=limits),
                            svc_active=lambda:True, get_delegation=lambda:None, get_peers=lambda:4, get_disk=lambda:{}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(bot.check_node(),0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["signing"], signing)
        self.assertEqual([report[k] for k in ("slash_window","jail_threshold","missed_warning","missed_critical")],
                         [100,50,5,40])
