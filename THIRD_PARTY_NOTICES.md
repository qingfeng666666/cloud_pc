# 第三方组件

项目自有代码采用 MIT；以下组件不适用项目的 MIT 授权。

## 授权边界

- `android_slave/app/src/main/java/com/cloudpc/androidslave/` 中本项目自有代码采用 MIT。
- WireGuard 不复制进自有代码目录，不修改其源码；Gradle 通过 `com.wireguard.android:tunnel:1.0.20260102` 引入上游原版 AAR。
- `android_slave/app/src/main/assets/licenses/` 中第三方许可文本保留原授权，不适用项目 MIT；`CLOUD-PC-MIT.txt` 是本项目自身许可证。
- 第三方源码归档与安装包单独发布，不混入 MIT 源码目录。原版依赖仍需保留版权、许可与适用的源码材料。

| 组件 | 固定版本 | 许可/用途 |
| --- | --- | --- |
| WireGuard Android tunnel | 1.0.20260102 | Java/Kotlin 及集成代码 Apache-2.0 |
| wireguard-tools | 上述版本记录的 Git 子模块提交 | GPL-2.0；APK 内 `libwg.so`、`libwg-quick.so` |
| wireguard-go | v0.0.0-20250521234502-f333402bd9cb | MIT；`libwg-go.so` |
| Go | 1.24.3 | BSD-3-Clause；包含上游 Android runtime 补丁 |
| golang.org/x/sys | v0.33.0 | BSD-3-Clause |
| golang.org/x/crypto | v0.38.0 | BSD-3-Clause |
| golang.org/x/net | v0.40.0 | BSD-3-Clause；模块依赖，不代表全部链接 |
| AndroidX annotation / collection | 1.9.1 / 1.5.0 | Apache-2.0 |
| desugar_jdk_libs | 2.1.5 | 含 OpenJDK 衍生代码，需保留其适用许可及 Classpath Exception |

另外保留 WireGuard go.mod 中的 wintun 间接模块源码（MIT）；这不表示它被链接进安卓 APK。源码构建辅助工具 elf-cleaner 保留自身 COPYING，不适用项目 MIT。

源码材料还包括上游模块图中的 google/btree v1.1.2（Apache-2.0）、gvisor v0.0.0-20250503011706-39ed1f5ac29c（Apache-2.0，保留其第三方声明）、x/time v0.7.0、x/term v0.32.0 和 x/text v0.25.0（BSD-3-Clause）。源码包中的所有模块保留各自原许可；归档包含不代表 APK 实际链接。

WireGuard 来源：https://git.zx2c4.com/wireguard-android/ 。以固定版本源码内的逐文件声明为准。

安卓应用提供“开源许可与第三方声明”入口；文本位于 `android_slave/app/src/main/assets/licenses/`。

## 发布限制

许可文本不等于完整履行源码提供义务。安卓安装包发布前必须完成 [第三方发布检查](docs/THIRD_PARTY_RELEASE.md)，同时发布对应源码归档，不仅提供上游主页链接。

本清单覆盖已识别的安卓依赖，不能代替 Windows/Linux 打包依赖的完整审计；尤其 PySide6/Qt、SciPy、NumPy 等须保留各自的许可证与随包通知。
