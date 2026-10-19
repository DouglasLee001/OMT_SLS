# 最小面积排样

把固定方向的矩形工件放入整数宽高的容器，工件之间不得重叠。
允许边界接触，目标最小化容器面积 `cost = W × H`。
整数宽高的乘积是本应用的非线性。

本类有 8 个新增实例，另有 8 个来源重放；重放不计新增。
`src/` 放源码，`instance/` 放生成的参数、SMT 模型、见证和核验结果。

主要 profiles：

- `rectangles`：生成固定方向矩形，可设工件数、最大边长和随机种子。
- `squares`：放置边长为 `1..n` 的方块；`n=3..10` 为来源重放。

所有命令均从 `/home/lbh/OMT` 执行：

```bash
python3 new_benchmark/packing/src/generate.py --profile rectangles --items 12 --max-side 8 --seed 10
python3 new_benchmark/packing/src/manage.py validate new_benchmark --tier witness
```

方块例可使用 `--profile squares --n 1,2,3`。
可加 `--out /tmp/nia-packing`，结果位于该目录的 `packing/instance/`。
核验总览见 [release_summary.json](src/_support/reports/release_summary.json)。
见证通过表示解可行；最优性以报告中的独立核验结果为准。
