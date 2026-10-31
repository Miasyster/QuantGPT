"""WorldQuant BRAIN API client.

Wraps the WQ BRAIN REST API for alpha simulation, quality checks, and
formal submission. Credentials are read from environment variables
WQ_BRAIN_EMAIL and WQ_BRAIN_PASSWORD.
"""

import logging
import os
import time
from typing import Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

API_BASE = "https://api.worldquantbrain.com"

SUBMIT_THRESHOLDS = {
    "sharpe": 1.25,
    "fitness": 1.0,
    "turnover_max": 0.7,
    "turnover_min": 0.01,
}

_POLL_INTERVAL = 10
_POLL_MAX_ATTEMPTS = 36
_CONCURRENT_BACKOFF = 30
_MAX_RETRIES = 5


_ACCOUNT_ENV = {
    "primary": ("WQ_BRAIN_EMAIL", "WQ_BRAIN_PASSWORD"),
    "alt": ("WQ_BRAIN_ALT_EMAIL", "WQ_BRAIN_ALT_PASSWORD"),
}


def is_configured(account: str | None = None) -> bool:
    if account:
        env_email, env_pwd = _ACCOUNT_ENV.get(account, _ACCOUNT_ENV["primary"])
        return bool(os.environ.get(env_email) and os.environ.get(env_pwd))
    return any(
        bool(os.environ.get(e) and os.environ.get(p))
        for e, p in _ACCOUNT_ENV.values()
    )


def configured_accounts() -> list[str]:
    return [
        name for name, (e, p) in _ACCOUNT_ENV.items()
        if os.environ.get(e) and os.environ.get(p)
    ]


def get_client(account: str = "primary") -> "WQBrainClient":
    env_email, env_pwd = _ACCOUNT_ENV.get(account, _ACCOUNT_ENV["primary"])
    return WQBrainClient(
        email=os.environ.get(env_email, ""),
        password=os.environ.get(env_pwd, ""),
    )


