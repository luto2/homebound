// Homebound focus script: reports the active window caption + geometry to Homebound over D-Bus,
// and the captions of all windows whenever one opens, closes or is renamed (to see which EVE clients are open).
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

function reportWindows() {
    var captions = [];
    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        if (windows[i].normalWindow) {
            captions.push(String(windows[i].caption || ""));
        }
    }
    callDBus("org.homebound", "/Focus", "org.homebound.Focus", "Windows", captions);
}
function watchWindow(w) {
    if (w && w.captionChanged) {
        w.captionChanged.connect(reportWindows);
    }
}

workspace.windowActivated.connect(track);
workspace.windowAdded.connect(function (w) { watchWindow(w); reportWindows(); });
workspace.windowRemoved.connect(reportWindows);
var existing = workspace.windowList();
for (var j = 0; j < existing.length; j++) {
    watchWindow(existing[j]);
}
track(workspace.activeWindow);
reportWindows();
