# CP 2008 装箱搜索策略：来源与阅读范围

## 文献

Helmut Simonis、Barry O’Sullivan，*Search Strategies for Rectangle Packing*，CP 2008，LNCS 5202，pp.52–66，DOI [10.1007/978-3-540-85958-1_4](https://doi.org/10.1007/978-3-540-85958-1_4)。这是用户参考清单中的补充背景文献。

## 获取情况（2026-10-11）

**本次尚未取得该文献的完整 PDF，不能声称逐页读完了它。** 已核对出版社摘要和书目信息，并追踪公开原稿入口：

| 来源 | 地址 | 本次结果 |
|---|---|---|
| 出版社 | [Springer 章节](https://link.springer.com/chapter/10.1007/978-3-540-85958-1_4) | 页面明确为订阅内容，可读摘要 |
| 作者机构原稿 | [4c.ucc.ie 作者 PDF](http://4c.ucc.ie/~hsimonis/Search%20Strategies%20for%20Rectangle%20Packing.pdf) | 连接超时；`www.cs.ucc.ie` 对应备用路径为 404 |
| CiteSeerX 存档 | [存档入口](https://citeseerx.ist.psu.edu/document?doi=20a627af7e426c11f49f20e1ee27105022b6c408&repid=rep1&type=pdf) | 可检索正文片段；直接下载重定向至不可连接的 Archive 地址，未获得文件 |
| ResearchGate | [书目页面](https://www.researchgate.net/publication/221633171_Search_Strategies_for_Rectangle_Packing) | 标记为 Request Full-text，未提供可下载全文 |

没有将另一篇相近题名的 *Using Global Constraints for Rectangle Packing* 当作本篇论文，也没有将摘要、首面预览或自行排版文件冒充原稿。

## 已有资料可以支持什么

出版社摘要确认它采用 SICStus Prolog 的标准 CP 装箱模型，讨论搜索策略和改进。对此只作背景说明，不从摘要推导完整生成算法。

另外，已完整取得的 [Huang 与 Korf 的 JAIR 论文](Huang_Korf_2013_Optimal_Rectangle_Packing.pdf) 在 §3.4（p.57）讨论了 Simonis 与 O’Sullivan 的方法，包括先选横坐标、用区间缩小搜索、再选纵坐标以及累计约束。这里属于 **JAIR 论文对 CP 2008 工作的描述**，不等同于直接阅读 CP 2008 全文。

对生成 OMT(NIA) 数据，现阶段已经可以依赖完整 JAIR 论文与已保存的 [官方 square_pack.mzn](sources/square-packing/square_pack.mzn)：整数容器宽高、整数物品坐标、包含和不重叠约束，以及整数面积目标 `W*H`。具体生成步骤见 [装箱方法总结](Huang_Korf_2013_Optimal_Rectangle_Packing_方法总结.md)。

搜索标注、区间分裂的变量顺序和 CP 传播算法属于求解过程。它们不是把同一装箱输入生成多个独立应用实例的理由。取得本篇原稿后可以补充其启发式细节及结果表，目前不将这些未核实内容写成原文结论。
