# EscrowVault 整改施工规格（路线 A · 硬化版）

> 给程序员的施工图　|　分支：`fix/audit-remediation`
> 覆盖 finding：SOL-04 / SOL-05 / SOL-06 / SOL-09 / SOL-10（均在 `contracts/EscrowVault.sol`）
> 决策依据：D0'=路线 A（硬化现有累计 root 架构），D1=种子⊕区块哈希，2026-06-30 负责人拍板

---

## 0. 整改原则（先读，决定所有取舍）

业务是**小额高频任务**（单任务奖金 ~1 USDC，单人 0.x USDC）。领奖 gas 由用户自付，**必须保留"累计 root + 跨任务一次领完"的聚合领取模型**，否则按 task 领取的 gas 会吃掉大部分奖金。

因此整改的总原则是 **「合约强制守住资金安全底线 + 乐观验证保证分配正确」**：

- **合约强制（链上硬保证）**：永远发不出超过已结算总额的钱；坏 root 必须过最小审查窗口。→ 杜绝跨任务盗取资金。
- **乐观验证（链下可复算 + Guardian 窗口挑战）**：抽奖结果做成任何人可独立复算，Operator 在守恒上限内最多"发错人"，且会被 Guardian 在审查窗口内抓出并 `cancelPendingRoot`。

> 注意：本路线**不追求**"合约每次领取都重算验证"——在小额场景这是 gas 不可行的。SOL-06 的整改形态是"确定性可复算 + 守恒 + 审查窗口"，对 Minor 级 finding 足够，且 CertiK 可接受。

---

## 1. 不要动的部分（明确划线）

以下是这套架构在小额场景下的正确设计，**保持不变**：

- 累计 Merkle root 模型（`activeRoots` / `pendingRoots`）、`claim` 凭 proof 领 delta、`claimed[user][token]` 累计记账。
- `publishPendingRoot → activateRoot` 两段式 + Guardian `cancelPendingRoot` 审查机制。
- `settledButUnallocated` 记账缓冲。
- Task 生命周期、`createTask` / `cancelTask` / `emergencyRefund` / `claimRefund` / 平台费逻辑。

整改是在这套骨架上**打补丁**，不是重写。

---

## 2. SOL-04（Medium）聚合守恒 —— 杜绝超额领取

**位置**：`claim()`，约 `EscrowVault.sol:978~988`

**现状**：`claim` 只验 proof，发 `delta = cumulativeAmount - alreadyClaimed`，**不校验是否超过该 token 真实分配总额**。恶意/错误 root 可让用户超领，挖走其他任务/退款/平台费的钱。

**⚠️ 与程序员本地改动的冲突**：本地版加的是单用户封顶 `cumulativeAmount <= totalAllocated`，**不充分**（多个用户各写满额可合计超发）。**请废弃单用户封顶，改用下面的聚合守恒。**

**施工**：
```solidity
// 新增状态变量（放在合约状态区，见 §7 存储注意）：
mapping(address => uint256) public totalClaimed;   // 每个 token 累计已发放

// claim() 内，转账前：
uint256 deltaAmount = cumulativeAmount - alreadyClaimed;
require(
    totalClaimed[token] + deltaAmount <= activeRoots[token].totalAllocated,
    "Exceeds allocated"
);
totalClaimed[token] += deltaAmount;
// ...原有 claimed/transfer 逻辑不变
```

**为什么充分**：`totalAllocated` 只能经 `activateRoot` 增长，且 `epochDeltaAmount <= settledButUnallocated`，所以它是"累计真实结算额"的可信上界。聚合守恒保证**合约累计付出永不超过累计分配**——不管 root 里写了什么，都偷不走别的钱。

**验收**：构造一个含两个用户、各写 `totalAllocated` 满额的恶意 root → 第二个用户领取必须 revert。

---

## 3. SOL-05（Medium）最小审查窗口 + 防截断

**位置**：`publishPendingRoot()`，约 `EscrowVault.sol:873`

**现状**：`activateAfter = uint64(block.timestamp + delayWindow)`，`delayWindow` 由 Operator 自由传入、无下限。设 0 立即激活；设超大值触发 uint64 截断同样立即激活。审查窗口形同虚设。

