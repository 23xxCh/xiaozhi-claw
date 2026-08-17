// Generated device WSS V1 vocabulary. Do not edit by hand.
export const deviceWsProtocolVersion = 1 as const;
export const deviceWsMessageTypes = ["hello", "listen", "tts", "mcp", "system", "alert", "device_config_ack"] as const;
export const ttsStates = ["start", "ready", "stop", "drained"] as const;
export const optionalCorrelationFields = ["session_id", "turn_id", "reply_id"] as const;
export type DeviceWsMessageType = (typeof deviceWsMessageTypes)[number];
export type TtsState = (typeof ttsStates)[number];
