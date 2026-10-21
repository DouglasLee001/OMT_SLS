# NIA_LS 的 OMT(NIA) 输入通道

现在可以把单目标 OMT(NIA) 的 SMT2 文件转换后，真正读入 NIA_LS 的变量、单项式、文字、子句和独立优化目标中。默认使用 Z3 预处理，原来的 SMT 可行性输入通道保留。

本阶段完成**输入与目标保留**。尚未实现 OMT 局部搜索、改进目标的循环和最优性证明。因此 OMT 输入必须加 `--import-only`；不加时程序会明确报错，避免把第一个可行解误报为优化结果。

## 使用

从 `/home/lbh/OMT` 执行：

```bash
make -C NIA_LS/src -j8
./NIA_LS/import_omt.sh new_benchmark/packing/instance/squares-n02--8d5fbe62518a.smt2 /tmp/packing.nia
```

这条命令依次转换、检查，并在 `/tmp/packing.nia` 保留中间文件。省略第二个参数时，使用检查后自动删除的临时文件。

也可以分开执行：

```bash
python3 NIA_LS/converter/omt_to_ls.py input.smt2 -o /tmp/input.nia
NIA_LS/src/nia_ls /tmp/input.nia --import-only
```

JSON 摘要中的 `status=imported` 表示约束和目标已读入，**不表示已求出可行解**；`preprocessing_unsat` 表示转换阶段已得到矛盾。摘要还包括 `direction`、原始目标、变量数量、目标常数、目标项数量和目标涉及的变量。`search_started=false` 明确表示未执行搜索。

需要对比预处理效果时：

```bash
python3 NIA_LS/converter/omt_to_ls.py input.smt2 -o /tmp/input.nia --preprocess=off
```

`off` 关闭传播和等式消元；必要的表达式规范化、整数 ITE 消除和 CNF 转换仍会执行。

## 如何保住优化目标

不能只把硬约束送进可满足性预处理。例如：

```smt2
(assert (= cost (* x x)))
(minimize cost)
```

如果预处理只看到约束，就可能消掉 `cost`。本实现参考本地 Z3 自身的 Optimize 实现，在预处理前加入一个全新的 `Int -> Bool` 标记函数：

```text
硬约束 ∧ marker(cost)
    ↓ simplify / propagate-values / solve-eqs / simplify / elim-term-ite / simplify
改写后的硬约束 ∧ marker(x*x)
```

这个标记只用于携带目标；其解释可自由选择为真，不限制原问题的整数赋值。预处理结束后，提取标记的唯一参数作为新目标，再移除标记，只把硬约束转为 CNF。这样仍然可以利用等式消元，且优化 `cost` 与优化改写后的 `x*x` 的可行目标值相同。若约束固定 `x=7`，目标会正确成为常数 `49`。

每一阶段都会检查标记恰好保留一次；无法确认时明确拒绝转换。直接发现矛盾时使用不可满足状态和空子句，保留原目标文本；此时中间文件中的数值目标置零，因为没有可行赋值。

方向单独保存为 `min` / `max`，不会把 maximize 的负号弄反。仅在目标中出现的自由变量也会保存。目标中的 ITE 通过新整数变量及定义约束消除。复杂乘积可能引入精确的辅助整数等式，避免直接分配展开后的巨大多项式；这些等式保留在硬约束中。

前端仅调用 `converter/z3_fork` 的 Python SDK 解析器与 tactic，不调用该 fork 的 `Solver.check()` 或 `Optimize.check()`，也不走其原有的 SMT 求解后打印路径。用户已有的 `qfnia_tactic.cpp` 修改保留。

## 原生数据结构

新格式使用独立读取器 `src/nia_omt_input.cpp`，绕过旧 `build_lits()` 和 `build_instance()` 的再次消元、浮点边界计算与系数截断。硬边界保留为约束，不会转换为近似数值边界。

- 变量的整数/布尔类型和名称进入 `_vars` / `name2var`；被预处理消掉的原变量不保证仍存在。
- 整数单项式进入 `_terms`，约束进入 `_lits` / `_clauses`，并构建变量、文字、子句和单项式之间的索引。
- `omt_direction` 保存优化方向，`omt_objective_constant` 与 `omt_objective_terms` 保存改写后的目标。目标项直接引用同一 `_terms` 和最终变量编号。
- `omt_original_objective` 保存原始目标文本，`omt_source` 保存源文件路径。中间变量表保留现存变量名称；这不是对所有已消元原变量的完整模型重构。
- `evaluate_omt_objective(assignment, value, error)` 可以在原生变量赋值上计算目标；乘法、幂和求和都检查溢出。

旧字段 `best_found_cost` 仍表示未满足硬子句的数量，不是 OMT 的目标值。后续实现优化时应从上述 `omt_*` 字段和目标求值接口读取实际目标，且只在满足全部硬约束的赋值上更新最优记录。