**施工**：
```solidity
// 新增常量与可配置下限：
uint64 public constant MIN_REVIEW_FLOOR = 1 hours;   // 管理员也不能调到此值以下
uint64 public constant MAX_DELAY_WINDOW = 30 days;   // 上限，防 griefing
uint64 public minReviewWindow;                       // 管理员可配，默认 24h（见 initialize/initializeV2）

function setMinReviewWindow(uint64 v) external onlyRole(DEFAULT_ADMIN_ROLE) {
    require(v >= MIN_REVIEW_FLOOR, "below floor");
    minReviewWindow = v;
}

// publishPendingRoot() 内：
require(delayWindow >= minReviewWindow && delayWindow <= MAX_DELAY_WINDOW, "bad delayWindow");
uint256 activateAt = uint256(block.timestamp) + uint256(delayWindow);   // uint256 防截断
require(activateAt <= type(uint64).max, "activateAt overflow");
uint64 activateAfter = uint64(activateAt);
```
- `initialize` / `initializeV2` 中将 `minReviewWindow` 设为默认 `24 hours`。
- **建议值**：审查窗口默认 24h（给 Guardian 充分时间复算+集齐多签）。负责人可按运营节奏在 `[MIN_REVIEW_FLOOR, MAX_DELAY_WINDOW]` 内调。

**验收**：`delayWindow=0` / 低于 `minReviewWindow` / 超大触发截断的值 → 全部 revert。

---

## 4. SOL-06（Minor）确定性可复算抽奖 + 熵硬化

**位置**：`finalizeQualification()` 约 `:647`、`settleTask()` 约 `:692~716`

**现状**：`settleTask` 只校验 `keccak256(seedReveal)==seedCommit`，中奖名单全凭 Operator 链下计算并报数；`entropyRef/entropyValue` 是 Operator 传入的、不参与任何校验的"装饰字段"。Operator 可任意挑中奖者。

**整改思路（路线 A）**：让抽奖**确定性、可被任何人用链上数据复算**，并把熵绑定到**锁定合格名单之后、不可控的来源**，再靠 Guardian 在审查窗口内复算比对。合约本身不重算（小额场景 gas 不可行）。

### 4.1 熵硬化（D1：种子 ⊕ 锁名单后区块哈希）

```solidity
// 新增：taskId => 取熵区块号（用新 mapping，避免改 struct，见 §7）
mapping(bytes32 => uint64) public taskEntropyBlock;
mapping(bytes32 => bytes32) public taskEntropy;       // 落定的最终熵
uint64 public constant ENTROPY_BLOCK_DELAY = 10;       // K：锁名单后第 K 块取哈希

// finalizeQualification() 末尾追加：
taskEntropyBlock[taskId] = uint64(block.number) + ENTROPY_BLOCK_DELAY;

// settleTask() 内，校验 seedReveal 之后：
uint64 eb = taskEntropyBlock[taskId];
require(block.number > eb, "entropy block not reached");
bytes32 bh = blockhash(eb);
require(bh != bytes32(0), "entropy block expired");      // 超过 256 块取不到 → 见失败处理
taskEntropy[taskId] = keccak256(abi.encode(seedReveal, bh));
```
- **移除** `settleTask` 参数里 Operator 传入的 `entropyValue`；熵由合约派生，Operator 无从选择。`entropyRef` 改记 `taskEntropyBlock`。
- **运营约束（写进 SOP）**：① 种子必须在第 `eb` 块出块之后才揭示；② 结算必须在 `eb` 之后 256 块内（BSC≈13 分钟）完成。

### 4.2 去掉 Operator 对中奖人数的自由裁量

```solidity
// settleTask() 内，中奖人数改为确定性，不再信任 Operator 报送：
uint16 expectedWinners = q.qualifiedCount >= t.lotteryWinnerCount
    ? t.lotteryWinnerCount
    : uint16(q.qualifiedCount);
require(actualWinnerCount == expectedWinners, "winnerCount not deterministic");
```

### 4.3 确定性抽奖算法（链下，必须公开 + 钉哈希）

- 算法：从 `taskEntropy[taskId]` 出发，在合格名单（`qualifiedRoot` 对应的有序名单）中用拒绝采样抽取 `expectedWinners` 个**不重复索引**作为中奖者（Fisher-Yates / rejection sampling，顺序固定、可复现）。
- 将算法实现 + 版本号公开（仓库 + manifest），**算法版本哈希建议也上链存档**（可复用一个 manifest 字段）。
- Operator 据此构建奖励 Merkle root；root 内容**唯一确定**于（熵, 合格名单, base/bonus 单价）。

### 4.4 Guardian 复算验证（替代"合约重算"的乐观防线）

- Guardian（及任何监督者）在 `publishPendingRoot` 之后、审查窗口内：拉取合格 manifest + 结果 manifest，校验其哈希对上链上值 → 用链上 `taskEntropy` 跑公开算法重算正确 root → 与 Operator 发布的 root 比对。
- 不一致 → `cancelPendingRoot`。这把 SOL-06 的防线挂到 SOL-05 的审查窗口上。

**验收**：① `settleTask` 不再接收 Operator 的熵/人数自由值；② 给定相同 (熵, 名单)，链下算法复算结果稳定且与发布 root 一致；③ 模拟"锁名单前已知熵"vs"锁名单后产生熵"，验证后者无法 grind 名单；④ Guardian 复算脚本能对一个被篡改的 root 报警。

---

