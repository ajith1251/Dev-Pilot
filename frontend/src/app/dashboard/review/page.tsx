"use client";

import { useState, useCallback } from "react";

// ── Type Definitions (matching backend models/review.py) ──────

type QualityGateDecision = "approved" | "rejected" | "needs_human_review" | "incomplete";
type FindingSeverity = "critical" | "high" | "medium" | "low" | "info";
type FindingCategory =
  | "requirement" | "correctness" | "testing" | "security"
  | "architecture" | "maintainability" | "scope" | "regression"
  | "documentation" | "quality" | "tampering";
type RequirementStatusValue =
  | "satisfied" | "partially_satisfied" | "unsatisfied" | "unverified" | "not_applicable";

interface ReviewFinding {
  finding_id: string;
  category: FindingCategory;
  severity: FindingSeverity;
  title: string;
  description: string;
  file_path?: string | null;
  line_start?: number | null;
  line_end?: number | null;
  symbol?: string | null;
  requirement_ids: string[];
  plan_step_ids: string[];
  evidence: string[];
  recommendation: string;
  blocking: boolean;
  confidence: number;
}

interface RequirementCoverage {
  requirement_id: string;
  requirement_description: string;
  status: RequirementStatusValue;
  plan_steps: string[];
  changed_files: string[];
  evidence: string[];
  tests: string[];
  notes: string;
}

interface TestSummary {
  executed: boolean;
  status: string;
  tests_passed?: number | null;
  tests_failed?: number | null;
  tests_skipped?: number | null;
  commands_total: number;
  commands_passed: number;
  commands_failed: number;
  commands_rejected: number;
  duration_seconds: number;
  has_skipped: boolean;
  has_timeout: boolean;
  environment_ready: boolean;
  warnings: string[];
}

interface RepairSummary {
  attempted: boolean;
  status?: string | null;
  attempts: number;
  stop_reason: string;
  remaining_failures: number;
}

interface SecuritySummary {
  passed: boolean;
  blocked_patterns: string[];
  warnings: string[];
}

interface ScopeSummary {
  in_scope_files: string[];
  out_of_scope_files: string[];
  warnings: string[];
}

interface PlanStepAssessment {
  step_id: string;
  step_title: string;
  status: string;
  changed_files: string[];
  notes: string;
}

interface ReviewReport {
  review_id: string;
  workspace_id: string;
  requirement_coverage: RequirementCoverage[];
  plan_assessment: PlanStepAssessment[];
  findings: ReviewFinding[];
  test_summary: TestSummary;
  repair_summary: RepairSummary;
  security_summary: SecuritySummary;
  scope_summary: ScopeSummary;
  agent_summary: string;
  quality_metrics?: { overall?: number } | null;
  created_at: string;
  duration_seconds: number;
  warnings: string[];
}

interface QualityGateResult {
  review_id: string;
  decision: QualityGateDecision;
  blocking_findings: string[];
  warnings: string[];
  requirements_status: RequirementStatusValue;
  requirements_satisfied: number;
  requirements_partial: number;
  requirements_unsatisfied: number;
  requirements_unverified: number;
  verification_status: string;
  security_status: string;
  score?: number | null;
  reason_codes: string[];
  summary: string;
}

interface ReviewResponse {
  report: ReviewReport;
  gate_result: QualityGateResult;
}

// ── Helpers ────────────────────────────────────────────────────

function severityClass(sev: FindingSeverity): string {
  switch (sev) {
    case "critical": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
    case "high": return "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400";
    case "medium": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400";
    case "low": return "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400";
    case "info": return "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-400";
  }
}

function categoryIcon(cat: FindingCategory) {
  switch (cat) {
    case "security":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
        </svg>
      );
    case "tampering":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
        </svg>
      );
    case "requirement":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      );
    case "correctness":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
        </svg>
      );
    case "testing":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
      );
    default:
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75z" />
        </svg>
      );
  }
}

function decisionClass(dec: QualityGateDecision): string {
  switch (dec) {
    case "approved": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800";
    case "rejected": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800";
    case "needs_human_review": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border-amber-200 dark:border-amber-800";
    case "incomplete": return "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-700";
  }
}

function reqStatusClass(st: RequirementStatusValue): string {
  switch (st) {
    case "satisfied": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
    case "partially_satisfied": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400";
    case "unsatisfied": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
    case "unverified": return "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400";
    case "not_applicable": return "bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-500";
  }
}

function formatDuration(sec: number): string {
  if (sec < 1) return `${(sec * 1000).toFixed(0)}ms`;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(0);
  return `${m}m ${s}s`;
}

function findingPreview(sev: FindingSeverity): string {
  switch (sev) {
    case "critical": return "Must be resolved before acceptance";
    case "high": return "Should be resolved, likely blocking";
    case "medium": return "Meaningful quality concern";
    case "low": return "Minor improvement suggestion";
    case "info": return "Non-blocking observation";
  }
}

// ── Decision Banner ────────────────────────────────────────────

