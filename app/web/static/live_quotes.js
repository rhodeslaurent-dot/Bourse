/* Live quotes (user decision of 28/10/2026): fills the elements carrying data-live-isin from /api/cours,
   the distances to the limit (data-live-distance, computed by the server) and the latest re-evaluation
   (data-live-reeval). Polls only while the page is visible and the server gives next_poll_s (a venue
   in session); « Rafraîchir les cours » asks a manual refresh (EODHD budget and minimal interval kept by the
   server). No dependency. A price never feeds a proposal from here: display only. */
(function () {
  "use strict";
  if (!document.querySelector("[data-live-isin]")) return;

  var BADGE = "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap ";
  var BADGE_TONES = {
    green: "bg-emerald-50 text-emerald-800 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/30",
    amber: "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/30",
    red: "bg-rose-50 text-rose-800 ring-rose-200 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-500/30",
    blue: "bg-sky-50 text-sky-800 ring-sky-200 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-500/30",
    slate: "bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700"
  };
  var PANEL = "mt-1 rounded-lg px-2 py-1 text-xs ";
  var PANEL_TONES = {
    green: "bg-emerald-50 text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-200",
    amber: "bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-200",
    red: "bg-rose-50 text-rose-800 dark:bg-rose-500/10 dark:text-rose-200",
    slate: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300"
  };
  var UP = "text-emerald-700 dark:text-emerald-300";
  var DOWN = "text-rose-700 dark:text-rose-300";

  var timer = null;
  var nextPoll = null;
  var lastFetch = 0;
  var busy = false;

  function isins() {
    var seen = {};
    var out = [];
    document.querySelectorAll("[data-live-isin]").forEach(function (n) {
      var i = n.getAttribute("data-live-isin");
      if (i && !seen[i]) { seen[i] = true; out.push(i); }
    });
    return out;
  }

  function field(root, name) { return root.querySelector("[data-live-field=" + name + "]"); }

  function badge(text, tone, title) {
    var s = document.createElement("span");
    s.className = BADGE + (BADGE_TONES[tone] || BADGE_TONES.slate);
    s.textContent = text;
    if (title) s.title = title;
    return s;
  }

  function applyQuote(root, q) {
    var price = field(root, "price");
    var variation = field(root, "variation");
    var b = field(root, "badge");
    var time = field(root, "time");
    if (price) {
      price.textContent = q.cours_txt + (q.cours !== null && q.devise && q.devise !== "EUR" ? " " + q.devise : "");
      price.title = q.source ? "source : " + q.source + (q.recu_a ? " · reçu " + q.recu_a.slice(11, 19) + " UTC" : "") : "";
    }
    if (variation) {
      variation.textContent = q.variation_txt || "";
      if (variation.getAttribute("data-base") === null) variation.setAttribute("data-base", variation.className);
      variation.className = variation.getAttribute("data-base") + (q.variation !== null ? " " + (q.variation >= 0 ? UP : DOWN) : "");
      variation.title = "variation depuis la dernière clôture";
    }
    if (b) {
      b.textContent = "";
      b.appendChild(badge(q.statut_txt, q.ton, q.note || ""));
      if (!q.vraisemblable && q.cours !== null) b.appendChild(badge("à vérifier", "amber", q.note || ""));
    }
    if (time) {
      time.textContent = q.heure ? q.heure : "";
      time.title = q.heure_txt ? "heure de marché : " + q.heure_txt : "";
    }
  }

  function apply(data) {
    var quotes = data.cours || {};
    document.querySelectorAll("[data-live-isin]").forEach(function (n) {
      var q = quotes[n.getAttribute("data-live-isin")];
      if (q) applyQuote(n, q);
    });
    var gaps = data.ecarts || {};
    document.querySelectorAll("[data-live-distance]").forEach(function (n) {
      var g = gaps[n.getAttribute("data-live-distance")];
      if (g) n.textContent = g.ecart_txt;
    });
    var toSeuil = data.ecarts_seuil || {};
    document.querySelectorAll("[data-live-distance-seuil]").forEach(function (n) {
      var g = toSeuil[n.getAttribute("data-live-distance-seuil")];
      if (g) n.textContent = g.ecart_txt;
    });
    var re = data.reevaluations || {};
    document.querySelectorAll("[data-live-reeval]").forEach(function (n) {
      var r = re[n.getAttribute("data-live-reeval")];
      if (!r) return;
      n.textContent = r.texte;
      n.className = PANEL + (PANEL_TONES[r.ton] || PANEL_TONES.slate);
    });
  }

  function say(text, warn) {
    document.querySelectorAll("[data-live-msg]").forEach(function (m) {
      m.textContent = text || "";
      m.classList.toggle("text-amber-700", !!warn);
    });
  }

  function schedule() {
    clearTimeout(timer);
    if (nextPoll && document.visibilityState === "visible") {
      timer = setTimeout(function () { load(false); }, nextPoll * 1000);
    }
  }

  function load(force) {
    var list = isins();
    if (!list.length || busy) return;
    busy = true;
    var buttons = document.querySelectorAll("[data-live-refresh]");
    if (force) buttons.forEach(function (b) { b.disabled = true; });
    fetch("/api/cours?" + new URLSearchParams({ isins: list.join(","), force: force ? "1" : "0" }), {
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (data) {
        lastFetch = Date.now();
        apply(data);
        nextPoll = data.next_poll_s || null;
        var refused = data.manuel && data.manuel.demande && !data.manuel.accepte;
        say(data.message, refused || (data.eodhd && data.eodhd.epuise));
      })
      .catch(function () { say("cours indisponibles (serveur injoignable)", true); })
      .then(function () {
        busy = false;
        buttons.forEach(function (b) { b.disabled = false; });
        schedule();
      });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState !== "visible") { clearTimeout(timer); return; }
    if (nextPoll && Date.now() - lastFetch >= nextPoll * 1000) load(false);
    else schedule();
  });
  document.querySelectorAll("[data-live-refresh]").forEach(function (b) {
    b.addEventListener("click", function () { load(true); });
  });
  load(false);
})();