class WQBrainClient:
    def __init__(self, email: str | None = None, password: str | None = None):
        self.email = email or os.environ.get("WQ_BRAIN_EMAIL", "")
        self.password = password or os.environ.get("WQ_BRAIN_PASSWORD", "")
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.trust_env = False
            retry = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
            adapter = HTTPAdapter(max_retries=retry)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
        return self._session

    def close(self):
        if self._session:
            self._session.close()
            self._session = None

    def authenticate(self, _max_retries: int = 5) -> bool:
        s = self._get_session()
        for attempt in range(_max_retries):
            r = s.post(
                f"{API_BASE}/authentication",
                auth=(self.email, self.password),
            )
            if r.status_code == 429:
                retry = int(r.headers.get("Retry-After", "60"))
                logger.info(f"WQ auth rate-limited, waiting {retry}s (attempt {attempt + 1}/{_max_retries})")
                time.sleep(retry + 1)
                continue

            if r.status_code not in (200, 201):
                logger.error(f"WQ auth failed: HTTP {r.status_code}")
                return False

            data = r.json()
            if "inquiry" in data:
                logger.error("WQ auth requires biometric verification — log in via browser first")
                return False

            logger.info("WQ BRAIN authenticated")
            return True

        logger.error(f"WQ auth failed: rate-limited {_max_retries} times")
        return False

    def get_user_info(self) -> dict:
        r = self._get_session().get(f"{API_BASE}/users/self")
        return r.json() if r.status_code == 200 else {}

    def simulate(
        self,
        expression: str,
        region: str = "USA",
        universe: str = "TOP3000",
        delay: int = 1,
        decay: int = 0,
        neutralization: str = "SUBINDUSTRY",
        truncation: float = 0.08,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict:
        s = self._get_session()
        payload = {
            "type": "REGULAR",
            "settings": {
                "instrumentType": "EQUITY",
                "region": region,
                "universe": universe,
                "delay": delay,
                "decay": decay,
                "neutralization": neutralization,
                "truncation": truncation,
                "pasteurization": "ON",
                "unitHandling": "VERIFY",
                "nanHandling": "OFF",
                "language": "FASTEXPR",
                "visualization": False,
            },
            "regular": expression,
        }

        for attempt in range(_MAX_RETRIES):
            try:
                r = s.post(f"{API_BASE}/simulations", json=payload)
            except (requests.ConnectionError, requests.Timeout) as e:
                wait = _CONCURRENT_BACKOFF * (attempt + 1)
                logger.warning(f"WQ connection error (attempt {attempt+1}/{_MAX_RETRIES}): {e}, retrying in {wait}s")
                if progress_callback:
                    progress_callback(0, f"连接异常，等待 {wait}s（第 {attempt+1} 次重试）")
                time.sleep(wait)
                continue

            if r.status_code in (200, 201, 202):
                break

            if r.status_code == 429:
                detail = ""
                try:
                    detail = r.json().get("detail", "")
                except Exception:
                    pass

                if "CONCURRENT_SIMULATION_LIMIT" in detail:
                    wait = _CONCURRENT_BACKOFF * (attempt + 1)
                    logger.info(f"WQ concurrent limit, waiting {wait}s (attempt {attempt+1}/{_MAX_RETRIES})")
                    if progress_callback:
                        progress_callback(0, f"并发限制，等待 {wait}s（第 {attempt+1} 次重试）")
                    time.sleep(wait)
                    continue

                retry = int(r.headers.get("Retry-After", "60"))
                logger.info(f"WQ rate-limited, waiting {retry}s")
                if progress_callback:
                    progress_callback(0, f"速率限制，等待 {retry}s")
                time.sleep(retry + 1)
                continue

            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
        else:
            return {"ok": False, "error": "WQ concurrent retry limit exceeded"}

        location = r.headers.get("Location", "")
        if not location:
            return {"ok": False, "error": "No Location header in response"}

        url = location if location.startswith("http") else f"{API_BASE}{location}"

        for i in range(_POLL_MAX_ATTEMPTS):
            try:
                r = s.get(url)
            except (requests.ConnectionError, requests.Timeout):
                logger.warning(f"WQ poll connection error (attempt {i+1}), retrying...")
                time.sleep(_POLL_INTERVAL)
                continue
            if r.status_code != 200:
                time.sleep(_POLL_INTERVAL)
                continue

            try:
                data = r.json()
            except Exception:
                time.sleep(_POLL_INTERVAL)
                continue
            status = data.get("status", "").upper()
            progress = data.get("progress", 0)

            if progress_callback:
                pct = int(progress * 100) if isinstance(progress, float) and progress <= 1 else int(progress)
                progress_callback(min(pct, 99), f"模拟进行中 ({pct}%)")

            if status in ("DONE", "COMPLETE"):
                alpha_raw = data.get("alpha", "")
                alpha_id = alpha_raw.split("/")[-1] if alpha_raw else None

                is_data = data.get("is", {})
                oos_data = data.get("oos", {})

                if alpha_id and not is_data:
                    alpha_detail = self._fetch_alpha(alpha_id)
                    is_data = alpha_detail.get("is", {})
                    oos_data = alpha_detail.get("oos", {})

                if progress_callback:
                    progress_callback(100, "模拟完成")

                return {
                    "ok": True,
                    "expression": expression,
                    "is": is_data,
                    "oos": oos_data,
                    "settings": data.get("settings", {}),
                    "alpha_id": alpha_id,
                    "simulation_id": data.get("id", ""),
                }
            elif status in ("ERROR", "FAILED"):
                return {"ok": False, "error": f"WQ simulation failed: {data.get('message', status)}"}

            time.sleep(_POLL_INTERVAL)

        return {"ok": False, "error": "WQ simulation polling timeout (6min)"}

    def _fetch_alpha(self, alpha_id: str) -> dict:
        r = self._get_session().get(f"{API_BASE}/alphas/{alpha_id}")
        if r.status_code == 200:
            try:
                return r.json()
            except Exception:
                logger.warning(f"Empty/invalid JSON from /alphas/{alpha_id}")
                return {}
        return {}

    def check_alpha_status(self, alpha_id: str) -> dict:
        """Fetch actual platform-side alpha status including submission state."""
        data = self._fetch_alpha(alpha_id)
        if not data:
            return {"ok": False, "error": f"Alpha {alpha_id} not found"}
        return {
            "ok": True,
            "alpha_id": alpha_id,
            "status": data.get("status"),
            "dateSubmitted": data.get("dateSubmitted"),
            "dateCreated": data.get("dateCreated"),
            "grade": data.get("grade"),
            "color": data.get("color"),
            "hidden": data.get("hidden"),
            "is": data.get("is", {}),
            "checks": data.get("checks", {}),
        }

    def submit_alpha(self, alpha_id: str) -> dict:
        s = self._get_session()

        for submit_try in range(3):
            r = None
            for attempt in range(3):
                try:
                    r = s.post(f"{API_BASE}/alphas/{alpha_id}/submit")
                    body = r.text[:500]
                    logger.info(f"Submit {alpha_id}: HTTP {r.status_code}, body={body}")
                    break
                except (requests.ConnectionError, requests.Timeout) as e:
                    logger.warning(f"Submit {alpha_id}: connection error (attempt {attempt+1}): {e}")
                    time.sleep(5 * (attempt + 1))
            else:
                return {"status_code": 0, "ok": False, "detail": "connection failed after retries"}

            if r.status_code == 403:
                try:
                    resp = r.json()
                    checks = resp.get("is", {}).get("checks", [])
                    sc = next((c for c in checks if c.get("name") == "SELF_CORRELATION"), None)
                    if sc and sc.get("result") == "FAIL":
                        logger.warning(f"Submit {alpha_id}: SC FAIL value={sc.get('value')} limit={sc.get('limit')}")
                        return {
                            "status_code": 403,
                            "ok": False,
                            "detail": f"SC FAIL: value={sc.get('value')} > limit={sc.get('limit')}",
                            "sc_value": sc.get("value"),
                            "sc_limit": sc.get("limit"),
                            "checks": checks,
                        }
                except Exception:
                    pass
                return {"status_code": 403, "ok": False, "detail": body}

            if r.status_code == 429:
                wait = 30 * (submit_try + 1)
                logger.warning(f"Submit {alpha_id}: rate limited (429), waiting {wait}s before retry")
                time.sleep(wait)
                continue

            if r.status_code not in (200, 201, 202):
                logger.warning(f"Submit {alpha_id}: unexpected HTTP {r.status_code}, waiting 15s before retry")
                time.sleep(15)
                continue

            poll_result = self._poll_alpha_submission(alpha_id)

            if poll_result.get("ok"):
                return poll_result

            if poll_result.get("platform_status") == "TIMEOUT":
                alpha_data = self._fetch_alpha(alpha_id)
                actual_status = (alpha_data.get("status") or "").upper()
                if actual_status == "UNSUBMITTED":
                    logger.warning(f"Submit {alpha_id}: platform still UNSUBMITTED after poll, retrying submit (try {submit_try+1})")
                    time.sleep(10)
                    continue
                logger.info(f"Submit {alpha_id}: poll timeout but platform status={actual_status}, treating as submitted")
                poll_result["ok"] = True
                poll_result["detail"] = f"poll timeout but platform accepted (status={actual_status})"
                return poll_result

            return poll_result

        return {"status_code": 200, "ok": False, "detail": "submit failed after 3 outer retries, alpha still UNSUBMITTED"}

    def _poll_alpha_submission(self, alpha_id: str, max_polls: int = 12, interval: int = 10) -> dict:
        """Poll alpha status until platform confirms submission or SC check completes."""
        s = self._get_session()
        for i in range(max_polls):
            time.sleep(interval)
            try:
                r = s.get(f"{API_BASE}/alphas/{alpha_id}")
            except (requests.ConnectionError, requests.Timeout):
                logger.warning(f"Submit poll {alpha_id}: connection error at poll #{i}")
                continue
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except Exception:
                continue

            status = data.get("status", "").upper()
            is_data = data.get("is", {})
            checks = is_data.get("checks", [])

            sc_check = next((c for c in checks if c.get("name") == "SELF_CORRELATION"), None)
            sc_result = sc_check.get("result", "PENDING") if sc_check else "MISSING"

            logger.info(f"Submit poll {alpha_id} #{i}: status={status}, SC={sc_result}")

            if status == "ACTIVE":
                logger.info(f"Submit {alpha_id}: confirmed ACTIVE on platform")
                return {
                    "status_code": 200,
                    "ok": True,
                    "detail": f"submitted and ACTIVE, SC={sc_result}",
                    "platform_status": status,
                }
            elif sc_result == "FAIL":
                sc_value = sc_check.get("value", "?")
                sc_limit = sc_check.get("limit", "?")
                logger.warning(f"Submit {alpha_id}: SC FAIL (value={sc_value}, limit={sc_limit})")
                return {
                    "status_code": 200,
                    "ok": False,
                    "detail": f"SC FAIL: value={sc_value} > limit={sc_limit}",
                    "platform_status": status,
                    "sc_value": sc_value,
                    "sc_limit": sc_limit,
                }
            elif sc_result == "PASS" and status == "UNSUBMITTED":
                logger.info(f"Submit {alpha_id}: SC PASS but still UNSUBMITTED, retrying submit...")
                try:
                    s.post(f"{API_BASE}/alphas/{alpha_id}/submit")
                except Exception:
                    pass

        return {
            "status_code": 200,
            "ok": False,
            "detail": f"submission polling timeout ({max_polls * interval}s), last status={status}, SC={sc_result}",
            "platform_status": "TIMEOUT",
        }

    def delete_alpha(self, alpha_id: str) -> dict:
        """Delete/retire an alpha from the platform."""
        s = self._get_session()
        r = s.delete(f"{API_BASE}/alphas/{alpha_id}")
        if r.status_code in (200, 204):
            return {"ok": True, "detail": f"Alpha {alpha_id} deleted"}
        if r.status_code == 405:
            r2 = s.patch(f"{API_BASE}/alphas/{alpha_id}", json={"hidden": True})
            if r2.status_code in (200, 204):
                return {"ok": True, "detail": f"Alpha {alpha_id} hidden via PATCH"}
            return {"ok": False, "detail": f"DELETE 405, PATCH also failed: {r2.status_code} {r2.text[:200]}"}
        return {"ok": False, "detail": f"DELETE failed: {r.status_code} {r.text[:200]}"}

    def unhide_alpha(self, alpha_id: str) -> dict:
        """Restore a hidden alpha."""
        s = self._get_session()
        r = s.patch(f"{API_BASE}/alphas/{alpha_id}", json={"hidden": False})
        if r.status_code in (200, 204):
            return {"ok": True, "detail": f"Alpha {alpha_id} restored"}
        return {"ok": False, "detail": f"Unhide failed: {r.status_code} {r.text[:200]}"}

