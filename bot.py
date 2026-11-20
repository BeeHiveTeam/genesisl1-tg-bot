#!/usr/bin/env python3
"""GenesisL1 validator Telegram monitoring adapted from BeeHive Provenance bot.

Read-only local CometBFT and ABCI queries, systemd status and disk usage.
Configuration is stored in /etc/genesisl1-tg-bot/config.env.
Runtime state is stored in /var/lib/genesisl1-tg-bot.
"""
import base64
import calendar
import fcntl
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

# --- IPv4 вперёд ---------------------------------------------------------------
# api.telegram.org отдаёт и A, и AAAA. На хосте с глобальным IPv6, у которого маршрут до
# Telegram не работает, getaddrinfo ставит IPv6 первым, а urllib, в отличие от curl, не умеет
# Happy Eyeballs: он перебирает адреса по порядку и на каждом ждёт ПОЛНЫЙ таймаут. Замер на
# нашем хосте: 2 запроса из 20 висли 5.7 и 24.7 с, при IPv4 впереди — 0 из 20. Наружу это
# выглядит как «бот не отвечает»: висит sendMessage, а не обработчик.
# Не фильтруем, а переупорядочиваем — на хосте только с IPv6 список останется прежним.
if os.environ.get("PREFER_IPV4", "1") != "0":
    import socket as _socket
    _orig_getaddrinfo = _socket.getaddrinfo

    def _ipv4_first(*args, **kwargs):
        res = _orig_getaddrinfo(*args, **kwargs)
        return sorted(res, key=lambda r: 0 if r[0] == _socket.AF_INET else 1)

    _socket.getaddrinfo = _ipv4_first

CFG_PATH = os.environ.get("GENESISL1_BOT_CONFIG", "/etc/genesisl1-tg-bot/config.env")
HOST = os.uname().nodename

