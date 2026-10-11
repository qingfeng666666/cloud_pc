# 主机内置 LLM 对话接口（2026-10-09）

## 运行状态事件（安卓管理 1.1.6 / 桌面源码同步）

`open/current` 增加 `run_status={state,label,...}`、`status_text`；沿用现有云电脑链路，不新增端口。显示 Working、正在执行命令／工具、等待批准、重试中、已完成、已停止、失败、空闲。忙碌状态附加原有累计秒数，状态变化不重置计时。连接异常明确显示连接中断／旧内容已过期，不把超时伪装为 LLM 重试，不重发 SEND。

- Codex：订阅 `thread/resume` 后读取 turn/item/error 通知、`willRetry`、审批请求及 authoritative thread/turn 状态；公开思考摘要仍独立显示，不读取隐藏推理。
- DSH：读取历史里的 `tool/call`/`tool/result`（按 callId 配对）、`approval/asked`/`approval/decided`、`llm/retry`/`llm/retry-started`、`turn/end.reason`；原生 running 标志确认忙碌与结束。
- OpenCode：读取 `/session/status` 的 busy/retry/idle（重试次数及 next）、最新消息的运行工具/错误、`/permission` 的会话级待批准请求。旧版无 `/permission`（404）时降级，不自动批准。

API 未暴露的细节不杜撰；重试次数仅在后端明确提供时显示。工具审批仍须在原生界面操作。非忙碌的完成/失败/停止状态保留显示，不再隐藏整条状态栏。

此模块替代新管理界面的桌面键盘/AT-SPI转发链路。模型、提供商、登录、密钥、工具权限由用户在主机的各 LLM 原生程序中提前配置；管理界面不会修改这些配置或安装服务。选择 DSH/OpenCode 后，主机复用已有本地服务；端口未监听时按 `start_command` 参数数组启动服务。仅浏览提供商列表不启动服务；启动不重试 SEND，不使用 shell。启动日志在 `~/.local/state/cloud_pc/llm_logs/`，服务保持运行，不随管理界面返回而退出。

**当前主程序配置入口已统一为程序旁 `host.json` 的 `llm` 对象**，默认值首次启动生成，已有配置不覆盖。管理界面最近会话保存在同目录 `management.json`。以下旧 `llm_config.json`/用户目录路径仅为独立 LLMService 和显式 `--llm-config` 的历史接口说明，不是新版主程序默认读取路径。完整字段见 [三模块配置规则](../shared_components/design/CONFIGURATION.md)。

### 生成过程显示（2026-10-09）

安卓管理界面 1.1.1 及当前 Windows/Linux 管理界面增加独立的 `Working（23s）` 行，仅工作中显示，每秒按主机 `working_seconds` 更新，不修改正文 revision 或滚动位置。主机按提供商及会话分别计时：主机 SEND 成功时起算；对于原生界面发起的工作，从主机首次观察到 busy 起算（不是精确的模型运行耗时）。返回菜单或切换管理界面不会重置；观察到空闲后清零，主机重启后重新观察计时。

三平台管理界面同步支持“返回上次对话”、Working 下方独立的编号/提交结果、对话页面缩减底部留白。桌面端最后会话现合并进同目录 `management.json.last_dialogue`，只保存提供商和会话 ID，成功打开后更新，不保存输入或密钥。安卓配置版管理界面为 1.1.5；安卓从机为 0.5.23，WireGuard/从机独立启停，此 VPN 按钮变更不涉及 Windows/Linux（使用系统 WireGuard）。

