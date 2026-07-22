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
def test_dashboard_data_requires_staff_login(client, server):
    response = client.get(reverse("dashboard_data"))
    assert response.status_code in (302, 403)


@pytest.mark.django_db
def test_dashboard_data_returns_server_list_json(staff_client, server):
    with patch(
        "mcmanager.console.views.dashboard.get_dashboard_data",
        return_value=[{
            'server': server,
            'running': True,
            'stats_available': True,
            'cpu_usage': 7.5,
            'memory_usage': 128.0,
            'players_available': True,
            'players_raw': 'There are 0 of a max of 20 players online: ',
        }],
    ):
        response = staff_client.get(reverse("dashboard_data"))

    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'success'
    assert len(data['servers']) == 1
    entry = data['servers'][0]
    assert entry['id'] == server.id
    assert entry['name'] == server.name
    assert entry['running'] is True
    assert entry['stats_available'] is True
    assert entry['cpu_usage'] == 7.5
    assert entry['memory_usage'] == 128.0
    assert entry['players_available'] is True
    assert entry['players_raw'] == 'There are 0 of a max of 20 players online: '


@pytest.mark.django_db
def test_dashboard_data_handles_stopped_server_with_no_stats_fields(staff_client, server):
    with patch(
        "mcmanager.console.views.dashboard.get_dashboard_data",
        return_value=[{'server': server, 'running': False}],
    ):
        response = staff_client.get(reverse("dashboard_data"))

    assert response.status_code == 200
    entry = response.json()['servers'][0]
    assert entry['running'] is False
    assert entry['stats_available'] is False
    assert entry['players_available'] is False
    assert entry['cpu_usage'] is None
    assert entry['players_raw'] is None
