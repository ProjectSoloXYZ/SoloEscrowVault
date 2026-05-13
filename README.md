# EscrowVault — 半中心化任务结算系统

基于 Solidity 的可升级智能合约，实现 Sponsor-Operator 模式的任务资金托管与结算。

## 项目介绍

EscrowVault 解决的是 **“链下大规模业务结算 + 链上可验证发放”** 这一类常见场景：广告主投放悬赏、活动平台发奖、众包平台结算、空投与积分兑现等。完全在链上跑这些业务，往往会被参与人数、复杂的合格判定逻辑与高昂的 Gas 费拦住；完全在链下做，又会失去资金透明度与抗赖账能力。

本项目的核心思路是把流程拆成三段：

- **资金链上托管**：Sponsor 一次性把 ERC20 预算锁入合约（含基础奖励池 + 抽奖额外奖池），并提交一个随机种子承诺（Commit）。
- **业务链下计算**：Operator 在链下完成合格名单、随机抽奖、最终金额的计算，并把结果摘要（Merkle Root + 清单哈希 + 揭示种子）回写到链上。Commit-Reveal 保证 Operator 在预算锁定后无法操纵随机性。
- **结算链上兑付**：合约把多个任务的应得金额合并成 **累计型 Merkle Root**，进入 `delay window` 公示期后激活，用户随时拿 Proof 自助提现；Sponsor 的退款、Operator 的错误、Guardian 的紧急介入都有对应分支。

由此得到几个值得强调的特性：

1. **半中心化但可制衡**：Operator 拥有计算/编排权，但所有资金移动都受 `whenNotPaused / nonReentrant / Merkle 校验 / RoleGuard` 多重门控；Guardian 可暂停系统并在公示期内取消恶意 Pending Root，Sponsor 在结算超时后可触发 `emergencyRefund` 强制取回预算。
2. **累计型 Merkle Root + 用户余额账本**：合约用 `claimed[user][token]` 记录终身累计已领，而非按 Task 记账，因此用户即便参与了多个任务，也只需 **一次 `claim` 调用** 即可一次性提走所有未领部分，极大降低 Gas。
3. **状态机驱动的资金分配**：`NONE → FUNDED → QUALIFIED → SETTLED / REFUNDABLE → REFUNDED / CANCELLED` 把每一个钱包动作都映射到唯一一次状态跃迁；Operator 只能在合法状态下推进，避免重复结算或错位发放。
4. **可升级架构（UUPS）**：核心逻辑通过 `ERC1967Proxy` 部署，`_authorizeUpgrade` 由 `DEFAULT_ADMIN_ROLE` 限定；存储层预留 `__gap[41]` 槽位，未来追加新字段不会破坏代理布局。
5. **可验证的随机性与审计链路**：每个任务都绑定 `seedCommit / seedReveal / entropyRef`，可同时引入外部熵；每个生命周期事件都带索引化字段（`taskId / sponsor / token / rootId`），便于链下索引器与审计工具回放任意时刻的状态。

整套合约配合 `tests/test_escrow_full.py` 形成可复跑的端到端样例：脚本会在 Anvil 上部署 UUPS 代理、跑完“基础结算 → 多任务汇总提现 → 紧急退款 / Guardian 拦截 → UUPS 升级保留状态”四个阶段，覆盖正常路径与异常分支，适合作为合约设计、Solidity 编程实践与运维演练的参考实现。

## 功能概述

- **Sponsor** 创建任务并锁定 ERC20 代币预算
- **Operator** 确认合格名单、提交结算摘要
- **Operator** 发布累计 Merkle Root，用户凭 Proof 提现
- **Guardian** 可暂停系统、取消错误的 Pending Root
- 支持 UUPS 合约升级、ReentrancyGuard 防重入攻击

## 合约说明

| 合约 | 说明 |
|------|------|
| `EscrowVault.sol` | 核心托管合约，基于 OpenZeppelin 可升级框架 |
| `SimpleToken.sol` | 测试用 ERC20 代币 |

### 角色

- `DEFAULT_ADMIN_ROLE` — 管理员
- `OPERATOR_ROLE` — 操作员（资格确认、结算、发布 Root）
- `GUARDIAN_ROLE` — 守护者（暂停、取消错误 Root）

