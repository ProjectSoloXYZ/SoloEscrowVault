# CertiK 审计回复 — SoloMission

**日期**：2026-06-27  
**对应审计**：CertiK 初步审计报告（2026-06-26）  
**代码库**：ProjectSoloXYZ/SoloEscrowVault  

---

## SOL-01：初始代币全量分配给部署者（Centralization）

**状态**：Acknowledged — 部署配置解决

**回复**：  
SimpleToken 是测试/MVP 用途的简化 ERC20，**不作为生产代币合约使用**。生产环境直接使用已审计的第三方 ERC20（如 USDT/USDC），不存在"全量供应分配给部署者"的问题。

**关闭条件所需信息**（部署后补充）：
- 代币分配计划链接：https://www.solomission.xyz/tokenomics （TODO：部署后填入）
- 多签钱包地址：`0x...` （TODO：Gnosis Safe 部署后填入）
- Signer 1：`0x...`
- Signer 2：`0x...`
- Signer 3：`0x...`

**补充说明**：即便 SimpleToken 仅用于测试，部署时也将使用 Gnosis Safe 多签（⅔ 阈值）作为部署者地址，配合 TokenVesting 合约实现线性归属。

---

## SOL-02：EscrowVault 多角色中心化（Centralization）

**状态**：Acknowledged — 部署配置解决（非代码层面问题）

**回复**：  
当前 MVP 阶段采用中心化角色管理是有意为之的设计决策。此问题通过**部署配置**而非代码修改解决：

**去中心化路线图**：
1. **v1.1（主网部署时）**：`DEFAULT_ADMIN_ROLE` 直接授予 Gnosis Safe 多签（3/5 阈值），单地址 EOA 不持有任何特权角色
2. **v2.0**：引入 OpenZeppelin TimelockController（48h 延迟）作为多签上层，所有特权操作需经过延迟窗口
3. **v3.0**：引入 Governor 合约实现 DAO 治理，社区投票控制升级和参数变更

**关闭条件所需信息**（部署后补充）：
- Timelock 合约地址：`0x...` （TODO：部署后填入）
- Gnosis Safe 多签地址：`0x...` （TODO：部署后填入）
- 所有 Signer 地址：（TODO：部署后填入）
- Medium/Blog 公示链接：https://medium.com/@solomission/... （TODO：发布后填入）

**代码层面**：合约已支持通过 `updateOperator` / `updateGuardian` 实现角色轮换，管理员权限可随时迁移至多签地址，无需升级合约。

---

## SOL-03：UUPS 升级权限集中（Centralization）

**状态**：Acknowledged — 部署配置解决（同 SOL-02）

**回复**：  
与 SOL-02 方案一致，升级权限通过**部署配置**逐步去中心化：

1. **短期**：`DEFAULT_ADMIN_ROLE` 授予 TimelockController（48h 延迟），TimelockController 的 proposer/executor 由 Gnosis Safe 多签控制
2. **长期**：升级权限移交 DAO Governor 合约

**代码层面已有的安全措施**：
- `_authorizeUpgrade` 函数已限制为 `onlyRole(DEFAULT_ADMIN_ROLE)`
- 多签迁移后，任何升级操作需 3/5 签名 + 48h 公示期，社区有充足时间审查

**关闭条件**：与 SOL-02 共享，部署后提供 Timelock + 多签地址即可同时关闭 SOL-02 和 SOL-03。

---

## SOL-04：Merkle 累计领取可超出分配额（Medium）

**状态**：已修改，代码已提交

**修改内容**：  
在 `claim()` 函数中新增链上校验：

```solidity
// SOL-04 修复：用户累计领取不得超过当前 root 的 totalAllocated
require(cumulativeAmount <= a.totalAllocated, "Exceeds total allocated");
```

**位置**：`contracts/EscrowVault.sol` — `claim()` 函数，Merkle proof 验证后  
**效果**：任何用户的 `cumulativeAmount` 都不能超过当前 active root 的 `totalAllocated`，从而保证链上累计领取额受控。

