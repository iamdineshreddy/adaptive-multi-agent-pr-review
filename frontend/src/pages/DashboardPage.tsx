import { useEffect, useState } from "react";
import { fetchDashboardSummary } from "../services/api";
import type { DashboardSummary } from "../types";

function statusLabel(status: string): string {
  return status.replace(/_/g, " ").toLowerCase();
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchDashboardSummary()
      .then((summary) => !cancelled && setData(summary))
      .catch((e: unknown) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <p className="text-red-700">Error: {error}</p>;
  if (!data) return <p className="text-slate-500">Loading...</p>;

  const statuses = Object.entries(data.by_status);

  return (
    <section>
      <h2 className="mb-4 text-xl font-semibold">Queue Status</h2>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <p className="text-sm font-medium text-slate-500">Total Reviews</p>
          <p className="mt-1 text-3xl font-bold">{data.total}</p>
        </div>
        {statuses.map(([status, count]) => (
          <div
            key={status}
            className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm"
          >
            <p className="text-sm font-medium text-slate-500">
              {statusLabel(status)}
            </p>
            <p className="mt-1 text-3xl font-bold">{count}</p>
          </div>
        ))}
      </div>
    </section>
  );
}