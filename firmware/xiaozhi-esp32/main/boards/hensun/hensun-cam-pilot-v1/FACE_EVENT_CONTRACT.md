# Hensun Face Event Contract v2

## 60-scene upgrade design

**Goal:** render the 60 scenes in the HensunAI V1.0 development pack on the
240x320 ST7789 without storing the 1536x1024 PNG masters in firmware.

**Inputs:** local device states, XiaoZhi's structured `llm.emotion` /
`alert.emotion` strings, and the future Hensun cloud's lowercase ASCII scene
event names. **Output:** a locally rendered LVGL vector face at 20 FPS.

`device/cloud event -> SetEmotion or StateFromDevice -> route table ->
HensunFaceState -> RenderFace -> LVGL objects -> ST7789`

The implementation keeps camera preview ownership unchanged, accepts all 21
XiaoZhi standard emotions, logs and safely falls back on unknown inputs, and
uses BOOT long-press to demonstrate the 60 product scenes in manifest order.
The face is assembled from reusable eyes, angled brows, cheeks, a curved/open
mouth and a small status symbol. The HD PNGs remain design references only.

## Hensun 60-scene canonical inputs

| # | Scene | Canonical input |
|---:|---|---|
| 01 | Focused listening | `listening_started` |
| 02 | Positive response | `positive_response` |
| 03 | Clarification needed | `clarification_needed` |
| 04 | Processing | `processing_started` |
| 05 | Confirmation required | `confirmation_required` |
| 06 | ASR low confidence | `asr_low_confidence` |
| 07 | Noisy environment | `noisy_environment` |
| 08 | Waiting for user to continue | `user_continue_expected` |
| 09 | User interrupted assistant | `user_interrupted_assistant` |
| 10 | Boot ready | `boot_ready` |
| 11 | Wake word detected | `wake_word_detected` |
| 12 | Idle breathing | `idle_entered` |
| 13 | Pairing mode | `pairing_mode_entered` |
| 14 | Network connected | `network_connected` |
| 15 | Charging | `charging_started` |
| 16 | Charge complete | `charge_complete` |
| 17 | Sleep entered | `sleep_entered` |
| 18 | Mild amusement | `mild_amusement` |
| 19 | Strong amusement | `strong_amusement` |
| 20 | Positive surprise | `positive_surprise` |
| 21 | Compliment received | `compliment_received` |
| 22 | Achievement celebration | `achievement_celebration` |
| 23 | Encouragement | `encouragement_requested` |
| 24 | Thanks received | `thanks_received` |
| 25 | Affection received | `affection_received` |
| 26 | Curiosity engaged | `curiosity_engaged` |
| 27 | Sadness detected | `sadness_detected` |
| 28 | Comfort mode | `comfort_mode_entered` |
| 29 | Worry detected | `worry_detected` |
| 30 | Anger de-escalation | `anger_detected` |
| 31 | Fear care | `fear_detected` |
| 32 | Loneliness support | `loneliness_detected` |
| 33 | Fatigue detected | `fatigue_detected` |
| 34 | Unfairness distress | `unfairness_distress` |
| 35 | Disappointment detected | `disappointment_detected` |
| 36 | Assistant apology | `assistant_apology_required` |
| 37 | First interaction | `first_interaction` |
| 38 | Morning greeting | `morning_greeting` |
| 39 | Noon greeting | `noon_greeting` |
| 40 | Bedtime greeting | `bedtime_greeting` |
| 41 | Return after absence | `return_after_absence` |
| 42 | Birthday greeting | `birthday_greeting` |
| 43 | Holiday greeting | `holiday_greeting` |
| 44 | Meal check-in | `meal_check_in` |
| 45 | Reminder created | `reminder_created` |
| 46 | Reminder due | `reminder_due` |
| 47 | Timer started | `timer_started` |
| 48 | Timer finished | `timer_finished` |
| 49 | Alarm triggered | `alarm_triggered` |
| 50 | Volume changed | `volume_changed` |
| 51 | Mode changed | `mode_changed` |
| 52 | Query result ready | `query_result_ready` |
| 53 | Network unavailable | `network_unavailable` |
| 54 | Cloud service unavailable | `cloud_service_unavailable` |
| 55 | Battery low | `battery_low` |
| 56 | Battery critical | `battery_critical` |
| 57 | Device overheat | `device_overheat` |
| 58 | Microphone fault | `microphone_fault` |
| 59 | Content safety blocked | `content_safety_blocked` |
| 60 | User crisis intervention | `user_crisis_detected` |

The table is the stable product vocabulary. Battery and charging scenes are
available for future hardware but are not emitted by this USB-powered CAM
pilot board. Safety and hardware facts must come from trusted local or backend
safety modules, never from free-form LLM prose.

## Legacy and XiaoZhi compatibility inputs

This contract binds the 60 scene states to real firmware and cloud events. The
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
`kissy`, `confident`, `sleepy`, `silly`, and `confused`. `cool` and `confident`
reuse the encouragement face, `kissy` reuses the affection face, and `neutral`
restores the face for the current local device state.

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
