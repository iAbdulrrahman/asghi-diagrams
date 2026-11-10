# Implementation Specification — اصغِ / Crowdsourced Arabic Listening Practice App

Derived **only** from the four artifacts in this folder plus the requirements PDF:

| Artifact | Used for |
|---|---|
| `system_requirements.pdf` | 41 functional requirements, NFRs 11–21 |
| `class-diagram.puml` | 19 classes, 8 enums, 47 operations, 19 associations |
| `h_seq1…h_seq5.puml` | 87 messages across 5 flows |
| `h_uc.puml` | 17 use cases, 24 actor links |

**Rule for this document:** anything written as a plain statement is traceable to an artifact.
Anything the artifacts do not decide is marked **DECISION REQUIRED** with a recommended default.
Do not let the implementing agent invent those silently — confirm them first.

---

## 1. Scope

**In scope (implement fully):** requirements 1, 4, 5, 6.2–6.7, 7.3, 8, 9 — the complete
register → claim → record → review → verified → practise → flag loop.

**Modelled but no flow defined (implement from the model, expect design work):**
2 (catalogue browse), 3 (AI generation), 7.1–7.2 (question authoring), 10.1 (reputation), 10.2 (admin reporting).

**Out of scope for v1:** 6.8 offline download (not modelled anywhere), 21 compensation model (business decision).

---

## 2. Domain model → persistence

One table per class; enums as `VARCHAR` + check constraint (or native enum).

| Table | Key columns | Notes from the model |
|---|---|---|
| `user` | `user_id`, `email?`, `phone_no?`, `display_name`, `password_hash`, `arabic_level` | at least one of email/phone non-null |
| `user_role_profile` | `user_role_profile_id`, `user_id`, `role` | 1 user → 1..* profiles; `role` decides the subclass |
| `learner_profile` | + `current_session_id?` | extends `user_role_profile` |
| `contributor_profile` / `reviewer_profile` / `admin_profile` | + role-specific columns; reviewer has `is_specialist` | |
| `reputation` | `score`, `approval_rate`, `total_submissions` | one per contributor, one per reviewer |
| `learner_progress` | `total_score`, `total_time_spent`, `current_streak_days` | derived from test-session history |
| `passage` | `passage_id`, `text`, `level`, `topic`, `duration`, `origin`, `status`, `author_id?`, `claimed_by?`, `claimed_at?` | `author_id IS NULL ⇔ origin = AI` |
| `audio_recording` | `audio_file`, `duration`, `attribution`, `passage_id`, `status` | `attribution`: SHOW_NAME \| ANONYMOUS |
| `question` | `question_id`, `type`, `prompt_text`, `answers[]`, `correct_answer`, `explanation`, `passage_reference`, `difficulty`, `status`, `passage_id` | |
| `test_session` | `session_id`, `learner_id`, `passage_id`, `started_at`, `finished_at?`, `total_score`, `current_difficulty` | |
| `question_response` | `response_id`, `session_id`, `question_id`, `submitted_answer`, `is_correct` | |
| `review_assignment` | `review_id`, `reviewer_id`, `content_id`, `created_at`, `is_specialist_review`, `is_approved`, `reason` | |
| `review_feedback` | `feedback_id`, `review_id`, `is_fusha`, `dialect_free?`, `audio_quality?`, `feedback` | |
| `content_flag` | `flag_id`, `content_id`, `learner_id`, `reason`, `status` | |

`passage`, `audio_recording` and `question` are all `ReviewableContent` — keep `content_id`,
`status`, `origin`, `author_id` on a shared table or on each (single-table vs joined inheritance).
**DECISION REQUIRED:** inheritance mapping. *Recommended: one `reviewable_content` table + subtype tables.*

Audio files are blobs/files, not bytes in the DB. **DECISION REQUIRED:** object storage.
*Recommended: S3-compatible bucket, DB stores the key.*

---

## 3. State machines

### 3.1 `ContentStatus` (applies to passage, recording, question)

```
DRAFT ──claim()──► CLAIMED ──addAudio()──► PENDING_REVIEW ──meetsThreshold()──► VERIFIED
                      │                          │
                      │ releaseClaim()/expiry     └──reject(reason)──► REJECTED ──claim()──► CLAIMED …
                      ▼
                    DRAFT

VERIFIED ──markFlagged()──► FLAGGED
FLAGGED  ──DECISION REQUIRED──► ?   (no exit path exists in any diagram)
```

