/*
 * Family Hub Fridge card — shows a Samsung Family Hub fridge's three shelf
 * pictures stacked like the inside of the fridge, how old the photo is, and
 * a refresh button. Served by the samsung_familyhub_fridge integration, so
 * no separate install is needed.
 *
 *   type: custom:familyhub-fridge-card
 *   device_id: <optional; defaults to the first fridge>
 *   title: <optional; defaults to the fridge's name>
 *   show_header: true
 */
const DOMAIN = "samsung_familyhub_fridge";
const SHELVES = ["top", "middle", "bottom"];
const NOT_FOUND_GRACE_MS = 5000;

function relativeTime(iso) {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const secs = Math.round((Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  const units = [
    ["year", 31536000], ["month", 2592000], ["week", 604800],
    ["day", 86400], ["hour", 3600], ["minute", 60],
  ];
  for (const [unit, size] of units) {
    const n = Math.floor(secs / size);
    if (n >= 1) return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
  }
  return "just now";
}

/** This integration's entities grouped by device: {deviceId: {cameras, button}}. */
function findFridges(hass) {
  const fridges = {};
  for (const ent of Object.values(hass.entities || {})) {
    if (ent.platform !== DOMAIN || !ent.device_id) continue;
    const fridge = (fridges[ent.device_id] ||= { cameras: {}, button: null });
    const [domain] = ent.entity_id.split(".");
    if (domain === "camera") {
      const shelf = SHELVES.find((s) => ent.entity_id.endsWith(`_${s}`));
      if (shelf) fridge.cameras[shelf] = ent.entity_id;
    } else if (domain === "button") {
      fridge.button = ent.entity_id;
    }
  }
  return fridges;
}

function deviceName(hass, deviceId) {
  const dev = hass.devices && hass.devices[deviceId];
  return (dev && (dev.name_by_user || dev.name)) || "Family Hub Fridge";
}

class FamilyHubFridgeCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  static getConfigElement() {
    return document.createElement("familyhub-fridge-card-editor");
  }

  setConfig(config) {
    this._config = { show_header: true, ...(config || {}) };
    this._rendered = null;
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 7;
  }

  _fridge() {
    const fridges = findFridges(this._hass);
    const id = this._config.device_id || Object.keys(fridges)[0];
    return id && fridges[id] ? { id, ...fridges[id] } : null;
  }

  /** Re-render only when something shown actually changed. */
  _signature(fridge) {
    const parts = [this._config && JSON.stringify(this._config)];
    if (fridge) {
      for (const shelf of SHELVES) {
        const st = this._hass.states[fridge.cameras[shelf]];
        parts.push(st && st.state, st && st.attributes.entity_picture,
          st && st.attributes.links_issued_at, st && st.attributes.photo_taken_at);
      }
      parts.push(deviceName(this._hass, fridge.id));
    }
    // Relative time ticks over each minute.
    parts.push(Math.floor(Date.now() / 60000));
    return parts.join("|");
  }

  _render() {
    if (!this._config || !this._hass) return;
    const fridge = this._fridge();
    const sig = this._signature(fridge);
    if (sig === this._rendered) return;
    this._rendered = sig;

    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const root = this.shadowRoot;
    root.textContent = "";

    const style = document.createElement("style");
    style.textContent = `
      ha-card { overflow: hidden; }
      .header { display: flex; align-items: center; gap: 8px;
        padding: 12px 8px 8px 16px; }
      .titles { flex: 1; min-width: 0; }
      .title { font-size: 1.15em; font-weight: 500;
        color: var(--primary-text-color); white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis; }
      .age { font-size: 0.85em; color: var(--secondary-text-color); }
      .age.stale { color: var(--warning-color, #ff9800); }
      button.refresh { background: none; border: none; cursor: pointer;
        color: var(--secondary-text-color); padding: 8px; border-radius: 50%;
        display: flex; }
      button.refresh:hover { background: var(--secondary-background-color); }
      button.refresh:focus-visible { outline: 2px solid var(--primary-color); }
      button.refresh.busy ha-icon { animation: spin 1s linear infinite; }
      @keyframes spin { to { transform: rotate(360deg); } }
      .shelves { display: flex; flex-direction: column; gap: 2px;
        background: var(--divider-color); }
      .shelf { position: relative; display: block; width: 100%; padding: 0;
        border: none; background: var(--card-background-color); cursor: pointer; }
      .shelf img { display: block; width: 100%; height: auto; }
      .shelf .label { position: absolute; left: 8px; top: 8px;
        background: rgba(0,0,0,0.55); color: #fff; font-size: 0.75em;
        padding: 2px 8px; border-radius: 10px; text-transform: capitalize; }
      .missing { padding: 24px 16px; text-align: center;
        color: var(--secondary-text-color); font-size: 0.9em; }
    `;
    root.appendChild(style);

    const card = document.createElement("ha-card");
    root.appendChild(card);

    if (!fridge) {
      const msg = document.createElement("div");
      msg.className = "missing";
      // Right after the dashboard opens, Home Assistant's entity list can
      // still be empty or incomplete. Show "loading" for a few seconds
      // before concluding the fridge really isn't there.
      if (!this._graceOver) {
        msg.textContent = "Loading fridge…";
        card.appendChild(msg);
        if (!this._graceTimer) {
          this._graceTimer = setTimeout(() => {
            this._graceOver = true;
            this._rendered = null;
            this._render();
          }, NOT_FOUND_GRACE_MS);
        }
        return;
      }
      msg.textContent = this._config.device_id
        ? "That fridge wasn't found. Check the card's fridge setting."
        : "No Samsung Family Hub fridge found. Set up the Samsung FamilyHub Fridge integration first.";
      card.appendChild(msg);
      return;
    }

    const states = SHELVES.map((s) => this._hass.states[fridge.cameras[s]]);
    const taken = states.map((st) => st && st.attributes.photo_taken_at).find(Boolean);

    if (this._config.show_header !== false) {
      const header = document.createElement("div");
      header.className = "header";
      const titles = document.createElement("div");
      titles.className = "titles";
      const title = document.createElement("div");
      title.className = "title";
      title.textContent = this._config.title || deviceName(this._hass, fridge.id);
      titles.appendChild(title);
      if (taken) {
        const age = document.createElement("div");
        age.className = "age";
        const rel = relativeTime(taken);
        age.textContent = rel ? `Photo taken ${rel}` : "";
        age.title = new Date(taken).toLocaleString();
        if (Date.now() - Date.parse(taken) > 2 * 86400000) age.classList.add("stale");
        titles.appendChild(age);
      }
      header.appendChild(titles);

      if (fridge.button) {
        const btn = document.createElement("button");
        btn.className = "refresh";
        btn.title = "Refresh pictures";
        btn.setAttribute("aria-label", "Refresh pictures");
        const icon = document.createElement("ha-icon");
        icon.setAttribute("icon", "mdi:camera-retake");
        btn.appendChild(icon);
        btn.addEventListener("click", async () => {
          btn.classList.add("busy");
          try {
            await this._hass.callService("button", "press", { entity_id: fridge.button });
          } finally {
            setTimeout(() => btn.classList.remove("busy"), 1500);
          }
        });
        header.appendChild(btn);
      }
      card.appendChild(header);
    }

    const shelves = document.createElement("div");
    shelves.className = "shelves";
    SHELVES.forEach((shelf, i) => {
      const st = states[i];
      const entityId = fridge.cameras[shelf];
      const pic = st && st.state !== "unavailable" && st.attributes.entity_picture;
      if (!pic) {
        const missing = document.createElement("div");
        missing.className = "missing";
        missing.textContent = `${shelf[0].toUpperCase()}${shelf.slice(1)} shelf: no picture yet`;
        shelves.appendChild(missing);
        return;
      }
      const btn = document.createElement("button");
      btn.className = "shelf";
      btn.setAttribute("aria-label", `${shelf} shelf, open full size`);
      const img = document.createElement("img");
      img.alt = `${shelf} shelf`;
      img.loading = "lazy";
      // Bust the browser cache when Samsung republishes the photos.
      const bust = encodeURIComponent(st.attributes.links_issued_at || st.last_updated || "");
      img.src = `${pic}${pic.includes("?") ? "&" : "?"}v=${bust}`;
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = shelf;
      btn.append(img, label);
      btn.addEventListener("click", () => {
        this.dispatchEvent(new CustomEvent("hass-more-info", {
          detail: { entityId }, bubbles: true, composed: true,
        }));
      });
      shelves.appendChild(btn);
    });
    card.appendChild(shelves);
  }
}

class FamilyHubFridgeCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...(config || {}) };
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._render();
  }

  _changed(key, value) {
    const config = { ...this._config };
    if (value === "" || value === undefined) delete config[key];
    else config[key] = value;
    this._config = config;
    this.dispatchEvent(new CustomEvent("config-changed", {
      detail: { config }, bubbles: true, composed: true,
    }));
  }

  _render() {
    if (!this._hass || !this._config) return;
    this.textContent = "";
    const wrap = document.createElement("div");
    wrap.style.cssText = "display:flex;flex-direction:column;gap:12px;";

    const fridges = findFridges(this._hass);
    const select = document.createElement("select");
    const auto = document.createElement("option");
    auto.value = "";
    auto.textContent = "First fridge found";
    select.appendChild(auto);
    for (const id of Object.keys(fridges)) {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = deviceName(this._hass, id);
      if (id === this._config.device_id) opt.selected = true;
      select.appendChild(opt);
    }
    select.addEventListener("change", () => this._changed("device_id", select.value));
    wrap.appendChild(this._labelled("Fridge", select));

    const title = document.createElement("input");
    title.type = "text";
    title.placeholder = "Fridge name";
    title.value = this._config.title || "";
    title.addEventListener("change", () => this._changed("title", title.value));
    wrap.appendChild(this._labelled("Title", title));

    const header = document.createElement("input");
    header.type = "checkbox";
    header.checked = this._config.show_header !== false;
    header.addEventListener("change", () => this._changed("show_header", header.checked ? undefined : false));
    wrap.appendChild(this._labelled("Show header", header));

    this.appendChild(wrap);
  }

  _labelled(text, control) {
    const label = document.createElement("label");
    label.style.cssText = "display:flex;flex-direction:column;gap:4px;font-size:0.9em;";
    if (control.type === "checkbox") label.style.flexDirection = "row";
    const span = document.createElement("span");
    span.textContent = text;
    label.append(span, control);
    return label;
  }
}

if (!customElements.get("familyhub-fridge-card")) {
  customElements.define("familyhub-fridge-card", FamilyHubFridgeCard);
  customElements.define("familyhub-fridge-card-editor", FamilyHubFridgeCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "familyhub-fridge-card",
    name: "Family Hub Fridge",
    description: "Samsung Family Hub shelf pictures, photo age and a refresh button.",
    preview: true,
  });
}
