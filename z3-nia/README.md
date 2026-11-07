# Z3 5.1 内嵌 NIA_LS

本目录从 `competitor/z3-5.1.0` 单独 `cp` 得到。SLS 源码位于 `sls/src/`，集成基线来自 Git 提交 `6614f3c1ea13539918b6f641c64446b753e6db0e`。来源及原文件校验和见 `embedding-provenance.json`；当前工作树在该基线上增加了下面单独开关的深度交互原型，没有重用旧的不可靠回调实现。

算法解释、贡献草稿、研究方向和相关历史方案已统一整理到
[writing_paper 材料索引](../writing_paper/materials/README.md)。
方法部分按“整体伪代码＋三个贡献章节”组织，直接阅读 [LaTeX 方法稿 PDF](../writing_paper/main.pdf)，
正文和编译方式见 [论文写作目录](../writing_paper/README.md)。

2026-11-02：无界方向后端已替换为纯局部搜索，并加入搜索反馈指导的固定集选择；原运行命令继续适用。[实现与历史 A1 回归](../writing_paper/materials/无界方向SLS替换实现与回归.md)。

2026-11-03：已删除core驱动的固定变量及逐步释放邻域模块、命令行参数和统计字段。
当前版本继续在完整原式上检查严格改善查询，以UNSAT证明有限最优；SAT模型经校验后回灌搜索。
无界方向的SLS构造及精确认证、convex-exchange和smt-exchange=hints继续保留。
以下旧轮次结果与冻结二进制配置属于历史记录，不能代表当前代码或可执行文件。

2026-11-04：已从活跃源码物理删除额外的目标凸切线模块，包括普通切线、整数对角及整数仿射变种，
以及相应选项、遥测、证书检查器、专属测试和构建依赖。
当前完整实验入口为 `bash script/run_no_convex.sh`，保留旧入口名但使用当前构建；
guarded-product、finite-cutset、无提示SMT预试、BV120、默认Z3后备路径及同界暂停恢复继续保留。
`run_full_node1_3.sh` 使用同样的完整配置，默认节点为node1–3。
纯Z3证明仍可通过 `run_plain_z3.sh` 单独对照，它会跳过额外辅助。

Z3内核自身的乘积切平面、符号和顺序引理保留，见
[source/src/math/lp/nla_tangent_lemmas.cpp](source/src/math/lp/nla_tangent_lemmas.cpp)。
这些是内置非线性算术模块的局部线性细化，与已删除的全目标凸二次外逼近不是同一个模块。
以下旧轮次、冻结二进制和日志保留其历史配置，不代表当前可接受的参数。

2026-11-04后续清理：已物理删除SLS到Z3的软初值映射、传递接口、hints模式和专属测试，
并还原当初专门为提示转交增加的Z3 tactic补丁。Z3标准初始化API和无界方向的SLS反馈保留。
当前完整脚本统一使用 `--smt-exchange=backend`，保留约5秒无提示SMT预试、BV120、
guarded、有限覆盖、SAT模型回灌及暂停续跑。`--smt-exchange=hints` 不再接受。
node1–3的运行命令为 `bash script/run_full_node1_3.sh`。

## 当前证明后备：一条Z3 tactic链

2026-11-05：将原先独立的5秒SMT检查与BV/default后备组合为同一个tactic solver的
一次 `check()`。完整硬约束和严格改善目标界保持一致；guarded-product、有限覆盖及
可选source-slice仍先按各自原有语义尝试，未解决时进入这条后备链：

```text
or_else(
  try_for(with(smt, arith.solver=2) + fail-if-undecided, 至多5秒),
  try_for(qfnia-omt-bv + fail-if-undecided, BV配置时限),
  default
)
```

`--smt-exchange=backend` 启用首分支；`--optimality-bv-seconds=120` 启用第二分支，
设0则省略。两项都关闭时继续使用普通默认solver。参数仅作用于对应分支，
未决通过 `fail-if-undecided` 转为失败，`or_else` 恢复原目标后尝试下一条。
这是真正的单次组合检查，不再先后调用两个solver的check；不同tactic不自动共享学习子句。

