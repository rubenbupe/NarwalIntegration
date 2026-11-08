/*
 * Narwal map card: clean rooms by tapping them, clean drawn zones, go to a point,
 * draw no-go zones and drive the robot with a joystick. Served by the Narwal
 * integration; talks to it through narwal/map, narwal/pose, narwal/remote and the
 * narwal.* services. No external dependencies.
 */
const CARD_VERSION = "1.4.1";

const MODES = [
  ["vacuum_and_mop", "Vacuum & mop"],
  ["vacuum_then_mop", "Vacuum, then mop"],
  ["vacuum", "Vacuum"],
  ["mop", "Mop"],
];
const TABS = [
  ["rooms", "Rooms", "mdi:floor-plan"],
  ["zones", "Zones", "mdi:selection-drag"],
  ["goto", "Go to", "mdi:map-marker-radius"],
  ["nogo", "No-go", "mdi:cancel"],
  ["remote", "Remote", "mdi:gamepad-variant-outline"],
];
const PALETTE = [
  "#cdbfe8", "#bfd8f2", "#c8e6c9", "#ffe0b2", "#f8c8d8", "#d7ccc8",
  "#b2dfdb", "#e6ee9c", "#ffccbc", "#c5cae9", "#f0f4c3", "#d1c4e9",
];
const CELL_WALL = 250;
const CELL_FLOOR = 251;
const MAX_ZONES = 5;

const css = `
  :host { display: block; }
  ha-card { overflow: hidden; }
  .head { display: flex; align-items: center; gap: 8px; padding: 12px 16px 4px; }
  .title { font-size: 16px; font-weight: 500; flex: 1; color: var(--primary-text-color); }
  .status { font-size: 12px; color: var(--secondary-text-color); }
  .tabs { display: flex; gap: 6px; padding: 8px 12px; overflow-x: auto; scrollbar-width: none; }
  .tabs::-webkit-scrollbar { display: none; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 18px;
          border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color);
          font: inherit; font-size: 13px; cursor: pointer; white-space: nowrap; user-select: none; }
  .chip ha-icon { --mdc-icon-size: 18px; }
  .chip.on { background: var(--narwal-accent); border-color: var(--narwal-accent); color: #fff; }
  .chip:disabled { opacity: .45; cursor: default; }
  .map { position: relative; padding: 0 12px; }
  canvas.view { display: block; margin: 0 auto; border-radius: 12px; touch-action: pan-y; }
  .hint { text-align: center; font-size: 12px; color: var(--secondary-text-color); padding: 6px 16px 0; min-height: 18px; }
  .bar { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 12px 4px; align-items: center; }
  .bar.end { justify-content: flex-end; padding-bottom: 14px; }
  .grow { flex: 1; }
  .primary { background: var(--narwal-accent); color: #fff; border: none; border-radius: 20px;
             padding: 10px 22px; font: inherit; font-weight: 500; cursor: pointer; }
  .primary:disabled { opacity: .45; cursor: default; }
  .ghost { background: transparent; color: var(--narwal-accent); border: none; padding: 10px 12px;
           font: inherit; cursor: pointer; }
  .remote { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 8px 12px 14px; }
  .pad { width: 200px; height: 200px; border-radius: 50%; position: relative; touch-action: none;
         background: radial-gradient(circle, color-mix(in srgb, var(--narwal-accent) 10%, transparent) 0%,
                     color-mix(in srgb, var(--narwal-accent) 22%, transparent) 100%);
         border: 1px solid var(--divider-color); }
  .knob { position: absolute; width: 64px; height: 64px; border-radius: 50%; left: 68px; top: 68px;
          background: var(--narwal-accent); box-shadow: 0 2px 6px rgba(0,0,0,.25); pointer-events: none; }
  .err { color: var(--error-color, #db4437); padding: 16px; }
`;

function fire(node, type, detail) {
  node.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
}

class NarwalMapCard extends HTMLElement {
  static getStubConfig(hass) {
    const vac = Object.keys(hass.states).find((e) => e.startsWith("vacuum.") && e.includes("narwal"));
    return { entity: vac || "" };
  }

  static getConfigElement() {
    return document.createElement("narwal-map-card-editor");
  }

  setConfig(config) {
    this._config = { title: config.selection === "switches" ? "" : "Narwal", ...config };
    const allowed = Array.isArray(this._config.tabs) && this._config.tabs.length ? this._config.tabs : TABS.map((t) => t[0]);
    this._allowed = allowed;
    this._tab = this._config.selection === "switches"
      ? "rooms"
      : allowed.includes(this._config.default_tab) ? this._config.default_tab : allowed[0];
    this._selected = [];
    this._zones = [];
    this._goto = null;
    this._draft = null;
    this._nogoType = "no_go";
    this._nogoPicked = null;
    this._mode = "vacuum_and_mop";
    this._freo = false;
    this._passes = 1;
    this._speed = 60;
    this._remoteOn = false;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._build();
  }

