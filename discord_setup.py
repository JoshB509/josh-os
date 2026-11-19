"""Set up the team's Discord bots. Run on this PC:  python discord_setup.py

Asks for your Discord user id and each bot's token (typed tokens aren't shown), and saves them to Data/discord.json,
which stays on this PC and out of git. Restart Josh OS afterwards.

A token is only saved when it looks like a real Discord bot token (three dot separated parts of letters, digits, - and _).
Anything else, a blank answer, Ctrl+C or a closed window keeps what was already saved, so a bad paste can never replace a
working token. If Data/discord.json can't be read, nothing is written at all. The file is replaced in one step, and the
version before each save is kept as Data/discord.json.previous.

On Windows, paste into the hidden token prompt with a right-click. Ctrl+V there can type a control character instead of
pasting, which this check now refuses.
"""
import getpass
import json
import os
import re
import sys
from pathlib import Path

TEAM = ['sam', 'tariq', 'laia', 'saskia', 'anouk', 'max']
TOKEN = re.compile(r'[A-Za-z0-9_-]{20,40}\.[A-Za-z0-9_-]{5,10}\.[A-Za-z0-9_-]{25,60}')
USER_ID = re.compile(r'\d{17,20}')
CHANNEL = re.compile(r'[a-z0-9_-]{1,100}|\d{17,20}')
TRIES = 3


class Cancelled(Exception):
    pass


def clean_token(raw):
    """The token if it looks like a Discord bot token, else None. Never echoes it."""
    t = str(raw or '').strip().strip('"').strip("'").strip()
    if t.lower().startswith('bot '):
        t = t[4:].strip()
    return t if TOKEN.fullmatch(t) else None


def describe(raw):
    """Why a token was refused, without showing any of it."""
    t = str(raw or '')
    if any(ord(ch) < 32 for ch in t):
        return 'it has a control character in it, which usually means Ctrl+V typed a character instead of pasting. Paste with a right-click'
    parts = t.strip().split('.')
    if len(parts) != 3:
        return f'it has {len(parts)} part{"" if len(parts) == 1 else "s"} instead of 3 separated by dots ({len(t.strip())} characters)'
    return f'its parts are the wrong length or have characters a token never has ({len(t.strip())} characters)'


def load(file):
    """The saved config. Raises ValueError when the file exists but can't be read, so nothing overwrites it."""
    if not file.exists():
        return {}
    try:
        c = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise ValueError(f'{file} could not be read ({type(e).__name__}), so nothing was changed. Fix or move that file first.') from None
    if not isinstance(c, dict) or not isinstance(c.get('bots', {}), dict):
        raise ValueError(f'{file} is not in the expected shape, so nothing was changed.')
    return c


def save(file, config):
    """Replace the file in one step, keeping the version before as .previous."""
    file.parent.mkdir(exist_ok=True)
    tmp = file.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(config, indent=2), encoding='utf-8')
    if file.exists():
        prev = file.with_suffix('.json.previous')
        prev.write_bytes(file.read_bytes())
    os.replace(tmp, file)


def ask_token(name, have, secret, say):
    for _ in range(TRIES):
        raw = secret(f"{name}'s bot token (hidden while you type, Enter to keep what's saved): ")
        if not str(raw or '').strip():
            return None
        token = clean_token(raw)
        if token:
            return token
        say(f"That doesn't look like a Discord bot token: {describe(raw)}. Nothing was changed. Try again.")
    say(f"No valid token after {TRIES} tries, so {name}'s " + ('saved token was kept.' if have else 'bot was skipped.'))
    return None


def main(file=None, ask=input, secret=getpass.getpass, say=print, agents=None, keep_owner=False):
    agents = list(TEAM if agents is None else agents)
    if not agents or any(a not in TEAM for a in agents):
        say('Unknown or empty bot selection. Nothing was changed.')
        return 1
    file = file or Path(__file__).resolve().parent / 'Data' / 'discord.json'
    try:
        config = load(file)
    except ValueError as e:
        say(str(e))
        return 1
    before = json.dumps(config, sort_keys=True)
    config.setdefault('bots', {})
    owners = config.get('owners') or []
    try:
        say('Discord bridge setup. Press Enter to keep what is already saved.\n')
        say('Updating: ' + ', '.join(a.title() for a in agents) + '. Other bots are preserved.')
        if keep_owner and owners:
            say('Keeping your saved Discord user ID.')
        else:
            say('Your Discord user id: in Discord, turn on Settings > Advanced > Developer Mode, then right-click')
            say('your name and choose Copy User ID.')
        owner = '' if keep_owner and owners else ask(f"Your user id [{', '.join(owners) or 'none'}]: ").strip()
        if owner:
            if USER_ID.fullmatch(owner):
                config['owners'] = [owner]
            else:
                say("That isn't a Discord user id (17 to 20 digits), so the saved one was kept.")
        for agent in agents:
            have = bool((config['bots'].get(agent) or {}).get('token'))
            answer = ask(f"\nSet up {agent.title()}'s bot? ({'already set up, ' if have else ''}y/n): ").strip().lower()
            if answer != 'y':
                continue
            token = ask_token(agent.title(), have, secret, say)
            if not token and not have:
                continue
            bot = dict(config['bots'].get(agent) or {})
            if token:
                bot['token'] = token
            channel = ask(f"Channel {agent.title()} listens in [{', '.join(bot.get('channels') or [agent])}]: ").strip().lstrip('#').lower()
            if channel and not CHANNEL.fullmatch(channel):
                say("That isn't a Discord channel name or id, so the saved channel was kept.")
                channel = ''
            bot['channels'] = [channel] if channel else (bot.get('channels') or [agent])
            config['bots'][agent] = bot
    except (KeyboardInterrupt, EOFError):
        say('\nStopped. Nothing was changed.')
        return 1
    if json.dumps(config, sort_keys=True) == before:
        say('\nNothing changed, so the file was left as it was.')
        return 0
    save(file, config)
    say(f"\nSaved to {file}. Bots set up: {', '.join(sorted(config['bots'])) or 'none'}. Restart Josh OS to connect them.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
