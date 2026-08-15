# ADR 0006：固件硬件、显示和产品参数采用版本化 Profile

- 状态：接受
- 日期：2026-08-15

## 背景

Hensun CAM 的引脚、屏幕方向、表情资源和产品功能曾同时出现在 `config.h`、`config.json`、CMake、PowerShell 脚本及测试中。修改 TFT 尺寸或方向需要同步多处字符串判断，容易生成能编译但资源画布错误的固件。

## 决策

- 每个固件变体组合一个 `HardwareProfile`、`DisplayProfile` 和 `ProductVariant`。
- 三类 Profile 使用版本化 JSON Schema 校验，并在进入 ESP-IDF 配置阶段前完成交叉校验。
- 构建时在 `build/generated` 生成 C++ Header、CMake 变量和不含密钥的 Profile 元数据；生成目录不提交 Git。
- `config.json` 只声明变体名称和三个 Profile 引用，不再复制 `sdkconfig_append`。
- 自有 Bootstrap URL 仍由构建参数注入，不进入 HardwareProfile 或版本库。
- 固件携带三个 Profile ID、Schema 版本和组合 SHA256，后续用于设备诊断、OTA 板型隔离和工厂追溯。
- CMake 根据生成的资源目录打包表情，不再判断横竖屏名称；烧录脚本使用生成元数据取得资源路径。
- 生成器在构建前拒绝 GPIO 冲突、分区重叠/越界、屏幕变换与逻辑尺寸不一致、表情画布不匹配及资源超限。

## 兼容策略

- `CONFIG_HENSUN_DISPLAY_LANDSCAPE` 等现有 Kconfig 仍由生成器输出，先保持固件行为和上游构建流程兼容。
- 当前横屏自有版是金样机基准；官网版和竖屏自有版继续使用独立 Profile。
- 新增同类 ST7789 屏幕时只增加 DisplayProfile 和匹配画布资源。新增其他控制器时还需增加 Panel 适配器，但不得修改情绪映射、口型或会话协议。

## 后果

- 优点：硬件事实、显示事实和产品策略分层；构建前即可发现常见烧录风险；脚本不再修改 `managed_components` 或向板型目录写临时配置。
- 代价：直接调用 ESP-IDF 且绕过 `scripts/build.py` 将因缺少生成文件而明确失败；开发者必须通过仓库构建入口生成 Profile。
