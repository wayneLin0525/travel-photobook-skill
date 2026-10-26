# 平台接入边界

公共业务流程见workflow.md。安装目录由平台技能安装器决定，不让用户手抄路径。仅有技能格式可读不代表支持本地图片与程序执行。

- Codex：让其技能安装器从仓库发现`skills/travel-photobook`；使用当前文件、图片和执行工具。可选界面信息在agents/openai.yaml。
- Claude Code：公共技能可放`.claude/skills/travel-photobook`或个人技能目录；其他Claude聊天/工作环境另外验证。
- WorkBuddy：从 https://github.com/wayneLin0525/travel-photobook-skill/releases/tag/v0.2.0 导入发行的技能包；技能源码目录为`skills/travel-photobook`。若平台要求市场字段，使用build_skill.py生成的WorkBuddy包，不改变选片流程。
- QwenWork / Qwen Code：按各自安装入口导入；两者不是同一宿主。
- 豆包工作、Muse、Grok Bot：待确认具体版本和真实工具能力，不能仅凭名称声称已通过。

助手能够执行程序但不能直接看本地图时，先尝试宿主已有图片附件/图像工具；没有可用路径就报告能力缺失。不要截屏整个桌面代替授权照片读取。

源码安装与运行包准备应由助手完成。发行清单未配置时启动器明确停止；禁止临时猜测GitHub用户名、下载地址或要求普通用户自行构建Python程序。开发模式可由维护者显式提供PHOTOBOOK_ENGINE，不属于普通用户安装。

当前公开测试运行包只提供 Apple Silicon Mac（macos-arm64）。不要在 Windows、Intel Mac 或 Linux 上下载并尝试运行这个 Mac ARM64 包；没有对应平台发行清单时明确报告尚未提供。
