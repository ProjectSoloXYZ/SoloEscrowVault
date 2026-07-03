"""
SOL-06 链下 Guardian 复算脚本
=================================
用途：Guardian 在审查窗口内独立复算某个 token 的待激活 Merkle Root，
     与链上 pendingRoots[token].merkleRoot 比对；不一致则告警，
     Guardian 应在 activateAfter 之前调用 cancelPendingRoot 挑战。

对应整改：
- README 复核清单第 3 条「SOL-06 … Guardian 复算脚本可用」
- CertiK_Audit_Response.md「整改更新」SOL-06：链下确定性可复算抽奖 + Guardian 窗口挑战

链下公开算法（与后端 root 生成器必须完全一致，任何人可独立复现）：
1. 最终熵      finalEntropy = keccak256(seedReveal ‖ blockhash(taskEntropyBlock))
               （对齐合约 settleTask：keccak256(abi.encode(seedReveal, bh))，
                 两个 bytes32 的 abi.encode 即 64 字节直接拼接）
2. 中奖人数    winnerCount = min(qualifiedCount, lotteryWinnerCount)   —— 合约确定性算出
3. 中奖名单    对每个合格地址计算打分：
                 qualifiedLeaf = keccak256(taskId ‖ address[20B])
                 score         = int(keccak256(finalEntropy ‖ qualifiedLeaf))
               按 score 升序排序，取前 winnerCount 名为中奖者（并列以地址字典序打破）
4. 金额分配    每个合格者得 baseRewardPerQualified；中奖者额外得 lotteryRewardPerWinner
5. 累计 root   把每个用户跨任务/历史的应得额累加成 cumulativeAmount，
               leaf = keccak256(keccak256(abi.encode(user, token, rootId, cumulativeAmount)))
               commutative-keccak256 两两合并成 root（对齐 OZ MerkleProof + 合约 claim）

运行：
    # 离线自测（验证算法与 Merkle 实现自洽，无需连链）
    python scripts/guardian_recompute.py --self-test

    # 连链复算并与链上 pending root 比对
    export RPC_URL="http://127.0.0.1:8545"
    export ESCROW_PROXY_ADDRESS="0x..."
    python scripts/guardian_recompute.py --manifest path/to/manifest.json

manifest.json 结构（Guardian 从后端 / PendingRootPublished 事件旁的清单获取）：
{
  "token": "0x...",
  "rootId": 3,
  "priorCumulative": { "0xUser": "已在旧 root 累计的应得额(wei，字符串)" },
  "tasks": [
    {
      "taskId": "0x...(32B hex)",
      "lotteryWinnerCount": 2,
      "lotteryRewardPerWinner": "1000000000000000000",
      "baseRewardPerQualified": "2000000000000000000",
      "qualified": ["0xAddr1", "0xAddr2", "0xAddr3"]
    }
  ]
}
"""

import argparse
import json
import os
import sys

from eth_utils import keccak, to_checksum_address


# ==========================================
# 纯算法层（不依赖链，可被单测直接调用）
# ==========================================

def _addr_bytes(addr: str) -> bytes:
    """0x 前缀地址 → 20 字节。"""
    return bytes.fromhex(addr[2:].lower())


def _hex_bytes32(h: str) -> bytes:
    """0x 前缀的 32 字节 hex → bytes32。"""
    b = bytes.fromhex(h[2:] if h.startswith(("0x", "0X")) else h)
    if len(b) != 32:
        raise ValueError(f"expect 32 bytes, got {len(b)}: {h}")
    return b


def derive_entropy(seed_reveal: bytes, block_hash: bytes) -> bytes:
    """
    复算合约在 settleTask 中派生的最终熵：
        finalEntropy = keccak256(abi.encode(seedReveal, blockhash))
    seedReveal、blockhash 均为 bytes32，abi.encode 后即 64 字节拼接。
    """
    assert len(seed_reveal) == 32 and len(block_hash) == 32
    return keccak(seed_reveal + block_hash)


def select_winners(task_id: bytes, entropy: bytes, qualified: list, winner_count: int) -> list:
    """
    确定性选出中奖地址列表（升序打分，取前 winner_count 名）。
    返回 checksum 地址列表。winner_count 应由调用方按 min(qualified, lotteryWinnerCount) 传入。
    """
    if winner_count <= 0 or not qualified:
        return []
    scored = []
    for addr in qualified:
        q_leaf = keccak(task_id + _addr_bytes(addr))
        score = int.from_bytes(keccak(entropy + q_leaf), "big")
        # 并列时用地址字典序打破，保证全网复算结果一致
        scored.append((score, addr.lower(), to_checksum_address(addr)))
    scored.sort(key=lambda x: (x[0], x[1]))
    n = min(winner_count, len(scored))
    return [x[2] for x in scored[:n]]


