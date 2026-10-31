# Z3 5.1 内嵌 NIA_LS

本目录从 `competitor/z3-5.1.0` 单独 `cp` 得到。SLS 源码位于 `sls/src/`，集成基线来自 Git 提交 `6614f3c1ea13539918b6f641c64446b753e6db0e`。来源及原文件校验和见 `embedding-provenance.json`；当前工作树在该基线上增加了下面单独开关的深度交互原型，没有重用旧的不可靠回调实现。

## 当前交付：native013，完整集成功净增32例

2026-10-31的本轮目标以005完整ON `NIA_20261030_112517_518871` 为固定基线：6310例中成功5942、对逐实例较好对手负23；本轮要求成功至少5967且负不超过23，完整运行实得5974成功、20负，已通过全量验收。实验资源为node1、node2、node3，各180并行，每例1200秒。当前 `build/z3` 是native013（SHA256 `14c5c9dc00d45990987c82820410bb10d450bde772178b6153bf8767dc0bd9db`），不再是下文历史005二进制。

| 完整6310例 | A1 | A2 | B | C | 成功A1+A2 | 对较好对手胜/平/负 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 固定005基线 | 2356 | 3586 | 345 | 23 | 5942 | 2137/4150/23 |
| native013 | 2355 | 3619 | 313 | 23 | 5974 | 2136/4154/20 |

成功净增32，负例减少3。新增成功44、旧成功回退12；新增负例5、移除负例8，属于整体改善，不表示每例都更好。本轮收益主要体现在有限最优证明：A2增加33、A1减少1；未找到初始解仍为23例。结果来自同一完整运行，不拼接不同实验的逐例最好值。

本轮新增的 `--guarded-product` 默认为关闭，须配合 `--prove-optimal`。它在完整原式和当前严格改善界上引入单项式坐标，保留原布尔守卫，并加入可精确核对的等式乘变量及不等式乘积后果，再把整数坐标放松为实数，交给QF_LRA处理。只接受UNSAT作为原严格改善查询无解的证明；放松SAT不充当原式整数模型。生成、求解与证书发布共享原证明时限，该辅助阶段另有3秒软预算；不支持、未知或预算耗尽后回到原证明流程。新路径没有按应用名称设置规则。

例如，整数约束为 `x >= 0`、`y >= 0`、`z = x*y`，最小化 `z`，SLS已找到 `z=0`。证明时将乘积 `x*y` 替换成新的实数坐标 `p`，保留 `z=p`，并加入有效后果 `(x>=0 and y>=0) => p>=0`。再加严格改善要求 `z<=-1`，线性算术即可推出UNSAT。每个原整数模型都能以 `p=x*y` 映入该放松，所以这个UNSAT能证明当前值最优。若条件只出现在某个析取分支，后果仍保留条件守卫，不能把当前分支当成全局硬约束。

本轮沿用已有的core-neighborhood、convex-exchange、smt-exchange=hints三个交互，另开启 `--optimality-bv-seconds=120` 与上述guarded-product。BV分支放宽该SAT子阶段原有的500冲突和100MB局部门槛，保留调用者及全局限制，并以120秒软墙钟限制该阶段；失败或未知后回到默认策略。它处理完整原式的严格改善查询，不替代原式模型检查；配置BV不等于实际执行过。未开启的integer/affine convex、slack、objective slice、tail及诊断moment原型不计作本轮收益来源。

先完成固定391例筛选（成功65、负18），再运行完整6310例 `NIA_20261031_160915_094021`。全套6287份保留native模型、692份最终A2实际引用的guarded源转换证据均通过核验；其中53份以明确的官方typed parser宏展开前后身份链连接原source与audit副本。其余2012份求解前侧车不当作证明。核验不重放LRA UNSAT，运行答案和及时性以保留执行记录为准。9个new实例曾因审计规范名映射中断，失败与恢复证据均保留，未重跑模型或求解实验。

运行及聚合的 `status=error`、`interrupted` 均为0；336个B/C实例的error文本为正常的1200秒墙钟预算到期说明。完整证据已归档到本轮结果目录的 `verification/goal25/`，见[验收摘要](/data/shared/lbh/OMT/result/NIA_20261031_160915_094021/verification/goal25/README.md)和[逐例结果汇总](/data/shared/lbh/OMT/result/NIA_20261031_160915_094021/summary.md)。低收益方案及停止决定见[优化尝试记录](优化尝试记录.md)，研究原稿归纳见[研究本子总结与后续优化方向](研究本子总结与后续优化方向.md)。

本轮已完成，下面配置仅供未来复现，不需要重复提交本次实验：

