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

## Audit Remediation Notes (CertiK Preliminary, 2026-06)

### Cumulative root model (SOL-09)

The vault publishes **cumulative** Merkle roots per token rather than per-task roots, which is required for sub-USDC payouts where per-task claim gas would exceed the reward itself.

Implications for users and off-chain root generation:

- Only the **latest active root** is valid for `claim`. Once a new root is activated, proofs derived from any previous root become invalid; users must obtain a fresh proof against the latest `(rootId, merkleRoot)`.
- Every new root **MUST** include the latest cumulative amount for every user who still has unclaimed balance from any previous root. Dropping a user from a new root permanently locks their remaining entitlement.
- This is an **off-chain invariant** enforced by the root-generator pipeline and its unit tests. The on-chain aggregate-solvency check (SOL-04) only prevents over-payment; it cannot detect under-payment caused by dropping users.

### Aggregate solvency (SOL-04)

`claim` enforces `totalClaimed[token] + delta <= activeRoots[token].totalAllocated`. The contract can never pay out more than has been settled, regardless of the contents of any individual root. This blocks multi-user collusion where each user is granted the full `totalAllocated`.

### Minimum review window (SOL-05)

`publishPendingRoot` enforces `minReviewWindow <= delayWindow <= MAX_DELAY_WINDOW`, where `minReviewWindow` defaults to 24h and the floor (`MIN_REVIEW_FLOOR`) is 1h. The window guarantees Guardian has time to verify the manifest and cancel via `cancelPendingRoot` before activation. `activateAfter` is computed in `uint256` and bounds-checked before being cast to `uint64`, eliminating the truncation-bypass path.

### Deterministic lottery + entropy hardening (SOL-06)

- After `finalizeQualification`, the contract records `taskEntropyBlock[taskId] = block.number + ENTROPY_BLOCK_DELAY` (K=10). The qualified list is locked before the entropy source is known, removing the operator's ability to grind the qualified set.
- `settleTask` derives the final entropy as `keccak256(seedReveal, blockhash(taskEntropyBlock))`. Operator no longer provides an `entropyValue` parameter.
- `actualWinnerCount` is computed deterministically as `min(qualifiedCount, lotteryWinnerCount)`; operator cannot inflate or deflate the winner count.
- Winners are selected off-chain by a published deterministic algorithm (Fisher–Yates / rejection sampling) seeded by `taskEntropy[taskId]`. Anyone, including Guardian, can independently recompute the reward root from the on-chain entropy and the public algorithm, and challenge a mismatch within the review window via `cancelPendingRoot`.

#### Operational SOP for SOL-06

- The seed **MUST** be revealed only after the entropy block has been mined; revealing earlier still works mechanically but defeats the unpredictability.
- Settlement **MUST** be completed within 256 blocks (~13 minutes on BSC) of the entropy block, otherwise `blockhash` returns zero and `settleTask` reverts with `"entropy block expired"`. The off-chain pipeline must alert operators well before this deadline.

### Delisted token wind-down (SOL-10)

If a token is removed from the whitelist after settlement but before its `settledButUnallocated` balance is fully distributed, `publishPendingRoot` still accepts roots for that token (`tokenWhitelist[token] || settledButUnallocated[token] > 0`). New tasks remain blocked (`createTask` still requires whitelisted tokens). This avoids introducing an admin-controlled emergency withdraw path that would itself be a centralization risk.

### SimpleToken scope (SOL-07)

`contracts/SimpleToken.sol` is a test-only ERC20 mock and is **out of scope for production**. Production deployments use audited stablecoins (USDT/USDC). The mock has a defensive zero-address check in `transfer` / `transferFrom`; this is belt-and-suspenders, not a guarantee of full ERC20 compliance.

### Role rotation (SOL-08)

`updateOperator` and `updateGuardian` revert if the supplied `oldX` does not currently hold the corresponding role. This prevents a silent no-op where a typo in the old address leaves the real holder in place while the new address is granted, resulting in two simultaneous role holders.
