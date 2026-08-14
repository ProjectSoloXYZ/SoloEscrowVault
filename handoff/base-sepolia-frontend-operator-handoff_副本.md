# EscrowVault Base Sepolia 前端与 Operator 交接文档

最后更新：2026-07-09

本文档用于交接给负责前端和结算服务的同事。请不要转发 `.env`、私钥、Safe owner 私钥或任何签名设备助记词；本文只包含公开链上地址、ABI 路径、调用流程和结算规则。

## 1. 当前部署信息

| 项目 | 值 |
| --- | --- |
| 网络 | Base Sepolia |
| Chain ID | `84532` |
| EscrowVault Proxy（业务合约地址，前端/Operator 主要使用） | `0x86493b48CBEbd7B25BD89de585FFbEC439A4e453` |
| EscrowVault Implementation | `0xace943dB8abDCdf79B27c2715067CCfB4E59b8d0` |
| TimelockController | `0x89946bb9D13b8BDF24C9047C7c655AC4acA58c49` |
| Admin Safe | `0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182` |
| Guardian Safe | `0x9DbB8F6cD251D508015D93D14E08a4CEAac84C40` |
| Operator 当前地址 | `0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776` |
| Base Sepolia USDC | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| ABI | `abi/EscrowVault.abi.json` |
| Timelock ABI | `abi/TimelockController.abi.json` |
| 部署输出 | `deployments/path_b_base_sepolia.json` |

### 当前链上配置快照

以下为 2026-07-09 查询到的 Base Sepolia 链上状态：

| 配置项 | 当前值 |
| --- | --- |
| `tokenWhitelist(USDC)` | `true` |
| `platformFeeBps` | `200`，即 2% |
| `platformTreasury` | `0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182`（Admin Safe） |
| `minReviewWindow` | `86400` 秒，即 24 小时 |
| `paused()` | `false` |
| `settledButUnallocated(USDC)` | `0` |
| `activeRoots(USDC)` | `rootId=0`，尚无 active root |
| `pendingRoots(USDC)` | 当前有一个测试 pending root：`rootId=1`，`epochDeltaAmount=1000000`，`activateAfter=2026-07-10 15:18:40 CST` |

当前这个 `rootId=1` 是单用户 smoke test root，root 为：

```text
0xf43c43f2b6699b51e968f3580773e7efd6597fc44ad1cae72c377afbbaca6d1f
```

如果只是交接生产开发，可以忽略这个测试 root；如果要继续这次 smoke test，等到 `activateAfter` 之后调用 `activateRoot(USDC, 1)`，再用对应 proof claim。

接手前需要先处理这个 pending root，否则后续发布新的 USDC root 会遇到 `Pending root exists`：

- 如果继续 smoke test：到 `2026-07-10 15:18:40 CST` 后激活 `rootId=1`。
- 如果不继续 smoke test：由 Guardian Safe 调用 `cancelPendingRoot(USDC, 1)` 清掉 pending root。

## 2. 与旧版合约的兼容性说明

本节用于给前端和业务函数同事快速判断：哪些代码可以基本沿用，哪些必须按当前 ABI 和新业务规则修改。这里的“旧版”指交接前 GitHub 仓库 `Winston-9527/SoloEscrowVault` 中的 EscrowVault 合约；当前部署使用的是审计整改后的本地版本。

### 基本没变的部分

这些函数的函数名、参数类型和调用入口保持兼容，旧代码通常只需要替换为当前 ABI 和当前部署地址：

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

业务模型中也保持不变的部分：

- Sponsor 仍然先 `approve`，再调用 `createTask` 把预算转入 Vault。
- 用户领取仍然走累计金额模型：root 中放 `cumulativeAmount`，链上只领取 `cumulativeAmount - claimed[user][token]`。
- claim 的 Merkle leaf 格式未变：
  ```solidity
  keccak256(bytes.concat(keccak256(abi.encode(account, token, rootId, cumulativeAmount))))
  ```
- Operator 仍负责资格冻结、任务结算、发布 pending root；Guardian 仍负责紧急暂停和取消可疑 pending root。
- `OPERATOR_ROLE` 的日常业务动作不走 Timelock；`GUARDIAN_ROLE` 的 `pause()` / `cancelPendingRoot()` 也不走 Timelock。

