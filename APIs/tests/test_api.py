# 简单冒烟测试：先用 httpx 直接调用 app，不需要真的启动端口。
import os
import tempfile

os.environ["ADMIN_KEY"] = "test-key"
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


def test_health():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_login_bad_key():
    r = client.post("/login", data={"admin_key": "wrong"})
    assert r.status_code == 401


def test_put_and_get_with_cookie():
    r = client.post("/login", data={"admin_key": "test-key"})
    assert r.status_code == 200
    assert "admin_session" in r.cookies

    account = {
        "id": "user@example.com",
        "csesidx": "csesidx",
        "config_id": "config_id",
        "secure_c_ses": "secure_c_ses",
        "host_c_oses": "host_c_oses",
        "expires_at": "2030-01-01 00:00:00",
    }

    r = client.put("/admin/accounts-config", json=[account])
    assert r.status_code == 200

    r = client.get("/admin/accounts-config")
    assert r.status_code == 200
    assert r.json()["accounts"][0]["id"] == "user@example.com"


def test_put_requires_auth():
    from fastapi.testclient import TestClient as TC
    anon = TC(app)
    r = anon.put("/admin/accounts-config", json=[])
    assert r.status_code == 401
