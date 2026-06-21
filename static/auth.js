// Shared auth helpers for ToolShop (JWT stored in localStorage).
const TOKEN_KEY = 'toolshop_token';
const USER_KEY = 'toolshop_user';

function getToken() { return localStorage.getItem(TOKEN_KEY); }
function getUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); }
  catch (e) { return null; }
}
function setAuth(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}
function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}
function logout() {
  clearAuth();
  window.location = '/login';
}

// Redirect to /login if not authenticated. Call at the top of protected pages.
function requireAuth() {
  if (!getToken()) {
    window.location = '/login';
    return false;
  }
  return true;
}

// fetch() wrapper that attaches the Bearer token and handles expiry.
async function authFetch(url, options = {}) {
  options.headers = Object.assign({}, options.headers);
  const token = getToken();
  if (token) options.headers['Authorization'] = 'Bearer ' + token;
  const res = await fetch(url, options);
  if (res.status === 401) {
    clearAuth();
    window.location = '/login';
  }
  return res;
}

// Renders the logged-in user's email + a logout button into an element.
function renderUserBar(el) {
  const user = getUser();
  if (!el) return;
  el.innerHTML = '';
  if (user) {
    const span = document.createElement('span');
    span.textContent = user.email;
    span.style.marginRight = '10px';
    span.style.fontSize = '14px';
    el.appendChild(span);
  }
  const btn = document.createElement('button');
  btn.textContent = 'Log out';
  btn.className = 'btn btn-ghost btn-sm';
  btn.addEventListener('click', logout);
  el.appendChild(btn);
}
