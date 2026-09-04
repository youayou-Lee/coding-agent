# PR 流程与工程开发规范

> 本项目（单人开发）的实际工作流程。原则：**main 随时可发布，每个变更有据可查**。
> 配套设施：CI（`.github/workflows/tests.yml`，unittest 17 项）、分支保护（required check `tests` + strict）。

## 一图流

```
Issue（登记问题，定义"什么算修好"）
  → git switch -c feat/xxx     （从最新 main 切分支）
  → 开发 + 小步 commit          （commit message 写"为什么"）
  → 本地自测                    （unittest + 涉及评测则跑 TB 基线）
  → gh pr create               （body 里写 "Closes #N" 关联 Issue）
  → CI 自动跑测试
  → 自我 review diff
  → squash merge               （main 历史保持 一 PR = 一功能）
  → 删分支，Issue 自动关闭
```

## 1. Issue

- 一切非常规改动（bug 修复、新功能、行为变更）先开 Issue，哪怕只有自己一个人。
- 标准：**读者不看代码就能判断"什么算修好"**。必须包含：现象/动机、复现或场景、验收标准。
- 模板已配置（bug / feature），`blank_issues_enabled: false` 强制走模板。
- 标签：`bug` / `enhancement` / `documentation`；跨版本的挂 Milestone。

## 2. 分支

```bash
git switch main && git pull --ff-only        # 先同步主干
git switch -c feat/tool-error-classification  # 前缀：feat/ fix/ docs/ chore/
```

- 分支从最新 main 切出；一个分支只承载一个 Issue。
- 命名用英文小写连字符，前缀标类型。

## 3. Commit

- 一个 commit 一件事；message 写动机，不写 diff 能自明的内容：

```
✗ fix terminal.py
✓ fix: 多行命令 base64 单行化，避免 send_keys 逐行敲入触发 PS2 续行吞掉完成信号
```

- 推荐格式：`type: 摘要`（type = feat / fix / docs / refactor / test / chore）。

## 4. 本地自测（合并前必过）

```bash
uv run python -m unittest discover -s coding_agent -p "test_*.py" -v
# 涉及 agent 行为/工具执行的改动，追加 TB 对照：
./tb_run.sh run --agent-import-path coding_agent.tb_adapter:CodingAgentTB \
    --dataset-path tb/tasks --task-id <相关任务>
```

**交付规范**（沿袭课程规则）：写完必自跑；不留残渣；live 声明以真实执行为准。TB 对照数据（几题、resolved 与否）直接贴进 PR 描述。

## 5. PR

```bash
git push -u origin feat/tool-error-classification
gh pr create --fill-first          # 标题取首个 commit，正文可再补
```

PR 描述至少包含：

1. **动机**：链接 Issue（`Closes #12`，合并时自动关闭）
2. **改动**：逐模块一句话（新文件/修改点）
3. **验证**：测试结果 + （如适用）TB 对照数据
4. **风险与回滚**：影响面；出问题 revert 单个 PR 即可

## 6. 合并与收尾

- **Squash merge**：分支上的碎 commit 压成一个进 main：

```bash
gh pr merge --squash --delete-branch
```

- squash 后的 commit message 即该功能的"一句话传记"，main 历史 = 干净的功能列表。
- Issue 随合并自动关闭；版本发布时打 tag：`git tag v0.5.0 && git push --tags`。

## 7. 计划表三层

| 层 | 载体 | 粒度 |
|---|---|---|
| Roadmap | 本文档 §8 | 月级，方向 |
| Milestone | GitHub Milestone（带截止） | 版本级，Issue 容器 |
| Issue | GitHub Issue | 1-3 天可完成的原子任务 |

向上汇报看 Milestone 进度条，向下执行拆 Issue。

## 8. Roadmap

- **v0.5** 可靠性二期：工具错误分类与恢复策略（Milestone: v0.5）
- **v0.6** 计划-执行分离（plan/todo）；评测扩容（TB 题库 → 20 题）
- **Backlog** Agno ModelProviderError 重试层与 ProviderChain 叠加语义；agent_timeout 平台计分影响确认

> 本文件随流程演进更新；改本文件也走 PR（docs/ 前缀）。
