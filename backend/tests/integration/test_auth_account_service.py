"""
HYQUB — Integration tests for SQLite Auth & Account Provisioning Service.
Requires Anvil running on http://127.0.0.1:8545.
"""

import os
import tempfile
import pytest
from web3 import Web3

from app.crypto import mldsa
from app.services import key_vault
from app.services.auth_service import init_db, login, signup, get_db_connection
from app.utils.blockchain import get_key_version, get_web3, is_wallet_registered


@pytest.mark.integration
def test_signup_provisions_account_and_registers_key_on_chain():
    """
    Test complete signup flow:
      1. Creates user in DB.
      2. Deploys a new HYQUBWallet contract & funds with 1 ETH.
      3. Generates ML-DSA keypair, encrypts secret key with password.
      4. Registers key hash on-chain (HYQUBRegistry).
      5. Correct password decrypts secret key blob; wrong password fails.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_hyqub.db")

        username = "alice_test"
        password = "supersecret_password_123"

        # 1. Perform Signup
        user = signup(username, password, db_path=db_path)

        # Assert User Record
        assert user["user_id"] is not None
        assert user["username"] == username
        assert user["wallet_address"] is not None
        assert user["wallet_address"].startswith("0x")
        assert len(user["wallet_address"]) == 42
        assert user["ml_dsa_public_key"] is not None
        assert user["encrypted_secret_key"] is not None
        assert user["key_version"] >= 1

        # Assert Wallet Balance >= 1 ETH on Anvil
        web3 = get_web3()
        balance_wei = web3.eth.get_balance(Web3.to_checksum_address(user["wallet_address"]))
        assert balance_wei >= web3.to_wei(1, "ether")

        # Assert On-Chain Registration
        assert is_wallet_registered(user["wallet_address"]) is True
        chain_key_ver = get_key_version(user["wallet_address"])
        assert user["key_version"] == chain_key_ver

        # Assert DB Secret Key is Encrypted (not plaintext)
        blob = user["encrypted_secret_key"]
        assert blob != user["ml_dsa_public_key"]

        # Assert Correct Password Decrypts
        decrypted_secret = key_vault.decrypt_secret(blob, password)
        assert len(decrypted_secret) == mldsa.SECRET_KEY_LEN

        # Assert Wrong Password Fails
        with pytest.raises(Exception):
            key_vault.decrypt_secret(blob, "wrong_password_999")


@pytest.mark.integration
def test_login_preserves_account():
    """
    Test login flow runs ensure_account and returns existing user.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_login.db")

        username = "bob_test"
        password = "bob_password_456"

        user_signup = signup(username, password, db_path=db_path)
        user_login = login(username, password, db_path=db_path)

        assert user_login["user_id"] == user_signup["user_id"]
        assert user_login["wallet_address"] == user_signup["wallet_address"]
        assert user_login["ml_dsa_public_key"] == user_signup["ml_dsa_public_key"]
        assert user_login["encrypted_secret_key"] == user_signup["encrypted_secret_key"]
        assert user_login["key_version"] == user_signup["key_version"]


def test_migration_adds_missing_columns():
    """
    Test ALTER TABLE migration on existing users table.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_migration.db")

        # Create an old schema table without the new columns
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    wallet_address TEXT
                )
            """)
            cursor.execute(
                "INSERT INTO users (username, password_hash, password_salt) VALUES (?, ?, ?)",
                ("olduser", "hash", "salt")
            )
            conn.commit()

        # Run init_db (should apply ALTER TABLE)
        init_db(db_path)

        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(users)")
            cols = {row["name"] for row in cursor.fetchall()}
            assert "ml_dsa_public_key" in cols
            assert "encrypted_secret_key" in cols
            assert "key_version" in cols

            cursor.execute("SELECT * FROM users WHERE username = 'olduser'")
            row = dict(cursor.fetchone())
            assert row["username"] == "olduser"
