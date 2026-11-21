"use strict";
const $ = (selector) => document.querySelector(selector);
const storeKey = "departure-current-report";
let session = null;
let state = null;
let busy = 0;
let polling = null;
let creating = null;
let saving = Promise.resolve();
const objectUrls = new Map();
const pendingFailures = new Set();
const previewStates = new Map();
let needsRefresh = false;

function notify(message) {
  const notice = $("#notice");
  if (notice) { notice.textContent = message; notice.hidden = !message; }
}
async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (session) headers.Authorization = `Bearer ${session.owner_token}`;
  if (options.json !== undefined) { headers["Content-Type"] = "application/json"; options.body = JSON.stringify(options.json); }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "The request could not be completed." }));
    const failure = new Error(typeof error.detail === "string" ? error.detail : "Please check your input and try again.");
    failure.status = response.status;
    throw failure;
  }
  return response.json();
}
function details() { return Object.fromEntries(["vehicle", "plate", "company", "location", "notes"].map(key => [key, $(`#${key}`).value])); }
async function ensureSession() {
  if (session) return;
  if (!creating) creating = api("/api/reports", { method: "POST", json: details() }).then(result => {
    session = result;
    localStorage.setItem(storeKey, JSON.stringify(session));
  }).finally(() => { creating = null; });
  await creating;
}
function saveDetails() {
  if (!session || state?.status !== "draft") return;
  const payload = details();
  saving = saving.catch(() => {}).then(() => api(`/api/reports/${session.id}`, { method: "PATCH", json: payload }));
  saving.catch(error => notify(`Could not save details: ${error.message}`));
}
function photoContainer(view) { return view === "extra" ? $("#extra-photos") : $(`[data-view="${view}"] .slot-photos`); }
function updateButton() {
  const photos = state?.photos || [];
  const count = photos.length;
  const pendingPreviews = photos.filter(photo => previewStates.get(photo.id) !== "ready");
  const previewFailed = pendingPreviews.some(photo => previewStates.get(photo.id) === "failed");
  $("#analyze").disabled = busy > 0 || !count || pendingFailures.size > 0 || pendingPreviews.length > 0 || needsRefresh;
  $("#photo-count").textContent = `${count} photo${count === 1 ? "" : "s"}`;
  let message = "Add a photo to get started.";
  if (busy) message = `Uploading ${busy} photo(s)...`;
  else if (pendingFailures.size) message = `${pendingFailures.size} photo(s) failed to upload. Choose Retry to upload again or Dismiss to continue without them.`;
  else if (needsRefresh) message = "Reload this page to restore your saved photos and review their previews.";
  else if (previewFailed) message = "Some saved photos could not be previewed. Retry their previews or remove those photos before continuing.";
  else if (pendingPreviews.length) message = `Loading ${pendingPreviews.length} photo preview(s). Review them before creating your report.`;
  else if (count) message = "Review your photo previews, then select Create my report.";
  $("#upload-status").textContent = message;
}
function renderPhotos() {
  for (const photo of state.photos) {
    if (document.getElementById(`photo-${photo.id}`)) continue;
    const tile = document.createElement("div"); tile.className = "thumbnail"; tile.id = `photo-${photo.id}`;
    const image = document.createElement("img"); image.alt = photo.filename; image.hidden = true;
    const previewNotice = document.createElement("div"); previewNotice.className = "upload-error";
    tile.append(image, previewNotice); photoContainer(photo.view).append(tile);
    loadPreview(photo.id, image, previewNotice);
    const remove = document.createElement("button"); remove.className = "remove-photo"; remove.type = "button"; remove.textContent = "×"; remove.setAttribute("aria-label", `Remove ${photo.filename}`);
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try {
        await api(`/api/reports/${session.id}/photos/${photo.id}`, { method: "DELETE" });
        tile.remove();
        if (objectUrls.has(photo.id)) URL.revokeObjectURL(objectUrls.get(photo.id));
        objectUrls.delete(photo.id);
        previewStates.delete(photo.id);
        await refresh();
      }
      catch (error) { notify(error.message); remove.disabled = false; }
    }); tile.append(remove);
  }
}
async function loadPreview(photoId, image, notice) {
  previewStates.set(photoId, "loading");
  notice.textContent = "Loading preview…";
  notice.hidden = false;
  updateButton();
  let url;
  try {
    const response = await fetch(`/api/reports/${session.id}/photos/${photoId}`, {
      headers: { Authorization: `Bearer ${session.owner_token}` },
      signal: AbortSignal.timeout(20000),
    });
    if (!response.ok) throw new Error("Preview unavailable");
    const blob = await response.blob();
    if (!image.isConnected) return;
    url = URL.createObjectURL(blob);
    image.src = url;
    await image.decode();
    if (!image.isConnected) { URL.revokeObjectURL(url); return; }
    objectUrls.set(photoId, url);
    image.hidden = false;
    notice.hidden = true;
    previewStates.set(photoId, "ready");
  } catch (_) {
    if (url) URL.revokeObjectURL(url);
    if (!image.isConnected) return;
    previewStates.set(photoId, "failed");
    notice.textContent = "Photo saved. Preview unavailable. ";
    const retry = document.createElement("button");
    retry.type = "button"; retry.className = "retry-upload"; retry.textContent = "Retry preview";
    retry.onclick = () => loadPreview(photoId, image, notice);
    notice.append(retry);
  } finally {
    updateButton();
  }
}
async function refresh() {
  if (!session) return;
  state = await api(`/api/reports/${session.id}`);
  needsRefresh = false;
  const draft = state.status === "draft", ready = state.status === "ready";
  $("#editor").hidden = !draft; $("#progress").hidden = draft || ready; $("#ready").hidden = !ready;
  if (draft) { updateButton(); renderPhotos(); }
  if (!draft && !ready) {
    const count = state.photos.filter(p => p.analyzed).length;
    $("#analysis-progress").max = state.photos.length || 1; $("#analysis-progress").value = count;
    $("#progress-title").textContent = state.status === "failed" ? "The review needs another try." : "Taking a closer look.";
    $("#progress-text").textContent = state.status === "failed" ? state.error : `${count} of ${state.photos.length} photos reviewed. ${state.status === "queued" ? "Waiting for the analysis worker. Temporary failures are retried automatically." : "We're checking the visible details and preparing your report."} You can return in this browser.`;
    $("#retry").hidden = state.status !== "failed";
  }
  if (ready) {
    const path = `/r/${session.share_token}`, url = new URL(path, location.origin).href;
    $("#view-report").href = path; $("#download-pdf").href = `${path}/download/pdf`; $("#download-photos").href = `${path}/download/photos`;
    $("#share-url").value = url;
    $("#expiry").textContent = `Available until ${new Date(state.expires_at * 1000).toUTCString()}.`;
    $("#email-form button").disabled = !state.email_enabled;
    const latest = state.emails[0];
    $("#email-status").textContent = !state.email_enabled ? "Email delivery is not configured on this server. You can still download or copy the link." : latest ? ({ done: "Your report was accepted by the email server. Check your inbox and spam folder.", failed: latest.error, queued: "Your email is queued for delivery.", running: "Sending your report..." }[latest.status] || "") : "Your address is used only to deliver this report.";
  }
  clearTimeout(polling);
  if (["queued", "processing"].includes(state.status) || state.emails.some(e => ["queued", "running"].includes(e.status))) polling = setTimeout(() => refresh().catch(e => { notify(e.message); polling = setTimeout(() => refresh().catch(e => notify(e.message)), 5000); }), 2500);
}
async function upload(file, view, failureTile = null) {
  busy++; updateButton();
  const tile = failureTile || document.createElement("div"); tile.className = "upload-pending"; tile.textContent = `Uploading ${file.name}...`;
  if (!failureTile) photoContainer(view).append(tile);
  try {
    if (file.size > 15 * 1024 * 1024) throw new Error("Photo exceeds 15 MiB.");
    await ensureSession();
    await api(`/api/reports/${session.id}/photos?view=${encodeURIComponent(view)}&filename=${encodeURIComponent(file.name)}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
    pendingFailures.delete(tile);
    tile.remove();
    await refresh().catch(error => {
      needsRefresh = true;
      notify(`Photo uploaded, but the report could not be refreshed. Reload this page to see it. ${error.message}`);
    });
  } catch (error) {
    tile.className = "upload-error"; tile.textContent = `${file.name}: Upload failed. ${error.message} `; pendingFailures.add(tile);
    const retry = document.createElement("button"); retry.className = "retry-upload"; retry.textContent = "Retry"; retry.onclick = () => upload(file, view, tile);
    const dismiss = document.createElement("button"); dismiss.className = "retry-upload"; dismiss.textContent = "Dismiss"; dismiss.onclick = () => { pendingFailures.delete(tile); tile.remove(); updateButton(); };
    tile.append(retry, dismiss);
  } finally {
    busy--;
    updateButton();
  }
}
async function submitAnalysis() {
  $("#analyze").disabled = true; $("#retry").disabled = true; notify("");
  try { await saving; if (state.status === "draft") await api(`/api/reports/${session.id}`, { method: "PATCH", json: details() }); await api(`/api/reports/${session.id}/analyze`, { method: "POST" }); await refresh(); }
  catch (error) { notify(error.message); updateButton(); }
  finally { $("#retry").disabled = false; }
}
async function copyLink() {
  const button = $("#copy-link");
  try { await navigator.clipboard.writeText($("#share-url").value); button.textContent = "Copied ✓"; }
  catch (_) { $("#share-url").select(); button.textContent = "Select & copy"; }
}
async function init() {
  $("#copy-link")?.addEventListener("click", copyLink);
  if (!$("#editor")) return;
  try { session = JSON.parse(localStorage.getItem(storeKey) || "null"); } catch (_) { localStorage.removeItem(storeKey); }
  document.querySelectorAll("[data-upload]").forEach(input => input.addEventListener("change", async () => { const files = Array.from(input.files); input.value = ""; for (const file of files) await upload(file, input.dataset.upload); }));
  for (const key of ["vehicle", "plate", "company", "location", "notes"]) $(`#${key}`).addEventListener("change", saveDetails);
  $("#analyze").addEventListener("click", submitAnalysis); $("#retry").addEventListener("click", submitAnalysis);
  $("#new-report").addEventListener("click", () => { if (confirm("Save your report link before starting another report. Continue?")) { localStorage.removeItem(storeKey); location.reload(); } });
  $("#email-form").addEventListener("submit", async event => {
    event.preventDefault(); const button = $("#email-form button"); button.disabled = true;
    try { await api(`/api/reports/${session.id}/email`, { method: "POST", json: { email: $("#email").value, share_token: session.share_token } }); $("#email").value = ""; await refresh(); }
    catch (error) { $("#email-status").textContent = error.message; }
    finally { button.disabled = !state?.email_enabled; }
  });
  if (session) {
    try { await refresh(); for (const [key, value] of Object.entries(state.details)) $(`#${key}`).value = value; }
    catch (error) {
      if ([404, 410].includes(error.status)) {
        session = null;
        localStorage.removeItem(storeKey);
        notify(`${error.message} You can start a new report below.`);
      } else {
        needsRefresh = true;
        updateButton();
        notify(`Could not load your saved report. Reload this page to try again. ${error.message}`);
      }
    }
  }
}
init();
