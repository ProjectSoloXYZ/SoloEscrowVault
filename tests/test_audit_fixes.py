"""
CertiK 审计修复验证测试脚本
============================
针对 SOL-04、SOL-05、SOL-07、SOL-08、SOL-10 的代码修改逐项验证。

使用方式：
    1. 确保 Anvil 已在后台运行: anvil
    2. 确保已编译: forge build
    3. 运行: python tests/test_audit_fixes.py
"""

from web3 import Web3
import json
import os
import secrets

# 确保本地 RPC 不被代理拦截
_no_proxy = os.environ.get("NO_PROXY", "")
for host in ("127.0.0.1", "localhost"):
    if host not in _no_proxy:
        _no_proxy = host if not _no_proxy else f"{_no_proxy},{host}"
os.environ["NO_PROXY"] = _no_proxy
os.environ["no_proxy"] = _no_proxy

# ==========================================
# 配置
# ==========================================
RPC_URL = "http://127.0.0.1:8545"

PRIVATE_KEYS = {
    "admin":    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "operator": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "guardian": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "sponsor":  "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
    "user1":    "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
}

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_PROJECT_ROOT, "out")
TOKEN_JSON_PATH = os.path.join(_OUT_DIR, "SimpleToken.sol", "SimpleToken.json")
ESCROW_JSON_PATH = os.path.join(_OUT_DIR, "EscrowVault.sol", "EscrowVault.json")
PROXY_JSON_PATH = os.path.join(_OUT_DIR, "ERC1967Proxy.sol", "ERC1967Proxy.json")

# ==========================================
# 初始化
# ==========================================
w3 = Web3(Web3.HTTPProvider(RPC_URL))
assert w3.is_connected(), f"无法连接到 {RPC_URL}，请确保 Anvil 正在运行"

accounts = {}
for name, key in PRIVATE_KEYS.items():
    accounts[name] = w3.eth.account.from_key(key)


def load_artifact(path):
    with open(path) as f:
        art = json.load(f)
    return art["abi"], art.get("bytecode", {}).get("object", "")


TOKEN_ABI, TOKEN_BYTECODE = load_artifact(TOKEN_JSON_PATH)
ESCROW_ABI, ESCROW_BYTECODE = load_artifact(ESCROW_JSON_PATH)
PROXY_ABI, PROXY_BYTECODE = load_artifact(PROXY_JSON_PATH)


