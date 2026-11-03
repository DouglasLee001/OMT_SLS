# LaTeX 方法稿

本目录用于先阅读和修改方法部分，正文为中文。整体伪代码先行，然后依次展开三个机制：约束耦合的目标改善搜索、核心引导邻域与搜索反馈、见证驱动的混合认证。包含四份算法、三个命题及证明。

- `main.pdf`：编译后的阅读稿。
- `main.tex`：论文入口。
- `sections/`：按章节拆分的正文，方便逐节修改。
- [materials/README.md](materials/README.md)：从 `z3-nia` 集中迁入的算法解释、贡献草稿、研究方向与历史方案索引。
- `template/`：完整官方 LIPIcs 模板及示例，保留原文件和许可证。
- `build.sh`：一键编译；中间文件位于 `build/`，阅读稿输出为 `main.pdf`。

在本机重新编译：

```bash
cd /home/lbh/OMT/writing_paper
./build.sh
```

模板使用 [Dagstuhl 官方下载页](https://www.dagstuhl.de/en/publishing/series/details/LIPIcs) 当前推荐的 `lipics-v2021 v2021.1.3`，与 [SAT 2026 CFP](https://satisfiability.org/SAT26/pages/cfp.html) 所要求的模板系列一致。截至 2026-11-02，尚未核实到 SAT 2027 官方作者指南，后续按当届要求调整。类文件不作修改；中文阅读版额外加载 `ctex`，用 XeLaTeX 编译。官方出版流程建议 pdfLaTeX，英文投稿版需按正式要求重新核验。

完整模板来自 [官方 ZIP](https://submission.dagstuhl.de/styles/download-tag/lipics/v2021.1.3/authors/zip)。原始 ZIP 的 SHA256 为 `b9a4a6d6b44444cc4b9463c8d402e8f41b87e411a344b4af2508cd5bed6c19ef`；类文件的 SHA256 为 `ecae53dfd29c7b22d02a9065327c104760fb726a028b146d556732021a5977f0`。

本稿是内部方法讨论材料，不是可直接投稿的成稿。没有虚构作者、单位、资助或实验数据；暂未写引言、相关工作与实验章节。SAT 2026 对生成式 AI 正文有明确限制，不能推定为 2027 的已公布政策；正式写作与使用方式需核对当届 [官方说明](https://satisfiability.org/SAT26/pages/cfp.html#use-of-generative-ai)。

方法依据现有 [中文方法稿](materials/methods_zh.md) 和 [实现梳理](materials/当前算法流程与创新点梳理.md)，没有修改求解器。数学叙述以最小化为例；实现保留输入的优化方向。前端保持可实现目标值集合是可靠性前提，搜索表示中的见证不等同于恢复了所有原式变量的模型。核心邻域依赖 SMT 信息；严格改善检查和调度本身不另包装为新贡献。

编译器和渲染工具在仓库外的 `/home/lbh/.tmp/omt-paper-tools` 配置，不将 TeX 发行版放进 Git。若在另一台机器上使用，安装包含中文支持的 TeX Live，并确保 `xelatex`、`ctex`、Fandol 字体及算法包可用。
