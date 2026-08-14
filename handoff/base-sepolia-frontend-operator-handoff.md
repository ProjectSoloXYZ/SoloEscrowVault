# EscrowVault Base Sepolia Handoff Runbook

最后更新：2026-07-09

## 0. 结论

当前合约已经重新部署到 Base Sepolia，业务入口是 EscrowVault Proxy：

```text
0x86493b48CBEbd7B25BD89de585FFbEC439A4e453
```

接手方必须使用当前仓库里的新版 ABI：

```text
abi/EscrowVault.abi.json
```

核心判断：

| 模块 | 结论 | 接手动作 |
| --- | --- | --- |
| Sponsor 创建任务 | 函数签名兼容，但校验更严格 | 替换 ABI/地址，并在前端加 1 小时结算窗口和平台费预算校验 |
| 用户领取奖励 | 函数签名和 Merkle leaf 格式兼容 | 替换 ABI/地址，继续使用累计金额模型 |
| Operator 结算 | 不兼容旧代码 | 必须改 `settleTask` 参数、结算时机和金额公式 |
| 事件监听/indexer | 不兼容旧 ABI | 必须用新版 ABI 重新解码 `TaskCreated`、`TaskSettled` |
| Admin 管理 | 权限路径变化 | 通过 Admin Safe + Timelock 执行，不能直接调 Vault |
| Guardian 应急 | 直接可用 | Guardian Safe 直接调用 `pause()` / `cancelPendingRoot()` |

接手前有一个待处理状态：当前 USDC 已存在测试 pending root，`rootId=1`。在处理它之前，同一 token 不能发布新的 pending root。

处理方式二选一：

- 继续 smoke test：到 `2026-07-10 15:18:40 CST` 后调用 `activateRoot(USDC, 1)`。
- 放弃 smoke test：由 Guardian Safe 调用 `cancelPendingRoot(USDC, 1)`。

## 1. Source Of Truth

| 项目 | 值 |
| --- | --- |
| 网络 | Base Sepolia |
| Chain ID | `84532` |
| EscrowVault Proxy | `0x86493b48CBEbd7B25BD89de585FFbEC439A4e453` |
| EscrowVault Implementation | `0xace943dB8abDCdf79B27c2715067CCfB4E59b8d0` |
| TimelockController | `0x89946bb9D13b8BDF24C9047C7c655AC4acA58c49` |
| Admin Safe | `0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182` |
| Guardian Safe | `0x9DbB8F6cD251D508015D93D14E08a4CEAac84C40` |
| Operator 当前地址 | `0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776` |
| Base Sepolia USDC | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| Vault ABI | `abi/EscrowVault.abi.json` |
| Timelock ABI | `abi/TimelockController.abi.json` |
| 部署输出 | `deployments/path_b_base_sepolia.json` |

当前链上配置快照：

| 配置项 | 当前值 |
| --- | --- |
| `tokenWhitelist(USDC)` | `true` |
| `platformFeeBps` | `200`，即 2% |
| `platformTreasury` | `0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182` |
| `minReviewWindow` | `86400` 秒，即 24 小时 |
| `paused()` | `false` |
| `settledButUnallocated(USDC)` | `0` |
| `activeRoots(USDC)` | `rootId=0`，尚无 active root |
| `pendingRoots(USDC)` | `rootId=1`，`epochDeltaAmount=1000000`，`activateAfter=2026-07-10 15:18:40 CST` |

测试 pending root：

```text
0xf43c43f2b6699b51e968f3580773e7efd6597fc44ad1cae72c377afbbaca6d1f
```

## 2. Ownership And Permissions

| 角色 | 链上持有者 | 职责 | 日常调用路径 |
| --- | --- | --- | --- |
| `DEFAULT_ADMIN_ROLE` | TimelockController | 白名单、平台费、treasury、升级、角色轮换、unpause | Admin Safe schedule -> wait -> execute |
| Timelock `PROPOSER/EXECUTOR/CANCELLER` | Admin Safe | 排队、执行、取消治理动作 | Safe Transaction Builder |
| `OPERATOR_ROLE` | Operator EOA `0x7331...8776` | 资格确认、结算、发布 pending root | 直接调用 Vault |
| `GUARDIAN_ROLE` | Guardian Safe `0x9DbB...C40` | 紧急暂停、取消可疑 pending root | 直接调用 Vault |

