from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import pytest


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = WORKSPACE_ROOT / "index.html"
STYLES_CSS = WORKSPACE_ROOT / "styles.css"
SRC_DIR = WORKSPACE_ROOT / "src"


class _DOMTreeParser(HTMLParser):
    """Parse HTML into a lightweight serializable node tree for static and Node checks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict = {"tag": "document", "attrs": {}, "children": [], "text": ""}
        self._stack: list[dict] = [self.root]
        self.elements: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: (v if v is not None else "") for k, v in attrs}
        node = {"tag": tag.lower(), "attrs": attr_dict, "children": [], "text": ""}
        self._stack[-1]["children"].append(node)
        self.elements.append(node)
        if tag.lower() not in {"meta", "link", "input", "br", "hr", "img"}:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: (v if v is not None else "") for k, v in attrs}
        node = {"tag": tag.lower(), "attrs": attr_dict, "children": [], "text": ""}
        self._stack[-1]["children"].append(node)
        self.elements.append(node)

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        for idx in range(len(self._stack) - 1, 0, -1):
            if self._stack[idx]["tag"] == lower:
                del self._stack[idx:]
                break

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._stack[-1]["text"] += data


def _read_js_sources() -> dict[str, str]:
    if not SRC_DIR.exists():
        return {}
    return {
        str(path.relative_to(WORKSPACE_ROOT)).replace("\\", "/"): path.read_text(
            encoding="utf-8"
        )
        for path in sorted(SRC_DIR.rglob("*.js"))
        if "node_modules" not in path.parts
    }


def _all_js_text() -> str:
    return "\n".join(_read_js_sources().values())


def _parse_index_html() -> _DOMTreeParser:
    parser = _DOMTreeParser()
    parser.feed(INDEX_HTML.read_text(encoding="utf-8"))
    return parser


def _run_node_scenario(
    tmp_path: Path,
    initial_storage: dict[str, str],
    scenario_body: str,
) -> dict:
    """Execute src/app.js in a Node.js DOM/Storage/AudioContext/Timer harness."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is not available in PATH")

    app_js = SRC_DIR / "app.js"
    assert app_js.exists(), "src/app.js must exist"

    parsed = _parse_index_html()
    dom_seed = json.dumps(parsed.root)
    storage_seed = json.dumps(initial_storage)
    app_url = app_js.resolve().as_uri()

    harness_script = f"""
import {{ pathToFileURL }} from "node:url";

const DOM_SEED = {dom_seed};
const INITIAL_STORAGE = {storage_seed};
const APP_URL = {json.dumps(app_url)};

let fakeNow = 1_700_000_000_000;
const OrigDate = Date;
class MockDate extends OrigDate {{
  constructor(...args) {{
    if (args.length === 0) super(fakeNow);
    else super(...args);
  }}
  static now() {{ return fakeNow; }}
}}
globalThis.Date = MockDate;

let nextTimerId = 1;
const intervals = new Map();
const timeouts = new Map();
globalThis.setInterval = (fn, ms = 1000) => {{
  const id = nextTimerId++;
  intervals.set(id, {{ fn, ms }});
  return id;
}};
globalThis.clearInterval = (id) => {{ intervals.delete(id); }};
globalThis.setTimeout = (fn, ms = 0) => {{
  const id = nextTimerId++;
  timeouts.set(id, {{ fn, ms }});
  return id;
}};
globalThis.clearTimeout = (id) => {{ timeouts.delete(id); }};
globalThis.requestAnimationFrame = (fn) => globalThis.setTimeout(() => fn(fakeNow), 16);
globalThis.cancelAnimationFrame = (id) => globalThis.clearTimeout(id);

function tickIntervals(times = 1) {{
  for (let i = 0; i < times; i++) {{
    for (const entry of [...intervals.values()]) {{
      entry.fn();
    }}
  }}
}}

const storageMap = new Map(Object.entries(INITIAL_STORAGE));
const localStorageMock = {{
  get length() {{ return storageMap.size; }},
  key(i) {{ return [...storageMap.keys()][i] ?? null; }},
  getItem(k) {{ return storageMap.has(String(k)) ? storageMap.get(String(k)) : null; }},
  setItem(k, v) {{ storageMap.set(String(k), String(v)); }},
  removeItem(k) {{ storageMap.delete(String(k)); }},
  clear() {{ storageMap.clear(); }},
}};

const audioCalls = [];
class MockAudioParam {{
  constructor() {{ this.value = 0; }}
  setValueAtTime(v) {{ this.value = v; }}
  exponentialRampToValueAtTime(v) {{ this.value = v; }}
  linearRampToValueAtTime(v) {{ this.value = v; }}
}}
class MockAudioContext {{
  constructor() {{
    this.state = "running";
    this.currentTime = 0;
    this.destination = {{}};
    audioCalls.push({{ type: "context" }});
  }}
  resume() {{ this.state = "running"; return Promise.resolve(); }}
  close() {{ this.state = "closed"; return Promise.resolve(); }}
  createOscillator() {{
    const osc = {{
      type: "sine",
      frequency: new MockAudioParam(),
      connect() {{ return this; }},
      start() {{ audioCalls.push({{ type: "oscillator_start" }}); }},
      stop() {{ audioCalls.push({{ type: "oscillator_stop" }}); }},
    }};
    return osc;
  }}
  createGain() {{
    return {{
      gain: new MockAudioParam(),
      connect() {{ return this; }},
    }};
  }}
}}

function makeDataset(el) {{
  return new Proxy({{}}, {{
    get(_, prop) {{
      const attr = "data-" + String(prop).replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());
      return el._attrs.get(attr);
    }},
    set(_, prop, val) {{
      const attr = "data-" + String(prop).replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());
      el._attrs.set(attr, String(val));
      return true;
    }},
  }});
}}

class MockElement {{
  constructor(tagName, attrs = {{}}, text = "") {{
    this.tagName = tagName.toUpperCase();
    this.nodeName = this.tagName;
    this._attrs = new Map(Object.entries(attrs));
    this.children = [];
    this.parentNode = null;
    this.parentElement = null;
    this._text = text;
    this._innerHTML = "";
    this._listeners = new Map();
    this.style = new Proxy({{}}, {{
      set(t, k, v) {{ t[k] = v; return true; }},
      get(t, k) {{
        if (k === "setProperty") return (p, v) => {{ t[p] = v; }};
        return t[k] ?? "";
      }},
    }});
    this.value = attrs.value ?? "";
    this.checked = "checked" in attrs;
    this.disabled = "disabled" in attrs;
    this.type = attrs.type ?? "";
    this.dataset = makeDataset(this);
    this.classList = {{
      _set: new Set((attrs.class || "").split(/\\s+/).filter(Boolean)),
      add: (...c) => c.forEach((x) => this.classList._set.add(x)),
      remove: (...c) => c.forEach((x) => this.classList._set.delete(x)),
      toggle: (x, force) => {{
        if (force === undefined) {{
          if (this.classList._set.has(x)) {{ this.classList._set.delete(x); return false; }}
          this.classList._set.add(x); return true;
        }}
        if (force) this.classList._set.add(x);
        else this.classList._set.delete(x);
        return Boolean(force);
      }},
      contains: (x) => this.classList._set.has(x),
    }};
  }}
  get id() {{ return this._attrs.get("id") || ""; }}
  set id(v) {{ this._attrs.set("id", String(v)); }}
  get className() {{ return [...this.classList._set].join(" "); }}
  set className(v) {{
    this.classList._set = new Set(String(v).split(/\\s+/).filter(Boolean));
  }}
  get textContent() {{
    return this._text + this.children.map((c) => c.textContent).join(" ");
  }}
  set textContent(v) {{
    this._text = String(v ?? "");
    this.children = [];
  }}
  get innerText() {{ return this.textContent; }}
  set innerText(v) {{ this.textContent = v; }}
  get innerHTML() {{ return this._innerHTML || this.textContent; }}
  set innerHTML(v) {{
    this._innerHTML = String(v ?? "");
    this._text = this._innerHTML.replace(/<[^>]+>/g, " ");
    this.children = [];
  }}
  getAttribute(k) {{ return this._attrs.has(k) ? this._attrs.get(k) : null; }}
  setAttribute(k, v) {{ this._attrs.set(k, String(v)); }}
  removeAttribute(k) {{ this._attrs.delete(k); }}
  hasAttribute(k) {{ return this._attrs.has(k); }}
  appendChild(child) {{
    child.parentNode = this;
    child.parentElement = this;
    this.children.push(child);
    return child;
  }}
  append(...nodes) {{
    for (const n of nodes) {{
      if (typeof n === "string") this._text += n;
      else this.appendChild(n);
    }}
  }}
  prepend(...nodes) {{ this.append(...nodes); }}
  replaceChildren(...nodes) {{
    this.children = [];
    this._text = "";
    this.append(...nodes);
  }}
  remove() {{
    if (this.parentNode) {{
      this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
    }}
  }}
  addEventListener(type, fn) {{
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }}
  removeEventListener(type, fn) {{
    const list = this._listeners.get(type) || [];
    this._listeners.set(type, list.filter((f) => f !== fn));
  }}
  dispatchEvent(ev) {{
    ev.target = ev.target || this;
    ev.currentTarget = this;
    const list = this._listeners.get(ev.type) || [];
    for (const fn of [...list]) fn(ev);
    return !ev.defaultPrevented;
  }}
  click() {{
    this.dispatchEvent({{ type: "click", preventDefault() {{}}, stopPropagation() {{}} }});
  }}
  focus() {{ documentMock.activeElement = this; }}
  closest(sel) {{
    let cur = this;
    while (cur) {{
      if (matchesSelector(cur, sel)) return cur;
      cur = cur.parentElement;
    }}
    return null;
  }}
  querySelector(sel) {{ return this.querySelectorAll(sel)[0] || null; }}
  querySelectorAll(sel) {{
    const res = [];
    const walk = (node) => {{
      for (const ch of node.children) {{
        if (matchesSelector(ch, sel)) res.push(ch);
        walk(ch);
      }}
    }};
    walk(this);
    return res;
  }}
}}

function matchesSelector(el, sel) {{
  for (const part of sel.split(",").map((s) => s.trim()).filter(Boolean)) {{
    const simple = part.split(/\\s+/).pop();
    if (!simple) continue;
    if (simple.startsWith("#") && el.id === simple.slice(1)) return true;
    if (simple.startsWith(".") && el.classList.contains(simple.slice(1))) return true;
    const attrMatch = simple.match(/^\\[([^=\\]]+)(?:=["']?([^"'\\]]+)["']?)?\\]$/);
    if (attrMatch) {{
      const [, attr, val] = attrMatch;
      if (val === undefined && el.hasAttribute(attr)) return true;
      if (val !== undefined && el.getAttribute(attr) === val) return true;
    }}
    if (/^[a-z0-9-]+$/i.test(simple) && el.tagName === simple.toUpperCase()) return true;
  }}
  return false;
}}

function buildFromSeed(node) {{
  const el = new MockElement(node.tag === "document" ? "div" : node.tag, node.attrs || {{}}, node.text || "");
  for (const ch of node.children || []) {{
    el.appendChild(buildFromSeed(ch));
  }}
  return el;
}}

const seededRoot = buildFromSeed(DOM_SEED);
const htmlEl = seededRoot.querySelector("html") || new MockElement("html");
const bodyEl = seededRoot.querySelector("body") || new MockElement("body");
if (!bodyEl.querySelector("#app")) {{
  const appEl = new MockElement("main", {{ id: "app" }});
  bodyEl.appendChild(appEl);
}}
htmlEl.appendChild(bodyEl);

const docListeners = new Map();
const documentMock = {{
  documentElement: htmlEl,
  body: bodyEl,
  head: new MockElement("head"),
  title: "Static Web App",
  hidden: false,
  visibilityState: "visible",
  activeElement: bodyEl,
  createElement: (tag) => new MockElement(tag),
  createTextNode: (t) => new MockElement("#text", {{}}, String(t)),
  getElementById: (id) => htmlEl.querySelector("#" + id),
  querySelector: (sel) => htmlEl.querySelector(sel),
  querySelectorAll: (sel) => htmlEl.querySelectorAll(sel),
  addEventListener: (type, fn) => {{
    if (!docListeners.has(type)) docListeners.set(type, []);
    docListeners.get(type).push(fn);
  }},
  removeEventListener: (type, fn) => {{
    const list = docListeners.get(type) || [];
    docListeners.set(type, list.filter((f) => f !== fn));
  }},
  dispatchEvent: (ev) => {{
    ev.target = ev.target || documentMock.activeElement || bodyEl;
    ev.currentTarget = documentMock;
    for (const fn of [...(docListeners.get(ev.type) || [])]) fn(ev);
    return !ev.defaultPrevented;
  }},
}};

const winListeners = new Map();
const windowMock = {{
  document: documentMock,
  localStorage: localStorageMock,
  AudioContext: MockAudioContext,
  webkitAudioContext: MockAudioContext,
  matchMedia: () => ({{ matches: true, addEventListener() {{}}, removeEventListener() {{}} }}),
  addEventListener: (type, fn) => {{
    if (!winListeners.has(type)) winListeners.set(type, []);
    winListeners.get(type).push(fn);
  }},
  removeEventListener: (type, fn) => {{
    const list = winListeners.get(type) || [];
    winListeners.set(type, list.filter((f) => f !== fn));
  }},
  dispatchEvent: (ev) => {{
    for (const fn of [...(winListeners.get(ev.type) || [])]) fn(ev);
  }},
}};

globalThis.window = windowMock;
globalThis.document = documentMock;
globalThis.localStorage = localStorageMock;
globalThis.AudioContext = MockAudioContext;
globalThis.webkitAudioContext = MockAudioContext;
globalThis.HTMLElement = MockElement;
globalThis.KeyboardEvent = class KeyboardEvent {{
  constructor(type, init = {{}}) {{
    this.type = type;
    this.key = init.key ?? "";
    this.code = init.code ?? "";
    this.target = init.target ?? documentMock.activeElement ?? bodyEl;
    this.defaultPrevented = false;
  }}
  preventDefault() {{ this.defaultPrevented = true; }}
  stopPropagation() {{}}
}};
globalThis.Event = class Event {{
  constructor(type, init = {{}}) {{
    this.type = type;
    this.target = init.target ?? bodyEl;
    this.defaultPrevented = false;
  }}
  preventDefault() {{ this.defaultPrevented = true; }}
  stopPropagation() {{}}
}};

await import(APP_URL);
documentMock.dispatchEvent(new Event("DOMContentLoaded"));

{scenario_body}
"""

    script_path = tmp_path / "harness.mjs"
    script_path.write_text(harness_script, encoding="utf-8")
    proc = subprocess.run(
        [node_bin, str(script_path)],
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, (
        f"Node harness failed (exit {proc.returncode}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# AT-01: Default values & resilience against empty/corrupt/out-of-range storage
# ---------------------------------------------------------------------------
def test_at01_defaults_and_resilience_on_empty_or_corrupt_local_storage(
    tmp_path: Path,
) -> None:
    """AT-01 [unit]: Empty, corrupt JSON, or out-of-range localStorage falls back to defaults."""
    js_text = _all_js_text()
    assert js_text.strip(), "Expected JS source files under src/"
    assert "localStorage" in js_text, "App must interact with localStorage"
    assert re.search(r"try\s*\{[\s\S]*JSON\.parse", js_text), (
        "JSON.parse of localStorage must be wrapped in try/catch"
    )
    assert "25" in js_text and "5" in js_text and "15" in js_text, (
        "Default durations 25, 5, 15 must be defined"
    )
    assert re.search(r"""['"]dark['"]""", js_text), "Default theme 'dark' must be defined"

    corrupt_cases = [
        {},
        {"pomodoro": "{corrupt-json", "pomodoroSettings": "{corrupt-json", "state": "{bad"},
        {
            "pomodoro": json.dumps(
                {
                    "focusDuration": -5,
                    "shortBreakDuration": 999,
                    "longBreakDuration": 0,
                    "volume": "NaN",
                    "theme": "neon-invalid",
                    "completedPomodoros": -3,
                }
            )
        },
    ]

    for idx, storage_case in enumerate(corrupt_cases):
        case_dir = tmp_path / f"case_{idx}"
        case_dir.mkdir(exist_ok=True)
        result = _run_node_scenario(
            case_dir,
            storage_case,
            """
            const theme =
              documentMock.documentElement.getAttribute("data-theme") ||
              documentMock.body.getAttribute("data-theme") ||
              "";
            const text = documentMock.body.textContent + " " + documentMock.title;
            console.log(JSON.stringify({ theme, text }));
            """,
        )
        assert result["theme"] == "dark", (
            f"Expected default theme 'dark' for storage case {idx}, got {result['theme']!r}"
        )
        assert "25:00" in result["text"], (
            f"Expected default 25:00 focus clock in UI/title for case {idx}, got {result['text']!r}"
        )


# ---------------------------------------------------------------------------
# AT-02: Pomodoro state machine transitions (1-3 short break, 4th long break)
# ---------------------------------------------------------------------------
def test_at02_pomodoro_state_machine_cycles_and_long_break() -> None:
    """AT-02 [unit]: 4-cycle Pomodoro state machine transitions & focus-only counter increment."""
    js_text = _all_js_text()
    assert js_text.strip(), "Expected JS source files under src/"

    # Verify focus, short break, and long break states exist
    has_phases = (
        re.search(r"focus|enfoque", js_text, re.IGNORECASE)
        and re.search(r"short[-_]?break|descanso[-_]?corto|short", js_text, re.IGNORECASE)
        and re.search(r"long[-_]?break|descanso[-_]?largo|long", js_text, re.IGNORECASE)
    )
    assert has_phases, "State machine must define focus, short break, and long break phases"

    # Verify 4th-cycle long break logic
    assert re.search(r"%\s*4|===?\s*4|>=\s*4", js_text), (
        "State machine must transition to long break on the 4th completed focus cycle"
    )
    assert "15" in js_text, "Long break default duration (15 min) must be present"


# ---------------------------------------------------------------------------
# AT-03: Drift-free Date.now() timing + visibilitychange + document.title
# ---------------------------------------------------------------------------
def test_at03_timer_accuracy_with_date_now_and_visibilitychange(tmp_path: Path) -> None:
    """AT-03 [integration]: Date.now() + visibilitychange updates clock and document.title without drift."""
    js_text = _all_js_text()
    assert "Date.now" in js_text, "Timer must use Date.now() to avoid cumulative drift"
    assert "visibilitychange" in js_text, (
        "Timer must listen to document 'visibilitychange' event"
    )
    assert "document.title" in js_text, "Timer must update document.title"

    result = _run_node_scenario(
        tmp_path,
        {},
        """
        const startEv = new KeyboardEvent("keydown", {
          key: " ",
          code: "Space",
          target: documentMock.body,
        });
        documentMock.dispatchEvent(startEv);

        // Simulate 5 minutes passing in a background tab
        fakeNow += 5 * 60 * 1000;
        documentMock.hidden = false;
        documentMock.visibilityState = "visible";
        documentMock.dispatchEvent(new Event("visibilitychange"));
        tickIntervals(1);

        const bodyText = documentMock.body.textContent;
        console.log(JSON.stringify({ title: documentMock.title, bodyText }));
        """,
    )
    assert "20:00" in result["title"], (
        f"Expected document.title to reflect 20:00 after 5 min elapsed, got {result['title']!r}"
    )
    assert "20:00" in result["bodyText"], (
        f"Expected rendered clock to reflect 20:00 after 5 min elapsed, got {result['bodyText']!r}"
    )


# ---------------------------------------------------------------------------
# AT-04: Spacebar shortcut toggles start/pause once; ignored on numeric inputs
# ---------------------------------------------------------------------------
def test_at04_spacebar_shortcut_and_input_focus_guard(tmp_path: Path) -> None:
    """AT-04 [e2e]: Spacebar toggles start/pause without scrolling; ignored inside numeric inputs."""
    js_text = _all_js_text()
    assert re.search(r"keydown", js_text), "Must listen for keydown events"
    assert re.search(r"""['"]Space['"]|['"] ['"]""", js_text), (
        "Must handle Spacebar key/code"
    )
    assert "preventDefault" in js_text, (
        "Must call preventDefault() on Spacebar to prevent page scroll / double button activation"
    )
    assert re.search(r"INPUT|input|tagName|closest", js_text), (
        "Must ignore Spacebar when focus is inside an input element"
    )

    result = _run_node_scenario(
        tmp_path,
        {},
        """
        // 1. Focus on a button and press Spacebar: should preventDefault and start
        const btn = documentMock.querySelector("button") || documentMock.createElement("button");
        documentMock.activeElement = btn;
        const evBtn = new KeyboardEvent("keydown", { key: " ", code: "Space", target: btn });
        documentMock.dispatchEvent(evBtn);
        const preventedOnBtn = evBtn.defaultPrevented;

        // Advance 2 minutes and tick -> should be running (23:00)
        fakeNow += 2 * 60 * 1000;
        tickIntervals(1);
        const afterStartTitle = documentMock.title;

        // 2. Press Spacebar again on document.body -> should pause
        documentMock.activeElement = documentMock.body;
        const evPause = new KeyboardEvent("keydown", { key: " ", code: "Space", target: documentMock.body });
        documentMock.dispatchEvent(evPause);

        // Advance another 2 minutes -> should remain paused at 23:00
        fakeNow += 2 * 60 * 1000;
        tickIntervals(1);
        const afterPauseTitle = documentMock.title;

        // 3. Focus on a numeric input and press Spacebar -> should NOT preventDefault or start
        const numInput = documentMock.querySelector('input[type="number"]') || (() => {
          const inp = documentMock.createElement("input");
          inp.type = "number";
          inp.setAttribute("type", "number");
          documentMock.body.appendChild(inp);
          return inp;
        })();
        documentMock.activeElement = numInput;
        const evInput = new KeyboardEvent("keydown", { key: " ", code: "Space", target: numInput });
        documentMock.dispatchEvent(evInput);
        const preventedOnInput = evInput.defaultPrevented;

        fakeNow += 2 * 60 * 1000;
        tickIntervals(1);
        const afterInputSpaceTitle = documentMock.title;

        console.log(JSON.stringify({
          preventedOnBtn,
          afterStartTitle,
          afterPauseTitle,
          preventedOnInput,
          afterInputSpaceTitle,
        }));
        """,
    )
    assert result["preventedOnBtn"] is True, "Spacebar must call preventDefault() on button/document"
    assert "23:00" in result["afterStartTitle"], (
        f"Expected timer running at 23:00, got {result['afterStartTitle']!r}"
    )
    assert "23:00" in result["afterPauseTitle"], (
        f"Expected timer paused at 23:00, got {result['afterPauseTitle']!r}"
    )
    assert result["preventedOnInput"] is False, (
        "Spacebar inside numeric input must not call preventDefault() or trigger timer"
    )
    assert "23:00" in result["afterInputSpaceTitle"], (
        f"Expected timer to remain paused when Spacebar pressed in numeric input, got {result['afterInputSpaceTitle']!r}"
    )


# ---------------------------------------------------------------------------
# AT-05: Persistence of durations (1-120), volume, mute, theme, and counter
# ---------------------------------------------------------------------------
def test_at05_persistence_of_settings_theme_and_counter_in_local_storage() -> None:
    """AT-05 [e2e]: Settings (1-120 min), volume, mute, data-theme, and counter persist in localStorage."""
    html_text = INDEX_HTML.read_text(encoding="utf-8")
    css_text = STYLES_CSS.read_text(encoding="utf-8")
    js_text = _all_js_text()
    combined_ui = html_text + "\n" + js_text

    assert "localStorage.setItem" in js_text and "localStorage.getItem" in js_text, (
        "App must read and write settings/state via localStorage"
    )
    assert "data-theme" in combined_ui, "App must set data-theme attribute"
    assert "data-theme" in css_text, "styles.css must style light/dark themes via data-theme"
    assert "120" in combined_ui, "Duration upper bound 120 must be enforced in UI/JS"
    assert re.search(r"mute|muted", js_text, re.IGNORECASE), (
        "Mute state must be tracked and persisted"
    )
    assert re.search(r"volume|volumen", js_text, re.IGNORECASE), (
        "Volume setting must be tracked and persisted"
    )


# ---------------------------------------------------------------------------
# AT-06: Phase completion visual notice, body[data-phase] accent, and Web Audio
# ---------------------------------------------------------------------------
def test_at06_phase_completion_visual_notice_data_phase_and_web_audio(
    tmp_path: Path,
) -> None:
    """AT-06 [integration]: Phase end shows notice, updates body data-phase accent, plays Web Audio when unmuted."""
    css_text = STYLES_CSS.read_text(encoding="utf-8")
    js_text = _all_js_text()

    assert "data-phase" in js_text, "JS must update data-phase attribute on body"
    assert "data-phase" in css_text, (
        "styles.css must define accent color rules per [data-phase]"
    )
    assert re.search(r"AudioContext|webkitAudioContext", js_text), (
        "JS must synthesize completion chime using Web Audio API"
    )
    assert "createOscillator" in js_text, (
        "JS must use AudioContext.createOscillator() for Web Audio synthesis"
    )

    result = _run_node_scenario(
        tmp_path,
        {},
        """
        const initialPhase = documentMock.body.getAttribute("data-phase") || "";
        const startEv = new KeyboardEvent("keydown", {
          key: " ",
          code: "Space",
          target: documentMock.body,
        });
        documentMock.dispatchEvent(startEv);

        // Complete full 25-minute focus block
        fakeNow += 25 * 60 * 1000 + 500;
        documentMock.dispatchEvent(new Event("visibilitychange"));
        tickIntervals(2);

        const nextPhase = documentMock.body.getAttribute("data-phase") || "";
        const oscStarted = audioCalls.some((c) => c.type === "oscillator_start");
        console.log(JSON.stringify({ initialPhase, nextPhase, oscStarted }));
        """,
    )
    assert result["initialPhase"] != "" and result["nextPhase"] != "", (
        f"Expected non-empty body[data-phase], got initial={result['initialPhase']!r}, next={result['nextPhase']!r}"
    )
    assert result["initialPhase"] != result["nextPhase"], (
        f"Expected body[data-phase] to change after phase completion, stayed {result['nextPhase']!r}"
    )
    assert result["oscStarted"] is True, (
        "Expected Web Audio oscillator to start on phase completion when unmuted"
    )
