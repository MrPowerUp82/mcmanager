"""Self-implemented Source RCON protocol client, used to send commands to and
gracefully stop a running Minecraft server. No Django or Server model
knowledge lives here — it's a pure network protocol module."""
import socket
import struct

DEFAULT_TIMEOUT = 5.0

_SERVERDATA_AUTH = 3
_SERVERDATA_EXECCOMMAND = 2


class RconError(Exception):
    """Base class for RCON errors."""


class RconAuthError(RconError):
    """Raised when RCON authentication fails (wrong password)."""


class RconConnectionError(RconError):
    """Raised when the RCON socket can't be opened or the connection drops."""


class RconTimeoutError(RconError):
    """Raised when the RCON server doesn't respond within the timeout."""


def _recv_exact(sock, n):
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RconConnectionError("RCON connection closed unexpectedly")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b''.join(chunks)


def _read_packet(sock):
    raw_len = _recv_exact(sock, 4)
    length = struct.unpack('<i', raw_len)[0]
    body = _recv_exact(sock, length)
    request_id, packet_type = struct.unpack('<ii', body[:8])
    payload = body[8:-2]
    return request_id, packet_type, payload


def _send_packet(sock, packet_type, payload):
    request_id = 1
    body = struct.pack('<ii', request_id, packet_type) + payload + b'\x00\x00'
    sock.sendall(struct.pack('<i', len(body)) + body)


def execute(host: str, port: int, password: str, command: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Opens a new RCON connection, authenticates, sends `command`, and
    returns the server's response text. Raises RconAuthError,
    RconConnectionError, or RconTimeoutError on failure."""
    # The connect step is handled separately from auth/command exchange: a
    # timeout here means the server was unreachable (connection error), while
    # a timeout after connecting means the server accepted the connection but
    # didn't respond in time (timeout error). Both raise socket.timeout /
    # TimeoutError, so they can't be told apart by exception type alone.
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise RconConnectionError(f"RCON connection to {host}:{port} failed: {exc}") from exc

    try:
        with sock:
            sock.settimeout(timeout)
            _send_packet(sock, _SERVERDATA_AUTH, password.encode('utf-8'))
            request_id, _packet_type, _payload = _read_packet(sock)
            if request_id == -1:
                raise RconAuthError("RCON authentication failed")

            _send_packet(sock, _SERVERDATA_EXECCOMMAND, command.encode('utf-8'))
            _request_id, _packet_type, payload = _read_packet(sock)
            return payload.decode('utf-8', errors='replace')
    except RconAuthError:
        raise
    except socket.timeout as exc:
        raise RconTimeoutError(f"RCON request to {host}:{port} timed out after {timeout}s") from exc
    except OSError as exc:
        raise RconConnectionError(f"RCON connection to {host}:{port} failed: {exc}") from exc
