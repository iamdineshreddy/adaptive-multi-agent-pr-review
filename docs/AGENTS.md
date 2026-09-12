# Agent Architecture

Agents detect issues; they do not decide what gets shown. Detection and decision stay
separate at every level (see `docs/ARUM.md`).

---

## 1. Agent types

| Agent | Focus | Typical categories emitted |
| --- | --- | --- |
| `security` | OWASP flaws, authn/z, injection, secrets, unsafe data handling, unsafe deps, security config | `security/xss`, `security/sql-injection`, `security/secrets`, `security/authorization`, `security/dependency`, `security/crypto` |
| `quality` | maintainability, readability, smells, duplication, error handling, complexity | `quality/maintainability`, `quality/duplication`, `quality/error-handling`, `quality/complexity` |
| `performance` | loops, expensive ops, db queries, memory, API call nesting, algorithms | `performance/query`, `performance/loop`, `performance/memory`, `performance/api` |
| `architecture` | layering, SoC, coupling, dependencies, scalability, design consistency | `architecture/layering`, `architecture/coupling`, `architecture/dependency`, `architecture/scalability` |
| `standards` | repo-specific conventions, naming, formatting, organisation rules | `standards/naming`, `standards/formatting`, `standards/convention` |

The Supervisor (`supervisor`) does not emit findings; it plans, coordinates, merges,
resolves conflicts, and assembles decision-layer input.

---

## 2. State / transition model (LangGraph)

The graph holds: `review context`, `task plan`, `per-agent states`, `consolidation`,
`decision`, `publication`, and `iteration` state.

Nodes and edges:

```text
SUPERVISOR_PLAN ──► AGENT_FAN_OUT ──► [SECURITY | QUALITY | PERFORMANCE
                                          | ARCHITECTURE | STANDARDS]  (parallel)
                                          │
                            AGENT_ERROR / AGENT_TIMEOUT ──► RETRY (bounded) ──► AGENT_FAN_OUT
                                          │
                                          └── ALL_DONE ──► CONSOLIDATE ──► REDUNDANCY
                                                                └──► DECIDE (ARUM) ──► BUDGET_GATES
                                                                         └──► PUBLISH ──► DONE
                                                                              └──► ITERATE ──► TARGETED_FAN_OUT
```

Failure handling:

- Agent retry (max 2): on LLM timeout, invalid output, provider 5xx.
- If an agent permanently fails: its queue status → `failed`, its (partial) valid
  findings still flow to consolidation, and a supervisor note is appended. The review
  is not silently completed.
- Any task > timeout → `FAILED` path; the state machine enters a diagnosed terminal
  state `FAILED` with `root_cause`.

`LangGraph` purpose is explicit graph semantics (states, transitions, retries,
iteration). It is not invoked "for its own sake"; the same graph is unit-tested for
the sequences in `docs/EXPERIMENTS.md` (queue failures, worker crashes, LLM timeouts).

---

## 3. Agent input

Each agent receives (via `review_tasks.scope`):

- repository id + language + repo memory summary (standards, accepted patterns)
- PR summary (title, description, base/head, changed files)
- **diff slices** relevant to the agent (trimmed, with line numbers mapped back to both
  diff and absolute positions)
- RAG context: top-k similar historical findings + repo standards (only when useful)

Context windows are budgeted per agent (tokens tracked). Large PRs are chunked by
file; chunks run as separate scoped tasks and results are merged.

---

## 4. Structured output contract (Pydantic)

Every agent must return exactly this shape (or a per-category refinement of it). LLM
output is parsed and validated; invalid items are dropped and counted.

```python
class AgentFinding(BaseModel):
    file_path: str
    line_start: int | None
    line_end: int | None
    category: str            # e.g. "security/sql-injection"
    severity: Severity       # info|low|medium|high|critical
    confidence: float        # 0..1
    title: str
    description: str
    evidence: dict           # {"snippet": "...", "diff_hunk": "...", "rule": "..."}
    suggested_fix: str
    reason_summary: str      # concise; NO chain-of-thought
    related_categories: list[str] = []
```

Validation rules: no chain-of-thought fields; durations/invalid ranges dropped; file
must exist in the changed set; severity/confidence coerced or invalidated. Malformed
batches: one re-request; then fail with `failed_results` count (metric).

---

## 5. Prompt hygiene & injection defence

Repo code, diffs, and PR text are **untrusted input**. Defences:

- System prompt is fixed, versioned, and separated from user content.
- User content is placed inside a delimited data block and never concatenated into
  instruction positions.
- Role-audit: models are instructed that PR descriptions/comments are data, not
  instructions; any apparent instruction is ignored.
- Instruction markers inside diffs are neutralised/stripped when extracted.
- Output is schema-constrained; structural violations are detectable without trusting
  the model.
- A prompt-injection evaluation harness is part of the test suite (see
  `docs/EXPERIMENTS.md`).

---

## 6. Disagreement & supervisor arbitration

- Redundancy groups with high cross-agent severity/confidence variance are flagged.
- Supervisor resolves: optionally one evidence-restricted verification pass, or
  `review is escalated` with priority bump.
- `AgentAgreement(f)` is reduced for these findings in ARUM (see `docs/ARUM.md` §8).
- All arbitration steps are recorded in `agent_metrics`/`findings` for evaluation.

---

## 7. LLM abstraction

All LLM access goes through `LLMProvider` (OpenAI, Anthropic, pluggable local model):

- model + provider from env/secrets (never hardcoded)
- JSON-schema / tool-call-forced structured output where available
- retries + fallback provider
- token/cost accounting per call (Langfuse trace)
- no secrets logged

Provider behaviour is integration-tested against fakes; real keys only at runtime.