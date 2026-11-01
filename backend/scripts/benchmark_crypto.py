import time
import statistics
import json
import itertools
from app.crypto import mldsa
from app.crypto.encoding import encode_key_or_signature
from app.crypto.transaction_intent import hash_transaction_intent
from app.crypto.approval import canonicalize_payload
from app.services.verifier_engine import PQVerifier
from app.services.quorum_engine import evaluate_quorum
from app.services.approval_service import ApprovalService

def benchmark(func, iterations=100):
    # Warmup
    for _ in range(5):
        func()
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        func()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)  # ms
    return {
        "iterations": iterations,
        "mean_ms": round(statistics.mean(times), 3),
        "median_ms": round(statistics.median(times), 3),
        "min_ms": round(min(times), 3),
        "max_ms": round(max(times), 3),
        "stdev_ms": round(statistics.stdev(times), 3) if len(times) > 1 else 0,
        "ops_per_sec": round(1000.0 / statistics.mean(times), 1)
    }

print("Running HYQUB Performance Benchmark Suite...", flush=True)

# 1. Keypair generation
keygen_metrics = benchmark(lambda: mldsa.generate_keypair(), iterations=100)

# Pre-generate sample data
pubkey_bytes, seckey_bytes = mldsa.generate_keypair()
test_msg = hash_transaction_intent(
    chain_id=31337,
    wallet_address="0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",
    target="0x4444444444444444444444444444444444444444",
    value=10000000000000000,
    data=b"",
    key_version=1
).encode("utf-8")

sig_bytes = mldsa.sign(test_msg, seckey_bytes)

# 2. Signing
sign_metrics = benchmark(lambda: mldsa.sign(test_msg, seckey_bytes), iterations=100)

# 3. Verification
verify_metrics = benchmark(lambda: mldsa.verify(test_msg, sig_bytes, pubkey_bytes), iterations=100)

# 4. Quorum Verification (2 verifiers)
verifier_a = PQVerifier(verifier_id="verifier-a")
verifier_b = PQVerifier(verifier_id="verifier-b")
pub_b64 = encode_key_or_signature(pubkey_bytes)
sig_b64 = encode_key_or_signature(sig_bytes)
msg_str = test_msg.decode("utf-8")

def run_quorum():
    ra = verifier_a.verify(public_key=pub_b64, message=msg_str, signature=sig_b64, key_version=1)
    rb = verifier_b.verify(public_key=pub_b64, message=msg_str, signature=sig_b64, key_version=1)
    q = evaluate_quorum([ra, rb], required_quorum=2)
    assert q.approved
    return q

quorum_metrics = benchmark(run_quorum, iterations=100)

# 5. Approval Token Generation (EIP-191 ECDSA) with monotonic nonce
approval_service = ApprovalService()
q_res = run_quorum()
now = int(time.time())
nonce_counter = itertools.count(start=10000)

def run_approval():
    return approval_service.create_approval_token(
        wallet_address="0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",
        message_hash=msg_str,
        quorum_result=q_res,
        nonce=next(nonce_counter),
        issued_at=now,
        expires_at=now + 3600
    )

approval_metrics = benchmark(run_approval, iterations=100)

# 6. Full End-to-End Off-chain Gate
gate_nonce_counter = itertools.count(start=500000)
def run_full_offchain_gate():
    # Signing
    sig = mldsa.sign(test_msg, seckey_bytes)
    sb64 = encode_key_or_signature(sig)
    # 2-of-2 Quorum
    ra = verifier_a.verify(public_key=pub_b64, message=msg_str, signature=sb64, key_version=1)
    rb = verifier_b.verify(public_key=pub_b64, message=msg_str, signature=sb64, key_version=1)
    q = evaluate_quorum([ra, rb], required_quorum=2)
    # ECDSA Approval Token
    tok = approval_service.create_approval_token(
        wallet_address="0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0",
        message_hash=msg_str,
        quorum_result=q,
        nonce=next(gate_nonce_counter),
        issued_at=now,
        expires_at=now + 3600
    )
    return tok

gate_metrics = benchmark(run_full_offchain_gate, iterations=100)

# Sizes
app_token = run_approval()
sizes = {
    "mldsa_public_key_raw_bytes": len(pubkey_bytes),
    "mldsa_public_key_b64_chars": len(pub_b64),
    "mldsa_secret_key_raw_bytes": len(seckey_bytes),
    "mldsa_signature_raw_bytes": len(sig_bytes),
    "mldsa_signature_b64_chars": len(sig_b64),
    "intent_hash_chars": len(msg_str),
    "ecdsa_approval_signature_chars": len(app_token.signature),
    "canonical_payload_bytes": len(canonicalize_payload(app_token.payload))
}

results = {
    "algorithm": "ML-DSA-65 (NIST FIPS 204 / CRYSTALS-Dilithium3)",
    "security_level": "NIST Category 3 (192-bit quantum-safe security)",
    "sizes": sizes,
    "benchmarks": {
        "keygen": keygen_metrics,
        "sign": sign_metrics,
        "verify_single": verify_metrics,
        "quorum_2_of_2": quorum_metrics,
        "ecdsa_approval_token": approval_metrics,
        "full_offchain_gate": gate_metrics
    }
}

print("=== BENCHMARK RESULTS JSON ===", flush=True)
print(json.dumps(results, indent=2), flush=True)
