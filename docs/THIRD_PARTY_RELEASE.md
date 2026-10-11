# 安卓第三方源码与发布检查

## 固定版本

- tunnel AAR：`com.wireguard.android:tunnel:1.0.20260102`
- WireGuard Android tag 对应提交：`09b75c2bd37f749e2a8c85876394854113c74be7`
- wireguard-tools 子模块：`e2ecaaa739144997ccff89d6ad6ec81698ea6ced`
- elf-cleaner 子模块：`7efc05090675ec6161b7def862728086a26c3b1f`
- Go 与模块版本见根目录 `THIRD_PARTY_NOTICES.md`；额外包含 go.mod 中的 wintun 间接依赖。
- x/net 的附加模块：x/term v0.32.0、x/text v0.25.0，归档保留其原声明；不表示这些模块全部链接进 APK。
- desugar_jdk_libs 2.1.5 源码采用上游“Prepare for ... version 2.1.5”提交 `73170c345e6a762fc6a1f0301bb15218850023ef`。

子模块是独立源码，不包含在普通 GitHub 主仓库 ZIP 中。不得只附主仓库 ZIP 就宣称源码齐全。

## 生成源码材料

在有 Git、curl、tar、gzip、sha256sum 的 Linux 上运行：

```bash
bash android_slave/collect_third_party_sources.sh /absolute/new/source-output
```

脚本拒绝覆盖已有目录，核对固定提交，收集 Go 完整源码、模块源码和原生工具源码，并生成哈希清单。下载失败即退出；不生成虚假的“完成”标记。

将 `archives/` 中全部文件与 APK 放在同一发布页面、同样可访问的下载位置。保留全部上游版权、许可证、PATENTS、补丁及构建脚本，不仅提供上游首页链接。不采用未经准备的“三年书面承诺”替代源码下载。

## 发布门禁

- 核对源码 tag 与所用 Maven AAR 的对应关系，记录 AAR、APK、源码包 SHA-256；当前使用上游预编译 AAR，不声称已做可复现二进制重建。
- 原生工具构建入口：WireGuard Android `tunnel/build.gradle.kts`、`tunnel/tools/CMakeLists.txt`；Go 构建入口：`tunnel/tools/libwg-go/Makefile`，包含 runtime 补丁。
- 同时提供 Cloud PC 源码、Gradle 文件和所用构建工具版本说明；本项目未修改上游 AAR。
- 检查 Go 模块源码是否还要求其他模块，缺失时补齐。保留 Go 的 LICENSE、PATENTS 与第三方声明。
- 核对 desugar_jdk_libs 2.1.5 所对应源码及 OpenJDK 许可、Classpath Exception、ASSEMBLY_EXCEPTION、ADDITIONAL_LICENSE_INFO；不能用 Apache 文本代替这些文件。
- 保留 AndroidX annotation 1.9.1、collection 1.5.0 的版权及 Apache 声明。
- 核对应用“开源许可与第三方声明”能查看随包通知；第三方许可文件与相应源码版本一致。
- 检查发布包不包含私人 VPN 配置、密钥、访问令牌、个人日志或本地签名文件。

只有上述检查全部完成，才能将 APK 标记为第三方材料核验完成。根目录 MIT 许可证不改变第三方义务。

## 本地材料验证记录（2026-10-11）

第三方归档位于仓库外 `cloud_pc_releases/third_party_sources/`；不纳入 MIT 源码仓库。发布时另行上传归档及 SHA256SUMS。

- 云电脑从机 0.5.32 已构建，并通过 APK 签名校验；仍为开发 debug 签名，不应当作正式发布签名。
- AAR SHA-256：`2b9c16db026496123e4db695d26d03d1958a201096c7c4c89b21077dc70f3119`。
- APK SHA-256：`94ca3e1ee4e16e31f91c7902a2d3f67909a1fbac7c0dee1cc6ec50813b25a247`。
- APK 内 12 个 WireGuard 原生库与原 AAR 逐字节一致，未修改。
- WireGuard、x/sys、x/crypto、x/net、wintun、x/time、google/btree、gvisor 的模块源码 h1 与固定版本上游 go.sum 一致。
- x/term 与 x/text 为附加源码，未列于顶层 go.sum，不宣称已经由该文件验证。
- 随包的 GPL、wireguard-go MIT、Go BSD 以及 x/sys、x/crypto、x/net BSD 文本已与对应源码比较一致。
- 尚未在设备上手动验证许可弹窗；未进行上游 AAR 的可复现重建，或覆盖所有平台二进制的法律审计。

可用 `android_slave/verify_third_party.py SOURCE_DIR ORIGINAL_AAR APK` 重跑原生库与模块源码检查。
