# 云电脑 Android 从机（Android 16）

## 0.5.24：WireGuard 配置手动填写

首次安装的 WireGuard 配置为空，APK 不再嵌入私人配置或密钥。覆盖升级保留 slave.json 中已保存的配置。输入框缩小至 3–4 行，长内容在框内滚动；移除“保存配置”按钮，点击“启动 WireGuard”自动保存后请求 VPN 授权。点击“启动从机”仍会保存当前设置。以下旧版本的内置配置描述仅为历史记录。

## 0.5.12：管理界面主动请求，适配器独立

“打开当前窗口”页面每秒发起host_current_text_read；离开页面停止并清除尚未发送的读取请求。SEND/STOP仍由用户点击发起。主机只从从机事件队列接收并转发，操作由独立127.0.0.1:19627 Codex适配器完成；主机19527本身不读终端、不注入键盘，也不自动启动适配器。旧版本不具备读取事件，需覆盖更新。0.5.12与0.5.11签名相同。

APK：dist/cloud-android-slave-0.5.12-debug.apk。下载仅限WireGuard手机10.0.0.7：http://10.0.0.2:18765/cloud-android-slave-0.5.12-debug.apk。APK仍含私人WireGuard配置，不允许公开分发。

此工程将 WireGuard 官方 Android `tunnel` 库嵌入从机应用。安装的是**一个 APK**；WireGuard VPN、CLDC 从机与“当前窗口”页面共用同一应用和隧道。库版本 `1.0.20260102`，由 Gradle 从 Maven Central 获取。用户提供的 `G:\eu5_project\cloud_pc\wireguard-android-master` 是上游完整应用源码；这里复用其官方发布的 tunnel 模块，不复制或改写上游源码。

## 当前能力

- 用户粘贴标准 WireGuard `.conf`，同意 Android VPN 授权后启动隧道；前台通知显示虚拟 IP 和状态。
- 在配置的 WireGuard IPv4 地址监听 CLDC TCP 9527。主机仍经常驻主机端程序访问从机，协议帧与 Windows 从机一致。0.5.10 的 TCP 9541 接收 HoloLens 关节 CSV 和摄像头 MP4，当前入口只接受手机 `wlan0` 热点 /24 网段内的来源、指定文件名、单文件最多 64 MiB；它不是通用 CLDC 或命令端口。
- 支持握手、心跳、列目录、文件信息、下载，以及受限的 `TYPE_FILE_MUTATE`（创建目录、删除空目录/文件、重命名、单块 ≤1 MiB 写入）。文件只在本应用共享目录内：`Android/data/com.cloudpc.androidslave/files/shared`。
- 不支持 `TYPE_EXEC`、UE/MCP、屏幕/麦克风快照和 AR 本机转发；收到这些请求会明确返回 `UNSUPPORTED_ON_ANDROID`。不会把 Android `shell` 伪装成 Windows 命令执行。`0.5.3` 新增的 `TYPE_DH_BRIDGE_CALL` 只接受下述 HoloLens 设备门户动作，并非通用命令执行或 HTTP 代理。
- “当前窗口”页面通过 GNOME Terminal 的 AT-SPI 无障碍文本接口读取当前 Codex 终端文字，每秒更新一次，不做截图或 OCR。主机中继合并已观察到的终端画面，保留最近 500 行、最多约 63,000 字符；手机上的文字区有常显滚动条。历史从中继启动后开始累积，重启中继会清空，不读取启动前的完整 Codex 会话记录。
- 输入框独占顶部一整行，SEND 和 STOP 在它下方，避免窄屏按钮挤没输入框，也避免被系统输入法遮挡。用户触摸或停留在历史位置时，文字区暂不刷新，回到底部才追上新文字。输入法确认仅保存草稿；点 SEND 才经 CLDC 事件交给 Linux 主机，由主机定位当前 Codex 终端、输入文字并按 Enter。STOP 由主机核对 Codex 正在工作后向该终端发送一次 Esc；同一轮工作重复点 STOP 不再发 Esc，空闲时也不发送。桌面锁定、目标窗口不存在或输入不合法时会拒绝操作并显示原因。
- 每次点击 SEND 分配 `会话号-序号`，包括被拒绝的点击。页面显示编号；Android Logcat 与 Linux 转发日志按同一编号记录 `queued`、`dequeued_for_poll`、`received`、`submitted/failed`、`host_status` 等阶段，仅记录字节数，不记录正文。只读查看手机最近 100 条阶段记录：`python3 cloud_pc/android_slave/current_window_diagnostics.py`。**这只是诊断，不做事件确认后删除或超时重发**；若在取走队列后断线，事件仍可能丢失。
- 中文等非 ASCII 输入在 Linux 主机端使用临时剪贴板粘贴。主机先等待 GTK 剪贴板所有权生效，再按粘贴快捷键；每个被接受的 SEND 向目标终端输入一次文字并发送一次 Enter，不再因终端快照中有旧草稿或无障碍文字暂未更新而跳过 Enter。回执只表示输入及 Enter 已投递到 X11，不声称 Codex 已处理该消息。原剪贴板为空则粘贴后恢复为空，原有文本则恢复原文本；非文本剪贴板无法无损保存时拒绝覆盖。该逻辑在主机转发器中，更新它不需要重装手机 APK。

