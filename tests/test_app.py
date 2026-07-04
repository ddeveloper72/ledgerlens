def test_app_creation(client):
    response = client.get("/")
    assert response.status_code == 200