`--isolate-exchange-context` 现在隔离整条后备链，而非只隔离旧5秒检查。
SAT模型统一翻译回原解析上下文，再经完整原式、目标值和NIA_LS模型恢复校验。
外层证明轮的预算、自适应调度、同目标界的暂停恢复、新目标界的重建及实例1200秒总限保留。
首分支的有效毫秒数在AST准备后按5秒、主预算和共享advisory前缀剩余取快照。
随后由Z3的 `try_for` 限制分支执行；组合solver构造及模型恢复继续受外层总预算约束。
这与旧预试“包含全部构造、返回时再单独拒收前缀迟到结果”的计时并非毫秒级等价。
分支计时仍为含暂停的软墙钟，不额外增加外层证明轮或实例总时限。

日志使用 `omt_tactic_chain phase=configured/check/result smt_ms=... bv_ms=...`，
只描述组合配置、实际整链检查及最终答案；不把最终答案归因于无法观测的某个子分支。
旧逐分支日志不再输出。历史实验结果和冻结二进制仍属于各自旧版本。

另在Z3原生 `or_else` 中缩短目标备份的生命周期：前面分支失败时仍按原逻辑恢复，
进入最后一条分支后释放已不再需要的备份。原目标已独立持有断言、proof、dependency
和converter引用；最后分支不再有下一条回退路线，因此无需继续持有备份。
这避免旧目标在后备搜索期间持续保活，恢复了Hanoi既有13秒回归要求：
同一组合查询从13秒未决恢复为约8.1秒证明UNSAT。此处是内存生命周期调整，
没有加入面向Hanoi的参数、规则或特殊分支。

当前构建已通过40项CLI、49项adapter/Slurm、原68项提交回归（1项原有可选数据集
检查未启用）及fallback/round2/advisory/BV组件检查；SAT模型、UNSAT core和proof对象
的原生生命周期检查也通过。尚未重跑6310例完整性能实验。

完整配置继续使用原命令和参数；node3–5、各180并行、每例1200秒可运行：

```bash
cd /home/lbh/OMT
bash script/run_tactic_chain.sh
```

## 当前 SLS 与目标邻域消融

2026-11-07：已从活跃源码物理删除因子覆盖选变量、线性化后调用LIA-SLS的分块修复，
包括实现文件、调用调度、构建依赖、专属测试、命令行开关和统计字段。
随后已从活跃源码物理删除平台压力引导，包括目标改善受阻压力、同值中性移动、
平台代表解状态、调用入口、专属开关及统计字段。
目标联动邻域、NIA严格界修复、大目标探测和全部证明后端继续保留。
当前 `build/z3` 已重新编译，确认不含已删除模块的符号；原68项搜索/证明回归
（1项原有可选数据集检查跳过）、CLI/脚本检查以及目标投影和决策变量定向检查通过。
此前四组实验及其冻结二进制属于历史记录，见
[三项消融结果](/data/shared/lbh/OMT/result/NIA_20261105_230638_652473/sls_ablation_summary.md)。

当前保留默认开启的目标邻域搜索开关。用同一可执行文件运行
当前完整版和目标邻域消融版；每组使用全部6310例（original 5744、new 566），
默认node3–5、每台180并行、单例1200秒（含导入）。证明无界或有限最优后仍提前结束。

| 配置 | 唯一关闭的模块 | 原生开关 |
| --- | --- | --- |
| `full` | 无，作为本轮对照 | 无 |
| `no-objective-neighborhoods` | 常规可行改善邻域中的等式联动、投影后二次候选、双变量交换、差分联合修复、目标前沿及析取联合修复 | `--no-sls-objective-neighborhoods` |

目标邻域是常规目标改善算子的**成组消融**，不能据此区分其中单个算子的贡献。
等式投影也是其他模块的共享工具，因此初解/不可行状态修复、
重启时的目标kick和布尔守卫宏移动、已接受方向的外推仍可使用投影；
关闭该开关不会全局删除 `State::project`。
严格改善操作和普通重启继续保留。证明阶段的有限覆盖枚举与已删除的搜索阶段
分块修复是不同模块，仍保留。

两组的种子策略、初解流程、无界证书、guarded-product、有限覆盖、
arith2/BV/default证明tactic链和自适应预算配置一致。
搜索轨迹变化后，实际的证明调用时机和得到的目标值可以变化，这属于消融效果。
本入口使用完整证明后端，不继承之前 `run_no_guarded_no_cutset.sh` 的双关闭配置。

