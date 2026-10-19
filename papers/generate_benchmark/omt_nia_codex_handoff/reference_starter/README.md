# OMT(NIA) 应用转换小样

核查日期：2026-10-19。本包用于检查“自然应用模型能否转换为 OMT(NIA)”，不是经过完整性能评测的正式基准发布。

本包有 19 个 QF_NIA 优化输入：4 个整数持仓组合、4 个循环资源调度、8 个最小面积排样，以及 3 个新生成的整数系数 D-optimal 实验设计。前三类保留所核查模型的目标与整数域；第四类采用文献模型，但输入数据由本包新生成，不能称为原论文实验实例。

## 1. 整数持仓组合：最容易扩充的现成来源

来源是 MINLPLib 的 Small Investor Mean-Variance Portfolio Optimization，原始来源为 POLIP。持仓决策表示可交易最小单位的整数倍；对于预算较小的投资者，连续持仓比例不能直接替代这些决策。风险是整数持仓数量的二次式，预算与收益要求是线性约束。

参考实例：https://www.minlplib.org/smallinvDAXr1b150-165.html

原模型有 30 个非负整数决策变量和一个连续风险辅助变量 objvar。非线性约束是 Q(x) <= objvar，而目标是最小化 objvar。objvar 没有其他模型作用，因此可以代入并消去，改为直接最小化 Q(x)。把文本中的有限小数系数视为精确有理数，并乘以正的公共分母 D，就得到纯整数多项式目标 C(x)=D*Q(x)。该操作保持最优持仓集合，原风险值是 C/D。

本包四个例子的 D 均为 2500000000。这个缩放因子由 Fraction 和公分母计算产生，不采用浮点近似或四舍五入。既有求解器的数值界仍受其浮点容差影响，不能自动充当 SMT 精确最优性证明。

已核实的 MINLPLib 当前列表含 30 个 DAX 参数变体，按收益要求和预算范围变化。本包仅转换其中四个。SNP 系列在列表中属于已撤下实例，给出的原因是原实例生成时存在精度损失，未将其纳入小样。

实例目录与移除记录：https://www.minlplib.org/instances.html

本包核对了 smallinvDAXr1b150-165 的公开 p2 持仓点。在转换模型中，该点满足全部约束，精确风险为 88.10493476。核对参考点并不等于证明其精确全局最优。

参考点：https://www.minlplib.org/sol/smallinvDAXr1b150-165.p2.sol

## 2. 循环资源调度：有逻辑结构和整数变量乘积

来源：https://github.com/MiniZinc/minizinc-benchmarks/tree/master/cyclic-rcpsp

实际优化模型：https://github.com/MiniZinc/minizinc-benchmarks/blob/master/cyclic-rcpsp/rcmsp.mzn

相关文献：Bonfietti, Lombardi, Benini, Milano, A Constraint Based Approach to Cyclic RCPSP, CP 2011。MiniZinc 模型独立提供了完整变量、约束和优化目标；本包不声称其代码与该论文的具体实现完全相同。

整数变量 s[i] 表示周期内的开始位置，k[i] 表示迭代编号，T=s[n_tasks] 是周期。一个迭代中用来计算跨度的时刻是 s[i]-k[i]*T。优化目标保留原模型的 T*t_max+makespan，其中 makespan 是上述时刻的最大值减最小值再加 1。

关键的非线性是 k[i]*T：两个因子都是待求整数变量，k[i] 不是布尔选择器。非线性集中在 makespan 的恢复和次级优化中；若只优化周期，存在更简单的线性编码，不能声称所有循环调度模型都天然需要 NIA。

源码中的所有非虚拟任务持续时间均为 1，开始时刻为整数。因此，cumulative 资源约束可精确改写为：在每个任务开始的时隙，所有具有相同开始时刻的任务资源需求之和不超过容量。本包只对这一特定模型作该改写，不用于任意持续时间的 cumulative。

本包转换 easy_1、easy_2、medium_1、hard_1 四个公开数据文件，分别有 46、26、15、86 个任务。原仓库另有数据，可使用相同转换思路扩充。保留原模型的变量界、makespan 界和加权目标，未把它换成任意变量目标。

## 3. 最小面积排样：容易转换，非线性结构较简单

实际使用的优化源模型：https://github.com/MiniZinc/minizinc-examples/blob/master/square_pack.mzn

另一个明确带优化目标的版本：https://github.com/MiniZinc/minizinc-benchmarks/blob/master/rectangle-packing/rect_packing_opt.mzn

注意普通 rect_packing.mzn 的 solve 是 satisfy，不能只看该文件的题注就说它是优化实例。

相关文献：Simonis and O'Sullivan, Search Strategies for Rectangle Packing, CP 2008, DOI 10.1007/978-3-540-85958-1_4。论文讨论电子设计自动化等应用背景；本包的 1x1 到 nxn 方块族属于标准合成基准，不是实际芯片或工厂布局数据。

