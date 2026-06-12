# Security Model

EscrowVault is a semi-centralized escrow and payout system. It keeps custody and payout accounting on-chain, while qualification, lottery selection, and Merkle allocation are computed off-chain by trusted operators.

## Roles

### Admin

`DEFAULT_ADMIN_ROLE` is the highest-privilege role.

Admin can:

- Upgrade the UUPS implementation.
- Update the global platform fee rate.
- Update `platformTreasury`.
- Withdraw accrued platform fees.
- Manage token whitelist entries.
- Rotate Operator and Guardian roles.
- Unpause the system.

Admin key compromise is critical because it can change protocol logic through upgrades and redirect privileged configuration.

### Operator

`OPERATOR_ROLE` is the main semi-centralized trust point.

Operator can:

- Finalize qualification results.
- Submit task settlement data.
- Publish pending cumulative Merkle roots.

The contract enforces budget conservation, platform fee accounting, and payout formula checks, but it does not independently verify the off-chain qualification list or Merkle distribution correctness. Guardian review is therefore part of the safety model.

### Guardian

`GUARDIAN_ROLE` is the operational safety role.

Guardian can:

- Pause the system.
- Cancel a suspicious pending root before it is activated.

Guardian should actively monitor pending roots, settlement manifests, and payout deltas during the audit window. If Guardian is offline while Operator publishes a bad root, users may be exposed once that root becomes active.

### Sponsor

Sponsor creates tasks and deposits `totalBudget`.

Sponsor can:

- Cancel a task while it is still `FUNDED`.
- Claim `refundableAmount` after a refundable settlement.
- Call `emergencyRefund` after `settlementDeadline` if the task is still `FUNDED` or `QUALIFIED`.

Successful settlement charges the task's locked platform fee. `cancelTask` and `emergencyRefund` refund the full budget and do not accrue platform fees.

### Users

Users claim rewards from active cumulative Merkle roots.

Claim safety depends on:

- The active Merkle root being correct.
- The claim leaf matching `(msg.sender, token, rootId, cumulativeAmount)`.
- Guardian having enough time and context to cancel suspicious pending roots before activation.

The contract prevents duplicate claims by recording `claimed[account][token]` as cumulative lifetime claimed amount.

## Token Assumptions

Whitelisted tokens must be standard ERC20 tokens where the amount transferred equals the amount received by the vault.

Unsupported token types include:

- fee-on-transfer tokens
- burn-on-transfer tokens
- rebasing tokens
- tokens whose vault balance can change without an explicit vault accounting action

`createTask` checks the vault balance before and after `safeTransferFrom` and reverts if the received amount differs from `totalBudget`.

## Known Operational Assumptions

- Admin and Guardian should be controlled by separate operational keys.
- Guardian must monitor pending roots during the audit window.
- Operator should publish result manifests that are externally auditable.
- Production deployments should use a nonzero minimum root audit window policy.
- All upgrades should be reviewed for storage layout compatibility before execution.