def load_cfg():
    cfg = {}
    try:
        with open(CFG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1); cfg[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return cfg

CFG = load_cfg()
STATE_PATH = CFG.get("STATE_PATH", "/var/lib/genesisl1-tg-bot/state.json")
SERVICE = CFG.get("SERVICE", "genesisl1.service")
CHAIN_ID = CFG.get("CHAIN_ID", "genesis_29-2")
TOKEN   = CFG.get("BOT_TOKEN", "").strip()
ALLOWED = set(x.strip() for x in CFG.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip())
RPC      = CFG.get("RPC_URL", "http://127.0.0.1:26657").rstrip("/")
VALOPER = CFG.get("VALOPER", "").strip()
VALCONS  = CFG.get("VALCONS", "").strip()
CHECK_INTERVAL = int(CFG.get("CHECK_INTERVAL", "60"))
DISK_WARN_PCT  = int(CFG.get("DISK_WARN_PCT", "85"))
MISSED_WARN_CONFIG = CFG.get("MISSED_WARN", "").strip()
MISSED_WARN = int(MISSED_WARN_CONFIG or "500")
STALL_TICKS    = int(CFG.get("STALL_TICKS", "3"))   # тиков подряд без роста высоты до алерта
MISSED_CRIT_CONFIG = CFG.get("MISSED_CRIT", "").strip()
MISSED_CRIT = int(MISSED_CRIT_CONFIG or "4000")
MISSED_WARN_HYSTERESIS = max(1, int(CFG.get("MISSED_WARN_HYSTERESIS", "50")))
MISSED_WARN_STEP = max(1, int(CFG.get("MISSED_WARN_STEP", "100")))
MISSED_WARN_REMINDER_SEC = max(60, int(CFG.get("MISSED_WARN_REMINDER_SEC", "3600")))
# Populated from current on-chain slashing parameters before signing reports.
SLASH_WINDOW = 0
JAIL_THRESHOLD = 0
# Минимальный интервал между алертами «активно пропускает блоки», сек (см. monitor()).
MISSED_ALERT_MIN_GAP = int(CFG.get("MISSED_ALERT_MIN_GAP", "900"))
# Сколько циклов подряд signing-info должна быть недоступна, прежде чем это станет алертом.
# Не 1: одиночный таймаут RPC — обычное дело и алертом быть не должен.
SIGNING_FAIL_ALERT_TICKS = int(CFG.get("SIGNING_FAIL_ALERT_TICKS", "3"))
PEERS_FAIL_ALERT_TICKS = int(CFG.get("PEERS_FAIL_ALERT_TICKS", "5"))
PEERS_MIN      = int(CFG.get("PEERS_MIN", "3"))
LAG_WARN       = int(CFG.get("BLOCK_LAG_WARN_SEC", "30"))
PENDING_MAX     = int(CFG.get("PENDING_MAX", "50"))      # максимум недоставленных алертов
PENDING_TTL_SEC = int(CFG.get("PENDING_TTL_SEC", "21600"))  # 6ч: позже алерт неактуален
API = "https://api.telegram.org/bot%s/" % TOKEN
SSLCTX = ssl.create_default_context()

# ---------- telegram ----------
def tg(method, params=None, timeout=35):
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(API + method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSLCTX) as r:
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

# ── i18n ──────────────────────────────────────────────────────────────────────
# Russian is the default when LANG is unset in config.env. The 🌐 button and /lang
# switch between en, ru and de at runtime.
_cfg_lang = CFG.get("LANG", "ru").strip().lower()
DEFAULT_LANG = _cfg_lang if _cfg_lang in ("en", "ru", "de") else "en"
_lang = DEFAULT_LANG

T = {
  "b_status": {"en": "📟 Status",    "ru": "📟 Статус",     "de": "📟 Status"},
  "b_val":    {"en": "🛡 Validator", "ru": "🛡 Валидатор",  "de": "🛡 Validator"},
  "b_sync":   {"en": "🔄 Sync",      "ru": "🔄 Синк",       "de": "🔄 Sync"},
  "b_peers":  {"en": "🌐 Peers",     "ru": "🌐 Пиры",       "de": "🌐 Peers"},
  "b_disk":   {"en": "💾 Disk",      "ru": "💾 Диск",       "de": "💾 Platte"},
  "b_help":   {"en": "❓ Help",       "ru": "❓ Помощь",     "de": "❓ Hilfe"},
  "b_lang":   {"en": "🌐 EN",        "ru": "🌐 RU",         "de": "🌐 DE"},

  "y_no":     {"en": "✅ no",  "ru": "✅ нет", "de": "✅ nein"},
  "y_yes":    {"en": "🔴 YES", "ru": "🔴 ДА",  "de": "🔴 JA"},
  "svc":      {"en": "Service: ", "ru": "Сервис: ", "de": "Dienst: "},
  "svc_bad":  {"en": "🔴 NOT active", "ru": "🔴 НЕ active", "de": "🔴 NICHT aktiv"},

  "s_nolag":  {"en": "❔ lag not measured", "ru": "❔ lag не измерен", "de": "❔ Lag nicht messbar"},
  "s_behind": {"en": "🔴 BEHIND", "ru": "🔴 ОТСТАЁТ", "de": "🔴 ZURÜCK"},
  "s_ok":     {"en": "✅ in sync", "ru": "✅ в синке", "de": "✅ synchron"},

  "st_net":   {"en": "Network: %s  genesisd %s (comet %s)", "ru": "Сеть: %s  genesisd %s (comet %s)", "de": "Netz: %s  genesisd %s (comet %s)"},
  "st_sync":  {"en": "Sync: %s  block %s  lag %s", "ru": "Синк: %s  блок %s  lag %s", "de": "Sync: %s  Block %s  Lag %s"},
  "st_jail_unk": {"en": "Jailed: ⚠️ NOT CHECKED — signing-info unavailable", "ru": "Jailed: ⚠️ НЕ ПРОВЕРЕНО — signing-info недоступна", "de": "Jailed: ⚠️ NICHT GEPRÜFT — signing-info nicht verfügbar"},
  "st_missed_unk": {"en": "Missed blocks: ⚠️ NOT CHECKED", "ru": "Missed blocks: ⚠️ НЕ ПРОВЕРЕНО", "de": "Missed blocks: ⚠️ NICHT GEPRÜFT"},
  "st_missed": {"en": "Missed blocks: %d / %d (jail threshold >%d)\nVoting power: %s", "ru": "Missed blocks: %d / %d (порог джейла >%d)\nVoting power: %s", "de": "Missed blocks: %d / %d (Jail-Schwelle >%d)\nVoting power: %s"},
  "st_peers": {"en": "Peers: %d", "ru": "Пиры: %d", "de": "Peers: %d"},
  "st_stake": {"en": "Total delegated: %s L1", "ru": "Всего делегировано: %s L1", "de": "Insgesamt delegiert: %s L1"},
  "st_stake_unk": {"en": "Total delegated: ⚠️ unavailable", "ru": "Всего делегировано: ⚠️ данные недоступны", "de": "Insgesamt delegiert: ⚠️ nicht verfügbar"},
  "st_peers_unk": {"en": "Peers: ⚠️ NOT CHECKED (/net_info unavailable)", "ru": "Пиры: ⚠️ НЕ ПРОВЕРЕНО (/net_info недоступен)", "de": "Peers: ⚠️ NICHT GEPRÜFT (/net_info nicht verfügbar)"},
  "st_disk":  {"en": "Disk /: %s %d%% used, %s GB", "ru": "Диск /: %s %d%% занято, %s ГБ", "de": "Platte /: %s %d%% belegt, %s GB"},
  "st_disk_unk": {"en": "Disk /: ⚠️ usage unavailable", "ru": "Диск /: ⚠️ данные о занятом месте недоступны", "de": "Platte /: ⚠️ Belegung nicht verfügbar"},
  "st_unchecked": {"en": "NOT CURRENTLY CHECKED (RPC %s / genesisd / VALCONS)", "ru": "СЕЙЧАС НЕ ПРОВЕРЯЮТСЯ (RPC %s / genesisd / VALCONS)", "de": "DERZEIT NICHT GEPRÜFT (RPC %s / genesisd / VALCONS)"},

  "v_body":   {"en": "Validator %s\nJailed: %s\njailed_until: %s\nTombstoned: %s\n", "ru": "Валидатор %s\nJailed: %s\njailed_until: %s\nTombstoned: %s\n", "de": "Validator %s\nJailed: %s\njailed_until: %s\nTombstoned: %s\n"},
  "v_missed": {"en": "Missed blocks: %d / window %d (jail at >%d)", "ru": "Missed blocks: %d / окно %d (джейл при >%d)", "de": "Missed blocks: %d / Fenster %d (Jail bei >%d)"},
  "v_nosign": {"en": "signing-info unavailable (check VALCONS/node)", "ru": "signing-info недоступна (проверь VALCONS/ноду)", "de": "signing-info nicht verfügbar (VALCONS/Node prüfen)"},
  "sy_body":  {"en": "Sync %s\nblock: %s\nlag: %s\ncatching_up: %s", "ru": "Синк %s\nблок: %s\nlag: %s\ncatching_up: %s", "de": "Sync %s\nBlock: %s\nLag: %s\ncatching_up: %s"},
  "pe_body":  {"en": "Peers: %s", "ru": "Пиры: %s", "de": "Peers: %s"},
  "dk_body":  {"en": "Disk /: %s\n%s", "ru": "Диск /: %s\n%s", "de": "Platte /: %s\n%s"},
  "dk_used":  {"en": "%d%% used, %s GB free", "ru": "%d%% занято, %s ГБ свободно", "de": "%d%% belegt, %s GB frei"},

  "a_jail":   {"en": "🔴🔴 VALIDATOR IS JAILED! jailed_until %s", "ru": "🔴🔴 ВАЛИДАТОР В ДЖЕЙЛЕ! jailed_until %s", "de": "🔴🔴 VALIDATOR IST GEJAILT! jailed_until %s"},
  "a_jail_still": {"en": "🔴🔴 VALIDATOR STILL JAILED", "ru": "🔴🔴 ВАЛИДАТОР ВСЁ ЕЩЁ В ДЖЕЙЛЕ", "de": "🔴🔴 VALIDATOR WEITERHIN GEJAILT"},
  "a_unjail": {"en": "✅ Validator is out of jail", "ru": "✅ Валидатор вышел из джейла", "de": "✅ Validator ist aus dem Jail"},
  "a_tomb":   {"en": "💀 TOMBSTONED — the validator is permanently removed!", "ru": "💀 TOMBSTONED — валидатор перманентно выведен!", "de": "💀 TOMBSTONED — der Validator ist dauerhaft entfernt!"},
  "a_missed": {"en": "🔴 GenesisL1: MISSED BLOCKS = %d (≥%d, close to jail at %d!)", "ru": "🔴 GenesisL1: MISSED BLOCKS = %d (≥%d, близко к джейлу %d!)", "de": "🔴 GenesisL1: MISSED BLOCKS = %d (≥%d, nahe am Jail bei %d!)"},
  "a_missed_warn": {"en": "🟡 GenesisL1: MISSED BLOCKS = %d (≥%d)", "ru": "🟡 GenesisL1: ПРОПУЩЕНО БЛОКОВ = %d (≥%d)", "de": "🟡 GenesisL1: VERPASSTE BLÖCKE = %d (≥%d)"},
  "a_missed_still": {"en": "🔴 GenesisL1: MISSED BLOCKS still %d (jail threshold %d)", "ru": "🔴 GenesisL1: MISSED BLOCKS всё ещё %d (порог джейла %d)", "de": "🔴 GenesisL1: MISSED BLOCKS weiterhin %d (Jail-Schwelle %d)"},
  "a_missing": {"en": "🟡 Actively missing blocks: +%d this cycle (%d total)", "ru": "🟡 Активно пропускает блоки: +%d за цикл (всего %d)", "de": "🟡 Verpasst aktiv Blöcke: +%d in diesem Zyklus (%d gesamt)"},
  "a_vp_zero": {"en": "🔴 VOTING POWER = 0 (dropped from the active set / jailed?)", "ru": "🔴 VOTING POWER = 0 (выпали из активного сета / джейл?)", "de": "🔴 VOTING POWER = 0 (aus dem aktiven Set gefallen / gejailt?)"},
  "a_vp_zero_still": {"en": "🔴 VOTING POWER still 0 (outside the active set?)", "ru": "🔴 VOTING POWER всё ещё 0 (вне активного сета?)", "de": "🔴 VOTING POWER weiterhin 0 (außerhalb des aktiven Sets?)"},
  "a_vp_drop": {"en": "⚠️ VOTING POWER dropped: %s → %s", "ru": "⚠️ VOTING POWER упал: %s → %s", "de": "⚠️ VOTING POWER gefallen: %s → %s"},
  "a_vp_ok":  {"en": "✅ VOTING POWER restored: %s", "ru": "✅ VOTING POWER восстановлен: %s", "de": "✅ VOTING POWER wiederhergestellt: %s"},
  "a_svc_down": {"en": "🔴 SERVICE DOWN: genesisd is not active!", "ru": "🔴 СЕРВИС УПАЛ: genesisd неактивен!", "de": "🔴 DIENST AUSGEFALLEN: genesisd ist nicht aktiv!"},
  "a_svc_still": {"en": "🔴 genesisd still down", "ru": "🔴 genesisd всё ещё down", "de": "🔴 genesisd weiterhin ausgefallen"},
  "a_svc_up": {"en": "✅ genesisd is active again", "ru": "✅ genesisd снова active", "de": "✅ genesisd ist wieder aktiv"},
  "a_restart": {"en": "🔄 RESTART: genesisd restarted\nstarted: %s", "ru": "🔄 РЕСТАРТ: genesisd перезапущен\nстарт: %s", "de": "🔄 NEUSTART: genesisd neu gestartet\nStart: %s"},
  "a_rpc_down": {"en": "🔴 RPC %s not responding", "ru": "🔴 RPC %s не отвечает", "de": "🔴 RPC %s antwortet nicht"},
  "a_rpc_still": {"en": "🔴 RPC %s still not responding", "ru": "🔴 RPC %s всё ещё не отвечает", "de": "🔴 RPC %s antwortet weiterhin nicht"},
  "a_rpc_up": {"en": "✅ RPC %s responding again", "ru": "✅ RPC %s снова отвечает", "de": "✅ RPC %s antwortet wieder"},
  "rpc_wrong_chain": {"en": "🔴 RPC %s reports chain %s (expected %s)", "ru": "🔴 RPC %s вернул сеть %s (ожидалась %s)", "de": "🔴 RPC %s meldet das Netz %s (erwartet: %s)"},
  "rpc_chain_ok": {"en": "✅ RPC %s reports the expected chain %s again", "ru": "✅ RPC %s снова возвращает нужную сеть %s", "de": "✅ RPC %s meldet wieder das erwartete Netz %s"},
  "a_stuck":  {"en": "🔴 BLOCKS NOT ADVANCING: height stuck at %s (~%d min)", "ru": "🔴 БЛОКИ НЕ РАСТУТ: высота застряла на %s (~%d мин)", "de": "🔴 BLÖCKE STEHEN: Höhe bei %s festgefahren (~%d Min)"},
  "a_stuck_still": {"en": "🔴 Blocks still not advancing (%s)", "ru": "🔴 Блоки всё ещё не растут (%s)", "de": "🔴 Blöcke wachsen weiterhin nicht (%s)"},
  "a_stuck_ok": {"en": "✅ Blocks advancing again: %s", "ru": "✅ Блоки снова растут: %s", "de": "✅ Blöcke wachsen wieder: %s"},
  "a_catching": {"en": "🟡 NODE BEHIND: catching_up=true (block %s)", "ru": "🟡 НОДА ОТСТАЁТ: catching_up=true (блок %s)", "de": "🟡 NODE ZURÜCK: catching_up=true (Block %s)"},
  "a_sync_ok": {"en": "✅ Sync recovered (block %s)", "ru": "✅ Синк восстановлен (блок %s)", "de": "✅ Sync wiederhergestellt (Block %s)"},
  "a_lag":    {"en": "🟡 Block lag: %ds", "ru": "🟡 Отставание блока: lag %ds", "de": "🟡 Block-Rückstand: %ds"},
  "a_lag_ok": {"en": "✅ lag is normal (%ds)", "ru": "✅ lag в норме (%ds)", "de": "✅ Lag normal (%ds)"},
  "a_peers_low": {"en": "🟡 Few peers: %d (<%d)", "ru": "🟡 Мало пиров: %d (<%d)", "de": "🟡 Wenige Peers: %d (<%d)"},
  "a_peers_ok": {"en": "✅ Peers normal: %d", "ru": "✅ Пиры в норме: %d", "de": "✅ Peers normal: %d"},
  "a_disk":   {"en": "🟡 DISK /: %d%% (%s GB free)", "ru": "🟡 ДИСК /: %d%% (%s ГБ свободно)", "de": "🟡 PLATTE /: %d%% (%s GB frei)"},
  "a_disk_ok": {"en": "✅ Disk ok: %d%%", "ru": "✅ Диск ок: %d%%", "de": "✅ Platte ok: %d%%"},
  "a_nosign": {"en": "⚠️ signing-info unavailable for %d cycles — jail and missed blocks ", "ru": "⚠️ signing-info недоступна %d циклов подряд — джейл и пропущенные блоки ", "de": "⚠️ signing-info seit %d Zyklen nicht verfügbar — Jail und verpasste Blöcke "},
  "a_signing_ok": {"en": "✅ signing-info available again", "ru": "✅ signing-info снова доступна", "de": "✅ signing-info wieder verfügbar"},
  "a_nonet":  {"en": "⚠️ /net_info unavailable for %d cycles — peer count is NOT being checked", "ru": "⚠️ /net_info недоступен %d циклов — число пиров НЕ проверяется", "de": "⚠️ /net_info seit %d Zyklen nicht verfügbar — Peer-Anzahl wird NICHT geprüft"},
  "a_repeat": {"en": "(repeat) ", "ru": "(повтор) ", "de": "(Wiederholung) "},
  "a_reminder": {"en": "⏰ REMINDER (repeats every 30 min):\n", "ru": "⏰ НАПОМИНАНИЕ (повтор каждые 30 мин):\n", "de": "⏰ ERINNERUNG (alle 30 Min):\n"},

  "m_started": {"en": "🟢 genesisl1-tg-bot started on %s. /help for commands.", "ru": "🟢 genesisl1-tg-bot запущен на %s. /help — команды.", "de": "🟢 genesisl1-tg-bot gestartet auf %s. /help für Befehle."},
  "m_unauth": {"en": "⛔ Not authorised. Your chat_id: %s", "ru": "⛔ Не авторизован. Твой chat_id: %s", "de": "⛔ Nicht autorisiert. Deine chat_id: %s"},
  "m_chatid": {"en": "chat_id: %s\nAdd it to ALLOWED_CHAT_IDS and restart the service.\n\n%s", "ru": "chat_id: %s\nДобавь в ALLOWED_CHAT_IDS и перезапусти сервис.\n\n%s", "de": "chat_id: %s\nTrage sie in ALLOWED_CHAT_IDS ein und starte den Dienst neu.\n\n%s"},
  "m_unknown": {"en": "Unknown command. /help", "ru": "Неизвестная команда. /help", "de": "Unbekannter Befehl. /help"},
  "m_lang":   {"en": "Language: English. Tap 🌐 or /lang to cycle.", "ru": "Язык: русский. Нажмите 🌐 или /lang для смены.", "de": "Sprache: Deutsch. 🌐 oder /lang zum Wechseln."},
  "help":     {"en": ("GenesisL1 validator bot — %s\n\n"
                      "/status — overview\n/val — validator (jailed/missed/power)\n"
                      "/sync — sync\n/peers — peers\n/disk — disk\n"
                      "/id — chat id\n/lang — switch language\n/help — this message"),
               "ru": ("GenesisL1 validator bot — %s\n\n"
                      "/status — общая сводка\n/val — валидатор (jailed/missed/power)\n"
                      "/sync — синхронизация\n/peers — пиры\n/disk — диск\n"
                      "/id — chat id\n/lang — сменить язык\n/help — помощь"),
               "de": ("GenesisL1 validator bot — %s\n\n"
                      "/status — Übersicht\n/val — Validator (jailed/missed/power)\n"
                      "/sync — Sync\n/peers — Peers\n/disk — Platte\n"
                      "/id — chat id\n/lang — Sprache wechseln\n/help — diese Nachricht")},
}


def tr(key):
    return T[key].get(_lang, T[key]["en"])


def keyboard():
    """Inline keyboard in the current language, with a language toggle."""
    return json.dumps({"inline_keyboard": [
        [{"text": tr("b_status"), "callback_data": "status"}, {"text": tr("b_val"), "callback_data": "val"}],
        [{"text": tr("b_sync"), "callback_data": "sync"}, {"text": tr("b_peers"), "callback_data": "peers"}],
        [{"text": tr("b_disk"), "callback_data": "disk"}, {"text": tr("b_help"), "callback_data": "help"}],
        [{"text": tr("b_lang"), "callback_data": "lang"}],
    ]})

def send(chat_id, text, kb=False, timeout=35):
    # kb=True — прикрепить инлайн-кнопки к последней части
    ok = True
    chunks = [text[i:i+3900] for i in range(0, len(text), 3900)]
    for n, chunk in enumerate(chunks):
        params = {"chat_id": chat_id, "text": chunk,
                  "disable_web_page_preview": "true"}
        if kb and n == len(chunks) - 1:
            params["reply_markup"] = keyboard()
        r = tg("sendMessage", params, timeout=timeout)
        if not (r and r.get("ok")):
            ok = False
    return ok

def broadcast(text, st=None):
    for cid in ALLOWED:
        if send(cid, text, kb=True):
            sys.stderr.write("Telegram alert delivered\n")
        elif st is not None:
            st.setdefault("pending_alerts", []).append({"cid": cid, "text": text, "ts": time.time()})
            sys.stderr.write("Telegram alert queued for retry\n")

def retry_pending(st):
    now = time.time()
    pending = [alert for alert in st.get("pending_alerts", [])
               if now - float(alert.get("ts", 0)) <= PENDING_TTL_SEC][-PENDING_MAX:]
    remaining = []
    for alert in pending[:2]:
        if not send(alert["cid"], tr("a_repeat") + alert["text"], kb=True, timeout=5):
            remaining.append(alert)
    st["pending_alerts"] = remaining + pending[2:]

# ---------- helpers ----------
def sh_args(argv, timeout=15):
    """Без shell: список аргументов. Значения конфигурации (VALCONS, VALOPER, RPC) попадали
    в строку, исполняемую shell'ом от root — точка с запятой или подстановка в config.env
    выполнялась бы как команда. Файл наш, но это ровно та ошибка, которую незачем оставлять
    в публичном репозитории."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return False, ""
    return r.returncode == 0, r.stdout.strip()

def http_get(path, timeout=6):
    try:
        with urllib.request.urlopen(RPC + path, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None

def app_version():
    response = http_get("/abci_info")
    try:
        return response["result"]["response"]["version"]
    except (KeyError, TypeError):
        return "?"

def svc_active():
    ok, output = sh_args(["systemctl", "is-active", SERVICE])
    return ok and output == "active"
def svc_start():
    return sh_args(["systemctl", "show", SERVICE,
                    "-p", "ExecMainStartTimestamp", "--value"])[1]

def rpc_endpoint():
    """Show the configured host and port, omitting credentials, paths and query parameters."""
    try:
        endpoint = urllib.parse.urlsplit(RPC)
        host = endpoint.hostname
        if not host or endpoint.scheme not in ("http", "https"):
            return "?"
        port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
        return "%s:%d" % ("[" + host + "]" if ":" in host else host, port)
    except ValueError:
        return "?"

def rpc_problem(status):
    if status and "wrong_chain" in status:
        return tr("rpc_wrong_chain") % (rpc_endpoint(), status["wrong_chain"], CHAIN_ID)
    return tr("a_rpc_down") % rpc_endpoint()

def get_status():
    d = http_get("/status")
    r = d.get("result") if isinstance(d, dict) else None
    if not isinstance(r, dict):
        return None
    ni = r.get("node_info", {})
    network = ni.get("network") if isinstance(ni, dict) else None
    if not isinstance(network, str) or not network:
        return None
    if network != CHAIN_ID:
        return {"wrong_chain": network}
    si = r.get("sync_info") or {}
    vi = r.get("validator_info") or {}
    if not isinstance(si, dict) or not isinstance(vi, dict):
        return None
    h = si.get("latest_block_height")
    ts = si.get("latest_block_time", "")
    lag = None
    # latest_block_time приходит в UTC. `time.mktime() - time.timezone` трактует его как
    # локальное время и в зоне с DST применяет altzone, вычитая при этом timezone — ошибка
    # до часа: либо ложное «Отставание блока», либо замаскированный реальный lag.
    # Здесь хост в UTC, поэтому раньше совпадало случайно.
    bt = _parse_iso_utc(ts)
    if bt is not None:
        lag = int(time.time()) - int(bt)
    # voting_power: None, если поля validator_info нет вовсе (ранний старт comet).
    # `int(x or 0)` делал это неотличимым от «выпали из активного сета».
    vp_raw = vi.get("voting_power")
    return {"height": int(h) if str(h).isdigit() else None,
            "catching_up": bool(si.get("catching_up")),
            "vp": (int(vp_raw) if str(vp_raw).lstrip("-").isdigit() else None),
            "version": ni.get("version", "?"),
            "network": ni.get("network", "?"),
            "lag": lag}

def _parse_iso_utc(s):
    """ISO-8601 → epoch (UTC). None если не разобрать."""
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})", s)
    if not m:
        return None
    try:
        return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))
    except Exception:
        return None

def get_signing():
    global SLASH_WINDOW, JAIL_THRESHOLD, MISSED_WARN, MISSED_CRIT
    if not VALCONS or not VALOPER:
        return None
    try:
        signing_response = abci_query("/cosmos.slashing.v1beta1.Query/SigningInfo", VALCONS)
        validator_response = abci_query("/cosmos.staking.v1beta1.Query/Validator", VALOPER)
        params_response = abci_query("/cosmos.slashing.v1beta1.Query/Params")
        signing = protobuf_fields(signing_response[1])
        validator = protobuf_fields(validator_response[1])
        params = protobuf_fields(params_response[1])
        if signing[1].decode() != VALCONS or validator[1].decode() != VALOPER:
            return None
        window = params[1]
        minimum = Decimal(params[2].decode()) / Decimal(10**18)
        if window <= 0 or not Decimal(0) <= minimum <= Decimal(1):
            return None
        with localcontext() as context:
            context.prec = 80
            required = int((minimum * window).to_integral_value(rounding=ROUND_HALF_EVEN))
        allowed_misses = window - required
        warning = int(MISSED_WARN_CONFIG) if MISSED_WARN_CONFIG else max(1, allowed_misses // 10)
        critical = int(MISSED_CRIT_CONFIG) if MISSED_CRIT_CONFIG else max(warning, allowed_misses * 4 // 5)
        if not 1 <= warning <= critical <= allowed_misses + 1:
            raise ValueError("Missed-block thresholds exceed the current slashing budget")
        SLASH_WINDOW = window
        JAIL_THRESHOLD = allowed_misses
        MISSED_WARN = warning
        MISSED_CRIT = critical
        jailed_timestamp = protobuf_fields(signing.get(4, b""))
        seconds = jailed_timestamp.get(1, 0)
        if seconds >= 1 << 63:
            seconds -= 1 << 64
        jailed_until = datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
        return {"missed": signing.get(6, 0),
                "jailed": bool(validator.get(3, 0)),
                "jailed_until": jailed_until,
                "tombstoned": bool(signing.get(5, 0))}
    except Exception as error:
        sys.stderr.write("GenesisL1 signing query failed: %s\n" % error)
        return None

def get_peers():
    d = http_get("/net_info")
    try:
        return int(d["result"]["n_peers"])
    except Exception:
        return None

def get_delegation():
    """The validator's full bonded token balance, including self-delegation.

    Read the SDK token amount (el1), not rounded CometBFT voting power or
    delegator shares. Keep integer arithmetic for all 18 decimal places.
    """
    if not VALOPER:
        return None
    try:
        response = abci_query("/cosmos.staking.v1beta1.Query/Validator", VALOPER)
        validator = protobuf_fields(response[1])
        if validator[1].decode() != VALOPER:
            raise ValueError("Unexpected validator address")
        raw = validator[5].decode("ascii")
        if not re.fullmatch(r"[0-9]+", raw):
            raise ValueError("Invalid staking token balance")
        whole, fraction = divmod(int(raw), 10**18)
        digits = ("%018d" % fraction).rstrip("0")
        amount = format(whole, ",").replace(",", " ")
        if digits:
            amount += "." + digits
        return {"tokens_el1": raw, "tokens_L1_formatted": amount}
    except Exception as error:
        sys.stderr.write("GenesisL1 delegation query failed: %s\n" % type(error).__name__)
        return None

def fmt_delegation():
    delegation = get_delegation()
    return (tr("st_stake") % delegation["tokens_L1_formatted"]
            if delegation is not None else tr("st_stake_unk"))

def get_disk():
    try:
        usage = shutil.disk_usage("/")
        available_total = usage.used + usage.free
        if available_total > 0:
            return {"pct": round(100 * usage.used / available_total),
                    "avail_gb": round(usage.free / 1024**3, 1)}
    except OSError:
        pass
    return {"pct": None, "avail_gb": None}

def disk_summary():
    disk = get_disk()
    if disk["pct"] is None:
        return tr("st_disk_unk")
    return tr("st_disk") % ("✅" if disk["pct"] < DISK_WARN_PCT else "🟡",
                            disk["pct"], disk["avail_gb"])

# ---------- reports ----------
def sync_symbol(s):
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
        return tr("s_nolag")
    if s["lag"] > LAG_WARN:
        return tr("s_behind")
    if s["catching_up"]:
        return "🟡 catching_up"
    return tr("s_ok")


def fmt_status():
    L = ["🔷 GenesisL1 — %s" % HOST]
    L.append(tr("svc") + ("✅ active" if svc_active() else tr("svc_bad")))
    s = get_status()
    if s and "wrong_chain" in s:
        return "\n".join(L + [rpc_problem(s), disk_summary()])
    if s:
        L.append(tr("st_net") % (s["network"], app_version(), s["version"]))
        L.append(tr("st_sync") % (sync_symbol(s), s["height"],
                 "?" if s["lag"] is None else "%ds" % s["lag"]))
        L.append("Voting power: %s" % s["vp"])
    else:
        L.append(rpc_problem(s))
    L.append(fmt_delegation())
    sg = get_signing()
    if sg:
        jl = tr("y_yes") if sg["jailed"] else tr("y_no")
        tb = " 💀tombstoned" if sg["tombstoned"] else ""
        L.append("Jailed: %s%s" % (jl, tb))
        L.append(tr("v_missed") % (sg["missed"], SLASH_WINDOW, JAIL_THRESHOLD))
    else:
        # Раньше эти строки просто ПРОПАДАЛИ из сводки. Отчёт выглядел здоровым — без единого
        # признака, что джейл и пропущенные блоки не проверялись вообще: не отвечает RPC, нет
        # genesisd, не задан VALCONS. Молчание читается как «всё хорошо», а это худший из
        # возможных ответов для проверки, ради которой бот и существует.
        L.append(tr("st_jail_unk"))
        L.append(tr("st_missed_unk"))
    p = get_peers()
    L.append(tr("st_peers") % p if p is not None else tr("st_peers_unk"))
    L.append(disk_summary())
    return "\n".join(L)

def fmt_val():
    s = get_status()
    if s and "wrong_chain" in s:
        return rpc_problem(s)
    sg = get_signing()
    if not sg:
        lines = [tr("v_nosign"), fmt_delegation()]
        if s is None:
            lines.insert(0, rpc_problem(s))
        return "\n".join(lines)
    return ((tr("v_body") + tr("st_missed"))
            % (CHAIN_ID, tr("y_yes") if sg["jailed"] else tr("y_no"), sg["jailed_until"],
               tr("y_yes") if sg["tombstoned"] else tr("y_no"), sg["missed"], SLASH_WINDOW, JAIL_THRESHOLD,
               s["vp"] if s else "?")) + "\n" + fmt_delegation()

def fmt_sync():
    s = get_status()
    if not s or "wrong_chain" in s: return rpc_problem(s)
    return (tr("sy_body")
            % (sync_symbol(s),
               s["height"], "?" if s["lag"] is None else "%ds" % s["lag"], s["catching_up"]))

def fmt_peers():
    s = get_status()
    if s and "wrong_chain" in s:
        return rpc_problem(s)
    p = get_peers(); return tr("pe_body") % ("?" if p is None else p)

def fmt_disk():
    d = get_disk()
    if d["pct"] is None:
        return tr("st_disk_unk")
    ok, detail = sh_args(["df", "-h", "/"])
    return (tr("dk_body") % (tr("dk_used") % (d["pct"], d["avail_gb"]),
                             detail.splitlines()[-1] if ok and detail else "")).rstrip()

# ---------- state & alerts ----------
def load_state():
    try:
        with open(STATE_PATH) as f: return json.load(f)
    except Exception: return {}

def save_state(st):
    tmp = STATE_PATH + ".tmp"
    st["lang"] = _lang
    with open(tmp, "w") as f: json.dump(st, f)
    os.replace(tmp, STATE_PATH)

def missed_warning_due(st, missed, previous, now):
    """Latch a warning across boundary noise without hiding growth or critical alerts."""
    active = bool(st.get("missed_warn_active", previous is not None and previous >= MISSED_WARN))
    last_at = st.get("missed_warn_last_ts")
    last_level = st.get("missed_warn_last_level")
    if last_at is None:
        last_at = now
    if last_level is None:
        last_level = previous if previous is not None else missed
    st["missed_warn_last_ts"] = last_at
    st["missed_warn_last_level"] = last_level
    # A real recovery rearms immediately; one expired entry at 499 does not.
    if missed <= max(0, MISSED_WARN - MISSED_WARN_HYSTERESIS):
        active = False
    due = False
    if missed >= MISSED_CRIT:
        active = True
        st["missed_warn_last_ts"] = now
        st["missed_warn_last_level"] = missed
    elif missed >= MISSED_WARN:
        due = (not active or missed >= last_level + MISSED_WARN_STEP
               or now - last_at >= MISSED_WARN_REMINDER_SEC)
        active = True
        if due:
            st["missed_warn_last_ts"] = now
            st["missed_warn_last_level"] = missed
    st["missed_warn_active"] = active
    return due

def monitor(st):
    A = []
    # service
    active = svc_active(); start = svc_start()
    if st.get("active") is True and not active: A.append(tr("a_svc_down"))
    elif st.get("active") is False and active:  A.append(tr("a_svc_up"))
    if st.get("start") and start and start != st["start"] and active:
        A.append(tr("a_restart") % start)
    st["active"], st["start"] = active, start
    # rpc / sync
    s = get_status()
    rpc_dead = s is None
    # An outage cannot establish that a previously observed foreign endpoint has been corrected.
    wrong_chain = s.get("wrong_chain") if s else st.get("wrong_chain")
    if rpc_dead and not st.get("rpc_dead"):
        A.append(rpc_problem(s))
    elif s and wrong_chain and wrong_chain != st.get("wrong_chain"):
        A.append(rpc_problem(s))
    elif s and not wrong_chain:
        if st.get("wrong_chain"):
            A.append(tr("rpc_chain_ok") % (rpc_endpoint(), CHAIN_ID))
        elif st.get("rpc_dead"):
            A.append(tr("a_rpc_up") % rpc_endpoint())
    st["rpc_dead"] = rpc_dead
    st["wrong_chain"] = wrong_chain
    sg = None if wrong_chain else get_signing()
    tombstoned = sg["tombstoned"] if sg else bool(st.get("tombstoned"))
    if s and not wrong_chain:
        if s["catching_up"] and not st.get("catching_up"):
            A.append(tr("a_catching") % s["height"])
        elif not s["catching_up"] and st.get("catching_up"):
            A.append(tr("a_sync_ok") % s["height"])
        st["catching_up"] = s["catching_up"]
        if s["lag"] is not None:
            behind = s["lag"] > LAG_WARN
            if behind and not st.get("behind"): A.append(tr("a_lag") % s["lag"])
            elif not behind and st.get("behind"): A.append(tr("a_lag_ok") % s["lag"])
            st["behind"] = behind
        # block stall (height not advancing) — STALL_TICKS тиков подряд, не с одного
        # замера: одиночное совпадение высот ложнит при рестарте бота (первый тик
        # нового процесса стартует через секунды после последнего тика старого)
        ph = st.get("height")
        if ph is not None and s["height"] == ph:
            st["stall_ticks"] = st.get("stall_ticks", 0) + 1
            if st["stall_ticks"] >= STALL_TICKS and not st.get("stalled"):
                A.append(tr("a_stuck")
                         % (s["height"], st["stall_ticks"] * CHECK_INTERVAL // 60))
                st["stalled"] = True
        elif s["height"] is None:
            # Высоты нет — CometBFT ещё поднимается или /status отдал неполный ответ. Раньше
            # эта ветка сливалась с «высота растёт»: бот слал «✅ Блоки снова растут: None» и
            # сбрасывал stalled по ОТСУТСТВУЮЩИМ данным, то есть снимал реальный алерт,
            # ничего не измерив. Состояние не трогаем.
            pass
        else:
            if st.get("stalled"):
                A.append(tr("a_stuck_ok") % s["height"])
            st["stall_ticks"] = 0
            st["stalled"] = False
        if s["height"] is not None:
            st["height"] = s["height"]
        # voting power. vp is None = поля validator_info в /status нет (ранний старт comet):
        # раньше это давало 0 и было неотличимо от «выпали из активного сета» — ложный
        # алерт на здоровой ноде. И наоборот: при РЕАЛЬНОМ выпадении алерт уходил один раз
        # (pvp становился 0 и falsy), повторов не было, возврат power не сообщался.
        pvp = st.get("vp")
        vp = s["vp"]
        if vp is not None:
            if not tombstoned:
                if pvp and vp and vp < pvp * 0.9:
                    A.append(tr("a_vp_drop") % (pvp, vp))
                if vp == 0 and pvp != 0:
                    A.append(tr("a_vp_zero"))
                elif vp > 0 and pvp == 0:
                    A.append(tr("a_vp_ok") % vp)
            st["vp"] = vp
    # validator signing-info
    if sg:
        if tombstoned and not st.get("tombstoned"):
            A.append(tr("a_tomb"))
        st["tombstoned"] = tombstoned
        if tombstoned:
            st["jailed"], st["missed"] = sg["jailed"], sg["missed"]
        had_signing_alert = st.get("signing_alerted", (st.get("signing_fail_ticks") or 0) >= SIGNING_FAIL_ALERT_TICKS)
        if had_signing_alert and not tombstoned:
            A.append(tr("a_signing_ok"))
        st["signing_fail_ticks"] = 0
        st["signing_alerted"] = False
    if sg and not tombstoned:
        if sg["jailed"] and not st.get("jailed"):
            A.append(tr("a_jail") % sg["jailed_until"])
        elif not sg["jailed"] and st.get("jailed"):
            A.append(tr("a_unjail"))
        st["jailed"] = sg["jailed"]
        m = sg["missed"]; pm = st.get("missed")
        warn_due = missed_warning_due(st, m, pm, time.time())
        # Critical escalation is independent of the yellow-warning latch.
        if m >= MISSED_CRIT and (pm is None or pm < MISSED_CRIT):
            A.append(tr("a_missed") % (m, MISSED_CRIT, JAIL_THRESHOLD))
        elif warn_due:
            A.append(tr("a_missed_warn") % (m, MISSED_WARN))
        # Активный пропуск блоков. Рейт-лимит обязателен: при блоке ~6с валидатор, который не
        # подписывает, набирает ~10 промахов в минуту, и это условие срабатывало КАЖДЫЙ тик —
        # 60 сообщений в час, которыми затирается всё остальное, включая джейл-алерт.
        if pm is not None and m - pm >= 10:
            now = time.time()
            if now - float(st.get("missed_rate_alert_ts") or 0) >= MISSED_ALERT_MIN_GAP:
                A.append(tr("a_missing") % (m - pm, m))
                st["missed_rate_alert_ts"] = now
        st["missed"] = m
    elif not sg and not wrong_chain and not tombstoned:
        # Ветки else тут не было вовсе: при недоступной signing-info проверка джейла просто
        # переставала выполняться, молча и навсегда. Бот продолжал слать бодрые сводки, а
        # единственная проверка, ради которой он существует, не работала. Отказ мониторинга
        # обязан быть слышен — иначе он неотличим от «всё хорошо».
        fails = int(st.get("signing_fail_ticks") or 0) + 1
        st["signing_fail_ticks"] = fails
        if fails == SIGNING_FAIL_ALERT_TICKS:
            A.append((tr("a_nosign") + tr("st_unchecked")) % (fails, rpc_endpoint()))
            st["signing_alerted"] = True
    # peers
    p = None if wrong_chain else get_peers()
    if p is None and not wrong_chain:
        # Тот же класс, что PB-2, ставки ниже: /net_info недоступен → проверка пиров молча
        # пропускается. Раз подряд — не событие, но постоянная слепота должна быть слышна.
        _pf = int(st.get("peers_fail_ticks") or 0) + 1
        st["peers_fail_ticks"] = _pf
        if _pf == PEERS_FAIL_ALERT_TICKS:
            A.append(tr("a_nonet") % _pf)
    elif p is not None:
        st["peers_fail_ticks"] = 0
    if p is not None:
        low = p < PEERS_MIN
        if low and not st.get("peers_low"): A.append(tr("a_peers_low") % (p, PEERS_MIN))
        elif not low and st.get("peers_low"): A.append(tr("a_peers_ok") % p)
        st["peers_low"] = low
    # disk
    d = get_disk()
    if d["pct"] is not None:
        warn = d["pct"] >= DISK_WARN_PCT
        if warn and not st.get("disk_warn"): A.append(tr("a_disk") % (d["pct"], d["avail_gb"]))
        elif not warn and st.get("disk_warn"): A.append(tr("a_disk_ok") % d["pct"])
        st["disk_warn"] = warn
    # РЕ-АЛЕРТ критических состояний каждые 30 мин (урок инцидента 2026-07-19)
    now = time.time()
    crit = []
    if not tombstoned and not wrong_chain:
        if st.get("jailed"): crit.append(tr("a_jail_still"))
        if (st.get("missed") or 0) >= MISSED_CRIT:
            crit.append(tr("a_missed_still") % (st["missed"], JAIL_THRESHOLD))
        if st.get("vp") == 0: crit.append(tr("a_vp_zero_still"))
    if not wrong_chain and st.get("stalled"): crit.append(tr("a_stuck_still") % st.get("height"))
    if rpc_dead: crit.append(tr("a_rpc_still") % rpc_endpoint())
    if st.get("active") is False: crit.append(tr("a_svc_still"))
    if crit:
        if now - st.get("last_realert", 0) >= 1800:
            A.append(tr("a_reminder") + "\n".join(crit))
            st["last_realert"] = now
    else:
        st["last_realert"] = 0
    if A: broadcast("\n\n".join(A), st)
    retry_pending(st)
    save_state(st)

# ---------- commands ----------
CB_SEEN = {}  # (cid:data) -> ts последнего исполнения кнопки, для дебаунса

def dispatch(cid, cmd):
    # общий диспетчер для /команд и нажатий кнопок
    if   cmd == "status": send(cid, fmt_status(), kb=True)
    elif cmd == "val":    send(cid, fmt_val(), kb=True)
    elif cmd == "sync":   send(cid, fmt_sync(), kb=True)
    elif cmd == "peers":  send(cid, fmt_peers(), kb=True)
    elif cmd == "disk":   send(cid, fmt_disk(), kb=True)
    elif cmd == "help":   send(cid, tr("help") % HOST, kb=True)
    elif cmd == "lang":
        # Cycle EN -> RU -> DE and reply with the full help: Telegram cannot re-render earlier
        # messages, so a bare confirmation would look like nothing happened.
        global _lang
        order = ["en", "ru", "de"]
        _lang = order[(order.index(_lang) + 1) % len(order)] if _lang in order else "en"
        st = load_state(); st["lang"] = _lang; save_state(st)
        send(cid, tr("m_lang") + "\n\n" + tr("help") % HOST, kb=True)
    else: send(cid, tr("m_unknown"), kb=True)

def handle(msg):
    cid = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"): return
    cmd = text.split()[0].lstrip("/").split("@")[0].lower()
    if cmd in ("id", "start"):
        send(cid, tr("m_chatid")
                  % (cid, tr("help") % HOST if cid in ALLOWED else ""), kb=cid in ALLOWED); return
    if cid not in ALLOWED:
        send(cid, tr("m_unauth") % cid); return
    dispatch(cid, cmd)

def handle_callback(cb):
    # нажатие инлайн-кнопки: гасим "часики" и выполняем как команду
    # message может отсутствовать (недоступное сообщение) — fallback на id нажавшего
    cid = str((cb.get("message") or {}).get("chat", {}).get("id", "")
              or cb.get("from", {}).get("id", ""))
    t0 = time.time()
    r = tg("answerCallbackQuery", {"callback_query_id": cb.get("id", "")}, timeout=15)
    sys.stderr.write("callback acknowledged=%s took=%.1fs\n"
                     % ((r or {}).get("ok"), time.time() - t0))
    if cid not in ALLOWED:
        send(cid, tr("m_unauth") % cid); return
    # дебаунс: та же кнопка не чаще раза в 10с — гасит спам при серии нажатий
    # и при пачке протухших колбэков после залипания доставки. Отвечаем ВСЕГДА,
    # даже если "часики" погасить не удалось (Telegram бывает тормозит сам ответ
    # на answerCallbackQuery на 10-15с и потом отдаёт "query is too old").
    data = (cb.get("data") or "").strip().lower()
    key = cid + ":" + data
    now = time.time()
    if now - CB_SEEN.get(key, 0) < 10:
        sys.stderr.write("Ignoring duplicate callback\n")
        return
    CB_SEEN[key] = now
    t1 = time.time()
    dispatch(cid, data)
    sys.stderr.write("callback completed in %.1fs\n" % (time.time() - t1))

def monitor_once(st):
    try:
        monitor(st)
    except Exception as error:
        sys.stderr.write("monitor: %s\n" % type(error).__name__)

def run_bot():
    if not TOKEN:
        sys.stderr.write("BOT_TOKEN is not set\n"); sys.exit(1)
    st = load_state()
    # Restore the language chosen with /lang across restarts; config LANG is the fallback.
    global _lang
    _lang = st.get("lang", DEFAULT_LANG)
    offset = st.get("offset", 0)
    monitor_once(st)
    broadcast(tr("m_started") % HOST)
    last = time.time()
    # net_fail ДОЛЖЕН существовать до первой итерации: он инкрементируется в ветке
    # сетевого сбоя, а обнулялся только в ветках успеха — первый же сбой сразу после
    # старта давал UnboundLocalError и убивал бота (воспроизведено).
    net_fail = 0
    while True:
        # allowed_updates явно: настройка ПЕРСИСТЕНТНА на стороне Telegram —
        # без callback_query в списке нажатия кнопок молча не доставляются
        upd = tg("getUpdates", {"offset": offset, "timeout": 20,
                                "allowed_updates": '["message","edited_message","callback_query"]'},
                 timeout=35)
        if upd and upd.get("ok"):
            for u in upd["result"]:
                offset = u["update_id"] + 1; st["offset"] = offset
                m = u.get("message") or u.get("edited_message")
                if m:
                    try: handle(m)
                    except Exception as e: sys.stderr.write("handle: %s\n" % e)
                cb = u.get("callback_query")
                if cb:
                    try: handle_callback(cb)
                    except Exception as e: sys.stderr.write("callback: %s\n" % e)
            try:
                save_state(st)
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
        if time.time() - last >= CHECK_INTERVAL:
            monitor_once(st)
            last = time.time()

def read_varint(data, offset):
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
        shift += 7
    raise ValueError("Invalid protobuf varint")


def protobuf_fields(data):
    fields = {}
    offset = 0
    while offset < len(data):
        tag, offset = read_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("Invalid protobuf field number")
        if wire == 0:
            fields[number], offset = read_varint(data, offset)
        elif wire == 2:
            size, offset = read_varint(data, offset)
            end = offset + size
            if end > len(data):
                raise ValueError("Truncated protobuf field")
            fields[number] = data[offset:end]
            offset = end
        elif wire in (1, 5):
            offset += 8 if wire == 1 else 4
            if offset > len(data):
                raise ValueError("Truncated fixed-width protobuf field")
        else:
            raise ValueError("Unsupported protobuf wire type")
    return fields


def abci_query(path, address=None):
    body = bytearray()
    if address is not None:
        encoded = address.encode()
        body.append(10)
        size = len(encoded)
        while size >= 128:
            body.append((size & 127) | 128)
            size >>= 7
        body.append(size)
        body.extend(encoded)
    query = urllib.parse.urlencode({"path": json.dumps(path), "data": "0x" + body.hex()})
    result = http_get("/abci_query?" + query)
    if not result or "error" in result:
        raise ValueError("Local ABCI query unavailable")
    response = result["result"]["response"]
    if int(response.get("code", 0)) != 0:
        raise ValueError("ABCI returned code %s" % response["code"])
    return protobuf_fields(base64.b64decode(response["value"], validate=True))


def check_node():
    status = get_status()
    wrong_chain = status.get("wrong_chain") if status else None
    signing = None if wrong_chain else get_signing()
    report = {"chain_id": CHAIN_ID, "host": HOST,
              "service_active": svc_active(), "status": status,
              "signing": signing, "slash_window": SLASH_WINDOW,
              "delegation": None if wrong_chain else get_delegation(),
              "jail_threshold": JAIL_THRESHOLD,
              "missed_warning": MISSED_WARN, "missed_critical": MISSED_CRIT,
              "peers": None if wrong_chain else get_peers(), "disk": get_disk()}
    if wrong_chain:
        report["rpc_error"] = {"kind": "chain_id_mismatch", "endpoint": rpc_endpoint(),
                               "expected": CHAIN_ID, "actual": wrong_chain}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status and not wrong_chain and signing and report["service_active"] else 1


def main():
    if sys.argv[1:] == ["--check"]:
        raise SystemExit(check_node())
    if sys.argv[1:]:
        raise SystemExit("Usage: bot.py [--check]")
    if not TOKEN:
        raise SystemExit("Configure BOT_TOKEN before starting the bot")
    os.makedirs(os.path.dirname(STATE_PATH), mode=0o700, exist_ok=True)
    with open(STATE_PATH + ".lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another instance is already using this bot state")
        run_bot()


if __name__ == "__main__":
    main()
