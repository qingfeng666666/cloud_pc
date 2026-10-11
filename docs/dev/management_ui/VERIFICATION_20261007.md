# 独立Android / Windows管理界面验证 2026-10-07

## 本轮修正（当前有效设计）

安卓从机升级后已实际报告management_bridge_v1，处于online。独立管理页仍timeout；新增主机计数显示连续92轮成功取队列、0事件/0管理请求，故问题在独立界面请求进入本机桥之前，而非适配器执行。1.0.2将客户端改成显式无代理Socket、增加阶段错误和本机健康检测，等待真机验证；未确认系统代理是根因。主机仍仅监听回环19527。

1.0.2本机健康检查仍SocketTimeoutException：Java实际LocalManagementServer源码在桌面JVM17运行测试，HTTP健康响应、请求队列/结果回传、自探针均通过（org.json20240303，仅android.util.Log使用日志桩）。该测试不能证明安卓运行环境正常。新增0.5.14从机阶段计数/公共目录诊断JSON及CLDC只读自身探针，须更新后读取设备证据才能确认根因。未修改WireGuard路由或安全策略。

上一轮“管理界面直连远程主机”的实现偏离用户要求，已撤销。下文1.0.0及远程认证记录仅为历史，不代表当前路径。

当前：从机设备上的管理界面 → 同设备从机127.0.0.1:19528 → 已建立CLDC会话 → 主机；主机设备上的界面 → 同设备主机127.0.0.1:19527。主机19527仅回环监听，远程绑定和令牌参数移除，原令牌文件移到codex界面运行记录/已停用管理令牌.txt，不再使用。

Android从机0.5.13、独立管理界面1.0.1离线构建与验签通过。管理界面移除url/token设置及旧偏好，增加本机角色选择，默认从机。两份APK均须更新后真机验收。

Windows独立界面重新构建；本机桥HTTP及三个Tk页面构造/关闭烟测通过（WINDOWS_LOCAL_UI_SMOKE_OK）。从机新包基于Windows现有shared_components源码镜像，仅修改connection.py的管理桥挂接并新增management_bridge.py；未覆盖音频/数字人业务代码。新包CloudSlaveManagement.exe生成（56,173,228B），--help正常。

Windows自动切换从机后9527未恢复可达，主机有限重试耗尽；本轮不能标记Windows部署/真实转发验证完成。切换监督程序预先备份原EXE到G:\eu5_project\cloud_pc\cloud_slave.before_local_management_20261007.exe；部署日志在G:\eu5_project\CloudManagement\local_management_deploy.log。恢复后必须读取日志确认更新/回滚状态，不能推断已成功回滚。可能需检查冻结程序继承的PyInstaller环境，尚未取到日志，不能确认根因。

可在Windows重新启动已构建的新从机：G:\eu5_project\CloudManagement\slave_source\dist\CloudSlaveManagement.exe --root G:\eu5_project --name MyServer1。本地start_windows_slave.bat设置PYINSTALLER_RESET_ENVIRONMENT=1供新进程启动，尚未传到离线Windows。安卓端两份APK均可下载，但当前在线从机仍未报告management_bridge_v1，需要用户安装后验收。

13项管理测试通过：原10项 + 本机HTTP管理请求经事件队列/主机处理/按ID回传闭环、断线与超时不重试、禁止远程API地址。桌面主机8通过/1依赖缺失跳过。未注入真实SEND或ESC。

管理请求使用0602/0682事件队列中的host_management_request；主机0606/从机0686交付按ID结果。主机只在管理界面提交请求后调用适配器，按从机management_bridge_v1能力启用此通道；无从机/界面主动建立主机连接。

## 产物

- Android: management_android/dist/cloud-management-1.0.0-debug.apk；包名com.cloudpc.management；SDK36，Java17，Gradle8.11.1，离线构建成功，apksigner verify通过。不带WireGuard私钥/管理令牌，不覆盖安卓从机。
- Windows: G:\eu5_project\CloudManagement\dist\CloudManagement.exe；Python3.8.20/PyInstaller6.21.0/Pillow10.4.0单文件构建成功。源码镜像在该目录cloud_pc/management_ui。

## 验证

- 12项管理自动测试全部通过；新增无令牌401、非管理路由403、认证后按需读取、危险绑定地址与短令牌拒绝。
- 桌面主机测试8通过/1跳过（本地缺numpy的SlaveServer实测用例），不声称该跳过用例本轮通过。
- Windows Tk首页、两个截图页及Codex页构造/关闭，模拟主机后端：WINDOWS_UI_SMOKE_OK。测试未注入真实SEND或ESC。
- Windows从机访问10.0.0.2:19527/health返回401，证明网络可达、认证生效。
- 主机本机认证访问WireGuard管理入口/health成功；19527仍同时监听127.0.0.1，19627适配器未对外开放。
- 主机重启启用新入口后，192.168.4.20:9527与10.0.0.7:9527均恢复在线。
- 独立管理程序仅请求主机；主机转发适配器请求，不主动采集Codex文字。新程序没有从机监听或直连从机代码。

## 待真机验收与边界

安装Android APK并配置主机地址/管理令牌，测试截图、软键盘、历史滚动、SEND及STOP；Windows实际输入操作同样待验收。源码构造与HTTP测试不等同于真实输入成功。

本轮没有完成统一活动截图/音频订阅、全平台从机身份认证或全平台IP转接。长期断线重连和ALXR管理界面已根据用户要求移出范围。令牌HTTP入口仅在WireGuard等加密内网使用；不要开放公网。
