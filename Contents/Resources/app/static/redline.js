// Draws check findings on the image the way a reviewer would mark a proof:
// a hand-drawn red circle on each problem, an arrow, and a red note in the margin.

const RED = "#d8261c";
const CHECK_GREEN = "#12894d";
const CHECK_FONT_SIZE = 22;
const NOTE_FONT = '"Klee", "Klee One", "Hannotate SC", "Hiragino Maru Gothic ProN", "Hiragino Sans", sans-serif';
const IMAGE_WIDTH = 800;
const NOTE_WIDTH = 330;
const LEADER_GAP = 64;
const PAD = 20;
const FONT_SIZE = 26;
const LINE_HEIGHT = 37;
const NOTE_GAP = 22;
const NOTE_LIMIT = 90;
const NO_LINE_START = "、。，．）」』】〕！？ー…ぁぃぅぇぉっゃゅょゎ,.)!?";

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("画像を読めませんでした。"));
    image.src = url;
  });
}

function clip(text, limit) {
  const chars = Array.from(String(text || "").trim());
  return chars.length > limit ? `${chars.slice(0, limit - 1).join("")}…` : chars.join("");
}

function wrapText(ctx, text, maxWidth) {
  const lines = [];
  let line = "";
  for (const char of Array.from(text)) {
    const next = line + char;
    if (line && ctx.measureText(next).width > maxWidth && !NO_LINE_START.includes(char)) {
      lines.push(line);
      line = char;
    } else {
      line = next;
    }
  }
  if (line) lines.push(line);
  return lines;
}

// A box that covers nearly the whole picture is a whole-image remark, not a spot.
function usableBox(box) {
  if (!Array.isArray(box) || box.length !== 4 || box.some((value) => typeof value !== "number")) return null;
  const [x0, y0, x1, y1] = box;
  if (x1 <= x0 || y1 <= y0) return null;
  if (x1 - x0 > 0.9 && y1 - y0 > 0.9) return null;
  return box;
}

function roughEllipse(ctx, cx, cy, rx, ry, seed) {
  const steps = 72;
  const sweep = Math.PI * 2 + 0.6;
  const start = -Math.PI / 2 + (seed % 7) * 0.35;
  ctx.beginPath();
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const angle = start + t * sweep;
    const wobble = 1 + 0.035 * Math.sin(angle * 3 + seed) + 0.02 * Math.sin(angle * 5 + seed * 2) + t * 0.08;
    const x = cx + Math.cos(angle) * rx * wobble;
    const y = cy + Math.sin(angle) * ry * wobble;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
}

function drawLeader(ctx, from, center, rx, ry, seed) {
  const dx = from.x - center.x;
  const dy = from.y - center.y;
  const reach = Math.sqrt((dx / (rx * 1.06)) ** 2 + (dy / (ry * 1.06)) ** 2);
  if (reach <= 1) return;
  const to = { x: center.x + dx / reach, y: center.y + dy / reach };
  const length = Math.hypot(to.x - from.x, to.y - from.y);
  const bend = (seed % 2 === 0 ? 1 : -1) * length * 0.1;
  const control = {
    x: (from.x + to.x) / 2 - ((to.y - from.y) / length) * bend,
    y: (from.y + to.y) / 2 + ((to.x - from.x) / length) * bend,
  };
  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.quadraticCurveTo(control.x, control.y, to.x, to.y);
  ctx.stroke();

  const heading = Math.atan2(to.y - control.y, to.x - control.x);
  const head = 18;
  ctx.beginPath();
  ctx.moveTo(to.x - Math.cos(heading - 0.5) * head, to.y - Math.sin(heading - 0.5) * head);
  ctx.lineTo(to.x, to.y);
  ctx.lineTo(to.x - Math.cos(heading + 0.5) * head, to.y - Math.sin(heading + 0.5) * head);
  ctx.stroke();
}