### 必须适配的变化

这些点会直接影响前端、结算后端、indexer 或后台管理工具，不能沿用旧 ABI/旧参数。

1. `settleTask` 签名变了，旧结算代码会直接调用失败。

   旧版：
   ```solidity
   settleTask(bytes32,bytes32,uint64,bytes32,bytes32,uint96,uint96,uint96,uint16)
   ```

   当前版本：
   ```solidity
   settleTask(bytes32,bytes32,bytes32,uint96,uint96,uint96)
   ```

   当前版本不再由 Operator 传入 `entropyRef`、`entropyValue`、`actualWinnerCount`。合约在 `finalizeQualification` 后设置 `taskEntropyBlock`，结算时用 `seedReveal + blockhash(taskEntropyBlock)` 派生熵，并按 `min(qualifiedCount, lotteryWinnerCount)` 确定实际中奖人数。

2. `TaskCreated` 和 `TaskSettled` 事件 ABI 变了，事件 topic 也变了。

   - `TaskCreated` 新增 `platformFeeBps` 字段。
   - `TaskSettled` 新增 `platformFeeAmount` 字段。

   如果后端/indexer 监听这些事件，必须更新为 `abi/EscrowVault.abi.json`，不要用旧 ABI 解码。

3. `tasks(taskId)` 和 `settlements(taskId)` 的返回值变长。

   - `tasks` 现在多返回 `platformFeeBps`。
   - `settlements` 现在多返回 `platformFeeAmount`。

   如果业务代码用数组下标或手写返回 tuple，需要同步修改。下文的 `cast call` 示例已经是新版返回签名。

4. 创建任务的预算校验更严格。

   当前创建任务时会锁定平台费率，默认 `platformFeeBps = 200`，即 2%。预算必须满足：

   ```text
   basePool + lotteryRewardPerWinner * lotteryWinnerCount + totalBudget * platformFeeBps / 10000 <= totalBudget
   ```

   同时，合约会检查 ERC20 实收金额必须等于 `totalBudget`，因此不支持转账税、通缩或 rebasing token。生产请使用标准 USDC。

5. 时间窗口更严格。

   - `createTask` 要求 `settlementDeadline >= qualifyDeadline + 3600`。
   - `finalizeQualification` 时要求当前时间距离 `settlementDeadline` 至少还有 1 小时。
   - `settleTask` 必须在 `taskEntropyBlock` 之后，并且在 256 块 blockhash 可读窗口内完成。Base Sepolia 当前约 2 秒/块，窗口约 8 分 32 秒。

   这意味着结算后端必须自动化执行，不建议依赖人工在页面上手动点结算。

6. `publishPendingRoot.delayWindow` 不能再随便填短窗口。

   当前默认 `minReviewWindow = 86400` 秒，即 24 小时；`delayWindow` 必须满足：

   ```text
   minReviewWindow <= delayWindow <= MAX_DELAY_WINDOW
   ```

   旧代码如果传 `0`、`3600` 或其他小于 24 小时的值，会 revert `Delay too short`。如需调低，必须由 Admin Safe 通过 Timelock 调用 `setMinReviewWindow`。

7. 新增平台费相关读写接口。

   前端/管理后台可以读取：

   ```solidity
   platformFeeBps()
   platformTreasury()
   platformFeeBalances(address token)
   ```

   管理动作包括：

   ```solidity
   setPlatformFeeBps(uint16)
   setPlatformTreasury(address)
   withdrawPlatformFees(address,address,uint256)
   ```

   这些管理动作都属于 admin 权限，当前必须通过 Admin Safe + Timelock 执行。

## 3. 权限模型

这个部署采用 Safe + Timelock 的治理结构：

| 角色 | 链上持有者 | 用途 | 是否走 Timelock |
| --- | --- | --- | --- |
| `DEFAULT_ADMIN_ROLE` | TimelockController | 白名单、平台费、treasury、升级、角色轮换、unpause | 是 |
| Timelock `PROPOSER/EXECUTOR/CANCELLER` | Admin Safe | 代表管理团队排队和执行管理动作 | Safe 发起 Timelock 交易 |
| `OPERATOR_ROLE` | Operator EOA：`0x7331...8776` | 资格确认、任务结算、发布 pending root | 否，日常直接调用 Vault |
| `GUARDIAN_ROLE` | Guardian Safe：`0x9DbB...C40` | 紧急 `pause()`、取消可疑 pending root | 否，直接调用 Vault |

