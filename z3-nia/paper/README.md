# SAT 2027 方法写作工作区

当前阶段只整理方法，不生成实验结论或投稿文件。

- [methods_zh.md](methods_zh.md)：中文方法讨论稿。整体算法先行，随后 C1 联合改善搜索、C2 核心引导邻域、C3 见证驱动认证三节展开，含四份伪代码与三个命题。
- [贡献草稿](../论文核心贡献草稿.md)：论文主线与引言贡献表述。
- [实现梳理](../当前算法流程与创新点梳理.md)：对应当前代码的详细事实与源码入口。

## 章节组织与实现边界

保留已有三个贡献。前两节聚焦提高搜索能力：C1 是 SLS 内部的结构化动作与修复；C2 用 SMT core 指导 SLS 和邻域扩展，因此不是纯 SLS。第三节讲原式模型与证明接口，重点展开 C3 的整数无界证书。普通严格改善查询是认证框架的一部分，不单独当作新的创新点。

方法中的最小化约定只是数学叙述的归一化。实际代码仍保存输入的 min/max 方向。伪代码把当前内联控制流程组织成语义子过程，不表示新增了另一套求解器或改动了运行算法。

`F` 表示导入后的 native CNF。普通候选和射线的精确核验针对它；原式查询使用另外保留的 `Fs`。从原式模型到 native 的补全过程不应被写成通用双向 model converter。方法中的目标值集合等价关系是预处理应保持的语义前提，不是运行时另有一个完整等价性证明器。

## 已安装的论文技能

使用内置 `skill-installer` 安装到用户技能目录，固定到以下版本；没有修改全局代理，没有安装第三方 API 服务或运行这些技能附带的脚本。

| 技能 | 用途与来源 | 安装版本 |
| --- | --- | --- |
| `ml-paper-writing` | 论文主线、整体算法与方法叙事、引文核验。[源码](https://github.com/KalarisLabs/research-agent-skills/tree/fa023d6d166562a8de118f59bb73647c0e444b8c/skills/ml-paper-writing) | `fa023d6d166562a8de118f59bb73647c0e444b8c` |
| `peer-review` | 后续作者自审时检查论证与证据，不替代会议审稿。[源码](https://github.com/K-Dense-AI/scientific-agent-skills/tree/5f2bc0341354fd5590aef76a533ada1e1e5b4e62/skills/peer-review) | `5f2bc0341354fd5590aef76a533ada1e1e5b4e62` |

安装路径分别为 `/home/lbh/.codex/skills/ml-paper-writing` 与 `/home/lbh/.codex/skills/peer-review`。新技能在下一轮对话可被自动发现。本轮已读取写作技能及其研究仓库写作、表达指南；审稿技能仅安装留作后续使用。

ML 写作技能中的其他会议模板、匿名规则和页数不能套用到 SAT。实际投稿要求始终核对 SAT 当届官方说明。当前先用 Markdown 讨论方法，尚未排版或编译 LaTeX。

## 会议要求核对：2026-11-02

截至本次核对，SAT 系列主站仍将 SAT 2026 列为下一届，未查到 SAT 2027 的官方作者指南。因此目前不固定 2027 的截止日期、页数或匿名要求。[SAT 系列官方主站](https://www.satisfiability.org/)

仅作为临时参考，SAT 2026 使用 LIPIcs LaTeX，长文 9–15 页、短文及工具论文最多 8 页，不计参考文献和附录；采用 single-blind。2027 的正式要求需要重新核实。[SAT 2026 官方 CFP](https://satisfiability.org/SAT26/pages/cfp.html)

SAT 2026 的 CFP 禁止论文包含 AI 生成正文，实验内容中的 AI 输出除外；允许编辑和润色作者文字。这不能推定为 2027 已公布的政策。当前稿因此明确作为内部方法讨论材料，作者后续须按当届实际政策决定正文写作与使用方式。[SAT 2026 的生成式 AI 说明](https://satisfiability.org/SAT26/pages/cfp.html#use-of-generative-ai)

## 当前实现参数的对应

这些数值用于后续实现与实验设置，不应取代方法章节的算法论述。

| 参数 | 当前完整实验配置 |
| --- | --- |
| 单例总时间 | 1200 秒，导入计入 |
| 最优检查的正常停滞等待 / 首片 | 10 秒 / 30 秒，开启 resume、burst、balanced |
| 无初解 Z3 救援 | native 阶段达到 300 秒后一次，最多 120 秒 |
| 核心邻域 | 默认停滞 60 秒，单片至多 2 秒，总预算至多 60 秒 |
| 有限大目标 / 无界触发阈值 | 幅值均为 1000000，按目标方向判断 |
| 射线构造 | 至多一次，最多约 5 秒，最多 8 个固定集变体 |
| 搜索数值表示 | 坐标与导入系数为 signed 128-bit；优化值与射线核验使用任意精度整数 |
| 证明辅助开关 | core-neighborhood、convex-exchange、smt-exchange=hints、guarded-product；BV 软预算 120 秒 |

命题、伪代码和符号仍需作者逐项审定；文中没有写入未做的实验、统计显著性或未验证的“首次”主张。
