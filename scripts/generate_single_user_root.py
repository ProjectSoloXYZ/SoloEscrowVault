"""
Generate the off-chain Merkle root/proof for a single-user EscrowVault claim.

For a single leaf:
    merkleRoot = leaf
    proof = []

The leaf format must match EscrowVault.claim():
    keccak256(keccak256(abi.encode(account, token, rootId, cumulativeAmount)))

Usage:
    CLAIM_USER=0x... TOKEN=0x... ROOT_ID=1 AMOUNT=1000000 python scripts/generate_single_user_root.py

Optional:
    OUTPUT=artifacts/single_user_root.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from eth_abi import encode
from web3 import Web3


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def getenv_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def checksum_address(name: str, value: str) -> str:
    if not Web3.is_address(value):
        raise SystemExit(f"{name} is not a valid address: {value}")
    return Web3.to_checksum_address(value)


def getenv_int(name: str) -> int:
    value = getenv_required(name)
    try:
        return int(value, 0)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got: {value}") from exc


def hex0(value: bytes) -> str:
    return "0x" + value.hex()


def main() -> None:
    account_value = os.environ.get("CLAIM_USER", os.environ.get("ACCOUNT", "")).strip()
    if not account_value:
        account_value = getenv_required("CLAIM_USER")
    account = checksum_address("CLAIM_USER", account_value)
    token = checksum_address("TOKEN", os.environ.get("TOKEN", os.environ.get("USDC", "")))
    root_id = getenv_int("ROOT_ID")
    amount = getenv_int("AMOUNT")

    inner = Web3.keccak(
        encode(
            ["address", "address", "uint64", "uint128"],
            [account, token, root_id, amount],
        )
    )
    leaf = Web3.keccak(inner)
    merkle_root = leaf
    proof: list[str] = []

    result = {
        "account": account,
        "token": token,
        "root_id": root_id,
        "cumulative_amount": amount,
        "leaf": hex0(leaf),
        "merkle_root": hex0(merkle_root),
        "proof": proof,
        "claim_args": {
            "token": token,
            "rootId": root_id,
            "cumulativeAmount": amount,
            "merkleProof": proof,
            "recipient": account,
        },
    }

    output = os.environ.get("OUTPUT", "").strip()
    if output:
        output_path = Path(output)
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"Wrote {output_path}")

    print(json.dumps(result, indent=2))
    print()
    print("Shell exports:")
    print(f"export MERKLE_ROOT={hex0(merkle_root)}")
    print(f"export CLAIM_PROOF='[]'")
    print(f"export CLAIM_ROOT_ID={root_id}")
    print(f"export CLAIM_AMOUNT={amount}")


if __name__ == "__main__":
    main()
