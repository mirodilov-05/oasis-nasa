/* TerraShift cinematic intro: fully programmatic, no video.
 *
 *  Layers (back to front): canvas-2D starfield, three.js Earth + satellite, canvas-2D
 *  field scan, copy/HUD. Timeline: orbit -> approach -> scan -> reveal.
 *  The overlay shell, Skip button and the finish() routine live in index.html, so the
 *  intro can always be dismissed even if this module or WebGL fails to load.
 */
const ctl = window.__intro;

async function start() {
  ctl.started = true;
  const root = document.getElementById("intro");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const T = TEXT[ctl.lang] || TEXT.en;
  const $ = (id) => document.getElementById(id);
  const copy = { kicker: $("intro-kicker"), title: $("intro-title"), line: $("intro-line"), enter: $("intro-enter") };
  const hud = $("intro-hud"), bar = root.querySelector(".intro-progress > span");
  const target = ctl.target; // {lat, lon} of the saved field, or a default
  copy.kicker.textContent = T.kicker;
  copy.enter.textContent = T.enter;
  $("intro-skip").textContent = T.skip;

  const quality = pickQuality();
  const stars = makeStars($("intro-stars"), quality);
  const fields = makeFields($("intro-fields"), T, quality);

  let THREE;
  try {
    THREE = await import("/vendor/three.module.min.js");
  } catch (e) {
    THREE = null;
  }
  if (!ctl.active) return; // skipped while loading
  const globe = THREE ? makeGlobe(THREE, $("intro-gl"), target, quality) : null;

  const PHASES = [
    { at: 0.0, title: "TerraShift", line: T.l1 },
    { at: 3.6, title: T.t2, line: T.l2 },
    { at: 7.2, title: T.t3, line: T.l3 },
    { at: 10.2, title: T.t4, line: T.l4, enter: true },
  ];
  const DURATION = 13.2;
  let phase = -1;
  const setPhase = (i) => {
    if (i === phase) return;
    phase = i;
    const p = PHASES[i];
    root.dataset.phase = String(i);
    swapText(copy.title, p.title);
    swapText(copy.line, p.line);
    if (p.enter) copy.enter.hidden = false;
  };

  const hudLine = (alt, scan) => {
    const ns = target.lat >= 0 ? "N" : "S", ew = target.lon >= 0 ? "E" : "W";
    hud.textContent = `${T.hud_target} ${Math.abs(target.lat).toFixed(2)}°${ns} ${Math.abs(target.lon).toFixed(2)}°${ew}` +
      `  ·  ${T.hud_alt} ${alt}` + (scan ? `  ·  ${scan}` : "");
  };

  if (reduced || !globe) {
    // One still frame, the final copy and the Enter button. Nothing moves.
    stars.draw(0, 0);
    if (globe) { globe.update(0.62, 0); globe.render(); } else { fields.show(1); fields.draw(1, 1); }
    setPhase(3);
    hudLine(globe ? "1 400 km" : "—", null);
    bar.style.transform = "scaleX(1)";
    copy.enter.focus({ preventScroll: true });
    ctl.onFinish(() => globe && globe.dispose());
    return;
  }

  // Compile shaders before the clock starts so the first second does not stutter.
  globe.update(0, 0);
  globe.render();
  await new Promise((r) => requestAnimationFrame(r));
  if (!ctl.active) { globe.dispose(); return; }

  let last = performance.now(), t = 0, raf = 0, frames = 0, slow = 0;
  const frame = (now) => {
    const dt = Math.min(0.25, (now - last) / 1000);
    last = now;
    if (!document.hidden) t += dt;
    // Adaptive quality: if the device cannot hold ~35 fps, lower the render resolution.
    frames++;
    if (dt > 0.028) slow++;
    if (frames === 45) { if (slow > 20) globe.degrade(); frames = 0; slow = 0; }

    const u = Math.min(1, t / DURATION);
    bar.style.transform = `scaleX(${u})`;
    let p = 0;
    for (let i = 0; i < PHASES.length; i++) if (t >= PHASES[i].at) p = i;
    setPhase(p);

    const approach = smooth(clamp01((t - 3.0) / 4.0));        // camera dives toward the field
    const dive = smooth(clamp01((t - 6.4) / 1.4));            // globe dissolves into the field view
    const scan = clamp01((t - 7.4) / 3.0);                    // scan line sweeps the parcels
    const settle = smooth(clamp01((t - 10.2) / 2.0));

    stars.draw(approach, t);
    if (dive < 1) { globe.update(approach, t); globe.render(); }
    globe.el.style.opacity = String(1 - dive);
    fields.show(dive);
    if (dive > 0) fields.draw(scan, settle, t);
    root.style.setProperty("--haze", String(Math.sin(Math.PI * dive) * 0.85));

    const altKm = Math.round(36000 * Math.pow(1 - approach, 3) + 1400 * (1 - dive) + 3 * (1 - settle));
    hudLine(`${altKm.toLocaleString("en-US").replace(/,/g, " ")} km`,
      dive > 0.5 ? `${T.hud_scan} ${Math.round(scan * 100)}%` : null);

    if (t >= DURATION) { ctl.finish(); return; }
    raf = requestAnimationFrame(frame);
  };
  raf = requestAnimationFrame(frame);
  ctl.seek = (s) => { t = s; }; // used by browser tests to check each phase
  ctl.onFinish(() => { cancelAnimationFrame(raf); globe.dispose(); });
}

