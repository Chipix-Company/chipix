/**
 * Landing demo: teaser hit-layer → invite animation → fullscreen overlay.
 */
(function () {
  const DEMO_SRC = "demo/?fs=1";
  const HISTORY_KEY = "chipix-demo-fs";

  const stage = document.querySelector(".lp-demo-stage");
  const hitlayer = document.getElementById("lpDemoHitlayer");
  const hint = document.getElementById("lpDemoHint");
  const hintText = hint && hint.querySelector(".lp-demo-hint-text");
  const overlay = document.getElementById("lpDemoFs");
  const fsFrame = document.getElementById("lpDemoFsFrame");
  const btnBack = document.getElementById("lpDemoFsBack");
  const btnClose = document.getElementById("lpDemoFsClose");
  const btnReset = document.getElementById("lpDemoFsReset");

  if (!stage || !hitlayer || !overlay || !fsFrame) return;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const defaultHint = "Click to open full demo";
  let open = false;
  let nudgeTimer = null;
  let expanding = false;

  function setHint(text, flash) {
    if (!hintText) return;
    hintText.textContent = text;
    if (!hint || !flash || reduceMotion) return;
    hint.classList.remove("is-flash");
    void hint.offsetWidth;
    hint.classList.add("is-flash");
  }

  function playNudge(strong) {
    if (reduceMotion) {
      setHint("Open full demo to scroll & type", true);
      return;
    }
    stage.classList.remove("lp-demo-nudge", "lp-demo-nudge-strong");
    void stage.offsetWidth;
    stage.classList.add(strong ? "lp-demo-nudge-strong" : "lp-demo-nudge");
    setHint("Open full demo to scroll & type", true);
    clearTimeout(nudgeTimer);
    nudgeTimer = setTimeout(function () {
      stage.classList.remove("lp-demo-nudge", "lp-demo-nudge-strong");
      if (!open) setHint(defaultHint, false);
    }, 700);
  }

  function ensureFsSrc() {
    if (!fsFrame.getAttribute("src")) {
      fsFrame.setAttribute("src", DEMO_SRC);
    }
  }

  function openDemo(opts) {
    opts = opts || {};
    if (open || expanding) return;
    expanding = true;

    function show() {
      ensureFsSrc();
      overlay.hidden = false;
      overlay.setAttribute("aria-hidden", "false");
      document.body.classList.add("lp-demo-fs-open");
      open = true;
      expanding = false;
      stage.classList.remove("lp-demo-expanding", "lp-demo-nudge", "lp-demo-nudge-strong");
      setHint(defaultHint, false);
      if (btnBack) btnBack.focus();

      if (opts.pushState !== false) {
        try {
          history.pushState({ [HISTORY_KEY]: true }, "", "?demo=1");
        } catch (_) {
          /* ignore */
        }
      }
    }

    if (reduceMotion || opts.immediate) {
      show();
      return;
    }

    stage.classList.add("lp-demo-expanding");
    setTimeout(show, 280);
  }

  function closeDemo(opts) {
    opts = opts || {};
    if (!open) return;
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    document.body.classList.remove("lp-demo-fs-open");
    open = false;
    expanding = false;
    hitlayer.focus({ preventScroll: true });

    if (opts.historyBack) {
      try {
        if (history.state && history.state[HISTORY_KEY]) history.back();
        else if (/\bdemo=1\b/.test(location.search) || location.hash === "#demo-open") {
          history.replaceState(null, "", location.pathname + location.hash.replace("#demo-open", ""));
        }
      } catch (_) {
        /* ignore */
      }
    } else if (/\bdemo=1\b/.test(location.search)) {
      try {
        const url = new URL(location.href);
        url.searchParams.delete("demo");
        history.replaceState(null, "", url.pathname + url.search + url.hash);
      } catch (_) {
        /* ignore */
      }
    }
  }

  function isComposerZone(event) {
    const rect = hitlayer.getBoundingClientRect();
    const y = event.clientY - rect.top;
    return y >= rect.height * 0.72;
  }

  hitlayer.addEventListener("click", function (event) {
    if (isComposerZone(event)) {
      playNudge(true);
      setTimeout(function () {
        openDemo();
      }, reduceMotion ? 0 : 220);
      return;
    }
    openDemo();
  });

  hitlayer.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openDemo();
    }
  });

  hitlayer.addEventListener("mouseenter", function () {
    stage.classList.add("is-hover");
  });
  hitlayer.addEventListener("mouseleave", function () {
    stage.classList.remove("is-hover");
  });

  hitlayer.addEventListener(
    "wheel",
    function (event) {
      event.preventDefault();
      playNudge(false);
    },
    { passive: false }
  );

  let touchStartY = null;
  hitlayer.addEventListener(
    "touchstart",
    function (event) {
      if (event.touches[0]) touchStartY = event.touches[0].clientY;
    },
    { passive: true }
  );
  hitlayer.addEventListener(
    "touchmove",
    function (event) {
      if (touchStartY == null || !event.touches[0]) return;
      if (Math.abs(event.touches[0].clientY - touchStartY) > 8) {
        event.preventDefault();
        playNudge(false);
        touchStartY = event.touches[0].clientY;
      }
    },
    { passive: false }
  );

  document.querySelectorAll("[data-open-demo]").forEach(function (el) {
    el.addEventListener("click", function (event) {
      event.preventDefault();
      openDemo({ immediate: true });
    });
  });

  if (btnBack) btnBack.addEventListener("click", function () { closeDemo({ historyBack: true }); });
  if (btnClose) btnClose.addEventListener("click", function () { closeDemo({ historyBack: true }); });
  if (btnReset) {
    btnReset.addEventListener("click", function () {
      ensureFsSrc();
      try {
        fsFrame.contentWindow.location.reload();
      } catch (_) {
        fsFrame.setAttribute("src", DEMO_SRC);
      }
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      closeDemo({ historyBack: true });
    }
  });

  window.addEventListener("popstate", function () {
    if (open) closeDemo({ historyBack: false });
  });

  // Deep link: ?demo=1 or #demo-open
  const params = new URLSearchParams(location.search);
  if (params.get("demo") === "1" || location.hash === "#demo-open") {
    openDemo({ immediate: true, pushState: false });
  }

  window.openDemo = openDemo;
  window.closeDemo = closeDemo;
})();
