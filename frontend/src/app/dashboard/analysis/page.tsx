"use client";

import { useState } from "react";

interface AnalysisResult {
  name: string;
  languages: { name: string; percentage: number }[];
  technologies: { name: string; confidence: string }[];
  modules: { name: string; type: string }[];
  totalFiles: number;
}

export default function AnalysisPage() {
  const [repoPath, setRepoPath] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<AnalysisResult | null>(null);

  const handleAnalyze = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!repoPath.trim()) return;

    setAnalyzing(true);
    setResult(null);

    // Simulate analysis - in production this would call the API
    setTimeout(() => {
      setResult({
        name: repoPath.split("/").pop() || repoPath,
        languages: [
          { name: "Python", percentage: 62 },
          { name: "TypeScript", percentage: 22 },
          { name: "JavaScript", percentage: 8 },
          { name: "CSS", percentage: 4 },
          { name: "Shell", percentage: 2 },
          { name: "Other", percentage: 2 },
        ],
        technologies: [
          { name: "FastAPI", confidence: "HIGH" },
          { name: "Next.js", confidence: "HIGH" },
          { name: "Tailwind CSS", confidence: "HIGH" },
          { name: "pytest", confidence: "HIGH" },
          { name: "Docker", confidence: "MEDIUM" },
        ],
        modules: [
          { name: "backend", type: "python-app" },
          { name: "frontend", type: "nextjs-app" },
        ],
        totalFiles: 186,
      });
      setAnalyzing(false);
    }, 1500);
  };

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Repository Analysis</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Analyze repository structure, languages, technologies, and dependencies.
        </p>
      </div>

      {/* Input Form */}
      <form onSubmit={handleAnalyze} className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <label htmlFor="repo-path" className="sr-only">Repository path</label>
            <input
              id="repo-path"
              type="text"
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              placeholder="Enter repository path (e.g. /path/to/repo or GitHub URL)"
              className="w-full px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent transition-all"
            />
          </div>
          <button
            type="submit"
            disabled={analyzing || !repoPath.trim()}
            className="px-5 py-2.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150 flex items-center gap-2"
          >
            {analyzing ? (
              <>
                <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Analyzing...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
                </svg>
                Analyze
              </>
            )}
          </button>
        </div>
      </form>

      {/* Results */}
      {result && (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-300">
          {/* Summary Card */}
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
                Results for <span className="text-primary-600 dark:text-primary-400">{result.name}</span>
              </h2>
              <span className="text-xs text-slate-500 dark:text-slate-400">{result.totalFiles} files scanned</span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Languages */}
              <div>
                <h3 className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-3">Languages</h3>
                <div className="space-y-2">
                  {result.languages.map((lang) => (
                    <div key={lang.name}>
                      <div className="flex items-center justify-between text-sm mb-1">
                        <span className="text-slate-700 dark:text-slate-300">{lang.name}</span>
                        <span className="text-slate-500 dark:text-slate-400">{lang.percentage}%</span>
                      </div>
                      <div className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-primary-500 rounded-full transition-all duration-700 ease-out"
                          style={{ width: `${lang.percentage}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Technologies */}
              <div>
                <h3 className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-3">Technologies</h3>
                <div className="space-y-2">
                  {result.technologies.map((tech) => (
                    <div
                      key={tech.name}
                      className="flex items-center justify-between px-3 py-2 rounded-lg bg-slate-50 dark:bg-slate-700/50"
                    >
                      <span className="text-sm text-slate-700 dark:text-slate-300">{tech.name}</span>
                      <span className={`text-xs font-medium px-2 py-0.5 rounded ${
                        tech.confidence === "HIGH"
                          ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                          : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                      }`}>
                        {tech.confidence}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Modules */}
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <h3 className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-3">Detected Modules</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {result.modules.map((mod) => (
                <div
                  key={mod.name}
                  className="flex items-center gap-3 px-4 py-3 rounded-lg border border-slate-200 dark:border-slate-700"
                >
                  <div className="w-8 h-8 rounded-lg bg-primary-100 dark:bg-primary-900/30 flex items-center justify-center">
                    <svg className="w-4 h-4 text-primary-600 dark:text-primary-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 9.776c.112-.017.227-.026.344-.026h15.812c.117 0 .232.009.344.026m-16.5 0a2.25 2.25 0 00-1.883 2.542l.857 6a2.25 2.25 0 002.227 1.932H19.05a2.25 2.25 0 002.227-1.932l.857-6a2.25 2.25 0 00-1.883-2.542m-16.5 0V6A2.25 2.25 0 016 3.75h3.879a1.5 1.5 0 011.06.44l2.122 2.12a1.5 1.5 0 001.06.44H18A2.25 2.25 0 0120.25 9v.776" />
                    </svg>
                  </div>
                  <div>
                    <p className="text-sm font-medium text-slate-900 dark:text-white">{mod.name}</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">{mod.type}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Empty State */}
      {!result && !analyzing && (
        <div className="text-center py-16">
          <svg className="w-16 h-16 mx-auto text-slate-300 dark:text-slate-600 mb-4" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 0v3.75m-16.5-3.75v3.75m16.5 0v3.75C20.25 16.153 16.556 18 12 18s-8.25-1.847-8.25-4.125v-3.75m16.5 0c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125" />
          </svg>
          <h3 className="text-lg font-medium text-slate-900 dark:text-white mb-1">Ready to Analyze</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Enter a repository path or GitHub URL above to start analysis.
          </p>
        </div>
      )}

      {analyzing && !result && (
        <div className="text-center py-16">
          <svg className="animate-spin w-10 h-10 mx-auto text-primary-500 mb-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <h3 className="text-lg font-medium text-slate-900 dark:text-white mb-1">Analyzing Repository</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Scanning files, detecting languages and technologies...
          </p>
        </div>
      )}
    </div>
  );
}
