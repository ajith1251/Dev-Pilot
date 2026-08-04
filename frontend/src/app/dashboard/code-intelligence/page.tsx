"use client";

import { useState, useEffect } from "react";

interface IndexStatus {
  indexed: boolean;
  index_id?: string;
  repository_path?: string;
  node_count?: number;
  edge_count?: number;
  file_count?: number;
  kinds?: Record<string, number>;
  relationships?: Record<string, number>;
}

interface SymbolResult {
  id: string;
  name: string;
  qualified_name: string;
  kind: string;
  file_path: string;
  language: string;
  start_line?: number;
  end_line?: number;
  signature?: string;
}

export default function CodeIntelligencePage() {
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [repoPath, setRepoPath] = useState("");
  const [indexing, setIndexing] = useState(false);
  const [indexMessage, setIndexMessage] = useState("");
  const [activeTab, setActiveTab] = useState<"status" | "symbols" | "query">("status");
  const [symbols, setSymbols] = useState<SymbolResult[]>([]);
  const [symbolKind, setSymbolKind] = useState("");
  const [symbolName, setSymbolName] = useState("");
  const [symbolLimit, setSymbolLimit] = useState(30);
  const [symbolLoading, setSymbolLoading] = useState(false);
  const [querySymbol, setQuerySymbol] = useState("");
  const [queryResult, setQueryResult] = useState("");
  const [queryLoading, setQueryLoading] = useState(false);

  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "";

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${apiBase}/api/v1/code-intelligence-v2/status`);
      const json = await res.json();
      if (json.success) setStatus(json.data);
    } catch {
      // Backend may not be running
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
  }, []);

  const buildIndex = async () => {
    if (!repoPath.trim()) return;
    setIndexing(true);
    setIndexMessage("Indexing repository...");
    try {
      const res = await fetch(
        `${apiBase}/api/v1/code-intelligence-v2/index?path=${encodeURIComponent(repoPath.trim())}`,
        { method: "POST" }
      );
      const json = await res.json();
      if (json.success) {
        setIndexMessage(
          `✅ Indexed ${json.data.files_parsed} files, ${json.data.symbols_extracted} symbols, ${json.data.edges_created} edges`
        );
        fetchStatus();
      } else {
        setIndexMessage(`❌ ${json.message}`);
      }
    } catch (err: any) {
      setIndexMessage(`❌ Error: ${err.message}`);
    } finally {
      setIndexing(false);
    }
  };

  const fetchSymbols = async () => {
    setSymbolLoading(true);
    try {
      const params = new URLSearchParams();
      if (symbolKind) params.set("kind", symbolKind);
      if (symbolName) params.set("name", symbolName);
      params.set("limit", String(symbolLimit));
      const res = await fetch(
        `${apiBase}/api/v1/code-intelligence-v2/symbols?${params}`
      );
      const json = await res.json();
      if (json.success) {
        setSymbols(json.data.symbols || []);
      }
    } catch {
      // ignore
    } finally {
      setSymbolLoading(false);
    }
  };

  const runQuery = async () => {
    if (!querySymbol.trim()) return;
    setQueryLoading(true);
    setQueryResult("Querying graph...");
    try {
      const res = await fetch(
        `${apiBase}/api/v1/code-intelligence-v2/retrieve?` +
        `symbol_names=${encodeURIComponent(querySymbol.trim())}&expand_depth=2&max_expanded=30`,
        { method: "POST" }
      );
      const json = await res.json();
      if (json.success) {
        const data = json.data;
        let text = `## Graph Context for "${querySymbol}"\n\n`;
        text += `**Direct matches:** ${data.direct_matches?.length || 0}\n`;
        text += `**Related symbols:** ${data.graph_context?.length || 0}\n\n`;
        if (data.direct_matches?.length > 0) {
          text += "### Direct Matches\n";
          data.direct_matches.slice(0, 10).forEach((m: any) => {
            text += `- **${m.name}** (${m.kind}) — ${m.file_path}\n`;
            if (m.signature) text += `  \`${m.signature}\`\n`;
          });
        }
        if (data.graph_context?.length > 0) {
          text += "\n### Related Symbols\n";
          data.graph_context.slice(0, 15).forEach((m: any) => {
            text += `- [${m.kind}] **${m.name}** — ${m.file_path} (dist=${m.distance})\n`;
          });
        }
        if (data.truncated) text += "\n*(Results truncated)*\n";
        setQueryResult(text);
      } else {
        setQueryResult(`❌ ${json.message}`);
      }
    } catch (err: any) {
      setQueryResult(`❌ Error: ${err.message}`);
    } finally {
      setQueryLoading(false);
    }
  };

  const kindCounts = status?.kinds
    ? Object.entries(status.kinds)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 8)
    : [];

  const relCounts = status?.relationships
    ? Object.entries(status.relationships)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 8)
    : [];

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
          Code Intelligence <span className="text-xs font-normal text-primary-500 ml-2">Phase 12</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Semantic repository graph with structural code understanding, impact analysis, and graph-aware retrieval.
        </p>
      </div>

      {/* Index Builder */}
      <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Index Repository</h2>
        <div className="flex gap-3">
          <input
            type="text"
            value={repoPath}
            onChange={(e) => setRepoPath(e.target.value)}
            placeholder="/path/to/repository"
            className="flex-1 px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-sm text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary-500"
          />
          <button
            onClick={buildIndex}
            disabled={indexing || !repoPath.trim()}
            className="px-4 py-2 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {indexing ? "Indexing..." : "Build Index"}
          </button>
        </div>
        {indexMessage && (
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">{indexMessage}</p>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 rounded-lg bg-slate-100 dark:bg-slate-700 p-1 w-fit">
        {(["status", "symbols", "query"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
              activeTab === tab
                ? "bg-white dark:bg-slate-600 text-slate-900 dark:text-white shadow-sm"
                : "text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
            }`}
          >
            {tab === "status" ? "Index Status" : tab === "symbols" ? "Symbol Browser" : "Graph Query"}
          </button>
        ))}
      </div>

      {/* Tab: Status */}
      {activeTab === "status" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <h2 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Graph Statistics</h2>
            {loading ? (
              <p className="text-sm text-slate-400">Loading...</p>
            ) : !status?.indexed ? (
              <p className="text-sm text-slate-400">No index loaded. Build an index above.</p>
            ) : (
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-slate-500">Index ID</span>
                  <span className="text-slate-900 dark:text-white font-mono text-xs">{status.index_id}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-500">Repository</span>
                  <span className="text-slate-900 dark:text-white text-xs truncate max-w-[200px]">{status.repository_path}</span>
                </div>
                <div className="border-t border-slate-200 dark:border-slate-700 my-2" />
                <StatRow label="Nodes (symbols)" value={status.node_count ?? 0} color="bg-blue-500" />
                <StatRow label="Edges (relationships)" value={status.edge_count ?? 0} color="bg-emerald-500" />
                <StatRow label="Files" value={status.file_count ?? 0} color="bg-violet-500" />
              </div>
            )}
          </div>

          <div className="space-y-6">
            {/* Symbol Kinds */}
            <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Symbol Kinds</h2>
              {kindCounts.length === 0 ? (
                <p className="text-sm text-slate-400">No data</p>
              ) : (
                <div className="space-y-2">
                  {kindCounts.map(([kind, count]) => (
                    <div key={kind} className="flex items-center justify-between text-sm">
                      <span className="text-slate-600 dark:text-slate-400">{kind}</span>
                      <span className="text-slate-900 dark:text-white font-mono">{count}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Relationship Types */}
            <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Relationship Types</h2>
              {relCounts.length === 0 ? (
                <p className="text-sm text-slate-400">No data</p>
              ) : (
                <div className="space-y-2">
                  {relCounts.map(([rel, count]) => (
                    <div key={rel} className="flex items-center justify-between text-sm">
                      <span className="text-slate-600 dark:text-slate-400">{rel}</span>
                      <span className="text-slate-900 dark:text-white font-mono">{count}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Tab: Symbols */}
      {activeTab === "symbols" && (
        <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Symbol Browser</h2>
          <div className="flex flex-wrap gap-3 mb-4">
            <input
              type="text"
              value={symbolName}
              onChange={(e) => setSymbolName(e.target.value)}
              placeholder="Filter by name"
              className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-sm w-48"
            />
            <input
              type="text"
              value={symbolKind}
              onChange={(e) => setSymbolKind(e.target.value)}
              placeholder="Filter by kind"
              className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-sm w-32"
            />
            <input
              type="number"
              value={symbolLimit}
              onChange={(e) => setSymbolLimit(Number(e.target.value))}
              min={1}
              max={500}
              className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-sm w-20"
            />
            <button
              onClick={fetchSymbols}
              disabled={symbolLoading}
              className="px-4 py-1.5 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-700 disabled:opacity-50 transition-colors"
            >
              {symbolLoading ? "Loading..." : "Search"}
            </button>
          </div>

          {symbols.length === 0 ? (
            <p className="text-sm text-slate-400">No symbols found. Build an index first and click Search.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 dark:border-slate-700">
                    <th className="text-left py-2 text-slate-500 font-medium">Name</th>
                    <th className="text-left py-2 text-slate-500 font-medium">Kind</th>
                    <th className="text-left py-2 text-slate-500 font-medium">File</th>
                    <th className="text-left py-2 text-slate-500 font-medium">Lines</th>
                  </tr>
                </thead>
                <tbody>
                  {symbols.map((sym) => (
                    <tr key={sym.id} className="border-b border-slate-100 dark:border-slate-700/50 hover:bg-slate-50 dark:hover:bg-slate-700/30 transition-colors">
                      <td className="py-2 text-slate-900 dark:text-white font-mono text-xs">{sym.name}</td>
                      <td className="py-2">
                        <span className="px-2 py-0.5 rounded text-xs font-medium bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                          {sym.kind}
                        </span>
                      </td>
                      <td className="py-2 text-slate-600 dark:text-slate-400 text-xs">{sym.file_path}</td>
                      <td className="py-2 text-slate-500 text-xs">
                        {sym.start_line && sym.end_line ? `${sym.start_line}-${sym.end_line}` : "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Tab: Query */}
      {activeTab === "query" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <h2 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Graph Query</h2>
            <div className="flex gap-3">
              <input
                type="text"
                value={querySymbol}
                onChange={(e) => setQuerySymbol(e.target.value)}
                placeholder="Symbol name (e.g., AuthService)"
                className="flex-1 px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-sm"
              />
              <button
                onClick={runQuery}
                disabled={queryLoading || !querySymbol.trim()}
                className="px-4 py-2 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-700 disabled:opacity-50 transition-colors"
              >
                {queryLoading ? "Querying..." : "Query"}
              </button>
            </div>
          </div>
          <div className="rounded-xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 p-5">
            <h2 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">Results</h2>
            <pre className="text-xs text-slate-600 dark:text-slate-400 whitespace-pre-wrap font-mono leading-relaxed max-h-96 overflow-y-auto">
              {queryResult || "Run a query to see results."}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

function StatRow({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <div className="flex items-center gap-2">
        <div className={`w-2 h-2 rounded-full ${color}`} />
        <span className="text-slate-500">{label}</span>
      </div>
      <span className="text-slate-900 dark:text-white font-semibold font-mono">{value.toLocaleString()}</span>
    </div>
  );
}
