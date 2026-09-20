"use strict";
const data = JSON.parse(document.getElementById("data").textContent);
const grid = document.getElementById("grid");
const query = document.getElementById("q");
const picker = document.getElementById("version-picker");
const pickerButton = document.getElementById("version-button");
const pickerValue = document.getElementById("version-value");
const pickerMenu = document.getElementById("version-menu");
// "" means every version. The custom listbox writes this; draw() reads it.
let version = "";
const countLabel = document.getElementById("count");
const clearButton = document.getElementById("clear");
const newest = data.mod.minecraft;
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
let shown = data.variants;

/* ------------------------------------------------------------- versions */

// Dotted numbers, compared component by component and padded to four, so 1.20.3
// beats 1.20 and the 26.x renumbering still beats every 1.x. The renderer sorts
// the manifest with the same rule, newest first.
function versionKey(version) {
  const parts = String(version).split(".").map(Number);
  if (!version || parts.some(Number.isNaN)) return null;
  while (parts.length < 4) parts.push(0);
  return parts.slice(0, 4);
}
function compareVersions(a, b) {
  const ka = versionKey(a), kb = versionKey(b);
  if (!ka || !kb) return 0;
  for (let i = 0; i < 4; i++) if (ka[i] !== kb[i]) return ka[i] - kb[i];
  return 0;
}

// Newest first, matching the grid. A mod with no enum to read version markers from
// has no `since` on any variant, and then the picker is not shown at all.
const versions = [...new Set(data.variants.map(v => v.since).filter(Boolean))]
  .sort((a, b) => compareVersions(b, a));

// How many pots a given version can show, counting everything added up to it. The same
// cumulative rule the filter applies, so the tally always matches what you get.
const reach = v => data.variants.filter(x => x.since && compareVersions(x.since, v) <= 0).length;

if (versions.length > 1) {
  picker.hidden = false;
  const row = (value, label, tally) =>
    `<div class="picker-option" role="option" tabindex="-1" data-value="${esc(value)}"` +
    ` aria-selected="${value === version}">` +
      `<span class="label">${esc(label)}</span>` +
      `<span class="tally">${tally}</span>` +
      `<svg class="tick" width="16" height="16" aria-hidden="true"><use href="#i-check"/></svg>` +
    `</div>`;
  pickerMenu.innerHTML =
    row("", "All versions", data.variants.length) +
    '<div class="picker-sep" role="presentation"></div>' +
    versions.map(v => row(v, "Minecraft " + v, reach(v))).join("");
}

/* ------------------------------------------------------- version listbox */

const options = () => [...pickerMenu.querySelectorAll(".picker-option")];

// Right-aligned under the trigger, but never past the edge of the viewport. The menu is
// `width: max-content` and the trigger can be much narrower than it, so a plain
// `right: 0` sent the menu's left edge off-screen on a phone -- measured at -28px in a
// 380px viewport. Clamping beats a breakpoint here: the trigger's position depends on
// the search field and the count beside it, not on the viewport width alone.
function placeMenu(anchor, menu) {
  const gutter = parseFloat(
    getComputedStyle(document.documentElement).getPropertyValue("--menu-gutter")) || 12;
  menu.style.left = "0px";
  const box = anchor.getBoundingClientRect();
  const width = menu.offsetWidth;
  const preferred = box.width - width;
  const min = gutter - box.left;
  const max = innerWidth - gutter - width - box.left;
  // max can fall below min only if the menu is wider than the gutters allow, and
  // max-width already prevents that; Math.max last keeps the left edge on screen.
  menu.style.left = Math.round(Math.max(min, Math.min(preferred, max))) + "px";
}

function openMenu(focus) {
  if (!pickerMenu.hidden) return;
  pickerMenu.hidden = false;
  pickerButton.setAttribute("aria-expanded", "true");
  placeMenu(picker, pickerMenu);
  const list = options();
  const target = list.find(o => o.dataset.value === version) || list[0];
  if (focus && target) target.focus();
  // Keep the checked row in view when the list is long enough to scroll.
  if (target) target.scrollIntoView({ block: "nearest" });
}

function closeMenu(restore) {
  if (pickerMenu.hidden) return;
  pickerMenu.hidden = true;
  pickerButton.setAttribute("aria-expanded", "false");
  if (restore) pickerButton.focus();
}

