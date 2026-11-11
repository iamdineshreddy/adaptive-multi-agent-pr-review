import { useCallback } from "react";
import {
  fetchRepositories,
  fetchGitHubRepos,
  fetchReviews,
} from "../services/api";
import { useAsyncData } from "../hooks/useAsyncData";
import type { RepositorySummary, GitHubRepo, ReviewSummary } from "../types";

export default function RepositoriesPage() {
  const load = useCallback(async () => {
    const [ghRepos, dbRepos, reviewsPage] = await Promise.all([
      fetchGitHubRepos().catch(() => [] as GitHubRepo[]),
      fetchRepositories().catch(() => [] as RepositorySummary[]),
      fetchReviews(undefined, 200, 0).catch(() => ({ items: [] as ReviewSummary[], nextLimit: 0, nextOffset: 0 })),
    ]);
    const reviewsByRepo = new Map<string, number>();
    for (const r of reviewsPage.items) {
      const name = r.pull_request.repository;
      reviewsByRepo.set(name, (reviewsByRepo.get(name) ?? 0) + 1);
    }
    const dbByName = new Map(dbRepos.map((r) => [r.full_name, r]));
    return ghRepos.map((g) => ({
      github: g,
      recorded: dbByName.get(g.full_name) ?? null,
      reviewCount: reviewsByRepo.get(g.full_name) ?? 0,
    }));
  }, []);

  const { data, error } = useAsyncData(load);

  if (error) return <p className="text-red-700">Error: {error}</p>;
  if (!data) return <p className="text-slate-500">Loading repositories...</p>;

  return (
    <section>
      <h2 className="mb-4 text-xl font-semibold">Repositories</h2>
      <p className="mb-4 text-sm text-slate-500">
        Live GitHub data ({data.length} repositories) with review activity.
      </p>
      {data.length === 0 ? (
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
                <th className="px-4 py-2 text-right font-semibold text-slate-600">Reviews</th>
                <th className="px-4 py-2 text-left font-semibold text-slate-600">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.map((row) => (
                <tr key={row.github.id}>
                  <td className="px-4 py-2">
                    <a
                      href={row.github.html_url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-medium text-blue-700 hover:underline"
                    >
                      {row.github.full_name}
                    </a>
                    {row.github.private && (
                      <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
                        private
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-slate-600">{row.github.language ?? "-"}</td>
                  <td className="px-4 py-2 text-right text-slate-600">{row.github.stars}</td>
                  <td className="px-4 py-2 text-right text-slate-600">{row.github.forks}</td>
                  <td className="px-4 py-2 text-right text-slate-600">{row.github.open_issues}</td>
                  <td className="px-4 py-2 text-right text-slate-600">{row.reviewCount}</td>
                  <td className="px-4 py-2 text-slate-500">
                    {row.github.updated_at ? new Date(row.github.updated_at).toLocaleDateString() : "-"}
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
