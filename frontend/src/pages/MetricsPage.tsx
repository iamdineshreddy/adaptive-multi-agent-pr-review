import { fetchMetrics } from "../services/api";
import type { MetricsRollup } from "../types";
import { useAsyncData } from "../hooks/useAsyncData";

function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-bold">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-slate-400">{sub}</p>}
    </div>
  );
}

function statusLabel(status: string): string {
  return status.replace(/_/g, " ").toLowerCase();
}

export default function MetricsPage() {
  const { data, error, reload } = useAsyncData<MetricsRollup>(() => fetchMetrics());

  if (error) return <p className="text-red-700">Error: {error}</p>;
  if (!data) return <p className="text-slate-500">Loading...</p>;

  const reviewRows = Object.entries(data.reviews_by_status);
  const findingRows = Object.entries(data.findings_by_status);
  const feedbackRows = Object.entries(data.feedback_by_outcome);

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Metrics</h2>
        <button
          onClick={() => reload()}
          className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm ring-1 ring-slate-200 hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        <StatCard label="Total Findings" value={data.findings_total} />
        <StatCard
          label="Redundancy Rate"
          value={`${(data.redundancy_rate * 100).toFixed(1)}%`}
        />
        <StatCard label="Total Feedback" value={data.feedback_total} />
        <StatCard
          label="Total Cost"
          value={`$${data.agent_metrics.cost_usd.toFixed(4)}`}
        />
        <StatCard
          label="Tokens In / Out"
          value={`${data.agent_metrics.tokens_in.toLocaleString()} / ${data.agent_metrics.tokens_out.toLocaleString()}`}
        />
        <StatCard
          label="Avg Latency"
          value={`${data.agent_metrics.latency_avg_ms.toFixed(1)} ms`}
        />
        <StatCard
          label="Max Latency"
          value={`${data.agent_metrics.latency_max_ms} ms`}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-100 px-4 py-3 text-sm font-semibold">
            Reviews by status
          </div>
          {reviewRows.length === 0 ? (
            <p className="px-4 py-3 text-sm text-slate-500">No reviews.</p>
          ) : (
            <ul className="divide-y divide-slate-100 text-sm">
              {reviewRows.map(([status, count]) => (
                <li key={status} className="flex justify-between px-4 py-2">
                  <span className="text-slate-600">{statusLabel(status)}</span>
                  <span className="font-medium">{count}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-100 px-4 py-3 text-sm font-semibold">
            Findings by publication status
          </div>
          {findingRows.length === 0 ? (
            <p className="px-4 py-3 text-sm text-slate-500">No findings.</p>
          ) : (
            <ul className="divide-y divide-slate-100 text-sm">
              {findingRows.map(([status, count]) => (
                <li key={status} className="flex justify-between px-4 py-2">
                  <span className="text-slate-600">{statusLabel(status)}</span>
                  <span className="font-medium">{count}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-100 px-4 py-3 text-sm font-semibold">
            Feedback by outcome
          </div>
          {feedbackRows.length === 0 ? (
            <p className="px-4 py-3 text-sm text-slate-500">No feedback.</p>
          ) : (
            <ul className="divide-y divide-slate-100 text-sm">
              {feedbackRows.map(([outcome, count]) => (
                <li key={outcome} className="flex justify-between px-4 py-2">
                  <span className="text-slate-600">{statusLabel(outcome)}</span>
                  <span className="font-medium">{count}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}