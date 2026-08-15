import createClient from "openapi-fetch";

import type { paths } from "@/lib/generated/openapi";

export const API_BASE =
  process.env.NEXT_PUBLIC_CONTROL_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

type ErrorPayload = { code?: string; message?: string; request_id?: string; detail?: string };

export const typedApi = createClient<paths>({
  baseUrl: API_BASE,
  credentials: "include",
});

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code = "REQUEST_FAILED",
    public requestId?: string,
  ) {
    super(message);
  }
}

function redirectForAuth(status: number, code?: string) {
  if (typeof window === "undefined") return;
  const next = `${window.location.pathname}${window.location.search}`;
  if (status === 401) {
    window.dispatchEvent(new CustomEvent("hensun-auth-redirect", { detail: `/login?next=${encodeURIComponent(next)}` }));
  } else if (status === 403 && code === "AGREEMENTS_REQUIRED") {
    window.dispatchEvent(new CustomEvent("hensun-auth-redirect", { detail: `/auth/complete?next=${encodeURIComponent(next)}` }));
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
  } catch {
    throw new ApiError("无法连接服务，请检查网络后重试", 0, "NETWORK_ERROR");
  }
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as ErrorPayload | null;
    redirectForAuth(response.status, payload?.code);
    throw new ApiError(
      payload?.message ?? payload?.detail ?? `请求失败（${response.status}）`,
      response.status,
      payload?.code,
      payload?.request_id,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function unwrapTyped<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response };
  try {
    result = await request;
  } catch {
    throw new ApiError("无法连接服务，请检查网络后重试", 0, "NETWORK_ERROR");
  }
  if (result.error !== undefined) {
    const payload = result.error as ErrorPayload;
    redirectForAuth(result.response.status, payload?.code);
    throw new ApiError(
      payload?.message ?? payload?.detail ?? `请求失败（${result.response.status}）`,
      result.response.status,
      payload?.code,
      payload?.request_id,
    );
  }
  return result.data as T;
}