  getCardSize() { return 8; }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: "auto" }; }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._loadMap(false);
    if (this._switchMode) this._syncSwitchSelection();
    this._renderStatus();
  }

  get _switchMode() { return this._config.selection === "switches"; }

  /* selection mirrored from the integration's room selection switches */
  _roomSwitches() {
    const out = {};
    for (const [id, st] of Object.entries(this._hass.states)) {
      if (id.startsWith("switch.") && id.endsWith("_selected") && id.includes("narwal") && st.attributes.room_id != null) {
        out[Number(st.attributes.room_id)] = st;
      }
    }
    return out;
  }

  _syncSwitchSelection() {
    const sw = this._roomSwitches();
    const on = Object.entries(sw).filter(([, st]) => st.state === "on").map(([id]) => Number(id));
    const orderEntity = this._config.order_entity || "input_text.narwal_room_order";
    const raw = (this._hass.states[orderEntity] || {}).state || "";
    const ordered = raw.split(",").map((v) => parseInt(v, 10)).filter((v) => on.includes(v));
    const selected = [...ordered, ...on.filter((v) => !ordered.includes(v))];
    let changed = selected.join(",") !== this._selected.join(",");
    this._selected = selected;
    // zone mode + zones live in HA helpers so the dashboard's Start can use them
    const zm = this._config.zone_mode_entity;
    const zoneMode = !!zm && (this._hass.states[zm] || {}).state === "on";
    const tab = zoneMode ? "zones" : "rooms";
    if (tab !== this._tab) { this._tab = tab; changed = true; this._renderTabs(); }
    const ze = this._config.zones_entity;
    if (ze && !this._drag) {
      const raw = (this._hass.states[ze] || {}).state || "[]";
      if (raw !== this._zonesRaw) {
        this._zonesRaw = raw;
        try {
          this._zones = (JSON.parse(raw) || []).map(([x1, y1, x2, y2]) => ({ x1, y1, x2, y2 }));
        } catch (_) { this._zones = []; }
        changed = true;
      }
    }
    const ge = this._config.global_entity || "input_boolean.narwal_use_global_settings";
    const profiles = (this._hass.states[ge] || {}).state === "off";
    const profText = profiles ? Object.entries(this._roomSwitches()).map(([id, st]) => `${id}:${this._profileFor(st)}`).join("|") : "";
    if (profText !== this._profText) { this._profText = profText; changed = true; }
    this._showProfiles = profiles;
    if (changed && this._map) { this._paintBase(); this._draw(); this._renderPanel(); }
  }

  _roomKey(st) {
    const m = st.entity_id.match(/narwal_flow_(.+)_selected$/);
    return m ? m[1] : null;
  }

  _profileFor(st) {
    const key = this._roomKey(st);
    const tpl = this._config.profile_entity || "sensor.narwal_{key}_profile";
    const ps = key && this._hass.states[tpl.replace("{key}", key)];
    return ps ? ps.state : "";
  }

  _saveZones() {
    const ze = this._config.zone_mode_entity && this._config.zones_entity;
    if (!this._switchMode || !ze) return;
    const value = JSON.stringify(this._zones.map((z) => [z.x1, z.y1, z.x2, z.y2]));
    this._zonesRaw = value;
    this._hass.callService("input_text", "set_value", { entity_id: this._config.zones_entity, value });
  }

  _openRoom(id) {
    const st = this._roomSwitches()[id];
    const key = st && this._roomKey(st);
    const tpl = this._config.room_path;
    if (!key || !tpl) return;
    history.pushState(null, "", tpl.replace("{key}", key));
    window.dispatchEvent(new CustomEvent("location-changed"));
  }

  connectedCallback() {
    this._poseTimer = setInterval(() => this._pollPose(), 1500);
    this._mapTimer = setInterval(() => this._loadMap(false), 60000);
    this._ro = new ResizeObserver(() => this._draw());
    if (this._mapBox) this._ro.observe(this._mapBox);
  }

  disconnectedCallback() {
    clearInterval(this._poseTimer);
    clearInterval(this._mapTimer);
    this._stopDriving();
    if (this._ro) this._ro.disconnect();
  }

  get _entity() { return this._config.entity; }

  _ws(msg) {
    const payload = { ...msg };
    if (this._entity) payload.entity_id = this._entity;
    return this._hass.callWS(payload);
  }

  async _service(service, data) {
    try {
      await this._hass.callService("narwal", service, data, this._entity ? { entity_id: this._entity } : undefined);
      return true;
    } catch (err) {
      fire(this, "hass-notification", { message: `Narwal: ${err.message || err}` });
      return false;
    }
  }

  async _loadMap(refresh) {
    if (!this._hass || this._loading) return;
    this._loading = true;
    try {
      const map = await this._ws({ type: "narwal/map", refresh: !!refresh });
      this._setMap(map);
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._loading = false;
    this._draw();
    this._renderPanel();
  }

  async _pollPose() {
    if (!this._hass || !this._map || document.hidden) return;
    try {
      this._live = await this._ws({ type: "narwal/pose" });
      if (this._live && !this._live.robot && !this._live.docked) {
        const now = Date.now();
        if (!this._lastPoseMap || now - this._lastPoseMap > 10000) {
          this._lastPoseMap = now;
          this._loadMap(true);
        }
      }
      await this._syncPath();
      this._draw();
      this._renderStatus();
    } catch (_) { /* robot busy */ }
  }

  async _syncPath() {
    const signature = this._live && this._live.path_signature;
    if (!signature || this._config.show_path === false) { this._path = null; this._pathKey = null; return; }
    const key = signature.join(":");
    if (key === this._pathKey) return;
    const result = await this._ws({ type: "narwal/path" });
    this._path = result.path;
    this._pathKey = key;
  }

  _drawPath(ctx) {
    if (!this._path || !this._path.length) return;
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,.9)";
    ctx.lineWidth = Math.max(1.5, this._scale * 0.9);
    ctx.lineJoin = ctx.lineCap = "round";
    ctx.beginPath();
    let pen = false;
    for (const point of this._path) {
      if (!point) { pen = false; continue; }
      const [px, py] = this._cellToCanvas(point[0], point[1]);
      if (pen) ctx.lineTo(px, py); else ctx.moveTo(px, py);
      pen = true;
    }
    ctx.stroke();
    ctx.strokeStyle = "rgba(63,81,181,.85)";
    ctx.lineWidth = Math.max(1, this._scale * 0.45);
    ctx.stroke();
    ctx.restore();
  }

  _setMap(map) {
    this._map = map;
    this._rotation = this._config.rotation != null ? Number(this._config.rotation) : ((map.rotation % 360) + 360) % 360;
    const bin = atob(map.cells);
    const cells = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) cells[i] = bin.charCodeAt(i);
    this._cells = cells;
    // bounding box of known cells + room centroids
    let minC = map.width, maxC = 0, minR = map.height, maxR = 0;
    const sums = {};
    for (let row = 0; row < map.height; row++) {
      const yIdx = map.height - 1 - row;
      for (let col = 0; col < map.width; col++) {
        const v = cells[yIdx * map.width + col];
        if (!v) continue;
        if (col < minC) minC = col; if (col > maxC) maxC = col;
        if (row < minR) minR = row; if (row > maxR) maxR = row;
        if (v < CELL_WALL) {
          const s = (sums[v] = sums[v] || [0, 0, 0]);
          s[0] += col; s[1] += row; s[2] += 1;
        }
      }
    }
    const pad = 2;
    this._crop = {
      c0: Math.max(0, minC - pad), r0: Math.max(0, minR - pad),
      c1: Math.min(map.width - 1, maxC + pad), r1: Math.min(map.height - 1, maxR + pad),
    };
    this._centroids = {};
    this._roomSize = {};
    for (const [id, s] of Object.entries(sums)) { this._centroids[id] = [s[0] / s[2], s[1] / s[2]]; this._roomSize[id] = s[2]; }
    this._names = {};
    map.rooms.forEach((r) => { this._names[r.id] = r.name; });
    this._selected = this._selected.filter((id) => this._names[id]);
    this._paintBase();
  }

  /* ---------- coordinates ---------- */
  /* crop-relative continuous (col,row) <-> display (u,v), honouring the map rotation */
  _rot(c, r) {
    const W = this._crop.c1 - this._crop.c0 + 1, H = this._crop.r1 - this._crop.r0 + 1;
    switch (this._rotation) {
      case 90: return [H - r, c];
      case 180: return [W - c, H - r];
      case 270: return [r, W - c];
      default: return [c, r];
    }
  }

  _unrot(u, v) {
    const W = this._crop.c1 - this._crop.c0 + 1, H = this._crop.r1 - this._crop.r0 + 1;
    switch (this._rotation) {
      case 90: return [v, H - u];
      case 180: return [W - u, H - v];
      case 270: return [W - v, u];
      default: return [u, v];
    }
  }

  _cellToCanvas(x, y) {
    const m = this._map;
    const col = x - m.left - this._crop.c0;
    const row = (m.height - 1 - (y - m.bottom)) - this._crop.r0;
    const [u, v] = this._rot(col, row);
    return [u * this._scale, v * this._scale];
  }

  _canvasToCell(px, py) {
    const m = this._map;
    const [c, r] = this._unrot(px / this._scale, py / this._scale);
    const col = c + this._crop.c0;
    const row = r + this._crop.r0;
    return [m.left + col, m.bottom + (m.height - 1 - row)];
  }

  _roomAt(x, y) {
    const m = this._map;
    const col = Math.floor(x - m.left);
    // canvas row r -> y = bottom + (height - 1 - r); the cell is floor(r)
    const yi = m.height - 1 - Math.floor(m.height - 1 - (y - m.bottom));
    if (col < 0 || col >= m.width || yi < 0 || yi >= m.height) return 0;
    const v = this._cells[yi * m.width + col];
    return v && v < CELL_WALL ? v : 0;
  }

  _accent() {
    return getComputedStyle(this).getPropertyValue("--narwal-accent").trim() || "#926bc7";
  }

  /* ---------- drawing ---------- */
  _paintBase() {
    const m = this._map;
    const w = this._crop.c1 - this._crop.c0 + 1;
    const h = this._crop.r1 - this._crop.r0 + 1;
    const off = document.createElement("canvas");
    off.width = w; off.height = h;
    const ctx = off.getContext("2d");
    const img = ctx.createImageData(w, h);
    const accent = hexToRgb(this._accent());
    const pick = this._tab === "rooms" ? new Set(this._selected) : null;
    for (let r = 0; r < h; r++) {
      const row = r + this._crop.r0;
      const yIdx = m.height - 1 - row;
      for (let c = 0; c < w; c++) {
        const v = this._cells[yIdx * m.width + c + this._crop.c0];
        const o = (r * w + c) * 4;
        let rgb = null;
        if (v === CELL_WALL) rgb = [120, 116, 132];
        else if (v === CELL_FLOOR) rgb = [226, 224, 234];
        else if (v) {
          rgb = hexToRgb(PALETTE[v % PALETTE.length]);
          if (pick && pick.size) rgb = pick.has(v) ? mix(rgb, accent, 0.55) : mix(rgb, [245, 245, 248], 0.55);
        }
        if (rgb) { img.data[o] = rgb[0]; img.data[o + 1] = rgb[1]; img.data[o + 2] = rgb[2]; img.data[o + 3] = 255; }
      }
    }
    ctx.putImageData(img, 0, 0);
    const rot = this._rotation || 0;
    if (!rot) { this._base = off; return; }
    const out = document.createElement("canvas");
    const swap = rot === 90 || rot === 270;
    out.width = swap ? h : w; out.height = swap ? w : h;
    const octx = out.getContext("2d");
    octx.imageSmoothingEnabled = false;
    octx.translate(out.width / 2, out.height / 2);
    octx.rotate((rot * Math.PI) / 180);
    octx.drawImage(off, -w / 2, -h / 2);
    this._base = out;
  }

  _draw() {
    if (this._canvas) {
      // only capture gestures while drawing; otherwise let the page scroll
      this._canvas.style.touchAction = this._tab === "zones" || this._tab === "nogo" ? "none" : "pan-y";
    }
    if (!this._canvas || !this._map || !this._base) return;
    const box = this._mapBox.clientWidth - 24;
    if (box <= 0) return;
    const maxH = Math.max(260, Math.min(window.innerHeight * 0.62, 640));
    const bw = this._base.width, bh = this._base.height;
    this._scale = Math.min(box / bw, maxH / bh);
    const cw = Math.round(bw * this._scale), ch = Math.round(bh * this._scale);
    const dpr = window.devicePixelRatio || 1;
    const cv = this._canvas;
    if (cv.width !== cw * dpr || cv.height !== ch * dpr) {
      cv.width = cw * dpr; cv.height = ch * dpr;
      cv.style.width = `${cw}px`; cv.style.height = `${ch}px`;
    }
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(this._base, 0, 0, cw, ch);
    const accent = this._accent();

    // carpets (outline cells, hatched)
    for (const cp of this._map.carpets || []) {
      if (!cp.polygon || cp.polygon.length < 3) continue;
      ctx.beginPath();
      cp.polygon.forEach(([x, y], i) => {
        const [px, py] = this._cellToCanvas(x + 0.5, y + 0.5);
        i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
      });
      ctx.closePath();
      ctx.fillStyle = "rgba(121,85,72,.16)";
      ctx.fill();
      ctx.lineWidth = 1; ctx.strokeStyle = "rgba(121,85,72,.55)"; ctx.stroke();
    }

    // no-go zones
    for (const z of this._map.no_go_zones || []) {
      if (!z.polygon || z.polygon.length < 3) continue;
      ctx.beginPath();
      z.polygon.forEach(([x, y], i) => {
        const [px, py] = this._cellToCanvas(x, y);
        i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
      });
      ctx.closePath();
      const picked = this._tab === "nogo" && this._nogoPicked === z.id;
      ctx.fillStyle = z.type === 2 ? "rgba(33,150,243,.22)" : "rgba(219,68,55,.22)";
      ctx.fill();
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = picked ? 3 : 1.5;
      ctx.strokeStyle = z.type === 2 ? "#2196f3" : "#db4437";
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // room labels and order badges
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const placed = [];
    const overlaps = (b) => placed.some((o) => b.x < o.x + o.w && b.x + b.w > o.x && b.y < o.y + o.h && b.y + b.h > o.y);
    const order = Object.entries(this._centroids).sort((a, b) => (this._roomSize[b[0]] || 0) - (this._roomSize[a[0]] || 0));
    for (const [id, [col, row]] of order) {
      const [u, v] = this._rot(col - this._crop.c0 + 0.5, row - this._crop.r0 + 0.5);
      const px = u * this._scale, py = v * this._scale;
      const order = this._tab === "rooms" ? this._selected.indexOf(Number(id)) : -1;
      ctx.font = "500 11px var(--paper-font-body1_-_font-family, sans-serif)";
      if (order >= 0) {
        ctx.fillStyle = accent;
        ctx.beginPath(); ctx.arc(px, py - 14, 10, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "#fff"; ctx.fillText(String(order + 1), px, py - 14);
      }
      const name = this._names[id] || "";
      const sw = this._switchMode && this._showProfiles && this._tab === "rooms" ? this._roomSwitches()[id] : null;
      const prof = sw ? this._profileFor(sw) : "";
      if (name) {
        let showProf = !!prof;
        const measure = () => {
          ctx.font = "500 11px var(--paper-font-body1_-_font-family, sans-serif)";
          let w = ctx.measureText(name).width + 10;
          if (showProf) { ctx.font = "10px sans-serif"; w = Math.max(w, ctx.measureText(prof).width + 10); }
          return { w, h: showProf ? 28 : 16 };
        };
        let { w: tw, h: bh } = measure();
        // try the centroid, then nudges; drop the profile line if still crowded
        let ly = py, box = null;
        for (const pass of [0, 1]) {
          if (pass === 1 && showProf) { showProf = false; ({ w: tw, h: bh } = measure()); }
          for (const dy of [0, bh + 2, -(bh + 2), 2 * (bh + 2)]) {
            const b = { x: px - tw / 2, y: py + dy - 8, w: tw, h: bh };
            if (!overlaps(b)) { box = b; ly = py + dy; break; }
          }
          if (box) break;
        }
        if (!box) box = { x: px - tw / 2, y: py - 8, w: tw, h: bh };
        placed.push(box);
        ctx.fillStyle = "rgba(255,255,255,.86)";
        roundRect(ctx, box.x, box.y, tw, bh, 8); ctx.fill();
        ctx.font = "500 11px var(--paper-font-body1_-_font-family, sans-serif)";
        ctx.fillStyle = "#3c3a48"; ctx.fillText(name, px, ly);
        if (showProf) { ctx.font = "10px sans-serif"; ctx.fillStyle = "#6b6878"; ctx.fillText(prof, px, ly + 12); }
      }
    }

    // zones
    if (this._tab === "zones") this._zones.forEach((z, i) => this._drawRect(ctx, z, accent, String(i + 1)));
    if (this._tab === "nogo" && this._draft) this._drawRect(ctx, this._draft, this._nogoType === "no_mop" ? "#2196f3" : "#db4437", "");

    // go-to target
    if (this._tab === "goto" && this._goto) {
      const [px, py] = this._cellToCanvas(this._goto[0], this._goto[1]);
      ctx.fillStyle = accent;
      ctx.beginPath(); ctx.arc(px, py - 12, 9, Math.PI, 0); ctx.lineTo(px, py); ctx.closePath(); ctx.fill();
      ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(px, py - 12, 3.5, 0, Math.PI * 2); ctx.fill();
    }

    this._drawPath(ctx);

    // dock
    if (this._map.dock) {
      const [px, py] = this._cellToCanvas(this._map.dock.x, this._map.dock.y);
      ctx.fillStyle = "#fff"; ctx.strokeStyle = "#5f5b6e"; ctx.lineWidth = 1.5;
      roundRect(ctx, px - 7, py - 7, 14, 14, 3); ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#5f5b6e"; ctx.fillRect(px - 3, py - 3, 6, 6);
    }

    // robot (live pose if moving, else map pose)
    const live = this._live && this._live.robot;
    const robot = live || this._map.robot;
    if (robot) {
      const [px, py] = this._cellToCanvas(robot.x, robot.y);
      const heading = live ? (robot.heading_deg * Math.PI) / 180 : robot.theta || 0;
      const [hx, hy] = this._cellToCanvas(robot.x + Math.cos(heading) * 4, robot.y + Math.sin(heading) * 4);
      const hl = Math.hypot(hx - px, hy - py) || 1;
      ctx.fillStyle = accent; ctx.strokeStyle = "#fff"; ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.arc(px, py, 8, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(px, py);
      ctx.lineTo(px + ((hx - px) / hl) * 8, py + ((hy - py) / hl) * 8); ctx.stroke();
    }
  }

  _drawRect(ctx, z, color, label) {
    const p1 = this._cellToCanvas(z.x1, z.y1), p2 = this._cellToCanvas(z.x2, z.y2);
    const ax = Math.min(p1[0], p2[0]), bx = Math.max(p1[0], p2[0]);
    const ay = Math.min(p1[1], p2[1]), by = Math.max(p1[1], p2[1]);
    ctx.fillStyle = hexA(color, 0.18);
    ctx.fillRect(ax, ay, bx - ax, by - ay);
    ctx.lineWidth = 2; ctx.strokeStyle = color; ctx.strokeRect(ax, ay, bx - ax, by - ay);
    // delete (top-right) and resize (bottom-right) handles
    ctx.fillStyle = color;
    ctx.beginPath(); ctx.arc(bx, ay, 9, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(bx - 3.5, ay - 3.5); ctx.lineTo(bx + 3.5, ay + 3.5);
    ctx.moveTo(bx + 3.5, ay - 3.5); ctx.lineTo(bx - 3.5, ay + 3.5); ctx.stroke();
    ctx.fillStyle = color;
    ctx.beginPath(); ctx.arc(bx, by, 7, 0, Math.PI * 2); ctx.fill();
    if (label) {
      ctx.fillStyle = color; ctx.font = "600 12px sans-serif";
      ctx.fillText(label, ax + 10, ay + 11);
    }
    z._px = { ax, ay, bx, by };
  }

  /* ---------- pointer interaction ---------- */
  _onDown(ev) {
    if (!this._map) return;
    const p = this._point(ev);
    this._press = { x: p[0], y: p[1], moved: false };
    clearTimeout(this._holdTimer);
    if (this._tab === "rooms" && this._switchMode && this._config.room_path) {
      const c = this._canvasToCell(p[0], p[1]);
      const id = this._roomAt(c[0], c[1]);
      if (id) {
        this._holdTimer = setTimeout(() => {
          if (this._press && !this._press.moved) { this._press.held = true; this._openRoom(id); }
        }, 550);
      }
    }
    const rects = this._tab === "zones" ? this._zones : this._tab === "nogo" && this._draft ? [this._draft] : [];
    for (let i = rects.length - 1; i >= 0; i--) {
      const z = rects[i]._px;
      if (!z) continue;
      if (dist(p, [z.bx, z.ay]) < 14) { this._drag = { kind: "delete", index: i }; return; }
      if (dist(p, [z.bx, z.by]) < 14) { this._drag = { kind: "resize", rect: rects[i] }; return; }
      if (p[0] > z.ax && p[0] < z.bx && p[1] > z.ay && p[1] < z.by) {
        const c = this._canvasToCell(p[0], p[1]);
        this._drag = { kind: "move", rect: rects[i], from: c, orig: { ...rects[i] } };
        return;
      }
    }
    if (this._tab === "zones" && this._zones.length < MAX_ZONES) {
      const c = this._canvasToCell(p[0], p[1]);
      this._drag = { kind: "new", start: c };
    } else if (this._tab === "nogo") {
      const c = this._canvasToCell(p[0], p[1]);
      this._drag = { kind: "new", start: c };
    }
  }

  _onMove(ev) {
    if (!this._press) return;
    const p = this._point(ev);
    if (dist(p, [this._press.x, this._press.y]) > 6) { this._press.moved = true; clearTimeout(this._holdTimer); }
    const d = this._drag;
    if (!d || !this._press.moved) return;
    const c = this._canvasToCell(p[0], p[1]);
    if (d.kind === "new") {
      const rect = { x1: Math.round(d.start[0]), y1: Math.round(d.start[1]), x2: Math.round(c[0]), y2: Math.round(c[1]) };
      if (this._tab === "zones") {
        if (!d.rect) { d.rect = rect; this._zones.push(rect); } else Object.assign(d.rect, rect);
      } else {
        this._draft = rect; this._nogoPicked = null;
      }
    } else if (d.kind === "move") {
      const dx = Math.round(c[0] - d.from[0]), dy = Math.round(c[1] - d.from[1]);
      Object.assign(d.rect, { x1: d.orig.x1 + dx, x2: d.orig.x2 + dx, y1: d.orig.y1 + dy, y2: d.orig.y2 + dy });
    } else if (d.kind === "resize") {
      const r = d.rect;
      if (!d.anchor) {
        // anchor = the corner farthest from the grabbed handle
        const corners = [[r.x1, r.y1], [r.x2, r.y2], [r.x1, r.y2], [r.x2, r.y1]];
        d.anchor = corners.reduce((best, k) => (dist(k, c) > dist(best, c) ? k : best));
      }
      Object.assign(r, { x1: d.anchor[0], y1: d.anchor[1], x2: Math.round(c[0]), y2: Math.round(c[1]) });
    }
    this._draw();
  }

  _onUp(ev) {
    const press = this._press, d = this._drag;
    this._press = null; this._drag = null;
    clearTimeout(this._holdTimer);
    if (!press || !this._map || press.held) return;
    const zonesBefore = JSON.stringify(this._zones.map((z) => [z.x1, z.y1, z.x2, z.y2]));
    const p = this._point(ev);
    if (d && d.kind === "delete" && !press.moved) {
      if (this._tab === "zones") this._zones.splice(d.index, 1); else this._draft = null;
    } else if (d && d.kind === "new" && d.rect) {
      if (Math.abs(d.rect.x2 - d.rect.x1) < 3 || Math.abs(d.rect.y2 - d.rect.y1) < 3) this._zones.splice(this._zones.indexOf(d.rect), 1);
    } else if (!press.moved) {
      const c = this._canvasToCell(p[0], p[1]);
      if (this._tab === "rooms") {
        const id = this._roomAt(c[0], c[1]);
        if (id && this._switchMode) {
          const st = this._roomSwitches()[id];
          if (st) this._hass.callService("switch", "toggle", { entity_id: st.entity_id });
        } else if (id) {
          const i = this._selected.indexOf(id);
          i >= 0 ? this._selected.splice(i, 1) : this._selected.push(id);
          this._paintBase();
        }
      } else if (this._tab === "goto") {
        this._goto = c;
      } else if (this._tab === "nogo") {
        this._nogoPicked = this._zoneAt(c);
        if (this._nogoPicked != null) this._draft = null;
      }
    }
    if (this._tab === "nogo" && this._draft && (Math.abs(this._draft.x2 - this._draft.x1) < 3 || Math.abs(this._draft.y2 - this._draft.y1) < 3)) this._draft = null;
    if (this._tab === "zones" && JSON.stringify(this._zones.map((z) => [z.x1, z.y1, z.x2, z.y2])) !== zonesBefore) this._saveZones();
    this._draw();
    this._renderPanel();
  }

  _zoneAt([x, y]) {
    for (const z of this._map.no_go_zones || []) {
      if (z.polygon && pointInPolygon([x, y], z.polygon)) return z.id;
    }
    return null;
  }

  _point(ev) {
    const r = this._canvas.getBoundingClientRect();
    return [ev.clientX - r.left, ev.clientY - r.top];
  }

  /* ---------- actions ---------- */
  async _startRooms() {
    const ok = await this._service("clean_rooms", {
      rooms: this._selected.length ? this._selected : "all",
      mode: this._mode, freo: this._freo, passes: this._passes,
    });
    if (ok) fire(this, "hass-notification", { message: "Narwal: cleaning started" });
  }

  async _startZones() {
    const zones = this._zones.map((z) => [z.x1, z.y1, z.x2, z.y2]);
    const ok = await this._service("clean_zone", { zones, mode: this._mode, freo: this._freo, passes: this._passes });
    if (ok) fire(this, "hass-notification", { message: "Narwal: zone cleaning started" });
  }

  async _goToPoint() {
    const ok = await this._service("go_to_point", { x: +this._goto[0].toFixed(2), y: +this._goto[1].toFixed(2) });
    if (ok) fire(this, "hass-notification", { message: "Narwal: on the way" });
  }

  async _saveNogo() {
    const d = this._draft;
    const ids = (this._map.no_go_zones || []).map((z) => z.id || 0);
    const zone_id = (ids.length ? Math.max(...ids) : 0) + 1;
    const x1 = Math.min(d.x1, d.x2), x2 = Math.max(d.x1, d.x2), y1 = Math.min(d.y1, d.y2), y2 = Math.max(d.y1, d.y2);
    const ok = await this._service("add_no_go_zone", {
      polygon: [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], zone_id, type: this._nogoType,
    });
    if (ok) { this._draft = null; await this._loadMap(true); }
  }

  async _deleteNogo() {
    if (this._nogoPicked == null) return;
    if (!confirm("Delete this no-go zone?")) return;
    const ok = await this._service("remove_no_go_zone", { zone_id: this._nogoPicked });
    if (ok) { this._nogoPicked = null; await this._loadMap(true); }
  }

  /* ---------- remote control ---------- */
  async _remoteMode(mode) {
    try {
      await this._ws({ type: "narwal/remote", mode });
      this._remoteOn = mode !== "off";
    } catch (err) {
      fire(this, "hass-notification", { message: `Narwal: ${err.message || err}` });
    }
    this._renderPanel();
  }

  _padDown(ev) {
    ev.preventDefault();
    this._pad.setPointerCapture(ev.pointerId);
    if (!this._remoteOn) this._remoteMode("joystick");
    this._padMove(ev);
    this._drive = setInterval(() => this._sendVelocity(), 200);
  }

  _padMove(ev) {
    if (!this._drive && ev.type !== "pointerdown") return;
    const r = this._pad.getBoundingClientRect();
    let dx = (ev.clientX - r.left - r.width / 2) / (r.width / 2 - 32);
    let dy = (ev.clientY - r.top - r.height / 2) / (r.height / 2 - 32);
    const len = Math.hypot(dx, dy);
    if (len > 1) { dx /= len; dy /= len; }
    this._vec = [dx, dy];
    this._knob.style.transform = `translate(${dx * (r.width / 2 - 32)}px, ${dy * (r.height / 2 - 32)}px)`;
  }

  _padUp() { this._stopDriving(); }

  _sendVelocity() {
    if (!this._vec) return;
    const linear = Math.round(-this._vec[1] * this._speed);
    const angular = Math.round(-this._vec[0] * this._speed);
    this._ws({ type: "narwal/remote", linear, angular }).catch(() => {});
  }

  _stopDriving() {
    if (this._drive) {
      clearInterval(this._drive);
      this._drive = null;
      this._vec = null;
      if (this._hass) this._ws({ type: "narwal/remote", linear: 0, angular: 0 }).catch(() => {});
    }
    if (this._knob) this._knob.style.transform = "";
  }

  /* ---------- DOM ---------- */
  _build() {
    this.shadowRoot.innerHTML = `
      <style>${css}</style>
      <ha-card>
        <div class="head"><div class="title"></div><div class="status"></div></div>
        <div class="tabs"></div>
        <div class="map"><canvas class="view"></canvas></div>
        <div class="hint"></div>
        <div class="panel"></div>
      </ha-card>`;
    const root = this.shadowRoot;
    root.host.style.setProperty("--narwal-accent", this._config.accent || "var(--purple-color, #926bc7)");
    this._mapBox = root.querySelector(".map");
    this._canvas = root.querySelector("canvas");
    this._canvas.addEventListener("pointerdown", (e) => { this._canvas.setPointerCapture(e.pointerId); this._onDown(e); });
    this._canvas.addEventListener("pointermove", (e) => this._onMove(e));
    this._canvas.addEventListener("pointerup", (e) => this._onUp(e));
    this._canvas.addEventListener("pointercancel", () => { this._press = null; this._drag = null; });
    if (this._ro) this._ro.observe(this._mapBox);
    root.querySelector(".title").textContent = this._config.title || "";
    if (this._switchMode && !this._config.title) root.querySelector(".head").style.display = "none";
    this._renderTabs();
    this._renderPanel();
  }

  _renderTabs() {
    const tabs = this.shadowRoot.querySelector(".tabs");
    tabs.innerHTML = "";
    if (this._switchMode) {
      const zm = this._config.zone_mode_entity;
      tabs.style.display = zm ? "" : "none";
      if (!zm) return;
      for (const [key, label, icon] of [["rooms", "Rooms", "mdi:floor-plan"], ["zones", "Zones", "mdi:selection-drag"]]) {
        tabs.appendChild(chip(label, icon, this._tab === key, () => {
          this._hass.callService("input_boolean", key === "zones" ? "turn_on" : "turn_off", { entity_id: zm });
        }));
      }
      return;
    }
    const visible = TABS.filter(([key]) => this._allowed.includes(key));
    tabs.style.display = visible.length > 1 ? "" : "none";
    for (const [key, label, icon] of visible) {
      const b = chip(label, icon, this._tab === key, () => {
        if (this._tab === "remote" && key !== "remote") this._stopDriving();
        this._tab = key;
        this._renderTabs();
        if (this._map) this._paintBase();
        this._draw();
        this._renderPanel();
      });
      tabs.appendChild(b);
    }
  }

  _renderStatus() {
    const el = this.shadowRoot && this.shadowRoot.querySelector(".status");
    if (!el || !this._hass) return;
    const st = this._entity && this._hass.states[this._entity];
    const parts = [];
    if (st) parts.push(this._hass.formatEntityState ? this._hass.formatEntityState(st) : st.state);
    const bat = this._live && this._live.battery;
    if (bat) parts.push(`${bat}%`);
    el.textContent = parts.join(" · ");
  }

  _modeChips(bar) {
    for (const [key, label] of MODES) {
      bar.appendChild(chip(label, null, this._mode === key, () => { this._mode = key; this._renderPanel(); }));
    }
    const freo = chip("Freo Mind", "mdi:creation", this._freo, () => { this._freo = !this._freo; this._renderPanel(); });
    bar.appendChild(freo);
  }

  _passChips(bar) {
    if (this._freo) return;
    for (const n of [1, 2, 3]) {
      bar.appendChild(chip(`${n}×`, null, this._passes === n, () => { this._passes = n; this._renderPanel(); }));
    }
  }

  _renderPanel() {
    const panel = this.shadowRoot && this.shadowRoot.querySelector(".panel");
    const hint = this.shadowRoot && this.shadowRoot.querySelector(".hint");
    if (!panel) return;
    panel.innerHTML = "";
    this._mapBox.style.display = this._tab === "remote" && this._config.hide_map_in_remote ? "none" : "";
    if (this._error) {
      panel.innerHTML = `<div class="err">${escapeHtml(this._error)}</div>`;
      hint.textContent = "";
      return;
    }
    const bar = div("bar"), end = div("bar end");
    if (this._switchMode) {
      // picker mode: no description under the map (the dashboard explains itself)
      hint.textContent = "";
      hint.style.display = "none";
      if (this._tab === "zones" && this._zones.length) {
        end.appendChild(button("Clear zones", "ghost", () => { this._zones = []; this._saveZones(); this._draw(); this._renderPanel(); }));
        panel.appendChild(end);
      }
      return;
      // eslint-disable-next-line no-unreachable
      hint.style.paddingBottom = "12px";
      if (this._tab === "zones") {
        hint.textContent = this._zones.length
          ? `${this._zones.length} zone${this._zones.length > 1 ? "s" : ""} · drag to move, ● to resize, ✕ to remove`
          : "Drag on the map to draw up to 5 zones";
        if (this._zones.length) {
          end.appendChild(button("Clear zones", "ghost", () => { this._zones = []; this._saveZones(); this._draw(); this._renderPanel(); }));
          panel.appendChild(end);
        }
      } else {
        const holdHint = this._config.room_path ? " · hold a room for its settings" : "";
        hint.textContent = this._selected.length
          ? this._selected.map((id, i) => `${i + 1}. ${this._names[id] || id}`).join("  ·  ")
          : `Tap rooms in cleaning order (none = whole house)${holdHint}`;
      }
      return;
    }
    if (this._tab === "rooms") {
      hint.textContent = this._selected.length
        ? this._selected.map((id) => this._names[id]).join(" → ")
        : "Tap rooms to choose them (in cleaning order), or start the whole house";
      this._modeChips(bar); this._passChips(bar);
      end.appendChild(button("Clear", "ghost", () => { this._selected = []; this._paintBase(); this._draw(); this._renderPanel(); }, !this._selected.length));
      end.appendChild(button(this._selected.length ? `Clean ${this._selected.length} room${this._selected.length > 1 ? "s" : ""}` : "Clean whole house", "primary", () => this._startRooms()));
    } else if (this._tab === "zones") {
      hint.textContent = this._zones.length ? `${this._zones.length} zone(s) · drag to move, ● to resize, ✕ to remove` : "Drag on the map to draw a zone (up to 5)";
      this._modeChips(bar); this._passChips(bar);
      end.appendChild(button("Clear", "ghost", () => { this._zones = []; this._draw(); this._renderPanel(); }, !this._zones.length));
      end.appendChild(button("Clean zones", "primary", () => this._startZones(), !this._zones.length));
    } else if (this._tab === "goto") {
      hint.textContent = this._goto ? "Target set" : "Tap where the robot should go";
      end.appendChild(button("Go", "primary", () => this._goToPoint(), !this._goto));
    } else if (this._tab === "nogo") {
      hint.textContent = this._nogoPicked != null ? `Zone #${this._nogoPicked} selected` : "Drag to draw a zone, or tap an existing one";
      bar.appendChild(chip("No-go", "mdi:cancel", this._nogoType === "no_go", () => { this._nogoType = "no_go"; this._draw(); this._renderPanel(); }));
      bar.appendChild(chip("No-mop", "mdi:water-off", this._nogoType === "no_mop", () => { this._nogoType = "no_mop"; this._draw(); this._renderPanel(); }));
      end.appendChild(button("Delete zone", "ghost", () => this._deleteNogo(), this._nogoPicked == null));
      end.appendChild(button("Save zone", "primary", () => this._saveNogo(), !this._draft));
    } else if (this._tab === "remote") {
      hint.textContent = this._remoteOn ? "Hold and drag the joystick to drive" : "Touch the joystick to take control (the robot leaves the dock)";
      const remote = div("remote");
      const pad = div("pad"), knob = div("knob");
      pad.appendChild(knob);
      this._pad = pad; this._knob = knob;
      pad.addEventListener("pointerdown", (e) => this._padDown(e));
      pad.addEventListener("pointermove", (e) => this._padMove(e));
      pad.addEventListener("pointerup", () => this._padUp());
      pad.addEventListener("pointercancel", () => this._padUp());
      remote.appendChild(pad);
      const speeds = div("bar");
      speeds.style.justifyContent = "center";
      for (const [label, v] of [["Slow", 35], ["Normal", 60], ["Fast", 100]]) {
        speeds.appendChild(chip(label, null, this._speed === v, () => { this._speed = v; this._renderPanel(); }));
      }
      speeds.appendChild(chip("Vacuum", "mdi:fan", this._remoteOn === "sweep", async () => {
        const on = this._remoteOn === "sweep";
        await this._remoteMode(on ? "joystick" : "sweep_normal");
        this._remoteOn = on ? true : "sweep";
        this._renderPanel();
      }));
      remote.appendChild(speeds);
      panel.appendChild(remote);
      end.appendChild(button("End & return to dock", "primary", () => { this._stopDriving(); this._remoteMode("off"); }, !this._remoteOn));
    }
    if (bar.childNodes.length) panel.appendChild(bar);
    panel.appendChild(end);
  }
}

/* ---------- small helpers ---------- */
function div(cls) { const d = document.createElement("div"); d.className = cls; return d; }
function chip(label, icon, on, cb) {
  const b = document.createElement("button");
  b.className = `chip${on ? " on" : ""}`;
  if (icon) { const i = document.createElement("ha-icon"); i.setAttribute("icon", icon); b.appendChild(i); }
  b.appendChild(document.createTextNode(label));
  b.addEventListener("click", cb);
  return b;
}
function button(label, cls, cb, disabled) {
  const b = document.createElement("button");
  b.className = cls; b.textContent = label; b.disabled = !!disabled;
  b.addEventListener("click", cb);
  return b;
}
function hexToRgb(hex) {
  const h = hex.replace("#", "").trim();
  if (h.length !== 6) return [146, 107, 199];
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function mix(a, b, t) { return [0, 1, 2].map((i) => Math.round(a[i] * (1 - t) + b[i] * t)); }
function hexA(color, alpha) {
  if (!color.startsWith("#")) return color;
  const [r, g, b] = hexToRgb(color);
  return `rgba(${r},${g},${b},${alpha})`;
}
function dist(a, b) { return Math.hypot(a[0] - b[0], a[1] - b[1]); }
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
function pointInPolygon([x, y], poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }

/* ---------- visual editor ---------- */
class NarwalMapCardEditor extends HTMLElement {
  setConfig(config) { this._config = config; this._render(); }
  set hass(hass) { this._hass = hass; this._render(); }
  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => ({ entity: "Vacuum", title: "Title", default_tab: "Open on", selection: "Mode" })[s.name] || s.name;
      this._form.addEventListener("value-changed", (e) => fire(this, "config-changed", { config: e.detail.value }));
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = [
      { name: "entity", selector: { entity: { domain: "vacuum" } } },
      { name: "title", selector: { text: {} } },
      { name: "default_tab", selector: { select: { mode: "dropdown", options: TABS.map(([v, l]) => ({ value: v, label: l })) } } },
      { name: "selection", selector: { select: { mode: "dropdown", options: [{ value: "", label: "Full card" }, { value: "switches", label: "Room picker only (uses the room selection switches)" }] } } },
    ];
  }
}

customElements.define("narwal-map-card", NarwalMapCard);
customElements.define("narwal-map-card-editor", NarwalMapCardEditor);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "narwal-map-card",
  name: "Narwal map",
  description: "Clean rooms or drawn zones, go to a point, edit no-go zones and drive the robot.",
  preview: false,
});
console.info(`%c NARWAL-MAP-CARD %c ${CARD_VERSION} `, "background:#926bc7;color:#fff", "");
