# 内嵌进度与继续执行记录

## 最终核验结论

以下为最终状态，后面的“运行中”“待完成”段落是历史过程记录。

用户随后要求清理原目录：`NIA_LS` 的9个跟踪文件已恢复到6614f3c，21个未跟踪原型/测试文件及19个忽略的旧对象文件已移除；原版可执行文件换回已全量验证的c5a315...提交版。旧源码与diff临时归档在 `/home/lbh/.tmp/nia_ls_cleanup_20261029_072134_660017/`；深度交互设计、具体980/915反例及生命周期教训已保存在 `sls/深度融合待实现.md`。原目录Git状态干净，新内嵌版二进制及其他跟踪脚本未改。

独立 `cp` 的 Z3 5.1 与独立 `sls/` 模块已完成，原提交固定为 `6614f3c1ea13539918b6f641c64446b753e6db0e`；未使用旧工作树的未提交交换原型。SLS 直接编入同一 Z3 core，提供搜索、无界证明和有界最优证明入口。`make -j64` 已完成；当前 `build/z3` SHA256 为 `29bbfe34cb9292cbd55aa02564728965a7a28c424a212126f71a92bc2b370ca8`，与被测候选及构建凭据一致。

正式 `NIA_20261029_011322_291446` 的 watcher 76864 已 exit 0、state=evaluation_complete。6310 例新鲜对照：baseline/embedded 的 A1 为2356/2360、A2为3531/3529、B为399/399、C为24/22；成功5887/5889，有初始解6286/6288，error0/0。全体目标值38胜6235平37负，双方B/C411例为26胜356平29负。达到预先整体标准，但不是逐例无损、所有子集改善或统计显著提升；52严格gate少1个证明的失败保留。

独立复算12份completion、69个冻结产物和6310输入哈希通过；manifest仍 `1b8eb76f7c48504c6fa4541fbaff68704d8006bdaf9090fe9cc6868046f1aeed`。无A1/A2交叉冲突，无A2被另一版更优有效模型反驳。全部被计入的初始解、最好值和证明时间都在1200秒内。baseline02的1052条数据已完整写出但清理目录报ENOTEMPTY；只恢复元信息，原CSV仍SHA `d09d4bf19fc8565da20834e24ea94ebf918e9b57ecd329363be4b234a46fafed`，原manifest和清理失败证据保留，completion明确kind=recovered，原作业未被表述为正常退出。

最终严格IR核验已由root重放：全部6310对当前文件重新读取/核验，6294字节相同、15精确重编号后相同、1经356个完整无环布尔定义投影与恒等变换后结构相同，all_equivalent=true。报告为 `verification/full-native-ir-equivalence-strict.json`；工具SHA `77249c4e51d3b9ef2565bbd37fa6027549ea04122191d0b3f8d7acddf53907fc`。初版仅复用旧CSV标签的入口缺口已用两个RED反例修正，6项测试通过；数学变换未变，旧证据全部保留。这是IR对IR等价，不是独立重放UNSAT。

CLI 11项、导入12项及原回归无新增失败的证据保留；旧极大值subcase失败仍如实记录。实验harness38项、focus诊断13项、自重复诊断5项、显式清理恢复13项、最终IR审计6项的测试与源码可追溯。README和结果summary已披露完整指标、编码范围、证明子进程边界与清理恢复。未自动git commit。

## 任务和基线

用户在明确讨论结束后授权实际集成：单独 cp Z3 5.1，SLS 独立目录，三种模式，性能不弱于 Git 最新提交。固定基线为 `6614f3c1ea13539918b6f641c64446b753e6db0e`。不使用旧工作树中未提交的 search-exchange / soft-jointpoint 实验。

`z3-nia/` 从 competitor 整体 cp；旧 build 改名 build-competitor，新 build 重新 CMake。`sls/src/` 来自 git archive。SLS 核心 .cpp/.h 除 call.cpp 的入口、计时与写入前路径检查衔接外保持提交版。原工作树及其用户已有修改保留。

## 已实现和验证