def compute_task_allocations(task: dict, entropy: bytes) -> dict:
    """
    复算单个 task 每个合格地址的应得额（基础奖励 + 若中奖则加抽奖奖励）。
    返回 { checksum_addr: amount_wei }。
    """
    task_id = _hex_bytes32(task["taskId"])
    qualified = task["qualified"]
    base = int(task["baseRewardPerQualified"])
    lottery = int(task["lotteryRewardPerWinner"])
    lottery_winner_count = int(task["lotteryWinnerCount"])

    # 合约确定性中奖人数
    winner_count = min(len(qualified), lottery_winner_count)
    winners = set(select_winners(task_id, entropy, qualified, winner_count))

    out = {}
    for addr in qualified:
        c = to_checksum_address(addr)
        out[c] = base + (lottery if c in winners else 0)
    return out, winners, winner_count


def encode_leaf(user: str, token: str, root_id: int, cumulative_amount: int) -> bytes:
    """
    对齐合约 claim 的 leaf：
        keccak256(keccak256(abi.encode(address, address, uint64, uint128)))
    """
    inner = (
        _addr_bytes(user).rjust(32, b"\x00")
        + _addr_bytes(token).rjust(32, b"\x00")
        + int(root_id).to_bytes(32, "big")
        + int(cumulative_amount).to_bytes(32, "big")
    )
    return keccak(keccak(inner))


def _commutative_keccak(a: bytes, b: bytes) -> bytes:
    return keccak(a + b) if a < b else keccak(b + a)


class MerkleTree:
    """OZ 兼容 commutative Merkle 树（与合约 MerkleProof.verify 一致）。"""

    def __init__(self, leaves: list):
        self.leaves = leaves
        self.levels = self._build(leaves)
        self.root = self.levels[-1][0] if self.levels else bytes(32)

    @staticmethod
    def _build(leaves: list) -> list:
        if not leaves:
            return []
        levels = [leaves[:]]
        while len(levels[-1]) > 1:
            cur = levels[-1]
            nxt = []
            for i in range(0, len(cur), 2):
                left = cur[i]
                right = cur[i + 1] if i + 1 < len(cur) else cur[i]
                nxt.append(_commutative_keccak(left, right))
            levels.append(nxt)
        return levels

    def get_proof(self, index: int) -> list:
        proof = []
        idx = index
        for level in self.levels[:-1]:
            sib = idx + 1 if idx % 2 == 0 else idx - 1
            proof.append(level[sib] if sib < len(level) else level[idx])
            idx //= 2
        return proof

    def verify(self, leaf: bytes, proof: list) -> bool:
        computed = leaf
        for sib in proof:
            computed = _commutative_keccak(computed, sib)
        return computed == self.root


def build_root_from_cumulative(token: str, root_id: int, cumulative: dict):
    """
    由 { addr: cumulativeAmount } 构建 Merkle 树。
    地址按字典序排序，保证全网复算得到同一棵树/同一 root。
    返回 (MerkleTree, ordered_leaves, ordered_addrs)。
    """
    addrs = sorted(cumulative.keys(), key=lambda a: a.lower())
    leaves = [encode_leaf(a, token, root_id, cumulative[a]) for a in addrs]
    return MerkleTree(leaves), leaves, addrs


def recompute_root(manifest: dict, entropy_by_task: dict):
    """
    复算整份 manifest 的累计 root。
    entropy_by_task: { taskId(小写hex): finalEntropy(bytes32) }。
    返回 (root_bytes, cumulative_dict, per_task_detail)。
    """
    token = to_checksum_address(manifest["token"])
    root_id = int(manifest["rootId"])

    # 历史累计（跨旧 root）
    cumulative = {}
    for addr, amt in manifest.get("priorCumulative", {}).items():
        cumulative[to_checksum_address(addr)] = int(amt)

    detail = []
    for task in manifest["tasks"]:
        tid_key = task["taskId"].lower()
        if tid_key.startswith("0x"):
            tid_key = tid_key[2:]
        entropy = entropy_by_task[tid_key]
        allocs, winners, winner_count = compute_task_allocations(task, entropy)
        for addr, amt in allocs.items():
            cumulative[addr] = cumulative.get(addr, 0) + amt
        detail.append({
            "taskId": task["taskId"],
            "winnerCount": winner_count,
            "winners": sorted(winners),
            "payout": sum(allocs.values()),
        })

    tree, _, _ = build_root_from_cumulative(token, root_id, cumulative)
    return tree.root, cumulative, detail


# ==========================================
# 离线自测：验证算法与 Merkle 实现自洽
# ==========================================

