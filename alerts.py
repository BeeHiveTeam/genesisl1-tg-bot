"""Alert transitions. The persisted state is shared across polling cycles and restarts."""
from messages import rpc_problem


def changed(st, key, value):
    previous = st.get(key, False)
    st[key] = value
    return value != previous


class Monitor:
    def __init__(self, settings, node, translate):
        self.cfg = settings
        self.node = node
        self.tr = translate

    def set_validator(self, st):
        identity = [self.cfg.chain_id, self.cfg.valcons, self.cfg.valoper]
        previous = st.get("signing_identity")
        if previous is not None and previous != identity:
            for name in ("vp", "jailed", "tombstoned", "missed", "missed_rate_alert_ts", "missed_warn_active",
                         "missed_warn_last_level", "missed_warn_last_ts", "signing_fail_ticks", "signing_alerted",
                         "signing_limits"):
                st.pop(name, None)
        st["signing_identity"] = identity

    def collect(self, st, now):
        self.set_validator(st)
        alerts = []
        self.service(st, alerts)
        status = self.node.get_status()
        self.rpc(st, alerts, status)
        wrong = st["wrong_chain"]
        signing = None if wrong else self.node.get_signing()
        tombstoned = bool(st.get("tombstoned") or (signing and signing["tombstoned"]))
        if status and not wrong:
            self.sync(st, alerts, status)
            self.voting_power(st, alerts, status["vp"], tombstoned)
        if not wrong:
            self.signing(st, alerts, signing, tombstoned, now)
            self.peers(st, alerts, self.node.get_peers())
        self.disk(st, alerts, self.node.get_disk())
        self.reminders(st, alerts, tombstoned, now)
        return alerts

    def service(self, st, alerts):
        active, start = self.node.svc_active(), self.node.svc_start()
        if st.get("active") is True and not active:
            alerts.append(self.tr("a_svc_down"))
        elif st.get("active") is False and active:
            alerts.append(self.tr("a_svc_up"))
        if active and st.get("start") and start and start != st["start"]:
            alerts.append(self.tr("a_restart") % start)
        st["active"], st["start"] = active, start

    def rpc(self, st, alerts, status):
        endpoint = self.node.rpc_endpoint()
        dead = status is None
        # A failed request cannot clear a previously observed chain mismatch.
        wrong = st.get("wrong_chain") if dead else status.get("wrong_chain")
        if (dead and not st.get("rpc_dead")) or (not dead and wrong and wrong != st.get("wrong_chain")):
            alerts.append(rpc_problem(self.tr, endpoint, self.cfg.chain_id, status))
        elif not dead and not wrong:
            if st.get("wrong_chain"):
                alerts.append(self.tr("rpc_chain_ok") % (endpoint, self.cfg.chain_id))
            elif st.get("rpc_dead"):
                alerts.append(self.tr("a_rpc_up") % endpoint)
        st["rpc_dead"], st["wrong_chain"] = dead, wrong

    def sync(self, st, alerts, status):
        catching = status["catching_up"]
        if changed(st, "catching_up", catching):
            alerts.append(self.tr("a_catching" if catching else "a_sync_ok") % status["height"])
        lag = status["lag"]
        if lag is not None and changed(st, "behind", lag > self.cfg.lag_warn):
            alerts.append(self.tr("a_lag" if st["behind"] else "a_lag_ok") % lag)
        self.height(st, alerts, status["height"])

    def height(self, st, alerts, height):
        if height is None:
            return
        if st.get("height") == height:
            st["stall_ticks"] = st.get("stall_ticks", 0) + 1
            if st["stall_ticks"] >= self.cfg.stall_ticks and not st.get("stalled"):
                minutes = st["stall_ticks"] * self.cfg.check_interval // 60
                alerts.append(self.tr("a_stuck") % (height, minutes))
                st["stalled"] = True
        else:
            if st.get("stalled"):
                alerts.append(self.tr("a_stuck_ok") % height)
            st["stall_ticks"], st["stalled"] = 0, False
        st["height"] = height

    def voting_power(self, st, alerts, power, tombstoned):
        if power is None:
            return
        previous = st.get("vp")
        st["vp"] = power
        if tombstoned:
            return
        if previous and power and power < previous * 0.9:
            alerts.append(self.tr("a_vp_drop") % (previous, power))
        if power == 0 and previous != 0:
            alerts.append(self.tr("a_vp_zero"))
        elif power > 0 and previous == 0:
            alerts.append(self.tr("a_vp_ok") % power)

    def signing(self, st, alerts, signing, tombstoned, now):
        if not signing:
            if not tombstoned:
                fails = st["signing_fail_ticks"] = int(st.get("signing_fail_ticks") or 0) + 1
                if fails == self.cfg.signing_fail_alert_ticks:
                    alerts.append((self.tr("a_nosign") + self.tr("st_unchecked")) % (fails, self.node.rpc_endpoint()))
                    st["signing_alerted"] = True
            return
        # Older state files recorded only the count of failed probes.
        had_alert = st.get("signing_alerted", (st.get("signing_fail_ticks") or 0) >= self.cfg.signing_fail_alert_ticks)
        st["signing_fail_ticks"], st["signing_alerted"] = 0, False
        st["signing_limits"] = signing["limits"]
        if tombstoned and not st.get("tombstoned"):
            alerts.append(self.tr("a_tomb"))
        st["tombstoned"] = tombstoned
        if tombstoned:
            st["jailed"], st["missed"] = signing["jailed"], signing["missed"]
            return
        if had_alert:
            alerts.append(self.tr("a_signing_ok"))
        if changed(st, "jailed", signing["jailed"]):
            alerts.append(self.tr("a_jail") % signing["jailed_until"] if signing["jailed"] else self.tr("a_unjail"))
        self.missed(st, alerts, signing["missed"], signing["limits"], now)

    def missed(self, st, alerts, missed, limits, now):
        previous = st.get("missed")
        warn, crit = limits["warning"], limits["critical"]
        due = self.missed_warning_due(st, missed, previous, now, warn, crit)
        if missed >= crit and (previous is None or previous < crit):
            alerts.append(self.tr("a_missed") % (missed, crit, limits["jail"]))
        elif due:
            alerts.append(self.tr("a_missed_warn") % (missed, warn))
        if previous is not None and missed - previous >= 10:
            if now - float(st.get("missed_rate_alert_ts") or 0) >= self.cfg.missed_alert_min_gap:
                alerts.append(self.tr("a_missing") % (missed - previous, missed))
                st["missed_rate_alert_ts"] = now
        st["missed"] = missed

    def missed_warning_due(self, st, missed, previous, now, warn, crit):
        active = bool(st.get("missed_warn_active", previous is not None and previous >= warn))
        last_at = st.get("missed_warn_last_ts")
        last_level = st.get("missed_warn_last_level")
        if last_at is None:
            last_at = now
        if last_level is None:
            last_level = previous if previous is not None else missed
        due = False
        if missed <= max(0, warn - self.cfg.missed_warn_hysteresis):
            active = False
        if missed >= crit:
            active = True
            last_at, last_level = now, missed
        elif missed >= warn:
            due = (not active or missed >= last_level + self.cfg.missed_warn_step
                   or now - last_at >= self.cfg.missed_warn_reminder_sec)
            active = True
            if due:
                last_at, last_level = now, missed
        st.update(missed_warn_active=active, missed_warn_last_ts=last_at, missed_warn_last_level=last_level)
        return due

    def peers(self, st, alerts, count):
        if count is None:
            fails = st["peers_fail_ticks"] = int(st.get("peers_fail_ticks") or 0) + 1
            if fails == self.cfg.peers_fail_alert_ticks:
                alerts.append(self.tr("a_nonet") % fails)
            return
        st["peers_fail_ticks"] = 0
        low = count < self.cfg.peers_min
        if changed(st, "peers_low", low):
            alerts.append(self.tr("a_peers_low") % (count, self.cfg.peers_min) if low else self.tr("a_peers_ok") % count)

    def disk(self, st, alerts, usage):
        pct = usage["pct"]
        if pct is None:
            return
        warn = pct >= self.cfg.disk_warn_pct
        if changed(st, "disk_warn", warn):
            alerts.append(self.tr("a_disk") % (pct, usage["avail_gb"]) if warn else self.tr("a_disk_ok") % pct)

    def reminders(self, st, alerts, tombstoned, now):
        critical = []
        if not tombstoned and not st["wrong_chain"]:
            limits = st.get("signing_limits") or self.cfg.default_limits()
            if st.get("jailed"):
                critical.append(self.tr("a_jail_still"))
            if (st.get("missed") or 0) >= limits["critical"]:
                critical.append(self.tr("a_missed_still") % (st["missed"], limits["jail"]))
            if st.get("vp") == 0:
                critical.append(self.tr("a_vp_zero_still"))
        if not st["wrong_chain"] and st.get("stalled"):
            critical.append(self.tr("a_stuck_still") % st.get("height"))
        if st["rpc_dead"]:
            critical.append(self.tr("a_rpc_still") % self.node.rpc_endpoint())
        if st.get("active") is False:
            critical.append(self.tr("a_svc_still"))
        if not critical:
            st["last_realert"] = 0
        elif now - st.get("last_realert", 0) >= 1800:
            alerts.append(self.tr("a_reminder") + "\n".join(critical))
            st["last_realert"] = now