function DecisionBanner({ result }: { result: QualityGateResult }) {
  const iconMap: Record<QualityGateDecision, JSX.Element> = {
    approved: (
      <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
    rejected: (
      <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 9.75l4.5 4.5m0-4.5l-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
    needs_human_review: (
      <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
      </svg>
    ),
    incomplete: (
      <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  };

  return (
    <div className={`rounded-xl border-2 p-6 transition-all duration-500 animate-in fade-in ${decisionClass(result.decision)}`}>
      <div className="flex items-center gap-4">
        {iconMap[result.decision]}
        <div className="flex-1">
          <h2 className="text-2xl font-bold capitalize">{result.decision.replace(/_/g, " ")}</h2>
          <p className="text-sm mt-1 opacity-80">{result.summary}</p>
        </div>
        {result.score != null && (
          <div className="text-right shrink-0">
            <p className="text-3xl font-bold">{result.score.toFixed(0)}</p>
            <p className="text-xs opacity-60">Quality Score</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Requirement Status Badge ────────────────────────────────────

function ReqStatusBadge({ status }: { status: RequirementStatusValue }) {
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${reqStatusClass(status)}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

// ── Finding Card ────────────────────────────────────────────────

function FindingCard({ finding }: { finding: ReviewFinding }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className={`rounded-lg border overflow-hidden transition-all duration-200 ${
      finding.blocking
        ? "border-red-200 dark:border-red-800/50 bg-red-50/50 dark:bg-red-900/10"
        : "border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800"
    }`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-start gap-3 px-4 py-3 text-left hover:opacity-80 transition-opacity"
      >
        <div className="mt-0.5 shrink-0">
          {categoryIcon(finding.category)}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${severityClass(finding.severity)}`}>
              {finding.severity}
            </span>
            <span className="text-[10px] font-medium text-slate-500 dark:text-slate-400 uppercase">
              {finding.category}
            </span>
            {finding.blocking && (
              <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">
                BLOCKING
              </span>
            )}
          </div>
          <p className="text-sm font-medium text-slate-900 dark:text-white mt-1">{finding.title}</p>
          <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5 line-clamp-2">{finding.description}</p>
        </div>
        <svg
          className={`w-4 h-4 text-slate-400 shrink-0 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      <div className={`transition-all duration-300 ease-in-out overflow-hidden ${
        expanded ? "max-h-[2000px] opacity-100" : "max-h-0 opacity-0"
      }`}>
        <div className="px-4 pb-4 space-y-3 border-t border-slate-200 dark:border-slate-700 pt-3">
          {finding.file_path && (
            <p className="text-xs font-mono text-slate-500 dark:text-slate-400">
              File: {finding.file_path}
              {finding.line_start ? `:${finding.line_start}${finding.line_end && finding.line_end !== finding.line_start ? `-${finding.line_end}` : ""}` : ""}
              {finding.symbol ? ` (${finding.symbol})` : ""}
            </p>
          )}

          {finding.evidence.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">Evidence</p>
              <ul className="space-y-1">
                {finding.evidence.map((e, i) => (
                  <li key={i} className="text-xs text-slate-600 dark:text-slate-400 flex items-start gap-2">
                    <span className="text-slate-300 mt-1">-</span>
                    {e}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {finding.recommendation && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">Recommendation</p>
              <p className="text-xs text-slate-700 dark:text-slate-300">{finding.recommendation}</p>
            </div>
          )}

          <div className="flex items-center gap-3 text-[10px] text-slate-400">
            {finding.requirement_ids.length > 0 && <span>Reqs: {finding.requirement_ids.join(", ")}</span>}
            {finding.plan_step_ids.length > 0 && <span>Steps: {finding.plan_step_ids.join(", ")}</span>}
            <span>Confidence: {(finding.confidence * 100).toFixed(0)}%</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Requirement Row ─────────────────────────────────────────────

function RequirementRow({ cov }: { cov: RequirementCoverage }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-700/30 transition-colors"
      >
        <ReqStatusBadge status={cov.status} />
        <span className="flex-1 text-xs font-mono font-medium text-slate-800 dark:text-slate-200">
          {cov.requirement_id}
        </span>
        <span className="text-xs text-slate-600 dark:text-slate-400 truncate max-w-[300px]">
          {cov.requirement_description}
        </span>
        <svg
          className={`w-4 h-4 text-slate-400 shrink-0 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </button>
      <div className={`transition-all duration-300 ease-in-out overflow-hidden ${
        expanded ? "max-h-[2000px] opacity-100" : "max-h-0 opacity-0"
      }`}>
        <div className="px-4 pb-4 space-y-2 border-t border-slate-200 dark:border-slate-700 pt-3">
          {cov.plan_steps.length > 0 && (
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              <span className="font-medium">Plan steps:</span> {cov.plan_steps.join(", ")}
            </p>
          )}
          {cov.changed_files.length > 0 && (
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              <span className="font-medium">Files:</span> {cov.changed_files.join(", ")}
            </p>
          )}
          {cov.evidence.length > 0 && (
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              <span className="font-medium">Evidence:</span> {cov.evidence.join("; ")}
            </p>
          )}
          {cov.tests.length > 0 && (
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              <span className="font-medium">Tests:</span> {cov.tests.join(", ")}
            </p>
          )}
          {cov.notes && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              <span className="font-medium">Notes:</span> {cov.notes}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Stat Card ───────────────────────────────────────────────────

function StatCard({ label, value, sub, color }: {
  label: string;
  value: string | number;
  sub?: string;
  color: string;
}) {
  return (
    <div className="p-4 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 transition-all duration-200 hover:shadow-md">
      <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{label}</p>
      <p className={`mt-1 text-2xl font-bold ${color}`}>{value}</p>
      {sub && <p className="mt-1 text-[11px] text-slate-400">{sub}</p>}
    </div>
  );
}

// ── Expandable Section ─────────────────────────────────────────

function ExpandableSection({ title, subtitle, defaultOpen = false, children }: {
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden transition-all duration-200">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors"
      >
        <div>
          <span className="text-sm font-semibold text-slate-900 dark:text-white">{title}</span>
          {subtitle && <span className="ml-2 text-xs text-slate-500 dark:text-slate-400">{subtitle}</span>}
        </div>
        <svg
          className={`w-4 h-4 text-slate-400 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </button>
      <div className={`transition-all duration-300 ease-in-out overflow-hidden ${
        open ? "max-h-[5000px] opacity-100" : "max-h-0 opacity-0"
      }`}>
        <div className="px-5 pb-4">{children}</div>
      </div>
    </div>
  );
}

// ── Capabilities Badge ─────────────────────────────────────────

function CapBadge({ label, present }: { label: string; present: boolean }) {
  return (
    <div className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all ${
      present
        ? "bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800/50"
        : "bg-slate-50 dark:bg-slate-700/30 text-slate-400 dark:text-slate-500 border border-slate-200 dark:border-slate-700"
    }`}>
      {present ? (
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
        </svg>
      ) : (
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      )}
      {label}
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────

export default function ReviewPage() {
  // Input fields
  const [workspaceId, setWorkspaceId] = useState("");
  const [requirementsJson, setRequirementsJson] = useState("");
  const [planJson, setPlanJson] = useState("");
  const [patchJson, setPatchJson] = useState("");
  const [testResultJson, setTestResultJson] = useState("");
  const [repairResultJson, setRepairResultJson] = useState("");

  // Review state
  const [reviewing, setReviewing] = useState(false);
  const [report, setReport] = useState<ReviewReport | null>(null);
  const [gateResult, setGateResult] = useState<QualityGateResult | null>(null);

  // Active tabs
  const [detailTab, setDetailTab] = useState<"findings" | "requirements" | "plan" | "evidence">("findings");

  // Current demo scenario (used for quick-fill highlight + API fallback)
  const [demoScenario, setDemoScenario] = useState<"approved" | "rejected">("approved");

  // Error state
  const [error, setError] = useState<string | null>(null);

  // Whether we're using demo mode (API unavailable)
  const [demoMode, setDemoMode] = useState(false);

  // ── Helper: build demo data ──
  const buildDemoData = useCallback((isRejected: boolean, wsId: string) => {
    const demoReport: ReviewReport = {
      review_id: `review-demo-${Date.now().toString(36)}`,
      workspace_id: wsId || "review-demo",
      requirement_coverage: [
        {
          requirement_id: "REQ-001",
          requirement_description: "Expired reset tokens must be rejected",
          status: isRejected ? "unsatisfied" : "satisfied",
          plan_steps: ["STEP-001"],
          changed_files: ["auth/tokens.py"],
          evidence: isRejected
            ? ["Test failed for token expiry - assertion error on line 42"]
            : ["Tests pass for token validation (5/5)"],
          tests: ["test_expired_token", "test_valid_token"],
          notes: isRejected ? "Boundary condition off by 1 second" : "",
        },
        {
          requirement_id: "REQ-003",
          requirement_description: "Audit log must record all token operations",
          status: isRejected ? "unsatisfied" : "satisfied",
          plan_steps: ["STEP-003"],
          changed_files: isRejected ? [] : ["audit/logger.py"],
          evidence: isRejected
            ? ["No audit.log entries found in test output"]
            : ["Audit entries confirmed in tests/audit_test.py"],
          tests: ["test_audit_log"],
          notes: isRejected ? "Requirement documented but never implemented" : "",
        },
      ],
      plan_assessment: [
        { step_id: "STEP-001", step_title: "Add token validation", status: "implemented", changed_files: ["auth/tokens.py"], notes: "" },
        { step_id: "STEP-003", step_title: "Add audit logging", status: isRejected ? "missing" : "implemented", changed_files: isRejected ? [] : ["audit/logger.py"], notes: isRejected ? "Not implemented" : "" },
      ],
      findings: isRejected
        ? [
            {
              finding_id: "DET-002", category: "testing", severity: "critical",
              title: "Final tests failed",
              description: "2 of 5 tests failed: test_expired_token (AssertionError), test_audit_log (ModuleNotFoundError)",
              file_path: null, line_start: null, line_end: null, symbol: null,
              requirement_ids: ["REQ-001", "REQ-003"], plan_step_ids: ["STEP-001", "STEP-003"],
              evidence: ["FAILED test_expired_token -- AssertionError", "FAILED test_audit_log -- ModuleNotFoundError"],
              recommendation: "Fix token boundary condition and implement audit logger",
              blocking: true, confidence: 1.0,
            },
            {
              finding_id: "DET-009", category: "requirement", severity: "critical",
              title: "REQ-003 unsatisfied: audit logging missing",
              description: "No implementation evidence for audit logging requirement.",
              file_path: null, line_start: null, line_end: null, symbol: null,
              requirement_ids: ["REQ-003"], plan_step_ids: ["STEP-003"],
              evidence: ["0 matching files for 'audit'", "STEP-003 marked MISSING"],
              recommendation: "Implement audit logging before approval",
              blocking: true, confidence: 0.95,
            },
            {
              finding_id: "DET-017", category: "tampering", severity: "critical",
              title: "Test file tampering detected",
              description: "File tests/test_users.py was deleted in the patch.",
              file_path: "tests/test_users.py", line_start: 1, line_end: null, symbol: null,
              requirement_ids: ["REQ-002"], plan_step_ids: ["STEP-001"],
              evidence: ["DELETE operation on tests/test_users.py", "3 assertions removed"],
              recommendation: "Restore deleted test file",
              blocking: true, confidence: 1.0,
            },
            {
              finding_id: "DET-020", category: "security", severity: "high",
              title: "Potential command injection in token handler",
              description: "subprocess.run() with unsanitized user input and shell=True",
              file_path: "auth/tokens.py", line_start: 88, line_end: 88, symbol: "execute_reset",
              requirement_ids: [], plan_step_ids: ["STEP-001"],
              evidence: ["Line 88: subprocess.run(f'echo {user_input}', shell=True)"],
              recommendation: "Replace with safe subprocess call",
              blocking: true, confidence: 0.9,
            },
          ]
        : [
            {
              finding_id: "F-001", category: "maintainability", severity: "low",
              title: "Consider adding type hints",
              description: "validate_reset_token lacks type annotations.",
              file_path: "auth/tokens.py", line_start: 10, line_end: 15, symbol: "validate_reset_token",
              requirement_ids: [], plan_step_ids: ["STEP-001"],
              evidence: ["No type hints on function signature"],
              recommendation: "Add Python type hints",
              blocking: false, confidence: 0.6,
            },
          ],
      test_summary: {
        executed: true,
        status: isRejected ? "failed" : "passed",
        tests_passed: isRejected ? 3 : 5, tests_failed: isRejected ? 2 : 0, tests_skipped: isRejected ? 1 : 0,
        commands_total: 1, commands_passed: isRejected ? 0 : 1, commands_failed: isRejected ? 1 : 0,
        commands_rejected: 0, duration_seconds: 0.42, has_skipped: isRejected, has_timeout: false,
        environment_ready: true,
        warnings: isRejected ? ["1 test skipped -- likely newly introduced"] : [],
      },
      repair_summary: {
        attempted: isRejected,
        status: isRejected ? "max_attempts" : null,
        attempts: isRejected ? 3 : 0,
        stop_reason: isRejected ? "MAX_ATTEMPTS reached (3/3)." : "",
        remaining_failures: isRejected ? 2 : 0,
      },
      security_summary: {
        passed: !isRejected,
        blocked_patterns: isRejected ? ["subprocess.run with shell=True"] : [],
        warnings: isRejected ? ["Unsafe shell execution in auth/tokens.py:88"] : [],
      },
      scope_summary: {
        in_scope_files: ["auth/tokens.py"],
        out_of_scope_files: isRejected ? ["analytics/dashboard.py"] : [],
        warnings: isRejected ? ["Unplanned changes"] : [],
      },
      agent_summary: isRejected
        ? "CRITICAL: failures, missing req, security issue, tampering."
        : "All requirements satisfied. Tests pass (5/5). APPROVED.",
      quality_metrics: { overall: isRejected ? 28.4 : 92.5 },
      created_at: new Date().toISOString(),
      duration_seconds: 0.85,
      warnings: isRejected
        ? ["Repair loop max attempts", "Scope violation"]
        : [],
    };

    const demoGate: QualityGateResult = {
      review_id: demoReport.review_id,
      decision: isRejected ? "rejected" : "approved",
      blocking_findings: isRejected
        ? ["Final tests failed", "REQ-003 unsatisfied", "Test tampering", "Command injection"]
        : [],
      warnings: isRejected ? ["Suspicious skipped test", "Repair max attempts"] : [],
      requirements_status: isRejected ? "unsatisfied" : "satisfied",
      requirements_satisfied: isRejected ? 0 : 2,
      requirements_partial: isRejected ? 0 : 0,
      requirements_unsatisfied: isRejected ? 2 : 0,
      requirements_unverified: 0,
      verification_status: isRejected ? "failed" : "passed",
      security_status: isRejected ? "FAIL" : "PASS",
      score: isRejected ? 28.4 : 92.5,
      reason_codes: isRejected
        ? ["tests_failed", "critical_finding", "requirement_unsatisfied", "security_blocker", "test_tampering"]
        : ["review_passed"],
      summary: isRejected
        ? "REJECTED: Tests failed, audit missing, tampering, security vulnerability"
        : "APPROVED: All requirements satisfied, tests pass, security clear",
    };
    return { report: demoReport, gate: demoGate };
  }, []);

  // ── Run Review (calls backend API, falls back to demo) ──
  const handleRunReview = useCallback(async () => {
    setReviewing(true);
    setReport(null);
    setGateResult(null);
    setError(null);
    setDemoMode(false);

    const isRejected = demoScenario === "rejected";

    // Build request body from form fields
    const body: Record<string, unknown> = {
      workspace_id: workspaceId || "review-demo",
    };

    try {
      if (requirementsJson.trim()) body.requirements = JSON.parse(requirementsJson);
      if (planJson.trim()) body.implementation_plan = JSON.parse(planJson);
      if (patchJson.trim()) body.original_patch = JSON.parse(patchJson);
      if (testResultJson.trim()) body.test_result = JSON.parse(testResultJson);
      if (repairResultJson.trim()) body.repair_result = JSON.parse(repairResultJson);
    } catch {
      setError("Invalid JSON in one or more fields. Please fix or clear them.");
      setReviewing(false);
      return;
    }

    // Try real API call
    try {
      const res = await fetch("/api/v1/review/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(`API ${res.status}: ${errText.slice(0, 200)}`);
      }

      const data: ReviewResponse = await res.json();
      setReport(data.report);
      setGateResult(data.gate_result);
      setReviewing(false);
      return;
    } catch (err) {
      // API unavailable — fall back to demo
      console.warn("Review API unavailable, using demo mode:", err);
      setDemoMode(true);
    }

    // Fallback: demo mode
    setTimeout(() => {
      const { report: demoReport, gate: demoGate } = buildDemoData(isRejected, workspaceId);
      setReport(demoReport);
      setGateResult(demoGate);
      setReviewing(false);
    }, 1200);
  }, [workspaceId, requirementsJson, planJson, patchJson, testResultJson, repairResultJson, demoScenario, buildDemoData]);

  // ── Clear ──
  const handleClear = useCallback(() => {
    setReport(null);
    setGateResult(null);
    setError(null);
  }, []);

  // ── Load demo data into form fields ──
  const loadDemoData = useCallback((scenario: "approved" | "rejected") => {
    setDemoScenario(scenario);
    if (scenario === "approved") {
      setRequirementsJson(JSON.stringify({ objective: "Fix auth token validation", requirements: [{ id: "REQ-001", description: "Expired tokens must be rejected" }] }, null, 2));
      setPlanJson(JSON.stringify({ summary: "Token validation fix", objective: "Fix auth token validation", steps: [{ id: "STEP-001", title: "Add expiry check to validate_reset_token", description: "Update validate_reset_token to reject expired tokens", affected_areas: ["auth/tokens.py"] }, { id: "STEP-002", title: "Add config for TTL", description: "Add token TTL configuration", affected_areas: ["config.py"] }] }, null, 2));
      setTestResultJson(JSON.stringify({ run_id: "run-e2e", workspace_id: workspaceId || "review-demo", status: "passed", commands_total: 1, commands_passed: 1, commands_failed: 0, commands_skipped: 0, tests_total: 5, tests_passed: 5, tests_failed: 0, tests_skipped: 0, duration_seconds: 0.42, failures: [], process_results: [] }, null, 2));
      setRepairResultJson("");
      setPatchJson("");
    } else {
      setRequirementsJson(JSON.stringify({ objective: "Fix auth token validation with audit logging", requirements: [{ id: "REQ-001", description: "Expired tokens must be rejected" }, { id: "REQ-003", description: "Audit log must record all token operations" }] }, null, 2));
      setPlanJson(JSON.stringify({ summary: "Token validation + audit", objective: "Fix auth token validation with audit logging", steps: [{ id: "STEP-001", title: "Add expiry check", description: "Update validate_reset_token to reject expired tokens", affected_areas: ["auth/tokens.py"] }, { id: "STEP-003", title: "Add audit logging", description: "Add audit logging for all token operations", affected_areas: ["audit/logger.py"] }] }, null, 2));
      setTestResultJson(JSON.stringify({ run_id: "run-e2e-fail", workspace_id: workspaceId || "review-demo", status: "failed", commands_total: 1, commands_passed: 0, commands_failed: 1, commands_skipped: 0, tests_total: 5, tests_passed: 3, tests_failed: 2, tests_skipped: 1, duration_seconds: 0.42, failures: [{ test_id: "test_expired_token", test_name: "test_expired_token", status: "failed", message: "AssertionError: assert expired == True", file_path: "tests/test_auth.py", line_number: 42 }], process_results: [] }, null, 2));
      setRepairResultJson(JSON.stringify({ status: "max_attempts", attempts: 3, stop_reason: "MAX_ATTEMPTS", remaining_failures: 2 }, null, 2));
    }
  }, []);

  // ── Render ──
  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Review & Quality Gate</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Evaluate implementation quality against requirements, plan, test evidence, and security invariants — Phase 9.
        </p>
      </div>

      {/* Capabilities Strip */}
      <div className="flex flex-wrap items-center gap-2 px-4 py-3 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
        <span className="text-xs font-medium text-slate-500 dark:text-slate-400 mr-1">Quality Gate:</span>
        <CapBadge label="9 deterministic checks" present={true} />
        <CapBadge label="Requirement coverage" present={true} />
        <CapBadge label="Evidence validation" present={true} />
        <CapBadge label="Hallucination protection" present={true} />
        <CapBadge label="Read-only review" present={true} />
        <CapBadge label="LLM not required" present={true} />
      </div>

      {/* Input Form */}
      <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5 space-y-4">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-white">1. Configure Review Input</h2>

        <div>
          <label htmlFor="ws-id" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1.5">
            Workspace ID
          </label>
          <input
            id="ws-id"
            type="text"
            value={workspaceId}
            onChange={(e) => setWorkspaceId(e.target.value)}
            placeholder="e.g. ws-abc123"
            className="w-full px-3.5 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all"
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label htmlFor="req-json" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1.5">
              Requirements JSON <span className="text-slate-400 font-normal">(StructuredRequirements)</span>
            </label>
            <textarea
              id="req-json"
              value={requirementsJson}
              onChange={(e) => setRequirementsJson(e.target.value)}
              rows={4}
              placeholder={'{"objective":"Fix auth","requirements":[{"description":"Expired tokens rejected"}]}'}
              className="w-full px-3.5 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all resize-none font-mono text-[11px]"
            />
          </div>
          <div>
            <label htmlFor="plan-json" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1.5">
              Implementation Plan JSON
            </label>
            <textarea
              id="plan-json"
              value={planJson}
              onChange={(e) => setPlanJson(e.target.value)}
              rows={4}
              placeholder={'{"summary":"Add auth","steps":[{"id":"STEP-001","title":"Add validation"}]}'}
              className="w-full px-3.5 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all resize-none font-mono text-[11px]"
            />
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label htmlFor="test-json" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1.5">
              Test Result JSON <span className="text-slate-400 font-normal">(TestRunResult)</span>
            </label>
            <textarea
              id="test-json"
              value={testResultJson}
              onChange={(e) => setTestResultJson(e.target.value)}
              rows={3}
              placeholder={'{"status":"passed","tests_total":5,"tests_passed":5}'}
              className="w-full px-3.5 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all resize-none font-mono text-[11px]"
            />
          </div>
          <div>
            <label htmlFor="repair-json" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1.5">
              Repair Result JSON <span className="text-slate-400 font-normal">(optional)</span>
            </label>
            <textarea
              id="repair-json"
              value={repairResultJson}
              onChange={(e) => setRepairResultJson(e.target.value)}
              rows={3}
              placeholder={'{"status":"success","attempts":2}'}
              className="w-full px-3.5 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all resize-none font-mono text-[11px]"
            />
          </div>
        </div>

        {/* Demo mode — populate form with sample data */}
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Quick fill:</span>
          <div className="flex rounded-lg border border-slate-300 dark:border-slate-600 overflow-hidden">
            <button
              onClick={() => { loadDemoData("approved"); }}
              className={`px-3 py-1.5 text-xs font-medium transition-all ${
                demoScenario === "approved"
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                  : "bg-white dark:bg-slate-700 text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-600"
              }`}
            >
              <span className="flex items-center gap-1.5">
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                </svg>
                APPROVED
              </span>
            </button>
            <button
              onClick={() => { loadDemoData("rejected"); }}
              className={`px-3 py-1.5 text-xs font-medium transition-all ${
                demoScenario === "rejected"
                  ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                  : "bg-white dark:bg-slate-700 text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-600"
              }`}
            >
              <span className="flex items-center gap-1.5">
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
                REJECTED
              </span>
            </button>
          </div>
          <button
            onClick={() => {
              setRequirementsJson("");
              setPlanJson("");
              setPatchJson("");
              setTestResultJson("");
              setRepairResultJson("");
            }}
            className="px-2 py-1.5 text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
          >
            Clear all
          </button>
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          <button
            onClick={handleRunReview}
            disabled={reviewing}
            className="px-5 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 flex items-center gap-2"
          >
            {reviewing ? (
              <>
                <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Reviewing...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
                </svg>
                Run Review
              </>
            )}
          </button>
          <button
            onClick={handleClear}
            disabled={!report && !gateResult && !error}
            className="px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-400 text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Clear Results
          </button>
          <span className="text-xs text-slate-400 ml-auto">
            POSTs to <code className="font-mono bg-slate-100 dark:bg-slate-700 px-1 rounded">/api/v1/review/run</code>
            {demoMode && (
              <span className="ml-2 text-amber-500 font-medium">
                (falling back to demo)
              </span>
            )}
          </span>
        </div>

        {/* Error Banner */}
        {error && (
          <div className="flex items-start gap-3 p-3 rounded-lg bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800/50">
            <svg className="w-5 h-5 text-red-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126z" />
            </svg>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-red-700 dark:text-red-400">Review Error</p>
              <p className="text-xs text-red-600 dark:text-red-300 mt-0.5">{error}</p>
            </div>
            <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        )}
      </div>

      {/* Reviewing State */}
      {reviewing && (
        <div className="text-center py-12 animate-in fade-in">
          <svg className="animate-spin w-10 h-10 mx-auto text-primary-500 mb-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <h3 className="text-lg font-medium text-slate-900 dark:text-white mb-1">Running Review Pipeline</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Building context &gt; Running deterministic checks &gt; Validating evidence &gt; Invoking Quality Gate...
          </p>
          <div className="mt-4 flex items-center justify-center gap-3 text-xs text-slate-400">
            <span className="flex items-center gap-1.5">
              <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
              Context builder
            </span>
            <span className="flex items-center gap-1.5">
              <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              Deterministic review
            </span>
            <span className="flex items-center gap-1.5">
              <div className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
              Quality Gate
            </span>
          </div>
        </div>
      )}

      {/* Results */}
      {gateResult && report && !reviewing && (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
          {/* Decision Banner */}
          <DecisionBanner result={gateResult} />

          {/* Summary Stats Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatCard
              label="Requirements"
              value={`${gateResult.requirements_satisfied}/${gateResult.requirements_satisfied + gateResult.requirements_partial + gateResult.requirements_unsatisfied + gateResult.requirements_unverified}`}
              sub={gateResult.requirements_status.replace(/_/g, " ")}
              color={gateResult.requirements_status === "satisfied" ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}
            />
            <StatCard
              label="Verification"
              value={report.test_summary.status}
              sub={`${report.test_summary.tests_passed ?? 0}/${report.test_summary.tests_failed ?? 0}/${report.test_summary.tests_skipped ?? 0} p/f/s`}
              color={report.test_summary.status === "passed" ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}
            />
            <StatCard
              label="Security"
              value={report.security_summary.passed ? "PASS" : "FAIL"}
              sub={report.security_summary.warnings.length > 0 ? `${report.security_summary.warnings.length} warnings` : "No issues"}
              color={report.security_summary.passed ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}
            />
            <StatCard
              label="Findings"
              value={report.findings.length}
              sub={`${report.findings.filter(f => f.blocking).length} blocking`}
              color={report.findings.filter(f => f.blocking).length > 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}
            />
          </div>

          {/* Reason Codes */}
          {gateResult.reason_codes.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 px-4 py-3 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
              <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Reason codes:</span>
              {gateResult.reason_codes.map((rc) => (
                <span key={rc} className="px-2 py-0.5 rounded text-[10px] font-mono font-medium bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400">
                  {rc}
                </span>
              ))}
            </div>
          )}

          {/* Blocking Findings */}
          {gateResult.blocking_findings.length > 0 && (
            <div className="p-4 rounded-xl bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800/50">
              <h3 className="text-sm font-semibold text-red-800 dark:text-red-300 mb-2 flex items-center gap-2">
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                </svg>
                Blocking Issues ({gateResult.blocking_findings.length})
              </h3>
              <ul className="space-y-1.5">
                {gateResult.blocking_findings.map((bf, i) => (
                  <li key={i} className="text-sm text-red-700 dark:text-red-400 flex items-start gap-2">
                    <span className="text-red-400 mt-0.5">-</span>
                    {bf}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Metrics Score Bar */}
          {report.quality_metrics?.overall != null && (
            <div className="p-4 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Quality Score</h3>
                <span className="text-lg font-bold text-slate-900 dark:text-white">{report.quality_metrics.overall.toFixed(1)}/100</span>
              </div>
              <div className="w-full h-2 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-1000 ${
                    report.quality_metrics.overall >= 80 ? "bg-emerald-500" :
                    report.quality_metrics.overall >= 50 ? "bg-amber-500" : "bg-red-500"
                  }`}
                  style={{ width: `${report.quality_metrics.overall}%` }}
                />
              </div>
            </div>
          )}

          {/* Detail Tabs */}
          <div className="flex items-center gap-1 p-0.5 rounded-lg bg-slate-100 dark:bg-slate-700 w-fit">
            {(["findings", "requirements", "plan", "evidence"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setDetailTab(tab)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium capitalize transition-all ${
                  detailTab === tab
                    ? "bg-white dark:bg-slate-600 text-slate-900 dark:text-white shadow-sm"
                    : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-white"
                }`}
              >
                {tab}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          <div className="space-y-3">
            {detailTab === "findings" && (
              <>
                {report.findings.length > 0 ? (
                  report.findings.map((f) => (
                    <FindingCard key={f.finding_id} finding={f} />
                  ))
                ) : (
                  <div className="text-center py-12">
                    <svg className="w-12 h-12 mx-auto text-emerald-400 mb-3" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                    </svg>
                    <p className="text-sm text-slate-500 dark:text-slate-400">No findings — all checks passed!</p>
                  </div>
                )}
              </>
            )}

            {detailTab === "requirements" && (
              <div className="space-y-2">
                {report.requirement_coverage.map((cov) => (
                  <RequirementRow key={cov.requirement_id} cov={cov} />
                ))}
              </div>
            )}

            {detailTab === "plan" && (
              <div className="space-y-2">
                {report.plan_assessment.map((pa) => (
                  <div key={pa.step_id} className="p-4 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-medium text-slate-500 dark:text-slate-400">{pa.step_id}</span>
                        <span className="text-sm font-medium text-slate-900 dark:text-white">{pa.step_title}</span>
                      </div>
                      <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
                        pa.status === "implemented"
                          ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                          : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                      }`}>
                        {pa.status}
                      </span>
                    </div>
                    {pa.changed_files.length > 0 && (
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        Files: {pa.changed_files.join(", ")}
                      </p>
                    )}
                    {pa.notes && <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">{pa.notes}</p>}
                  </div>
                ))}
              </div>
            )}

            {detailTab === "evidence" && (
              <div className="space-y-4">
                <ExpandableSection title="Test Summary" defaultOpen>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/30">
                      <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase">Status</p>
                      <p className="mt-1 font-medium text-slate-900 dark:text-white">{report.test_summary.status}</p>
                    </div>
                    <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/30">
                      <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase">Duration</p>
                      <p className="mt-1 font-mono font-medium text-slate-900 dark:text-white">{formatDuration(report.test_summary.duration_seconds)}</p>
                    </div>
                    <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/30">
                      <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase">Tests</p>
                      <p className="mt-1 font-medium text-slate-900 dark:text-white">
                        {report.test_summary.tests_passed}/{report.test_summary.tests_passed! + report.test_summary.tests_failed! + report.test_summary.tests_skipped!} p/f/s
                      </p>
                    </div>
                    <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/30">
                      <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase">Commands</p>
                      <p className="mt-1 font-medium text-slate-900 dark:text-white">
                        {report.test_summary.commands_passed}/{report.test_summary.commands_total} passed
                      </p>
                    </div>
                  </div>
                </ExpandableSection>

                <ExpandableSection title="Repair History" subtitle={report.repair_summary.attempted ? `${report.repair_summary.attempts} attempt(s)` : "None"}>
                  {report.repair_summary.attempted ? (
                    <div className="space-y-2 text-xs">
                      <p className="text-slate-700 dark:text-slate-300">
                        <span className="font-medium">Status:</span> {report.repair_summary.status ?? "N/A"}
                      </p>
                      <p className="text-slate-700 dark:text-slate-300">
                        <span className="font-medium">Stop reason:</span> {report.repair_summary.stop_reason || "N/A"}
                      </p>
                      <p className="text-slate-700 dark:text-slate-300">
                        <span className="font-medium">Remaining failures:</span> {report.repair_summary.remaining_failures}
                      </p>
                    </div>
                  ) : (
                    <p className="text-xs text-slate-400">No repair was needed.</p>
                  )}
                </ExpandableSection>

                <ExpandableSection title="Security Summary" subtitle={report.security_summary.passed ? "Passed" : "Issues"}>
                  {report.security_summary.passed ? (
                    <p className="text-xs text-emerald-600 dark:text-emerald-400">No security issues detected.</p>
                  ) : (
                    <div className="space-y-2 text-xs">
                      {report.security_summary.blocked_patterns.length > 0 && (
                        <div>
                          <p className="font-medium text-slate-700 dark:text-slate-300">Blocked patterns:</p>
                          <ul className="list-disc list-inside text-red-600 dark:text-red-400 mt-1">
                            {report.security_summary.blocked_patterns.map((p, i) => (
                              <li key={i}>{p}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {report.security_summary.warnings.length > 0 && (
                        <div>
                          <p className="font-medium text-slate-700 dark:text-slate-300">Warnings:</p>
                          {report.security_summary.warnings.map((w, i) => (
                            <p key={i} className="text-amber-600 dark:text-amber-400">{w}</p>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </ExpandableSection>

                <ExpandableSection title="Agent Summary">
                  <p className="text-xs text-slate-600 dark:text-slate-400 leading-relaxed">
                    {report.agent_summary || "No agent summary available."}
                  </p>
                </ExpandableSection>
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3 pt-2">
            <button
              onClick={handleRunReview}
              className="px-5 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 transition-all duration-150 flex items-center gap-2"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
              </svg>
              Re-run Review
            </button>
            <button
              onClick={handleClear}
              className="px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-400 text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
            >
              New Review
            </button>
          </div>
        </div>
      )}

      {/* Empty State */}
      {!report && !gateResult && !reviewing && (
        <div className="text-center py-16">
          <svg className="w-16 h-16 mx-auto text-slate-300 dark:text-slate-600 mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
          </svg>
          <h3 className="text-lg font-medium text-slate-900 dark:text-white mb-1">Ready to Review</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400 max-w-md mx-auto">
            Paste JSON from Phases 4-8 above, or leave fields blank for demo mode. 
            The Quality Gate will evaluate requirements, plan, test evidence, and security invariants.
          </p>
          <div className="mt-6 flex items-center justify-center gap-4 text-xs text-slate-400">
            <div className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-emerald-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
              Context builder
            </div>
            <div className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-blue-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944" />
              </svg>
              Deterministic review
            </div>
            <div className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374" />
              </svg>
              Quality Gate
            </div>
          </div>
        </div>
      )}

      {/* Metadata */}
      {report && (
        <div className="text-center text-[10px] text-slate-400">
          Review {report.review_id} — {formatDuration(report.duration_seconds)} — {new Date(report.created_at).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
