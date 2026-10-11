# 云电脑独立管理界面

## 当前发布（2026-10-09 同步版）

最新配置入口改为程序同目录 `management.json`，角色、两个从机槽位和上次对话都写入同一个文件。Windows 在 EXE 旁，Linux 在启动脚本旁；不再使用用户目录的两份配置。详见 [配置规则](../shared_components/design/CONFIGURATION.md)。安卓对应管理界面为 1.1.5，从机为 0.5.23；下文早期版本号仅为变更记录。

Windows Tk、Linux GTK 和安卓独立管理界面均使用主机本地 LLM API 模块，支持 Codex/DSH/OpenCode、继续/新建会话、“返回上次对话”、公开思考摘要及工具过程、Working 计时。提交结果带编号显示在 Working 下方，轮询不会覆盖，不再为对话页额外预留底部大段空白。历史滚动位置保持原策略。

Windows EXE 在 `../windows_version/20261009/dist/`，Linux 可执行源码与启动脚本在 `../linux_version/dist/20261009/`；安卓管理界面 1.1.4、安卓从机 0.5.22。管理界面仅访问本机主机19527或本机从机19528，跨设备仍由云电脑连接处理。模型/认证由用户预先配置，API 服务由主机按需启动，不新增对外 LLM 端口。当前功能及限制见 [LLM_API.md](../host_program/LLM_API.md)。

## 以下为 2026-10-07 桌面转发版的历史记录（非当前 LLM 实现）

首页仅三个业务入口：从机界面1、从机界面2、主机 Codex 界面。Linux GTK3保留，新增Windows Tk独立程序及[独立安卓管理界面](../management_android/README.md)。不再计划移植ALXR，也不增加长期断线重连。

## Windows版

2026-10-07已在Windows从机实际构建单文件程序：`G:\eu5_project\CloudManagement\dist\CloudManagement.exe`（Python3.8.20、PyInstaller6.21.0）。Tk首页及三个页面构造/关闭测试通过，测试采用模拟主机，不注入真实SEND/ESC。真机输入、历史滚动及截图交互仍需用户验收。

源码入口 `python -m cloud_pc.management_ui.windows_main`，依赖Python/Tk和Pillow。在Windows执行build_windows.bat可生成单文件CloudManagement.exe，独立于主机和从机进程。

菜单“连接设置”选择管理界面所在设备（从机或主机），填写两个从机IP作为主机管理目标。界面在从机设备时仅访问本机从机127.0.0.1:19528，再沿主机已建立的CLDC会话取得信息；在主机设备时仅访问本机主机127.0.0.1:19527。配置保存为用户目录.cloud-pc-management.json，不需要远程主机地址或令牌，不扫描或直连目标从机。

上一轮新增远程19527管理入口及令牌参数已撤销。主机19527、从机本机管理19528与适配器19627均只监听127.0.0.1。管理界面不建立跨设备连接；跨设备消息由云电脑主从CLDC会话传递。

安卓须同时更新从机0.5.13与独立管理界面1.0.1。Windows管理桥源码位于shared_components/slave/management_bridge.py；部署基于Windows原源码仅添加管理桥，不覆盖音频/数字人业务文件。

## 启动

```sh
/home/qf/file/云电脑管理界面.sh
```

启动脚本检查云电脑主机19527，未运行时启动 /home/qf/file/启动云电脑主机.sh，然后打开界面。不会启动、重启或引用 digital_human 服务、ASR、声纹模型。

主机入口为 python3 -m cloud_pc.host_program.main --headless；代码复用 cloud_pc/desktop_version/host。默认服务 http://127.0.0.1:19527，与已有8765服务分开，不改变原服务。

第一从机默认已知 Windows IP 192.168.4.20。第二槽位在 config.json 留空，不猜测头显当前IP。配置第二设备时，需要在主机通过 --slave IP:9527 登记，并在管理界面的配置或 --slave2 IP 中绑定相同地址。界面不会根据任意输入擅自连接未知从机。

## 行为

- 从机截图通过主机请求，每秒发起一次；上一请求未完成时不堆积。未配置、离线及失败时明确提示，旧截图显示为过期。不是视频流，尚未实现通用远程键鼠。
- Codex页每秒获取文字，顶部输入框、SEND/STOP、历史滚动。默认最新内容，用户浏览历史时保留滚动位置。
- SEND由独立适配器串行执行并分配编号；相同请求ID与正文在最近128条缓存内不重复注入。主机只转发。成功表示调用了一次输入与Enter操作，不代表Codex已消费或回复；失败保留文字，不超时自动重发。
- STOP的忙碌判断与每阶段单次ESC限制由适配器负责；主机不判断Codex状态。空闲不发送、不退出会话，去重不跨适配器进程重启保存。
- 适配器在被请求读取时更新最近约500行、最多约64K字符的观测文本；无后台采集线程，不是完整历史会话档案。

