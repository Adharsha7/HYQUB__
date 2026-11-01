"""
HYQUB — Submit Transaction Service

Orchestrates the complete pipeline proven in full_pipeline_test.py,
now as a reusable service backing POST /submit-transaction:

    Raw transaction fields (target, value, data)
        ↓
    Recompute TransactionIntent message_hash (backend never trusts a
    client-supplied hash)
        ↓
    Verify the client's ML-DSA signature via independent verifiers
        ↓
    Evaluate quorum
        ↓
    Create a signed ApprovalToken
        ↓
    Build a PackedUserOperation and submit it to the real EntryPoint
        ↓
    Return the transaction hash

This service does not talk to FastAPI directly — it raises typed
exceptions that the API route translates into HTTP responses.
"""

from __future__ import annotations

import secrets
import time

from web3 import Web3

from app.config import get_settings
from app.crypto.approval import canonicalize_payload
from app.crypto.transaction_intent import hash_transaction_intent
from app.models.submit_transaction import (
    SubmitTransactionRequest,
    SubmitTransactionResponse,
)
from app.services.approval_service import ApprovalService, ApprovalServiceError
from app.services.quorum_engine import evaluate_quorum
from app.services.verifier_engine import PQVerifier
from app.services.verify_service import WalletNotRegisteredError
from app.utils.blockchain import submit_user_operation
from app.utils.storage import WalletStorage


class TransactionNotApprovedError(Exception):
    """Raised when the verifier quorum does not approve the transaction."""


class TransactionSubmissionError(Exception):
    """Raised when the on-chain UserOperation submission fails."""


# A single shared ApprovalService instance (and therefore a single shared
# UsedNonceStore) for the lifetime of the process. Creating a fresh
# ApprovalService per request — as our standalone test scripts did —
# would silently disable the in-process replay check, since each
# instance gets its own empty nonce store. The on-chain
# usedApprovalNonces mapping is still the ultimate source of truth,
# but this backend-side store gives faster, off-chain rejection of
# an obviously-replayed nonce before spending gas on a tx that would
# revert anyway.
_approval_service = ApprovalService()


def _generate_approval_nonce() -> int:
    """
    Generate an approval nonce.
    """
    return secrets.randbits(256)


def submit_transaction(
    request: SubmitTransactionRequest,
    storage: WalletStorage,
) -> SubmitTransactionResponse:
    """
    Verify a client's ML-DSA-signed transaction intent through the full
    HYQUB quorum pipeline, then submit it as a real UserOperation.

    Raises:
        WalletNotRegisteredError: if wallet_address has no stored profile.
        TransactionNotApprovedError: if verifier quorum is not reached.
        TransactionSubmissionError: if approval creation or on-chain
            submission fails for any reason.
    """

    profile = storage.get(request.wallet_address)
    if profile is None:
        raise WalletNotRegisteredError(request.wallet_address)

    settings = get_settings()

    wallet_checksum = Web3.to_checksum_address(request.wallet_address)
    target_checksum = Web3.to_checksum_address(request.target)
    data_bytes = bytes.fromhex(request.data[2:])

    web3 = Web3(Web3.HTTPProvider(settings.rpc_url))
    chain_id = web3.eth.chain_id

    current_key_version = profile.key_version

    # ---------------------------------------------------------------
    # 1. Recompute the TransactionIntent hash ourselves. We never trust
    #    a client-supplied message_hash.
    # ---------------------------------------------------------------
    message_hash_hex = hash_transaction_intent(
        chain_id=chain_id,
        wallet_address=wallet_checksum,
        target=target_checksum,
        value=request.value_wei,
        data=data_bytes,
        key_version=current_key_version,
    )

    # ---------------------------------------------------------------
    # 2. Verify the client's ML-DSA signature via independent verifiers.
    #
    #    NOTE: verifier-a and verifier-b currently run in this same
    #    process against the same stored public key — they are not yet
    #    separate services with independent trust boundaries. This
    #    proves the quorum-aggregation logic correctly, but the real
    #    security benefit of "independent" verifiers requires actually
    #    splitting them out later.
    # ---------------------------------------------------------------
    verifier_a = PQVerifier(verifier_id="verifier-a")
    verifier_b = PQVerifier(verifier_id="verifier-b")

    result_a = verifier_a.verify(
        public_key=profile.ml_dsa_public_key,
        message=message_hash_hex,
        signature=request.ml_dsa_signature,
        key_version=current_key_version,
    )
    result_b = verifier_b.verify(
        public_key=profile.ml_dsa_public_key,
        message=message_hash_hex,
        signature=request.ml_dsa_signature,
        key_version=current_key_version,
    )

    quorum_result = evaluate_quorum(
        [result_a, result_b],
        required_quorum=settings.required_quorum,
    )

    if not quorum_result.approved:
        raise TransactionNotApprovedError(
            f"Quorum not reached: {len(quorum_result.approving_verifier_ids)}"
            f"/{quorum_result.required_quorum} verifiers approved"
        )

    # ---------------------------------------------------------------
    # 3. Create a signed ApprovalToken.
    # ---------------------------------------------------------------
    now = int(time.time())
    approval_nonce = _generate_approval_nonce()

    try:
        approval_token = _approval_service.create_approval_token(
            wallet_address=wallet_checksum,
            message_hash=message_hash_hex,
            quorum_result=quorum_result,
            nonce=approval_nonce,
            issued_at=now,
            expires_at=now + 3600,
        )
    except ApprovalServiceError as exc:
        raise TransactionSubmissionError(
            f"Failed to create approval token: {exc}"
        ) from exc

    # ---------------------------------------------------------------
    # 4. Build and submit the real UserOperation.
    # ---------------------------------------------------------------
    approval_payload_bytes = canonicalize_payload(approval_token.payload)
    approval_signature_bytes = bytes.fromhex(approval_token.signature[2:])

    try:
        tx_hash, status = submit_user_operation(
            wallet_address=wallet_checksum,
            target=target_checksum,
            value_wei=request.value_wei,
            data=data_bytes,
            approval_payload_bytes=approval_payload_bytes,
            approval_signature_bytes=approval_signature_bytes,
        )
    except Exception as exc:
        raise TransactionSubmissionError(
            f"On-chain submission failed: {exc}"
        ) from exc

    if status != 1:
        raise TransactionSubmissionError(
            f"UserOperation transaction reverted (status={status}, tx={tx_hash})"
        )

    return SubmitTransactionResponse(
        success=True,
        message="Transaction submitted and executed successfully",
        transaction_hash=tx_hash,
        approval_nonce=approval_nonce,
    )
