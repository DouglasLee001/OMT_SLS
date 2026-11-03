# 删除 core 辅助 Z3 邻域路径

> **For agentic workers:** 使用 subagent-driven-development 按下列边界并行修改，主线程检查并运行验证。不提交 Git。

**Goal:** 按用户要求彻底删除 core 驱动的固定变量及逐步释放邻域，实现与已完成的关闭实验相同的求解功能。

**Architecture:** 移除独立邻域模块及优化循环中的调用、预算、状态和输出，删除相应 CLI 与 Python 适配入口。完整原公式的严格改善查询、SAT 模型验收回灌、无界认证和其他交换保持现有语义。历史实验目录、冻结二进制及论文材料不修改。

**Tech Stack:** C++17/GMP、Z3、Python unittest、CMake/Make、Bash。

### 1. 接口回归与实现删除

- [x] 修改 CLI 测试：历史邻域开关及时间参数均以未知选项拒绝，帮助与 JSON 不再包含邻域字段；测试完整最优证明与无界认证仍可运行。
- [x] 在删除前对当前二进制运行测试，记录预期失败。
- [x] 删除 `z3-nia/sls/src/nia_omt_core_neighborhood.{h,cpp}` 及独立模块测试，移除 `nia_omt_optimize.{h,cpp}` 的调用、计时、统计、状态和头文件。
- [x] 清理 `call.cpp`、`frontend/nia_frontend.cpp`、`sls/CMakeLists.txt`、`sls/src/Makefile` 中的开关与构建引用。

### 2. 实验入口与文档

- [x] 先修改适配器测试，验证所有旧邻域开关被拒绝，其余 solver options 仍正常传入优化阶段。
- [x] 删除 `z3-nia/experiment/runtime_adapter.py` 的邻域白名单及专属校验；更新 Slurm help 与现有相关测试，保留其他选项的快照验证。
- [x] 更新 `script/run_full_node1_3.sh` 及当前 README 示例，不再携带已删除参数；现有冻结关闭组启动脚本保持作为历史复现实验入口。

### 3. 构建、回归和审查

- [x] `make -C z3-nia/build -j4 shell`，确认构建配置已重新生成且不再链接邻域模块。
- [x] 运行 CLI/适配器/Slurm 相关测试及 `z3-nia/sls/tests/run_committed_regressions.py`。
- [x] 以小例验证 A2、SAT 模型回灌、无界路径；可用已有端到端测试覆盖。
- [x] 搜索生产源码、构建列表、活动脚本中残余邻域符号，审查 diff 与 `git diff --check`。
- [x] 记录通过与跳过数量、编译产物 hash，向用户概述；不自动提交或启动全集实验。

## 验证记录

- 删除前 RED：CLI 4项测试有8处预期失败；适配器3项测试有6处预期失败，均由旧功能仍存在导致。
- `make -C z3-nia/build -j4 shell`：通过，CMake已重新生成；链接命令不再包含邻域模块。
- 新CLI回归4项、适配器3项、其他交换适配器2项、嵌入式Slurm37项、通用Slurm15项：全部通过。
- 原有端到端回归68项：67通过，1项既有可选Hanoi数据集测试跳过；覆盖最优证明、SAT模型回灌、无界认证。报告 `/tmp/omt-remove-core-neighborhood-regressions.json`。
- 活动脚本语法、生产源码/前端/适配器残留检查、链接检查与 `git diff --check`：通过。
- 独立只读代码审查未发现缺陷。没有提交Git、启动新实验或改写已有实验。
- 新 `z3-nia/build/z3` SHA256：`c4d9c8f4153535fdaa2f706f0c55e4a6ab7de02b3511ddc0fccfb0b6bd617237`。