## 构建

需要 JDK 17、Android SDK 36、Build Tools 36 和 Gradle 8.11.1（或等价 Android Studio）。在本目录执行：

```bash
gradle :app:assembleDebug
```

构建任务从 `/home/qf/桌面/wg_friend5.conf` 读取配置，复制进**生成的 APK assets**；源码目录中不保存 `.conf`。输出 `app/build/outputs/apk/debug/app-debug.apk`。Debug APK 仅供测试，正式分发应使用自己的密钥构建 release 并妥善保存签名密钥。

**安全提示：APK 内置的 WireGuard 私钥可以被提取。** 此 APK 只应给持有人和可信设备安装；若 APK 外泄，必须在 WireGuard 服务端撤销 `wg_friend5` peer 并重新发放密钥。不要把 APK 公开上传，也不要把 `.conf` 提交到源码仓库。

## 首次运行

1. 在手机安装 APK，打开应用。
2. 应用默认填入构建时的 `wg_friend5.conf`（当前虚拟地址 `10.0.0.7`）；需要时可临时修改。`[Interface]` 须有 IPv4 Address；`[Peer]` 须包含主机侧服务的 Endpoint、PublicKey 和可到达主机的 AllowedIPs。
3. 点击“启动 WireGuard 与从机”，批准系统 VPN 提示。Android 16 前台服务通知要允许显示。
4. 在常驻主机端配置该手机的 WireGuard IP 和端口 `9527`，再做 CLDC 握手与列目录测试。主机不能用手机 Wi-Fi IP 直连此监听。

从 Linux 主机先做只读探针：`python3 cloud_pc/android_slave/probe.py <手机 WireGuard IP>`。握手和根目录列表成功后，再接入常驻主机端。

Linux 主机的 WireGuard 地址 `10.0.0.2` 与手机 `10.0.0.7` 互通后，启动文字/输入事件转发：

```bash
python3 cloud_pc/android_slave/current_window_relay.py --host 10.0.0.7
```

该进程只作为 CLDC 客户端连接手机 9527，不在 Linux 主机新开端口。它复用 HoloLens “当前界面”的窗口身份、锁屏检查与 SEND 注入函数，但显示数据来自终端 AT-SPI 文本而非图片。手机端点击“打开当前窗口”即可看到文字和输入栏。主机进程需要在有当前 X11/AT-SPI 会话的环境中运行；退出转发进程后手机只保留最后一版文字，SEND 事件将等待下一次连接。

私钥在 APK assets 中，运行时不写日志。当前协议本身没有额外身份认证，因此只应让可信设备加入这个 WireGuard 网络，并用 WireGuard peer / 路由限制可访问者。服务由用户显式启动，停止或系统杀进程后需要重新启动；本版不做开机自启。

## 通过手机热点部署 HoloLens（0.5.5）

