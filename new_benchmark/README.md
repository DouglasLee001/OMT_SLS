# 应用型 OMT(NIA) 基准

这里有四类整数非线性优化应用，生成器只需要 Python 3 标准库。
所有命令均从 `/home/lbh/OMT` 执行。

| 应用目录 | 新增实例 |
| --- | ---: |
| [portfolio/](portfolio/README.md)：整数持仓风险 | 7 |
| [cyclic/](cyclic/README.md)：循环资源调度 | 8 |
| [packing/](packing/README.md)：最小面积排样 | 8 |
| [design/](design/README.md)：D 最优实验设计 | 43 |

共 66 个新增应用实例；另有 19 个来源重放，重放不计新增。
实验设计另有 1 份同一应用的编码对照，共 85 个应用实例、86 个优化模型。

每个应用目录只有 `README.md`、`src/` 和 `instance/`。
`src/` 放生成源码，`instance/` 放参数、SMT 模型、见证和核验结果。

```bash
python3 new_benchmark/portfolio/src/generate.py --profile synthetic-gram --assets 8 --seed 10
python3 new_benchmark/cyclic/src/generate.py --profile planted --real-tasks 12 --resources 2 --period 10 --extra-edges 16 --seed 10
python3 new_benchmark/packing/src/generate.py --profile rectangles --items 12 --max-side 8 --seed 10
python3 new_benchmark/design/src/generate.py --profile integer --dimension 4 --candidates 8 --runs 8 --seed 10 --encoding cauchy-binet
python3 new_benchmark/packing/src/manage.py validate new_benchmark --tier witness
```

可加 `--out /tmp/nia-benchmark` 将生成结果放到其他目录。
核验总览见 [release_summary.json](packing/src/_support/reports/release_summary.json)。
见证通过表示解可行；最优性以报告中的独立核验结果为准。
