const USER_API_URL = process.env.NEXT_PUBLIC_USER_API_URL ?? 'http://localhost:8001';
const BACKLOG_API_URL = process.env.NEXT_PUBLIC_BACKLOG_API_URL ?? 'http://localhost:8002';
const INTEGRATION_API_URL = process.env.NEXT_PUBLIC_INTEGRATION_API_URL ?? 'http://localhost:8003';
const TOKEN_KEY = 'backlog_token';

interface AuthResponse { token: string; user_id: number; }

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

function token() { return window.localStorage.getItem(TOKEN_KEY); }

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const sessionToken = token();
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {}),
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(body?.detail ?? 'The request failed.', response.status);
  return body as T;
}

export const api = {
  auth: {
    hasSession: () => Boolean(token()),
    clearSession: () => window.localStorage.removeItem(TOKEN_KEY),
    async authenticate(mode: 'login' | 'register', email: string, password: string) {
      const result = await request<AuthResponse>(`${USER_API_URL}/${mode}`, { method: 'POST', body: JSON.stringify({ email, password }) });
      window.localStorage.setItem(TOKEN_KEY, result.token);
      return result;
    },
  },
  backlog: { list: <T>() => request<T>(`${BACKLOG_API_URL}/backlog`) },
  integrations: { importSteam: <T>(steamId: string) => request<T>(`${INTEGRATION_API_URL}/steam/library/${encodeURIComponent(steamId)}`) },
};