### 任务状态机

```
NONE → FUNDED → QUALIFIED → SETTLED / REFUNDABLE → REFUNDED
        ↓
    CANCELLED
```

## 目录结构

```
.
├── contracts/          # Solidity 合约（含触发 ERC1967Proxy 编译的 Proxy.sol）
├── tests/              # Python + web3.py 测试脚本
├── docs/               # 设计文档
├── foundry.toml        # Foundry 编译配置（src/out/remappings）
├── lib/                # OpenZeppelin 依赖（forge install 后生成，已在 .gitignore）
└── README.md
```

## 技术栈

- **Solidity** ^0.8.20（编译器锁定 0.8.24）
- **OpenZeppelin** 可升级合约（AccessControl / UUPS / Pausable）
- **Python 3.10+** + **web3.py 7.x**
- **Anvil** / Foundry 本地测试网

## 快速开始

> 前置依赖：已安装 [Foundry](https://book.getfoundry.sh/getting-started/installation)（提供 `forge` / `anvil`）与 Python ≥ 3.10。

### 1. 创建并激活 Python 虚拟环境

```bash
cd /root/blockchain_project
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install web3
```

### 2. 安装 Solidity 依赖

```bash
# 拉取 OpenZeppelin 合约库到 lib/
forge install OpenZeppelin/openzeppelin-contracts --no-commit
forge install OpenZeppelin/openzeppelin-contracts-upgradeable --no-commit
```

> 已在 `foundry.toml` 中通过 `remappings` 把 `@openzeppelin/...` 指向 `lib/...`，无需额外配置。

### 3. 编译合约（生成 ABI / 字节码工件）

```bash
forge build
```

成功后 `out/` 目录会包含 Python 脚本所需的三份工件：

- `out/SimpleToken.sol/SimpleToken.json`
- `out/EscrowVault.sol/EscrowVault.json`
- `out/ERC1967Proxy.sol/ERC1967Proxy.json`（由 `contracts/Proxy.sol` 触发生成）

### 4. 启动本地链 Anvil

新开一个终端窗口：

```bash
anvil
```

保持该终端常驻；它会监听 `http://127.0.0.1:8545`，并打印 10 个预置账户的私钥（测试脚本使用前 4 个）。

### 5. 运行 Python 端到端测试

回到第一个已激活 venv 的终端，执行：

```bash
python tests/test_escrow_full.py
```

脚本会自动完成：

1. **部署上链**——`SimpleToken` + `EscrowVault` 实现合约 + `ERC1967Proxy` 代理 + Token 白名单初始化，并把代理地址写入 `deployed_contracts.json`（下次运行复用，无需重复部署）。
2. **第一阶段**：`createTask` → `finalizeQualification` → `settleTask` 基础结算。
3. **第二阶段**：多任务汇总、`publishPendingRoot` → `activateRoot` → 用户 `claim` 提现。
4. **第三阶段**：`emergencyRefund` 超时退款 + Guardian `cancelPendingRoot`。
5. **第四阶段**：UUPS `upgradeToAndCall` 升级及权限校验。

### 6. 重新跑测试（可选）

如需从干净状态再跑一次，关闭 Anvil 后重新启动，并删除地址缓存：

```bash
rm -f deployed_contracts.json
python tests/test_escrow_full.py
```

> 提示：测试脚本会自动给 `NO_PROXY` 追加 `127.0.0.1,localhost`，避免本机 `HTTP(S)_PROXY` 拦截到 Anvil 的请求。

## 部署架构（UUPS 代理）

`tests/test_escrow_full.py` 的 `deploy_contracts()` 已实现完整流程，对应 README 早期描述的三步：

1. 部署 `EscrowVault` 实现合约（逻辑合约）。
2. 用 `EscrowVault.initialize(admin, operator, guardian)` 的 calldata 部署 `ERC1967Proxy`。
3. 通过代理地址 `setTokenWhitelist(token, true)` 完成白名单初始化。

## 安全特性

- ReentrancyGuard 防重入攻击
- Pausable 紧急暂停
- SafeERC20 兼容非标准代币
- Merkle Proof 链下验证链上结算
- Commit-Reveal 随机数机制

## License

MIT
