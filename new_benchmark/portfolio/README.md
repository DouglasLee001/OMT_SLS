# 整数持仓风险

选择各资产的非负整数持仓，满足预算和收益等约束，最小化风险。
风险是持仓的二次多项式，含真实的整数交叉乘积。
输出目标 `cost = D × 风险`，其中正整数 `D` 用于清除系数分母。

已有首批新增实例 7 个，本轮新增 120 个；含 4 个来源重放，当前共 131 个优化模型。`src/` 放生成源码。
默认输出 `/home/lbh/OMT/new_benchmark/portfolio/instance/`，只平铺可直接用于优化实验的 `.smt2`，无子目录。

| 本轮规模 | 数量 | 资产数 / 因子数 |
| --- | ---: | --- |
| `small` | 40 | 6 / 3、8 / 4 |
| `medium` | 40 | 12 / 6、16 / 8 |
| `large` | 40 | 20 / 10、24 / 12 |

规模标签只表示参数大小，不代表固定求解难度或保证求解成功。完整配置见 [src/scaled_tiers.json](src/scaled_tiers.json)，本轮清单见 [src/scaled_instances.txt](src/scaled_instances.txt)。

```bash
cd /home/lbh/OMT
python3 new_benchmark/portfolio/src/generate.py --profile synthetic-gram --assets 8 --risk-factors 4 --seed 100 --count 10
```

| 参数 | 含义 |
| --- | --- |
| `--assets` | 资产数。 |
| `--risk-factors` | 生成风险矩阵使用的因子数。 |
| `--seed` | 起始随机种子，批量生成时逐次加 1。 |
| `--count` | 尝试的连续种子数，默认 1。 |

相同实例自动跳过，实际数量以输出为准；生成过程不启动求解。
可选 `--out /tmp/nia-portfolio`，输出到该目录的 `portfolio/instance/`。


## 实验筛选子集

[instance/](instance/) 保留本类全部 131 个优化模型；[bounded/](bounded/) 有 128 个，[unsolved_bounded_sol/](unsolved_bounded_sol/) 有 96 个，均为平铺的 `.smt2` 副本。