关键边界：

- Admin Safe 不直接持有 Vault 的 `DEFAULT_ADMIN_ROLE`。Vault admin 是 Timelock，Admin Safe 控制 Timelock。
- Admin 动作当前需要先 `schedule`，等待 `600` 秒 Timelock delay，再 `execute`。
- Operator 高频业务动作不经过 Timelock。
- Guardian 的 `pause()` 和 `cancelPendingRoot()` 不经过 Timelock，用于快速止损。
- Operator 私钥只能放在后端密钥管理系统或安全运行环境中，不能进入前端、移动端、Git 仓库、日志或错误上报系统。

## 3. Compatibility Matrix

本节用于判断旧代码是否能继续使用。这里的“旧版”指交接前 GitHub 仓库 `Winston-9527/SoloEscrowVault` 中的 EscrowVault 合约；当前部署是审计整改后的本地版本。

### 3.1 基本兼容

这些函数的函数名和参数类型保持兼容。接手时仍应替换为当前 ABI 和当前 Proxy 地址。

```solidity
createTask(bytes32,address,uint96,uint96,uint96,uint16,uint64,uint64,bytes32)
finalizeQualification(bytes32,uint32,bytes32,bytes32)
claim(address,uint64,uint128,bytes32[],address)
claimRefund(bytes32,address)
cancelTask(bytes32)
emergencyRefund(bytes32)
publishPendingRoot(address,uint64,bytes32,uint128,uint64,bytes32)
activateRoot(address,uint64)
cancelPendingRoot(address,uint64)
```

业务模型保持不变：

- Sponsor 仍然先 `approve`，再调用 `createTask`。
- 用户领取仍然使用累计金额模型。
- claim 的 leaf 格式保持不变。
- Operator 仍负责资格冻结、任务结算、发布 pending root。
- Guardian 仍负责暂停和取消 pending root。

claim leaf 必须严格使用：

```solidity
keccak256(bytes.concat(
  keccak256(abi.encode(account, token, rootId, cumulativeAmount))
))
```

### 3.2 必须适配

| 变化 | 旧版 | 当前版本 | 影响 |
| --- | --- | --- | --- |
| `settleTask` 签名 | `settleTask(bytes32,bytes32,uint64,bytes32,bytes32,uint96,uint96,uint96,uint16)` | `settleTask(bytes32,bytes32,bytes32,uint96,uint96,uint96)` | 旧 Operator 结算代码会直接失败 |
| 熵来源 | Operator 传 `entropyRef` / `entropyValue` | 合约用 `seedReveal + blockhash(taskEntropyBlock)` 派生 | 后端只传 `seedReveal` 和金额结果 |
| 中奖人数 | Operator 传 `actualWinnerCount` | 合约按 `min(qualifiedCount, lotteryWinnerCount)` 计算 | 后端 manifest 必须与合约规则一致 |
| `TaskCreated` 事件 | 无 `platformFeeBps` | 新增 `platformFeeBps` | indexer 必须换 ABI |
| `TaskSettled` 事件 | 无 `platformFeeAmount` | 新增 `platformFeeAmount` | indexer 必须换 ABI |
| `tasks(taskId)` | 11 个返回值 | 12 个返回值，新增 `platformFeeBps` | 手写 tuple/数组下标要改 |
| `settlements(taskId)` | 9 个返回值 | 10 个返回值，新增 `platformFeeAmount` | 手写 tuple/数组下标要改 |
| `createTask` 预算 | 不计平台费 | 必须预留平台费 | 前端必须提前校验 |
| `publishPendingRoot.delayWindow` | 可传短窗口 | 默认至少 24 小时 | Operator 发布 root 不能传 `0` 或 `3600` |

当前 `settleTask` 调用格式：

```solidity
settleTask(
  bytes32 taskId,
  bytes32 seedReveal,
  bytes32 resultManifestHash,
  uint96 payoutAmount,
  uint96 refundableAmount,
  uint96 baseRewardPerQualified
)
```

当前预算约束：

