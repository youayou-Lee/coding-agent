# 工程开发规范：流程、计划、测试

> 本项目（单人 + AI 协作）的实际工作规范。原则：**main 随时可发布，每个变更有据可查，一切以数据为准**。
> 北极星（项目定位与毕业标准）见 mind：`20_我是谁/决策/决策-coding-agent项目三重定位.md`；本文件管"怎么干活"。
> 配套设施：CI（unittest，PR 强制）、分支保护（required check 对齐 + strict）、Issue 模板。

## 0. 计划表三层：在哪、怎么看

| 层 | 载体 | 位置 |
|---|---|---|
| 北极星（月级以上） | 三重定位 + 毕业标准 | mind 决策文档（版本收口时对照更新） |
| 版本切分 | Roadmap（本文件 §6）+ GitHub Milestone | 见 §6 |
| 任务级 | Issue（挂 Milestone） | GitHub Issues |

**看进度的方法**：

```bash
# 网页：仓库页 → Issues 标签 → Milestones（有进度条）；或直达
#   https://github.com/youayou-Lee/coding-agent/milestones

# CLI：
gh api repos/youayou-Lee/coding-agent/milestones --jq '.[] | "\(.title): \(.open_issues) open / \(.closed_issues) closed, due \(.due_on)"'
gh issue list --milestone "v0.5" --state all     # 看某版本下所有任务
```

## 1. 一个功能的生命周期：七步，每步合格标准

### Step 1 立项 —— 写 Issue
- [ ] "要解决的问题"讲清场景（痛点，不是"实现 X"）
- [ ] 验收标准 ≥3 条，每条**可测试**（不写"体验更好"，写"TB 对照不退步"）
- [ ] 挂 Milestone；预计 >3 天 → 拆成子 Issue（见 §2）
- 样例：Issue #1 及其子 Issue

### Step 2 设计 —— 先方案后代码
- [ ] Issue 下补设计评论：模块划分、核心数据结构/枚举、接缝（改哪些文件）、测试计划
- [ ] 能用一段话讲清"错误/数据怎么流动"；讲不清 → 回去重想
- [ ] 关键决策列 A/B 备选 + 取舍理由（决策可追溯）

### Step 3 开发 —— 分支 + 小步提交
- [ ] `git switch main && git pull --ff-only` 后切 `feat/xxx` / `fix/xxx` / `docs/xxx`；一分支一 Issue
- [ ] **测试红不 commit**；一个 commit 一件事；message `type: 动机`（feat/fix/docs/refactor/test/chore）
- [ ] 混了就 `git rebase -i` 拆（AI 代劳）

### Step 4 自测 —— 三层（详见 §3）
- [ ] L1 unittest 全绿（与 CI 同套）
- [ ] 涉及 agent 行为 → L3 TB 对照集不退步（6/6 → 6/6）
- [ ] 测试数据留痕，贴进 PR

### Step 5 PR
- [ ] 描述四要素：动机（`Refs #N`）/ 改动（逐模块一句话）/ 验证（数据）/ 风险与回滚
- [ ] CI 绿（分支保护强制，不许绕）；merge 前自己通读一遍 diff

### Step 6 合并收尾
- [ ] 只用 squash：`gh pr merge --squash --delete-branch`
- [ ] CHANGELOG 当天有条目；`Closes #N` 仅限"本 PR 完全解决该 Issue"，前置/关联一律 `Refs #N`
- [ ] 核对 Milestone 进度

### Step 7 版本收口 —— 复盘
- [ ] Milestone 全关 → `git tag v0.x.0 && git push --tags`
- [ ] 对照北极星毕业标准逐项更新（功能勾选、TB 数据）
- [ ] CHANGELOG 版本总结：做了什么、数据、下一版本为什么是它

## 2. 计划怎么分

```
毕业标准（7 项功能 + TB 达标 + 发布）
  → Milestone（版本，4-6 周量级）：v0.5 → v0.6 → v0.7 → 发布
    → Issue（1-3 天原子任务，一 Issue = 一 PR）
```

**拆 Issue 规则**：
- 一 Issue = 一 PR，预计 1-3 天；估超 → 继续拆
- 每版本**先打通最小可验收路径**（happy path 全链路），再补边界加固——防止单点完美主义而链路不通
- 依赖关系写进 Issue 正文（"依赖 #N"）；父 Issue 用 tasklist（`- [ ] #N`）跟踪子 Issue
- **Milestone 建立时机：上一版本收口时建下一个**，不提前全建，计划跟实际走

