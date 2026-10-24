# Benchmark 批量运行

## NIA_LS 实验结束后导出与比较

```bash
cd /home/lbh/OMT
./script/report_results.sh
```

默认选择 `result/optimize` 中开始时间最近且完整结束的一轮 NIA_LS 实验，与固定的 `result/competitor.csv` 比较。不启动求解器。报告会直接显示在终端，并保存到 `result/comparison/该轮目录名/`：

- `nia_ls.csv`：四列，文件名、状态、目标值、时间（秒）。
- `combined.csv`：按实例名合并的十列三算法表。
- `summary.md`：全部实例及三者均未解决子集的最好值数量、已解决数量和 NIA_LS 胜平负。

```bash
# 推荐对指定的一轮生成报告，避免混淆不同实验
./script/report_results.sh result/optimize/20261023-071653-857674

# 也可传入该轮 results.csv，或另选基线与输出目录
./script/report_results.sh result/optimize/20261023-071653-857674/results.csv \
  --competitor result/competitor.csv \
  --output result/comparison/my_run
```

状态 `A1` 表示证明无界，`A2` 表示确认有限最优，`B` 表示有可行解但未证明最优，`C` 表示没有初始可行解；已解决数量为 `A1+A2`。NIA_LS 的 `A2` 沿用此前口径，也包括 `known_optimum_reached`，即达到该轮记录的对手已证明最优值。脚本会检查目标值与该元数据一致，不根据数值很大或与普通候选值相等推断证明。

无界填 `-oo`（最大化时为 `+oo`），时间取 `time_to_unbounded_seconds`；有限可行值时间取 `time_to_best_seconds`；无解填 `NA`，时间取 `wall_seconds`，包括提前异常结束的实际耗时。基线兼容原先中文状态和 `A1/A2/B/C`，输入基线不会被改写。

比较时只看目标值：无界最好、`NA` 最差，有限整数按每例 `manifest.json` 中的最小化／最大化方向比较，时间不参与胜负。并列均计达到最好，`NA/NA` 计平，同时提供排除三者全为 `NA` 后的最好值数量。三者均未解决集合要求三个状态都是 `B/C`。

支持 bounded 等子集实验：只比较该轮实际运行的实例，基线可以含更多实例；该轮每个实例必须出现在基线中。缺失或重复实例、未完成或中断的实验、缺失时间、未校验模型等会报错，避免静默少算。

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
