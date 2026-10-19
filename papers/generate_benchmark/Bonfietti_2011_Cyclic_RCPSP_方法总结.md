# Bonfietti 等（CP 2011）循环 RCPSP：原文证据与 OMT(NIA) benchmark 路线

本文只讨论整数非线性算术上的优化。下文严格区分 **[论文]**、**[相关公开模型]** 与 **[建议]**；公开 MiniZinc 模型不是已验证的论文原实现，建议路线也不是作者未公开的生成器。

## 1. 文献、阅读版本与获取状态

- Alessio Bonfietti, Michele Lombardi, Luca Benini, Michela Milano, *A Constraint Based Approach to Cyclic RCPSP*, CP 2011, LNCS 6876, pp. 130–144。DOI：[10.1007/978-3-642-23786-7_12](https://doi.org/10.1007/978-3-642-23786-7_12)。[出版社章节页](https://link.springer.com/chapter/10.1007/978-3-642-23786-7_12)显示订阅访问；[Unibo 机构记录](https://cris.unibo.it/handle/11585/119540)显示附件未公开。
- **实际阅读全文版本**：[ResearchGate 上合著者 Michela Milano 上传的公开全文](https://www.researchgate.net/publication/221632790_A_Constraint_Based_Approach_to_Cyclic_RCPSP)。网页明确标记“Author content”及上传者；正文从作者稿第 1 页延续到第 15 页参考文献。作者稿页码 1–15 对应出版页 130–144。本文以下采用作者稿页码，便于核对。
- 已逐段阅读标题、摘要、正文第 1–6 节、四组实验与参考文献，保存全文文字抓取于 [paper-author-fulltext.txt](sources/cyclic-rcpsp/paper-author-fulltext.txt)。通过 `web.open` 读取公开可见文本，按网页行号合并 L66–L1389；正文行号无缺失。**这是网页抽取文本，不是原 PDF，图像没有归档；公式上下标、上下界横线及图表排版可能在抽取时损失。** 对不需精确重现的过滤算法不依据损坏的上下界符号重写。
- [作者公开 PDF 链接](https://www.researchgate.net/profile/Michela-Milano/publication/221632790_A_Constraint_Based_Approach_to_Cyclic_RCPSP/links/09e4150bc82b771346000000/A-Constraint-Based-Approach-to-Cyclic-RCPSP.pdf)在本次下载中返回 HTTP 403。**未取得原 PDF，因此没有创建 `Bonfietti_2011_Cyclic_RCPSP.pdf`，也没有把网页重新排版的 PDF 冒充原文。** 正文可读与 PDF 无法下载是两个不同状态。检索到的 CP 2011 doctoral-program PDF 是短摘要，不是该 15 页论文，不能替代全文。
- 核查日期：2026-10-11 UTC。来源、文件大小及 SHA-256 见 [SOURCE_MANIFEST.json](sources/cyclic-rcpsp/SOURCE_MANIFEST.json)。

## 2. 论文实际模型及非线性来源

### 2.1 图、无限重复与整数周期（第 2–3 页，式 1–2）

**[论文]** 输入为有向图 `G=(V,A)`。节点 `i` 为固定持续时间 `d_i` 的任务；边 `(i,j)` 具有结束到开始的最小时延 `theta_ij` 和重复距离 `delta_ij`。每次执行满足

\[
start(j,\omega)\ge start(i,\omega-\delta_{ij})+d_i+\theta_{ij}.
\]

周期模式为 `start(i,omega)=start(i,0)+omega*T`，其中 `T>0`。作者把初始绝对开始时刻分解为

\[
start(i,0)=s_i+k_iT,\qquad 0\le s_i\le T-d_i,
\]

从而先后约束为

\[
s_j+k_jT\ge s_i+d_i+\theta_{ij}+(k_i-\delta_{ij})T. \tag{P}
\]

`s_i` 是周期内相位，`k_i` 是整数迭代偏移。两者与 `T` 都是决策变量，所以 `k_i*T`、`k_j*T` 是两个未知整数的乘积。这正是保留周期为未知量时的 NIA 来源。若先固定 `T` 再求解，乘法变成常数乘变量，核心时间约束退化为线性整数算术；这与本任务所需 OMT(NIA) 不同。整数开始时刻和周期的假设亦在第 13 页第 5 节明确说明。

资源 `r` 的容量为 `cap_r`，任务需求为 `req_ir`。必须在所有时间满足容量限制；原论文只允许每个任务的整个执行区间位于单个周期内，因此可以对相位使用传统 cumulative。该限制是 **每个任务不跨周期边界**；不同逻辑迭代的不同任务仍可重叠。第 14 页结论明确把允许单个任务跨边界列作后续工作。

### 2.2 有限变量域、缓冲区、对称性（第 4–5 页）

令 `N=|V|`，作者定义

\[
H_{paper}=\sum_{i\in V}d_i+\sum_{(i,j)\in A}\theta_{ij}.
\]

第 4 页给定相位域 `[0,H_paper]`，迭代域 `[-N,+N]`，周期域 `(0,H_paper]`，并结合 `s_i+d_i<=T`。不能擅自把迭代域换成非负域：第 7 页把参考任务固定为 `s_src=k_src=0`，并说明允许负 `k` 维持此参考点选择下的完整性。以上为论文采用的界；本文没有证明这些界适用于任意扩展输入。

**[论文]** 每条生产者—消费者边的缓冲容量 `B_ij` 可用式 3 限制：

\[
k_j-k_i+[s_i+d_i\le s_j]\le B_{ij}-\delta_{ij},
\]

方括号为真取 1，否则取 0。论文指出 `B_ij>=delta_ij` 是初始 token 数能放入缓冲区的必要条件。缓冲区约束属于该论文实际模型；后述公开 `rcmsp.mzn` 没有这些参数与约束。

第 5 页式 5 使用前驱集合与向上取整给 `k_j` 加对称性界；第 6–7 页提出 modular precedence 的专用传播，分别缩小迭代、相位与周期域。它是 CP 求解策略，不是新的输入生成规则。第 7 页搜索交错选择相位与迭代，采用 schedule-or-postpone；其启发式选择无正 `delta` 入边、出边多的参考任务。复现 SMT 语义需要保留数学约束，不要求模仿 ILOG 传播器和搜索顺序。

### 2.3 目标及下界

**[论文]** 最小化周期 `T`，以提高长期吞吐率。论文把周期称为 makespan（第 3 页）；不要与后述 MiniZinc 源码中的第二目标 `makespan` 混用。

第 9–10 页的单资源、单位需求合成实验使用

\[
LB=\left\lceil\max\left(ib,\frac{\sum_i d_i}{cap}\right)\right\rceil.
\]

`ib` 为图的迭代下界，与循环平均/比例有关。**[推导]** 对模型 `(P)`，将某一有向环 `C` 的约束相加，若 `sum_C delta>0`，得到

\[
T\ge \left\lceil\frac{\sum_{(i,j)\in C}(d_i+\theta_{ij})}{\sum_{(i,j)\in C}\delta_{ij}}\right\rceil.
\]

这是直接从公式推出的必要条件，不是本文重新取得作者的 cycle-bound 实现。零距离环若总时延严格为正，则不可满足。**[建议]** 多资源可加入 `ceil(sum_i req_ir*d_i / cap_r)` 的资源能量下界；常数数据下此项在导出时计算即可。

## 3. 论文的 benchmark 是怎样来的：公开到什么程度

第 8 页说明合成图使用内部开发、具有现实结构和参数的 task-graph generator，**生成器设计明确排除在论文范围之外**。全文没有给出可逐随机数重现的拓扑生成算法、概率分布、完整参数文件、随机种子或生成器代码。因此能复现的是已说明的实验设定，不能声称精确重新生成作者的全部实例。

|实验组|实际来源与数量|已公开的参数与操作|原文位置|尚缺的生成信息|
|---|---|---|---|---|
|工业/VLIW|36 个 STMicroelectronics ST200 指令调度实例，引用 Ayala–Artigues 2010 RCMSP 工作|真实操作持续时间为 1；图从 10 节点/42 边到 214 节点/1063 边；原资源需求单位量，困难变体把资源需求替换为不超过容量的随机整数|第 8–10 页，§4.1，表 1|随机整数的精确分布、种子及改写程序；论文自身没有附 36 个输入文件|
|合成质量|1200 个多处理器调度合成实例|20–100 个活动；高并发循环图；1 个 cumulative 资源，容量 6，任务资源需求为 1；求解时间上限 300 秒，比较所得周期与理想下界|第 9–11 页，§4.2，表 2|各规模数量、持续时间分布、边密度、时延与距离分布、生成代码|
|modulo 对 unfolding|220 个合成实例|14–65 个活动；正文分组 14–24、25–44、45–65；比较 blocked 与 unfolding 因子 2、3、4、5、6、8、10|第 11–13 页，§4.3，表 3|拓扑和数据采样详情；表 3 的列标签显示 14–20、25–40、45–65，与正文分组边界不一致，不应自行把一方当作无歧义生成规范|
|buffer 效果|400 个 20 节点合成实例|通过调参使无资源约束的最优方案需要 3–6 个 buffer 单位；分别测试 B=1、2、3、6、9|第 12–13 页，§4.4，表 4|用于得到指定 buffer 需求的调参算法、拒绝条件、参数与种子|

原实现运行于 ILOG Solver 6.7、2.8 GHz Core2Duo、4 GB RAM（第 8 页）。论文的 300 秒质量结果与其最优性证明不是同一指标：不能把“发现很近的可行方案”标注为“最优已证明”。表 2 的 gap 参考必要下界，而非已知真实最优；本文不会把该数值当作已知 optimum。

论文引用的工业输入源：[Ayala–Artigues 的 HAL 原文链接](https://hal.archives-ouvertes.fr/docs/00/53/88/21/PDF/ArticuloChristianMaria.pdf)。这是原论文明确给出的出处；该旧链接本次 web 工具无法访问，不表示本次已取得该来源的完整原始实例库。

## 4. 已取得的相关公开 MiniZinc 模型与输入

**[相关公开模型]** [MiniZinc/minizinc-benchmarks/cyclic-rcpsp](https://github.com/MiniZinc/minizinc-benchmarks/tree/26bcd0a78433025f7b6896a6fa8caf128795760b/cyclic-rcpsp)，固定提交 `26bcd0a78433025f7b6896a6fa8caf128795760b`（仓库提交时间 `2019-07-24T09:57:04Z`）。模型头部版权为 University of Melbourne 与 NICTA（2011）；没有 Bonfietti 论文引用，也没有作者内部生成器代码，不能仅凭问题同名认定为作者的原始实例。

模型 [rcmsp.mzn](sources/cyclic-rcpsp/rcmsp.mzn)；20 个 `.dzn` 命名为 `easy_1..6`、`medium_1..3`、`hard_1..11`。另外保留原 `.metadata`。部分元数据只包含 `TAGS=instance2011`，不是已知最优值、随机种子或与论文实验组的映射。每个下载文件的固定 URL、字节数与 SHA-256 记录在 manifest；应以此清单核对本地完整性。

实际保存 **32 个仓库原始文件**（1 个模型、20 个实例、11 个 metadata），通过固定提交的 codeload `tar.gz` 流提取原字节。全部实例均有 6 个资源，latency 范围 0–3、distance 范围 0–4；容量为 `[4,1,1,2,2,2]` 或 `[10,10,10,10,10,10]`，需求不超过对应容量。N 包含两个人工任务，实际单位时长任务数为 N−2。维度、节点编号、非负需求及容量界已逐文件检查。

|原始文件|N|先后关系数|H（正 latency 总和）|
|---|---:|---:|---:|
|easy_1.dzn|46|210|119|
|easy_2.dzn|26|115|68|
|easy_3.dzn|26|137|79|
|easy_4.dzn|15|70|44|
|easy_5.dzn|31|143|85|
|easy_6.dzn|21|103|65|
|medium_1.dzn|15|70|44|
|medium_2.dzn|14|66|42|
|medium_3.dzn|34|154|96|
|hard_1.dzn|86|405|246|
|hard_2.dzn|101|462|307|
|hard_3.dzn|65|323|192|
|hard_4.dzn|65|323|192|
|hard_5.dzn|19|86|52|
|hard_6.dzn|23|102|70|
|hard_7.dzn|44|192|114|
|hard_8.dzn|32|138|75|
|hard_9.dzn|38|173|123|
|hard_10.dzn|59|266|176|
|hard_11.dzn|44|192|114|

作者全文文字抓取 SHA-256：`0b029a032a1a16b8290289c1109852c03ac84fca2c9559e41ff1b59d027d9474`；原始模型 SHA-256：`1c00d183a0656fe275f2c3d439e1dde5e2df4b3df213274d3bd382c50bdcbf40`。原 PDF 未保存，没有可报告的 PDF 哈希或 PDF 文件页数；15 页是公开作者稿正文显示的页码跨度。

### 4.1 输入与域的精确定义（源码行 35–85）

输入只有 `n_res`、`rcap[r]`、`n_tasks=N`、`rreq[i,r]`、`n_prec`、`prec[e,1..4]`。一条 `prec` 为 `[a,b,latency,distance]`；同一边可重复，不能在“逐字复现”时悄悄去重。持续时间在模型内硬编码：源 `1` 与汇 `N` 为 0，其余任务为 1；`.dzn` 不提供可变持续时间。

\[
H=\sum_{e:\,latency_e>0}latency_e,\quad 0\le s_i\le H,\quad 0\le k_i\le N,\quad T=s_N.
\]

这与论文的 `sum(d)+sum(theta)`、`k in [-N,N]` 不同。源码没有直接写 `T>0`；有真正任务（`N>=3`）时，由 `s_i+1<=T` 自动推出 `T>=1`。源码还要求 `s_1=k_1=0`、`s_i+d_i<=T (i<N)` 与 `k_N=max(k_i:i<N)`。源/汇的特殊角色与原数据的零资源需求应保留。

### 4.2 `u=s-k*T`、makespan 与主次目标（源码行 74–85）

源码直接定义

\[
u_i=s_i-k_iT\quad(i=1,\ldots,N-1),
\quad M=\max_i u_i-\min_i u_i+1,
\quad O=H T+M.
\]

**集合包含人工源 `1`，排除人工汇 `N`**；不能仅凭注释把源也删掉。`M` 被声明为整数域 `[0,H]`，而表达式自动使 `M>=1`；因此 `M<=H` 是实际可行性限制。仅把 `M` 当无界派生项会扩大问题。

最小化 `O` 确实实现先最小化 `T`、再最小化 `M`：由于 `1<=M<=H`，周期增加 1 的成本 H 超过 M 可改善的最大量 H−1。`O` 的域是 `[0,H*(1+H)]`。`u_i` 允许为负，并含真正变量乘积；不能把 `k_i*T` 改成常量、把 `T` 固定，或把所有乘積线性化后仍称其为原生 NIA。

**语义核查**：论文的初始绝对开始时刻用 `s_i+k_i*T`，这个公开模型的第二目标用 `s_i-k_i*T`。源码没有证明两者的跨度一致；本文仅称 M 为“源码定义的跨度”。逐字复现必须保留负号；若换成正号或真实完成时间 `max(start_i+d_i)-min(start_i)`，应单独标注为新模型，不可默默修正。

### 4.3 先后关系不能直接冒充论文式 (P)（源码行 90–97）

原公开模型对每条边定义 `b_e <-> (s_a+latency<=s_b)`，并加入

\[
k_a+ite(b_e,0,1)\le k_b+distance.
\]

它只区分“相位先后关系成立/不成立”，违规时要求至少移动一个周期；延迟超过一个周期的情况不能由这一布尔量准确计算需要跨几个周期。若记 `A=s_a+latency-s_b`、`D=k_b+distance-k_a`，论文型先后约束为 `A<=D*T`，而源码重述为 `D>=(A>0?1:0)`。当 `-T<A<=T` 时二者等价；**任意延迟与相位下并不等价**。

反例：`T=2,s_a=1,s_b=0,latency=3,k_a=0,k_b=1,distance=0`。源码布尔重述成立，但 `A=4>D*T=2`，论文型不等式失败。本次原始数据存在 latency=3，因此不能在未经完整解集审计时宣称两个模型严格相同。反例说明局部约束差异，不代表已证明某个原实例的最优值有差异。

### 4.4 cumulative 的正确有限展开（源码行 111–131）

对每个资源先取 `J_r={i:rreq_ir>0 and d_i>0}`。若需求总和不超过容量，源码跳过该资源约束；否则调用普通 cumulative。可依据 [MiniZinc 官方 cumulative 规范](https://docs.minizinc.dev/en/stable/lib-globals-scheduling.html#cumulative)把任务占用解释为半开区间 `[s_i,s_i+d_i)`。

**[推导]** 因资源需求非负且持续时间固定，资源总负载只会在任务开始时增加。故只需对每个 `j in J_r` 检查

\[
\sum_{i\in J_r}ite(s_i\le s_j\ \land\ s_j<s_i+d_i,\ rreq_{ir},\ 0)\le rcap_r. \tag{C}
\]

原模型单位持续时间和整数开始时刻使 `(C)` 可简化为 `sum_i ite(s_i=s_j,rreq_ir,0)<=rcap_r`。这不是 pairwise disjunctive 的一般替代：多任务各自两两需求不超容量，也可能三者同时超载。源码额外的两两不重叠约束只对存在资源使 `rreq_ir+rreq_jr>cap_r` 的任务对添加。

相位所有执行区间都在 `[0,T]` 内，所以检查一个周期即可代表无限重复的资源限制；不能把 `(C)` 用于允许单任务跨周期边界的新模型。若扩展持续时间但继续禁止跨边界，保留半开区间的 `(C)` 即可；若允许跨边界，需要真正循环 cumulative 或额外周期副本，并证明副本数量足够。

## 5. 可复现的 QF_NIA / SMT-LIB 转化规范

以下是导出规则与一个具体示例，不实现实例生成器。所有有限索引循环应在导出时展开，数组数据变成整数常数，不使用量词、UF、浮点数或机器整数。采用 [SMT-LIB 的 QF_NIA](https://smt-lib.org/logics-all.shtml#QF_NIA)；`minimize` 是 OMT 求解器接口，例如 [Z3 官方优化说明](https://microsoft.github.io/z3guide/docs/optimization/arithmeticaloptimization/)，不是纯 SAT 文件的标准命令。

### 5.1 路线 A：逐字保留公开 MiniZinc 模型

1. 读取 `.dzn` 的六个输入字段，计算常数 N、H、d，逐任务声明 `s_i,k_i : Int`，保留所有域、源/汇约束和原重述 `b_e`。
2. 用 `(= b_e (<= (+ s_a latency) s_b))` 与 `(<= (+ k_a (ite b_e 0 1)) (+ k_b distance))` 转换每条先后关系。不要把注释中的自然时间式替换进来。
3. 对每条需要的资源加 `(C)`；保留源码按常数判断的冗余 pairwise 约束。单位时长可精确简化为相等事件。
4. 定义每个 `u_i=(- s_i (* k_i T)) (i<N)`。`max` 可用辅助变量 `U` 加 `U>=u_i` 及 `or_i(U=u_i)`；`min` 可用 `L<=u_i` 及 `or_i(L=u_i)`。只加不等式而不加等值析取，会允许 U、L 脱离真实极值。
5. 加 `M=U-L+1`、`0<=M<=H`、`O=H*T+M` 及 O 原域，最后 `(minimize O)`。`k_N=max(k_i:i<N)` 同样需要极值等值析取。`s_N` 与 T 可用 `define-fun` 别名，勿重复引入不相等的周期。
6. 搜索 annotation 不属于 SMT 语义；将其留在元数据中，不给某个求解器强行模拟 `smallest/indomain_min`。

### 5.2 路线 B：保留论文的真正 modular-precedence NIA

使用第 2 节的整数域，按 `(P)` 生成

```smt2
; Concrete i, j, d_i, theta_ij, delta_ij replace placeholders.
(assert (>= (+ s_j (* k_j T))
            (+ s_i d_i theta_ij (* (- k_i delta_ij) T))))
```

各任务加 `s_i+d_i<=T`；资源按 `(C)`；buffer 式转换为 `(+ (- k_j k_i) (ite (<= (+ s_i d_i) s_j) 1 0)) <= B_ij-delta_ij`；目标只 `(minimize T)`。如保留论文式 5 的对称性约束，可把向上取整 q 定义为整数，约束 `q*T>=a` 且 `(q-1)*T<a`（T>0），这里 `a=s_i+d_i-s_j+theta_ij`；然后精确编码前驱最大值。不要用实数除法、截断到零的除法替代 ceil；也不要在未证明安全时把额外启发式界加入基准。

此路线的输入延迟为**结束到开始**的 theta，公开 MiniZinc 的 latency 是**开始到开始**的距离；当确实转换同一任务边时需要 `latency=d_i+theta_ij`，不可把字段直接复制。公开数据含人工边，未必都能转换成 theta>=0 的论文任务边，转换需单独记录。

### 5.3 一个能直接运行的微型路线 A 示例

示例是新构造数据，**不是论文或原库实例**：N=4，源/汇时长 0，任务 2、3 时长 1；一个容量 1 资源，两个真任务需求 1；六条先后边为 `[1,2,0,0]`、`[1,3,0,0]`、`[2,3,1,0]`、`[2,4,1,0]`、`[3,4,1,0]`、`[4,1,0,1]`，故 H=3。完整编码如下：

```smt2
(set-logic QF_NIA)
(set-option :produce-models true)
(declare-const s1 Int) (declare-const s2 Int)
(declare-const s3 Int) (declare-const s4 Int)
(declare-const k1 Int) (declare-const k2 Int)
(declare-const k3 Int) (declare-const k4 Int)
(define-fun T () Int s4)
(define-fun bound-s ((x Int)) Bool (and (<= 0 x) (<= x 3)))
(define-fun bound-k ((x Int)) Bool (and (<= 0 x) (<= x 4)))
(assert (and (bound-s s1) (bound-s s2) (bound-s s3) (bound-s s4)
             (bound-k k1) (bound-k k2) (bound-k k3) (bound-k k4)))
(assert (and (= s1 0) (= k1 0)
             (<= s1 T) (<= (+ s2 1) T) (<= (+ s3 1) T)))
(assert (and (>= k4 k1) (>= k4 k2) (>= k4 k3)
             (or (= k4 k1) (= k4 k2) (= k4 k3))))
(define-fun prec ((sa Int) (sb Int) (ka Int) (kb Int)
                  (lat Int) (dist Int)) Bool
  (<= (+ ka (ite (<= (+ sa lat) sb) 0 1)) (+ kb dist)))
(assert (and (prec s1 s2 k1 k2 0 0) (prec s1 s3 k1 k3 0 0)
             (prec s2 s3 k2 k3 1 0) (prec s2 s4 k2 k4 1 0)
             (prec s3 s4 k3 k4 1 0) (prec s4 s1 k4 k1 0 1)))
; Source pairwise constraint and exact unit-duration cumulative events.
(assert (or (<= (+ s2 1) s3) (<= (+ s3 1) s2)))
(assert (<= (+ 1 (ite (= s2 s3) 1 0)) 1))
(assert (<= (+ (ite (= s2 s3) 1 0) 1) 1))
(define-fun u1 () Int (- s1 (* k1 T)))
(define-fun u2 () Int (- s2 (* k2 T)))
(define-fun u3 () Int (- s3 (* k3 T)))
(declare-const U Int) (declare-const L Int)
(assert (and (>= U u1) (>= U u2) (>= U u3)
             (or (= U u1) (= U u2) (= U u3))))
(assert (and (<= L u1) (<= L u2) (<= L u3)
             (or (= L u1) (= L u2) (= L u3))))
(declare-const M Int) (declare-const O Int)
(assert (and (= M (+ (- U L) 1)) (<= 0 M) (<= M 3)
             (= O (+ (* 3 T) M)) (<= 0 O) (<= O 12)))
(minimize O)
(check-sat)
(get-value (T M O s1 s2 s3 s4 k1 k2 k3 k4))
```

该示例已经用本地 **Z3 4.16.0** 执行验证：返回 `T=2,M=2,O=8`，一组模型为 `s=(0,0,1,2),k=(0,0,0,0)`；去掉优化命令、加入 `(assert (< O 8))` 的独立纯 SMT 检查返回 **UNSAT**。一般验证最优时应保存 OMT 得到的可行 assignment，并独立排除严格更优目标；不能只看求解器输出 `sat` 就断言 optimum。

父任务另以本项目 `competitor/bin/z3`（实际版本输出 **Z3 5.1.0**）独立核验了同一示例，获得相同 `T=2,M=2,O=8` 与 `O<8` 为 UNSAT。输出证据见 [example_verification.json](example_verification.json)。以上只验证这一个说明性小实例，没有启动原库 20 个实例的完整求解实验。

## 6. 新实例参数生成路线：应如何做而不冒充原生成器

### 6.1 建议首先完成的基准集

**[建议]** 先转化原库 20 个 `.dzn` 为路线 A 的 QF_NIA OMT，以固定输入、固定模型、固定编码版本建立可复核起点。每个输入保留 original filename、SHA-256、N、资源数、边数、H、容量、需求/latency/distance 范围、模型来源 commit、编码版本和目标含义。规模标签来自原命名，不能当作某个 SMT 求解器上已经验证的难度等级。

然后新建路线 B 的论文语义基准；即使使用同一图骨架，也须独立 family 标识与 source lineage，因为先后约束、迭代域、buffer 和目标均可能不同。

### 6.2 生成参数与可行性保证

**[建议]** 一个明确的参数记录可包括 `seed,N,R,edge_density,delta_range,duration_range,theta_range,capacity[r],demand_density,buffer_policy,H,K_bound,encoding`。可采用以下先造见证再造约束的过程：

1. 选择论文已说明的规模层：N=20、40、60、80、100；或工业库观察到的规模。容量 6/单位需求为论文合成实验设定，其他容量、持续时间、稀疏度均标记为扩展选择。
2. 先选 `T0>=max_i d_i`，安排整数相位 `0<=s_i<=T0-d_i`，逐资源检查 `(C)`。容量不足时重新采样相位或调整资源需求；不要只检查每个单任务的需求<=容量。对输入资源需求用常数整数，避免引入 `req_i*duration_i` 两个决策变量的新问题。
3. 选参考任务 `s_src=k_src=0`，选择见证整数迭代偏移 `k_i`；若模拟论文域，使用 `[-N,N]`。最终 H 与 k 界必须能容纳该见证，否则即使生成的无限周期关系可行，有限域基准仍可能不可满足。
4. 对候选边取整数 `theta>=0`，根据见证计算 `A=s_i+d_i+theta-s_j`，令
   `delta >= max(0, ceil(A/T0) + k_i-k_j)`。
   这来自 `(P)`，保证见证满足该边。改变 delta 分布会改变图循环下界和吞吐压力；把所有循环边随意置为 delta=0 会产生不可能的正延迟循环。
5. 若需 buffer 变体，按见证选择
   `B_ij >= max(delta_ij, delta_ij+k_j-k_i+[s_i+d_i<=s_j])`，再用多个 B 上限比较。要声称“无资源最优需要 buffer=3..6”，必须额外求解并证明该最优与 buffer 性质；仅见证满足这一范围还不够。
6. 固定随机数算法与完整参数，保存见证与必要下界。见证 `T0` 是可行上界，不是已知最优值。资源瓶颈、拓扑并发、cycle bound 与 buffer 紧度应分别变化，避免一个参数同时改变多个结构而无法解释难度。

若只扩展公开路线 A，应按其**字面**重述选边，并检查 `M<=H`。从论文式 `(P)` 得到的见证不一定满足路线 A 的额外界，反之亦然。持续时间变为非单位、负 latency、大 latency 或跨边界都要另设 family，不能继续引用原文件称其是同一 benchmark。

### 6.3 适合 OMT(NIA) 的实验设计

- 同一数据分别保留变量 `T` 的 QF_NIA 编码与固定 T 的 QF_LIA 检查，后者只作为诊断对照；主 benchmark 仍优化未知周期。
- 记录乘法项数（路线 A 为每个 `u_i` 的 `k_i*T`；路线 B 为 modular-precedence 产品）、布尔/整数变量数、事件展开大小、最优值已证明/仅有上下界状态。
- 难度划分来自统一机器、统一预算、至少两个独立求解器的结果，不直接复用原文件 `easy/hard` 标签。记录 `unknown` 和 timeout，不把它们当 UNSAT。
- 可用两种资源展开做编码对照：固定时间 `t=0..H-1` 的有限负载约束，或任务开始事件 `(C)`；两者均无量词。对相同解集应作小实例穷举交叉检查，特别核查半开区间端点、人工源/汇、负 u、极值等值析取与 M 上界。
- 输出 optimum 时需独立验证：可行模型回代原整数约束，随后检查“目标严格小于已报告值”为 UNSAT。多个周期下 makespan 次目标的最优性需对应同一个定义，不能用纸面真实起止跨度替代源码 M。

## 7. 可以与不能从本次材料推断什么

可以直接复用公开库模型和数据作为一个相关 OMT(NIA) 起点；可以依据实际作者全文复建论文有限域 modular-precedence 与 buffer 数学模型；可以按已披露的规模、容量与需求设定制作明确标为扩展的合成实例。

不能精确复现未公开的内部 task-graph generator，不能认定公开 20 个 MiniZinc 文件等于论文全部工业/合成实例，不能认定模型重述对所有 latency 等价于论文式 `(P)`，不能把 `s-k*T` 的 M 默称为论文实际绝对时间 makespan，也不能把未取得的 PDF 标记为已下载。

