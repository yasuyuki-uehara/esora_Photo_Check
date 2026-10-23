const drop = document.querySelector("#drop");
const fileInput = document.querySelector("#file");
const browse = document.querySelector("#browse");
const focusChecks = document.querySelector("#focus-checks");
const modelSelect = document.querySelector("#model");
const queue = document.querySelector("#queue");
const checkButton = document.querySelector("#check");
const clearButton = document.querySelector("#clear");
const results = document.querySelector("#results");
const session = document.querySelector("#session");
const referenceDrop = document.querySelector("#reference-drop");
const referenceInput = document.querySelector("#reference-file");
const referenceBrowse = document.querySelector("#reference-browse");
const referenceEmpty = document.querySelector("#reference-empty");
const referencePicked = document.querySelector("#reference-picked");
const referencePreview = document.querySelector("#reference-preview");
const referenceName = document.querySelector("#reference-name");
const referenceMeta = document.querySelector("#reference-meta");
const referenceClear = document.querySelector("#reference-clear");

const items = [];
let reference = null;
let busy = false;

referenceBrowse.addEventListener("click", () => referenceInput.click());
referenceClear.addEventListener("click", clearReference);
referenceInput.addEventListener("change", () => {
  const file = referenceInput.files && referenceInput.files[0];
  if (file) setReference(file);
  referenceInput.value = "";
});
referenceDrop.addEventListener("dragover", (event) => {
  event.preventDefault();
  referenceDrop.classList.add("over");
});
referenceDrop.addEventListener("dragleave", () => referenceDrop.classList.remove("over"));
referenceDrop.addEventListener("drop", (event) => {
  event.preventDefault();
  referenceDrop.classList.remove("over");
  const file = [...(event.dataTransfer.files || [])].find((item) => isImage(item));
  if (file) setReference(file);
});

focusChecks.addEventListener("change", (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || input.type !== "checkbox") return;
  const role = input.dataset.role;
  if (role === "all") {
    setAllChecks(input.checked);
    return;
  }
  if (role === "group") {
    setGroupChecks(input.dataset.group, input.checked);
    syncAllCheck();
    return;
  }
  if (role === "item") {
    syncGroupCheck(input.dataset.group);
    syncAllCheck();
  }
});

browse.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => addFiles(fileInput.files));
drop.addEventListener("dragover", (event) => {
  event.preventDefault();
  drop.classList.add("over");
});
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  drop.classList.remove("over");
  addFiles(event.dataTransfer.files);
});
checkButton.addEventListener("click", runChecks);
clearButton.addEventListener("click", clearAll);

loadSession();
loadModels();

function isImage(file) {
  return ["image/png", "image/jpeg", "image/webp"].includes(file.type);
}

function checkboxes(selector) {
  return [...focusChecks.querySelectorAll(selector)];
}

function setAllChecks(checked) {
  for (const input of checkboxes('input[type="checkbox"]')) input.checked = checked;
}

function setGroupChecks(group, checked) {
  for (const input of checkboxes(`input[data-group="${group}"]`)) input.checked = checked;
}

function syncGroupCheck(group) {
  const items = checkboxes(`input[data-role="item"][data-group="${group}"]`);
  const parent = focusChecks.querySelector(`input[data-role="group"][data-group="${group}"]`);
  if (!parent || !items.length) return;
  parent.checked = items.every((input) => input.checked);
}

function syncAllCheck() {
  const all = focusChecks.querySelector('input[data-role="all"]');
  const others = checkboxes('input[type="checkbox"]:not([data-role="all"])');
  if (!all || !others.length) return;
  all.checked = others.every((input) => input.checked);
}

function selectedFocusNote() {
  const lines = [];
  const bodyItems = checkboxes('input[data-role="item"][data-group="body"]:checked').map((input) => input.dataset.label);
  const decoItems = checkboxes('input[data-role="item"][data-group="deco"]:checked').map((input) => input.dataset.label);
  const bodyGroup = focusChecks.querySelector('input[data-role="group"][data-group="body"]');
  const decoGroup = focusChecks.querySelector('input[data-role="group"][data-group="deco"]');
  if (bodyGroup && bodyGroup.checked) {
    lines.push("人体の構造を細かく確認する");
  }
  for (const label of bodyItems) lines.push(`・${label}`);
  if (decoGroup && decoGroup.checked) {
    lines.push("装飾のチェックを細かく確認する");
  }
  for (const label of decoItems) lines.push(`・${label}`);
  if (!lines.length) return "";
  return (
    "次の項目を特に注意して確認する。問題があれば必ず指摘する。\n" +
    lines.join("\n") +
    "\n服の色や装飾のデザインの違いは差分、パースの崩れはレイアウトか差分として出す。"
  );
}

