const ANSI_COLORS = ["#b3b3b3", "#ff6666", "#66b366", "#ffff66", "#6666ff", "#ff66ff", "#66ffff", "#ffffff"];
const ANSI_COLORS_LIGHT = ["#d9d9d9", "#ff9999", "#99cc99", "#ffff99", "#9999ff", "#ff99ff", "#ccffff", "#ffffff"];
const EventTypes = { EXEC: 0, BUF: 1 };
const GraphConfig = [{ pcolor: "#c9a8ff", unit: "B", fillColor: "#2B1B72" }, { pcolor: "#4fa3cc", unit: "Hz", fillColor: "#4fa3cc" }];

const profilerEl = d3.select("#profiler");
const captureSummary = document.getElementById("capture-summary");
const statusEl = document.getElementById("status");
const detailsEl = document.getElementById("details");
const instsEl = document.getElementById("insts");
const tooltipEl = document.getElementById("tooltip");

let data = null;
let focusedShape = null;
let canvasZoom = null;
let zoomLevel = d3.zoomIdentity;

const darkenHex = (hex, percent = 0) => {
  const color = parseInt(hex.slice(1), 16);
  const factor = 1 - percent / 100;
  return `#${(
    ((color >> 16 & 255) * factor | 0) << 16 |
    ((color >> 8 & 255) * factor | 0) << 8 |
    ((color & 255) * factor | 0)
  ).toString(16).padStart(6, "0")}`;
};

