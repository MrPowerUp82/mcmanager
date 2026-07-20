from unittest.mock import patch

import pytest
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
def test_home_shows_off_for_non_running_server(client, server, staff_user):
    client.force_login(staff_user)
    with patch("mcmanager.console.views.process.is_running", return_value=False):
        response = client.get(reverse("home"))
    assert response.status_code == 200
    content = response.content.decode()
    assert server.name in content
    assert "(OFF)" in content


@pytest.mark.django_db
def test_home_shows_on_for_running_server_using_live_process_state(client, server, staff_user):
    client.force_login(staff_user)
    with patch("mcmanager.console.views.process.is_running", return_value=True):
        response = client.get(reverse("home"))
    assert response.status_code == 200
    content = response.content.decode()
    assert server.name in content
    assert "(ON)" in content


def test_server_model_has_no_status_field():
    field_names = {f.name for f in Server._meta.get_fields()}
    assert "status" not in field_names