在 `/home/lbh/OMT` 下用一条命令后台依次运行两组：

```bash
bash script/run_sls_ablation.sh all
```

顺序为当前完整版、关闭目标邻域；每组协调器完成汇总、
退出后才开始下一组，失败则停止。命令立即返回总控PID和总日志路径，终端退出后继续。
每组开始前核对求解器二进制哈希，版本发生变化则停止，避免混用版本。
总日志放在 `/data/shared/lbh/OMT/result/sls_ablation_*.log`；
`SLS_ABLATION_LOG_DIR` 可单独调整总日志的目录。
`all --dry-run` 在前台检查两组，不创建总日志或提交作业。
`all` 不接受 `--output` 或 `--prepare-only`，各组自动使用独立结果目录。

也可以单独提交某一组：

```bash
bash script/run_sls_ablation.sh full
bash script/run_sls_ablation.sh no-objective-neighborhoods
```

上述单组命令默认提交后立即返回；追加 `--wait` 则等待该组协调器结束并返回退出码。
额外启动参数可追加到单组或 `all` 命令后，
例如 `--nodes node1,node2,node3`；用 `--dry-run` 只检查配置，不提交。
结果各自在 `/data/shared/lbh/OMT/result/NIA_<时间戳>/`，自动生成
`nia_ls.csv`、`combined.csv`、`summary.md`。配置在各组的manifest与冻结solver options中保留。
优先比较A1/A2/B/C、成功数和目标值胜平负；后端可能补回搜索损失，成功数相同也需要比较目标值与找到最好值的时间。

各实例原生日志的最终JSON保留 `sls_objective_neighborhoods_enabled` 和
`sls_objective_neighborhood_evaluations`（常规坐标/拟合/交换的投影候选评估数）。
被关闭模块对应的调用数应为0。
这些统计只增加在原生日志中，不增加规范结果CSV的列。

## 历史交付：native013，完整集成功净增32例

2026-10-31的本轮目标以005完整ON `NIA_20261030_112517_518871` 为固定基线：6310例中成功5942、对逐实例较好对手负23；本轮要求成功至少5967且负不超过23，完整运行实得5974成功、20负，已通过全量验收。实验资源为node1、node2、node3，各180并行，每例1200秒。该轮native013二进制SHA256为 `14c5c9dc00d45990987c82820410bb10d450bde772178b6153bf8767dc0bd9db`；当前工作树已继续修改。

| 完整6310例 | A1 | A2 | B | C | 成功A1+A2 | 对较好对手胜/平/负 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 固定005基线 | 2356 | 3586 | 345 | 23 | 5942 | 2137/4150/23 |
| native013 | 2355 | 3619 | 313 | 23 | 5974 | 2136/4154/20 |

成功净增32，负例减少3。新增成功44、旧成功回退12；新增负例5、移除负例8，属于整体改善，不表示每例都更好。本轮收益主要体现在有限最优证明：A2增加33、A1减少1；未找到初始解仍为23例。结果来自同一完整运行，不拼接不同实验的逐例最好值。

本轮新增的 `--guarded-product` 默认为关闭，须配合 `--prove-optimal`。它在完整原式和当前严格改善界上引入单项式坐标，保留原布尔守卫，并加入可精确核对的等式乘变量及不等式乘积后果，再把整数坐标放松为实数，交给QF_LRA处理。只接受UNSAT作为原严格改善查询无解的证明；放松SAT不充当原式整数模型。生成、求解与证书发布共享原证明时限，该辅助阶段另有3秒软预算；不支持、未知或预算耗尽后回到原证明流程。新路径没有按应用名称设置规则。

例如，整数约束为 `x >= 0`、`y >= 0`、`z = x*y`，最小化 `z`，SLS已找到 `z=0`。证明时将乘积 `x*y` 替换成新的实数坐标 `p`，保留 `z=p`，并加入有效后果 `(x>=0 and y>=0) => p>=0`。再加严格改善要求 `z<=-1`，线性算术即可推出UNSAT。每个原整数模型都能以 `p=x*y` 映入该放松，所以这个UNSAT能证明当前值最优。若条件只出现在某个析取分支，后果仍保留条件守卫，不能把当前分支当成全局硬约束。

