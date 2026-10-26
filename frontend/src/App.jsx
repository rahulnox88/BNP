import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BadgeDollarSign,
  ClipboardList,
  Clock3,
  Flame,
  LayoutDashboard,
  ListChecks,
  LoaderCircle,
  PackageCheck,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  UserRoundCheck,
  Workflow,
  Wrench,
} from "lucide-react";
import { api, loadWorkspace } from "./api.js";

const NAV = [
  ["overview", "Overview", LayoutDashboard],
  ["claims", "Claim queue", ClipboardList],
  ["agent_completed", "Agent completed", ListChecks],
  ["review", "Human review", UserRoundCheck],
  ["actions", "Demo actions", BadgeDollarSign],
  ["warranties", "Warranties", ShieldCheck],
  ["audit", "Audit trail", Activity],
  ["usage", "Token usage", Workflow],
  ["policy", "Policy", SlidersHorizontal],
];
const TITLE = {
  overview: "Warranty desk",
  claims: "Claim queue",
  agent_completed: "Agent completed claims",
  review: "Human review queue",
  actions: "Demo actions",
  warranties: "Warranty records",
  audit: "Audit trail",
  usage: "Token consumption",
  policy: "Policy",
};
const REVIEW_STATUSES = new Set(["awaiting_review", "needs_review"]);
const QUEUED_STATUSES = new Set(["new", "error"]);
const AUTO_DECISIONS = new Set(["auto_refund", "auto_credit", "auto_replacement"]);
const isAgentAutoResolved = (claim) =>
  claim.status === "resolved" && AUTO_DECISIONS.has(claim.decision) && !claim.reviewer && Boolean(claim.action);

const money = (value) => (Number.isFinite(Number(value)) ? `$${Number(value).toFixed(2)}` : "—");
const pretty = (value) => String(value || "unknown").replaceAll("_", " ");
const claimIntentLabel = (claim) =>
  claim.intent ||
  (["processing", "assessing"].includes(claim.status)
    ? "analyzing"
    : claim.status === "error"
      ? "analysis failed"
      : "not analyzed");
const claimIntentFilterValue = (claim) => claim.intent || "not analyzed";
const dateTime = (value) => (value ? new Date(value).toLocaleString() : "—");
const dateShort = (value) => (value ? new Date(value).toLocaleDateString() : "—");

function Badge({ value }) {
  return <span className={`badge badge-${String(value || "unknown").replaceAll("_", "-")}`}>{pretty(value)}</span>;
}

function Eyebrow({ children }) {
  return <span className="eyebrow">{children}</span>;
}