function pick(value) {
  version = value;
  const chosen = options().find(o => o.dataset.value === value);
  pickerValue.textContent = chosen ? chosen.querySelector("span").textContent : "All versions";
  options().forEach(o => o.setAttribute("aria-selected", String(o.dataset.value === value)));
  closeMenu(true);
  draw();
}

function neighbour(list, from, delta) {
  const at = list.indexOf(from);
  return list[(at + delta + list.length) % list.length];
}

pickerButton.addEventListener("click", () => {
  pickerMenu.hidden ? openMenu(true) : closeMenu(false);
});
pickerButton.addEventListener("keydown", event => {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    openMenu(true);
  }
});
pickerMenu.addEventListener("click", event => {
  const option = event.target.closest(".picker-option");
  if (option) pick(option.dataset.value);
});
pickerMenu.addEventListener("keydown", event => {
  const list = options();
  const active = document.activeElement.closest(".picker-option");
  if (event.key === "ArrowDown") { event.preventDefault(); neighbour(list, active, 1).focus(); }
  else if (event.key === "ArrowUp") { event.preventDefault(); neighbour(list, active, -1).focus(); }
  else if (event.key === "Home") { event.preventDefault(); list[0].focus(); }
  else if (event.key === "End") { event.preventDefault(); list[list.length - 1].focus(); }
  else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    if (active) pick(active.dataset.value);
  }
  else if (event.key === "Escape") { event.preventDefault(); closeMenu(true); }
  else if (event.key === "Tab") closeMenu(false);
});
// A click anywhere else dismisses it, the way a native menu does.
addEventListener("pointerdown", event => {
  if (!pickerMenu.hidden && !picker.contains(event.target)) closeMenu(false);
});
// The trigger moves when the toolbar reflows, so an open menu has to follow it.
addEventListener("resize", () => { if (!pickerMenu.hidden) placeMenu(picker, pickerMenu); });

/* --------------------------------------------------------------- downloads */

// The store links, when the mod's gallery.json declares any. The whole control
// is absent otherwise, so every reference here has to tolerate a missing node --
// PottedDelight shipped without downloads while NotEnoughPots had them.
const downloads = document.getElementById("downloads");
if (downloads) {
  const downloadButton = document.getElementById("downloads-button");
  const downloadMenu = document.getElementById("downloads-menu");
  const links = () => [...downloadMenu.querySelectorAll(".menu-link")];

  const openDownloads = focus => {
    if (!downloadMenu.hidden) return;
    downloadMenu.hidden = false;
    downloadButton.setAttribute("aria-expanded", "true");
    placeMenu(downloads, downloadMenu);
    if (focus) links()[0].focus();
  };
  const closeDownloads = restore => {
    if (downloadMenu.hidden) return;
    downloadMenu.hidden = true;
    downloadButton.setAttribute("aria-expanded", "false");
    if (restore) downloadButton.focus();
  };

  downloadButton.addEventListener("click", () => {
    downloadMenu.hidden ? openDownloads(true) : closeDownloads(false);
  });
  downloadButton.addEventListener("keydown", event => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openDownloads(true);
    }
  });
  downloadMenu.addEventListener("keydown", event => {
    const list = links();
    const active = document.activeElement.closest(".menu-link");
    if (event.key === "ArrowDown") { event.preventDefault(); neighbour(list, active, 1).focus(); }
    else if (event.key === "ArrowUp") { event.preventDefault(); neighbour(list, active, -1).focus(); }
    else if (event.key === "Escape") { event.preventDefault(); closeDownloads(true); }
    else if (event.key === "Tab") closeDownloads(false);
  });
  // Following a link leaves the menu open behind the new tab otherwise.
  downloadMenu.addEventListener("click", () => closeDownloads(false));
  addEventListener("pointerdown", event => {
    if (!downloadMenu.hidden && !downloads.contains(event.target)) closeDownloads(false);
  });
  addEventListener("resize", () => {
    if (!downloadMenu.hidden) placeMenu(downloads, downloadMenu);
  });
}

/* ---------------------------------------------------------------- grid */

