# HYQUB — Post-Quantum Smart Account

HYQUB is an ERC-4337 smart wallet where every transaction is
authorised by an **ML-DSA-65 (CRYSTALS-Dilithium)** signature verified
off-chain by a verifier quorum, then wrapped in an ECDSA approval token
checked on-chain by `HYQUBWallet.validateUserOp()`.

> The PQ layer is an off-chain authorisation gate.  
> The on-chain check is classical ECDSA via `ecrecover`.  
> HYQUB is **not** described as "fully quantum-proof".

---

## Repository layout

```
HYQUB/
├── backend/          FastAPI PQ Verification Engine
│   ├── app/          Application source (api, services, crypto, utils)
│   ├── tests/        Automated pytest suite (unit + integration)
│   ├── scripts/      Manual diagnostic/operational scripts
│   ├── .env.example  Configuration template — copy to .env and fill in
│   └── requirements.txt  Pinned Python dependencies
├── blockchain/       Foundry smart contracts
│   ├── src/          HYQUBRegistry.sol · HYQUBWallet.sol · TestTarget.sol
│   ├── test/         Forge test suite (51 tests)
│   ├── script/       Deployment scripts
│   ├── lib/          Forge dependencies (managed by foundry.lock)
│   └── foundry.lock  Pinned dependency commits
├── hyqub-console.html  Standalone browser console (vanilla JS, no build)
├── start_anvil.sh    Start Anvil with persistent state
└── start_hyqub.sh    Start Anvil + backend together
```

---

## Prerequisites

| Dependency | Version tested | Install |
|---|---|---|
| Python | 3.14 | system / pyenv |
| Foundry (forge, cast, anvil) | latest stable | https://getfoundry.sh |
| liboqs | system package | see below |
| Git | any | system |

### liboqs (required for ML-DSA-65)

`liboqs-python` (the `oqs` Python module) requires the **liboqs C library**
installed at the system level. It cannot be installed via pip alone.

**Ubuntu / Debian / Kali:**
```bash
sudo apt-get update
sudo apt-get install -y cmake ninja-build libssl-dev
# Build and install liboqs from source
git clone --depth 1 https://github.com/open-quantum-safe/liboqs
cd liboqs && mkdir build && cd build
cmake -GNinja -DBUILD_SHARED_LIBS=ON ..
ninja && sudo ninja install
sudo ldconfig
```

Or if your distro ships a package:
```bash
sudo apt-get install -y liboqs-dev   # Kali / newer Debian
```

---

## Quick start

### 1. Clone and initialise blockchain dependencies

```bash
git clone <repo-url> HYQUB
cd HYQUB/blockchain
git submodule update --init --recursive
```

If submodules are not initialised, `forge test` will fail with
"Source not found" errors for `forge-std`, `account-abstraction`, and
`openzeppelin-contracts`. Run the command above to fix this.

Dependency versions are pinned in `blockchain/foundry.lock`:

| Library | Tag | Commit |
|---|---|---|
| forge-std | v1.16.2 | `bf647bd6` |
| account-abstraction | v0.9.0 | `b36a1ed5` |
| openzeppelin-contracts | v5.7.0 | `cab19933` |

To install dependencies without git submodule history (faster, for CI):
```bash
cd blockchain
forge install foundry-rs/forge-std --no-git
forge install eth-infinitism/account-abstraction --no-git
forge install OpenZeppelin/openzeppelin-contracts --no-git
```

### 2. Set up the backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate          # Windows WSL: same command
pip install -r requirements.txt
```

> `liboqs-python` will fail to install if the liboqs C library is not
> present. Install it first (see Prerequisites above).

### 3. Configure environment

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and fill in:
#   HYQUB_REGISTRAR_PRIVATE_KEY
#   HYQUB_APPROVAL_SIGNER_PRIVATE_KEY
#   HYQUB_ENTRYPOINT_ADDRESS
#   HYQUB_REGISTRY_ADDRESS
#   HYQUB_WALLET_ADDRESS
#   HYQUB_TEST_TARGET_ADDRESS
```

For local development the remaining defaults are correct as-is.

### 4. Start Anvil (local chain)

```bash
# From repo root (WSL / Linux):
./start_anvil.sh
```

This starts Anvil on `http://127.0.0.1:8545` with `--load-state` from
`blockchain/anvil-state/anvil-state.json` so contract deployments
persist across restarts.

### 5. Start the backend

