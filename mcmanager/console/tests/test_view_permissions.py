import pytest
from django.test import Client
from django.urls import reverse

from mcmanager.console.models import Server, Type


@pytest.fixture
def server(db):
    server_type = Type.objects.create(name="Vanilla")
    return Server.objects.create(
        name="Test", jar_template="paper.jar", jar="1_paper.jar", type=server_type
    )


@pytest.fixture
def staff_user(django_user_model):
    return django_user_model.objects.create_user(
        username="admin", password="pw", is_staff=True
    )


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["index", "view_logs", "get_server_stats"])
def test_get_only_control_views_require_staff_login(client, server, url_name):
    response = client.get(reverse(url_name, kwargs={"id": server.id}))
    assert response.status_code == 302


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["start_server", "stop_server", "force_stop_server", "send_command"])
def test_mutating_views_require_staff_login(client, server, url_name):
    response = client.post(reverse(url_name, kwargs={"id": server.id}))
    assert response.status_code == 302


@pytest.mark.django_db
def test_home_requires_staff_login(client):
    response = client.get(reverse("home"))
    assert response.status_code == 302


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["start_server", "stop_server", "force_stop_server", "send_command"])
def test_mutating_views_reject_get_from_staff_user(client, server, staff_user, url_name):
    client.force_login(staff_user)
    response = client.get(reverse(url_name, kwargs={"id": server.id}))
    assert response.status_code == 405


@pytest.mark.django_db
def test_send_command_rejects_missing_csrf_token(server, staff_user):
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(staff_user)
    response = csrf_client.post(
        reverse("send_command", kwargs={"id": server.id}), {"command": "say hi"}
    )
    assert response.status_code == 403