该历史轮次启用了core-neighborhood、convex-exchange、smt-exchange=hints三个交互，另开启 `--optimality-bv-seconds=120` 与上述guarded-product；core-neighborhood已从当前版本删除。BV分支放宽该SAT子阶段原有的500冲突和100MB局部门槛，保留调用者及全局限制，并以120秒软墙钟限制该阶段；失败或未知后回到默认策略。它处理完整原式的严格改善查询，不替代原式模型检查；配置BV不等于实际执行过。未开启的integer/affine convex、slack、objective slice、tail及诊断moment原型不计作本轮收益来源。

先完成固定391例筛选（成功65、负18），再运行完整6310例 `NIA_20261031_160915_094021`。全套6287份保留native模型、692份最终A2实际引用的guarded源转换证据均通过核验；其中53份以明确的官方typed parser宏展开前后身份链连接原source与audit副本。其余2012份求解前侧车不当作证明。核验不重放LRA UNSAT，运行答案和及时性以保留执行记录为准。9个new实例曾因审计规范名映射中断，失败与恢复证据均保留，未重跑模型或求解实验。

运行及聚合的 `status=error`、`interrupted` 均为0；336个B/C实例的error文本为正常的1200秒墙钟预算到期说明。完整证据已归档到本轮结果目录的 `verification/goal25/`，见[验收摘要](/data/shared/lbh/OMT/result/NIA_20261031_160915_094021/verification/goal25/README.md)和[逐例结果汇总](/data/shared/lbh/OMT/result/NIA_20261031_160915_094021/summary.md)。低收益方案及停止决定见[优化尝试记录](../writing_paper/materials/优化尝试记录.md)，研究原稿归纳见[研究本子总结与后续优化方向](../writing_paper/materials/研究本子总结与后续优化方向.md)。

本轮已完成。复现历史求解配置需要该轮冻结环境，当前启动器已不接收旧邻域参数。
原配置见结果目录的 `source/solver_options.json`；以下命令仅比较已经完成的历史结果：

```bash
cd /home/lbh/OMT
python3 -B z3-nia/experiment/deepmix_compare.py \
  --baseline-run /data/shared/lbh/OMT/result/NIA_20261030_112517_518871 \
  --candidate-run /data/shared/lbh/OMT/result/NIA_20261031_160915_094021 \
  --required-gain 25 --expected-nodes node1,node2,node3 --expected-jobs-per-node 180
```

## 历史交付：build005，上一轮用户目标增加45例

上一轮选择已经完成全6310例运行并通过native全量核验的 **build005** 交付。用户在2026-10-30将成功数增量目标从50改为45；固定基线仍为 `/data/shared/lbh/OMT/result/NIA_20261029_170740_990913` 的5895成功、对逐例较好对手24负，因此新门槛为 **成功至少5940且负不超过24**。005 ON 实测5942成功、23负，净增47。ON/OFF的全量原生核验和同二进制对照均已结束；新增成功中59/61已有额外源级独立事实。保守地只计算这59个新增成功，加上5881个状态及证明值与基线一致的保留成功，仍有5940成功、净增45，满足更新后的完整目标。

额外两例的独立重放未确认最优：一例1200秒返回unknown；另一例通过完整原式/严格查询/可行见证检查后主动撤回。两例均未被补标或改写原始A2记录，也未计入上述保守下界。最终独立审计在ON的 `verification/delivery-independent-audit-45.md`，逐例结果和当前45门槛比较在 `deepmix_compare.md`。构建收据中的旧5945目标文字保留其历史语境，当前验收以用户更新和该报告为准。

| 完整运行（均6310例） | A1 无界 | A2 有限最优 | B 有解未证明 | C 无初始解 | 成功 A1+A2 | 相对固定基线 | 对较好对手胜/平/负 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 固定基线 `NIA_20261029_170740_990913` | 2358 | 3537 | 393 | 22 | 5895 | — | 2136/4150/24 |
| 005 ON `NIA_20261030_112517_518871` | 2356 | 3586 | 345 | 23 | 5942 | +47 | 2137/4150/23 |
| 同二进制005 OFF `NIA_20261030_112622_121790` | 2354 | 3536 | 397 | 23 | 5890 | -5 | 2134/4152/24 |