```text
basePool + lotteryRewardPerWinner * lotteryWinnerCount + totalBudget * platformFeeBps / 10000 <= totalBudget
```

当前时间约束：

```text
settlementDeadline >= qualifyDeadline + 3600
finalizeQualification 时距离 settlementDeadline 至少还有 3600 秒
settleTask 必须在 taskEntropyBlock 后且 256 块内完成
```

Base Sepolia 当前约 2 秒/块，256 块约 8 分 32 秒。结算服务必须自动化执行，不应依赖人工在页面上手动点结算。

## 4. Frontend Integration SOP

### 4.1 Public Config

前端只使用公开配置，不读取部署 `.env`。

```env
NEXT_PUBLIC_CHAIN_ID=84532
NEXT_PUBLIC_ESCROW_VAULT=0x86493b48CBEbd7B25BD89de585FFbEC439A4e453
NEXT_PUBLIC_USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e
NEXT_PUBLIC_ADMIN_SAFE=0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182
NEXT_PUBLIC_GUARDIAN_SAFE=0x9DbB8F6cD251D508015D93D14E08a4CEAac84C40
```

### 4.2 Sponsor 创建任务

Sponsor 创建任务分两笔交易：

1. `USDC.approve(EscrowVaultProxy, totalBudget)`
2. `EscrowVault.createTask(...)`

```solidity
createTask(
  bytes32 taskId,
  address token,
  uint96 totalBudget,
  uint96 basePool,
  uint96 lotteryRewardPerWinner,
  uint16 lotteryWinnerCount,
  uint64 qualifyDeadline,
  uint64 settlementDeadline,
  bytes32 seedCommit
)
```

前端和后端必须在发交易前校验：

- `token == USDC`，生产只使用标准 USDC。
- USDC 是 6 decimals，金额使用最小单位，例如 `1 USDC = 1000000`。
- `taskId` 唯一，建议由后端生成并持久化，例如 `keccak256(campaignId, sponsor, nonce)`。
- `seedReveal` 由后端生成并安全保存到结算阶段。
- `seedCommit = keccak256(abi.encodePacked(seedReveal))`。
- `qualifyDeadline > now`。
- `settlementDeadline >= qualifyDeadline + 3600`。
- `basePool + lotteryRewardPerWinner * lotteryWinnerCount + totalBudget * platformFeeBps / 10000 <= totalBudget`。

当前平台费率：

```text
platformFeeBps = 200
```

合约会检查 ERC20 实收金额必须等于 `totalBudget`，不支持转账税、通缩或 rebasing token。

### 4.3 用户领取奖励

```solidity
claim(
  address token,
  uint64 rootId,
  uint128 cumulativeAmount,
  bytes32[] merkleProof,
  address recipient
)
```

领取规则：

- `msg.sender` 必须是 leaf 中的 `account`。
- `recipient` 可以不同于 `msg.sender`。
- 用户本次到账金额是 `cumulativeAmount - claimed[user][token]`。
- 前端展示可领取金额前，应先调用 `claimableDelta(account, token, cumulativeAmount)`。

单用户 smoke test root 生成脚本：

```bash
CLAIM_USER=0x... TOKEN=0x036CbD53842c5426634e7929541eC2318f3dCF7e ROOT_ID=1 AMOUNT=1000000 \
  python scripts/generate_single_user_root.py
```

多用户/生产 root 生成器必须使用同一 leaf 格式，并与 OpenZeppelin `MerkleProof` 的 sorted-pair Merkle 树兼容。

### 4.4 前端常用读取接口

```solidity
tokenWhitelist(address token) returns (bool)
platformFeeBps() returns (uint16)
platformTreasury() returns (address)
minReviewWindow() returns (uint64)
paused() returns (bool)
tasks(bytes32 taskId)
qualifications(bytes32 taskId)
settlements(bytes32 taskId)
pendingRoots(address token)
activeRoots(address token)
claimed(address account, address token)
claimableDelta(address account, address token, uint128 cumulativeAmount)
settledButUnallocated(address token)
platformFeeBalances(address token)
taskEntropyBlock(bytes32 taskId)
taskEntropy(bytes32 taskId)
```

### 4.5 事件监听

必须用新版 ABI 监听和解码：

