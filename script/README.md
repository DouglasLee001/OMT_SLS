# Benchmark 批量运行

在 `script` 目录中选择求解器运行：

```bash
cd /home/lbh/OMT/script
./run.sh z3
./run.sh optimathsat
```

每次运行默认最多 **180 个 benchmark 同时求解**，每例限时 **1200 秒**。默认只运行 OMT(NIA)：输入清单为 `/data/shared/lbh/OMT/benchmarks/nia_benchmarks_subset.txt`，共 **5744 例**。

脚本使用 `competitor` 中已安装的求解器。Z3 使用该版本的 Python Optimize API 获取候选模型回调；OptiMathSAT 使用本地编译的命令行程序，启用优化、模型生成、二分搜索与实时进度输出，并关闭官方非线性支持要求关闭的 `toplevel_propagation`。两者都运行单个最小化目标。

Z3 的计时对应带模型回调的 API 执行，与此前裸命令行试跑的耗时可能不同。

## 结果位置

每次运行建立新目录，保留以前的结果；`latest` 指向最新一次运行：

```text
result/z3/latest/results.csv
result/optimathsat/latest/results.csv
```

具体目录结构为 `result/{solver}/{UTC时间戳}/`：

- `results.csv`：每个实例一行，完成后立即刷新，可以边运行边查看。
- `results.jsonl`：完整结构化记录，每个实例一行。
- `metadata.json`、`inputs.txt`：预算、并行数、计时定义、脚本哈希及输入清单。
- `summary.json`：结束或中断后的结果数量。
- `cases/{路径哈希}/result.json`：单例结果及调用命令。
- `cases/{路径哈希}/events.jsonl`：实时目标改进事件，超时前已找到的值也会保留。
- 单例目录的 `stdout.txt`、`stderr.txt`、`worker-stderr.txt`：求解器和包装进程的原始输出。

## 目标值和时间字段

| 字段 | 含义 |
| --- | --- |
| `best_value` | 求解器报告或模型中观察到的最佳有限候选目标值；超时也可能有值 |
| `time_to_best_seconds` | 首次观察到最终保留的 `best_value` 的时间；重复报告同一值不会重置时间 |
| `best_value_source` | `model_callback` 为 Z3 模型回调，`solver_progress` 为 OptiMathSAT 进度输出，`final_model` 为结束后的模型查询 |
| `reported_optimum` | 求解器最终报告的目标值，保留精确有理数、代数数、`epsilon` 和 `oo` 表达式 |
| `time_to_reported_optimum_seconds` | 首次观察到该报告值的时间；有匹配的候选记录时使用原时间，否则使用目标查询/上下界读取的观察时间 |
| `time_to_optimal_seconds` | 对一致的有限最优报告，求解器完成最优性搜索的时间；与找到候选值的时间分别记录 |
| `model_value` | 最终模型中的具体目标值 |
| `lower_bound`、`upper_bound` | 求解器最后报告的优化上下界 |
| `optimality_status` | `attained`、`infimum`、`unbounded`、`unconfirmed`、`inconsistent` 或 `none` |
| `solver_reported_optimal` | 求解器自身是否宣称优化已完成；即使为 True，`inconsistent` 仍表示报告存在矛盾 |
| `status` | `sat`、`unsat`、`unknown`、`timeout`、`interrupted` 或 `error` |
| `total_seconds` | 单例完整运行及清理耗时 |

所有时间为**从该实例的包装进程启动前开始的单调壁钟秒数**，包括启动、解析和调度开销，不包括排队等待。进度输出的时间是收到该消息的时间；`final_model` 表示只在最终查询中观察到该值，不能推断它更早何时已经在求解器内部出现。

`attained` 表示求解器的有限目标报告与模型、最佳候选及可用上下界一致；脚本没有做独立最优性证明。`infimum` 表示无穷小边界，`unbounded` 表示报告无界，这些都不是某个已取得的有限最优解。`inconsistent` 会保留原始报告用于审查。OptiMathSAT 1.7.4.b 已在前面的简单 NRA 探针中出现目标/模型/上下界矛盾，因此不能仅凭 `sat` 或 `solver_reported_optimal` 判定结果正确。

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
python3 -m unittest discover -s script/tests -v
bash -n script/run.sh
```

测试使用小型公式、临时目录和用于调度测试的短进程；不会启动完整的 5744 例 NIA 批跑。