ON开启了当时的core-neighborhood、convex-exchange、smt-exchange=hints三个交互；OFF关闭它们，使用同一005二进制、完整清单、五节点各40并行及单例1200秒协议。两组的6310行native模型、事件和总预算核验均已通过，`finalized.json`均为returncode=0。ON比同二进制OFF多52个成功；用户验收始终采用固定基线的净增47，不更换基线、不拼接不同运行的逐例最好结果。ON有61个原B/C转为成功、14个原成功回退，完整结果不等于逐例均改善。对照报告位于ON的 `verification/same-binary-off-control/attribution.md`。

冻结005二进制SHA256为 `aaf060ea7cb1be5f4474c3d9082eb54b1c793fde5ae11d52919b51de9ada9d6d`，原构建收据在 `logs/deepmix/build-005/`。005交付当时的主产品源码精确恢复005；`sls/src/nia_omt_optimality.cpp` 的SHA256为 `4cf4dc96d449f54d7db6ebec584cb7d2f2cd5077da8edc3690b0cfa4195bd382`。`logs/deepmix/build-delivery-005/`记录重新完整编译、28项CLI/API及fallback组件检查通过；当时52个产品源码指纹和live `build/z3` 哈希均精确匹配实测005；当前工作树和二进制已继续修改，以本页顶部候选记录为准。

006的最小context隔离修复、真实Hanoi RED/GREEN及恢复材料已归档在 ON目录的 `verification/context-isolation-fix-006/archived-candidate/`。它是可恢复候选，没有全量性能结果，不是本次交付性能的数据来源；当前不启动006正式作业。原 `NIA_LS/` 和 `competitor/` 没有修改。

以下两个可选信息交换通道保留供旧实验复现，纯Z3证明模式不使用：

- 历史 `--convex-exchange`（已删除）：对原式可精确认证的PSD凸二次目标，SLS赋值提供切线支持点，QF_LIA返回新的候选点或完整切线查询的UNSAT。SAT经原式/native桥及GMP检查后回灌；最优证明保存可独立重放的原式、PSD、切线和可行见证证书。
- 历史 `--smt-exchange=hints`（已删除）：将已验证incumbent中的同名整数坐标交给Z3数值初始值接口，作为软提示，不增加赋值等式；Z3的完整严格改善SAT模型仍走原式/native回灌。SMT预试最多5秒，unknown后回到保留的默认策略并共享原预算。`backend`仅是无坐标提示的后端消融，后端切换本身不作为交互创新。

历史005完整运行中，普通Z3 SAT回灌涉及372例，其中199例随后由SLS继续改善。可见final记录有680个core和1,118,510次优先级扫描；368个超时例缺少final，所以这些core合计仅为下界，完整运行没有接受 `z3_core` 模型。当前版本已删除整个core邻域模块及其反馈和计数。实际提示消费另有初始化跟踪测试，不能只看mapped数量。完整遥测与这些限制在ON的 `verification/information-exchange-telemetry.md`，开关组合收益不等于某一个通道单独贡献。

历史005完整ON的求解配置应使用该轮冻结环境复现；新版实验入口已不接受旧邻域开关。
下列命令只比较已经完成的ON/OFF结果，不启动求解：

```bash
cd /home/lbh/OMT
python3 -B z3-nia/experiment/deepmix_compare.py \
  --baseline-run /data/shared/lbh/OMT/result/NIA_20261029_170740_990913 \
  --candidate-run /data/shared/lbh/OMT/result/NIA_20261030_112517_518871
```

比较入口读取完整manifest及逐例原始结果，当前门槛为固定基线+45；模型/事件预算核验和原SMT2证明重放分别记录。旧设计及历史报告中的+50保留其当时语境，见 [DEEP_MIX_PLAN.md](../writing_paper/materials/DEEP_MIX_PLAN.md)。

## 历史交互试验与原+50门槛

原目标要求成功至少5945且负不超过24。005 ON完整运行实得5942成功、23负，当时净增47、尚差3例，**没有达到原增加50例目标**。这条历史事实保留；新目标是用户随后改为+45，并非补标、合并或更改原运行结果。005的628例筛选 `NIA_20261030_095102_159708` 已完成，A1/A2/B/C=19/246/341/22，成功265；固定基线同集合成功213，净增52，对较好对手283胜324平21负。筛选结果不替代完整6310例验收。