## 3. 三层测试体系

| 层 | 测什么 | 怎么跑 | 何时跑 | 合格标准 |
|---|---|---|---|---|
| **L1 单元** | 纯逻辑（不碰 LLM/shell）：分类器、状态机、包装函数 | unittest，`test_*.py` 就近放 | 每次 commit 前 + CI 强制 | 全绿；每公开函数有正常例+边界例（空/极值/非法） |
| **L2 组件** | agent loop 编排行为（fake 模型按脚本吐 JSON） | scripted 场景，零 API 成本 | agent 行为改动的 PR | 四路径覆盖：正常完成 / 失败重试 / 重试耗尽放弃 / 主动停止；断点类改动加恢复场景 |
| **L3 评测** | 真实 LLM 端到端（TB） | 开发期 6 题对照集（分钟级）；版本收口 80 题全量 | 涉及 agent 行为的 PR 跑对照集；纯重构/文档不跑 | **不退步**；数据贴 PR；毕业以 80 题数据定分 |

**四条纪律（铁律）**：
1. 测试红 → 不 push 不 merge
2. 修 bug 先写复现测试再修；修复后测试永久留在回归集
3. "应该没问题"不作数，TB 数据说话
4. **红测试 = 契约冲突**，只有两种合法处理：

| 红的原因 | 判定 | 动作 |
|---|---|---|
| 故意变更行为 | 契约需要更新 | **同一 PR 内更新测试**——新契约必须在 diff 里可见，reviewer 能看到契约怎么变 |
| 无意改坏 | 代码有 bug | 修代码恢复原行为，测试一字不动 |

**禁止第三种处理**：删测试、skip/xfail 来"消灭"红色。红测试是行为变更的强制确认点，绕过它 = 契约静默变更。
（首例：2026-09-05 #4，`backend.run` 失败行为从返字符串改为抛 ToolExecutionError，旧测试同步替换为 `test_nonzero_exit_raises_tool_execution_error`，PR #9）

## 4. 一票否决速查表

| 环节 | 一票否决项 |
|---|---|
| Issue | 无可测试验收标准 → 不开工 |
| 设计 | 讲不清数据流动 → 重想 |
| 开发 | 测试红 commit → 打回 |
| PR | CI 不绿 / 描述缺要素 → 不 merge |
| 收尾 | CHANGELOG 缺条目 / Issue 关错 → 补完算完 |
| 版本收口 | 毕业对照表未更新 → 不打 tag |

## 5. 常用命令

```bash
# 分支与提交
git switch main && git pull --ff-only
git switch -c feat/xxx

# Issue / PR
gh issue create -t "标题" -l enhancement -m "v0.5" -b "正文"
gh pr create --fill-first          # 标题取首个 commit，正文补四要素
gh pr checks                       # CI 状态
gh pr merge --squash --delete-branch

# 测试
uv run python -m unittest discover -s coding_agent -p "test_*.py" -v
./tb_run.sh run --agent-import-path coding_agent.tb_adapter:CodingAgentTB \
    --dataset-path tb/tasks --task-id <任务名>

# 版本收口
git tag v0.x.0 && git push --tags
```

## 6. Roadmap

| 版本 | 内容（对应毕业标准功能项） | 状态 |
|---|---|---|
| v0.4 | Provider 故障切换 + 多行命令（TB 6/6） | ✅ 已发布 |
| 流程脚手架 | Issue/PR 模板 + 本文档 + Milestone | ✅ 2026-09-04 |
| **v0.5** | 功能 2：LLM/工具可靠性（错误分类 + 恢复策略，Issue #1 拆 4 子任务） | 🚧 进行中，截止 09-30 |
| v0.6 | 功能 3 plan/todo；功能 4 会话持久化+断点恢复；**80 题全量基线** | v0.5 收口时建 |
| v0.7 | 功能 5 上下文压缩；功能 6 文件工具集；功能 7 权限模式；定毕业分 | 未建 |
| 发布 | tag + README 面向使用者重写 → 训练场毕业 | 未建 |

> 本文件随流程演进更新；改本文件也走 PR（docs/ 前缀）。
