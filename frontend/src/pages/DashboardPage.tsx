import { useCallback } from "react";
import { fetchDashboardSummary, fetchGitHubRepos } from "../services/api";
import { useAsyncData } from "../hooks/useAsyncData";
import type { DashboardSummary, GitHubRepo } from "../types";

export default function DashboardPage() {
  const loadSummary = useCallback(() => fetchDashboardSummary(), []);
  const loadRepos = useCallback(() => fetchGitHubRepos().catch(() => [] as GitHubRepo[]), []);
  const { data: summary, error: summaryError } = useAsyncData<DashboardSummary>(loadSummary);
  const { data: repos } = useAsyncData<GitHubRepo[]>(loadRepos);

  if (summaryError) return <p className="text-red-700">Error: {summaryError}</p>;
  if (!summary) return <p className="text-slate-500">Loading...</p>;

  const totalStars = repos?.reduce((s, r) => s + r.stars, 0) ?? 0;
  const totalForks = repos?.reduce((s, r) => s + r.forks, 0) ?? 0;
  const languages = repos ? Array.from(new Set(repos.map((r) => r.language).filter(Boolean))) : [];

  return (
    <section className="space-y-6">
      <h2 className="text-xl font-semibold">Dashboard</h2>
      <div className="grid gap-4 md:grid-cols-4">
        <Card label="Total reviews" value={summary.total} />
        {Object.entries(summary.by_status).map(([status, count]) => (
          <Card key={status} label={status} value={count} />
        ))}
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold uppercase text-slate-500">
          GitHub activity (live)
        </h3>
        {!repos ? (
          <p className="text-sm text-slate-500">Loading GitHub data...</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-4">
            <Card label="Repositories" value={repos.length} />
            <Card label="Total stars" value={totalStars} />
            <Card label="Total forks" value={totalForks} />
            <Card label="Languages" value={languages.length} />
          </div>
        )}
        {repos && repos.length > 0 && (
          <p className="mt-3 text-xs text-slate-500">
            Languages: {languages.join(", ")}
          </p>
        )}
      </div>
    </section>
  );
}

function Card({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-semibold uppercase text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-800">{value}</p>
    </div>
  );
}
