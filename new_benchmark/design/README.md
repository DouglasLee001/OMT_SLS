# D 最优实验设计

为已知候选特征向量选择非负整数实验次数，总次数固定。
目标最大化信息矩阵 `M = sum(n_i × a_i × a_i^T)` 的行列式，并要求其为正。
非线性来自行列式中的整数次数乘积；维度越高，多项式次数越高。

本类有 43 个新增实例，另有 3 个来源重放；重放不计新增。
另含同一应用的 1 份编码对照，不增加应用实例数。
`src/` 放源码，`instance/` 放生成的参数、SMT 模型、见证和核验结果。

主要 profiles：

- `integer`：生成整数候选向量，可设维度、候选数、总次数和随机种子。
- `rational`：使用精确有理数向量；`tiny`、`tiny-rational` 为微型例。
- `reference`：重放三组参考向量。

所有命令均从 `/home/lbh/OMT` 执行：

```bash
python3 new_benchmark/design/src/generate.py --profile integer --dimension 4 --candidates 8 --runs 8 --seed 10 --encoding cauchy-binet
python3 new_benchmark/packing/src/manage.py validate new_benchmark --tier witness
```

编码可选 `cauchy-binet` 或 `determinant`，同一输入的编码变体不计新增。
可加 `--out /tmp/nia-design`，结果位于该目录的 `design/instance/`。
核验总览见 [release_summary.json](../packing/src/_support/reports/release_summary.json)。
见证通过表示解可行；最优性以报告中的独立核验结果为准。