/* ---------------- helpers ---------------- */

const clamp01 = (x) => Math.max(0, Math.min(1, x));
const smooth = (x) => x * x * (3 - 2 * x);

function swapText(el, text) {
  if (el.textContent === text) return;
  el.classList.remove("intro-in");
  void el.offsetWidth; // restart the CSS animation
  el.textContent = text;
  el.classList.add("intro-in");
}

function pickQuality() {
  const small = Math.min(innerWidth, innerHeight) < 600;
  const cores = navigator.hardwareConcurrency || 4;
  const mem = navigator.deviceMemory || 4;
  const saveData = navigator.connection && navigator.connection.saveData;
  const low = saveData || cores <= 4 || mem <= 2 || small;
  return {
    low,
    dpr: Math.min(window.devicePixelRatio || 1, low ? 1.25 : 2),
    segments: low ? 72 : 128,
    octaves: low ? 4 : 6,
    stars: low ? 260 : 650,
  };
}

/* ---------- starfield (canvas 2D) ---------- */

function makeStars(canvas, q) {
  const ctx = canvas.getContext("2d");
  const off = document.createElement("canvas");
  let w = 0, h = 0, dpr = 1, twinkle = [];
  const rand = mulberry32(7);
  const resize = () => {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = canvas.clientWidth; h = canvas.clientHeight;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    // Static layer is 10 % larger than the screen so it can drift (parallax).
    off.width = Math.round(w * 1.1 * dpr); off.height = Math.round(h * 1.1 * dpr);
    const o = off.getContext("2d");
    o.fillStyle = "#02040a"; o.fillRect(0, 0, off.width, off.height);
    // A faint galactic band
    const g = o.createLinearGradient(0, off.height * 0.1, off.width, off.height * 0.9);
    g.addColorStop(0, "rgba(70,90,160,0)"); g.addColorStop(0.5, "rgba(90,110,190,0.10)"); g.addColorStop(1, "rgba(70,90,160,0)");
    o.fillStyle = g; o.fillRect(0, 0, off.width, off.height);
    twinkle = [];
    for (let i = 0; i < q.stars; i++) {
      const x = rand() * off.width, y = rand() * off.height, r = (rand() ** 3 * 1.4 + 0.3) * dpr;
      const warm = rand();
      const col = warm > 0.85 ? "255,214,170" : warm < 0.15 ? "170,200,255" : "235,240,255";
      if (i % 9 === 0) twinkle.push({ x, y, r: r * 1.2, col, ph: rand() * 6.28, sp: 1 + rand() * 2 });
      else { o.fillStyle = `rgba(${col},${0.35 + rand() * 0.6})`; o.beginPath(); o.arc(x, y, r, 0, 6.283); o.fill(); }
    }
  };
  resize();
  addEventListener("resize", resize);
  return {
    draw(approach, t) {
      const dx = -approach * w * 0.06 * dpr, dy = -approach * h * 0.04 * dpr;
      ctx.drawImage(off, dx, dy);
      for (const s of twinkle) {
        const a = 0.45 + 0.55 * Math.sin(t * s.sp + s.ph) ** 2;
        ctx.fillStyle = `rgba(${s.col},${a})`;
        ctx.beginPath(); ctx.arc(s.x + dx, s.y + dy, s.r, 0, 6.283); ctx.fill();
      }
    },
  };
}

/* ---------- Earth + satellite (three.js) ---------- */

