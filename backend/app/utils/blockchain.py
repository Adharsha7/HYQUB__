from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.contract import Contract

from app.config import get_settings


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

settings = get_settings()

RPC_URL = settings.rpc_url


# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]

WALLET_ABI_PATH = (
    PROJECT_ROOT
    / "blockchain"
    / "out"
    / "HYQUBWallet.sol"
    / "HYQUBWallet.json"
)

ENTRYPOINT_ABI_PATH = (
    PROJECT_ROOT
    / "blockchain"
    / "out"
    / "EntryPoint.sol"
    / "EntryPoint.json"
)

REGISTRY_ABI_PATH = (
    PROJECT_ROOT
    / "blockchain"
    / "out"
    / "HYQUBRegistry.sol"
    / "HYQUBRegistry.json"
)


# ---------------------------------------------------------------------------
# ABI helpers
# ---------------------------------------------------------------------------

def _load_abi(path: Path) -> list[dict[str, Any]]:
    """
    Load a Foundry artifact and return its ABI.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Foundry artifact not found: {path}"
        )

    with path.open("r", encoding="utf-8") as f:
        artifact = json.load(f)

    if "abi" not in artifact:
        raise RuntimeError(
            f"Artifact does not contain an ABI: {path}"
        )

    return artifact["abi"]


WALLET_ABI = _load_abi(WALLET_ABI_PATH)
ENTRYPOINT_ABI = _load_abi(ENTRYPOINT_ABI_PATH)
REGISTRY_ABI = _load_abi(REGISTRY_ABI_PATH)


# ---------------------------------------------------------------------------
# Web3
# ---------------------------------------------------------------------------

def get_web3() -> Web3:
    """
    Create a Web3 connection to Anvil.
    """
    web3 = Web3(
        Web3.HTTPProvider(RPC_URL)
    )

    if not web3.is_connected():
        raise RuntimeError(
            f"Could not connect to blockchain at {RPC_URL}"
        )

    return web3


def get_wallet_balance(wallet_address: str) -> int:
    """
    Return the ETH balance (in wei) of a wallet address.
    """
    web3 = get_web3()
    return web3.eth.get_balance(Web3.to_checksum_address(wallet_address))


# ---------------------------------------------------------------------------
# Contract helpers
# ---------------------------------------------------------------------------

def get_wallet_contract(
    web3: Web3,
    wallet_address: str,
) -> Contract:
    """
    Return HYQUBWallet contract instance.
    """
    wallet_addr = Web3.to_checksum_address(
        wallet_address
    )

    return web3.eth.contract(
        address=wallet_addr,
        abi=WALLET_ABI,
    )


def get_entrypoint_contract(
    web3: Web3,
) -> Contract:
    """
    Return EntryPoint contract instance.
    """
    entrypoint_addr = Web3.to_checksum_address(
        settings.entrypoint_address
    )

    return web3.eth.contract(
        address=entrypoint_addr,
        abi=ENTRYPOINT_ABI,
    )


def get_registry_contract(
    web3: Web3,
) -> Contract:
    """
    Return HYQUBRegistry contract instance.
    """
    registry_addr = Web3.to_checksum_address(
        settings.registry_address
    )

    return web3.eth.contract(
        address=registry_addr,
        abi=REGISTRY_ABI,
    )

def get_key_version(wallet_address: str) -> int:
    """Read-only: return the wallet's current key version."""
    web3 = get_web3()
    contract = get_registry_contract(web3)
    wallet = Web3.to_checksum_address(wallet_address)
    return contract.functions.getKeyVersion(wallet).call()


def is_wallet_registered(wallet_address: str) -> bool:
    """Read-only check: is this wallet already registered on HYQUBRegistry?"""
    web3 = get_web3()
    contract = get_registry_contract(web3)
    wallet = Web3.to_checksum_address(wallet_address)
    return contract.functions.isRegistered(wallet).call()


