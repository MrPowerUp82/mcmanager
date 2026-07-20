import pytest

from mcmanager.console.models import Server, Type


@pytest.fixture
def staff_client(client, django_user_model):
    staff = django_user_model.objects.create_user(username="admin", password="pw", is_staff=True)
    client.force_login(staff)
    return client


@pytest.fixture
def server_with_log(settings, tmp_path):
    settings.SERVERS_DIR = tmp_path / "servers"
    settings.SERVERS_DIR.mkdir()
    server_type = Type.objects.create(name="Vanilla")
    server = Server.objects.create(name="Test", jar_template="paper.jar", jar="1_paper.jar", port=25566, type=server_type)
    log_dir = settings.SERVERS_DIR / f"server_{server.id}" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "latest.log").write_text("line one\nline two\n", encoding="utf-8", newline="\n")
    return server


@pytest.mark.django_db
def test_view_logs_without_offset_returns_whole_file(staff_client, server_with_log):
    resp = staff_client.get(f"/console/view_logs/{server_with_log.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["logs"] == "line one\nline two\n"
    assert body["offset"] == len("line one\nline two\n".encode("utf-8"))


@pytest.mark.django_db
def test_view_logs_with_offset_returns_only_new_content(settings, staff_client, server_with_log):
    first_line_bytes = len("line one\n".encode("utf-8"))

    resp = staff_client.get(f"/console/view_logs/{server_with_log.id}?offset={first_line_bytes}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["logs"] == "line two\n"
    assert body["offset"] == len("line one\nline two\n".encode("utf-8"))


@pytest.mark.django_db
def test_view_logs_offset_past_end_of_truncated_file_restarts_from_zero(settings, staff_client, server_with_log):
    log_path = settings.SERVERS_DIR / f"server_{server_with_log.id}" / "logs" / "latest.log"
    huge_offset = 999999

    resp = staff_client.get(f"/console/view_logs/{server_with_log.id}?offset={huge_offset}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["logs"] == "line one\nline two\n"
    assert body["offset"] == log_path.stat().st_size


@pytest.mark.django_db
def test_view_logs_invalid_offset_falls_back_to_zero(staff_client, server_with_log):
    resp = staff_client.get(f"/console/view_logs/{server_with_log.id}?offset=not-a-number")

    assert resp.status_code == 200
    body = resp.json()
    assert body["logs"] == "line one\nline two\n"


@pytest.mark.django_db
def test_view_logs_negative_offset_falls_back_to_zero(staff_client, server_with_log):
    resp = staff_client.get(f"/console/view_logs/{server_with_log.id}?offset=-5")

    assert resp.status_code == 200
    body = resp.json()
    assert body["logs"] == "line one\nline two\n"


@pytest.mark.django_db
def test_view_logs_missing_file_returns_error(settings, staff_client, tmp_path):
    settings.SERVERS_DIR = tmp_path / "servers"
    settings.SERVERS_DIR.mkdir()
    server_type = Type.objects.create(name="Vanilla")
    server = Server.objects.create(name="NoLog", jar_template="paper.jar", jar="2_paper.jar", port=25567, type=server_type)

    resp = staff_client.get(f"/console/view_logs/{server.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["message"] == "Log file not found"


@pytest.mark.django_db
def test_view_logs_requires_staff_login(client, server_with_log):
    resp = client.get(f"/console/view_logs/{server_with_log.id}")
    assert resp.status_code == 302
