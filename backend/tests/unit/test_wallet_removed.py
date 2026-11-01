"""
Unit test: GET /wallet/{wallet_address}/state must return 404.

The route was removed from the public API to close the address-leak
(the new frontend does not use it; the service code in wallet.py is kept).
This test confirms the route is no longer registered.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# conftest patches Web3 before this import.
from app.main import app

client = TestClient(app)

_ADDR = "0x0000000000000000000000000000000000000001"


class TestWalletRouteRemoved:
    def test_wallet_state_returns_404(self):
        """GET /wallet/{address}/state must not exist in the public router."""
        resp = client.get(f"/wallet/{_ADDR}/state")
        assert resp.status_code == 404, (
            f"Expected 404 (route removed) but got {resp.status_code}. "
            "Check that wallet.router is not included in app/main.py."
        )