function makeGlobe(THREE, host, target, q) {
  const renderer = new THREE.WebGLRenderer({ antialias: !q.low, alpha: true, powerPreference: "high-performance" });
  let dpr = q.dpr;
  renderer.setPixelRatio(dpr);
  renderer.setClearColor(0x000000, 0);
  host.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 100);
  const resize = () => {
    const w = host.clientWidth, h = host.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // Keep the whole globe in view on tall phone screens.
    camera.fov = w / h < 0.8 ? 58 : 40;
    camera.updateProjectionMatrix();
  };
  resize();
  addEventListener("resize", resize);

  const sun = new THREE.Vector3(-0.55, 0.35, 0.75).normalize();
  const earth = new THREE.Group();
  earth.rotation.z = THREE.MathUtils.degToRad(-23.4) * 0.5;
  scene.add(earth);

  // lat/lon -> object-space unit vector (y up, lon 0 facing +z)
  const toVec = (lat, lon) => {
    const la = THREE.MathUtils.degToRad(lat), lo = THREE.MathUtils.degToRad(lon);
    return new THREE.Vector3(Math.cos(la) * Math.sin(lo), Math.sin(la), Math.cos(la) * Math.cos(lo));
  };
  const targetLocal = toVec(target.lat, target.lon);

  const uniforms = {
    uSun: { value: sun.clone() },
    uTarget: { value: targetLocal.clone() },
    uDetail: { value: 0 },
    uTime: { value: 0 },
    uScan: { value: 0 },
  };
  const earthMat = new THREE.ShaderMaterial({
    uniforms,
    defines: { OCTAVES: q.octaves },
    vertexShader: /* glsl */`
      varying vec3 vObj; varying vec3 vNormalW; varying vec3 vViewDir;
      void main() {
        vObj = normalize(position);
        vec4 wp = modelMatrix * vec4(position, 1.0);
        vNormalW = normalize(mat3(modelMatrix) * normal);
        vViewDir = normalize(cameraPosition - wp.xyz);
        gl_Position = projectionMatrix * viewMatrix * wp;
      }`,
    fragmentShader: /* glsl */`
      uniform vec3 uSun; uniform vec3 uTarget; uniform float uDetail; uniform float uTime; uniform float uScan;
      varying vec3 vObj; varying vec3 vNormalW; varying vec3 vViewDir;
      ${NOISE_GLSL}
      void main() {
        vec3 n = vObj;
        float d = distance(n, uTarget);
        // Continents from fractal noise; a guaranteed landmass under the chosen field.
        float h = fbm(n * 2.1) + 0.32 * exp(-d * d / 0.03) - 0.02 * abs(n.y);
        float land = smoothstep(0.505, 0.525, h);
        float relief = fbm(n * 9.0 + 3.1);
        vec3 deep = vec3(0.01, 0.05, 0.16), shallow = vec3(0.03, 0.22, 0.38);
        vec3 ocean = mix(deep, shallow, smoothstep(0.38, 0.505, h));
        float arid = smoothstep(0.45, 0.7, fbm(n * 3.3 + 11.0)) * (1.0 - smoothstep(0.55, 0.85, abs(n.y)));
        vec3 green = mix(vec3(0.10, 0.24, 0.09), vec3(0.20, 0.33, 0.12), relief);
        vec3 desert = mix(vec3(0.52, 0.42, 0.27), vec3(0.66, 0.55, 0.38), relief);
        vec3 ground = mix(green, desert, arid);
        ground = mix(ground, vec3(0.93, 0.95, 0.98), smoothstep(0.80, 0.9, abs(n.y) + relief * 0.08));
        vec3 col = mix(ocean, ground, land);

        // Parcel grid around the field, revealed as the camera dives in.
        vec3 t1 = normalize(cross(vec3(0.0, 1.0, 0.0), uTarget));
        vec3 t2 = cross(uTarget, t1);
        vec2 p = vec2(dot(n, t1), dot(n, t2)) * 140.0;
        vec2 cell = floor(p + vec2(hash3(vec3(floor(p.y), 1.0, 2.0)) * 0.6, 0.0));
        float rnd = hash3(vec3(cell, 4.0));
        vec3 crop = rnd < 0.33 ? vec3(0.22, 0.42, 0.12) : rnd < 0.6 ? vec3(0.55, 0.5, 0.22) : rnd < 0.85 ? vec3(0.13, 0.32, 0.10) : vec3(0.42, 0.32, 0.22);
        vec2 f = fract(p);
        float edge = 1.0 - smoothstep(0.0, 0.06, min(min(f.x, 1.0 - f.x), min(f.y, 1.0 - f.y)));
        float fieldPatch = (1.0 - smoothstep(0.035, 0.09, d)) * uDetail * land;
        col = mix(col, mix(crop, vec3(0.75, 0.72, 0.6), edge * 0.6), fieldPatch);
        // Scan footprint ring
        float ring = (1.0 - smoothstep(0.0, 0.004, abs(d - 0.02 - 0.02 * fract(uTime * 0.6)))) * uScan;
        col += vec3(0.2, 0.75, 1.0) * ring;

        vec3 N = normalize(vNormalW);
        float diff = dot(N, uSun);
        float lit = smoothstep(-0.15, 0.35, diff);
        vec3 day = col * (0.08 + 1.1 * max(diff, 0.0));
        // Sun glint on water
        vec3 H = normalize(uSun + vViewDir);
        day += vec3(1.0, 0.9, 0.75) * pow(max(dot(N, H), 0.0), 60.0) * (1.0 - land) * 0.6;
        // Night side: settlements as warm specks on land
        float city = step(0.86, hash3(floor(n * 260.0))) * land * smoothstep(0.45, 0.6, fbm(n * 6.0));
        vec3 night = col * 0.02 + vec3(1.0, 0.72, 0.38) * city * 0.9;
        vec3 outc = mix(night, day, lit);
        // Atmospheric rim inside the disc
        float rim = pow(1.0 - max(dot(N, vViewDir), 0.0), 3.0);
        outc += vec3(0.25, 0.55, 1.0) * rim * (0.25 + 0.75 * lit);
        gl_FragColor = vec4(outc, 1.0);
      }`,
  });
  const sphere = new THREE.SphereGeometry(1, q.segments, Math.round(q.segments / 2));
  earth.add(new THREE.Mesh(sphere, earthMat));

  // Cloud shell
  const cloudMat = new THREE.ShaderMaterial({
    uniforms: { uSun: uniforms.uSun, uTime: uniforms.uTime, uFade: { value: 1 } },
    defines: { OCTAVES: Math.max(3, q.octaves - 1) },
    transparent: true, depthWrite: false,
    vertexShader: /* glsl */`
      varying vec3 vObj; varying vec3 vN;
      void main() { vObj = normalize(position); vN = normalize(mat3(modelMatrix) * normal);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */`
      uniform vec3 uSun; uniform float uTime; uniform float uFade; varying vec3 vObj; varying vec3 vN;
      ${NOISE_GLSL}
      void main() {
        float c = fbm(vObj * 3.0 + vec3(uTime * 0.01, 0.0, 0.0));
        float a = smoothstep(0.52, 0.75, c) * 0.75 * uFade;
        float lit = smoothstep(-0.1, 0.4, dot(normalize(vN), uSun));
        gl_FragColor = vec4(vec3(0.95) * (0.05 + lit), a * (0.15 + 0.85 * lit));
      }`,
  });
  const clouds = new THREE.Mesh(new THREE.SphereGeometry(1.012, q.segments, Math.round(q.segments / 2)), cloudMat);
  earth.add(clouds);

  // Atmosphere halo (back faces, additive)
  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(1.16, 64, 32),
    new THREE.ShaderMaterial({
      uniforms: { uSun: uniforms.uSun },
      side: THREE.BackSide, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
      vertexShader: /* glsl */`
        varying vec3 vN; varying vec3 vV;
        void main() { vec4 wp = modelMatrix * vec4(position, 1.0); vN = normalize(mat3(modelMatrix) * normal);
          vV = normalize(cameraPosition - wp.xyz); gl_Position = projectionMatrix * viewMatrix * wp; }`,
      fragmentShader: /* glsl */`
        uniform vec3 uSun; varying vec3 vN; varying vec3 vV;
        void main() {
          float k = pow(max(0.0, 0.72 + dot(vN, vV)), 3.2);
          float day = 0.25 + 0.75 * smoothstep(-0.4, 0.5, dot(-vN, uSun));
          gl_FragColor = vec4(vec3(0.3, 0.6, 1.0) * k * day, k * day);
        }`,
    }),
  );
  scene.add(halo);

  // Satellite: bus, solar wings, dish, blinking beacon
  const sat = new THREE.Group();
  const gold = new THREE.MeshStandardMaterial({ color: 0xc9a34a, metalness: 0.8, roughness: 0.35 });
  const panelMat = new THREE.MeshStandardMaterial({ color: 0x1b3d8f, metalness: 0.5, roughness: 0.3, emissive: 0x0a1a44 });
  const frameMat = new THREE.MeshStandardMaterial({ color: 0xb8c0cc, metalness: 0.9, roughness: 0.4 });
  sat.add(new THREE.Mesh(new THREE.BoxGeometry(0.032, 0.032, 0.05), gold));
  for (const s of [-1, 1]) {
    const wing = new THREE.Mesh(new THREE.BoxGeometry(0.11, 0.002, 0.036), panelMat);
    wing.position.x = s * 0.075;
    sat.add(wing);
    const strut = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.003, 0.003), frameMat);
    strut.position.x = s * 0.02;
    sat.add(strut);
  }
  const dish = new THREE.Mesh(new THREE.ConeGeometry(0.014, 0.01, 16, 1, true), frameMat);
  dish.position.z = -0.03; dish.rotation.x = -Math.PI / 2;
  sat.add(dish);
  const beacon = new THREE.Mesh(new THREE.SphereGeometry(0.004, 8, 8), new THREE.MeshBasicMaterial({ color: 0xff5544 }));
  beacon.position.y = 0.02;
  sat.add(beacon);
  sat.scale.setScalar(0.9);
  scene.add(sat);
  scene.add(new THREE.AmbientLight(0x8899bb, 0.6));
  const light = new THREE.DirectionalLight(0xffffff, 2.2);
  light.position.copy(sun).multiplyScalar(5);
  scene.add(light);

  // Inclined orbit path
  const ORBIT_R = 1.42, incl = THREE.MathUtils.degToRad(52);
  const orbitAt = (a) => new THREE.Vector3(Math.cos(a) * ORBIT_R, Math.sin(a) * Math.sin(incl) * ORBIT_R, Math.sin(a) * Math.cos(incl) * ORBIT_R);
  const pts = [];
  for (let i = 0; i <= 160; i++) pts.push(orbitAt((i / 160) * Math.PI * 2));
  const orbitMat = new THREE.LineBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.35 });
  const orbit = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), orbitMat);
  scene.add(orbit);

  // Scan beam: an open cone from the satellite to the ground, stripes moving down
  const beamMat = new THREE.ShaderMaterial({
    uniforms: { uTime: uniforms.uTime, uAlpha: { value: 0 } },
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
    vertexShader: /* glsl */`varying float vY; void main(){ vY = position.y + 0.5; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
    fragmentShader: /* glsl */`
      uniform float uTime; uniform float uAlpha; varying float vY;
      void main(){ float s = 0.55 + 0.45 * sin(vY * 40.0 + uTime * 8.0);
        gl_FragColor = vec4(0.25, 0.8, 1.0, uAlpha * s * (0.15 + 0.6 * (1.0 - vY))); }`,
  });
  const beam = new THREE.Mesh(new THREE.ConeGeometry(1, 1, 32, 1, true), beamMat);
  scene.add(beam);

  const tmp = new THREE.Vector3(), camDir0 = new THREE.Vector3(0.28, 0.3, 1).normalize();
  const up = new THREE.Vector3(0, 1, 0), lookAt = new THREE.Vector3();
  const baseRot = -THREE.MathUtils.degToRad(target.lon) - 0.9; // the field rotates into view during the approach

  return {
    el: renderer.domElement,
    update(approach, t) {
      uniforms.uTime.value = t;
      // Spin slows to a stop as the camera commits to the field.
      earth.rotation.y = baseRot + 0.9 * smoothstep01(Math.min(1, t / 7)) + 0.03 * t * (1 - approach);
      clouds.rotation.y = t * 0.012;
      earth.updateMatrixWorld();
      const tgtW = targetLocal.clone().applyMatrix4(earth.matrixWorld).normalize();

      // Camera: slerp from a wide orbit view to a point above the field.
      const dir = camDir0.clone().lerp(tgtW, approach).normalize();
      const dist = 3.6 - 2.32 * approach;
      camera.position.copy(dir).multiplyScalar(dist);
      lookAt.copy(tgtW).multiplyScalar(0.95 * approach);
      camera.up.copy(up);
      camera.lookAt(lookAt);

      // Satellite: free orbit, then it is guided to a point above the field.
      const free = orbitAt(t * 0.42 + 0.6);
      const guide = smoothstep01(Math.min(1, Math.max(0, (approach - 0.15) / 0.6)));
      const over = tgtW.clone().multiplyScalar(1.1).add(tmp.copy(dir).cross(up).normalize().multiplyScalar(0.11)).add(up.clone().multiplyScalar(0.05));
      sat.position.copy(free.lerp(over, guide));
      sat.lookAt(0, 0, 0);
      beacon.visible = Math.sin(t * 6) > 0.6;
      orbitMat.opacity = 0.35 * (1 - guide);

      // Beam and the grid on the ground
      const beamOn = smoothstep01(Math.min(1, Math.max(0, (approach - 0.6) / 0.3)));
      const ground = tgtW.clone();
      const mid = sat.position.clone().add(ground).multiplyScalar(0.5);
      const len = sat.position.distanceTo(ground);
      beam.position.copy(mid);
      beam.quaternion.setFromUnitVectors(up, sat.position.clone().sub(ground).normalize());
      beam.scale.set(0.022, len, 0.022);
      beamMat.uniforms.uAlpha.value = beamOn;
      uniforms.uScan.value = beamOn;
      uniforms.uDetail.value = smoothstep01(Math.min(1, Math.max(0, (approach - 0.45) / 0.45)));
      cloudMat.uniforms.uFade.value = 1 - 0.85 * approach;
    },
    render() { renderer.render(scene, camera); },
    degrade() {
      if (dpr <= 0.75) return;
      dpr = dpr > 1 ? 1 : 0.75;
      renderer.setPixelRatio(dpr);
      resize();
    },
    dispose() {
      removeEventListener("resize", resize);
      scene.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
      renderer.dispose();
      renderer.forceContextLoss();
    },
  };
}

