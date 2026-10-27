# Z3 调度实验计划

> 按现有用户授权连续实施；不提交 Git。仅在每次提交前确认 server、node1–5 的空闲状态。

**目标：** 相对 `NIA_20261026_125753_104828`（6310 例，A1+A2=5683，逐实例更强对手负85），一组完整独立实验达到 A1+A2>=5734，负<=74。每例1200s，每台最多180并行；不读取对手值决定求解行为。

**设计：** 保留停滞50s后的首次30s证明。失败后比较连续SLS窗口50、250、650s，再将余下预算交给单次连续Z3，避免重复冷启动。新目标恢复短探；已SAT但不能导入模型的同一目标不再重复证明。只有严格改进查询UNSAT才能声明A2，SAT模型仍必须完整验证。`--optimality-search-window=0`保留原调度用于复现。

**范围：** 修改调度器与参数传递；保持SLS、约束转换、无界证明算法不变。另修复模型发布和结果事件的截止竞争，保留最后已发表事件对应模型，不能提升未发表目标或延长时间限。

- [x] 核对基准与空闲节点；独立分析未完成和落后实例。
- [x] 定位记录竞争，添加确定性回归并修复。
- [x] 新增调度状态机测试，先确认旧实现不满足新状态机。
- [x] 实现windowed调度和CLI/manifest参数，验证旧调度兼容及SAT/UNSAT语义。
- [x] 在相同定向集合（同值待证明121、负85、已解决control至少100）比较三个窗口，每个候选冻结二进制、脚本与配置。
- [x] 每次提交前保存sinfo/squeue检查；只使用当时空闲节点，提交后追踪同一job handle。
- [x] 在全部未解决627及control上验证候选，调查明显回退；必要时继续调度实验。
- [x] 最佳候选完整跑6310，独立验证模型、证书、列表完整性、1200s/180限制，再按相同competitor.csv比较。
- [x] 只有同一组完整结果满足两个门槛且验证通过才完成目标；否则持续迭代，保留失败实验。

**验证：** `make -C NIA_LS/src test-omt-optimality-schedule test-optimality-optimization`；相关Python runner/Slurm/verify回归；最终 `script/verify_optimize.py --expected-cases 6310`（依当前CLI核对参数）。报告仍保留用户要求的三张统计表，实验过程另存实验记录。

## 已提交的首轮定向实验

2026-10-26 北京时间16:21，提交前逐次检查全部六节点；初始均idle，按互不重叠节点分配。

| 作业 | 窗口(s) | 节点 | 目录 |
| --- | --- | --- | --- |
| 87093 | 50 | node1,server | `/data/shared/lbh/OMT/result/NIA_20261026_162158_630577` |
| 87094 | 250 | node2,node3 | `/data/shared/lbh/OMT/result/NIA_20261026_162158_843934` |
| 87095 | 650 | node4,node5 | `/data/shared/lbh/OMT/result/NIA_20261026_162159_054729` |

每组360例、每台180并行、1200s；冻结native SHA `44529e7347395a8f277d54de035c9b8a4457255a7aa8f8d31e98ea7a1d7f7e5a`，首次50s/30s，未启用补充初解Z3。运行记录 `/home/lbh/.tmp/nia-schedule-20261026/pilots.json`。分析器同目录 `compare_pilots.py`，未完成时只报告partial，不将未测部分估计视作全量结果。

## 可选无初解调度

现有10s initializer是小域线性化+自研LIA局搜，不是原公式Z3 SMT check。新增默认关闭的 `--initial-z3-after`（计划300s）/`--initial-z3-timeout`（默认120s）允许长期无初解时对原硬公式调用一次Z3。与最优性查询共用hard deadline和模型桥接，但使用显式独立API `find_omt_feasible`；原式UNSAT永不声明A2，只有验证的SAT模型作为普通incumbent。初解一旦存在不调用，SLS算法不改。后续试验需包含65个C及45个>=300s才找到初解的保护样本。

## 用户追加：保留同一界限的 Z3 搜索状态

新增默认关闭的 `--optimality-resume`，同一 incumbent 的严格改进查询在时间片结束后 SIGSTOP 并确认暂停；下一时间片 SIGCONT 继续原搜索。目标改善立即 kill/reap 旧查询；新界限重新创建 worker/context。子进程不设置会结束搜索的 slice timeout，父进程监管每个wallclock时间片，整例外层1200s仍包含SLS和暂停时间。完成结果若错过片尾保留在匿名FD，下一有效片内才读取；所有SAT模型及UNSAT证据的原验证条件保持。会话reset和析构必须杀死并回收停止的child。

