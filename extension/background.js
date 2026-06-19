// Auto Publishing — Tistory bridge background service worker.
//
// Manifest V3 service worker 는 idle 시 ~30초 후 unloaded. 다시 깨우려면
// chrome.alarms 또는 외부 이벤트가 필요. Chrome 120+ 에선 periodInMinutes
// 최소값 0.5 (30초) — 그 이하는 알람이 등록되지 않을 수 있다.

console.log("[bridge] background.js loaded @", new Date().toISOString());

const BRIDGE = "http://localhost:5757";
const ACTIVE_KEY = "active_item";
const POLL_PERIOD_MIN = 0.5; // 30초 — Chrome 알람 최소값
// Kakao SSO 쿠키가 무인/idle 상태에서 ~24h 안에 만료되는 경향. 6시간마다
// 각 블로그의 /manage 를 fetch 해서 활동 흔적을 남기고 토큰 회전을 유도한다.
// www.tistory.com 에는 /manage 가 없음 — 반드시 <blog>.tistory.com/manage.
const KEEPALIVE_PERIOD_MIN = 360;

// ─── 상태 관리 ────────────────────────────────────────────────────────────────

async function getEnabled() {
  const { enabled } = await chrome.storage.local.get("enabled");
  return enabled !== false;
}

async function setActiveItem(item) {
  await chrome.storage.local.set({ [ACTIVE_KEY]: item || null });
}

async function getActiveItem() {
  const r = await chrome.storage.local.get(ACTIVE_KEY);
  return r[ACTIVE_KEY] || null;
}

// ─── bridge HTTP ──────────────────────────────────────────────────────────────

async function fetchNext() {
  try {
    const r = await fetch(`${BRIDGE}/next`, { method: "GET" });
    if (r.status === 204) return null;
    if (!r.ok) {
      console.warn("[bridge] /next status", r.status);
      return null;
    }
    return await r.json();
  } catch (e) {
    console.warn("[bridge] /next 연결 실패 (bridge server 실행 중인지 확인):", e.message);
    return null;
  }
}

async function reportDone(id, url, postId) {
  try {
    await fetch(`${BRIDGE}/done`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, url, post_id: postId || "" }),
    });
  } catch (e) {
    console.warn("[bridge] /done 보고 실패", e);
  }
}

async function reportFail(id, error) {
  try {
    await fetch(`${BRIDGE}/fail`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, error: String(error).slice(0, 500) }),
    });
  } catch (e) {
    console.warn("[bridge] /fail 보고 실패", e);
  }
}

// ─── 작업 처리 ────────────────────────────────────────────────────────────────

// 큐 항목용 탭을 연다. 서비스워커만 살아있고 모든 Chrome 창이 닫힌 상태에서는
// chrome.tabs.create({url}) 가 "No current window" 예외로 실패한다 — 06-15 이후
// 큐가 조용히 막혔던 주원인. 열린 normal 창이 있으면 windowId 를 명시해 그 창에
// 탭을 만들고, 하나도 없으면 chrome.windows.create 로 창을 새로 띄워 자가복구한다.
async function openItemTab(url) {
  let wins = [];
  try {
    wins = await chrome.windows.getAll({ windowTypes: ["normal"] });
  } catch (e) {
    wins = [];
  }
  if (wins && wins.length > 0) {
    const win = wins.find((w) => w.focused) || wins[0];
    return await chrome.tabs.create({ windowId: win.id, url, active: true });
  }
  // 열린 창이 전혀 없음 → 새 창 생성. 사용자 방해 최소화 위해 focused:false.
  // 캡차가 필요해지면 captcha-needed 핸들러가 windows.update 로 포커스한다.
  console.warn("[bridge] 열린 Chrome 창 없음 → 새 창 생성 (자가복구)");
  const win = await chrome.windows.create({ url, focused: false });
  return win && win.tabs && win.tabs.length ? win.tabs[0] : null;
}

