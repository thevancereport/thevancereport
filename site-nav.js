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

  var ITEMS = [
    { key: "index",         href: "index.html",         label: "Today\u2019s screen" },
    { key: "how",           href: "vance-value-screener.html", label: "How it works" },
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