```bash
cd backend
source venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check: `curl http://127.0.0.1:8000/health`

---

## Running tests

### Blockchain (Forge)

```bash
cd blockchain
forge test              # all 51 tests
forge test --summary    # with per-suite counts
forge test -vvv         # verbose (shows logs on failure)
```

### Backend (pytest)

```bash
cd backend
source venv/bin/activate

# Unit tests only (no Anvil required):
pytest -m "not integration" -q

# Integration tests (requires Anvil + backend running):
pytest -m integration -q

# All tests:
pytest -q
```

See `backend/tests/` for the full test suite.  
---

## Run the demo

Local development only. You need three terminals.

1. **Anvil** (keeps its saved state): `./start_anvil.sh`
2. **Backend:**
```bash
cd backend
source venv313/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
3. **Frontend:**
```bash
cd frontend
python -m http.server 3000
```

Open **http://localhost:3000**. Do not use `127.0.0.1:3000`, because the backend CORS only allows `localhost:3000`.

Demo users are created through the normal signup flow. With the backend running, set a password in your shell (never commit it) and run:

```powershell
$env:HYQUB_DEMO_PASSWORD = "<choose a password, 8+ characters>"
python scripts\seed_demo_users.py
```

Create `backend/.env` from `backend/.env.example` and `frontend/js/config.local.js` from `config.example.js`. Both are git-ignored.

---

## Transaction flow (overview)

```
1. Generate ML-DSA-65 keypair  (client-side; secret key never leaves device)
2. POST /register              → SHA-256(pubkey) stored in HYQUBRegistry
3. GET  /transaction-intent    → canonical message_hash
4. Client signs message_hash.encode("utf-8") with ML-DSA secret key
5. POST /submit-transaction    → 2-of-2 quorum → ApprovalToken → EntryPoint
6. HYQUBWallet.validateUserOp  → on-chain ECDSA + intent hash check
```

See `backend/README.md` for the full API reference.  
See `blockchain/README.md` for contract architecture details.

---

## Security model and limitations

HYQUB is a working prototype on a local Anvil chain. It is **not audited and not for real funds**.

**How a payment is approved**
1. The user logs in and confirms a payment with their password.
2. The backend decrypts the user's ML-DSA-65 secret key (scrypt + AES-GCM) and signs the transaction intent.
3. Two verifiers check the ML-DSA signature and a quorum approves it.
4. The backend signs a classical ECDSA approval token. `HYQUBWallet.validateUserOp()` checks that token on-chain.

**Known limitations**
- **Verifier independence:** verifier A and B run in one Python process, so the 2-of-2 quorum does not give real independence yet.
- **Single approval signer:** on-chain trust rests on one classical ECDSA key. If it leaks, the off-chain ML-DSA checks can be bypassed.
- **Custodial signing:** the user's password reaches the server and decrypts the ML-DSA secret key in memory for each payment. The key is never stored in plaintext, but the server can see it while signing. Private keys do not stay in the browser.
- **Owner bypass:** `HYQUBWallet.execute()` can still be called directly by the wallet owner (Anvil account 0) without any ML-DSA signature or approval token.
- **On-chain step is classical:** ML-DSA is verified off-chain only. The on-chain check uses ECDSA, so this project does not claim post-quantum security for the on-chain step.
- **Dev endpoints:** /dev/sign and /dev/generate-keypair only start when HYQUB_ENABLE_DEV_TOOLS=true and the chain id is 31337. The app refuses to start with dev tools on any other chain. Turn them off for demos. Never commit .env.
- **Single password:** login password is also the payment password.

**Controls in place**
- Sender comes from the session, never from the request body. Recipients are chosen by name and wallet addresses are not returned to clients.
- Sessions last 30 minutes and are stored as SHA-256 hashes in SQLite. 5 wrong logins lock a user for 60 seconds. Both survive a backend restart.
- Limits: 0.5 ETH per payment, 1 ETH per 24 hours, 5 payments per minute per user.
- `POST /pay` accepts an `Idempotency-Key` header, so a double submit sends one transaction.
- `/register`, `/rotate` and `/wallet/{address}/state` are not exposed.

---

## Roadmap

- Client-side WASM ML-DSA signing, so the private key never leaves the browser
- Independent verifier services
- Multi-signer or KMS-held approval key
- WalletFactory (CREATE2) and a fix for the owner bypass
- Persisted payment statuses (pending, confirmed) and a receipt watcher
- Sepolia deployment with a real bundler and paymaster

