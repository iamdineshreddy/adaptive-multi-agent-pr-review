import { useCallback } from "react";
import { fetchGitHubRepos } from "../services/api";
import { useAsyncData } from "../hooks/useAsyncData";
import type { GitHubRepo } from "../types";

export default function GitHubReposPage() {
  const load = useCallback(() => fetchGitHubRepos(), []);
  const { data: repos, error } = useAsyncData<GitHubRepo[]>(load);

  if (error) return <p className="text-red-700">Error: {error}</p>;
  if (!repos) return <p className="text-slate-500">Loading GitHub repositories...</p>;

  return (
    <section>
      <h2 className="mb-4 text-xl font-semibold">My GitHub Repositories</h2>
      <p className="mb-4 text-sm text-slate-500">
        Live data from GitHub ({repos.length} repositories).
      </p>
      {repos.length === 0 ? (
        <p className="text-slate-500">No repositories found.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-4 py-2 text-left font-semibold text-slate-600">Repository</th>
                <th className="px-4 py-2 text-left font-semibold text-slate-600">Language</th>
                <th className="px-4 py-2 text-right font-semibold text-slate-600">Stars</th>
                <th className="px-4 py-2 text-right font-semibold text-slate-600">Forks</th>
                <th className="px-4 py-2 text-right font-semibold text-slate-600">Open Issues</th>
                <th className="px-4 py-2 text-left font-semibold text-slate-600">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {repos.map((repo) => (
                <tr key={repo.id}>
                  <td className="px-4 py-2">
                    <a
                      href={repo.html_url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-blue-700 hover:underline"
                    >
                      {repo.full_name}
                    </a>
                    {repo.private && (
                      <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
                        private
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-slate-600">{repo.language ?? "-"}</td>
                  <td className="px-4 py-2 text-right text-slate-600">{repo.stars}</td>
                  <td className="px-4 py-2 text-right text-slate-600">{repo.forks}</td>
                  <td className="px-4 py-2 text-right text-slate-600">{repo.open_issues}</td>
                  <td className="px-4 py-2 text-slate-500">
                    {repo.updated_at ? new Date(repo.updated_at).toLocaleDateString() : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
