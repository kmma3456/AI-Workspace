/** 직원 데스크 공통: 활동 타임라인 · 사이드바 업체명 */
function fmtInboxTime(iso) {
  const d = new Date(iso);
  const now = new Date();
  const tm = d.toLocaleString("ko-KR", { hour: "numeric", minute: "2-digit", hour12: true });
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return tm;
  const md = `${d.getMonth() + 1}. ${d.getDate()}.`;
  if (d.getFullYear() !== now.getFullYear()) return `${d.getFullYear()}. ${md} ${tm}`;
  return `${md} ${tm}`;
}

/** 채팅 말풍선 — 항상 날짜 + 시간 */
function fmtChatMessageTime(iso) {
  const d = new Date(iso);
  const now = new Date();
  const tm = d.toLocaleString("ko-KR", { hour: "numeric", minute: "2-digit", hour12: true });
  const md = `${d.getMonth() + 1}. ${d.getDate()}.`;
  if (d.getFullYear() !== now.getFullYear()) return `${d.getFullYear()}. ${md} ${tm}`;
  return `${md} ${tm}`;
}

function activityCustomerLabel(phone, conversations = []) {
  const row = conversations.find((r) => r.phone === phone);
  if (row?.name) return `${row.name} 고객님`;
  return `${fmtPhone(phone)} 고객님`;
}

function activityTitle(m, conversations = []) {
  const who = esc(activityCustomerLabel(m.phone, conversations));
  if (m.source === "ai") return `${who}에게 <strong>AI</strong>가 답변 완료`;
  if (m.source === "customer") return `${who} 상담 진행 중`;
  if (m.source === "template") return `${who}에게 자동 문자 발송`;
  if (m.source === "owner") return `${who}에게 사장님이 답장`;
  return `${who} · ${esc(m.source)}`;
}

function activitySubtext(m) {
  const t = (m.content || "").trim();
  if (!t) return "";
  const short = t.length > 80 ? `${t.slice(0, 80)}…` : t;
  if (m.source === "template" && /부재|전화/.test(t)) return "(부재중 전화 → 문자 안내)";
  return `(${short})`;
}

function activityTone(source) {
  if (source === "customer") return "purple";
  if (source === "template") return "green";
  if (source === "owner") return "orange";
  return "blue";
}

function activityIcon(source) {
  if (source === "customer") return "🎧";
  if (source === "template") return "📞";
  if (source === "owner") return "📋";
  return "💬";
}

function renderActivityFeed(container, rows, emptyText, conversations = []) {
  container.innerHTML = rows.length
    ? rows.map((m) => `
        <div class="dash-tl-item tone-${activityTone(m.source)}">
          <div class="dash-tl-rail">
            <div class="dash-tl-icon" aria-hidden="true">${activityIcon(m.source)}</div>
          </div>
          <div class="dash-tl-body">
            <div class="dash-tl-time">${fmtChatMessageTime(m.created_at)}</div>
            <div class="dash-tl-title">${activityTitle(m, conversations)}</div>
            ${activitySubtext(m) ? `<div class="dash-tl-sub">${esc(activitySubtext(m))}</div>` : ""}
          </div>
        </div>`).join("")
    : `<div class="dash-empty">${emptyText}</div>`;
}

function renderNotificationsFeed(container, items) {
  container.innerHTML = items.length
    ? items.map((n) => `
        <div class="dash-tl-item tone-orange">
          <div class="dash-tl-rail">
            <div class="dash-tl-icon" aria-hidden="true">🔔</div>
          </div>
          <div class="dash-tl-body">
            <div class="dash-tl-time">${esc(n.at)}</div>
            <div class="dash-tl-title">${esc(n.text)}</div>
          </div>
        </div>`).join("")
    : `<div class="dash-empty">알림이 없습니다</div>`;
}

function renderDashPagination(container, { page, pageSize, total, onPage }) {
  if (!container) return;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const p = Math.min(Math.max(1, page), pages);
  if (total <= pageSize) {
    container.innerHTML = "";
    container.hidden = true;
    return;
  }
  container.hidden = false;
  const from = (p - 1) * pageSize + 1;
  const to = Math.min(p * pageSize, total);
  const nums = [];
  const window = 5;
  let start = Math.max(1, p - Math.floor(window / 2));
  let end = Math.min(pages, start + window - 1);
  start = Math.max(1, end - window + 1);
  for (let i = start; i <= end; i++) nums.push(i);
  container.innerHTML = `
    <div class="dash-pagination-summary">${from.toLocaleString("ko-KR")}–${to.toLocaleString("ko-KR")} / ${total.toLocaleString("ko-KR")}건</div>
    <div class="dash-pagination-controls">
      <button type="button" class="dash-page-btn" data-page="prev" ${p <= 1 ? "disabled" : ""} aria-label="이전">‹</button>
      ${nums.map((n) => `<button type="button" class="dash-page-btn${n === p ? " is-active" : ""}" data-page="${n}">${n}</button>`).join("")}
      <button type="button" class="dash-page-btn" data-page="next" ${p >= pages ? "disabled" : ""} aria-label="다음">›</button>
    </div>`;
  container.querySelectorAll("[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      const v = btn.dataset.page;
      let next = p;
      if (v === "prev") next = p - 1;
      else if (v === "next") next = p + 1;
      else next = Number(v);
      if (next !== p && onPage) onPage(next);
    });
  });
}