- C++ importer 等价移植提交版 converter，保护目标 marker，保留遍历与编码顺序，不调用 Solver.check / Optimize.check。
- `z3 --nia-ls INPUT.smt2 --mode=sls|unbounded|optimal`，原 native IR 和各独立证明开关亦保留。
- 19 个 NIA object 直接链接新 shell；C/C++ API 由相同 Z3 core 对象提供。`ldd` 无 libz3，GMP 静态链接。
- 提交版暂停/恢复 proof 子进程、严格改善查询及 SAT 模型回灌保留；并非已经实现同进程共享冲突/活动信息。
- 输入保护覆盖 SMT2、native SOURCE/IR、模型及其派生名、query/default/initial 路径。18 个失败反例 RED 后修复；原参数校验顺序经 post-validation hook 保留。
- make -j64 构建成功。节点缺少 GMP dev 和 CMake，通过 server 已有文件组成 toolchain 本地依赖，没有更改系统软件。
- 最新 CLI 11 项通过；导入 12 项（含 original 7 家族/new 4 类真实样例）通过；harness 32 mock 通过。
- 提交版 68 项旧回归：baseline 有 3 个 subcase 失败，embedded 有 2 个，均是 baseline 已有的极大目标值搜索测试；新增失败/错误为零。不要把这写成“68 项全通过”。另 1 项可选 Hanoi 烟测未开启。
- 有限 cutset、optimality schedule、model bridge、optimality 和 unbounded 五项 C++ 组件目标通过。

## 当前正式实验

目录：`/data/shared/lbh/OMT/result/NIA_20261028_151848_060693`

- baseline SHA256：`c5a3152391895cefcd85c9e57db200274fa45543489f8a6baa680091ac0ce617`
- embedded SHA256：`ad8a81287e1d6056efe00adaa0a3fe7cf614e963c3d228cc54ae13f6c2eef216`
- frozen manifest SHA256：`542d83beb63d33b295c12f88a392efb21f6f7c4c50a5c17376d732e48c85c3cb`
- 共 6310 例：5744 original + 566 new。
- 52 个分层回归样例，含 Leipzig 和 portfolio 1058。每 variant 一个 job，2 regression jobs；通过后自动继续 6 shards × 2 variants = 12 full jobs。
- 每实例 1200 秒总预算，包括转换；default seed mt=0、lia_seed=0，不加 target、不参考 known optimum 停止。证明配置沿旧 full：stall10、slice30、resume/burst/cutset/balanced、initialZ3after300/120、大数和无界阈值1e6。
- 仅允许 server、node1–5。每节点最多一个 harness job，80 CPUs，显式比例内存，健康且 free effective CPU>=80，支持 MIXED，共享节点无 exclusive/mem0。
- 首批 job：baseline 87342/node4；embedded 87343/node5。切勿重复提交。
- 当时启动的 watcher：unified exec session **42153**；现已因回归 gate 失败退出 code 2，未接 full。run/watch.pid 为历史记录，不表示进程仍存活。
- 52 个回归例子的 native IR 已逐字节全相同：run/verification/regression-native-ir.json 和 csv。
- run/verification/ 中保存构建、旧测试基线对照及日志。全量 IR 核验命令：

```bash
python3 -B z3-nia/experiment/compare_imports.py /data/shared/lbh/OMT/result/NIA_20261028_151848_060693 --phase full
```

### 第一轮回归已结束，未进入 full

watcher 42153 已退出 code 2，不能重启它或重复提交原实验。52 例：两版均 A1=7/A2=30/B=13/C=2，目标比较 embedded 对 baseline 为 0胜50平2负。两例均 cyclic：hard_1 的 5478/5728，planted-q36-r3-p19-e64-s1102 的 7213/7767。原目录的全部冻结数据保留，未改变 gate。

只读轨迹审查发现：分岔已经发生在 progressive initializer 的交接候选，早于第一次 proof。初始化是自有 LIA 局部搜索，内部含 100ms/500ms/10s 墙钟限制；相同种子、相同 prefix/check 数不保证相同步数/赋值。两例分别交接违反子句数609/500、242/249。不能据此断言单一根因；后者甚至在900秒前嵌入一直领先，到终点才落后。

