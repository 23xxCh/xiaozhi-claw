export type Agent = {
  id: string;
  name: string;
  avatar_url: string | null;
  system_prompt: string;
  model_preset_id: string;
  voice_preset_id: string;
  memory_consent: boolean;
  tools: Record<string, boolean>;
  config_version: number;
  device_count: number;
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
  online: boolean;
  last_seen_at: string | null;
};

export type ModelPreset = {
  id: string;
  display_name: string;
  description: string;
  is_default: boolean;
};

export type VoicePreset = {
  id: string;
  display_name: string;
  language: string;
  voice: string;
  is_default: boolean;
};

export type Usage = {
  voice_turns: number;
  provider_cost_micros: number;
  pricing_configured: boolean;
  asr_units: number;
  llm_input_units: number;
  llm_output_units: number;
  tts_units: number;
};

export type Memory = { id: string; key: string; value: string; updated_at: string };
