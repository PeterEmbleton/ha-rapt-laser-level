/*
 * RAPT Keg card: every keg from the RAPT Laser Level (local BLE) integration,
 * with its drink picture, a level bar, volume and temperature.
 *
 * Served and registered by the integration itself, so it needs no separate
 * install. Card type: custom:rapt-keg-card
 */

const DOMAIN = "rapt_laser_level";
const VERSION = "0.4.1";
const POURING = "Rapid readings (pour)";

const DEFAULTS = {
  title: "On tap",
  sort: "device",
  warning: 15,
  critical: 10,
  show_image: true,
  show_volume: true,
  show_temperature: true,
  find_pictures: true,
};

// Missing drink pictures are looked up from the viewer's browser: KegLand's
// shop refuses Home Assistant's own requests but answers browsers. Each
// product is tried at most once per page load and once every 6 hours.
const LOOKUP_GAP_MS = 6 * 3600 * 1000;
const attempted = new Set();

function shopifyFeedUrl(productUrl) {
  try {
    const u = new URL(productUrl);
    if (u.protocol !== "https:" || !u.pathname.includes("/products/")) return null;
    let path = u.pathname.replace(/\/+$/, "");
    if (!path.endsWith(".js")) path += ".js";
    return u.origin + path;
  } catch (e) {
    return null;
  }
}

async function findPicture(hass, drinkEntity) {
  const attrs = hass.states[drinkEntity]?.attributes || {};
  const productUrl = attrs.product_url;
  if (!productUrl || attrs.entity_picture || attempted.has(productUrl)) return;
  attempted.add(productUrl);
  const key = `rapt-keg-card:lookup:${productUrl}`;
  try {
    if (Date.now() - Number(localStorage.getItem(key) || 0) < LOOKUP_GAP_MS) return;
    localStorage.setItem(key, String(Date.now()));
  } catch (e) {
    /* storage unavailable: the per-page-load limit still applies */
  }
  const feed = shopifyFeedUrl(productUrl);
  if (!feed) return;
  try {
    const resp = await fetch(feed, { credentials: "omit" });
    if (!resp.ok) return;
    const data = await resp.json();
    let image = data.featured_image || (data.images || [])[0];
    if (!image) return;
    if (image.startsWith("//")) image = "https:" + image;
    await hass.callWS({
      type: `${DOMAIN}/drink_found`,
      entity_id: drinkEntity,
      product_url: productUrl,
      image,
      title: data.title || null,
    });
  } catch (e) {
    console.debug("rapt-keg-card: picture lookup failed", productUrl, e);
  }
}

// Entities are found by translation key; the entity-id suffix is a fallback
// for installs whose registry hasn't picked the keys up yet.
const ROLES = ["drink", "level", "volume", "temperature", "activity"];

function findKegs(hass) {
  const byDevice = new Map();
  for (const ent of Object.values(hass.entities || {})) {
    if (ent.platform !== DOMAIN || !ent.device_id || ent.hidden) continue;
    let role = ROLES.includes(ent.translation_key) ? ent.translation_key : null;
    if (!role && ent.entity_id.startsWith("sensor.")) {
      role = ROLES.find((r) => ent.entity_id.endsWith(`_${r}`)) || null;
    }
    if (!role) continue;
    if (!byDevice.has(ent.device_id)) byDevice.set(ent.device_id, {});
    byDevice.get(ent.device_id)[role] = ent.entity_id;
  }
  const kegs = [];
  for (const [deviceId, ents] of byDevice) {
    const dev = (hass.devices || {})[deviceId] || {};
    kegs.push({ deviceId, tap: dev.name_by_user || dev.name || deviceId, ...ents });
  }
  return kegs;
}