注意：Admin Safe 不是直接持有 Vault 的 `DEFAULT_ADMIN_ROLE`。Vault 的管理员是 Timelock；Admin Safe 控制 Timelock。也就是说，管理员动作要先 `schedule`，等 600 秒 Timelock delay 后再 `execute`。Operator 的日常结算动作不经过 Timelock。

## 4. 前端需要集成的核心能力

前端主要面向三类用户：Sponsor、用户领取者、内部 Operator/管理后台。

### Sponsor 创建任务

Sponsor 创建任务分两笔交易：

1. `USDC.approve(EscrowVaultProxy, totalBudget)`
2. `EscrowVault.createTask(...)`

`createTask` 参数：

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

前端/后端必须保证：

- USDC 是 6 decimals，所有金额用最小单位。例如 `1 USDC = 1000000`。
- `taskId` 必须唯一，建议后端生成并持久化，例如 `keccak256(campaignId, sponsor, nonce)`。
- `seedReveal` 是后端生成的 32 字节随机值，必须安全保存到结算阶段。
- `seedCommit = keccak256(abi.encodePacked(seedReveal))`，创建任务时上链，结算时 reveal。
- `qualifyDeadline` 必须是未来时间。
- `settlementDeadline >= qualifyDeadline + 3600`，合约要求最小结算窗口 1 小时。
- 创建任务时会按当前 `platformFeeBps` 锁定平台费率。当前为 2%。
- 预算约束：`basePool + lotteryRewardPerWinner * lotteryWinnerCount + totalBudget * platformFeeBps / 10000 <= totalBudget`。

### 用户领取奖励

用户领取调用：

```solidity
claim(
  address token,
  uint64 rootId,
  uint128 cumulativeAmount,
  bytes32[] merkleProof,
  address recipient
)
```

领取模型是“累计金额模型”：

- Merkle leaf 里存的是用户在当前 root 下的累计应得金额 `cumulativeAmount`。
- 合约里记录 `claimed[user][token]`，用户本次实际拿到的是 `cumulativeAmount - claimed[user][token]`。
- 用户必须自己作为 `msg.sender` 调用 `claim`，因为 leaf 中绑定的是 `msg.sender`。
- `recipient` 可以是用户自己，也可以是另一个收款地址。

claim leaf 格式必须严格一致：

```solidity
leaf = keccak256(bytes.concat(
  keccak256(abi.encode(account, token, rootId, cumulativeAmount))
));
```

单用户测试 root 生成脚本见：

```bash
CLAIM_USER=0x... TOKEN=0x036CbD53842c5426634e7929541eC2318f3dCF7e ROOT_ID=1 AMOUNT=1000000 \
  python scripts/generate_single_user_root.py
```

多用户/生产 root 生成器也必须使用同一 leaf 格式和 OpenZeppelin `MerkleProof` 兼容的 sorted-pair Merkle 树。

### 前端常用读取接口

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
```

前端需要重点监听这些事件：

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
```

## 5. Operator 结算流程

Operator 地址当前是：

```text
0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776
```

Operator 私钥由负责结算的同事保管，不要放到前端，不要提交到仓库。Operator 日常动作直接调用 `EscrowVault Proxy`，不通过 Safe/Timelock。

### 步骤 1：等任务报名/资格期结束

读取任务：

```bash
cast call $ESCROW "tasks(bytes32)(bytes32,address,address,uint96,uint96,uint96,uint16,uint64,uint64,bytes32,uint8,uint16)" $TASK_ID --rpc-url $RPC_URL
```

必须满足：

- `status == FUNDED`，枚举值为 `1`
- 当前时间 `>= qualifyDeadline`
- 当前时间距离 `settlementDeadline` 至少还有 1 小时，否则 `finalizeQualification` 会 revert `insufficient settlement window`

### 步骤 2：冻结合格名单

Operator 链下计算合格名单，生成：

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

调用成功后，合约会设置：

```text
taskEntropyBlock = 当前区块 + 10
```

### 步骤 3：在取熵区块后尽快结算任务