const parseColors = (text, defaultColor = "#ffffff") => Array.from(text.matchAll(/(?:\u001b\[(\d+)m([\s\S]*?)\u001b\[0m)|([^\u001b]+)/g),
  ([_, code, coloredText, plain]) => ({
    st: coloredText ?? plain,
    color: code != null ? (code >= 90 ? ANSI_COLORS_LIGHT : ANSI_COLORS)[(parseInt(code) - 30 + 60) % 60] : defaultColor,
  }));

const colored = value => d3.create("span").call(root =>
  root.selectAll("span").data(typeof value === "string" ? parseColors(value) : value).join("span")
    .style("color", d => d.color).text(d => d.st)
).node();

const rect = node => (typeof node === "string" ? document.querySelector(node) : node).getBoundingClientRect();

function formatMicroseconds(ts, showUs = true) {
  const seconds = Math.floor(ts / 1e6);
  const millis = Math.floor((ts % 1e6) / 1e3);
  const micros = Math.round(ts % 1e3);
  const parts = [];
  if (seconds) parts.push(`${seconds}s`);
  if (millis || (!showUs && !seconds)) parts.push(`${millis}ms`);
  if (showUs && (micros || (!millis && !seconds))) parts.push(`${micros}us`);
  return parts.join(" ");
}

function formatCycles(cycles) {
  const mega = Math.floor(cycles / 1e6);
  const kilo = Math.floor((cycles % 1e6) / 1e3);
  const single = Math.round(cycles % 1e3);
  const parts = [];
  if (mega) parts.push(`${mega}M`);
  if (kilo) parts.push(`${kilo}K`);
  if (single || (!mega && !kilo)) parts.push(`${single}`);
  return parts.join(" ");
}

const formatUnit = (value, unit = "") => d3.format(".3~s")(value) + unit;
const formatCaptureSummary = metadata => {
  const eventCount = metadata.event_count ?? metadata.events;
  const selectedUnit = [metadata.se, metadata.cu, metadata.simd].every(value => value != null)
    ? `SE ${metadata.se} / CU ${metadata.cu} / SIMD ${metadata.simd}` : null;
  const summary = [
    metadata.kernel_name,
    metadata.kernel_iteration != null ? `iter ${metadata.kernel_iteration}` : null,
    selectedUnit,
    metadata.target,
    eventCount != null ? `${eventCount} events` : null,
  ].filter(Boolean);
  if (summary.length) return summary.join(" · ");
  return [metadata.att, metadata.codeobj, metadata.target, metadata.events != null ? `${metadata.events} events` : null].filter(Boolean).join(" · ");
};

function tabulate(rows) {
  const root = d3.create("div").classed("table-grid", true);
  for (const [key, value] of rows) {
    root.append("div").classed("key", true).text(key);
    root.append("div").classed("value", true).node().append(value instanceof Node ? value : document.createTextNode(String(value)));
  }
  return root.node();
}

function drawLine(ctx, x, y, opts) {
  ctx.beginPath();
  ctx.moveTo(x[0], y[0]);
  ctx.lineTo(x[1], y[1]);
  ctx.fillStyle = ctx.strokeStyle = opts?.color || "#f0f0f5";
  ctx.stroke();
}

const waveColor = op => {
  let color = data.waveColors.find(([pattern]) => op.includes(pattern))?.[1] ?? "#ffffff";
  if (op.includes("OTHER_") || op.includes("_ALT")) color = darkenHex(color, 75);
  if (op.includes("LDS_")) color = darkenHex(color, 25);
  return color;
};

const colorScheme = {
  DEFAULT: ["#2b2e39", "#2c2f3a", "#31343f", "#323544", "#2d303a", "#2e313c", "#343746", "#353847", "#3c4050", "#404459", "#444862", "#4a4e65"],
  WAVE: waveColor,
};

const cycleColors = (list, i) => list[i % list.length];

function selectShape(key) {
  if (key == null) return {};
  const [trackName, index] = key.split(/-(?=[^-]+$)/);
  const track = data.tracks.get(trackName);
  return { eventType: track?.eventType, e: track?.shapes[Number(index)] };
}

const timelineScale = () => d3.scaleLinear().domain([data.first, data.dur]).range([0, document.getElementById("canvas-wrap").clientWidth]);

function canvasRect(key, pixelScale) {
  const { e } = selectShape(key);
  const track = data.tracks.get(key.split(/-(?=[^-]+$)/)[0]);
  const x = pixelScale(e.x);
  const width = pixelScale(e.x + e.width) - x;
  const y = track.offsetY + e.y;
  return { x0: x, x1: x + width, y0: y, y1: y + e.height };
}

function timeAtCycle(clk) {
  if (clk < data.instSt || clk > data.instEt || data.tracks.get("Shader Clock") == null) return "-";
  let current = data.instSt;
  let nanoseconds = 0;
  let freq = null;
  for (const [start, value] of data.tracks.get("Shader Clock").valueMap) {
    if (freq != null && freq > 0 && current < start) {
      const end = Math.min(clk, start);
      nanoseconds += (end - current) * 1e9 / freq;
      current = end;
      if (current === clk) break;
    }
    freq = value;
  }
  if (freq != null && current < clk) nanoseconds += (clk - current) * 1e9 / freq;
  const remNs = Math.round(nanoseconds % 1000);
  return nanoseconds / 1000 > 1 ? `${formatMicroseconds(nanoseconds / 1000, true)}${remNs ? ` ${remNs}ns` : ""}` : `${Math.round(nanoseconds)}ns`;
}

function getZoomIdentity() {
  if (data.instSt == null || data.instEt == null) return d3.zoomIdentity;
  const scale = (data.dur - data.first) / (data.instEt - data.instSt);
  const xscale = timelineScale();
  return d3.zoomIdentity.translate(-xscale(data.instSt) * scale, 0).scale(scale);
}

function buildInstructionList() {
  if (data.pcMap == null) {
    instsEl.textContent = "No instruction map found.";
    return;
  }
  const lines = Object.entries(data.pcMap).sort((a, b) => Number(a[0]) - Number(b[0]));
  instsEl.replaceChildren();
  for (const [pcKey, label] of lines) {
    const pc = Number(pcKey);
    const pcHex = pc.toString(16).padStart(Math.max(4, Math.ceil(pc.toString(16).length / 4) * 4), "0");
    const line = document.createElement("div");
    line.className = "line";
    const left = document.createElement("span");
    left.className = "left";
    left.id = `inst-${pc}`;
    const pcSpan = document.createElement("span");
    pcSpan.className = "pc";
    pcSpan.textContent = `0x${pcHex}`;
    const labelSpan = document.createElement("span");
    labelSpan.className = "label";
    labelSpan.textContent = label;
    left.append(pcSpan, labelSpan);
    line.append(left);
    instsEl.append(line);
  }
}

function highlightInstruction(pc) {
  for (const el of instsEl.querySelectorAll(".left.highlight")) el.classList.remove("highlight");
  if (pc == null) return;
  const target = document.getElementById(`inst-${pc}`);
  if (target == null) return;
  target.classList.add("highlight");
  const panel = rect(instsEl);
  const line = rect(target);
  if (Math.max(panel.top - line.bottom, line.top - panel.bottom) >= -30) {
    instsEl.scrollTop = target.offsetTop - instsEl.clientHeight / 2 + target.clientHeight / 2;
  }
}

function setFocus(key) {
  if (key !== focusedShape) {
    const { e } = selectShape(key);
    const link = e?.arg.link ?? data.links.get(key);
    data.link = link == null ? null : [key, link];
    focusedShape = key;
    d3.select("#timeline").call(canvasZoom.transform, zoomLevel);
    tooltipEl.style.display = "none";
  }

  const { eventType, e } = selectShape(key);
  detailsEl.replaceChildren();
  if (eventType !== EventTypes.EXEC || e == null) {
    detailsEl.append(tabulate([["Selection", "None"]]));
    highlightInstruction(null);
    return;
  }

  const rows = [["Name", colored(e.arg.displayName)], ["Duration", formatCycles(e.width)], ["Cycle", formatCycles(e.x - data.instSt)]];
  rows.push(["Time", `${timeAtCycle(e.x)} (${formatCycles(e.x)})`]);
  if (data.link != null) rows.push(["Delay", `${formatCycles(Math.abs(selectShape(data.link[0]).e.x - selectShape(data.link[1]).e.x))} cycles`]);
  if (e.arg.pc != null) rows.push(["PC", `0x${e.arg.pc.toString(16)}`]);
  detailsEl.append(tabulate(rows));

  if (e.arg.infoLines.length) {
    const info = document.createElement("div");
    info.className = "args";
    for (const line of e.arg.infoLines) {
      const p = document.createElement("p");
      p.textContent = line;
      info.append(p);
    }
    detailsEl.append(info);
  }

  const pc = e.arg.pc ?? (data.link != null ? selectShape(data.link[1]).e.arg.pc : null);
  highlightInstruction(pc);
}

function renderProfiler(buf) {
  profilerEl.html("");
  data = { tracks: new Map(), links: new Map(), first: null, link: null };
  buildInstructionList();
  detailsEl.replaceChildren();

  const shell = profilerEl.append("div").attr("id", "profiler-shell");
  const deviceList = shell.append("div").attr("id", "device-list");
  const canvasWrap = shell.append("div").attr("id", "canvas-wrap");
  const canvas = canvasWrap.append("canvas").attr("id", "timeline").node();
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;

  const view = new DataView(buf);
  let offset = 0;
  const u8 = () => { const ret = view.getUint8(offset); offset += 1; return ret; };
  const u32 = () => { const ret = view.getUint32(offset, true); offset += 4; return ret; };
  const u64 = () => { const ret = Number(view.getBigUint64(offset, true)); offset += 8; return ret; };
  const f32 = () => { const ret = view.getFloat32(offset, true); offset += 4; return ret; };
  const optional = value => value === 0 ? null : value - 1;

  const dur = u32();
  const tracePeak = u64();
  const indexLen = u32();
  const layoutsLen = u32();
  const textDecoder = new TextDecoder("utf-8");
  const { strings, dtypeSize, markers, ...extData } = JSON.parse(textDecoder.decode(new Uint8Array(buf, offset, indexLen)));
  offset += indexLen;
  Object.assign(data, extData, { dur, markers, dtypeSize });

  const tickSize = 5;
  const padding = 8;
  const baseOffset = markers.length ? 14 : 0;
  const axisHeight = tickSize * 2 + padding * 2;
  deviceList.style("padding-top", `${axisHeight + baseOffset}px`);

  const colorMap = new Map();
  const heightScale = d3.scaleLinear().domain([0, tracePeak]).range([4, 100]);
  const canvasTop = () => rect(canvas).top;

  ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";

  for (let i = 0; i < layoutsLen; i++) {
    const nameLen = u8();
    const rowName = textDecoder.decode(new Uint8Array(buf, offset, nameLen));
    offset += nameLen;
    const div = deviceList.append("div").attr("id", rowName).text(rowName);
    const eventType = u8();
    const eventsLen = u32();
    const rowBorderColor = i < layoutsLen - 1 ? "#22232a" : null;
    if (rowBorderColor != null) div.style("border-bottom", `1px solid ${rowBorderColor}`);
    const { y: baseY, height: baseHeight } = rect(div.node());
    const offsetY = baseY - canvasTop() + padding / 2;
    const shapes = [];
    const visible = [];

    if (eventType === EventTypes.EXEC) {
      const levelHeight = (baseHeight - padding) * 0.5;
      const levels = [];
      data.tracks.set(rowName, { shapes, eventType, visible, offsetY, pcolor: "#00c72f", scolor: "#858b9d", rowBorderColor });
      let colorKey = null;
      for (let j = 0; j < eventsLen; j++) {
        const event = { name: strings[u32()], ref: optional(u32()), key: optional(u32()), st: u32(), dur: f32(), info: strings[u32()] || null };
        let depth = levels.findIndex(levelEnd => event.st >= levelEnd);
        const endTime = event.st + Math.trunc(event.dur);
        if (depth === -1) {
          depth = levels.length;
          levels.push(endTime);
        } else {
          levels[depth] = endTime;
        }
        if (depth === 0) colorKey = event.name.split(" ")[0];
        if (!colorMap.has(colorKey)) {
          const colors = colorScheme.WAVE;
          const color = typeof colors === "function" ? colors(colorKey) : cycleColors(colorScheme.DEFAULT, colorMap.size);
          colorMap.set(colorKey, d3.rgb(color));
        }
        const fillColor = colorMap.get(colorKey).brighter(0.3 * depth).toString();
        const label = parseColors(event.name).flatMap(({ color, st }) => {
          const parts = [];
          for (let index = 0; index < st.length; index += 4) {
            const part = st.slice(index, index + 4);
            parts.push({ color, st: part, width: ctx.measureText(part).width });
          }
          return parts;
        });
        let infoLines = [];
        let pc = null;
        let link = null;
        if (event.info?.startsWith("PC:")) pc = Number(event.info.split(":")[1]);
        else if (event.info?.startsWith("LINK:")) {
          link = event.info.replace("LINK:", "");
        } else if (event.info != null && event.info !== "") {
          infoLines = event.info.split("\n");
        }

        const key = `${rowName}-${j}`;
        if (link != null) data.links.set(link, key);
        const tooltipParts = [`N:${shapes.length}`, formatCycles(event.dur)];
        if (pc != null) tooltipParts.push(`PC 0x${pc.toString(16)}`);
        for (const line of infoLines) tooltipParts.push(line);
        shapes.push({
          x: event.st,
          y: levelHeight * depth,
          width: event.dur,
          height: levelHeight,
          label: null,
          fillColor,
          arg: { key, displayName: event.name, label, pc, link, infoLines, tooltipText: tooltipParts.join("\n") },
        });
        if (j === 0) data.first = data.first == null ? event.st : Math.min(data.first, event.st);
      }
      div.style("height", `${levelHeight * levels.length + padding}px`).style("pointer-events", "none");
    } else {
      const linear = u8();
      const peak = u64();
      const config = GraphConfig[linear];
      const timestamps = [];
      const valueMap = new Map();
      for (let j = 0; j < eventsLen; j++) {
        const ts = u32();
        const value = u64();
        timestamps.push(ts);
        valueMap.set(ts, value);
      }
      timestamps.push(dur);
      const height = (baseHeight - padding) * 1;
      const yscale = d3.scaleLinear().domain([0, peak]).range([height, 0]);
      const base0 = yscale(0);
      const sum = { x: [], y0: [], y1: [], fillColor: config.fillColor };
      for (let j = 0; j < timestamps.length - 1; j++) {
        const y = yscale(valueMap.get(timestamps[j]));
        sum.x.push(timestamps[j], timestamps[j + 1]);
        sum.y1.push(y, y);
        sum.y0.push(base0, base0);
      }
      if (timestamps.length > 0) data.first = data.first == null ? timestamps[0] : Math.min(data.first, timestamps[0]);
      data.tracks.set(rowName, { shapes: [sum], eventType, linear, visible, offsetY, pcolor: config.pcolor, height, peak, valueMap, rowBorderColor });
      div.style("height", `${height + padding}px`).style("pointer-events", "none");
    }
  }

  if (data.pcMap != null) buildInstructionList();

  let instRange = null;
  for (const [trackName, track] of data.tracks) {
    if (track.eventType !== EventTypes.EXEC) continue;
    const first = track.shapes[0]?.x;
    const last = track.shapes.at(-1)?.x + track.shapes.at(-1)?.width;
    if (first == null || last == null) continue;
    instRange = instRange == null ? [first, last] : [Math.min(first, instRange[0]), Math.max(last, instRange[1])];
  }
  if (instRange != null) [data.instSt, data.instEt] = instRange;

  function resize() {
    const canvasWidth = document.getElementById("canvas-wrap").clientWidth;
    const canvasHeight = Math.round(rect("#device-list").height);
    if (canvas.width === canvasWidth * dpr && canvas.height === canvasHeight * dpr) return;
    canvas.width = canvasWidth * dpr;
    canvas.height = canvasHeight * dpr;
    canvas.style.width = `${canvasWidth}px`;
    canvas.style.height = `${canvasHeight}px`;
    ctx.scale(dpr, dpr);
    ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
    d3.select(canvas).call(canvasZoom.transform, zoomLevel);
  }

  function render(transform) {
    zoomLevel = transform;
    const canvasWidth = canvas.clientWidth;
    const xscale = timelineScale();
    ctx.clearRect(0, 0, canvasWidth, canvas.clientHeight);

    const visibleX = xscale.range().map(zoomLevel.invertX, zoomLevel).map(xscale.invert, xscale);
    const st = visibleX[0];
    const et = visibleX[1];
    xscale.domain([st, et]);

    const visibleYStart = profilerEl.node().scrollTop;
    const visibleYEnd = visibleYStart + profilerEl.node().clientHeight;

    ctx.textBaseline = "middle";
    for (const [trackName, track] of data.tracks) {
      track.visible.length = 0;
      const rowHeight = rect(document.getElementById(trackName)).height;
      if (track.offsetY + rowHeight < visibleYStart || track.offsetY > visibleYEnd) continue;

      if (track.eventType === EventTypes.EXEC) {
        for (const shape of track.shapes) {
          if (shape.x > et || shape.x + shape.width < st) continue;
          const x = xscale(shape.x);
          const y = track.offsetY + shape.y;
          const width = xscale(shape.x + shape.width) - x;
          ctx.beginPath();
          ctx.rect(x, y, width, shape.height);
          track.visible.push({ x0: x, x1: x + width, y0: y, y1: y + shape.height, arg: shape.arg });
          ctx.fillStyle = shape.fillColor;
          ctx.fill();
          if ((focusedShape != null && shape.arg.key === focusedShape) || (data.link != null && (shape.arg.key === data.link[0] || shape.arg.key === data.link[1]))) {
            ctx.strokeStyle = track.pcolor;
            ctx.stroke();
          } else if (track.scolor != null && width > 10) {
            ctx.strokeStyle = track.scolor;
            ctx.stroke();
          }
        }
      } else {
        const shape = track.shapes[0];
        ctx.beginPath();
        const x = shape.x.map(xscale);
        ctx.moveTo(x[0], track.offsetY + shape.y1[0]);
        for (let i = 1; i < x.length; i++) {
          ctx.lineTo(x[i], track.offsetY + shape.y1[i]);
          track.visible.push({
            x0: x[i - 1],
            x1: x[i],
            y0: track.offsetY + shape.y1[i - 1],
            y1: track.offsetY + shape.y0[i],
            arg: { tooltipText: formatUnit(track.valueMap.get(shape.x[i - 1]), GraphConfig[1].unit) },
          });
        }
        ctx.strokeStyle = shape.fillColor;
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.lineWidth = 1;
      }

      if (track.rowBorderColor != null) {
        const y = track.offsetY + rowHeight - padding / 2 - 0.5;
        drawLine(ctx, [0, canvasWidth], [y, y], { color: track.rowBorderColor });
      }
    }

    if (data.link != null) {
      const [a, b] = [canvasRect(data.link[0], xscale), canvasRect(data.link[1], xscale)];
      const [left, right] = a.x0 <= b.x0 ? [a, b] : [b, a];
      const startX = left.x1;
      const endX = right.x0;
      const leftY = (left.y0 + left.y1) / 2;
      const rightY = (right.y0 + right.y1) / 2;
      const bend = Math.max(12, Math.min(40, (endX - startX) / 2));
      ctx.beginPath();
      ctx.moveTo(startX, leftY);
      ctx.bezierCurveTo(startX + bend, leftY, endX - bend, rightY, endX, rightY);
      ctx.strokeStyle = "#858b9d";
      ctx.stroke();
    }

    ctx.save();
    ctx.translate(0, baseOffset);
    const axisY = tickSize + padding;
    drawLine(ctx, xscale.range(), [axisY, axisY]);
    let lastLabelEnd = -Infinity;
    for (const tick of xscale.ticks()) {
      if (!Number.isInteger(tick)) continue;
      const x = xscale(tick);
      drawLine(ctx, [x, x], [axisY, axisY + tickSize]);
      const labelX = x + ctx.lineWidth + 2;
      if (labelX <= lastLabelEnd) continue;
      const label = formatCycles(tick);
      ctx.textBaseline = "top";
      ctx.fillStyle = "#f0f0f5";
      ctx.fillText(label, labelX, axisY + tickSize);
      lastLabelEnd = labelX + ctx.measureText(label).width + 4;
      drawLine(ctx, [x, x], [axisY, axisY - tickSize]);
      const secondary = timeAtCycle(tick);
      ctx.fillText(secondary, labelX, 0);
      lastLabelEnd = Math.max(lastLabelEnd, labelX + ctx.measureText(secondary).width + 4);
    }
    ctx.restore();
  }

  function findRectAtPosition(clientX, clientY) {
    let track = null;
    for (const k of data.tracks.keys()) {
      const r = rect(document.getElementById(k));
      if (clientY >= r.y && clientY <= r.y + r.height) { track = data.tracks.get(k); break; }
    }
    if (track == null) return null;
    const canvasRect = rect(canvas);
    const x = ((clientX - canvasRect.left) * (canvas.width / canvasRect.width)) / dpr;
    const y = ((clientY - canvasRect.top) * (canvas.height / canvasRect.height)) / dpr;
    for (const visible of track.visible) {
      if (y >= visible.y0 && y <= visible.y1 && x >= visible.x0 && x <= visible.x1) return visible.arg;
    }
    return null;
  }

  canvas.addEventListener("click", event => {
    const found = findRectAtPosition(event.clientX, event.clientY);
    setFocus(found?.key ?? null);
  });

  canvas.addEventListener("mousemove", event => {
    const found = findRectAtPosition(event.clientX, event.clientY);
    if (found?.tooltipText == null) {
      tooltipEl.style.display = "none";
      return;
    }
    tooltipEl.replaceChildren(colored(found.displayName || ""), document.createTextNode((found.displayName ? "\n" : "") + found.tooltipText));
    tooltipEl.style.display = "block";
    tooltipEl.style.left = `${event.pageX + 10}px`;
    tooltipEl.style.top = `${event.pageY}px`;
  });
  canvas.addEventListener("mouseleave", () => { tooltipEl.style.display = "none"; });
  canvas.addEventListener("wheel", event => {
    event.stopPropagation();
    event.preventDefault();
  }, { passive: false });

  const vizZoomFilter = event => (!event.ctrlKey || event.type === "wheel" || event.type === "mousedown") && !event.button && event.type !== "dblclick";
  canvasZoom = d3.zoom().filter(vizZoomFilter).on("zoom", event => render(event.transform));
  zoomLevel = getZoomIdentity();
  d3.select(canvas).call(canvasZoom);
  d3.select(canvas).call(canvasZoom.transform, zoomLevel);

  new ResizeObserver(() => resize()).observe(profilerEl.node());
  profilerEl.on("scroll", () => render(zoomLevel));
  resize();
  setFocus(null);
}

async function main() {
  try {
    statusEl.textContent = "Loading bundle...";
    const [metadata, timeline] = await Promise.all([
      fetch("./metadata.json").then(resp => resp.ok ? resp.json() : {}),
      fetch("./timeline.bin").then(resp => {
        if (!resp.ok) throw new Error(`failed to load timeline.bin: ${resp.status}`);
        return resp.arrayBuffer();
      }),
    ]);
    document.title = metadata.title || "TinygradProfiler PKTS";
    captureSummary.textContent = formatCaptureSummary(metadata) || "Timeline bundle loaded.";
    renderProfiler(timeline);
    statusEl.textContent = "";
  } catch (err) {
    statusEl.textContent = err.stack || err.message || String(err);
  }
}

document.getElementById("zoom-to-fit-btn").addEventListener("click", () => {
  const canvas = d3.select("#timeline");
  if (!canvas.empty()) canvas.call(canvasZoom.transform, getZoomIdentity());
});

main();