Transitions and their triggers, all traceable:

| From | To | Trigger | Source |
|---|---|---|---|
| DRAFT | CLAIMED | `Passage.claim()` success | seq1 msg 9–11 |
| CLAIMED | DRAFT | `releaseClaim()` / TTL expiry | seq1 note |
| CLAIMED | PENDING_REVIEW | `addAudio()` creates recording | seq1 msg 12–13 |
| PENDING_REVIEW | VERIFIED | `meetsThreshold()` then `verify()` | seq2 msg 14, 15 |
| PENDING_REVIEW | REJECTED | `reject(reason)` | seq2 msg 17/21 |
| REJECTED | CLAIMED | re-record (a new recording after re-claim) | seq2 note |
| VERIFIED | FLAGGED | `markFlagged()` | seq5 msg 9 |
| FLAGGED | — | **undefined** | — |

**DECISION REQUIRED:** the FLAGGED exit. *Recommended: `ACTION_TAKEN` → `PENDING_REVIEW`
(content pulled for re-review); `NO_ACTION` → back to `VERIFIED`.*

### 3.2 `FlagStatus` (one per `ContentFlag`)

```
PENDING ──resolveFlag(ACTION_TAKEN)──► ACTION_TAKEN
PENDING ──resolveFlag(NO_ACTION)────► NO_ACTION
```

### 3.3 Claim lifecycle (on `Passage`)

- A passage can be claimed only if: nobody holds it, **or** the holder's claim expired,
  **and** it has no `VERIFIED` and no `PENDING_REVIEW` recording.
- `claimedAt` starts a TTL; expiry is evaluated lazily by `isClaimExpired()` on every claim attempt.
- `PENDING_REVIEW` freezes the clock but keeps the claim; `VERIFIED`/`REJECTED` release it.
- **DECISION REQUIRED:** TTL value. *Recommended: 72 h (the model note says "about 72 hours").*
- **DECISION REQUIRED:** sweeper job interval (lazy expiry alone leaves stale rows visible to admins).

---

## 4. Rules and constants

Traceable today:

| Rule | Value / logic | Source |
|---|---|---|
| Quorum | 1 specialist review **or** 3 general reviews by distinct reviewers | `ReviewableContent` note |
| Self-review | reviewer ≠ content author; assignment refused | seq2 `break` |
| Fusha requirement | reviewer must verify Fusha + dialect-free before approving | `ReviewFeedback`, req 5.5 |
| Claim eligibility | see 3.3 | `Passage` note |
| AI content | always created `PENDING_REVIEW`, `author_id NULL` | `AIEngine` note |
| Progress | derived from `TestSession` history, never edited directly | `LearnerProgress` note |
| Identity | at least one of email/phone | `User` note |
| Difficulty tiers | EASY / MEDIUM / HARD | `Difficulty` enum |
| Levels | A1…C2 | `Level` enum |
| Roles | LEARNER / CONTRIBUTOR / REVIEWER / ADMIN | `UserRole` enum |

Not decided anywhere — **DECISION REQUIRED**, with defaults:

| Constant | Recommended default |
|---|---|
| Score per correct answer | +1, no partial credit (`evaluate()` returns boolean) |
| Test length per session | 5 questions |
| Adaptation step | one tier down after 1 wrong, one tier up after 3 consecutive correct |
| Minimum questions per tier per passage (req 6.3) | 1 easy + 1 medium + 1 hard |
| Audio quality scale (`ReviewFeedback.audioQuality: int`) | 1–5 |
| Reputation formula (req 10.1) | `approvalRate = approvals / totalSubmissions`; `score = Σ(+2 approve, −1 reject)`; recompute on each verdict |
| Playback speeds (`playAudio(playbackSpeed: float)`) | 0.75 / 1.0 / 1.25 / 1.5 |
| Tashkeel toggle state | needs an attribute — `User.showTashkeel: boolean` is **missing from the class diagram** |

---

## 5. API contract

One endpoint per facade operation. Every actor talks only to its own profile — that maps 1:1 to
a controller/service. Auth: **DECISION REQUIRED**, *recommended: JWT bearer, roled by `UserRole`.*

### Auth & account
```
POST   /auth/register            {emailOrPhone, password}          → User.register
POST   /auth/login               {emailOrPhone, password}          → User.login  → token
PATCH  /users/me/profile         {displayName, level, roleInterest}→ User.completeProfile
POST   /users/me/roles           {role}                            → User.addRole
DELETE /users/me/roles/{role}                                      → User.removeRole
PATCH  /users/me/preferences     {showTashkeel}                    → (attribute, see §4)
```

