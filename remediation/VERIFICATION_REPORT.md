# 审计整改验收报告

> 审计：CertiK Preliminary（2026-06-26，基准 commit `2f80050`）
> 整改分支：`fix/audit-remediation`
> 整改日期：2026-06-30
> 测试结果：**42 / 42 通过**

---

## 一、验收清单完成情况

对照 `remediation/README.md` 组长验收清单逐条核对：

| # | 清单要求 | 完成状态 | 合约改动位置 | 测试用例 |
|---|---|:---:|---|---|
| 1 | SOL-04 聚合守恒（非单用户封顶）；多用户超额 root 测试通过 | ✅ | `EscrowVault.sol` `claim()` + `totalClaimed` mapping | `test_SOL04_aggregateSolvency_blocksMultiUserOverClaim` |
| 2 | SOL-05 最小窗口 + 防截断 + 上限齐全；默认 24h | ✅ | `publishPendingRoot()` + `setMinReviewWindow()` + `initializeV3()` | `test_SOL05_*`（6 个） |
| 3 | SOL-06 熵由合约派生（非 Operator 传入）；中奖人数确定性；Guardian 可复算 | ✅ | `finalizeQualification()` + `settleTask()` + `taskEntropyBlock` / `taskEntropy` | `test_SOL06_*`（5 个） |
| 4 | SOL-10 存量清退走通；`createTask` 对下架 token 仍 revert | ✅ | `publishPendingRoot()` 白名单条件放宽；删除 `emergencyReleaseDelistedFunds` | `test_SOL10_*`（2 个） |
| 5 | SOL-07/08 按 minor-fixes.md | ✅ | `SimpleToken.sol` 加 NatSpec mock 警示；`updateOperator/updateGuardian` 已有 `hasRole` 校验 | `test_SOL07_*`（2 个）、`test_SOL08_*`（3 个） |
| 6 | 存储布局：新增变量从 `__gap` 扣除，未破坏升级兼容 | ✅ | `__gap` 从 38 减为 34（新增 4 个顶层变量），不动任何 struct | — |
| 7 | 回归测试全绿；`forge build` 无错误 | ✅ | — | `EscrowVaultTest`（21 个回归） |

> SOL-01/02/03（治理类）：不在合约施工范围，靠部署时多签 + Timelock 配置关闭，见 `remediation/README.md` 治理章节。

---

## 二、涉及文件清单

### 合约文件

| 文件 | 改动内容 |
|---|---|
| `contracts/EscrowVault.sol` | SOL-04 聚合守恒、SOL-05 审查窗口、SOL-06 熵硬化、SOL-08 角色校验、SOL-10 存量清退 |
| `contracts/SimpleToken.sol` | SOL-07 NatSpec mock 警示头 |

### 测试文件

| 文件 | 内容 |
|---|---|
| `test/EscrowVault.t.sol` | 原有 21 个回归用例（适配新 `settleTask` 签名、`delayWindow` 默认值） |
| `test/RemediationAudit.t.sol` | **新建**，21 个整改专项验收用例 |

### 文档文件

| 文件 | 改动内容 |
|---|---|
| `docs/security_model.md` | SOL-09 累计 root 使用约束 + 各 Finding 整改说明 |
| `docs/CertiK_Audit_Response.md` | 追加整改更新章节，覆盖原 VRF 承诺 |

---

## 三、测试执行命令

### 前置要求

```bash
# 确认 Foundry 已安装
forge --version

# 进入项目目录
cd /Users/zhenghuili/Desktop/keyan/SoloEscrowVault
```

### 全量测试（推荐）

```bash
# 编译（应无错误）
forge build

# 跑全部 42 个用例，输出汇总
forge test --summary
```

**预期输出：**

```
EscrowVaultTest:      21 passed; 0 failed; 0 skipped
RemediationAuditTest: 21 passed; 0 failed; 0 skipped
Total: 42 / 42 ✅
```

### 分套运行

```bash
# 原有回归（21 个）
forge test --match-contract EscrowVaultTest -v

# 整改专项验收（21 个）
forge test --match-contract RemediationAuditTest -v
```

### 按 Finding 单独验证

