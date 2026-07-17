import socket
import struct
import threading


def _recv_exact(sock, n):
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return b''.join(chunks)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


def _read_packet(sock):
    raw_len = _recv_exact(sock, 4)
    if len(raw_len) < 4:
        return None
    length = struct.unpack('<i', raw_len)[0]
    body = _recv_exact(sock, length)
    request_id, packet_type = struct.unpack('<ii', body[:8])
    payload = body[8:-2]
    return request_id, packet_type, payload


def _send_packet(sock, request_id, packet_type, payload):
    body = struct.pack('<ii', request_id, packet_type) + payload + b'\x00\x00'
    sock.sendall(struct.pack('<i', len(body)) + body)


class FakeRconServer:
    """A minimal RCON server for tests: accepts connections, checks the
    configured password, and echoes back a canned response per command."""

    def __init__(self, password, responses=None):
        self.password = password
        self.responses = responses or {}
        self.received_commands = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(('127.0.0.1', 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve_forever, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._sock.close()

    def _serve_forever(self):
        while True:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                return
            self._handle_connection(conn)

    def _handle_connection(self, conn):
        with conn:
            packet = _read_packet(conn)
            if packet is None:
                return
            request_id, _packet_type, payload = packet
            if payload.decode('utf-8', errors='replace') != self.password:
                _send_packet(conn, -1, 2, b'')
                return
            _send_packet(conn, request_id, 2, b'')
            while True:
                packet = _read_packet(conn)
                if packet is None:
                    return
                request_id, _packet_type, payload = packet
                command = payload.decode('utf-8', errors='replace')
                self.received_commands.append(command)
                response = self.responses.get(command, '')
                _send_packet(conn, request_id, 0, response.encode('utf-8'))
