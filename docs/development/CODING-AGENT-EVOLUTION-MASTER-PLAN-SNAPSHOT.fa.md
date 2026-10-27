<!-- Repository snapshot: sanitized for publication on 2026-10-27. -->
> This is a reviewed, path-redacted snapshot of the externally maintained canonical Coding Agent Evolution plan. Historical entries retain their original subjects; the current task index and later ledger entries record superseding decisions. The user authorized this repository copy while preserving the external original. This is distinct from the restricted v0.8 master plan.

# طرح جامع تکامل قابلیت‌های کدنویسی ComputerPilot MCP

<!-- MASTER-QUICK-START-START -->
<a id="quick-start"></a>
## QUICK START / CURRENT STATE

- PLAN_STATUS: DESIGN_COMPLETE؛ EXECUTION_STATUS: IN_PROGRESS؛ authoritative task state در TASK INDEX MAP و شواهد در Ledger است.
- آغاز بررسی: 2026-10-18؛ ادامه و تکمیل: 2026-10-19، Asia/Tehran؛ نسخه طرح: 1؛ نویسنده هم‌زمان: فقط همین نشست.
- مسیر canonical: LOCAL_PATH_REDACTED
- ریشه سورس: LOCAL_PATH_REDACTED
- SOURCE_BASE (historical): e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4؛ accepted runtime source / LOCAL_MAIN / ORIGIN_MAIN / LIVE_GITHUB_MAIN: 965a5cb641af40f1768f6fe3512d8332662ab28c.
- Branch: fix/coding-evolution-acceptance in the original checkout; follow-up changes are tests/documentation only. PR #82 is MERGED; subsequent publication is recorded in the Ledger.
- PR #81 merged into the historical source base; PR #82 merged as 965a5cb. No release or tag is part of this continuation.
- release پایدار v0.8.0 روی e4930a77c60b844a7845a82997e178c16694562e است. main جدیدتر از release است.
- hosted main: all ten applicable checks succeeded on 965a5cb; Publish Release detected a non-release subject and skipped publication. Later follow-up gates are separate.
- تغییرات از قبل موجود: panel HTML and the two reviewed LSP reports were preserved and committed in PR #82; uv.lock remains untracked and is not used by requirements-based installation.
- CURRENT_PHASE: backup recovery, context admission and Batch containment corrections accepted on the validated runtime source. P15-04 is BLOCKED by the measured single-file production overhead; P05-02/03 and P14-06 remain BLOCKED.
- NEXT_TASK_ID: no ungated feature activation. P15-04 needs a safety-preserving performance solution meeting the unchanged 5ms target; P05-02 needs a separately budgeted, independently graded model run.
- P14 mandatory scope remains VERIFIED; P14-05 is DEFERRED and P14-06 BLOCKED. P15-02 safety is VERIFIED; P15-04 final performance acceptance is BLOCKED and P15-03 DEFERRED.
- Historical P08 evidence: P08 local closure2298passed28skipped1known unrelated test-isolation warning540.96s; full Ruff/mypy293/compileall/startup151/health5 PASS. Full catalogue209326->191656bytes; ten schemas48706->31036bytes; normalized fixed byte/token allowances PASS in all profiles. Small-profile actual growthcoding30.09percent/core58.19percent disclosed. No Git publication or live activation.
- Real LSP follow-up evidence (2026-10-26): Python/Pyright1.1.403, TLS5.3.0+TS5.9.3 and PHP/Intelephense1.18.5 all exit0. Exact cross-file definition; declaration/call references3/3/2; real clean0→type-error1→repair0 diagnostics; version/hash/edit; complete cycle after fresh restart; owned process/fixture cleanup PASS. Full pytest2338PASS28SKIP518.06s, independent243PASS1SKIP, full Ruff/mypy297/compileall/startup151/health5 PASS. [Acceptance appendix](#real-lsp-provider-acceptance-20261026) and repo docs/development/REAL-LSP-PROVIDER-ACCEPTANCE-2026-10-26.md. Hosted/other-platform/production activation NOT RUN.
- اجرای مجاز فعلی: user authorized reviewed commits, push and conditional PR merge after safety and same-head gates; no tag or release. Live operational state and tunnel remain protected.
- 16 فاز (P00..P15)، 25 milestone و 54 task؛ current executable safety revalidation is complete; conditional gates remain explicit.
- Task state: VERIFIED=43, IN_PROGRESS=0, IMPLEMENTED_UNVERIFIED=0, NOT_STARTED=0, BLOCKED=6, DEFERRED=5, FAILED=0, NEEDS_REVALIDATION=0. The Index is authoritative.
- VERIFIED در سه task خواندنی فقط صحت خروجی طراحی/بازرسی را نشان می‌دهد؛ پیشرفت پیاده‌سازی قابلیت‌های پیشنهادی نیست.
- Implementation Progress: 32/33=97.0% implemented; Verified Completion=32/33=97.0% weighted. These weighted figures exclude weight-zero conditional tasks and are not 100% plan acceptance; 11 task records remain BLOCKED/DEFERRED.
- NEEDS_REVALIDATION_TASK_IDS: none. P03-05/P04-03/P15-02 have accepted corrective evidence; P15-04 is explicitly BLOCKED on numeric performance, not silently waived.
- فرمول: Σ(weight × implemented)/Σ(weight) و Σ(weight × accepted)/Σ(weight). برای taskهای جدید implemented=0 و accepted=0 است؛ taskهای تصمیم/پذیرش/اختیاری وزن0 دارند. مقدار ناشناخته NOT_MEASURED است و هرگز صفر فرض نمی‌شود.
- READY_TO_EXECUTE: فقط با dependencyهای VERIFIED، Hash Guard صحیح و نبود blocker؛ مجوز جدید CI گیت‌های مستقل امنیت/deployment را حذف نمی‌کند.
- طراحی P0 عمدتاً خرد و review-gated است؛ اطمینان از اجرای مدل ضعیف فقط با dry-run خواندنی سنجیده می‌شود.
- Plan SHA خودارجاعی ذخیره نمی‌شود؛ پیش از هر update از Get-FileHash همین فایل استفاده کن و digest را در handoff بیرونی ثبت کن.
- [Snapshot و شواهد](#snapshot)؛ [۱۱ یافته](#findings)؛ [معماری](#architecture)؛ [قراردادها](#shared)؛ [Runbook](#runbook)؛ [ارزیابی اجراپذیری](#weak-review)؛ [Ledger](#ledger).

<!-- TASK-INDEX-START -->
## TASK INDEX MAP

STATE_RECORD_ID همان Task ID در ستون نخست است؛ این جدول تنها authority وضعیت task است. متن بلوک‌ها وضعیت مستقل تکرار نمی‌کند.

| ID | تغییر/هدف | State | Executor class | Weight | Depends | Anchor |
|---|---|---|---|---:|---|---|
| <a id="state-TASK-P00-01"></a>TASK-P00-01 | ثبت baseline و گیت‌های همان SHA | VERIFIED | WEAK_READY | 0 | — | [بلوک](#task-p00-01) |
| <a id="state-TASK-P00-02"></a>TASK-P00-02 | اندازه‌گیری امن کاتالوگ و بازتولید خواندنی شکاف‌ها | VERIFIED | WEAK_READY | 0 | TASK-P00-01 | [بلوک](#task-p00-02) |
| <a id="state-TASK-P01-01"></a>TASK-P01-01 | اصلاح صفحه‌بندی سراسری روابط | VERIFIED | WEAK_READY | 1 | TASK-P00-02 | [بلوک](#task-p01-01) |
| <a id="state-TASK-P01-02"></a>TASK-P01-02 | صداقت completeness و confidence | VERIFIED | WEAK_READY | 1 | TASK-P01-01 | [بلوک](#task-p01-02) |
| <a id="state-TASK-P01-03"></a>TASK-P01-03 | پیمایش عمق صفر تا دو با بودجه ثابت | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P01-02 | [بلوک](#task-p01-03) |
| <a id="state-TASK-P01-04"></a>TASK-P01-04 | نسخه محتوا و کران cache | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P01-03 | [بلوک](#task-p01-04) |
| <a id="state-TASK-P02-01"></a>TASK-P02-01 | fixture نشست LSP چنددرخواستی | VERIFIED | WEAK_READY | 1 | TASK-P00-02 | [بلوک](#task-p02-01) |
| <a id="state-TASK-P02-02"></a>TASK-P02-02 | provider و هویت نشست قابل اعتماد | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P02-01 | [بلوک](#task-p02-02) |
| <a id="state-TASK-P02-03"></a>TASK-P02-03 | مالکیت درخت فرایند و محیط حداقلی | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P02-02 | [بلوک](#task-p02-03) |
| <a id="state-TASK-P02-04"></a>TASK-P02-04 | transport و correlation محدود | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P02-03 | [بلوک](#task-p02-04) |
| <a id="state-TASK-P02-05"></a>TASK-P02-05 | state machine نشست و single-flight | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P02-04, TASK-P03-01 | [بلوک](#task-p02-05) |
| <a id="state-TASK-P02-06"></a>TASK-P02-06 | همگام‌سازی نسخه سند و invalidation | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P02-05, TASK-P01-04 | [بلوک](#task-p02-06) |
| <a id="state-TASK-P02-07"></a>TASK-P02-07 | اتصال registry و drain به lifecycle | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P02-06, TASK-P03-01 | [بلوک](#task-p02-07) |
| <a id="state-TASK-P02-08"></a>TASK-P02-08 | پذیرش crash و benchmark سرد/گرم | VERIFIED | STRONG_ONLY | 0 | TASK-P02-07 | [بلوک](#task-p02-08) |
| <a id="state-TASK-P03-01"></a>TASK-P03-01 | مدل و اعتبارسنجی methodهای LSP | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P02-02 | [بلوک](#task-p03-01) |
| <a id="state-TASK-P03-02"></a>TASK-P03-02 | ورودی typed سازگار با params قدیمی | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P03-01, TASK-P02-07 | [بلوک](#task-p03-02) |
| <a id="state-TASK-P03-03"></a>TASK-P03-03 | نسخه سند و guard ویرایش معنایی | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P03-02 | [بلوک](#task-p03-03) |
| <a id="state-TASK-P03-05"></a>TASK-P03-05 | بازیابی durable ویرایش چندفایلی و حفظ backup | VERIFIED | STRONG_ONLY | 1 | TASK-P03-03 | [بلوک](#task-p03-05) |
| <a id="state-TASK-P03-04"></a>TASK-P03-04 | پذیرش schema و denial و recovery | VERIFIED | STRONG_ONLY | 0 | TASK-P03-03, TASK-P02-08, TASK-P03-05 | [بلوک](#task-p03-04) |
| <a id="state-TASK-P04-01"></a>TASK-P04-01 | قرارداد شواهد و هویت نماد چندزبانه | VERIFIED | WEAK_READY | 1 | TASK-P01-04, TASK-P03-04 | [بلوک](#task-p04-01) |
| <a id="state-TASK-P04-02"></a>TASK-P04-02 | adapter معنایی محدود روی نشست موجود | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P04-01 | [بلوک](#task-p04-02) |
| <a id="state-TASK-P04-03"></a>TASK-P04-03 | اتصال context چندزبانه و cache مشترک | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P04-02 | [بلوک](#task-p04-03) |
| <a id="state-TASK-P04-04"></a>TASK-P04-04 | fixtureهای Python و TS و PHP و worktree | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P04-03 | [بلوک](#task-p04-04) |
| <a id="state-TASK-P05-01"></a>TASK-P05-01 | ساخت server بدون side effect زمان ثبت | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P00-02 | [بلوک](#task-p05-01) |
| <a id="state-TASK-P05-02"></a>TASK-P05-02 | آزمایش profile coding_core ثابت | BLOCKED | WEAK_WITH_REVIEW | 1 | TASK-P05-01, TASK-P03-04 | [بلوک](#task-p05-02) |
| <a id="state-TASK-P05-03"></a>TASK-P05-03 | refresh پویا فقط پس از شواهد client | BLOCKED | DESIGN_BLOCKED | 0 | TASK-P05-02 | [بلوک](#task-p05-03) |
| <a id="state-TASK-P06-01"></a>TASK-P06-01 | تشخیص خواندنی package و test runner | VERIFIED | WEAK_READY | 1 | TASK-P04-04 | [بلوک](#task-p06-01) |
| <a id="state-TASK-P06-02"></a>TASK-P06-02 | انتخاب محافظه‌کارانه تست چندزبانه | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P06-01 | [بلوک](#task-p06-02) |
| <a id="state-TASK-P06-03"></a>TASK-P06-03 | ساخت و اجرای runner محدود چندزبانه | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P06-02 | [بلوک](#task-p06-03) |
| <a id="state-TASK-P06-04"></a>TASK-P06-04 | اتصال verification مستقیم و background و workflow | VERIFIED | STRONG_ONLY | 1 | TASK-P06-03 | [بلوک](#task-p06-04) |
| <a id="state-TASK-P06-05"></a>TASK-P06-05 | پذیرش یکپارچه انتخاب تست و اجرای چندزبانه | VERIFIED | STRONG_ONLY | 0 | TASK-P06-04 | [بلوک](#task-p06-05) |
| <a id="state-TASK-P07-01"></a>TASK-P07-01 | پذیرش دوباره migration و no-replay موجود | VERIFIED | STRONG_ONLY | 0 | TASK-P00-01 | [بلوک](#task-p07-01) |
| <a id="state-TASK-P07-02"></a>TASK-P07-02 | تصمیم GO مستقل برای DAG | VERIFIED | STRONG_ONLY | 0 | TASK-P07-01 | [بلوک](#task-p07-02) |
| <a id="state-TASK-P08-01"></a>TASK-P08-01 | خروجی typed برای context و impact | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P04-04, TASK-P06-05 | [بلوک](#task-p08-01) |
| <a id="state-TASK-P08-02"></a>TASK-P08-02 | خروجی typed برای جستجو و خلاصه پروژه | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P08-01 | [بلوک](#task-p08-02) |
| <a id="state-TASK-P08-03"></a>TASK-P08-03 | خروجی typed وضعیت job و workflow و health | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P08-02 | [بلوک](#task-p08-03) |
| <a id="state-TASK-P08-04"></a>TASK-P08-04 | خروجی typed verification و زبان و Router | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P08-03 | [بلوک](#task-p08-04) |
| <a id="state-TASK-P09-01"></a>TASK-P09-01 | runbook gh و هویت PR و release | VERIFIED | WEAK_READY | 0 | TASK-P00-01 | [بلوک](#task-p09-01) |
| <a id="state-TASK-P10-01"></a>TASK-P10-01 | قرارداد capture و تبدیل مختصات offline | DEFERRED | WEAK_WITH_REVIEW | 0 | TASK-P03-04 | [بلوک](#task-p10-01) |
| <a id="state-TASK-P10-02"></a>TASK-P10-02 | گیت grounding و dispatch حساس | BLOCKED | DESIGN_BLOCKED | 0 | TASK-P10-01, TASK-P07-01 | [بلوک](#task-p10-02) |
| <a id="state-TASK-P11-01"></a>TASK-P11-01 | تصمیم عدم افزودن ابزار AGENTS فعلاً | DEFERRED | STRONG_ONLY | 0 | TASK-P00-01 | [بلوک](#task-p11-01) |
| <a id="state-TASK-P12-01"></a>TASK-P12-01 | مرز runtime و controller بیرونی | DEFERRED | STRONG_ONLY | 0 | TASK-P04-04, TASK-P06-05 | [بلوک](#task-p12-01) |
| <a id="state-TASK-P13-01"></a>TASK-P13-01 | قرارداد delegation و isolation آینده | BLOCKED | DESIGN_BLOCKED | 0 | TASK-P02-08, TASK-P03-04, TASK-P04-04, TASK-P07-02, TASK-P12-01 | [بلوک](#task-p13-01) |
| <a id="state-TASK-P14-01"></a>TASK-P14-01 | ممیزی منشأ تنظیم DAG و بنچمارک هم‌ارز اجرای ابزارها | VERIFIED | WEAK_READY | 0 | TASK-P00-02, TASK-P07-01 | [بلوک](#task-p14-01) |
| <a id="state-TASK-P14-02"></a>TASK-P14-02 | ردیابی همبسته مرز سرور، Timing/Audit و Job Worker | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P14-01, TASK-P08-03 | [بلوک](#task-p14-02) |
| <a id="state-TASK-P14-03"></a>TASK-P14-03 | هدایت Schema/Router برای انتخاب ابزار بومی و Process به‌جای Shell غیرضروری | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P14-01, TASK-P08-04 | [بلوک](#task-p14-03) |
| <a id="state-TASK-P14-04"></a>TASK-P14-04 | راهنمای تجمیع ویرایش‌ها و انتخاب Direct در برابر Durable Job | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P14-03 | [بلوک](#task-p14-04) |
| <a id="state-TASK-P14-05"></a>TASK-P14-05 | Preflight اختیاری با گیت اثبات سود و بودجه زمانی | DEFERRED | WEAK_WITH_REVIEW | 0 | TASK-P14-01, TASK-P04-01, TASK-P06-01, TASK-P08-02 | [بلوک](#task-p14-05) |
| <a id="state-TASK-P14-06"></a>TASK-P14-06 | طراحیِ مشروط قفل دولایه‌ای Job، بدون تغییر کد تا رفع گیت ایمنی | BLOCKED | DESIGN_BLOCKED | 0 | TASK-P14-01, TASK-P14-02, TASK-P07-01 | [بلوک](#task-p14-06) |
| <a id="state-TASK-P14-07"></a>TASK-P14-07 | پذیرش کمی P14 و عدم پسرفت؛ تسک‌های مشروط مانع پذیرش بخش قطعی نیستند | VERIFIED | STRONG_ONLY | 0 | TASK-P14-02, TASK-P14-03, TASK-P14-04 | [بلوک](#task-p14-07) |
| <a id="state-TASK-P15-01"></a>TASK-P15-01 | قرارداد و بنچمارک Native Read Batch و تصمیم بودجهٔ کاتالوگ | VERIFIED | STRONG_ONLY | 0 | TASK-P14-01 | [بلوک](#task-p15-01) |
| <a id="state-TASK-P15-02"></a>TASK-P15-02 | Batch خواندن فایل با ورودی typed و خروجی و منابع محدود | VERIFIED | WEAK_WITH_REVIEW | 1 | TASK-P15-01, TASK-P14-02 | [بلوک](#task-p15-02) |
| <a id="state-TASK-P15-03"></a>TASK-P15-03 | گسترش مشروط به metadata/search/Git فقط پس از اثبات safety و سود | DEFERRED | STRONG_ONLY | 0 | TASK-P15-02, TASK-P14-03 | [بلوک](#task-p15-03) |
| <a id="state-TASK-P15-04"></a>TASK-P15-04 | پذیرش یک درخواست/پاسخ، cancellation، پروفایل و عدم پسرفت Batch | BLOCKED | STRONG_ONLY | 0 | TASK-P15-02, TASK-P14-04 | [بلوک](#task-p15-04) |
<!-- TASK-INDEX-END -->

## PHASE DASHBOARD

| فاز | اولویت | State | Implementation / Verified | Milestone | مانع یا قدم بعد |
|---|---|---|---|---|---|
| P00 | Gate | VERIFIED | N/A / N/A؛ دو audit task | M00 | baseline فقط تا تغییر SHA معتبر است |
| P01 | P0 | VERIFIED | 4/4 / 4/4 | M01a pagination+truth، M01b depth+cache | P02-01 |
| P02 | P0 | COMPLETE (original tasks plus Windows LIVE LSP) | 7/7 / 7/7 (original scope) | M02a provider+transport، M02b lifecycle، M02c acceptance | Original8tasks accepted; real connected MCP on Windows with Pyright/TypeScript LS/Intelephense definition/references/diagnostics edit/repair VERIFIED_LIVE (2026-10-26); cross-OS and release validation remain separate |
| P03 | P0 | VERIFIED | 4/4 implemented / 4/4 verified | M03 accepted | Corrective source, recovery regression suite and same-source hosted validation accepted; see EVIDENCE-16 |
| P04 | P0 | VERIFIED | 4/4 implemented / 4/4 verified | M04 accepted | Context capacity/freshness correction accepted; see EVIDENCE-16 |
| P05 | P1 | BLOCKED | 2/2 / 1/2 | M05 | Static coding_core and three-profile AGY micro-pilot exist; controlled20x3x3 model evaluation remains unaccepted. P05-03 actual-client refresh transcript absent |
| P06 | P1 | COMPLETE | 4/4 / 4/4 | M06 | All5tasks accepted; weighted4/4; local351pass3skip and four actual runner versions/16cases retained; no runtime activation |
| P07 | P1 | VERIFIED; authorized local GO | N/A / N/A | M07 | All10hosted31c11ab PASS; live cap16 with17/17completed; compatible schema11 backup/restore; no unresolved effects; source default remains operator gated |
| P08 | P2 | COMPLETE | 4/4 / 4/4 | M08a/M08b | Local2298pass28skip1known outside-scope warning; full18.73percent, normalized byte/token gates PASS; independent final review PASS; subsequent runtime source 965a5cb passed all hosted gates |
| P09 | P2 | VERIFIED | N/A / N/A؛ runbook | M09 | امروز mutation انتشار ندارد |
| P10 | P2 | BLOCKED | NOT_MEASURED / NOT_MEASURED | M10 | DESIGN-BLOCKER-02؛ سخت‌افزار/grounder |
| P11 | اختیاری | DEFERRED | N/A / N/A | M11 | نیاز اضافه بر host اثبات نشده |
| P12 | P2/P3 | DEFERRED | N/A / N/A | M12 | runtime به controller مستقل تبدیل نمی‌شود |
| P13 | P3 | BLOCKED | NOT_MEASURED / NOT_MEASURED | M13 | DESIGN-BLOCKER-03؛ isolation و authorization |
| P14 | P1/P2 | VERIFIED (mandatory scope) | 3/3 / 3/3؛ optional decisions independent | M14a/M14b VERIFIED; M14c conditional | P14-05 DEFERRED / P14-06 BLOCKED, no automatic activation |
| P15 | P1/P2 | BLOCKED (final performance acceptance) | 1/1 implemented / 1/1 safety verified | M15b numeric gate BLOCKED | P15-02 safety accepted; production single-file overhead exceeds 5ms; P15-03 DEFERRED |

Milestone یک checkpoint مرورپذیر است؛ هر task یک نوبت محلی یا یک gate مشخص. P00/P04/P05/P07/P09/P10/P11/P12/P13 هرکدام یک milestone؛ P01/P03/P06/P08 هرکدام دو؛ P02 سه؛ و P14 سه milestone جدید M14a/M14b/M14c: P15 دو milestone M15a/M15b اضافه می‌کند: جمع25. M14c شامل تصمیم‌های مشروط است و BLOCKED/DEFERRED آن، پذیرش M14a/M14b را بی‌دلیل مسدود نمی‌کند.
<!-- MASTER-QUICK-START-END -->

<!-- CHUNK-01-END -->

<a id="snapshot"></a>
## SOURCE SNAPSHOT و حدود بررسی

SOURCE_BASE در تمام SOURCE_ANCHORهای این سند دقیقاً e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 است. خطوط به این snapshot مربوط‌اند؛ پس از تغییر، anchor و digest authority تشخیص drift هستند. فایل‌های Python مورد تحلیل نسبت به HEAD تغییری نداشتند. HEAD، origin/main محلی و main زنده با API خوانده شدند؛ fetch یا checkout انجام نشد.

| شاهد | نتیجه و هویت |
|---|---|
| SRC-GIT | git status: یک tracked diff در panel و uv.lock untracked؛ هیچ commit محلیِ جلوتر از main مشاهده نشد |
| SRC-RELEASE | [v0.8.0](https://github.com/dibbed/computerpilot-mcp/releases/tag/v0.8.0)، publishedAt=2026-10-14T19:51:12Z، target=e4930a77c60b844a7845a82997e178c16694562e |
| SRC-PR81 | [PR81](https://github.com/dibbed/computerpilot-mcp/pull/81)، merge در 2026-10-18T13:24:53Z، final head=379e9e5db987bc70d9d3e865001bd2d53ecf2a17 |
| CI-MAIN | [CI 39131440536](https://github.com/dibbed/computerpilot-mcp/actions/runs/39131440536)، push روی SOURCE_BASE، 6/6 success |
| PLATFORM-MAIN | [Platform 39131440505](https://github.com/dibbed/computerpilot-mcp/actions/runs/39131440505)، push روی SOURCE_BASE، 3/3 success |
| PACKAGE-MAIN | [Packaging 39131440461](https://github.com/dibbed/computerpilot-mcp/actions/runs/39131440461)، push روی SOURCE_BASE، package success |
| CI-PR81 | [CI 39128348447](https://github.com/dibbed/computerpilot-mcp/actions/runs/39128348447)، [Platform 39128348497](https://github.com/dibbed/computerpilot-mcp/actions/runs/39128348497)، [Packaging 39128348431](https://github.com/dibbed/computerpilot-mcp/actions/runs/39128348431)، همه 10 check روی head نهایی موفق |
| PUBLISH-MAIN | [39131611639](https://github.com/dibbed/computerpilot-mcp/actions/runs/39131611639) job success؛ log شامل publish=false است. موفقیت این workflow به‌معنی انتشار release جدید نیست |
| CLI | gh 2.74.2؛ API و auth خواندنی موفق، active account=true؛ credential در این سند ثبت نمی‌شود |
| DAG | core/config.py:69 default=False؛ متغیر MCP_WORKFLOW_DAG_ENABLED در process همین تحلیل UNSET؛ وضعیت process سرور مستقل UNVERIFIED |
| STORE | core/workflow_store.py:33 schema supported=11؛ فایل DB کاربر باز/مهاجرت نشد؛ user_version زنده UNVERIFIED |
| LOCAL TESTS | suite/collection/build/Doctor اجرا نشد؛ local runtime acceptance این نوبت UNVERIFIED |
| PLAN LOCATION | خروجی هم‌نام قبل از ساخت موجود نبود. master plan قدیمی v0.8 در مسیر اعلام‌شده Downloads پیدا نشد؛ بازآفرینی نشد. طرح حاضر دامنه تکامل جدید دارد و جایگزین ledger قدیمی یا DAG نیست |

قرارداد ریشه AGENTS.md و دستورهای ارائه‌شده کاربر خوانده شدند. تنها AGENTS.md یافت‌شده در فایل‌های پروژه همین فایل بود؛ فایل‌های حساس و state عملیاتی برای کشف دستورها پیمایش نشدند. در parentهای مستقیمِ بررسی‌شده AGENTS اضافی وجود نداشت.

### اندازه‌گیری کاتالوگ و محدودیت روش

CURRENT / MEASURED، Windows، بدون extension، همان SDK نصب‌شده:

| Profile | تعداد | JSON فشرده UTF-8 byte | خروجی generic object |
|---|---:|---:|---:|
| full | 151 | 153636 | 151 |
| coding | 96 | 92674 | 96 |
| minimal | 32 | 31555 | 32 |

روش serialization: list از model_dump(mode="json", by_alias=True, exclude_none=True)، سپس json.dumps با ensure_ascii=False و separators=(',', ':'). این عدد transport envelope و token واقعی client را شامل نمی‌شود. اعداد تاریخی 168527/101592 با serialization نامعلوم معیار regression مستقیم نیستند.

ساخت عادی server، ensure_runtime_dirs و JobStore در tools/testing/registry.py:43 و tools/jobs/registry.py:22 دارد؛ همچنین ensure_job_scheduler فراخوانی می‌شود. بنابراین برای اندازه‌گیری فقط در process موقت Python -B، ensure_runtime_dirs، دو constructor ذخیره‌ساز و دو scheduler call با no-op جایگزین شدند. ابزارها فراخوانی نشدند؛ lifespan اجرا نشد؛ کل descriptorها از registrar واقعی ساخته شدند. audit hook هر write/network/spawn را منع و Thread.start را رد کرد؛ اجرای نهایی BLOCKED_EVENTS=[] بود. دو تلاش اولیه به socket.bind مربوط به self-pipe asyncio و mkdir ذخیره‌ساز برخورد کردند؛ پس از بررسی source SDK، coroutine list_tools بدون event loop و بدون suspension مصرف شد. این اندازه‌گیری **schema-only** است، نه startup smoke یا آزمون lifecycle.

### خطاهای CI: زنجیره واقعی، نه تکرار فرض تاریخی

| ID / class | Fail → شاهد | اصلاح موجود → شاهد جدید | باقی‌مانده |
|---|---|---|---|
| CI-HIST-01 / HISTORICAL RESOLVED FAILURE | run 39118430496، SHA 4a18a372e99525ce72b03fa2d337339140bca97f، macOS Python3.12؛ test_collect_diagnostics_total_deadline_prevents_late_dispatch؛ assert [] == ['ruff'] | commit 81d9f423ade550f75f02147e2716314498bcbcbc؛ پذیرش timeout قبل admission با حفظ عدم dispatch mypy؛ head نهایی و main 10/10 سبز | کار جدید رفع این assertion تعریف نمی‌شود |
| CI-HIST-02 / HISTORICAL RESOLVED FAILURE | همان run/SHA، Windows Python3.12؛ test_count_retention_coalesces_concurrent_atomic_backup_publication؛ assert 3 == 2 | commit 705bb838a108823384280eb0d604cf686016d159؛ شمارش فقط directory fixture؛ head نهایی و main سبز | benchmark performance همچنان موضوع مستقل است |
| CI-HIST-03 / HISTORICAL RESOLVED FAILURE | run39074878955، SHA b5919d5078135065a8d3a4beb15172424e52e5e7؛ migration v10 sqlite3.DatabaseError:not authorized و Windows descendant retirement | checks نهایی موفق؛ برای ریشه دقیق همه failureهای قدیمی تحقیق مستقلی انجام نشد | ROOT_CAUSE_UNVERIFIED برای این دو شاخه؛ bug جاری محسوب نشوند |
| GATE-DAG / PENDING-UNVERIFIED | ledger DAG در docs/roadmap/COMPUTERPILOT-PARALLEL-EXECUTION-AND-DAG-MASTER-PLAN.fa.md:9–19 هنوز release-NO-GO دارد | سورس merged و matrix سبز است | امضای performance/production activation و release مجزا؛ TASK-P07-02 |
| CURRENT FAILURE | روی SOURCE_BASE در سه workflow الزامی مشاهده نشد | شواهد بالا | صفر observed hosted failure؛ تضمین نبود bug نیست |

لینک fail اصلی: [39118430496](https://github.com/dibbed/computerpilot-mcp/actions/runs/39118430496). این run با source tests فعلی و diff دو commit مقایسه شد. برای همه تاریخچه CI ادعای بررسی کامل نمی‌شود.

<a id="findings"></a>
## HISTORICAL FINDINGS RECONCILIATION — دقیقاً ۱۱ یافته

Historical source در prompt عمدتاً e66c8c9 نامیده شده؛ این بار همان commit هنوز main است. بنابراین Changed/Resolved را فقط وقتی evidence جدا داریم می‌نویسیم. تمام ردیف‌ها location=MAIN و released بودنشان از این snapshot نتیجه نمی‌شود.

| ID | Historical claim | Verdict | شاهد source / test در SOURCE_BASE | باقی‌مانده، severity و owner |
|---|---|---|---|---|
| FIND-01 | Full151/Coding96 و discovery ناکافی | Partially Confirmed | core/tool_profiles.py:16–40، core/registry.py:101–194؛ tests/test_tool_profiles.py::test_full_is_default_and_preserves_complete_catalog؛ شمار ابزار تأیید، byte روش جدید بالا | discovery و recommend موجودند؛ «ناکافی» نیاز به benchmark دارد. P1، TASK-P05-02/03 |
| FIND-02 | intelligence متمرکز بر Python | Confirmed | tools/project/service.py:102–113، tools/project/index.py::build_python_metadata؛ tests/test_code_context.py::test_python_metadata_records_calls_and_references_without_retaining_ast | TS/PHP semantic adapters ندارند؛ P0، P04 |
| FIND-03 | خروجی dict[str,Any] | Confirmed | core/registry.py و registrars؛ full151 generic outputSchema، tests/test_language_registry.py | ضروری P0 در P01/P03؛ عمومی P2 در P08؛ generic بودن به‌تنهایی نبود validation ورودی نیست |
| FIND-04 | LSP params عمومی | Confirmed | tools/language/registry.py:30–52، common؛ tests/test_language_registry.py::test_language_tool_schemas_do_not_expose_arbitrary_command | typed position و method validation مشترک، P0، TASK-P03-01/02 |
| FIND-05 | ابزار typed کافی Push/PR/Release نیست | Partially Confirmed | tools/git/registry.py:37–337 محلی؛ docs/release-process.md و SECURITY.md؛ tests/test_git_tools.py | ابزار اختصاصی remote وجود ندارد؛ نیاز ثابت نشده؛ gh مسیر پذیرفته؛ P2، TASK-P09-01 |
| FIND-06 | max_depth مصرف نمی‌شود | Confirmed | tools/project/context.py:272–292، del max_depth؛ registry.py:273 فقط 1..2؛ tests/test_code_context.py | comment رزرو عمق2، وعده schema را اجرایی نمی‌کند؛ P0، TASK-P01-03 |
| FIND-07 | recommend_tools عمدتاً lexical | Confirmed | core/registry.py:128–194 score terms/name/description؛ tests/test_tool_profiles.py::test_discovery_reports_active_profile_and_recommends_registered_tools | با ExecutionRouter اشتباه نشود؛ correctness defect ذاتی نیست؛ P1 آزمایش P05 |
| FIND-08 | affected_tests Python-centric | Confirmed | tools/testing/impact.py:175–309؛ tests/test_affected_tests.py؛ build_verification_plan در verification.py:22–37 | تغییر TS/PHP ممکن است decision=none و checks_only شود؛ fallback در P06 ضروری |
| FIND-09 | OS sandbox نیست | Confirmed | SECURITY.md:30 و44–50؛ resolve_path در core/config.py:198؛ permission همان حساب OS | کنترل trust/env/ownership sandbox نیست؛ قرارداد سراسری و P02/P13 |
| FIND-10 | قطعیت context بیش از شواهد | Partially Confirmed | evidence=name_only و ambiguous موجود است؛ context.py:389 complete خطاهای parse را لحاظ نمی‌کند؛ tests/test_code_context.py محدود | ادعای فقدان همه evidence نادرست؛ truth gap مشخص P0 TASK-P01-02 |
| FIND-11 | runtime فاقد loop تصمیم مستقل | Confirmed | main.py؛ core/registry.py؛ core/execution_router.py::build_route_planner؛ tools/workflows/builtins.py::builtin_workflow | advisory و workflow تعریف‌شده planner مستقل نیستند؛ P12 حفظ همین مرز |

### دو ادعای مکمل مستقل

| ID | claim / verdict | شاهد و محل | نتیجه اجرایی |
|---|---|---|---|
| SUP-LSP | process تازه برای هر request: Confirmed برای مسیر _request | registry.py:26 → LspClient.request:309 → Popen:315 → shutdown:378 و kill:402؛ tests/test_lsp_client.py::test_lsp_client_performs_initialize_request_and_shutdown | P02 reuse با cleanup واقعی؛ زمان‌گیری این نوبت NOT_MEASURED |
| SUP-DAG | DAG/PR باز یا غیرفعال: Changed | PR81 merged؛ SCHEMA_VERSION11؛ config default=False؛ process تحلیل UNSET؛ سرور زنده UNVERIFIED | بازنویسی زیرساخت ممنوع؛ P07 فقط acceptance/GO؛ activation امروز صفر |

### شکاف‌های اضافه با شاهد محدود و قابل تکرار

- BUG-CTX-PAGE: offset بر references/imports/tests اعمال نمی‌شود؛ consumed بعد از overwrite callers حساب می‌شود. در read-only AST extraction تابع واقعی با fixture حافظه‌ای سه reference، offsetهای0 و1 هر دو line1 می‌دهند؛ offset3 count0 ولی یک row می‌دهد. TASK-P01-01.
- BUG-CTX-COMPLETE: همان fixture با parse_errors=1 و offset3، complete=true. این بازتولید فقط اجرای تابع استخراج‌شده با stub index است، نه تست end-to-end.
- BUG-CTX-CACHE: ContextIndexCache._entries بدون eviction است؛ کلید signature فقط path/mtime/size؛ PythonMetadataCache هم همین version را استفاده می‌کند. خطر stale برای تغییر هم‌اندازه با حفظ mtime و رشد rootهای زیاد از source است؛ آزمون runtime نشده. TASK-P01-04.
- GAP-LSP-BOUNDARY: Popen قبل از _document_notification، env حذف نشده، cleanup فقط parent.kill، errorها str(provider_payload). سمت caller command arbitrary داده نمی‌شود؛ این‌ها hardening gap قابل اثبات سورس‌اند، نه ادعای exploit اجراشده. TASK-P02-03/P03-01.
- GAP-CATALOG-INIT: ثبت testing/jobs ذخیره‌ساز/scheduler را ایجاد می‌کند؛ audit خواندنی mkdir را منع کرد. TASK-P05-01، بدون ادعای اینکه صرف list_tools به‌تنهایی scheduler تازه می‌سازد.
- GAP-EDIT-RECOVERY: apply_workspace_edit قفل، مقایسه before و rollback در exception دارد؛ process crash وسط چند فایل معادل transaction اتمیک disk نیست. TASK-P03-03 باید از journal موجود استفاده کند و failure نامطمئن را نگه دارد.

<!-- CHUNK-02-END -->

<a id="evidence-map"></a>
## SOURCE / GRAPH EVIDENCE MAP

| Graph/System | Available / scope | Freshness | رابطه روشن‌شده | تأیید source و محدودیت |
|---|---|---|---|---|
| Codebase Memory | session tool فعال، root همین checkout؛ 5341 node / 28451 edge | index=2026-10-18T14:23:07Z، ready؛ پنج مسیر کلیدی metadata_match | lookup_code_context ← project.registry و scripts.perf_benchmark؛ → build_context_index → ContextIndexCache.get | context.py و registry.py باز شدند؛ metadata_match اثبات محتوایی همه فایل‌ها نیست؛ nested toolها همیشه symbol مستقل نیستند |
| Graphify | query_graph روی graphify-out/graph.json؛ version0.9.92؛ 5877 node /19321 link | built_at_commit=SOURCE_BASE | _request → LspClient و fixtureهای strict/oversize؛ اختلاف نقش runtime و lifecycle | query بار اول truncated بود؛ query بار دوم با LspClient.request و context=call کامل هشت node داشت؛ lsp.py و registry.py تأیید مستقیم |
| Coverage | core/tools/tests/scripts | partial/unusable/skipped=0 در status؛ gaps فهرست cache/excluded dirs | پوشش fileهای critical تأیید best-effort | scope paging کامل انجام نشد؛ ادعای پوشش تمام مخزن نداریم. state و secrets عمداً خارج‌اند |

گراف برای انتخاب مسیر استفاده شد؛ completion هیچ task وابسته به نصب این دو MCP در executor آینده نیست. برای مسیر دقیق، source excerpt و اسم‌های زیر کافی‌اند. هیچ index بازسازی/ویرایش نشد.

| SRC ID | File : symbol : خطوط در SOURCE_BASE | Role و consumers | Test anchor واقعی | Risk / کاربرد |
|---|---|---|---|---|
| SRC-REG | core/registry.py:create_server:63–285 | main → registrars16؛ profile و platform gates؛ discovery/router/health | tests/test_tool_profiles.py؛ tests/test_mcp_integration.py | registration به‌تنهایی schema نمی‌ماند: jobs initialization |
| SRC-CTX | tools/project/context.py:lookup_code_context:272–410 | project.registry.code_context:262 و perf_benchmark._context_lookup_once:726 | tests/test_code_context.py::_write_project | pagination/depth/completeness |
| SRC-CACHE | tools/project/context.py:ContextIndexCache:150–195؛ tools/project/index.py:PythonMetadataCache:243–369 | service.python_metadata → compact AST؛ context و impact مصرف‌کننده | tests/test_code_context.py::test_context_index_refreshes_after_source_changes_and_singleflights_builds | bounded AST cache موجود؛ context cache unbounded |
| SRC-LSP | tools/language/lsp.py:LspClient:209–412 | registry._request → request → Popen → protocol → shutdown | tests/test_lsp_client.py؛ tests/fixtures/strict_lsp_server.py | per-call process، URI بعد spawn |
| SRC-LREG | tools/language/registry.py:_command:19، _request:26، register:30 | هفت common tool، rename_symbol، apply_code_action | tests/test_language_registry.py و test_language_hardening.py | public command ممنوع؛ params آزاد |
| SRC-EDIT | tools/language/edits.py:prepare_workspace_edit:24–92، apply_workspace_edit:95–113 | rename/code action → claim+lock → _write_bytes | tests/test_workspace_edit_regressions.py | hash و alias guards موجود؛ resource operation ممنوع |
| SRC-FS | tools/filesystem/patches.py:apply_patch_transaction:335؛ service.py:_backup_file:198 | MCP apply_patch؛ workflow actions؛ atomic file publication/backup | tests/test_workspace_edit_regressions.py؛ filesystem testهای فهرست‌شده در tests | چند فایل ACID disk نیست؛ recovery و backup حفظ شوند |
| SRC-IMPACT | tools/testing/impact.py:select_affected_tests:175–309 | affected_tests → build_verification_plan → verify_changed_repository | tests/test_affected_tests.py | Python-only و fallback برای زبان دیگر |
| SRC-VERIFY | tools/testing/verification.py:build_verification_plan:22، verify_changed_repository:85 | foreground، background worker و typed workflow action | tests/test_background_verification.py؛ test_parallel_diagnostics.py | accepted/job submitted با final success فرق دارد |
| SRC-LIFE | core/heartbeat.py:lifespan:28؛ core/lifecycle.py:RuntimeLifecycle:22 | extension start/drain، retention، stopping؛ core.registry | tests/test_lifecycle.py | import/list نباید process فعال کند |
| SRC-OWN | core/process_ownership.py:spawn_owned_process:311–369 | executor/extensions؛ OwnedProcess.terminate_tree/close_ownership | tests/test_windows_job_backend.py؛ tests/test_job_worker.py | require_native باید پیش از payload اجرا fail closed کند |
| SRC-REC | core/recovery.py:OperationRecoveryJournal:387؛ core/tooling.py:compact_errors:134–199 | native mutation wrapper، direct call fences، uncertain evidence | tests/test_recovery_journal.py | journal authority تکرار نشود |
| SRC-DAG | core/workflow_models.py:workflow_topological_order:86؛ workflow_store.py:SCHEMA_VERSION:33، claim_ready_operation:2161 | workflow executor/worker، lease/CAS و resource claims | tests/test_workflow_dag.py؛ test_parallel_workflow_scheduler.py | schema11 قدیمی‌ترها را ایمن رد می‌کند؛ no downgrade |
| SRC-RES | core/resource_claims.py:managed_resource_claims:266 و managed_path_claims:351 | filesystem، LSP edits، analyzer و git | tests/test_parallel_workflow_resources.py؛ test_parallel_workflow_recovery.py | token propagation و unknown effects |
| SRC-EXT | core/extensions/bridge.py:call_tool:117؛ config.py:build_extension_environment:141 | validated generation/profile → manager.execute_guarded_call | tests/test_extension_security.py؛ test_extension_registry.py | namespace/profile/zero extension حفظ شود |
| SRC-BROWSER | tools/browser/registry.py:browser_click_semantic:350 و browser_snapshot:261 | manager session/snapshot → semantic target | tests/test_browser_semantic_registry.py و tests/test_browser_semantics.py موجود؛ P10 متکی به fixture offline است | pixel grounding هنوز ندارد؛ source دقیق dispatcher موجود حفظ شود |
| SRC-DESKTOP | tools/desktop/registry.py:desktop_screenshot:128، mouse_click:177؛ native.py:click_mouse:169 | ImageGrab → delivery؛ raw input مستقیم | tests/test_desktop_uia.py | window/DPI snapshot guard برای raw click فعلی وجود ندارد |
| SRC-GIT | tools/git/registry.py:_git_resource_claims:25، git_stage:289، git_commit:304 | local Git structured، مشترک common-dir claim | tests/test_git_tools.py؛ test_git_operations.py | remote publication authority بیرون runtime |
| SRC-MEM | tools/memory/registry.py:memory_read:50، memory_update:84 | JSON project notes با guard؛ intelligence evidence نیست | tests/test_memory_registry.py | حافظه جای canonical source/index نیست |
| SRC-PLANS | tools/workflows/builtins.py:builtin_workflow:18 | implement_and_verify / safe_git_commit / prepare_release | tests/test_builtin_workflows.py | تعریف workflow به معنی model controller نیست |

### Hash guard مسیرهای P0 اولیه

| path | SHA-256 raw bytes هنگام تحقیق |
|---|---|
| tools/project/context.py | c55e72b422b987d1905f9e987025c0639af093bd3b039def40374b3f55dccb15 |
| tools/project/registry.py | b62c9d492cbccdfa95eaa47846513cb68e74648f636b21e9c7ee6d57f607d4f7 |
| tools/project/index.py | ee131ac557f6c5f6fb9afda57ee1cf503e14b11547f68484eb3ff57faa608829 |
| tools/language/lsp.py | 8138c5de15cdcfe513843073446f16def24f0af382d48c2e41a833d0d4d10f9e |
| tools/language/registry.py | 087f67a8b70965beb9b87087cee1eb0637de3f66e43974e62e7a6d27d0ddf783 |
| tools/language/edits.py | 0d3aec9c8b6d57ac52e6ad5b50b4d231b3fedaf3880037f2e9a945a4445c9f42 |
| core/heartbeat.py | 0ab38e14ff1d3d1a34b152ad28974e679a61337e6ebd62d3041abe89a31aa9ca |
| tests/test_code_context.py | a9663c656a1b3030612ba136a4c9a0383172e9e6f7a6cce0aae0b98150364292 |

برای taskهای وابسته، hash بالا عمداً بعد از task قبلی تغییر می‌کند: executor فقط وقتی می‌تواند ادامه دهد که reviewer digest جدید و diff predecessor را در ledger همان task تأیید کرده باشد. حذف SHA guard برای «راحت‌تر اجراشدن» ممنوع؛ ارتقای baseline یک اقدام مشخص strong-review در پایان milestone است.

<a id="architecture"></a>
## ARCHITECTURE & DECISION RECORD

| ADR | تصمیم نهایی این طرح | دلیل / جایگزین ردشده |
|---|---|---|
| ADR-01 | runtime backend باقی می‌ماند؛ planner/controller بیرونی | router advisory، workflow typed و journal authority موجودند؛ engine موازی نمی‌سازیم |
| ADR-02 | ابتدا صحت pagination/confidence، سپس depth؛ public depth0 additive | max_depth=2 قبلاً reserve بوده؛ depth1 و نام ابزار حفظ می‌شود؛ حذف صرف del max_depth کافی نیست |
| ADR-03 | یک LspSessionManager به‌ازای MCP server/runtime generation؛ session owner thread با queue محدود | native tools sync هستند؛ subprocess pipes sync و ownership موجود است. worker-per-request و global process pool با identity نامعلوم رد می‌شود |
| ADR-04 | transport/process logic از LspClient جدا، one-shot API به‌عنوان compatibility adapter باقی | tests و callerهای مستقیم فعلی نباید با manager اجباری شکسته شوند |
| ADR-05 | persistent mode opt-in تا پذیرش؛ provider config فقط local operator، نه public arbitrary command | no automatic download، trust hash برای binary/entrypoint؛ legacy command discovery در حالت قدیم دست‌نخورده |
| ADR-06 | index داخلی Python + LSP evidence محدود؛ اتصال اجباری Graphify/CBM به محصول رد | runtime صفر extension مستقل بماند؛ گراف‌های Agent ابزار تحقیق‌اند؛ protocolهای import و inheritance همه‌جا کامل نیستند |
| ADR-07 | profile جدید ثابت coding_core اختیاری؛ full/coding ثابت؛ dynamic refresh blocked تا client evidence | 32 ابزار minimal موجود است؛ target عدد30–50 بدون accuracy experiment مجوز حذف ابزار نیست |
| ADR-08 | affected-tests چندزبانه selection و runner execution دو مرحله | config/script نامطمئن فقط خوانده می‌شود؛ no tests + incomplete هرگز success نیست |
| ADR-09 | DAG موجود دوباره پیاده نمی‌شود؛ only acceptance و NO-GO preservation | PR81 merged است؛ source infrastructure با production authorization یکسان نیست |
| ADR-10 | output typing تدریجی، ابتدا TypedDict contract داخلی بدون شکستن public schema | union بزرگ Pydantic برای همه151 ابزار catalog را بی‌دلیل بزرگ می‌کند |
| ADR-11 | Visual و delegation future opt-in، هیچ execution_run یا اعتماد از متن provider | policy و mutation guard در typed executor باقی می‌ماند |

| ADR-12 | در P14-06 قرارداد Shared-Host + Exclusive-Workspace فقط برای عملیات با اثر جانبی واقعاً محدود و اثبات‌شده بررسی شود؛ فرمان‌های آزاد همیشه Host-Exclusive | قفل هماهنگی Shared روی coordination:host برای کار مقید، با قفل Exclusive سراسری فرمان آزاد تداخل دارد؛ جداکردن صرف کلیدها غلط است. OS sandbox وجود ندارد و صرفِ cwd، shell=False، allowlist executable یا تایپ‌شدن runner اثبات isolation نیست. |
| ADR-13 | هدایت کم‌هزینه اجرا از طریق Schema کوتاه و Router مشورتی؛ اثر روی انتخاب مدل فقط با آزمون واقعی سنجیده می‌شود | ابزار native/Direct برای کار هم‌ارز ممکن است بهتر باشد؛ مشاهده‌های پنل workload غیرهم‌ارز هستند و مشورت Router الزاماً به ابزار فراخوانی‌شده توسط Agent نمی‌رسد. |
| ADR-14 | Preflight و command cache فقط با شواهد نیاز، بودجه latency/bytes و قرارداد اعتبارسنجی؛ tracing فقط Server-Boundary | نبود hash تکراری در نمونه ۶۹تایی به معنی بی‌فایده بودن دائمی cache نیست. کاتالوگ ۱۵۱تایی و بودجه باقی‌مانده نیازمند سنجش مجدد در زمان اجراست. |

| ADR-15 | Batch و Parallel دو قرارداد جدا هستند: Native Read Batch محدود، با یک ورودی/پاسخ؛ اجرای موازی اختیاری و سقف‌دار در همان مرز typed موجود | Workflow فقط actionهای ثبت‌شده را می‌پذیرد؛ arbitrary native dispatcher، recursion، extension و write-batching تازه اضافه نمی‌شود. نسخهٔ نخست فقط file read؛ سایر انواع خواندنی پس از اندازه‌گیری و بررسی side effect. |

### مقایسه گزینه‌های intelligence

| گزینه | زبان / precision | هزینه، invalidation و ownership | انتخاب |
|---|---|---|---|
| A: internal index + LSP | AST Python؛ TS/JS/PHP از provider؛ dynamic/runtime حقیقت نیست | process RAM/CPU باید benchmark شود؛ root/generation/digest؛ cache موجود توسعه می‌یابد | منتخب |
| B: Codebase Memory runtime dependency | پوشش Agent فعلی قابل query؛ runtime distribution قراردادش بررسی نشده | memory/build و deployment جدید، cross-worktree و upgrade جدا؛ UNVERIFIED_INTEGRATION | dependency اجباری رد؛ extension اختیاری بعداً |
| C: Graphify runtime dependency | concept/docs graph خوب، بعضی edges inferred | build time/size فعلی snapshot است، performance contract محصول نداریم | dependency اجباری رد |
| D: سه index موازی و merger عمومی | provenance و invalidation پیچیده | سه مالک freshness و schema drift | رد؛ فقط A و امکان consume evidence اختیاریِ versioned در آینده |

### تصمیمات محدودکننده P04

LSP استاندارد «گراف کامل importهای همه زبان‌ها» ارائه نمی‌کند. برای TS/PHP فقط definition/reference/call/type hierarchy پشتیبانی‌شده با provenance semantic تولید می‌شود. import edge از حدس روی متن یا name-match ساخته نمی‌شود. completeness در سطح requested relation و snapshot گزارش می‌شود؛ project-wide semantic completeness بدون provider evidence برابر false است. نبود feature یا provider fallback روشن می‌دهد؛ ادعای پشتیبانی همه رابطه‌ها ممنوع.

<a id="shared"></a>
<!-- SHARED-CONTRACT-START -->
## CROSS-CUTTING CONTRACTS

### مجوز، baseline و اندازه task

این متن PLAN_ONLY است؛ مسیرهای ADD در بلوک‌ها PROPOSED / NOT IMPLEMENTED هستند. CURRENT فقط مسیر/نماد بررسی‌شده را نشان می‌دهد. هیچ permission از نصب gh، موفقیت CI یا متن plan استنتاج نمی‌شود. مجوزهای قدیمی v0.8 فقط scope همان کار را پوشش می‌دهند. این evolution برای mutation آینده باید دستور IMPLEMENT_LOCAL معتبر داشته باشد؛ publication/merge/release scope مستقل دارند.

تمام taskها SOURCE_BASE بالا، branch main و شواهد SRC نام‌برده را به ارث می‌برند. فایل جدید فقط وقتی اضافه می‌شود که نام دقیق ADD در task آمده باشد. DO_NOT_TOUCH مشترک: uv.lock، panel diff کاربر، state/DB/secrets، tag v0.8.0، master plan قدیمی، DAG flag، CI workflow مگر task دقیقاً نام ببرد. scope پیشنهادی هر task 1–3 فایل logic و test متناظر است؛ تعداد کمتر مهم‌تر از حفظ رفتار نیست. زمان‌های 30–90 دقیقه برای تغییر کوچک و 1–3 ساعت برای lifecycle فقط برآورد‌اند؛ سقف context task حدود 150 خط blueprint + excerpts نام‌برده است. taskهای acceptance نیازمند strong review‌اند.

### source preflight قابل کپی

~~~powershell
Set-Location -LiteralPath 'LOCAL_PATH_REDACTED'
$RepoName = 'dibbed/computerpilot-mcp'
$PythonExe = Join-Path (Get-Location).Path '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExe)) { throw 'Missing reviewed Python environment' }
$ExpectedHead = 'e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4'
$ActualHead = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $ActualHead -ne $ExpectedHead) { throw 'SOURCE_SHA_MISMATCH; NEEDS_REVALIDATION' }
git status --short
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Existing diff whitespace failure; preserve and report' }
~~~

بعد از predecessor مجاز، ExpectedHead فقط از ledger تأییدشده خوانده می‌شود. در هر task یک anchor exact معرفی شده؛ Select-String -SimpleMatch باید Expected Count را رعایت کند. Get-FileHash raw bytes را با جدول یا ledger مقایسه کن. اگر line drift تنها ناشی از predecessor تصویب‌شده نیست، STOP.

### استاندارد تست آینده

هر دستور pytest فقط در اجرای آینده مجاز است؛ این نوبت اجرا نشده. task ابتدا test پیشنهادی را در همان fixture pattern می‌افزاید، failure لازم را ثبت می‌کند، کوچک‌ترین edit را می‌زند و test را تکرار می‌کند. Fixtureهای tmp_path و monkeypatch/asyncio.run موجود ترجیح دارند. ثبت registry واقعی برای integration لازم است؛ mock صرف helper اثبات denied-before-dispatch عمومی نیست.

~~~powershell
# FUTURE IMPLEMENT_LOCAL ONLY؛ پس از ساخت تست نام‌برده در task:
& $PythonExe -m pytest -o addopts= -q tests/test_code_context.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p01
if ($LASTEXITCODE -ne 0) { throw 'TASK_TEST_FAILED; record and stop' }
& $PythonExe -m ruff check tools/project/context.py tests/test_code_context.py
if ($LASTEXITCODE -ne 0) { throw 'RUFF_FAILED' }
& $PythonExe -m mypy core tools scripts tests
if ($LASTEXITCODE -ne 0) { throw 'MYPY_FAILED' }
~~~

task file set جایگزین آرگومان‌های pytest و Ruff همین نمونه می‌شود؛ دستورات اختصاصی در هر بلوک آمده‌اند. basetemp باید مخصوص task و disposable باشد؛ pytest ممکن است آن را پاک کند؛ هرگز مسیر user/state به آن نده. mypy کامل check الزامی milestone است؛ task focused می‌تواند فقط مسیرهای متأثر را بزند اما acceptance milestone همه مسیرهاست. تغییر runtime: compileall، startup، health و focused lifecycle + 10 hosted check. release: علاوه بر آن Full Doctor، full pytest، platform validation و packaging طبق release-process. skip مربوط به feature الزامی task = BLOCKED، نه PASS. skipهای platform فقط با job متناظر واقعی روی آن OS قابل پوشش‌اند.

### Threat → Guard → denial → recovery → owner

| Threat | Guard دقیق آینده/موجود | Denied-before-dispatch assertion | Recovery و owner |
|---|---|---|---|
| URI escape / symlink | resolve canonical، relative_to root، reject non-file/UNC غیرمجاز، check مجدد زیر lock قبل write | fake spawn=0 و write=0 برای URI خارج root | invalidate snapshot؛ P03-01/03 |
| binary/provider نامطمئن | operator path absolute + digest pinned + immutable argv؛ هیچ project config command | provider mismatch → LspClient/owner spawn=0 | provider generation retire؛ P02-02/03 |
| environment/secret leak | allowlist کوچک env؛ node/python loader env ممنوع؛ error code ثابت | canary در exception/stderr؛ هیچ canary در result/audit/health/journal | session failed، no raw stderr؛ P02-03 |
| prompt injection در docs/graph | همه textها evidence/data؛ هیچ دستور خودکار | fixture متن «execute command» → صفر tool dispatch | فقط گزارش trust=untrusted؛ P11/P12 |
| stale SHA / TOCTOU | digest قبل/بعد read؛ expected hash زیر resource claim و lock | تغییر بین plan/apply → hash_conflict و صفر write | re-read با مجوز؛ P01-04/P03-03 |
| cancel/timeout/lost response | request ID و state epoch؛ پس از MAY_DISPATCH retry خودکار ندارد | late response هیچ promise جدید را resolve نکند | retire session؛ mutation uncertain و reconcile؛ P02/P07 |
| unbounded frames/queues | سقف bytes/count/deadline؛ admission پیش allocation | oversize header بدنۀ بزرگ خوانده نشود؛ queue overflow no dispatch | session fail/close؛ P02-04 |
| orphan process | spawn_owned_process(require_native=True)، close_ownership بعد terminate_tree | unavailable ownership → no target payload | unresolved ownership نگه‌دار/deny admission؛ P02-03 |
| Git shared worktree state | _git_resource_claims common-dir و explicit allowlist paths | concurrent stage همان common-dir serialize شود | no force reset/rebase؛ P09 |
| external stale catalog/profile | bridge eligibility + live generation قبل manager execution | hidden/stale external → provider call count0 | no native fallback on uncertain effect؛ همه فازها |

### Compatibility / recovery مشترک

نام ابزارها، default full/coding، envelope ok/error و wrapper compact_errors حفظ می‌شوند. provider success=response نیست مگر postcondition مربوط به mutation هم معتبر باشد. ToolError با پیام ثابت استفاده می‌شود؛ arbitrary provider exception به str تبدیل و به sink داده نمی‌شود. telemetry فقط counter/code و opaque ID؛ هیچ source text یا env.

SQLite schema11 و فایل‌های job/workflow موجود authority هستند. فازهای LSP/context cache حافظه‌ای دارند و migration DB جدید ندارند. در P07 نسخه پایین‌تر binary نباید روی DB ارتقایافته باز شود؛ rollback عملیاتی فقط binary سازگار + backup مستقل با توقف writers، نه PRAGMA کم‌کردن version یا حذف evidence. process kill به‌معنی rollback نیست. failed multi-file write با rollback ناموفق باید uncertain بماند.

### Evidence registration مشترک — EVIDENCE-1

پس از هر task آینده، یک entry انتهای ledger: Task ID، زمان با timezone، HEAD کامل + digest diff، مجوز scope، changed files، exact commands و exit codes، passed/failed/skipped، reviewer، CI run/head/tested ref، blocker/error، next task و digest جدید فایل‌ها. بعد از ثبت و فقط با پذیرش gate، یک سطر state با Exact Match/Count1 عوض شود. implementation=true بدون acceptance → IMPLEMENTED_UNVERIFIED. فاز از state taskها مشتق می‌شود؛ بدون تکرار status در body.

هر بلوک زیر همین SOURCE_BASE، preflight، DO_NOT_TOUCH، EVIDENCE-1 و stop مشترک را به ارث می‌برد؛ تکرارنشدن این فیلدها حذف الزام نیست.
<!-- SHARED-CONTRACT-END -->

<!-- CHUNK-03-END -->

<a id="phase-p00"></a>
<!-- PHASE-P00-START -->
## P00 — baseline و شواهد خواندنی؛ M00

Scope: snapshot/CI/graph/catalog؛ Non-goals: اصلاح محصول یا بازاجرای suite. ورودی SOURCE_BASE، خروجی evidence همین سند. rollback محصول ندارد چون mutation محصول ندارد. gate: SHAهای معلوم، یافته‌ها source-grounded، محدودیت‌ها صریح. نقشه فاز پیشنهادی V3 به P00..P13 بدون renumber حفظ شده؛ طرح DAG شش‌فازی قدیمی فقط به acceptance P07 نگاشت می‌شود.

<a id="task-p00-01"></a>
<!-- TASK-P00-01-START -->
### TASK-P00-01 — ثبت baseline و گیت‌های همان SHA
EXECUTOR_CLASS: WEAK_READY؛ Priority: Gate؛ Source/Depends/State: shared contract و سطر Index.
**1. Current / gap:** AGENTS و source snapshot بالا CURRENT هستند؛ historical CI مجوز green جدید نبود.
**2. Change map:** KEEP همه product files. OUTPUT فقط snapshot و CI identity در همین plan؛ API جدید ندارد. ترتیب: status → HEAD → remote API main → PR81 → jobs سه run.
**3. Commands / assertions:** git rev-parse HEAD؛ git status --short؛ gh api repos/dibbed/computerpilot-mcp/commits/main --jq .sha؛ gh pr view 81 --repo dibbed/computerpilot-mcp --json state,headRefOid,mergeCommit؛ gh run view 39131440536 --repo dibbed/computerpilot-mcp --json headSha,jobs. Expect=SOURCE_BASE و 6+3+1 success.
**4. Gate:** snapshot تا drift معتبر؛ شواهد audit این نوبت PASS، runtime تازه اجرا نشده.
**5. Exception:** گیت publish job success را release جدید تفسیر نکن؛ publish=false.
**6. Order / stop:** شروع از source preflight؛ remote unavailable فقط hosted-dependent task را BLOCKED کند.
**7. Evidence:** SRC-GIT/CI-MAIN؛ ledger BUILD-01؛ VERIFIED فقط خواندن.
<!-- TASK-P00-01-END -->

<a id="task-p00-02"></a>
<!-- TASK-P00-02-START -->
### TASK-P00-02 — اندازه‌گیری امن و بازتولید خواندنی
EXECUTOR_CLASS: WEAK_READY؛ Priority: Gate.
**1. Current / gap:** create_server:63 و JobStore creation مانع اجرای واقعی read-only هستند؛ signature CURRENT create_server() -> MCPServer.
**2. Change map:** KEEP سورس؛ schema-only process با no-op storage/scheduler و audit hooks مطابق snapshot؛ استخراج AST تابع lookup و index fake فقط حافظه‌ای. PROPOSED mutation ندارد.
**3. Assertions:** full151/coding96/minimal32 و byteهای snapshot؛ offset0/1 reference مشابه و offset3 count0 با row موجود؛ parse_errors1 با complete=true بازتولید شد. هیچ process provider/tool dispatch نشد.
**4. Gate:** PASS برای source/schema research؛ startup/runtime validation مستقل و UNVERIFIED.
**5. Exception:** این harness تست product یا baseline latency نیست؛ موفقیت آن به suite منتقل نمی‌شود.
**6. Order / stop:** هر audit denial را تحلیل کن؛ محدودیت را حذف نکن؛ تنها dependency side effect را پس از source review در حافظه stub کن. تکرار بدون دلیل ممنوع.
**7. Evidence:** SRC-CTX، GAP-CATALOG-INIT، شمارش واقعی بالا؛ ledger BUILD-02.
<!-- TASK-P00-02-END -->
<!-- PHASE-P00-END -->

<a id="phase-p01"></a>
<!-- PHASE-P01-START -->
## P01 — صحت code_context؛ M01a و M01b

Scope: truth، pagination، depth و freshness. Non-goals: LSP یا graph خارجی، inference runtime، database جدید. Files: context.py، project registry/index/service و tests/test_code_context.py و tests/test_project_cache.py. M01a=01,02؛ M01b=03,04. Non-regression: نام code_context، default depth1، categoryهای قدیمی، source cap و extension-independent behavior. rollback: revert focused patch با حفظ cache disposable؛ data migration ندارد. Exit: depth/page/cache fixtures + full relevant tests + typing/CI؛ benchmark B-CTX در انتهای سند. ترتیب P01 مستقل از ساخت fixture P02 است، اما هم‌زمان نویسنده plan نداریم.

CURRENT exact signature در tools/project/context.py:272:
~~~text
def lookup_code_context(
    root: Path, symbol: str, *, match: Literal["exact", "contains"] = "exact",
    file_filter: str | None = None, include_source: bool = True,
    include_callers: bool = True, include_callees: bool = True,
    include_references: bool = True, include_imports: bool = True,
    include_tests: bool = True, max_depth: int = 1, max_files: int = 5_000,
    max_relationships: int = 100, source_max_chars: int = 20_000, offset: int = 0,
) -> dict[str, Any]:
~~~
PROPOSED همان signature باقی می‌ماند؛ registry constraint max_depth از ge1 به ge0 تغییر additive است. خروجی additive: query_depth:int، traversal_truncated:bool، total_exact:bool، confidence_summary:dict[str,int]، incomplete_reasons:list[str]، snapshot_id:str، freshness:str. فقط فیلدهایی که task مربوط تولید می‌کند همان مرحله اضافه شوند؛ field جعلی قبل از implementation ممنوع.

<a id="task-p01-01"></a>
<!-- TASK-P01-01-START -->
### TASK-P01-01 — اصلاح صفحه‌بندی سراسری روابط
EXECUTOR_CLASS: WEAK_READY؛ Priority:P0؛ Scope: context.py و test_code_context.py؛ complexity:small، risk:medium؛ estimate:30–60min.
**1. Verified behavior / preflight:** SRC-CTX. source anchor دقیق "relationships = [*callers, *callees, *references, *imports, *tests]" یک match. consumers project.registry و perf benchmark؛ امضای lookup عوض نمی‌شود. reproduction AST در snapshot discrepancy را ثابت کرد. SHA/hash shared باید پاس شود.
~~~powershell
$Anchor = 'relationships = [*callers, *callees, *references, *imports, *tests]'
if (@(Select-String tools/project/context.py -SimpleMatch $Anchor).Count -ne 1) { throw 'ANCHOR_MISMATCH' }
(Get-FileHash tools/project/context.py -Algorithm SHA256).Hash.ToLowerInvariant()
~~~
**2. Executable change / PROPOSED:**
1. ADD testهای زیر در tests/test_code_context.py؛ reuse _write_project.
2. EDIT lookup_code_context از anchor relationships تا قبل complete؛ helper take و offset math حذف شود.
3. KEEP collection و definition/source logic در این task.
~~~python
# PROPOSED replacement of pagination region only
categories = ("callers", "callees", "references", "imports", "tests")
groups = (callers, callees, references, imports, tests)
tagged = [(category, row) for category, rows in zip(categories, groups, strict=True) for row in rows]
page = tagged[offset:offset + max_relationships]
paged = {category: [] for category in categories}
for category, row in page:
    paged[category].append(row)
callers, callees, references, imports, tests = (paged[category] for category in categories)
relationships_truncated = offset + len(page) < len(tagged)
# return count=len(page), total=len(tagged); keep offset/limit values
~~~
complete فعلاً همان scan/page criterion می‌ماند تا task02 تغییر رفتاری جدا باشد. variable relationships در return با len(tagged) جایگزین و window_end/budget/take حذف شوند. imports موردنیاز جدید ندارد.
**3. Copy-ready checks / named assertions:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_code_context.py -k 'paginat' -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0101
& $PythonExe -m ruff check tools/project/context.py tests/test_code_context.py
~~~
PROPOSED test_lookup_code_context_paginates_every_category: _write_project، baseline max_relationships1000؛ flatten با order پنج category؛ برای هر offset0..total+1 و limit1,2,3 assert flatten(page)==baseline[offset:offset+limit]، count==sum lengths، total ثابت، offset>total همه arrays خالی. test_lookup_code_context_pagination_has_no_duplicate_or_missing_rows: concatenated limit1 pages دقیقاً baseline؛ include_source=False تا مقایسه deterministic. EXISTING test_lookup_code_context_paginates_relationships_across_categories حفظ و broaden شود؛ test جدید existing passed معرفی نشود.
**4. Binary gate:** exact slicing برای همه categoryها PASS، schema/default unchanged، focused checks exit0؛ test name بدون match/zero collected FAIL.
**5. Exceptions:** هیچ permission/persistence جدید ندارد؛ pagination global است، نه per-category.
**6. Order / stop:** preflight → tests RED → region patch → tests GREEN → diff review. failure بیرون pagination STOP؛ task02 در همین patch ادغام نشود.
**7. Evidence:** EVIDENCE-1؛ ثبت before/after offset3 و digest context/test؛ next TASK-P01-02 فقط پس از reviewer baseline advancement.
<!-- TASK-P01-01-END -->

<a id="task-p01-02"></a>
<!-- TASK-P01-02-START -->
### TASK-P01-02 — صداقت completeness و confidence
EXECUTOR_CLASS: WEAK_READY؛ Priority:P0؛ Scope:context.py و test_code_context.py؛ risk:medium.
**1. Current:** SRC-CTX، anchor "complete = not index.scan_truncated and not relationships_truncated" یک match بعد01. _resolve_call→Evidence یکی از exact_qualified/import_bound/same_module/name_only؛ referenceها name_only؛ ambiguity در definitions موجود است. tests اکنون فقط دو دسته پایه را می‌سنجند.
**2. PROPOSED edits:**
1. ADD _confidence(evidence: Evidence) -> Literal["static_resolved","inferred"] در context.py؛ سه evidence اول static_resolved و name_only inferred؛ هیچ کدام RUNTIME_CERTAIN نیستند.
2. هر row evidenceدار confidence و source_kind="python_ast" بگیرد؛ imports evidence="import_bound" فقط binding syntax و confidence="static_resolved" بدون ادعای وجود target.
3. tests extraction از شرط include_callers جدا شود: target selected AND include_tests AND is_test → test row، حتی include_callers=False.
4. reasons از scan_truncated, relationships_truncated, bool(index.parse_errors), any source_truncated بساز؛ complete=not reasons. ambiguous نتیجه match جداست؛ به‌خودی‌خود incomplete نیست، اما inference باعث semantic_complete=False می‌شود. ADD semantic_complete:bool=false برای AST-only در این مرحله.
5. confidence_summary تعداد rowهای **همین صفحه** را بر اساس confidence بشمارد؛ نام confidence جدید نباید evidence قدیم را تغییر دهد.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_code_context.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0102
~~~
PROPOSED test_context_parse_errors_prevent_complete: یک valid و یک syntax-invalid file؛ parse_errors nonempty، complete false و reason parse_errors. test_context_name_only_is_inferred: تعریف foo تنها در a و unresolved call در b بدون import؛ evidence name_only و confidence inferred. test_context_tests_do_not_depend_on_caller_visibility: fixture _write_project با include_callers=False/include_tests=True؛ callers خالی، tests شامل tests/test_service.py. test_context_source_limit_reports_incomplete: source_max_chars=0 و include_source=True؛ reason source_truncated. imports-only row evidence assertion.
**4. Gate:** همه reasons در failureها هستند؛ categoryها و evidenceهای قدیمی حفظ؛ هیچ high-confidence runtime claim. PASS فقط tests+Ruff و review diff.
**5. Exceptions:** complete معنای «در bounds پاسخ کامل» دارد؛ semantic_complete جدا مانع تفسیر runtime می‌شود. تغییر complete برای parse error اصلاح truth contract است و در changelog آینده شرح داده شود.
**6. Order:** 01 verified → fixtureها → helper row metadata → completeness → test. failed expected evidence STOP، heuristic تازه اختراع نکن.
**7. Evidence:** EVIDENCE-1؛ next03؛ ثبت compatibility result و source digest.
<!-- TASK-P01-02-END -->

<a id="task-p01-03"></a>
<!-- TASK-P01-03-START -->
### TASK-P01-03 — depth صفر/یک/دو، بدون cycle و انفجار fan-out
EXECUTOR_CLASS: WEAK_WITH_REVIEW؛ Priority:P0؛ Scope:context.py، project/registry.py، test_code_context.py؛ risk:medium-high.
**1. Current:** del max_depth در context.py یک match؛ current registry max_depth Annotated[int,Field(ge=1,le=2)]=1. comment می‌گوید depth2 رزرو است؛ انتخاب معنای جدید در ADR-02 انجام شده. source signature P01 مرجع است.
**2. PROPOSED exact contract:**
- max_depth type int و 0..2 در helper هم validate شود؛ bool رد. Public default1 ثابت، ge0/le2.
- depth0: فقط definitions/source؛ هیچ category relationship؛ total0، complete به parse/scan/source مربوط.
- identity=(file,qualified_name,line,kind). adjacency یک‌بار از file.metadata.calls با _resolve_call و _enclosing_definition ساخته شود. edge دارای source identity یا module sentinel، target identity، call file/line/name، evidence.
- مسیر BFS incoming و outgoing جدا، deterministic sorted tuple؛ visited بر (direction,identity) با minimum distance. initial selected در depth0؛ برای هر vertex با d<max_depth، edgeهای direction مجاز را اضافه کن؛ hop=d+1؛ cycle دوباره enqueue نشود. incoming فقط caller expansion و outgoing فقط callee expansion؛ مسیر zig-zag مصنوعی ساخته نشود.
- include_callers/callees فقط delivery و expansion همان جهت را کنترل کنند؛ related tests برای incoming مستقل محاسبه شوند. references/imports فقط برای selected اولیه و depth>=1، tests برای incoming traversal تا depth انتخابی. rowهای قبلی حفظ؛ depth و via_qualified_name additive.
- کران hard داخلی: 1000 vertex، 10000 scanned call edge، 1s traversal پس از ساخت index. قبل هر batch64 بررسی monotonic. overflow → traversal_truncated=true، reason متناسب، total_exact=false؛ total lower bound تعداد retained edgeهاست. pagination بعد از stable collection انجام شود.
- source/definitions فقط initial matchها؛ اگر بیش از1000 selected بود cap و incomplete. name_only edge خروجی inferred است ولی برای hop دوم traverse نمی‌شود تا زنجیره حدس گسترش نیابد.
- حذف del max_depth فقط با این جایگزین؛ time و collections.deque import؛ بدون library جدید.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_code_context.py tests/test_tool_profiles.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0103
~~~
PROPOSED test_context_depth_zero_one_two: a.start→b.mid→c.leaf؛ depth0 روابط0، depth1 mid فقط، depth2 mid+leaf؛ caller reverse نیز. test_context_depth_cycle_is_finite: a↔b، تعداد یکتا و bounded. test_context_inferred_edge_is_not_transitively_expanded؛ test_context_traversal_budget_reports_lower_bound با monkeypatch monotonic sequence و10001 calls؛ assert complete=false,total_exact=false. test_context_depth_pagination_is_deterministic: صفحات با ترتیب sorting ثابت. integration schema test با MCP Client برای depth0 valid و depth3/bool invalid قبل helper. deep package alias fixture قبلی حفظ.
**4. Gate:** depth1 baseline category content بدون اضافه‌شدن unrelated vertex؛ depth2 نتیجه متفاوت و صحیح؛ bounds deterministic؛ all tests exit0 و strong review algorithm.
**5. Exceptions:** traversal limit تضمین زمان کل scan نیست؛ budget scan در04 مستقل است. total_exact=false هرگز count کل graph معرفی نشود.
**6. Order:** tests → adjacency helper خصوصی در همان module → traversal → registry ge0 → tests. اگر 1s با CI load فلکی شد clock را mock کن؛ threshold را سست نکن.
**7. Evidence:** EVIDENCE-1؛ latency اولیه فقط B-CTX؛ next04.
<!-- TASK-P01-03-END -->

<a id="task-p01-04"></a>
<!-- TASK-P01-04-START -->
### TASK-P01-04 — freshness مبتنی بر محتوا و cache محدود
EXECUTOR_CLASS: WEAK_WITH_REVIEW؛ Priority:P0؛ Scope:index.py/context.py/service.py و tests/test_project_cache.py و tests/test_code_context.py؛ risk:high؛ estimate:1–2h؛ تغییر واحد دو لایه cache باید یکجا پذیرفته شود.
**1. Current:** FileVersion(mtime_ns:int,size:int) در index.py:26 و constructor داخلی _version:275؛ ContextIndexCache._entries dict[(canonical_root,max_files),entry]، _file_signature فقط stat. _load_stable موجود قبل/بعد stat و invalidation generation را بررسی می‌کند. source anchorها کلاس‌ها و def _file_signature هر کدام1.
**2. PROPOSED file sequence:**
1. index.py: ADD content_sha256:str به FileVersion، در _version(path) digest streaming64KiB تا cap2MiB؛ stat قبل/بعد؛ تغییر → ToolError("project_file_changed_during_parse", پیام ثابت). فایل بزرگ → ToolError("project_file_too_large", پیام ثابت)، نه read کامل. _load_stable retry موجود حفظ، digest قبل/بعد مقایسه شود. build_python_metadata نیز حداکثر2MiB read کند؛ parse_error بزرگ‌بودن در result بیان شود. جلوگیری از publication با generation قدیمی حفظ.
2. context.py: signature شامل (canonical path,mtime_ns,size,sha256). ContextIndexCache.__init__(*,max_entries:int=16,max_bytes:int=64*1024*1024) با validation مثبت؛ OrderedDict و size accounting با helper _deep_size **موجود در index.py**، بدون serializer جدید. eviction oldest inactive entry تا هر دو cap؛ oversized entry بدون cache بازگردد. entries bytes/pending build بدون سقف نماند: حداکثر16 active build؛ اضافه → context_capacity_exceeded قبل build.
3. context build deadline5s پس از discovery شروع و قبل هر metadata read بررسی؛ timeout جواب ناقص با reason index_deadline یا ToolError ثابت اگر snapshot معتبر ندارد؛ هیچ partial index به‌عنوان complete cache نشود. signature قبل/بعد build و پیش source excerpt مقایسه شود؛ حداکثر2 تلاش؛ سپس context_stale و complete=false یا structured failure بدون rows مخلوط.
4. service.py::iter_files: directories و names را پیش cap مرتب کن؛ policy excluded dirs موجود حفظ؛ symlink directories دنبال نشوند. root canonical identity و path case semantics موجود canonical_path حفظ؛ symlink file خارج root حذف با reason در context (نه ارسال source بیرونی).
5. context result snapshot_id=SHA256 از canonical root + length-framed signature + max_files؛ freshness="current" فقط پس از recheck، وگرنه stale. cache هیچ DB/file جدید نمی‌سازد.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_project_cache.py tests/test_code_context.py tests/test_filesystem_registry.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0104
~~~
PROPOSED test_context_same_size_same_mtime_change_invalidates_both_caches: bytes foo→bar هم‌طول، os.utime(ns=old)، نتیجه جدید و snapshot متفاوت. test_context_delete_rename_invalidates؛ test_context_build_cannot_publish_after_invalidation با threading.Event (نه sleep race). test_context_lru_count_and_byte_limits با small caps و fixtures بدون private count-only assertions؛ repeat evicted root موجب rebuild و memory stats bounded. test_context_source_snapshot_change_never_returns_mixed_rows؛ test_project_large_file_is_bounded با file>2MiB. EXISTING test_context_index_refreshes_after_source_changes_and_singleflights_builds باید سالم بماند.
**4. Gate:** no stale reuse روی preserved mtime، bounded entry+bytes+builds، no source outside root، valid2MiB حد روشن؛ perf budget B-CTX و strong review دو cache. SHA256 hash cost measured بعداً، speed claim اکنون ندارد.
**5. Exceptions:** حد2MiB تغییر مستند رفتار برای فایل‌های بزرگ است؛ reject/incomplete روشن، نه skip ساکت. cap در config جدید public عرضه نشود تا consumer واقعی.
**6. Order:**01–03 verified → index version/read bounds → context lifecycle/budgets → deterministic scan → tests. failure cache consumers خارج file map: STOP و planner impact revalidation؛ آزادانه API عوض نکن.
**7. Evidence:** EVIDENCE-1؛ M01b acceptance و digest جدید برای P02-06/P04.
<!-- TASK-P01-04-END -->
<!-- PHASE-P01-END -->

<!-- CHUNK-04-END -->

<a id="phase-p02"></a>
<!-- PHASE-P02-START -->
## P02 — نشست LSP ماندگار و چندزبانه

M02a=01..04؛ M02b=05..07؛ M02c=08. Scope: provider config، framed transport، ownership و reuse. Non-goals: دانلود server، shell launcher، callbacks عامل خودمختار، اجرای workspace/executeCommand، DB جدید. prerequisite: P00؛ ورود production فقط بعد P03 validation و M02c. File map CURRENT: lsp.py، registry.py، core/config.py، heartbeat.py، process_ownership.py؛ ADD زیر tools/language فقط providers.py/transport.py/sessions.py و fixture persistent_lsp_server.py. No public tool name برای manager. Config flag پیشنهادی MCP_LSP_PERSISTENT_ENABLED default=False و MCP_LSP_PROVIDERS_CONFIG مسیر absolute؛ direct one-shot API حفظ می‌شود.

### قراردادهای PROPOSED / NOT IMPLEMENTED فاز

~~~python
# tools/language/providers.py
@dataclass(frozen=True)
class LspProvider:
    provider_id: Literal["pyright", "typescript", "phpactor"]
    languages: tuple[str, ...]
    executable: Path
    executable_sha256: str
    entrypoint: Path | None
    entrypoint_sha256: str | None
    declared_version: str
    trusted_roots: tuple[Path, ...]

@dataclass(frozen=True)
class SessionKey:
    workspace_root: str
    worktree_marker_digest: str
    language_id: str
    provider_id: str
    config_digest: str
    runtime_generation: str

~~~

API پیشنهادی LspSessionManager در tools/language/sessions.py:

| Method signature | قرارداد |
|---|---|
| __init__(self, providers:tuple[LspProvider,...], *, generation:str) -> None | فقط state حافظه‌ای؛ بدون thread/process |
| async arequest(self, root:Path, language_id:str, method:str, params:dict[str,object], *, timeout_sec:float) -> object | admission/queue/owner/Future طبق task05 |
| close_workspace(self, root:Path, *, timeout_sec:float=2.0) -> None | drain فقط keyهای root؛ task05 |
| close_all(self, *, timeout_sec:float=5.0) -> None | drain سراسری در task07 |
| snapshot(self) -> dict[str,int] | شمارنده‌های محدود بدون IO یا spawn |

این جدول inventory امضاست؛ body هر method در task05/07 مشخص است. هیچ placeholder runtime ساخته نشود.

حدود طراحی اولیه (قابل بررسی benchmark؛ اعداد تضمین عملکرد نیستند):4 session، 1 in-flight/session، 8 queued/session، 32 queued کل، 16 document/session و16MiB متن، هر document2MiB؛ frame4MB و هر request8MB، header64KiB، stderr64KiB private و هرگز public؛256 notification/request؛64MiB wire/session سپس retire در مرز idle؛ idle120s؛ حد request300s؛ cleanup grace2s؛ مجموع close5s؛ 2 restart admission/60s/key با backoff5s. Queue و deadline پیش از dispatch چک می‌شوند. پس از dispatch هیچ replay خودکار نیست.

CPU/RAM/handles hard sandbox نیستند: در owner loop هر0.5s و پس از request، psutil installed جمع process tree متعلق به owner را sample کند؛ RSS>512MiB/session یا2GiB کل، CPU consumed>30s/request، handles/FD>512/session → fail session و terminate owned tree؛ اگر measurement unavailable capability unknown، persistent activation NO-GO مگر platform review آستانه مشاهده‌پذیر دیگری تعیین کرده باشد. این پایش soft است؛ claim OS resource isolation ممنوع. metricهای spawn_count/reuse_count/active/failed/quarantined/queue_depth/restart_count محدود و بدون root/secret label هستند.

State table: NEW→STARTING→READY→DRAINING→CLOSED؛ STARTING/READY→FAILED؛ cleanup نامطمئن→QUARANTINED؛ QUARANTINED ظرفیت را نگه می‌دارد و admission همان key ممنوع. پاسخ دیررس با epoch قبلی drop می‌شود. FAILED فقط بعد proven termination به CLOSED می‌رود؛ request بعدی پس از backoff می‌تواند generation جدید بسازد.

مسیر خروج سالم: didClose سندهای باز → shutdown(id جدید) → response → exit → wait bounded → terminate_tree در صورت نیاز → join reader → close pipes → close_ownership. در protocol error shutdown handshake الزامی نیست؛ terminate_tree واجب است. close_all فقط بعد RUNTIME_LIFECYCLE.mark_stopping admission را می‌بندد.

منابع استاندارد بررسی‌شده: [initialize](https://raw.githubusercontent.com/microsoft/language-server-protocol/gh-pages/_specifications/lsp/3.17/general/initialize.md)، [didChange](https://raw.githubusercontent.com/microsoft/language-server-protocol/gh-pages/_specifications/lsp/3.17/textDocument/didChange.md)، [LSP3.17](https://raw.githubusercontent.com/microsoft/language-server-protocol/gh-pages/_specifications/lsp/3.17/specification.md). initialize یک بار قبل درخواست‌ها و initialized بعد پاسخ؛ version سند افزایشی است. جزئیات limits و ownership تصمیم طراحی این پروژه‌اند، نه الزام این منابع.

<a id="task-p02-01"></a>
<!-- TASK-P02-01-START -->
### TASK-P02-01 — fixture ماندگار قبل از manager
EXECUTOR_CLASS: WEAK_READY؛ Priority:P0؛ Scope:ADD tests/fixtures/persistent_lsp_server.py و tests/test_lsp_sessions.py؛ risk:low.
**1. Current:** tests/fixtures/fake_lsp_server.py و strict_lsp_server.py، tests/test_lsp_client.py::test_lsp_client_follows_strict_handshake_and_opens_text_document موجودند. LspClient.__init__(command:list[str],workspace:Path,*,timeout_sec:float=30,max_message_bytes:int=4_000_000,max_total_bytes:int=8_000_000)->None و request(self,method:str,params:dict[str,Any])->Any.
**2. PROPOSED fixture:** همان stdio Content-Length parser fixture strict را reuse/copy کوچک با attribution internal؛ loop تا exit. input test-only argv --mode با enum normal/slow/malformed/crash/oversize/hang-shutdown/flood/reverse-replies. state initialized bool، opens dict[uri,(version,text)]، request_count:int؛ initialize cap textDocumentSync=1/openClose=true، hoverProvider و definitionProvider و referencesProvider، callHierarchyProvider=true. method test/inspect پاسخ PID، request_count، open_count، doc version و فقط fixture content digest. command callbacks test-only mode sends workspace/applyEdit؛ production باید deny کند. stdout فقط frames؛ stderr canary در mode ویژه؛ فایل log در tmp_path فقط با آرگومان test، نه default. response id عین id request. duplicate initialize خطای -32600؛ didChange قبل didOpen و version غیرصعودی -32602 در inspect state. shutdown reply و exit bounded.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_lsp_sessions.py -k fixture -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0201
~~~
PROPOSED test_fixture_reuses_process_for_two_requests: stdlib subprocess fixture در tmp_path، initialize/initialized و2 query؛ PID ثابت، requests2. test_fixture_enforces_document_versions؛ test_fixture_shutdown_exits؛ fixture process در finally با existing process ownership تست پاک شود. no real provider/network.
**4. Gate:** fixture harness deterministic و no descendant alive؛ test بدون production import registry. PASS فقط این artifact؛ manager implemented محسوب نمی‌شود.
**5. Exceptions:** test/inspect هرگز در production method allowlist نیست.
**6. Order:**preflight موجود fixtures → ADD fixture → ADD harness → run؛ parsing failure فقط fixture protocol بررسی شود.
**7. Evidence:** EVIDENCE-1؛ next02.
<!-- TASK-P02-01-END -->

<a id="task-p02-02"></a>
<!-- TASK-P02-02-START -->
### TASK-P02-02 — provider، اعتماد و identity
EXECUTOR_CLASS: WEAK_WITH_REVIEW؛ Priority:P0؛ Scope:ADD tools/language/providers.py، EDIT core/config.py، ADD tests/test_lsp_providers.py؛ risk:high.
**1. Current:** registry._command()->list[str] فقط shutil.which("pyright-langserver") و --stdio است؛ root resolve_path full-access. anchor هر دو1. بدون نسخه config/runtime identity.
**2. PROPOSED contract/steps:**
1. Settings دو فیلد persistent_enabled:bool=False و providers_config:str|None=None؛ env keys بالا. هیچ load/config discovery هنگام import؛ فقط scalar read.
2. providers.py::load_lsp_providers(path:Path)->tuple[LspProvider,...]؛ UTF8≤64KiB، max depth8، duplicate keys/unknown field منع؛ schema_version literal1، providers list1..3؛ هیچ credential value/ref در این config لازم نیست. همه fields dataclass فاز required جز entrypoint و digest آن که باهم null یا باهم مقدارند؛ languages برای pyright=("python",)،typescript=("javascript","javascriptreact","typescript","typescriptreact")،phpactor=("php",) و subset ثابت پذیرفته؛ trusted_roots1..32 absolute، provider_id unique؛ declared_version nonempty≤64؛ digest lowercase hex64.
3. command_for(provider)->tuple[str,...]: entrypoint موجود برای node-based pyright/typescript، command=(absolute executable,absolute entrypoint,"--stdio")؛ phpactor=(absolute php executable,absolute entrypoint,"language-server")؛ native standalone pyright با entrypoint=None=(executable,"--stdio"). .cmd/.bat/.ps1 و shell=True مطلقاً ممنوع؛ Windows node.exe + JS entrypoint به‌جای npm shim.
4. validate_provider(root,language)->LspProvider: exact one eligible provider، root canonical زیر trusted_root و no symlink escape؛ before spawn فایل regular و hash binary/entrypoint recheck؛ mismatch lsp_provider_changed؛ نبود language lsp_provider_unavailable؛ config invalid پیام ثابت بدون محتوای config.
5. make_session_key(root,language,provider,generation)->SessionKey؛ root canonical؛ .git file≤4KiB فقط محتوا digest، .git directory canonical name + stat identity؛ root خودش worktree را جدا می‌کند. نبود Git "no-git". config_digest=canonical JSON غیرمحرمانه provider + binary digests؛ generation opaque uuid runtime. HEAD داخل key نیست: تغییر HEAD با document digest و config retirement مدیریت می‌شود.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_lsp_providers.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0202
~~~
PROPOSED test_provider_mapping_is_exact_and_does_not_spawn؛ test_duplicate_unknown_or_oversize_config_rejected؛ test_windows_batch_launcher_denied؛ test_provider_digest_change_denied_before_spawn؛ test_worktrees_same_common_dir_have_distinct_keys؛ test_root_symlink_escape_denied؛ Unicode/case paths مطابق canonical_path موجود. monkeypatch spawn spy count0 برای invalidها.
**4. Gate:** مسیر provider فقط local config؛ tool schema هیچ command ندارد؛ pin hash پیش dispatch؛ key reproducible و workspace isolated؛ review اعتماد لازم.
**5. Exceptions:** declared_version صرف label operator است، verified version به‌معنی --version اجراشده نیست. config یا binary تغییر کرد، session قدیمی retire شود؛ fallback پنهان ممنوع.
**6. Order:** models+loader → key/command helpers → scalar settings → tests؛ نصب server خارج task. Python/TS/PHP real executableها فعلاً UNVERIFIED.
**7. Evidence:** EVIDENCE-1؛ config canary در output صفر؛ next03.
<!-- TASK-P02-02-END -->

<a id="task-p02-03"></a>
<!-- TASK-P02-03-START -->
### TASK-P02-03 — ownership و env در مرز launch
EXECUTOR_CLASS: WEAK_WITH_REVIEW؛ Priority:P0؛ Scope:lsp.py، ADD transport.py، tests/test_lsp_sessions.py؛ risk:high.
**1. Current:** LspClient.request Popen:315 با env inherited و parent.kill:402. native helper CURRENT spawn_owned_process(command:Sequence[str],*,cwd:str|Path,env:dict[str,str]|None,stdin:int|IO[Any]|None,stdout:int|IO[Any]|None,stderr:int|IO[Any]|None,creationflags:int,require_native:bool=False)->OwnedProcess. contract close_ownership و terminate_tree موجود.
**2. PROPOSED:** transport.py::open_transport(provider:LspProvider,root:Path)->OwnedProcess فقط بعد validate_provider و URI/request validation؛ env از fixed OS variables SystemRoot/WINDIR/TEMP/TMP یا LANG/LC_ALL/LC_CTYPE/TMPDIR و PATH **فقط parent directory executableهای pinned** ساخته شود. PYTHONPATH/PYTHONHOME/NODE_OPTIONS/NODE_PATH و shell loader vars هیچ inherit ندارند. reuse ownership helper require_native=True؛ _creation_flags موجود؛ stdin/out/err PIPE. current direct LspClient compatibility adapter همین owned transport را برای command تست/legacy بدون provider config، با command allowlist caller خصوصی و validation path حفظ کند؛ public _command همان محدوده قدیمی دارد. transport helper خصوصی جدا برای command-reviewed tuple لازم است، نه public arbitrary dispatcher.
close_transport(process:OwnedProcess,*,timeout_sec:float)->bool: تلاش graceful caller، در timeout terminate_tree سپس bounded wait؛ close_ownership فقط بعد cleanup attempt؛ false اگر proof مرگ/ownership کامل نیست. false به manager quarantine برسد. raw stderr private cap64KiB و discard، provider error فقط code ثابت lsp_request_failed/protocol_error.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_lsp_client.py tests/test_lsp_sessions.py tests/test_windows_job_backend.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0203
~~~
PROPOSED test_lsp_native_ownership_denial_prevents_payload؛ test_lsp_timeout_reaps_descendant (fixture child disposable)، test_lsp_environment_excludes_secret_and_loader_vars؛ test_lsp_error_and_stderr_canaries_do_not_escape. input fake provider raises canary؛ assert in errors/audit/health buffers absent. Existing one-shot handshake/oversize tests preserved.
**4. Gate:** native ownership unavailable = zero payload execution؛ Windows + POSIX required tests؛ skip required ownership BLOCKED؛ no full parent env.
**5. Exceptions:** env filtering OS sandbox نیست؛ binary می‌تواند در حساب user کار کند. legacy one-shot output error wording ممکن است sanitization شود ولی error code حفظ.
**6. Order:** inspect OwnedProcess protocol → helper → direct adapter cleanup → tests؛ unresolved descendant cleanup را با parent PID disappearance پاس نکن.
**7. Evidence:** EVIDENCE-1؛ strong security review، next04.
<!-- TASK-P02-03-END -->

<a id="task-p02-04"></a>
<!-- TASK-P02-04-START -->
### TASK-P02-04 — framing و correlation محدود
EXECUTOR_CLASS: WEAK_WITH_REVIEW؛ Priority:P0؛ Scope:transport.py/lsp.py و tests/test_lsp_sessions.py؛ risk:high.
**1. Current:** lsp._frame، _reader:125 و _wait_response:242؛ message queue بدون maxsize؛ request IDs ثابت1/2/3. _reader lifetime max_total_bytes=8MB برای one-shot است.
**2. PROPOSED:** در transport.py کلاس LspTransport با signature __init__(process:OwnedProcess,*,max_message_bytes:int=4_000_000,max_request_bytes:int=8_000_000)->None؛ send(payload:dict[str,object])->None؛ receive(*,deadline:float,cancel:threading.Event)->dict[str,object]؛ close()->bool. _frame و parser bounded موجود جابه‌جا/استفاده شوند، import compatibility از lsp.py re-export بماند.
- reader فقط یک thread؛ queue maxsize64 و queued byte counter8MB؛ nonblocking enqueue در full → failure sentinel جدا atomic، process terminate درخواست شود، producer در put بی‌نهایت block نشود.
- Content-Length دقیقاً یک header، decimal غیرمنفی≤4MB؛ header64KiB؛ JSON dict/depth≤32؛ invalid id/method type رد؛ decode errors safe code.
- request counter strictly monotonic int؛ owner تنها writer و تنها consumer. pending یک ID چون serialization انتخاب شده؛ unsolicited response ID قبلی drop+bounded counter، unknown future ID protocol fail. notification تا256 در request؛ فقط diagnostics/progress allowlist store bounded metadata، source payload عمومی نشود.
- server request workspace/configuration → list of null متناسب items≤100؛ workspace/applyEdit/executeCommand و registration unsupported → method-not-supported -32601؛ هیچ mutation. initialize negotiation capabilityهای supported صادق باشد.
- byte budget per request reset فقط در boundary قبل send؛ wire lifetime64MiB در manager retire بین requests، نه قطع midway موفق.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_lsp_client.py tests/test_lsp_sessions.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0204
~~~
PROPOSED test_transport_fragmented_header_body؛ test_duplicate_length_denied؛ test_queue_byte_and_count_flood_bounds؛ test_old_response_id_cannot_complete_new_request؛ test_provider_apply_edit_request_is_denied؛ test_notification_flood_closes_session؛ count reads اثبات oversize body not buffered. partial byte pipe fixture deterministic chunks، zero sleeps مگر bounded deadline behavior.
**4. Gate:** bounded allocation/queues، exactly correlated result، no server callback dispatch؛ malformed paths cleanup confirmed.
**5. Exceptions:** partial result tokens هنوز public نیست؛ اگر request partial output دریافت شد جمع bounded و complete=false، هیچ payload silent discard به‌عنوان complete.
**6. Order:** framing move با tests unchanged → queue/counter → callback deny → regression؛ unexpected parser change خارج LSP STOP.
**7. Evidence:** EVIDENCE-1؛ next05.
<!-- TASK-P02-04-END -->

<!-- CHUNK-05-END -->

<a id="task-p02-05"></a>
<!-- TASK-P02-05-START -->
### TASK-P02-05 — manager با مالک واحد و reuse
EXECUTOR_CLASS: WEAK_WITH_REVIEW؛ Priority:P0؛ Scope:ADD sessions.py و tests/test_lsp_sessions.py؛ risk:high.
**1. Current:** manager فعلی وجود ندارد؛ only one-shot LspClient در SRC-LSP. transport/provider از task02..04 PROPOSED predecessor هستند؛ source digest آن‌ها باید در ledger ثبت شده باشد؛ نام‌های جدید را existing معرفی نکن.
**2. PROPOSED algorithm بدون تصمیم باز:**
1. ADD RequestTicket dataclass در sessions.py: request_id:int، payload:bytes، deadline:float، cancelled:threading.Event، future:concurrent.futures.Future[object]، phase:Literal["queued","preparing","may_dispatch","completed"]؛ payload JSON≤64KiB بعد validation immutable است.
2. manager __init__ فقط RLock، dict[SessionKey,Session]، closed=False و counts/capped retry table32 key با TTL60s بسازد. Session state، queue.Queue[RequestTicket](maxsize8)، owner_thread، transport optional، document map و last_activity را در همین module نگه دارد؛ manager registry دیگری جدا نیست.
3. arequest: monotonic deadline از ورود؛ timeout(0,300]، validate method/root/provider با P03-01، key بساز؛ زیر manager lock closed/quarantine/capacity check. برای key جدید Session STARTING را **پیش thread.start** publish کن؛ lock هنگام process startup نگه نداشته شود. Thread.start failure entry را FAILED و ticketها fail کند؛ count فقط وقتی ownership proven پاک شود. queue full → lsp_capacity_exceeded قبل send.
4. مالک session تنها transport را open/initialize می‌کند؛ initialize id1، پاسخ/capabilities، initialized notification؛ سپس READY. برای هر ticket: cancelled/expired → resolve error بدون send؛ phase=preparing و document sync؛ سپس phase=may_dispatch، ID افزایشی، send؛ receive تا ID همان ticket/deadline؛ response error به code safe؛ future اگر done/cancelled نیست resolve؛ phase=completed.
5. async caller با asyncio.wrap_future منتظر شود؛ asyncio.wait_for remaining؛ CancelledError/TimeoutError → cancelled.set و wake owner؛ queued ticket صرفاً حذف شود. in-flight → $/cancelRequest برای ID درست، grace≤250ms سپس retire؛ late response discard. no replay. Future completion بعد caller cancellation safe check داشته باشد.
6. loop queue.get timeout≤0.5s؛ idle120s/limit lifetime drain؛ periodic resource sample قرارداد فاز. 4 distinct key مستقل progress دارند؛ یک key serialized. failure همه queued را با lsp_session_failed خاتمه دهد. restart همان request ممنوع؛ request جدید بعد backoff5s و حداکثر2 start/60s مجاز.
7. close_workspace canonical matching + closed admission برای آن sessions؛ cancel queued و in-flight، owner signal؛ wait حداکثر2s؛ quarantine اگر owner هنوز زنده. snapshot فقط atomically copied counters؛ no filesystem/provider call.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_lsp_sessions.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0205
~~~
PROPOSED test_100_warm_requests_spawn_once؛ test_concurrent_first_request_single_spawn با barrier؛ test_two_workspaces_do_not_share_process_or_documents؛ test_cancel_queued_never_dispatches؛ test_late_result_never_completes_next_ticket؛ test_crash_fails_pending_without_replay؛ test_quarantined_owner_retains_capacity؛ test_idle_cleanup_releases_all_handles. counter fixture evidence PID/requests، نه mock invocation فقط.
**4. Gate:** healthy1/10/100 requests یک spawn؛ crossworkspace two؛ queue32 max؛ cancel no orphan؛ same-SHA hosted three OS و independent lifecycle review. این phase feature flag را روشن نمی‌کند.
**5. Exceptions:** per-session parallel LSP requests عمداً1 است؛ سطح parallelism بین sessionهاست. increase concurrency نیاز جدا و benchmark دارد.
**6. Order:** ticket+manager → owner loop → await/cancel bridge → close/snapshot → integration fake server. Future races خارج الگوی معین STOP و strong review.
**7. Evidence:** EVIDENCE-1؛ next06؛ metrics sizes و abandoned ownerها صفر یا blocker.
<!-- TASK-P02-05-END -->

<a id="task-p02-06"></a>
<!-- TASK-P02-06-START -->
### TASK-P02-06 — نسخه سند، disk snapshot و stale result
EXECUTOR_CLASS: WEAK_WITH_REVIEW؛ Priority:P0؛ Scope:sessions.py و tests/test_lsp_sessions.py؛ risk:high.
**1. Current:** LspClient._document_notification(method:str,params:dict[str,Any])->tuple[dict[str,Any],str]|None همیشه didOpen version1 از disk؛ didClose انتهای هر درخواست. P01-04 content digest آماده است؛ task از metadata cache متن را canonical truth فرض نمی‌کند.
**2. PROPOSED contract:** DocumentState(uri:str,path:Path,sha256:str,version:int,text:str,last_used:float) در Session owner فقط. prepare_document(uri,deadline)->DocumentState:
- uri_to_path و≤2MiB UTF8 strict پیش هر send؛ digest bytes و stat before/after؛2 retries bounded سپس lsp_document_changed.
- جدید: didOpen version1/languageId واقعی، در map؛ همان digest: هیچ didChange؛ تغییر: version+=1 و didChange طبق negotiated sync kind. Full(1): contentChanges[{text}]. Incremental(2): یک full-range edit از range(0,0) تا end قبلی با UTF16 position، text جدید. None(0): didClose/didOpen با version جدید؛ اگر server openClose را رد کرده structured unsupported، نه ادعای sync.
- didSave فقط زمانی که تغییر از disk خوانده شده و server save capability دارد؛ includeText فقط اگر requested. editable unsaved buffer API در این فاز وجود ندارد.
- برای16doc/16MiB LRU فقط سند بدون request فعال didClose و evict؛ اگر ظرفیت نمی‌رسد lsp_document_capacity.
- قبل return، digest سندهای ورودی بازبینی؛ تغییر در حین request → lsp_result_stale، response قابل apply نیست. rename capture versions map و digestها را همراه private result snapshot به P03 بدهد؛ generic read public envelope در02 تغییر نکند.
- حذف/rename/تغییر config: didClose و evict یا retire کل key؛ watcher دائمی نساز، next request detection کافی است؛ workspace cache invalidation از P01 helper بعد write.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_lsp_sessions.py tests/test_lsp_client.py tests/test_workspace_edit_regressions.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0206
~~~
PROPOSED test_same_document_unchanged_opens_once؛ test_disk_change_increments_version و fake sync kind1/2/0؛ UTF16 emoji و CRLF؛ test_change_during_hover_is_stale؛ test_delete_then_recreate_reopens؛ test_document_eviction_sends_did_close؛ test_config_digest_change_retires_session؛ test_worktree_case_and_unicode_isolation. Existing UTF16 helper assertion unchanged.
**4. Gate:** نسخه monotonic، no mixed-snapshot success، LRU bounded؛ pointer change در Windows/Linux semantics صحیح؛ pause/refresh request منجر به replay نشود.
**5. Exceptions:** languageId برای .tsx/.jsx دقیق typescriptreact/javascriptreact؛ client edits خارج disk پشتیبانی نمی‌شوند و صریح رد می‌شوند.
**6. Order:**P01-04/P02-05 verified → DocumentState/read guard → notification sequence → stale result check → tests. اگر provider sync capability نامعلوم است، unsupported code، نه defaultسازی پنهان.
**7. Evidence:** EVIDENCE-1؛ next07 بعد P03-01.
<!-- TASK-P02-06-END -->

<a id="task-p02-07"></a>
<!-- TASK-P02-07-START -->
### TASK-P02-07 — اتصال registration، cancellation و shutdown
EXECUTOR_CLASS: WEAK_WITH_REVIEW؛ Priority:P0؛ Scope:language/registry.py، core/registry.py، heartbeat.py و tests/test_lsp_sessions.py و tests/test_lifecycle.py؛ risk:high.
**1. Current:** register(mcp:MCPServer)->None و _request(root:str,timeout_sec:float,method:str,params:dict[str,Any])->Any؛ common closure sync و rename sync؛ core REGISTRARS["language"] و heartbeat.lifespan مالک lifecycle است. اجرای این task به P03-01 نیز وابسته است تا validation قبل dispatch موجود باشد.
**2. PROPOSED exact integration:**
1. language.register(mcp:MCPServer,*,manager:LspSessionManager|None=None)->None؛ یک manager برای server attribute lsp_manager فقط وقتی persistent flag true و config معتبر. manager construction process/thread ایجاد نکند. direct register با manager None و flag false legacy mode؛ flag true و manager missing خطای configuration startup امن، نه global manager مخفی.
2. core.create_server: برای domain language در flag true config loader خواندنی و manager generation opaque بساز؛ attribute server.lsp_manager؛ register_language(server,manager=manager). سایر registrarها unchanged. config invalid startup fail-safe code بدون prose config.
3. _request را async کن و keyword-only manager و language_id optional اضافه کن؛ deadline در tool entry؛ validate_request از P03-01؛ manager.arequest اگر enabled؛ legacy LspClient.request را via anyio.to_thread.run_sync اجرا کن با abandon_on_cancel=False تا cancellation completion lifecycle حفظ شود (legacy cancellation فوری وعده نمی‌دهد). manager mode async cancellation task05 را دارد.
4. common.tool و rename_symbol async؛ await _request؛ apply_code_action که server call ندارد sync بماند. await LSP response قبل ورود به file mutation helper؛ هیچ file lock هنگام waiting server نگه نداشته شود.
5. heartbeat.lifespan finally پس از mark_stopping، manager.close_all با asyncio.to_thread و timeout budget5s؛ cancellation parent مانع finally cleanup نشود؛ کنترل shutdown با shield+bounded wait و quarantine state. start/lifespan/list/health نباید LSP process بسازد.
6. collect_server_health صرف manager.snapshot را اضافه کند؛ health_snapshot.py فقط شمارنده‌های bounded، هیچ root/source/capabilities provider arbitrary. این فایل جزو scope مجاز integration است؛ tests/test_resource_health.py اضافه‌شونده در این task.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_lsp_sessions.py tests/test_lifecycle.py tests/test_language_registry.py tests/test_language_hardening.py tests/test_resource_health.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0207
~~~
PROPOSED test_mcp_language_requests_share_server_owned_manager؛ test_create_list_health_never_spawns_lsp؛ test_client_cancel_reaches_session_owner؛ test_lifespan_exit_closes_lsp_even_if_extension_drain_raises؛ test_zero_extension_and_persistent_off_preserve_language_schema. واقعی mcp.Client/asyncio.run، نه direct manager فقط.
**4. Gate:** sync/async wrappers mutation admission حفظ؛ no spawn-before-first-request؛ fresh manager هر runtime؛ all active counts0 بعد shutdown یا quarantine آشکار؛ same-head tests و strong review.
**5. Exceptions:** hard deadline legacy cancellation همان محدودیت existing است؛ persistent production GO منوط task08. هیچ global module singleton LSP بساز.
**6. Order:**P03-01 gate → register wiring → async callers → lifespan → health → tests؛ هر caller جدید خارج source graph STOP.
**7. Evidence:** EVIDENCE-1؛ dependency/contract hash برای P03-02 ثبت؛ next08 و P03-02.
<!-- TASK-P02-07-END -->

<a id="task-p02-08"></a>
<!-- TASK-P02-08-START -->
### TASK-P02-08 — acceptance فرایند، cancellation و benchmark
EXECUTOR_CLASS: STRONG_ONLY؛ Priority:P0؛ Scope:test evidence و benchmark در scripts/perf_benchmark.py فقط پس از implementation؛ risk:high.
**1. Current:** fake/strict/oversize fixtures و process ownership موجود؛ perf_benchmark.py::_context_lookup_once فقط context را benchmark می‌کند. persistent evidence این نوبت ندارد.
**2. PROPOSED:** ADD benchmark callable _lsp_session_case(requests:int,workspaces:int,persistent:bool)->dict[str,object] در perf_benchmark، fixture local با commands reviewed؛ output فقط elapsed,p50,p95,p99,spawns,restarts,max_queue,peak_rss,open_handles,errors. safe fixture temporary root با ownership cleanup. parser فعلی build_parser خط2236 و SUITES خط87 بررسی شده‌اند: --suite از SUITES با action=append است. مقدار lsp را به همان tuple اضافه کن و در run_benchmarks خط1891 یک branch if "lsp" in suites با measure و _temporary_root موجود بیفزا؛ flags جدید دیگری لازم نیست. tests/test_perf_benchmark.py موجود محل test parser و report تازه است؛ command آینده پس از این edit: & $PythonExe -m scripts.perf_benchmark --suite lsp --profile quick --runs 30 --output $LspReportPath ؛ LspReportPath باید مسیر گزارش disposable خارج repo باشد. cleanup temp موجود فقط fixtureهای مالکیت‌دار خودش را ببیند. تست جدید test_lsp_benchmark_report_has_correct_spawn_counts در tests/test_perf_benchmark.py این callable را با1/10 می‌خواند.
**3. Checks:** دستور task07 + hosted10؛ benchmark B-LSP: warmup5، repetitions30، N=1/10/100، workspace1/2؛ same machine/fixture، cold و warm جدا. PROPOSED test_crash_and_hang_shutdown_no_orphans، test_cancellation_after_dispatch_does_not_replay، test_failure_code_canary_free، test_session_pressure_denies_before_spawn.
**4. Gate:** warm healthy spawn1/workspace؛ errors0، child orphan0، no data mix؛ median/p95 و soft limits report؛ proposed nonregression p95 warm≤one-shot baseline و resources در حدود؛ تغییر threshold پس از دیدن نتیجه ممنوع. real provider integration برای Python/TS/PHP نسخه/hash شناخته‌شده جدا؛ absent real provider=UNVERIFIED برای آن provider، fake success معادل آن نیست.
**5. Exception:** enable flag یا ship default-on این task نیست؛ release authority مستقل. CPU/RAM isolation ادعا نمی‌شود.
**6. Order:** completed implementation → focused failure matrix → benchmark → same-SHA CI → independent review. timeout یا unavailable native runner BLOCKED، نه retry بی‌حد.
**7. Evidence:** EVIDENCE-1 و B-LSP rows؛ next P03-02 (P03-04 تنها پس از semantic edits و P03-03/05).
<!-- TASK-P02-08-END -->
<!-- PHASE-P02-END -->

<!-- CHUNK-06-END -->

<a id="phase-p03"></a>
<!-- PHASE-P03-START -->
## P03 — ورودی typed و ویرایش معنایی

M03a=01/02؛ M03b=03/05/04 به همین ترتیب. Scope: input validation مشترک، schema سازگار، hash/version و recovery. Non-goals: اجرای command برگشتی server، rename/create/delete filesystem resource از LSP، arbitrary method public. Interface current در SRC-LREG و SRC-EDIT؛ ADD requests.py، بدون جایگزینی identity ابزارها. migration public additive؛ tightening malformed legacy params مستند می‌شود. Rollback code فقط تا پیش format تازه journal بی‌خطر است؛ بعد checkpoint تازه unresolved، ابتدا journal را حفظ و reconcile کن؛ downgrade بدون بررسی ممنوع.

| Tool فعلی | LSP method ثابت | Typed request پیشنهادی |
|---|---|---|
| symbol_definition | textDocument/definition | PositionRequest |
| symbol_references | textDocument/references | ReferencesRequest |
| document_symbols | textDocument/documentSymbol | DocumentRequest |
| workspace_symbols | workspace/symbol | WorkspaceSymbolRequest |
| symbol_hover | textDocument/hover | PositionRequest |
| call_hierarchy | textDocument/prepareCallHierarchy | PositionRequest |
| language_diagnostics | textDocument/diagnostic | DiagnosticRequest |
| rename_symbol | textDocument/rename | existing scalar fields → RenameRequest داخلی |
| apply_code_action | هیچ server call | فقط action.edit با hash guard؛ command ignored |

PROPOSED مدل‌ها در requests.py همگی BaseModel با ConfigDict(extra="forbid")؛ StrictInt ge0/le2^31-1 برای Position.line/character و bool ممنوع؛ DocumentIdentifier.uri:str length1..32767؛ PositionRequest(textDocument:DocumentIdentifier,position:Position)؛ ReferenceContext(includeDeclaration:bool=True)؛ ReferencesRequest(PositionRequest + context:ReferenceContext default_factory)؛ DocumentRequest(textDocument)؛ WorkspaceSymbolRequest(query:str length0..500)؛ DiagnosticRequest(DocumentRequest + identifier:str|None length≤128 + previousResultId:str|None≤512)؛ RenameRequest(PositionRequest + newName:str length1..500). workDoneToken/partialResultToken از legacy فقط str≤128 یا strict int، اگر پشتیبانی partial aggregation task04 آماده نیست validation unsupported بدهد؛ token نامعتبر نادیده گرفته نشود. URI normalization/version read و positional bounds در validator shared پس از schema و پیش spawn اعمال می‌شود.

<a id="task-p03-01"></a>
<!-- TASK-P03-01-START -->
### TASK-P03-01 — method validation در مرز shared
EXECUTOR_CLASS: WEAK_WITH_REVIEW؛ Priority:P0؛ Scope:ADD requests.py، EDIT language/registry.py، tests/test_language_hardening.py؛ risk:high.
**1. Current:** common tool params dict max100 دارد؛ _request مستقیم LspClient می‌سازد؛ URI check بعد Popen است. CURRENT _request(root:str,timeout_sec:float,method:str,params:dict[str,Any])->Any. این task قبل P02-07 و مستقل از async conversion اجرا می‌شود.
**2. PROPOSED:** validate_request(root:Path,method:str,params:Mapping[str,object])->dict[str,object]؛ map method→models بالا، method ناشناخته lsp_method_unsupported. JSON tree depth16/64KiB و maxkeys100؛ schemas strict؛ URI canonical file فقط workspace، no crossroot؛ bounded2MiB read برای position_to_offset تا position واقعی UTF16 باشد؛ no NUL و malformed escape. line/character bool قبل helper رد؛ root باید directory. برای workspace query هیچ document read.
در _request ابتدا resolve_path(root) → validate_request → LspClient/manager؛ از public-only decorator اکتفا نکن. rename همان validator را از طریق _request مصرف می‌کند. server-driven command method public ندارد. پیام ToolError lsp_params_invalid/lsp_uri_unsupported/lsp_path_outside_workspace/lsp_position_invalid ثابت؛ error details source content ندارد.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_language_hardening.py tests/test_lsp_client.py tests/test_language_registry.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0301
~~~
PROPOSED test_language_invalid_request_never_spawns برای negative,bool,overflow position، missing textDocument، unknown extra، URI http/outside/symlink و large params. spy LspClient.request و spawn هر دو0؛ MCPClient و direct _request هردو. test_language_valid_utf16_request_dispatches_once؛ existing test_utf16_position_counts_astral_characters_as_two_units.
**4. Gate:** همه deniedها صفر dispatch؛ valid legacy standard params accepted؛ error code stable؛ source text/secret در sinks صفر.
**5. Exceptions:** legacy nonstandard arbitrary params عمداً دیگر پذیرفته نمی‌شوند؛ مستند شود، schema عمده هنوز dict است تا02. realm roots trusted P02 config قبل process rechecked.
**6. Order:**models → validator → _request wiring → denial tests؛ failure true valid request را با extra=allow پنهان نکن.
**7. Evidence:** EVIDENCE-1؛ next P02-07 یا P03-02.
<!-- TASK-P03-01-END -->

<a id="task-p03-02"></a>
<!-- TASK-P03-02-START -->
### TASK-P03-02 — schema typed additive بدون نام ابزار تازه
EXECUTOR_CLASS: WEAK_WITH_REVIEW؛ Priority:P0؛ Scope:registry.py، requests.py، test_language_registry.py؛ risk:medium.
**1. Current:** common factory هفت نام با یک signature register می‌کند؛ old arguments root,params,timeout_sec. پس از P02-07 async _request وجود دارد. KEEP همه tool names و annotations.
**2. PROPOSED:** factory common را با هفت wrapper کوتاه async با type صریح متناظر جدول جایگزین کن؛ هر wrapper فقط helper مشترک _dispatch_read را فراخوانی کند، نه duplicate validation. نمونه signature دقیق:
~~~python
async def symbol_definition(
    root: Annotated[str, Field(min_length=1, max_length=32_767)],
    params: Annotated[dict[str, Any] | None, Field(max_length=100)] = None,
    timeout_sec: Annotated[float, Field(gt=0, le=300)] = 30,
    request: PositionRequest | None = None,
    language_id: Literal["python", "typescript", "typescriptreact", "javascript", "javascriptreact", "php"] | None = None,
) -> dict[str, Any]:
    selected = select_request(params, request)
    result = await _request(root, timeout_sec, "textDocument/definition", selected, manager=manager, language_id=language_id)
    return {"ok": True, "method": "textDocument/definition", "result": result}
~~~
select_request(params,request:BaseModel|None)->dict[str,object]: exactly one non-None، else lsp_request_shape؛ typed model_dump(exclude_none=True) و همان validate_request. method ثابت در wrapperها. language_id None برای document از suffix، workspace_symbols default python (legacy compatible). mismatch explicit language با file suffix → lsp_language_mismatch، no spawn.
_out envelope ok/method/result unchanged. typed request exposure هزینه schema دارد؛ با byte snapshot baseline سنجیده شود. legacy params accepted تا major version policy جدا؛ هشدار حجیم در هر call اضافه نشود.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_language_registry.py tests/test_language_hardening.py tests/test_tool_profiles.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0302
~~~
PROPOSED test_typed_and_legacy_lsp_inputs_have_same_semantics؛ test_exactly_one_request_form_required؛ test_all_seven_schema_models_are_bounded؛ snapshot canonical selected schemas با fixture data explicit، tool names unchanged، no command property؛ tests count native151 ثابت. complete-wire bytes قبل/بعد گزارش، نه اعداد تاریخی با serializer دیگر.
**4. Gate:** old standard payload success shape یکسان؛ typed invalidها denied؛ no duplicated aliases؛ client old schema بدون refresh هنوز params old را می‌فرستد و کار می‌کند.
**5. Exceptions:** params از required به optional برای dual-input contract می‌رود، اما xor server-side الزامی است. direct call bypass pydantic با helper validate_request همچنان رد می‌شود.
**6. Order:**helper → wrappers → schema snapshot tests → catalog compare؛ no dynamic annotation hacking در factory.
**7. Evidence:** EVIDENCE-1؛ next03.
<!-- TASK-P03-02-END -->

<a id="task-p03-03"></a>
<!-- TASK-P03-03-START -->
### TASK-P03-03 — نسخه/هش و plan-edit صحیح
EXECUTOR_CLASS: WEAK_WITH_REVIEW؛ Priority:P0؛ Scope:edits.py، registry.py، tests/test_workspace_edit_regressions.py و tests/test_semantic_edits.py؛ risk:high.
**1. Current:** prepare_workspace_edit(root:Path,workspace_edit:dict[str,Any],*,expected_sha256:dict[str,str]|None=None,require_expected_sha256:bool=False)->tuple[PlannedFile,...]. version در documentChanges خوانده نمی‌شود؛ URI aliases به path واحد merge و overlap رد می‌شود؛ same-position insert order با test موجود تثبیت شده.
**2. PROPOSED:** امضا additive document_versions:Mapping[str,int]|None=None. برای documentChanges version int nonnegative یا None؛ اگر supplied nonnull و map absent/مخالف: lsp_document_version_conflict. direct callers بدون LSP snapshot اگر version supplied دارند باید map واقعی بفرستند؛ fake default version1 اختراع نشود. legacy changes (بدون version) فقط hash guards معتبر دارند.
- پیش parse file تغییرات≤100file،≤1000edit و aggregate replacement≤2MiB؛ unexpected types ثابت error، range None به AttributeError خام نرسد.
- duplicate identical (canonical path,start,end,newText) → lsp_edit_duplicate؛ same-position **different** insert ترتیب فعلی را حفظ کند.
- rename_symbol expected_sha256 input همه touched fileها را پوشش دهد؛ response زمان server با manager snapshot_version و source digest تطبیق، پس از prepare زیر lock دوباره before bytes مقایسه شود.
- به apply_code_action فیلد optional document_versions mapping برای فقط versioned response اضافه شود؛ server command هرگز اجرا نشود؛ dry_run هیچ write/backup ندارد.
- بعد apply موفق، service.PYTHON_METADATA_CACHE.invalidate و CONTEXT_CACHE.invalidate برای touched paths؛ manager.close_workspace(root) یا invalidate_documents(touched_paths) در owner queue (روش منتخب: close_workspace برای کمترین state sync، request بعدی cold). هیچ stale session پس write استفاده نشود.
**3. Checks:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_semantic_edits.py tests/test_workspace_edit_regressions.py tests/test_language_hardening.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0303
~~~
PROPOSED test_stale_document_version_prevents_all_writes؛ test_duplicate_edit_denied_without_double_insert؛ test_missing_range_returns_safe_error؛ test_edit_budget_rejects_before_reading_extra_files؛ test_success_invalidates_context_and_lsp. EXISTING test_same_position_insertions_keep_server_order و test_uri_aliases_are_applied_as_one_file preserved؛ test_workspace_edit_accepts_text_document_edits_and_rejects_resource_operations باید versions={uri:1} explicit بگیرد.
**4. Gate:** stale/version/hash mismatch file bytes unchanged؛ no provider command؛ invalidation جاری؛ full side-effect crash acceptance بعد05.
**5. Exceptions:** semantic rename با version شناخته‌شده کار می‌کند؛ apply plain edits فقط hash reliable دارد. transaction در process، ACID چند فایل نیست.
**6. Order:** validators/bounds → version map plumbing → invalidate after success → tests؛ no backup logic تا05.
**7. Evidence:** EVIDENCE-1؛ next TASK-P03-05.
<!-- TASK-P03-03-END -->

<a id="task-p03-05"></a>
<!-- TASK-P03-05-START -->
### TASK-P03-05 — journal و backup برای ویرایش چندفایلی
EXECUTOR_CLASS: STRONG_ONLY؛ Priority:P0؛ Scope:core/recovery.py، core/backups.py، tools/language/edits.py و regressionهای نام‌برده؛ risk:high؛ estimate:2–4h، این task مناسب executor ضعیف نیست.
**1. Current:** apply_workspace_edit(plan:tuple[PlannedFile,...])->dict[str,Any] در edits.py:95 فقط locks/rollback دارد. compact_errors journal دارد ولی direct helper callers در test_semantic_edits.py:28/62، test_workspace_edit_regressions.py:30/78 و test_parallel_workflow_resources.py:773 هم وجود دارند. native_uia_mutation:1509 pattern scope ownership موجود؛ OperationRecoveryJournal.begin:1072 / mark_uncertain:1239 / finish:1373؛ checkpoint_postcondition فقط یک file_sha256. BackupRetentionManager.schedule protection فقط یک scan است، durable pin نیست.
**2. PROPOSED دقیق و محدود به همین boundary:**
1. recovery.py: ADD WorkspaceEditEvidence dataclass با files tuple حداکثر100؛ هر row path canonical≤32767، before_sha256/after_sha256 hex64، backup_path canonical؛ JSON normalized≤32KiB. stage enum prepared/may_dispatch؛ schema version داخلی evidence=1. ADD checkpoint_workspace_edit(handle,evidence,*,stage)->None به journal با همان fsync transaction؛ target/file list immutable، only prepared→may_dispatch؛ conflict/unknown version fail closed. top journal version1 additive، field workspace_edit محفوظ در compaction؛ هیچ source/newText داخل record.
2. ADD workspace_edit_mutation(paths:tuple[Path,...])->Iterator[OperationHandle] در recovery.py؛ reuse current scope فقط اگر active و owner همان thread/task، مشابه native_uia_mutation. begin اگر owns؛ اگر handle None (journal disabled) خطای recovery_unavailable **پیش هر backup/write**؛ direct callers همان guard را دارند. outer MCP journal دوباره ساخته نمی‌شود. return success بعد hashes verify؛ exception بعد may_dispatch uncertain، finish(raised) نباید آن را completed کند.
3. backups.py: ADD BackupRetentionManager.pin_paths(paths:Sequence[Path])->ContextManager[None] برای پنجره active publication با refcount capped≤10000 و under existing _lock؛ cleanup snapshot union active pins. برای pin پیش publication، EDIT service._backup_file با optional keyword destination:Path|None=None (مسیر فقط child مستقیم SETTINGS.backup_dir، no symlink، exclusive destination)؛ caller نام UUID backup را اول رزرو/pin می‌کند سپس _backup_file(...,destination=...). این تنها تغییر اضافی مجاز tools/filesystem/service.py است؛ سایر callerها default رفتار قدیم دارند.
4. journal.protected_workspace_backups()->set[Path]: فقط unresolved prepared/may_dispatch/uncertain معتبر، snapshot bounded existing journal quota؛ recovery storage unavailable ⇒ retention cleanup جدید skip با reason recovery_unavailable، نه delete. core.backups در زمان cleanup این set را lazy از همان journal بگیرد؛ import-level cycle نساز. pin فعال تا انتشار checkpoint fsync باقی می‌ماند؛ سپس journal منبع محافظت است. بعد known successful result/reconciled، pin برداشته و policy معمول retention اجرا شود.
5. apply_workspace_edit: managed claims + RESOURCE_LOCKS → all before/hash recheck → journal scope → pin destinations → all atomic backups → checkpoint prepared → checkpoint may_dispatch → _write_bytes loop → verify all after → return success و finish؛ exception attempt reverse restore فقط اگر current bytes هنوز after خود ما هستند؛ conflict در restore یا partial/unknown ⇒ mark_uncertain("workspace_edit_outcome_uncertain")، هیچ replay. no automatic restart rollback؛ manual reconcile با manifest/hash. dry_run مسیر این تابع نیست.
6. اگر combined evidence>32KiB قبل backup/write رد lsp_edit_evidence_too_large؛ unresolved quota deny admission. storage error before may_dispatch صفر target write؛ after may_dispatch evidence/backup حفظ. schema-compatible older code rollback فقط پس از drain/reconcile؛ downgrade در حین unresolved ممنوع.
**3. Checks آینده:**
~~~powershell
& $PythonExe -m pytest -o addopts= -q tests/test_recovery_journal.py tests/test_backups.py tests/test_semantic_edits.py tests/test_workspace_edit_regressions.py tests/test_parallel_workflow_resources.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0305
~~~
PROPOSED test_workspace_edit_direct_call_requires_recovery_before_write؛ test_workspace_edit_mid_batch_crash_keeps_hashes_and_backups؛ test_retention_cannot_delete_prepared_or_uncertain_backups؛ test_pin_to_durable_checkpoint_has_no_retention_gap؛ test_workspace_edit_failed_rollback_stays_uncertain؛ test_workspace_edit_scope_does_not_reuse_child_task_admission؛ test_old_journal_records_and_new_checkpoint_compact_without_loss. subprocess disposable crash fixture reused pattern native UIA crash tests؛ existing semantic success tests configure OPERATION_RECOVERY to tmp_path/runtime, finally restore config؛ هیچ live DB.
**4. Gate:** denied before effects، real process kill در هر checkpoint، reopening حفظ manifest، no blind replay، active-use protection across concurrent cleanup، old record compatibility و مستقل review. اگر هر کدام skipped/pending، VERIFIED ممنوع.
**5. Exceptions:** atomicity per-file و recovery batch، نه filesystem-wide transaction؛ uncertain باید به operator نشان داده شود. اضافه‌شدن journal requirement برای direct helper و نسخه in-process بدون managed runtime باید release note داشته باشد؛ native API عمومی فقط این دو mutating tool در scope tightening هستند.
**6. Order:** schema validation + readers/compaction → retention pins → publication gap test → direct boundary → subprocess failures → whole relevant suite. این ترتیب storage changes را زودتر از enabling mutation آماده می‌کند؛ تغییر DAG یا journal external semantics ممنوع.
**7. Evidence:** EVIDENCE-1 با backup quota/failure coverage و independent review؛ next04.
<!-- TASK-P03-05-END -->

<a id="task-p03-04"></a>
<!-- TASK-P03-04-START -->
### TASK-P03-04 — پذیرش schema، امنیت و بازیابی
EXECUTOR_CLASS: STRONG_ONLY؛ Priority:P0؛ Scope:verification و docs/development/MCP-TOOL-AUTHORING.md و docs/architecture.md و CHANGELOG.md آینده؛ risk:high.
**1. Current:** tests language schema/hashes و existing native recovery؛ no current persistent typed evidence. reviewer باید diff واقعی task01/02/03/05 و P02-08 را ببیند.
**2. PROPOSED:** product features جدید در این task اضافه نمی‌شود. authoring docs مدل typed xor، params legacy، error codes، dry_run/no-server-command، persistent opt-in و recovery requirement را با SOURCE candidate همگام کند. changelog روز واقعی اجرا، تاریخ این plan کپی نشود.
**3. Checks:** تمام commands task03/05 و02-08، tests/test_tool_profiles.py و tests/test_extension_security.py؛ full pytest/Ruff/mypy/compileall/startup/health + hosted10 برای milestone runtime. input denial matrix ماندگار، false success unknown row ممنوع.
**4. Gate:** no public arbitrary executable/method، output legacy-compatible، valid paths only، all touched hashes/version checked، cancellation/crash no replay، complete wire size و client fixture recorded. review issue unresolved => BLOCKED.
**5. Exceptions:** release/merge بدون scope مرتبط ندارد؛ no default-on flag خودکار.
**6. Order:** strong source/diff read → local gates → authorized same-SHA CI → docs consistency. به task ضعیف گفته نشود «خودت architecture امن طراحی کن».
**7. Evidence:** EVIDENCE-1؛ M03b CLOSED فقط evidence کامل؛ next P04-01.
<!-- TASK-P03-04-END -->
<!-- PHASE-P03-END -->

<!-- CHUNK-07-END -->

<!-- PHASE-P04-START -->
<a id="phase-p04"></a>
## P04 — فهم چندزبانه با شواهد و snapshot مشخص

**قرارداد فاز:** مالک تحلیل ساختاری Python همان context/index است؛ مالک فرایند معنایی همان LspSessionManager فاز P02. فایل intelligence فقط نتایج این دو را با provenance یکسان ادغام می‌کند؛ graph database، watcher، daemon و parser جدید ساخته نمی‌شود. تصمیم ADR-06 گزینه A است. Codebase Memory/Graphify ابزار بازرسی توسعه باقی می‌مانند؛ dependency اجباری محصول یا fallback پنهان نیستند. default Python رفتار اصلاح‌شده P01 را نگه می‌دارد. TS/JS/PHP به provider مورد اعتماد وابسته‌اند و نبود آن پاسخ ناقص صادقانه می‌دهد.

**PROPOSED قرارداد مشترک P04:** SymbolKey=(canonical_workspace,language_id,relative_uri,qualified_name,kind,start_line,start_character,content_digest). line/character صفرمبنا، UTF-16 برای موقعیت LSP؛ مختصات Python فعلی در فیلد legacy باقی می‌مانند و coordinate_system صریح اضافه می‌شود. SnapshotEvidence شامل source_sha nullable برای فایل dirty، worktree_id، document_digest، document_version nullable، index_generation، provider_id nullable و source_kind=ast/lsp است. Edge شامل from/to، kind=definition/reference/import/caller/callee/inheritance/related_test، direction، confidence=static_resolved/semantic_reported/inferred، evidence، depth است. semantic_reported ادعای اجرای runtime نیست. همه ورودی/خروجی‌ها version=1 دارند؛ schema نامعتبر cache دور انداخته می‌شود و rebuild محدود می‌گیرد، نه migration پایگاه تازه.

RootResolver فقط root مجاز را canonical می‌کند؛ .git file/directory و manifestهای pyproject.toml/package.json/composer.json را بدون اجرای hook می‌خواند. nested package داخل root جداست؛ package بیرون از root/symlink escape رد می‌شود. package inventory حداکثر 256 manifest و هر کدام 256KiB؛ عمق32؛ missing/invalid manifest به missing_inputs می‌رود. monorepo owner نزدیک‌ترین manifest ancestor است؛ workspace wildcard فقط مسیر نسبی با '*' تک‌جزئی، بدون '..' و بدون shell expansion. زبان از suffix .py/.pyi، .ts/.tsx/.js/.jsx/.mjs/.cjs، .php؛ زبان ناشناخته=unsupported، نه Python پیش‌فرض پنهان.

**Result contract:** فیلدهای legacy definitions/callers/callees/references/imports/tests/pagination حفظ می‌شوند؛ language_id، backend، snapshot، completeness_by_relation، missing_inputs و fallback_reason additive هستند. complete یعنی تمام بخش‌های درخواست‌شده در scope پاسخ پوشش دارند؛ semantic_complete از آن جداست. نبود imports/inheritance در provider به complete=false آن بخش می‌انجامد. AST fallback Python آشکار است؛ برای TS/PHP ساختن رابطه با regex و برچسب exact ممنوع. capها P01/P02 مشترک‌اند، total_exact=false برای پاسخ بریده؛ context traversal حداکثر2، vertices1000، edges10000، fanout100 و deadline کلی5s پس از آماده‌شدن نشست. deadline درخواست provider از remaining budget تجاوز نمی‌کند.

<!-- TASK-P04-01-START -->
<a id="task-p04-01"></a>
### TASK-P04-01 — قرارداد داده و ریشه پروژه
**1. Identity / scope:** STATE_RECORD_ID=TASK-P04-01؛ PROPOSED؛ فایل جدید tools/project/intelligence_models.py و tools/project/project_types.py، تست جدید tests/test_project_intelligence_models.py. پیش‌نیازهای Index؛ همین دو module و test، بدون registration/IO اجرایی.
**2. Proof pack:** SOURCE_BASE؛ EXISTING tools/project/context.py::Definition/ContextIndex و tools/project/index.py::FileVersion؛ tools/project/service.py::iter_files/iter_python_files؛ کاربر فعلی project.registry.code_context. rg با نام‌های دقیق باید هر تعریف اصلی را یک بار بیابد. signature فعلی Definition در evidence map؛ تغییر نمی‌کند.
**3. Contract / patch:** dataclass frozen برای SymbolKey و SnapshotEvidence؛ Pydantic BaseModel extra=forbid برای EvidenceEdge و ContextEvidence، enumهای بالا و list<=1000. PROPOSED detect_project_types(root:Path,*,max_manifests:int=256)->ProjectInventory؛ resolve_package_for_file(inventory,path)->PackageInfo|None. از resolve_path/canonical_path موجود برای مرز سیاست استفاده شود؛ root منتخب caller authority است و .git صرفاً هویت snapshot می‌دهد. ابتدا schemaها، سپس reader محدود با duplicate-key denial برای JSON و tomllib در Python3.11+ برای pyproject؛ در Python3.10 فقط marker/hash و missing_inputs=toml_parser_unavailable، سپس resolver نزدیک‌ترین parent. بازخوانی hash manifest هنگام مصرف، نه اعتماد ابدی به cache. errors=invalid_project_manifest/project_scope_escape/inventory_limit؛ فقط reason/path نسبی، متن manifest در error نه.
**4. Tests / expected:** تست پیشنهادی test_symbol_identity_separates_worktrees با دو root و نام یکسان؛ test_nested_manifest_owner، test_symlink_escape_denied، test_json_duplicate_keys_rejected، test_schema_unknown_version_rejected؛ invalid بودن یک manifest بقیه را حذف نمی‌کند و complete=false. فرمان آینده:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_project_intelligence_models.py tests/test_project_cache.py
~~~
exit0 و تمام assertions؛ تعداد آینده از قبل ادعا نشود.
**5. Failure / compatibility:** tomli در dependencies این پروژه اثبات نشده است؛ در Python3.10 نبود tomllib فقط metadata آن manifest را ناقص می‌کند و inventory زبان/مسیر با marker و hash ادامه می‌یابد؛ import بدون fallback و dependency جدید ممنوع. نسخه SDK/مدل ناسازگار یا فرمت ناشناخته => توقف همین task و review، نه parser حدسی. قبل از coding واردکردن tomllib بدون fallback ممنوع.
**6. Order / impact:** مدل‌ها → detector → tests → focused Ruff/mypy؛ exports فقط import داخلی P04، tool جدید ندارد. consumerهای فعلی تغییر نمی‌کنند.
**7. Evidence / next:** EVIDENCE-1، diff سه فایل، version/denial assertions؛ next P04-02. نوشتن مدل به‌تنهایی task را VERIFIED نمی‌کند.
<!-- TASK-P04-01-END -->

<!-- TASK-P04-02-START -->
<a id="task-p04-02"></a>
### TASK-P04-02 — adapter معنایی با session موجود
**1. Identity / scope:** STATE_RECORD_ID=TASK-P04-02؛ PROPOSED tools/project/intelligence.py، تغییر کوچک tools/language/requests.py از P03 برای methodهای داخلی type/call hierarchy، tests/test_project_intelligence.py جدید. هیچ provider مستقل یا public arbitrary method.
**Source-reconciled owner boundary (2026-10-21):** scope همچنین tools/language/sessions.py و tests/test_lsp_sessions.py را برای SessionReply داخلی frozen شامل value، SessionKey موجود، incarnation یکتای هر نشست، شش capability flag ثابت و tupleهای محدود URI/version/digest دربرمی‌گیرد. owner پس از verify_documents همان ticket شواهد را منتشر می‌کند؛ arequest امضای قدیمی و raw result/RenameResult را نگه می‌دارد. نخستین درخواست واقعی انتخاب نماد، snapshot معتبر را می‌آورد؛ capability-only warmup یا getter فعال‌کننده نداریم. item.uri در پنج method داخلی hierarchy با همان document sync/validation موجود پوشش می‌گیرد. adapter به private session dictionaries دسترسی ندارد؛ در تغییر key/incarnation کل batch دور ریخته می‌شود. CodePosition ورودی internal/public-additive با path نسبی یا absolute-in-root و line/character صفرمبنا UTF-16 از Position موجود ساخته می‌شود؛ schema قدیمی public language tools تغییر نمی‌کند.
**2. Proof pack:** EXISTING LspClient.request در tools/language/lsp.py و tools/language/registry.py::_request؛ جایگزین وابسته P02 LspSessionManager.arequest. Anchor جدید باید از evidence P02 گرفته شود؛ اگر dependency VERIFIED نیست STOP. signature adapter پیشنهادی: async lookup_semantic_context(manager,root:Path,symbol:str,*,language_id:str,anchor:CodePosition|None,max_depth:int,max_relationships:int,offset:int,deadline:float)->ContextEvidence.
**3. Patch algorithm:** resolve root/language → obtain validated session → capability snapshot → انتخاب نماد. anchor صریح اولویت دارد و باید با symbol match کند؛ بدون anchor فقط workspace/symbol با query=symbol، حداکثر100 candidate و exact name/container filter؛ صفر=not_found، چند=ambiguous_symbol با candidateهای محدود، هیچ انتخاب اولین نتیجه. definition/LocationLink به یک identity نرمال شود؛ references با includeDeclaration=false؛ prepareCallHierarchy و incomingCalls/outgoingCalls فقط اگر capability اعلام شده؛ typeHierarchy prepare/supertypes/subtypes فقط capability معتبر و validator مشخص. import extraction فقط AST Python، برای TS/PHP unsupported_relation. همه URIها در root، digest قبل/بعد هر batch و generation/session ثابت. BFS جدا برای هر جهت، visited=SymbolKey+edge kind، cyclic duplicate حذف، fanout100. timeout/cancel قبل dispatch=no_result؛ بعدdispatch read-only=LSP_RESULT_UNAVAILABLE، درخواست تازه خودکار به provider دیگر نرود. output bounded و pagination مشترک P01؛ relationهایی که تکمیل نشده‌اند completeness=false.
**4. Tests:** fake manager از P02 با responseهای دقیق Location/LocationLink؛ test_ambiguous_symbol_requires_anchor، test_call_cycle_has_bounded_unique_edges، test_type_hierarchy_capability_absent_is_incomplete، test_provider_uri_outside_root_denied، test_generation_change_discards_batch؛ fake clock deadline و counts نشان دهند بیش از100 sibling یا عمق2 dispatch نشده. فرمان:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_project_intelligence.py tests/test_lsp_sessions.py
~~~
tests/test_lsp_sessions.py همان فایل پیشنهادی P02 است؛ اگر dependency آن را نساخته توقف، نه حذف از فرمان.
**5. Exceptions:** پاسخ بزرگ/بدفرمت provider به incomplete+reason یا safe error می‌رود؛ raw server exception/source در log نیست. server-initiated request سیاست P02 را دور نمی‌زند. پاسخ LSP مدرک runtime dynamic dispatch نیست.
**6. Order / consumers:** مدل internal hierarchy → adapter → fake tests؛ registry تا P04-03 دست‌نخورده. weak executor از schema/capability تعیین‌شده پیروی می‌کند؛ افزودن method باز نیازمند review.
**7. Evidence:** fixture transcript redacted، request_count/edge_count/digest assertions، EVIDENCE-1؛ next P04-03.
<!-- TASK-P04-02-END -->

<!-- TASK-P04-03-START -->
<a id="task-p04-03"></a>
### TASK-P04-03 — اتصال API و freshness بدون index موازی
**1. Identity / scope:** STATE_RECORD_ID=TASK-P04-03؛ EXISTING tools/project/registry.py::code_context، context.py::lookup_code_context، index.py::PYTHON_METADATA_CACHE؛ PROPOSED tests/test_multilanguage_context_registry.py. فقط adapter فاز قبل مصرف شود.
**Source-reconciled integration boundary (2026-10-22):** پارامتر language_id به‌صورت Literal[auto,python,typescript,javascript,php]|None=None است: omitted/null همان Python قدیمی؛ explicit auto از inventory و قانون language_required چندزبانه استفاده می‌کند. defaultauto پیشنهادی از explicitauto در SDK فعلی و direct calls قابل تفکیک نبود و mixed-root Python موجود را می‌شکست؛ بدون raw-request peeking یا tool تازه. نام واقعی path حفظ می‌شود. scope لازم شامل intelligence.py برای یک SemanticSnapshot داخلی با cap10000 edge و صفحه عمومی ContextEvidence cap1000، sessions.py برای همان مالک cache bounded32/16MiB/TTL30/LRU/singleflight، index.py برای epoch scalar روی همه invalidateها، project_types.py برای fingerprint اختیاریِ همه sourceهای مرتبط با همان scanner محدود، و tests مرتبط است. snapshot موقت یک نتیجه است، نه index/graph/watcher دوم؛ public adapter امضای فعلی را نگه می‌دارد. فیلتر includeها و ادغام AST imports/tests قبل از یک pagination سراسری انجام می‌شود. fingerprint ناممکن/ناقص => بدون admission/hit/coalescing؛ hashهای فقط فایل‌های برگشتی برای cache کافی نیستند. cache callback/IO بیرون RLock، cancellation و close generation guard دارند. wrapper manager را هنگام فراخوانی از server.lsp_manager موجود می‌گیرد؛ ترتیب registration پروژه پیش از زبان مالک تازه‌ای ایجاد نمی‌کند.
**2. Proof pack:** SOURCE_BASE code_context حوالی262 و lookup_code_context272؛ فراخوان دیگر scripts/perf_benchmark.py::_context_lookup_once است و sync AST helper ثابت می‌ماند. ماژول registry tool wrapper async می‌شود؛ semantic manager همان server.lifecycle ownership دارد.
**3. Contract / patch:** سه پارامتر additive در tool: language_id:Literal["auto","python","typescript","javascript","php"]="auto"؛ backend:Literal["auto","ast","semantic"]="auto"؛ semantic_anchor:CodePosition|None=None. root/symbol و fieldهای قدیمی همان معنا؛ auto+Python بدون anchor=AST، auto+nonPython یا anchor=semantic؛ ast+nonPython => unsupported_language؛ language auto بدون file/anchor و inventory چندزبانه=>language_required، نه حدس. sync AST helper با anyio.to_thread.run_sync و cancellation-safe انجام شود؛ semantic adapter await. هیچ await زیر RLock. AST cache P01 کلید root+digest manifest+indexgeneration دارد؛ semantic result در manager document-generation ذخیره می‌شود، maximum32 result/16MiB/TTL30s و byte LRU در همان مالک، بدون watcher جدید. write/invalidate به هر دو نسل اعلام می‌شود؛ snapshot digest هنگام return بازبینی. fallback هنگام missing provider فقط Python AST با reason و semantic_complete=false؛ در JS/PHP نتیجه خالی incomplete با راهنمای نصب local-admin، نه موفقیت.
**4. Tests:** test_legacy_python_call_shape_unchanged، test_typescript_without_provider_is_not_complete، test_multilanguage_auto_requires_language، test_concurrent_same_snapshot_singleflight، test_edit_and_rename_invalidate_context، test_warm_result_cannot_cross_worktree. tests/test_code_context.py موجود به همراه registry جدید:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_code_context.py tests/test_multilanguage_context_registry.py tests/test_project_intelligence.py
~~~
catalog snapshot باید input قدیمی را همچنان بپذیرد؛ incomplete دلیل دارد.
**5. Failure / compatibility:** نبود manager lifecycle در direct tool call => language_session_unavailable، no spawn؛ AST همچنان usable. persisted workflow که code_context را مستقیم action نمی‌شناسد نیازی به action تازه ندارد. رفتار ambiguity جدید فقط درخواست چندزبانه جدید را محدود می‌کند.
**6. Order:** additive schema → wrapper → invalidate hook → contract tests؛ root/index lock نشت نکند، provider IO بیرون lock.
**7. Evidence:** input-schema قبل/بعد و legacy fixture output، EVIDENCE-1؛ next P04-04.
<!-- TASK-P04-03-END -->

<!-- TASK-P04-04-START -->
<a id="task-p04-04"></a>
### TASK-P04-04 — fixture و پذیرش چندزبانه
**1. Identity / scope:** STATE_RECORD_ID=TASK-P04-04؛ ساخت fixtureها داخل tmp_path در tests/test_multilanguage_context_registry.py و tests/test_project_intelligence.py، helper جدید tests/fixtures/code_intelligence.py در صورت مصرف مشترک دو فایل؛ هیچ node_modules/vendor واقعی commit نشود. docs/architecture.md و docs/development/TESTING-STANDARDS.md فقط بعد از acceptance آینده.
**2. Proof pack:** tests/test_code_context.py::_write_project و fixtures P02 fake LSP؛ source context evidence P01؛ tests/test_project_cache.py invalidation cases. providerهای واقعی optional integration هستند، fake پروتکل deterministic gate اصلی است.
**3. Patch / fixtures:** Python app/service + importer alias + pytest؛ TS packages/ui src/App.tsx/helper.ts/test با duplicate helper درpackageدیگر؛ PHP app/Service.php + tests/ServiceTest.php با PHPUnit/Pest manifest؛ monorepo TS و PHP بدون فرض cross-language edge؛ symlink outside، rename/delete، broken syntax، circular calls، stale importedgraph marker. همه expected edges/file ranges/digestها در fixture تعریف شوند. هیچ test برای اثبات semantic accuracy به نام فایل به‌تنهایی تکیه نکند. LSP fake پاسخ از fixture map ثابت؛ test script counts initialize=1 و shutdown=1.
**4. Acceptance:** false definite edge=0 در fixture؛ missing relation=>incomplete؛ all pages concatenated == unique expected set؛ worktreeA هیچ مسیرB ندارد؛ provider unavailable اجرا نمی‌شود؛ broken syntax source حفظ می‌شود. cold/warm latency و RSS فعلاً NOT_MEASURED تا run مجاز، score precision فقط برای fixture با ground truth. فرمان:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_project_intelligence_models.py tests/test_project_intelligence.py tests/test_multilanguage_context_registry.py tests/test_code_context.py tests/test_project_cache.py
& .\.venv\Scripts\python.exe -m ruff check tools/project tools/language tests/test_project_intelligence_models.py tests/test_project_intelligence.py tests/test_multilanguage_context_registry.py
& .\.venv\Scripts\python.exe -m mypy core tools scripts tests
~~~
exit0؛ سپس گیت M04 و10check همانSHA در scopeمجاز.
**5. Exceptions:** optional real providers روی هیچ runner نصب نشوند مگر setup مصوب؛ skip آن‌ها را در evidence بنویس، fakegreen جای real integration ادعا نشود. parser thirdparty یا native binary جدید از اینtask خارج است.
**6. Order:** fixture → denial/freshness → counts → docs/changelog برای تغییرات واقعی؛ review semantic confidence؛ compatibility full/coding/minimal.
**7. Evidence:** M04 بسته می‌شود فقط با نتایج واقعی؛ EVIDENCE-1؛ P04-04 VERIFIED on0414382 hosted10 (P04 complete)؛ next P06-01 مطابق dependency. هم‌زمان writerهای context ممنوع.
<!-- TASK-P04-04-END -->
<!-- PHASE-P04-END -->
<!-- CHUNK-08-END -->

## RESUME CHECKPOINT — 2026-10-19

آخرین واحد سالم بازیابی‌شده CHUNK-08-END و TASK-P04-04-END است: 139306 bytes، 941 line، 23 جفت Task Marker و فازهای P00..P04 کامل. Index قدیمی 38 ردیف دارد؛ TASK-P03-05 در body سالم است و در پایان به Index افزوده می‌شود. هیچ body سالمی از نو تولید نشد. Ledger نهایی هنوز نوشته نشده بود؛ اطلاعات snapshot/CI و marker ها قابل بازیابی‌اند و هیچ شاهد لازم گم نشده است.

بازبینی خواندنی امروز: local HEAD، origin/main محلی و main زنده همچنان SOURCE_BASE هستند؛ PR81 همان head/merge و MERGED؛ PR باز صفر. سه run اصلی 39131440536/39131440505/39131440461 همچنان completed/success؛ Publish run39131611639 موفق است اما شاهد قبلی publish=false باقی است. tools/panel/index.html و uv.lock همان تغییرات پیش‌موجودند. در نتیجه بازرسی P00..P04 تکرار نشد. Graph Evidence تاریخی این نشست با همان SHA حفظ می‌شود؛ تغییر availability ابزار به‌تنهایی موجب rebuild نمی‌شود. ادامه append از P05.

<!-- PHASE-P05-START -->
<a id="phase-p05"></a>
## P05 — کاتالوگ کوچک‌تر بدون شکستن صلاحیت اجرا

**تصمیم:** full و coding دست‌نخورده؛ profile جدید coding_core صرفاً opt-in و static در زمان ساخت server. هدف 30–50 ابزار فرضیه UX است. demand-loading و تغییر پویا تا اثبات رفتار client مسدود می‌ماند. کم‌شدن byte به معنی token/latency کمتر نیست. شمارش پایه واقعی full151/153636bytes، coding96/92674bytes، minimal32/31555bytes با serializer یکسان است؛ اندازه‌های تاریخی 168527/101592 مبنای امروز نیستند.

<!-- TASK-P05-01-START -->
<a id="task-p05-01"></a>
### TASK-P05-01 — حذف side effect زمان registration
**1. Identity / scope:** STATE_RECORD_ID=TASK-P05-01؛ EXISTING core/registry.py::create_server، core/heartbeat.py::lifespan، tools/jobs/registry.py::register، tools/testing/registry.py::register؛ tests/test_tool_profiles.py و tests/test_jobs_registry.py و tests/test_background_verification.py. مالک JobStore و scheduler موجود حفظ شود.
**2. Proof pack:** SOURCE_BASE؛ create_server اکنون ensure_runtime_dirs را می‌خواند؛ jobs.register خط 22 store=JobStore و 23 ensure_job_scheduler؛ testing.register خط 43 watch_store=JobStore و 44 ensure_job_scheduler. core/job_scheduler.py::ensure_job_scheduler(store:JobStore)->JobScheduler|None هم launch_once و هم thread.start دارد. JobStore.submit مسیر موجود kickoff را دارد؛ آن را حذف نکن. نتیجه P00 instrumentation نشان می‌دهد list-only باید از activation جدا شود.
**3. Contract / pseudo-diff:** در create_server فقط construction/metadata انجام شود؛ ensure_runtime_dirs به آغاز lifespan، پیش از configure recovery منتقل شود. به هر دو register آرگومان keyword-only store_getter:Callable[[],JobStore]|None=None اضافه کن؛ default getter lazy با Lock و یک cached reference، بدون instantiate تا call واقعی. create_server یک getter مشترک به دو registrar می‌دهد؛ closure store_getter() جای store/watch_store پیش از مصرف. در lifespan اگر profile domains شامل jobs یا testing است یک بار getter را در asyncio.to_thread صدا بزن و ensure_job_scheduler را برای بازیابی queue موجود اجرا کن. explicit tool های submit/read job می‌توانند getter را مصرف کنند؛ list_tools/discovery/router/health به getter دست نزنند. scheduler همان durable queue owner است؛ lifespan shutdown نباید job مستقل را kill کند یا scheduler تازه‌ای بسازد. سیاست drain موجود jobs حفظ شود؛ claim اجرای tool همچنان wrapper است.
**4. Tests / acceptance:** test_catalog_construction_has_no_disk_process_or_thread_effect جدید با monkeypatch JobStore/ensure_runtime_dirs/Popen/Thread.start برای fail-on-call، روی همه profile ها و list_tools/recommend_tools/server_health؛ health اگر state آماده نیست unavailable-safe و بدون lazy activation. test_lifespan_recovers_queued_jobs_once با tmp DB و launch spy؛ test_job_tools_share_lazy_store؛ tests موجود background reconnect/cancel. فرمان آینده:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_tool_profiles.py tests/test_jobs_registry.py tests/test_background_verification.py
~~~
انتظار: no activation قبل lifespan، یک kickoff هنگام startup مجاز، queuedjob پس از restart قابل بازیابی، no duplicate dispatch.
**5. Exceptions / stop:** stale DB/migration failure هنگام lifespan fail-closed؛ construction همچنان catalog قابل‌تشخیص می‌دهد. اگر health getter پنهان دیگری store می‌سازد فقط همان call path را جدا و test کن؛ بازنویسی کل scheduler خارج scope. اگر lifetime scheduler مستلزم تغییر durable ownership باشد STRONG review و task BLOCKED، نه حذف recovery.
**6. Order / callers:** getter injection → registrars → lifespan → no-side-effect tests → recovery regression. direct tests register(mcp) هنوز با default lazy getter کار می‌کنند. wrapper schema ابزار عوض نمی‌شود. همه 5 فایل محصول touched را مرور کن.
**7. Evidence:** EVIDENCE-1 + شمارنده activation و catalog byte با instrumentation قبلی بدون monkeypatch مالکیت اصلی؛ next P05-02.
<!-- TASK-P05-01-END -->

<!-- TASK-P05-02-START -->
<a id="task-p05-02"></a>
### TASK-P05-02 — profile ثابت coding_core با آزمون UX
**1. Identity / scope:** STATE_RECORD_ID=TASK-P05-02؛ core/tool_profiles.py، core/registry.py، core/extensions/bridge.py، tests/test_tool_profiles.py، tests/test_extension_registry.py موجود. benchmark فقط scripts/perf_benchmark.py::catalog suite موجود؛ docs ابزارها بعد از acceptance.
**2. Proof pack:** ToolProfile(name,domains) و PROFILE_DOMAINS از 9 profile موجود؛ ExtensionMCPServer.call_tool خط 117 برای native به super می‌رود و external_tools خط 96 active_profile را بررسی می‌کند. SDK2.0.1، MCPServer.add_tool خط 570 یک seam عمومی synchronous با امضای fn/name/title/description/annotations/icons/meta/structured_output و returnNone است؛ decorator tool همین مسیر را مصرف می‌کند. remove_tool عمومی نیز موجود است، ولی برای filtering زمان ساخت به async list_tools یا private mutation نیاز نیست.
**3. Proposed contract / algorithm:** افزودن native_allowlist:frozenset[str]|None به ToolProfile با defaultNone؛ coding_core domains برابر coding، allowlist زیر 43 نام. ExtensionMCPServer.add_tool را با همان signature عمومی SDK override کن: name نهایی=name or fn.__name__؛ اگر allowlist وجود دارد و نام خارج آن است بدون registration برگرد؛ در غیراین‌صورت super().add_tool را با همه metadata اصلی صدا بزن و نام موفق را در set محدود native_registered_names ثبت کن. این set فقط نمای نام‌های ثبت‌شده است، registry اجرایی دوم نیست. create_server allowlist را به constructor بدهد و در پایان equality نام‌های ثبت‌شده با 43 نام را بررسی کند؛ نام غایب=>profile_invalid. ساخت server synchronous باقی می‌ماند و asyncio.run در event loop فعال ممنوع است. ExtensionMCPServer.call_tool قبل از native super، allowlist را دوباره enforce کند؛ direct MCP call هم hidden_tool_unavailable، no dispatch. Router و discovery فقط catalog واجدصلاحیت را مصرف کنند. extension ها برای profile جدید پیش‌فرض deny تا local operator profile eligibility صریح بدهد؛ namespace/generation/guard ها همان v0.8.
~~~text
discover_tool_domains, recommend_tools, execution_candidates, execution_recommend, server_health
read_file, search_files, list_directory, get_file_info, apply_patch, replace_exact, write_file, create_file
project_summary, code_context
symbol_definition, symbol_references, document_symbols, workspace_symbols, symbol_hover, call_hierarchy, language_diagnostics, rename_symbol, apply_code_action
affected_tests, verify_changes, start_verification
git_status, git_diff, git_diff_summary, git_changed_files, git_stage, git_commit, git_conflicts
list_uncertain_operations, inspect_uncertain_operation, reconcile_operation, acknowledge_uncertain_operation, get_operation_history
job_status, job_wait, job_output, cancel_job
~~~
Backend alias های خارج این profile حذف نمی‌شوند؛ full/coding و workflow های persisted همان implementation را نگه می‌دارند. profile محدود امنیت sandbox نیست؛ write_file مجاز همچنان mutation است و همان policy/recovery را دارد. Client فاقد refresh با restart روی profile انتخابی کار می‌کند؛ fallback صریح انتخاب coding/full و restart، نه نمایش ابزارِ فراخوانی‌ناپذیر.
**4. Tests / benchmark:** test_coding_core_catalog_matches_exact_allowlist، test_hidden_mutating_tool_denied_before_dispatch با spy، test_full_and_coding_legacy_catalog_unchanged، test_extension_requires_explicit_new_profile_eligibility، test_no_refresh_client_can_complete_static_journey. Journey=read→context→patch dry-run→apply با hash→verify→git diff؛ fixture job lifecycle هم لازم. فرمان:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_tool_profiles.py tests/test_extension_registry.py tests/test_background_verification.py
~~~
Benchmark آینده: same serializer + actual tokenizer در صورت موجود با tokenizer/version اعلام‌شده؛ در نبود tokenizer NOT_MEASURED. 20 task ثابت×3 تکرار×full/coding/coding_core با model/client/version و ترتیب متوازن؛ success، wrong-tool، تعداد discovery roundtrip، p50/p95 انتخاب و end-to-end، tokens/bytes جدا. پذیرش opt-in: success کمتر از coding نباشد، wrong-tool بیشتر نشود، p95 بیش از 5% بدتر نشود؛ با n کوچک فقط pilot، default switch ندارد.
**5. Stop:** ابزار allowlist غایب، eligibility bypass، footprint بزرگ‌تر یا clientjourney fail => profile پیشنهادی منتشر نشود؛ full/coding را برای کوچک‌کردن عدد حذف نکن. E2E مدل زنده بدون budget/مجوز جدا اجرا نشود.
**6. Order:** allowlist → public add_tool filter → dispatch guard → extension test → metadata → controlled benchmark. public discovery action تازه‌ای که arbitrary tool را activate کند ساخته نشود.
**7. Evidence:** M05 static acceptance به علاوه results/limitation؛ next P05-03 فقط اگر blocker رفع شد، وگرنه ادامه P06.
<!-- TASK-P05-02-END -->

<!-- TASK-P05-03-START -->
<a id="task-p05-03"></a>
### TASK-P05-03 — refresh پویا، DESIGN-BLOCKER-01
**1. Identity / scope:** STATE_RECORD_ID=TASK-P05-03؛ DESIGN_BLOCKED. امروز فقط این design record؛ mutation و registry جدید مجاز نیست.
**2. Source / gap:** ExtensionMCPServer.notify_catalog_changes نسل registry را با ToolsListChanged و legacy send_tool_list_changed ارسال می‌کند؛ این کد اثبات نمی‌کند client واقعی ابزارهای تازه را refresh می‌کند. client name/version، transcript دریافت notification، refresh و رفتار old cached call فعلاً UNVERIFIED.
**3. Conditional contract:** پس از رفع blocker، فقط extension mechanism موجود reuse شود. Proposed DomainActivationRequest={domain:enum,expected_catalog_generation:int}; نتیجه={generation,visible_names,expires_at}، سقف 8 domain، یک generation owner، activation فقط allowlisted policy و local approved profile. هیچ schema/description provider مجوز ایجاد نمی‌کند. نسل قبلی execution lease معتبر نباشد؛ admission همزمان activation/update باید CAS باشد. client بدون refresh => activation_unavailable و static profile؛ عدم پشتیبانی را با polling بی‌حد حل نکن.
**4. Acceptance design:** fixture دو client یکی refresh-capable و دیگری cached؛ notification burst coalesced، removed tool call denybeforedispatch، stale lease deny، reconnect catalog یکسان، extension shadow deny. Proposed tests/test_progressive_discovery.py؛ فقط پس از design approval/implementation فرمان pytest همین فایل همراه tests/test_extension_registry.py قابل اجراست. امروز EXPECTED=BLOCKED و هیچ تست تولید/اجرا نشده.
**5. Stop:** نبود transcript واقعی و budget UX مانع قطعی است. SLA/کاهش tokens ادعا نشود.
**6. Order:** جمع‌آوری شواهد read-only client → تصمیم کوچک‌ترین scope → مشخص‌کردن lifecycle و rollback در همین block → خردکردن implementation tasks قبل coding. تغییر تعداد tasks/index در آن آینده لازم است.
**7. Evidence:** DESIGN-BLOCKER-01؛ static P05-02 dependency های بعدی را آزاد می‌کند، dynamic prerequisite هیچ P0 نیست.
<!-- TASK-P05-03-END -->
<!-- PHASE-P05-END -->
<!-- CHUNK-09-END -->

<!-- PHASE-P06-START -->
<a id="phase-p06"></a>
## P06 — انتخاب و اجرای تست چندزبانه

M06a=TASK-P06-01/02؛ M06b=03/04/05. Python default و Check های قدیمی حفظ می‌شوند. انتخاب تست read-only است، اما اجرای pytest/Node/PHP می‌تواند کد repository و plugin های آن را اجرا کند؛ OPEN_WORLD_WRITE، مجوز اجرای کد آن repo، resource claims و ownership لازم است. Discovery هرگز package script/config JS/PHP را اجرا نمی‌کند. نتیجه بدون تست و با اطلاعات ناقص موفقیت نیست.

<!-- TASK-P06-01-START -->
<a id="task-p06-01"></a>
### TASK-P06-01 — شناسایی package و runner بدون اجرا
**1. Identity / scope:** STATE_RECORD_ID=TASK-P06-01؛ PROPOSED tools/testing/project_runners.py و tests/test_multilanguage_affected_tests.py؛ مصرف project_types.py از P04؛ حفظ tools/testing/runners.py::python_for.
**2. Proof pack:** SOURCE_BASE tools/testing/impact.py::select_affected_tests و tools/project/service.py::iter_python_files؛ testing/runners.py21 python_for(cwd:Path)->str. service._read_dependencies فعلی metadata می‌خواند ولی reader bounded و مرز monorepo P04 را جایگزین نمی‌کند؛ بدنه‌اش به این feature copy نشود.
**3. API / pseudo-diff:** discover_test_runners(root:Path,inventory:ProjectInventory)->RunnerInventory. RunnerSpec={runner:pytest/unittest/jest/vitest/phpunit/pest/unknown,package_root,manifest_digest,config_paths,entrypoint,version,discovery_evidence,complete,missing_inputs}. evidence فقط installed manifest و config names؛ package.json scripts متن untrusted است و برای argv استفاده نمی‌شود. pytest از pyproject/pytest.ini و tests fixture؛ unittest فقط با انتخاب صریح، نه دومین اجرای حدسی همان suite. Jest/Vitest از dependency+node_modules package metadata و bin canonical؛ Composer از require-dev+vendor/composer/installed.json و vendor/bin متن PHP؛ PHP config فقط bounded XML. هر فایل<=256KiB، aggregate4MiB، packages256؛ JSON duplicate/unknown type رد، XML دارای DOCTYPE/ENTITY رد؛ config JS/TS/PHP صرفاً path+hash ثبت شود.
**4. Tests:** test_discovery_never_runs_package_scripts؛ test_nested_packages_use_nearest_root؛ test_runner_ambiguity_is_reported؛ test_php_xml_entity_is_denied؛ test_escaping_bin_path_rejected؛ test_missing_runtime_is_unavailable. fixture tmp_path JSON/PHP/JS متن، no dependency install. فرمان آینده:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_multilanguage_affected_tests.py tests/test_affected_tests.py
~~~
انتظار no Popen، bounded manifest، unknown صریح.
**5. Stop / compatibility:** manifest ambiguous (Jest و Vitest با هم بدون config روشن) => runner_required؛ حذف یکی یا اجرای همه خودکار ممنوع. Python3.10 بدون tomllib فقط marker/hash و missing_inputs=toml_parser_unavailable؛ نصب parser یا اجرای build-system برای discovery ممنوع.
**6. Order / impact:** خواندن inventory → metadata validation → runner classification → tests؛ هیچ tool schema هنوز عوض نشود.
**7. Evidence:** EVIDENCE-1، no-execution spies و package ownership؛ next P06-02.
<!-- TASK-P06-01-END -->

<!-- TASK-P06-02-START -->
<a id="task-p06-02"></a>
### TASK-P06-02 — انتخاب محافظه‌کارانه با provenance
**1. Identity / scope:** STATE_RECORD_ID=TASK-P06-02؛ tools/testing/impact.py، tools/testing/registry.py::affected_tests، tests/test_affected_tests.py و tests/test_multilanguage_affected_tests.py. helpers P04/P06 reused؛ sync public selection هیچ LSP process تازه‌ای روشن نمی‌کند.
**2. Proof pack:** signature فعلی select_affected_tests(repo:Path,*,changed_paths=None,base=None,max_files=5000,max_depth=5,max_results=500,include_changed_tests=True,fallback_policy="report",deadline=None)->dict؛ discover_changed_paths سه منبع staged/unstaged/untracked و rename را دارد. فعلاً scan فقط Python است و تغییر JS/PHP ممکن است decision=none بگیرد؛ regression باید همین را هدف بگیرد.
**3. Contract / algorithm:** پارامتر test_backend:Literal["python","auto"]="python" additive به helper و affected_tests؛ در هر دو mode وجود executable nonPython باید unknown_coverage بدهد، حتی اگر auto خاموش است. auto از inventory P06-01 و snapshot evidence P04 استفاده می‌کند؛ برای هر changedpath ownerpackage و oldpath rename را تعیین کن؛ دو root نتیجه جدا. import/reverse graph Python موجود را reuse؛ برای JS/PHP edge های LSP تنها positive evidence هستند و نبودن edge proof عدم impact نیست. اگر relationcoverage کامل نیست package full suite یا در dependency بین package نامشخص root full suites پیشنهاد شود. config/lockfile/dynamicimport/unknownlanguage/truncated/stalesnapshot => fallback محافظه‌کارانه. فقط docs-only با پوشش کامل decision=none؛ tests=[] و complete=false هرگز none قطعی نیست.
Result additive: package_selections[{package_root,runner,tests,evidence,confidence,complete,fallback_reason}], missing_inputs، base_sha/head_sha/worktree_id، source_fingerprint، total_exact. flat tests Python برای compat باقی؛ order stable package/path، max_results global، pagination/truncation صادقانه. graph memory cache فقط snapshot معتبر؛ JSON index corrupt discard، neverexact.
**4. Tests:** direct/transitive tests موجود حفظ؛ جدید test_javascript_change_without_edges_requires_full_suite، test_deleted_php_file_selects_package_suite، test_renamed_file_marks_old_and_new_packages، test_cross_package_dynamic_import_broadens_scope، test_unknown_runner_never_returns_all_ok، test_stale_snapshot_not_used، test_doc_only_remains_none. فرمان:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_affected_tests.py tests/test_multilanguage_affected_tests.py
~~~
assert coverage incomplete=>fallback_reason nonempty؛ explicitchangedfileoutside=>deny؛ allpaths نه فقط resolvedsubset fingerprint.
**5. Exceptions:** Git base invalid یا root اشتباه fail-safe با code، emptydiff موفقیتِ تست نیست. حذف/rename حتی missingfile باید به selection بماند. اجرای LSP برای تکمیل graph در این helper ممنوع؛ task فقط evidence آماده یا fallback.
**6. Order:** testrepro → normalizechanges → packagejoin → resultmerge → registryparameter → all existingfixtures. سایر consumers helper defaultPython دارند، conservativeunknown جدید باید P06-04 در verification مصرف شود.
**7. Evidence:** EVIDENCE-1؛ M06a closure و next03.
<!-- TASK-P06-02-END -->

<!-- TASK-P06-03-START -->
<a id="task-p06-03"></a>
### TASK-P06-03 — runner محدود و نتیجه قابل اعتماد
**1. Identity / scope:** STATE_RECORD_ID=TASK-P06-03؛ tools/testing/project_runners.py، tools/testing/runners.py، tests/test_multilanguage_runners.py جدید. هیچ shell abstraction یا package install اضافه نشود.
**2. Proof pack:** core/executor.py::run_bounded، tools/testing/runners.py::run_pytest_summary/run_ruff_summary/run_mypy_summary؛ return شامل ok/exit_code/timed_out/summary/diagnostics. run_bounded موجود مالک capture/process deadline است؛ test حتماً child-tree cleanup آن را حفظ کند.
**3. API / pseudo-diff:** build_runner_argv(spec:RunnerSpec,targets:list[Path],report:Path)->list[str] و run_project_tests(spec,*,targets,deadline,diagnostic_cap)->dict. argvlist، shell=False؛ runtime absolute approved، binary+entrypoint hash recheck قبل spawn؛ UTF-8/space/nonASCII path بدون interpolation. .cmd/.bat و npm/npx/composer script ممنوع؛ Node مستقیم entrypoint JS و PHP مستقیم vendor/bin script. فرمان مفهومی ثابت: pytest همان helper؛ unittest python -m unittest discover -s approved_testdir؛ Jest node+bin+--runInBand+--json+--outputFile+report+--runTestsByPath+targets؛ Vitest node+bin+run+--reporter=json+--outputFile+report+--maxWorkers=1+--no-file-parallelism+targets؛ PHPUnit/Pest php+bin+--log-junit+report و مسیر test مجاز. path با'-' با absolutecanonical safe می‌شود؛ برای PHP چند target که CLI نسخهپشتیبانی نمی‌کند per-filebounded invocation یا fullpackage، regexfilter ساختهنشود. Pest sequential default؛ no --parallel.
Version support را capability fixtures قفل کنند: localpackage version و entrypoint موجود شرط است؛ flags پشتیبانی‌نشده => unsupported_runner_version، نه fallback shell. پیش از استفاده flag های نسخه‌ای با مستند هماننسخه تطبیق بده؛ defaultprofile محصول تا realintegration سبز opt-in می‌ماند.
Report output private tmpdirowned، max4MiB، nofollow/rootcheck، JSONdepth16/list10000، XML DOCTYPE/ENTITYdeny؛ reportmissing/invalid یا exit0with0tests whenexpected>0 => required_unknown=true/ok=false. timeout/cancel childtreecleanup و no retry؛ executionpossible=>uncertainty طبق wrapper. diagnostics<=200 و scrubsecret، متن کامل به log نرود. totalbudget1800/stage600 در caller اعمال، argvcount<=1000 و charbudget32768Windows؛ overbudget=>fullpackageplan یا clearerror، نه silenttruncate.
**4. Tests:** fake executable با Pythonfixture به جای Node/PHP فقط contractargv/report/cleanup؛ test_argv_spaces_are_single_arguments، test_script_command_not_executed، test_missing_report_is_unknown، test_report_symlink_denied، test_no_tests_is_not_success، test_timeout_reaps_grandchild، test_cancel_has_no_retry. فرمان:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_multilanguage_runners.py tests/test_executor.py
~~~
**5. Source support / stop:** [Jest CLI](https://github.com/jestjs/jest/blob/main/docs/CLI.md)، [Vitest CLI](https://main.vitest.dev/guide/cli)، [PHPUnit JUnit output](https://docs.phpunit.de/en/12.5/textui.html)، [Pest sequential/parallel](https://pestphp.com/docs/optimizing-tests). این منابع شکل interface را تأیید می‌کنند، نصب/سازگاری runner واقعی این repo را نه. PHP/Node موجودنبودن=>UNVERIFIED، autoinstall نه.
**6. Order:** argvbuilderpure → validators → boundedexecutor → reportparser → fake subprocess tests. mutation/resource wrapper در caller P06-04؛ helperdirect نیز trusted spec و path revalidation می‌خواهد.
**7. Evidence:** exactversion/hash/argvredacted/exit/reportcount؛ EVIDENCE-1؛ next04.
<!-- TASK-P06-03-END -->

<!-- TASK-P06-04-START -->
<a id="task-p06-04"></a>
### TASK-P06-04 — یک semantics برای سه مسیر verification
**1. Identity / scope:** STATE_RECORD_ID=TASK-P06-04؛ STRONG_ONLY. tools/testing/verification.py، registry.py، background.py، scripts/verification_worker.py، core/workflow_actions.py؛ scripts/validation_watch.py defaultPython باقی. مدل پایگاه یا scheduler جدید ندارد.
**2. Proof pack:** graph trace exact verify_changed_repository inbound، callers سه scripts.verification_worker.run_request/core.workflow_actions._verify_changes/scripts.validation_watch.main؛ registry مستقیم source line367 نیز caller واقعی است که graph ندیده، بنابراین sourcegap ثبت می‌شود. build_verification_plan22 و verify_changed_repository85؛ VerificationRequest.options همهفیلدها بجز repo/fingerprint/maxparallel را پاس می‌دهد؛ VerifyChangesInput در core/workflow_actions.py47 و allowedset219 whitelist دارد.
**3. Contract / pseudo-diff:** test_backend:Literal["python","auto"]="python" به buildplan/verify/helper، دو tool verify_changes/start_verification، VerificationRequest و VerifyChangesInput اضافه؛ allowedsetworkflow و verificationworker kwargs به‌طورصریح test. Check literal قدیمی 4 مقدار ثابت بماند؛ انتخاب auto فقط stagepytest را برای package های Python نگه و package_test:<runner>:<root> stage برایبقیه append کند؛ callerchecks که pytest راحذفکرده اجازه testingextra نمی‌دهد. syntax/ruff/mypy فقط Python، scripts/validation_watch بدونپارامتر auto نشود. plan با unknownrunner یا incomplete selection unresolved=>required_unknowntrue؛ skipped stage ناشیاز missingrunnerunknown است، نه passed. ok = requiredchecksallpassed AND no requiredunknown؛ docsonlychecksnotrequired می‌تواند oktrue و tests_executedfalse بدهد، claimtestingfalse.
Publicdirect، worker و workflow همگی اصل claim coordination+root write subtree، sharedanalyzercapacity و deadline راحفظکنند. validate budget beforequeue. fingerprint فعلی background همه input های manifest/config/bin و explicitdeletedpath را پوششدهد؛ runnerbinary approval خارج repohash در request bounded reference جدا؛ secretspersist نشوند. options payload64KiB و event schema1 additive؛ oldrequest بدون test_backendPython. sourcechangedbefore/after worker=>stale، noeffectreplay.
**4. Tests:** tests/test_background_verification.py existingstale/stream/quota؛ tests/test_parallel_verification.py؛ tests/test_workflow_actions.py؛ tests/test_multilanguage_verification.py جدید: test_same_plan_direct_worker_workflow، test_auto_not_enabled_by_old_request، test_unknown_required_runner_fails_workflow، test_no_pytest_check_means_no_foreign_test_dispatch، test_shared_analyzer_capacity_is_global، test_stale_package_config_rejects_before_dispatch. فرمان:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_multilanguage_verification.py tests/test_background_verification.py tests/test_parallel_verification.py tests/test_workflow_actions.py tests/test_parallel_workflow_resources.py
~~~
**5. Stop:** این task چند entrypoint حساسدارد؛ اگر options فیلدجدیدگم شود، بازه checks ناسازگاریا resourceclaim نیازبهتغییر engine باشد reviewstrong؛ admission نبایدبه fakegreen حذفشود.
**6. Order:** verificationmodel → functionplan/result → registry → persistedrequest → worker → workflowwhitelist → tests و docs. callableguard در direct مسیرمستقلهم enforce شود. test_backend auto فقط opt-in؛ storedv1requestmigration نمی‌خواهد.
**7. Evidence:** SHA/diff+تمام caller assertions؛ EVIDENCE-1؛ next05.
<!-- TASK-P06-04-END -->

<!-- TASK-P06-05-START -->
<a id="task-p06-05"></a>
### TASK-P06-05 — پذیرش runner و compatibility
**1. Identity / scope:** STATE_RECORD_ID=TASK-P06-05؛ STRONG_ONLY، weight0؛ tests/docsgate برای M06b، نه feature جدید.
**2. Sources:** P06-01..04 evidence و fixtures P04؛ tests/test_affected_tests.py existingbaseline؛ currentsource ناهمخوان=>NEEDS_REVALIDATION.
**3. Acceptance procedure:** fake deterministic همه CI؛ localreal optional fixturesPythonpytest،Pythonunittest،JSJest،TSVitest،PHPPHPUnit/Pest با runtimeapproved و versionrecorded. direct/transitive/configchanged/deleted/monorepo/untracked/unknownrunner/0tests/crash/timeout؛ countselected نسبت groundtruth، false-negative0، extra-tests مجاز conservative. runner نصبنشده notmeasured و integrationgate همان runner بسته؛ featuredefault قدیمیبازبماند.
**4. Commands:** ابتدا tests/test_multilanguage_affected_tests.py، tests/test_multilanguage_runners.py و tests/test_multilanguage_verification.py همراه existingaffected/background؛ سپس fullrepositoryruff/mypy/compile و CI10check مطابق GATE-ALL. execution تستواقعی با argv ثبت‌شده P06-03 فقطدر repofixture و scope مجاز؛ no networkinstall.
**5. Stop:** test skip مهم، unknownpassed، cross-package miss یا leakedchild =>M06BLOCKED. توانایی executingforeignconfigs اثبات sandbox نیست.
**6. Order:** deterministic → realavailable → oldrequestcompat → documentationcurrentbehavior → checkpoint؛ performancebad نمی‌توان soundness راکمکند.
**7. Evidence:** EVIDENCE-1 با جدول runner/version/platform/fake/real/skipped؛ nextP08-01؛ P06 «کامل» فقطبرای runner هاییبا actualgate، بقیه capabilityunavailable باقی.
<!-- TASK-P06-05-END -->
<!-- PHASE-P06-END -->
<!-- CHUNK-10-END -->

<!-- PHASE-P07-START -->
<a id="phase-p07"></a>
## P07 — پذیرش DAG موجود و تصمیم فعال‌سازی مستقل

کد DAG در main ادغام شده و schema supported=11 است. flag پیش‌فرض False؛ env فرایند بررسی‌شده unset؛ configuration سرور زنده UNVERIFIED. این فاز بازنویسی DAG یا انتقال وضعیت از roadmap دیگر نیست. پذیرش گذشته با شواهد همان SHA حفظ می‌شود و برای تغییرات آینده دوباره gate متناسب اجرا می‌شود. DB عملیاتی در این مأموریت باز/upgrade نشده است.

<!-- TASK-P07-01-START -->
<a id="task-p07-01"></a>
### TASK-P07-01 — ماتریس پذیرش recovery موجود
**1. Identity / scope:** STATE_RECORD_ID=TASK-P07-01؛ STRONG_ONLY، پذیرش future، نه تغییر feature flag. EXISTING core/workflow_store.py و tests/test_workflow_migrations.py، tests/test_parallel_workflow_scheduler.py، tests/test_parallel_workflow_resources.py، tests/test_parallel_workflow_recovery.py. داده آزمایش فقط tmp_path/disposable DB.
**2. Proof pack:** SOURCE_BASE؛ schema11 و PR81/mainchecks موجود؛ tests migration v9_capacity/v10_partial/v11_policy دقیق در فایل موجود. core.workflow_actions._verify_changes هماهنگی resource در مسیر sequential را دارد؛ parent/child ownership و unknown effects invariant مشترک‌اند. baseline drift => فقط flow تغییرکرده دوباره trace.
**3. Procedure / acceptance map:**

| خطر | فایل/نماد موجود یا assertion لازم |
|---|---|
| cycle/order/cap/fairness | test_parallel_workflow_scheduler.py::test_dag_scheduler_respects_cap_and_join_dependencies، cap_one_remains_serial؛ test_workflow_dag.py برای cycle |
| atomic resource admission | test_global_admission_limit_is_atomic_across_workflows؛ test_resource_storage_denial_rolls_back_all_claims_and_prevents_dispatch |
| file exact/subtree/Git common-dir | test_parallel_workflow_resources.py؛ conflictfixtures دو worktree باید یک common-dirclaim بگیرند |
| cross-process child ownership | test_workflow_children_require_dispatch_barrier_and_native_ownership؛ realchildretirement باهویت createdtime |
| crash before/after dispatch | test_parallel_workflow_recovery.py::test_real_executor_process_death_keeps_all_effects_without_replay؛ test_deadline_consumed_by_intent_prevents_action_dispatch |
| late result/retry | test_late_attempt_result_cannot_overwrite_settled_operation؛ test_descriptor_retry_policy_controls_automatic_replay |
| unknown/quarantine/resume | test_restart_recovers_every_branch_and_keeps_possible_effect_fenced؛ test_queue_transition_rechecks_all_replay_blockers_atomically |
| old DB/migration | v9/v10/v11tests؛ retain rowcounts/hashes/uncertain ownership؛ schema جدیدتر از supporteddeny بدون write |
| cancel/drain/restart | test_workflow_cancellation.py، test_workflow_crash_recovery.py، test_workflow_lease_heartbeat.py |
| pressure/retention/analyzers | test_admission_record_quota_preserves_unresolved_fences، test_parallel_verification.py؛ fullquota denybeforedispatch |
| same-SHA platform | GATE-ALL: Windows/Linux/macOS + packaging، نه صرفاً artifacttargets |

**4. Future commands / assertions:**
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_workflow_migrations.py tests/test_workflow_dag.py tests/test_parallel_workflow_scheduler.py tests/test_parallel_workflow_resources.py tests/test_parallel_workflow_recovery.py tests/test_workflow_cancellation.py tests/test_workflow_lease_heartbeat.py
~~~
قبل/بعد migration روی fixture rowcount، ownedresources، journaluncertainty و schema_version assert؛ crash afterdispatch شمار sideeffect حداکثر 1 و stateuncertain تا reconciliation. snapshot backup با SQLite backupAPI فقط fixture؛ rollbackfilecopy بعد بسته‌شدن connection ها و نبود writer، نه کپی WAL زنده. اگر مورد فوق coverage ندارد ابتدا regression محدود و مستقل برای review پیشنهاد/ثبت شود؛ شاهد وجود test جای اجرای آن نیست.
**5. Stop:** live DB نامعلوم/سرور writer فعال، ownershipunavailable، migrationpartial، skipped platformcritical، unresolvedeffect => BLOCKED؛ فعال‌سازی یا automaticretry ممنوع. خطای تاریخی fixed دوباره به‌عنوان defect current ثبت نشود.
**6. Order:** source/versioncheck → disposable migration → focused recovery → remainingplatform → independentreview. تغییر کد برای شکست جدید نیازمند scope همان remediation آینده است؛ این plan-only نوبت آن را انجام نمی‌دهد.
**7. Evidence:** EVIDENCE-1 + matrix OS/check/status؛ nextP07-02، NO-GO تا گیت مستقل.
<!-- TASK-P07-01-END -->

<!-- TASK-P07-02-START -->
<a id="task-p07-02"></a>
### TASK-P07-02 — تصمیم GO با snapshot عملیاتی
**1. Identity / scope:** STATE_RECORD_ID=TASK-P07-02؛ STRONG_ONLY؛ تصمیم عملیاتی، weight0. source field core/config.py::Settings.workflow_dag_enabled و .env.example را فقط پس از مجوز مستقل deployment تغییر بده؛ مجوز مستقیم فعال‌سازی محلی کاربر در Ledger2026-10-26 ثبت و پذیرش فنی کامل شد؛ پیش‌فرض عمومی و مجوز release تغییر نمی‌کند.
**2. Source / missing evidence:** P07-01، roadmap DAG موجود، tests/reflection actual SHA؛ live user_version، activeworkers، unresolvedleases، performancebudget و operator rollout window هنوز UNVERIFIED.
**3. Proposed decision contract:** DagActivationDecision={schema_version:1,subject_sha,config_digest,db_schema,required_checks,backup_reference,unresolved_count,independent_review,authorization_reference,decision:go/no_go,reasons}. فقط reference/hash، نه secret. GO iff sameSHA10/10، fixture+nativeownership pass، unresolvedcount0 یا isolationreview مصوب برایهرمورد، backuprestorerun، rolloutdeadline و rollbackplan موجود. خاموش‌کردن flag admission جدید را می‌بندد؛ ongoing/uncertain jobs حذف نمی‌شوند. disable هرگز activeprocess را بدون reconciliation رها نکند.
**4. Acceptance:** fixture decision برای missingcheck/pending/skipped/wrongSHA/schemafuture/unresolved =>no_go؛ همه conditions+authorization =>go فقطپیشنهاد، mutationconfig جدا. Proposed tests/test_dag_activation_gate.py فقط اگر تصمیم بعداً به code تبدیل شد؛ امروز runbook review کافی و تست جدید لازم نیست.
**5. Stop:** هر Unknown یا approval گمشده؛ maingreen به‌تنهایی GO نیست. downgrade DB زنده یا force deletion fence مجاز نیست.
**6. Order:** read-onlysnapshot → reviewbackuprestore → reviewsameSHA → operatorauthorization → stagedactivation → health/uncertainty مشاهده →rollbackadmission در failure. هیچ backgroundmonitor در اینمأموریت ساختهنشود.
**7. Evidence:** BLOCK-DAG-GO تااطلاعات واقعی؛ P13 به GO نیاز دارد، P01..P06 ندارد.
<!-- TASK-P07-02-END -->
<!-- PHASE-P07-END -->

<!-- PHASE-P08-START -->
<a id="phase-p08"></a>
## P08 — مدل خروجی با حفظ wire contract

M08a=01/02؛ M08b=03/04. قراردادهای ضروری context/LSP قبلاً در P0 هستند؛ این فاز سراسری جلوتر از P0 اجرا نشود. اصل: shape خروجی، optional بودن، absent دربرابر null و متن error قدیمی ثابت؛ مدل typed فقط فیلدهای واقعی را توصیف/اعتبارسنجی کند. core/response.py::failure فعلی {ok:false,operation,error,message,hint?} است؛ error.code nested ساختگی اضافه نشود.

**قرارداد پیشنهادی مشترک:** core/response_models.py::NativeFailure با ok:Literal[False]، operation:str، error:str، message:str، hint:str|None اختیاری با exclude_unset/exclude_none مطابق shape قدیم. code های جدید هر domain Literal محدود، code های قدیمی str برای compat. extra="allow" برای envelopelegacy تا فیلد قدیمی حذف نشود؛ submodel جدید extra="forbid". schema version فیلدفعلی اگر هست required همان مقدار، وگرنه versionmetadata در JSONschema قرار گیرد، نه تزریق اجباری payloadfield. dictreturn فعلی به model_validate→model_dump(mode="json",exclude_unset=True) از همان boundary موجود عبور کند. در verify نتیجه ok=false با stages یک نتیجه آزمون شکست‌خورده است و NativeFailure نیست؛ union براساس requiredstructure انجام شود نه فقط ok.

SDK mcp2.0.1 در snapshot همه 151tool را genericobject نشان می‌دهد. تست protocol باید ثابت کند schema typed باعث اضافه‌شدن wrapper "result" یا تغییر content/structuredContent نشده؛ در صورت wrapper اجباری، schema rollout همین task BLOCKED و runtime مدل داخلی حفظ شود، نه شکستن API. $defs مشترک و no duplicateddescription؛ بودجه بازنگری‌شده P08 در2026-10-26 با مجوز صریح کاربر برای تغییر مستدل: سقف رشد20% برای full حفظ می‌شود؛ همان افزایش مطلقِ مجاز برای coding/coding_core نیز اعمال می‌شود، چون همان ابزار باید schema یکسان داشته باشد و هزینه ثابت تعاریف نباید به کوچک‌بودن profile وابسته شود. Baselineهای frozen: full161424،coding100462،coding_core47156byte؛ سقف افزایش برای هر profile=floor(0.20×161424)=32284byte. برای توکن نیز افزایش هر profile حداکثر7487cl100k_base و7882o200k_base، معادل20% baselinefull37436/39414token. serializer فشرده UTF8/ensure_asciiFalse/sort_keysTrue ثابت؛ درصد واقعی هر profile گزارش شود. تعداد، names/input/annotations/descriptions و سایر metadata بدون output_schema باید IDENTICAL بماند؛ schema هر ابزار مشترک در profileها IDENTICAL و constraints/wire برابر بمانند. no tokenizer dependency در runtime؛ اندازه‌گیری با tiktoken0.7.0 موجود و vocabularyhashهای ثبت‌شده، نه billed/client prompt usage. سقف پس از شکست تست تغییر نکند؛ شکست این معیارهای پیش‌ثبت‌شده همچنان BLOCKED است.

<!-- TASK-P08-01-START -->
<a id="task-p08-01"></a>
### TASK-P08-01 — context و affected-tests
**1. Identity / scope:** STATE_RECORD_ID=TASK-P08-01؛ core/response_models.py جدید، tools/project/intelligence_models.py از P04، tools/testing/models.py جدید، registry های project/testing فقط annotation/resultvalidation؛ tests/test_typed_output_contracts.py جدید.
**2. Proof pack:** context.lookup_code_context/result از P01/P04 و impact.select_affected_tests/resultP06؛ publiccode_context و affected_tests موجود؛ core/tooling.py::compact_errors functools.wraps و signature حفظ می‌کند، شکستبایدهمان failureenvelope بماند.
**3. Proposed models / patch:** CodeContextResult={ok:bool,symbol,definitions,callers,callees,references,imports,tests,pagination,complete,semantic_complete,snapshot,...legacyextras} با reuse EvidenceEdge نه model دوم؛ AffectedTestsResult={decision,tests,complete,scan_truncated,package_selections?,missing_inputs?,fallback_reason?,...}. فیلدهای 실제fixtureP01/P06 authority؛ requirement فیلد تازه برای oldoutput نشود. ابتدا NativeFailure، سپس TypedDict/Pydanticreturn union با method-specificrequiredkeys و protocoltest؛ هیچ mutationguard تغییر نکند. boundslist از toolinputcaps؛ count>=0، total>=count فقطوقتی total_exacttrue، incomplete دلیل required.
**4. Tests:** test_output_schema_describes_context_fields؛ test_context_error_uses_legacy_failure_shape؛ test_missing_required_field_fails_internal_validation؛ test_unknown_evidence_rejected؛ test_output_roundtrip_preserves_optional_absence؛ test_sdk_does_not_wrap_output؛ snapshot هازمان/runtimeidsnormalize ولی status حفظ. فرمان:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_typed_output_contracts.py tests/test_code_context.py tests/test_affected_tests.py tests/test_multilanguage_affected_tests.py
~~~
**5. Stop:** scalarinsteadlist، schemawrap، oldfielddrop، genericfallback بدون reason=>review. خطای validation provider/sourcepayload به message چاپنشود؛ safe output_contract_violation.
**6. Order:** fail-shape test → model → annotation → protocolserialization → compatibility؛ noautomaticglobalwrapperrewrite.
**7. Evidence:** before/afterwire snapshot و schema bytes؛ next02.
<!-- TASK-P08-01-END -->

<!-- TASK-P08-02-START -->
<a id="task-p08-02"></a>
### TASK-P08-02 — search_files و project_summary
**1. Identity / scope:** STATE_RECORD_ID=TASK-P08-02؛ tools/filesystem/models.py جدید، tools/project/intelligence_models.py یا models.py فقط اگر domainmodel موجودنامتعارضدارد؛ filesystem.registry.search_files و project.registry.project_summary؛ tests/test_typed_output_contracts.py.
**2. Proof pack:** search_files در registry300 چند return برای snapshot/streaming/error؛ service.project_summary202 فیلد path,files_scanned,scan_truncated,languages,frameworks,dependency_count,dependencies,dependencies_truncated,entry_points,top_level دارد. schema باید variants واقعی search snapshot راپوشش دهد، نهفقط firstpage.
**3. Patch:** SearchFilesResult union با existingmode/fieldpresence، items با path/line/snippet واقعی، pagination/cursornullable، stale snapshotfailure همان code؛ ProjectSummaryResult با countnonnegative و lists حدودفعلی (languages8/frameworks15/dependencies30/entrypoints20/top_level30)، extraslegacyallow. لاگ/selection از modeldump بدونحذف data؛ newtop_levelcap نداشته codechange نگیرد. snapshotcontinuation تغییرنکند؛ zeroextension/nativecatalog تکرارپذیر.
**4. Tests:** first/next/empty/stale/limitunicode search fixtures موجود tests/test_filesystem_registry.py؛ summaryempty/mixedlanguages/parsefailure fixtures؛ parameterized wirebeforeafter equality. فرمان:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_typed_output_contracts.py tests/test_filesystem_registry.py tests/test_project_cache.py
~~~
**5. Stop:** returnbranch بدون coverage یا sourcefield نامعلوم؛ متنجستجو credentials ممکنستباشد، snapshottestdummycanary و no log؛ تغییر resultsorting خارج scope.
**6. Order:** enumeratebranchshapes از AST/read → fixtures هر branch → models → annotations → SDKcatalog؛ preexistingpaneldiff نه.
**7. Evidence:** branchcoverage map و compatibility؛ next03.
<!-- TASK-P08-02-END -->

<!-- TASK-P08-03-START -->
<a id="task-p08-03"></a>
### TASK-P08-03 — job_status، workflow_status و server_health
**1. Identity / scope:** STATE_RECORD_ID=TASK-P08-03؛ مدل‌ها در core/response_models.py با reuse enum های jobs/workflow؛ tools/jobs/registry.py::job_status، tools/workflows/registry.py::workflow_status، core/registry.py::server_health؛ tests/test_typed_output_contracts.py.
**2. Proof pack:** job_status50 {ok:true,**store.get(job_id)}؛ workflow_status140 مستقیم workflow_store(...).get؛ healthcollect async با bound_runtime_health512KiB و snapshot_schema_version/runtime_generation. health fallback ممکنستفقط ok:false/health_status/snapshot_truncated/snapshot_reason داشته باشد و NativeFailure نیست.
**3. Patch:** JobStatusResult/WorkflowStatusResult همان status enums و version/lease/stepfieldsexisting؛ optional rawspec حذفنه، redactedexistingpreserve. HealthResult=FullHealth|BoundedHealth|NativeFailure با discriminator فیلدهایواقعی، fullsnapshot_schema_version1 و timestamp/monotonicfinite، fallbackreasonpayload_limit. modelvalidate بعد sanitization/bounding، نهقبلبرای validate کردن secret. readpathshealth هیچ DB/provideractivation ازمدل‌سازیایجادنکند. unknownfuturestatus از persistedstore=structuredunsupportedversion، نه successmap.
**4. Tests:** empty/pending/running/failed/uncertain/cancelled fixturesjobsworkflow؛ healthfull/summary/unavailable و stalegeneration موجود tests/test_health_snapshot_runtime.py؛ canary در args/errorneverleaks. فرمان:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_typed_output_contracts.py tests/test_jobs_registry.py tests/test_workflow_tools.py tests/test_health_snapshot_runtime.py tests/test_extension_health.py
~~~
**5. Stop:** وضعیت uncertain به failed/success تبدیلشودیا healthpollactivation=>stop؛ schemachange برایکوتاه‌کردن status نه.
**6. Order:** existingenumreuse → nullableaudit → boundedvarianttests → registryannotate؛ JobStore/WorkflowStore migration ندارد.
**7. Evidence:** statebyvariantsnapshot و bytes؛ next04.
<!-- TASK-P08-03-END -->

<!-- TASK-P08-04-START -->
<a id="task-p08-04"></a>
### TASK-P08-04 — verify_changes، language_diagnostics و execution_recommend
**1. Identity / scope:** STATE_RECORD_ID=TASK-P08-04؛ tools/testing/models.py، tools/language/requests.py، core/response_models.py؛ registry های مربوط؛ tests/test_typed_output_contracts.py.
**2. Proof pack:** verification._stage/status/verify_changed_repository از 85؛ language_diagnostics common("textDocument/diagnostic")؛ execution_recommend244 به ExecutionRouter.recommend(intent,explain,max_candidates) می‌رود و executor نیست.
**3. Patch:** VerificationResult requiredok:bool/stages/mode/required_unknown/duration_ms؛ StageStatus=passed/failed/skipped/timed_out/unavailable و skipreasonrequired، checkfailed!=transportfailure؛ DiagnosticsResult همان normalizedP03 با range UTF16، severity،sourcebounded، incomplete/pullunchangedreport تفاوتحفظ؛ RouteRecommendation مدل return واقعی Router با blocked/recommended/alternatives/explanation هایوجوددار و permissiongranted فیلدتازهساختهنشود. no public execute handler. تمام annotationsunionlegacyerror؛ runtimediagnostics sanitizebeforemodel.
**4. Tests:** test_failed_verification_remains_valid_result، test_unknown_is_not_passed، test_diagnostics_partial_report_is_incomplete، test_router_output_cannot_authorize_dispatch، full10toolsschemavalidationcatalog; tests/test_execution_router.py، tests/test_language_registry.py، tests/test_parallel_verification.py. فرمان:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_typed_output_contracts.py tests/test_execution_router.py tests/test_language_registry.py tests/test_parallel_verification.py
~~~
**5. Stop:** rawLSPresult به typedoutputcast بدون validation، providerexceptionecho یا implicitpermission=>fail. schema قراردادی context نباید متناقض با P03/P04 باشد.
**6. Order:** models → errorvariants → wrappers → schema/bytepilot → fullgatesM08 و docs؛ publicdefault در صورتی wirecompatible. 10tooltyped به‌معنی 151tooltyped نیست.
**7. Evidence:** 10toolmatrixbefore/after، protocolfixture، schemafull/coding bytes، EVIDENCE-1؛ M08closure.
<!-- TASK-P08-04-END -->
<!-- PHASE-P08-END -->
<!-- CHUNK-11-END -->

<!-- PHASE-P09-START -->
<a id="phase-p09"></a>
## P09 — GitHub از مسیر gh و گیت‌های هویت

<!-- TASK-P09-01-START -->
<a id="task-p09-01"></a>
### TASK-P09-01 — runbook خواندنی و چرخه مجاز آینده
**1. Identity / scope:** STATE_RECORD_ID=TASK-P09-01؛ VERIFIED فقط برای audit و runbook این سند. محصول جدید ندارد. tools/git/registry.py::git_status/git_diff/git_stage/git_commit/git_restore_file موجودند؛ push/PR/release عمومی در آن registry نیست. gh نصب/احراز هویت/خواندن repo در این نشست موفق بوده؛ مقدار token ثبت نشده.
**2. Proof pack:** SOURCE_BASE؛ PR81 merged روی e66c8c9... با head379e9e5...؛ gh read-only 2026-10-19 reviewDecision=""، reviews=[]، requestedReviews=0، main protected=false. بنابراین «10 گیت لازم» در این سند الزام قرارداد پروژه است؛ تضمین enforcement حفاظت branch یا دریافت review انسانی از GitHub نیست. repo settings تغییر نمی‌کند. gh API 403/404 در آینده=UNKNOWN، نه green.
**3. Read-only commands / exact identity:**
~~~powershell
$repo = 'dibbed/computerpilot-mcp'
git status --short
git diff --check
git diff --stat
git diff --cached --stat
git branch --show-current
git rev-parse HEAD origin/main
gh api "repos/$repo/commits/main" --jq '.sha'
gh pr list --repo $repo --state open --json number,headRefName,headRefOid,baseRefName
gh pr view 81 --repo $repo --json number,state,headRefOid,mergeCommit,statusCheckRollup
gh run list --repo $repo --commit e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 --limit 20 --json databaseId,workflowName,event,headSha,status,conclusion
gh run view 39131440536 --repo $repo --json headSha,status,conclusion,jobs
~~~
برای PR آینده از branch واقعی و داده JSON استفاده کن، نه PR81:
~~~powershell
$repo = 'dibbed/computerpilot-mcp'
$branch = (git branch --show-current).Trim()
$localHead = (git rev-parse HEAD).Trim()
$prs = @(gh pr list --repo $repo --state open --head $branch --json number,headRefOid,baseRefName | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $prs.Count -ne 1) { throw 'STOP: expected one open PR for this branch' }
$pr = $prs[0]
if ($pr.headRefOid -ne $localHead -or $pr.baseRefName -ne 'main') { throw 'STOP: local/PR identity mismatch' }
gh pr checks $pr.number --repo $repo
gh run list --repo $repo --commit $localHead --limit 50 --json databaseId,workflowName,event,headSha,status,conclusion
~~~
Expected سه workflow با latestattempt برای currenthead و job های GATE-ALL است؛ pending/queued/in_progress=WAIT؛ failure/cancelled/timed_out/action_required=FAIL؛ skipped/neutral به‌جای check لازم PASS نیست؛ absence=NOT_TRIGGERED. PRevent ممکن است checkout merge-ref داشته باشد؛ headRefOid authority کد PR، workflowpayload/checkout SHA را جدا ثبت کن، syntheticmergeSHA را با main اشتباه نگیر. دوباره push کردن branch برای retrigger بدون مجوزنه.
**4. Future authorized cycle / acceptance:** ابتدا task localchecks → selective reviewedpaths → Conventional Commit → push branch → create/updatePR → 10checkcurrenthead → review → merge با headguard → mainnewSHA10check. workflow های فعلی فقط push main و pull_request main trigger دارند؛ branchpush بدون PR CI راه نمی‌اندازد. اگر PR وجودندارد و مجوز PR نیست، status=BLOCKED:PERMISSION_PR؛ workflowdispatch/rerun جایگزین مجوز نیست. gh run view واقعی failedID --log-failed برای همان OS، سپس focusedfix و newSHA؛ lograwsecret قابل‌چاپنیست. watch طولانی در agent به بازه<=60s/pollcompact با backoff تقسیم شود، UI بدون update رها نشود.
Preflightstaging: git diff و cached و untracked را جدا بخوان؛ فقط فایل‌های task، هرگز git add -A. panel/index.html و uv.lock فعلی از staging خارج؛ secrets/db/log/.agent_state/graphify-out/این MasterPlan ممنوع. example future branch fix/code-context-pagination و subject fix(project): correct relationship pagination؛ نام نباید نویسندگی خودکار القا کند. git common-dir claim برای کارهمزمان reuse شود. branchbase بعداجازه از origin/main current؛ oldverifiedwork را reset نکن.
PRbody با UTF8 temp file خارج trackedpaths و gh pr create --body-file یا gh pr edit --body-file؛ متن روشن problem/behavior/validation/risks. approval و merge به scope جدا؛ gh pr merge --match-head-commit currentSHA فقطبعددهگیت و review مجاز. publish فقط docs/release-process.md exactsubject و guardedworkflow؛ دستی tag/releasecreateneitherfallbacknorrepair. rollback با revertreviewedcommit وگیت‌ها؛ immutablev0.8.0 جابجانشود.
**5. Stop:** authmissing/wrongrepo/dirtyunreviewedstage/stalehead/requiredcheckmissing/CIbudgetapproval =>permissionrecord و STOPmutation؛ خواندن و تکمیل سند ادامه دارد. موفقیت Publishworkflow با publish=false انتشار release نیست. forcepush/rebaseblind/clean/reset ممنوع.
**6. Order / no new API:** native Git برای local state و gh برای hostedstate کافی است؛ TypedGitHubExtension تنها با gap جدید، همان externalpolicy و approval، نه duplicateGitHubclient. acceptance خواندنی امروز head/run/PR تطبیق داده شد؛ mutation آینده اجرا نشده.
**7. Evidence:** SRC-GIT/SRC-PR81/CI-MAIN و resumecheckpoint؛ taskVERIFIED صفر وزن محصول؛ nextdependency ندارد.
<!-- TASK-P09-01-END -->
<!-- PHASE-P09-END -->

<!-- PHASE-P10-START -->
<a id="phase-p10"></a>
## P10 — Visual Grounding مکمل، پس از تحلیل کد

<!-- TASK-P10-01-START -->
<a id="task-p10-01"></a>
### TASK-P10-01 — قرارداد capture و تبدیل مختصات، offline
**1. Identity / scope:** STATE_RECORD_ID=TASK-P10-01؛ DEFERRED، قابلیت امروز ندارد. EXISTING tools/desktop/registry.py::desktop_screenshot128، mouse_click177، native.py::click_mouse169؛ browser.registry.browser_snapshot261 و browser_click_semantic350. PROPOSED tools/desktop/grounding_models.py و tests/test_grounding_coordinates.py، بدون کلیک واقعی.
**2. Proof pack:** screenshot ورودی path/all_screens/region/image_format/quality/delivery وخروجی image metadata؛ mouseclick ورودی x,y/button/clicks/interval_ms و raw native dispatch. این paths در snapshot هیچ frameid/DPI generation argument ندارند. UIA و semanticbrowser مسیرترجیحی‌اند.
**3. Proposed model / algorithm:** CaptureFrame={schema_version:1,frame_id,sha256,captured_monotonic,runtime_generation,window_id,process_id,process_created,monitor_ids,physical_bounds,content_bounds,pixel_size,coordinate_space,image_to_physical:[6floats],display_generation,focus_generation}. bounds از APIreal، نهاندازه image فرضی؛ crop/resize affinetransform ثبت شود؛ negative monitororigin مجاز. pure map_point(frame,u,v)->PhysicalPoint؛ rejectNaN/Inf/outsidecrop/zeroarea/singularmatrix. invertroundtrip خطا<=0.5physicalpixel؛ permonitortransform برای mixedDPI، یک scaleglobal حدسی نه. cache حداکثر 4frame/64MiB/TTL5s، no imagepersistdefault؛ audit فقط hash/metadata و retention موجودبرای artifactexplicit.
**4. Offline tests:** parameterize 96/144/192DPI metadata، two-monitornegativeorigin،cropoffset/resizedimage، rotatedboundsunsupported=>deny، windowmoved/displaygenerationchanged/focuschanged/staleage=>deny. test_roundtrip_coordinates، test_cross_monitor_transform_requires_correct_segment، test_stale_frame_never_yields_dispatch_token. فرمان آینده فقط پس از implementation مجازی:
~~~powershell
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_grounding_coordinates.py
~~~
هیچ mouse/keyboard/PILlivecapture در test؛ platformfixturefake.
**5. Stop:** metadata کافیاز backend نیست یا calibrationhardwareunknown =>DESIGN-BLOCKER-02. مختصات تخمینی وارد mouse_click نشود. PortableCoreimport با WindowsAPIdependency نبایدبشکند.
**6. Order:** typedmetadata → puretransform → offlinefixtures → realcalibration جدا در scope مصوب؛ اضافه‌کردن optionalframedata به screenshot فقطبعد stage2 بدونشکستن legacy. publicclicktool جدیددر این task نیست.
**7. Evidence:** transformmatrix/fixturegroundtruth و limitation؛ no runtimecapabilityclaim؛ nextP10-02blocked.
<!-- TASK-P10-01-END -->

<!-- TASK-P10-02-START -->
<a id="task-p10-02"></a>
### TASK-P10-02 — grounding و dispatch با مجوز، DESIGN-BLOCKER-02
**1. Identity / scope:** STATE_RECORD_ID=TASK-P10-02؛ DESIGN_BLOCKED. groundermodel/provider، dataegresspolicy، calibrationnativebackend و postconditiontype نامعلوم؛ امروزفقطقرارداد.
**2. Source:** mouse_click/currentnative_uiamutationprotocol و core/recovery.py uncertainty؛ Routerhardconstraints قبل ranking؛ no LLMcallbuiltin.
**3. Conditional contract:** GroundCandidate={frame_id,target_description_hash,box_in_image,confidence,alternatives,evidence_kind}; validatedtarget شامل samewindow/processcreated/generation، signedopaqueleaseTTL2s و expectedpostcondition. pipeline Capture→Normalize→Ground→validateidentity/freshness/ambiguity→policy/explicitpermission→nativeowneddispatch→postcondition→audit/recovery. DOM/UIAcandidate با exactlocator اول؛ pixels فقط rawallowed و semanticunavailable، ambiguity>1 یا confidence غیرکالیبره=>failclosed. destructive/sensitiveaction بایددرمجوزرویداد خاص صریح پوشش داده شود؛ hiddenconfirmation ازمتن provider نه. requestid/hashintentidempotency مانع replay، lostresponseafterinput=>uncertain، reobserve قبل هر تصمیم تازه.
**4. Acceptance design:** proposedtests/test_visual_grounding_policy.py؛ deniedpermission/spoofedwindow/reusedpid/staleframe/movedwindow/multicandidate=>nativecallcount0؛ afterdispatchtimeout=>journaluncertain/noautoclick؛ successpostconditionrequired؛ cancellation beforedispatchsafe، afterdispatchuncertain. server/contentpromptinjection ورودی data است.
**5. Stop:** DESIGN-BLOCKER-02 شاملانتخاب provider+privacy+calibration+postcondition و review خطر؛ softwaretests جای multi-monitorhardwareevidence نیستند.
**6. Order:** رفعجزئیات design → خردکردن implementation → offlinepolicy → isolatednativecalibration → opt-in؛ اینسندهیچکدامرافعال نمی‌کند.
**7. Evidence:** missinginputs در Blockerregistry، P0 مسدودنمی‌شود.
<!-- TASK-P10-02-END -->
<!-- PHASE-P10-END -->

<!-- PHASE-P11-START -->
<a id="phase-p11"></a>
## P11 — کشف AGENTS در پروژه مقصد، اختیاری

<!-- TASK-P11-01-START -->
<a id="task-p11-01"></a>
### TASK-P11-01 — فعلاً ابزار تازه نساز
**1. Identity / scope:** STATE_RECORD_ID=TASK-P11-01؛ DEFERRED. این host همین‌اکنون AGENTS را از کاربر/فایل دریافت می‌کند؛ server نباید متن repository رابه authority بالارتبدیلکند. tools/project/service.py::iter_files و filesystem.read_file ابزار خواندن موجودند.
**2. Source / value gate:** gap اضافی نسبت به host اثبات نشده؛ ابزار AGENTS شرط LSP نیست. دستورخواندن AGENTS این repo با featuretargetrepo متفاوتاست.
**3. Conditional interface:** فقطاگر clientopt-in واقعیخواستهشد discover_project_instructions(root:Path,*,target_relative_path:str,max_files:int=32,max_bytes:int=262144)->InstructionInventory. boundedread-only، canonicalroot+ancestors از root تا parenttarget، noancestor خارج root، nestedscope از relativeparent. item={path,scope,sha256,encoding:"utf-8",truncated,trust:"repository_data",text?}; invalidencodingreport، no auto decode باحدس. no persistcache ضروری، digestinvalidate اگر cache اضافهشد. conflictingrule هابه client دادهشود؛ server هیچ command/policy/env ازمتن اجرا/تغییرندهد.
**4. Acceptance conditional:** missing/nested/conflict/maliciousinstruction/UTF8/largefile/symlinkescape/stalehash fixture؛ maliciousinstruction باید data بماند و dispatchspy0. proposedtests/test_instruction_discovery.py تنهابعد انتخاب feature؛ امروز code/testnew ندارد.
**5. Stop:** درخواست خودکاراجرای AGENTS یا overrideuser/system=>deny؛ symlinkrootescape و quota=>boundederror؛ nofeaturecreep در P01.
**6. Order:** نیاز client → ADRvalue → contract/review → read-onlyfunction → toolschema/metadata؛ scope تغییر نیافتهامروزدلیلشروعنیست.
**7. Evidence:** تصمیم defer ثبت؛ اگرهرگزنیازاثباتنشد همین no-new-code تصمیم نهایی feature است، implementationprogress صفرنشان‌دهنده backlog اختیاریاست.
<!-- TASK-P11-01-END -->
<!-- PHASE-P11-END -->

<!-- PHASE-P12-START -->
<a id="phase-p12"></a>
## P12 — runtime ابزار می‌ماند؛ controller در host

<!-- TASK-P12-01-START -->
<a id="task-p12-01"></a>
### TASK-P12-01 — مرز decision loop و بودجه
**1. Identity / scope:** STATE_RECORD_ID=TASK-P12-01؛ DEFERRED. backend خودمختار جدید، مدل APIkey، scheduler جدید یا execution_run در scope نیست. core/registry.py::execution_recommend و core/execution_router.py::ExecutionRouter.recommend advisory باقی.
**2. Proof pack:** tools/workflows/builtins.py::builtin_workflow(name:str,parameters:dict)->WorkflowDefinition؛ implement_and_verify فقط git_status و verify_changes رویتغییرقبلاًانجام‌شده دارد وکدنویسیخودکارنمی‌کند؛ safe_git_commit selectivepaths می‌خواهد؛ prepare_release validation است؛ deploy_and_healthcheck صریحاً deployment_adapter_required می‌دهد ودر BUILTIN_NAMES سه‌تایی نیست.
**3. Chosen contract:** hostcontroller چرخه understand→inspect→propose→authorizedexecute→verify→adjust/stop راصاحب می‌شود. runtimepolicy/typedexecution/state/recovery owner می‌ماند؛ contextmanagerhost فقط references+hash/generation و summary نگه می‌دارد. ControllerBudget={max_tool_calls:20,max_elapsed_sec:900,max_mutation_attempts:1,max_adjustments:2,token_budget:explicit_or_null} پیشنهاد default؛ tokenbudget بدون مجوزحسابکاربرفرضنمی‌شود. هر steprequirescurrentevidence+permissionscope؛ recommendresult دلیل انتخاب است، executiontoken نیست. HALT در uncertain/stalesnapshot/unresolvedpermission/۲بار failure مشابه/budgetexhaustion؛ rollback خودکار mutationunknown نه.
**4. Deterministic evaluation design:** 20fixturetask با expectedallowedroute و forbiddentool؛ fakecallertranscript برای wrongtool/falsecompletion/budgetoverrun؛ sourceupdatedmidloop=>reinspect؛ failedtest=>boundedadjust؛ uncertainwrite=>STOP. proposedtests/test_controller_contract.py فقطاگر controllerintegration مصوبشد؛ runtimebuiltin موجود tests/test_builtin_workflows.py بدون modelnetwork acceptance فعلی.
**5. Stop:** درخواست runtime برای reasoningnested یا autoapprovepermissions=>scopechange و strongreview. کاستن latency با skipverification مجازنیست.
**6. Order:** hostprotocol بهصورتخارج runtime تعریف → fakeeval → boundedclientintegration؛ از workflow های موجود reuse، نه truth دومبرای jobs/evidence.
**7. Evidence:** تصمیم backend-only امروزقطعی؛ featurecontrollerdeferred، P13 به controllerauthorization/detail وابسته.
<!-- TASK-P12-01-END -->
<!-- PHASE-P12-END -->

<!-- PHASE-P13-START -->
<a id="phase-p13"></a>
## P13 — delegation آینده، خارج از DAG ثابت

<!-- TASK-P13-01-START -->
<a id="task-p13-01"></a>
### TASK-P13-01 — قرارداد delegation و isolation، DESIGN-BLOCKER-03
**1. Identity / scope:** STATE_RECORD_ID=TASK-P13-01؛ DESIGN_BLOCKED. هیچ subagent/process جدیددر اینمأموریت اجرا نمی‌شود. DAG مرحله‌هایاز قبلتعریف‌شده دارد؛ delegatedagent تصمیم‌گیرمستقل است و worktree sandbox امنیتی نیست.
**2. Source / dependency:** P02/P03/P04 verified +P07GO +P12hostboundary؛ core/jobs.py/WorkflowStore durableownership، managed_resource_claims و git common-dirclaim؛ SECURITY.md حدود accountpermissions. isolation backend/securitypolicy/agenttransport و authorization model انتخاب‌نشده‌اند.
**3. Conditional schema:** DelegatedTask={schema_version:1,parent_task_id,child_task_id,authorization_ref,source_commit,worktree_root,allowed_relative_paths,permission_profile,read_scope,write_scope,allowed_tool_names,budget,deadline,idempotency_key,expected_output_contract}. limitschildren<=2، nesting_depth=0، toolcalls<=20، timeout<=900s، outputs<=4MiB، diff<=1MiB، tokenbudget فقط explicitpositive یا providerfixedcap معتبر؛ nofallbackunlimited. denyunknownfield/arbitrarycommand/secretforwarding/profileescalation. runtimejob/workflowexisting ownerdurablechildstate؛ extension/sessionowner از P02 اگر transport استفادهشد، engine ثانویه نه.
Child خروجی={base_sha,changed_paths,diff_sha256,artifact_ref,checks,uncertainties,completion_status}; patchproposal، نه auto merge. fileownership غیرهمپوشان؛ commonGitdirwriteclaim زمان branch/worktreeoperation؛ parent تغییرخودرا reverse نکند. stalehead یا conflict=>review/rebaseproposal با scope مصوب، noforcepush. childcomplete فقط report است؛ parentdiff/testsverifyauthority.
**4. Failure / test design:** proposedtests/test_delegation_contract.py؛ دو child دو file مستقل accept؛ samefiledeny؛ basechangedreject؛ childcrashpartialdiffquarantine؛ cancelpropagatesownedtree و durableunknownretain؛ rogueexternalprocessownershipfailuredeny-beforelaunch؛ worktreeescape/credentialaccess/unauthorizedtooldeny؛ token/timebudgetexhaustionHALT. artifactsretain تا review، cleanup فقط resolvedpath داخل ownedworktree و noactivelease؛ unfinishedevidence حذف نشود.
**5. Stop:** DESIGN-BLOCKER-03 شامل executionisolation واقعی، providerbillingbudget، parentapprovallease، secretsboundary و independentreview. مسیر tools نامطمئن یاکاربر از delegate مجوزنداده=>dispatch0. فعال‌سازی باصرفوجود worktree ممنوع.
**6. Order:** threat/isolationcontract → boundedtransportdesign → خردکردن implementationtask ها → fakeagents → localisolatednative → opt-inreview؛ nofeatureactivation درطرح.
**7. Evidence:** blocked با missinginputs صریح؛ بعدرفع gate اینبلوکبایدبه atomicimplementationtask تقسیمشود، ضعیفنباید architecture امنیتراحدس بزند.
<!-- TASK-P13-01-END -->
<!-- PHASE-P13-END -->
<!-- PHASE-P14-START -->
<a id="phase-p14"></a>
## P14 — بهینه‌سازی مبتنی بر شواهد مسیر اجرای Agent و ردیابی امن سرور

M14a=TASK-P14-01/02؛ M14b=TASK-P14-03/04/07؛ M14c=TASK-P14-05/06 (اختیاری، مستقل و measurement-gated).

**Scope:** کاهش زمان قابل‌مشاهده در مرز MCP با سنجش هم‌ارز، انتخاب بهتر ابزار و فرمان، تجمیع ویرایش‌ها، و ارتباط Timing/Audit/Job. **Non-goals:** بازنویسی Durable Jobs/Recovery/LSP، تغییر DAG زنده، ادعای latency شبکه یا استنتاج مدل، OS sandbox ادعایی، حذف قفل سراسری اجرای آزاد، ایجاد ابزار ۱۵۲ام، و انتشار کد بدون مجوز مستقل. این بلوک با درخواست مستقیم ادامهٔ پیاده‌سازی وارد اجرای محلی شده است؛ P14-01 طبق Ledger پذیرفته شد. سایر taskهای P14 مطابق Index و گیت‌های مستقل باقی می‌مانند.

**Observed, NOT benchmarked (2026-10-26):** از پنل، p50 ابزارهای read_file=2.8ms، replace_exact=50.2ms، run_process=137.4ms و run_powershell=1048.5ms است؛ نسبت 7.63x فقط برای نمونه‌های *غیرهم‌ارز* بوده و speedup پیش‌بینی‌شده نیست. p95 run_process=2352.4ms، run_powershell=1594.5ms؛ توزیع‌ها و workload متفاوت‌اند. ۶۹ command hash مشاهده‌شده در بازه 07:57–08:43 تکرار نداشتند؛ این فقط شاهد همان نمونه است. نسبت رشد Catalog P08 (full فعلی 191,656 در برابر سقف 193,708 بایت، حاشیه 2,052 بایت) باید روی HEAD روز پیاده‌سازی مجدد محاسبه شود.

<!-- TASK-P14-01-START -->
<a id="task-p14-01"></a>
### TASK-P14-01 — منشأیابی خواندنی DAG و بنچمارک کنترل‌شده B-EXEC-COST
**1. Identity / scope:** STATE_RECORD_ID=TASK-P14-01؛ WEAK_READY؛ weight0؛ P1 Gate. فقط بررسی config provenance و افزودن کد benchmark ایزوله در scripts/perf_benchmark.py و tests/test_perf_benchmark.py؛ بدون تغییر رفتار ابزار تولیدی.
**2. Proof pack:** core/config.py:69 پیش‌فرض workflow_dag_enabled=False دارد، در حالی که وضعیت زنده قبلاً true گزارش شده و P07-02 در Index برابر BLOCKED/NO_GO است. این الزاماً bug نیست؛ منشأ واقعی env/config/operator باید بدون افشای secret مشخص شود. اعداد پنل همان کار واحد نبوده‌اند.
**3. Procedure / contract:** (الف) بررسی صرفاً خواندنی منشأ مقدار مؤثر DAG و تفکیک default/source/runtime، بدون نمایش مقدارهای محرمانه، بازکردن state DB یا تغییر flag؛ (ب) با SHA دقیق از core/registry.py::REGISTRARS و فایل‌های مرتبط اثبات entrypoint؛ (ج) افزودن suite به SUITES/run_benchmarks موجود با owned tmp fixture، warmup5 و repeats30 برای خواندن فایل و جستجوی ثابت از native در برابر Python process مستقیم و PowerShell و برای یک اجرای کوتاه/میانه از Direct در برابر submit_job+job_wait؛ (د) برای edit روی کپی همسان، چهار replace_exact متوالی در برابر یک safe_refactor/apply_patch با نتیجه محتوایی برابر و guard معادل؛ (هـ) سنجش جداگانه p50/p95/p99، process launches، errors، claim wait و elapsed، با ثبت platform/runtime/load. p99 با n=30 اکتشافی است، نه شاهد پایدار برای گیت سخت.
**4. Commands / checks:** بعد از IMPLEMENT_LOCAL مجاز، اجرای pytest -o addopts= -q tests/test_perf_benchmark.py -k exec_cost -p no:cacheprovider --basetemp=.pytest-tmp-plan-p1401؛ سپس ruff scripts/perf_benchmark.py tests/test_perf_benchmark.py و mypy core tools scripts tests. هر سناریو checksum خروجی/اثر جانبی معادل داشته باشد؛ خروجی‌های نامعتبر یا errors>0 مقایسه نمی‌شوند.
**5. Gate / stop:** گزارش JSON و SHA-256 معتبر و provenance خواندنی DAG؛ بدون فرض سرعت واقعی، بدون benchmark روی user DB یا راه‌اندازی/خاموش‌کردن سرویس زنده. اگر workload قابل‌قیاس نبود، نتیجه NOT_COMPARABLE و تصمیم بهبود UNVERIFIED.
**6. Order:** ثبت SHA و clean source boundaries → config provenance → tests RED/GREEN → warmups + randomized/paired repeats → evidence ledger.
**7. Evidence / next:** EVIDENCE-1 و B-EXEC-COST؛ dependencies P14-02/03. P14-05/06 هنوز gate مستقل دارند.
<!-- TASK-P14-01-END -->

<!-- TASK-P14-02-START -->
<a id="task-p14-02"></a>
### TASK-P14-02 — Server-Boundary Correlated Tracing برای Audit/Timing/Job
**1. Identity / scope:** STATE_RECORD_ID=TASK-P14-02؛ WEAK_WITH_REVIEW؛ weight1؛ P1. فایل هدف: core/tooling.py، core/jobs.py، scripts/job_worker.py، sink واقعی Timing/Audit پس از ردیابی در P14-01، tests/test_correlated_tracing.py.
**2. Proof pack:** Timing و Audit و Job Worker زمان‌های جداگانه دارند ولی trace مشترک کافی برای انتساب end-to-end وجود ندارد؛ queue_wait میانه حدود 1.13ms و serialization حدود 0.05ms صرفاً sample سمت سرور هستند. زمان تصمیم‌گیری مدل، اینترنت و تونل از این scope خارج است.
**3. Proposed contract:** تولید شناسه internal correlation_id از ۱۶ بایت entropy، با ۳۲ hex، برای هر invocation در wrapper مشترک و propagation با ContextVar فقط درون فرایند. برای Job پس‌زمینه، ذخیره durable شناسه در metadata نسخه‌بندی‌شده JobStore/JobSpec و انتقال صریح به child worker؛ ContextVar به‌تنهایی از مرز پروسه عبور نمی‌کند. client_trace_ref اختیاری فقط پس از validate شکل و طول ثبت شود، هرگز جای internal ID را نگیرد. ثبت claim_wait_ms، worker_launch_ms، command_exec_ms و serialization_ms فقط در نقاط دقیق قابل‌اندازه‌گیری؛ clock monotonic و عدم دوباره‌شماری intervalها. Trace و parent_operation_id باید Audit/Timing/Job را پیوند دهند، بدون منبع حقیقتِ دوم برای state یا schema migration بی‌طرح.
**4. Checks:** pytest -o addopts= -q tests/test_correlated_tracing.py tests/test_jobs_registry.py tests/test_typed_output_contracts.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p1402؛ ruff تغییرات؛ mypy core tools scripts tests. تست direct/submit_job/job_wait/crash/recovery و concurrent traces، timing disabled، canary در env/argv/invalid client ID، no sensitive payload در Audit/Timing/Health و no side effects هنگام catalog construction.
**5. Gate / stop:** اتصال شناسه ۱۰۰٪ در fixtureهایی که sinkهای لازم فعال‌اند؛ خطای telemetry حق خراب‌کردن Job/Recovery را ندارد؛ Wire Output ابزارهای موجود بدون ناسازگاری. endpoint سرور Egress و پایان worker دو رخداد جداگانه‌اند، نه یک بازه زمانی موهوم.
**6. Order:** source map → مدل metadata → propagation synchronous/durable → timing metrics → failure/privacy tests → review.
**7. Evidence / next:** EVIDENCE-1؛ سنجش سهم actual lock wait برای تصمیم احتمالی P14-06.
<!-- TASK-P14-02-END -->

<!-- TASK-P14-03-START -->
<a id="task-p14-03"></a>
### TASK-P14-03 — راهنمای چندلایه انتخاب ابزار Native/Direct در برابر Shell
**1. Identity / scope:** STATE_RECORD_ID=TASK-P14-03؛ WEAK_WITH_REVIEW؛ weight1؛ P1. core/execution_router.py، core/response_models.py، توضیح کوتاه registrar واقعی run_process/run_powershell و tests/test_execution_router.py.
**2. Proof pack:** execution_recommend advisory است؛ Agent می‌تواند آن را دور بزند. نسبت 7.63x نمونه پنل، به دلیل ناهم‌ارزی فرمان‌ها شاهد تغییر رفتار یا speedup نیست؛ routing benchmark و live-model pilot جدا هستند.
**3. Proposed contract:** در description ابزارها، حداکثر ۱۲۰ بایت افزوده برای هر ابزار، راهنمای native read_file/search_files/git_* و direct run_process برای باینری بدون Shell قرار گیرد؛ قابلیت‌های واقعی PowerShell همچنان پیشنهاد معتبر باشند. Router، هزینه native/direct/shell/durable را با cost_class اختیاری و سازگار با مدل فعلی و بر اساس workload ثابت B-EXEC-COST رتبه‌بندی کند. در صورت امکان، cost_notice کوچک non-blocking فقط با بررسی الگوی کاملاً محافظه‌کارانه و بدون parse کردن payload محرمانه در Audit ثبت شود؛ افزودن این notice به خروجی عمومی اجباری نیست و اگر Wire Contract P08 را بشکند، حذف شود.
**4. Checks:** pytest -o addopts= -q tests/test_execution_router.py tests/test_typed_output_contracts.py tests/test_tool_profiles.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p1403؛ Ruff/Mypy؛ native first روی سناریوی متناظر، Shell first برای Cmdlet/Pipeline واقعی، no dispatch denial، no secret text in notices.
**5. Gate / stop:** اندازه‌گیری مجدد full/coding/coding_core/minimal catalogue در Byte/Token روی HEAD جدید؛ سقف P08 و identical common schemas رعایت شود. رشد پیشنهادی P14-03 زیر 700 بایت باشد، نه مجوز تخطی از سقف کلی. تغییر انتخاب مدل در کار واقعی UNVERIFIED تا pilot قبل/بعدِ هم‌ارز.
**6. Order:** fixture intent و score → small schema guidance → optional advisory feedback فقط پس از privacy review → catalog and behavior tests.
**7. Evidence / next:** EVIDENCE-1، diff schema/size، و baseline آزمون رفتاری Agent برای P14-04.
<!-- TASK-P14-03-END -->

<!-- TASK-P14-04-START -->
<a id="task-p14-04"></a>
### TASK-P14-04 — هدایت Edit Coalescing و انتخاب Direct در برابر Durable Job
**1. Identity / scope:** STATE_RECORD_ID=TASK-P14-04؛ WEAK_WITH_REVIEW؛ weight1؛ P1. tools/filesystem/registry.py، tools/jobs/registry.py، core/execution_router.py و تست‌های filesystem/job_wait/router.
**2. Proof pack:** ۲۷ invocation از replace_exact در نمونه Audit وجود داشت، نه الزاماً ۲۷ ویرایش قابل‌تجمیع؛ safe_refactor و apply_patch از قبل موجودند. JobStore و job_wait از قبل Durable Progress را ارائه می‌کنند، بنابراین سیستم جدید polling/jobs نباید ساخته شود.
**3. Proposed contract:** فقط راهنمای کوتاه در description replace_exact برای تغییرهای مستقلِ معلوم در یک فایل (safe_refactor) یا چند فایل (apply_patch)، بدون تغییر رفتار patch، backup، hash guard یا recovery. همان guidance در ExecutionRouter برای batch intents؛ برای فرمان کوتاه و interactive مسیر direct، برای نیاز به دوام/مدت طولانی submit_job همراه job_wait معرفی شود. راهنمای submit_job/job_status کوتاه و سازگار با catalogue باشد. هیچ ادغام خودکار درخواست‌های جدا یا حافظه نشست مخفی برای دستکاری نتایج ساخته نشود.
**4. Checks:** pytest -o addopts= -q tests/test_filesystem_registry.py tests/test_job_wait.py tests/test_execution_router.py tests/test_typed_output_contracts.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p1404؛ Ruff/Mypy؛ check برای semantic equivalence چهار edit روی کپی fixture، rollback/error/failure guards.
**5. Gate / stop:** no breaking wire change؛ budget تجمیعی P14-03+04 با هدف زیر 1200 بایت و هر سه profile کوچک مطابق P08؛ اثر واقعی تعداد round-trip مدل بعداً با paired client pilot سنجیده شود. ویرایش وابسته به خروجی گام قبلی batch نمی‌شود.
**6. Order:** توضیح/Router → fixture مقایسه‌ای → test+size → real-model measurement فقط اگر مجاز و در دسترس.
**7. Evidence / next:** EVIDENCE-1؛ P14-07 مستقل از تسک‌های شرطی P14-05/06.
<!-- TASK-P14-04-END -->

<!-- TASK-P14-05-START -->
<a id="task-p14-05"></a>
### TASK-P14-05 — Bounded Workspace Preflight؛ فقط بعد از اثبات نیاز و هزینه
**1. Identity / scope:** STATE_RECORD_ID=TASK-P14-05؛ WEAK_WITH_REVIEW؛ weight0؛ P2 optional. tools/project/service.py، tools/project/registry.py، مدل ProjectSummaryResult، scripts/perf_benchmark.py و tests/test_typed_output_contracts.py پس از gate تصمیمی مستقل؛ این تسک در مسیر بحرانی M14a/M14b نیست.
**2. Proof pack:** نبود ابزار ۱۵۲ام به‌تنهایی افزایش سرعت را ثابت نمی‌کند. P14-01/02 باید فراوانی رفت‌وبرگشت‌های اول پروژه را با workload واقعی نشان دهند؛ B-CONTEXT در طرح موجود، context cache را می‌سنجد نه لزوماً latency همین preflight.
**3. Conditional contract:** فقط پس از evidence و تایید promotion از DEFERRED، include_preflight: bool=False به project_summary افزوده شود. حالت False خروجی default سابق را با صفر کار اضافی حفظ کند. حالت True اطلاعات محدود is_git_repo/head/branch، changed_files حداکثر ۵۰ مورد، runner inventory از خواننده‌های موجود و complete/incomplete_reasons داشته باشد؛ بدون اجراکردن کد پروژه، اسکن عمیق untracked یا side effect. هدف سقف 500ms و خروجی حداکثر 16KiB و بهتر بودن از درخواست‌های جداگانه در fixtureهای 100/1000/5000 فایل. Deadline فقط با عملیات واقعاً bounded/cancellable قابل ادعای «سخت» است؛ timeout صوری روی I/O blocking تضمین نیست، و در آن صورت NO-GO.
**4. Checks:** پس از activation مجاز: pytest -o addopts= -q tests/test_typed_output_contracts.py tests/test_project_cache.py tests/test_perf_benchmark.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p1405؛ B-PREFLIGHT با cold/warm، dirty/clean/multi-package، paired repeats و B-CATALOG.
**5. Gate / stop:** byte-equivalent default wire، p95 default regression <=5% تنها پس از تعیین نویز و CI variance، enabled p95<=500ms با partial semantics صحیح و <=16384 bytes و کمتر از مجموع همان خروجیِ معادل با ابزارهای جداگانه. در صورت شکست، DEFERRED حفظ و تغییرات برگشت؛ هیچ تغییر سقف بعد از دیدن نتیجه.
**6. Order:** شواهد need → تصمیم Go/No-Go → optional design → focused tests → benchmark → independent review.
**7. Evidence / next:** ثبت تصمیم شرطی EVIDENCE-1؛ اگر NO-GO باشد P14-07 قابل پایان است.
<!-- TASK-P14-05-END -->

<!-- TASK-P14-06-START -->
<a id="task-p14-06"></a>
### TASK-P14-06 — طراحی مشروط Multiple-Granularity Job Locking؛ بدون پیاده‌سازی فعلی
**1. Identity / scope:** STATE_RECORD_ID=TASK-P14-06؛ DESIGN_BLOCKED؛ weight0؛ P2 conditional. هیچ فایل تولیدی core/resource_claims.py، core/jobs.py یا scripts/job_worker.py در این تسک فعلی تغییر نمی‌کند؛ اجرای آینده به طراحی/مجوز جدا و تسک‌های STRONG_ONLY نیاز دارد.
**2. Proof pack:** scripts/job_worker.py فعلی برای هر Job ترکیب coordination_claim(exclusive=True) و path_claim(cwd,write,subtree) می‌گیرد؛ max_running_jobs=4 فقط ظرفیت admission است نه تضمین concurrency واقعی. سیستم OS sandbox نیست (FIND-09). دو cwd متفاوت تضمین containment ندارند؛ shell=False، executable allowlist و حتی verification_worker تایپ‌شده اگر کد تستِ پروژه را اجرا کند امکان اثر جانبی خارج از Workspace دارد.
**3. Conditional safety contract:** شرط اول: P14-01/02 باید نشان دهند contention واقعی هماهنگی برای workload هدف معنادار است (مثلاً p95 claim_wait_ms>=250ms یا >15% elapsed؛ threshold پیش از آزمون تثبیت شود و admission wait از claim wait جدا اندازه‌گیری گردد). شرط دوم: تنها عملیات واقعاً closed-effect و قابل اثباتِ داخلی یا دارای isolation enforceable، نه submit_job آزاد/run_process/run_powershell و نه runner اجرای arbitrary project code، می‌تواند کاندید workspace-scoped باشد. قرارداد پیشنهادی در managed_resource_claims باید اتمیک باشد: Job آزاد host-exclusive روی coordination:host WRITE؛ Job ایزوله روی همان coordination:host READ (Shared) + workspace canonical fs subtree WRITE + git-common-dir WRITE (در صورت Git) و resourceهای مشترک واقعی دیگر. READ میزبان با WRITE میزبان conflict دارد؛ دو workspace ایزوله READ میزبان را share می‌کنند. canonicalization/symlink/junction، inherited handles، shared temp/cache/services، process ownership و cleanup هم باید پیش از اجازه اجرا راستی‌آزمایی شوند. resource lock مجوز امنیتی یا sandbox نیست.
**4. Acceptance design:** در صورت GO و صدور وظایف اجرایی جدید: matrix تست تضاد Shared-Host/Exclusive-Host، workspace مشترک، linked worktree و shared common-dir، و ۲ تا ۴ fixture کاملاً bounded؛ no overlap برای کار global با scoped و same scope، no orphan/crash/cancel escape؛ throughput هدف >=1.5x فقط برای عملیات واقعاً قابل ایزوله‌سازی. هر دستور آزاد و هر template که project code نامطمئن اجرا کند در حالت host-exclusive می‌ماند. جلوگیری از bypass در دیگر مسیرهای workflow/verification واجب است.
**5. Stop:** اگر هر یک از گلوگاه، قابلیت اثبات containment یا عدم دورزدن قفل‌ها تأیید نشد NO-GO؛ بدون تغییر قفل‌های live. هیچ افزودن پارامتر عمومی isolation_mode مجاز نیست.
**6. Order:** benchmark actual claim/admission → threat model shared resource → independent safety review → Go/No-Go → در GO فقط طراحی تسک‌های اتمی STRONG_ONLY آینده.
**7. Evidence / next:** BLOCK-P14-JOB-ISOLATION؛ خارج مسیر بحرانی P14-07 و بدون ادعای speedup کنونی.
<!-- TASK-P14-06-END -->

<!-- TASK-P14-07-START -->
<a id="task-p14-07"></a>
### TASK-P14-07 — پذیرش کمی فاز فعال P14 و عدم پسرفت
**1. Identity / scope:** STATE_RECORD_ID=TASK-P14-07؛ STRONG_ONLY؛ weight0؛ P1 Gate؛ پذیرش P14-01..04. P14-05/06 فقط پس از activation مستقل وارد scope می‌شوند؛ NO-GO آن‌ها مانع بستن M14a/M14b نیست.
**2. Proof pack:** روی SHA نهایی، diff تمام فایل‌های P14 در برابر source baseline مجاز، تحقق P05 lazy/no-side-effect و P08 wire/catalog invariants و P07 DAG activation NO-GO بررسی شود. یافته‌های نمونه‌ای به benchmark قطعی تبدیل نشوند.
**3. Quantitative gate:** (الف) B-EXEC-COST: ۵ warmup/۳۰ measurement و توزیع p50/p95/p99 همراه error/launch count، paired run، کنترل نویز و تمام result hashها؛ تغییر latency native <=5% فقط با confidence و تکرار مناسب قابل حکم، n=30 p99 را evidence اکتشافی بدانید. (ب) Tracing: 100% fixture traces با Audit/Timing/JobStore در صورت enabled بودن sinks همبسته، Canary leak=0، claim_wait nonnegative، egress/worker intervals به‌طور جداگانه سنجیده شود. (ج) Catalogue: روی HEAD جدید full <=193708 bytes و token allowance P08 و tool profile count=151 full/96 coding/43 coding_core/32 minimal در صورت ثابت بودن baseline، shared schemas identical. (د) هیچ تغییر قفل production یا DAG و هیچ orphan process؛ P14-05 اگر opt-in شد B-PREFLIGHT با p95<=500ms/output<=16KiB در fixture5000، و no regression default.
**4. Commands:** بعد از پیاده‌سازی مستقل و با Python محیط بررسی‌شده: python -m compileall -q core tools scripts tests؛ python -m ruff check .؛ python -m mypy core tools scripts tests؛ python main.py --check؛ python -m pytest -o addopts= -q -p no:cacheprovider --basetemp=.pytest-tmp-plan-p1407؛ health_check --json مطابق GATE-ALL. Same-SHA hosted 6 CI + 3 platform + 1 packaging برای ادعای multi-platform/release لازم؛ در IMPLEMENT_LOCAL بدون hosted، UNVERIFIED گزارش شود.
**5. Failure / stop:** شکست privacy، rollback، ownership، compat، budget یا معیار عددی → STOP/BLOCKED با شاهد؛ از تغییر پسینی threshold یا تغییر شاخه/main/release بدون مجوز خودداری شود. P14-05/06 مسدود/تعویق‌شده به‌عنوان نتیجه شرطی مستند شود، نه تست pass جعلی.
**6. Order:** audit SHA/diff → B-EXEC-COST → trace/canary → catalog budget → full tests/hosted evidence → independent review → update Ledger/CHANGELOG فقط در scope authorized.
**7. Evidence / next:** EVIDENCE-1؛ نتیجه مستقل M14a/M14b، تصمیم مشروط M14c. Speedup کل زمان Agent/مدل فقط بعداً با آزمایش بیرون سرور claim می‌شود.
<!-- TASK-P14-07-END -->
<!-- PHASE-P14-END -->

<!-- CHUNK-12-END -->

<a id="runbook"></a>
<!-- LOW-CAPABILITY-RUNBOOK-START -->
<a id="phase-p15"></a>
<!-- PHASE-P15-START -->
## P15 — Bounded Native Read Batch؛ کاهش رفت‌وبرگشت بدون Shell و بدون dispatcher آزاد

**درخواست مستقیم کاربر، 2026-10-26:** کار قبلی و پذیرش DAG ادامه یابد؛ تجمیع چند عملیات Native مستقل نیز عمیق بررسی و در همین master plan ثبت یا اجرا شود. تصمیم این مرحله: طراحی و پذیرش‌نامهٔ جداگانه؛ پیاده‌سازی Batch تا قرارداد بودجه/پروفایل/لغو و tracing مشخص نشود به‌صورت عجولانه کنار activation قرار نمی‌گیرد. این محدودکردن اجرا به design انتخاب فنی همین نشست است؛ نیاز به تکرار مجوز اصل قابلیت نیست. GO محلی DAG هیچ مجوز اجرای arbitrary command یا Batch mutating نمی‌دهد؛ Batch خواندنی به DAG وابسته نیست.

### وضعیت واقعی و شواهد فعلی

| قابلیت | سورس مرجع | قرارداد و حد فعلی |
|---|---|---|
| چند فرمان Shell | tools/terminal/registry.py::run_powershell | یک اسکریپت؛ هزینهٔ process/shell و ریسک‌های همان ابزار، نه Native Batch |
| Workflow/DAG | tools/workflows/registry.py::WorkflowInput و core/workflow_actions.py::ACTION_DESCRIPTORS | 1..100مرحله؛ DAG cap1..16؛ فقط actionهای allowlisted؛ read_file/get_file_info/search_files ابزار دلخواهِ قابل تزریق نیستند |
| diagnostics/verification | tools/testing/registry.py::collect_diagnostics | backendهای ثابت Ruff/mypy/pytest؛ cap1..3 و سیاست/claim مربوط به هر backend |
| multi-file edit | tools/filesystem/registry.py::apply_patch | transaction چندفایلی، hash guard، backup و rollback موجود؛ Batch نوشتن جایگزین آن نمی‌شود |
| multi-edit تک‌فایل | tools/filesystem/registry.py::safe_refactor | 1..25ویرایش typed؛ تغییر وابسته به نتیجهٔ گام قبلی تجمیع خودکار نمی‌شود |
| چند خواندن Native در یک MCP request | registry فعلی filesystem | ابزار عمومیِ فعلی برای این قرارداد وجود ندارد؛ هنوز پیاده نشده |

- اندازه‌گیری واقعی P14-01 فقط هزینهٔ سمت سرور در fixture را نشان داد: read native p95=1.355ms، Python=152.242ms، PowerShell=415.948ms. این اعداد latency Tunnel، token/model و سرعت Native Batch جدید نیستند. قابلیت جدید هنوز هیچ نتیجهٔ بنچمارک تأیید‌شده ندارد.
- Batch چند عملیات را در یک request/response می‌گنجاند؛ Parallel هم‌پوشانی اجرای عملیات مستقل است. کاهش roundtrip می‌تواند حتی در max_parallel=1 وجود داشته باشد. فراخوانی متوالی workflow_start/execute/status معادل one-shot native batch نیست.
- core/extensions/bridge.py::ExtensionMCPServer.call_tool پیش از native dispatch، hidden-tool/profile را enforce می‌کند؛ فراخوانی مستقیم helper به‌جای مرز عمومی می‌تواند این تضمین و validation را دور بزند. ثابت‌بودن enum و allowlist لازم است و annotations به‌تنهایی مجوز اجرا نیست.
- tools/git/service.py::command_arguments، fsmonitor و ext-diff/textconv و transport را محدود می‌کند؛ اما GIT_OPTIONAL_LOCKS=0 در command_environment فعلی دیده نشد. بنابراین git_status صرفاً به‌دلیل برچسب read-only، «فاقد هر اثر بر index» فرض نمی‌شود و ورود آن به Batch به‌صورت جدا gate دارد.

### قرارداد پیشنهادی کمینه — هنوز اجرا نشده

1. یک نام پیشنهادی `native_read_batch`، نسخهٔ اول فقط enum `read_file`؛ نه `tool_name: str` آزاد، نه command/argv/script، external tool، callbacks، workflow، jobs، nested batch یا handler importشونده. این نخستین مصرف واقعی قرارداد است؛ mixed read-only بعداً همان قرارداد را با enum ثابت و مدل مستقل هر نوع گسترش می‌دهد.
2. ورودی typed شامل root صریح و 1..16item با id یکتای کوتاه، path و window خواندن محدود؛ max_parallel پیش‌فرض1 و سقف4، deadline پیش‌فرض10s و سقف60s. dependency در این API وجود ندارد؛ عملیات وابسته باید جدا درخواست شوند یا از Workflow موجود استفاده کنند.
3. فایل عادیِ محلی، containment با canonical_path و recheck پیش از open؛ symlink escape، UNC/network و input نامعتبر بدون dispatch. در MVP فقط window کوچک با max_chars اجباری/محدود، فایل حداکثر2MiB و مجموع منبع حداکثر8MiB؛ code/config/import محتوا اجرا نمی‌شود. نمایش تعداد عملیات، حد و خطای روشن بهتر از fallback پنهان به Shell است.
4. متن پیش‌فرض هر مورد4096char، مجموع پاسخ64KiB و سقف سخت encoded JSON192KiB با احتساب metadata/UTF-8؛ رزرو بودجه طبق ترتیب ورودی پیش از dispatch، نه رقابت completion order. خطاهای هر مورد مستقل و محدود؛ محتوای بریده حتماً truncated=true. انتخاب artifact delivery نباید به‌عنوان یک roundtrip کاملِ خواندن محتوا جا زده شود.
5. خروجی stable order با id، status، elapsed_ms، result/error و complete/truncated؛ وضعیت‌های success/error/not_started/deadline/cancelled صریح. schema ساختاری خراب/نوع عملیات ناشناخته => کل درخواست invalid و صفر dispatch. path/permission failure معتبرِ یک مورد => همان مورد denied؛ خواهران مجاز می‌توانند اجرا شوند. error متن source/provider/env را به audit/health/timing نبرد.
6. هیچ snapshot اتمیک چندفایلی، rollback یا transaction مشترک ادعا نمی‌شود. timestamp/hash هر مورد فقط برای داده‌ای که واقعاً سنجیده شده معتبر است؛ hash متن برگشتی با hash کل فایل یکی نیست. تغییر منبع/خواندن ناقص completeness=false می‌دهد؛ عدم مشاهدهٔ خطا اثبات snapshot consistency نیست.
7. registration/profile eligibility، SDK input/output validation، policy، lifecycle/admission، per-item timing و compact_errors موجود authority هستند. orchestration باید روی server instance موجود کار کند؛ server/registry/router/thread pool جدید یا recursion در MCP نسازد. قبل از هر dispatch، native-visible بودن و classification مجاز مجدداً کنترل شود؛ direct in-process calls همان denial را ببینند.
8. admission و buffer در سطح همان MCP server/runtime مشترک و محدود باشند: حداکثر4 batch پذیرفته‌شده، حداکثر4 read واقعاً in-flight در مجموع، بدون صف admission بی‌کران؛ اشباع => batch_busy و صفر dispatch تازه. سهم buffer پاسخ حداکثر4×192KiB است و سقف items پذیرفته‌شده64؛ این limiter موقت registry یا scheduler اجرایی تازه نیست و thread pool جدا نمی‌سازد. parallel read زیر cap واقعی و بودجهٔ مشترک می‌ماند؛ cancellation/deadline مانع شروع item تازه است و نتیجهٔ دیررس success پذیرفته نمی‌شود. timeout caller به معنی توقف thread/I/O نیست؛ slot/buffer فعال تا پایان واقعی آزاد نشود. deadline روی regular local file تعاونی است؛ OS-hard timeout یا توقف قطعی blocking I/O بدون شواهد ادعا نشود. اگر این قرارداد با service فعلی قابل حفظ نباشد، task تا تصمیم روشن BLOCKED است، نه abandon/leak پنهان.
9. log/trace فقط correlation، opaque item-id، status، bytes و زمان؛ نه محتوای فایل/args/env. per-item trace فرزند همان server-boundary trace P14-02 است. هیچ cache/index/watcher/persistent batch engine/SQLite migration جدید در MVP ساخته نمی‌شود.
10. profileهای موجود و ابزارهای قدیمی خودکار تغییر نمی‌کنند؛ نام جدید حداکثر یک ابزار اضافه می‌کند و تعداد151 فعلی تا gate انتشار آن حفظ می‌شود. P14 خودش ابزار152ام نمی‌سازد؛ اگر قابلیت تازه در P15 پذیرفته شد، شمارش تازه صادقانه با scope جدید ثبت شود. coding_core allowlist43 فعلی بدون تصمیم صریح تغییر نکند و Batch ابزار پنهان‌شده را قابل دسترس نکند.

<!-- TASK-P15-01-START -->
<a id="task-p15-01"></a>
### TASK-P15-01 — قرارداد، پروفایل و B-NATIVE-BATCH قبل از کد تولیدی
**1. Scope:** scripts/perf_benchmark.py، tests/test_perf_benchmark.py، core/registry.py، core/tool_profiles.py و همین سند. SOURCE CURRENT refs بالا؛ ADD هنوز در این task ندارد.
**2. Smallest work:** fixture ثابت1/4/8/16file با محتوا/اندازه برابر؛ مقایسه N public native calls ترتیبی، orchestration prototype خواندنی cap1/2/4 و PowerShell فقط به‌عنوان reference هم‌ارز. latency مصنوعی0/20/100/250ms و wire framing واقعی از latency سمت سرور جدا شوند؛ مصنوعی هرگز latency واقعی Tunnel نام نگیرد. warmup5/repeat30 randomized paired؛ hash خروجی برابر، errors0 و ترتیب ثابت.
**3. Acceptance:** p50/p95، actual MCP request/response count، bytes/tokens، CPU/RSS، concurrent workers و cleanup؛ N=1 مسیر مستقیم reference و سربار حداکثر5ms محلی، سناریوی latency100ms و N>=4 کاهش حداقل50percent roundtrip-wait. هیچ ادعای speedup تولیدی/مدل بدون سنجش واقعی. tokenizer/version یا NOT_MEASURED.
**4. Catalog gate:** baseline روز در full/coding/coding_core/minimal با serializer ثابت و P08 allowances؛ سقف P08 با رشد ابزار جدید به‌طور پنهانی waive نشود. اگر name/schema پیشنهادی در بودجه جا نشد، اینجا ابتدا کوچک‌ترین schema/registration یا بودجهٔ feature-specific با دلیل و مجوز روشن ثبت شود؛ default activation قبل از تصمیم ممنوع. اثر جدید روی 43-tool profile از migration ابزارهای قدیمی جداست.
**5. Checks:** tests/test_perf_benchmark.py -k native_batch؛ Ruff/Mypy و actual SDK prototype snapshot؛ invalid/hidden tool dispatch count0. Next P15-02 فقط پس از این evidence و tracing contract.
<!-- TASK-P15-01-END -->

<!-- TASK-P15-02-START -->
<a id="task-p15-02"></a>
### TASK-P15-02 — Native Read Batch فایل، بدون generic dispatcher
**1. Scope:** EXISTING tools/filesystem/registry.py، tools/filesystem/service.py::read_window، core/registry.py و core/tool_catalog.py؛ ADD tools/filesystem/batch.py و tests/test_native_batch.py فقط پس از P15-01. helper فعلی خواندن و SDK wrapperها reuse شوند؛ duplicate reader و registry ایجاد نشود.
**2. Smallest change:** اجرای قرارداد10بندی بالا، input/output typed و bounded؛ یک registration روی server موجود با allowlist ثابت read_file و دسترسی root/file. هر تغییری برای deadline به shared bounded-reader boundary برسد و رفتار read_file قدیمی بدون ورودی تازه حفظ شود.
**3. Denial/tests:** unknown kind/duplicate id/oversize count/hidden profile/root escape/symlink/invalid encoding و excess budgets => dispatch spy0 برای موارد مردود؛ خواندن درست+خطای مستقل+order+UTF8+truncation+deadline+cancel+late result+concurrency slot و zero side effects در create_server/list_tools/health. direct call و actual MCP Client هر دو بررسی شوند.
**4. Checks:** pytest tests/test_native_batch.py tests/test_filesystem_registry.py tests/test_tool_profiles.py tests/test_typed_output_contracts.py؛ full Ruff/Mypy، actual wire/catalog، before/after P15-01 benchmark و focused lifecycle. خطای کاناری در audit/timing/health نیاید؛ محتوای فایلِ صریحاً درخواست‌شده payload مجاز کاربر است، نه log مجاز.
**5. Stop:** profile bypass، مخفی‌شدن failure، buffer/worker بی‌کران، snapshot کاذب یا نیاز به executor عمومی => BLOCKED؛ launch process برای هر read راه‌حل پیش‌فرض نیست. Next P15-04؛ mixed الزامی نیست.
<!-- TASK-P15-02-END -->

<!-- TASK-P15-03-START -->
<a id="task-p15-03"></a>
### TASK-P15-03 — Mixed Read-only؛ مشروط و DEFERRED
**1. Scope:** فقط پس از benefit و safety evidence؛ reuse همین ابزار/SDK با union ثابت get_file_info/search_files/git_status، نه arbitrary names. tools/filesystem/registry.py، tools/git/service.py و tests/test_native_batch.py فایل‌های شناخته‌شده‌اند؛ هر consumer/schema جدید ابتدا در این block دقیق شود.
**2. Guards:** search فقط bounded non-snapshot/count_mode=none/max_scan_files/time budget و response cap؛ metadata بدون hash/child scan بی‌کران. Git فقط status محلی با fsmonitor/ext helpers/lazy fetch/transport غیرفعال و اثبات عدم تغییر index با GIT_OPTIONAL_LOCKS یا مرز موجود معادل؛ mutation routes هرگز وارد enum نشوند. نیاز Git/workflow مشترک به host exclusive با برچسب read-only حذف نمی‌شود.
**3. Acceptance:** یک پاسخ mixed واقعاً با N request کمتر، payload متناسب، hidden-domain denial و byte-identical input-index در fixture؛ config hostile و secret canary و timeout before/after dispatch. independent security review برای whitelist/classification و actual DAG/parallel contention؛ در نبود سود اندازه‌گیری‌شده اجرا نشود.
**4. Gate:** فعلاً DEFERRED؛ پذیرش file-only به آن وابسته نیست و جایگزین guard مستقل DAG/Job نمی‌شود.
<!-- TASK-P15-03-END -->

<!-- TASK-P15-04-START -->
<a id="task-p15-04"></a>
### TASK-P15-04 — پذیرش یک request/response و عدم پسرفت
**1. Scope:** tests/test_native_batch.py، tests/test_perf_benchmark.py، tests/test_tool_profiles.py، tests/test_typed_output_contracts.py و مستندات ابزار فعلی پس از acceptance؛ no release/tag inference.
**2. Gate:** actual MCP transcript تعداد request/response، wrapper/profile/policy denial، bounded resource/cancel/error و source-freshness proof؛ full pytest/Ruff/Mypy/compileall و actual catalogue/legacy equality؛ hosted same-subject gates فقط برای کد منتشرشده ادعا شوند. fixed-profile client بدون refresh باید راه دسترسی معتبر یا unavailable روشن بگیرد.
**3. Completion:** file-only feature پذیرفته‌شده با B-NATIVE-BATCH و no leak/no hidden dispatch؛ mixed می‌تواند DEFERRED بماند. نتایج مدل، latency واقعی Tunnel و اثر token فقط وقتی واقعاً سنجیده شدند به Ledger وارد شوند. rollback این قابلیت، بستن admission/registration جدید و drain امن است؛ data/user state حذف نمی‌شود.
<!-- TASK-P15-04-END -->
<!-- PHASE-P15-END -->

## LOW-CAPABILITY CODING AGENT RUNBOOK

### A — Orientation با حداقل context

این بخش دستور اجرای آینده است. مأموریت فعلی همچنان PLAN_ONLY است. اول Quick Start تا MASTER-QUICK-START-END؛ سپس فقط قرارداد مشترک، phase مربوط و task فعال. فایل کامل را در هر turn دوباره نخوان. دستورات زیر فقط استخراج و نمایش می‌دهند:

~~~powershell
$PlanPath = 'LOCAL_PATH_REDACTED'
$TaskId = 'TASK-P01-01'
$Utf8Strict = [System.Text.UTF8Encoding]::new($false, $true)
$PlanText = [System.IO.File]::ReadAllText($PlanPath, $Utf8Strict)
$StartTag = "<!-- $TaskId-START -->"
$EndTag = "<!-- $TaskId-END -->"
if ([regex]::Matches($PlanText, [regex]::Escape($StartTag)).Count -ne 1) { throw 'TASK_START_NOT_UNIQUE' }
if ([regex]::Matches($PlanText, [regex]::Escape($EndTag)).Count -ne 1) { throw 'TASK_END_NOT_UNIQUE' }
$StartOffset = $PlanText.IndexOf($StartTag, [StringComparison]::Ordinal)
$EndOffset = $PlanText.IndexOf($EndTag, [StringComparison]::Ordinal)
if ($EndOffset -le $StartOffset) { throw 'TASK_TAG_ORDER_INVALID' }
$PlanText.Substring($StartOffset, $EndOffset + $EndTag.Length - $StartOffset)
Get-FileHash -LiteralPath $PlanPath -Algorithm SHA256
~~~

اگر task قبلی commit/diff دارد، SOURCE_BASE اولیه را بی‌فکر با HEAD جدید برابر نکن؛ evidence predecessor را بخوان و فقط anchor های متاثر را بررسی کن. dependencies از Index authority وضعیت‌اند. فازهای متداخل بر اساس DAG اجرا می‌شوند: P02-01..04، P03-01، P02-05..08، سپس P03-02/03/05/04؛ شماره فاز الزام ترتیب خطی نیست. task ی با dependency غیر VERIFIED یا design blocker اجرا نشود.

### B — Implement در scope مجاز

1. ثبت permission reference و mode=IMPLEMENT_LOCAL، worktree/branch/HEAD، task ID، وضعیت اولیه و fingerprint. permission انتشار از این مجوز مشتق نمی‌شود.
2. diff از قبل موجود را با DO_NOT_TOUCH تطبیق بده؛ اگر فایل task تغییر ناشناخته دارد STOP و گزارش. اگر unrelated است حفظ و ادامه همان scope.
3. در Index فقط همان task به IN_PROGRESS تغییر کند؛ task دیگری هم‌زمان مالک همان فایل نباشد. baseline test مربوط اگر عملی/کم‌هزینه است اجرا و خروجی کوتاه با exit ثبت شود.
4. fixture فعلی → regression معنی‌دار → implementation به ترتیب task → callers/registration/schema/config/cleanup. اگر task افزودن API را نگفته، برای ردکردن تست API تازه نساز.
5. source excerpt کافی است؛ unresolved cross-module uncertainty ابتدا scoped graph با freshness/coverage، سپس source. نبود ابزار یا index نامتناسب را مستند و direct reads محدود؛ نصب/rebuild برایظاهررعایت‌قاعده نه.
6. diff همه فایل‌های touched را بخوان؛ وقتی تستپاس‌شده متعلق بهکدپیش ازآخرین edit است دوباره focusedtest بزن. تغییردلیل‌دارنبود broadrepeat نه.

### C — Verification و توقف‌های روشن

- تست شکست قبل fix و موفقیت بعد fix باید به همان fixture/ورودی مربوط باشد. تعداد futuretest را پیشاپیش invent نکن؛ skipped را جدا بنویس.
- شکست deterministic: exactcommand، code،assertion و sourceanchor را ثبت کن؛ تا تشخیص علت همان command را تکرار نکن. یک اصلاح موجه و یک focusedrerun؛ شکست دوباره بدون شواهد جدید =>BLOCKED.
- خطای transient شبکه: حداکثر یک retry با retry-after یا backoff؛ timeout پس از mutation proofrollback نیست و autoplay نبایدشود.
- sourceSHA mismatch، test واقعیغایب برخلاف EXISTING، policy/storage denial، unknownsideeffect، quotaexhausted، missingnativeownership و approval نامشخص =>HALTmutation. parsererror در این Plan به‌معنیتغییرسورسبرایهماهنگی نیست.
- بعد codewritten و beforeacceptance: IMPLEMENTED_UNVERIFIED؛ بعد taskacceptance واقعی VERIFIED. FAILED برایآزمونشکست‌خورده؛ BLOCKED برای dependency/permission/design؛ NEEDS_REVALIDATION برای drift. فقط taskdone به phaseprogress کمکمی‌کند، وزن 0audit پیشرفتمحصولندارد.

### D — GitHub CI و permission edge cases

Runbook P09 authority command وهویت است. چهار mode مجزا: PLAN_ONLY؛ IMPLEMENT_LOCAL؛ PUBLISH_BRANCH_PR؛ MERGE_RELEASE. دوتایآخرحتیبا CIgreen scope مصوبمی‌خواهند. ایننوبت mode اول است.

| وضعیت | نتیجه مجاز |
|---|---|
| branch push مجاز، PR نامجاز/ناموجود | push checkpoint فقط در scope؛ CI=NOT_TRIGGERED/BLOCKED_PR_PERMISSION؛ نه dispatch پنهان |
| PR موجود و currenthead۱۰checkpending | WAIT، summarycompact و newuserinput پذیر؛ نه merge |
| local HEAD با PRhead فرق دارد | STOP identity؛ stalecheck را accept نکن |
| rerun قدیمی سبز اما head جدید | invalid evidence؛ run همان head لازم |
| fork/first-timeapproval/actionsdisabled | BLOCKED_HOSTED، صاحب repo باید constraint حلکند؛ workflowpolicy راخودتتغییرنده |
| branch protected=false | قرارداد 10check همچنانالزام؛ greenbadge تنها authority نیست |
| job skipped بهعلت pathcondition | اگر check لازم scope است gate بسته؛ علتثبتشود |
| gh403/401/rate limit | UNVERIFIED؛ secret/authstate چاپنشود، read retry محدود |
| releaseworkflow success با publish=false | publicationNOT_PERFORMED؛ tag ساختنراه‌حلنیست |
| merge مجاز و currentPRheadgreen | headguard، سپس newmainSHApushchecks؛ release حدسممنوع |

### E — Update Plan Without Corruption

فقط یک writer. ابتدا hash، exactrow، tagcount و tail را بخوان؛ تغییر کوچک row با expectedoldtext/count1 و append یک evidence entry. فایل را با replace-all regex روی ID های مشابه یا Out-File encodingdefault بازنویسی نکن. در صورت استفاده helper، باید atomicwrite با temp همان directory، flush و recheckhash قبل replace داشته باشد؛ concurrencycomparefail=>بدون overwrite. در این نشست apply_patch با exactcontext و strictUTF8re-read استفاده شده است.

شکل evidence آینده (template عمداً داده خالی دارد، نه ادعای completion):

~~~json
{
  "task_id": "TASK-P01-01",
  "state": "IMPLEMENTED_UNVERIFIED",
  "source_head": null,
  "tested_diff_sha256": null,
  "permission_reference": null,
  "changed_files": [],
  "checks": [],
  "hosted_runs": [],
  "risks": [],
  "blockers": [],
  "next_task_id": null
}
~~~

این template نباید به‌عنوان VERIFIED ثبت شود. check row حداقل command/exit/status/passed/failed/skipped/testedhead یا diff داشته باشد؛ hostedrow runID/attempt/head/event/jobs داشته باشد. پس از update UTF8،markers،link ها،DAGdependencies و derivedcounts راچککن؛ corruptfile یاحذف history=>STOP واز نسخه سالم بازیابی محدود، نه شروع مجدد.

### Small-model escalation

Task پیچیده را باکوتاه‌کردن guards ساده نکن. اگرسمت اثر/transaction/lease بیرونبلوکتسکشد، stage موجودرا record کن و نیاز review را با۱)anchor،۲) تفاوت expected/actual،۳) کوچک‌ترین تصمیملازم،۴) هیچ mutation تاپاسخ،۵) کارمستقل باقی توضیح بده. برای STRONG_ONLY به agent کم‌توان اجازهطراحیامنیتندان؛ او فقط preflight/خواندن/evidence جمع می‌کند. currentpermission پرسش تکراری نمی‌خواهد؛ فقط ambiguity واقعی یا scope جدید.
<!-- LOW-CAPABILITY-RUNBOOK-END -->

## GATE-ALL — بررسی‌های محلی و hosted مورد نیاز آینده

تمام فرمان‌های این بخش در مأموریت ساخت طرح **اجرا نشده‌اند**. متناسب با task focused شروع شود؛ milestone/runtime/release همه checks لازمقراردادپروژه را دارد.

~~~powershell
& .\.venv\Scripts\python.exe -m compileall -q core tools scripts tests
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m mypy core tools scripts tests
& .\.venv\Scripts\python.exe main.py --check
& .\.venv\Scripts\python.exe -m scripts.health_check --json
& .\.venv\Scripts\python.exe -m pytest -o addopts= -q --basetemp=.pytest-tmp
~~~

هر exitcode بلافاصله بررسی شود؛ شکستاولی دربالا مجوز اجرایکوربقیه نیست. startup/health ممکنست runtime state بسازند، بنابراین در PLAN_ONLY یاروی production نامعلومنه. releasecommands FullDoctor/platform/package از docs/release-process.md و parser همان HEAD گرفتهشوند؛ نسخه package باید actualversion باشد، نه hardcodedrelease قدیمی. packaging5target اثبات nativeexecution پنجمعماری نیست.

| Workflow | check های لازم | subject |
|---|---|---|
| CI | Test & Validate روی Windows/Linux/macOS × Python3.10/3.12 =6 | PRcurrenthead + checkoutref ثبت‌شده |
| Platform Release Validation | Release Gate روی Windows/Linux/macOS Python3.12 =3 | همان head |
| Release Packaging Smoke | package =1، deterministic5archive/checksums | همان head |
| پس از merge | همین 6+3+1 | newmainSHA |
| Publish Release | guardedexactsubject و sameSHAgates؛ publishtrue بایدشاهد داشته باشد | releaseSHA، نههر maingreen |

### Benchmark contracts — امروز عددسازی نشده

| ID | ورودی ثابت / کنترل | اندازه‌گیری و پذیرش آینده | وضعیت امروز |
|---|---|---|---|
| B-CATALOG | full/coding/minimal؛serializer یکسان؛runtime/provider صفر | count/UTF8bytes واقعی؛tokenizer/version جدا؛ outputSchema count | سه baseline اندازه‌گیری‌شده؛tokens/latency NOT_MEASURED |
| B-CONTEXT | fixture100/1000/5000files،cold/warm،edit/delete/rename؛sameworkspace | p50/p95،RSS،cachehits،parsecounts،falseedge0،completeness؛1000cyclesboundedcache | PARTIAL_LOCAL: 100 files cold3502.625ms/warmmedian1645.477ms/RSSmax109756416B؛1000 cold4879.595/warm1961.188/RSS118190080B؛5000 cold7994.456/warm5562.480/RSS137560064B; 1 definition retained, content edit SHA changed and new source visible. No 1000-cycle soak/delete/rename/production benchmark; 3 timing samples per size are NOT p95 evidence |
| B-LSP | fakeownedserver N1/10/100،workspace1/2؛one-shotvs persistent،warmup5/repeat30 | starts1/keywarm،p95nonregression،queue/doc/handle/RSSlimits،noorphans | measured 2026-10-20 on aeb7558 with local reviewed fixture: 12 cases×35 runs؛ persistent warm req p95 32.449/66.592/41.650/90.005/51.288/95.025ms all ≤ paired one-shot 123.484–150.699ms؛ persistent spawns1/workspace (1/2) vs one-shot1/10/100؛ errors/restarts0؛ queue≤2؛ child RSS28.8–57.6MB persistent vs13.4–17.7 one-shot؛ handles147–294؛ orphans0؛ report sha256 8ba0c02a858ded7aeef4ae00150f77c206ea9843397d2af725dd2792148a4bfc؛ realproviders UNVERIFIED |
| B-PROFILE |20journey×3 تکرار،model/client/version ثابت،balancedorder | success/wrongtool/roundtrips/p50/p95/token/byte؛P05 حدود | blockedliveUXbudget؛staticbaselineavailable |
| B-TESTS | Python/JS/TS/PHPfixturegroundtruth،monorepo/unknown/dynamic | false-negative0،extra-tests مجاز؛selection/execution جدا؛0testsunknownfalse-success0 | PARTIAL_LOCAL: Python1000-module/200-test chain evaluated changed modules 0000/0500/0999; decision full_suite 3/3, plan mode full 3/3. Conservative fallback avoids unsupported focused mapping; no grounded multilingual recall, hidden-test accuracy or skip-rate improvement claimed. Corrected benchmark to accept justified full_suite instead of raising RuntimeError; quick project suite rerun exit0 |
| B-DAG | samecaps،serialvsDAGreadfixtures+conflictfixtures | throughput و p95/maxconcurrency،uncertainty/fairness؛gateNO-GO تا evidence | historicalinfra فقط،production NOT_MEASURED |

| B-EXEC-COST | fixture متن/جستجو/ویرایش هم‌ارز و مستقیم در برابر Shell/Job؛ warmup5, repeat30, paired randomized؛ SHA baseline ثابت | elapsed p50/p95/p99، output hashes، process starts، SQLite/worker/claim wait؛ p99 n30 فقط اکتشافی؛ اندازه‌گیری سمت سرور، نه latency مدل | VERIFIED_LOCAL:12equivalentcases x warmup5/repeat30; output hashes equal, errors0, worker cleanup PASS. Report SHA256 e0eecb69816c8622eeae0677a02edeb2498d607fb368ad01d1caab7ef33fcdb4; worker claim/SQLite/serialization NOT_MEASURED until P14-02; p99 exploratory only |
| B-PREFLIGHT | fixture 100/1000/5000 فایل، clean/dirty/multi-package و cold/warm؛ default و opt-in در برابر N فراخوانی هم‌ارز | default wire unchanged و p95 <= +5% با کنترل نویز؛ opt-in p95<=500ms و JSON<=16KiB و سریع‌تر از N درخواست | DEFERRED / فقط در صورت evidence نیاز و Go مستقل |
| B-JOB-CONTENTION | 1/2/4 Job هم‌زمان، shared/isolated fixture؛ تفکیک admission از claim wait و proc time | اثبات وجود/عدم وجود گلوگاه واقعی پیش از طراحی MGL؛ هیچ claim سرعت پیشینی | NOT_MEASURED؛ P14-06 DESIGN_BLOCKED |

Benchmark ها در tmpownedfixture، seed/version/hardware/OS/runtime/load و baselineSHA/resultSHA ثبت شوند؛ mean تنهاکافی نیست. warm measurement با startup درهمنشود. threshold قبل نتیجهتعیینشده؛ تغییرپس ازشکستبدون ADR نه. هیچ speedup در اینطرح claim نشده است.

## ERROR / BLOCKER REGISTRY

| ID | state / affected tasks | علت واقعی و شاهد | رفع یا next action |
|---|---|---|---|
| HIST-CI-DEADLINE | RESOLVED_HISTORY | run39118430496/macOS deadlineassert؛ fix81d9f423... و sameheadgreen بعدی | regression نگه‌دار؛ عیب current نیست |
| HIST-CI-RETENTION | RESOLVED_HISTORY | run39118430496/Windows count3!=2؛ fix705bb838... scopedfixturecount | acceptance قرارداد retention نگه‌دار |
| GAP-CONTEXT-PAGE | RESOLVED / P01-01 | Historical snapshot pagination defect; P01-01 VERIFIED in Index/Ledger | Preserve pagination regressions; no restart |
| GAP-CONTEXT-TRUTH | RESOLVED / P01-02..04 | P01 completeness/depth/cache fixes VERIFIED; historical concurrency remediations retained in Ledger | Current source plus accepted regression evidence |
| GAP-LSP-SESSION | IMPLEMENTATION_VERIFIED / P02 | P02 tasks all VERIFIED; persistent mode default false; real providers UNVERIFIED | No automatic activation; keep native ownership/no-replay guards |
| GAP-LSP-INPUT-EDIT | RESOLVED / P03 | All5P03tasks VERIFIED on25ecb60 after four acceptance repairs; typed/legacy and durable recovery acceptance complete | Preserve tests; unopened-target rename fail-closed limit documented; realproviders remain UNVERIFIED |
| GAP-MULTILANGUAGE | IMPLEMENTATION_VERIFIED / P04/P06 | Bounded semantic fixtures, conservative selection and actual isolated runner acceptance recorded in Ledger; real LSP providers and aggregate benchmark remain UNVERIFIED | Preserve evidence boundaries; not an unimplemented Python-only gap |
| GAP-REGISTRATION | RESOLVED / P05-01 | P05-01 lazy construction VERIFIED; accepted fixture isolation repairs recorded in Ledger | Preserve zero-side-effect create/list/health contracts |
| BLOCK-P08-CATALOG-FOOTPRINT | RESOLVED_LOCAL / P08-01..04 | Real output-schema compaction48706->31036bytes; full catalogue191656(+18.73percent). Preregistered normalized allowances +32284bytes/+7487cl100k/+7882o200k PASS across all3profiles; shared schemas/non-output metadata identical | New reasoned budget keeps original full20percent and the same absolute fixed-definition allowance for smaller profiles; actualcoding30.09/core58.19percent retained; no validation removal or token/billing overclaim |
| TEST-ISOLATION-GLOBAL-SLEEP | KNOWN_MINOR / OUTSIDE_P08 | Preexisting local test_parallel_workflow_resources.py:760 patches the shared stdlib time.sleep; retention thread core/job_retention.py:320 hits pytest.fail, causing1warning in finalfull; no P08 failure demonstrated | Preserve unrelated user edit; separate test-isolation follow-up, no warning-free or retention-readiness claim |
| DESIGN-BLOCKER-01 | BLOCKED / P05-03 | clientrefresh transcript و UXbudget/version ناموجود | evidenceclient؛staticprofile مستقل |
| BLOCK-DAG-GO | RESOLVED / P07-02 | Explicit operator authorization and exact31c11ab checks/native ownership/live cap16/backup-restore accepted | Local Windows GO only; new deployment/version needs proportional revalidation |
| DESIGN-BLOCKER-02 | BLOCKED / P10-02 | grounder/privacy/calibration/nativeDPI/postcondition انتخاب نشده | design/hardwareevidence؛offlinecontract فقط |
| DESIGN-BLOCKER-03 | BLOCKED / P13-01 | agentisolation/providerbilling/authorization/transport نامشخص | strongdesignreview؛nodelegation |
| DEFER-AGENTS | DEFERRED / P11 | gap فرا host اثبات نشده | no-new-code تا نیاز |
| DEFER-CONTROLLER | DEFERRED / P12 | backend-only تصمیم؛hostintegrationscope مستقل | noautonomousruntime |
| PERMISSION-IMPLEMENT | LOCAL_ONLY_CURRENT | New audit/remediation request resumes local work; earlier branch-push authority is historical and the current local-only boundary is retained | No staging/commit/push/PR/main/release/tag/configuration or live-state activation in this session |
| RUNTIME-REAL-PROVIDERS | VERIFIED_WINDOWS_LIVE; HOSTED_AND_CROSS_OS_UNVERIFIED | Pinned Pyright/TypeScript LS/Intelephense published to active .agent_state/config after independent guarded backup; runtime PID13292→25320 generation1→2; tool count151; actual MCP tool calls returned cross-file definition, references3/3/4, real diagnostic reportArgumentType/TS2345/P1006 and clean repair; final active0/quarantined0 | Retain backup/rollback and do not claim Linux/macOS live-provider acceptance |
| BLOCK-P14-JOB-ISOLATION | BLOCKED / P14-06 | نبود containment اثبات‌شده برای arbitrary command و حتی verification runner اجرای کد پروژه؛ نیاز به اندازه‌گیری claim contention و بررسی conflict میزبان/Workspace | P14-01/02 measurement + مستقل safety review؛ no code change تا Go مستقل |
| DEFER-P14-PREFLIGHT | DEFERRED / P14-05 | چند رفت‌وبرگشت اولیه و سود تجمیع در workload واقعی هنوز اثبات نشده؛ ریسک افزایش latency خود project_summary | فقط با evidence و B-PREFLIGHT در گیت 500ms/16KiB فعال شود |
| CONFIG-P14-DAG-PROVENANCE | UNVERIFIED_PROVENANCE / P14-01, P07-02 | default کد False و گزارش live true؛ امکان تنظیم عمدی operator، نه defect قطعی | read-only trace effective config بدون افشای env/secret و بدون تغییر DAG |
| DEFER-P14-COMMAND-CACHE | OUT_OF_SCOPE / P14 | 69 hash بدون تکرار در نمونه 46دقیقه‌ای؛ نبود invalidation/side-effect contract | نه تعمیم به همه workloadها و نه پیاده‌سازی cache فرمان آزاد در P14 |

| PLAN-RESUME | RECOVERED | CHUNK08/23task سالم،SHAunchanged،checkpoint2026-10-19 | ادامه 09..final؛نشستازاولشروعنشد |

Blocker های آینده به معنی ناقص‌بودن طراحی P0 نیستند؛ قسمت‌های بازِ معماری به‌صراحت DESIGN_BLOCKED و خارج readiness قرار دارند. missingauthorization امروز permissionrequest برایتحویلطرح ایجاد نمی‌کند. شناسه error جدیداز sameassertionduplicate نساز؛ resolvedhistory حذف نشود.
<!-- CHUNK-13-END -->

<a id="weak-review"></a>
<!-- WEAK-EXECUTABILITY-REVIEW-START -->
## Weak-Agent Executability Gate — ارزیابی خواندنی واقعی

**نوع ارزیابی: PLAN_EXECUTABILITY_REVIEWED.** این بررسی در 2026-10-19 با استخراج واقعی بلوک‌ها از فایل، AST سورس Python، تطبیق SHA و parser دستورات PowerShell انجام شد. هیچ agent ضعیف جداگانه اجرا نشده، mutation محصول و pytest انجام نشده و نتیجه RUNTIME_VERIFIED نیست. readiness طراحی با permission/dependency برای اجرا فرق دارد.

چهار نمونه: TASK-P01-01 از context پایه، TASK-P02-05 از lifecycle نشست، TASK-P03-01 از validation/schema/compatibility، TASK-P09-01 از گیت حساس GitHub. Index هرکدام دقیقاً یک ردیف و marker شروع/پایان یکتا دارد. اندازه استخراج اولیه به‌ترتیب 37/21/15/39 خط و 3304/3816/2327/4776 کاراکتر بود؛ هرکدام هفت بخش دارد. برای lifecycle، فایل sessions.py صریحاً PROPOSED است و signature فعلی LspClient.request از source موجود خوانده شد؛ وجود manager ادعا نشده است.

### Dry-run A — TASK-P01-01

مسیر Quick Start → Index → phaseP01 → task مستقل قابل استخراج است. AST فعلی lookup_code_context در tools/project/context.py:272 ورودی offset:int=0 و max_relationships:int=100 را تأیید کرد. بررسی phase/source نشان داد offset روی flatten همه categoryها به‌صورت یک پنجره اعمال نمی‌شود؛ بازتولید P00 شاهد defect است. executor از blueprint می‌داند قبل از slice باید relationها را با category tag در ترتیب فعلی جمع کند، یک بار [offset:offset+limit] بگیرد، سپس به bucketهای خروجی برگرداند؛ count ازهمانpage و total ازقبلslice محاسبه شود. فقط context.py و test_code_context.py تغییر می‌کنند؛ registry API ثابت است.

تست‌های pagination پیشنهادی با fixture فعلی، offsetهای0/1/limit/total/aftertotal، empty category، ترکیبinclude flags، no duplicate/skip و count واقعی را می‌پوشانند. command scoped در خود بلوک آماده است. SHA/anchor mismatch یا تغییر خروجی legacy خارج این defect =>STOP. پس از actualtest evidence، فقط state row همانID و entry ledger به‌روزرسانی می‌شود؛ امروز هنوز NOT_STARTED.

### Dry-run B — TASK-P02-05

executor ابتدا dependencyهای P02-04 وP03-01 را از Index می‌خواند؛ بدون transport/provider/validator پذیرفته‌شده شروع نمی‌کند. source موجود tools/language/lsp.py:309، LspClient.request(self,method,params) یک‌بارمصرف است. فایل جدید sessions.py ازAPI فاز و الگوریتم هفت‌مرحله‌ای task ساخته می‌شود: entrySTARTING پیش ازthreadstart، ownerواحد، queue محدود، monotonicID، cancel bridge، retire/quarantine، idlecleanup. حد global32 queued،8/session و4sessions معلوم است؛ dispatcher زیرmanagerlock اجرا نمی‌شود.

tests/test_lsp_sessions.py و tests/fixtures/persistent_lsp_server.py به‌صراحت محصول predecessor هستند؛ وجودشان درHEADکنونی شرط نیست، اما نبودشان پس ازdependency VERIFIED شرطSTOP است. assertionsspawn1/100warm، two-workspaceisolation، cancelledqueueddispatch0 وlate-resultعدمresolveدرخواستبعدی روشن‌اند. race خارج قرارداد به strongreview می‌رود. درaudit dependency P03-01 به اینtask افزوده شد تا مدلضعیف مجبوربهساخت validator حدسی نشود.

### Dry-run C — TASK-P03-01

AST سورس tools/language/registry.py:26 امضای _request(root:str,timeout_sec:float,method:str,params:dict[str,Any])->Any را تأیید کرد. تغییر دقیق: validator مشترک در tools/language/requests.py سپس validate قبل ساخت/اجرای LspClient؛ registry وdirectcaller هردو protected. strictbool/negative/overflow،URI خارجroot،symlink،UTF16 وbyte/depthcap مشخص‌اند. نام تست موجود test_utf16_position_counts_astral_characters_as_two_units در tests/test_lsp_client.py:16 واقعاً پیدا شد؛ denied tests جدید PROPOSED هستند.

command سه فایل موجود language_hardening/lsp_client/language_registry را اجرا می‌کند؛ spawn/request spy برایورودیردشده باید0 باشد. errorcode ثابت و canary همهsinkها بررسی می‌شود. extra=allow برایپنهان‌کردنfailure راه‌حل نیست. defaultpublicparams دراینtask هنوزdict است؛ typedpublic schema بهtask بعدی واگذار شده وcompatibility روشن است.

### Dry-run D — TASK-P09-01

همه ابزارهای لازم فقطPowerShell/git/gh/read هستند. SHAمحلی/remote،PR81وrunهایاصلی واقعاًخوانده شدند؛ currentmain همانSOURCE_BASE است. runbook آینده PR را ازbranch واقعی پیدا و headRefOid راباlocalHEAD مقایسه می‌کند. وضعیت pending/absent/skipped/failure باsuccess فرق دارد؛10check الزامقرارداد است حتی وقتیbranch protected=false.

هیچcommand mutation درdry-run اجرا نشد. مسیرنبودPRبدونمجوز،wronghead،403،logfailed،publish=false وpost-merge newmainSHA مشخص است. branch/commit/push/PR/merge/release ازmodeخواندنی استنتاجنمی‌شود. taskVERIFIED فقطrunbook وliveaudit است، نهمجوزانتشار.

### Executability Scorecard

هر PASS به شاهد همان ستون متکی است؛ معیارهای این جدول سنجش کیفیت طرح‌اند، نه تضمین موفقیت agent یا کد. هیچ N/A به‌صورت PASS شمرده نشده است.

| معیار | P01-01 | P02-05 | P03-01 | P09-01 |
|---|---|---|---|---|
| WEAK-01: Index/marker یکتا | PASS؛1row/1pair استخراج‌شده | PASS؛1row/1pair | PASS؛1row/1pair | PASS؛1row/1pair |
| WEAK-02: SHA وcontract | PASS؛SOURCE_BASE وsignatureAST | PASS؛SOURCE_BASE + predecessor hashes | PASS؛SOURCE_BASE + _request AST | PASS؛livegh SHA/PRrun identity |
| WEAK-03: فایل/نماد واقعی | PASS؛context.py272،test موجود | PASS؛LspClient309 موجود؛sessions صریحاًADD | PASS؛registry26 وUTF16test16 | PASS؛gitregistry37،gh2.74.2 |
| WEAK-04: ترتیب تغییر بسته | PASS؛tag/flatten/slice/bucket | PASS؛هفتگامowner/queue/state | PASS؛models→validate→dispatch | PASS؛read→gate→authorizedpublication |
| WEAK-05: state/error/concurrency | PASS؛page/count/truncation | PASS؛STARTING/READY/retire/quarantine + caps | PASS؛strictboundaries وdispatch0 | PASS؛pending/failure/stalehead/permissions |
| WEAK-06: test/assertion روشن | PASS؛offsetmatrix + scopedcommand | PASS؛spawn/cancel/cleanup fixture | PASS؛UTF16/URI/canary/spy0 | PASS؛sameSHA و10jobstatus خواندنی |
| WEAK-07: Stop/Escalation | PASS؛drift/contract mismatch | PASS؛unknownrace/ownership→strong | PASS؛invalidschema/validrequestregression | PASS؛auth/PRmissing/wrongSHA |
| WEAK-08: permission gate | PASS؛IMPLEMENT_LOCALلازم | PASS؛activationdefaultoff | PASS؛no arbitrarycommand | PASS؛4mode وnoimplicitmerge |
| WEAK-09: safe state update | PASS؛row1 + appendEVIDENCE-1 | PASS؛همانقاعدهhash/count | PASS؛همانقاعدهhash/count | PASS؛weight0auditجدا |
| WEAK-10: context bounded | PASS؛37خط + namedexcerpts | PASS؛21خط + phaseAPI،reviewگیت | PASS؛15خط + modelsبخشP03 | PASS؛39خطrunbook،read-only |

**Score: هر نمونه10/10؛ جمع40/40 معیار طراحی PASS.** این امتیاز از مرور دستی خود planner و چک ساختاری به دست آمده، نه benchmark یک مدلضعیف یا independentreview. تقسیم taskها پس ازبازبینی به43 واحد رسید: 8WEAK_READY،23WEAK_WITH_REVIEW،9STRONG_ONLY و3DESIGN_BLOCKED. بلوک‌هایdesignblocked دستورcodingناقصِ آماده‌اجرا معرفی نشده‌اند؛ تصمیم‌های باقی‌مانده و مسیر رفعمانع مشخص‌اند. P03-05 وintegrationچندمسیرهP06-04 عمداًstrong-only هستند.

### Task Handoff Example — واقعی و قابل‌استفاده

~~~text
TASK-ID: TASK-P01-01
EXECUTOR-CLASS: WEAK_READY
SOURCE: main @ e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4
CURRENT FILE: tools/project/context.py
CURRENT SYMBOL: lookup_code_context, line272; offset:int=0, max_relationships:int=100
PROPOSED CONTRACT: global pagination across tagged caller/callee/reference/import/test edges.
STEP 1 PRECHECK: shared SHA guard; exact function count=1; preserve panel/index.html and uv.lock.
STEP 2 EDIT: collect tagged relations before truncation; slice once; bucket page; derive count/next offset.
STEP 3 CONSUMERS: project.registry.code_context and perf_benchmark._context_lookup_once keep signature.
STEP 4 TEST: task-local tests/test_code_context.py command; full page traversal equals ordered full set.
STEP 5 REVIEW: no source/provenance/schema field loss; git diff --check; focused Ruff; required mypy.
STOP IF: SHA/anchor differs without accepted predecessor, unrelated change needed, test fails without cause.
EVIDENCE: actual test exits + tested HEAD/diff + changed files in EVIDENCE-1; then only this state row.
CURRENT RESULT: PLAN_EXECUTABILITY_REVIEWED; product implementation NOT_STARTED.
~~~

### اصلاحات حاصل از audit، بدون بازنویسی بخش سالم

نام مسیر browser test به موجود واقعی اصلاح شد؛ shorthand چندtest بهpathهایجدا تبدیل شد؛ module درخواستLSP بهrequests.py یکسان شد؛ ADRمعماری چندزبانه بهADR-06 اصلاح شد؛ وابستگیvalidator پیش ازsessionmanager روشن شد؛ Python3.10 بدونفرضtomli fallback metadata دارد؛ profilefilter بهseam عمومی synchronous add_tool منتقل شد؛ perf suite lsp باSUITES/run_benchmarks/build_parser واقعی طرح شد؛ signature-only Python fence بهtext تبدیل شد. این‌ها اصلاح موضعی اشکالات blueprint بودند وfeature/source تغییر نکرد.
<!-- WEAK-EXECUTABILITY-REVIEW-END -->
<!-- CHUNK-14-END -->

<a id="ledger"></a>
<!-- EXECUTION-LEDGER-START -->
## EXECUTION LEDGER — ساخت سند، نه اجرای محصول

| واحد ساخت | خروجی ثبت‌شده | شاهد / وضعیت ساخت |
|---|---|---|
| PLAN-P14 (2026-10-26) | الحاق خواندنی/PLAN_ONLY فاز P14، هفت task و سه ADR؛ counts/Index/Dashboard/benchmark/blockers همگام شدند | هیچ سورس، تنظیم زنده، Job، Git یا CI تغییر نکرد؛ اجرای آتی نیازمند IMPLEMENT_LOCAL، SHA guard و EVIDENCE-1 واقعی است |

| CHUNK-01 | Quick Start،Index،dashboard | بازیابی‌شده؛ counts نهایی با43task همگام شد |
| CHUNK-02 | snapshot،Git/CI،11یافته+2supplemental | SOURCE_BASE وlivegh دوباره2026-10-19تطبیق داده شد |
| CHUNK-03 | graph/source map،ADR،shared contracts | گراف retrievalaid؛ sourcecritical تأیید؛ index rebuild نشد |
| CHUNK-04 | P00/P01blueprint | بازیابی‌شده؛ taskmarkerها سالم |
| CHUNK-05 | P02fixture/provider/transport | بازیابی‌شده؛ lifecycle contract حفظ |
| CHUNK-06 | P02manager/documents/wiring/acceptance | بازیابی‌شده؛ dependency/benchmark اصلاح موضعی |
| CHUNK-07 | P03validation/schema/edits/durable recovery | بازیابی‌شده؛122474bytes/19task تا این نقطه |
| CHUNK-08 | P04codeintelligence | checkpoint قطعی139306bytes/941line/23task؛ آخرین بخش کامل قبل توقف |
| CHUNK-09 | resume وP05 | appendverified؛152916bytes/26pair |
| CHUNK-10 | P06با دوtask اضافی برایintegration/acceptance | appendverified؛169680bytes/31pair |
| CHUNK-11 | P07/P08با دوtask اضافی برایoutputs | appendverified؛188199bytes/37pair |
| CHUNK-12 | P09..P13 | appendverified؛207334bytes/43pair |
| CHUNK-13 | runbook،CIgates،benchmarks،blockers | appendverified؛ پس ازاصلاحاتموضعی225606bytes/1509line |
| INDEX-RECONCILE |43row،dependencyDAG،counts،20milestone | incremental exactpatch؛ خطایترتیبhunk یک‌بار بدونwrite رخداد و باهویتفایلثابت اصلاح شد |
| CHUNK-14 |4dry-run،40criterion،handoffواقعی | PLAN_EXECUTABILITY_REVIEWED؛ خودبازبینی planner، نه independent agent |
| CHUNK-15 | ledger وfinalaudit | COMPLETE؛ structural/operational checks زیر پاس شدند |

اندازه‌های تاریخی بالا فقط نقطه ثبت همان append هستند؛ اندازه نهایی فایل در گزارش تحویل از filesystem خوانده می‌شود. digest فایل داخل خودش قرار نمی‌گیرد؛ این کار self-reference می‌سازد. source hashها وSHAها درsnapshot ثابت‌اند. هیچentry گذشته برایجاافتادنادعایpassساخته نشده است.

### Evidence اجراشده در این مأموریت

- خواندن UTF-8 سندV3،AGENTS،skill اصلی planning و source/config/tests/docs مرتبط؛ Gitstatus/history/diff وghread-only.
- CodebaseMemory exactsymbol/trace وGraphify scopedgraph؛ coverage/freshness وsourcecritical بررسی. trace verification سهcaller را نشان داد و source چهارمین registrycaller را تکمیل کرد؛ graphجایsourceproofنیست.
- introspection کاتالوگ با guard درون‌فرایندیِ منعwrite/spawn/network و lazy substitutes مستند P00؛ نتیجه count/bytes واقعی، نهstartup/fullhealthpass.
- بازتولید AST محدود pagination با داده ساختگی درحافظه؛ همانfunctionsource، بدونwrite/importمحصول؛ counterexample ثبت‌شده.
- بازیابی checkpoint وmarkerها؛ read-onlyghrevalidation ثابت‌ماندنHEAD/PR/runs؛ branchprotected=false وreviews=[] شفاف.
- ASTparse فقط برای یافتنsignatureها وsyntax snippet؛ PowerShell Parser.ParseInput فقطبرایsyntax، نهInvoke. structural index/dependency/link/marker/UTF8 checks.
- هیچ pytest محصول، benchmarkruntime، FullDoctor، migration، deployment، agentdelegation یا hostedworkflowdispatch دراینمأموریت انجام نشده است.

### Final structural / operational audit

FINAL-AUDIT-PASS — 2026-10-19: strict UTF-8؛43 جفت task marker یکتا و43 ردیف Index منطبق؛14 فاز و15 chunk کامل؛ هفت بخش در تمام43 task؛ dependencyهای43 task بدونچرخه/شناسهناموجود؛53 لینک داخلی و109 anchor بدونbroken/duplicate؛ QuickStart تاخط102؛3 بلوک Python و1 بلوک JSON قابلparse؛38 بلوک PowerShell بدونخطایParser.ParseInput؛ پایانMASTER-PLAN-END یکتا؛ weights29 وcounts/classification مطابقIndex؛ git diff --check exit0 وstatusهمان panel/uv.lock پیش‌موجود. فقطsyntax/structure/read-only بررسی‌شده، هیچفرمانfuturetest اجرا نشد. چهارdry-run طراحی PASSباحدودصریح؛ sourcepathsموجود/PROPOSED/fixtureازهمتفکیکشدند.

### وضعیت تحویل و اقدام دقیق بعدی

خروجی تنها همین فایل canonical درDownloads است؛ درcheckout/worktree/docs/cloud کپی یاlink ساخته نشده است. فایل اصلیV3 ورودی باقی مانده؛ masterقدیمیv0.8 بازسازی نشده است. source/test/Git/PR/release/settings تغییر نکرده‌اند. index authority وضعیتtask است؛ همهچیزپیشنهادی به‌جزسهaudit باوزن0 هنوز اجرا نشده است.

**EXACT NEXT TASK: TASK-P01-01.** در مأموریت IMPLEMENT_LOCAL جداگانه و مجاز: QuickStart وsharedcontract وبلوکTASK-P01-01 → تطبیقHEAD/anchor → regressionpagination → patchمحدود → focusedtests/requiredchecks → evidence/indexupdate. تاوقتیscopeهمینPLAN_ONLYاست، اقدامبعدی فقطتحویل فایل کامل است؛ هیچimplementation خودکار شروع نمی‌شود.

### IMPLEMENT_LOCAL EVIDENCE — TASK-P01-01 (2026-10-19 11:45:31 +03:30)

| field | value |
|---|---|
| task_id | TASK-P01-01 |
| state | VERIFIED |
| source_head | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 |
| tested_diff_sha256 | 80251a736ba04373176424a52f06868b81e4a94504e7e45a851132c10a71c8c7 |
| permission_reference | user message 2026-10-19: IMPLEMENT_LOCAL granted for all unblocked tasks; base SHA e66c8c9c |
| changed_files | tools/project/context.py (f9f53308552527bba97139eaa0de3a08be600db4ea590e00a6d2905bdca76294); tests/test_code_context.py (2d9ad299f42a5113c39bd2b77c1f01e657d914396844842d03371cc60849e89d) |
| preflight | HEAD exact; anchor "relationships = [*callers, *callees, *references, *imports, *tests]" count=1; all eight SOURCE_ANCHOR SHA-256 values matched the plan table before edit |
| behavior_before | offset=2,limit=2 => references+tests (expected callees+references); offset>=total still returned references+tests rows; count window math could disagree with emitted rows |
| behavior_after | offset=2,limit=2 => callees 1 + references 1, count=2, truncated=true; offset=3,limit=2 => references 1 + tests 1, truncated=false; offset>=total returns all-empty categories |
| tests_added | test_lookup_code_context_paginates_every_category; test_lookup_code_context_pagination_has_no_duplicate_or_missing_rows; broadened EXISTING test_lookup_code_context_paginates_relationships_across_categories |
| RED | `pytest -k paginat` => 2 failed, 1 passed, exit 1 (global slicing mismatch at index 0 for offset=2) |
| GREEN | `pytest -o addopts= -q tests/test_code_context.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0101-full` => 7 passed, exit 0 |
| ruff | `ruff check tools/project/context.py tests/test_code_context.py` => All checks passed, exit 0 |
| mypy | `mypy core tools scripts tests` => Success, 270 files, exit 0 |
| schema_defaults | lookup signature, defaults (max_relationships=100, offset=0), category names, envelope keys unchanged; no new imports |
| do_not_touch | tools/panel/index.html and uv.lock untouched by this task |
| risks | page composition is now contiguous in category order, so a category boundary can split across pages (intended global pagination); depth/completeness behavior intentionally deferred to TASK-P01-02 |
| blockers | none |
| reviewer | pending strong review at M01a milestone boundary |
| next_task_id | TASK-P01-02 |

### IMPLEMENT_LOCAL EVIDENCE — TASK-P01-02 (2026-10-19, resumed after context cutoff)

| field | value |
|---|---|
| task_id | TASK-P01-02 |
| state | VERIFIED |
| source_head | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 |
| tested_diff_sha256 | 0a0c60c10622c40934c94dd80c1ca16485016e44195e4870a083782b33c938a2 (diff for context.py + test_code_context.py) |
| permission_reference | user message 2026-10-19: IMPLEMENT_LOCAL granted; executor+reviewer authorized for baseline advancement |
| changed_files | tools/project/context.py (04e5a52133371b7ae867f0c77c22aaf00145d8fb7ee38a73d7a3fb329963b75c); tests/test_code_context.py (63080433b2925d0ae5a27a39b28ddc86b537f03354a018117d0fedee75a6a744) |
| resume_note | working tree already contained P01-02 edits from the cutoff session; inspected, completed missing evidence, and re-ran all gates before marking VERIFIED |
| preflight | HEAD exact; P01-01 row VERIFIED; anchor `complete = not index.scan_truncated and not relationships_truncated` count=1 before edit |
| behavior_before | no confidence/source_kind on rows; tests extraction nested under include_callers; complete ignored parse_errors and source truncation; no semantic_complete or confidence_summary |
| behavior_after | every evidence-bearing row carries confidence (name_only→inferred, others→static_resolved) and source_kind=python_ast; test rows emit with include_callers=False; incomplete_reasons covers scan_truncated/relationships_truncated/parse_errors/source_truncated; complete=not reasons; semantic_complete=False; confidence_summary counts only the current relationship page |
| tests_added | test_context_parse_errors_prevent_complete; test_context_name_only_is_inferred; test_context_tests_do_not_depend_on_caller_visibility; test_context_source_limit_reports_incomplete; test_context_imports_row_reports_binding_confidence |
| RED | baseline context.py restored from HEAD with new tests => 7 failed, 5 passed, exit 1; current file hash restored byte-identical (RESTORED_MATCH=True) |
| GREEN | `pytest -o addopts= -q tests/test_code_context.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0102` => 12 passed, exit 0 |
| ruff | `ruff check tools/project/context.py tests/test_code_context.py` => All checks passed, exit 0 |
| mypy | `mypy core tools scripts tests` => Success, 270 files, exit 0 |
| compatibility | lookup signature/defaults, category names, evidence literals, pagination semantics, and envelope keys preserved; new keys are additive |
| do_not_touch | tools/panel/index.html and uv.lock untouched by this task |
| risks | source_truncated makes complete=false whenever any definition excerpt is clipped; intended “in-bounds answer” semantics, semantic_complete remains the runtime-truth guard |
| blockers | none |
| reviewer | dual-role executor/reviewer accepted; no independent reviewer claimed |
| next_task_id | TASK-P01-03 |

### IMPLEMENT_LOCAL EVIDENCE — TASK-P01-03 (2026-10-19, autonomous loop)

| field | value |
|---|---|
| task_id | TASK-P01-03 |
| state | VERIFIED |
| source_head | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 |
| tested_diff_sha256 | 6cfb97f04ae40df21e844c59255a198df9076ad67ee7c27e4d6947e104d58487 |
| permission_reference | user message 2026-10-19: IMPLEMENT_LOCAL granted; executor+reviewer authorized |
| changed_files | tools/project/context.py (d13164715e014bc32eb0176786d569f25af444acafdeeb755f5f349e2c26c1c7); tools/project/registry.py (d58dd481a2996f98bd815141f65b9af9c1a57d3c55c24be3b41520faba7d3802); tests/test_code_context.py (bb1342f6592ed5b53b387501920dbff3be4b7ac53c3c0513ca9de835327e932e) |
| preflight | HEAD exact; P01-02 VERIFIED; anchors `del max_depth`, registry `Field(ge=1, le=2)` and current caller-loop each count=1 |
| implementation | identity=(file,qname,line,kind); one-pass incoming/outgoing adjacency with module sentinel; separate deterministic BFS per direction; min-distance visited; name_only edges emitted but never expanded; depth0 returns definitions/source only; references/imports only for initial matches at depth>=1; definitions capped at 1000; 1000 vertices / 10k scanned calls / 1s / batch-64 budget; additive depth, via_qualified_name, query_depth, traversal_truncated, total_exact |
| tests_added | test_context_depth_zero_one_two; test_context_depth_cycle_is_finite; test_context_inferred_edge_is_not_transitively_expanded; test_context_traversal_budget_reports_lower_bound; test_context_depth_pagination_is_deterministic; test_context_depth_helper_rejects_out_of_range_and_bool; test_code_context_schema_depth_bounds_and_bool_rejection |
| RED | new tests against pre-task context => 5 failed, 14 passed, exit 1 (missing query_depth/traversal_truncated/time import; no helper validation; schema rejected depth0) |
| GREEN | `pytest -o addopts= -q tests/test_code_context.py tests/test_tool_profiles.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0103-final` => 27 passed, exit 0 |
| related | `pytest tests/test_project_cache.py tests/test_mcp_integration.py` => 39 passed, exit 0 |
| ruff | `ruff check tools/project/context.py tools/project/registry.py tests/test_code_context.py` => All checks passed, exit 0 |
| mypy | `mypy core tools scripts tests` => Success, 270 files, exit 0 |
| strong_review | depth1 baseline rows preserved (consumer.execute caller, helper callee, test_service test); depth2 adds exactly the next static hop; cycles terminate; name_only expansion blocked; edge budget deterministic under patched monotonic; schema rejects depth3 and bool before helper |
| do_not_touch | tools/panel/index.html and uv.lock untouched by this task |
| risks | global 1000-vertex budget is shared by incoming then outgoing; time budget can truncate very large adjacency scans; both report truncation rather than silently claiming completeness |
| blockers | none |
| reviewer | dual-role executor/reviewer accepted; algorithm reviewed against contract |
| next_task_id | TASK-P01-04 |

### IMPLEMENT_LOCAL EVIDENCE — TASK-P01-04 (2026-10-19, autonomous loop)

| field | value |
|---|---|
| task_id | TASK-P01-04 |
| state | VERIFIED |
| source_head | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 |
| tested_diff_sha256 | 5790c27eddd958ccd518ef86231d926af4f445afdeab6c3aa640098f96b97df4 |
| permission_reference | user message 2026-10-19: IMPLEMENT_LOCAL granted; executor+reviewer authorized |
| changed_files | tools/project/index.py (78c8fb62341319bc316ed57d061eb20053fed30574545737b3cfd26dedbb6053); tools/project/context.py (006ee673710c19d32295ffb74c19008d8df94dc5b015d7b19fbaf035f49eee9a); tools/project/service.py (f4c96ebe5282e716c2c93777fe7519e5783cffe150e2519bb242903160f0c53a); tests/test_project_cache.py (7de7344d85f3c83e8acde50b94a4bbddb60e05dbace76d41ec4432cc1a63ddf5); tests/test_code_context.py (ba08bf167281a26bedf38db69aeac908260f6af08bde4c7dea3a0cd695454587) |
| preflight | HEAD exact; P01-03 VERIFIED; anchors FileVersion/_file_signature/ContextIndexCache/_version/build_python_metadata/iter_files each count=1 |
| implementation | FileVersion gains streaming sha256 (64 KiB chunks, 2 MiB cap; too-large => project_file_too_large; stat before/after mismatch => project_file_changed_during_parse); build_python_metadata bounds reads and reports too-large as parse_error; context signature = canonical path+mtime+size+sha256; snapshot_id = length-framed sha256(root,max_files,signature); cache capped at 16 entries/64 MiB with OrderedDict LRU, deep-size accounting, oldest eviction, oversized not cached; 16 active build cap => context_capacity_exceeded; 5 s build deadline => index_deadline partial (never cached); max 2 build attempts against a stable signature else context_stale; generation guard blocks post-invalidation publication; source excerpts re-verify per-file version; iter_files sorts dirs/names before cap and skips symlinked dirs; outside-root symlink files skipped with symlink_outside_root reason |
| tests_added | test_file_version_detects_same_size_same_mtime_content_change; test_context_same_size_same_mtime_change_invalidates_both_caches; test_context_delete_rename_invalidates; test_context_build_cannot_publish_after_invalidation; test_context_lru_count_and_byte_limits; test_context_source_snapshot_change_never_returns_mixed_rows; test_context_build_deadline_raises_before_any_snapshot; test_context_symlink_outside_root_is_skipped; test_project_large_file_is_bounded; test_project_discovery_sorts_before_cap |
| RED | new tests against pre-task code => 7 failed, 22 passed, exit 1 (missing file_version/content digest, cache budget args, large-file bound) |
| GREEN | `pytest -o addopts= -q tests/test_project_cache.py tests/test_code_context.py tests/test_filesystem_registry.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0104c` => 63 passed, exit 0 |
| related | test_mcp_integration + test_tool_profiles + test_affected_tests => 44 passed; test_apply_patch/search/semantic/workspace/verify_changes => 66 passed |
| full_suite | `pytest -o addopts= -q -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0104-full` => 1604 passed, 6 skipped, 1 warning, exit 0 (421 s) |
| ruff | `ruff check tools/project/index.py tools/project/context.py tools/project/service.py tests/test_project_cache.py tests/test_code_context.py` => All checks passed, exit 0 |
| mypy | `mypy core tools scripts tests` => Success, 270 source files, exit 0 |
| strong_review | two-layer cache accepted together: same-size/same-mtime mutation invalidates metadata and context caches; delete/rename invalidates; invalidation during build cannot publish (generation check); LRU count/byte caps evict and oversized entries are returned uncached; source snapshot change never returns mixed rows (retry then stale/error); deadline fails closed before any snapshot; no DB/file side effects |
| do_not_touch | tools/panel/index.html and uv.lock untouched by this task |
| risks | digests are computed multiple times per build (signature before/after plus cache internals) and this cost is bounded but unmeasured; outside-root symlink digests are still hashed in the outer signature; both are documented follow-ups for bench B-CTX |
| blockers | none |
| reviewer | dual-role executor/reviewer accepted; full-suite gate green |
| next_task_id | TASK-P02-01 |

### IMPLEMENT_LOCAL EVIDENCE — TASK-P02-01 (2026-10-19, autonomous loop)

| field | value |
|---|---|
| task_id | TASK-P02-01 |
| state | VERIFIED |
| source_head | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 |
| tested_diff_sha256 | 3dc3fffcb9f8366e5df3c9b5bcb8d5bb80323ccecf8281b5aa9f0cb03ae11eca (sha256 of sorted `path:sha256` manifest) |
| permission_reference | user message 2026-10-19: IMPLEMENT_LOCAL granted; executor+reviewer authorized |
| changed_files | tests/fixtures/persistent_lsp_server.py (f0a0351332f10fa76d499cbe56449b2858afa8d5d10bfd2a707eae61064b0add); tests/test_lsp_sessions.py (8e96ed51cc52c063e02cac5ce27f740295ee220a3c98780cb64db1b6d56d7041) |
| preflight | HEAD exact; P01-04 VERIFIED; existing fixtures fake_lsp_server.py / strict_lsp_server.py / oversize_lsp_server.py and LspClient(command,workspace,*,timeout_sec,max_message_bytes,max_total_bytes)/request signatures inspected; artifacts are test-only |
| implementation | fixture: stdio Content-Length loop until exit or stdin EOF; --mode enum normal/slow/malformed/crash/oversize/hang-shutdown/flood/reverse-replies; initialize caps textDocumentSync{openClose:true,change:1}/hoverProvider/definitionProvider/referencesProvider/callHierarchyProvider; state initialized/opened uri->(version,text)/request_count; test/inspect returns pid/mode/initialized/request_count/open_count/documents/fixture_digest/held_requests; test/emitApplyEdit sends server->client workspace/applyEdit; duplicate initialize -> -32600; didChange before didOpen or non-increasing version -> -32602; shutdown replies then exit; log file only with --log; stderr canary only in flood mode; harness FixtureSession: threaded read timeout, server-request collection, stdin-EOF graceful close then taskkill /T /F on win32 / terminate on POSIX, _process_alive descendant check |
| tests_added | test_fixture_reuses_process_for_two_requests; test_fixture_enforces_document_versions; test_fixture_shutdown_exits; test_fixture_close_leaves_no_descendant_alive |
| RED | first harness run 1 failed, 2 passed: venv python.exe is a launcher shim (Popen.pid 2792 vs interpreter pid 21444, stable 21444 across two requests) so Popen.pid equality was wrong and terminate() orphaned the real interpreter; mypy then flagged 6 defects (object.get narrowing, result redefinition); harness and fixture fixed before GREEN |
| GREEN | `pytest -o addopts= -q tests/test_lsp_sessions.py -k fixture -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0201` => 4 passed, exit 0, 1.51 s (1 pre-existing cache_dir config warning) |
| ruff | `ruff check tests/fixtures/persistent_lsp_server.py tests/test_lsp_sessions.py` => All checks passed, exit 0 |
| mypy | `mypy core tools scripts tests` => Success, 272 source files, exit 0 |
| strong_review | constant fixture pid across two requests proves one long-lived process; didChange rejects unopened document and non-increasing version with -32602; shutdown returns null and exit yields code 0; after stdin-EOF close the real interpreter pid is gone (descendant check); fixture performs no network IO, imports no production modules; harness imports stdlib+pytest only; test/inspect and test/emitApplyEdit are test-only methods outside the production allowlist |
| do_not_touch | tools/panel/index.html and uv.lock untouched by this task |
| risks | sys.executable on this Windows venv is a launcher shim so Popen.pid is not the interpreter pid; later session-manager tasks must not treat Popen.pid as the LSP server identity and must use tree-aware cleanup (stdin EOF first) |
| blockers | none |
| reviewer | dual-role executor/reviewer accepted; scoped gates green |
| next_task_id | TASK-P02-02 |

### IMPLEMENT_LOCAL EVIDENCE — TASK-P02-02 (2026-10-19, autonomous loop)

| field | value |
|---|---|
| task_id | TASK-P02-02 |
| state | VERIFIED |
| source_head | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 |
| tested_diff_sha256 | 8948f63089b3b5137bc5e312e93bb120c4ea7d91eea527833e12159740895ab5 (sha256 of sorted `path:sha256` manifest) |
| permission_reference | user message 2026-10-19: IMPLEMENT_LOCAL granted; executor+reviewer authorized |
| changed_files | tools/language/providers.py (36c1d5a3a6d27c4533167851ed010b91bbe2fbc6609f98d1ba929b12c348368e); core/config.py (d449abec77d79ca7183cc0ea016e3ea38eac506377f14ea3c3418c022fa39b60); tests/test_lsp_providers.py (7c9962dc6725a0f89e62cf527b09b31fed7d3f489fc62c1fed54707c56608624) |
| preflight | HEAD exact; P02-01 VERIFIED; registry._command and lsp.py signatures inspected; core/extensions/config.py JSON-strictness conventions reused; canonical_path in core/resource_locks.py aligns with existing path behavior |
| implementation | providers.py: LspProvider/SessionKey frozen dataclasses; load_lsp_providers enforces 64 KiB, depth 8, duplicate-key rejection, exact field sets, schema_version==1, 1..3 providers, fixed language subsets, absolute local paths, .cmd/.bat/.ps1 denial, lowercase hex64 digests, entrypoint/entrypoint_sha256 pairing (required for typescript/phpactor), 1..32 absolute trusted_roots, unique provider_id; command_for: pyright/typescript=(exe,entrypoint,--stdio), phpactor=(exe,entrypoint,language-server), standalone pyright=(exe,--stdio); validate_provider: exactly one language+canonical-root-eligible provider, lsp_provider_unavailable/lsp_provider_ambiguous, per-call regular-file and sha256 recheck => lsp_provider_changed; make_session_key: canonical workspace_root, .git file digest (<=4 KiB), .git dir canonical+stat identity, no-git marker, config_digest over canonical non-secret JSON, HEAD excluded |
| settings | core/config.py: persistent_enabled=_env_bool(MCP_LSP_PERSISTENT_ENABLED, False); providers_config=MCP_LSP_PROVIDERS_CONFIG; scalar read only, no config load at import |
| tests_added | test_provider_mapping_is_exact_and_does_not_spawn; test_native_standalone_pyright_command_has_no_entrypoint; test_duplicate_unknown_or_oversize_config_rejected; test_windows_batch_launcher_denied; test_provider_digest_change_denied_before_spawn; test_ambiguous_provider_selection_denied; test_worktrees_same_common_dir_have_distinct_keys; test_root_symlink_escape_denied; test_unicode_and_case_paths_follow_canonical_path; test_settings_scalar_flags_follow_environment |
| RED | `pytest tests/test_lsp_providers.py` against pre-task tree => ModuleNotFoundError collection error (module absent), exit 1 |
| GREEN | `pytest -o addopts= -q tests/test_lsp_providers.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0202` => 10 passed, exit 0, 2.05 s |
| related | test_tool_profiles + test_resource_health + test_extension_config + test_mcp_integration => 85 passed, exit 0 (18.7 s) |
| ruff | `ruff check tools/language/providers.py core/config.py tests/test_lsp_providers.py` => All checks passed, exit 0 |
| mypy | `mypy core tools scripts tests` => Success, 274 source files, exit 0 |
| strong_review | spawn spy proves zero process launches across load/command/validate/key paths; config canary absent from error str/repr (fixed messages only); symlink escape resolves outside trusted root via canonical_path and is denied; tampered or missing pinned binary/entrypoint denied before any dispatch; uppercase launcher suffixes denied; HEAD not read into the key; two worktrees under one common .git dir get distinct keys; Unicode paths accepted; case behavior follows canonical_path per platform |
| do_not_touch | tools/panel/index.html and uv.lock untouched by this task |
| risks | declared_version is an operator label only (no --version executed); st_ino fallback covers filesystems without inode identity; config_digest changes whenever any provider field changes, retiring old sessions by design |
| blockers | none |
| reviewer | dual-role executor/reviewer accepted; high-risk trust boundary reviewed (paths only from local config, hash recheck before dispatch) |
| next_task_id | TASK-P02-03 |

### IMPLEMENT_LOCAL INTEGRITY CHECKPOINT — TASK-P02-03 (2026-10-19, resumed session)

| field | value |
|---|---|
| task_id / state | TASK-P02-03 / BLOCKED before execution; no VERIFIED baseline advancement |
| permission_reference | user: IMPLEMENT_LOCAL; executor and reviewer authorized; explicit hard stop on unexpected source drift |
| source_head / branch / workspace | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 / main / LOCAL_PATH_REDACTED |
| orientation | strict UTF-8; Quick Start, ledger tail, active TASK-P02-03 block only; MASTER-PLAN-END intact; git status --short, diff --stat, diff --check, rev-parse HEAD, branch/remotes/recent history inspected |
| blocker | tests/test_project_cache.py expected SHA-256 7de7344d85f3c83e8acde50b94a4bbddb60e05dbace76d41ec4432cc1a63ddf5 from P01-04 VERIFIED ledger; actual raw SHA-256 0a9486bbda7a4c38046190af7aca350edbaf434cc4b830b79df80d26ad783901; LF-normalized SHA-256 identical to actual; outside active P02-03 file scope; provenance unresolved |
| guard_results | All other latest ledger file digests match except tests/test_lsp_sessions.py, whose edits belong to interrupted P02-03: actual d686e7a138c149a3813ed19ce62d50f281a01deeb438c897b246bd53a679d62d vs predecessor 8e96ed51cc52c063e02cac5ce27f740295ee220a3c98780cb64db1b6d56d7041. lsp.py changes and new transport.py also belong to interrupted P02-03; preserved as found |
| pending_checks | P02-03 scoped pytest, Ruff, full mypy: NOT RUN; the preflight PowerShell command threw DRIFT before reaching pytest. No test-spawned process in this session |
| product_edits_this_session | none; only this canonical plan checkpoint and Quick Start/index row updated |
| preserved | tools/panel/index.html pre-existing diff, untracked uv.lock, .agent_state, graphify-out, live DB/state and all interrupted product/test edits; no commit/stage/push/PR/release |
| next_action | Resolve provenance of tests/test_project_cache.py digest mismatch against P01-04 evidence; do not overwrite it or silently accept a new baseline. Revalidate affected predecessor evidence before resuming TASK-P02-03 and its scoped gates; downstream tasks not started |

### EVIDENCE-1 — TASK-P01-04 integrity revalidation (2026-10-19)
| field | value |
|---|---|
| authorization | User explicitly instructed this session to resolve all problems and continue, including checksum discrepancy; executor/reviewer baseline advancement authorized |
| state | VERIFIED by fresh revalidation; previous bytes cannot be reconstructed from ledger, historical cause UNVERIFIED; no test/product edit for this reconciliation |
| source_head | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 |
| changed_files | tests/test_project_cache.py (0a9486bbda7a4c38046190af7aca350edbaf434cc4b830b79df80d26ad783901) |
| review | Current diff remains within P01-04: content digest, same-mtime change, generation invalidation, count/byte eviction, stale-source retry, deadline, root escape, large-file and deterministic discovery regressions; production P01-04 and test_code_context digests still match prior verified evidence; no unrelated change found |
| checks | pytest test_project_cache.py test_code_context.py => 49 passed, 1 skipped, exit 0 (Windows symlink privilege unavailable); pytest test_filesystem_registry.py => 13 passed, exit 0; ruff affected P01-04 files => exit 0; mypy core tools scripts tests => 275 files, exit 0 |
| limitations | Native file-symlink regression skipped on Windows; no performance claim or Linux/macOS runtime claim; existing cache_dir warning |
| next_task_id | TASK-P02-03 |

### EVIDENCE-1 — TASK-P02-03 local implementation (2026-10-19)
| field | value |
|---|---|
| task_id / state | TASK-P02-03 / IMPLEMENTED_UNVERIFIED: local Windows gates pass; required POSIX gate unavailable |
| source_head | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 |
| permission_reference | Current user: resolve all problems and continue locally; executor+reviewer authorized; no Git publication |
| changed_files | tools/language/lsp.py (d2770a477852a3958cb20cb025e4aebe4372f99bd258b4b6dfcd16cec792adc2); tools/language/transport.py (fcad0cd7640c811b6137713169976217837f58305324376061ce6752dcc138dd); tests/test_lsp_sessions.py (b4eb153119c5f61b5adc47ee13861599cf7ae0ce8f563d1e4348bceb5e8487fd); CHANGELOG.md (b592ed816e49b6be9a15adef04981c97d487fdb58345ca788410a64f6c7598ad) |
| tested_diff_sha256 | a4356196597d057fec5b52eff6c55575370fe77b850b5ab6844d2b6327501888 (sorted path:sha256 manifest) |
| RED | existing canary test => 1 failed, 23 passed: initialize error leaked provider text; added outside-URI/provider-tamper/cleanup-false regressions => 3 failed, exit 1 |
| implementation | Native require_native ownership; only fixed OS env and executable-parent PATH; provider root/digest recheck at launch; document URI/read before legacy spawn; stderr continuously discarded; fixed protocol/initialization messages; cleanup failure cannot return success; live streams not closed while death unproven |
| GREEN | pytest -o addopts= -q tests/test_lsp_client.py tests/test_lsp_sessions.py tests/test_windows_job_backend.py tests/test_lsp_providers.py tests/test_language_registry.py tests/test_language_hardening.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0203-final => 38 passed, 1 skipped (provider symlink privilege), exit 0 |
| platform_check | pytest including tests/test_posix_process_ownership.py => 27 passed, 4 skipped, exit 0; all 4 skips require POSIX and therefore are NOT acceptance evidence |
| ruff / mypy | ruff check tools/language/lsp.py tools/language/transport.py tests/test_lsp_sessions.py => exit 0; mypy core tools scripts tests => 275 files, exit 0; git diff --check => exit 0 |
| review | Self-review covered LspClient callers, native cleanup protocols, denied-before-spawn spies, stderr/error canaries and real Windows descendant timeout reaping. No session activation, public arbitrary command, inherited secret environment or schema change |
| blocker | WSL list reports Wsl/WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED; Docker CLI absent. Installing OS feature/reboot is outside local source work. POSIX process ownership acceptance cannot be claimed on Windows |
| next_task_id | TASK-P03-01 (depends only on VERIFIED P02-02); P02-04 and descendants remain gated |

### EVIDENCE-1 — TASK-P03-01 (2026-10-19)
| field | value |
|---|---|
| task_id / state | TASK-P03-01 / VERIFIED |
| source_head / permission | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 / IMPLEMENT_LOCAL; dual-role executor/reviewer |
| preflight | P02-02 VERIFIED; registry.py snapshot digest exact; original hardening test digest c81e38a38dcb637446d525c3443934bd94ffcf8a512379c2cac91236faf984ee; scoped graph located shared _request before source review; real common and rename callers verified |
| changed_files | tools/language/requests.py (cebdab788a11d798aa7875c3b24d6b98e26b620c9a5934f96d5b519fc97c08e3); tools/language/registry.py (be0d5f2055416ef07aaeacd1566b4bf27e0b5fcff9bb7842165a9d24677a0f99); tests/test_language_hardening.py (7f704293c1e557cd29e48b59396beeeadf721e7e832ab5696340fcf971059674); tests/test_language_registry.py (da22a4240b01b72e5801a5d36e9ea57661c39689331eafdbae44cf9e94f00323); CHANGELOG.md (ed496166c78bd3f1062416d169c246169c63f246f16a5e27a7d8f009e132bd5e) |
| tested_diff_sha256 | 1adda0d2a14c93b7f6e1aa2a3cfb75b2baacdb0d1803e81aefd11ab39ec9099e (sorted path:sha256 manifest) |
| RED | pytest tests/test_language_hardening.py -k 'workspace_query or bounded or invalid_request' -x => invalid negative position reached spy dispatch; 1 failed, exit 1 |
| implementation | Strict extra-forbid Pydantic method models; no bool/coerced position; depth16/key100/64KiB JSON; local canonical file URI, strict escapes/UTF-8, NUL/query/fragment/netloc denial; bounded2MiB UTF16 position read before LspClient construction; standard methods preserved; tokens rejected until aggregation exists |
| GREEN | pytest -o addopts= -q tests/test_language_hardening.py tests/test_lsp_client.py tests/test_language_registry.py -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0301-final2 => 34 passed, 0 skipped, exit 0 |
| ruff / mypy | ruff check tools/language/requests.py tools/language/registry.py tests/test_language_hardening.py tests/test_language_registry.py => exit 0; mypy core tools scripts tests => 276 files, exit 0 |
| review | Direct and MCP denial spies prove zero dispatch; secret canaries absent from errors; real Windows directory junction escape denied; standard legacy definition/reference/document/call-hierarchy/diagnostic/rename accepted; workspace query reads no source; split-surrogate and overflow positions denied |
| test_isolation | Language tests now register the real language domain on a bare MCPServer with temporary audit settings, avoiding unrelated full-server JobStore/scheduler activation. Earlier full-server test invocations used pre-existing construction behavior; no explicit live DB edit was made, but incidental writes by that old behavior cannot be ruled out |
| limitations | Linux/macOS execution and persistent-session activation unverified; existing cache_dir warning; no Git publication |
| next_task_id | TASK-P05-01; P02 chain gated by POSIX acceptance |

### EVIDENCE-1 — TASK-P05-01 (2026-10-19)
| field | value |
|---|---|
| task_id / state | TASK-P05-01 / VERIFIED (local scope; no hosted publication) |
| source_head / permission | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 / IMPLEMENT_LOCAL; dual-role executor/reviewer |
| preflight | P00-02 VERIFIED; affected five original source files unmodified before task, heartbeat snapshot exact; scoped graph located ensure_job_scheduler, source confirmed synchronous launch_once plus background recovery thread |
| changed_files | core/jobs.py (ec58912ae80db100493be299f86157226d8aefd907180c0ab258aba106f1b591); core/registry.py (665aa0222282ba529235934d7a8533c4a52c6d6470921de097e036137adf0204); core/heartbeat.py (ff713ee64125d37e08d69e36e62cb29dfad891b763b464ee0da2b6a4469b8e74); core/extensions/bridge.py (ad7f33af161f8bb248a49e35394a1fa62c0433d3c76bd28e56e845842db141fb); tools/jobs/registry.py (9e3de80f5148721607203002e8459b326233ce4dd92974149b50b85c80dbfbcf); tools/testing/registry.py (a8c2414b251254bfe181c89e9d2b711ca4c47ed704e33c14c561f6083ebc3092); tests/test_tool_profiles.py (67f6afc3278a052e7cee672124a99836a9bddf404c2a3a14aeedc967ea992dd9); tests/test_jobs_registry.py (9652143640bf5e9dc0c2936dd5dfbfd34a8828e5370bec4ee7669c33adb2e873); CHANGELOG.md (44dffa5d969bad91b6c387e384307d9ad3c3c412eca07f011956945547495267) |
| tested_diff_sha256 | cbe119e1875ee9dd45a3c76bdc046f2e9c0b0f4cd0bf4e8bbac2c64bf64f4ef0 (sorted path:sha256 manifest) |
| implementation | One locked lazy_job_store factory in existing core/jobs.py, shared by core server jobs/testing domains; keyword-only default lazy getters preserve bare-registrar callers; native directories and queue recovery move to lifespan; existing submit kickoff retained; pre-startup health returns unavailable metadata without metrics/store activation; typed server fields live on existing ExtensionMCPServer, no extra owner/registry |
| RED | all-profile no-activation test first fails at create_server ensure_runtime_dirs (1 failed, exit 1); later environmental MCP_TIMINGS opt-in writer exposed by thread-start spy, isolated test explicitly disables optional timing instead of weakening spy |
| GREEN | pytest -o addopts= -q tests/test_tool_profiles.py tests/test_jobs_registry.py tests/test_background_verification.py --tb=short -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0501-green => 70 passed, exit 0 |
| expanded_checks | Isolated-settings pytest bootstrap, same three suites plus tests/test_lifecycle.py tests/test_extension_health.py tests/test_mcp_integration.py --tb=short -x -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0501-runtime => 114 passed, 0 skipped, exit 0. Bootstrap sets existing SETTINGS state/memory paths to disposable temp before tests; timings disabled unless explicitly enabled by a test |
| static | ruff affected six source files plus two test files => exit 0; mypy core tools scripts tests => 276 files, exit 0; compileall -q core tools scripts tests => exit 0 |
| startup_health | main.main(['--check']) with isolated state => ok true, tool_count 151, unique true; real stdio scripts.health_check.run_checks through temporary settings bootstrap => import/startup/registration/filesystem/terminal all true, 151 tools, exit 0 |
| review | 9 profiles prove no directory/store/Popen/thread activation for construction/list/discovery/router/pre-start health with optional timings off; 24 concurrent job reads and testing watch share exactly one store; fresh-store restart scans queued fixture once; store failure never yields startup; background reconnect/cancel and lifecycle/extension regressions pass; detached worker lifetime remains unchanged |
| scope_detail | core/jobs.py holds shared lazy helper rather than duplicate registrar locks; core/extensions/bridge.py declares typed server lifecycle fields to keep full mypy clean; no SDK/dependency/database/schema/flag change |
| limitations | Runtime smoke uses actual current source with isolated state, not live operator state; Linux/macOS and hosted same-SHA checks not run; optional explicit timing telemetry can still create its documented writer thread |
| next_task_id | TASK-P07-01; P05-02 depends on P03-04 and remains gated |

### EVIDENCE-1 — TASK-P07-01 local recovery matrix (2026-10-19)
| field | value |
|---|---|
| task_id / state | TASK-P07-01 / BLOCKED on required cross-platform/same-SHA gates; local matrix passed |
| source_head / permission | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 / IMPLEMENT_LOCAL; no publication authorized |
| source_review | SCHEMA_VERSION=11; workflow_store.py raw SHA-256 d567bf6d45981833a45cedaa9cd0f3c477c340761757fdbafcfd4ae32ae7a659; git shows no workflow/schema product edits; current migration/recovery tests and subprocess ownership paths inspected |
| tests | Isolated SETTINGS bootstrap then pytest -o addopts= -q tests/test_workflow_migrations.py tests/test_workflow_dag.py tests/test_parallel_workflow_scheduler.py tests/test_parallel_workflow_resources.py tests/test_parallel_workflow_recovery.py tests/test_workflow_cancellation.py tests/test_workflow_lease_heartbeat.py tests/test_workflow_crash_recovery.py tests/test_parallel_verification.py --tb=short -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0701 => 153 passed, 0 skipped, exit 0 |
| review | v9 ownership preserved; partial v10 upgrade rolls back before retry; v11 unknown effects fenced and legacy activation denied; real executor death preserves effects as uncertain without replay; caps/admission/quota/Git-common-dir/child ownership/cancel/heartbeat/retry/late-result gates pass on Windows |
| static | ruff check . => exit 0; git diff --check => exit 0; full mypy and compileall passed in immediately preceding P05-01 checks; no source edit for this acceptance |
| matrix | Windows current working diff: PASS (153 tests); Linux: NOT RUN; macOS: NOT RUN; packaging: NOT RUN; hosted same-SHA validation: NOT RUN |
| blocker | Local host lacks usable WSL (Wsl/WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED) and Docker. Hosted publication/CI mutation excluded by current user scope. Existing old same-SHA CI cannot validate this new working diff |
| invariants | No live DB migration, no schema/flag change, no replay/activation, no rollback of state, no commit/push/PR/release; P07-02 remains BLOCKED/NO-GO |
| next_task_id | TASK-P02-03 local bounded-document review amendment, then await required external platform evidence; no remaining NOT_STARTED task has all dependencies VERIFIED |

### EVIDENCE-1 — TASK-P02-03 bounded-document amendment and final local checkpoint (2026-10-19)
| field | value |
|---|---|
| task_id / state | TASK-P02-03 / IMPLEMENTED_UNVERIFIED: local Windows implementation green; required POSIX acceptance unavailable |
| source_head / branch / workspace | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 / main / LOCAL_PATH_REDACTED |
| permission | User explicitly authorized resolving all problems and continuing locally, executor/reviewer; no publication |
| changed_files | tools/language/lsp.py (0f4318d7f33cc59dddc8e4f0f282f108b066a2a6f1e9e9c691b1fcbf76320aac); tests/test_lsp_sessions.py (691cb5e48245020366459ea9fe0c83cef4df7f0e61ca9c95ba16da39d80734bf) |
| tested_diff_sha256 | 3e56c584764f80c4f3c47cf72de54ce89b9cb5b6bdb8998c118cae44b392c492 (SHA256 of sorted path:sha256 manifest joined by host newline; other P02-03 file digests unchanged) |
| RED | test_lsp_document_open_read_is_bounded => 1 failed, exit 1: didOpen preparation called read(-1), allocating before checking cap |
| GREEN | didOpen now reads at most 2MiB+1 before spawn; isolated-settings pytest -o addopts= -q tests/test_lsp_client.py tests/test_lsp_sessions.py tests/test_lsp_providers.py tests/test_windows_job_backend.py tests/test_language_hardening.py tests/test_language_registry.py --tb=short -p no:cacheprovider --basetemp=.pytest-tmp-plan-language-final => 64 passed, 1 skipped (provider symlink privilege), exit 0 |
| final_static | ruff check . => exit 0; mypy core tools scripts tests => 276 files, exit 0; compileall -q core tools scripts tests => exit 0; git diff --check => exit 0 |
| other_acceptance | P01-04: 49 passed/1 skipped plus 13 filesystem tests; P03-01: 34 passed/0 skipped; P05-01 expanded: 114 passed/0 skipped plus real isolated stdio startup/health, 151 unique tools; P07-01 local recovery matrix: 153 passed/0 skipped |
| cleanup / protected | psutil fixture-name scan: no remaining LSP fixture processes; panel SHA256 6f682baebf08556a4b5bab2d9d9e34f541ccf6ebbee13abda611a3c9b67bd3d4 and uv.lock SHA256 e9bbee1693a12ebdde82d62ef98722036e5b14db754842bd6c865375594e56aa unchanged; no staging/commit/branch/push/PR/release |
| remaining_blockers | P02-03 requires actual POSIX acceptance: Wsl/WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED, Docker CLI absent. P07-01 requires Linux/macOS plus packaging/hosted same-SHA evidence. No skip/unexecuted gate counted as success; all downstream dependencies remain enforced |
| state_caveat | Initial test invocations used pre-existing eager full-server construction and inherited optional timing settings; incidental operator-state writes cannot be ruled out and were not rolled back. Later broad tests and real stdio smoke isolate state/memory/timing destinations; no explicit live DB edit, deletion, restore or migration command |
| final_progress | Product implementation 9/29 (31.0%), verified 8/29 (27.6%); 11 VERIFIED tasks including zero-weight inspections; P02-03 not VERIFIED; no IN_PROGRESS task; original BLOCKED/DEFERRED exclusions retained |
| exact_next_action | Provide usable POSIX validation for current diff and finish P02-03 acceptance, then P02-04. P07-01 stays NO-GO until its independent same-SHA platform gates. OS-feature installation/reboot and publication remain outside local source scope |

### EVIDENCE-1 — autonomous resume and CI authorization (2026-10-19)
| field | value |
|---|---|
| active_task / state | TASK-P02-03 / IN_PROGRESS: recover required POSIX gate, preserve existing implementation |
| authorization | New user mission explicitly permits dedicated branch, selective project commits, push only that branch and Draft PR/Actions; forbids main push, merge, force push, release, tags; no independent approval impersonation |
| preflight | Current AGENTS.md, strict UTF-8 Quick Start/Index/latest Ledger and P02-03/P02-04 blocks read incrementally; all 26 latest recorded file digests match; plan end intact; git diff --check exit 0 |
| HEAD / branch / remote | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 / feature/coding-agent-evolution / dibbed/computerpilot-mcp; current remote main same SHA; no open PR; branch newly created carrying authorized task changes |
| preserved | panel raw SHA256 6f682baebf08556a4b5bab2d9d9e34f541ccf6ebbee13abda611a3c9b67bd3d4; uv.lock raw SHA256 e9bbee1693a12ebdde82d62ef98722036e5b14db754842bd6c865375594e56aa; master plan stays outside all checkouts |
| CI discovery | Existing ci.yml: 6 Windows/Linux/macOS x Python3.10/3.12 jobs, full pytest/static/startup/health; platform-validation.yml: 3 native OS gates; release-packaging.yml: deterministic inventory/checksum smoke, no publication |
| exact_next_action | Review/selectively commit current project-task checkpoint, create Draft PR and run existing CI; local full tests in disposable detached worktree to isolate subprocess defaults from live .agent_state; do not begin P02-04 before P02-03 acceptance |

### EVIDENCE-1 — P01-04 review remediation opened (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P01-04 / IN_PROGRESS; prior accepted snapshot preserved as historical, only discovered cache lifecycle defects reopened |
| cause | Current source review found invalidate(file) no longer invalidates containing root, and _generations retains inactive keys after LRU eviction despite bounded entries. Source unchanged at detection; authorization covers root-cause auto-recovery |
| pending | Add regression for file-scoped invalidation during build and generation metadata under repeated LRU eviction; fail first, patch shared cache boundary, focused pytest/Ruff/full mypy, then advance digest |
| validation_workspace | LOCAL_PATH_REDACTED; baseline full suite running there on copied 26-file manifest; source remains authoritative in Downloads checkout |
| exact_next_action | Complete narrow P01-04 remediation before committing checkpoint and opening Draft PR; P02-03 POSIX acceptance remains IN_PROGRESS |

### EVIDENCE-1 — TASK-P01-04 cache lifecycle remediation accepted (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P01-04 / VERIFIED after narrow review remediation; P01-01..03 untouched |
| tested_head / branch | e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4 plus reviewed working diff / feature/coding-agent-evolution |
| changed_files | tools/project/context.py (28f1950dbabe2373c8292ffca697a8736ee1a7c32a11b195cdd74c788e4abaf2); tests/test_project_cache.py (bb26092db2f39665af2ebcde5ce4bcb4a7b89ce8d9b3f688378cae49fe75b9d2); CHANGELOG.md (5a9bb8d9f512378bdb407ca56bf7d4ff9e63a751d78a0fee9cf6eaa95bc1db98) |
| RED | pytest test_project_cache.py -k 'cannot_publish or generation_metadata' => 2 failed, 1 passed; file invalidation returned current instead of stale. After adding metadata gauge only, generation regression => 3 keys for max_entries=2, exit 1 |
| GREEN | pytest -o addopts= -q tests/test_project_cache.py tests/test_code_context.py --tb=short -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0104-review-green => 51 passed, 1 skipped (Windows symlink privilege), exit 0 |
| implementation | Invalidation handles both cached roots inside a target subtree and targets inside cached roots; inactive generation keys removed on invalidation/eviction/clear; active generation fences preserved until build cleanup; existing stats exposes bounded generation count |
| static / review | ruff check tools/project/context.py tests/test_project_cache.py => exit 0; mypy core tools scripts tests => 276 files, exit 0; source-reviewed central invalidation and all cache consumers; no disk/state/schema change |
| remaining | Original POSIX acceptance pending for P02-03; original full-suite manifest still running in isolated validation worktree; hosted gates required for newly committed snapshot |
| next_task_id | TASK-P02-03; selectively commit project checkpoint and create Draft PR, no merge/release |

### EVIDENCE-1 — dedicated CI checkpoint published as Draft PR (2026-10-19)
| field | value |
|---|---|
| active_task / state | TASK-P02-03 / IN_PROGRESS, waiting actual Windows/POSIX acceptance; no gate bypass |
| commit / branch | 41985f8f5fc1c86f8ca9497b3dce32f0effad043 / feature/coding-agent-evolution |
| Draft PR | https://github.com/dibbed/computerpilot-mcp/pull/82; attached to current chat; base main unchanged; no merge/release/tag action |
| staged_scope | Exactly 26 latest-ledger project source/test/changelog files, verified by set equality before commit. tools/panel/index.html and uv.lock remain local only. No canonical master plan, state, DB, secret, graph, runtime or unrelated file staged |
| local_test_state | Full pytest baseline is running in isolated detached worktree copied from the prior 26-file manifest; 4 failures observed so far and not yet diagnosed; current P01-04 remediation has separate fresh 51-passed focused evidence |
| CI_state | Existing 3 pull_request workflows requested by Draft PR creation; results still pending, not VERIFIED |
| recovery | gh pr create succeeded with PR82; follow-up gh pr view without positional PR failed CLI usage, corrected with explicit PR number; no duplicate PR created |
| exact_next_action | Inspect full-suite failures and exact-commit CI diagnostics, remediate reproducible root causes within scope, test and push only the same branch. Advance P02-03/P07-01 only with matching successful platform evidence |

### EVIDENCE-1 — TASK-P07-01 platform gate recovery resumed (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P07-01 / IN_PROGRESS; environment-only blocker now recoverable under new Draft PR/Actions authorization |
| tested_commit / Draft PR | 41985f8f5fc1c86f8ca9497b3dce32f0effad043 / https://github.com/dibbed/computerpilot-mcp/pull/82 |
| observed_checks | Release Packaging Smoke run 39314016010 success; Platform Release Validation run 39314016118 Linux job success, Windows/macOS pending; CI run 39314016277 all 6 jobs pending full acceptance |
| local_matrix | Prior 153-passed disposable recovery matrix preserved by source digest; full local regression currently running in detached isolated checkout, 4 failures observed and still pending diagnosis |
| exact_next_action | Finish ten-check matrix on current PR head, diagnose local/CI failures; keep P07-02 independent GO blocked and DAG disabled |

### EVIDENCE-1 — P05-01 full-suite fixture regression diagnosed (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P05-01 / IN_PROGRESS for fixture remediation only; product lazy-store behavior preserved |
| baseline_tested_manifest | Isolated worktree e66c8c9 base plus 26 copied predecessor files (before latest P01-04 amendment) |
| full_local_result | pytest -o addopts= -q --tb=short --maxfail=5 -p no:cacheprovider --basetemp=.pytest-tmp-plan-full => 4 failed, 1640 passed, 25 skipped, exit 1, 362.07s |
| exact_failures | tests/test_job_wait.py::{test_mcp_job_wait_returns_existing_output_immediately,test_mcp_job_wait_returns_heartbeat_when_no_progress,test_mcp_job_wait_preserves_explicit_short_timeout,test_mcp_job_wait_does_not_block_state_update}; AttributeError tools.jobs.registry has no attribute ensure_job_scheduler |
| root_cause / fix_scope | Four fixtures still patch removed eager-registration scheduler import; update fixtures for existing lazy registration API, do not reintroduce eager launch or unused production alias |
| CI | PR82 commit41985f8: all 3 Platform Release Validation jobs and packaging success; 6 full CI jobs still pending/unaccepted |
| next_action | Correct four obsolete fixture patches, focused job_wait/jobs/background regression plus Ruff/full mypy; selectively commit/push branch; fresh full regression in isolated checkout and exact-head Actions |

### EVIDENCE-1 — P05-01 fixture remediation accepted (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P05-01 / VERIFIED; product code unchanged, obsolete test seam removed |
| changed_files | tests/test_job_wait.py (636b185384bae63f9455b8c1f28ee3b54162b86b5ecb9a07c67ab793b7f66f91) |
| RED | Full isolated suite: 4 AttributeError failures for removed tools.jobs.registry.ensure_job_scheduler; 1640 passed, 25 skipped, exit 1 |
| GREEN | pytest -o addopts= -q tests/test_job_wait.py tests/test_jobs_registry.py --tb=short -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0501-fixtures => 19 passed, exit 0 |
| static / review | ruff check tests/test_job_wait.py exit 0; mypy core tools scripts tests => 276 files, exit 0; diff --check exit 0; exactly four obsolete patches deleted; production submit recovery and lazy factory untouched |
| next_action | Commit only test_job_wait.py on dedicated branch and push; exact-head full CI must still pass before P02-03/P07-01 advance. New clean validation checkout will run full pytest without touching operator state |

### EVIDENCE-1 — corrected exact-head CI checkpoint (2026-10-19)
| field | value |
|---|---|
| active_tasks | TASK-P02-03 and TASK-P07-01 IN_PROGRESS; no dependency advancement until actual checks |
| commit / branch / Draft PR | 4235e58f67ef1401304977f5c6ab4ed0f1baaecc / feature/coding-agent-evolution / https://github.com/dibbed/computerpilot-mcp/pull/82 |
| change | Only tests/test_job_wait.py: four obsolete scheduler monkeypatches removed; selective staging checked; dedicated-branch push succeeded |
| isolated_exact_head_tests | Clean detached worktree LOCAL_PATH_REDACTED at exact commit4235e58; full pytest started with temporary default state confined to this checkout and credential/config environment removed |
| original_validation_worktree | LOCAL_PATH_REDACTED retained as failed baseline evidence; full run completed 4 failed/1640 passed/25 skipped, no process still running |
| gate_rule | Earlier 41985f8 Platform/Packaging successes are historical only; current head must pass its own 10 checks. No main update, merge, release, tag or unrelated staging |
| exact_next_action | Await/inspect current-head local full regression and all Actions checks; repair reproducible failures and record digests; advance P02-03 and P07-01 only after their matching gates, then start P02-04 |

### EVIDENCE-1 — P01-04 Linux concurrency regression discovered (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P01-04 / IN_PROGRESS for content-version retry integration |
| exact_CI_evidence | PR82 commit41985f8, CI run39314016277, Linux3.12 job118270675066: 5 failed, 1645 passed, 21 skipped; four known obsolete job_wait patches plus test_concurrent_waiters_retry_after_invalidation -> ToolError Python source changed while being read |
| diagnosis_scope | New file_version can raise project_file_changed_during_parse while computing the initial metadata-cache version; inspect existing bounded version retry boundary and add deterministic regression; do not weaken digest/invalidation guards |
| current_head | 4235e58f67ef1401304977f5c6ab4ed0f1baaecc fixes four fixture failures, not this newly observed Linux race; its CI/local full run remain pending |
| exact_next_action | Reproduce transient version-read failure and bounded persistent failure, patch metadata-cache retry boundary, focused tests/static; selectively commit/push current branch and revalidate exact-head CI |

### EVIDENCE-1 — P01-04 bounded content-version retry accepted (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P01-04 / VERIFIED after Linux-reproduced transient content-version race remediation |
| changed_files | tools/project/index.py (84883e0204da111716d2f79e4033912f1cf258e4e029d644436b374dd2e9f246); tests/test_project_cache.py (1750a83f9ce3397d5d8c7bfe7b0ba5f570d5c3d40cd467130267de79be3140fb) |
| RED | Added deterministic initial-read and post-loader content changes plus continuous-change cap; all 3 regressions failed before patch (ToolError escaped immediately, persistent case had 1 read instead of bounded 3) |
| GREEN | pytest -o addopts= -q tests/test_project_cache.py tests/test_code_context.py --tb=short -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0104-race-final => 54 passed, 1 skipped (Windows symlink privilege), exit 0 |
| fix / review | Existing PythonMetadataCache._version retries only project_file_changed_during_parse up to existing MAX_PARSE_VERSION_RETRIES=3; other failures propagate. Initial and post-load snapshots share this boundary, unchanged direct file_version guard; latest content returned, no stale publication; persistent instability remains bounded/fail-closed |
| static | ruff check . exit 0; mypy core tools scripts tests => 276 files exit 0; compileall -q core tools scripts tests exit 0; git diff --check exit 0 |
| CI_provenance | Original Linux3.12 job118270675066 on41985f8 exposed this race in concurrent_waiters; current4235e58 local full regression is still running without failures observed so far. Acceptance of current branch head still requires new Actions runs |
| next_action | Selectively commit only index.py and test_project_cache.py, push dedicated branch, record exact new SHA and CI; keep P02-03/P07-01 gates IN_PROGRESS |

### EVIDENCE-1 — exact-head concurrency correction published (2026-10-19)
| field | value |
|---|---|
| active_tasks | P02-03/P07-01 IN_PROGRESS pending current-head Actions; P01-04 remediation locally VERIFIED |
| current_commit / branch / PR | 7d401edbde9ffd0b31f7f0038360f5c21411986e / feature/coding-agent-evolution / Draft PR82 |
| selectively_committed | tools/project/index.py and tests/test_project_cache.py only; commit fix(project): retry transient content-version races; push to dedicated branch succeeded |
| current_local_checks | 54 focused context/cache tests passed, one Windows symlink skip; full Ruff/mypy276/compileall/diff check exit 0 after last source edit |
| ongoing_full_run | Isolated clean worktree at prior4235e58 still running (no failures observed through64%); this does NOT claim full validation of new7d401ed |
| exact_next_action | Inspect all 10 current-head Actions checks and any failures; verify tested PR checkout/tree binding; finish local full regression evidence, then P02-03 acceptance and P02-04. Main/merge/release/tag remain forbidden |

### EVIDENCE-1 — full local regression and tested-tree binding (2026-10-19)
| field | value |
|---|---|
| local_exact_commit | 4235e58f67ef1401304977f5c6ab4ed0f1baaecc, clean detached isolated worktree head-validation-d511ca9772ca4744af2c9763b2a3898b |
| full_local_command | python -m pytest -o addopts= -q --tb=short --maxfail=5 -p no:cacheprovider --basetemp=.pytest-tmp-plan-head-full |
| full_local_result | 1646 passed, 25 skipped, exit 0, 364.03s; no code copied or modified during this run; state/DB confined to temporary checkout. Skips are not platform acceptance |
| current_head_delta | 7d401ed adds bounded version retry and 3 deterministic regressions, separately 54 focused passed/1 skipped with full Ruff/mypy/compileall. Full local4235 success does not prove newer head, which awaits its own CI |
| tested_PR_ref | Existing Actions checkout refs/remotes/pull/82/merge; packaging log proves merge commit433fe323f8493d95ac841ca8a62e1a40609728ca, parents e66c8c9 +7d401ed |
| exact_source_binding | GitHub Git API gives identical tree03fca4fefae81c7cefe18ce4da8f10601b3c0577 for branch head7d401ed and tested merge433fe323; run headSha7d401ed. This is explicit merge-ref/tree evidence, not a claim that merge SHA equals branch SHA |
| current_runs | CI39315237101; Platform Release Validation39315237100; Packaging39315237099 success; remaining current-head checks pending/unverified |
| next_action | Inspect current and superseded CI failures, accept only matching current-head successful gates, then P02-03 and P02-04; P07-02 GO still blocked |

### EVIDENCE-1 — P02-03 macOS environment assertion diagnosed (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P02-03 / IN_PROGRESS; production env allowlist not to be expanded |
| exact_evidence | Superseded head4235e58 CI run39314556788, macOS3.12 job118272543643: test_lsp_environment_excludes_secret_and_loader_vars fails because child environment contains __CF_USER_TEXT_ENCODING; separate content-version race already fixed in7d401ed |
| root_cause | Darwin runtime synthesizes an OS-owned environment key after launch; child observed environment is not identical to explicit env passed to native spawn |
| fix_contract | Capture exact spawn env and assert only fixed OS keys/executable PATH, seed parent __CF_USER_TEXT_ENCODING canary and prove it is not forwarded. Permit synthesized key only in observed Darwin child, never in launch allowlist |
| exact_next_action | Amend environment regression only; local LSP suite/Ruff/mypy, selectively commit/push single test file and validate new head on macOS. Do not count superseded failing CI as acceptance |

### EVIDENCE-1 — P02-03 explicit launch-env assertion strengthened (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P02-03 / IN_PROGRESS; local Windows gates green, required macOS/CI acceptance pending |
| changed_files | tests/test_lsp_sessions.py (8811da2d3bd4da2fe4fa5792b1bb5ab17f66cf36e1fa0d44cebcfe9ce731742a) |
| RED_source | Actual macOS3.12 CI failure at superseded4235e58 for OS-synthesized __CF_USER_TEXT_ENCODING; product native spawn env remained fixed allowlist |
| change / review | Exact spawn-env spy enforces fixed OS variables plus executable PATH; parent __CF_USER_TEXT_ENCODING canary must never reach child; only observed Darwin runtime may add this key. No production allowlist/binary policy/cleanup change |
| GREEN | pytest -o addopts= -q tests/test_lsp_client.py tests/test_lsp_sessions.py tests/test_windows_job_backend.py --tb=short -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0203-macos-env => 28 passed, no skips, exit 0; ruff test_lsp_sessions.py exit 0; mypy core tools scripts tests 276 files exit 0 |
| current_CI | Head7d401ed Platform gates Windows/Linux/macOS and packaging all success; full six CI jobs pending, expected Darwin assertion requires this test amendment. No acceptance claimed for superseded head |
| next_action | Commit only this test file and push dedicated branch; require fresh exact-head ten-check matrix, then advance P02-03/P07-01 and proceed to P02-04 without merge |

### EVIDENCE-1 — Darwin assertion checkpoint published (2026-10-19)
| field | value |
|---|---|
| active_tasks | P02-03/P07-01 IN_PROGRESS awaiting matching current-head results |
| current_head / branch / Draft PR | be5925d91b9981d75cdc5e8c56b7e5030dfc4430 / feature/coding-agent-evolution / https://github.com/dibbed/computerpilot-mcp/pull/82 |
| commit_scope | tests/test_lsp_sessions.py only; test(lsp): distinguish Darwin runtime environment additions; push succeeded; production environment policy unchanged |
| local_checks | 28 LSP/Windows ownership tests passed, 0 skipped; Ruff and full mypy276 exit 0; full prior4235 local regression1646 passed/25 skipped; subsequent cache retry54 focused passed/1 skipped |
| unverified | Current-head Windows/Linux/macOS full CI, native gates and packaging must complete. No independent human review/approval claimed; no merge/main/tag/release action |
| exact_next_action | Inspect current-head ten-check matrix and checkout/tree identity. If all required gates pass, accept P02-03 and P07-01, then immediately start P02-04. If a new reproducible failure appears, record it and remediate without weakening guards |

### EVIDENCE-1 — P01-04 initial-version concurrency root cause refined (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P01-04 / IN_PROGRESS; no extra blind retry or weakening digest guard |
| evidence | Superseded7d401ed Linux3.10 job118274869076 still fails concurrent_waiters at index.py:get initial _version -> final file_version retry. Linux3.12 passes. The initial read happens before existing SingleFlight, so waiters hash a file while the owner deliberately waits for the writer |
| root_fix | Move initial cache/version lookup into existing single-flight _load_stable instead of reading independently in every waiter; keep existing bounded content guard and failure limit. Add deterministic owner/waiter regression and phase-based transient-read injection |
| additional_CI_flake | Same superseded Linux3.10 job: test_backups.py::test_manager_rate_limits_repeated_cleanup_scans observed3 calls vs expected2 with 30ms real-time interval; inspect existing fixture to make timing deterministic if needed, no live backup cleanup |
| next_action | Complete deterministic coalesced-read regression, patch cache boundary, focused tests and full static; amend only task-owned files, then current-head Actions |

### EVIDENCE-1 — P05-01 CI harness isolation remediation opened (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P05-01 / IN_PROGRESS for required CI harness isolation, no backup product change |
| exact_failure | Superseded7d401ed Linux3.10 job118274869076: test_manager_rate_limits_repeated_cleanup_scans counted3 cleanup calls instead of2 |
| source_diagnosis | Fixture patches global backups.cleanup_backups and counts all retention managers; unrelated background retention callbacks can enter the same spy. Existing production per-manager locking/rate limit remains bounded |
| next_action | Make unrelated temporary retention owner deterministic in existing test (RED), then count only the tested manager directory, preserve real cleanup/delay assertions; local isolated backup suite/Ruff/mypy and selective test-only commit |

### EVIDENCE-1 — P01-04 version reads coalesced at existing owner boundary (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P01-04 / VERIFIED after root-cause coordination fix; CI acceptance of whole head still pending |
| changed_files | tools/project/index.py (e9891f699f56785b416532f9a8acc492235faf03a84f96b4b0734eab395ac8e7); tests/test_project_cache.py (86e4756fba3850bf8aba01c42dc4c4ee8fae8093f61ff18f90464eda29a9c885) |
| RED | Deterministic held owner plus concurrent waiter: waiter performs version IO before joining SingleFlight and raises project_file_changed_during_parse even with 3 retries; 1 failed/2 phase-based transient tests passed |
| fix | Remove duplicate get() pre-flight hash/cache lookup; existing SingleFlight now owns _load_stable version lookup as well as parse/retry/publication. Warm cache still rechecks content inside owner; unrelated path keys remain parallel; no new abstraction or longer retry |
| GREEN | pytest -o addopts= -q tests/test_project_cache.py tests/test_code_context.py --tb=short -p no:cacheprovider --basetemp=.pytest-tmp-plan-p0104-coalesce-green => 55 passed, 1 skipped (Windows symlink privilege), exit 0 |
| static / review | Ruff entire repo exit 0; mypy core tools scripts tests276 exit 0; diff --check exit 0; existing concurrency/warm cache/invalidation/bounded retry tests pass; no state or schema changes |
| next_action | Commit only index.py and test_project_cache.py; publish with independent backup-fixture correction on same dedicated branch; exact-head CI remains required |

### EVIDENCE-1 — P05-01 retention test isolation accepted (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P05-01 / VERIFIED; CI fixture only, backup implementation untouched |
| changed_files | tests/test_backups.py (6b777574fd49207a2b5f49c8809d52744fc2ff602976dba7eda62e219bcde0ef) |
| RED | Add unrelated temporary retention owner to existing rate-limit scenario; unscoped global spy deterministically counted3 instead of2, exit 1. Initial copy attempt used same source/destination and tested old fixture, explicitly NOT RED evidence; corrected absolute source copy produced real failure |
| GREEN | In disposable validation checkout, pytest -o addopts= -q tests/test_backups.py --tb=short -p no:cacheprovider --basetemp=.pytest-tmp-plan-backup-isolation-green => 32 passed, 2 skipped (platform symlink privileges), exit 0 |
| fix / review | Spy counts only tested manager directory; unrelated temporary owner still really runs and its result asserted; own-manager rate delay assertions retained; no timing relaxation, no production cleanup/deletion or live state operation |
| static | Ruff repo exit 0; mypy core tools scripts tests276 exit 0; diff --check exit 0 |
| next_action | Commit only test_backups.py then push only feature/coding-agent-evolution; fresh exact-head ten-check matrix required before advancing P02-03/P07-01 |

### EVIDENCE-1 — coordinated cache and isolated fixture CI checkpoint (2026-10-19)
| field | value |
|---|---|
| active_tasks | P02-03/P07-01 IN_PROGRESS waiting current-head acceptance; P01-04/P05-01 root fixes locally VERIFIED |
| current_commit / branch / Draft PR | 6b7e85f87c209f29cb0e804e4e1074b8b42a3e97 / feature/coding-agent-evolution / https://github.com/dibbed/computerpilot-mcp/pull/82 |
| commits | 28d1441 coalesces metadata version reads with existing refresh owner; 6b7e85f isolates backup rate-limit observations; both selectively staged and pushed only dedicated branch |
| current_Actions | CI39316720662 pending; Platform39316720889 pending; Packaging39316720814 success. Oldbe5925d Linux3.10/Linux3.12/macOS3.12 full CI success observed, but old successes are not counted for new head |
| local_validation | New clean detached worktree LOCAL_PATH_REDACTED at exact6b7e85f; full pytest started with fresh local state and config/credential env removed. Prior4235 full1646 passed/25 skipped is historical |
| exact_next_action | Complete exact6b7e85f local/full hosted tests and tested merge-tree binding. If all required checks pass accept P02-03 and P07-01, then start P02-04; otherwise diagnose actual errors with bounded root-cause recovery, no guard bypass |

### EVIDENCE-1 — current-head gate progress and implementation accounting (2026-10-19)
| field | value |
|---|---|
| head / PR | 6b7e85f87c209f29cb0e804e4e1074b8b42a3e97 / Draft PR82 |
| checked_gates | Current head: Packaging and all three native Platform gates success; macOS Python3.10 full CI success; remaining five full CI jobs pending. Current local full suite has no failures observed through68% |
| progress_accounting | Previously implemented P01-04/P02-03/P05-01 retain implementation weight while IN_PROGRESS only for revalidation; accepted weight still requires VERIFIED. In particular P02-03 code exists and is locally green, so implementation remains9/29 while verified8/29; no pending gate counted as accepted |
| exact_next_action | Finish current-head tests and checkout binding, accept P02-03/P07-01 only after matching required evidence, then begin P02-04. No new source edit while current checkpoint runs |

### EVIDENCE-1 — P02-03 hosted acceptance (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P02-03 / VERIFIED |
| head / tested checkout | head6b7e85f87c209f29cb0e804e4e1074b8b42a3e97; PR merge0694574e3d0115ab5fae09dd2f33515cc8d50472; both tree808e9753e82228dcbd6d0849cbf7148813d3467b; parents SOURCE_BASE + exact head. Hosted PR checkout tests merge ref with identical source tree, not literal head |
| required gates | all10 SUCCESS: six OS/Python CI https://github.com/dibbed/computerpilot-mcp/actions/runs/39316720662; three native OS https://github.com/dibbed/computerpilot-mcp/actions/runs/39316720889; five-target packaging https://github.com/dibbed/computerpilot-mcp/actions/runs/39316720814 |
| review | Executor review confirms require_native fail-closed before payload, minimal explicit env, fixed errors and stderr discard, subtree ownership/cleanup and direct client regressions. This is authorized executor/reviewer review, not independent approval |
| local evidence | prior focused28 passed and static green; latest local full process output lost across tool/context session, so its final result is UNVERIFIED. Repeating in same disposable checkout with durable log; hosted Windows full suites both success |
| preserved | panel/uv.lock unchanged; no live-state writes, main push, merge, tags or release |
| next_action | TASK-P02-04 RED tests and bounded transport implementation; P07 independent acceptance review remains separate |

### EVIDENCE-1 — P07-01 platform evidence complete, independent review pending (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P07-01 / BLOCKED only by independent acceptance review |
| evidence | schema11 unchanged; previous disposable recovery matrix153 passed; same current source tree all10 hosted gates SUCCESS on three native OS + packaging, see P02-03 acceptance |
| remaining | P07-01 explicitly requires independent review; current executor review does not establish independence. No independent reviewer evidence available. P07-02 remains BLOCKED and DAG activation stays closed; this does not block P02-04 |
| exact_next_action | Continue P02-04. Request independent review only when no remaining authorized independent progress; do not invent approval |

### EVIDENCE-1 — P02-04 transport implementation checkpoint (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P02-04 / IMPLEMENTED_UNVERIFIED pending independent automated review and fresh CI |
| head / branch / PR | 4f1f663619a6d02fde7a2f27fa7a434779d5f50e / feature/coding-agent-evolution / Draft PR82 |
| changed_files | tools/language/transport.py (a740664f6c617aec11dca8938e9ec59b1b4c49f8db68bcb51d4c9b3e7ac0f66e); tools/language/lsp.py (c02d535cc19240aeb8cb93c37ae8c98ff07ebdda14b2eb830fe98610fbcff8c4); tests/test_lsp_sessions.py (64598aeeefe7eed0bcbe82fe19ca9cad15956c9f89cbdae51258009c2b1cd5af); CHANGELOG.md (1403db2eaeb737b366c41148a79d3579c79fae9281904bed4f336152c6c35c6c) |
| RED | Initial transport acceptance13failed(AttributeError missing LspTransport); added partial-progress regression failed KeyError complete before fix, exit1 |
| GREEN | scoped pytest test_lsp_client,test_lsp_sessions,test_language_registry,test_language_hardening,test_lsp_providers,test_windows_job_backend:83passed1skipped(Windows symlink); exit0. Initial broad command named nonexistent test_lsp_requests.py and exited4, corrected filename set then passed |
| static | Ruff touched Python exit0; full mypy core tools scripts tests276 exit0; git diff --check exit0 |
| behavior / review | shared parser replaces duplicate one-shot framing; bounds headers/body/depth/queue/request; same-owner serialized monotonic IDs; old drop/future failure; callback mutation denial; private progress aggregation marked complete=false and legacy client fails closed; only metadata counters retained for other notifications |
| independent_review | Read-only transport_review dispatched per requesting-code-review skill. Recovery_review separately dispatched for P07-01; these are independent automated checks, not human/operator approval |
| prior_checkpoint_full | Exact6b7e85f disposable checkout durable full-validation.log:1650passed25skipped361.51s exit0; earlier lost output remains unverified, repeat is authoritative |
| preserved | Protected panel6f682bae... and uv.locke9bbee16... match originals; only4 reviewed task files committed/pushed, no master plan in repository, no main/release/tag mutations |
| exact_next_action | Resolve independent transport review findings with RED/GREEN; accept04 after required checks, then05. Current4f CI still pending; no old CI counted for new head |

### EVIDENCE-1 — P02-04 independent-review root-cause recovery (2026-10-19)
| field | value |
|---|---|
| task / state | P02-04 IMPLEMENTED_UNVERIFIED; current working changes atop4f1f663 |
| review_findings | Independent automated transport_review confirmed high configured limits rejected after spawn without finally, and reader thread.start failures leaked ownership/provider exceptions |
| RED / GREEN | Four new bounded fake-process tests failed before patch;4passed after patch. Full affected regression running; no results invented |
| fix | Validate shared hard bounds in LspClient constructor before launch; LspTransport catches thread-start RuntimeError, closes native ownership/readers, emits fixed safe error; joins only actually-started threads |
| changed_files | tools/language/transport.py (2162c3de3b9f348c47d2d71f58c00edfae7ec0de750d6a560e89662fb3116fab); tools/language/lsp.py (e7a885e119ec2655d09cc83a6851888104289560dfc1d5f5fcf15b7ec07f0673); tests/test_lsp_sessions.py (477939fdd4299fcf1e2164333e60d9a68c5ad32d8956d33bd186eb84cbbd4f91) |
| exact_next_action | Finish regression/Ruff/full mypy, independent reviewer recheck, selectively commit/push corrections and re-evaluate current-head gates before04 acceptance |

### EVIDENCE-1 — P07-01 independent acceptance failure and safe recovery (2026-10-19)
| field | value |
|---|---|
| task / state | TASK-P07-01 / IN_PROGRESS remediation; previous BLOCKED review now yielded actionable defect |
| independent_review | recovery_review:153passed0skipped46.63s on4f1f663, but FAIL because future schema guard ran after WAL/base DDL; no independent approval fabricated |
| hash_guard | workflow_store.py raw d567bf6d45981833a45cedaa9cd0f3c477c340761757fdbafcfd4ae32ae7a659 equals prior recorded source review before patch |
| RED | New schema12 temporary DB, sentinel rows and SHA256/journal/schema/file assertions, recover false/true:2failed on changed database bytes |
| root_fix | Check user_version before WAL setup; hold existing migration file lock across guard, base DDL and migrations; no nested duplicate lock; same schema11, no new migration/data rewrite |
| GREEN / static | Entire migration file15passed exit0; Ruff changed Python exit0; full mypy276 exit0; broader isolated recovery and independent recheck pending |
| changed_files | core/workflow_store.py (7ad7d02b274efcace48f7819405050c74c6e0b295a7f0efc44e1c18b51b404e4); tests/test_workflow_migrations.py (3ee12d54d5baf69c2c76aacea6b469d2d20a51efa16caa326bbf6049d469af60); CHANGELOG.md (2ad4652c7fefc8cbda792a5b5536a87f0c19032b79ca3d3eeaa9d5b56f84dbba) |
| exact_next_action | Run broader disposable recovery matrix; independent reviewer recheck; commit only remediation files, push dedicated branch and require all10 newhead hosted gates. P07-02 remains blocked |

### EVIDENCE-1 — transport and schema guard committed verification checkpoint (2026-10-19)
| field | value |
|---|---|
| head / branch / PR | bd61d07362d8341817e6abf5bd38bd31c38e8636 / feature/coding-agent-evolution / https://github.com/dibbed/computerpilot-mcp/pull/82 Draft |
| selective_commits | dbc814a initialization cleanup; bd61d07 future schema guard and regressions; no unrelated file staged, no main mutation |
| transport_review | Independent automated transport_review rechecked fixes:24passed11deselected, no remaining consequential scoped finding. Executor broader87passed1Windows-symlinkskip, Ruff/fullmypy276 green. Hosted current-head acceptance still pending |
| recovery_review | New guard/concurrency source review clean, broader155-case run pending; schema remains11 |
| isolated_full | New detached worktree LOCAL_PATH_REDACTED at exactbd61d07; fresh full suite with env isolation and durable full-validation.log running |
| next | Finish fresh hosted matrix + full suite + independent recovery result; then accept04/P07-01 on matching evidence and begin05. P07-02 operator GO stays blocked |

### EVIDENCE-1 — M02a transport accepted (2026-10-20 Asia/Tehran)
| field | value |
|---|---|
| task / state | TASK-P02-04 VERIFIED; tasks02-01..04 complete |
| head / tested checkout | bd61d07362d8341817e6abf5bd38bd31c38e8636; PR merge3fd0ff26ef0291807e63db4a84972855fc8758a7; identical treea32e6fc7d1d6e171e7a668bc7a6949843d9d5ac8; parents SOURCE_BASE+head. Hosted tests merge ref, not literal head |
| all10 hosted | CI https://github.com/dibbed/computerpilot-mcp/actions/runs/39319638087 all6success; Platform https://github.com/dibbed/computerpilot-mcp/actions/runs/39319638059 all3success; Packaging https://github.com/dibbed/computerpilot-mcp/actions/runs/39319638096 success |
| full suites | Windows3.10:1694passed6skipped360.14s plus dedicated ownership1passed; Linux3.12:1679passed21skipped267.41s; macOS3.12:1679passed21skipped355.44s. No platform job skipped |
| local / independent | Executor87passed1Windows-symlinkskip; Ruff/fullmypy276 green. Independent automated transport review24passed, no remaining scoped finding after initialization fixes; no human approval implied |
| interrupted local full | Disposable bd61d07 full log stops25%, tool process handle lost during environment reset; final result UNVERIFIED and not counted. Current hosted Windows complete suites provide full regression evidence |
| next | P02-05 inert manager constructor, ticket/owner reuse RED tests; persistent flag stays disabled |

### EVIDENCE-1 — P07-01 accepted after future-schema remediation (2026-10-20)
| field | value |
|---|---|
| task / state | TASK-P07-01 VERIFIED; TASK-P07-02 remains BLOCKED and DAG disabled |
| source_hashes | core/workflow_store.py (7ad7d02b274efcace48f7819405050c74c6e0b295a7f0efc44e1c18b51b404e4); tests/test_workflow_migrations.py (3ee12d54d5baf69c2c76aacea6b469d2d20a51efa16caa326bbf6049d469af60) |
| independent automated review | recovery_review:155passed0skipped50.52s exit0 with isolated SETTINGS/temp storage; same file hashes through final bd61d07; narrow guard/concurrency recheck clean. No human/operator GO implied |
| tested contract | Future-schema2RED=>GREEN; all15migration tests; v9/v10/v11/rollback, concurrent open/WAL retry, claims/worktree identity/no-replay/recovery/CAS/cancel/heartbeat/quota matrix passed |
| hosted | Current bd61d07 all10 success on identical head/merge tree per M02a entry |
| preserved | Schema remains11; only early guard and existing lock scope changed; no live DB touched, no activation |
| next | Continue P02-05; independent operator GO for DAG intentionally excluded |

### EVIDENCE-1 — P02-05 owner manager implementation checkpoint (2026-10-20)
| field | value |
|---|---|
| task / state | TASK-P02-05 IMPLEMENTED_UNVERIFIED pending independent lifecycle review + same-head hosted three OS |
| head / branch / PR | c7dcbbe8e1e678a6335db07d69c788de71fba410 / feature/coding-agent-evolution / Draft PR82 |
| RED | Initial six real-fixture manager tests failed missing module; adversarial cancel/crash tests subsequently2failed on stale in_flight counter,11passed; clear current only after bounded cleanup corrected them |
| GREEN | Manager13passed then added full admission/inert-constructor coverage; latest full language/provider/transport/ownership regression102passed1Windows-symlinkskip26.67s exit0; Ruff touched exit0, full mypy277 exit0, staged diff --check exit0 |
| behavior | Real pinned fixture1/10/100warm requests one PID/spawn,8concurrent first one owner,2roots2PIDs;4owners32queued cap; queued cancel/expiry zero dispatch; inflight cancellation/crash retires without replay; blocked owner retains quarantine capacity; unknown/RSS-denied sampling prevents business dispatch; idle shutdown reaps native owner |
| recovery notes | Constructor remains inert; only owner starts process; per-tree resource sampling every0.5s and afterresponse; request Future belongs to ticket and completion races with cancellation use standard InvalidStateError guard; no documents sync/lifecycle activation yet |
| hash_guard | All recorded predecessor file digests matched before checkpoint except active task-owned tests; protected panel/uv.lock raw digests unchanged; no state changes |
| changed_files | tools/language/sessions.py (686cabb4738c56fe62bac2e455c771b4d0b8895b7d110417835e8eab0280ef94); tests/test_lsp_sessions.py (7f5bcc874ee5e600662ece34630a72727111b921b21e87216aca7e2bd633bc37); CHANGELOG.md (9d689dcb3fd4966fe57bbbf2ad59895fc26662ba9f26e00e627d04fbf3437274) |
| independent review | transport_review read-only lifecycle review dispatched; current source now committed c7dcbbe; no acceptance claimed before review and hosted gates |
| exact_next_action | Resolve lifecycle review findings RED/GREEN, require same-head native Windows/Linux/macOS + existing hosted matrix; accept05 only after matching evidence then begin06 |

### EVIDENCE-1 — P02-05 independent lifecycle findings and recovery (2026-10-20)
| field | value |
|---|---|
| task / state | TASK-P02-05 IMPLEMENTED_UNVERIFIED; review FAIL on c7dcbbe until fixes accepted |
| findings | Admission used request-entry timestamp so slow validation consumed backoff before launch; CPU difference of live-tree sums lost exited-child consumption and reused PID identity |
| RED | Three deterministic no-process regressions: slow-validation backoff, exited-child CPU, reused-PID CPU; all3failed before fix |
| GREEN | All3passed after fix0.80s exit0; wider regression running, final static/review/hosted pending |
| fix | Admission uses current monotonic under lock; cumulative observed CPU deltas keyed PID/create-time with current identity map capped512 processes, dead identities removed without reducing cumulative total; capture baseline before send and recheck deadline/cancel after resource probe |
| changed_files | tools/language/sessions.py (f14557b66a1aef4333cb6f47892bfcc4035cf1da49916f9d7677c56e09dcbe2a); tests/test_lsp_sessions.py (76da01b7287d8a8b8bbf6c0ed1de63bfe95d6800f5dd26e2a79196a2b5b007bd) |
| exact_next_action | Complete affected checks, independent reviewer recheck, commit/push correction and bind fresh hosted three-OS matrix before05 acceptance |

### EVIDENCE-1 — P02-05 lifecycle remediation independently accepted (2026-10-20)
| field | value |
|---|---|
| task / state | TASK-P02-05 IMPLEMENTED_UNVERIFIED pending current-head hosted gates |
| head / PR | 7f0d3c32606899fa94d2ac0fb2701b276ff533ed / Draft PR82, only dedicated branch pushed |
| changed_files | tools/language/sessions.py (f14557b66a1aef4333cb6f47892bfcc4035cf1da49916f9d7677c56e09dcbe2a); tests/test_lsp_sessions.py (76da01b7287d8a8b8bbf6c0ed1de63bfe95d6800f5dd26e2a79196a2b5b007bd) |
| RED / GREEN | Three lifecycle regressions3failed before fix =>3passed after; broader affected105passed1Windows-symlinkskip17.54s. After final deadline/probe ordering edit reran all18manager cases18passed9.03s; unaffected other cases reused from same broader run |
| static | Last-edit Ruff touched exit0, mypy core tools scripts tests277 exit0, git diff --check and staged check exit0 |
| independent automated review | transport_review recheck PASS:18passed35deselected8.66s; before/after exact reviewed file hashes unchanged; no remaining scoped lifecycle finding. This is automated independent review, not human/operator approval |
| next | Require matching7f0d3c3 hosted native three-OS+full CI; count no superseded c7dcbbe results for changed code. Then05 VERIFIED and06 RED DocumentState/snapshot tests |

### EVIDENCE-1 — P02-05 Linux CI gate diagnosis (2026-10-20)
| field | value |
|---|---|
| task / state | TASK-P02-05 IMPLEMENTED_UNVERIFIED;7f0d3c3 hosted8success2failure, not accepted |
| failures | Linux3.10/3.12 test_manager_cancel_inflight_retires_without_replay waits active0 after cancel; each1failed1696passed21skipped. Native platform3 + packaging + Windows/macOS full CI passed |
| root cause | Test waited current!=None counter, which includes preparing before business send. Resource probe can expose that phase longer on Linux. Cancel then correctly skips dispatch and preserves READY session, contradicting test's incorrect expectation of retirement. Separate global psutil.Process mock contaminated native POSIX cleanup and emitted thread warning |
| correction | Fixture logs dispatch when server receives method before slow response; inflight cancel waits that true dispatch barrier. Added deterministic paused-preparation regression proving predispatch cancellation preserves warm session and never sends cancelled method. psutil fakes now module-local namespaces, preserving native backend's real measurement/cleanup library |
| changed_files | tests/test_lsp_sessions.py (09b2bc9662e2cd2064506a91cba0a0c372c755f3497697c0c1f43926506a6fcb) |
| evidence / next | Linux failures are RED evidence; local manager rerun/static checks running. No timeout relaxation or product lifecycle change; independent recheck + new exact-head CI required |

### EVIDENCE-1 — P02-05 Linux fixture correction committed (2026-10-20)
| field | value |
|---|---|
| head / PR | 200c544859c7e88f6dbf936c7f9725927fe762be / Draft PR82 |
| changes | Selectively committed only test_lsp_sessions.py; sessions.py unchanged from reviewed f14557b66a1aef4333cb6f47892bfcc4035cf1da49916f9d7677c56e09dcbe2a |
| current local | All19manager cases passed9.80s exit0, including true-dispatch cancellation and deterministic preparation cancellation; Ruff and full mypy277 exit0, staged diff check exit0 |
| hosted | New exact200c544 matrix required; prior7f Linux pair failed and remains failed evidence, never counted as accepted. Independent fixture recheck pending |
| exact_next_action | Finish independent fixture/phase and psutil isolation review; inspect200c544 hosted results and head/merge tree binding; accept05 only after current required native/platform checks, then06 |

### EVIDENCE-1 — independent fixture recheck and precise recovery command (2026-10-20)
| field | value |
|---|---|
| P02-05 independent automated recheck | PASS,19passed35deselected10.10s, stable manager sourcef14557... and tests09b2bc9662e2cd2064506a91cba0a0c372c755f3497697c0c1f43926506a6fcb; real dispatch barrier and module-local resource mocks verified; no timeout relaxation |
| P07-01 exact executed command clarification | Root .venv python via stdin script: create tempfile p0701-independent-recheck; replace config.SETTINGS state_dir=root/state,memory_dir=root/memory,workflow_dag_enabled=False before test imports; pytest.main -o addopts= -q --tb=short -p no:cacheprovider --basetemp=root/fixtures |
| P07-01 exact files | tests/test_workflow_migrations.py tests/test_workflow_dag.py tests/test_parallel_workflow_scheduler.py tests/test_parallel_workflow_resources.py tests/test_parallel_workflow_recovery.py tests/test_workflow_cancellation.py tests/test_workflow_lease_heartbeat.py tests/test_workflow_crash_recovery.py tests/test_parallel_verification.py |
| P07-01 recorded result |155passed0skipped50.52s exit0 on bd61d07; evidence retrieval only, no rerun or live-state inspection |
| next | Current200c544 hosted gate completion remains required for P02-05; do not conflate independent automated evidence with human/operator GO |

### EVIDENCE-1 — current-head Windows pressure gate failure (2026-10-20)
| field | value |
|---|---|
| head / state | 200c544859c7e88f6dbf936c7f9725927fe762be; P02-05 not accepted; P07-01 reopened only to reconcile current pressure failure |
| current hosted |9success1failure. Both Linux full suites now pass after fixture correction; Windows3.12/macOS full and all3native+packaging pass. Windows3.10 job118647479020 only test_mixed_resource_claim_pressure_has_finite_progress fails:1failed1712passed6skip471.90s |
| observed contract | Existing test200requests8threads,10sec admission progress deadline, returns false rather than code exception; no request IDs/reasons printed, so root cause not yet proven. No claim that raising timeout or ignoring result is acceptable |
| source | Workflow store unchanged since accepted bd61d07 guard (raw7ad7...); no workflow product diff in LSP commits; current suite/load could expose contention, hypothesis only |
| action | Run same pressure test once with fresh disposable Settings/DB; independent bounded reviewer traces new initialization lock versus admission/release for concrete inversion/starvation. If isolated pass and no deterministic defect, one same-head failed-job retry is allowed; otherwise root-cause regression/fix |
| next | Complete this diagnosis; retain genuine failed gate evidence, no05 completion while current required check unresolved |

### EVIDENCE-1 — Windows pressure isolation and one bounded retry (2026-10-20)
| field | value |
|---|---|
| local isolated | Root .venv python stdin: replace config.SETTINGS state/memory with tempfile p0701-pressure-isolation and workflow_dag_enabled=False, then pytest -o addopts= -q --tb=short -p no:cacheprovider --basetemp=temp/fixtures tests/test_parallel_workflow_resources.py::test_mixed_resource_claim_pressure_has_finite_progress =>1passed17.09s exit0 |
| independent review | No introduced lock inversion: pressure10sec deadline begins after construction, reserve/release never take migration lock or construct stores, waiting constructors have no pending claims. Possible SQLite writer scheduling contention is unproven hypothesis, not defect claim |
| same-head retry | gh run rerun39428622683 --job118647479020 --repo dibbed/computerpilot-mcp executed once, exit0; same head200c544; no source changes or timeout changes. Prior9successful gates remain same source, failed original result preserved |
| exact_next_action | Inspect rerun Windows3.10 full outcome. If passes and current-head identity matches, reconcileP07-01 andP02-05 then start06. If repeated pressure failure, instrument request IDs/reasons/transaction waits before any product fix; no repeated blind retry |

### EVIDENCE-1 — P07-01 pressure revalidation completed (2026-10-20)
| field | value |
|---|---|
| task / state | TASK-P07-01 VERIFIED after one bounded same-head retry; original failed check preserved |
| exact head |200c544859c7e88f6dbf936c7f9725927fe762be; workflow source7ad7... unchanged; all10 current checks success |
| failed-job recovery | CI39428622683 attempt2 Windows3.10 job118653762963 SUCCESS; same commit and no source/timeout/assertion changes. Original attempt1 one pressure failure remains historical evidence, not reported as fixed product defect |
| diagnosis limit | Local isolated pressure1passed17.09s; independent source review found no introduced migration/admission lock cycle. Original hosted pressure cause remains UNVERIFIED; one successful retry proves this check ran and passed, not a root-cause fix |
| next | Continue P02-06 after P02-05 acceptance; P07-02 operator GO stays BLOCKED, DAG disabled |

### EVIDENCE-1 — P02-05 session manager accepted (2026-10-20)
| field | value |
|---|---|
| task / state | TASK-P02-05 VERIFIED |
| head / tested checkout |200c544859c7e88f6dbf936c7f9725927fe762be; merge dbf182096adb5ef754b413c9c6b6800208b43425; both tree57152b0d6e7224177909411f00d146fff1ce02c1, base SOURCE_BASE. Hosted executes merge checkout with identical source tree |
| all10 gates | CI https://github.com/dibbed/computerpilot-mcp/actions/runs/39428622683 completed success attempt2 (only failed Windows3.10 rerun, other5successes reused unchanged); native https://github.com/dibbed/computerpilot-mcp/actions/runs/39428622621 all3success; packaging https://github.com/dibbed/computerpilot-mcp/actions/runs/39428622755 success |
| source / tests | tools/language/sessions.py (f14557b66a1aef4333cb6f47892bfcc4035cf1da49916f9d7677c56e09dcbe2a); tests/test_lsp_sessions.py (09b2bc9662e2cd2064506a91cba0a0c372c755f3497697c0c1f43926506a6fcb) |
| local acceptance | Broader105passed1Windows-symlinkskip before final test-only correction; all19current manager cases19passed9.80s, independent19passed10.10s; current Ruff/fullmypy277/staged diff checks exit0 |
| independent automated review | Lifecycle and fixture recheck PASS after explicit RED/GREEN fixes for admission backoff and cumulative per-identity CPU. No remaining scoped finding; no human/operator approval or activation implied |
| static recovery notes | Earlier reversible test-only E501 and object-index typing errors were corrected with formatting and runtime fixture-result assertions; final checks exit0. No unresolved static failure |
| invariants |4sessions,8queued/session32total, oneowner and onepending, no replay, resource unknown denies, abandoned cleanup retains capacity, constructor/snapshot inert; real warm1/10/100onePID. Documents/lifecycle integration not implemented by this unit |
| exact_next_action | Independently select next NOT_STARTED task from verified dependencies: P02-06. Hash guard then RED DocumentState/sync/stale tests; persistent flag remains disabled |

### EVIDENCE-1 — P02-06 document synchronization first RED/GREEN (2026-10-20)
| field | value |
|---|---|
| task / state | TASK-P02-06 IN_PROGRESS, not accepted |
| RED | Six tests failed before implementation: sync kind0/1/2, change during hover, document LRU, openClose denial; no result fabricated |
| GREEN | Same six passed5.51s exit0 after owner-only DocumentState, bounded strict disk snapshot, negotiated notifications/versioning, SHA stale check, LRU and didClose shutdown |
| required remaining | Ruff E501 in test fixture helper remains; full mypy not yet run after this patch; deletion/recreate, byte cap, config/key retirement, private rename snapshot and platform/regression/independent review pending |
| scope | Only sessions.py/test_lsp_sessions.py edited. Canonical fixture untouched; test-only copy adds actual notification handling for versioned sync and bounded synthetic logs |
| changed_files | tools/language/sessions.py (691b1824857d54b493ea9dc6adcab54a0ad0ff88bad450607ef2fb7c13ec6526); tests/test_lsp_sessions.py (5ec3306e9b08d42cf268d724bc4705bdddee42e31d813a3ff420817c86fae9b9) |
| exact_next_action | Fix static formatting, add remaining RED document/config/rename cases, finish exact06 contract, then scoped regression/Ruff/fullmypy/review/evidence. Do not mark06 VERIFIED yet |

### EVIDENCE-1 — 2026-10-20 TASK-P02-06 recovery checkpoint (not acceptance)
| field | evidence |
|---|---|
| task / state | TASK-P02-06 IN_PROGRESS on HEAD200c544; only sessions.py/test_lsp_sessions.py product edits |
| additional RED/GREEN | Deleted/recreated document, config/marker/pin retirement, byte cap, private immutable rename snapshots: four RED and six GREEN recorded during implementation; no acceptance yet |
| latest failure / root cause | New large-byte pytest parameter generated an oversized Windows tmp path (two setup errors), and snapshot replacement test triggered its stat seam during URI resolution (one failure). These were test instrumentation defects; no product guard weakened |
| recovery | Short parameter labels with bytes built inside test; replacement test uses a prevalidated URI resolver seam. Exact focused four snapshot/invalid-document tests passed2.10s exit0 |
| gates pending | Full scoped regression, Ruff, full mypy, independent document/lifecycle review and current-commit platform validation |
| exact_next_action | Finish bounded retirement review and regressions; scoped sessions/client/workspace-edit tests then static and independent review; keep06 IN_PROGRESS |

### EVIDENCE-1 — 2026-10-20 TASK-P02-06 independent review remediation
| field | evidence |
|---|---|
| state | IN_PROGRESS, not acceptance; required final gates pending |
| reproduced / fixed | Concurrent retirement1RED/1GREEN; queued position against wrong disk snapshot1RED/1GREEN; rename eviction of active snapshot1RED/1GREEN; workspace query stale open documents2RED/2GREEN plus LRU regression |
| transport boundary | Independent review proved prep bytes charged to preceding request. Verified transport SHA2162c3de3b9f348c47d2d71f58c00edfae7ec0de750d6a560e89662fb3116fab before narrow additive begin_request boundary; fit/overflow2RED/2GREEN. Preparation and business exchange now share one budget, callbacks cannot reset it |
| resource regressions | Preparation CPU baseline and lifetime-wire accounting2RED; move CPU baseline before sync and check lifetime cap between every ticket, including preparation errors. GREEN check running in same checkpoint cycle |
| scope justification | Sync integration required narrow transport.py adjustment in addition to sessions/test; no public tool or activation changes; no replay or live state touched |
| last broad checks | Before latest transport/resource fixes:95 passed31.75s, Ruff exit0, full mypy277 exit0. These do not verify later edits |
| exact_next_action | Re-run scoped suite/static, complete independent review, selective task commit/push and exact-head native-platform validation; keep06 unaccepted until gates pass |

### EVIDENCE-1 — 2026-10-20 TASK-P02-06 final local recovery gates
| field | evidence |
|---|---|
| state | IN_PROGRESS; no acceptance until independent review/platform checks |
| latest scoped result | 101 passed35.30s exit0, exact sessions/client/workspace_edit_regressions; after final document-error CPU fix,3 affected resource tests passed1.33s exit0 |
| final static | Ruff sessions.py/transport.py/test_lsp_sessions.py exit0; mypy core tools scripts tests:277 files exit0; git diff --check exit0 |
| final resource correction | CPU-error path1RED (capacity instead of CPU denial), then sample with original baseline before document-error continuation;3CPU/wire GREEN. Lifetime wire checked between all tickets |
| process cleanup | Win32_Process query of managed-fixture.py/persistent_lsp_server.py Python processes returned0 |
| review | Automated independent reviewer found real snapshot/LRU/queue/budget gaps; all reproduced and fixed with regressions. Final independent full scoped suite running; not human approval |
| exact_next_action | Await final independent scoped result, selectively commit only four task files, push feature/coding-agent-evolution and validate PR82 exact head on native Windows/Linux/macOS; no main/merge/release |
| changed_files | tools/language/sessions.py (5fb6a10ea00b793010bc111ae7f391c62a3dd8be374af5e8fa806196ecf1425c); tools/language/transport.py (b6b4233a22d1ae3ffe1cfe842e68b5c9b370da45fd17a19ef665d6f4329cc0d0); tests/test_lsp_sessions.py (99ff0c0ec186964e9a2a3f99188931469fd08777972294f3a0280f7781b2650d); CHANGELOG.md (2ec85f491daf0c71a36f91ddbad6b419962224a44c4debc9016b8cdadd837108) |


### EVIDENCE-1 — 2026-10-20 TASK-P02-06 committed local acceptance / hosted pending
| field | evidence |
|---|---|
| state | IMPLEMENTED_UNVERIFIED until native hosted gates for e9100c298d6cfb7b315b471483927b55edbfcaa1; tested predecessor200c544 |
| changes | owner-only disk DocumentState, negotiated versions/saves,2MiB strict bounded reads/2retries,16doc/16MiB LRU, protected active snapshots, queued-position validation, cached-buffer sync/stale rejection, private rename digests, identity retirement |
| shared transport | begin_request starts one prep+business byte budget; callback/notification cannot reset it. CPU limits include successful and failed preparation, lifetime wire checked between all tickets |
| independent review | Automated completion_verifier reviewed final frozen diff and executed exact scoped suite:102 passed35.27s exit0; before/after all source/test hashes equal. No concrete blocking finding; not human/operator approval |
| checks | Scoped102 GREEN; Ruff touched exit0; full mypy277 exit0; git diff --check exit0; no test LSP processes remain |
| changed_files | tools/language/sessions.py (5fb6a10ea00b793010bc111ae7f391c62a3dd8be374af5e8fa806196ecf1425c); tools/language/transport.py (b6b4233a22d1ae3ffe1cfe842e68b5c9b370da45fd17a19ef665d6f4329cc0d0); tests/test_lsp_sessions.py (99ff0c0ec186964e9a2a3f99188931469fd08777972294f3a0280f7781b2650d); CHANGELOG.md (2ec85f491daf0c71a36f91ddbad6b419962224a44c4debc9016b8cdadd837108) |
| git | Selectively staged only four task files; commit e9100c298d6cfb7b315b471483927b55edbfcaa1 pushed only feature/coding-agent-evolution, existing Draft PR82. Protected panel/uv hashes unchanged; no main/merge/release/tag |
| exact_next_action | Validate all ten PR82 gates against e9100c2 with synthetic merge tree binding; on success advance06VERIFIED then start07. On failure diagnose/reproduce safely and record real evidence |

### EVIDENCE-1 — 2026-10-20 e9100c2 hosted gate failure / root cause
| field | evidence |
|---|---|
| state | TASK-P02-06 IMPLEMENTED_UNVERIFIED; no platform acceptance invented |
| hosted | Platform39436901528 all3 success; Packaging39436901704 success; CI39436901358 Windows3.12 job118676176496 FAILURE |
| exact failure | test_background_verification.py::test_cancel_running_verification_reaps_test_process: ValueError invalid literal for int() with base10:'' at marker.read_text;1 failed1741 passed6 skipped280.57s |
| root cause | Test fixture writes PID with Path.write_text, which creates an empty visible marker before writing. Reader waits only exists, then int('') races publication. This is a test readiness race, not evidence of product process-cleanup failure |
| recovery scope | Publish test PID marker atomically after complete write; preserve process/capacity assertions and bounded wait. No blind rerun; no product guard relaxation or live state |
| exact_next_action | Reproduce deterministic empty-marker window, fix fixture publication, run affected background test and static checks, independent bounded review, selective test-only commit/push, fresh exact-head gates |

### EVIDENCE-1 — 2026-10-20 CI readiness-race recovery committed
| field | evidence |
|---|---|
| state | TASK-P02-06 IMPLEMENTED_UNVERIFIED; native exact-new-head checks pending |
| predecessor final | e9100c2:9/10 hosted SUCCESS; only Windows3.12 PID empty-marker test failed. Linux/macOS bothPython versions, Windows3.10, all3 platform and packaging succeeded; failures retained in prior entry |
| root fix | tests/test_background_verification.py only: fixture writes/closes sibling .pending PID, then Path.replace publishes final marker. No product code, timeout, process identity or zero-capacity assertion changed |
| baseline / hashes | File unchanged between SOURCE_BASE e66c8c9 and pre-fix HEAD e9100c2; pre-fix raw SHA4e8aa47ed55f919d2a48b564985e8395eb59745de8745dd0f2750ddedf9ce92b. tests/test_background_verification.py (946faaf6848ab1a85ae790abcb06eec7f649b1b55ae27cc06837e28bd883b3a9) |
| validation | Entire affected file48 passed13.10s exit0 in tempfile configuration; Ruff touched exit0; full mypy277 exit0; diffcheck exit0. Independent exact cancellation test1 passed0skip4.24s and narrow fix review PASS, automated only |
| commit | 6007b98062842690533876a2537e490545bc3fd4, only one selectively staged fixture file, pushed feature/coding-agent-evolution only; Draft PR82 remains open |
| next | Fresh exact-head hosted gates; predecessor failures not erased or labelled pass. On success06VERIFIED and07IN_PROGRESS |

### EVIDENCE-1 — 2026-10-20 TASK-P02-06 VERIFIED native hosted acceptance
| field | evidence |
|---|---|
| state | VERIFIED after all10 hosted gates SUCCESS, exact branch head6007b98062842690533876a2537e490545bc3fd4 |
| identity | Basee66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4; PR synthetic mergefd15c4596e3e33957ba114c77d0364604220db5d; both head/merge tree da01681161c4a778aca22dec7769eddab1eb67c0. Hosted checkout tree bound to tested branch, not falsely described as literal headcheckout |
| workflows | CI39438352665 all6 SUCCESS; Platform39438352705 all3 SUCCESS; Packaging39438352691 SUCCESS; no required gate skipped/failed/pending |
| native suites | Windows3.10 1742passed6skip338.15s; Windows3.12 1742passed6skip291.66s; Linux3.10 1727passed21skip304.50s; Linux3.12 1727passed21skip298.25s; macOS3.12 1727passed21skip341.86s; macOS3.10 job118681281031 SUCCESS (exact summary retained in hostedlogs). Windows ownership smoke1pass each |
| local / review | Independent exact102passed35.27s, scoped and source review PASS; Ruff/fullmypy277/diffcheck exit0. CI fixture recovery48passed plus independent1; source/document hashes unchanged from last committed entries |
| preserved |32 recordedfile hashes match; protected panel/uv hashes unchanged; only branch task commits pushed. Real providers not exercised in06; persistent/DAG defaults remain disabled;08 acceptance still separate |
| next | TASK-P02-07 eligible: verified06 plus03-01. Connect server-owned manager/async requests/lifecycle/health; no global manager |

### EVIDENCE-1 — 2026-10-20 TASK-P02-07 start and scoped baseline
| field | evidence |
|---|---|
| state | IN_PROGRESS; dependencies06/03-01 VERIFIED; HEAD6007b98 |
| discovery | Exact _request callers are registry common/rename and three hardening-test calls. core.create_server REGISTRARS owns language; heartbeat.lifespan owns shutdown; collect_server_health is the shared health path. Graph access current session Connection closed; source fallback bounded to these known modules/callers |
| baseline | Before any07 edits: sessions/lifecycle/language_registry/language_hardening/resource_health =>131passed2skipped38.99s exit0 in tempfile state configured before test imports; skips are Windows directory-symlink availability, not pass claims |
| scope | language/registry.py, core/registry.py, heartbeat.py, health_snapshot.py, sessions.py close_all and affected tests; common/rename async, apply_code_action sync; no tool-name/schema expansion until03-02 |
| exact_next_action | Hash guard untouched files against source/recorded baseline, write failing in-process MCP/lifecycle/health tests; implement exact07 contract, scoped regression/static/strong review/evidence; production flag remains false |

### EVIDENCE-1 — 2026-10-20 TASK-P02-07 initial RED/GREEN
| field | evidence |
|---|---|
| state | IN_PROGRESS, not acceptance; HEAD6007b98 unchanged |
| hash guard | registry/sessions/core registry/heartbeat/tests recorded SHA matched snapshot or latest ledger. health_snapshot.py4b73789097e6cdbd5394867d70d9443e7cc91a98770a113a3dccb1ca7d04a56d, lifecycle98cae593fbc83377fa4305514423b8076fa56377e5befbfc1a6b6e8fbf83a81e, resource_health test6aac10d6c9b64ea8705f14068c49c54a345483223324b53eb29ffa1eb72dc54c unchanged from SOURCE_BASE where table has no per-file digest |
| RED | Four tests failed as expected: missing server-manager denial, fresh inert managers in core, shared manager through real mcp.Client, close_all missing |
| GREEN | Same4 passed3.07s exit0 after async request/common/rename, per-server pinned config manager, close_all and shielded lifespan integration. No process from registration/list |
| shutdown | Seal manager admission with zero-wait close before scheduling5s threaded drain; preserve existing extension/maintenance cleanup inside shielded finalization. Extra parent cancellation/extension failure/health/compatibility tests pending |
| exact_next_action | Add remaining cancellation/no-spawn/health/schema regressions; update three direct hardening callers to await async _request; required scoped regression/Ruff/fullmypy/strong review, then evidence |

### EVIDENCE-1 — 2026-10-20 TASK-P02-07 integration review failure / remediation
| field | evidence |
|---|---|
| state | IN_PROGRESS, not accepted. Last broad141passed2Windows-symlinkskip40.87s; Ruff/fullmypy277 passed before current review fixes |
| review confirmed | Second raw asyncio Task.cancel detached lifespan cleanup; synchronous seal failure skipped remaining cleanup; async conversion blocked event loop during validation (2MiB valid position pulse delayed578ms), persistent manager repeated it, rename disk work also remained synchronous |
| RED | Strengthened lifecycle cancellation/initial-seal tests:2 failures2passes6.48s. Earlier missing healthcounter1RED=>1GREEN; no result fabricated |
| lifecycle root fix | Repeated-cancel shield awaitloop retains finalization until completion then preserves cancellation; initial seal guarded so bounded retry/remaining cleanup proceeds. Drain finally quarantines every unresolved session even if join fails |
| pending blocking work | Offload validation/provider/key preparation with bounded same-manager admission; offload legacy request and post-LSP rename without abandoning in-flight mutation/recovery scope. Add deterministic responsiveness and cancellation regressions before acceptance |
| exact_next_action | Finish review fixes/test evidence; re-run full07 scope, Ruff/fullmypy, independent fresh review. No07VERIFIED or activation until contract passes |

### EVIDENCE-1 — 2026-10-20 TASK-P02-07 bounded offload recovery
| field | evidence |
|---|---|
| state | IN_PROGRESS; no acceptance or activation |
| RED/GREEN | Deterministic offloop validation and rename mutation-scope tests4RED9.89s=>4GREEN1.47s. Repeatedcancel/seal regression2RED=>4GREEN1.33s. Preparation cap/caller-cancel/deadline2GREEN0.96s; combined affected7GREEN1.68s |
| implementation | Legacy resolve/validate/client and post-LSP prepare/audit/apply in nonabandoning shielded workers; raw repeated cancellation preserves enclosing mutation/recovery context until write completes. Persistent root/validation/provider/key preparation offloop under same-manager preparing+queued<=32; abandoned callers retain read-only preparation slots until completion, no late dispatch |
| latest broad failure | Required5file scope1failed149passed2skip42.21s: old backoff fixture globally replaced Thread.start and now also broke the new asyncio executor (and its shutdown). Root cause test instrumentation, owner-start semantics unchanged |
| fixture recovery | Restrict injected failure to existing lsp-session-owner thread; let executor/shutdown threads start normally; preserves assertion starts==106 and second admission lsp_restart_limited. Exact focused check running |
| pending | Re-run broad scope/affected extension and health regression, static gates, final independent review. Current source not yet committed |
| exact_next_action | Finish07 verification/evidence; preserve defaultFalse. Then task08 native acceptance/benchmark and independently eligible03-02 in dependency order |

### EVIDENCE-1 — 2026-10-20 TASK-P02-07 extended local GREEN / final review pending
| field | evidence |
|---|---|
| state | IN_PROGRESS onHEAD6007b98 with reviewed working diff; no07acceptance claimed |
| tests | Exact required5files plus extension_manager, extension_health, tool_profiles, health_snapshot_runtime:210passed2Windows directory-symlink skips52.53s exit0; isolated config set before imports in tempfile root. Final backoff seam exact1pass0.90s |
| static | Ruff touched exit0; mypy core tools scripts tests277files exit0; compileall same roots exit0; git diff --check exit0 |
| contract | Same-manager prep+queued<=32, per-sessionqueue8/nativeowners<=4; offloop validation/provider hashing/root identity; deadline/caller cancellation cannot dispatch late or free an unfinished preparation. Postrename worker keeps mutation/recovery context through repeated cancels; lifecycle awaits cleanup despite repeated cancels/seal exception and reports unresolved quarantine |
| scope | Ten task files including dated active changelog; protected panel/uv unchanged. No singleton manager, public input schema/name change, production flag activation, live database or release mutation |
| review | Final independent frozen-source review in progress; previous3confirmed findings reproduced and remediated. Automated reviewer evidence separate from human approval |
| exact_next_action | Resolve any concrete final finding, run necessary fresh checks, selective task commit and pushonly dedicatedbranch. Task07 local completion precedes08 native acceptance/benchmark; do not claim hosted gates for new commit until they run |
| working_files (unaccepted) | core/heartbeat.py (638c56e8bcc004c20252be564b6a26a7dbd015d5d19350ea1dfec18026248fb8); core/registry.py (f671f1c9773fe86ae0ac1e2c76776c66623e42b4c5bcfd406fae868b16056bed); core/health_snapshot.py (5e85dae0640ca6a699c228bef1ca31489eedaac4c1e9c51c98eb0545b297eb49); tools/language/registry.py (d10cc66e0bb93747e827302ca5260799e5388ce8d448daeb0a33c891adf82ef2); tools/language/sessions.py (1a4bfac69593993011863ebc550bf867eeca716504f8e21d422f81b0974d3c32); tests/test_lsp_sessions.py (1f1f08cabf4cf863d98d7c763c8e083cd78913a39dad62a6a37332bf3e634c00); tests/test_lifecycle.py (81b77e0656269fa861637ba6a10ea08d192719c6939c9ff80f8601b75bb035bb); tests/test_language_hardening.py (cd12190d7951b5418a555fc42144f5ffdfd60ce206c7dbf1fada64eff516755c); tests/test_resource_health.py (d2c9fc4d8d5bf9629bc38bdde3131648b2a4d88b3de0789b4b12fdece88a2042); CHANGELOG.md (280caac5eaa5a270ddd21c8da1a2c0ce0e6ccb56f598a0aa75dea9d1145f41b7) |


### EVIDENCE-1 — 2026-10-20 TASK-P02-07 VERIFIED exact-head hosted acceptance
| field | evidence |
|---|---|
| state | VERIFIED after all10 hosted gates SUCCESS on exact branch head3344d4d72f34381ee3e83a830ec4fdc1cd35296c |
| identity | Basee66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4; PR82 synthetic merge2d4aad05ddaa935271857866d3041087a5a11a15; merge/head tree both d9f10c5842c72192d9b54b11edaa6f1325aeb550 |
| workflows | CI39448303207 6/6 SUCCESS (Windows/Linux/macOS x Python3.10/3.12); Platform39448303274 3/3 SUCCESS; Packaging39448302979 SUCCESS; no required gate skipped/failed/pending |
| local / review | 210passed2skip55.67s; Ruff/fullmypy277/compileall/diffcheck exit0; frozen-diff independent reviewer PASS with two minor observations recorded, no blocker; automated only |
| preserved | panel diff and uv.lock untouched; persistent/DAG defaults remain disabled; no public tool name/schema change; only dedicated branch commit pushed; no merge/release/tag |
| exact_next_action | TASK-P02-08: implement bounded LSP benchmark and failure matrix, record cold/warm 1/10/100 x1/2 evidence, run hosted10 and independent review. Do not enable persistent mode |

### EVIDENCE-1 — 2026-10-20 TASK-P02-08 VERIFIED benchmark and failure acceptance
| field | evidence |
|---|---|
| state | VERIFIED after all10 hosted gates SUCCESS on exact branch head aeb75584f9b2c47633e4f700478cb1c6e98771bc؛ P02 8/8 tasks accepted (weighted 7/7) |
| identity | Base e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4؛ commits 53b0754 (benchmark/acceptance) and aeb7558 (macOS sampler fix)؛ PR82 Draft؛ no merge/release/tag |
| benchmark | Local reviewed fixture only (provider_id pyright, declared_version fixture), not real-provider coverage. 12 cases×35 runs, warmup5 discarded؛ warm request p95 32.449/66.592/41.650/90.005/51.288/95.025ms versus paired one-shot 123.484/126.458/112.063/111.438/137.875/150.699ms, all warm ≤ cold baseline؛ persistent spawns exactly1 per workspace (1/2) versus one-shot 1/10/100؛ errors/restarts0؛ queue≤2؛ child RSS 28.8–57.6MB persistent and 13.4–17.7MB one-shot؛ handles147–294؛ orphan0؛ report sha256 8ba0c02a858ded7aeef4ae00150f77c206ea9843397d2af725dd2792148a4bfc |
| tests | Affected suite 170passed8skip75.091s؛ focused lsp benchmark5passed؛ hang/no-replay/no-orphan, fail-closed request and warmup-only failure coverage |
| macOS fix | First hosted run failed only macOS3.10 peak_rss0 from exact argv-string matching؛ fixed with realpath/basename+ancestry matching and manager RSS captured before close؛ rerun all ten green |
| static / review | Ruff full repo, mypy277, compileall, git diff --check exit0؛ frozen v5 and v6 independent reviews PASS with no material findings؛ automated only |
| hosted | CI39475185293 6/6 SUCCESS؛ Platform39475185549 3/3 SUCCESS؛ Packaging39475185379 SUCCESS؛ no required gate skipped/failed/pending |
| preserved | panel diff and uv.lock untouched؛ persistent flag false؛ no public tool name/schema change؛ real providers UNVERIFIED؛ only dedicated branch commits pushed |
| exact_next_action | TASK-P03-02: RED typed/legacy schema tests, then explicit typed wrappers plus select_request in registry.py/requests.py while KEEPING every tool name and annotation |

### EVIDENCE-1 — 2026-10-20 TASK-P03-02 VERIFIED typed additive LSP schema
| field | evidence |
|---|---|
| state | VERIFIED after all10 hosted gates SUCCESS on exact branch head efeba869fca130bef1e0ca6626ad190b55bd1440؛ P03 advanced to 2/4 |
| identity | Base e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4؛ commit efeba86 (4 files, +482/−36)؛ PR82 Draft؛ no merge/release/tag |
| implementation | Replaced the seven-tool factory with explicit async wrappers retaining names, OPEN_WORLD_READ annotations and `{ok,method,result}` envelopes. `select_request` enforces exactly one of legacy `params` or typed `request`; typed models use `exclude_none=True` and the shared validator. Document language is inferred from six suffixes, workspace symbols default to python, explicit suffix mismatch raises `lsp_language_mismatch` before dispatch, and language URI decoding is bounded at 32,767 chars. Default one-shot dispatch remains pyright-only؛ persistent session selection consumes `language_id`. |
| schema | Canonical per-tool schema sum 2,457→9,093 bytes (+6,636, 3.70x), max 1,643 per tool؛ `required=["root"]`, `params.maxProperties=100`, six-value language enum, typed `$defs`, no `command`, no extra tool aliases |
| RED / GREEN | RED collection failed on missing `select_request`؛ GREEN focused 10 passed؛ affected 201 passed, 1 skipped, 52.946s across registry/hardening/client/providers/sessions/workspace-edit/semantic/profile/MCP integration |
| static / review | Ruff full repo, mypy 277, compileall and `git diff --check` exit0؛ frozen v1/v2 independent reviews PASS, no material findings. Non-blocking notes: legacy mode validates language but still dispatches pyright؛ non-Python `rename_symbol` inference untested؛ user docs remain pyright-only pending later docs work |
| hosted | CI39487237993 6/6 SUCCESS؛ Platform39487237663 3/3 SUCCESS؛ package39487237684 SUCCESS؛ no required gate skipped/failed/pending |
| preserved | panel diff and uv.lock untouched؛ persistent flag false؛ no merge/release/tag؛ one dated Unreleased changelog entry added؛ legacy clients still send `params` |
| exact_next_action | TASK-P03-03: RED stale-version/duplicate/missing-range/budget tests, then additive document_versions validation, safe edit bounds, post-prepare hash recheck and success invalidation in edits.py/registry.py |

### EVIDENCE-1 — 2026-10-21 TASK-P03-03 VERIFIED semantic edit snapshot and budget guards
| field | evidence |
|---|---|
| state | VERIFIED after all10 hosted gates SUCCESS on exact branch head bac7e28929ac5bf982cba5090ab7871d503d2c46؛ P03 advanced to 3/4 |
| identity | Base e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4؛ commit bac7e28 (4 files, +495/−27)؛ PR82 Draft؛ no merge/release/tag |
| implementation | Additive document_versions validation for versioned documentChanges؛ 100-file/1000-edit/2MiB bounds before any read؛ duplicate identical edit denial؛ missing/reversed/bad range safe ToolErrors؛ same-position server order and URI aliases preserved؛ rename_symbol requires expected hashes for every touched file and matches manager snapshot_version/source_sha256 before prepare؛ apply_code_action accepts optional versions؛ successful writes invalidate Python/context caches and close the persistent workspace session؛ provider command never executed and dry_run remains write/backup-free |
| hashes | tests/test_semantic_edits.py f98c9dcfac7155d32e99ea86b3a4b0d931f76a54ebf94ba42a323671090e46bf؛ tests/test_workspace_edit_regressions.py ff7c2f44e174acffb0cff9688e3777c32bf59b176194b4edd238650b62cc49c5؛ tools/language/edits.py bd7de3b4306dde7446bfed31e3278357aa7ca89b5be2f080b9c22b24951d7d53؛ tools/language/registry.py 9ecf4a649f5b32767c687ae1b864204fabff3d9c126265387e2caf5f51d9412e |
| RED / GREEN | RED reproduced missing APIs/behavior (document_versions TypeError, duplicate/budget/missing-range failures)؛ new focused tests 29 passed؛ exact task suite 50 passed then 34 passed across semantic/regressions with added TOCTOU/invalid-version coverage؛ broader affected set 264 passed, 1 skipped, 129.42s |
| static / review | Ruff touched exit0؛ full mypy277 exit0؛ compileall exit0؛ git diff --check exit0؛ review found no material finding؛ automated review only, not independent human approval |
| hosted | CI39574962187 6/6 SUCCESS؛ Platform39574962157 3/3 SUCCESS؛ package39574962041 SUCCESS؛ no required gate skipped/failed/pending |
| preserved | panel diff and uv.lock untouched؛ persistent flag false؛ no public tool name change؛ no live DB/state mutation؛ only dedicated branch commit pushed |
| exact_next_action | TASK-P03-05: finish durable journal/backup integration, run its full listed suite plus Ruff/mypy/compileall and independent review, then selective commit/push and require all10 hosted gates on the exact commit |

### EVIDENCE-1 — 2026-10-21 TASK-P03-05 VERIFIED durable workspace edit recovery
| field | evidence |
|---|---|
| state | VERIFIED after all10 hosted check-runs SUCCESS on exact head 5169eb4a16b069f91290641a20e12773c2e55a3b؛ P03 advanced to 4/4 |
| identity | Base e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4؛ commits 8e1c509 (8 files, +1132/−30) and 5169eb4 (3 files, +154/−1)؛ PR82 Draft؛ no merge/release/tag |
| implementation | Persist bounded workspace-edit evidence and exclusive verified backups before dispatch؛ prepared/may_dispatch checkpoints؛ byte-identical postimage rollback only؛ unknown outcomes stay uncertain without replay؛ journal-aware backup protection and pin-to-checkpoint window؛ reuse the owning tool operation across offloaded workers؛ deny new admissions at the unresolved quota before backups and recheck under the journal lock at prepared؛ residual URI/version/UTF-8 guards remain fail-closed |
| hashes | core/backups.py 7422566f4df27b08e2e3e04251f8cbd359accbf58e1a3e27627f897b5fc52cc7؛ core/recovery.py 14a44d672a42c298d2da89c2eab1f97794ef72ee0a44bb6aed4d64bc2b603cb3؛ tests/test_backups.py bf64e74489b4b7362a3cb7800d5245b43e8e679152cc11aca5f6ad743c07021d؛ tests/test_recovery_journal.py d029a66e78bf2eb26edd599d04a45bd8d91a101de39f5a9308635489799f87a2؛ tests/test_semantic_edits.py fc5695c2657e93767c8e3e87f9e6fef811cb4af79ac7fd9291a58a7e78527b92؛ tests/test_workspace_edit_regressions.py c0f23cd7992b96e0b492558ce5ab1ff0a37f09545cfe5b525bab52072eb89bcf؛ tools/filesystem/service.py da9eb843fed083ff09a50ab2e86c9fea65a8d458198952247295eeb33756c951؛ tools/language/edits.py b582308ec2aeacacb23ee3ffddbb0fd50bb0abf8260dbad6d7dfc1ff04c665a4 |
| RED / GREEN | RED reproduced missing recovery evidence/backup APIs and unsafe direct-call behavior؛ listed task suite 179 passed, 2 skipped, 42.82s؛ extended affected suite 323 passed, 2 skipped, 93.98s across recovery journal/reconciliation/tools, backups, semantic edits, workspace regressions, parallel workflow resources, language hardening/registry and LSP sessions؛ real subprocess crashes at prepared and mid-batch may_dispatch preserve files/backups and never replay |
| static / review | Ruff full repo exit0؛ mypy 277 files exit0؛ compileall exit0؛ git diff --check exit0؛ automated source/diff review found no material correctness finding؛ not independent human approval |
| hosted | 5169eb4 all10 check-runs SUCCESS (CI39580440368 6/6؛ Platform39580440359 3/3؛ package39580440390 success)؛ 8e1c509 also all10 SUCCESS؛ a752877 CI macOS 3.10 test_perf_benchmark peak_rss failed once and did not reproduce on 8e1c509/5169eb4؛ no required gate skipped/failed/pending |
| preserved | panel diff and uv.lock untouched؛ persistent flag false؛ no live DB/state mutation؛ no merge/release/tag؛ only dedicated branch commits pushed؛ CHANGELOG P03-05 entry already present |
| limitation | a752877 makes rename_symbol reject any documentChanges target absent from the persistent server snapshot؛ cross-file rename of unopened targets is therefore disabled pending P03-04 acceptance/review; this is not a P03-05 safety regression but a functional acceptance risk |
| exact_next_action | TASK-P03-04: review P03-01/02/03/05 and P02-08 diffs؛ run schema/denial/recovery/wire-size/crash-no-replay acceptance plus full pytest/Ruff/mypy/compileall/startup/health؛ synchronize authoring/architecture docs and the dated changelog؛ require all10 hosted gates on the exact acceptance head؛ resolve or explicitly accept the a752877 unopened-rename-target limitation |


### EVIDENCE-1 — 2026-10-21 P03-04 autonomous reconciliation and benchmark recovery opened
| field | evidence |
|---|---|
| authorization | 2026-10-21 user autonomous implementation/verification; selective dedicated-branch push and Draft PR82 only; no merge/main/release/tags |
| identity | feature/coding-agent-evolution HEAD d47e04366e7bf36079e4a6d28ded4797801ad4de; two post-ledger commits 6a10ad7/d47e043 inspected; unrelated panel/uv preserved |
| state | TASK-P03-04 IN_PROGRESS; dependencies P03-03/P03-05/P02-08 previously VERIFIED; current acceptance must be rerun |
| failure | CI39582478664 macOS3.10: test_lsp_benchmark_reports_spawn_counts_and_cleanup one_shot peak_rss=0; 1803passed21skip1failed; remaining9checksSUCCESS |
| recovery | Existing interrupted scripts/perf_benchmark.py and tests/test_perf_benchmark.py edits attributable to one-shot sampling recovery; preserve/review/test before staging |
| graph | Current repository graph ready/watch active but exact sampler symbols absent and apply_workspace_edit snippet predates P03-05; scoped current source fallback for implementation-critical recovery/acceptance; no index rebuild needed |
| exact_next_action | Reproduce deterministic sampling RED from committed implementation; validate recovery focused tests and all static gates; independent source review; docs acceptance and isolated full suite; exact-head hosted10 |


### EVIDENCE-1 — 2026-10-21 P03-04 independent acceptance failure and scoped recovery
| field | evidence |
|---|---|
| review | Independent completion_verifier FAIL: crash child state isolation, child-task offload ownership, cleanup/pin snapshot race, boolean LSP positions; no acceptance claimed |
| sampler | Committed-source RED ImportError missing _one_shot_request; interrupted implementation GREEN38passed6skip30.49s; CI macOS acceptance still pending |
| safety | Stopped owned affected pytest tree before unsafe subprocess fixtures; replaced ignored MCP_STATE_DIR with explicit SETTINGS.state_dir before child imports and asserted disposable backup containment; no live state cleanup/reset |
| ownership_RED | 2failed1.82s: unbound child offload reused parent operation; bind-in-child helper absent. Parent implementing explicit origin-checked worker binding |
| backup | Independent worker owns only core/backups.py and tests/test_backups.py for deterministic live-pin race RED/GREEN; parent sole plan writer |
| exact_next_action | Complete four narrow remediation items, focused tests then independent frozen-source review, full isolated regression/static/startup/health, docs, dedicated commit/push and exact-head hosted10 |


### EVIDENCE-1 — 2026-10-21 P03-04 remediation locally GREEN and independently reviewed
| field | evidence |
|---|---|
| state | IN_PROGRESS; four review findings fixed, no new product feature/activation; unopened rename targets explicitly accepted fail-closed with documented limitations |
| RED_GREEN | Child offload2RED->GREEN; boolean ranges3RED3pass->6GREEN; backup race1RED->38pass2skip; end-to-end crash containment assertions pass |
| affected | Isolated bootstrap pytest16listedfiles:463passed9skipped135.18s; skips remain explicit, not counted as executed behavior |
| static | Full Ruff exit0; full mypy277 exit0; compileall/diffcheck exit0; isolated main --check exit0 tool_count151 unique=true |
| health | Real stdio main with child SETTINGS bootstrap isolated:exit0 all5checks true tool_count151. Earlier parent-only isolation health attempt stopped; child may have initialized live runtime metadata (UNVERIFIED); no live cleanup/reset |
| review | Independent completion_verifier re-review PASS and separately15tests passed4.33s; automated review, not human authorization |
| wire | 9full language descriptors13452bytes sha256d8874781c6e273add966fe394be9ca5f4cbecdc1b05e3ebbbf6c8ff3a40a2098; seven input schemas9093bytes; canonical complete JSONRPC tools/list id1 response 13496UTF8bytes SHA256 2cd0b37f4608597d83f607b7b98528cf156f8b213f8e750158a03ae5e66c0559; HTTP/SSE headers excluded; installed SDK real Client fixture covered |
| hashes | core/backups.py b3f55145e9e296bbc23c3679d800d41723016a485a835a15d23cc6c95a15e5d9; core/recovery.py c9e37c40a7876713b1482efcfd85dab506f3e18904bea2ede5b45be5f93a0814; tools/language/lsp.py 5fada917b0e375b435acebc30cc46bcc8f2984a15ba82f431107c9a136fa0280; tools/language/registry.py 1e6dc2de22a1a5f2f751dc2f51ac04006aaa5b88f294bce438ddfbae09f31227; scripts/perf_benchmark.py 64228c392ed94ed63f116fb5aa72b03451139d11b0586f9fdf34bc37bba88c74; tests/test_backups.py 68ae281dcabda94b44f6f1371f8005e2b36caa0747870fe2b2a7deffb0484730; tests/test_recovery_journal.py e9b34c37c8735c6f5378f4f507ad5069ddca73adadba77c8b77457cc40b4e905; tests/test_workspace_edit_regressions.py 98a1c039923b9d23a6eb3a27f7c08ef81408229512e80701c80c183dedba7599; tests/test_perf_benchmark.py 5efbe179b42ff0c1e176b907ccf841735c05af1ab5e6c48fbb741b687eeeead6; docs/architecture.md 571e5ded9f9756560225a3cbc6cf9b138537e35a8d138f1b7ab1ad119dd831dd; docs/development/MCP-TOOL-AUTHORING.md 2a4d10f330b56b544a2980d5cff89f898e0d0e17a003d9599a611d530be55ff1; CHANGELOG.md 83d8908bb297baa82707d184be0dc7918c998758ccfab774baf2d50284c74186 |
| exact_next_action | Full regression in disposable checkout for subprocess isolation; dedicated reviewed commit/push+DraftPR82 exact-head hosted10; repeat changed benchmark acceptance; no P03-04VERIFIED before required gates |


### EVIDENCE-1 — 2026-10-21 P03-04 reviewed checkpoint committed for same-head CI
| field | evidence |
|---|---|
| identity | 25ecb60ac198af5d8f1f53a41161d8d8bdae6d8a; selective12taskfiles commit +241/-48; dedicated feature/coding-agent-evolution only; PR82 remains Draft |
| local | Affected463passed9skip135.18s; full Ruff/mypy277/compileall/startup/isolated stdio health green; independent15pass+sourcePASS |
| full_regression | Running in isolated managed test worktree; tracked task files copied byte-for-byte before run; live-source panel/uv excluded |
| pending | Full isolated pytest, full repeated changed-sampler LSP benchmark, all10 hosted checks on this exact head; no P03-04acceptance yet |
| exact_next_action | Wait exact full-suite result; run benchmark warmup5+30repetitions with defined paired thresholds; inspect current-head CI; fix only reproduced within-scope failures then revalidate |


### EVIDENCE-1 — 2026-10-21 P03-04 hosted checkpoint identity confirmed
| field | evidence |
|---|---|
| hosted | CI39647562487 and Platform39647562489 in_progress on25ecb60; Packaging39647562538 SUCCESS on same head; no acceptance yet |
| exact_next_action | Finish isolated worktree full regression, run changed-sampler B-LSP warmup5/repeat30 across12cases, require all10 exact-head checks |


### EVIDENCE-1 — 2026-10-21 Blocker registry reconciled against accepted predecessor evidence
| field | evidence |
|---|---|
| reconciliation | Historical baseline defects retained but state reflects VERIFIED P01/P02/P05-01; P03 acceptance remains pending; current user authorization supersedes former PLAN_ONLY permission row |
| preserved | All intentional DESIGN_BLOCKED/DEFERRED and real-provider UNVERIFIED rows retained; no DAG activation, deployment, merge or release authority inferred |
| exact_next_action | Continue current25ecb60 full-regression/benchmark/hosted acceptance; P04/P05-02 remain dependency-blocked until P03-04 VERIFIED |


### EVIDENCE-1 — 2026-10-21 P03-04 full isolated regression accepted; benchmark running
| field | evidence |
|---|---|
| identity | 25ecb60ac198af5d8f1f53a41161d8d8bdae6d8a; disposable test checkout diff against candidate empty; protected original panel/uv unchanged |
| full_pytest | External validated interpreter -m pytest -o addopts= -q -p no:cacheprovider --basetemp=.pytest-tmp-resume-acceptance in isolated test worktree:1809passed25skipped403.96s exit0; no skipped behavior claimed |
| hosted | Platform39647562489 SUCCESS3/3; Packaging39647562538 SUCCESS; currentPR6/10SUCCESS4pending0failed at observation |
| benchmark | cp_evolution_lsp_benchmark.py running: exact candidate+fixture hashes,12cases N1/10/100 xworkspace1/2xpersistent/one-shot;5warmups discarded then30measured; request-latency samples aggregate; paired p95 threshold unchanged; realprovidersUNVERIFIED |
| exact_next_action | Finish benchmark and exact-head10checks; only then accept TASK-P03-04 and advance P04-01/P05-02 per verified dependencies |


### EVIDENCE-1 — 2026-10-21 P03-04 exact-head hosted10 GREEN; benchmark remaining
| field | evidence |
|---|---|
| identity | HEAD 25ecb60ac198af5d8f1f53a41161d8d8bdae6d8a; PR82 synthetic merge 6343518539fced7117ca3d63715ab0a7d03b134f; merge/head tree both 934f9352c05ee0ca2ff8b96ffa1273631166a686; Draft unchanged; no merge performed |
| hosted | CI39647562487 6/6SUCCESS Windows/Linux/macOS Python3.10/3.12; Platform39647562489 3/3SUCCESS; Packaging39647562538SUCCESS;0missing/failed/skipped/pending required checks |
| benchmark | Current acceptance benchmark still running; completed N1/N10 pairs meet unchanged warm-p95<=paired-one-shot threshold; N100 pending; no full benchmark PASS yet |
| exact_next_action | Finish B-LSP12cases/5warmups/30repeats and record report digest; accept P03-04 only if all assertions pass, then P04-01 and independently eligible P05-02 with live-model benchmark authorization gate kept separate |


### EVIDENCE-1 — 2026-10-21 P04 read-only dependency preflight; no implementation started
| field | evidence |
|---|---|
| scope | P04-01 remains NOT_STARTED behind current P03-04 benchmark; read-only preflight only by existing agent, no P04 edits |
| graph_source | Scoped exact graph lookups plus current source verified Definition/ContextIndex/FileVersion, canonical_path, iter_files and consumers; preserve existing shapes |
| contracts | Definition remains1basedAST with no column; do not invent exactUTF16 coordinates. ContextIndex.snapshot_id string and FileVersion.content_sha256 current fields. resolve_path/canonical_path normalize but do NOT enforce caller-root containment; inventory must do so explicitly |
| reuse_limits | service.iter_files sorted nofollow-directory-symlink and EXCLUDED_DIRS patterns reusable; file symlinks need containment. New bounded manifest traversal must cap depth32/count256/bytes256KiB plus directories/deadline; no full arbitrary-file scan |
| python310 | No declared tomli; service._read_dependencies does not catch missingtomllib, so avoid reuse. New guarded parser retains marker/hash and missing_inputs=toml_parser_unavailable on3.10; duplicateJSON keys deny; suffix map must cover.mjs/.cjs |
| exact_next_action | Finish current P03-04 gates before marking/implementing P04-01. P05-02 live-model20x3 benchmark has separate budget/authorization stop condition, preserve that gate |


### EVIDENCE-1 — 2026-10-21 P03-04 benchmark resumable checkpoint
| field | evidence |
|---|---|
| benchmark_command | & .\.venv\Scripts\python.exe LOCAL_PATH_REDACTED; helperSHA256 80540152a963ea3809cbf5cb122ad09d21e3a6a1c2c0c98dacc74e5ea1e14050 |
| process_identity | [{"pid": 1352, "create_time": 1792605495.3823545}, {"pid": 13164, "create_time": 1792605495.3756807}]; verify exact command and creation time before treating PID as same process; process handles are not authorization to kill |
| partial_report | LOCAL_PATH_REDACTED; 9/12completed cases; no final PASS yet; current N100 one-shot1workspace has completed5/35runs including discarded5warmups |
| verified_gates | Exact25ecb60 all10hostedSUCCESS; isolatedfull1809passed25skip; Ruff/mypy277/compileall/startup/health+independentreviewPASS |
| exact_next_action | If matching benchmark process still live, wait without launching duplicate benchmark. If ended, read final output/partial JSON and require12cases+statusPASS+pairedp95 before acceptance; final durable report is written under Downloads/ComputerPilot-Coding-Evidence. On failure diagnose first, retain partial evidence; do not relabel missing samples as pass |


### EVIDENCE-1 — 2026-10-21 P04-02 implementation-critical source drift identified before entry
| field | evidence |
|---|---|
| state | Read-only reconciliation; P04-02 NOT_STARTED, no implementation while P03-04 benchmark pending |
| current_source | LspSessionManager.arequest returns raw provider result except rename snapshot metadata; snapshot is aggregate health only. _Session.capabilities and SessionKey are private; same key can be reused after session CLOSED/recreated; _generation is config generation, not incarnation |
| blueprint_gap | P04-02 requires capability snapshot and fixed session identity across batches, but listed intelligence.py/requests.py/tests-only scope cannot prove either through existing supported APIs |
| minimal_repair | At P04-02 entry reconcile blueprint to include sessions.py/test_lsp_sessions.py: internal immutable bounded request evidence containing known capability flags/provider/session key and per-session incarnation; preserve arequest raw return contract, existing queues/owner/admission/cancellation, aggregate health privacy; do not expose private manager dicts or add separate session state owner |
| acceptance | Compare per-request incarnation across complete adapter batch; same-key restart/generation change discards output without retry. Add before-dispatch capability gating and native compatibility tests. This is required plumbing for the already-specified P04 capability/freshness contract, not a public tool/API expansion |
| exact_next_action | Finish current B-LSP, acceptP03-04 only afterPASS, implementP04-01 first; verify precise source/return shapes then make the bounded owner evidence repair in P04-02 before adapter |


### EVIDENCE-1 — 2026-10-21 TASK-P03-04 VERIFIED complete exact-head acceptance
| field | evidence |
|---|---|
| state | TASK-P03-04 VERIFIED; P03 COMPLETE all5tasks, weighted4/4; totalweighted progress16/29 unchanged because acceptance taskweight0 |
| identity | 25ecb60ac198af5d8f1f53a41161d8d8bdae6d8a; commit25ecb60; all12reviewed taskfiles selectively committed/pushed; DraftPR82; syntheticmerge6343518539fced7117ca3d63715ab0a7d03b134f and headtree934f9352c05ee0ca2ff8b96ffa1273631166a686 match |
| gates | Full isolated1809passed25skip403.96s; affected463passed9skip135.18s; independent15passed4.33s+sourcePASS; Ruff/fullmypy277/compileall/startup151/realstdioisolatedhealth exit0; docsUTF8/fences/links+diffcheckPASS |
| hosted | CI39647562487 6SUCCESS; Platform39647562489 3SUCCESS; Packaging39647562538SUCCESS; exact25ecb60 no missing/failed/skipped/pending required gates |
| benchmark | PASS12casesx35invocations,5warmups discarded/30measured each;13320measuredrequests; elapsed1587.188s; paired requestp95 warm/cold: 1req/1ws 40.195/158.725ms; 1req/2ws 48.344/164.905ms; 10req/1ws 38.693/183.352ms; 10req/2ws 77.89/174.388ms; 100req/1ws 38.607/168.145ms; 100req/2ws 71.98/180.294ms |
| resources | Errors/restarts0; expectedspawns every invocation; no orphan/cleanup uncertainty; peakchildRSS56.793MiB; handles306; queue2 |
| report | LOCAL_PATH_REDACTED; SHA2560041c129dddb84c5532f53be7804802f3051b893d0148aa1a0dae3d011889a24; head/fixture/benchmarkdigests rechecked after run |
| limits | RealprovidersUNVERIFIED; unopened rename targets intentionally failclosed until snapshotted; native/DAG defaults remainfalse; no mainpush/merge/release/tag; live state not cleaned/reset; earlier stopped health attempt state impactUNVERIFIED documented above |
| exact_next_action | TASK-P04-01: frozen evidence/symbol contracts plus bounded project manifest inventory and root containment; RED->GREEN focusedtests/Ruff/mypy/review; P05-02 independentlyeligible with live-modelbudget gate retained |


### EVIDENCE-1 — 2026-10-21 TASK-P04-01 implementation opened from verified dependencies
| field | evidence |
|---|---|
| state | TASK-P04-01 IN_PROGRESS; depsP01-04/P03-04 VERIFIED; baselineHEAD25ecb60; no existing task-file changes; panel/uv DO_NOT_TOUCH |
| scope | New tools/project/intelligence_models.py, tools/project/project_types.py, tests/test_project_intelligence_models.py only; parent alone owns plan and changelog; unchanged current Definition/ContextIndex/FileVersion and registrations |
| exact_next_action | Worker owns three blueprint files REDtests->minimum models/inventory->GREEN; parent reviews contracts/boundaries and independent verification; no manager/provider activation |


### EVIDENCE-1 — 2026-10-21 TASK-P05-02 static implementation opened independently
| field | evidence |
|---|---|
| state | TASK-P05-02 IN_PROGRESS; depsP05-01/P03-04 VERIFIED; P04-01 worker owns separate three files; parent owns profile/bridge/registry/tests and sole plan/changelog |
| graph | Scoped exact ToolProfile/ExtensionMCPServer/create_server graph call failed Connection closed in current session; bounded current-source fallback confirms add_tool public seam, construction closure and native/external dispatch eligibility; no tooling rebuild necessary |
| scope | ToolProfile.native_allowlist defaultNone; coding_core exact43names matching coding domains; public SDK add_tool filter + directcallguard + startup equality; existing extension explicit-profile eligibility reused; native defaults/full/coding unchanged |
| gate | Static SDK journey and job lifecycle, catalog byte footprint and denied-no-dispatch checks authorized. Plan explicitly forbids live-model20x3benchmark without separate budget/authorization; no such budget supplied, so live pilot remains BLOCKED and no default switch/release |
| exact_next_action | RED exactcatalog/dispatch/extension-profile/staticjourney tests; minimum implementation using public SDK seam; focused GREEN+Ruff/mypy/review; retain live-model benchmark blocker truthfully |


### EVIDENCE-1 — 2026-10-21 P04-01 containment review failure and P05-02 static recovery
| field | evidence |
|---|---|
| P04-01 | Independent review FAIL: queued directory replaced with Windowsjunction before scandir leaks outside-only filename in missing_inputs. Worker owns repair/tests in same3files; no acceptance. Existing73pass2skip does not cover replacement race |
| repair | Require queued directory identity/root/reparse validation before and after scandir open and before consuming entries; discard changed directory batch with directory-only safe reason; deterministic junction RED/GREEN without requiring symlink admin |
| P05-02_RED_GREEN | 3RED missingprofile/constructorguard/startupcode -> guardsGREEN; extensionexplicit-profile and metadata forwarding suite49pass6.56s; static realSDK cachedcatalog journey includinghashguarddryrun/apply/syntaxverify/backgroundpytest/job_wait/output/gitdiff1pass5.76s in disposablecheckout |
| fixture_diagnosis | Journey initial failures were test assumptions: WindowsCRLF fixture, code_context requirespath not blueprintroot, Gitdiff header required, directverify hasstages notfreshness, start_verification requiresidempotency_key. Corrected fixture to current APIs; no production API loosened |
| static | Fullmypy280pass after SDKresultunion guard fix; Ruff line-length findings fixed; catalog full151/160393bytes and coding96/99431bytes descriptors byte-equivalent to25ecb60 baseline; coding_core43/46125bytes; tokenizerNOT_MEASURED absent; no install |
| exact_next_action | Finish P04 junctionrace regression/re-review; finish P05 required3file regression/currentstatic and independent security review. Live-model20x3pilot remains separatebudget/authorization gate, not invented approval |


### EVIDENCE-1 — 2026-10-21 TASK-P05-02 static implementation GREEN; live UX budget blocker retained
| field | evidence |
|---|---|
| state | BLOCKED only for acceptance; static implementation complete and separately counted as implemented-unverified. No default change or main publication; Draft checkpoint pending commit/hosted revalidation |
| RED_GREEN | 3RED missingprofile/allowlistguard/startupcode -> focused guardsGREEN; aliasmetadata and4explicitextensionprofile cases pass; real cachedcatalog SDKjourney1pass5.76s; required3files98pass16.97s; extended9files241pass33.36s |
| review | Independent completion_verifier staticPASS and9tests2.11s; no material findings; explicitly not fullTASK/UXacceptance and not human approval |
| static | Fullmypy280pass; Rufffull/compileall/diffcheck exit0; docs/config/readme/authoring describe unreleasedoptin and livepilotpending |
| catalog | Canonical UTF8 descriptors full151/160393bytes SHA c08ea2f2ca3d0d8945c70fa58af7256e0411ff597ce90514fc0d76538fca0a0f; coding96/99431bytes SHA da0f898e4f528cd78fdfdad973998241b0ef8e81cff5c3c80f7174d0e1c0a89b; both exactlists equal25ecb60 baseline; coding_core43/46125bytes SHA ba2ab74dabf9c86ba3914741b0ee61e2337fb2d49d8a190a670fe0636617a081; tokensNOT_MEASURED(no tokenizer) |
| blocker | P05-02 blueprint5 explicitly forbids live-modelE2E without separatebudget/authorization. No model/client/version or budget supplied for20tasksx3repetitionsx3profiles. DeterministicSDK success is not modelUXsuccess/p95nonregression; those gates UNVERIFIED |
| hashes | core/tool_profiles.py 235aad6bab928475df8128a924159fc48f0e0192020223aab0df6c02482f5efe; core/registry.py 8d2c05c6c31760b3bc1a8a6d9c8f7adf4e01fbb6827676bcfd1c3270d14d545c; core/extensions/bridge.py 271e07bffc653203f3eba241d6214d2605e26a3561d4c6110a01a9286b9b49cc; tests/test_tool_profiles.py 2c0a6d89d4ef124e9cd6ad1d3b3e106a73f8f2798c682117a0e5ea22ef2fea44; tests/test_extension_registry.py de81d5c78a04206bc5fc5fa4d040af53f4db3fd51d237c12eed5fa463b64037b |
| exact_next_action | Commit/push only reviewed staticprofilefiles+docs on dedicatedbranch and require current-head hosted10; leave P05-02 BLOCKED until approved pilot. Continue independent P04-01 race remediation/review; no paid/live-model request made |


### EVIDENCE-1 — 2026-10-21 P04-01 ABA junction privacy failure found before acceptance
| field | evidence |
|---|---|
| state | TASK-P04-01 remainsIN_PROGRESS; not accepted after prior77pass2skip and first race remediation |
| failure | Worker independently reproduced ABA: outside iterator opened through temporary junction then original inode restored before return; unverified child lstat exception echoed outside-only basename in missing_inputs |
| recovery | Worker repairing same3files with directory-only reasons for unverified discovered entries, explicit ABA regression and no fabricated outside language/package evidence; no OS sandbox/no-outside-enumeration claim |
| exact_next_action | Finish final bounded containment/privacy fix and focused/static gates, then fresh independent P04-01 re-review before any acceptance or dependent adapter work |


### EVIDENCE-1 — 2026-10-21 P05-02 static Draft checkpoint committed; acceptance remains BLOCKED
| field | evidence |
|---|---|
| identity | a80883f94537ce1234280fbb91af4bda2aa862c2; selective10profile/source/test/docfiles commit+243/-3; only dedicated branchpush; DraftPR82; P04newfiles notstaged; userpanel/uv preserved |
| gates | Extended241pass33.36s; independent9pass+staticreviewPASS; fullmypy280/Ruff/compileall/docslinks/diffcheckPASS for reviewed static implementation; hosted10 for newhead pending |
| blocker | Live-model20x3pilot separatebudget/authorization stillmissing; P05-02 BLOCKED, implementation counted separately, no defaultswitch/modelquality/token claim |
| exact_next_action | Inspect exact-head hostedCI; continue P04-01 finalABA privacyfix+independentreview; do not mark P05-02 VERIFIED from static or CI success alone |


### EVIDENCE-1 — 2026-10-21 TASK-P04-01 committed locally accepted source; hosted gate pending
| field | evidence |
|---|---|
| state | IMPLEMENTED_UNVERIFIED; task NOT VERIFIED until exact-headPython310/POSIX hostedgates; P05-02 code remainsBLOCKED on independentliveUXbudget |
| identity | 7e9dfe7c1437f5f2fcd2f260e3c4648b3c3fb24b; selective4files (+807) evidence/models/inventory/tests+datedchangelog; dedicatedbranch only; DraftPR82; protectedpanel/uv untouched |
| RED_GREEN | InitialmissingmoduleRED; deadlinefalsecompleteRED->GREEN; nativequeuedjunction outside-nameRED->GREEN; ABAnameRED->GREEN; metadataABA falsecomplete/languageRED->GREEN; six nativejunction/identity cases executed |
| local | Parent final specified models+cache suite79passed2symlinkskip1.36s; independent rerun79passed2skip1.35s; fullRuff/mypy280/compileall/diffcheckexit0; independent frozen sourcePASS |
| contract | Frozenversion1 SymbolKey/SnapshotEvidence; bounded extra-forbid evidence models; inventory256manifests/256KiB/depth32/10kentries/5s; JSONduplicatesdeny; tomllib310guardmarker/hashfallback; callerroot/symlinkscope; nearestowner+consumptionhashrecheck; unknownASTcolumn notUTF16 |
| privacy | Queuedidentity checks and strict existing in-root resolution; directory-only unverified-entry errors; changediterator evidence incomplete; no outsidefilename/payload/language evidence in fixtures; no OSsnapshot/sandboxclaim |
| hashes | tools/project/intelligence_models.py c3460f287d7732b9deca3acd9e7ed5a9258a13864745bd2fcce1cc4b24833518; tools/project/project_types.py 6d39a55db96e888f46421e4c175fe94cd30f15273d98b0c5d62c52ba7c90112a; tests/test_project_intelligence_models.py eefd5092b36e46b6b3c30b1aae22e1b6e9848bed7c2faeaa4c1c853c34c518fc |
| exact_next_action | Require hosted10 on7e9dfe7 exacthead, including native310/POSIX; inspect failurelogs and safelyfix genuine rootcauses if any. Then P04-01VERIFIED and beginP04-02 with recorded minimal existing-manager evidence repair; P05livepilot remains separateBLOCKED |


### EVIDENCE-1 — 2026-10-21 P04-02 bounded owner contract reconciled before dependent implementation
| field | evidence |
|---|---|
| state | Read-only blueprint repair; P04-02 remains NOT_STARTED until P04-01 exact-head acceptance |
| source | Current arequest raw object; owner alone holds capabilities and document versions; SessionKey reused on same-key restart; hierarchy item URI currently not synchronized |
| decision | One immutable owner-published SessionReply, per-session incarnation; original arequest unwraps value; no introspection spawn/new registry/private adapter peeking; fixed hierarchy validators only |
| scope | sessions.py/test_lsp_sessions.py added only for already-required capability/session freshness proof; parent requests/intelligence tests; source fallback because scoped graph connection closed; source-critical paths verified directly |
| next | Wait remaining current-head CI; then mark P04-01 VERIFIED and P04-02 IN_PROGRESS before edits |


### EVIDENCE-1 — 2026-10-22 TASK-P04-01 accepted on exact hosted commit
| field | evidence |
|---|---|
| identity | 7e9dfe7c1437f5f2fcd2f260e3c4648b3c3fb24b; DraftPR82 unchanged; tree9408b34c7bbee6ae4d5a93996a1ed01e30bf3523 equals PR synthetic merge a1e883f66b0d176a49f283bc9ebfefda23d65f40 tree |
| hosted | All10SUCCESS exacthead: CI39655101067 six OS/Python310+312; Platform39655101074 three OS/Python312; Packaging39655101126; none skipped |
| acceptance | Prior local79pass2symlinkskip, independent79pass2skip sourcePASS, Ruff/fullmypy280/compileall/diffcheck retained; source unchanged since tests |
| scope | P04-01 VERIFIED; P05-02 staysBLOCKED on separately authorized live-model UX pilot; protected panel/uv unchanged |
| next | Enter P04-02 RED fixed hierarchy validation and owner SessionReply, then bounded semantic adapter |


### EVIDENCE-1 — 2026-10-22 TASK-P04-02 entry
| field | evidence |
|---|---|
| state | IN_PROGRESS before source edits; dependencies P04-01/P03/P02 VERIFIED; frozen root7e9dfe7, only protected panel/uv dirty |
| scope | Parent owns requests.py/intelligence.py/tests/test_project_intelligence.py plus plan/changelog; worker owns sessions.py/tests/test_lsp_sessions.py; neither stages or modifies other files |
| contract | Use reconciled blueprint: owner-published SessionReply, raw arequest compatibility, fixed internal hierarchy methods, per-session incarnation, bounds/provenance/deadline/no guessed selection |
| next | RED tests first; no public arbitrary method, no new process/index/cache owner; P04-03 registration remains untouched |


### EVIDENCE-1 — 2026-10-22 P04-02 hierarchy validator RED to GREEN checkpoint
| field | evidence |
|---|---|
| RED | 12 failed missing fixed internal hierarchy methods (lsp_method_unsupported), isolated state; short summary retained, repeated stacks truncated but all12 listed |
| GREEN | 12passed0.85s after strict item/range/UTF16/containment validation and five fixed methods; mypy requests PASS |
| style | Ruff found E731 lambda + twoE501 lines; replaced local lambda with typed def and wrapped lines; rerun required with next adapter checks |
| next | Add adapter RED tests; owner SessionReply worker proceeds in nonoverlapping files; public registration unchanged |


### EVIDENCE-1 — 2026-10-22 P04-02 semantic adapter RED checkpoint
| field | evidence |
|---|---|
| RED | Two focused adapter tests failed ModuleNotFoundError tools.project.intelligence; expected before implementation; 20other tests deselected |
| coverage | New bounded fake-manager transcript tests target exact/ambiguous selection, explicit anchor, LocationLink/reference evidence, cycles/pagination, capability denial, outsideURI privacy, session/source changes, deadline/fanout/depth |
| next | Implement adapter against owner SessionReply contract; all failures remain accounted and task IN_PROGRESS |


### EVIDENCE-1 — 2026-10-22 P04-02 adapter progress and reproduced review findings
| field | evidence |
|---|---|
| initial_GREEN | 22passed2.45s and touched Ruff/mypy PASS after Windows fake-key canonicalization, LF fixturebytes and style fixes; earlier7fail then1CRLFhashfail diagnosed, no retries without cause |
| new_RED_GREEN | Three REDs: distinct-container collapse, unrelated hierarchy root acceptance, missing capability reportednotfound; implemented provider-qualified keys/rootmatch/explicitcapreason then26passed1.85s/Ruff/mypyPASS |
| independent_review | Reviewer reproduced query-dependent qualifiedidentity (same rootcause; regression short/qualified equality added) and missing/null mandatory fromRanges accepted as empty; missing/null regression2RED now fixed by list-required guard, final rerun pending |
| owner | Worker final frozen sessions.py44line/test219line:166passed1symlinkskip53.83s, focused17passed11.50s, touchedRuff/mypy/compile/diffPASS; queued item-range TOCTOU separatelyRED->GREEN |
| next | Final adapter edgecase checks and full affected regression, fullstaticchecks, independent final review; task IN_PROGRESS |


### EVIDENCE-1 — 2026-10-22 P04-02 normalization regressions GREEN before integration gates
| field | evidence |
|---|---|
| RED_GREEN | Missing/null fromRanges2RED->strict list; coarse workspace->LocationLink selection2RED incl multipledefinition ambiguity->normalization/no guess; independent no-definition-cap fullspan1RED->prepared exact name/kind/URI/fullspan normalization before references |
| GREEN | All33 adapter/validator tests passed2.48s; touched Ruff/mypy PASS before final readability-only helper extraction; regression required after last edits |
| owner_review | Independent frozen owner reviewPASS,17newcases independently17passed11.67s; raw/RenameResult compatibility and postverifypublication verified |
| scope | Adapter remains internal/unregistered; no paid/live provider/model pilot; existing process owner only; changelog22Oct unreleased entry added |
| next | Freeze source after affected regression/fullstatic, inspect diff, request final independent adapter re-review; then selective checkpoint+exact-headCI |


### EVIDENCE-1 — 2026-10-22 TASK-P04-02 tested Draft checkpoint committed
| field | evidence |
|---|---|
| identity | b74dc0f565fb94c36cc99cae204bda8609ddb69e; selective6files1103insert/9delete; dedicatedbranchpush only; DraftPR82; protected panel/uv unchanged |
| local | Final after-last-edit9file regression333passed3symlinkskip57.24s; fullRuff PASS; fullmypy282 PASS; compileall/diffcheckPASS; tests isolated in Temp, no default runtime startup |
| independent | Owner17passed11.67s + sourcePASS; frozenadapter33passed2.57s + positiveinheritancefixture + sourcePASS; no impersonated liveprovider/model approval |
| contract | One session owner; immutable published key/incarnation/capflags/docversions; raw arequest/RenameResult compatibility; exact/ambiguous selection, normalizedLocation/Link/hierarchyroots, five internalmethods, range/URI/hash/session guards, bounded BFS/deadline/pagination; public context registration pending P04-03 |
| hashes | tools/project/intelligence.py 7dbdafd9fa4512157d2f774696192542cdbbba8d8317e20c7641dced9e632d72; tools/language/requests.py cfc9d0439a4ba5b795beb1fa41e438ff2280bfb30859192edd813046639dfc2c; tools/language/sessions.py 8d88b360fde52fb63f72906c3067b936be9863327431720be5403310cc6e70ec; tests/test_project_intelligence.py c4bb7bbffd8eb3e6433779e1da69e14c0b12891f2b5180ff25192c509e65b9cb; tests/test_lsp_sessions.py 39246c9c01b49cf42d8a336858472f3ec75f129c6cee118d46834424934c629b |
| state | IMPLEMENTED_UNVERIFIED until exacthead hosted10; no dependent P04-03 code until acceptance; P05liveUX remainsBLOCKED |
| next | Poll exactb74dc0f workflows; investigate genuine failures then final sameSHA acceptance. Read-only P04-03 graph/source/cache-boundary reconciliation may proceed. |


### EVIDENCE-1 — 2026-10-22 P04-03 read-only source reconciliation before entry
| field | evidence |
|---|---|
| state | P04-03 NOT_STARTED; dependency P04-02 hosted10 stillpending (Linux310/312 nowSUCCESS; Windows/macOS remain) |
| discovery | Current-root graph ready/watch reports parsecoverage but nested code_context wrapper absent; exact lookup_code_context graph identified existingASTowner19callers13callees inclregistry/benchmark/tests. Verified current scoped registry/context/index/session/edits/filesystem source; graph is retrieval aid, notfreshnessproof |
| compatibility | Independent designcheck supports nullable defaultNone; SDK fillsdefaults, transport-specific raw Context alternative inconsistentwithdirectcalls; NonelegacyPython and explicitauto multilingual selection preserve actualpath API |
| cache_gap | Existing PythonMetadataCache invalidation drops untracked/nonPython generation events; scalar monotonicepoch onEVERYinvalidate/clear closesnativewrite gap without subscribers. Cache mustalso verifyfullsourcefingerprint for external edits/add/delete; returned-doc-only hashes insufficient |
| pagination_gap | Existing DTO caps outputedges1000 while traversal10000; publiclegacy includes flags+ASTmerge require one fullbounded normalizedsnapshot beforefilter/globalpagination. Internal resultmodel+paginatedpublicadapter avoids repeated providerbatches and extra graphowner |
| next | AcceptP04-02 only afterall10 exacthead; then markP04-03 IN_PROGRESS and implement reconciled contract with RED tests; no source edits made in preflight |


### EVIDENCE-1 — 2026-10-22 P04-03 preflight contract fixed without implementation
| field | evidence |
|---|---|
| compatibility | Independent source/SDK review: nullable language_id=None bestpreserveslegacy mixedPythonroot/directcalls; explicitauto newinventorymode. Public Context rawparams alternative exists but transportcoupling rejected, notclaimed unavailable |
| owner_design | Manager-local bytes cache32entries/16MiB/TTL30+LRU, bounded<=32 asyncflights using concurrentFuture and shielded loopwaiters; synchronous SingleFlight blocks/rejects same-loop overlap so reuse notsafe. Producer finally always completes/fails sharedfuture; close/retire fences generation and publication before drain |
| freshness | Full existing-inventory sourcefingerprint + metadata monotonic epoch before/after scan + fresh provider/SessionKey/exact incarnation; recheck everydelivery. Unavailable/partial fingerprint disables admission/hit/coalescing; source-file-only digest no fullworkspaceproof |
| integration | Internal SemanticSnapshot cap10000 edges (temporary normalizedresult, notnewindex) and original paginatedContextEvidence cap1000. Filter/merge before globallegacy pagination; no secondproviderquery merelyforpagination. ASTimports/test remainder can use existinglookup offset/total without materializing >1000 rows |
| bounds | Optional scanner fingerprint hashes all supported source paths+bytes and manifests under existing depth/entries/deadline and16MiB cumulative cap; failures disablecache, never partialfingerprint. ASTcache includesmanifestdigest/worktreeidentity inexistingowner, keeps sync helper signature |
| timing | At implementation test cold-ready/warm deadline distinction: public adapter absolute callerdeadline remainshard bound, first request can warm within supplied cap, then<=5s remaining batch; no unbounded warmup or health/getter activation |
| next | One Windows310 suite stillrunning,9/10SUCCESS exactb74dc0f. NoP04-03code beforedependency acceptance; then REDs for cache/lifecycle/legacy/multilang/globalpagination. |


### EVIDENCE-1 — 2026-10-22 TASK-P04-02 exact hosted acceptance
| field | evidence |
|---|---|
| identity | b74dc0f565fb94c36cc99cae204bda8609ddb69e exactPRhead; syntheticmerge ca3da8ea57f600babcad0ac48e32ad1da6cac7f9 has identical tree35ce595037b951b6e848aa609433bd4dca84cc6b |
| hosted | All10SUCCESS: CI39822582979 six OS/Python310+312; Platform39822582976 three OS/Python312; Packaging39822582988; no skipped gates |
| local_retained | 333passed3symlinkskip57.24s + fullRuff/mypy282/compile/diffPASS; independent17owner/33adapter+positiveinheritance PASS; reviewed hashes unchanged |
| state | P04-02 VERIFIED. P04-01/02 now2/4 P04verified; accepted18/29=62.1%, implementation19/29=65.5% with P05liveUX stillunverified |
| next | P04-03 owner cache/epoch + existing inventory fingerprint + ASTcachemanifest + async publicwrapper per reconciled blueprint, RED first |


### EVIDENCE-1 — 2026-10-22 TASK-P04-03 entry
| field | evidence |
|---|---|
| state | IN_PROGRESS before source edits; dependencies VERIFIED; exactHEADb74dc0f; only protectedpanel/uv dirty |
| ownership | Worker sessions.py/test_lsp_sessions.py + index.py/test_project_cache.py; parent project_types.py/modeltests, intelligence.py/adaptertests, context.py/code_contexttests, registry.py/newmultilangtests, docs/changelog/plan. No staging untilallreviewed |
| contract | Source-reconciled Nonelegacy/default, explicitauto multilingual; same manager boundedbytescache+asyncflights, fullfingerprint+epoch+sessionguards; one internal fullsnapshot thenlegacyfilter/merge/globalpage; no watcher/parallelindex/newprocessowner |
| next | RED perboundedowner/scanner/contract groups; publiclanguage tools stay fixed; no liveprovider/model activation |


### EVIDENCE-1 — 2026-10-22 P04-03 RED GREEN and scanner review recovery checkpoint
| field | evidence |
|---|---|
| parent_RED_GREEN | New fullsnapshot missingAPI and coldreadiness deadline2RED ->35adaptertestsPASS2.88s/RuffPASS; internalSemanticSnapshot cap10000 excludedexactcachegeneration, originalpagedDTO cap1000; five-secondbudget startsafterfirstrealreply underhard supplieddeadline |
| owner_inprogress | Owner cache/epoch worker implementation inprogress; parent mypy recursivelyfound sessions.py325 wrap_future genericmismatch; senttoowner, notclaimedPASS |
| scanner_initial | Workernewfingerprint8RED->62passed1symlinkskip1.29s/Ruff/mypyPASS; scannerdefaultpreserved andsixjunctionraces tested enabled+disabled; sourceNOT accepted yet |
| independent_finding | Final directory/.git checks miss previouslyhashed file same-size+restoredmtime rewrite during laterfileread; reproducedoldnonnullfingerprint whilefreshscan differs. Windowsctime cannotprove contentfreshness. Scannerworker nowaddsbounded final source/manifest rehash overretainedpaths, no secondwalk; mismatch=>None |
| next | Complete scannerRED->GREEN/re-review, add ASTmanifest/worktree evidence REDs, registry/cacheintegration; no taskacceptance/commit while owner/scanner outstanding |


### EVIDENCE-1 — 2026-10-22 P04-03 AST evidence GREEN and scanner subunit accepted
| field | evidence |
|---|---|
| AST_RED_GREEN | Manifest and.git same-size/restoredmtime snapshottests2RED + missingbackend/evidence1RED ->59passed1skip combinedAST/cache3.33s; mypy2PASS. Ruff found tupleconcatRUF005 + longline, fixed withlistframe/rootdigest; finalrerunneeded |
| AST_contract | Existing sync lookup signature and legacy1-basedline/source_kind retained; extra python_legacy SymbolKey unknowncolumnNone andperdoc SnapshotEvidence; existingASTcache signature nowincludesmanifestcontent/worktree/inventorydigest, incompleteinventory bypassescache andreturnsstalehonestresult |
| scanner_recovery | Two independent source/manifest midscanrace regressions RED->GREEN; final bounded second contentpass overretainedpaths (no secondwalk), afterthat directory/.git checks. Frozen491b1b1.../5ab9b6... independently101passed2skip2.01s + sourcePASS; subunitonly, no whole-taskacceptance |
| owner_progress | Cache/epoch25focusedPASS/Ruff/mypyPASS; independentclose-before-fingerprint race1RED->GREEN, final210case regressionrerunpending |
| next | PublicMCPwrapper RED journeys (legacy, explicitauto, TS/PHP providerabsence, same-manager realfixture warmcache/pagination/invalidation) thenimplementation/regression/independentreview |



### EVIDENCE-1 — 2026-10-23 TASK-P04-03 exact hosted acceptance
| field | evidence |
|---|---|
| identity | c8f38aedc239e8e878e90f3b4753a21a98353b04 exactPRhead; selective14files2027insert/74delete; DraftPR82; protectedpanel/uv unchanged |
| local | Final isolated fullsuite1986passed26skipped442.77s (copy includes START_MCP.bat; live .agent_state untouched); touchedproject327passed2skipped; fullRuff PASS; fullmypy283 PASS; compileall/diffcheckPASS |
| hosted | All10SUCCESS exactc8f38ae: CI39850035156 six OS/Python310+312; Platform39850035191 three OS/Python312; Packaging39850035164; 10check-runs total, none skipped |
| contract | Omittedlanguage_id legacyPythonAST; explicitauto inventorylanguage; mixedroot withoutfile/anchor->language_required; unavailableprovider honestincomplete+hint noSpawn; exactfilefilter; tsconfig/jsconfig fingerprintbytes; one snapshot<=10000edges beforefilter/ASTsupplement/globalpage<=1000; manager cache32/16MiB/TTL30/LRU/singleflight fenced by fullfingerprint+epoch+worktree+session/doc; stale supplement disables import/testcompleteness; no perdefinition rehash |
| review | Findings#2-#6 RESOLVED; circularimport,max_depth0completeness,stalefreshness,file-onlyworktreeidentity,incompletereasonpropagation allRESOLVED; reviewer modified no files |
| hashes | tools/project/intelligence.py fd7269684a39efb4c0d9d9bd28d65d86df389dd48e2eb179b8aecc9844cf0363; tools/project/context.py f2dafe17e75dd51f5a855b9bc33d5b7d4e125784822a09c83b45df4a5e862903; tools/project/registry.py 5736b0c40f9f388f7198033dc658757c7ddf59e910b6b91b3c01772f272a7378; tools/project/project_types.py c47c2d6d9ff41b9672c4ac16106479df5edf86528514e5f9e72551ceea73851a; tools/project/index.py fa57e5ea87c59c35426c7ff2a5ed5a2dd0c51f9d660fbecc3e02c97b12fd41e7; tools/project/intelligence_models.py 37cdf50377d0ac8adaa2ec00675298e6e2a37ed3d57345267302ba2c3f8de538; tools/language/sessions.py dc7e73595f3c5f366ce3dec1b08ddd57650cccb58fc35a4deb29831798e73f65; tests/test_multilanguage_context_registry.py 5e0dbfd549d525c853dba59ac3cd36472046d0b081ce490bd351595393b25584 |
| state | P04-03 VERIFIED. P04 tasks3/4verified; accepted19/29=65.5%, implementation19/29=65.5%; P05liveUX stillunverified |
| next | TASK-P04-04 RED fixtures/acceptance for Python/TS/PHP/worktree with expected edges/ranges/digests, provider-absence, symlink/rename/broken-syntax/circular/stale cases; no live provider activation |




### EVIDENCE-1 — 2026-10-23 TASK-P04-04 entry
| field | evidence |
|---|---|
| state | IN_PROGRESS before fixture edits; dependency P04-03 VERIFIED exactc8f38ae hosted10; exactHEADc8f38ae; only protectedpanel/uv dirty |
| scope | tmp_path fixtures in tests/test_multilanguage_context_registry.py + tests/test_project_intelligence.py; shared helper only if both consume it; no node_modules/vendor committed, no docs edits before acceptance |
| contract | fixed fake-provider map with initialize=1/shutdown=1; expected edges/ranges/digests declared by fixture; zero false definite edges; missing relation=>incomplete; concatenated pages==unique expected set; worktreeA excludes B; unavailable provider never executed; broken syntax source preserved; latency/RSS NOT_MEASURED |
| next | RED fixture/acceptance pack, then GREEN only for real gaps; no live provider activation and no concurrent context writer |




### EVIDENCE-1 — 2026-10-23 TASK-P04-04 exact hosted acceptance
| field | evidence |
|---|---|
| identity | 04143829c323c291fad10085b659ee33ab7eb0a5 exactPRhead; selective1file422insert/34delete; DraftPR82; production unchanged; protectedpanel/uv unchanged |
| local | Final isolated fullsuite1991passed26skipped417.98s (copy includes START_MCP.bat; live .agent_state untouched); P04 focused183passed2skipped14.43s (two documented symlink-creation skips); registry14passed; fullRuff PASS; fullmypy283 PASS; compileall/diffcheck/main--check PASS |
| hosted | All10SUCCESS exact0414382: CI39862898120 six OS/Python310+312; Platform39862898079 three OS/Python312; Packaging39862898341; 10check-runs total, none skipped |
| contract | fixed-map fake providers Python/TS/PHP; exact import_bound/name_only rows and fixture-declared lines/digests; zero false definite edges; duplicate-helper ambiguity plus anchored page concat==4 unique; missing relation->incomplete (TS import/test, PHP related_test); TS+PHP monorepo emits no cross-language rows; foreign PHP reply rejected without leaking paths; worktreeA excludes B by canonical workspace/paths/digests; unavailable TS provider not spawned during PHP query; broken-syntax bytes preserved; initialize=1/shutdown=1 each; latency/RSS NOT_MEASURED |
| review | Independent review PASS, no blocking findings; earlier findings #1-#6 and vacuous-relation finding RESOLVED; reviewer modified no files |
| hashes | tests/test_multilanguage_context_registry.py 1a34c0f4fbb3c8e22d19ded39c28b20b5c45ff79ab6353c2a5c681c6a71f7ce3 (staged LF blob) |
| state | P04-04 VERIFIED. P04 tasks 4/4 verified; accepted20/29=69.0%, implementation20/29=69.0%; P05liveUX stillunverified |
| next | TASK-P06-01 read-only discover_test_runners with bounded manifests, nearest package root, explicit ambiguity, no package-script execution; no live provider activation |


### EVIDENCE-1 — 2026-10-23 TASK-P06-01 autonomous recovery preflight
| field | evidence |
|---|---|
| identity | Requested checkout; branch feature/coding-agent-evolution; HEAD04143829c323c291fad10085b659ee33ab7eb0a5; DraftPR82 samehead. All10 hosted checks independently queried SUCCESS; P04-04 predecessor retained VERIFIED |
| interrupted_files | Existing untracked project_runners.py SHA2560d13df723e96d097b039d973299db768d2ffbad77464f26f8f4b34dc2724d7d5 and test_multilanguage_affected_tests.py SHA2566720f6ed9d61924fd5ef69a98e731761e4c9e2916110cde81e719785bbd3d63f plus matching changelog entry recovered, not discarded. No historical RED/GREEN evidence assumed |
| preserved | panel SHA2566f682baebf08556a4b5bab2d9d9e34f541ccf6ebbee13abda611a3c9b67bd3d4; uv.lock SHA256e9bbee1693a12ebdde82d62ef98722036e5b14db754842bd6c865375594e56aa; live state untouched; master plan external only |
| state | P06-01 IN_PROGRESS; authorized implementation, selective branch push/DraftPR CI only; no merge/main/release/tag |
| next | Baseline existing interrupted implementation in isolated temporary state; inspect remaining source and runner-boundary regressions before edits |

### EVIDENCE-1 — 2026-10-23 P06-01 recovered baseline and RED/GREEN
| field | evidence |
|---|---|
| baseline | Isolated SETTINGS/temp pytest:43passed1symlinkskip2.41s exit0; no cacheprovider caused cache_dir warning, later runs use temp cache. Focused Ruff PASS; mypy failed Optional[Path] installed_dir at line428 |
| RED | 10new cases failed1.17s exit1: undeclared/missing bin3, installed name1, malformed dependency types4, Composer entry after256 and duplicate packages2. One preliminary harness syntax error corrected before tests; no behavior result inferred |
| GREEN | Strict declared-bin/name/dependency validation, complete bounded installed list and duplicate denial, Optional narrowing; affected+discovery53passed1symlinkskip2.08s exit0. Full static/review gates pending |
| discovery | codebase-memory index_status connection closed; Graphify scoped query found legacy impact/registry/verification path but graph timestamp2026-10-18 lacks new P04/P06 files. Current scoped source used; no rebuild justified for isolated new helper |
| next | Reproduce independent JSON config/PHP entrypoint findings; add byte/count/containment acceptance, fullstatic then frozen independent re-review and exact CI |

### EVIDENCE-1 — 2026-10-23 P06-01 independent findings and recovery
| field | evidence |
|---|---|
| review | Automated independent read-only reviewer reproduced malformed Jest JSON config accepted and binary/oversized vendor/bin PHP accepted. Added6RED regressions, all6failed1.25s; fixed strict JSON parsing and shared-reader bounded PHP-text validation preserving in-workspace vendor symlinks |
| test_harness | Initial oversized-bytes parameter auto-ID produced Windows fixture path errors and truncated output; explicit bounded test IDs resolve harness defect; rerun6RED is decisive. Initial aggregate-budget fixture512 exceeded actual reads so did not fail; corrected cap300 below sum but above each input, then rerun required |
| checks | Full Ruff exit0; full mypy285sourcefiles exit0. First broadened regression128passed2symlinkskips1fixturefailed2.95s; no acceptance claimed |
| count_reconciliation | Exact task-index parser finds43tasks,26VERIFIED,8NOT_STARTED,5BLOCKED,3DEFERRED,1IN_PROGRESS. QuickStart NOT_STARTED9 was inherited off-by-one and corrected to8; totalweight29,verifiedweight20 |
| next | Rerun corrected acceptance/regression; freeze diff for independent final review; then selective checkpoint/exacthead10hosted |

### EVIDENCE-1 — 2026-10-23 P06-01 dependency truncation recovery
| field | evidence |
|---|---|
| prior_checks | Seven-file isolated regression246passed3symlinkskips25.96s exit0; fullRuff/mypy285/compileall/diffcheck exit0; these precede latest dependency fix |
| independent_review | Prior JSON/PHP defects resolved; independent129passed2skips3.10s and stable reviewed hashes. Further reviewer reproduction: valid bounded Python manifest with pytest after dependency10000 returns complete empty runner inventory |
| RED_GREEN_scope | Four end-to-end tests for project dependencies, optional dependencies, PEP735groups and Poetry each RED1.43s. Removed silent list/mapping slices and unnecessary copying; existing per-file256KiB/aggregate4MiB limits bound parsing and traversal, no new limit or abstraction. Final rerun/re-review pending |
| state | IN_PROGRESS; no commit/CI yet, no acceptance claimed |
| next | Final seven-file regression/fullstatic and independent frozen review; selectivecommit only after passing |

### EVIDENCE-1 — 2026-10-23 P06-01 final local regression
| field | evidence |
|---|---|
| RED_GREEN | Six additional Python malformed table/list metadata cases RED6failed1.17s -> strict shared table/list guards; existing PEP735 include-group markers retained; no new package/script execution |
| local_final | After latest edits seven-file isolated regression256passed3symlinkskips24.48s exit0: multilanguage_affected_tests, affected_tests, project_intelligence_models, parallel_verification, background_verification, project_cache, tool_profiles. Full Ruff exit0, full mypy285 exit0, compileall exit0, diffcheck exit0 |
| limitations | Local symlink tests skipped because Windows account lacks creation capability; hostedLinux/macOS required. Real Node/PHP runner execution not part of discovery task. No startup/live state used |
| next | Await independent frozen review; if accepted selectively stage only project_runners.py/test_multilanguage_affected_tests.py/CHANGELOG.md, inspect staged diff and commit/push dedicatedbranch for exacthead10CI |

### EVIDENCE-1 — 2026-10-23 TASK-P06-01 tested Draft checkpoint
| field | evidence |
|---|---|
| identity | Commit a87a6c410081f9f858d8f2925cd8601eddbcc81e; feat(testing): discover bounded multilingual test runners; selectively3files1401insert; pushed ONLY feature/coding-agent-evolution; existing DraftPR82. No main/merge/release/tags |
| independent | Automated read-only completion_verifier PASS;139passed2symlinkskips3.10s, scoped Ruff/mypy, source/changelog review, hashes unchanged. Earlier concrete findings resolved. This is independent automated review, not human/operator approval |
| local | Latest seven-file regression256passed3symlinkskips24.48s; fullRuff/fullmypy285/compileall/diffcheck exit0. Staged diff detected trailing blank at new-test EOF; removed only final extra newline, restaged, stageddiffcheck exit0 and final focused139passed2skips3.40s |
| hashes | Staged source b9faddf7f65a531e56c0c63f5ee893b7eeac594da42299a1ba6e7aac483dc354; test8b452cae965204fa5ca1c378ec40d7f1c2d14fa2b1cc2aa9567499d41ceaf343; changelog9973d60588c5b4dd4d335f063ff7233cd91de5f52332f21e52ce0ba78c08586d; stageddiff1bc872a7fe31b1c0a001f9124e5fa78e6df1bb620dad75cfdb2ca64664ea1e32 |
| preserved | Protected panel/uv raw digests unchanged from preflight; live state untouched; no tool schema or dispatch wiring added |
| progress_reconciliation | implementation22/29 includes accepted20 + alreadyimplemented P05-02 blockedliveUX1 + P06-01 pendinghosted1. Prior quick percentage omitted alreadyimplemented P05-02 although dashboard counted it; corrected derivedmetric, no acceptance invented |
| state | IMPLEMENTED_UNVERIFIED until all10 required exacthead hosted checks succeed; P06-02 remains NOT_STARTED |
| next | Poll sameSHA CI6/Platform3/Packaging1; independent read-only P06-02 source reconciliation may continue during CI |

### EVIDENCE-1 — 2026-10-23 TASK-P06-01 exact hosted acceptance and P06-02 entry
| field | evidence |
|---|---|
| identity | ExactPRhead a87a6c410081f9f858d8f2925cd8601eddbcc81e; DraftPR82 remainsdraft; no main/merge/release/tag |
| hosted | All10 required check-runs COMPLETED SUCCESS, none skipped: CI40002509236 sixWindows/Linux/macOS Python310+312; Platform40002509261 threeOS Python312; Packaging40002509212. All runs pull_request subject exacta87a6c4 |
| retained_local | Afterlastrelevantedit256passed3localsymlinkskips;139passed2skips finalEOFcheck; fullRuff/mypy285/compileall/stageddiffcheck PASS; independent139passed2skips and sourcePASS |
| state | P06-01 VERIFIED; weightedaccepted21/29=72.4%; implemented22/29=75.9% including blockedP05staticcode. P06-02 IN_PROGRESS because dependencyverified, no taskcodewritten yet |
| source_reconciliation | Existing sync selection only Python can emit decisionnone forJS/PHP; renameoldpath not included in impactgraph. No supported sync cache-only semantic snapshot API: async acontext_result miss invokes LSP builder and can spawn; privatecache bypass would break TTL/provider/session/doc/fingerprint guards. Use existing P04 inventory/worktree/fingerprint evidence and explicit semantic coverage fallback, no parallel cache/API |
| next | P06-02 RED cases perblueprint, normalizeallchangedpaths+renames, conservativepackagejoin, globalbounds/provenance, additive registryparam; current verification consumers retainPythondefault untilP06-04 |

### EVIDENCE-1 — 2026-10-23 P06-02 RED and first integration recovery
| field | evidence |
|---|---|
| baseline_hashes | impactd5f9947c2a5f45c1825e3429146d69931f69979a146d7444842f44d175354597; registrya8c2414b251254bfe181c89e9d2b711ca4c47ed704e33c14c561f6083ebc3092; test_affected6b21372d52efe2262fd3fa87b7d184eeeb59f893c1334eb753b8055ac1046766; sourceunchanged from accepted baseline beforetask |
| RED | Twelve new selection tests failed1.22s on missing additive backend/API/evidence: default/autoJS, deletedPHP, renameold/newroots, dynamiccrosspackage, unknownrunner, stalesnapshot, docsonly, outsidepath2, globalcap, deletedpathfingerprint |
| first_GREEN_attempt | Firstimplementation85passed1symlinkskip1legacyfailure3.28s: existing changed_paths field must retain renamedestinationonly. Added touched_paths for allold/newaffectedpaths while preserving legacychanged_paths. Ruff1overlongline and mypy2dict-inference errors fixed; rerun pending |
| scope | Existing Python reverse graph reused; P04 inventory/fingerprint plus P06runner metadata; no private semanticcache access, no LSP activation or new persistentstate. Public affected_tests gets opt-in test_backend, existing verification remains Pythondefault untilP06-04 |
| next | Rerun focused checks then additional public/default/denial/fingerprint cases; review new selection and current consumers; fullregression/static/independentreview/exactCI beforeacceptance |

### EVIDENCE-1 — 2026-10-23 P06-02 focused GREEN and boundary regressions
| field | evidence |
|---|---|
| GREEN | Focused discovery+selection86passed1symlinkskip3.17s; scopedRuff/mypy3filesPASS. Additional omitted-default-foreign, PHPXML/excludedsourcefingerprint and realSDK MCP no-Popen tests89passed1skip3.52s |
| new_RED | Deleted Python test was selected as existingtest; rootPythonconfig omitted nestedNodepackage inauto. Both reproduced2failed1.26s; now omit deletedrunnabletest/fullfallback and broaden configaffectedpackages conservatively; rerun pending |
| compatibility | Renamedestination-only changed_paths contract preserved; touched_paths adds old/new forselection/fingerprint. Newdocs describe unknowncoverage and read-onlyauto plans; verification runner integration remainsP06-04 scope |
| state | P06-02 IN_PROGRESS; independent review underway, no taskcommit/push yet |
| next | Six-file relevant regression/static then address reproduced independent findings; fullisolatedsuite atM06a closure and exacthead hosted |

### EVIDENCE-1 — 2026-10-23 P06-02 independent review repairs
| field | evidence |
|---|---|
| prior_GREEN | Six-file regression206passed2symlinkskips17.98s exit0 before final3findings |
| independent_RED | Reviewer reproduced cross-package selected Python-test owner omitted, nestedComposer installedmetadata omitted fromfingerprint, depthcutoff reports complete despiteknown reverseedges. Owner regressions all3RED1.15s |
| fixes | Include selected-test owners and broaden incompletePythonplans; mark pendingedges at maxdepth as dependency_depth_truncated and requirefallback; fix shared runner installed-evidence producer toemit workspace-relative nestedComposerpath (nativeNode evidence alreadyworkspace-relative). P06-01source touched narrowly under P06-02 rootcause, accepted historicalcheckpoint retained |
| state | IN_PROGRESS, focused/fullstatic rerun pending; no dependent runnerexecution code |
| next | Final regression/static and frozenindependentreview; isolated fullsuite/startup/health forM06a; selectivecommit/push/exactheadCI |

### EVIDENCE-1 — 2026-10-23 P06-02 late stale-scope recovery
| field | evidence |
|---|---|
| prior_GREEN | Six-file209passed2skips19.81s; independent159passed2skips4.30s; static285PASS beforelatescopefix |
| independent_RED_GREEN | Python sibling gains import duringfinalinventoryscan: stale correctlydetected butonlyoriginalpackagefullsuite returned. Reproduced1RED1.01s; movepackageplanning afterfinalsnapshotchecks and invalidate scope across initial+final package roots, explicitrootfallback forstale/incompleteinventory; focused95passed1skip3.57s |
| additional_RED | Crosspackagefallback omitted rootpytest.ini runner withoutmanifest although discovery foundit; reproducedmanifestless-root regression then includeall discoveredrunnerroots inbroadened scope; finalrerun pending |
| isolation_setup | Fullsuitecopy preflight safelyrejected tracked publicmemory README/example under overbroad runtime-dir guard; inspectedexacttrackednames and allowed only memory/README.md and memory/sample_project.example.json. Live memory/state/secrets excluded; no masterplan copied. Earliercopysnapshot predateslatescopefix and wasNOTexecuted, mustrefreshbeforefullsuite |
| state | IN_PROGRESS; finalindependentreview/fullisolatedsuite/CI unresolved |
| next | Freeze aftercurrentregression, finalreview, refreshisolated374filecopy andrunfullsuite+isolatedstartup/health thencheckpoint/exactCI |

### EVIDENCE-1 — 2026-10-23 P06-02 frozen local review and full-suite execution
| field | evidence |
|---|---|
| local | Lastsixfile211passed2symlinkskips20.16s; Gitrefresolution reorderedbeforefilesnapshots andbase pinned fordiff; fullRuff/mypy285/compile/diffPASS afterward |
| independent | Final automated read-only review PASS;161passed2skips4.52s, scopedRuff/mypy4files/diffPASS; all6hashes stable andearlierfindings resolved. No human/operatorapproval implied |
| frozen_hashes | impacta6e066d56d3d84a4814de22f215fb1304e5428e4d299a42a71c54266422e52da; registry8501dcaac46f8c5c47dc2b94a646add9f14b4a09f813a9e501eeca5cab79a3d4; projectrunners02e6335cfe8086b2e508717d5d6392fe8f3f1a2f84d20f4d900657fcce8e64d6; testsf204d023091cd0e4f8f6c631c8589e2b946baed86dcbf263a681cafa525bb181; docs704263afa843fb72ed5557e51658592f0a2f33c90f8b5d8422bbc3408b6e30a1; changelogb6bcffe7a083e0e892cecfa94d723bb4c36e7e1e5b6ed6cdd3afef2ee8a26a67 |
| isolation | Disposablecopy LOCAL_PATH_REDACTED excludeslive state/secrets/uv/masterplans. Sixsource/copyhashesmatch; diff4898b87d329f33148ba83b64c9d1e2682eb5ddc15dd70fac7c2f64f0353f8ea7 includesprotectedpanelunchanged |
| execution_caveat | Copyrefreshcommand yielded session4627 and fullsuite60221 was started beforeexplicitcopycompletionwait; copylatercompleted andhashesmatch but initialimportorderingunproven. Currentfullrun is diagnostic only; finalfullgate must rerun aftercopycompletion, withoutsourceedits. No invented currentfullPASS |
| next | Collectdiagnosticfullresult, thenfreshfullsuite inalreadycompletedisolatedcopy plusstartup/health, selectivecommit/push/exact10hosted; no dependentrunnercodeuntilacceptance |

### EVIDENCE-1 — 2026-10-23 TASK-P06-02 tested Draft checkpoint
| field | evidence |
|---|---|
| identity | c3949fae61435af9ba376f745c057f4b2859048a; feat(testing): plan conservative multilingual affected tests; selective6files536insert14delete; dedicatedbranchpushonly; DraftPR82 retained |
| local_review | Lastsixfileregression211passed2skips20.16s beforeGitqueryreorder; final independent161passed2skips4.52s includesaffected/sourcechecks afterreorder, all6 reviewed/stagedhashes unchanged. FullRuff/mypy285/compileall/diff/stageddiffPASS. StageddiffSHAa61d0df7f84e9144037797caed513595d1b9d06f9c1794308d4229d4bb3a8594 |
| contract | PythonflatAPI preserved; opt-inauto packageplans; foreignunknown bothmodes; allold/new touchedpaths fenced, missing/deleted/config/dynamic/crosspackage/depth/stale/runneruncertainty=>fullfallback; globalcap; no semanticcache/LSP/projectexecution. NestedComposer evidence producerfixedonce. All priorreviewfindings reproduced/fixed |
| preservation | Panel/uv rawdigests unchanged; onlysix reviewedtaskfiles staged; no liveDB/state, no main/merge/release/tags/masterplancopy |
| fullgate | Localdisposable fullrun60221 stilldiagnostic duecopywait ordering caveat; matchingexactSHA hostedfullsuites will be authoritative fullregression gate, avoiding redundantlocalfullrun oncehostedpasses. No fullPASS claimedyet; startup/health inisolatedcopy pending |
| state | IMPLEMENTED_UNVERIFIED; accepted21/29=72.4%,implemented23/29=79.3%; P06-03 NOT_STARTED pending02hosted |
| next | Poll exactc3949fa all10checks; collectdiagnosticresult; isolatedstartup/health; onacceptance recordrunIDs/results then03RED boundedapprovedruntime/report/cleanup/argv cases |

### EVIDENCE-1 — 2026-10-23 P06-02 diagnostic harness failure isolated
| field | evidence |
|---|---|
| diagnostic_result | Copyrefresh-overlap run60221 exit1:2067passed27skips2failed420.45s. Failures are exactly late-added stalePythonpackagescope and manifestlessrootrunner regressions; behavior matches oldimpact imported beforecopyrefreshfinished. Currentcopied/sourcefiles unchanged andmatchingcurrent6hashes |
| recovery | Fresh Pythonprocess in same completedcopy executes both failures:2passed0.84s exit0. No sourcechange needed; this supports copy/importordering cause, olddiagnosticrun notaccepted |
| final_isolation | Created new disposablecopy LOCAL_PATH_REDACTED from exactc3949fa; copycommand exit0 and all6hashesvalidated BEFORE launching freshfullsuite in nexttoolcall. No live state/secrets or masterplan copied |
| next | Collect freshfullresult, isolatedstartup/health, exactc3949fa10hosted. P06-02 remainsIMPLEMENTED_UNVERIFIED; no blindrerun orweakenedtest |

### EVIDENCE-1 — 2026-10-23 P06-02 Python3.10 hosted failure diagnosis
| field | evidence |
|---|---|
| failure | Exactc3949fa CI40007151747 Linux310 job120597847430 pytest failed: stalePython-scope test demanded stale_snapshot but actualmissing_inputs correctlycontains source_fingerprint_unavailable/toml_parser_unavailable. 1failed2063passed32skips343.81s; no runtime dispatch or falsecomplete |
| reproduction | Samefailure locally with only project_types._toml and project_runners._toml setNone:1RED0.19s. Source on310 intentionally cannot produce fingerprint, so exactstale detection is not claimed; conservative fullroot/package fallback alreadycorrect |
| fix | Test-only param host-default/without-toml, assert exactreason appropriate to parserpresence plus completefalse/fingerprintNone/allknownpackagefullsuites; no parserinstall, no productionguard weakened. Focused/static rerun pending |
| log_access | gh runview --job --log-failed refused whileruninprogress; directread-only jobs/logs API returned decisivejobtrace. No retry requested |
| state | P06-02 stillIMPLEMENTED_UNVERIFIED; currenttestdiff uncommitted; currentfreshfullcopy15208 uses pre-fixturefix frozenproduction and remains useful for unchangedproduction, willreport precise coverage |
| next | GREEN bothparsermodes; independentfixture re-review; selectivetest-onlyfixcommit/push thenexactnewSHA all10CI; collectfreshlocalfull andsmokes without touchinglive state |

### EVIDENCE-1 — 2026-10-23 TASK-P06-02 Python310 fixture checkpoint and full local gate
| field | evidence |
|---|---|
| identity | Test-onlycommit328e76d550e4f4456111229733941cefffc9bd9f, selective1file15insert2delete, dedicatedbranchpushonly. Stageddiff2cb56daeb1f0ed3829d0e33a141564fcfec1a9327c85e36f6c30154864c01891; test7074563d574f69c0f3fe4d01175abb2396058882fd12e13935c2db126c766862 |
| local | Bothparsermodes covered; focused162passed2skips4.72s; fullRuff/mypy285/compile/diffPASS. Independent2case re-reviewPASS with productionhashesunchanged; old3.12staleassertion retained, 310honestunavailable guardrequired |
| full | Freshcompletedcopy15208 fullsuite2069passed27skips431.08s exit0, exactproductionc3949fa (unchanged in328e76d). This supersedes diagnosticcopy-overlap failure; latesttest-onlyparam separatelyfocusedverified and exactnewheadhostedfull stillrequired |
| startup | Freshcopy defaultflags explicitoff, main.py--check exit0, oktrue/version0.8.0/tool_count151/unique_tool_namestrue; livecheckoutstate untouched. Healthnext |
| hosted_prior | c3949fa all7othergatesSUCCESS, threePython310pytestjobs failed same invalidfixtureexpectation; historicalfailuresretained, no rerunwithoutfix. Newexact328e76d all10pending |
| state | IMPLEMENTED_UNVERIFIED, no dependent03code; productionhashes fromfinalreview retained, userpanel/uvunchanged |
| next | Completeisolatedhealth; all10sameSHA328e76d thenP06-03. Read-only CLIreferences checked Jest29.7/Vitest3/PHPUnit11.5/Pestoptimization beforefutureargvimplementation; no runnerinstall oractivation |

### EVIDENCE-1 — 2026-10-23 P06-02 isolated health complete
| field | evidence |
|---|---|
| health | Freshcompletedcopy scripts.health_check--json exit0/oktrue; import,mcp_startup,tool_registration,filesystem,terminal alltrue; toolcount151. Extensions/persistent/DAG off fordefaultnative smoke; no livecheckoutstateused |
| hosted_identity | Newexact328e76d pull_request CI40008686850, Platform40008686913 pending; Packaging40008686870SUCCESS; no missinggate treatedaspass |
| housekeeping | PRbodyupdate initially exactparagraph-anchor miss due CRLF frompreviousWindows write; normalize lineendings in temporarybody and preserve paragraphscope, no source/runtimechange |
| next | Exactheadhostedall10 acceptance thenmark02VERIFIED and03IN_PROGRESS; current source taskchanges committed, onlyprotectedpanel/uvdirty |

### EVIDENCE-1 — 2026-10-23 TASK-P06-02 exact hosted acceptance / M06a complete
| field | evidence |
|---|---|
| identity | ExactPRhead328e76d550e4f4456111229733941cefffc9bd9f; branchfeature/coding-agent-evolution; DraftPR82; no main/merge/release/tags |
| hosted | All10 COMPLETED SUCCESS, nonefailed/pending/skipped: CI40008686850 sixOS/Python310+312; Platform40008686913 threeOS/Python312; Packaging40008686870 deterministic smoke. Native310fixturebehavior nowaccepted onallthreeOS |
| local_retained | Freshisolatedunchangedproductionfull2069passed27skips431.08s; subsequenttest-onlyfixfocused162passed2skips4.72s plusindependent2casePASS; fullRuff/mypy285/compile/diffPASS; isolatedstartup151unique andhealth5/5PASS. Earliercopy-overlapdiagnostic andoldSHA310failuresremainrecorded |
| state | P06-02 VERIFIED; M06a complete; accepted22/29=75.9%,implemented23/29=79.3% (blockedP05staticcode1). P06-03 IN_PROGRESS withverifieddependency; no03codeyet |
| preparation | Read-onlyarchitectureaudit: no general runtimeapprovalregistry; LSPconfigtrust isLSP-only, do not reuse it. Existing authorizednative test invocation may bindimmutable runtime path/hash fromoperatorenvironment outsideworkspace; projectmetadata cannotapprove runtime. Keep discovery separate from executionreference; revalidatecurrentboundeddiscovery/root/version/config/bin and originalpins beforeeachspawn. P06-04 persistsbounded runtimeapprovalrefseparatelyfromrepofingerprint |
| CLI_sources | Jest29.7 https://jestjs.io/docs/29.7/cli supportsjson/outputFile/runInBand/runTestsByPath; Vitest3 https://v3.vitest.dev/guide/cli supportsrun/json/outputFile/maxWorkers/no-file-parallelism; PHPUnit11.5 https://docs.phpunit.de/en/11.5/textui.html log-junit; Pest https://pestphp.com/docs/optimizing-tests explicitparalleloptin. No realrunnerinstallation/compatibility claim; fixedversioncapfixturesrequired |
| next | P06-03 RED/runtimepins/argv/privateboundedreport/unknownnocases/nativecleanup; existingrun_bounded ownsprocess/capturedeadlineandfinallycleanup. Currentprotectedpanel/uv unchanged |

### EVIDENCE-1 — TASK-P06-03 RED / boundary preflight (2026-10-23)
| field | evidence |
|---|---|
| state | IN_PROGRESS; P06-02 exact head 328e76d550e4f4456111229733941cefffc9bd9f all ten hosted gates SUCCESS, no merge/main push |
| RED | Isolated tests/test_multilanguage_runners.py: 4 failed in 0.96s, expected missing bind_runner_runtime API; implementation not yet accepted |
| boundary | Existing managed_path_claims supplies or validates coordination/package-write authority, native process ownership and DAG dispatch barrier; keep synchronous run_project_tests and reuse run_bounded, no executor API change |
| independent discovery | Current graph omits registry caller; source confirms direct verifier lacks own claims, worker job currently owns PROJECT_ROOT rather than external request repo. P06-04 must provision actual target scope without clearing inherited capability |
| scope | Worker owns project_runners.py, runners.py, new test_multilanguage_runners.py only; root sole plan/Git writer; protected panel and uv.lock preserved |
| EXACT_NEXT_ACTION | Complete P06-03 argv/runtime/version/report/cleanup guards and RED-to-GREEN evidence, focused regression/static checks, independent review and exact-head ten-gate CI before P06-04 |


### EVIDENCE-1 — TASK-P06-03 first GREEN checkpoint (2026-10-23)
| field | evidence |
|---|---|
| state | IN_PROGRESS, HEAD remains 328e76d550e4f4456111229733941cefffc9bd9f; no completion claim |
| tests | Worker isolated tests/test_multilanguage_runners.py: 35 passed in 4.41s. Real Windows root/child/grandchild timeout cleanup, stale manifest/config/entrypoint/runtime/workspace zero dispatch, unsupported versions, missing/zero-test reports, symlink denial, secret sanitation and cancellation no-retry |
| pending | PHP direct/per-file/XML, explicit unittest and Python venv compatibility, inherited capability denial, runtime spoof denial; Ruff/mypy/regression/independent review/exact-hosted CI not yet accepted |
| EXACT_NEXT_ACTION | Finish remaining runner acceptance tests and bounded implementation; root reviews frozen diff and independent verifier reruns relevant tests before selective branch-only commit/push |


### EVIDENCE-1 — TASK-P06-03 platform fixture diagnosis (2026-10-23)
| field | evidence |
|---|---|
| state | IN_PROGRESS, implementation still unaccepted |
| failure | Expanded runner suite: 48 passed, 1 failed; Windows interpreter-symlink setup raised WinError 1314 (privilege unavailable), not a production assertion failure |
| recovery | POSIX venv interpreter-symlink behavior must run on hosted Linux/macOS. Move real report symlink creation before mocked dispatch to avoid false positive from normalized writer failure; Windows retains synthetic reparse denial and real hardlink denial |
| pending | Hosted native symlink/venv evidence, final GREEN/regression/static/review/ten gates |
| EXACT_NEXT_ACTION | Correct platform fixtures without weakening production nofollow/pin checks; finish focused tests and independent review, then branch-only exact-SHA CI |


### EVIDENCE-1 — TASK-P06-03 local acceptance checkpoint (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| state | IMPLEMENTED_UNVERIFIED on HEAD 328e76d550e4f4456111229733941cefffc9bd9f; no task commit/push yet |
| files | tools/testing/project_runners.py, tools/testing/runners.py, tests/test_multilanguage_runners.py, docs/tooling.md, CHANGELOG.md |
| frozen source SHA256 | project_runners 17fb72a3bc8a8376991c497156b1310d708cb1cb7a25d70add0dbdeba195b07a; runners a2151652cf6a5cc85fdf4e9bdbf06571d729d67bf4aee73c81676a90d3e0d33c; tests 1a2ba54313198f8aabb88da86de71b78ca8dfba0b88ffa4c1b0d0e36639e1d36 |
| executed checks | .venv/Scripts/python.exe temp run_tests.py tests/test_multilanguage_runners.py tests/test_executor.py tests/test_multilanguage_affected_tests.py tests/test_affected_tests.py tests/test_parallel_verification.py tests/test_background_verification.py --tb=short: 212 passed, 2 skipped in20.32s exit0; full Ruff exit0; full mypy 286 source files exit0; compileall core tools scripts tests exit0; diff whitespace exit0 |
| skip limits | Windows lacks native symlink privilege; POSIX interpreter-symlink and PHP symlink discovery require corresponding Linux/macOS jobs. Real PHP/Jest/Vitest integration not measured, no installs |
| invariants | python_for untouched; original native runners unchanged; fixed semver capabilities; minimal env values and bounded counters; private report nofollow/hardlink/root checks; runtime/workspace/entrypoint pin revalidation; no package scripts, no retry; protected panel and uv.lock hashes unchanged |
| independent review | p06_discovery_review requested; pending, never impersonated. Worker usage-limit error arrived after frozen implementation/evidence; root continued validation |
| MCP diagnosis user request | Live Ali Computer Agent server_health returned tunnel_client_not_seen/404, last seen>300s. No supervisor/tunnel listeners; launcher PID26592 title Select LOCAL_PATH_REDACTED with bootstrap15572/20988 and doctor2836/12292 descendants waiting. Console text-selection pause is observed cause; user instructed Esc. No live state/config/code modified for diagnosis |
| EXACT_NEXT_ACTION | Independent review then selective five-file commit/push to existing branch and exact ten-gate CI. P06-04 remains ineligible until P06-03 VERIFIED |


### EVIDENCE-1 — TASK-P06-03 independent review FAIL / safe recovery (2026-10-24)
| field | evidence |
|---|---|
| state | IMPLEMENTED_UNVERIFIED, not VERIFIED; no commit/push yet |
| independent findings | runner_boundary_audit reproduced nested JUnit suite counters contradictory to testcase data returning oktrue. completion_verifier independently reproduced Vitest3.2.4 pending/running assertion returning oktrue/required_unknownfalse; legitimate Jest pending skips must remain supported |
| RED / GREEN | Root new nested-inconsistent regression: 1 failed54deselected1.11s; after per-suite counter validation, PHP/nested checks11passed45deselected1.46s. Vitest/Jest terminal-status regression: 1 failed2passed56deselected1.10s; failure is genuine Vitest pending falsegreen, fix pending |
| review scope | Both independent reviewers found no additional concrete pin/scope/argv/env/report-link/cleanup defect in original artifact; original overall independent suite137pass2skip8.49s. These are not acceptance for latest parser edits |
| root checks before parser changes | Isolated-copy main--check151tools and health all5checksPASS; fullRuff/mypy286/compileall and focused212pass2skip were prior artifact only and must rerun affected checks |
| EXACT_NEXT_ACTION | Validate available assertion statuses with runner-specific pending semantics and counter consistency; add malformed-report coverage, rerun affected/static/smoke, request new independent exact-hash review, then branch-only commit/CI |


### EVIDENCE-1 — TASK-P06-03 tested branch checkpoint / CI pending (2026-10-24)
| field | evidence |
|---|---|
| state | IMPLEMENTED_UNVERIFIED, exact commit4d93417964efefa29cdb765661e7a17c9b15e763 feat(testing): execute pinned package test runners; selectively staged five reviewed files; pushed only feature/coding-agent-evolution; DraftPR82 preserved |
| working diff SHA256 | 54d4c5ac8217d767426cc46841670afb1a0e3f7ada6b934ea501e42fb4cc4aa2 (tracked binarydiff plus framed new test file) |
| file SHA256 | project_runners17fb72a3bc8a8376991c497156b1310d708cb1cb7a25d70add0dbdeba195b07a; runnersd389a78b9191efcd6672ef4055e2b28720c7f8fbf686388a3562b1a8b2f5bcb4; tests8ef248828f2fcc65cf2ebf2b2e81a6a974a7484d53b829c5fd311f8dd8fbde8a; docsd7302943be82c91cbfd092852832712045cc80387d1f6731f00c4d2b4be243f8; changelog464f84c56d7006ac24a41f1d86499e004b50fca5a4ead9e0d773f004bb8f3816 |
| executed final local | Isolated6file regression221passed2skipped19.02s exit0; fullRuff exit0; fullmypy286files exit0; compileall/diffcheck exit0. One E501 line149>140 after parser fix was corrected by wrapping expression, then Ruff rerunPASS |
| independent review | completion_verifier146passed2skipped8.15s, scopedRuff/mypy/diffPASS; before/after3hashesmatched; both falsegreen findings resolved. architecture_auditor isolatednestedJUnit originaldenied, validnested2casescountedonce; no edits; real foreignrunner integration not claimed |
| hosted exact head | Three pull_request runs IN_PROGRESS: CI40059286533, Platform40059286437, Packaging40059286553, all head4d93417964efefa29cdb765661e7a17c9b15e763; ten checks not yet accepted |
| protected state | gitstatus contains only unrelated modifiedtools/panel/index.html and untrackeduv.lock; hashesunchanged; no master plan/state/secrets staged, no main/merge/release/tag action |
| EXACT_NEXT_ACTION | Collect exact ten gates and POSIX evidence; on successmarkP06-03VERIFIED andP06-04IN_PROGRESS, implement shared verifier/runtime guards and proper background targetscope from prepared source evidence |


### EVIDENCE-1 — TASK-P06-03 hosted fixture failure / recovery (2026-10-24)
| field | evidence |
|---|---|
| state | IMPLEMENTED_UNVERIFIED; exact4d93417 not accepted |
| hosted failure | CI40059286533/LinuxPython3.12/job120769077767: test_report_hardlink_denied failed assert created; result correctlyokfalse/required_unknowntrue, but mockedwriter did not reachcreatedappend. Fulljob1failed2138passed21skipped244.65s. Platform3OS andPackaging alreadySUCCESS, remainingCIjobs pending atsnapshot |
| diagnosis | Fixture hardlink target lives checkouttmp while report lives systemtmp; samefilesystem was not guaranteed. Original providerexception intentionally sanitized, so exact OS errno unavailable; crossfilesystem cause is inference pending correctedhostedrun |
| safe correction | Test-only monkeypatch tempfile.tempdir to ownedtmp_path ensures target/report sharefilesystem while remaining distinct files/private reportroot. No runtime bypass or parser/runtime changes. Focusedlocal hardlink1passed62deselected1.01s |
| EXACT_NEXT_ACTION | Independent tiny-fixture review, run meaningful affected/static checks, selectivelycommit onlytest and push existingbranch; collect allten exactnewheadgates. Do not retry oldunchangedartifact blindly |


### EVIDENCE-1 — TASK-P06-03 corrected fixture checkpoint / exact CI pending (2026-10-24)
| field | evidence |
|---|---|
| state | IMPLEMENTED_UNVERIFIED exactaf1236ca2795956ff138ea9f1921b32e86836d70 test(testing): keep hardlink fixtures on one filesystem; onefile2lines selectivecommit pushed existingbranchonly |
| local correction | Runner suite62passed1WindowsPOSIXskip6.39s exit0; fullRuff/fullmypy286/diffcheck exit0. Production code unchanged from independently reviewed4d93417 |
| independent correction review | completion_verifier1passed0.83s, tempfile.tempdir restoration explicitlyverified; guards/assertions unchanged, sourcehashesunchanged; originalLinuxerrno remainsinference |
| corrected test SHA256 | eb3576fb0f755291f843d7953292dc30d626a019d81c76c13250177ac9e2b938 |
| hosted exact head | CI40059801632, Platform40059801796, Packaging40059801562 IN_PROGRESS, all pull_request/head af1236ca2795956ff138ea9f1921b32e86836d70; ten gates not accepted yet |
| protected | gitstatus only unrelatedpaneldiff anduv.lock; master remains externalsolecopy; no live state/config/DB changes for MCP diagnosis |
| EXACT_NEXT_ACTION | Await corrected ten exact-SHAgates. P06-04 read-only prepcomplete: verifier worker owns verification.py/newtest; root owns shared owner-aware run_native_operation inexistingrecovery, native runnerwrapper, registry/background/worker/workflow integration. Internal runner_specs tuple pins (None directbinding vs emptytuple noapprovedruntime) persisted separately from snapshot input paths; no edits until03accepted |


### EVIDENCE-1 — TASK-P06-03 VERIFIED / TASK-P06-04 IN_PROGRESS (2026-10-24)
| field | evidence |
|---|---|
| accepted03 | af1236ca2795956ff138ea9f1921b32e86836d70 exactPRhead; tenchecksSUCCESS, nofailed/pending/skippedgate. CI40059801632 sixOS/Python3.10/3.12, Platform40059801796 threeOS, Packaging40059801562. CorrectedPOSIXhardlink fixture/venv interpreter symlink now covered by actual Linux/macOS fullCI; localWindows skip remains honest |
| evidence | Production local221pass2skip, independent146pass2skip + fixedtest62pass1skip/independent1pass, fullRuff/mypy286/compileall, isolatedstartup/health151 retained; no realforeignrunner install/integration claimed. Read-onlypreparation graph confirmedworker/workflow/watch; registry/currentaffectedpaths via sourcefallback because graph omitscaller |
| task04 write ownership | worker verification.py + newtests/test_multilanguage_verification.py only. Root registry/background/verification_worker/workflow_actions and necessaryexisting sharedboundary recovery.py/tooling.py/runners.py + workflowregistry/executor threadbindings, relevantexistingtests/docs/changelog. Root soleplan/Gitwriter; no duplicate state/journal/engine, strong independent review beforeacceptance |
| audited04 root causes | Directverifier lacks own lifecycle/resource admission; background jobownsPROJECT_ROOT ratherthan targetrepo; globalanalyzercapacityoptional; queue excludedfrombudget. Existing ordinaryjournal collapses unknownexecutedtest to completedreturned_error; use existingmark_uncertain, preservefinishno-overwrite guard, share monotonic dispatchsignal through explicitownedthreadbinding; preserveunknownafterstoragefailure, neverfinalizeknownfailure asfallback |
| integration decisions | run_native_operation(operation,target,call) reuseslive sameowner admission or createsexistinglifecycle/journal scope. Original runtime RunnerSpec pins internal tuple(None directbind, empty noapprovedpins); boundedsnapshotpaths separatefromchangedpaths. Knownfullpackages resolve only semantic/crosspackage-selection uncertainty with stablecompleteinventory and reportvalidfullsuite; unknown/stale/bounded/unavailable remainunknown. Workflow executedunknown raises existingSideEffectUncertain; WorkflowStore remains authority |
| EXACT_NEXT_ACTION | RED04 cases first, implement only auditedboundaries/callerforwarding; no edits to liveDB/secrets/state/protectedpanel/uv.lock, no mainpush/merge/tag/release, persistentLSP/DAG defaultsclosed |


### EVIDENCE-1 — TASK-P06-04 RED / first shared-boundary GREEN (2026-10-24)
| field | evidence |
|---|---|
| state | IN_PROGRESS onHEADaf1236ca2795956ff138ea9f1921b32e86836d70, concurrentboundedfileownership; no04commit |
| accepted03 fullmatrix | exactCI40059801632 completedSUCCESS: Linux3.12 2139pass21skip246.25s; Linux3.10 2128pass32skip347.74s; macOS3.12 2139pass21skip342.63s; macOS3.10 2128pass32skip435.33s; Windows3.12 2153pass7skip382.12s; Windows3.10 2142pass18skip323.57s. Platform3OS+PackagingSUCCESS too |
| RED04 | verifierworker3failed0.89s missingtest_backend; integrationworker6targetfailures defaultpins/cwd/worker/workflowforwarding, then7targetGREEN beforeintermediateimports. Rootrecovery8failed1.85s missingguard/markAPI and falseknowncompletion settlement |
| root first GREEN | Recoverytargeted8passed38deselected1.52s: returnedexecutedunknown=>uncertain, coverageonlyunknown=>failed, successknown, RuntimeError/CancelledError postdispatchuncertain, explicitboundthreadpropagation withoutfinishingparent, unboundcopiedscope denied, uncertainwritefailure leavesbeginpending/restartuncertain |
| sharedboundary implementation | ExistingScope adds per-attempt threading.Event + explicitparentlink; marker requiresliveowner and propagatesmonotonically toancestors beforedispatch; boundthreadcontextfencesendedparents. run_native_operation reusesadmission withownattemptscope orusesexistinglifecycle/journal. Existingfinish_returned specialcasesonlyverify_changes/execution_uncertain; nooverwriteuncertainguardpreserved. compact_errors exceptionalsettlement aftertestdispatch marksuncertain, neverfallbackknownfailure onstorageerror |
| ownership update | Added integrationworker owns background.py/registry.py/verification_worker.py/workflow_actions.py + their3existingtests; verifierworker owns verification.py/newmultilangverificationtest + test_parallel_verification.py negativebudgetexpectation update; root owns recovery/tooling/runners/workflowthreadtransfer+guardtests/docs/changelog. Allworkersnotalone/no planGitwrites/delegation |
| pending | Rootnative runnerwrapper/uncertainflags, lifecycle/drain/cancel/storage/admittedthread tests, integration/fingerprint/pin/stageGREEN, meaningful independentstrongreview, fullrelevant/fullCI. Intermediateimports mayfail whileAPIsland; notacceptance |
| EXACT_NEXT_ACTION | Finish04auditedboundaries and tests, thenreconcileallcallers; no native defaultsactivation/liveDBchanges/extraengine, no03restart |



### EVIDENCE-1 — TASK-P06-04 resumed boundary corrections (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | af1236ca2795956ff138ea9f1921b32e86836d70 / feature/coding-agent-evolution |
| state | IN_PROGRESS; initial HEAD/project_runners/protected hashes matched predecessor; no task05/P08/Doctor implementation started |
| discovery | Current codebase-memory root/index checked; exact verify_changed_repository query confirms worker/workflow/watch/registry; critical paths verified against source |
| initial_checks | Required5files207passed66.82s; extra8files194passed1POSIXskip44.34s; fullRuffPASS/fullmypy287PASS before final corrections |
| independent_findings | boundary_review confirmed cancellation abandonment and sequential cleanup uncertainty mismatch; integration_review confirmed omitted worker testing flags, callback capacity leak and Python budget admission ordering |
| RED_GREEN | Worker flags2RED->2GREEN; actual workflow repeatedcancellation1RED->1GREEN; callback capacity1RED->1GREEN; workflow caps/cleanup3RED->7GREEN including4previouslypassing |
| corrections | Existing AnyIO nonabandon shield pattern; dispatch-signal exceptional journal settlement; cleanup ToolError converted only after resource quarantine; permit entered before notification; additive worker flags; Python caps before admission |
| pending | Fresh combined regression/fullstatic/startuphealth, independent exacthash rereview, selective commit+push+10exactheadCI; StartupDoctor only after04acceptance |
| protected | panel6f682baebf08556a4b5bab2d9d9e34f541ccf6ebbee13abda611a3c9b67bd3d4; uv.locke9bbee1693a12ebdde82d62ef98722036e5b14db754842bd6c865375594e56aa unchanged |
| file_SHA256 | CHANGELOG.md=018e8ba10da665fc3aa3d181e3a5291793bdb0dab04093103f4f30548b8b7d69; core/recovery.py=326b383a593d5596c486fe6a9a6edc96c55c8b79c4ec56f0e1191ceba548767c; core/tooling.py=2012c3e29d0938823bd27befbdd349ca4f10ec0c2a21d2ced286fa61f404c944; core/workflow_actions.py=818d9588dbd6f913375faa38f6bc02f6dd77e1121b4f22dd5aa4ca8556a61ee9; core/workflow_executor.py=8c50a0a8849ea2cc88b271108a0c42742c82f82a4cdd47a4422cf08ad49e0f69; docs/tooling.md=b3f1730fa8ead9baf5d351b0638829843ad497170ebe9589d935911d6a9f17c0; scripts/verification_worker.py=8bee4e5c907a5154a37e39411646783a2e3d4df940c55fce6dc807fda18438b0; tests/test_background_verification.py=817f2520d0196ca7fd2e7c5e83dfecb65328d97cb5a0c3fbed167e363b9547c9; tests/test_lifecycle.py=88341901fe68336ff6d144b7903ffd792658751980015a883c568bb2552d9771; tests/test_multilanguage_runners.py=5f2a487f40b33fd38008ac51d602cb1f1f777ba012e03cbcb3cfb45bca417579; tests/test_multilanguage_verification.py=a51615110e9d6a6dcea42014c5d4c86425181ad08446a6916ca69d15e4add276; tests/test_parallel_verification.py=f9077061aae44b10957846899824d76a47996ddeaefc1a7d42414d7f047003b3; tests/test_recovery_journal.py=63e17724396e66170def0b73f724f83388a6d25ed9b625fba437731e809ec4f9; tests/test_shared_analyzer_capacity.py=207c6f7dad3bdbf1c32c6b31f02d3069019b3e5afff601d5dab907d68c481f6f; tests/test_verify_changes.py=bb9dcf18c7be715582a80224361d1b8ca91ad7eaa9b69860957e516bdab892f4; tests/test_workflow_actions.py=2ecf37a9eae96d3a120fcdae02d7327816f46e298bb35383678229f8d329eca8; tests/test_workflow_tools.py=efd2f8827ae6897fbb6bc6773fda4039233c9bdf132849a2fe93401ff805d727; tools/testing/background.py=598b798bc41952b0749cc8feff612e1273160a13ceba4f9262970931b62e3ffa; tools/testing/registry.py=676aed8a39333548e4c1b610a1a5ec40782af65ca0ceb8ee18a9ffe7790021a3; tools/testing/runners.py=9f545213569c92b135a97b7c37720f930144fd9dccc1bc93ab5250ca733bd4eb; tools/testing/verification.py=52ddb04bbd34780051a3fc8b0e6cd333bb296e9870998c2017dfca1559fa0b98; tools/workflows/registry.py=0f52f48458cee2d9e6eaf95f6bee02ee70f3a7624f74a30e0008587a645b8d91 |


### EVIDENCE-1 — TASK-P06-04 independent review accepted; full regression running (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | af1236ca2795956ff138ea9f1921b32e86836d70 / feature/coding-agent-evolution |
| state | IN_PROGRESS; implementation frozen; review accepted only within scoped evidence, no hosted/completion claim |
| final_static | fullRuffPASS; fullmypy287filesPASS; compileallPASS; gitdiffcheckPASS after last source/test edit |
| independent_review | integration_review9passed59deselected1.42s; originalcallbackleak reproduction zeroactiveanalyzers/claims. boundary_review171passed1POSIXskip25.69s; sourcehashes frozen before/after; cancellation3times/lifecycle/exactoneeffect and actualWorkflowStore uncertainty covered |
| smoke | main--check isolatedparentstate151toolsPASS; health isolatedtrackedsourcecopy fivechecksPASS151tools6.10s. Initial helper health child used originalroot despite parentconfig override and was terminated as an invalid isolation attempt; no rollback or deletion of operator state performed; subsequent subprocess smoke uses full disposable source copy |
| pending | Complete fullWindows regression currentlyrunning; commit reviewed22files excludingpanel/uv, pushexistingbranch, exact10CI; THEN StartupDoctor diagnosis/fix only |
| file_SHA256 | CHANGELOG.md=018e8ba10da665fc3aa3d181e3a5291793bdb0dab04093103f4f30548b8b7d69; core/recovery.py=326b383a593d5596c486fe6a9a6edc96c55c8b79c4ec56f0e1191ceba548767c; core/tooling.py=2012c3e29d0938823bd27befbdd349ca4f10ec0c2a21d2ced286fa61f404c944; core/workflow_actions.py=818d9588dbd6f913375faa38f6bc02f6dd77e1121b4f22dd5aa4ca8556a61ee9; core/workflow_executor.py=8c50a0a8849ea2cc88b271108a0c42742c82f82a4cdd47a4422cf08ad49e0f69; docs/tooling.md=b3f1730fa8ead9baf5d351b0638829843ad497170ebe9589d935911d6a9f17c0; scripts/verification_worker.py=8bee4e5c907a5154a37e39411646783a2e3d4df940c55fce6dc807fda18438b0; tests/test_background_verification.py=817f2520d0196ca7fd2e7c5e83dfecb65328d97cb5a0c3fbed167e363b9547c9; tests/test_lifecycle.py=88341901fe68336ff6d144b7903ffd792658751980015a883c568bb2552d9771; tests/test_multilanguage_runners.py=5f2a487f40b33fd38008ac51d602cb1f1f777ba012e03cbcb3cfb45bca417579; tests/test_multilanguage_verification.py=a51615110e9d6a6dcea42014c5d4c86425181ad08446a6916ca69d15e4add276; tests/test_parallel_verification.py=f9077061aae44b10957846899824d76a47996ddeaefc1a7d42414d7f047003b3; tests/test_recovery_journal.py=63e17724396e66170def0b73f724f83388a6d25ed9b625fba437731e809ec4f9; tests/test_shared_analyzer_capacity.py=207c6f7dad3bdbf1c32c6b31f02d3069019b3e5afff601d5dab907d68c481f6f; tests/test_verify_changes.py=bb9dcf18c7be715582a80224361d1b8ca91ad7eaa9b69860957e516bdab892f4; tests/test_workflow_actions.py=2ecf37a9eae96d3a120fcdae02d7327816f46e298bb35383678229f8d329eca8; tests/test_workflow_tools.py=efd2f8827ae6897fbb6bc6773fda4039233c9bdf132849a2fe93401ff805d727; tools/testing/background.py=598b798bc41952b0749cc8feff612e1273160a13ceba4f9262970931b62e3ffa; tools/testing/registry.py=676aed8a39333548e4c1b610a1a5ec40782af65ca0ceb8ee18a9ffe7790021a3; tools/testing/runners.py=9f545213569c92b135a97b7c37720f930144fd9dccc1bc93ab5250ca733bd4eb; tools/testing/verification.py=52ddb04bbd34780051a3fc8b0e6cd333bb296e9870998c2017dfca1559fa0b98; tools/workflows/registry.py=0f52f48458cee2d9e6eaf95f6bee02ee70f3a7624f74a30e0008587a645b8d91 |


### EVIDENCE-1 — TASK-P06-04 tested branch checkpoint; exact CI pending (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | e9649e8474be6da66bbcae9c5096b9123bd4e4e3 / feature/coding-agent-evolution |
| state | IN_PROGRESS/IMPLEMENTED_UNVERIFIED until ten exacthead hostedgates; commit e9649e8474be6da66bbcae9c5096b9123bd4e4e3 selectively22files pushedexistingfeaturebranch; DraftPR82 kept |
| local_final | FullWindows suite2213passed28skipped465.92s exit0; fullRuffPASS; fullmypy287PASS; compileallPASS; stageddiffcheckPASS; isolatedmain151toolsPASS; isolatedsourcecopyhealth5checksPASS |
| tested_diff_sha256 | bc39d7e79582599625e08bdf8ea7ff2348bc93d4d0363304748bbd7c8cdaed96 (staged binarydiff before commit; source matches reviewed hash guards) |
| independent | integration_review9pass; boundary_review171pass1skip; reproduced five boundary defects fixed with RED/GREEN; no outstanding confirmed findings in assigned scopes |
| preserved | workingtree only userpaneldiff anduntrackeduv.lock; both hashesunchanged; canonicalplan outsidecheckout; no mainpush/merge/release/forcepush/DAGactivation |
| pending | ExactPRhead tenhostedchecks and correspondingPOSIXrunner evidence; only afteracceptance begin StartupDoctor hang reproduction/rootcause/fix |
| file_SHA256 |  |


### EVIDENCE-1 — TASK-P06-04 Index reconciliation; hosted runs started (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | e9649e8474be6da66bbcae9c5096b9123bd4e4e3 / feature/coding-agent-evolution |
| state | IMPLEMENTED_UNVERIFIED; local acceptance/review complete, task remains unaccepted until ten gates |
| CI | PRhead e9649e8474be6da66bbcae9c5096b9123bd4e4e3; CI40123162742, Platform40123162747, Packaging40123162732 queued/inprogress; connector queried by exactcommit; no skippedcheck accepted |
| progress | TaskIndex04 IMPLEMENTED_UNVERIFIED; P06 4/4 implemented3/4 accepted; task05/P08 remainNOT_STARTED; no other task state changed |
| EXACT_NEXT_ACTION | Await exacthead ten gates, resolve concrete failures, then mark04VERIFIED and begin StartupDoctor; stop after bothverified or genuineblocker |
| file_SHA256 |  |


### EVIDENCE-1 — TASK-P06-04 VERIFIED; Startup Doctor IN_PROGRESS (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | e9649e8474be6da66bbcae9c5096b9123bd4e4e3 / feature/coding-agent-evolution |
| accepted04 | ExactPRhead e9649e8474be6da66bbcae9c5096b9123bd4e4e3 all10checksSUCCESS; nofailed/pending/skippedgate; PR82 staysDraft/open, no merge |
| hosted | CI40123162742 sixOS/Python3.10+3.12; Platform40123162747 threeOS; Packaging40123162732 success |
| source_binding | PRbase e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4; checked merge bca5b91c40ebaae6aba659c353d8634625bdd92f via Windows3.10checkout logs; branchhead andmerge have identical tree1bfce935b5c9c05fa64f5d7dabf0daa311cb0b47. This is explicit tree equivalence, not equalcommitSHA. Windows3.10 fullsuite2223passed18skipped317.07s |
| local_review | Windows2213pass28skip465.92s; fullRuff/mypy287/compileall/diffPASS; isolatedstartup151/health5PASS; independent integration9pass andboundary171pass1skip; relevantPOSIXrunner covered by hostedjobs |
| preserved | Onlypaneldiff/uv.lock remain; protectedhashes unchanged; no05/P08implementation, liveDBmigration, defaultactivation, mainpush/merge/release |
| STARTUP_DOCTOR | IN_PROGRESS as explicitly authorized maintenance after04; reproduce exact nextcheck after dependencyimports/pipcheck on Windows in disposable source/state; no live operator changes |
| EXACT_NEXT_ACTION | Read START_MCP launcher/bootstrap/Doctor check order and current subprocess ownership; reproduce hang with bounded watchdog/diagnostics; RED regression then rootfix; realWindows startup/fullDoctor completion, independent review and branch-only CI |
| file_SHA256 |  |


### EVIDENCE-1 — Startup Doctor root cause and Windows startup GREEN (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | e9649e8474be6da66bbcae9c5096b9123bd4e4e3 / feature/coding-agent-evolution |
| state | STARTUP_DOCTOR IN_PROGRESS; P06-04 staysVERIFIED e9649e8/all10CI; no other Plan taskstarted |
| rootcause | Exactnext afterPASS_pipcheck is MCPfilesystem/terminalsmoke via scripts.health_check. Windows subprocess.run TimeoutExpired killsroot then communicate withouttimeout; grandchildretainedpipes causesindefinitewait. Independent .25s budget returned2.578s; root external6s watchdog RED provedoriginalDoctorhang while 30sgrandchildheldpipes |
| RED_GREEN | Inheritedpipetest RED watchdog6sexceeded; explicitnativeflag RED missingparam; RUNprogressRED missingdispatch; healthsilentstartup+payload2RED; bootstrapwatchdogs2RED. Afterfix51targetchecksPASS; success/timeoutchildcleanup additions underregression |
| implementation | Existing run_bounded optin require_native OR stricterinheritedrequirement; Doctor zerocapture/nativeownedcleanup/finitebudgets/progress, rg bounded4096/versionvalidation; health Client30s+sameTaskAnyIO90s/staticphase/safefailure; Bootstrapcache30/full900nativewatchdogs, no newstateengine |
| realWindows | Actual START_MCP.bat validation-only local-http on disposabletrackedsource/tempvenvusingrealinstalleddependencies: cold fullDoctor exit0 allchecksPASS (imports1.70s,pipcheck.46s,filesystemterminal3.44s,browserimports.28s), [5/5]Validationcomplete. Warmcache exit0 fastchecks. Allroot/child launches underwindows_job; no live operator profile or DB intentionally launched for these acceptedchecks |
| fixturecorrection | Initial temporaryruntime .pth omitted pywin32 bootstrappaths; genuine ModuleNotFoundError(pywintypes), noPASS. Reusinginstalledsite throughsite.addsitedir fixedonlytemporaryfixture; actualdependencyimports/pipcheck rerunPASS, no runtime gate weakened |
| limits | Original hung launcher no longerpresent onread-onlyprocessinventory, so nooriginalconsoleinteraction performed; live tunnelprofile/network readiness UNVERIFIED; accepted nativeWindowsstartup deliberatelyisolated |
| pending | Finalregression/static/independent exacthashreview, selectivecommit/push samebranch and tenexactheadCI; stopafterDoctorverified |
| file_SHA256 | CHANGELOG.md=35db33e70afb170aa7d348264bf46cf1752256af1eb87826793760642c874971; core/executor.py=18085fea3dc426ca3d6fb2236319e714991448504773d51639889dba62f0ec4b; docs/installation.md=ebb1b4bb9e185e2126dfc206eccf77d0761dd2493964d8ef20d503b8a4cc40ff; scripts/bootstrap.py=bcda0ece5067cc7a332450c51dc0f0a0c85688f321f209591d6ace3d77b4dc8c; scripts/doctor.py=554e3bb3572100abade4601a987aceaa002ff6606c8781439439ea225615660a; scripts/health_check.py=d5345547156880ad8de57a6240e1422f417c1d81a6020400eb4864a1d6ae9ccb; tests/test_bootstrap.py=84995688aba5095dd6ce0f53371117c030ac5c3b94747626bba303c22032d9ed; tests/test_doctor.py=74bc9bd7a762168517b3c6487b84c160d6d0cc361fd6b047db7708f942235e64; tests/test_executor.py=20bab5c30a1a31dd11815b3779fbd5ae3e69d3776632c5e7219d3d9fe979767c; tests/test_health_check.py=76ff6204359cf267fba85cf5e20666daef7dc2e9113d1f613081c689c04c1273 |


### EVIDENCE-1 — Startup Doctor independent final review and final Windows startup (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | e9649e8474be6da66bbcae9c5096b9123bd4e4e3 / feature/coding-agent-evolution |
| state | IMPLEMENTED_UNVERIFIED pending finalregression+branchCI; P06-04 staysVERIFIED |
| additional_RED_GREEN | Explicitnative OR initiallyskipped inheriteddispatchguard:1RED deniedprocessdispatched->1GREEN afterguardfirst. Cacheuncleancleanupfallback2RED->2GREEN fatalcleanup+freshdependencydeferral. SDKshieldedshutdownpastdeadline1RED falseok->1GREEN explicitfinalmonotonicdeadlinecheck |
| independent | boundary_review86passed0skip6.48s frozenDoctor/bootstrap/executor plusnativeWindowsjobs and descendantdeadBEFOREoutercleanup. integration_review7actualSDKsubprocesscases+3existingtests: init/catalog/cumulative/shutdown deadlines, canaries,oneJSON/staticstderr,allchildPIDsgone. No outstandingconfirmed findings |
| final_Windows | Current fourlogicfiles rawbytehashmatched into disposabletrackedsource; actual START_MCP.bat validation-onlylocalhttp finalcoldfullDoctor exit0/imports1.68s/pip.46s/nativefilesystemterminal3.13s/browserimports.22s/[5/5]Validationcomplete; finalwarmcache exit0 unchangedfingerprint. windows_job/nativecleanup; no liveprofile/networkactivation |
| static | FullRuffPASS, fullmypy288filesPASS, compileallPASS, diffcheckPASS afterlastlogic/testedit; importsortwarningfixed thenRuffrerunPASS. Two modifiedPythonfiles normalizedto.gitattrsLF explicitly beforefinalreview |
| pending | Final8file regression running; then selective10filecommit/push currentbranch and exacthead10CI; startupnative localverified, liveoperatorTunnel/network remainsUNVERIFIED |
| file_SHA256 | CHANGELOG.md=35db33e70afb170aa7d348264bf46cf1752256af1eb87826793760642c874971; core/executor.py=e5b5ec45911149a8fa303ff228b367392c8102ffcc0a0bd7544c0fd377bb0ef5; docs/installation.md=6d1ec8dd2560acb7ea5242344e5956a97e29df98d84ffeed53d4dc0a955e28da; scripts/bootstrap.py=102388e13afe805b163feca984ff9f5198bf78c1e3f63ea829f4c030f15578c7; scripts/doctor.py=554e3bb3572100abade4601a987aceaa002ff6606c8781439439ea225615660a; scripts/health_check.py=6794f2ccdecb830bb3952579cd28e39d0365f615963f9369ea1d9331b4354467; tests/test_bootstrap.py=7b42f88a108272fd2ded9083239745673cbc61448bd82d2b2fa2640f9817435a; tests/test_doctor.py=74bc9bd7a762168517b3c6487b84c160d6d0cc361fd6b047db7708f942235e64; tests/test_executor.py=768214f3e5212c3efad5cb3a7b50358d4b9a6043829c8b4bfb54ce9c5645e6dd; tests/test_health_check.py=09504503ac2af5f1831090a299dcc3101e2520b2e9ba6644d1c4606df5ee3854 |


### EVIDENCE-1 — Startup Doctor tested branch checkpoint; final CI pending (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | b3ee91aa56e745cfd75c92addd9445f5583f04b4 / feature/coding-agent-evolution |
| state | IMPLEMENTED_UNVERIFIED awaiting exacthead hosted10; commit b3ee91aa56e745cfd75c92addd9445f5583f04b4 fix(startup): bound Doctor checks and owned cleanup; selectively10files branchonly, no protectedfileincommit |
| final_regression | Exact final8files278passed1POSIXskip57.54s exit0; focused4Doctor/bootstrap/executor/health57passed4.98s beforeimportsortonly; fullRuff/mypy288/compileall/diffPASS afterlastlogic/testedit |
| independent_final | Boundary86passed0skip6.48s; health7realSDKcases+3unitcasesPASS,allchildPIDsgone,secretcanariesabsent,deadline-overrunshutdownFAILasrequired; allfinalreviewedsourcehashesmatched beforecommit |
| final_Windows | Actual START_MCP.bat validation-onlylocalhttp disposabletempstate/source/dependencyenvironment fullDoctor exit0/imports1.68s,pip.46s,filesystemterminal3.13s,browserimports.22s/[5/5]Validationcomplete; warmfingerprintcache exit0; currentfourlogicfileshashmatchedcopy |
| diff_sha256 | b40612bdfc6431675c8143f0e1980ae1e2a09423b9388bb2b6531e87d1eb50df (staged binarydiff beforecommit) |
| final_source_hashes | executor e5b5ec45911149a8fa303ff228b367392c8102ffcc0a0bd7544c0fd377bb0ef5; doctor554e3bb3572100abade4601a987aceaa002ff6606c8781439439ea225615660a; bootstrap102388e13afe805b163feca984ff9f5198bf78c1e3f63ea829f4c030f15578c7; health6794f2ccdecb830bb3952579cd28e39d0365f615963f9369ea1d9331b4354467 |
| protected | workingtree only userpaneldiff6f682baebf08556a4b5bab2d9d9e34f541ccf6ebbee13abda611a3c9b67bd3d4 anduv.locke9bbee1693a12ebdde82d62ef98722036e5b14db754842bd6c865375594e56aa; no main/merge/release/forcepush, no futurePlan task |
| file_SHA256 |  |


### EVIDENCE-1 — Startup Doctor final CI identity; initial isolation limitation (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | b3ee91aa56e745cfd75c92addd9445f5583f04b4 / feature/coding-agent-evolution |
| CI | Exacthead b3ee91aa56e745cfd75c92addd9445f5583f04b4; CI40125536780, Platform40125536796, Packaging40125536788. Platformthree+PackagingSUCCESS; sixfullCIjobs pending, nofailure at snapshot |
| final_regression | 278passed1POSIXskip57.54s exact8filefinalregression; allrequirednativeWindowsDoctor cases covered. FullRuff/mypy288/compileall/diffPASS, independentboundary86pass andhealth7SDK+3unitPASS |
| initial_isolation_limitation | Earlier health wrapper onlyredirected parentcore.config.SETTINGS; SDKsubprocess loadedactualrepositoryroot. Attempt wasterminated and notaccepted asvalidsmoke. Operator jobs.sqlite3 mtime2026-10-24T18:50:38Z/workflows.sqlite3 mtime18:51:39Z overlaptheattempt; no pre-attemptDBhashsnapshot exists, causal attribution and logicaldata changes UNVERIFIED. Do not claim live-state noninterference. No rollback/deletion/reconciliation ofoperatorstate performed; allacceptedstartupchecks usefulltemp source/state |
| constraint_status | Nativefunctionalfixverifiedlocally; finalCI pending. Historical noninterference requirement cannotbeproved afterinitialinvalidattempt; retainasmaterial limitation/genuineevidenceblocker, not fakePASS. Protectedpanel/uvhashes remainverifiedunchanged |
| EXACT_NEXT_ACTION | Finish exacthead10CI; preserveoperatorstate/evidence; report functionalresults and initialisolationlimitation separately; STOP withoutnextPlan tasks |
| file_SHA256 |  |


### EVIDENCE-1 — Startup Doctor health-state isolation RED/GREEN and final review (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | b3ee91aa56e745cfd75c92addd9445f5583f04b4 / feature/coding-agent-evolution |
| state | IMPLEMENTED_UNVERIFIED finalisolationcheckpoint; P06-04 remainsVERIFIED; no otherfeaturetask |
| confirmed_problem | Actualchildsettings regression RED showed source_root/.agent_state before runtime import; currentDoctorhealth normally opens operatorJobStore/lifespan scheduler. Initialinvalidsmoke noninterference limitation remainsirreversiblewithoutbaseline; no restorationattempt |
| isolation_fix | ParentownedTemporaryDirectory encloses entire SDKclient; -I child trusted sourcebootstrap dataclasses.replace core.config.SETTINGS(state_dir,memory_dir) BEFORE runpy main; no fullenvcopy; currentSDK defaultsafeenv excludesoperator MCP/provider/lifecycle/credentials. No fake server/omittedcheck inacceptedstartup |
| RED_GREEN | ActualSDKchildreported operatorstate1RED->1GREEN afterbootstrap; currentDoctor+health35passed17.59s; independenthealth4passed0skip13.88s,7SDKfailurematrixPASS,9derivedpathsunderownedtemp+removedaftercleanup,parentenvnotinherited |
| Windows_final | Directactualhealth5checksPASS151tools and actualSTART_MCP.bat localhttpvalidation fullDoctor exit0/[5/5]complete; preexistingdisposableoperatoraudit/jobs/workflowDB3rawhashesUNCHANGED before/afteractualserverprobe+Doctor. Fullsmoke3.38s; allunderwindows_job |
| static | FullRuff/mypy288/compileall/diffPASS afterfinalisolationedit; frozenhealthhash7bbf44e1409fba172a4f87afb0e998155204dc00b237de53abb4e57d9a8260c4; testef4d3c107738715a02edc4c4b25c91d63365c3a95d79a8846c200c1b84b91c3e |
| CI | Pre-isolation b3ee91a9/10green/Windows3.10pending atsnapshot; finalisolationrequiresnewexacthead10CI. No mainpush/merge/release/tag; protectedpanel/uvunchanged |
| file_SHA256 | CHANGELOG.md=db7b137784c8b81f740fadcaa8e26a5c22af5816b80288e0406e3863d5c6d9d4; docs/installation.md=49e45f90686010f97814162f5cd132a9ebe2e8af83213e3259d873596abf4284; scripts/health_check.py=7bbf44e1409fba172a4f87afb0e998155204dc00b237de53abb4e57d9a8260c4; tests/test_health_check.py=ef4d3c107738715a02edc4c4b25c91d63365c3a95d79a8846c200c1b84b91c3e |


### EVIDENCE-1 — Startup Doctor isolation branch checkpoint; exact final CI pending (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 / feature/coding-agent-evolution |
| state | IMPLEMENTED_UNVERIFIED until finalexacthead10; commit02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 fix(startup): isolate health probe runtime state; fourreviewedfiles selectivecommit/push existingbranchonly |
| accepted_local | ActualWindowsnativehealth+FullDoctor/[5/5]PASS,3disposableoperatorfilehashesunchanged; finalhealth4unit+7actualSDKcases+9derivedpathsindependentPASS; previousboundary86pass/relevant278pass1skipretainedforunchangedboundaries; finalfullRuff/mypy288/compileall/diffPASS |
| scope_stop | No remainingimplementationtask withinmission; only finalCIcollection/evidence. Initialinvalidattemptlive-state noninterference remainsUNVERIFIED and mustbereportedasmaterialevidenceblocker; no operatorrollback/deletion authorized/performed |
| protected | Onlypaneluserdiff/uv.lockuntracked remain; canonicalplansoleexternalcopy; no mainpush/merge/release/forcepush/defaultactivation/futuretask |
| file_SHA256 |  |


### EVIDENCE-1 — Startup Doctor final hosted identity and stop boundary (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 / feature/coding-agent-evolution |
| final_head | 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 onexistingfeaturebranch; PR82Draft/open;base e66c8c9c74ec73efca2eb1f7f23bd5bc2e6ee1b4; currenttestedmerge7241c1ec315491b2097904f8d75fe1e4b65aa317 |
| final_CI | CI40126345756 sixfullOS/Pythonjobs IN_PROGRESS; Platform40126345776 threeOS SUCCESS; Packaging40126345795 SUCCESS; 4/10green,no failure at snapshot. Need all10onthishead beforefunctionalacceptance |
| protected_guards | StrictUTF8/43Indexrows/endmarkersPASS; branchguardPASS; panel/uvrawSHA unchanged; statuscontainsno productdiffaftercommits; no masterplanincheckout |
| EXACT_NEXT_ACTION | Collect sixCIjobs tocompletion andsameheadcheckrollup/treecheckoutproof. Ifallpass, recordDoctorfunctionalVERIFIED alongside initialprobeoperatorstateevidencelimitation andSTOP. Do not opentasks05/P08 or dispatch anyproductionstate recovery. |
| file_SHA256 |  |


### EVIDENCE-1 — P06-04 and Startup Doctor final functional acceptance; mission STOP (2026-10-24 Asia/Tehran)
| field | evidence |
|---|---|
| HEAD/branch | 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 / feature/coding-agent-evolution |
| functional_acceptance | TASK-P06-04 VERIFIED on e9649e8474be6da66bbcae9c5096b9123bd4e4e3 with all10hostedchecks, retained by final02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 all10. STARTUP_DOCTOR VERIFIED_NATIVE_FUNCTIONAL on finalhead; no outstanding confirmed functional/review defect |
| final_exact_CI | All10SUCCESS/no failed/pending/skippedgate; CI40126345756 sixOS/Python3.10+3.12, Platform40126345776 threeOS, Packaging40126345795. Allthreecompleted/success eventpull_request exacthead02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 |
| checkout_identity | ActualWindows3.12+Linux3.12 checkoutlogs showmerge7241c1ec315491b2097904f8d75fe1e4b65aa317; headandmerge identicaltree65f2755ed820414fe8928b010e12b7105ef29fbf. PRbasee66c8c9unchanged, PR82Draft/open. TestedmergeSHA is notbranchSHA; explicittreeequivalenceverified |
| full_CI_test_evidence | WindowsPython3.12 2249passed7skipped495.14s; LinuxPython3.12 2235passed21skipped362.79s; allsixfullmatrixjobsSUCCESS. NativeDoctor/ownership regressions executed onWindows andPOSIX; platform-only skips remain honest |
| local_acceptance | P06-04 fullWindows2213pass28skip465.92s beforeDoctor; finalruntime regression278pass1POSIXskip57.54s beforeisolation-onlypatch; finalDoctor+updatedhealth35pass17.59s; finalfullRuff/mypy288/compileall/diffPASS. Independentboundary86pass0skip; finalhealth4unit+7actualSDKcases+9derivedpath/env/cleanupchecksPASS |
| actual_Windows_startup | Real START_MCP.bat validation-onlylocalhttp in disposabletrackedsource/reusedinstalledruntime: coldFullDoctor exit0/[5/5]Validationcomplete; filesystemterminal3.38s, imports/pip/browserimportsPASS; warmcachePASS. Updatednativehealth5checksPASS151tools; threepreexistingdisposableoperatoraudit/jobs/workflowfiles rawhashesUNCHANGED aroundhealth+Doctor. Probechild nowowns disposableSettingsbeforemainimport |
| changed_files_and_commits | e9649e8 22scopedP06files; b3ee91a 10Doctor/coreexecutor/bootstrap/health/test/docsfiles;02796e9 fourhealth-isolation/test/docsfiles. No newdeps/schema/stateengine/provideractivation; no otherPlan task implemented |
| final_source_hashes | core/executor.py e5b5ec45911149a8fa303ff228b367392c8102ffcc0a0bd7544c0fd377bb0ef5; scripts/bootstrap.py102388e13afe805b163feca984ff9f5198bf78c1e3f63ea829f4c030f15578c7; scripts/doctor.py554e3bb3572100abade4601a987aceaa002ff6606c8781439439ea225615660a; scripts/health_check.py7bbf44e1409fba172a4f87afb0e998155204dc00b237de53abb4e57d9a8260c4 |
| strict_constraint_evidence_blocker | Initialinvalidhealthattempt child loadedactualrepo state; no pre-attemptDBhashbaseline, so historical operator-state noninterference remainsUNVERIFIED/BLOCKED_NO_BASELINE. No rollback/deletion or fabricatedPASS. This cannotbeproved retrospectively by finalisolatedtests; user-facingreport mustretain limitation |
| unverified_runtime | LiveoperatorTunnel/profile/networkreadiness and realforeignrunnerinstallation/integration notclaimed; no live service was launched foracceptedvalidation. Originalhungconsole no longerpresent duringread-onlyinventory; reproducedexactWindowsinheritedpipe failure withownedfixtures |
| final_preservation | gitstatus only usertools/panel/index.html diff anduv.lockuntracked; panel6f682baebf08556a4b5bab2d9d9e34f541ccf6ebbee13abda611a3c9b67bd3d4 anduve9bbee1693a12ebdde82d62ef98722036e5b14db754842bd6c865375594e56aa unchanged. No mainpush/merge/release/forcepush/tag. Canonicalplan remainssoleexternalcopy |
| EXACT_NEXT_ACTION | STOP. Nativefunctionalmissioncomplete with initialoperator-state evidenceblocker documented. Do not startTASK-P06-05,P08 or anyotherPlan feature, do not modify/reconcileoperatorDB. A later explicitrequest may supply pre-attemptsnapshot for separate read-onlycomparison; no such audit inferred now |
| file_SHA256 |  |

### EVIDENCE-1 — TASK-P06-05 VERIFIED local runner/compatibility acceptance (2026-10-25 Asia/Tehran)
| field | evidence |
|---|---|
| state | VERIFIED؛ STRONG_ONLY weight0 acceptance over P06-01..04؛ no product change؛ HEAD 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 / feature/coding-agent-evolution |
| baseline command | .venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_multilanguage_affected_tests.py tests/test_multilanguage_runners.py tests/test_multilanguage_verification.py tests/test_affected_tests.py tests/test_background_verification.py → 261 passed, 2 skipped, exit0, 42.75s |
| skips | Windows-only: tests/test_multilanguage_affected_tests.py:438 PHP symlink privilege؛ tests/test_multilanguage_runners.py:517 POSIX virtualenv interpreter symlinks. Both previously exercised by hosted Linux/macOS CI on accepted P06 heads؛ honest platform skips, not unknown pass |
| static gates | full `ruff check .` PASS؛ full `mypy core tools scripts tests` 288 files PASS؛ `compileall -q core tools scripts tests` exit0 |
| runner matrix | Python pytest 3.12.8 (repo interpreter): fake deterministic + real repository suite PASS؛ Python unittest 3.12.8: deterministic selection/verification PASS؛ Node v22.18.0/npm10.9.3 present but Jest29/Vitest3 not installed → notmeasured؛ PHP/PHPUnit11/Pest3 absent → notmeasured. No network install performed |
| compatibility coverage | direct/transitive/configchanged/deleted/monorepo/untracked/unknownrunner/0tests/crash/timeout covered by deterministic fixtures؛ countselected vs groundtruth asserted؛ extra conservative tests allowed؛ Python default behavior unchanged؛ auto backend opt-in only |
| docs | docs/tooling.md documents fixed argv, version gates, no package scripts/installs, unknown semantics, and explicit non-sandbox limitation؛ consistent with current source |
| test file SHA256 | test_multilanguage_affected_tests 7074563d574f69c0f3fe4d01175abb2396058882fd12e13935c2db126c766862؛ runners 5f2a487f40b33fd38008ac51d602cb1f1f777ba012e03cbcb3cfb45bca417579؛ verification a51615110e9d6a6dcea42014c5d4c86425181ad08446a6916ca69d15e4add276؛ affected 6b21372d52efe2262fd3fa87b7d184eeeb59f893c1334eb753b8055ac1046766؛ background 817f2520d0196ca7fd2e7c5e83dfecb65328d97cb5a0c3fbed167e363b9547c9؛ tooling.md b3f1730fa8ead9baf5d351b0638829843ad497170ebe9589d935911d6a9f17c0 |
| scope limits | P06 complete only for runners with an actual executed gate (Python pytest/unittest)؛ Jest/Vitest/PHPUnit/Pest remain capability-unavailable/notmeasured؛ no foreign-config sandbox claim؛ live operator state/DB untouched |
| EXACT_NEXT_ACTION | P08-01 RED: tests/test_typed_output_contracts.py؛ then typed output models/annotations under P08-01 scope |

### EVIDENCE-1 — TASK-P08-01 VERIFIED typed context/impact wire contracts (2026-10-25 Asia/Tehran)
| field | evidence |
|---|---|
| state | VERIFIED؛ WEAK_WITH_REVIEW weight1؛ HEAD 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 / feature/coding-agent-evolution |
| RED | tests/test_typed_output_contracts.py failed on missing core.response_models/tools.project intelligence models/tools.testing.models before implementation؛ no product assertion weakened |
| GREEN command | .venv/Scripts/python.exe -m pytest -o addopts= -q -rs tests/test_typed_output_contracts.py tests/test_code_context.py tests/test_affected_tests.py tests/test_multilanguage_affected_tests.py → 134 passed, 1 skipped, exit0, 8.26s |
| skip | tests/test_multilanguage_affected_tests.py:438 symlink creation unavailable on Windows؛ same accepted P06 platform skip؛ no silent pass |
| wire compatibility | code_context/affected_tests output_schema has no SDK `result` wrapper؛ success dict equality before/after validation؛ absent optional source/hint stay absent not null؛ legacy failure remains {ok:false,operation,error,message,hint?}؛ provider/source message not echoed in output_contract_violation |
| model invariants | CodeContextResult reuses ContextRow/EvidenceEdge/SnapshotEvidence؛ unknown semantic edge rejected؛ incomplete context requires reasons؛ AffectedTestsResult requires total>=count only when total_exact=true and mirrors test_count؛ package_selections empty list preserved |
| static gates at P08-01 | full Ruff PASS؛ full mypy 291 source files PASS؛ compileall exit0؛ final combined P08-01/02 gate mypy 293 files PASS |
| test file SHA256 | test_typed_output_contracts.py 8fcf1b37b93a0535000ad7da609d2eb8a5b6fc9aab78d0c5a5de9f6558e94fdd (current file also contains accepted P08-02 tests) |
| source SHA256 | core/response_models.py a9b156b508721cea12d3ddac5c2fe3ec6a48b045c7a9110b2856bc57c518c548؛ tools/project/intelligence_models.py cf7403e70a1d9ebfc2f9c18d070f4758c8ccc3cddb1ab28b896474f0bd5c047d؛ tools/testing/models.py 9e66e2e0513b88536e8b84925b351e979ff73800287bcaa3e6c9a9de76e23f8b؛ tools/project/registry.py 33c8e4d11983d8d59586b9434551117ec3b691fc8affd3ce72f448b696b457e3؛ tools/testing/registry.py f6336729d576c0510cbe15cdeb5377092c314fda586ef7dabdf5af52220b74ed |
| scope limits | Public tool count/schema identity for all 151 tools unchanged؛ no dispatcher/execution permission added؛ no operator DB/state touched |
| EXACT_NEXT_ACTION | P08-02 search_files/project_summary typed variants |

### EVIDENCE-1 — TASK-P08-02 VERIFIED search/project-summary wire contracts (2026-10-25 Asia/Tehran)
| field | evidence |
|---|---|
| state | VERIFIED؛ WEAK_WITH_REVIEW weight1؛ HEAD 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 / feature/coding-agent-evolution |
| search branch map | cursor continuation scanned_files=0/from_snapshot؛ exact snapshot creation؛ exact page؛ empty page؛ content match canary non-leak؛ streaming count_mode=none؛ streaming snapshot؛ scan-cap page has_more=true/next_offset=null/scan_truncated=true؛ stale cursor compact failure search_cursor_mismatch؛ all validate to identical wire dict |
| summary branch map | empty directory؛ mixed Python/TypeScript+React/Express+tests؛ invalid package.json/pyproject.toml returns ok=true bounded empty metadata؛ impossible dependency_count rejected؛ list caps declared languages8/frameworks15/dependencies30/entry_points20/test_paths15/top_level30 |
| RED_GREEN bug | tests/test_search_streaming.py exposed over-strict validator on scan-truncated streaming page (has_more=true with no progressing offset)؛ fixed to require next_offset or cursor only when not scan_truncated؛ focused search+typed suite 22 passed after fix |
| acceptance command | .venv/Scripts/python.exe -m pytest -o addopts= -q -rs tests/test_typed_output_contracts.py tests/test_filesystem_registry.py tests/test_project_cache.py tests/test_project_intelligence_models.py tests/test_search_snapshots.py tests/test_search_streaming.py --deselect tests/test_filesystem_registry.py::test_failed_safe_refactor_invalidates_after_rollback → 153 passed, 4 skipped, 1 deselected, exit0, 11.39s |
| skips | test_project_cache.py:752 symlink unavailable؛ test_project_intelligence_models.py:174 directory symlinks unavailable؛ test_search_snapshots.py:449/476 directory links unavailable؛ all host capability skips |
| live-state blocker | test_failed_safe_refactor_invalidates_after_rollback does not isolate config.SETTINGS.state_dir and hit pre-existing live .agent_state resource admission timeout؛ reproduced exact rollback behavior in disposable state: result validation_failed_rolled_back, file restored, invalidation (target, recursive=False). Live DB not opened/modified/reconciled |
| wire compatibility | search_files output_schema has no SDK `result` wrapper؛ SearchItem actual wire is {path,matched_in} (no invented line/snippet)؛ snapshot metadata/cursor fields preserved؛ project_summary schema matches current service fields؛ failure envelope unchanged |
| static gates | full Ruff PASS؛ full mypy 293 source files PASS؛ compileall exit0؛ panel user diff and uv.lock untouched |
| test file SHA256 | tests/test_typed_output_contracts.py 8fcf1b37b93a0535000ad7da609d2eb8a5b6fc9aab78d0c5a5de9f6558e94fdd |
| source SHA256 | tools/filesystem/models.py 1714490adc04b9e2fae3b5aff19189ff29be5009e4cb8bcfd249fd0607b818e0؛ tools/project/models.py be3193af87894453de83a031c2ae62892e4f32c1cf7bb17d762e36c2d38b81f3؛ tools/filesystem/registry.py 437e4d59a7f2ac8eb9cf239e9d75cb758515f4e9752654a0e248e9fcab8a814f؛ tools/project/registry.py 33c8e4d11983d8d59586b9434551117ec3b691fc8affd3ce72f448b696b457e3؛ core/response_models.py a9b156b508721cea12d3ddac5c2fe3ec6a48b045c7a9110b2856bc57c518c548 |
| scope limits | No result sorting/filtering change؛ no new top_level cap؛ zero-extension native catalog preserved؛ no operator state mutation |
| EXACT_NEXT_ACTION | P08-03 job_status/workflow_status/server_health typed variants |

### EVIDENCE-1 — TASK-P08-03 VERIFIED operational wire contracts (2026-10-25 Asia/Tehran)

| field | evidence |
|---|---|
| state | VERIFIED; executor/reviewer local authorization; HEAD 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8; branch feature/coding-agent-evolution; uncommitted task diff |
| integrity recovery | P02-03 was already VERIFIED: preserved 2026-10-19 predecessor revalidation of tests/test_project_cache.py 0a9486bb... and hosted acceptance head6b7e85f...; no historical baseline rewritten. Seven older parsed digest differences are tracked clean at current HEAD with subsequent feature commits (0414382/c8f38ae/b74dc0f); full current regression will revalidate before milestone closure. Panel/uv protected hashes match prior ledger. |
| discovery | Scoped current codebase-memory search identified shared collect_server_health and runtime integration tests; nested registry handlers absent from exact symbol lookup, so verified current source registrations/callers directly. No graph rebuild or claim of complete coverage. |
| RED | 11 new failures: missing JobStatusResult/WorkflowStatusResult/HealthResult and existing untyped status schema; expected before implementation. |
| GREEN | Disposable tracked-source snapshot plus all owned typed-model/test additions: pytest -o addopts= -q --tb=short tests/test_typed_output_contracts.py tests/test_jobs_registry.py tests/test_workflow_tools.py tests/test_health_snapshot_runtime.py tests/test_extension_health.py -> 51 passed, 0 skipped, exit0, 13.05s. No operator state copied/opened. |
| recovery | Corrected workflow timestamp types to actual persisted ISO strings; fixed fixture to use allowlisted verify_changes action; no weakening of wire assertions. Temporary runner cwd restored before cleanup on Windows. |
| contracts | Job states retain queued/running/orphaned/succeeded/failed/cancelled/timed_out/interrupted; workflow reuses WorkflowState/StepState including uncertain. Workflow status still has no invented ok field. Full/summary/unavailable/payload_limit health all preserve dict equality and no SDK result wrapper. Unknown stored state returns output_version_unsupported without raw value. No schema/store migration, polling activation or dispatch added. |
| static | full Ruff PASS; full mypy 293 files PASS; prior compileall PASS; full milestone gates follow P08-04. |
| file_SHA256 | core/response_models.py=7aad2c406e2dac744a40159f53bffcf3bb54ea2710072297bfca450a42a6274b; core/registry.py=c3cc25ac7ebfe0ca24190e21fb332f54d24f7bac71e2f9703521c8cb8733eb6b; tools/jobs/registry.py=2adbb7e6a3aa1a292de1aa7b6bf75edb9af715e58d84ae0bf4cdfc9df6a2333f; tools/workflows/registry.py=877364427af8e9884c27b967ab9f3aaa568cc89f00d051e313d37fae33cb00a8; tests/test_typed_output_contracts.py=e71d467f7531e5262fd50a731c5ca26bbb25ba022ea38d792020f4c3574cbbe3 |
| EXACT_NEXT_ACTION | TASK-P08-04 RED -> typed verification/diagnostic/advisory Router models -> local acceptance and full M08 gates. No commit/push/PR/release authorized. |


### EVIDENCE-1 — TASK-P08-04 IN_PROGRESS recovery checkpoint (2026-10-25 Asia/Tehran)

| field | evidence |
|---|---|
| state | IN_PROGRESS; P08-03 remains VERIFIED; branch feature/coding-agent-evolution; HEAD 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8; no commits/staging/pushes |
| RED_GREEN | 4 RED missing typed models -> P08-04 focused 67 PASS; added real owned strict-LSP full/unchanged UTF-16 diagnostic fixture and canary: typed+client 36 PASS; P08-03 51 PASS. Full current Ruff/mypy293/compileall/diff PASS. |
| root_cause_recovery | Full regression exposed legacy MCP catalog rejection of union output schemas lacking top-level type:object. Fixed once in WireRootModel json_schema_extra; actual stdio+HTTP/legacy transport plus typed tests now 33 PASS. Incomplete verify_changes forwarding stub updated to actual required wire shape; background/multilingual/profile/extension-router/integration boundary 174 PASS. |
| harness_recovery | Disposable validation runner injected PYTHONPATH and shadowed the isolated launcher fixture's namespace scripts package; removed injection. Launcher fixture 2 failed -> 4 PASS. No product launcher edit. No operator state used; all temporary copies outside checkout. |
| startup_health | Final current source main.py --check PASS151 unique tools; scripts.health_check --json PASS all5 actual native checks; owned disposable state with no unresolved operations. |
| current_remaining | Initial broad run is still finishing its old snapshot (known 6 failures already fixed; cannot be used as final PASS). Need rerun full pytest on final source using corrected runner, then targeted P08 gates/static and final atomic VERIFIED. No further product edit pending. |
| integrity | Seven older digest records map to tracked-clean HEAD: semantic_edits test 8e1c509; language edits 6a10ad7; tool authoring docs a80883f; intelligence models/tests and sessions c8f38ae; multilingual context registry 0414382. Historical digests retained; final current regression required for fresh milestone baseline. |
| protected | panel6f682baebf08556a4b5bab2d9d9e34f541ccf6ebbee13abda611a3c9b67bd3d4; uv.lock e9bbee1693a12ebdde82d62ef98722036e5b14db754842bd6c865375594e56aa; no operator DB read/mutation; canonical plan sole external copy. |
| genuine_gates | P05-02 live-model UX pilot requires separately scoped model/client/budget; P05-03 real refresh transcript absent; P07-02 needs independent deployment/backup/operational evidence; P10/P11/P12/P13 remain intentional BLOCKED/DEFERRED. Historical initial Doctor operator-state noninterference cannot be proved without a pre-attempt baseline; do not fabricate or reconcile live DB. |
| EXACT_NEXT_ACTION | Wait session71118 old full snapshot to finish; inspect failure summary. Run external temp cp-evolution-local-fnyju_r2/validate.py with repo and pytest -o addopts= -q --tb=short -rs --durations=10 --basetemp=.pytest-tmp, retaining log. Runner copies only tracked source plus five owned untracked typed files, excludes uv/state/memory, never copies master plan. Then full static/schema metrics and append hashes/atomic P08-04 VERIFIED; no eligible unfinished task should remain. |

### EVIDENCE-1 — TASK-P08-04 VERIFIED and M08 local closure (2026-10-25 Asia/Tehran)

| field | evidence |
|---|---|
| state | TASK-P08-04 VERIFIED; P08-01..04 all VERIFIED; no NOT_STARTED/IN_PROGRESS/FAILED/NEEDS_REVALIDATION task remains. Executor and Reviewer under explicit IMPLEMENT_LOCAL plus milestone SHA-256 advancement authorization. |
| subject | HEAD 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8; branch feature/coding-agent-evolution; uncommitted reviewed local diff; tracked-owned diff SHA256 23cff680cd29607d1a5fc4b2336d02bb63e424eec2a7084be937fcd61aea40d1 |
| tested_source_identity | Whole source snapshot SHA256 edaa0c1883b1e665a835e9baf24c32800bdc2468dab5df31105df467a2ad0dbe over 380 tracked/owned source files (includes preserved panel); current raw source recomputed IDENTICAL after final suite. SHA256 over sorted path + NUL + raw-file-SHA256 + newline; uv.lock/live state/memory/secrets excluded. |
| RED | Four focused failures for absent VerificationResult/DiagnosticsResult/RouteRecommendation, before implementation. No assertion weakened. |
| GREEN | Focused P08-04 typed/execution-router/language-registry/parallel-verification suite 67 PASS; P08-03 status/jobs/workflow/health/extension-health 51 PASS; real owned LSP protocol plus typed/client 36 PASS; legacy+modern stdio/HTTP transport plus typed suite 33 PASS; background/multilingual/profile/extension-router/integration boundary 174 PASS. Final full suite below supersedes all changed shared-helper/fixture evidence. |
| full_final | Requested repo .venv/Scripts/python.exe; disposable source; python -m pytest -o addopts= -q --tb=short -rs --durations=10 --basetemp=.pytest-tmp -> 2257 passed, 28 skipped, 0 failed, 480.19s; pytest child exit0 and outer validation runner exit0 including disposable cleanup. All 28 skips are Windows symlink privilege/POSIX-only capability limits; not reported as passes. |
| full_log_SHA256 | 6d21a23e21bc1f5aee4edd4ef24f0ef90561be3e8bf7868cc490254521287d51 (temporary diagnostic log outside checkout; decisive results retained here) |
| final_static | Full Ruff exit0; full mypy core tools scripts tests exit0, 293 source files; compileall core tools scripts tests exit0; git diff --check exit0 after last product/test edit. |
| native_smoke | main.py --check exit0, 151 unique native tools; python -m scripts.health_check --json exit0, all five actual import/startup/registration/filesystem/terminal checks PASS, workflow unresolved=0 in disposable state. No operator DB/state used. |
| verified_contracts | Ten tools have real object-root JSON Schemas and legacy dictionary shape, no added SDK result wrapper. Verification failures/unknown remain reports; skipped stages require a reason; uncertainty never becomes passed. Diagnostics validates bounded LSP full/unchanged reports and UTF-16 ranges after opaque data redaction. Partial complete:false stays false; no invented completion for omitted flag. Router only recommends; permission/dispatcher absent. Unknown persisted job/workflow state denied with output_version_unsupported. |
| proof_pack_correction | Current P03 source returns the native LSP pull-report envelope, not the common testing diagnostic shape. Preserved actual kind/items/resultId/result wire and validated it; no conversion to a fabricated normalized result. Actual strict Content-Length subprocess fixture exercised full then unchanged reports and redacted credential-key canaries. |
| auto_recovery | Initial full run: 6 failed / 2250 passed / 28 skipped. Root causes: two incomplete registry forwarding fixture cases, two legacy tools/list root-schema errors, two PYTHONPATH-shadowed disposable launcher cases. Fixed actual schemas at common WireRootModel; updated existing fixture contract; removed PYTHONPATH injection and moved pytest into a child so exit handlers close log handles before snapshot cleanup. Focused reruns and final full suite all passed. No production launcher/dependency/state fix inferred from the invalid harness attempt. |
| catalogue_bytes | Same serializer: full151 161424 -> 202897 bytes (+41473); coding96 100462 -> 141935 (+41473); coding_core43 47156 -> 84550 (+37394). Names/input schemas/annotations/descriptions and all metadata excluding output_schema have IDENTICAL SHA256 before/after in each profile. Output-schema total for ten tools 42277 bytes; tokens NOT_MEASURED, no latency or model-UX improvement claim. |
| baseline_recovery | P02-03 was already VERIFIED with explicit predecessor revalidation (test_project_cache 0a9486bb...) and 2026-10-19 hosted acceptance; preserved historical 7de7344d discrepancy as unexplained, never fabricated original bytes. Eight current historical predecessor files listed below were revalidated by final full suite; seven digest differences map to later tracked-clean HEAD commits as previous checkpoint records. Fresh baseline advances only now, with current raw hashes and executed evidence; no historical entry rewritten. |
| changed_files | CHANGELOG.md; core/registry.py; core/response_models.py; docs/tooling.md; tests/fixtures/strict_lsp_server.py; tests/test_background_verification.py; tests/test_typed_output_contracts.py; tools/filesystem/models.py; tools/filesystem/registry.py; tools/jobs/registry.py; tools/language/registry.py; tools/language/requests.py; tools/project/intelligence_models.py; tools/project/models.py; tools/project/registry.py; tools/testing/models.py; tools/testing/registry.py; tools/workflows/registry.py |
| file_SHA256 | CHANGELOG.md=83af284f75721edc8cac4d01f831b2489f6f529f7866f39a3d70313359b67455; core/registry.py=8f5ad3121c9e065e526561961ca4f1905c904e71e3f5c3842fa2aa0676bd2c79; core/response_models.py=17198c630987aac82ba13e090a0122a4a90cd16489fd19fffa31f64ac4fb0be4; docs/tooling.md=37e363feb29a7f08994c96147d6e2bb9ef7fc741938dee1ef3ef95fcb7096b55; tests/fixtures/strict_lsp_server.py=a39f8dda54a9668fc86a6cf766caa59f7244e726e48c51a380996fc1b2446b75; tests/test_background_verification.py=328af057991949774cb4f07fa033e81139d614a9e440e278fcd1ad8de7dd7e6f; tests/test_typed_output_contracts.py=2e2421a4c1b835b84852c28e0e0984b034137c29d4b4dc5422dc3c60013091dc; tools/filesystem/models.py=1714490adc04b9e2fae3b5aff19189ff29be5009e4cb8bcfd249fd0607b818e0; tools/filesystem/registry.py=437e4d59a7f2ac8eb9cf239e9d75cb758515f4e9752654a0e248e9fcab8a814f; tools/jobs/registry.py=2adbb7e6a3aa1a292de1aa7b6bf75edb9af715e58d84ae0bf4cdfc9df6a2333f; tools/language/registry.py=d16b360cadcd4a1c4713b30ac23cca2d71ba741c896ffccfb5ab757e585d2f78; tools/language/requests.py=68346237bb404699244f6965c18bfb5ea6085f06a349aeb424eb0bf0d591222e; tools/project/intelligence_models.py=cf7403e70a1d9ebfc2f9c18d070f4758c8ccc3cddb1ab28b896474f0bd5c047d; tools/project/models.py=be3193af87894453de83a031c2ae62892e4f32c1cf7bb17d762e36c2d38b81f3; tools/project/registry.py=33c8e4d11983d8d59586b9434551117ec3b691fc8affd3ce72f448b696b457e3; tools/testing/models.py=1d67a963f15a8da214d43aba11a1a21061bdb78e48613f0a8971e73264bea091; tools/testing/registry.py=a960b6dd6870c5b8095b6148e4e5e49e5d75070e73826ab41f8fb42a81c127ba; tools/workflows/registry.py=877364427af8e9884c27b967ab9f3aaa568cc89f00d051e313d37fae33cb00a8; tests/test_semantic_edits.py=9f4f339d54e741b389e12eb705f992073d4936f68dd08cffc661dff772e098c5; tools/language/edits.py=9ace9c046bbe6154cfa1a0330d54e88c135a21610b6f0c64ffd0423e72d2798b; docs/development/MCP-TOOL-AUTHORING.md=361f3f1f22d50e807027fce2e1af295082c9991bb80cdfd032e00bc90c2f5e21; tests/test_project_intelligence_models.py=94f1ed99f734341a9204ef894673518b020b6cde2b3a4a52e191231bb5e5bfaf; tests/test_project_intelligence.py=b6d9715ff322bf5646516144e8f9836d76db44ef8b48a689d8944c7e4c47ca87; tests/test_lsp_sessions.py=8fadcd8dbf4fa0725465b34cb1eecb3c0c99894b4dd5c9d1bddc02f7bc002315; tests/test_multilanguage_context_registry.py=690fc826be0760f1fad2ef8be076260f2ff226898b44f30fdfb7bf441ca3b60c; tests/test_project_cache.py=73e901fc1491e15b06a4f17f27bddc7a616d126e9c95953d33c3caef8a231428 |
| remaining_gates | P05-02: static implementation retained, live-model/client/version/budget UX pilot unavailable and independently authorized budget required; P05-03: real refresh transcript/design approval absent. P07-02: independent operational/deployment/backup/rollout GO absent. P10-02/P13-01 remain future grounding/delegation design gates; P10-01/P11-01/P12-01 intentionally DEFERRED. No independent eligible task remains. |
| historical_state_limit | Initial 2026-10-24 Doctor attempt has no pre-attempt live DB baseline; historical noninterference remains UNVERIFIED/BLOCKED_NO_BASELINE. Current isolated validation does not retroactively prove it. No rollback, deletion, restoration or reconciliation of operator DB performed. |
| unverified | No hosted checks for this uncommitted diff, no fresh Linux/macOS or real foreign-provider runs, no real-model UX/tokens benchmark, no Full Doctor/release/packaging acceptance claimed. Persistent LSP/DAG activation remains closed. |
| preservation | panel SHA6f682baebf08556a4b5bab2d9d9e34f541ccf6ebbee13abda611a3c9b67bd3d4 and uv.lock SHAe9bbee1693a12ebdde82d62ef98722036e5b14db754842bd6c865375594e56aa unchanged; no staging/commit/push/PR/release/tag/main operation. Canonical plan sole external copy. |
| EXACT_NEXT_ACTION | STOP: all eligible local tasks are VERIFIED; retain 5 BLOCKED and 3 DEFERRED gates. Continue only if missing external evidence/explicitly scoped authority becomes available; do not activate providers/DAG or alter live state. |


### EVIDENCE-1 — Deep remediation reopened P08-01/P08-02 (2026-10-25 Asia/Tehran)

| field | evidence |
|---|---|
| authorization | User explicitly requested fixing deeply everything remaining; IMPLEMENT_LOCAL and no commit/push/PR/release/live-state mutation constraints retained. |
| state | P08-01/P08-02 IN_PROGRESS for reproduced defects; all previous evidence preserved; no future design gate silently activated. |
| RED | Disposable snapshot typed-output regressions: 9 failed, 1 passed. Two empty search pages (offset100 beyond total4 or0) returned output_contract_violation; four advertised JSON Schemas accepted {ok:true} without mandatory payload and {ok:false} without legacy failure identity; object serialization escaped safe ToolError, NaN/Inf silently became null. |
| first_divergence | SearchFilesResult total_count>=offset+count is invalid for an empty page beyond total; common SuccessWireModel required-field runtime checks were absent from published schemas and allowed null payloads; validate_tool_output serialized outside the safe error boundary. |
| evidence_scope | Current graph scoped exact symbols identified shared validate_tool_output/VerificationReport and search adapters; implementation-critical producers confirmed in source. Baseline 2257pass28skip retained as historical, not proof for new edge cases. |
| EXACT_NEXT_ACTION | RED -> shared conditional schema/nullability + safe JSON boundary + empty-page fix -> GREEN, full static, actual transport and full disposable regression; record hashes before VERIFIED advancement. |

### EVIDENCE-1 — User-narrowed P06 closure checkpoint (2026-10-25 Asia/Tehran)

| field | evidence |
|---|---|
| user_scope | User reports 34 percent limit remaining and requests completing only one section then updating the plan. Choose P06 multilingual runner remediation/real acceptance; stop afterward. P08 fixes remain preserved, IN_PROGRESS and not accepted in this turn. |
| recovered_changes | Actual Jest29 returned unknown/exit1 despite a passing fixture; filtered USERPROFILE was empty and broke Node resolution. Private existing report-directory HOME/USERPROFILE restores execution without forwarding operator home or secrets. Native fake regression 1 RED ->1 GREEN. Actual Composer vendor manifests were selected as workspace projects; inventory regression 1 RED ->1 GREEN after vendor was added to shared project exclusion, reusing it in background fingerprinting. Explicit vendor-root reads still work; installed runner metadata still validated explicitly. |
| real_runner_gate | Isolated Jest29.7.0, Vitest3.2.4, PHPUnit11.5.57 and Pest3.8.7: each pass+skip, known failure, zero tests -> unknown, and verify_changes(auto) integration passed after the two root fixes. All outside checkout; Node22.18.0; official PHP8.3.36 ZIP SHA256 matched releases.json; Composer2.10.3 PHAR SHA256 matched official checksum. Installer scripts/plugins disabled; temporary Composer audit override removed and both final strict audits exit0/no advisories/no abandoned packages. No repo dependencies or operator configuration changed. |
| pending | Run task-focused regression/static on final P06 source, update its two states/evidence/hashes atomically, preserve P08 checkpoint and STOP. No full-suite/hosted/platform/production acceptance implied. |

### EVIDENCE-1 — P06 deep runner remediation VERIFIED; user-requested single-section STOP (2026-10-25 Asia/Tehran)

| field | evidence |
|---|---|
| state | TASK-P06-01 and TASK-P06-03 VERIFIED by local revalidation; P06 acceptance enriched with actual foreign runners. P08-01/P08-02 remain IN_PROGRESS, not accepted or discarded. User explicitly requested one section only because 34 percent limit remains; STOP after this update. |
| subject | HEAD 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 / feature/coding-agent-evolution; uncommitted P06 patch only, all earlier unrelated/P08 edits preserved. |
| defects_RED_GREEN | Jest pass fixture failed exit1/unknown: empty USERPROFILE broke Node resolution; restoring a real platform home or omitting empty variables isolated the cause. Fixed by supplying HOME/USERPROFILE=owned temporary report directory, retaining secret/loader scrubbing; regression 1RED->1GREEN. Composer vendor dependencies were inventoried as 27 workspace verification stages, including unavailable ones; shared vendor exclusion removes false packages without hiding explicit metadata or explicit vendor-root reads; regression 1RED->1GREEN. |
| discovery | Scoped graph lookup identified detect_project_types as the shared package inventory; source confirmed its EXCLUDED_DIRS owner is tools/project/service.py and background already excluded vendor separately. Reused one shared exclusion rather than adding another discovery owner. |
| focused_command | Repo .venv/Scripts/python.exe via disposable source: python -m pytest -o addopts= -q --tb=short -rs tests/test_multilanguage_affected_tests.py tests/test_multilanguage_runners.py tests/test_multilanguage_verification.py tests/test_background_verification.py tests/test_affected_tests.py tests/test_project_intelligence_models.py tests/test_tool_profiles.py |
| focused_result | 351 passed, 3 skipped, 0 failed, exit0, 41.18s; skips are Windows file/directory symlink privilege and POSIX virtualenv symlink cases. Both pytest child and outer runner exited0 including cleanup. |
| actual_runner_matrix | Node22.18.0: Jest29.7.0 and Vitest3.2.4; official portable PHP8.3.36: PHPUnit11.5.57 and Pest3.8.7. All four passed actual native-owned pass+skip, known failure, zero tests=>unknown, and verify_changes(test_backend=auto) integration: 16 asserted cases, outer exit0. Pest zero-test exit0 remains unknown, never success. Failure source canary never appears in returned diagnostics. No package test scripts invoked. |
| provenance | PHP https://downloads.php.net/~windows/releases/php-8.3.36-nts-Win32-vs16-x64.zip SHA2563c9242622c7402b47c38d2ec2ed779cef3a290fd6f2bde6c35f9c482699e7900 matched official releases.json. Composer2.10.3 PHAR SHA2567a2d379d5b8ffdaa028580ef26494c36d2feef4b178d3dd1473a4dbc5e17c8d6 matched official sha256sum. All downloads/installed test packages stay under owned external temporary cp-real-runners-kxqtoled, not repo/runtime/PATH configuration. Scripts/plugins disabled; final strict Composer audits exit0/no advisories/no abandoned packages; no advisory override retained. |
| static | Full Ruff PASS/exit0; full mypy core tools scripts tests PASS293files/exit0; compileall PASS/exit0; git diff --check PASS. Documentation wording corrected afterward only to state exactly which home variables are private; no tested executable/test source drift. |
| tested_manifest | Focused suite and final actual-runner matrix share whole-source SHA2566f7f073b180580b2504ed830e2956fe96fc0fbc2984b84c12f755ee5a0a05e63 (380 tracked/owned inputs); current source reconciled exactly except declared documentation-only wording correction. |
| logs_SHA256 | P06-focused-closure.log=c3097a2dc2e183c6af20e17fe179e23edd3bc051d4947214979e13b391a5b356; real-all-P06-final.log=dd3d8040fa5c7d1ba99b6f4e1a1e83f5120a35441280d34554108f4056053548; temporary logs outside checkout; durable decisive evidence retained here. |
| changed_files | tools/testing/runners.py; tools/testing/background.py; tools/project/service.py; tests/test_multilanguage_runners.py; tests/test_project_intelligence_models.py; docs/tooling.md; CHANGELOG.md |
| file_SHA256 | tools/testing/runners.py=1b06d8cd70db3050c04eb52e7d0c862b91d3d1e37ad407ad23b30d31e3e70749; tools/testing/background.py=3762e42b6dbcb75ce96f761426c1eec3fdf364234ec9a47b2fbec9ba4c1b1230; tools/project/service.py=c8773a1e3b085b144dd6b4f4c52bfda0c4d84173f02b15aa819aac105ea7e011; tests/test_multilanguage_runners.py=fa20de6aad42f7448c691b05c507ef9f99da4892cec8c2b91c03fa52b31a3149; tests/test_project_intelligence_models.py=cb083b37143be96a93a4550722d8b8ca2fe42cb2f984dc7916451c8a3ae2d8ab; docs/tooling.md=97d4127f4516a7f06b44c0f9826242f842b707796934fcbe47b9852396eabf77; CHANGELOG.md=9ac0380dbf2a0ffee6114cab1a939eadfe3d6faaf9ce2c131a03dc3019e25e2f |
| preserved | tools/panel/index.html SHA6f682baebf08556a4b5bab2d9d9e34f541ccf6ebbee13abda611a3c9b67bd3d4 and uv.lock SHAe9bbee1693a12ebdde82d62ef98722036e5b14db754842bd6c865375594e56aa unchanged. No live DB/state changes, staging/commit/push/PR/release/tag or provider/DAG activation. No master-plan copy in checkout. |
| skipped_unverified | No fresh full suite, hosted CI, Linux/macOS, operator installations or real-model UX acceptance in this narrowed section. Old2257pass28skip belongs to previous source and is not current full acceptance. Historical initial Doctor live-state noninterference still lacks a pre-attempt baseline and remains UNVERIFIED. |
| EXACT_NEXT_ACTION | STOP now. Next session: P08-01/P08-02 pending fixes in core/response_models.py, tools/filesystem/models.py, tools/project/intelligence_models.py, tools/testing/models.py and tests/test_typed_output_contracts.py. Existing new tests reproduce9 failures then105pass2skip locally, but full/legacy-schema acceptance must run on final current source before marking VERIFIED. Keep 5 BLOCKED and3 DEFERRED design/operational gates unchanged. |

<!-- EXECUTION-LEDGER-END -->
<!-- CHUNK-15-END -->
<!-- MASTER-PLAN-END -->

















### EVIDENCE-1 — Fresh whole-plan audit and P08 diagnostics reopened (2026-10-25 Asia/Tehran)

| field | evidence |
|---|---|
| user_scope | New direct user request: inspect every remaining/blocked/partial section, list findings first, then fix within local scope. Earlier single-section STOP is superseded; no Git publication/deployment/live-state activation inferred. |
| source | Existing checkout feature/coding-agent-evolution, HEAD02796e9e7af9401bbe9fc5eda8ce2dde4980dad8; preserve all existing changes and protected panel/uv files. |
| baseline | Fresh isolated P08-01/02 acceptance: 211passed2platformskipped in17.24s, child/outer exit0; 380-input source manifest e948ec12f9ebc595c4064265c0951d8a4e6116b0db371e3ad679e525ce720356. |
| fresh_finding | DiagnosticReport runtime rejects full reports without items and unchanged reports without resultId/with items, but published JSON Schema omits those conditions. Reopen P08-04 before a regression test and minimal shared-schema repair. |
| catalogue_gate | Same serializer/current source: full208901 versus HEAD161424 bytes; coding147939 versus100462; coding_core90554 versus47156. Names/input/other metadata hashes unchanged. All exceed the plan20percent gate; earlier local acceptance did not enforce it. Keep this gate OPEN, never waive it silently. |
| external_gates | Five BLOCKED and three DEFERRED task gates remain evidence/design dependent; real LSP provider acceptance and historical Doctor no-baseline limitation remain separate. |
| EXACT_NEXT_ACTION | RED diagnostics advertised-schema test; shared model fix; focused/static/legacy protocol/full disposable regression; record source hashes and truthful remaining catalogue blocker. |


### EVIDENCE-1 — Fresh P08 functional closure and corrected rollout blocker (2026-10-25 Asia/Tehran)

| field | evidence |
|---|---|
| state | P08-01..04 local functional checks PASS; each remains BLOCKED on the shared unwaived catalogue20percent requirement.31VERIFIED9BLOCKED3DEFERRED,43total; implemented29/29,accepted24/29. Previous VERIFIED entries remain historical evidence, not overwritten. |
| subject | Existing feature/coding-agent-evolution; HEAD02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 with reviewed uncommitted edits; final380-input source manifest SHA256 7ff2014da6ba700c7b4dbe9061d24b6fd254560df2b294a53e6d5ea7ca59bdf7, recomputed equal after tests. No new commit or hosted acceptance claimed. |
| discovery | Ali Computer Agent read/status and exact anchored edits; codebase-memory coverage metadata matched four cited files and exact-symbol/caller queries located shared validation; scoped Graphify query located the typed contracts. Implementation-critical conditions and registrations verified against current source. No graph rebuild or fresh-index handoff promised. |
| RED_GREEN | Published language_diagnostics schema accepted6 structurally invalid full/unchanged reports rejected at runtime; verification schema accepted2 skipped stages without a nonempty reason.8RED ->8GREEN using actual tools/list schemas; diagnostics coverage includes relatedDocuments. Add conditional requirements to the existing shared models; runtime guards and payloads unchanged. |
| prior_pending_repairs | Existing P08-01/02 conditional success/failure fields, nullable exceptions, safe JSON serialization and empty search pages revalidated; no claim that these preexisting edits were newly authored in this session. |
| focused | Initial P08-01/02 baseline211passed2skipped17.24s; after diagnostics repair233passed2skipped17.60s; final P08+legacy/modern transport/language/native compatibility274passed2skipped46.24s. All isolated source/state; child and outer exit0. One attempted focused command named a nonexistent transport test; no tests ran, corrected to actual test files before acceptance. |
| full_final | Disposable source, repo .venv/Scripts/python.exe; python -m pytest -o addopts= -q --tb=short -rs --durations=10 --basetemp=.pytest-tmp: 2288 passed, 28 skipped in 515.45s;0failed; child and outer exit0 including cleanup. Skips retain Windows privilege/POSIX capability limitations. |
| static | Separate explicit exits: full Ruff0, full mypy0/293files, compileall0, git diff --check0. No executable/test edits after final checks. |
| smoke | main.py --check0,151unique native tools; health_check --json0,all5 import/startup/registration/filesystem/terminal checks true; unresolved0 in disposable state. No operator DB opened by these isolated checks. |
| catalogue_acceptance_FAIL | Same serializer against HEAD: full151tools161424->209326bytes(+29.67percent);coding96tools100462->148364(+47.68percent);coding_core43tools47156->90979(+92.93percent). Metadata excluding output_schema has identical hashes in each profile. Ten typed outputs are structurally valid; the plan20percent budget FAILS and was previously missed. No fabricated pass or silently raised threshold. |
| compaction_review | Isolated in-memory experiment only: remove cosmetic title/description/default/examples from just the ten output schemas, retaining structural constraints. Result full197435bytes(+22.31percent),coding136473(+35.85percent),coding_core80154(+69.98percent); all still exceed20percent. No product schema changed; a simple annotation-only cleanup cannot close this gate. Initial external helper import-path failure corrected before measurement; not a runtime defect. |
| external_backlog | P05-02 needs model/client/version and a separately scoped live pilot budget;P05-03 needs actual notification/refresh/cached-call transcript;P07-02 needs deployment snapshot, backup/restore, same-subject gates and rollout/rollback authority;P10-01/02,P11-01,P12-01,P13-01 retain stated design/defer boundaries. Real Python/TS/PHP LSP providers remain optional UNVERIFIED; an installed pyright executable is not acceptance. |
| benchmark_backlog | B-CONTEXT full100/1000/5000 cold/warm/edit/delete/rename/RSS/cache/1000cycles remains NOT_MEASURED;B-TESTS aggregate ground-truth selection/execution benchmark remains NOT_MEASURED despite retained actual16runner cases. B-PROFILE live-model and B-DAG production performance remain gated. No speed/token/UX claim. |
| hosted | GitHub MCP authentication failed; bounded read-only gh fallback succeeded. PR82 OPEN/Draft, head02796e9e7af9401bbe9fc5eda8ce2dde4980dad8,10success0pending. Those checks cover committed HEAD, not this uncommitted diff. No rerun/push/merge. |
| changed_this_session | tools/language/requests.py;tools/testing/models.py;tests/test_typed_output_contracts.py;CHANGELOG.md;canonical external plan only. All other preexisting work retained; protected panel6f682bae... and uv.locke9bbee16... digests identical. No master-plan copy in any checkout. |
| file_SHA256 | tools/language/requests.py=61b982712b6ab72c2f2ea80d5740c0f32580c165678828c2c018fc6a32310546; tools/testing/models.py=85db9cd171e534a388c312495e26c9e364c8b44b3db7a42ee6744eee76c60b0b; tests/test_typed_output_contracts.py=28494783021ee7b8fc674c38f0ccd734fea6e4b3661323a8730e6e5a872fc6a2; CHANGELOG.md=45a385024c2e5af465495bed58f90ecb26cd15318adfcff4ff4dc7b3465c5de3 |
| diagnostic_log_SHA256 | audit-full-final.log=d8548444bfb6e79dba0ede0a657a345a722d2aa0be15094507ea5a570c171a86; audit-catalog-final.json=42b54c1e136521a4fa3b0efe7207401a166b19ca3a66fccd8c41b4965d9ef2d5; audit-startup-final.log=826b2544d3a9e2d6f81accbaf6630a7e5514e45bd1695ee8ad693102cc8cbe6e; audit-health-final.log=35dbdd2b20d03ada8a3b12140c8cce8b53525b60ccc9211830c287f84b7076e0; temporary artifacts outside checkout; decisive evidence preserved in this ledger. |
| historical_limit | Initial Doctor operator-state noninterference remains UNVERIFIED/BLOCKED_NO_BASELINE; fresh isolated tests cannot prove an earlier absence of effects. No live DB reconciliation, rollback or deletion attempted. |
| EXACT_NEXT_ACTION | Resolve BLOCK-P08-CATALOG-FOOTPRINT through a reviewed compact publication design or explicit budget revision, then rerun affected schema/catalogue acceptance. Supply model/client/budget, real refresh transcript, deployment/backup or grounding/isolation decisions for their separate gates. Do not activate DAG/providers/grounding/delegation or publish current edits without new scope. |


### EVIDENCE-1 — P08 deep footprint/token remediation started (2026-10-26 Asia/Tehran)

| field | evidence |
|---|---|
| authorization | User explicitly requests completing P08 deeply and correctly: actual schema compression and token cost; any budget revision requires a clear evidence-backed reason. Local implementation only; no other feature, Git publication, activation or live-state changes. |
| subject | Original requested checkout feature/coding-agent-evolution, HEAD02796e9e7af9401bbe9fc5eda8ce2dde4980dad8; existing uncommitted work retained. Baseline source manifest7ff2014da6ba700c7b4dbe9061d24b6fd254560df2b294a53e6d5ea7ca59bdf7. |
| discovery | Exact current schema owners are WireModel/SuccessWireModel and WireRootModel; current SDK calls their public model_json_schema directly. Scoped codebase-memory coverage matched cited source, caller lookup found six native registry modules; source confirmed no second registry or transport is needed. |
| baseline | Prior isolated2288passed28skipped515.45s; current schemas48KB aggregate and catalogue budget failure preserved as baseline, not fresh test proof. Global Python has installed tiktoken0.7.0; repo venv has no tokenizer, so token analysis will use the existing separate interpreter without adding production dependencies. |
| method | Capture exact current and committed-HEAD catalogues under inert zero-extension profiles in disposable source; compare annotations-only and structural lossless compaction; count actual cl100k_base/o200k_base tokens on fixed serialized JSON, explicitly not billed/client prompt usage. Test constraints/protocol/native metadata and repeat appropriate full acceptance after edits. |
| state | P08-01..04 IN_PROGRESS; no acceptance or arbitrary threshold waiver. Five external BLOCKED/three DEFERRED tasks remain unchanged. |
| EXACT_NEXT_ACTION | Capture baseline/token/structural measurements, choose smallest safe compaction, add RED constraint-equivalence/byte-budget regressions, implement shared boundary once, then complete focused/full/static/protocol/native and catalogue evidence. |


### EVIDENCE-1 — P08 measured design decision and acceptance budget fixed before implementation (2026-10-26 Asia/Tehran)

| field | evidence |
|---|---|
| reasoned_revision | User explicitly permits a budget change only with clear evidence. The same ten schema definitions add fixed bytes/tokens to full/coding, while coding_core omits workflow_status. A per-profile20percent limit would impose different schema allowances on the same tool merely because unrelated tools were hidden. Preserve full20percent; normalize the identical absolute allowance for all profiles and retain actual relative-growth reporting. No validation loss, schema hiding or input/tool filtering permitted. |
| fixed_acceptance | Baselinebytes full161424/coding100462/coding_core47156. Each profile delta<=32284bytes. Token baselinefull cl100k37436/o200k39414; each profile delta<=7487/7882tokens respectively. These limits are fixed now before RED regression/product implementation, and cannot be raised after a failure. All shared output schemas and all non-output metadata must be identical across the expected snapshots. |
| prototype_rejection | General repeated-subtree $defs factoring reduced bytes but increased o200k tokens compared with simpler compaction: clean47808 versus factoring47982(full). Reject the extra algorithm and opaque aliases. |
| selected_design | Existing common model boundary only: remove schema annotations (never literal/property data), compact simple nullable primitive branches, omit string type already implied by const/enum, and group known non-null conditions once while keeping published properties. Preserve local defs/ref identities and unknown legacy extras, including newline-containing names. No public tool/transport/configuration/dependency additions. |
| baseline_tokens | Installed tiktoken0.7.0, exact canonical JSON: before full48979/51684 cl100k/o200k; coding/core measured separately. Selected throwaway representation full44839/47166; these are exploratory numbers, not product acceptance or live-model UX evidence. All encoding vocabularies hash-verified; analysis external to repo. |
| EXACT_NEXT_ACTION | Add meaningful failing equivalence/metadata/byte-budget regressions, then implement the selected small boundary change and verify actual SDK/protocol output, per-profile catalogue/token budgets, static checks, disposable full suite and smoke. |


### EVIDENCE-1 — Explicit subagent-driven continuation and preflight (2026-10-26 Asia/Tehran)

| unit/interface | consistency and ownership |
|---|---|
| P08-01 self | Existing context/affected models retain fields/nullability/counts; common schema compaction only, no source/parser changes. |
| P08-02 self | Search/summary wire and empty-page acceptance remain unchanged; no new search feature. |
| P08-03 self | Job/workflow/health models preserve union and unavailable/failure identities; no lifecycle/state change. |
| P08-04 self | Verification unknown/failure, advisory Router and diagnostics full/unchanged conditions remain validated; no dispatch or permission change. |
| 01/02 | Share SuccessWireModel schema owner and literal/nullability rules; one production owner, one test/doc worker, no overlapping writes. |
| 01/03 | Share WireModel with JobStatus; full/coding/core metadata and same-tool schema equality required. |
| 01/04 | Share native model publication boundary, not runtime validators; all affected registry callers inspected. |
| 02/03 | Catalogue/profile budgets shared; same absolute full20percent allowance, no profile-specific schema hiding. |
| 02/04 | Typed output compaction independent of package runners/LSP launch; no provider/network activation. |
| 03/04 | WireRootModel handles legacy unions in both; tools/list root object and text/structuredContent parity remain gates. |

| field | evidence |
|---|---|
| Ruling: checkout | Continue the user's explicit original feature checkout, rather than create a different checkout that lacks these uncommitted edits — original workspace and user work are authoritative — interference risk is bounded by file ownership, source manifests and protected-file hashes. |
| Ruling: artifacts | Reuse the sole canonical external plan ledger; keep bounded briefs/reports under owned external temp cp-p08-footprint-20261026, no .superpowers plan copy/linked snapshot or second authoritative ledger — repository/one-writer rules override the skill's scratch layout — temporary report loss is mitigated by persisting decisive evidence here. |
| Ruling: Git | Keep the existing local-only no-stage/commit/push boundary despite the skill template's commit step — no new publication authority was requested — hosted acceptance of this uncommitted diff remains unclaimed. |
| Ruling: review scope | Apply task and final reviews to the complete current P08 unit, preserving already-verified P01-P07 work — user explicitly selected only the first remaining section — unrelated branch defects are not silently pulled into implementation. |
| dispatch | Fresh no-history gpt-6.1-sol/high worker /root/p08_finish owns only six test/doc/benchmark files listed in external brief. Fresh read-only gpt-6.1-sol/high explorer /root/p08_semantics investigates the named equivalence uncertainty. Neither may spawn agents; root alone writes canonical ledger/production core. |
| current_proof | Current production typed suite49passed6.14s; additional metadata-immutability characterization1passed. Actual captured code catalogue full191656/coding130694/core74594bytes. o200k tokens full47166/coding31943/core18373; cl100k full44839. All currently within the pre-fixed normalized byte/token allowances; final acceptance waits for worker, reviews and full verification. |
| EXACT_NEXT_ACTION | Continue from this checkpoint only: worker targeted coverage/benchmark/docs, semantic inquiry, fresh task review (spec+quality), final full/transport/static/smoke and final P08-wide review. Do not redo completed compaction or old phases. |


### EVIDENCE-1 — P08 complete local acceptance after subagent task/final review (2026-10-26 Asia/Tehran)

| field | evidence |
|---|---|
| completion | Task P08 compaction/finalization complete on HEAD02796e9 plus frozen uncommitted source64f82e09bad7f0c59d5c391b5e2d8a5ad893eae640f630e3ee679a5f74367af2; task SpecPASS/QualityPASS and final independent localPASS. P08-01..04 VERIFIED local;35VERIFIED5BLOCKED3DEFERRED43total; implemented29/29,locallyaccepted28/29. No commits were permitted or invented. |
| source_identity |380tracked/owned inputs SHA25664f82e09bad7f0c59d5c391b5e2d8a5ad893eae640f630e3ee679a5f74367af2, recomputed exactly after full suite and every review. Final reviewer independently regenerated21P08diffs and21source hashes against actual HEAD and matched the review package; no relevant source drift. |
| production_change | Only existing WireModel/WireRootModel output-schema hooks compact native generated schemas: schema annotations removed without touching property/literal data; string const/enum redundant types omitted; isolated safe nullable branches folded; exact non-null guards grouped. Runtime validation/dumps/input schemas/metadata/provider eligibility/dispatch/defaults unchanged. Existing benchmark gets only additive actual-wire output_schema_json_bytes. No dependency, schema alias registry, public tool or configuration added. |
| RED_GREEN | Initial normalized budget regression full+47902bytes failed cap32284, and annotation-compaction test failed; corrected fixture-only custom-$defs setup before accepting those RED reasons. Both GREEN after shared-boundary implementation. Original49typed tests passed; worker added8public-model recursive/ref/nullable/conditional cases, final typed57passed6.05s. Existing assertions preserved, including safe serialization and unknown outcomes. |
| focused | Worker combined typed+perf95passed6skipped50.35s on its pre-typing-correction snapshot; scoped mypy found a new test dict annotation issue, fixed explicitly; affected typed57passed afterward. Final full below supersedes that earlier snapshot and proves current perf/typed cases. No fabricated raw logs or duplicate suites. |
| full_final | Repo .venv/Scripts/python.exe with disposable source/state; python -m pytest -o addopts= -q --tb=short -rs --durations=10 --basetemp=.pytest-tmp -> 2298 passed, 28 skipped, 1 warning in 540.96s;0failed; actual outer session85632/chunk7c02e3 exit0 including pytest child and snapshot cleanup. All28skips are explicit Windows-link/POSIX limitations; six perf skips are symlink-unavailable at test_perf_benchmark.py:65. No warning-free claim. |
| static_and_smoke | Full Ruff exit0, full mypy293files exit0, compileall exit0, diff--check exit0 after freeze; actual root tool chunks673003/ea1930/621848. main.py--check exit0/151unique tools; health_check--json exit0/all5actual checks true/unresolved0 in disposable state. Three named disposable source directories absent after cleanup, independently checked. |
| structural_proof | Read-only semantic explorer found no demonstrated defect after current source/validator reasoning, all52localrefs, ten condition branches, recursive schemas, literal data and metadata ownership probes. Separate ECMAScript unicode-mode Node22.18.0 check10patterns570assertions PASS. Native default SDK/Pydantic generation only, not arbitrary external schemas/custom generators or general URI rebasing. |
| byte_measurements | Same canonical serializer and stable151/96/43counts: full209326->191656 versus HEAD161424(+30232=18.7283percent);coding148364->130694 versus100462(+30232=30.0930percent);core90979->74594 versus47156(+27438=58.1856percent). Ten schemas48706->31036bytes, saving17670(36.28percent). Every profile fits preregistered+32284budget; non-output metadata hashes and every shared tool schema IDENTICAL. Smaller profiles are not claimed below20percent. |
| actual_encoding_counts | Installed tiktoken0.7.0, both exact vocabularies hash-verified; canonical JSON only. cl100k full48979->44839, coding34580->30440,core21407->17540; added7403/7403/6780 versusHEAD, all<=7487. o200k full51684->47166,coding36461->31943,core22591->18373; added7752/7752/7090,all<=7882. Final reviewer independently reran both encodings and matched all counts without modifying artifacts. Not billed usage, full client prompts or live-model UX. |
| independent_reviews | User explicitly invoked subagent-driven-development. Fresh no-history gpt-6.1-sol/high implementer /root/p08_finish; read-only semantic explorer /root/p08_semantics; fresh task reviewer /root/p08_task_review SpecPASS/QualityPASS; fresh most-capable gpt-6-astra/high final reviewer /root/p08_final_review localPASS and reconciliationALLOWED. No recursive delegation. Root retained canonical single-writer authority and original checkout. |
| Ruling: normalized budget | Keep original full20percent ceiling and its identical absolute introduced-schema allowance for smaller profiles — the same per-tool definitions have fixed cost independent of hidden unrelated tools, with explicit user permission for a measured revision — cost is disclosed30.09/58.19percent relative small-profile growth and no live-UX assertion. Limits fixed before product implementation were not raised after failures. |
| Ruling: outside warning | Retain the diagnosed preexisting local global-time.sleep test-isolation warning as a known Minor outside this P08 unit — source test patch predates this turn and no P08 defect was demonstrated; preserve unrelated user work instead of silently widening scope — a separate test-isolation fix remains advisable, and this is not general retention/merge readiness. |
| Ruling: headroom | Retain the fixed measured allowance and report narrow84cl100k/130o200k token margin for full/coding — current source passes without deleting constraints — future schema/SDK changes require fresh measured review, not reuse of a waiver. |
| unjudged_boundaries | Final reviewer declined hosted same-subject/platform/FullDoctor/packaging/release/modelUX/clientrefresh, realprovider/DAG/live storage activation, unrelated old phases, custom schema generators/third-party vocabularies/cyclicdicts and full runtime-to-JSONSchema equivalence. These remain UNVERIFIED/outsideP08; no acceptance inferred. Runtime-only cross-field validators and historical Doctor no-baseline limitation retained. |
| Ruling: continuity_and_finish | Existing original feature checkout, external brief/review artifacts and sole canonical ledger retained under prior preflight rulings; no master-plan copy/link in checkout, no secondary truth, no user worktree deletion, no Git/publication/activation action. Keep owned temporary review evidence because work is uncommitted; decisive hashes persist here. |
| changed_this_turn | Seven files: core/response_models.py;tests/test_typed_output_contracts.py;scripts/perf_benchmark.py;tests/test_perf_benchmark.py;docs/tooling.md;docs/development/MCP-TOOL-AUTHORING.md;CHANGELOG.md; plus this external canonical plan. All initial unrelated/P06/panel/uv edits retained; panel6f682bae... and uv.locke9bbee16... digests match. |
| file_SHA256 | core/response_models.py=5d4b3f59013a302aa88853d31eb1229260e1a67d5bf4f20442d7ebb553d2ed05; tests/test_typed_output_contracts.py=e7fed91219556d70a4af81e9fac12cf46ccdff4b3bb2260077dc5773f44078d8; scripts/perf_benchmark.py=2ab0ada4117ebca8df881bd68d43d5989ab220fd8150862d796f0d7d1bde770f; tests/test_perf_benchmark.py=a87c166d0d4faa30d3648919704c3bbcebd8a02d9915edf9ff80bc4379e625fb; docs/tooling.md=1c3c0a8a27623307c7455efa4c76aa1d58aa678f19fa76b4781e69a9b5879274; docs/development/MCP-TOOL-AUTHORING.md=408febdb6ed212ef9d52f97406f91bb03cacae5dd1dc6988131ca6f1cfda1fa3; CHANGELOG.md=485f6eec314109464cff9c113c2ea2a4a68a83fab5658b5f514cbdfe1f31b14d |
| proof_SHA256 | full-final.log=16e0afb907c9d43749c0435cf2f8ea4598947658512292c9f49a80878b668e04; startup-final.log=0156a73707b94242e51fae52878a0ac4988448fe776497f5cb017e1b4a223566; health-final.log=2fdca166c2fce78797d9aa53316ab5088190f1d51039b399cdbd0a016ee111cf; budget-verification.json=b417f1e12e90a5bd59a2454151546510035fe16ef792e5f8ac2b8f934d8b5064; token-measurements.json=04fe5f38e06dc89befb4df22e1ad57d3273d6bc98fcffc60422c8dfb5980a652; semantic-report.md=5f2dee77ea3f4937b728392977ead8e876751e3a4d224d361a78b4a473638d59; task-p08-finalization-report.md=61c084f5231a5030cf9280aecc00ce67c404ac01b81e93cc3ac41ed916230731; task-p08-review.md=89370770ab31248cb3cf9ddce4918d37ada9b218005e04330ed724264ba5c985; final-p08-review.md=fbb7bf55032389708fe77b8ada304b55e1c7768a8eeb6bb38847e04e2c9440e0; review-manifest.json=62f1fa2fb0258408b286500c88a1a08a9d08bc56a4ef97d1ddd8be411a5affe8; owned external temp artifacts; matching decisive facts retained in this ledger. |
| EXACT_NEXT_ACTION | STOP: requested P08 work is locally complete. Next separately scoped work may validate real LSP providers, then aggregate context/test-selection benchmarks and the live-model profile pilot after model/client/budget details. Five external/designBLOCKED and threeDEFERRED gates stay unchanged; do not automatically activate providers/DAG, rewrite unrelated tests or publish Git/release changes. |

---

<a id="real-lsp-provider-acceptance-20261026"></a>
## REAL LSP PROVIDER ACCEPTANCE — 2026-10-26 (initial probe; historical, superseded by verified follow-up below)

این بخش نتیجهٔ **اجرای واقعی** LSP در ویندوز است؛ با تست‌های offline و fake پیشین P02/P03/P04 یکی نیست. وضعیت تکمیلی این سه گیت برای تصمیم عملیاتی مهم است، اما Task ID/وزن/index اصلی 43 task را بی‌اجازه تغییر نمی‌دهد. P08 قبلی همچنان VERIFIED می‌ماند و TASK-P05-02 همچنان BLOCKED است. هیچ کدام از providerها به محیط MCP جاری اضافه/فعال نشده‌اند.

| Gate | Status | Real execution evidence | Missing acceptance |
|---|---|---|---|
| REAL-LSP-PY (Pyright) | **VERIFIED_LOCAL** | Node22.18.0+pyright-langserver.js pinned؛ definition cross-file PASS؛ references3؛ pull diagnostic `full` PASS و **1 diagnostic واقعی بعد از تغییر type**؛ on-disk edit/didChange/version/hash PASS؛ restart/new-generation PASS؛ clean process-tree (no orphan)، workspace removed؛ exit0 | hosted CI و تست Linux/macOS/activation جداگانه |
| REAL-LSP-TS (TypeScript) | **PARTIAL_BLOCKED** | typescript-language-server5.3.0 + TS5.9.3 pinned؛ initialization PASS بعد از تنظیم tsserver.path؛ documentSymbol/references2، edit/version/hash، restart، full tree cleanup PASS؛ run exit1 | definition فقط alias در main.ts، نه service.ts، و pull diagnostics `lsp_request_failed`؛ تا real cross-file و diagnostics حل نشوند VERIFIED نشود |
| REAL-LSP-PHP (PHPActor) | **BLOCKED** | PHP8.4.27 NTScurrent SHA256=93bd8a815a52156606fb906d0573437908c61f96e2cc52c03a51e36a41bf5236. PHPActor2026.07.22.0 bundle native Windows نیازمند ext-posix، startup BLOCKED. PHPActor2025.12.21.1 با PHP local `--version` اجرا شد؛ بدون HOME محدود ابتدا error cache `LOCAL_CACHE_PATH_REDACTED`، با override فقط آزمایشی HOME پاسخ definition `null` بود؛ هر دو acceptance fail. | سازگاری واقعی PHP provider روی سیستم‌عامل هدف و semantic definition/references/diagnostics/edit/restart/cleanup؛ بدون تغییر جهانی PATH یا فعال‌سازی خودکار |

**Cause-and-fix evidence:**
- `tools/language/sessions.py`: مسیر `typescript/lib/tsserver.js` فقط از بستهٔ node_modules **کنار entrypoint پین‌شدهٔ** `typescript-language-server/lib/cli.mjs` با `resolve(strict=True)` انتخاب می‌شود، نه از `node_modules` کنترل‌شدهٔ workspace. SHA مستقل خود tsserver و زنجیرهٔ تأمین بسته همچنان گیت review فعال‌سازی است.
- TypeScript provider واقعی در زمان initialize دارای چهار process، 574..591 handle، RSS حدود 137..150MiB بود؛ سقف عمومی 512 حفظ شد و فقط TypeScript تا 1024 handle افزایش یافت. CPU/RSS/process native ownership و fail-closed همچنان پابرجا هستند؛ تست مرزی provider-specific موجود.
- `scripts/real_lsp_acceptance.py`: ابزار acceptance اختیاری با ساخت fixture زیر `tests/.real_lsp_runs` / `tests/.real_lsp_tools/workspaces` (نه Windows Temp)، Pin SHA256، آزمایش cross-file/diagnostics/edit/restart/child cleanup، خروجی JSON و exit غیرصفر روی ناکامل.
- `.gitignore`: artifactهای npm/PHP و workspace زیر `tests/.real_lsp_tools` و `tests/.real_lsp_runs` قابل commit نیستند. No global provider config / env toggle / state migration or Git publication.
- **Verification:** scoped LSP/offline tests **188 passed, 1 skipped**؛ full Ruff PASS، full mypy295 source PASS، compileall PASS، git diff --check PASS؛ full pytest/hosted same-SHA/other platforms **NOT RUN** برای این task. مرور source، Python/TS/PHP real bench و local memory/graph انجام شد؛ parallel tool probes به معنای subagent مستقل نیست.
- **Repository evidence:** `docs/development/REAL-LSP-PROVIDER-ACCEPTANCE-2026-10-26.md` + `tests/test_real_lsp_policy.py` و runner فوق. Historical P08 prior full2298passed28skipped1warning remains historical, not proof of current diff.

**Historical next action (resolved by follow-up below):** original TS cross-file/diagnostic and PHP provider-compatibility blockers were the next local work. The following VERIFIED_LOCAL record supersedes their initial PARTIAL/BLOCKED status; hosted, other platforms and production activation remain separate gates.

<!-- REAL-LSP-FOLLOWUP-20261026-START -->
### REAL-LSP follow-up — 2026-10-26 (VERIFIED_LOCAL / [x])

- Scope completed: TypeScript and native-Windows PHP reach the same strict definition/references/diagnostics/edit/restart/cleanup criteria as Python. This supplements P02; the original 43-task authority, weights and 35 VERIFIED / 5 BLOCKED / 3 DEFERRED states are unchanged.
- Tested checkout: feature/coding-agent-evolution @ 02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 **plus existing and new uncommitted diff**. No commit was created; HEAD alone does not identify tested source. Manifest digest for the eight LSP source/probe/test paths recorded in the repository acceptance report: SHA-256 323fda0b17ec1731cdce733ec497f766f1a55ebb65a4e7c1db9ba87b640e8c64; checked unchanged after the full suite.
- Parallel execution: three workers owned TS runtime, PHP provider, and acceptance harness; a fourth, fresh read-only reviewer verified integration after implementation. Parent alone wrote this canonical plan. No plan copy, link, staging or packaging inside the repo.

| Real gate | Version / runtime | Final evidence | Status |
|---|---|---|---|
| Python | Pyright1.1.403 / Node22.18.0 | Cross-file definition; references3; clean0→reportArgumentType1→repair0; file version/hash; entire cycle repeated after restart; 4 observed process identities drained; no orphan; fixture removed; exit0 | VERIFIED_LOCAL [x] |
| TypeScript | TLS5.3.0 + TS5.9.3 / Node22.18.0 | Cross-file definition; references3; clean0→TS2345 severity1→repair0; file version/hash; entire cycle repeated after restart; 6 observed process identities drained; no orphan; fixture removed; exit0 | VERIFIED_LOCAL [x] |
| PHP | Intelephense1.18.5 / Node22.18.0 | Cross-file definition; references2; strict_types clean0→P1006 severity1→repair0; file version/hash; entire cycle repeated after restart; 4 observed process identities drained; no orphan; fixture removed; exit0 | VERIFIED_LOCAL [x] |

- Runtime fix: advertise publishDiagnostics/definition support; TS semantic server only and automatic typing acquisition disabled; normalize Windows admitted URI identities; bound, redact, invalidate and return actual publications through the existing diagnostics API. Missing publication is timeout, never a fabricated empty success. Explicit stale versions/foreign docs are rejected; unversioned diagnostics retain standard latest-publication semantics, not invented causal/version evidence.
- PHP choice: additive pinned provider_id=intelephense, PHP-only, required entrypoint, max4 configured providers, existing ambiguity/trust/hash checks. PHPActor POSIX incompatibility on native Windows is historical and is **not** claimed fixed; the supported Intelephense path establishes PHP acceptance. Paid rename/code-action acceptance remains outside this six-capability scope.
- Private state: Intelephense cache/licence lookup stays inside per-session SETTINGS.state_dir; existing interprocess lock gates admission before spawn, max16 directories, each sampled under128MiB/4096entries/0.25sec with no-follow traversal. Cleanup happens after process drain; failure preserves quarantine. Supplemental real-provider os.homedir marker+throw guard also passed with absent marker. No user licence/home forwarding or production configuration activation.
- Harness: fixtures, .state and TEMP/TMP/TMPDIR scratch stay inside tests/.real_lsp_runs; env is restored; failed cleanup retains evidence and exits nonzero. Every final real fixture was removed, pre-existing ignored .cache preserved. Exact path+range and actual call references, clean/error/repaired diagnostics and entire repeated generation are mandatory.
- Independent review: two concrete edge defects (cached result after cancellation/deadline; reopen tracking loss) fixed and regression-tested. Final independent scoped run243passed1skipped68.05s; no remaining actionable finding. The existing skip is symlink-creation permission.
- Final full checks: pytest2338passed28skipped518.06s exit0, no warning/failure in final summary; full RuffPASS; full mypy297sourcePASS; compileallPASS; startup/catalog151uniquePASS; isolated health5groupsPASS; git diff--checkPASS. Initial compileall failure came from owned pytest residue with deliberately invalid Python; residue was safely removed and the exact standard compileall passed. Hosted CI/platform/release gates NOT RUN.
- Changed files: tools/language/providers.py, sessions.py, transport.py; scripts/real_lsp_acceptance.py; tests/test_lsp_providers.py, test_lsp_push_diagnostics.py, test_real_lsp_acceptance.py; docs/tooling.md; docs/development/REAL-LSP-PROVIDER-ACCEPTANCE-2026-10-26.md; CHANGELOG.md. Existing test_real_lsp_policy.py was included in the tested manifest, not newly changed by this follow-up.
- Discovery: Codebase Memory MCP returned Connection closed; scoped Graphify transport neighbors identified persistent and legacy consumers. The index predates these changes, so current source/configuration and executed tests verified implementation-critical paths; no disproportionate rebuild or false graph-freshness claim.
- Preservation: panel SHA-256 6f682baebf08556a4b5bab2d9d9e34f541ccf6ebbee13abda611a3c9b67bd3d4 and uv.lock SHA-256 e9bbee1693a12ebdde82d62ef98722036e5b14db754842bd6c865375594e56aa remained unchanged after final tests. Other pre-existing edits preserved. No staging, commit, push, PR, merge, release, main/tag mutation or live activation.
- EXACT_NEXT_ACTION: requested local follow-up is complete. If separately authorized, collect hosted same-SHA and real Linux/macOS provider evidence, review complete installed runtime/dependency supply chain and operator configuration, then decide production activation; TASK-P05-02/live model and original blocked/deferred tasks remain separate. No further local code task is implied.
<!-- REAL-LSP-FOLLOWUP-20261026-END -->

<!-- FREE-PERSISTENT-EFFICIENCY-20261026-START -->
### Free persistent LSP efficiency — 2026-10-26 (COMPLETE_LOCAL_VERIFIED; LIVE_ACTIVATION_UNVERIFIED)

- Latest authorization: user asks to perform the best practical free setup and avoid repeated LSP requests. This new scope permits preparing reviewed local provider configuration; native defaults, unrelated runtime settings/DBs, earlier source edits and protected files remain preserved. No staging/commit/push/release.
- Baseline branch feature/coding-agent-evolution @02796e9e7af9401bbe9fc5eda8ce2dde4980dad8 plus existing uncommitted diff; prior three-provider and2338passed28skipped acceptance belongs to its recorded manifest, not yet these new changes.
- Current runtime evidence: connected Ali MCP health reports persistent enabled/available but spawn_count0; local .agent_state/config/lsp-providers.json contains pinned Pyright only. Source-to-running-server identity/reload still requires verification; no claim that current process already serves three providers.
- Owners: reuse worker owns manager and bounded existing project inventory extension/tests; activation worker owns ignored reviewed candidate config/operational probe only; Python candidate worker provisioned/compared BasedPyright in ignored repo-local storage; parent alone owns this plan and final evidence docs. Independent final review required.
- Choice pending final checks: retain verified Pyright+TypeScript LS+free Intelephense navigation/diagnostics. BasedPyright1.40.2 passed packaged fixture but stricter implicit-relative-import rule broke original clean gate; tiny timing observations show no demonstrated replacement benefit, not a benchmark. No premium licence/features activated.
- Implementation contract: reuse existing session/context cache owner, no new daemon/registry. Validate before cache hits; retain only proven fresh positive readonly results; mutations/diagnostics/provenance-sensitive paths bypass generic cache. No file/directory mtime heuristic presented as full dependency fingerprint; unavailable/truncated fingerprints bypass reuse. Preserve timeout/cancellation and no replay.
- EXACT_NEXT_ACTION: Local source+tests+candidate validation are complete. LIVE_ACTIVATION_UNVERIFIED is the remaining independent gate: recheck runtime readiness/SHA and separate operator-authorized supervised restart plus post-restart verification/rollback; hosted CI and other-platform release gates also remain open. Do not claim the current connected process has all providers or reduce model-token calls without measurements.

- FINAL_SOURCE_SCOPE: tools/language/sessions.py; tools/project/project_types.py; core/health_snapshot.py; tests/test_health_snapshot_runtime.py; tests/test_lsp_request_cache.py (new); previous local provider/transport changes retained. Single existing manager/cache owner, no new daemon. No Git stage/commit/push.
- FAILURE_AND_FIX: First broad LSP regression run 10failed/208passed/1skipped due to extra preparation on unconfigured roots. Cache-ineligible roots now immediately retain legacy arequest_with_evidence cancellation/deadline path; all ten failures rerun 10PASS. Removed uncertain-fingerprint fallback replay: fail closed after possible dispatch.
- PROVEN_CACHED_DISPATCH: New black-box fixture tests6PASS; two equal sequential calls=one actual dispatch; five concurrent equal calls=one dispatch; source edit invalidates; missing/external config bypasses; deleted source denies; mid-flight missing fingerprint never replays. Large/external/unconfigured project inputs intentionally do NOT claim cache hit.
- REAL_CANDIDATE_SHA256: 09c952ddde6e73eb0faa6f34847060423c2c28fa1f0d5225d61d69e79cd8c78e; LIVE_EXISTING_SHA256: 275a2f14981e2f02a192450e416315191cf1cf481bd39e93c60ec0ccf1cedcd2. Candidate not published; existing Pyright preserved; TypeScript LS+free Intelephense pinned to same trusted checkout. BasedPyright1.40.2 accepted packaged fixture but no measured replacement advantage; Pyright remains default.
- REAL_CANDIDATE_RERUN: current source 151tools; 3 real providers pyright/typescript/intelephense; 3 spawned sessions,18reuses; definition/references3/3/2 and clean/error/repaired diagnostics all PASS; closing active0/quarantined0 and workspace removed, exit0; raw cache hits0 expected because validation deliberately uses evidence requests.
- TARGETED_FINAL: 220 passed,1 skipped; full Ruff exit0; full mypy298 files exit0. DISPOSABLE_FULL_PYTEST: 2345 passed,28 skipped,0 failed in481.34s, exit0. Snapshot 386 files SOURCE_MANIFEST_SHA256 ab3be3bfdccce017d6e6ec9542580d5e5e4c52b5af8a74a6b0a7318676cb5272; isolated under ignored tests/.real_lsp_runs; live .agent_state/memory/uv.lock excluded and owned snapshot cleanup automatic.
- DOC_EVIDENCE: docs/development/FREE-PERSISTENT-LSP-EFFICIENCY-2026-10-26.md. Independent hosted same-SHA CI, Linux/macOS provider testing, operator supply-chain review and production activation are unverified. Connected Ali MCP hosts current control plane, so self-restart could sever independent postcondition checks; do not silently invoke without safe handoff.
<!-- FREE-PERSISTENT-EFFICIENCY-20261026-END -->

<!-- ACTIVE-LSP-AND-CODING-PILOT-20261026-START -->
### Real Windows LSP rollout, model pilot attempts and benchmarking — 2026-10-26

- **WIN-LSP-LIVE: VERIFIED**. Independently scheduled one-shot Python controller (ignored repo-local script) verified SHA256 candidate 09c952ddde6e73eb0faa6f34847060423c2c28fa1f0d5225d61d69e79cd8c78e against live baseline 275a2f14981e2f02a192450e416315191cf1cf481bd39e93c60ec0ccf1cedcd2, pinned executables and provider entrypoints, no active calls/mutations/jobs/workflow leases/uncertain recovery. Backed up original config into ignored tests/.real_lsp_tools/free-defaults/live-providers-before-activation-6900.json; atomically published candidate to .agent_state/config/lsp-providers.json. POST /api/restart via existing authenticated panel, token in memory only. Controller survived runtime drain through Windows Task Scheduler, verified healthy new pid25320 vs13292/generation2 vs1, 151 tools, no quarantine in two stable observations. Evidence live-activation-evidence.json status ACTIVATED_HEALTH_VERIFIED. No need to assume activation merely from local candidate success.
- **DIRECT LIVE MCP: VERIFIED**. Under ignored tests/.real_lsp_runs/live-provider-tool-smoke-20261026, Python/TypeScript/PHP definition all reached source symbols (PHP required source documentSymbol before indexing); references3/3/4; initial diagnostics clean. Actual disk edit induced severity-1 Python reportArgumentType, TypeScript TS2345, PHP P1006 (PHP again required source indexing). Reverted all three owned files, repeated diagnostics0/0/0. Session manager eventually active0/quarantined0/failed0, fixture removed. Live config SHA exactly candidate after successful restart. Cross-platform/native releases not inferred.
- **P05-02: BLOCKED_EXTERNAL_CLIENT_QUOTA**. Real authenticated Codex CLI (logged in using ChatGPT) run in ephemeral/read-only/no-user-config mode returned usage limit exit1 without selection output. Authenticated Claude firstParty OAuth CLI with tools disabled, --max-turns1, --max-budget-usd0.05, no session persistence, timed out75s with empty stdout; no model answer or usage evidence. Therefore 20 tasks ×3 repeats × full/coding/coding_core live client pilot NOT PERFORMED, no success/wrong-tool/token/p95 acceptance or model spend claim. Static 43-tool coding_core registration and no-refresh journey remain locally tested, but task state stays BLOCKED.
- **B-CONTEXT PARTIAL_LOCAL**. Deterministic fixture of 5000 Python files; 3 observed runs per 100/1000/5000 count. Cold ms:3502.625/4879.595/7994.456; warm median ms:1645.477/1961.188/5562.480; max RSS bytes:109756416/118190080/137560064; definition count1 each, limited inventory honesty preserved. Altering owned source changes SHA and returns refreshed source containing9001. No claim of steady-state p95, 1000-cycle soak or real-project cache speedup. Ignored JSON evidence tests/.real_lsp_tools/free-defaults/context-scale-benchmark.json.
- **B-TESTS PARTIAL_LOCAL**. 1000-module Python fixture; change module0000,0500,0999 gives decision full_suite all three; verification planner actually selects full all three; explicit unknown_coverage or no_reliable_test_mapping evidence preserved. Preliminary quick benchmark initially exit1 because its obsolete assertion rejected safe full_suite. Updated scripts/perf_benchmark.py::_impact_once to accept only focused/focused_plus_full_recommended or reasoned full_suite, with returned conservative decision. Exact rerun quick project benchmark exit0. Tests on real-client/test-runner ground truth remain separately needed.
- **P05-03/REFRESH: DESIGN_BLOCKED**. Current connected tool surface has 151 fixed registered methods; restart with same names is not evidence that this actual client supports ToolsListChanged dynamic refresh and stale-cache invocations. Collect live capability/notification transcript before implementing policy/activation.
- **P07-02/DAG: NO_GO**. Panel/recovery at the operational LSP rollout gate reported zero active calls/mutations/leases/jobs/uncertainty, but a DAG-specific backup+restore rehearsal, production schema acceptance, independent GO review and explicit DAG activation authorization remain unverified. DAG not enabled, no live DB mutation.
- **P10/P11/P12/P13**: deferred or design-blocked exactly as 43-task index. No uncontrolled pixel grounding, controller, or child delegation activation.
- **Git checkpoint**: on feature/coding-agent-evolution, seven focused Conventional Commits ac0783a,9aceef3,eab00a8,2e4f9a8,91c8c4d,547460b,5f26cc9 pushed to origin. HEAD/PR#82 head 5f26cc96e70b9e41ba2c4380239be34be9596fc8; 41 code/test/docs paths, staged0. Protected original uncommitted START_MCP.bat, core/resource_claims.py, tests/test_parallel_workflow_resources.py, tests/test_terminal_platform.py, tools/panel/index.html, local evidence .md, uv.lock and all .agent_state files. No merge/release/tag. CI run40271288494, Platform40271288474, Packaging40271288483 started in_progress on this head; await same-SHA full ten checks, then determine next decision (no merge without authorization).
- **Post-benchmark QA**: local Ruff pass, mypy298 files pass, compileall pass, git diff check pass. Focused direct checkout run: 83passed/6skipped/1failed due to resource_admission_timeout while competing with live MCP; exact tests on disposable isolated source rerun 84passed/6skipped, exit0. Earlier 2345passed/28skipped whole-suite is prior source manifest (before benchmark exception-handling edit), not falsely claimed full-suite coverage of new commit. Hosted same-SHA results require separate verification.
- **EXACT_NEXT_ACTION**: when an authenticated model client is available with an agreed usage budget and no rate limit, run P05-02 predeclared balanced live-model evaluation and record actual tokens, p50/p95 and task success. Review current PR head ten hosted checks. Refresh remains gated on actual client notification, DAG remains no-go until backup/restore and authorization, P10/P13 remain design-blocked. No further restart unless release/source/runtime changes require it.
<!-- ACTIVE-LSP-AND-CODING-PILOT-20261026-END -->

<!-- CI-AGY-CONTINUATION-20261026-START -->
### Continuation checkpoint: same-SHA CI, AGY real MCP, and remaining gates (2026-10-26)

- CHECKPOINT: feature/coding-agent-evolution / e19daa707a07a7856f7d73577fd37bdec2111f2c, pushed to Draft PR #82. Eight earlier commits plus this ninth test-only CI fix. No merge, main push, tag, release, or force-push.
- CI first HEAD 5f26cc9: all six CI matrix jobs failed while 3 Platform and 1 Packaging succeeded. Fix 737c61d resolved Python3.10 tomllib fallback, Windows source enumeration race, and POSIX handle/fd fixture; both Windows CI jobs, 3 Platform, 1 Packaging passed. Four POSIX CI jobs still rejected Windows-only frozen metadata SHA.
- CI fix e19daa7: Windows-only metadata SHA/baseline now scoped to Windows, POSIX checks stable tool counts, names and a separate bounded schema overhead. Local relevant pytest 140 pass / 1 skip; Ruff whole repo, mypy298, compileall and diff check PASS. Third same-SHA CI 40275664050, Platform 40275664012, Packaging 40275664022 started, not represented as completed green.
- LOCAL COMPLETE SUITE before final test-only CI fix: 2353 PASSED, 28 SKIPPED, exit0, 526.02sec on isolated 386-file source manifest SHA256 2c2bf5de06b8fc9227495fb9f21a7bc281701785ee356fbddfd61a0418b12c63. No claim these numbers are for a different commit.
- REAL AGY MODEL CLIENT P05-02 MICRO-PILOT (PARTIAL; original task remains BLOCKED): AGY CLI1.4.0 authenticated to gemini-3.8-flash-low, separate from Antigravity desktop2.26.0. Created three ignored, isolated MCP stdio workspace profiles with native tool counts 43 coding_core, 96 coding, 151 full; each ran real read-only computerpilot.project_summary, exactly one completed MCP call in each plus one extra built-in read_file; each accurately identified Python301 and PowerShell2. No external repo mutation.
- CODING_CORE: 16.47s, input7785, cached-read75639, output170, SUCCESS. CODING: 18.78s, input32732, cached-read50208, output175, SUCCESS. FULL: 20.00s, input33010, cached-read50761, output175, SUCCESS. Results stored in ignored tests/.real_lsp_tools/agy-profile-pilot/<profile>-permissioned-result.json. Each run permitted only mcp(computerpilot/project_summary) via a brief AGY CLI permission entry, and restored original settings bytes exactly; never a wildcard grant or dangerously-skip-permissions.
- P05-02 NOT ACCEPTED: one scenario per profile with order effects and different cache-read amounts; no balanced task set, no repeated samples, no p95, no controlled model cost, no wrong-tool survey, no trustworthy causal performance/token comparison. Available AGY removes old complete client-unavailability barrier but not acceptance gate.
- B-CONTEXT/B-TESTS: 100/1000/5000-file cold/warm memory and source edit freshness evidence recorded previously. A 1000-module selection fixture correctly fell back to full_suite. Missing representative multilingual truth sets, 1000-cycle longevity, false negatives and production p95.
- P07-02 DAG: isolated read-only backup and copy/restore of existing workflows.sqlite3 succeeded: schema 11, 12 tables, integrity ok and identical backup/restore SHA256 08ccf4ff34dc847d0a79ca92e39a8f7b04f45a5f9ddc228415defc300ce26585, 9273344 bytes, live DB unchanged and temporary copies cleaned. This is not proof of rollback of real DAG side effects, stress, or GO permission. DAG is still NO_GO and disabled.
- P05-03 dynamic client refresh: actual tools/list_changed receipt and use by client not yet demonstrated; fixed profiles remain sufficient for the local pilot. Dynamic activation NOT performed.
- Visual Grounding, Controller, Delegation: remain design-gated on provider identity, privacy, isolation, authorization and bounded costs, not activated.
- NEXT ORDER: first verify all ten new hosted same-SHA checks and correct any failures; then full AGY pilot under stated spend limits; then context/test-groundtruth acceptance; then P05-03 refresh proof; then P07-02 GO/NO_GO and deferred visual/delegation. Preserve operational .agent_state, secrets, nonrelated local edits and 43-task state index.
<!-- CI-AGY-CONTINUATION-20261026-END -->

### EVIDENCE-1 — Resume audit and TASK-P14-01 IN_PROGRESS (2026-10-26 Asia/Tehran)

- Direct user request authorizes continued local implementation in the named checkout. User permits AGY or this session; paid-model budget is not inferred.
- HEAD and Draft PR82 head e19daa707a07a7856f7d73577fd37bdec2111f2c; fresh gh pr view: all ten checks SUCCESS, CI40275664050 six, Platform40275664012 three, Packaging40275664022 one. These checks exclude current uncommitted edits.
- All ten pre-existing changed/untracked files preserved; external hash baseline Temp/cp-evolution-resume-20261026-baseline.json. Current checkout reused; no staging/commit/push/main/tag/release/restart.
- Graph exact lookup found current run_benchmarks lines2206-2494; nested registrar symbols absent, scoped source resolves missing tool contracts. No graph rebuild needed.
- Scope: scripts/perf_benchmark.py, tests/test_perf_benchmark.py, existing CHANGELOG.md and this external plan. Owned isolated source/state only; no live DB or DAG flag changes. P05-02 remains unaccepted.
- EXACT_NEXT_ACTION: RED equivalence/error/isolation tests; exec_cost suite; randomized paired warmup5/repeats30; focused/full-static validation; read-only DAG provenance and honest metric limitations.

### EVIDENCE-1 — TASK-P14-01 VERIFIED_LOCAL / B-EXEC-COST (2026-10-26 Asia/Tehran)

- Scope completed: read-only DAG provenance and existing opt-in exec_cost suite. Exact HEAD e19daa707a07a7856f7d73577fd37bdec2111f2c; uncommitted whole-diff SHA256 804f85fc9e1b53c59060681d1122f232d784ec4810a3e58550f08ec814d8a2f2. No branch/staging/commit/push/PR-readiness/main/tag/release mutation.
- Changed only scripts/perf_benchmark.py, tests/test_perf_benchmark.py, CHANGELOG.md and this external canonical plan; generated report remains outside checkout. All10 pre-existing changed/untracked file hashes match external baseline.
- DAG provenance correction: core/config.py:71 defaults False; current shell environment and User/Machine registry contain no DAG override. START_MCP.bat:23 calls existing .agent_state/runtime-settings.cmd whose line5 sets MCP_WORKFLOW_DAG_ENABLED=1. Current bootstrap process identities7540/16076 also expose DAG=1. This proves the current operator override, not its historical author/time or GO authority; previous claims that live DAG is disabled are superseded. No flag/environment/live database/service was changed.
- Discovery: available codebase-memory exact graph located run_benchmarks; missing nested registrar coverage was resolved by scoped current-source reads of filesystem/terminal/jobs registration, JobStore/scheduler and worker contracts. No runtime graph dependency or rebuild introduced.
- RED:2newtests failed on absent helper/suite. GREEN final focused: python -m pytest -o addopts= -q tests/test_perf_benchmark.py -k exec_cost --basetemp=.pytest-tmp-plan-p1401-final =>6passed,44deselected,exit0,26.60sec. Surrounding module =>44passed,6skipped,exit0,60.48sec; skipped optional/platform tests are not exec_cost acceptance. Earlier -p no:cacheprovider run produced only known unknown-cache_dir config warning; final tests use normal configured cache.
- Full Ruff PASS; full mypy core tools scripts tests PASS298files; compileall core/tools/scripts/tests PASS; git diff --check PASS. Full repository pytest/startup/Doctor/new hosted checks NOT RUN for this benchmark-only patch; hosted10green applies only committed e19daa7. No external reviewer claim; source/diff and failure-path review performed in this session.
- Final command: .venv/Scripts/python.exe -m scripts.perf_benchmark --suite exec_cost --runs 30 --include-jobs --output <TEMP>/computerpilot-p1401-exec-cost-final-20261026.json,exit0. Owned copy142trackedPythonfiles; manifestSHA256 80607420e3fc1e395d0872c511c114596322d556f2570bc26911717c0d0b08cf. All360measuredsamples plus60warmups passed output/effect hashes. Fixed seed1401 randomized paired order. Isolated defaults and explicit DAG/LSP-off/full-profile override; no live state DB access. All worker/payload PID+creation-time identities dead before cleanup. Failed fixtures are retained with safe fixed error, no raw stderr.
- Durable report: LOCAL_PATH_REDACTED; SHA256 e0eecb69816c8622eeae0677a02edeb2498d607fb368ad01d1caab7ef33fcdb4. Runtime/load/binary hash and per-sample durations/admission wait/process roots are recorded. Start hostCPU4.2percent/memory64.8percent; shared host is not a controlled production SLA. p99 with30samples is exploratory. Timings cover in-process SDK/server paths, not network/tunnel/model selection.
- Measured launch roots: native/edit0, directPython/PowerShell1, durable worker+payload2 from returned/persisted PIDs; OS helper processes are excluded. Parent claim wait is observed; worker claim, exact SQLite and serialization phase decomposition remain NOT_MEASURED for P14-02. The patch variant uses a whole-file hash guard in addition to exact hunk validation; sequential replacements use exact-count guards. Both run on isolated uncontended equal preimages; no claim that their concurrency/rollback guarantees are identical.

| Equivalent fixture | p50 ms | p95 ms | p99 ms (exploratory) |
|---|---:|---:|---:|
| read/native | 1.095 | 1.355 | 1.379 |
| read/python | 101.159 | 152.242 | 167.539 |
| read/powershell | 303.945 | 415.948 | 493.018 |
| search/native | 3.78 | 4.385 | 4.891 |
| search/python | 99.888 | 124.21 | 129.744 |
| search/powershell | 338.022 | 390.392 | 399.8 |
| edit/sequential | 95.48 | 113.126 | 129.168 |
| edit/patch | 28.477 | 36.085 | 38.672 |
| short/direct | 95.734 | 116.452 | 124.297 |
| short/job | 996.811 | 1119.2 | 1139.631 |
| medium/direct | 286.454 | 300.766 | 303.03 |
| medium/job | 1233.781 | 1415.593 | 1583.635 |

- FileSHA256: scripts/perf_benchmark.py=4ae99e698cff7e974c0d14a698ecda2677f5b7f66f8b5f95d77dedf852c570a0, tests/test_perf_benchmark.py=d051563c07c1d89f4ea73991583fb81077d337a39258f6026d8f68be4b21cb46, CHANGELOG.md=2bec7067db6dc80218059863e93ebe54e0cbef29103ea375c7b18c175855b070.
- Acceptance is limited to benchmark correctness/isolation/provenance. No production speedup, model success, missing timing metric, or DAG operational acceptance is inferred. P14-01 weight0 means weighted implementation29/32 and verified28/32 remain unchanged; Index counts36VERIFIED/4NOT_STARTED/6BLOCKED/4DEFERRED,50total.
- EXACT_NEXT_ACTION: TASK-P14-02 existing Timing/Audit/Job trace propagation, with versioned private metadata excluded from command idempotency fingerprints, no public wire changes, disabled-timing/privacy/concurrency/worker-failure tests and exact interval boundaries. Then P14-03/04/07; retain conditional05/06 and original external/design gates. Preserve all pre-existing changes and live DAG until separately authorized operational decision.

### EVIDENCE-1 — TASK-P07-02 IN_PROGRESS; explicit DAG activation authorization (2026-10-26 Asia/Tehran)

- DIRECT AUTHORIZATION: user requests continuing and explicitly granting DAG activation in the master plan, and completing remaining work required for full activation. Operator authorization is now present; absence of authorization is no longer a blocker. This does not fabricate technical acceptance or waive required checks.
- Existing local DAG flag is already1; source default remainsFalse. Reuse current checkout and live generation349c25ec8f1d473589a2d49ec16eb02f; preserve tunnel/settings/limits, existing user edits, recovery fences and history. No restart unless needed by verified source/config changes.
- Read-only live snapshot:151tools,workflowDAGtrue,cap16; zero active workflow leases/running/queued/uncertain workflows/unresolved operations; operation recovery pending0/uncertain0; resource quarantine0. Degraded reason is unrelated AST cache pressure (67017112/67108864bytes); do not erase caches or raise limits merely to turn health green.
- History of actual prior public DAG activation is present in user memory; it informs discovery only. Fresh tests/snapshots, backup+restore, same-subject CI and live disposable canary remain required evidence.
- EXACT_NEXT_ACTION: isolate current-source DAG/native-ownership/recovery regression from operator state; read-only SQLite backup+restore validation; check exact hosted subject and local DAG diff; complete safe live read-only parallel/join canary, rollback-admission rehearsal in disposable runtime, and record GO only to the extent gates are actually proven.

### EVIDENCE-1 — DAG live parallel proof and P14-01 portability regression (2026-10-26 Asia/Tehran)

- TASK-P07-02 operator approval persists. Fresh347DAG/ownership/migration tests passed,2platform/optional skips; full current-source regression still running.
- Live read-only canary c646fcf868f04bd19385a119c4af04f4 completed3/3. Separate loopback barrier canary fbee943efad44b5787a3ea1dadd57c2d completed3/3, actual observed peak2 and join-after-both true, no remaining admission. Temporary loopback server stopped. No production command/mutation/side-effect replay was used.
- Backup/restore: workflows schema11 and jobs schema4 integrityOK, copied restore opens with exact table counts retained. Jobs live main/WAL hashes unchanged; workflow main/WAL changed while service remained live, so historical byte noninterference is NOT claimed. Our live access used only SQLite mode=ro/query_only and backup API; no live migration/recovery/restore.
- P14-01 NEEDS_REVALIDATION: full disposable source copy has no .git; _exec_cost_once incorrectly depended on git ls-files. Reproduced exact CalledProcessError exit128 and meaningful RED with no-Git assertion. Reuse bounded source enumeration instead of making benchmark depend on Git or creating fake Git metadata. Existing timing numbers remain historical frozen-source evidence, not retracted or relabelled as final-patch measurements.
- New user steering: deeply evaluate bounded Native Batch and add/execute appropriate work in this same external master plan while continuing DAG. Fixed read-only capability scope, bounded per-item outcomes and no generic arbitrary executor are required; add a separate design track after source-grounded gap analysis.
- EXACT_NEXT_ACTION: repair only benchmark source snapshot enumeration, verify RED/GREEN and isolated full regression failures before technical DAG acceptance; preserve live flags/tunnel/limits and all previous edits.

### EVIDENCE-1 — Native Read Batch design recorded; DAG work continues (2026-10-26 Asia/Tehran)

- Direct user steering accepted without replacing DAG work. P15 adds4tasks and2milestones; only P15-02 has implementation weight1. No batch tool/runtime/profile/extension was activated or coded in this update.
- Existing specialized batching, typed workflow allowlist, read_file/get_file_info/search_files wrappers, 25-edit safe_refactor and Git helper/environment behavior inspected in current source. The graph located execution/store/lease paths; nested registrar coverage was insufficient, so scoped source resolves SDK/profile and native helper boundaries.
- Decision: bounded file-read first; mixed Git/search/metadata is conditional. Preserve original native schemas and coding_core allowlist until measured publication decision. Existing API names do not prove multi-operation support or client roundtrip savings.
- Corrective validation: initial full copied source run2357passed/28skipped/4failed. Two benchmark failures were genuine no-.git portability defects; two PowerShell launcher failures arose from validation runner PYTHONPATH overriding fake scripts.bootstrap. Original venv pip check PASS. Fixed benchmark bounded source enumeration and removed runner-only PYTHONPATH; final isolated reproduction/full pipeline running at current manifeste50452ebcfd2f1a00c1039a4065a0367d352043f5fa99183301836523658e5d1. No launcher/test bypass or global dependency changes performed by this repair.

### EVIDENCE-1 — DAG cap16 live acceptance and final isolation corrections (2026-10-26 Asia/Tehran)

- Live workflow1db76ed1c85d417fb8d18214e139aaec completed17/17 with configured/effective cap16. Controlled loopback HTTP barrier observed all16 branches concurrently, then join; active admissions0. Fixture server was stopped; no production side effect, process replay, tunnel change, restart or limit change.
- Final current-source full regression before the last isolation fixes yielded2360passed/28skipped/1failed in532.65sec: test_success_invalidates_context_and_lsp. Standalone reproduction passed, but injecting an unrelated quarantined global resource fence reproduced resource_quarantined. The semantic-edit fixture isolated backup/journal but not config.SETTINGS workflow DB; it now isolates state_dir too. Under the same poison fixture all5semantic tests PASS and the unrelated fence remains quarantined. No runtime fence was cleared to fix a test.
- Additional benchmark RED/GREEN: valid admitted parent capability made owned benchmark children reuse the wrong authority. Parent admission/ownership remains unchanged; only the trusted owned fixture bootstrap drops the inherited capability before importing its fresh runtime. Existing real-SDK regression now asserts parent fence active during execution and released only on normal parent exit; all6exec_cost checks PASS,31.52sec. Malformed parent capability correctly remains rejected before outer dispatch; that guard was not bypassed.
- Final frozen copy386files manifest6227454fbbbe193e9b860ee7632e2cab56281624da28190bec6cc353f0310676; full Ruff/Mypy298/compileall PASS; full pytest still running. Relevant existing runtime corrections will be selectively checkpointed on the existing feature branch so required same-subject CI can run. Exclude unrelated panel/uv/local docs, never the canonical plan/state/secrets/backups. No merge/main/tag/release authority inferred.
- Batch design strengthened with server-wide admission/worker/buffer bounds, beyond per-request limits; no new production Batch API was added.

### EVIDENCE-1 — Final local acceptance and feature-branch checkpoints (2026-10-26 Asia/Tehran)

- Final frozen source386files, manifest6227454fbbbe193e9b860ee7632e2cab56281624da28190bec6cc353f0310676: full pytest2361passed/28skipped,exit0,539.04sec; full Ruff/Mypy298/compileall PASS. Prior failures are retained above with actual RED/GREEN evidence; no test exclusion or product guard weakening.
- TASK-P14-01 revalidated VERIFIED_LOCAL after bounded Git-independent source copy, private fixture-capability isolation and regression checks. Original30repeat timing report remains its own frozen-source measurement; final source functionality is verified, no invented replacement timings.
- Source bytes matched frozen manifest before checkpoint. Selective commits: d17ebfe (runtime admission/launcher/project-fixture guards and semantic-test state isolation),31c11aba00bbd29825a1e841450ab3996121bf44 (bounded native execution cost suite/tests and changelog). Pushed only existing feature/coding-agent-evolution branch. DraftPR82 retained; no main/merge/release/tag/force operation.
- Excluded/still-local: tools/panel/index.html,uv.lock and two pre-existing LSP acceptance/efficiency docs. The canonical plan and all state/secrets/backups remain outside Git.
- Remote push accepted e19daa7..31c11ab. Immediate gh pr view was still cached at e19daa7 with old10green; those checks are NOT acceptance of31c11ab. Await exacthead check-runs and tested merge-tree identity.
- DAG is operationally enabled with explicit user permission, confirmed live cap16 with17/17completed and zero remaining admissions; final formal GO waits only matching technical evidence, not renewed operator authorization.
- EXACT_NEXT_ACTION: collect ten exact31c11ab hosted gates, repair any verified in-scope failure, recheck live schema/config/zero uncertainty and record final P07-02 decision. Then P14 tracing and the recorded file-first Native Read Batch track; no generic dispatcher, mixed mutation batch or host-lock relaxation.

### EVIDENCE-1 — TASK-P07-02 VERIFIED/GO; TASK-P14-02 IN_PROGRESS (2026-10-26 Asia/Tehran)

- All10checks SUCCESS on31c11aba00bbd29825a1e841450ab3996121bf44: CI40287892138 sixOS/Python jobs; Platform40287892179 threeOS jobs; Packaging40287892247. GitHub API exact commit check-runs retained externally. One observer retrieval failed transiently; one evidence-based retry succeeded10/10, no CI failure or fabricated cached acceptance.
- PR synthetic merge2a418f549b6591bceb7a00d49e6478f120848e7b has exact same treeca07301bfbd3539e475caacdaffe90fa977a6b26 as finalhead31c11ab; basee66c8c9 unchanged. Full local2361PASS28SKIP and native-focused347PASS2SKIP retained with precise source manifests.
- Current Windows GO decision stored outsidecheckout: Downloads/ComputerPilot-DAG-Activation-2026-10-26/DAG-ACTIVATION-DECISION-2026-10-26.json. Config SHA256658c3845d4904e637736a87d46d0419ce0dd3d60c95e7d968e320c4850995754; schema11; immutable backup/restore refs; explicit operator permission; existing DAG guards independently reviewed in this session against prior-authored changes and executed regression. No external-human review claim.
- Live cap16canary1db76ed1c85d417fb8d18214e139aaec completed17/17 with measured16overlap and post-dependency join; no leases/running/queued/uncertain/unresolved operations and quarantine0; configured numeric budgets unchanged. Source defaultFalse preserved; no service/tunnel restart or mutation, no main/merge/release/tag. Cache-pressure warning remains independent of DAG.
- One remaining pending native.testing.project admission e2a7a130d1f9 was inspected: native owner dead by PID/create-time, workflow/operation links absent, statepending (no dispatch), nonreleasedchildcapacity0. Exact-owner release_resource_request safely changed it to released, retaining history and durable audit. No quarantine/active effect was cleared; detailed safe evidence stored externally.
- User renewed direct instruction to execute the rest of this plan. Start TASK-P14-02; authorized source scope: core/timings.py (existing trace/timing owner),core/audit.py,core/tooling.py,core/jobs.py,scripts/job_worker.py,core/job_scheduler.py,tools/jobs/registry.py,core/resource_claims.py and newtests/test_correlated_tracing.py. Existing core/executor process_spawn/process_execution spans are reused rather than duplicated. No database schema migration or new persistent journal/registry.
- EXACT_NEXT_ACTION: RED tracing identity/correlation/disabled-telemetry/idempotency tests; implement versioned private JobSpec trace metadata after command fingerprint; restore explicitly across scheduler/worker; link existing audit/timing sinks with safe opaque identifiers and exact bounded phase measurements; then focused/static/wire/lifecycle verification.

### EVIDENCE-1 — P14-02 resumed after capacity interruption; working checkpoint (2026-10-26 Asia/Tehran)

- User explicitly requests resuming the half-finished P14-02 and remaining plan, preserving edits and prohibiting merge/tag/release without approval. Current branch feature/coding-agent-evolution, HEAD31c11aba00bbd29825a1e841450ab3996121bf44; prior DAG GO remains accepted for that subject; tracing changes are uncommitted and not claimed active in the existing service.
- Recovered final running check:13passed in20.49sec (new trace cases plus one-worker-connection regression). Latest focused SDK/jobs/scheduler/wait run44passed in24.64sec. Earlier broader run98passed/2skipped/1failed exposed an extra worker SQLite connection; fixed by restoring trace through the existing runtime connection. No current full-suite/hosted acceptance claimed.
- New13-case trace coverage: concurrent per-invocation IDs; audit/timing correlation; invalid/future/private metadata rejection; entropy/enqueue failures; idempotent submit with private metadata excluded from fingerprint; actual MCP validation denial; DAG thread propagation; post-commit audit failure; real subprocess success/timeout/legacy/timing-disabled with canaries across audit/timing/health. Legacy rows deliberately join via job_id rather than fabricated originating IDs.
- Graph exact lookup located bind_operation_recovery,WorkflowExecutor._run_dag_attempt and _run_durable_job; current source showed DAG pool lacked context propagation while verification already used copy_context().run. Meaningful RED observed empty trace in both branches, then GREEN after applying that existing pattern without removing explicit recovery-owner binding. Added core/workflow_executor.py to necessary scope; existing DAG scheduler/recovery suite46passed.
- Audit/timing IDs do not grant policy/recovery/resource authority. Public schemas/payloads and job fingerprint inputs remain unchanged. Job submit completion observation is best-effort after persistence, with a regression proving telemetry failure cannot turn a queued job into failure. Native process execution/spawn and serialization probe intervals are reused and documented; no network/model elapsed claims.
- Updated existing docs/configuration.md and CHANGELOG.md for observable behavior and exact interval semantics. Source static checks already PASS (Ruff,full mypy299,compileall); final task checks must run after these final edits.
- Work-in-progress diff SHA256 2f9ae74699e8e3f223e96c769a9cbb6cab130b8deec3ac69106e75d2fe8a92ca. Preserve tools/panel/index.html,uv.lock and two pre-existing untracked LSP docs. No merge/main/tag/release.
- EXACT_NEXT_ACTION: run focused tracing + job/worker/lifecycle + native zero-activation/wire + DAG denial/recovery checks in a fresh owned source/state snapshot including tests/test_correlated_tracing.py. Review all changed files, run full static/full pytest and native startup/health smoke, then feature-branch checkpoint/ten exacthead gates before VERIFIED advancement and P14-03/04/P15 continuation.


### EVIDENCE-1 — P14-02 local acceptance; feature checkpoint d2752c8 (2026-10-26 Asia/Tehran)

- Commit/push d2752c812a0ed9e975c9f1cd92cc31aa02798dbc on feature/coding-agent-evolution, Draft PR82; exactly12 scoped files. Preserved unrelated panel/uv/two LSP docs. No merge/main/tag/release, no service or tunnel restart, no numeric limit changes.
- Isolated functional-source snapshot cp-p1402-20261026-867jazl4, manifest4541dd4e90275e22e623c758e44e5381769c3f87fd3de16d714e09f821b83bdb: focused495PASS2SKIP; full2374PASS28SKIP531.39sec; full Ruff, mypy299, compileall, startup151 and all5health checks PASS. Skips are explicit platform/symlink limitations, not passing executions.
- Final test-only delta adds crash and fresh JobStore restoration plus timeout-worker audit linkage. Latest14trace cases PASS25.58sec; new timeout assertion PASS1/13deselected5.59sec; final test Ruff/Mypy PASS. Source code identical across snapshots; final manifestf67f0f550a8aba617b6b2746e32a14ac147f7cbca29650e3fd8cab27a2464734 retained at cp-p1402-20261026-xk20b48z. Full final14-case ordering will also run on the exact hosted subject; no fictional full2375 claim.
- Unchanged current catalogue: full151/191656bytes, coding96/130694, coding_core43/74594, minimal32/49798; actual SDK serializer fixed/sorted UTF8, schemas and existing public payloads unchanged. Correlation remains private metadata outside command fingerprint, durable worker restores it using the existing single SQLite connection.
- Live MCP health observed healthy with generation1298b05db068426b930e2c2b7736ce37/toolcount151; generation changed since earlier DAG evidence without any restart issued here. Do not infer which source is loaded or claim tracing activated. Local runtime settings SHA remains658c3845d4904e637736a87d46d0419ce0dd3d60c95e7d968e320c4850995754.
- Graph lookup for IntentRouteRule returned Connection closed; use scoped current-source fallback for P14-03 relationships. Router owner imports intent_route_rules from core/tool_catalog.py; public planning entrypoints are core/registry.py; no dispatch or new public cost field is needed.
- EXACT_NEXT_ACTION: collect ten exactd2752c8 checks and compare PR synthetic tree, then mark P14-02 VERIFIED and P14-03 IN_PROGRESS. Preserve current feature branch and original changes; implement bounded description guidance and canonical intent rules using existing cost/latency tiers, then catalog/denial/typed regression.


### EVIDENCE-1 — P14-03 independent unit IN_PROGRESS (2026-10-26 Asia/Tehran)

- P14-03 depends only on VERIFIED P14-01/P08-04, not P14-02. P14-02 exactd2752c8 hosted gates currently6success/4pending/0failed; do not overwrite that subject before collecting its checks. Begin independent local work without marking pending acceptance complete.
- Scope after scoped-source fallback: core/tool_catalog.py owns canonical intent rules and existing cost/latency tiers; core/execution_router.py consumes them; tools/terminal/registry.py owns tool descriptions; core/registry.py exposes advisory APIs; docs/tooling.md lists intents. Existing response model accepts these tiers, so no speculative cost_class field or public schema change. Test owners tests/test_execution_router.py,tests/test_execution_catalog.py,tests/test_typed_output_contracts.py.
- Preserve old metadata hashes by normalizing only explicitly changed descriptions back to frozen baseline and separately enforcing per-description/aggregate growth; never blindly rebaseline unrelated metadata or waive P08 limits. Add no command parsing, secret-bearing notice or execution fallback.
- EXACT_NEXT_ACTION: RED native/direct/PowerShell intent and denied-capability tests, implement fixed rules/guidance, then affected catalog/profiles/typed tests and byte measurement. Preserve current unrelated files and no merge/tag/release.


### EVIDENCE-1 — P14-02 VERIFIED; P14-03 local acceptance; P14-04 IN_PROGRESS (2026-10-26 Asia/Tehran)

- All10exactd2752c812a0ed9e975c9f1cd92cc31aa02798dbc hosted checks SUCCESS: CI40311341205 sixOS/Python jobs, Platform40311341382 threeOS jobs, Packaging40311341287. PR synthetic mergea5ac6221a3c0f4981c9eb2e845646344ace1e065 has exact head tree3c22b175d6875f6b9b24b525f5699b229c2551a6; basee66c8c9 unchanged. No merge was performed. Evidence retained externally in Downloads/ComputerPilot-P14-Acceptance-2026-10-26.
- P14-03 RED2fails unknown process.powershell, GREEN21router/catalog tests then110PASS1SKIP affected router/catalog/typed/profile/terminal tests in isolated snapshotb19e45618da26e19d148a1bd9deab432c2cc7c1b739db052c2eb99c27c11f25e. Full Ruff/mypy299/compileall PASS. Actual rule owner core/tool_catalog.py reused; core/execution_router.py and response_models.py require no new code or public cost_class.
- Explicit PowerShell intent has only its registered/capability-eligible route; no incompatible executable fallback. Existing process.run and native file/Git priorities retained; no parsing/logging of commands or cost notices. Two descriptions each add<120UTF8bytes; only those known descriptions normalized to frozen P08 metadata SHA, all other metadata remains guarded.
- Actual SDK P14-03 catalog full151/191826bytes, coding96/130864, core43/74594,minimal32/49798. Growth full/coding170bytes, core/minimal0, below700 and unchanged P08 ceilings. Global existing tiktoken0.7.0 (no dependency installation) cl100k full44876/coding30477/core17540/minimal11637; o200k47203/31980/18373/12195. Full/coding margin47cl100k and93o200k tokens remains; not billed or model-prompt usage.
- P14-03 VERIFIED means its local task-specific acceptance only; current guidance diff is uncommitted and has no hosted claim. M14b combined checkpoint/hosted gate remains before milestone closure. P14-04 now eligible by VERIFIED P14-03, scope tools/filesystem/registry.py,tools/jobs/registry.py,core/tool_catalog.py,existing router/tests/docs.
- EXACT_NEXT_ACTION: RED explicit independent one-file/multi-file edit and durable intent selection, add compact guidance within remaining47cl100k tokens, reuse existing patch/refactor/job boundaries unchanged; focused rollback/wire/profile/size checks, then combined milestone acceptance.


### EVIDENCE-1 — Fresh-session P14-04 recovery (2026-10-26 Asia/Tehran)

- Current user continuation supersedes historical local-only/STOP entries; scoped commits and feature-branch CI authorized, merge/main/tag/release forbidden. Runtime/tunnel/operator state unchanged.
- Full canonical plan read sequentially, including superseded records; Index/latest ledger authority retained. HEAD d2752c812a0ed9e975c9f1cd92cc31aa02798dbc, feature/coding-agent-evolution; fresh GitHub PR82 all10SUCCESS. Existing uncommitted P14 guidance recovered rather than rewritten.
- TASK-P14-04 remains IN_PROGRESS. Existing fixed edit/durable intents and descriptions need fresh RED/GREEN, edit-equivalence/rollback regression, catalogue byte/token/schema verification and review. P14-07/P15 not yet accepted.
- Initial modified/untracked file hashes and source snapshot recorded outside checkout in ComputerPilot-P14-Acceptance-2026-10-26/resume-session.json. Preserve panel, uv.lock and both unrelated LSP docs; original feature checkout retained.
- Scoped current graph root ready/watch active; exact intent_route_rules/compact_errors/read_window resolved existing owners. Critical execution/profile boundaries verified in current source; nested registrars inspected directly.
- EXACT_NEXT_ACTION: P14-04 focused baseline and RED against committed rules only in disposable source; finish guidance/semantics, measure budgets and review before P14-07.


### EVIDENCE-1 — P14-04 tested scoped checkpoint; P14-07 gates running (2026-10-26 Asia/Tehran)

- Recovered P14-03/04 guidance completed without changing execution bodies/signatures. Added independent four-edit equivalence across sequential replacements, safe_refactor and hash-guarded apply_patch. Fixture-only config/backup and Git-header errors corrected; no product guard weakened.
- RED against committed routing rules:1failure unknown filesystem.edit_batch, only in disposable source; original work preserved. Final affected tests148PASS23.76sec; full Ruff/mypy299/compileall PASS, final Ruff/mypy after test edit PASS. Independent engineering reviewer5PASS2.57sec plus ten-file source/diff review PASS, catalogue recomputation191904bytes; tokenization not independently repeated.
- Actual catalogue full151/191904,coding96/130942,core43/74673,minimal32/49852bytes; cl100k44902/30503/17560/11651 and o200k47229/32006/18393/12209. Within fixed P08 allowance; only five intentional descriptions normalized for golden metadata checks. Full margin21cl100k/67o200k tokens, no budget increase.
- Scoped commit09658348803559bc98e015cc8cb7019a23606fad pushed only feature branch, DraftPR82 unchanged. Exactly10files staged; panel/uv/twoLSPdocs raw hashes unchanged, none staged. No main/merge/tag/release/runtime/tunnel/operator changes.
- B-EXEC-COST repeats30,warmups5,paired randomized:12cases all360measurements equivalent hashes/errors0,workers_cleanedtrue. Report p1407-exec-cost.json SHA25638dbf4e578fc6db876ce84381d440cb229a7fee289905b504b6e281646d1e024. Native read/search p95 1.510/5.051ms vs historical1.355/4.385 on different load/source; no <=5percent no-regression claim from those incomparable snapshots. Controlled same-code paired check pending. Job/direct short p95 1184.541/107.995ms; medium1504.746/315.217ms; fixture metrics only.
- P14-04 remains IN_PROGRESS until final acceptance; full isolated regression and exact0965834 hosted gates pending. P14-05 DEFERRED,06 BLOCKED; P15 not yet implemented. EXACT_NEXT_ACTION: finish P14 full/native equivalence/hosted quantitative gates; then P15 contract/budget first.


### EVIDENCE-1 — P14 full-regression compact-description recovery (2026-10-26 Asia/Tehran)

- Full isolated2378PASS28SKIP1FAIL560.57sec on0965834-equivalent frozen source. Sole failure test_mcp_integration.py::test_registration_is_unique_strict_and_compact:PowerShell description195>180 due indentation/newlines. This is a genuine missed catalogue contract, not unrelated noise; no pass fabricated.
- Minimal repair shortens only the second docstring sentence; keeps original prefix, command behavior, typed schema and all validation. Ruff/fullmypy299/diffcheck PASS; full integration/typed/router/profile regression running. Fresh current-head CI required after scoped correction commit; old0965834 checks cannot be reused for changed text.
- Controlled same-process paired native benchmark2000pairs+5warmups each,1000paired bootstrap resamples; read p95ratio0.99846 CIupper1.02203;search0.98481 CIupper1.01492, both<=1.05. JSON SHA2566214232ca7a3db2d51394a05a94bb86538adbe4d8c80e11d60f4119e49fef0f2. Same implementation with baseline/candidate metadata; no production/tunnel/model speed claim.
- EXACT_NEXT_ACTION: focused integration GREEN, independent repair recheck, one-file scoped commit/push and exact-head10hosted. P14-07 remains unaccepted pending matching full/CI evidence.


### EVIDENCE-1 — P14-04 VERIFIED_LOCAL; P14-07 IN_PROGRESS / P15-01 independent gate (2026-10-26 Asia/Tehran)

- TASK-P14-04 VERIFIED for task-specific local contract:148affectedPASS including four-edit equivalence, existing rollback/hash/denial/wire suites; independent5PASS+Spec/QualityPASS. Tiny P14-03 description correction separately129PASS1SKIP including actual full-catalogue compactness regression; original full2378PASS28SKIP1FAIL remains disclosed.
- P14-07 IN_PROGRESS until exact580335f27d4592fdeeb1558646a70a7296d5f84f hostedfull10. One-file correction committed/pushed existingfeaturebranch; isolatedstartup151unique and actualhealth5groupsPASS/unresolved0. No live/runtime/tunnel/state change.
- P15-01 IN_PROGRESS for its independent weight0 contract/prototype gate (depends only VERIFIED P14-01), while external P14 CI completes; no P15-02 implementation before benchmark/profile gate. Preserve original profiles151/96/43/32 and P08 budget, do not waive21token headroom. Candidate decision: publish at most one fixed read-file Batch tool under a new explicit opt-in read_batch static profile; existing profiles/tool schemas/allowlist remain unchanged. New profile is user-selected only, never applied to current operator runtime. This is a minimal registration choice within the preauthorized P15 contract, not a relaxed full-profile budget.
- P15 acceptance remains file-only1..16items/global4batches4reads/sharedbuffer bounds, whole-invalid zero dispatch, per-item denied outcome, cooperative timeout/cancellation with slots held until real completion, existing native wrapper/classification/profile boundary, no generic dispatcher or mutation. MixedP15-03 staysDEFERRED.
- EXACT_NEXT_ACTION: P15-01 RED benchmark/prototype and actual typed profile-size decision; collect P14 exact580335f10checks before milestone closure, then P15-02 only after its independent preregistered gates pass.


### EVIDENCE-1 — P15-01 VERIFIED_LOCAL contract/prototype gate; P15-02 IN_PROGRESS (2026-10-26 Asia/Tehran)

- RED missing _native_batch_worker; GREEN benchmark contract1PASS; complete perf module45PASS6platformsymlinkSKIP63.26sec. Ruff/Mypy affected2files PASS. Reused existing isolated-source _exec_cost_once publication/cleanup; no production Batch dispatcher yet.
- B-NATIVE-BATCH private prototype:counts1/4/8/16,cap1/2/4 plus direct and PowerShell;5warmups/30measured,seed1501paired randomized,20cases600measuredsamples; all hashes equal/errors0/workerscleanedtrue. Actual tools/call request/response middleware counts directN/batch1. Raw serialized params/response-body bytes separately reported, no false full network frame measurement. CPU and before/after RSS measured, not claimed continuous peak.
- N1 prototype p95batch1/2/4=1.093/1.017/1.001ms vs direct1.277ms, overhead under5ms; N4 p95direct4.505ms/batch4 1.614ms,N8 6.986/2.585,N16 27.443/6.931. Synthetic RTT0/20/100/250 explicitly arithmetic; at100ms,N>=4 request-wait saved75/87.5/93.75percent, above50. No tunnel/model or production speed claim. Reportp1501-native-batch-prototype.json SHA256908192c75c130df49cf68535306ade0f08cc86aeb23bc22a584612c3f9fc5683.
- Preregistered opt-in registration decision: new static read_batch profile with only read_file,native_read_batch and5existing discovery/router/health tools; no terminal/jobs/extensions implicit activation. Full/coding/core/minimal catalogues and all existing tool schemas remain unchanged; coding_core43allowlist preserved. Projection from actual SDK typed prototype7tools13149bytes/cl100k3047/o200k3208. Dedicated-profile ceiling32768bytes/10000tokens per encoding fixed before implementation. This does not change any P08 ceiling or default/full runtime.
- Source reconciliation: P15-02 also touches core/tool_profiles.py for this measured explicit profile and core registry health for its bounded ephemeral counters. Reuse shared read_window with a private optional bounded window context; default read_file behavior unchanged. One per-server admission/read limiter, no executor/registry/cache/scheduler/state store.
- P15-02 IN_PROGRESS: add typed input/output and real source/read/cancel/claim/profile denial tests RED first; only then implement bounded shared-reader and registration. P14-07 still awaits all10exact580335fchecks;6success0failure now, no acceptance invented.


### EVIDENCE-1 ? New-session actual-state audit and isolated verification opened (2026-10-26 Asia/Tehran)

- Authorized continuation from user: local implementation and scoped feature-branch publication only; merge/main/tag/release forbidden. Actual HEAD580335f27d4592fdeeb1558646a70a7296d5f84f supersedes supplied d2752c8 checkpoint. Index P14-04 VERIFIED, P14-07/P15-01 IN_PROGRESS; stale QUICK START/dashboard will be reconciled against accepted gates, never treated as current evidence.
- Canonical QUICK START/Index/active task details/shared contracts/runbook/gates/latest ledger inspected; Universal-Git-Workflow-&-Commit-Rules.txt absent at requested Downloads path. Existing benchmark edits retained; protected raw hashes and387-file tested snapshot recorded in Downloads/ComputerPilot-Evolution-2026-10-26-session/initial.json. Main checkout preserved; all mutating tests/acceptance run in that external disposable source. No master-plan copy made.
- Current codebase-memory graph root/branch ready, watched; exact read_window/resolve_profile/compact_errors metadata query identified existing reader, profile and wrapper owners. Critical boundaries verified in current source. No new index build.
- Hosted evidence unavailable in current environment: GitHub connector requires approval under never policy; local gh public request denied by network socket permissions. No same-head hosted claim. Focused native-batch prototype1PASS50deselected (preexisting implementation), not sufficient P15 acceptance.
- EXACT_NEXT_ACTION: finish isolated P14 focused/full gates, audit benchmark methodology and actual SDK/profile budget; retain P15-02 NOT_STARTED until P15-01 gates pass.


### EVIDENCE-1 — P14-07 VERIFIED exact hosted acceptance (2026-10-26 Asia/Tehran)

- All10current-head checksSUCCESS on580335f27d4592fdeeb1558646a70a7296d5f84f; PR82Draft/open; head/merged0e181270fda7ceb5602636b8c5c109362a15f2e identicaltree825370f075dff4c3b21a747517a643e5f232735c; basee66c8c9 unchanged. Raw check-runs and tree binding preserved in p14-final-hosted.json. No merge/main/tag/release.
- P14-02/03/04 and M14a/M14b accepted; actual local148PASS plus independent5PASS; compact-description correction129PASS1SKIP and independent2PASS. Prior full2378PASS28SKIP1FAIL preserved; corrected exacthead now passed allsix hosted fullCI suites and3platform+1packaging. No unexecuted local finalfull claim.
- Full catalogue191881bytes after172character PowerShell repair; common schemas and othermetadata unchanged; P08 ceilings retained. Paired native confidence-gate and30repeat equivalent execution-cost evidence remain as above. Native startup151unique/health5groupsPASS in isolatedstate. Trace tests included in hostedfull regression; no leak/replay/lock/profile/cancellation changes. P14-05DEFERRED/P14-06BLOCKED remain independent.
- P15-01 prototype gated locally; P15-02 is IN_PROGRESS, uncommitted and NOT included in this P14 exacthead evidence. EXACT_NEXT_ACTION: complete file-only Batch tests/security review/source/schema budget and actual production benchmark; preserve fixed old profiles and all user/runtime work.



### EVIDENCE-1 — P15-02 IMPLEMENTED_UNVERIFIED; P15-04 IN_PROGRESS / exact-head hosted acceptance (2026-10-26 Asia/Tehran)

- User-authorized continuation performed with ComputerPilot Windows MCP (not Codex). Existing HEAD580335f and preexisting unrelated panel/uv/LSP docs preserved. P15 implemented in 3 scoped commits: 53c44a29 feat files, f0e2e36d benchmark/Windows launcher correction, e6dbe07f docs, pushed to feature/coding-agent-evolution and PR82 Draft OPEN; no main/merge/tag/release, no live profile switch or restart.
- File-only typed native_read_batch exposed exclusively by explicit read_batch profile, seven-tool real SDK catalog13013bytes (budget32768). Full original startup151tools and health5groups PASS; original full/coding/core/minimal tool catalog and P08 metadata/equality acceptance retained.
- Isolated source snapshot in disposable Windows temp, no live runtime data touched. Focused114PASS exit0; full pytest2400PASS28SKIP exit0 518.24s; Ruff core/tools/scripts/tests PASS, mypy301 source files PASS, compileall PASS, main.py --check full151/read_batch7 PASS, health_check PASS. One preceding full 2398PASS28SKIP1FAIL (exec_cost) and subsequent focused2PASS + full GREEN disclosed. The fixed old benchmark failed when Windows Store pwsh.EXE shim could execute but not be opened for SHA; only benchmark metadata now marks unreadable launcher; strict security hash helper stays unchanged.
- Actual MCP Client transcript proves four native read_file calls are replaced by exactly one native_read_batch call with equal ordered contents. Typed invalid/unknown kind/duplicate/oversize fails before dispatch; denial profiles/direct SDK, symlink/escape, independent errors, source-freshness, truncation, UTF8, deadline/late success, repeated cancellation/global max4 batches/max4 reads, drain, claim cleanup and no leaked slots tested. Existing read_file behavior retained. Per-server WorkflowStore caching removes repeated schema initialization; durable resource claims remain mandatory.
- Local production Batch via real in-process SDK: 1/4/8/16 independent files, 5 warmups + 30 measured paired trials per cardinality, seed1501, no mismatched content/no errors. Actual per-trial SDK calls direct N vs Batch1. p50ms direct/batch: N1=1.017/14.212, N4=3.138/48.153, N8=5.801/98.892, N16=11.455/206.649. Local batching is SLOWER; request-wait saving across a sufficiently high-latency tunnel is hypothetical until measured. JSON report ComputerPilot-P15-Validation-2026-10-26/sdk-batch-comparison.json SHA256 57a39ed918e144bfdeb41eed7d5567136a3635a27b33f1be88bd66f9c895c896. No model/tunnel latency, production speedup, billed token result or tokenizer falsely claimed.
- P15-03 mixed mode remains DEFERRED; no generic dispatcher, job/writer batch, schema migration, or runtime activation. EXACT_NEXT_ACTION: all10 hosted checks for same e6dbe07 SHA; if green change P15-02 and P15-04 Index to VERIFIED and record hosted evidence. Until then local acceptance only and no merge/main/tag/release.



### EVIDENCE-2 — Portable hosted failures isolated and repaired; ce311e2 acceptance pending (2026-10-26 Asia/Tehran)

- Initial P15 pushed e6dbe07: Linux/macOS Test & Validate failed only mypy with tools/filesystem/service.py:122 `Module has no attribute windll [attr-defined]`; Packaging and 3 Release Gate SUCCESS. Guarded Windows drive check now uses cast(Any, ctypes) under os.name==nt; same runtime semantics; no weakening of local/network path denial. Ruff/Mypy on current 301-file snapshot PASS. Scoped fix aadfc201 pushed.
- Exact aadfc20 hosted jobs passed Packaging, all3 Release Gate, Windows3.12 Test & Validate; Linux3.12 and macOS3.10 full pytest failed at test_global_capacity_and_repeated_cancel_hold_slots_until_reads_finish, asserting cancelled first Batch remained pending. Four simultaneous Batches with four items each may allocate all four global reader slots to another batch; first Batch can legitimately have zero reads and terminate immediately. The test assumed fairness not contracted by the implementation; this is a test-harness error, not permission to reduce safety guarantees.
- Fixed fixture: four simultaneous Batches each own exactly one independently blocked reader; after all four entered, cancellation of first must keep its slot and response admission until I/O finishes. No production execution changes. Focused17PASS, Ruff PASS, mypy301 PASS. Commit ce311e2e7b9fe5bbdd039d3bb9f511ebc70524a4 pushed to Draft PR82; new exact-head hosted checks pending. Prior e6dbe07/aadfc20 runs are superseded and must not be reused for acceptance.
- LIVE runtime/tool profile/tunnel/supervisor not restarted or switched. No main merge/tag/release. EXACT_NEXT_ACTION: collect all 10 current-head hosted gates for ce311e2 and record actual result; only then promote P15-02 and P15-04 Index to VERIFIED.


### EVIDENCE-3 — P15-02/P15-04 exact-head hosted acceptance (2026-10-26 Asia/Tehran)

- Exact HEAD = PR #82 head = ce311e2e7b9fe5bbdd039d3bb9f511ebc70524a4; PR remains OPEN/DRAFT. Six CI Test & Validate (Windows/Linux/macOS, Python3.10/3.12), three Platform Release Validation, and one Release Packaging Smoke all COMPLETED/SUCCESS, 10/10 on this SHA. Workflow runs: CI 40361863418, Platform 40361862950, Packaging 40361862971.
- P15 file-only native_read_batch and safety contracts unchanged after scoped scheduler-independent cancellation fixture commit ce311e2; prior Windows isolated full2400PASS28SKIP and typed/wire/profile/cancellation acceptance remain recorded above. Earlier aadfc20 Linux3.12 failure and earlier e6dbe07 portable mypy failure are historical, not current-head checks.
- Independent follow-up via MCP-run test process: clearing only inherited COMPUTERPILOT_INTERNAL_RESOURCE_CAPABILITY for isolated child execution eliminated resource_capability_scope_violation / batch_scope_denied false negatives; focused113PASS1FAIL in an archive-only environment due to frozen Windows metadata SHA mismatch (no Git checkout metadata), not silently represented as a passing local suite. Exact-head hosted Windows3.10/3.12 full checks succeeded. No code change based on this environmental mismatch.
- Measured local SDK batch latency was slower than direct reads; the hypothetical RTT advantage is not asserted as a real production speedup. Runtime/tool profile/tunnel/state not switched or restarted. No main merge/tag/release, and P15-03 remains DEFERRED. TASK-P15-02 and TASK-P15-04 VERIFIED for the contracted file-only acceptance.


### EVIDENCE-4 — P05-02 controlled profile evaluation harness; model acceptance still BLOCKED (2026-10-26)

- Snapshot source at start of this work: ce311e2e7b9fe5bbdd039d3bb9f511ebc70524a4, branch feature/coding-agent-evolution; final three narrowly scoped commits 2b4b966 (offline 20x3x3 scheduling and committed-HEAD snapshot isolation), ec138f5 (opt-in AGY read-only model pilot, settings restoration and server-side tool identity), 555a166 (truthful developer evidence). Pushed to Draft PR #82, exact PR head 555a166c6173ca0cf841d4bb55a74430d6ee674e; never merged or released.
- Offline manifest: 20 immutable tool-selection tasks x3 repetitions x3 profiles =180 planned calls; Latin-square profile first-position balancing, strict per-task/profile uniqueness, real-profile count and refusal of missing/ambiguous/unreviewed trials. Three NEW isolated workspaces from git archive HEAD, 390 tracked source files each, no inclusion of unrelated worktree modifications or production .agent_state. Initial relative-path snapshot bug was caught by security preflight before model use and fixed, verified by relative-path regression test.
- Bounded AGY CLI 1.4.7 actual probes: first full-profile trial TIMEOUT70.047s with no trusted model usage; direct no-tool connectivity SUCCESS13.78s, input29620 (not profile test); coding_core project_summary trial SUCCESS35.047s/input35341, separate trial SUCCESS31.172s/input35341. In isolated new coding_core runtime timings.jsonl native phase=request_pipeline/method=tools/call confirms project_summary actual dispatch. Client generic call_mcp_tool/built-in read_file aliases cannot independently prove MCP tool identity; new runner uses isolated server-side trace. Each pilot explicitly restored scoped read-only CLI settings; no live runtime profile switch or restart.
- No independent answer correctness, controlled 20x3x3 live executions, matched model/cache baselines or meaningful p50/p95 and wrong-tool survey. Three new MODEL attempts were made (one full timed out with no confirmed dispatch, two coding_core successful native MCP project_summary dispatches); no new coding trial on current source. No spend/token reduction claim. Usage cap is observational, not provider-enforced billing cap. The ignored evidence root is tests/.real_lsp_tools/profile-eval-ce311e2-v2; legacy preexisting profile pilot source was stale relative to HEAD.
- Exact focused related pytest 64 PASS,1 existing pytest config warning; entire Ruff PASS; entire mypy305 source files PASS; compileall PASS; source main.py --check registered151 names PASS. Tests and static checks run before commits on the same source diff. Source/product runtime implementation untouched; only two test scripts, two dedicated test modules, one evidence doc committed.
- Classification remains TASK-P05-02 BLOCKED, weight1 not VERIFIED. The 97.0% (32/33) verified weighted percentage and task-index BLOCKED/DEFERRED counts MUST NOT be promoted by this tooling. P05-03 design-blocked without actual client list_changed/refresh proof; P10/P13 design-blocked; P14-05, P15-03 remain optional DEFERRED, P14-06 remains isolation-gated BLOCKED. Full acceptance requires a separately scoped model cost budget, independent answer grading, server tool provenance, 20x3x3 comparable runs, and representative isolated read/context/patch/verify/Git journeys. No main merge/tag/release.
- Hosted same-SHA checks on 555a166: Packaging run40375785888 SUCCESS, Platform Validation run40375786014 all3 SUCCESS. CI run40375785760 (six Linux/macOS/Windows Python3.10/3.12 jobs) remained IN_PROGRESS when last checked. Thus observed4/10 SUCCESS,6 PENDING, not final hosted acceptance. Never borrow ce311e2 old 10/10 result; update only with same-SHA observations.


### EVIDENCE-5 — PR #82 finalization and live recovery review IN_PROGRESS (2026-10-26 Asia/Tehran)

- Current authorized task: inspect PR #82 and uncommitted work; verify suspected Batch race, production benchmark, and quarantined resource ownership before any completion or merge decision. Preserve operational .agent_state, databases, tunnel and all user files.
- Branch feature/coding-agent-evolution, HEAD 555a166c6173ca0cf841d4bb55a74430d6ee674e; PR open/Draft, all ten hosted gates SUCCESS on that HEAD, no review threads at inspection. These checks do not cover subsequent edits.
- Live Ali Computer Agent health reports degraded: one quarantined native.command resource; direct uncertain-operation list is empty. Do not release ownership without authoritative identity and conclusive evidence. P05-02 and other BLOCKED/DEFERRED items remain so unless their acceptance is proved.
- The user now explicitly authorizes a reviewed, non-sensitive repository copy of this plan, replacing the earlier prohibition for this request; the external original remains canonical. No tag or release is authorized.
- EXACT_NEXT_ACTION: inspect Batch and benchmark source/tests, identify the quarantined resource via supported recovery and read-only evidence, then run isolated validation before any publication.


### EVIDENCE-6 — Native Read Batch race reproduced; isolated remediation IN_PROGRESS (2026-10-26)

- An isolated deterministic Windows junction-swap regression returned the outside-file canary through native_read_batch at 555a166; the initial security check FAILED as expected. The attempted mitigation exists only in a disposable source clone, not yet in the main checkout or PR; it needs remaining review and full cross-platform hosted verification.
- Isolated Windows Batch/filesystem focused suite: 32 passed; security regression plus real/prototype benchmark dispatch checks passed separately. Targeted Ruff and Linux-target mypy passed; Linux/macOS actual execution is not yet verified.
- A 30-repeat production MCP-in-memory benchmark with 5 warmups returned matching results and zero sample errors; four-file p50 direct 5.467 ms versus production batch parallel4 58.499 ms, 16-file direct 20.272 ms versus batch 234.428 ms. Source manifest b360cdf33092f62cc66348cf70217d242632f186382c853a98be3847310cd175; this is not live Tunnel performance. The raw report remains in the isolated validation workspace pending archival.
- Live official health remains degraded due one quarantined native.command resource; standalone uncertain operations list is empty. Read-only evidence shows no linked unresolved capacity but does not prove process-tree cleanup or side effect status; leave the resource fenced.
- Exact next action: recover the running exec-cost benchmark, finish review and focused regression checks, run isolated full validation, publish only reviewed safe changes, collect all hosted checks on the final SHA, and postpone cleanup until GitHub disposition and evidence archival.


### EVIDENCE-7 — Independent review follow-up IN_PROGRESS (2026-10-26)

- LSP context preparation returned an admission slot while a timed-out to_thread worker remained blocked. Deterministic regression reproduced 0 rather than 1; isolated change keeps the preparation shielded until completion. Focused context/cache suite 40 passed.
- Cross-process retention deleted an in-progress reserved backup before durable workspace checkpoint. A second process reproduced the deletion. Isolated candidate coordinates pin/checkpoint and retention via the existing file lock, and fails closed if a canonical recovery journal exists but is not configured. Backup/recovery suite 124 passed/2 skipped; additional deterministic contention and abrupt owner-death checks 4 passed. Cross-platform hosted validation and old-runtime rolling compatibility remain open.
- Independent benchmark review identified parent timing output and mixed-suite retention risks. Isolated candidate suppresses benchmark parent timings in context, requires explicit output and refuses mixed production-suite runs; focused safety regressions pass. Actual in-memory production observations do not establish real Tunnel latency.
- EXACT_NEXT_ACTION: finish initial full-suite diagnosis, independently review the latest edits, rerun affected/full checks on a fresh exact candidate, publish only validated changes and collect hosted gates for the final SHA. Do not alter operational state or clear quarantined ownership without conclusive evidence.


### EVIDENCE-8 — Main workspace integration and Windows validation IN_PROGRESS (2026-10-26)

- Per user instruction the reviewed candidate changes were transferred to the original checkout, preserving the existing panel change and untracked uv.lock. The live server has not been restarted.
- On an isolated copy matching the current source, full pytest: 2430 passed, 28 skipped in 558.51 seconds. Two preceding full runs were interrupted during review or a timing-test hang and do not count as passing. The timing test was fixed to avoid waiting on a global shared queue; its focused suite: 50 passed, 6 skipped.
- Recovery/backup tests: 101 passed, 2 skipped in isolated state. Journal corruption, disappearance, invalid terminal records, cross-process contention and startup/compaction checks are covered. An existing invalid journal now blocks recovery and compaction; retention fails closed. Older processes sharing the backup directory must be drained before relying on interprocess locking.
- Isolated compileall, startup/catalog and health smoke passed; startup reported 151 unique tools. Direct compileall in the main checkout failed on two intentionally invalid Python fixtures under an old ignored test output tree; those files were preserved. Windows-specific path handling was tested; hosted Linux/macOS evidence is pending.
- Production in-memory MCP benchmark: 30 measured runs and 5 warmups, 20 cases, zero errors, workers and resource claims drained. Four-file p50 direct 5.229 ms and batch parallel4 58.593 ms; sixteen-file p50 direct 18.573 ms and batch 226.985 ms. Source manifest 718a3a695ae615a93f9dc7763aa7f808713dd5c357b92e86524526897c49326c, report SHA-256 4202353bbbc23a1dc3c42f977505da0ec7e4b696a5ce43eb79cddf6ed50e6b83. These are not Tunnel measurements.
- Live health remains degraded with a quarantined native.command resource. Existing evidence does not prove the old process tree is gone; do not clear the fence. P05 live-model acceptance remains blocked.
- EXACT_NEXT_ACTION: finish exec-cost benchmark, finalize sanitized snapshot, review and selectively commit changes, push PR branch, verify all hosted checks on the new head, and decide PR disposition before evidence archival and cleanup.


### EVIDENCE-9 — PR head published; hosted validation IN_PROGRESS (2026-10-27)

- Pushed feature/coding-agent-evolution through bfde66935e775d9f0fddc0e87aaba1c4b12d189e to PR #82 without rewriting history. Seven focused commits cover backup/recovery, LSP, native read safety, benchmarks, panel and documentation. The pre-existing uv.lock remains untracked and unstaged.
- Final exec-cost benchmark: 30 runs, 12 cases, zero errors, workers cleaned, source manifest 718a3a695ae615a93f9dc7763aa7f808713dd5c357b92e86524526897c49326c; report SHA-256 9e21a9070c5bd34f902bee29cd85bad2c9bb3f6415619cbed8e47e7843c03530.
- The exact source files used for the 2430-passed full run were compared byte-for-byte with the committed checkout. The current local runtime journal passed the new strict read-only validation over 1927 records. No quarantine was released.
- PR remains draft while hosted checks on bfde669 are pending. EXACT_NEXT_ACTION: assess all ten hosted results for that SHA, review blockers, then determine PR disposition; archive evidence and inspect safe cleanup only after GitHub disposition.


### EVIDENCE-10 — Hosted transport failure and fixture hardening IN_PROGRESS (2026-10-27)

- Hosted Linux/Python 3.12 on bfde669 failed test_actual_http_entrypoint_exposes_modern_external_tools after waiting for a restored catalog notification; 2436 passed and 21 skipped. The CI server log was unavailable, so the precise hosted cause remains unconfirmed.
- A deliberate pause during an in-place epoch write reproduced the same timeout locally. Publishing complete epoch files by same-directory replacement passed that forced reproduction. The extension fixture/refresh/transport suite passed 40 tests; scoped Ruff and mypy passed.
- Pushed test-only commit 3f7c88c8781f5b09b4a0d80d4a3ad88c703ea660. The 2430-passed full Windows run preceded this test-only commit; its changed suite passed afterward. Hosted checks for the new SHA are pending; no merge or cleanup yet.
- EXACT_NEXT_ACTION: inspect all hosted results on 3f7c88c, diagnose any failure, retain the live quarantine, decide PR status, and only then archive evidence and perform safe cleanup.


### EVIDENCE-11 — PR merged; main validation IN_PROGRESS (2026-10-27)

- All ten hosted checks succeeded on PR head 3f7c88c8781f5b09b4a0d80d4a3ad88c703ea660. Scoped independent review found no new concrete blocker. PR #82 was marked ready and merged with a non-squash merge commit 965a5cb641af40f1768f6fe3512d8332662ab28c.
- Local main and origin/main were fast-forwarded to 965a5cb641af40f1768f6fe3512d8332662ab28c; the working checkout remains on feature/coding-agent-evolution and untracked uv.lock remains preserved. No tag or release was created.
- Live health is still degraded because one native.command resource remains quarantined; no conclusive ownership evidence was found and the fence remains. Tunnel process is running with the previously observed executable hash; end-to-end connectivity is unverified.
- EXACT_NEXT_ACTION: verify all hosted workflows on the merge SHA, then archive important evidence and inspect only demonstrably safe, inactive duplicates for Recycle Bin transfer.


### EVIDENCE-12 — Hosted main gates and evidence preservation (2026-10-27)

- All ten applicable hosted gates succeeded on merge commit 965a5cb641af40f1768f6fe3512d8332662ab28c. The automatic Publish Release workflow detected a non-release commit and skipped artifact build and publication; no new tag or release was created. Local main and origin/main match the merge SHA.
- Archived 97 root-level evidence, benchmark, patch, log and database-backup files with source-before/source-after and destination SHA-256 checks to a dedicated local Downloads archive; a second manifest verification found zero mismatches. The four original evidence directories remain because the plan references them and nested evidence is not fully duplicated.
- The five p1501 directories return UnauthorizedAccessException and remain untouched. Deep scan of the evolution session found 10 access errors; P14 contains numerous nested test Git repositories and database files. Fifty-seven nonempty project pytest temporary directories remain because their evidence, dependencies or active use cannot all be disproved. No item was moved to Recycle Bin in this cleanup stage.
- Live health still reports resource_ownership_unresolved; leave quarantine fenced. Static LSP/P05 evidence does not substitute for the blocked live-model pilot, and Tunnel end-to-end connectivity remains unverified.
- EXACT_NEXT_ACTION: investigate and reconcile the live ownership fence through supported recovery only if conclusive evidence becomes available; retain the blocked P05 pilot status until its acceptance criteria can be executed safely.


### EVIDENCE-13 — Preboot ownership reconciliation VERIFIED (2026-10-27)

- User explicitly requested remediation with full access. New current evidence establishes an OS reboot at 2026-10-26T19:14:12Z after the native.command quarantine at 18:53:33Z. CIM and psutil agree; the recorded native owner identity is no longer alive and all linked child capacities are released. This proves preboot process ownership ended; it does not prove command success.
- A consistent SQLite backup passed integrity_check and schema 11 validation. Recovery was rehearsed on a separate copy with the existing WorkflowStore.resolve_quarantined_resource_request API, yielding zero quarantined requests without changing operation history.
- EXACT_NEXT_ACTION: independently review this evidence, recheck the record and OS boot, use the official store resolution with bounded evidence, then verify public health and a harmless command without changing Tunnel settings.


### EVIDENCE-14 — Live ownership reconciliation VERIFIED (2026-10-27)

- Rechecked exact live store, sole quarantined native.command request, preboot timestamps, dead owner identity, and zero unsettled child capacities immediately before resolution. Independent source/evidence review found no concrete blocker to this bounded release. Used the existing WorkflowStore.resolve_quarantined_resource_request method with bounded evidence; no direct SQL UPDATE/DELETE, replay or success reclassification. The constructor still performs normal store initialization; no migration was needed (schema 11).
- Pre-change SQLite backup is consistent, passes integrity_check and retains its recorded SHA-256; rehearsal succeeded on a separate copy. Live integrity_check passed after resolution. Original run_process journal history still contains exactly its begin and raised result records.
- Public plugin health after resolution and again after the public command smoke: healthy, 151 unique tools, zero quarantine/active/pending resource requests, zero pending/uncertain recovery operations. The harmless command printed COMPUTERPILOT_RECOVERY_SMOKE_OK, exited 0, was not timed out and used windows_job ownership.
- Tunnel process PID and executable hash remained unchanged across this recovery; no restart or code changes. Existing untracked uv.lock remains untouched. Evidence and backup are in the local Ownership Recovery folder.
- EXACT_NEXT_ACTION: continue independently blocked P05 live-model acceptance only when its separate requirements can be fulfilled. Ownership cleanup is now verified; end-to-end network latency and broader product acceptance are not implied.


### EVIDENCE-15 — Remaining acceptance continuation IN_PROGRESS (2026-10-27)

- Work continues in the original checkout on fix/coding-evolution-acceptance from origin/main 965a5cb. Existing untracked uv.lock and operational state remain protected.
- P03-05/P04-03/P15-02 safety fixes have merged same-subject hosted evidence but the index still needs an evidence-based reconciliation.
- P15-04 reopened for a concrete numeric gap: the production benchmark single-file p50 overhead is 17.558 - 1.451 = 16.107 ms versus the plan's 5 ms target. Safety checks must not be weakened to meet the target. Diagnose before deciding acceptance.
- P05 live-model evaluation still requires explicit budget and independently graded evidence; no paid/model calls have been dispatched in this continuation.
- EXACT_NEXT_ACTION: profile production read overhead using disposable data, verify focused acceptance and Full Doctor safely, then reconcile only substantiated task statuses and report every remaining conditional gate.


### EVIDENCE-16 — Remaining-task acceptance and explicit performance gate (2026-10-27)

- Original workspace remains the edit location; all production source is unchanged from validated merge 965a5cb. Focused acceptance over recovery/backups/semantic edits/resources/context/LSP/Batch/profiles/output: 553 passed, 2 Windows directory-link skips, 1 cacheprovider configuration warning. Two link tests now re-raise on POSIX so hosted validation cannot silently skip these required guards. Updated backup suite: 49 passed, 2 Windows skips. Final follow-up hosted checks must confirm the test-only tightening.
- Full Doctor in local-http mode with browser launch passed dependency imports, pip check, isolated MCP/filesystem/terminal health, browser import and actual headless launch. The Doctor source matched the main checkout and used disposable state; Tunnel-mode or end-to-end network readiness was not claimed.
- P03-05, P04-03 and P15-02 reconcile to VERIFIED using their executed focused checks, previous complete runtime-source hosted gates and independent corrective review. No unchecked production change is introduced here.
- P15-04 remains BLOCKED: the 30-run production report measured 1-file p50 direct 1.451 ms versus Batch1 17.558 ms, overhead 16.107 ms against the unchanged 5ms target. Synthetic 100ms hop savings for N>=4 pass but cannot waive the local gate. A 30-read cProfile diagnostic attributed 0.367 of 0.427 seconds to durable managed claims; transaction commits accounted for 0.175 seconds. This is diagnostic evidence, not a hard universal lower bound. Do not remove durable claims or change SQLite durability to manufacture a pass.
- P05 offline snapshots and balanced 180-invocation schedule were rebuilt for 965a5cb beneath the main checkout's ignored test fixtures; no live model was called. Separately approved budget, fixed client/model selection and independent correctness grading remain missing. Static SDK refresh is not evidence of the real host client's dynamic catalog support.
- Index: 43 VERIFIED, 6 BLOCKED, 5 DEFERRED out of 54. No default profile, optional preflight, mixed batch, Job isolation, grounding, controller or delegation capability was activated.
- EXACT_NEXT_ACTION: publish the reviewed acceptance records and collect hosted checks for the follow-up test-only changes; preserve the remaining gates rather than inventing missing approvals or acceptance evidence.
