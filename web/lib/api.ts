export const API_BASE =
  process.env.NEXT_PUBLIC_CONTROL_API_URL?.replace(/\/$/, "") ?? "";

type ErrorPayload = { code?: string; message?: string; request_id?: string; detail?: string };

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
