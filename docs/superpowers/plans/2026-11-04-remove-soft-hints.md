# 删除 SLS 到 Z3 的软初值提示

**目标：** 物理移除自定义软初值的映射、传递、选项和日志，保留约5秒的无提示SMT预试及其他求解能力。

**范围：** 清理 `incumbent_hint` API 参数和 `set_initial_value` 调用，拒绝退役的 `--smt-exchange=hints`，保留 `--smt-exchange=backend`。还原当初仅为提示转交增加的Z3 tactic补丁；保留Z3原有通用初始化API及无界方向局部搜索反馈。当前运行脚本统一使用backend模式。

历史实验快照、日志和归档材料保留，不提交完整实验或Git commit。

- [x] 删除C++初值映射、调用链、模式及遥测。
- [x] 清理Python白名单、当前脚本和说明。
- [x] 还原专用Z3提示转交补丁，确认无关原生代码不变。
- [x] 迁移测试，先确认旧参数仍可用的RED，再验证删除后的拒绝行为。
- [x] 重新编译并完成原有及必要组件回归。
- [x] 预演node1–3各180并行、每例1200秒、全部6310例。
- [x] 独立审查：API顺序、完整查询、SAT校验、暂停续跑和ray反馈均无阻塞问题。
- [x] 给出node1–3完整运行命令，不替用户提交实验。

验证记录：

- 完整构建日志：`/tmp/omt-remove-soft-hints-build.log`；shell和libz3均成功。
- 新可执行文件SHA256：`77041952ee7beed96a563b3b74d211758151454b9b541bfff433aca37857dc62`。
- 新增删除行为及相关CLI：26项通过，日志`/tmp/omt-soft-changed-cli.log`。
- 原有提交回归：68项，67项通过，1项既有可选数据集检查未启用；日志`/tmp/omt-soft-committed.log`。
- Adapter/Slurm：49项通过，日志`/tmp/omt-soft-adapter-slurm.log`。
- 真实5秒SMT未知后回退、同界恢复和50ms共享预算：通过，日志`/tmp/omt-soft-fallback.log`。
- Round2及共享advisory预算组件通过，日志`/tmp/omt-soft-test_round2_advisory.log`和`/tmp/omt-soft-test_omt_advisory_budget.log`。后者修正了一个旧测试的时序假设：关闭预算分配时也可能在原时限内真正完成证明，仍检查原查询、默认check和总预算。
- 当前libz3上的BV组件五种模式（模型、guard、关闭、暂停计时、宽度/LIA）全部通过；报告`/home/lbh/.tmp/omt-soft-bv-current-qrry7dgj/evidence/report.json`，libSHA256为`fd11a001a717c920eccee3ce3d5be1d940647db431ebade6722c0e1719e171e1`。历史测试脚本的build010固定库摘要仅在内存运行时绕过，仓库中的历史摘要锁未改。
- 主脚本`bash script/run_full_node1_3.sh --dry-run`输出6310例、各180并行、合计540、1200秒；未提交实验。
- `git diff --check`及当前入口的`bash -n`通过。

运行命令：

```bash
cd /home/lbh/OMT
bash script/run_full_node1_3.sh
```

每轮新建`/data/shared/lbh/OMT/result/NIA_<北京时间戳>/`，完整结束后自动生成CSV与`summary.md`。
