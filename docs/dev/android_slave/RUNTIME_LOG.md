# 0.5.19 从机服务运行日志

公共目录：Android/data/com.cloudpc.androidslave/files/shared/slave_runtime.log。
每行JSON，记录时间、APK版本、PID、线程、事件、阶段及异常完整堆栈。1MiB轮转，另保留slave_runtime.previous.log。外部存储不可用时退到应用内部files/shared；手机“查看从机运行日志”显示实际路径与最近64KB，不依赖主机连接。

记录：页面/服务创建、START/STOP请求、配置解析完成（仅IP）、WireGuard状态、CLDC监听与握手、录制/ALXR日志端口启动、启动失败阶段、客户端断开与请求错误、清理、用户停止、服务销毁及正常打开页面后设置的未捕获异常处理器。

不记录配置正文、文件内容、正常输入或密钥；异常中的WireGuard格式Base64密钥及PrivateKey/PresharedKey字段脱敏。不逐条记录心跳/正常轮询。系统强杀无法保证调用onDestroy或异常处理器，日志不能代替Android系统全部崩溃日志。

主机连通后通过既有文件下载接口读取slave_runtime.log；0605及握手ACK新增app_version。启动失败导致9527关闭时，需手机页面本地查看日志。

修正CldcServer.close漏掉management.close：关闭本机桥、失败结束待处理请求、清除静态active；不改变自动重连策略或WireGuard启动参数。仍保留原启动失败时清理全部服务的行为，先以日志定位真实失败阶段。

下载：http://10.0.0.2:18765/cloud-android-slave-0.5.19-debug.apk。管理界面无需为本次日志修改升级（现行1.0.6）。
