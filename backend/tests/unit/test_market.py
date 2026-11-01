"""
Unit tests for backend/app/market.py and GET /market/eth.

All httpx calls are mocked — no live network, no Anvil node needed.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _binance_row(open_ms: float, close: float):
    """Return a minimal Binance klines row (index 0=open_ms, 4=close)."""
    return [open_ms, 0, 0, 0, str(close), 0, 0, 0, 0, 0, 0, 0]


def _kraken_resp(pair: str, rows):
    return {"error": [], "result": {pair: rows, "last": 999}}


def _kraken_row(ts_s: float, close: float):
    """[time, open, high, low, close, vwap, volume, count]"""
    return [ts_s, 0, 0, 0, str(close), 0, 0, 0]


def _make_client_mock(side_effects):
    """Return a mock httpx.AsyncClient context-manager with given get side_effects."""
    inst = AsyncMock()
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=False)
    inst.get = AsyncMock(side_effect=side_effects)
    return inst


# ---------------------------------------------------------------------------
# Tests for _demo_series
# ---------------------------------------------------------------------------

class TestDemoSeries:
    def test_length(self):
        from app.market import _demo_series
        assert len(_demo_series()) == 90

    def test_structure(self):
        from app.market import _demo_series
        for ms, price in _demo_series():
            assert ms > 0
            assert price > 0

    def test_deterministic(self):
        from app.market import _demo_series
        assert _demo_series() == _demo_series()

    def test_base_respected(self):
        from app.market import _demo_series
        low  = _demo_series(base=1000.0)
        high = _demo_series(base=5000.0)
        assert max(p for _, p in high) > max(p for _, p in low)

    def test_ascending_timestamps(self):
        from app.market import _demo_series
        ts = [ms for ms, _ in _demo_series()]
        assert ts == sorted(ts)


# ---------------------------------------------------------------------------
# Tests for _validate
# ---------------------------------------------------------------------------

class TestValidate:
    def test_nan_price_rejected(self):
        from app.market import _validate
        import math
        pts = [[float(i * 86_400_000), float("nan")] for i in range(10)]
        with pytest.raises(ValueError, match="invalid price"):
            _validate(pts)

    def test_negative_price_rejected(self):
        from app.market import _validate
        pts = [[float(i * 86_400_000), -1.0] for i in range(10)]
        with pytest.raises(ValueError, match="invalid price"):
            _validate(pts)

    def test_zero_price_rejected(self):
        from app.market import _validate
        pts = [[float(i * 86_400_000), 0.0] for i in range(10)]
        with pytest.raises(ValueError, match="invalid price"):
            _validate(pts)

    def test_non_ascending_timestamps_rejected(self):
        from app.market import _validate
        pts = [[float(i * 86_400_000), 2000.0] for i in range(10)]
        pts[5] = [pts[4][0], 2000.0]   # duplicate => not strictly ascending
        with pytest.raises(ValueError, match="not strictly ascending"):
            _validate(pts)

    def test_fewer_than_7_points_rejected(self):
        from app.market import _validate
        pts = [[float(i * 86_400_000), 2000.0] for i in range(6)]
        with pytest.raises(ValueError, match="point count"):
            _validate(pts)

    def test_exactly_7_points_accepted(self):
        from app.market import _validate
        pts = [[float(i * 86_400_000), 2000.0] for i in range(7)]
        _validate(pts)  # should not raise


# ---------------------------------------------------------------------------
# Tests for _fetch_binance
# ---------------------------------------------------------------------------

class TestFetchBinance:
    def test_happy_path(self):
        from app.market import _fetch_binance
        rows = [_binance_row(i * 86_400_000.0, 2000 + i) for i in range(90)]
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = rows
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        pts = asyncio.run(_fetch_binance(mock_client))
        assert len(pts) == 90
        assert pts[0] == [0.0, 2000.0]

    def test_non_200_raises(self):
        from app.market import _fetch_binance
        mock_resp = MagicMock(status_code=503)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(ValueError, match="503"):
            asyncio.run(_fetch_binance(mock_client))

    def test_empty_list_raises(self):
        from app.market import _fetch_binance
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = []
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(ValueError, match="bad response"):
            asyncio.run(_fetch_binance(mock_client))

    def test_invalid_price_falls_through(self):
        """NaN price in Binance response triggers validation failure."""
        from app.market import _fetch_binance
        rows = [_binance_row(i * 86_400_000.0, float("nan")) for i in range(10)]
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = rows
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(ValueError):
            asyncio.run(_fetch_binance(mock_client))

    def test_non_ascending_ts_falls_through(self):
        """Non-ascending timestamps in Binance response trigger validation failure."""
        from app.market import _fetch_binance
        rows = [_binance_row(0.0, 2000.0) for _ in range(10)]   # all same ts
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = rows
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(ValueError):
            asyncio.run(_fetch_binance(mock_client))

    def test_fewer_than_7_falls_through(self):
        """Fewer than 7 rows in Binance response trigger validation failure."""
        from app.market import _fetch_binance
        rows = [_binance_row(i * 86_400_000.0, 2000.0) for i in range(3)]
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = rows
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(ValueError):
            asyncio.run(_fetch_binance(mock_client))


# ---------------------------------------------------------------------------
# Tests for _fetch_kraken
# ---------------------------------------------------------------------------

class TestFetchKraken:
    def test_happy_path(self):
        from app.market import _fetch_kraken
        rows = [_kraken_row(i * 86400.0, 2000 + i) for i in range(90)]
        body = _kraken_resp("XETHZUSD", rows)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = body
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        pts = asyncio.run(_fetch_kraken(mock_client))
        assert len(pts) == 90
        assert pts[0][0] == 0.0 * 1000

    def test_api_error_raises(self):
        from app.market import _fetch_kraken
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"error": ["EGeneral:Unknown"], "result": {}}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(ValueError, match="Kraken error"):
            asyncio.run(_fetch_kraken(mock_client))

    def test_non_200_raises(self):
        from app.market import _fetch_kraken
        mock_resp = MagicMock(status_code=429)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(ValueError, match="429"):
            asyncio.run(_fetch_kraken(mock_client))

    def test_invalid_price_falls_through(self):
        """Negative price in Kraken response triggers validation failure."""
        from app.market import _fetch_kraken
        rows = [_kraken_row(i * 86400.0, -1.0) for i in range(10)]
        body = _kraken_resp("XETHZUSD", rows)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = body
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(ValueError):
            asyncio.run(_fetch_kraken(mock_client))

    def test_fewer_than_7_falls_through(self):
        """Fewer than 7 rows in Kraken response trigger validation failure."""
        from app.market import _fetch_kraken
        rows = [_kraken_row(i * 86400.0, 2000.0) for i in range(4)]
        body = _kraken_resp("XETHZUSD", rows)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = body
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with pytest.raises(ValueError):
            asyncio.run(_fetch_kraken(mock_client))


# ---------------------------------------------------------------------------
# Cache / state reset fixture (covers all new module-level variables)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cache():
    """Reset module-level cache before every test."""
    import app.market as m
    m._cache_data = None
    m._cache_ts   = 0.0
    m._cache_lock = None
    m._last_good  = None
    m._cache_ttl  = m._CACHE_TTL
    yield
    m._cache_data = None
    m._cache_ts   = 0.0
    m._cache_lock = None
    m._last_good  = None
    m._cache_ttl  = m._CACHE_TTL


# ---------------------------------------------------------------------------
# Tests for get_eth_price_series (cache + fallback logic)
# ---------------------------------------------------------------------------

class TestGetEthPriceSeries:

    def test_returns_binance_on_success(self):
        from app.market import get_eth_price_series
        rows = [_binance_row(i * 86_400_000.0, 2500.0 + i) for i in range(90)]
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = rows
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock([mock_resp])
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "binance"
        assert len(result["points"]) == 90

    def test_falls_back_to_kraken(self):
        from app.market import get_eth_price_series
        rows = [_kraken_row(i * 86400.0, 2500.0 + i) for i in range(90)]
        body = _kraken_resp("XETHZUSD", rows)
        binance_resp = MagicMock(status_code=503)
        kraken_resp  = MagicMock(status_code=200)
        kraken_resp.json.return_value = body
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock([binance_resp, kraken_resp])
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "kraken"

    def test_both_fail_no_cache_returns_demo_null(self):
        """Both providers fail with no previous good result → source=demo, points=None."""
        from app.market import get_eth_price_series
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock(Exception("network down"))
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "demo"
        assert result["points"] is None

    def test_both_fail_with_previous_good_returns_stale(self):
        """Both providers fail but a previous good result exists → return stale data."""
        import app.market as m
        from app.market import get_eth_price_series
        stale = {"source": "binance", "points": [[float(i * 86_400_000), 3000.0] for i in range(90)]}
        m._last_good = stale
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock(Exception("network down"))
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "binance"
        assert result["points"] == stale["points"]

    def test_cache_hit_skips_network(self):
        import app.market as m
        from app.market import get_eth_price_series
        m._cache_data = {"source": "binance", "points": [[1, 2500.0]]}
        m._cache_ts = time.monotonic()
        with patch("app.market.httpx.AsyncClient") as MockClient:
            result = asyncio.run(get_eth_price_series())
            MockClient.assert_not_called()
        assert result["source"] == "binance"

    def test_stale_cache_refreshes(self):
        import app.market as m
        from app.market import get_eth_price_series
        m._cache_data = {"source": "stale", "points": []}
        m._cache_ts = time.monotonic() - 400   # older than TTL
        rows = [_binance_row(i * 86_400_000.0, 2500.0) for i in range(90)]
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = rows
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock([mock_resp])
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "binance"

    def test_binance_bad_validation_falls_back_to_kraken(self):
        """Binance returns valid HTTP but invalid data → falls through to Kraken."""
        from app.market import get_eth_price_series
        bad_rows = [_binance_row(i * 86_400_000.0, 2500.0) for i in range(6)]
        good_rows = [_kraken_row(i * 86400.0, 2500.0 + i) for i in range(90)]
        body = _kraken_resp("XETHZUSD", good_rows)
        binance_resp = MagicMock(status_code=200)
        binance_resp.json.return_value = bad_rows
        kraken_resp = MagicMock(status_code=200)
        kraken_resp.json.return_value = body
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock([binance_resp, kraken_resp])
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "kraken"

    def test_binance_nan_price_falls_back_to_kraken(self):
        from app.market import get_eth_price_series
        bad_rows = [_binance_row(i * 86_400_000.0, float("nan")) for i in range(10)]
        good_rows = [_kraken_row(i * 86400.0, 2500.0 + i) for i in range(90)]
        binance_resp = MagicMock(status_code=200, json=MagicMock(return_value=bad_rows))
        kraken_resp = MagicMock(status_code=200, json=MagicMock(return_value=_kraken_resp("XETHZUSD", good_rows)))
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock([binance_resp, kraken_resp])
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "kraken"

    def test_binance_negative_price_falls_back_to_kraken(self):
        from app.market import get_eth_price_series
        bad_rows = [_binance_row(i * 86_400_000.0, -10.0) for i in range(10)]
        good_rows = [_kraken_row(i * 86400.0, 2500.0 + i) for i in range(90)]
        binance_resp = MagicMock(status_code=200, json=MagicMock(return_value=bad_rows))
        kraken_resp = MagicMock(status_code=200, json=MagicMock(return_value=_kraken_resp("XETHZUSD", good_rows)))
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock([binance_resp, kraken_resp])
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "kraken"

    def test_binance_zero_price_falls_back_to_kraken(self):
        from app.market import get_eth_price_series
        bad_rows = [_binance_row(i * 86_400_000.0, 0.0) for i in range(10)]
        good_rows = [_kraken_row(i * 86400.0, 2500.0 + i) for i in range(90)]
        binance_resp = MagicMock(status_code=200, json=MagicMock(return_value=bad_rows))
        kraken_resp = MagicMock(status_code=200, json=MagicMock(return_value=_kraken_resp("XETHZUSD", good_rows)))
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock([binance_resp, kraken_resp])
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "kraken"

    def test_binance_non_ascending_ts_falls_back_to_kraken(self):
        from app.market import get_eth_price_series
        bad_rows = [_binance_row(1000.0, 2500.0) for _ in range(10)]
        good_rows = [_kraken_row(i * 86400.0, 2500.0 + i) for i in range(90)]
        binance_resp = MagicMock(status_code=200, json=MagicMock(return_value=bad_rows))
        kraken_resp = MagicMock(status_code=200, json=MagicMock(return_value=_kraken_resp("XETHZUSD", good_rows)))
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock([binance_resp, kraken_resp])
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "kraken"

    def test_binance_fewer_than_7_points_falls_back_to_kraken(self):
        from app.market import get_eth_price_series
        bad_rows = [_binance_row(i * 86_400_000.0, 2500.0) for i in range(6)]
        good_rows = [_kraken_row(i * 86400.0, 2500.0 + i) for i in range(90)]
        binance_resp = MagicMock(status_code=200, json=MagicMock(return_value=bad_rows))
        kraken_resp = MagicMock(status_code=200, json=MagicMock(return_value=_kraken_resp("XETHZUSD", good_rows)))
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock([binance_resp, kraken_resp])
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "kraken"

    def test_demo_retry_after_30s(self):
        import app.market as m
        from app.market import get_eth_price_series
        m._cache_data = {"source": "demo", "points": None}
        m._cache_ts = time.monotonic() - 35
        m._cache_ttl = 30
        rows = [_binance_row(i * 86_400_000.0, 2500.0) for i in range(90)]
        mock_resp = MagicMock(status_code=200, json=MagicMock(return_value=rows))
        with patch("app.market.httpx.AsyncClient") as MockClient:
            MockClient.return_value = _make_client_mock([mock_resp])
            result = asyncio.run(get_eth_price_series())
        assert result["source"] == "binance"


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------

class TestMarketRoute:
    @pytest.fixture()
    def client_and_token(self, tmp_path, monkeypatch):
        from app.main import app
        from app.services import auth_service
        from app.services.session_store import create_session

        db_file = str(tmp_path / "market_test.db")
        monkeypatch.setattr(auth_service, "DEFAULT_DB_PATH", db_file)
        auth_service.init_db(db_file)

        uid = 9999  # integer, as create_session expects
        tok = create_session(uid, db_path=db_file)

        fake_user = {
            "user_id": uid,
            "username": "marketuser",
            "wallet_address": "0x0000000000000000000000000000000000000000",
            "ml_dsa_public_key": "",
            "encrypted_secret_key": "",
            "key_version": 1,
            "password_hash": "",
            "password_salt": "",
        }
        with patch("app.dependencies.get_user_by_id", return_value=fake_user):
            with TestClient(app) as c:
                yield c, tok

    def test_requires_auth(self, client_and_token):
        client, _ = client_and_token
        resp = client.get("/market/eth")
        assert resp.status_code == 401

    def test_returns_200_with_valid_session(self, client_and_token):
        import app.market as m
        m._cache_data = {"source": "demo", "points": None}
        m._cache_ts   = time.monotonic()

        client, tok = client_and_token
        resp = client.get("/market/eth", headers={"Authorization": f"Bearer {tok}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "demo"

    def test_returns_market_data(self, client_and_token):
        import app.market as m
        m._cache_data = {"source": "binance", "points": [[1.0, 2500.0]]}
        m._cache_ts   = time.monotonic()

        client, tok = client_and_token
        resp = client.get("/market/eth", headers={"Authorization": f"Bearer {tok}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "binance"
        assert isinstance(body["points"], list)

    def test_no_cache_header(self, client_and_token):
        import app.market as m
        m._cache_data = {"source": "demo", "points": None}
        m._cache_ts   = time.monotonic()

        client, tok = client_and_token
        resp = client.get("/market/eth", headers={"Authorization": f"Bearer {tok}"})
        assert resp.headers.get("cache-control") == "no-store"
