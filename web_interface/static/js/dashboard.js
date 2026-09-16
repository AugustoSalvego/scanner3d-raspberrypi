/*
 * Scanner3D dashboard.
 *
 * Talks to the /api/* endpoints, which all answer the same envelope:
 * {success, message, data}. Everything the server sends is inserted with
 * textContent rather than innerHTML - filenames and log lines are data, not
 * markup.
 *
 * Polling is adaptive: one second while a scan is running, five seconds when
 * the scanner is idle. A Raspberry Pi 3 has better things to do than answer a
 * status request every second while nothing is happening.
 */

(function () {
  "use strict";

  const POLL_ACTIVE_MS = 1000;
  const POLL_IDLE_MS = 5000;

  const state = {
    status: null,
    settings: null,
    selectedSession: null,
    selectedCloud: null,
    viewer: null,
    viewerError: null,
    pollTimer: null,
    pollInterval: POLL_IDLE_MS,
    busy: false,
  };

  // ------------------------------------------------------------------ utils
  const $ = (id) => document.getElementById(id);

  function toast(message, kind) {
    const element = $("toast");

    element.textContent = message;
    element.className = "toast show " + (kind || "info");

    clearTimeout(element.dataset.timer);

    const timer = setTimeout(() => {
      element.className = "toast";
    }, 6000);

    element.dataset.timer = String(timer);
  }

  async function request(url, options) {
    const response = await fetch(url, options);

    let payload = {};

    try {
      payload = await response.json();
    } catch (error) {
      payload = {};
    }

    if (!response.ok || payload.success === false) {
      throw new Error(payload.message || "Request failed (" + response.status + ").");
    }

    return payload.data || {};
  }

  function postJson(url, body, method) {
    return request(url, {
      method: method || "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  /** Wrap an action so the UI cannot fire two conflicting operations at once. */
  function action(handler) {
    return async function () {
      if (state.busy) {
        return;
      }

      state.busy = true;
      applyControlState();

      try {
        await handler();
      } catch (error) {
        toast(error.message, "error");
      } finally {
        state.busy = false;
        await refreshStatus();
      }
    };
  }

  function text(id, value) {
    const element = $(id);

    if (element) {
      element.textContent = value === null || value === undefined || value === "" ? "-" : String(value);
    }
  }

  function formatSeconds(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const minutes = Math.floor(total / 60);

    return minutes + "m " + String(total % 60).padStart(2, "0") + "s";
  }

  function clearChildren(element) {
    while (element.firstChild) {
      element.removeChild(element.firstChild);
    }
  }

  function emptyState(container, message) {
    clearChildren(container);

    const div = document.createElement("div");

    div.className = "empty";
    div.textContent = message;

    container.appendChild(div);
  }

  function makeButton(label, className, onClick) {
    const button = document.createElement("button");

    button.className = "small " + (className || "ghost");
    button.textContent = label;
    button.addEventListener("click", onClick);

    return button;
  }

  // ----------------------------------------------------------------- status
  async function refreshStatus() {
    try {
      const data = await request("/api/status");

      state.status = data;

      renderStatus(data);
      applyControlState();

      const desired = data.scan.running ? POLL_ACTIVE_MS : POLL_IDLE_MS;

      if (desired !== state.pollInterval) {
        state.pollInterval = desired;
        schedulePoll();
      }
    } catch (error) {
      const badge = $("badge");

      badge.textContent = "OFFLINE";
      badge.className = "pill bad";
    }
  }

  function renderStatus(data) {
    const scan = data.scan;

    const badge = $("badge");

    badge.textContent = data.badge;
    badge.className =
      "pill " +
      (data.badge === "SCANNING" || data.badge === "RECONSTRUCTING" || data.badge === "PREPARING"
        ? "busy"
        : data.badge === "ERROR"
          ? "bad"
          : data.badge === "NEEDS SETUP" || data.badge === "CANCELLED"
            ? "warn"
            : "ok");

    const modePill = $("modePill");

    modePill.textContent = "Mode: " + data.mode;
    modePill.className = "pill " + (data.mode === "physical" ? "ok" : "busy");

    text("serviceVersion", data.service.version);

    // Hardware ------------------------------------------------------------
    const camera = data.camera || {};
    const motor = data.motor || {};
    const laser = data.laser || {};

    text("hwCamera", camera.available ? "available" : camera.open ? "open" : "not open");

    const actual = camera.actual || {};
    const requested = camera.requested || {};

    text(
      "hwCameraFormat",
      actual.width
        ? actual.width + "x" + actual.height + " " + (actual.fourcc || "") +
          (requested.width && (actual.width !== requested.width || actual.height !== requested.height)
            ? " (requested " + requested.width + "x" + requested.height + ")"
            : "")
        : "unknown"
    );

    text("hwMotor", motor.driver ? motor.driver + (motor.open ? " (open)" : " (closed)") : "-");
    text("hwMotorPins", Array.isArray(motor.pins) ? "BCM " + motor.pins.join(", ") : "simulated");
    text("hwLaser", (laser.control || "-") + " / " + (laser.state || "-"));

    // Scan ----------------------------------------------------------------
    text("scanPhase", scan.phase);
    text("scanSession", scan.session_id || scan.last_session_id);
    text(
      "scanCaptures",
      scan.capture_total ? scan.capture_index + " / " + scan.capture_total : scan.capture_index
    );
    text("scanAngle", scan.angle_deg === null ? "-" : Number(scan.angle_deg).toFixed(2) + " deg");
    text("scanMotor", scan.motor_position + (scan.motor_target !== null ? " -> " + scan.motor_target : ""));
    text("scanElapsed", formatSeconds(scan.elapsed_seconds));
    text("scanPoints", scan.last_point_count === null ? "-" : scan.last_point_count);
    text("scanLastError", scan.last_error);

    $("progressFill").style.width = scan.progress_percent + "%";
    text("progressLabel", scan.progress_percent.toFixed(1) + "%");

    // Calibration ---------------------------------------------------------
    renderCalibration(data.calibration, data.mode);

    // Blocking issues -----------------------------------------------------
    const issues = $("scanIssues");

    clearChildren(issues);

    if (scan.blocking_issues && scan.blocking_issues.length && !scan.running) {
      const list = document.createElement("ul");

      list.className = "issue-list";

      scan.blocking_issues.forEach((issue) => {
        const item = document.createElement("li");

        item.textContent = issue;
        list.appendChild(item);
      });

      issues.appendChild(list);
    }
  }

  function renderCalibration(calibration, mode) {
    const container = $("calibrationStatus");

    clearChildren(container);

    [
      ["Camera", calibration.camera],
      ["Laser plane", calibration.laser],
      ["Turntable", calibration.turntable],
    ].forEach(([label, profile]) => {
      const row = document.createElement("div");

      row.className = "row";

      const name = document.createElement("span");

      name.textContent = label;

      const value = document.createElement("span");

      if (!profile || !profile.present) {
        value.textContent = "not calibrated";
      } else if (!profile.valid) {
        value.textContent = "invalid";
      } else {
        const parts = [profile.quality || "valid"];

        if (profile.source) parts.push(profile.source);
        if (profile.rms_reprojection_error !== undefined)
          parts.push(Number(profile.rms_reprojection_error).toFixed(3) + " px");
        if (profile.rms_mm !== undefined && label !== "Turntable")
          parts.push(Number(profile.rms_mm).toFixed(3) + " mm");
        if (profile.created_at) parts.push(String(profile.created_at).slice(0, 16).replace("T", " "));

        value.textContent = parts.join(" / ");
      }

      row.appendChild(name);
      row.appendChild(value);
      container.appendChild(row);
    });

    const note = $("calibrationNote");

    if (mode === "simulation") {
      note.textContent =
        "Simulation mode uses the exact geometry of the virtual rig. These stored " +
        "calibrations apply to physical mode only.";
    } else if (calibration.ready_for_physical_scan) {
      note.textContent = "Calibration is complete. Physical scanning is unlocked.";
    } else {
      note.textContent =
        "Physical scanning stays blocked until all three calibrations are valid. " +
        "See docs/calibration.md for the step by step procedure.";
    }

    const issues = $("calibrationIssues");

    clearChildren(issues);

    if (calibration.blocking_issues && calibration.blocking_issues.length && mode !== "simulation") {
      const list = document.createElement("ul");

      list.className = "issue-list";

      calibration.blocking_issues.slice(0, 8).forEach((issue) => {
        const item = document.createElement("li");

        item.textContent = issue;
        list.appendChild(item);
      });

      issues.appendChild(list);
    }
  }

  /** Disable every control that would conflict with what is happening now. */
  function applyControlState() {
    const status = state.status;
    const running = Boolean(status && status.scan.running);
    const canStart = Boolean(status && status.scan.can_start) && !state.busy;
    const offline = Boolean(status && status.mode === "offline");

    $("startScan").disabled = !canStart || running;
    $("stopScan").disabled = !running;

    ["captureImage", "clearCaptures", "resetCamera", "diagnoseLaser"].forEach((id) => {
      $(id).disabled = running || state.busy || offline;
    });

    ["saveSettings", "modeSelect", "calibrateCamera", "calibrateLaser", "saveTurntable"].forEach(
      (id) => {
        const element = $(id);

        if (element) {
          element.disabled = running || state.busy;
        }
      }
    );

    const reconstruct = $("reconstructSession");

    reconstruct.disabled = running || state.busy || !state.selectedSession;
  }

  function schedulePoll() {
    clearInterval(state.pollTimer);

    state.pollTimer = setInterval(() => {
      refreshStatus();
      refreshLogs();
    }, state.pollInterval);
  }

  // ------------------------------------------------------------------- logs
  async function refreshLogs() {
    try {
      const data = await request("/api/logs?limit=120");
      const container = $("logs");

      clearChildren(container);

      if (!data.logs.length) {
        emptyState(container, "No log entries yet.");

        return;
      }

      data.logs.forEach((entry) => {
        const line = document.createElement("div");

        line.className = "log-line " + entry.level;

        const time = document.createElement("span");
        time.className = "t";
        time.textContent = entry.time;

        const level = document.createElement("span");
        level.className = "lvl";
        level.textContent = entry.level;

        const message = document.createElement("span");
        message.textContent = entry.message;

        line.appendChild(time);
        line.appendChild(level);
        line.appendChild(message);

        container.appendChild(line);
      });
    } catch (error) {
      /* the status poll already reports connectivity problems */
    }
  }

  // --------------------------------------------------------------- settings
  async function loadSettings() {
    const data = await request("/api/settings");

    state.settings = data.settings;

    const settings = data.settings;

    $("modeSelect").value = settings.mode;
    $("captureCount").value = settings.scan.capture_count;
    $("settleDelay").value = settings.scan.settle_delay;
    $("captureDelay").value = settings.scan.capture_delay;
    $("autoReconstruct").checked = settings.scan.auto_reconstruct;
    $("saveRawFrames").checked = settings.scan.save_raw_frames;

    $("stepsPerRevolution").value = settings.motor.steps_per_revolution;
    $("stepDelay").value = settings.motor.step_delay;
    $("motorDirection").value = String(settings.motor.direction);

    $("cameraWidth").value = settings.camera.width;
    $("cameraHeight").value = settings.camera.height;
    $("cameraFourcc").value = settings.camera.fourcc || "";
    $("flushFrames").value = settings.camera.flush_frames;

    $("saturationMin").value = settings.detector.saturation_min;
    $("valueMin").value = settings.detector.value_min;
    $("minConfidence").value = settings.detector.min_confidence;
    $("voxelSize").value = settings.filtering.voxel_size_mm;
  }

  const saveSettings = action(async () => {
    const patch = {
      scan: {
        capture_count: Number($("captureCount").value),
        settle_delay: Number($("settleDelay").value),
        capture_delay: Number($("captureDelay").value),
        auto_reconstruct: $("autoReconstruct").checked,
        save_raw_frames: $("saveRawFrames").checked,
      },
      motor: {
        steps_per_revolution: Number($("stepsPerRevolution").value),
        step_delay: Number($("stepDelay").value),
        direction: Number($("motorDirection").value),
      },
      camera: {
        width: Number($("cameraWidth").value),
        height: Number($("cameraHeight").value),
        fourcc: $("cameraFourcc").value.trim() || null,
        flush_frames: Number($("flushFrames").value),
      },
      detector: {
        saturation_min: Number($("saturationMin").value),
        value_min: Number($("valueMin").value),
        min_confidence: Number($("minConfidence").value),
      },
      filtering: {
        voxel_size_mm: Number($("voxelSize").value),
      },
    };

    await request("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });

    toast("Configuration saved.", "success");

    await loadSettings();
  });

  const changeMode = action(async () => {
    const mode = $("modeSelect").value;

    await postJson("/api/settings/mode", { mode: mode });

    toast("Mode set to " + mode + ".", "success");

    await loadSettings();
  });

  // ----------------------------------------------------------------- camera
  const captureImage = action(async () => {
    const data = await postJson("/api/camera/capture");

    toast("Captured " + data.file.name + ".", "success");

    await refreshCaptures();
  });

  const clearCaptures = action(async () => {
    const data = await postJson("/api/camera/captures/clear");

    toast("Deleted " + data.deleted + " capture(s).", "success");

    await refreshCaptures();
  });

  const resetCamera = action(async () => {
    await postJson("/api/camera/reset");

    toast("Camera released; it reopens on the next frame.", "info");

    const image = $("stream");

    image.src = "/video?t=" + Date.now();
  });

  async function refreshCaptures() {
    try {
      const data = await request("/api/camera/captures");
      const container = $("captureThumbs");

      if (!data.captures.length) {
        emptyState(container, "No manual captures yet.");

        return;
      }

      clearChildren(container);

      data.captures.slice(0, 24).forEach((capture) => {
        const figure = document.createElement("figure");
        const image = document.createElement("img");

        image.src = capture.url;
        image.alt = capture.name;
        image.loading = "lazy";

        const caption = document.createElement("figcaption");

        caption.textContent = capture.size_kb + " KB";

        figure.appendChild(image);
        figure.appendChild(caption);
        container.appendChild(figure);
      });
    } catch (error) {
      /* non fatal */
    }
  }

  // ------------------------------------------------------------------- scan
  const startScan = action(async () => {
    const data = await postJson("/api/scan/start");

    toast("Scan started: " + data.session_id, "success");

    state.pollInterval = POLL_ACTIVE_MS;
    schedulePoll();
  });

  const stopScan = action(async () => {
    await postJson("/api/scan/stop");

    toast("Stop requested.", "warning");
  });

  async function refreshSessions() {
    try {
      const data = await request("/api/scan/sessions");
      const container = $("sessionsTable");

      if (!data.sessions.length) {
        emptyState(container, "No scan sessions yet.");

        return;
      }

      clearChildren(container);

      const wrap = document.createElement("div");

      wrap.className = "table-wrap";

      const table = document.createElement("table");
      const head = document.createElement("thead");

      head.innerHTML =
        "<tr><th>Session</th><th>Status</th><th>Mode</th><th>Captures</th><th>Points</th><th>Created</th><th></th></tr>";

      const body = document.createElement("tbody");

      data.sessions.slice(0, 40).forEach((session) => {
        const row = document.createElement("tr");

        if (session.session_id === state.selectedSession) {
          row.className = "selected";
        }

        [
          session.session_id,
          session.status,
          session.mode,
          String(session.capture_count),
          session.point_count === null ? "-" : String(session.point_count),
          (session.created_at || "").replace("T", " "),
        ].forEach((value) => {
          const cell = document.createElement("td");

          cell.textContent = value;
          row.appendChild(cell);
        });

        const actions = document.createElement("td");

        actions.appendChild(
          makeButton("Open", "ghost", () => selectSession(session.session_id))
        );
        actions.appendChild(
          makeButton("Delete", "danger", () => deleteSession(session.session_id))
        );

        row.appendChild(actions);
        body.appendChild(row);
      });

      table.appendChild(head);
      table.appendChild(body);
      wrap.appendChild(table);
      container.appendChild(wrap);
    } catch (error) {
      /* non fatal */
    }
  }

  async function selectSession(sessionId) {
    state.selectedSession = sessionId;

    applyControlState();

    try {
      const data = await request("/api/scan/sessions/" + encodeURIComponent(sessionId));
      const session = data.session;

      text("detailId", session.session_id);
      text("detailStatus", session.status);
      text("detailMode", session.mode);
      text("detailCreated", (session.created_at || "").replace("T", " "));
      text("detailFinished", (session.finished_at || "").replace("T", " "));
      text("detailCaptures", session.captures.length);

      const reconstruction = session.reconstruction || {};

      text(
        "detailPoints",
        reconstruction.points_after_filter === undefined
          ? "-"
          : reconstruction.points_after_filter
      );
      text(
        "detailFrames",
        reconstruction.frames_accepted === undefined
          ? "-"
          : reconstruction.frames_accepted + " / " + reconstruction.frames_total
      );
      text(
        "detailLaserPixels",
        reconstruction.laser_pixels === undefined ? "-" : reconstruction.laser_pixels
      );
      text(
        "detailRejected",
        reconstruction.accepted_points === undefined
          ? "-"
          : [
              "parallel " + (reconstruction.rejected_near_parallel || 0),
              "behind " + (reconstruction.rejected_behind_camera || 0),
              "depth " + (reconstruction.rejected_out_of_depth || 0),
              "radius " + (reconstruction.rejected_out_of_radius || 0),
              "height " + (reconstruction.rejected_out_of_height || 0),
            ].join(", ")
      );
      text(
        "detailTiming",
        reconstruction.reconstruction_seconds === undefined
          ? "-"
          : reconstruction.reconstruction_seconds + " s"
      );

      const errors = session.errors || [];

      text("detailErrors", errors.length ? errors[errors.length - 1].message : "none");

      renderSessionFrames(session);
      renderSessionClouds(session);

      $("sessionDetail").hidden = false;

      await refreshSessions();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function renderSessionFrames(session) {
    const container = $("sessionFrames");

    if (!session.captures.length) {
      emptyState(container, "This session has no stored frames.");

      return;
    }

    clearChildren(container);

    session.captures.forEach((capture) => {
      const figure = document.createElement("figure");
      const image = document.createElement("img");

      image.src = capture.url;
      image.alt = capture.filename;
      image.loading = "lazy";

      const caption = document.createElement("figcaption");

      caption.textContent = Number(capture.angle_deg).toFixed(1) + " deg";

      figure.appendChild(image);
      figure.appendChild(caption);
      container.appendChild(figure);
    });
  }

  function renderSessionClouds(session) {
    const container = $("sessionClouds");

    if (!session.point_clouds.length) {
      emptyState(container, "This session has no point cloud yet.");

      return;
    }

    clearChildren(container);

    const row = document.createElement("div");

    row.className = "button-row";

    session.point_clouds.forEach((cloud) => {
      row.appendChild(
        makeButton("View " + cloud.filename, "violet", () =>
          loadCloud(cloud.filename, session.session_id)
        )
      );
      row.appendChild(
        makeButton("Download", "ghost", () => {
          window.location.href = cloud.download_url;
        })
      );
    });

    container.appendChild(row);
  }

  const reconstructSession = action(async () => {
    if (!state.selectedSession) {
      return;
    }

    toast("Reconstructing " + state.selectedSession + "...", "info");

    const data = await postJson(
      "/api/scan/sessions/" + encodeURIComponent(state.selectedSession) + "/reconstruct"
    );

    toast("Reconstructed " + data.point_count + " points.", "success");

    await selectSession(state.selectedSession);
    await refreshClouds();
  });

  async function deleteSession(sessionId) {
    if (!window.confirm("Delete session " + sessionId + " and all its files?")) {
      return;
    }

    try {
      await request("/api/scan/sessions/" + encodeURIComponent(sessionId), {
        method: "DELETE",
      });

      toast("Session deleted.", "success");

      if (state.selectedSession === sessionId) {
        state.selectedSession = null;
        $("sessionDetail").hidden = true;
      }

      await refreshSessions();
      await refreshClouds();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  // ----------------------------------------------------------- point clouds
  async function refreshClouds() {
    try {
      const data = await request("/api/point-clouds");
      const container = $("cloudsTable");

      if (!data.files.length) {
        emptyState(container, "No point clouds generated yet.");

        return;
      }

      clearChildren(container);

      const wrap = document.createElement("div");

      wrap.className = "table-wrap";

      const table = document.createElement("table");
      const head = document.createElement("thead");

      head.innerHTML =
        "<tr><th>File</th><th>Session</th><th>Size</th><th>Modified</th><th></th></tr>";

      const body = document.createElement("tbody");

      data.files.forEach((file) => {
        const row = document.createElement("tr");

        [
          file.name,
          file.session_id || "manual",
          file.size_kb + " KB",
          file.modified_at,
        ].forEach((value) => {
          const cell = document.createElement("td");

          cell.textContent = value;
          row.appendChild(cell);
        });

        const actions = document.createElement("td");

        actions.appendChild(
          makeButton("View", "violet", () => loadCloud(file.name, file.session_id))
        );
        actions.appendChild(
          makeButton("Download", "ghost", () => {
            window.location.href = file.session_id
              ? "/download/point-cloud/" +
                encodeURIComponent(file.session_id) +
                "/" +
                encodeURIComponent(file.name)
              : "/download/point-cloud/" + encodeURIComponent(file.name);
          })
        );
        actions.appendChild(
          makeButton("Delete", "danger", () => deleteCloud(file.name, file.session_id))
        );

        row.appendChild(actions);
        body.appendChild(row);
      });

      table.appendChild(head);
      table.appendChild(body);
      wrap.appendChild(table);
      container.appendChild(wrap);
    } catch (error) {
      /* non fatal */
    }
  }

  async function deleteCloud(filename, sessionId) {
    if (!window.confirm("Delete " + filename + "?")) {
      return;
    }

    const url = sessionId
      ? "/api/point-clouds/" + encodeURIComponent(sessionId) + "/" + encodeURIComponent(filename)
      : "/api/point-clouds/" + encodeURIComponent(filename);

    try {
      await request(url, { method: "DELETE" });

      toast("Point cloud deleted.", "success");

      await refreshClouds();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  // ----------------------------------------------------------------- viewer
  function ensureViewer() {
    if (state.viewer || state.viewerError) {
      return state.viewer;
    }

    try {
      state.viewer = new window.PointCloudViewer($("viewerCanvas"));
    } catch (error) {
      state.viewerError = error.message;

      $("viewerMessage").textContent =
        "3D viewer unavailable: " + error.message + " You can still download the PLY files.";
    }

    return state.viewer;
  }

  async function loadCloud(filename, sessionId) {
    const viewer = ensureViewer();

    if (!viewer) {
      toast(state.viewerError, "error");

      return;
    }

    const params = new URLSearchParams({ file: filename });

    if (sessionId) {
      params.set("session", sessionId);
    }

    params.set("max_points", $("maxPoints").value || "300000");

    $("viewerMessage").textContent = "Loading " + filename + "...";

    try {
      const response = await fetch("/api/point-clouds/data?" + params.toString());

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));

        throw new Error(payload.message || "Could not load the point cloud.");
      }

      const total = response.headers.get("X-Point-Count-Total");
      const buffer = await response.arrayBuffer();
      const info = viewer.loadBinary(buffer);

      state.selectedCloud = { filename: filename, sessionId: sessionId };

      $("colorMode").value = info.hasColors ? "rgb" : "height";
      viewer.setColorMode($("colorMode").value);

      const size = info.bounds.max.map((value, axis) =>
        (value - info.bounds.min[axis]).toFixed(1)
      );

      $("viewerMessage").textContent =
        filename +
        " - showing " +
        info.count.toLocaleString() +
        (Number(total) > info.count ? " of " + Number(total).toLocaleString() : "") +
        " points, bounding box " +
        size.join(" x ") +
        " mm";
    } catch (error) {
      $("viewerMessage").textContent = error.message;

      toast(error.message, "error");
    }
  }

  // ------------------------------------------------------------ diagnostics
  const diagnoseLaser = action(async () => {
    const data = await postJson("/api/calibration/laser/diagnose");
    const container = $("diagnosticsImages");

    clearChildren(container);

    Object.keys(data.images).forEach((name) => {
      const figure = document.createElement("figure");

      figure.style.margin = "0";

      const image = document.createElement("img");

      image.src = data.images[name] + "?t=" + Date.now();
      image.alt = name;

      const caption = document.createElement("figcaption");

      caption.textContent = name;
      caption.style.color = "var(--text-muted)";
      caption.style.fontSize = "11px";
      caption.style.textAlign = "center";

      figure.appendChild(image);
      figure.appendChild(caption);
      container.appendChild(figure);
    });

    const detection = data.detection;

    text("diagAccepted", detection.accepted ? "yes" : "no");
    text("diagPoints", detection.point_count);
    text("diagConfidence", Number(detection.mean_confidence || 0).toFixed(3));
    text("diagThickness", Number(detection.mean_thickness_px || 0).toFixed(2) + " px");
    text("diagReason", detection.reason);

    toast(
      detection.accepted
        ? "Laser detected: " + detection.point_count + " points."
        : "Laser not detected: " + detection.reason,
      detection.accepted ? "success" : "warning"
    );
  });

  const calibrateCamera = action(async () => {
    const payload = {
      folder: $("cameraFolder").value.trim() || "camera",
      checkerboard: {
        columns: Number($("boardColumns").value),
        rows: Number($("boardRows").value),
        square_size_mm: Number($("boardSquare").value),
      },
    };

    toast("Running camera calibration...", "info");

    const data = await postJson("/api/calibration/camera", payload);

    toast(
      "Camera calibrated: RMS " +
        Number(data.calibration.rms_reprojection_error).toFixed(4) +
        " px from " +
        data.calibration.image_count +
        " views.",
      "success"
    );
  });

  const calibrateLaser = action(async () => {
    const payload = {
      folder: $("laserFolder").value.trim() || "laser",
      checkerboard: {
        columns: Number($("boardColumns").value),
        rows: Number($("boardRows").value),
        square_size_mm: Number($("boardSquare").value),
      },
    };

    toast("Fitting the laser plane...", "info");

    const data = await postJson("/api/calibration/laser", payload);

    toast(
      "Laser plane fitted: RMS " +
        Number(data.profile.rms_mm).toFixed(4) +
        " mm from " +
        data.profile.pose_count +
        " poses.",
      "success"
    );
  });

  const saveTurntable = action(async () => {
    const payload = {
      axis_origin: $("axisOrigin").value.split(",").map(Number),
      axis_direction: $("axisDirection").value.split(",").map(Number),
      steps_per_revolution: Number($("stepsPerRevolution").value),
      rotation_direction: Number($("rotationDirection").value),
    };

    await postJson("/api/calibration/turntable/manual", payload);

    toast("Turntable calibration saved.", "success");
  });

  // ------------------------------------------------------------------- init
  function bind() {
    $("startScan").addEventListener("click", startScan);
    $("stopScan").addEventListener("click", stopScan);
    $("captureImage").addEventListener("click", captureImage);
    $("clearCaptures").addEventListener("click", clearCaptures);
    $("resetCamera").addEventListener("click", resetCamera);
    $("saveSettings").addEventListener("click", saveSettings);
    $("modeSelect").addEventListener("change", changeMode);
    $("reconstructSession").addEventListener("click", reconstructSession);
    $("diagnoseLaser").addEventListener("click", diagnoseLaser);
    $("calibrateCamera").addEventListener("click", calibrateCamera);
    $("calibrateLaser").addEventListener("click", calibrateLaser);
    $("saveTurntable").addEventListener("click", saveTurntable);
    $("refreshAll").addEventListener("click", () => {
      refreshSessions();
      refreshClouds();
      refreshCaptures();
      toast("Lists refreshed.", "info");
    });

    $("resetView").addEventListener("click", () => {
      if (state.viewer) state.viewer.resetCamera();
    });

    $("colorMode").addEventListener("change", (event) => {
      if (state.viewer) state.viewer.setColorMode(event.target.value);
    });

    $("pointSize").addEventListener("input", (event) => {
      if (state.viewer) state.viewer.setPointSize(event.target.value);

      text("pointSizeLabel", Number(event.target.value).toFixed(1));
    });

    $("showGrid").addEventListener("change", (event) => {
      if (state.viewer) state.viewer.setShowGrid(event.target.checked);
    });

    $("maxPoints").addEventListener("change", () => {
      if (state.selectedCloud) {
        loadCloud(state.selectedCloud.filename, state.selectedCloud.sessionId);
      }
    });
  }

  async function boot() {
    bind();

    try {
      await loadSettings();
    } catch (error) {
      toast("Could not load the configuration: " + error.message, "error");
    }

    await refreshStatus();
    await Promise.all([refreshLogs(), refreshSessions(), refreshClouds(), refreshCaptures()]);

    schedulePoll();
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
