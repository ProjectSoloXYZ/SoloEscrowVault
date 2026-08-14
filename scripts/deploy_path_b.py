"""
Path B deployment script for EscrowVault.

This is the clean deployment path for a new network:
1. Deploy TimelockController with Admin Safe as proposer/executor.
2. Renounce the deployer's temporary Timelock admin role.
3. Deploy EscrowVault implementation.
4. Deploy ERC1967Proxy and initialize EscrowVault with:
   admin    = TimelockController
   operator = OPERATOR_ADDRESS
   guardian = Guardian Safe

Run:
    cp .env.example .env
    # fill .env
    forge build
    python scripts/deploy_path_b.py

If a run fails after Timelock deployment, set TIMELOCK_ADDRESS in .env and rerun.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from web3 import Web3


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "out"

TIMELOCK_JSON_PATH = OUT_DIR / "TimelockController.sol" / "TimelockController.json"
ESCROW_JSON_PATH = OUT_DIR / "EscrowVault.sol" / "EscrowVault.json"
PROXY_JSON_PATH = OUT_DIR / "ERC1967Proxy.sol" / "ERC1967Proxy.json"

DEFAULT_ADMIN_ROLE = bytes(32)
PROPOSER_ROLE = Web3.keccak(text="PROPOSER_ROLE")
EXECUTOR_ROLE = Web3.keccak(text="EXECUTOR_ROLE")
CANCELLER_ROLE = Web3.keccak(text="CANCELLER_ROLE")
OPERATOR_ROLE = Web3.keccak(text="OPERATOR_ROLE")
GUARDIAN_ROLE = Web3.keccak(text="GUARDIAN_ROLE")


def getenv_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def getenv_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got: {value}") from exc


def getenv_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on"}


def load_artifact(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Missing artifact: {path}\nRun `forge build` first.")

    artifact = json.loads(path.read_text())
    bytecode = artifact.get("bytecode", {}).get("object", "")
    if not bytecode:
        raise SystemExit(f"Artifact has no bytecode: {path}")

    return {"abi": artifact["abi"], "bytecode": bytecode}


def checksum(w3: Web3, name: str, value: str) -> str:
    if not w3.is_address(value):
        raise SystemExit(f"{name} is not a valid address: {value}")
    return Web3.to_checksum_address(value)


def fee_params(w3: Web3) -> dict[str, int]:
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas")

    if base_fee is None:
        return {"gasPrice": w3.eth.gas_price}

    priority = getenv_int("MAX_PRIORITY_FEE_PER_GAS_WEI", 1_000_000)
    max_fee = getenv_int("MAX_FEE_PER_GAS_WEI", int(base_fee) * 2 + priority)
    return {
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority,
    }


def raw_transaction(signed: Any) -> bytes:
    raw = getattr(signed, "raw_transaction", None)
    if raw is not None:
        return raw
    return getattr(signed, "rawTransaction")


def wait_for_receipt(w3: Web3, tx_hash: bytes, label: str) -> Any:
    timeout = getenv_int("TX_TIMEOUT_SECONDS", 300)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
    if receipt["status"] != 1:
        tx = w3.eth.get_transaction(tx_hash)
        gas_limit = tx.get("gas")
        gas_used = receipt.get("gasUsed")
        hint = ""
        if gas_limit is not None and gas_used == gas_limit:
            hint = " The transaction used the full gas limit; increase the *_GAS value and retry."
        raise SystemExit(
            f"{label} failed. tx={tx_hash.hex()} gasUsed={gas_used} gasLimit={gas_limit}.{hint}"
        )
    return receipt


def build_sign_send(
    w3: Web3,
    account: Any,
    private_key: str,
    tx_builder: Callable[[dict[str, Any]], dict[str, Any]],
    label: str,
    gas_limit: int,
) -> Any:
    tx = tx_builder(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": gas_limit,
            "chainId": w3.eth.chain_id,
            **fee_params(w3),
        }
    )
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(raw_transaction(signed))
    print(f"  {label} tx: {tx_hash.hex()}")
    return wait_for_receipt(w3, tx_hash, label)


def role_hex(role: bytes) -> str:
    return "0x" + role.hex()


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    rpc_url = getenv_required("RPC_URL")
    private_key = getenv_required("DEPLOYER_PRIVATE_KEY")
    timelock_delay = getenv_int("TIMELOCK_DELAY", 172800)
    expected_chain_id = os.environ.get("EXPECTED_CHAIN_ID", "").strip()
    existing_timelock = os.environ.get("TIMELOCK_ADDRESS", "").strip()
    existing_escrow_impl = os.environ.get("ESCROW_IMPL_ADDRESS", "").strip()
    dry_run = getenv_bool("DRY_RUN", False)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise SystemExit(f"Unable to connect to RPC_URL: {rpc_url}")

    chain_id = w3.eth.chain_id
    if expected_chain_id and int(expected_chain_id) != chain_id:
        raise SystemExit(
            f"Connected chain_id={chain_id}, but EXPECTED_CHAIN_ID={expected_chain_id}"
        )

    deployer = w3.eth.account.from_key(private_key)
    admin_safe = checksum(w3, "ADMIN_SAFE_ADDRESS", getenv_required("ADMIN_SAFE_ADDRESS"))
    guardian_safe = checksum(
        w3, "GUARDIAN_SAFE_ADDRESS", getenv_required("GUARDIAN_SAFE_ADDRESS")
    )
    operator = checksum(w3, "OPERATOR_ADDRESS", getenv_required("OPERATOR_ADDRESS"))

    print("=" * 72)
    print("EscrowVault Path B Deployment")
    print("=" * 72)
    print(f"Chain ID:       {chain_id}")
    print(f"Deployer:       {deployer.address}")
    print(f"Admin Safe:     {admin_safe}")
    print(f"Guardian Safe:  {guardian_safe}")
    print(f"Operator:       {operator}")
    print(f"Timelock delay: {timelock_delay} seconds")
    if existing_timelock:
        print(f"Existing Timelock: {existing_timelock}")
    if existing_escrow_impl:
        print(f"Existing Escrow impl: {existing_escrow_impl}")
    print()

    if dry_run:
        print("DRY_RUN=true: configuration parsed successfully; no transactions sent.")
        return

    timelock_artifact = load_artifact(TIMELOCK_JSON_PATH)
    escrow_artifact = load_artifact(ESCROW_JSON_PATH)
    proxy_artifact = load_artifact(PROXY_JSON_PATH)

    TimelockController = w3.eth.contract(
        abi=timelock_artifact["abi"],
        bytecode=timelock_artifact["bytecode"],
    )

    if existing_timelock:
        print("Step 1/5: Reuse existing TimelockController")
        timelock_address = checksum(w3, "TIMELOCK_ADDRESS", existing_timelock)
        code = w3.eth.get_code(timelock_address)
        if code in (b"", "0x"):
            raise SystemExit(f"TIMELOCK_ADDRESS has no code: {timelock_address}")
        print(f"  TimelockController: {timelock_address}")
        print()
    else:
        # 1. Deploy TimelockController.
        print("Step 1/5: Deploy TimelockController")
        receipt = build_sign_send(
            w3,
            deployer,
            private_key,
            lambda opts: TimelockController.constructor(
                timelock_delay,
                [admin_safe],
                [admin_safe],
                deployer.address,
            ).build_transaction(opts),
            "deploy TimelockController",
            getenv_int("TIMELOCK_DEPLOY_GAS", 4_000_000),
        )
        timelock_address = Web3.to_checksum_address(receipt["contractAddress"])
        print(f"  TimelockController: {timelock_address}")
        print()

    timelock = w3.eth.contract(address=timelock_address, abi=timelock_artifact["abi"])

    # 2. Renounce deployer's temporary Timelock admin.
    print("Step 2/5: Renounce deployer's temporary Timelock admin role")
    if timelock.functions.hasRole(DEFAULT_ADMIN_ROLE, deployer.address).call():
        build_sign_send(
            w3,
            deployer,
            private_key,
            lambda opts: timelock.functions.renounceRole(
                DEFAULT_ADMIN_ROLE,
                deployer.address,
            ).build_transaction(opts),
            "renounce Timelock admin",
            getenv_int("TIMELOCK_RENOUNCE_GAS", 200_000),
        )
        print("  Deployer temporary Timelock admin renounced.")
    else:
        print("  Deployer does not hold Timelock admin; skipping.")
    print()

    # 3. Deploy EscrowVault implementation.
    print("Step 3/5: Deploy EscrowVault implementation")
    EscrowVault = w3.eth.contract(
        abi=escrow_artifact["abi"],
        bytecode=escrow_artifact["bytecode"],
    )
    if existing_escrow_impl:
        escrow_impl = checksum(w3, "ESCROW_IMPL_ADDRESS", existing_escrow_impl)
        code = w3.eth.get_code(escrow_impl)
        if code in (b"", "0x"):
            raise SystemExit(f"ESCROW_IMPL_ADDRESS has no code: {escrow_impl}")
        print(f"  Reusing EscrowVault implementation: {escrow_impl}")
    else:
        receipt = build_sign_send(
            w3,
            deployer,
            private_key,
            lambda opts: EscrowVault.constructor().build_transaction(opts),
            "deploy EscrowVault implementation",
            getenv_int("ESCROW_IMPL_DEPLOY_GAS", 8_000_000),
        )
        escrow_impl = Web3.to_checksum_address(receipt["contractAddress"])
    print(f"  EscrowVault implementation: {escrow_impl}")
    print()

    # 4. Deploy ERC1967Proxy and initialize EscrowVault roles.
    print("Step 4/5: Deploy ERC1967Proxy and initialize EscrowVault")
    escrow_impl_contract = w3.eth.contract(address=escrow_impl, abi=escrow_artifact["abi"])
    init_data_hex = escrow_impl_contract.functions.initialize(
        timelock_address,
        operator,
        guardian_safe,
    )._encode_transaction_data()
    init_data = bytes.fromhex(init_data_hex[2:])

    ERC1967Proxy = w3.eth.contract(
        abi=proxy_artifact["abi"],
        bytecode=proxy_artifact["bytecode"],
    )
    receipt = build_sign_send(
        w3,
        deployer,
        private_key,
        lambda opts: ERC1967Proxy.constructor(escrow_impl, init_data).build_transaction(opts),
        "deploy ERC1967Proxy",
        getenv_int("PROXY_DEPLOY_GAS", 5_000_000),
    )
    escrow_proxy = Web3.to_checksum_address(receipt["contractAddress"])
    print(f"  EscrowVault proxy: {escrow_proxy}")
    print()

    # 5. Verify roles and deployment state.
    print("Step 5/5: Verify roles")
    escrow = w3.eth.contract(address=escrow_proxy, abi=escrow_artifact["abi"])

    checks = {
        "Vault admin is Timelock": escrow.functions.hasRole(
            DEFAULT_ADMIN_ROLE, timelock_address
        ).call(),
        "Vault operator is OPERATOR_ADDRESS": escrow.functions.hasRole(
            OPERATOR_ROLE, operator
        ).call(),
        "Vault guardian is Guardian Safe": escrow.functions.hasRole(
            GUARDIAN_ROLE, guardian_safe
        ).call(),
        "Deployer is not Vault admin": not escrow.functions.hasRole(
            DEFAULT_ADMIN_ROLE, deployer.address
        ).call(),
        "Admin Safe is Timelock proposer": timelock.functions.hasRole(
            PROPOSER_ROLE, admin_safe
        ).call(),
        "Admin Safe is Timelock executor": timelock.functions.hasRole(
            EXECUTOR_ROLE, admin_safe
        ).call(),
        "Admin Safe is Timelock canceller": timelock.functions.hasRole(
            CANCELLER_ROLE, admin_safe
        ).call(),
        "Deployer is not Timelock admin": not timelock.functions.hasRole(
            DEFAULT_ADMIN_ROLE, deployer.address
        ).call(),
        "Timelock is self-admin": timelock.functions.hasRole(
            DEFAULT_ADMIN_ROLE, timelock_address
        ).call(),
    }

    all_ok = True
    for name, ok in checks.items():
        all_ok = all_ok and ok
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")

    platform_treasury = escrow.functions.platformTreasury().call()
    print(f"  platformTreasury: {platform_treasury}")
    print()

    deployment = {
        "deployed_at": int(time.time()),
        "chain_id": chain_id,
        "deployer": deployer.address,
        "admin_safe": admin_safe,
        "guardian_safe": guardian_safe,
        "operator": operator,
        "timelock_delay_seconds": timelock_delay,
        "timelock_controller": timelock_address,
        "escrow_vault_implementation": escrow_impl,
        "escrow_vault_proxy": escrow_proxy,
        "platform_treasury": platform_treasury,
        "roles": {
            "DEFAULT_ADMIN_ROLE": role_hex(DEFAULT_ADMIN_ROLE),
            "OPERATOR_ROLE": role_hex(OPERATOR_ROLE),
            "GUARDIAN_ROLE": role_hex(GUARDIAN_ROLE),
            "PROPOSER_ROLE": role_hex(PROPOSER_ROLE),
            "EXECUTOR_ROLE": role_hex(EXECUTOR_ROLE),
            "CANCELLER_ROLE": role_hex(CANCELLER_ROLE),
        },
        "checks": checks,
        "next_steps": [
            "Use Admin Safe to schedule Timelock calls for setTokenWhitelist(token, true).",
            "Use Admin Safe to schedule Timelock call for setPlatformTreasury(treasury).",
            "Use a small proposal to verify schedule -> wait -> execute end to end.",
        ],
    }

    output_path = Path(
        os.environ.get(
            "DEPLOYMENT_OUTPUT",
            PROJECT_ROOT / "deployments" / f"path_b_{chain_id}.json",
        )
    )
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(deployment, indent=2) + "\n")
    print(f"Deployment saved to: {output_path}")

    if not all_ok:
        raise SystemExit("Deployment completed, but one or more verification checks failed.")

    print("\nDone. Use the EscrowVault proxy address for all business interactions.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
