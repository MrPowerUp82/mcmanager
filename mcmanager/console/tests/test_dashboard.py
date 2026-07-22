from unittest.mock import patch

import pytest

from mcmanager.console.models import Server, Type
from mcmanager.console.services import dashboard, process


@pytest.fixture
def server_type(db):
    return Type.objects.create(name="Vanilla")


@pytest.fixture
def stopped_server(server_type):
    return Server.objects.create(name="Stopped", jar_template="paper.jar", port=25570, type=server_type)


@pytest.fixture
def running_server(server_type):
    return Server.objects.create(name="Running", jar_template="paper.jar", port=25571, type=server_type)


@pytest.mark.django_db
def test_stopped_server_has_no_stats_or_players_and_is_not_polled(stopped_server):
    with patch("mcmanager.console.services.dashboard.process.is_running", return_value=False), \
         patch("mcmanager.console.services.dashboard.process.get_stats") as mock_get_stats, \
         patch("mcmanager.console.services.dashboard.process.send_command") as mock_send_command:
        result = dashboard.get_dashboard_data()

    assert len(result) == 1
    entry = result[0]
    assert entry['server'] == stopped_server
    assert entry['running'] is False
    assert entry.get('stats_available', False) is False
    assert entry.get('players_available', False) is False
    mock_get_stats.assert_not_called()
    mock_send_command.assert_not_called()


@pytest.mark.django_db
def test_running_server_with_working_stats_and_rcon_reports_both(running_server):
    with patch("mcmanager.console.services.dashboard.process.is_running", return_value=True), \
         patch(
             "mcmanager.console.services.dashboard.process.get_stats",
             return_value={'cpu_usage': 12.5, 'memory_usage': 256.0, 'total_memory': 1024.0,
                           'used_memory': 512.0, 'total_cpu_usage': 30.0},
         ) as mock_get_stats, \
         patch(
             "mcmanager.console.services.dashboard.process.send_command",
             return_value='There are 1 of a max of 20 players online: Steve',
         ):
        result = dashboard.get_dashboard_data()

    entry = result[0]
    assert entry['running'] is True
    assert entry['stats_available'] is True
    assert entry['cpu_usage'] == 12.5
    assert entry['memory_usage'] == 256.0
    assert entry['players_available'] is True
    assert entry['players_raw'] == 'There are 1 of a max of 20 players online: Steve'
    mock_get_stats.assert_called_once_with(running_server, cpu_interval=dashboard.STATS_CPU_INTERVAL)


@pytest.mark.django_db
def test_rcon_failure_marks_only_players_unavailable_stats_still_reported(running_server):
    with patch("mcmanager.console.services.dashboard.process.is_running", return_value=True), \
         patch(
             "mcmanager.console.services.dashboard.process.get_stats",
             return_value={'cpu_usage': 5.0, 'memory_usage': 100.0, 'total_memory': 1024.0,
                           'used_memory': 512.0, 'total_cpu_usage': 10.0},
         ), \
         patch(
             "mcmanager.console.services.dashboard.process.send_command",
             side_effect=Exception("RCON connection refused"),
         ):
        result = dashboard.get_dashboard_data()

    entry = result[0]
    assert entry['stats_available'] is True
    assert entry['cpu_usage'] == 5.0
    assert entry.get('players_available', False) is False


@pytest.mark.django_db
def test_stats_failure_for_one_server_does_not_prevent_other_servers_reporting(server_type):
    broken = Server.objects.create(name="Broken", jar_template="paper.jar", port=25572, type=server_type)
    healthy = Server.objects.create(name="Healthy", jar_template="paper.jar", port=25573, type=server_type)

    def fake_get_stats(server, cpu_interval):
        if server.id == broken.id:
            raise process.ProcessNotRunningError("race: process exited")
        return {'cpu_usage': 1.0, 'memory_usage': 10.0, 'total_memory': 1024.0,
                'used_memory': 512.0, 'total_cpu_usage': 2.0}

    with patch("mcmanager.console.services.dashboard.process.is_running", return_value=True), \
         patch("mcmanager.console.services.dashboard.process.get_stats", side_effect=fake_get_stats), \
         patch("mcmanager.console.services.dashboard.process.send_command", return_value='There are 0 of a max of 20 players online: '):
        result = dashboard.get_dashboard_data()

    by_id = {entry['server'].id: entry for entry in result}
    assert by_id[broken.id].get('stats_available', False) is False
    assert by_id[healthy.id]['stats_available'] is True
    assert by_id[healthy.id]['cpu_usage'] == 1.0
