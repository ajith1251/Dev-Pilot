"use client";

/**
 * Phase 20A6 — Repository Selector.
 *
 * Searchable, filterable multi-select over the organization graph's
 * registered repository namespaces (`GET /api/v1/graph/org/repositories`).
 *
 * Performance: paginated fetch + incremental "load more" via an
 * IntersectionObserver sentinel — a large org never loads every repository
 * at once. Search/filter re-queries the backend (pagination resets).
 *
 * Isolation: only namespaces the backend exposes are shown; the payload is
 * bounded + evidence-only (no credentials or hidden metadata).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { orgApi } from "@/lib/api/client";
import type { OrgRepository } from "@/lib/api/client";

const PAGE_SIZE = 25;

export interface RepositorySelection {
  repository_id: string;
  name: string;
  organization_id: string;
  source_type: string;
  path: string;
}

export default function RepositorySelector({
  open,
  onClose,
  selected,
  onToggle,
  onConfirm,
}: {
  open: boolean;
  onClose: () => void;
  selected: Set<string>;
  onToggle: (repo: RepositorySelection) => void;
  onConfirm: (selectedRepos: RepositorySelection[]) => void;
}) {
  const [repos, setRepos] = useState<OrgRepository[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [orgFilter, setOrgFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const offsetRef = useRef(0);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const [orgs, setOrgs] = useState<string[]>([]);

  const fetchPage = useCallback(
    async (offset: number, reset: boolean, q: string, org: string) => {
      setLoading(true);
      setError(null);
      try {
        const result = await orgApi.repositories({
          q: q || undefined,
          organization_id: org || undefined,
          limit: PAGE_SIZE,
          offset,
        });
        const page = result.data?.repositories || [];
        offsetRef.current = offset + page.length;
        setTotal(result.data?.total ?? 0);
        setRepos((prev) => (reset ? page : [...prev, ...page]));
        // Collect distinct organizations for the filter dropdown.
        setOrgs((prev) => {
          const next = new Set(prev);
          for (const r of page) {
            if (r.organization_id) next.add(r.organization_id);
          }
          return Array.from(next).sort();
        });
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load repositories");
      } finally {
        setLoading(false);
      }
    },
    []
  );

  // Reset + load first page when opened or filters change.
  useEffect(() => {
    if (!open) return;
    setRepos([]);
    offsetRef.current = 0;
    fetchPage(0, true, query, orgFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, query, orgFilter]);

  // "Load more" sentinel — IntersectionObserver avoids loading everything.
  useEffect(() => {
    if (!open) return;
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (
          entries[0]?.isIntersecting &&
          !loading &&
          repos.length < total
        ) {
          fetchPage(offsetRef.current, false, query, orgFilter);
        }
      },
      { rootMargin: "200px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [open, loading, repos.length, total, query, orgFilter, fetchPage]);

  if (!open) return null;

  const selectedRepos = repos.filter((r) => selected.has(r.repository_id));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 shadow-2xl w-full max-w-2xl mx-4 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200 dark:border-slate-700">
          <div>
            <h2 className="text-base font-semibold text-slate-900 dark:text-white">
              Select Repositories
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Organization graph namespaces — {total} registered
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            aria-label="Close repository selector"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Search + filter */}
        <div className="px-5 py-3 border-b border-slate-200 dark:border-slate-700 space-y-2">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <svg className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z" />
              </svg>
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search by name, id or path..."
                className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-all"
              />
            </div>
            <select
              value={orgFilter}
              onChange={(e) => setOrgFilter(e.target.value)}
              className="px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-all"
            >
              <option value="">All orgs</option>
              {orgs.map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Repository list */}
        <div className="flex-1 overflow-y-auto px-3 py-2">
          {error && (
            <p className="text-xs text-red-600 dark:text-red-400 px-2 py-3">{error}</p>
          )}
          {!loading && repos.length === 0 && !error && (
            <p className="text-xs text-slate-500 dark:text-slate-400 px-2 py-6 text-center">
              No repositories match — register namespaces via the org graph API first.
            </p>
          )}
          <div className="space-y-1">
            {repos.map((repo) => {
              const isSelected = selected.has(repo.repository_id);
              return (
                <button
                  key={repo.repository_id}
                  onClick={() =>
                    onToggle({
                      repository_id: repo.repository_id,
                      name: repo.name,
                      organization_id: repo.organization_id,
                      source_type: repo.source_type,
                      path: repo.path,
                    })
                  }
                  className={`w-full flex items-start gap-3 px-3 py-2.5 rounded-lg border text-left transition-all duration-150 ${
                    isSelected
                      ? "bg-primary-50 dark:bg-primary-900/20 border-primary-300 dark:border-primary-700"
                      : "border-transparent hover:bg-slate-50 dark:hover:bg-slate-700/40"
                  }`}
                >
                  <span
                    className={`mt-0.5 w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-all ${
                      isSelected
                        ? "bg-primary-600 border-primary-600 text-white"
                        : "border-slate-300 dark:border-slate-600"
                    }`}
                  >
                    {isSelected && (
                      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={3} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                      </svg>
                    )}
                  </span>
                  <span className="flex-1 min-w-0">
                    <span className="flex items-center gap-2">
                      <span className="font-mono text-xs font-semibold text-slate-900 dark:text-white truncate">
                        {repo.repository_id}
                      </span>
                      <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400">
                        {repo.source_type}
                      </span>
                    </span>
                    <span className="block text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 truncate">
                      {repo.name}{repo.name !== repo.repository_id ? ` · ${repo.repository_id}` : ""}
                    </span>
                    <span className="block text-[10px] text-slate-400 dark:text-slate-500 mt-0.5 truncate">
                      {repo.organization_id}
                      {repo.path ? ` · ${repo.path}` : ""}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
          {/* Load-more sentinel */}
          <div ref={sentinelRef} className="py-3 text-center">
            {loading && (
              <svg className="animate-spin w-5 h-5 mx-auto text-primary-500" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            {!loading && repos.length < total && (
              <span className="text-[11px] text-slate-400">
                Scroll for more ({repos.length}/{total})
              </span>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-slate-200 dark:border-slate-700 flex items-center justify-between">
          <span className="text-xs text-slate-500 dark:text-slate-400">
            {selected.size} selected
            {selectedRepos.length > 0 && (
              <span className="ml-1 text-slate-400">· {selectedRepos.length} on this page</span>
            )}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-lg text-xs font-medium text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => {
                const chosen = repos.filter((r) => selected.has(r.repository_id));
                onConfirm(
                  chosen.map((r) => ({
                    repository_id: r.repository_id,
                    name: r.name,
                    organization_id: r.organization_id,
                    source_type: r.source_type,
                    path: r.path,
                  }))
                );
                onClose();
              }}
              disabled={selected.size === 0}
              className="px-4 py-2 rounded-lg text-xs font-medium bg-primary-600 text-white hover:bg-primary-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            >
              Add {selected.size > 0 ? selected.size : ""} repo{selected.size === 1 ? "" : "s"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
