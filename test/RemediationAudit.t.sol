// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../lib/openzeppelin-contracts/lib/forge-std/src/Test.sol";
import "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";

import "../contracts/EscrowVault.sol";
import "../contracts/SimpleToken.sol";

/**
 * @title RemediationAuditTest
 * @notice 审计整改专项验收用例（对照 remediation/ 文档逐条覆盖）
 *         - SOL-04 聚合守恒
 *         - SOL-05 最小审查窗口 + 防截断 + 上限
 *         - SOL-06 熵硬化 + 确定性中奖人数
 *         - SOL-07 SimpleToken 零地址
 *         - SOL-08 角色轮换旧地址校验
 *         - SOL-10 下架 token 存量清退
 */
contract RemediationAuditTest is Test {
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
    uint256 internal constant DEFAULT_FEE = 2 * TOKEN; // 200bps * 100 TOKEN
    uint256 internal nextTaskNonce;

    function setUp() public {
        token = new SimpleToken("Test", "TST", 1_000_000);

        EscrowVault impl = new EscrowVault();
        bytes memory initData = abi.encodeCall(EscrowVault.initialize, (admin, operator, guardian));
        ERC1967Proxy proxy = new ERC1967Proxy(address(impl), initData);
        vault = EscrowVault(address(proxy));

        vm.prank(admin);
        vault.setTokenWhitelist(address(token), true);

        token.transfer(sponsor, 10_000 * TOKEN);
        vm.prank(sponsor);
        token.approve(address(vault), type(uint256).max);
    }

    // ─────────────────────────────────────────────────────────────
    // SOL-07
    // ─────────────────────────────────────────────────────────────

    function test_SOL07_transferToZero_reverts() public {
        vm.expectRevert(bytes("Transfer to zero address"));
        token.transfer(address(0), 1 * TOKEN);
    }

    function test_SOL07_transferFromToZero_reverts() public {
        token.approve(address(this), 1 * TOKEN);
        vm.expectRevert(bytes("Transfer to zero address"));
        token.transferFrom(address(this), address(0), 1 * TOKEN);
    }

    // ─────────────────────────────────────────────────────────────
    // SOL-08
    // ─────────────────────────────────────────────────────────────

    function test_SOL08_updateOperator_oldNotHoldingRole_reverts() public {
        address fakeOld = address(0xdead);
        address newOp = address(0xbeef);
        vm.prank(admin);
        vm.expectRevert(bytes("oldOperator lacks role"));
        vault.updateOperator(fakeOld, newOp);
    }

    function test_SOL08_updateGuardian_oldNotHoldingRole_reverts() public {
        address fakeOld = address(0xdead);
        address newG = address(0xbabe);
        vm.prank(admin);
        vm.expectRevert(bytes("oldGuardian lacks role"));
        vault.updateGuardian(fakeOld, newG);
    }

    function test_SOL08_updateOperator_happyPath() public {
        address newOp = address(0xbeef);
        vm.prank(admin);
        vault.updateOperator(operator, newOp);
        assertTrue(vault.hasRole(vault.OPERATOR_ROLE(), newOp));
        assertFalse(vault.hasRole(vault.OPERATOR_ROLE(), operator));
    }

    // ─────────────────────────────────────────────────────────────
    // SOL-05
    // ─────────────────────────────────────────────────────────────

    function test_SOL05_defaultMinReviewWindowIs24h() public {
        assertEq(vault.minReviewWindow(), 24 hours);
    }

    function test_SOL05_publishWithDelayBelowMinReverts() public {
        // 先把 98 TOKEN 注入 settledButUnallocated 池
        _settleOneUserTask(98 * TOKEN);

        vm.prank(operator);
        vm.expectRevert(bytes("Delay too short"));
        vault.publishPendingRoot(address(token), 1, keccak256("root"), 1, 0, keccak256("manifest"));

        vm.prank(operator);
        vm.expectRevert(bytes("Delay too short"));
        vault.publishPendingRoot(address(token), 1, keccak256("root"), 1, 1 hours, keccak256("manifest"));
    }

    function test_SOL05_publishWithDelayAboveMaxReverts() public {
        _settleOneUserTask(98 * TOKEN);
        vm.prank(operator);
        vm.expectRevert(bytes("Delay too long"));
        vault.publishPendingRoot(
            address(token), 1, keccak256("root"), 1,
            uint64(30 days + 1),
            keccak256("manifest")
        );
    }

    function test_SOL05_publishUint64TruncationGuard_reverts() public {
        _settleOneUserTask(98 * TOKEN);
        // 用 type(uint64).max 触发"上限"校验（先于 overflow 检查）
        // 这保证旧版本"用截断绕过 delay"的攻击路径被堵死
        uint64 huge = uint64(type(uint64).max);
        vm.prank(operator);
        vm.expectRevert(bytes("Delay too long"));
        vault.publishPendingRoot(address(token), 1, keccak256("root"), 1, huge, keccak256("manifest"));
    }

    function test_SOL05_setMinReviewWindow_belowFloor_reverts() public {
        vm.prank(admin);
        vm.expectRevert(bytes("below floor"));
        vault.setMinReviewWindow(uint64(1 hours) - 1);
    }

    function test_SOL05_setMinReviewWindow_aboveCeiling_reverts() public {
        vm.prank(admin);
        vm.expectRevert(bytes("above ceiling"));
        vault.setMinReviewWindow(uint64(30 days) + 1);
    }

    function test_SOL05_setMinReviewWindow_happyPath() public {
        vm.prank(admin);
        vault.setMinReviewWindow(2 hours);
        assertEq(vault.minReviewWindow(), 2 hours);

        // 调低后，2h delay 可通过
        _settleOneUserTask(98 * TOKEN);
        vm.prank(operator);
        vault.publishPendingRoot(address(token), 1, keccak256("root"), 1, 2 hours, keccak256("manifest"));
    }

    // ─────────────────────────────────────────────────────────────
    // SOL-04 聚合守恒
    // ─────────────────────────────────────────────────────────────

    /// @notice 单用户正常领取，totalClaimed 累加
    function test_SOL04_normalClaim_updatesTotalClaimed() public {
        // 1 个合格用户，basePool=98 TOKEN，lotteryWinnerCount=0
        bytes32 taskId = _settleOneUserTask(98 * TOKEN);
        uint128 epochDelta = uint128(98 * TOKEN);

        // 发 root：单 leaf root = leaf 本身
        bytes32 leaf = _claimLeaf(user1, 1, uint128(98 * TOKEN));
        vm.prank(operator);
        vault.publishPendingRoot(address(token), 1, leaf, epochDelta, 24 hours, keccak256("manifest"));
        vm.warp(block.timestamp + 24 hours + 60);
        vault.activateRoot(address(token), 1);

        assertEq(vault.totalClaimed(address(token)), 0);

        bytes32[] memory emptyProof = new bytes32[](0);
        vm.prank(user1);
        vault.claim(address(token), 1, uint128(98 * TOKEN), emptyProof, user1);

        assertEq(vault.totalClaimed(address(token)), 98 * TOKEN);
        assertEq(token.balanceOf(user1), 98 * TOKEN);

        taskId; // silence
    }

    /// @notice 关键：多用户合谋写满 cumulativeAmount → 第二个用户领取必须 revert
    /// 这是 spec §2 明确要求的验收用例
    function test_SOL04_aggregateSolvency_blocksMultiUserOverClaim() public {
        // 仅一个 task 注入 100 TOKEN（98 TOKEN 进结算池），但 root 里给两个用户各写满 98 TOKEN
        // 合计 196 > totalAllocated=98，必须有一笔 revert
        _settleZeroQualified(1 * TOKEN); // 这步不够，需要更大注入
        // 重新用更标准路径：一个 100 TOKEN 任务，0 资格 → 全退；
        // 改用 1 个合格用户领 98 TOKEN 的任务
        bytes32 taskId = _settleOneUserTask(98 * TOKEN);
        uint128 epochDelta = uint128(98 * TOKEN);

        // 构造一个 2-leaf 恶意 root：user1 和 user2 都被写成 cumulativeAmount=98 TOKEN
        bytes32 leaf1 = _claimLeaf(user1, 1, uint128(98 * TOKEN));
        bytes32 leaf2 = _claimLeaf(user2, 1, uint128(98 * TOKEN));
        bytes32 maliciousRoot = _hashPair(leaf1, leaf2);

        vm.prank(operator);
        vault.publishPendingRoot(address(token), 1, maliciousRoot, epochDelta, 24 hours, keccak256("evil"));
        vm.warp(block.timestamp + 24 hours + 60);
        vault.activateRoot(address(token), 1);

        // user1 领取成功：totalClaimed 98
        bytes32[] memory proof1 = new bytes32[](1);
        proof1[0] = leaf2;
        vm.prank(user1);
        vault.claim(address(token), 1, uint128(98 * TOKEN), proof1, user1);
        assertEq(token.balanceOf(user1), 98 * TOKEN);
        assertEq(vault.totalClaimed(address(token)), 98 * TOKEN);

        // user2 领取必须 revert（聚合守恒拦截）
        bytes32[] memory proof2 = new bytes32[](1);
        proof2[0] = leaf1;
        vm.prank(user2);
        vm.expectRevert(bytes("Exceeds allocated"));
        vault.claim(address(token), 1, uint128(98 * TOKEN), proof2, user2);

        taskId; // silence
    }

    // ─────────────────────────────────────────────────────────────
    // SOL-06 熵硬化 + 确定性中奖人数
    // ─────────────────────────────────────────────────────────────

    function test_SOL06_entropyBlock_setOnFinalize() public {
        bytes32 taskId = _createTask(100 * TOKEN, 78 * TOKEN, 10 * TOKEN, 1);
        bytes32 qRoot = _qualifiedRoot(taskId);

        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 2, qRoot, keccak256("qm"));

        uint64 eb = vault.taskEntropyBlock(taskId);
        assertEq(eb, uint64(block.number) + vault.ENTROPY_BLOCK_DELAY());
    }

    function test_SOL06_settleBeforeEntropyBlock_reverts() public {
        bytes32 taskId = _createTask(100 * TOKEN, 78 * TOKEN, 10 * TOKEN, 1);
        bytes32 qRoot = _qualifiedRoot(taskId);

        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 2, qRoot, keccak256("qm"));

        // 不 roll，立刻 settle → entropy block 还没到
        vm.prank(operator);
        vm.expectRevert(bytes("entropy block not reached"));
        vault.settleTask(taskId, bytes32("seed"), keccak256("r"), uint96(88 * TOKEN), uint96(10 * TOKEN), uint96(39 * TOKEN));
    }

    function test_SOL06_settleAfter256Blocks_reverts() public {
        bytes32 taskId = _createTask(100 * TOKEN, 78 * TOKEN, 10 * TOKEN, 1);
        bytes32 qRoot = _qualifiedRoot(taskId);

        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 2, qRoot, keccak256("qm"));

        // 跳过太远：超过 256 块 → blockhash 取不到
        vm.roll(block.number + 300);

        vm.prank(operator);
        vm.expectRevert(bytes("entropy block expired"));
        vault.settleTask(taskId, bytes32("seed"), keccak256("r"), uint96(88 * TOKEN), uint96(10 * TOKEN), uint96(39 * TOKEN));
    }

    function test_SOL06_entropy_isDeterministic() public {
        bytes32 taskId = _createTask(100 * TOKEN, 78 * TOKEN, 10 * TOKEN, 1);
        bytes32 qRoot = _qualifiedRoot(taskId);

        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 2, qRoot, keccak256("qm"));
        vm.roll(block.number + 11);

        vm.prank(operator);
        vault.settleTask(taskId, bytes32("seed"), keccak256("r"), uint96(88 * TOKEN), uint96(10 * TOKEN), uint96(39 * TOKEN));

        bytes32 finalEntropy = vault.taskEntropy(taskId);
        assertTrue(finalEntropy != bytes32(0), "entropy should be derived");

        // 链下复算：相同 seedReveal + 同 blockhash → 相同熵
        uint64 eb = vault.taskEntropyBlock(taskId);
        // 注意：blockhash 在测试里要可得，eb 必须仍在最近 256 块内
        bytes32 bh = blockhash(eb);
        bytes32 expected = keccak256(abi.encode(bytes32("seed"), bh));
        assertEq(finalEntropy, expected);
    }

    function test_SOL06_actualWinnerCount_isDeterministic_min() public {
        // qualified=2, lotteryWinnerCount=5 → expectedWinners = min(2,5) = 2
        bytes32 taskId = _createTask(200 * TOKEN, 78 * TOKEN, 10 * TOKEN, 5);
        bytes32 qRoot = _qualifiedRoot(taskId);

        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 2, qRoot, keccak256("qm"));
        vm.roll(block.number + 11);

        // expectedWinners=2，公式 = 39*2 + 10*2 = 98。totalBudget=200，fee=200*2%=4，refund=200-98-4=98
        vm.prank(operator);
        vault.settleTask(taskId, bytes32("seed"), keccak256("r"), uint96(98 * TOKEN), uint96(98 * TOKEN), uint96(39 * TOKEN));

        (, , , , , uint16 actualWinnerCount, , , ,) = vault.settlements(taskId);
        assertEq(actualWinnerCount, 2);
    }

    // ─────────────────────────────────────────────────────────────
    // SOL-10 存量清退
    // ─────────────────────────────────────────────────────────────

    function test_SOL10_windDown_delistedToken_publishAllowed() public {
        // 先让 token 进 settledButUnallocated
        bytes32 taskId = _settleOneUserTask(98 * TOKEN);
        uint256 stuck = vault.settledButUnallocated(address(token));
        assertEq(stuck, 98 * TOKEN);

        // 下架 token
        vm.prank(admin);
        vault.setTokenWhitelist(address(token), false);

        // createTask 对下架 token 必须 revert
        vm.prank(sponsor);
        vm.expectRevert(bytes("Token not allowed"));
        vault.createTask(
            keccak256("new-task"), address(token),
            uint96(1 * TOKEN), uint96(1 * TOKEN), 0, 0,
            uint64(block.timestamp + 1 days), uint64(block.timestamp + 2 days),
            keccak256(abi.encodePacked(bytes32("s")))
        );

        // 但已下架 token 仍可通过 publishPendingRoot 清退存量
        bytes32 leaf = _claimLeaf(user1, 1, uint128(98 * TOKEN));
        vm.prank(operator);
        vault.publishPendingRoot(address(token), 1, leaf, uint128(98 * TOKEN), 24 hours, keccak256("wind-down"));
        vm.warp(block.timestamp + 24 hours + 60);
        vault.activateRoot(address(token), 1);

        bytes32[] memory emptyProof = new bytes32[](0);
        vm.prank(user1);
        vault.claim(address(token), 1, uint128(98 * TOKEN), emptyProof, user1);
        assertEq(token.balanceOf(user1), 98 * TOKEN);

        taskId; // silence
    }

    function test_SOL10_delisted_noStuckBalance_publishStillReverts() public {
        // 没有任何存量
        vm.prank(admin);
        vault.setTokenWhitelist(address(token), false);

        vm.prank(operator);
        vm.expectRevert(bytes("Token not allowed and nothing to wind down"));
        vault.publishPendingRoot(address(token), 1, keccak256("r"), 1, 24 hours, keccak256("m"));
    }

    // ─────────────────────────────────────────────────────────────
    // helpers
    // ─────────────────────────────────────────────────────────────

    function _createTask(
        uint256 totalBudget,
        uint256 basePool,
        uint256 lotteryRewardPerWinner,
        uint16 lotteryWinnerCount
    ) internal returns (bytes32 taskId) {
        bytes32 seed = bytes32("seed");
        taskId = keccak256(abi.encodePacked(
            "task", nextTaskNonce++, totalBudget, basePool,
            lotteryRewardPerWinner, lotteryWinnerCount, seed, block.timestamp
        ));
        vm.prank(sponsor);
        vault.createTask(
            taskId, address(token),
            uint96(totalBudget), uint96(basePool),
            uint96(lotteryRewardPerWinner), lotteryWinnerCount,
            uint64(block.timestamp + 1 days), uint64(block.timestamp + 2 days),
            keccak256(abi.encodePacked(seed))
        );
    }

    function _settleZeroQualified(uint256 budget) internal returns (bytes32 taskId) {
        taskId = _createTask(budget, budget - _fee(budget), 0, 0);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 0, bytes32(0), keccak256("qm"));
        vm.prank(operator);
        vault.settleTask(taskId, bytes32("seed"), keccak256("r"), 0, uint96(budget - _fee(budget)), 0);
    }

    function _settleOneUserTask(uint256 payoutAmount) internal returns (bytes32 taskId) {
        // 100 TOKEN 总预算，1 个合格用户，全部 payoutAmount 给他，lotteryWinnerCount=0
        taskId = _createTask(100 * TOKEN, 98 * TOKEN, 0, 0);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 1, keccak256("qroot"), keccak256("qm"));
        // lotteryWinnerCount=0 → 短路 entropy 校验
        vm.prank(operator);
        vault.settleTask(
            taskId, bytes32("seed"), keccak256("r"),
            uint96(payoutAmount),
            uint96(100 * TOKEN - DEFAULT_FEE - payoutAmount),
            uint96(payoutAmount)
        );
    }

    function _fee(uint256 budget) internal pure returns (uint256) {
        return (budget * 200) / 10_000;
    }

    function _claimLeaf(address account, uint64 rootId, uint128 cumulativeAmount) internal view returns (bytes32) {
        return keccak256(bytes.concat(keccak256(abi.encode(account, address(token), rootId, cumulativeAmount))));
    }

    function _qualifiedRoot(bytes32 taskId) internal view returns (bytes32) {
        bytes32 l1 = keccak256(abi.encodePacked(taskId, user1));
        bytes32 l2 = keccak256(abi.encodePacked(taskId, user2));
        return _hashPair(l1, l2);
    }

    function _hashPair(bytes32 a, bytes32 b) internal pure returns (bytes32) {
        return a < b ? keccak256(abi.encodePacked(a, b)) : keccak256(abi.encodePacked(b, a));
    }
}
