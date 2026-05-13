// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// 仅用于让 forge build 生成 ERC1967Proxy 的 ABI/字节码工件，
// 供 Python 测试脚本通过 web3.py 部署 UUPS 代理合约。
import "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
