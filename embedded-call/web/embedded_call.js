// Embedded, minimizable Jitsi call inside Zulip.
//
// Track B of the embedded-call design (docs/embedded-call-design.md
// section 2). Replaces the old `window.open(url)` join with a Jitsi
// `JitsiMeetExternalAPI` iframe hosted INSIDE Zulip, so the user keeps chatting
// and navigating during the call.
//
// Three rules from the design, and each is load-bearing:
//
//   1. The call container lives at the APP ROOT (document.body), never inside a
//      message view. Zulip is a SPA; an iframe mounted in the narrow is unmounted
//      on channel switch, which drops the call. This container is created once
//      and never removed while a call is live.
//   2. Minimize is a CSS state change, NOT a DOM removal. Full view <-> a compact
//      status bar. Unmounting the iframe would leave the call, so we only ever
//      resize/reposition it.
//   3. Single active call (v1). Starting a call while one is live asks to leave
//      the current one first.
//
// The in-call roster in the status bar comes free from the External API
// (participant events); it does not need the conferencing service.
//
// Packaging note: an earlier attempt found that a brand-new .ts module would not resolve in
// Zulip's webpack (never root-caused), so the call button was inlined into
// web/src/ui_init.js. This file is written as a plain ES module with named
// exports; import it from ui_init.js, or inline its body there the same way, and
// call `startEmbeddedCall(url)` where the button currently does `window.open`.
// See README.md for the exact swap.

// Where external_api.js is served from. Self-hosting a version-pinned copy under
// Zulip's own origin avoids a cross-origin `script-src` in the CSP (see
// csp_settings.py); until that copy exists this falls back to the meet origin,
// which the CSP must then allow in `script-src`.
const EXTERNAL_API_PATH = "/external_api.js";

let externalApiPromise = null;
let current = null; // { api, url, label, minimized, participants }

// -- parsing -----------------------------------------------------------------

// The calls endpoint returns a full join URL:
//   https://meet.zulip.davig01.net/<tenant>/<room>?jwt=<token>
// The External API wants the pieces separately. Deriving them here means the
// backend response does not have to change.
export function parseJitsiUrl(rawUrl) {
    const url = new URL(rawUrl);
    const jwt = url.searchParams.get("jwt") || undefined;
    const roomName = url.pathname.replace(/^\/+/, ""); // "<tenant>/<room>"
    return { domain: url.host, roomName, jwt };
}

// -- loading the External API ------------------------------------------------

function loadExternalApi(domain) {
    if (window.JitsiMeetExternalAPI) {
        return Promise.resolve();
    }
    if (externalApiPromise) {
        return externalApiPromise;
    }
    // Prefer a same-origin, version-pinned copy; fall back to the meet origin.
    const sameOrigin = EXTERNAL_API_PATH;
    const crossOrigin = `https://${domain}/external_api.js`;
    externalApiPromise = loadScript(sameOrigin)
        .catch(() => loadScript(crossOrigin))
        .then(() => {
            if (!window.JitsiMeetExternalAPI) {
                throw new Error("external_api.js loaded but JitsiMeetExternalAPI is missing");
            }
        })
        .catch((err) => {
            externalApiPromise = null; // allow a retry on the next call
            throw err;
        });
    return externalApiPromise;
}

function loadScript(src) {
    return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = src;
        script.async = true;
        script.onload = () => resolve();
        script.onerror = () => reject(new Error(`failed to load ${src}`));
        document.head.appendChild(script);
    });
}

// -- the persistent container ------------------------------------------------

