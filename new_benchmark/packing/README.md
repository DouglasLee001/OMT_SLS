# 最小面积排样

把固定方向的矩形工件放入整数宽高的容器，工件之间不得重叠。
允许边界接触，目标最小化容器面积 `cost = W × H`。
整数宽高的乘积是本应用的非线性。

已有首批新增实例 8 个，本轮新增 120 个；含 8 个来源重放，当前共 136 个优化模型。`src/` 放生成源码。
默认输出 `/home/lbh/OMT/new_benchmark/packing/instance/`，只平铺可直接用于优化实验的 `.smt2`，无子目录。

| 本轮规模 | 数量 | 工件数 / 最大边长 |
| --- | ---: | --- |
| `small` | 40 | 6 / 6、8 / 8 |
| `medium` | 40 | 10 / 8、12 / 10 |
| `large` | 40 | 16 / 12、20 / 14 |

规模标签只表示参数大小，不代表固定求解难度或保证求解成功。完整配置见 [src/scaled_tiers.json](src/scaled_tiers.json)，本轮清单见 [src/scaled_instances.txt](src/scaled_instances.txt)。

```bash
cd /home/lbh/OMT
python3 new_benchmark/packing/src/generate.py --profile rectangles --items 12 --max-side 8 --seed 100 --count 10
```

| 参数 | 含义 |
| --- | --- |
| `--items` | 矩形工件数。 |
| `--max-side` | 随机生成工件的最大边长。 |
| `--seed` | 起始随机种子，批量生成时逐次加 1。 |
| `--count` | 尝试的连续种子数，默认 1。 |

相同实例自动跳过，实际数量以输出为准；生成过程不启动求解。
可选 `--out /tmp/nia-packing`，输出到该目录的 `packing/instance/`。

## 1. 应用问题、输入和来源

给定 `n` 个工件，第 `i` 个工件的宽、高分别为正整数 `w_i`、`h_i`。选择整数尺寸的矩形容器，并给每个工件选择整数坐标，使所有工件放得下、两两没有正面积重叠，最后最小化容器面积。坐标单位是离散网格单位，方向固定。

生成器支持两种输入：

| profile | 工件尺寸怎么来 | 与已有资料的关系 |
| --- | --- | --- |
| `squares` | 恰好放边长为 `1,2,...,n` 的正方形，各一个 | 对本地保存的 [MiniZinc `square_pack.mzn`](../../papers/generate_benchmark/sources/square-packing/square_pack.mzn) 进行整数 SMT 转写；`n=3..10` 记作来源重放 |
| `rectangles` | 随机生成 `--items` 对宽高；每条边独立取 `1..--max-side` 的整数，随机种子为 `--seed` | 沿用排样问题结构，生成新的固定方向矩形数据；当前新增的 120 例使用这一 profile |

随机数据在 Python 中生成后，尺寸就成为 SMT 文件中的**常数**。例如 `w_0=3,h_0=2` 表示一个固定的 `3×2` 工件，求解器只选择它的位置。编号从 `0` 开始，原 MiniZinc 中的第 `s` 个正方形对应这里的下标 `i=s-1`。

这里比较的是整数坐标、固定方向模型下的最优面积。允许边界接触，例如两个工件共用一条边。随机生成器可以产生相同尺寸的多个工件，这些工件各自仍有一组位置变量。

## 2. 数学模型与 SMT 变量

| 应用量 | SMT 符号 | 类型与作用 |
| --- | --- | --- |
| 容器宽、高 | `W`、`H` | `Int`，需要优化选择的容器尺寸 |
| 工件 `i` 左下角 | `x_i`、`y_i`，如 `x_0`、`y_0` | `Int`，非负网格坐标 |
| 容器面积 | `area` | `Int`，辅助变量，必须等于 `W*H` |
| 统一优化目标 | `cost` | `Int`，必须等于 `area` |
| 工件宽、高 | 无对应变量 | 作为整数常数写进约束 |

总共有 `2n+4` 个整数变量。`area` 与 `cost` 的两个名字是为了保留排样模型的面积含义，同时和其他应用统一使用目标符号 `cost`；它们并不是两个优化目标。

### 2.1 工件必须在容器内部

对每个工件，要求

\[
x_i\ge 0,\qquad y_i\ge 0,\qquad
x_i+w_i\le W,\qquad y_i+h_i\le H.
\]

例如 `3×2` 工件的右边、上边不能越过容器边界。下面是相应 SMT 片段，变量声明和其余工件约束省略：

```smt2
(assert (>= x_0 0))
(assert (>= y_0 0))
(assert (<= (+ x_0 3) W))
(assert (<= (+ y_0 2) H))
```

### 2.2 每对工件至少在一个方向上分开

