"""Set up the team's Discord bots. Run on this PC:  python discord_setup.py

Asks for your Discord user id and each bot's token (typed tokens aren't shown), and saves
them to Data/discord.json, which stays on this PC and out of git. Restart Josh OS afterwards.
"""
import getpass
import json
from pathlib import Path

TEAM = ['sam', 'tariq', 'laia', 'saskia', 'anouk']


def main():
    file = Path(__file__).resolve().parent / 'Data' / 'discord.json'
    file.parent.mkdir(exist_ok=True)
    try:
        config = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        config = {}
    config.setdefault('bots', {})
    owners = config.get('owners') or []
    print('Discord bridge setup. Press Enter to keep what is already saved.\n')
    print('Your Discord user id: in Discord, turn on Settings > Advanced > Developer Mode, then right-click')
    print('your name and choose Copy User ID.')
    owner = input(f"Your user id [{', '.join(owners) or 'none'}]: ").strip()
    if owner:
        config['owners'] = [owner]
    for agent in TEAM:
        have = agent in config['bots']
        answer = input(f"\nSet up {agent.title()}'s bot? ({'already set up, ' if have else ''}y/n): ").strip().lower()
        if answer != 'y':
            continue
        token = getpass.getpass(f"{agent.title()}'s bot token (hidden while you type): ").strip()
        if not token and not have:
            print('No token, skipped.')
            continue
        bot = config['bots'].setdefault(agent, {})
        if token:
            bot['token'] = token
        channel = input(f"Channel {agent.title()} listens in [{', '.join(bot.get('channels') or [agent])}]: ").strip()
        bot['channels'] = [channel.lstrip('#')] if channel else (bot.get('channels') or [agent])
    file.write_text(json.dumps(config, indent=2), encoding='utf-8')
    print(f"\nSaved to {file}. Bots set up: {', '.join(config['bots']) or 'none'}. Restart Josh OS to connect them.")


if __name__ == '__main__':
    main()