// Every term has to match somewhere, with underscores read as spaces, so "dead coral"
// finds potted_dead_tube_coral and "26.3" finds what is new in this version.
const haystack = v => `${v.name} ${v.label} ${v.item} ${v.since}`.replace(/_/g, " ").toLowerCase();
function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function draw() {
  const terms = query.value.toLowerCase().split(/\s+/).filter(Boolean);
  // A version picks everything playable *in* that version, not everything added
  // in it -- a pot from 1.13 still exists in 1.21, so the filter is cumulative.
  const ceiling = version;
  shown = data.variants.filter(v => {
    if (ceiling && (!v.since || compareVersions(v.since, ceiling) > 0)) return false;
    const hay = haystack(v);
    return terms.every(t => hay.includes(t));
  });

  clearButton.hidden = !query.value;
  countLabel.textContent = shown.length === data.variants.length
    ? `${shown.length}`
    : `${shown.length} / ${data.variants.length}`;

  grid.innerHTML = shown.length ? "" : '<p class="empty">Nothing matches that.</p>';
  const fragment = document.createDocumentFragment();
  shown.forEach((v, index) => {
    const card = document.createElement("a");
    card.className = "card";
    card.href = v.block_png;
    card.dataset.index = index;
    // A fixed meta row above the art keeps the version and the item icon on one
    // baseline across every tile, whatever height the artwork resolves to.
    card.innerHTML =
      `<div class="meta-row">` +
        (v.since ? `<span class="since${v.since === newest ? " new" : ""}">${esc(v.since)}</span>` : "<span></span>") +
        (v.item_png ? `<img class="badge" src="${v.item_png}" alt="" title="${esc(v.item)}">` : "") +
      `</div>` +
      `<img class="art" src="${v.block_png}" alt="${esc(v.label)}" loading="lazy">` +
      `<div class="info">` +
        `<div class="name">${esc(v.label)}</div>` +
        `<div class="id" title="${esc(v.name)}">${esc(v.name)}</div>` +
      `</div>`;
    card.addEventListener("click", event => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      openVariant(index);
    });
    fragment.append(card);
  });
  grid.append(fragment);
}

/* ------------------------------------------------------------- webgl */

const VERT = `
attribute vec3 aPos; attribute vec2 aUV; attribute vec3 aCol; attribute vec4 aRect;
uniform mat4 uMVP;
varying vec2 vUV; varying vec3 vCol; varying vec4 vRect;
void main() {
  vUV = aUV; vCol = aCol; vRect = aRect;
  gl_Position = uMVP * vec4(aPos, 1.0);
}`;

const FRAG = `
precision mediump float;
uniform sampler2D uTex; uniform float uCutout;
varying vec2 vUV; varying vec3 vCol; varying vec4 vRect;
void main() {
  // Keep the sample inside the face's own texel rectangle. Without this a sample on the
  // far edge reads one texel past it, which on the flower pot is transparent, and the
  // alpha test below punches a hairline of backdrop along the edge of the face.
  vec4 t = texture2D(uTex, clamp(vUV, vRect.xy, vRect.zw));
  if (uCutout > 0.5) {
    // Cutout: a texel is either in or out. Writing the texture's own alpha through would
    // let the page show along every not-quite-opaque texel, which reads as a see-through
    // seam on a solid block. The PNG renderer forces 1.0 here for the same reason.
    if (t.a < 0.5) discard;
    gl_FragColor = vec4(t.rgb * vCol, 1.0);
  } else {
    if (t.a <= 0.0) discard;
    gl_FragColor = vec4(t.rgb * vCol * t.a, t.a);
  }
}`;

