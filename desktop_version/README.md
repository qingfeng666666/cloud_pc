# 桌面主机程序

## 独立服务启动

在本目录执行：

```sh
python -m host.main --headless --slave 192.168.4.20:9527
```

可以重复指定 --slave。无界面模式不需要 PySide6，主机仍主动连接从机、维持心跳和重连，并提供本地 HTTP API（127.0.0.1:19527）。Ctrl+C 停止服务。不加 --headless 时保留现有桌面界面。

此入口不同于当前 Linux 上常驻的 digital_human 主机服务（8765）；本次没有替换或重启该服务，也没有开放远程 API。

## 本次基础完善

- 将 Qt 导入移到界面启动分支，支持主机独立运行。
- 新增响应订阅机制，API 请求、任务输出和下载不再覆盖界面回调，结束或异常时清理订阅。
- 对缺少请求编号的同类 API 响应串行处理，降低并发串结果风险。
- 任务和下载超时明确返回错误，不再将不完整结果当作成功。

## 验证与剩余边界

在仓库根目录执行：

```sh
python3 -m unittest discover -s cloud_pc/desktop_version/tests -v
```

覆盖无界面参数、界面回调保留、同类并发请求和超时清理。尚未完成远程管理界面解耦、统一从机间 IP 转接及主机身份认证。旧协议超时后的迟到响应、API 与界面的同时同类操作、同路径并发下载，仍需端到端请求编号解决；串行处理不是完整的协议级关联方案。

Windows、Android、HoloLens 安装包未在本次重建或部署。

## 第二批：带请求编号的 RPC 与主机转接

桌面主机与 shared_components 中的 Python 从机新增四种 CLDC 消息：

| 类型 | 方向 | 含义 |
| --- | --- | --- |
| 0x0701 | 主机 → 从机 | 带 request_id 的单结果 RPC |
| 0x0781 | 从机 → 主机 | 对应 RPC 结果 |
| 0x0702 | 从机 → 主机 | 指定已知目标 IP 的转接请求 |
| 0x0782 | 主机 → 请求从机 | 对应转接结果 |

握手能力为 correlated_rpc_v1、host_relay_v1。首批操作为 file.list、file.stat，沿用 FileService 的实际读取策略（当前 Python 版本允许显式绝对路径读取），不等同于仅限共享目录。执行、删除、上传和媒体暂未纳入新 RPC。

转接默认拒绝。主机只使用已配置且在线的从机会话，不扫描或连接请求正文指定的未知 IP。显式授权示例（在本目录执行）：

```sh
python -m host.main --headless --slave 192.168.4.20 --slave 192.168.4.21 --relay-grant 192.168.4.20,192.168.4.21,file.list
```

请求从机的本地业务代码通过已有主机会话发起，不创建到目标的连接：

```python
# server 为运行中的 SlaveServer；明确选择所需主机会话。
session = server.host_sessions[connection_id]
reply = await session.request("192.168.4.21", "file.list", {"path": "G:/shared"})
```

转接正文含 request_id、target_ip、operation、payload。来源取自主机实际连接对象，不采信正文的来源字段。结果关联原 request_id；目标 RPC 使用独立请求编号，迟到或未知结果被忽略。断线时等待者收到错误；每个连接最多 16 个 RPC，主机最多 16 个正在处理的转接。

管理 API 新增 POST /api/<slave_id>/rpc，正文例如 {"operation":"file.stat","payload":{"path":"G:/shared"}}。GET list 对支持新能力的目标自动使用编号 RPC；旧消息路径仍存在，旧客户端不会被误认为支持新协议。HTTP 入口按 Content-Length 完整读取正文，拒绝超大正文和不支持的 Transfer-Encoding。

安全限制：来源 IP/操作授权只是路由约束，不是密码学身份认证。当前从机握手尚无主机身份认证，不能声称已经实现“只接受授权主机”。本批不开放远程 API；只能在可信网络内测试，完整认证仍待实施。

新增测试涵盖真实 CLDC TCP 转接、实际 Python SlaveServer 双向转接、并发/迟到回复、权限拒绝、离线/未知/不支持目标及断线清理。使用 digital_human/env/bin/python 执行测试时 9 项通过；系统 Python 缺 numpy 时跳过实际 SlaveServer 的一项测试。

该实现尚未接入常驻 8765 服务，也未移植到 UWP/Android 或部署到设备。管理界面解耦、活动信息统一汇总和其他操作的编号化仍需继续完成。

## 第三批：独立管理客户端

cloud_pc/host_program/main.py 提供独立主机入口，复用本工程服务，监听19527，不加载digital_human。新增当前Codex文字/SEND/STOP接口以及已登记从机的截图入口；GTK管理客户端在 ../management_ui，详见其README。6项新测试通过。本批不修改数字人、声纹或ASR，也不替换8765常驻服务。