```bash
# SOL-04 聚合守恒（2 个用例）
forge test --match-test "SOL04" -vv

# SOL-05 最小审查窗口（7 个用例）
forge test --match-test "SOL05" -vv

# SOL-06 熵硬化 + 确定性中奖（5 个用例）
forge test --match-test "SOL06" -vv

# SOL-07 SimpleToken 零地址（2 个用例）
forge test --match-test "SOL07" -vv

# SOL-08 角色轮换旧地址校验（3 个用例）
forge test --match-test "SOL08" -vv

# SOL-10 下架 token 存量清退（2 个用例）
forge test --match-test "SOL10" -vv
```

---

## 四、专项验收用例说明

> 文件路径：`test/RemediationAudit.t.sol`

| 用例名 | 验证内容 |
|---|---|
| `test_SOL04_normalClaim_updatesTotalClaimed` | 正常领取后 `totalClaimed` 累加正确 |
| `test_SOL04_aggregateSolvency_blocksMultiUserOverClaim` | **关键**：两用户各写满 allocated → 第二笔 revert（聚合守恒拦截） |
| `test_SOL05_defaultMinReviewWindowIs24h` | 初始化后默认窗口 = 24h |
| `test_SOL05_publishWithDelayBelowMinReverts` | `delayWindow = 0` 或 `1h` → revert |
| `test_SOL05_publishWithDelayAboveMaxReverts` | `delayWindow > 30d` → revert |
| `test_SOL05_publishUint64TruncationGuard_reverts` | `uint64.max` 超大值 → revert（截断绕过路径被堵） |
| `test_SOL05_setMinReviewWindow_belowFloor_reverts` | 管理员设 `< 1h` → revert |
| `test_SOL05_setMinReviewWindow_aboveCeiling_reverts` | 管理员设 `> 30d` → revert |
| `test_SOL05_setMinReviewWindow_happyPath` | 合法调低后对应 delay 通过 |
| `test_SOL06_entropyBlock_setOnFinalize` | `finalizeQualification` 后 `taskEntropyBlock` 正确记录 |
| `test_SOL06_settleBeforeEntropyBlock_reverts` | 熵块未到 → revert |
| `test_SOL06_settleAfter256Blocks_reverts` | 超 256 块 blockhash 过期 → revert |
| `test_SOL06_entropy_isDeterministic` | 派生熵 = `keccak256(seed, blockhash)`，链下可独立复算 |
| `test_SOL06_actualWinnerCount_isDeterministic_min` | 合格 2 人、计划 5 人中奖 → 合约算出 2（min 规则） |
| `test_SOL07_transferToZero_reverts` | `transfer(address(0))` → revert |
| `test_SOL07_transferFromToZero_reverts` | `transferFrom(..., address(0))` → revert |
| `test_SOL08_updateOperator_oldNotHoldingRole_reverts` | 传错旧 operator 地址 → revert |
| `test_SOL08_updateGuardian_oldNotHoldingRole_reverts` | 传错旧 guardian 地址 → revert |
| `test_SOL08_updateOperator_happyPath` | 正常换人，旧角色撤销、新角色授予 |
| `test_SOL10_windDown_delistedToken_publishAllowed` | 下架 token 存量可通过 publishPendingRoot 清退；createTask 被拒 |
| `test_SOL10_delisted_noStuckBalance_publishStillReverts` | 无存量时下架 token publish 仍被拒 |

---

## 五、升级注意事项（已部署代理）

新增 4 个顶层状态变量，`__gap` 从 38 减为 34：

| 变量 | Finding | 槽位 |
|---|---|---|
| `uint64 public minReviewWindow` | SOL-05 | -1 |
| `mapping(address => uint256) public totalClaimed` | SOL-04 | -1 |
| `mapping(bytes32 => uint64) public taskEntropyBlock` | SOL-06 | -1 |
| `mapping(bytes32 => bytes32) public taskEntropy` | SOL-06 | -1 |

**旧代理升级后必须由 admin 调用一次 `initializeV3()`**，否则 `minReviewWindow = 0`，`publishPendingRoot` 会拒掉所有合法 delay。

不修改任何已有 struct（`TaskConfig` / `Qualification` / `Settlement` / `PendingRoot` / `ActiveRoot`），存储布局向后兼容。