---

## SOL-05：审查窗口延迟可被绕过（Medium）

**状态**：已修改，代码已提交

**修改内容**：

1. 新增常量强制最小审查窗口：
```solidity
uint64 public constant MIN_DELAY_WINDOW = 3600; // 1 hour minimum
```

2. 在 `publishPendingRoot()` 中增加校验：
```solidity
// SOL-05 修复：强制最小审查窗口，防止 operator 设置 0 绕过 guardian 审查
require(delayWindow >= MIN_DELAY_WINDOW, "Delay too short");

// SOL-05 修复：使用 uint256 计算后再安全转换，防止 uint64 截断导致溢出绕过
uint256 activateAfterCalc = block.timestamp + uint256(delayWindow);
require(activateAfterCalc <= type(uint64).max, "Delay overflow");
uint64 activateAfter = uint64(activateAfterCalc);
```

**位置**：`contracts/EscrowVault.sol` — `publishPendingRoot()` 函数  
**效果**：
- `delayWindow = 0` 场景 → revert "Delay too short"
- `delayWindow` 超大值导致 uint64 截断 → revert "Delay overflow"
- Guardian 至少有 1 小时窗口审查并 cancel 可疑 root

---

## SOL-06：彩票结果未链上验证（Minor）

**状态**：Acknowledged — 当前不修改，gas 成本不可接受

**回复**：  
当前设计中彩票胜者选定采用半中心化方案，**不在链上强制验证**。理由：

**为什么不改**：
1. **gas 成本不可接受**：链上验证需要遍历合格名单进行 Fisher-Yates 抽样，复杂度 O(n)。100 人 ≈ 50 万 gas；1000 人 ≈ 500 万 gas；BSC 上单次结算成本 $2-$10+
2. **Chainlink VRF 方案需额外依赖**：需要 LINK 代币 + VRF Coordinator 合约 + 回调机制，MVP 阶段引入过多外部依赖增加攻击面

**现有可验证性保障**：
1. `settleTask()` 强制 `keccak256(seedReveal) == seedCommit`，operator 无法事后篡改种子
2. 链上公开 `entropyRef` + `entropyValue` + `qualifiedRoot`，任何人可链下独立重现抽奖过程
3. `resultManifestHash` 链上不可篡改，支持链下审计
4. Guardian 可暂停系统，阻止可疑结算

**后续计划（v2.0）**：集成 Chainlink VRF v2.5，在链上完成完整抽奖逻辑，彻底消除 operator 操控空间。

---

## SOL-07：缺少零地址检查（Minor）

**状态**：已修改，代码已提交

**修改内容**：  
在 `SimpleToken.transfer()` 和 `SimpleToken.transferFrom()` 中新增：

```solidity
require(to != address(0), "Transfer to zero address");
```

**位置**：`contracts/SimpleToken.sol` — 第 24 行、第 38 行  
**效果**：防止代币被意外转入零地址造成永久丢失。

---

## SOL-08：角色轮换未验证旧地址是否持有角色（Informational）

**状态**：已修改，代码已提交

**修改内容**：  
在 `updateOperator()` 和 `updateGuardian()` 中增加显式验证：

```solidity
require(hasRole(OPERATOR_ROLE, oldOperator), "oldOperator lacks role");
```

```solidity
require(hasRole(GUARDIAN_ROLE, oldGuardian), "oldGuardian lacks role");
```

**位置**：`contracts/EscrowVault.sol` — `updateOperator()` / `updateGuardian()` 函数  
**效果**：传入错误的旧地址时交易会 revert，避免 OZ AccessControl 静默 no-op 导致旧账户权限残留。

---

## SOL-09：累计根激活使旧 Proof 失效（Discussion）

**状态**：Acknowledged — 确认为预期设计，不修改

**回复**：  
**确认：这是有意设计，不是 bug。** 理由如下：

**架构设计意图**：
1. **累计模型**：每次新 root 包含所有用户的最新累计应得金额（含历史），旧 proof 的信息已被新 root 完整覆盖，不存在"丢失"
2. **为什么不保留多 root 并存**：多 root 并存需要 claim 时遍历多个 root 验证，存储和验证复杂度翻倍，gas 成本不可控

