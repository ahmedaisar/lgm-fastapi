"use strict";
/* LGM CMS admin SPA. Page is public; every data call sends the Bearer token. */
(function () {
  const $ = (id) => document.getElementById(id);
  const TOKEN_KEY = "lgm_admin_token";
  let hotels = [];

  const token = () => sessionStorage.getItem(TOKEN_KEY) || "";

  async function api(path, { method = "GET", body, rawBody, contentType } = {}) {
    const headers = { Authorization: `Bearer ${token()}` };
    let payload = body;
    if (body !== undefined && !rawBody) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    if (contentType) headers["Content-Type"] = contentType;
    const res = await fetch(path, { method, headers, body: payload });
    if (res.status === 401 || res.status === 503) {
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) logout();
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    return res;
  }

  function show(view) {
    $("login-view").classList.toggle("hidden", view !== "login");
    $("main-view").classList.toggle("hidden", view !== "main");
  }

  function flash(msg, ok) {
    const el = $("status");
    el.textContent = msg;
    el.classList.remove("hidden", "text-red-700", "border-red-300", "bg-red-50", "text-teal-700", "border-teal-300", "bg-teal-50");
    el.classList.add(...(ok
      ? ["text-teal-700", "border-teal-300", "bg-teal-50"]
      : ["text-red-700", "border-red-300", "bg-red-50"]));
    clearTimeout(flash.t);
    flash.t = setTimeout(() => el.classList.add("hidden"), 5000);
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ---- auth ----
  async function doLogin() {
    const err = $("login-error");
    err.classList.add("hidden");
    try {
      const res = await fetch("/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user: $("login-user").value, password: $("login-pass").value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      sessionStorage.setItem(TOKEN_KEY, data.token);
      $("login-pass").value = "";
      await refresh();
      show("main");
    } catch (e) {
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
  }

  function logout() {
    sessionStorage.removeItem(TOKEN_KEY);
    show("login");
  }

  // ---- list ----
  async function refresh() {
    try {
      hotels = await (await api("/admin/hotels")).json();
      render();
    } catch (e) {
      flash(e.message, false);
    }
  }

  function filtered() {
    const q = $("search").value.trim().toLowerCase();
    if (!q) return hotels;
    return hotels.filter((h) =>
      [h.id, h.name, h.atoll, h.portal_hotel_id, h.portal_slug]
        .map((v) => String(v ?? "").toLowerCase())
        .some((v) => v.includes(q))
    );
  }

  function render() {
    const rows = filtered();
    $("hotel-count").textContent = `(${hotels.length})`;
    $("empty-note").classList.toggle("hidden", rows.length > 0);
    $("hotel-rows").innerHTML = rows.map((h) => {
      const thumb = (h.photos && h.photos[0]) || "";
      const link = h.portal_hotel_id
        ? `<span class="inline-block bg-teal-50 text-teal-700 border border-teal-200 rounded px-2 py-0.5 text-xs font-semibold">id ${esc(h.portal_hotel_id)}</span>`
        : `<span class="inline-block bg-gray-100 text-gray-500 border border-gray-200 rounded px-2 py-0.5 text-xs">unmapped</span>`;
      return `<tr class="border-t border-gray-100">
        <td class="px-3 py-2">
          <div class="flex items-center gap-2">
            ${thumb ? `<img class="thumb" src="${esc(thumb)}" loading="lazy" onerror="this.remove()" />` : ""}
            <div><div class="font-semibold">${esc(h.name)}</div>
            <div class="text-xs text-gray-500">${esc(h.id)}</div></div>
          </div>
        </td>
        <td class="px-3 py-2">${esc(h.atoll || "—")}</td>
        <td class="px-3 py-2">${h.stars ?? "—"}</td>
        <td class="px-3 py-2">${link}</td>
        <td class="px-3 py-2 text-gray-500">${(h.photos || []).length} img · ${(h.amenities || []).length} am</td>
        <td class="px-3 py-2 text-right whitespace-nowrap">
          <button data-edit="${esc(h.id)}" class="btn bg-white border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50">Edit</button>
          <button data-del="${esc(h.id)}" class="btn bg-white border border-red-300 text-red-600 px-2 py-1 text-xs hover:bg-red-50">Delete</button>
        </td>
      </tr>`;
    }).join("");
    $("hotel-rows").querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => openEditor(b.getAttribute("data-edit"))));
    $("hotel-rows").querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => removeHotel(b.getAttribute("data-del"))));
  }

  // ---- editor ----
  function openEditor(id) {
    const h = id ? hotels.find((x) => x.id === id) : null;
    $("editor-title").textContent = h ? `Edit — ${h.name}` : "Add hotel";
    $("editor-error").classList.add("hidden");
    $("f-id").value = h ? h.id : "";
    $("f-name").value = h ? h.name : "";
    $("f-description").value = h ? h.description || "" : "";
    $("f-atoll").value = h ? h.atoll || "" : "";
    $("f-stars").value = h && h.stars !== null && h.stars !== undefined ? h.stars : "";
    $("f-website").value = h ? h.website || "" : "";
    $("f-portal-id").value = h ? h.portal_hotel_id || "" : "";
    $("f-portal-slug").value = h ? h.portal_slug || "" : "";
    $("f-photos").value = h ? (h.photos || []).join("\n") : "";
    $("f-amenities").value = h ? (h.amenities || []).join(", ") : "";
    $("f-check-in-from").value = h ? h.check_in_from || "" : "";
    $("f-check-in-notes").value = h ? h.check_in_notes || "" : "";
    $("f-check-out-until").value = h ? h.check_out_until || "" : "";
    $("f-cancellation").value = h ? h.cancellation_prepayment || "" : "";
    $("f-child-policies").value = h ? h.child_policies || "" : "";
    $("f-bed-policies").value = h ? h.cot_extra_bed_policies || "" : "";
    $("f-age-restriction").value = h ? h.age_restriction || "" : "";
    $("f-payment-methods").value = h ? (h.payment_methods || []).join(", ") : "";
    $("f-pets-allowed").value = !h || h.pets_allowed === null || h.pets_allowed === undefined
      ? "" : String(h.pets_allowed);
    $("f-pets-notes").value = h ? h.pets_notes || "" : "";
    $("f-fine-print").value = h ? h.fine_print || "" : "";
    $("editor-backdrop").classList.remove("hidden");
  }

  function closeEditor() {
    $("editor-backdrop").classList.add("hidden");
  }

  function splitLines(v) {
    return String(v || "").split("\n").map((s) => s.trim()).filter(Boolean);
  }

  async function saveEditor() {
    const err = $("editor-error");
    err.classList.add("hidden");
    const starsRaw = $("f-stars").value.trim();
    const petsRaw = $("f-pets-allowed").value;
    const payload = {
      name: $("f-name").value.trim(),
      description: $("f-description").value,
      atoll: $("f-atoll").value.trim(),
      stars: starsRaw === "" ? null : Number(starsRaw),
      website: $("f-website").value.trim(),
      portal_hotel_id: $("f-portal-id").value.trim() || null,
      portal_slug: $("f-portal-slug").value.trim() || null,
      photos: splitLines($("f-photos").value),
      amenities: String($("f-amenities").value).split(/[,\n|]/).map((s) => s.trim()).filter(Boolean),
      check_in_from: $("f-check-in-from").value.trim(),
      check_in_notes: $("f-check-in-notes").value,
      check_out_until: $("f-check-out-until").value.trim(),
      cancellation_prepayment: $("f-cancellation").value,
      child_policies: $("f-child-policies").value,
      cot_extra_bed_policies: $("f-bed-policies").value,
      age_restriction: $("f-age-restriction").value.trim(),
      pets_allowed: petsRaw === "" ? null : petsRaw === "true",
      pets_notes: $("f-pets-notes").value.trim(),
      payment_methods: String($("f-payment-methods").value).split(/[,\n|]/).map((s) => s.trim()).filter(Boolean),
      fine_print: $("f-fine-print").value,
    };
    const id = $("f-id").value;
    try {
      if (id) await api(`/admin/hotels/${encodeURIComponent(id)}`, { method: "PUT", body: payload });
      else await api("/admin/hotels", { method: "POST", body: payload });
      closeEditor();
      await refresh();
      flash(id ? "Hotel updated." : "Hotel created.", true);
    } catch (e) {
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
  }

  async function removeHotel(id) {
    const h = hotels.find((x) => x.id === id);
    if (!window.confirm(`Delete "${h ? h.name : id}"?`)) return;
    try {
      await api(`/admin/hotels/${encodeURIComponent(id)}`, { method: "DELETE" });
      await refresh();
      flash("Hotel deleted.", true);
    } catch (e) {
      flash(e.message, false);
    }
  }

  // ---- import / export ----
  async function doImport(file) {
    const box = $("import-result");
    box.classList.remove("hidden");
    box.innerHTML = "<p>Importing…</p>";
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/admin/import", {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const errs = (data.errors || []).map((e) => `<li>line ${e.line}: ${esc(e.error)}</li>`).join("");
      box.innerHTML =
        `<p><strong>Import done:</strong> ${data.created} created, ${data.updated} updated.</p>` +
        (errs ? `<ul class="list-disc ml-5 mt-2 text-red-600">${errs}</ul>` : "");
      await refresh();
    } catch (e) {
      box.innerHTML = `<p class="text-red-600">Import failed: ${esc(e.message)}</p>`;
    }
  }

  async function doExport() {
    try {
      const res = await fetch("/admin/export", {
        headers: { Authorization: `Bearer ${token()}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "cms-export.csv";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      flash(e.message, false);
    }
  }

  // ---- wire up ----
  $("login-btn").addEventListener("click", doLogin);
  $("login-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
  $("logout-btn").addEventListener("click", logout);
  $("add-btn").addEventListener("click", () => openEditor(null));
  $("search").addEventListener("input", render);
  $("editor-cancel").addEventListener("click", closeEditor);
  $("editor-save").addEventListener("click", saveEditor);
  $("editor-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "editor-backdrop") closeEditor();
  });
  $("import-btn").addEventListener("click", () => $("import-file").click());
  $("import-file").addEventListener("change", (e) => {
    if (e.target.files[0]) doImport(e.target.files[0]);
    e.target.value = "";
  });
  $("export-btn").addEventListener("click", doExport);

  show(token() ? "main" : "login");
  if (token()) refresh();
})();
