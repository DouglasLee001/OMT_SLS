# 纯 Z3 严格改善证明路径

用户明确要求：暂时不使用此前增加的证明辅助，只用默认 Z3 求完整原式加严格改善界；提供完整实验脚本，由用户运行。

采用独立 `--plain-z3-proof` 模式，当前完整实验入口默认选择它。C++ 中明确跳过全部辅助阶段及自定义 BV tactic，只保留默认 `z3::solver`、完整硬约束、严格目标界与 `check()`。旧实验实现保留供复现，纯模式与辅助开关冲突时报错，避免悄悄夹带。SLS、方向 SLS 无界认证、证明时间调度、同界暂停恢复、SAT模型精确校验回灌不改。

- [x] 先写并运行纯模式CLI/API失败测试：默认Z3日志、保存查询完整、SAT回灌、UNSAT最优、无辅助阶段和冲突开关拒绝。
- [x] 修改 `nia_omt_optimality.{h,cpp}`、`nia_omt_optimize.{h,cpp}`、`call.cpp`、`nia_frontend.cpp` 接入纯路径；给输出标注实际纯模式。
- [x] 修改Python适配器允许纯模式并拒绝混用辅助开关；纯模式运行配置关闭默认finite-cutset。
- [x] 新增 `script/run_plain_z3.sh`：server、node4、node5，各180并行、1200秒、96000MB；更新完整入口和文档。
- [x] 编译完整shell，运行CLI/adapter/Slurm回归及原68项端到端回归，核对SAT回灌和无界路径。
- [x] dry-run确认6310例及资源；独立代码审查；不提交Git，不启动正式实验。

验证记录：

- CLI RED：旧二进制7项测试有18处预期失败（新开关不存在），随后补充BV=0冲突子项。
- Adapter RED：旧实现不支持新模式，观察到预期失败；新适配器4项通过，另外两套适配器回归2项和3项通过。
- Slurm配置RED：纯模式仍开启cutset的两项断言失败；修改后40项回归通过。
- `make -C z3-nia/build -j4 shell` 通过，日志 `/tmp/omt-plain-z3-build.log`。
- 实际新二进制纯模式CLI7项通过：min/max完整查询重放、SAT回灌、UNSAT最优、冲突参数拒绝及SLS无界认证。
- 原端到端68项：67通过，1项既有可选Hanoi数据集测试跳过。报告 `/tmp/omt-plain-z3-regressions.json`。
- 新脚本dry-run确认server/node4/node5，每台180、共540并行，6310例、1200秒；未写实验目录或提交任务。
- 独立只读代码审查未发现缺陷；脚本语法、`git diff --check`通过。
- 可执行文件SHA256：`81d9404d14f19da680a56480e1a75a5c4fec07e9520f8dfb94678f9205a9e12d`；报告hash与当前文件一致，全部修改的C++源码均早于该构建产物。
- 当前完整脚本默认选择纯模式；旧辅助实现及冻结对照脚本保留历史语义。没有提交Git或启动正式实验。
