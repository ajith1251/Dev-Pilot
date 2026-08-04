"use client";

import { useState, useCallback, useEffect } from "react";

// ── Type Definitions (matching backend models/repair.py) ──────

type Repairability = "repairable" | "possibly_repairable" | "not_repairable" | "environmental" | "insufficient_context";
type FailureCategory = "assertion_failure" | "import_error" | "syntax_error" | "type_error" | "build_failure" | "lint_failure" | "timeout" | "dependency_error" | "configuration_error" | "execution_error" | "unknown";
type RepairStatus = "success" | "failed" | "no_repair" | "environmental" | "max_attempts" | "no_progress" | "repeated_patch" | "unsafe_repair" | "error";
type CommandCategory = "test" | "lint" | "typecheck" | "build" | "other";
type ExecutionStatus = "passed" | "failed" | "timeout" | "rejected" | "error" | "skipped" | "environment_not_ready" | "running" | "pending";

interface FailureMapping {
  failure_id: string;
  file_path: string;
  line_number: number | null;
  test_name: string;
  changed_file: string | null;
  plan_step: string | null;
  is_patch_related: boolean | null;
}

interface FailureEvidence {
  evidence_type: string;
  value: string;
  relevant: boolean;
}

interface FailureDiagnosis {
  diagnosis_id: string;
  run_id: string;
  failure_ids: string[];
  category: FailureCategory;
  summary: string;
  likely_cause: string;
  confidence: number;
  repairability: Repairability;
  affected_files: string[];
  affected_symbols: string[];
  related_plan_steps: string[];
  related_patch_changes: string[];
  failure_mappings: FailureMapping[];
  evidence: FailureEvidence[];
  warnings: string[];
  context_used: string[];
}

interface FileChange {
  operation: "CREATE" | "MODIFY" | "DELETE";
  file_path: string;
  content: string | null;
  old_content: string | null;
  original_hash: string | null;
}

interface PatchSet {
  patch_id: string;
  plan_id: string;
  changes: FileChange[];
  metadata: Record<string, unknown>;
}

interface RepairProposal {
  proposal_id: string;
  status: string;
  diagnosis_id: string;
  attempt_number: number;
  target_failure_ids: string[];
  patch: PatchSet | null;
  reason: string;
  expected_effect: string;
  context_used: string[];
  warnings: string[];
}

interface TestFailure {
  failure_id: string;
  framework: string;
  test_name: string;
  file_path: string | null;
  line_number: number | null;
  message: string;
  failure_type: FailureCategory;
  stack_trace: string | null;
  related_output: string | null;
  step_id: string | null;
}

interface ProcessExecutionResult {
  step_id: string;
  command: string;
  category: CommandCategory;
  status: ExecutionStatus;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  stdout_truncated: boolean;
  stderr_truncated: boolean;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  timed_out: boolean;
}

interface TestRunResult {
  run_id: string;
  workspace_id: string;
  status: ExecutionStatus;
  commands_total: number;
  commands_passed: number;
  commands_failed: number;
  commands_skipped: number;
  tests_total: number | null;
  tests_passed: number | null;
  tests_failed: number | null;
  tests_skipped: number | null;
  failures: TestFailure[];
  process_results: ProcessExecutionResult[];
  duration_seconds: number;
  summary: string;
  warnings: string[];
  metadata: Record<string, unknown>;
}

interface RepairAttempt {
  attempt_id: string;
  attempt_number: number;
  diagnosis: FailureDiagnosis;
  proposal: RepairProposal | null;
  patch_application: unknown | null;
  test_result: TestRunResult | null;
  started_at: string | null;
  finished_at: string | null;
  status: string;
}

interface RepairResult {
  session_id: string;
  status: RepairStatus;
  initial_test_result: TestRunResult;
  final_test_result: TestRunResult | null;
  attempts: RepairAttempt[];
  best_attempt: number | null;
  stop_reason: string;
  remaining_failures: TestFailure[];
  workspace_id: string;
  summary: string;
  duration_seconds: number;
}

interface RepairCapabilities {
  max_attempts: number;
  max_provider_retries: number;
  max_context_bytes: number;
  allow_test_modification: boolean;
  allow_config_modification: boolean;
  diagnosis_categories: string[];
  repairability_categories: string[];
  uses_llm: boolean;
}

// ── Helpers ────────────────────────────────────────────────────

function failCatClass(fc: FailureCategory): string {
  switch (fc) {
    case "assertion_failure": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
    case "import_error":
    case "dependency_error": return "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400";
    case "syntax_error":
    case "type_error": return "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400";
    case "build_failure": return "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400";
    case "lint_failure": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400";
    case "timeout": return "bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-400";
    case "configuration_error": return "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400";
    default: return "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-400";
  }
}

