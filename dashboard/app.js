/* Lynxo Bot dashboard logic. No build step, no dependencies. */
"use strict";

const $ = (id) => document.getElementById(id);

function say(msg) {
  $("status").textContent = msg;
}

function creds() {
  return {
    base: (localStorage.getItem("lynxo_api_base_v2") || "").replace(/\/$/, ""),
    key: localStorage.getItem("lynxo_session") || "",
  };
}

async function api(path, opts) {
  opts = opts || {};
  const c = creds();
  if (!c.base || !c.key) {
    say("Log in with Discord above first.");
    throw new Error("not connected");
  }
  let res;
  try {
    res = await fetch(c.base + path, {
      method: opts.method || "GET",
      headers: {
        "Authorization": "Bearer " + c.key,
        "Content-Type": "application/json",
      },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    say("Could not reach the bot. Check the address — the bot may be asleep; try again in a minute.");
    throw e;
  }
  let data = {};
  try { data = await res.json(); } catch (e) { /* ignore */ }
  if (!res.ok) {
    say(data.error || ("Request failed (" + res.status + ")."));
    throw new Error(data.error || res.status);
  }
  return data;
}

/* ---------- toast ---------- */

let toastTimer = null;

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  say(msg);
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3000);
}

/* ---------- tabs ---------- */

const TAB_NAMES = ["connect", "settings", "reactionroles", "modlog", "userlookup", "emergency", "backups", "newreleases", "community", "custom", "serverbuilder", "tickets", "achievements"];

function showTab(name) {
  if (!TAB_NAMES.includes(name)) name = "connect";
  for (const n of TAB_NAMES) {
    const sec = $("tab-" + n);
    if (sec) sec.hidden = n !== name;
  }
  for (const btn of document.querySelectorAll("#side-menu button")) {
    if (btn.dataset.tab === name) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  }
  try { localStorage.setItem("lynxo_dashboard_tab", name); } catch (e) { /* ignore */ }
}

for (const btn of document.querySelectorAll("#side-menu button")) {
  btn.addEventListener("click", () => { showTab(btn.dataset.tab); closeMenu(); });
}

/* ---------- menu drawer ---------- */

function isMenuOpen() {
  return $("side-menu").classList.contains("open");
}

function openMenu() {
  $("side-menu").classList.add("open");
  $("menu-scrim").hidden = false;
  $("menu-toggle").setAttribute("aria-expanded", "true");
  const first = document.querySelector("#side-menu button");
  if (first) first.focus();
}

function closeMenu() {
  if (!isMenuOpen()) return;
  $("side-menu").classList.remove("open");
  $("menu-scrim").hidden = true;
  const t = $("menu-toggle");
  t.setAttribute("aria-expanded", "false");
  t.focus();
}

$("menu-toggle").addEventListener("click", () => {
  if (isMenuOpen()) closeMenu();
  else openMenu();
});
$("menu-scrim").addEventListener("click", closeMenu);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && isMenuOpen()) closeMenu();
});

/* ---------- connect ---------- */

function saveBase() {
  const base = $("api-base").value.trim().replace(/\/$/, "");
  localStorage.setItem("lynxo_api_base_v2", base);
  return base;
}

$("btn-discord-login").addEventListener("click", async () => {
  const base = saveBase();
  if (!base) { say("Type your bot's address first."); return; }
  say("Opening Discord login…");
  try {
    const res = await fetch(base + "/api/oauth/start");
    const data = await res.json();
    if (!res.ok || !data.url) {
      say(data.error || "The bot did not offer a login link. It may be asleep — try again in a minute.");
      return;
    }
    window.location.href = data.url;
  } catch (e) {
    say("Could not reach the bot. Check the address — the bot may be asleep; try again in a minute.");
  }
});

async function checkLogin() {
  say("Checking login…");
  try {
    const data = await api("/api/health");
    const g = data.guilds[0];
    const who = data.discord_user ? "Logged in as " + data.discord_user.username + ". " : "";
    if (g) {
      $("server-info").textContent = who + "Connected to " + g.name + " (" + g.members + " members).";
      say(who + "Connected to " + g.name + ".");
    } else {
      say(who + "Connected, but the bot has no server set up yet. Run /setup in Discord first.");
    }
  } catch (e) { /* say() already ran */ }
}

(function handleReturn() {
  const params = new URLSearchParams(window.location.search);
  const session = params.get("session");
  const error = params.get("error");
  if (session || error) {
    // Strip the query string so the token never sits in the address bar.
    window.history.replaceState({}, document.title, window.location.pathname);
  }
  if (session) {
    localStorage.setItem("lynxo_session", session);
    showTab("connect");
    checkLogin();
    return;
  }
  if (error === "not_owner") {
    say("That Discord account isn't the server owner, so it can't open this dashboard.");
    return;
  }
  if (error) {
    say("Discord login didn't finish. Press “Log in with Discord” to try again.");
    return;
  }
  // Returning visit: restore the bot address and last-open tab; re-check a saved login.
  $("api-base").value = localStorage.getItem("lynxo_api_base_v2") || "https://lynxo-bot.onrender.com";
  let savedTab = null;
  try { savedTab = localStorage.getItem("lynxo_dashboard_tab"); } catch (e) { /* ignore */ }
  showTab(savedTab || "connect");
  if ($("api-base").value && localStorage.getItem("lynxo_session")) {
    say("Welcome back. Checking your saved login…");
    checkLogin();
  }
})();

/* ---------- settings ---------- */