### Learner
```
GET    /passages?level&topic&maxDuration                           → Passage.browse
GET    /passages/{id}                                              → Passage details + questions
POST   /sessions                 {passageId}                       → LearnerProfile.startSession
POST   /sessions/{id}/play       {playbackSpeed}                   → LearnerProfile.playAudio
GET    /sessions/{id}/next-question                                → TestSession.nextQuestion
POST   /sessions/{id}/answers    {questionId, answer}              → LearnerProfile.submitAnswer
POST   /sessions/{id}/finish                                       → LearnerProfile.finishSession
GET    /me/progress                                                → LearnerProgress (derived)
POST   /flags                    {contentId, reason}               → LearnerProfile.flagContent
```

### Contributor
```
GET    /passages/awaiting-recording                                → Passage.awaitingRecording
POST   /passages/{id}/claim                                        → ContributorProfile.claim
DELETE /passages/{id}/claim                                        → Passage.releaseClaim
POST   /passages/{id}/audio      multipart(file)                   → ContributorProfile.recordOrUploadAudio
PATCH  /me/tashkeel                                                → ContributorProfile.toggleTashkeel
```

### Reviewer
```
GET    /reviews/pending                                            → ReviewerProfile.pendingRecordings
POST   /reviews                  {contentId}                       → ReviewerProfile.assignReview
GET    /reviews/{id}/audio                                         → ReviewerProfile.getAudioFile
POST   /reviews/{id}/feedback    {isFusha, dialectFree, audioQuality} → ReviewerProfile.createFeedback
POST   /reviews/{id}/decision    {feedbackId, isApproved, reason}  → ReviewerProfile.submitDecision
```

### Admin
```
POST   /admin/reviewers/{id}/specialist                            → AdminProfile.assignSpecialistStatus
POST   /admin/disputes/{contentId}  {action}                       → AdminProfile.resolveDispute
POST   /admin/flags/{id}            {newState}                     → AdminProfile.resolveFlag
POST   /admin/users/{id}            {action}                       → AdminProfile.manageUser
POST   /admin/ai/{passageId}                                       → AdminProfile.triggerAIGeneration
GET    /admin/reports?period                                        → AdminProfile.generateReport
```

### Authoring (Contributor | Reviewer | Admin)
```
POST   /passages/{id}/questions            {question}              → QuestionAuthor.authorQuestion
POST   /passages/{id}/questions/generate   {count}                 → QuestionAuthor.generateQuestions
```

Response codes for the branches the sequences define:

| Case | Code | Source |
|---|---|---|
| claim refused — held by another, not expired | 409 | seq1 alt 1 |
| claim refused — already verified / pending review | 409 | seq1 alt 2 |
| self-review attempted | 403 | seq2 `break` |
| verdict recorded but quorum not reached | 202 + `pending` | seq2 alt 3 |
| reject with reason | 200, reason returned to contributor | seq2 msg 21 |

---

## 6. Acceptance criteria

One testable line per requirement. `S1–S5` = the five sequences.

