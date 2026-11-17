const $ = (id) => document.getElementById(id);
const state = { regions: [], region: null, item: null, last: null, cart: [], lastMeasure: null, perUnit: false };

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) { /* JSON 이 아닌 오류 응답 */ }
  if (!res.ok) throw new Error(data.error || "요청에 실패했습니다.");
  return data;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const norm = (s) => (s || "").replace(/\s+/g, "").toLowerCase();
function showError(msg) { $("error").textContent = msg || ""; $("error").hidden = !msg; }
function chip(label, on, handler) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "chip" + (on ? " on" : ""); b.textContent = label;
  b.addEventListener("click", handler);
  return b;
}
const guard = (fn) => (...a) => Promise.resolve(fn(...a)).catch((e) => showError(e.message));

// ---------------------------------------------------------------- ① 지역 검색
async function loadRegions() {
  const [reg, health] = await Promise.all([api("/api/regions"), api("/api/health")]);
  state.regions = reg.regions;
  const banner = $("data-banner");
  if (health.refreshing) {
    banner.textContent = `공공데이터포털에서 최신 수수료 데이터를 받아오는 중입니다(${health.progress || "준비 중"}). 끝나면 새로고침해 주세요.`;
    banner.hidden = false;
  } else if (reg.sample_data) {
    banner.textContent = "시연용 샘플 데이터로 동작 중입니다. 표시되는 금액은 실제 금액이 아닙니다.";
    banner.hidden = false;
  }
}
function renderRegionResults() {
  const q = norm($("region-q").value);
  if (!q) { $("region-results").replaceChildren(); return; }
  const hits = state.regions.filter((r) => norm(r.id).includes(q)).slice(0, 12);
  if (!hits.length) { $("region-results").replaceChildren(); showError("일치하는 지역이 없습니다. 시·군·구 이름을 다시 확인해 주세요."); return; }
  showError("");
  $("region-results").replaceChildren(...hits.map((r) => chip(r.id, false, () => chooseRegion(r.id).catch((e) => showError(e.message)))));
}
async function chooseRegion(id) {
  state.region = id; state.cart = []; renderCart();
  $("region-q").value = ""; $("region-results").replaceChildren();
  $("region-chosen").textContent = `선택한 지역: ${id}`; $("region-chosen").hidden = false;
  $("item-card").hidden = false;
  resetBelow();
  const data = await api("/api/items?region=" + encodeURIComponent(id));
  $("items").replaceChildren(new Option("선택하세요", ""), ...data.items.map((i) => new Option(i, i)));
}
function resetBelow() {
  state.item = null; $("spec-card").hidden = true; $("result-card").hidden = true;
  $("matches").replaceChildren(); $("ai-result").replaceChildren(); showError("");
}

// ---------------------------------------------------------------- ② 품목: 사진 추천 → 직접 검색
async function identify() {
  showError("");
  const file = $("photo").files[0];
  if (!file) { showError("사진을 먼저 선택해 주세요."); return; }
  const out = $("ai-result");
  out.textContent = "사진을 분석하는 중입니다…";
  $("identify").disabled = true;
  try {
    const fd = new FormData();
    fd.append("image", file); fd.append("region", state.region);
    const data = await api("/api/identify", { method: "POST", body: fd });
    out.replaceChildren();
    if (data.candidates.length) {
      const head = document.createElement("p"); head.className = "hint";
      head.textContent = "AI 추천 품목입니다. 맞는 것을 눌러 주세요. 맞지 않으면 아래에서 직접 검색하거나 선택하세요.";
      out.append(head);
      data.candidates.forEach((c) => {
        const row = document.createElement("div"); row.className = "cand";
        row.append(chip(`${c.item} (${Math.round(c.confidence * 100)}%)`, false, () => chooseItem(c.item).catch((e) => showError(e.message))));
        if (c.evidence) { const ev = document.createElement("span"); ev.className = "ev"; ev.textContent = c.evidence; row.append(ev); }
        out.append(row);
      });
    } else {
      const p = document.createElement("p"); p.className = "hint";
      p.textContent = data.message || "추천할 품목이 없습니다. 아래에서 직접 검색하거나 선택해 주세요.";
      out.append(p);
    }
  } catch (e) { out.replaceChildren(); showError(e.message); }
  finally { $("identify").disabled = false; }
}
function showPreview() {
  const file = $("photo").files[0];
  if (!file) { $("preview").hidden = true; return; }
  $("preview").src = URL.createObjectURL(file); $("preview").hidden = false;
}

async function search() {
  showError("");
  const q = $("query").value.trim();
  if (!q) return;
  const data = await api(`/api/items?region=${encodeURIComponent(state.region)}&q=${encodeURIComponent(q)}`);
  if (!data.matches.length) { $("matches").replaceChildren(); showError("일치하는 품목을 찾지 못했습니다. 아래 목록에서 직접 선택해 주세요."); return; }
  $("matches").replaceChildren(...data.matches.map((m) => chip(m.item, false, () => chooseItem(m.item).catch((e) => showError(e.message)))));
}

// ---------------------------------------------------------------- ③ 규격 → ④ 수수료
async function chooseItem(item) {
  showError(""); state.item = item; $("items").value = item;
  const q = await api(`/api/spec-question?region=${encodeURIComponent(state.region)}&item=${encodeURIComponent(item)}`);
  $("spec-card").hidden = false; $("result-card").hidden = true;
  $("spec-options").replaceChildren(...q.options.map((o) => chip(o, false, () => calc({ spec: o }))));
  $("measure-box").hidden = !q.measure_kind;
  $("measure-label").textContent = (q.measure_label ? `실측값: ${q.measure_label}` : "실측값") + (q.measure_hint ? ` — ${q.measure_hint}` : "");
  state.perUnit = !!q.per_unit;
  if (!q.needs_question) calc({ spec: q.options[0] });
}