const SETTING_FIELDS = [
  "word_filter", "invite_filter", "link_filter", "massping_filter", "ai_moderation",
  "audit_log",
  "new_account_check", "new_account_age_days", "new_account_action",
  "ghostping_filter", "copypasta_count", "copypasta_window",
  "raid_join_threshold", "raid_join_window",
  "spam_warn_heat", "spam_timeout_heat",
  "strikes_timeout", "strikes_kick", "strikes_ban",
  "nuke_channel_threshold", "nuke_auto_restore", "nuke_action",
  "auto_backup", "auto_backup_channel_id",
  "mass_mention_filter", "mass_mention_count", "voice_raid_protection",
  "heat_slowmode", "heat_slowmode_seconds", "heat_slowmode_threshold",
  "raid_pattern_check",
  "welcome_enabled", "welcome_channel_id", "welcome_message",
  "goodbye_enabled", "goodbye_channel_id", "goodbye_message",
  "leveling_enabled", "levelup_channel_id", "level_roles",
  "starboard_enabled", "starboard_channel_id", "starboard_threshold",
  "suggest_channel_id",
  "custom_commands", "autoresponders", "tickets_enabled", "stats_enabled",
];

// Textarea fields that hold dicts, and the separator used per line.
const DICT_TEXTAREAS = {
  "custom_commands": "=",
  "autoresponders": "=",
};

function dictToTextarea(obj, sep) {
  if (!obj || typeof obj !== "object") return "";
  return Object.keys(obj).map((k) => k + " " + sep + " " + obj[k]).join("\n");
}

function textareaToDict(text, sep) {
  const out = {};
  for (const raw of String(text || "").split("\n")) {
    const i = raw.indexOf(sep);
    if (i < 0) continue;
    const k = raw.slice(0, i).trim();
    const v = raw.slice(i + sep.length).trim();
    if (k && v) out[k] = v;
  }
  return out;
}

async function ensureChannelSelect(selId, defaultLabel) {
  const sel = $(selId);
  if (!sel || sel.dataset.loaded === "1") return;
  const data = await api("/api/channels");
  const current = sel.value;
  sel.innerHTML = "";
  const def = document.createElement("option");
  def.value = "";
  def.textContent = defaultLabel;
  sel.appendChild(def);
  for (const c of data.channels) {
    const o = document.createElement("option");
    o.value = c.id;
    o.textContent = "#" + c.name;
    sel.appendChild(o);
  }
  sel.value = current;
  sel.dataset.loaded = "1";
}

async function ensureAllChannelSelects() {
  await ensureChannelSelect("set-auto_backup_channel_id", "Mod-log channel");
  await ensureChannelSelect("set-welcome_channel_id", "— pick a channel —");
  await ensureChannelSelect("set-goodbye_channel_id", "— pick a channel —");
  await ensureChannelSelect("set-levelup_channel_id", "Same channel they leveled up in");
  await ensureChannelSelect("set-starboard_channel_id", "— pick a channel —");
  await ensureChannelSelect("set-suggest_channel_id", "Off");
}

function fillSettings(s) {
  for (const name of SETTING_FIELDS) {
    if (name === "level_roles") {
      if (name in s) renderLevelRewardRows(s[name] || {});
      continue;
    }
    const el = $("set-" + name);
    if (!el || !(name in s)) continue;
    if (el.type === "checkbox") { el.checked = !!s[name]; continue; }
    if (name in DICT_TEXTAREAS) {
      el.value = dictToTextarea(s[name], DICT_TEXTAREAS[name]);
      continue;
    }
    if (s[name] == null) el.value = "";
    else el.value = s[name];
  }
}

function settingInputValue(name, el) {
  if (el.type === "checkbox") return el.checked;
  if (name === "heat_slowmode_threshold") {
    const t = el.value.trim();
    return t === "" ? null : Number(t);
  }
  if (name in DICT_TEXTAREAS) return textareaToDict(el.value, DICT_TEXTAREAS[name]);
  if (name === "level_roles") return levelRewardRowsToDict();
  if (el.tagName === "SELECT") return el.value;
  if (el.tagName === "TEXTAREA") return el.value;
  return Number(el.value);
}

async function loadAllSettings() {
  say("Loading settings…");
  try {
    await ensureAllChannelSelects();
    const data = await api("/api/settings");
    fillSettings(data.settings);
    say("Settings loaded for " + data.guild.name + ".");
  } catch (e) { /* say() already ran */ }
}

let lrRolesCache = null;
async function lrEnsureRoles() {
  if (lrRolesCache) return lrRolesCache;
  try {
    const data = await api("/api/roles");
    lrRolesCache = data.roles || [];
  } catch (e) { lrRolesCache = []; }
  return lrRolesCache;
}
function lrFillRoleSelect(sel, selectedId) {
  sel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "\u2014 pick a role \u2014";
  sel.appendChild(blank);
  for (const r of (lrRolesCache || [])) {
    const o = document.createElement("option");
    o.value = r.id;
    o.textContent = r.name;
    if (String(r.id) === String(selectedId)) o.selected = true;
    sel.appendChild(o);
  }
}
function addLevelRewardRow(level, roleId) {
  const wrap = $("level-rewards");
  if (!wrap) return;
  const row = document.createElement("div");
  row.className = "lr-row";
  const lvlLabel = document.createElement("label");
  lvlLabel.textContent = "Level ";
  const lvl = document.createElement("input");
  lvl.type = "number";
  lvl.min = "1";
  lvl.max = "100";
  lvl.className = "lr-level";
  lvl.value = level || "";
  lvl.setAttribute("aria-label", "Level number");
  lvlLabel.appendChild(lvl);
  const roleLabel = document.createElement("label");
  roleLabel.textContent = "Role ";
  const sel = document.createElement("select");
  sel.className = "lr-role";
  sel.setAttribute("aria-label", "Reward role");
  lrFillRoleSelect(sel, roleId || "");
  roleLabel.appendChild(sel);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "Remove";
  remove.setAttribute("aria-label", "Remove this level reward");
  remove.addEventListener("click", () => row.remove());
  row.appendChild(lvlLabel);
  row.appendChild(roleLabel);
  row.appendChild(remove);
  wrap.appendChild(row);
}
function renderLevelRewardRows(dict) {
  const wrap = $("level-rewards");
  if (!wrap) return;
  wrap.innerHTML = "";
  lrEnsureRoles().then(() => {
    const entries = Object.keys(dict || {}).sort((a, b) => Number(a) - Number(b));
    if (!entries.length) addLevelRewardRow("", "");
    for (const lvl of entries) addLevelRewardRow(lvl, dict[lvl]);
    for (const sel of wrap.querySelectorAll(".lr-role")) {
      lrFillRoleSelect(sel, sel.value);
    }
  });
}
function levelRewardRowsToDict() {
  const out = {};
  const wrap = $("level-rewards");
  if (!wrap) return out;
  for (const row of wrap.querySelectorAll(".lr-row")) {
    const lvl = row.querySelector(".lr-level").value.trim();
    const rid = row.querySelector(".lr-role").value;
    if (lvl && rid) out[lvl] = rid;
  }
  return out;
}
async function saveAllSettings() {
  const settings = {};
  for (const name of SETTING_FIELDS) {
    if (name === "level_roles") {
      settings[name] = levelRewardRowsToDict();
      continue;
    }
    const el = $("set-" + name);
    if (!el) continue;
    settings[name] = settingInputValue(name, el);
  }
  say("Saving…");
  try {
    const data = await api("/api/settings", { method: "PUT", body: { settings } });
    toast("Saved " + data.changed.length + " setting(s).");
  } catch (e) { /* say() already ran */ }
}

