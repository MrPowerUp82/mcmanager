import json

from mcmanager.console.json_utils import json_error


def test_json_error_sets_status_and_http_code():
    response = json_error('Something broke', status=503)
    assert response.status_code == 503
    body = json.loads(response.content)
    assert body['status'] == 'error'
    assert body['message'] == 'Something broke'


def test_json_error_defaults_to_400():
    response = json_error('Bad input')
    assert response.status_code == 400
