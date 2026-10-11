# 管理会话与专属重连保障

本轮实现：主机、Python从机、Windows/Linux管理界面源码、Android从机0.5.20、Android管理界面1.0.8。

管理界面 → 本机从机 Binder/127.0.0.1:19528 → 主机19526短CLDC注册 → 主机主动连接本机从机9527。主机设备上的管理界面直接调用本机19527注册，不用19526。

注册请求：12字节CLDC头（magic CLDC、version 1、type 0x0701、JSON长度，网络字节序），JSON字段operation=activate/release、session_id=32位小写十六进制、slave_port。回复type=0x0781。来源IP必须已经在主机从机配置里，端口也必须匹配；不接受任意目标转发、文件或执行命令。

管理业务由从机通过既有 CLDC 连接主动发送 0x0607 请求，主机用 0x0606 按请求 ID 返回结果；不轮询管理队列。payload 附加 _management_session，主机校验后移除，不交给后端适配器。本机 HTTP 请求用 X-Management-Session 头。主机发起连接建立，连接建立后双方均可主动发送普通消息；升级主机和从机以启用 management_push_v1。

只存在一个活动管理会话；新的显式激活使旧会话永久失效（主机保留最近1024个旧编号）。旧会话不得自动抢回，用户可以手动点“激活本管理界面”产生新编号。读取、SEND、STOP、截图和旧事件入口都校验会话。返回首页、手机切后台不释放；正常关闭尽力释放。意外断电/断网保留活动会话，不自动重发业务。

主机每5秒检查活动管理从机：已有连接任务不会重复启动；此前重连耗尽则恢复任务；连接重试间隔固定5秒，不受普通有限次数限制。接替或释放后旧从机恢复普通有限次策略，不主动切断其正在使用的连接。主机本机管理界面不指定远端保障对象。

主机参数：--management-bind（默认0.0.0.0，仅注册入口）、--management-port（默认19526）、--management-interval（默认5）、--management-state（默认用户.local/state/cloud_pc/management.json）。管理业务HTTP仍仅127.0.0.1:19527。Python从机首次主机地址通过CLOUD_MANAGEMENT_HOST/CLOUD_MANAGEMENT_PORT配置；Android默认10.0.0.2并持久保存真实主机会话来源地址。

安全边界：注册只使用已配置来源IP白名单，不宣称已经实现设备级认证；应在WireGuard/受控局域网使用，不直接暴露公网。state文件权限0600；日志/健康状态不泄露会话编号。

自动测试：test_management_session.py（接替、旧会话拒绝、释放、仅活动从机持续恢复、重启持久恢复、从机首次注册、超出普通次数、实际HTTP界面接替）；management_ui/tests/test_local_bridge.py包含注册→CLDC读取与一次SEND闭环。Android两份APK均成功编译，真实断线恢复待安装新版本后验证；Windows旧EXE需重打包，不会自动更新。
