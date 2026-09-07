export const CLAIM_CODE_KEY = "hensun_claim_code";
export const CLAIM_DEVICE_KEY = "hensun_claim_device_id";
export const CLAIM_PENDING_KEY = "hensun_claim_pending";

export function safeNext(value: string | null) {
  if (!value?.startsWith("/") || value.startsWith("//")) return "/console";
  try {
    if (/[\\\u0000-\u001f\u007f]/.test(decodeURIComponent(value))) return "/console";
    const url = new URL(value, "https://hensun.invalid");
    return url.origin === "https://hensun.invalid" ? `${url.pathname}${url.search}${url.hash}` : "/console";
  } catch {
    return "/console";
  }
}
