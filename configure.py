#!/usr/bin/env python3
"""Enter Telegram credentials privately and activate the installed monitoring bot."""
import getpass
import grp
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import warnings


CONFIG = Path('/etc/genesisl1-tg-bot/config.env')
SERVICE = 'genesisl1-tg-bot.service'


def telegram(token, method):
    request = urllib.request.Request(
        'https://api.telegram.org/bot' + token + '/' + method,
        urllib.parse.urlencode({'timeout': 0}).encode())
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError('Telegram returned HTTP ' + str(error.code)) from None
    except Exception:
        raise RuntimeError('Telegram connection failed. Retry configuration.') from None
    if not result.get('ok'):
        raise RuntimeError('Telegram rejected the request')
    return result['result']


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run this configuration helper with sudo.')
    if not sys.stdin.isatty():
        raise SystemExit('Run interactively in a terminal to enter the token privately.')
    if not CONFIG.is_file():
        raise SystemExit('Run install.sh first.')
    original = CONFIG.read_text()
    values = {}
    for line in original.splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    # Abort before input if the terminal cannot disable echo.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        try:
            token = getpass.getpass('New GenesisL1 bot token (Enter keeps configured token): ').strip()
        except getpass.GetPassWarning:
            raise SystemExit('A terminal with hidden input is required to enter the bot token.') from None
    token = token or values.get('BOT_TOKEN', '')
    if not re.fullmatch(r'\d{5,}:[A-Za-z0-9_-]{20,}', token):
        raise SystemExit('Invalid Telegram bot token format.')
    identity = telegram(token, 'getMe')
    print('Configuring @' + identity['username'])
    chats = input('Allowed chat IDs, comma-separated (Enter keeps configured IDs): ').strip()
    chats = chats or values.get('ALLOWED_CHAT_IDS', '')
    if not chats:
        active = subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE]).returncode == 0
        if active:
            raise SystemExit('Enter chat IDs explicitly while the bot is running.')
        input('Send /start to this bot in Telegram, then press Enter here: ')
        candidates = {}
        for update in telegram(token, 'getUpdates'):
            message = update.get('message') or update.get('edited_message') or {}
            chat = message.get('chat', {})
            if 'id' in chat:
                candidates[str(chat['id'])] = chat.get('username') or chat.get('title') or chat.get('first_name', '')
        for chat_id, label in candidates.items():
            print('Detected chat', chat_id, repr(label))
        chats = input('Enter the chat IDs to authorize, comma-separated: ').strip()
    allowed = [value.strip() for value in chats.split(',') if value.strip()]
    if not allowed or not all(re.fullmatch(r'-?\d+', value) for value in allowed):
        raise SystemExit('At least one numeric chat ID is required.')
    replacements = {'BOT_TOKEN': token, 'ALLOWED_CHAT_IDS': ','.join(allowed)}
    lines = []
    for line in original.splitlines():
        key = line.split('=', 1)[0].strip()
        if key in replacements:
            line = key + '=' + replacements.pop(key)
        lines.append(line)
    lines.extend(key + '=' + value for key, value in replacements.items())
    descriptor, temporary = tempfile.mkstemp(prefix='config.', dir=CONFIG.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            output.write('\n'.join(lines) + '\n')
        os.chmod(temporary, 0o640)
        os.chown(temporary, 0, grp.getgrnam('genesisl1-bot').gr_gid)
        os.replace(temporary, CONFIG)
    finally:
        Path(temporary).unlink(missing_ok=True)
    subprocess.run(['systemctl', 'enable', SERVICE], check=True)
    subprocess.run(['systemctl', 'restart', SERVICE], check=True)
    subprocess.run(['systemctl', 'is-active', SERVICE], check=True)
    print('GenesisL1 bot configured. Send /status in Telegram.')


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(str(error))
