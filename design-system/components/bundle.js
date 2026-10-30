/* Homebound — preview helpers. Returns HTML strings that mirror what the Qt app paints.
   The SVG paths in FX and CHAR are the same geometry to build QPainterPaths from (16×16 / 14×14 grid). */
window.Homebound = (function () {
  var FX = {
    pulsar: '<circle cx="8" cy="8" r="2.2" fill="currentColor"/><path d="M8 1v3.2M8 11.8V15M1 8h3.2M11.8 8H15M3.1 3.1l2.2 2.2M10.7 10.7l2.2 2.2M12.9 3.1l-2.2 2.2M5.3 10.7l-2.2 2.2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    blackhole: '<circle cx="8" cy="8" r="3.3" fill="currentColor"/><ellipse cx="8" cy="8" rx="7" ry="2.6" fill="none" stroke="currentColor" stroke-width="1.4" transform="rotate(-20 8 8)"/>',
    cataclysmic: '<circle cx="5.5" cy="9.5" r="3.6" fill="currentColor"/><circle cx="12.2" cy="4.6" r="2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8.6 7.4c1-.6 1.6-1.2 2.2-1.9" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" fill="none"/>',
    magnetar: '<circle cx="8" cy="8" r="1.9" fill="currentColor"/><path d="M8 8C3 1.5 0.5 8 8 8S13 14.5 8 8M8 8C13 1.5 15.5 8 8 8S3 14.5 8 8" fill="none" stroke="currentColor" stroke-width="1.3"/>',
    redgiant: '<circle cx="8" cy="8" r="6.3" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M1.7 8a6.3 6.3 0 0 0 12.6 0z" fill="currentColor"/>',
    wolfrayet: '<path d="M8 1.6l5.5 3.2v6.4L8 14.4l-5.5-3.2V4.8z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M8 4.6v6.8M5 6.3l6 3.4M11 6.3l-6 3.4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    none: '<circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-dasharray="2 2.2"/>'
  };
  var FXNAME = { pulsar: 'Pulsar', blackhole: 'Black Hole', cataclysmic: 'Cataclysmic Variable', magnetar: 'Magnetar', redgiant: 'Red Giant', wolfrayet: 'Wolf-Rayet', none: 'No effect' };
  var CHAR = [
    '<circle cx="7" cy="7" r="5.6"/>',
    '<rect x="1.6" y="1.6" width="10.8" height="10.8" rx="1"/>',
    '<path d="M7 1.2l6 11H1z"/>',
    '<path d="M7 .8L13.2 7 7 13.2.8 7z"/>',
    '<path d="M7 1l5.2 3v6L7 13 1.8 10V4z"/>',
    '<path d="M7 .8l1.9 4 4.3.4-3.3 2.9 1 4.3L7 10.1 3.1 12.4l1-4.3L.8 5.2l4.3-.4z"/>'
  ];
  var CLS = {
    C1: ['class-c1', 'C1'], C2: ['class-c2', 'C2'], C3: ['class-c3', 'C3'], C4: ['class-c4', 'C4'], C5: ['class-c5', 'C5'], C6: ['class-c6', 'C6'],
    TH: ['class-thera', 'TH'], C13: ['class-c13', 'C13', 'shattered'], DR: ['class-drifter', 'DR'], PV: ['class-pochven', 'PV', 'pochven'],
    HS: ['class-hs', 'HS', 'k'], LS: ['class-ls', 'LS', 'k'], NS: ['class-ns', 'NS', 'k']
  };
  function fx(e, size) {
    e = e || 'none'; var s = size || 14;
    return '<svg class="lm-fx" width="' + s + '" height="' + s + '" viewBox="0 0 16 16" style="color:var(--effect-' + e + ')" role="img" aria-label="' + FXNAME[e] + '"><title>' + FXNAME[e] + '</title>' + FX[e] + '</svg>';
  }
  function cls(c, sec) {
    var d = CLS[c]; if (!d) return '';
    if (d[2] === 'k') return '<span class="lm-badge k" style="color:var(--' + d[0] + ')">' + d[1] + (sec != null ? ' <b>' + sec + '</b>' : '') + '</span>';
    return '<span class="lm-badge ' + (d[2] || '') + '" style="background:var(--' + d[0] + ')">' + d[1] + '</span>';
  }
  function statik(c) { var d = CLS[c]; return '<span class="lm-static" style="color:var(--' + d[0] + ')">' + d[1] + '</span>'; }
  function owner(t, n, home) { return '<span class="lm-owner' + (home ? ' home' : '') + '" style="background:var(--owner-' + n + ')">' + t + '</span>'; }
  function chr(n) { return '<svg class="lm-char" viewBox="0 0 14 14" fill="var(--char-' + n + ')" stroke="var(--ink-inverse)" stroke-width="1" aria-label="character ' + n + '">' + CHAR[n - 1] + '</svg>'; }
  function zk(level, recent) { return '<span class="lm-zk ' + (level || 'none') + '" aria-label="zKill ' + (level || 'none') + '"><i></i><i></i><i></i></span>' + (recent ? '<span class="lm-zk-recent" title="kill < 10 min"></span>' : ''); }
  /* o: {name, sub, j, cls, sec, fx, statics:[], owner:[ticker,n,home], chars:[n], zk, recent, state:'is-selected …', x, y} */
  function node(o) {
    var pos = o.x != null ? ' style="position:absolute;left:' + o.x + 'px;top:' + o.y + 'px"' : '';
    var k = CLS[o.cls] && CLS[o.cls][2] === 'k';
    var h = '<div class="lm-node ' + (k ? 'k ' : '') + (o.state || '') + '"' + pos + '>';
    if (o.chars && o.chars.length) h += '<div class="chars">' + o.chars.map(chr).join('') + '</div>';
    h += '<div class="l1"><span class="nm">' + o.name + (o.sub ? ' <small>' + o.sub + '</small>' : '') + '</span><span class="end">' + zk(o.zk, o.recent) + '</span></div>';
    h += '<div class="l2">' + cls(o.cls, o.sec) + (o.fx && o.fx !== 'none' ? fx(o.fx) : '') + '<span class="jn">' + (o.j || '') + '</span><span class="end">' + (o.statics || []).map(statik).join('') + '</span></div>';
    if (o.owner || o.stale) h += '<div class="l3">' + (o.owner ? owner(o.owner[0], o.owner[1], o.owner[2]) : '') + (o.stale ? '<span class="lm-faint stale-tag" style="font-size:10px;margin-left:auto">stale ' + o.stale + '</span>' : '') + '</div>';
    return h + '</div>';
  }
  /* label plate for an edge: {type, mass:'fresh|reduced|critical|unknown', eol, frig} */
  function elabel(e) {
    var c = '';
    if (e.mass === 'reduced') c += '<span class="chip" style="background:var(--edge-reduced)">R</span>';
    if (e.mass === 'critical') c += '<span class="chip" style="background:var(--edge-critical)">CRIT</span>';
    if (e.mass === 'unknown') c += '<span class="chip" style="background:var(--edge-unknown)">?</span>';
    if (e.frig) c += '<span class="chip" style="background:var(--edge-frig)">F</span>';
    if (e.eol) c += '<span class="chip" style="background:var(--edge-eol)">◷ EOL</span>';
    return '<span class="lm-elabel">' + e.type + c + '</span>';
  }
  var EDGE = {
    fresh: { c: 'var(--edge-fresh)', w: 2, d: '' }, reduced: { c: 'var(--edge-reduced)', w: 2.5, d: '' },
    critical: { c: 'var(--edge-critical)', w: 3.5, d: '8 4' }, unknown: { c: 'var(--edge-unknown)', w: 2, d: '3 5' }
  };
  /* SVG segments for one connection between two points */
  function edgeSvg(x1, y1, x2, y2, e) {
    var s = '', m = EDGE[e.mass || 'fresh'];
    if (e.eol) s += '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="var(--edge-eol)" stroke-width="6" stroke-linecap="round" stroke-dasharray="0.1 9" />';
    var c = e.frig ? 'var(--edge-frig)' : m.c, w = e.frig ? 1.5 : m.w;
    s += '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="' + c + '" stroke-width="' + w + '"' + (m.d ? ' stroke-dasharray="' + m.d + '"' : '') + ' />';
    if (e.frig) { var mx = (x1 + x2) / 2, my = (y1 + y2) / 2; s += '<path d="M' + mx + ' ' + (my - 5) + 'l5 5-5 5-5-5z" fill="var(--edge-frig)" />'; }
    return s;
  }
  /* overlay row: {tree, name, cls, sec, j, fx, k1, k24, owner, cur, dist, recent} */
  function ovRow(r) {
    var h = '<div class="r' + (r.cur ? ' cur' : '') + (r.dist ? ' k' : '') + '"><span class="cr">' + (r.cur ? '▶' : '') + '</span>';
    if (r.tree) h += '<span class="tree">' + r.tree + '</span>';
    h += '<span class="nm">' + r.name + '</span>' + cls(r.cls, r.sec);
    if (r.dist) h += '<span class="dist">' + r.dist + '</span>';
    else h += '<span class="jn">' + (r.j || '') + '</span>' + (r.fx && r.fx !== 'none' ? fx(r.fx, 13) : '');
    h += '<span class="kills">' + (r.recent ? '<span class="lm-zk-recent"></span>' : '') + (r.k1 != null ? r.k1 + '/' + r.k24 : '') + '</span>';
    h += r.owner ? '<span class="own">' + owner(r.owner[0], r.owner[1], r.owner[2]) + '</span>' : '';
    return h + '</div>';
  }
  return { fx: fx, FX: FX, FXNAME: FXNAME, cls: cls, statik: statik, owner: owner, chr: chr, zk: zk, node: node, elabel: elabel, edgeSvg: edgeSvg, ovRow: ovRow };
})();