对所有 `i<j`，编码以下四个条件的析取：

\[
(x_i+w_i\le x_j)\ \lor\
(x_j+w_j\le x_i)\ \lor\
(y_i+h_i\le y_j)\ \lor\
(y_j+h_j\le y_i).
\]

这四项分别表示：`i` 在 `j` 左边、`j` 在 `i` 左边、`i` 在 `j` 下边、`j` 在 `i` 下边。它们只需满足一项，也可以同时满足多项。使用 `<=` 正好允许贴边。

例如一个 `3×2` 工件和一个 `5×2` 工件之间的实际写法为：

```smt2
(assert
  (or (<= (+ x_0 3) x_1)
      (<= (+ x_1 5) x_0)
      (<= (+ y_0 2) y_1)
      (<= (+ y_1 2) y_0)))
```

生成器在 Python 中枚举全部 `n(n-1)/2` 对工件，为每一对写出一个 `or`。SMT 文件中没有量词；这里数学叙述中的“对所有工件”已经在生成阶段展开了。

### 2.3 面积与优化目标

目标关系和文件末尾固定为：

```smt2
(assert (= area (* W H)))
(assert (= cost area))
(minimize cost)
(check-sat)
```

因此求解器返回的目标值可以直接读作面积：`面积 = cost`，没有清分母或还原系数。`W`、`H` 都是未知整数，乘积 `W*H` 是本应用真正的非线性；位置和不重叠约束本身是线性整数关系的布尔组合。

## 3. 变量上下界是怎么推出来的

所有尺寸和坐标都写了有限整数域，上下界由 [src/model.py](src/model.py) 的 `bounds()` 计算。

### 3.1 矩形 profile：用一条已知可行的水平排样给上界

定义

\[
A_{\mathrm{LB}}=\sum_i w_i h_i,\qquad
W_{\min}=\max_i w_i,\qquad H_{\min}=\max_i h_i.
\]

面积至少为工件面积之和；容器的宽、高至少分别容得下最宽、最高的工件。

再把所有工件依次放在同一条水平带里：

\[
W_{\mathrm{strip}}=\sum_i w_i,\qquad
H_{\mathrm{strip}}=H_{\min},\qquad
A_{\mathrm{UB}}=W_{\mathrm{strip}}H_{\min}.
\]

这个布局一定可行，所以全局最优面积不超过 `A_UB`。生成器写入以下域：

\[
\begin{aligned}
W_{\min}&\le W\le \left\lfloor A_{\mathrm{UB}}/H_{\min}\right\rfloor,\\
H_{\min}&\le H\le \left\lfloor A_{\mathrm{UB}}/W_{\min}\right\rfloor,\\
0&\le x_i\le W_{\max},\qquad 0\le y_i\le H_{\max},\\
A_{\mathrm{LB}}&\le area,cost\le A_{\mathrm{UB}}.
\end{aligned}
\]

后续的放入约束还会要求 `x_i+w_i<=W`、`y_i+h_i<=H`，因此上述坐标域只是方便生成的统一外层范围。

这些域可能排除面积更大的可行布局，但**保留原问题的全局最优值**：水平带已经给出面积为 `A_UB` 的可行解；任何更优布局都满足面积上界；再结合 `H>=H_min`、`W>=W_min`，可以推出它也满足这里的宽高上界。该界限是建模的一部分，所有求解器读到相同的约束。

### 3.2 正方形 profile：保留原 MiniZinc 模型的域

令

\[
S=1+2+\cdots+n=\frac{n(n+1)}2,\qquad
A_{\mathrm{LB}}=\sum_{s=1}^n s^2.
\]

这里使用原模型的范围：

\[
n\le W,H\le S,\qquad
0\le x_i,y_i\le S,\qquad
A_{\mathrm{LB}}\le area,cost\le nS.
\]

把全部正方形排成宽 `S`、高 `n` 的一条水平带，就得到面积 `nS` 的可行上界。转写时保留这些域以及原模型的逐对不重叠约束；原 MiniZinc 的搜索顺序注解没有写入 SMT。源文件中的 `diffn` 和 `cumulative` 约束本身处于注释状态，转写采用的是源文件实际启用的四方向析取。

## 4. 对照一个实际 `.smt2` 文件

### 4.1 三个正方形：从参数到文件

对应文件为 [squares-n03--7edc83c7a58a.smt2](instance/squares-n03--7edc83c7a58a.smt2)。输入是 `n=3`，尺寸分别为 `(1,1)`、`(2,2)`、`(3,3)`，所以

\[
S=6,\quad A_{\mathrm{LB}}=1+4+9=14,\quad A_{\mathrm{UB}}=3\times6=18.
\]