const usable = (st) => st && !["unknown", "unavailable", ""].includes(st.state);
const num = (st) => (usable(st) && !isNaN(parseFloat(st.state)) ? parseFloat(st.state) : null);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class RaptKegCard extends HTMLElement {
  static getStubConfig() {
    return { title: DEFAULTS.title };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        {
          name: "sort",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "device", label: "Device name" },
                { value: "drink", label: "Drink name" },
                { value: "level", label: "Level, lowest first" },
              ],
            },
          },
        },
        {
          type: "grid",
          name: "",
          schema: [
            { name: "warning", selector: { number: { min: 0, max: 100, mode: "box", unit_of_measurement: "%" } } },
            { name: "critical", selector: { number: { min: 0, max: 100, mode: "box", unit_of_measurement: "%" } } },
          ],
        },
        {
          type: "grid",
          name: "",
          schema: [
            { name: "show_image", selector: { boolean: {} } },
            { name: "show_volume", selector: { boolean: {} } },
            { name: "show_temperature", selector: { boolean: {} } },
            { name: "find_pictures", selector: { boolean: {} } },
          ],
        },
      ],
      computeLabel: (s) =>
        ({
          title: "Title",
          sort: "Sort by",
          warning: "Yellow below",
          critical: "Red at or below",
          show_image: "Show picture",
          show_volume: "Show volume",
          show_temperature: "Show temperature",
          find_pictures: "Find missing pictures",
        })[s.name],
    };
  }

  setConfig(config) {
    this._config = { ...DEFAULTS, ...(config || {}) };
    this._sig = null;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
    // Saving needs an admin; other viewers just see what's there.
    if (this._config?.find_pictures && hass.user?.is_admin) {
      for (const k of this._kegs || []) if (k.drink) findPicture(hass, k.drink);
    }
  }

  getCardSize() {
    return 1 + 2 * Math.max(1, (this._kegs || []).length);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  _render() {
    if (!this._config || !this._hass || !this.shadowRoot) return;
    const hass = this._hass;
    const c = this._config;
    const kegs = findKegs(hass);
    const rows = kegs.map((k) => {
      const drinkSt = hass.states[k.drink];
      const lvl = num(hass.states[k.level]);
      return {
        ...k,
        name: usable(drinkSt) ? drinkSt.state : k.tap,
        pic: drinkSt?.attributes?.entity_picture,
        lvl,
        vol: num(hass.states[k.volume]),
        volUnit: hass.states[k.volume]?.attributes?.unit_of_measurement || "L",
        temp: num(hass.states[k.temperature]),
        tempUnit: hass.states[k.temperature]?.attributes?.unit_of_measurement || "°C",
        pouring: hass.states[k.activity]?.state === POURING,
      };
    });
    const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
    rows.sort((a, b) => {
      if (c.sort === "level") return (a.lvl ?? 101) - (b.lvl ?? 101) || collator.compare(a.tap, b.tap);
      if (c.sort === "drink") return collator.compare(a.name, b.name) || collator.compare(a.tap, b.tap);
      return collator.compare(a.tap, b.tap);
    });
    this._kegs = rows;

    // Only redraw when something shown has changed.
    const sig = JSON.stringify([c, rows]);
    if (sig === this._sig) return;
    this._sig = sig;

    const body = rows.length
      ? rows.map((r) => this._row(r)).join("")
      : `<div class="empty">No RAPT laser level sensors set up yet.</div>`;
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card class="${c.title ? "titled" : ""}" header="${esc(c.title || "")}">
        <div class="kegs">${body}</div>
      </ha-card>`;
    this.shadowRoot.querySelectorAll(".keg").forEach((el) =>
      el.addEventListener("click", () => this._moreInfo(el.dataset.entity))
    );
  }

  _row(r) {
    const c = this._config;
    const colour =
      r.lvl === null ? "none" : r.lvl <= c.critical ? "critical" : r.lvl < c.warning ? "warning" : "ok";
    const sub = [r.name !== r.tap ? esc(r.tap) : "", r.pouring ? `<span class="pouring">pouring now</span>` : ""]
      .filter(Boolean)
      .join(" · ");
    const facts = [];
    if (r.lvl !== null) facts.push(`<b>${Math.round(r.lvl)}%</b>`);
    if (c.show_volume && r.vol !== null) facts.push(`${r.vol.toFixed(1)} ${esc(r.volUnit)}`);
    if (c.show_temperature && r.temp !== null) facts.push(`${r.temp.toFixed(1)} ${esc(r.tempUnit)}`);
    const picture = c.show_image
      ? `<div class="pic">${
          r.pic ? `<img src="${esc(r.pic)}" alt="" loading="lazy">` : `<ha-icon icon="mdi:keg"></ha-icon>`
        }</div>`
      : "";
    return `
      <div class="keg" role="button" tabindex="0" data-entity="${esc(r.level || r.drink || "")}">
        ${picture}
        <div class="info">
          <div class="name">${esc(r.name)}</div>
          ${sub ? `<div class="sub">${sub}</div>` : ""}
          ${
            r.lvl === null
              ? `<div class="sub"><i>No recent reading</i></div>`
              : `<div class="bar ${colour}"><div style="width:${Math.max(0, Math.min(100, r.lvl))}%"></div></div>
                 <div class="facts">${facts.join(" · ")}</div>`
          }
        </div>
      </div>`;
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }
}

const STYLE = `
  .kegs { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  ha-card.titled .kegs { padding-top: 0; }
  .empty { color: var(--secondary-text-color); }
  .keg { display: flex; gap: 12px; align-items: center; cursor: pointer; border-radius: 8px; }
  .keg:focus-visible { outline: 2px solid var(--primary-color); }
  .pic { width: 64px; height: 64px; flex: none; display: flex; align-items: center; justify-content: center;
         border-radius: 8px; overflow: hidden; background: var(--secondary-background-color); }
  .pic img { width: 100%; height: 100%; object-fit: contain; }
  .pic ha-icon { --mdc-icon-size: 36px; color: var(--secondary-text-color); }
  .info { flex: 1; min-width: 0; }
  .name { font-weight: 500; color: var(--primary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sub, .facts { font-size: 0.9em; color: var(--secondary-text-color); }
  .pouring { color: var(--info-color, var(--primary-color)); font-weight: 500; }
  .bar { height: 8px; border-radius: 4px; background: var(--divider-color); overflow: hidden; margin: 6px 0 4px; }
  .bar > div { height: 100%; border-radius: 4px; transition: width 0.5s ease; }
  .bar.ok > div { background: var(--success-color, #43a047); }
  .bar.warning > div { background: var(--warning-color, #ffa600); }
  .bar.critical > div { background: var(--error-color, #db4437); }
`;

if (!customElements.get("rapt-keg-card")) {
  customElements.define("rapt-keg-card", RaptKegCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "rapt-keg-card",
    name: "RAPT Keg card",
    description: "Every keg's drink, level, volume and temperature, from the RAPT Laser Level integration.",
    preview: true,
    documentationURL: "https://github.com/PeterEmbleton/ha-rapt-laser-level#dashboard-card",
  });
  console.info(`%c RAPT-KEG-CARD %c ${VERSION} `, "background:#43a047;color:#fff", "");
}