function smoothstep01(x) { return x * x * (3 - 2 * x); }

/* ---------- field scan (canvas 2D) ---------- */

function makeFields(canvas, T, q) {
  const ctx = canvas.getContext("2d");
  let w = 0, h = 0, dpr = 1, raw = null, ndvi = null, parcels = [], mine = null;
  const build = () => {
    dpr = Math.min(window.devicePixelRatio || 1, q.low ? 1.25 : 2);
    w = canvas.clientWidth; h = canvas.clientHeight;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    const rand = mulberry32(42);
    // Recursive subdivision of a large rotated block into parcels
    const S = Math.hypot(w, h) * 1.35;
    parcels = [];
    const split = (x, y, pw, ph, depth) => {
      if (depth > 8 || (depth > 4 && rand() < 0.25) || pw * ph < S * S / 700) {
        parcels.push({ x, y, w: pw, h: ph, crop: Math.floor(rand() * 5), v: rand(), rows: rand() < 0.5 });
        return;
      }
      const cut = 0.35 + rand() * 0.3;
      if (pw > ph) { split(x, y, pw * cut, ph, depth + 1); split(x + pw * cut, y, pw * (1 - cut), ph, depth + 1); }
      else { split(x, y, pw, ph * cut, depth + 1); split(x, y + ph * cut, pw, ph * (1 - cut), depth + 1); }
    };
    split(-S / 2, -S / 2, S, S, 0);
    // "Your field": the parcel closest to the centre with a reasonable size
    mine = parcels.reduce((best, p) => {
      const cx = p.x + p.w / 2, cy = p.y + p.h / 2, d = Math.hypot(cx, cy) - Math.min(p.w, p.h) * 0.3;
      return !best || d < best.d ? { p, d } : best;
    }, null).p;
    mine.crop = 1; mine.v = 0.62; // a healthy irrigated crop
    raw = paint(false); ndvi = paint(true);
  };
  const BASE = [[150, 132, 92], [92, 112, 64], [120, 104, 70], [70, 96, 58], [160, 140, 104]];
  const ramp = (v) => { // NDVI-like colour ramp: bare soil red -> yellow -> dense green
    const stops = [[0, [165, 0, 38]], [0.3, [244, 109, 67]], [0.5, [254, 224, 139]], [0.7, [166, 217, 106]], [1, [26, 152, 80]]];
    for (let i = 1; i < stops.length; i++) if (v <= stops[i][0]) {
      const [a, ca] = stops[i - 1], [b, cb] = stops[i], k = (v - a) / (b - a);
      return ca.map((c, j) => Math.round(c + (cb[j] - c) * k));
    }
    return stops[stops.length - 1][1];
  };
  const paint = (isNdvi) => {
    const c = document.createElement("canvas");
    c.width = canvas.width; c.height = canvas.height;
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, w / 2 * dpr, h / 2 * dpr);
    g.rotate(-0.21);
    g.fillStyle = isNdvi ? "#3a1020" : "#5b5240";
    g.fillRect(-w * 2, -h * 2, w * 4, h * 4);
    for (const p of parcels) {
      const col = isNdvi ? ramp(p.crop === 4 ? 0.15 + p.v * 0.25 : 0.5 + p.v * 0.5) : BASE[p.crop];
      g.fillStyle = `rgb(${col.join(",")})`;
      g.fillRect(p.x + 2, p.y + 2, p.w - 4, p.h - 4);
      g.strokeStyle = isNdvi ? "rgba(0,0,0,.18)" : "rgba(30,24,14,.22)";
      g.lineWidth = 1;
      g.beginPath();
      const step = 6 + p.v * 5;
      if (p.rows) for (let x = p.x + 4; x < p.x + p.w - 3; x += step) { g.moveTo(x, p.y + 3); g.lineTo(x, p.y + p.h - 3); }
      else for (let y = p.y + 4; y < p.y + p.h - 3; y += step) { g.moveTo(p.x + 3, y); g.lineTo(p.x + p.w - 3, y); }
      g.stroke();
    }
    // Irrigation canal
    g.strokeStyle = isNdvi ? "rgba(40,120,200,.9)" : "rgba(60,110,140,.9)";
    g.lineWidth = 5;
    g.beginPath(); g.moveTo(-w * 2, h * 0.18); g.bezierCurveTo(-w * 0.3, h * 0.05, w * 0.2, h * 0.32, w * 2, h * 0.12); g.stroke();
    return c;
  };
  build();
  addEventListener("resize", build);

  return {
    show(a) { canvas.style.opacity = String(a); },
    draw(scan, settle, t = 0) {
      const W = canvas.width, H = canvas.height;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      // Slow descent: zoom from 1.35 to 1.0
      const z = 1.18 - 0.18 * Math.min(1, scan * 0.9 + settle * 0.1);
      ctx.save();
      ctx.translate(W / 2, H / 2); ctx.scale(z, z); ctx.translate(-W / 2, -H / 2);
      ctx.drawImage(raw, 0, 0);
      const sx = scan * W * 1.1 - W * 0.05;
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, Math.max(0, sx), H); ctx.clip();
      ctx.globalAlpha = 0.92;
      ctx.drawImage(ndvi, 0, 0);
      ctx.restore();
      ctx.restore();

      // Scan line
      if (scan > 0 && scan < 1) {
        const g = ctx.createLinearGradient(sx - 60 * dpr, 0, sx + 4 * dpr, 0);
        g.addColorStop(0, "rgba(56,189,248,0)"); g.addColorStop(1, "rgba(56,189,248,.55)");
        ctx.fillStyle = g; ctx.fillRect(sx - 60 * dpr, 0, 64 * dpr, H);
        ctx.fillStyle = "rgba(200,240,255,.95)"; ctx.fillRect(sx, 0, 2 * dpr, H);
      }

      // The user's field: animated dashed outline + readout once the scan has passed it
      ctx.save();
      ctx.setTransform(dpr * z, 0, 0, dpr * z, W / 2, H / 2);
      ctx.rotate(-0.21);
      const p = mine;
      ctx.lineWidth = 2.5 / z;
      ctx.setLineDash([10, 7]); ctx.lineDashOffset = -t * 30;
      ctx.strokeStyle = "rgba(56,189,248,.95)";
      ctx.strokeRect(p.x + 2, p.y + 2, p.w - 4, p.h - 4);
      ctx.restore();
      const reveal = Math.min(1, Math.max(0, (scan - 0.55) / 0.2));
      if (reveal > 0) {
        const fs = Math.max(11, Math.min(14, w / 60)) * dpr;
        ctx.font = `600 ${fs}px ui-monospace, Consolas, monospace`;
        const lines = [T.f_field, `NDVI ${(0.5 + mine.v * 0.5).toFixed(2)}`, T.f_et, T.f_rot];
        const bw = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 20 * dpr, bh = lines.length * fs * 1.45 + 14 * dpr;
        const bx = Math.min(W - bw - 16 * dpr, W * 0.58), by = Math.max(16 * dpr, H * 0.2);
        ctx.globalAlpha = reveal;
        ctx.fillStyle = "rgba(6,12,24,.82)"; ctx.strokeStyle = "rgba(56,189,248,.7)"; ctx.lineWidth = dpr;
        ctx.fillRect(bx, by, bw, bh); ctx.strokeRect(bx, by, bw, bh);
        ctx.fillStyle = "#e6ecf5";
        lines.forEach((l, i) => { ctx.fillStyle = i === 0 ? "#38bdf8" : "#e6ecf5"; ctx.fillText(l, bx + 10 * dpr, by + 10 * dpr + fs * (i + 1) * 1.3); });
        ctx.globalAlpha = 1;
      }
    },
  };
}

