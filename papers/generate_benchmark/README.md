# OMT(NIA) benchmark 生成资料

先读 [生成方法总览](OMT_NIA_benchmark_generation_summary.md)，再按应用查看逐篇总结。

| 应用 | 方法总结 | 原文 |
|---|---|---|
| 最小面积装箱 | [整数编码、尺寸族、旋转、高精度整数化](Huang_Korf_2013_Optimal_Rectangle_Packing_方法总结.md) | [JAIR 2013 PDF](Huang_Korf_2013_Optimal_Rectangle_Packing.pdf) |
| 循环资源调度 | [论文公式、公开模型、资源展开与新图生成](Bonfietti_2011_Cyclic_RCPSP_方法总结.md) | [CP 2011 作者全文文字](sources/cyclic-rcpsp/paper-author-fulltext.txt) |
| 三维管道布线 | [公开非线性源码、立方柔性、单位与新布局](Belov_2017_3D_Pipe_Routing_方法总结.md) | [CP 2017 PDF](Belov_2017_3D_Pipe_Routing.pdf) |
| TSN 窗口调度 | [NIA 审查、原式问题与独立数据生成器](Craciunas_2017_TSN_Formal_Scheduling_方法总结.md) | [2017 报告 PDF](Craciunas_2017_TSN_Formal_Scheduling.pdf) |
| 装箱搜索策略补充 | [CP 2008 来源与阅读范围说明](Simonis_2008_Rectangle_Packing_阅读范围说明.md) | 尚未取得完整原文 |

原始参考清单：[OMT_NIA_application_benchmark_references.pdf](OMT_NIA_application_benchmark_references.pdf)。

`sources/` 保存官方装箱模型、20 个循环调度输入及模型、5 个非线性管道子模型、TSNKit 相关资料，各自有固定来源版本和哈希清单。TSN 经审查可消除所有选择器乘法，暂排除主要 NIA 数据源。

已收集 3 篇完整 PDF 和 1 篇作者稿全文文字。CP 2011 原 PDF 下载返回 403，全文以文字存档且未保存图像；CP 2008 补充文献的全文入口失效/受限，已明确记录。文件校验见 [collection_manifest.json](collection_manifest.json)，微型 NIA 示例验证见 [example_verification.json](example_verification.json)。