```text
TaskCreated
QualificationFinalized
TaskSettled
PendingRootPublished
PendingRootCancelled
RootActivated
Claimed
RefundClaimed
TaskCancelled
Paused
Unpaused
PlatformFeeAccrued
PlatformFeeWithdrawn
```

## 5. Operator Runbook

Operator 地址：

```text
0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776
```

私有配置：

```env
RPC_URL=...
ESCROW_PROXY_ADDRESS=0x86493b48CBEbd7B25BD89de585FFbEC439A4e453
USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e
OPERATOR_ADDRESS=0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776
OPERATOR_PRIVATE_KEY=...
```

### 5.1 任务进入可冻结状态

读取任务：

```bash
cast call $ESCROW \
  "tasks(bytes32)(bytes32,address,address,uint96,uint96,uint96,uint16,uint64,uint64,bytes32,uint8,uint16)" \
  $TASK_ID \
  --rpc-url $RPC_URL
```

必须满足：

- `status == 1`，即 `FUNDED`。
- 当前时间 `>= qualifyDeadline`。
- 当前时间距离 `settlementDeadline` 至少还有 3600 秒。

如果距离 `settlementDeadline` 不足 3600 秒，`finalizeQualification` 会 revert `insufficient settlement window`。此时不要强行推进，等待 Sponsor 到期走 `emergencyRefund`。

### 5.2 冻结合格名单

链下生成：

- `qualifiedCount`
- `qualifiedRoot`
- `qualificationManifestHash`

调用：

```bash
cast send $ESCROW \
  "finalizeQualification(bytes32,uint32,bytes32,bytes32)" \
  $TASK_ID \
  $QUALIFIED_COUNT \
  $QUALIFIED_ROOT \
  $QUALIFICATION_MANIFEST_HASH \
  --rpc-url $RPC_URL \
  --private-key $OPERATOR_PRIVATE_KEY
```

成功后读取：

```solidity
taskEntropyBlock(taskId)
```

合约会设置：

```text
taskEntropyBlock = finalizeQualification 所在区块 + 10
```

### 5.3 结算任务

结算必须在 `taskEntropyBlock` 之后，并在该区块的 `blockhash` 仍可读取的 256 块内完成。

金额公式：

```text
actualWinnerCount = min(qualifiedCount, lotteryWinnerCount)
platformFeeAmount = totalBudget * platformFeeBps / 10000
payoutAmount = baseRewardPerQualified * qualifiedCount + lotteryRewardPerWinner * actualWinnerCount
refundableAmount = totalBudget - payoutAmount - platformFeeAmount
```

调用：

```bash
cast send $ESCROW \
  "settleTask(bytes32,bytes32,bytes32,uint96,uint96,uint96)" \
  $TASK_ID \
  $SEED_REVEAL \
  $RESULT_MANIFEST_HASH \
  $PAYOUT_AMOUNT \
  $REFUNDABLE_AMOUNT \
  $BASE_REWARD_PER_QUALIFIED \
  --rpc-url $RPC_URL \
  --private-key $OPERATOR_PRIVATE_KEY
```

成功后：

- `payoutAmount` 进入 `settledButUnallocated[token]`。
- `platformFeeAmount` 进入 `platformFeeBalances[token]`。
- 如果 `refundableAmount > 0`，Sponsor 可调用 `claimRefund(taskId, recipient)`。
- `settlements(taskId).entropyRef` 是取熵区块号。
- `settlements(taskId).entropyValue` 是合约派生的最终熵。

### 5.4 生成累计 Merkle Root

Operator 可以把一个或多个已结算任务合并成 token 维度的累计 root。manifest 至少应保存：

```json
{
  "token": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  "rootId": 2,
  "priorCumulative": {
    "0xUser...": "previous cumulative amount"
  },
  "tasks": [
    {
      "taskId": "0x...",
      "lotteryWinnerCount": 2,
      "lotteryRewardPerWinner": "1000000",
      "baseRewardPerQualified": "500000",
      "qualified": ["0xUser1...", "0xUser2..."]
    }
  ]
}
```

要求：

- `rootId` 必须大于当前 `activeRoots(token).rootId`。
- `epochDeltaAmount` 是本次新增分配金额，不是历史累计总额。
- 所有 leaf 必须使用第 3 节的 leaf 格式。
- manifest 必须能让 Guardian 独立复算 root、中奖名单和金额。