首轮三个360例实验均独立验证 `ok=true`，无真正error。50/250/650窗口净成功增量分别+31/+21/+0，净负例减少19/17/14。正常wall_timeout说明不能算error。最强50窗口尚不足完整目标，接下来对比persistent原adaptive节奏与persistent+50窗口；另独立验证初解Z3补充，避免同时变更多项后无法定位收益。

## 第二轮已提交

暂停worker生命周期审查发现父进程异常退出会遗留暂停子进程。已取消短暂启动的87099/87100，保留 `experiment_cancelled.json`；正式回归先RED，随后仅persistent child加 `PR_SET_PDEATHSIG=SIGKILL` 和fork-parent PID竞态检查，独立GREEN后重交。顺便修复临时提交器连续提交时Slurm idle状态短暂滞后造成的重复节点选择：实时job handle仍在队列即视作预留节点。

- 87098：`NIA_20261026_165506_587331`，server，110例，无初解300s后调用Z3最多120s，window50，不resume。
- 87101：`NIA_20261026_170304_120251`，node1,node2，360例，原adaptive节奏+resume。
- 87102：`NIA_20261026_170304_491364`，node4,node5，360例，window50+resume。

node3当时被其他用户job87097占用，以上每次提交均重新检查并避开。两个persistent实验binary SHA `d899fa755ab04715e0d81d21e233ac07c6a48868bbd48085f3b6233534b88c14`；source110使用此前binary `8ca732669c496dbfd35b652300432c7eeb98dc15d6e58e0dc54328b8553f3f0a`，resume关闭。完整脚本回归428通过；修复后native API/生命周期、17真实native以及调度状态机通过。

统一比较：`PYTHONDONTWRITEBYTECODE=1 python3 /home/lbh/.tmp/nia-schedule-20261026/refresh_comparisons.py`。进展及后续命令位置同目录progress.json、submit_stage.py，任何部分结果或未测部分估计均不视为完整目标达成。

## 第二轮结果与组合试验

87098独立审计110/110通过：基准13A1/28A2/4B/65C，本轮35/48/7/20，成功41→83（+42），负例恢复37/新增1。两个DivMinus原A1退化为C；保留其失败，未把不同轮次拼接。

87101与87102各360/360独立审计通过：resume原节奏成功+30、负净减少24；resume+50窗口成功+27、负净减少18。选resume原节奏，与初解300/120组合。真实design例仅1start+4resume，在812s独立证明9216最优；已确认state复用生效。

87103：`/data/shared/lbh/OMT/result/NIA_20261026_172520_509103`，768例、六节点、180/node、1200s，resume=true、window=0、initial300/120。提交前六台均idle。自动监控与完成后审计进程session6052，脚本wait_and_verify.py及状态monitor_status.json。最后全量验收使用临时audit_goal.py，必须真实单轮6310、同一competitor及列表、独立验证，再检查5734/74两门槛；errors如实列出不当作既得解。

## 768例验证通过，已启动完整实验

87103独立审计768/768通过，744模型、91无界证书、120有限最优，case_failures=0。基准63/78/562/65→本轮91/120/533/24，成功净+70、负例净减少47。未测部分保持基准的5753/38仅为选参估计，不能代替完整实验。

87104：`/data/shared/lbh/OMT/result/NIA_20261026_175345_895315`，真实6310例、六节点、180/node、1200s；同binary d899fa...、resume=true、window=0、initial300/120，不按对手目标早停。2026-10-26北京时间17:53提交，之前再次核对六节点均idle。

## 全量验收通过

87104全部6310例结束，2026-10-26 UTC10:24:57监控完成验收，audit_id为
`f048edbe-9032-4b90-a07f-96dcfc938c85`，退出码0。最终状态2353A1/3390A2/540B/27C，
A1+A2=5743，相对5683净增加60；对逐实例更强对手为2137胜/4130平/43负，相对85负净减少42。
同一完整单轮达到>=5734成功、<=74负两个门槛。

`verification.json` 为该次独立调用生成，6310例清单与6283最终模型检查通过，
2353无界证书、3390有限最优报告、事件及总预算核验通过，case_failures=0；
有限最优检查严格改善UNSAT报告，不重放SMT证明，也不额外证明转换等价性。
`same_run_consistency.json` 对六个rank与总表6310行逐字段核对通过。
`scheduling_comparison.json` 记录基准、同一competitor哈希、真实error=0、actual_complete_single_run=true、achieved=true。
所有比较使用精确整数及原min/max方向，同值不按证明状态打破平局。

最终推荐参数和可复现命令已写入NIA_LS/README.md，暂停恢复、初解查询和记录修复详见
NIA_LS/实现细节.md第16节；保留各轮失败/取消记录，未拼接结果，未提交Git。