早期core-only运行 `NIA_20261029_213458_324172` 和convex-only运行 `NIA_20261029_215055_598194` 都只选固定基线415个B/C，使用server/node1/node2各120并行、每例1200秒。core-only报告A1/A2/B/C=4/1/389/21，成功5、对较好对手负21；convex-only报告9/23/361/22，成功32、负17；固定基线同集合成功0、负24。20份凸A2证书均通过独立原式、PSD、切线、严格查询UNSAT及可行见证核验；证据见convex-only目录的 `verification/convex-final-audit-20261029T160522Z/final_evidence.md`。这些部分集合收益不能相加。

早期backend组合筛选完整结果为7/56/330/22，成功63，对较好对手259胜135平21负，无error/interrupted。56个A2的相同原式最优界已有独立证明与可行见证，其中20个复用已核验的凸原式事实，36个用未修改的Z3 5.1重放完整严格改善查询。7个A1也另行完成原SMT2级仿射射线验证：两个采样点只生成全变量候选，最终原hard和目标的全参数反例查询均UNSAT；证据在该backend筛选目录的 `verification/unbounded-independent/`。没有用对手答案或目标值本身充当证明。

早期backend/hints筛选分别为 `NIA_20261029_234334_122192`、`NIA_20261029_234451_940362`，使用同一 `f876ce64...` 二进制。其完整6310例实验均无error/interrupted，但没有达到当时的性能目标：

| 历史实验 | A1 | A2 | B | C | 成功 | 对较好对手胜/平/负 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 固定基线 | 2358 | 3537 | 393 | 22 | 5895 | 2136/4150/24 |
| f876 OFF，`NIA_20261030_000257_160486` | 2355 | 3527 | 407 | 21 | 5882 | 2136/4148/26 |
| f876 backend，`NIA_20261030_000932_586277` | 2352 | 3427 | 507 | 24 | 5779 | 2132/4131/47 |
| f876 hints，`NIA_20261030_013157_510154` | 2357 | 3422 | 510 | 21 | 5779 | 2137/4129/44 |

backend/hints相对固定基线分别有175/177个已解决退化，其中140/145个目标值持平。f876会一直使用选定SMT后端；build005改为5秒可选预试加默认策略回退，并在邻域真实unknown时结束当前incumbent搜索，不再无依据释放坐标。其真实RED/GREEN、28项产品/API检查和持久回退检查记录在 `logs/deepmix/build-005/`，性能以顶部完整ON/OFF结果为准。

历史数值提示消费曾经过6个初始化目标测试（当前专属测试已删除），真实SMT/arith2日志出现 `initialize x := 4`、`initialize y := 4`；无hint对照没有这些初始化，双方assertions不变。证据保存在 `logs/deepmix/hint-consumption-current/`，没有只凭 `mapped` 数量推断提示生效。

## 结构

- `source/`：独立的 Z3 5.1 源码，shell 增加 NIA_LS 入口。
- `sls/src/`：SLS搜索、SAT模型回灌、无界证明及各条有界最优证明路径。
- `sls/frontend/`：C++ SMT2 解析与预处理、入口和参数映射。
- `sls/tests/`：原有回归及新增导入、接口、实验调度测试。
- `experiment/`：提交版与内嵌版对照实验，不依赖原目录的未提交脚本。
- `reference/`：Git archive 提取的原提交，用于新编译参考程序；生成产物不纳入 Git。
- `build/`：新构建；`build-competitor/` 只是 cp 时带来的旧构建，不能在其中重新配置。

## 构建

```bash
cd /home/lbh/OMT
bash z3-nia/build.sh
```

脚本使用 CMake 的 Unix Makefiles，再执行 `make -j64`。共享节点缺少 GMP 开发头文件和 CMake 时，当前环境使用 `toolchain/` 中从 server 复制的开发文件与构建工具；没有安装或修改节点的系统软件。`COPYRIGHT` 和 `provenance.json` 记录这些依赖的来源。这些是本地构建依赖，不纳入 Git；有系统 CMake 和 GMP 开发包的环境也可以直接构建。新 shell 内的 SLS 与证明查询使用当前编译的同一份 Z3 内核，不链接 competitor 的 libz3。

## 三种模式

