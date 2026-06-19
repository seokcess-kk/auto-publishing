// popup UI 로직 — bridge 상태 / 활성 작업 / 큐 통계 표시.

const BRIDGE = "http://localhost:5757";

async function refresh() {
  // enabled 토글
  const { enabled } = await chrome.storage.local.get("enabled");
  document.getElementById("enabled").checked = enabled !== false;

  // 활성 작업
  const { active_item } = await chrome.storage.local.get("active_item");
  const active = document.getElementById("active");
  if (active_item) {
    active.innerHTML = `<strong>활성 작업:</strong> ${(active_item.title || "").slice(0, 60)}<br><code>id=${active_item.id.slice(0, 8)}</code>`;
  } else {
    active.textContent = "활성 작업 없음";
  }

  // bridge healthz
  const bridge = document.getElementById("bridge");
  try {
    const r = await fetch(`${BRIDGE}/healthz`);
    if (r.ok) {
      const d = await r.json();
      bridge.innerHTML = `<span class="ok">✓ bridge OK</span> — pending ${d.pending || 0}건`;
    } else {
      bridge.innerHTML = `<span class="err">✗ bridge ${r.status}</span>`;
    }
  } catch (e) {
    bridge.innerHTML = `<span class="err">✗ bridge 미응답</span> — <code>python -m pipelines.tistory_bridge</code> 실행 필요`;
  }

  // 큐 통계
  const counts = document.getElementById("counts");
  try {
    const r = await fetch(`${BRIDGE}/list`);
    if (r.ok) {
      const items = await r.json();
      const tally = { pending: 0, claimed: 0, done: 0, failed: 0 };
      for (const it of items) tally[it.status] = (tally[it.status] || 0) + 1;
      counts.innerHTML = `pending <strong>${tally.pending}</strong> · claimed ${tally.claimed} · done ${tally.done} · failed ${tally.failed}`;
    }
  } catch (e) {
    counts.textContent = "큐 통계 — bridge 응답 없음";
  }
}

document.getElementById("enabled").addEventListener("change", async (e) => {
  await chrome.storage.local.set({ enabled: e.target.checked });
});

document.getElementById("poll-now").addEventListener("click", async () => {
  // background 의 alarm 외에 즉시 트리거 — 새로 처리하라고 메시지
  try {
    await chrome.runtime.sendMessage({ type: "poll-now" });
  } catch (e) {}
  setTimeout(refresh, 500);
});

document.getElementById("clear-active").addEventListener("click", async () => {
  // stuck 된 활성 작업 수동 해제 (이미 발행됐는데 active 가 안 풀리는 경우)
  try {
    await chrome.runtime.sendMessage({ type: "clear-active" });
  } catch (e) {}
  setTimeout(refresh, 300);
});

refresh();
setInterval(refresh, 3000);
