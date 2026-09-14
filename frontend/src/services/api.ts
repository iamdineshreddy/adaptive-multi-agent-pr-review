import type {
  DashboardSummary,
  ReviewSummary,
  ReviewsPage,
  ReviewDetail,
  ReviewStatus,
} from "../types";

const BASE = "/api/v1";

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as {
        detail?: { message?: string };
      };
      if (body.detail?.message) message = body.detail.message;
    } catch {
      // non-JSON error body; keep the generic message
    }
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export async function fetchDashboardSummary(): Promise<DashboardSummary> {
  return getJson<DashboardSummary>("/dashboard/summary");
}

export async function fetchReviews(
  status?: ReviewStatus,
  limit = 50,
  offset = 0,
): Promise<ReviewsPage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) params.set("status", status);
  const items = await getJson<ReviewSummary[]>(`/reviews?${params.toString()}`);
  return { items, nextLimit: limit, nextOffset: offset + items.length };
}

export async function fetchReview(id: string): Promise<ReviewDetail> {
  return getJson<ReviewDetail>(`/reviews/${id}`);
}

export { ApiError };