当 HoloLens 连接手机热点、Windows 构建机无法直接访问头显时，使用现有云电脑主机端 API `/api/cloud/bridge/call` 转发到手机 `10.0.0.7:9527`。手机仅接受 `portal_probe`、`portal_stage_begin`、`portal_stage_chunk`、`portal_stage_install`、`portal_status` 和只读 `portal_inspect`：探测设备门户证书、分块暂存最多 64 MiB 的 MSIX、校验整体 SHA-256、提交安装，并查询安装状态及 ALXR 实际版本。不提供任意 URL、端口、HTTP 方法或 shell 转发；目标 IP 当前限定在手机热点 `10.169.56.0/24`。每块最多 512 KiB，安装在后台执行，避免主机端 CLDC 请求超时。

先在手机上升级安装 [cloud-android-slave-0.5.9-debug.apk](dist/cloud-android-slave-0.5.9-debug.apk)，重新打开并启动 WireGuard 与从机。该 APK 与 0.5.4 使用同一 Debug 签名，版本号已递增；**APK 内含 `wg_friend5.conf` 的私钥，只能私下传给这台手机**。旧版不认识 `portal_inspect`，主机探针会返回 `UNSUPPORTED_ON_ANDROID`。

安装新 APK 后，主机执行：

```bash
python3 cloud_pc/android_slave/hololens_portal_relay.py probe --device-ip 10.169.56.66
python3 cloud_pc/android_slave/hololens_portal_relay.py inspect --device-ip 10.169.56.66
python3 cloud_pc/android_slave/hololens_portal_relay.py install --device-ip 10.169.56.66
```

第三条命令通过常驻主机端从 Windows 从机下载已签名的 ALXR `1.82.1.40` MSIX，分块交给手机，再由手机访问热点内 HoloLens 的设备门户。主机使用现有 `HOLOLENS_PORTAL_USER/PASSWORD`，凭证仅在此次 CLDC/WireGuard 请求和手机内存中传递，不写入 APK、暂存文件或日志。`probe` 返回头显证书 SHA-256；`inspect` 和 `install` 在同一次运行重新探测并使用该指纹固定 TLS 连接。首次指纹属于 TOFU，应与设备证书独立核对；如已有可信指纹，传 `--cert-sha256` 强制比对。MSIX 暂存在手机应用私有缓存，任务结束后删除。

设备门户 POST `/api/app/packagemanager/package` 后，手机会继续查询 `/api/app/packagemanager/packages`；只有看到目标版本才返回 `verified`。0.5.4 修正了门户将 `Version` 返回为对象时的解析，并新增 `inspect` 读取 `/api/app/packagemanager/state`；0.5.5 增加安装列表字段摘要，定位门户报告成功但找不到 ALXR 包身份的问题。`submitted_unverified` 或 `failed` 都不能视为安装成功。0.5.5 已通过 Android APK 编译，仍需手机安装后真机验证。

## 与 Windows 从机的差异

