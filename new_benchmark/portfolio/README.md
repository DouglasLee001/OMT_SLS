# 整数持仓风险

选择各资产的非负整数持仓，满足预算和收益等约束，最小化风险。
风险是持仓的二次多项式，含真实的整数交叉乘积。
输出目标 `cost = D × 风险`，其中正整数 `D` 用于清除系数分母。

本类有 7 个新增实例，另有 4 个来源重放；重放不计新增。
`src/` 放源码，`instance/` 放生成的参数、SMT 模型、见证和核验结果。

主要 profiles：

- `synthetic-gram`：生成合成整数风险数据，可设资产数和随机种子。
- `tiny`：二资产微型例，便于检查语义。
- `minlplib`：重放已缓存的四个 DAX 原模型。

所有命令均从 `/home/lbh/OMT` 执行：

```bash
python3 new_benchmark/portfolio/src/generate.py --profile synthetic-gram --assets 8 --seed 10
python3 new_benchmark/packing/src/manage.py validate new_benchmark --tier witness
```

可加 `--out /tmp/nia-portfolio`，结果位于该目录的 `portfolio/instance/`。
核验总览见 [release_summary.json](../packing/src/_support/reports/release_summary.json)。
见证通过表示解可行；最优性以报告中的独立核验结果为准。
