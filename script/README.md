# Benchmark 批量运行

## NIA_LS 完整优化实验与每轮结果目录

```bash
cd /home/lbh/OMT
./script/run_optimize.sh all --subset all \
  --prove-unbounded --large-target 1000000 \
  --jobs 180 --timeout 1200
```

运行原始 5744 例和新应用 566 例，共 6310 例。每例最多 1200 秒、最多 180 并行，沿用初始解、优化、无界证明与达到已知最优值后提前结束的处理。

每次运行默认新建 `/data/shared/lbh/OMT/result/NIA_年月日_时分秒_微秒/`，目录名使用北京时间。例如 `NIA_20261024_120000_123456`。运行开始时打印完整路径。原始结果、单例日志和模型放在这个目录内；完整结束后自动与仓库的 `result/competitor.csv` 比较，统计文件也直接写到同一目录：

```text
/data/shared/lbh/OMT/result/NIA_<时间戳>/
  results.csv       # 原始逐例记录
  manifest.json     # 输入清单、参数与可执行文件摘要
  summary.json      # 求解阶段汇总
  cases/            # 单例日志、中间输入、模型及证明
  nia_ls.csv        # A1/A2/B/C 四列结果
  combined.csv      # 三算法合并表
  summary.md        # 本轮比较统计，同时显示到终端
```

不需要另跑统计命令。中断的实验保留已写出的原始记录，不生成完整统计；如果求解已完成但自动统计失败，会报告错误并保留原始结果，可以修正基线后重跑下面的统计入口。`--output` 仍可显式指定不存在的目录，统计随该目录保存；不会覆盖已有实验目录。

若所有选定实例的记录都已保留，但部分实例提前中断或作业收尾失败，可显式生成带
“未完整完成”提示的汇总；中断实例按其已验证的最好结果归入B/C，原始数据和
`summary.json` 的完成标记保持不变。缺少实例记录、未校验的模型或无效证明仍拒绝导出：

```bash
./script/report_results.sh /data/shared/lbh/OMT/result/NIA_<时间戳> \
  --competitor /data/shared/lbh/OMT/result/NIA_<时间戳>/source/result/competitor.csv \
  --allow-incomplete
```

该选项必须指定实验目录；默认命令仍只选择完整结束的一轮。

## NIA_LS 实验结束后导出与比较

```bash
cd /home/lbh/OMT
./script/report_results.sh
```

需要重新生成统计时，默认只从 `/data/shared/lbh/OMT/result` 选择开始时间最近且完整结束的一轮 NIA_LS 实验，与固定的 `result/competitor.csv` 比较。不启动求解器，也不回退到旧本地实验目录。报告显示在终端，文件直接保存到所选实验目录：

- `nia_ls.csv`：四列，文件名、状态、目标值、时间（秒）。
- `combined.csv`：按实例名合并的十列三算法表。
- `summary.md`：只展示三项统计：三个求解器的 A1/A2/B/C 数量及成功数（A1+A2）、未成功数（B+C）；全部实例上的 NIA_LS 胜平负；三者均未成功求解子集上的 NIA_LS 胜平负。两个胜平负表都分别比较 Z3、OptiMathSAT 和逐实例两者的优胜者。

```bash
# 显式输入也兼容历史实验目录
./script/report_results.sh result/optimize/20261023-071653-857674

# 也可传入该轮 results.csv，或另选基线
./script/report_results.sh result/optimize/20261023-071653-857674/results.csv \
  --competitor result/competitor.csv
```

新实验可直接把运行时显示的 `/data/shared/lbh/OMT/result/NIA_...` 完整目录传给此入口。统计命令的 `--output` 可覆盖输出位置；省略时始终写回输入实验目录，原始 `results.csv`、`manifest.json`、`summary.json` 保持不变。

状态 `A1` 表示证明无界，`A2` 表示确认有限最优，`B` 表示有可行解但未证明最优，`C` 表示没有初始可行解；已解决数量为 `A1+A2`。历史模式的 NIA_LS `A2` 也包括 `known_optimum_reached`，即达到该轮记录的对手已证明最优值；启用 `--prove-optimal` 的新模式只认可本轮严格改善 SMT 查询的 UNSAT，匹配对手值只保留为比较信息。脚本检查证明状态、目标值及时间一致性，不根据数值很大或与普通候选值相等推断证明。