## 5. SOL-09（Discussion）累计模型 —— 保留 + 文档化

**结论**：累计 root 是小额场景的 gas 刚需，**保留**。但必须落实：

1. **文档**（`docs/security_model.md` 增补）：明确"只有最新 active root 可用于 `claim`；旧 proof 在新 root 激活后失效；用户必须取得最新 rootId/merkleRoot 的新 proof"。
2. **链下硬约束（测试卡死）**：每个新 root **必须包含所有"老 root 里仍有未领余额"用户的最新累计额**，否则有人永久领不到。这是链下 root 生成器的不变量，需单测覆盖。
3. 与 SOL-04 协同：聚合守恒只防超领，**漏人导致的少发是链下流程 bug**，靠上面的不变量 + 测试保证。

**验收**：链下 root 生成器单测——构造"上一轮有人没领完"的场景，新 root 必须携带其累计额。

---

## 6. SOL-10（Minor）下架 token 的存量清退路径

**位置**：`publishPendingRoot()` 白名单校验，约 `:856`

**现状**：`publishPendingRoot` 要求 `tokenWhitelist[token]`。token 在结算后、发 root 前被下架，则 `settledButUnallocated[token]` 里欠用户的钱卡死，无任何释放通道。

**施工（采用"只禁新单、不卡存量清退"，不引入新的 admin 提款权）**：
```solidity
// publishPendingRoot() 内，把：
//   require(tokenWhitelist[token], "Token not allowed");
// 改为：
require(
    tokenWhitelist[token] || settledButUnallocated[token] > 0,
    "Token not allowed and nothing to wind down"
);
```
- `createTask` 仍只允许白名单 token（不变）→ **下架后不能开新任务**。
- 但已结算欠用户的钱，仍可通过发 root 正常清退（`epochDeltaAmount <= settledButUnallocated` 已自然约束上限）。
- 不新增 admin 提款函数，避免把 Minor 修成"admin 可挪用用户资金"的中心化风险。

**验收**：token 下架后，对其 `settledButUnallocated>0` 的余额仍能 `publishPendingRoot` 并让用户领取；但 `createTask` 对该 token 必须 revert。

---

## 7. 存储与升级安全（务必遵守）

- 合约是 UUPS 可升级 + 有 `uint256[39] __gap`。**新增状态变量会占用 `__gap` 槽位**，每加一个就把 `__gap` 长度相应减一。
- **新增 per-task 数据用新的顶层 mapping**（如 `taskEntropyBlock`、`taskEntropy`），**不要往现有 `Qualification`/`Settlement`/`TaskConfig` struct 里塞字段**——除非确认合约尚未部署。改 struct 会改变 mapping value 的存储布局，已部署合约升级后读旧数据会错位。
- 新增的顶层变量清单：`totalClaimed`、`minReviewWindow`、`taskEntropyBlock`、`taskEntropy`（按需）。逐一从 `__gap` 扣除。
- 若新增 `initialize` 之后才有的变量，需在 `initializeV2`（或新增 `initializeV3` + `reinitializer(3)`）里赋默认值，并注意旧代理升级路径。

---

## 8. 测试要求（Foundry，合并到 `test/EscrowVault.t.sol`）

必须新增覆盖：
- **SOL-04**：多用户超额 root → 聚合守恒拦截；正常领取不受影响；`totalClaimed` 累计正确。
- **SOL-05**：`delayWindow` = 0 / 低于下限 / 触发 uint64 截断的超大值 → 全 revert；正常窗口可激活。
- **SOL-06**：熵派生正确；`entropyBlock` 未到 / 已过 256 块的分支；`actualWinnerCount` 非确定值 → revert；确定性算法复算稳定。
- **SOL-10**：下架 token 存量清退可走通；`createTask` 对下架 token revert。
- **回归**：原有 happy-path（创建→资格→结算→发 root→激活→领取→退款→平台费）全绿。

---

## 9. 与程序员本地未推送改动的协调

程序员本地已改 SOL-04/05/07/08/10（commit 声称 `5f61dee`，**尚未 push**）。施工前请：
1. **先 push 该分支**，我（组长）先 review 实际代码。
2. **SOL-04 必须从"单用户封顶"改为本规格的"聚合守恒"**（§2）。
3. SOL-05 对照本规格补齐"最小窗口下限 + 防截断 + 上限"（本地若只加了 `MIN_DELAY=3600`，窗口偏短且可能未防截断，见 §3）。
4. SOL-10 若本地用了新增 admin 提款函数，**改为本规格的存量清退方案**（§6）。
5. SOL-07/08 见 [minor-fixes.md](minor-fixes.md)。

---

*规格完。SOL-01/02/03（中心化）属部署与治理，不在本合约施工范围，见 [README.md](README.md) 与仓库外 `solo_contract/审计整改评估报告.md`。*