$("btn-load-settings").addEventListener("click", loadAllSettings);
$("btn-save-settings").addEventListener("click", saveAllSettings);
$("btn-load-community").addEventListener("click", loadAllSettings);
$("btn-save-community").addEventListener("click", saveAllSettings);
const lrAdd = $("btn-add-level-reward");
if (lrAdd) lrAdd.addEventListener("click", async () => {
  await lrEnsureRoles();
  addLevelRewardRow("", "");
});
$("btn-load-custom").addEventListener("click", loadAllSettings);
$("btn-save-custom").addEventListener("click", saveAllSettings);

/* ---------- mod log ---------- */

$("btn-refresh-log").addEventListener("click", async () => {
  say("Loading log…");
  try {
    const data = await api("/api/logs?limit=50");
    const ul = $("log-list");
    ul.innerHTML = "";
    if (!data.logs.length) {
      ul.innerHTML = "<li>No log entries yet.</li>";
    }
    for (const entry of data.logs.slice().reverse()) {
      const li = document.createElement("li");
      li.textContent = entry.ts + " — " + entry.text;
      ul.appendChild(li);
    }
    say("Log refreshed (" + data.logs.length + " entries).");
  } catch (e) { /* say() already ran */ }
});

/* ---------- user lookup ---------- */

let lookedUpId = null;

$("btn-lookup").addEventListener("click", async () => {
  const id = $("lookup-id").value.trim();
  if (!id) { say("Type a user ID first."); return; }
  say("Looking up…");
  try {
    const data = await api("/api/strikes?user_id=" + encodeURIComponent(id));
    lookedUpId = id;
    $("btn-clear-strikes").disabled = false;
    const box = $("lookup-result");
    let html = "<p><strong>" + (data.name || "User " + id) + "</strong> has " +
      data.count + " strike(s).</p>";
    if (data.history.length) {
      html += "<ul class='loglist'>" + data.history.map((h) =>
        "<li>" + h.ts + " — " + h.reason + " (by " + h.by + ")</li>").join("") + "</ul>";
    }
    box.innerHTML = html;
    say("Found " + data.count + " strike(s).");
  } catch (e) { /* say() already ran */ }
});

$("btn-clear-strikes").addEventListener("click", async () => {
  if (!lookedUpId) return;
  if (!confirm("Clear all strikes for this user?")) return;
  say("Clearing…");
  try {
    await api("/api/strikes/clear", { method: "POST", body: { user_id: lookedUpId } });
    $("lookup-result").innerHTML = "<p>Strikes cleared.</p>";
    $("btn-clear-strikes").disabled = true;
    say("Strikes cleared.");
  } catch (e) { /* say() already ran */ }
});

/* ---------- emergency ---------- */

async function panicCall(mode) {
  const label = mode === "lockdown" ? "full lockdown" : "panic mode";
  if (!confirm("Turn on " + label + "? This locks the server immediately.")) return;
  say("Turning on " + label + "…");
  try {
    const data = await api("/api/panic", { method: "POST", body: { mode } });
    say(data.message);
  } catch (e) { /* say() already ran */ }
}

$("btn-panic").addEventListener("click", () => panicCall("panic"));
$("btn-lockdown").addEventListener("click", () => panicCall("lockdown"));

$("btn-unlock").addEventListener("click", async () => {
  if (!confirm("Turn panic mode off and restore permissions?")) return;
  say("Unlocking…");
  try {
    const data = await api("/api/unlock", { method: "POST", body: {} });
    say(data.message);
  } catch (e) { /* say() already ran */ }
});

/* ---------- backups ---------- */

