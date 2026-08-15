import type { components } from "@/lib/generated/openapi";

export type Agent = components["schemas"]["AgentResponse"];
export type Device = components["schemas"]["DeviceDetailResponse"];
export type DeviceConfiguration = components["schemas"]["DeviceConfigurationResponse"];
export type DeviceConfigurationField =
  components["schemas"]["DeviceConfigurationFieldResponse"];
export type DeviceConfigurationSchema =
  components["schemas"]["DeviceConfigurationSchemaResponse"];
export type ModelPreset = components["schemas"]["ModelPresetResponse"];
export type VoicePreset = components["schemas"]["VoicePresetResponse"];
export type Usage = components["schemas"]["UsageSummaryResponse"];
export type Memory = components["schemas"]["MemoryResponse"];
export type UsageProfile = components["schemas"]["UsageProfileResponse"];
export type OnboardingStatus = components["schemas"]["OnboardingStatusResponse"];
