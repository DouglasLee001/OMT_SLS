# 删除额外凸切线模块

**目标：** 按用户要求物理移除额外的目标凸切线实现及其两个整数变种，保留 Z3 自身的非线性切平面、其他辅助证明及暂停续跑。

**实施范围：** 删除 `sls/src/nia_omt_convex_exchange.{h,cpp}`、独立证书检查器和专属测试；清理 C++ API、选项、遥测、构建依赖及 Python 参数透传。现有完整运行脚本改用当前构建，启用 guarded、finite-cutset、hints、BV120。纯 Z3 入口作为独立对照保留。

历史实验目录、冻结二进制、日志和归档论文材料保留；不提交完整实验，不提交 Git commit。

- [x] 删除 C++ 实现、调用和构建依赖。
- [x] 迁移测试并确认旧参数被拒绝，保留其他功能覆盖。
- [x] 清理 Python 参数白名单，更新当前运行脚本和说明。
- [x] 核查 Z3 原生切线的具体规则并确认源文件不变。
- [x] 重新编译，运行接口、调度及原有求解回归。
- [x] 检查活跃源码和二进制均无额外凸切线实现；预演完整实验入口。
- [x] 独立审查、记录验证结果并给出运行命令。

## 验证记录

- `make -C z3-nia/build -j4 shell`：通过，日志 `/tmp/omt-remove-convex-build.log`。
- 新二进制 SHA256：`e4a8d7d29d3a45513353356cc4ff14f7636160bcdc779df12ddd7f2e0941db0d`。
- `test_removed_convex_controls.py`：旧版本先出现预期失败；新版本4项通过，确认三个旧参数及带值形式拒绝、其他证明路径仍正常。
- 原有提交版回归68项：67通过、1个既有可选数据集跳过，报告 `/tmp/omt-remove-convex-committed.json`。
- 适配器9项、Slurm40项、plain CLI7项、round2 CLI6项、advisory-share CLI3项全部通过。
- shared-budget、round2 advisory、SMT fallback、BV模型/守卫/宽度/暂停计时组件全部通过；命令记录 `/tmp/omt-remove-convex-test-verification.json`。
- 三个完整/纯配置入口预演均选择6310例、每节点180并行、每例1200秒，没有创建快照或提交实验。
- 活跃自定义实现与检查器文件已不存在，`nm -C`确认无自定义convex符号，原生 `nla::tangents::tangent_lemma()` 仍链接。
- 原生5个NLA源文件与competitor及保存的5.1.0源码归档逐字节一致，报告 `/tmp/omt-native-z3-tangent-audit-20261104.json`。
- 独立审查确认参数顺序、保留辅助路径和入口配置一致；历史构建receipt仍保留其真实旧版本信息。
