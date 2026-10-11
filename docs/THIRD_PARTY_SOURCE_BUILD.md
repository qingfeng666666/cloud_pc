# 第三方源码包使用说明

源码归档保持上游原样，不受 Cloud PC MIT 许可证覆盖。对应 Cloud PC Android Slave 0.5.32，依赖原版 tunnel AAR 1.0.20260102。

## WireGuard 原生工具与库

解压 `wireguard-android-1.0.20260102.tar.gz`。分别解压 `wireguard-tools.tar.gz`、`elf-cleaner.tar.gz`，将各自顶层目录内的内容放入主源码中的：

```text
tunnel/tools/wireguard-tools/
tunnel/tools/elf-cleaner/
```

注意：不是把外层带提交号的目录再嵌套一层。主仓库归档不包含子模块内容，所以必须放回以上两处。

保留上游 Gradle wrapper 和配置。使用 Java 17、Android SDK 36，并按上游 Gradle/CMake 设置安装所需 NDK 和 CMake，在主源码根目录执行：

```bash
./gradlew :tunnel:assembleRelease
```

该命令为上游构建入口说明，本项目未完成 AAR 可复现重建，不声称输出与 Maven AAR 逐位相同。

原生编译规则在 `tunnel/tools/CMakeLists.txt`。Go 编译规则在 `tunnel/tools/libwg-go/Makefile`，其中会使用 Go 1.24.3 并应用同目录的 runtime 补丁；不要漏掉该补丁。模块源码 ZIP 保持 Go module proxy 原始格式，可用本地文件代理或 Go 模块缓存供构建使用。

## 其他归档

- `go1.24.3.src.tar.gz`：Go 源码、许可证、PATENTS 和运行时第三方声明。
- `desugar_jdk_libs-2.1.5.tar.gz`：保留上游构建规则、OpenJDK 许可及例外条款。其源码构建按归档内 README/BUILD 执行；本项目使用原版 Maven 构件，不修改其源码。
- 各 Go 模块 ZIP：保留原始 LICENSE、NOTICE、PATENTS 和逐文件版权声明。包含模块不表示 APK 链接了模块内所有包。
- `SHA256SUMS`：在解包目录执行 `sha256sum -c SHA256SUMS` 检查发布文件完整性。

本源码材料与 APK 应在同一发布页面分别提供下载。Cloud PC 自有源码和构建配置另在 MIT 源码仓库提供。不要仅上传 APK 或用上游主页链接代替源码材料。