function setReference(file) {
  if (!isImage(file)) return;
  if (reference) URL.revokeObjectURL(reference.url);
  reference = { file, url: URL.createObjectURL(file) };
  referencePreview.src = reference.url;
  referenceName.textContent = file.name;
  referenceMeta.textContent = formatSize(file.size);
  referenceEmpty.hidden = true;
  referencePicked.hidden = false;
}

function clearReference() {
  if (!reference) return;
  URL.revokeObjectURL(reference.url);
  reference = null;
  referencePreview.removeAttribute("src");
  referenceEmpty.hidden = false;
  referencePicked.hidden = true;
}

function addFiles(list) {
  for (const file of list || []) {
    if (!isImage(file)) continue;
    items.push({
      id: crypto.randomUUID(),
      file,
      url: URL.createObjectURL(file),
    });
  }
  fileInput.value = "";
  renderQueue();
}

function renderQueue() {
  queue.hidden = items.length === 0;
  queue.replaceChildren(
    ...items.map((item) => {
      const row = document.createElement("div");
      row.className = "queue-item";
      const image = document.createElement("img");
      image.src = item.url;
      image.alt = "";
      const text = document.createElement("div");
      const name = document.createElement("p");
      name.className = "name";
      name.textContent = item.file.name;
      const meta = document.createElement("p");
      meta.className = "meta";
      meta.textContent = formatSize(item.file.size);
      text.append(name, meta);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "icon-button";
      remove.textContent = "削除";
      remove.addEventListener("click", () => removeItem(item.id));
      row.append(image, text, remove);
      return row;
    }),
  );
  const hasItems = items.length > 0 && !busy;
  checkButton.disabled = !hasItems;
  clearButton.disabled = !hasItems;
}

function removeItem(id) {
  const index = items.findIndex((item) => item.id === id);
  if (index < 0) return;
  URL.revokeObjectURL(items[index].url);
  items.splice(index, 1);
  renderQueue();
}

function clearAll() {
  for (const item of items) URL.revokeObjectURL(item.url);
  items.splice(0);
  results.replaceChildren(emptyState());
  renderQueue();
}

async function runChecks() {
  if (!items.length || busy) return;
  busy = true;
  renderQueue();
  const batch = items.slice();
  const compared = reference;
  results.replaceChildren();
  for (const item of batch) {
    const card = renderCard(item, { state: "pending", reference: compared });
    results.append(card);
    card.scrollIntoView({ block: "nearest" });
    try {
      const body = await review(item.file, compared);
      renderCard(item, body.ok ? { state: "done", body, reference: compared } : { state: "error", message: body.error || "確認に失敗しました。", reference: compared }, card);
    } catch (error) {
      renderCard(item, { state: "error", message: error.message || "確認に失敗しました。", reference: compared }, card);
    }
  }
  busy = false;
  renderQueue();
}

