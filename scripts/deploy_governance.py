"""
CertiK 审计修复 — SOL-01/02/03 部署配置脚本
==============================================
功能：
1. 部署 OpenZeppelin TimelockController（48h 延迟）
2. 将 EscrowVault 的 DEFAULT_ADMIN_ROLE 授予 TimelockController
3. TimelockController 的 PROPOSER_ROLE / EXECUTOR_ROLE 授予 Gnosis Safe 多签地址
4. Revoke 原始 EOA 的 DEFAULT_ADMIN_ROLE

使用方式：
    # 1. 先用 forge build 编译合约
    # 2. 配置环境变量（见下方 CONFIG 区域）
    # 3. 运行脚本
    python scripts/deploy_governance.py

环境变量：
    RPC_URL              - 链的 RPC 地址（默认 BSC Mainnet）
    DEPLOYER_PRIVATE_KEY - 当前持有 DEFAULT_ADMIN_ROLE 的 EOA 私钥
    ESCROW_PROXY_ADDRESS - 已部署的 EscrowVault 代理合约地址
    GNOSIS_SAFE_ADDRESS  - 已部署的 Gnosis Safe 多签地址（3/5 阈值）
    TIMELOCK_DELAY       - TimelockController 延迟秒数（默认 172800 = 48h）
"""

from web3 import Web3
import json
import os
import sys

# ==========================================
# 配置区
# ==========================================

# 确保本地 RPC 不被代理拦截
_no_proxy = os.environ.get("NO_PROXY", "")
for host in ("127.0.0.1", "localhost"):
    if host not in _no_proxy:
        _no_proxy = host if not _no_proxy else f"{_no_proxy},{host}"
os.environ["NO_PROXY"] = _no_proxy
os.environ["no_proxy"] = _no_proxy

# 从环境变量读取配置
RPC_URL = os.environ.get("RPC_URL", "https://bsc-dataseed1.binance.org/")
DEPLOYER_PRIVATE_KEY = os.environ.get("DEPLOYER_PRIVATE_KEY", "")
ESCROW_PROXY_ADDRESS = os.environ.get("ESCROW_PROXY_ADDRESS", "")
GNOSIS_SAFE_ADDRESS = os.environ.get("GNOSIS_SAFE_ADDRESS", "")
TIMELOCK_DELAY = int(os.environ.get("TIMELOCK_DELAY", "172800"))  # 48 hours

# 项目路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_PROJECT_ROOT, "out")

# TimelockController 编译产物路径
TIMELOCK_JSON_PATH = os.path.join(
    _OUT_DIR, "TimelockController.sol", "TimelockController.json"
)
ESCROW_JSON_PATH = os.path.join(_OUT_DIR, "EscrowVault.sol", "EscrowVault.json")

# 角色常量
DEFAULT_ADMIN_ROLE = bytes(32)  # 0x00...00
OPERATOR_ROLE = Web3.keccak(text="OPERATOR_ROLE")
GUARDIAN_ROLE = Web3.keccak(text="GUARDIAN_ROLE")

# TimelockController 角色
TIMELOCK_ADMIN_ROLE = bytes(32)  # DEFAULT_ADMIN_ROLE
PROPOSER_ROLE = Web3.keccak(text="PROPOSER_ROLE")
EXECUTOR_ROLE = Web3.keccak(text="EXECUTOR_ROLE")
CANCELLER_ROLE = Web3.keccak(text="CANCELLER_ROLE")


def load_contract_artifact(json_path: str) -> dict:
    """加载 forge build 生成的合约 JSON"""
    with open(json_path, "r") as f:
        artifact = json.load(f)
    return {
        "abi": artifact["abi"],
        "bytecode": artifact.get("bytecode", {}).get("object", ""),
    }


def validate_config():
    """验证必要配置"""
    errors = []
    if not DEPLOYER_PRIVATE_KEY:
        errors.append("DEPLOYER_PRIVATE_KEY 未设置")
    if not ESCROW_PROXY_ADDRESS:
        errors.append("ESCROW_PROXY_ADDRESS 未设置")
    if not GNOSIS_SAFE_ADDRESS:
        errors.append("GNOSIS_SAFE_ADDRESS 未设置")
    if not os.path.exists(ESCROW_JSON_PATH):
        errors.append(f"EscrowVault 编译产物不存在: {ESCROW_JSON_PATH}\n  请先运行 forge build")

    if errors:
        print("❌ 配置错误：")
        for e in errors:
            print(f"   - {e}")
        sys.exit(1)


