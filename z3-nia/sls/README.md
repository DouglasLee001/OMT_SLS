# SLS 模块

`src/` 保存 Git 提交 `6614f3c1ea13539918b6f641c64446b753e6db0e` 的 NIA_LS 搜索和证明逻辑，供新 Z3 shell 直接调用。统一构建使用上级的 `build.sh` 与本目录的 `CMakeLists.txt`；`src/Makefile` 保留作提交版构建记录，不用于新 shell 链接。

`frontend/nia_import.cpp` 用当前 Z3 内核读取 SMT2，并输出原 `NIA_LS_OMT 1` 编码，保持目标表达式、变量和子句的遍历顺序。`frontend/nia_frontend.cpp` 处理三种模式、预算和输入/输出路径。`src/call.cpp` 保留原参数校验和执行逻辑，通过内部函数入口接入；写入检查在原参数校验通过后执行。

搜索、无界证明、有界最优证明及 SAT 模型恢复没有引入旧工作树的回调实验。暂停与恢复沿用提交版 proof 子进程，后续会话和信息共享计划见 [深度融合待实现.md](深度融合待实现.md)。
