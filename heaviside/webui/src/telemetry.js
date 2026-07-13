// Shared OpenConverters interaction telemetry — "what the user adds or touches".
//
// Two fire-and-forget sinks, both PRODUCTION-ONLY (never fire on localhost / the
// vite dev server / any non-*.openconverters.com host), so dev traffic never
// pollutes the data:
//
//   1) A same-origin POST to /telemetry, proxied by nginx to the local
//      `oc-telemetry` service, which writes to the OpenMagnetics Postgres under
//      the dedicated `openconverters_telemetry` schema (the "same DB, another
//      schema" pattern OM uses for `telemetry` and Heaviside for
//      `heaviside_telemetry`). Carries event_type + target + a small props blob.
//   2) window.umami.track(...) — the reused OM Umami instance served same-origin
//      under /stats. Auto-pageviews are handled by the Umami script itself; this
//      only mirrors the discrete product events.
//
// Design rules (inherited from OM's telemetry.js): never throw, swallow every
// error, and send nothing but lightweight metadata — real designs stay on the
// user's machine.

let _ctx = null;

function _uuid() {
    try {
        if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
    } catch (_) { /* fall through */ }
    // RFC-4122-ish fallback for older browsers.
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
}

// Load the self-hosted Umami tracker (same-origin /stats). `data-domains`
// enforces production-only recording inside Umami too, belt-and-suspenders with
// our own host check. No-ops without a websiteId.
function _loadUmami(site, websiteId) {
    if (!websiteId) return;
    try {
        const s = document.createElement('script');
        s.defer = true;
        s.src = '/stats/script.js';
        s.setAttribute('data-website-id', websiteId);
        s.setAttribute('data-domains', `${site}.openconverters.com`);
        document.head.appendChild(s);
    } catch (_) { /* analytics must never break the app */ }
}

// ctx: { site, umamiWebsiteId, appVersion }
//   site: 'kelvin' | 'kirchhoff' | 'heaviside' (partitions the shared table).
export function initTelemetry({ site, umamiWebsiteId = null, appVersion = null } = {}) {
    let isProd = false;
    try {
        isProd = typeof location !== 'undefined' &&
                 location.hostname.endsWith('.openconverters.com');
    } catch (_) { isProd = false; }

    let sessionId = null;
    try {
        sessionId = sessionStorage.getItem('oc_tel_sid');
        if (!sessionId) {
            sessionId = _uuid();
            sessionStorage.setItem('oc_tel_sid', sessionId);
        }
    } catch (_) { sessionId = _uuid(); }  // private mode / storage blocked

    _ctx = {
        site,
        sessionId,
        appVersion,
        environment: isProd ? 'production' : 'development',
        enabled: isProd,
    };

    if (isProd) _loadUmami(site, umamiWebsiteId);
}

// Record a discrete product interaction. `target` is the thing the user touched
// (a part MPN, a topology, a filter column, …); any extra keys become `props`.
export function trackEvent(eventType, { target = null, ...props } = {}) {
    try {
        if (!_ctx || !_ctx.enabled) return;   // dev/localhost: no-op

        const hasProps = props && Object.keys(props).length > 0;
        const body = JSON.stringify({
            site: _ctx.site,
            session_id: _ctx.sessionId,
            event_type: eventType,
            target,
            props: hasProps ? props : undefined,
            environment: _ctx.environment,
            app_version: _ctx.appVersion,
        });

        // sendBeacon survives page unload (export clicks, navigations); fall back
        // to keepalive fetch. Both are fire-and-forget.
        let sent = false;
        try {
            if (navigator && typeof navigator.sendBeacon === 'function') {
                sent = navigator.sendBeacon('/telemetry',
                    new Blob([body], { type: 'application/json' }));
            }
        } catch (_) { sent = false; }
        if (!sent) {
            fetch('/telemetry', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body,
                keepalive: true,
            }).catch(() => {});
        }

        // Mirror to Umami (lightweight props only). No-ops until the script loads.
        if (typeof window !== 'undefined' && window.umami &&
            typeof window.umami.track === 'function') {
            window.umami.track(eventType, hasProps ? { target, ...props } : { target });
        }
    } catch (_) { /* telemetry must never break the app */ }
}