沿用每秒 `current` 刷新，无新增对外端口，也不要求更新管理界面 APK。正文区按“思考摘要 / 工具执行 / 工具结果 / assistant”区分内容。
Codex 读取 reasoning.summary 和工具条目，并接收 agentMessage、summaryTextDelta、commandExecution.outputDelta 的增量事件；最终条目替换生成中条目。不会读取 reasoning.content 或 encryptedContent。OpenCode 读取公开 reasoning/text/tool parts（包括生成中的状态）；DSH 读取公开 reasoning/text 块、assistant/chunk 和 tool/call/result。没有公开摘要时不伪造思考内容。
这是增量内容经每秒快照显示，不是管理界面直连 SSE；历史阅读时仍遵守原有不抢滚动位置策略。工具输出每项截断至 8000 字符，整体正文仍有 65536 字符上限。权限确认仍在原生界面完成，不自动批准。

## 入口与连接

管理首页：从机界面1、从机界面2、LLM 对话界面、主机运行状态。

LLM 对话界面 → Codex / DSH / OpenCode → 主机返回的会话列表 → 继续指定会话或新建 → 顶部输入框、SEND、STOP、可滚动文字。会话归属各自程序，不在三个后端之间移植上下文。

从机设备：管理界面 → 本机从机（Android Binder，桌面127.0.0.1:19528） → 主机主动建立的CLDC连接 → 主机内置LLM模块 → 主机本地应用API。

主机设备：管理界面 → 127.0.0.1:19527 → 同一内置LLM模块。没有新跨设备端口，不依赖19627独立适配器。管理会话仍受单一活动界面策略约束。旧current接口暂供尚未更新的界面使用，不是新LLM页面路径。

## 主机配置

配置查找顺序：`--llm-config /绝对路径/llm.json`、环境变量`CLOUD_LLM_CONFIG`、用户目录`.config/cloud_pc/llm.json`、工程自带`llm_config.json`。更改配置后重启云电脑主机。只读此文件，不写入各模型的原生配置。

```json
{
  "codex": {"enabled": true, "socket": "auto", "command": ["codex", "app-server", "--stdio"]},
  "dsh": {"enabled": true, "url": "http://127.0.0.1:3080", "cwd": "/mnt/old-linux"},
  "opencode": {"enabled": true, "url": "http://127.0.0.1:4096", "cwd": "/mnt/old-linux"}
}
```

`cwd`可省略，省略时使用原生服务的默认目录。新增会话用此工作目录；继续旧会话不会将其强改为默认目录。`enabled:false`禁用入口。

- Codex：Linux `socket:auto`以HTTP Upgrade连接已有managed app-server的Unix WebSocket，使用`CODEX_HOME`或用户目录`.codex/app-server-control/app-server-control.sock`，没有新TCP监听。也可填绝对socket路径。没有daemon时明确报错，不自动另开一个服务冒充当前界面。
- Windows或用户明确省略`socket`时，按`command`参数数组启动本地stdio App Server，并继承主机用户已配置的登录、模型和权限。该进程可读取同一用户的持久会话，但不承诺与另一个原生终端的实时工作状态同步。希望实时同步时应配置连接既有服务的本地传输方案。
- DSH：默认 `start_command` 为 `["dsh", "web"]`；已有服务不重复启动。
- OpenCode：默认 `start_command` 为 `["opencode", "serve", "--hostname", "127.0.0.1", "--port", "4096"]`。找不到 PATH 命令时检查 `~/.opencode/bin/opencode`。自定义端口须同步修改启动命令；如需与原生界面同步，应使用同一个服务、工作目录和 session ID。自定义配置文件需提供自己的 `start_command`。
- OpenCode本地Basic认证可配置`username`、`password_env`，密码仅从主机环境变量读取，不下发管理界面。仅允许回环HTTP地址，不允许配置远程模型服务地址绕过云电脑链路。

## 请求

统一POST `/api/management/llm/{operation}`，JSON对象。经CLDC的操作名为`llm_{operation}`。不新增协议消息类型，沿用0602请求队列/0606编号返回。

| operation | 输入 | 返回 |
| --- | --- | --- |
| providers | `{}` | 三后端及主机是否启用 |
| sessions | provider, 可选cursor | 会话id、标题、时间、下一页游标 |
| create | provider, request_id | 新会话session_id |
| open | provider, session_id | 恢复指定会话并读取文字 |
| current | provider, session_id | text、revision、busy、stopped |
| send | provider, session_id, request_id, text | 编号、提交结果，不是最终模型回复 |
| stop | provider, session_id, request_id | 停止当前回合；空闲时无操作 |