const viewer = (() => {
  const canvas = document.getElementById("view");
  // An opaque drawing buffer cleared to the stage colour, so a multisampled edge blends
  // against the backdrop inside GL and the browser never composites the canvas at all.
  // With a transparent buffer the edges came out both darker and see-through: multisample
  // resolve hands back premultiplied colour, and the default premultipliedAlpha:false told
  // the compositor to multiply by alpha a second time.
  // Colour is still written premultiplied, which is what the blend below expects.
  const gl = canvas.getContext("webgl", { alpha: false, antialias: true, premultipliedAlpha: true });
  if (!gl) return null;

  const compile = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  };
  const program = gl.createProgram();
  gl.attachShader(program, compile(gl.VERTEX_SHADER, VERT));
  gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAG));
  gl.linkProgram(program);
  gl.useProgram(program);
  const aPos = gl.getAttribLocation(program, "aPos");
  const aUV = gl.getAttribLocation(program, "aUV");
  const aCol = gl.getAttribLocation(program, "aCol");
  const aRect = gl.getAttribLocation(program, "aRect");
  const uMVP = gl.getUniformLocation(program, "uMVP");
  const uCutout = gl.getUniformLocation(program, "uCutout");
  const uTex = gl.getUniformLocation(program, "uTex");

  const textures = new Map();
  function texture(file) {
    let entry = textures.get(file);
    if (entry) return entry;
    entry = { handle: gl.createTexture(), ready: false };
    gl.bindTexture(gl.TEXTURE_2D, entry.handle);
    // One opaque magenta texel until the real image lands, so nothing flashes white.
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
                  new Uint8Array([0, 0, 0, 0]));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const image = new Image();
    image.onload = () => {
      gl.bindTexture(gl.TEXTURE_2D, entry.handle);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      entry.ready = true;
      request();
    };
    image.src = "textures/" + file;
    textures.set(file, entry);
    return entry;
  }

  // A quad becomes two triangles; colour folds the tint and the baked face shade
  // together, because Minecraft's shading is fixed in model space and does not follow
  // the camera around.
  function build(model) {
    const groups = new Map();
    let radius = 1;
    for (const quad of model.q) {
      const key = `${quad.i}|${quad.b || 0}`;
      let group = groups.get(key);
      if (!group) {
        group = { file: model.t[quad.i], blend: !!quad.b, data: [] };
        groups.set(key, group);
      }
      const shade = quad.s === undefined ? 1 : quad.s;
      const c = quad.c || [1, 1, 1];
      const col = [c[0] * shade, c[1] * shade, c[2] * shade];
      const rect = quad.r;
      const vertex = i => {
        const x = quad.p[i * 3] - 8, y = quad.p[i * 3 + 1] - 8, z = quad.p[i * 3 + 2] - 8;
        radius = Math.max(radius, Math.hypot(x, y, z));
        group.data.push(x, y, z, quad.u[i * 2] / 16, quad.u[i * 2 + 1] / 16, ...col, ...rect);
      };
      [0, 1, 2, 0, 2, 3].forEach(vertex);
    }
    for (const group of groups.values()) {
      group.buffer = gl.createBuffer();
      group.count = group.data.length / 12;
      gl.bindBuffer(gl.ARRAY_BUFFER, group.buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(group.data), gl.STATIC_DRAW);
      group.data = null;
      group.texture = texture(group.file);
    }
    return { groups: [...groups.values()], radius };
  }

  // Columns of Ortho * Rx(pitch) * Ry(yaw), the same camera the PNGs are rendered with.
  // Depth only has to separate a 16-unit block, so the orthographic z term is a constant
  // squeeze; negating it keeps "larger z is nearer", matching gl.LESS.
  function matrix(yaw, pitch, extent, aspect) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    const sx = 1 / (extent * Math.max(aspect, 1));
    const sv = 1 / (extent * Math.max(1 / aspect, 1));
    const sz = -0.005;
    return new Float32Array([
      cy * sx, sp * sy * sv, -cp * sy * sz, 0,
      0, cp * sv, sp * sz, 0,
      sy * sx, -sp * cy * sv, cp * cy * sz, 0,
      0, 0, 0, 1,
    ]);
  }

  // Reduced motion suppresses the automatic spin, but not one the viewer asks for.
  // The camera the PNGs were rendered with, carried in the manifest rather than repeated
  // here -- these two numbers drifting apart is exactly how the 3D view would stop
  // matching the thumbnail it opens from.
  const home = data.view || { pitch: 30, yaw: 240 };
  let current = null, yaw = home.yaw, pitch = home.pitch, zoom = 1, spin = !reduced;
  let pending = 0, active = false, onSpin = null, backdrop = [0, 0, 0];

  // Read the stage's own background rather than the CSS variable, so it follows the theme
  // without this having to know how the theme is expressed.
  function readBackdrop() {
    const parts = getComputedStyle(canvas.parentElement).backgroundColor.match(/[\d.]+/g);
    backdrop = parts ? parts.slice(0, 3).map(Number).map(v => v / 255) : [0, 0, 0];
    request();
  }

  function setSpin(value) {
    spin = value;
    if (onSpin) onSpin(spin);
    request();
  }

  function request() { if (!pending) pending = requestAnimationFrame(render); }

  function render() {
    pending = 0;
    if (!current) return;
    const ratio = Math.min(devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(canvas.clientWidth * ratio));
    const h = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    gl.viewport(0, 0, w, h);
    gl.clearColor(backdrop[0], backdrop[1], backdrop[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.enable(gl.CULL_FACE);
    gl.cullFace(gl.BACK);
    gl.frontFace(gl.CCW);

    const mvp = matrix(yaw * Math.PI / 180, pitch * Math.PI / 180,
                       current.radius * 1.08 / zoom, w / h);
    gl.uniformMatrix4fv(uMVP, false, mvp);

    const pass = (blend) => {
      for (const group of current.groups) {
        if (group.blend !== blend || !group.texture.ready) continue;
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, group.texture.handle);
        gl.uniform1i(uTex, 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, group.buffer);
        for (const [loc, size, offset] of [[aPos, 3, 0], [aUV, 2, 12], [aCol, 3, 20],
                                          [aRect, 4, 32]]) {
          gl.enableVertexAttribArray(loc);
          gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 48, offset);
        }
        gl.drawArrays(gl.TRIANGLES, 0, group.count);
      }
    };

    gl.disable(gl.BLEND);
    gl.depthMask(true);
    gl.uniform1f(uCutout, 1);
    pass(false);
    gl.enable(gl.BLEND);
    // "over" for premultiplied source; it accumulates coverage instead of eroding the
    // alpha the opaque pass already wrote.
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.depthMask(false);
    gl.uniform1f(uCutout, 0);
    pass(true);
    gl.depthMask(true);

    if (spin && active) { yaw += 0.35; request(); }
  }

  const cache = new Map();
  function show(name) {
    const models = window.NEP_MODELS || {};
    if (!models[name]) { current = null; active = false; return false; }
    active = true;
    if (!cache.has(name)) cache.set(name, build(models[name]));
    current = cache.get(name);
    readBackdrop();
    reset();
    return true;
  }
  function reset() { yaw = home.yaw; pitch = home.pitch; zoom = 1; request(); }

  let dragging = false, lastX = 0, lastY = 0;
  canvas.addEventListener("pointerdown", event => {
    dragging = true; lastX = event.clientX; lastY = event.clientY;
    if (spin) setSpin(false);
    canvas.classList.add("dragging");
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", event => {
    if (!dragging) return;
    yaw += (event.clientX - lastX) * 0.6;
    pitch = Math.max(-89, Math.min(89, pitch + (event.clientY - lastY) * 0.6));
    lastX = event.clientX; lastY = event.clientY;
    request();
  });
  const stop = () => { dragging = false; canvas.classList.remove("dragging"); };
  canvas.addEventListener("pointerup", stop);
  canvas.addEventListener("pointercancel", stop);
  canvas.addEventListener("wheel", event => {
    event.preventDefault();
    zoom = Math.max(0.4, Math.min(4, zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12)));
    request();
  }, { passive: false });
  addEventListener("resize", request);

  return {
    show, reset, request,
    stop: () => { active = false; },
    setSpin, spinning: () => spin, readBackdrop,
    onSpinChange: fn => { onSpin = fn; fn(spin); },
  };
})();

