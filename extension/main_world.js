// Main-world script — 페이지 영역에서 tinymce 등 page globals 접근.
//
// Manifest v3 의 "world": "MAIN" 으로 로드되어 content.js (isolated world) 와
// 격리된다. content.js 와는 window.postMessage 로 통신.
//
// chrome.runtime/storage 같은 extension API 는 main world 에선 사용 불가 —
// 통신은 모두 postMessage 사용.

(function () {
  console.log("[ap-mw] main_world.js loaded @", location.href);

  window.addEventListener("message", async (ev) => {
    if (ev.source !== window) return;
    const data = ev.data;
    if (!data || typeof data.type !== "string" || !data.type.startsWith("ap-mw-req:")) return;

    const cmd = data.type.slice("ap-mw-req:".length);
    const requestId = data.requestId;
    let result;
    try {
      result = await handle(cmd, data.payload || {});
    } catch (e) {
      result = { ok: false, error: e.message || String(e) };
    }
    window.postMessage({ type: "ap-mw-res", requestId, result }, "*");
  });

  async function handle(cmd, payload) {
    if (cmd === "ping") {
      return { ok: true, tinymce: !!window.tinymce, hasActive: !!(window.tinymce && window.tinymce.activeEditor) };
    }
    if (cmd === "wait-tinymce") {
      const timeoutMs = payload.timeoutMs || 30000;
      const start = Date.now();
      while (Date.now() - start < timeoutMs) {
        if (window.tinymce && window.tinymce.activeEditor) {
          return { ok: true, editorId: window.tinymce.activeEditor.id };
        }
        await sleep(200);
      }
      return { ok: false, error: "tinymce.activeEditor timeout" };
    }
    if (cmd === "set-content") {
      const editor = window.tinymce && window.tinymce.activeEditor;
      if (!editor) {
        return { ok: false, error: "tinymce.activeEditor 없음" };
      }
      const html = payload.html || "";

      // (1) tinymce 본 API — editor DOM 업데이트
      editor.setContent(html, { format: "raw" });

      // (2) iframe body 직접 innerHTML — keditor 래퍼가 listen 하는 영역
      try {
        const ifr = document.getElementById(editor.id + "_ifr");
        if (ifr && ifr.contentDocument && ifr.contentDocument.body) {
          ifr.contentDocument.body.innerHTML = html;
          // input/change/keyup 이벤트 — keditor / 기타 listener 트리거
          for (const t of ["input", "change", "keyup"]) {
            ifr.contentDocument.body.dispatchEvent(new Event(t, { bubbles: true }));
          }
        }
      } catch (e) {
        console.warn("[ap-mw] iframe body 갱신 실패 (무시):", e.message);
      }

      // (3) 원본 textarea (selector='#editor-tistory') 도 값 박아 넣음
      try {
        const ta = document.getElementById(editor.id);
        if (ta) {
          ta.value = html;
          ta.dispatchEvent(new Event("input", { bubbles: true }));
          ta.dispatchEvent(new Event("change", { bubbles: true }));
        }
      } catch (e) {
        console.warn("[ap-mw] textarea 갱신 실패 (무시):", e.message);
      }

      // (4) tinymce save() — internal serialization → textarea
      try { editor.save(); } catch (e) {}
      // (5) dirty 마킹 + change 이벤트 broadcast
      try {
        editor.setDirty(true);
        editor.fire("change");
        editor.fire("input");
        editor.fire("NodeChange");
      } catch (e) {}

      // 검증
      const finalLen = (editor.getContent() || "").length;
      const taLen = (document.getElementById(editor.id)?.value || "").length;
      return { ok: true, editorLen: finalLen, textareaLen: taLen };
    }
    if (cmd === "diagnose") {
      const editor = window.tinymce && window.tinymce.activeEditor;
      const out = {
        has_tinymce: !!window.tinymce,
        has_editor: !!editor,
        editor_id: editor?.id,
        editor_settings_selector: editor?.settings?.selector,
        editor_content_len: editor ? (editor.getContent() || "").length : 0,
        has_keditor: !!window.keditor,
        keditor_keys: window.keditor ? Object.keys(window.keditor).slice(0, 20) : [],
        textarea_len: editor ? (document.getElementById(editor.id)?.value || "").length : 0,
        window_keys_editor_like: Object.keys(window).filter(k => /editor|keditor|kakao/i.test(k)).slice(0, 20),
      };
      return { ok: true, info: out };
    }
    if (cmd === "get-content") {
      if (!window.tinymce || !window.tinymce.activeEditor) {
        return { ok: false, error: "tinymce.activeEditor 없음" };
      }
      return { ok: true, html: window.tinymce.activeEditor.getContent() };
    }
    return { ok: false, error: "unknown cmd: " + cmd };
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }
})();
