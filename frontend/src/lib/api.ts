export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Options = Omit<RequestInit, "body"> & { token?: string | null; body?: BodyInit | null };

async function request<T>(path: string, options: Options = {}, json = true): Promise<T> {
  const { token, headers, ...rest } = options;
  const res = await fetch(`${API_URL}${path}`, {
    ...rest,
    credentials: "include",
    headers: {
      ...(json ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(headers ?? {}),
    },
  });

  if (!res.ok) {
    let detail = `Request failed with status ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail))
        detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  try {
    return (text ? JSON.parse(text) : undefined) as T;
  } catch {
    return text as unknown as T;
  }
}

export function api<T>(path: string, options: Options = {}): Promise<T> {
  return request<T>(path, options, true);
}

/** Multipart upload: the browser sets its own Content-Type boundary. */
export function upload<T>(path: string, form: FormData, token?: string | null): Promise<T> {
  return request<T>(path, { method: "POST", body: form, token }, false);
}
