# Android 本机管理 IPC 更新

从机 0.5.15 / 独立管理界面 1.0.3。

旧版本：从机进程内 TCP 自检成功，但用户执行跨应用测试后没有新增外部 accepted/queued 计数。尚未确定跨应用 TCP 超时的具体系统原因，不将其归因为所有 Android 禁止回环通信。

本次：删除 Android 本机19528监听，改为 ContentProvider.call，authority 为 com.cloudpc.androidslave.management。Manifest 签名权限与 call 中显式权限检查限制同签名调用者。管理请求在后台线程执行；大结果通过 ParcelFileDescriptor 管道返回，避免截图超过 Binder 事务大小。

不变：主机发起并维持 CLDC；0602/0682携带队列事件，0606/0686返回同请求编号的结果。SEND/STOP不超时重发；Windows同设备回环19528保持不变。WireGuard、音频、声纹均未修改。

已验证：两个APK离线构建成功，apksigner验证成功且同一SHA256签名；Python管理链路13项测试通过。这些不等同于安卓跨应用Binder真机验收。

待验收：用户更新两份APK并启动从机，打开管理界面本机健康检测，再打开主机Codex界面。经主机 management_diag 查看 transport=binder、accepted/queued/delivered，主机 health 查看 management_requests。未执行任何测试文字或ESC。