Guardian 复算脚本：

```bash
python scripts/guardian_recompute.py --self-test
RPC_URL=$RPC_URL ESCROW_PROXY_ADDRESS=0x86493b48CBEbd7B25BD89de585FFbEC439A4e453 \
  python scripts/guardian_recompute.py --manifest path/to/manifest.json
```

这个脚本的完整使用要求见第 6.1 节。Operator 生成 manifest 时必须保证字段结构和脚本约定一致，否则 Guardian 无法在审查窗口内独立复算。

### 5.5 发布 pending root

同一 token 同时只能存在一个 pending root。

当前 `minReviewWindow = 86400` 秒，因此 `delayWindow` 默认填 `86400`。

```bash
cast send $ESCROW \
  "publishPendingRoot(address,uint64,bytes32,uint128,uint64,bytes32)" \
  $USDC \
  $ROOT_ID \
  $MERKLE_ROOT \
  $EPOCH_DELTA_AMOUNT \
  86400 \
  $MANIFEST_HASH \
  --rpc-url $RPC_URL \
  --private-key $OPERATOR_PRIVATE_KEY
```

发布成功后：

- `epochDeltaAmount` 会从 `settledButUnallocated[token]` 扣除。
- `PendingRootPublished` 会给出 `activateAfter`。
- Guardian 应在 `activateAfter` 前完成复算。

### 5.6 激活 root

`activateRoot` 不要求 Operator 权限，任何人都可以调用。后端可以自动执行：

```bash
cast send $ESCROW \
  "activateRoot(address,uint64)" \
  $USDC \
  $ROOT_ID \
  --rpc-url $RPC_URL \
  --private-key $OPERATOR_PRIVATE_KEY
```

激活后，用户才能基于该 root claim。

## 6. Guardian Runbook

Guardian Safe：

```text
0x9DbB8F6cD251D508015D93D14E08a4CEAac84C40
```

Guardian 不经过 Timelock，职责是快速止损。

可调用函数：

```solidity
pause()
cancelPendingRoot(address token, uint64 rootId)
```

Guardian 在收到 `PendingRootPublished` 后应完成：

1. 读取 pending root 的 `rootId`、`merkleRoot`、`epochDeltaAmount`、`activateAfter`、`manifestHash`。
2. 获取对应 manifest。
3. 用 `scripts/guardian_recompute.py` 独立复算 root。
4. 检查中奖人数是否等于 `min(qualifiedCount, lotteryWinnerCount)`。
5. 检查金额公式、`epochDeltaAmount` 和累计 root 是否一致。
6. 如果发现 root 不一致、金额不守恒、manifest 不可获得或中奖规则不一致，在 `activateAfter` 前调用 `cancelPendingRoot(token, rootId)`。

取消后，`epochDeltaAmount` 会退回 `settledButUnallocated[token]`，Operator 可修正 manifest/root 后重新发布。

### 6.1 Guardian 复算脚本使用说明

参考脚本：

```text
scripts/guardian_recompute.py
```

用途：

- 在审查窗口内独立复算 Operator 发布的待激活 Merkle root。
- 比对本地复算 root 与链上 `pendingRoots(token).merkleRoot`。
- 交叉校验链上 `settlements(taskId)` 中的熵、中奖人数和基础奖励。
- 发现不一致时提示 Guardian 在 `activateAfter` 前调用 `cancelPendingRoot(token, rootId)`。

脚本实现的公开算法：

1. 熵派生：
   ```text
   finalEntropy = keccak256(seedReveal || blockhash(taskEntropyBlock))
   ```
   对齐合约中的 `keccak256(abi.encode(seedReveal, blockhash))`。
2. 中奖人数：
   ```text
   winnerCount = min(qualifiedCount, lotteryWinnerCount)
   ```
3. 中奖排序：对每个合格地址计算 score，按 score 升序取前 `winnerCount` 名。
4. 金额分配：每个合格者得 `baseRewardPerQualified`，中奖者额外得 `lotteryRewardPerWinner`。
5. 累计 root：对每个用户跨历史和本批任务累加 `cumulativeAmount`，再用 claim leaf 格式构建 OZ 兼容 Merkle root。