```bash
# 搜索与优化，关闭无界证明和有界最优证明。
./z3-nia/build/z3 --nia-ls example.smt2 --mode=sls --timeout=1200

# 在搜索基础上加入无界证明。
./z3-nia/build/z3 --nia-ls example.smt2 --mode=unbounded --timeout=1200

# 同时加入无界证明和有界最优证明。
./z3-nia/build/z3 --nia-ls example.smt2 --mode=optimal --timeout=1200
```

默认初始化保留提交版的线性化局部搜索，Z3 用于前端和 AST 处理；`sls` 模式关闭两种证明。需要同时关闭初始化阶段时加 `--initialize=off`。`--seed=N`、`--model-output=PATH` 等提交版参数仍可使用。有界最优证明也可单独启用：使用 `--optimize --prove-optimal`，不加 `--mode` 和 `--prove-unbounded`。

历史005完整ON的单例命令使用冻结005二进制，保留提交版证明调度并显式开启三个交互：

```bash
./z3-nia/logs/research-round2/baseline/z3-005 --nia-ls example.smt2 --mode=optimal --timeout=1200 \
  --large-target=1000000 --unbounded-threshold=1000000 \
  --optimality-stall=10 --optimality-timeout=30 \
  --optimality-resume --optimality-burst --optimality-cutset --optimality-balanced \
  --initial-z3-after=300 --initial-z3-timeout=120 \
  --core-neighborhood --convex-exchange --smt-exchange=hints
```

输入是单个 QF_NIA 整数最小化或最大化问题，保持提交版的非增量、单目标限制。C++ 转换阶段用新鲜 marker 保护目标，沿提交版相同次序运行 Z3 预处理，再导入精确的 native IR。最大化方向单独保存，避免被 Optimize 的内部目标取负语义影响。

无需预先用 Python 转换 SMT2。初始化和修复暂时仍会读取同一次导入产生的私有 native 文件；这是保留原行为的内部过渡边界。程序直接调用编入 shell 的 SLS 函数，不执行另一个 NIA_LS 程序。证明查询保留提交版的子进程暂停、恢复机制，当前阶段不宣称已经实现同进程的双引擎续传。

## Slurm 运行完整测试集

当前完整配置覆盖5744个original和566个new实例，默认server、node4、node5各180并行：

```bash
cd /home/lbh/OMT
bash script/run_no_convex.sh
```

该入口使用当前构建，启用guarded-product、finite-cutset、无提示SMT预试和BV120，
不包含已删除的额外凸切线模块。每例总时限1200秒，每节点申请96000MB内存。
纯Z3最优性检查对照使用 `bash script/run_plain_z3.sh`；其运行器关闭finite-cutset和其他辅助，
直接求完整严格改善查询，Z3默认solver内部的算法仍然保留。

节点名单是允许使用的资源池。后台调度器按允许节点数拆分清单（实例更少时减少非空分片），每片独立申请单节点指定核数；某节点有足够的 Slurm 可分配 CPU 和内存就先运行，同一节点同时最多一个 worker，完成并释放资源后也可以继续领取下一片。其他节点有资源时会加入。上面的三节点命令最多540个实例并行；历史实验保留其实际节点与并行数。物理 CPU 利用率低不代表已被其他作业预留的核心可分配。

1200 秒是每个实例导入与优化共用的时限。默认开启无界证明、有限最优证明及上面的完整证明调度参数，关闭对手目标值和已知最优值的提前停止。当前命令每片申请96000 MB内存，显式使用 `--memory-mb 96000`，不申请整节点独占。资源不足时调度器等待，一有合适节点便提交；遇到不能确认的提交会保留意图，避免重复运行同一片。

启动后输出后台调度器 PID、日志及 `/data/shared/lbh/OMT/result/NIA_<时间戳>/` 结果目录。`controller.json` 记录等待/运行状态，`shards/<分片编号>/receipt.json` 记录实际 Slurm 作业编号。全部分片结束后自动生成 `results.csv`、`nia_ls.csv`、`combined.csv` 和 `summary.md`；汇总区分允许的节点池和实际使用的节点。运行时冻结可执行文件、依赖库、参考脚本和启动适配器；导入与求解均调用内嵌版 Z3，Python SDK 用于原有模型/结果验证。后续重新编译不会改变已提交任务使用的快照。

只检查清单可加 `--dry-run`；只生成快照和调度器启动命令可加 `--prepare-only`。恢复已有调度使用输出的冻结 `_schedule <结果目录>` 命令，文件锁会阻止重复调度器。入口复用 `reference/` 中的干净提交版实验基础设施，运行前应保留本目录已有的 `reference/`。

