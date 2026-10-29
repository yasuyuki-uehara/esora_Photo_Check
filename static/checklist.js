// The reviewer's own checklist, shown under the check button.
//
// Ticking an item also puts a check mark on the image, at the parts that item
// is about. `targets` says which parts: "all" is the whole picture, "skin" is
// the face, hands, arms and legs, and the rest are the region kinds returned by
// the server's locate pass (person, face, eyes, hair, hands, arms, legs,
// clothes, accessories, structures).

const SKIN_KINDS = ["face", "hands", "arms", "legs"];

// `children` may hold items or { label, items } sub-headings.
const CHECKLIST = [
  {
    title: "全体",
    items: [
      { id: "all-spec", text: "制作内容・仕様・参考資料を確認した", targets: ["all"] },
      { id: "all-latest", text: "最新の指示・修正内容を反映している", targets: ["all"] },
      { id: "all-ui", text: "UI画面に当てはめてチェックした", targets: ["all"] },
      {
        id: "all-ai",
        text: "AIっぽさが残っていないか確認した",
        targets: ["all"],
        children: [
          {
            label: "人物",
            items: [
              { id: "ai-perspective", text: "パース／デッサン", targets: ["person"] },
              { id: "ai-count", text: "指や腕等の本数", targets: ["hands", "arms"] },
              { id: "ai-skin", text: "肌の色", targets: ["skin"] },
              { id: "ai-body", text: "人体の構成", targets: ["person"] },
              { id: "ai-hair", text: "髪の長さ／変なところから生えていないか等", targets: ["hair"] },
              { id: "ai-sheet", text: "設定資料との齟齬がないか", targets: ["person"] },
            ],
          },
          {
            label: "背景関連",
            items: [{ id: "ai-structure", text: "構造物の構成", targets: ["structures"] }],
          },
        ],
      },
    ],
  },
  {
    title: "構図",
    items: [
      { id: "comp-pose", text: "ポーズ・表情・視線が意図に合っている", targets: ["person", "face"] },
      { id: "comp-drawing", text: "デッサンが正しいか確認した", targets: ["person"] },
      {
        id: "comp-lead",
        text: "主役や見せたい要素が伝わる配置になっている",
        note: "（三角構図やひねりなど、キャラクターを魅力的に見せる構図になっているか）",
        targets: ["person"],
      },
      { id: "comp-limbs", text: "手足の長さに違和感がなく、体のつまりがないか確認した", targets: ["arms", "legs"] },
    ],
  },
  {
    title: "線画",
    items: [
      { id: "line-hands", text: "手指は特に注意深く確認した", targets: ["hands"] },
      { id: "line-face", text: "顔まわりが似せられており、かわいく仕上がっているか確認した", targets: ["face"] },
      { id: "line-rough", text: "確認済みの構図・ラフを反映している", targets: ["all"] },
      { id: "line-shape", text: "顔・身体の形に不自然な箇所がない", targets: ["face", "person"] },
      { id: "line-costume", text: "衣装・小物の形や細部が設定どおりになっている", targets: ["clothes", "accessories"] },
      {
        id: "line-fix",
        text: "修正稿の場合",
        targets: ["all"],
        children: [{ items: [{ id: "line-fix-other", text: "FB以外の箇所も変わっていないかどうか", targets: ["all"] }] }],
      },
    ],
  },
  {
    title: "着彩",
    items: [
      {
        id: "color-spec",
        text: "指定色・配色・参考資料を反映している",
        targets: ["all"],
        children: [
          {
            items: [
              { id: "color-hair", text: "髪", targets: ["hair"] },
              { id: "color-skin", text: "肌", targets: ["skin"] },
              { id: "color-eyes", text: "瞳", targets: ["eyes"] },
              { id: "color-clothes", text: "服", targets: ["clothes"] },
            ],
          },
        ],
      },
      { id: "color-others", text: "他キャラと比較して色味に差がないか確認した", targets: ["all"] },
      { id: "color-eye-light", text: "瞳のハイライトの形状と角度が左右で合っているか確認した", targets: ["eyes"] },
      { id: "color-contrast", text: "全体の明るさ・コントラストが意図に合っている", targets: ["all"] },
      { id: "color-nails", text: "爪の塗りを確認した", targets: ["hands"] },
      {
        id: "color-fix",
        text: "修正稿の場合",
        targets: ["all"],
        children: [
          {
            items: [
              {
                id: "color-fix-other",
                text: "FB以外の箇所も変わっていないか",
                targets: ["all"],
                children: [
                  {
                    items: [
                      { id: "color-fix-tone", text: "色味", targets: ["all"] },
                      { id: "color-fix-taste", text: "着彩テイスト", targets: ["all"] },
                      { id: "color-fix-highlight", text: "ハイライトの入り方", targets: ["all"] },
                    ],
                  },
                ],
              },
            ],
          },
        ],
      },
    ],
  },
];

const KIND_LABELS = {
  person: "人物",
  face: "顔",
  eyes: "目",
  hair: "髪",
  hands: "手",
  arms: "腕",
  legs: "脚",
  clothes: "服",
  accessories: "小物",
  structures: "背景",
};

