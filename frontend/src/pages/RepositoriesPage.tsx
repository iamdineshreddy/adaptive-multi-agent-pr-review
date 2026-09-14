import { useCallback } from "react";
import {
  fetchRepositories,
  fetchRepositoryMemory,
  fetchRepositoryFeedback,
} from "../services/api";
import { useAsyncData } from "../hooks/useAsyncData";
import type { FeedbackEntry, RepositoryMemory, RepositorySummary } from "../types";

export default function RepositoriesPage() {
  const load = useCallback(() => fetchRepositories(), []);
  const { data: repos, error } = useAsyncData<RepositorySummary[]>(load);

  if (error) return <p className="text-red-700">Error: {error}</p>;
  if (!repos) return <p className="text-slate-500">Loading...</p>;

  return (
    <section>
      <h2 className="mb-4 text-xl font-semibold">Repositories</h2>
      {repos.length === 0 ? (
        <p className="text-slate-500">No repositories recorded.</p>
      ) : (
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white shadow-sm">
          {repos.map((repo) => (
            <li key={repo.id} className="px-4 py-3">
              <details>
                <summary className="cursor-pointer font-medium text-slate-800">
                  {repo.full_name}
                  <span className="ml-3 text-xs font-normal text-slate-500">
                    {repo.main_language ?? "unknown"} · {repo.default_branch ?? "—"}
                  </span>
                </summary>
                <div className="mt-3 grid gap-4 md:grid-cols-2">
                  <MemoryPanel repositoryId={repo.id} />
                  <FeedbackPanel repositoryId={repo.id} />
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function MemoryPanel({ repositoryId }: { repositoryId: string }) {
  const load = useCallback(() => fetchRepositoryMemory(repositoryId), [repositoryId]);
  const { data, error } = useAsyncData<RepositoryMemory>(load);
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
      <p className="text-xs font-semibold uppercase text-slate-500">
        Memory (version {data?.version ?? "?"})
      </p>
      {error ? (
        <p className="mt-1 text-xs text-red-700">{error}</p>
      ) : data === null ? (
        <p className="mt-1 text-xs text-slate-500">Loading...</p>
      ) : (
        <pre className="mt-2 max-h-64 overflow-auto text-xs text-slate-700">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}

function FeedbackPanel({ repositoryId }: { repositoryId: string }) {
  const load = useCallback(() => fetchRepositoryFeedback(repositoryId), [repositoryId]);
  const { data, error } = useAsyncData<FeedbackEntry[]>(load);
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
      <p className="text-xs font-semibold uppercase text-slate-500">Feedback</p>
      {error ? (
        <p className="mt-1 text-xs text-red-700">{error}</p>
      ) : data === null ? (
        <p className="mt-1 text-xs text-slate-500">Loading...</p>
      ) : data.length === 0 ? (
        <p className="mt-1 text-xs text-slate-500">No feedback events.</p>
      ) : (
        <ul className="mt-2 divide-y divide-slate-200 text-xs">
          {data.map((entry) => (
            <li key={entry.id} className="flex items-center gap-2 py-1.5">
              <span className="font-medium text-slate-700">{entry.outcome}</span>
              <span className="text-slate-500">by {entry.author_login ?? "unknown"}</span>
              <span className="ml-auto text-slate-400">
                {entry.created_at ? new Date(entry.created_at).toLocaleString() : "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}