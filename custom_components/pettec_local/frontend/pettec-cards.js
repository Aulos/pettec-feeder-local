/* PetTec cards: no build step or external runtime dependencies. */
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const CSS = `
  :host {display:block;color:var(--primary-text-color);font-family:inherit}
  ha-card {padding:20px;overflow:hidden}
  h2 {font-size:20px;font-weight:600;margin:0 0 6px}
  p {margin:0 0 18px;color:var(--secondary-text-color);font-size:14px}
  fieldset {border:0;border-top:1px solid var(--divider-color);padding:18px 0;margin:0;min-width:0}
  legend {padding:0 8px 0 0;font-weight:600;font-size:14px}
  .fields {display:flex;gap:12px;align-items:end;flex-wrap:wrap}
  label {display:flex;flex-direction:column;gap:6px;font-size:13px}
  input {font:inherit;color:inherit;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:8px;padding:10px;box-sizing:border-box;min-height:44px}
  input[type=number] {width:105px}
  .enabled {flex-direction:row;align-items:center;min-height:44px}
  input[type=checkbox] {width:20px;accent-color:var(--primary-color)}
  .days {display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px;margin:14px 0}
  .days button {padding:8px 2px;font-size:12px}
  button {font:inherit;cursor:pointer;min-height:44px;border:1px solid var(--divider-color);border-radius:8px;padding:8px 12px;background:var(--card-background-color);color:inherit}
  button[aria-pressed=true] {background:var(--primary-color);color:var(--text-primary-color,#fff);border-color:var(--primary-color)}
  .primary {background:var(--primary-color);color:var(--text-primary-color,#fff);border:0;font-weight:600}
  button:disabled,input:disabled {opacity:.5;cursor:default}
  :focus-visible {outline:2px solid var(--primary-color);outline-offset:3px}
  .status {font-size:13px;line-height:1.5;margin:10px 0 0;min-height:20px}
  .error {color:var(--error-color,#db4437)}
  :host {container-type:inline-size}
  .feed-card {padding:14px}
  .feed-header {display:flex;align-items:center;gap:10px;min-width:0}
  .feeder-art {width:42px;height:46px;flex:none;color:var(--primary-color)}
  .feed-heading {min-width:0;flex:1}
  .feed-heading h2 {font-size:16px;line-height:1.3;margin:0 0 3px;overflow-wrap:anywhere}
  .feed-caption {font-size:11px;letter-spacing:.04em;color:var(--secondary-text-color)}
  .feed-state {display:inline-flex;align-items:center;gap:5px;font-size:11px;white-space:nowrap;color:var(--secondary-text-color)}
  .feed-state::before {content:"";width:6px;height:6px;border-radius:50%;background:var(--primary-color)}
  .feed-state.offline::before {background:var(--disabled-text-color,#888)}
  .feed {display:grid;grid-template-columns:minmax(0,1fr) 44px;gap:10px;align-items:end;margin:12px 0 0}
  .feed label {font-size:11px;gap:4px;min-width:0}
  .feed input[type=number] {width:100%;font-size:16px;font-weight:500;padding:8px 10px}
  .feed button {display:flex;align-items:center;justify-content:center;width:44px;height:44px;padding:0}
  .bowl-icon {width:19px;height:19px;flex:none}
  .feed-card .status {margin-top:10px;min-height:0;font-size:12px}
  .feed-card .status:empty {display:none}
  @container (min-width:460px) {
    .feed-card {display:grid;grid-template-columns:minmax(0,1fr) minmax(160px,.75fr);align-items:center;gap:14px}
    .feed {margin:0}
    .feed-state {display:none}
    .feed-card .status {grid-column:1/-1;margin-top:0}
  }
  @container (max-width:260px) {
    .feed-state {font-size:0;gap:0}
    .feed {gap:8px;grid-template-columns:minmax(0,1fr) 44px}
  }
`;

