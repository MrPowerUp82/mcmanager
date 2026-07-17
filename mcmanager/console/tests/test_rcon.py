import socket
import threading
import time

import pytest

from mcmanager.console.services import rcon
from mcmanager.console.tests.fixtures.fake_rcon_server import FakeRconServer


@pytest.fixture
def fake_rcon_server():
    server = FakeRconServer(password="secret123", responses={"say hi": "Said: hi"})
    server.start()
    yield server
    server.stop()


def test_execute_returns_command_response(fake_rcon_server):
    response = rcon.execute("127.0.0.1", fake_rcon_server.port, "secret123", "say hi")
    assert response == "Said: hi"
    assert fake_rcon_server.received_commands == ["say hi"]


def test_execute_raises_auth_error_on_wrong_password(fake_rcon_server):
    with pytest.raises(rcon.RconAuthError):
        rcon.execute("127.0.0.1", fake_rcon_server.port, "wrong-password", "say hi")


def test_execute_raises_connection_error_when_nothing_listening():
    with pytest.raises(rcon.RconConnectionError):
        rcon.execute("127.0.0.1", 1, "secret123", "say hi", timeout=1.0)


def test_execute_raises_timeout_error_when_server_never_responds():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    held_connections = []

    def accept_and_hang():
        try:
            conn, _addr = listener.accept()
            held_connections.append(conn)
            time.sleep(5)
        except OSError:
            pass

    thread = threading.Thread(target=accept_and_hang, daemon=True)
    thread.start()

    try:
        with pytest.raises(rcon.RconTimeoutError):
            rcon.execute("127.0.0.1", port, "secret123", "say hi", timeout=0.5)
    finally:
        listener.close()
        for c in held_connections:
            c.close()
