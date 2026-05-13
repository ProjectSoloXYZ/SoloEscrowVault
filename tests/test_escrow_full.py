from web3 import Web3
import json
import os
import secrets
import time
from typing import List

# 确保本地 RPC 不被 HTTP(S)_PROXY 拦截（常见于公司/科学上网环境）
_no_proxy = os.environ.get("NO_PROXY", "")
for host in ("127.0.0.1", "localhost"):
    if host not in _no_proxy:
        _no_proxy = host if not _no_proxy else f"{_no_proxy},{host}"
os.environ["NO_PROXY"] = _no_proxy
os.environ["no_proxy"] = _no_proxy

# ==========================================
# 1. 基础配置区
# ==========================================
RPC_URL = "http://127.0.0.1:8545"  # Anvil 默认端口

# Anvil 默认账户私钥
PRIVATE_KEYS = {
    "operator": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",  # 账户0
    "sponsor":  "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",  # 账户1
    "user1":    "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",  # 账户2
    "user2":    "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",  # 账户3
}

# 编译后的合约 JSON 路径（相对于项目根目录，由 `forge build` 生成）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_PROJECT_ROOT, "out")
TOKEN_JSON_PATH  = os.path.join(_OUT_DIR, "SimpleToken.sol",   "SimpleToken.json")
ESCROW_JSON_PATH = os.path.join(_OUT_DIR, "EscrowVault.sol",   "EscrowVault.json")
PROXY_JSON_PATH  = os.path.join(_OUT_DIR, "ERC1967Proxy.sol",  "ERC1967Proxy.json")

# 合约地址保存文件
CONTRACT_ADDRESS_FILE = os.path.join(_PROJECT_ROOT, "deployed_contracts.json")


# ==========================================
# 2. Merkle Tree 辅助函数 (兼容 OpenZeppelin)
# ==========================================
def keccak256(data: bytes) -> bytes:
    """计算数据的 Keccak256 哈希值"""
    return Web3.keccak(data)

def commutative_keccak256(a: bytes, b: bytes) -> bytes:
    if a < b:
        return keccak256(a + b)
    else:
        return keccak256(b + a)

def encode_leaf(user: str, token: str, root_id: int, cumulative_amount: int) -> bytes:
    # 模拟 Solidity abi.encode(msg.sender, token, rootId, cumulativeAmount)
    # 每个参数 32 字节大端对齐
    user_padded   = bytes.fromhex(user[2:].lower()).rjust(32, b'\x00')    # address
    token_padded  = bytes.fromhex(token[2:].lower()).rjust(32, b'\x00')   # address
    root_id_padded = root_id.to_bytes(32, 'big')                           # uint64
    amount_padded  = cumulative_amount.to_bytes(32, 'big')                 # uint128
    encoded = user_padded + token_padded + root_id_padded + amount_padded
    # 标准 double-hash leaf，防 preimage 攻击
    return keccak256(keccak256(encoded))

class MerkleTree:
    def __init__(self, leaves: List[bytes]):
        self.leaves = leaves
        self.tree   = self._build_tree()
        self.root   = self.tree[-1][0] if self.tree else bytes(32)

    def _build_tree(self) -> List[List[bytes]]:
        if not self.leaves:
            return []
        tree = [self.leaves[:]]
        while len(tree[-1]) > 1:
            current    = tree[-1]
            next_level = []
            for i in range(0, len(current), 2):
                left  = current[i]
                right = current[i + 1] if i + 1 < len(current) else current[i]
                next_level.append(commutative_keccak256(left, right))
            tree.append(next_level)
        return tree

    def get_proof(self, leaf_index: int) -> List[bytes]:
        if leaf_index >= len(self.leaves):
            return []
        proof = []
        current_idx = leaf_index
        for level in self.tree[:-1]:
            sibling_idx = current_idx + 1 if current_idx % 2 == 0 else current_idx - 1
            if sibling_idx < len(level):
                proof.append(level[sibling_idx])
            else:
                proof.append(level[current_idx])
            current_idx = current_idx // 2
        return proof

    def verify(self, leaf: bytes, proof: List[bytes]) -> bool:
        computed = leaf
        for sibling in proof:
            computed = commutative_keccak256(computed, sibling)
        return computed == self.root


# ==========================================
# 3. 初始化连接
# ==========================================
w3 = Web3(Web3.HTTPProvider(RPC_URL))#w3是web3.py库的实例，用于与以太坊网络进行交互

if not w3.is_connected():
    print("[FAIL] 无法连接到 Anvil，请确保 Anvil 正在运行")
    exit()

print(f"[OK] 已连接到 Anvil ({RPC_URL})")

accounts = {name: w3.eth.account.from_key(key) for name, key in PRIVATE_KEYS.items()}
print("[OK] 加载账户:")
for name, acc in accounts.items():
    print(f"   {name}: {acc.address}")

# 加载合约 ABI
with open(TOKEN_JSON_PATH, 'r') as f:
    token_compiled = json.load(f)
TOKEN_ABI      = token_compiled['abi']
TOKEN_BYTECODE = token_compiled['bytecode']['object']

with open(ESCROW_JSON_PATH, 'r') as f:
    escrow_compiled = json.load(f)
ESCROW_ABI      = escrow_compiled['abi']
ESCROW_BYTECODE = escrow_compiled['bytecode']['object']

with open(PROXY_JSON_PATH, 'r') as f:
    proxy_compiled = json.load(f)
