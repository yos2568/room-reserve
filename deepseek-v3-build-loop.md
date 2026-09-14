# DeepSeek execution prompt — Room Reserve V3

Use this with DeepSeek inside a coding agent that can read/write the repository, run terminal commands, start Docker/PostgreSQL, and operate a real browser. A chat-only session cannot execute this verification loop. Attach or make `roomreserveapp.v3.md` available in the same workspace. The prompt does not itself schedule runs, bypass API limits or certify software.

## Paste this complete prompt into the coding agent

```text
Build the Room Reserve App described in roomreserveapp.v3.md into a complete,
usable application. Implement the app; do not stop at a plan, mockup or scaffold.
V3 is the sole product specification. V1.2 and v2.0 are historical references and
must not override V3. Follow applicable repository/environment instructions and
preserve unrelated user work. Do not invent previous approvals or test results.

MISSION
Deliver an evidenced LOCAL_PASS as defined by V3, then complete deployment checks
only where authorized credentials and infrastructure are available. Keep working
through implement → test → inspect → repair → retest until the required local
checks pass. Do not stop after the first working page or after one happy-path test.
Do not claim zero bugs, flawless software or production readiness without evidence.

START
1. Inspect the repository, applicable instructions, installed tools, working tree,
   existing app and V3 specification. Reuse valid existing work; do not overwrite
   or reset unrelated changes. Record environment versions and missing prerequisites.
2. Create/update BUILD_STATUS.md with the phase, current revision/state, decisions,
   remaining acceptance IDs, latest real commands/results, blockers and next action.
3. Map every A01–A30 requirement into docs/acceptance-matrix.md with actual test
   locations or an explicit manual/deployment check. Do not treat a checkbox as a test.
4. Use V3's concrete defaults. Resolve routine implementation choices autonomously
   and record them in docs/decisions.md. Ask only when a real contradiction or
   missing external dependency prevents progress; continue independent work.
5. Build locally using synthetic students/staff and a mail catcher. No real student
   roster is needed for local completion. Do not send real invitations/emails,
   buy services or deploy publicly unless separately authorized.

IMPLEMENTATION CONTRACT
- Use the V3 Django/PostgreSQL/server-rendered stack, not an in-memory demo.
- Implement every in-scope student/staff route and failure state, not placeholder
  buttons, TODO handlers, success-only mocks or static dashboard screenshots.
- All operational mutations use V3's common lock and services. Reconcile persisted
  rows before writes; a normal rejected request must still commit reconciliation.
- Separate future reservation, owned-reservation check-in and current-slot use-now.
  Keep the exact original slot end for walk-ins. Do not reinstate the 24-hour rule.
- Enforce quota/status/adjacency rules, consumed rolling strikes, account eligibility,
  closures, permissions, idempotency and transactional outbox exactly as specified.
- Deliver a documented clean local bootstrap, migrations, demo access instructions,
  UI, translations, posters, tests, CI, production configuration and recovery runbook.
- Do not hard-code actual secrets, weaken authentication for demos, expose debug
  clock/role-switch endpoints in production, or use real personal data as fixtures.

REPAIR LOOP — EXECUTE, DO NOT MERELY DESCRIBE
For each meaningful implementation slice:
A. Run the relevant checks with actual exit codes. Inspect failures and logs.
B. Reproduce each defect with a focused test or minimal reproduction. Identify the
   root cause and fix the application/configuration. Add a meaningful regression
   test for discovered defects. Do not change expected behavior to bless a bug.
C. Rerun the affected checks and integrations. At phase/integration gates run the
   broader suite. Continue to the next phase without asking permission for routine
   edits, local tests or reversible fixes already within scope.
D. Exercise the actual running app through a browser for completed user flows.
   Inspect visible state, server/browser errors and persisted results. Repair
   incorrect behavior, inaccessible controls, untranslated messages and unusable
   mobile layouts. A screenshot alone does not establish that a flow works.
E. Update evidence and BUILD_STATUS.md after each phase or substantial repair.

Build scripts/verify (or one documented equivalent) that runs all automated local
checks, fails nonzero on failures and records per-check status. A skipped or missing
required check must appear as BLOCKED/NOT RUN and prevent a complete LOCAL_PASS;
do not swallow exit codes with '|| true', empty test selection or blanket exception
handlers. Manual browser/recovery checks remain separately evidenced if they cannot
be automated, and are still mandatory for the relevant completion label.

Minimum final verification includes:
- Dependency installation and pinned versions; formatter/linter; Django checks;
  migration drift check (makemigrations --check --dry-run or equivalent).
- Fresh migrations and bootstrap using an isolated new PostgreSQL database/Compose
  project. Never delete an existing user's database or volume to prove a clean boot.
- All policy, permissions, lifecycle, outbox and transaction tests using PostgreSQL.
- Independent-connection concurrency tests with synchronized starts, including
  20 users/one slot, one user/multiple slots, check-in/no-show and closure races.
  Repeat concurrency stress at least 20 rounds with fresh fixtures.
- Real browser student and staff flows in Thai and English, desktop and 360px mobile,
  keyboard access, basic no-JavaScript forms, negative states and stale refresh.
- Docker image/build and restart persistence; production-style Django deployment
  checks with appropriate local test settings; dependency/security review.
- Backup and restore into an isolated fresh database, followed by functional checks;
  migration/image rollback rehearsal with documented schema compatibility.
- Generated QR targets and poster legibility; local mail-catcher delivery and retry.
- Performance measurements with hardware recorded; deployment targets are only
  called verified if tested on that environment.

ADVERSARIAL REVIEW BEFORE COMPLETION
Inspect the final implementation against V3 A01–A30, specifically trying to break:
exact :00/:15/:60 boundaries; late/short walk-ins; completed/no-show quota; cross-room
same-hour and adjacency; stale unique rows; rejected-request reconciliation rollback;
scheduler outage and third strike; repeated/expired/voided sanctions; simultaneous
POSTs; closure/deactivation conflicts; invitation replay/account collisions; staff
privilege escalation; privacy leaks; outbox retries and restore of stale messages.
For any defect found, reproduce → fix → add regression → rerun relevant tests.
Do not skip/remove tests, loosen thresholds, disable constraints or redefine scope
to get green output. If a test is wrong, explain why from the V3 requirement and
correct it while preserving or strengthening behavioral coverage.

FINAL PASS AND STOP CONDITIONS
After the last code change:
1. Run the full verification suite and all required local manual checks.
2. Verify a fresh documented bootstrap in an isolated environment and smoke-test
   the core student/staff flows on the same final code. If anything changes, rerun
   affected checks and full final verification before declaring completion.
3. No unresolved core functionality, critical/high correctness/security issues,
   required skipped checks, fake persistence or unhandled workflow errors may remain.
4. Write QA_REPORT.md with requirement IDs, exact commands, timestamps, exit codes,
   pass/fail/blocked results, environment, evidence paths, known limitations and
   code revision (or file hashes if no Git). Do not include secrets in evidence.
5. Mark LOCAL_PASS only when its complete gate is evidenced. If real SMTP/domain/
   NAS/pilot inputs are absent, label deployment/pilot BLOCKED or NOT RUN, preserve
   the local pass, and specify the minimum missing input. Never claim a live pilot
   or physical QR inspection from simulated/browser tests.
6. Provide the usable local URL (only if running and checked), start/stop commands,
   safe local demo-account instructions, delivered features and QA evidence paths.
   If the environment cannot keep the app running, say so and give tested restart steps.
7. When the full suite and fresh-start check pass on unchanged code, stop redundant
   looping. Report LOCAL_PASS, DEPLOYMENT_VERIFIED and PILOT_ACCEPTED separately.

BLOCKERS AND CONTINUATION
Do not blindly retry forever. After three unsuccessful attempts at the same symptom,
change diagnostic strategy: inspect logs/environment, isolate a reproduction and
record hypotheses tried. Fix anything still within your tools and authorization.
Continue all independent work. If access, missing software, network, credentials or
an explicit owner decision is the remaining blocker, document the exact condition,
commands/errors and smallest required action; do not manufacture a passing result.
Tool absence does not convert a required test into optional work.
Before context/API/runtime limits end a run, save a precise checkpoint to
BUILD_STATUS.md and QA_REPORT.md, including failed/pending commands and next action.
Resume from it rather than restarting or assuming earlier checks are still current.
A completion response must distinguish implemented, actually tested and still blocked.
Start now by inspecting the repository and V3, then implement the app.
```

## Resume prompt after interruption or API/context limit

```text
Continue the Room Reserve V3 build. Read roomreserveapp.v3.md,
deepseek-v3-build-loop.md, BUILD_STATUS.md, QA_REPORT.md and the current repository
state. Verify the checkpoint against files and available tools. Resume the next
unfinished acceptance item and continue the specified implement/test/repair loop.
Do not restart completed work or claim old test results apply after code changes.
Work toward evidenced LOCAL_PASS, then authorized deployment checks. Preserve honest
BLOCKED/NOT RUN statuses for missing external prerequisites and save a checkpoint
before any further interruption.
```

## Expected final report

The agent must provide a concise report with: local/deployment/pilot status separately; tested URL or startup command; local demo access method; implemented scope; real test/concurrency/browser/restore results; QA report paths; remaining external gates. An agent saying “all done” without that evidence has not completed this prompt.