function node(tag, text, attrs = {}) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
}
function feederGraphic(bowl = false) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", bowl ? "0 0 24 24" : "0 0 48 52");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", bowl ? "bowl-icon" : "feeder-art");
  // Fixed vector artwork, never user-supplied markup.
  svg.innerHTML = bowl
    ? '<g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h18l-2.5 7h-13zM5 20h14"/><path d="M8 7v1m4-3v3m4-1v1"/></g>'
    : '<g fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="10" y="3" width="28" height="34" rx="7" fill="currentColor" fill-opacity=".08"/><path d="M11 12h26"/><rect x="18" y="18" width="12" height="7" rx="2"/><path d="M21 32h6M24 37v3"/><path d="M7 41h34l-4 8H11z" fill="currentColor" fill-opacity=".13"/><path d="M13 50h22"/></g>';
  return svg;
}

function input(label, type, attrs = {}) {
  const wrapper = node("label", label);
  const control = node("input", undefined, {type, ...attrs});
  wrapper.append(control);
  return [wrapper, control];
}
function maskFor(label) {
  if (label === "Every day") return 127;
  if (label === "Monday–Friday") return 31;
  if (label === "Saturday–Sunday") return 96;
  if (label === "No days") return 0;
  return label.split(", ").reduce((mask, day) => {
    const bit = DAYS.indexOf(day);
    if (bit < 0) throw new Error("Unrecognized weekday state. Reload the card.");
    return mask | (1 << bit);
  }, 0);
}

class PetTecCard extends HTMLElement {
  constructor() { super(); this.attachShadow({mode: "open"}); }
  setConfig(config) {
    this.config = {...config};
    this.registry = null;
    this.loading = false;
    this.generation = (this.generation || 0) + 1;
    this.shadowRoot.replaceChildren();
    this.refresh();
  }
  set hass(hass) { this._hass = hass; this.refresh(); }
  getCardSize() { return this instanceof PetTecFeedCard ? 2 : 10; }
  static getConfigElement() { return document.createElement("pettec-card-editor"); }
  static getStubConfig() { return {device_id: ""}; }
  shell(title, description) {
    this.shadowRoot.replaceChildren(node("style", CSS));
    this.card = node("ha-card");
    this.card.append(node("h2", this.config.title || title), node("p", description));
    this.shadowRoot.append(this.card);
  }
  async refresh() {
    if (!this.config || !this._hass || this.loading) return;
    if (!this.config.device_id) {
      this.shell("PetTec", "Choose a feeder in the card editor.");
      return;
    }
    if (!this.registry) {
      this.loading = true;
      const generation = this.generation;
      try {
        const registry = await this._hass.callWS({type: "config/entity_registry/list"});
        if (generation !== this.generation) return;
        this.registry = registry.filter(e => e.platform === "pettec_local" && e.device_id === this.config.device_id);
        this.build();
      } catch (error) {
        if (generation === this.generation) this.shell("PetTec", error.message || String(error));
      } finally {
        if (generation === this.generation) this.loading = false;
      }
    }
    if (this.registry) this.update();
  }
  entity(suffix) { return this.registry.find(e => e.unique_id.endsWith(`_${suffix}`))?.entity_id; }
  state(entity) {
    const value = this._hass.states[entity]?.state;
    return value && !["unknown", "unavailable"].includes(value) ? value : null;
  }
  message(target, text, error = false) { target.textContent = text; target.classList.toggle("error", error); }
}

