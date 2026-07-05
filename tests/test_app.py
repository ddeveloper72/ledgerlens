def test_app_creation(client):
    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Monthly Income" in body
    assert "Monthly Spending" in body
    assert "Top Categories (Monthly Spending)" in body
