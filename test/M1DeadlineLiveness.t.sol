// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import "../contracts/EscrowVault.sol";
import "../contracts/SimpleToken.sol";

/**
 * @title M1DeadlineLivenessTest
 * @notice M-1 验收用例：qualifyDeadline 到 settlementDeadline 之间的窗口过窄，
 *         会导致抽奖任务进入 QUALIFIED 后天然无法 settleTask（取熵还没到就已经
 *         "Settlement expired"），非抽奖任务窗口也窄到只有一个块，运营上不可用。
 *
 *         修复：MIN_SETTLEMENT_WINDOW = 1 hours，两层校验——
 *         ① createTask：早失败，禁止创建注定无法结算的任务
 *         ② finalizeQualification：兜底，防 Operator 拖延吃掉 createTask 时预留的窗口
 */
contract M1DeadlineLivenessTest is Test {
    EscrowVault internal vault;
    SimpleToken internal token;

    address internal admin = address(0xA11CE);
    address internal operator = address(0x0A0A);
    address internal guardian = address(0xB0B);
    address internal sponsor = address(0xC0DE);
    address internal user1 = address(0x1111);
    address internal user2 = address(0x2222);

    uint256 internal constant TOKEN = 1e18;

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

    function _qualifiedRoot(bytes32 taskId) internal view returns (bytes32) {
        bytes32 l1 = keccak256(abi.encodePacked(taskId, user1));
        bytes32 l2 = keccak256(abi.encodePacked(taskId, user2));
        return l1 < l2 ? keccak256(abi.encodePacked(l1, l2)) : keccak256(abi.encodePacked(l2, l1));
    }

    // ─────────────────────────────────────────────────────────────
    // 第一层：createTask 拒绝窗口过窄的配置
    // ─────────────────────────────────────────────────────────────

    /// @notice 修复前：这个配置能创建成功但天然无法结算。修复后：createTask 直接 revert。
    function test_M1_lotteryTask_equalDeadlines_rejectedAtCreation() public {
        bytes32 taskId = keccak256("m1-lottery-equal");
        uint64 deadline = uint64(block.timestamp + 1 days);

        vm.prank(sponsor);
        vm.expectRevert(bytes("Settlement window too short"));
        vault.createTask(
            taskId, address(token),
            uint96(100 * TOKEN), uint96(78 * TOKEN), uint96(10 * TOKEN), 1,
            deadline, deadline,
            keccak256(abi.encodePacked(bytes32("seed")))
        );
    }

    /// @notice 非抽奖任务同样受最小窗口约束（原本技术上能同块结算，但窗口只有一个块，运营不可用）。
    function test_M1_nonLotteryTask_equalDeadlines_rejectedAtCreation() public {
        bytes32 taskId = keccak256("m1-nonlottery-equal");
        uint64 deadline = uint64(block.timestamp + 1 days);

        vm.prank(sponsor);
        vm.expectRevert(bytes("Settlement window too short"));
        vault.createTask(
            taskId, address(token),
            uint96(100 * TOKEN), uint96(98 * TOKEN), 0, 0,
            deadline, deadline,
            keccak256(abi.encodePacked(bytes32("seed")))
        );
    }

    /// @notice 窗口恰好等于 MIN_SETTLEMENT_WINDOW（边界值）应该刚好放行。
    function test_M1_exactMinWindow_boundaryAllowed() public {
        bytes32 taskId = keccak256("m1-boundary");
        uint64 qualifyDeadline = uint64(block.timestamp + 1 days);
        uint64 settlementDeadline = qualifyDeadline + uint64(vault.MIN_SETTLEMENT_WINDOW());

        vm.prank(sponsor);
        vault.createTask(
            taskId, address(token),
            uint96(100 * TOKEN), uint96(78 * TOKEN), uint96(10 * TOKEN), 1,
            qualifyDeadline, settlementDeadline,
            keccak256(abi.encodePacked(bytes32("seed")))
        );
        // 未 revert 即证明恰好等于最小窗口时可以创建成功
    }

    /// @notice 比最小窗口差 1 秒，应该被拒绝（精确边界，不是"差很多才拒绝"）。
    function test_M1_windowOneSecondShort_rejected() public {
        bytes32 taskId = keccak256("m1-one-second-short");
        uint64 qualifyDeadline = uint64(block.timestamp + 1 days);
        uint64 settlementDeadline = qualifyDeadline + uint64(vault.MIN_SETTLEMENT_WINDOW()) - 1;

        vm.prank(sponsor);
        vm.expectRevert(bytes("Settlement window too short"));
        vault.createTask(
            taskId, address(token),
            uint96(100 * TOKEN), uint96(78 * TOKEN), uint96(10 * TOKEN), 1,
            qualifyDeadline, settlementDeadline,
            keccak256(abi.encodePacked(bytes32("seed")))
        );
    }

    // ─────────────────────────────────────────────────────────────
    // 正常路径：窗口充足时，抽奖任务能完整走完 finalize → settle
    // ─────────────────────────────────────────────────────────────

    /// @notice 窗口给够（qualifyDeadline + 2h），抽奖任务应该能正常走完整个结算流程，
    ///         证明这次修复没有误伤合法配置。
    function test_M1_sufficientWindow_lotteryTaskSettlesSuccessfully() public {
        bytes32 taskId = keccak256("m1-sufficient-window");
        uint64 qualifyDeadline = uint64(block.timestamp + 1 days);
        uint64 settlementDeadline = qualifyDeadline + 2 hours;

        vm.prank(sponsor);
        vault.createTask(
            taskId, address(token),
            uint96(100 * TOKEN), uint96(78 * TOKEN), uint96(10 * TOKEN), 1,
            qualifyDeadline, settlementDeadline,
            keccak256(abi.encodePacked(bytes32("seed")))
        );

        vm.warp(qualifyDeadline);
        vm.prank(operator);
        vault.finalizeQualification(taskId, 2, _qualifiedRoot(taskId), keccak256("qm"));

        // 等取熵区块就位（11 块，真实链上伴随秒级时间推进，这里给足余量仍在窗口内）
        vm.roll(block.number + 11);
        vm.warp(block.timestamp + 22);

        vm.prank(operator);
        vault.settleTask(taskId, bytes32("seed"), keccak256("r"), uint96(88 * TOKEN), uint96(10 * TOKEN), uint96(39 * TOKEN));

        (,,,,,, uint96 payoutAmount,,,) = vault.settlements(taskId);
        assertEq(payoutAmount, 88 * TOKEN, "settlement should succeed with sufficient window");
    }

    // ─────────────────────────────────────────────────────────────
    // 第二层：finalizeQualification 兜底，防 Operator 拖延吃掉窗口
    // ─────────────────────────────────────────────────────────────

    /// @notice createTask 时窗口是够的，但 Operator 拖到只剩 30 分钟才 finalize
    ///         （< MIN_SETTLEMENT_WINDOW=1h）—— 第二层校验应该拦下，任务留在 FUNDED。
    function test_M1_operatorDelay_eatsWindow_finalizeRejected() public {
        bytes32 taskId = keccak256("m1-operator-delay");
        uint64 qualifyDeadline = uint64(block.timestamp + 1 days);
        uint64 settlementDeadline = qualifyDeadline + 2 hours; // createTask 时窗口充足

        vm.prank(sponsor);
        vault.createTask(
            taskId, address(token),
            uint96(100 * TOKEN), uint96(78 * TOKEN), uint96(10 * TOKEN), 1,
            qualifyDeadline, settlementDeadline,
            keccak256(abi.encodePacked(bytes32("seed")))
        );

        // Operator 拖到 settlementDeadline 前 30 分钟才想起来 finalize —— 窗口已不足 1h
        vm.warp(settlementDeadline - 30 minutes);
        vm.prank(operator);
        vm.expectRevert(bytes("insufficient settlement window"));
        vault.finalizeQualification(taskId, 2, _qualifiedRoot(taskId), keccak256("qm"));

        // 任务应仍留在 FUNDED（未进入注定失败的 QUALIFIED），Sponsor 到期后仍能全额拿回
        // TaskConfig 12 个字段，status 是第 11 个：taskId,sponsor,token,totalBudget,basePool,
        // lotteryRewardPerWinner,lotteryWinnerCount,qualifyDeadline,settlementDeadline,seedCommit,status,platformFeeBps
        (,,,,,,,,,, EscrowVault.TaskStatus status,) = vault.tasks(taskId);
        assertEq(uint256(status), uint256(EscrowVault.TaskStatus.FUNDED), "task must stay FUNDED, not enter doomed QUALIFIED");

        vm.warp(uint256(settlementDeadline) + 1);
        vm.prank(sponsor);
        vault.emergencyRefund(taskId);
        assertEq(token.balanceOf(sponsor), 10_000 * TOKEN, "sponsor recovers full budget, no fund lock");
    }
}