function repairabilityClass(r: Repairability): string {
  switch (r) {
    case "repairable": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
    case "possibly_repairable": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400";
    case "not_repairable":
    case "insufficient_context": return "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400";
    case "environmental": return "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400";
    default: return "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400";
  }
}

function repairStatusClass(st: RepairStatus): string {
  switch (st) {
    case "success": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
    case "failed":
    case "error": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
    case "no_repair":
    case "environmental": return "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400";
    case "max_attempts":
    case "no_progress":
    case "repeated_patch": return "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400";
    case "unsafe_repair": return "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400";
    default: return "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400";
  }
}

function statusIcon(st: ExecutionStatus | RepairStatus) {
  if (st === "passed" || st === "success") {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
      </svg>
    );
  }
  if (st === "failed" || st === "error" || st === "unsafe_repair") {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    );
  }
  if (st === "max_attempts" || st === "no_progress" || st === "repeated_patch") {
    return (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
      </svg>
    );
  }
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14" />
    </svg>
  );
}

function formatDuration(sec: number): string {
  if (sec < 1) return `${(sec * 1000).toFixed(0)}ms`;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(0);
  return `${m}m ${s}s`;
}

function confClass(val: number): string {
  if (val >= 0.8) return "bg-emerald-500";
  if (val >= 0.5) return "bg-amber-500";
  return "bg-slate-400";
}

// ── Expandable Section ─────────────────────────────────────────

