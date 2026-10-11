# 1.0.0 源码发布检查

## 产品版本

- Windows/Linux 共用版本：`shared_components/common/release.py`。
- Windows EXE 文件属性：`shared_components/windows_version_info.txt`；主机、从机 spec 和管理界面构建脚本已引用。
- 安卓从机、管理界面：各自 `app/build.gradle.kts` 的 `versionName=1.0.0`、`versionCode=1`。首次正式发布，旧测试版需先卸载再安装；卸载前备份需要保留的配置和共享文件。
- CLDC 协议版本、第三方依赖版本不随产品版本改动。
- 版本一致性测试位于 `management_ui/tests/test_release_version.py`。

## 源码边界

项目自有代码采用根目录 MIT 许可证；WireGuard 为未修改的固定版本 Maven 依赖，保留原许可。源码发布与 APK 发布分别执行检查。

已依据 `.gitignore` 检查待发布候选文件：个人配置、密钥文件、日志、缓存、二进制、发布目录及本机专用部署脚本不进入候选范围。规则扫描未发现 PEM 私钥块、常见访问令牌格式或 WireGuard 私钥赋值；规则检查不构成完整安全审计。

本仓库尚未初始化 Git，因此检查的是候选文件，不是实际暂存区。初始化后必须再检查：

```bash
git add .
git diff --cached --stat
git diff --cached
```

不要使用 `git add -f` 绕过忽略规则。仅更改 `.gitignore` 不会移除已经跟踪的敏感文件；如已有历史需另外检查并处理。

## 安装包发布

- 源码版本修改不会更新已安装或历史 EXE/APK。需从当前源码重新构建各平台产物。
- 安卓 `assembleRelease` 生成的 APK 未配置发布签名，不可直接作为已签名正式版；debug APK 仅用于验证。
- 使用自己的发布密钥并妥善保管；不可把 debug 签名改名后当成正式签名。
- 每次发布 APK 应同步提供匹配的第三方源码、许可证和校验清单，见 `THIRD_PARTY_RELEASE.md`。
- 所有设备上的升级、主从管理请求、文件访问、截图和 LLM 会话仍需进行端到端验收。

## 本次验证结果

- 管理模块单元测试 67 项通过；桌面模块 22 项测试，跳过 1 项，其余通过；协议脚本 4 项通过。
- 两个安卓应用的 debug/release 构建通过，包括 release lint 检查；安装包元数据均为 1.0.0 / versionCode 1。
- 两个 release APK 已使用独立发布密钥签名，正式文件名为 `cloud-android-slave-1.0.0.apk`、`cloud-management-1.0.0.apk`，版本为 1.0.0 / versionCode 1。签名校验通过，不使用 debug 签名。
- 发布密钥与密码保存在源码仓库外，不得提交；后续更新必须沿用该发布密钥，并递增 versionCode。开发 debug 包与发布包签名不同，切换时需卸载旧包，提前备份数据。
- Windows 三个 EXE 已从当前源码重新构建，`--version` 均返回 1.0.0，管理界面离线窗口创建测试通过；附带第三方许可归档。构建产物另存，不自动替换运行中的服务。
- Linux 便携源码运行包已重新生成，三个启动脚本的版本检查及 GTK 管理界面窗口创建测试通过。Linux 包需要系统 Python 和对应模块依赖，不是静态独立二进制。
