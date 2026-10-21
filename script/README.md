# Benchmark 批量运行

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
