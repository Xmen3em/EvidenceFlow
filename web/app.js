const $ = (id) => document.getElementById(id);
const kinds = { po: "Purchase order", invoice: "Invoice", receipt: "Goods receipt" };
const fields = { supplier: "Supplier", po_ref: "PO reference", total: "Total", item: "Item" };
const decisions = { follow_up: "Request follow-up", close_review: "Close evidence review" };
const casePanels = ["findings", "documents", "review", "activity", "mcp"];
const state = { user: null, cases: [], active: null, detail: null, panel: "findings", visit: 0 };
let toastTimer;
let polling = false;
let sourceSelection;

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value !== undefined) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function button(value, className, callback) {
  const node = element("button", value, className);
  node.type = "button";
  node.addEventListener("click", () => perform(node, callback));
  return node;
}

function displayDate(value) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function notify(value) {
  $("notice").textContent = value;
  $("notice").hidden = !value;
  clearTimeout(toastTimer);
  if (value) toastTimer = setTimeout(() => { $("notice").hidden = true; }, 6500);
}

function showAuth() {
  state.user = null;
  state.active = null;
  state.detail = null;
  state.cases = [];
  state.visit += 1;
  clearGrant();
  for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  for (const id of ["cases", "rail-cases", "finding-list", "document-list", "proposal-list", "activity-list", "investigation-output"]) {
    $(id).replaceChildren();
  }
  $("identity-name").textContent = "";
  $("identity-icon").textContent = "";
  $("case-title").textContent = "";
  $("case-ref").textContent = "";
  for (const id of ["login", "case-form", "upload-form", "proposal", "investigation"]) $(id).reset();
  $("workspace").hidden = true;
  $("auth").hidden = false;
  document.querySelector(".skip").href = "#auth";
}

async function api(path, { method = "GET", json, body, headers = {} } = {}) {
  const response = await fetch("/api/" + path, {
    method, credentials: "same-origin",
    headers: json === undefined ? headers : { ...headers, "Content-Type": "application/json" },
    body: json === undefined ? body : JSON.stringify(json),
  });
  if (response.status === 204) return null;
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && state.user) showAuth();
    throw new Error(result.detail || "The request could not be completed.");
  }
  return result;
}

async function perform(control, callback) {
  if (control) control.disabled = true;
  try {
    await callback();
  } catch (error) {
    notify(error.message || "The request could not be completed.");
  } finally {
    if (control) control.disabled = false;
  }
}

const casePath = (id = state.active) => "cases/" + encodeURIComponent(id);

function clearGrant() {
  $("grant-box").hidden = true;
  $("grant-value").value = "";
}

function renderRegister() {
  const rail = $("rail-cases");
  const cards = $("cases");
  rail.replaceChildren();
  cards.replaceChildren();
  for (const item of state.cases) {
    const link = button(item.title, "rail-link", () => openCase(item.id));
    link.prepend(element("small", item.reference));
    if (state.active === item.id) link.setAttribute("aria-current", "page");
    rail.append(link);
    const card = button(undefined, "case-card", () => openCase(item.id));
    card.append(element("small", item.reference), element("strong", item.title),
      element("small", item.document_count + (item.document_count === 1 ? " document" : " documents")));
    cards.append(card);
  }
  if (!state.cases.length) {
    cards.append(element("p", "No cases yet. Create a case to start collecting evidence.", "block"));
  }
}

async function openRegister() {
  state.active = null;
  state.detail = null;
  state.visit += 1;
  const visit = state.visit;
  clearGrant();
  $("case-view").hidden = true;
  $("register").hidden = false;
  $("breadcrumb").textContent = "Workspace / Case register";
  const cases = await api("cases");
  if (visit !== state.visit) return;
  state.cases = cases;
  renderRegister();
}