function Panel({ title, note, action, className = "", children }) {
  return (
    <section className={`panel ${className}`}>
      {(title || note || action) && (
        <header className="panel-head">
          <div>
            {title && <h2>{title}</h2>}
            {note && <p>{note}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

function Stat({ label, value, className = "" }) {
  return (
    <article className={`stat-card ${className}`}>
      <span className="stat-label">{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function Empty({ children }) {
  return <div className="empty">{children}</div>;
}

function App() {
  const [data, setData] = useState(null);
  const [view, setView] = useState("overview");
  const [provider, setProvider] = useState("inhouse");
  const [selectedClaim, setSelectedClaim] = useState(null);
  const [detailEvents, setDetailEvents] = useState([]);
  const [detailActions, setDetailActions] = useState([]);
  const [detailReviews, setDetailReviews] = useState([]);
  const [reviewer, setReviewer] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [overrideNote, setOverrideNote] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [intentFilter, setIntentFilter] = useState("all");
  const [warrantySearch, setWarrantySearch] = useState("");
  const [eligibility, setEligibility] = useState("all");
  const [auditSearch, setAuditSearch] = useState("");
  const [auditFilter, setAuditFilter] = useState("all");
  const [auditClaimId, setAuditClaimId] = useState("");
  const [auditReviews, setAuditReviews] = useState([]);
  const [actionClaim, setActionClaim] = useState("all");
  const [batchSize, setBatchSize] = useState("10");
  const [batchProgress, setBatchProgress] = useState(null);
  const [busy, setBusy] = useState(false);
  const [busyClaim, setBusyClaim] = useState("");
  const [clearingData, setClearingData] = useState(false);
  const [toast, setToast] = useState(null);
  const [loadError, setLoadError] = useState("");
  const selectionRef = useRef("");

  const refresh = useCallback(async () => {
    const next = await loadWorkspace();
    setData(next);
    setLoadError("");
    const availableProviders = next.config?.providers || {};
    const saved = localStorage.getItem("warranty-provider");
    setProvider((current) =>
      saved && availableProviders[saved]
        ? saved
        : availableProviders[current]
          ? current
          : next.config?.provider || "inhouse",
    );
    return next;
  }, []);

  useEffect(() => {
    refresh().catch((error) => setLoadError(error.message));
  }, [refresh]);

  useEffect(() => {
    if (view !== "audit") return undefined;
    const claimId = auditClaimId || data?.claims?.[0]?.claim_id;
    if (!claimId) {
      setAuditReviews([]);
      return undefined;
    }
    let active = true;
    api(`/api/claims/${encodeURIComponent(claimId)}/reviews`)
      .then((next) => {
        if (active) setAuditReviews(next);
      })
      .catch(() => {
        if (active) setAuditReviews([]);
      });
    return () => {
      active = false;
    };
  }, [view, auditClaimId, data?.claims]);

  useEffect(() => {
    if (!busy) return undefined;
    const timer = window.setInterval(() => {
      loadWorkspace()
        .then((next) => {
          setData(next);
          setLoadError("");
          const availableProviders = next.config?.providers || {};
          const saved = localStorage.getItem("warranty-provider");
          setProvider((current) =>
            saved && availableProviders[saved]
              ? saved
              : availableProviders[current]
                ? current
                : next.config?.provider || "inhouse",
          );
        })
        .catch(() => {});
    }, 2200);
    return () => window.clearInterval(timer);
  }, [busy]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const claims = data?.claims || [];
  const actions = data?.actions || [];
  const events = data?.events || [];
  const warranties = data?.warranties || [];
  const policy = data?.config?.policy || {
    policy_version: "—",
    auto_approval_limit_usd: 100,
    refunds_require_human_approval: true,
    credits_require_human_approval: true,
    replacements_require_human_approval: true,
    supported_warranty_categories: [],
  };
  const providers = data?.config?.providers || {};
  const demoDataResetEnabled = Boolean(data?.config?.demo_data_reset_enabled);
  const providerConfig = providers[provider];
  const providerReady = Boolean(providerConfig?.available);
  const queued = useMemo(() => claims.filter((claim) => QUEUED_STATUSES.has(claim.status)), [claims]);
  const reviews = useMemo(() => claims.filter((claim) => REVIEW_STATUSES.has(claim.status)), [claims]);
  const agentCompleted = useMemo(() => claims.filter(isAgentAutoResolved), [claims]);
  const selectedWarranty = (productRef) => warranties.find((item) => item.product_ref === productRef);

  const notify = (message, error = false) => setToast({ message, error });

  async function loadClaimHistory(claimId) {
    const path = `/api/claims/${encodeURIComponent(claimId)}`;
    const [events, actions, reviews] = await Promise.all([
      api(`${path}/events`),
      api(`${path}/actions`),
      api(`${path}/reviews`),
    ]);
    return { events, actions, reviews };
  }

  function showClaimDetail(claim, history) {
    setSelectedClaim(claim);
    setDetailEvents(history.events);
    setDetailActions(history.actions);
    setDetailReviews(history.reviews);
  }

  async function runClaimCommand(claimId, path, body, message) {
    setBusy(true);
    setBusyClaim(claimId);
    try {
      const result = await api(path, {
        method: "POST",
        body: JSON.stringify(body),
      });
      await refresh();
      showClaimDetail(result.claim, await loadClaimHistory(claimId));
      notify(message);
      return true;
    } catch (error) {
      notify(error.message, true);
      await refresh().catch(() => {});
      return false;
    } finally {
      setBusy(false);
      setBusyClaim("");
    }
  }

  async function selectClaim(claimId, destinationView = "claims") {
    selectionRef.current = claimId;
    setSelectedClaim(null);
    setOverrideNote("");
    setView(destinationView);
    setReviewer("");
    setReviewNote("");
    try {
      const [claim, history] = await Promise.all([
        api(`/api/claims/${encodeURIComponent(claimId)}`),
        loadClaimHistory(claimId),
      ]);
      if (selectionRef.current !== claimId) return;
      showClaimDetail(claim, history);
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function processOne(claimId) {
    if (!providerReady)
      return notify(
        `Configure ${providerConfig?.requires_api_key ? "the selected API key in .env" : "Ollama and its model"} first.`,
        true,
      );
    await runClaimCommand(
      claimId,
      `/api/claims/${encodeURIComponent(claimId)}/process`,
      { provider },
      `${claimId} assessment completed.`,
    );
  }

  async function submitReview(approved) {
    if (!selectedClaim || !reviewer.trim() || !reviewNote.trim())
      return notify("Enter your name and a review note first.", true);
    const claimId = selectedClaim.claim_id;
    await runClaimCommand(
      claimId,
      `/api/claims/${encodeURIComponent(claimId)}/review`,
      {
        provider,
        approved,
        reviewer: reviewer.trim(),
        note: reviewNote.trim(),
      },
      approved ? "Review approved; simulated action recorded." : "Claim denial recorded.",
    );
  }

  async function overrideDecision(decision) {
    if (!selectedClaim || !reviewer.trim() || !overrideNote.trim())
      return notify("Enter your name and an override note first.", true);
    const claimId = selectedClaim.claim_id;
    const reverseAgentAction = isAgentAutoResolved(selectedClaim);
    const succeeded = await runClaimCommand(
      claimId,
      reverseAgentAction
        ? `/api/claims/${encodeURIComponent(claimId)}/reverse`
        : `/api/claims/${encodeURIComponent(claimId)}/override`,
      reverseAgentAction
        ? { reviewer: reviewer.trim(), note: overrideNote.trim() }
        : { decision, reviewer: reviewer.trim(), note: overrideNote.trim() },
      reverseAgentAction
        ? "Agent action reversed. Claim moved to the denied section."
        : "Human override recorded.",
    );
    if (succeeded && reverseAgentAction) {
      selectionRef.current = "";
      setSelectedClaim(null);
      setStatusFilter("denied");
      setView("claims");
    }
  }

  async function processBatch() {
    if (!providerReady) return notify("Configure the selected LLM provider before processing.", true);
    const limit = batchSize === "all" ? queued.length : Math.min(Number(batchSize), queued.length);
    const claimIds = queued.slice(0, limit).map((claim) => claim.claim_id);
    if (!claimIds.length) return notify("There are no unprocessed claims in the queue.");
    setBusy(true);
    try {
      let job = await api("/api/claims/process-batch", {
        method: "POST",
        body: JSON.stringify({ provider, claim_ids: claimIds }),
      });
      setBatchProgress(job);
      let refreshedAt = 0;
      while (job.status === "queued" || job.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        job = await api(`/api/batch-jobs/${encodeURIComponent(job.job_id)}`);
        setBatchProgress(job);
        if (job.completed - refreshedAt >= 5) {
          await refresh();
          refreshedAt = job.completed;
        }
      }
      await refresh();
      notify(
        `${job.processed} of ${job.total} claim(s) processed; ${job.errors.length} error(s).${job.errors.length ? ` First error: ${job.errors[0].error}` : ""}`,
        job.errors.length > 0,
      );
    } catch (error) {
      notify(error.message, true);
    } finally {
      setBusy(false);
      setBatchProgress(null);
    }
  }

  async function importClaims() {
    try {
      const result = await api("/api/claims/import", { method: "POST" });
      await refresh();
      notify(`Imported ${result.inserted} new claims.`);
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function clearAllData() {
    if (clearingData || busy) return;
    const confirmed = window.confirm(
      "Clear all saved claims, decisions, actions, reviews, token usage and audit history? The original 90 seed claims will be reloaded as new. This cannot be undone.",
    );
    if (!confirmed) return;
    setClearingData(true);
    try {
      const result = await api("/api/admin/clear-data", { method: "POST" });
      selectionRef.current = "";
      setSelectedClaim(null);
      setDetailEvents([]);
      setDetailActions([]);
      setDetailReviews([]);
      setReviewer("");
      setReviewNote("");
      setOverrideNote("");
      await refresh();
      notify(`Saved data cleared. ${result.claims} seed claims are ready for a fresh test.`);
    } catch (error) {
      notify(error.message, true);
    } finally {
      setClearingData(false);
    }
  }

  function changeProvider(value) {
    setProvider(value);
    localStorage.setItem("warranty-provider", value);
    const info = providers[value];
    const ready = Boolean(info?.available);
    const availability = ready
      ? "available"
      : value === "ollama"
        ? `model ${info?.model || ""} not detected; start Ollama and pull the model`
        : value === "inhouse"
          ? "set INHOUSE_LLM_BASE_URL in .env and restart the API"
          : `API key missing; set ${value === "openai" ? "OPENAI_API_KEY" : "GROQ_API_KEY"} in .env and restart the API`;
    const label =
      value === "ollama"
        ? "Ollama · local"
        : value === "inhouse"
          ? "In-house"
          : value[0].toUpperCase() + value.slice(1);
    notify(`${label} selected · ${availability}`, !ready);
  }

  const filteredClaims = useMemo(() => {
    const query = search.trim().toLowerCase();
    return claims.filter(
      (claim) =>
        (statusFilter === "all" || claim.status === statusFilter) &&
        (intentFilter === "all" || claimIntentFilterValue(claim) === intentFilter) &&
        `${claim.claim_id} ${claim.customer_name} ${claim.product_ref} ${claim.claim_text}`
          .toLowerCase()
          .includes(query),
    );
  }, [claims, search, statusFilter, intentFilter]);

  const filteredWarranties = useMemo(
    () =>
      warranties.filter((item) => {
        const eligible = ["true", "yes", "1"].includes(String(item.credit_eligible).toLowerCase());
        return (
          (eligibility === "all" || (eligibility === "yes") === eligible) &&
          `${item.product_ref} ${item.customer_name} ${item.product_name} ${item.service_plan} ${item.claim_category}`
            .toLowerCase()
            .includes(warrantySearch.toLowerCase())
        );
      }),
    [warranties, eligibility, warrantySearch],
  );

  const filteredEvents = useMemo(
    () =>
      events.filter((event) => {
        const details = event.details || {};
        const matchesText = `${event.claim_id} ${event.actor} ${event.event_type} ${JSON.stringify(details)}`
          .toLowerCase()
          .includes(auditSearch.toLowerCase());
        const matchesType =
          auditFilter === "all" ||
          (auditFilter === "failed" &&
            (event.event_type.includes("failed") || event.event_type === "workflow_error")) ||
          (auditFilter === "review" && event.event_type.includes("review")) ||
          (auditFilter === "action" && ["action_taken", "action_reversed"].includes(event.event_type));
        return matchesText && matchesType;
      }),
    [events, auditSearch, auditFilter],
  );

  const batchLimit = batchSize === "all" ? queued.length : Math.min(Number(batchSize), queued.length);
  const total = claims.length;
  const resolved = claims.filter((claim) => claim.status === "resolved").length;
  const denied = claims.filter((claim) => claim.status === "denied").length;
  const pending = claims.filter((claim) => ["new", "error", "processing"].includes(claim.status)).length;
  const automated = agentCompleted.length;
  const outflow = actions.reduce((sum, action) => sum + Number(action.amount || 0), 0);
  const reversedActionIds = new Set(
    actions.filter((action) => action.action_type === "reversal").map((action) => action.details?.reverses_action_id),
  );
  const replacements = actions.filter(
    (action) => action.action_type === "replacement" && !reversedActionIds.has(action.action_id),
  ).length;
  const pageSubtitles = {
    overview:
      "Current totals from the claim ledger. Processing mode is shown below; historical decisions retain their original mode.",
    claims:
      "New claims have not been interpreted yet. Run the agent on a claim or batch to extract the request and product, check warranty evidence, and apply policy.",
    agent_completed: "Agent-resolved claims and their evidence. Select one to review the action and override it to denied.",
    review: "Claims the rules engine cannot safely resolve on its own.",
    actions: "Refunds, credits, and replacement schedules in the simulated action ledger.",
    warranties: "Coverage on file, matched to claims by product reference.",
    audit: "Every rule evaluation, tool call, provider event, and reviewer decision.",
    usage: "Provider-reported token totals for each agent call.",
    policy: "Configured limits that decide what the rules engine may resolve without a person.",
  };

  function pageIntro(eyebrow, title, description, extra = null) {
    return (
      <div className="page-intro">
        <Eyebrow>{eyebrow}</Eyebrow>
        <h2>{title}</h2>
        <p>{description}</p>
        {extra}
      </div>
    );
  }

  function onOverview() {
    const reviewPct = total ? (reviews.length / total) * 100 : 0;
    const pendingPct = total ? (pending / total) * 100 : 0;
    const resolvedPct = total ? (resolved / total) * 100 : 0;
    const deniedPct = total ? (denied / total) * 100 : 0;
    const statusRows = [
      ["Review", reviews.length, reviewPct, "review"],
      ["Pending", pending, pendingPct, "pending"],
      ["Resolved", resolved, resolvedPct, "resolved"],
      ["Denied", denied, deniedPct, "denied"],
    ];
    return (
      <>
        <div className="stats-grid">
          <Stat label="TOTAL CLAIMS" value={total} />
          <Stat label="AWAITING PROCESSING" value={pending} />
          <Stat label="NEEDS HUMAN REVIEW" value={reviews.length} className="stat-review" />
          <Stat label="AUTOMATION RATE" value={total ? `${((automated / total) * 100).toFixed(1)}%` : "0%"} />
        </div>
        <div className="overview-grid">
          <div className="overview-left">
            <Panel
              title={<Eyebrow>STATUS DISTRIBUTION</Eyebrow>}
              note={`${total} claims`}
              className="distribution-panel"
            >
              <div className="distribution-body">
                <div className="distribution-bar">
                  {statusRows.map(([name, count, width, key]) => (
                    <span
                      key={key}
                      className={`bar-${key}`}
                      style={{ width: `${width}%` }}
                      title={`${name}: ${count}`}
                    />
                  ))}
                </div>
                <div className="distribution-legend">
                  {statusRows.map(([name, count, share, key]) => (
                    <div className="legend-row" key={key}>
                      <span>
                        <i className={`legend-${key}`} />
                        {name}
                      </span>
                      <small>
                        {count} · {share.toFixed(1)}%
                      </small>
                    </div>
                  ))}
                </div>
              </div>
            </Panel>
            <Panel title={<Eyebrow>SIMULATED OUTFLOW</Eyebrow>} className="flow-panel">
              <div className="flow-metrics">
                <div>
                  <small>Resolved value (demo)</small>
                  <b>{money(outflow)}</b>
                </div>
                <div>
                  <small>Replacements scheduled</small>
                  <b>{replacements}</b>
                </div>
                <div>
                  <small>Resolved</small>
                  <b>{resolved}</b>
                </div>
                <div>
                  <small>Rejected</small>
                  <b>{denied}</b>
                </div>
              </div>
              <button className="text-link" onClick={() => setView("actions")}>
                View demo action ledger <ArrowRight size={13} />
              </button>
            </Panel>
          </div>
          <Panel
            title={<Eyebrow>RECENT EVENTS</Eyebrow>}
            action={
              <button className="text-link" onClick={() => setView("audit")}>
                Full audit trail
              </button>
            }
            className="recent-panel"
          >
            <EventList events={events.slice(0, 12)} onSelect={(id) => selectClaim(id)} compact />
          </Panel>
        </div>
      </>
    );
  }

  function claimDetailPanel(backLabel) {
    return (
      <ClaimDetail
        claim={selectedClaim}
        events={detailEvents}
        actions={detailActions}
        reviews={detailReviews}
        providerReady={providerReady}
        busy={busyClaim === selectedClaim.claim_id}
        reviewer={reviewer}
        reviewNote={reviewNote}
        overrideNote={overrideNote}
        setOverrideNote={setOverrideNote}
        setReviewer={setReviewer}
        setReviewNote={setReviewNote}
        process={() => processOne(selectedClaim.claim_id)}
        review={submitReview}
        override={overrideDecision}
        backLabel={backLabel}
        back={() => {
          selectionRef.current = "";
          setSelectedClaim(null);
        }}
      />
    );
  }

  function onQueue() {
    if (selectedClaim) return claimDetailPanel("Claim queue");
    const eligibleAmount = (claim) => {
      const record = claim.warranty || selectedWarranty(claim.product_ref);
      return record?.credit_value > 0 ? money(record.credit_value) : "—";
    };
    return (
      <>
        {pageIntro(
          "INTAKE",
          "Claim queue",
          pageSubtitles.claims,
          <button className="button secondary" onClick={importClaims}>
            ＋ Import seed claims
          </button>,
        )}
        <Panel className="claim-queue-panel">
          <div className="queue-filters">
            <label className="search-box">
              <Search size={15} />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search claims, customers, products"
              />
            </label>
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              aria-label="Filter status"
            >
              <option value="all">All statuses</option>
              {["new", "error", "awaiting_review", "needs_review", "resolved", "denied"].map((value) => (
                <option key={value} value={value}>
                  {pretty(value)}
                </option>
              ))}
            </select>
            <select
              value={intentFilter}
              onChange={(event) => setIntentFilter(event.target.value)}
              aria-label="Filter intent"
            >
              <option value="all">All intents</option>
              {["refund", "credit", "replacement", "unclear", "not analyzed"].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </div>
          <small className="record-count">
            {filteredClaims.length} claim
            {filteredClaims.length === 1 ? "" : "s"}
          </small>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>CLAIM</th>
                  <th>CUSTOMER / PRODUCT</th>
                  <th>STATUS</th>
                  <th>INTENT</th>
                  <th>AMOUNT</th>
                  <th>FILED</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filteredClaims.map((claim) => {
                  const record = claim.warranty || selectedWarranty(claim.product_ref) || {};
                  return (
                    <tr className="claim-row" key={claim.claim_id} onClick={() => selectClaim(claim.claim_id)}>
                      <td>
                        <b>{claim.claim_id}</b>
                        <small className="claim-preview">{claim.issue_summary || claim.claim_text}</small>
                      </td>
                      <td className="customer-cell">
                        <b>{claim.customer_name}</b>
                        <small>
                          {record.product_name || claim.product_ref} · {claim.product_ref}
                        </small>
                      </td>
                      <td>
                        <Badge value={claim.status} />
                      </td>
                      <td>
                        <span className={`intent-tag ${claim.intent ? "" : "intent-unprocessed"}`}>
                          {claimIntentLabel(claim)}
                        </span>
                      </td>
                      <td className="amount-cell">{eligibleAmount(claim)}</td>
                      <td>{dateShort(claim.claim_date)}</td>
                      <td className="arrow">›</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!filteredClaims.length && <Empty>No claims match this filter.</Empty>}
          </div>
        </Panel>
      </>
    );
  }

  function onReview() {
    return (
      <>
        {pageIntro("ESCALATIONS", "Human review queue", pageSubtitles.review)}
        <div className="review-queue">
          {reviews.length ? (
            reviews.map((claim) => (
              <button className="review-card" key={claim.claim_id} onClick={() => selectClaim(claim.claim_id)}>
                <span className="review-customer">
                  <code>{claim.claim_id}</code>
                  <b>{claim.customer_name}</b>
                  <small>
                    {claim.product_ref} · {dateShort(claim.claim_date)}
                  </small>
                  <em>{claim.warranty?.product_name || selectedWarranty(claim.product_ref)?.product_name}</em>
                </span>
                <span className="review-reason">
                  <b>{claim.reason || "Human decision required."}</b>
                  <small>
                    <span className="intent-tag">{claim.intent || "unclear"}</span>
                  </small>
                </span>
                <strong>
                  Decide <ArrowRight size={14} />
                </strong>
              </button>
            ))
          ) : (
            <Panel>
              <Empty>No claims are waiting for human review.</Empty>
            </Panel>
          )}
        </div>
      </>
    );
  }

  function onAgentCompleted() {
    if (selectedClaim) return claimDetailPanel("Agent completed");
    return (
      <>
        {pageIntro("AUTOMATION", "Agent completed", pageSubtitles.agent_completed)}
        <div className="review-queue">
          {agentCompleted.length ? (
            agentCompleted.map((claim) => (
              <button
                className="review-card"
                key={claim.claim_id}
                onClick={() => selectClaim(claim.claim_id, "agent_completed")}
              >
                <span className="review-customer">
                  <code>{claim.claim_id}</code>
                  <b>{claim.customer_name}</b>
                  <small>
                    {claim.product_ref} · {dateShort(claim.claim_date)}
                  </small>
                  <em>{claim.action?.product_name || claim.warranty?.product_name || claim.product_ref}</em>
                </span>
                <span className="review-reason">
                  <b>{claim.reason || "Automatically resolved by policy."}</b>
                  <small>
                    <span className="intent-tag">{pretty(claim.action?.action_type || claim.decision)}</span>
                    {claim.action?.amount ? ` · ${money(claim.action.amount)}` : ""}
                  </small>
                </span>
                <strong>
                  Inspect / override <ArrowRight size={14} />
                </strong>
              </button>
            ))
          ) : (
            <Panel>
              <Empty>No agent-completed claims are waiting for an override.</Empty>
            </Panel>
          )}
        </div>
      </>
    );
  }

  function onActions() {
    const entries = claims
      .filter(
        (claim) =>
          claim.status !== "new" &&
          claim.status !== "processing" &&
          (claim.decision || ["error", ...REVIEW_STATUSES, "resolved", "denied"].includes(claim.status)),
      )
      .filter((claim) => actionClaim === "all" || claim.claim_id === actionClaim);
    return (
      <>
        {pageIntro(
          "SIMULATED TOOLS",
          "Demo actions",
          "Refunds, credits and replacement schedules. No real money moves and no goods ship.",
        )}
        <Panel
          title="Decision & action history"
          note="Processed requests, including approvals, denials, reviews, and recorded actions."
          action={
            <select
              value={actionClaim}
              onChange={(event) => setActionClaim(event.target.value)}
              aria-label="Filter claim"
            >
              <option value="all">All requests</option>
              {claims.map((claim) => (
                <option key={claim.claim_id} value={claim.claim_id}>
                  {claim.claim_id} · {claim.customer_name}
                </option>
              ))}
            </select>
          }
        >
          <div className="action-history-banner">
            Select a request to inspect the customer report, decision, policy checks, reviewer override and full audit
            timeline.
          </div>
          <div className="actions-list">
            {entries.length ? (
              entries.map((claim) => (
                <ResolutionRow
                  key={claim.claim_id}
                  claim={claim}
                  onClick={() => selectClaim(claim.claim_id)}
                  warranty={claim.warranty || selectedWarranty(claim.product_ref)}
                />
              ))
            ) : (
              <Empty>No processed decision history yet.</Empty>
            )}
          </div>
        </Panel>
        {actionClaim !== "all" && (
          <Panel title="Request timeline" note={`Complete workflow history for ${actionClaim}.`}>
            <Timeline
              events={events
                .filter((event) => event.claim_id === actionClaim)
                .slice()
                .reverse()}
            />
          </Panel>
        )}
      </>
    );
  }

  function onWarranties() {
    return (
      <>
        {pageIntro("EVIDENCE", "Warranty records", pageSubtitles.warranties)}
        <Panel>
          <div className="queue-filters">
            <label className="search-box">
              <Search size={15} />
              <input
                value={warrantySearch}
                onChange={(event) => setWarrantySearch(event.target.value)}
                placeholder="Search reference, holder, product, plan"
              />
            </label>
            <select value={eligibility} onChange={(event) => setEligibility(event.target.value)}>
              <option value="all">Any credit eligibility</option>
              <option value="yes">Credit eligible</option>
              <option value="no">Not credit eligible</option>
            </select>
          </div>
          <small className="record-count">
            {filteredWarranties.length} of {warranties.length} records
          </small>
          <div className="table-wrap warranty-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>REFERENCE</th>
                  <th>HOLDER</th>
                  <th>PRODUCT</th>
                  <th>PLAN</th>
                  <th>CATEGORY</th>
                  <th>PRICE</th>
                  <th>CREDIT</th>
                </tr>
              </thead>
              <tbody>
                {filteredWarranties.map((row) => (
                  <tr key={row.product_ref}>
                    <td>{row.product_ref}</td>
                    <td>{row.customer_name}</td>
                    <td>{row.product_name}</td>
                    <td>{row.service_plan}</td>
                    <td>{row.claim_category}</td>
                    <td>{money(row.purchase_price)}</td>
                    <td>{Number(row.credit_value) > 0 ? money(row.credit_value) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </>
    );
  }

  function onAudit() {
    const walkthroughClaimId = auditClaimId || claims[0]?.claim_id || "";
    const walkthroughClaim = claims.find((claim) => claim.claim_id === walkthroughClaimId);
    const walkthroughEvents = events.filter((event) => event.claim_id === walkthroughClaimId);
    return (
      <>
        {pageIntro("LEDGER", "Audit trail", pageSubtitles.audit)}
        <Panel
          title="Backend walkthrough"
          note="The nine brief requirements, traced against one claim's saved records and actual workflow events."
          action={
            <select
              value={walkthroughClaimId}
              onChange={(event) => setAuditClaimId(event.target.value)}
              aria-label="Choose claim for backend walkthrough"
            >
              {claims.map((claim) => (
                <option key={claim.claim_id} value={claim.claim_id}>
                  {claim.claim_id} · {claim.customer_name}
                </option>
              ))}
            </select>
          }
        >
          {walkthroughClaim ? (
            <BackendWalkthrough
              claim={walkthroughClaim}
              events={walkthroughEvents}
              reviews={auditReviews}
              policy={policy}
              dataSummary={data?.dataSummary || {}}
            />
          ) : (
            <Empty>Import claims to inspect the backend flow.</Empty>
          )}
        </Panel>
        <Panel
          title="Current audit log"
          note="Raw timestamped events emitted by the running backend. Select a row to open its claim."
        >
          <div className="queue-filters">
            <label className="search-box">
              <Search size={15} />
              <input
                value={auditSearch}
                onChange={(event) => setAuditSearch(event.target.value)}
                placeholder="Filter by claim, agent, tool or text"
              />
            </label>
            <select value={auditFilter} onChange={(event) => setAuditFilter(event.target.value)}>
              <option value="all">All outcomes</option>
              <option value="failed">Failures</option>
              <option value="review">Human review</option>
              <option value="action">Actions</option>
            </select>
          </div>
          <small className="record-count">{filteredEvents.length} events</small>
          <EventList events={filteredEvents} onSelect={(id) => selectClaim(id)} />
        </Panel>
      </>
    );
  }

  function onPolicy() {
    return (
      <>
        {pageIntro("AUTOMATION POLICY", "Policy", pageSubtitles.policy)}
        <div className="policy-content">
          <Panel title={<Eyebrow>THRESHOLDS · POLICY {policy.policy_version}</Eyebrow>}>
            <div className="policy-grid">
              <div>
                <label>Auto-resolution limit (USD)</label>
                <strong>{money(policy.auto_approval_limit_usd)}</strong>
                <small>
                  Refund/credit amount and replacement unit value at or above this cap require human review.
                </small>
              </div>
              <div>
                <label>Repeat replacement handling</label>
                <strong>Human review</strong>
                <small>Any recorded prior replacement or explicit repeat-issue wording requires review.</small>
              </div>
            </div>
          </Panel>
          <Panel title={<Eyebrow>AUTOMATION RULES</Eyebrow>}>
            <div className="policy-rules">
              <p>
                <b>Refunds:</b>{" "}
                {policy.refunds_require_human_approval
                  ? "Human approval required"
                  : "May auto-resolve when covered and below cap"}
              </p>
              <p>
                <b>Credits:</b>{" "}
                {policy.credits_require_human_approval
                  ? "Human approval required"
                  : "May auto-resolve when covered and below cap"}
              </p>
              <p>
                <b>Like-for-like replacements:</b>{" "}
                {policy.replacements_require_human_approval
                  ? "Human approval required"
                  : "May schedule automatically when supported and below cap"}
              </p>
              <p>
                <b>Eligible warranty categories:</b> {policy.supported_warranty_categories.join(", ")}
              </p>
              <p className="policy-note">
                Unclear intent, unsupported evidence, uncertain extraction, repeat replacement indicators and
                replacement limits can still require review. Edit policy.json and restart the API to change these
                settings.
              </p>
            </div>
          </Panel>
          <Panel
            title={<Eyebrow>TEST DATA</Eyebrow>}
            note="Reset saved app activity and start again from the supplied seed claims."
          >
            <div className="clear-data-row">
              <div>
                <b>Clear all saved data</b>
                <small>
                  Removes decisions, simulated actions, reviews, token usage, checkpoints and audit history. Reloads the
                  90 original claims as new. CSV files and policy settings stay unchanged.
                  {!demoDataResetEnabled && " Enable ENABLE_DEMO_DATA_RESET=true in .env for a trusted local demo."}
                </small>
              </div>
              <button
                className="button danger"
                onClick={clearAllData}
                disabled={clearingData || busy || !demoDataResetEnabled}
              >
                {clearingData ? <LoaderCircle className="spin" size={14} /> : <AlertTriangle size={14} />}
                {clearingData ? "Clearing…" : "Clear all data"}
              </button>
            </div>
          </Panel>
        </div>
      </>
    );
  }

  function onUsage() {
    const usage = data?.usage || {};
    return (
      <>
        <div className="stats-grid usage-stats">
          <Stat label="TOTAL TOKENS" value={Number(usage.total_tokens || 0).toLocaleString()} />
          <Stat label="INPUT TOKENS" value={Number(usage.prompt_tokens || 0).toLocaleString()} />
          <Stat label="OUTPUT TOKENS" value={Number(usage.completion_tokens || 0).toLocaleString()} />
          <Stat label="LLM CALLS" value={usage.calls || 0} />
        </div>
        <div className="usage-grid">
          <Panel title="Usage by model" note="Provider-reported usage totals.">
            <div className="usage-table">
              {usage.by_model?.length ? (
                usage.by_model.map((row) => (
                  <div className="usage-model-row" key={`${row.provider}-${row.model}`}>
                    <b>
                      {row.provider} · {row.model}
                    </b>
                    <span>{Number(row.total_tokens).toLocaleString()} tokens</span>
                    <small>
                      {row.calls} calls · {row.reported_calls} with usage metadata
                    </small>
                  </div>
                ))
              ) : (
                <Empty>Token usage will appear after an agent calls an LLM.</Empty>
              )}
            </div>
          </Panel>
          <Panel title="Recent LLM calls" note="Claim, agent, model and recorded tokens.">
            <div className="usage-calls-list">
              {usage.recent?.map((row) => (
                <button className="usage-call" key={row.id} onClick={() => selectClaim(row.claim_id)}>
                  <span>
                    <b>{pretty(row.agent)}</b>
                    <small>
                      {row.claim_id} · {row.provider} · {row.model}
                    </small>
                  </span>
                  <strong>
                    {row.usage_available ? `${Number(row.total_tokens).toLocaleString()} tokens` : "Usage unavailable"}
                  </strong>
                  <time>{dateTime(row.created_at)}</time>
                </button>
              ))}
            </div>
          </Panel>
        </div>
        <p className="usage-disclaimer">
          Counts come from provider metadata. Missing usage is shown as unreported; the app does not estimate token
          counts or cost.
        </p>
      </>
    );
  }

  const page = {
    overview: onOverview,
    claims: onQueue,
    agent_completed: onAgentCompleted,
    review: onReview,
    actions: onActions,
    warranties: onWarranties,
    audit: onAudit,
    policy: onPolicy,
    usage: onUsage,
  }[view]();

  if (!data)
    return (
      <div className="loading-shell">
        <LoaderCircle className="spin" />
        {loadError ? (
          <div>
            <b>API unavailable</b>
            <p>{loadError}. Start the FastAPI server, then refresh.</p>
          </div>
        ) : (
          <span>Connecting to the claim ledger…</span>
        )}
      </div>
    );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            setView("overview");
          }}
        >
          <span className="brand-mark">
            <Flame size={18} fill="currentColor" />
          </span>
          <span>
            NovaHearth<small>WARRANTY OPS</small>
          </span>
        </a>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          {NAV.map(([id, label, Icon]) => (
            <button
              key={id}
              className={`nav-item ${view === id ? "active" : ""}`}
              onClick={() => {
                setView(id);
                if (["claims", "agent_completed", "review"].includes(id) && selectedClaim) {
                  selectionRef.current = "";
                  setSelectedClaim(null);
                }
              }}
            >
              <Icon size={15} />
              <span>{label}</span>
              {id === "claims" && <small className="nav-count">{claims.length}</small>}
              {id === "agent_completed" && agentCompleted.length > 0 && (
                <small className="nav-count">{agentCompleted.length}</small>
              )}
              {id === "review" && reviews.length > 0 && <small className="nav-count">{reviews.length}</small>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="connection">
            <span className="pulse" />
            <span>
              <b>LLM AGENT IDLE</b>
              <small>{loadError || "Connected · API ready"}</small>
            </span>
          </div>
          <small className="version">LOCAL DEMO · V1.4</small>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="title-block">
            <Eyebrow>
              CUSTOMER OPERATIONS / <span>{view.toUpperCase()}</span>
            </Eyebrow>
            <h1>{TITLE[view]}</h1>
            {view === "overview" && <p className="topbar-subtitle">{pageSubtitles[view]}</p>}
          </div>
          <div className="top-actions">
            {["overview", "claims"].includes(view) && (
              <div className="batch-controls">
                <label>
                  Batch size
                  <select value={batchSize} onChange={(event) => setBatchSize(event.target.value)}>
                    <option value="10">10 claims</option>
                    <option value="30">30 claims</option>
                    <option value="50">50 claims</option>
                    <option value="all">All pending</option>
                  </select>
                </label>
                <button
                  className="button primary"
                  onClick={processBatch}
                  disabled={busy || !providerReady || batchLimit === 0}
                >
                  {busy ? <LoaderCircle className="spin" size={13} /> : "▶"}{" "}
                  {batchProgress
                    ? `Processing ${batchProgress.completed}/${batchProgress.total}${batchProgress.current_claim_id ? ` · ${batchProgress.current_claim_id}` : ""}`
                    : `Process ${batchSize === "all" ? `${queued.length} pending` : `${batchLimit} pending`}`}
                </button>
              </div>
            )}
            <label className={`provider-pill ${providerReady ? "provider-ready" : "provider-unavailable"}`}>
              <span className="status-dot" />
              <select
                value={provider}
                onChange={(event) => changeProvider(event.target.value)}
                aria-label="Choose LLM provider"
              >
                {Object.entries(providers).map(([name, item]) => {
                  const label =
                    name === "ollama"
                      ? `Ollama · ${item.model}`
                      : name === "inhouse"
                        ? `In-house · ${item.model}`
                        : name[0].toUpperCase() + name.slice(1);
                  const unavailable = !item.available
                    ? name === "ollama"
                      ? " · model unavailable"
                      : name === "inhouse"
                        ? " · endpoint missing"
                        : " · key missing"
                    : "";
                  return (
                    <option key={name} value={name}>
                      {label}
                      {unavailable}
                    </option>
                  );
                })}
              </select>
            </label>
            <button
              className="icon-button"
              onClick={() => refresh().catch((error) => notify(error.message, true))}
              aria-label="Refresh"
            >
              <RefreshCw size={15} />
            </button>
          </div>
        </header>
        {page}
        <footer>AI recommendations are advisory. Fulfilment and financial actions in this demo are simulated.</footer>
      </main>
      {toast && (
        <div role="status" className={`toast ${toast.error ? "error" : ""}`}>
          {toast.message}
        </div>
      )}
    </div>
  );
}

function EventList({ events, onSelect, compact = false }) {
  return (
    <div className={`event-list ${compact ? "compact" : ""}`}>
      {events.length ? (
        events.map((event) => {
          const details = event.details || {};
          const title =
            details.step ||
            details.tool ||
            {
              llm_usage_recorded: "LLM response received",
              agent_decision: "Agent decision saved",
              human_review_requested: "Human review required",
              action_taken: "Resolution action recorded",
              action_reversed: "Agent action reversed",
              workflow_error: "Workflow error",
              provider_rate_limited: "Provider rate limited",
            }[event.event_type] ||
            pretty(event.event_type);
          const summary =
            details.reason ||
            details.note ||
            details.error_type ||
            (details.total_tokens != null ? `${details.total_tokens} tokens` : event.actor);
          const mood =
            event.event_type.includes("failed") || event.event_type === "workflow_error"
              ? "failed"
              : event.event_type.includes("review")
                ? "review"
                : "";
          return (
            <button key={event.id} className="event-row" onClick={() => onSelect(event.claim_id)}>
              <i className={`event-dot ${mood}`} />
              <span className="event-copy">
                <b>{pretty(title)}</b>
                <small>{summary}</small>
                <em>
                  {event.claim_id} · {pretty(event.actor)}
                </em>
              </span>
              <time>{timeAgo(event.created_at)}</time>
            </button>
          );
        })
      ) : (
        <Empty>No workflow events recorded yet.</Empty>
      )}
    </div>
  );
}

function ResolutionRow({ claim, warranty, onClick }) {
  const action = claim.action || {};
  const proposal = claim.proposal || {};
  const support = claim.support || {};
  const interpretation = claim.interpretation || {};
  const actionText = action.action_type
    ? `${action.status === "reversed" ? "Reversed" : "Recorded"} ${pretty(action.action_type)}${action.amount ? ` · ${money(action.amount)}` : ""}`
    : proposal.action_type
      ? `Proposed ${pretty(proposal.action_type)}${proposal.amount ? ` · ${money(proposal.amount)}` : ""}`
      : "No resolution action recorded";
  return (
    <button className="resolution-row" onClick={onClick}>
      <span className="resolution-main">
        <span className="resolution-heading">
          <b>
            {claim.customer_name} · {claim.claim_id}
          </b>
          <Badge value={claim.status} />
        </span>
        <small>
          {claim.product_ref} · {warranty?.product_name || "Product not looked up"} · Request:{" "}
          {interpretation.intent || claim.intent || "not interpreted"}
        </small>
        <span className="resolution-quote">“{claim.claim_text}”</span>
        <small>
          <b>Warranty:</b> {warranty?.claim_category || "not assessed"} · <b>Coverage:</b>{" "}
          {support.supported == null ? "Not assessed" : support.supported ? "Supported" : "Not supported"}
          {support.basis ? ` · ${support.basis}` : ""}
        </small>
        <small>
          <b>Decision:</b> {pretty(claim.decision || claim.status)} · {claim.reason || "No decision reason recorded."}
        </small>
        <small>
          {actionText}
          {claim.reviewer ? ` · Reviewed by ${claim.reviewer}` : ""}
          {claim.reviewer_note ? ` · Reviewer note: ${claim.reviewer_note}` : ""}
        </small>
      </span>
      <em>
        View customer and audit history <ArrowRight size={13} />
      </em>
    </button>
  );
}

function BackendWalkthrough({ claim, events, reviews, policy, dataSummary }) {
  const interpretation = claim.interpretation || {};
  const warranty = claim.warranty || {};
  const support = claim.support || {};
  const action = claim.action || {};
  const intent = interpretation.intent || claim.intent || "unclear";
  const reviewPending = REVIEW_STATUSES.has(claim.status);
  const modelEvent = events.find((event) => event.event_type === "llm_usage_recorded");
  const modelDetails = modelEvent?.details || {};
  const productValue = intent === "replacement" ? Number(warranty.purchase_price) : Number(warranty.credit_value);
  const valueLabel = intent === "replacement" ? "Replacement unit purchase price" : "Refund / credit amount";
  const hasValue = warranty.product_name && Number.isFinite(productValue);
  const valueBelowCap = hasValue && productValue < Number(policy.auto_approval_limit_usd);
  const toolWasCalled = (name) =>
    events.some((event) => event.event_type === "tool_call_completed" && event.actor === `tool:${name}`);
  const toolEvidence = [
    `warranty lookup: ${toolWasCalled("lookup_warranty_record") ? "called" : "not found in event log"}`,
    `coverage check: ${toolWasCalled("check_warranty_support") ? "called" : "not found in event log"}`,
    `replacement history: ${toolWasCalled("lookup_replacement_history") ? "called" : "not found in event log"}`,
  ].join(" · ");
  const step = (number, title, state, summary, detail = "") => ({
    number,
    title,
    state,
    summary,
    detail,
  });
  const rows = [
    step(
      1,
      "Ingest the supplied datasets",
      claim.created_at ? "done" : "pending",
      claim.created_at
        ? `${dataSummary.warranty_claims ?? "—"} claims · ${dataSummary.product_warranty_records ?? "—"} warranty records · ${dataSummary.replacement_history ?? "—"} replacement-history rows`
        : "Source datasets have not loaded.",
      `Source files: ${(dataSummary.source_files || []).join(", ") || "loading"} · Claim ${claim.claim_id} ${claim.created_at ? "is saved in the claim ledger." : "is not saved yet."}`,
    ),
    step(
      2,
      "Interpret the request and product",
      interpretation.intent ? "done" : "pending",
      interpretation.intent
        ? `LLM request: ${pretty(intent)} · issue: ${interpretation.issue_category || "unknown"}`
        : "Waiting for the claim-intake agent.",
      interpretation.intent
        ? `Customer-mentioned product: ${interpretation.product_mentioned || "not explicitly named"} · product resolved by trusted claim reference ${claim.product_ref}: ${warranty.product_name || "no matching warranty record"} · model: ${modelDetails.provider ? `${modelDetails.provider} / ${modelDetails.model}` : "not recorded"} · issue excerpt: ${interpretation.issue_summary || claim.claim_text}`
        : "",
    ),
    step(
      3,
      "Look up warranty and test coverage",
      support.supported == null ? (warranty.product_name ? "review" : "pending") : "done",
      support.supported == null
        ? "Warranty lookup or coverage check is pending / unavailable."
        : `Coverage: ${support.supported ? "supported" : "not supported"}`,
      `${toolEvidence} · record category: ${warranty.claim_category || "not found"} · basis: ${support.basis || "not assessed"}`,
    ),
    step(
      4,
      "Apply decision rules",
      claim.decision ? (reviewPending ? "review" : "done") : "pending",
      claim.decision ? `${pretty(claim.decision)} · ${pretty(claim.status)}` : "Decision has not run.",
      claim.reason || "No decision reason has been recorded.",
    ),
    step(
      5,
      "Check the auto-resolution value limit",
      hasValue ? (valueBelowCap ? "done" : "review") : "pending",
      hasValue
        ? `${valueLabel}: ${money(productValue)} · limit: ${money(policy.auto_approval_limit_usd)} · ${valueBelowCap ? "below cap" : "at or above cap; human review required"}`
        : "Waiting for the matching warranty record and remedy.",
      claim.policy_version
        ? `Recorded decision policy: ${claim.policy_version} · currently configured policy: ${policy.policy_version}. The cap shown is the current setting.`
        : "No policy decision has been recorded; showing the current configured cap.",
    ),
    step(
      6,
      "Issue refund or credit with an action tool",
      ["refund", "credit"].includes(intent)
        ? action.action_type
          ? "done"
          : reviewPending
            ? "review"
            : "pending"
        : "na",
      action.action_type && ["refund", "credit"].includes(action.action_type)
        ? `Tool recorded a simulated ${action.action_type}: ${money(action.amount)} · ${action.status}`
        : ["refund", "credit"].includes(intent)
          ? reviewPending
            ? "No money action yet; awaiting human decision."
            : "No money action was recorded."
          : "Not applicable to this requested remedy.",
      action.external_reference
        ? `Demo reference: ${action.external_reference}. This adapter records a simulation; it does not move money.`
        : "",
    ),
    step(
      7,
      "Schedule like-for-like replacement with its tool",
      intent === "replacement"
        ? action.action_type === "replacement"
          ? "done"
          : reviewPending
            ? "review"
            : "pending"
        : "na",
      action.action_type === "replacement"
        ? `Replacement tool recorded ${action.status} for ${action.product_name || warranty.product_name || claim.product_ref}.`
        : intent === "replacement"
          ? reviewPending
            ? "No replacement scheduled; awaiting human decision."
            : "No replacement action was recorded."
          : "Not applicable to this requested remedy.",
      action.action_type === "replacement"
        ? `Value gate uses the unit purchase price (${money(warranty.purchase_price)}). Demo reference: ${action.external_reference || "—"}.`
        : "",
    ),
    step(
      8,
      "Show evidence and allow human review / override",
      reviews.length || reviewPending ? "review" : "done",
      reviews.length
        ? `${reviews.length} human review decision(s) saved.`
        : reviewPending
          ? "Waiting for reviewer action."
          : "Evidence and concise decision reason are available; no reviewer action is currently recorded.",
      `Coverage: ${support.basis || "pending"} ${claim.reviewer ? `· last reviewer: ${claim.reviewer}` : ""} ${claim.reviewer_note ? `· note: ${claim.reviewer_note}` : ""}`,
    ),
    step(
      9,
      "Persist claim, decision, actions and overrides",
      claim.created_at ? "done" : "pending",
      claim.created_at
        ? `Claim row saved · ${events.length} audit events · ${action.action_id ? "action row saved" : "no action row"} · ${reviews.length} review record(s)`
        : "Claim is not yet persisted.",
      `Created: ${dateTime(claim.created_at)} · Updated: ${dateTime(claim.updated_at)} · storage: SQLite`,
    ),
  ];
  return (
    <div className="backend-walkthrough">
      <p className="walkthrough-disclosure">
        This is an operational trace, not private model chain-of-thought. It summarizes source records, tool results,
        policy gates, saved actions and reviewer events.
      </p>
      <ol className="walkthrough-steps">
        {rows.map((row) => (
          <li className={`walkthrough-step walkthrough-${row.state}`} key={row.number}>
            <span className="walkthrough-number">{row.number}</span>
            <div className="walkthrough-body">
              <div className="walkthrough-heading">
                <b>{row.title}</b>
                <span className={`walkthrough-state ${row.state}`}>
                  {row.state === "na"
                    ? "Not applicable"
                    : row.state === "done"
                      ? "Complete"
                      : row.state === "review"
                        ? "Human review"
                        : "Pending"}
                </span>
              </div>
              <p>{row.summary}</p>
              {row.detail && <small>{row.detail}</small>}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function ClaimDetail({
  claim,
  events,
  actions,
  reviews,
  providerReady,
  busy,
  reviewer,
  reviewNote,
  overrideNote,
  setOverrideNote,
  setReviewer,
  setReviewNote,
  process,
  review,
  override,
  backLabel,
  back,
}) {
  const interpretation = claim.interpretation || {};
  const warranty = claim.warranty || {};
  const evidence = claim.evidence || {};
  const support = claim.support || {};
  const proposal = claim.proposal || null;
  const canRun = QUEUED_STATUSES.has(claim.status) || (claim.status === "awaiting_review" && !claim.action);
  const canReverseAutoAction = isAgentAutoResolved(claim);
  return (
    <Panel className="claim-detail-panel">
      <div className="detail-content">
        <button className="detail-back" onClick={back}>
          <ArrowLeft size={13} /> {backLabel}
        </button>
        <div className="detail-title">
          <div>
            <h2>{claim.customer_name}</h2>
            <div className="product-ref">
              {claim.claim_id} · {claim.product_ref} · filed {dateShort(claim.claim_date)}
            </div>
          </div>
          <Badge value={claim.status} />
        </div>
        {canRun && claim.status !== "awaiting_review" && (
          <div className="top-action-bar">
            <button className="button primary run-primary" disabled={busy || !providerReady} onClick={process}>
              {busy ? <LoaderCircle className="spin" size={14} /> : <Wrench size={14} />}
              Run agent on this claim
              <ArrowRight size={15} />
            </button>
          </div>
        )}
        <div className="claim-quote">“{claim.claim_text}”</div>
        <DetailSection title="Agent decision">
          <p className="decision">{claim.decision || "Not processed"}</p>
          <p className="reason">{claim.reason || "The claim has not been assessed yet."}</p>
        </DetailSection>
        {interpretation.intent && (
          <DetailSection title="LLM interpretation">
            <div className="detail-grid">
              <Info label="Requested remedy" value={interpretation.intent} />
              <Info
                label="Product mentioned by customer"
                value={interpretation.product_mentioned || "Not explicitly named"}
              />
              <Info label="Customer text excerpt" value={interpretation.issue_summary} />
              <Info label="Reported cause" value={interpretation.issue_category} />
            </div>
          </DetailSection>
        )}
        {warranty.product_name && (
          <DetailSection title="Warranty record">
            <div className="detail-grid">
              <Info label="Product" value={warranty.product_name} />
              <Info label="Warranty category" value={warranty.claim_category} />
              <Info label="Credit value" value={money(warranty.credit_value)} />
              <Info label="Plan" value={warranty.service_plan} />
            </div>
          </DetailSection>
        )}
        {evidence.evidence_summary && (
          <DetailSection title="Evidence assessment">
            <p>
              <b>{pretty(evidence.alleged_category)}</b>
            </p>
            <p>{evidence.evidence_summary}</p>
          </DetailSection>
        )}
        {claim.support && (
          <DetailSection title="Coverage check">
            <p>
              <b>{support.supported ? "Supported" : "Not supported"}</b>
            </p>
            <p>{support.basis}</p>
          </DetailSection>
        )}
        {proposal && (
          <DetailSection title="Proposed action">
            <div className="detail-grid">
              <Info label="Action" value={proposal.action_type} />
              <Info label="Product" value={proposal.product_name} />
              <Info label="Amount" value={proposal.amount ? money(proposal.amount) : "—"} />
            </div>
          </DetailSection>
        )}
        {claim.action && (
          <DetailSection title="Action recorded">
            <div className="detail-grid">
              <Info label="Action" value={claim.action.action_type} />
              <Info label="Status" value={claim.action.status} />
              <Info label="Reference" value={claim.action.external_reference} />
            </div>
          </DetailSection>
        )}
        <DetailSection title="Action history for this request">
          {actions.length ? (
            actions.map((action) => {
              const isReversal = action.action_type === "reversal";
              return (
                <p className="action-history-line" key={action.action_id}>
                  <b>
                    {isReversal ? `Reversed ${pretty(action.details?.reversed_action_type)}` : pretty(action.action_type)}
                  </b>{" "}
                  · {pretty(action.status)} · {action.details?.product_name}{" "}
                  {action.amount
                    ? `· ${money(Math.abs(Number(action.amount)))}${isReversal ? " reversed" : ""}`
                    : ""}
                  {isReversal ? ` · original ${action.details?.reverses_action_id}` : ""} · {dateTime(action.created_at)}
                </p>
              );
            })
          ) : (
            <p className="reason">No resolution action has been recorded for this request.</p>
          )}
        </DetailSection>
        {claim.status === "awaiting_review" && (
          <DetailSection title="Human review / override">
            <div className="review-form">
              <label>
                Reviewer name
                <input
                  value={reviewer}
                  onChange={(event) => setReviewer(event.target.value)}
                  maxLength={120}
                  placeholder="Your name"
                />
              </label>
              <label>
                Review note
                <textarea
                  value={reviewNote}
                  onChange={(event) => setReviewNote(event.target.value)}
                  maxLength={2000}
                  rows={3}
                  placeholder="Reason for your decision"
                />
              </label>
              <div className="review-buttons">
                <button className="button primary" disabled={busy || !proposal} onClick={() => review(true)}>
                  Approve action
                </button>
                <button className="button danger" disabled={busy} onClick={() => review(false)}>
                  Deny claim
                </button>
              </div>
              <p className="data-disclosure">Action execution is simulated for this demo.</p>
            </div>
          </DetailSection>
        )}
        {canReverseAutoAction && (
          <DetailSection title="Override agent decision">
            <div className="review-form">
              <label>
                Reviewer name
                <input
                  value={reviewer}
                  onChange={(event) => setReviewer(event.target.value)}
                  maxLength={120}
                  placeholder="Your name"
                />
              </label>
              <label>
                Override note
                <textarea
                  value={overrideNote}
                  onChange={(event) => setOverrideNote(event.target.value)}
                  maxLength={2000}
                  rows={2}
                  placeholder="Why should this automated decision be denied?"
                />
              </label>
              <label>
                Override
                <select value="deny" aria-label="Override decision" disabled>
                  <option value="deny">Denied</option>
                </select>
              </label>
              <button className="button danger" disabled={busy} onClick={() => override("deny")}>
                Override
              </button>
              <p className="data-disclosure">
                This records a simulated reversal, preserves the original action in the audit history, and moves the
                claim to Denied.
              </p>
            </div>
          </DetailSection>
        )}
        {!QUEUED_STATUSES.has(claim.status) &&
          claim.status !== "processing" &&
          claim.status !== "awaiting_review" &&
          !canReverseAutoAction &&
          !claim.action && (
            <DetailSection title="Human override">
              <div className="review-form">
                <label>
                  Reviewer name
                  <input
                    value={reviewer}
                    onChange={(event) => setReviewer(event.target.value)}
                    maxLength={120}
                    placeholder="Your name"
                  />
                </label>
                <label>
                  Override note
                  <textarea
                    value={overrideNote}
                    onChange={(event) => setOverrideNote(event.target.value)}
                    maxLength={2000}
                    rows={2}
                    placeholder="Reason for override"
                  />
                </label>
                <div className="review-buttons">
                  <button className="button danger" disabled={busy} onClick={() => override("deny")}>
                    Override: deny
                  </button>
                  <button className="button secondary" disabled={busy} onClick={() => override("review")}>
                    Return to review
                  </button>
                </div>
              </div>
            </DetailSection>
          )}
        {reviews.length > 0 && (
          <DetailSection title="Human review history">
            {reviews.map((item) => (
              <p className="action-history-line" key={item.review_id}>
                <b>{pretty(item.decision)}</b> · {item.reviewer} · {item.note} · {dateTime(item.created_at)}
              </p>
            ))}
          </DetailSection>
        )}
        <DetailSection title="Audit history">
          <Timeline events={events.slice().reverse()} />
        </DetailSection>
      </div>
    </Panel>
  );
}

function DetailSection({ title, children }) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}
function Info({ label, value }) {
  return (
    <div className="mini-card">
      <span>{label}</span>
      <b>{value == null || value === "" ? "—" : String(value)}</b>
    </div>
  );
}

function Timeline({ events }) {
  return events.length ? (
    <div className="timeline">
      {events.map((event) => {
        const details = event.details || {};
        const title = details.step || details.tool || details.agent || event.event_type;
        const summary =
          details.total_tokens != null
            ? `${details.total_tokens} tokens`
            : details.path || details.tool || details.error_type || details.reason || details.decision || "";
        return (
          <article
            className={`timeline-item ${event.event_type.includes("failed") || event.event_type === "workflow_error" ? "failed" : ""}`}
            key={event.id}
          >
            <span className="timeline-marker" />
            <div className="timeline-main">
              <b>{pretty(title)}</b>
              <span className="timeline-event">{pretty(event.event_type)}</span>
              <small>
                {event.actor}
                {summary ? ` · ${summary}` : ""}
              </small>
              <time>{dateTime(event.created_at)}</time>
            </div>
          </article>
        );
      })}
    </div>
  ) : (
    <Empty>No events have been recorded for this request.</Empty>
  );
}

function timeAgo(value) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (!Number.isFinite(seconds)) return dateTime(value);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return dateShort(value);
}

export default App;
