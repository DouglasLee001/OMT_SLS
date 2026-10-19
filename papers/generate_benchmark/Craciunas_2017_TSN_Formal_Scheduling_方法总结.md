# Craciunas–Serna Oliver–Steiner (2017)：TSN窗口调度方法与OMT(NIA)适用性审查

## 1. 文献与资料状态

- 作者：Silviu S. Craciunas，Ramon Serna Oliver，Wilfried Steiner。
- 题名：*Formal Scheduling Constraints for Time-Sensitive Networks*。
- 类型：2017年技术报告，arXiv:1712.02246v1；提交日期2017-12-06。这里收集的是该报告，不是作者2016年RTNS论文，也不是2018年基于数组理论的GCL论文。
- 原文：[arXiv元数据](https://arxiv.org/abs/1712.02246)、[arXiv PDF](https://arxiv.org/pdf/1712.02246)、[作者旧站PDF](https://www.cs.uni-salzburg.at/~scraciunas/pdf/techreports/craciunas_CoRR17.pdf)、[作者当前站PDF](https://scraciunas.github.io/pdf/techreports/craciunas_CoRR17.pdf)。[作者当前主页](https://scraciunas.github.io/)在“Technical reports”部分列出本报告。
- 本地全文：[Craciunas_2017_TSN_Formal_Scheduling.pdf](./Craciunas_2017_TSN_Formal_Scheduling.pdf)，12页，408829字节；SHA256：`0646ecaad25609c557ac80ee6ce671932a2c8e070f9bed56f0fc70a879f79676`。arXiv下载与当前作者站下载二进制校验值相同。
- 阅读覆盖：逐页阅读PDF 1–12页，包括正文、§4的多周期限制、§5的理论/目标描述与参考文献；下文页码是PDF页码，也等于原文印刷页码。原文只有两个编号公式：(1)在p.5，(2)在p.8；其他公式按页码与原文段落名称定位，不虚构式号。
- 收集日期：2026-10-11。此次仅收集和分析原文、独立工具官方资料；没有生成SMT实例，没有实现生成器，没有运行TSNKit。

**结论先行：建议从主OMT(NIA)数据源排除，保留为“可线性化的应用建模”比较资料。** §5 p.10称作者编码使用NIA，但固定网络和流参数后，全文乘法都能归入“常数×变量”“0/1选择器×时间/窗口宽度”或“两个0/1选择器的合取”。消去这些选择器乘法后，没有保留两个多值整数决策变量相乘的约束。原式的语法可属于QF_NIA；应用问题本身可等价编码为QF_LIA。这一判断来自下文逐类审查，不把排版中的乘号直接当作实质NIA证据。

另外，p.8印刷式(2)在已声明的变量域下恒真，因而单独无法表达正文叙述的流隔离要求。§4明确没有给出多周期jitter公式。两处缺口都必须保留，不能把修正模型或TSNKit结果称为2017原作者benchmark。

## 2. 从应用输入到决策变量

§2 pp.3–4用图`G=(V,E)`表示网络。一个无向物理连接产生两个有向链路，两个方向分别调度。端系统发送/接收，交换机转发。正文限制为单播、每周期一帧，每条egress链路仅有一个scheduled-traffic队列，队列按FIFO发送；扩展到多播、多帧、多队列仅作说明，并未逐项给出完整编码。

一条流`i`的输入包含：已给定的talker、listener、按顺序排列的有向路径`route_i`，周期`T_i`、数据大小`B_i`（bytes）、最大端到端时延`D_i`、最大接收抖动`J_i`。**路径是输入常量，不是路由优化变量。** 本模型也没有带宽/链路速率设计、周期选择、报文大小选择等决策。

流`i`经过有向链路`e`时产生该链路上的帧`f_i^e`，其周期等于流周期；发送时长`L_i^e`根据流bytes大小和链路速率预计算，因此对求解器是常量。p.4的`.L`指传输时间，不应把bytes数量直接代入时间不等式。原文没有规定统一的时间单位、Ethernet头部/IFG开销计入方式或取整规则。

每条链路`e`输入最大窗口数`W_e`，来自设备GCL容量要求。原文未给出GCL硬件条目数到`W_e`的一般精确换算式。每个窗口`k=1..W_e`的未知量是：

| 记号（本文简写） | 原文对象 | 域与意义 |
| --- | --- | --- |
| `o[e,k]` | `w_k^e.open` | Integer，开门时刻 |
| `c[e,k]` | `w_k^e.close` | Integer，关门时刻 |
| `s[e,k]` | `w_k^e.size` | Integer，窗口持续时间 |
| `b[e,k,i]` | `w_k^e.ε(f_i^e)` | Integer取0或1，帧是否分配到窗口；可改为Bool |
| `χ[i,e,k,l]` | p.7的`χ_i,k,l` | 两相邻链路选择位的乘积，因此只能取0/1；可不声明而直接用Bool合取 |

`hp_e=lcm({T_i : i经过e})`在数据预处理时计算，是数值常量，不是符号`lcm`操作或变量周期相乘。同周期主模型有`T_i=T`，所有用到的链路超周期均为`T`。空链路没有帧时`lcm(∅)`未定义，复现应只编码被使用链路，或另外声明一致的超周期约定，不能隐含处理。

## 3. 同周期约束逐项整理

以下是对§3 pp.4–9的重记号整理，不改变原式语义。

### 3.1 合法窗口与先后顺序：p.5式(1)及“Ordered windows”

对每条链路、每个窗口：

```text
0 ≤ o[e,k] ≤ c[e,k] < hp_e
b[e,k,i] ∈ {0,1}
```

原文先讨论任意两个窗口用析取表达不重叠，随后采用按编号排序的方式去除排列对称：

```text
c[e,k] ≤ o[e,k+1]       k=1..W_e−1
```

因为帧到窗口的分配尚未固定，窗口重新编号不会损失解；有序形式只需要相邻窗口约束。pp.5–6进一步指出，保留`o[e,1]≥0`与`c[e,W_e]<hp_e`即可结合顺序传递得到所有时间边界。窗口允许`o=c`，所以空窗口可以是0宽度。

**边界细节：** 原文是严格`close<hp`，不是`close≤hp`。若以整数tick编码，就应保持`c≤hp−1`；把末尾关门事件允许到超周期边界会改变原始可行域。此严格条件也与p.10的实例周期区间非严格上界同时存在，最后一个周期仍受`close<hp`限制。

### 3.2 唯一分配与窗口大小：p.6

```text
sum(k=1..W_e, b[e,k,i]) = 1
s[e,k] = sum(i经过e, b[e,k,i] * L_i^e)
c[e,k] = o[e,k] + s[e,k]
```

每个帧在每条经过链路上恰好分配一个窗口，链路的所有分配帧一起确定该窗口长度。`L_i^e`已知，所以这里的乘法是常数系数线性项；在SMT-LIB中即使保留`(* b 1200)`，也仍属于LIA。

这是总帧发送时间恰好铺满窗口的模型，不是任意留出空闲余量的窗口模型。窗口内个别帧的精确开始时刻及FIFO排列不是独立变量；接收jitter限制利用整个窗口宽度界定帧可能位置。

### 3.3 流沿固定路径前进：p.7“Stream constraints”

相邻链路`e=[v_j,v_{j+1}]`、`e'=[v_{j+1},v_{j+2}]`以及其窗口`k,l`满足：

```text
χ = b[e,k,i] * b[e',l,i]
χ * (c[e,k] + δ) ≤ χ * o[e',l]
```

`δ`是输入的全局时钟同步精度，即两个节点本地时钟的最大差值。该公式在两次分配都选中时要求后继窗口开门不早于前驱窗口关门加同步误差；未选中组合退化为`0≤0`。原文并未在此额外引入独立的交换机processing或wire propagation参数，不应把TSNKit的`t_proc/t_prop`当成论文既有参数。

### 3.4 流隔离：pp.7–8、印刷式(2)

正文要求：两条流从不同ingress进入同一交换机并共享一个egress时，若不共用egress窗口，应在时间上隔离，避免FIFO状态因丢帧、变长帧、同时到达而改变。原文p.8式(2)按每个egress窗口`k`与前驱窗口`l,m`写成：

```text
(c[e,k] * b[e,k,i] ≤ o[pred_j,m] * b[pred_j,m,j]
 OR
 c[e,k] * b[e,k,j] ≤ o[pred_i,l] * b[pred_i,l,i])
OR (b[e,k,j] = b[e,k,i])
```

**我们的原式审查发现：该印刷公式在声明的非负时刻、0/1选择域下恒真。** 已用原PDF第8页渲染图片目视复核，见[第8页公式截图](./sources/Craciunas_2017_page8_isolation_audit.png)；以下结论针对印刷公式，不声称已经核验作者未公开的实现。

令`a=b[e,k,i]`、`b=b[e,k,j]`，两个ingress开门时刻均非负，其与0/1位的乘积也非负：

| 情况 | 必定成立的子式 |
| --- | --- |
| `a=b`，即`(0,0)`或`(1,1)` | 最后一个等式直接成立 |
| `(a,b)=(1,0)` | 第二个不等式左边为0，右边非负，因此成立 |
| `(a,b)=(0,1)` | 第一个不等式左边为0，右边非负，因此成立 |

因此对任意`k,l,m`都成立，无法排除两流在不同egress窗口时未时间隔离的情形。它不是因为NIA求解困难才失效，而是布尔结构的逻辑问题。若以后按正文意图修正，必须独立命名版本并给出证明/运行语义核验，不应静默修正后称“复现原式”。本次不实现修正编码。

### 3.5 端到端时延：p.8“Stream End-to-end latency constraints”

记流首链路`first_i`与末链路`last_i`的选中窗口时刻：

```text
O_i = sum(k, b[first_i,k,i] * o[first_i,k])
C_i = sum(l, b[last_i,l,i] * c[last_i,l])
C_i − O_i ≤ D_i − L_i^last − δ
```

因恰好一个选择位为1，两个和分别选取首窗口开门与末窗口关门时刻。这里保留论文右侧的`−L_last−δ`，不擅自调整为另一种常见frame-level时延定义。原文不同时优化数据大小和时间，`L_last`与`δ`均是常量。

### 3.6 接收jitter：pp.8–9“Stream jitter constraints”

```text
ψ_i = sum(l, b[last_i,l,i] * s[last_i,l])
ψ_i ≤ J_i + L_i^last
```

`ψ_i`是包含该流的末跳窗口宽度；`ψ_i−L_i^last`是论文用来约束接收时间变化的窗口余宽。单帧独占末跳窗口时余宽为0；同一窗口打包其他流会增大这一界。原文主要只约束listener处抖动，称若需要中间节点抖动可在相应链路重复施加。此处不是在样本轨迹上估计统计方差，也不是2016模型的严格零jitter周期公式。

## 4. §5的OMT目标与NIA逐项审查

§5 p.10把非线性整数算术作为其写法的背景理论，提及有限整数域可用bit-blasting等技术。它没有给出solver版本、完整SMT文件、数据生成器、随机种子、运行表格或实验实例清单。

目标只作高层描述：p.10提出最小化端到端时延；p.11提出最小化接收jitter，可以对各流求和，也可设为多个单独目标。论文没有指定所有目标如何加权、是否采用lexicographic/Pareto模式、如何将时延目标聚合、或“窗口数最小化”作为独立正式目标。因此以下可操作目标表达式是基于正文公式的复现建议，应标明是我们补充的目标实例化，而不是作者已发布的benchmark specification：

- 单个流时延表达式可取`C_i−O_i`，或包含常量`L_last+δ`的上界；单目标最优排序等价。若聚合为总和，需要显式记录选择了`sum_i`。
- 总jitter表达式可取`sum_i(ψ_i−L_i^last)`；因所有`L`固定，也等价于最小化`sum_i ψ_i`。各流独立目标则需另行注明优化模式。
- `W_e`在原式中是输入上界。改变`W_e`是生成多个实例族的参数选择，不等于原式已经支持可变窗口数的多值乘法。

### 4.1 全文所有乘法分类

| 位置 | 乘法类型 | 固定输入后的地位 | 精确消除方法 |
| --- | --- | --- | --- |
| p.6窗口大小 | `b * L` | `L`为常量，已是线性 | 保留常数系数；或`ite(b,L,0)` |
| p.7流序列 | `b * b'` | 两个因子均0/1 | `χ`改为`and(B,B')`；不用整数乘法 |
| p.7流序列 | `χ*(c+δ)`、`χ*o'` | 0/1×多值时间 | 改为`(B and B') => c+δ≤o'` |
| p.8式(2)流隔离 | `c*b`、`o*b'` | 0/1×多值时间 | 每项改为`ite(B,time,0)`；原式语义完整保留，但另有上述恒真缺口 |
| p.8端到端时延 | `b*c`、`b*o` | 0/1×多值时间 | 改为`ite(B,time,0)`，再线性求和 |
| p.9接收jitter | `b*s` | 0/1×多值宽度；`s`本身是常系数和 | 改为`ite(B,s,0)`，不产生多值×多值项 |
| p.10多周期 | `β*o`、`β*c` | `β`为0/1分配位 | 改为`B => o≥jT`、`B => c≤(j+1)T` |
| p.10多周期 | `β*j*T`、`β*(j+1)*T` | `j`是展开时的实例索引；`T`输入常量 | `jT`、`(j+1)T`预计算为常数，或直接写上述蕴含 |

对于所有选择项，0/1整数`b`和Bool变量`B`满足精确双向映射`b=ite(B,1,0)`；`b*t=ite(B,t,0)`对任意整数`t`成立，而非近似或舍入。若需要全部项都用新整数变量`z`承载，也可以施加`(B=>z=t) ∧ (!B=>z=0)`；关系两支均为线性整数等式。`χ=b*b'`由`and(B,B')`直接表达，不应保留一个未受0/1约束的多值χ变量来人为制造NIA。

有限集合上的`∀e,k,i,l,m`在导出SMT时展开为有限个assertion，是生成过程的元层循环，而非保留一阶整数全称量词。固定周期、路由、速率、帧大小后，得到的模型只剩布尔连接、整数加减、数值常系数乘法、比较和`ite`，属于QF_LIA；OMT目标同样只需分段线性整数项。

### 4.2 主NIA资料应怎样分类

- **语法QF_NIA比较集（可保留）：** 忠实输出原式`b*time`，标记所有乘法可消去。此类数据可测试预处理与不同表达形式对优化器的影响，须同时保留等价LIA版本作对照。
- **实质多值NIA主集合（不纳入）：** 本报告没有剩余多值整数决策量的乘积；把`T`、`rate`、`L`、实例索引`j`或窗口个数改为符号变量，会改变原应用与原模型，不能据此把它作为原论文NIA来源。
- **NRA（不生成）：** 时间取整与有理数精确缩放应在输入层完成，未引入Real变量。

这并不否认论文作者在§5使用NIA编码的描述，而是区分该具体写法的逻辑声明和应用数学模型所必需的算术能力。

## 5. 时间整数化、变量界与导出路径

原文§5要求整数时间，但没有指定ns或µs。可复现时应把单位作为实例元数据显式固定。例如统一使用ns，再将输入bytes与速率转换为整数tick传输时长；如果精确传输时间为有理数，则用所有有理输入的分母最小公倍数缩放，使`T,L,D,J,δ`共同变成整数。周期和时延必须一起缩放，不能只把某条链路的`L`取整。

以不计额外协议开销的`8B/r`作为`L`计算只是一个明确的生成器约定；原文没有明确写出完整开销公式。若选择ceil至tick、计入header/IFG、加入processing delay，均要标为不同的工程实例化规则，不能声称它们来自2017报告。同样，采用TSNKit的ns格式并不意味着2017原式本来使用ns。

现有公式推出的界：

```text
0 ≤ o[e,k] ≤ c[e,k] ≤ hp_e−1
0 ≤ s[e,k] ≤ min(sum(i经过e,L_i^e), hp_e−1)
b[e,k,i] ∈ {0,1}
χ ∈ {0,1}
```

第一行用整数语义把严格界等价改写；第三/四行都是有限域。`O_i,C_i,ψ_i`由恰好一个选择值产生，分别有对应窗口时间/宽度界。`hp_e`与数据大小常量可能很大，尤其是互不整除的周期；这会扩大索引展开和整数位数，但不产生变量周期乘法。

推荐转换路径：`网络与流记录 → 固定route_i → 各链路帧集合 → 预计算L与hp → 展开有限窗口/流索引 → 原式选择器乘法版或等价Bool/ite版 → 显式指定目标及优化模式 → SMT-LIB文件和元数据`。本次只给转换说明，没有写转换程序或生成benchmark。

## 6. 多周期：原文完整与未完整的界线

§4 pp.9–10对流`i`按每条链路的超周期展开重复实例：`j=0..hp_e/T_i−1`，为每个`f_{i,j}^e`另设分配位。选中窗口必须完整落在该实例所属周期区间：

```text
β[e,k,i,j] = b[e,k,i,j]
β * o[e,k] ≥ β * j * T_i
β * c[e,k] ≤ β * (j+1) * T_i
```

`j`与实例个数由输入计算，所以精确的等价形式是选中时`jT_i≤o`且`c≤(j+1)T_i`。窗口大小要累加所有重复帧实例的固定发送时长，唯一分配也要逐实例施加。原文保留全部窗口的超周期边界，确保未分配任何帧的空窗口仍有界。

| 内容 | 原文支持程度 |
| --- | --- |
| 同周期窗口定义、排序、分配、宽度、序列、端到端约束、接收jitter公式 | 已逐项列出，pp.5–9；但流隔离印刷式另有逻辑缺口 |
| 多周期实例数、实例索引、按period区间约束选中窗口 | 明确给出，pp.9–10 |
| 多周期端到端时延 | p.10描述需检查同一流所有发送/接收实例，未展开完整公式 |
| 多周期jitter | p.10明确把earliest-open/latest-close归一到周期的公式留待future work；不能仅复制同周期`ψ−L`声称完整复现 |
| 逐链路`hp_e`不同情况下的跨链路实例对齐/完整超周期编码 | 未提供独立详细规则；实现时需要额外规范 |
| 多播、多帧、多队列、同步协议实现、frame preemption具体约束 | 描述为扩展方向或标准背景，未完整编码 |

如果未来只为比较构建完整版本，最小稳妥范围是同周期、固定路由、单队列、单播单帧，并显式区分“原式语义版”与“按文字意图修订隔离版”。多周期版本若使用全网公共`H=lcm_i T_i`而不是逐链路`hp_e`，这属于额外实现决策，应记录，不能把补全的jitter公式署给作者。

## 7. 应用数据生成步骤：方法说明，不执行

2017报告不发布随机benchmark参数分布或实例文件。下面是将该应用做成可追踪比较资料的步骤建议，所有额外选择必须写入元数据：

1. 固定图的拓扑、节点种类、有向速率及时间tick；选择流数量、端系统发送/接收点，并确定每流路径。网络、速率、路线作为输入，不在SMT中优化。
2. 优先取全部同周期`T`，指定固定bytes大小以及由统一规则得到的`L_i^e`；生成`D_i,J_i,δ`与`W_e`。记录所有取值范围与随机种子；报告没有提供“原作者范围”。
3. 构造每链路经过的帧集合并计算负载`sum_i L_i^e / T`。严格`close<T`使极满载有边界影响；容量失败可作为有原因的UNSAT候选，但不能只根据某求解器timeout赋UNSAT标签。
4. 根据§3展开窗口、选择位及所有路径约束；对流隔离式记录原式恒真问题。若选择修订隔离，仅输出另名版本，先验证队列语义。
5. 指定单个目标。例如总接收jitter，或明确声明自行选择总时延。多目标要记录先后顺序/Pareto模式及每个原始目标项，不能用未经说明的权重总和替代。
6. 对同一输入保留原始QF_NIA乘法表达和等价QF_LIA的Bool/ite表达，核验SAT/UNSAT状态、目标值和从模型恢复的窗口/选择值一致。此比较用于表达形式研究；它不会把数据转成实质多值NIA主数据。
7. 记录输入参数、代码/文档版本、SMT逻辑、目标、实例sha256、求解状态与证据、最优值/上下界、time/memory limit，以及生成规则出处。未完整补全多周期jitter时，不输出“已完整复现多周期原模型”的实例。

## 8. TSNKit：独立工具资料与使用核验

TSNKit是另一作者团队的工具。本次查阅的[官方首页](https://tsnkit.readthedocs.io/en/latest/index.html)引用2023综述预印本；[官方GitHub](https://github.com/ChuanyuXue/tsnkit)当前README引用Xue等人的RTAS 2024论文。**这不是Craciunas等2017报告的原作者benchmark。** 该工具可以提供独立网络/流输入，但其算法、默认参数与语义不能直接当作2017报告复现。

[Data preparation官方文档](https://tsnkit.readthedocs.io/en/latest/dataprep.html)和[Getting started](https://tsnkit.readthedocs.io/en/latest/quickstarted.html)标题版本为`0.3.0`；`latest`是可变URL。本次用只读`git ls-remote`固定官方main为`8924ff2f64329d99d3279602a1b7c96782ae5473`，保存有限相关源码和版本文件到[sources/tsnkit](./sources/tsnkit/)。`VERSION`同样是`0.3.0`，仍应同时保留commit，不能只靠版本标签锁定代码。没有克隆整个仓库。

### 8.1 输入格式及映射注意事项

官方输入是CSV：

| 文件 | 字段 | 单位/意义 |
| --- | --- | --- |
| `N_task.csv` | `stream,src,dst,size,period,deadline,jitter` | size为Bytes；period、deadline、jitter为ns；dst使用列表表示listeners |
| `N_topo.csv` | `link,q_num,rate,t_proc,t_prop` | link为有向边；rate为bit/ns；t_proc、t_prop是processing及传播时延 |

例如`rate=1`代表1Gbps，不是1byte/ns。TSNKit接受多队列、多播格式；2017报告正文为单队列、单播单帧，并已经给定route。若利用独立输入，须另外选择固定路由、设scheduled队列数为1并说明如何处理processing/propagation参数。不能把工具CSV直接读入当作原模型的全部参数。

### 8.2 官方生成器命令，仅供后续使用

```bash
python3.10 -m tsnkit.data.generator --num_ins 1 --num_stream 8 --num_sw 8 --period 1 --size 1 --deadline 1 --topo 1 --output ./tsn-input/
```

这是根据官方文档的单实例示例补上明确输出目录；此次没有执行。可配置参数为`--num_ins,--num_stream,--num_sw,--period,--size,--deadline,--topo,--output`。流数、交换机数及各模式接受单整数或逗号分隔整数列表；生成器对其取笛卡尔积再重复`num_ins`次，不是把列表整体当成一个参数值。拓扑编号：0=line，1=ring，2=tree，3=mesh，4=mesh_2d。

静态源码`DatasetGenerator.run`写入序号`N_task.csv`、`N_topo.csv`、`dataset_logs.csv`。路径直接用字符串`path+header`拼接，所以上述`--output`保留尾部`/`。官方generator.py默认：num_ins=1、num_stream=8、num_sw=8、period=1、size=2、deadline=1、topo=0、output=./；文档示例显式使用size=1、topo=1，不能误当默认值。源码内调用网络构造时固定`num_queue=8,data_rate=1`；CLI列出的主参数不让用户直接改这两值。

mode参数是离散模式编号，不是把`--period 1`当成1ns、或把`--size 1`当成1Byte。下表来自固定commit的[dataset_spec.py](./sources/tsnkit/dataset_spec.py)静态阅读；随机模式均从集合中用`np.random.choice`选值。

| `--period` | 周期值（ns） |
| --- | --- |
| 1 | 固定2,000,000 |
| 2 | 固定400,000 |
| 3 | {500,000; 1,000,000; 2,000,000; 4,000,000} |
| 4 | {100,000; 200,000; 400,000; 800,000} |
| 5 | {250,000; 500,000; 1,250,000; 2,500,000; 4,000,000} |
| 6 | {50,000; 100,000; 250,000; 500,000; 800,000} |

| `--size` | 大小值（Bytes） |
| --- | --- |
| 1 | 固定50 |
| 2 | 100..500，步长100 |
| 3 | 200..1,500，步长100 |
| 4 | 500..4,500，步长100 |
| 5 | 1,500..4,500，步长100 |

这些是工具的数据大小模式；模式4/5包含大于常见单个Ethernet帧的payload，映射至2017单帧模型时应显式限制或定义分帧规则，不能默认认为所有工具模式都是原报告的单帧实例。

| `--deadline` | deadline生成规则（ns） |
| --- | --- |
| 1 | 直接令`deadline=period`；不会调用`deadline_spec(1)` |
| 2 | `base + choice({100,000;200,000;400,000;800,000;1,600,000})` |
| 3 | `base + choice({10,000;25,000;50,000;100,000;200,000;400,000})` |
| 4 | `base + choice({0;10,000;20,000;25,000;50,000})` |
| 5 | `base + 0` |

其中`base=hop_count*(2000+8*size)`，`hop_count`由NetworkX最短路径边数得到；此计算配合工具固定rate=1 bit/ns。若候选`deadline>period`则重新采样；成功流的CSV行把`jitter`直接写成同一个`deadline`值，并非独立采样jitter预算。

流生成流程：优先从已累计egress利用率≤75%的end-system中随机选talker，若已无此候选则重新开放所有end-system；随机选不同listener；计算最短路径；采样period、size、deadline；只接受deadline不超过period的流，直到流数达到请求值。这里只优先控制源端口，不能据此认定整个网络所有链路负载≤75%，也不能认定输入必定schedulable。最短路径用于生成deadline，但route没有写入task CSV，原模型固定route仍需单独保存。

line/ring/mesh通常使用`2*num_sw`个节点并为每个交换机接一个end-system；tree构造二叉形连接，节点总数`2*num_sw+1`，生成器仍取`num_es=num_sw`，因此不要按一般任意tree生成器理解。mesh_2d要求交换机数量是平方数，`n=sqrt(num_sw)`，边界交换机各接一个end-system，端系统数为`4*(n−1)`。网络CSV使用q_num=8、rate=1、t_proc=2000、t_prop=0。

### 8.3 与2017报告的复现距离

- 固定commit生成器令`jitter=deadline`，因此没有独立jitter模式或严格jitter数据族；需显式调整预算才能研究2017报告的窗口数/抖动权衡，并标明该调整属于独立实例化。
- generator.py和dataset_spec.py没有直接初始化随机种子，且主参数定义未单独列出seed。core导出文件包含`SEED`常量，生成器还调用`core.parse_command_line_constants`；此公共函数源码的单文件直连获取未成功，因此本次不确认是否有额外seed CLI或是否实际执行NumPy种子初始化。不能仅凭generator.py断言整个工具没有seed。未来若补设随机种子，要记录调用方式及NumPy版本。
- 只读文档与源码能核验格式、单位、模式、算法步骤；没有执行，所以不声称该commit在当前Python环境运行成功。
- [TSNKit Benchmark generation](https://tsnkit.readthedocs.io/en/latest/benchmark.html)所称benchmark是TSNKit对应论文的实验，完整复现指向`legacy`分支及其数据，不是本2017报告的数据。


## 9. 保存的工具资料与核验边界

[sources/tsnkit/SOURCE_MANIFEST.json](./sources/tsnkit/SOURCE_MANIFEST.json)记录每个已保存资料的官方URL、bytes、SHA256和版本/commit；保留工具原LICENSE。文档原始Markdown与HTML快照分别为[dataprep.md.txt](./sources/tsnkit/dataprep_latest_0.3.0.md.txt)和[dataprep.html](./sources/tsnkit/dataprep_latest_0.3.0.html)，源码保留generator.py、dataset_spec.py、VERSION及相关包装/文档配置。所有源码只做静态阅读，没有import或执行工具模块。

获取过程中GitHub API触发匿名限流，raw域名部分直连超时；最终通过官方raw域名的可用边缘节点获取dataset_spec，三个下载副本SHA256一致。一次codeload流式读取达到16MiB上限即中止，只保存了选中的VERSION/LICENSE文件；一次仅获取Git树和单个blob的过滤fetch未成功。没有保留大型整仓库归档或检出整个仓库。这些网络失败不会被写成工具运行失败。

资料核验：本地PDF12页；当前作者PDF与arXiv PDF哈希相同；式(2)图片与抽取文本一致；固定commit源码VERSION与ReadTheDocs标题都为0.3.0；mode定义、flow sampling、jitter赋值、输出文件命名和默认参数均从已保存源码核验。尚未核验的是完整工具运行、公共seed初始化细节、作者未公开的实现及原报告未给出的多周期jitter公式。