def main():
    print("=" * 60)
    print("CertiK SOL-01/02/03 修复 — 治理部署脚本")
    print("=" * 60)

    validate_config()

    # 连接链
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print(f"❌ 无法连接到 RPC: {RPC_URL}")
        sys.exit(1)

    chain_id = w3.eth.chain_id
    print(f"✅ 已连接到链 ID: {chain_id}")

    # 部署者账户
    deployer = w3.eth.account.from_key(DEPLOYER_PRIVATE_KEY)
    print(f"📋 部署者地址: {deployer.address}")
    print(f"📋 Gnosis Safe: {GNOSIS_SAFE_ADDRESS}")
    print(f"📋 EscrowVault: {ESCROW_PROXY_ADDRESS}")
    print(f"📋 Timelock 延迟: {TIMELOCK_DELAY} 秒 ({TIMELOCK_DELAY // 3600} 小时)")
    print()

    # ==========================================
    # Step 1: 部署 TimelockController
    # ==========================================
    print("─" * 40)
    print("Step 1: 部署 TimelockController")
    print("─" * 40)

    # 检查 TimelockController 编译产物
    # 如果没有 TimelockController 的编译产物，使用内联 ABI + bytecode
    if os.path.exists(TIMELOCK_JSON_PATH):
        timelock_artifact = load_contract_artifact(TIMELOCK_JSON_PATH)
    else:
        print("⚠️  TimelockController 编译产物不存在，请确保已安装 OpenZeppelin 并编译")
        print("   运行: forge install OpenZeppelin/openzeppelin-contracts")
        print("   然后: forge build")
        sys.exit(1)

    gnosis_safe = Web3.to_checksum_address(GNOSIS_SAFE_ADDRESS)

    # TimelockController constructor:
    # constructor(uint256 minDelay, address[] proposers, address[] executors, address admin)
    TimelockController = w3.eth.contract(
        abi=timelock_artifact["abi"],
        bytecode=timelock_artifact["bytecode"],
    )

    # proposers = [GnosisSafe], executors = [GnosisSafe], admin = deployer (临时，后续 renounce)
    construct_txn = TimelockController.constructor(
        TIMELOCK_DELAY,
        [gnosis_safe],  # proposers
        [gnosis_safe],  # executors
        deployer.address,  # admin（临时持有，部署完成后 renounce）
    ).build_transaction({
        "from": deployer.address,
        "nonce": w3.eth.get_transaction_count(deployer.address),
        "gas": 3_000_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": chain_id,
    })

    signed_txn = w3.eth.account.sign_transaction(construct_txn, DEPLOYER_PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt["status"] != 1:
        print(f"❌ TimelockController 部署失败! TX: {tx_hash.hex()}")
        sys.exit(1)

    timelock_address = receipt["contractAddress"]
    print(f"✅ TimelockController 已部署: {timelock_address}")
    print(f"   TX: {tx_hash.hex()}")
    print()

    # ==========================================
    # Step 2: 将 EscrowVault 的 DEFAULT_ADMIN_ROLE 授予 TimelockController
    # ==========================================
    print("─" * 40)
    print("Step 2: 授予 TimelockController admin 角色")
    print("─" * 40)

    escrow_artifact = load_contract_artifact(ESCROW_JSON_PATH)
    escrow = w3.eth.contract(
        address=Web3.to_checksum_address(ESCROW_PROXY_ADDRESS),
        abi=escrow_artifact["abi"],
    )

    # 验证当前 deployer 确实持有 DEFAULT_ADMIN_ROLE
    has_admin = escrow.functions.hasRole(DEFAULT_ADMIN_ROLE, deployer.address).call()
    if not has_admin:
        print(f"❌ 部署者 {deployer.address} 不持有 DEFAULT_ADMIN_ROLE!")
        sys.exit(1)

    # grantRole(DEFAULT_ADMIN_ROLE, timelockAddress)
    tx = escrow.functions.grantRole(
        DEFAULT_ADMIN_ROLE, timelock_address
    ).build_transaction({
        "from": deployer.address,
        "nonce": w3.eth.get_transaction_count(deployer.address),
        "gas": 200_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": chain_id,
    })

    signed_tx = w3.eth.account.sign_transaction(tx, DEPLOYER_PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt["status"] != 1:
        print(f"❌ grantRole 失败! TX: {tx_hash.hex()}")
        sys.exit(1)

    print(f"✅ DEFAULT_ADMIN_ROLE 已授予 TimelockController: {timelock_address}")
    print(f"   TX: {tx_hash.hex()}")
    print()

    # ==========================================
    # Step 3: Revoke deployer 的 DEFAULT_ADMIN_ROLE
    # ==========================================
    print("─" * 40)
    print("Step 3: Revoke 部署者的 admin 角色")
    print("─" * 40)

    tx = escrow.functions.revokeRole(
        DEFAULT_ADMIN_ROLE, deployer.address
    ).build_transaction({
        "from": deployer.address,
        "nonce": w3.eth.get_transaction_count(deployer.address),
        "gas": 200_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": chain_id,
    })

    signed_tx = w3.eth.account.sign_transaction(tx, DEPLOYER_PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt["status"] != 1:
        print(f"❌ revokeRole 失败! TX: {tx_hash.hex()}")
        sys.exit(1)

    print(f"✅ 已 Revoke 部署者 {deployer.address} 的 DEFAULT_ADMIN_ROLE")
    print(f"   TX: {tx_hash.hex()}")
    print()

    # ==========================================
    # Step 4: Renounce TimelockController 中 deployer 的临时 admin
    # ==========================================
    print("─" * 40)
    print("Step 4: Renounce TimelockController 中的临时 admin")
    print("─" * 40)

    timelock = w3.eth.contract(
        address=Web3.to_checksum_address(timelock_address),
        abi=timelock_artifact["abi"],
    )

    tx = timelock.functions.renounceRole(
        TIMELOCK_ADMIN_ROLE, deployer.address
    ).build_transaction({
        "from": deployer.address,
        "nonce": w3.eth.get_transaction_count(deployer.address),
        "gas": 200_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": chain_id,
    })

    signed_tx = w3.eth.account.sign_transaction(tx, DEPLOYER_PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt["status"] != 1:
        print(f"❌ renounceRole 失败! TX: {tx_hash.hex()}")
        sys.exit(1)

    print(f"✅ 已 Renounce TimelockController 中部署者的临时 admin 角色")
    print(f"   TX: {tx_hash.hex()}")
    print()

    # ==========================================
    # Step 5: 验证最终状态
    # ==========================================
    print("─" * 40)
    print("Step 5: 验证最终状态")
    print("─" * 40)

    # EscrowVault 状态验证
    deployer_has_admin = escrow.functions.hasRole(DEFAULT_ADMIN_ROLE, deployer.address).call()
    timelock_has_admin = escrow.functions.hasRole(DEFAULT_ADMIN_ROLE, timelock_address).call()

    print(f"  EscrowVault DEFAULT_ADMIN_ROLE:")
    print(f"    Deployer ({deployer.address}): {'❌ 已移除' if not deployer_has_admin else '⚠️ 仍持有!'}")
    print(f"    Timelock ({timelock_address}): {'✅ 已授予' if timelock_has_admin else '❌ 未授予!'}")

    # TimelockController 状态验证
    deployer_has_timelock_admin = timelock.functions.hasRole(TIMELOCK_ADMIN_ROLE, deployer.address).call()
    safe_has_proposer = timelock.functions.hasRole(PROPOSER_ROLE, gnosis_safe).call()
    safe_has_executor = timelock.functions.hasRole(EXECUTOR_ROLE, gnosis_safe).call()

    print(f"\n  TimelockController 角色:")
    print(f"    Deployer ADMIN: {'⚠️ 仍持有!' if deployer_has_timelock_admin else '❌ 已 Renounce'}")
    print(f"    GnosisSafe PROPOSER: {'✅' if safe_has_proposer else '❌'}")
    print(f"    GnosisSafe EXECUTOR: {'✅' if safe_has_executor else '❌'}")
    print()

    # ==========================================
    # 输出汇总 — 用于填入 CertiK 审计回复
    # ==========================================
    print("=" * 60)
    print("📝 CertiK 审计回复所需信息（复制到 CertiK_Audit_Response.md）")
    print("=" * 60)
    print(f"  TimelockController 合约地址: {timelock_address}")
    print(f"  Gnosis Safe 多签地址:        {gnosis_safe}")
    print(f"  Timelock 延迟:               {TIMELOCK_DELAY} 秒 ({TIMELOCK_DELAY // 3600} 小时)")
    print(f"  EscrowVault 代理地址:        {ESCROW_PROXY_ADDRESS}")
    print()

    # 保存部署结果
    result = {
        "chain_id": chain_id,
        "timelock_controller": timelock_address,
        "timelock_delay_seconds": TIMELOCK_DELAY,
        "gnosis_safe": gnosis_safe,
        "escrow_vault_proxy": ESCROW_PROXY_ADDRESS,
        "deployer": deployer.address,
        "deployer_admin_revoked": not deployer_has_admin,
        "timelock_has_escrow_admin": timelock_has_admin,
    }

    output_path = os.path.join(_PROJECT_ROOT, "governance_deployment.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"💾 部署结果已保存至: {output_path}")

    # 验证是否全部成功
    all_ok = (
        not deployer_has_admin
        and timelock_has_admin
        and not deployer_has_timelock_admin
        and safe_has_proposer
        and safe_has_executor
    )

    if all_ok:
        print("\n🎉 所有步骤完成！SOL-01/02/03 部署配置已就绪。")
    else:
        print("\n⚠️  部分验证未通过，请检查上方输出。")
        sys.exit(1)


if __name__ == "__main__":
    main()
