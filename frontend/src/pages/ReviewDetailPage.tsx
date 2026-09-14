import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { fetchReview } from "../services/api";
import {
  STATUS_BADGE,
  STATUS_SEVERITY,
  type Finding,
  type ReviewDetail,
  type ReviewStatus,
} from "../types";

function statusBadge(status: ReviewStatus): string {
  return STATUS_BADGE[status] ?? "bg-slate-100 text-slate-700";
}

function severityBadge(severity: Finding["severity"]): string {
  return (
    STATUS_SEVERITY[severity] ?? "bg-slate-100 text-slate-700"
  );
}

function formatConfidence(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(0)}%`;
}

export default function ReviewDetailPage() {
  const { reviewId } = useParams<{ reviewId: string }>();
  const [review, setReview] = useState<ReviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!reviewId) return;
    let cancelled = false;
    fetchReview(reviewId)
      .then((detail) => !cancelled && setReview(detail))
      .catch((e: unknown) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [reviewId]);

  if (error) return <p className="text-red-700">Error: {error}</p>;
  if (!review) return <p className="text-slate-500">Loading...</p>;

  return (
    <section className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-xl font-semibold">
            #{review.pull_request.number} · {review.pull_request.title}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {review.pull_request.repository} · {review.pull_request.author_login}
            {" · "}head {review.pull_request.head_sha.slice(0, 7)}
          </p>
        </div>
        <span
          className={`inline-block rounded-full px-3 py-1 text-xs font-medium ${statusBadge(review.status)}`}
        >
          {review.status}
        </span>
      </div>

      <section>
        <h3 className="mb-2 text-lg font-semibold">Findings</h3>
        {review.findings.length === 0 ? (
          <p className="text-slate-500">No findings.</p>
        ) : (
          <ul className="space-y-3">
            {review.findings.map((finding) => (
              <li
                key={finding.id}
                className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
              >
                <div className="flex items-center gap-2">
                  <span className="font-medium">{finding.title}</span>
                  <span
                    className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${severityBadge(finding.severity)}`}
                  >
                    {finding.severity}
                  </span>
                  <span className="text-xs text-slate-500">
                    {finding.publication_status}
                  </span>
                  {finding.arum_utility !== null && (
                    <span className="text-xs text-slate-500">
                      ARUM {finding.arum_utility.toFixed(3)}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-slate-500">
                  {finding.category} · {finding.file_path}
                  {finding.line_start !== null && `:${finding.line_start}`} ·{" "}
                  confidence {formatConfidence(finding.confidence)}
                </p>
                {finding.description && (
                  <p className="mt-2 text-sm text-slate-600">
                    {finding.description}
                  </p>
                )}
                {finding.suggested_fix && (
                  <p className="mt-2 text-xs text-emerald-700">
                    Suggested fix: {finding.suggested_fix}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="mb-2 text-lg font-semibold">Iterations</h3>
        {review.iterations.length === 0 ? (
          <p className="text-slate-500">No iteration history.</p>
        ) : (
          <ul className="space-y-2">
            {review.iterations.map((iteration) => (
              <li
                key={iteration.iteration}
                className="rounded-lg border border-slate-200 bg-white p-4 text-sm shadow-sm"
              >
                <div className="font-medium">
                  Round {iteration.iteration} · {iteration.base_sha.slice(0, 7)}
                  {" → "}
                  {iteration.head_sha.slice(0, 7)}
                </div>
                <p className="mt-1 text-xs text-slate-500">
                  {iteration.published_count} published ·{" "}
                  {iteration.resolved_count} resolved · {iteration.stale_count}{" "}
                  stale · invoked{" "}
                  {(iteration.agents_invoked?.targeted as string[] | undefined)
                    ?.join(", ") ?? "unknown"}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}