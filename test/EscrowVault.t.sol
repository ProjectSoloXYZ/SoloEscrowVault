// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../lib/openzeppelin-contracts/lib/forge-std/src/Test.sol";
import "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";

import "../contracts/EscrowVault.sol";
import "../contracts/SimpleToken.sol";

contract EscrowVaultV2 is EscrowVault {
    function version() external pure returns (string memory) {
        return "v2";
    }
}

contract FeeOnTransferToken is SimpleToken {
    constructor() SimpleToken("Fee Token", "FEE", 1_000_000) {}

    function transfer(address to, uint256 value) public override returns (bool) {
        uint256 received = value - (value / 50);
        require(balanceOf[msg.sender] >= value, "Insufficient balance");
        balanceOf[msg.sender] -= value;
        balanceOf[to] += received;
        totalSupply -= value - received;
        emit Transfer(msg.sender, to, received);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) public override returns (bool) {
        uint256 received = value - (value / 50);
        require(balanceOf[from] >= value, "Insufficient balance");
        require(allowance[from][msg.sender] >= value, "Insufficient allowance");
        balanceOf[from] -= value;
        balanceOf[to] += received;
        allowance[from][msg.sender] -= value;
        totalSupply -= value - received;
        emit Transfer(from, to, received);
        return true;
    }
}