$("btn-backup-now").addEventListener("click", async () => {
  say("Making a backup… this can take a minute.");
  try {
    const data = await api("/api/backup", { method: "POST", body: {} });
    const blob = new Blob([JSON.stringify(data.snapshot, null, 2)],
                          { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = data.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    say("Backup downloaded as " + data.filename + ". Keep it somewhere safe.");
  } catch (e) { /* say() already ran */ }
});

$("btn-refresh-backups").addEventListener("click", async () => {
  say("Loading backups…");
  try {
    const data = await api("/api/backups");
    const ul = $("backup-list");
    ul.innerHTML = "";
    if (!data.backups.length) {
      ul.innerHTML = "<li>No backups on the bot. Press “Back up now”.</li>";
    }
    for (const b of data.backups) {
      const li = document.createElement("li");
      li.textContent = b.name + " (" + b.size_kb + " KB)";
      ul.appendChild(li);
    }
    say(data.backups.length + " backup(s) on the bot.");
  } catch (e) { /* say() already ran */ }
});

/* ---------- reaction roles ---------- */

function colorWord(hex) {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex || "");
  if (!m) return "a color";
  const r = parseInt(m[1].slice(0, 2), 16) / 255;
  const g = parseInt(m[1].slice(2, 4), 16) / 255;
  const b = parseInt(m[1].slice(4, 6), 16) / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
  const l = (mx + mn) / 2;
  if (l > 0.92) return "white";
  if (l < 0.08) return "black";
  if (mx - mn < 0.12) return "gray";
  const d = mx - mn;
  let h;
  if (mx === r) h = ((g - b) / d) % 6;
  else if (mx === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  h = (h * 60 + 360) % 360;
  if (h < 15 || h >= 345) return "red";
  if (h < 45) return "orange";
  if (h < 75) return "yellow";
  if (h < 155) return "green";
  if (h < 195) return "teal";
  if (h < 255) return "blue";
  if (h < 285) return "purple";
  return "pink";
}

let rrRoles = [];       // [{id, name, color, position}] from /api/roles
let rrListsLoaded = false;

function rrSay(msg) {
  $("rr-status").textContent = msg;
  say(msg);
}

async function rrEnsureLists() {
  if (rrListsLoaded) return;
  const [ch, roles] = await Promise.all([
    api("/api/channels"),
    api("/api/roles"),
  ]);
  const sel = $("rr-channel");
  sel.innerHTML = "";
  for (const c of ch.channels) {
    const o = document.createElement("option");
    o.value = c.id;
    o.textContent = "#" + c.name;
    sel.appendChild(o);
  }
  rrRoles = roles.roles;
  rrListsLoaded = true;
  // Refresh role selects in any rows already on screen.
  for (const row of document.querySelectorAll("#rr-mappings .rr-row")) {
    rrFillRoleSelect(row.querySelector(".rr-role"), row.dataset.roleId || "");
  }
}

function rrFillRoleSelect(sel, selectedId) {
  sel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "— pick a role —";
  sel.appendChild(blank);
  for (const r of rrRoles) {
    const o = document.createElement("option");
    o.value = r.id;
    o.textContent = r.name + (r.color ? " (" + colorWord(r.color) + ")" : "");
    if (r.id === selectedId) o.selected = true;
    sel.appendChild(o);
  }
}

const RR_EMOJIS = [
  ["\U0001F918", "Rock on"],
  ["\U0001F3B8", "Guitar"],
  ["\U0001F3A4", "Microphone"],
  ["\U0001F3A7", "Headphones"],
  ["\U0001F3B6", "Music notes"],
  ["\U0001F3B5", "Music note"],
  ["\U0001F941", "Drum"],
  ["\U0001F3B9", "Piano"],
  ["\U0001F479", "Ogre"],
  ["\U0001F480", "Skull"],
  ["\U0001F525", "Fire"],
  ["\U000026A1", "Lightning"],
  ["\U00002764", "Heart"],
  ["\U0001F4AF", "100"],
  ["\U0001F44D", "Thumbs up"],
  ["\U0001F44E", "Thumbs down"],
  ["\U00002705", "Check mark"],
  ["\U0000274C", "Cross mark"],
  ["\U00002B50", "Star"],
  ["\U0001F31F", "Glowing star"],
  ["\U0001F4AA", "Flexed arm"],
  ["\U0001F3AF", "Target"],
  ["\U0001F3AA", "Circus tent"],
  ["\U0001F3AD", "Masks"],
  ["\U0001F3A8", "Art"],
  ["\U0001F4E2", "Megaphone"],
  ["\U0001F4C5", "Calendar"],
  ["\U0001F39F", "Tickets"],
  ["\U0001F37A", "Beer"],
  ["\U00002615", "Coffee"],
];
function rrFillEmojiSelect(sel, selectedEmoji) {
  sel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "\u2014 pick an emoji \u2014";
  sel.appendChild(blank);
  for (const [emoji, label] of RR_EMOJIS) {
    const o = document.createElement("option");
    o.value = emoji;
    o.textContent = emoji + " " + label;
    if (emoji === selectedEmoji) o.selected = true;
    sel.appendChild(o);
  }
  const other = document.createElement("option");
  other.value = "__other__";
  other.textContent = "Other (type your own)";
  if (selectedEmoji && !RR_EMOJIS.some(([e]) => e === selectedEmoji)) {
    other.selected = true;
  }
  sel.appendChild(other);
}
function rrAddRow(mapping) {
  mapping = mapping || {};
  const row = document.createElement("div");
  row.className = "rr-row";
  row.dataset.roleId = mapping.role_id || "";

  const emojiLabel = document.createElement("label");
  emojiLabel.textContent = "Emoji ";
  const emojiSel = document.createElement("select");
  emojiSel.className = "rr-emoji-sel";
  emojiSel.setAttribute("aria-label", "Emoji");
  rrFillEmojiSelect(emojiSel, mapping.emoji || "");
  const emojiCustom = document.createElement("input");
  emojiCustom.type = "text";
  emojiCustom.className = "rr-emoji";
  emojiCustom.setAttribute("inputmode", "text");
  emojiCustom.placeholder = "\U0001F918";
  emojiCustom.maxLength = 32;
  emojiCustom.setAttribute("aria-label", "Custom emoji");
  const isCustom = mapping.emoji &&
    !RR_EMOJIS.some(([e]) => e === mapping.emoji);
  emojiCustom.value = isCustom ? mapping.emoji : "";
  emojiCustom.style.display = isCustom ? "" : "none";
  emojiSel.addEventListener("change", () => {
    emojiCustom.style.display =
      emojiSel.value === "__other__" ? "" : "none";
  });
  emojiLabel.appendChild(emojiSel);
  emojiLabel.appendChild(emojiCustom);

  const roleLabel = document.createElement("label");
  roleLabel.textContent = "Role ";
  const roleSel = document.createElement("select");
  roleSel.className = "rr-role";
  roleSel.setAttribute("aria-label", "Role");
  rrFillRoleSelect(roleSel, mapping.role_id || "");
  roleSel.addEventListener("change", () => {
    row.dataset.roleId = roleSel.value;
    // Default the label to the role name when empty.
    const labelEl = row.querySelector(".rr-label");
    if (!labelEl.value) {
      const r = rrRoles.find((x) => x.id === roleSel.value);
      if (r) labelEl.value = r.name;
    }
  });
  roleLabel.appendChild(roleSel);

  const nameLabel = document.createElement("label");
  nameLabel.textContent = "Shown name ";
  const labelInput = document.createElement("input");
  labelInput.type = "text";
  labelInput.className = "rr-label";
  labelInput.maxLength = 100;
  labelInput.value = mapping.label || "";
  labelInput.placeholder = "Metalhead";
  labelInput.setAttribute("aria-label", "Shown name");
  nameLabel.appendChild(labelInput);

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "rr-remove";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => row.remove());

  row.appendChild(emojiLabel);
  row.appendChild(roleLabel);
  row.appendChild(nameLabel);
  row.appendChild(remove);
  $("rr-mappings").appendChild(row);
}

$("btn-rr-add-row").addEventListener("click", async () => {
  try {
    await rrEnsureLists();
  } catch (e) { /* say() already ran */ return; }
  rrAddRow();
  rrSay("Row added. Fill in the emoji, role, and shown name.");
});

let rrEmbeds = [];
let rrCurrentId = "";
function rrFillEmbedForm(embed) {
  rrCurrentId = embed.id || "";
  $("rr-name").value = embed.name || "";
  $("rr-channel").value = embed.channel_id || "";
  $("rr-title").value = embed.title || "Pick your roles";
  $("rr-description").value = embed.description || "";
  $("rr-color").value = embed.color || "#FFD700";
  $("rr-image-url").value = embed.image_url || "";
  $("rr-mappings").innerHTML = "";
  for (const m of embed.mappings) rrAddRow(m);
  if (embed.message_id) {
    rrSay("Loaded " + (embed.name || "embed") + ". A message is already posted.");
  } else {
    rrSay("Loaded " + (embed.name || "embed") + ". No message posted yet.");
  }
}
function rrRefreshEmbedSelect() {
  const sel = $("rr-embed-select");
  const cur = rrCurrentId;
  sel.innerHTML = "";
  if (!rrEmbeds.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "\u2014 no embeds yet \u2014";
    sel.appendChild(o);
    return;
  }
  for (const e of rrEmbeds) {
    const o = document.createElement("option");
    o.value = e.id;
    o.textContent = (e.name || "Embed") +
      (e.message_id ? " (posted)" : " (not posted)");
    sel.appendChild(o);
  }
  sel.value = cur || (rrEmbeds[0] && rrEmbeds[0].id) || "";
}
$("rr-embed-select").addEventListener("change", () => {
  const embed = rrEmbeds.find((e) => e.id === $("rr-embed-select").value);
  if (embed) rrFillEmbedForm(embed);
});
$("btn-rr-new").addEventListener("click", () => {
  rrFillEmbedForm({ id: "", name: "", channel_id: "", title: "Pick your roles",
    description: "", color: "#FFD700", image_url: "", mappings: [],
    message_id: "" });
  rrSay("New embed. Fill it in, then Save.");
  $("rr-name").focus();
});
$("btn-rr-find").addEventListener("click", async () => {
  const raw = $("rr-find-id").value.trim();
  if (!raw) { rrSay("Paste a message ID or link first."); return; }
  rrSay("Looking up embed…");
  try {
    const data = await api("/api/reaction-roles/find",
      { method: "POST", body: { message_id: raw } });
    const embed = data.embed;
    const ix = rrEmbeds.findIndex((e) => e.id === embed.id);
    if (ix >= 0) rrEmbeds[ix] = embed; else rrEmbeds.push(embed);
    rrRefreshEmbedSelect();
    rrFillEmbedForm(embed);
    toast("Found.");
  } catch (e) { /* say() already ran */ }
});
$("btn-rr-remove").addEventListener("click", async () => {
  if (!rrCurrentId) { rrSay("Nothing to delete — this embed was never saved."); return; }
  const embed = rrEmbeds.find((e) => e.id === rrCurrentId);
  if (!confirm("Delete the embed '" + (embed && embed.name || "embed") +
               "' and its posted message?")) return;
  rrSay("Deleting embed…");
  try {
    await api("/api/reaction-roles/remove",
              { method: "POST", body: { id: rrCurrentId } });
    rrEmbeds = rrEmbeds.filter((e) => e.id !== rrCurrentId);
    rrRefreshEmbedSelect();
    const next = rrEmbeds[0];
    if (next) rrFillEmbedForm(next);
    else {
      rrCurrentId = "";
      $("rr-mappings").innerHTML = "";
      rrSay("Embed deleted. No embeds left — press New embed.");
    }
    toast("Deleted.");
  } catch (e) { /* say() already ran */ }
});
$("btn-rr-load").addEventListener("click", async () => {
  rrSay("Loading reaction roles…");
  try {
    await rrEnsureLists();
    const data = await api("/api/reaction-roles");
    rrEmbeds = data.embeds || [];
    rrRefreshEmbedSelect();
    const first = rrEmbeds.find((e) => e.id === $("rr-embed-select").value) ||
      rrEmbeds[0];
    if (first) rrFillEmbedForm(first);
    else {
      rrCurrentId = "";
      $("rr-mappings").innerHTML = "";
      rrSay("No embeds yet — press New embed to create one.");
    }
  } catch (e) { /* say() already ran */ }
});

$("btn-rr-save").addEventListener("click", async () => {
  const mappings = [];
  for (const row of document.querySelectorAll("#rr-mappings .rr-row")) {
    const emojiSel = row.querySelector(".rr-emoji-sel");
    const emojiCustom = row.querySelector(".rr-emoji");
    const emoji = (emojiSel && emojiSel.value === "__other__"
      ? emojiCustom.value
      : (emojiSel ? emojiSel.value : emojiCustom.value)).trim();
    const roleId = row.querySelector(".rr-role").value;
    const label = row.querySelector(".rr-label").value.trim();
    if (!emoji || !roleId) continue;
    mappings.push({ emoji, role_id: roleId, label });
  }
  rrSay("Saving…");
  try {
    const data = await api("/api/reaction-roles", {
      method: "POST",
      body: {
        id: rrCurrentId,
        name: $("rr-name").value.trim(),
        channel_id: $("rr-channel").value,
        title: $("rr-title").value,
        description: $("rr-description").value,
        color: $("rr-color").value,
        image_url: $("rr-image-url").value.trim(),
        mappings,
      },
    });
    const saved = data.embed;
    const ix = rrEmbeds.findIndex((e) => e.id === saved.id);
    if (ix >= 0) rrEmbeds[ix] = saved; else rrEmbeds.push(saved);
    rrCurrentId = saved.id;
    rrRefreshEmbedSelect();
    rrSay("Saved " + saved.mappings.length + " role mapping(s) in " +
      (saved.name || "embed") + ".");
    toast("Saved.");
  } catch (e) { /* say() already ran */ }
});

$("btn-rr-post").addEventListener("click", async () => {
  rrSay("Posting…");
  try {
    if (!rrCurrentId) { rrSay("Save the embed first."); return; }
    const data = await api("/api/reaction-roles/post",
      { method: "POST", body: { id: rrCurrentId } });
    rrSay("Message posted (id " + data.message_id + ").");
    toast("Posted.");
  } catch (e) { /* say() already ran */ }
});

$("btn-rr-delete").addEventListener("click", async () => {
  if (!confirm("Delete the posted reaction-role message?")) return;
  rrSay("Deleting…");
  try {
    if (!rrCurrentId) { rrSay("Nothing to delete."); return; }
    const data = await api("/api/reaction-roles/delete",
      { method: "POST", body: { id: rrCurrentId } });
    rrSay(data.deleted ? "Message deleted." : "No posted message to delete.");
    toast(data.deleted ? "Deleted." : "Nothing to delete.");
  } catch (e) { /* say() already ran */ }
});

$("btn-mycolor").addEventListener("click", async () => {
  $("mycolor-result").textContent = "Checking your name color…";
  try {
    const data = await api("/api/mycolor");
    let msg;
    if (data.display_color && data.display_role) {
      msg = "Your name should appear in " + colorWord(data.display_color) +
        ", from the " + data.display_role + " role.";
    } else {
      msg = "None of your roles have a color, so your name shows in white.";
    }
    if (data.roles && data.roles.length) {
      const bits = data.roles.map((r) =>
        r.name + " (" + (r.color ? colorWord(r.color) : "no color") + ")");
      msg += " Your roles, top to bottom: " + bits.join(", ") + ".";
    }
    $("mycolor-result").textContent = msg;
    say(msg);
  } catch (e) { /* say() already ran */ }
});

/* ---------- new releases ---------- */

let relChannelsLoaded = false;

function relSay(msg) {
  $("rel-status").textContent = msg;
  say(msg);
}

async function relEnsureChannels() {
  if (relChannelsLoaded) return;
  const data = await api("/api/channels");
  const sel = $("rel-channel");
  sel.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "— pick a channel —";
  sel.appendChild(none);
  for (const c of data.channels) {
    const o = document.createElement("option");
    o.value = c.id;
    o.textContent = "#" + c.name;
    sel.appendChild(o);
  }
  relChannelsLoaded = true;
}

function relShowRecent(recent) {
  const ul = $("rel-recent");
  ul.innerHTML = "";
  if (!recent || !recent.length) {
    const li = document.createElement("li");
    li.textContent = "Nothing announced yet.";
    ul.appendChild(li);
    return;
  }
  for (const r of recent.slice().reverse()) {
    const li = document.createElement("li");
    li.textContent = (r.artist || "Unknown artist") + " — " +
      (r.title || "Unknown title");
    ul.appendChild(li);
  }
}

$("btn-rel-load").addEventListener("click", async () => {
  relSay("Loading release alerts…");
  try {
    await relEnsureChannels();
    const data = await api("/api/releases");
    $("rel-enabled").checked = !!data.enabled;
    $("rel-channel").value = data.channel_id || "";
    relShowRecent(data.recent);
    if (data.last_check) {
      relSay("Loaded. Last checked: " + data.last_check + ".");
    } else {
      relSay("Loaded. Never checked yet — press “Check now”.");
    }
  } catch (e) { /* say() already ran */ }
});

$("btn-rel-save").addEventListener("click", async () => {
  relSay("Saving…");
  try {
    await api("/api/releases", {
      method: "POST",
      body: {
        enabled: $("rel-enabled").checked,
        channel_id: $("rel-channel").value || null,
      },
    });
    relSay("Saved.");
    toast("Saved.");
  } catch (e) { /* say() already ran */ }
});

$("btn-rel-check").addEventListener("click", async () => {
  relSay("Checking for new releases… this can take a few seconds.");
  try {
    const data = await api("/api/releases/check", {
      method: "POST", body: {},
    });
    const n = data.posted || 0;
    relSay("Done. Posted " + n + " new release" + (n === 1 ? "." : "s."));
    const cfg = await api("/api/releases");
    relShowRecent(cfg.recent);
  } catch (e) { /* say() already ran */ }
});

/* ---------- server builder ---------- */

$("btn-builder-run").addEventListener("click", async () => {
  const prompt = $("builder-prompt").value.trim();
  if (!prompt) {
    say("Type what you want changed first.");
    return;
  }
  const btn = $("btn-builder-run");
  btn.disabled = true;
  say("Working on it…");
  $("builder-result").textContent = "Working on it…";
  try {
    const data = await api("/api/editserver", {
      method: "POST",
      body: { prompt: prompt },
    });
    const lines = [];
    for (const d of (data.done || [])) lines.push(d);
    for (const s of (data.skipped || [])) lines.push("Skipped: " + s);
    $("builder-result").textContent =
      lines.length ? lines.join("\n") : "Nothing was changed.";
    toast("Done. " + (data.done || []).length + " change(s) made.");
  } catch (e) { /* say() already ran */ }
  btn.disabled = false;
});

/* ---------------- Tickets tab ---------------- */
function tkSay(msg) {
  const el = $("tickets-status");
  if (el) { el.textContent = msg; say(msg); }
}
const TK_EMOJIS = [["🎫","Ticket"],["🎟️","Tickets"],["❓","Question mark"],
  ["❔","White question"],["💬","Speech bubble"],["📩","Envelope"],
  ["📨","Incoming envelope"],["🆘","SOS"],["⚠️","Warning"],["🔧","Wrench"],
  ["🛠️","Tools"],["🤝","Handshake"],["👋","Wave"],["🙏","Folded hands"]];
function tkFillEmojiSelect(sel, current) {
  sel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "— no emoji —";
  sel.appendChild(blank);
  for (const [emoji, label] of TK_EMOJIS) {
    const o = document.createElement("option");
    o.value = emoji;
    o.textContent = emoji + " " + label;
    if (emoji === current) o.selected = true;
    sel.appendChild(o);
  }
  if (current && !TK_EMOJIS.some(([e]) => e === current)) {
    const o = document.createElement("option");
    o.value = current;
    o.textContent = current + " (current)";
    o.selected = true;
    sel.appendChild(o);
  }
}
async function tkEnsureLists() {
  // Channels
  const chSel = $("tk-channel");
  if (chSel && !chSel.dataset.loaded) {
    try {
      const data = await api("/api/channels");
      const cur = chSel.value;
      chSel.innerHTML = "";
      const def = document.createElement("option");
      def.value = "";
      def.textContent = "— pick a channel —";
      chSel.appendChild(def);
      for (const c of data.channels) {
        if (c.type !== 0) continue;
        const o = document.createElement("option");
        o.value = c.id;
        o.textContent = "#" + c.name;
        chSel.appendChild(o);
      }
      chSel.value = cur;
      chSel.dataset.loaded = "1";
    } catch (e) { /* say() already ran */ }
  }
  // Roles
  const rSel = $("tk-support-role");
  if (rSel && !rSel.dataset.loaded) {
    try {
      const data = await api("/api/roles");
      const cur = rSel.value;
      rSel.innerHTML = "";
      const def = document.createElement("option");
      def.value = "";
      def.textContent = "— no special role (admins only) —";
      rSel.appendChild(def);
      for (const r of (data.roles || [])) {
        const o = document.createElement("option");
        o.value = r.id;
        o.textContent = r.name;
        rSel.appendChild(o);
      }
      rSel.value = cur;
      rSel.dataset.loaded = "1";
    } catch (e) { /* say() already ran */ }
  }
}
function tkRenderOpenList(tickets) {
  const wrap = $("tickets-open-list");
  wrap.innerHTML = "";
  if (!tickets.length) {
    wrap.innerHTML = "<p class=\"muted\">No open tickets.</p>";
    return;
  }
  for (const t of tickets) {
    const row = document.createElement("div");
    row.className = "tk-row";
    const label = document.createElement("span");
    label.textContent = t.thread_name + " — opened by " + t.owner + " ";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "Close";
    btn.setAttribute("aria-label", "Close ticket " + t.thread_name);
    btn.addEventListener("click", async () => {
      if (!confirm("Close ticket '" + t.thread_name + "'?")) return;
      tkSay("Closing…");
      try {
        const data = await api("/api/tickets/close",
          { method: "POST", body: { thread_id: t.thread_id } });
        tkSay(data.closed ? "Ticket closed." : "Ticket was already gone.");
        toast(data.closed ? "Closed." : "Already gone.");
        tkLoadOpen();
      } catch (e) { /* say() already ran */ }
    });
    row.appendChild(label);
    row.appendChild(btn);
    wrap.appendChild(row);
  }
}
async function tkLoadOpen() {
  try {
    const data = await api("/api/tickets");
    tkRenderOpenList(data.open_tickets || []);
  } catch (e) { /* say() already ran */ }
}
$("btn-tickets-load").addEventListener("click", async () => {
  tkSay("Loading ticket settings…");
  try {
    await tkEnsureLists();
    const data = await api("/api/tickets");
    const p = data.panel || {};
    $("tk-enabled").checked = !!p.enabled;
    $("tk-support-role").value = p.support_role_id || "";
    $("tk-welcome").value = p.welcome || "";
    $("tk-channel").value = p.channel_id || "";
    $("tk-title").value = p.title || "Need help?";
    $("tk-description").value = p.description || "";
    $("tk-color").value = p.color || "#FFD700";
    $("tk-image-url").value = p.image_url || "";
    $("tk-button-text").value = p.button_text || "Open a Ticket";
    tkFillEmojiSelect($("tk-button-emoji"), p.button_emoji || "🎫");
    $("tk-button-style").value = p.button_style || "primary";
    tkRenderOpenList(data.open_tickets || []);
    tkSay(p.message_id ? "Loaded. Panel is posted." : "Loaded. Panel not posted yet.");
  } catch (e) { /* say() already ran */ }
});
$("btn-tickets-save").addEventListener("click", async () => {
  tkSay("Saving…");
  try {
    await api("/api/tickets", { method: "POST", body: {
      enabled: $("tk-enabled").checked,
      support_role_id: $("tk-support-role").value,
      welcome: $("tk-welcome").value,
      channel_id: $("tk-channel").value,
      title: $("tk-title").value,
      description: $("tk-description").value,
      color: $("tk-color").value,
      image_url: $("tk-image-url").value.trim(),
      button_text: $("tk-button-text").value,
      button_emoji: $("tk-button-emoji").value,
      button_style: $("tk-button-style").value,
    }});
    tkSay("Ticket settings saved.");
    toast("Saved.");
  } catch (e) { /* say() already ran */ }
});
$("btn-tickets-post").addEventListener("click", async () => {
  tkSay("Posting panel…");
  try {
    const data = await api("/api/tickets/post", { method: "POST", body: {} });
    tkSay("Panel posted.");
    toast("Posted.");
  } catch (e) { /* say() already ran */ }
});
$("btn-tickets-refresh").addEventListener("click", () => {
  tkSay("Refreshing…");
  tkLoadOpen();
  tkSay("List refreshed.");
});

/* ---------------- Achievements tab ---------------- */
function achSay(msg) {
  const el = $("ach-status");
  if (el) { el.textContent = msg; say(msg); }
}
const ACH_TYPES = [
  ["messages", "Messages sent"],
  ["level", "Level reached"],
  ["days", "Days active"],
  ["roles", "Reaction roles claimed"],
];
const ACH_EMOJIS = [["🏆","Trophy"],["💬","Speech"],["⭐","Star"],["🌟","Glowing star"],
  ["🔥","Fire"],["💀","Skull"],["🤘","Rock on"],["🎸","Guitar"],["👑","Crown"],
  ["💎","Gem"],["🎯","Target"],["🚀","Rocket"],["💪","Flexed arm"],["🎉","Party"]];
function achFillEmojiSelect(sel, current) {
  sel.innerHTML = "";
  for (const [emoji, label] of ACH_EMOJIS) {
    const o = document.createElement("option");
    o.value = emoji;
    o.textContent = emoji + " " + label;
    if (emoji === current) o.selected = true;
    sel.appendChild(o);
  }
  if (current && !ACH_EMOJIS.some(([e]) => e === current)) {
    const o = document.createElement("option");
    o.value = current;
    o.textContent = current + " (current)";
    o.selected = true;
    sel.appendChild(o);
  }
}
function achAddRow(d) {
  d = d || {};
  const wrap = $("ach-defs");
  const row = document.createElement("div");
  row.className = "ach-row";
  row.dataset.aid = d.id || "";
  const nameL = document.createElement("label");
  nameL.textContent = "Name ";
  const nameI = document.createElement("input");
  nameI.type = "text"; nameI.className = "ach-name"; nameI.maxLength = 100;
  nameI.value = d.name || ""; nameI.setAttribute("aria-label", "Achievement name");
  nameL.appendChild(nameI);
  const descL = document.createElement("label");
  descL.textContent = "Description ";
  const descI = document.createElement("input");
  descI.type = "text"; descI.className = "ach-desc"; descI.maxLength = 500;
  descI.value = d.description || ""; descI.setAttribute("aria-label", "Description");
  descL.appendChild(descI);
  const emojiL = document.createElement("label");
  emojiL.textContent = "Emoji ";
  const emojiS = document.createElement("select");
  emojiS.className = "ach-emoji"; emojiS.setAttribute("aria-label", "Emoji");
  achFillEmojiSelect(emojiS, d.emoji || "🏆");
  emojiL.appendChild(emojiS);
  const typeL = document.createElement("label");
  typeL.textContent = "Earned for ";
  const typeS = document.createElement("select");
  typeS.className = "ach-type"; typeS.setAttribute("aria-label", "Requirement type");
  for (const [v, label] of ACH_TYPES) {
    const o = document.createElement("option");
    o.value = v; o.textContent = label;
    if (v === d.type) o.selected = true;
    typeS.appendChild(o);
  }
  typeL.appendChild(typeS);
  const thL = document.createElement("label");
  thL.textContent = "How many ";
  const thI = document.createElement("input");
  thI.type = "number"; thI.className = "ach-threshold"; thI.min = "1"; thI.max = "100000";
  thI.value = d.threshold || ""; thI.setAttribute("aria-label", "Threshold number");
  thL.appendChild(thI);
  const remove = document.createElement("button");
  remove.type = "button"; remove.textContent = "Remove";
  remove.setAttribute("aria-label", "Remove this achievement");
  remove.addEventListener("click", () => row.remove());
  row.appendChild(nameL); row.appendChild(descL); row.appendChild(emojiL);
  row.appendChild(typeL); row.appendChild(thL); row.appendChild(remove);
  wrap.appendChild(row);
}
async function achEnsureChannels() {
  const sel = $("ach-channel");
  if (!sel || sel.dataset.loaded) return;
  try {
    const data = await api("/api/channels");
    const cur = sel.value;
    sel.innerHTML = "";
    const def = document.createElement("option");
    def.value = ""; def.textContent = "Same channel they earned it in";
    sel.appendChild(def);
    for (const c of data.channels) {
      if (c.type !== 0) continue;
      const o = document.createElement("option");
      o.value = c.id; o.textContent = "#" + c.name;
      sel.appendChild(o);
    }
    sel.value = cur;
    sel.dataset.loaded = "1";
  } catch (e) { /* say() already ran */ }
}
$("btn-ach-load").addEventListener("click", async () => {
  achSay("Loading achievements…");
  try {
    await achEnsureChannels();
    const data = await api("/api/achievements");
    $("ach-enabled").checked = !!data.enabled;
    $("ach-channel").value = data.announce_channel_id || "";
    $("ach-defs").innerHTML = "";
    for (const d of data.defs) achAddRow(d);
    achSay("Loaded " + data.defs.length + " achievement(s).");
  } catch (e) { /* say() already ran */ }
});
$("btn-ach-add").addEventListener("click", () => {
  achAddRow({});
  achSay("Achievement added. Fill it in, then Save.");
});
$("btn-ach-save").addEventListener("click", async () => {
  const defs = [];
  for (const row of document.querySelectorAll("#ach-defs .ach-row")) {
    const name = row.querySelector(".ach-name").value.trim();
    if (!name) continue;
    defs.push({
      id: row.dataset.aid || "",
      name,
      description: row.querySelector(".ach-desc").value.trim(),
      emoji: row.querySelector(".ach-emoji").value,
      type: row.querySelector(".ach-type").value,
      threshold: parseInt(row.querySelector(".ach-threshold").value, 10) || 0,
    });
  }
  achSay("Saving…");
  try {
    const data = await api("/api/achievements", { method: "POST", body: {
      enabled: $("ach-enabled").checked,
      announce_channel_id: $("ach-channel").value,
      defs,
    }});
    // Refresh ids for newly created ones.
    $("ach-defs").innerHTML = "";
    for (const d of data.achievements.defs) achAddRow(d);
    achSay("Saved " + data.achievements.defs.length + " achievement(s).");
    toast("Saved.");
  } catch (e) { /* say() already ran */ }
});