运行前置条件：

- 本地已安装 Python 依赖：`web3`、`eth_utils`。
- 已执行过 `forge build`，因为脚本会读取：
  ```text
  out/EscrowVault.sol/EscrowVault.json
  ```
- 已设置链上读取环境变量：
  ```env
  RPC_URL=https://...
  ESCROW_PROXY_ADDRESS=0x86493b48CBEbd7B25BD89de585FFbEC439A4e453
  ```

离线自测：

```bash
python scripts/guardian_recompute.py --self-test
```

自测不连链，验证脚本内的熵派生、中奖选择、金额分配、Merkle proof 和篡改检测是否自洽。预期返回码为 `0`，输出包含：

```text
[SUCCESS] 自测全部通过
```

连链复算：

```bash
RPC_URL=$RPC_URL \
ESCROW_PROXY_ADDRESS=0x86493b48CBEbd7B25BD89de585FFbEC439A4e453 \
python scripts/guardian_recompute.py --manifest path/to/manifest.json
```

manifest 最小结构：

```json
{
  "token": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  "rootId": 2,
  "priorCumulative": {
    "0xUser...": "1000000"
  },
  "tasks": [
    {
      "taskId": "0x...",
      "lotteryWinnerCount": 2,
      "lotteryRewardPerWinner": "1000000",
      "baseRewardPerQualified": "500000",
      "qualified": [
        "0xUser1...",
        "0xUser2..."
      ]
    }
  ]
}
```

字段要求：

- `token` 必须与待复算的 pending root token 一致。
- `rootId` 必须与 pending root 的 `rootId` 一致。
- `priorCumulative` 是旧 active root 已累计的用户应得金额，金额用 token 最小单位字符串。
- `tasks[].taskId` 必须是已成功 `settleTask` 的任务 ID。
- `tasks[].qualified` 必须是该任务冻结后的合格地址列表，顺序不影响结果，但集合必须完整。
- 金额字段必须用最小单位字符串，USDC 场景下 `1 USDC = 1000000`。

脚本返回码约定：

| 返回码 | 含义 | 处理 |
| --- | --- | --- |
| `0` | 复算通过，本地 root 与链上 pending/active root 一致 | 可继续等待 `activateAfter` 后激活 |
| `1` | 环境或输入错误，例如未设置 `ESCROW_PROXY_ADDRESS`、RPC 不通、缺少编译产物 | 修复环境后重跑，不要激活 |
| `2` | 复算发现异常 | Guardian 应在 `activateAfter` 前取消 pending root |

连链复算成功时，输出会列出：

- token
- rootId
- 本地复算 root
- 链上 pending root
- 链上 active root
- 每个 task 的中奖人数和 payout

如果输出包含 `[ALERT]`，默认视为不可激活，除非 Operator 和 Guardian 双方完成原因定位并重新发布正确 root。

## 7. Admin Governance Runbook

这些动作属于治理配置，不是 Operator 日常流程：

```solidity
setTokenWhitelist(address token, bool allowed)
setPlatformFeeBps(uint16 newBps)
setPlatformTreasury(address newTreasury)
setMinReviewWindow(uint64 newWindow)
withdrawPlatformFees(address token, address recipient, uint256 amount)
updateOperator(address oldOperator, address newOperator)
updateGuardian(address oldGuardian, address newGuardian)
unpause()
upgradeToAndCall(address newImplementation, bytes data)
```

调用路径：

1. 在 Admin Safe Transaction Builder 选择 TimelockController：
   ```text
   0x89946bb9D13b8BDF24C9047C7c655AC4acA58c49
   ```
2. ABI 使用：
   ```text
   abi/TimelockController.abi.json
   ```
3. 调用 `schedule(target, value, data, predecessor, salt, delay)`。
4. 等待当前 delay：`600` 秒。
5. 调用 `execute(target, value, data, predecessor, salt)`。

其中 `target` 是 EscrowVault Proxy：

```text
0x86493b48CBEbd7B25BD89de585FFbEC439A4e453
```

`data` 是被治理的 Vault 函数 calldata，例如：

