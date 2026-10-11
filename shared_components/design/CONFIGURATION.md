# 三模块同目录 JSON 配置

## 规则

主机、从机、管理界面分别使用 `host.json`、`slave.json`、`management.json`。默认值在代码内；首次实际启动创建自身文件，后续启动只读现有配置。`--help` 不创建文件。修改 JSON 后重启对应程序生效，界面“保存”立即落盘。升级二进制不覆盖已有配置；JSON 格式或类型错误报错，不自动重置。目录不可写时报错，不能静默改存其他目录。

Windows 单文件 EXE：位于 EXE 旁，不是运行时解包目录，也不随启动工作目录变化。请把程序放在可写目录，不要直接放入普通用户无写权限的 Program Files。

Linux 发布版：位于三个启动脚本旁；脚本设置 `CLOUD_CONFIG_DIR`。源码运行默认在 `cloud_pc/` 根目录。相对共享目录按配置文件目录解析，而不是终端工作目录。

安卓：APK 安装目录只读，不能把 JSON 写进 APK 同目录。因此每个应用存于自身 `getExternalFilesDir(null)` 下（无外部目录时退回应用内部目录）。典型路径：

- 从机：`/storage/emulated/0/Android/data/com.cloudpc.androidslave/files/slave.json`
- 管理界面：`/storage/emulated/0/Android/data/com.cloudpc.management/files/management.json`

实际路径在从机首页、管理界面连接设置中显示。它们不在 `shared` 公共目录；从机配置含 WireGuard 私钥，禁止公开上传。APK 覆盖安装保留配置；卸载/清除应用数据会删除应用配置。首次升级从已有偏好迁移名称、管理角色/入口/最近会话；后续以 JSON 为准，不再读取偏好作为配置来源。

## host.json

`headless`、`slaves`（字符串数组，IP:端口）、`current_slaves`（旧界面用途）、`relay_grants`、`management_bind`、`management_port`、`management_interval`、`log_level`、`llm`。LLM 提供商配置原样嵌入 `llm` 对象，包括本机 API 地址、启动命令、工作目录、认证环境变量名。模型、登录及密钥仍由原生 LLM 程序保存，不拷贝进此文件。主机固定回环 HTTP 19527 的设计不变。

本机生产配置在 `/mnt/old-linux/cloud_pc/host.json`，保留 Windows 与 Android 的已知地址。`/home/qf/file/启动云电脑主机.sh` 不再把地址写死为命令行参数。

## slave.json

Windows/Linux：`name`、`port`、`root`、`log_level`。默认 root 为程序旁 `shared`，避免无配置时暴露整个用户主目录。命令行 `--root/--name/--port/--log-level` 若明确提供，会保存到 JSON，以后可无参数启动。可用 `--config` 显式指定配置路径。

安卓：`name`、`port`、`root`、`host_ip`、`host_registration_port`、`wireguard_config`。0.5.24 起 WireGuard 内置默认配置为空，APK 不包含私人配置；已有 JSON 配置覆盖升级保留。首页无单独保存按钮，点击启动 WireGuard 或启动从机时自动记住名称/WireGuard 配置；文件中的监听端口及共享根目录供下次启动从机读取。WireGuard 与从机仍独立启停，配置不会导致开应用就自动连接 VPN。

## management.json

Windows/Linux：`role`（host/slave）、`last_dialogue`（provider/session_id）。不再存从机 IP 或槽位，旧 `slaves` 字段忽略并在下次保存时清除。返回上次对话记录也存这里，不再额外生成 `.cloud-pc-last-dialogue.json`。Windows 连接设置只保存本机角色。

安卓：`on_host`、`last_llm_provider`、`last_llm_session`；升级时删除旧 `slave0/slave1`，不再保存从机 IP。

管理界面首页从主机读取完整动态从机列表（包括离线项），支持添加 IPv4/端口、移除（需确认），经原有从机 Binder/CLDC 或本机主机接口调用 `POST /api/management/slaves`。只有活动管理界面有权修改，主机保存到自身 `host.json.slaves` 并即时发起连接／停止连接。移除不删除从机文件；禁止移除当前管理界面所在从机，防止自断管理链路。Android 复用已有 `slaves` 操作，无需升级从机；Windows/Linux 本机从机桥新增此路由，管理界面位于从机上时需要包含该路由的从机版本。

## 不属于配置文件的运行数据

请求去重 SQLite、管理活动会话状态、日志及诊断快照属于运行数据，不是第四套用户配置。旧源码中的 `llm_config.json`、`management_ui/config.json` 是历史默认资源，不是新版主程序的活动配置；无需随程序更新覆盖用户文件。

## 验证范围

单元测试覆盖默认生成、已有值不覆盖、损坏文件保留、字段/端口校验、EXE 路径选择、三文件与上次会话共存。Linux GTK 离线烟测；安卓 APK 编译/签名验证。Windows 冻结程序实际启动验证单独记录于发布清单。安卓覆盖安装与可编辑目录仍需真机确认。