// Stack notes down a margin column, each as close as possible to its target.
function stackColumn(notes, imageTop) {
  notes.sort((a, b) => a.centerY - b.centerY);
  let floor = imageTop;
  for (const note of notes) {
    note.top = Math.max(note.centerY - note.height / 2, floor);
    floor = note.top + note.height + NOTE_GAP;
  }
  return floor - NOTE_GAP;
}

// A green tick in a ring: the reviewer's "I checked this" mark.
function drawTick(ctx, cx, cy, r) {
  ctx.save();
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = "#ffffff";
  ctx.fill();
  ctx.lineWidth = Math.max(3, r * 0.16);
  ctx.strokeStyle = CHECK_GREEN;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(cx - r * 0.5, cy + r * 0.02);
  ctx.lineTo(cx - r * 0.12, cy + r * 0.42);
  ctx.lineTo(cx + r * 0.55, cy - r * 0.4);
  ctx.lineWidth = Math.max(4, r * 0.28);
  ctx.stroke();
  ctx.restore();
}

function drawHaloText(ctx, text, x, y) {
  ctx.save();
  ctx.font = `700 ${CHECK_FONT_SIZE}px ${NOTE_FONT}`;
  ctx.lineJoin = "round";
  ctx.lineWidth = 6;
  ctx.strokeStyle = "#ffffff";
  ctx.strokeText(text, x, y);
  ctx.fillStyle = CHECK_GREEN;
  ctx.fillText(text, x, y);
  ctx.restore();
}

// Check marks sit on the image: a dashed outline round each checked part with a
// tick at its corner and its name, and a badge at the top-left for the items
// about the whole picture.
function drawChecks(ctx, marks, geometry) {
  if (!marks) return;
  const { left, top, width, height } = geometry;
  ctx.save();
  ctx.textBaseline = "alphabetic";
  for (const region of marks.regions || []) {
    const [x0, y0, x1, y1] = region.box;
    const bx0 = left + x0 * width;
    const by0 = top + y0 * height;
    const bx1 = left + x1 * width;
    const by1 = top + y1 * height;
    ctx.save();
    ctx.setLineDash([10, 6]);
    ctx.lineWidth = 3;
    ctx.strokeStyle = CHECK_GREEN;
    ctx.strokeRect(bx0 - 3, by0 - 3, bx1 - bx0 + 6, by1 - by0 + 6);
    ctx.restore();

    const radius = Math.min(22, Math.max(14, Math.min(bx1 - bx0, by1 - by0) * 0.24));
    const cx = Math.min(bx1, left + width - radius - 3);
    const cy = Math.max(by0, top + radius + 3);
    drawTick(ctx, cx, cy, radius);

    if (region.label) {
      ctx.font = `700 ${CHECK_FONT_SIZE}px ${NOTE_FONT}`;
      const labelWidth = ctx.measureText(region.label).width;
      const x = Math.min(Math.max(bx0, left + 4), left + width - labelWidth - 4);
      const above = by0 - 10 > top + CHECK_FONT_SIZE;
      drawHaloText(ctx, region.label, x, above ? by0 - 10 : Math.min(by1 + CHECK_FONT_SIZE + 6, top + height - 6));
    }
  }
  if (marks.whole > 0) {
    const text = `全体 ${marks.whole}項目`;
    ctx.font = `700 ${CHECK_FONT_SIZE}px ${NOTE_FONT}`;
    const textWidth = ctx.measureText(text).width;
    const r = 17;
    const x = left + 12;
    const y = top + 12;
    ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
    ctx.beginPath();
    ctx.roundRect(x, y, r * 2 + textWidth + 26, r * 2 + 10, r + 5);
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = CHECK_GREEN;
    ctx.stroke();
    drawTick(ctx, x + r + 6, y + r + 5, r);
    ctx.fillStyle = CHECK_GREEN;
    ctx.fillText(text, x + r * 2 + 16, y + r + 5 + CHECK_FONT_SIZE * 0.35);
  }
  ctx.restore();
}

