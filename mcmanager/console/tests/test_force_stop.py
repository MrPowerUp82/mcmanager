import os
import subprocess
import sys

import psutil
import pytest
from django.test import Client

from mcmanager.console.models import Server, Type


@pytest.mark.django_db
@pytest.mark.skipif(os.name != "posix", reason="force_stop_server uses /tmp PID files (POSIX-only until Phase 2)")
def test_force_stop_server_kills_process_without_shelling_out(django_user_model):
    server_type = Type.objects.create(name="Vanilla")
    server = Server.objects.create(
        name="Test", jar_template="paper.jar", jar="1_paper.jar", type=server_type
    )
    staff_user = django_user_model.objects.create_user(
        username="admin", password="pw", is_staff=True
    )

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    pid_file = f"/tmp/minecraft_server_{server.id}.pid"
    with open(pid_file, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))

    try:
        client = Client()
        client.force_login(staff_user)
        response = client.post(f"/console/force_stop_server/{server.id}")

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        proc.wait(timeout=5)
        assert not psutil.pid_exists(proc.pid)
    finally:
        if os.path.exists(pid_file):
            os.remove(pid_file)
        if proc.poll() is None:
            proc.kill()
