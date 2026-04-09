import { getAccessToken, signOut } from "@/lib/cognitoAuth";

export async function apiFetch(path: string, init: RequestInit = {}) {
  const token = getAccessToken();
  const headers = new Headers(init.headers ?? {});

  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}${path}`, {
    ...init,
    headers,
  });

  if (response.status === 401) {
    signOut();
    window.location.href = "/login";
  }

  return response;
}
