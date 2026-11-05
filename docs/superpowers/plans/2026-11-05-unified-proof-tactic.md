# Unified proof tactic implementation plan

> **For agentic workers:** Implement the authorized design in this shared workspace; use independent test and review agents, with disjoint file ownership. Do not commit or submit full experiments.

**Goal:** 将 arith2 短尝试、扩展 BV 与默认后备改成同一个 Z3 tactic solver 的顺序回退，替代先后调用两个 solver.check 的做法。

**Architecture:** 完整原式和严格改善目标界仍只构建一次，供保存查询和 guarded/finite-cutset 辅助使用。辅助未解决后，组合 `or_else(try_for(and_then(with(smt, arith.solver=2), fail-if-undecided), smt_ms), try_for(and_then(qfnia-omt-bv, fail-if-undecided), bv_ms), default)` 并只检查一次；关闭的分支直接省略。SLS、SAT精确校验、目标界变化时重建worker、同界暂停恢复和实例总预算保留。

**Tech Stack:** Z3 5.1 C++ tactic API、C++17、现有 Python/C++ 回归与 Slurm 启动器。

用户已在上一轮讨论设计后明确要求实施。本轮不再请求设计批准，不改已有未提交的实验入口，不更改 competitor 和历史结果。

## Scope and contracts

- 修改 `z3-nia/sls/src/nia_omt_optimality.cpp`，保留公开函数签名及配置开关。
- `arith.solver=2` 只通过 `with` 配置第一分支，不向链根、BV或默认分支传递。
- `fail-if-undecided` 将未决返回转成失败，使 `or_else` 真正继续下一条；SAT/UNSAT结束组合检查。
- 第一个分支的毫秒数在上下文/断言准备后按5秒、主预算、advisory前缀剩余折算；不足1毫秒则跳过。主预算和外层worker仍限制整条链。
- `--isolate-exchange-context` 在后备链启用时隔离整条链，而不再仅隔离旧5秒solver；SAT模型翻译回原解析context后再走原有恢复与校验桥。
- 不把组合最终答案冒充SMT/BV分支答案。新日志 `omt_tactic_chain phase=configured/check/result smt_ms=... bv_ms=... fallback=default` 描述配置及整链检查；不声称跨tactic共享学习状态。
- guarded、有限覆盖及可选objective-slice保持现有辅助语义。guarded放松SAT不当作原式SAT。
- 4种开关组合继续有效；两项都关闭的普通默认solver路径保留，plain模式也保持。

## Tasks

- [x] 测试agent先扩展 `test_smt_exchange_fallback.cpp/.py`：使用真实lam3n1未决→默认可证例子，wrap公共check并确认旧实现为2次；有限和暂停续跑两种模式新合同均为1次。RED日志 `/tmp/omt-tactic-chain-red.log`。
- [x] root在确认RED后修改 `run_probe`：移除独立selected.check预试分支，懒构造组合solver；保持完整assertions、预算、SAT模型恢复和未知原因记录。
- [x] 测试agent迁移旧逐分支日志断言；保留源码查询、min/max宽整数和Bool模型、50ms总限、isolate、advisory前缀、BV开关/计时和暂停续跑回归。不用配置日志冒充实际执行分支证据。
- [x] root执行 `make -C z3-nia/build -j4`，记录日志和新二进制摘要。
- [x] 跑新增单check回归、现有SMT/round2/advisory/BV必要组件、相关CLI与原68项提交回归。已通过后不重复扩大测试。
- [x] 独立审查实际diff、预算和模型语义；无必须修复项。
- [x] 更新前端帮助、API注释与 `z3-nia/README.md`、`script/README.md`，明确新tactic链及isolate语义。提供node3–5各180并行、1200秒、完整6310例命令，不代用户提交。
- [x] `git diff --check`、脚本参数检查及最终验证记录。

## Verification commands

```bash
python3 z3-nia/sls/tests/test_smt_exchange_fallback.py --chain-only
make -C z3-nia/build -j4
python3 -m unittest discover -s z3-nia/sls/tests -p 'test_smt_exchange_cli.py'
python3 z3-nia/sls/tests/test_round2_advisory.py
python3 z3-nia/sls/tests/test_omt_advisory_budget.py
git diff --check
```

其他CLI/原提交回归及BV组件采用现有harness；BV历史build010摘要锁保持，不伪造当前库摘要。

## Accepted limits

`try_for`限制tactic执行，计时为软墙钟，SIGSTOP暂停会计入分支时限。设置阶段时会扣去已消耗准备时间，但不承诺毫秒级精确复现旧阶段返回时的单独迟到拒收；最终答案仍必须在外层证明窗口和实例时限内被接收。此改动是顺序portfolio的真实组合，不声称自动共享不同tactic的学习子句，也不保证性能净增。

## Native fallback lifetime regression and fix

Hanoi旧13秒回归在最初裸组合中失败。独立公共API最小矩阵确认：即使前置分支只是立即失败的 `fail`，`fail | default` 也会发生同样回退，不能归因arith2参数泄漏或计时取消。

最终仅在 `source/src/tactic/tactical.cpp` 中将 `or_else` 的栈目标备份改为 `optional<goal>`，最后分支前销毁；前支失败的恢复、异常处理和其它语义不变。独立代码审查确认最后分支已无备份使用路径，输入已独立持有断言和转换器引用。该单变量改动让同一公共API组合查询在8.12066秒证明UNSAT；实际round2组件回归为8.1397秒，原13秒合同不变。

曾尝试将普通solver工厂包装成尾tactic，未改善，已经完全删除该试验，不留额外factory或注册。最终原生源只保留上述备份生命周期变动。

构建日志：`/tmp/omt-tactic-chain-build.log`。
最终shell SHA256：`02d31e165ac08edbcdbea0bb62324198c5f61cd1867796dd1f2a82fec7e2f9db`。
最终lib SHA256：`ea1cc642a5b5de74b71ed88db154cd0e642f96c854f09076ab0f12053410a791`。
Hanoi证据：`/tmp/omt-tactic-chain-hanoi-verified/report.json`。

补充原生生命周期验证：`/tmp/omt-tactic-backup-validation.cpp`，匹配当前库运行，SAT模型转换、带标签UNSAT core和proof对象三项通过。

## Final verification

- `40`项CLI、`49`项adapter/Slurm全通过。
- 原`68`项提交回归：`67`通过、`1`个原有可选数据集检查未启用，没有新增skip。
- fallback完整组件、有限/暂停恢复各一次公共check、round2组件、advisory七模式、BV五模式均通过。
- Hanoi默认`3.21864s`、隔离组合链`8.1397s`，原`13s`要求不变，保存查询逐字一致。
- 状态汇总`/tmp/omt-tactic-chain-final-status.json`；原提交回归报告`/tmp/omt-tactic-chain-final-committed.json`。
- BV报告`/tmp/omt-tactic-chain-final-bv-kqm02hqw/evidence/report.json`记录匹配当前库的真实摘要。
- 独立最终审查未发现soundness、模型、proof/core、对象生命周期或预算阻塞项。
- `bash script/run_tactic_chain.sh --dry-run`确认node3,node4,node5、各180、合计540、6310例、1200秒，无正式提交。
- `git diff --check`与脚本语法检查通过，最终可执行文件摘要未变。

完整运行：

```bash
cd /home/lbh/OMT
bash script/run_tactic_chain.sh
```

运行后自动输出到新的`/data/shared/lbh/OMT/result/NIA_<北京时间戳>/`并生成CSV和`summary.md`。本轮没有运行完整性能实验，也没有Git提交。
