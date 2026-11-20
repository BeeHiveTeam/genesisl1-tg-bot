"""Configuration for one bot instance; the command line entry point loads the file."""
from dataclasses import dataclass, field
from typing import Optional
import os

DEFAULT_PATH = "/etc/genesisl1-tg-bot/config.env"


@dataclass(frozen=True)
class Settings:
    host: str
    state_path: str
    service: str
    chain_id: str
    token: str = field(repr=False)
    allowed: frozenset = field(repr=False)
    rpc: str = field(repr=False)
    valoper: str
    valcons: str
    default_lang: str
    check_interval: int
    disk_warn_pct: int
    missed_warn: Optional[int]
    missed_crit: Optional[int]
    stall_ticks: int
    missed_warn_hysteresis: int
    missed_warn_step: int
    missed_warn_reminder_sec: int
    missed_alert_min_gap: int
    signing_fail_alert_ticks: int
    peers_fail_alert_ticks: int
    peers_min: int
    lag_warn: int
    pending_max: int
    pending_ttl_sec: int

    def default_limits(self):
        return {"window": 0, "jail": 0,
                "warning": self.missed_warn if self.missed_warn is not None else 500,
                "critical": self.missed_crit if self.missed_crit is not None else 4000}

    @classmethod
    def from_values(cls, cfg):
        warn = cfg.get("MISSED_WARN", "").strip()
        crit = cfg.get("MISSED_CRIT", "").strip()
        lang = cfg.get("LANG", "ru").strip().lower()
        return cls(
            host=os.uname().nodename, state_path=cfg.get("STATE_PATH", "/var/lib/genesisl1-tg-bot/state.json"),
            service=cfg.get("SERVICE", "genesisl1.service"), chain_id=cfg.get("CHAIN_ID", "genesis_29-2"),
            token=cfg.get("BOT_TOKEN", "").strip(),
            allowed=frozenset(x.strip() for x in cfg.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()),
            rpc=cfg.get("RPC_URL", "http://127.0.0.1:26657").rstrip("/"),
            valoper=cfg.get("VALOPER", "").strip(), valcons=cfg.get("VALCONS", "").strip(),
            default_lang=lang if lang in ("en", "ru", "de") else "en",
            check_interval=int(cfg.get("CHECK_INTERVAL", 60)), disk_warn_pct=int(cfg.get("DISK_WARN_PCT", 85)),
            missed_warn=int(warn) if warn else None, missed_crit=int(crit) if crit else None,
            stall_ticks=int(cfg.get("STALL_TICKS", 3)),
            missed_warn_hysteresis=max(1, int(cfg.get("MISSED_WARN_HYSTERESIS", 50))),
            missed_warn_step=max(1, int(cfg.get("MISSED_WARN_STEP", 100))),
            missed_warn_reminder_sec=max(60, int(cfg.get("MISSED_WARN_REMINDER_SEC", 3600))),
            missed_alert_min_gap=int(cfg.get("MISSED_ALERT_MIN_GAP", 900)),
            signing_fail_alert_ticks=int(cfg.get("SIGNING_FAIL_ALERT_TICKS", 3)),
            peers_fail_alert_ticks=int(cfg.get("PEERS_FAIL_ALERT_TICKS", 5)), peers_min=int(cfg.get("PEERS_MIN", 3)),
            lag_warn=int(cfg.get("BLOCK_LAG_WARN_SEC", 30)), pending_max=int(cfg.get("PENDING_MAX", 50)),
            pending_ttl_sec=int(cfg.get("PENDING_TTL_SEC", 21600)),
        )

    @classmethod
    def from_file(cls, path):
        values = {}
        try:
            with open(path) as source:
                for line in source:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        values[key.strip()] = value.strip()
        except FileNotFoundError:
            pass
        return cls.from_values(values)