async function processOne() {
  console.log("[bridge] poll tick @", new Date().toISOString());
  if (!(await getEnabled())) {
    console.log("[bridge] 비활성 — skip");
    return;
  }

  const active = await getActiveItem();
  if (active) {
    // 활성 작업이 유효한지 점검 — 그 탭이 닫혔으면 stale 로 보고 해제한다.
    // 발행이 끝났는데(또는 사용자가 탭을 닫았는데) active 가 안 풀려 이후 모든
    // 신규 claim 을 영구히 막던 문제(같은 글 반복/정지) 자가치유.
    const s = await chrome.storage.local.get("active_tab_id");
    let tabAlive = false;
    if (s.active_tab_id != null) {
      try { await chrome.tabs.get(s.active_tab_id); tabAlive = true; }
      catch (e) { tabAlive = false; }
    }
    if (tabAlive) {
      console.log("[bridge] 활성 작업 진행 중:", active.id?.slice(0, 8), "— 새 작업 claim 건너뜀");
      return;
    }
    console.warn("[bridge] 활성 작업의 탭이 없음 — stale 로 판단해 해제:", active.id?.slice(0, 8));
    await setActiveItem(null);
    await chrome.storage.local.remove("active_tab_id");
    // 아래로 진행해 새 작업 claim
  }

  const item = await fetchNext();
  if (!item) {
    console.log("[bridge] pending 없음");
    return;
  }

  console.log("[bridge] claim", item.id?.slice(0, 8), item.title);
  await setActiveItem(item);

  const url = `https://${item.blog_name}.tistory.com/manage/newpost/?type=post&_apid=${item.id}`;
  try {
    const tab = await openItemTab(url);
    if (!tab || tab.id == null) throw new Error("탭/창 생성 결과 없음");
    await chrome.storage.local.set({ active_tab_id: tab.id });
    console.log("[bridge] 새 탭 열림 id=", tab.id);
  } catch (e) {
    console.error("[bridge] 탭 생성 실패:", e);
    // tab open 실패 → 작업 취소 + fail 보고
    await reportFail(item.id, "탭 생성 실패: " + e.message);
    await setActiveItem(null);
  }
}

// content script 가 보내는 메시지 처리
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  console.log("[bridge] onMessage:", msg.type, "from tab", sender.tab?.id);
  (async () => {
    if (msg.type === "get-item") {
      const item = await getActiveItem();
      sendResponse({ item });
      return;
    }
    if (msg.type === "publish-done") {
      const active = await getActiveItem();
      if (active) {
        await reportDone(active.id, msg.url || "", msg.post_id || "");
        await setActiveItem(null);
        await chrome.storage.local.remove("active_tab_id");
        try { chrome.notifications.create("", {
          type: "basic", iconUrl: "icon.png", title: "✓ 발행 완료",
          message: `${(active.title || "").slice(0, 60)}\n${msg.url || ""}`.trim(),
        }); } catch (e) {}
      }
      sendResponse({ ok: true });
      return;
    }
    if (msg.type === "publish-fail") {
      const active = await getActiveItem();
      if (active) {
        await reportFail(active.id, msg.error || "");
        await setActiveItem(null);
        await chrome.storage.local.remove("active_tab_id");
        try { chrome.notifications.create("", {
          type: "basic", iconUrl: "icon.png", title: "✗ 발행 실패",
          message: `${(active.title || "").slice(0, 60)}\n${msg.error || ""}`.trim(),
        }); } catch (e) {}
      }
      sendResponse({ ok: true });
      return;
    }
    if (msg.type === "poll-now") {
      processOne().catch(console.warn);
      sendResponse({ ok: true });
      return;
    }
    if (msg.type === "clear-active") {
      // 팝업의 '활성 작업 해제' — stuck 된 active 를 수동으로 비운다.
      await setActiveItem(null);
      await chrome.storage.local.remove("active_tab_id");
      console.warn("[bridge] 활성 작업 수동 해제됨");
      sendResponse({ ok: true });
      return;
    }
    if (msg.type === "fill-captcha-in-iframe") {
      // content.js (메인 페이지) 가 호출 — dkaptcha.kakao.com iframe 의
      // captcha_frame.js 로 답안 forward.
      const tabId = sender.tab?.id;
      if (!tabId) {
        sendResponse({ ok: false, error: "no tabId" });
        return;
      }
      try {
        const frames = await chrome.webNavigation.getAllFrames({ tabId });
        const dkapFrame = frames.find(f => (f.url || "").includes("dkaptcha.kakao.com"));
        if (!dkapFrame) {
          console.error("[bridge] dkaptcha iframe 미발견. frames:", frames.map(f => f.url?.slice(0,80)));
          sendResponse({ ok: false, error: "dkaptcha iframe not in tab" });
          return;
        }
        console.log("[bridge] dkaptcha frameId=", dkapFrame.frameId, " URL=", dkapFrame.url?.slice(0,80));
        const r = await chrome.tabs.sendMessage(
          tabId,
          { type: "fill-captcha", answer: msg.answer },
          { frameId: dkapFrame.frameId }
        );
        console.log("[bridge] captcha_frame 응답:", r);
        sendResponse(r || { ok: false, error: "no response from captcha_frame" });
      } catch (e) {
        console.error("[bridge] fill-captcha-in-iframe 예외:", e);
        sendResponse({ ok: false, error: e.message || String(e) });
      }
      return;
    }
    if (msg.type === "captcha-needed") {
      // content.js 가 DKAPTCHA 위젯을 감지 → 우리가 탭 캡처 후 bridge 경유 텔레그램 전송
      const tabId = sender.tab?.id;
      const windowId = sender.tab?.windowId;
      const itemId = msg.item_id || "";
      if (!tabId || !itemId) {
        sendResponse({ ok: false, error: "no tabId / item_id" });
        return;
      }
      try {
        // 캡차 탭을 active 로 만들어 captureVisibleTab 이 정확히 그 탭을 잡도록
        // (사용자가 다른 탭으로 옮겼을 가능성 차단)
        await chrome.tabs.update(tabId, { active: true });
        await chrome.windows.update(windowId, { focused: true });
        await new Promise(r => setTimeout(r, 500)); // 렌더 안정화
        const dataUrl = await chrome.tabs.captureVisibleTab(windowId, { format: "png" });
        const imageB64 = dataUrl.split(",")[1];
        console.log("[bridge] captureVisibleTab 캡처 길이:", imageB64.length);
        const r = await fetch(`${BRIDGE}/captcha/needed`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: itemId, image_b64: imageB64 }),
        });
        const result = await r.json().catch(() => ({}));
        console.log("[bridge] captcha-needed → bridge:", r.status, result);
        sendResponse(result);
      } catch (e) {
        console.error("[bridge] captureVisibleTab/POST 예외:", e);
        sendResponse({ ok: false, error: e.message || String(e) });
      }
      return;
    }
    sendResponse({ ok: false, error: "unknown msg" });
  })();
  return true;
});

