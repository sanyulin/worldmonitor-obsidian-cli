# World Monitor Obsidian CLI

将本机或自托管 **World Monitor** 的新闻交给 **DeepSeek** 整理，每天写入 Obsidian。默认通过后台 CLI 运行，不打开浏览器，不需要 Codex 或 Obsidian 保持打开。

公开仓库只包含程序、通用配置示例和合成测试。运行配置、加密凭据、日志、采集快照和个人日报保存在本机，默认位于源码目录以外。

## 能做什么

- 默认后台启动，按北京时间每天 `08:00` 生成；当天时间已过则补运行。
- 关注全球大事、科技与 AI、经济和供应链；限制同一来源数量、保守合并重复记录。
- 对关键陈述提供来源链接和输入资料中的原文短引文。
- 新闻不足、模型失败或引文校验失败时保留失败阶段，不伪造日报。
- 原子发布笔记，完整日报不覆盖、不重复调用模型；并发执行由文件锁保护。
- 阅读入口显示最近日报、失败原因和最近七个日历日的可见运行记录。
- 网络 GET 请求有限重试；每个日期最多三次自动尝试，失败后间隔约十五分钟。

**边界：** 标题和摘要不是新闻全文；引文匹配不等于事实正确。不同媒体可能转载同一稿件，来源数量不代表独立证据数量。本工具不提供事实认证、交易决策或全球新闻完整覆盖承诺。

## 前提

