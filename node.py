"""Read-only CometBFT, SDK, systemd and filesystem queries."""
import base64
import calendar
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request



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


def signing_limits(window, minimum_atomics, warning, critical):
    if type(window) is not int or window <= 0 or not re.fullmatch(rb"[0-9]+", minimum_atomics):
        raise ValueError("Invalid slashing window or signed-block fraction")
    with localcontext() as context:
        context.prec = 80
        minimum = Decimal(minimum_atomics.decode()) / Decimal(10**18)
        if minimum > 1:
            raise ValueError("Invalid minimum signed-block fraction")
        required = int((minimum * window).to_integral_value(rounding=ROUND_HALF_EVEN))
    allowed = window - required
    warn = max(1, allowed // 10) if warning is None else warning
    crit = max(warn, allowed * 4 // 5) if critical is None else critical
    if not 1 <= warn <= crit <= allowed + 1:
        raise ValueError("Missed-block thresholds exceed the current slashing budget")
    return {"window": window, "jail": allowed, "warning": warn, "critical": crit}


class Node:
    def __init__(self, settings):
        self.settings = settings

    def http_get(self, path, timeout=6):
        try:
            with urllib.request.urlopen(self.settings.rpc + path, timeout=timeout) as r:
                return json.load(r)
        except Exception:
            return None

    def app_version(self):
        response = self.http_get("/abci_info")
        try:
            return response["result"]["response"]["version"]
        except (KeyError, TypeError):
            return "?"

    def svc_active(self):
        ok, output = sh_args(["systemctl", "is-active", self.settings.service])
        return ok and output == "active"

    def svc_start(self):
        return sh_args(["systemctl", "show", self.settings.service,
                        "-p", "ExecMainStartTimestamp", "--value"])[1]

    def rpc_endpoint(self):
        """Show the configured host and port, omitting credentials, paths and query parameters."""
        try:
            endpoint = urllib.parse.urlsplit(self.settings.rpc)
            host = endpoint.hostname
            if not host or endpoint.scheme not in ("http", "https"):
                return "?"
            port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
            return "%s:%d" % ("[" + host + "]" if ":" in host else host, port)
        except ValueError:
            return "?"

    def get_status(self):
        d = self.http_get("/status")
        r = d.get("result") if isinstance(d, dict) else None
        if not isinstance(r, dict):
            return None
        ni = r.get("node_info", {})
        network = ni.get("network") if isinstance(ni, dict) else None
        if not isinstance(network, str) or not network:
            return None
        if network != self.settings.chain_id:
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

    def get_signing(self):
        if not self.settings.valcons or not self.settings.valoper:
            return None
        try:
            signing_response = self.abci_query("/cosmos.slashing.v1beta1.Query/SigningInfo", self.settings.valcons)
            validator_response = self.abci_query("/cosmos.staking.v1beta1.Query/Validator", self.settings.valoper)
            params_response = self.abci_query("/cosmos.slashing.v1beta1.Query/Params")
            signing = protobuf_fields(signing_response[1])
            validator = protobuf_fields(validator_response[1])
            params = protobuf_fields(params_response[1])
            if signing[1].decode() != self.settings.valcons or validator[1].decode() != self.settings.valoper:
                return None
            limits = signing_limits(params[1], params[2], self.settings.missed_warn, self.settings.missed_crit)
            jailed_timestamp = protobuf_fields(signing.get(4, b""))
            seconds = jailed_timestamp.get(1, 0)
            if seconds >= 1 << 63:
                seconds -= 1 << 64
            jailed_until = datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
            return {"limits": limits, "missed": signing.get(6, 0),
                    "jailed": bool(validator.get(3, 0)),
                    "jailed_until": jailed_until,
                    "tombstoned": bool(signing.get(5, 0))}
        except Exception as error:
            sys.stderr.write("GenesisL1 signing query failed: %s\n" % error)
            return None

    def get_peers(self):
        d = self.http_get("/net_info")
        try:
            return int(d["result"]["n_peers"])
        except Exception:
            return None

    def get_delegation(self):
        """The validator's full bonded token balance, including self-delegation.

        Read the SDK token amount (el1), not rounded CometBFT voting power or
        delegator shares. Keep integer arithmetic for all 18 decimal places.
        """
        if not self.settings.valoper:
            return None
        try:
            response = self.abci_query("/cosmos.staking.v1beta1.Query/Validator", self.settings.valoper)
            validator = protobuf_fields(response[1])
            if validator[1].decode() != self.settings.valoper:
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

    def get_disk(self):
        try:
            usage = shutil.disk_usage("/")
            available_total = usage.used + usage.free
            if available_total > 0:
                return {"pct": round(100 * usage.used / available_total),
                        "avail_gb": round(usage.free / 1024**3, 1)}
        except OSError:
            pass
        return {"pct": None, "avail_gb": None}

    def abci_query(self, path, address=None):
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
        result = self.http_get("/abci_query?" + query)
        if not result or "error" in result:
            raise ValueError("Local ABCI query unavailable")
        response = result["result"]["response"]
        if int(response.get("code", 0)) != 0:
            raise ValueError("ABCI returned code %s" % response["code"])
        return protobuf_fields(base64.b64decode(response["value"], validate=True))
