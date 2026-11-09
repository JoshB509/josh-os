"""Talk to the team on Discord.

Each team member can have their own Discord bot account (Sam's bot shows as Sam, with his
picture). This bridge runs inside Josh OS on this PC. It listens for Josh's messages, passes
each one to that team member's normal Josh OS chat (the same conversation as the chat in the
app, with every power they have there), and posts the reply from the matching bot, with any
files they send back.

Settings live in Data/discord.json, which stays on this PC and out of git. Make it with
`python discord_setup.py`, which asks for each bot's token without showing it.

    {
      "owners": ["<Josh's Discord user id>"],
      "bots": {
        "sam":   {"token": "...", "channels": ["sam"]},
        "tariq": {"token": "...", "channels": ["tariq"]}
      }
    }

A bot answers in direct messages, in channels named in its "channels" list (by name or id),
and in any other channel where it is @mentioned. It only ever answers the owners, so nobody
else in a server can make a team member act.
"""
import asyncio
import json
import re
import threading
import time
import uuid
from pathlib import Path

IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/webp'}
MAX_FILE_BYTES = 25 * 1024 * 1024
LIMIT = 1900  # Discord's limit is 2000 characters per message


def load_config(data_root):
    file = Path(data_root) / 'discord.json'
    try:
        c = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    bots = {k: v for k, v in (c.get('bots') or {}).items() if isinstance(v, dict) and v.get('token')}
    owners = [str(o) for o in (c.get('owners') or []) if str(o).strip()]
    return dict(owners=owners, bots=bots) if bots and owners else {}


def allowed(config, agent_id, author_id, is_dm, channel_name, channel_id, mentioned):
    """Whether this bot should answer this message."""
    if str(author_id) not in config.get('owners', []):
        return False
    if is_dm or mentioned:
        return True
    channels = [str(c).lower().lstrip('#') for c in (config['bots'].get(agent_id, {}).get('channels') or [agent_id])]
    return str(channel_name or '').lower() in channels or str(channel_id) in channels


def split(text, limit=LIMIT):
    """Split a reply into Discord-sized messages, breaking at paragraphs, then lines, then words."""
    text = (text or '').strip() or '…'
    out = []
    while len(text) > limit:
        cut = max(text.rfind('\n\n', 0, limit), text.rfind('\n', 0, limit))
        if cut < limit // 2:
            cut = text.rfind(' ', 0, limit)
        if cut < limit // 2:
            cut = limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    out.append(text)
    return out


def clean(text, bot_user_id=None):
    """Drop the bot's own @mention from the start of the message."""
    if bot_user_id:
        text = re.sub(rf'<@!?{bot_user_id}>', '', text)
    return text.strip()


def saved_line(saved):
    """A short line about what the team member changed in Josh OS, under their reply."""
    if not saved:
        return ''
    done = [s for s in saved if s.get('action') != 'failed']
    failed = [s for s in saved if s.get('action') == 'failed']
    bits = []
    if done:
        names = ', '.join(f"{s.get('title') or s.get('kind', 'item')}" for s in done[:5])
        bits.append(f"Saved in Josh OS: {names}" + (f" and {len(done) - 5} more" if len(done) > 5 else ''))
    if failed:
        bits.append(f"Couldn't save {len(failed)}: " + '; '.join(s.get('error', '')[:80] for s in failed[:3]))
    return '\n'.join(f'-# {b}' for b in bits)


class Bridge:
    """Connects each configured bot to its team member's chat."""

    def __init__(self, server):
        self.server = server
        self.data_root = Path(server.store.root)
        self.config = load_config(self.data_root)
        self.status = dict(enabled=bool(self.config), bots={}, error='')
        self.loop = None

    # Work that doesn't need Discord, so it can be tested on its own ------------------------
    def save_attachment(self, agent_id, name, content_type, data):
        """Images become Josh OS images; anything else goes to the inbox for the agent to read."""
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f'{name} is over 25 MB.')
        if (content_type or '').split(';')[0] in IMAGE_TYPES:
            return 'image', self.server.store.import_image(name, data)['asset']
        inbox = self.data_root / 'discord' / 'inbox' / agent_id
        inbox.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[^A-Za-z0-9._ -]+', '_', Path(name).name)[:120] or 'file'
        path = inbox / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{safe}"
        path.write_bytes(data)
        return 'file', str(path)

    def handle(self, agent_id, text, attachments=()):
        """Run one Discord message through the team member's chat. Returns (messages, files to send)."""
        images, files = [], []
        for name, content_type, data in attachments:
            kind, ref = self.save_attachment(agent_id, name, content_type, data)
            (images if kind == 'image' else files).append(ref)
        s = self.server
        try:
            r = s.agents.chat(dict(agent_id=agent_id, message=text, images=images, files=files, via='discord'),
                              s.carousels.all()['items'], s.carousels, s.inspo)
        except Exception as e:  # noqa: BLE001 - every failure goes back to Josh in Discord
            return [f"I couldn't do that: {str(e).strip(chr(39) + chr(34))[:300]}"], []
        reply = r.get('reply') or {}
        body = reply.get('text') or ''
        note = saved_line(reply.get('saved'))
        return split(body + ('\n' + note if note else '')), list(reply.get('files') or [])

    # Discord -----------------------------------------------------------------------------
    def start(self):
        if not self.config:
            return self
        try:
            import discord  # noqa: F401
        except ImportError:
            self.status['error'] = 'The Discord bridge needs discord.py. Install it with: pip install "discord.py>=2.3"'
            return self
        threading.Thread(target=self._run, daemon=True, name='discord-bridge').start()
        return self

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(asyncio.gather(*(self._bot(a, b) for a, b in self.config['bots'].items())))
        except Exception as e:  # noqa: BLE001
            self.status['error'] = str(e)[:300]

    async def _bot(self, agent_id, bot):
        import discord
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        client = discord.Client(intents=intents)
        state = self.status['bots'].setdefault(agent_id, dict(connected=False, name='', error=''))

        @client.event
        async def on_ready():
            state.update(connected=True, name=str(client.user), error='')

        @client.event
        async def on_disconnect():
            state['connected'] = False

        @client.event
        async def on_message(msg):
            if msg.author.bot or msg.author == client.user:
                return
            is_dm = isinstance(msg.channel, discord.DMChannel)
            mentioned = client.user in msg.mentions if client.user else False
            if not allowed(self.config, agent_id, msg.author.id, is_dm, getattr(msg.channel, 'name', ''),
                           msg.channel.id, mentioned):
                return
            text = clean(msg.content, client.user.id if client.user else None)
            attachments = []
            for a in msg.attachments[:10]:
                if a.size > MAX_FILE_BYTES:
                    await msg.channel.send(f'{a.filename} is over 25 MB, so I skipped it.')
                    continue
                attachments.append((a.filename, a.content_type or '', await a.read()))
            if not text and not attachments:
                return
            async with msg.channel.typing():
                messages, files = await asyncio.to_thread(self.handle, agent_id, text, attachments)
            for i, part in enumerate(messages):
                last = i == len(messages) - 1
                send = [discord.File(f) for f in files] if last and files else None
                await msg.channel.send(part, files=send)

        try:
            await client.start(bot['token'])
        except Exception as e:  # noqa: BLE001 - a bad token shouldn't stop the other bots
            state.update(connected=False, error=str(e)[:200])
