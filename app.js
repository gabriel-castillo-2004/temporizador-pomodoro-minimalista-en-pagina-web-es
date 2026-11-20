(() => {
  "use strict";

  const STORAGE_KEY = "pomodoro_minimal_state_v1";
  const MIN_MINUTES = 1;
  const MAX_MINUTES = 120;
  const TICK_INTERVAL_MS = 250;

  const DEFAULTS = {
    focusDuration: 25,
    shortBreakDuration: 5,
    longBreakDuration: 15,
    volume: 0.7,
    muted: false,
    theme: "dark",
    completedPomodoros: 0,
  };

  const PHASE_LABELS = {
    focus: "Enfoque",
    short: "Descanso corto",
    long: "Descanso largo",
  };

  const VALID_THEMES = new Set(["dark", "light"]);
  const VALID_PHASES = new Set(["focus", "short", "long"]);

  function clamp(num, min, max) {
    return Math.min(max, Math.max(min, num));
  }

  function sanitizeDuration(value, fallback) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return fallback;
    }
    const rounded = Math.round(value);
    if (rounded < MIN_MINUTES || rounded > MAX_MINUTES) {
      return fallback;
    }
    return rounded;
  }

  function sanitizeVolume(value, fallback) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return fallback;
    }
    if (value < 0 || value > 1) {
      return fallback;
    }
    return value;
  }

  function sanitizeTheme(value, fallback) {
    return typeof value === "string" && VALID_THEMES.has(value) ? value : fallback;
  }

  function sanitizeCount(value, fallback) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      return fallback;
    }
    const intVal = Math.floor(value);
    return intVal >= 0 ? intVal : fallback;
  }

  function loadAndSanitizeState() {
    let raw = null;
    try {
      raw = window.localStorage.getItem(STORAGE_KEY);
    } catch (_err) {
      return { ...DEFAULTS };
    }

    if (!raw) {
      return { ...DEFAULTS };
    }

    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return { ...DEFAULTS };
      }
      return {
        focusDuration: sanitizeDuration(parsed.focusDuration, DEFAULTS.focusDuration),
        shortBreakDuration: sanitizeDuration(
          parsed.shortBreakDuration,
          DEFAULTS.shortBreakDuration
        ),
        longBreakDuration: sanitizeDuration(
          parsed.longBreakDuration,
          DEFAULTS.longBreakDuration
        ),
        volume: sanitizeVolume(parsed.volume, DEFAULTS.volume),
        muted: typeof parsed.muted === "boolean" ? parsed.muted : DEFAULTS.muted,
        theme: sanitizeTheme(parsed.theme, DEFAULTS.theme),
        completedPomodoros: sanitizeCount(
          parsed.completedPomodoros,
          DEFAULTS.completedPomodoros
        ),
      };
    } catch (_err) {
      return { ...DEFAULTS };
    }
  }

  function saveState(persisted) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(persisted));
    } catch (_err) {
      // Ignore storage quota or private browsing errors.
    }
  }

  function formatTime(ms) {
    const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  function getDurationMs(settings, phase) {
    if (phase === "short") return settings.shortBreakDuration * 60 * 1000;
    if (phase === "long") return settings.longBreakDuration * 60 * 1000;
    return settings.focusDuration * 60 * 1000;
  }

  function init() {
    const persisted = loadAndSanitizeState();

    const state = {
      phase: "focus",
      status: "IDLE", // 'IDLE' | 'RUNNING' | 'PAUSED' | 'COMPLETED'
      remainingMs: getDurationMs(persisted, "focus"),
      targetTimestamp: null,
      alertActive: false,
    };

    let intervalId = null;
    let audioCtx = null;

    let lastRenderedSecond = null;
    let lastRenderedPhase = null;
    let lastRenderedStatus = null;

    const els = {
      phaseLabel: document.getElementById("phase-label"),
      timerDisplay: document.getElementById("timer-display"),
      phaseAlert: document.getElementById("phase-alert"),
      btnToggle: document.getElementById("btn-toggle"),
      btnReset: document.getElementById("btn-reset"),
      btnSkip: document.getElementById("btn-skip"),
      pomodoroCount: document.getElementById("pomodoro-count"),
      btnResetCount: document.getElementById("btn-reset-count"),
      btnTheme: document.getElementById("btn-theme"),
      btnMute: document.getElementById("btn-mute"),
      inputVolume: document.getElementById("input-volume"),
      inputFocus: document.getElementById("input-focus"),
      inputShortBreak: document.getElementById("input-short-break"),
      inputLongBreak: document.getElementById("input-long-break"),
      phaseTabs: document.querySelectorAll("[data-phase-select]"),
    };

    function persistCurrent() {
      saveState({
        focusDuration: persisted.focusDuration,
        shortBreakDuration: persisted.shortBreakDuration,
        longBreakDuration: persisted.longBreakDuration,
        volume: persisted.volume,
        muted: persisted.muted,
        theme: persisted.theme,
        completedPomodoros: persisted.completedPomodoros,
      });
    }

    function ensureAudioContext() {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      if (!audioCtx) {
        try {
          audioCtx = new Ctx();
        } catch (_err) {
          audioCtx = null;
          return null;
        }
      }
      if (audioCtx && audioCtx.state === "suspended" && typeof audioCtx.resume === "function") {
        audioCtx.resume().catch(() => {});
      }
      return audioCtx;
    }

    function playCompletionChime() {
      if (persisted.muted) return;
      const safeVolume = sanitizeVolume(persisted.volume, DEFAULTS.volume);
      if (safeVolume <= 0) return;

      const ctx = ensureAudioContext();
      if (!ctx) return;
      if (ctx.state === "suspended" && typeof ctx.resume === "function") {
        ctx.resume().catch(() => {});
      }

      try {
        const now = typeof ctx.currentTime === "number" ? ctx.currentTime : 0;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();

        osc.type = "sine";
        osc.frequency.setValueAtTime(660, now);
        osc.frequency.exponentialRampToValueAtTime(880, now + 0.18);

        gain.gain.setValueAtTime(safeVolume * 0.25, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.45);

        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.onended = () => {
          if (typeof osc.disconnect === "function") osc.disconnect();
          if (typeof gain.disconnect === "function") gain.disconnect();
        };

        osc.start(now);
        osc.stop(now + 0.45);
      } catch (_err) {
        // Ignore audio synthesis errors in restricted environments.
      }
    }

    function applyTheme() {
      document.documentElement.setAttribute("data-theme", persisted.theme);
      document.body.setAttribute("data-theme", persisted.theme);
      if (els.btnTheme) {
        const isDark = persisted.theme === "dark";
        els.btnTheme.textContent = isDark ? "Tema: Oscuro" : "Tema: Claro";
        els.btnTheme.setAttribute("aria-pressed", String(isDark));
      }
    }

    function syncAudioControls() {
      if (els.btnMute) {
        els.btnMute.textContent = persisted.muted ? "Sonido: Silenciado" : "Sonido: Activo";
        els.btnMute.setAttribute("aria-pressed", String(persisted.muted));
      }
      if (els.inputVolume) {
        els.inputVolume.value = String(persisted.volume);
      }
    }

    function syncDurationInputs() {
      if (els.inputFocus) els.inputFocus.value = String(persisted.focusDuration);
      if (els.inputShortBreak) els.inputShortBreak.value = String(persisted.shortBreakDuration);
      if (els.inputLongBreak) els.inputLongBreak.value = String(persisted.longBreakDuration);
    }

    function syncCounter() {
      if (els.pomodoroCount) {
        els.pomodoroCount.textContent = String(persisted.completedPomodoros);
      }
    }

    function syncAlertBanner() {
      const active = Boolean(state.alertActive);
      document.body.setAttribute("data-alert", String(active));
      document.body.classList.toggle("phase-completed", active);
      if (els.phaseAlert) {
        els.phaseAlert.setAttribute("data-alert", String(active));
        els.phaseAlert.classList.toggle("phase-completed", active);
        if (active) {
          els.phaseAlert.removeAttribute("hidden");
        } else {
          els.phaseAlert.setAttribute("hidden", "");
        }
      }
    }

    function renderTimerDifferential(force = false) {
      const currentSecond = Math.max(0, Math.ceil(state.remainingMs / 1000));
      const phaseChanged = state.phase !== lastRenderedPhase;
      const statusChanged = state.status !== lastRenderedStatus;
      const secondChanged = currentSecond !== lastRenderedSecond;

      if (!force && !phaseChanged && !statusChanged && !secondChanged) {
        return;
      }

      lastRenderedSecond = currentSecond;
      lastRenderedPhase = state.phase;
      lastRenderedStatus = state.status;

      const formatted = formatTime(state.remainingMs);
      const label = PHASE_LABELS[state.phase] || PHASE_LABELS.focus;

      if (els.timerDisplay) {
        els.timerDisplay.textContent = formatted;
      }
      if (els.phaseLabel) {
        els.phaseLabel.textContent = label;
      }

      document.title = `(${formatted}) ${label} - Pomodoro`;
      document.body.setAttribute("data-phase", state.phase);
      document.body.setAttribute("data-status", state.status);

      if (els.btnToggle) {
        if (state.status === "RUNNING") {
          els.btnToggle.textContent = "Pausar";
        } else if (state.status === "PAUSED") {
          els.btnToggle.textContent = "Reanudar";
        } else {
          els.btnToggle.textContent = "Iniciar";
        }
      }

      if (els.phaseTabs) {
        els.phaseTabs.forEach((tab) => {
          const tabPhase = tab.getAttribute("data-phase-select");
          const selected = tabPhase === state.phase;
          tab.classList.toggle("is-active", selected);
          tab.setAttribute("aria-selected", String(selected));
        });
      }
    }

    function stopTicker() {
      if (intervalId !== null) {
        clearInterval(intervalId);
        intervalId = null;
      }
    }

    function startTicker() {
      stopTicker();
      intervalId = setInterval(tick, TICK_INTERVAL_MS);
    }

    function transitionAfterPhaseEnd(completedPhase) {
      if (completedPhase === "focus") {
        persisted.completedPomodoros += 1;
        syncCounter();
        persistCurrent();
        return persisted.completedPomodoros % 4 === 0 ? "long" : "short";
      }
      return "focus";
    }

    function completeCurrentPhase() {
      stopTicker();
      state.targetTimestamp = null;
      const nextPhase = transitionAfterPhaseEnd(state.phase);
      state.phase = nextPhase;
      state.status = "COMPLETED";
      state.remainingMs = getDurationMs(persisted, nextPhase);
      state.alertActive = true;
      syncAlertBanner();
      renderTimerDifferential(true);
      playCompletionChime();
    }

    function tick() {
      if (state.status !== "RUNNING" || state.targetTimestamp === null) {
        return;
      }
      const delta = state.targetTimestamp - Date.now();
      if (delta <= 0) {
        state.remainingMs = 0;
        completeCurrentPhase();
        return;
      }
      state.remainingMs = delta;
      renderTimerDifferential(false);
    }

    function startOrResume() {
      ensureAudioContext();
      state.alertActive = false;
      syncAlertBanner();
      state.targetTimestamp = Date.now() + state.remainingMs;
      state.status = "RUNNING";
      startTicker();
      renderTimerDifferential(true);
    }

    function pauseTimer() {
      if (state.status !== "RUNNING") return;
      state.remainingMs =
        state.targetTimestamp !== null
          ? Math.max(0, state.targetTimestamp - Date.now())
          : state.remainingMs;
      state.targetTimestamp = null;
      state.status = "PAUSED";
      stopTicker();
      renderTimerDifferential(true);
    }

    function toggleStartPause() {
      if (state.status === "RUNNING") {
        pauseTimer();
      } else {
        startOrResume();
      }
    }

    function resetPhase() {
      stopTicker();
      state.targetTimestamp = null;
      state.status = "IDLE";
      state.alertActive = false;
      state.remainingMs = getDurationMs(persisted, state.phase);
      syncAlertBanner();
      renderTimerDifferential(true);
    }

    function skipPhase() {
      stopTicker();
      state.targetTimestamp = null;
      state.alertActive = false;
      const nextPhase = state.phase === "focus" ? "short" : "focus";
      state.phase = nextPhase;
      state.status = "IDLE";
      state.remainingMs = getDurationMs(persisted, nextPhase);
      syncAlertBanner();
      renderTimerDifferential(true);
    }

    function selectPhase(nextPhase) {
      if (!VALID_PHASES.has(nextPhase)) return;
      stopTicker();
      state.targetTimestamp = null;
      state.alertActive = false;
      state.phase = nextPhase;
      state.status = "IDLE";
      state.remainingMs = getDurationMs(persisted, nextPhase);
      syncAlertBanner();
      renderTimerDifferential(true);
    }

    function handleDurationChange(key, phaseKey, inputEl) {
      if (!inputEl) return;
      const rawNum = Number(inputEl.value);
      if (!Number.isFinite(rawNum)) {
        inputEl.value = String(persisted[key]);
        return;
      }
      const clamped = clamp(Math.round(rawNum), MIN_MINUTES, MAX_MINUTES);
      persisted[key] = clamped;
      inputEl.value = String(clamped);
      persistCurrent();

      if (state.status === "IDLE" && state.phase === phaseKey) {
        state.remainingMs = getDurationMs(persisted, state.phase);
        renderTimerDifferential(true);
      }
    }

    function isEditableElement(el) {
      if (!el) return false;
      const tag = el.tagName ? el.tagName.toUpperCase() : "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
        return true;
      }
      if (el.isContentEditable) {
        return true;
      }
      if (typeof el.closest === "function") {
        return Boolean(el.closest("input, textarea, select, [contenteditable='true']"));
      }
      return false;
    }

    // Event bindings
    if (els.btnToggle) {
      els.btnToggle.addEventListener("click", toggleStartPause);
    }
    if (els.btnReset) {
      els.btnReset.addEventListener("click", resetPhase);
    }
    if (els.btnSkip) {
      els.btnSkip.addEventListener("click", skipPhase);
    }
    if (els.phaseTabs) {
      els.phaseTabs.forEach((tab) => {
        tab.addEventListener("click", () => {
          selectPhase(tab.getAttribute("data-phase-select"));
        });
      });
    }
    if (els.btnResetCount) {
      els.btnResetCount.addEventListener("click", () => {
        persisted.completedPomodoros = 0;
        syncCounter();
        persistCurrent();
      });
    }
    if (els.btnTheme) {
      els.btnTheme.addEventListener("click", () => {
        persisted.theme = persisted.theme === "dark" ? "light" : "dark";
        applyTheme();
        persistCurrent();
      });
    }
    if (els.btnMute) {
      els.btnMute.addEventListener("click", () => {
        ensureAudioContext();
        persisted.muted = !persisted.muted;
        syncAudioControls();
        persistCurrent();
      });
    }
    if (els.inputVolume) {
      els.inputVolume.addEventListener("input", () => {
        ensureAudioContext();
        const val = Number(els.inputVolume.value);
        if (Number.isFinite(val)) {
          persisted.volume = clamp(val, 0, 1);
          persistCurrent();
        }
      });
    }
    if (els.inputFocus) {
      els.inputFocus.addEventListener("change", () =>
        handleDurationChange("focusDuration", "focus", els.inputFocus)
      );
    }
    if (els.inputShortBreak) {
      els.inputShortBreak.addEventListener("change", () =>
        handleDurationChange("shortBreakDuration", "short", els.inputShortBreak)
      );
    }
    if (els.inputLongBreak) {
      els.inputLongBreak.addEventListener("change", () =>
        handleDurationChange("longBreakDuration", "long", els.inputLongBreak)
      );
    }

    document.addEventListener("visibilitychange", tick);
    window.addEventListener("focus", tick);

    document.addEventListener("keydown", (event) => {
      if (event.code !== "Space" && event.key !== " ") return;
      const focused = document.activeElement;
      if (isEditableElement(focused) || isEditableElement(event.target)) {
        return;
      }
      event.preventDefault();
      toggleStartPause();
    });

    // Initial UI synchronization
    applyTheme();
    syncAudioControls();
    syncDurationInputs();
    syncCounter();
    syncAlertBanner();
    renderTimerDifferential(true);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
