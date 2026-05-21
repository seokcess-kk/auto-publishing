// Auto Publishing — Tistory editor content script (isolated world).
//
// 페이지의 window.tinymce 등 globals 는 isolated world 에서 접근 불가.
// main_world.js (world: MAIN) 와 window.postMessage 로 RPC.

(async () => {
  console.log("[ap] content.js loaded @", location.href);

  if (window.__autoPublishingRan) {
    console.log("[ap] 이미 실행됨 — skip");
    return;
  }
  window.__autoPublishingRan = true;

  // ─── main world RPC ──────────────────────────────────────────────────────
  let _reqSeq = 0;
  function mw(cmd, payload = {}) {
    return new Promise((resolve, reject) => {
      const requestId = "r" + (++_reqSeq) + "_" + Date.now();
      const timeout = setTimeout(() => {
        window.removeEventListener("message", handler);
        reject(new Error("main world RPC timeout: " + cmd));
      }, 60000);
      const handler = (ev) => {
        if (ev.source !== window) return;
        const d = ev.data;
        if (d && d.type === "ap-mw-res" && d.requestId === requestId) {
          clearTimeout(timeout);
          window.removeEventListener("message", handler);
          resolve(d.result);
        }
      };
      window.addEventListener("message", handler);
      window.postMessage({ type: "ap-mw-req:" + cmd, requestId, payload }, "*");
    });
  }

  // ─── 아이템 조회 ─────────────────────────────────────────────────────────
  let item = null;
  try {
    const r = await chrome.runtime.sendMessage({ type: "get-item" });
    item = r?.item || null;
  } catch (e) {
    console.warn("[ap] background 통신 실패:", e.message);
    return;
  }
  if (!item) {
    console.log("[ap] 활성 작업 없음 — 자동 처리 skip");
    return;
  }

  const params = new URLSearchParams(location.search);
  const apid = params.get("_apid");
  if (apid && apid !== item.id) {
    console.warn("[ap] _apid 불일치 — skip");
    return;
  }

  // URL 에 type=post 가 없으면 editor 가 초기화 안 됨 — 자동 보정.
  if (!params.has("type")) {
    console.log("[ap] type=post 없음 — URL 보정 후 재로드");
    const u = new URL(location.href);
    u.searchParams.set("type", "post");
    u.searchParams.set("_apid", item.id);
    location.href = u.toString();
    return;
  }

  console.log("[ap] 자동 처리 시작:", item.title);

  // ─── DOM 헬퍼 ────────────────────────────────────────────────────────────
  async function waitFor(selector, timeoutMs = 15000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const el = document.querySelector(selector);
      if (el && el.offsetParent !== null) return el;
      await new Promise((r) => setTimeout(r, 200));
    }
    throw new Error(`waitFor timeout: ${selector}`);
  }

  // ─── DKAPTCHA 자동 처리 (텔레그램 relay) ─────────────────────────────────
  async function handleCaptchaIfPresent(itemId) {
    // 공개 발행 클릭 후 위젯 등장까지 최대 8초 대기
    const deadlineDetect = Date.now() + 8000;
    let captchaContainer = null;
    while (Date.now() < deadlineDetect) {
      // DKAPTCHA 가 자주 쓰는 selector 후보들
      const cand =
        document.querySelector('[class*="dkaptcha"]') ||
        document.querySelector('iframe[src*="dkaptcha"]') ||
        document.querySelector('iframe[src*="captcha"]') ||
        // dialog 안에 input[placeholder*="정답"] 가 있으면 캡차로 추정
        (document.querySelector('input[placeholder*="정답"]') ? document.body : null);
      if (cand) {
        captchaContainer = cand;
        break;
      }
      await new Promise((r) => setTimeout(r, 300));
    }
    if (!captchaContainer) {
      console.log("[ap] DKAPTCHA 위젯 미감지 (캡차 없는 슬롯이거나 다른 흐름) — 통상 navigation 대기로 진행");
      return false;
    }
    console.log("[ap] ✓ DKAPTCHA 위젯 감지 — 캡차 이미지 로드 대기");

    // 모달이 떠도 위성지도 이미지가 아직 로드 안 됐을 수 있음 — 실제 image 가
    // naturalHeight > 0 이 될 때까지 또는 최대 5초 대기 후 캡처.
    const imgLoadDeadline = Date.now() + 5000;
    while (Date.now() < imgLoadDeadline) {
      const imgs = [...document.querySelectorAll("img, canvas")].filter(el => el.offsetParent !== null);
      const hasLoadedImg = imgs.some(img =>
        (img.tagName === "IMG" && img.complete && img.naturalHeight > 50) ||
        (img.tagName === "CANVAS" && img.width > 50 && img.height > 50)
      );
      // 또는 iframe 안의 이미지
      const captchaIframe = document.querySelector('iframe[src*="captcha"], iframe[src*="dkaptcha"]');
      if (hasLoadedImg || captchaIframe) break;
      await new Promise(r => setTimeout(r, 200));
    }
    // 추가 안전 마진 — 텍스트 / 글자 채우기 placeholder 등 렌더 시간
    await new Promise(r => setTimeout(r, 1000));
    console.log("[ap] 이미지 로드 완료 추정 — 텔레그램 relay 시작");

    // 1) background 에 캡처 + 텔레그램 전송 요청
    let resp;
    try {
      resp = await chrome.runtime.sendMessage({ type: "captcha-needed", item_id: itemId });
    } catch (e) {
      console.error("[ap] background 통신 실패:", e);
      return false;
    }
    if (!resp?.ok) {
      console.error("[ap] 캡차 텔레그램 전송 실패:", resp);
      return false;
    }
    console.log("[ap] ✓ 텔레그램 발송 완료 — 본인 답글 대기 중...");

    // 2) bridge 에 답안 polling — 최대 8분
    const answerDeadline = Date.now() + 8 * 60_000;
    let answer = null;
    while (Date.now() < answerDeadline) {
      try {
        const r = await fetch(`http://localhost:5757/captcha/answer/${itemId}`);
        if (r.status === 200) {
          const d = await r.json();
          if (d.answer) {
            answer = d.answer;
            break;
          }
        }
      } catch (e) {}
      await new Promise((r) => setTimeout(r, 3000));
    }
    if (!answer) {
      console.warn("[ap] 캡차 답안 timeout (8분) — 사용자 수동 풀이 fallback");
      return false;
    }
    console.log("[ap] ✓ 답안 수신:", answer);

    // 3) 답안 input 탐색 — 메인 페이지 + 모든 same-origin iframe
    const ctx = findCaptchaContext();
    if (ctx) {
      console.log(`[ap] 답안 input 발견: ${ctx.where} (placeholder='${ctx.input.placeholder}')`);
      ctx.input.focus();
      ctx.input.value = answer;
      ctx.input.dispatchEvent(new Event("input", { bubbles: true }));
      ctx.input.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 300));

      let submitBtn = findSubmitButton(ctx.doc) || findSubmitButton(document);
      if (!submitBtn) {
        console.error("[ap] '답변 제출' 버튼 미발견");
        return false;
      }
      submitBtn.click();
      console.log("[ap] ✓ 답변 제출 클릭 (same-origin)");
      return true;
    }

    // cross-origin DKAPTCHA iframe — background 거쳐 captcha_frame.js 에 위임
    console.log("[ap] 메인 페이지에 input 없음 — cross-origin iframe 으로 위임 (captcha_frame.js)");
    try {
      const r = await chrome.runtime.sendMessage({
        type: "fill-captcha-in-iframe",
        answer: answer,
      });
      if (!r?.ok) {
        console.error("[ap] iframe 내 답안 입력 실패:", r);
        dumpCaptchaDOM();
        return false;
      }
      console.log("[ap] ✓ iframe 답안 입력 + 제출 완료");
      return true;
    } catch (e) {
      console.error("[ap] background 통신 실패:", e);
      return false;
    }
  }

  function findCaptchaContext() {
    // 메인 페이지 우선
    const phMatch = ['정답', '답안', '답을', 'answer'];
    function pickInput(doc) {
      const inputs = [...doc.querySelectorAll('input[type="text"], input:not([type])')];
      for (const inp of inputs) {
        const ph = (inp.placeholder || '').toLowerCase();
        if (phMatch.some(k => ph.includes(k.toLowerCase()))) return inp;
      }
      // 폴백: title/tag 가 아닌 visible 텍스트 input
      for (const inp of inputs) {
        if (inp.id === 'post-title-inp' || inp.id === 'tagText') continue;
        if (inp.offsetParent && (inp.maxLength > 0 && inp.maxLength < 20)) return inp;
      }
      return null;
    }
    const mainInp = pickInput(document);
    if (mainInp) return { input: mainInp, doc: document, where: 'main' };

    // iframe 안 (same-origin 접근 가능한 것만)
    const iframes = [...document.querySelectorAll('iframe')];
    for (const ifr of iframes) {
      try {
        const d = ifr.contentDocument;
        if (!d) continue;
        const inp = pickInput(d);
        if (inp) return { input: inp, doc: d, iframe: ifr, where: `iframe(${ifr.id || ifr.src.slice(0,40)})` };
      } catch (e) {}
    }
    return null;
  }

  function findSubmitButton(doc) {
    const btns = [...doc.querySelectorAll('button, a[role="button"]')];
    for (const b of btns) {
      if (b.offsetParent === null) continue;
      const t = (b.innerText || '').trim();
      if (t.includes('답변 제출') || t === '제출' || t.includes('확인')) return b;
    }
    return null;
  }

  function dumpCaptchaDOM() {
    function snapshot(doc, label) {
      return {
        label,
        inputs: [...doc.querySelectorAll('input')]
          .map(i => ({ type: i.type, id: i.id, name: i.name, placeholder: i.placeholder,
                       maxlength: i.maxLength, visible: !!i.offsetParent,
                       cls: i.className.slice(0,60) })),
        buttons: [...doc.querySelectorAll('button')]
          .filter(b => b.offsetParent)
          .map(b => ({ text: (b.innerText||'').trim().slice(0,30), id: b.id }))
          .filter(b => b.text),
      };
    }
    const out = [snapshot(document, 'main')];
    for (const ifr of document.querySelectorAll('iframe')) {
      try {
        if (ifr.contentDocument) {
          out.push({ ...snapshot(ifr.contentDocument, 'iframe'), src: (ifr.src || '').slice(0,80), id: ifr.id });
        } else {
          out.push({ label: 'iframe (no contentDocument)', src: (ifr.src || '').slice(0,80), id: ifr.id });
        }
      } catch (e) {
        out.push({ label: 'iframe (cross-origin)', src: (ifr.src || '').slice(0,80), error: e.message });
      }
    }
    console.error("[ap] DOM dump:\n" + JSON.stringify(out, null, 2));
  }

  try {
    // 0) main world ping 으로 통신 확인
    const pong = await mw("ping");
    console.log("[ap] main_world ping:", JSON.stringify(pong));

    // 1) tinymce 초기화 대기 (main world 에서 활성 에디터 검사)
    console.log("[ap] tinymce.activeEditor 대기 중...");
    const wait = await mw("wait-tinymce", { timeoutMs: 60000 });
    if (!wait?.ok) {
      throw new Error("tinymce 초기화 실패: " + (wait?.error || "?"));
    }
    console.log("[ap] ✓ tinymce 준비됨 editor=" + wait.editorId);

    // 2) 제목 (isolated world DOM 으로 가능)
    const titleEl = await waitFor("textarea#post-title-inp");
    titleEl.focus();
    titleEl.value = item.title || "";
    titleEl.dispatchEvent(new Event("input", { bubbles: true }));
    titleEl.dispatchEvent(new Event("change", { bubbles: true }));
    console.log("[ap] ✓ 제목 입력");

    // 3) 본문 (main world 에서 tinymce.setContent)
    const setRes = await mw("set-content", { html: item.content || "" });
    if (!setRes?.ok) {
      throw new Error("setContent 실패: " + (setRes?.error || "?"));
    }
    console.log("[ap] ✓ 본문 setContent (길이=" + (item.content || "").length + ")");

    // 4) 태그
    if (item.tags && item.tags.length) {
      const tagInput = document.querySelector("#tagText");
      if (tagInput) {
        tagInput.focus();
        for (const tg of item.tags.slice(0, 10)) {
          tagInput.value = tg;
          tagInput.dispatchEvent(new Event("input", { bubbles: true }));
          tagInput.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter", code: "Enter", keyCode: 13 }));
          await new Promise((r) => setTimeout(r, 250));
        }
        console.log("[ap] ✓ 태그", item.tags.length, "개 입력");
      }
    }

    // 5) 완료 클릭
    const completeBtn = await waitFor("#publish-layer-btn");
    completeBtn.click();
    console.log("[ap] ✓ '완료' 클릭");

    await waitFor(".inner_editor_layer");
    await new Promise((r) => setTimeout(r, 800));

    // 6) visibility radio
    const v = item.visibility ?? 20;
    const vSel = { 0: 'input[value="0"]', 15: 'input[value="15"]', 20: 'input[value="20"]' }[v] || 'input[value="20"]';
    const vEl = document.querySelector(vSel);
    if (vEl) {
      vEl.click();
      console.log("[ap] ✓ visibility=" + v + " radio");
    } else {
      console.warn("[ap] visibility radio 미발견:", vSel);
    }
    await new Promise((r) => setTimeout(r, 400));

    // 7) 공개 발행 클릭 → DKAPTCHA 등장 가능
    const publishBtnId = v === 0 ? "save-private-btn" : "publish-btn";
    const publishBtn = document.querySelector("#" + publishBtnId);
    if (!publishBtn) {
      throw new Error("발행 버튼 미발견: #" + publishBtnId);
    }
    publishBtn.click();
    console.log("[ap] ✓ '" + (v === 0 ? "비공개 저장" : "공개 발행") + "' 클릭");

    // 7.5) DKAPTCHA 자동 처리 — 텔레그램 캡차 relay
    // 클릭 후 위젯 등장 대기 → 탭 캡처 → 텔레그램 → 본인 답글 → 자동 입력
    await handleCaptchaIfPresent(item.id);

    // 8) URL 이 글 ID 로 이동하면 발행 성공
    const blogHost = location.host;
    const startWait = Date.now();
    const WAIT_MAX = 5 * 60_000;
    while (Date.now() - startWait < WAIT_MAX) {
      const u = location.href;
      const m = u.match(new RegExp(`https?://${blogHost.replace(/\./g, "\\.")}/(\\d+)`));
      if (m && !u.includes("/manage")) {
        console.log("[ap] ✓✓✓ 발행 성공:", u);
        chrome.runtime.sendMessage({ type: "publish-done", url: u, post_id: m[1] });
        return;
      }
      if (u.includes("/manage/posts") && !u.includes("/newpost")) {
        console.log("[ap] ✓ /manage/posts 도달 — 발행 완료로 간주");
        chrome.runtime.sendMessage({ type: "publish-done", url: u, post_id: "" });
        return;
      }
      await new Promise((r) => setTimeout(r, 2000));
    }

    throw new Error("발행 완료 navigation timeout — 5분 내 캡차/페이지 이동 없음");
  } catch (e) {
    console.error("[ap] ✗ 실패:", e);
    try {
      chrome.runtime.sendMessage({ type: "publish-fail", error: e.message || String(e) });
    } catch (_) {}
  }
})();
