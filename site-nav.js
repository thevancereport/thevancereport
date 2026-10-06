/* The Vance Report — site navigation.
 *
 * One menu, defined once, on every page. Each page loads this file as the
 * first thing inside <body>, and it writes the bar in place, so the menu is
 * there before the page paints rather than popping in afterwards.
 *
 * To add a page (a second screener, say) add one line to ITEMS. Nothing
 * else on the site has to change.
 *
 * A page that belongs to a section without being its index page (a field
 * note, for instance) says so on its script tag:
 *     <script src="site-nav.js" data-section="blog">   (then the closing tag)
 * Otherwise the current item is worked out from the file name.
 */
(function () {
  "use strict";

  // Bigger small print (30 Sep 2026). The labels, captions and table text
  // on every page were set at 10-14px while the pages had room to spare.
  // Anything under 15px is drawn 50% larger, up to 18px (just above the
  // 17.5px body text), so headlines and body text keep their sizes and the
  // hierarchy stays. Done here, once, because every page loads this file.
  // The menu bar itself is left alone: at 50% larger it no longer fits on
  // one line.
  var SMALL_PX = 15, GROW = 1.5, CAP_PX = 18;
  var already = window.__vrGrown;
  window.__vrGrown = true;
  var seen = typeof WeakSet === "function" ? new WeakSet() : null;

  function toPx(v) {
    var m = /^\s*([\d.]+)(px|rem)\s*$/.exec(String(v || ""));
    if (!m) return null;
    return m[2] === "rem" ? parseFloat(m[1]) * 16 : parseFloat(m[1]);
  }
  function bigger(px) { return Math.min(Math.round(px * GROW * 10) / 10, CAP_PX); }
  function growStyle(st) {
    var fs = st.fontSize;
    var pri = st.getPropertyPriority("font-size") || st.getPropertyPriority("font");
    // "font: 500 13px var(--sans)" leaves fontSize empty, because the browser
    // can't split a shorthand that uses var() until it draws the page. Read
    // the size out of the shorthand itself (5 Oct 2026).
    if (!fs) {
      var m = /(?:^|\s)([\d.]+)px(?:\s*\/|\s)/.exec(st.getPropertyValue("font") + " ");
      if (!m) return;
      fs = m[1] + "px";
    }
    var px = toPx(fs);
    if (px && px < SMALL_PX) { st.setProperty("font-size", bigger(px) + "px", pri); return; }
    // clamp(9px, 1.7cqw, 14px): raise the floor and the ceiling the same way.
    var c = /^clamp\(\s*([\d.]+)px\s*,(.*),\s*([\d.]+)px\s*\)$/.exec(fs);
    if (c && parseFloat(c[1]) < SMALL_PX) {
      var lo = bigger(parseFloat(c[1])), hi = Math.max(parseFloat(c[3]), lo);
      if (parseFloat(c[3]) < SMALL_PX) hi = Math.max(bigger(parseFloat(c[3])), lo);
      st.setProperty("font-size", "clamp(" + lo + "px," + c[2] + "," + hi + "px)", pri);
    }
  }
  function growRules(rules) {
    for (var i = 0; i < rules.length; i++) {
      var r = rules[i];
      if (r.style && (r.style.fontSize || r.style.getPropertyValue("font"))) growStyle(r.style);
      if (r.cssRules) growRules(r.cssRules);
    }
  }
  function growSheets() {
    var sheets = document.styleSheets;
    for (var i = 0; i < sheets.length; i++) {
      var s = sheets[i];
      if (seen && seen.has(s)) continue;
      var owner = s.ownerNode;
      if (owner && owner.id === "vr-nav-css") { if (seen) seen.add(s); continue; }
      var rules;
      try { rules = s.cssRules; } catch (e) { continue; }   // another site's stylesheet (fonts)
      if (!rules) continue;
      growRules(rules);
      if (seen) seen.add(s);
    }
  }
  function growInline(root) {
    if (!root || root.nodeType !== 1) return;
    var els = [root].concat(Array.prototype.slice.call(root.querySelectorAll('[style*="font"]')));
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.hasAttribute("data-vr-grown") || !/font(-size)?\s*:/.test(el.getAttribute("style") || "")) continue;
      if (el.closest && el.closest(".vr-nav")) continue;
      growStyle(el.style);
      el.setAttribute("data-vr-grown", "");
    }
  }
  // Article pages: use the width the page has. Tables and charts get the
  // wider column; running text stays at a comfortable reading width.
  var roomy = document.createElement("style");
  roomy.id = "vr-roomy-css";
  roomy.textContent =
    "@media (min-width:1100px){" +
      ".article-grid{grid-template-columns:160px minmax(0,1000px)!important}" +
      ".article-grid>.prose>p,.article-grid>.prose>h2,.article-grid>.prose>h3," +
      ".article-grid>.prose>ul,.article-grid>.prose>ol,.article-grid>.prose>blockquote" +
      "{max-width:720px}" +
    "}";
  if (!already) (document.head || document.documentElement).appendChild(roomy);

  if (!already) {
  growSheets();
  document.addEventListener("DOMContentLoaded", function () {
    growSheets();
    growInline(document.body);
    if (typeof MutationObserver === "function") {
      new MutationObserver(function (muts) {
        for (var i = 0; i < muts.length; i++) {
          var added = muts[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            var n = added[j];
            if (n.nodeName === "STYLE" || n.nodeName === "LINK") growSheets();
            else growInline(n);
          }
        }
      }).observe(document.body, { childList: true, subtree: true });
    }
  });
  window.addEventListener("load", growSheets);
  }


  var ITEMS = [
    { key: "index",         href: "index.html",         label: "Today\u2019s screen" },
    { key: "how",           href: "vance-value-screener.html", label: "How it works" },
    { key: "scorecard",     href: "scorecard.html",            label: "Track record" },
    { key: "report",        href: "report.html",        label: "Inbox report" },
    { key: "stock",         href: "stock.html",         label: "Stock lookup" },
    { key: "blog",          href: "blog.html",          label: "Field notes" },
    { key: "arden-k-vance", href: "arden-k-vance.html", label: "Author" },
    { key: "reviews",       href: "reviews.html",       label: "Reviews" }
  ];

  var me = document.currentScript;
  if (!me || document.querySelector(".vr-nav")) return;

  var file = (location.pathname.split("/").pop() || "").replace(/\.html?$/i, "") || "index";
  var here = me.getAttribute("data-section") || file;

  // Colours are the site palette written out rather than read from each
  // page's CSS variables, because the pages do not all define the same
  // ones -- stock.html has its own set. The bar looks the same everywhere.
  var CSS =
    ".vr-nav{position:sticky;top:0;z-index:80;background:rgba(19,15,10,.95);" +
      "-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);" +
      "border-bottom:1px solid #372d23;font-family:'IBM Plex Mono',ui-monospace,monospace;" +
      "font-weight:400;line-height:1.2}" +
    ".vr-nav *{box-sizing:border-box}" +
    ".vr-nav-in{max-width:1180px;margin:0 auto;padding:0 28px;height:56px;" +
      "display:flex;align-items:center;gap:24px}" +
    ".vr-brand{display:flex;align-items:center;gap:11px;color:#e7e3d8;" +
      "text-decoration:none;font-size:15px;letter-spacing:.05em;white-space:nowrap}" +
    ".vr-glyph{border:1px solid #e0a33c;color:#e0a33c;font-size:12.5px;" +
      "padding:2px 6px;letter-spacing:.08em}" +
    ".vr-links{display:flex;gap:2px;margin:0 0 0 auto;padding:0;list-style:none}" +
    ".vr-links li{margin:0;padding:0}" +
    ".vr-links a{display:block;padding:9px 11px;color:#aea59d;text-decoration:none;" +
      "font-size:13px;letter-spacing:.12em;text-transform:uppercase;white-space:nowrap;" +
      "border-bottom:1px solid transparent}" +
    ".vr-links a:hover{color:#e7e3d8}" +
    ".vr-links a:focus-visible,.vr-brand:focus-visible,.vr-toggle:focus-visible" +
      "{outline:1px solid #e0a33c;outline-offset:2px}" +
    ".vr-links a[aria-current=page]{color:#e0a33c;border-bottom-color:#e0a33c}" +
    ".vr-toggle{display:none}" +
    "@media (max-width:1080px){" +
      ".vr-nav-in{padding:0 18px;height:52px}" +
      ".vr-toggle{display:inline-flex;align-items:center;margin-left:auto;" +
        "background:none;border:1px solid #372d23;color:#e7e3d8;cursor:pointer;" +
        "font:inherit;font-size:12.5px;letter-spacing:.16em;text-transform:uppercase;" +
        "padding:8px 12px}" +
      ".vr-nav.is-open .vr-toggle{border-color:#e0a33c;color:#e0a33c}" +
      ".vr-links{display:none;position:absolute;left:0;right:0;top:52px;" +
        "flex-direction:column;gap:0;margin:0;background:#130f0a;" +
        "border-bottom:1px solid #372d23;padding:6px 0 10px}" +
      ".vr-nav.is-open .vr-links{display:flex}" +
      ".vr-links a{padding:14px 18px;font-size:14px;border-bottom:0;" +
        "border-left:2px solid transparent}" +
      ".vr-links a[aria-current=page]{border-left-color:#e0a33c}" +
    "}";

  var style = document.createElement("style");
  style.id = "vr-nav-css";   // the menu keeps its own sizes
  style.textContent = CSS;

  var links = ITEMS.map(function (it) {
    var cur = it.key === here ? ' aria-current="page"' : "";
    return '<li><a href="' + it.href + '"' + cur + ">" + it.label + "</a></li>";
  }).join("");

  var nav = document.createElement("nav");
  nav.className = "vr-nav";
  nav.setAttribute("aria-label", "Site");
  nav.innerHTML =
    '<div class="vr-nav-in">' +
      '<a class="vr-brand" href="index.html"><span class="vr-glyph">VR</span>' +
        '<span class="vr-name">The Vance Report</span></a>' +
      '<button class="vr-toggle" type="button" aria-expanded="false" ' +
        'aria-controls="vr-links">Menu</button>' +
      '<ul class="vr-links" id="vr-links">' + links + "</ul>" +
    "</div>";

  me.parentNode.insertBefore(style, me);
  me.parentNode.insertBefore(nav, me);

  // Run edge to edge whatever the page does with its own padding. Most
  // pages have none; stock.html pads the whole body by 24px, which left the
  // bar inset and floating 24px below the top. Pull the bar out by exactly
  // the body's padding and give the same space back underneath it, so the
  // page below sits where it always did.
  var bs = window.getComputedStyle(document.body);
  var pt = parseFloat(bs.paddingTop) || 0;
  var pl = parseFloat(bs.paddingLeft) || 0;
  var pr = parseFloat(bs.paddingRight) || 0;
  if (pt || pl || pr) {
    nav.style.margin = -pt + "px " + -pr + "px " + pt + "px " + -pl + "px";
  }

  var btn = nav.querySelector(".vr-toggle");
  function setOpen(open) {
    nav.classList.toggle("is-open", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    btn.textContent = open ? "Close" : "Menu";
  }
  btn.addEventListener("click", function () {
    setOpen(!nav.classList.contains("is-open"));
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && nav.classList.contains("is-open")) {
      setOpen(false);
      btn.focus();
    }
  });
  document.addEventListener("click", function (e) {
    if (nav.classList.contains("is-open") && !nav.contains(e.target)) setOpen(false);
  });
})();
