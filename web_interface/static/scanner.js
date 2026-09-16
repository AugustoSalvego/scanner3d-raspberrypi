async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok || !result.success) throw new Error(result.message || "Request failed");
  return result;
}
function showMessage(message, type = "info") {
  const box = document.getElementById("systemMessage");
  box.textContent = message;
  box.className = "message show " + type;
}
async function action(url, payload) {
  try {
    const options = {method: "POST"};
    if (payload !== undefined) {
      options.headers = {"Content-Type": "application/json"};
      options.body = JSON.stringify(payload);
    }
    const result = await requestJson(url, options);
    showMessage(result.message, "success");
    await refresh();
    return result;
  } catch (error) { showMessage(error.message, "error"); }
}
function resetCamera() { return action("/reset-camera"); }
function startScan() { return action("/start-scan"); }
function stopScan() { return action("/stop-scan"); }
function generatePly() { return action("/generate-ply"); }
function downloadLastPly() { window.location.href = "/download-ply"; }
function deletePly(filename) { return action("/delete-ply/" + filename.split("/").map(encodeURIComponent).join("/")); }
async function clearCaptures() {
  if (await action("/clear-captures")) document.getElementById("capturesContainer").replaceChildren();
}
async function captureImage() {
  const result = await action("/capture");
  if (result) {
    const image = document.createElement("img");
    image.src = "/captures/" + encodeURIComponent(result.data.file);
    image.className = "thumbnail";
    image.alt = result.data.capture.source + " capture";
    document.getElementById("capturesContainer").prepend(image);
  }
}
async function toggleSimulationMode() {
  await action("/simulation-mode");
  await loadSettings();
  const video = document.querySelector('img[src^="/video"]');
  if (video) video.src = "/video?t=" + Date.now();
}
async function saveSettings() {
  await action("/settings", {
    scan_steps: Number(document.getElementById("scanStepsInput").value),
    step_delay: Number(document.getElementById("stepDelayInput").value),
    capture_delay: Number(document.getElementById("captureDelayInput").value)
  });
  await loadSettings();
}
async function loadSettings() {
  try {
    const settings = (await requestJson("/settings")).data.settings;
    document.getElementById("scanStepsInput").value = settings.scan_steps;
    document.getElementById("stepDelayInput").value = settings.step_delay;
    document.getElementById("captureDelayInput").value = settings.capture_delay;
  } catch (error) { showMessage(error.message, "error"); }
}
function line(parent, text, className = "session-info") {
  const element = document.createElement("div");
  element.className = className; element.textContent = text; parent.append(element);
  return element;
}
async function updateStatus() {
  const data = (await requestJson("/status")).data.status;
  const values = {
    versionStatus: data.version, runningStatus: data.running ? "YES" : "NO",
    framesCaptured: data.frames_captured, motorPosition: data.motor_position,
    lastCapture: data.last_capture ?? "None", plyStatus: data.point_cloud_generated ? "YES" : "NO",
    lastPointCloud: data.last_point_cloud ?? "None", lastScan: data.last_scan_time ?? "None",
    simulationStatus: data.simulation_mode ? "ON — synthetic images" : "OFF — physical hardware",
    environmentBadge: "Mode: " + data.mode
  };
  Object.entries(values).forEach(([id, text]) => document.getElementById(id).textContent = text);
  const badge = document.getElementById("scannerBadge");
  badge.textContent = data.status_badge;
  badge.className = "status-badge " + (data.running ? "status-scanning" :
      data.phase === "error" ? "status-error" : data.phase === "completed" ? "status-completed" : "status-idle");
  document.querySelectorAll('button[onclick="startScan()"], button[onclick="generatePly()"], button[onclick="saveSettings()"], button[onclick="toggleSimulationMode()"]').forEach(button => button.disabled = data.running);
}
async function updateLogs() {
  const logs = (await requestJson("/logs")).data.logs;
  const container = document.getElementById("logsContainer");
  container.replaceChildren();
  logs.forEach(text => line(container, text, "log"));
}
async function updatePointClouds() {
  const files = (await requestJson("/point-clouds")).data.files;
  const container = document.getElementById("pointCloudsContainer");
  container.replaceChildren();
  if (!files.length) { line(container, "No point clouds generated yet."); return; }
  files.forEach(file => {
    const row = line(container, file.name + " • " + file.size_kb + " KB", "session-row");
    const link = document.createElement("a");
    link.href = file.download_url; link.textContent = "Download"; row.append(link);
    const button = document.createElement("button");
    button.textContent = "Delete"; button.className = "small-button btn-danger";
    button.addEventListener("click", () => deletePly(file.name)); row.append(button);
  });
}
async function updateScanSessions() {
  const sessions = (await requestJson("/scan-sessions")).data.sessions;
  const container = document.getElementById("scanSessionsContainer");
  container.replaceChildren();
  if (!sessions.length) { line(container, "No scan sessions created yet."); return; }
  sessions.forEach(session => {
    const row = document.createElement("div"); row.className = "session-row";
    line(row, session.session_id, "session-title");
    line(row, "Status: " + session.status + " • Source: " + session.source);
    line(row, "Captures: " + session.captures.length + " • Created: " + session.created_at);
    if (session.error) line(row, session.error);
    const metadata = document.createElement("a");
    metadata.href = session.metadata_url; metadata.textContent = "Metadata and acquisition angles";
    row.append(metadata);
    if (session.preview_url) {
      const link = document.createElement("a"); link.href = session.preview_url; link.target = "_blank";
      const image = document.createElement("img"); image.src = session.preview_url;
      image.alt = session.source + " point cloud preview"; image.loading = "lazy";
      image.style.cssText = "display:block;width:100%;max-width:1000px;margin-top:12px";
      link.append(image); row.append(link);
    }
    container.append(row);
  });
}
let refreshing = false;
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try { await Promise.all([updateStatus(), updateLogs(), updatePointClouds(), updateScanSessions()]); }
  catch (error) { showMessage(error.message, "error"); }
  finally { refreshing = false; }
}
loadSettings(); refresh(); setInterval(refresh, 2000);
