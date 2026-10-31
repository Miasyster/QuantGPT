"""Tests for wq_brain_client.py and routes/wq_brain.py."""

import os
from unittest.mock import MagicMock, patch

import pytest

from quantgpt.wq_brain_client import WQBrainClient, configured_accounts, get_client, is_configured

pytestmark = pytest.mark.asyncio


class TestIsConfigured:
    def test_not_configured_when_empty(self):
        with patch.dict(os.environ, {"WQ_BRAIN_EMAIL": "", "WQ_BRAIN_PASSWORD": ""}, clear=False):
            assert is_configured() is False

    def test_not_configured_when_missing(self):
        env = os.environ.copy()
        env.pop("WQ_BRAIN_EMAIL", None)
        env.pop("WQ_BRAIN_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            assert is_configured() is False

    def test_configured_when_both_set(self):
        with patch.dict(os.environ, {"WQ_BRAIN_EMAIL": "a@b.com", "WQ_BRAIN_PASSWORD": "pw"}, clear=False):
            assert is_configured() is True

    def test_configured_specific_account(self):
        with patch.dict(os.environ, {
            "WQ_BRAIN_EMAIL": "a@b.com", "WQ_BRAIN_PASSWORD": "pw",
            "WQ_BRAIN_ALT_EMAIL": "", "WQ_BRAIN_ALT_PASSWORD": "",
        }, clear=False):
            assert is_configured("primary") is True
            assert is_configured("alt") is False

    def test_configured_alt_account(self):
        with patch.dict(os.environ, {
            "WQ_BRAIN_EMAIL": "", "WQ_BRAIN_PASSWORD": "",
            "WQ_BRAIN_ALT_EMAIL": "alt@b.com", "WQ_BRAIN_ALT_PASSWORD": "pw2",
        }, clear=False):
            assert is_configured("primary") is False
            assert is_configured("alt") is True
            assert is_configured() is True

    def test_configured_accounts(self):
        with patch.dict(os.environ, {
            "WQ_BRAIN_EMAIL": "a@b.com", "WQ_BRAIN_PASSWORD": "pw",
            "WQ_BRAIN_ALT_EMAIL": "alt@b.com", "WQ_BRAIN_ALT_PASSWORD": "pw2",
        }, clear=False):
            accts = configured_accounts()
            assert "primary" in accts
            assert "alt" in accts

    def test_get_client_primary(self):
        with patch.dict(os.environ, {
            "WQ_BRAIN_EMAIL": "main@b.com", "WQ_BRAIN_PASSWORD": "pw1",
            "WQ_BRAIN_ALT_EMAIL": "alt@b.com", "WQ_BRAIN_ALT_PASSWORD": "pw2",
        }, clear=False):
            c = get_client("primary")
            assert c.email == "main@b.com"
            c2 = get_client("alt")
            assert c2.email == "alt@b.com"


class TestWQBrainClient:
    def test_init_from_params(self):
        c = WQBrainClient(email="test@test.com", password="pass")
        assert c.email == "test@test.com"
        assert c.password == "pass"

    def test_init_from_env(self):
        with patch.dict(os.environ, {"WQ_BRAIN_EMAIL": "env@test.com", "WQ_BRAIN_PASSWORD": "envpw"}):
            c = WQBrainClient()
            assert c.email == "env@test.com"
            assert c.password == "envpw"

    def test_close_without_session(self):
        c = WQBrainClient(email="a", password="b")
        c.close()

    def test_authenticate_success(self):
        c = WQBrainClient(email="a@b.com", password="pw")
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"permissions": ["SUBMIT"]}
        mock_session.post.return_value = mock_resp
        c._session = mock_session
        assert c.authenticate() is True

    def test_authenticate_failure(self):
        c = WQBrainClient(email="a@b.com", password="wrong")
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_session.post.return_value = mock_resp
        c._session = mock_session
        assert c.authenticate() is False

    def test_authenticate_biometric(self):
        c = WQBrainClient(email="a@b.com", password="pw")
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"inquiry": "biometric_required"}
        mock_session.post.return_value = mock_resp
        c._session = mock_session
        assert c.authenticate() is False


class TestWQBrainStatusEndpoint:
    async def test_status_returns_config(self, client):
        resp = await client.get("/api/v1/wq-brain/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "configured" in data
        assert "thresholds" in data
        assert data["thresholds"]["sharpe"] == 1.25


class TestWQBrainSubmitEndpoint:
    async def test_submit_returns_503_when_not_configured(self, client):
        with patch.dict(os.environ, {"WQ_BRAIN_EMAIL": "", "WQ_BRAIN_PASSWORD": ""}, clear=False):
            resp = await client.post("/api/v1/wq-brain/submit", json={
                "expression": "rank(close)",
                "tag": "test-agent",
            })
            assert resp.status_code == 503

    async def test_submit_creates_task(self, client):
        with (
            patch.dict(os.environ, {"WQ_BRAIN_EMAIL": "a@b.com", "WQ_BRAIN_PASSWORD": "pw"}, clear=False),
            patch("quantgpt.routes.wq_brain._run_wq_brain_task"),
        ):
            resp = await client.post("/api/v1/wq-brain/submit", json={
                "expression": "rank(close)",
                "tag": "test-agent",
            })
            assert resp.status_code == 202
            data = resp.json()
            assert "task_id" in data
            assert data["status"] == "pending"


class TestSubmittedAlphasEndpoint:
    async def test_list_returns_empty(self, client):
        resp = await client.get("/api/v1/wq-brain/submitted-alphas")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["alphas"] == []


class TestSubmitAlphaEndpoint:
    async def test_submit_alpha_task_not_found(self, client):
        with patch.dict(os.environ, {"WQ_BRAIN_EMAIL": "a@b.com", "WQ_BRAIN_PASSWORD": "pw"}, clear=False):
            resp = await client.post("/api/v1/wq-brain/nonexistent/submit-alpha")
            assert resp.status_code == 404