async function renderRedline(imageUrl, findings, marks) {
  const image = await loadImage(imageUrl);
  const imageWidth = IMAGE_WIDTH;
  const imageHeight = Math.max(1, Math.round((image.naturalHeight * imageWidth) / image.naturalWidth));

  const measure = document.createElement("canvas").getContext("2d");
  measure.font = `600 ${FONT_SIZE}px ${NOTE_FONT}`;

  const left = [];
  const right = [];
  const loose = [];
  findings.forEach((finding, index) => {
    const text = clip(finding.detail || finding.location, NOTE_LIMIT);
    if (!text) return;
    const box = usableBox(finding.box);
    if (!box) {
      loose.push(text);
      return;
    }
    const lines = wrapText(measure, text, NOTE_WIDTH);
    const note = {
      // The finding's own number, so a circle keeps its look when others are hidden.
      index: Number.isInteger(finding._index) ? finding._index : index,
      box,
      lines,
      width: Math.max(...lines.map((line) => measure.measureText(line).width)),
      height: lines.length * LINE_HEIGHT,
      centerY: PAD + ((box[1] + box[3]) / 2) * imageHeight,
      top: 0,
    };
    ((box[0] + box[2]) / 2 < 0.5 ? left : right).push(note);
  });

  const leftMargin = left.length ? PAD + NOTE_WIDTH + LEADER_GAP : PAD;
  const rightMargin = right.length ? PAD + NOTE_WIDTH + LEADER_GAP : PAD;
  const width = leftMargin + imageWidth + rightMargin;
  const imageTop = PAD;

  const columnBottom = Math.max(
    imageTop + imageHeight,
    left.length ? stackColumn(left, imageTop) : 0,
    right.length ? stackColumn(right, imageTop) : 0,
  );

  const footerLines = [];
  for (const text of loose) {
    footerLines.push(...wrapText(measure, `全体: ${text}`, width - PAD * 2));
  }
  const footerTop = columnBottom + 28;
  const height = Math.ceil((footerLines.length ? footerTop + footerLines.length * LINE_HEIGHT : columnBottom) + PAD);

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(image, leftMargin, imageTop, imageWidth, imageHeight);
  drawChecks(ctx, marks, { left: leftMargin, top: imageTop, width: imageWidth, height: imageHeight });

  ctx.strokeStyle = RED;
  ctx.fillStyle = RED;
  ctx.lineWidth = 4;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.font = `600 ${FONT_SIZE}px ${NOTE_FONT}`;
  ctx.textBaseline = "top";

  const draw = (note, side) => {
    const [x0, y0, x1, y1] = note.box;
    const center = {
      x: leftMargin + ((x0 + x1) / 2) * imageWidth,
      y: imageTop + ((y0 + y1) / 2) * imageHeight,
    };
    const rx = ((x1 - x0) * imageWidth) / 2 * 1.12 + 8;
    const ry = ((y1 - y0) * imageHeight) / 2 * 1.12 + 8;
    roughEllipse(ctx, center.x, center.y, rx, ry, note.index + 1);

    const textX = side === "left" ? PAD : leftMargin + imageWidth + LEADER_GAP;
    note.lines.forEach((line, row) => {
      ctx.fillText(line, textX, note.top + row * LINE_HEIGHT + 2);
    });
    const anchor =
      side === "left"
        ? { x: textX + note.width + 10, y: note.top + FONT_SIZE / 2 + 2 }
        : { x: textX - 10, y: note.top + FONT_SIZE / 2 + 2 };
    drawLeader(ctx, anchor, center, rx, ry, note.index + 1);
  };
  left.forEach((note) => draw(note, "left"));
  right.forEach((note) => draw(note, "right"));

  footerLines.forEach((line, row) => {
    ctx.fillText(line, PAD, footerTop + row * LINE_HEIGHT);
  });

  return canvas;
}

function downloadCanvas(canvas, filename) {
  canvas.toBlob((blob) => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, "image/png");
}