// ─── 세션 keepalive ───────────────────────────────────────────────────────────

async function sessionKeepalive() {
  // bridge /healthz 응답의 blogs 목록을 사용 — .env 의 TISTORY_BLOG_* 매핑 그대로.
  let blogs = [];
  try {
    const r = await fetch(`${BRIDGE}/healthz`);
    if (r.ok) {
      const d = await r.json();
      blogs = Array.isArray(d.blogs) ? d.blogs : [];
    }
  } catch (e) {
    console.warn("[keepalive] bridge /healthz 실패:", e.message);
    return;
  }
  if (blogs.length === 0) {
    console.warn("[keepalive] bridge 가 blog 목록을 반환하지 않음 — skip");
    return;
  }
  for (const blog of blogs) {
    try {
      const url = `https://${blog}.tistory.com/manage/`;
      // credentials:include 로 사용자 Chrome 의 카카오/티스토리 쿠키를 그대로 사용.
      // /auth/login 으로 리다이렉트되면 세션 만료.
      const r = await fetch(url, { method: "GET", credentials: "include", redirect: "follow" });
      const expired = (r.url || "").includes("/auth/login");
      console.log(`[keepalive] ${blog} ${r.status} ${expired ? "(EXPIRED → /auth/login)" : "OK"}`);
    } catch (e) {
      console.warn(`[keepalive] ${blog} fetch 실패:`, e.message);
    }
  }
}

// ─── 주기 polling ─────────────────────────────────────────────────────────────

// chrome.alarms.create — Chrome 120+ 에선 unpacked 환경에서 최소 0.5분 (30초).
// 그 미만 값은 silent 하게 floor 되거나 무시될 수 있어 정확히 0.5 명시.
chrome.runtime.onInstalled.addListener(() => {
  console.log("[bridge] onInstalled — alarm 등록");
  chrome.alarms.create("poll", { periodInMinutes: POLL_PERIOD_MIN, delayInMinutes: 0.1 });
  chrome.alarms.create("keepalive", { periodInMinutes: KEEPALIVE_PERIOD_MIN, delayInMinutes: 1 });
});

chrome.runtime.onStartup.addListener(() => {
  console.log("[bridge] onStartup — alarm 재등록 + 즉시 1회");
  chrome.alarms.create("poll", { periodInMinutes: POLL_PERIOD_MIN, delayInMinutes: 0.1 });
  chrome.alarms.create("keepalive", { periodInMinutes: KEEPALIVE_PERIOD_MIN, delayInMinutes: 1 });
  processOne().catch(console.warn);
  sessionKeepalive().catch(console.warn);
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "poll") processOne().catch(console.warn);
  if (alarm.name === "keepalive") sessionKeepalive().catch(console.warn);
});