async function calc(extra) {
  showError("");
  try {
    const r = await post("/api/fee", { region: state.region, item: state.item, ...extra });
    state.last = r; state.lastMeasure = extra.measure ? Number(extra.measure) : null;
    $("fee").textContent = r.fee_text;
    $("fee").classList.toggle("pending", !!r.conflict);
    const b = r.basis;
    const rows = [["지역", b.region], ["품목", b.item], ["분류", b.category || "-"], ["규격", `${b.spec} (${b.decided_by})`],
      ...(b.calc_note ? [["계산", b.calc_note]] : []),
      ["데이터 기준일", b.data_date], ["제공기관", b.provider], ["출처", b.source]];
    $("basis").replaceChildren(...rows.flatMap(([k, v]) => {
      const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd");
      if (k === "출처") {
        const a = document.createElement("a"); a.href = b.source_url; a.target = "_blank"; a.rel = "noopener"; a.textContent = v;
        dd.append(a);
      } else dd.textContent = v;
      return [dt, dd];
    }));
    // 조례 별표 원문 링크(있는 지자체만). 주소는 법제처 일련번호로 만든 것이라 textContent/href 로만 넣는다.
    const ol = b.ordinance_links || [];
    $("ordin-box").hidden = ol.length === 0;
    $("ordin").replaceChildren(...ol.map((l) => {
      const li = document.createElement("li"), a = document.createElement("a");
      a.href = l.url; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = l.name;
      li.append(a); return li;
    }));
    // 근거 안내: 쉬운 설명 문장, 경계값 주의, 다른 규격과의 비교 (모두 textContent 로만 넣는다)
    $("explain").textContent = r.explanation || "";
    $("boundary").textContent = r.boundary_warning || "";
    $("boundary").hidden = !r.boundary_warning;
    const alts = r.alternatives || [];
    $("alt-box").hidden = alts.length === 0;
    $("alt").replaceChildren(...alts.map((a) => {
      const tr = document.createElement("tr");
      [a.spec, a.fee_text, a.diff_text || "-"].forEach((t, i) => {
        const td = document.createElement("td"); td.textContent = t;
        if (i === 2 && a.diff > 0) td.className = "up"; else if (i === 2 && a.diff < 0) td.className = "down";
        tr.append(td);
      });
      if (a.conflict) tr.className = "conf";
      return tr;
    }));
    $("copy-msg").textContent = "";
    // 안내 상자: 평소에는 일반 안내 문구, 금액이 확정되지 않은 경우(같은 규격에 금액이 둘 이상)에는 경고(후보 금액 포함)로 대체
    $("notice").textContent = r.notice;
    $("notice").hidden = !!r.conflict;
    $("conflict").hidden = !r.conflict;
    if (r.conflict) {
      $("conflict").textContent = r.notice + " 등록된 금액: " +
        r.candidates.map((c) => `${c.fee_text}(${c.data_date}${c.category ? ", " + c.category : ""})`).join(" / ");
    }
    $("add").hidden = !!r.conflict;
    $("result-card").hidden = false;
  } catch (e) { $("result-card").hidden = true; showError(e.message); }
}

function renderCart() {
  $("cart-card").hidden = state.cart.length === 0;
  $("cart").replaceChildren(...state.cart.map((c) => { const li = document.createElement("li"); li.textContent = `${c.item} · ${c.spec} · ${c.fee_text}`; return li; }));
}
async function refreshTotal() {
  if (!state.cart.length) return renderCart();
  const t = await post("/api/fee/total", { region: state.region, entries: state.cart.map((c) => ({ item: c.item, spec: c.spec, measure: c.measure })) });
  $("total").textContent = "합계 " + t.total_text + (t.unconfirmed ? ` (금액 확인이 필요한 ${t.unconfirmed}건 제외)` : ""); renderCart();
}

$("region-q").addEventListener("input", renderRegionResults);
$("region-q").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const hits = state.regions.filter((r) => norm(r.id).includes(norm($("region-q").value)));
  if (hits.length === 1) guard(chooseRegion)(hits[0].id);
});
$("identify").addEventListener("click", guard(identify));
$("photo").addEventListener("change", showPreview);
$("items").addEventListener("change", (e) => e.target.value && guard(chooseItem)(e.target.value));
$("search").addEventListener("click", guard(search));
$("query").addEventListener("keydown", (e) => e.key === "Enter" && guard(search)());
$("measure-go").addEventListener("click", () => calc({ measure: $("measure").value }));
$("add").addEventListener("click", () => {
  if (!state.last) return;
  state.cart.push({ item: state.last.basis.item, spec: state.last.basis.spec, measure: state.perUnit ? state.lastMeasure : null, fee_text: state.last.fee_text });
  refreshTotal().catch((e) => showError(e.message));
});
async function copyEvidence() {
  if (!state.last || !state.last.copy_text) return;
  const text = state.last.copy_text;
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) { // 클립보드 권한이 없거나 http 환경이면 임시 입력칸으로 복사
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.append(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (!ok) { $("copy-msg").textContent = "복사하지 못했습니다. 직접 선택해 복사해 주세요."; return; }
  }
  $("copy-msg").textContent = "복사했습니다.";
}
$("copy").addEventListener("click", copyEvidence);
$("clear").addEventListener("click", () => { state.cart = []; renderCart(); });
guard(loadRegions)();