/* -------------------------------------------------------------- modal */

const modal = document.getElementById("modal");
const stage = document.querySelector(".stage");
let index = 0, loading = null;

function loadGeometry() {
  if (loading) return loading;
  loading = new Promise(resolve => {
    const script = document.createElement("script");
    script.src = "models.js";
    script.onload = () => resolve(true);
    script.onerror = () => resolve(false);
    document.head.append(script);
  });
  return loading;
}

function fill(v) {
  document.getElementById("m-title").textContent = v.label;
  // A zero-width space after the namespace colon gives the id a sensible place to
  // wrap on a narrow panel, instead of breaking in the middle of a word.
  document.getElementById("m-id").textContent =
    data.mod.id + ":" + "\u200b" + v.name;
  // A mod without version data (no enum to read markers from) shows no Since row.
  const since = document.getElementById("m-since");
  since.innerHTML = v.since
    ? `<span>${esc(v.since)}</span>` +
      (v.since === newest ? '<span class="tag-new">new</span>' : "")
    : "";
  since.parentElement.hidden = !v.since;
  document.getElementById("m-item").innerHTML =
    (v.item_png ? `<img src="${v.item_png}" alt="">` : "") + `<span>${esc(v.item)}</span>`;
  document.getElementById("m-png").href = v.block_png;
  document.getElementById("m-png").setAttribute("download", v.name + ".png");
}

