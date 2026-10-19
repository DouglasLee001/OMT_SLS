# 新旧应用类型与参考样例对照

本次先读 START_HERE，再读 master、四应用、验收与来源范围，并对照旧论文总结、模型快照和参考包。原 new_benchmark 为空，因此旧文献覆盖某类型不等于已有正式生成器。

| 类型 | 重合情况 | 本次处理 |
| --- | --- | --- |
| 持仓 portfolio | 新指南新增；starter 已有四个 DAX 转换小样 | 单一目录；四个原数据重放，另生成 Gram 合成风险与微型回归 |
| 循环 cyclic | 旧 Bonfietti 资料与新指南重合 | 单一目录合并；保留具体 MiniZinc 源语义，生成 planted 新输入，不另建论文同名类型 |
| 排样 packing | 旧 Huang/Korf、Simonis 与新指南重合 | 单一目录；方块来源参数与新固定方向矩形是 profile，不按论文重复建类型 |
| 实验设计 design | 新指南新增；starter 已有三个整数子族小样 | 三例重放；新整数向量、多种次数预算及有理缩放回归；两编码共用应用ID |
| 管道 pipe | 旧目录有完整论文和5个公开非线性模型，新指南获取状态过时 | 更正来源状态；本轮按四主族规格暂缓，未伪造精确转换 |
| TSN | 两套资料均不宜作为多值 NIA 主族 | 不生成 |
| 齿轮 gear | 新指南暂缓，变量分母影响目标排序 | 不生成 |

## 19 个来源重放不计新增

- DAX：smallinvDAXr1b010-011、smallinvDAXr1b150-165、smallinvDAXr3b100-110、smallinvDAXr5b200-220。
- cyclic：easy_1、easy_2、medium_1、hard_1。
- packing：square n=3..10。
- design：d=3,m=6,N=6,seed=1；d=4,m=7,N=7,seed=2；d=5,m=8,N=8,seed=3。

它们是按源参数重新构建、补充见证与核验的 replay，不能因改变变量名或编码而增加新增计数。三维随机设计 d3/m6/N6/seed1 与已有参考完全重复，配置主动省略。方块 n3 同时承担微型回归，不另生成一个相同数据的微型文件。

## 与原下载 SMT 大基准的关系

既有 `/data/shared/lbh/OMT/benchmarks/QF_NIA` 的来源目录包括 UltimateLassoRanker、LassoRanker、AProVE、mcm、leipzig、20170427-VeryMax/{ITS,CInteger,SAT14}、calypto，未发现以这里四应用命名的来源。该检查依据目录和来源清单，未对五千余个公式进行任意重命名/数学等价去重，不宣称全库语义互异。

## 来源证据

MINLPLib 原文从官方站点下载并核对已知SHA，新增缓存保留原样与实际下载日期。MiniZinc模型与DZN来自旧目录固定快照，生成时匹配 pinned SHA。D最优整数向量属于新应用数据，论文仅支持数学模型背景；planted调度与Cauchy–Binet编码不归于论文作者。未将随机合成参数称作真实工业/市场测量数据。