Android 的沙箱、VPN 授权及前台服务限制与 Windows 不同。特别是主机现有的 Windows 路径（例如 `G:\`）不能在手机使用；根目录 `/` 映射为上述应用共享目录。后续若需截图、音频或命令执行，应逐项定义 Android 权限、用户同意和安全策略。

## ALXR 关节 CSV 直传（0.5.7）

ALXR 停止录制后主动连手机热点地址 `10.169.56.128:9541`（失败时尝试手机 WireGuard 地址 `10.0.0.7:9541`）。该地址由 `phone_network` 查询手机接口确认；不要假定热点网关是 `.1`。安卓从机把完整 CSV 原子保存到自身 `shared/ar_hand_joints/`，回复 `OKAY` 后头显显示 `SAVED ON PHONE`；原始文件仍留在 ALXR `LocalState`。手机从机无需也不能通过 HoloLens 本机从机转发。

Linux 主机上的 `hololens-hand-sync.service` 每 3 秒通过常驻主机端的 `/api/cloud/files/list` / `download` 取回新文件，保存到 `/home/qf/桌面/HoloLens手势识别/hand_joints/`；不会删除手机或头显原件。`SAVED ON PHONE` 只证明手机落盘，最终到达主机以同步服务日志中的 `saved` 和主机文件为准。

安装包：[0.5.9 Debug APK](dist/cloud-android-slave-0.5.9-debug.apk)。与 0.5.5 使用同一签名；APK 内含 WireGuard 私钥，仅在可信设备与私有链路传送。9541 目前靠热点隔离及源 IP 限制，尚无应用层认证，不应用于不可信网络。检查主机同步：`systemctl --user status hololens-hand-sync.service`、`journalctl --user -u hololens-hand-sync.service -n 30`。

0.5.8 起另接收 ALXR 发往手机热点地址 `10.169.56.128:9999/udp` 的诊断日志；0.5.9 当前只接受来源 `10.169.56.66`。保存为手机从机 `shared/ar_diagnostics/alxr-client.log`，超过 16 MiB 时轮换为 `alxr-client.previous.log`。主机通过常驻控制服务的 `/api/cloud/files/download` 读取，不再依赖旧 Windows 从机的 UDP 9999 监听。可用 `phone_network` 只读桥接动作核查手机的实际 IPv4 地址。当前上传目标和来源白名单仍为静态配置，热点地址再次变化时需要同步更新两端。

## ALXR 摄像头视频与关节数据同步（0.5.10）

**安装使用 0.5.11 修正版**：[APK](dist/cloud-android-slave-0.5.11-debug.apk)。0.5.10 只检查 `wlan0`，手机同时连接 Wi-Fi、开启热点时，热点实际使用 `wlan2`，导致设备门户桥、视频/CSV 与日志入口拒绝头显。0.5.11 检查活动 `wlan*` / `ap*` 接口的实际 IPv4 前缀（仅接受 /24 到 /30），支持当前 `wlan2` 的 `10.169.56.0/24`；隧道和移动数据接口不纳入来源范围。

手机先升级到 [0.5.10 Debug APK](dist/cloud-android-slave-0.5.10-debug.apk)，再部署 ALXR 1.82.1.45。该 APK 内含 WireGuard 私钥，仅能通过可信私有链路交给该手机。9541 新增 `ARVID001` 上传类型，严格接受 `hand_video_<数字>.mp4`，原子保存于 `shared/ar_hand_video/`；原有 `ARCSV001`、`shared/ar_hand_joints/` 不变。

热点 DHCP 可能改变手机与头显地址；0.5.10 的手机接收端和设备门户桥改按当前 `wlan0` /24 网段限制来源/目标，而不是写死旧 `10.169.56.*` 网段。ALXR 1.82.1.45 尝试当前观测到的手机 `10.42.0.173`、旧 `10.169.56.128`，最后尝试 WireGuard `10.0.0.7`。如果手机热点地址再次改变，ALXR 上传目标仍需更新；本版尚未实现自动服务发现。

ALXR 控制界面的 `VIDEO` 一次启动 HoloLens 彩色摄像头画面（720p、无麦克风）与关节 CSV 录制，`STOP` 停止两者并上传。两个文件使用相同时间戳名称；CSV 增加 `unix_time_ms` 与 `video_start_unix_ms` 列以供时间对齐。录制失败、上传失败会留在 ALXR `LocalState` 并记录诊断日志；头显状态文字会区分上传中、手机已保存与失败。此处录制的是摄像头画面，不是混合现实合成画面或 UE/窗口叠加层。

Linux 主机上的 `hololens-video-sync.service` 每 3 秒经常驻主机服务从手机取回 MP4，保存到 `/home/qf/桌面/HoloLens手势识别/hand_video/`，不删除源文件。用 `systemctl --user status hololens-video-sync.service` 与 `journalctl --user -u hololens-video-sync.service -n 30` 检查同步；到达主机后以 `ffprobe` 验证可解码性。MP4 最大 64 MiB；较长录制若超限，手机会拒绝上传，但 ALXR 本地副本保留。
# 当前窗口通信更新（2026-10-07）

安卓9527的文字/SEND/STOP收发现由云电脑主机直接管理，不再单独运行 current_window_relay.py。启动 /home/qf/file/主机Codex界面.sh，或 python3 -m cloud_pc.host_program.main --headless --current-slave 10.0.0.7:9527。主机管理API为127.0.0.1:19527，本地Codex适配器无外部连接。以下旧版本记录中的独立转发启动命令不再适用于当前版本。
# 0.5.13 本机独立管理界面桥

新增127.0.0.1:19528，仅接受同设备管理界面的current/send/stop/slaves/screenshot请求。请求沿主机已建立的CLDC会话0602/0682送主机处理，0606/0686按编号交回结果。无新增对外端口、主动回连或扫描，未确认操作不自动重发。握手能力management_bridge_v1供主机识别。

搭配独立管理界面1.0.1使用，两份APK都须更新，无需远程主机IP与令牌。不改WireGuard、录音或声纹配置。

下载：http://10.0.0.2:18765/cloud-android-slave-0.5.13-debug.apk
# 0.5.14 本机管理桥诊断

0.5.17：删除从机内置“打开当前窗口”功能（入口、Activity、CurrentWindowState及旧0603/0604消息处理）。Codex界面只由独立管理应用1.0.5提供；0602/0606管理请求队列与0605日志导出保留。从机不再保存Codex对话或生成旧窗口业务事件。以上旧版内置界面说明不再适用于当前版本。

当前版本为0.5.15，搭配管理界面1.0.3：同设备通信改为同签名权限保护的Binder Provider，删除Android回环19528监听。0605返回provider_ready、transport=binder与管理队列计数；旧local_management_probe参数仍可读取诊断，不再执行TCP自检。请求仍沿原CLDC连接交主机处理，不改WireGuard和音频。下载：http://10.0.0.2:18765/cloud-android-slave-0.5.15-debug.apk。两份APK均编译与签名验证通过，跨应用真机验收待更新后执行。以下0.5.14记录仅描述历史诊断方式。

记录management_diagnostics.json到从机公共shared目录，不含输入正文/密钥。主机通过原CLDC0605/0685读取accepted/parsed/queued/drained/delivered/expired/hosts与监听状态；local_management_probe启用从机进程自身127.0.0.1健康测试。不新增对外端口，不改变WireGuard配置。

下载：http://10.0.0.2:18765/cloud-android-slave-0.5.14-debug.apk
# 0.5.20 管理会话注册更新（2026-10-07）

配合管理界面1.0.8：从机代发首次激活/释放CLDC注册通知至已知主机19526；默认10.0.0.2，保存已连接主机来源IP。主机仅持续重连唯一活动管理从机，业务仍由主机主动建立9527连接。APK已编译，真机断线恢复待更新后验收。

私有下载：http://10.0.0.2:18765/cloud-android-slave-0.5.20-debug.apk（仅WireGuard手机10.0.0.7可下载，内含用户专用WireGuard配置，勿公开分发）。
# 0.5.23：独立 JSON 配置

应用文件目录生成 `slave.json`，启动后不再每次用 APK 内置 WireGuard 配置覆盖设置。首页显示路径并提供“保存配置”，名称、WireGuard 配置持久保存；JSON 中可设置监听 port、共享 root、host_ip 和 host_registration_port。WireGuard 与从机继续独立启停。配置内含私钥，不放入 shared 公共目录。覆盖安装保留文件，损坏 JSON 报错且不重置。

详见 [三模块配置规则](../shared_components/design/CONFIGURATION.md)。

# 0.5.22：WireGuard 与从机独立启停

首页分别提供“启动 WireGuard”“启动从机”“停止 WireGuard”“停止从机”。只有启动 WireGuard 才申请 VPN 授权；启动从机不读取 VPN 配置、不要求 VPN 授权。重复启动已运行模块不会重启或断开连接。一方失败或停止不清理另一方；两者均停止时退出常驻服务。

从机监听 `0.0.0.0:9527`，可先启动从机，再启动 VPN，或通过可信局域网使用；管理 Binder、文件、9541 录制上传及 9999 日志入口保留。停 VPN 会中断 VPN 路由上的连接，但不停止从机监听。常驻通知同时显示两者状态。CLDC 尚无独立应用层认证，监听范围包含局域网，仅应在可信网络启用，不要映射到公网。

APK：`dist/cloud-android-slave-0.5.22-debug.apk`，包含用户原有 WireGuard 私密配置，只通过受限下载服务发送。已编译，独立启停仍需手机安装后真机确认。

