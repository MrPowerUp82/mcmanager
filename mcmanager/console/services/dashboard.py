"""Aggregates per-server status/stats/player-count for the dashboard view.
Each running server's data is collected in its own thread so one slow or
unreachable server doesn't add its latency to every other server's; a
failure for one server never prevents the others from reporting."""
from concurrent.futures import ThreadPoolExecutor

from ..models import Server
from . import process

STATS_CPU_INTERVAL = 0.2


def get_dashboard_data():
    servers = list(Server.objects.all())
    running = {s.id: process.is_running(s) for s in servers}
    running_servers = [s for s in servers if running[s.id]]

    details = {}
    if running_servers:
        with ThreadPoolExecutor(max_workers=len(running_servers)) as pool:
            collected = pool.map(_collect_running_server_data, running_servers)
        details = dict(zip((s.id for s in running_servers), collected))

    return [
        {
            'server': s,
            'running': running[s.id],
            'jar_missing': process.is_jar_missing(s),
            **(details.get(s.id) or {}),
        }
        for s in servers
    ]


def _collect_running_server_data(server):
    result = {'stats_available': False, 'players_available': False}
    try:
        stats = process.get_stats(server, cpu_interval=STATS_CPU_INTERVAL)
        result['cpu_usage'] = stats['cpu_usage']
        result['memory_usage'] = stats['memory_usage']
        result['stats_available'] = True
    except Exception:
        pass
    try:
        result['players_raw'] = process.send_command(server, 'list')
        result['players_available'] = True
    except Exception:
        pass
    return result