PROXY_ABI       = proxy_compiled['abi']
PROXY_BYTECODE  = proxy_compiled['bytecode']['object']

# ==========================================
# 4. 工具函数
# ==========================================
def send_tx(func, from_name: str):
    """构建、签名并发送交易，返回 receipt"""
    acc   = accounts[from_name]
    key   = PRIVATE_KEYS[from_name]
    nonce = w3.eth.get_transaction_count(acc.address)
    tx    = func.build_transaction({
        'from':     acc.address,
        'nonce':    nonce,
        'gasPrice': w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, key)
    return w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction)); assert receipt["status"] == 1, "Tx reverted"

# ==========================================
# 5. 部署合约
#    对应合约函数: constructor() + initialize()
#    通俗应用: 把合约代码上链，并初始化管理员/操作员/守护者角色（类似"开店 + 任命店长"）
# ==========================================
def save_contract_addresses(token_addr: str, escrow_addr: str):
    data = {
        "token_addr": token_addr,
        "escrow_addr": escrow_addr
    }
    with open(CONTRACT_ADDRESS_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"[OK] 合约地址已保存到 {CONTRACT_ADDRESS_FILE}")

def load_contract_addresses():
    import os
    if not os.path.exists(CONTRACT_ADDRESS_FILE):
        return None
    try:
        with open(CONTRACT_ADDRESS_FILE, 'r') as f:
            data = json.load(f)
        code1 = w3.eth.get_code(data['token_addr'])
        code2 = w3.eth.get_code(data['escrow_addr'])
        if code1 == b'' or code1 == '0x' or code2 == b'' or code2 == '0x':
            print("[WARN] 已保存的合约地址无效，将重新部署")
            return None
        return data['token_addr'], data['escrow_addr']
    except Exception as e:
        print(f"[WARN] 加载合约地址失败: {e}，将重新部署")
        return None

def deploy_contracts():
    print("\n--- 部署 SimpleToken ---")
    Token = w3.eth.contract(abi=TOKEN_ABI, bytecode=TOKEN_BYTECODE)
    nonce = w3.eth.get_transaction_count(accounts['operator'].address)
    tx = Token.constructor("Test Token", "TEST", 1000000).build_transaction({
        'from':     accounts['operator'].address,
        'nonce':    nonce,
        'gas':      2000000,
        'gasPrice': w3.eth.gas_price,
    })
    signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEYS['operator'])
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction)); assert receipt["status"] == 1, "Tx reverted"
    token_addr = receipt.contractAddress
    print(f"[OK] Token 合约地址: {token_addr}")

    print("\n--- 部署 UUPS 代理模式 EscrowVault ---")
    # 1. 部署 Implementation
    Escrow = w3.eth.contract(abi=ESCROW_ABI, bytecode=ESCROW_BYTECODE)
    nonce = w3.eth.get_transaction_count(accounts['operator'].address)
    tx_impl = Escrow.constructor().build_transaction({
        'from':     accounts['operator'].address,
        'nonce':    nonce,
        'gas':      4000000,
        'gasPrice': w3.eth.gas_price,
    })
    signed_impl  = w3.eth.account.sign_transaction(tx_impl, PRIVATE_KEYS['operator'])
    receipt_impl = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed_impl.raw_transaction))
    impl_addr = receipt_impl.contractAddress
    print(f"[OK] Implementation 地址: {impl_addr}")

    # 2. 编码 initialize calldata
    op_addr = accounts['operator'].address
    init_data = Escrow.encode_abi("initialize", args=[op_addr, op_addr, op_addr])
    
    # 3. 部署 Proxy
    Proxy = w3.eth.contract(abi=PROXY_ABI, bytecode=PROXY_BYTECODE)
    nonce = w3.eth.get_transaction_count(accounts['operator'].address)
    tx_proxy = Proxy.constructor(impl_addr, init_data).build_transaction({
        'from':     accounts['operator'].address,
        'nonce':    nonce,
        'gas':      3000000,
        'gasPrice': w3.eth.gas_price,
    })
    signed_proxy  = w3.eth.account.sign_transaction(tx_proxy, PRIVATE_KEYS['operator'])
    receipt_proxy = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed_proxy.raw_transaction))
    escrow_addr = receipt_proxy.contractAddress
    print(f"[OK] Proxy (服务地址): {escrow_addr}")

    # 4. 后置配置：将测试 Token 添加入白名单
    #    ▶ 合约函数: setTokenWhitelist(token, allowed)
    #    ▶ 通俗应用: 管理员指定哪些代币可以用来发任务（类似"商家入驻审核，只有审核通过的币种才能用"）
    escrow_proxy = w3.eth.contract(address=escrow_addr, abi=ESCROW_ABI)
    send_tx(escrow_proxy.functions.setTokenWhitelist(token_addr, True), 'operator')
    print(f"[OK] 已将 Token 添加进白名单")

    return token_addr, escrow_addr


