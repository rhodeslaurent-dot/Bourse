/* Activity overlay for form submissions: the page shows that the system is working, with the
   elapsed time and the usual duration (data-busy / data-busy-eta). No fake percentage: the server
   does not report progress. Forms whose submit is handled in JavaScript (preventDefault) are skipped. */
(function () {
  "use strict";
  var box = document.getElementById("busy");
  if (!box) return;
  var msg = document.getElementById("busy-msg");
  var eta = document.getElementById("busy-eta");
  var time = document.getElementById("busy-time");
  var tick = null;
  var delay = null;

  function show(text, etaText) {
    msg.textContent = text;
    eta.textContent = etaText ? "Durée habituelle : " + etaText : "";
    box.classList.remove("hidden");
    box.classList.add("flex");
    var t0 = Date.now();
    time.textContent = "0 s";
    tick = setInterval(function () { time.textContent = Math.round((Date.now() - t0) / 1000) + " s"; }, 1000);
  }

  function hide() {
    clearTimeout(delay);
    clearInterval(tick);
    box.classList.add("hidden");
    box.classList.remove("flex");
    document.querySelectorAll("[data-busy-disabled]").forEach(function (b) {
      b.disabled = false;
      b.removeAttribute("data-busy-disabled");
    });
  }

  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!(f instanceof HTMLFormElement) || e.defaultPrevented) return;
    if ((f.getAttribute("method") || "get").toLowerCase() !== "post" || f.dataset.busy === "off") return;
    // disable after the browser has captured the submitter's name/value
    setTimeout(function () {
      f.querySelectorAll("button").forEach(function (b) {
        if (!b.disabled) { b.disabled = true; b.setAttribute("data-busy-disabled", ""); }
      });
    }, 0);
    if (f.dataset.busy) show(f.dataset.busy, f.dataset.busyEta || "");
    else delay = setTimeout(function () { show("Enregistrement…", ""); }, 400); // quick saves: no flash
  });

  window.addEventListener("pageshow", hide); // back button (page restored from cache)
  window.BoursePilotBusy = { show: show, hide: hide };
})();