async function enterWorkspace() {
  state.user = await api("session");
  $("identity-name").textContent = state.user.name;
  $("identity-role").textContent = state.user.role;
  $("identity-icon").textContent = Array.from(state.user.name)[0].toUpperCase();
  $("hf-button").hidden = !state.user.hf_enabled;
  $("hf-consent").hidden = !state.user.hf_enabled;
  const local = ["localhost", "127.0.0.1"].includes(window.location.hostname);
  const remoteCommand = "uv run --locked python -m app.cli mcp-inspect --url " + window.location.origin + "/protocol/mcp";
  $("mcp-command").textContent = local && window.location.port === "8080"
    ? "docker compose exec app python -m app.cli mcp-inspect"
    : remoteCommand;
  $("workspace").hidden = false;
  $("auth").hidden = true;
  document.querySelector(".skip").href = "#main";
  await openRegister();
}

async function openCase(id) {
  const visit = ++state.visit;
  const detail = await api(casePath(id));
  if (visit !== state.visit) return;
  state.active = id;
  state.detail = detail;
  clearGrant();
  $("investigation-output").replaceChildren();
  $("investigation").reset();
  $("proposal").reset();
  $("register").hidden = true;
  $("case-view").hidden = false;
  renderRegister();
  renderCase();
  setPanel("findings");
  $("main").focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

function originalLink(source) {
  const link = element("a", source.filename + " · p. " + source.page + ", line " + source.line);
  link.href = "/api/" + casePath() + "/documents/" + encodeURIComponent(source.document_id) + "/original";
  link.setAttribute("download", "");
  return link;
}

function displayValue(field, value) {
  if (field === "total") return value.currency + " " + value.amount;
  if (field === "item") return value.sku + " | " + value.qty;
  return value;
}

function sourceList(sources) {
  const list = element("div", undefined, "sources");
  for (const source of sources) {
    const item = element("div", undefined, "source");
    item.append(originalLink(source), element("code", source.quote));
    if (source.source_method === "ocr") item.append(element("small", "OCR transcription"));
    if (source.extracted_value !== undefined) {
      item.append(element("small", "Reviewer value · " + displayValue(source.field, source.value), "reviewer-value"));
    }
    const doc = state.detail.documents.find((record) => record.id === source.document_id);
    const pdf = doc?.media_type === "application/pdf";
    const index = doc?.facts?.fields.findIndex((fact) => fact.page === source.page && fact.line === source.line) ?? -1;
    if (pdf || index >= 0) {
      item.append(button(pdf ? "Inspect source page" : "Inspect field", "source-button secondary",
        () => openSource(source, pdf, index >= 0 ? index : null)));
    }
    list.append(item);
  }
  return list;
}

async function openSource(source, pdf, index) {
  const selection = { caseId: state.active, revision: state.detail.case.revision, source, index };
  sourceSelection = selection;
  const form = $("correction-form");
  form.reset();
  form.hidden = index === null || state.user.role !== "reviewer";
  $("correction-error").textContent = "";
  $("correction-format").textContent = { total: "Format · USD 1200.00", item: "Format · SKU | quantity" }[source.field] || "";
  form.elements.value.value = displayValue(source.field, source.value);
  $("source-title").textContent = fields[source.field] + " source";
  const content = element("div", undefined, "source");
  content.append(originalLink(source), element("code", source.quote));
  if (source.source_method === "ocr") content.append(element("small", "OCR transcription. Compare with the page image."));
  if (source.extracted_value !== undefined) {
    content.append(element("small", "Extracted value · " + displayValue(source.field, source.extracted_value)));
    content.append(element("small", "Reviewer value · " + displayValue(source.field, source.value), "reviewer-value"));
  }
  $("source-detail").replaceChildren(content);
  const page = $("source-page");
  $("source-preview-error").textContent = "";
  page.hidden = !pdf;
  if (pdf) {
    page.alt = source.filename + ", page " + source.page;
    page.src = "/api/" + casePath() + "/documents/" + source.document_id + "/pages/" + source.page;
  } else {
    page.removeAttribute("src");
  }
  $("correction-history").hidden = true;
  $("correction-history").replaceChildren();
  $("source-dialog").showModal();
  if (index === null || source.extracted_value === undefined) return;
  const history = await api(fieldPath(selection) + "/corrections");
  if (selection !== sourceSelection) return;
  const section = $("correction-history");
  section.hidden = false;
  section.append(element("h3", "Correction history"));
  for (const entry of history) {
    const row = element("div", undefined, "source");
    row.append(element("strong", displayValue(source.field, entry.value)),
      element("p", entry.reason),
      element("small", entry.reviewer + " · revision " + entry.revision + " · " + displayDate(entry.created_at)));
    section.append(row);
  }
}

function fieldPath(selection) {
  return casePath(selection.caseId) + "/documents/" + selection.source.document_id + "/fields/" + selection.index;
}

function findingCard(finding) {
  const card = element("article", undefined, "finding");
  const top = element("div", undefined, "row-top");
  top.append(element("h3", finding.message), element("span", finding.level === "gap" ? "Evidence gap" : "Difference", "pill " + finding.level));
  card.append(top);
  if (finding.sources.length) card.append(sourceList(finding.sources));
  return card;
}

function renderDocuments() {
  const list = $("document-list");
  list.replaceChildren();
  for (const doc of state.detail.documents) {
    const row = element("article", undefined, "row");
    const top = element("div", undefined, "row-top");
    top.append(element("h3", doc.filename), element("span", doc.status, "pill" + (doc.status === "failed" ? " difference" : "")));
    const original = element("a", "Download original");
    original.href = "/api/" + casePath() + "/documents/" + doc.id + "/original";
    original.setAttribute("download", "");
    row.append(top, element("p", kinds[doc.kind], "muted"), original);
    if (doc.error) row.append(element("p", doc.error, "error"));
    if (doc.facts?.fields.length) {
      const details = element("details", undefined, "details");
      details.dataset.document = doc.id;
      details.append(element("summary", doc.facts.fields.length + " extracted fields"));
      const fields = doc.facts.fields.map((field) => ({ ...field, filename: doc.filename, document_id: doc.id }));
      details.append(sourceList(fields));
      row.append(details);
    } else if (doc.status === "ready") {
      row.append(element("p", "No supported fields found. Check the file format.", "muted"));
    }
    list.append(row);
  }
  if (!list.children.length) list.append(element("p", "Upload a document to start extraction.", "block"));
}

function renderProposals() {
  const list = $("proposal-list");
  list.replaceChildren();
  for (const proposal of state.detail.proposals) {
    const row = element("article", undefined, "row");
    const top = element("div", undefined, "row-top");
    const current = proposal.revision === state.detail.case.revision;
    const status = proposal.status === "pending" && !current ? "Stale" : proposal.status;
    top.append(element("h3", decisions[proposal.decision]), element("span", status, "pill"));
    row.append(top, element("small", "Revision " + proposal.revision + " · " + proposal.proposed_by + " · " + displayDate(proposal.created_at)), element("p", proposal.note));
    if (proposal.decided_by) row.append(element("p", "Decided by " + proposal.decided_by, "muted"));
    if (proposal.status === "pending" && state.user.role === "reviewer" && !proposal.is_mine) {
      const actions = element("div", undefined, "actions");
      for (const verdict of current ? ["approved", "rejected"] : ["rejected"]) {
        actions.append(button(verdict === "approved" ? "Approve" : "Reject", verdict === "approved" ? "primary" : "secondary", async () => {
          const id = state.active;
          await api(casePath(id) + "/proposals/" + proposal.id + "/decision", { method: "POST", json: { status: verdict } });
          notify("Decision recorded.");
          if (id === state.active) await refreshCase();
        }));
      }
      row.append(actions);
    }
    list.append(row);
  }
  if (!list.children.length) list.append(element("p", "No review proposals yet.", "block"));
}

function renderCase() {
  const { case: record, review } = state.detail;
  $("breadcrumb").textContent = "Case register / " + record.reference;
  $("case-title").textContent = record.title;
  $("case-ref").textContent = record.reference;
  const currentDecision = state.detail.proposals.find((item) => item.revision === record.revision && item.status === "approved");
  const disposition = currentDecision ? " · Approved: " + decisions[currentDecision.decision] : "";
  $("case-revision").textContent = "Evidence revision " + record.revision + disposition + " · " + review.scope;
  $("metrics").replaceChildren();
  const metrics = [
    [review.documents, "Documents"],
    [review.findings.filter((item) => item.level === "difference").length, "Differences"],
    [review.findings.filter((item) => item.level === "gap").length, "Evidence gaps"],
  ];
  for (const [amount, label] of metrics) {
    const metric = element("div", undefined, "metric");
    metric.append(element("strong", amount), element("small", label));
    $("metrics").append(metric);
  }
  $("finding-list").replaceChildren(...review.findings.map(findingCard));
  if (!review.findings.length) {
    $("finding-list").append(element("p", "No differences in the supported fields. Check the originals during review.", "block"));
  }
  renderDocuments();
  renderProposals();
}

async function refreshCase() {
  const id = state.active;
  const visit = state.visit;
  const detail = await api(casePath(id));
  if (visit !== state.visit) return;
  if (detail.case.revision !== state.detail.case.revision && $("investigation-output").children.length) {
    $("investigation-output").replaceChildren(element("p", "Evidence changed. Run a new investigation.", "muted"));
  }
  state.detail = detail;
  renderCase();
}

async function loadActivity() {
  const visit = state.visit;
  const events = await api(casePath() + "/activity");
  if (visit !== state.visit || state.panel !== "activity") return;
  const list = $("activity-list");
  list.replaceChildren();
  for (const event of events) {
    const row = element("div", undefined, "row");
    row.append(element("strong", event.event), element("p", (event.actor || "Extraction worker") + " · " + displayDate(event.created_at), "muted"));
    if (event.metadata.revision !== undefined) row.append(element("small", "Revision " + event.metadata.revision));
    list.append(row);
  }
  if (!events.length) list.append(element("p", "No activity yet.", "block"));
}

function setPanel(name) {
  state.panel = name;
  for (const panel of casePanels) $(panel).hidden = panel !== name;
  for (const tab of document.querySelectorAll("[data-panel]")) {
    tab.setAttribute("aria-pressed", String(tab.dataset.panel === name));
  }
  if (name === "review") void perform(null, refreshCase);
  if (name === "activity") {
    $("activity-list").replaceChildren(element("p", "Loading activity…", "muted"));
    void perform(null, loadActivity);
  }
}

function bindForm(id, callback) {
  $(id).addEventListener("submit", (event) => {
    event.preventDefault();
    void perform(event.submitter, () => callback(event.currentTarget || $(id), event.submitter));
  });
}

bindForm("login", async (form) => {
  $("login-error").textContent = "";
  try {
    await api("session", { method: "POST", json: Object.fromEntries(new FormData(form)) });
    form.reset();
    await enterWorkspace();
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});

bindForm("case-form", async (form) => {
  const record = await api("cases", { method: "POST", json: Object.fromEntries(new FormData(form)) });
  $("case-dialog").close();
  state.cases = await api("cases");
  await openCase(record.id);
  notify("Case created.");
});

bindForm("upload-form", async (form) => {
  const id = state.active;
  const file = form.elements.file.files[0];
  if (!file?.size || file.size > 5 * 1024 * 1024) throw new Error("Choose a nonempty file of up to 5 MiB.");
  await api(casePath(id) + "/documents?kind=" + encodeURIComponent(form.elements.kind.value), {
    method: "POST", headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) }, body: file,
  });
  $("upload-dialog").close();
  notify("Document queued for extraction.");
  if (id === state.active) {
    setPanel("documents");
    await refreshCase();
  }
});