// Built once, lives at document.body for the life of the page. Everything is
// created here so the rest of the module only toggles classes and text.
function ensureContainer() {
    let root = document.getElementById("jitsi-embedded-call");
    if (root) {
        return root;
    }
    root = document.createElement("div");
    root.id = "jitsi-embedded-call";
    root.className = "jitsi-embedded-call hidden";
    root.innerHTML = `
        <div class="jec-bar">
            <span class="jec-status">In call</span>
            <span class="jec-label"></span>
            <span class="jec-count" title="people in the call"></span>
            <span class="jec-spacer"></span>
            <button type="button" class="jec-btn jec-mute" title="Mute / unmute">Mute</button>
            <button type="button" class="jec-btn jec-restore" title="Return to the call">Expand</button>
            <button type="button" class="jec-btn jec-minimize" title="Keep the call, shrink it">Minimize</button>
            <button type="button" class="jec-btn jec-leave" title="Leave the call">Leave</button>
        </div>
        <div class="jec-frame"></div>
    `;
    document.body.appendChild(root);

    root.querySelector(".jec-minimize").addEventListener("click", minimizeCall);
    root.querySelector(".jec-restore").addEventListener("click", restoreCall);
    root.querySelector(".jec-leave").addEventListener("click", leaveCall);
    root.querySelector(".jec-mute").addEventListener("click", () => {
        if (current && current.api) {
            current.api.executeCommand("toggleAudio");
        }
    });
    return root;
}

function frameNode() {
    return ensureContainer().querySelector(".jec-frame");
}

// -- lifecycle ---------------------------------------------------------------

// Start (or switch to) an embedded call from a join URL.
export async function startEmbeddedCall(rawUrl, { label = "" } = {}) {
    const { domain, roomName, jwt } = parseJitsiUrl(rawUrl);

    if (current && current.api) {
        if (current.url === rawUrl) {
            // Same call: just bring it back into view.
            restoreCall();
            return;
        }
        // Single active call (v1): confirm before dropping the live one.
        const ok = window.confirm(
            "You're already in a call. Leave it and start this one?",
        );
        if (!ok) {
            return;
        }
        disposeCurrent();
    }

    await loadExternalApi(domain);

    const api = new window.JitsiMeetExternalAPI(domain, {
        roomName,
        jwt,
        parentNode: frameNode(),
        configOverwrite: { prejoinPageEnabled: false },
    });

    current = { api, url: rawUrl, label, minimized: false, participants: 0 };
    wireApiEvents(api);
    setLabel(label);
    showContainer();
}

function wireApiEvents(api) {
    const bump = (delta) => {
        if (!current) {
            return;
        }
        current.participants = Math.max(0, current.participants + delta);
        updateCount();
    };
    // The External API gives us the roster for free — no service round-trip.
    api.addListener("videoConferenceJoined", () => bump(1));
    api.addListener("participantJoined", () => bump(1));
    api.addListener("participantLeft", () => bump(-1));
    api.addListener("videoConferenceLeft", leaveCall);
    // Fired when Jitsi itself wants to close (kicked, ended, hangup button).
    api.addListener("readyToClose", leaveCall);
}

export function minimizeCall() {
    if (!current) {
        return;
    }
    current.minimized = true;
    ensureContainer().classList.add("minimized");
}

export function restoreCall() {
    if (!current) {
        return;
    }
    current.minimized = false;
    ensureContainer().classList.remove("minimized");
}

export function leaveCall() {
    disposeCurrent();
    hideContainer();
}

export function isCallActive() {
    return current !== null;
}

function disposeCurrent() {
    if (current && current.api) {
        try {
            current.api.dispose();
        } catch (err) {
            // Disposing a half-dead call must not wedge the UI.
            console.warn("embedded call: dispose failed", err);
        }
    }
    current = null;
}

// -- small view helpers ------------------------------------------------------

function showContainer() {
    const root = ensureContainer();
    root.classList.remove("hidden", "minimized");
    updateCount();
}

function hideContainer() {
    const root = document.getElementById("jitsi-embedded-call");
    if (root) {
        root.classList.add("hidden");
        root.classList.remove("minimized");
        root.querySelector(".jec-frame").innerHTML = ""; // drop the disposed iframe
    }
}

function setLabel(label) {
    const el = ensureContainer().querySelector(".jec-label");
    el.textContent = label ? `· ${label}` : "";
}

function updateCount() {
    const el = ensureContainer().querySelector(".jec-count");
    const n = current ? current.participants : 0;
    el.textContent = n > 0 ? `· ${n} in call` : "";
}
