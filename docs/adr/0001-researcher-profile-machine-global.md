# 0001 · 研究者画像存机器级配置，不随论文

研究者画像（领域/立场/熟悉）是「你是谁」的稳定数据，跨所有论文/扫描不变。存机器级 `${XDG_CONFIG_HOME:-~/.config}/paper-muse/researcher.md`，而非 spec v2 §4 原设的每篇论文 `output_dir/docs/agents/muse/profile.md`。

**为什么**：路径恒定 → 复用键最稳（不受主题措辞漂移影响，无 `PAPER_MUSE_OUTPUT_DIR` 时尤甚）；与任何论文/仓库解耦（写第二篇不重敲）；不进 git（`*results/` 已被忽略，且避免把个人画像写进 GitHub 历史）。

**代价 / 边界**：每台机器首填一次；跨机同步由用户自行软链。困惑不属画像（见 CONTEXT.md），作本次扫描的一次性输入单独处理，不落此文件。
