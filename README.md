# Cloud PC 云电脑

Cloud PC 用于联通自己的多台设备，让运行在一台机器上的 LLM 能够通过统一接口操控其他设备，完成文件访问、命令执行和消息转发等操作。同时提供远程 LLM 对话界面，方便用户在其他设备上继续对话、查看回复，并掌握 LLM 的任务执行状态。

程序由主机、从机和独立管理界面组成，支持 Windows、Linux 和 Android；LLM 需由用户提前在主机上安装并配置，目前支持 Codex、DSH 和 OpenCode。

正式发布基线：**1.0.0**。Windows/Linux 主机、从机、管理界面及两个安卓应用统一使用该产品版本；CLDC 协议版本与第三方依赖版本独立，不随产品版本重置。仓库外历史安装包不会因源码版本修改而自动更新。

## 快速开始

通信方式：管理界面 ⇄ 本机主机或从机 ⇄ 主机服务；远程连接统一由主机建立。

安装[对应模块的依赖](#依赖)后，将源码目录命名为 `cloud_pc`，在其父目录执行所需命令。以下为 Linux 示例，每条命令在独立终端运行：

```bash
python3 -m cloud_pc.host_program.main --headless
python3 -m cloud_pc.shared_components.slave.main
python3 -m cloud_pc.management_ui.main
```

配置在首次启动时自动生成。先在主机 `host.json.slaves` 中填写从机地址，再在管理界面选择所在设备角色并连接。无需在每台机器上运行全部三个程序。

- [文档索引](#文档索引)
- [各平台启动方式](#2-启动程序)
- [配置文件与字段](#3-配置文件)
- [日常操作](#4-管理界面日常操作)
- [开发者接口](#5-开发者接口可选)
- [日志与常见问题](#6-日志与常见问题)
- [安全边界](#安全边界)

## 依赖

以下针对源码运行；使用已打包的 EXE/APK 时无需另外安装 Python 依赖。

| 模块 | Python/系统依赖 | 安装清单 |
| --- | --- | --- |
| 无窗口主机，`headless=true` | Python 标准库；所选 LLM 命令行/API 服务由用户单独安装配置 | `requirements-host.txt` |
| 主机自带桌面窗口，`headless=false` | PySide6，Windows/Linux 图形会话 | `requirements-host-gui.txt` |
| Windows/Linux 从机 | NumPy、SciPy、sounddevice；音频需 PortAudio 和可用输入/输出设备 | `requirements-slave.txt` |
| Windows 独立管理界面 | tkinter/Tk、Pillow | `requirements-ui-windows.txt` |
| Linux 独立管理界面 | GTK3、PyGObject、GdkPixbuf，由系统包管理器安装 | 见下方示例 |
| Windows EXE 打包 | PyInstaller 及待打包模块的运行依赖 | `requirements-build.txt` |
| 安卓工程构建 | Java 17、Android SDK 36、Gradle 依赖 | 各安卓工程的 Gradle 配置 |

在仓库根目录按需安装，例如：

```bash
python3 -m pip install -r requirements-slave.txt
python3 -m pip install -r requirements-host-gui.txt
```

`requirements.txt` 和兼容入口 `shared_components/requirements.txt` 汇总桌面开发依赖，包含 PySide6、Pillow、NumPy、SciPy、sounddevice、PyInstaller；只运行无窗口主机时无需安装全部依赖。

Ubuntu/Debian 的 Linux 管理界面与音频系统依赖示例：

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 libportaudio2
```

GTK 系统绑定需对启动程序使用的 Python 可见，独立虚拟环境未必能导入系统安装的 `gi`。Windows 安装 Python 时需包含 Tcl/Tk。当前从机截图后端依赖 Windows PowerShell、System.Drawing 和交互桌面，未提供 Linux 原生截图实现。

## 源码布局

| 目录 | 内容 |
| --- | --- |
| `host_program/` | 主机入口、管理会话、活动记录和 LLM API 适配 |
| `desktop_version/host/` | 主机网络与可选 PySide6 桌面窗口 |
| `shared_components/slave/` | Windows/Linux 共用从机源码 |
| `shared_components/common/` | 公共协议与配置 |
| `android_slave/` | 安卓从机 Java 工程，含 WireGuard 接入 |
| `management_ui/` | Windows Tk / Linux GTK 管理界面 |
| `management_android/` | 安卓管理界面工程 |
| `shared_components/design/` | 架构与配置设计 |
| `docs/dev/` | 历史开发和验证记录，不作为当前使用说明 |

仓库不包含 EXE/APK 等构建产物；安装包通过 Releases 单独发布。`windows_version/`、`linux_version/` 的本地发布目录不属于版本控制内容。

## 文档索引

除本文件外，仓库内还有以下说明文档。`docs/dev/` 为历史开发和验证记录，只反映当时的实现，不作为当前使用说明。

| 文档 | 内容 |
| --- | --- |
| [总体设计](shared_components/DESIGN.md) | 模块划分与通信模型 |
| [配置设计](shared_components/design/CONFIGURATION.md) | 三模块同目录 JSON 配置 |
| [主机程序设计](shared_components/design/HOST_PROGRAM.md) | 主机分项设计 |
| [从机程序设计](shared_components/design/SLAVE_PROGRAM.md) | 从机分项设计 |
| [管理界面设计](shared_components/design/MANAGEMENT_UI.md) | 管理界面分项设计 |
| [从机使用说明](shared_components/云电脑从机使用说明.md) | 从机端操作 |
| [HoloLens 2 从机使用说明](shared_components/HoloLens2从机使用说明.md) | HoloLens 2 从机操作 |
| [桌面主机程序](desktop_version/README.md) | Windows/Linux 主机模块 |
| [安卓从机](android_slave/README.md) | 安卓从机工程 |
| [独立管理界面](management_ui/README.md)、[安卓管理界面](management_android/README.md) | 管理界面模块 |
| [连接主机](management_ui/HOST_CONNECTION.md) | 管理界面连接主机流程 |
| [LLM 对话接口](host_program/LLM_API.md) | 主机内置 LLM 对话 API |
| [手动连接](host_program/MANUAL_CONNECT.md)、[管理会话](host_program/MANAGEMENT_SESSION.md)、[运行状态](host_program/ACTIVITY.md) | 主机连接与状态记录 |
| [第三方组件](THIRD_PARTY_NOTICES.md)、[安卓发布检查](docs/THIRD_PARTY_RELEASE.md)、[第三方源码包](docs/THIRD_PARTY_SOURCE_BUILD.md) | 许可证与发布合规 |
| [历史开发记录](docs/dev/README.md) | 历史开发与验证记录 |

## 1. 架构与网络

| 模块 | 职责 |
| --- | --- |
| 主机 | 主动建立、维持从机连接；转发请求、维护操作记录；按需调用本机 LLM API |
| 从机 | 接受主机连接；执行本机能力；主动转交管理请求和返回结果 |
| 管理界面 | 查看设备、截图、操作记录和 LLM 会话；通过所在设备的本机服务访问主机 |

```text
主机设备                                  从机设备
管理界面                                  管理界面
    │                                         │
    │ 本机 HTTP 127.0.0.1:19527                │ Windows/Linux: 127.0.0.1:19528
    ▼                                         │ 安卓: Binder
 主机服务 ═════ 主机主动建立 CLDC 连接 ═══════► 从机服务
    ▲                   TCP 9527                  │
    └──── 管理界面登记需求: TCP 19526 ────────────┘
```

- 两个 HTTP 管理端口仅对本机开放；跨设备使用主从 CLDC 连接，不开放远程管理 HTTP。
- 连接建立由主机发起；连接建立后，双方均可主动发送普通请求。管理请求按 ID 回应，不由主机轮询队列。
- 主机和从机均需支持 `management_push_v1`；正式版 `1.0.0` 包含该能力。
- 管理界面访问从机时，由主机另行联系对应从机；操作记录由主机直接维护，无需查询从机日志。
- 全部设备需处于局域网，或能互相访问的虚拟局域网。跨网络可用 WireGuard，配置中填写相应 VPN 地址。
- 安卓从机提供 WireGuard 配置和启停入口；Windows/Linux 使用系统安装的 WireGuard。用户自行准备 VPN 配置，VPN 不改变通信架构。
- 仅接入可信设备；不要将远程执行或登记端口无保护地暴露公网。

### 安全边界

- 两个 HTTP 管理端口 `19527`、`19528` 只监听本机回环地址，不提供远程管理 HTTP；不要用反向代理或端口映射把它暴露出去。
- 从机协议端口 `9527` 和管理登记端口 `19526` 仅应在可信局域网或 WireGuard 等虚拟局域网内使用，**不要映射到公网**；跨网络请走 VPN，而不是开放端口。
- 从机只应共享必要目录：`root` 范围越大，可读范围越大。写入、创建、删除、重命名受从机自身的路径允许策略校验，策略外的路径会被拒绝。
- 远程命令执行受从机端命令策略限制（例如 PowerShell 可能被限制为只读命令）。HTTP 任务收集超时约 60 秒，**超时不表示远端命令已终止**，不要据此重发有副作用的命令。
- 安卓配置可能包含 WireGuard 配置与私钥，不要放进共享目录或公开发送；同理，个人配置、日志和访问令牌都不要提交到仓库。
- 发布安装包应从已审查的源码构建，并使用自己的发布签名；第三方组件的许可证与源码提供义务见“许可证”一节。

## 2. 启动程序

按设备类型选择启动方式，只启动该设备需要承担的角色。例如，主机设备运行主机程序，受控设备运行从机程序；管理界面可安装在其中任一设备。

### Windows 设备

发布版分别双击运行：

| 程序 | 启动文件 |
| --- | --- |
| 主机 | `CloudHost.exe` |
| 从机 | `CloudSlave.exe` |
| 管理界面 | `CloudManagement.exe` |

源码运行需先安装 Python 和相应依赖，将仓库目录命名为 `cloud_pc`，在它的父目录使用 PowerShell 分别执行所需命令：

```powershell
python -m cloud_pc.host_program.main --headless
python -m cloud_pc.shared_components.slave.main
python -m cloud_pc.management_ui.windows_main
```

### Linux 设备

发布版在发布目录分别运行所需脚本：

```bash
./启动主机.sh
./启动从机.sh
./启动管理界面.sh
```

源码运行需先安装 Python 和相应依赖，将仓库目录命名为 `cloud_pc`，在它的父目录分别执行所需命令：

```bash
python3 -m cloud_pc.host_program.main --headless
python3 -m cloud_pc.shared_components.slave.main
python3 -m cloud_pc.management_ui.main
```

以上每条命令启动一个独立程序，需要同时运行时请使用不同终端。不要在同一终端依次执行并期待三个程序同时启动。

### 安卓设备

1. 安装安卓从机 APK 和独立管理界面 APK；当前不提供安卓主机程序。
2. 打开从机应用，需要 VPN 时填写自己的 WireGuard 配置并点击“启动 WireGuard”，按系统提示授权。
3. 点击“启动从机”。WireGuard 与从机独立启停，不需要 VPN 时跳过第 2 步。
4. 打开管理界面，设置主机 IP 并连接；主机需要已登记该设备的从机地址。

各平台都不要重复启动多个主机实例占用同一端口。发布包与源码可能存在版本差异，请核对版本。

## 3. 配置文件

三个模块分别维护自己的配置，不共用一份文件：

| 模块 | 配置文件 | 用途 |
| --- | --- | --- |
| 主机 | `host.json` | 初始连接目标、管理连接参数、日志及 LLM 后端设置 |
| 从机 | `slave.json` | 设备名称、监听端口、共享目录；安卓还保存 WireGuard 设置 |
| 管理界面 | `management.json` | 所在设备角色、主机 IP、最近使用的对话 |

### 配置保存位置与生效方式

- **Windows 发布版**：`host.json` 在 `CloudHost.exe` 同目录，`slave.json` 在 `CloudSlave.exe` 同目录，`management.json` 在 `CloudManagement.exe` 同目录。
- **Linux 发布版**：三个配置文件分别在对应的 `启动主机.sh`、`启动从机.sh`、`启动管理界面.sh` 同目录。
- **Windows/Linux 源码运行**：默认都保存到仓库根目录 `cloud_pc/`，不随启动终端的工作目录变化。
- **安卓**：从机与管理界面分别保存到各自应用的文件目录，具体路径见下文。

每个程序首次启动时生成自己的默认配置，之后读取已有文件；升级程序不会覆盖已有设置。手工修改 JSON 后重启对应程序生效；通过界面保存的设置由界面即时写入。配置目录必须可写。

### host.json

`slaves` 决定主机初始连接目标，例如：

```json
{
  "headless": true,
  "slaves": ["192.0.2.10:9527", "192.0.2.11:9527"],
  "current_slaves": [],
  "relay_grants": [],
  "management_bind": "0.0.0.0",
  "management_port": 19526,
  "management_interval": 5,
  "log_level": "INFO"
}
```

以上是文档占位地址，使用前替换为实际设备地址。已有文件中的 `llm` 等配置应保留，不要用上述片段覆盖整份生产文件。

<details>
<summary>字段说明</summary>

| 字段 | 含义 |
| --- | --- |
| `headless` | `true` 表示只运行主机服务，不打开主机自带的桌面窗口；独立管理界面仍可使用。 |
| `slaves` | 主机启动时主动连接的从机地址列表，格式为 `IP:端口`；空数组表示未配置目标。 |
| `current_slaves` | 旧当前窗口通道的兼容连接列表，一般保持 `[]`，正常从机统一配置在 `slaves`。 |
| `relay_grants` | 从机经主机转发只读文件请求的显式授权；一般保持 `[]`，需要时填 `源IP,目标IP,file.list` 或 `源IP,目标IP,file.stat`。 |
| `management_bind` | 管理登记服务的监听地址；`0.0.0.0` 表示监听所有 IPv4 网卡，不是主机本机 HTTP 管理接口的地址。 |
| `management_port` | 管理登记端口，默认 `19526`，供本机从机向主机登记管理界面连接需求。 |
| `management_interval` | 活动管理界面所在从机的连接维持检查间隔，单位秒，默认 `5`。 |
| `log_level` | 日志级别：`DEBUG`、`INFO`、`WARNING`、`ERROR`，一般使用 `INFO`。 |
| `llm` | 可选的 LLM 后端配置对象，设置 API 地址或启动命令等；省略时使用程序默认配置，模型及登录由相应 LLM 程序管理。 |

</details>

主机本机 HTTP 管理接口仍为 `127.0.0.1:19527`，不由 `management_bind` 或 `management_port` 修改。

### slave.json

```json
{
  "root": "G:\\shared",
  "port": 9527,
  "name": "MyServer1",
  "log_level": "INFO"
}
```

Windows 路径在 JSON 中反斜杠须写成 `\\`，也可使用 `G:/shared`。默认 `root` 为 `shared`，相对配置文件目录解析。扩大根目录会扩大共享范围，应只提供必要目录；文件变更还可能受到额外允许目录策略限制。

<details>
<summary>字段说明</summary>

| 字段 | 含义 |
| --- | --- |
| `root` | 共享文件根目录；默认 `shared`，相对路径以配置文件目录为基准。 |
| `port` | 接受主机连接的协议端口，默认 `9527`，不是本机管理接口 `127.0.0.1:19528`。 |
| `name` | 从机设备名称，用于识别设备。 |
| `log_level` | Windows/Linux 从机日志级别，一般使用 `INFO`。 |

</details>

安卓从机还保存 `host_ip`（主机地址）、`host_registration_port`（管理登记端口，默认 `19526`）、`wireguard_config`（用户填写的 WireGuard 配置）。安卓配置格式以应用生成文件为准，不要直接用桌面版示例覆盖。

### management.json

桌面版主要设置 `role`（`host` 或 `slave`）、`host_ip`、`last_dialogue`；安卓对应字段以应用生成文件为准。在连接设置中设置主机 IPv4 并连接。该地址用于让本机从机向主机登记，不是让管理界面直连远程 19527。从机列表连接后由主机返回，添加/移除会同步保存到主机 `host.json`。

桌面版示例：

```json
{
  "role": "slave",
  "host_ip": "192.0.2.1",
  "last_dialogue": null
}
```

<details>
<summary>字段说明</summary>

| 字段 | 含义 |
| --- | --- |
| `role` | 管理界面所在设备的角色：`slave` 通过本机从机访问主机；`host` 直接访问本机主机。 |
| `host_ip` | 主机的实际 IPv4 地址，供本机从机登记连接使用；示例地址使用前须替换。 |
| `last_dialogue` | 最近使用的 LLM 对话记录，初始为 `null`，选择会话后自动保存，用于返回上次对话。 |

</details>

安卓管理界面的对应字段为 `on_host`（是否在主机设备上）、`host_ip`、`last_llm_provider`（最近的 LLM 后端）、`last_llm_session`（最近的会话 ID），由界面自动保存。

安卓实际配置路径：

```text
/storage/emulated/0/Android/data/com.cloudpc.androidslave/files/slave.json
/storage/emulated/0/Android/data/com.cloudpc.management/files/management.json
```

从机共享目录通常为前者 `files/shared/`。安卓受应用沙箱限制，不能理解成可读取手机所有文件。配置可能含 WireGuard 私钥，不要放进共享目录或公开发送。

## 4. 管理界面日常操作

1. 启动主机和所在设备从机，再打开管理界面并连接主机。
2. 在首页添加目标从机 IP/端口，选择对应从机查看截图；可旋转截图。截图不是连续视频。
3. 在“LLM 对话”选择 Codex、DSH 或 OpenCode，再继续已有会话或新建会话。登录、模型等须由用户预先在主机配置。
4. SEND 提交一次；运行中再次提交按后端适配规则排队，STOP 请求停止。Working 状态来自 API 事件与运行时间，不代表展示模型全部内部思考。
5. 查看主机运行状态，确认哪个 IP 执行了什么操作及其结果。

正常从机有限次重连结束后可手动连接；管理界面所在从机有单独的连接维持策略。同一时刻仅一个活动管理界面。

## 5. 开发者接口（可选）

本节供脚本和程序集成使用，普通用户通过管理界面操作即可。以下命令在主机本机终端运行，接口地址为 `http://127.0.0.1:19527`；网络边界见“架构与网络”。

```bash
curl -sS http://127.0.0.1:19527/health
curl -sS http://127.0.0.1:19527/api/slaves
```

从返回列表选出目标 `id`，再设置变量：

```bash
SLAVE_ID='替换为实际从机id'
```

ID 不是 IP，也不保证重启后不变。下列接口统一经主机转发：

下表描述当前源码实现，不代表旧发布包具备相同能力：

| 接口 | Windows 从机 | Linux 从机 | 安卓从机 |
| --- | --- | --- | --- |
| POST `/api/<id>/connect` | 支持，主机发起连接 | 支持，主机发起连接 | 支持，主机发起连接 |
| GET `/api/<id>/list?path=...` | 支持 | 支持 | 支持，仅应用可访问目录 |
| GET `/api/<id>/download?path=...` | 支持 | 支持 | 支持，仅应用可访问目录 |
| POST `/api/<id>/exec` | 支持，受任务策略限制 | 支持，限可用系统命令 | 不支持 |
| POST `/api/<id>/rpc` | 支持 `file.list`、`file.stat` | 支持 `file.list`、`file.stat` | 未声明此能力 |
| GET `/api/<id>/management_diag` | 未实现该诊断消息 | 未实现该诊断消息 | 支持，尝试汇集管理界面日志 |
| POST `/api/cloud/screen/capture` | 支持，需交互桌面 | 当前后端不支持 | 当前未实现远程截图回包 |
| POST `/api/<id>/upload` | 未实现 HTTP 路由 | 未实现 HTTP 路由 | 未实现 HTTP 路由 |
| POST `/api/<id>/delete`、`mkdir` | 旧路由仍在，当前从机禁用对应变更消息 | 同左 | 不支持对应旧消息 |

- 下载返回 JSON 中的 `text` 或 `base64`，不是直接返回文件字节。
- 任务正文为 `{"cmd":"whoami","cwd":""}`。
- RPC 正文为 `{"operation":"file.list","payload":{"path":"/"}}`；操作范围以从机声明的能力为准。
- 文件写入/创建/删除/重命名使用专用 CLDC `TYPE_FILE_MUTATE`（`0x0108`）协议并接受路径策略校验，不通过上述旧变更路由。

手动发起连接：

```bash
curl -sS -X POST "http://127.0.0.1:19527/api/$SLAVE_ID/connect"
```

读取 Windows 从机目录：

```bash
curl -sS -G "http://127.0.0.1:19527/api/$SLAVE_ID/list" --data-urlencode 'path=G:/shared'
```

读取文本文件：

```bash
curl -sS -G "http://127.0.0.1:19527/api/$SLAVE_ID/download" --data-urlencode 'path=G:/shared/example.txt'
```

执行只读命令：

```bash
curl -sS -X POST "http://127.0.0.1:19527/api/$SLAVE_ID/exec" \
  -H 'Content-Type: application/json' \
  -d '{"cmd":"whoami","cwd":""}'
```

返回包括 `task_id`、`exit_code`、`stdout`、`stderr`。当前 HTTP 任务收集等待上限约 60 秒；超时不表示远端任务已终止，不要据此重复执行有副作用的命令。

注意：当前 `exec` 实现按空白拆分命令，不是完整 shell 解析器。不要直接套用含空格路径、复杂引号、管道、重定向的终端命令；复杂任务优先调用部署好的脚本并使用无空格路径。PowerShell 还可能受到只读策略限制。


## 6. 日志与常见问题

日志属于产生它的程序，默认保存在该程序运行的设备上，不会因为管理界面正在查看该设备就自动保存到管理界面所在设备。

| 日志来源 | 所在设备 | 查看位置与内容 |
| --- | --- | --- |
| Windows/Linux 从机程序 | 运行该从机程序的 Windows/Linux 设备 | 该设备配置的共享根目录下 `slave.log`，记录从机连接、请求处理及异常。 |
| 安卓从机程序 | 运行该从机 APK 的安卓设备 | 该设备从机共享目录下 `slave_runtime.log`，上一份轮转日志为 `slave_runtime.previous.log`。 |
| 安卓管理界面程序 | 运行管理界面 APK 的安卓设备 | 通过本机从机诊断可将管理界面日志汇集到该安卓设备的从机共享目录，文件名为 `management_ui.log`；不是目标从机或主机的日志。 |
| 主机程序运行日志 | 运行主机程序的 Windows/Linux 设备 | 输出到该设备的启动终端；如启动脚本配置了重定向，则保存在脚本指定的本机文件中。 |
| 主机维护的简要操作记录 | 由主机设备上的主机程序维护 | 管理界面向主机请求后显示，用于查看哪个 IP 执行了什么操作及结果；无需查询各从机日志。 |

排查顺序：

1. `/health` 不通：检查主机启动和 19527 占用。
2. 从机离线：检查 IP、从机启动、9527、防火墙/VPN；有限重连结束后手动连接。
3. 管理界面等待/超时：先检查本机从机或 Binder，再检查主从连接，不应把远程 19527 当作管理界面直连地址。
4. 转发失败：检查目标程序是否运行、监听地址与端口是否正确。
5. 转发成功但业务未生效：检查目标程序返回的业务结果和日志；传输成功不等于业务成功。

### FAQ

**管理界面安装在哪台机器？**  
可以安装在主机设备，也可以安装在从机设备。前者访问本机主机；后者需要本机从机运行并连接主机。

**能否只运行主机、不运行本机从机？**  
可以。主机设备不需要为了管理远程从机再启动本机从机；只有需要被控制的设备才需要从机。

**截图为什么不是视频？**  
当前截图页定时请求静态图片，不是视频流。实际可用性受平台后端限制，见接口能力矩阵。

**端口被占用怎么办？**  
先检查是否重复启动程序，关闭不需要的旧实例，不要随意结束未知进程。修改从机 `slave.json.port` 后，需同步修改主机中的从机地址。

**DSH 显示找不到文件怎么办？**  
检查主机 `host.json.llm.dsh.start_command` 与主机进程的 PATH。NVM 等安装方式可使用 Node 和 DSH 入口脚本的绝对路径，避免桌面启动环境找不到命令。

## 7. 开发与发布

源码与安装包分开管理：Git 仓库保存源码、构建脚本、文档和示例配置，EXE/APK/ZIP 通过 Releases 发布。

个人配置、私钥、访问令牌、日志及构建缓存不应提交。项目提供了 `.gitignore`，但提交前仍应检查暂存内容；密钥一旦进入提交历史，需要更换密钥并处理历史。

发布安装包应从已审查的源码构建，并使用自己的发布签名。

### 许可证

本项目自有代码与文档采用 [MIT 许可证](LICENSE)，版权署名为 `Cloud PC contributors`。

第三方组件与版本见 [第三方声明](THIRD_PARTY_NOTICES.md)，安卓安装包发布前执行 [第三方发布检查](docs/THIRD_PARTY_RELEASE.md)。

第三方代码与依赖不因本项目采用 MIT 而改变许可证，仍遵循各自的版权声明和许可条款。安卓 WireGuard 客户端/库的 Apache-2.0 声明不代表其所有原生依赖均采用 Apache-2.0。发布 APK 等安装包前，需按实际包含的组件核对许可证、通知及适用的对应源码提供义务；仓库根目录的 MIT 许可证不能替代这些要求。

### 构建限制

`host_program/package_llm_release.py` 是特定部署环境下的辅助工具，其中包含固定路径、日期和外部模块依赖，不是通用的独立构建入口。使用前需适配自己的环境。

### 测试

在仓库父目录执行：

```bash
python3 -m unittest discover -s cloud_pc/management_ui/tests -v
python3 -m unittest discover -s cloud_pc/desktop_version/tests -v
```
