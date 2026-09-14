import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchReviews } from "../services/api";
import { STATUS_BADGE, type ReviewSummary } from "../types";

function statusBadge(status: ReviewSummary["status"]): string {
  return STATUS_BADGE[status] ?? "bg-slate-100 text-slate-700";
}

export default function ReviewsPage() {
  const [reviews, setReviews] = useState<ReviewSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchReviews()
      .then((page) => !cancelled && setReviews(page.items))
      .catch((e: unknown) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <p className="text-red-700">Error: {error}</p>;
  if (!reviews) return <p className="text-slate-500">Loading...</p>;
  if (reviews.length === 0) {
    return <p className="text-slate-500">No reviews recorded.</p>;
  }

  return (
    <section>
      <h2 className="mb-4 text-xl font-semibold">Reviews</h2>
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-3">PR</th>
              <th className="px-4 py-3">Repository</th>
              <th className="px-4 py-3">Author</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Mode</th>
              <th className="px-4 py-3">Created</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {reviews.map((review) => (
              <tr key={review.id} className="hover:bg-slate-50">
                <td className="px-4 py-3">
                  <Link
                    to={`/reviews/${review.id}`}
                    className="font-medium text-blue-700 hover:underline"
                  >
                    #{review.pull_request.number}
                  </Link>
                  <p className="max-w-md truncate text-slate-600">
                    {review.pull_request.title}
                  </p>
                </td>
                <td className="px-4 py-3 text-slate-600">
                  {review.pull_request.repository}
                </td>
                <td className="px-4 py-3 text-slate-600">
                  {review.pull_request.author_login}
                </td>
                <td className="px-4 py-3">
                  <span
                    className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${statusBadge(review.status)}`}
                  >
                    {review.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-600">{review.mode}</td>
                <td className="px-4 py-3 text-slate-500">
                  {review.created_at ? new Date(review.created_at).toLocaleString() : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}