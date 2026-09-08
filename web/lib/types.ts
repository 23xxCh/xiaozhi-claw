export type Agent = {
  id: string;
  usage_profile_id: string;
  name: string;
  avatar_url: string | null;
  system_prompt: string;
  model_preset_id: string;
  voice_preset_id: string;
  memory_consent: boolean;
  tools: Record<string, boolean>;
  llm_temperature: number;
  tts_speech_rate: number;
  config_version: number;
  device_count: number;
};

export type DeviceConfiguration = {
  device_id: string;
  desired_version: number;
  applied_version: number;
  speaker_volume: number;
  screen_brightness: number;
  applied_speaker_volume: number | null;
  applied_screen_brightness: number | null;
  sync_status: "unknown" | "pending" | "synced" | "failed";
  last_error_code: string | null;
  command_id: string | null;
  updated_at: string;
  applied_at: string | null;
};

export type Device = {
  id: string;
  serial_number: string;
  board_type: string;
  lifecycle: string;
  name: string;
  hardware_version: string;
  firmware_version: string;
  ota_auto_update: boolean;
  active_agent_id: string | null;
  active_profile_id: string | null;
  online: boolean;
  last_seen_at: string | null;
};

export type RouteCapabilities = { system_prompt?: boolean; history?: boolean; llm_temperature: boolean; tts_speech_rate: boolean; tools: boolean; supported_tool_ids: string[] };
export type ModelPreset = { id: string; display_name: string; description: string; is_default: boolean; route_kind: "cascade" | "realtime_s2s" | "managed_app"; capabilities: RouteCapabilities; compatible_voice_ids: string[]; default_voice_preset_id: string | null };
export type VoicePreset = { id: string; display_name: string; language: string; provider: string; voice: string; preview_url: string | null; is_default: boolean };
export type Usage = { voice_turns: number; provider_cost_micros: number; pricing_configured: boolean; asr_units: number; llm_input_units: number; llm_output_units: number; tts_units: number; managed_dialog_requests?: number; realtime_s2s_requests: number; unknown_cost_records: number };
export type Memory = { id: string; key: string; value: string; updated_at: string };
export type UsageProfile = { id: string; kind: "adult" | "youth"; display_name: string; age_band: "12_13" | "14_17" | null; guardian_consent_version: string | null; guardian_consent_at: string | null; memory_consent: boolean; quiet_start: string; quiet_end: string; daily_limit_minutes: number; continuous_reminder_minutes: number };
export type OnboardingStatus = { device_bound: boolean; assistant_configured: boolean; device_online: boolean; first_conversation_complete: boolean; next_action: "bind_device" | "configure_assistant" | "bring_device_online" | "start_conversation" | "complete"; active_device_id: string | null; active_agent_id: string | null };