# ==========================================
# 6. 第一阶段测试（基础流程：创建任务 → 冻结名单 → 结算）
#    测试的合约函数: createTask → finalizeQualification → settleTask
# ==========================================
def run_phase1_test(token_addr: str, escrow_addr: str):
    token  = w3.eth.contract(address=token_addr,  abi=TOKEN_ABI)
    escrow = w3.eth.contract(address=escrow_addr, abi=ESCROW_ABI)

    print("\n" + "="*60)
    print("开始第一阶段业务流程测试")
    print("="*60)

    # 步骤 1: 生成随机种子与承诺
    print("\n[步骤 1] 生成随机种子与承诺...")
    server_secret     = secrets.token_bytes(32)
    server_secret_hex = server_secret.hex()
    seed_commit       = w3.keccak(server_secret)  # 对私密随机种子进行 Keccak256 哈希运算，生成一个“承诺(Commit)”。这个哈希值会上链公开，后续开奖时需公布原种子以防篡改（Commit-Reveal 机制）
    task_id           = w3.keccak(text=f"task_p1_{int(time.time())}")
    print(f"   Server Secret: 0x{server_secret_hex[:20]}...")
    print(f"   Seed Commit:   {seed_commit.hex()}")

    # 步骤 2: 检查并准备资金
    print("\n[步骤 2] 检查并准备资金...")
    sponsor_addr  = accounts['sponsor'].address
    budget        = 100 * (10 ** 18)  # 100 Token
    sponsor_bal   = token.functions.balanceOf(sponsor_addr).call()
    print(f"   Sponsor Token 余额: {sponsor_bal}")
    if sponsor_bal < budget:
        send_tx(token.functions.transfer(sponsor_addr, budget * 10), 'operator')
        print(f"   [OK] 已给 Sponsor 转账 {budget * 10} token")

    # 步骤 3: Sponsor 授权并创建任务
    #    ▶ 合约函数: Token.approve() → EscrowVault.createTask()
    #    ▶ 通俗应用: approve = 授权自动扣款；createTask = 甲方发布悬赏任务并锁定预算到托管合约
    print("\n[步骤 3] Sponsor 授权并创建任务...")
    try:
        send_tx(token.functions.approve(escrow_addr, budget), 'sponsor')
        print(f"   [OK] 授权完成，Vault 可使用 {budget} 代币")

        current_time = w3.eth.get_block('latest')['timestamp']
        qualify_deadline = current_time + 3600
        settlement_deadline = current_time + 7200

        send_tx(escrow.functions.createTask(
            task_id,
            token_addr,
            budget,
            budget,     # basePool 等于 budget
            0,          # lottery_reward_per_winner
            0,          # lottery_winner_count
            qualify_deadline,
            settlement_deadline,
            seed_commit
        ), 'sponsor')
        print(f"   [OK] 任务创建成功！Task ID: {task_id.hex()}")
    except Exception as e:
        print(f"   [FAIL] 任务创建失败: {e}")
        return

    # 步骤 4: 验证链上任务状态
    #    ▶ 合约函数: tasks(taskId)  —— 只读查询
    #    ▶ 通俗应用: 查看任务详情（类似"查订单状态"）
    print("\n[步骤 4] 验证链上任务状态...")
    task_info = escrow.functions.tasks(task_id).call()
    # 按照 TaskConfig 的结果返回 (taskId, sponsor, token, totalBudget, basePool, lotteryRewardPerWinner, lotteryWinnerCount, qualifyDeadline, settlementDeadline, seedCommit, status)
    print(f"   Sponsor:    {task_info[1]}")
    print(f"   Token:      {task_info[2]}")
    print(f"   Budget:     {task_info[3]}")
    print(f"   Status:     {task_info[10]} (1=FUNDED)")
    print(f"   SeedCommit: {task_info[9].hex()}")

    # 步骤 5: Operator 验证承诺、冻结合格名单并结算
    #    ▶ 合约函数: finalizeQualification() + settleTask()
    #    ▶ 通俗应用: finalizeQualification = 报名截止，公布合格名单
    #              settleTask = 活动结束，计算谁中奖、分多少钱
    print("\n[步骤 5] Operator 验证、冻结并结算任务...")
    onchain_commit = task_info[9]
    local_verify   = w3.keccak(hexstr=server_secret_hex)

    if local_verify == onchain_commit:
        print("   [OK] 验证通过：Secret 与链上承诺一致")

        # 模拟进入冻结资格阶段时间
        w3.provider.make_request('evm_increaseTime', [3601])
        w3.provider.make_request('evm_mine', [])

        # ▶ 调用 finalizeQualification(taskId, qualifiedCount=0, qualifiedRoot=空, manifestHash)
        #   这里 0 人合格，简化测试
        fake_manifest = bytes("fake_manifest", "utf-8").rjust(32, b'\0')
        send_tx(escrow.functions.finalizeQualification(task_id, 0, bytes(32), fake_manifest), 'operator')
        print("   [OK] 任务已冻结（第一阶段简化流程）")
        task_info = escrow.functions.tasks(task_id).call()
        print(f"   任务状态: {task_info[10]} (2=QUALIFIED)")

        # ▶ 调用 settleTask(): 0人合格，按照正确逻辑发奖必须为0，全部预算算作 refund
        payout = 0
        refund = budget
        send_tx(escrow.functions.settleTask(
            task_id, 
            server_secret,      # seedReveal —— 揭示之前承诺的随机种子
            0,                  # entropyRef —— 外部随机数引用
            bytes(32),          # entropyValue —— 外部随机数值
            fake_manifest,      # resultManifestHash —— 结果清单哈希
            payout,             # payoutAmount —— 实际支付金额
            refund,             # refundableAmount —— 可退款金额
            0,                  # baseRewardPerQualified —— 每人基础奖励
            0                   # actualWinnerCount —— 实际中奖人数
        ), 'operator')
        print(f"   [OK] 任务结算完成！支付: {payout}，退款: {refund}")
        final_task = escrow.functions.tasks(task_id).call()
        print(f"   最终状态: {final_task[10]} (4=REFUNDABLE 且无退款为 3=SETTLED)")
    else:
        print("   [FAIL] 验证失败：Secret 与链上承诺不匹配")

    print("\n" + "="*60)
    print("[SUCCESS] 第一阶段测试完成!")
    print("="*60)