const Checklist = (() => {
  const checked = new Set();
  const byId = new Map();
  const parentOf = new Map();
  const inputs = new Map();
  let notify = () => {};
  let counter = null;

  function walk(nodes, parent) {
    for (const node of nodes) {
      if (node.items) {
        walk(node.items, parent);
        continue;
      }
      byId.set(node.id, node);
      if (parent) parentOf.set(node.id, parent);
      if (node.children) walk(node.children, node.id);
    }
  }
  for (const section of CHECKLIST) walk(section.items, null);

  function childItems(item) {
    const out = [];
    for (const group of item.children || []) {
      for (const child of group.items || [group]) out.push(child);
    }
    return out;
  }

  function descendants(item) {
    const out = [];
    for (const child of childItems(item)) {
      out.push(child, ...descendants(child));
    }
    return out;
  }

  function setChecked(id, value) {
    if (value) checked.add(id);
    else checked.delete(id);
  }

  function sync() {
    // A parent mirrors its children: ticked when every child is, partly when some are.
    const parents = [...byId.values()].filter((item) => item.children);
    parents.sort((a, b) => descendants(b).length - descendants(a).length);
    for (const item of parents.reverse()) {
      const kids = childItems(item);
      const on = kids.filter((kid) => checked.has(kid.id)).length;
      const partial = kids.some((kid) => inputs.get(kid.id)?.indeterminate);
      setChecked(item.id, on === kids.length);
      const input = inputs.get(item.id);
      if (input) input.indeterminate = on !== kids.length && (on > 0 || partial);
    }
    for (const [id, input] of inputs) input.checked = checked.has(id);
    if (counter) counter.textContent = `${checked.size} / ${byId.size}`;
  }

  function onToggle(item, value) {
    setChecked(item.id, value);
    for (const child of descendants(item)) setChecked(child.id, value);
    sync();
    notify();
  }

  function renderItem(item) {
    const wrapper = document.createElement("div");
    const label = document.createElement("label");
    label.className = "check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.addEventListener("change", () => onToggle(item, input.checked));
    inputs.set(item.id, input);
    const text = document.createElement("span");
    text.textContent = item.text;
    if (item.note) {
      const note = document.createElement("small");
      note.className = "check-note";
      note.textContent = item.note;
      text.append(note);
    }
    label.append(input, text);
    wrapper.append(label);
    if (item.children) {
      const box = document.createElement("div");
      box.className = "check-children";
      for (const group of item.children) {
        if (group.label) {
          const heading = document.createElement("p");
          heading.className = "check-group-label";
          heading.textContent = group.label;
          box.append(heading);
        }
        for (const child of group.items) box.append(renderItem(child));
      }
      wrapper.append(box);
    }
    return wrapper;
  }

  function mount(container, onChange) {
    notify = onChange || notify;
    container.replaceChildren();

    const head = document.createElement("div");
    head.className = "manual-head";
    const title = document.createElement("h2");
    title.textContent = "目視チェック";
    counter = document.createElement("span");
    counter.className = "manual-count";
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "icon-button";
    reset.textContent = "すべて外す";
    reset.addEventListener("click", () => resetAll());
    head.append(title, counter, reset);
    container.append(head);

    const hint = document.createElement("p");
    hint.className = "manual-hint";
    hint.textContent = "チェックを入れると、結果の画像の該当する箇所にもチェックマークが付きます。";
    container.append(hint);

    for (const section of CHECKLIST) {
      const block = document.createElement("section");
      block.className = "manual-section";
      const heading = document.createElement("h3");
      heading.textContent = `■${section.title}`;
      block.append(heading);
      for (const item of section.items) block.append(renderItem(item));
      container.append(block);
    }
    sync();
  }

  function resetAll() {
    checked.clear();
    sync();
    notify();
  }

  function expand(targets) {
    return targets.flatMap((target) => (target === "skin" ? SKIN_KINDS : [target]));
  }

  // Check marks for the image: one per located part (with the part's name), plus a
  // whole-image count for items about the whole picture or about a part that was
  // not found in the image, so every ticked item leaves a trace.
  function marksFor(regions) {
    const list = Array.isArray(regions) ? regions : [];
    const marked = new Map();
    let whole = 0;
    for (const id of checked) {
      const item = byId.get(id);
      if (!item) continue;
      let placed = false;
      for (const kind of expand(item.targets)) {
        if (kind === "all") continue;
        list.forEach((region, index) => {
          if (region.kind !== kind || !Array.isArray(region.box)) return;
          marked.set(index, region);
          placed = true;
        });
      }
      if (!placed) whole += 1;
    }
    return {
      regions: [...marked.values()].map((region) => ({
        box: region.box,
        label: region.label || KIND_LABELS[region.kind] || "",
      })),
      whole,
    };
  }

  function hasMarks(marks) {
    return marks.whole > 0 || marks.regions.length > 0;
  }

  return { mount, marksFor, hasMarks, reset: resetAll, checkedCount: () => checked.size };
})();
