"""
HYQUB – Auth API models
=======================

Pydantic models for the POST /auth/signup and POST /auth/login endpoints.

Kept separate from models/register.py because:
  - register.py describes the *wallet key registration* contract (ML-DSA key,
    wallet address) — that's a blockchain-level operation.
  - auth.py describes the *user account* contract (username, password) — that's
    an application-level operation.

The response intentionally omits the password hash, salt, and the raw
encrypted_secret_key blob — callers only need the minimal identity they
need to drive the frontend session.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    username: str = Field(..., min_length=1, description="Unique username")
    password: str = Field(..., min_length=8, description="User password (min 8 chars)")


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, description="Username")
    password: str = Field(..., min_length=1, description="User password")


class AuthResponse(BaseModel):
    """
    Returned by both /auth/signup and /auth/login on success.

    Fields:
        user_id         – Integer primary key in the users table.
        username        – The authenticated / newly registered username.
        wallet_address  – Checksummed Ethereum address of the user's
                          deployed HYQUBWallet contract.
        ml_dsa_public_key – Base-64 encoded ML-DSA-65 public key registered
                            on-chain for this wallet.
        key_version     – Current on-chain key version (monotonic counter).
    """

    user_id: int
    username: str
    wallet_address: str
    ml_dsa_public_key: str
    key_version: int