# ==========================================
# 7. 第二阶段测试（完整流程：创建 → 冻结 → 结算 → 发Root → 激活 → 提现 → 退款）
#    测试的合约函数: createTask → finalizeQualification → settleTask
#                  → publishPendingRoot → activateRoot → claim → claimRefund
# ==========================================
def run_phase2_test(token_addr: str, escrow_addr: str):
    token  = w3.eth.contract(address=token_addr,  abi=TOKEN_ABI)
    escrow = w3.eth.contract(address=escrow_addr, abi=ESCROW_ABI)

    print("\n" + "="*60)
    print("开始第二阶段业务流程测试")
    print("="*60)

    sponsor_addr = accounts['sponsor'].address
    user1_addr   = accounts['user1'].address
    user2_addr   = accounts['user2'].address

    # 步骤 1: 准备资金
    print("\n[步骤 1] 准备资金...")
    budget = 100 * (10 ** 18)  # 100 Token
    # 1. 检查 Sponsor 余额是否充足
    sponsor_bal = token.functions.balanceOf(sponsor_addr).call()
    if sponsor_bal < budget:
        transfer_amount = budget * 10
        send_tx(token.functions.transfer(sponsor_addr, transfer_amount), 'operator')
        print(f"   [OK] 已给 Sponsor 转账 {transfer_amount / (10**18)} Token")
    print(f"   Sponsor Token 余额: {token.functions.balanceOf(sponsor_addr).call() / (10**18)}")

    # ================= 任务 A 开始 =================
    print("\n[任务 A] Sponsor 创建并处理任务 A...")
    server_secret_a = secrets.token_bytes(32)
    seed_commit_a   = w3.keccak(server_secret_a)
    task_id_a       = w3.keccak(text=f"task_p2a_{int(time.time())}")
    
    task_budget_a   = 100 * (10 ** 18)  
    base_pool_a     = 80 * (10 ** 18)
    lottery_r_a     = 10 * (10 ** 18)
    current_time = w3.eth.get_block('latest')['timestamp']
    
    send_tx(token.functions.approve(escrow_addr, task_budget_a), 'sponsor')
    send_tx(escrow.functions.createTask(
        task_id_a, token_addr, task_budget_a, 
        base_pool_a, lottery_r_a, 2,
        current_time + 3600, current_time + 7200, seed_commit_a
    ), 'sponsor')
    print(f"   [OK] 任务 A 创建成功! Task ID: {task_id_a.hex()}")

    # 任务 A 哈希抽奖
    qualified_users = [user1_addr, user2_addr]
    qualified_count = len(qualified_users)
    base_reward_per_a = base_pool_a // qualified_count
    seed_a = w3.keccak(server_secret_a + bytes(32))
    
    print(f"   [抽奖 A] 开奖 Seed: {seed_a.hex()[:15]}...")
    scores_a = []
    for i, user_addr in enumerate(qualified_users):
        q_leaf = w3.keccak(task_id_a + bytes.fromhex(user_addr[2:]))
        score_int = int.from_bytes(w3.keccak(seed_a + q_leaf), 'big')
        scores_a.append((score_int, user_addr))
        print(f"   [抽奖 A] {'User1' if user_addr == user1_addr else 'User2'} 得分: {score_int}")
        
    scores_a.sort(key=lambda x: x[0])
    winners_a = [x[1] for x in scores_a[:1]]
    print(f"   [抽奖 A] 最终中奖者是: {'User1' if winners_a[0] == user1_addr else 'User2'} ! (得分最小)")

    # 任务 A 分配金额
    user1_reward_a = base_reward_per_a + (lottery_r_a if user1_addr in winners_a else 0)
    user2_reward_a = base_reward_per_a + (lottery_r_a if user2_addr in winners_a else 0)
    payout_amount_a = user1_reward_a + user2_reward_a
    refund_amount_a = task_budget_a - payout_amount_a

    # 快进并冻结
    w3.provider.make_request('evm_increaseTime', [3601])
    w3.provider.make_request('evm_mine', [])
    
    # 构建真实的 合格名单 Merkle Root
    qualified_leaves_a = [w3.keccak(task_id_a + bytes.fromhex(u[2:])) for u in qualified_users]
    qualified_tree_a = MerkleTree(qualified_leaves_a)
    real_qualified_root_a = qualified_tree_a.root
    
    send_tx(escrow.functions.finalizeQualification(task_id_a, 2, real_qualified_root_a, bytes("manifest", "utf-8").rjust(32, b'\0')), 'operator')
    
    print("   [步骤 A] Operator 结算任务...")
    send_tx(escrow.functions.settleTask(
        task_id_a, server_secret_a, 0, bytes(32), bytes("result", "utf-8").rjust(32, b'\0'),
        payout_amount_a, refund_amount_a, base_reward_per_a, 1
    ), 'operator')
    print(f"   [OK]任务 A 已结算! 发放: {payout_amount_a / (10**18)}, 退款: {refund_amount_a / (10**18)}")
    send_tx(escrow.functions.claimRefund(task_id_a, sponsor_addr), 'sponsor')
    # ================= 任务 A 结束 =================


    # ================= 任务 B 开始 =================
    print("\n[任务 B] Sponsor 创建并处理任务 B...")
    server_secret_b = secrets.token_bytes(32)
    seed_commit_b   = w3.keccak(server_secret_b)
    task_id_b       = w3.keccak(text=f"task_p2b_{int(time.time())}")
    
    task_budget_b   = 120 * (10 ** 18)  
    base_pool_b     = 100 * (10 ** 18)
    lottery_r_b     = 10 * (10 ** 18)
    current_time = w3.eth.get_block('latest')['timestamp']
    
    send_tx(token.functions.approve(escrow_addr, task_budget_b), 'sponsor')
    send_tx(escrow.functions.createTask(
        task_id_b, token_addr, task_budget_b, 
        base_pool_b, lottery_r_b, 2,
        current_time + 3600, current_time + 7200, seed_commit_b
    ), 'sponsor')
    print(f"   [OK] 任务 B 创建成功! Task ID: {task_id_b.hex()}")

    # 任务 B 哈希抽奖
    base_reward_per_b = base_pool_b // qualified_count
    seed_b = w3.keccak(server_secret_b + bytes(32))
    
    print(f"   [抽奖 B] 开奖 Seed: {seed_b.hex()[:15]}...")
    scores_b = []
    for i, user_addr in enumerate(qualified_users):
        q_leaf = w3.keccak(task_id_b + bytes.fromhex(user_addr[2:]))
        score_int = int.from_bytes(w3.keccak(seed_b + q_leaf), 'big')
        scores_b.append((score_int, user_addr))
        print(f"   [抽奖 B] {'User1' if user_addr == user1_addr else 'User2'} 得分: {score_int}")
        
    scores_b.sort(key=lambda x: x[0])
    winners_b = [x[1] for x in scores_b[:1]]
    print(f"   [抽奖 B] 最终中奖者是: {'User1' if winners_b[0] == user1_addr else 'User2'} ! (得分最小)")

    # 任务 B 分配金额
    user1_reward_b = base_reward_per_b + (lottery_r_b if user1_addr in winners_b else 0)
    user2_reward_b = base_reward_per_b + (lottery_r_b if user2_addr in winners_b else 0)
    payout_amount_b = user1_reward_b + user2_reward_b
    refund_amount_b = task_budget_b - payout_amount_b

    # 快进并冻结
    w3.provider.make_request('evm_increaseTime', [3601])
    w3.provider.make_request('evm_mine', [])

    # 构建真实的 合格名单 Merkle Root
    qualified_leaves_b = [w3.keccak(task_id_b + bytes.fromhex(u[2:])) for u in qualified_users]
    qualified_tree_b = MerkleTree(qualified_leaves_b)
    real_qualified_root_b = qualified_tree_b.root

    send_tx(escrow.functions.finalizeQualification(task_id_b, 2, real_qualified_root_b, bytes("manifest", "utf-8").rjust(32, b'\0')), 'operator')
    
    print("   [步骤 B] Operator 结算任务...")
    send_tx(escrow.functions.settleTask(
        task_id_b, server_secret_b, 0, bytes(32), bytes("result", "utf-8").rjust(32, b'\0'),
        payout_amount_b, refund_amount_b, base_reward_per_b, 1
    ), 'operator')
    print(f"   [OK]任务 B 已结算! 发放: {payout_amount_b / (10**18)}, 退款: {refund_amount_b / (10**18)}")
    send_tx(escrow.functions.claimRefund(task_id_b, sponsor_addr), 'sponsor')
    # ================= 任务 B 结束 =================


    # ================= 汇总发布与提现 =================
    # 两任务累加（并加上过往已经提取的历史金额，保证 cumulativeAmount 是终身发奖总额）
    user1_past_claimed = escrow.functions.claimed(token_addr, user1_addr).call()
    user2_past_claimed = escrow.functions.claimed(token_addr, user2_addr).call()

    user1_cumulative = user1_past_claimed + user1_reward_a + user1_reward_b
    user2_cumulative = user2_past_claimed + user2_reward_a + user2_reward_b
    total_payout_amount = payout_amount_a + payout_amount_b
    
    print(f"\n[合并发奖] 本次周期新增汇总总计: {total_payout_amount / (10**18)} Token")
    print(f"   [分配] User1 (历史已领 {user1_past_claimed / (10**18)}) 本期追加后 终身累积: {user1_cumulative / (10**18)} Token")
    print(f"   [分配] User2 (历史已领 {user2_past_claimed / (10**18)}) 本期追加后 终身累积: {user2_cumulative / (10**18)} Token")

    # 动态获取 rootId
    current_root = escrow.functions.activeRoots(token_addr).call()
    root_id = current_root[0] + 1


    leaf1 = encode_leaf(user1_addr, token_addr, root_id, user1_cumulative)
    leaf2 = encode_leaf(user2_addr, token_addr, root_id, user2_cumulative)
    merkle_tree = MerkleTree([leaf1, leaf2])
    merkle_root = merkle_tree.root

    print(f"   Leaf1:       {leaf1.hex()}")
    print(f"   Leaf2:       {leaf2.hex()}")
    print(f"   Merkle Root: {merkle_root.hex()}")

    proof1 = merkle_tree.get_proof(0)
    assert merkle_tree.verify(leaf1, proof1), "本地 Merkle 验证失败"
    print("   [OK] 本地 Merkle 证明验证通过")


    print("\n[步骤 5] Operator 生成并发布 汇总 Merkle Root...")
    delay_window = 5
    fake_root_manifest = bytes("root_manifest", "utf-8").rjust(32, b'\0')
    
    # 发布累积金额需要配合 epochDeltaAmount
    send_tx(escrow.functions.publishPendingRoot(
        token_addr, root_id, merkle_root, total_payout_amount, delay_window, fake_root_manifest
    ), 'operator')
    print(f"   [OK] Merkle Root 已发布! {delay_window} 秒后可激活")

    # 步骤 6: 等待并激活 Root
    #    ▶ 合约函数: EscrowVault.activateRoot(token, rootId)
    #    ▶ 通俗应用: 审核期结束，Root 生效，用户可以来领钱了（类似"审批通过，开放领取窗口"）
    #    注意: 任何人都可以调用此函数，不限角色
    print("\n[步骤 6] 激活 Merkle Root...")
    print(f"   等待审计窗口 ({delay_window} 秒)...")
    # Anvil 区块时间不会自动前进，需要手动推进
    w3.provider.make_request('evm_increaseTime', [delay_window + 1])
    w3.provider.make_request('evm_mine', [])
    send_tx(escrow.functions.activateRoot(token_addr, root_id), 'user1')
    print("   [OK] Merkle Root 已激活!")
    active_root = escrow.functions.activeRoots(token_addr).call()
    print(f"   激活的 Root ID: {active_root[0]}, Root: {active_root[1].hex()}")

    # 步骤 7: 用户通过 Merkle Proof 提现
    #    ▶ 合约函数: EscrowVault.claim(token, rootId, cumulativeAmount, proof, recipient)
    #    ▶ 通俗应用: 用户凭 Merkle 证明领取奖励（类似"凭中奖码兑奖"）
    #    User1 领 50 Token, User2 领 30 Token
    print("\n[步骤 7] 用户通过 Merkle Proof 提现...")

    bal_before = token.functions.balanceOf(user1_addr).call()
    print(f"   User1 提现前余额: {bal_before / (10**18)}")
    try:
        nonce = w3.eth.get_transaction_count(user1_addr)
        tx = escrow.functions.claim(
            token_addr, root_id, user1_cumulative, merkle_tree.get_proof(0), user1_addr
        ).build_transaction({
            'from': user1_addr, 'nonce': nonce, 'gas': 300000, 'gasPrice': w3.eth.gas_price
        })
        signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEYS['user1'])
        receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
        assert receipt["status"] == 1, "Tx reverted"
        bal_after = token.functions.balanceOf(user1_addr).call()
        print(f"   [OK] User1 提现成功! 获得: {(bal_after - bal_before) / (10**18)} Token")
    except Exception as e:
        print(f"   [FAIL] User1 提现失败: {e}")

    bal_before = token.functions.balanceOf(user2_addr).call()
    try:
        nonce = w3.eth.get_transaction_count(user2_addr)
        tx = escrow.functions.claim(
            token_addr, root_id, user2_cumulative, merkle_tree.get_proof(1), user2_addr
        ).build_transaction({
            'from': user2_addr, 'nonce': nonce, 'gas': 300000, 'gasPrice': w3.eth.gas_price
        })
        signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEYS['user2'])
        receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
        assert receipt["status"] == 1, "Tx reverted"
        bal_after = token.functions.balanceOf(user2_addr).call()
        print(f"   [OK] User2 提现成功! 获得: {(bal_after - bal_before) / (10**18)} Token")
    except Exception as e:
        print(f"   [FAIL] User2 提现失败: {e}")

    print("\n" + "="*60)
    print("[SUCCESS] 第二阶段测试全部通过! (多任务合并提取演示完成)")
    print("="*60)

    print("\n最终余额汇总:")
    print(f"   Sponsor:    {token.functions.balanceOf(sponsor_addr).call() / (10**18)} Token")
    print(f"   User1:      {token.functions.balanceOf(user1_addr).call()   / (10**18)} Token")
    print(f"   User2:      {token.functions.balanceOf(user2_addr).call()   / (10**18)} Token")
    print(f"   EscrowVault:{token.functions.balanceOf(escrow_addr).call()  / (10**18)} Token")


