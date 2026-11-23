# ai —— 命令行 AI 工具包

零依赖（纯 Python 标准库），配合自建知识库（kb-core）使用。

## 安装

```bash
# Windows（Git Bash / CMD）
# 把 kb/tools 目录加入 PATH，或直接：
py -3 kb\tools\ai.py config     # 首次配置 APIKey 与服务器
```

## 常用命令

| 命令 | 作用 |
|------|------|
| `ai ask "问题"` | 一次性问答 |
| `ai sum 长文.txt` | 超长文档深度摘要（自动分块汇总） |
| `ai trans 文章.md --to en` | 翻译 |
| `ai prompt list` | 查看提示词库 |
| `ai prompt weekly-review 本周记录...` | 套用模板 |
| `ai kb "关键词"` | 检索个人知识库 |
| `ai clip 资料.pdf.txt --source doc` | 文件推入知识库 |
| `ai digest` | 今日知识库日报 |
| `ai stats` | 库存与 token 消耗统计 |

## 提示词库

`prompts/` 目录：`weekly-review`（周复盘）、`meeting-notes`（会议纪要）、
`code-review`（代码审查）、`learn-card`（费曼学习卡）、`writing-polish`（中文润色）。
新增模板：拷贝任意 `.md`，正文里用 `{{input}}` 占位即可。