bindForm("proposal", async (form) => {
  const id = state.active;
  await api(casePath(id) + "/proposals", {
    method: "POST", json: { ...Object.fromEntries(new FormData(form)), revision: state.detail.case.revision },
  });
  form.reset();
  notify("Proposal submitted for a different reviewer.");
  if (id === state.active) await refreshCase();
});

bindForm("correction-form", async (form) => {
  const selection = sourceSelection;
  const data = Object.fromEntries(new FormData(form));
  $("correction-error").textContent = "";
  try {
    await api(fieldPath(selection), { method: "PATCH", json: { ...data, revision: selection.revision } });
  } catch (error) {
    if (selection === sourceSelection) $("correction-error").textContent = error.message;
    return;
  }
  if (selection !== sourceSelection) return;
  $("source-dialog").close();
  await refreshCase();
  const details = state.panel === "documents"
    ? $("document-list").querySelector(`[data-document="${CSS.escape(selection.source.document_id)}"]`)
    : null;
  if (details) {
    details.open = true;
    details.querySelector("summary").focus();
  } else {
    document.querySelector(`[data-panel="${state.panel}"]`)?.focus();
  }
  notify("Correction saved. Findings reflect the new evidence revision.");
});

bindForm("investigation", async (form, submitter) => {
  const visit = state.visit;
  const mode = submitter?.value || "offline";
  if (mode === "hugging_face" && !form.elements.consent.checked) throw new Error("Confirm external processing before sending evidence.");
  for (const control of form.querySelectorAll("button")) control.disabled = true;
  $("investigation-output").replaceChildren(element("p", "Reading evidence through MCP…", "muted"));
  try {
    const result = await api(casePath() + "/investigate", {
      method: "POST", json: { question: form.elements.question.value, mode, allow_external: form.elements.consent.checked },
    });
    if (visit !== state.visit) return;
    const label = result.mode === "offline" ? "Offline rules" : "AI commentary · verify against the findings";
    $("investigation-output").replaceChildren(
      element("h3", label + " · revision " + result.revision), element("p", result.summary),
      ...result.citations.map(findingCard),
    );
  } catch (error) {
    if (visit === state.visit) $("investigation-output").replaceChildren(element("p", error.message, "error"));
  } finally {
    for (const control of form.querySelectorAll("button")) control.disabled = false;
    form.elements.consent.checked = false;
  }
});