def send_tx(func, sender_name, value=0):
    acc = accounts[sender_name]
    tx = func.build_transaction({
        "from": acc.address,
        "nonce": w3.eth.get_transaction_count(acc.address),
        "gasPrice": w3.eth.gas_price,
        "value": value,
    })
    signed = w3.eth.account.sign_transaction(tx, acc.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1, f"TX failed: {tx_hash.hex()}"
    return receipt


def send_tx_expect_revert(func, sender_name, expected_msg=None):
    """期望交易 revert"""
    acc = accounts[sender_name]
    try:
        tx = func.build_transaction({
            "from": acc.address,
            "nonce": w3.eth.get_transaction_count(acc.address),
            "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(tx, acc.key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt["status"] == 0:
            return True  # reverted at execution
        assert False, f"Expected revert but TX succeeded: {tx_hash.hex()}"
    except Exception as e:
        err_msg = str(e)
        if expected_msg and expected_msg not in err_msg:
            assert False, f"Expected '{expected_msg}' in error but got: {err_msg}"
        return True


# ==========================================
# 部署合约
# ==========================================
def deploy_contracts():
    print("=" * 60)
    print("部署合约...")
    print("=" * 60)

    # 部署 SimpleToken
    Token = w3.eth.contract(abi=TOKEN_ABI, bytecode=TOKEN_BYTECODE)
    tx = Token.constructor("TestToken", "TT", 1_000_000).build_transaction({
        "from": accounts["admin"].address,
        "nonce": w3.eth.get_transaction_count(accounts["admin"].address),
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, accounts["admin"].key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    token_addr = receipt["contractAddress"]
    print(f"  Token: {token_addr}")

    # 部署 EscrowVault Implementation
    Impl = w3.eth.contract(abi=ESCROW_ABI, bytecode=ESCROW_BYTECODE)
    tx = Impl.constructor().build_transaction({
        "from": accounts["admin"].address,
        "nonce": w3.eth.get_transaction_count(accounts["admin"].address),
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, accounts["admin"].key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    impl_addr = receipt["contractAddress"]

    # 部署 Proxy — 使用 encodeABI 编码 initialize calldata（不触发 gas 估算）
    impl_contract = w3.eth.contract(address=impl_addr, abi=ESCROW_ABI)
    init_data = impl_contract.functions.initialize(
        accounts["admin"].address,
        accounts["operator"].address,
        accounts["guardian"].address,
    )._encode_transaction_data()

    Proxy = w3.eth.contract(abi=PROXY_ABI, bytecode=PROXY_BYTECODE)
    tx = Proxy.constructor(impl_addr, bytes.fromhex(init_data[2:])).build_transaction({
        "from": accounts["admin"].address,
        "nonce": w3.eth.get_transaction_count(accounts["admin"].address),
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, accounts["admin"].key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    proxy_addr = receipt["contractAddress"]
    print(f"  EscrowVault Proxy: {proxy_addr}")

    # 白名单
    escrow = w3.eth.contract(address=proxy_addr, abi=ESCROW_ABI)
    send_tx(escrow.functions.setTokenWhitelist(token_addr, True), "admin")
    print(f"  Token 已加入白名单")

    # 给 sponsor 转 token
    token = w3.eth.contract(address=token_addr, abi=TOKEN_ABI)
    send_tx(token.functions.transfer(accounts["sponsor"].address, 10000 * 10**18), "admin")
    print(f"  已给 sponsor 转 10000 Token\n")

    return token_addr, proxy_addr


# ==========================================
# Merkle Tree 辅助
# ==========================================
def keccak256(data: bytes) -> bytes:
    return Web3.keccak(data)


def encode_leaf(account, token, rootId, cumulativeAmount):
    inner = w3.codec.encode(
        ["address", "address", "uint64", "uint128"],
        [account, token, rootId, cumulativeAmount]
    )
    return keccak256(keccak256(inner))


# ==========================================
# 辅助：创建并结算一个任务
# ==========================================
def create_and_settle_task(escrow, token, token_addr, payout_amount):
    """创建任务并结算，返回 taskId"""
    budget = 100 * 10**18
    seed_secret = secrets.token_bytes(32)
    seed_commit = keccak256(seed_secret)

    task_id = keccak256(secrets.token_bytes(32))

    # approve
    send_tx(token.functions.approve(escrow.address, budget), "sponsor")

    # 获取当前区块时间，设置未来的 deadline
    current_block = w3.eth.get_block("latest")
    current_time = current_block["timestamp"]
    qualify_deadline = current_time + 60       # 60 秒后
    settlement_deadline = current_time + 3600  # 1 小时后

    # createTask
    send_tx(escrow.functions.createTask(
        task_id, token_addr, budget,
        budget - 2 * 10**18,  # basePool (留 2% 平台费空间)
        0, 0,  # no lottery
        qualify_deadline, settlement_deadline,
        seed_commit
    ), "sponsor")

    # warp past qualifyDeadline
    w3.provider.make_request("evm_increaseTime", [61])
    w3.provider.make_request("evm_mine", [])

    # finalizeQualification
    send_tx(escrow.functions.finalizeQualification(
        task_id, 1, keccak256(b"qualified"), keccak256(b"manifest")
    ), "operator")

    # settleTask
    platform_fee = budget * 200 // 10000  # 2%
    refund = budget - payout_amount - platform_fee

    send_tx(escrow.functions.settleTask(
        task_id, seed_secret, 0, bytes(32),
        keccak256(b"result"),
        payout_amount, refund,
        payout_amount, 0  # baseRewardPerQualified = payoutAmount, 0 winners
    ), "operator")

    return task_id


# ==========================================
# TEST SOL-04: Merkle 领取不能超出 totalAllocated
# ==========================================
def test_sol04(token_addr, escrow_addr):
    print("─" * 60)
    print("TEST SOL-04: Merkle 领取不能超出 totalAllocated")
    print("─" * 60)

    escrow = w3.eth.contract(address=escrow_addr, abi=ESCROW_ABI)
    token = w3.eth.contract(address=token_addr, abi=TOKEN_ABI)

    # 创建任务并结算，payout = 10 Token
    payout = 10 * 10**18
    create_and_settle_task(escrow, token, token_addr, payout)

    # publishPendingRoot: epochDelta = payout (10 Token)
    root_id = 1
    # 构建一个包含超额领取的 leaf (cumulativeAmount = 50 Token >> 10 Token allocated)
    inflated_amount = 50 * 10**18
    leaf = encode_leaf(accounts["user1"].address, token_addr, root_id, inflated_amount)
    merkle_root = leaf  # 单叶树，root = leaf

    send_tx(escrow.functions.publishPendingRoot(
        token_addr, root_id, merkle_root, payout, 3600, keccak256(b"manifest")
    ), "operator")

    # 等待并激活
    w3.provider.make_request("evm_increaseTime", [3601])
    w3.provider.make_request("evm_mine", [])
    send_tx(escrow.functions.activateRoot(token_addr, root_id), "admin")

    # 验证 totalAllocated = 10 Token
    active_root = escrow.functions.activeRoots(token_addr).call()
    assert active_root[2] == payout, f"totalAllocated 不匹配: {active_root[2]} != {payout}"

    # 尝试 claim 50 Token (超出 totalAllocated)
    proof = []  # 单叶树不需要 proof
    reverted = send_tx_expect_revert(
        escrow.functions.claim(token_addr, root_id, inflated_amount, proof, accounts["user1"].address),
        "user1",
        "Exceeds total allocated"
    )
    assert reverted
    print("  ✅ SOL-04 验证通过: 超额 claim 被正确 revert (Exceeds total allocated)\n")


# ==========================================
# TEST SOL-05: 审查窗口最小值 + 溢出防护
# ==========================================
def test_sol05(token_addr, escrow_addr):
    print("─" * 60)
    print("TEST SOL-05: 审查窗口最小值 + 溢出防护")
    print("─" * 60)

    escrow = w3.eth.contract(address=escrow_addr, abi=ESCROW_ABI)
    token = w3.eth.contract(address=token_addr, abi=TOKEN_ABI)

    # 创建并结算一个任务
    payout = 5 * 10**18
    create_and_settle_task(escrow, token, token_addr, payout)

    # 检查是否有 pending root 阻塞（清理）
    pending = escrow.functions.pendingRoots(token_addr).call()
    if pending[0] > 0:  # rootId > 0 说明有 pending
        # cancel it
        send_tx(escrow.functions.cancelPendingRoot(token_addr, pending[0]), "guardian")

    active = escrow.functions.activeRoots(token_addr).call()
    root_id = active[0] + 1

    # 测试 1: delayWindow = 0 → 应该 revert "Delay too short"
    reverted = send_tx_expect_revert(
        escrow.functions.publishPendingRoot(
            token_addr, root_id, keccak256(b"root"), payout, 0, keccak256(b"m")
        ),
        "operator",
        "Delay too short"
    )
    assert reverted
    print("  ✅ delayWindow=0 被正确 revert (Delay too short)")

    # 测试 2: delayWindow = 100 (< 3600) → 应该 revert
    reverted = send_tx_expect_revert(
        escrow.functions.publishPendingRoot(
            token_addr, root_id, keccak256(b"root"), payout, 100, keccak256(b"m")
        ),
        "operator",
        "Delay too short"
    )
    assert reverted
    print("  ✅ delayWindow=100 被正确 revert (Delay too short)")

    # 测试 3: delayWindow = 3600 → 应该成功
    send_tx(escrow.functions.publishPendingRoot(
        token_addr, root_id, keccak256(b"root"), payout, 3600, keccak256(b"m")
    ), "operator")
    print("  ✅ delayWindow=3600 正常发布成功")

    # 清理 pending root
    send_tx(escrow.functions.cancelPendingRoot(token_addr, root_id), "guardian")
    print("  ✅ SOL-05 验证通过: 最小审查窗口强制生效\n")


# ==========================================
# TEST SOL-07: 零地址检查
# ==========================================
def test_sol07(token_addr):
    print("─" * 60)
    print("TEST SOL-07: SimpleToken 零地址检查")
    print("─" * 60)

    token = w3.eth.contract(address=token_addr, abi=TOKEN_ABI)

    # 测试 transfer to address(0)
    reverted = send_tx_expect_revert(
        token.functions.transfer("0x0000000000000000000000000000000000000000", 1),
        "sponsor",
        "Transfer to zero address"
    )
    assert reverted
    print("  ✅ transfer(address(0)) 被正确 revert")

    # 测试 transferFrom to address(0)
    # 先 approve
    send_tx(token.functions.approve(accounts["admin"].address, 100), "sponsor")

    reverted = send_tx_expect_revert(
        token.functions.transferFrom(
            accounts["sponsor"].address,
            "0x0000000000000000000000000000000000000000",
            1
        ),
        "admin",
        "Transfer to zero address"
    )
    assert reverted
    print("  ✅ transferFrom(address(0)) 被正确 revert")
    print("  ✅ SOL-07 验证通过: 零地址转账被阻止\n")


# ==========================================
# TEST SOL-08: 角色轮换验证旧地址
# ==========================================
def test_sol08(escrow_addr):
    print("─" * 60)
    print("TEST SOL-08: 角色轮换验证旧地址持有角色")
    print("─" * 60)

    escrow = w3.eth.contract(address=escrow_addr, abi=ESCROW_ABI)

    # 测试 updateOperator 传入错误的旧地址 → 应 revert
    fake_old_operator = accounts["user1"].address  # user1 不是 operator
    new_operator = accounts["sponsor"].address

    reverted = send_tx_expect_revert(
        escrow.functions.updateOperator(fake_old_operator, new_operator),
        "admin",
        "oldOperator lacks role"
    )
    assert reverted
    print("  ✅ updateOperator(错误旧地址) 被正确 revert (oldOperator lacks role)")

    # 测试 updateGuardian 传入错误的旧地址 → 应 revert
    fake_old_guardian = accounts["user1"].address
    new_guardian = accounts["sponsor"].address

    reverted = send_tx_expect_revert(
        escrow.functions.updateGuardian(fake_old_guardian, new_guardian),
        "admin",
        "oldGuardian lacks role"
    )
    assert reverted
    print("  ✅ updateGuardian(错误旧地址) 被正确 revert (oldGuardian lacks role)")

    # 测试正确的旧地址 → 应成功
    real_operator = accounts["operator"].address
    send_tx(escrow.functions.updateOperator(real_operator, new_operator), "admin")
    print("  ✅ updateOperator(正确旧地址) 正常执行")

    # 换回来
    send_tx(escrow.functions.updateOperator(new_operator, real_operator), "admin")
    print("  ✅ SOL-08 验证通过: 角色轮换错误输入被阻止\n")


# ==========================================
# TEST SOL-10: 代币下架后紧急释放
# ==========================================
def test_sol10(token_addr, escrow_addr):
    print("─" * 60)
    print("TEST SOL-10: 代币下架后紧急释放资金")
    print("─" * 60)

    escrow = w3.eth.contract(address=escrow_addr, abi=ESCROW_ABI)
    token = w3.eth.contract(address=token_addr, abi=TOKEN_ABI)

    # 先结算一个任务让 settledButUnallocated 有余额
    payout = 8 * 10**18
    create_and_settle_task(escrow, token, token_addr, payout)

    unallocated = escrow.functions.settledButUnallocated(token_addr).call()
    print(f"  当前 settledButUnallocated: {unallocated / 10**18} Token")
    assert unallocated >= payout

    # 测试 1: 代币仍在白名单时调用 → 应 revert
    reverted = send_tx_expect_revert(
        escrow.functions.emergencyReleaseDelistedFunds(
            token_addr, accounts["admin"].address, payout
        ),
        "admin",
        "Token still whitelisted"
    )
    assert reverted
    print("  ✅ 代币在白名单时调用被 revert (Token still whitelisted)")

    # 移除代币白名单
    send_tx(escrow.functions.setTokenWhitelist(token_addr, False), "admin")
    print("  ✅ 代币已从白名单移除")

    # 测试 2: publishPendingRoot 应该失败（代币已下架）
    pending = escrow.functions.pendingRoots(token_addr).call()
    if pending[0] > 0:
        send_tx(escrow.functions.cancelPendingRoot(token_addr, pending[0]), "guardian")

    active = escrow.functions.activeRoots(token_addr).call()
    root_id = active[0] + 1

    reverted = send_tx_expect_revert(
        escrow.functions.publishPendingRoot(
            token_addr, root_id, keccak256(b"root"), payout, 3600, keccak256(b"m")
        ),
        "operator",
        "Token not allowed"
    )
    assert reverted
    print("  ✅ 代币下架后 publishPendingRoot 被正确阻止")

    # 测试 3: emergencyReleaseDelistedFunds 应该成功
    admin_balance_before = token.functions.balanceOf(accounts["admin"].address).call()
    send_tx(escrow.functions.emergencyReleaseDelistedFunds(
        token_addr, accounts["admin"].address, payout
    ), "admin")
    admin_balance_after = token.functions.balanceOf(accounts["admin"].address).call()

    released = admin_balance_after - admin_balance_before
    assert released == payout, f"释放金额不匹配: {released} != {payout}"
    print(f"  ✅ 紧急释放成功: {released / 10**18} Token 已转出")

    # 验证 settledButUnallocated 减少
    new_unallocated = escrow.functions.settledButUnallocated(token_addr).call()
    assert new_unallocated == unallocated - payout
    print(f"  ✅ settledButUnallocated 正确减少: {new_unallocated / 10**18} Token")

    # 恢复白名单（给后续测试用）
    send_tx(escrow.functions.setTokenWhitelist(token_addr, True), "admin")
    print("  ✅ SOL-10 验证通过: 紧急释放路径工作正常\n")


# ==========================================
# 主入口
# ==========================================
def main():
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║   CertiK 审计修复验证测试 (SOL-04/05/07/08/10)        ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    token_addr, escrow_addr = deploy_contracts()

    test_sol07(token_addr)       # SimpleToken 零地址检查
    test_sol08(escrow_addr)      # 角色轮换验证
    test_sol05(token_addr, escrow_addr)  # 审查窗口限制
    test_sol04(token_addr, escrow_addr)  # Merkle 超额领取
    test_sol10(token_addr, escrow_addr)  # 代币下架紧急释放

    print("═" * 60)
    print("🎉 全部 5 项代码修复验证通过!")
    print("═" * 60)
    print()
    print("验证结果汇总:")
    print("  SOL-04 ✅ claim() 超额领取被链上拦截")
    print("  SOL-05 ✅ delayWindow < MIN_DELAY_WINDOW 被 revert")
    print("  SOL-07 ✅ transfer/transferFrom to address(0) 被 revert")
    print("  SOL-08 ✅ 角色轮换传错旧地址被 revert")
    print("  SOL-10 ✅ 代币下架后 emergencyReleaseDelistedFunds 正常释放")
    print()


if __name__ == "__main__":
    main()
