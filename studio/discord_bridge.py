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

When Sam sends carousel slides, they arrive as a review (studio/discord_review.py): one message with the
current slide, Previous and Next buttons, the slide position, and feedback buttons. Only the owners can use
the buttons, and the state lives in Josh OS, so they keep working after a restart.
"""
import asyncio
import json
import re
import threading
import time
import uuid
from pathlib import Path

from . import discord_review

IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/webp'}
# Configured bots can receive owner-initiated chat. Proactive delivery is separate.
DISCONNECTED = frozenset()
NO_PROACTIVE_DM = frozenset({'max'})
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
    done = [s for s in saved if s.get('action') not in ('failed', 'already saved', 'unchanged')]
    already = [s for s in saved if s.get('action') == 'already saved']
    same = [s for s in saved if s.get('action') == 'unchanged']
    failed = [s for s in saved if s.get('action') == 'failed']
    bits = []
    if already:
        bits.append('Already in Josh OS: ' + ', '.join(f"{s.get('title') or s.get('kind', 'item')}" for s in already[:5]))
    if done:
        names = ', '.join(f"{s.get('title') or s.get('kind', 'item')}"
                          + (f" ({', '.join(s['fields'])}{', now ' + s['status'] if 'status' in (s.get('fields') or []) else ''})" if s.get('fields') else '')
                          for s in done[:5])
        bits.append(f"Saved in Josh OS: {names}" + (f" and {len(done) - 5} more" if len(done) > 5 else ''))
    if same:
        bits.append('Nothing changed: ' + ', '.join(f"{s.get('title') or s.get('kind', 'item')}" for s in same[:5]))
    if failed:
        bits.append(f"Couldn't save {len(failed)}: " + '; '.join(s.get('error', '')[:160] for s in failed[:3]))
    return '\n'.join(f'-# {b}' for b in bits)


def business_audience(ev, owners):
    """Whether Max's pinned Business channel is provably private to Josh and his own bots. Returns (ok, why).

    ev comes from Bridge.fixed_channel. When Max's bot can see the server's members (Server Members Intent), every
    account that can read the channel must be Josh or one of the bots this bridge runs, and that is enough even in a
    bigger server with other people in it. Without the list, the server must be owned by Josh and its whole member count
    must equal Josh plus his connected bots in it. Josh's word that a server is private is not enough for money figures,
    so there is no override."""
    if not ev.get('found'):
        return False, "The pinned Business channel was not found by Max's bot."
    if str(ev.get('guild_id')) != str(ev.get('expected_guild_id')):
        return False, 'The pinned channel is not in the pinned server, so nothing was sent.'
    if ev.get('expected_bot_user_id') and str(ev.get('bot_user_id')) != str(ev['expected_bot_user_id']):
        return False, 'The bot that connected is not the pinned Max bot, so nothing was sent.'
    if not ev.get('can_send'):
        return False, "Max's bot cannot post in the Business channel."
    ours = {str(x) for x in ev.get('our_bot_ids') or []}
    owners = {str(o) for o in owners or []}
    viewers = ev.get('viewers')
    if viewers is not None:
        # The full list of who can read this channel. The server's owner and admins are in it, since Discord lets them
        # read every channel, so who owns the server doesn't need checking separately.
        strangers = [v for v in viewers if str(v) not in owners | ours]
        if strangers:
            return False, (f'{len(strangers)} account(s) other than Josh and his bots that run in Josh OS can read the Business '
                           'channel, so nothing was sent.')
        return True, ''
    count = ev.get('member_count')
    # Without the list, the count only proves privacy if the one person in it is Josh, so the owner must be Josh here.
    if not ev.get('owner_is_josh'):
        return False, ("Max can't see who is in the server (Server Members Intent is off), and the server's owner isn't Josh, "
                       'so the audience is unknown. Nothing was sent.')
    if ev.get('unconnected_bots'):
        return False, "Not all of Josh's bots are connected, so the server's members cannot be accounted for."
    if isinstance(count, int) and count == 1 + len(ours):
        return True, ''
    return False, (f'The server has {count} members and only {1 + len(ours)} are accounted for (Josh and his bots), so other '
                   'people may be able to read the Business channel. Nothing was sent.')


class Bridge:
    """Connects each configured bot to its team member's chat."""

    def __init__(self, server):
        self.server = server
        self.data_root = Path(server.store.root)
        self.config = load_config(self.data_root)
        kept_off = sorted(a for a in self.config.get('bots', {}) if a in DISCONNECTED)
        for a in kept_off:
            del self.config['bots'][a]
        if self.config and not self.config.get('bots'):
            self.config = {}
        self.status = dict(enabled=bool(self.config), bots={}, error='', kept_off=kept_off)
        self.notices = None  # studio/notices.py, set by the server, for Josh's !pause and !mute commands
        self.loop = None
        self.channels = {}  # the channel Josh last wrote to each team member in, for results that come later
        self.clients = {}  # each bot's live connection, so a review can check who is in its server
        carousels = getattr(server, 'carousels', None)
        flow = getattr(server, 'edit_flow', None)
        notes = (lambda pid, note: flow.set_anim_notes(pid, note, add=True)) if flow is not None else None
        self.reviews = discord_review.Reviews(server.store, carousels, notes) if carousels is not None else None

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

    def handle(self, agent_id, text, attachments=(), discord=None):
        """Run one Discord message through the team member's chat. Returns (messages, files to send)."""
        messages, files, _ = self.handle_full(agent_id, text, attachments, discord)
        return messages, files

    def handle_full(self, agent_id, text, attachments=(), discord=None):
        """As handle, plus the reply's offers (the email replies it showed). discord is where the message came from:
        author_id, channel_id, is_dm, message_id and reply_to (the message Josh replied to), all checked by the bridge."""
        images, files = [], []
        for name, content_type, data in attachments:
            kind, ref = self.save_attachment(agent_id, name, content_type, data)
            (images if kind == 'image' else files).append(ref)
        s = self.server
        try:
            r = s.agents.chat(dict(agent_id=agent_id, message=text, images=images, files=files, via='discord', discord=discord),
                              s.carousels.all()['items'], s.carousels, s.inspo)
        except Exception as e:  # noqa: BLE001 - every failure goes back to Josh in Discord
            return [f"I couldn't do that: {str(e).strip(chr(39) + chr(34))[:300]}"], [], []
        reply = r.get('reply') or {}
        body = reply.get('text') or ''
        note = saved_line(reply.get('saved'))
        return split(body + ('\n' + note if note else '')), list(reply.get('files') or []), list(reply.get('offers') or [])

    def lineage(self):
        return getattr(getattr(self.server, 'replies', None), 'lineage', None)

    def begin_sent(self, agent_id, discord, offers):
        """Before an Anouk reply goes out on Discord: record it as "sending" in its conversation, to the owner who wrote,
        so it blocks older offers there. Raises if it can't be saved, and then the reply isn't sent. Other team members
        never show email replies."""
        lin = self.lineage()
        if agent_id != 'anouk':
            return None
        if lin is None:
            raise RuntimeError('Josh OS could not record this reply, so it was not sent.')
        from .approval_lineage import conversation
        return lin.begin(agent_id, conversation(discord), offers, kind='chat', owner_id=discord.get('author_id'),
                         channel_id=discord.get('channel_id'))

    def unrecorded_note(self, agent_id, where, error):
        """When an Anouk reply couldn't be recorded: block approvals in that conversation, then return the note to post,
        or None to stay silent. The note goes out only when the block is saved on disk, so no unrecorded message can
        ever leave an older offer approvable, even after a restart."""
        from .approval_lineage import conversation
        lin = self.lineage()
        self.status['error'] = f'An Anouk reply could not be recorded and was not sent ({str(error)[:150]}).'
        if lin is None:
            return None
        try:
            durable = lin.block(agent_id, conversation(where), f'Reply not recorded: {str(error)[:200]}')
        except Exception:  # noqa: BLE001 - no block at all, so say nothing
            return None
        return ("I couldn't record my reply, so I didn't send it, and nothing here can be approved until I show you the "
                'reply again.') if durable else None

    def finish_sent(self, pending, message_ids, complete, reason=''):
        """After the send: only a complete send can be approved. A failure here leaves it "sending", which blocks."""
        if pending is None:
            return None
        return self.lineage().finish(pending, message_ids, 'sent' if complete else ('partial' if message_ids else 'uncertain'), reason=reason)

    def review_split(self, agent_id, files):
        """Sam's carousel slides go out as a review instead of loose images. Returns (other files, carousel ids)."""
        if agent_id != 'sam' or not self.reviews or not files:
            return list(files), []
        owner = {}
        for item in self.server.carousels.all()['items']:
            for f in self.server.carousels.images(item):
                owner[str(Path(f).resolve()).lower()] = item['id']
        rest, cids = [], []
        for f in files:
            cid = owner.get(str(Path(f).resolve()).lower())
            if cid:
                if cid not in cids:
                    cids.append(cid)
            else:
                rest.append(f)
        return rest, cids

    def is_owner(self, user_id):
        return str(user_id) in self.config.get('owners', [])

    def notify(self, agent_id, text, files=()):
        """Send a later result (like finished thumbnails) to the channel Josh last used with this team member.
        Only that channel, which already passed the owner check, and only files that exist and fit Discord's limit."""
        import discord
        channel = self.channels.get(agent_id)
        if not channel or not self.loop:
            return False
        send = [str(f) for f in files if Path(f).is_file() and Path(f).stat().st_size <= MAX_FILE_BYTES][:10]

        async def go():
            parts = split(text) or ['']
            for i, part in enumerate(parts):
                last = i == len(parts) - 1
                await channel.send(part or None, files=[discord.File(f) for f in send] if last and send else None)
        asyncio.run_coroutine_threadsafe(go(), self.loop)
        return True

    # Discord -----------------------------------------------------------------------------
    def start(self):
        if getattr(self.server,'maintenance_hold_dispatch',False):
            return self  # No Gateway connection, on_ready flush or Discord dispatch.
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
        if agent_id == 'max':
            from .business import load_settings
            # Only after Josh turns on Server Members Intent for Max's bot; otherwise Discord refuses the login.
            intents.members = bool(load_settings(self.data_root).get('members_intent'))
        client = discord.Client(intents=intents)
        self.clients[agent_id] = client
        state = self.status['bots'].setdefault(agent_id, dict(connected=False, name='', error=''))

        @client.event
        async def on_ready():
            state.update(connected=True, name=str(client.user), error='')
            if agent_id not in NO_PROACTIVE_DM:
                await self.send_requested_reviews(client, agent_id)

        @client.event
        async def on_disconnect():
            state['connected'] = False

        @client.event
        async def on_resumed():
            # A resumed session does not emit on_ready. Restore Max's delivery readiness.
            # Other agents' notification behavior is outside this daily-brief change.
            if agent_id == 'max':
                state.update(connected=True, name=str(client.user), error='')

        @client.event
        async def on_message(msg):
            if msg.author.bot or msg.author == client.user:
                return
            is_dm = isinstance(msg.channel, discord.DMChannel)
            mentioned = client.user in msg.mentions if client.user else False
            if not allowed(self.config, agent_id, msg.author.id, is_dm, getattr(msg.channel, 'name', ''),
                           msg.channel.id, mentioned):
                return
            if agent_id == 'max' and not is_dm and not self.business_channel_ok(client, msg.channel):
                await msg.channel.send("I only talk business numbers in a DM or in the pinned Business channel once it's "
                                       'confirmed private to you. Nothing was shared here.')
                return
            text = clean(msg.content, client.user.id if client.user else None)
            from .owner_release_chat import is_command, discord_principal, dispatch
            if is_command(text):
                try:
                    principal=discord_principal(msg,self.config,is_dm=is_dm,agent_id=agent_id)
                    answer=await asyncio.to_thread(dispatch,self.server.chat_upload_approval,text,principal,agent_id)
                except (ValueError,KeyError) as exc:
                    answer='Release command stopped: '+str(exc)
                for part in split(answer):
                    await msg.channel.send(part,allowed_mentions=discord.AllowedMentions.none())
                return
            if self.notices is not None and text.startswith('!'):
                try:
                    answer = await asyncio.to_thread(self.notices.command, agent_id, text)
                except Exception as e:  # noqa: BLE001 - say so rather than pass it to the chat
                    answer = f"That didn't work: {str(e)[:200]}"
                if answer:
                    await msg.channel.send(answer, allowed_mentions=discord.AllowedMentions.none())
                    return
            self.channels[agent_id] = msg.channel
            attachments = []
            for a in msg.attachments[:10]:
                if a.size > MAX_FILE_BYTES:
                    await msg.channel.send(f'{a.filename} is over 25 MB, so I skipped it.')
                    continue
                attachments.append((a.filename, a.content_type or '', await a.read()))
            if not text and not attachments:
                return
            # Discord can deliver the same message twice (after a reconnect). The second one is ignored.
            lin = self.lineage()
            if lin is not None and not await asyncio.to_thread(lin.claim_inbound, f'bridge:{agent_id}:{msg.id}', dict(channel_id=str(msg.channel.id))):
                return
            ref = getattr(msg, 'reference', None)
            where = dict(author_id=str(msg.author.id), channel_id=str(msg.channel.id), is_dm=is_dm, message_id=str(msg.id),
                         reply_to=str(ref.message_id) if ref is not None and getattr(ref, 'message_id', None) else None)
            async with msg.channel.typing():
                messages, files, offers = await asyncio.to_thread(self.handle_full, agent_id, text, attachments, where)
            files, carousel_ids = await asyncio.to_thread(self.review_split, agent_id, files)
            sent_ids, complete, why = [], False, ''
            try:
                pending = await asyncio.to_thread(self.begin_sent, agent_id, where, offers)
            except Exception as e:  # noqa: BLE001 - not recorded, so the reply is not sent and approvals here are blocked
                note = await asyncio.to_thread(self.unrecorded_note, agent_id, where, e)
                if note:
                    await msg.channel.send(note, allowed_mentions=discord.AllowedMentions.none())
                return
            try:
                for i, part in enumerate(messages):
                    last = i == len(messages) - 1
                    send = [discord.File(f) for f in files] if last and files else None
                    sent_ids.append(str((await msg.channel.send(part, files=send)).id))
                complete = True
            except Exception as e:  # noqa: BLE001 - recorded as partial, then raised as before
                why = str(e)[:200]
                raise
            finally:
                try:
                    await asyncio.to_thread(self.finish_sent, pending, sent_ids, complete, why)
                except Exception:  # noqa: BLE001 - the record stays "sending", which blocks every offer here
                    pass
                finally:
                    if pending is not None and self.lineage() is not None:
                        self.lineage().release(pending)   # the next message in this conversation can go
            for cid in carousel_ids:
                try:
                    r = await asyncio.to_thread(self.reviews.create, cid, agent_id)
                    await self.post_review(msg.channel, r)
                except Exception as e:  # noqa: BLE001 - say so in the channel rather than drop it
                    await msg.channel.send(f"I couldn't post that carousel for review: {str(e)[:200]}")

        @client.event
        async def on_interaction(interaction):
            await self.on_review_interaction(interaction)

        if not re.fullmatch(r'[A-Za-z0-9_-]{20,40}\.[A-Za-z0-9_-]{5,10}\.[A-Za-z0-9_-]{25,60}', str(bot.get('token') or '')):
            state.update(connected=False, error="The saved token isn't shaped like a Discord bot token. Run discord_setup.py and paste a new one.")
            return
        try:
            await client.start(bot['token'])
        except Exception as e:  # noqa: BLE001 - a bad token shouldn't stop the other bots
            state.update(connected=False, error=str(e)[:200])

    # Carousel reviews ---------------------------------------------------------------------------
    def review_message(self, r):
        """The text, slide file and buttons for a review, as discord.py objects."""
        import discord
        v = self.reviews.view(r)
        embed = discord.Embed(description=v['text'])
        if v['file']:
            embed.set_image(url=f"attachment://{v['filename']}")
        view = discord.ui.View(timeout=None)
        styles = {'prev': discord.ButtonStyle.secondary, 'next': discord.ButtonStyle.primary,
                  'fb': discord.ButtonStyle.secondary, 'fball': discord.ButtonStyle.secondary}
        for label, action, disabled in v['buttons']:
            view.add_item(discord.ui.Button(label=label, style=styles[action], disabled=disabled,
                                            custom_id=discord_review.custom_id(r['id'], action), row=0 if action in ('prev', 'next') else 1))
        return embed, (discord.File(v['file'], filename=v['filename']) if v['file'] else None), view

    async def post_review(self, channel, r):
        embed, file, view = await asyncio.to_thread(self.review_message, r)
        sent = await channel.send(embed=embed, view=view, **({'file': file} if file else {}))
        await asyncio.to_thread(self.reviews.sent, r['id'], channel.id, sent.id, getattr(sent, 'jump_url', ''))
        return sent

    async def on_review_interaction(self, interaction):
        """Previous, Next and the feedback buttons. Only the owners can use them."""
        import discord
        data = getattr(interaction, 'data', None) or {}
        parsed = discord_review.parse(data.get('custom_id'))
        if not parsed or interaction.type != discord.InteractionType.component or not self.reviews:
            return
        rid, action = parsed
        if not self.is_owner(interaction.user.id):
            await interaction.response.send_message('Only Josh can review this carousel.', ephemeral=True)
            return
        try:
            r = await asyncio.to_thread(self.reviews.get, rid)
        except KeyError as e:
            await interaction.response.send_message(str(e).strip("'"), ephemeral=True)
            return
        if action in ('prev', 'next'):
            r = await asyncio.to_thread(self.reviews.move, rid, action)
            embed, file, view = await asyncio.to_thread(self.review_message, r)
            await interaction.response.edit_message(embed=embed, attachments=[file] if file else [], view=view)
            return
        slide = r['position'] if action == 'fb' else None
        await interaction.response.send_modal(self.feedback_modal(r, slide))

    def feedback_modal(self, r, slide):
        import discord
        bridge = self

        class Feedback(discord.ui.Modal):
            def __init__(self):
                shots = r.get('kind') == 'shots'
                what = 'Shot' if shots else 'Slide'
                whole = 'The whole shortlist' if shots else 'The whole carousel'
                super().__init__(title=(f'{what} {slide + 1} of {len(r["slides"])}' if slide is not None else whole)[:45], timeout=900)
                self.box = discord.ui.TextInput(label=f"Your feedback for {str(r.get('agent_id') or 'sam').title()}", style=discord.TextStyle.paragraph,
                                                max_length=discord_review.FEEDBACK_LIMIT, required=True)
                self.add_item(self.box)

            async def on_submit(self, interaction):
                if not bridge.is_owner(interaction.user.id):
                    await interaction.response.send_message('Only Josh can review this carousel.', ephemeral=True)
                    return
                try:
                    saved = await asyncio.to_thread(bridge.reviews.add_feedback, r['id'], self.box.value, interaction.user.id, slide)
                except (KeyError, ValueError) as e:
                    await interaction.response.send_message(str(e).strip("'"), ephemeral=True)
                    return
                shots = saved.get('kind') == 'shots'
                where = (f'shot {slide + 1}' if shots else f'slide {slide + 1}') if slide is not None else ('the whole shortlist' if shots else 'the whole carousel')
                kept = ('kept with this test review' if saved['test'] else
                        "saved to the video's animation notes for Tariq" if shots else "saved to the carousel's notes for Sam")
                await interaction.response.send_message(f'Got it. Your feedback on {where} is {kept}.', ephemeral=True)
                try:
                    embed, file, view = await asyncio.to_thread(bridge.review_message, saved)
                    if interaction.message:
                        await interaction.message.edit(embed=embed, attachments=[file] if file else [], view=view)
                except Exception:  # noqa: BLE001 - the feedback is saved even if the count can't refresh
                    pass
        return Feedback()

    async def send_requested_reviews(self, client, agent_id):
        """Send each review requested in Data/discord/review-requests once, to this bot's configured channel, but only
        when everyone who can see that channel is accounted for (see review_channel). Each request is tried once."""
        if not self.reviews:
            return
        todo = [(f, r) for f, r in discord_review.pending(self.data_root) if (r.get('agent_id') or 'sam') == agent_id]
        if not todo:
            return
        await self.all_bots_settled()
        for file, req in todo:
            outcome = dict(request=req, agent_id=agent_id)
            try:
                confirmed = req.get('audience_confirmed_by_owner') if isinstance(req.get('audience_confirmed_by_owner'), dict) else None
                channel, why, evidence = self.review_channel(client, agent_id, owner_confirmed=bool(confirmed and confirmed.get('quote')))
                outcome['audience'] = evidence
                if channel and req.get('expected_channel_id') and (str(channel.id) != str(req['expected_channel_id'])
                                                                   or str(channel.guild.id) != str(req.get('expected_guild_id') or channel.guild.id)):
                    channel, why = None, 'The configured channel is not the one this request names, so nothing was sent.'
                if not channel:
                    discord_review.record(file, dict(outcome, state='blocked', reason=why)); continue
                r = await asyncio.to_thread(self.reviews.create, req['carousel_id'], agent_id, bool(req.get('test', True)))
                outcome.update(review_id=r['id'], channel_id=str(channel.id), channel_name=channel.name, guild_id=str(channel.guild.id))
            except Exception as e:  # noqa: BLE001 - nothing was sent yet
                discord_review.record(file, dict(outcome, state='not_sent', reason=str(e)[:300])); continue
            try:
                sent = await self.post_review(channel, r)
            except Exception as e:  # noqa: BLE001 - Discord may or may not have posted it, so never retry
                status = getattr(e, 'status', None)
                discord_review.record(file, dict(outcome, state='failed' if status and 400 <= status < 500 else 'uncertain', reason=str(e)[:300]))
                continue
            outcome.update(state='sent', message_id=str(sent.id), jump_url=getattr(sent, 'jump_url', ''))
            try:
                back = await channel.fetch_message(sent.id)
                outcome.update(confirmed=True, confirmed_slide_text=(back.embeds[0].description if back.embeds else '')[:200],
                               confirmed_buttons=sum(len(row.children) for row in back.components))
            except Exception as e:  # noqa: BLE001 - Discord accepted it; the read-back is extra proof
                outcome.update(confirmed=False, confirm_error=str(e)[:200])
            discord_review.record(file, outcome)

    async def all_bots_settled(self, wait=60):
        """Wait until every configured bot has connected or failed, so the audience check knows all of Josh's bots."""
        for _ in range(wait):
            states = [self.status['bots'].get(a, {}) for a in self.config.get('bots', {})]
            if all(st.get('connected') or st.get('error') for st in states):
                return
            await asyncio.sleep(1)

    def review_channel(self, client, agent_id, owner_confirmed=False):
        """This bot's configured channel, only when its whole audience is known to be Josh and his own bots.

        Without the privileged members intent, Discord still reports a server's total member count. If the server's
        owner is Josh and that count equals Josh plus the bots this bridge runs that are in the server, nobody else
        is in the server, so nobody else can see the channel. Anything else, or anything missing, stops the send.
        A single request can carry Josh's own written confirmation of the audience (audience_confirmed_by_owner, with his
        quote). That replaces only the member count check; the owner, channel and posting checks still apply.
        Returns (channel, '', evidence) or (None, why, evidence)."""
        wanted = [str(c).lower().lstrip('#') for c in (self.config['bots'].get(agent_id, {}).get('channels') or [agent_id])]
        found = [ch for g in client.guilds for ch in g.text_channels if ch.name.lower() in wanted or str(ch.id) in wanted]
        if not found:
            return None, 'The configured review channel was not found in any server the bot is in.', {}
        if len(found) > 1:
            return None, 'More than one channel matches the configured name, so nothing was sent.', {}
        ch = found[0]
        g = ch.guild
        ours = sorted(a for a, c in self.clients.items() if getattr(c, 'user', None) and c.get_guild(g.id) is not None)
        owners = self.config.get('owners', [])
        count = getattr(g, 'member_count', None)
        evidence = dict(guild_id=str(g.id), guild_name=getattr(g, 'name', ''), channel_name=ch.name, member_count=count,
                        owner_is_josh=str(getattr(g, 'owner_id', '')) in owners, josh_bots_in_server=ours,
                        everyone_role_can_view=bool(ch.permissions_for(g.default_role).view_channel),
                        unconnected_bots=sorted(a for a in self.config.get('bots', {}) if not self.status['bots'].get(a, {}).get('connected')))
        me = g.me
        if not ch.permissions_for(me).send_messages or not ch.permissions_for(me).attach_files:
            return None, 'The bot cannot post images in the review channel.', evidence
        if not evidence['owner_is_josh']:
            return None, "The server's owner isn't Josh's Discord account, so its audience can't be confirmed.", evidence
        if not isinstance(count, int):
            return None, "Discord didn't report the server's member count, so its audience can't be confirmed.", evidence
        evidence['audience_confirmed_by_owner'] = bool(owner_confirmed)
        if owner_confirmed:
            return ch, '', evidence
        if evidence['unconnected_bots']:
            return None, 'Not every one of Josh\'s bots is connected, so the member count could not be fully accounted for.', evidence
        if count != 1 + len(ours):
            return None, (f'The server has {count} members but only {1 + len(ours)} are accounted for (Josh and '
                          f'{len(ours)} of his bots), so other people may be able to see the channel.'), evidence
        return ch, '', evidence

    # Max's business messages --------------------------------------------------------------------------------------------
    def fixed_channel(self, client, agent_id, settings):
        """The pinned Business channel and the evidence about who can read it. Returns (channel or None, why, evidence)."""
        ev = dict(expected_guild_id=str(settings.get('guild_id') or ''), expected_channel_id=str(settings.get('channel_id') or ''),
                  expected_bot_user_id=str(settings.get('bot_user_id') or ''), found=False)
        if not ev['expected_guild_id'] or not ev['expected_channel_id'].isdigit():
            return None, 'The Business channel is not pinned yet (Data/business/settings.json).', ev
        ev['bot_user_id'] = str(getattr(client.user, 'id', ''))
        ch = client.get_channel(int(ev['expected_channel_id']))
        if ch is not None and getattr(ch, 'guild', None) is not None:
            g = ch.guild
            me = g.me
            perms = ch.permissions_for(me) if me else None
            ev.update(found=True, guild_id=str(g.id), guild_name=getattr(g, 'name', ''), channel_id=str(ch.id), channel_name=ch.name,
                      owner_is_josh=str(getattr(g, 'owner_id', '')) in self.config.get('owners', []),
                      can_send=bool(perms and perms.send_messages and perms.view_channel), member_count=getattr(g, 'member_count', None),
                      everyone_can_view=bool(ch.permissions_for(g.default_role).view_channel),
                      our_bot_ids=sorted(str(c.user.id) for c in self.clients.values() if getattr(c, 'user', None) and c.get_guild(g.id) is not None),
                      unconnected_bots=sorted(a for a in self.config.get('bots', {}) if not self.status['bots'].get(a, {}).get('connected')))
            if client.intents.members and getattr(g, 'chunked', False):
                ev['viewers'] = sorted(str(m.id) for m in ch.members)
        ok, why = business_audience(ev, self.config.get('owners', []))
        return (ch if ok else None), why, ev

    def business_channel_ok(self, client, channel):
        from .business import load_settings
        s = load_settings(self.data_root)
        return str(getattr(channel, 'id', '')) == str(s.get('channel_id')) and self.fixed_channel(client, 'max', s)[0] is not None

    async def _send_fixed(self, client, agent_id, settings, parts):
        import discord
        if settings.get('destination') == 'owner_dm':
            ch, why, ev = await self.business_owner_dm(client, agent_id, settings)
        else:
            ch, why, ev = self.fixed_channel(client, agent_id, settings)
        out = dict(audience=ev, guild_id=ev.get('guild_id'), channel_id=ev.get('channel_id'), message_ids=[])
        if ch is None:
            return dict(out, state='blocked', reason=why)
        for part in parts:
            try:
                m = await ch.send(part, allowed_mentions=discord.AllowedMentions.none())
            except Exception as e:  # noqa: BLE001 - say exactly how far it got
                status = getattr(e, 'status', None)
                if not out['message_ids'] and status and 400 <= status < 500:
                    return dict(out, state='failed', reason=f'Discord refused the message ({str(e)[:150]}). Nothing was posted.')
                return dict(out, state='partial' if out['message_ids'] else 'uncertain',
                            reason=f'Sending stopped after {len(out["message_ids"])} of {len(parts)} messages ({str(e)[:150]}). Not sent again on its own.')
            out['message_ids'].append(str(m.id))
        try:
            for message_id, part in zip(out['message_ids'], parts):
                readback = await ch.fetch_message(int(message_id))
                if (readback.content != part or str(readback.author.id) != str(client.user.id)
                        or str(readback.channel.id) != str(ch.id)):
                    raise ValueError('Discord readback did not match the accepted briefing')
            out['confirmed'] = True
            from .store import now
            out['confirmed_at'] = now()
        except Exception as e:  # noqa: BLE001 - Discord accepted every part; the read-back is extra proof
            out.update(confirmed=False, confirm_error=str(e)[:200])
            return dict(out, state='uncertain', reason='Discord accepted the briefing, but readback could not verify every part. Not resent automatically.')
        return dict(out, state='sent', reason='')

    async def business_owner_dm(self, client, agent_id, settings):
        """Daily briefing only: exact configured owner, pinned Max bot and existing private DM.

        Does not enable the general proactive-DM/notice route or create a new destination.
        """
        import discord
        ev = dict(destination='owner_dm', owner_user_id=str(settings.get('owner_user_id') or ''),
                  bot_user_id=str(getattr(client.user, 'id', '')), channel_id=str(settings.get('channel_id') or ''))
        owners = [str(o) for o in self.config.get('owners', [])]
        if (agent_id != 'max' or agent_id in DISCONNECTED or settings.get('enabled') is not True
                or owners != [ev['owner_user_id']] or not ev['owner_user_id'].isdigit()
                or not ev['channel_id'].isdigit()
                or ev['bot_user_id'] != str(settings.get('bot_user_id') or '')):
            return None, 'Daily owner briefing is not enabled with matching pinned owner, bot and DM.', ev
        try:
            ch = await client.fetch_channel(int(ev['channel_id']))
        except Exception as e:
            return None, f'Could not verify the pinned owner DM ({str(e)[:120]}). Nothing sent.', ev
        if (not isinstance(ch, discord.DMChannel) or str(ch.id) != ev['channel_id']
                or str(getattr(ch.recipient, 'id', '')) != ev['owner_user_id']):
            return None, 'Pinned destination is not a private DM with the configured owner. Nothing sent.', ev
        ev['verified'] = True
        return ch, '', ev

    def send_fixed(self, agent_id, settings, parts, wait=120):
        """Send messages to the pinned channel only, waiting for Discord to accept each one. Never a last-used channel.
        Returns dict(state=sent|blocked|failed|partial|uncertain, reason, message_ids, guild_id, channel_id, audience)."""
        if agent_id not in self.config.get('bots', {}):
            return dict(state='blocked', reason='Max does not have a Discord bot set up in Josh OS yet. Nothing was sent.')
        client = self.clients.get(agent_id)
        if not self.loop or not client or not self.status['bots'].get(agent_id, {}).get('connected'):
            return dict(state='blocked', reason="Max's Discord bot is not connected right now. Nothing was sent.")
        future = asyncio.run_coroutine_threadsafe(self._send_fixed(client, agent_id, settings, list(parts)), self.loop)
        try:
            return future.result(timeout=wait)
        except Exception as e:  # noqa: BLE001 - it may still have posted, so this is never retried on its own
            future.cancel()
            return dict(state='uncertain', reason=f'No answer from Discord in time ({str(e)[:120] or "timeout"}). Not sent again on its own.')

    # Messages the team sends on their own (studio/notices.py) -----------------------------------------------------
    def dm_ready(self, agent_id):
        """Whether this team member's bot can message Josh directly right now. Returns (ok, why not)."""
        if agent_id in DISCONNECTED or agent_id in NO_PROACTIVE_DM:
            return False, 'Proactive direct messages are not enabled for this team member.'
        if agent_id not in self.config.get('bots', {}):
            return False, 'No Discord bot is set up for this team member.'
        if len(self.config.get('owners', [])) != 1:
            return False, "Josh's Discord user id is not set up as the only owner."
        if not self.loop or not self.clients.get(agent_id) or not self.status['bots'].get(agent_id, {}).get('connected'):
            return False, 'The Discord bot is not connected right now.'
        return True, ''

    async def _send_dm(self, client, text):
        import discord
        try:
            user = await client.fetch_user(int(self.config['owners'][0]))
            ch = user.dm_channel or await user.create_dm()
        except Exception as e:  # noqa: BLE001 - nothing was posted yet
            return dict(state='failed', reason=f"Couldn't open a direct message with Josh ({str(e)[:150]}). Nothing was posted.", message_ids=[])
        ids = []
        parts = split(text)
        for part in parts:
            try:
                m = await ch.send(part, allowed_mentions=discord.AllowedMentions.none())
            except Exception as e:  # noqa: BLE001 - say exactly how far it got
                status = getattr(e, 'status', None)
                if not ids and status and 400 <= status < 500:
                    return dict(state='failed', reason=f'Discord refused the message ({str(e)[:150]}). Nothing was posted.', message_ids=ids)
                return dict(state='partial' if ids else 'uncertain', message_ids=ids, channel_id=str(ch.id),
                            reason=f'Sending stopped after {len(ids)} of {len(parts)} messages ({str(e)[:150]}). Not sent again on its own.')
            ids.append(str(m.id))
        return dict(state='sent', reason='', message_ids=ids, channel_id=str(ch.id))

    def dm_owner(self, agent_id):
        """The one owner a DM from this bot goes to (send_dm only sends when there is exactly one)."""
        owners = self.config.get('owners', [])
        return str(owners[0]) if len(owners) == 1 else None

    def send_dm(self, agent_id, text, wait=60):
        """Send one message to Josh alone, as a direct message from this team member's own bot. Never a channel.
        Returns dict(state=sent|failed|partial|uncertain|blocked, reason, message_ids)."""
        ok, why = self.dm_ready(agent_id)
        if not ok:
            return dict(state='blocked', reason=why + ' Nothing was sent.', message_ids=[])
        future = asyncio.run_coroutine_threadsafe(self._send_dm(self.clients[agent_id], text), self.loop)
        try:
            return future.result(timeout=wait)
        except Exception as e:  # noqa: BLE001 - it may still have posted, so this is never retried on its own
            future.cancel()
            return dict(state='uncertain', reason=f'No answer from Discord in time ({str(e)[:120] or "timeout"}). Not sent again on its own.',
                        message_ids=[])

    # Tariq's shot shortlist, sent only when Josh asks ------------------------------------------------------------
    def send_shortlist(self, project_id, edit_review, test=False, wait=90):
        """Send Tariq's shot shortlist for one video as a review, from Tariq's own bot only. Returns a line for the chat.

        It goes to the Discord channel Josh last wrote to Tariq in (which already passed the owner check), or else
        Tariq's configured channel if its whole audience is Josh and his bots. It never falls back to another bot.
        The same version isn't sent twice within ten minutes, and a send with an unclear outcome isn't retried."""
        from . import shot_review
        agent_id = 'tariq'
        if agent_id not in self.config.get('bots', {}):
            return "I'm not connected to Discord yet, so I couldn't send the shortlist. Nothing was sent."
        client = self.clients.get(agent_id)
        if not self.loop or not client or not self.status['bots'].get(agent_id, {}).get('connected'):
            return "My Discord connection isn't up right now, so I couldn't send the shortlist. Nothing was sent."
        try:
            shortlist = shot_review.build(edit_review, project_id)
        except (KeyError, ValueError, OSError) as e:
            return f"I couldn't put the shortlist together: {str(e).strip(chr(39))[:200]} Nothing was sent."
        again = self.reviews.recent(project_id, shortlist['source'])
        if again:
            return f"I sent this exact shortlist a few minutes ago, so I didn't send it again: {again.get('jump_url') or 'see Discord'}"
        channel = self.channels.get(agent_id)
        if channel is None:
            channel, why, _ = self.review_channel(client, agent_id)
            if channel is None:
                return f"I didn't send the shortlist. {why} Message me on Discord and ask there, and I'll send it to that chat."
        if not shot_review.same_source(edit_review, project_id, shortlist['source']):
            return "The deck or shot list changed while I was getting the shortlist ready, so I didn't send it. Ask me again."
        r = self.reviews.create_shots(shortlist, agent_id, test)
        future = asyncio.run_coroutine_threadsafe(self.post_review(channel, r), self.loop)
        try:
            sent = future.result(timeout=wait)
        except Exception as e:  # noqa: BLE001 - Discord may or may not have posted it, so never retry
            status = getattr(e, 'status', None)
            if status and 400 <= status < 500:
                return f"Discord refused the shortlist ({str(e)[:150]}). Nothing was sent."
            return ("I'm not sure the shortlist went through, so I won't send it again on my own. Check Discord, and ask me "
                    "again if it isn't there.")
        items = shortlist['items']
        found = sum(p['found'] for i in items for p in i['presses'])
        total = sum(len(i['presses']) for i in items)
        talk = sum(1 for i in items if i.get('kind') == 'talk')
        pics = sum(1 for i in items if i.get('kind') != 'talk' and i['picture'])
        cov = shortlist['source'].get('coverage') or {}
        covered = 'the whole script' if cov.get('complete') else f"{cov.get('matched', '?')} of {cov.get('script_words', '?')} script words"
        return (f"Sent the shortlist for {shortlist['name']} on Discord as one message you page through: {len(items)} shots "
                f"({talk} talking head, {len(items) - talk} animations, {pics} of them with their finished picture), {found} of {total} "
                f"press cues marked, covering {covered}, from {shortlist['source']['shot_list']}. {getattr(sent, 'jump_url', '')}").strip()