所有输入系数、常数和目标系数按十进制精确读取到 signed `__int128`，超范围明确报错。支持大于 10000000 的系数及 4、5 次和更高次的项，不再套用旧解析器的限制。多项式求值仍受 128 位中间运算范围限制。现有局部搜索的默认变量边界和部分计算逻辑仍需在实现 OMT 搜索时进一步处理；本次导入检查不以这些默认边界削弱逻辑约束，也不启动搜索。

## 中间格式 `NIA_LS_OMT 1`

文本按空白分隔，名称、路径、原始目标用 UTF-8 的十六进制编码，`-` 代表允许为空的文本。变量编号从 0 开始，文字编号从 1 开始，声明必须按编号递增。SMT2 源文件不被修改。

```text
NIA_LS_OMT 1
STATUS ready
SOURCE <路径的UTF8hex>
ORIGINAL_OBJECTIVE <原目标的UTF8hex>
DIRECTION min
VARIABLES N
0 I <变量名的UTF8hex>
...
LITERALS M
1 B <布尔变量编号>
2 L <常数> <项数>
<系数> <因子数> <变量编号> <正整数指数> ...
...
CLAUSES C
<子句长度> <带符号的文字编号> ...
...
OBJECTIVE <常数> <项数>
<系数> <因子数> <变量编号> <正整数指数> ...
...
END
```

变量类型 `I` 为整数，`B` 为布尔；整数文字的 `L` 表示 `常数 + Σ(系数 × 单项式) <= 0`，`E` 表示同一多项式等于零。严格整数不等式用加一精确转换。子句中的负编号表示文字的逻辑否定。目标采用相同多项式表示。读取器检查格式版本、类型、编号、系数范围、完整性与尾部垃圾，错误时返回非零退出码。

目前支持一个非增量的整数优化目标，以及 QF_NIA 的整数多项式、布尔组合和 ITE；多目标、量词、Real、位向量、一般 UF、div/mod 等输入明确拒绝，不会当作转换成功。

## 验证与批量检查

```bash
make -C NIA_LS/src test-omt-import
python3 NIA_LS/tests/check_omt_benchmarks.py --jobs 16 --timeout 60
```

批量检查默认读取原实验 CSV 中的 5744 个 original 实例和 `new_benchmark/*/instance` 中的 566 个新实例，逐个执行转换及原生读入，不运行求解。报告保存为 `NIA_LS/validation/omt_import_coverage.csv`，不会写入实验 `result`。中间文件默认自动删除，`--keep-converted /path/to/output` 可保留。

如需扩展到原始 QF_NIA 目录下所有文件，而不仅是原实验清单：

```bash
python3 NIA_LS/tests/check_omt_benchmarks.py --all-original --jobs 16 --timeout 60 --report /tmp/omt_all_import.csv
```

回归测试独立使用已编译的官方 Z3 SDK 在小型有限域问题上枚举可行目标值，对比转换前后集合是否一致；不使用 fork 求解结果作为等价性依据。另检查目标变量被消去、常量目标、自由目标、min/max、布尔/ITE、带空格和 Unicode 的名称、大系数、高次项及损坏输入。2026-10-20 实测结果：

| 输入集合 | 实例数 | 转换及原生读入成功 | 失败 |
| --- | ---: | ---: | ---: |
| original 原实验清单 | 5744 | 5744 | 0 |
| portfolio | 131 | 131 | 0 |
| cyclic | 132 | 132 | 0 |
| packing | 136 | 136 | 0 |
| design | 167 | 167 | 0 |
| 合计 | 6310 | 6310 | 0 |

以上每一例都经过“SMT2 转换 → 固定的最终 NIA_LS 可执行程序副本 → 原生数据结构读入”，报告文件为 `validation/omt_import_coverage.csv`。逐例路径集合已与实验 CSV 和新实例目录核对，均未缺失或重复。当前集合全部返回 `imported`；这不是可行性或最优性求解结果。

最终 `NIA_LS_DATASET_SMOKE=1 make -C NIA_LS/src test-omt-import` 的 16 项测试全部通过（包括 13 个真实实例代表），原生 C++ 驱动也通过大系数、目标求值与损坏输入检查；旧格式简单可行性实例仍正常返回 `sat`。

这里的 original 覆盖数专指原实验使用的 5744 例，不代表原始 QF_NIA 目录全部 33397 个文件都做过转换和原生读入检查。


另外对未入原实验清单的 27653 个原始文件做了完整静态语法扫描：全部来自 VeryMax（CInteger 2072、ITS 21396、SAT14 4185），均为 QF_NIA、单个 minimize、变量目标和一次 check-sat；仅使用已支持的 Int/Bool、整数多项式及布尔组合，未发现新的语法类型、解析错误或 div/mod、Real、UF、量词、多目标、增量查询。这说明这部分没有发现额外的语法障碍；仍不能替代上面 `--all-original` 的逐例转换与原生读入验证。
