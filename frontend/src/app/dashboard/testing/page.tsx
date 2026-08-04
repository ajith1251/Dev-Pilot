"use client";

import { useState, useEffect, useCallback } from "react";

// ── Type Definitions (matching backend models/testing.py) ──────

type CommandCategory = "test" | "lint" | "typecheck" | "build" | "other";
type ExecutionStatus = "passed" | "failed" | "timeout" | "rejected" | "error" | "skipped" | "environment_not_ready" | "running" | "pending";
type FailureCategory = "assertion_failure" | "import_error" | "syntax_error" | "type_error" | "build_failure" | "lint_failure" | "timeout" | "dependency_error" | "configuration_error" | "execution_error" | "unknown";

interface CommandCandidate {
  command_id: string;
  category: CommandCategory;
  executable: string;
  arguments: string[];
  working_directory: string;
  source: string;
  confidence: number;
  reason: string;
}

interface ExecutionStep {
  step_id: string;
  category: CommandCategory;
  executable: string;
  arguments: string[];
  working_directory: string;
  timeout_seconds: number;
  required: boolean;
  source: string;
  reason: string;
}

interface ExecutionPlan {
  plan_id: string;
  workspace_id: string;
  workspace_root: string;
  steps: ExecutionStep[];
  max_total_timeout_seconds: number;
  metadata: Record<string, unknown>;
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

interface Capabilities {
  supported_categories: string[];
  supported_frameworks: string[];
  max_commands_per_run: number;
  default_timeout_seconds: number;
  max_output_bytes: number;
  environment_sanitization: boolean;
  workspace_isolation: boolean;
  llm_required: boolean;
}

// ── Helpers ────────────────────────────────────────────────────

function catClass(cat: CommandCategory): string {
  switch (cat) {
    case "test": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
    case "lint": return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400";
    case "typecheck": return "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400";
    case "build": return "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400";
    default: return "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-400";
  }
}

function statusClass(st: ExecutionStatus): string {
  switch (st) {
    case "passed": return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
    case "failed": return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
    case "timeout": return "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400";
    case "rejected": return "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-400";
    case "error": return "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400";
    case "skipped": return "bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400";
    case "environment_not_ready": return "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400";
    case "running": return "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400";
    default: return "bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400";
  }
}

function statusIcon(st: ExecutionStatus) {
  switch (st) {
    case "passed":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
        </svg>
      );
    case "failed":
    case "error":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      );
    case "timeout":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      );
    case "running":
      return (
        <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      );
    default:
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14" />
        </svg>
      );
  }
}

function catIcon(cat: CommandCategory) {
  switch (cat) {
    case "test":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
      );
    case "lint":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
        </svg>
      );
    case "typecheck":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
        </svg>
      );
    case "build":
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17l-5.06-5.06M12 3v3m0 0l-2.12 2.12M12 6l2.12-2.12M3 12h3m0 0l2.12 2.12M6 12l2.12-2.12m9.46 3.05l5.06-5.06" />
        </svg>
      );
    default:
      return (
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zM3.75 12h.007v.008H3.75V12zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm-.375 5.25h.007v.008H3.75v-.008zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" />
        </svg>
      );
  }
}

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

function formatDuration(sec: number): string {
  if (sec < 1) return `${(sec * 1000).toFixed(0)}ms`;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(0);
  return `${m}m ${s}s`;
}

