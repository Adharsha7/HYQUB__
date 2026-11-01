import os, base64, hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32,
                          maxmem=64 * 1024 * 1024)

def encrypt_secret(secret_key: bytes, password: str) -> str:
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_derive(password, salt)).encrypt(nonce, secret_key, b"hyqub-mldsa-v1")
    return base64.b64encode(salt + nonce + ct).decode()

def decrypt_secret(blob: str, password: str) -> bytes:
    raw = base64.b64decode(blob)
    salt, nonce, ct = raw[:16], raw[16:28], raw[28:]
    return AESGCM(_derive(password, salt)).decrypt(nonce, ct, b"hyqub-mldsa-v1")