async function openVariant(at) {
  index = at;
  const v = shown[index];
  if (!v) return;
  document.documentElement.classList.add("modal-open");
  fill(v);
  document.getElementById("m-position").textContent = `${index + 1} / ${shown.length}`;
  const alone = shown.length < 2;
  document.getElementById("prev").disabled = alone;
  document.getElementById("next").disabled = alone;
  if (!modal.open) modal.showModal();
  const ok = await loadGeometry();
  if (!ok || !viewer || !viewer.show(v.name)) {
    stage.classList.add("unavailable");
    stage.querySelector(".hint").textContent = "3D view unavailable";
  } else {
    stage.classList.remove("unavailable");
    stage.querySelector(".hint").textContent = "drag to rotate · scroll to zoom";
  }
}

function step(delta) {
  if (!shown.length) return;
  openVariant((index + delta + shown.length) % shown.length);
}

function closeModal() {
  if (!modal.open) return;
  modal.classList.add("closing");
  const done = () => {
    modal.classList.remove("closing");
    modal.close();
    document.documentElement.classList.remove("modal-open");
    viewer && viewer.stop();
  };
  if (reduced) return done();
  setTimeout(done, 170);
}

modal.addEventListener("click", event => {
  // A click landing on the dialog itself is a click on the backdrop; the panel fills it.
  // `closest`, not `hasAttribute`: the button holds an <svg>, so a real click reports
  // the <use> inside it as the target and never the button itself.
  if (event.target === modal || event.target.closest("[data-close]")) closeModal();
});
modal.addEventListener("cancel", event => { event.preventDefault(); closeModal(); });
document.getElementById("prev").addEventListener("click", () => step(-1));
document.getElementById("next").addEventListener("click", () => step(1));
document.getElementById("m-reset").addEventListener("click", () => viewer && viewer.reset());

const spinButton = document.getElementById("m-spin");
const spinIcon = document.getElementById("spin-icon");
if (viewer) {
  viewer.onSpinChange(on => {
    // The button shows the action it performs, not the state it is in, which is
    // the convention every media control follows.
    spinIcon.setAttribute("href", on ? "#i-pause" : "#i-play");
    const label = on ? "Pause rotation" : "Play rotation";
    spinButton.setAttribute("aria-label", label);
    spinButton.title = label;
    spinButton.setAttribute("aria-pressed", String(on));
  });
  spinButton.addEventListener("click", () => viewer.setSpin(!viewer.spinning()));
} else {
  spinButton.disabled = true;
}
addEventListener("keydown", event => {
  if (!modal.open) {
    // "/" jumps to the filter, the one shortcut worth having on a page that is
    // mostly a search box and a grid.
    if (event.key === "/" && document.activeElement !== query) {
      event.preventDefault();
      query.focus();
      query.select();
    }
    return;
  }
  if (event.key === "ArrowLeft") step(-1);
  if (event.key === "ArrowRight") step(1);
});

/* --------------------------------------------------------------- filters */

query.addEventListener("input", draw);
clearButton.addEventListener("click", () => {
  query.value = "";
  query.focus();
  draw();
});


/* ----------------------------------------------------------------- theme */

const themeButton = document.getElementById("theme");
const themeIcon = document.getElementById("theme-icon");
const system = matchMedia("(prefers-color-scheme: light)");
let stored = null;
try { stored = localStorage.getItem("theme"); } catch (error) { /* private mode */ }

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  // The icon offers the theme you would switch to, so it reads as a destination
  // rather than a status light.
  themeIcon.setAttribute("href", theme === "dark" ? "#i-sun" : "#i-moon");
  const label = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
  themeButton.setAttribute("aria-label", label);
  themeButton.title = label;
  if (viewer) viewer.readBackdrop();
}

applyTheme(stored || (system.matches ? "light" : "dark"));

themeButton.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  stored = next;
  try { localStorage.setItem("theme", next); } catch (error) { /* private mode */ }
});
// Follow the system only while the visitor has not chosen for themselves.
system.addEventListener("change", event => {
  if (!stored) applyTheme(event.matches ? "light" : "dark");
});

draw();