function applyIntakeNavBadge(status) {
  const intakePending = status?.intake_pending ?? 0;
  const navBadge = document.getElementById("nav-intake-badge");
  if (!navBadge) return intakePending;
  if (intakePending > 0) {
    navBadge.hidden = false;
    navBadge.textContent = String(intakePending);
  } else {
    navBadge.hidden = true;
    navBadge.textContent = "";
  }
  return intakePending;
}

async function refreshSidebarBusiness() {
  try {
    const status = await api("/status");
    const el = document.getElementById("sidebar-business");
    if (el) el.textContent = status.business_name || "OO업체";
    applyIntakeNavBadge(status);
    return status;
  } catch {
    return null;
  }
}

function mobileNavPageTitle() {
  const active = document.querySelector(".dash-nav a.active");
  if (!active) {
    const t = document.title.split("-")[0].trim();
    return t || "AI 직원";
  }
  const clone = active.cloneNode(true);
  clone.querySelectorAll(".dash-nav-badge").forEach((el) => el.remove());
  return clone.textContent.replace(/\s+/g, " ").trim();
}

function initDashMobileNav() {
  const app = document.body;
  if (!app.classList.contains("dash-app")) return;
  const main = document.querySelector(".dash-main");
  const sidebar = document.querySelector(".dash-sidebar");
  if (!main || !sidebar) return;

  sidebar.id = sidebar.id || "dash-sidebar";

  let backdrop = document.querySelector(".dash-mobile-backdrop");
  if (!backdrop) {
    backdrop = document.createElement("div");
    backdrop.className = "dash-mobile-backdrop";
    backdrop.hidden = true;
    app.appendChild(backdrop);
  }

  if (!document.getElementById("dash-mobile-bar")) {
    const bar = document.createElement("header");
    bar.className = "dash-mobile-bar";
    bar.id = "dash-mobile-bar";
    bar.innerHTML = `
      <button type="button" class="dash-mobile-menu-btn" id="dash-menu-btn" aria-expanded="false" aria-controls="dash-sidebar">
        <span class="dash-mobile-menu-ico" aria-hidden="true"></span>
        <span class="sr-only">메뉴 열기</span>
      </button>
      <p class="dash-mobile-bar-title" id="dash-mobile-page-title"></p>`;
    main.insertBefore(bar, main.firstChild);
  }

  const titleEl = document.getElementById("dash-mobile-page-title");
  if (titleEl) titleEl.textContent = mobileNavPageTitle();

  const setOpen = (open) => {
    sidebar.classList.toggle("is-open", open);
    backdrop.hidden = !open;
    app.classList.toggle("dash-nav-open", open);
    const btn = document.getElementById("dash-menu-btn");
    if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  };

  document.getElementById("dash-menu-btn")?.addEventListener("click", () => {
    setOpen(!sidebar.classList.contains("is-open"));
  });
  backdrop.addEventListener("click", () => setOpen(false));
  sidebar.querySelectorAll(".dash-nav a").forEach((a) => {
    a.addEventListener("click", () => setOpen(false));
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setOpen(false);
  });
}

/** 고객 대화 등 2열 그리드 — 모바일에서 목록 ↔ 채팅 전환 */
function syncMobileChatSplit(gridEl, hasSelection) {
  if (!gridEl) return;
  const mq = window.matchMedia("(max-width: 900px)");
  const apply = () => {
    const mobile = mq.matches;
    const open = mobile && !!hasSelection();
    gridEl.classList.toggle("is-chat-open", open);
    const back = document.getElementById("btn-chat-back");
    if (back) back.hidden = !open;
  };
  apply();
  if (!gridEl.dataset.mobileChatBound) {
    gridEl.dataset.mobileChatBound = "1";
    mq.addEventListener("change", apply);
    document.getElementById("btn-chat-back")?.addEventListener("click", () => {
      if (typeof gridEl._mobileChatClear === "function") gridEl._mobileChatClear();
    });
  }
  gridEl._mobileChatRefresh = apply;
  return apply;
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("btn-logout")?.addEventListener("click", () => logoutAdmin());
  initDashMobileNav();
});
