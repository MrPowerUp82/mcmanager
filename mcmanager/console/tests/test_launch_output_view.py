from unittest.mock import patch

import pytest
from django.urls import reverse

from mcmanager.console.models import Server, Type


@pytest.fixture
def staff_client(client, django_user_model):
    staff = django_user_model.objects.create_user(username="admin", password="pw", is_staff=True)
    client.force_login(staff)
    return client


@pytest.fixture
def server(db):
    server_type = Type.objects.create(name="Vanilla")
    return Server.objects.create(name="Test", jar_template="paper.jar", type=server_type)


@pytest.mark.django_db
def test_launch_output_requires_staff_login(client, server):
    resp = client.get(reverse("launch_output", args=[server.id]))
    assert resp.status_code in (302, 403)


@pytest.mark.django_db
def test_launch_output_returns_captured_content(staff_client, server):
    with patch(
        "mcmanager.console.views.process.read_launch_output",
        return_value="FAKE JAVA ERROR: test crash message\n",
    ):
        resp = staff_client.get(reverse("launch_output", args=[server.id]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert "FAKE JAVA ERROR" in body["output"]


@pytest.mark.django_db
def test_launch_output_returns_404_when_not_captured_yet(staff_client, server):
    with patch("mcmanager.console.views.process.read_launch_output", return_value=None):
        resp = staff_client.get(reverse("launch_output", args=[server.id]))

    assert resp.status_code == 404
    assert resp.json()["status"] == "error"
