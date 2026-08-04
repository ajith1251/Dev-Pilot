"use client";

import { useState } from "react";

interface PlanStep {
  id: string;
  title: string;
  description: string;
  status: "pending" | "in_progress" | "complete";
}

export default function PlanningPage() {
  const [task, setTask] = useState("");
  const [description, setDescription] = useState("");
  const [generating, setGenerating] = useState(false);
  const [generated, setGenerated] = useState(false);

  const [steps] = useState<PlanStep[]>([
    { id: "STEP-001", title: "Add token expiration field to auth service", description: "Extend the authentication service to include token expiration validation.", status: "pending" },
    { id: "STEP-002", title: "Update password reset route with expiration check", description: "Modify the password reset API endpoint to check token expiration.", status: "pending" },
    { id: "STEP-003", title: "Add tests for token expiration", description: "Write tests verifying expired tokens are rejected.", status: "pending" },
  ]);

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!task.trim()) return;
    setGenerating(true);

    // Simulate plan generation
    setTimeout(() => {
      setGenerating(false);
      setGenerated(true);
    }, 2000);
  };

  const getStepIcon = (status: string, index: number) => {
    switch (status) {
      case "complete":
        return (
          <div className="w-8 h-8 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center">
            <svg className="w-4 h-4 text-emerald-600 dark:text-emerald-400" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
            </svg>
          </div>
        );
      case "in_progress":
        return (
          <div className="w-8 h-8 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center">
            <svg className="animate-spin w-4 h-4 text-blue-600 dark:text-blue-400" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          </div>
        );
      default:
        return (
          <div className="w-8 h-8 rounded-full bg-slate-100 dark:bg-slate-700 flex items-center justify-center">
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">
              {index + 1}
            </span>
          </div>
        );
    }
  };

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Implementation Planning</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Describe a task and generate a structured implementation plan with ordered steps.
        </p>
      </div>

      {/* Task Input */}
      <form onSubmit={handleGenerate} className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5 space-y-4">
        <div>
          <label htmlFor="task-title" className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
            Task Title
          </label>
          <input
            id="task-title"
            type="text"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            placeholder="e.g. Add password reset token expiration"
            className="w-full px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all"
          />
        </div>
        <div>
          <label htmlFor="task-desc" className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
            Description (optional)
          </label>
          <textarea
            id="task-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="Describe the task in more detail..."
            className="w-full px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all resize-none"
          />
        </div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
            </svg>
            Plans use LLM-based analysis to generate structured requirements and ordered implementation steps.
          </div>
          <button
            type="submit"
            disabled={generating || !task.trim()}
            className="px-5 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 flex items-center gap-2"
          >
            {generating ? (
              <>
                <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Generating Plan...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m6-6H6" />
                </svg>
                Generate Plan
              </>
            )}
          </button>
        </div>
      </form>

      {/* Generated Plan */}
      {generated && (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-300">
          {/* Plan Summary */}
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <div className="flex items-start justify-between mb-4">
              <div>
                <h2 className="text-lg font-semibold text-slate-900 dark:text-white">{task}</h2>
                <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Implementation Plan</p>
              </div>
              <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
                Validated
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 p-4 rounded-lg bg-slate-50 dark:bg-slate-700/50">
              {[
                { label: "Steps", value: "3" },
                { label: "Requirements", value: "5" },
                { label: "Risk Level", value: "Low" },
                { label: "Test Strategy", value: "Unit + Integration" },
              ].map((item) => (
                <div key={item.label} className="text-center">
                  <p className="text-lg font-semibold text-slate-900 dark:text-white">{item.value}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">{item.label}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Steps */}
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <h3 className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-4">Implementation Steps</h3>
            <div className="space-y-0">
              {steps.map((step, i) => (
                <div key={step.id} className="flex gap-4">
                  {/* Timeline */}
                  <div className="flex flex-col items-center">
                    {getStepIcon(step.status, i)}
                    {i < steps.length - 1 && <div className="w-px flex-1 bg-slate-200 dark:bg-slate-700 my-1" />}
                  </div>
                  {/* Step content */}
                  <div className={`flex-1 pb-6 ${i < steps.length - 1 ? '' : ''}`}>
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="text-sm font-medium text-slate-900 dark:text-white">{step.id}: {step.title}</p>
                        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{step.description}</p>
                      </div>
                      <span className={`text-xs font-medium px-2 py-0.5 rounded-full ml-4 ${
                        step.status === "complete"
                          ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                          : step.status === "in_progress"
                          ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
                          : "bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400"
                      }`}>
                        {step.status === "complete" ? "Complete" : step.status === "in_progress" ? "In Progress" : "Pending"}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3">
            <button className="px-4 py-2 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 transition-colors">
              Use Plan for Coding
            </button>
            <button className="px-4 py-2 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
              Export JSON
            </button>
            <button className="px-4 py-2 rounded-lg border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300 text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors">
              Regenerate
            </button>
          </div>
        </div>
      )}

      {/* Empty State */}
      {!generated && !generating && (
        <div className="text-center py-16">
          <svg className="w-16 h-16 mx-auto text-slate-300 dark:text-slate-600 mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <h3 className="text-lg font-medium text-slate-900 dark:text-white mb-1">No Plan Generated Yet</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Describe a task above and DevPilot will generate a structured implementation plan.
          </p>
        </div>
      )}
    </div>
  );
}