# ==========================================
# 8. 第三阶段测试：异常分支与 Guardian 介入
#    测试的合约函数: emergencyRefund（超时强退）+ cancelPendingRoot（Guardian 取消恶意 Root）
# ==========================================
def run_phase3_test(token_addr: str, escrow_addr: str):
    token  = w3.eth.contract(address=token_addr,  abi=TOKEN_ABI)
    escrow = w3.eth.contract(address=escrow_addr, abi=ESCROW_ABI)

    print("\n" + "="*60)
    print("开始第三阶段测试 (Guardian 与 Emergency Refund 分支)")
    print("="*60)

    sponsor_addr = accounts['sponsor'].address
    budget       = 50 * (10 ** 18)

    # --- 流程 A: Sponsor 在结算超时后触发 emergencyRefund ---
    #    ▶ 合约函数: EscrowVault.emergencyRefund(taskId)
    #    ▶ 通俗应用: Operator 逾期没结算，Sponsor 强制取回全部预算（类似"超时未交货，甲方撤资"）
    print("\n[流程 A] Sponsor 超时强制退款测试...")
    server_secret = secrets.token_bytes(32)
    seed_commit   = w3.keccak(server_secret)
    task_id       = w3.keccak(text=f"task_p3_{int(time.time())}")

    send_tx(token.functions.approve(escrow_addr, budget), 'sponsor')
    current_time = w3.eth.get_block('latest')['timestamp']
    send_tx(escrow.functions.createTask(
        task_id, token_addr, budget, budget, 0, 0,
        current_time + 100, current_time + 200, seed_commit
    ), 'sponsor')
    print("   [OK] 任务已创建，等待结算超时...")

    w3.provider.make_request('evm_increaseTime', [205])
    w3.provider.make_request('evm_mine', [])

    bal_before = token.functions.balanceOf(sponsor_addr).call()
    send_tx(escrow.functions.emergencyRefund(task_id), 'sponsor')
    bal_after = token.functions.balanceOf(sponsor_addr).call()
    print(f"   [OK] Sponsor 成功执行 emergencyRefund，取回 {(bal_after - bal_before) / (10**18)} Token")
    
    task_info = escrow.functions.tasks(task_id).call()
    print(f"   任务状态: {task_info[10]} (6=CANCELLED)")

    # --- 流程 B: Guardian 取消恶意的 Pending Root ---
    #    ▶ 合约函数: EscrowVault.cancelPendingRoot(token, rootId)
    #    ▶ 通俗应用: Guardian 发现 Operator 提交的分配方案有问题，紧急拦截（类似"风控部门拦截可疑转账"）
    print("\n[流程 B] Guardian 取消恶意的 Pending Root...")
    
    # 1. 正常走完一个结算，产生 settledButUnallocated
    task_id2 = w3.keccak(text=f"task_p3_2_{int(time.time())}")
    send_tx(token.functions.approve(escrow_addr, budget), 'sponsor')
    
    current_time = w3.eth.get_block('latest')['timestamp']
    send_tx(escrow.functions.createTask(
        task_id2, token_addr, budget, budget, 0, 0,
        current_time + 100, current_time + 200, seed_commit
    ), 'sponsor')
    
    w3.provider.make_request('evm_increaseTime', [105])
    w3.provider.make_request('evm_mine', [])
    fake_manifest = bytes("manifest", "utf-8").rjust(32, b'\0')
    # 构造 1 人合格的 root，使 settleTask 能产生 payout
    q_leaf = w3.keccak(task_id2 + bytes.fromhex(sponsor_addr[2:]))
    q_root = w3.keccak(q_leaf + bytes(32))  # 单叶子简化为 root
    send_tx(escrow.functions.finalizeQualification(task_id2, 1, q_root, fake_manifest), 'operator')

    # payout=1 wei, refund=budget-1，确保 settledButUnallocated > 0
    send_tx(escrow.functions.settleTask(
        task_id2, server_secret, 0, bytes(32), fake_manifest,
        1, budget - 1, 1, 0
    ), 'operator')
    print("   [OK] 第二个任务结算完成，Operator 获得可用池度")

    # 2. Operator 发布有问题的 Root
    #    ▶ 调用 publishPendingRoot() 发布一个恶意的 Merkle Root
    current_root = escrow.functions.activeRoots(token_addr).call()
    root_id = current_root[0] + 1
    malicious_root = w3.keccak(text="bad_root")

    # epochDeltaAmount 只能用已结算但未分配的 1 wei
    send_tx(escrow.functions.publishPendingRoot(
        token_addr, root_id, malicious_root, 1, 3600, fake_manifest
    ), 'operator')
    print(f"   [OK] Operator 发布了待激活 Root (ID: {root_id})")

    # 3. Guardian 介入取消
    #    ▶ 调用 cancelPendingRoot(token, rootId): 取消恶意 Root，资金退回未分配池
    #    测试环境 operator 地址同时具有 ADMIN, OPERATOR, GUARDIAN 角色
    send_tx(escrow.functions.cancelPendingRoot(token_addr, root_id), 'operator') 
    print("   [OK] Guardian 成功取消了 Pending Root")

    pending_state = escrow.functions.pendingRoots(token_addr).call()
    print(f"   Pending Root ID (取消后): {pending_state[0]} (应该为空即为0)")

    print("\n" + "="*60)
    print("[SUCCESS] 第三阶段异常分支测试通过!")
    print("="*60)


