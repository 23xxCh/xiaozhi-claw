# 豆包 3.0 音色扩展：候选验证

保留原 `doubao-vv`（jupiter）和所有已有角色。新增四个默认禁用的音色记录，复用现有模型过滤、角色原子保存和下一轮配置快照；未改网页、固件或线上服务。

来源：[官方音色目录](https://docs.volcengine.com/docs/6561/1257544?lang=zh) 的“语音合成模型 2.0、S2S-O2.0、S2S-全双工”部分。此批只选主推四种，不将整个 TTS 库视为已验收音色。

| 目录 ID | 供应商音色 | 真实会话建立耗时 |
|---|---|---:|
| doubao-vv-2 | zh_female_vv_uranus_bigtts | 516 ms |
| doubao-xiaohe-2 | zh_female_xiaohe_uranus_bigtts | 500 ms |
| doubao-yunzhou-2 | zh_male_m191_uranus_bigtts | 375 ms |
| doubao-xiaotian-2 | zh_male_taocheng_uranus_bigtts | 313 ms |

2026-09-09 使用模型 `1.2.6.1`、现有服务端凭据，每种执行一次 `scripts/doubao_probe.py --voice <供应商音色>`。四次均完成鉴权及会话配置；未发送录音、未播放、未测设备。以上不是首响延迟，也不能证明实际返回音色、完整尾音或多轮体验正确。

自动检查：`python -m pytest backend/tests/test_voice_routes.py backend/tests/test_doubao_realtime.py -q`，41 项通过；ruff 和 diff 检查通过。新增回归覆盖禁用候选不能创建角色、显式启用后目录不覆盖、兼容路线可保存、串联路线拒绝该音色、未知音色拒绝。

下一步：用已授权测试 WAV 执行 `--audio <16k单声道16bit WAV> --save-audio <输出 WAV>`，再做真机三轮、停止、尾音及音色实听。通过前不修改候选 `enabled`，不设置生产验证标志。确认后通过受控数据库变更开放相应记录；现有网页会从音色接口读取，无需另写一份前端音色列表。

Qwen-Audio 3.0 原生端到端仍未实现；现有 `aliyun-dialog` 是阿里托管应用路线，不能改名冒充新模型。OpenAI 路线未新增。回退本批代码前，若曾开放新音色，应先将引用它们的角色切回兼容旧音色，避免回退后的白名单拒绝配置。