主机当前会话适配器位于 cloud_pc/host_program/current_session.py，复用安卓转发既有的Linux X11、AT-SPI和窗口输入辅助逻辑，不调用任何LLM API。当前目标仍是现有“创建 AR 窗口程序”GNOME终端；其他终端后端尚未适配。

安卓当前窗口通道已迁入云电脑主机（2026-10-07）。启动主机参数 --current-slave 10.0.0.7:9527 由主机建立和维持连接、发送文字、接收SEND/STOP并返回状态，不再运行独立安卓网络转发脚本。current_window_relay.py 的旧网络入口已停用，只保留本地辅助函数导入兼容；本地Codex适配器位于 host_program/codex_adapter.py。

实际流程：安卓管理界面 → 安卓从机9527 → 云电脑主机 → 独立Codex适配器127.0.0.1:19627 → Codex。19527是主机管理HTTP接口，不是安卓TCP连接的目标；TCP连接仍由主机主动发起。

安卓0.5.12的页面onResume开启每秒读取请求，onPause停止并清除尚未发送的读取请求。读取事件为host_current_text_read；SEND/STOP也由页面事件触发。主机只取从机事件队列并转发收到的操作，不自行生成Codex读取请求。桌面页面同样通过主机19527请求适配器。旧安卓版本没有读取事件，不提供主机自主读取的兜底，需更新APK。

/home/qf/file/主机Codex界面.sh 会检查Codex、启动主机及独立适配器。加 --no-adapter 则不启动适配器。仅启动云电脑主机不会隐式启动适配器；未运行时请求返回明确错误。

独立启动：/home/qf/file/启动Codex适配器.sh（前台，Ctrl+C结束）。主机19527和适配器19627均仅监听本机。适配器不连接任何从机、不调用LLM API。可用19627/health的read_requests计数核对是否发生读取；健康检查本身不读Codex。

## 接口与安全

- GET /api/management/current：文字、修订号、时间、工作状态、最近提交结果。
- POST /api/management/current/send：request_id、text。
- POST /api/management/current/stop：request_id。
- POST /api/cloud/screen/capture：slave_host、slave_port，必须匹配主机已登记的唯一从机。

主机仅监听回环地址；没有开放公网。远程界面需受保护隧道，尚无完整远程认证。写接口拒绝浏览器Origin请求，但这不是完整身份认证。管理界面不直连从机、不自行执行键盘注入。截图当前使用旧CLDC请求响应，未实现统一截图/音频订阅及头显设备门户兜底。

通过主机Codex界面.sh启动时，日志在 /home/qf/file/codex界面运行记录/云电脑主机.log、Codex适配器.log。适配器记录请求ID、编号、阶段和字节数，不记输入正文；适配器重启后缓存清空，不依赖跨重启去重。

## 验证

```sh
python3 -m unittest discover -s cloud_pc/management_ui/tests -v
python3 -m cloud_pc.management_ui.main --smoke-test
```

13项测试覆盖原10项及本机HTTP→从机请求队列→主机处理→按编号返回、断线/超时不重试、禁止远程API地址。模拟输入，不注入真实文字/ESC。安卓从机0.5.13、管理界面1.0.1已构建与验签，真机更新后待验收。
# LLM 对话界面更新（2026-10-09）

原“主机Codex界面”改为“LLM对话界面”，Linux GTK与Windows Tk均支持三后端选择、主机会话列表、继续/新建、顶部输入、SEND/STOP。管理界面只访问同设备主机/从机，后端API由主机内置模块执行，配置由用户在主机预先完成。设计及限制见[LLM接口说明](../host_program/LLM_API.md)。下面保留早期实现记录。

# 对话预览与截图旋转（2026-10-09）

Windows/Linux 对话长消息默认约四行正文加省略号，使用“展开全部／收起长内容”切换，完整原文保留。截图页支持90°旋转，刷新时保持旋转方向，返回后重置。安卓管理1.1.8支持逐段展开／收起。本轮只更新管理界面，无需重启主机或更新安卓从机。

