export type ReviewStatus =
  | "RECEIVED"
  | "QUEUED"
  | "PROCESSING"
  | "AGENTS_RUNNING"
  | "CONSOLIDATING"
  | "DECIDING"
  | "PUBLISHED"
  | "WAITING_FOR_FEEDBACK"
  | "ITERATING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type ReviewMode =
  | "OPENED"
  | "SYNCHRONIZE"
  | "READY_FOR_REVIEW"
  | "REOPENED"
  | "MANUAL_RERUN";

export type FindingStatus =
  | "CANDIDATE"
  | "SCHEDULED"
  | "PUBLISHED"
  | "SUPPRESSED"
  | "RESOLVED"
  | "STALE";

export type Severity = "INFO" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface PullRequestSummary {
  number: number;
  title: string;
  author_login: string;
  base_ref: string;
  head_ref: string;
  head_sha: string;
  last_reviewed_sha: string | null;
  state: string;
  repository: string;
  changed_files: number;
  additions: number;
  deletions: number;
}

export interface ReviewSummary {
  id: string;
  status: ReviewStatus;
  mode: ReviewMode;
  priority_score: number | null;
  risk_class: string | null;
  budget_cap: number | null;
  arum_version: string | null;
  supervisor_notes: string[];
  failure_reason: string | null;
  root_cause: string | null;
  created_at: string | null;
  updated_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  pull_request: PullRequestSummary;
}

export interface Finding {
  id: string;
  file_path: string;
  line_start: number | null;
  line_end: number | null;
  category: string;
  severity: Severity;
  confidence: number | null;
  title: string;
  description: string;
  suggested_fix: string | null;
  publication_status: FindingStatus;
  arum_utility: number | null;
  arum_version: string | null;
  feedback_labelled: boolean;
  created_at: string | null;
}

export interface ReviewIteration {
  iteration: number;
  base_sha: string;
  head_sha: string;
  diff_stats: Record<string, unknown>;
  agents_invoked: Record<string, unknown>;
  published_count: number;
  resolved_count: number;
  stale_count: number;
  created_at: string | null;
}

export interface ReviewDetail extends ReviewSummary {
  findings: Finding[];
  iterations: ReviewIteration[];
}

export interface DashboardSummary {
  total: number;
  by_status: Record<string, number>;
}

export interface RepositorySummary {
  id: string;
  full_name: string;
  default_branch: string | null;
  main_language: string | null;
  is_active: boolean;
}

export interface RepositoryMemory {
  repository_id: string;
  version: number;
  snapshot: Record<string, unknown>;
  decay_params: Record<string, unknown>;
  learned_weights: Record<string, unknown> | null;
  updated_at: string | null;
}

export interface FeedbackEntry {
  id: string;
  finding_id: string | null;
  review_id: string;
  outcome: string;
  source: string;
  author_login: string | null;
  commit_sha: string | null;
  created_at: string | null;
}

export interface AgentMetrics {
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  latency_avg_ms: number;
  latency_max_ms: number;
}

export interface MetricsRollup {
  reviews_by_status: Record<string, number>;
  findings_total: number;
  findings_by_status: Record<string, number>;
  redundancy_rate: number;
  feedback_total: number;
  feedback_by_outcome: Record<string, number>;
  agent_metrics: AgentMetrics;
}

export interface ReviewsPage {
  items: ReviewSummary[];
  nextLimit: number;
  nextOffset: number;
}

export const STATUS_SEVERITY: Record<Severity, string> = {
  INFO: "bg-slate-100 text-slate-700",
  LOW: "bg-emerald-100 text-emerald-700",
  MEDIUM: "bg-amber-100 text-amber-800",
  HIGH: "bg-orange-100 text-orange-800",
  CRITICAL: "bg-red-100 text-red-700",
};

export const STATUS_BADGE: Record<ReviewStatus, string> = {
  RECEIVED: "bg-slate-100 text-slate-700",
  QUEUED: "bg-slate-100 text-slate-700",
  PROCESSING: "bg-blue-100 text-blue-700",
  AGENTS_RUNNING: "bg-blue-100 text-blue-700",
  CONSOLIDATING: "bg-indigo-100 text-indigo-700",
  DECIDING: "bg-indigo-100 text-indigo-700",
  PUBLISHED: "bg-emerald-100 text-emerald-700",
  WAITING_FOR_FEEDBACK: "bg-violet-100 text-violet-700",
  ITERATING: "bg-sky-100 text-sky-700",
  COMPLETED: "bg-emerald-100 text-emerald-700",
  FAILED: "bg-red-100 text-red-700",
  CANCELLED: "bg-slate-100 text-slate-700",
};