## 结果与验证

程序输出提交版兼容的 JSON 进展和最终记录，可记录模型、最好目标值、找到时间及自身证明状态。达到对手给出的值不作为证明依据。

`--timeout` 的预算包含已花费的导入时间；导入本身尚未提供可中断接口。正式实验启动器会对转换和求解的整个子进程组执行 1200 秒硬时限。独立运行若也需要覆盖导入阶段的硬时限，可在命令前加 `timeout --kill-after=5s 1200s`。

正确性和性能验收分别进行：先检查 C++ 与提交版 Python 转换结果、模型和三种模式，再在相同输入、种子、参数和逐实例 1200 秒预算下对照新编译的提交版。本轮native013完整实验使用node1/node2/node3各180并行、每片96000 MB，允许共享节点，不申请整节点独占。固定005基线当时使用server/node1/node2/node4/node5各40并行；本轮与其采用相同输入和单例1200秒预算，属于固定基线数量对照，不称为同二进制、同资源的因果消融。

所有正式运行及报告放在 `/data/shared/lbh/OMT/result/NIA_{时间戳}/`。未完成完整对照之前，不能把“成功编译”或“小例子通过”写成“效果已不弱于提交版”。最新验证范围以该运行目录下的报告为准。

## 历史：交互实现前的内嵌完整对照

测试目录为 `/data/shared/lbh/OMT/result/NIA_20261029_011322_291446`，对应深度交互实现之前的冻结内嵌二进制（SHA256 `29bbfe34cb9292cbd55aa02564728965a7a28c424a212126f71a92bc2b370ca8`），并非现在的工作树构建。使用新编译的提交版作对照，完整覆盖 5744 个 original 和 566 个 new 实例；每例 1200 秒、每个任务 80 并行，参数和种子一致，不使用对手最优值提前停止。

| 版本 | A1 无界 | A2 有限最优 | B 有解未证明 | C 无初始解 | 成功求解 A1+A2 | 有初始解 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 提交版 6614f3c | 2356 | 3531 | 399 | 24 | 5887 | 6286 |
| 内嵌版 | 2360 | 3529 | 399 | 22 | 5889 | 6288 |

按目标值比较，内嵌版对提交版为 **38 胜、6235 平、37 负**。双方均未成功求解的 411 例为 **26 胜、356 平、29 负**。双方 error 均为 0。完整数据、逐实例比较及预先声明的整体标准见该目录的 `summary.md`、`reports/full/paired.csv` 和 `full_evaluation.json`。

本轮实测达到整体标准：无新增错误、初始解数和成功求解数不减、全体实例目标值胜数不少于负数。结论是整体保持提交版水平，并非保证逐实例或每个子集都不下降，也不是统计显著改进。52 例小回归因少 1 个最优性证明未过更严格门槛，这个失败记录保留；相同二进制的自重复实验也观察到墙钟限时下的轨迹波动，没有把全部差异都归因于同一个原因。

完整编码审计覆盖 6310 例：6294 例逐字节一致；15 例在恢复变量原名后精确多项式、CNF 和目标相同；另 1 例在核验全部 356 个完整且无环的辅助布尔定义、消去定义并逆分配后，精确结构与目标相同。保留原字节差异记录，未把最后一例说成简单重命名。可复验工具为 `experiment/audit_ir_equivalence.py`，严格证据见运行目录的 `verification/full-native-ir-equivalence-strict.json`，已重新读取全部 6310 对当前 IR、核验完整头尾和哈希，对字节相同项也实际比较两侧内容。它验证两份 IR 之间的等价，不替代原 SMT2 转换正确性测试或独立重放 UNSAT。

提交版 `full-baseline-02` 已完成全部 1052 例，但批次临时目录清理时报 `ENOTEMPTY`。经完整 CSV、逐行 stdout、配置、作业身份及冻结哈希校验，只恢复了收尾元信息；原 CSV 未改，原清理失败、原 manifest 和显式恢复凭据均保留在该任务目录及 `verification/recovery/`。原作业没有被表述为正常退出。

后续信息交换与会话重构见 [深度融合待实现.md](../writing_paper/materials/深度融合待实现.md)。
