# Hensun No-CAM 标准版表情适配

## 目标

将用户提供的九个“标准版表情包”GIF 接入 `hensun-nocam-pilot-v1`，替换当前过小的字体 Emoji，并在 320×240 横屏上铺满画布。设备状态和回复字幕作为浮层显示，不再为它们预留白边。

## 接口设计

- 输入：固件现有 `Display::SetEmotion(const char*)` 情绪名称。
- 输出：与情绪匹配的 320×240 循环 GIF；未知情绪回退到 `neutral`。

## 数据流

```text
服务端 llm.emotion
-> HensunNoCamDisplay 情绪别名映射
-> assets 分区中的标准版 GIF
-> LVGL 320×240 满屏播放
```

## 与现有代码的关系

- 为 non-CAM 板型增加独立 GIF 资源目录，不改变 CAM 金样机表情包。
- 默认资源构建器接受显式表情目录，并继续把语音模型、字体和表情合并到 `assets` 分区。
- non-CAM 显示类只负责情绪别名映射，沿用通用 `SpiLcdDisplay` 的状态与字幕浮层。
- 构建、测试阶段不刷机；刷机前重新确认端口与授权，只允许更新应用及 `assets`，不得覆盖 NVS、Wi-Fi 或 `hensun_keys`。

## 验收

- 九个资源均为有效 320×240 动画 GIF，名称和映射完整。
- non-CAM 主机测试、默认资源构建和 ESP-IDF 固件构建通过。
- 真机上状态栏、字幕、声音和九类表情互不遮挡；未知情绪显示默认表情。
