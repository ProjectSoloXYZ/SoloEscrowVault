// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// 仅用于让 forge build 生成 TimelockController 的 ABI/字节码工件，
// 供 Python 部署脚本通过 web3.py 部署治理合约。
import "@openzeppelin/contracts/governance/TimelockController.sol";