所有位置、宽、高都是整数；目标为 area=height*width。包含位置界、容器边界和两两不重叠的四选一析取。本包按 square_pack.mzn 转换 n=3,...,10，保留其变量域和面积上下界。该源文件中 diffn 和 cumulative 的调用均被注释，所以转换不需要为它们设计新的语义。

这是一类目标自然、易生成的 NIA 例子，但核心变量乘积主要只有容器宽乘高，不能代表复杂的高次 NIA 约束。对于整数尺寸的轴对齐排样，部分变体还有整数坐标最优解性质，因此也不宜用它作为“实数松弛一定改变最优值”的通用例证。

## 4. 精确 D-optimal 实验设计：适合生成高次多项式目标

文献：Hendrych, Besancon, Pokutta, Solving the Optimal Experiment Design Problem with Mixed-Integer Convex Methods。

论文：https://arxiv.org/abs/2312.11200

作者实现与实验生成代码：https://github.com/ZIB-IOL/OptimalDesignWithBoscia

每个 n[i] 是第 i 类实验的重复次数，必须为非负整数；总次数为给定预算 N。信息矩阵 M(n)=sum_i n[i]*a[i]*transpose(a[i])。原 D-optimal 目标最小化 -log(det(M))；在正定信息矩阵上，这等价于最大化 det(M)。当 a[i] 的系数是整数或有理数时，展开并清除常数分母可得到纯 NIA。这里去除 log 是基于严格单调性的等价目标变换。

在 d 个待估参数的情形，行列式对实验次数是至多 d 次的多项式。因此，d=3、4、5 可提供三次、四次、五次的目标表达式。

本包三个例子用整数实验向量新生成，前 d 个向量是标准基，其他向量按固定种子取 [-3,3] 的整数。它们属于文献模型的一个整数系数子族；与论文中的浮点/随机实验数据不同。要求 det(M)>0；由于 n>=0 时 M 为 Gram 型半正定矩阵，这正好保证正定。详细向量与种子记录在 manifest.json。

本转换器使用排列展开行列式，只针对小维度小样。它不是适合大维度的行列式编译器。大维度需要共享子表达式或其他精确多项式电路编码。

## 5. 其余曾提及的候选

TSN 门控窗口：Craciunas, Serna Oliver, Steiner, Formal Scheduling Constraints for Time-Sensitive Networks, 2017, https://arxiv.org/abs/1712.02246 。作者称其为 NIA，但正文主要乘积是 0/1 分配变量之间、或 0/1 量与时间量之间的乘积，可通过布尔条件和 ite 精确消去。因此它不作为本包的核心 NIA 来源。

工业管道布线：Belov et al., An Optimization Model for 3D Pipe Routing with Flexibility Constraints, CP 2017, DOI 10.1007/978-3-319-66158-2_21。大学论文页面确认与 Woodside Energy 的 LNG 工厂合作和 MiniZinc 实现。检索结果还显示整数毫米网格，但本次未取得完整原始 MiniZinc 模型及实例，未验证完整非线性编码，未转换。不要把此前的候选介绍当作已验证的数据包。

大学页面：https://research.monash.edu/en/publications/an-optimization-model-for-3d-pipe-routing-with-flexibility-constr/

齿轮组设计：https://www.minlplib.org/gear.html 。齿数是真正的整数变量，但原目标包含变量分母的齿轮比。给定误差阈值可以用正分母交叉相乘转为 NIA 检查；直接乘掉分母后最小化分子通常改变原目标排序。该类暂不作为简单直接的整数多项式目标转换来源。

## 6. 使用和核验

包中 generated 下有四个子目录，每个文件声明 QF_NIA，使用 Z3/OptiMathSAT 风格的 minimize/maximize 扩展。约束与决策变量均为整数和布尔；没有把真实连续变量强行改成整数。

生成仅需 Python 标准库。重新下载所选输入并生成：

```bash
python convert_models.py --fetch
```

核验脚本额外需要 z3-solver：

```bash
python -m pip install z3-solver
python check_examples.py
```

完整核验记录在 verification.json：19 个文件解析成功，全部有可行见证；投资组合检查公开参考点；循环调度另外计算前置关系、资源占用和原目标值；排样 n=3 通过可行解和更小目标 UNSAT 得到最优面积 15；三维 D-optimal 小例子枚举全部 462 个整数次数分配，最优行列式为 1152。Z3 Optimize 也直接读取并求得排样 n=3 的目标 15。

没有声称所有实例已求最优，没有完整跨求解器等价性评测，也没有做性能比较。原始模型的数学对照和更大规模抽查应在正式发布 benchmark 前继续完善。convert_models.py 只解析本包用到的 GAMS 多项式子集；不是通用 GAMS 或 MiniZinc 转换器。

manifest.json 记录目标缩放因子、源链接、输入文件 SHA256、变量数量和新生成设计参数。未打包上游建模程序；源模型和输入由上述公开地址取得。MINLPLib 页面标注 CC BY 4.0，应保留来源署名；其他上游资源使用其各自许可。

建议优先扩充整数持仓组合和循环调度；排样作为简单应用族；D-optimal 作为高次目标的生成型应用族。正式求解器比较还应参考各原问题已有的数学规划求解器结果。