function ExpandableSection({
  title,
  subtitle,
  defaultOpen = false,
  children,
}: {
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
          {subtitle && (
            <span className="ml-2 text-xs text-slate-500 dark:text-slate-400">{subtitle}</span>
          )}
        </div>
        <svg
          className={`w-4 h-4 text-slate-400 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </button>
      <div
        className={`transition-all duration-300 ease-in-out overflow-hidden ${
          open ? "max-h-[2000px] opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <div className="px-5 pb-4">{children}</div>
      </div>
    </div>
  );
}

// ── Capabilities Badge ─────────────────────────────────────────

function CapabilitiesBadge({ label, present }: { label: string; present: boolean }) {
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

// ── Repair Attempt Card ────────────────────────────────────────

function RepairAttemptCard({ attempt, index }: { attempt: RepairAttempt; index: number }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden transition-all duration-200">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-700/30 transition-colors"
      >
        <div className="w-7 h-7 rounded-full bg-slate-100 dark:bg-slate-700 flex items-center justify-center shrink-0">
          <span className="text-xs font-medium text-slate-500 dark:text-slate-400">{index + 1}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${repairStatusClass(attempt.status as RepairStatus)}`}>
              {attempt.status}
            </span>
            {attempt.diagnosis.summary && (
              <span className="text-xs text-slate-700 dark:text-slate-300 truncate">{attempt.diagnosis.summary}</span>
            )}
          </div>
          <div className="flex items-center gap-3 mt-0.5">
            {attempt.test_result && (
              <span className="text-[11px] text-slate-500">
                Tests: {attempt.test_result.tests_passed ?? "?"}/{attempt.test_result.tests_total ?? "?"} passed
              </span>
            )}
            {attempt.proposal?.reason && (
              <span className="text-[11px] text-slate-400 truncate">{attempt.proposal.reason}</span>
            )}
          </div>
        </div>
        <svg
          className={`w-3.5 h-3.5 text-slate-400 shrink-0 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </button>
      <div
        className={`transition-all duration-300 ease-in-out overflow-hidden ${
          expanded ? "max-h-[3000px] opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <div className="px-4 pb-4 space-y-3 border-t border-slate-200 dark:border-slate-700 pt-3">
          {/* Diagnosis */}
          <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/30">
            <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">Diagnosis</p>
            <p className="text-xs text-slate-700 dark:text-slate-300 mb-1">{attempt.diagnosis.likely_cause}</p>
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${failCatClass(attempt.diagnosis.category)}`}>
                {attempt.diagnosis.category.replace(/_/g, " ")}
              </span>
              <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${repairabilityClass(attempt.diagnosis.repairability)}`}>
                {attempt.diagnosis.repairability.replace(/_/g, " ")}
              </span>
              <span className="text-[10px] text-slate-400">
                Confidence: {(attempt.diagnosis.confidence * 100).toFixed(0)}%
              </span>
            </div>
          </div>

          {/* Affected Files */}
          {attempt.diagnosis.affected_files.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">Affected Files</p>
              <div className="flex flex-wrap gap-1.5">
                {attempt.diagnosis.affected_files.map((f, i) => (
                  <span key={i} className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400">
                    {f}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Repair Patch */}
          {attempt.proposal?.patch && attempt.proposal.patch.changes.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">Patch Changes</p>
              <div className="space-y-1">
                {attempt.proposal.patch.changes.map((change, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className={`font-mono font-medium ${
                      change.operation === "CREATE" ? "text-emerald-600" :
                      change.operation === "MODIFY" ? "text-blue-600" :
                      change.operation === "DELETE" ? "text-red-600" : ""
                    }`}>
                      {change.operation}
                    </span>
                    <code className="text-slate-700 dark:text-slate-300">{change.file_path}</code>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Test Result Summary */}
          {attempt.test_result && (
            <div className="flex items-center gap-3 text-xs text-slate-500">
              <span>Status: <strong className="text-slate-700 dark:text-slate-300">{attempt.test_result.status}</strong></span>
              <span>Duration: {formatDuration(attempt.test_result.duration_seconds)}</span>
              <span>Failures: {attempt.test_result.failures.length}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Diagnosis Card ─────────────────────────────────────────────

function DiagnosisCard({ diag }: { diag: FailureDiagnosis }) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-5 py-4 text-left hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className={`px-2 py-0.5 rounded text-xs font-medium ${repairabilityClass(diag.repairability)}`}>
            {diag.repairability.replace(/_/g, " ")}
          </span>
          <span className={`px-2 py-0.5 rounded text-xs font-medium ${failCatClass(diag.category)}`}>
            {diag.category.replace(/_/g, " ")}
          </span>
          <span className="text-xs text-slate-500">{diag.diagnosis_id}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-400">{(diag.confidence * 100).toFixed(0)}% confident</span>
          <svg
            className={`w-4 h-4 text-slate-400 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
            fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
          </svg>
        </div>
      </button>
      <div
        className={`transition-all duration-300 ease-in-out overflow-hidden ${
          expanded ? "max-h-[3000px] opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <div className="px-5 pb-4 space-y-3 border-t border-slate-200 dark:border-slate-700 pt-3">
          {/* Summary */}
          <p className="text-sm text-slate-700 dark:text-slate-300">{diag.summary}</p>

          {/* Likely Cause */}
          <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/30 border border-slate-200 dark:border-slate-700">
            <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">Likely Cause</p>
            <p className="text-sm text-slate-800 dark:text-slate-200">{diag.likely_cause}</p>
          </div>

          {/* Confidence bar */}
          <div>
            <div className="flex items-center justify-between text-xs text-slate-500 mb-1">
              <span>Confidence</span>
              <span>{(diag.confidence * 100).toFixed(0)}%</span>
            </div>
            <div className="w-full h-2 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-700 ${confClass(diag.confidence)}`}
                style={{ width: `${diag.confidence * 100}%` }}
              />
            </div>
          </div>

          {/* Warnings */}
          {diag.warnings.length > 0 && (
            <div className="p-3 rounded-lg bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-800/50">
              <p className="text-[10px] font-semibold text-amber-700 dark:text-amber-400 uppercase tracking-wider mb-1">Warnings</p>
              <ul className="space-y-1">
                {diag.warnings.map((w, i) => (
                  <li key={i} className="text-xs text-amber-700 dark:text-amber-400 ml-4 list-disc">{w}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Affected Files */}
          {diag.affected_files.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1.5">Affected Files</p>
              <div className="flex flex-wrap gap-1.5">
                {diag.affected_files.map((f, i) => (
                  <span key={i} className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-400">
                    {f}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Failure Mappings */}
          {diag.failure_mappings.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1.5">Failure Mappings</p>
              <div className="space-y-1">
                {diag.failure_mappings.map((m, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className="font-mono text-slate-500">{m.failure_id}</span>
                    <span className="text-slate-400">→</span>
                    <code className="text-slate-700 dark:text-slate-300">{m.file_path}:{m.line_number ?? "?"}</code>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                      m.is_patch_related ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                      : m.is_patch_related === false ? "bg-slate-100 text-slate-500 dark:bg-slate-700"
                      : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                    }`}>
                      {m.is_patch_related === true ? "patch-related" :
                       m.is_patch_related === false ? "pre-existing" : "unknown"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Context Used */}
          {diag.context_used.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">Context Used</p>
              <div className="flex flex-wrap gap-1.5">
                {diag.context_used.map((c, i) => (
                  <span key={i} className="text-[10px] px-2 py-0.5 rounded bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400">
                    {c}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Evidence */}
          {diag.evidence.length > 0 && (
            <ExpandableSection title="Evidence" subtitle={`${diag.evidence.length} items`}>
              <div className="space-y-1.5">
                {diag.evidence.map((e, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    <span className={`shrink-0 mt-0.5 w-1.5 h-1.5 rounded-full ${e.relevant ? "bg-emerald-500" : "bg-slate-400"}`} />
                    <span className="text-slate-500 font-medium capitalize">{e.evidence_type.replace(/_/g, " ")}:</span>
                    <span className="text-slate-700 dark:text-slate-300 font-mono text-[10px] leading-relaxed break-all">{e.value}</span>
                  </div>
                ))}
              </div>
            </ExpandableSection>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main Page Component ────────────────────────────────────────

export default function RepairPage() {
  // Diagnose state
  const [diagnoseLoading, setDiagnoseLoading] = useState(false);
  const [diagnosis, setDiagnosis] = useState<FailureDiagnosis | null>(null);

  // Repair workflow state
  const [repairRunning, setRepairRunning] = useState(false);
  const [repairResult, setRepairResult] = useState<RepairResult | null>(null);

  // Capabilities
  const [caps, setCaps] = useState<RepairCapabilities | null>(null);

  // Workspace / failure input
  const [workspaceId, setWorkspaceId] = useState("");
  const [failingTests, setFailingTests] = useState("");

  // Tab state for repair result
  const [resultTab, setResultTab] = useState<"summary" | "diagnoses" | "attempts" | "failures">("summary");

  // Load capabilities on mount
  useEffect(() => {
    const timer = setTimeout(() => {
      setCaps({
        max_attempts: 3,
        max_provider_retries: 1,
        max_context_bytes: 16384,
        allow_test_modification: false,
        allow_config_modification: false,
        diagnosis_categories: [
          "assertion_failure", "syntax_error", "import_error", "type_error",
          "build_failure", "lint_failure", "timeout",
          "dependency_error", "configuration_error", "execution_error", "unknown",
        ],
        repairability_categories: [
          "repairable", "possibly_repairable", "not_repairable",
          "environmental", "insufficient_context",
        ],
        uses_llm: true,
      });
    }, 300);
    return () => clearTimeout(timer);
  }, []);

  // ── Diagnose ──
  const handleDiagnose = useCallback(async () => {
    if (!workspaceId.trim()) return;
    setDiagnoseLoading(true);
    setDiagnosis(null);
    setRepairResult(null);

    // Simulate diagnosis
    setTimeout(() => {
      const failures = failingTests
        ? failingTests.split("\n").map((f) => f.trim()).filter(Boolean)
        : ["test_calc_is_positive", "test_auth_token_expiry"];

      const diag: FailureDiagnosis = {
        diagnosis_id: `diag-${Date.now().toString(36)}`,
        run_id: `run-${Date.now().toString(36)}`,
        failure_ids: ["FAIL-001", failures.length > 1 ? "FAIL-002" : "FAIL-001"].slice(0, failures.length > 1 ? 2 : 1),
        category: failures.some((f) => f.toLowerCase().includes("syntax")) ? "syntax_error" : "assertion_failure",
        summary: `Analyzed ${failures.length} test failure(s) in workspace "${workspaceId}".`,
        likely_cause: failures.some((f) => f.toLowerCase().includes("positive"))
          ? "Boundary condition error: comparison operator is incorrect (used >= instead of >)"
          : "Assertion failure in test logic; production code likely has incorrect return value.",
        confidence: 0.82,
        repairability: "repairable",
        affected_files: ["calc.py", "tests/test_calc.py"],
        affected_symbols: ["is_positive"],
        related_plan_steps: ["STEP-001"],
        related_patch_changes: ["CHANGE-001"],
        failure_mappings: [
          {
            failure_id: "FAIL-001",
            file_path: "tests/test_calc.py",
            line_number: 12,
            test_name: "test_is_positive",
            changed_file: "calc.py",
            plan_step: "STEP-001",
            is_patch_related: true,
          },
        ],
        evidence: [
          { evidence_type: "test_output", value: "FAILED test_calc.py::test_is_positive - AssertionError", relevant: true },
          { evidence_type: "exit_code", value: "1", relevant: true },
          { evidence_type: "stack_trace", value: "test_is_positive() -> assert is_positive(0) == False", relevant: true },
        ],
        warnings: failures.length > 2 ? ["Multiple failure categories detected — prioritising first category"] : [],
        context_used: [
          "ImplementationPlan",
          "Original PatchSet",
          "TestRunResult",
          "Repository Code: calc.py",
          "Repository Code: test_calc.py",
        ],
      };

      setDiagnosis(diag);
      setDiagnoseLoading(false);
    }, 2000);
  }, [workspaceId, failingTests]);

  // ── Run Repair ──
  const handleRepair = useCallback(async () => {
    if (!diagnosis) return;
    setRepairRunning(true);
    setRepairResult(null);

    // Simulate repair workflow
    setTimeout(() => {
      const attempt1Result: TestRunResult = {
        run_id: `run-att1-${Date.now().toString(36)}`,
        workspace_id: workspaceId,
        status: "passed",
        commands_total: 2,
        commands_passed: 2,
        commands_failed: 0,
        commands_skipped: 0,
        tests_total: 5,
        tests_passed: 5,
        tests_failed: 0,
        tests_skipped: 0,
        failures: [],
        process_results: [
          {
            step_id: "STEP-001",
            command: "python -m pytest tests/test_calc.py -q",
            category: "test",
            status: "passed",
            exit_code: 0,
            stdout: "collected 5 items\ntests/test_calc.py .....\n\n=================== 5 passed in 0.35s ====================",
            stderr: "",
            stdout_truncated: false,
            stderr_truncated: false,
            started_at: new Date(Date.now() - 5000).toISOString(),
            finished_at: new Date(Date.now() - 1000).toISOString(),
            duration_seconds: 0.85,
            timed_out: false,
          },
        ],
        duration_seconds: 1.2,
        summary: "2 commands: 2 passed, 0 failed. 5 tests: 5 passed, 0 failed, 0 skipped.",
        warnings: [],
        metadata: {},
      };

      const initialTestResult: TestRunResult = {
        run_id: `run-init-${Date.now().toString(36)}`,
        workspace_id: workspaceId,
        status: "failed",
        commands_total: 2,
        commands_passed: 1,
        commands_failed: 1,
        commands_skipped: 0,
        tests_total: 5,
        tests_passed: 4,
        tests_failed: 1,
        tests_skipped: 0,
        failures: [
          {
            failure_id: "FAIL-001",
            framework: "pytest",
            test_name: "test_calc_is_positive",
            file_path: "tests/test_calc.py",
            line_number: 12,
            message: "AssertionError: assert is_positive(0) == False",
            failure_type: "assertion_failure",
            stack_trace: "test_calc.py:12: test_is_positive\n    assert is_positive(0) == False\nE   AssertionError",
            related_output: "FAILED test_calc.py::test_is_positive - AssertionError",
            step_id: "STEP-001",
          },
        ],
        process_results: [
          {
            step_id: "STEP-001",
            command: "python -m pytest tests/test_calc.py -q",
            category: "test",
            status: "failed",
            exit_code: 1,
            stdout: "collected 5 items\ntests/test_calc.py ...F.\n\n=================== 4 passed, 1 failed in 0.42s ====================",
            stderr: "",
            stdout_truncated: false,
            stderr_truncated: false,
            started_at: new Date(Date.now() - 15000).toISOString(),
            finished_at: new Date(Date.now() - 13000).toISOString(),
            duration_seconds: 0.52,
            timed_out: false,
          },
        ],
        duration_seconds: 0.8,
        summary: "2 commands: 1 passed, 1 failed. 5 tests: 4 passed, 1 failed, 0 skipped.",
        warnings: [],
        metadata: {},
      };

      const result: RepairResult = {
        session_id: `session-${Date.now().toString(36)}`,
        status: "success",
        initial_test_result: initialTestResult,
        final_test_result: attempt1Result,
        attempts: [
          {
            attempt_id: `att-${Date.now().toString(36)}-1`,
            attempt_number: 1,
            diagnosis: diagnosis,
            proposal: {
              proposal_id: `prop-${Date.now().toString(36)}`,
              status: "proposed",
              diagnosis_id: diagnosis.diagnosis_id,
              attempt_number: 1,
              target_failure_ids: ["FAIL-001"],
              patch: {
                patch_id: `patch-${Date.now().toString(36)}`,
                plan_id: "plan-demo",
                changes: [
                  {
                    operation: "MODIFY",
                    file_path: "calc.py",
                    content: "def is_positive(n):\n    return n > 0\n",
                    old_content: "def is_positive(n):\n    return n >= 0\n",
                    original_hash: "abc123",
                  },
                ],
                metadata: {},
              },
              reason: "Boundary condition: changed '>= 0' to '> 0'",
              expected_effect: "0 will no longer be considered positive",
              context_used: ["calc.py", "test_calc.py::test_is_positive"],
              warnings: [],
            },
            patch_application: { status: "applied" },
            test_result: attempt1Result,
            started_at: new Date(Date.now() - 10000).toISOString(),
            finished_at: new Date(Date.now() - 1000).toISOString(),
            status: "success",
          },
        ],
        best_attempt: 0,
        stop_reason: "All tests passed after 1 repair attempt(s)",
        remaining_failures: [],
        workspace_id: workspaceId,
        summary: "Repair session completed successfully. 1 attempt, 0 remaining failures.",
        duration_seconds: 3.2,
      };

      setRepairResult(result);
      setRepairRunning(false);
    }, 3000);
  }, [diagnosis, workspaceId]);

  // ── Render ──
  return (
    <div className="space-y-8">
      {/* ── Header ── */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Repair Agent</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Diagnose test failures and run bounded automated repair workflows — Phase 8. The Fix Agent proposes patches,
          but deterministic Phase 6 and Phase 7 controls remain authoritative.
        </p>
      </div>

      {/* ── Capabilities Strip ── */}
      {caps && (
        <div className="flex flex-wrap items-center gap-2 px-4 py-3 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
          <span className="text-xs font-medium text-slate-500 dark:text-slate-400 mr-1">Capabilities:</span>
          <CapabilitiesBadge label={`Max ${caps.max_attempts} attempts`} present={true} />
          <CapabilitiesBadge label="LLM-powered diagnosis" present={caps.uses_llm} />
          <CapabilitiesBadge label="Test modification" present={caps.allow_test_modification} />
          <CapabilitiesBadge label="Config modification" present={caps.allow_config_modification} />
          <CapabilitiesBadge label={`${caps.diagnosis_categories.length} diagnosis categories`} present={true} />
        </div>
      )}

      {/* ── Input Section ── */}
      <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5 space-y-4">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-white">1. Configure Diagnosis Input</h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label htmlFor="repair-ws-id" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1.5">
              Workspace ID
            </label>
            <input
              id="repair-ws-id"
              type="text"
              value={workspaceId}
              onChange={(e) => setWorkspaceId(e.target.value)}
              placeholder="e.g. ws-abc123"
              className="w-full px-3.5 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all"
            />
          </div>
          <div className="flex items-end">
            <div className="px-3 py-2 rounded-lg bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700 text-xs text-slate-500 w-full">
              <span className="font-medium text-slate-700 dark:text-slate-300">Source:</span> Phase 7 TestRunResult
            </div>
          </div>
        </div>

        <div>
          <label htmlFor="failing-tests" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1.5">
            Failing Tests <span className="text-slate-400 font-normal">(one per line — from Phase 7 failures)</span>
          </label>
          <textarea
            id="failing-tests"
            value={failingTests}
            onChange={(e) => setFailingTests(e.target.value)}
            rows={3}
            placeholder={"test_calc_is_positive (AssertionError)\ntest_auth_token_expiry (AssertionError)"}
            className="w-full px-3.5 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all resize-none font-mono"
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleDiagnose}
            disabled={diagnoseLoading || !workspaceId.trim()}
            className="px-5 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 flex items-center gap-2"
          >
            {diagnoseLoading ? (
              <>
                <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Diagnosing...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
                </svg>
                Diagnose Failures
              </>
            )}
          </button>
          <button
            onClick={() => {
              setDiagnosis(null);
              setRepairResult(null);
            }}
            disabled={!diagnosis && !repairResult}
            className="px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-400 text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Clear
          </button>
        </div>
      </div>

      {/* ── Diagnosis Results ── */}
      {diagnosis && !repairResult && !repairRunning && (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-300">
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white">2. Diagnosis Results</h2>
          <DiagnosisCard diag={diagnosis} />

          {/* Repair action */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleRepair}
              disabled={repairRunning || diagnosis.repairability === "not_repairable" || diagnosis.repairability === "insufficient_context"}
              className="px-5 py-2.5 rounded-lg bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 flex items-center gap-2"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17l-5.06-5.06M12 3v3m0 0l-2.12 2.12M12 6l2.12-2.12M3 12h3m0 0l2.12 2.12M6 12l2.12-2.12m9.46 3.05l5.06-5.06" />
              </svg>
              Run Repair Workflow
            </button>
            {diagnosis.repairability === "not_repairable" && (
              <span className="text-xs text-slate-500">This failure is not suitable for automated repair</span>
            )}
            {diagnosis.repairability === "environmental" && (
              <span className="text-xs text-amber-600">Environmental failure — no code repair needed</span>
            )}
          </div>
        </div>
      )}

      {/* ── Running State ── */}
      {repairRunning && (
        <div className="text-center py-12 animate-in fade-in">
          <svg className="animate-spin w-10 h-10 mx-auto text-emerald-500 mb-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <h3 className="text-lg font-medium text-slate-900 dark:text-white mb-1">Running Bounded Repair Loop</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Diagnosing → FixAgent → RepairPolicy → PatchEngine → TestAgent → Evaluating...
          </p>
          <div className="mt-4 flex items-center justify-center gap-2">
            <div className="flex items-center gap-1.5 text-xs text-slate-400">
              <div className="w-2 h-2 rounded-full bg-emerald-500" />
              Bounded at {caps?.max_attempts ?? 3} attempts
            </div>
            <div className="flex items-center gap-1.5 text-xs text-slate-400">
              <div className="w-2 h-2 rounded-full bg-blue-500" />
              No-progress detection
            </div>
            <div className="flex items-center gap-1.5 text-xs text-slate-400">
              <div className="w-2 h-2 rounded-full bg-amber-500" />
              Worsening rollback
            </div>
          </div>
        </div>
      )}

      {/* ── Repair Results ── */}
      {repairResult && (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-300">
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white">3. Repair Results</h2>

          {/* Result Header */}
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold text-slate-900 dark:text-white">Repair Session</h3>
                  <span className={`px-2.5 py-1 rounded-full text-xs font-medium flex items-center gap-1 ${repairStatusClass(repairResult.status)}`}>
                    {statusIcon(repairResult.status)}
                    {repairResult.status.replace(/_/g, " ")}
                  </span>
                </div>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                  {repairResult.session_id} · {repairResult.attempts.length} attempt(s) · {formatDuration(repairResult.duration_seconds)}
                </p>
              </div>
            </div>

            {/* Summary */}
            <p className="text-sm text-slate-700 dark:text-slate-300 mb-4">{repairResult.summary}</p>

            {/* Stop Reason */}
            <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/30 border border-slate-200 dark:border-slate-700 mb-4">
              <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-0.5">Stop Reason</p>
              <p className="text-sm text-slate-800 dark:text-slate-200">{repairResult.stop_reason}</p>
            </div>

            {/* Counts Grid */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
              {/* Attempts */}
              <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700">
                <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Attempts</p>
                <p className="mt-1 text-xl font-bold text-slate-900 dark:text-white">{repairResult.attempts.length}</p>
                <p className="text-[11px] text-slate-500 mt-1">
                  {repairResult.attempts.filter((a) => a.status === "success").length} successful
                </p>
              </div>

              {/* Initial Test State */}
              <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700">
                <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Initial Tests</p>
                <p className="mt-1 text-xl font-bold text-red-600 dark:text-red-400">
                  {repairResult.initial_test_result.tests_failed ?? "?"}
                </p>
                <p className="text-[11px] text-slate-500 mt-1">failing</p>
              </div>

              {/* Final Test State */}
              <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700">
                <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Final Tests</p>
                <p className={`mt-1 text-xl font-bold ${repairResult.final_test_result && (repairResult.final_test_result.tests_failed ?? 0) === 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
                  {repairResult.final_test_result?.tests_failed ?? "?"}
                </p>
                <p className="text-[11px] text-slate-500 mt-1">
                  {repairResult.final_test_result && (repairResult.final_test_result.tests_failed ?? 0) === 0 ? "all passing" : "remaining"}
                </p>
              </div>

              {/* Remaining Failures */}
              <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700">
                <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Remaining</p>
                <p className={`mt-1 text-xl font-bold ${repairResult.remaining_failures.length === 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
                  {repairResult.remaining_failures.length}
                </p>
                <p className="text-[11px] text-slate-500 mt-1">failures</p>
              </div>
            </div>

            {/* Progress visualization */}
            <div className="mb-4">
              <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">Repair Progress</p>
              <div className="flex items-center gap-2">
                {repairResult.attempts.map((attempt, i) => (
                  <div key={attempt.attempt_id} className="flex items-center gap-2">
                    <div className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium ${
                      attempt.status === "success"
                        ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                        : attempt.status === "failed"
                        ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                        : "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400"
                    }`}>
                      <span>Attempt {i + 1}</span>
                      {attempt.status === "success" ? (
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                        </svg>
                      ) : (
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      )}
                    </div>
                    {i < repairResult.attempts.length - 1 && (
                      <svg className="w-4 h-4 text-slate-400" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                      </svg>
                    )}
                  </div>
                ))}
                <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400">
                  <span>End</span>
                  <span className={`w-2 h-2 rounded-full ${
                    repairResult.status === "success" ? "bg-emerald-500" : "bg-slate-400"
                  }`} />
                </div>
              </div>
            </div>

            {/* Tabs */}
            <div className="flex items-center gap-1 p-0.5 rounded-lg bg-slate-100 dark:bg-slate-700 w-fit mb-4">
              {(["summary", "diagnoses", "attempts", "failures"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setResultTab(tab)}
                  className={`px-3 py-1.5 rounded-md text-xs font-medium capitalize transition-all ${
                    resultTab === tab
                      ? "bg-white dark:bg-slate-600 text-slate-900 dark:text-white shadow-sm"
                      : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-white"
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>

            {/* Tab Content */}
            {resultTab === "summary" && (
              <div className="space-y-2">
                <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/30 text-xs text-slate-600 dark:text-slate-400 leading-relaxed">
                  <p><strong className="text-slate-800 dark:text-slate-200">Session:</strong> {repairResult.session_id}</p>
                  <p><strong className="text-slate-800 dark:text-slate-200">Workspace:</strong> {repairResult.workspace_id}</p>
                  <p><strong className="text-slate-800 dark:text-slate-200">Status:</strong> {repairResult.status.replace(/_/g, " ")}</p>
                  <p><strong className="text-slate-800 dark:text-slate-200">Duration:</strong> {formatDuration(repairResult.duration_seconds)}</p>
                  <p><strong className="text-slate-800 dark:text-slate-200">Attempts:</strong> {repairResult.attempts.length}</p>
                  <p><strong className="text-slate-800 dark:text-slate-200">Best attempt:</strong> #{repairResult.best_attempt !== null ? repairResult.best_attempt + 1 : "N/A"}</p>
                  <p><strong className="text-slate-800 dark:text-slate-200">Initial failures:</strong> {repairResult.initial_test_result.failures.length}</p>
                  <p><strong className="text-slate-800 dark:text-slate-200">Remaining failures:</strong> {repairResult.remaining_failures.length}</p>
                </div>
              </div>
            )}

            {resultTab === "diagnoses" && (
              <div className="space-y-3">
                {repairResult.attempts.map((attempt) => (
                  <DiagnosisCard key={attempt.attempt_id} diag={attempt.diagnosis} />
                ))}
              </div>
            )}

            {resultTab === "attempts" && (
              <div className="space-y-2">
                {repairResult.attempts.map((attempt, i) => (
                  <RepairAttemptCard key={attempt.attempt_id} attempt={attempt} index={i} />
                ))}
              </div>
            )}

            {resultTab === "failures" && (
              <div className="space-y-2">
                {repairResult.remaining_failures.length > 0 ? (
                  repairResult.remaining_failures.map((f) => (
                    <div key={f.failure_id} className="p-3 rounded-lg border border-red-200 dark:border-red-800/50 bg-red-50/50 dark:bg-red-900/10">
                      <div className="flex items-center gap-2 mb-1">
                        <code className="text-xs font-mono font-medium text-red-800 dark:text-red-300">{f.test_name}</code>
                        <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${failCatClass(f.failure_type)}`}>
                          {f.failure_type.replace(/_/g, " ")}
                        </span>
                      </div>
                      <p className="text-xs text-red-700 dark:text-red-400">{f.message}</p>
                    </div>
                  ))
                ) : (
                  <div className="text-center py-8">
                    <svg className="w-12 h-12 mx-auto text-emerald-400 mb-3" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                    </svg>
                    <p className="text-sm text-slate-500 dark:text-slate-400">No remaining failures — all repaired!</p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleDiagnose}
              disabled={diagnoseLoading}
              className="px-4 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 flex items-center gap-2"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
              </svg>
              New Diagnosis
            </button>
            <button
              onClick={() => {
                setDiagnosis(null);
                setRepairResult(null);
              }}
              className="px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-400 text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
            >
              Start Over
            </button>
          </div>
        </div>
      )}

      {/* ── Empty State ── */}
      {!diagnosis && !repairResult && !diagnoseLoading && !repairRunning && (
        <div className="text-center py-16">
          <svg className="w-16 h-16 mx-auto text-slate-300 dark:text-slate-600 mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17l-5.06-5.06M12 3v3m0 0l-2.12 2.12M12 6l2.12-2.12M3 12h3m0 0l2.12 2.12M6 12l2.12-2.12m9.46 3.05l5.06-5.06" />
          </svg>
          <h3 className="text-lg font-medium text-slate-900 dark:text-white mb-1">Failure Diagnosis & Repair</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400 max-w-md mx-auto">
            Enter a workspace with failing tests (from Phase 7) to diagnose failures, classify repairability,
            and run the bounded repair loop. The Fix Agent will propose targeted patches, and the
            deterministic safety controls will validate every step.
          </p>
          <div className="mt-6 flex items-center justify-center gap-4 text-xs text-slate-400">
            <div className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-emerald-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
              Failure Diagnosis
            </div>
            <div className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-blue-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17l-5.06-5.06M12 3v3m0 0l-2.12 2.12M12 6l2.12-2.12M3 12h3m0 0l2.12 2.12M6 12l2.12-2.12m9.46 3.05l5.06-5.06" />
              </svg>
              Fix Agent
            </div>
            <div className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
              </svg>
              Bounded Loop
            </div>
            <div className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-rose-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
              </svg>
              Safety Controls
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