def run_self_test() -> int:
    print("=" * 60)
    print("SOL-06 Guardian 复算脚本 — 离线自测")
    print("=" * 60)

    token = to_checksum_address("0x" + "11" * 20)
    root_id = 3
    seed = b"\x01" * 32
    bh = b"\x02" * 32

    # 1) 熵派生：确定性 + 与手工哈希一致
    e1 = derive_entropy(seed, bh)
    e2 = derive_entropy(seed, bh)
    assert e1 == e2, "熵派生不确定"
    assert e1 == keccak(seed + bh), "熵派生与规范公式不符"
    print(f"  [OK] 熵派生确定且符合规范: {e1.hex()[:16]}...")

    users = [to_checksum_address("0x" + f"{i:02x}" * 20) for i in range(0xa0, 0xa6)]  # 6 个合格地址
    task = {
        "taskId": "0x" + "ab" * 32,
        "lotteryWinnerCount": 2,
        "lotteryRewardPerWinner": str(5 * 10**18),
        "baseRewardPerQualified": str(1 * 10**18),
        "qualified": users,
    }
    tid = _hex_bytes32(task["taskId"])

    # 2) 中奖选取：确定性 + 人数 = min(qualified, winnerCount)
    w1 = select_winners(tid, e1, users, 2)
    w2 = select_winners(tid, e1, users, 2)
    assert w1 == w2, "中奖选取不确定"
    assert len(w1) == 2, f"中奖人数应为 2，实际 {len(w1)}"
    assert min(6, 2) == 2
    # 合格者少于名额时，全员中奖
    w_all = select_winners(tid, e1, users[:1], 2)
    assert len(w_all) == 1, "合格者不足时应全员中奖"
    print(f"  [OK] 中奖确定性、人数=min(qualified,winnerCount): winners={[a[:8] for a in w1]}")

    # 3) 金额分配守恒
    allocs, winners, wc = compute_task_allocations(task, e1)
    base = 1 * 10**18
    lottery = 5 * 10**18
    expected_payout = base * len(users) + lottery * wc
    assert sum(allocs.values()) == expected_payout, "金额分配与预期不符"
    for a in winners:
        assert allocs[a] == base + lottery, "中奖者金额应为 base+lottery"
    print(f"  [OK] 金额守恒: payout={expected_payout/10**18} = base*{len(users)} + lottery*{wc}")

    # 4) 累计 root：跨任务累加 + 历史 prior
    manifest = {
        "token": token,
        "rootId": root_id,
        "priorCumulative": {users[0]: str(3 * 10**18)},
        "tasks": [task, {
            "taskId": "0x" + "cd" * 32,
            "lotteryWinnerCount": 1,
            "lotteryRewardPerWinner": str(2 * 10**18),
            "baseRewardPerQualified": str(1 * 10**18),
            "qualified": users[:3],
        }],
    }
    entropy_by_task = {
        "ab" * 32: e1,
        "cd" * 32: derive_entropy(seed, b"\x03" * 32),
    }
    root, cumulative, detail = recompute_root(manifest, entropy_by_task)
    assert root == recompute_root(manifest, entropy_by_task)[0], "root 复算不确定"
    print(f"  [OK] 累计 root 确定: {root.hex()[:16]}...  (涉及 {len(cumulative)} 个地址)")

    # 5) 每个 leaf 的 proof 能通过 verify（对齐合约 claim 验证）
    tree, leaves, addrs = build_root_from_cumulative(token, root_id, cumulative)
    assert tree.root == root
    for i, addr in enumerate(addrs):
        proof = tree.get_proof(i)
        leaf = encode_leaf(addr, token, root_id, cumulative[addr])
        assert tree.verify(leaf, proof), f"{addr} 的 proof 验证失败"
    print(f"  [OK] 全部 {len(addrs)} 个 leaf 的 Merkle proof 验证通过")

    # 6) 篡改检测：改一分钱累计额，root 必变
    tampered = dict(cumulative)
    victim = addrs[0]
    tampered[victim] += 1
    bad_tree, _, _ = build_root_from_cumulative(token, root_id, tampered)
    assert bad_tree.root != root, "篡改累计额后 root 应发生变化"
    print("  [OK] 篡改检测: 任一累计额被改动都会导致 root 变化")

    print("=" * 60)
    print("[SUCCESS] 自测全部通过 — Guardian 复算脚本算法自洽、可用")
    print("=" * 60)
    return 0


# ==========================================
# 连链复算：与链上 pending root 比对
# ==========================================

