# 应用型 OMT(NIA) 基准

这里有四类整数非线性优化应用，生成器只需要 Python 3 标准库。

| 应用目录 | 已有首批新增 | 本轮新增 | 当前模型总数 |
| --- | ---: | ---: | ---: |
| [portfolio/](portfolio/README.md)：整数持仓风险 | 7 | 120 | 131 |
| [cyclic/](cyclic/README.md)：循环资源调度 | 8 | 120 | 132 |
| [packing/](packing/README.md)：最小面积排样 | 8 | 120 | 136 |
| [design/](design/README.md)：D 最优实验设计 | 43 | 120 | 167 |
| 合计 | 66 | 480 | 566 |

本轮保留原有 86 个模型，每类新增 120 个应用实例，分为 `small`、`medium`、`large` 各 40 个。当前共 546 个新增应用实例，另有 19 个来源重放（不计新增）；含实验设计的 1 份编码对照，共 565 个应用实例、566 个优化模型。

| 本轮规模 | portfolio：资产数 / 因子数 | cyclic：任务数 / 资源数 | packing：工件数 / 最大边长 | design：维度 / 候选数 / 总次数 |
| --- | --- | --- | --- | --- |
| `small` | 6 / 3、8 / 4 | 8 / 2、12 / 2 | 6 / 6、8 / 8 | 2 / 6 / 8、3 / 8 / 9 |
| `medium` | 12 / 6、16 / 8 | 16 / 2、24 / 3 | 10 / 8、12 / 10 | 3 / 12 / 15、4 / 12 / 16 |
| `large` | 20 / 10、24 / 12 | 28 / 3、36 / 3 | 16 / 12、20 / 14 | 4 / 16 / 24、5 / 14 / 25 |

这些标签只表示参数规模，不代表固定求解难度或保证求解成功。各类 `src/scaled_tiers.json` 记录完整配置，`src/scaled_instances.txt` 列出本轮新增文件。批量实验入口见 [script/README.md](../script/README.md)。

各应用的 README 给出了从输入数据到 SMT 文件的详细编码过程，包含变量表、数学约束、目标值还原方式、实际小例和源码入口：

| 编码说明 | 重点 |
| --- | --- |
| [整数持仓风险](portfolio/README.md) | 预算和收益约束、二次风险、有理系数清分母 |
| [循环资源调度](cyclic/README.md) | 周期内相位、迭代编号、依赖与资源约束、跨度和目标 |
| [最小面积排样](packing/README.md) | 固定尺寸工件、四方向不重叠析取、面积目标与可行上界 |
| [D 最优实验设计](design/README.md) | 信息矩阵、整数实验次数、Cauchy–Binet 与行列式展开、缩放 |

每个应用目录只有 `README.md`、`src/` 和 `instance/`。
`src/` 放生成源码；`instance/` 只放可直接用于优化实验的 `.smt2`，文件平铺，无子目录。

```bash
cd /home/lbh/OMT
python3 new_benchmark/portfolio/src/generate.py --profile synthetic-gram --assets 8 --risk-factors 4 --seed 100 --count 10
python3 new_benchmark/cyclic/src/generate.py --profile planted --real-tasks 12 --resources 2 --period 10 --extra-edges 16 --seed 100 --count 10
python3 new_benchmark/packing/src/generate.py --profile rectangles --items 12 --max-side 8 --seed 100 --count 10
python3 new_benchmark/design/src/generate.py --profile integer --dimension 4 --candidates 8 --runs 8 --seed 100 --count 10 --encoding cauchy-binet
```

可调整 `--count`、起始 `--seed` 和各类规模参数；上述命令分别尝试种子 100–109。
相同实例自动跳过，实际数量以输出为准；生成过程不启动求解。
可选 `--out /tmp/nia-benchmark` 切换输出根目录，文件位于其中的 `{family}/instance/`。
