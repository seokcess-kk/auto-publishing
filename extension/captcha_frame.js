// captcha_frame.js — DKAPTCHA iframe (dkaptcha.kakao.com) 안에서 실행되는 content script.
//
// content.js (메인 페이지) 가 텔레그램 답안 받으면 background → 이 iframe 으로
// "fill-captcha" 메시지 전달 → 여기서 input 채우고 '답변 제출' 클릭.
//
// 중요: paste (value 직접 설정) 만 하면 DKAPTCHA 의 submit 버튼이 disabled 상태로
// 남는다. 한 글자씩 keyboard event + native value setter 로 실제 타이핑을 시뮬레이션
// 해야 button 이 활성화된다.

console.log("[ap-dkap] captcha_frame.js loaded @", location.href);

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// React/Vue framework 의 value 동기화를 위해 native setter 사용
const _nativeInputValueSetter = Object.getOwnPropertyDescriptor(
  window.HTMLInputElement.prototype, "value"
).set;

function setReactValue(el, value) {
  _nativeInputValueSetter.call(el, value);
}

function makeKeyEvent(type, char, opts = {}) {
  const code = opts.keyCode != null ? opts.keyCode : char.charCodeAt(0);
  const ev = new KeyboardEvent(type, {
    key: opts.key || char,
    code: opts.code || "Key" + (char.toUpperCase().match(/[A-Z]/) ? char.toUpperCase() : "A"),
    bubbles: true,
    cancelable: true,
    composed: true,
  });
  try {
    Object.defineProperty(ev, "keyCode", { value: code, configurable: true });
    Object.defineProperty(ev, "which", { value: code, configurable: true });
    Object.defineProperty(ev, "charCode", { value: code, configurable: true });
  } catch (e) {}
  return ev;
}

async function typeChar(input, char) {
  input.dispatchEvent(makeKeyEvent("keydown", char));
  setReactValue(input, input.value + char);
  input.dispatchEvent(new InputEvent("input", {
    data: char, inputType: "insertText", bubbles: true, cancelable: true,
  }));
  input.dispatchEvent(makeKeyEvent("keypress", char));
  input.dispatchEvent(makeKeyEvent("keyup", char));
  await sleep(70);
}

async function pressBackspace(input) {
  const evOpts = { key: "Backspace", code: "Backspace", keyCode: 8 };
  input.dispatchEvent(makeKeyEvent("keydown", "\b", evOpts));
  setReactValue(input, input.value.slice(0, -1));
  input.dispatchEvent(new InputEvent("input", {
    inputType: "deleteContentBackward", bubbles: true, cancelable: true,
  }));
  input.dispatchEvent(makeKeyEvent("keyup", "\b", evOpts));
  await sleep(60);
}

async function typeIntoInput(input, text) {
  input.focus();
  input.click();
  await sleep(100);
  setReactValue(input, "");
  input.dispatchEvent(new Event("input", { bubbles: true }));
  await sleep(80);

  // ─── PHASE 1: 한 글자씩 타이핑 ─────────────────────────────────────────
  console.log("[ap-dkap] PHASE 1 — 타이핑 시작");
  for (const char of text) {
    await typeChar(input, char);
  }
  console.log("[ap-dkap] PHASE 1 완료. value=", input.value);
  await sleep(200);

  // ─── PHASE 2: 백스페이스로 전체 삭제 (dirty state 강제 활성) ───────────
  console.log("[ap-dkap] PHASE 2 — 백스페이스로 지움");
  while (input.value.length > 0) {
    await pressBackspace(input);
  }
  console.log("[ap-dkap] PHASE 2 완료. value=", input.value);
  await sleep(200);

  // ─── PHASE 3: 다시 한 글자씩 타이핑 ───────────────────────────────────
  console.log("[ap-dkap] PHASE 3 — 재타이핑");
  for (const char of text) {
    await typeChar(input, char);
  }
  console.log("[ap-dkap] PHASE 3 완료. value=", input.value);

  input.dispatchEvent(new Event("change", { bubbles: true }));
  input.blur();
  await sleep(150);
  input.focus();
  return true;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type !== "fill-captcha") return;
  console.log("[ap-dkap] fill-captcha 수신:", msg.answer);

  (async () => {
    // input 탐색
    const input = document.querySelector('input[placeholder*="정답"]') ||
                  document.querySelector('input[placeholder*="답"]') ||
                  document.querySelector('input[type="text"]') ||
                  document.querySelector('input:not([type])');
    if (!input) {
      const inputs = [...document.querySelectorAll("input")].map(i => ({
        type: i.type, name: i.name, placeholder: i.placeholder, cls: i.className.slice(0, 80),
      }));
      console.error("[ap-dkap] input 미발견. all inputs:", JSON.stringify(inputs));
      sendResponse({ ok: false, error: "input not found" });
      return;
    }
    console.log("[ap-dkap] input 발견 placeholder=", input.placeholder);

    // 한 글자씩 타이핑 (paste 가 아니라 실제 키 입력)
    await typeIntoInput(input, msg.answer);
    console.log("[ap-dkap] ✓ 타이핑 완료. value=", input.value);

    // submit 버튼 활성화 대기 (최대 3초)
    let submitBtn = null;
    const submitDeadline = Date.now() + 3000;
    while (Date.now() < submitDeadline) {
      const btns = [...document.querySelectorAll("button, input[type='submit'], a[role='button']")];
      for (const b of btns) {
        if (b.offsetParent === null && b.tagName !== "INPUT") continue;
        const t = (b.innerText || b.value || "").trim();
        if (t.includes("답변 제출") || t === "제출" || t.includes("확인") || t.includes("Submit")) {
          submitBtn = b;
          break;
        }
      }
      if (submitBtn && !submitBtn.disabled) break;
      await sleep(200);
    }

    if (!submitBtn) {
      const btnDump = [...document.querySelectorAll("button")]
        .filter(b => b.offsetParent)
        .map(b => ({ text: (b.innerText || "").trim().slice(0, 30), disabled: b.disabled, id: b.id }))
        .filter(b => b.text);
      console.error("[ap-dkap] '답변 제출' 버튼 미발견. visible buttons:", JSON.stringify(btnDump));
      sendResponse({ ok: false, error: "submit button not found" });
      return;
    }

    if (submitBtn.disabled) {
      console.warn("[ap-dkap] submit 버튼이 여전히 disabled — force click 시도");
      submitBtn.removeAttribute("disabled");
    }

    submitBtn.click();
    console.log("[ap-dkap] ✓ 답변 제출 클릭 (disabled=" + submitBtn.disabled + ")");
    sendResponse({ ok: true });
  })();

  return true; // async sendResponse
});