1. Python **3.11 或以上**，只使用标准库，无需 `pip install`。
2. 已有 Obsidian 笔记库，其中存在 `.obsidian` 目录。
3. 已部署并配置可用的 [World Monitor](https://github.com/koala73/worldmonitor)，能够访问 `/api/news/v1/list-feed-digest`。
4. 有效的 World Monitor API key 和 DeepSeek API key。

World Monitor 部署及 API 权限由其上游项目决定。本仓库不分发上游实例密钥，不绕过 API 访问限制，也不包含完整 World Monitor 源码。请先按[上游自托管文档](https://www.worldmonitor.app/docs/getting-started)完成部署并检查新闻采集可用性。

Windows 可使用本工具提供的 DPAPI 密钥录入和登录自启动。Linux/macOS 支持手动后台启动和环境变量凭据；本版不自动安装其系统服务。

## 快速开始：Windows

进入克隆后的仓库目录，使用自己的现有笔记库路径：

```powershell
python cli.py init --vault "D:\Notes\MyVault"
python cli.py secrets
python cli.py probe
python cli.py
```

`secrets` 会在本机终端隐藏输入两个 API key，并用当前 Windows 用户的 DPAPI 加密。不要把密钥放进命令参数、截图、Issue 或 Git 提交。

最后一条命令默认等价于 `python cli.py start`：启动后返回终端，后台继续运行。使用 `probe` 只检查采集，不调用 DeepSeek；需要至少六条有效新闻才算采集检查通过。

如果已有可运行的 Docker Compose 部署，希望生成日报前自动启动它，可在初始化时指定：

```powershell
python cli.py init --vault "D:\Notes\MyVault" --worldmonitor-dir "D:\Apps\worldmonitor"
```

此选项只执行已配置部署的 `docker compose up -d --no-build`，不会替用户构建镜像、创建上游密钥或修改代理、防火墙和 WSL。没有 Windows Docker CLI、仅在 WSL 内部署时，请先自行启动该实例，使用默认的本机 API 地址接入。

## CLI 命令

| 命令 | 用途 |
|---|---|
| `python cli.py` | 默认后台启动 |
| `python cli.py start` | 后台启动，已运行时不重复启动 |
| `python cli.py status` | 查看后台是否运行、当日尝试与最近结果 |
| `python cli.py stop` | 请求停止；等待当前日报工作结束后退出 |
| `python cli.py run` | 立即生成当日日报；已有完整笔记时跳过 |
| `python cli.py probe` | 检查真实新闻采集，不调用模型 |
| `python cli.py serve` | 在前台运行，适合调试或交给系统服务管理器 |
| `python cli.py install-autostart` | 安装当前 Windows 用户登录后隐藏启动的任务 |
| `python cli.py uninstall-autostart` | 仅移除本工具的登录启动任务，不删除任何文件 |

安装登录自启动后再运行一次 `python cli.py` 即可立即生效。停止后台不会同时卸载登录任务。若需要暂停后续开机运行，先执行 `uninstall-autostart`，再执行 `stop`。

安装器优先使用当前用户的隐藏计划任务；账户没有注册权限时，退回当前用户的 Windows“启动”文件夹，以隐藏的 Windows Script Host 启动后台 CLI。两种方式均延迟45秒，不要求管理员权限。若系统策略同时禁用这两种方式，仍可手动后台启动，登录自启动则需由系统管理员配置。

电脑需要保持开机，Windows 登录自启动需要用户已登录；本工具不会唤醒关机电脑。电脑在当天设定时间之后启动时会补运行，但不会用今天的新闻冒充以前漏掉的日报。

## 配置与本地数据

`init` 会创建本地配置，默认位置为：

- Windows：`%LOCALAPPDATA%\worldmonitor-obsidian-cli\`
- Linux/macOS：`~/.local/state/worldmonitor-obsidian-cli/`

可在启动前设置 `WM_OBSIDIAN_HOME` 改变本地状态目录。`config.example.json` 仅是通用示例，实际配置由 `init` 写入上述目录；不要直接把个人路径提交回示例。

可编辑本地 `config.json` 的 `hour`（北京时间，格式 `HH:MM`）、`base_url`、`worldmonitor_dir` 或采样数量，修改后停止并重新启动后台。远程 API 必须使用 HTTPS；本地默认 `http://127.0.0.1:3000`。

配置不包含密钥。Windows 密钥文件为 `credentials.dpapi.json`，只能由对应 Windows 用户解密；迁移电脑后应重新录入。本工具也支持进程环境变量：

- `WORLDMONITOR_API_KEY`
- `DEEPSEEK_API_KEY`

Linux/macOS 请通过自己的服务管理器或安全环境配置提供这些值，避免在共享终端历史中直接键入密钥。不要共享本地状态目录，其中的日志、快照、任务 XML 和日报引用可能包含个人路径或新闻数据。

## 故障与成本

先运行 `python cli.py status`，再在本机检查 `background.log`、`latest-run.json` 和 `runs/<run-id>/manifest.json`。提交 Issue 时只提供脱敏后的错误类型，不要上传整份日志或采集快照。

- 没有新闻：检查 World Monitor 自己的采集源、API key、网络和必要的代理。
- 模型失败：检查账户可用性和模型权限；当前默认模型为 `deepseek-flash`，可在本地配置调整。
- 同名日报不完整：程序保留原文并报错，请先检查该笔记，再自行决定如何恢复。
- 原子发布依赖笔记库所在文件系统支持硬链接（如 NTFS/ext4/APFS）。不支持时会失败并保留暂存，不退回到可能写出半份日报的方式。
- 暂存、运行快照和测试数据会保留，不自动批量删除；可自行审阅和清理明确的本地文件。

每次模型 POST 不自动重发；后台任务稍后的再次尝试仍可能产生新的调用费用。完整日报一旦生成，后续相同日期运行不会重复调用模型。源数量或格式变化可能影响采样，本仓库没有绑定上游私有数据集。

## 验证

```powershell
python -m unittest discover -s tests -v
python cli.py --help
```

测试只使用合成新闻、临时本地笔记库及模拟 API，不需要真实密钥，也不会读取你的实际笔记或上传内容。测试文件留在忽略的 `test-state/`，不自动批量删除。

公开版还应在你自己的实例上运行 `probe`，再按需执行一次 `run`，确认真实接口、网络和模型权限；离线测试不能证明所有部署环境均可工作。

维护者发布前先审阅 `git diff --cached`，再运行 `python check_public.py`。该检查读取已暂存内容，阻止运行数据、常见密钥格式、个人绝对路径和非本机 IPv4 地址进入明确的源码清单；它不能替代人工检查，也不能发现所有形式的敏感信息。

## 隐私与开源许可

仓库从干净的源码导出开始，不携带个人工作目录的历史。`.gitignore` 排除配置、凭据、日志、运行档案和备份；发布时仍应检查暂存区和提交内容，不能只依赖忽略规则。

采用 **AGPL-3.0-only**，见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。上游 World Monitor 单独部署、单独维护；本项目不代表上游官方。