contract EscrowVaultTest is Test {
    EscrowVault internal vault;
    SimpleToken internal token;

    address internal admin = address(0xA11CE);
    address internal operator = address(0x0A0A);
    address internal guardian = address(0xB0B);
    address internal sponsor = address(0xC0DE);
    address internal user1 = address(0x1111);
    address internal user2 = address(0x2222);
    address internal attacker = address(0xBAD);

    uint256 internal constant TOKEN = 1e18;
    uint256 internal constant DEFAULT_FEE = 2 * TOKEN;
    uint256 internal nextTaskNonce;

    function setUp() public {
        token = new SimpleToken("Test Token", "TEST", 1_000_000);

        EscrowVault implementation = new EscrowVault();
        bytes memory initData = abi.encodeCall(EscrowVault.initialize, (admin, operator, guardian));
        ERC1967Proxy proxy = new ERC1967Proxy(address(implementation), initData);
        vault = EscrowVault(address(proxy));

        vm.prank(admin);
        vault.setTokenWhitelist(address(token), true);

        token.transfer(sponsor, 10_000 * TOKEN);

        vm.prank(sponsor);
        token.approve(address(vault), type(uint256).max);

        assertEq(vault.platformFeeBps(), vault.DEFAULT_PLATFORM_FEE_BPS());
        assertEq(vault.platformTreasury(), admin);
    }

    function testCreateTaskLocksFundsAndStoresConfig() public {
        bytes32 taskId = _createTask(100 * TOKEN, 78 * TOKEN, 10 * TOKEN, 2);

        (
            bytes32 storedTaskId,
            address storedSponsor,
            address storedToken,
            uint96 totalBudget,
            uint96 basePool,
            uint96 lotteryRewardPerWinner,
            uint16 lotteryWinnerCount,
            ,
            ,
            ,
            EscrowVault.TaskStatus status,
            uint16 taskPlatformFeeBps
        ) = vault.tasks(taskId);

        assertEq(storedTaskId, taskId);
        assertEq(storedSponsor, sponsor);
        assertEq(storedToken, address(token));
        assertEq(totalBudget, 100 * TOKEN);
        assertEq(basePool, 78 * TOKEN);
        assertEq(lotteryRewardPerWinner, 10 * TOKEN);
        assertEq(lotteryWinnerCount, 2);
        assertEq(uint256(status), uint256(EscrowVault.TaskStatus.FUNDED));
        assertEq(taskPlatformFeeBps, 200);
        assertEq(token.balanceOf(address(vault)), 100 * TOKEN);
    }

    function testCreateTaskRejectsInvalidInputs() public {
        bytes32 taskId = keccak256("invalid-task");
        bytes32 seedCommit = keccak256(abi.encodePacked(bytes32("seed")));

        vm.prank(sponsor);
        vm.expectRevert(bytes("Token not allowed"));
        vault.createTask(
            taskId,
            address(0x1234),
            uint96(1 * TOKEN),
            uint96(1 * TOKEN),
            0,
            0,
            uint64(block.timestamp + 1 days),
            uint64(block.timestamp + 2 days),
            seedCommit
        );

        vm.prank(sponsor);
        vm.expectRevert(bytes("Empty seedCommit"));
        vault.createTask(
            taskId,
            address(token),
            uint96(1 * TOKEN),
            uint96(1 * TOKEN),
            0,
            0,
            uint64(block.timestamp + 1 days),
            uint64(block.timestamp + 2 days),
            bytes32(0)
        );

        vm.prank(sponsor);
        vm.expectRevert(bytes("Budget params invalid"));
        vault.createTask(
            taskId,
            address(token),
            uint96(1 * TOKEN),
            uint96(1 * TOKEN),
            uint96(1),
            1,
            uint64(block.timestamp + 1 days),
            uint64(block.timestamp + 2 days),
            seedCommit
        );

        vm.prank(sponsor);
        vm.expectRevert(bytes("Budget params invalid"));
        vault.createTask(
            keccak256("no-room-for-fee"),
            address(token),
            uint96(100 * TOKEN),
            uint96(100 * TOKEN),
            0,
            0,
            uint64(block.timestamp + 1 days),
            uint64(block.timestamp + 2 days),
            seedCommit
        );
    }

    function testCreateTaskRejectsFeeOnTransferTokens() public {
        bytes32 seedCommit = keccak256(abi.encodePacked(bytes32("seed")));
        FeeOnTransferToken feeToken = new FeeOnTransferToken();

        vm.prank(admin);
        vault.setTokenWhitelist(address(feeToken), true);

        feeToken.transfer(sponsor, 1_000 * TOKEN);

        vm.prank(sponsor);
        feeToken.approve(address(vault), type(uint256).max);

        vm.prank(sponsor);
        vm.expectRevert(bytes("Bad token transfer"));
        vault.createTask(
            keccak256("fee-on-transfer-task"),
            address(feeToken),
            uint96(100 * TOKEN),
            uint96(98 * TOKEN),
            0,
            0,
            uint64(block.timestamp + 1 days),
            uint64(block.timestamp + 2 days),
            seedCommit
        );
    }

    function testCancelTaskRefundsSponsorBeforeQualification() public {
        bytes32 taskId = _createTask(100 * TOKEN, 98 * TOKEN, 0, 0);

        uint256 beforeBalance = token.balanceOf(sponsor);
        vm.prank(sponsor);
        vault.cancelTask(taskId);

        assertEq(token.balanceOf(sponsor) - beforeBalance, 100 * TOKEN);
        assertEq(token.balanceOf(address(vault)), 0);
        assertEq(uint256(_taskStatus(taskId)), uint256(EscrowVault.TaskStatus.CANCELLED));
    }

    function testOnlySponsorCanCancelOrRefund() public {
        bytes32 taskId = _createTask(100 * TOKEN, 98 * TOKEN, 0, 0);

        vm.prank(attacker);
        vm.expectRevert(bytes("Only sponsor"));
        vault.cancelTask(taskId);
    }

    function testFinalizeQualificationRequiresOperatorDeadlineAndRoot() public {
        bytes32 taskId = _createTask(100 * TOKEN, 98 * TOKEN, 0, 0);
        bytes32 manifest = keccak256("qualification-manifest");

        vm.prank(attacker);
        vm.expectRevert();
        vault.finalizeQualification(taskId, 0, bytes32(0), manifest);

        vm.prank(operator);
        vm.expectRevert(bytes("Qualification too early"));
        vault.finalizeQualification(taskId, 0, bytes32(0), manifest);

        vm.warp(block.timestamp + 1 days + 1);

        vm.prank(operator);
        vm.expectRevert(bytes("Empty qualifiedRoot"));
        vault.finalizeQualification(taskId, 1, bytes32(0), manifest);

        vm.prank(operator);
        vault.finalizeQualification(taskId, 0, bytes32(0), manifest);

        assertEq(uint256(_taskStatus(taskId)), uint256(EscrowVault.TaskStatus.QUALIFIED));
    }

    function testSettleTaskValidatesCommitRevealAndAmounts() public {
        bytes32 seed = bytes32("seed-a");
        bytes32 taskId = _createTaskWithSeed(100 * TOKEN, 98 * TOKEN, 0, 0, seed);
        _finalizeZeroQualified(taskId);

        vm.prank(operator);
        vm.expectRevert(bytes("seedReveal mismatch"));
        vault.settleTask(
            taskId,
            bytes32("wrong-seed"),
            0,
            bytes32(0),
            keccak256("result"),
            0,
            uint96(100 * TOKEN),
            0,
            0
        );

        vm.prank(operator);
        vm.expectRevert(bytes("Sum mismatch"));
        vault.settleTask(taskId, seed, 0, bytes32(0), keccak256("result"), 1, uint96(100 * TOKEN), 0, 0);

        vm.prank(operator);
        vault.settleTask(taskId, seed, 0, bytes32(0), keccak256("result"), 0, uint96(98 * TOKEN), 0, 0);

        assertEq(uint256(_taskStatus(taskId)), uint256(EscrowVault.TaskStatus.REFUNDABLE));
        assertEq(vault.platformFeeBalances(address(token)), DEFAULT_FEE);
    }

    function testSettleTaskRejectsInvalidWinnerMath() public {
        bytes32 taskId = _createTask(100 * TOKEN, 78 * TOKEN, 10 * TOKEN, 1);
        bytes32 qualifiedRoot = _qualifiedRoot(taskId);

        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 2, qualifiedRoot, keccak256("qualification-manifest"));

        vm.prank(operator);
        vm.expectRevert(bytes("Too many winners"));
        vault.settleTask(
            taskId,
            bytes32("seed"),
            0,
            bytes32(0),
            keccak256("result"),
            uint96(98 * TOKEN),
            0,
            uint96(40 * TOKEN),
            2
        );
    }

    function testSettleTaskRejectsPayoutLargerThanRewardFormula() public {
        bytes32 taskId = _createTask(100 * TOKEN, 78 * TOKEN, 10 * TOKEN, 2);
        bytes32 qualifiedRoot = _qualifiedRoot(taskId);

        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 2, qualifiedRoot, keccak256("qualification-manifest"));

        vm.prank(operator);
        vm.expectRevert(bytes("Payout mismatch"));
        vault.settleTask(
            taskId,
            bytes32("seed"),
            0,
            bytes32(0),
            keccak256("result"),
            uint96(90 * TOKEN),
            uint96(8 * TOKEN),
            uint96(30 * TOKEN),
            1
        );
    }

    function testFullSettlementRootActivationClaimsAndRefund() public {
        bytes32 taskId = _createTask(100 * TOKEN, 78 * TOKEN, 10 * TOKEN, 2);
        bytes32 qualifiedRoot = _qualifiedRoot(taskId);

        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 2, qualifiedRoot, keccak256("qualification-manifest"));

        vm.prank(operator);
        vault.settleTask(
            taskId,
            bytes32("seed"),
            0,
            bytes32(0),
            keccak256("result"),
            uint96(88 * TOKEN),
            uint96(10 * TOKEN),
            uint96(39 * TOKEN),
            1
        );

        assertEq(vault.settledButUnallocated(address(token)), 88 * TOKEN);
        assertEq(vault.platformFeeBalances(address(token)), DEFAULT_FEE);
        assertEq(uint256(_taskStatus(taskId)), uint256(EscrowVault.TaskStatus.REFUNDABLE));

        vm.prank(sponsor);
        vault.claimRefund(taskId, sponsor);
        assertEq(uint256(_taskStatus(taskId)), uint256(EscrowVault.TaskStatus.REFUNDED));

        uint64 rootId = 1;
        uint128 user1Cumulative = uint128(49 * TOKEN);
        uint128 user2Cumulative = uint128(39 * TOKEN);
        bytes32 leaf1 = _claimLeaf(user1, rootId, user1Cumulative);
        bytes32 leaf2 = _claimLeaf(user2, rootId, user2Cumulative);
        bytes32 merkleRoot = _hashPair(leaf1, leaf2);

        vm.prank(operator);
        vault.publishPendingRoot(address(token), rootId, merkleRoot, uint128(88 * TOKEN), 1 hours, keccak256("root"));

        assertEq(vault.settledButUnallocated(address(token)), 0);

        vm.expectRevert(bytes("Audit window active"));
        vault.activateRoot(address(token), rootId);

        vm.warp(block.timestamp + 1 hours);
        vault.activateRoot(address(token), rootId);

        bytes32[] memory proofForUser1 = new bytes32[](1);
        proofForUser1[0] = leaf2;
        vm.prank(user1);
        vault.claim(address(token), rootId, user1Cumulative, proofForUser1, user1);
        assertEq(token.balanceOf(user1), 49 * TOKEN);
        assertEq(vault.claimed(user1, address(token)), 49 * TOKEN);

        vm.prank(user1);
        vm.expectRevert(bytes("Nothing to claim"));
        vault.claim(address(token), rootId, user1Cumulative, proofForUser1, user1);

        bytes32[] memory proofForUser2 = new bytes32[](1);
        proofForUser2[0] = leaf1;
        vm.prank(user2);
        vault.claim(address(token), rootId, user2Cumulative, proofForUser2, user2);
        assertEq(token.balanceOf(user2), 39 * TOKEN);

        assertEq(token.balanceOf(address(vault)), DEFAULT_FEE);
    }

    function testDefaultPlatformFeeCanBeWithdrawn() public {
        bytes32 taskId = _createTask(100 * TOKEN, 98 * TOKEN, 0, 0);
        _finalizeZeroQualified(taskId);

        vm.prank(operator);
        vault.settleTask(taskId, bytes32("seed"), 0, bytes32(0), keccak256("result"), 0, uint96(98 * TOKEN), 0, 0);

        assertEq(vault.platformFeeBalances(address(token)), DEFAULT_FEE);

        vm.prank(attacker);
        vm.expectRevert();
        vault.withdrawPlatformFees(address(token), attacker, DEFAULT_FEE);

        vm.prank(admin);
        vm.expectRevert(bytes("insufficient fee balance"));
        vault.withdrawPlatformFees(address(token), admin, DEFAULT_FEE + 1);

        uint256 beforeBalance = token.balanceOf(admin);
        vm.prank(admin);
        vault.withdrawPlatformFees(address(token), admin, DEFAULT_FEE);

        assertEq(token.balanceOf(admin) - beforeBalance, DEFAULT_FEE);
        assertEq(vault.platformFeeBalances(address(token)), 0);
    }

    function testPlatformFeeCanBeSetToZeroForNewTasksOnly() public {
        bytes32 oldFeeTaskId = _createTask(100 * TOKEN, 98 * TOKEN, 0, 0);

        vm.prank(admin);
        vault.setPlatformFeeBps(0);

        bytes32 zeroFeeTaskId = _createTask(100 * TOKEN, 100 * TOKEN, 0, 0);

        _finalizeZeroQualified(oldFeeTaskId);
        vm.prank(operator);
        vault.settleTask(oldFeeTaskId, bytes32("seed"), 0, bytes32(0), keccak256("old-result"), 0, uint96(98 * TOKEN), 0, 0);

        _finalizeZeroQualified(zeroFeeTaskId);
        vm.prank(operator);
        vault.settleTask(zeroFeeTaskId, bytes32("seed"), 0, bytes32(0), keccak256("zero-result"), 0, uint96(100 * TOKEN), 0, 0);

        assertEq(vault.platformFeeBalances(address(token)), DEFAULT_FEE);
    }

    function testPlatformFeeRateBoundsAndTreasuryManagement() public {
        vm.prank(attacker);
        vm.expectRevert();
        vault.setPlatformFeeBps(0);

        vm.prank(admin);
        vault.setPlatformFeeBps(1_000);
        assertEq(vault.platformFeeBps(), 1_000);

        vm.prank(admin);
        vm.expectRevert(bytes("fee too high"));
        vault.setPlatformFeeBps(1_001);

        vm.prank(attacker);
        vm.expectRevert();
        vault.setPlatformTreasury(attacker);

        vm.prank(admin);
        vm.expectRevert(bytes("bad treasury"));
        vault.setPlatformTreasury(address(0));

        vm.prank(admin);
        vault.setPlatformTreasury(attacker);
        assertEq(vault.platformTreasury(), attacker);
    }

    function testClaimRejectsInvalidProofAndRootMismatch() public {
        _publishAndActivateSingleLeafRoot(user1, 1, uint128(10 * TOKEN), uint128(10 * TOKEN));

        bytes32[] memory emptyProof = new bytes32[](0);

        vm.prank(user1);
        vm.expectRevert(bytes("Root mismatch"));
        vault.claim(address(token), 2, uint128(10 * TOKEN), emptyProof, user1);

        vm.prank(user2);
        vm.expectRevert(bytes("Invalid proof"));
        vault.claim(address(token), 1, uint128(10 * TOKEN), emptyProof, user2);
    }

    function testPublishPendingRootRequiresOperatorBudgetAndSinglePending() public {
        _settleOneWeiTask();

        vm.prank(attacker);
        vm.expectRevert();
        vault.publishPendingRoot(address(token), 1, keccak256("root"), 1, 1 hours, keccak256("manifest"));

        vm.prank(operator);
        vm.expectRevert(bytes("Delta exceeds unallocated"));
        vault.publishPendingRoot(address(token), 1, keccak256("root"), 2, 1 hours, keccak256("manifest"));

        vm.prank(operator);
        vault.publishPendingRoot(address(token), 1, keccak256("root"), 1, 1 hours, keccak256("manifest"));

        vm.prank(operator);
        vm.expectRevert(bytes("Pending root exists"));
        vault.publishPendingRoot(address(token), 2, keccak256("root2"), 1, 1 hours, keccak256("manifest2"));
    }

    function testGuardianCanCancelPendingRootAndRestoreUnallocated() public {
        _settleOneWeiTask();

        vm.prank(operator);
        vault.publishPendingRoot(address(token), 1, keccak256("root"), 1, 1 hours, keccak256("manifest"));

        assertEq(vault.settledButUnallocated(address(token)), 0);

        vm.prank(attacker);
        vm.expectRevert();
        vault.cancelPendingRoot(address(token), 1);

        vm.prank(guardian);
        vault.cancelPendingRoot(address(token), 1);

        assertEq(vault.settledButUnallocated(address(token)), 1);
        (uint64 pendingRootId,,,,,,) = vault.pendingRoots(address(token));
        assertEq(pendingRootId, 0);
    }

    function testEmergencyRefundAfterSettlementDeadline() public {
        bytes32 taskId = _createTask(100 * TOKEN, 98 * TOKEN, 0, 0);

        vm.prank(sponsor);
        vm.expectRevert(bytes("Settlement deadline not passed"));
        vault.emergencyRefund(taskId);

        vm.warp(block.timestamp + 2 days + 1);

        uint256 beforeBalance = token.balanceOf(sponsor);
        vm.prank(sponsor);
        vault.emergencyRefund(taskId);

        assertEq(token.balanceOf(sponsor) - beforeBalance, 100 * TOKEN);
        assertEq(uint256(_taskStatus(taskId)), uint256(EscrowVault.TaskStatus.CANCELLED));
    }

    function testPauseBlocksBusinessFlowsUntilAdminUnpauses() public {
        vm.prank(guardian);
        vault.pause();

        vm.prank(sponsor);
        vm.expectRevert();
        vault.createTask(
            keccak256("paused"),
            address(token),
            uint96(100 * TOKEN),
            uint96(100 * TOKEN),
            0,
            0,
            uint64(block.timestamp + 1 days),
            uint64(block.timestamp + 2 days),
            keccak256(abi.encodePacked(bytes32("seed")))
        );

        vm.prank(attacker);
        vm.expectRevert();
        vault.unpause();

        vm.prank(admin);
        vault.unpause();

        _createTask(100 * TOKEN, 98 * TOKEN, 0, 0);
    }

    function testAdminCanUpdateOperatorGuardianAndWhitelist() public {
        address newOperator = address(0x0B0B);
        address newGuardian = address(0x0C0C);

        vm.prank(admin);
        vault.updateOperator(operator, newOperator);
        assertFalse(vault.hasRole(vault.OPERATOR_ROLE(), operator));
        assertTrue(vault.hasRole(vault.OPERATOR_ROLE(), newOperator));

        vm.prank(admin);
        vault.updateGuardian(guardian, newGuardian);
        assertFalse(vault.hasRole(vault.GUARDIAN_ROLE(), guardian));
        assertTrue(vault.hasRole(vault.GUARDIAN_ROLE(), newGuardian));

        vm.prank(admin);
        vault.setTokenWhitelist(address(token), false);

        vm.prank(sponsor);
        vm.expectRevert(bytes("Token not allowed"));
        vault.createTask(
            keccak256("not-whitelisted"),
            address(token),
            uint96(100 * TOKEN),
            uint96(100 * TOKEN),
            0,
            0,
            uint64(block.timestamp + 1 days),
            uint64(block.timestamp + 2 days),
            keccak256(abi.encodePacked(bytes32("seed")))
        );
    }

    function testUUPSUpgradeRequiresAdminAndPreservesState() public {
        bytes32 taskId = _createTask(100 * TOKEN, 98 * TOKEN, 0, 0);
        EscrowVaultV2 v2 = new EscrowVaultV2();

        vm.prank(attacker);
        vm.expectRevert();
        vault.upgradeToAndCall(address(v2), "");

        vm.prank(admin);
        vault.upgradeToAndCall(address(v2), "");

        assertEq(EscrowVaultV2(address(vault)).version(), "v2");
        assertEq(uint256(_taskStatus(taskId)), uint256(EscrowVault.TaskStatus.FUNDED));
        assertTrue(vault.hasRole(vault.OPERATOR_ROLE(), operator));

        vm.prank(attacker);
        vm.expectRevert();
        vault.initializeV2(attacker);

        vm.prank(admin);
        vault.initializeV2(sponsor);
        assertEq(vault.platformTreasury(), sponsor);
        assertEq(uint256(_taskStatus(taskId)), uint256(EscrowVault.TaskStatus.FUNDED));
    }

    function testClaimableDeltaUsesAccountThenTokenMappingOrder() public {
        _publishAndActivateSingleLeafRoot(user1, 1, uint128(10 * TOKEN), uint128(10 * TOKEN));

        assertEq(vault.claimableDelta(user1, address(token), uint128(10 * TOKEN)), 10 * TOKEN);

        bytes32[] memory emptyProof = new bytes32[](0);
        vm.prank(user1);
        vault.claim(address(token), 1, uint128(10 * TOKEN), emptyProof, user1);

        assertEq(vault.claimableDelta(user1, address(token), uint128(10 * TOKEN)), 0);
        assertEq(vault.claimed(user1, address(token)), 10 * TOKEN);
    }

    function _createTask(
        uint256 totalBudget,
        uint256 basePool,
        uint256 lotteryRewardPerWinner,
        uint16 lotteryWinnerCount
    ) internal returns (bytes32 taskId) {
        return _createTaskWithSeed(totalBudget, basePool, lotteryRewardPerWinner, lotteryWinnerCount, bytes32("seed"));
    }

    function _createTaskWithSeed(
        uint256 totalBudget,
        uint256 basePool,
        uint256 lotteryRewardPerWinner,
        uint16 lotteryWinnerCount,
        bytes32 seed
    ) internal returns (bytes32 taskId) {
        taskId = keccak256(abi.encodePacked(
            "task",
            nextTaskNonce++,
            totalBudget,
            basePool,
            lotteryRewardPerWinner,
            lotteryWinnerCount,
            seed,
            block.timestamp
        ));

        vm.prank(sponsor);
        vault.createTask(
            taskId,
            address(token),
            uint96(totalBudget),
            uint96(basePool),
            uint96(lotteryRewardPerWinner),
            lotteryWinnerCount,
            uint64(block.timestamp + 1 days),
            uint64(block.timestamp + 2 days),
            keccak256(abi.encodePacked(seed))
        );
    }

    function _finalizeZeroQualified(bytes32 taskId) internal {
        (,,,,,,, uint64 qualifyDeadline,,,,) = vault.tasks(taskId);
        if (block.timestamp < qualifyDeadline) {
            vm.warp(uint256(qualifyDeadline) + 1);
        }
        vm.prank(operator);
        vault.finalizeQualification(taskId, 0, bytes32(0), keccak256("qualification-manifest"));
    }

    function _settleOneWeiTask() internal returns (bytes32 taskId) {
        taskId = _createTask(1 * TOKEN, 1, 0, 0);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 1, keccak256("qualified-root"), keccak256("qualification-manifest"));

        vm.prank(operator);
        uint96 feeAmount = uint96((1 * TOKEN * 200) / 10_000);
        vault.settleTask(taskId, bytes32("seed"), 0, bytes32(0), keccak256("result"), 1, uint96(1 * TOKEN - feeAmount - 1), 1, 0);
    }

    function _publishAndActivateSingleLeafRoot(
        address account,
        uint64 rootId,
        uint128 cumulativeAmount,
        uint128 epochDelta
    ) internal {
        _settlePayout(epochDelta);

        vm.prank(operator);
        vault.publishPendingRoot(address(token), rootId, _claimLeaf(account, rootId, cumulativeAmount), epochDelta, 0, keccak256("root"));

        vault.activateRoot(address(token), rootId);
    }

    function _settlePayout(uint128 payoutAmount) internal returns (bytes32 taskId) {
        taskId = _createTask(100 * TOKEN, 98 * TOKEN, 0, 0);
        vm.warp(block.timestamp + 1 days + 1);

        vm.prank(operator);
        vault.finalizeQualification(taskId, 1, keccak256("qualified-root"), keccak256("qualification-manifest"));

        vm.prank(operator);
        vault.settleTask(
            taskId,
            bytes32("seed"),
            0,
            bytes32(0),
            keccak256("result"),
            uint96(payoutAmount),
            uint96(100 * TOKEN - DEFAULT_FEE - payoutAmount),
            uint96(payoutAmount),
            0
        );
    }

    function _taskStatus(bytes32 taskId) internal view returns (EscrowVault.TaskStatus status) {
        (,,,,,,,,,, status,) = vault.tasks(taskId);
    }

    function _qualifiedRoot(bytes32 taskId) internal view returns (bytes32) {
        bytes32 leaf1 = keccak256(abi.encodePacked(taskId, user1));
        bytes32 leaf2 = keccak256(abi.encodePacked(taskId, user2));
        return _hashPair(leaf1, leaf2);
    }

    function _claimLeaf(address account, uint64 rootId, uint128 cumulativeAmount) internal view returns (bytes32) {
        return keccak256(bytes.concat(keccak256(abi.encode(account, address(token), rootId, cumulativeAmount))));
    }

    function _hashPair(bytes32 a, bytes32 b) internal pure returns (bytes32) {
        return a < b ? keccak256(abi.encodePacked(a, b)) : keccak256(abi.encodePacked(b, a));
    }
}