**链下 proof 分发保障**：
1. 后端监听 `PendingRootPublished` 事件，自动为所有有余额的用户生成新 proof
2. 用户可通过 API 实时查询最新 proof；同时推送邮件/站内信通知
3. `MIN_DELAY_WINDOW`（1 小时）审查窗口保证旧 root 不会在用户 claim 之前被突然替换

**文档补充**：
- 已在合约 `activateRoot()` 函数注释中补充说明此设计意图
- 用户协议和开发者文档中明确标注：新 root 激活后旧 proof 失效，用户需通过 API 获取最新 proof

---

## SOL-10：代币下架后已结算资金无法取回（Minor）

**状态**：已修改，代码已提交

**修改内容**：  
新增紧急释放函数 `emergencyReleaseDelistedFunds()`：

```solidity
function emergencyReleaseDelistedFunds(address token, address recipient, uint256 amount)
    external
    onlyRole(DEFAULT_ADMIN_ROLE)
    nonReentrant
{
    require(recipient != address(0), "bad recipient");
    require(!tokenWhitelist[token], "Token still whitelisted");
    require(amount > 0, "zero amount");
    require(amount <= settledButUnallocated[token], "Exceeds unallocated");

    settledButUnallocated[token] -= amount;
    IERC20(token).safeTransfer(recipient, amount);

    emit EmergencyFundsReleased(token, recipient, amount);
}
```

**位置**：`contracts/EscrowVault.sol` — 新增函数  
**效果**：当代币已从白名单移除时，管理员可将被锁的 `settledButUnallocated` 资金转出给指定接收者（如受影响用户的多签合约），防止资金永久锁定。

---

## 总结

| 编号 | 严重等级 | 处理方式 | 状态 | 说明 |
|------|---------|---------|------|------|
| SOL-01 | Centralization | 部署配置 | Acknowledged | 非生产合约；部署时用多签，提供地址即可关闭 |
| SOL-02 | Centralization | 部署配置 | Acknowledged | 部署 Timelock + 多签，提供地址 + blog 链接关闭 |
| SOL-03 | Centralization | 部署配置 | Acknowledged | 同 SOL-02，共享关闭条件 |
| SOL-04 | Medium | **代码修改** | Resolved | claim() 增加 totalAllocated 上限校验 |
| SOL-05 | Medium | **代码修改** | Resolved | 强制 MIN_DELAY_WINDOW + uint256 防截断 |
| SOL-06 | Minor | Acknowledged | Acknowledged | gas 不可接受；现有 commit-reveal 可链下验证 |
| SOL-07 | Minor | **代码修改** | Resolved | SimpleToken 增加 to != address(0) 检查 |
| SOL-08 | Informational | **代码修改** | Resolved | 角色轮换增加 hasRole 前置校验 |
| SOL-09 | Discussion | Acknowledged | Acknowledged | 确认为预期设计，文档已补充说明 |
| SOL-10 | Minor | **代码修改** | Resolved | 新增 emergencyReleaseDelistedFunds() |

---

## 关闭条件 Action Items

### 代码修改（5 项，已完成）
- `contracts/EscrowVault.sol`（SOL-04、SOL-05、SOL-08、SOL-10）
- `contracts/SimpleToken.sol`（SOL-07）
- **专项验证脚本**：`tests/test_audit_fixes.py`

```bash
# 运行审计修复专项测试（需 Anvil 在后台运行）
source ~/.zshenv
source .venv/bin/activate
pkill -f anvil 2>/dev/null; anvil &
sleep 2
python tests/test_audit_fixes.py
```

