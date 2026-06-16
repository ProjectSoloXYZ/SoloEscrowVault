// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// OpenZeppelin 可升级合约权限控制
import "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
// 可升级初始化基类
import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
// UUPS 升级模式
import "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
// 可暂停模块
import "@openzeppelin/contracts-upgradeable/utils/PausableUpgradeable.sol";
// 重入攻击防护
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

// ERC20 接口
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
// 安全 ERC20 操作，兼容非标准 ERC20
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
// Merkle Proof 验证库
import "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";

/**
 * @title EscrowVault
 * @notice 半中心化任务结算 MVP：
 *         - Sponsor 创建任务并将预算锁入合约
 *         - Operator 冻结合格名单并提交结算摘要
 *         - Operator 发布累计 Root，用户基于 Proof 提现
 *         - Guardian 可暂停系统、取消错误 Pending Root
 */
contract EscrowVault is Initializable, AccessControlUpgradeable, PausableUpgradeable, UUPSUpgradeable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // 操作员角色：负责资格确认、结算、发布 root
    bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");

    // 守护者角色：负责暂停系统、取消错误 root
    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");

    // 费率分母，10000 = 100%
    uint16 public constant FEE_DENOMINATOR_BPS = 10_000;

    // 平台费率上限，1000 = 10%
    uint16 public constant MAX_PLATFORM_FEE_BPS = 1_000;

    // 默认平台费率，200 = 2%
    uint16 public constant DEFAULT_PLATFORM_FEE_BPS = 200;

    /**
     * @dev 任务状态机
     * NONE       : 不存在
     * FUNDED     : 已创建并注资
     * QUALIFIED  : 已完成资格确认
     * SETTLED    : 已结算且无需退款
     * REFUNDABLE : 已结算，有退款可领
     * REFUNDED   : 退款已领
     * CANCELLED  : Sponsor 在资格确认前取消
     */
    enum TaskStatus {
        NONE,
        FUNDED,
        QUALIFIED,
        SETTLED,
        REFUNDABLE,
        REFUNDED,
        CANCELLED
    }

    /**
     * @dev 任务配置
     * taskId                  任务唯一 ID
     * sponsor                 任务发起方
     * token                   使用的 ERC20 代币
     * totalBudget             总预算
     * basePool                给所有合格用户的基础奖励池
     * lotteryRewardPerWinner  每个中奖者额外奖励
     * lotteryWinnerCount      计划中奖人数
     * qualifyDeadline         资格确认截止时间
     * settlementDeadline      结算截止时间
     * seedCommit              对随机种子的承诺值（commit）seedCommit：前面先提交的哈希承诺
     * status                  当前任务状态
     * platformFeeBps          创建任务时锁定的平台费率
     */
    struct TaskConfig {
        bytes32 taskId;
        address sponsor;
        address token;
        uint96 totalBudget;
        uint96 basePool;
        uint96 lotteryRewardPerWinner;
        uint16 lotteryWinnerCount;
        uint64 qualifyDeadline;
        uint64 settlementDeadline;
        bytes32 seedCommit;
        TaskStatus status;
        uint16 platformFeeBps;
    }

    /**
     * @dev 资格确认结果
     * qualifiedCount              合格人数
     * qualifiedRoot               合格名单的 Merkle Root
     * qualificationManifestHash   合格名单对应的清单哈希（链下文件哈希）
     * finalizedAt                 确认时间
     */
    struct Qualification {
        uint32 qualifiedCount;
        bytes32 qualifiedRoot;
        bytes32 qualificationManifestHash;
        uint64 finalizedAt;
    }

    /**
     * @dev 结算结果
     * seedReveal              随机种子揭示值（reveal）
     * entropyRef              外部熵引用编号
     * entropyValue            外部熵值
     * resultManifestHash      结算结果清单哈希
     * baseRewardPerQualified  每个合格用户可得基础奖励
     * actualWinnerCount       实际中奖人数
     * payoutAmount            本任务实际用于发奖的金额
     * refundableAmount        Sponsor 可退回金额
     * platformFeeAmount       平台服务费金额
     * settledAt               结算时间
     */
    struct Settlement {
        bytes32 seedReveal;
        uint64 entropyRef;
        bytes32 entropyValue;
        bytes32 resultManifestHash;
        uint96 baseRewardPerQualified;
        uint16 actualWinnerCount;
        uint96 payoutAmount;
        uint96 refundableAmount;
        uint64 settledAt;
        uint96 platformFeeAmount;
    }

    /**
     * @dev 待激活 Root
     * rootId            root 版本号，必须递增
     * token             对应 token
     * merkleRoot        Merkle Root
     * epochDeltaAmount  这一次新增可分配金额
     * activateAfter     延迟窗口结束后才能激活
     * manifestHash      链下明细文件哈希
     * cancelled         是否被取消（这里实际没用上）
     */
    struct PendingRoot {
        uint64 rootId;
        address token;
        bytes32 merkleRoot;
        uint128 epochDeltaAmount;
        uint64 activateAfter;
        bytes32 manifestHash;
        bool cancelled;
    }

    /**
     * @dev 当前生效中的 Root
     * rootId          当前有效 root 的版本号
     * merkleRoot      当前有效 root
     * totalAllocated  截止当前累计已经分配进 root 的金额
     * manifestHash    对应链下清单哈希
     */
    struct ActiveRoot {
        uint64 rootId;
        bytes32 merkleRoot;
        uint128 totalAllocated;
        bytes32 manifestHash;
    }

    // taskId => 任务配置，“我要建立一个名为 tasks 的登记簿。以后只要你给我一个编号（bytes32），我就能立刻从这个登记簿里把对应的任务详情（TaskConfig）找出来给你看。”
    mapping(bytes32 => TaskConfig) public tasks;

    // taskId => 合格信息
    mapping(bytes32 => Qualification) public qualifications;

    // taskId => 结算信息
    mapping(bytes32 => Settlement) public settlements;

    // token => 当前待激活 root
    mapping(address => PendingRoot) public pendingRoots;

    // token => 当前已激活 root
    mapping(address => ActiveRoot) public activeRoots;

    // 用户累计已领取金额：account => token => claimedAmount
    // 注意：这里是“累计已领”，不是按 task 记录，而是按 token 记录
    mapping(address => mapping(address => uint256)) public claimed;

    // token => 已结算但尚未被分配进 root 的金额
    mapping(address => uint256) public settledButUnallocated;

    // token 白名单，只有白名单 token 才能用于任务
    mapping(address => bool) public tokenWhitelist;

    // 当前全局平台费率，单位 bps；创建任务时会锁定到 TaskConfig
    uint16 public platformFeeBps;

    // 平台费默认接收地址，由管理员维护
    address public platformTreasury;

    // token => 已计提但尚未提现的平台费
    mapping(address => uint256) public platformFeeBalances;

    // 给未来升级预留存储槽，避免存储冲突
    uint256[39] private __gap;

    // =========================
    //         事件
    // =========================

    /**
     * @dev 任务创建事件 - 当 Sponsor 创建新任务并注资时触发
     * @param taskId                  任务唯一标识（已索引，可链上过滤）
     * @param sponsor                 任务发起方地址（已索引）
     * @param token                   使用的 ERC20 代币地址（已索引）
     * @param totalBudget             任务总预算
     * @param basePool                基础奖励池（平分给所有合格用户）
     * @param lotteryRewardPerWinner  每个中奖者的额外奖励金额
     * @param lotteryWinnerCount      计划中奖人数上限
     * @param qualifyDeadline         资格确认截止时间（过了这个时间才能冻结名单）
     * @param settlementDeadline      结算截止时间（最晚必须完成结算）
     * @param seedCommit              随机种子承诺值（后续 reveal 时验证）
     */
    event TaskCreated(
        bytes32 indexed taskId,
        address indexed sponsor,
        address indexed token,
        uint96 totalBudget,
        uint96 basePool,
        uint96 lotteryRewardPerWinner,
        uint16 lotteryWinnerCount,
        uint64 qualifyDeadline,
        uint64 settlementDeadline,
        bytes32 seedCommit,
        uint16 platformFeeBps
    );

    /**
     * @dev 任务取消事件 - 当 Sponsor 在资格确认前取消任务，或结算超时后紧急退款时触发
     * @param taskId         任务唯一标识（已索引）
     * @param sponsor        任务发起方地址（已索引）
     * @param refundedAmount 退还给 Sponsor 的金额
     */
    event TaskCancelled(bytes32 indexed taskId, address indexed sponsor, uint256 refundedAmount);

    /**
     * @dev 资格确认完成事件 - 当 Operator 完成合格名单冻结时触发
     * @param taskId                    任务唯一标识（已索引）
     * @param qualifiedCount            合格参与者人数
     * @param qualifiedRoot             合格名单的 Merkle Root（用于链上验证某用户是否合格）
     * @param qualificationManifestHash 资格清单的链下文件哈希（用于审计追溯）
     */
    event QualificationFinalized(
        bytes32 indexed taskId,
        uint32 qualifiedCount,
        bytes32 qualifiedRoot,
        bytes32 qualificationManifestHash
    );

    /**
     * @dev 任务结算事件 - 当 Operator 完成任务结算（分奖+退款计算）时触发
     * @param taskId                  任务唯一标识（已索引）
     * @param seedReveal              种子揭示值（与创建时的 seedCommit 对应，用于验证随机性）
     * @param entropyRef              熵源引用（指向链上随机数来源，如区块号）
     * @param entropyValue            实际使用的熵值（随机数）
     * @param resultManifestHash      结算结果清单哈希（链下结果文件的摘要，用于审计）
     * @param payoutAmount            实际支付给参与者的总金额
     * @param refundableAmount        可退还给 Sponsor 的剩余金额
     * @param baseRewardPerQualified  每个合格用户可得的基础奖励
     * @param actualWinnerCount       本次结算实际选出的中奖者人数
     * @param platformFeeAmount       本次计提的平台服务费
     */
    event TaskSettled(
        bytes32 indexed taskId,
        bytes32 seedReveal,
        uint64 entropyRef,
        bytes32 entropyValue,
        bytes32 resultManifestHash,
        uint96 payoutAmount,
        uint96 refundableAmount,
        uint96 baseRewardPerQualified,
        uint16 actualWinnerCount,
        uint96 platformFeeAmount
    );

    /**
     * @dev 退款领取事件 - 当 Sponsor 领取结算后的退款时触发
     * @param taskId    任务唯一标识（已索引）
     * @param sponsor   任务发起方地址（已索引）
     * @param recipient 实际收款地址（已索引，可以与 sponsor 不同）
     * @param amount    退款金额
     */
    event RefundClaimed(bytes32 indexed taskId, address indexed sponsor, address indexed recipient, uint256 amount);

    /**
     * @dev 待激活 Root 发布事件 - 当 Operator 发布新的 Merkle Root 等待审核时触发
     * @param token            代币地址（已索引）
     * @param rootId           Root 版本号（已索引，严格递增）
     * @param merkleRoot       新的 Merkle Root
     * @param epochDeltaAmount 本次新增的可分配金额
     * @param activateAfter    审核窗口结束时间（过后才能激活）
     * @param manifestHash     链下奖励明细文件哈希
     */
    event PendingRootPublished(
        address indexed token,
        uint64 indexed rootId,
        bytes32 merkleRoot,
        uint128 epochDeltaAmount,
        uint64 activateAfter,
        bytes32 manifestHash
    );

    /**
     * @dev 待激活 Root 取消事件 - 当 Guardian 取消一个可疑的 pending root 时触发
     * @param token  代币地址（已索引）
     * @param rootId 被取消的 Root 版本号（已索引）
     */
    event PendingRootCancelled(address indexed token, uint64 indexed rootId);

    /**
     * @dev Root 激活事件 - 当 pending root 通过审核窗口被激活为当前生效 root 时触发
     * @param token          代币地址（已索引）
     * @param rootId         激活的 Root 版本号（已索引）
     * @param merkleRoot     生效的 Merkle Root
     * @param totalAllocated 截止当前累计已分配进 root 的总金额
     * @param manifestHash   链下奖励明细文件哈希
     */
    event RootActivated(
        address indexed token,
        uint64 indexed rootId,
        bytes32 merkleRoot,
        uint128 totalAllocated,
        bytes32 manifestHash
    );

    /**
     * @dev 用户领取奖励事件 - 当用户通过 Merkle Proof 领取奖励时触发
     * @param account          领取者地址（已索引，即 msg.sender）
     * @param token            代币地址（已索引）
     * @param recipient        实际收款地址（已索引，可以与 account 不同）
     * @param rootId           使用的 Root 版本号
     * @param cumulativeAmount 用户在该 root 下的累计应得总额
     * @param deltaAmount      本次实际转出的增量金额（= cumulativeAmount - 之前已领）
     */
    event Claimed(
        address indexed account,
        address indexed token,
        address indexed recipient,
        uint64 rootId,
        uint128 cumulativeAmount,
        uint256 deltaAmount
    );

    /**
     * @dev Token 白名单更新事件 - 管理员添加/移除可用代币时触发
     * @param token   代币地址（已索引）
     * @param allowed 是否允许（true=加入白名单，false=移除）
     */
    event TokenWhitelistUpdated(address indexed token, bool allowed);

    /**
     * @dev 操作员更换事件
     * @param oldOperator 旧操作员地址（已索引）
     * @param newOperator 新操作员地址（已索引）
     */
    event OperatorUpdated(address indexed oldOperator, address indexed newOperator);

    /**
     * @dev 守护者更换事件
     * @param oldGuardian 旧守护者地址（已索引）
     * @param newGuardian 新守护者地址（已索引）
     */
    event GuardianUpdated(address indexed oldGuardian, address indexed newGuardian);

    /**
     * @dev 平台费率更新事件
     */
    event PlatformFeeBpsUpdated(uint16 oldBps, uint16 newBps);

    /**
     * @dev 平台费接收地址更新事件
     */
    event PlatformTreasuryUpdated(address indexed oldTreasury, address indexed newTreasury);

    /**
     * @dev 平台费计提事件
     */
    event PlatformFeeAccrued(bytes32 indexed taskId, address indexed token, uint256 amount, uint16 feeBps);

    /**
     * @dev 平台费提现事件
     */
    event PlatformFeeWithdrawn(address indexed token, address indexed recipient, uint256 amount);

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        // 禁用实现合约的初始化，防止实现合约被人单独初始化劫持
        _disableInitializers();
    }

    /**
     * @dev 初始化函数，代替构造函数
     * @param initialAdmin    默认管理员
     * @param initialOperator 初始操作员
     * @param initialGuardian 初始守护者
     这个函数负责把管理员、操作员、守护者三个关键角色配置好。带有 initializer 的函数： 全寿命只能运行一次！
     */
    function initialize(address initialAdmin, address initialOperator, address initialGuardian) public initializer {
        require(initialAdmin != address(0), "bad admin");///检测不能是空地址
        require(initialOperator != address(0), "bad operator");
        require(initialGuardian != address(0), "bad guardian");

        __AccessControl_init();
        __Pausable_init();

        // 授予角色
        _grantRole(DEFAULT_ADMIN_ROLE, initialAdmin);
        _grantRole(OPERATOR_ROLE, initialOperator);
        _grantRole(GUARDIAN_ROLE, initialGuardian);

        platformFeeBps = DEFAULT_PLATFORM_FEE_BPS;
        platformTreasury = initialAdmin;
        emit PlatformFeeBpsUpdated(0, DEFAULT_PLATFORM_FEE_BPS);
        emit PlatformTreasuryUpdated(address(0), initialAdmin);
    }

    /**
     * @dev V2 初始化：供旧代理升级后设置平台费默认参数。
     */
    function initializeV2(address initialTreasury) external onlyRole(DEFAULT_ADMIN_ROLE) reinitializer(2) {
        require(initialTreasury != address(0), "bad treasury");

        platformFeeBps = DEFAULT_PLATFORM_FEE_BPS;
        platformTreasury = initialTreasury;
        emit PlatformFeeBpsUpdated(0, DEFAULT_PLATFORM_FEE_BPS);
        emit PlatformTreasuryUpdated(address(0), initialTreasury);
    }

    /**
     * @dev UUPS 升级授权：只有管理员可以升级
     */
    function _authorizeUpgrade(address newImplementation) internal override onlyRole(DEFAULT_ADMIN_ROLE) {}

    /**
     * @dev 限制只有任务 sponsor 可调用
     */
    modifier onlySponsor(bytes32 taskId) {
        require(msg.sender == tasks[taskId].sponsor, "Only sponsor");
        _;
    }

    /**
     * @dev 设置 token 白名单平台先规定哪些 ERC20 代币可以拿来发任务，Sponsor 创建任务时只能用这些被允许的 token。
     */
    function setTokenWhitelist(address token, bool allowed) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(token != address(0), "bad token");
        tokenWhitelist[token] = allowed;
        emit TokenWhitelistUpdated(token, allowed);
    }

    /**
     * @dev 更新操作员
     * 注意：这里要求传 oldOperator，且无校验 oldOperator 当前是否真有该角色
     */
    function updateOperator(address oldOperator, address newOperator) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(newOperator != address(0), "bad operator");
        _revokeRole(OPERATOR_ROLE, oldOperator);
        _grantRole(OPERATOR_ROLE, newOperator);
        emit OperatorUpdated(oldOperator, newOperator);
    }

    /**
     * @dev 更新守护者
     */
    function updateGuardian(address oldGuardian, address newGuardian) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(newGuardian != address(0), "bad guardian");
        _revokeRole(GUARDIAN_ROLE, oldGuardian);
        _grantRole(GUARDIAN_ROLE, newGuardian);
        emit GuardianUpdated(oldGuardian, newGuardian);
    }

    /**
     * @dev 管理员更新平台费率，单位 bps。
     */
    function setPlatformFeeBps(uint16 newBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(newBps <= MAX_PLATFORM_FEE_BPS, "fee too high");
        uint16 oldBps = platformFeeBps;
        platformFeeBps = newBps;
        emit PlatformFeeBpsUpdated(oldBps, newBps);
    }

    /**
     * @dev 管理员更新平台费默认接收地址。
     */
    function setPlatformTreasury(address newTreasury) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(newTreasury != address(0), "bad treasury");
        address oldTreasury = platformTreasury;
        platformTreasury = newTreasury;
        emit PlatformTreasuryUpdated(oldTreasury, newTreasury);
    }

    /**
     * @dev 管理员提现已计提的平台费。
     */
    function withdrawPlatformFees(address token, address recipient, uint256 amount)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
        nonReentrant
    {
        require(recipient != address(0), "bad recipient");
        require(amount > 0, "zero amount");
        require(amount <= platformFeeBalances[token], "insufficient fee balance");

        platformFeeBalances[token] -= amount;
        IERC20(token).safeTransfer(recipient, amount);

        emit PlatformFeeWithdrawn(token, recipient, amount);
    }

    /**
     * @dev 守护者暂停系统
     */
    function pause() external onlyRole(GUARDIAN_ROLE) {
        _pause();
    }

    /**
     * @dev 管理员恢复系统
     */
    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }

    /**
     * @dev Sponsor 创建任务并转入预算
     * @param taskId                  任务 ID
     * @param token                   使用的 token
     * @param totalBudget             总预算
     * @param basePool                基础奖励池
     * @param lotteryRewardPerWinner  单个中奖额外奖励
     * @param lotteryWinnerCount      计划中奖人数
     * @param qualifyDeadline         资格确认截止时间“报名/参与结束，名单冻结的时间”
     * @param settlementDeadline      结算截止时间“最晚必须把奖金和退款算完的时间”
     * @param seedCommit              对 seedReveal 的承诺
     没问题
     */
    function createTask(
        bytes32 taskId,
        address token,
        uint96 totalBudget,
        uint96 basePool,
        uint96 lotteryRewardPerWinner,
        uint16 lotteryWinnerCount,
        uint64 qualifyDeadline,
        uint64 settlementDeadline,
        bytes32 seedCommit
    ) external whenNotPaused nonReentrant {
        // 任务不能重复创建
        require(tasks[taskId].status == TaskStatus.NONE, "Task exists");

        // token 必须在白名单
        require(tokenWhitelist[token], "Token not allowed");

        // 必须提供随机种子承诺
        require(seedCommit != bytes32(0), "Empty seedCommit");

        // 总预算必须大于 0
        require(totalBudget > 0, "Zero budget");

        // 资格确认截止必须在未来
        require(qualifyDeadline > block.timestamp, "Bad qualifyDeadline");

        // 结算截止必须不早于资格截止
        require(settlementDeadline >= qualifyDeadline, "Bad settlementDeadline");

        // 保底分配 + 抽奖分配 + 平台费不能超过总预算。
        // 平台费从 totalBudget 中计提，因此创建时必须确保最大承诺奖励仍可支付。
        uint256 reserved = uint256(basePool) + uint256(lotteryRewardPerWinner) * uint256(lotteryWinnerCount);
        uint256 estimatedPlatformFee = _calculatePlatformFee(totalBudget, platformFeeBps);
        require(reserved + estimatedPlatformFee <= totalBudget, "Budget params invalid");

        // Sponsor 把总预算转进合约，并强制实收金额等于 totalBudget。
        // EscrowVault 使用内部账本分配资金，不支持转账税、通缩或 rebasing token。
        uint256 beforeBalance = IERC20(token).balanceOf(address(this));
        IERC20(token).safeTransferFrom(msg.sender, address(this), totalBudget);
        uint256 received = IERC20(token).balanceOf(address(this)) - beforeBalance;
        require(received == totalBudget, "Bad token transfer");

        // 保存任务信息
        tasks[taskId] = TaskConfig({
            taskId: taskId,
            sponsor: msg.sender,
            token: token,
            totalBudget: totalBudget,
            basePool: basePool,
            lotteryRewardPerWinner: lotteryRewardPerWinner,
            lotteryWinnerCount: lotteryWinnerCount,
            qualifyDeadline: qualifyDeadline,
            settlementDeadline: settlementDeadline,
            seedCommit: seedCommit,
            status: TaskStatus.FUNDED,
            platformFeeBps: platformFeeBps
        });

        emit TaskCreated(
            taskId,
            msg.sender,
            token,
            totalBudget,
            basePool,
            lotteryRewardPerWinner,
            lotteryWinnerCount,
            qualifyDeadline,
            settlementDeadline,
            seedCommit,
            platformFeeBps
        );
    }

    /**
     * @dev Sponsor 在资格确认前取消任务，并拿回全部预算
     * 只允许在 FUNDED 状态取消
     */
    function cancelTask(bytes32 taskId) external whenNotPaused nonReentrant onlySponsor(taskId) {
        TaskConfig storage t = tasks[taskId];

        require(t.status == TaskStatus.FUNDED, "Task not FUNDED");

        uint256 amount = t.totalBudget;
        t.status = TaskStatus.CANCELLED;

        // 全额退回 sponsor
        IERC20(t.token).safeTransfer(msg.sender, amount);

        emit TaskCancelled(taskId, msg.sender, amount);
    }

    /**
     * @dev Operator 完成资格确认
     * @param taskId                    任务 ID
     * @param qualifiedCount            合格人数
     * @param qualifiedRoot             合格名单 root
     * @param qualificationManifestHash 链下资格清单哈希
     */
    function finalizeQualification(
        bytes32 taskId,
        uint32 qualifiedCount,
        bytes32 qualifiedRoot,
        bytes32 qualificationManifestHash
    ) external onlyRole(OPERATOR_ROLE) whenNotPaused {
        TaskConfig storage t = tasks[taskId];

        // 必须在 FUNDED 状态下才能确认资格
        require(t.status == TaskStatus.FUNDED, "Task not FUNDED");

        // 必须等到资格截止之后
        require(block.timestamp >= t.qualifyDeadline, "Qualification too early");

        // 资格清单哈希不能为空
        require(qualificationManifestHash != bytes32(0), "Empty qualification manifest");

        // 如果有人合格，则 root 不能为空；0 人合格时允许空 root
        require(qualifiedCount == 0 || qualifiedRoot != bytes32(0), "Empty qualifiedRoot");

        qualifications[taskId] = Qualification({
            qualifiedCount: qualifiedCount,
            qualifiedRoot: qualifiedRoot,
            qualificationManifestHash: qualificationManifestHash,
            finalizedAt: uint64(block.timestamp)
        });

        // 进入 QUALIFIED 状态
        t.status = TaskStatus.QUALIFIED;

        emit QualificationFinalized(taskId, qualifiedCount, qualifiedRoot, qualificationManifestHash);
    }

    /**
     * @dev Operator 完成任务结算
     * @param taskId                  任务 ID
     * @param seedReveal              提交 seed reveal，用于和 commit 对上
     * @param entropyRef              外部熵来源引用
     * @param entropyValue            外部熵值
     * @param resultManifestHash      结果清单哈希
     * @param payoutAmount            要分给用户的总金额
     * @param refundableAmount        可退 Sponsor 的金额
     * @param baseRewardPerQualified  每个合格用户的基础奖励
     * @param actualWinnerCount       实际中奖人数
     */
    function settleTask(
        bytes32 taskId,
        bytes32 seedReveal,
        uint64 entropyRef,
        bytes32 entropyValue,
        bytes32 resultManifestHash,
        uint96 payoutAmount,
        uint96 refundableAmount,
        uint96 baseRewardPerQualified,
        uint16 actualWinnerCount
    ) external onlyRole(OPERATOR_ROLE) whenNotPaused {
        TaskConfig storage t = tasks[taskId];
        Qualification memory q = qualifications[taskId];

        // 必须已经资格确认完成
        require(t.status == TaskStatus.QUALIFIED, "Task not QUALIFIED");

        // 必须在结算截止前结算
        require(block.timestamp <= t.settlementDeadline, "Settlement expired");

        // 结果清单不能为空
        require(resultManifestHash != bytes32(0), "Empty result manifest");

        // reveal 必须和创建任务时的 seedCommit 对上
        require(keccak256(abi.encodePacked(seedReveal)) == t.seedCommit, "seedReveal mismatch");

        uint96 platformFeeAmount = _calculatePlatformFee(t.totalBudget, t.platformFeeBps);

        // 发奖金额 + 退款金额 + 平台费 = 总预算
        require(
            uint256(payoutAmount) + uint256(refundableAmount) + uint256(platformFeeAmount) == t.totalBudget,
            "Sum mismatch"
        );

        // 实际中奖人数不能超过计划中奖人数
        require(actualWinnerCount <= t.lotteryWinnerCount, "Too many winners");

        if (q.qualifiedCount == 0) {
            // 0 个合格用户时，每人基础奖励必须为 0
            require(baseRewardPerQualified == 0, "baseReward should be 0");
            // 0 个合格用户时，不应有中奖者
            require(actualWinnerCount == 0, "winnerCount should be 0");
            // 0 个合格用户时，发奖总额必须为 0，资金应全额退回
            require(payoutAmount == 0, "payout must be 0 if no one qualified");
        } else {
            // 基础奖励总额不能超过 basePool
            require(
                uint256(baseRewardPerQualified) * uint256(q.qualifiedCount) <= t.basePool,
                "base allocation overflow"
            );

            // 实际中奖人数不能大于合格人数
            require(actualWinnerCount <= q.qualifiedCount, "winnerCount exceeds qualified");
        }

        uint256 expectedPayout = uint256(baseRewardPerQualified) * uint256(q.qualifiedCount)
            + uint256(t.lotteryRewardPerWinner) * uint256(actualWinnerCount);
        require(uint256(payoutAmount) == expectedPayout, "Payout mismatch");

        settlements[taskId] = Settlement({
            seedReveal: seedReveal,
            entropyRef: entropyRef,
            entropyValue: entropyValue,
            resultManifestHash: resultManifestHash,
            baseRewardPerQualified: baseRewardPerQualified,
            actualWinnerCount: actualWinnerCount,
            payoutAmount: payoutAmount,
            refundableAmount: refundableAmount,
            settledAt: uint64(block.timestamp),
            platformFeeAmount: platformFeeAmount
        });

        // 把这次结算中应发给用户的金额，放进“已结算但还没分配到 root”的池子
        settledButUnallocated[t.token] += payoutAmount;

        // 只有成功结算才计提平台费；取消和超时强退不收费。
        if (platformFeeAmount > 0) {
            platformFeeBalances[t.token] += platformFeeAmount;
            emit PlatformFeeAccrued(taskId, t.token, platformFeeAmount, t.platformFeeBps);
        }

        // 有退款则进入 REFUNDABLE，否则直接 SETTLED
        t.status = refundableAmount > 0 ? TaskStatus.REFUNDABLE : TaskStatus.SETTLED;

        emit TaskSettled(
            taskId,
            seedReveal,
            entropyRef,
            entropyValue,
            resultManifestHash,
            payoutAmount,
            refundableAmount,
            baseRewardPerQualified,
            actualWinnerCount,
            platformFeeAmount
        );
    }

    /**
     * @dev Sponsor 领取退款
     * @param taskId    任务 ID
     * @param recipient 退款接收地址
     */
    function claimRefund(bytes32 taskId, address recipient) external whenNotPaused nonReentrant onlySponsor(taskId) {
        TaskConfig storage t = tasks[taskId];
        Settlement storage s = settlements[taskId];

        require(recipient != address(0), "bad recipient");
        require(t.status == TaskStatus.REFUNDABLE, "Not REFUNDABLE");
        require(s.refundableAmount > 0, "No refund");

        uint256 amount = s.refundableAmount;

        // 先清零，防止重复领取
        s.refundableAmount = 0;
        t.status = TaskStatus.REFUNDED;

        IERC20(t.token).safeTransfer(recipient, amount);
        emit RefundClaimed(taskId, msg.sender, recipient, amount);
    }

    /**
     * @dev Sponsor 在结算超时后强制取回资金
     * 当 Operator 未在 settlementDeadline 前完成结算，
     * Sponsor 可直接取回全部预算，避免资金永久锁定。
     * 适用于 FUNDED 或 QUALIFIED 状态下超时的情况。
     */
    function emergencyRefund(bytes32 taskId) external whenNotPaused nonReentrant onlySponsor(taskId) {
        TaskConfig storage t = tasks[taskId];

        // 只有 FUNDED 或 QUALIFIED 状态才允许超时退款
        require(
            t.status == TaskStatus.FUNDED || t.status == TaskStatus.QUALIFIED,
            "Not eligible for emergency refund"
        );

        // 必须已经超过结算截止时间
        require(block.timestamp > t.settlementDeadline, "Settlement deadline not passed");

        uint256 amount = t.totalBudget;
        t.status = TaskStatus.CANCELLED;

        IERC20(t.token).safeTransfer(msg.sender, amount);

        emit TaskCancelled(taskId, msg.sender, amount);
    }

    /**
     * @dev Operator 发布一个待激活 root
     * @param token            token 地址
     * @param rootId           root 版本号，必须严格递增
     * @param merkleRoot       新 root
     * @param epochDeltaAmount 本次新增分配金额
     * @param delayWindow      延迟审核窗口
     * @param manifestHash     链下明细哈希
     */
    function publishPendingRoot(
        address token,
        uint64 rootId,
        bytes32 merkleRoot,
        uint128 epochDeltaAmount,
        uint64 delayWindow,
        bytes32 manifestHash
    ) external onlyRole(OPERATOR_ROLE) whenNotPaused {
        require(tokenWhitelist[token], "Token not allowed");

        // 新 rootId 必须比当前 active root 更大
        require(rootId > activeRoots[token].rootId, "rootId too small");

        require(merkleRoot != bytes32(0), "Empty root");
        require(manifestHash != bytes32(0), "Empty manifest");

        // 同一 token 只允许一个 pending root
        require(pendingRoots[token].rootId == 0, "Pending root exists");

        require(epochDeltaAmount > 0, "Zero delta");

        // 不能超过当前“已结算但未分配”的金额
        require(epochDeltaAmount <= settledButUnallocated[token], "Delta exceeds unallocated");

        // 审核期结束时间
        uint64 activateAfter = uint64(block.timestamp + delayWindow);

        pendingRoots[token] = PendingRoot({
            rootId: rootId,
            token: token,
            merkleRoot: merkleRoot,
            epochDeltaAmount: epochDeltaAmount,
            activateAfter: activateAfter,
            manifestHash: manifestHash,
            cancelled: false
        });

        // 先从未分配池扣掉，表示已经准备放进一个新 root
        settledButUnallocated[token] -= epochDeltaAmount;

        emit PendingRootPublished(token, rootId, merkleRoot, epochDeltaAmount, activateAfter, manifestHash);
    }

    /**
     * @dev Guardian 取消 pending root
     * 取消后金额退回 settledButUnallocated
     */
    function cancelPendingRoot(address token, uint64 rootId) external onlyRole(GUARDIAN_ROLE) {
        PendingRoot memory p = pendingRoots[token];

        require(p.rootId > 0, "No pending root");
        require(p.rootId == rootId, "Root mismatch");

        // 金额退回未分配池
        settledButUnallocated[token] += p.epochDeltaAmount;

        // 删除待激活 root
        delete pendingRoots[token];

        emit PendingRootCancelled(token, rootId);
    }

    /**
     * @dev 任何人都可以在延迟窗口后激活 root
     */
    function activateRoot(address token, uint64 rootId) external whenNotPaused {
        PendingRoot memory p = pendingRoots[token];
        ActiveRoot memory a = activeRoots[token];

        require(p.rootId > 0, "No pending root");
        require(p.rootId == rootId, "Root mismatch");

        // 必须等审核窗口结束
        require(block.timestamp >= p.activateAfter, "Audit window active");

        // 激活新 root，并累计 totalAllocated
        activeRoots[token] = ActiveRoot({
            rootId: p.rootId,
            merkleRoot: p.merkleRoot,
            totalAllocated: a.totalAllocated + p.epochDeltaAmount,
            manifestHash: p.manifestHash
        });

        // 删除 pending root
        delete pendingRoots[token];

        emit RootActivated(
            token,
            rootId,
            activeRoots[token].merkleRoot,
            activeRoots[token].totalAllocated,
            activeRoots[token].manifestHash
        );
    }

    /**
     * @dev 用户领取奖励
     * @param token            token 地址
     * @param rootId           当前生效 rootId
     * @param cumulativeAmount 用户在该 root 中的累计应得金额
     * @param merkleProof      Merkle 证明
     * @param recipient        实际收款地址
     *
     * 这里采用“累计金额”模型：
     * - root 中存的是用户累计可领总额
     * - 合约记录用户之前已经领了多少
     * - 本次只能领 delta = cumulativeAmount - alreadyClaimed
     */
    function claim(
        address token,
        uint64 rootId,
        uint128 cumulativeAmount,
        bytes32[] calldata merkleProof,
        address recipient
    ) external whenNotPaused nonReentrant {
        require(recipient != address(0), "bad recipient");

        ActiveRoot memory a = activeRoots[token];
        require(a.rootId > 0, "No active root");
        require(a.rootId == rootId, "Root mismatch");

        // 标准 double-hash leaf：内层 abi.encode 保证类型边界，外层 keccak256 防 preimage 攻击
        bytes32 leaf = keccak256(bytes.concat(
            keccak256(abi.encode(msg.sender, token, rootId, cumulativeAmount))
        ));

        // 验证 proof
        require(MerkleProof.verify(merkleProof, a.merkleRoot, leaf), "Invalid proof");

        // 已领取累计值
        uint256 alreadyClaimed = claimed[msg.sender][token];
        require(cumulativeAmount > alreadyClaimed, "Nothing to claim");

        // 本次可领取增量
        uint256 deltaAmount = cumulativeAmount - alreadyClaimed;

        // 更新已领累计值
        claimed[msg.sender][token] = cumulativeAmount;

        // 转账
        IERC20(token).safeTransfer(recipient, deltaAmount);

        emit Claimed(msg.sender, token, recipient, rootId, cumulativeAmount, deltaAmount);
    }

    /**
     * @dev 查看在给定 cumulativeAmount 下，还能再领多少
     */
    function claimableDelta(address account, address token, uint128 cumulativeAmount) external view returns (uint256) {
        uint256 alreadyClaimed = claimed[account][token];

        if (cumulativeAmount <= alreadyClaimed) {
            return 0;
        }

        return cumulativeAmount - alreadyClaimed;
    }

    function _calculatePlatformFee(uint96 totalBudget, uint16 feeBps) internal pure returns (uint96) {
        return uint96((uint256(totalBudget) * uint256(feeBps)) / FEE_DENOMINATOR_BPS);
    }
}
