#!/usr/bin/env python3
"""Read-only Telegram monitoring for a GenesisL1 validator."""
import fcntl
import json
import os
import socket
import ssl
import sys
import time
import urllib.parse
import urllib.request

from alerts import Monitor
from messages import T, rpc_problem
from node import Node, sh_args
from settings import DEFAULT_PATH, Settings


def prefer_ipv4():
    """Apply the daemon's address ordering at startup. Preserve IPv6-only answers."""
    original = socket.getaddrinfo
    def resolve(*args, **kwargs):
        return sorted(original(*args, **kwargs), key=lambda r: r[0] != socket.AF_INET)
    socket.getaddrinfo = resolve


class Bot:
    def __init__(self, settings):
        self.cfg = settings
        self.node = Node(settings)
        self.lang = settings.default_lang
        self.alerts = Monitor(settings, self.node, self.tr)
        self.api = "https://api.telegram.org/bot%s/" % settings.token
        self.ssl_context = ssl.create_default_context()
        self.cb_seen = {}
        self.state = {}

    def tr(self, key):
        return T[key].get(self.lang, T[key]["en"])

    def rpc_problem(self, status):
        return rpc_problem(self.tr, self.node.rpc_endpoint(), self.cfg.chain_id, status)

    def monitor(self, st):
        alerts = self.alerts.collect(st, time.time())
        if alerts:
            self.broadcast("\n\n".join(alerts), st)
        self.retry_pending(st)
        self.save_state(st)

    def tg(self, method, params=None, timeout=35):
        data = urllib.parse.urlencode(params or {}).encode()
        req = urllib.request.Request(self.api + method, data=data)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ssl_context) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # тело ошибки Telegram содержит error_code/description — отдаём наверх,
            # чтобы отличать "колбэк протух" (400) от сетевых сбоев (None)
            try:
                body = json.load(e)
            except Exception:
                body = {"ok": False, "error_code": e.code}
            sys.stderr.write("tg %s HTTP error: %s\n" % (method, e.code))
            return body
        except Exception as e:
            sys.stderr.write("tg %s transport error: %s\n" % (method, type(e).__name__)); return None

    def keyboard(self):
        """Inline keyboard in the current language, with a language toggle."""
        return json.dumps({"inline_keyboard": [
            [{"text": self.tr("b_status"), "callback_data": "status"}, {"text": self.tr("b_val"), "callback_data": "val"}],
            [{"text": self.tr("b_sync"), "callback_data": "sync"}, {"text": self.tr("b_peers"), "callback_data": "peers"}],
            [{"text": self.tr("b_disk"), "callback_data": "disk"}, {"text": self.tr("b_help"), "callback_data": "help"}],
            [{"text": self.tr("b_lang"), "callback_data": "lang"}],
        ]})

    def send(self, chat_id, text, kb=False, timeout=35):
        # kb=True — прикрепить инлайн-кнопки к последней части
        ok = True
        chunks = [text[i:i+3900] for i in range(0, len(text), 3900)]
        for n, chunk in enumerate(chunks):
            params = {"chat_id": chat_id, "text": chunk,
                      "disable_web_page_preview": "true"}
            if kb and n == len(chunks) - 1:
                params["reply_markup"] = self.keyboard()
            r = self.tg("sendMessage", params, timeout=timeout)
            if not (r and r.get("ok")):
                ok = False
        return ok

    def broadcast(self, text, st=None):
        for cid in self.cfg.allowed:
            if self.send(cid, text, kb=True):
                sys.stderr.write("Telegram alert delivered\n")
            elif st is not None:
                st.setdefault("pending_alerts", []).append({"cid": cid, "text": text, "ts": time.time()})
                sys.stderr.write("Telegram alert queued for retry\n")

    def retry_pending(self, st):
        now = time.time()
        pending = [alert for alert in st.get("pending_alerts", [])
                   if now - float(alert.get("ts", 0)) <= self.cfg.pending_ttl_sec][-self.cfg.pending_max:]
        remaining = []
        for alert in pending[:2]:
            if not self.send(alert["cid"], self.tr("a_repeat") + alert["text"], kb=True, timeout=5):
                remaining.append(alert)
        st["pending_alerts"] = remaining + pending[2:]

    def fmt_delegation(self):
        delegation = self.node.get_delegation()
        return (self.tr("st_stake") % delegation["tokens_L1_formatted"]
                if delegation is not None else self.tr("st_stake_unk"))

    def disk_summary(self):
        disk = self.node.get_disk()
        if disk["pct"] is None:
            return self.tr("st_disk_unk")
        return self.tr("st_disk") % ("✅" if disk["pct"] < self.cfg.disk_warn_pct else "🟡",
                                disk["pct"], disk["avail_gb"])

    def sync_symbol(self, s):
        """
        Состояние синхронизации по ВОЗРАСТУ БЛОКА, а не по флагу catching_up.

        catching_up=false у CometBFT значит «догоняющая синхронизация не идёт» — это же
        возвращает и остановившаяся нода: процесса нет, значит false, значит зелёно. Возраст
        последнего блока — единственный признак, который врать не умеет. Три состояния, а не
        два: измерили и норма, измерили и отстаём, измерить не смогли.

        Тот же баг был в monad-tg-bot и проявился 2026-08-07: нода стояла на одном блоке,
        отставая на тысячи, а /status рисовал ✅. Здесь детект в monitor() работает правильно
        и без этой правки — сломан был только показываемый значок.
        """
        if s["lag"] is None:
            return self.tr("s_nolag")
        if s["lag"] > self.cfg.lag_warn:
            return self.tr("s_behind")
        if s["catching_up"]:
            return "🟡 catching_up"
        return self.tr("s_ok")

    def fmt_status(self):
        L = ["🔷 GenesisL1 — %s" % self.cfg.host]
        L.append(self.tr("svc") + ("✅ active" if self.node.svc_active() else self.tr("svc_bad")))
        s = self.node.get_status()
        if s and "wrong_chain" in s:
            return "\n".join(L + [self.rpc_problem(s), self.disk_summary()])
        if s:
            L.append(self.tr("st_net") % (s["network"], self.node.app_version(), s["version"]))
            L.append(self.tr("st_sync") % (self.sync_symbol(s), s["height"],
                     "?" if s["lag"] is None else "%ds" % s["lag"]))
            L.append("Voting power: %s" % s["vp"])
        else:
            L.append(self.rpc_problem(s))
        L.append(self.fmt_delegation())
        sg = self.node.get_signing()
        if sg:
            jl = self.tr("y_yes") if sg["jailed"] else self.tr("y_no")
            tb = " 💀tombstoned" if sg["tombstoned"] else ""
            L.append("Jailed: %s%s" % (jl, tb))
            L.append(self.tr("v_missed") % (sg["missed"], sg["limits"]["window"], sg["limits"]["jail"]))
        else:
            # Раньше эти строки просто ПРОПАДАЛИ из сводки. Отчёт выглядел здоровым — без единого
            # признака, что джейл и пропущенные блоки не проверялись вообще: не отвечает RPC, нет
            # genesisd, не задан VALCONS. Молчание читается как «всё хорошо», а это худший из
            # возможных ответов для проверки, ради которой бот и существует.
            L.append(self.tr("st_jail_unk"))
            L.append(self.tr("st_missed_unk"))
        p = self.node.get_peers()
        L.append(self.tr("st_peers") % p if p is not None else self.tr("st_peers_unk"))
        L.append(self.disk_summary())
        return "\n".join(L)

    def fmt_val(self):
        s = self.node.get_status()
        if s and "wrong_chain" in s:
            return self.rpc_problem(s)
        sg = self.node.get_signing()
        if not sg:
            lines = [self.tr("v_nosign"), self.fmt_delegation()]
            if s is None:
                lines.insert(0, self.rpc_problem(s))
            return "\n".join(lines)
        return ((self.tr("v_body") + self.tr("st_missed"))
                % (self.cfg.chain_id, self.tr("y_yes") if sg["jailed"] else self.tr("y_no"), sg["jailed_until"],
                   self.tr("y_yes") if sg["tombstoned"] else self.tr("y_no"), sg["missed"], sg["limits"]["window"], sg["limits"]["jail"],
                   s["vp"] if s else "?")) + "\n" + self.fmt_delegation()

    def fmt_sync(self):
        s = self.node.get_status()
        if not s or "wrong_chain" in s: return self.rpc_problem(s)
        return (self.tr("sy_body")
                % (self.sync_symbol(s),
                   s["height"], "?" if s["lag"] is None else "%ds" % s["lag"], s["catching_up"]))

    def fmt_peers(self):
        s = self.node.get_status()
        if s and "wrong_chain" in s:
            return self.rpc_problem(s)
        p = self.node.get_peers(); return self.tr("pe_body") % ("?" if p is None else p)

    def fmt_disk(self):
        d = self.node.get_disk()
        if d["pct"] is None:
            return self.tr("st_disk_unk")
        ok, detail = sh_args(["df", "-h", "/"])
        return (self.tr("dk_body") % (self.tr("dk_used") % (d["pct"], d["avail_gb"]),
                                 detail.splitlines()[-1] if ok and detail else "")).rstrip()

    def load_state(self):
        try:
            with open(self.cfg.state_path) as f: return json.load(f)
        except Exception: return {}

    def save_state(self, st):
        tmp = self.cfg.state_path + ".tmp"
        st["lang"] = self.lang
        with open(tmp, "w") as f: json.dump(st, f)
        os.replace(tmp, self.cfg.state_path)

    def dispatch(self, cid, cmd):
        # общий диспетчер для /команд и нажатий кнопок
        if   cmd == "status": self.send(cid, self.fmt_status(), kb=True)
        elif cmd == "val":    self.send(cid, self.fmt_val(), kb=True)
        elif cmd == "sync":   self.send(cid, self.fmt_sync(), kb=True)
        elif cmd == "peers":  self.send(cid, self.fmt_peers(), kb=True)
        elif cmd == "disk":   self.send(cid, self.fmt_disk(), kb=True)
        elif cmd == "help":   self.send(cid, self.tr("help") % self.cfg.host, kb=True)
        elif cmd == "lang":
            # Cycle EN -> RU -> DE and reply with the full help: Telegram cannot re-render earlier
            # messages, so a bare confirmation would look like nothing happened.
            order = ["en", "ru", "de"]
            self.lang = order[(order.index(self.lang) + 1) % len(order)] if self.lang in order else "en"
            self.save_state(self.state)
            self.send(cid, self.tr("m_lang") + "\n\n" + self.tr("help") % self.cfg.host, kb=True)
        else: self.send(cid, self.tr("m_unknown"), kb=True)

    def handle(self, msg):
        cid = str(msg.get("chat", {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"): return
        cmd = text.split()[0].lstrip("/").split("@")[0].lower()
        if cmd in ("id", "start"):
            self.send(cid, self.tr("m_chatid")
                      % (cid, self.tr("help") % self.cfg.host if cid in self.cfg.allowed else ""), kb=cid in self.cfg.allowed); return
        if cid not in self.cfg.allowed:
            self.send(cid, self.tr("m_unauth") % cid); return
        self.dispatch(cid, cmd)

    def handle_callback(self, cb):
        # нажатие инлайн-кнопки: гасим "часики" и выполняем как команду
        # message может отсутствовать (недоступное сообщение) — fallback на id нажавшего
        cid = str((cb.get("message") or {}).get("chat", {}).get("id", "")
                  or cb.get("from", {}).get("id", ""))
        t0 = time.time()
        r = self.tg("answerCallbackQuery", {"callback_query_id": cb.get("id", "")}, timeout=15)
        sys.stderr.write("callback acknowledged=%s took=%.1fs\n"
                         % ((r or {}).get("ok"), time.time() - t0))
        if cid not in self.cfg.allowed:
            self.send(cid, self.tr("m_unauth") % cid); return
        # дебаунс: та же кнопка не чаще раза в 10с — гасит спам при серии нажатий
        # и при пачке протухших колбэков после залипания доставки. Отвечаем ВСЕГДА,
        # даже если "часики" погасить не удалось (Telegram бывает тормозит сам ответ
        # на answerCallbackQuery на 10-15с и потом отдаёт "query is too old").
        data = (cb.get("data") or "").strip().lower()
        key = cid + ":" + data
        now = time.time()
        if now - self.cb_seen.get(key, 0) < 10:
            sys.stderr.write("Ignoring duplicate callback\n")
            return
        self.cb_seen[key] = now
        t1 = time.time()
        self.dispatch(cid, data)
        sys.stderr.write("callback completed in %.1fs\n" % (time.time() - t1))

    def monitor_once(self, st):
        try:
            self.monitor(st)
        except Exception as error:
            sys.stderr.write("monitor: %s\n" % type(error).__name__)

    def run_bot(self):
        if not self.cfg.token:
            sys.stderr.write("BOT_TOKEN is not set\n"); sys.exit(1)
        self.state = st = self.load_state()
        # Restore the language chosen with /lang across restarts; config LANG is the fallback.
        lang = st.get("lang")
        self.lang = lang if lang in ("en", "ru", "de") else self.cfg.default_lang
        offset = st.get("offset", 0)
        self.monitor_once(st)
        self.broadcast(self.tr("m_started") % self.cfg.host)
        last = time.time()
        # net_fail ДОЛЖЕН существовать до первой итерации: он инкрементируется в ветке
        # сетевого сбоя, а обнулялся только в ветках успеха — первый же сбой сразу после
        # старта давал UnboundLocalError и убивал бота (воспроизведено).
        net_fail = 0
        while True:
            # allowed_updates явно: настройка ПЕРСИСТЕНТНА на стороне Telegram —
            # без callback_query в списке нажатия кнопок молча не доставляются
            upd = self.tg("getUpdates", {"offset": offset, "timeout": 20,
                                    "allowed_updates": '["message","edited_message","callback_query"]'},
                     timeout=35)
            if upd and upd.get("ok"):
                for u in upd["result"]:
                    offset = u["update_id"] + 1; st["offset"] = offset
                    m = u.get("message") or u.get("edited_message")
                    if m:
                        try: self.handle(m)
                        except Exception as e: sys.stderr.write("handle: %s\n" % e)
                    cb = u.get("callback_query")
                    if cb:
                        try: self.handle_callback(cb)
                        except Exception as e: sys.stderr.write("callback: %s\n" % e)
                try:
                    self.save_state(st)
                except OSError as error:
                    sys.stderr.write("state save: %s\n" % type(error).__name__)
                net_fail = 0
            elif upd is not None:
                time.sleep(2)  # HTTP-ошибка API (401/409/429): не долбим в busy-loop
                net_fail = 0
            else:
                # upd is None = не-HTTP сбой (DNS, ECONNREFUSED, обрыв). Раньше паузы тут не было
                # вообще: getaddrinfo падает за доли миллисекунды, и цикл крутился ~155 раз в
                # секунду — ядро в полке рядом с боевой нодой плюс флуд в journald.
                net_fail += 1
                time.sleep(min(2 * (2 ** min(net_fail - 1, 4)), 60))  # 2,4,8,16,32,→60 с
            if time.time() - last >= self.cfg.check_interval:
                self.monitor_once(st)
                last = time.time()

    def check_node(self):
        status = self.node.get_status()
        wrong_chain = status.get("wrong_chain") if status else None
        signing = None if wrong_chain else self.node.get_signing()
        limits = signing["limits"] if signing else self.cfg.default_limits()
        report = {"chain_id": self.cfg.chain_id, "host": self.cfg.host,
                  "service_active": self.node.svc_active(), "status": status,
                  "signing": {k:v for k,v in signing.items() if k != "limits"} if signing else None,
                  "slash_window": limits["window"],
                  "delegation": None if wrong_chain else self.node.get_delegation(),
                  "jail_threshold": limits["jail"],
                  "missed_warning": limits["warning"], "missed_critical": limits["critical"],
                  "peers": None if wrong_chain else self.node.get_peers(), "disk": self.node.get_disk()}
        if wrong_chain:
            report["rpc_error"] = {"kind": "chain_id_mismatch", "endpoint": self.node.rpc_endpoint(),
                                   "expected": self.cfg.chain_id, "actual": wrong_chain}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if status and not wrong_chain and signing and report["service_active"] else 1


def main():
    if sys.argv[1:] not in ([], ["--check"]):
        raise SystemExit("Usage: bot.py [--check]")
    settings = Settings.from_file(os.environ.get("GENESISL1_BOT_CONFIG", DEFAULT_PATH))
    if os.environ.get("PREFER_IPV4", "1") != "0":
        prefer_ipv4()
    bot = Bot(settings)
    if sys.argv[1:] == ["--check"]:
        raise SystemExit(bot.check_node())
    if not settings.token:
        raise SystemExit("Configure BOT_TOKEN before starting the bot")
    os.makedirs(os.path.dirname(settings.state_path), mode=0o700, exist_ok=True)
    with open(settings.state_path + ".lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another instance is already using this bot state")
        bot.run_bot()


if __name__ == "__main__":
    main()
