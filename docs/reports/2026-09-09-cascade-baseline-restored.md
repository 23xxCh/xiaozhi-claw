# 串联优先：运行基线恢复

用户确认先完成 ASR → LLM → TTS，再推进端到端。执行优先级已写入总体开发计划，
保留已有角色与候选方案；本次没有修改角色选择、烧录或触发语音测试。

## 本轮验证

- 起始状态：控制面 8000、网页 3000 正常监听，实时网关 8001 缺失。
  上一批只启动了控制面和网页，因此本轮不将该缺失误判为新的网关崩溃。
- 恢复网关；按端口监听及进程命令核实 PID，修正运行记录中启动器 PID 和实际服务 PID
  的区别。当前控制面 16396、网关 30952、网页 47752。
- 三个 HTTP 健康/页面检查均 200；新网关日志观察到一次设备连接 accepted，
  ERROR/Traceback/Exception 匹配行数为 0。这只证明连接建立，不证明收音或发声质量。
- `python -m pytest backend/tests/test_voice_release_regressions.py
  backend/tests/test_device_connections.py backend/tests/test_websocket.py
  scripts/tests/test_local_pilot_preflight.py -q`：39 项通过。
- 未新增常驻监控/自动拉起服务；本轮是恢复运行基线，不宣称已经实现生产进程守护。

## 下一道门槛

固定 fast-chat + Cherry 验收，先确认目标设备选择该路线，再做唤醒、短句尾字、长句、
连续三轮、说话时停止以及断网恢复。之后才开始两组 50 次、2 小时连续对话、8 小时待机。
本轮未采集这些真机数据，不能提高对应成功率、延迟或声学体验评分。
