import os
import re
import sys
import uuid
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _setup_app_with_temp_db(monkeypatch):
    db_dir = Path(ROOT) / "_workspace" / "test_dbs"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"test_register_confirm_{uuid.uuid4().hex}.db"

    monkeypatch.setenv("WHATUDOIN_BASE_DIR", ROOT)
    monkeypatch.setenv("WHATUDOIN_RUN_DIR", ROOT)
    import database as db

    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    import app as app_module

    return app_module, db


def test_register_template_sends_password_confirm():
    src = (Path(ROOT) / "templates" / "register.html").read_text(encoding="utf-8")

    assert 'id="reg-form"' in src
    assert len(re.findall(r'type="password"', src)) >= 2, "register form needs password and confirmation fields"
    assert "password_confirm" in src, "register fetch payload must include password_confirm"
    assert "dataset.submitting" in src, "register submit must guard duplicate submissions"
    assert "submitBtn.disabled = true" in src, "submit button must be disabled while registration is in-flight"


def test_register_api_accepts_legacy_payload_without_password_confirm(monkeypatch):
    from fastapi.testclient import TestClient

    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        response = client.post(
            "/api/register",
            json={"name": f"legacy{uuid.uuid4().hex[:8]}", "password": "pw1234"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert db.get_user_by_login(response.json()["name"], "pw1234")


def test_register_api_rejects_mismatched_password_confirm(monkeypatch):
    from fastapi.testclient import TestClient

    app_module, db = _setup_app_with_temp_db(monkeypatch)
    name = f"mismatch{uuid.uuid4().hex[:8]}"
    with TestClient(app_module.app) as client:
        response = client.post(
            "/api/register",
            json={"name": name, "password": "pw1234", "password_confirm": "pw9999"},
        )

        assert response.status_code == 400, response.text
        assert db.get_user_by_login(name, "pw1234") is None


def test_register_api_accepts_matching_password_confirm(monkeypatch):
    from fastapi.testclient import TestClient

    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        response = client.post(
            "/api/register",
            json={
                "name": f"confirm{uuid.uuid4().hex[:8]}",
                "password": "pw1234",
                "password_confirm": "pw1234",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert db.get_user_by_login(response.json()["name"], "pw1234")
