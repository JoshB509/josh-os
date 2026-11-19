"""Origin connection queue regression; no live app or background workers."""
import socket
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from studio.server import Server

class OriginBacklogTests(unittest.TestCase):
    def test_page_asset_burst_queues_before_accept(self):
        # Deliberately pause accepts, modelling a short scheduler stall while the
        # tunnel opens connections for all assets. Do not construct live Server.
        class IsolatedServer(ThreadingHTTPServer):
            request_queue_size = Server.request_queue_size
        self.assertGreaterEqual(Server.request_queue_size, 64)
        with IsolatedServer(('127.0.0.1', 0), BaseHTTPRequestHandler) as server:
            clients = []
            try:
                for _ in range(64):
                    clients.append(socket.create_connection(server.server_address, timeout=1))
                self.assertEqual(len(clients), 64)
            finally:
                for client in clients:
                    client.close()