文件中的声明和关键等式如下；这段摘录省略了坐标域、其余放入约束和另外两对不重叠约束，完整文件以上方链接为准：

```smt2
(set-logic QF_NIA)
(declare-fun W () Int)
(assert (>= W 3))
(assert (<= W 6))
(declare-fun H () Int)
(assert (>= H 3))
(assert (<= H 6))
; x_0,y_0,...,x_2,y_2 在完整文件中均声明为 Int
(declare-fun area () Int)
(assert (>= area 14))
(assert (<= area 18))
(declare-fun cost () Int)
(assert (>= cost 14))
(assert (<= cost 18))
(assert (= area (* W H)))
(assert (= cost area))
(assert (<= (+ x_1 2) W))
(assert (<= (+ y_1 2) H))
(assert (or (<= (+ x_1 2) x_2)
            (<= (+ x_2 3) x_1)
            (<= (+ y_1 2) y_2)
            (<= (+ y_2 3) y_1)))
(minimize cost)
(check-sat)
```

生成器保存的一组布局为：

| 工件 | 尺寸 | 左下角 `(x_i,y_i)` |
| --- | --- | --- |
| `0` | `1×1` | `(3,2)` |
| `1` | `2×2` | `(3,0)` |
| `2` | `3×3` | `(0,0)` |

令 `W=5,H=3`，则三个工件都在容器内，只有贴边，没有正面积交叠，`cost=15`。面积至少为 `14`；在整数宽高均不小于 `3` 的条件下，没有面积为 `14` 的容器，因此这个小例的最优面积就是 `15`。这是该微型例的直接几何论证，不能推广成其他随机实例的最优性结论。

### 4.2 六个随机矩形：参数改变的是常数数据

对应文件为 [rectangles-n6-max8-seed1--9a7974d92f85.smt2](instance/rectangles-n6-max8-seed1--9a7974d92f85.smt2)，数据为：

```text
dimensions = [(3,2), (5,2), (8,8), (8,7), (4,2), (8,1)]
```

这里总工件面积为 `152`，最宽、最高工件均为 `8`，各工件宽度之和为 `36`。因此

```text
8 <= W <= 36
8 <= H <= 36
152 <= area,cost <= 288
```

默认可行见证是 `W=36,H=8`，所有工件的 `y_i=0`，`x_i` 依次为 `0,3,8,16,24,28`。它说明模型至少有一个面积为 `288` 的可行布局，不表示 `288` 最优；求解器需要继续寻找更小容器并判断最优性。

## 5. 从生成命令到优化文件的完整流程

```text
src/generate.py 的命令行参数
  -> 共用 cli.main() 展开 profile、seed、count
  -> src/model.py: make() 固定每个工件的尺寸
  -> _case() 构造可行布局、来源记录和应用指纹
  -> bounds() + build() 创建整数变量与全部约束
  -> 共用 write_case() 审核 QF_NIA 并检查见证
  -> instance/<参数标签>--<内容哈希前12位>.smt2
```

`--count=10` 表示从起始种子开始尝试十个连续种子，不保证增加十个不同应用：尺寸多重集合相同的矩形输入会按应用指纹去重。生成器用排序后的 `(w_i,h_i)` 多重集合计算这一指纹，因此仅打乱工件编号不会算成一个新的应用实例。

见证检查器直接计算两个矩形的水平、竖直交叠长度：只有两个方向的交叠长度都大于 `0` 才是非法重叠。这条检查路径与 SMT 的四项 `or` 写法不同，用于核对几何语义。普通见证检查只证明给定布局可行及面积计算正确，不证明随机实例最优。

公共输出层为每个文件写 `(set-logic QF_NIA)`、整数声明、域和几何约束，最后追加单个 `(minimize cost)` 与 `(check-sat)`。文件已经包含所有求解所需的常数和约束，不依赖生成器内部 JSON 才能求解。内部的参数、来源和见证记录放在 `src/case_data/`，`instance/` 仍只有平铺的优化 `.smt2`。

| 想核对的步骤 | 对应源码 |
| --- | --- |
| 输入检查、随机尺寸、见证、域、约束 | [src/model.py](src/model.py) 中的 `_sizes()`、`make()`、`_case()`、`bounds()`、`build()` |
| 独立几何检查及赋值/解码 | [src/model.py](src/model.py) 中的 `check()`、`assignment()`、`decode()` |
| 逐种子批量调用、应用去重 | [src/nia_bench/cli.py](src/nia_bench/cli.py) 中的 `main()`、`generate_specs()` |
| 整数声明、SMT 审核、目标追加、内容哈希 | [src/nia_bench/core.py](src/nia_bench/core.py) 中的 `Model`、`audit_smt()`、`write_case()` |
