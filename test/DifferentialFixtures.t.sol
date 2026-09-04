// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "../lib/openzeppelin-contracts/lib/forge-std/src/Test.sol";
import "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import "../contracts/EscrowVault.sol";
import "../contracts/SimpleToken.sol";

/// Emits differential-conformance fixtures for the Anchor port.
///
/// Not a test of this contract — it is a GENERATOR. Each case drives the real, audited contract
/// through create -> finalize -> settle and writes the reference's own outputs to
/// `fixtures/differential.json`. The Anchor suite (layer 2) loads that file and asserts identical
/// outputs for identical inputs, which is what makes "matches the reference" a mechanical property
/// rather than a judgement call.
///
/// Regenerate with:
///   forge test --match-contract DifferentialFixtures
contract DifferentialFixturesTest is Test {
    EscrowVault vault;
    SimpleToken token;

    address admin = address(0xA11CE);
    address operator = address(0xB0B);
    address guardian = address(0xCAFE);
    address sponsor = address(0xDEAD);

    uint256 constant TOKEN = 1e6; // 6 decimals, matching USDC and the Anchor fixture mint
    uint256 nonce;

    string out;

    /// Kept as a struct so `_append` has few enough live locals to compile: the flat version hit
    /// Yul's "variable is too deep in the stack" under via_ir.
    struct Row {
        uint256 budget;
        uint256 base_pool;
        uint256 lottery_per_winner;
        uint16 lottery_winner_count;
        uint32 qualified;
        uint256 base_reward;
        uint256 fee_bps;
        uint96 payout;
        uint96 refundable;
        uint96 fee;
        uint16 winners_out;
    }

    function setUp() public {
        token = new SimpleToken("Test Token", "TEST", 1_000_000);
        EscrowVault impl = new EscrowVault();
        bytes memory initData = abi.encodeCall(EscrowVault.initialize, (admin, operator, guardian));
        vault = EscrowVault(address(new ERC1967Proxy(address(impl), initData)));

        vm.prank(admin);
        vault.setTokenWhitelist(address(token), true);
        token.transfer(sponsor, 900_000 * TOKEN);
        vm.prank(sponsor);
        token.approve(address(vault), type(uint256).max);
    }

    function test_emit_differential_fixtures() public {
        out = "[";

        // (budget, basePool, lotteryPerWinner, lotteryWinners, qualified, baseReward)
        // Chosen to exercise: no lottery, lottery with surplus candidates, lottery with fewer
        // candidates than seats (R-G5/R-G7), zero qualified (R-D5), and the R-D4 boundary.
        _case(100, 90, 0, 0, 10, 5);
        _case(100, 90, 0, 0, 10, 9); // R-D4 boundary: base_reward * qualified == base_pool
        _case(100, 90, 0, 0, 0, 0); // R-D5: zero qualified
        _case(100, 0, 40, 2, 5, 0); // lottery, more candidates than seats
        _case(100, 0, 40, 2, 1, 0); // R-G5: fewer candidates than seats
        _case(100, 50, 20, 2, 5, 10);
        _case(1000, 800, 0, 0, 40, 20);
        _case(7, 5, 0, 0, 3, 1); // small, non-round numbers to catch rounding

        out = string.concat(out, "\n]\n");
        // Emitted to stdout rather than written with vm.writeFile: writing would need an
        // fs_permissions entry in this repo's foundry.toml, and the generator should not require
        // changing the reference's build config. The caller captures it — see
        // solo-escrow-solana/scripts/gen-fixtures.sh.
        console.log("---FIXTURES-BEGIN---");
        console.log(out);
        console.log("---FIXTURES-END---");
    }

    function _case(
        uint256 budgetU,
        uint256 baseU,
        uint256 lotPerU,
        uint16 winners,
        uint32 qualified,
        uint256 baseRewardU
    ) internal {
        uint256 budget = budgetU * TOKEN;
        uint256 basePool = baseU * TOKEN;
        uint256 lotPer = lotPerU * TOKEN;
        uint256 baseReward = baseRewardU * TOKEN;

        bytes32 seed = bytes32(uint256(0x2a)); // 42, matching the Anchor fixture's seed_reveal
        bytes32 taskId = keccak256(abi.encodePacked("fx", nonce++, budget, block.timestamp));

        vm.prank(sponsor);
        vault.createTask(
            taskId,
            address(token),
            uint96(budget),
            uint96(basePool),
            uint96(lotPer),
            winners,
            uint64(block.timestamp + 1 days),
            uint64(block.timestamp + 3 days),
            keccak256(abi.encodePacked(seed))
        );

        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, qualified, keccak256("cohort"), keccak256("manifest"));

        // The reference derives entropy from a future blockhash; roll past it so settle can read it.
        vm.roll(block.number + 20);

        uint16 expectedWinners = qualified >= winners ? winners : uint16(qualified);
        uint256 payout = baseReward * qualified + lotPer * expectedWinners;
        uint256 feeBps = vault.platformFeeBps();
        uint256 fee = (budget * feeBps) / 10_000;
        uint256 refundable = budget - payout - fee;

        vm.prank(operator);
        vault.settleTask(
            taskId,
            seed,
            keccak256("result"),
            uint96(payout),
            uint96(refundable),
            uint96(baseReward)
        );

        // `settlements` is a public mapping, so Solidity generates a tuple getter in struct-field
        // order: seedReveal, entropyRef, entropyValue, resultManifestHash, baseRewardPerQualified,
        // actualWinnerCount, payoutAmount, refundableAmount, settledAt, platformFeeAmount.
        (
            ,
            ,
            ,
            ,
            ,
            uint16 gotWinners,
            uint96 gotPayout,
            uint96 gotRefundable,
            ,
            uint96 gotFee
        ) = vault.settlements(taskId);
        assertEq(uint256(gotFee), fee, "generator: fee drifted from the contract");

        _append(
            Row({
                budget: budget,
                base_pool: basePool,
                lottery_per_winner: lotPer,
                lottery_winner_count: winners,
                qualified: qualified,
                base_reward: baseReward,
                fee_bps: feeBps,
                payout: gotPayout,
                refundable: gotRefundable,
                fee: gotFee,
                winners_out: gotWinners
            })
        );
    }

    function _append(Row memory r) internal {
        string memory a = string.concat(
            nonce == 1 ? "\n  {" : ",\n  {",
            '"budget":', vm.toString(r.budget),
            ',"base_pool":', vm.toString(r.base_pool),
            ',"lottery_per_winner":', vm.toString(r.lottery_per_winner)
        );
        string memory b = string.concat(
            ',"lottery_winner_count":', vm.toString(uint256(r.lottery_winner_count)),
            ',"qualified":', vm.toString(uint256(r.qualified)),
            ',"base_reward":', vm.toString(r.base_reward),
            ',"fee_bps":', vm.toString(r.fee_bps)
        );
        string memory c = string.concat(
            ',"expect_payout":', vm.toString(uint256(r.payout)),
            ',"expect_refundable":', vm.toString(uint256(r.refundable)),
            ',"expect_fee":', vm.toString(uint256(r.fee)),
            ',"expect_winners":', vm.toString(uint256(r.winners_out)),
            "}"
        );
        out = string.concat(out, a, b, c);
    }
}
