/* Lynxo Bot dashboard logic. No build step, no dependencies. */
"use strict";
const $ = (id) => document.getElementById(id);
function say(msg) { $("status").textContent = msg; }
function creds() {
  return {
    base: (localStorage.getItem("lynxo_api_base") || "").replace(/\/$/, ""),
    key: localStorage.getItem("lynxo_api_key") || "",
  };
}
async function api(path, opts) {
  opts = opts || {};
  const c = creds();
  if (!c.base || !c.key) {
    say("Enter your bot address and dashboard key above, then press Save.");
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
$("btn-save-creds").addEventListener("click", () => {
  localStorage.setItem("lynxo_api_base", $("api-base").value.trim());
  localStorage.setItem("lynxo_api_key", $("api-key").value.trim());
  say("Saved. Press Test connection to check it.");
});
$("btn-test").addEventListener("click", async () => {
  say("Testing…");
  try {
    const data = await api("/api/health");
    const g = data.guilds[0];
    if (g) {
      $("server-info").textContent = "Connected to " + g.name + " (" + g.members + " members).";
      say("Connected to " + g.name + ".");
    } else {
      say("Connected, but the bot has no server set up yet. Run /setup in Discord first.");
    }
  } catch (e) {}
});
(function restoreCreds() {
  $("api-base").value = localStorage.getItem("lynxo_api_base") || "";
  $("api-key").value = localStorage.getItem("lynxo_api_key") || "";
  if ($("api-base").value && $("api-key").value) {
    say("Welcome back. Press Test connection to reconnect.");
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
      ul.innerHTML = "<li>No backups on the bot. Press Back up now.</li>";
    }
    for (const b of data.backups) {
      const li = document.createElement("li");
      li.textContent = b.name + " (" + b.size_kb + " KB)";
      ul.appendChild(li);
    }
    say(data.backups.length + " backup(s) on the bot.");
  } catch (e) {}
});
