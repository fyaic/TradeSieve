# Agile operating model

**Status:** Active delivery policy for Phase 1.

## Delivery method

TradeSieve uses a lightweight Scrum/Kanban hybrid:

- two-week, outcome-based sprints for the Phase 1 vertical slice;
- a single ordered backlog in GitHub Issues;
- explicit research/security/compliance spikes with time boxes and decision outputs;
- pull-request flow with small increments and continuous integration;
- incomplete work does not receive partial credit or silently roll into “done.”

The objective is working, reviewable evidence at the end of every sprint—not completion percentages.

## Work hierarchy

| Level | GitHub representation | Purpose |
| --- | --- | --- |
| Phase | Milestone `Phase 1 — Service MVP` | Time-boxed user outcome and release gate |
| Epic | Issue labelled `type/epic` | End-to-end capability group with measurable exit criteria |
| Story | Issue labelled `type/story` | User/operational value demonstrable inside one sprint |
| Spike | Issue labelled `type/spike` | Time-boxed uncertainty reduction ending in evidence/decision |
| Task/defect | Issue or checklist item | Small implementation or correction tied to a story |

Compliance risk priorities `P0/P1/P2` describe case risk and must not be reused as backlog priority. Delivery priority labels are `priority/now`, `priority/next`, and `priority/later`.

## Workflow states

1. **Backlog** — valuable but not refined.
2. **Ready** — meets Definition of Ready and can enter a sprint.
3. **In progress** — owner actively working; WIP limit applies.
4. **Review** — PR/evidence available and checks running.
5. **Blocked** — named dependency/decision with owner and next review date.
6. **Done** — merged, verified, documented, and accepted against the story.

Without an organization GitHub Project board, labels plus milestone and linked PRs are the source of truth. A Project board may be added when token/access and team workflow justify it.

## Cadence

| Event | Cadence | Output |
| --- | --- | --- |
| Sprint planning | Every two weeks | Sprint goal, selected ready stories, capacity/risk assumptions |
| Daily coordination | Working days, async-first | Progress, next action, blockers; no status theatre |
| Backlog refinement | Weekly | Ready stories, acceptance tests, dependencies, owner questions |
| Architecture/compliance review | At least weekly and on relevant PRs | ADR/source/rule/security decisions and recorded approvals |
| Sprint review | End of sprint | Working increment demonstrated from a clean environment |
| Retrospective | End of sprint | One or two concrete process experiments for next sprint |
| Source/risk review | On source/rule changes | Freshness, change impact, rescreen and approval evidence |

## Definition of Ready

A story may enter a sprint when:

- it states the user/operational outcome and why it matters;
- acceptance criteria are observable and include failure/safety behavior;
- requirement IDs and parent epic are linked;
- dependencies, data/source/licence, security/privacy, and human-approval needs are identified;
- synthetic fixtures or test strategy exist;
- it is small enough to finish and demonstrate in one sprint;
- unresolved questions are bounded enough that the team can still commit.

Spikes instead require a question, time box, evidence method, decision owner, and exit artifact.

## Definition of Done

Applicable items must all be true:

- acceptance criteria and negative/safety cases pass;
- code is reviewed and merged through a PR with linked issue;
- formatter/lint/type/unit/contract/integration/security checks pass;
- interface/schema changes update OpenAPI, examples, CLI/MCP parity tests, and migration notes;
- source/rule/matcher changes record version, provenance/licence, fixtures, evaluation, approval, rescreen impact, and rollback;
- authorization, tenant boundary, idempotency, audit, observability/redaction, and error behavior are tested;
- user/operator/integration documentation is updated;
- no secrets or production data are committed;
- the increment works from a clean checkout/reference deployment;
- known limitations and deferred work are explicit.

“Code complete,” “works on my machine,” and “no name match found” are not definitions of done.

## Pull-request policy

- Branches: `agent/<description>`, `feature/<issue>-<description>`, `docs/<issue>-<description>`, or `fix/<issue>-<description>`.
- One coherent change and linked issue per PR where practical.
- Draft PR early for risky architecture/contracts; ready only when acceptance evidence is present.
- Material decisions require an ADR; ADRs are superseded rather than rewritten after acceptance.
- Source/rule/human-decision controls require named domain review.
- Squash merge and branch deletion are the repository defaults.
- Main-branch protection is a desired control but cannot be enforced on the current GitHub Free private-repository plan; maintainers must follow PR-only policy procedurally until the plan changes.

## WIP and quality policy

- Default WIP: one active story per contributor plus one shared urgent defect.
- Do not start a new feature while a reviewable story is blocked only on finishing tests/docs.
- A P0 security/compliance-control defect interrupts the sprint; product priority changes require explicit scope trade-off.
- Flaky tests are defects. A check is not repeatedly rerun to obtain green without root-cause action.
- Technical debt is logged with user/risk impact and scheduled; it is not hidden inside “cleanup.”

## Sprint evidence

Each sprint review records:

- sprint goal met/not met and a working demo link/script;
- stories done versus carried, with cause;
- automated test/contract/security results;
- source/matcher benchmark or user-review metrics where applicable;
- open decisions, risk, and changed assumptions;
- release/readiness impact and next sprint goal.

## Delivery metrics

Use metrics for learning, not individual performance:

- cycle time and blocked time by story type;
- escaped defects and failed-change recovery;
- PR review/check duration;
- percentage of stories meeting DoR before sprint;
- API/CLI/MCP parity failures;
- golden-path success from clean checkout;
- case/matcher/reviewer/source metrics defined in the product baseline.