每次明确传provider和session_id，不把当前选择存成主机全局变量。界面切换后的旧结果不应用到新页面。文字每1秒按需读取，无变化不重绘；首页/会话列表不轮询对话。首次定位最新，浏览历史不重置阅读位置。当前显示最近20轮Codex/20条DSH消息窗口，最终最多65536字符，后台原生会话不截断；`history_more`/`truncated`表明有更早内容，完整档案保存在原生程序。

## 发送、停止与安全

- 非空文字最多16384 UTF-8字节；API允许换行，拒绝NUL。
- 每次SEND/STOP/新建携带唯一ID，主机SQLite持久去重；相同ID不同内容报冲突。超时、断线和主机重启都不自动重发。已开始但结果未保存的请求标为unknown。
- 同会话串行执行。忙碌时 SEND 在主机排队，当前轮完成后按顺序提交下一轮，Codex 不使用 steer，不打断当前任务。DSH/OpenCode 同样先等待原生 API 报告空闲。状态栏显示排队条数，重复 request_id 不重复排队。最多64条；排队文字只存内存，不记日志、不在重启后自动重放。提交异常标记 unknown 并取消后续队列，绝不自动重发。
- STOP仅忙碌时发原生中止API，同一连续忙碌阶段不重复中止，不退出服务。新版不发送Enter/ESC，也不受桌面锁屏或焦点影响。
- 工具审批仍遵循原生配置，不自动放行。Codex审批通知显示在文字中；复杂审批、工具用户输入目前须在原生界面处理，管理页没有新增审批表单。不能把API接通视为所有工具交互均已适配。
- 主机日志记录来源IP、后端、操作、会话ID、SEND编号、阶段、字节数，不记录输入正文和密钥。主机运行状态显示会话/发送/停止操作；每秒current不刷满行为列表。

## 实测与限制

- 单元/链路测试包含去重、重启未确认不重放、会话隔离、失效管理界面拒绝、CLDC转发、DSH/OpenCode HTTP契约。
- 本机Codex 0.162.0：已有daemon会话列表和历史读取、新建、API发送均成功；独立测试会话返回“接口测试成功”。
- 本机DSH 3080：会话列表、历史、新建、发送均成功；独立测试会话返回同一测试回复。未修改声纹、语音或数字人程序。
- Codex与DSH的真实新建测试回合均成功调用STOP，结果为stopped；不是仅模拟按钮。测试回合独立于正在使用的会话。
- OpenCode：本机 1.18.33 服务按需启动成功，4096 健康检查、真实会话列表及公开思考/工具记录读取已通过；未额外发起模型对话付费测试。不同版本以原生`/doc`定义为准，当前不是v2 `/api/session`协议。
- 安卓管理1.1.0与从机0.5.21需一起更新：从机增加明确操作白名单，其他职责不变。APK编译通过，真机页面选择/SEND仍待安装后验证。
- Windows三EXE构建并下载到`cloud_pc/windows_version/20261009/dist`，最终文件大小/哈希以发布清单为准。Host完成健康/管理注册/providers接口实测，Management启动存活，Slave的帮助入口通过；未替换现有Windows运行从机。Linux源码包位于`cloud_pc/linux_version/dist/20261009`。
- Linux常驻主机已重启，新健康字段`llm_protocol=session-api-v1`，两个配置从机均恢复在线；18765私有下载服务已启动，仅允许WireGuard手机10.0.0.7。两个APK签名SHA256相同，可升级并保留设置。

协议依据：[Codex App Server](https://learn.chatgpt.com/docs/app-server)、[OpenCode Server](https://opencode.ai/docs/server/)、本机已安装DSH的`dsh-host-apiproxy/lib/types/api/sessions.schema.js`。
