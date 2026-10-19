求解器安装与编译（核验日期：2026-10-11 UTC）

当前入口：
  competitor/bin/z3 -> z3-5.1.0/install/bin/z3
  competitor/bin/optimathsat -> optimathsat-1.7.4/install/bin/optimathsat

Z3 5.1.0 从官方稳定版源码完整编译。源码、下载哈希、CMake Release 构建日志、
共享库和 Python 绑定均保留在 z3-5.1.0/；详细说明见该目录 README.txt。

OptiMathSAT 的最新公开 Linux 包是 1.7.4，实际核心版本 1.7.4.b。
在本机编译了官方 C 入口并链接官方预编译核心 libmathsat.a，另编译官方优化 API 示例。
核心本身没有从源码构建，因为公开包不包含核心源码。详细证据见 optimathsat-1.7.4/README.md。

在 /home/lbh/OMT 下调用：
  ./competitor/bin/z3 --version
  ./competitor/bin/optimathsat -version

  timeout --kill-after=2s 30s ./competitor/bin/z3 -smt2 /data/shared/lbh/OMT/benchmarks/QF_NIA/calypto/problem-005895.cvc.1@0.smt2
  timeout --kill-after=2s 30s ./competitor/bin/optimathsat -optimization=true -preprocessor.toplevel_propagation=false -opt.strategy=bin /data/shared/lbh/OMT/benchmarks/QF_NIA/calypto/problem-005895.cvc.1@0.smt2

OptiMathSAT 的 -optimization=true 不可省略；非线性试跑同时关闭 toplevel_propagation。

重编译：
  python3 competitor/z3-5.1.0/build-source.py
  bash competitor/optimathsat-1.7.4/build/build-sdk.sh
不修改系统级安装；依赖本机已存在的 GCC/G++、CMake/Ninja、GMP 开发库。

真实 benchmark 共抽取 13 例（6 NRA、7 NIA），每求解器每例限时 30 秒、最多2个并发：
  Z3：NRA 1 sat/2 unsat/3 timeout，NIA 7 sat。
  OptiMathSAT：NRA 2 sat/2 unsat/2 timeout，NIA 6 sat/1 timeout。
没有解析或参数错误；只做运行检查，不以 sat 状态作为独立最优性证明。
完整结果见 ../result/solver-smoke-20261011T122741Z/REPORT.txt 与 results.json。

额外简单 NRA 探针发现 OptiMathSAT 目标/模型不一致：0<=x<=10、x*x>=9、min x
的最优值应为3；Z3返回3，OptiMathSAT返回0+epsilon与x=10。请查看记录后再用于最优解比较。

官方来源：
  https://github.com/Z3Prover/z3/releases/tag/z3-5.1.0
  https://optimathsat.disi.unitn.it/pages/download-js.html
  https://optimathsat.disi.unitn.it/pages/documentation.html
