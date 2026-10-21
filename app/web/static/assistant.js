/* Floating assistant panel (docs/18 §18.3). Progressive enhancement: without JavaScript the
   form posts to /assistant. No external dependency. */
(function () {
  "use strict";
  var panel = document.getElementById("ai-panel");
  var form = document.getElementById("ai-form");
  if (!panel || !form) return;
  var thread = document.getElementById("ai-thread");
  var isinInput = form.querySelector("[name=isin]");
  var chip = document.getElementById("ai-chip");
  var chipName = document.getElementById("ai-chip-name");
  var button = form.querySelector("button[type=submit]");
  var loadedFor = null;

  function key() { return form.page.value + "|" + isinInput.value; }
  function scrollDown() { thread.scrollTop = thread.scrollHeight; }
  function setIsin(isin, name) {
    isinInput.value = isin || "";
    chip.classList.toggle("hidden", !isin);
    chipName.textContent = name || isin || "";
  }
  function load(force) {
    if (!force && loadedFor === key()) return;
    loadedFor = key();
    thread.innerHTML = '<p class="text-slate-500">Chargement…</p>';
    fetch("/assistant/panel?" + new URLSearchParams({ page: form.page.value, isin: isinInput.value }), {
      credentials: "same-origin",
    })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
      .then(function (html) { thread.innerHTML = html; scrollDown(); })
      .catch(function () { thread.innerHTML = '<p class="text-rose-600">Assistant indisponible.</p>'; });
  }

  panel.addEventListener("toggle", function () {
    if (panel.open) { load(false); form.question.focus(); }
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!form.question.value.trim()) return;
    var body = new URLSearchParams(new FormData(form));
    button.disabled = true;
    button.textContent = "…";
    var wait = document.createElement("div");
    wait.className = "rounded-xl bg-slate-50 dark:bg-slate-800/60 p-3 text-slate-600 dark:text-slate-300";
    wait.innerHTML =
      '<div class="flex items-center gap-2"><svg class="h-4 w-4 shrink-0 animate-spin text-sky-600" viewBox="0 0 24 24" fill="none">' +
      '<circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity=".25"/>' +
      '<path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>' +
      "<span>L’assistant rassemble les données et rédige sa réponse…</span></div>" +
      '<div class="mt-2 h-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"><div class="busy-bar h-full w-2/5 rounded-full bg-sky-600"></div></div>' +
      '<div class="mt-1 text-[11px] text-slate-500 dark:text-slate-400">habituellement 5 à 30 s · écoulé <b class="tabular">0 s</b></div>';
    thread.appendChild(wait);
    var t0 = Date.now();
    var elapsed = wait.querySelector("b");
    var tick = setInterval(function () { elapsed.textContent = Math.round((Date.now() - t0) / 1000) + " s"; }, 1000);
    scrollDown();
    fetch("/assistant/panel", { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) {
        if (r.ok) return r.text();
        return r.json().then(function (j) { return Promise.reject(j.detail || r.status); }, function () { return Promise.reject(r.status); });
      })
      .then(function (html) { thread.innerHTML = html; form.question.value = ""; loadedFor = key(); })
      .catch(function (why) {
        var p = document.createElement("p");
        p.className = "rounded-xl bg-rose-50 dark:bg-rose-500/10 p-2 text-rose-700 dark:text-rose-300";
        p.textContent = "Question non envoyée : " + why;
        thread.appendChild(p);
      })
      .finally(function () {
        clearInterval(tick);
        if (wait.parentNode) wait.parentNode.removeChild(wait);
        button.disabled = false;
        button.textContent = "Demander";
        scrollDown();
      });
  });

  document.addEventListener("click", function (e) {
    var ask = e.target.closest("[data-ai-isin]");
    if (ask) {
      e.preventDefault();
      setIsin(ask.getAttribute("data-ai-isin"), ask.getAttribute("data-ai-name"));
      panel.open = true;
      load(true);
      return;
    }
    var sug = e.target.closest("[data-ai-suggest]");
    if (sug) {
      e.preventDefault();
      form.question.value = sug.getAttribute("data-ai-suggest");
      form.question.focus();
    }
  });

  var clear = document.getElementById("ai-chip-clear");
  if (clear) clear.addEventListener("click", function (e) { e.preventDefault(); setIsin("", ""); load(true); });
})();
