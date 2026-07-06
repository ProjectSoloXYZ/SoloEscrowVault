# 审计整改文档包 · 交接索引

> 分支：`fix/audit-remediation`　|　审计：CertiK Preliminary（2026-06-26，基准 commit `2f80050`）
> 读者：程序员（施工）、技术组长（复核）、负责人（审阅）
> 维护：技术组长　|　更新：2026-06-30

---

## 工作流

负责人 + 组长写文档 → **程序员照本规格改代码并自测（`forge build` + `forge test`）** → 提交给组长复核 → 组长汇总报告给负责人 → 整改后交 CertiK 关闭。

> ⚠️ **施工前必做**：程序员把本地未推送的改动（声称 commit `5f61dee`）先 `git push` 上来，组长 review 后再按本规格调整。当前远程仍停在审计基准 `2f80050`。

---

## 锁定的关键决策（2026-06-30 负责人拍板）

| 决策 | 结论 | 理由 |
|---|---|---|
| **D0'（架构路线）** | **路线 A 硬化版**：保留累计 root + 跨任务聚合领取，逐条打补丁 | 业务是小额任务（~1 USDC/任务，0.x USDC/人），按 task 领取 gas 不可行；累计聚合领取是 gas 刚需 |
| D1（熵源） | 种子 ⊕ 锁名单后区块哈希（**不用 VRF**） | 半中心化 + Guardian 监控，VRF 外部依赖不值当 |
| D2（抽奖验证） | 确定性算法 + 链下可复算 + Guardian 窗口挑战（**非合约重算**） | 合约重算在小额场景 gas 不可行；守恒已挡资金安全底线 |

> 历史：曾探索"路线 B（按 task 现算、拆 root 子系统）"，因小额 gas 经济性被否决，相关设计稿见 git 历史 commit `7477e02` / `c598050`（已作废）。

---

## Finding 状态总表（10 条）

| ID | 严重级 | 类别 | 整改归属 | 文档 |
|----|--------|------|----------|------|
| SOL-04 | Medium | 资金安全 | 合约：聚合守恒 | [EscrowVault-hardening-spec.md](EscrowVault-hardening-spec.md) §2 |
| SOL-05 | Medium | 资金安全 | 合约：最小审查窗口+防截断 | 同上 §3 |
| SOL-06 | Minor | 一致性 | 合约+链下：确定性可复算抽奖 | 同上 §4 |
| SOL-09 | Discussion | 设计 | 保留+文档化+链下不变量 | 同上 §5 |
| SOL-10 | Minor | 设计 | 合约：存量清退 | 同上 §6 |
| SOL-07 | Minor | 代码 | 合约：零地址检查 | [minor-fixes.md](minor-fixes.md) |
| SOL-08 | Info | 一致性 | 合约：角色校验 | 同上 |
| SOL-01 | Centralization | 治理 | **部署**：多签持币 | 见下「治理类」 |
| SOL-02 | Centralization | 治理 | **部署**：Timelock+多签 | 见下「治理类」 |
| SOL-03 | Centralization | 治理 | **部署**：同 SOL-02 | 见下「治理类」 |

---

## 治理类（SOL-01/02/03）—— 不是合约施工，是部署与运营

不需改合约代码，靠**部署时的角色分配 + 运营披露**关闭。要点（详见组长的整体评估报告，存于仓库外 `solo_contract/审计整改评估报告.md`）：

- `DEFAULT_ADMIN_ROLE` → **TimelockController(48h) + Gnosis Safe 多签(3/5)**。
- ⚠️ **`GUARDIAN_ROLE` 不要进 Timelock** —— 它是应急刹车（`pause`/`cancelPendingRoot`），必须能快速动作，挂 48h 等于拆刹车。用独立快速多签。
- `OPERATOR_ROLE` → 运营热钱包或轻量多签（结算/发 root 是高频操作，不能进 48h Timelock）。
- SimpleToken 标为测试 mock（见 SOL-07），生产用 USDT/USDC，多签持币。
- 交 CertiK 关闭需提交：Timelock 地址、Gnosis 多签地址 + 全部签名人、公开说明链接、token 分发计划。

> 这部分需要负责人 + DevOps 准备部署脚本与多签，**组长会另出一份部署清单**（尚未撰写）。

---

## 复核（组长）验收清单

代码提交后，组长逐条核：
1. SOL-04 是**聚合守恒**而非单用户封顶；构造多用户超额 root 测试通过。
2. SOL-05 最小窗口 + 防截断 + 上限齐全；默认 24h。
3. SOL-06 熵由合约派生（非 Operator 传入）；中奖人数确定性；Guardian 复算脚本可用。
4. SOL-10 存量清退走通，且 `createTask` 对下架 token 仍 revert。
5. SOL-07/08 按 [minor-fixes.md](minor-fixes.md)。
6. 存储布局：新增变量正确从 `__gap` 扣除，未破坏升级兼容（见 hardening §7）。
7. 回归测试全绿；`forge build` 无编译错误，且无存储布局/可见性相关告警（测试文件中的 unsafe-typecast 为 lint 级提示，不在此列）。

---

## 文档清单

- [README.md](README.md) —— 本文件，交接索引
- [EscrowVault-hardening-spec.md](EscrowVault-hardening-spec.md) —— 合约主施工图（SOL-04/05/06/09/10）
- [minor-fixes.md](minor-fixes.md) —— SOL-07/08
- `solo_contract/审计整改评估报告.md`（仓库外）—— 给负责人的整体评估（含治理类详述）