| Req | Done when… |
|---|---|
| 1 | four roles exist; each account has exactly one profile per granted role; endpoints are role-gated |
| 1.1 | the four `UserRole` values are enforced server-side |
| 1.2 | register + sign in succeed with email and, separately, with phone |
| 1.3 | a new user must supply display name, A1–C2 level and role interest before using any feature |
| 2 | a passage row carries text, ≥1 audio, questions, level, topic, duration, origin |
| 2.1 | passage detail returns text + recordings + questions |
| 2.2 | origin is AI or HUMAN and is immutable after creation |
| 2.3 | browse filters by level, topic and max duration and returns them |
| 3 | AIEngine produces passage text and questions through the configured provider |
| 3.1 | triggering generation creates a passage with `origin=AI`, `author_id=NULL` |
| 3.2 | generated set contains at least one EASY, one MEDIUM, one HARD question |
| 3.3 | AI-created content starts `PENDING_REVIEW` and is invisible to learners until `VERIFIED` |
| 4 | contributor can claim, record/upload, and see the claim released on verdict |
| 4.1 | awaiting-recording list excludes claimed and already-recorded passages |
| 4.2 | in-app recording and file upload both create one `AudioRecording` (S1) |
| 4.3 | passage text renders beside the recorder; tashkeel can be toggled on/off |
| 4.4 | `attribution` SHOW_NAME stores the name; ANONYMOUS does not expose it anywhere in the API |
| 5 | reviewer can list pending work, fetch audio, submit criteria and a decision (S2) |
| 5.1 | a decision is impossible without `isFusha = true` and dialect confirmation |
| 5.2 | 1 specialist approval verifies; otherwise 3 distinct general approvals verify; 2 do not |
| 5.3 | assigning a reviewer to their own content is refused (403) |
| 5.4 | disagreement escalates to admin; `resolveDispute(contentId, action)` produces exactly one outcome |
| 5.5 | Fusha + dialect-free are recorded per assignment and required for approval |
| 6 | learner can browse, play, test, see progress (S3) |
| 6.1 | browse returns only `VERIFIED` audio filtered by level, topic, length |
| 6.2 | a session starts on any verified passage and returns questions one at a time |
| 6.3 | approving a passage is blocked if any tier has zero questions |
| 6.4 | a wrong answer lowers the next question's tier; recovery raises it |
| 6.5 | each answer is scored and returns correct answer + explanation |
| 6.6 | progress totals equal the sum over finished sessions |
| 6.7 | flagging a question or audio creates a `PENDING` flag |
| 6.8 | **out of scope v1** — offline download not modelled |
| 7 | authoring available to contributor, reviewer, admin |
| 7.1 | manual question creation and AI generation both attach to a verified passage |
| 7.2 | generated set is balanced across the three tiers |
| 7.3 | human- and AI-authored questions stay `PENDING_REVIEW` until verified |
| 8 | rejection returns the reviewer's `reason` to the contributor |
| 9 | wrong answers return the explanation, not just "incorrect" |
| 10 | admin can moderate, manage users, trigger generation, report |
| 10.1 | reputation recalculates on every verdict and is visible per contributor/reviewer |
| 10.2 | dashboard lists pending flags, disputes and generation jobs; reports export by period |

---

## 7. Non-functional constraints (from the PDF)

| NFR | Constraint on the build |
|---|---|
| 11 | mobile client (Android + iOS) — API must be mobile-first, not web-only |
| 12 | 25 MB upload < 10 s on 4G → direct-to-storage upload, not through the app server |
| 13 | p95 screen load < 2 s → paginate catalogue, cache passage lists |
| 14 | 1 000 concurrent users → stateless API, connection pooling |
| 15 | 99.5 % uptime → health checks, managed DB |
| 16.1 | HTTPS/TLS 1.2+ everywhere |
| 16.2 | voice recordings + AI prompts: retention and consent policy required |
| 16.3 | attribution and copyright on AI-generated text |
| 17.1 | WCAG 2.1 AA — contrast, focus order, screen-reader labels |
| 17.2 | first-time learner needs no instructions |
| 18.1 / 18.2 | Fusha only; content guidelines enforced at review, not in code |
| 19 | recordings 1–3 min → validate duration on upload |
| 20 | Arabic-first, full RTL layout |
| 21 | compensation model — business decision before launch |

---

## 8. How to hand this to an implementing agent

Give it, in this order:

1. `system_requirements.pdf`
2. `class-diagram.puml`
3. `h_seq1…h_seq5.puml`
4. `h_uc.puml`
5. **this file**

Then say:

> Implement the system specified by these artifacts. `class-diagram.puml` is the authoritative
> domain model — every class → entity, every operation → service method, no extra domain concepts.
> The five sequence diagrams are the authoritative flows; do not add steps they don't contain.
> `IMPLEMENTATION.md` fixes the persistence mapping, state machines, API contract and acceptance
> criteria. Every item marked DECISION REQUIRED must be confirmed with me before you build it —
> list your assumptions and wait.

---

## 9. Open decisions (must be answered before implementation)

1. FLAGGED exit transition (§3.1)
2. Claim TTL + sweeper interval (§3.3)
3. Inheritance mapping for `ReviewableContent` (§2)
4. Audio storage backend (§2)
5. Auth mechanism (§5)
6. Scoring, test length, adaptation step, tier minimum, audio-quality scale, reputation formula, playback speeds (§4)
7. `showTashkeel` attribute — add to the class diagram or accept it as UI-only state
8. Which five of these are acceptable to defer for a prototype: browse contract, AI provider, reputation, admin reporting, offline