独立最优性原型的完整运行命令：

```bash
cd /home/lbh/OMT
./script/run_optimize.sh all --subset all \
  --prove-optimal --optimality-stall 50 --optimality-timeout 30 \
  --prove-unbounded --large-target 1000000 \
  --jobs 180 --timeout 1200
```

此模式禁用对手目标的全部提前停止。目标连续 50 秒未改善后，调用 Z3 普通 SMT
检查完整原始硬约束加严格改善条件，UNSAT 才提前停止并标记 A2。
初始检查预算为 30 秒；未证后回到 SLS，再停滞 50 秒就重试。
SLS 这一段没有改善则下一次预算翻倍，有改善则取上一轮预算的一半，每段只调整一次。
SAT 模型恢复并精确校验后也会更新最好解。每次检查使用子进程硬截止，计入 1200 秒总时限。
无界检测可同时启用。实际查询保存在每例的 `optimality.smt2`；
`optimality_attempts/optimality_seconds/optimality_status/time_to_optimality_seconds`
保留在原始 CSV，有限值四列表仍记录找到该最好值的时间。
原始 CSV 另记录 `optimality_schedule`、`optimality_next_timeout_seconds`、
`optimality_sat_models_accepted`；旧轮次没有这三列也可继续导出比较报告。
详细编码与边界见 [NIA_LS 实现细节](../NIA_LS/实现细节.md#15-目标停滞后调用-z3-独立检查有限最优)。

无界填 `-oo`（最大化时为 `+oo`），时间取 `time_to_unbounded_seconds`；有限可行值时间取 `time_to_best_seconds`；无解填 `NA`，时间取 `wall_seconds`，包括提前异常结束的实际耗时。基线兼容原先中文状态和 `A1/A2/B/C`，输入基线不会被改写。

比较时只看目标值：无界最好、`NA` 最差，有限整数按每例 `manifest.json` 中的最小化／最大化方向比较，时间和证明状态不用于打破同值平局，`NA/NA` 也计平。三者均未成功求解集合要求三个状态都是 `B/C`，即没有任何一个求解器达到 A1/A2。

支持 bounded 等子集实验：只比较该轮实际运行的实例，基线可以含更多实例；该轮每个实例必须出现在基线中。缺失或重复实例、未完成或中断的实验、缺失时间、未校验模型等会报错，避免静默少算。

## Slurm 三节点运行 NIA_LS

```bash
cd /home/lbh/OMT
./script/run_optimize_slurm.sh all --subset all \
  --nodes server,node1,node2 --partition all \
  --jobs 180 --timeout 1200 \
  --prove-optimal --optimality-stall 50 --optimality-timeout 30 \
  --prove-unbounded --large-target 1000000
```

提交一个 sbatch 作业，在 `server`、`node1`、`node2` 上各启动一个 worker。
`--jobs 180` 是**每节点**同时运行的实例数，上限合计 540；不是整个集群只开 180。
使用 `all` 分区，因为 `compute` 不包含 server。作业独占这三个节点，worker 各自最多
180 个实例并行；Slurm 可能因此显示预留了三个节点全部 576 个 CPU，这不改变实例并发上限。
1200 秒是每例包含转换、初始化、搜索和证明的总预算，不包含排队时间。
整个 Slurm 作业的时间限按最大分片的批次数计算并额外留 30 分钟，避免一到 1200 秒就杀掉全批。

命令提交后返回 job ID；可用 `squeue -j <job_id>` 看状态，`scancel <job_id>` 取消。
不需要保持提交终端开启。`--dry-run` 只检查实例选择，不构建、不提交、不运行。
常用的 suite、subset、证明参数和重复 `--input` 与本地入口相同。

只测试此前那 92 个负例，并将完整 1200 秒留给 NIA_LS 搜索的对照命令：

```bash
./script/run_optimize_slurm.sh all --subset all \
  --input-list /data/shared/lbh/OMT/result/NIA_20261025_212144_155179/losses92.txt \
  --nodes server,node1,node2 --partition all --jobs 180 --timeout 1200 \
  --prove-optimal --optimality-stall 1201 \
  --no-target --no-known-optimum-stop --large-target 1000000
```

等待阈值 1201 秒超过每例总时限，实际不会启动最优性证明，也未启用无界证明；
保留独立判定口径，达到对手最优值不借用对手证明。92 例分到三个节点各约 31 例，
实际并发不超过 92，这个小对照不可能用满每节点 180。仍使用原有前端、初始化和搜索修复。

每次提交仍建立 `/data/shared/lbh/OMT/result/NIA_<时间戳>/`，根目录保存统一的
`manifest.json`、`results.csv`、`summary.json`、`nia_ls.csv`、`combined.csv` 和三张表的 `summary.md`。
`source/` 冻结求解器、非 glibc 动态库、转换器 SDK、Python 脚本和对手 CSV；
解决 node1/node2 缺系统 Z3/GMP 库的问题。基准文件继续从共享目录读取，运行期间应保持这些文件不变。
`shards/` 保留每节点清单、CSV 和完成状态，`node-<节点>.out/.err` 为节点日志，
`slurm-<job_id>.out/.err` 为主作业日志。

worker 按全局序号分片，合并时检查实例身份、重复、缺失、指纹和配置，再把模型等恢复到根
`cases/` 的标准编号目录。节点失败或取消时保留已完成部分，`complete=false`，不生成完整报告，
默认报告入口也不会将它当作已完成轮次。必要时可在所有 worker 结束后用该轮冻结入口重新汇总：
`python3 <结果目录>/source/script/run_optimize_slurm.py _finalize <结果目录>`；
它会重新检查分片，不补造缺失结果。
统计与独立验证器兼容总并发 540，同时继续限制每节点不超过 180、每例不超过 1200 秒。

## 重新统计对手的有限最优和无界结果

```bash
cd /home/lbh/OMT
./script/run_unbounded.sh z3 --jobs 180 --timeout 1200
./script/run_unbounded.sh optimathsat --jobs 180 --timeout 1200
```

依次执行两个命令。每个命令先运行原始 OMT(NIA) 全集 5744 例，再运行新应用全集 566 例，合计 6310 例，包括此前被 bounded 筛选去掉的实例。新应用只读取各类型的 `instance/*.smt2`，不重复读取 `bounded` 等筛选副本。每例限时 1200 秒、最多 180 并行，不额外设置内存上限；有限最优或无界结论完成后正常结束。沿用原有求解器配置，超时、内存不足和进程异常均如实记录。

```text
/data/shared/lbh/OMT/unbounded/original/z3/latest/results.csv
/data/shared/lbh/OMT/unbounded/original/optimathsat/latest/results.csv
/data/shared/lbh/OMT/unbounded/new/z3/latest/results.csv
/data/shared/lbh/OMT/unbounded/new/optimathsat/latest/results.csv
```

每轮另有时间戳目录，`latest` 指向最新一轮，保留以往结果。结果包括 CSV、JSONL、配置、输入清单、汇总计数，以及每例完整事件和原始 stdout/stderr。旧 `result/original`、`result/new` 不会被改写。

新入口保留原四列，并增加下列字段：

| 字段 | 含义 |
| --- | --- |
| `objective_sense` | 原输入的 `minimize` 或 `maximize` |
| `status` | `sat`、`unsat`、`unknown`、`timeout`、`error`、`interrupted` |
| `reported_optimum` | 求解器最终报告的目标值，保留精确表达式，包括 `(- oo)`、`(* (- 1) oo)`、`oo` 等 |
| `time_to_reported_optimum_seconds` | 首次观察到最终报告值的时间；和有限候选值的时间分别记录 |
| `optimality_status` | `attained`（有限最优）、`unbounded`（最终报告无界）、`unconfirmed`、`none` 等 worker 判断 |
| `is_unbounded` | 只有成功完成优化、最终报告为目标方向对应的无穷且求解器给出完成标记时为 `true`；仅进度区间含无穷不够 |

`best_value` 始终是有限可行模型的目标值，`is_optimal` 始终只表示确认达到有限最优。因此无界时 `is_optimal=false`、`is_unbounded=true` 是正常的，已解决数可以统计两者的并集。无界依赖对手的最终报告，脚本不独立校验其无界证明。若只是超时前得到很大的有限值，不能据此标记无界。完整记录另外保留上下界、求解器完成标记和错误原因。

```bash
# 仅预检全部输入，不创建输出、不启动求解器
./script/run_unbounded.sh z3 --dry-run

# 可选：仅跑一个来源，或指定另一输出根目录
./script/run_unbounded.sh optimathsat --suite original
./script/run_unbounded.sh z3 --suite new --output-root /tmp/omt-rerun
```

新入口的预算和路径由上述命令行选项控制，不继承旧入口的 `BENCHMARK_*`、`RESULT_ROOT`、`JOBS`、`TIMEOUT`、`MEMORY_MB` 环境覆盖。旧 `run.sh` 和 `run_new.sh` 的四字段输出保持兼容；本轮复跑请使用 `run_unbounded.sh`。

## NIA_LS 初始可行解检查

```bash
cd /home/lbh/OMT
./script/run_initial.sh all
```

此命令先编译一次 NIA_LS，再运行原实验的 5744 个实例和新生成的 566 个实例，默认 **180 并行、每例总计 1200 秒**。总预算包括 OMT 转换、NIA_LS 读入和寻找初始解，不包括排队时间。只使用 NIA_LS 自身的初始化与可行性搜索；每例找到第一个通过精确约束校验的可行模型便立即结束，不继续优化。没有加入内存限制、自动重试或竞争求解器回退。

```bash
# 分别运行原实例、新实例
./script/run_initial.sh original
./script/run_initial.sh new

# 只检查选择范围，不编译、不求解、不创建结果
./script/run_initial.sh all --dry-run

# 小规模试跑，或者指定单个 SMT2 文件
./script/run_initial.sh new --limit 5 --jobs 2 --timeout 30
./script/run_initial.sh --input new_benchmark/packing/instance/squares-n02--8d5fbe62518a.smt2

# 显式指定资源和新的结果目录（目录不能已存在）
JOBS=180 TIMEOUT=1200 ./script/run_initial.sh all --output /home/lbh/OMT/result/initial/my_run
```

每次运行只保留一个 `result/initial/日期时间/results.csv`，逐例完成后立即写入；已有 `result/original` 与 `result/new` 的 CSV 不会改动。中间转换文件、模型文件和诊断输出放在临时目录，退出后清理。运行期间使用同一份 NIA_LS 可执行文件快照。按 `Ctrl+C` 或收到 `SIGTERM` 时，停止提交新实例并清理活动任务及其子进程。

| 字段 | 含义 |
| --- | --- |
| `filename` | 原实例相对 `/data/shared/lbh/OMT/benchmarks` 的路径，或新实例相对仓库的路径 |
| `category` | `original`，或新实例的 `portfolio`、`cyclic`、`packing`、`design` |
| `status` | `sat` 已找到并校验初始解；`unknown` 未找到；`unsat` 预处理已证明不可行；`timeout` 总预算耗尽；`error` 转换或进程出错；`interrupted` 被用户终止 |
| `initial_value` | 首个已校验可行模型的精确目标值；只有 `sat` 才填写，不表示最优 |
| `time_to_first_seconds` | 从该例开始转换到 NIA_LS 完成首个可行模型的精确校验及模型写出的墙钟秒数；使用原生 `elapsed_seconds`，排除父进程轮询发现退出的延迟；只有 `sat` 才填写 |
| `error` | 超时所在阶段或异常原因；没有可行值时保持空值，不伪造结果 |

## 新应用基准

在 `script` 目录运行四类 [应用型 OMT(NIA) 基准](../new_benchmark/README.md)：

```bash
cd /home/lbh/OMT/script
./run_new.sh z3
./run_new.sh optimathsat
```

默认运行 `new_benchmark` 下全部 **566 个模型**，每例 **1200 秒**，最多 **180 个并行任务**。原有 86 个模型保留，本轮每类新增 120 个，共 480 个；`small`、`medium`、`large` 每类各 40 个，只表示参数规模，不代表固定求解难度。

```bash
# 只运行本轮新增的 480 个模型
./run_new.sh z3 --new-only

# 只运行一类；可与 --new-only 组合
./run_new.sh z3 packing
./run_new.sh z3 packing --new-only

# 仅预检输入和预算，不创建结果目录、不启动 worker
./run_new.sh z3 --dry-run

# 显式覆盖默认预算、并行数和结果根目录
TIMEOUT=600 JOBS=8 RESULT_ROOT=/tmp/omt-new-results ./run_new.sh z3
```

新入口的数据根固定为本仓库的 `new_benchmark`，不受旧 `BENCHMARK_ROOT`、`BENCHMARK_LIST` 等 `BENCHMARK_*` 环境变量影响。结果默认位于：

```text
result/new_benchmark/z3/latest/results.csv
result/new_benchmark/optimathsat/latest/results.csv
```

汇总结果只包含实例来源、最佳候选值、首次报出该值的时间，以及是否已确认最优这四项，定义见下文。最大化目标的结果会还原为原应用目标值。

## 重跑 Z3 被强制终止的 70 个实例

```bash
cd /home/lbh/OMT/script
JOBS=35 TIMEOUT=1200 MEMORY_MB=4096 \
BENCHMARK_ROOT=/home/lbh/OMT/new_benchmark \
BENCHMARK_LIST=/home/lbh/OMT/script/z3_retry_70.txt \
RESULT_ROOT=/home/lbh/OMT/result/retry_70_4g ./run.sh z3
```

清单固定为原实验中收到 SIGKILL 的 70 个实例，不随 `latest` 变化。每例限时 1200 秒，最多 35 个并行任务；四字段汇总写入 `result/retry_70_4g/z3/latest/results.csv`。

按统一运行协议和既定资源限制报告结果。内存耗尽、超时和进程异常均如实保留，不针对个别实例调整求解器预处理或搜索参数补跑。已有 4 GiB 一轮的 70 例均报内存不足，没有记录到候选目标值；`best_value` 和 `time_to_best_seconds` 为空，`is_optimal` 为 `false`。这不表示实例不可行。

`MEMORY_MB=4096` 通过 `prlimit` 将每个 worker 的地址空间上限设为 4 GiB，不是预留内存，也不是 RSS 上限；设为 `0` 或不设置时不加限制。35 个 worker 的地址空间上限合计 140 GiB，高于本机约 125 GiB 的物理内存，仍可能出现总内存不足。

## 原有 NIA 基准

在 `script` 目录中选择求解器运行：

```bash
cd /home/lbh/OMT/script
./run.sh z3
./run.sh optimathsat
```

每次运行默认最多 **180 个 benchmark 同时求解**，每例限时 **1200 秒**。默认只运行 OMT(NIA)：输入清单为 `/data/shared/lbh/OMT/benchmarks/nia_benchmarks_subset.txt`，共 **5744 例**。

脚本使用 `competitor` 中已安装的求解器。Z3 使用该版本的 Python Optimize API 获取候选模型回调；OptiMathSAT 使用本地编译的命令行程序，启用优化、模型生成、二分搜索与实时进度输出，并关闭官方非线性支持要求关闭的 `toplevel_propagation`。两者都支持单个最小化或最大化目标。

Z3 的计时对应带模型回调的 API 执行，与此前裸命令行试跑的耗时可能不同。

## 结果位置

两个入口每次运行都建立新目录，保留以前的结果；`latest` 指向最新一次运行。原有 `run.sh` 的结果位于：

```text
result/z3/latest/results.csv
result/optimathsat/latest/results.csv
```

具体目录结构为 `result/{solver}/{UTC时间戳}/`，`run_new.sh` 则为 `result/new_benchmark/{solver}/{UTC时间戳}/`：

- `results.csv`：四字段汇总，每个实例一行，完成后立即刷新，可以边运行边查看。
- `results.jsonl`：与 CSV 相同的四个字段，每个实例一行。
- `metadata.json`、`inputs.txt`、`summary.json` 及 `cases/`：保留运行配置、输入清单、完成数量、单例完整记录和原始日志，供排查异常。

## 汇总字段

| 字段 | 含义 |
| --- | --- |
| `benchmark` | SMT2 实例的完整绝对路径，用于确定实例来源 |
| `best_value` | 求解过程中观察到的最佳有限候选目标值，保留 SMT 精确表达式；超时也可能有值 |
| `time_to_best_seconds` | 首次观察到最终保留的 `best_value` 的墙钟秒数；重复报告相同值不会重置时间 |
| `is_optimal` | `true`：求解器报告有限最优，且目标值、模型和可用上下界的一致性检查通过；`false`：尚未确认最优 |

没有找到候选值时，`best_value` 和 `time_to_best_seconds` 为空，`is_optimal` 为 `false`。超时、无界或异常也不会标为已确认最优；`false` 不表示已证明这个候选值不是最优值。这里依赖求解器的最优性判断，脚本没有独立校验最优性证明。

计时从**该实例 worker 启动前**开始，包括启动和解析开销，不包括排队等待。记录的是脚本收到该值的时间，不能推断求解器内部更早何时已经找到它。

## 可选配置

```bash
# 可覆盖默认的 1200 秒预算，例如改为 600 秒；仍默认 180 并行
TIMEOUT=600 ./run.sh z3

# 不限时
TIMEOUT=0 ./run.sh optimathsat

# 仅运行指定清单；每行是相对 BENCHMARK_ROOT 的 .smt2 路径
BENCHMARK_LIST=/path/to/list.txt ./run.sh z3

# 可覆盖 benchmark 根目录、输出根目录及并行数
BENCHMARK_ROOT=/path/to/benchmarks RESULT_ROOT=/path/to/result JOBS=180 ./run.sh optimathsat
```

`Ctrl+C` 或 `SIGTERM` 会停止调度新实例、终止活动实例及其子进程，并保留已收到的部分值。写入失败也会停止活动任务。超时进程会先收到终止信号，随后必要时被强制停止。

## 轻量验证

```bash
cd /home/lbh/OMT
python3 -m unittest discover -s script/tests -p test_run.py -v
python3 -m unittest discover -s script/tests -p test_run_new.py -v
bash -n script/run.sh
bash -n script/run_new.sh
./script/run_new.sh z3 --dry-run
```

上述验证使用临时目录、模拟 worker 和预检，不启动真实求解器。


### Z3 调度实验参数（2026-10-26）

独立证明模式仍使用 `--prove-optimal --optimality-stall 50 --optimality-timeout 30`，不按对手已知值提前停止。

- `--optimality-search-window 0`：复现原 adaptive_v1 调度。
- `--optimality-search-window W`（W>0）：首次短证明不变；预算耗尽后让局搜连续运行 W 秒，再给 Z3 一次较长的连续证明。新目标恢复短探测；同一目标的严格改进问题已返回 SAT 时，等目标改善后再证明。后续长调用保留 2% 剩余预算供收尾，外层1200秒不延长。
- `--optimality-resume`：保留相同目标界限的 Z3 搜索进程。时间片结束后暂停，下一次用 SIGCONT 继续；目标改善时回收旧进程并重新建查询。默认关闭；可配合原倍增时间片（window=0）或正窗口使用，整例1200秒仍包含全部等待和搜索时间。
- `--initial-z3-after 300 --initial-z3-timeout 120`：可选。300秒仍没有初始可行解时，使用原公式硬约束进行一次完整 Z3 SAT 检查。只有经过源公式和 native 精确验证的模型才加入局搜；此调用本身不声明最优。默认 `--initial-z3-after 0` 关闭，不影响原始轨迹。

以上参数会冻结在 Slurm 配置、manifest 和 summary.json 中；运行中修改源码不影响已提交实验。

完整6310例实验 `NIA_20261026_175345_895315` 已验证：相对同一基准，A1+A2 为
5683→5743（+60），对逐实例更强对手的负例为85→43（减少42）。推荐组合为
`--optimality-resume --optimality-search-window 0 --initial-z3-after 300 --initial-z3-timeout 120`，
其余保留停滞50秒、首次检查30秒、每例1200秒及每台180并行。
完整命令、server/node1–4的节点设置和验证范围见
[NIA_LS README](../NIA_LS/README.md#升级前的全量结果与调度配置2026-10-266310-例)。

2026-10-27：转换器、原生求解与对手统一使用本地Z3 5.1.0。转换器通过局部
`factor_nonlinear=False` 保留非线性算术文字；原生求解保留默认参数。新Slurm快照的
转换端和原生端库来自同一文件，不再使用临时4.12.3运行库。
`NIA_LS/src/nia_ls --z3-version` 可查看实际加载版本；版本不符时启动即报错，
Slurm的原生 `--help` 预检也执行此检查。历史快照和历史统计保持原样。
server单机重测命令见 [统一5.1说明](../NIA_LS/README.md#统一51并保留原来的算术编码2026-10-27)。

## 对127个同值未证明实例单独检查严格改善界

`run_strict_bound.sh` 针对server轮次 `NIA_20261026_211519_926612` 中NIA_LS状态为B、
已达到至少一个对手A2目标值的127例。它从原始SMT文件保留全部硬约束，删除优化及
原来的求解/输出/超时设置，加上严格改善界后生成普通SMT：117例最小化加 `obj < M`，
10例最大化加 `obj > M`。M是本轮NIA_LS最终找到、且与对手已证明最优值相同的整数。
生成只用文本解析；求解直接执行 `competitor/z3-5.1.0/build/z3 -smt2 文件`，不通过Python API求解。

准备好的127份查询位于：

```text
/data/shared/lbh/OMT/result/strict_bound/queries127_NIA_20261026_211519_926612/
  instance/*.smt2
  manifest.json
  README.md
```

只在server上通过Slurm运行，180为并行上限，实际最多127例同时运行：

```bash
cd /home/lbh/OMT
sbatch --partition=all --nodelist=server --nodes=1 --ntasks=1 \
  --cpus-per-task=180 --exclusive --mem=0 --time=00:30:00 \
  --job-name=z3-bound127 \
  --output=/data/shared/lbh/OMT/result/strict_bound/slurm-%j.out \
  --wrap='/home/lbh/OMT/script/run_strict_bound.sh --prepared /data/shared/lbh/OMT/result/strict_bound/queries127_NIA_20261026_211519_926612 --jobs 180 --timeout 1200'
```

也可以登录server后直接运行同一脚本命令。每次结果新建在
`/data/shared/lbh/OMT/result/strict_bound/Z3_<北京时间戳>/`，不覆盖已有结果。
其中 `instance/` 保存本次查询副本，`logs/` 保存逐例stdout/stderr，
`results.csv` 保存结果和墙钟耗时，`summary.md` 统计全部127例、Z3原已证明的110例、
仅OptiMathSAT原已证明的17例分别有多少本次证明UNSAT。

每例1200秒只用于本次Z3普通SMT进程，包含解析；准备/复制及Slurm排队不计入。
UNSAT才证明不存在更优解。SAT单独报告以便核查，unknown/timeout不能当作证明；
非零退出及SMT错误不会误计为UNSAT。没有新增每进程内存上限。
`results.csv` 还保留旧对手耗时和原NIA轮次的目标时间、证明次数/耗时供比较；
旧NIA没有留下证明final字段时保持空白，空白不代表0秒或从未调用过Z3。
存在数学最优值并不保证Z3普通SMT策略在1200秒内证明UNSAT，正是本实验要检验的情况。

如需从原始轮次重新准备，指定一个不存在的新目录：

```bash
./script/run_strict_bound.sh \
  --run /data/shared/lbh/OMT/result/NIA_20261026_211519_926612 \
  --prepare-only --output /你选择的全新目录
```

原benchmark和NIA_LS源码保持原样；查询内容保存SHA256，运行前检查所有127份文件未变化。
