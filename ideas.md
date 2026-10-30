# University 3D Printer Farm — User Management & Augmentation Roadmap

Staged implementation plan and checklist for running a University of Florida (UF) student 3D printer farm on BamBuddy.

---

## Phase 1: Account Termination & Time-Limited Access (Completed)
Automate time-limited student accounts by assigning an optional termination datetime (`expires_at`) when creating or updating a user.

- [x] **Database & Model (`User`)**
  - [x] Add nullable `expires_at` (`DateTime`) column to `User` model (`backend/app/models/user.py`).
  - [x] Add startup schema migration (`ALTER TABLE users ADD COLUMN expires_at ...`) in `backend/app/core/database.py`.
  - [x] Add `is_expired` and `is_effectively_active` properties on `User` so expired accounts are immediately treated as inactive at the exact termination timestamp.
- [x] **Real-Time Auth & Session Enforcement**
  - [x] Enforce expiration check in `backend/app/core/auth.py` (JWT validation, login, API key owner resolution, media tokens).
  - [x] Enforce expiration check in `backend/app/api/routes/auth.py` (`/login` including LDAP, `/me`, password reset), `backend/app/api/routes/mfa.py` (2FA & OIDC exchange/callback), `backend/app/api/routes/connected_apps.py`, and `backend/app/main.py` auth gateway.
- [x] **Background Sweeper (`user_expiration_service`)**
  - [x] Periodically sweep users where `is_active = True` and `expires_at <= now` to set `is_active = False` in the database (while safeguarding the last admin).
  - [x] Gracefully handle queued jobs on expiration: leave currently printing (`RUNNING`) jobs untouched to finish, and cancel pending/skipped queue items (`PrintQueueItem`) owned by expired users.
- [x] **User Management API (`backend/app/api/routes/users.py` & `backend/app/schemas/auth.py`)**
  - [x] Add `expires_at` to `UserCreate`, `UserUpdate`, and `UserResponse` schemas.
  - [x] Support setting, updating, and clearing `expires_at` on `POST /users` and `PATCH /users/{id}`.
  - [x] Prevent setting a past expiration timestamp on the last active admin user.
  - [x] Automatically re-activate (`is_active = True`) an expired user if an admin extends `expires_at` into the future (unless `is_active=False` was explicitly passed).
- [x] **Frontend UI (`UsersPage.tsx`, `SettingsPage.tsx`, `CreateUserAdvancedAuthModal.tsx`)**
  - [x] Add Termination Datetime input (`datetime-local`) and "Never expires / Clear" action to Create User (Basic & Advanced Auth) and Edit User modals.
  - [x] Add quick UF semester & duration presets (*End of Fall*, *End of Spring*, *End of Summer*, *+16 Weeks*, *+1 Year*).
  - [x] Display expiration status badges (*Expired*, *Expires Soon*, *Expires \<date\>*) in the Users table and Settings > Users list.
- [x] **Automated Tests**
  - [x] Add unit/integration tests covering user creation/update with `expires_at`, immediate JWT/login rejection upon expiry, background sweeper deactivation + pending queue cancellation, and last-admin protection.

---

## Phase 2: Group-Level Expiration & Expiration Warnings (Completed)
Allow course/semester permissions to expire without locking students out of their historical portfolio data.

- [x] **Group Membership Expiration**
  - [x] Add optional `expires_at` column to `user_groups` association table (or `Group` model for cohort groups like `EML2322L-Fall2026`).
  - [x] Automatically drop expired group memberships so students transition from `Operators` (can queue/print) to `Viewers` (read-only access to past `PrintArchive` logs and 3MF downloads).
- [x] **Automated Pre-Expiration Email Warnings**
  - [x] Send email notifications via `email_service.py` at 7 days and 24 hours prior to account or group expiration.
  - [x] Add customizable notification templates for impending account/semester expiration.

---

## Phase 3: Cohort & Roster Onboarding (UF GatorLink / Canvas Workflow) (Completed)
Streamline onboarding hundreds of students each semester during drop/add week.

- [x] **Bulk CSV Roster Import**
  - [x] Admin endpoint and UI modal to upload a Canvas / UF Registrar CSV (`username, email, group, cost_center, expires_at`).
  - [x] Upsert logic: create new student accounts or extend `expires_at` and update group/cost-center memberships for returning students.
- [x] **Self-Service Enrollment Codes / Invite Links**
  - [x] Allow admins/instructors to mint an invite code/URL bound to a target `Group`, `CostCenter`, `expires_at` date, and max redemption count.
  - [x] Auto-enroll students who sign in via UF SSO (`OIDCProvider`) or local registration using the invite link.

---

## Phase 4: Training & Safety Gatekeeping (Certifications) (Completed)
Ensure students only operate printer tiers they have been trained and signed off to use.

- [x] **Printer-to-Group Access Control**
  - [x] Add `allowed_group_ids` relationship on `Printer` (empty = all groups with printer permissions; populated = restricted to specified groups).
  - [x] Enforce group-based printer eligibility in queue dispatch (`print_scheduler.py`), direct print start, and printer control routes (e.g., *Tier 1: A1/P1S for General Undergrads*, *Tier 2: X1E/H2D for Senior Design / Grad Research*).
- [x] **Safety Certification Expiry**
  - [x] Add `certified_until` timestamp or certification badges to user profiles requiring annual safety quiz renewal before queueing jobs.

---

## Phase 5: Print Quotas, Fair-Share Scheduling & TA Approval Queue (Completed)
Prevent printer farm monopolization during midterm and finals weeks.

- [x] **Per-Group Job & Concurrency Quotas**
  - [x] Add optional limits on `Group`: `max_print_duration_minutes`, `max_filament_grams_per_job`, and `max_active_queue_items`.
  - [x] Enforce quotas when students enqueue or slice prints, with clear error messages indicating which group limit was exceeded.
- [x] **TA Approval Queue ("Hold for Review")**
  - [x] Add `requires_approval` group setting and `pending_approval` status on `PrintQueueItem`.
  - [x] Add `queue:approve` permission for Lab TAs to inspect plate slicing/orientation, approve jobs for dispatch, or reject with feedback notes.
- [x] **Operating Hours & Overnight Print Rules**
  - [x] Configurable time-of-day windows per group (e.g., restrict novice prints to staffed lab hours, or route long prints >4h to overnight slots).

---

## Phase 6: Physical Lab Operations & End-of-Semester Cleanup (Completed)
Keep build plates moving and disk storage clean across semesters.

- [x] **Build-Plate Pickup SLA & Overdue Alerts**
  - [x] Start a pickup timer when a student print finishes; notify the student and Lab TAs if the plate is not cleared within $X$ hours.
- [x] **End-of-Semester Library Storage Cleanup**
  - [x] Automatically move a student's `LibraryFile` uploads to trash $N$ days after their account `expires_at` date.