```bash
cd /home/lbh/OMT
./script/run_z3_nia_slurm.sh --solver /home/lbh/OMT/z3-nia/build/z3 \
  --nodes node1,node2,node3 --jobs 180 --timeout 1200 \
  --memory-mb 96000 --queue-workers \
  --solver-option=--core-neighborhood \
  --solver-option=--convex-exchange \
  --solver-option=--smt-exchange=hints \
  --solver-option=--optimality-bv-seconds=120 \
  --solver-option=--guarded-product

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

ON开启下述三个交互开关；OFF关闭它们，使用同一005二进制、完整清单、五节点各40并行及单例1200秒协议。两组的6310行native模型、事件和总预算核验均已通过，`finalized.json`均为returncode=0。ON比同二进制OFF多52个成功；用户验收始终采用固定基线的净增47，不更换基线、不拼接不同运行的逐例最好结果。ON有61个原B/C转为成功、14个原成功回退，完整结果不等于逐例均改善。对照报告位于ON的 `verification/same-binary-off-control/attribution.md`。

冻结005二进制SHA256为 `aaf060ea7cb1be5f4474c3d9082eb54b1c793fde5ae11d52919b51de9ada9d6d`，原构建收据在 `logs/deepmix/build-005/`。005交付当时的主产品源码精确恢复005；`sls/src/nia_omt_optimality.cpp` 的SHA256为 `4cf4dc96d449f54d7db6ebec584cb7d2f2cd5077da8edc3690b0cfa4195bd382`。`logs/deepmix/build-delivery-005/`记录重新完整编译、28项CLI/API及fallback组件检查通过；当时52个产品源码指纹和live `build/z3` 哈希均精确匹配实测005；当前工作树和二进制已继续修改，以本页顶部候选记录为准。

006的最小context隔离修复、真实Hanoi RED/GREEN及恢复材料已归档在 ON目录的 `verification/context-isolation-fix-006/archived-candidate/`。它是可恢复候选，没有全量性能结果，不是本次交付性能的数据来源；当前不启动006正式作业。原 `NIA_LS/` 和 `competitor/` 没有修改。

三个通道实际消费的搜索信息如下，默认均关闭：

- `--core-neighborhood`：SLS提供自身可行incumbent的坐标，Z3用临时assumptions检查严格改善邻域；真实UNSAT core决定释放哪些坐标，并作为SLS变量搜索的软优先级。SAT完整模型经GMP验收后回灌；邻域UNSAT不设置全局最优标志。
- `--convex-exchange`：对原式可精确认证的PSD凸二次目标，SLS赋值提供切线支持点，QF_LIA返回新的候选点或完整切线查询的UNSAT。SAT经原式/native桥及GMP检查后回灌；最优证明保存可独立重放的原式、PSD、切线和可行见证证书。
- `--smt-exchange=hints`：将已验证incumbent中的同名整数坐标交给Z3数值初始值接口，作为软提示，不增加赋值等式；Z3的完整严格改善SAT模型仍走原式/native回灌。SMT预试最多5秒，unknown后回到保留的默认策略并共享原预算。`backend`仅是无坐标提示的后端消融，后端切换本身不作为交互创新。

本轮完整运行中，普通Z3 SAT回灌涉及372例，其中199例随后由SLS继续改善。可见final记录有680个core和1,118,510次优先级扫描；368个超时例缺少final，所以这些core合计仅为下界，完整运行没有接受 `z3_core` 模型。实际提示消费另有初始化跟踪测试，不能只看mapped数量。完整遥测与这些限制在ON的 `verification/information-exchange-telemetry.md`，开关组合收益不等于某一个通道单独贡献。

历史005完整ON的复现命令如下，显式使用冻结005二进制；执行会新建实验，交付核验不需要重复提交已经完成的ON/OFF：

```bash
cd /home/lbh/OMT
./script/run_z3_nia_slurm.sh --solver /home/lbh/OMT/z3-nia/logs/research-round2/baseline/z3-005 \
  --nodes server,node1,node2,node4,node5 --jobs 40 --timeout 1200 \
  --memory-mb 96000 --queue-workers \
  --solver-option=--core-neighborhood \
  --solver-option=--convex-exchange \
  --solver-option=--smt-exchange=hints

# 同二进制完整OFF：保留同样资源及预算，省去三个 --solver-option。
python3 -B z3-nia/experiment/deepmix_compare.py \
  --baseline-run /data/shared/lbh/OMT/result/NIA_20261029_170740_990913 \
  --candidate-run /data/shared/lbh/OMT/result/NIA_20261030_112517_518871
```

比较入口读取完整manifest及逐例原始结果，当前门槛为固定基线+45；模型/事件预算核验和原SMT2证明重放分别记录。旧设计及历史报告中的+50保留其当时语境，见 [DEEP_MIX_PLAN.md](experiment/DEEP_MIX_PLAN.md)。

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

数值提示消费还经过6个初始化目标测试，真实SMT/arith2日志出现 `initialize x := 4`、`initialize y := 4`；无hint对照没有这些初始化，双方assertions不变。证据保存在 `logs/deepmix/hint-consumption-current/`，没有只凭 `mapped` 数量推断提示生效。

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

按本轮native013配置运行，完整覆盖5744个original和566个new实例。已完成的实验无需重跑；下面命令用于后续复现：

```bash
cd /home/lbh/OMT
./script/run_z3_nia_slurm.sh --solver /home/lbh/OMT/z3-nia/build/z3 \
  --nodes node1,node2,node3 --jobs 180 --timeout 1200 \
  --memory-mb 96000 --queue-workers \
  --solver-option=--core-neighborhood \
  --solver-option=--convex-exchange \
  --solver-option=--smt-exchange=hints \
  --solver-option=--optimality-bv-seconds=120 \
  --solver-option=--guarded-product
```

节点名单是允许使用的资源池。后台调度器按允许节点数拆分清单（实例更少时减少非空分片），每片独立申请单节点指定核数；某节点有足够的 Slurm 可分配 CPU 和内存就先运行，同一节点同时最多一个 worker，完成并释放资源后也可以继续领取下一片。其他节点有资源时会加入。上面的三节点命令最多540个实例并行；历史实验保留其实际节点与并行数。物理 CPU 利用率低不代表已被其他作业预留的核心可分配。

1200 秒是每个实例导入与优化共用的时限。默认开启无界证明、有限最优证明及上面的完整证明调度参数，关闭对手目标值和已知最优值的提前停止。本轮复现命令每片申请96000 MB内存，显式使用 `--memory-mb 96000`，不申请整节点独占。资源不足时调度器等待，一有合适节点便提交；遇到不能确认的提交会保留意图，避免重复运行同一片。

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

后续信息交换与会话重构见 [深度融合待实现.md](sls/深度融合待实现.md)。
