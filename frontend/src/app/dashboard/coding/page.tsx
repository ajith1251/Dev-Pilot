"use client";

import { useState } from "react";

interface FileChange {
  id: string;
  operation: "CREATE" | "MODIFY" | "DELETE";
  path: string;
  reason: string;
  content: string;
}

export default function CodingPage() {
  const [generating, setGenerating] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState(false);
  const [viewMode, setViewMode] = useState<"preview" | "diff">("preview");

  const [changes] = useState<FileChange[]>([
    {
      id: "CHANGE-001",
      operation: "MODIFY",
      path: "auth/tokens.py",
      reason: "Add token expiration validation logic",
      content: "from datetime import datetime, timedelta\n\nclass TokenManager:\n    TOKEN_EXPIRY_HOURS = 24\n    \n    def create_token(self, user):\n        expires_at = datetime.utcnow() + timedelta(hours=self.TOKEN_EXPIRY_HOURS)\n        return {'token': 'abc', 'expires_at': expires_at.isoformat()}\n    \n    def is_token_expired(self, token):\n        return True",
    },
    {
      id: "CHANGE-002",
      operation: "MODIFY",
      path: "auth/routes.py",
      reason: "Add expiration check to password reset endpoint",
      content: "from auth.service import AuthService\nfrom auth.tokens import TokenManager\n\ndef reset_password(token, new_password):\n    tm = TokenManager()\n    if tm.is_token_expired(token):\n        raise ValueError('Token expired')\n    return True",
    },
    {
      id: "CHANGE-003",
      operation: "CREATE",
      path: "tests/test_token_expiry.py",
      reason: "Add unit tests for token expiration",
      content: "import pytest\nfrom auth.tokens import TokenManager\n\ndef test_token_expiry():\n    tm = TokenManager()\n    assert tm.is_token_expired('test_token') == True",
    },
  ]);

  const handleGenerate = () => {
    setGenerating(true);
    setTimeout(() => {
      setGenerating(false);
      setGenerated(true);
    }, 2000);
  };

  const handleApply = () => {
    setApplying(true);
    setTimeout(() => {
      setApplying(false);
      setApplied(true);
    }, 1500);
  };

  const getOperationBadge = (op: string) => {
    switch (op) {
      case "CREATE":
        return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400";
      case "MODIFY":
        return "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400";
      case "DELETE":
        return "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400";
      default:
        return "bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-400";
    }
  };

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Code Generation</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Generate, review, and apply structured code patches from implementation plans.
        </p>
      </div>

      {/* Plan Selection & Generate */}
      <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-900 dark:text-white">Active Plan</h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Add password reset token expiration</p>
          </div>
          <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
            Plan Ready
          </span>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleGenerate}
            disabled={generating}
            className="px-5 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 flex items-center gap-2"
          >
            {generating ? (
              <>
                <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Generating...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
                </svg>
                Generate Patches
              </>
            )}
          </button>
          <button className="px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
            Select Different Plan
          </button>
        </div>
      </div>

      {/* Generated Patches */}
      {generated && (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-300">
          {/* Patch Summary */}
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white">Generated PatchSet</h2>
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-500 dark:text-slate-400">patch-abc123</span>
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                  {applied ? "Applied" : "Proposed"}
                </span>
              </div>
            </div>

            {/* View toggle */}
            <div className="flex items-center gap-1 mb-4 p-0.5 rounded-lg bg-slate-100 dark:bg-slate-700 w-fit">
              <button
                onClick={() => setViewMode("preview")}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  viewMode === "preview"
                    ? "bg-white dark:bg-slate-600 text-slate-900 dark:text-white shadow-sm"
                    : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-white"
                }`}
              >
                Preview
              </button>
              <button
                onClick={() => setViewMode("diff")}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  viewMode === "diff"
                    ? "bg-white dark:bg-slate-600 text-slate-900 dark:text-white shadow-sm"
                    : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-white"
                }`}
              >
                Diff
              </button>
            </div>

            {/* Changes */}
            <div className="space-y-3">
              {changes.map((change) => (
                <div key={change.id} className="rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
                  {/* Change header */}
                  <div className="flex items-center justify-between px-4 py-2.5 bg-slate-50 dark:bg-slate-700/50 border-b border-slate-200 dark:border-slate-700">
                    <div className="flex items-center gap-3">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded ${getOperationBadge(change.operation)}`}>
                        {change.operation}
                      </span>
                      <span className="text-sm font-mono text-slate-700 dark:text-slate-300">{change.path}</span>
                    </div>
                    <span className="text-xs text-slate-500 dark:text-slate-400">{change.id}</span>
                  </div>
                  {/* Change reason */}
                  <div className="px-4 py-2 border-b border-slate-100 dark:border-slate-700/50">
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      <span className="font-medium text-slate-700 dark:text-slate-300">Reason:</span> {change.reason}
                    </p>
                  </div>
                  {/* Code content */}
                  <div className="relative">
                    <pre className="p-4 text-xs font-mono text-slate-800 dark:text-slate-200 bg-slate-50 dark:bg-slate-900 overflow-x-auto leading-relaxed">
                      {viewMode === "diff" ? (
                        <code>
                          <span className="text-emerald-600 dark:text-emerald-400">+ </span>
                          {change.content.split("\n").join("\n<span class='text-emerald-600 dark:text-emerald-400'>+ </span>")}
                        </code>
                      ) : (
                        <code>{change.content}</code>
                      )}
                    </pre>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Validation Status */}
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center">
                  <svg className="w-4 h-4 text-emerald-600 dark:text-emerald-400" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                </div>
                <div>
                  <p className="text-sm font-medium text-slate-900 dark:text-white">Patch Validation Passed</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">All changes verified — no conflicts, valid hashes, safe paths</p>
                </div>
              </div>
              <span className="text-xs text-slate-400 dark:text-slate-500">SecurePatch v1.0</span>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleApply}
              disabled={applying || applied}
              className="px-5 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 flex items-center gap-2"
            >
              {applying ? (
                <>
                  <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Applying...
                </>
              ) : applied ? (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                  Applied Successfully
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                  Apply to Workspace
                </>
              )}
            </button>
            <button className="px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
              Dry Run
            </button>
            <button className="px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
              Export PatchSet
            </button>
          </div>
        </div>
      )}

      {/* Empty State */}
      {!generated && !generating && (
        <div className="text-center py-16">
          <svg className="w-16 h-16 mx-auto text-slate-300 dark:text-slate-600 mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M17.25 6.75L22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3l-4.5 16.5" />
          </svg>
          <h3 className="text-lg font-medium text-slate-900 dark:text-white mb-1">No Patches Generated</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Select a plan above and generate code patches to see them here.
          </p>
        </div>
      )}
    </div>
  );
}