```bash
cast calldata "setMinReviewWindow(uint64)" 3600
cast calldata "setPlatformTreasury(address)" 0xNewTreasury...
cast calldata "setPlatformFeeBps(uint16)" 200
```

`execute` 必须使用和 `schedule` 完全相同的 `target/value/data/predecessor/salt`。

## 8. Acceptance Checklist

接手后建议按以下顺序验收。每一步失败都应停下来排查，不要跳步推进。

1. 前端连接 Base Sepolia，确认 `chainId == 84532`。
2. 使用新版 ABI 读取 `platformFeeBps == 200`。
3. 读取 `tokenWhitelist(USDC) == true`。
4. 读取 `paused() == false`。
5. 处理现有 `pendingRoots(USDC).rootId == 1`，选择激活或取消。
6. Sponsor approve 少量 USDC 给 Vault。
7. Sponsor 创建小额任务，确保 `settlementDeadline >= qualifyDeadline + 3600`。
8. 到 `qualifyDeadline` 后，Operator 调 `finalizeQualification`。
9. 读取 `taskEntropyBlock(taskId)`。
10. 到 `taskEntropyBlock` 后，Operator 在 256 块内调 `settleTask`。
11. 读取 `settledButUnallocated(USDC)` 和 `platformFeeBalances(USDC)`，确认金额符合预期。
12. Operator 生成累计 Merkle root。
13. Operator 调 `publishPendingRoot`，`delayWindow` 当前使用 `86400`。
14. Guardian 使用 `scripts/guardian_recompute.py` 复算 pending root。
15. 到 `activateAfter` 后调用 `activateRoot`。
16. 用户用 proof 调 `claim`，确认 `Claimed` 事件和 USDC 到账。
17. 如果有退款，Sponsor 调 `claimRefund`。

## 9. Troubleshooting

| 错误 | 常见原因 | 处理 |
| --- | --- | --- |
| `Token not allowed` | token 未白名单，或下架且无待清退余额 | 生产只用白名单 USDC；如需调整由 Admin Safe 通过 Timelock 执行 |
| `Settlement window too short` | 创建任务时 `settlementDeadline - qualifyDeadline < 3600` | 前端限制最小 1 小时窗口 |
| `insufficient settlement window` | Operator 太晚 finalize，距离 settlementDeadline 不足 1 小时 | 不再推进结算，等 Sponsor 到期 `emergencyRefund` |
| `entropy block not reached` | settle 太早 | 等 `taskEntropyBlock` 之后再结算 |
| `entropy block expired` | settle 太晚，超过 256 块 | 结算服务必须自动化；Base Sepolia 窗口约 8 分 32 秒 |
| `Payout mismatch` | `payoutAmount` 未按合约公式计算 | 使用 `baseRewardPerQualified * qualifiedCount + lotteryRewardPerWinner * min(qualifiedCount, lotteryWinnerCount)` |
| `Sum mismatch` | `payoutAmount + refundableAmount + platformFeeAmount != totalBudget` | 重新按平台费公式计算退款 |
| `Delay too short` | `delayWindow < minReviewWindow` | 当前填 `86400`，或由 Admin Safe 通过 Timelock 调低 `minReviewWindow` |
| `Pending root exists` | 同一 token 已有 pending root 未激活/取消 | 先激活或 Guardian 取消 |
| `Invalid proof` | proof、leaf、root 参数不匹配 | 检查 leaf 格式、token、rootId、cumulativeAmount、account |
| `Nothing to claim` | 用户已领过或累计金额不大于已领金额 | 前端先读 `claimableDelta` |

## 10. File Index

| 文件 | 用途 |
| --- | --- |
| `abi/EscrowVault.abi.json` | 前端、后端、indexer 调用 Vault 的唯一 ABI |
| `abi/TimelockController.abi.json` | Admin Safe Transaction Builder 使用的 ABI |
| `deployments/path_b_base_sepolia.json` | 当前部署地址和角色配置 |
| `scripts/generate_single_user_root.py` | 单用户 smoke test root/proof 生成 |
| `scripts/guardian_recompute.py` | Guardian 复算 manifest/root 的参考实现 |
| `contracts/EscrowVault.sol` | 合约源码和最终业务规则 |