// ─── 발행 완료 감지 (webNavigation) ──────────────────────────────────────────
// content script 는 cross-document navigation 시 죽으므로 자체 감지 불가.
// 활성 탭이 글 URL 또는 /manage/posts 로 이동하면 발행 성공으로 간주.
async function markPublishDone(item, url, postId) {
  await reportDone(item.id, url, postId || "");
  await setActiveItem(null);
  await chrome.storage.local.remove("active_tab_id");
  try {
    chrome.notifications.create("", {
      type: "basic",
      iconUrl: "icon.png",
      title: "✓ 발행 완료",
      message: `${(item.title || "").slice(0, 60)}\n${url}`.trim(),
    });
  } catch (e) {}
}

// Tistory 발행 직후 글 목록 (/manage/posts) 로 보내고 실제 글 URL 로는 안 가는
// 경우 — admin API 로 최신 글 ID 를 직접 조회.
async function fetchLatestPostUrl(blogName) {
  const candidates = [
    `https://${blogName}.tistory.com/manage/posts.json?page=1&size=5`,
    `https://${blogName}.tistory.com/manage/posts.json?page=1`,
    `https://${blogName}.tistory.com/manage/post.json?action=list&page=1`,
  ];
  for (const apiUrl of candidates) {
    try {
      const r = await fetch(apiUrl, { credentials: "include" });
      if (!r.ok) continue;
      const d = await r.json();
      // Tistory 응답 스키마가 가변적 — 여러 키 후보
      const items =
        d.items || d.posts || d.entries ||
        (d.data && (d.data.items || d.data.posts || d.data.entries)) ||
        [];
      for (const it of items) {
        const id = it.id || it.postId || it.entryId;
        if (id) {
          return { url: `https://${blogName}.tistory.com/${id}`, postId: String(id) };
        }
      }
    } catch (e) {
      // 다음 후보로
    }
  }
  return null;
}

async function onTabNavigated(details) {
  if (details.frameId !== 0) return; // top frame only
  const s = await chrome.storage.local.get(["active_item", "active_tab_id"]);
  const item = s.active_item;
  if (!item) return;
  if (s.active_tab_id != null && details.tabId !== s.active_tab_id) return;

  const u = details.url || "";
  const blogHost = `${item.blog_name}.tistory.com`;
  const re = new RegExp(`https?://${blogHost.replace(/\./g, "\\.")}/(\\d+)(?:/|$|\\?|#)`);
  const m = u.match(re);

  // (A) 가장 정확한 케이스 — 글 ID URL 로 직접 이동
  if (m && !u.includes("/manage")) {
    console.log("[bridge] webNavigation 발행 감지 (post URL):", u);
    await markPublishDone(item, u, m[1]);
    return;
  }

  // (B) /manage/posts 도달 — 실제 글 URL 을 admin API 로 회수
  if (u.includes("/manage/posts") && !u.includes("/newpost")) {
    console.log("[bridge] /manage/posts 도달 — admin API 로 글 URL 조회 시도");
    // 2초 대기: 목록 인덱싱 반영 시간
    await new Promise((r) => setTimeout(r, 2000));
    const latest = await fetchLatestPostUrl(item.blog_name);
    if (latest) {
      console.log("[bridge] 최신 글 URL 회수:", latest.url);
      await markPublishDone(item, latest.url, latest.postId);
    } else {
      console.log("[bridge] 글 URL 회수 실패 — /manage/posts URL 로 fallback");
      await markPublishDone(item, u, "");
    }
    return;
  }
}
chrome.webNavigation.onCommitted.addListener(onTabNavigated);
chrome.webNavigation.onHistoryStateUpdated.addListener(onTabNavigated);

// service worker module 평가 시점에 alarm 등록 보장 (onInstalled 가 안 불릴 수도)
chrome.alarms.get("poll", (a) => {
  if (!a) {
    console.log("[bridge] alarm 'poll' 미등록 → 등록");
    chrome.alarms.create("poll", { periodInMinutes: POLL_PERIOD_MIN, delayInMinutes: 0.1 });
  } else {
    console.log("[bridge] alarm 'poll' 이미 등록됨 period=", a.periodInMinutes);
  }
});

// 즉시 1회 실행 — service worker 가 깨어난 직후
processOne().catch(console.warn);