新旧 Z3 core 均 GCC13.3、C++20、相同 flags，mem/gparams/tactic 初始化文件逐字节相同。可控的 SLS 构建差异是新组件 C++20+fPIC，而 HEAD 是默认 GNU++17、无显式PIC、含-g。现仅修改本地 `sls/CMakeLists.txt`，把 SLS 恢复 GNU++17、PIC OFF、-g；Z3 core 保持20、搜索算法和证明调度不改。这是待验证候选，尚不能断言已修复性能差异。旧 ad8a binary 始终在第一轮 run/source/embedded/solver，可做同节点对照。

候选构建及小回归已排队 Slurm **87356**，job name `nia-std17-check`，receipt `logs/profile17-submit.json`。当前各节点均 CPUAlloc192，允许的80核资源暂不可用。不要重复提交构建；待 `logs/profile17-build.json` 生成并验证job成功后才能把 live build/z3 当候选17，之前仍是旧 ad8a。下一步 focus_profiles.py 正由 agent 编写：父回归双方均B/C的15例，同一份冻结nativeIR，baseline/embedded20/candidate17各独立worker进程，同节点单80CPU allocation，总并发<=78（15×3实际45），各1200秒；用于隔离构建因素，不作为全量end-to-end证据。脚本不自己sbatch，root核对资源后执行。

完整结果尚未完成，**不能标记目标 complete，也不能宣称性能已不弱**。接下来编译候选，检查CLI/导入/旧回归，再对两例同节点配对测试；保真后新建冻结实验重跑52→6310。不可更改第一轮 frozen files、manifest、输入或二进制。

## 已结束的构建与验证作业

87320/87321：节点缺 GMP dev，失败，未用于正式 baseline。
87322/87323：参考构建和 importer 初始对照成功。
87334：节点缺 CMake，失败，未用于正式 embedded。
87335：初次完整构建成功，已被后续 binary 取代。
87336：输入保护预期 RED。
87337：输入保护 GREEN、导入/调度测试成功；baseline 旧回归揭示原有失败。
87338：发现错误参数校验顺序回归，未用于正式实验。
87340：最终构建、11 CLI、旧回归无新增失败、5 C++ 目标通过；其 binary 已冻结正式使用。

## 尚存边界

独立 SMT2 入口会扣除导入耗时，但尚无导入取消接口；正式 runner 对整个进程组硬限时，因此实验总时限有效。独立运行的硬限时可加 GNU timeout。初始化已核实是基线自有 LIA 局部搜索（Z3 提供 AST），并非直接用完备 Z3 求 SAT；关闭初始化须 `--initialize=off`。README 已更正早先不准确的初始化描述。


## 2026-10-29 继续核查：C++17 和同节点诊断均已结束

- 构建/小回归作业 87356 已完成。候选17 SHA256 为 `29bbfe34cb9292cbd55aa02564728965a7a28c424a212126f71a92bc2b370ca8`；11 个 CLI 测试通过，68 项提交版回归无新增失败，保留基线已有极大值 subcase 失败事实。receipt 在 `logs/profile17-build.json`。
- 作业 87358 已结束；输出 `/data/shared/lbh/OMT/result/NIA_20261028_161244_414866`。三版本同节点、同冻结 native IR、每例1200秒，各15例；只隔离 native 求解，不是 SMT2 全流程验证。
- baseline / embedded20 / candidate17 均 A2=1、B=12、C=2。embedded20 对 baseline 为 1胜12平2负；candidate17 为 0胜13平2负，均无错误。编译配置调整没有消除全部性能回退，不能宣称已修复。
- candidate17 两负为 hard_1 的 5492 对 5466、packing seed1095 的 798 对 792。原先 planted 的三版均达到 6658 且证明 A2，说明单次墙钟截断结果有漂移，但尚无重复基线噪声实验，不能把所有差异归因于噪声。
- 下一步保留原52 gate失败事实，增加显式全量采证模式；默认 gate 仍阻断。全量实验完成仅表示采证结束，性能验收必须另外核对错误、可行解数、A1+A2 和目标值胜平负，不得把完整性直接当作不弱。


## 新一轮正式全量采证已启动

目录：`/data/shared/lbh/OMT/result/NIA_20261029_011322_291446`。当前候选17为29bb...，基线仍为c5a...。新harness SHA为be8645e656a344b27e2f99b23ee2017c6c035a356d7694a867d9b0cfefc235cb；38项mock通过，另有独立审计。

