# Z3 5.1 内嵌 NIA_LS

本目录从 `competitor/z3-5.1.0` 单独 `cp` 得到。SLS 源码位于 `sls/src/`，来自 Git 提交 `6614f3c1ea13539918b6f641c64446b753e6db0e`，不包含原工作树尚未提交的深度交换实验。来源及原文件校验和见 `embedding-provenance.json`。

## 结构

- `source/`：独立的 Z3 5.1 源码，shell 增加 NIA_LS 入口。
- `sls/src/`：提交版搜索、SAT 模型回灌、无界证明和有界最优证明代码。
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

完整性能对照使用以下提交版实验参数，避免把简化接口的默认调度与原满集实验的调度混为一谈：

```bash
./z3-nia/build/z3 --nia-ls example.smt2 --mode=optimal --timeout=1200 \
  --large-target=1000000 --unbounded-threshold=1000000 \
  --optimality-stall=10 --optimality-timeout=30 \
  --optimality-resume --optimality-burst --optimality-cutset --optimality-balanced \
  --initial-z3-after=300 --initial-z3-timeout=120
```

输入是单个 QF_NIA 整数最小化或最大化问题，保持提交版的非增量、单目标限制。C++ 转换阶段用新鲜 marker 保护目标，沿提交版相同次序运行 Z3 预处理，再导入精确的 native IR。最大化方向单独保存，避免被 Optimize 的内部目标取负语义影响。

无需预先用 Python 转换 SMT2。初始化和修复暂时仍会读取同一次导入产生的私有 native 文件；这是保留原行为的内部过渡边界。程序直接调用编入 shell 的 SLS 函数，不执行另一个 NIA_LS 程序。证明查询保留提交版的子进程暂停、恢复机制，当前阶段不宣称已经实现同进程的双引擎续传。

## Slurm 运行完整测试集

单独运行当前编译的内嵌版，完整覆盖 5744 个 original 和 566 个 new 实例：

```bash
cd /home/lbh/OMT
./script/run_z3_nia_slurm.sh \
  --nodes server,node1,node2,node3,node4,node5 \
  --jobs 80 --timeout 1200
```

这是一个六节点作业，每节点 80 个实例并行，总计最多 480；1200 秒是每个实例导入与优化共用的时限。默认开启无界证明、有限最优证明及上面的完整证明调度参数，关闭对手目标值和已知最优值的提前停止。默认每节点申请 53493 MB 内存，可用 `--memory-mb` 指定其他正数；节点忙时由 Slurm 排队，不申请整节点独占。

提交后输出作业编号和 `/data/shared/lbh/OMT/result/NIA_<时间戳>/` 结果目录。作业结束后自动生成 `results.csv`、`nia_ls.csv`、`combined.csv` 和 `summary.md`。运行时冻结可执行文件、依赖库、参考脚本和启动适配器；导入与求解均调用内嵌版 Z3，Python SDK 用于原有模型/结果验证。后续重新编译不会改变已提交任务使用的快照。

只检查清单可加 `--dry-run`；只生成快照及提交命令可加 `--prepare-only`。入口复用 `reference/` 中的干净提交版实验基础设施，运行前应保留本目录已有的 `reference/`。

## 结果与验证

程序输出提交版兼容的 JSON 进展和最终记录，可记录模型、最好目标值、找到时间及自身证明状态。达到对手给出的值不作为证明依据。

`--timeout` 的预算包含已花费的导入时间；导入本身尚未提供可中断接口。正式实验启动器会对转换和求解的整个子进程组执行 1200 秒硬时限。独立运行若也需要覆盖导入阶段的硬时限，可在命令前加 `timeout --kill-after=5s 1200s`。

正确性和性能验收分别进行：先检查 C++ 与提交版 Python 转换结果、模型和三种模式，再在相同输入、种子、参数和逐实例 1200 秒预算下对照新编译的提交版。实验仅使用 server、node1–5 中至少有 80 个可分配核心的健康节点，每节点 80 并行，允许共享 MIXED 节点；不申请整节点独占。

所有正式运行及报告放在 `/data/shared/lbh/OMT/result/NIA_{时间戳}/`。未完成完整对照之前，不能把“成功编译”或“小例子通过”写成“效果已不弱于提交版”。最新验证范围以该运行目录下的报告为准。

## 本次完整对照

测试目录为 `/data/shared/lbh/OMT/result/NIA_20261029_011322_291446`，对应当前 `build/z3`（SHA256 `29bbfe34cb9292cbd55aa02564728965a7a28c424a212126f71a92bc2b370ca8`）。使用新编译的提交版作对照，完整覆盖 5744 个 original 和 566 个 new 实例；每例 1200 秒、每个任务 80 并行，参数和种子一致，不使用对手最优值提前停止。

| 版本 | A1 无界 | A2 有限最优 | B 有解未证明 | C 无初始解 | 成功求解 A1+A2 | 有初始解 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 提交版 6614f3c | 2356 | 3531 | 399 | 24 | 5887 | 6286 |
| 内嵌版 | 2360 | 3529 | 399 | 22 | 5889 | 6288 |

按目标值比较，内嵌版对提交版为 **38 胜、6235 平、37 负**。双方均未成功求解的 411 例为 **26 胜、356 平、29 负**。双方 error 均为 0。完整数据、逐实例比较及预先声明的整体标准见该目录的 `summary.md`、`reports/full/paired.csv` 和 `full_evaluation.json`。

本轮实测达到整体标准：无新增错误、初始解数和成功求解数不减、全体实例目标值胜数不少于负数。结论是整体保持提交版水平，并非保证逐实例或每个子集都不下降，也不是统计显著改进。52 例小回归因少 1 个最优性证明未过更严格门槛，这个失败记录保留；相同二进制的自重复实验也观察到墙钟限时下的轨迹波动，没有把全部差异都归因于同一个原因。

完整编码审计覆盖 6310 例：6294 例逐字节一致；15 例在恢复变量原名后精确多项式、CNF 和目标相同；另 1 例在核验全部 356 个完整且无环的辅助布尔定义、消去定义并逆分配后，精确结构与目标相同。保留原字节差异记录，未把最后一例说成简单重命名。可复验工具为 `experiment/audit_ir_equivalence.py`，严格证据见运行目录的 `verification/full-native-ir-equivalence-strict.json`，已重新读取全部 6310 对当前 IR、核验完整头尾和哈希，对字节相同项也实际比较两侧内容。它验证两份 IR 之间的等价，不替代原 SMT2 转换正确性测试或独立重放 UNSAT。

提交版 `full-baseline-02` 已完成全部 1052 例，但批次临时目录清理时报 `ENOTEMPTY`。经完整 CSV、逐行 stdout、配置、作业身份及冻结哈希校验，只恢复了收尾元信息；原 CSV 未改，原清理失败、原 manifest 和显式恢复凭据均保留在该任务目录及 `verification/recovery/`。原作业没有被表述为正常退出。

后续信息交换与会话重构见 [深度融合待实现.md](sls/深度融合待实现.md)。
