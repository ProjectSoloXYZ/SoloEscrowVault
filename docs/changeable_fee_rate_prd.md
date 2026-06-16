# 可配置平台费率 PRD

## 1. 背景与目标

EscrowVault 已支持 Sponsor 发布任务、Operator 结算、Guardian 风控拦截、用户基于累计 Merkle Root 自助提现。为了支持平台商业化与活动补贴，需要在任务资金流中增加可配置平台服务费。

目标：

- 平台默认从每个成功结算任务的总预算中计提 2% 服务费。
- 管理员可以在 0%-10% 之间调整费率，例如活动期间设置为 0%。
- 费率在任务创建时锁定，避免任务创建后被后续调价影响。
- 平台费与用户发奖、Sponsor 退款保持链上资金守恒。

## 2. 角色与权限

- `DEFAULT_ADMIN_ROLE`
  - 设置全局平台费率。
  - 设置平台费默认接收地址 `platformTreasury`。
  - 提现已计提的平台费。
- `OPERATOR_ROLE`
  - 继续负责资格确认、任务结算和发布累计 Merkle Root。
  - 结算时不传平台费，平台费由合约内部根据任务锁定费率计算。
- `GUARDIAN_ROLE`
  - 继续负责暂停系统和取消可疑 Pending Root。
- Sponsor
  - 创建任务时锁入 `totalBudget`。
  - 正常结算后可领取 `refundableAmount`。
  - 主动取消或超时强退时可全额取回 `totalBudget`。

## 3. 计费规则

全局默认配置：

- `platformFeeBps = 200`，即 2%。
- `MAX_PLATFORM_FEE_BPS = 1000`，即 10%。
- `FEE_DENOMINATOR_BPS = 10000`。

任务创建时：

- `TaskConfig.platformFeeBps` 保存当前全局费率。
- 后续管理员调整全局费率，不影响已创建任务。
- Sponsor 转入预算后，Vault 必须校验实际收到的 token 数量等于 `totalBudget`。
- 如果 token 存在转账税、通缩、rebasing 或其他导致 `balanceOf(address(this))` 被动变化的机制，任务创建必须 revert 或不得加入白名单。
- 合约按当前全局费率预估平台费：

```text
estimatedPlatformFee = totalBudget * platformFeeBps / 10000
```

- 最大承诺奖励必须给平台费留出空间：

```text
basePool + lotteryRewardPerWinner * lotteryWinnerCount + estimatedPlatformFee <= totalBudget
```

- 如果上述条件不满足，`createTask` 必须 revert，避免 Sponsor 创建一个无法同时覆盖奖励和平台费的任务。

任务结算时：

```text
platformFeeAmount = totalBudget * taskPlatformFeeBps / 10000
```

- 舍入规则：向下取整。
- 用户发奖总额必须由任务规则唯一决定：

```text
payoutAmount = baseRewardPerQualified * qualifiedCount + lotteryRewardPerWinner * actualWinnerCount
```

- Operator 不能通过提高 `payoutAmount` 压缩 Sponsor 的 `refundableAmount`。
- 合约校验：

```text
payoutAmount + refundableAmount + platformFeeAmount == totalBudget
```

资金流：

- `payoutAmount` 进入 `settledButUnallocated[token]`，后续发布 Root 给用户提现。
- `refundableAmount` 由 Sponsor 调用 `claimRefund` 领取。
- `platformFeeAmount` 进入 `platformFeeBalances[token]`，由管理员调用 `withdrawPlatformFees` 提现。
- 白名单 token 必须满足转账实收金额等于转账参数金额，保证链上内部账本和合约真实余额一致。

## 4. 异常与边界规则

- `cancelTask`
  - 仅允许任务仍处于 `FUNDED` 状态。
  - 不产生平台费。
  - Sponsor 全额取回 `totalBudget`。
- `emergencyRefund`
  - 仅允许任务处于 `FUNDED` 或 `QUALIFIED`，且已超过 `settlementDeadline`。
  - 不产生平台费。
  - Sponsor 全额取回 `totalBudget`。
- 0 人合格但正常结算
  - `payoutAmount` 必须为 0。
  - 仍按 `totalBudget` 和任务锁定费率计提平台费。
  - Sponsor 领取 `totalBudget - platformFeeAmount`。
- 0% 活动费率
  - 新任务锁定 0% 后，正常结算不产生平台费。
  - 旧任务继续使用创建时锁定的旧费率。

## 5. 验收标准

合约验收：

- 默认费率为 2%，管理员可调整为 0%-10%。
- 超过 10% 的费率设置必须 revert。
- 非管理员不能修改费率、修改 treasury 或提现平台费。
- `createTask` 必须拒绝 `最大承诺奖励 + 预估平台费 > totalBudget` 的任务。
- `createTask` 必须拒绝实际到账金额不等于 `totalBudget` 的 token 转账。
- `settleTask` 必须拒绝 `payoutAmount` 不等于基础奖励加抽奖奖励的结算。
- 正常结算后平台费进入 `platformFeeBalances[token]`。
- 提现平台费会减少账本并把 token 转给指定 recipient。
- `cancelTask` 和 `emergencyRefund` 不产生平台费。
- UUPS 升级后已有状态和角色保持不变。

测试验收：

- `forge build` 通过。
- `forge test -vvv` 全部通过。
- Anvil 上运行 `tests/test_escrow_full.py` 端到端通过。
- README 展示平台费资金流与运行方式。