def run_on_chain(manifest_path: str) -> int:
    from web3 import Web3

    # 本地 RPC 不走代理
    _no_proxy = os.environ.get("NO_PROXY", "")
    for host in ("127.0.0.1", "localhost"):
        if host not in _no_proxy:
            _no_proxy = host if not _no_proxy else f"{_no_proxy},{host}"
    os.environ["NO_PROXY"] = _no_proxy
    os.environ["no_proxy"] = _no_proxy

    rpc = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
    escrow_addr = os.environ.get("ESCROW_PROXY_ADDRESS", "")
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    escrow_json = os.path.join(project_root, "out", "EscrowVault.sol", "EscrowVault.json")

    if not escrow_addr:
        print("[FAIL] 未设置 ESCROW_PROXY_ADDRESS")
        return 1
    if not os.path.exists(escrow_json):
        print(f"[FAIL] 缺少编译产物 {escrow_json}，请先 forge build")
        return 1

    with open(manifest_path) as f:
        manifest = json.load(f)
    with open(escrow_json) as f:
        abi = json.load(f)["abi"]

    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        print(f"[FAIL] 无法连接 RPC: {rpc}")
        return 1
    escrow = w3.eth.contract(address=Web3.to_checksum_address(escrow_addr), abi=abi)
    token = Web3.to_checksum_address(manifest["token"])

    # 逐 task 从链上读结算数据，复算熵并交叉校验
    entropy_by_task = {}
    mismatches = []
    for task in manifest["tasks"]:
        tid = task["taskId"]
        tid_bytes = _hex_bytes32(tid)
        s = escrow.functions.settlements(tid_bytes).call()
        # Settlement: seedReveal, entropyRef, entropyValue, resultManifestHash,
        #             baseRewardPerQualified, actualWinnerCount, ...
        seed_reveal, entropy_ref, entropy_value = s[0], s[1], s[2]
        chain_base, chain_winner_count = s[4], s[5]

        block = w3.eth.get_block(int(entropy_ref))
        bh = bytes(block["hash"])
        local_entropy = derive_entropy(bytes(seed_reveal), bh)

        tid_key = tid[2:].lower() if tid.startswith("0x") else tid.lower()
        entropy_by_task[tid_key] = local_entropy

        # 交叉校验：本地派生熵 == 链上记录熵；本地中奖人数 == 链上 actualWinnerCount
        if local_entropy != bytes(entropy_value):
            mismatches.append(f"task {tid}: 熵不一致（链上被篡改？）")
        exp_wc = min(len(task["qualified"]), int(task["lotteryWinnerCount"]))
        if exp_wc != chain_winner_count:
            mismatches.append(
                f"task {tid}: 中奖人数不一致 本地={exp_wc} 链上={chain_winner_count}")
        if int(task["baseRewardPerQualified"]) != chain_base:
            mismatches.append(
                f"task {tid}: baseReward 不一致 本地={task['baseRewardPerQualified']} 链上={chain_base}")

    local_root, cumulative, detail = recompute_root(manifest, entropy_by_task)

    # 读链上待激活 root（优先）与已激活 root
    pending = escrow.functions.pendingRoots(token).call()
    active = escrow.functions.activeRoots(token).call()
    pending_root = bytes(pending[2])  # PendingRoot.merkleRoot
    active_root = bytes(active[1])    # ActiveRoot.merkleRoot

    print("=" * 60)
    print("SOL-06 Guardian 复算 — 连链比对")
    print("=" * 60)
    print(f"  token        : {token}")
    print(f"  rootId       : {manifest['rootId']}")
    print(f"  本地复算 root: 0x{local_root.hex()}")
    print(f"  链上 pending : 0x{pending_root.hex()}")
    print(f"  链上 active  : 0x{active_root.hex()}")
    for d in detail:
        print(f"    - task {d['taskId'][:12]}… winners={d['winnerCount']} payout={d['payout']}")

    ok = True
    for m in mismatches:
        print(f"  [ALERT] {m}")
        ok = False

    target = "pending" if pending_root != bytes(32) else "active"
    chain_root = pending_root if target == "pending" else active_root
    if local_root == chain_root:
        print(f"  [OK] 本地复算 root 与链上 {target} root 一致")
    else:
        ok = False
        print(f"  [ALERT] 本地复算 root 与链上 {target} root 不一致！")
        if target == "pending":
            print("         → Guardian 应在 activateAfter 之前调用 "
                  "cancelPendingRoot(token, rootId) 挑战该 root")

    print("=" * 60)
    if ok:
        print("[SUCCESS] 复算通过，未发现异常")
        return 0
    print("[ALERT] 复算发现异常，请 Guardian 立即处置")
    return 2


def main():
    parser = argparse.ArgumentParser(description="SOL-06 Guardian 链下复算脚本")
    parser.add_argument("--self-test", action="store_true", help="离线自测，不连链")
    parser.add_argument("--manifest", help="待复算的 root manifest JSON 路径")
    args = parser.parse_args()

    if args.self_test:
        sys.exit(run_self_test())
    if args.manifest:
        sys.exit(run_on_chain(args.manifest))
    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
