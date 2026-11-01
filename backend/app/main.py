"""
HYQUB PQ Verification Engine — entrypoint.

Uses an application-factory pattern (create_app()) rather than building
app directly at import time. This keeps main.py responsible only for
wiring configuration, middleware, and API routers together.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth
from app.api import health
from app.api import market
from app.api import me
from app.api import pay
from app.api import submit_transaction
from app.api import transaction_intent
from app.api import users
from app.api import verify

from app.config import get_settings


def _assert_local_chain(rpc_url: str) -> None:
    """
    Connect to the RPC node and assert chain_id == 31337.

    Called only when HYQUB_ENABLE_DEV_TOOLS=true.  Raises RuntimeError
    if the chain is not a local Anvil instance so the process refuses
    to start rather than silently exposing dev routes against a real
    network.
    """
    from web3 import Web3

    try:
        web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
        chain_id = web3.eth.chain_id
    except Exception as exc:
        raise RuntimeError(
            f"HYQUB_ENABLE_DEV_TOOLS=true but could not reach RPC at "
            f"{rpc_url}: {exc}"
        ) from exc

    if chain_id != 31337:
        raise RuntimeError(
            f"HYQUB_ENABLE_DEV_TOOLS=true is not permitted on chain_id={chain_id}. "
            f"Dev tools may only be enabled on a local Anvil chain (chain_id=31337). "
            f"Set HYQUB_ENABLE_DEV_TOOLS=false or point HYQUB_RPC_URL at a local "
            f"Anvil instance."
        )


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "Post-quantum (ML-DSA) verification engine for HYQUB — "
            "an ERC-4337 smart account secured by lattice-based "
            "signatures via delegated, quorum-based verification."
        ),
    )

    # -------------------------------------------------
    # CORS
    # -------------------------------------------------

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------------------------------------------------
    # Core HYQUB API
    # -------------------------------------------------

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(market.router)
    app.include_router(me.router)
    app.include_router(users.router)
    app.include_router(verify.router)
    app.include_router(transaction_intent.router)
    app.include_router(submit_transaction.router)
    app.include_router(pay.router)

    # -------------------------------------------------
    # DEVELOPMENT TOOLS
    #
    # These routes are registered ONLY when:
    #   1. HYQUB_ENABLE_DEV_TOOLS=true  (explicit opt-in)
    #   2. chain_id == 31337            (local Anvil only)
    #
    # The application REFUSES TO START if enable_dev_tools=True
    # and the connected chain is not 31337.  This prevents
    # accidentally exposing /dev/sign (which handles ML-DSA secret
    # keys) against a real network.
    #
    # Note: setting HYQUB_ENVIRONMENT=development alone is no longer
    # sufficient — you must also set HYQUB_ENABLE_DEV_TOOLS=true.
    # -------------------------------------------------

    if settings.enable_dev_tools:
        _assert_local_chain(settings.rpc_url)

        from app.api import dev_tools

        app.include_router(dev_tools.router)

        print(
            "⚠️  HYQUB development tools enabled "
            f"(environment={settings.environment!r}, "
            "chain_id=31337)"
        )

    return app


# ---------------------------------------------------------------------------
# Module-level application instance
#
# uvicorn loads this module and looks for the `app` symbol at import time.
# During unit testing (when pytest sets PYTEST_CURRENT_TEST or sys.argv[0]
# is pytest), we still need to create the app, but we must not let a missing
# Anvil connection break test collection.  The unit test conftest patches
# `_assert_local_chain` at the module level before pytest imports any test
# file, which means this call is safe as long as conftest loads first.
#
# If you see a RuntimeError here during `pytest -m "not integration"`, ensure
# that `tests/unit/conftest.py` is present and sets up the patch.
# ---------------------------------------------------------------------------

app = create_app()