async function review(file, compared) {
  const data = await fileToBase64(file);
  const request = {
    filename: file.name,
    mime: file.type,
    note: selectedFocusNote(),
    model: modelSelect.value,
    data_base64: data,
  };
  if (compared) {
    request.reference_mime = compared.file.type;
    request.reference_base64 = await fileToBase64(compared.file);
  }
  const response = await fetch("/api/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) await loadSession();
  if (!response.ok && payload.ok !== false) {
    throw new Error(payload.error || `確認に失敗しました (${response.status})`);
  }
  return payload;
}

function renderCard(item, view, existing) {
  const card = existing || document.createElement("article");
  const compared = view.reference;
  card.className = compared ? "result compared" : "result";
  card.replaceChildren();
  const body = document.createElement("div");
  body.className = "body";
  const title = document.createElement("h2");
  title.textContent = item.file.name;
  body.append(title);
  const media = document.createElement("div");
  fillMedia(media, item, view);
  body.append(media);

  if (view.state === "pending") {
    const pending = document.createElement("p");
    pending.className = "pending";
    pending.textContent = "確認しています…";
    body.append(pending);
  } else if (view.state === "error") {
    const error = document.createElement("p");
    error.className = "error-text";
    error.textContent = view.message;
    body.append(error);
  } else {
    const summary = document.createElement("p");
    summary.className = "summary";
    summary.textContent = view.body.summary || "";
    body.append(summary);
    if (view.body.warning) {
      const warning = document.createElement("p");
      warning.className = "warning-text";
      warning.textContent = view.body.warning;
      body.append(warning);
    }
    const findings = view.body.findings || [];
    if (findings.length >= 20) {
      const redo = document.createElement("div");
      redo.className = "redo-banner";
      redo.setAttribute("role", "alert");
      const redoTitle = document.createElement("p");
      redoTitle.className = "redo-title";
      redoTitle.textContent = "やりなおせーーーー！！";
      const redoCopy = document.createElement("p");
      redoCopy.className = "redo-copy";
      redoCopy.textContent = `チェックNGが ${findings.length} 件あります。`;
      redo.append(redoTitle, redoCopy);
      body.append(redo);
    }
    if (!findings.length) {
      const ok = document.createElement("p");
      ok.className = "ok";
      ok.textContent = "指摘はありません。";
      body.append(ok);
    } else {
      const list = document.createElement("ul");
      list.className = "findings";
      for (const finding of findings) {
        const row = document.createElement("li");
        row.className = `finding ${finding.severity || "low"}`;
        const tag = document.createElement("div");
        tag.className = "tag";
        tag.textContent = `${labelSeverity(finding.severity)} · ${finding.category || "指摘"} · ${finding.location || ""}`;
        const detail = document.createElement("p");
        detail.textContent = finding.detail || "";
        row.append(tag, detail);
        list.append(row);
      }
      body.append(list);
    }
  }
  card.append(body);
  return card;
}

// Pending, failed and clean results show the plain image(s). A result with
// findings shows the image marked up in red, with the reference beside it.
function fillMedia(media, item, view) {
  const compared = view.reference;
  const findings = view.state === "done" ? view.body.findings || [] : [];
  media.replaceChildren();
  if (!findings.length) {
    media.className = compared ? "result-media pair" : "result-media";
    media.append(shot(item.url, item.file.name, compared ? "確認画像" : ""));
    if (compared) media.append(shot(compared.url, compared.file.name, "参考画像"));
    return;
  }

  media.className = "result-media redline";
  const holder = document.createElement("div");
  holder.className = "redline-holder";
  holder.textContent = "赤入れ画像を作っています…";
  media.append(holder);
  if (compared) {
    const figure = shot(compared.url, compared.file.name, "参考画像");
    figure.classList.add("reference-shot");
    media.append(figure);
  }

  renderRedline(item.url, findings)
    .then((canvas) => {
      canvas.className = "redline-canvas";
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", `${item.file.name} の赤入れ画像（指摘 ${findings.length} 件）`);
      const save = document.createElement("button");
      save.type = "button";
      save.className = "button secondary save-redline";
      save.textContent = "赤入れ画像を保存";
      save.addEventListener("click", () => downloadCanvas(canvas, `${baseName(item.file.name)}_赤入れ.png`));
      holder.replaceChildren(canvas, save);
    })
    .catch(() => {
      holder.replaceChildren(shot(item.url, item.file.name, ""));
    });
}

function baseName(name) {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

function shot(url, name, caption) {
  if (!caption) {
    const image = document.createElement("img");
    image.src = url;
    image.alt = name;
    return image;
  }
  const figure = document.createElement("figure");
  figure.className = "shot";
  const image = document.createElement("img");
  image.src = url;
  image.alt = name;
  const label = document.createElement("figcaption");
  label.textContent = caption;
  figure.append(image, label);
  return figure;
}

function emptyState() {
  const paragraph = document.createElement("p");
  paragraph.className = "empty";
  paragraph.textContent = "結果はここに出ます。";
  return paragraph;
}

async function loadSession() {
  session.textContent = "確認中…";
  try {
    const response = await fetch("/api/status");
    const payload = await response.json();
    if (!payload.signed_in) {
      session.replaceChildren(document.createTextNode("未サインイン"), loginButton());
      return;
    }
    const email = payload.email || "サインイン済み";
    if (payload.expired) {
      session.replaceChildren(document.createTextNode(`${email}（再サインインが必要）`), loginButton());
      return;
    }
    session.textContent = email;
  } catch {
    session.textContent = "接続を確認できませんでした。";
  }
}

function loginButton() {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button secondary";
  button.textContent = "サインイン";
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "ブラウザでサインイン…";
    try {
      const response = await fetch("/api/login", { method: "POST" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "サインインに失敗しました。");
      await loadSession();
      await loadModels();
    } catch (error) {
      session.replaceChildren(document.createTextNode(error.message), loginButton());
    }
  });
  return button;
}

async function loadModels() {
  try {
    const response = await fetch("/api/models");
    if (!response.ok) return;
    const payload = await response.json();
    const models = payload.models || [];
    modelSelect.replaceChildren(
      ...models.map((item) => {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.label;
        if (item.id === "gemini_3_5_flash_text") option.selected = true;
        return option;
      }),
    );
  } catch {
    /* The check request reports a missing model list itself. */
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = String(reader.result || "");
      const comma = value.indexOf(",");
      resolve(comma >= 0 ? value.slice(comma + 1) : "");
    };
    reader.onerror = () => reject(new Error("画像を読めませんでした。"));
    reader.readAsDataURL(file);
  });
}

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function labelSeverity(severity) {
  return { high: "高", medium: "中", low: "低" }[severity] || "中";
}