function sourceLabel(src: string): string {
  switch (src) {
    case "pyproject": return "pyproject.toml";
    case "package_json": return "package.json";
    case "phase2_detection": return "Phase 2";
    case "default_framework_rule": return "Framework Rule";
    case "user_approved": return "User Approved";
    default: return src;
  }
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

// ── Failure Card ───────────────────────────────────────────────

function FailureCard({ failure }: { failure: TestFailure }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-red-200 dark:border-red-800/50 bg-red-50/50 dark:bg-red-900/10 overflow-hidden transition-all duration-200">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-start gap-3 px-4 py-3 text-left hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
      >
        <div className="mt-0.5 shrink-0 text-red-500">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
          </svg>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <code className="text-xs font-mono font-medium text-red-800 dark:text-red-300">
              {failure.test_name || failure.failure_id}
            </code>
            <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${failCatClass(failure.failure_type)}`}>
              {failure.failure_type.replace(/_/g, " ")}
            </span>
          </div>
          <p className="text-xs text-red-700 dark:text-red-400 mt-0.5 line-clamp-2">{failure.message}</p>
          {failure.file_path && (
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1 font-mono">
              {failure.file_path}{failure.line_number ? `:${failure.line_number}` : ""}
            </p>
          )}
        </div>
        <svg
          className={`w-4 h-4 text-slate-400 shrink-0 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      {/* Expanded detail */}
      <div
        className={`transition-all duration-300 ease-in-out overflow-hidden ${
          expanded ? "max-h-[3000px] opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <div className="px-4 pb-4 space-y-3 border-t border-red-200 dark:border-red-800/50 pt-3">
          {failure.stack_trace && (
            <div>
              <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">Stack Trace</p>
              <pre className="p-3 rounded-lg bg-slate-900 text-green-400 text-[11px] font-mono leading-relaxed overflow-x-auto max-h-48 overflow-y-auto">
                {failure.stack_trace}
              </pre>
            </div>
          )}
          {failure.related_output && (
            <div>
              <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">Related Output</p>
              <pre className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/50 text-xs font-mono text-slate-700 dark:text-slate-300 leading-relaxed overflow-x-auto max-h-32 overflow-y-auto">
                {failure.related_output}
              </pre>
            </div>
          )}
          <div className="flex items-center gap-3 text-[11px] text-slate-400">
            {failure.framework && <span>Framework: {failure.framework}</span>}
            {failure.step_id && <span>Step: {failure.step_id}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Process Result Row ─────────────────────────────────────────

function ProcessResultRow({ pr }: { pr: ProcessExecutionResult }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-slate-50 dark:hover:bg-slate-700/30 transition-colors"
      >
        <div className={`shrink-0 ${statusClass(pr.status)} p-0.5 rounded`}>{statusIcon(pr.status)}</div>
        <code className="flex-1 text-xs font-mono text-slate-700 dark:text-slate-300 truncate">{pr.command}</code>
        <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${statusClass(pr.status)}`}>
          {pr.status}
        </span>
        {pr.duration_seconds != null && (
          <span className="text-[11px] text-slate-400 font-mono">{formatDuration(pr.duration_seconds)}</span>
        )}
        <svg
          className={`w-3.5 h-3.5 text-slate-400 shrink-0 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
          fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </button>
      <div
        className={`transition-all duration-300 ease-in-out overflow-hidden ${
          expanded ? "max-h-[2000px] opacity-100" : "max-h-0 opacity-0"
        }`}
      >
        <div className="px-4 pb-3 space-y-2 border-t border-slate-200 dark:border-slate-700 pt-2">
          {pr.exit_code != null && (
            <div className="flex items-center gap-2 text-[11px]">
              <span className="text-slate-500">Exit code:</span>
              <span className={`font-mono font-medium ${pr.exit_code === 0 ? "text-emerald-600" : "text-red-600"}`}>
                {pr.exit_code}
              </span>
            </div>
          )}
          {pr.stdout && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">
                stdout{pr.stdout_truncated ? " (truncated)" : ""}
              </p>
              <pre className="p-2 rounded bg-slate-50 dark:bg-slate-700/30 text-[11px] font-mono text-slate-700 dark:text-slate-300 leading-relaxed overflow-x-auto max-h-40 overflow-y-auto">
                {pr.stdout}
              </pre>
            </div>
          )}
          {pr.stderr && (
            <div>
              <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">
                stderr{pr.stderr_truncated ? " (truncated)" : ""}
              </p>
              <pre className="p-2 rounded bg-red-50 dark:bg-red-900/10 text-[11px] font-mono text-red-700 dark:text-red-400 leading-relaxed overflow-x-auto max-h-40 overflow-y-auto">
                {pr.stderr}
              </pre>
            </div>
          )}
          {pr.timed_out && (
            <div className="flex items-center gap-1.5 text-[11px] text-orange-600">
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              Process timed out
            </div>
          )}
          {pr.started_at && pr.finished_at && (
            <div className="text-[10px] text-slate-400">
              {new Date(pr.started_at).toLocaleTimeString()} → {new Date(pr.finished_at).toLocaleTimeString()}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Step Card ──────────────────────────────────────────────────

function StepCard({ step, index }: { step: ExecutionStep; index: number }) {
  return (
    <div className="flex gap-4">
      <div className="flex flex-col items-center">
        <div className="w-8 h-8 rounded-full bg-slate-100 dark:bg-slate-700 flex items-center justify-center shrink-0">
          <span className="text-xs font-medium text-slate-500 dark:text-slate-400">{index + 1}</span>
        </div>
        <div className="w-px flex-1 bg-slate-200 dark:bg-slate-700 my-1" />
      </div>
      <div className="flex-1 pb-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`text-xs font-medium px-2 py-0.5 rounded ${catClass(step.category)}`}>
                <span className="inline-flex items-center gap-1">
                  {catIcon(step.category)}
                  {step.category}
                </span>
              </span>
              <code className="text-sm font-mono text-slate-800 dark:text-slate-200">
                {step.executable} {step.arguments.join(" ")}
              </code>
            </div>
            {step.reason && (
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">{step.reason}</p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {step.required && (
              <span className="text-[10px] font-medium text-rose-500 dark:text-rose-400 border border-rose-200 dark:border-rose-800 px-1.5 py-0.5 rounded">
                required
              </span>
            )}
            <span className="text-[10px] font-mono text-slate-400">{step.timeout_seconds}s</span>
          </div>
        </div>
        <div className="flex items-center gap-2 mt-1.5">
          <span className="text-[10px] text-slate-400 bg-slate-100 dark:bg-slate-700 px-1.5 py-0.5 rounded">
            {sourceLabel(step.source)}
          </span>
          {step.working_directory !== "." && (
            <span className="text-[10px] font-mono text-slate-400">{step.working_directory}</span>
          )}
        </div>
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

// ── Main Page Component ────────────────────────────────────────

export default function TestingPage() {
  // Input state
  const [workspaceId, setWorkspaceId] = useState("");
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [changedFiles, setChangedFiles] = useState("");

  // Plan state
  const [planning, setPlanning] = useState(false);
  const [plan, setPlan] = useState<ExecutionPlan | null>(null);
  const [planWarnings, setPlanWarnings] = useState<string[]>([]);
  const [planReasoning, setPlanReasoning] = useState("");

  // Run state
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<TestRunResult | null>(null);

  // Capabilities state
  const [caps, setCaps] = useState<Capabilities | null>(null);

  // Tab state for result view
  const [resultTab, setResultTab] = useState<"summary" | "processes" | "failures">("summary");

  // Load capabilities on mount
  useEffect(() => {
    // Simulate API call in demo
    const timer = setTimeout(() => {
      setCaps({
        supported_categories: ["test", "lint", "typecheck", "build"],
        supported_frameworks: ["pytest", "unittest", "vitest", "jest", "generic"],
        max_commands_per_run: 10,
        default_timeout_seconds: 60,
        max_output_bytes: 1_048_576,
        environment_sanitization: true,
        workspace_isolation: true,
        llm_required: false,
      });
    }, 300);
    return () => clearTimeout(timer);
  }, []);

  // ── Build Plan ──
  const handleBuildPlan = useCallback(async () => {
    if (!workspaceId.trim() || !workspaceRoot.trim()) return;
    setPlanning(true);
    setPlan(null);
    setResult(null);

    // Simulate plan creation
    setTimeout(() => {
      const files = changedFiles
        ? changedFiles.split("\n").map((f) => f.trim()).filter(Boolean)
        : [];

      // Build demo plan from fields
      const steps: ExecutionStep[] = [
        {
          step_id: "STEP-001",
          category: "test",
          executable: "python",
          arguments: ["-m", "pytest", "-q", ...(files.length > 0 ? files.filter(f => f.includes("test")) : [])],
          working_directory: ".",
          timeout_seconds: 60,
          required: true,
          source: "phase2_detection",
          reason: files.length > 0
            ? `Tests relevant to changed files: ${files.join(", ")}`
            : "Standard Python test suite",
        },
        {
          step_id: "STEP-002",
          category: "lint",
          executable: "python",
          arguments: ["-m", "pylint", "--exit-zero"],

          working_directory: ".",
          timeout_seconds: 30,
          required: false,
          source: "pyproject",
          reason: "Lint check for code quality",
        },
      ];

      // If there are changed files, add typecheck step
      if (files.length > 0) {
        steps.push({
          step_id: "STEP-003",
          category: "typecheck",
          executable: "python",
          arguments: ["-m", "mypy", "--ignore-missing-imports", ...files],
          working_directory: ".",
          timeout_seconds: 30,
          required: false,
          source: "phase2_detection",
          reason: "Type checking changed files",
        });
      }

      setPlan({
        plan_id: `plan-${Date.now().toString(36)}`,
        workspace_id: workspaceId,
        workspace_root: workspaceRoot,
        steps,
        max_total_timeout_seconds: 300,
        metadata: {},
      });
      setPlanWarnings(files.length === 0 ? ["No changed files specified — running full suite"] : []);
      setPlanReasoning(`Found ${steps.length} relevant commands for workspace "${workspaceId}". Prioritized test execution based on ${files.length > 0 ? `${files.length} changed files` : "default discovery"}.`);
      setPlanning(false);
    }, 1500);
  }, [workspaceId, workspaceRoot, changedFiles]);

  // ── Execute Plan ──
  const handleExecute = useCallback(async () => {
    if (!plan) return;
    setRunning(true);
    setResult(null);

    // Simulate execution
    setTimeout(() => {
      const passed = plan.steps.length > 1 ? plan.steps.length - 1 : plan.steps.length;
      const failed = 1;

      const processResults: ProcessExecutionResult[] = plan.steps.map((step, i) => ({
        step_id: step.step_id,
        command: `${step.executable} ${step.arguments.join(" ")}`,
        category: step.category,
        status: i === 1 ? "failed" : "passed" as ExecutionStatus,
        exit_code: i === 1 ? 1 : 0,
        stdout: i === 1
          ? "________________________ FAILURES ________________________\ntests/test_auth.py::test_token_expiry - AssertionError: assert True == False\n\nExpected token to be expired but got valid."
          : "collected 5 items\ntests/test_auth.py .....\n\n=================== 5 passed in 0.42s ====================",
        stderr: "",
        stdout_truncated: false,
        stderr_truncated: false,
        started_at: new Date(Date.now() - (plan.steps.length - i) * 2000).toISOString(),
        finished_at: new Date(Date.now() - (plan.steps.length - 1 - i) * 1000).toISOString(),
        duration_seconds: Math.random() * 2 + 0.3,
        timed_out: false,
      }));

      const failures: TestFailure[] = [
        {
          failure_id: "FAIL-001",
          framework: "pytest",
          test_name: "tests/test_auth.py::test_token_expiry",
          file_path: "tests/test_auth.py",
          line_number: 15,
          message: "AssertionError: assert True == False\n\nExpected: token.is_expired() to return True\nActual: returned False",
          failure_type: "assertion_failure",
          stack_trace: "test_token_expiry = <function test_token_expiry at 0x...>\n\n    def test_token_expiry():\n        tm = TokenManager()\n        token = tm.create_token(\"user@example.com\")\n>       assert tm.is_token_expired(token) == True\nE       AssertionError: assert True == False",
          related_output: "FAILED tests/test_auth.py::test_token_expiry - AssertionError: assert True == False",
          step_id: "STEP-001",
        },
        {
          failure_id: "FAIL-002",
          framework: "pylint",
          test_name: "Lint: auth/tokens.py",
          file_path: "auth/tokens.py",
          line_number: 42,
          message: "W0612: Unused variable 'old_token' (unused-variable)",
          failure_type: "lint_failure",
          stack_trace: null,
          related_output: "auth/tokens.py:42:12: W0612: Unused variable 'old_token' (unused-variable)",
          step_id: "STEP-002",
        },
      ];

      setResult({
        run_id: `run-${Date.now().toString(36)}`,
        workspace_id: workspaceId,
        status: "failed",
        commands_total: plan.steps.length,
        commands_passed: passed,
        commands_failed: failed,
        commands_skipped: 0,
        tests_total: 8,
        tests_passed: 5,
        tests_failed: 2,
        tests_skipped: 1,
        failures,
        process_results: processResults,
        duration_seconds: 3.7,
        summary: `Ran ${plan.steps.length} commands: ${passed} passed, ${failed} failed. 8 tests: 5 passed, 2 failed, 1 skipped.`,
        warnings: [],
        metadata: {},
      });
      setRunning(false);
    }, 2500);
  }, [plan, workspaceId]);

  // ── Render ──
  return (
    <div className="space-y-8">
      {/* ── Header ── */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Test Agent & Execution</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Discover candidate commands, build execution plans, run controlled tests, and inspect normalized results — Phase 7.
        </p>
      </div>

      {/* ── Capabilities Strip ── */}
      {caps && (
        <div className="flex flex-wrap items-center gap-2 px-4 py-3 rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700">
          <span className="text-xs font-medium text-slate-500 dark:text-slate-400 mr-1">Capabilities:</span>
          <CapabilitiesBadge label={`${caps.max_commands_per_run} max commands`} present={true} />
          <CapabilitiesBadge label={`${caps.default_timeout_seconds}s default timeout`} present={true} />
          <CapabilitiesBadge label="Environment sanitization" present={caps.environment_sanitization} />
          <CapabilitiesBadge label="Workspace isolation" present={caps.workspace_isolation} />
          <CapabilitiesBadge label="LLM required" present={caps.llm_required} />
        </div>
      )}

      {/* ── Workspace & Plan Form ── */}
      <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5 space-y-4">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-white">1. Configure Workspace</h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
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
          <div>
            <label htmlFor="ws-root" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1.5">
              Workspace Root Path
            </label>
            <input
              id="ws-root"
              type="text"
              value={workspaceRoot}
              onChange={(e) => setWorkspaceRoot(e.target.value)}
              placeholder="e.g. /tmp/devpilot/ws-abc123"
              className="w-full px-3.5 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all"
            />
          </div>
        </div>

        <div>
          <label htmlFor="changed-files" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1.5">
            Changed Files <span className="text-slate-400 font-normal">(one per line — from Phase 6 patch)</span>
          </label>
          <textarea
            id="changed-files"
            value={changedFiles}
            onChange={(e) => setChangedFiles(e.target.value)}
            rows={3}
            placeholder={"auth/tokens.py\ntests/test_auth.py"}
            className="w-full px-3.5 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all resize-none font-mono"
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleBuildPlan}
            disabled={planning || !workspaceId.trim() || !workspaceRoot.trim()}
            className="px-5 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 flex items-center gap-2"
          >
            {planning ? (
              <>
                <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Discovering Commands...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
                Build Execution Plan
              </>
            )}
          </button>
          <button
            onClick={() => {
              setPlan(null);
              setResult(null);
            }}
            disabled={!plan && !result}
            className="px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-400 text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Clear
          </button>
        </div>
      </div>

      {/* ── Execution Plan ── */}
      {plan && !result && (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-300">
          {/* Plan Header */}
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-sm font-semibold text-slate-900 dark:text-white">Execution Plan</h2>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                  {plan.plan_id} · {plan.steps.length} step{plan.steps.length !== 1 ? "s" : ""} · max {plan.max_total_timeout_seconds}s total
                </p>
              </div>
              <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                Ready
              </span>
            </div>

            {/* Plan Reasoning */}
            <div className="mb-4 p-3 rounded-lg bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700">
              <p className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-1">Test Agent Reasoning</p>
              <p className="text-sm text-slate-700 dark:text-slate-300">{planReasoning}</p>
            </div>

            {/* Warnings */}
            {planWarnings.length > 0 && (
              <div className="mb-4 p-3 rounded-lg bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-800/50">
                <div className="flex items-center gap-1.5 mb-1">
                  <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                  </svg>
                  <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">Warnings</p>
                </div>
                <ul className="space-y-1">
                  {planWarnings.map((w, i) => (
                    <li key={i} className="text-xs text-amber-700 dark:text-amber-400 ml-5 list-disc">{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Steps */}
            <h3 className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-3">Steps</h3>
            <div className="space-y-0">
              {plan.steps.map((step, i) => (
                <StepCard key={step.step_id} step={step} index={i} />
              ))}
            </div>
          </div>

          {/* Execute Button */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleExecute}
              disabled={running}
              className="px-5 py-2.5 rounded-lg bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 flex items-center gap-2"
            >
              {running ? (
                <>
                  <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Executing...
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.348a1.125 1.125 0 010 1.971l-11.54 6.347a1.125 1.125 0 01-1.667-.985V5.653z" />
                  </svg>
                  Execute Plan
                </>
              )}
            </button>
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.007v.008H12v-.008z" />
              </svg>
              All commands validated by Execution Policy
            </div>
          </div>
        </div>
      )}

      {/* ── Running State ── */}
      {running && (
        <div className="text-center py-12 animate-in fade-in">
          <svg className="animate-spin w-10 h-10 mx-auto text-primary-500 mb-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <h3 className="text-lg font-medium text-slate-900 dark:text-white mb-1">Executing Test Plan</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Running controlled execution — capturing stdout, stderr, exit codes, and enforcing timeouts...
          </p>
          <div className="mt-4 flex items-center justify-center gap-2">
            <div className="flex items-center gap-1.5 text-xs text-slate-400">
              <div className="w-2 h-2 rounded-full bg-emerald-500" />
              Policy validated
            </div>
            <div className="flex items-center gap-1.5 text-xs text-slate-400">
              <div className="w-2 h-2 rounded-full bg-blue-500" />
              Workspace isolated
            </div>
            <div className="flex items-center gap-1.5 text-xs text-slate-400">
              <div className="w-2 h-2 rounded-full bg-amber-500" />
              Environment sanitized
            </div>
          </div>
        </div>
      )}

      {/* ── Test Run Results ── */}
      {result && (
        <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-300">
          {/* Result Header */}
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <div className="flex items-center justify-between mb-5">
              <div>
                <h2 className="text-sm font-semibold text-slate-900 dark:text-white">Test Run Results</h2>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                  {result.run_id} · {formatDuration(result.duration_seconds)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className={`px-2.5 py-1 rounded-full text-xs font-medium flex items-center gap-1 ${statusClass(result.status)}`}>
                  {statusIcon(result.status)}
                  {result.status}
                </span>
              </div>
            </div>

            {/* Summary */}
            <p className="text-sm text-slate-700 dark:text-slate-300 mb-4">{result.summary}</p>

            {/* Warnings */}
            {result.warnings.length > 0 && (
              <div className="mb-4 p-3 rounded-lg bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-800/50">
                {result.warnings.map((w, i) => (
                  <p key={i} className="text-xs text-amber-700 dark:text-amber-400">{w}</p>
                ))}
              </div>
            )}

            {/* Counts Grid */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
              {/* Command counts */}
              <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700">
                <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Commands</p>
                <p className="mt-1 text-xl font-bold text-slate-900 dark:text-white">{result.commands_total}</p>
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-[11px] text-emerald-600 dark:text-emerald-400">{result.commands_passed} passed</span>
                  {result.commands_failed > 0 && (
                    <span className="text-[11px] text-red-600 dark:text-red-400">{result.commands_failed} failed</span>
                  )}
                </div>
              </div>

              {/* Test counts (if available) */}
              <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700">
                <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                  Tests{result.tests_total == null ? " (unavailable)" : ""}
                </p>
                {result.tests_total != null ? (
                  <>
                    <p className="mt-1 text-xl font-bold text-slate-900 dark:text-white">{result.tests_total}</p>
                    <div className="flex items-center gap-2 mt-1">
                      <span className="text-[11px] text-emerald-600 dark:text-emerald-400">{result.tests_passed} passed</span>
                      {(result.tests_failed ?? 0) > 0 && (
                        <span className="text-[11px] text-red-600 dark:text-red-400">{result.tests_failed} failed</span>
                      )}
                      {(result.tests_skipped ?? 0) > 0 && (
                        <span className="text-[11px] text-slate-400">{result.tests_skipped} skipped</span>
                      )}
                    </div>
                  </>
                ) : (
                  <p className="mt-2 text-sm text-slate-400">Framework counts not available</p>
                )}
              </div>

              {/* Failures count */}
              <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700">
                <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Failures</p>
                <p className={`mt-1 text-xl font-bold ${result.failures.length > 0 ? "text-red-600 dark:text-red-400" : "text-emerald-600 dark:text-emerald-400"}`}>
                  {result.failures.length}
                </p>
                <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
                  {result.failures.length > 0 ? "Need investigation" : "All clear"}
                </p>
              </div>

              {/* Duration */}
              <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-700/50 border border-slate-200 dark:border-slate-700">
                <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Duration</p>
                <p className="mt-1 text-xl font-bold text-slate-900 dark:text-white font-mono">{formatDuration(result.duration_seconds)}</p>
                <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">Elapsed</p>
              </div>
            </div>

            {/* Tabs for detailed results */}
            <div className="flex items-center gap-1 p-0.5 rounded-lg bg-slate-100 dark:bg-slate-700 w-fit mb-4">
              {(["summary", "processes", "failures"] as const).map((tab) => (
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
                  <p><strong className="text-slate-800 dark:text-slate-200">Run:</strong> {result.run_id}</p>
                  <p><strong className="text-slate-800 dark:text-slate-200">Workspace:</strong> {result.workspace_id}</p>
                  <p><strong className="text-slate-800 dark:text-slate-200">Status:</strong> {result.status}</p>
                  <p><strong className="text-slate-800 dark:text-slate-200">Duration:</strong> {formatDuration(result.duration_seconds)}</p>
                  <p><strong className="text-slate-800 dark:text-slate-200">Commands:</strong> {result.commands_passed}/{result.commands_total} passed</p>
                  {result.tests_total != null && (
                    <p><strong className="text-slate-800 dark:text-slate-200">Tests:</strong> {result.tests_passed}/{result.tests_total} passed ({result.tests_skipped} skipped)</p>
                  )}
                  <p><strong className="text-slate-800 dark:text-slate-200">Failures:</strong> {result.failures.length}</p>
                </div>
              </div>
            )}

            {resultTab === "processes" && (
              <div className="space-y-2">
                {result.process_results.map((pr) => (
                  <ProcessResultRow key={pr.step_id} pr={pr} />
                ))}
              </div>
            )}

            {resultTab === "failures" && (
              <div className="space-y-2">
                {result.failures.length > 0 ? (
                  result.failures.map((f) => (
                    <FailureCard key={f.failure_id} failure={f} />
                  ))
                ) : (
                  <div className="text-center py-8">
                    <svg className="w-12 h-12 mx-auto text-emerald-400 mb-3" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                    </svg>
                    <p className="text-sm text-slate-500 dark:text-slate-400">No failures — all tests passed!</p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleExecute}
              disabled={running}
              className="px-4 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 flex items-center gap-2"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
              </svg>
              Re-run Tests
            </button>
            <button
              onClick={() => {
                setPlan(null);
                setResult(null);
              }}
              className="px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-400 text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors"
            >
              New Test Run
            </button>
          </div>
        </div>
      )}

      {/* ── Empty State ── */}
      {!plan && !result && !planning && (
        <div className="text-center py-16">
          <svg className="w-16 h-16 mx-auto text-slate-300 dark:text-slate-600 mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
          </svg>
          <h3 className="text-lg font-medium text-slate-900 dark:text-white mb-1">Ready to Test</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400 max-w-md mx-auto">
            Enter a workspace path above to discover candidate commands and build an execution plan. 
            The Test Agent will determine what to verify, and the Execution Policy will ensure only safe commands run.
          </p>
          <div className="mt-6 flex items-center justify-center gap-4 text-xs text-slate-400">
            <div className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-emerald-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
              Command Discovery
            </div>
            <div className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-blue-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
              </svg>
              Execution Policy
            </div>
            <div className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-violet-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 006 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0118 16.5h-2.25m-7.5 0h7.5m-7.5 0l-1 3m8.5-3l1 3m0 0l.5 1.5m-.5-1.5h-9.5m0 0l-.5 1.5m.75-9l3-3 2.148 2.148A12.061 12.061 0 0116.5 7.605" />
              </svg>
              Result Normalization
            </div>
          </div>
        </div>
      )}

      {/* ── Planning Loading ── */}
      {planning && !plan && (
        <div className="text-center py-12">
          <svg className="animate-spin w-10 h-10 mx-auto text-primary-500 mb-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <h3 className="text-lg font-medium text-slate-900 dark:text-white mb-1">Building Execution Plan</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Discovering commands, validating against execution policy...
          </p>
        </div>
      )}
    </div>
  );
}
