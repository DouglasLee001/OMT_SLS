# Z3 5.1 内嵌 NIA_LS 实施计划

> **For agentic workers:** Use superpowers:subagent-driven-development to implement and review each independent module. Do not commit on the user's behalf.

**Goal:** 把 Git 提交 `6614f3c1ea13539918b6f641c64446b753e6db0e` 的 NIA_LS 编进独立的 Z3 5.1，提供搜索、无界证明和有界最优证明接口，并验证效果不弱于该提交。

**Architecture:** `z3-nia/` 由 `cp -a --reflink=auto competitor/z3-5.1.0 z3-nia` 得到，竞争算法目录保持不动。`sls/src` 使用 Git archive 导出的提交版；C++ 前端通过同一个 Z3 内核解析、预处理 SMT2，产生原有 native IR，再直接调用已编入当前可执行文件的 SLS。保留基线严格改善查询、SAT 模型回灌及证明子进程暂停续传行为，不引入工作树中尚未提交的交换信息实验。

**Tech Stack:** C++、Z3 5.1 C/C++ API、GMP、CMake/Make、Python 实验与验证脚本、Slurm。

## 接口和边界

- `z3-nia/build/z3 --nia-ls INPUT.smt2 --mode=sls|unbounded|optimal`：默认运行时限 1200 秒，兼容提交版调度参数和模型输出参数。
- `sls` 关闭两种证明；`unbounded` 打开无界证明；`optimal` 同时打开无界和有界最优证明。另保留两个独立证明开关以便消融。
- 原提交默认使用自有 LIA 局部搜索做初始化，Z3 负责 AST 构造与求值；`--initialize=off` 可单独关闭该初始化。
- 所有 AST 处理与 SMT 查询链接新副本编出的同一套 Z3 core，不启动旧 `nia_ls`、Python converter 或 competitor 的 Z3 可执行文件。
- 此阶段不声称已完成冲突、子句或内部活动度的信息交换；为下一阶段保留独立模块和内部调用边界。
- 原生 SMT2 输入仅支持单个非增量 QF_NIA 整数目标，与提交版转换器的限制一致；不悄悄忽略多目标或增量命令。

## Task 1: 固定基线和源码隔离

- [x] 读取 HEAD 并确认现有 dirty 文件，禁止以工作树实验作为性能基线。
- [x] cp 独立 Z3；复制来的 build 改名 `build-competitor`，从新的 `build/` 配置编译，避免旧 CMake 缓存写回 competitor。
- [x] `git archive HEAD` 提取 `sls/{src,converter,tests}` 和只读参考 `reference/{NIA_LS,script,result}`。
- [x] 记录复制来源、提交哈希、源文件校验和、编译参数和二进制校验和。

## Task 2: 原生 C++ SMT2 导入

**Files:** `z3-nia/sls/frontend/nia_import.h`, `nia_import.cpp`, `z3-nia/sls/tests/test_native_import.py`.

- [x] 先写与提交版 Python converter 对照的测试，包括最大化、目标变量被消去、整数 ITE、布尔分支、非线性展开、keep-definitions、非法输入。
- [x] 实现 parse-only 命令过滤、目标 marker、相同顺序的 Z3 tactics、精确 GMP 多项式与原 native IR 编码。
- [x] 对 original/new 的输入进行编码对照；对 fresh 名称的差异单独解释，不以求解器输出代替语义验证。

## Task 3: SLS 编入 Z3 与三个入口

**Files:** `z3-nia/sls/CMakeLists.txt`, `z3-nia/sls/frontend/nia_frontend.{h,cpp}`, 新副本 shell/CMake 的必要接线。

- [x] 写模式与参数映射测试，先观察未实现接口失败。
- [x] 原提交的 SLS 编为独立 object/static 组件，删除主函数冲突，保留随机数调用与初始化次序。
- [x] shell 添加 `--nia-ls` 分支，标准 Z3 入口继续沿原路径运行。
- [x] 提供 C++ native IR 入口，内部暂用私有文件复用初始化/修复；记录读取/预处理时间并计入总时限。
- [x] 运行 `cmake`，`make -j64`；检验无外部旧 NIA_LS、旧 Z3 库依赖。

## Task 4: 正确性与提交版回归

- [x] 有限最优 min/max、无界 min/max、不可行、无初始解、SAT 回灌与暂停续传回归。
- [x] 使用独立模型验证器检查返回模型，证明状态必须来自实际证明结果。
- [x] 对之前 1058 反例进行小规模回归，禁止继承尚未提交的 user-propagator/callback 实验。冻结运行 `NIA_20261028_151848_060693` 的回归 index 6197，两版均为 B、目标值 915，未继承旧实验错误 A2=980；该证据仅覆盖此例。
- [x] 复查原测试有无错误依赖 competitor；测试配置直接指定新构建产物。

## Task 5: Slurm 性能对照

**Files:** `z3-nia/experiment/embedding_experiment.py`、说明文档与独立测试。

- [x] 只允许 server、node1–5；按 `min(CPUTot, CPUEfctv)-CPUAlloc >= 80` 和健康状态筛选节点，支持共享 MIXED 节点。
- [x] 每个节点最多 80 worker，1200 秒逐实例时限，不使用 `--exclusive` 或 `--mem=0` 抢整节点资源。资源不足时准备任务并等待，不强行运行。
- [x] 提交版参考与内嵌版使用相同实例、种子、证明参数和预算；冻结二进制、配置和输入清单，存入 `/data/shared/lbh/OMT/result/NIA_{时间戳}/`。
- [x] 先覆盖 original/new 类型和已知易退步例子，再完整集合。`NIA_20261029_011322_291446` 已完成 6310 例新鲜配对：成功求解 5887/5889、有初始解 6286/6288；内嵌版全体 38胜6235平37负，双方未解决的411例为26胜356平29负。
- [x] 对小回归差异排查编码、预算、参数、调用次序和墙钟敏感轨迹，并重测 GNU++17 候选与基线自重复。保留52严格gate少1个证明的失败、全量37个逐例loss和B/C子集变化，不把这些抹成逐例无损；全量已达到下列预先声明的整体标准，结论仅限本轮整体实测。
- [x] 增加显式全量采证模式；原回归失败保持可见，完整采证不等同验收。全量冻结前声明：无新增错误、可行解数不减、A1+A2 不减、目标值胜数不少于负数；逐例零负单独展示。

## Task 6: 收尾与审查

- [x] 独立审查模块边界、证明可靠性、文件来源、资源策略。
- [x] `z3-nia/README.md` 写明构建、三模式调用、实验命令、结果位置和已验证的范围，并披露baseline02完整数据清理失败后的显式元数据恢复。
- [x] 保留旧实验源文件与审计记录，除非确认可安全删除；不把它们编入新 solver。
- [x] 全部6310对当前IR已直接重读核验；6294字节相同、15精确重编号后相同、1完整Tseitin定义投影后相同，原16例字节差异保留，严格报告及可复验工具归档。
- [x] 汇报实际完成情况和证据，不自动 git commit。整体实测达预声明标准，逐例与B/C子集波动如实保留；不把它写成逐例保证。
