#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    printf '%s\n' 'Run this installer as root.' >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 -m py_compile "$SCRIPT_DIR/bot.py" "$SCRIPT_DIR/configure.py"

if ! getent passwd genesisl1-bot >/dev/null; then
    /usr/sbin/useradd --system --user-group --no-create-home \
        --home-dir /var/lib/genesisl1-tg-bot --shell /usr/sbin/nologin genesisl1-bot
fi

install -d -m 0755 /opt/genesisl1-tg-bot
install -d -m 0750 -o root -g genesisl1-bot /etc/genesisl1-tg-bot
for name in bot.py configure.py LICENSE; do
    install -m 0644 "$SCRIPT_DIR/$name" "/opt/genesisl1-tg-bot/$name"
done
if [ ! -e /etc/genesisl1-tg-bot/config.env ]; then
    install -m 0640 -o root -g genesisl1-bot \
        "$SCRIPT_DIR/config.env.example" /etc/genesisl1-tg-bot/config.env
fi
install -m 0644 "$SCRIPT_DIR/genesisl1-tg-bot.service" \
    /etc/systemd/system/genesisl1-tg-bot.service
systemctl daemon-reload
printf '%s\n' 'Installed. Configure Telegram and start the bot with:' \
    'sudo python3 /opt/genesisl1-tg-bot/configure.py'
