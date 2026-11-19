"""Daily-only owner destination and readback; no real Discord calls."""
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import discord
from studio.discord_bridge import Bridge, NO_PROACTIVE_DM

class OwnerBrief(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge.__new__(Bridge)
        self.bridge.config = {'owners': ['111'], 'bots': {'max': {}}}
        self.settings = dict(enabled=True, destination='owner_dm', owner_user_id='111', bot_user_id='222', channel_id='333')
        self.channel = Mock(spec=discord.DMChannel)
        self.channel.id = 333
        self.channel.recipient = NS(id=111)
        self.channel.send = AsyncMock(return_value=NS(id=444))
        self.channel.fetch_message = AsyncMock(return_value=NS(content='brief', author=NS(id=222), channel=NS(id=333)))
        self.client = NS(user=NS(id=222), fetch_channel=AsyncMock(return_value=self.channel))

    def send(self, **changes):
        return asyncio.run(self.bridge._send_fixed(self.client, 'max', dict(self.settings, **changes), ['brief']))

    def test_verified_owner_dm_and_every_part_readback(self):
        result = self.send()
        self.assertEqual(result['state'], 'sent')
        self.assertTrue(result['confirmed'])
        self.assertTrue(result['confirmed_at'])
        self.assertEqual(result['message_ids'], ['444'])
        self.channel.fetch_message.assert_awaited_once_with(444)

    def test_disabled_or_wrong_pins_block_before_send(self):
        for changes in [dict(enabled=False), dict(owner_user_id='999'), dict(bot_user_id='999'), dict(channel_id='')]:
            with self.subTest(changes=changes):
                self.assertEqual(self.send(**changes)['state'], 'blocked')
        self.channel.send.assert_not_awaited()

    def test_other_agents_cannot_use_daily_exception(self):
        result=asyncio.run(self.bridge.business_owner_dm(self.client, 'sam', self.settings))
        self.assertIsNone(result[0])
        self.channel.send.assert_not_awaited()

    def test_multiple_owners_block(self):
        self.bridge.config['owners'].append('999')
        self.assertEqual(self.send()['state'], 'blocked')

    def test_group_or_guild_channel_blocks(self):
        self.client.fetch_channel.return_value=NS(id=333, recipient=NS(id=111))
        self.assertEqual(self.send()['state'], 'blocked')

    def test_wrong_recipient_or_channel_blocks(self):
        self.channel.recipient.id=999
        self.assertEqual(self.send()['state'], 'blocked')
        self.channel.recipient.id=111
        self.channel.id=999
        self.assertEqual(self.send()['state'], 'blocked')

    def test_readback_failure_or_mismatch_is_uncertain_with_ids(self):
        for message in [NS(content='other', author=NS(id=222), channel=NS(id=333)), NS(content='brief', author=NS(id=999), channel=NS(id=333))]:
            self.channel.fetch_message.return_value=message
            result=self.send()
            self.assertEqual(result['state'], 'uncertain')
            self.assertEqual(result['message_ids'], ['444'])
            self.assertFalse(result['confirmed'])
        self.channel.fetch_message.side_effect=TimeoutError()
        self.assertEqual(self.send()['state'], 'uncertain')

    def test_general_proactive_dm_remains_blocked(self):
        self.assertIn('max', NO_PROACTIVE_DM)
        self.assertFalse(self.bridge.dm_ready('max')[0])

    def test_every_message_is_read_back(self):
        self.channel.send.side_effect=[NS(id=444), NS(id=445)]
        self.channel.fetch_message.side_effect=[NS(content='first', author=NS(id=222), channel=NS(id=333)), NS(content='second', author=NS(id=222), channel=NS(id=333))]
        r=asyncio.run(self.bridge._send_fixed(self.client, 'max', self.settings, ['first','second']))
        self.assertEqual(r['state'], 'sent')
        self.assertEqual(self.channel.fetch_message.await_count, 2)

    def test_max_resume_restores_readiness_without_proactive_reviews(self):
        events={}
        fake=NS(user='Max#9735',event=lambda fn: events.setdefault(fn.__name__,fn))
        self.bridge.clients={}
        self.bridge.status={'bots':{}}
        self.bridge.data_root='unused'
        self.bridge.send_requested_reviews=AsyncMock()
        with patch('discord.Client',return_value=fake), patch('studio.business.load_settings',return_value={}):
            asyncio.run(self.bridge._bot('max',{'token':''}))
        asyncio.run(events['on_ready']())
        asyncio.run(events['on_disconnect']())
        self.assertFalse(self.bridge.status['bots']['max']['connected'])
        asyncio.run(events['on_resumed']())
        self.assertTrue(self.bridge.status['bots']['max']['connected'])
        self.bridge.send_requested_reviews.assert_not_awaited()

if __name__ == '__main__': unittest.main()