`settleTask` 必须在 `taskEntropyBlock` 之后，并且在 EVM `blockhash` 仍可读取的 256 块内完成。Base Sepolia 约 2 秒/块，256 块大约 8 分 32 秒，所以这一步要由后端自动化监控执行，不建议人工慢慢点。

结算公式：

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

- `payoutAmount` 进入 `settledButUnallocated[token]`
- `platformFeeAmount` 进入 `platformFeeBalances[token]`
- 如果 `refundableAmount > 0`，Sponsor 后续可调用 `claimRefund(taskId, recipient)`

### 步骤 4：生成累计 Merkle Root

Operator 可以把一个或多个已结算任务合并成一个 token 维度的累计 root。manifest 至少应保存：

```json
{
  "token": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  "rootId": 2,
  "priorCumulative": {
    "0xUser...": "旧 root 已累计应得金额"
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

Guardian 复算脚本已有参考实现：

```bash
python scripts/guardian_recompute.py --self-test
RPC_URL=$RPC_URL ESCROW_PROXY_ADDRESS=0x86493b48CBEbd7B25BD89de585FFbEC439A4e453 \
  python scripts/guardian_recompute.py --manifest path/to/manifest.json
```

### 步骤 5：发布 pending root

`rootId` 必须大于当前 `activeRoots(token).rootId`，并且同一 token 同时只能有一个 pending root。

`delayWindow` 必须满足：

```text
minReviewWindow <= delayWindow <= MAX_DELAY_WINDOW
```

当前 `minReviewWindow = 86400` 秒，所以生产/测试如果没有先通过 Timelock 调整它，`delayWindow` 不能填 3600，否则会 revert `Delay too short`。

调用：

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

其中：

- `epochDeltaAmount` 是本次新加入 root 的发奖增量总额，不是全历史累计金额。
- `manifestHash` 是链下 manifest 文件的哈希，建议用 IPFS CID 对应内容哈希或稳定 JSON 文件的 keccak。
- 发布成功后，`epochDeltaAmount` 会从 `settledButUnallocated[token]` 扣除，进入 pending root。

### 步骤 6：审查窗口结束后激活 root

`activateRoot` 不要求 Operator 权限，任何人都可以调用。后端可以自动执行：

```bash
cast send $ESCROW \
  "activateRoot(address,uint64)" \
  $USDC \
  $ROOT_ID \
  --rpc-url $RPC_URL \
  --private-key $OPERATOR_PRIVATE_KEY