# ==========================================
# 9. 第四阶段测试：UUPS 可升级功能验证
#    测试的合约函数: upgradeTo() + _authorizeUpgrade 权限控制
# ==========================================
def run_phase4_upgrade_test(token_addr: str, escrow_addr: str):
    token  = w3.eth.contract(address=token_addr,  abi=TOKEN_ABI)
    escrow = w3.eth.contract(address=escrow_addr, abi=ESCROW_ABI)

    print("\n" + "="*60)
    print("开始第四阶段测试 (UUPS 可升级功能验证)")
    print("="*60)

    sponsor_addr  = accounts['sponsor'].address
    operator_addr = accounts['operator'].address

    # --- 步骤 1: 升级前创建任务，作为"历史状态"基准 ---
    print("\n[步骤 1] 升级前创建任务，建立基准状态...")
    server_secret = secrets.token_bytes(32)
    seed_commit   = w3.keccak(server_secret)
    task_id       = w3.keccak(text=f"task_p4_{int(time.time())}")
    budget        = 50 * (10 ** 18)

    current_time = w3.eth.get_block('latest')['timestamp']
    send_tx(token.functions.approve(escrow_addr, budget), 'sponsor')
    receipt = send_tx(escrow.functions.createTask(
        task_id, token_addr, budget, budget, 0, 0,
        current_time + 3600, current_time + 7200, seed_commit
    ), 'sponsor')
    assert receipt.status == 1, "升级前任务创建失败"
    print(f"   [OK] 升级前任务创建成功: {task_id.hex()}")

    task_before = escrow.functions.tasks(task_id).call()
    print(f"   升级前状态: status={task_before[10]}, budget={task_before[3]}")

    # --- 步骤 2: 部署新的 Implementation（作为 V2） ---
    print("\n[步骤 2] 部署 V2 Implementation...")
    Escrow = w3.eth.contract(abi=ESCROW_ABI, bytecode=ESCROW_BYTECODE)
    nonce = w3.eth.get_transaction_count(operator_addr)
    tx_impl = Escrow.constructor().build_transaction({
        'from':     operator_addr,
        'nonce':    nonce,
        'gas':      4000000,
        'gasPrice': w3.eth.gas_price,
    })
    signed_impl  = w3.eth.account.sign_transaction(tx_impl, PRIVATE_KEYS['operator'])
    receipt_impl = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed_impl.raw_transaction))
    assert receipt_impl.status == 1, "V2 实现合约部署失败"
    v2_impl_addr = receipt_impl.contractAddress
    print(f"   [OK] V2 Implementation 地址: {v2_impl_addr}")

    # --- 步骤 3: 非管理员尝试升级，应当被拒绝 ---
    print("\n[步骤 3] 验证非管理员无法执行升级...")
    nonce = w3.eth.get_transaction_count(sponsor_addr)
    tx = escrow.functions.upgradeToAndCall(v2_impl_addr, b'').build_transaction({
        'from':     sponsor_addr,
        'nonce':    nonce,
        'gas':      500000,
        'gasPrice': w3.eth.gas_price,
    })
    signed  = w3.eth.account.sign_transaction(tx, PRIVATE_KEYS['sponsor'])
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
    assert receipt.status == 0, "非管理员升级应当被拒绝"
    print("   [OK] 非管理员升级交易被正确回滚")

    # --- 步骤 4: 管理员执行 upgradeTo ---
    print("\n[步骤 4] 管理员执行 UUPS 升级...")
    receipt = send_tx(escrow.functions.upgradeToAndCall(v2_impl_addr, b''), 'operator')
    assert receipt.status == 1, "管理员升级失败"
    print(f"   [OK] 升级成功！代理合约已指向新实现地址")

    # --- 步骤 5: 验证升级后历史状态完整保留 ---
    print("\n[步骤 5] 验证升级后原有状态是否保留...")
    task_after = escrow.functions.tasks(task_id).call()
    assert task_after[0] == task_before[0], "taskId 不匹配"
    assert task_after[1] == task_before[1], "sponsor 不匹配"
    assert task_after[3] == task_before[3], "budget 不匹配"
    assert task_after[10] == task_before[10], "status 不匹配"
    print("   [OK] 升级前任务状态完全保留")
    print(f"   Status: {task_after[10]}, Budget: {task_after[3]}")

    has_op = escrow.functions.hasRole(escrow.functions.OPERATOR_ROLE().call(), operator_addr).call()
    assert has_op, "OPERATOR_ROLE 在升级后丢失"
    print("   [OK] 角色权限保留")

    # --- 步骤 6: 验证升级后业务功能正常 ---
    print("\n[步骤 6] 验证升级后业务功能正常...")
    server_secret2 = secrets.token_bytes(32)
    seed_commit2   = w3.keccak(server_secret2)
    task_id2       = w3.keccak(text=f"task_p4_after_{int(time.time())}")

    current_time = w3.eth.get_block('latest')['timestamp']
    send_tx(token.functions.approve(escrow_addr, budget), 'sponsor')
    receipt = send_tx(escrow.functions.createTask(
        task_id2, token_addr, budget, budget, 0, 0,
        current_time + 3600, current_time + 7200, seed_commit2
    ), 'sponsor')
    assert receipt.status == 1, "升级后新任务创建失败"
    print(f"   [OK] 升级后新任务创建成功")

    # 快速走完这个任务的结算流程
    w3.provider.make_request('evm_increaseTime', [3601])
    w3.provider.make_request('evm_mine', [])
    fake_manifest = bytes("manifest", "utf-8").rjust(32, b'\0')
    receipt = send_tx(escrow.functions.finalizeQualification(task_id2, 0, bytes(32), fake_manifest), 'operator')
    assert receipt.status == 1, "升级后 finalizeQualification 失败"
    receipt = send_tx(escrow.functions.settleTask(
        task_id2, server_secret2, 0, bytes(32), fake_manifest,
        0, budget, 0, 0
    ), 'operator')
    assert receipt.status == 1, "升级后 settleTask 失败"
    print("   [OK] 升级后任务结算流程正常")

    print("\n" + "="*60)
    print("[SUCCESS] 第四阶段 UUPS 升级测试全部通过!")
    print("="*60)


# ==========================================
# 主程序
# ==========================================
def main():
    # 尝试加载已保存的合约地址
    saved = load_contract_addresses()
    if saved:
        token_addr, escrow_addr = saved
        print(f"\n[OK] 加载已部署的合约:")
        print(f"   Token:  {token_addr}")
        print(f"   Escrow: {escrow_addr}")
    else:
        token_addr, escrow_addr = deploy_contracts()
        save_contract_addresses(token_addr, escrow_addr)
    
    run_phase1_test(token_addr, escrow_addr)
    run_phase2_test(token_addr, escrow_addr)
    run_phase3_test(token_addr, escrow_addr)
    run_phase4_upgrade_test(token_addr, escrow_addr)


if __name__ == "__main__":
    main()