$("logout").addEventListener("click", () => perform($("logout"), async () => {
  await api("session", { method: "DELETE" });
  showAuth();
  notify("Signed out.");
}));
$("home-button").addEventListener("click", () => perform($("home-button"), openRegister));
$("new-case").addEventListener("click", () => { $("case-form").reset(); $("case-dialog").showModal(); });
$("add-doc").addEventListener("click", () => { $("upload-form").reset(); $("upload-dialog").showModal(); });
$("reload-activity").addEventListener("click", () => perform($("reload-activity"), loadActivity));
$("create-grant").addEventListener("click", () => perform($("create-grant"), async () => {
  const visit = state.visit;
  const result = await api(casePath() + "/mcp-grants", { method: "POST" });
  if (visit !== state.visit) return;
  $("grant-box").hidden = false;
  $("grant-value").value = result.token;
  $("grant-value").focus();
  $("grant-value").select();
}));
$("revoke-grants").addEventListener("click", () => perform($("revoke-grants"), async () => {
  await api(casePath() + "/mcp-grants", { method: "DELETE" });
  clearGrant();
  notify("Your tokens for this case were revoked.");
}));

for (const tab of document.querySelectorAll("[data-panel]")) tab.addEventListener("click", () => setPanel(tab.dataset.panel));
for (const close of document.querySelectorAll("[data-close]")) close.addEventListener("click", () => close.closest("dialog").close());
$("source-dialog").addEventListener("close", () => { sourceSelection = null; $("source-page").removeAttribute("src"); });
$("source-page").addEventListener("error", () => {
  $("source-page").hidden = true;
  $("source-preview-error").textContent = "Preview unavailable. Download the original to inspect this page.";
});

setInterval(async () => {
  if (polling || !state.active || document.hidden ||
      !state.detail?.documents.some((doc) => ["queued", "processing"].includes(doc.status))) return;
  polling = true;
  try { await refreshCase(); } catch (error) { notify(error.message); } finally { polling = false; }
}, 2500);

enterWorkspace().catch(() => showAuth());
