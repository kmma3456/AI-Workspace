const SESSION_KEY = "ai-staff-session";

function loginUrl(next) {
  const path = next || (location.pathname + location.search);
  if (path.startsWith("/ui/")) {
    return `/?next=${encodeURIComponent(path)}`;
  }
  return "/";
}

async function ensureSession() {
  const res = await fetch("/api/auth/me", { credentials: "same-origin" });
  if (!res.ok) {
    location.replace(loginUrl());
    return false;
  }
  return true;
}

async function logoutAdmin() {
  await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  location.href = "/";
}

/** @deprecated webhook token — use login session */
function getToken() {
  return "";
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch("/api" + path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) {
    location.replace(loginUrl());
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function fmtPhone(phone) {
  const p = String(phone || "");
  if (p.length === 11) return `${p.slice(0, 3)}-${p.slice(3, 7)}-${p.slice(7)}`;
  if (p.length === 10) return `${p.slice(0, 3)}-${p.slice(3, 6)}-${p.slice(6)}`;
  return p;
}

/** 탭이 보일 때만 주기적으로 갱신하고, 다시 보이는 순간 한 번 바로 갱신한다. */
function startPolling(fn, intervalMs) {
  setInterval(() => { if (!document.hidden) fn(); }, intervalMs);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) fn(); });
}

const SOURCE_LABEL = { customer: "고객", ai: "AI", owner: "사장님", template: "자동문자" };

/** perspective: "customer" 면 고객 메시지가 오른쪽, "business" 면 업체 메시지가 오른쪽 */
function renderThread(container, messages, perspective) {
  const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 40;
  if (!messages.length) {
    container.innerHTML = `<div class="empty">아직 대화가 없습니다.</div>`;
    return;
  }
  container.innerHTML = messages.map((m) => {
    const isCustomer = m.source === "customer";
    const side = (perspective === "customer") === isCustomer ? "right" : "left";
    return `
      <div class="bubble-wrap ${side} ${esc(m.source)}">
        <div class="bubble">${esc(m.content)}</div>
        <div class="bubble-meta">${esc(SOURCE_LABEL[m.source] || m.source)} · ${fmtTime(m.created_at)}</div>
      </div>`;
  }).join("");
  if (atBottom) container.scrollTop = container.scrollHeight;
}

function notifClass(text) {
  if (/차단|오류|긴급|확인 필요|실패/.test(text)) return "warn";
  if (/견적/.test(text)) return "quote";
  if (/일정/.test(text)) return "ok";
  return "";
}

function renderNotifications(container, items) {
  container.innerHTML = items.length
    ? items.map((n) => `<div class="notif ${notifClass(n.text)}"><div class="muted small">${esc(n.at)}</div>${esc(n.text)}</div>`).join("")
    : `<div class="empty">알림이 없습니다.</div>`;
}

async function renderTopbar(activePage) {
  const status = await api("/status");
  const topbar = document.getElementById("topbar");
  if (topbar) {
    const badges = [];
    badges.push(status.paused ? `<span class="badge red">AI 정지됨</span>` : `<span class="badge green">AI 작동 중</span>`);
    if (status.mock_mode) badges.push(`<span class="badge">MOCK</span>`);
    if (status.dry_run) badges.push(`<span class="badge amber">DRY RUN</span>`);
    topbar.innerHTML = `
      <a href="/ui/admin.html">관리</a>
      <a href="/ui/test.html"${activePage === "test" ? ' class="active"' : ""}>테스트</a>
      <span class="spacer"></span>${badges.join(" ")}
      <button type="button" onclick="logoutAdmin()">로그아웃</button>`;
  }
  return status;
}
