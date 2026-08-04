# Embedded, minimizable Jitsi call (Track B)

Track B of `docs/embedded-call-design.md` §2: the call button opens
Jitsi **inside** Zulip and minimizes to a compact status bar, so the user keeps
chatting during the call — replacing the old `window.open(url)`.

This is the **frontend + CSP** track. It is independent of the core-hook
messaging track (`zulip-server-patch/files/jitsi_hook.py` + the conferencing service):
neither depends on the other, and they can ship in either order.

## Files

| File | Where it goes in the fork | What it is |
|---|---|---|
| `web/embedded_call.js` | `web/src/` (or inlined into `ui_init.js`) | The iframe host: start / minimize / restore / leave, single active call, in-call roster from the External API. |
| `web/embedded_call.css` | a bundled stylesheet | The full-panel ↔ compact-bar states (CSS only; minimize never unmounts the iframe). |
| `csp_settings.py` | `zproject/computed_settings.py` | Widen CSP for the meet origin so the iframe, `external_api.js`, and signalling load. |

## The swap

The call button was inlined into `web/src/ui_init.js` (a new `.ts` module
would not resolve in their webpack — never root-caused). Find where the button
handler takes the calls endpoint's response and does `window.open`:

```js
// before — pops a separate tab, call dies on tab close, no minimize
const { url } = await get_jitsi_call_url(...);
window.open(url, "_blank", "noopener");
```

and replace the open with an embedded start:

```js
import { startEmbeddedCall } from "./embedded_call";
// ...
const { url } = await get_jitsi_call_url(...);
startEmbeddedCall(url, { label: narrow_label() /* e.g. "#engineering" */ });
```

`startEmbeddedCall` derives the domain, room, and JWT from the returned `url`
(`https://<meet>/<tenant>/<room>?jwt=…`), so **no backend change is needed** for
Track B — the existing calls endpoint response is enough. (If you prefer to inline
rather than import, paste the body of `embedded_call.js` into `ui_init.js` the
same way the button was inlined; the exports become module-local functions.)

Load `embedded_call.css` wherever Zulip's app CSS is bundled.

## external_api.js — self-host it

`embedded_call.js` tries `"/external_api.js"` (same-origin) first and falls back
to `https://<meet>/external_api.js`. **Self-hosting a version-pinned copy** under
Zulip's own origin is recommended: it removes the cross-origin `script-src` from
the CSP (one less hole) and pins the API version so a meet upgrade can't change
the embed API underneath you. Copy the file from the running meet deployment
(`/usr/share/jitsi-meet/external_api.js`) into Zulip's static assets and drop the
`script-src` meet-origin entry from `csp_settings.py`.

## CSP

See `csp_settings.py`. It opens exactly one origin (`meet.zulip.davig01.net`) in
`frame-src` / `child-src` / `connect-src` (+ `script-src` only if you did **not**
self-host `external_api.js`). The exact django-csp settings shape is
version-specific — the file documents both forms and how to tell which the fork
uses.

## Per-user speaking glow — `jitsi-speaking-relay.js`

The call-aware sidebar lights up **each** participant who is speaking. The iframe
API only reports the single *dominant* speaker, so per-user speaking comes from a
small script that runs **inside** the Jitsi web app, where lib-jitsi-meet exposes
per-participant audio levels: [`jitsi-speaking-relay.js`](jitsi-speaking-relay.js).
It debounces those levels into a "who is speaking" set and `postMessage`s the
display names out to Zulip; `embedded_call.ts` validates the meet origin and glows
the matching sidebar avatars (matched by display name, so duplicate names glow
together).

- **Deploy** it in your **custom docker-jitsi-meet web image** (load it on the
  conference page with a `<script>`) — the same surface you use for other web
  tweaks; no Prosody or JVB change. Details in the file header.
- **Tune** `SPEAKING_LEVEL` / `SILENCE_MS`, and verify the **local** participant
  (your own avatar) glows — the local audio-level/name path uses web-app internals
  that vary by Jitsi version.

## Verification status — UNPROVEN, needs the dev env

This is written to
convention and is **not** exercised. Before relying on it, in the WSL2 dev env
(`~/zulip`, `./tools/run-dev`):

1. Wire the swap, add the CSP block, rebuild the web assets.
2. Click call in a channel → the iframe mounts at the app root and the call
   connects (token from the existing endpoint).
3. **Switch narrows while in-call** → the call must survive (this is the whole
   point of hosting at `document.body`, not in the narrow).
4. Minimize → compact bar, call still connected; restore → full view.
5. Leave → iframe disposed, container hidden; the JWT's 2-minute expiry means a
   fresh click re-mints.
6. Watch the browser console for CSP violations and widen precisely per the named
   directive.

## Not in v1 (design §2, §5)

- Multiple concurrent embedded calls (v1 is single active; starting another asks
  to leave the current one).
- Mobile (`zulip-flutter`) — the Jitsi Meet Flutter SDK launch, with
  minimize/PiP as its own later milestone.