`bounded` 按任一求解器 `abs(best_value)>10000000000` 剔除；`unsolved_bounded_sol` 进一步要求双方都未确认最优、至少一方有候选值。数值条件不是无界性证明。本类 `cost` 可能经过整数缩放，因此目标数值较大也可能来自缩放。筛选来自当前 [new.csv](../../result/new/new.csv) 的 1200 秒完整实验，详见 [统一筛选说明](../README.md#实验筛选子集)。两个新目录为静态副本；生成器仍只输出到 `instance/`，不会自动更新实验筛选子集。

## 1. 应用含义与数据来源

这里的决策是“每种资产买多少个最小交易单位”，因此持仓是非负整数，而不是总和为 1 的连续投资比例。价格、收益系数和风险系数都是给定的数据；求解器选择持仓，使总花费位于预算区间、总收益达到要求，并使组合风险最小。

当前有三种 `profile`，使用同一套整数持仓、线性约束和二次风险编码：

| `profile` | 数据从哪里来 | 是否是新应用数据 |
| --- | --- | --- |
| `minlplib` | MINLPLib 的 4 个 `smallinvDAX...` 实例，保留原始价格、线性约束和风险多项式。GAMS `.gms` 是模型依据，AMPL `.mod` 用来交叉核对变量域，公开 `.sol` 提供可行持仓。 | 否，是来源重放。 |
| `synthetic-gram` | 本项目用种子生成价格、收益、风险因子和可行持仓，再构造预算及收益门槛。新增的大批量实例属于这一类。 | 是。 |
| `tiny` | 固定的两资产有理数小例子，用于人工核对和完整枚举回归。 | 是固定测试例，不是随机 Gram 数据。 |

4 个来源模型为 `smallinvDAXr1b010-011`、`smallinvDAXr1b150-165`、`smallinvDAXr3b100-110`、`smallinvDAXr5b200-220`。原文、公开持仓、下载记录和固定 SHA256 在 [src/data/raw/portfolio/](src/data/raw/portfolio/) 中；来源是 MINLPLib 的 Small Investor Mean-Variance Portfolio Optimization，原始出处标注为 POLIP。具体来源 URL 与哈希也保存在每个实例的 `src/case_data/<实例名>/metadata.json` 的 `source_records` 中。

因此，“portfolio 类”表示应用类别，不表示该目录的所有数据都是从原论文直接下载的。判断单例来源可查看 `metadata.json` 中的 `profile`、`replay`、`origin` 和 `parameters`。

## 2. 数学符号如何对应实际文件

设资产数为 \(n\)，持仓列向量为 \(x\)。`data.json` 的持仓数组从 0 开始排列，SMT 符号从 1 开始编号，顺序以 `variables` 数组为准。

| 数学记号 | 实际数据或 SMT 符号 | 含义 |
| --- | --- | --- |
| \(n\) | CLI `--assets`；`variables` 的长度 | 资产数量。 |
| \(q\) | CLI `--risk-factors`；`risk_factor_matrix` 的行数 | 合成数据的风险因子数量，不是求解变量。 |
| \(x_i\) | 合成与 tiny 使用 `x1`、`x2`、…；来源重放保留 `i1`、`i2`、… | 资产持仓，SMT 类型为 `Int`。 |
| \(p_i\) | `prices[i-1]` | 每个最小交易单位的价格，是常量。 |
| \(r_i\) | 合成与 tiny 的 `return_coefficients[i-1]`；重放保存在原线性方程中 | 每单位持仓的收益系数，是常量，可以为负。 |
| \(B_{\min}, B_{\max}\) | `budget_min`、`budget_max` | 总花费的下限和上限。 |
| \(R_{\min}\) | 合成与 tiny 的 `return_min`；重放的原收益方程常数 | 最低收益要求。 |
| \(A,\lambda,\Sigma\) | 合成数据的 `risk_factor_matrix`、`lambda`、`covariance` | 构造风险系数的常量；只有合成 profile 保存这些字段。 |
| \(Q(x)\) | `risk_terms` | 原始风险多项式，系数是精确有理数。 |
| \(D\) | `risk_scale`；元数据 `objective.scale` | 风险多项式系数分母的最小公倍数，正整数。 |
| \(\mathrm{cost}\) | SMT 中的 `cost : Int` | 缩放后的风险，满足 `cost = D × Q(x)`，是唯一优化目标。 |

实际 SMT 中没有为价格、收益、矩阵元素分别声明变量，它们已经代入为整数常量。每个实例有 \(n\) 个持仓变量和 1 个辅助目标变量 `cost`。

## 3. 先编码可行持仓，再定义风险目标

### 3.1 持仓域、预算与收益

合成数据和 tiny 的应用约束是：

\[
x_i\in\mathbb Z_{\ge0},\qquad
B_{\min}\le\sum_{i=1}^{n}p_i x_i\le B_{\max},\qquad
\sum_{i=1}^{n}r_i x_i\ge R_{\min}.
\]

没有额外的“总持仓等于某个常数”约束，也没有收益上限。来源重放则逐条保留解析得到的原始线性等式或不等式，不统一替换成合成数据的门槛。

由于价格均为正、持仓非负，预算上限已经蕴含：

\[
0\le x_i\le\left\lfloor B_{\max}/p_i\right\rfloor.
\]

`_budget_and_bounds()` 在识别到相同正价格系数的预算上下界时，把这个冗余上界显式写入 SMT；如果来源还有更紧的上界，取二者较小值。这里的有限域来自应用预算，而不是人为把无界问题截成某个固定区间。对于一般被解析的来源数据，只有满足识别前提才推导上界。

`build()` 将线性式移到左边并与 0 比较，例如预算下界写为 \(\sum p_i x_i-B_{\min}\ge0\)。每条式子分别乘以其系数分母的正最小公倍数，因此不等号方向和可行集合不变。预算、收益的清分母倍数可不同，记录在 `metadata.json` 的 `constraint_scaling` 中。

### 3.2 合成风险矩阵与二次展开

`synthetic-gram` 先生成整数矩阵 \(A\in\mathbb Z^{q\times n}\) 和正整数 \(\lambda\)，再计算：

\[
\Sigma=A^\mathsf{T}A+\lambda I,
\qquad Q(x)=x^\mathsf{T}\Sigma x
=\sum_{k=1}^{q}\left(\sum_{i=1}^{n}A_{ki}x_i\right)^2
+\lambda\sum_{i=1}^{n}x_i^2.
\]

由于 \(\lambda>0\)，任意非零向量的风险都为正。这保证生成的风险数据符合正定二次风险的结构；求解器仍须在整数持仓和预算、收益约束下优化。

输出时不保留矩阵乘法，而是展开成：

\[
Q(x)=\sum_i\Sigma_{ii}x_i^2+
\sum_{i<j}2\Sigma_{ij}x_ix_j.
\]

**非对角元需要乘 2**：\(x^\mathsf{T}\Sigma x\) 中的 \((i,j)\) 与 \((j,i)\) 合并成一项。生成器只输出一次 `xi xj`；零系数项省略。对角项输出 `(* c xi xi)`，交叉项输出 `(* c xi xj)`，保留真正的多值整数变量乘积。

例如已有的 [gram-m4-q2-s1--946a284443f2.smt2](instance/gram-m4-q2-s1--946a284443f2.smt2) 使用：

\[
A=\begin{pmatrix}-3&4&-4&-1\\-4&2&2&2\end{pmatrix},\quad\lambda=4,
\quad\Sigma_{11}=29,\quad\Sigma_{12}=-20.
\]

所以风险中有 \(29x_1^2-40x_1x_2\)。对应的局部 SMT 表达式为 `(* 29 x1 x1)` 和 `(* (- 40) x1 x2)`；交叉项为负与整个矩阵正定并不矛盾。这个实例的价格为 `[8,8,17,2]`，预算为 `[96,118]`，收益系数为 `[28,28,39,-25]`，最低收益为 `281`。

来源重放和 tiny 已经提供风险**多项式系数**，解析后直接保留；不会再把已有的交叉项系数乘 2，也不会用随机 Gram 矩阵替换来源数据。

### 3.3 有理数清分母与 `cost` 的单位

输入十进制字符串和分数使用 Python `Fraction` 精确解析，不先转成二进制浮点。对合并同类项后的风险系数 \(c_\alpha=a_\alpha/b_\alpha\)，计算：

\[
D=\operatorname{lcm}_{\alpha}(b_\alpha)>0,\qquad
\mathrm{cost}=DQ(x)=\sum_\alpha(Dc_\alpha)x^\alpha.
\]

因此 SMT 中全是整数系数，不需要 `Real`、小数或除法。\(D\) 是该实例的常量，且为正，所以：

\[
\arg\min_x Q(x)=\arg\min_x\mathrm{cost},\qquad
\text{原风险}=\frac{\mathrm{cost}}{D}.
\]

`synthetic-gram` 的系数原本为整数，通常这里就是 `D=1`；tiny 为 `D=6`。4 个固定 MINLPLib 重放的 `D=2500000000`，例如公开可行持仓在 `smallinvDAXr1b150-165` 上的 `cost=220262336900`，对应原风险精确值 `88.10493476`。这个公开可行点本身不是最优性证明。

直接运行实例或查看实验 CSV 时，求解器优化并报告的是文件中的 **`cost`**。同一实例上两求解器可直接比较；跨实例比较原风险时应先用各自的 \(D\) 恢复单位。

## 4. 用现有 tiny 文件逐步看 SMT 怎么写

完整文件是 [portfolio-tiny--c05060af2764.smt2](instance/portfolio-tiny--c05060af2764.smt2)，其数据在 [src/case_data/portfolio-tiny--c05060af2764/data.json](src/case_data/portfolio-tiny--c05060af2764/data.json)。下面是从该文件拆出的局部片段，每段均不是独立完整脚本。

输入为：

\[
p=(2,3),\ B_{\min}=5,\ B_{\max}=6,\ r=(1,1),\ R_{\min}=2,
\qquad Q(x)=\tfrac12x_1^2+\tfrac13x_1x_2+\tfrac23x_2^2.
\]

**第一步：声明整数变量，并写非负域和预算推导上界。** 这里 \(\lfloor6/2\rfloor=3\)、\(\lfloor6/3\rfloor=2\)。

```smt2
(set-logic QF_NIA)
(declare-fun x1 () Int)
(assert (>= x1 0))
(assert (<= x1 3))
(declare-fun x2 () Int)
(assert (>= x2 0))
(assert (<= x2 2))
```

**第二步：写预算上下界和收益下界。** 三行分别表示 \(2x_1+3x_2\ge5\)、\(2x_1+3x_2\le6\)、\(x_1+x_2\ge2\)。

```smt2
(assert (>= (+ (- 5) (* 2 x1) (* 3 x2)) 0))
(assert (<= (+ (- 6) (* 2 x1) (* 3 x2)) 0))
(assert (>= (+ (- 2) (* 1 x1) (* 1 x2)) 0))
```

**第三步：将风险乘以 \(D=\mathrm{lcm}(2,3)=6\)，定义唯一目标。** `cost` 与风险表达式通过等式绑定，不是自由取值的额外决策。

```smt2
(declare-fun cost () Int)
(assert (= cost (+ (* 3 x1 x1) (* 2 x1 x2) (* 4 x2 x2))))
(minimize cost)
(check-sat)
```

SMT-LIB 中负整数写成 `(- 5)`，而不是把 `-5` 当作数字字面量。`(* 3 x1 x1)` 是 \(3x_1^2\)，`(* 2 x1 x2)` 是 \(2x_1x_2\)。所有声明都是整数、没有量词，并且存在变量与变量相乘，所以约束属于 `QF_NIA`；最后用优化命令 `minimize` 将它扩展为 OMT(NIA) 实例。

这个小例子的所有可行持仓只有 `(0,2)`、`(1,1)`、`(3,0)`，对应 `cost` 为 `16`、`9`、`27`，所以最优为 `(1,1)`、`cost=9`，原风险为 `9/6=3/2`。[src/test_model.py](src/test_model.py) 的 `test_tiny_complete_enumeration()` 对完整整数域做了枚举。这个结论仅适用于 tiny，不表示其它实例的生成过程预先知道最优值。

## 5. MINLPLib 原目标中的连续变量如何处理

来源 GAMS 模型有整数持仓 `i1,...` 和连续目标变量 `objvar`，风险约束形式为：

\[
Q(i)-\mathrm{objvar}\le0,\qquad\min\mathrm{objvar}.
\]

在 `objvar` 无额外上下界、仅出现在这一条风险约束且被最小化时，对任意固定持仓，最好的 `objvar` 就是 \(Q(i)\)。因此可以消去连续变量，直接最小化 \(Q(i)\)，再使用正整数 \(D\) 定义整数 `cost`。这保持持仓可行集合、最优持仓及恢复单位后的最优风险值。

`parse_gams()` 会检查这些前提，包括 `objvar` 只能出现在一个约束中、该约束是 `Q - objvar <= 0`、目标方向为最小化、没有额外目标变量域限制。它只支持这里固定模型使用的语法；遇到不支持的表达式或主动 GAMS 指令会报错，不会执行下载的 GAMS 程序。外部参数注入不被接受；原文件中条件性的用户 include 在未提供参数时不启用。

原始持仓名字和线性方程名称保留在 `data.json`，风险方程不再单独输出为不等式，而由 `cost = D × Q(i)` 取代。AMPL 文件用于核对持仓确为非负整数及原目标变量无额外限制；`.sol` 中打印的浮点 `objvar` 不当作精确风险使用，风险从公开整数持仓重新精确计算。

## 6. 新随机实例如何保证至少有可行解

`synthetic-gram` 不是生成一组随机约束后碰运气判断是否可行，而是先构造一组持仓 \(\bar x\)，再围绕它设置约束。默认参数如下；可通过 `--params` 传入精确 JSON 参数修改未单独列成 CLI 开关的项目。

| 生成参数 | 默认值及实际用途 |
| --- | --- |
| `assets` | 默认 8，至少 2；资产数。 |
| `risk_factors` | 未指定时 `max(2, assets // 2)`，显式指定时至少 1。 |
| `coefficient_max` | 默认 5；\(A_{ki}\) 从 `[-5,5]` 抽取，\(\lambda\) 从 `[1,5]` 抽取。 |
| `price_min`、`price_max` | 默认 2、25，价格为区间内整数；第二种资产价格被设为与第一种相同。 |
| `witness_max` | 默认 5，至少 2；初始持仓从 `[0,5]` 抽取，再将第一项至少设为 2、第二项至少设为 1；资产数大于 2 时最后一项设为 0。这不是所有可行持仓的上界。 |
| `budget_slack` | 默认精确 `"0.10"`，范围 \([0,1)\)；围绕已构造持仓的花费设置预算松弛。 |
| `return_tightness` | 默认精确 `"0.90"`，范围 \((0,1]\)；最低收益取已构造持仓收益的这一比例后向下取整。 |

收益系数先从 `[1, 2*price_max]` 抽取，再把最后一项改为 `[-price_max,-1]` 的负收益。资产数大于 2 时第一项收益至少与第二项一样大；恰好两资产时，第一项收益另行增大以保证构造可行性，因此修正后的收益不一定仍在初始抽样区间内。

设 \(S=\sum_i p_i\bar x_i\)、\(P=\sum_i r_i\bar x_i>0\)，先取：

\[
B_{\min}=\lfloor(1-\text{slack})S\rfloor,\qquad
B_{\max}^{(0)}=\lceil(1+\text{slack})S\rceil,\qquad
R_{\min}=\lfloor\text{tightness}\cdot P\rfloor.
\]

随后只可能放宽预算上限：令 \(k=\max(1,\lceil B_{\min}/p_n\rceil)\)，使用 \(B_{\max}=\max(B_{\max}^{(0)},kp_n)\)。这样“只持有负收益的最后一种资产 \(k\) 个单位”的方案满足预算，却严格违反收益门槛，证明收益约束不是预算约束的重复。

第一、第二资产价格相同，且第一资产收益不小于第二资产，所以把 \(\bar x\) 的第一项加 1、第二项减 1，可以得到另一组相同花费、收益不减的可行持仓。原持仓和替代持仓都经过独立应用检查。若随机风险矩阵完全没有非零交叉项，最多重新抽取 32 次，避免输出退化成纯对角风险的实例。

这些持仓只是构造与检查用的 **witness（可行性见证）**：它们保存在 `src/case_data/<实例名>/witness.json` 等辅助数据中，**不会作为持仓等式、目标上界或已知最优值写进公开 SMT 文件**。求解器必须自行寻找持仓；生成器不宣称这些 witness 最优。

`check()` 使用 `Fraction` 重算原始约束和风险，不依赖求解器模型；对 Gram 数据还独立验证 \(\Sigma=A^\mathsf{T}A+\lambda I\) 及“展开多项式风险 = 因子平方和风险”。检查通过表示确有可行解和目标计算一致，不是最优性证明。

## 7. 从参数到 `.smt2` 的流水线与复现

1. [src/generate.py](src/generate.py) 将参数转交共享 CLI；`--count k` 对随机 profile 展开成从 `seed` 到 `seed+k-1` 的种子。
2. [src/model.py](src/model.py) 的 `make()` 选择 profile；`_synthetic()` 生成新数据，`parse_gams()` 解析来源模型，`_case()` 保存参数、来源、缩放和见证信息。
3. 同文件的 `build()` 声明整数持仓、写线性约束、定义整数 `cost`；`polynomial_smt()` 完成同类项之后的精确清分母，`_budget_and_bounds()` 推导预算上界。
4. [../packing/src/nia_bench/core.py](../packing/src/nia_bench/core.py) 的 `write_case()` 调用独立 `check()` 和 `audit_smt()`，检查整数类型与允许的表达式，再添加唯一的 `(minimize cost)` 和 `(check-sat)`。
5. [../packing/src/nia_bench/cli.py](../packing/src/nia_bench/cli.py) 的 `generate_specs()` 按规范化应用数据识别重复；输出文件为 `instance/<label>--<12位哈希>.smt2`。完整数据、参数和源码哈希保存在 `src/case_data/<相同实例名>/`，实验只需要公开 SMT 文件。

例如当前 `gram-m4-q2-s1--946a284443f2.smt2` 的 `m4` 是 4 资产，`q2` 是 2 风险因子，`s1` 是种子 1；后缀哈希由精确数据和生成的基础约束内容计算。在当前生成源码、默认参数和数据来源版本一致时，相同种子与参数可复现同一模型；不仅要记录种子，也要保留资产数、因子数及 `--params` 中的其它配置。

下面保留常用的一键批量命令，生成 10 个种子对应的优化文件到 `portfolio/instance/`，已经存在的相同应用自动跳过：

```bash
cd /home/lbh/OMT
python3 new_benchmark/portfolio/src/generate.py --profile synthetic-gram --assets 8 --risk-factors 4 --seed 100 --count 10
```

需要改变预算松弛或系数范围时，可以把额外参数附在同一条命令后。例如以下命令仅作为使用示例，本说明编写时未执行生成：

```bash
python3 new_benchmark/portfolio/src/generate.py --profile synthetic-gram --assets 8 --risk-factors 4 --seed 200 --count 10 --params '{"budget_slack":"0.05","return_tightness":"0.95","coefficient_max":7}'
```

小规模、来源重放和新增 120 例使用上述同一条编码流水线；规模层级的差别是输入参数，不是换成另一种理论或目标。