def register_wallet_on_chain(
    wallet_address: str,
    key_hash: str,
) -> str | None:
    """
    Register a wallet and its ML-DSA public-key hash on HYQUBRegistry.

    If the wallet is already registered on-chain, skip the transaction
    and return None instead of reverting.
    """

    if is_wallet_registered(wallet_address):
        return None

    web3 = get_web3()
    contract = get_registry_contract(web3)

    private_key = settings.registrar_private_key
    account = web3.eth.account.from_key(private_key)
    sender = account.address

    wallet = Web3.to_checksum_address(wallet_address)

    clean_hash = key_hash[2:] if key_hash.startswith("0x") else key_hash
    key_hash_bytes = bytes.fromhex(clean_hash)

    transaction = contract.functions.registerWallet(
        wallet,
        key_hash_bytes,
    ).build_transaction(
        {
            "from": sender,
            "nonce": web3.eth.get_transaction_count(sender),
            "gas": 200000,
            "gasPrice": web3.eth.gas_price,
            "chainId": web3.eth.chain_id,
        }
    )

    signed_transaction = web3.eth.account.sign_transaction(
        transaction,
        private_key=private_key,
    )

    tx_hash = web3.eth.send_raw_transaction(signed_transaction.raw_transaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status != 1:
        raise RuntimeError("Blockchain registration transaction failed")

    return tx_hash.hex()
# ---------------------------------------------------------------------------
# Basic blockchain helpers
# ---------------------------------------------------------------------------

def get_chain_id() -> int:
    """
    Return the connected chain ID.
    """
    web3 = get_web3()
    return int(web3.eth.chain_id)


def get_wallet_balance(
    wallet_address: str,
) -> int:
    """
    Return wallet ETH balance in wei.
    """
    web3 = get_web3()

    wallet_addr = Web3.to_checksum_address(
        wallet_address
    )

    return int(
        web3.eth.get_balance(wallet_addr)
    )


def get_wallet_nonce(
    wallet_address: str,
) -> int:
    """
    Read the wallet nonce directly from HYQUBWallet.
    """
    web3 = get_web3()

    wallet = get_wallet_contract(
        web3,
        wallet_address,
    )

    return int(
        wallet.functions.nonce().call()
    )


# ---------------------------------------------------------------------------
# Wallet state
# ---------------------------------------------------------------------------

def get_wallet_state(
    wallet_address: str,
) -> dict[str, Any]:
    """
    Read the current deployed HYQUBWallet state.
    """
    web3 = get_web3()

    wallet_addr = Web3.to_checksum_address(
        wallet_address
    )

    wallet = get_wallet_contract(
        web3,
        wallet_addr,
    )

    owner = wallet.functions.owner().call()
    balance_wei = web3.eth.get_balance(wallet_addr)
    nonce = wallet.functions.nonce().call()

    registered = False
    key_version = 0

    # Try registry state where supported.
    try:
        registry = get_registry_contract(web3)

        # Keep this defensive because registry ABI/function naming can vary
        # between development deployments.
        if hasattr(registry.functions, "getWallet"):
            registry_state = registry.functions.getWallet(
                wallet_addr
            ).call()

            if isinstance(registry_state, tuple):
                registered = bool(
                    registry_state[0]
                )

                if len(registry_state) > 1:
                    try:
                        key_version = int(
                            registry_state[1]
                        )
                    except Exception:
                        pass

    except Exception:
        pass

    return {
        "wallet_address": wallet_addr,
        "owner": Web3.to_checksum_address(owner),
        "balance_wei": str(balance_wei),
        "balance_eth": str(
            Web3.from_wei(
                balance_wei,
                "ether",
            )
        ),
        "nonce": int(nonce),
        "chain_id": int(web3.eth.chain_id),
        "block_number": int(
            web3.eth.block_number
        ),
        "registered": registered,
        "key_version": key_version,
    }


# ---------------------------------------------------------------------------
# Transaction receipt helper
# ---------------------------------------------------------------------------

def wait_for_transaction(
    tx_hash: str | bytes,
) -> Any:
    """
    Wait for a blockchain transaction receipt.
    """
    web3 = get_web3()

    if isinstance(tx_hash, str):
        tx_hash = Web3.to_bytes(
            hexstr=tx_hash
        )

    return web3.eth.wait_for_transaction_receipt(
        tx_hash
    )


# ---------------------------------------------------------------------------
# Key rotation transaction helper
# ---------------------------------------------------------------------------

def submit_key_rotation(
    wallet_address: str,
    new_public_key: bytes,
) -> str:
    """
    Submit a key rotation transaction.

    This helper is retained for compatibility with the existing backend.
    """
    web3 = get_web3()

    wallet = get_wallet_contract(
        web3,
        wallet_address,
    )

    private_key = settings.registrar_private_key

    account = web3.eth.account.from_key(
        private_key
    )

    if not hasattr(
        wallet.functions,
        "rotateKey",
    ):
        raise RuntimeError(
            "HYQUBWallet ABI does not expose rotateKey()."
        )

    tx = wallet.functions.rotateKey(
        new_public_key
    ).build_transaction(
        {
            "from": account.address,
            "nonce": web3.eth.get_transaction_count(
                account.address
            ),
            "gas": 500_000,
            "gasPrice": web3.eth.gas_price,
            "chainId": web3.eth.chain_id,
        }
    )

    signed_transaction = (
        web3.eth.account.sign_transaction(
            tx,
            private_key=private_key,
        )
    )

    tx_hash = web3.eth.send_raw_transaction(
        signed_transaction.raw_transaction
    )

    receipt = web3.eth.wait_for_transaction_receipt(
        tx_hash
    )

    if receipt.status != 1:
        raise RuntimeError(
            "Blockchain key rotation transaction failed"
        )

    return web3.to_hex(
        tx_hash
    )


# ---------------------------------------------------------------------------
# ERC-4337 Helpers
# ---------------------------------------------------------------------------

def _pack_gas_limits(
    hi: int,
    lo: int,
) -> bytes:
    """
    Pack two uint128 gas values into bytes32.

    PackedUserOperation.accountGasLimits:

        first 16 bytes  = verificationGasLimit
        second 16 bytes = callGasLimit
    """

    if hi < 0 or hi >= 2**128:
        raise ValueError(
            "hi gas value must fit inside uint128"
        )

    if lo < 0 or lo >= 2**128:
        raise ValueError(
            "lo gas value must fit inside uint128"
        )

    return (
        (hi << 128) | lo
    ).to_bytes(
        32,
        "big",
    )


# ---------------------------------------------------------------------------
# ERC-4337 UserOperation Submission
# ---------------------------------------------------------------------------

def submit_user_operation(
    wallet_address: str,
    target: str,
    value_wei: int,
    data: bytes,
    approval_payload_bytes: bytes,
    approval_signature_bytes: bytes,
) -> tuple[str, int]:
    """
    Build and submit an ERC-4337 PackedUserOperation.

    Flow:

        HYQUB Approval
              ↓
        PackedUserOperation
              ↓
        EntryPoint.handleOps()
              ↓
        HYQUBWallet.validateUserOp()
              ↓
        HYQUBWallet.execute()
              ↓
        Target Contract / ETH Transfer

    The EntryPoint nonce is fetched dynamically.

    Returns:

        (
            transaction_hash,
            outer_transaction_receipt_status
        )
    """

    # ---------------------------------------------------------
    # Connect to blockchain
    # ---------------------------------------------------------

    web3 = get_web3()

    # ---------------------------------------------------------
    # Settings
    # ---------------------------------------------------------

    settings = get_settings()

    # ---------------------------------------------------------
    # Addresses
    # ---------------------------------------------------------

    wallet_addr = Web3.to_checksum_address(
        wallet_address
    )

    target_addr = Web3.to_checksum_address(
        target
    )

    entrypoint_addr = Web3.to_checksum_address(
        settings.entrypoint_address
    )

    # ---------------------------------------------------------
    # Contract objects
    # ---------------------------------------------------------

    wallet_contract = web3.eth.contract(
        address=wallet_addr,
        abi=WALLET_ABI,
    )

    entrypoint_contract = web3.eth.contract(
        address=entrypoint_addr,
        abi=ENTRYPOINT_ABI,
    )

    # ---------------------------------------------------------
    # Build HYQUBWallet.execute()
    #
    # execute(
    #     address target,
    #     uint256 value,
    #     bytes data
    # )
    #
    # This is used for:
    #
    # 1. ETH transfer
    # 2. Smart-contract call
    # ---------------------------------------------------------

    call_data = wallet_contract.encode_abi(
        "execute",
        args=[
            target_addr,
            value_wei,
            data,
        ],
    )

    print(
        "HYQUBWallet execute calldata:",
        call_data,
        flush=True,
    )

    # ---------------------------------------------------------
    # Build UserOperation signature
    #
    # abi.encode(
    #     approvalPayload,
    #     approvalSignature
    # )
    # ---------------------------------------------------------

    user_op_signature = web3.codec.encode(
        [
            "bytes",
            "bytes",
        ],
        [
            approval_payload_bytes,
            approval_signature_bytes,
        ],
    )

    # ---------------------------------------------------------
    # Fetch CURRENT ERC-4337 nonce
    #
    # IMPORTANT:
    #
    # Never hardcode this to 0.
    #
    # EntryPoint maintains the UserOperation nonce.
    # ---------------------------------------------------------

    current_nonce = (
        entrypoint_contract.functions.getNonce(
            wallet_addr,
            0,
        ).call()
    )

    print(
        f"ERC-4337 current nonce: {current_nonce}",
        flush=True,
    )

    # ---------------------------------------------------------
    # Gas configuration
    #
    # IMPORTANT FIX:
    #
    # The known-good full pipeline test uses:
    #
    # verificationGasLimit = 300000
    # callGasLimit         = 300000
    #
    # The previous API path used:
    #
    # verificationGasLimit = 300000
    # callGasLimit         = 150000
    #
    # Match the known-good pipeline.
    # ---------------------------------------------------------

    verification_gas_limit = 300_000
    call_gas_limit = 300_000

    pre_verification_gas = 50_000

    max_priority_fee_per_gas = 1_000_000_000
    max_fee_per_gas = 10_000_000_000

    # ---------------------------------------------------------
    # Packed accountGasLimits
    #
    # first 16 bytes:
    #     verificationGasLimit
    #
    # second 16 bytes:
    #     callGasLimit
    # ---------------------------------------------------------

    account_gas_limits = _pack_gas_limits(
        verification_gas_limit,
        call_gas_limit,
    )

    # ---------------------------------------------------------
    # Packed gasFees
    #
    # first 16 bytes:
    #     maxPriorityFeePerGas
    #
    # second 16 bytes:
    #     maxFeePerGas
    # ---------------------------------------------------------

    gas_fees = _pack_gas_limits(
        max_priority_fee_per_gas,
        max_fee_per_gas,
    )

    # ---------------------------------------------------------
    # Build PackedUserOperation
    # ---------------------------------------------------------

    user_op = (
        wallet_addr,

        # nonce
        current_nonce,

        # initCode
        b"",

        # callData
        bytes.fromhex(
            call_data[2:]
        ),

        # accountGasLimits
        account_gas_limits,

        # preVerificationGas
        pre_verification_gas,

        # gasFees
        gas_fees,

        # paymasterAndData
        b"",

        # signature
        user_op_signature,
    )

    print(
        "PackedUserOperation built successfully.",
        flush=True,
    )

    print(
        f"  sender: {wallet_addr}",
        flush=True,
    )

    print(
        f"  nonce: {current_nonce}",
        flush=True,
    )

    print(
        f"  verificationGasLimit: "
        f"{verification_gas_limit}",
        flush=True,
    )

    print(
        f"  callGasLimit: "
        f"{call_gas_limit}",
        flush=True,
    )

    # ---------------------------------------------------------
    # Bundler / relayer account
    #
    # Local development:
    # registrar account acts as bundler EOA.
    # ---------------------------------------------------------

    private_key = (
        settings.registrar_private_key
    )

    account = web3.eth.account.from_key(
        private_key
    )

    # ---------------------------------------------------------
    # Build EntryPoint.handleOps()
    # ---------------------------------------------------------

    tx_nonce = (
        web3.eth.get_transaction_count(
            account.address
        )
    )

    tx = (
        entrypoint_contract.functions.handleOps(
            [user_op],
            account.address,
        ).build_transaction(
            {
                "from": account.address,

                "nonce": tx_nonce,

                "gas": 1_000_000,

                "gasPrice": web3.eth.gas_price,

                "chainId": web3.eth.chain_id,
            }
        )
    )

    # ---------------------------------------------------------
    # Sign outer transaction
    # ---------------------------------------------------------

    signed_transaction = (
        web3.eth.account.sign_transaction(
            tx,
            private_key=private_key,
        )
    )

    # ---------------------------------------------------------
    # Send transaction
    # ---------------------------------------------------------

    tx_hash = web3.eth.send_raw_transaction(
        signed_transaction.raw_transaction
    )

    tx_hash_hex = web3.to_hex(
        tx_hash
    )

    print(
        f"EntryPoint transaction submitted: "
        f"{tx_hash_hex}",
        flush=True,
    )

    # ---------------------------------------------------------
    # Wait for receipt
    # ---------------------------------------------------------

    receipt = (
        web3.eth.wait_for_transaction_receipt(
            tx_hash
        )
    )

    print(
        f"EntryPoint receipt status: "
        f"{receipt.status}",
        flush=True,
    )

    return (
        tx_hash_hex,
        int(receipt.status),
    )


def rotate_wallet_on_chain(
    wallet_address: str,
    new_key_hash: str,
) -> str:
    """
    Rotate a wallet's ML-DSA public-key hash on HYQUBRegistry.

    Calls HYQUBRegistry.rotateKey(wallet, newKeyHash). Raises
    RuntimeError if the transaction reverts. The wallet must already
    be registered on-chain, or this call will revert with
    "Not registered" (enforced by the contract itself).
    """
    web3 = get_web3()
    contract = get_registry_contract(web3)

    private_key = settings.registrar_private_key
    account = web3.eth.account.from_key(private_key)
    sender = account.address

    wallet = Web3.to_checksum_address(wallet_address)

    clean_hash = new_key_hash[2:] if new_key_hash.startswith("0x") else new_key_hash
    key_hash_bytes = bytes.fromhex(clean_hash)

    transaction = contract.functions.rotateKey(
        wallet,
        key_hash_bytes,
    ).build_transaction(
        {
            "from": sender,
            "nonce": web3.eth.get_transaction_count(sender),
            "gas": 200000,
            "gasPrice": web3.eth.gas_price,
            "chainId": web3.eth.chain_id,
        }
    )

    signed_transaction = web3.eth.account.sign_transaction(
        transaction,
        private_key=private_key,
    )

    tx_hash = web3.eth.send_raw_transaction(signed_transaction.raw_transaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status != 1:
        raise RuntimeError("Blockchain key rotation transaction failed")

    return tx_hash.hex()
