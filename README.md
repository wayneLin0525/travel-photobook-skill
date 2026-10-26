# Travel Photo Book · 旅行照片书

将旅行照片文件夹制作成精选照片书，最终只有一个PDF和一个可离线翻阅的单文件HTML。选片与设计规则共用，宿主使用自己的视觉能力，不绑定对话模型厂商，也无需额外大模型API Key。

**v0.2.0 为公开测试版，首先提供 Apple Silicon Mac 运行包。** 已通过本机 Skill 入口完成真实旅行照片书制作；WorkBuddy 等其他助手的一句话安装与完整制作仍需真实联调。Windows、Intel Mac 和其他宿主的状态见下表，未测试项不算已支持。

## 用户使用方式

在能够访问本地照片、执行程序并实际查看图片的助手中说：

> 帮我安装 https://github.com/wayneLin0525/travel-photobook-skill 中的旅行照片书 Skill，技能目录是 skills/travel-photobook。安装后，把「照片文件夹的绝对路径」制作成照片书，输出到「新的成品文件夹」，只交付 PDF 和可以离线翻页的 HTML。请自动完成运行组件、模型准备和制作流程。

也可以从 [v0.2.0 下载页](https://github.com/wayneLin0525/travel-photobook-skill/releases/tag/v0.2.0) 导入 `travel-photobook-0.2.0-generic.zip`；WorkBuddy 需要扩展元数据时使用 `travel-photobook-0.2.0-workbuddy.zip`。助手根据其正常技能安装入口安装，不能把整个源码仓库当作已经安装好的技能目录。

助手安装`skills/travel-photobook`，按SKILL.md完成运行包及模型准备和持续制作。平台首次目录与执行授权仍按其正常机制处理。若只有技能说明能安装、程序不能运行，不报告安装制作成功。

## 仓库组成与能力

- `skills/travel-photobook/`：公共技能、审阅协议、OS原生启动器。
- `src/photobook/`：唯一维护的制作引擎；含逐张决定、地点证据、版本与恢复。
- `scripts/`：字体准备、按系统构建运行包、技能导入包与验证。
- `tests/`：只使用合成/允许公开的素材；不带私人旅行照片。

初筛→本地向量→去重→地点确认→事件聚类→事件及全书精选→故事→排版→实际页面检查→两成品发布。助手必须实际看图；程序不会假装有宿主模型API，也不自动写假审阅。

## 开发与发行

开发者使用Python3.12，创建虚拟环境后安装`requirements-runtime.lock`及`.[dev]`，运行`python scripts/prepare_font.py`、`python -m pytest -q`。这段是维护者流程，普通用户无需执行。

用`python scripts/build_runtime.py --local-manifest`构建当前系统运行包和私有测试技能。运行`python scripts/check_runtime.py`检查移位、无开发PATH的执行；本地测试清单只在`dist/`，不提交机器路径。

运行包约 465 MB，不含模型；首次自动下载约 354 MB、固定 SHA-256 的 CLIP 权重并缓存。下载期间需要能访问 GitHub Release 和模型源站；失败时助手应报告实际错误，不能要求用户手动拼环境或假装成功。字体随运行包携带，元数据读取使用内置库，无需安装Perl或ExifTool。运行包使用PyInstaller目录模式；按OS/架构分别构建，不将Mac虚拟环境复制到Windows。

项目采用 **AGPL-3.0-only**，见 [LICENSE](LICENSE) 与第三方资源声明。维护者上传运行包后，以真实`--release-base-url`生成平台清单。再构建通用技能ZIP或`build_skill.py --platform workbuddy --author <真实作者>`。不填不存在的用户名或下载URL。`ci/verify.yml.example` 提供 Mac/Windows 的 GitHub Actions 构建模板，当前未启用自动工作流；模板存在不代表相应系统已经测试通过。

| 范围 | 当前状态 |
| --- | --- |
| 真实旅行完整制作、通用排版、离线阅读器 | 本机 Skill 入口已完成 272 张可解码旅行照片的完整制作；57 张入书、32 页内页和封面，PDF 与网页检查通过。私人素材和审阅数据不随仓库发布 |
| 当前Mac源码驱动与运行包 | macOS15.1.1 ARM64：移位启动、原生安装器、真实HEIC编码、两成品发布已验证；不是另一台干净机器的用户验收 |
| Windows x64、Intel Mac | 构建/测试配置已提供，未在本轮实际执行 |
| Codex技能完整新会话、Claude Code、WorkBuddy、QwenWork | 需分别真实安装联调 |
| 豆包工作、Muse、Grok Bot | 接入能力待核验，不声称已兼容 |

本轮完成598张元数据字段回归、593张真实编码回归；1000/3000张合成素材验证的是本地流程和检索规模，不代表几千张真实照片的视觉精选已验收。小样的全部页面实际审阅，PDF/HTML由独立运行包输出，并通过六种窗口和离线翻页浏览器检查。

资源条款见THIRD_PARTY_NOTICES.md。用户最终结果目录没有日志、ZIP或说明；内部job缓存保留回溯材料。HTML可离线阅读，制作中的宿主视觉能力可能联网。