使用显式 `--collect-full-evidence`：保留52严格gate结果，失败也可继续全量采证；完成状态为evaluation_complete，不代表性能验收。冻结manifest预先声明：6310例完整、无逐实例新增error/interrupted、可行解数量不减、A1+A2不减、目标值胜数不少于负数。逐实例零负作为更强指标单独展示。

watcher unified exec session **76864**；真实PID见run/watch.pid。首批 regression baseline **87364/server**、embedded **87365/node1**，均已核对RUNNING/80CPU。后续6shards×2variants由原watcher自动接续，切勿重复提交。旧两次诊断和52失败记录均保留。全量结果仍未产生，目标仍未完成。


补充基线自重复诊断：`/data/shared/lbh/OMT/result/NIA_20261029_011625_502055`，Slurm **87366/node2** 已核对RUNNING。仅5746、6100两例；baseline和candidate17各2份完全相同exe/IR/seed，共8例，各1200秒，80CPU allocation。已记录不同Linux物理core的实际绑定（0–7），但TaskPlugin=null，明确不宣称跨作业独占。此诊断不改变主全量评分，不是SMT2 end-to-end证据。`experiment/repeat_profiles.py` 及5项新测试、旧13+38项共56通过；旧focus默认行为未改。

继续执行先检查主watcher session76864和jobs87364/87365/87366。主watcher会自动接续6310全量，不要重新prepare或重复sbatch。主新run全量IR核对使用 `python3 -B z3-nia/experiment/compare_imports.py /data/shared/lbh/OMT/result/NIA_20261029_011322_291446 --phase full`，应在两个variant全量结束后执行。


主新run的52例转换核验已完成：`verification/regression-native-ir.json` 为expected52/identical52/all_identical=true；主manifest SHA256为 `1b8eb76f7c48504c6fa4541fbaff68704d8006bdaf9090fe9cc6868046f1aeed`。此时87364/87365/87366仍真实RUNNING，回归及重复诊断未结束。


重复诊断的中间轨迹（未到1200秒，不作为最终性能评分）：同一个baseline c5a二进制/IR/seed，hard_1两次progressive_handoff的candidate_unsat为604与508，首次目标值18202与17957；因此已直接观察到提交版本身的墙钟敏感分岔。候选两次亦分岔。packing候选一份已到792、其余当时798，仍属进行中；不得把中间最优值当最终W/T/L。这一证据支持停止盲调编译配置，继续预先冻结的全量统计；不能据此直接证明非劣性。


## 新run全量收尾核查

52回归最终：baseline A1=7/A2=31/B12/C2，embedded A1=7/A2=30/B13/C2；目标1胜51平0负，严格gate因成功证明数少1仍failed，原记录保留。差例global5834 planted，两版到6658总时间974.821s/1194.518s；最后严格改善query逐字节同SHA7d6ffa...。基线实际约19.873s证明，候选只剩5.48199s，不能说是接口失效或SAT回灌缺失（实际18/22次）。

重复诊断87366已完成：hard_1 baseline两份5473/5477，candidate两份5477/5226；packing baseline两份798/798、candidate两份792/792；都B。这是原程序自身墙钟敏感波动的实测，不替代全量验收。

全量87367–87378已由原watcher自动提交；当前embedded六分片6310条均齐，A1=2360/A2=3529/B399/C22，成功5889、有初始解6288、error0，receipt和CSV SHA逐份独立审计相符。baseline05作业87378仍真实RUNNING，其他解数据已齐。

发现baseline02（87371）缺worker_done：stdout完整[1052/1052]，CSV1052唯一行（1049sat/3timeout），stderr唯一错误为run_batch清理TemporaryDirectory时报ENOTEMPTY `/home/lbh/.tmp/nia-opt-run-z5ltl_my`，summary之前已收齐全部结果。原job现不在queue，scontrol已查不到。正按严格校验与原始证据备份写显式恢复工具，仅恢复收尾元数据；不改CSV、冻结源码/manifest、不重跑、不伪称原job正常exit0。恢复未经独立审查和执行前，仍不得把该分片当正式complete。
