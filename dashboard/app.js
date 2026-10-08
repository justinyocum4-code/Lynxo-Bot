/* Lynxo Bot dashboard logic. No build step, no dependencies. */
"use strict";
const $ = (id) => document.getElementById(id);
function say(msg) { $("status").textContent = msg; }
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
  try { data = await res.json(); } catch (e) {}
  if (!res.ok) {
    say(data.error || ("Request failed (" + res.status + ")."));
    throw new Error(data.error || res.status);
  }
  return data;
}
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
  } catch (e) {}
}
(function handleReturn() {
  const params = new URLSearchParams(window.location.search);
  const session = params.get("session");
  const error = params.get("error");
  if (session || error) {
    window.history.replaceState({}, document.title, window.location.pathname);
  }
  if (session) {
    localStorage.setItem("lynxo_session", session);
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
  $("api-base").value = localStorage.getItem("lynxo_api_base_v2") || "https://lynxo-bot.onrender.com";
  if ($("api-base").value && localStorage.getItem("lynxo_session")) {
    say("Welcome back. Checking your saved login…");
    checkLogin();
  }
})();
const SETTING_FIELDS = [
  "word_filter", "invite_filter", "link_filter", "massping_filter", "ai_moderation",
  "raid_join_threshold", "raid_join_window",
  "spam_warn_heat", "spam_timeout_heat",
  "strikes_timeout", "strikes_kick", "strikes_ban",
  "nuke_channel_threshold", "nuke_auto_restore", "nuke_action",
];
function fillSettings(s) {
  for (const name of SETTING_FIELDS) {
    const el = $("set-" + name);
    if (!el || !(name in s)) continue;
    if (el.type === "checkbox") el.checked = !!s[name];
    else el.value = s[name];
  }
}
$("btn-load-settings").addEventListener("click", async () => {
  say("Loading settings…");
  try {
    const data = await api("/api/settings");
    fillSettings(data.settings);
    say("Settings loaded for " + data.guild.name + ".");
  } catch (e) {}
});
$("btn-save-settings").addEventListener("click", async () => {
  const settings = {};
  for (const name of SETTING_FIELDS) {
    const el = $("set-" + name);
    if (!el) continue;
    settings[name] = el.type === "checkbox" ? el.checked
      : el.tagName === "SELECT" ? el.value
      : Number(el.value);
  }
  say("Saving…");
  try {
    const data = await api("/api/settings", { method: "PUT", body: { settings } });
    say("Saved " + data.changed.length + " setting(s).");
  } catch (e) {}
});
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
  } catch (e) {}
});
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
  } catch (e) {}
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
  } catch (e) {}
});
async function panicCall(mode) {
  const label = mode === "lockdown" ? "full lockdown" : "panic mode";
  if (!confirm("Turn on " + label + "? This locks the server immediately.")) return;
  say("Turning on " + label + "…");
  try {
    const data = await api("/api/panic", { method: "POST", body: { mode } });
    say(data.message);
  } catch (e) {}
}
$("btn-panic").addEventListener("click", () => panicCall("panic"));
$("btn-lockdown").addEventListener("click", () => panicCall("lockdown"));
$("btn-unlock").addEventListener("click", async () => {
  if (!confirm("Turn panic mode off and restore permissions?")) return;
  say("Unlocking…");
  try {
    const data = await api("/api/unlock", { method: "POST", body: {} });
    say(data.message);
  } catch (e) {}
});
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
  } catch (e) {}
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
  } catch (e) {}
});
