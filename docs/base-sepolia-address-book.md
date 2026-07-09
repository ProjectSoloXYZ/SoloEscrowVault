# EscrowVault Base Sepolia Address Book

最后更新：2026-07-09

本文档集中整理当前 Base Sepolia 部署和交接相关地址。

## 1. Wallet / Safe Addresses

| 名称 | 地址 | 类型 | 当前用途 | 
| --- | --- | --- | --- | 
| Admin Safe | `0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182` | Gnosis Safe / 多签钱包 | Timelock 的 proposer / executor / canceller；通过 Timelock 执行 admin 管理动作 |  
| Guardian Safe | `0x9DbB8F6cD251D508015D93D14E08a4CEAac84C40` | Gnosis Safe / 多签钱包 | 持有 Vault 的 `GUARDIAN_ROLE`；直接调用 `pause()` 和 `cancelPendingRoot()` |  
| Operator | `0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776` | EOA / 热钱包 | 持有 Vault 的 `OPERATOR_ROLE`；执行 `finalizeQualification`、`settleTask`、`publishPendingRoot` | 
| Deployer | `0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776` | EOA | 部署交易发送方；部署后不持有 Vault admin 权限 |  
| Smoke Test Claim User / Recipient | `0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776` | EOA | 单用户 smoke test root 的领取账户和收款地址 |  

注意：`0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776` 当前同时出现在 Deployer、Operator、smoke test claim user/recipient 中。不要因此推断它仍有 admin 权限；部署输出中的检查项显示 `Deployer is not Vault admin = true`。

## 2. Contract / Token Addresses

这些不是普通钱包地址，但前端、后端和 Safe Transaction Builder 也会用到，统一放在这里避免混淆。

| 名称 | 地址 | 类型 | 当前用途 | 
| --- | --- | --- | --- | 
| EscrowVault Proxy | `0x86493b48CBEbd7B25BD89de585FFbEC439A4e453` | UUPS Proxy / 业务合约入口 | 前端、Operator、用户领取、Sponsor 创建任务都应调用这个地址 | 
| EscrowVault Implementation | `0xace943dB8abDCdf79B27c2715067CCfB4E59b8d0` | 实现合约 | 当前 Proxy 指向的逻辑合约；业务方一般不直接调用 | 
| TimelockController | `0x89946bb9D13b8BDF24C9047C7c655AC4acA58c49` | 治理合约 | 持有 Vault 的 `DEFAULT_ADMIN_ROLE`；Admin Safe 通过它 schedule / execute 管理动作 | 
| Base Sepolia USDC | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` | ERC20 Token | 当前白名单 token；Sponsor 预算和用户奖励使用 USDC 最小单位 | 

## 3. Treasury / Role Mapping

| 项目 | 当前地址 | 说明 | 
| --- | --- | --- | 
| Vault `DEFAULT_ADMIN_ROLE` holder | `0x89946bb9D13b8BDF24C9047C7c655AC4acA58c49` | TimelockController 持有；Admin Safe 控制 Timelock | 
| Vault `OPERATOR_ROLE` holder | `0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776` | Operator EOA | 
| Vault `GUARDIAN_ROLE` holder | `0x9DbB8F6cD251D508015D93D14E08a4CEAac84C40` | Guardian Safe | 
| Timelock proposer | `0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182` | Admin Safe |
| Timelock executor | `0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182` | Admin Safe | 
| Timelock canceller | `0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182` | Admin Safe |
| Platform treasury | `0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182` | 交接文档链上快照显示当前为 Admin Safe | 

备注：`deployments/path_b_base_sepolia.json` 中的 `platform_treasury` 记录为 `0x89946bb9D13b8BDF24C9047C7c655AC4acA58c49`，交接文档链上快照记录当前 `platformTreasury` 为 Admin Safe。优先以最新链上读取结果和交接 Runbook 为准；如移交前再次核验，建议用 `platformTreasury()` 直接读链确认。

## 4. Safe Owner Addresses


| Safe | 角色说明 | 
| --- | --- | 
| Admin Safe | 管理治理动作的签名人 | 
| Guardian Safe | 应急暂停和取消 pending root 的签名人 | 

补充 owner 地址时，只记录公开钱包地址和角色说明，不记录私钥、助记词、设备位置或个人敏感信息。

## 5. Non-Wallet Hashes

以下值看起来像 `0x...`，但不是钱包地址，不要配置到钱包、Safe 或合约地址字段中。

| 名称 | 值 | 类型 | 用途 |
| --- | --- | --- | --- |
| Smoke test root / leaf | `0xf43c43f2b6699b51e968f3580773e7efd6597fc44ad1cae72c377afbbaca6d1f` | `bytes32` hash | 单用户 smoke test 的 Merkle root / leaf |
| `DEFAULT_ADMIN_ROLE` | `0x0000000000000000000000000000000000000000000000000000000000000000` | `bytes32` role id | OpenZeppelin 默认 admin role |
| `OPERATOR_ROLE` | `0x97667070c54ef182b0f5858b034beac1b6f3089aa2d3188bb1e8929f4fa9b929` | `bytes32` role id | Vault Operator role id |
| `GUARDIAN_ROLE` | `0x55435dd261a4b9b3364963f7738a7a662ad9c84396d64be3365284bb7f0a5041` | `bytes32` role id | Vault Guardian role id |
| `PROPOSER_ROLE` | `0xb09aa5aeb3702cfd50b6b62bc4532604938f21248a27a1d5ca736082b6819cc1` | `bytes32` role id | Timelock proposer role id |
| `EXECUTOR_ROLE` | `0xd8aa0f3194971a2a116679f7c2090f6939c8d4e01a2a8d7e41d55e5351469e63` | `bytes32` role id | Timelock executor role id |
| `CANCELLER_ROLE` | `0xfd643c72710c63c0180259aba6b2d05451e3591a24e58b62239378085726f783` | `bytes32` role id | Timelock canceller role id |

## 6. Frontend / Backend Env Mapping

前端公开变量：

```env
NEXT_PUBLIC_CHAIN_ID=84532
NEXT_PUBLIC_ESCROW_VAULT=0x86493b48CBEbd7B25BD89de585FFbEC439A4e453
NEXT_PUBLIC_USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e
NEXT_PUBLIC_ADMIN_SAFE=0xdd5CEfdE7A44a1A242e887dd697455fD81fbF182
NEXT_PUBLIC_GUARDIAN_SAFE=0x9DbB8F6cD251D508015D93D14E08a4CEAac84C40
```

Operator 后端私有变量：

```env
RPC_URL=...
ESCROW_PROXY_ADDRESS=0x86493b48CBEbd7B25BD89de585FFbEC439A4e453
USDC=0x036CbD53842c5426634e7929541eC2318f3dCF7e
OPERATOR_ADDRESS=0x73311B9e68D0e7B80D48444A8b7BFF1F1Fd88776
OPERATOR_PRIVATE_KEY=...
```