function mulberry32(a) {
  return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

const NOISE_GLSL = /* glsl */`
  float hash3(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
  float vnoise(vec3 x) {
    vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash3(i), hash3(i + vec3(1,0,0)), f.x), mix(hash3(i + vec3(0,1,0)), hash3(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(hash3(i + vec3(0,0,1)), hash3(i + vec3(1,0,1)), f.x), mix(hash3(i + vec3(0,1,1)), hash3(i + vec3(1,1,1)), f.x), f.y), f.z);
  }
  float fbm(vec3 p) { float a = 0.5, s = 0.0; for (int i = 0; i < OCTAVES; i++) { s += a * vnoise(p); p = p * 2.03 + vec3(1.7, 9.2, 3.1); a *= 0.5; } return s / (1.0 - pow(0.5, float(OCTAVES))); }
`;

const TEXT = {
  en: {
    kicker: "NASA POWER · GIBS · SRTM · HLS", skip: "Skip intro", enter: "Open the platform",
    l1: "35 years of NASA climate records, read from orbit.",
    t2: "A pass over your field", l2: "Heat, rain, soil water and vegetation — measured by satellites, not guessed.",
    t3: "Every parcel, scanned", l3: "Water need, heat and frost stress, and the rotation that wastes the least water.",
    t4: "Your field, measured from orbit.", l4: "Pick a place on the map to start the analysis.",
    hud_target: "TARGET", hud_alt: "VIEW ALT", hud_scan: "SCAN",
    f_field: "YOUR FIELD", f_et: "ETc  FAO-56", f_rot: "ROTATION  ranked",
  },
  uz: {
    kicker: "NASA POWER · GIBS · SRTM · HLS", skip: "Kirishni o'tkazib yuborish", enter: "Platformani ochish",
    l1: "35 yillik NASA iqlim ma'lumotlari — orbitadan.",
    t2: "Dalangiz ustidan o'tish", l2: "Issiqlik, yomg'ir, tuproq namligi va o'simliklar — taxmin emas, sun'iy yo'ldosh o'lchovi.",
    t3: "Har bir dala skanerlandi", l3: "Suv ehtiyoji, issiqlik va sovuq xavfi, eng kam suv sarflaydigan almashlab ekish.",
    t4: "Dalangiz — orbitadan o'lchangan.", l4: "Tahlilni boshlash uchun xaritada joyni tanlang.",
    hud_target: "NISHON", hud_alt: "BALANDLIK", hud_scan: "SKANER",
    f_field: "SIZNING DALANGIZ", f_et: "ETc  FAO-56", f_rot: "ROTATSIYA  tartiblandi",
  },
  ru: {
    kicker: "NASA POWER · GIBS · SRTM · HLS", skip: "Пропустить интро", enter: "Открыть платформу",
    l1: "35 лет климатических данных NASA — с орбиты.",
    t2: "Пролёт над вашим полем", l2: "Жара, осадки, влага почвы и растительность — измерены спутниками, а не угаданы.",
    t3: "Каждый участок просканирован", l3: "Потребность в воде, тепловой и морозный стресс и севооборот с наименьшим расходом воды.",
    t4: "Ваше поле, измеренное с орбиты.", l4: "Выберите место на карте, чтобы начать анализ.",
    hud_target: "ЦЕЛЬ", hud_alt: "ВЫСОТА", hud_scan: "СКАН",
    f_field: "ВАШЕ ПОЛЕ", f_et: "ETc  FAO-56", f_rot: "СЕВООБОРОТ  ранжирован",
  },
};

// Started last so every const above is initialised.
if (ctl && ctl.active) start().catch((err) => { console.warn("intro:", err); ctl.fallback(); });
