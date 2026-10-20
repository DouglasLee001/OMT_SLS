# D 最优实验设计

这一类把“每种候选实验做多少次”编码成整数优化：候选实验的特征向量已知，总实验次数固定，选择各候选的非负整数重复次数，使信息矩阵的行列式尽可能大。实例使用 `QF_NIA`，目标命令为 `(maximize cost)`。

已有首批新增实例 43 个，本轮新增 120 个；另含 3 个来源重放及同一应用的 1 份编码对照，当前共 166 个应用实例、167 个优化模型。`src/` 放生成源码。默认输出 `/home/lbh/OMT/new_benchmark/design/instance/`，只平铺可直接用于优化实验的 `.smt2`，无子目录。


## 实验筛选子集

[instance/](instance/) 保留本类全部 167 个优化模型；[bounded/](bounded/) 有 167 个，[unsolved_bounded_sol/](unsolved_bounded_sol/) 有 100 个，均为平铺的 `.smt2` 副本。

`bounded` 按任一求解器 `abs(best_value)>10000000000` 剔除；`unsolved_bounded_sol` 进一步要求双方都未确认最优、至少一方有候选值。数值条件不是无界性证明。筛选来自当前 [new.csv](../../result/new/new.csv) 的 1200 秒完整实验，详见 [统一筛选说明](../README.md#实验筛选子集)。两个新目录为静态副本；生成器仍只输出到 `instance/`，不会自动更新实验筛选子集。

## 1. 从应用问题到数学模型

设有 `m` 个候选实验，其特征向量为 `a_0, …, a_(m-1)`，每个向量有 `d` 个分量。例如，向量可描述一个候选实验设置下各回归项的取值。给定总次数 `N`，决策变量 `n_i` 表示第 `i` 个候选实验实际执行多少次。

在通常的线性实验设计解释下，信息矩阵为：

```text
M_original(n) = Σ_i n_i · a_i · a_i^T
```

`a_i · a_i^T` 是外积，其第 `(r,c)` 项为 `a_i[r]·a_i[c]`。增加一个候选的实验次数，就是给信息矩阵增加相应次数的外积。信息矩阵的逆与参数估计的不确定性有关；D 最优设计选择使行列式最大的分配，以减小相应置信椭球的体积。

本类求解以下整数问题：

```text
maximize    det(M_original(n))
subject to  n_i ∈ Z, 0 ≤ n_i ≤ min(N, u_i)
            Σ_i n_i = N
            det(M_original(n)) > 0
```

`u_i` 为可选的候选次数上限，默认全部等于 `N`。这是精确整数次数设计；不存在把连续权重四舍五入成整数次数的步骤。因为所有 `n_i≥0`，信息矩阵总是半正定；再要求行列式为正，便保证其正定，排除了无法识别全部 `d` 个方向的奇异设计。对正定信息矩阵，最大化行列式也等价于最大化 `log det(M_original)`，但 `.smt2` 中只编码多项式，不出现对数。

有些资料使用权重 `w_i=n_i/N`，并把信息矩阵写成 `M_normalized=Σ_i w_i·a_i·a_i^T=M_original/N`。于是：

```text
det(M_normalized) = det(M_original) / N^d
```

同一实例中 `N` 固定，因此两种约定选出的最优计数分配相同，报告的目标值相差固定的 `N^d`。本目录默认使用未除以 `N` 的信息矩阵。

应用和转换依据见 [生成指南](../../papers/generate_benchmark/omt_nia_codex_handoff/prompts/04_d_optimal_design.txt)。这里的整数、有理数候选点和 SMT 编码由本项目生成；这些新实例不是论文作者的浮点实验数据。

## 2. 输入、变量与合法上下界

生成数据保存在对应的 `src/case_data/<实例名>/data.json`。其中 `vectors` 保存的是整数向量 `b_i`；对于整数输入，`b_i=a_i`；对于有理输入，`b_i=L·a_i`，`L` 为共同分母，见第 5 节。SMT 实际构造整数信息矩阵：

```text
M(n) = Σ_i n_i · b_i · b_i^T = L^2 · M_original(n)
cost = det(M(n))
```

| 数据或 SMT 符号 | 含义和实际约束 |
| --- | --- |
| `dimension`，记为 `d` | 特征维度，即每个候选向量的长度。 |
| `vectors`，共 `m` 行 | 每行是一个 `d` 维整数向量 `b_i`；所有分量都是生成时已知的常量。 |
| `runs`，记为 `N` | 总实验次数；`Σ_i n_i=N`。 |
| `upper_bounds[i]`，记为 `u_i` | 候选 `i` 的次数容量，默认 `N`。 |
| `n_i : Int` | 第 `i` 个候选的次数，`0≤n_i≤min(N,u_i)`。索引从 0 开始。 |
| `cost : Int` | 整数信息矩阵的行列式，`0≤cost≤U`，另外断言 `cost>0`。 |
| `M_r_c : Int` | 仅 `determinant` 编码声明的矩阵辅助变量，且只声明 `r≤c` 的上三角部分。 |

代码中 `cost` 上界采用半正定矩阵的 Hadamard 不等式。令：

```text
D_r = N · max_i(b_i[r]^2)
U   = Π_(r=0,…,d-1) D_r
```

因为 `M[r,r]=Σ_i n_i·b_i[r]^2≤N·max_i(b_i[r]^2)=D_r`，而半正定矩阵满足 `det(M)≤Π_r M[r,r]`，所以 `cost≤U` 对所有合法分配成立。这个上界只用于给目标变量合法的有限范围，不是预先知道的最优值。

在 `determinant` 编码中，令 `B_rc=N·max_i |b_i[r]·b_i[c]|`，则对角辅助变量范围是 `0≤M_r_r≤B_rr`，非对角范围是 `−B_rc≤M_r_c≤B_rc`；同时用等式约束其精确值：

```text
M_r_c = Σ_i (b_i[r]·b_i[c]) · n_i
```

这些范围由总次数和已知向量直接推导，允许所有合法分配。代码不会为 `r>c` 另建变量，而是利用对称性引用 `M_c_r`。

## 3. 默认编码：Cauchy–Binet 多项式

把 `b_i` 按行组成 `m×d` 矩阵 `B`，则 `M=B^T·diag(n)·B`。Cauchy–Binet 公式给出：

```text
cost = Σ_(S⊆{0,…,m-1}, |S|=d) c_S · Π_(i∈S) n_i
c_S  = det(B_S)^2
```

`B_S` 是从 `B` 中选取 `S` 对应的 `d` 行形成的方阵。每个 `c_S` 都是非负整数常量：生成器先用精确整数 Bareiss 消元算出各常数子式，再平方；子式为零的项直接省略。**求解器处理的是最终的计数变量多项式，不需要执行子式计算。**

本编码只声明 `m` 个 `n_i` 和一个 `cost`，不声明 `M_r_c`。非线性来自各个 `d` 次乘积 `n_i1·…·n_id`；这些计数是多值整数，不是布尔变量。最多需要分析 `C(m,d)` 个候选子集，实际写入的项数等于非零子式的个数。增加 `m`、`d`、次数预算或系数大小，都会以不同方式影响模型规模与搜索空间；参数规模不直接保证运行时间。

### 真实小例子：三个二维候选

对应 [tiny-cauchy-binet--f57a37aa3a23.smt2](instance/tiny-cauchy-binet--f57a37aa3a23.smt2)，输入为：

```text
b_0 = (1,0), b_1 = (0,1), b_2 = (1,1)
d=2, m=3, N=3, u_0=u_1=u_2=3, L=1
```

三个二行子式分别为：

```text
det([b_0;b_1]) = 1
det([b_0;b_2]) = 1
det([b_1;b_2]) = −1
```

平方后系数都为 1，所以最终约束是：

```text
n_0+n_1+n_2 = 3
cost = n_0·n_1 + n_0·n_2 + n_1·n_2
cost > 0
```

以下就是该文件的完整优化内容，省略了首行注释：

```smt2
(set-logic QF_NIA)
(declare-fun n_0 () Int)
(assert (>= n_0 0))
(assert (<= n_0 3))
(declare-fun n_1 () Int)
(assert (>= n_1 0))
(assert (<= n_1 3))
(declare-fun n_2 () Int)
(assert (>= n_2 0))
(assert (<= n_2 3))
(assert (= (+ n_0 n_1 n_2) 3))
(declare-fun cost () Int)
(assert (>= cost 0))
(assert (<= cost 9))
(assert (> cost 0))
(assert (= cost (+ (* 1 n_0 n_1) (* 1 n_0 n_2) (* 1 n_1 n_2))))
(maximize cost)
(check-sat)
```

这里 `U=(3·1)·(3·1)=9`，但实际最优值为 3：取 `n=(1,1,1)` 时，`M=[[2,1],[1,2]]`，行列式为 `2·2−1·1=3`。另一方面，由 `(n_0−n_1)^2+(n_0−n_2)^2+(n_1−n_2)^2≥0` 可得 `n_0·n_1+n_0·n_2+n_1·n_2≤(n_0+n_1+n_2)^2/3=3`，所以这个小例子的最优性可以直接手工证明。

## 4. 对照编码：矩阵变量和排列展开

`--encoding determinant` 先用线性等式定义上三角矩阵变量，再按排列公式编码行列式：

```text
cost = Σ_(π∈S_d) sign(π) · Π_(r=0,…,d-1) M[r,π(r)]
```

`S_d` 是 `d` 个下标的全部排列；偶排列取正号，奇排列取负号。代码逐个枚举排列，并通过 `M_min(r,c)_max(r,c)` 引用对称矩阵元素，最多写入 `d!` 个带符号乘积，不使用行列式函数或变量除法。

同一组 tiny 输入的对照文件为 [tiny-determinant--36c3578445f4.smt2](instance/tiny-determinant--36c3578445f4.smt2)。其矩阵定义为：

```text
M_0_0 = n_0+n_2
M_0_1 = n_2
M_1_1 = n_1+n_2
cost  = M_0_0·M_1_1 − M_0_1·M_0_1
```

实际文件中的对应片段为：

```smt2
(declare-fun M_0_0 () Int)
(assert (>= M_0_0 0))
(assert (<= M_0_0 3))
(assert (= M_0_0 (+ (* 1 n_0) (* 1 n_2))))
(declare-fun M_0_1 () Int)
(assert (>= M_0_1 (- 3)))
(assert (<= M_0_1 3))
(assert (= M_0_1 (* 1 n_2)))
(declare-fun M_1_1 () Int)
(assert (>= M_1_1 0))
(assert (<= M_1_1 3))
(assert (= M_1_1 (+ (* 1 n_1) (* 1 n_2))))
(assert (= cost (+ (* M_0_0 M_1_1) (- (* M_0_1 M_0_1)))))
```

代入矩阵定义后，`(n_0+n_2)(n_1+n_2)−n_2^2=n_0n_1+n_0n_2+n_1n_2`，与上一节完全相同。两种写法可能带来不同的求解性能，但它们属于同一应用实例的编码变体，统计新增应用实例时只算一次。

## 5. 有理数输入如何保持为 OMT(NIA)

对于有理向量，生成器取所有分量分母的最小公倍数 `L>0`，用整数 `b_i=L·a_i` 建模。因此：

```text
M(n) = L^2 · M_original(n)
cost = det(M(n)) = L^(2d) · det(M_original(n))
det(M_original(n)) = cost / L^(2d)
det(M_normalized(n)) = cost / (L^(2d) · N^d)
```

缩放因子在同一实例中是固定正数，所以不改变最优计数分配。`.smt2` 中没有 `Real`、浮点舍入或除法：最后的单位恢复在解释结果时进行。通用 `result/new/new.csv` 中的最好值是 SMT 的整数 `cost`，不会自动恢复为原有理单位。

真实例子 [tiny-rational-cauchy-binet--9140183a1602.smt2](instance/tiny-rational-cauchy-binet--9140183a1602.smt2) 对应：

```text
a_0=(1/2,0), a_1=(0,1/2), a_2=(1/2,1/2)
L=2, b_0=(1,0), b_1=(0,1), b_2=(1,1), d=2, N=3
```

清分母后向量与整数 tiny 相同，所以两个 `.smt2` 的约束和整数最优 `cost=3` 相同；但原信息矩阵最优行列式是 `3/2^4=3/16`。如果使用除以总次数的归一化信息矩阵，其值进一步为 `(3/16)/3^2=1/48`。因此，比较不同有理缩放输入的应用目标值时，必须同时查看其 `L`，不能只比较文件中的整数 `cost`。该例的 `L` 见 [data.json](src/case_data/tiny-rational-cauchy-binet--9140183a1602/data.json)。

## 6. 候选点、来源重放和可行见证

当前支持以下 `profile`，每种都使用上面的相同整数优化语义：

| `profile` | 输入如何得到 |
| --- | --- |
| `integer` | 先放入 `d` 个标准基向量保证候选矩阵满秩，再使用 `random.Random(seed)` 生成剩余非零向量；每个分量均匀取自 `[-coefficient_bound,coefficient_bound]`，默认界为 3。允许重复或成比例的候选向量，不会擅自合并其次数变量。 |
| `rational` | 使用明确提供的精确有理向量，自动清共同分母；也可提供 `integer_vectors` 和 `common_vector_denominator`。不接受浮点数作为精确有理输入。 |
| `tiny` / `tiny-rational` | 固定的三候选二维回归例子，分别使用 `L=1` 和 `L=2`。 |
| `reference` | 从已有 [reference_starter 清单](../../papers/generate_benchmark/omt_nia_codex_handoff/reference_starter/generated/manifest.json) 重放 3、4、5 维的既有整数候选数据，标记为来源重放。 |

例如 [三维来源重放](instance/design_d3_m6-reference-cauchy-binet--858d1b65df44.smt2) 使用 `m=6,N=6`，其六个候选为 `(1,0,0)`、`(0,1,0)`、`(0,0,1)`、`(-2,1,3)`、`(3,3,-3)`、`(-1,-3,0)`。保存的见证次数为 `[2,0,0,2,2,0]`，对应 `cost=1152`，可对照 [输入](src/case_data/design_d3_m6-reference-cauchy-binet--858d1b65df44/data.json) 与 [见证](src/case_data/design_d3_m6-reference-cauchy-binet--858d1b65df44/witness.json)。它是既有参考数据，不算新生成的应用。

生成器为每个应用寻找一个可行见证：先从上限至少为 1 的候选中找到 `d` 个线性无关向量，给它们各分配一次，再在容量允许的情况下分配剩余 `N−d` 次。非零子式和正次数保证信息矩阵正定；后加的外积仍为半正定，不会破坏正定性。若容量不足或不能形成满秩支持，生成器直接报错。

见证的目标值用原有理信息矩阵和 `Fraction` 高斯消元独立计算；这个检查不复用 Cauchy–Binet 系数表。**生成成功说明已构造并检查一个可行分配，不意味着已证明它最优。** tiny 的最优性可按第 3 节手工证明；一般随机实例仍需求解器优化。

## 7. 一键生成与规模参数

在项目根目录运行以下命令，就会尝试生成 10 个连续种子的 `.smt2` 到本类的 `instance/`：

```bash
cd /home/lbh/OMT
python3 new_benchmark/design/src/generate.py --profile integer --dimension 4 --candidates 8 --runs 8 --seed 100 --count 10 --encoding cauchy-binet
```

| 参数 | 含义 |
| --- | --- |
| `--dimension` | 特征向量维度，`integer` 默认 3，须至少为 1。 |
| `--candidates` | 候选数，`integer` 默认 6，须不小于 `dimension`。 |
| `--runs` | 总实验次数，`integer` 默认 6，须不小于 `dimension`。 |
| `--encoding` | `cauchy-binet` 或 `determinant`，默认前者；同一输入的编码变体不计新增应用。 |
| `--seed` | 起始随机种子，默认 1，批量生成时逐次加 1。 |
| `--count` | 尝试的连续种子数，默认 1；批量模式仅用于 `integer`。 |
| `--params` | 额外参数的 JSON 对象，例如 `coefficient_bound`、`upper_bounds` 或精确有理候选。 |
| `--out` | 输出根目录，默认 `new_benchmark`；例如 `--out /tmp/nia-design` 会写到 `/tmp/nia-design/design/instance/`。 |

相同应用数据和相同编码自动跳过；随机种子不同也可能产生相同应用，实际数量以输出为准。生成过程不启动求解器。自定义整数候选容量和系数范围可在原命令后加 `--params '{"coefficient_bound":5,"upper_bounds":[8,8,8,8,8,8,8,8]}'`，数组长度必须等于候选数。

以下命令说明精确有理输入的写法；它会因与现有 tiny-rational 应用重复而跳过，不额外新增同一应用：

```bash
python3 new_benchmark/design/src/generate.py --profile rational --encoding cauchy-binet --params '{"vectors":[["1/2",0],[0,"1/2"],["1/2","1/2"]],"runs":3}'
```

实现中的保护范围是：`m≥d`、`N≥d`，次数上限非负且总容量至少为 `N`；常数子式分析至多检查 20,000 个候选子集，非零项上限也为 20,000，生成的基础 SMT 文本最多 4,000,000 字节；`determinant` 编码还要求 `d≤6`。尽管排列编码本身不使用 Cauchy–Binet 展开，当前生成流程仍计算常数子式以检查满秩和记录元数据，因此两种编码的生成都受候选子集分析限制。

已生成的本轮规模如下，均使用 `cauchy-binet`：

| 本轮规模 | 数量 | 维度 / 候选数 / 总次数 |
| --- | ---: | --- |
| `small` | 40 | 2 / 6 / 8、3 / 8 / 9 |
| `medium` | 40 | 3 / 12 / 15、4 / 12 / 16 |
| `large` | 40 | 4 / 16 / 24、5 / 14 / 25 |

这些标签按生成参数划分，不是求解成功率或运行时间标签。完整配置见 [src/scaled_tiers.json](src/scaled_tiers.json)，本轮清单见 [src/scaled_instances.txt](src/scaled_instances.txt)。

## 8. 代码入口和生成流水线

1. [src/generate.py](src/generate.py) 把参数交给共用 [cli.py](../packing/src/nia_bench/cli.py) 的 `generate` 命令；`--count` 展开为连续种子。
2. [src/model.py](src/model.py) 中的 `make()` 根据 profile 组织输入；`_data()` / `_parsed()` 检查整数、容量和共同分母；`_initial_counts()` 构造满秩可行见证。
3. 同文件的 `build()` 声明 `n_i`、总次数、目标范围与正行列式约束，再按编码选择 `cauchy_binet_terms()` 或排列展开构造目标等式。`_constant_determinant()` 只在生成时计算常数子式。
4. `check()` 用 `_information()` 构造原有理信息矩阵，再由 `_fraction_determinant()` 独立检查见证；`assignment()` 负责需要时补齐 `cost` 和矩阵辅助变量。
5. 共用 [core.py](../packing/src/nia_bench/core.py) 的 `write_case()` / `audit_smt()` 检查类型与算术形式、生成应用指纹并处理去重，最后追加 `(maximize cost)` 和 `(check-sat)`，将完整优化文件发布到 `instance/<实例名>.smt2`。来源、数据、见证和生成记录留在 `src/` 内，不是正式运行时的依赖。

最终 `.smt2` 自带所有常量和约束，可直接交给支持整数优化的求解器。它没有量词、实数变量、除法或外部数据引用；整数变量之间的乘积提供 NIA 非线性。实例本身始终保留原来的最大化目标；实验脚本若为适配求解器而在内部转换目标方向，是运行层的处理，不属于这里的实例编码。
