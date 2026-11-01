"""
HYQUB — Account Provisioning Service
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from eth_account import Account
from web3 import Web3

from app.config import get_settings
from app.crypto import mldsa
from app.crypto.encoding import encode_key_or_signature
from app.models.register import RegisterRequest, SecurityProfile
from app.models.rotate import RotateKeyRequest
from app.services import key_vault
from app.services.register_service import register_wallet
from app.services.rotate_service import rotate_key
from app.utils.blockchain import (
    get_key_version,
    get_web3,
    is_wallet_registered,
)
from app.utils.storage import storage


PROJECT_ROOT = Path(__file__).resolve().parents[3]
WALLET_ARTIFACT_PATH = (
    PROJECT_ROOT
    / "blockchain"
    / "out"
    / "HYQUBWallet.sol"
    / "HYQUBWallet.json"
)


def _hash_public_key_bytes(pubkey_bytes: bytes) -> str:
    return hashlib.sha256(pubkey_bytes).hexdigest()


def _deploy_and_fund_wallet() -> str:
    """
    Deploy a new HYQUBWallet contract using the Foundry artifact and match
    DeployHYQUB.s.sol constructor arguments exactly:
        HYQUBWallet(address _registry, address _approvalSigner, address _entryPoint)

    Fund the newly deployed wallet with 1 ETH from the registrar account.
    """
    if not WALLET_ARTIFACT_PATH.exists():
        raise FileNotFoundError(
            f"HYQUBWallet Foundry artifact not found: {WALLET_ARTIFACT_PATH}"
        )

    with WALLET_ARTIFACT_PATH.open("r", encoding="utf-8") as f:
        artifact = json.load(f)

    abi = artifact["abi"]
    bytecode = artifact["bytecode"]["object"]

    web3 = get_web3()
    settings = get_settings()

    registry_addr = Web3.to_checksum_address(settings.registry_address)
    approval_signer_addr = Account.from_key(
        settings.approval_signer_private_key
    ).address
    entrypoint_addr = Web3.to_checksum_address(settings.entrypoint_address)

    registrar_account = web3.eth.account.from_key(settings.registrar_private_key)

    wallet_contract = web3.eth.contract(abi=abi, bytecode=bytecode)

    deploy_tx = wallet_contract.constructor(
        registry_addr,
        approval_signer_addr,
        entrypoint_addr,
    ).build_transaction({
        "from": registrar_account.address,
        "nonce": web3.eth.get_transaction_count(registrar_account.address),
        "gas": 3_000_000,
        "gasPrice": web3.eth.gas_price,
        "chainId": web3.eth.chain_id,
    })

    signed_deploy_tx = web3.eth.account.sign_transaction(
        deploy_tx,
        private_key=settings.registrar_private_key,
    )
    tx_hash = web3.eth.send_raw_transaction(signed_deploy_tx.raw_transaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status != 1:
        raise RuntimeError("HYQUBWallet deployment transaction failed")

    wallet_address = Web3.to_checksum_address(receipt.contractAddress)

    # Fund the deployed wallet with 1 ETH from registrar account
    fund_tx = {
        "from": registrar_account.address,
        "to": wallet_address,
        "value": web3.to_wei(1, "ether"),
        "nonce": web3.eth.get_transaction_count(registrar_account.address),
        "gas": 100_000,
        "gasPrice": web3.eth.gas_price,
        "chainId": web3.eth.chain_id,
    }
    signed_fund_tx = web3.eth.account.sign_transaction(
        fund_tx,
        private_key=settings.registrar_private_key,
    )
    fund_tx_hash = web3.eth.send_raw_transaction(signed_fund_tx.raw_transaction)
    fund_receipt = web3.eth.wait_for_transaction_receipt(fund_tx_hash)

    if fund_receipt.status != 1:
        raise RuntimeError("Funding wallet with 1 ETH failed")

    return wallet_address


def ensure_account(
    user_id: int,
    password: str,
    db_path: str = "hyqub.db",
) -> dict[str, Any]:
    """
    Ensure user has a deployed smart wallet and an encrypted ML-DSA-65 keypair registered on-chain.

    Steps:
      1. If user.wallet_address is NULL: deploy a new HYQUBWallet, fund with 1 ETH, save to DB.
      2. If user has no encrypted_secret_key: generate ML-DSA-65 keypair, encrypt secret key,
         register/rotate key on-chain, read authoritative key_version, save to DB.
    """
    from app.services.auth_service import get_db_connection, get_user_by_id

    user = get_user_by_id(user_id, db_path)
    if not user:
        raise ValueError(f"User not found: {user_id}")

    wallet_address = user.get("wallet_address")

    # Step 3a: Deploy and fund wallet if NULL
    if not wallet_address:
        wallet_address = _deploy_and_fund_wallet()
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET wallet_address = ? WHERE user_id = ?",
                (wallet_address, user_id),
            )
            conn.commit()

    # Step 3b: Key generation & registration/rotation if no encrypted_secret_key
    if not user.get("encrypted_secret_key"):
        pubkey_bytes, secretkey_bytes = mldsa.generate_keypair()
        pubkey_b64 = encode_key_or_signature(pubkey_bytes)
        encrypted_blob = key_vault.encrypt_secret(secretkey_bytes, password)

        on_chain_registered = is_wallet_registered(wallet_address)

        if not on_chain_registered:
            reg_request = RegisterRequest(
                wallet_address=wallet_address,
                ml_dsa_public_key=pubkey_b64,
            )
            register_wallet(reg_request, storage)
        else:
            if storage.get(wallet_address) is None:
                current_ver = get_key_version(wallet_address)
                storage.add(
                    wallet_address,
                    SecurityProfile(
                        wallet_address=wallet_address,
                        ml_dsa_public_key=pubkey_b64,
                        ml_dsa_public_key_hash=_hash_public_key_bytes(pubkey_bytes),
                        key_version=current_ver,
                    ),
                )
            rotate_request = RotateKeyRequest(
                wallet_address=wallet_address,
                new_ml_dsa_public_key=pubkey_b64,
            )
            rotate_key(rotate_request, storage)

        chain_key_version = get_key_version(wallet_address)

        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE users
                SET ml_dsa_public_key = ?,
                    encrypted_secret_key = ?,
                    key_version = ?
                WHERE user_id = ?
                """,
                (pubkey_b64, encrypted_blob, chain_key_version, user_id),
            )
            conn.commit()

    return get_user_by_id(user_id, db_path)
