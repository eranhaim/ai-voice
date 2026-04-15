const BASE = "/api";

function getToken() {
  return localStorage.getItem("token");
}

function headers() {
  return { Authorization: getToken(), "Content-Type": "application/json" };
}

export async function login(password) {
  const res = await fetch(`${BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) throw new Error("Wrong password");
  const data = await res.json();
  localStorage.setItem("token", data.token);
  return data.token;
}

export function logout() {
  localStorage.removeItem("token");
}

export function isLoggedIn() {
  return !!getToken();
}

export async function getUsers() {
  const res = await fetch(`${BASE}/users`, { headers: headers() });
  if (res.status === 401) throw new Error("Unauthorized");
  return res.json();
}

export async function addUser(telegram_id, name) {
  const res = await fetch(`${BASE}/users`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ telegram_id: Number(telegram_id), name }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to add user");
  }
  return res.json();
}

export async function deleteUser(telegram_id) {
  const res = await fetch(`${BASE}/users/${telegram_id}`, {
    method: "DELETE",
    headers: headers(),
  });
  if (!res.ok) throw new Error("Failed to delete user");
}

export async function getRuns(telegramId) {
  const params = telegramId ? `?telegram_id=${telegramId}` : "";
  const res = await fetch(`${BASE}/runs${params}`, { headers: headers() });
  if (res.status === 401) throw new Error("Unauthorized");
  return res.json();
}

export async function getVoices(telegramId) {
  const params = telegramId ? `?telegram_id=${telegramId}` : "";
  const res = await fetch(`${BASE}/voices${params}`, { headers: headers() });
  if (res.status === 401) throw new Error("Unauthorized");
  return res.json();
}