**测试运行结果**（2026-06-27 通过）：
```
╔══════════════════════════════════════════════════════════╗
║   CertiK 审计修复验证测试 (SOL-04/05/07/08/10)        ║
╚══════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────
TEST SOL-07: SimpleToken 零地址检查
─────────────────────────────────────────────────────────────
  ✅ transfer(address(0)) 被正确 revert
  ✅ transferFrom(address(0)) 被正确 revert
  ✅ SOL-07 验证通过: 零地址转账被阻止

─────────────────────────────────────────────────────────────
TEST SOL-08: 角色轮换验证旧地址持有角色
─────────────────────────────────────────────────────────────
  ✅ updateOperator(错误旧地址) 被正确 revert (oldOperator lacks role)
  ✅ updateGuardian(错误旧地址) 被正确 revert (oldGuardian lacks role)
  ✅ updateOperator(正确旧地址) 正常执行
  ✅ SOL-08 验证通过: 角色轮换错误输入被阻止

─────────────────────────────────────────────────────────────
TEST SOL-05: 审查窗口最小值 + 溢出防护
─────────────────────────────────────────────────────────────
  ✅ delayWindow=0 被正确 revert (Delay too short)
  ✅ delayWindow=100 被正确 revert (Delay too short)
  ✅ delayWindow=3600 正常发布成功
  ✅ SOL-05 验证通过: 最小审查窗口强制生效

─────────────────────────────────────────────────────────────
TEST SOL-04: Merkle 领取不能超出 totalAllocated
─────────────────────────────────────────────────────────────
  ✅ SOL-04 验证通过: 超额 claim 被正确 revert (Exceeds total allocated)

─────────────────────────────────────────────────────────────
TEST SOL-10: 代币下架后紧急释放资金
─────────────────────────────────────────────────────────────
  ✅ 代币在白名单时调用被 revert (Token still whitelisted)
  ✅ 代币已从白名单移除
  ✅ 代币下架后 publishPendingRoot 被正确阻止
  ✅ 紧急释放成功: 8.0 Token 已转出
  ✅ settledButUnallocated 正确减少: 5.0 Token
  ✅ SOL-10 验证通过: 紧急释放路径工作正常

════════════════════════════════════════════════════════════
🎉 全部 5 项代码修复验证通过!
════════════════════════════════════════════════════════════
```

### 部署配置（3 项，部署后补充）

**自动化脚本**：`scripts/deploy_governance.py`

执行流程：
```bash
# 1. 编译合约（生成 TimelockController 编译产物）
forge build

# 2. 配置环境变量
export RPC_URL="https://bsc-dataseed1.binance.org/"
export DEPLOYER_PRIVATE_KEY="0x..."          # 当前持有 DEFAULT_ADMIN_ROLE 的 EOA
export ESCROW_PROXY_ADDRESS="0x..."          # 已部署的 EscrowVault 代理地址
export GNOSIS_SAFE_ADDRESS="0x..."           # 已创建的 Gnosis Safe 多签地址（3/5）
export TIMELOCK_DELAY="172800"               # 48 小时 = 172800 秒

# 3. 运行脚本
python scripts/deploy_governance.py
```

脚本自动完成：
1. 部署 TimelockController（48h 延迟）
2. 将 EscrowVault 的 `DEFAULT_ADMIN_ROLE` 授予 TimelockController
3. Revoke 原始 EOA 的 `DEFAULT_ADMIN_ROLE`
4. Renounce TimelockController 中 deployer 的临时 admin
5. 验证最终状态并输出 CertiK 所需地址

以下信息由脚本执行后自动输出，填入本文档并提交给 CertiK：

| 项目 | 用途 | 状态 |
|------|------|------|
| Gnosis Safe 多签地址 | SOL-01/02/03 关闭条件 | TODO |
| 所有 Signer 地址（3/5） | SOL-01/02/03 关闭条件 | TODO |
| TimelockController 合约地址 | SOL-02/03 关闭条件 | TODO |
| 代币分配计划公示链接 | SOL-01 关闭条件 | TODO |
| Medium/Blog 公示链接 | SOL-02/03 关闭条件 | TODO |

### Acknowledged（2 项，无需代码修改）
- SOL-06：后续 v2.0 集成 Chainlink VRF
- SOL-09：确认为预期设计