```

激活后，用户才能基于这个 root claim。

## 6. Guardian 的职责

Guardian Safe 当前地址：

```text
0x9DbB8F6cD251D508015D93D14E08a4CEAac84C40
```

Guardian 的动作不经过 Timelock，目的是应急快速处理：

```solidity
pause()
cancelPendingRoot(address token, uint64 rootId)
```

当 `PendingRootPublished` 出现后，Guardian 应在 `activateAfter` 前复算 manifest。如果 root 不一致、金额不守恒、中奖结果不符合公开算法，Guardian Safe 应调用：

```solidity
cancelPendingRoot(token, rootId)
```

取消后，`epochDeltaAmount` 会退回 `settledButUnallocated[token]`，Operator 可修正 manifest/root 后重新发布。

## 7. Admin Safe / Timelock 管理动作

这些动作不是 Operator 日常流程，只有管理配置时需要：

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

调用方式：

1. Admin Safe Transaction Builder 选择 TimelockController 地址：`0x89946bb9D13b8BDF24C9047C7c655AC4acA58c49`
2. ABI 使用：`abi/TimelockController.abi.json`
3. 先调用 `schedule(target, value, data, predecessor, salt, delay)`
4. 等 `delay` 秒。当前 Base Sepolia Timelock delay 是 `600` 秒。
5. 再调用 `execute(target, value, payload, predecessor, salt)`

这里的 `target` 是 EscrowVault Proxy：

```text
0x86493b48CBEbd7B25BD89de585FFbEC439A4e453
```

这里的 `data/payload` 是被治理的 Vault 函数 calldata，例如：

```bash
cast calldata "setMinReviewWindow(uint64)" 3600
cast calldata "setPlatformTreasury(address)" 0xNewTreasury...
cast calldata "setPlatformFeeBps(uint16)" 200
```

`execute` 必须使用和 `schedule` 完全相同的 `target/value/data/predecessor/salt`。

## 8. 前端/后端环境变量建议

不要沿用部署用 `.env`。前端和结算服务应拆开配置。

前端公开配置：

```env
NEXT_PUBLIC_CHAIN_ID=84532
NEXT_PUBLIC_ESCROW_VAULT=0x86493b48CBEbd7B25BD89de585FFbEC439A4e453
NEXT_PUBLIC_USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e
NEXT_PUBLIC_ADMIN_SAFE=0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182
NEXT_PUBLIC_GUARDIAN_SAFE=0x9DbB8F6cD251D508015D93D14E08a4CEAac84C40
```

结算后端私有配置：

```env
RPC_URL=...
ESCROW_PROXY_ADDRESS=0x86493b48CBEbd7B25BD89de585FFbEC439A4e453
USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e
OPERATOR_ADDRESS=0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776
OPERATOR_PRIVATE_KEY=...
```

`OPERATOR_PRIVATE_KEY` 只能放在后端密钥管理系统或安全运行环境中，不允许进入浏览器、移动端包、Git 仓库、日志、错误上报系统。

## 9. 测试清单

接手后建议按以下顺序验证：

1. 前端连接 Base Sepolia，读取 `platformFeeBps=200`、`tokenWhitelist(USDC)=true`、`paused=false`。
2. Sponsor approve 少量 USDC 给 Vault。
3. Sponsor 创建一个小额任务，`settlementDeadline` 至少比 `qualifyDeadline` 晚 1 小时。
4. 到 `qualifyDeadline` 后，Operator 调 `finalizeQualification`。
5. 过 `taskEntropyBlock` 后，Operator 在 256 块内调 `settleTask`。
6. Operator 生成累计 Merkle root，调用 `publishPendingRoot`。当前审查窗口默认 24 小时。
7. Guardian 用 `scripts/guardian_recompute.py` 复算 pending root。
8. 到 `activateAfter` 后调用 `activateRoot`。
9. 用户用 proof 调 `claim`，确认 `Claimed` 事件和 USDC 到账。
10. 如果有退款，Sponsor 调 `claimRefund`。

## 10. 常见错误

| 错误 | 原因 | 处理 |
| --- | --- | --- |
| `Token not allowed` | token 未白名单，或下架且无待清退余额 | 管理员通过 Timelock 白名单；生产只用标准 USDC |
| `Settlement window too short` | 创建任务时 `settlementDeadline - qualifyDeadline < 3600` | 前端限制最小 1 小时窗口 |
| `insufficient settlement window` | Operator 太晚 finalize，距离 settlementDeadline 不足 1 小时 | 后端加定时任务，超时让 Sponsor 走 emergencyRefund |
| `entropy block not reached` | settle 太早 | 等 `taskEntropyBlock` 之后再结算 |
| `entropy block expired` | settle 太晚，超过 256 块 | 必须自动化结算，Base Sepolia 约 8 分 32 秒窗口 |
| `Delay too short` | `publishPendingRoot.delayWindow < minReviewWindow` | 当前用 `86400`，或 Admin Safe 通过 Timelock 调低 |
| `Pending root exists` | 同一 token 已有 pending root 未激活/取消 | 先激活或 Guardian 取消 |
| `Invalid proof` | proof/leaf/root 不匹配 | 检查 leaf 格式、token、rootId、cumulativeAmount、地址大小写 |
| `Nothing to claim` | 用户已领过或累计金额不大于已领金额 | 前端先读 `claimableDelta` |

## 11. 文件索引

| 文件 | 用途 |
| --- | --- |
| `abi/EscrowVault.abi.json` | 前端/后端调用 Vault 的 ABI |
| `abi/TimelockController.abi.json` | Admin Safe Transaction Builder 使用的 ABI |
| `deployments/path_b_base_sepolia.json` | 当前部署地址和角色配置 |
| `scripts/generate_single_user_root.py` | 单用户 smoke test root/proof 生成 |
| `scripts/guardian_recompute.py` | Guardian 复算 manifest/root 的参考实现 |
| `contracts/EscrowVault.sol` | 合约源码和业务规则 |
