// Homebound focus script: reports the active window caption + geometry to Homebound over D-Bus.
// Homebound registers org.homebound on the session bus; calls are dropped silently if it isn't running.
function report(w) {
    var cap = "", cls = "", x = 0, y = 0, wd = 0, ht = 0;
    if (w) {
        cap = String(w.caption || "");
        cls = String(w.resourceClass || "");
        var g = w.frameGeometry;
        if (g) { x = g.x; y = g.y; wd = g.width; ht = g.height; }
    }
    callDBus("org.homebound", "/Focus", "org.homebound.Focus", "Activated",
             cap, cls, Math.round(x), Math.round(y), Math.round(wd), Math.round(ht));
}

var current = null;
function track(w) {
    if (current && current.captionChanged) {
        try { current.captionChanged.disconnect(onChange); } catch (e) {}
        try { current.frameGeometryChanged.disconnect(onChange); } catch (e) {}
    }
    current = w;
    if (w) {
        w.captionChanged.connect(onChange);
        w.frameGeometryChanged.connect(onChange);
    }
    report(w);
}
function onChange() { report(current); }

workspace.windowActivated.connect(track);
track(workspace.activeWindow);
