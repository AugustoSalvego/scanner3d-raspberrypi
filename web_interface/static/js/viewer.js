/*
 * Point cloud viewer.
 *
 * Deliberately dependency free: raw WebGL in about 400 lines instead of a
 * 600 KB library from a CDN. That matters here for two reasons - the scanner
 * is often used on a workshop network with no internet access, and the
 * Raspberry Pi has to serve every byte itself.
 *
 * Coordinates follow the reconstruction output: the object frame has +Z along
 * the turntable axis, so the grid lies on XY and the camera orbits with Z up.
 */

(function (global) {
  "use strict";

  // ---------------------------------------------------------------- matrices
  function identity() {
    return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  }

  function multiply(a, b) {
    const out = new Float32Array(16);

    for (let row = 0; row < 4; row += 1) {
      for (let column = 0; column < 4; column += 1) {
        let sum = 0;

        for (let k = 0; k < 4; k += 1) {
          sum += a[k * 4 + row] * b[column * 4 + k];
        }

        out[column * 4 + row] = sum;
      }
    }

    return out;
  }

  function perspective(fovyRadians, aspect, near, far) {
    const f = 1 / Math.tan(fovyRadians / 2);
    const out = new Float32Array(16);

    out[0] = f / aspect;
    out[5] = f;
    out[10] = (far + near) / (near - far);
    out[11] = -1;
    out[14] = (2 * far * near) / (near - far);

    return out;
  }

  function normalize(v) {
    const length = Math.hypot(v[0], v[1], v[2]) || 1;

    return [v[0] / length, v[1] / length, v[2] / length];
  }

  function cross(a, b) {
    return [
      a[1] * b[2] - a[2] * b[1],
      a[2] * b[0] - a[0] * b[2],
      a[0] * b[1] - a[1] * b[0],
    ];
  }

  function subtract(a, b) {
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  }

  function dot(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  }

  function lookAt(eye, center, up) {
    const z = normalize(subtract(eye, center));
    let x = cross(up, z);

    if (Math.hypot(x[0], x[1], x[2]) < 1e-6) {
      // Looking straight along the up vector: pick any perpendicular axis.
      x = cross([1, 0, 0], z);
    }

    x = normalize(x);

    const y = cross(z, x);
    const out = identity();

    out[0] = x[0]; out[4] = x[1]; out[8] = x[2];
    out[1] = y[0]; out[5] = y[1]; out[9] = y[2];
    out[2] = z[0]; out[6] = z[1]; out[10] = z[2];
    out[12] = -dot(x, eye);
    out[13] = -dot(y, eye);
    out[14] = -dot(z, eye);

    return out;
  }

  // ----------------------------------------------------------------- shaders
  const POINT_VERTEX_SHADER = `
    attribute vec3 aPosition;
    attribute vec3 aColor;

    uniform mat4 uViewProjection;
    uniform float uPointSize;
    uniform int uColorMode;
    uniform float uHeightMin;
    uniform float uHeightRange;

    varying vec3 vColor;

    vec3 ramp(float t) {
      // Perceptually even-ish blue -> cyan -> yellow ramp.
      vec3 low = vec3(0.16, 0.34, 0.75);
      vec3 mid = vec3(0.20, 0.78, 0.78);
      vec3 high = vec3(0.98, 0.85, 0.35);

      return t < 0.5
        ? mix(low, mid, t * 2.0)
        : mix(mid, high, (t - 0.5) * 2.0);
    }

    void main() {
      gl_Position = uViewProjection * vec4(aPosition, 1.0);
      gl_PointSize = uPointSize;

      if (uColorMode == 1) {
        vColor = aColor;
      } else if (uColorMode == 2) {
        float t = clamp((aPosition.z - uHeightMin) / uHeightRange, 0.0, 1.0);
        vColor = ramp(t);
      } else {
        vColor = vec3(0.85, 0.90, 0.96);
      }
    }
  `;

  const POINT_FRAGMENT_SHADER = `
    precision mediump float;

    varying vec3 vColor;

    void main() {
      // Round points read far better than squares at small sizes.
      vec2 offset = gl_PointCoord - vec2(0.5);

      if (dot(offset, offset) > 0.25) {
        discard;
      }

      gl_FragColor = vec4(vColor, 1.0);
    }
  `;

  const LINE_VERTEX_SHADER = `
    attribute vec3 aPosition;
    attribute vec3 aColor;

    uniform mat4 uViewProjection;

    varying vec3 vColor;

    void main() {
      gl_Position = uViewProjection * vec4(aPosition, 1.0);
      vColor = aColor;
    }
  `;

  const LINE_FRAGMENT_SHADER = `
    precision mediump float;

    varying vec3 vColor;

    void main() {
      gl_FragColor = vec4(vColor, 1.0);
    }
  `;

  function compile(gl, type, source) {
    const shader = gl.createShader(type);

    gl.shaderSource(shader, source);
    gl.compileShader(shader);

    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const message = gl.getShaderInfoLog(shader);

      gl.deleteShader(shader);

      throw new Error("Shader failed to compile: " + message);
    }

    return shader;
  }

  function createProgram(gl, vertexSource, fragmentSource) {
    const program = gl.createProgram();

    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, vertexSource));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, fragmentSource));
    gl.linkProgram(program);

    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error("Shader program failed to link: " + gl.getProgramInfoLog(program));
    }

    return program;
  }

  // ------------------------------------------------------------------ viewer
  function PointCloudViewer(canvas) {
    this.canvas = canvas;
    this.gl = null;
    this.pointCount = 0;
    this.hasColors = false;

    this.colorMode = "height";
    this.pointSize = 2.0;
    this.showGrid = true;

    this.orbit = { azimuth: 0.9, elevation: 0.5, distance: 300 };
    this.target = [0, 0, 0];
    this.bounds = null;

    this.dragging = null;
    this.lastPointer = null;

    this.initialise();
  }

  PointCloudViewer.prototype.initialise = function () {
    const options = { antialias: true, alpha: false, preserveDrawingBuffer: false };

    this.gl =
      this.canvas.getContext("webgl", options) ||
      this.canvas.getContext("experimental-webgl", options);

    if (!this.gl) {
      throw new Error("WebGL is not available in this browser.");
    }

    const gl = this.gl;

    this.pointProgram = createProgram(gl, POINT_VERTEX_SHADER, POINT_FRAGMENT_SHADER);
    this.lineProgram = createProgram(gl, LINE_VERTEX_SHADER, LINE_FRAGMENT_SHADER);

    this.positionBuffer = gl.createBuffer();
    this.colorBuffer = gl.createBuffer();
    this.lineBuffer = gl.createBuffer();
    this.lineColorBuffer = gl.createBuffer();

    gl.enable(gl.DEPTH_TEST);
    gl.clearColor(0.043, 0.055, 0.094, 1.0);

    this.attachEvents();
    this.buildGuides();
    this.resize();
  };

  PointCloudViewer.prototype.attachEvents = function () {
    const canvas = this.canvas;
    const self = this;

    canvas.addEventListener("pointerdown", function (event) {
      canvas.setPointerCapture(event.pointerId);

      self.dragging = event.button === 2 || event.shiftKey ? "pan" : "orbit";
      self.lastPointer = { x: event.clientX, y: event.clientY };
    });

    canvas.addEventListener("pointermove", function (event) {
      if (!self.dragging || !self.lastPointer) {
        return;
      }

      const dx = event.clientX - self.lastPointer.x;
      const dy = event.clientY - self.lastPointer.y;

      self.lastPointer = { x: event.clientX, y: event.clientY };

      if (self.dragging === "orbit") {
        self.orbit.azimuth -= dx * 0.008;
        self.orbit.elevation = Math.max(
          -1.5,
          Math.min(1.5, self.orbit.elevation + dy * 0.008)
        );
      } else {
        self.pan(dx, dy);
      }

      self.render();
    });

    function endDrag(event) {
      self.dragging = null;
      self.lastPointer = null;

      if (canvas.hasPointerCapture && canvas.hasPointerCapture(event.pointerId)) {
        canvas.releasePointerCapture(event.pointerId);
      }
    }

    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("contextmenu", function (event) {
      event.preventDefault();
    });

    canvas.addEventListener(
      "wheel",
      function (event) {
        event.preventDefault();

        const factor = Math.exp(event.deltaY * 0.001);

        self.orbit.distance = Math.max(1, Math.min(100000, self.orbit.distance * factor));
        self.render();
      },
      { passive: false }
    );

    window.addEventListener("resize", function () {
      self.resize();
    });
  };

  PointCloudViewer.prototype.pan = function (dx, dy) {
    const eye = this.eyePosition();
    const forward = normalize(subtract(this.target, eye));
    const right = normalize(cross(forward, [0, 0, 1]));
    const up = cross(right, forward);

    // Scale the pan with the distance so it feels the same at any zoom.
    const scale = this.orbit.distance * 0.0018;

    for (let axis = 0; axis < 3; axis += 1) {
      this.target[axis] += (-right[axis] * dx + up[axis] * dy) * scale;
    }
  };

  PointCloudViewer.prototype.eyePosition = function () {
    const { azimuth, elevation, distance } = this.orbit;
    const cosElevation = Math.cos(elevation);

    return [
      this.target[0] + distance * cosElevation * Math.cos(azimuth),
      this.target[1] + distance * cosElevation * Math.sin(azimuth),
      this.target[2] + distance * Math.sin(elevation),
    ];
  };

  PointCloudViewer.prototype.resize = function () {
    const canvas = this.canvas;
    const ratio = window.devicePixelRatio || 1;

    const width = Math.max(1, Math.floor(canvas.clientWidth * ratio));
    const height = Math.max(1, Math.floor(canvas.clientHeight * ratio));

    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    this.gl.viewport(0, 0, canvas.width, canvas.height);
    this.render();
  };

  PointCloudViewer.prototype.buildGuides = function () {
    const positions = [];
    const colors = [];

    const axisLength = 50;

    function pushLine(from, to, colour) {
      positions.push(from[0], from[1], from[2], to[0], to[1], to[2]);
      colors.push(colour[0], colour[1], colour[2], colour[0], colour[1], colour[2]);
    }

    pushLine([0, 0, 0], [axisLength, 0, 0], [0.90, 0.30, 0.34]);
    pushLine([0, 0, 0], [0, axisLength, 0], [0.35, 0.80, 0.45]);
    pushLine([0, 0, 0], [0, 0, axisLength], [0.35, 0.60, 0.95]);

    const extent = 100;
    const step = 10;
    const grey = [0.22, 0.27, 0.36];

    for (let value = -extent; value <= extent; value += step) {
      if (value === 0) {
        continue;
      }

      pushLine([-extent, value, 0], [extent, value, 0], grey);
      pushLine([value, -extent, 0], [value, extent, 0], grey);
    }

    this.guidePositions = new Float32Array(positions);
    this.guideColors = new Float32Array(colors);
    this.guideVertexCount = positions.length / 3;

    const gl = this.gl;

    gl.bindBuffer(gl.ARRAY_BUFFER, this.lineBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, this.guidePositions, gl.STATIC_DRAW);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.lineColorBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, this.guideColors, gl.STATIC_DRAW);
  };

  /**
   * Load the binary payload produced by GET /api/point-clouds/data.
   *
   * Layout (little endian): uint32 count, uint32 flags, float32 xyz[count*3],
   * then uint8 rgb[count*3] when bit 0 of flags is set.
   */
  PointCloudViewer.prototype.loadBinary = function (buffer) {
    const header = new DataView(buffer, 0, 8);
    const count = header.getUint32(0, true);
    const flags = header.getUint32(4, true);

    if (count === 0) {
      throw new Error("This point cloud is empty.");
    }

    const positions = new Float32Array(buffer, 8, count * 3);

    this.hasColors = (flags & 1) === 1;

    let colors = null;

    if (this.hasColors) {
      const raw = new Uint8Array(buffer, 8 + count * 12, count * 3);

      colors = new Float32Array(count * 3);

      for (let index = 0; index < colors.length; index += 1) {
        colors[index] = raw[index] / 255;
      }
    } else {
      colors = new Float32Array(count * 3);
    }

    const gl = this.gl;

    gl.bindBuffer(gl.ARRAY_BUFFER, this.positionBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STATIC_DRAW);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.colorBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, colors, gl.STATIC_DRAW);

    this.pointCount = count;
    this.bounds = this.computeBounds(positions);

    if (!this.hasColors && this.colorMode === "rgb") {
      this.colorMode = "height";
    }

    this.resetCamera();

    return { count: count, hasColors: this.hasColors, bounds: this.bounds };
  };

  PointCloudViewer.prototype.computeBounds = function (positions) {
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];

    for (let index = 0; index < positions.length; index += 3) {
      for (let axis = 0; axis < 3; axis += 1) {
        const value = positions[index + axis];

        if (value < min[axis]) min[axis] = value;
        if (value > max[axis]) max[axis] = value;
      }
    }

    return { min: min, max: max };
  };

  PointCloudViewer.prototype.resetCamera = function () {
    if (!this.bounds) {
      return;
    }

    const { min, max } = this.bounds;

    this.target = [
      (min[0] + max[0]) / 2,
      (min[1] + max[1]) / 2,
      (min[2] + max[2]) / 2,
    ];

    const size = Math.max(
      max[0] - min[0],
      max[1] - min[1],
      max[2] - min[2],
      1
    );

    this.orbit = { azimuth: 0.9, elevation: 0.45, distance: size * 2.4 };

    this.render();
  };

  PointCloudViewer.prototype.setColorMode = function (mode) {
    this.colorMode = mode === "rgb" && !this.hasColors ? "height" : mode;
    this.render();
  };

  PointCloudViewer.prototype.setPointSize = function (size) {
    this.pointSize = Math.max(0.5, Math.min(12, Number(size) || 2));
    this.render();
  };

  PointCloudViewer.prototype.setShowGrid = function (enabled) {
    this.showGrid = Boolean(enabled);
    this.render();
  };

  PointCloudViewer.prototype.viewProjection = function () {
    const aspect = this.canvas.width / Math.max(1, this.canvas.height);
    const near = Math.max(0.1, this.orbit.distance * 0.005);
    const far = this.orbit.distance * 20 + 1000;

    const projection = perspective(Math.PI / 4, aspect, near, far);
    const view = lookAt(this.eyePosition(), this.target, [0, 0, 1]);

    return multiply(projection, view);
  };

  PointCloudViewer.prototype.render = function () {
    const gl = this.gl;

    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    if (!this.pointCount) {
      return;
    }

    const viewProjection = this.viewProjection();

    if (this.showGrid) {
      this.drawGuides(viewProjection);
    }

    gl.useProgram(this.pointProgram);

    const positionLocation = gl.getAttribLocation(this.pointProgram, "aPosition");
    const colorLocation = gl.getAttribLocation(this.pointProgram, "aColor");

    gl.bindBuffer(gl.ARRAY_BUFFER, this.positionBuffer);
    gl.enableVertexAttribArray(positionLocation);
    gl.vertexAttribPointer(positionLocation, 3, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.colorBuffer);
    gl.enableVertexAttribArray(colorLocation);
    gl.vertexAttribPointer(colorLocation, 3, gl.FLOAT, false, 0, 0);

    gl.uniformMatrix4fv(
      gl.getUniformLocation(this.pointProgram, "uViewProjection"),
      false,
      viewProjection
    );

    const ratio = window.devicePixelRatio || 1;

    gl.uniform1f(
      gl.getUniformLocation(this.pointProgram, "uPointSize"),
      this.pointSize * ratio
    );

    const modes = { uniform: 0, rgb: 1, height: 2 };

    gl.uniform1i(
      gl.getUniformLocation(this.pointProgram, "uColorMode"),
      modes[this.colorMode] === undefined ? 2 : modes[this.colorMode]
    );

    const minZ = this.bounds ? this.bounds.min[2] : 0;
    const maxZ = this.bounds ? this.bounds.max[2] : 1;

    gl.uniform1f(gl.getUniformLocation(this.pointProgram, "uHeightMin"), minZ);
    gl.uniform1f(
      gl.getUniformLocation(this.pointProgram, "uHeightRange"),
      Math.max(1e-6, maxZ - minZ)
    );

    gl.drawArrays(gl.POINTS, 0, this.pointCount);
  };

  PointCloudViewer.prototype.drawGuides = function (viewProjection) {
    const gl = this.gl;

    gl.useProgram(this.lineProgram);

    const positionLocation = gl.getAttribLocation(this.lineProgram, "aPosition");
    const colorLocation = gl.getAttribLocation(this.lineProgram, "aColor");

    gl.bindBuffer(gl.ARRAY_BUFFER, this.lineBuffer);
    gl.enableVertexAttribArray(positionLocation);
    gl.vertexAttribPointer(positionLocation, 3, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, this.lineColorBuffer);
    gl.enableVertexAttribArray(colorLocation);
    gl.vertexAttribPointer(colorLocation, 3, gl.FLOAT, false, 0, 0);

    gl.uniformMatrix4fv(
      gl.getUniformLocation(this.lineProgram, "uViewProjection"),
      false,
      viewProjection
    );

    gl.drawArrays(gl.LINES, 0, this.guideVertexCount);
  };

  global.PointCloudViewer = PointCloudViewer;
})(window);