class PetTecFeedCard extends PetTecCard {
  build() {
    this.shell("Feed now", "");
    this.card.className = "feed-card";
    this.card.replaceChildren();
    const header = node("div", undefined, {class: "feed-header"});
    const heading = node("div", undefined, {class: "feed-heading"});
    heading.append(node("h2", this.config.title || "Feed now"), node("div", "Pet feeder", {class: "feed-caption"}));
    this.indicator = node("span", "Ready", {class: "feed-state"});
    header.append(feederGraphic(), heading, this.indicator);
    this.card.append(header);
    this.feedEntity = this.entity("feed");
    this.quantityEntity = this.entity("quantity");
    const form = node("form", undefined, {class: "feed"});
    const [label, amount] = input("Amount · g", "number", {min: 1, max: 200, step: 1, required: ""});
    this.amount = amount;
    amount.value = this.state(this.quantityEntity) || "10";
    this.submit = node("button", undefined, {class: "primary", type: "submit", "aria-label": "Dispense", title: "Dispense"});
    this.submit.append(feederGraphic(true));
    this.status = node("div", "", {class: "status", role: "status", "aria-live": "polite"});
    form.append(label, this.submit);
    this.card.append(form, this.status);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (this.busy || !form.reportValidity() || !this.feedAvailable()) return;
      this.busy = true; this.update();
      this.message(this.status, "Sending…");
      try {
        await this._hass.callService("pettec_local", "dispense", {
          entity_id: this.feedEntity, quantity: Number(amount.value),
        });
        this.message(this.status, "Dispense request acknowledged.");
      } catch (error) {
        this.message(this.status, `${error.message || error} Check the bowl before trying again; food may already have been dispensed.`, true);
      } finally { this.busy = false; this.update(); }
    });
  }
  feedAvailable() {
    const entity = this._hass.states[this.feedEntity];
    return Boolean(entity && entity.state !== "unavailable");
  }
  update() {
    if (!this.submit) return;
    const available = this.feedAvailable();
    this.indicator.textContent = !available ? "Offline" : this.busy ? "Sending" : "Ready";
    this.indicator.classList.toggle("offline", !available);
    this.submit.disabled = this.busy || !available;
    this.amount.disabled = this.busy || !available;
    if (!available) this.message(this.status, "Feeder unavailable.", true);
    else if (this.status.textContent === "Feeder unavailable.") this.message(this.status, "");
  }
}

class PetTecScheduleCard extends PetTecCard {
  build() {
    this.shell("Feeding schedule", "Saved on the feeder. Choose days, then save each meal.");
    this.rows = [];
    for (let slot = 1; slot <= 5; slot++) {
      const form = node("form");
      const fieldset = node("fieldset");
      fieldset.append(node("legend", `Meal ${slot}`));
      const fields = node("div", undefined, {class: "fields"});
      const [timeLabel, time] = input("Time", "time", {required: "", step: 60});
      const [amountLabel, amount] = input("Amount · g", "number", {min: 1, max: 200, step: 1, required: ""});
      const [enabledLabel, enabled] = input("Enabled", "checkbox");
      enabledLabel.className = "enabled";
      fields.append(timeLabel, amountLabel, enabledLabel);
      const days = node("div", undefined, {class: "days", role: "group", "aria-label": `Meal ${slot} weekdays`});
      const buttons = DAYS.map(day => {
        const button = node("button", day, {type: "button", "aria-pressed": "false"});
        days.append(button);
        return button;
      });
      const save = node("button", "Save meal", {type: "submit", class: "primary"});
      const status = node("div", "", {class: "status", role: "status", "aria-live": "polite"});
      fieldset.append(fields, days, save, status); form.append(fieldset); this.card.append(form);
      const ids = ["time", "amount", "days", "enabled"].map(field => this.entity(`schedule_${slot}_schedule_${field}`));
      const row = {slot, form, fieldset, time, amount, enabled, buttons, save, status, ids, dirty: false, busy: false};
      this.rows.push(row);
      fields.addEventListener("input", () => {row.dirty = true;});
      buttons.forEach(button => button.addEventListener("click", () => {
        button.setAttribute("aria-pressed", String(button.getAttribute("aria-pressed") !== "true")); row.dirty = true;
      }));
      form.addEventListener("submit", event => {event.preventDefault(); this.save(row);});
    }
  }
  snapshot(row) { return row.ids.map(id => this.state(id)); }
  update() {
    for (const row of this.rows || []) {
      const values = this.snapshot(row);
      const available = values.every(value => value !== null);
      row.fieldset.disabled = row.busy || !available;
      if (!available) {this.message(row.status, "Schedule unavailable.", true); continue;}
      if (row.status.textContent === "Schedule unavailable.") this.message(row.status, "");
      if (row.dirty || row.busy) continue;
      row.baseline = JSON.stringify(values);
      row.time.value = values[0].slice(0, 5);
      row.amount.value = values[1];
      row.enabled.checked = values[3] === "on";
      try {
        const mask = maskFor(values[2]);
        row.buttons.forEach((button, bit) => button.setAttribute("aria-pressed", String(Boolean(mask & (1 << bit)))));
      } catch (error) {row.fieldset.disabled = true; this.message(row.status, error.message, true);}
    }
  }
  async save(row) {
    if (row.busy || !row.form.reportValidity()) return;
    if (JSON.stringify(this.snapshot(row)) !== row.baseline) {
      row.dirty = false; this.update();
      this.message(row.status, "This meal changed elsewhere. Review its refreshed values before saving.", true); return;
    }
    const weekdays = row.buttons.reduce((mask, button, bit) => mask | (button.getAttribute("aria-pressed") === "true" ? 1 << bit : 0), 0);
    if (row.enabled.checked && !weekdays) {this.message(row.status, "Choose at least one weekday for an enabled meal.", true); return;}
    const [hour, minute] = row.time.value.split(":").map(Number);
    row.busy = true; this.update(); this.message(row.status, "Saving…");
    try {
      await this._hass.callService("pettec_local", "save_schedule", {
        entity_id: row.ids[0], slot: row.slot, enabled: row.enabled.checked,
        expected: (() => {
          const [time, quantity, days, enabled] = JSON.parse(row.baseline);
          const [hour, minute] = time.split(":").map(Number);
          return {hour, minute, quantity: Number(quantity), weekdays: maskFor(days), enabled: enabled === "on"};
        })(),
        hour, minute, quantity: Number(row.amount.value), weekdays,
      });
      row.dirty = false;
      this.message(row.status, "Saved and verified on the feeder.");
    } catch (error) {
      this.message(row.status, `${error.message || error} Review the feeder state before trying again.`, true);
      row.dirty = false;
    } finally {row.busy = false; this.update();}
  }
}

