"""Telegram messages in English, Russian and German."""

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


def rpc_problem(tr, endpoint, chain_id, status):
    if status and "wrong_chain" in status:
        return tr("rpc_wrong_chain") % (endpoint, status["wrong_chain"], chain_id)
    return tr("a_rpc_down") % endpoint
