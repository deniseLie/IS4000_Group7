from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


# Check that the FastAPI backend is running and responding
def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
