# Hensun Face Event Contract v1

This contract binds the 36 face states to real firmware and cloud events. The
ESP32 keeps the upstream XiaoZhi message path: `Application::OnIncomingJson`
routes `llm.emotion` and `alert.emotion` to `Display::SetEmotion`. No separate
display socket or text-keyword classifier is used.

## Local device events

| Face | Canonical input | Real trigger |
|---|---|---|
| Ready | `ready` | Boot/startup state |
| Idle | `idle` | Device state becomes idle |
| Listening | `listening` | Device state becomes listening |
| Thinking | `thinking` | Cloud emotion while waiting for an answer |
| Speaking | `speaking` | Device state becomes speaking |
| Interrupted | `interrupted` | User clicks BOOT while the device is speaking |
| Pairing | `pairing` | Wi-Fi scan/connect/configuration notification |
| Network OK | `network_ok` | Network-connected notification |
| Network error | `network_error` | Fatal state or network/server error alert |
| Updating | `updating` | Firmware/assets upgrade state or alert |
| Sleep | `sleep` | Cloud sleep command; this USB-powered board does not deep-sleep |
| Reminder | `reminder` | Reminder/alarm alert from the product backend |

## Conversation emotion events

| Face | Canonical input | Source |
|---|---|---|
| Happy | `happy` | `llm.emotion` |
| Curious | `curious` | `llm.emotion` |
| Caring | `caring` | `llm.emotion` |
| Laughing | `laughing` | `llm.emotion` |
| Funny | `funny` | `llm.emotion` |
| Loving | `loving` | `llm.emotion` |
| Embarrassed | `embarrassed` | `llm.emotion` |
| Confident | `confident` | `llm.emotion` |
| Delicious | `delicious` | `llm.emotion` |
| Sad | `sad` | `llm.emotion` |
| Crying | `crying` | `llm.emotion` |
| Sleepy | `sleepy` | `llm.emotion` |
| Silly | `silly` | `llm.emotion` |
| Angry | `angry` | `llm.emotion` |
| Surprised | `surprised` | `llm.emotion` |
| Shocked | `shocked` | `llm.emotion` |
| Winking | `winking` | `llm.emotion` |
| Relaxed | `relaxed` | `llm.emotion` |
| Confused | `confused` | `llm.emotion` |
| Proud | `proud` | `llm.emotion` |
| Excited | `excited` | `llm.emotion` |
| Worried | `worried` | `llm.emotion` or warning alert |
| Apology | `apology` | `llm.emotion` or product apology event |
| Safe block | `safe_block` | Safety layer blocks content or a restricted action |

The XiaoZhi standard 21-name emotion set is fully accepted: `neutral`, `happy`,
`laughing`, `funny`, `sad`, `angry`, `crying`, `loving`, `embarrassed`,
`surprised`, `shocked`, `thinking`, `winking`, `cool`, `relaxed`, `delicious`,
`kissy`, `confident`, `sleepy`, `silly`, and `confused`. `cool` reuses the
Confident face, `kissy` reuses the Loving face, and `neutral` restores the face
for the current local device state.

## Cloud payloads

Conversation emotion:

```json
{"type":"llm","emotion":"happy"}
```

Reminder or alarm. The device displays the reminder face for six seconds while
the existing alert path handles status text, message text and vibration sound:

```json
{
  "type":"alert",
  "status":"提醒",
  "message":"该休息一下了",
  "emotion":"reminder"
}
```

Safety block. The backend, not the ESP32, decides whether content is unsafe:

```json
{
  "type":"alert",
  "status":"安全提示",
  "message":"这个请求暂时无法执行",
  "emotion":"safe_block"
}
```

Supported compatibility aliases include `cool`, `kissy`, `alarm`, `timer_done`,
`reminder_due`, `warning`, `cancel`, `cloud_off`, `link`, `download`,
`cloud_download`, `robot_2`, `content_blocked`, and `safety_block`. Unknown
inputs are logged and fall back to the current local device state. Every
accepted route logs its source, input, selected face and hold time for
field-test auditing.
