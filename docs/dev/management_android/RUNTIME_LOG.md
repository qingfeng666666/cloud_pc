# 管理界面运行日志（1.0.4）

管理界面原始日志：
`/storage/emulated/0/Android/data/com.cloudpc.management/files/share/management_ui.log`。
每行JSON，含时间、版本、页面、请求编号、角色、操作路径、排队/开始/Binder调用/返回/结束、耗时、完整异常堆栈。Binder调用记录权限与Provider可见性。不记录输入正文、返回对话正文、截图或密钥。

超过256 KiB轮转为management_ui.previous.log，仅保留当前与上一份；外部存储不可用时退到应用内部files/share。首页显示实际BuildConfig版本。

从机0.5.16通过同签名保护的只读日志Provider独立读取管理界面日志，不依赖此前失败的“管理界面调用从机”方向。主机读取0605/0685诊断时，从机自动将当前日志镜像到已有云电脑公共共享目录：
`/storage/emulated/0/Android/data/com.cloudpc.androidslave/files/shared/management_ui.log`。
主机文件API看到的是根目录下management_ui.log。诊断返回同步字节数或management_ui_log_error；旧镜像可能存在，必须检查该错误与同步结果，不将旧文件视为最新。

此次需更新管理界面1.0.4与从机0.5.16。原管理队列、SEND/STOP、WireGuard与音频逻辑不变。构建及签名可本机验证，实际跨应用日志读取需更新后真机验收。
