# Cloud PC Windows 1.0.0

三个独立程序：`CloudHost.exe`、`CloudSlave.exe`、`CloudManagement.exe`。

- 主机：默认无窗口运行，负责建立和维持从机连接。首次启动生成同目录 `host.json`，设置 `slaves` 后重启。需要主机自带桌面窗口时设 `headless=false`；安装包已包含对应 Qt 库。
- 从机：首次启动生成同目录 `slave.json`。默认监听 TCP 9527，文件根目录为同目录 `shared`。需要修改共享范围时调整 `root` 后重启。
- 管理界面：首次启动生成同目录 `management.json`。选择所在设备角色，主机设备访问本机 19527，从机设备访问本机 19528；从机设备还需设置主机 IP。

不必在每台设备运行全部三个程序。不要重复启动主机占用相同端口。程序目录需可写，以便保存配置；升级时保留自己的 JSON，不用示例覆盖个人配置。

构建使用控制台子系统以保留 `--version` 和启动异常诊断输出；管理界面仍有独立图形窗口。

自有代码采用 MIT。保留随包 LICENSE、THIRD_PARTY_NOTICES.md、ThirdPartyLicenses.zip 和校验清单；Qt、NumPy、SciPy 等组件遵循各自许可证。许可文本收集不替代第三方适用的源码与其他再分发要求。

验证范围：EXE 产品版本、命令行版本、管理界面离线窗口创建，以及构建产物 SHA-256。完整跨设备操作应另作验收。Windows EXE 未配置 Authenticode 发布证书，系统可能提示未知发布者。
