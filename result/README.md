# 结果文件说明

- [original/original.csv](original/original.csv)：原始 benchmark 的结果。
- [new/new.csv](new/new.csv)：新生成 benchmark 的完整结果，共 566 例。
- [new/new_bound.csv](new/new_bound.csv)：对 [new/new.csv](new/new.csv) 使用与 [original/original_bound.csv](original/original_bound.csv) 相同的 `bounded` 数值筛选条件，保留 563 例；实例副本在 `new_benchmark/{类别}/bounded/`。
- [new/new_unsolved_bounded_sol.csv](new/new_unsolved_bounded_sol.csv)：在 [new/new_bound.csv](new/new_bound.csv) 中，两个对手均未确认最优且至少一个报告候选值的 243 例；实例副本在 `new_benchmark/{类别}/unsolved_bounded_sol/`。
- [original/original_bound.csv](original/original_bound.csv)：`bounded` 表示去掉疑似无界例子后的结果。具体按任一求解器的 `best_value` 绝对值大于 `10000000000` 筛除；这是实验筛选规则，不是严格的有界性或无界性证明。
- [original/original_unsolved_bounded_sol.csv](original/original_unsolved_bounded_sol.csv)：`unsolved_bounded_sol` 表示在上述 `bounded` 子集中，对手能找到一个尚未证明最优的候选解。具体要求两个对手都未确认最优，但至少一个对手报告了候选值。

所有时间单位为秒；目标值和对应时间为空，表示该次运行没有记录到候选值。`is_optimal=false` 表示未确认最优。

两个新增 CSV 保留 [new/new.csv](new/new.csv) 的列格式，第二列为来源类别。它们根据现有 1200 秒完整实验静态筛选，未重新求解；原有结果文件不变。`bounded` 的数值条件应用于 SMT 目标值，可能受到目标缩放影响，不能据此断言被剔除实例真正无界。
