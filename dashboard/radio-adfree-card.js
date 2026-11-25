/**
 * Radio AdFree — native Lovelace card.
 * Reads the MQTT entities created by the add-on. No external dependencies.
 */
const P = "radio_adfree";
const IDS = {
  station: ["select.radio_adfree_sender", "select.radio_adfree_station"],
  mode: ["sensor.radio_adfree_status", "sensor.radio_adfree_mode"],
  ad_break: ["binary_sensor.radio_adfree_werbung_aktiv", "binary_sensor.radio_adfree_ad_break"],
  radio_title: ["sensor.radio_adfree_radio_titel", "sensor.radio_adfree_radio_title"],
  radio_artist: ["sensor.radio_adfree_radio_interpret", "sensor.radio_adfree_radio_artist"],
  filler: ["sensor.radio_adfree_ersatzsong", "sensor.radio_adfree_filler"],
  ad_remaining: ["sensor.radio_adfree_werbung_verbleibend", "sensor.radio_adfree_ad_remaining"],
  learned: ["sensor.radio_adfree_gelernte_werbedauer", "sensor.radio_adfree_learned_duration"],
  delay: ["sensor.radio_adfree_verzogerung", "sensor.radio_adfree_delay"],
  buffer: ["sensor.radio_adfree_puffer", "sensor.radio_adfree_buffer"],
  enabled: ["switch.radio_adfree_werbung_ersetzen", "switch.radio_adfree_enabled"],
  ads: ["sensor.radio_adfree_werbung_ersetzt_heute", "sensor.radio_adfree_ads_today"],
  next_ad: ["sensor.radio_adfree_nachster_werbeblock", "sensor.radio_adfree_next_ad"],
  stream_path: ["sensor.radio_adfree_stream_pfad_ingress", "sensor.radio_adfree_stream_path"],
  stream_url: ["sensor.radio_adfree_stream_url"],
  test: ["button.radio_adfree_test_werbung", "button.radio_adfree_test_ad"],
  was_ad: ["button.radio_adfree_war_werbung", "button.radio_adfree_was_ad"],
  was_no: ["button.radio_adfree_war_keine_werbung", "button.radio_adfree_was_no_ad"],
  reconnect: ["button.radio_adfree_sender_neu_verbinden", "button.radio_adfree_reconnect"],
};
const S = (hass, key) => {
  for (const id of (IDS[key] || [key])) if (hass.states[id]) return hass.states[id];
  return { state: "", attributes: {} };
};
const num = (v) => { const n = parseFloat(v); return Number.isFinite(n) ? n : 0; };
const fmt = (s) => { s = Math.max(0, Math.round(num(s))); return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); };

class RadioAdFreeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
    this._playing = false;
    this._audio = new Audio();
    this._audio.preload = "none";
    this._audio.addEventListener("playing", () => { this._playing = true; this._paintPlay(); });
    this._audio.addEventListener("pause", () => { this._playing = false; this._paintPlay(); });
    this._audio.addEventListener("ended", () => { this._playing = false; this._paintPlay(); });
  }

  setConfig(config) { this.config = config || {}; }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  getCardSize() { return 8; }

  connectedCallback() { if (this._hass && !this._built) this._build(); }
  disconnectedCallback() { try { this._audio.pause(); } catch (e) {} }

  _svc(domain, service, data) {
    return this._hass.callService(domain, service, data || {});
  }

  _eid(key) {
    const ids = IDS[key] || [key];
    return ids.find((id) => this._hass.states[id]) || ids[0];
  }

  _build() {
    this._built = true;
    const root = this.shadowRoot;
    root.innerHTML = `
      <style>
        :host { display:block; }
        ha-card { padding: 16px; overflow:hidden; }
        .head { display:flex; align-items:center; gap:12px; margin-bottom:14px; }
        .logo { width:46px;height:46px;border-radius:14px;display:grid;place-items:center;
                font-weight:800;font-size:17px;color:#fff;
                background:linear-gradient(140deg,var(--ra-accent,#e2001a),var(--ra-accent2,#ff3b4f));
                box-shadow:0 6px 18px rgba(0,0,0,.25); flex:0 0 auto; }
        .hinfo { min-width:0; flex:1; }
        .title { font-size:16px;font-weight:700;line-height:1.2; }
        .sub { font-size:12px;color:var(--secondary-text-color);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }
        .pill { display:inline-flex;align-items:center;gap:6px;border:1px solid var(--divider-color);
                border-radius:999px;padding:6px 10px;font-size:11px;color:var(--secondary-text-color);white-space:nowrap; }
        .dot { width:7px;height:7px;border-radius:50%;background:var(--success-color,#39d98a); }
        .dot.ad { background:var(--ra-accent,#e2001a); box-shadow:0 0 0 4px color-mix(in srgb,var(--ra-accent,#e2001a) 18%,transparent); }
        .grid { display:grid;grid-template-columns:1.2fr .8fr;gap:12px; }
        @media(max-width:620px){ .grid{grid-template-columns:1fr} }
        .panel { background:var(--secondary-background-color);border:1px solid var(--divider-color);
                 border-radius:14px;padding:12px; }
        .track { display:flex;align-items:center;gap:10px;min-width:0; }
        .art { width:40px;height:40px;border-radius:11px;display:grid;place-items:center;flex:0 0 auto;
               background:var(--card-background-color);border:1px solid var(--divider-color);
               color:var(--secondary-text-color);font-size:10px;font-weight:700; }
        .art.f { background:linear-gradient(140deg,var(--ra-accent,#e2001a),var(--ra-accent2,#ff3b4f));color:#fff;border:0; }
        .lab { font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--secondary-text-color);margin-bottom:2px; }
        .tt { font-size:14px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }
        .ta { font-size:11px;color:var(--secondary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }
        .arrow { text-align:center;color:var(--secondary-text-color);font-size:11px;letter-spacing:.18em;margin:7px 0; }
        .row { display:flex;align-items:center;gap:8px;margin-top:10px;flex-wrap:wrap; }
        button { font:inherit;border:0;border-radius:10px;padding:10px 12px;cursor:pointer;
                 background:var(--secondary-background-color);color:var(--primary-text-color);
                 border:1px solid var(--divider-color);font-weight:600; }
        button:hover { background:var(--card-background-color); }
        button.primary { background:linear-gradient(140deg,var(--ra-accent,#e2001a),var(--ra-accent2,#ff3b4f));
                         color:#fff;border:0;box-shadow:0 6px 16px rgba(0,0,0,.2); }
        button.small { padding:8px 10px;font-size:12px;font-weight:600; }
        button:disabled { opacity:.5;cursor:default; }
        .play { width:100%;display:flex;align-items:center;justify-content:center;gap:8px;font-size:15px; }
        select,input[type=range] { width:100%; }
        select { font:inherit;background:var(--card-background-color);color:var(--primary-text-color);
                 border:1px solid var(--divider-color);border-radius:10px;padding:10px; }
        .volrow { display:flex;align-items:center;gap:8px;margin-top:10px;color:var(--secondary-text-color);font-size:11px; }
        .adbox { margin-top:12px;border:1px solid var(--divider-color);border-radius:14px;padding:12px;
                 background:var(--secondary-background-color); }
        .adbox.on { border-color:var(--ra-accent,#e2001a);
                    background:color-mix(in srgb,var(--ra-accent,#e2001a) 10%,var(--secondary-background-color)); }
        .adhead { display:flex;align-items:center;gap:8px;font-size:12px;font-weight:700; }
        .badge { font-size:9px;letter-spacing:.1em;text-transform:uppercase;padding:4px 7px;border-radius:999px;
                 background:var(--card-background-color);color:var(--secondary-text-color);border:1px solid var(--divider-color); }
        .badge.on { background:var(--ra-accent,#e2001a);color:#fff;border:0; }
        .admeta { display:flex;justify-content:space-between;gap:8px;color:var(--secondary-text-color);font-size:11px;margin-top:6px; }
        .bar { height:6px;border-radius:99px;background:var(--divider-color);overflow:hidden;margin-top:9px; }
        .bar > i { display:block;height:100%;width:0%;background:linear-gradient(90deg,var(--ra-accent,#e2001a),var(--ra-accent2,#ff3b4f));transition:width .4s linear; }
        .stats { display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px; }
        .stat { background:var(--secondary-background-color);border:1px solid var(--divider-color);border-radius:12px;padding:9px; }
        .stat .k { font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--secondary-text-color); }
        .stat .v { font-size:16px;font-weight:800;margin-top:2px; }
        .foot { margin-top:10px;color:var(--secondary-text-color);font-size:10px;line-height:1.45; }
        .foot a { color:var(--primary-color); }
        .hint { color:var(--warning-color,#ffb020);font-size:12px;padding:8px 0; }
        .mix { opacity:.45; }
        .mix.on { opacity:1; }
      </style>
      <ha-card>
        <div class="head">
          <div class="logo" id="logo">RB</div>
          <div class="hinfo">
            <div class="title" id="station">Radio AdFree</div>
            <div class="sub" id="sub">Werbung raus. Rock rein.</div>
          </div>
          <div class="pill"><span class="dot" id="dot"></span><span id="conn">–</span></div>
          <div class="pill">Δt <b id="delay" style="color:var(--primary-text-color);margin-left:3px">–</b></div>
        </div>
        <div id="hint" class="hint" style="display:none">
          Add-on starten und MQTT verbinden. Danach erscheinen die Radio-AdFree-Entitäten automatisch.
        </div>
        <div class="grid">
          <div class="panel">
            <div class="lab">Sender</div>
            <select id="select"></select>
            <div class="row">
              <button class="primary play" id="play"><span id="playicon">▶</span><span id="playlabel">Radio starten</span></button>
            </div>
            <div class="volrow"><span>🔊</span><input id="vol" type="range" min="0" max="100" value="85"><span id="volv">85%</span></div>
            <div class="row">
              <button class="small" id="enable">Werbung ersetzen</button>
              <button class="small" id="test">Test 30 s</button>
            </div>
          </div>
          <div class="panel">
            <div class="lab">Jetzt läuft</div>
            <div class="track">
              <div class="art">RADIO</div>
              <div style="min-width:0">
                <div class="lab">Radio (live)</div>
                <div class="tt" id="rtitle">–</div>
                <div class="ta" id="rartist">–</div>
              </div>
            </div>
            <div class="arrow">↓ überblendet ↓</div>
            <div class="track mix" id="mix">
              <div class="art f">DEIN</div>
              <div style="min-width:0">
                <div class="lab">Dein Song (werbefrei)</div>
                <div class="tt" id="ftitle">–</div>
                <div class="ta" id="fartist">–</div>
              </div>
            </div>
          </div>
        </div>
        <div class="adbox" id="adbox">
          <div class="adhead"><span class="badge" id="adbadge">PROGRAMM</span><span id="adlabel">Keine Werbung erkannt</span></div>
          <div class="bar"><i id="adbar"></i></div>
          <div class="admeta"><span id="adsource">–</span><span id="adremain">–</span></div>
        </div>
        <div class="stats">
          <div class="stat"><div class="k">Ersetzt heute</div><div class="v" id="ads">0</div></div>
          <div class="stat"><div class="k">Gelernte Werbedauer</div><div class="v" id="learned">–</div></div>
          <div class="stat"><div class="k">Nächster Block</div><div class="v" id="nextad">–</div></div>
          <div class="stat"><div class="k">Puffer / Latenz</div><div class="v" id="buffer">–</div></div>
        </div>
        <div class="row">
          <button class="small" id="yes">War Werbung</button>
          <button class="small" id="no">War keine Werbung</button>
          <button class="small" id="reconnect">Neu verbinden</button>
        </div>
        <div class="foot">
          Filler: Kevin MacLeod (incompetech.com), CC BY 4.0. Radio Bob &amp; Rock Antenne sind öffentliche Webstreams.
          Dieses Add-on ersetzt Werbung nur für deinen privaten Hörgenuss und speichert keine Audiodaten.
        </div>
      </ha-card>`;
    const $ = (id) => root.getElementById(id);
    this.$ = $;
    $("play").onclick = () => this._toggle();
    $("vol").oninput = () => { const v = $("vol").value / 100; this._audio.volume = v; $("volv").textContent = $("vol").value + "%"; localStorage.setItem("rafVol", v); };
    const sv = localStorage.getItem("rafVol"); if (sv !== null) { $("vol").value = Math.round(sv * 100); $("volv").textContent = $("vol").value + "%"; this._audio.volume = parseFloat(sv); }
    $("select").onchange = (e) => this._svc("select", "select_option", { entity_id: this._eid("station"), option: e.target.value });
    $("enable").onclick = () => {
      const on = S(this._hass, "enabled").state === "on";
      this._svc("switch", on ? "turn_off" : "turn_on", { entity_id: this._eid("enabled") });
    };
    $("test").onclick = () => this._svc("button", "press", { entity_id: this._eid("test") });
    $("yes").onclick = () => this._svc("button", "press", { entity_id: this._eid("was_ad") });
    $("no").onclick = () => this._svc("button", "press", { entity_id: this._eid("was_no") });
    $("reconnect").onclick = () => this._svc("button", "press", { entity_id: this._eid("reconnect") });
  }

  _toggle() {
    if (this._playing) { this._audio.pause(); return; }
    const path = S(this._hass, "stream_path").state;
    const direct = S(this._hass, "stream_url").state;
    let src = path ? window.location.origin + path : direct;
    if (!src) { this._playing = false; this._paintPlay(); return; }
    src += (src.includes("?") ? "&" : "?") + "t=" + Date.now();
    this._audio.src = src;
    this._audio.play().then(() => { this._playing = true; this._paintPlay(); })
      .catch(() => { this._playing = false; this._paintPlay(); });
  }

  _paintPlay() {
    if (!this.$) return;
    this.$("playicon").textContent = this._playing ? "❚❚" : "▶";
    this.$("playlabel").textContent = this._playing ? "Stopp" : "Radio starten";
  }

  _update() {
    if (!this.$) return;
    const h = this._hass;
    const st = S(h, "station");
    const mode = S(h, "mode").state || "–";
    const ad = S(h, "ad_break").state === "on";
    const radioTitle = S(h, "radio_title").state || "–";
    const radioArtist = S(h, "radio_artist").state || "–";
    const filler = S(h, "filler").state || "";
    const [fa, ft] = filler.includes(" – ") ? filler.split(" – ", 2) : ["", filler];
    const remaining = num(S(h, "ad_remaining").state);
    const learned = num(S(h, "learned").state);
    const delay = num(S(h, "delay").state);
    const buffer = num(S(h, "buffer").state);
    const enabled = S(h, "enabled").state === "on";
    const avail = S(h, "station").state !== "";
    this.$("hint").style.display = avail ? "none" : "block";
    this.$("station").textContent = st.state || "Radio AdFree";
    this.$("sub").textContent = ad ? "Werbung erkannt · Ersatz läuft" : (enabled ? "Live-Radio · werbefrei" : "Werbung ersetzen ist aus");
    this.$("conn").textContent = avail ? (mode === "Werbung" || ad ? "Werbung" : "live") : "offline";
    this.$("dot").className = "dot" + (ad ? " ad" : "");
    this.$("delay").textContent = (delay || 0).toFixed(1) + " s";
    this.$("rtitle").textContent = radioTitle;
    this.$("rartist").textContent = radioArtist;
    this.$("ftitle").textContent = ft || "–";
    this.$("fartist").textContent = fa || "–";
    this.$("mix").className = "track mix" + (ad || mode === "Werbung" ? " on" : "");
    this.$("adbox").className = "adbox" + (ad || mode === "Werbung" ? " on" : "");
    this.$("adbadge").className = "badge" + (ad || mode === "Werbung" ? " on" : "");
    this.$("adbadge").textContent = ad || mode === "Werbung" ? "WERBUNG" : "PROGRAMM";
    this.$("adlabel").textContent = ad || mode === "Werbung" ? "Ersatzmusik läuft" : "Keine Werbung erkannt";
    this.$("adremain").textContent = ad || mode === "Werbung" ? "noch " + fmt(remaining) : "";
    const total = Math.max(1, learned || remaining || 1);
    this.$("adbar").style.width = (ad || mode === "Werbung") ? Math.min(100, 100 * (1 - remaining / total)) + "%" : "0%";
    this.$("ads").textContent = S(h, "ads").state || "0";
    this.$("learned").textContent = learned ? Math.round(learned) + " s" : "–";
    this.$("nextad").textContent = S(h, "next_ad").state || "–";
    this.$("buffer").textContent = (buffer || 0).toFixed(1) + " s";
    const sel = this.$("select");
    const opts = S(h, "station").attributes.options || [];
    if (opts.length && sel.options.length !== opts.length) {
      sel.innerHTML = "";
      for (const o of opts) { const el = document.createElement("option"); el.value = o; el.textContent = o; sel.appendChild(el); }
    }
    const cur = S(h, "station").state;
    if (cur && document.activeElement !== sel && sel.value !== cur) sel.value = cur;
    const isRA = (st.state || "").includes("Rock Antenne");
    this.style.setProperty("--ra-accent", isRA ? "#c8102e" : "#e2001a");
    this.style.setProperty("--ra-accent2", isRA ? "#ff5a68" : "#ff3b4f");
    if (this.$("logo")) this.$("logo").textContent = isRA ? "RA" : "RB";
    if (this.$("adsource")) this.$("adsource").textContent = mode;
    this.$("enable").textContent = enabled ? "Werbung ersetzen: AN" : "Werbung ersetzen: AUS";
  }
}

customElements.define("radio-adfree-card", RadioAdFreeCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "radio-adfree-card",
  name: "Radio AdFree",
  description: "Werbefreies Radio Bob / Rock Antenne mit Crossfade-Filler und Lernstatus.",
});
