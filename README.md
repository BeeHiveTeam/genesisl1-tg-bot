# GenesisL1 Telegram validator bot

Adapted from BeeHiveTeam/provenance-tg-bot, commit
037d0f92a108dcafe54793f7c5d5dd5ef1cf4f12. MIT license retained.

The bot monitors a GenesisL1 validator and sends Telegram alerts for jail,
tombstoning, missed signatures, voting-power changes, service failures,
synchronization, block stalls, peer count and disk usage. It uses Python 3.8+
standard-library modules and runs under the dedicated genesisl1-bot user.

Commands and inline buttons support /status, /val, /sync, /peers, /disk,
/id, /lang and /help. English, Russian and German are available.
Both /status and /val show the validator's total delegated L1, including
self-delegation, from the staking module token balance. The bot converts el1
using 18 decimal places without floating-point rounding. Missing staking data
is reported as unavailable rather than as a zero balance.

RPC alerts show the host and port from `RPC_URL`. If a node reports a different
chain, the bot shows the expected and observed chain IDs and suspends validator
checks on that endpoint. A signing-info outage that triggered an alert produces
one recovery notice. A confirmed tombstone produces one alert for the validator.
Node, RPC and disk monitoring continue.

## Installation

On a Linux host with Python 3 and systemd, clone the repository and install the bot under its own service account.

```bash
git clone https://github.com/BeeHiveTeam/genesisl1-tg-bot.git
cd genesisl1-tg-bot
sudo bash install.sh
sudoedit /etc/genesisl1-tg-bot/config.env
```

Set `RPC_URL` to your node's local CometBFT RPC (the example uses `http://127.0.0.1:26657`), `SERVICE` to its systemd unit, and `VALOPER` and `VALCONS` to the validator's public operator and consensus addresses. The expected chain ID is `genesis_29-2`. Blank `MISSED_WARN` and `MISSED_CRIT` derive the warning and critical thresholds at 10% and 80% of the current on-chain missed-block budget.

Create a Telegram bot with `/newbot` in @BotFather, then run the configuration helper in a terminal.

```bash
sudo python3 /opt/genesisl1-tg-bot/configure.py
```

The helper prompts privately for the bot token and verifies it with Telegram. Enter the chat IDs you want to authorize. For a new chat you can send `/start` to your bot and select the detected ID. The helper enables the monitoring service after configuration. The live token and allowlist stay in `/etc/genesisl1-tg-bot/config.env` with mode `0640` and owner `root:genesisl1-bot`. Local alert state and message offsets belong in the service state directory. The public example leaves the token, chat IDs and validator addresses empty.

The staking validator record supplies the jailed flag. The bot reads signing
information, validator state and slashing parameters through local ABCI queries.
Its systemd sandbox restricts access to validator data directories.

## Verify and operate

```bash
python3 -m unittest discover -p 'test_*.py'
sudo -u genesisl1-bot python3 /opt/genesisl1-tg-bot/bot.py --check
systemctl status genesisl1-tg-bot.service
journalctl -u genesisl1-tg-bot.service -f
```

--check performs node probes and prints JSON without contacting Telegram.
Alert state and Telegram offsets are persisted in /var/lib/genesisl1-tg-bot.
A local lock prevents two instances from sharing the same runtime state.
Pending alert retries are bounded, expire after six hours by default and use
short timeouts. Language selection persists across restarts. Signing thresholds are derived from each successful probe. The monitor records the last verified thresholds for reminders during an outage.

When upgrading an existing installation, run the installer from the complete repository, run the service-user check above, then restart the Telegram bot with `sudo systemctl restart genesisl1-tg-bot.service`. The installer copies all runtime modules and preserves the existing configuration and alert state.

The process has a 10% CPU quota and a 128 MiB memory limit. Alerts depend on
this host and its network connection remaining available. Full Telegram
operation can be verified after the operator provides a bot token and chat ID.
