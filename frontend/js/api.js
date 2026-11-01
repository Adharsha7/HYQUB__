/**
 * HYQUB API client. The session token lives in this module's memory only:
 * never persistent web storage or cookies. A page reload logs the user out.
 */
import { API_BASE_URL } from './config.js';

let token = null;
let onUnauthorized = () => {};

export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }
export function hasSession() { return token !== null; }
export function clearSession() { token = null; }

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request(method, path, body, { auth = true, headers: extraHeaders = {} } = {}) {
  const headers = { 'Content-Type': 'application/json', ...extraHeaders };
  if (auth && token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'NETWORK', 'Cannot reach the server. Check that the backend is running.');
  }

  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }

  if (!res.ok) {
    const d = data && data.detail;
    const code = d && typeof d === 'object' && !Array.isArray(d) ? d.code : undefined;
    let message = 'Something went wrong. Please try again.';
    if (typeof d === 'string') message = d;
    else if (d && typeof d === 'object' && !Array.isArray(d) && d.message) message = d.message;
    else if (Array.isArray(d)) message = 'Please check the values you entered.';
    // A 401 on a protected call means the session expired. Login/pay errors are shown inline.
    if (res.status === 401 && auth && code !== 'WRONG_PASSWORD') onUnauthorized();
    throw new ApiError(res.status, code, message);
  }
  return data;
}

async function startSession(path, username, password) {
  const data = await request('POST', path, { username, password }, { auth: false });
  token = data.token;
  return data;
}

export const login  = (u, p) => startSession('/auth/login', u, p);
export const signup = (u, p) => startSession('/auth/signup', u, p);

export async function logout() {
  try { await request('POST', '/auth/logout'); } catch { /* best effort */ }
  token = null;
}

export const getMe       = () => request('GET', '/me');
export const getHistory  = () => request('GET', '/me/history');
export const searchUsers = (q) => request('GET', `/users/search?q=${encodeURIComponent(q)}`);
export const pay = (toUserId, amountEth, password, idemKey) =>
  request('POST', '/pay', { to_user_id: toUserId, amount_eth: amountEth, password }, {
    headers: idemKey ? { 'Idempotency-Key': idemKey } : {},
  });

/** Fetch ETH market data from the backend. */
export const getMarket = () => request('GET', '/market/eth');