class PetTecCardEditor extends HTMLElement {
  setConfig(config) {
    this.config = {...config};
    if (this.select) this.select.value = config.device_id || "";
  }
  set hass(hass) {
    this._hass = hass;
    if (this.loading || this.select) return;
    this.loading = true;
    Promise.all([
      hass.callWS({type: "config/entity_registry/list"}),
      hass.callWS({type: "config/device_registry/list"}),
    ]).then(([entities, devices]) => {
      const ids = new Set(entities.filter(e => e.platform === "pettec_local").map(e => e.device_id));
      const label = node("label", "Feeder ");
      this.select = node("select", undefined, {"aria-label": "Feeder"});
      this.select.append(node("option", "Choose a feeder", {value: ""}));
      devices.filter(device => ids.has(device.id)).forEach(device => {
        this.select.append(node("option", device.name_by_user || device.name || device.id, {value: device.id}));
      });
      this.select.value = this.config.device_id || "";
      this.select.addEventListener("change", () => {
        this.config = {...this.config, device_id: this.select.value};
        this.dispatchEvent(new CustomEvent("config-changed", {detail: {config: this.config}, bubbles: true, composed: true}));
      });
      label.append(this.select); this.replaceChildren(label);
    }).catch(error => {this.textContent = error.message || String(error);})
      .finally(() => {this.loading = false;});
  }
}
for (const [name, component] of [["pettec-feed-card", PetTecFeedCard], ["pettec-schedule-card", PetTecScheduleCard], ["pettec-card-editor", PetTecCardEditor]]) {
  if (!customElements.get(name)) customElements.define(name, component);
}
window.customCards = window.customCards || [];
for (const [type, name, description] of [
  ["pettec-feed-card", "PetTec · Feed now", "Choose an amount and dispense one portion."],
  ["pettec-schedule-card", "PetTec · Feeding schedule", "Edit five meals with weekday selection."],
]) {
  if (!window.customCards.some(card => card.type === type)) window.customCards.push({type, name, description, preview: true});
}
