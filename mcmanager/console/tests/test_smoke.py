import pytest
from django.conf import settings

from mcmanager.console.models import Type


@pytest.mark.django_db
def test_can_create_type():
    server_type = Type.objects.create(name="Vanilla")
    assert server_type.pk is not None
    assert str(server_type) == "Vanilla"


def test_user_data_dir_is_an_isolated_temp_dir():
    assert "mcmanager-tests-" in str(settings.USER_DATA_DIR)
