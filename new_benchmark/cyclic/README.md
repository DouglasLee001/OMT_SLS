# 循环资源调度

为任务安排周期内相位和迭代编号，满足依赖关系与资源容量限制。
目标最小化 `cost = U × T + C`，其中 `T` 为周期、`C` 为执行跨度。
非线性来自整数迭代编号与可变周期的乘积 `k[i] × T`。

本类有 8 个新增实例，另有 4 个来源重放；重放不计新增。
`src/` 放源码，`instance/` 放生成的参数、SMT 模型、见证和核验结果。

主要 profiles：

- `planted`：生成带已检查可行见证的新调度数据。
- `minizinc`：重放已缓存的循环资源调度原模型与数据。
- `tiny-a`、`tiny-b`：微型语义检查例。

所有命令均从 `/home/lbh/OMT` 执行：

```bash
python3 new_benchmark/cyclic/src/generate.py --profile planted --real-tasks 12 --resources 2 --period 10 --extra-edges 16 --seed 10
python3 new_benchmark/packing/src/manage.py validate new_benchmark --tier witness
```

`--period` 控制生成见证，模型中的周期仍是待优化变量。
可加 `--out /tmp/nia-cyclic`，结果位于该目录的 `cyclic/instance/`。
核验总览见 [release_summary.json](../packing/src/_support/reports/release_summary.json)。
见证通过表示解可行；最优性以报告中的独立核验结果为准。
