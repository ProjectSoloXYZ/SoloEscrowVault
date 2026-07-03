# 独立小修：SOL-07 / SOL-08

> 给程序员的施工图　|　分支：`fix/audit-remediation`
> 这两条与路线 A 主架构无关，可独立提交。

---

## SOL-07（Minor）SimpleToken 缺零地址检查

**位置**：`contracts/SimpleToken.sol:23~29`（`transfer`）、`37~45`（`transferFrom`）

**现状**：转账不检查收款地址是否为 `address(0)`，且无 burn / 不减 `totalSupply`，误转零地址的币永久锁死却仍计入总供应。

**施工**：
```solidity
function transfer(address to, uint256 value) public virtual returns (bool) {
    require(to != address(0), "transfer to zero");      // 新增
    require(balanceOf[msg.sender] >= value, "Insufficient balance");
    // ...不变
}

function transferFrom(address from, address to, uint256 value) public virtual returns (bool) {
    require(to != address(0), "transfer to zero");      // 新增
    require(balanceOf[from] >= value, "Insufficient balance");
    // ...不变
}
```

**更优先的处理（与 SOL-01 一致）**：`SimpleToken` 仅测试用途，生产用 USDT/USDC。建议**把它明确标注为测试 mock**（重命名 `MockERC20` 或移入 `test/`），向 CertiK 申请标记为 out-of-scope。加零地址检查无害，可与标注并行。

**验收**：向 `address(0)` 转账 → revert。

---

## SOL-08（Informational）角色轮换缺旧地址校验

**位置**：`contracts/EscrowVault.sol:467~482`（`updateOperator` / `updateGuardian`）

**现状**：`_revokeRole(ROLE, oldX)` 在 `oldX` 不持有角色时静默 no-op。若管理员传错旧地址，撤销空转、新地址照样被授予 → **真正的旧角色仍保留权限**，交易却"看似成功"。

**施工**：
```solidity
function updateOperator(address oldOperator, address newOperator) external onlyRole(DEFAULT_ADMIN_ROLE) {
    require(newOperator != address(0), "bad operator");
    require(hasRole(OPERATOR_ROLE, oldOperator), "old not operator");   // 新增：失败即关闭
    _revokeRole(OPERATOR_ROLE, oldOperator);
    _grantRole(OPERATOR_ROLE, newOperator);
    emit OperatorUpdated(oldOperator, newOperator);
}

function updateGuardian(address oldGuardian, address newGuardian) external onlyRole(DEFAULT_ADMIN_ROLE) {
    require(newGuardian != address(0), "bad guardian");
    require(hasRole(GUARDIAN_ROLE, oldGuardian), "old not guardian");   // 新增
    _revokeRole(GUARDIAN_ROLE, oldGuardian);
    _grantRole(GUARDIAN_ROLE, newGuardian);
    emit GuardianUpdated(oldGuardian, newGuardian);
}
```

**验收**：传一个不持有该角色的 `oldOperator/oldGuardian` → revert，不产生"双持有者"状态。

---

*备注：这两条不改动存储布局，无升级安全顾虑。*
