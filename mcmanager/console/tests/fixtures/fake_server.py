"""Stand-in for `java -jar server.jar nogui`, used only by mcmanager's own
test suite. If a server.properties file with enable-rcon=true exists in the
current working directory, it also serves a minimal RCON endpoint on
rcon.port/rcon.password from that file, mirroring how process.py's
stop()/send_command() interact with a real Minecraft server."""
import os
import signal
import socket
import struct
import threading
import time


def _read_server_properties():
    properties = {}
    if os.path.exists('server.properties'):
        with open('server.properties', 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                properties[key] = value
    return properties


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


def _serve_rcon(port, password, stop_event):
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(('127.0.0.1', port))
    server_sock.listen(1)
    server_sock.settimeout(1.0)
    while not stop_event.is_set():
        try:
            conn, _addr = server_sock.accept()
        except socket.timeout:
            continue
        with conn:
            packet = _read_packet(conn)
            if packet is None:
                continue
            request_id, _packet_type, payload = packet
            if payload.decode('utf-8', errors='replace') != password:
                _send_packet(conn, -1, 2, b'')
                continue
            _send_packet(conn, request_id, 2, b'')
            while True:
                packet = _read_packet(conn)
                if packet is None:
                    break
                request_id, _packet_type, payload = packet
                command = payload.decode('utf-8', errors='replace')
                if command == 'stop':
                    _send_packet(conn, request_id, 0, b'Stopping the server')
                    stop_event.set()
                    return
                _send_packet(conn, request_id, 0, f'Unknown command: {command}'.encode('utf-8'))


def main():
    print(f'fake_server started, pid={os.getpid()}', flush=True)
    properties = _read_server_properties()
    stop_event = threading.Event()

    if properties.get('enable-rcon') == 'true':
        rcon_port = int(properties['rcon.port'])
        rcon_password = properties['rcon.password']
        rcon_thread = threading.Thread(
            target=_serve_rcon, args=(rcon_port, rcon_password, stop_event), daemon=True
        )
        rcon_thread.start()

    if hasattr(signal, 'SIGTERM'):
        try:
            signal.signal(signal.SIGTERM, lambda *_a: stop_event.set())
        except (ValueError, OSError):
            pass

    while not stop_event.is_set():
        time.sleep(0.1)
    print('fake_server stopping', flush=True)


if __name__ == '__main__':
    main()
