# طرح جامع اجرای موازی و Workflow DAG در ComputerPilot MCP

تاریخ تدوین: **2026-10-17** — نوع سند: **طرح مرجع و دفتر اجرای محلی** — زبان قراردادها و شناسه‌ها: English؛ زبان توضیح: فارسی.

مسیر canonical این طرح: `docs/roadmap/COMPUTERPILOT-PARALLEL-EXECUTION-AND-DAG-MASTER-PLAN.fa.md`.

نسخه اولیه این سند با scope صرفاً PLANNING ONLY تهیه شد. درخواست جاری کاربر خواندن کامل طرح و متن handoff و رفع اشکالات کار نیمه‌تمام در مسیر اصلی است؛ با پاسخ صریح کاربر در 2026-10-18، تکمیل فازهای ۵ و ۶ و commit/push/PR مجاز شد؛ activation عمومی فقط پس از عبور همه گیت‌ها مجاز است و انتشار release درخواست نشده است. pointer، dashboard و journal زیر وضعیت repair جاری را ثبت می‌کنند؛ بخش‌های audit/design و دفترهای PLANNING سابقه baseline هستند و ادعای وضعیت فعلی همه سورس‌ها نیستند. طرح حاضر مستقل از master plan قدیمی v0.8 است؛ آن فایل خارج checkout می‌ماند و این سند کپی یا جایگزین آن نیست.

## CURRENT EXECUTION POINTER

| Field | مقدار فعلی |
|---|---|
| Current Phase | P5/P6 VERIFYING — source and regression fixes complete; FINAL GO pending new same-SHA checks |
| Current Task | P6-T07/P6-T08 — independent review follow-up for eight findings; seven commits after 81d9f42; new tip bc2242a |
| Last Successful Check | 6dcd3e1 full 1561 passed/23 skipped; 10/10 hosted gates PASS; latest focused 152 passed/1 skipped; Ruff/mypy270 PASS |
| Next Action | Push tracked plan + latest two fixes, execute independent delta review, full local and 10 hosted same-SHA checks; merge PR #81 only when clean; NO new version/tag/release |
| Active Blockers | Original review 6 findings fixed and 2 additional P2 findings fixed/tested; awaiting independent validation and hosted same-SHA checks on new tip; public DAG remains disabled |
| Implementation Authorization | پاسخ صریح 2026-10-18: تکمیل P5 و P6؛ push و PR؛ activation فقط پس از همه گیت‌ها؛ مسیر اصلی tunnel-client-v0.0.11-windows-amd64 |
| Planning Verification State | P1..P6 source implemented, cross-platform gates passed at old tip 6dcd3e1; final G5/G6 evidence on latest tip remains VERIFYING |

## ۱. شیوه استفاده، حدود ادعا و نقطه شروع

این سند سه نوع گزاره دارد: **CURRENT / SOURCE-VERIFIED** یعنی در سورس checkout خوانده شده؛ **PROPOSED** یعنی تصمیم طراحی این طرح و هنوز API موجود نیست؛ **UNVERIFIED** یعنی اجرای واقعی، benchmark یا deployment برای اثبات آن انجام نشده است. وجود تست به معنی پاس‌شدن آن در این نوبت نیست. شمارش process واقعی، effective environment، وضعیت سرور فعال، remote HEAD و CI فعلی بررسی اجرایی نشده‌اند. اعداد default زیر از سورس هستند، نه اندازه‌گیری ماشین کاربر.

Baseline محلی audit:

| مورد | شاهد |
|---|---|
| Repository | `C:\Users\Meliodas\Downloads\tunnel-client-v0.0.11-windows-amd64` |
| Branch / HEAD | `main` / `d721aa8e09e04dccffaf5dbcfda1a418e502c8ce` |
| نسخه source | `0.8.0` در `core/config.py:Settings.version` و `pyproject.toml` |
| Remote ثبت‌شده | `origin`، repository `dibbed/computerpilot-mcp`؛ fetch/network در audit انجام نشده |
| آخرین تاریخچه دیده‌شده | merge PR #80 با subject مربوط به `fix/uia-recovery-admission`؛ پیش از آن merge PR #79 برای background verification |
| تغییر موجود کاربر | `tools/panel/index.html`، شش خط diff، سه insertion و سه deletion |
| untracked موجود | `.freebuff/` و `uv.lock`؛ محتوا بررسی یا تغییر نکرد |
| Git visibility این artifact | `.gitignore:63` کل `docs/roadmap/` را ignore می‌کند؛ این فایل روی disk ایجاد شده ولی در status معمولی دیده نمی‌شود؛ `.gitignore` و Git index تغییر نکرده‌اند |
| caveat Git | هشدار permission روی ignore سراسری و `.codebase-memory/`؛ وضعیت گزارش‌شده محدود به داده قابل‌خواندن Git است |

Discovery برای نامشخص‌بودن callerها از `codebase-memory-mcp.index_status` و exact `search_graph` شروع شد: root همان repository، status برابر ready، 4,995 node و 25,704 edge؛ index time گزارش‌شده `2026-10-17T10:34:45Z` و watcher فعال بود. query مشخص روی `WorkflowExecutor`، `verify_changed_repository` و `reserve_worker_launches` callerهای worker/registry و تست‌ها را نشان داد. نام جست‌وجوشده `collect_repository_diagnostics` وجود نداشت؛ این اسم API نیست. سپس تمام تصمیم‌های مهم با سورس مستقیم تطبیق داده شدند. این نتیجه ادعای پوشش کامل همه graphها یا freshness دائمی index نیست. برای این کار index جدید ساخته نشد.

اصول Ponytail اعمال شده‌اند: reuse موتور verification، store، scheduler، process ownership و journal موجود؛ بدون framework جدید، broker جدید، scheduler موازی دوم یا dependency تازه. `brainstorming` / `writing-plans` در skillهای قابل‌دسترسی پیدا نشد؛ طرح از تفکیک audit/design، TDD، واحدهای کوچک و gateهای مستند استفاده می‌کند. برای planning حاضر sub-agent اجرا نشده است.

### ۱.۱ نقشه منابع بررسی‌شده و authority

| موضوع | منبع واقعی و نقاط اتصال |
|---|---|
| تنظیمات و سقف‌ها | [core/config.py](../../core/config.py): `Settings`, `_env_int`, `resource_budgets` |
| Durable Jobs | [core/jobs.py](../../core/jobs.py): `JobStore.submit`, `reserve_worker_launches`, `claim_for_execution`, `_reconcile`, `cancel`; [core/job_scheduler.py](../../core/job_scheduler.py): `JobScheduler`, `ensure_job_scheduler`; [scripts/job_worker.py](../../scripts/job_worker.py): `run` |
| verification | [tools/testing/verification.py](../../tools/testing/verification.py): `build_verification_plan`, `verify_changed_repository`; [tools/testing/background.py](../../tools/testing/background.py): `VerificationRequest`, `submit_verification`, `source_fingerprint`; [scripts/verification_worker.py](../../scripts/verification_worker.py): `run_request` |
| ثبت testing و diagnostics | [tools/testing/registry.py](../../tools/testing/registry.py): `register` و nested tools؛ [tools/testing/diagnostics.py](../../tools/testing/diagnostics.py): `normalize_diagnostic`, `merge_diagnostics`; [tools/testing/runners.py](../../tools/testing/runners.py): سه `run_*_summary` |
| workflow domain | [core/workflow_models.py](../../core/workflow_models.py)، [core/workflow_executor.py](../../core/workflow_executor.py)، [core/workflow_store.py](../../core/workflow_store.py)، [core/workflow_actions.py](../../core/workflow_actions.py)، [core/workflow_reconciliation.py](../../core/workflow_reconciliation.py) |
| workflow ورودی و worker | [tools/workflows/registry.py](../../tools/workflows/registry.py): `StepInput`, `WorkflowInput`, `register`; [tools/workflows/builtins.py](../../tools/workflows/builtins.py): `builtin_workflow`; [scripts/workflow_worker.py](../../scripts/workflow_worker.py): `main`, `build_executor_route_planner`; [core/workflows.py](../../core/workflows.py): compatibility exports |
| منابع، process و lifecycle | [core/resource_locks.py](../../core/resource_locks.py)، [core/executor.py](../../core/executor.py): `run_bounded`; [core/process_ownership.py](../../core/process_ownership.py): `OwnedProcess`, `spawn_owned_process`; [core/lifecycle.py](../../core/lifecycle.py): `RuntimeLifecycle`; [core/heartbeat.py](../../core/heartbeat.py): `lifespan`; [scripts/supervisor.py](../../scripts/supervisor.py): `drain_runtime`, `remember_children` |
| extension | [core/extensions/models.py](../../core/extensions/models.py): `ExtensionLimits`; [core/extensions/manager.py](../../core/extensions/manager.py): `ExtensionOwner`, `ExtensionLifecycleManager`, `connect_and_discover_tools`; config/transport/registry همان subsystem |
| قراردادها | [AGENTS.md](../../AGENTS.md)، [tooling](../tooling.md)، [architecture](../architecture.md)، [release process](../release-process.md)، [testing standards](../development/TESTING-STANDARDS.md)، `.github/workflows/ci.yml` و `platform-validation.yml` |
| تست‌های محوری خوانده‌شده | `tests/test_parallel_verification.py`, `test_workflow_concurrency_gates.py`, `test_resource_locks.py`, `test_call_admission.py`, `test_diagnostics.py`؛ همچنین تست‌های job admission، workflow cancellation/crash/migrations/heartbeat و MCP integration برای تعیین قرارداد و الگو |

## ۲. Current-State Audit واقعی

### ۲.۱ Durable Jobs: موازی در سطح job، نه همه descendantها

**CURRENT.** `Settings.max_running_jobs` از `MCP_MAX_RUNNING_JOBS` می‌آید: default برابر **4**، clamp بین **1 و 256**؛ `_env_int` در مقدار غیرقابل‌تبدیل default را برمی‌گرداند. این سقف روی jobهای همان jobs DB اعمال می‌شود. دو DB مستقل یا چند deployment با state جدا سقف مشترک ندارند؛ تنظیمات ناسازگار workerها نیز policy یکدست ایجاد نمی‌کند.

`JobStore` schema نسخه **3** دارد: جدول `jobs` شامل `request_key UNIQUE`, `fingerprint`, `spec`, `status`, `version`, `queue_deadline`, `launch_token`, `launch_started`, شناسه worker و command همراه create time است. `submit` spec را canonical JSON و SHA256 می‌کند؛ درخواست هم‌کلید و هم‌spec deduplicate می‌شود؛ هم‌کلید و spec متفاوت `idempotency_conflict` است. dedup تا retention رکورد معتبر است؛ حذف history می‌تواند استفاده دوباره از کلید را ممکن کند. این قرارداد exactly-once دائمی برای process خارجی نیست.

مسیر اجرا:

1. `submit` رکورد queued و output files را ایجاد و `ensure_job_scheduler` را صدا می‌زند.
2. `JobScheduler.launch_once`، `reserve_worker_launches(max_running=SETTINGS.max_running_jobs)` را اجرا می‌کند.
3. store، stale launch reservation و active processها را reconcile می‌کند؛ در `BEGIN IMMEDIATE` queued cancel/deadline را نهایی، تعداد `running` و `orphaned` و reservationهای queued را می‌شمارد؛ فقط ظرفیت باقی‌مانده را به ترتیب `created,id` reserve می‌کند.
4. هر reservation یک `launch_token` دارد. scheduler یک process مستقل `scripts.job_worker` با DB، job ID و token اجرا می‌کند. registry داخل process فقط launcher هم‌DB را coalesce می‌کند؛ تضمین بین processها از SQLite است.
5. worker با `claim_for_execution` دوباره ظرفیت active را در `BEGIN IMMEDIATE` چک و token، وضعیت queued، cancel و deadline را کنترل می‌کند. پاسخ `claimed` / `wait` / `terminal` است. claim با token اشتباه اجرا نمی‌شود.
6. پس از claim، `spawn_owned_process` command را اجرا می‌کند. خروج worker یا اتمام job successor را kick می‌کند. queue-empty race در `JobScheduler._run` با بررسی مجدد queue پوشش داده شده است.

Reservation زنده با PID/create-time حفظ می‌شود؛ reservation بدون worker زنده پس از **10 ثانیه** با token guard آزاد می‌شود. `same_process` PID و زمان ایجاد را مقایسه می‌کند تا PID reuse به مالکیت اشتباه منجر نشود. `orphaned` یعنی worker از دست رفته ولی command هنوز زنده است و **ظرفیت مصرف می‌کند**. command از دست‌رفته به `interrupted` می‌رود؛ خودکار replay نمی‌شود. queue timeout قبل از execution جدا از execution timeout است.

`JobStore.cancel`: queued را در transaction cancelled می‌کند؛ running را با `cancel_requested` علامت می‌زند؛ برای orphaned با PID/create-time، tree termination انجام می‌دهد. `scripts.job_worker.run` cancellation را با interval **0.2 / 0.5 / 1.0 ثانیه** بسته به عمر job poll می‌کند و tree را متوقف می‌کند. این اعداد polling هستند، SLA کامل cancellation نیستند؛ cleanup و I/O زمان اضافه دارند. cleanup ناموفق نباید موفقیت ساختگی بدهد؛ worker failure bounded را persist می‌کند و در failure DB connection تلاش recovery از connection تازه دارد.

**محدودیت:** سقف job فقط ریشه‌های admitted را محدود می‌کند. یک command می‌تواند process/threadهای دیگری بسازد. schema jobs resource claims، analyzer tokens یا سقف subtree process count ندارد. برای assertionهای admission به `tests/test_job_admission.py` و reservationها به `tests/test_job_scheduler.py` رجوع شود؛ تست‌ها در این planning اجرا نشده‌اند.

### ۲.۲ background verification و `max_parallel_checks`

**CURRENT.** `tools/testing/registry.py:register`، `start_verification` را فقط وقتی `background_enabled` فعال باشد ثبت می‌کند؛ قرارداد registry/profile نیازمند testing و jobs است. `VerificationRequest` با Pydantic، extra fields را رد می‌کند، اندازه‌ها را محدود می‌کند و `max_parallel_checks` را **1..3، default=1** می‌گیرد. برای مقدار بالای 1 باید `fail_fast=false` و `ruff_fix=false` باشد. background در تمام حالت‌ها `ruff_fix=true` را رد می‌کند؛ حتی در حالت serial. این تفاوت نباید با reuse اشتباه مدل background به foreground منتقل شود.

`submit_verification` قبل از admission، root و changed paths را validate، plan و source fingerprint را محاسبه و request را در command `scripts.verification_worker` می‌گذارد. سقف request/event برابر **64 KiB**، stream برابر **4 MiB**؛ snapshot حداکثر **10,000 فایل، 64 MiB، 10 ثانیه** دارد. command در JobStore با timeout برابر `total_timeout_sec + 30` ثبت می‌شود؛ `queue_timeout_sec` مستقل است. source fingerprint بخشی از spec است و تغییر ورودی با کلید قدیمی می‌تواند conflict بدهد.

`scripts.verification_worker.run_request` fingerprint ابتدا/انتها را مقایسه می‌کند، pytest basetemp/cache را در temporary directory می‌گذارد و JSONL schema version **1** با `started`, `stage_started`, `stage_finished`, `result` می‌دهد. `VerificationRequest.options()` عمداً `max_parallel_checks` را حذف می‌کند و worker آن را جدا به موتور می‌دهد؛ این forwarding را دو بار اضافه نکنید. `accepted=true` فقط پذیرش submission است. موفقیت قابل‌اتکا نیازمند terminal job `succeeded`, `exit_code=0`, آخرین JSONL result معتبر، `ok=true`, `required_unknown=false`, `freshness=current` است. قطع client، job durable را cancel نمی‌کند. fingerprint ابتدا/انتها transient edit/revert را اثباتاً کشف نمی‌کند.

`verify_changed_repository` کارها را در ترتیب ثابت `_CHECKS = syntax,ruff,mypy,pytest` می‌سازد، نه ترتیب دلخواه `checks`. syntax در حالت parallel preflight است؛ failure آن analyzerها را skipped می‌کند، ولی skipped ناشی از نبود Python target جلوی full checks را نمی‌گیرد. برای Ruff/mypy در full mode target برابر `.` و برای pytest full target list خالی است. `ThreadPoolExecutor(max_workers=max_parallel_checks)` و `pool.map` نتیجه‌ها را در ترتیب taskها برمی‌گردانند، هرچند callback رخدادها ترتیب زمانی واقعی دارند. callback lock فقط نوشتن concurrent آن را serialize می‌کند.

budget کل از `overall_started` پس از ساخت plan شروع می‌شود؛ هر analyzer هنگام شروع، `min(stage_timeout, remaining_total)` می‌گیرد. queued task پس از پایان budget اجرا نمی‌شود. syntax compile به شکل cooperative deadline درون هر فایل محدود نشده و خروج از context thread pool منتظر running task می‌ماند؛ total timeout را نباید hard kill کل function دانست. runnerها به `core/executor.py:run_bounded` متکی‌اند؛ process timeout cleanup و pipe drain زمان اضافه دارند. missing module و exception به unavailable و `required_unknown=true` تبدیل می‌شوند. `tests/test_parallel_verification.py` overlap/cap/order، syntax barrier، رد mode ناسازگار، full-mode target و exception/timeout را پوشش می‌دهد؛ در این نوبت نتیجه اجرایی ندارد.

### ۲.۳ چرا foreground `verify_changes` هنوز parallel option ندارد؟

**CURRENT.** nested `verify_changes` در `tools/testing/registry.py` پارامتر `max_parallel_checks` ندارد و هنگام صداکردن `verify_changed_repository` آن را forward نمی‌کند؛ موتور default=1 را می‌گیرد. علت قابل‌اثبات این شکاف signature/forwarding/schema است. از سورس نمی‌توان انگیزه تاریخی یا تصمیم قبلی برای پنهان‌کردن آن را نتیجه گرفت. annotation ابزار `OPEN_WORLD_WRITE` باقی است؛ pytest repository code اجرا می‌کند و `ruff_fix` هم ممکن است mutation کند. foreground serial با `ruff_fix=true` باید همچنان مجاز بماند.

caller دیگر `core/workflow_actions.py:_verify_changes` است: `VerifyChangesInput` و allowlist forwarding آن نیز `max_parallel_checks` ندارند. فاز ۱ فقط public foreground tool را گسترش می‌دهد؛ workflow action تا فاز ۴ عمداً serial می‌ماند و آن موقع تغییرش جدا و با بودجه nesting بررسی می‌شود. `scripts.validation_watch` نیز caller موتور است؛ default serial آن حفظ می‌شود. public catalog tool count با افزودن parameter نباید عوض شود.

### ۲.۴ چرا `collect_diagnostics` serial است و قرارداد واقعی آن چیست؟

**CURRENT.** این تابع nested در `tools/testing/registry.py:register`، حلقه `for backend in selected` دارد و runnerها را مستقیم به نوبت فراخوانی می‌کند؛ scheduler یا pool ندارد. `selected = backends or [ruff,mypy,pytest]` است: هم `None` و هم `[]` به default تبدیل می‌شوند؛ backend تکراری فعلاً دوباره اجرا می‌شود. `timeout_sec` فعلی **برای هر backend** است؛ سه backend serial می‌توانند تقریباً سه برابر آن به‌علاوه cleanup زمان ببرند. total deadline مستقل فعلاً وجود ندارد.

stageها ترتیب request را نگه می‌دارند و فقط `backend`, `ok`, `unavailable`, `timed_out`, `diagnostic_count` را دارند. `merge_diagnostics` پس از جمع‌آوری، normalize و dedup می‌کند: کلید dedup شامل file/line/column/severity/code/message/source است؛ source متفاوت dedup نمی‌شود. ترتیب نهایی با file casefold، line، column، severity، source، message تعیین می‌شود؛ cap **بعد از merge** اعمال و `truncated` از تعداد unique merged items محاسبه می‌شود. هر runner قبلاً diagnostic cap خود را اعمال کرده؛ `truncated=false` این API تضمین کامل‌بودن خروجی خام analyzer نیست. تغییر معنی truncation به ادعای جدید جامع بدون version/contract مجاز نیست.

`required_unknown` فعلی فقط از unavailable/timed_out stageها به دست می‌آید؛ diagnostic خالی به‌خودی‌خود موفقیت نیست. exception غیرمنتظره در حلقه فعلی تا `compact_errors` می‌رود و لزوماً پاسخ جزئی همه backendها نمی‌دهد. فاز ۲ باید behavior خطای جدید را شفاف و مشترک در mode serial/parallel کند، بدون مخفی‌کردن unknown یا diagnostics شاخه‌های موفق. validation و aggregation موجود دوباره نوشته نمی‌شوند.

### ۲.۵ چرا WorkflowExecutor خطی است؟

**CURRENT.** `StepDefinition` dataclass frozen شامل `name`, `action`, `arguments`, `timeout_sec`, `max_retries`, `postcondition`, `execution_intent` است. identity dependency یا `depends_on` ندارد. `WorkflowDefinition` فقط `name`, `steps: tuple[StepDefinition,...]`, `description` دارد. `StepInput`/`WorkflowInput` Pydantic در `tools/workflows/registry.py` extra را رد می‌کنند؛ حداکثر **100 step**، timeout عمومی step حداکثر **3600** و retry حداکثر **10** است. `name` فعلی display label است و uniqueness آن enforce نشده است؛ تبدیل ناگهانی آن به ID، workflowهای قدیمی با label تکراری را می‌شکند.

`WorkflowExecutor.execute` تنها queued را می‌پذیرد، materialize operations، acquire lease و transition به running می‌کند؛ بعد `for index in range(current_step,len(steps))` دارد. هر step و retry آن تا پایان اجرا، evidence و operation/step checkpoint و `advance_running(index+1)` می‌رود. یک operation اصلی با `operation_index=0` به هر step وصل است. join، ready queue و active set ندارد. `current_step` یک cursor خطی است، نه تعداد completedها یا frontier.

`tools/workflows/registry.py:workflow_execute` اجرای sync را با `asyncio.to_thread` منتقل می‌کند؛ این کار event loop را آزاد می‌کند و stepها را موازی نمی‌کند. `scripts/workflow_worker.py:main` queue را قدیمی‌تر اول می‌خواند و workflowهای هر worker را در حلقه serial اجرا می‌کند. چند worker/process یا چند MCP request می‌توانند workflowهای مختلف را اجرا کنند، ولی سقف جهانی native workflow execution در `Settings` موجود نیست. `MCP_MAX_RUNNING_JOBS` خود workflow thread را محدود نمی‌کند؛ تنها jobهایی که workflow submit می‌کند در آن سقف‌اند.

timeout در مدل فعلی پارامتر handler است، نه hard deadline سراسری thread: `_check_file` صریحاً `del timeout_sec` دارد و فایل را کامل می‌خواند؛ `prepare_action_intent` برای patch یک dry-run با timeout ثابت60 و برای Git probeهای10ثانیه دارد. پس جمع timeoutهای declared تضمین wall-clock execution فعلی نیست. فاز۶ باید deadline propagation و bounded reads/intent preparation را برای DAG enforce و limitations legacy را با regression روشن کند؛ timeout coordinator به‌تنهایی thread را متوقف نمی‌کند.

`route_planner` در constructor اجباری است: callable یا `NATIVE_ONLY_ROUTING`؛ omission/None مجاز نیست. route فقط advisory است و `_plan_route` باید با `ActionDescriptor.execution_route` ثابت منطبق شود و decision پیش از effect persist شود. اضافه‌کردن DAG مجوز external workflow action، arbitrary execution، Visual Grounding یا delegation نیست.

### ۲.۶ تضمین و حد lease، CAS، persistence و recovery

**CURRENT.** `WorkflowStore` schema **6** با جدول‌های `workflows`, `workflow_steps`, `workflow_operations`, `workflow_leases`, `workflow_events`, `workflow_maintenance` است. SQLite WAL، busy timeout **10s**، FK و transactionهای کوتاه `BEGIN IMMEDIATE` دارد. migration version check با `exclusive_file_lock` serialize می‌شود. نسخه DB جدیدتر fail closed با `workflow_schema_too_new` است. این سامانه Alembic ندارد؛ migration باید در الگوی خود store بماند.

`canonical_execution_definition` payload دقیق durable را از `asdict` می‌سازد؛ `execution_definition_json/hash` با public `definition_json` redacted متفاوت‌اند. legacy فاقد exact payload، با `execution_compatible=0` مجاز به اجرای دوباره نیست. `execution_definition()` از exact payload بازسازی می‌کند. `create` هم definition و هم inputs hash را در dedup می‌سنجد. افزودن default dataclass fields می‌تواند canonical hash قدیمی را ناخواسته عوض کند؛ این یکی از ریسک‌های اصلی migration است.

`_materialize_workflow` operation ID/idempotency را از workflow ID، step index، operation index و definition hash می‌سازد؛ operation موجود با definition متفاوت conflict است. completion record و ID قدیمی را بازتولید نکنید. `checkpoint_operation` lease token/expiry و transition را در transaction می‌سنجد؛ نسخه operation را increment می‌کند، اما API فعلی آن `expected_version` ندارد. `checkpoint_step` همه transitionهای StepState را validate نمی‌کند و transaction جدا دارد. `transition`, `advance_running`, `request_cancel`, `resolve_uncertain_operation` CAS دارند. پس عبارت «همه checkpointها CAS یکسان دارند» درباره وضع فعلی صحیح نیست.

`acquire_lease`: یک رکورد برای هر workflow، token یکتا، TTL **5..300s**؛ owner متفاوت با lease زنده رد می‌شود، ولی owner ID یکسان lease جاری را پس می‌گیرد. در parallel scheduler جدید owner ID باید برای هر execute attempt یکتا باشد و داخل همان execute هم یک owner کنترل‌کننده aggregate وجود داشته باشد. `_LeaseHeartbeat` در طول action و retry backoff فعال است، interval از TTL/3 بین **1 و 10s**؛ پیش از پذیرفتن نتیجه، `ensure_current` lease را چک می‌کند. `release_lease_if_current` lease جایگزین را حذف نمی‌کند.

`prepare_action_intent` پیش از mutation intent و postcondition نتیجه‌ناوابسته می‌سازد: hashهای patch، git index paths، before HEAD/staged tree/message hash، یا durable job request key. executor operation را running checkpoint می‌کند و بعد dispatch می‌کند؛ بنابراین crash در شکاف dispatch هم conservatively uncertain است. موفقیت effect بدون checkpoint، موفقیت workflow محسوب نمی‌شود.

`WorkflowStore.__init__`، `recover_interrupted` را اجرا می‌کند: workflowهای running/cancelling بدون lease زنده را می‌گیرد و **فقط operation در current_step** را بررسی می‌کند. operation succeeded، aggregate را به completed/paused/cancelled می‌رساند؛ failed/cancelled را حفظ می‌کند؛ created با attempts صفر و بدون started_at را safe paused می‌کند؛ بقیه uncertain می‌شوند. این منطق برای چند branch running کافی نیست.

`reconcile_operation` فقط postcondition را evaluate می‌کند و action را dispatch نمی‌کند. `resolve_uncertain_operation` operation/step/workflow را atomic و versioned حل می‌کند، اما نتیجه workflow را با `step_index+1` می‌سازد؛ این نیز خطی است. `workflow_resume` فعلی uncertain aggregate یا حتی یک operation uncertain را رد می‌کند. retry فعلی فقط `TransientActionError` و `max_retries` را در executor کنترل می‌کند؛ `ActionDescriptor.retry_policy` وجود دارد ولی loop، descriptor policy را مستقیماً gate نمی‌کند. فاز ۶ باید policy واقعی را پیش از retry enforce کند و هر تغییر behavior ایمنی را مستند سازد.

**حد تضمین:** lease مانع دو owner معتبر در DB است، نه fencing فیزیکی همه processها و ابزارهای خارجی. worker قدیمی پس از expiry ممکن است action را همچنان انجام دهد، هرچند checkpointش رد شود. exactly-once effect دلخواه روی filesystem/Git/شبکه از lease به‌تنهایی حاصل نمی‌شود؛ no duplicate در طرح یعنی dispatch یکتای admitted attempt و عدم replay بی‌شاهد، با ثبت uncertainty در crash window.

### ۲.۷ cancellation، restart و process ownership فعلی

**CURRENT.** `request_cancel` روی running حالت `cancelling` و درخواست durable می‌گذارد؛ بعضی حالت‌های غیرrunning را مستقیم cancelled می‌کند. executor cancel را بین stepها و قبل retry می‌سنجد. `ActionContext` برای handlerهای context-aware signal فراهم می‌کند؛ `_run_durable_job` با `JobStore.cancel` به child job منتقل می‌کند و external_ref/request_key را persist می‌کند. بیشتر actionها default `cancel_mode=deferred` دارند؛ از جمله verification، patch و Git. thread را نمی‌توان با `Future.cancel` متوقف‌شده فرض کرد. تست `test_cancel_during_long_read_only_verification_preserves_result_then_stops` همین حفظ نتیجه و توقف بعدی را می‌سنجد.

`run_bounded` با `OwnedProcess` Windows Job Object یا POSIX process group، timeout/tree cleanup و pipe readers را اداره می‌کند. readerها بعد از root termination drain می‌شوند؛ `capture_complete=false` موفقیت را رد می‌کند. processهای nested session، cleanup denial و خروج root باید با backend واقعی آزموده شوند. `RuntimeLifecycle` admission mutation را هنگام drain می‌بندد؛ این قرارداد باید در dispatch هر branch رعایت شود، نه فقط درخواست parent.

`core/heartbeat.py:lifespan` وضعیت lifecycle/recovery و extension startup/shutdown را مدیریت می‌کند؛ workflow worker جدید خودکار از این lifespan ساخته نمی‌شود. supervisor در `remember_children` عمداً `scripts.job_worker` و descendantهای آن را از cleanup runtime مستثنا می‌کند تا durable job با restart MCP از بین نرود. workflow thread داخل MCP چنین استقلالی ندارد؛ از دست رفتن runtime به lease expiry و recovery متکی است. دوام **state** و ادامه خودکار **execution** دو ادعای متفاوت‌اند. shutdown supervisor کامل، runtime-only restart، و process kill باید جدا تست شوند.

### ۲.۸ ResourceLocks فعلی

**CURRENT.** `ResourceLocks.sync` canonical pathها را resolve/normpath/normcase، dedup و sort می‌کند؛ سپس RLockهای process-local را به ترتیب canonical می‌گیرد و برعکس آزاد می‌کند. refcount entryها را پس از آخرین user حذف می‌کند. این طراحی reentrant، جلوگیری از deadlock ترتیب معکوس در یک call، و overlap کلیدهای مستقل را فراهم می‌کند. `tests/test_resource_locks.py` همین‌ها و Windows aliases و release بعد exception را می‌سنجد.

این lockها **read/write distinction، durable ownership، TTL، namespaceهای Git/session، یا conflict parent/child path ندارند**. کلید `repo/` و `repo/a.py` صرفاً دو کلید متفاوت‌اند. دو process حتی با یک نام path دو registry مجزا دارند. callerهای واقعی شامل filesystem write/move/delete/patch، language edits، documents، memory، artifact/retention و job output readers هستند. documents علاوه بر این‌ها file-lock اختصاصی دارند؛ existence آن قفل به معنی پوشش همه filesystem و Git نیست. writer خارجی editor/Git/سایر برنامه‌ها با این registry هماهنگ نیست.

`tools/filesystem/patches.py:apply_patch_transaction` از lock چندpath و SHA/precondition استفاده می‌کند. claim آینده باید قبل از `prepare_action_intent` گرفته و تا postcondition/checkpoint و cleanup معتبر بماند؛ گرفتن claim بعد از محاسبه intent، TOCTOU ایجاد می‌کند. SHA guard و atomic publication باید حفظ شوند؛ lock جای آن‌ها را نمی‌گیرد.

### ۲.۹ Extension concurrency و مرز استقلال آن

**CURRENT.** `ExtensionLimits`: configured=**16**، enabled=**8**، parallel startup=**2**، calls per extension=**2**، global calls=**8**. startup/call/shutdown default سقف‌ها به‌ترتیب **15/30/10s**، handshake/discovery هرکدام **5s**، restart attempts **3** در **600s** و unresolved operation quota برابر **128** است. این‌ها سقف source هستند؛ فعال‌بودن واقعی extensionهای deployment در این audit تأیید نشده است.

`ExtensionLifecycleManager` startup و global-call semaphore و هر `ExtensionOwner` semaphore خودش را دارد. `connect_and_discover_tools` startup deadline را پیش از انتظار gate می‌سازد؛ gate پس از startup/discovery آزاد می‌شود و تمام عمر session را اشغال نمی‌کند. یک owner task connection/session را enter/exit می‌کند. import/list_tools/router planning/health/panel نباید provider process فعال کنند.

`guard_call_admission` policy، trust/launch approval، profile، live generation/schema lease، mutation/recovery capacity و callback denial را پیش از dispatch می‌سنجد؛ global و بعد per-extension permit می‌گیرد، بعد از queue دوباره eligibility را چک می‌کند و در cancellation/finally آزاد می‌کند. انتظار per-extension در حالی است که global permit نگه داشته می‌شود؛ پس تعداد permitted برابر effective throughput قطعی نیست. `execute_guarded_call` یک total outer deadline دور validation/admission/dispatch دارد؛ timeout پس از dispatch mutation را rollback فرض نمی‌کند. تست‌های `test_call_admission.py` denied-no-dispatch، isolation، stale generation و global/per-extension timeout را بررسی می‌کنند.

این semaphoreها داخل همان manager/process هستند؛ سقف jobs یا workflowها را مصرف نمی‌کنند و میان چند manager مستقل distributed نیستند. در بودجه host باید جدا حساب شوند. طرح شش‌فازی هیچ external workflow action یا تغییر سقف extension را اضافه نمی‌کند. zero-extension mode، native identity/schema/profile و owner-session semantics باید حفظ شوند.

## ۳. هدف معماری، non-goals و Compatibility Contract

هدف: هم‌زمانی **محدود، صریح و قابل‌اندازه‌گیری**، با نتیجه مرتب، durable state، fencing در checkpoint، cancel درست، جلوگیری از conflict و recovery بدون blind replay. افزایش throughput نباید unknown را success کند، uncertainty را حذف کند یا تعداد side effectهای admitted را بیشتر از یک attempt معتبر کند.

non-goals مشترک: distributed multi-host scheduler، broker/Celery/Ray، arbitrary command workflow action تازه، external workflow invocation، nested autonomous agents، Visual Grounding، cleanup unrelated، تغییر publish/tag، تبدیل همه sync tools به async، transaction بلند دور action، rollback خودکار Git/فایل‌های تکمیل‌شده و ادعای sandbox برای pytest. استاندارد Python پروژه **3.10+** است؛ APIهای مخصوص 3.11 مثل `asyncio.TaskGroup` بدون fallback مبنای طرح نیستند. AnyIO موجود و `concurrent.futures` کفایت دارند.

### ۳.۱ Security/Safety Invariants

| ID | invariant الزامی و روش اثبات |
|---|---|
| INV-01 | هیچ dispatch بدون validation، eligibility، lifecycle admission و owner معتبر؛ denial test با marker صفر |
| INV-02 | dependency شرط ایمنی است، صرفاً اولویت نیست؛ descendant قبل از success همه parentها اجرا نمی‌شود |
| INV-03 | سقف step/analyzer/job/extension و waiting queue مستقل و bounded؛ peak با counter مستقل آزموده شود |
| INV-04 | succeeded/acknowledged معتبر دوباره dispatch نمی‌شود؛ effect counter پس از restart ثابت می‌ماند |
| INV-05 | timeout/cancel/lease expiry نشانه rollback نیست؛ unknown به uncertainty و reconciliation می‌رود |
| INV-06 | durable intent پیش از ممکن‌شدن effect؛ storage failure پیش از barrier یعنی dispatch صفر |
| INV-07 | conflict resource read/write در همه workflow workerهای هم-state serialize می‌شود؛ external writer از آن مستثنا و نیازمند precondition است |
| INV-08 | completed branch و evidence آن با failure/cancel شاخه دیگر از بین نمی‌رود |
| INV-09 | owner قدیمی aggregate/claim owner جدید را overwrite/release نمی‌کند |
| INV-10 | خروجی deterministic به معنای ترتیب projection/ready selection است؛ ترتیب پایان process مستقل وعده داده نمی‌شود |
| INV-11 | secrets/payload خام در metrics، error، event، panel یا artifact جدید ثبت نمی‌شوند؛ canary test لازم است |
| INV-12 | صفر extension و inputهای legacy، رفتار native قبلی را حفظ می‌کنند؛ gate ناقص خاموش می‌ماند |

### ۳.۲ Compatibility Contract ثابت

1. `verify_changes` بدون parameter تازه دقیقاً serial و `fail_fast=true` می‌ماند؛ delivery، affected tests، targets، stage names و errors موجود حفظ می‌شوند. foreground fix در serial حفظ می‌شود.
2. `collect_diagnostics` default serial می‌ماند؛ timeout قدیمی per-backend، ترتیب stageها، رفتار `backends=[]` و duplicate backend، merge/dedup/cap و shape پاسخ قبلی حفظ می‌شوند. exception غیرمنتظره به پاسخ structured unknown تبدیل‌شدن، تغییر صریح و آزموده فاز ۲ است.
3. workflow قدیمی بدون DAG opt-in همان ترتیب index، retry/cancel و public states را دارد. display name تکراری در legacy ممنوع نمی‌شود. builtins خودکار DAG نمی‌شوند.
4. exact definition/inputs hash، IDهای operations و request keyهای قبلی نباید با افزودن default fields عوض شوند. schema جدید روی DB قدیمی additive است و old binary روی DB جدید fail closed می‌کند.
5. `current_step` برای legacy همان cursor قبلی؛ برای DAG فقط projection سازگار **کمترین index ناتمام** یا N در همه‌تکمیل است و هرگز authority scheduling نیست. فیلدهای جدید DAG additive و مستند خواهند بود.
6. `route_planner` اجباری، allowlist action، postcondition، native route matching، runtime mutation gate، profiles و redaction باقی می‌مانند. `OPEN_WORLD_WRITE` testing به read-only تنزل نمی‌کند.
7. `workflow_resume` با هر operation uncertain رد می‌شود. cancelled terminal workflow دوباره resume نمی‌شود؛ کار تازه نیاز به workflow جدید دارد. موفقیت partial با موفقیت کل متفاوت است.
8. APIهای تازه در این سند همگی **PROPOSED** هستند، حتی اگر اسم پیشنهادی دقیق داده شده باشد. تغییر default یا شماره migration پس از drift source فقط با Decision Log و regressions مجاز است.

## ۴. معماری پیشنهادی مشترک و Budget Model

### ۴.۱ مالکیت اجرا و single source of truth

**PROPOSED.** یک `WorkflowExecutor` owner برای هر execute attempt، یک heartbeat در طول تمام اجرای آن، یک coordinator برای ready set و aggregate و یک bounded worker pool برای stepها. worker نتیجه را به coordinator برمی‌گرداند؛ هر operation durable attempt/token خودش را دارد. coordinator تنها workflow aggregate را می‌نویسد؛ operation context checkpointها با attempt token مجازند. snapshot in-memory cache است، نه حقیقت مستقل از SQLite.

خط لوله هر step:

```text
validate immutable graph -> acquire workflow lease -> recompute ready candidates
 -> atomic capacity/resource reservation -> recheck cancel/lease/preconditions
 -> prepare_action_intent under resource claim
 -> durable may-dispatch barrier + step/operation running checkpoint
 -> allowlisted action -> postcondition -> atomic branch checkpoint
 -> recompute graph projection -> release safe claims/capacity -> schedule next
```

این pipeline توصیف طراحی است، اسم تابع موجود نیست. side effect هرگز داخل transaction SQLite انجام نمی‌شود. قبل از barrier گرفتن process ownership ضروری است؛ شکست DB/ownership پیش از dispatch باید marker صفر داشته باشد. action غیرقابل‌cancel باید deferred و با deadline محدود باقی بماند؛ terminate thread جایگزین قرارداد cancellation نیست.

### ۴.۲ nested parallelism و سقف‌ها

مقادیر فعلی source: `J=4` job، `A<=3` analyzer/job، extension calls `E<=8` و per-provider `e<=2`، startup `S<=2`. در workload چهار background verification، upper bound analyzerهای مستقیم **4×3=12** است؛ علاوه بر چهار job worker، چهار verification worker، pipe threads، scheduler/reaper threads و childهای احتمالی pytest. این محاسبه سناریو است، نتیجه اندازه‌گیری نیست. pytest/plugin/BLAS/xdist می‌تواند descendantها را بیشتر کند. source فعلی native foreground calls و workflow instances را globally محدود نمی‌کند.

فرمول حسابداری پیشنهادی:

```text
active_analyzers = background_analyzers + foreground_analyzers + workflow_analyzers
active_analyzers <= analyzer_budget
active_steps_per_workflow <= min(requested_max_parallel_steps, configured_step_cap)
active_steps_all_workflows <= workflow_step_budget
active_job_roots + queued_launch_reservations <= configured_job_budget
active_extension_calls <= 8 per manager; active_calls_for_provider <= 2
host_pressure = owned_roots + descendants + threads + RSS + open_handles + disk_IO
```

عدد **PROPOSED** برای اولین opt-in workflow: default `max_parallel_steps=1`، request/config hard upper bound **16** برای step pool؛ مقدار admission واقعی از host baseline پایین‌تر clamp می‌شود. `MCP_MAX_PARALLEL_WORKFLOW_STEPS` و `MCP_MAX_ACTIVE_WORKFLOW_STEPS` و `MCP_MAX_ACTIVE_ANALYZERS` **نام‌های پیشنهادی** هستند؛ در `Settings` فعلی وجود ندارند. default global analyzer budget پیشنهادی برابر `Settings.max_running_jobs` و default global workflow-step budget نیز همان عدد است؛ این policy محافظه‌کارانه است، نتیجه ظرفیت سخت‌افزار نیست. با J=4، مجموع analyzerهای instrumented حداکثر 4 است، نه 12. فعال‌کردن broker در فاز ۴/۵ مستلزم gate و تغییر observable queue timing است؛ فازهای ۱/۲ تا آن زمان فقط per-call cap و هشدار nesting دارند و نباید globally bounded معرفی شوند.

بودجه runtime managed باید **در state DB موجود** persist شود، نه semaphore جدا در هر process. هدف فاز ۴ ساخت admission record برای workflow stepها در `workflows.sqlite3` است؛ فاز ۵ همان را با resource claims و analyzer permit گسترش می‌دهد. JobStore همچنان authority job root است و extension manager authority extension call است؛ در دو DB transaction اتمی ادعا نمی‌شود. تا اتصال analyzer worker به budget مشترک کامل نشده، opt-in parallel workflow verification به inner `max_parallel_checks=1` محدود می‌شود.

قانون nesting نهایی: outer و inner ظرفیت یک منبع fungible را دوبار نگیرند. slot step یک orchestration slot است؛ analyzer permit ظرفیت process-heavy است. parent هنگام انتظار child job **job slot نمی‌گیرد**؛ child تنها در JobStore claim می‌کند. analyzer permit فقط نزدیک dispatch analyzer گرفته می‌شود و زمان انتظارش جزو total deadline است. parent هیچ analyzer permitی در انتظار submit/claim job نگه نمی‌دارد. conflict claim لازم برای mutation تا تعیین تکلیف child باقی می‌ماند؛ scheduler نباید workflow دیگری با همان conflict را admit کند. admission all-or-none، بدون انتظار در transaction و بدون hold-and-wait میان permits هم‌رتبه است.

Extension budget دست‌نخورده باقی می‌ماند؛ host benchmark باید provider processهای زنده و callها را هم حساب کند. workflow action خارجی اضافه نمی‌شود. کاهش config budget، running کارها را terminate نمی‌کند؛ admission تازه تا رسیدن به سقف جدید متوقف می‌شود. همه workerهای مشترک باید policy fingerprint سازگار داشته باشند؛ ناسازگاری budget/schema، activation را fail closed می‌کند. resource pressure high watermark در benchmark تعیین و ثبت می‌شود، نه با عدد حدسی در این سند.

### ۴.۳ Performance Budgets و روش اندازه‌گیری

| معیار | baseline و measurement | target methodology / gate |
|---|---|---|
| serial vs parallel wall-clock | یک SHA/input/env، cold و warm جدا، حداقل 5 تکرار پس از warmup پیشنهادی؛ median/p95 و پراکندگی ثبت شود | برای سه analyzer مستقل fixture با مدت کنترل‌شده، overlap واقعی و کاهش wall-clock نزدیک critical path با overhead توضیح‌پذیر؛ برای repo واقعی regression budget در `P1-T01` قبل تغییر تصویب و ثبت شود |
| peak workers/processes/threads | sampling psutil همراه event counters و process identities؛ worker/root/descendant جدا | سقف admitted هیچ‌وقت رد نشود؛ تعداد descendantهای غیرقابل‌کنترل جدا گزارش شود؛ host فشار شدید نگیرد |
| queue latency | submit/reserve/claim/dispatch timestamp با monotonic duration | p50/p95 برای cap=1 و cap>1؛ starvation صفر در workload finite؛ deadline صرف waiting پنهان نشود |
| DB lock contention | زمان انتظار BEGIN IMMEDIATE، busy errors، transaction duration و WAL size | هیچ transaction دور process/I/O؛ under stress هیچ lost checkpoint/false success؛ نسبت تغییر نسبت به baseline ثبت و review شود |
| cancellation latency | زمان durable request تا no-new-dispatch، signal تا child exit و terminal checkpoint جدا | no-new-dispatch پس از barrier cancel؛ p95 cleanup کمتر از بودجه مصوب host در `P6-T02`؛ polling 1s فعلی به معنی SLA نیست |
| restart recovery time | kill واقعی تا recovered status و سپس explicit resume تا readiness | تمام branchها طبقه‌بندی شوند؛ pending uncertainty هرگز برای سریع‌شدن حذف نشود؛ target بعد baseline freeze شود |
| resource pressure | Core/children RSS، handle/FD، disk spool/WAL، CPU، context switches | افزایش متناسب cap و محدودیت memory/output؛ leak روندی در soak قابل‌قبول نیست |
| deterministic output | چند completion permutation با input یکسان | canonical projection یکسان، به‌جز timestamp/duration/physical event order |

هیچ عدد این جدول نتیجه اجرا نیست. پیش از optimization باید baseline artifact به مسیر runtime/test artifact مجاز و bounded نوشته، SHA و host ثبت شود؛ benchmark data خام در plan کپی نمی‌شود. انتخاب target باید قبل مقایسه نهایی در Decision/Verification Ledger ثبت شود تا هدف بعد از دیدن نتیجه جابه‌جا نشود.

## ۵. Execution Status Dashboard و قواعد اداره اجرا

این dashboard فقط وضعیت **implementation آینده** را نشان می‌دهد. audit سند جدا ثبت می‌شود. دقیقاً **6 Phase و 48 Task اصلی** وجود دارد؛ هر فاز هشت Task دارد. subtaskهای داخل Task، gate و ledger ID، Task اصلی اضافه محسوب نمی‌شوند.

### ۵.۱ status enum و transition

| Status | تعریف |
|---|---|
| NOT_STARTED | task هنوز شروع نشده؛ هیچ completion ادعا نمی‌شود |
| IN_PROGRESS | owner مشخص مشغول تغییر مجاز است؛ Started At ثبت شده |
| VERIFYING | تغییر آماده است و checks واقعی در حال اجرا/بازبینی‌اند |
| BLOCKED | ادامه وابسته به مانع خارجی، dependency یا authorization است؛ Issue ID اجباری |
| FAILED | check یا acceptance با failure مشخص شکست خورده؛ Issue ID و evidence اجباری |
| DONE | acceptance و gate متناسب task با evidence واقعی پاس شده‌اند |
| DEFERRED | فقط با تصمیم صریح owner پروژه از release scope کنار گذاشته شده؛ علت و اثر gate ثبت شود |

مسیر عادی `NOT_STARTED -> IN_PROGRESS -> VERIFYING -> DONE` است؛ از وضعیت فعال به BLOCKED/FAILED و پس از رفع با journal به IN_PROGRESS برگردید. DONE فقط با تغییر relevant code/config/input یا کشف evidence غلط و ثبت علت reopen می‌شود. DEFERRED معادل DONE نیست و gate الزامی را پاس نمی‌کند. phase تا همه هشت Task و GO gate آن DONE نشده، DONE نمی‌شود. برای اجرای مرتبط با قرارداد قدیمی، `DONE` این سند معادل verification واقعی است؛ enum محصول تغییر نمی‌کند.

**قانون اجباری هر agent:** پیش از شروع task، همین سطر و pointer را IN_PROGRESS و Owner/Started At/Last Updated را پر کند؛ هنگام تست VERIFYING؛ failure را با PAR ID به FAILED یا BLOCKED ببرد؛ فقط پس از ثبت command/exit/result/diff/SHA، DONE و checkbox را `[x]` کند. یک writer برای این plan؛ owner taskها قابل‌تفکیک‌اند ولی update ledger از writer واحد. timestampها ISO-8601 با timezone، ترجیحاً UTC، هستند. «آغاز نشده»، «ثبت نشده چون اجرا نشده» و «بدون commit» در dashboard وضعیت واقعی‌اند، نه وعده تکمیل فیلد در آینده.

### ۵.۲ Phase Dashboard

| Phase | Status | Current Task | Owner/Executor | Started At | Last Updated | Blocker/Issue ID | Verification State | Commit SHA/PR | Notes |
|---|---|---|---|---|---|---|---|---|---|
| P1 | VERIFYING | foreground parallel verification؛ G1 same-HEAD acceptance | اجرای محلی | 2026-10-17 | 2026-10-18 | hosted 10/10 PASSED; independent review pending | PASSED | 46dbf30; PR81 HEAD81d9f42 | کد و focused evidence موجود؛ GO هنوز ثبت نشده |
| P2 | VERIFYING | diagnostics parallel؛ G2 same-HEAD acceptance | اجرای محلی | 2026-10-17 | 2026-10-18 | hosted 10/10 PASSED; independent review pending | PASSED | 6fc2aca; PR81 HEAD81d9f42 | deadline boundary و deterministic order بازتست شدند |
| P3 | VERIFYING | DAG graph/schema؛ G3 same-HEAD acceptance | اجرای محلی | 2026-10-17 | 2026-10-18 | hosted 10/10 PASSED; independent review pending | PASSED | c1561cf; PR81 HEAD81d9f42 | schema11 و compatibility موجود؛ release gate باز |
| P4 | VERIFYING | parallel scheduler؛ G4 same-HEAD acceptance | اجرای محلی | 2026-10-17 | 2026-10-18 | hosted 10/10 PASSED; independent review pending | PASSED | 26feeb9+3c5fe14; PR81 HEAD81d9f42 | barrier overlap proof؛ activation عمومی بسته |
| P5 | VERIFYING | P5-T07/08؛ resource ownership + cross-platform gates | اجرای محلی | 2026-10-17T20:00:17Z | 2026-10-18 | full local+hosted PASS; independent review pending | PASSED | 45715d6..81d9f42; PR81 | native child exit bounded; public activation closed |
| P6 | VERIFYING | P6-T07؛ recovery/cancellation final gates | اجرای محلی | 2026-10-17 | 2026-10-18 | CI 10/10 PASS; independent review pending | PASSED | 88c55ab..81d9f42; PR81 | P6 source/tests implemented؛ P6-T08 GO not yet issued |

### ۵.۳ Task Dashboard

نام و work package هر task در فاز متناظر تعریف شده؛ `Current Task` در این جدول اقدام جاری همان task است. Verification State فقط یکی از `NOT_RUN`, `RED_CONFIRMED`, `RUNNING`, `PASSED`, `FAILED`, `SKIPPED_WITH_REASON` باشد. NOT_RUN یا SKIPPED gate را پاس نمی‌کند.

| Task | Status | Current Task | Owner/Executor | Started At | Last Updated | Blocker/Issue ID | Verification State | Commit SHA/PR | Notes |
|---|---|---|---|---|---|---|---|---|---|
| P1-T01 | VERIFYING | baseline و contract | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 46dbf30 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P1-T02 | VERIFYING | red schema/validation | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 46dbf30 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P1-T03 | VERIFYING | signature و forwarding | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 46dbf30 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P1-T04 | VERIFYING | compatibility regressions | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 46dbf30 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P1-T05 | VERIFYING | MCP/schema/profile checks | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 46dbf30 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P1-T06 | VERIFYING | docs و audit metadata | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 46dbf30 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P1-T07 | VERIFYING | focused/static validation | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 46dbf30 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P1-T08 | VERIFYING | G1 و checkpoint | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 46dbf30 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P2-T01 | VERIFYING | freeze diagnostics contract | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 6fc2aca / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P2-T02 | VERIFYING | red overlap/order tests | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 6fc2aca / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P2-T03 | VERIFYING | bounded dispatch | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 6fc2aca / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P2-T04 | VERIFYING | deadline/exception handling | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 6fc2aca / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P2-T05 | VERIFYING | merge/output regression | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 6fc2aca / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P2-T06 | VERIFYING | schema/docs/telemetry | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 6fc2aca / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P2-T07 | VERIFYING | measurement و checks | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 6fc2aca / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P2-T08 | VERIFYING | G2 و checkpoint | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 6fc2aca / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P3-T01 | VERIFYING | DAG identity contract | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | c1561cf / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P3-T02 | VERIFYING | red graph validation | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | c1561cf / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P3-T03 | VERIFYING | model و API validation | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | c1561cf / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P3-T04 | VERIFYING | schema migration | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | c1561cf / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P3-T05 | VERIFYING | readiness/projection | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | c1561cf / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P3-T06 | VERIFYING | legacy/recovery guard | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | c1561cf / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P3-T07 | VERIFYING | migration/static/full relevant | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | c1561cf / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P3-T08 | VERIFYING | G3 و checkpoint | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | c1561cf / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P4-T01 | VERIFYING | scheduler/attempt contract | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 26feeb9 + 3c5fe14 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P4-T02 | VERIFYING | red cap/lease/race tests | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 26feeb9 + 3c5fe14 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P4-T03 | VERIFYING | durable attempt/admission | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 26feeb9 + 3c5fe14 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P4-T04 | VERIFYING | bounded ready scheduler | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 26feeb9 + 3c5fe14 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P4-T05 | VERIFYING | branch aggregate/join | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 26feeb9 + 3c5fe14 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P4-T06 | VERIFYING | crash-safe branch scan | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 26feeb9 + 3c5fe14 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P4-T07 | VERIFYING | worker/telemetry/checks | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 26feeb9 + 3c5fe14 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P4-T08 | VERIFYING | G4 و checkpoint | اجرای محلی | اجرای قبلی؛ زمان دقیق ثبت نشده | 2026-10-17 | per-task acceptance و hosted gate هنوز بازبینی کامل نشده | NOT_RUN | 26feeb9 + 3c5fe14 / بدون PR | پیاده‌سازی بازیابی شده؛ DONE تاریخی فرض نمی‌شود |
| P5-T01 | VERIFYING | resource claim contract | اجرای محلی | 2026-10-17T21:09:45+00:00 | 2026-10-17T21:09:45+00:00 | G4؛ PAR-005 | RUNNING | 45715d6..429ae83 / PR81 | focused evidence در journal؛ پذیرش کامل G5 باز |
| P5-T02 | VERIFYING | red cross-process conflict | اجرای محلی | 2026-10-17T21:09:45+00:00 | 2026-10-17T21:09:45+00:00 | P5-T01 | RUNNING | 45715d6..429ae83 / PR81 | focused evidence در journal؛ پذیرش کامل G5 باز |
| P5-T03 | VERIFYING | persisted claim/admission | اجرای محلی | 2026-10-17T21:09:45+00:00 | 2026-10-17T21:09:45+00:00 | P5-T02 | RUNNING | 45715d6..429ae83 / PR81 | focused evidence در journal؛ پذیرش کامل G5 باز |
| P5-T04 | VERIFYING | repair مشخص‌شده VERIFIED؛ broader task gate باز | اجرای محلی | 2026-10-17T20:00:17Z | 2026-10-18 تهران | independent review و باقی acceptanceهای G5 | PASSED | بدون commit جدید/PR | final same-diff pytest1417pass/23skip؛ no public DAG activation |
| P5-T05 | VERIFYING | nested analyzer budget | اجرای محلی | 2026-10-17T21:09:45+00:00 | 2026-10-17T21:09:45+00:00 | P5-T04 | RUNNING | 45715d6..429ae83 / PR81 | focused evidence در journal؛ پذیرش کامل G5 باز |
| P5-T06 | IN_PROGRESS | retention و stale/precondition recovery | اجرای محلی | 2026-10-17T20:38:12+00:00 | 2026-10-17T20:38:12+00:00 | PAR-006 | RUNNING | 45715d6 / PR81 | no TTL unlock؛ activation بسته |
| P5-T07 | VERIFYING | pressure/deadlock/checks | اجرای محلی | 2026-10-17T21:09:45+00:00 | 2026-10-17T21:09:45+00:00 | P5-T06 | RUNNING | 45715d6..429ae83 / PR81 | focused evidence در journal؛ پذیرش کامل G5 باز |
| P5-T08 | VERIFYING | G5 و checkpoint | تخصیص نیافته | آغاز نشده | 2026-10-17 | P5-T07 | NOT_RUN | بدون commit/PR | prerequisite P6 |
| P6-T01 | IN_PROGRESS | exhaustive recovery matrix | تخصیص نیافته | آغاز نشده | 2026-10-17 | G5 | NOT_RUN | بدون commit/PR | crash points |
| P6-T02 | IN_PROGRESS | fan-out cancellation | تخصیص نیافته | آغاز نشده | 2026-10-17 | P6-T01 | NOT_RUN | بدون commit/PR | cancel modes |
| P6-T03 | IN_PROGRESS | lease/process/restart hardening | تخصیص نیافته | آغاز نشده | 2026-10-17 | P6-T02 | NOT_RUN | بدون commit/PR | stop all new dispatch |
| P6-T04 | IN_PROGRESS | reconcile/resume/retry | تخصیص نیافته | آغاز نشده | 2026-10-17 | P6-T03 | NOT_RUN | بدون commit/PR | every uncertain branch |
| P6-T05 | IN_PROGRESS | stress/platform/security | تخصیص نیافته | آغاز نشده | 2026-10-17 | P6-T04 | NOT_RUN | بدون commit/PR | real subprocess |
| P6-T06 | IN_PROGRESS | CI/docs/rollback drill | تخصیص نیافته | آغاز نشده | 2026-10-17 | P6-T05 | NOT_RUN | بدون commit/PR | same-SHA |
| P6-T07 | IN_PROGRESS | independent review/release gates | تخصیص نیافته | آغاز نشده | 2026-10-17 | P6-T06 | NOT_RUN | بدون commit/PR | activation decision |
| P6-T08 | VERIFYING | G6 و final evidence | تخصیص نیافته | آغاز نشده | 2026-10-17 | P6-T07 | NOT_RUN | بدون commit/PR | release readiness |

## ۶. Runbook مشترک اجرای Task و Verification Commands

ترتیب شش فاز ثابت است: `G1 -> P2 -> G2 -> P3 -> G3 -> P4 -> G4 -> P5 -> G5 -> P6 -> G6`. حتی task ظاهراً مستقل فاز بعد پیش از GO شروع نمی‌شود. Gateهای P3 تا P5 اجازه merge زیرساخت با activation بسته می‌دهند؛ به معنی آماده‌بودن parallel workflow برای کاربر نیستند. feature activation نهایی در G6 است.

هر Task این چرخه را دارد:

1. status/pointer و owner را ثبت؛ HEAD/diff و source مرتبط را refresh؛ پیش‌نیازهای Task را بررسی کنید.
2. fixture و test نزدیک موجود را بخوانید؛ کوچک‌ترین assertion observable برای behavior موردنظر را بنویسید؛ test باید به علت درست RED شود، نه missing dependency یا setup خراب. command و failure در ledger بماند.
3. کمترین تغییر در boundary مالک؛ no-dispatch، durability، backward compatibility و cleanup را با همان assertion GREEN کنید.
4. فقط duplication ضروری را حذف؛ production abstraction یا dependency اضافی نسازید. relevant tests پس از آخرین edit دوباره اجرا شوند.
5. diff همه فایل‌های متعلق به Task را review؛ Ruff/mypy و checks متناسب را اجرا؛ evidence و اثر روی docs/config/export/profile را ثبت کنید.
6. commit کوچک با scope روشن؛ task وقتی DONE است که acceptance خود را پاس کرده باشد. phase merge وقتی مجاز است که hosted gates همان head پاس شده باشند. PR/SHA تا پیش از ساخت واقعاً «بدون commit/PR» است.

### ۶.۱ command sets

نام‌های `C1..C6` و `CSTATIC/CRELEASE` فقط shorthand سند هستند، script جدید نیستند. از root واقعی و Python معتبر checkout استفاده شود. برای تست مستقل، basetemp جدا و config extensions خاموش لازم است؛ تست fixture خودش opt-in محلی را ایزوله می‌کند.

```powershell
# CSTATIC — Windows؛ بعد از آخرین relevant edit
$env:MCP_EXTENSIONS_ENABLED = '0'
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy core tools scripts tests
.venv/Scripts/python.exe -m compileall -q core tools scripts tests
git diff --check

# C1
.venv/Scripts/python.exe -m pytest -o addopts= -q --basetemp=.pytest-tmp-par-p1 tests/test_parallel_verification.py tests/test_background_verification.py tests/test_mcp_integration.py tests/test_tool_profiles.py

# C2 — مسیر proposed فقط پس از ایجاد تست در P2-T02 اجرا می‌شود
.venv/Scripts/python.exe -m pytest -o addopts= -q --basetemp=.pytest-tmp-par-p2 tests/test_parallel_diagnostics.py tests/test_diagnostics.py tests/test_parallel_verification.py tests/test_mcp_integration.py

# C3 — proposed test_workflow_dag.py در P3-T02 ساخته می‌شود
.venv/Scripts/python.exe -m pytest -o addopts= -q --basetemp=.pytest-tmp-par-p3 tests/test_workflow_dag.py tests/test_workflow_models_contracts.py tests/test_workflow_migrations.py tests/test_workflow_persistence_integrity.py tests/test_workflow_store.py tests/test_builtin_workflows.py tests/test_workflow_tools.py tests/test_workflow_transitions.py

# C4 — test_parallel_workflow_scheduler.py proposed است
.venv/Scripts/python.exe -m pytest -o addopts= -q --basetemp=.pytest-tmp-par-p4 tests/test_parallel_workflow_scheduler.py tests/test_workflow_concurrency_gates.py tests/test_workflow_leases.py tests/test_workflow_lease_heartbeat.py tests/test_workflow_crash_recovery.py tests/test_workflow_worker.py tests/test_workflow_routing.py tests/test_job_admission.py

# C5 — دو فایل parallel_* این command proposed هستند
.venv/Scripts/python.exe -m pytest -o addopts= -q --basetemp=.pytest-tmp-par-p5 tests/test_parallel_workflow_resources.py tests/test_shared_analyzer_capacity.py tests/test_resource_locks.py tests/test_job_admission.py tests/test_job_scheduler.py tests/test_call_admission.py tests/test_workflow_postconditions.py tests/test_workflow_durable_reconciliation.py

# C6 — test_parallel_workflow_recovery.py proposed است
.venv/Scripts/python.exe -m pytest -o addopts= -q --basetemp=.pytest-tmp-par-p6 tests/test_parallel_workflow_recovery.py tests/test_workflow_cancellation.py tests/test_workflow_crash_recovery.py tests/test_workflow_reconciliation.py tests/test_workflow_durable_reconciliation.py tests/test_workflow_concurrency_gates.py tests/test_process_ownership.py tests/test_supervisor.py tests/test_lifecycle.py

# CRELEASE — تست محصول، این planning آن را اجرا نکرده است
.venv/Scripts/python.exe main.py --check
.venv/Scripts/python.exe -m scripts.health_check --json
.venv/Scripts/python.exe -m scripts.doctor --mode local-http --browser
.venv/Scripts/python.exe -m pytest -o addopts= -q --basetemp=.pytest-tmp-par-release
.venv/Scripts/python.exe -m scripts.platform_validation --json
```

`tests/test_workflow_models_contracts.py` در C3 یک **Create پیشنهادی** برای قرارداد dataclass/serialization است، با `tests/test_workflow_contracts.py` موجود اشتباه نشود؛ اگر افزودن assertionها به فایل موجود کافی بود، به جای ساخت فایل جدید C3 و manifest را با همان تصمیم یک‌جا اصلاح کنید. هیچ command شامل فایل ناموجود نباید با حذف بی‌سروصدای آن فایل «پاس» شود. در POSIX همان commandها با `.venv/bin/python` اجرا شوند. Doctor/browser به environment واقعی نیاز دارد؛ skip با علت در ledger، release gate متناسب را باز نگه می‌دارد. برای package/checksum طبق parser و روش [release process](../release-process.md) اقدام شود، نه flag حدسی.

## ۷. PHASE 1 — Foreground Verification Parallel Option

**هدف:** expose کردن `max_parallel_checks` در foreground `verify_changes` با reuse موتور فعلی و رفتار serial کاملاً سازگار.

**ورودی / prerequisite:** دستور اجرای implementation، refresh baseline این سند، عدم overwrite تغییر panel/uv.lock کاربر. **خروجی:** public signature/schema/docs/test که parameter تازه را با default=1 می‌پذیرد. **non-goals:** تغییر scheduler، background protocol، globally bounded execution، workflow DAG، تغییر cancellation engine یا اضافه‌کردن پارامتر به workflow action.

### ۷.۱ Design و interface

**PROPOSED:** به انتهای پارامترهای موجود `verify_changes` یک `max_parallel_checks: Annotated[int, Field(ge=1, le=3)] = 1` اضافه شود؛ جای delivery و positional callهای داخلی را جابه‌جا نکنید. همان مقدار به `verify_changed_repository` منتقل شود. validation ترکیبی `fail_fast/ruff_fix` در موتور فعلی قبل planning اجرا می‌شود؛ منطق موازی جدید یا reuse کل `VerificationRequest` مجاز نیست. اگر برای تست direct helper validation مشترک لازم شد، فقط rule کوچک از همان موتور استخراج شود، با یک authority و حفظ background-specific prohibition جدا؛ اصل این فاز extraction را لازم نمی‌داند.

schema عدد/بازه را public validate می‌کند؛ guard داخلی پیش از plan ترکیب ناسازگار را رد می‌کند. در denial نباید analyzer، plan اثرگذار یا file fix اجرا شود. request با `max_parallel_checks=2` و `fail_fast` حذف‌شده، طبق default true رد می‌شود؛ tool نباید fail_fast را خودکار false کند. خروجی shape و stage order قبلی می‌ماند. optional audit metadata فقط requested/effective concurrency و mode bounded است، بدون repo payload یا diagnostic متن.

Data model: migration ندارد. background request/JSONL version تغییر نمی‌کند. workflow `VerifyChangesInput` و watch default بی‌تغییر می‌مانند؛ parameter فقط روی MCP foreground expose می‌شود.

### ۷.۲ فایل‌ها و Taskها

| نوع | فایل و مسئولیت |
|---|---|
| Modify | `tools/testing/registry.py`، signature/forwarding/docstring/audit کوچک |
| Modify در صورت نیاز rule مشترک | `tools/testing/verification.py`؛ بدون بازنویسی orchestration |
| Test/Modify | `tests/test_parallel_verification.py`, `tests/test_mcp_integration.py`, `tests/test_tool_profiles.py`, `tests/test_background_verification.py` |
| Docs/Modify هنگام اجرا | `docs/tooling.md`, active `CHANGELOG.md` با تاریخ واقعی تغییر |
| Create | فایل production جدید لازم نیست |

- [ ] **P1-T01 — baseline و contract.** public tool schema فعلی و omission-default response را با fixture کوچک ذخیره کنید؛ callerهای `verify_changed_repository` شامل registry، workflow action، watch و worker را با scoped search تطبیق دهید؛ env و dependency versions و serial wall-clock baseline را ثبت کنید. acceptance: مسیرهای لازم شناسایی و target performance قبل edit ثبت شده باشد.
- [ ] **P1-T02 — RED روی boundary.** MCP request با parameter=2 و fail_fast=false را تست کنید که اکنون به علت schema ناقص fail شود؛ tests برای 0/4، fix+parallel، default fail_fast+parallel و عدم runner dispatch اضافه کنید. acceptance: RED در schema/forwarding واقعی، نه fixture خراب.
- [ ] **P1-T03 — signature و forwarding.** parameter را اضافه و عین مقدار را منتقل کنید؛ composition rule existing engine را reuse کنید؛ هیچ mode خودکار تغییر نکند. acceptance: schema و execution هر دو مقدار 2/3 را می‌بینند.
- [ ] **P1-T04 — compatibility.** tests omission و explicit 1 با fail-fast و serial ruff_fix؛ full-mode no-python syntax skipped؛ `checks=[]`؛ failure، unavailable و timeout را اجرا کنید؛ workflow/watch همچنان default1 باشند. acceptance: stages/targets/output semantics برابر baseline، به‌جز timing.
- [ ] **P1-T05 — public catalog.** از MCP client واقعی schema bounds/default را assert و response را دریافت کنید؛ testing-only و jobs-enabled profiles را تفکیک کنید؛ unknown args همچنان reject شوند؛ tool count/names/platform guards عوض نشوند. acceptance: transport validation و body behavior هر دو آزموده‌اند.
- [ ] **P1-T06 — docs و observability.** مثال serial fix و مثال parallel با `fail_fast=false,ruff_fix=false`؛ timeout caveat، foreground durability و oversubscription را توضیح دهید؛ metadata را bounded نگه دارید. acceptance: docs ادعای background success یا global cap نداشته باشد.
- [ ] **P1-T07 — VERIFYING.** C1 و CSTATIC را روی آخرین diff اجرا و diff review کنید؛ هیچ untracked unrelated stage نشود. acceptance: واقعی PASS و exact command/exit/count در ledger.
- [ ] **P1-T08 — G1 و checkpoint.** evidence، کوچک‌ترین commit/PR و checkهای current head را ثبت کنید؛ پس از اجرای flow مجاز، same-SHA gates و main را طبق سیاست repo تأیید کنید؛ pointer را P2-T01 کنید. acceptance: G1 به‌صورت مستند GO، نه صرف نوشته‌شدن کد.

Dependency درون فاز به‌ترتیب T01..T08 است. TDD: T02 RED، T03 GREEN، T04/T05 regressions، T06 docs و T07 آخرین verification. مرز commit پیشنهادی: `feat(testing): expose foreground verification concurrency` برای signature+behavior+tests، سپس `docs(testing): explain verification concurrency limits` اگر docs مستقل و کامل باشد؛ test-only RED commit روی main merge نشود.

### ۷.۳ Test Matrix، failure و Gate

| سناریو | انتظار |
|---|---|
| omission / explicit1 / checks=[] | serial قبلی / بدون اجرای check در empty checks |
| 2 یا 3 با fail_fast=false و fix=false | overlap واقعی bounded، stage order ثابت |
| 0 / 4 / type نامعتبر / ناسازگاری mode | خطای typed؛ runner counter صفر |
| syntax failure / no-python full | به‌ترتیب analyzer dispatch صفر / full analyzers مجاز |
| analyzer failure یا unavailable | false success ممنوع؛ required_unknown طبق موتور |
| timeout و transport disconnect | cleanup طبق owned runner؛ cancel thread فرض نشود؛ foreground durable job نمی‌شود |
| serial fix | همچنان مجاز و پس از آن verification نتیجه درست بدهد |

race اصلی: concurrent callback/output و چند invocation مستقل؛ engine موجود را ناآگاهانه با pool دیگر wrap نکنید. total deadline شامل زمان ساخت plan نیست؛ همین behavior در این فاز حفظ و مستند شود. **G1 GO:** تمام Taskها و C1/CSTATIC پاس، schema و denial-before-dispatch و compatibility و docs ثابت شده‌اند. **NO-GO:** تغییر default، mutation موازی، output mismatch یا check pending. prerequisite P2: G1 و baseline schema جدید قابل‌اتکا.

## ۸. PHASE 2 — Bounded Parallel `collect_diagnostics`

**هدف:** Ruff/mypy/pytest مستقل با cap محدود هم‌زمان شوند، با stage order و merge semantics قبلی. **prerequisite:** G1. **non-goals:** syntax stage جدید، unify کل verify/diagnostics، parser جدید diagnostic، شکستن timeout قدیمی، sandbox repository tests یا global admission جدید در این فاز.

### ۸.۱ Design و interface دقیق

**PROPOSED:** در nested `collect_diagnostics`، پارامترهای additive `max_parallel_checks` با bounds **1..3/default1** و `total_timeout_sec: float | None` با bounds مثبت و حداکثر **10800s**، default=None. عدد 10800 پیشنهادی از سه backend و سقف فعلی 3600 per-backend ناشی می‌شود؛ مقدار config موجود نیست. در omission total deadline اضافه نمی‌شود؛ `timeout_sec` همان per-backend قبلی است. اگر total تعیین شد، deadline monotonic از ورود orchestration پس از `_cwd` ساخته و انتظار queue/permit را هم شامل شود؛ هر backend در شروع timeout `min(timeout_sec, remaining_total)` می‌گیرد. backend dispatch‌نشده پس از expiry stage `timed_out=true, ok=false` می‌گیرد. Cleanup grace جدا ثبت و مستند شود؛ total deadline تضمین پایان process در همان millisecond نیست.

`selected = backends or [...]` و ترتیب request حفظ؛ حتی duplicate backend با index جدا schedule شود، تا behavior قدیمی silently حذف نشود. pool cap، `min(max_parallel_checks,len(selected))`؛ result slots با index request، جمع‌آوری مستقل از completion order. همان `run_ruff_summary` با fix default false، `run_mypy_summary`، `run_pytest_summary`؛ هیچ runner دیگری و هیچ shell اضافه نشود. `merge_diagnostics` همان مرز نهایی normalize/dedup/sort/cap است. اجرا در registry بماند؛ module `diagnostics.py` را مجبور به import runners نکنید چون runners همین module را مصرف می‌کند و circular import می‌سازد.

Exception عادی هر backend داخل worker به یک stage unavailable با diagnostics خالی تبدیل شود؛ siblingها ادامه یابند. missing executable/module و invalid backend باید به‌ترتیب unavailable/validation denial باشند. خطا sanitization با native policy؛ raw exception که path/secret user repo دارد بی‌محابا log نشود. `BaseException`/KeyboardInterrupt/caller cancellation را به success یا unavailable عادی تبدیل نکنید؛ cleanup مالک process انجام و cancellation propagate شود. `Future.cancel` فقط pending task را حذف می‌کند؛ running thread تا timeout/owned termination می‌ماند. تا فاز ۶ cooperative process hook مشترک آماده نیست، این limitation صریح باشد.

`required_unknown` برای unavailable/timed_out/dispatch‌نشده به علت deadline، true؛ failure معلوم analyzer `ok=false` ولی به‌خودی‌خود unknown نیست. stageها fields قدیمی را نگه دارند. اگر reason یا effective concurrency افزوده شد additive/bounded و schema documented باشد. upstream truncation را می‌توان در فیلد additive جدا مانند **PROPOSED** `backend_output_truncated` گزارش داد؛ `truncated` فعلی همچنان فقط merged-cap است. افزودن این فیلد بدون test/versioned docs مجاز نیست و برای اصل فاز لازم نیست.

### ۸.۲ فایل‌ها و Taskها

| نوع | فایل و مسئولیت |
|---|---|
| Modify | `tools/testing/registry.py`، bounded dispatch و per-backend exception/deadline |
| Preserve / Test | `tools/testing/diagnostics.py`, `tools/testing/runners.py`, `core/executor.py`؛ تغییر فقط با failure مرتبط و boundary justification |
| Create پیشنهادی | `tests/test_parallel_diagnostics.py` |
| Test/Modify | `tests/test_diagnostics.py`, `tests/test_mcp_integration.py`, tests موجود testing runners مطابق scope |
| Docs/Modify | `docs/tooling.md`, `CHANGELOG.md` |

- [ ] **P2-T01 — freeze legacy.** `None`, `[]`, duplicate و request order را baseline کنید؛ stage shape و per-backend timeout، merged cap و source-sensitive dedup را assert کنید؛ total_timeout پیشنهادی را به ADR-P02 وصل کنید. acceptance: serial contract executable و target measurement ثبت شده است.
- [ ] **P2-T02 — RED overlap/order.** runnerهای fake با barrier و active counter بسازید؛ cap2 با سه backend و reverse completion، non-overlap در default1 و MCP schema parameter را تست کنید. acceptance: RED ناشی از serial implementation باشد.
- [ ] **P2-T03 — dispatch محدود.** local backend worker و indexed result slots در registry؛ pool فقط برای cap>1، serial مسیر یکسان را برای exception handling مصرف کند؛ sorted merge را دست نزنید. acceptance: peak<=requested و هیچ backend گم نشود.
- [ ] **P2-T04 — deadline/exception.** queue time از total کم شود؛ expired pending dispatch صفر؛ runner crash به unavailable و sibling results حفظ؛ cancellation منتظر cleanup محدود/صادقانه بماند. acceptance: failure/timeouts نتیجه ناقص را success نکنند و context manager منتظر task نامحدود نماند.
- [ ] **P2-T05 — aggregation regression.** duplicate diagnostics، source متفاوت، cap=1 و max، no diagnostics با exit nonzero، backend missing، mixed failure+timeout و reversed order را تست کنید؛ تغییر input در طول pytest outcome freshness ادعا نکند. acceptance: normalized deterministic output و required_unknown صحیح.
- [ ] **P2-T06 — schema/docs/metrics.** omission=serial، total=None=per-backend، opt-in total deadline و max3 را public document کنید؛ runtime/tool timing موجود را برای queue/run/cleanup تفکیک کنید، high-cardinality label اضافه نکنید. acceptance: schema و مثال با executable test تطبیق دارند.
- [ ] **P2-T07 — measured verification.** C2/CSTATIC؛ fake controlled subprocessها و سپس fixture repo واقعی با Ruff/mypy/pytest مستقل؛ wall-clock و peak را با serial مقایسه و artifact reference ثبت کنید. acceptance: بهبود بدون cap breach/leak و بدون نتیجه‌سازی از source defaults.
- [ ] **P2-T08 — G2.** diff، commands، platform gaps و commit/PR را ثبت؛ gate same-head را طبق policy بگذرانید؛ pointer=P3-T01. acceptance: G2 GO و signature contract frozen.

TDD: T02 RED، T03 GREEN، T04/T05 failure regressions، T07 بعد آخرین edit. commitها: `feat(testing): add bounded diagnostics concurrency` همراه tests؛ `fix(testing): bound diagnostic deadlines and partial failures` فقط اگر واحد جدا قابل‌review باشد؛ docs کوچک همراه behavior یا commit مستقل. commitی که failure semantics آن نیمه‌کاره است checkpoint VERIFIED نیست.

### ۸.۳ Matrix و G2

| محور | مقادیر / assertion |
|---|---|
| cap/backend count | cap1/2/3، یک/دو/سه backend، empty/default/duplicate؛ peak درست |
| ordering | تمام permutationهای پایان سه backend؛ stages مطابق request، diagnostics مطابق merge |
| result | pass، diagnostic failure، no-output failure، missing module، raised exception، timeout؛ unknown صحیح |
| deadlines | omitted total، total کمتر از یک stage، expiry در queue، stage شروع‌شده نزدیک deadline، cleanup failure |
| truncation | per-backend cap، merged cap، exact boundary، duplicate reducing count؛ بدون false completeness |
| cancellation | pending vs running؛ process child/grandchild در timeout از بین برود، نتیجه partial موفقیت کل نشود |
| public | schema bounds/default، invalid args و no-dispatch، delivery shape قبلی |

Raceهای مهم: shared list append ترتیب‌وابسته، چند backend با cache مشترک، duplicate pytest روی یک repo، submit همه futures و pending task پس از timeout. pytest artifact/cache run-specific در حد لازم reuse شود؛ backend output path مشترک نسازید. **G2 GO:** C2/CSTATIC و measurement/cleanup/compatibility پاس؛ **NO-GO:** result nondeterministic، hidden unknown، per-backend timeout silently تبدیل به total یا no-bound running task. prerequisite P3: G2 و جلوگیری از budget confusion در docs.

## ۹. PHASE 3 — Workflow DAG Foundation

**هدف:** graph dependency explicit، validated و durable با projection قابل‌فهم؛ بدون فعال‌کردن parallel workflow execution. **prerequisite:** G2. **non-goals:** scheduler موازی production، شرط‌های دلخواه، branch scripting، output interpolation جدید، compensating actions یا تغییر builtins به DAG.

### ۹.۱ Proposed مدل، identity و semantics

تمام تغییرهای زیر **PROPOSED** هستند و در `WorkflowDefinition/StepDefinition` فعلی وجود ندارند:

```python
# fields به انتهای dataclassها افزوده می‌شوند؛ positional fields قدیمی حفظ شوند
StepDefinition.step_id: str | None = None
StepDefinition.depends_on: tuple[str, ...] = ()
WorkflowDefinition.execution_mode: Literal["sequential", "dag"] = "sequential"
```

در `execution_mode=sequential` dependency semantics همان implicit chain `index0 -> index1 -> ...` است؛ فیلدهای DAG non-default در این mode رد شوند تا input نیمه‌صریح به رفتار surprising منجر نشود. نام step نمایش است و تکرار آن در legacy مجاز می‌ماند. در DAG، `step_id` الزامی، محدود به **1..100 ASCII** از `[A-Za-z0-9_-]`، case-sensitive و در workflow یکتا؛ `depends_on` فقط ID است، نه display name یا index. empty tuple در DAG یعنی root صریح. هر step باید ID داشته باشد حتی اگر هیچ edge ندارد. dependency تکراری داخل یک step invalid است، نه silently dedup؛ self، unknown، duplicate identity و cycle invalid‌اند. حداکثر steps همان **100** public فعلی؛ حداکثر dependency هر step **99** و edge کل حداکثر `N*(N-1)/2` برای graph معتبر. validation پیش از persistence و پیش از lease/dispatch انجام شود.

topological sort با Kahn و tie-break اصلی **original step_index**؛ ID tie-break فقط دفاع اضافه است چون index یکتا است. store order/index هرگز به ترتیب topological بازنویسی نمی‌شود؛ display/public/operation IDs پایدار می‌مانند. cycle detection باید path یا IDهای bounded قابل‌رفع بدهد؛ stack recursion نامحدود یا import dependency گراف جدید لازم نیست. graph immutable است؛ تغییر graph پس از create با idempotency key قدیم conflict می‌دهد.

Ready/blocked طراحی مشتق‌شده دارد: StepState جدید به enum اضافه نمی‌شود. operation/step `created` اگر همه parentها succeeded/acknowledged باشند **ready**؛ اگر parent nonterminal دارد **waiting_dependencies**؛ parent failed/unresolvable/cancelled یا unresolved uncertainty یعنی **blocked_dependencies** با bounded reason و ID parentها. phase4 active failure policy می‌تواند آماده‌ها را نیز stop کند؛ readiness به‌تنهایی اجازه dispatch نیست. cancellation سراسری و resource/admission نیز gate جدا هستند. این readiness در status/plan fieldهای additive نشان داده شود، ولی authority از operation stateها و edgeهای persisted محاسبه می‌شود؛ counter مستقل ready در DB حقیقت دوم نسازد.

### ۹.۲ persisted schema / migration پیشنهادی

tip واقعی اکنون 6 است؛ migration بعدی در این baseline **پیشنهاد v7** است. در زمان اجرا tip را دوباره بخوانید؛ اگر تغییر کرده است شماره جدید پیوسته انتخاب و همه references/fixtures این سند را به‌روز کنید.

| بخش پیشنهادی | قرارداد |
|---|---|
| workflow metadata | mode با default sequential؛ graph contract version جدا و immutable |
| `workflow_steps.step_id` | برای legacy nullable یا stable internal `legacy:<index>` در projection؛ برای DAG unique در workflow و immutable؛ display name بدون uniqueness جدید |
| `workflow_step_dependencies` | جدول جدید پیشنهادی با workflow_id، step_index و dependency_step_index؛ PK سه‌تایی، FK هر دو endpoint به همان workflow_steps و index reverse lookup؛ cross-workflow edge ممنوع |
| exact graph serialization | DAG fields در exact execution definition و public redacted definition؛ graph fingerprint با version؛ metadata tamper mismatch fail closed |
| legacy bytes/IDs | JSON/hash/operation_id موجود دست‌نخورده؛ serializer سازگاری برای sequential fields پیش‌فرض را omit کند؛ `_operation_payload` قدیمی همان bytes/hash مؤثر را بسازد |

اصل migration: edgeهای legacy chain از index مشتق/پُر شوند، بدون بازنویسی payload دقیق تاریخی. برای create/re-submit legacy، canonical representation باید با baseline قبل schema تغییر برابر بماند؛ صرفاً اضافه‌کردن `None/[]/sequential` به `asdict` کافی نیست. برای DAG جدید، graph semantics حتماً در definition fingerprint/idempotency لحاظ شود؛ دو graph متفاوت با action یکسان deduplicate نشوند. operation hash جدید فقط در DAG جدید graph identity را bind می‌کند؛ operationهای legacy دوباره materialize نشوند. graph read در `execution_definition` از schema version پشتیبانی‌شده انجام شود، unknown version fail closed.

Migration با `exclusive_file_lock` موجود، `BEGIN IMMEDIATE` کوتاه، restart/idempotent و foreign_keys فعال؛ checksum و rows پیش/پس با disposable fixture سنجیده شوند. v0/v1/...v6 fixtures و legacy unrecoverable behavior حفظ شود. دست‌کاری exact payload صرفاً برای پرکردن field جدید ممنوع؛ metadata جدا canonical graph را bind می‌کند. old binary DB v7 را نمی‌خواند؛ rolling mixed-version writer مجاز نیست و در Migration Plan پوشش داده می‌شود.

تا G4/G5/G6، `workflow_plan/start/status` می‌توانند graph معتبر را validate/persist/project کنند، ولی `WorkflowExecutor.execute` روی DAG با خطای safe **PROPOSED** `workflow_dag_execution_unavailable` پیش از تغییر running/dispatch رد شود. dry-run نباید execution به نظر برسد. DAG sequential prototype فقط در fixture تست مجاز است، نه fallback بی‌خبر به حلقه قدیمی. recovery بازکردن DB نباید DAG را با current_step خطی پردازش کند؛ graph pending با started صفر حفظ شود و هر DAG in-flight نامعتبر به بررسی safe/uncertain برود.

### ۹.۳ فایل‌ها و Taskها

| نوع | فایل و مسئولیت |
|---|---|
| Modify | `core/workflow_models.py`, `core/workflow_actions.py:validate_workflow_definition`, `core/workflow_store.py`, `tools/workflows/registry.py` |
| Modify guard | `core/workflow_executor.py`, `core/workflow_reconciliation.py` فقط برای جلوگیری از استفاده منطق خطی روی DAG ناقص |
| Preserve/Test | `core/workflows.py` exports، `tools/workflows/builtins.py`، `scripts/workflow_worker.py` |
| Create پیشنهادی | `tests/test_workflow_dag.py`, `tests/test_workflow_models_contracts.py`؛ helper topological کوچک می‌تواند در models/validation موجود بماند |
| Test/Modify | migrations/store/persistence/contracts/transitions/builtin/tools tests موجود |
| Docs/Modify | `docs/architecture.md`, `docs/tooling.md`, `CHANGELOG.md` |

- [ ] **P3-T01 — identity/serialization ADR.** ADR-P03 را با نمونه legacy duplicate names، DAG roots و exact JSON before/after freeze کنید؛ هر `StepDefinition(...)` positional و reconstruct site، مخصوصاً executor `execution_step` را جست‌وجو کنید. acceptance: append-fields contract و identity/hashes مشخص است.
- [ ] **P3-T02 — RED graph tests.** self/unknown/duplicate/cycle، disconnected roots، diamond، forward reference، empty graph و graph با 100 step را تست کنید؛ property هر topological edge parent-before-child؛ invalid graph DB row/dispatch صفر. acceptance: counterexampleها روی سورس قدیم RED معنادارند.
- [ ] **P3-T03 — مدل/validation/API.** dataclass و Pydantic fields و mode؛ validation direct create و MCP plan/start؛ deterministic topological projection و error bounded. acceptance: public/internal boundary قواعد یکسان دارند، legacy نام تکراری کار می‌کند.
- [ ] **P3-T04 — migration durable.** version tip را تأیید؛ additive graph tables/metadata و serializer سازگار؛ old bytes/hash/idempotency و materialize IDها را حفظ کنید؛ concurrent open/crash/reopen را تست کنید. acceptance: checksum payloadهای قدیمی ثابت و DAG reload همان edgeها را دارد.
- [ ] **P3-T05 — readiness/status.** parent success/acknowledged/failed/uncertain/cancel را به ready/blocked reasons map کنید؛ original index order و public current_step semantics را مستند کنید؛ countها از operationهای canonical مشتق شوند. acceptance: status/plan بدون side effect و projection deterministic.
- [ ] **P3-T06 — compatibility guard.** legacy executor و builtins را regression کنید؛ DAG execute gate قبل dispatch؛ `resolve_uncertain_operation`/recover خطی روی graph ممنوع؛ unknown schema fail closed؛ old irreversible legacy rows همچنان unrecoverable. acceptance: foundation به‌تنهایی parallel/DAG execution را accidentally فعال نمی‌کند.
- [ ] **P3-T07 — verification.** C3/CSTATIC و تمام workflow tests relevant، upgrade/reopen data-preservation و idempotency serialization؛ docs paths/schema examples. acceptance: final diff/tests واقعی پاس و migration recovery شاهد دارد.
- [ ] **P3-T08 — G3.** audit مجدد models/store/routing/status، migration evidence و commit/PR ثبت؛ activation closed در handoff؛ pointer=P4-T01. acceptance: G3 GO همراه graph immutable و legacy unaffected.

TDD: T02 RED؛ T03 graph GREEN؛ T04 fixture RED/GREEN جدا؛ T05/T06 runtime guards؛ T07 final. commit boundaries: `feat(workflows): validate explicit dependency graphs`، `feat(workflows): persist versioned dependency metadata`، `test(workflows): preserve legacy workflow identities` فقط همراه یا پس از fix کامل؛ هر migration atomic و قابل‌upgrade باشد.

### ۹.۴ Matrix، failure و G3

| محور | assertion |
|---|---|
| invalid graph | duplicate ID/dependency، self، unknown، 2-cycle و cycle بزرگ؛ صفر DB writes/effects |
| ordering | shuffled input با forward edges، roots متعدد، diamond؛ stable index tie و parent-before-child |
| legacy | names تکراری، omitted fields، positional construction، re-submit same key؛ hash و ID ثابت |
| migration | schema0..6، terminal/nonterminal، missing exact payload، concurrent open، interrupted migration و reopen |
| tamper | edge table در برابر exact hash، unsupported graph version، duplicate DB row؛ fail closed |
| readiness | all parents success، one waiting/failed/uncertain/cancelled/acknowledged؛ reason صحیح |
| guard | DAG start/status مجاز، execute denied تا activation؛ no process launch در plan/list/health |

timeout/cancel این فاز تغییر نمی‌کند؛ graph validation باید bounded به 100 nodes و edge سقف باشد و قبل execution رخ دهد. race اصلی migration و idempotent create با دو تعریف graph متفاوت است؛ winner فقط یک definition است. **G3 GO:** legacy migration/identity و graph validation/projection/closed gate ثابت و C3/CSTATIC پاس. **NO-GO:** rehash قدیمی، DAG entrypoint بدون gate، current_step authority در graph، unknown dependency پذیرفته‌شده. prerequisite P4: G3 و schema/definition قرارداد frozen.

## ۱۰. PHASE 4 — Parallel Workflow Scheduler

**هدف:** اجرای ready stepهای مستقل با cap، یک owner workflow، attempt fencing و aggregate درست؛ پایه recovery چندشاخه همین‌جا الزامی است. **prerequisite:** G3. **non-goals:** فعال‌سازی عمومی بدون resource claims و hardening، external workflow actions، multi-host consensus، automatic mutation retry، افزایش implicit concurrency قدیمی.

### ۱۰.۱ scheduling، ownership و interfaces پیشنهادی

**PROPOSED:** `WorkflowDefinition.max_parallel_steps: int = 1` و `WorkflowDefinition.total_timeout_sec: float | None = None` به انتهای dataclass و `WorkflowInput` اضافه شوند؛ max_parallel_steps bounds 1..16؛ total_timeout_sec در DAG پس از normalization default7200 و bounds مثبت تا86400 دارد و در legacy omission هیچ total deadline تازه اعمال نمی‌کند. effective cap برابر min(request, configured per-workflow cap, available global step permits). سقف config هنگام load validate/clamp و در health expose شود. sequential mode همیشه effective1 می‌ماند؛ request بالاتر در sequential با validation error رد شود تا هیچ درخواست قدیمی silently تغییر نکند. `workflow_execute` command shape موجود و `expected_version` باقی بماند؛ cap/deadline از definition immutable خوانده شوند، نه override mutable در هر resume. fieldهای default در serializer legacy omit شوند؛ DAG omitted/explicit default باید قبل fingerprint به representation واحد normalize شوند.

یک coordinator sync در `WorkflowExecutor.execute` با bounded `ThreadPoolExecutor` و `wait(..., FIRST_COMPLETED)`، بدون نیاز به API Python3.11. تعداد submitted/in-flight futures از effective cap بالاتر نرود؛ submit همه graph به executor queue ممنوع است. candidateها از snapshot canonical operation states و dependencies محاسبه، با `step_index` sort و اولین eligible admit شود. resource conflict در P5 readiness را پالایش می‌کند؛ فعلاً activation production بسته و فقط fixture safe actions/mocked mutations این مسیر را exercise می‌کنند.

هر `execute` invocation identity یکتا دارد، حتی اگر caller همان `owner_id` را دوباره بدهد. **PROPOSED** `acquire_lease(..., allow_reentry=False)` برای executor، با حفظ behavior reentry قبلی در callerهای مستقیم که آن را صریح می‌خواهند؛ یا فیلد execution identity معادل در همان lease. انتخاب نهایی همین‌جا به ADR-P04 bind شود؛ معیار قطعی این است که دو execute هم‌owner نیز نتوانند یک workflow را هم‌زمان dispatch کنند. worker فعلی owner ثابت process دارد؛ باید برای هر invocation suffix یکتا بسازد. MCP `_mcp_owner_id` از قبل unique است.

heartbeat به جای یک thread برای هر branch، یک thread برای کل owner execution است و از پیش از admission تا همه branchهای in-flight و finalize فعال می‌ماند. heartbeat error signal مشترک stop-admission ایجاد می‌کند؛ branch worker قبل barrier و قبل checkpoint lease/attempt را recheck می‌کند. owner قدیمی فقط می‌تواند late result را برای recovery محفوظ کند، نه authoritative aggregate جدید را overwrite کند.

### ۱۰.۲ تغییر durable attempt و atomic boundary

**PROPOSED** migration پس از graph foundation، روی baseline فعلی به‌ترتیب **v8** در نظر گرفته می‌شود؛ شماره نهایی وابسته به tip زمان اجراست. در `workflow_operations` فیلدهای attempt identity/phase و در همان store جدول admission active stepها اضافه شوند. نام‌های زیر پیشنهادی‌اند:

| بخش | قرارداد دقیق |
|---|---|
| `attempt_token` / `execution_id` | برای هر attempt تازه یکتا؛ به operation و workflow lease generation bind؛ late callback با token قبلی رد |
| `dispatch_phase` | `reserved`, `prepared`, `may_dispatch`, `settled`؛ قبل may_dispatch نبود effect قابل‌اثبات‌تر است؛ phase گذار monotonic و durable |
| `workflow_execution_admissions` | active operation، attempt token، lease token، owner/process identity، weight/category و timestamp؛ همان authority سقف active stepها، نه cached slot counter |
| `claim_ready_operation` | نام method **PROPOSED**: در یک transaction dependency/cancel/lease/state/version/cap را check، token بدهد و reservation persist کند؛ runner dispatch بعد commit |
| `complete_operation_attempt` | نام method **PROPOSED**: expected operation version+attempt token+lease را check و operation/step/event را atomic checkpoint کند؛ projection aggregate با coordinator recompute |
| waiting/retry | زمان next eligible retry به metadata همان operation bind شود؛ thread worker در backoff slot process-heavy نگیرد؛ retry attempt count فقط در شروع attempt واقعی increment شود |

atomicity قدیمی operation/step transaction جدا برای DAG کافی نیست؛ branch checkpoint جدید باید هر دو و event را atomic کند. legacy مسیر می‌تواند از همین helper با semantics قبلی استفاده کند، فقط پس از regressions. workflow aggregate version را workerها مستقل read/modify/write نکنند؛ coordinator در transaction latest facts را می‌خواند و با expected version از parent/cancel conflict جلوگیری می‌کند. busy/CAS failure به معنای اجرای مجدد action نیست؛ فقط checkpoint/re-read idempotent با token، یا توقف و recovery.

`current_step` در DAG کمترین index ناتمام است؛ status additive شامل ready indices، active indices، blocked dependency reasons، completed_count و requested/effective cap است. list/order عمومی همچنان original index. operation succeeded/acknowledged از active set حذف و هرگز دوباره submit نمی‌شود. same-key create/execute با CAS و lease و atomic claim باید marker exactly one در fixture بدهد.

### ۱۰.۳ branch failure، joins و deterministic semantics

سیاست اولیه **PROPOSED** فقط `all_success` و **stop-admission-on-failure** است؛ policy input آزاد، continue-on-error و OR-join اضافه نشود. child زمانی ready است که همه parentها succeeded یا operator-acknowledged-as-completed باشند. failure parent، descendant را blocked نگه می‌دارد. نخستین failure/uncertainty مشاهده‌شده جلوی dispatch جدید همه branchها را می‌گیرد؛ branchهایی که قبلاً started شده‌اند طبق cancel mode و cleanup drain می‌شوند و success آن‌ها حفظ می‌شود. failure یک branch حق rollback موفقیت دیگری ایجاد نمی‌کند.

workflow completed فقط وقتی همه required operations موفق/acknowledged باشند و هیچ in-flight/unresolved باقی نباشد. precedence پس از drain: **unresolved uncertainty -> uncertain؛ known failure/unresolvable -> failed؛ explicit cancel بدون uncertainty/failure -> cancelled؛ همه success بدون cancel -> completed؛ safe unfinished پس از interruption -> paused**. در حین cancel/drain `cancelling` یا running+stop-admission reason باقی می‌ماند؛ aggregate terminal نباید active effect پنهان کند. خطای primary در status با کمترین original index میان failureها انتخاب و همه خطاها bounded در operationها حفظ شوند؛ first-finished exception تنها علت نهایی نباشد.

determinism یعنی برای snapshot readiness/claims یکسان، انتخاب candidate و projection یکسان؛ ترتیب واقعی completion و تعداد branchهایی که پیش از مشاهده failure started شده‌اند می‌تواند با timing متفاوت باشد. طرح این تفاوت را پنهان نمی‌کند. برای evidence canonical، event ID ترتیب durable observation است؛ timestampها برای dependency authority استفاده نمی‌شوند.

### ۱۰.۴ crash/restart حداقل لازم در این فاز

`recover_interrupted` و `resolve_uncertain_operation` برای DAG باید **همه operations** را scan کنند، نه current_step. operation succeeded قبل از crash حفظ، step checkpoint ناقص ترمیم؛ created با attempt صفر یا reserved/prepared با اثبات عدم عبور barrier و عدم child زنده safe unfinished؛ may_dispatch/running/waiting بدون نتیجه معتبر uncertain؛ failed/cancelled حفظ. aggregate از کل graph derive شود. callback دیررس owner قدیمی نباید status را عوض کند. تا recovery of every branch و reconciliation aggregate درست نشده حتی scheduler fixture نباید «restart-safe» نامیده شود.

### ۱۰.۵ فایل‌ها و Taskها

| نوع | فایل و مسئولیت |
|---|---|
| Modify | `core/workflow_executor.py`, `core/workflow_store.py`, `core/workflow_models.py`, `core/workflow_reconciliation.py`, `core/config.py`, `tools/workflows/registry.py`, `scripts/workflow_worker.py` |
| Modify محدود | `core/workflow_actions.py:VerifyChangesInput/_verify_changes` فقط برای explicit inner cap در DAG؛ until P5 inner cap>1 deny؛ `core/workflows.py` export در صورت نیاز واقعی |
| Create پیشنهادی | `tests/test_parallel_workflow_scheduler.py`؛ production module تازه پیش‌فرض لازم نیست |
| Test/Modify | concurrency_gates، heartbeat، leases، crash_recovery، worker، routing، cancellation و migrations tests موجود |
| Docs/Modify | architecture/tooling/CHANGELOG و این ledger هنگام اجرا |

- [ ] **P4-T01 — coordinator/attempt ADR.** one-owner، no-reentry، cap/config defaults، stop-admission failure policy و aggregate precedence را freeze کنید؛ تمام execution entrypointها و callerهای `checkpoint_*`/`recover_interrupted`/reconcile را inventory کنید. acceptance: هیچ مسیر DAG از helper خطی عبور نمی‌کند.
- [ ] **P4-T02 — RED race/cap.** diamond با دو branch barrier، cap1/2، دو executor با owner متفاوت و یکسان، cancel racing claim، heartbeat failure قبل dispatch و concurrent branch completion را تست کنید؛ counter مستقل effect. acceptance: RED معنادار و bounded test timeout.
- [ ] **P4-T03 — durable attempt/admission.** atomic ready claim و attempt-aware checkpoint؛ operation+step+event transaction؛ global step admission rows و migration؛ no side effect in DB lock. acceptance: duplicate claim یک winner و late result/no current token رد می‌شود.
- [ ] **P4-T04 — scheduler.** bounded in-flight set، deterministic candidate order، یک heartbeat owner، worker result channel محدود و cleanup finally؛ cap از immutable definition/config. acceptance: peak<=cap و ready child فقط پس از parent success dispatch می‌شود.
- [ ] **P4-T05 — branch aggregate/join.** all_success join، failure stop، حفظ completed و blocked reasons، DAG current_step projection و primary error stable؛ retry queue بدون busy loop. acceptance: failure یک شاخه completion دیگری را پاک نمی‌کند و join زودهنگام ندارد.
- [ ] **P4-T06 — crash-safe foundation.** همه branchها در restart scan، safe-created vs uncertain may_dispatch، atomic reconciliation aggregation و no replay succeeded؛ simulated و real subprocess kill حداقل قبل/بعد checkpoint. acceptance: no active branch از recovery حذف نمی‌شود؛ activation production همچنان بسته است.
- [ ] **P4-T07 — integration/observability.** MCP/direct/worker همان execution path؛ native route planner guard؛ configured/effective counts و lease health؛ C4/CSTATIC و full relevant workflow suite. acceptance: no duplicate process tree و هیچ API ناقص DAG را فعال نمی‌کند.
- [ ] **P4-T08 — G4.** source/evidence دوباره audit، migration و pending riskهای منابع را ثبت، commit/PR checkpoint و pointer=P5-T01. acceptance: G4 GO فقط برای scheduler gated، نه product enablement.

TDD: T02 RED؛ T03 transaction GREEN؛ T04 scheduling؛ T05/T06 crash/property regressions؛ T07 last checks. commits پیشنهادی: `feat(workflows): fence durable step attempts`، `feat(workflows): schedule bounded dependency-ready steps`، `fix(workflows): recover every in-flight graph branch`. config/docs/tests در commit مرتبط و activation=false در تمام آن‌ها. test-only bypass gate فقط fixture scope و بدون env production bypass.

### ۱۰.۶ Matrix، timeouts و G4

| سناریو | assertion |
|---|---|
| chain / diamond / disconnected roots | cap و edge invariants؛ join فقط پس از همه parentها |
| cap1 و omitted fields | legacy serial event sequence و public response سازگار |
| دو executor / same-owner / CAS cancel | یک lease owner و یک admitted attempt/operation؛ denied effect counter صفر |
| دو completion هم‌زمان | هر دو نتیجه حفظ؛ workflow version conflict منجر replay نمی‌شود |
| branch A fails، B success/running | new dispatch متوقف؛ B success محفوظ؛ descendants blocked |
| heartbeat busy/expired/replaced | no new dispatch و no late checkpoint؛ owner جدید claim قدیمی را release نکند |
| crash قبل barrier / بعد barrier / بعد success record | safe unfinished / uncertain / success conserved |
| queue/cap timeout | timeout step از admitted execution start؛ admission wait deadline جدا؛ بدون started شدن attempt phantom |

timeout step موجود توسط action enforce می‌شود، coordinator thread را kill نمی‌کند. **PROPOSED** admission wait deadline برای DAG در definition به اندازه sum boundهای step یا explicit workflow total deadline bounded تعریف شود؛ default omission برای legacy هیچ total deadline تازه اعمال نمی‌کند. برای DAG `total_timeout_sec` پیشنهادی default **7200** و bounds مثبت تا **86400** است؛ freeze در T01 و schema/serialization همراه cap. total deadline شامل queue اجرای branch، retries و cleanup initiation است؛ cleanup زمان جدا دارد. زمان گذشته persisted با wall-clock deadline و مدت محلی monotonic ترکیب و clock skew fail closed بررسی شود. **G4 GO:** cap/claim/heartbeat/multi-branch recovery و no-reentry پاس، activation closed؛ **NO-GO:** terminal با in-flight بی‌صاحب، current_step recovery، losing completion، missed lease renewal. prerequisite P5: G4 و admission rows قابل‌گسترش، نه scheduler دوم.

## ۱۱. PHASE 5 — Resource-Aware Parallel Execution

**هدف:** مستقل‌بودن DAG با مستقل‌بودن منابع تطبیق داده شود؛ conflictها بین thread/process/workflow serialize و منابع مستقل parallel بمانند. **prerequisite:** G4. **non-goals:** قفل‌کردن editor خارجی، OS sandbox، distributed cross-host locks، read/write عمومی برای تمام برنامه‌های سیستم، جایگزینی SHA guard یا تمام `ResourceLocks` با DB.

### ۱۱.۱ تصمیم اصلی: persisted admission claims لازم است

**PROPOSED و لازم برای این scope:** چون workflow durable و multi-worker است، process-local `ResourceLocks` به‌تنهایی کافی نیست. claimها باید در **همان `workflows.sqlite3` و تحت `WorkflowStore`** durable شوند؛ authority جدید مستقل ساخته نشود. table پیشنهادی `workflow_resource_claims` فرزند admission/attempt با key/mode/scope و owner generation است. registry فعلی `ResourceLocks` برای critical section کوتاه داخل process حفظ می‌شود؛ claim workflow مرحله coordination بالاتر را می‌سازد. به جای نگه‌داشتن transaction DB دور action، claim row در transaction کوتاه reserve و پس از commit action اجرا می‌شود.

اگر همه writerهای native هم‌زمان از claim boundary عبور نکنند، فقط workflow-vs-workflow safety به دست می‌آید. این plan برای managed ComputerPilot native writers نیز conflict prevention می‌خواهد؛ بنابراین integration مشترک در boundary resourceها الزامی است. direct native mutation و workflow mutation باید به همان claim authority متصل شوند؛ nested native helper با **opaque internal attempt capability** می‌تواند claim parent را reuse کند. client/provider حق ارسال token اعتمادساز یا کم‌کردن resource set را ندارد. اگر integration یک writer هنوز اثبات نشده، آن action برای parallel execution **ineligible** است یا coarse exclusive claim می‌گیرد؛ ادعای host-wide safety ممنوع.

Native runtime بدون workflow/zero-extension نباید هنگام import یا list_tools DB جدیدی باز کند. adapter admission فقط هنگام execution و در managed resource scope لازم initialize شود؛ DB خراب در مسیر mutating که claim لازم دارد fail closed می‌کند. صحت independence native profiles/schema قبل و بعد با regression ثابت شود. claimها همان workflow DB را گسترش می‌دهند، journal truth جدا نیستند.

محدوده تضمین دقیق، **participating managed boundaries** است. terminal/shell، background process و durable command عمومی هم ممکن است بیرون footprint اعلام‌شده بنویسند؛ صرف «native» بودن آن‌ها coordination ایجاد نمی‌کند. برای claim safety سراسری در یک managed deployment، این entrypointها باید coarse exclusive admission را پیش از process spawn بگیرند و parent execution capability را به child attach کنند؛ در غیر این صورت مانند external writer غیرهماهنگ محسوب و guarantee به workflowها/مرزهای integrateشده محدود است. activation هر action با scope guarantee بزرگ‌تر از coverage واقعی NO-GO است. تغییر default admission legacy commandها و کاهش throughput ناشی از coarse claim باید با compatibility evidence و تصمیم صریح ثبت شود؛ خاموش/پنهان‌کردن اثر آن مجاز نیست.

### ۱۱.۲ Proposed resource model و derivation

تعریف domain پیشنهادی `ResourceClaim(key, mode, scope)`، modeهای `read` و `write`؛ mutation یک classification است و در conflict به write نگاشت می‌شود. scope یکی از `exact` / `subtree` / namespace object است. claimها از `ActionDescriptor` + validated arguments + capability context derive می‌شوند. field اختیاری **PROPOSED** `StepDefinition.resource_claims` فقط **سخت‌گیرانه‌تر** از derivation native مجاز است؛ برای نسخه اول ترجیح این است اصلاً public override expose نشود و derived claims در plan/status نمایش داده شوند. task implementation باید این انتخاب کم‌سطح‌تر را نگه دارد؛ consumer واقعی تازه تنها دلیل افزودن public field است و به ADR نیاز دارد.

| action موجود | derived claims پیشنهادی و دلیل |
|---|---|
| `check_file` | read exact canonical file؛ parent/subtree writers conflict دارند |
| `check_http` | request read در این action؛ endpoint grouping bounded، بدون ثبت URL secret/query؛ native resource mutation فرض نشود |
| `affected_tests` | read subtree repo source و read Git metadata؛ cacheهای metadata internal طبق existing lifecycle |
| `verify_changes` با fix=false | read subtree repo source + write cache/temp namespace اختصاصی هر invocation؛ pytest arbitrary repository code است، برای repo ناشناخته **coarse write repo claim** مگر read-only workload با policy معتبر و isolation اثبات شود |
| `verify_changes` با fix=true | برای DAG جدید ineligible تا mutation/postcondition contract اختصاصی تأیید شود؛ افزودن آن به این شش فاز لازم نیست. legacy workflow و foreground serial fix قرارداد قبلی را با write subtree claim حفظ می‌کنند؛ inner parallel همچنان deny |
| `apply_patch` | write exact همه old/new paths و namespace rename/delete؛ derive pathها با parser موجود، canonical parent overlaps؛ پیش از dry-run intent claim بگیرید |
| `git_status` | read canonical Git common-dir/worktree metadata و read worktree موردنیاز؛ Git lock side effects احتمالی conservatively بررسی شوند |
| `git_stage` | write Git index و read requested worktree paths؛ canonical git-dir و index path واقعی resolve شود |
| `git_commit` | write Git refs/HEAD/index namespace مشترک همان common-dir؛ worktree مربوط نیز در footprint لحاظ شود |
| `run_durable_job` | arbitrary executable است؛ resource inference از argv قابل‌اعتماد نیست؛ coarse `execution:unknown` exclusive managed-host claim + cwd subtree write تا terminal/reconcile، مگر execution policy داخلی footprint محدود را اثبات کند |
| browser/UIA adapter_required | همچنان unavailable؛ افزودن claim به معنی activation adapter نیست |

`ActionDescriptor.mutates=False` فعلی برای `verify_changes` به معنی purity واقعی pytest یا Ruff fix نیست. این **شکاف classification موجود** باید در eligibility موازی بسته شود: اعتبارسنجی `ruff_fix` و footprint derived، testing را conservative mutating-in-practice بداند. تغییر global API annotation یا native recovery به اسم cleanup خارج scope نیست، ولی parallel gate بدون این safety مجاز نیست. برای مثال DAG با سه `verify_changes` روی یک repo، overlap فقط با fixture/policy اثبات‌شده یا per-run immutable snapshot/isolation مجاز است. در workload عمومی، coarse claim آن‌ها را serialize می‌کند؛ speedup مصنوعی با حذف claim هدف نیست.

Canonical key: `canonical_path` موجود برای resolve/normcase base؛ key type prefix (`fs`, `git`, `cache`, `execution`) و semantic subtree relation با path components، نه string prefix ساده (`repo/a` با `repo/ab` هم‌پوشان نیست). Windows drive/UNC/junction، casefold platform، relative path و nonexistent destination parent لحاظ شود. Git worktreeهای مختلف می‌توانند common-dir مشترک داشته باشند؛ صرف repo path کافی نیست. hardlinkها با path equality کشف نمی‌شوند؛ برای inode/file identity قابل‌کشف در platform خاص alias supplement یا conservative coarse scope استفاده شود و limit مستند بماند. URL/secret/hash payload user به‌عنوان metric label یا public key خام ثبت نشود؛ شناسه امن bounded لازم است.

`execution:unknown` باید واقعاً universal conflict داشته باشد: هر participating action یک shared coordination claim روی host scope می‌گیرد و arbitrary unknown command یک exclusive claim همان scope؛ صرف key متفاوت با نام unknown همه keyها را قفل نمی‌کند. nested call همان execution با internal capability claim parent را reuse می‌کند تا exclusive parent در انتظار shared child deadlock نکند. این universal claim شامل writerهای غیرهمکار نیست و جای footprint جزئی را هم نمی‌گیرد.

### ۱۱.۳ Conflict matrix، acquisition و lifetime

| دو claim روی scope هم‌پوشان | read | write/mutation |
|---|---|---|
| read | مجاز | conflict |
| write/mutation | conflict | conflict |

کل resource set step قبل dispatch known و canonical sorted است. admission در یک `BEGIN IMMEDIATE`، همه conflictهای active و quarantined و ظرفیت‌ها را می‌سنجد و **همه یا هیچ** reserve می‌کند. acquisition در حالی که claim ناقص گرفته و منتظر بقیه است ممنوع. native process-local locks بعد از durable admission با canonical order گرفته و قبل release durable claim آزاد شوند؛ هیچ waiter DB transaction/`ResourceLocks` را نگه ندارد. acquiring منابع اضافه بعد شروع effect ممنوع؛ نیاز کشف‌نشده موجب fail-before-dispatch یا coarse claim از ابتدا می‌شود.

خواننده starvation و writer starvation با ticket order قدیمی‌تر (`created_at`, admission sequence, original index) حل شود؛ older conflicting writer جلوی readerهای تازه را می‌گیرد، ولی منابع واقعاً مستقل را متوقف نمی‌کند. wake-up محدود و backoff بدون busy loop؛ conflict reason bounded قابل‌مشاهده باشد. claim wait جزو workflow total/admission budget است، نه step execution timeout تا قبل barrier.

claim lifetime: از قبل prepare intent تا اتمام effect + postcondition + durable checkpoint + ownership cleanup. child durable job claim را تا terminal شدن همان child نگه می‌دارد؛ منتظرشدن parent یا مرگ parent claim را آزاد نمی‌کند. lease expiry فقط claim را **quarantined/unresolved** می‌کند، نه free. برای آزادکردن: process/child death مثبت و نتیجه/reconciliation مشخص، یا operator resolution audited لازم است. external ref نامعلوم، process inspection denied یا cleanup incomplete -> deny conflicting admission. TTL صرفاً trigger بررسی است، مجوز unlock نیست. موفقیت branch با child detached زنده settle کامل محسوب نمی‌شود.

### ۱۱.۴ SHA، external writers و durability

claim جلوی editor یا Git بیرون ComputerPilot را نمی‌گیرد. patch expected_sha256، publication atomic، Git before_head/staged_tree guards و fingerprint verification باید **زیر claim و نزدیک dispatch** دوباره check شوند. اگر external writer بعد check تغییر دهد، claim به‌تنهایی fencing kernel نیست؛ mutation helper باید precondition در نزدیک‌ترین boundary ممکن و atomic replace existing را حفظ کند. conflict یا stale fingerprint با success اشتباه نشود؛ result unknown/stale یا safe failure، بدون blind retry.

برای filesystem subtree snapshot بزرگ، نمی‌توان read locks میلیون فایل گرفت؛ repo subtree semantic claim و fingerprint bounded فعلی reuse شود. canonical key reassignment به دلیل junction swap باید pre-dispatch revalidate شود. SQLite busy/storage-full در گرفتن claim -> no dispatch؛ failure در checkpoint/release -> claim unresolved باقی و recovery بررسی کند. resource claim retention نباید با history cleanup حذف unresolved را مجاز کند.

### ۱۱.۵ Budget integration و فایل‌ها

**PROPOSED:** admission جدول P4 با category `workflow_step` و `analyzer` گسترش می‌یابد؛ analyzer permit در worker background و foreground یک policy manager به store می‌فرستد. `JobStore` root admission تغییر truth نمی‌دهد؛ worker قبل analyzer permit هیچ analyzer process نسازد. pending analyzerهایی که total deadline تمام شده dispatch نمی‌شوند. permit اثر running را تا cleanup نگه می‌دارد؛ بعد cancel future آزادشدن permit فرض نشود. برای non-managed direct helper contract، per-call cap باقی و claim عمومی سراسری ادعا نشود؛ public ComputerPilot entrypointها managed config یکسان دارند.

| نوع | فایل و مسئولیت |
|---|---|
| Modify | `core/workflow_store.py`, `core/workflow_executor.py`, `core/workflow_actions.py`, `core/config.py`, `core/resource_locks.py` فقط adapter حداقلی در صورت نیاز |
| Modify execution boundaries | `tools/filesystem/patches.py`, `tools/filesystem/registry.py`, `tools/language/edits.py`, `tools/documents/common.py`, `tools/git/registry.py` فقط مسیرهای conflict که این فاز activate می‌کند؛ writer بدون integration موازی eligible نیست |
| Modify در صورت تضمین command coordination | `tools/terminal/registry.py`, `tools/process/registry.py`, `tools/jobs/registry.py`, `scripts/job_worker.py`؛ coarse unknown footprint، lifetime و parent capability؛ بدون این coverage ادعای جلوگیری از conflict تمام native commands مجاز نیست |
| Modify analyzer integration | `tools/testing/verification.py`, `tools/testing/registry.py`, `scripts/verification_worker.py`, `tools/testing/background.py` فقط budget context و سازگاری request؛ `scripts/job_worker.py` تنها اگر انتقال ownership context لازم و آزموده باشد |
| Create پیشنهادی | `tests/test_parallel_workflow_resources.py`, `tests/test_parallel_admission.py`؛ helper production جدید فقط در صورت مرز مشترک اثبات‌شده و زیر ownership store |
| Test/Modify | resource_locks، job_admission/scheduler، call_admission، workflow postconditions/durable reconciliation/migrations/retention و native writer regressions |
| Docs/Modify | architecture/tooling/native tool authoring/CHANGELOG؛ بدون تغییر master plan قدیمی |

- [ ] **P5-T01 — footprint ADR.** از action input واقعی resource set derive و table بالا را با source دوباره تطبیق دهید؛ `verify_changes`/pytest و `run_durable_job` را conservative gate کنید؛ direct native writers affected را نام‌برده inventory کنید. acceptance: هر eligible action footprint authoritative دارد، unknown بدون coarse claim اجرا نمی‌شود.
- [ ] **P5-T02 — RED cross-process.** دو worker واقعی روی DB مشترک با file marker: read/read overlap، write/write و read/write serialize، subtree و alias و Git worktree common-dir؛ same key بعد process death. acceptance: شکست ResourceLocks-only با شاهد process مستقل، بدون تکیه صرف به threads.
- [ ] **P5-T03 — persisted claims.** migration بعد v8 با tip واقعی، FK/admission/attempt token، all-or-none conflict/cap reservation، fair tickets و quarantine؛ storage denial dispatch صفر. acceptance: DB transaction کوتاه، duplicate claim یا stale release winner ندارد.
- [ ] **P5-T04 — boundary integration.** claim را پیش از prepare intent بگیرید؛ parent capability internal به native helper منتقل؛ direct writers همان authority را مصرف کنند؛ ineligibleهای بدون coverage fail closed. acceptance: workflow-vs-native race نیز serialize، نه فقط workflow-vs-workflow.
- [ ] **P5-T05 — analyzer budget.** cap مشترک persisted و context در foreground/background/workflow؛ no parent-held child token؛ source defaults J=4/A=3 را stress؛ extension budget مستقل در host metrics. acceptance: analyzer peak instrumented<=budget در چند process و no nested deadlock.
- [ ] **P5-T06 — stale/uncertain.** lease loss، child زنده، PID reuse، inspection denial، orphan job، external writer/SHA mismatch و unlock-after-reconcile را اجرا کنید؛ unresolved claims در retention حفظ شوند. acceptance: TTL به‌تنهایی conflicting dispatch را آزاد نمی‌کند.
- [ ] **P5-T07 — deadlock/pressure validation.** C5/CSTATIC، چندصد mixed claim تحت event-controlled stress، reversed key order و abrupt worker death؛ WAL/queue/fairness و memory/handle روند را اندازه بگیرید. acceptance: deadlock/leak/starvation در workload finite نباشد و config حدود را رعایت کند.
- [ ] **P5-T08 — G5.** audit integration coverage و quarantine، commit/PR و exact tests؛ pointer=P6-T01. acceptance: G5 GO با activation عمومی هنوز بسته؛ hardening release باقی است.

TDD: T02 RED process-local gap؛ T03 GREEN durable claims؛ T04/T05 integration؛ T06/T07 kill/fault/pressure. commitها: `feat(workflows): reserve durable resource claims`، `feat(runtime): coordinate managed resource admission`، `feat(testing): enforce shared analyzer capacity`، `fix(workflows): quarantine unresolved resource owners`؛ هر commit کوچک با denial/cleanup tests و docs مربوط.

### ۱۱.۶ Matrix و G5

| محور | موارد ضروری |
|---|---|
| conflicts | read/read، read/write، write/write؛ exact/subtree، parent rename/delete، independent repo/file |
| aliases | relative/absolute، case Windows، junction، nonexistent destination، hardlink limit، Git common-dir |
| processes | دو WorkflowStore instance، دو OS worker، direct native writer، external noncooperating writer |
| stale | alive child after parent death، expired lease، PID reuse، denied inspection، cancel before/after claim، replacement lease |
| deadlock | reversed multi-key sets، nested verify/job، saturated job4/analyzer budget4، old writer vs new readers |
| failure | storage full/busy، claim transaction rollback، prepared barrier failure، postcondition unknown، release failure |
| safety | spoofed client claims/token denied، provider cannot self-authorize، key/URL secret canary absent |
| budgets | config decrease، manager multiplicity limitation، global analyzer permit، queue deadline و cleanup time |

**G5 GO:** cross-process و native writer conflict tests، nested budget، stale quarantine و SHA/precondition پاس، C5/CSTATIC سبز و coverage روشن. **NO-GO:** فقط local lock، TTL unlock، child زنده با آزادشدن write claim، pytest purity بدون evidence، job/permit deadlock. prerequisite P6: G5 و durable claims با recovery hooks؛ هیچ parallel mutable public activation پیش از G6.

## ۱۲. PHASE 6 — Parallel Cancellation, Recovery & Restart Hardening

**هدف:** release-grade behavior در cancel/failure/lease loss و مرگ process برای چند branch، با uncertainty صادقانه و بدون replay. **prerequisite:** G5. **non-goals:** exactly-once اثر arbitrary external system بدون primitive آن سیستم، rollback خودکار branch completed، retry دلخواه mutation یا عبور از release gate برای speedup.

### ۱۲.۱ cancellation fan-out و ownership

**PROPOSED:** cancel request با expected workflow version در transaction durable شود؛ سپس coordinator admission تازه را قطع و signal مشترک هر branch را set کند. ثبت cancel باید با ready claim در همان authority serialize شود؛ برنده claim اگر پیش از cancel barrier admitted شده هم باید درست پیش از may_dispatch دوباره cancel را check کند. branch نرسیده به barrier cancelled/no-effect؛ branch عبورکرده با cancel_mode خودش settle شود. result پس از cancel دور انداخته نشود.

| cancel_mode | رفتار لازم |
|---|---|
| immediate | فقط برای action با backend قابل‌stop و اثبات عدم mutation unresolved؛ pending worker future کافی نیست؛ owned childها terminate و reaped شوند |
| cooperative | `ActionContext` branch-specific signal همراه durable request؛ durable child job با JobStore.cancel؛ تا terminal/evidence claim نگه داشته شود |
| deferred | action bounded جاری تمام/timeout شود و postcondition/evidence حفظ؛ هیچ step تازه شروع نشود؛ UI به‌درستی cancelling بماند |

`ActionContext` پیشنهادی فیلد/closure stop signal و attempt validity می‌گیرد؛ signature compatibility handlers موجود حفظ شود. `run_bounded`/runnerهای testing فقط در صورت نیاز hook cancellation typed و internal می‌گیرند، با default behavior قبلی. process ownership authority همان `spawn_owned_process` است؛ registry PID دوم ساخته نشود. `Future.cancel()` فقط برای pending؛ return از `asyncio.to_thread` cancellation، thread و effect را stopped نمی‌کند. cancellation انتشار به همه analyzerهای فعال نیاز به context انتقال‌داده‌شده دارد؛ ContextVar به thread pool خودکار تضمین نشده و باید explicit/copy_context آزموده شود.

بعد از fan-out: completedها محفوظ، never-startedها cancelled/blocked با علت، activeها drain؛ uncertainty هر mutation unresolved اولویت دارد و aggregate `uncertain` می‌ماند، نه false cancelled. failure معلوم مستقل هم ثبت و aggregate طبق precedence فاز۴ انتخاب شود. برای workflowی که همه effectها تکمیل شده اما cancel پیش از aggregate آمده، cancel request و completed operationها هر دو حفظ و outcome legacy مطابق existing contract cancelled می‌شود؛ completion به‌عقب برگردانده نمی‌شود. هیچ terminal outcome تضمین undo نیست.

### ۱۲.۲ recovery algorithm پیشنهادی، مرحله‌به‌مرحله

1. startup DB migration/integrity و policy compatibility را بررسی؛ admission جدید برای recovery scope با ownership ناقص بسته باشد. graph/schema ناشناخته به safe error، نه legacy fallback.
2. فقط executionهایی که lease معتبر ندارند candidate شوند؛ workflow با owner زنده/lease جاری از recovery دست نخورَد. recovery خودش transaction/CAS ownership بگیرد تا دو constructor/reconciler رقابت نکنند.
3. **همه operationها و همه admission/resource claims همان execution** با pagination bounded خوانده شوند؛ completed records و attempts و child references حفظ شوند؛ graph metadata/hash verify شود.
4. succeeded/acknowledged را authoritative نگه دارید و projection step گمشده را repair؛ failed/unresolvable/cancelled را حفظ کنید؛ created با صفر attempts و بدون may_dispatch safe pending است.
5. reserved/prepared بدون effect، فقط با durable barrier phase و اثبات no-dispatch/no-child می‌تواند safe pending شود؛ absence یک result به معنی absence effect نیست. may_dispatch/running/waiting بدون conclusive checkpoint uncertain است، حتی اگر PID دیگر دیده نشود.
6. child job با request_key/external_ref را از JobStore بخوانید؛ running/orphaned/admission queued با effect uncertain را reattach/observe کنید، **submit مجدد command نکنید**. alive child claim را نگه می‌دارد. terminal success و exit0 به همراه postcondition معنادار اجازه resolve می‌دهد؛ failed/interrupted به‌تنهایی رد قطعی اثر partial نیست.
7. resource claims مربوط به uncertain effect quarantine باقی؛ PID/create-time و ownership backend را بررسی؛ access denied یا cleanup incomplete به خطای bounded و blocker می‌انجامد، نه unlock.
8. postconditionهای persisted allowlisted را side-effect-free evaluate کنید؛ observed failed postcondition فقط وقتی «عدم اثر/قابل retry» را اثبات می‌کند که reconciler contract چنین تضمینی دارد. file absent پس از crash می‌تواند نتیجه writer خارجی باشد؛ خودکار replay mutation مجاز نیست.
9. هر operation resolution با expected version/attempt generation و aggregate recomputation atomic؛ یک operation resolved، uncertainty sibling را پاک نمی‌کند. manual acknowledgement با actor/reason/audit و CAS؛ unresolvedها همچنان resume را می‌بندند.
10. aggregate از graph و cancel/failure facts محاسبه؛ safe unfinished -> paused، unresolved -> uncertain، تمام موفق -> completed مگر cancel معتبر طبق contract. startup خودبه‌خود mutation را resume نمی‌کند.
11. `workflow_resume` فقط پس از بررسی **همه** uncertainها، quarantined claims و childهای attached/active؛ expected_version، immutable definition و mode/activation را validate و queued کند. completed branch skip؛ failed branch retry فقط با policy زیر؛ cancelled workflow terminal و immutable بماند.
12. release/reclaim claim و permit فقط با matching token و evidence؛ event/journal با safe metadata، recovered count و duration ثبت؛ unresolved حفظ و admission scope conflicting بسته بماند.

### ۱۲.۳ retry، timeouts، lease loss و restart variants

**PROPOSED retry policy:** descriptor `never` یعنی خودکار retry صفر؛ `max_retries` آن را override نمی‌کند. `transient` فقط خطای شناخته‌شده‌ای که handler تأیید می‌کند pre-effect یا read-only-safe است؛ attempts count durable و backoff bounded موجود reuse شود. mutation uncertain هیچ auto retry ندارد. explicit resume روی failed mutation نیز نیازمند evidence «dispatch نشده/اثر ندارد» یا operator تصمیم مجاز برای workflow جدید است؛ failed یک label کافی برای replay نیست. lease/CAS/busy failure فقط retry transaction metadata می‌دهد، نه action. child job retained idempotency key attach می‌شود؛ اگر رکورد retention حذف شده و prior dispatch ممکن بوده، recreate ممنوع و نیازمند resolution انسانی است.

timeoutها: queue timeout job پیش از claim، admission/claim wait workflow، step action timeout، workflow total deadline و cleanup grace جدا ثبت شوند. earliest valid deadline dispatch را محدود می‌کند؛ clamp budget در هر analyzer/step start دوباره محاسبه شود. monotonic clock برای مدت داخل process؛ persisted UTC deadline برای restart با skew bounds؛ عقب‌رفتن ساعت heartbeat نباید permit نامحدود بدهد. lost lease پیش از dispatch => denied؛ بعد dispatch => stop new work، signal/drain و uncertain تا recovery. هیچ old owner با success دیررس aggregate تازه را commit نمی‌کند.

برای DAG، intent preparation و postcondition نیز همان remaining deadline را مصرف کنند؛ timeout ثابت helperها clamp شود. `check_file` برای `contains` نیازمند read chunked با سقف byte/deadline و overlap جست‌وجوی صحیح بین chunkها است، یا برای فایل بزرگ با خطای bounded پیش از dispatch fail کند؛ خواندن فایل نامحدود با thread غیرقابل‌قطع پذیرفته نیست. تغییر رفتار legacy فایل بزرگ/timeout باید صریح در compatibility tests و docs ثبت شود، نه به اسم parallelism بی‌خبر عوض شود. failure یا cancellation وسط آماده‌سازی پیش از barrier، no-effect بماند؛ postcondition deadline بعد effect به uncertainty می‌رود.

MCP restart: workflow thread از بین می‌رود؛ durable job worker ممکن است زنده بماند؛ recovery attach. runtime-only supervisor restart: mutation admission بسته، grace drain با defaults source **15s** و watchdog **2s**؛ این graceها تضمین خاتمه هر branch نیستند. supervisor process death/OS kill: process ownership platform و DB evidence باید تعیین تکلیف کنند؛ preserved durable jobs را عمداً قربانی cleanup runtime نکنید. worker standalone death و new worker با همان DB تست جدا. full host reboot case با fixture durable stale state شبیه‌سازی و ادعای native reboot تنها با اجرای واقعی ثبت شود.

### ۱۲.۴ فایل‌ها و Taskها

| نوع | فایل و مسئولیت |
|---|---|
| Modify | executor/store/reconciliation/actions/models، `tools/workflows/registry.py` resume/cancel/status، workflow retention فقط active evidence protection |
| Modify cancellation boundary در صورت نیاز | `core/executor.py`, `core/process_ownership.py`, testing runners/verification/registry، `scripts/verification_worker.py`, `scripts/job_worker.py`؛ هیچ process owner موازی ساخته نشود |
| Modify lifecycle/integration | `core/lifecycle.py`, `core/heartbeat.py`, `scripts/supervisor.py`, `scripts/workflow_worker.py` فقط drain/restart contracts مرتبط |
| Create پیشنهادی | `tests/test_parallel_workflow_recovery.py`؛ fault fixtures محلی در pattern tests موجود، نه public network |
| Test/Modify | cancellation/crash/durable_reconciliation/concurrency/retention/migration/process/supervisor/lifecycle/admission suites و extension zero-mode regression |
| CI/Docs Modify | `.github/workflows/ci.yml`, `.github/workflows/platform-validation.yml` فقط additions ضروری؛ `scripts/platform_validation.py` در صورت test integration؛ architecture/tooling/testing standards/CHANGELOG و release evidence |

- [ ] **P6-T01 — crash matrix RED.** برای هر boundary جدول بخش ۱۲.۵ یک injectable stop/marker در fixture طراحی کنید؛ subprocess child/grandchild و durable effect counter مستقل؛ baseline cancellation/recovery latencies و targetها freeze. acceptance: تمام crash windowها task/test/evidence ID دارند، فقط mock کافی نیست.
- [ ] **P6-T02 — fan-out.** durable cancel stop-admission، pending/running branch signal، cooperative child job و deferred completion؛ cancel hooks runner در boundary واقعی با cleanup؛ latency و no-new-dispatch assert. acceptance: completed preserved و active tree با status صادقانه settle می‌شود.
- [ ] **P6-T03 — death/lease/restart.** one heartbeat fail signal، stale generation reject، real kill executor/MCP/standalone worker/supervisor runtime؛ owned process cleanup، orphan attach و claim quarantine. acceptance: هیچ surviving unknown effect با permit آزاد پنهان نمی‌شود.
- [ ] **P6-T04 — reconcile/resume/retry.** algorithm بالا برای همه branchها؛ manual ACK vs auto reconcile CAS race؛ retry_policy واقعی gate؛ failed mutation resume guard؛ cancel reason در restart حفظ. acceptance: یک branch resolved خواهر uncertain را حل‌شده نشان نمی‌دهد و replay counter صفر است.
- [ ] **P6-T05 — exhaustive stress/platform/security.** C6/CSTATIC، mixed cap/nested budget/resource contention و random interleavings با seed ثبت‌شده؛ Windows/Linux/macOS real processes، secret canary همه sinkها، quota exhaustion و cleanup denial. acceptance: leak/deadlock/duplicate/false terminal صفر در workloadهای تعریف‌شده، skip platform به‌عنوان pass شمرده نشود.
- [ ] **P6-T06 — CI/docs/rollback drill.** deterministic fake-only tests وارد CI موجود؛ fixtures isolated؛ migration rollback drill روی کپی، activation disable و restore از backup بدون حذف evidence؛ docs exampleهای executable و limits نهایی. acceptance: operator با docs بتواند recovery صحیح انجام دهد و ten-check topology بی‌دلیل تغییر نکند.
- [ ] **P6-T07 — review/release gate.** بررسی مستقل معنادار source+diff+tests+denials؛ در اجرای آینده reviewer انسانی یا مستقل با مجوز مناسب، بدون expand scope؛ تمام CSTATIC/CRELEASE و same-head ده hosted gate؛ performance targets و unresolved issueها بررسی. acceptance: ایرادهای critical/high باز و activation بدون evidence ممنوع.
- [ ] **P6-T08 — G6 و evidence.** ledgerها، migration version، config defaults واقعی، SHA/PR، platform/results و activation decision را ثبت؛ final docs و Definition of Done را کامل؛ release publication تنها در دستور مجاز و guarded flow. acceptance: G6 GO و handoff self-contained برای همان SHA، یا NO-GO با PAR ID واقعی.

TDD: T01 چند RED برای هر failure class؛ T02..T04 GREEN محدود؛ T05 stress پس از focused pass؛ T06 rollback drill؛ T07 بعد آخرین relevant edit full verification. commitهای پیشنهادی: `fix(workflows): fan out cancellation across active branches`، `fix(workflows): reconcile graph executions before resume`، `test(workflows): cover process death and concurrent recovery`، `docs(workflows): document parallel execution recovery`؛ هر fix با regression مرتبط، commit کلی «parallel overhaul» ممنوع.

### ۱۲.۵ Exhaustive crash/failure matrix و G6

| crash point / race | durable truth / expected action |
|---|---|
| قبل reservation | created، dispatch صفر، safe pause/resume |
| بعد claim قبل intent | reserved، no-effect فقط با barrier/ownership proof؛ claim recovery |
| بعد intent قبل may_dispatch | prepared محفوظ؛ no-child proof، نه حدس از نبود result |
| بعد may_dispatch قبل actual effect | uncertain conservative؛ no blind replay |
| بعد effect قبل result | intent/postcondition/external_ref برای reconcile؛ duplicate effect صفر |
| بعد operation success قبل step/aggregate | repair projection از success، بدون re-execute |
| دو success هم‌زمان با cancel | هر دو evidence حفظ؛ cancel CAS و precedence درست |
| failure A، success B، running C و pending join | B محفوظ؛ C drain/uncertain؛ join blocked؛ aggregate از همه |
| lease expiry وسط سه branch | admission بسته، late checkpoint رد، هر سه scan و claim quarantine |
| cancel queued child job vs claim | یک winner؛ no cancelled-to-running dispatch |
| parent می‌میرد، durable child زنده | attach by request key؛ مصرف capacity/claims تا settle |
| DB busy/full/corrupt در pre-dispatch | no effect؛ safe storage failure و bounded error |
| DB failure پس از effect | uncertainty حفظ، no success بدون checkpoint، no release unsafe claim |
| cleanup permission denied/root exit/PID reuse | owner proof و bounded error؛ process دیگر اشتباه terminate نشود |
| reconcile vs ACK vs دو recovery process | CAS یک winner، sibling states حفظ، event درست |
| restart با cancel flag و همه completed | cancel contract حفظ، هیچ completed پاک یا replay نشود |
| unknown schema/policy/graph hash | fail closed؛ نه sequential fallback |
| retention هم‌زمان با recovery/status | active/uncertain/claim evidence حذف نشود؛ status safe |
| deadline در permit wait/backoff/in-flight | no new dispatch و timeout/uncertain براساس effect؛ cleanup جدا |
| config cap کاهش/افزایش حین backlog | running محفوظ، admission تابع policy معتبر، oversubscribe صفر |

**G6 GO:** همه 48 task و gateهای قبلی DONE، checks واقعی نهایی و platform/ownership/rollback/security/performance evidence کامل، تمام blocking PARها resolved، review مستقل و ده hosted checks همان head و سپس main سبز؛ activation فقط پس از این شواهد. **NO-GO:** uncertainty replay، free stale claim، mutable parallel path بدون footprint، pending platform gate، benchmark نامقایسه‌پذیر یا rollback data loss. G6 آمادگی release را اعلام می‌کند؛ خودش مجوز ad-hoc tag/publish نیست.

## ۱۳. Migration Plan سراسری

این برنامه به `WorkflowStore` migrationهای embedded و JobStore موجود متکی است. ابزار migration تازه نصب نشود. شماره‌های زیر **PROPOSED relative to schema6** هستند؛ قبل اجرای هر فاز tip زنده بررسی و ledger اصلاح شود.

| مرحله | تغییر | پیش‌شرط حفظ داده |
|---|---|---|
| P1/P2 | بدون DB migration | old tool invocationها همچنان معتبر |
| P3 / proposed v7 | graph mode/version، step identity، dependency edges | exact legacy payload/hash/operation ID ثابت؛ legacy unrecoverable fail closed |
| P4 / proposed v8 | attempt generation، dispatch phase، execution admission و workflow cap/deadline metadata | operation/step atomic checkpoint جدید؛ completed قدیمی دوباره running نشوند |
| P5 / proposed v9 | resource claim rows، quarantine، analyzer admission categories | authority همان DB؛ unresolved claim retention فعال |
| P6 | ترجیح بدون schema جدید؛ فقط اگر evidence واقعی نیاز داشته باشد، migration بعد tip با ADR | هیچ rewrite/remove evidence؛ default legacy semantics ثابت |

گام‌های deployment/migration اجرایی آینده:

1. activation DAG parallel خاموش و admission تازه متوقف؛ inventory workerهای MCP، standalone workflow و durable job و DB path/config fingerprint را ثبت کنید. تنها خاموش‌کردن پنل یا درخواست client کافی نیست.
2. در copy ایزوله schema version، row count هر table، terminal/unresolved counts، definition/operation IDs/hash و foreign_key_check را baseline کنید. از DB credential-bearing یا payload دقیق dump عمومی نسازید؛ hash/count و encrypted/protected backup reference کافی است.
3. backup سازگار SQLite با connection backup API یا shutdown همه DB writers و copy coordinated انجام شود؛ live `.sqlite3` تنها بدون WAL copy نشود. permissions backup مطابق exact execution payload حساس باشد. backup، WAL/state/job outputs و references لازم را با policy حفظ کنید.
4. نسخه جدید روی کپی upgrade؛ migration lock/concurrent open/restart وسط هر stage، idempotent reopen، too-new old reader denial و legacy no-payload fixture را اجرا کنید.
5. schema/data/graph integrity و legacy bytes/IDs را verify؛ activation هنوز بسته؛ startup/catalog و health و relevant suite را روی state ایزوله بگذرانید.
6. workerهای قدیمی workflow را متوقف و جدیدها را یکدست deploy؛ old/new concurrent writer روی DB جدید مجاز نیست. durable child job زنده را بی‌دلیل kill نکنید؛ protocol backward compatibility worker جاری با spec قدیمی آزمایش شود.
7. production migration با maintenance window محدود و audit، سپس read-only health/status؛ queued DAG صرفاً چون DB upgrade شده نباید auto-run شود. در failure، NO-GO، evidence و PAR ID ثبت و admission بسته بماند.
8. پس از G6، opt-in کوچک cap2 فقط روی fixture/approved independent footprint و سپس workload عملی؛ default legacy و cap1 حفظ. config enable/disable رسمی در docs ثبت شود؛ هیچ hidden environment bypass.

legacy definition را با reconstruct/new `asdict` و hash جدید «ترمیم» نکنید. default-field omission نسخه‌مند و compatible serialization تست می‌شود. graph metadata باید از exact definition قابل‌اثبات باشد؛ rows graph conflicting دست‌کاری‌شده fail closed. هر index جدید explain/query-time evidence داشته باشد؛ indexهای speculative متعدد نگذارید.

## ۱۴. Rollback / Recovery Plan

**rollback behavior با rollback داده یکی نیست.** disable activation و cap1 تغییرات durable را undo نمی‌کند. بعد از DB upgrade، اجرای binary قدیمی روی همان DB جدید مجاز نیست و schema-too-new باید آن را رد کند. downgrade با ویرایش دستی `PRAGMA user_version`، DROP table یا حذف operation/claim ممنوع است.

مسیر کم‌خطر rollback:

1. admission تازه را ببندید؛ cancel/drain با branch evidence، active job و claim inventory؛ unresolved را پاک نکنید.
2. feature flag/config را disable و cap تازه را1 کنید؛ اجرای legacy روی binary جدیدِ سازگار ترجیح دارد. DAG queued را paused/blocked با علت نگه دارید؛ آن را sequential تفسیر نکنید.
3. از DB/state پس از حادثه backup بگیرید تا success/uncertainty جدید گم نشود؛ failure evidence و old/new SHAs را journal کنید.
4. اگر binary rollback لازم است، rollback روی backup پیش‌ازmigration در محیط ایزوله اول تمرین شود. **در production restore قدیمی پس از effect جدید ممکن است dedup evidence را عقب ببرد و duplicate اجرا ایجاد کند.** در این حالت restore خودکار ممنوع؛ forward repair یا offline reconcile اثرهای جدید، child refs و tombstoneهای idempotency قبل admission الزامی است.
5. اگر هیچ effect بعد backup رخ نداده و همه workerها متوقف‌اند، restore با مجوز destructive لازم و کنترل مسیر/permissions؛ user authorization اجرای implementation به‌خودی‌خود مجوز حذف state نیست. fixture rollback در تست مجاز است؛ restore production تصمیم مستقل است.
6. post-rollback startup/health، schema، counts، unresolved claims، orphan children و no-replay markers بررسی شوند؛ pointer فقط بعد evidence به مرحله مناسب بازگردد.

drill فاز۶ باید دو حالت «بدون effect پس از migration» و «effect جدید با checkpoint ناقص» داشته باشد؛ اولی restore-safe با proof، دومی forward-recovery-only تا reconcile. rollback criterion: هیچ evidence unresolved گم نشده، هیچ effect دوباره execute نشده، credential redaction و file ownership حفظ و DB integrity درست است.

## ۱۵. Observability / Metrics و پاسخ اپراتور

متریک‌ها در timing/health/audit موجود ادغام شوند؛ storage/registry مانیتورینگ مستقل ساخته نشود. `Settings.resource_budgets`, `WorkflowStore.health_summary`, runtime health snapshot و panel summaries مصرف‌کننده‌اند. تغییر panel فعلی کاربر محفوظ است؛ UI توسعه فقط در مرحله اجرای مجاز با diff هماهنگ، و اگر JSON/status کافی است UI اضافه ضروری نیست.

| signal پیشنهادی | نوع و meaning | محدودیت cardinality/privacy |
|---|---|---|
| requested/effective concurrency | gauge/category برای analyzer/step و configured cap | تعداد label محدود، repo path یا workflow ID label نشود |
| ready/active/blocked counts | count از canonical graph/claims | recent samples bounded؛ readiness حقیقت دوم persist نشود |
| admission queue wait / run / cleanup | histograms یا timing spans موجود | duration، mode و outcome enum؛ raw argv ممنوع |
| denied admissions | counter به علت cap/resource/cancel/lease/policy/storage | error code ثابت؛ provider exception متن خام ممنوع |
| lease heartbeat lag/loss | lag و count؛ active lease count جدا از operation | owner token کامل در log/health منتشر نشود |
| claim waits/quarantine | count/age و namespace type | conflict key حساس hash/redact؛ sample bounded |
| branch transitions | audit event attempt/step index/state/barrier category | exact arguments/result خام persist عمومی نشود |
| recovery duration/outcome | scan count، repaired success، paused، uncertain، attached child | evidence ref و bounded reason؛ no automatic replay counter باید صفر بماند |
| cancellation | request-to-admission-stop، request-to-terminal و pending cleanup | explicit `cancelling/uncertain` را از cancelled جدا نشان دهید |
| DB pressure | busy count/wait duration، WAL/database bytes، transaction duration | queries/payload کامل log نشود |
| process/resource pressure | roots/descendants/RSS/threads/FD/handles/spool bytes | PID/create-time فقط در evidence مجاز، نه high-cardinality metric |

اگر export metrics موجود برای histogram مناسب نبود، نخست spans و bounded aggregate از همان ابزار timings reuse؛ backend metrics تازه non-goal است. eventهای branch می‌توانند arrival-dependent باشند؛ status array و diagnostic order canonical است. audit failure نباید effect جدید را replay کند؛ اگر durable evidence ضروری قابل‌نوشتن نیست قبل dispatch fail closed. operator در دیدن quarantine باید status/operation/child reference را بخواند، reconcile کند و فقط پس از proof آزاد کند؛ دکمه «clear all locks» یا retry-all نسازید.

## ۱۶. Test Strategy، Stress/Race Tests و CI additions

### ۱۶.۱ لایه‌های تست

1. **Pure validation/model:** graph، canonical JSON، deterministic order، resource conflicts و transition guard با بدون process و filesystem effect غیرضروری.
2. **Store integration:** SQLite واقعی `tmp_path`، دو connection و دو instance، CAS/admission/migration/retention. snapshot metadata فقط assertions لازم، نه test mirror از SQL داخلی.
3. **Executor deterministic concurrency:** event/barrier و markerهای مستقل، cap counter و controlled failure؛ timeout هر wait محدود و cleanup `finally`.
4. **Public MCP schema/client:** `Client(create_server(), raise_exceptions=True)` و `asyncio.run`/AnyIO patterns موجود؛ testing/jobs profiles، strict unknown fields، route admission و lifecycle no-dispatch.
5. **Real subprocess/ownership:** Windows Job Object، POSIX sessions و grandchild مستقل؛ restart و root-exit، permission-denied simulation و real cleanup با finite fixture؛ orphan child زنده اثبات شود.
6. **Full runtime/release:** startup/catalog، health، Full Doctor، platform validation، deterministic packaging و independent checksum/manifests همان SHA طبق release process.

هیچ dependency جدید test مانند pytest-asyncio یا pytest-xdist صرفاً برای style وارد نشود. repo از Python3.10 پشتیبانی می‌کند. fixtures configuration/environment/state را isolate می‌کنند؛ testها real extension config، credentials، `.agent_state` اصلی یا public MCP server نمی‌خوانند. cwd/import root و interpreter را قبل اجرا verify کنید. baseline flaky/unrelated را isolate و evidence ثبت کنید؛ شکست لازم را به بهانه pre-existing حذف نکنید.

### ۱۶.۲ Stress/Race workloadهای اجباری

| workload | روش قابل‌تکرار | invariant |
|---|---|---|
| admission stampede | چند OS process، start barrier و یک DB؛ job/step/analyzer هم‌زمان | cap و one attempt winner |
| diamond/wide DAG | chain، diamond، 100 nodes، ready fan-out و join fan-in | every edge respected، no lost result |
| random interleaving | seed ثبت‌شده، تأخیر در checkpoint/intent/postcondition/cleanup | canonical projection، success conserved، no duplicate effect |
| cancel storm | cancelهای CAS هم‌زمان با claim/complete/retry و child submit | no new post-cancel dispatch؛ safe late result |
| lease churn | expire/replace و heartbeat DB busy با branchهای blocked | old token رد، claim owner جدید محفوظ |
| resource hot spot | readers زیاد + writer قدیمی + independent key | no writer starvation، independent progress |
| nested pressure | چهار job × سه requested analyzers + چند workflow + extension fixture calls | effective analyzer/global steps cap؛ extension policy مستقل؛ deadlock صفر |
| crash sweep | kill واقعی در تمام نقاط جدول۱۲.۵ با durable marker | recovery classification و no blind replay |
| retention race | status/reconcile/recover/cleanup هم‌زمان | unresolved/active evidence محفوظ |
| output storm | analyzer diagnostics زیاد/duplicate/exception و JSONL byte boundary | bounded stream، deterministic cap، unknown حفظ |
| process leaks | چند چرخه start/cancel/restart با count پیش/پس | children/threads/handles/spool رشد ماندگار نداشته باشند |

تکرار پیشنهادی تست deterministic race حداقل **50 بار** برای focused seed set و soak پیشنهادی **15 دقیقه** در environment ایزوله است؛ این اعداد test methodology هستند، نه نتیجه یا SLA. runtime CI با timeoutهای موجود **15 دقیقه CI** و **25 دقیقه Platform** مقایسه شود؛ تست طولانی در validation مناسب قرار گیرد، broad suite را پنجاه بار در هر matrix اجرا نکنید. test با random sleep تنها، race proof نیست؛ event/marker جای دقیق crash window را نشان دهد.

### ۱۶.۳ CI additions لازم، بدون تغییر release authority

- P1/P2 tests به discovery pytest عادی اضافه می‌شوند؛ schema/default regression همان platform matrix را استفاده کند.
- P3 migration/identity fixtures در همه Windows/Linux/macOS و Python3.10/3.12 اجرا شوند؛ test داده تاریخی در repository با payload ساختگی، نه state واقعی.
- P4/P5 deterministic bounded concurrency/store tests در CI؛ real ownership/resource multi-process focused subset در `scripts.platform_validation` متناسب budget. تست heavy با marker/documented invocation فقط اگر consumer CI واقعی وجود دارد.
- P6 crash/restart/security subset در سه platform؛ full soak به job مشخص و bounded مناسب، بدون public network؛ seed/artifact logs sanitization و retention مشخص.
- `MCP_EXTENSIONS_ENABLED: "0"` سطح workflowهای اصلی حفظ؛ fixture explicit opt-in؛ fake provider موجود reuse شود. تغییر global/per-extension ceilings خارج scope نیست مگر failure مرتبط و ADR.
- سه workflow existing روی PR به main و push main حفظ؛ شش CI + سه Platform + یک Packaging همان **ده check** فعلی‌اند. اضافه‌کردن check blocking تازه باید docs/branch protection و release انتظارها را همزمان به‌روز کند؛ صرف test addition نیاز check-name جدید ندارد.
- artifactهای benchmark/recovery خام با secret یا exact workflow payload upload نشوند؛ summary metadata/hash و logs redacted فقط. failing logs برای diagnosis حفظ و gate دور زده نشود.

## ۱۷. Documentation Updates و Commit/PR policy

در اجرای واقعی این artifact canonical ledger می‌ماند؛ duplicate master plan نسازید. `docs/tooling.md` نمونه parameter/default/timeout/cancel/DAG، `docs/architecture.md` owner/store/admission/recovery، `docs/development/TESTING-STANDARDS.md` fixtures و gateهای واقعی و authoring docs در صورت resource-claim contract جدید به‌روز شوند. active `CHANGELOG.md` با تاریخ واقعی Gregorian برای تغییر substantive؛ در planning حاضر changelog edit نمی‌شود. public error codeها و proposed APIها بعد implementation فقط با source/tests قابل‌تأیید به CURRENT تبدیل شوند.

هر commit یک علت روشن و واحد تست‌پذیر داشته باشد. schema+reader+writer+fixtures که برای compatibility یکدیگر لازم‌اند در واحد coherent بمانند؛ آن‌ها را به commitهای موقتاً شکسته تجزیه نکنید. commit کلی چندفازی، `misc fixes`، staging کل worktree و واردکردن uv.lock/panel user/generated state ممنوع. Conventional Commit با فعل/اثر واقعی و بدون wording generated authorship. این نوبت هیچ commit یا branch ایجاد نمی‌کند.

برای implementation آینده flow repo: short-lived branch از current origin/main پس از وضعیت سالم، tested checkpoints، PR به main، exact current-head ده checks، merge مجاز طبق دستور اجرای وقت، سپس push checks همان main SHA پیش از فاز بعد. authorization تاریخی v0.8 نباید این planning-only را به خودکار implementation/merge تبدیل کند. انتظار CI و permissionهای خارجی با status/evidence گزارش شود؛ completion در pending CI ادعا نشود.

## ۱۸. دفترهای اجرایی قابل‌ویرایش

### ۱۸.۱ Append-only Execution Journal

هیچ entry تاریخی overwrite/delete نشود؛ correction با entry جدید و reference قبلی. timestamp شامل timezone؛ tests شامل command یا Verification ID و outcome واقعی؛ commit بی‌وجود ساخته نشود. برای task boundary هر شروع، RED، GREEN، verification، failure، blocker resolution و handoff یک entry بدهید. ستون next step باید عمل مشخص، نه «ادامه بده» باشد.

| Entry | Timestamp | Phase/Task | Action | Result | Tests | Commit | Next step |
|---|---|---|---|---|---|---|---|
| J-PLAN-001 | 2026-10-17T16:05:24+03:30 | PLANNING/AUDIT | Git/source/graph audit مستقیم | baseline d721aa8 و dirty files ثبت شد؛ state محصول تغییر نکرد | source/test inspection؛ product tests NOT_RUN | بدون commit | تکمیل شش فاز و self-review سند |
| J-PLAN-002 | 2026-10-17T16:19:56+03:30 | PLANNING/SELF-REVIEW | structural check، source caveats و Git visibility بررسی شد | UTF-8/links/fences و 6/48/48 پاس؛ roadmap ignored با `.gitignore:63`؛ اصلاح deadline/footprint design | V-PLAN؛ product tests NOT_RUN | بدون commit | اجرای دوباره بررسی artifact پس از آخرین edit و تحویل |
| J-0001 | 2026-10-17T20:00:17Z | P5-T04 / IN_PROGRESS | متن پیست‌شده و کل طرح خوانده شد؛ پنج commit تا 3c5fe14 و تمام 21 فایل pending از worktree قبلی به مسیر اصلی منتقل شدند | branch=fix/parallel-resource-ownership؛ panel و uv.lock با SHA256 قبل/بعد یکسان محفوظ؛ worktree قبلی دست‌نخورده؛ graph tool با Connection closed در دسترس نبود و source fallback استفاده شد | .venv Python3.12.8؛ reportlab موجود؛ چهار تست گزارش‌شده: 2 passed, 2 failed | HEAD=3c5fe14؛ اصلاحات بدون commit | child capability را پیش از spawn validate و به env منتقل؛ guard DAG fix؛ سپس تست‌های واقعی job و analyzer |

| J-0002 | 2026-10-17T20:09:41Z | P5-T04 / VERIFYING | worker پیش از spawn active owner/scope را validate می‌کند؛ token تا16384کاراکتر محدود؛ child env و analyzer copy_context؛ DAG fix قبل persistence رد می‌شود | forged/released/quarantined/wrong-owner و scope denial با counter مستقل no-spawn؛ legacy fix محفوظ | چهار تست اولیه2pass/2fail؛ worker denial پنجRED؛ thread context دوRED سپسGREEN | HEAD=3c5fe14 + working diff | full diagnostic و resource authority |
| J-0003 | 2026-10-17T20:20:23Z | P5-T04 / resource authority | full diagnostic:6failed/1405passed/23skipped؛ temporary JobStore به default resource DB وصل بود و تست kill worker بقیه را block می‌کرد | worker authority کنار jobs DB؛ nested analyzer از parent authority؛ test doubles قرارداد جدید را پذیرفتند؛ panel فقط test با HTML unescape سازگار شد و فایل کاربر تغییر نکرد | authority و child capacity هرکدام RED/GREEN؛ شش failure اولیه در focused10/10 پاس شدند | بدون commit جدید | cancellation permit cleanup |
| J-0004 | 2026-10-17T20:25:03Z | P5-T04 / cleanup verification | cancellation واقعاً analyzer permit را باقی می‌گذاشت؛ PID/create-time binding و snapshot فرزندهای Windows launcher پیش از termination؛ release فقط پس از cleanup موفق؛ alive_pids یعنی failed و permit محفوظ | تست cancellation از1active به0active؛ PID reuse و cleanup-incomplete denied؛ یک claim و سه permit فقط مربوط به fixtureهای همین اجرا با positive process-death و cancelled-job interval reconcile شدند؛ history حذف نشد | latest boundary93passed/1POSIXskip؛ Ruff/mypy269/compileall/diff-check پاس؛ final full pending | HEAD=3c5fe14؛ source/test diff SHA256=d44f62038d99108261c7c53a3c7648ec2fc5cd9e6953f008aaf42e128af49a7f | final full روی diff تثبیت‌شده، health و checkpoint |
| J-0005 | 2026-10-17T20:31:58Z | P5 repair / VERIFIED checkpoint | workspace=C:\Users\Meliodas\Downloads\tunnel-client-v0.0.11-windows-amd64؛ branch=fix/parallel-resource-ownership؛ HEAD=3c5fe14f7be58cfc06c0409c9d1032a04b02b4a3؛ staged=0؛ task paths=23tracked modified+4new؛ ignored canonical plan هم به‌روز شد؛ panel و uv.lock متعلق به کاربر با hash اولیه محفوظ | final code/test digest=d44f62038d99108261c7c53a3c7648ec2fc5cd9e6953f008aaf42e128af49a7f پیش/پس ثابت؛ root state unresolved resource/capacity=0؛ process مرتبط با final fixture=0؛ worktree قدیمی دست‌نخورده؛ commit/push/PR جدید انجام نشد | V-FIX-FULL-FINAL و V-FIX-STATIC/SMOKE؛ artifact UTF8/48tasks/links/fences پاس | بدون commit جدید | scope فعلی تمام؛ کل Phase5/6 و release/activation کامل اعلام نشود؛ Full Doctor، hosted CI و native Linux/macOS اجرا نشده‌اند |

برای append بعدی همین ستون‌ها را با Entry ترتیبی بعدی و داده واقعی پر کنید؛ row خالی یا نتیجه فرضی در دفتر نیست.

### ۱۸.۲ Blocker & Issue Log

ID پایدار `PAR-NNN` هرگز recycle نمی‌شود. severity: **CRITICAL** خطر duplicate/destructive effect یا exposure؛ **HIGH** safety/recovery/compatibility gate؛ **MEDIUM** boundedness/performance/observability مهم؛ **LOW** نقص docs غیرمسدود. state issue یکی از OPEN/RESOLVED/ACCEPTED_LIMIT؛ ACCEPTED_LIMIT فقط با تصمیم صریح و در صورت nonblocking بودن invariant. وجود issue طراحی به معنی شکست اجرای این planning نیست. blocker task اجرایی واقعی در dashboard فقط وقتی آن task فعال شد BLOCKED/FAILED می‌شود؛ اکنون taskها NOT_STARTED هستند.

| ID / State | Severity | First seen | Affected task | Evidence | Workaround / containment | Resolution لازم | Resolved commit |
|---|---|---|---|---|---|---|---|
| PAR-001 / OPEN | MEDIUM | 2026-10-17 | P1-T03 | foreground registry signature/forwarding `max_parallel_checks` ندارد، موتور دارد | foreground serial فعلی | expose parameter، validation/schema/regressions در G1 | بدون commit؛ unresolved |
| PAR-002 / OPEN | HIGH | 2026-10-17 | P3-T04 | `canonical_execution_definition=asdict` و `_operation_payload` در hash/ID نقش دارند | افزودن field بدون serializer سازگار ممنوع | legacy golden bytes/hash/idempotency و migration proof | بدون commit؛ unresolved |
| PAR-003 / OPEN | HIGH | 2026-10-17 | P4-T03 | `acquire_lease` برای owner یکسان lease زنده را برمی‌گرداند؛ checkpoint_operation expected_version ندارد | یک execute owner فعلی؛ DAG activation بسته | no-reentry execution + attempt/CAS fencing، same-owner race test | بدون commit؛ unresolved |
| PAR-004 / OPEN | HIGH | 2026-10-17 | P4-T06 / P6-T04 | recovery و aggregate reconciliation از current_step/step_index+1 استفاده می‌کنند | فقط sequential workflows فعلی | all-branch recovery/reconciliation و crash matrix | بدون commit؛ unresolved |
| PAR-005 / OPEN | HIGH | 2026-10-17 | P5-T03 | ResourceLocks process-local، exact key و فاقد read/write/subtree است | mutable workflow parallel فعال نشود | persisted all-or-none claims و native boundary coverage | بدون commit؛ unresolved |
| PAR-006 / OPEN | CRITICAL | 2026-10-17 | P5-T06 / P6-T03 | lease expiry checkpoint را fence می‌کند، process side effect را لزوماً متوقف نمی‌کند | quarantine، no replay و deny conflicting admission | death/child/effect evidence و reconcile-before-release | بدون commit؛ unresolved |
| PAR-007 / OPEN | MEDIUM | 2026-10-17 | P5-T05 | J=4 و A=3 تا 12 analyzer مستقیم؛ native calls/workflowها سقف مشترک ندارند | per-call opt-in، default1، nesting محدود | shared analyzer/step budget و pressure benchmarks | بدون commit؛ unresolved |
| PAR-008 / OPEN | HIGH | 2026-10-17 | P5-T01 / P6-T04 | verification descriptor mutates=false؛ pytest repository code و serial Ruff fix می‌توانند اثر داشته باشند | conservative coarse claims؛ fix parallel ممنوع | footprint/eligibility و safe retry classification آزموده | بدون commit؛ unresolved |
| PAR-009 / OPEN | MEDIUM | 2026-10-17 | P2-T04 | diagnostics loop per-backend timeout و exception جزئی کنترل‌نشده | serial current؛ no hard-total claim | indexed bounded execution، opt-in total و unknown semantics | بدون commit؛ unresolved |
| PAR-010 / OPEN | HIGH | 2026-10-17 | P6-T04 | executor retry loop `TransientActionError` را با max_retries می‌سنجد، descriptor retry_policy مستقیم enforce نمی‌شود | unknown mutation auto retry مجاز نشود | enforce retry_policy و evidence gating روی resume/retry | بدون commit؛ unresolved |
| PAR-011 / RESOLVED | HIGH | 2026-10-17 | P5-T04 | child env فاقد capability؛ worker inherited token را بدون validation می‌پذیرفت؛ limit200 برای token واقعی ناکافی بود | activation عمومی DAG بسته | bounded validated capability، process/thread propagation و denied-no-spawn؛ V-FIX-FULL-FINAL | local working diff d44f6203؛ بدون commit |
| PAR-012 / RESOLVED | HIGH | 2026-10-17 | P5-T01 | DAG Ruff fix پیش از persistence رد نمی‌شد | activation عمومی DAG بسته | validation guard و assertion صفر workflow row؛ legacy serial fix محفوظ؛ V-FIX-FULL-FINAL | local working diff d44f6203؛ بدون commit |
| PAR-013 / RESOLVED | HIGH | 2026-10-17 | P5-T04/P5-T05 | temporary JobStore و analyzer از authority اشتباه استفاده می‌کردند؛ cancellation سهمیه را باقی می‌گذاشت | DAG عمومی بسته؛ فقط fixtureهای تأییدشده reconcile شدند | parent/co-located authority؛ PID/create-time و descendant cleanup؛ alive/denied/reuse tests؛ V-FIX-FULL-FINAL | local working diff d44f6203؛ بدون commit |

وقتی issue حل شد evidence/test IDs، resolution و resolved commit/PR را روی همین row به‌روز و یک journal entry append کنید. اگر دلیل یا scope عوض شد history در journal بماند. runtime blocker تازه، ID بعدی `PAR-011` است.

### ۱۸.۳ Decision Log / ADR-lite

این‌ها تصمیم‌های **PROPOSED_FOR_IMPLEMENTATION** هستند؛ baseline طراحی این سندند، نه API ساخته‌شده. تغییرشان باید قبل source edit با دلیل، alternatives، compatibility/security impact و verifier در همین log ثبت و revision journal شود. هیچ design undecided به API موجود معرفی نشده است.

| ADR | تصمیم | alternatives ردشده / دلیل | اثر و criterion |
|---|---|---|---|
| ADR-P01 | foreground parameter با default1 و reuse guard موتور | استفاده از کل VerificationRequest، چون foreground serial fix را هم رد می‌کند | backward-compatible schema + no-dispatch invalid modes |
| ADR-P02 | diagnostics serial default، per-backend timeout قدیمی؛ total opt-in | تبدیل silently timeout قدیمی به total، چون زمان مجاز callهای قدیمی کم می‌شود | deterministic slots و unknown partial results |
| ADR-P03 | explicit execution_mode=dag و step_id جدا از name؛ derived readiness | تبدیل name به identity یا نبود deps=parallel، چون legacy duplicate names/serial می‌شکند | old JSON/hash/IDs ثابت؛ DAG explicit |
| ADR-P04 | یک coordinator/heartbeat، no executor reentry، attempt/CAS branch | heartbeat thread per branch و lease یکسان same-owner، چون race و DB pressure | exact admitted attempt و no old-token checkpoint |
| ADR-P05 | persisted claims در WorkflowStore و local locks حفظ | ResourceLocks-only یا registry truth جدید، چون multi-process/restart safe نیست | no conflicting managed writer dispatch |
| ADR-P06 | all_success join و stop-admission-on-failure | continue-on-error/conditional branches، چون scope و recovery را پیچیده می‌کند | completed محفوظ، descendants blocked |
| ADR-P07 | quarantine بعد lease loss و reconcile قبل resume/release | TTL unlock و blind retry، چون اثر قدیمی ممکن است ادامه داشته باشد | no duplicate mutation و no unsafe conflicting admission |
| ADR-P08 | inner analyzer budget مشترک، parent بدون child permit | ضرب همه capها یا یک semaphore per process، چون oversubscription/distributed admission غلط | peak و nested deadlock تست واقعی |
| ADR-P09 | feature activation عمومی پس از G6؛ foundationها gated merge | فعال‌سازی scheduler قبل resource/recovery، چون independent DAG به معنی safe resources نیست | هر entrypoint gate واحد و denial marker صفر |
| ADR-P10 | conservative pytest/arbitrary job footprint و external-writer preconditions | infer read-only از action label یا argv، چون authoritative نیست | speedup فقط در منابع اثباتاً مستقل |

هر ADR هنگام freeze task مربوط، تاریخ/owner و source SHA واقعی را در Notes آن task و journal بگیرد؛ الان همه planning-only و بدون implementation commit هستند.

### ۱۸.۴ Verification Ledger

این دفتر actual را از expected جدا می‌کند؛ تست اجرا‌نشده PASS نیست. برای هر اجرای تازه یک ID جدید، نه overwrite failure قبلی. فیلدهای لازم: timestamp، task، tested HEAD + relevant diff، platform/Python/deps، command، expected، actual/exit/count/duration، artifact reference، reviewer و effect on gate.

| ID | Timestamp / Task | Command / evidence | Expected | Actual | Tested SHA / Diff | Gate effect |
|---|---|---|---|---|---|---|
| V-AUDIT-001 | 2026-10-17 / PLANNING | `git status --short`, branch, log5, remote, diff stat؛ scoped source reads و graph query | شناخت checkout و pathهای واقعی بدون mutation | خوانده شد؛ dirty panel و untrackedها ثبت؛ permission caveat بخش۱ | d721aa8؛ panel diff متعلق به کاربر | فقط audit؛ implementation gate پاس نشده |
| V-PLAN | 2026-10-17 / PLANNING | Python stdlib check با `.venv/Scripts/python.exe` روی UTF-8، 33 لینک محلی، Markdown fences، required source paths، شمارش phases/tasks/dashboard و markerهای ناتمام؛ `git diff --check`؛ status و `git check-ignore -v` | فقط artifact جدید و بدون نقص ساختاری | PASS؛ شش phase، 48 Task یکتا و 48 سطر task dashboard؛ لینک نامعتبر و replacement char و marker ناتمام صفر؛ diff check exit0. Product tests/benchmark/CI NOT_RUN. Plan طبق ignore rule در diff معمولی نیست، پس whitespace خود فایل جدا چک می‌شود | d721aa8 + همین plan؛ تنها tracked diff همان panel قبلی است | فقط آمادگی تحویل سند؛ G1..G6 همچنان باز |
| V-IMPLEMENTATION-STATE | 2026-10-17 / P1..P6 | C1..C6 / CSTATIC / CRELEASE | در زمان اجرای آینده باید پاس شوند | NOT_RUN؛ هیچ source test/benchmark/runtime/CI در این planning اجرا نشده | بدون implementation diff | همه G1..G6 باز |

| V-FIX-BOUNDARY | 2026-10-17T20:25:03Z / P5-T04 | `.venv/Scripts/python.exe -m pytest -o addopts= -q --basetemp=.pytest-tmp-cleanup-alive-green tests/test_job_worker.py tests/test_background_verification.py tests/test_shared_analyzer_capacity.py tests/test_windows_job_backend.py tests/test_parallel_workflow_resources.py tests/test_parallel_diagnostics.py` | no deadlock، no invalid dispatch، cancellation cleanup، retained uncertain capacity | 93passed,1skipped در39.30s؛ POSIX-only skip روی Windows | 3c5fe14 + repair working diff؛ guard و fixture مرتبط بررسی شد | specific repair سبز؛ تمام G5/G6 acceptanceها پاس فرض نمی‌شوند |
| V-FIX-STATIC | 2026-10-17T20:25:03Z / P5-T04 | `.venv/Scripts/python.exe -m ruff check .`؛ `-m mypy core tools scripts tests`؛ `-m compileall -q core tools scripts tests`؛ `git diff --check` | exit0 | PASS؛ mypy269فایل | d44f62038d99108261c7c53a3c7648ec2fc5cd9e6953f008aaf42e128af49a7f | static سبز |
| V-FIX-SMOKE | 2026-10-17 / P5-T04 | MCP_EXTENSIONS_ENABLED=0؛ `.venv/Scripts/python.exe main.py --check` و `.venv/Scripts/python.exe -m scripts.health_check --json` | startup/catalog و health موفق | exit0؛151unique tools؛ import/mcp_startup/tool_registration/filesystem/terminal=true | 3c5fe14 + d44f62038d99108261c7c53a3c7648ec2fc5cd9e6953f008aaf42e128af49a7f | local smoke سبز؛ Full Doctor/hosted/native POSIX NOT_RUN |
| V-FIX-FULL-PRE-GUARD | 2026-10-17 / P5-T04 | `.venv/Scripts/python.exe -m pytest -o addopts= -q --tb=short --basetemp=.pytest-tmp-resource-final-confirmed` با JUnit در TEMP | full regression | 1416passed,23skipped در318.52s؛ exit0؛ این run پیش از آخرین guard alive_pids شروع شده بود و final same-diff gate محسوب نمی‌شود | 3c5fe14 + pre-guard working diff | baseline broad سبز؛ final d44f6203 run جدا لازم است |
| V-FIX-FULL-FINAL | 2026-10-17T20:31:58Z / 2026-10-18 تهران / P5 repair | MCP_EXTENSIONS_ENABLED=0؛ `.venv/Scripts/python.exe -m pytest -o addopts= -q --tb=short --basetemp=.pytest-tmp-resource-d44f6203-final --junitxml=$env:TEMP/computerpilot-resource-d44f6203-final.xml`؛ log همان نام با پسوندlog درTEMP | full regression روی آخرین code/test diff | exit0؛1417passed,23skipped در304.61s؛ JUnit independently parsed:1440tests/0failures/0errors/23skipped؛ fixture processes=0 | HEAD3c5fe14 + SHA256=d44f62038d99108261c7c53a3c7648ec2fc5cd9e6953f008aaf42e128af49a7f؛ digest قبل/بعد یکسان | specific repair VERIFIED؛ G5/G6/release/hosted و Full Doctor پاس فرض نمی‌شوند |

برای RED، expected=fail_on_missing_behavior و actual خطای واقعی با assertion؛ برای GREEN، exact command و pass/skip counts؛ برای skipped check، علت و gate باز ثبت شود. artifact خام حساس به plan/PR paste نشود. SHA تنها بدون working diff برای check روی کد uncommitted کافی نیست؛ diff digest یا reviewed file list همراه evidence لازم است.

### ۱۸.۵ Commit/PR Ledger

| Ledger ID | Phase/Task group | Status | Intended boundary | Commit SHA | PR | Verification refs | Notes |
|---|---|---|---|---|---|---|---|
| CP-PLAN | PLANNING artifact | بدون commit | فقط همین Markdown | ایجاد نشده | ایجاد نشده | V-PLAN | درخواست commit/push نشده است |
| CP-P1 | P1-T02..T08 | NOT_STARTED | foreground schema/forwarding + regressions/docs | ایجاد نشده | ایجاد نشده | NOT_RUN | implementation شروع نشده |
| CP-P2 | P2-T02..T08 | NOT_STARTED | diagnostics bounded execution + deadline/merge tests | ایجاد نشده | ایجاد نشده | NOT_RUN | commitهای coherent قابل‌تقسیم |
| CP-P3 | P3-T02..T08 | NOT_STARTED | graph validation؛ migration/identity جدا و کامل | ایجاد نشده | ایجاد نشده | NOT_RUN | snapshot schema قدیمی حفظ |
| CP-P4 | P4-T02..T08 | NOT_STARTED | attempt fencing؛ scheduler؛ all-branch recovery | ایجاد نشده | ایجاد نشده | NOT_RUN | activation بسته |
| CP-P5 | P5-T02..T08 | NOT_STARTED | durable claims؛ native boundaries؛ analyzer budget | ایجاد نشده | ایجاد نشده | NOT_RUN | هر تغییر با denial/race tests |
| CP-P6 | P6-T01..T08 | NOT_STARTED | cancellation؛ recovery؛ stress/release evidence | ایجاد نشده | ایجاد نشده | NOT_RUN | commit کلی چندفازی ممنوع |

هنگام اجرا برای هر commit واقعی row فرزند با Task IDs و SHA و exact PR head افزوده شود؛ intent row بالا جای actual commit نیست. merge SHA و post-merge main checks هم reference جدا بگیرند. gate مربوط با commit بعدی که code را تغییر داده دوباره ارزیابی شود.

### ۱۸.۶ interruption / handoff procedure

agent پیش از توقف، pointer و active Task را update و یک journal entry شامل absolute worktree، branch/HEAD، diff/staged/untracked summary، invariants، verified tasks، pending exact commands، blocker IDs، claim/job processهای زنده test و next action مشخص ثبت کند. task نیمه‌کاره DONE نمی‌شود. agent بعدی plan، AGENTS، status/history و adjacent tests/evidence را بخواند؛ test پاس قبلی فقط اگر source/config/input/runtime assumptions همان است reuse شود. در اجرای این نوبت active child job یا implementation branch ساخته نشده است.

## ۱۹. End-to-End Example — `prepare_release` پیش و پس از DAG

### ۱۹.۱ رفتار CURRENT واقعی builtin

`tools/workflows/builtins.py:builtin_workflow("prepare_release", parameters)` سه step زیر می‌سازد؛ publish یا tag نمی‌سازد:

```text
index0 validate clean release state: git_status(require_clean=true, tag_must_not_exist=tag)
  -> index1 validate version declaration: check_file(exists=true, contains=version)
  -> index2 run full verification: verify_changes(affected_only=false)
  -> workflow COMPLETED
```

`implement_and_verify` فعلی نیز فقط `git_status -> verify_changes` برای تغییر **قبلاً پیاده‌شده** است؛ کدی تولید نمی‌کند. در طرح حاضر builtinها به‌صورت پیش‌فرض تغییر نمی‌کنند. مثال بعد یک **custom definition پیشنهادی** با همان actionهای واقعی است و بعد G6 قابل‌اجرا می‌شود، نه API قابل‌استفاده فعلی.

### ۱۹.۲ تعریف Proposed کامل برای fixture ایزوله

ورودی فرضی: fixture repository در `C:/fixture/release-repo` با `pyproject.toml` شامل version `0.9.0`، clean Git و tag `v0.9.0` ناموجود. این path/version، مثال داده fixture است؛ هیچ فایل یا release واقعی با آن ساخته نشده است. root fixture باید روی هر platform با path محلی همان تست جایگزین شود. policy footprint بررسی شود: pytest ناشناخته coarse repo write می‌گیرد؛ فقط workload read-only تأییدشده و cache/output ایزوله اجازه read-claim overlap با check_file دارد. اگر چنین proof نیست، graph معتبر است ولی resource scheduler دو branch را serialize می‌کند.

```json
{
  "definition": {
    "name": "prepare_release_dag_example",
    "description": "Validate clean state, version and full checks; never publish",
    "execution_mode": "dag",
    "max_parallel_steps": 2,
    "total_timeout_sec": 7200,
    "steps": [
      {
        "step_id": "inspect",
        "name": "validate clean release state",
        "action": "git_status",
        "arguments": {
          "repo": "C:/fixture/release-repo",
          "require_clean": true,
          "tag_must_not_exist": "v0.9.0"
        },
        "depends_on": [],
        "timeout_sec": 30,
        "max_retries": 0
      },
      {
        "step_id": "version",
        "name": "validate version declaration",
        "action": "check_file",
        "arguments": {
          "path": "C:/fixture/release-repo/pyproject.toml",
          "exists": true,
          "contains": "0.9.0"
        },
        "depends_on": ["inspect"],
        "timeout_sec": 30,
        "max_retries": 0
      },
      {
        "step_id": "verify",
        "name": "run full verification",
        "action": "verify_changes",
        "arguments": {
          "cwd": "C:/fixture/release-repo",
          "affected_only": false,
          "fail_fast": false,
          "ruff_fix": false,
          "max_parallel_checks": 1
        },
        "depends_on": ["inspect"],
        "timeout_sec": 1800,
        "max_retries": 0
      }
    ]
  },
  "idempotency_key": "release-fixture-090-revision-1"
}
```

`execution_mode`, `step_id`, `depends_on`, `max_parallel_steps`, workflow `total_timeout_sec` و workflow-action `max_parallel_checks` در این مثال **PROPOSED** هستند؛ در checkout audit هنوز پذیرفته نمی‌شوند. پارامتر inner verification پس از P4/P5 با bounds و guard موتور فعلی اضافه می‌شود. path/args واقعی actionها مطابق input models موجودند. هیچ action خیالی `join` یا `publish` تعریف نشده است؛ join انتهایی aggregate است:

```text
                      inspect [git_status]
                       /              \
                      v                v
             version [check_file]   verify [verify_changes]
                      \                /
                       \              /
                        ALL_SUCCESS aggregate
                                |
                             COMPLETED
```

با requested step cap2 و analyzer inner1، دو branch مستقل حداکثر دو step و حداکثر یک analyzer مربوط به verify دارند؛ analyzer permit global هنوز لازم است. برای inner3 در این مثال باید guard و budget اجازه دهند و fail_fast=false بماند. نسخه با cap1 همین dependency graph را deterministic و serial در ترتیب original index اجرا می‌کند.

### ۱۹.۳ سفر اجرا و حالت‌ها

1. `workflow_plan` پیشنهادی graph را validate می‌کند: 3 step، 2 edge، inspect تنها root، version/verify children، all_success implicit join؛ no process dispatch. plan resource footprint و effective caps را safe projection می‌دهد.
2. `workflow_start` exact definition/hash و edges و operations را atomic persist می‌کند. تکرار key و payload یکسان همان workflow را می‌دهد؛ تغییر depends_on یا cap با همان key conflict است. `workflow_status.version` برای execute خوانده شود.
3. `workflow_execute(expected_version=...)` unique owner/lease می‌گیرد. یک inspect attempt admitted و checkpointed می‌شود. پس از success، version/verify ready؛ اگر claims مستقل و policy مناسب است cap2 هر دو را می‌گیرد؛ در غیر این صورت conflict reason باعث serialization می‌شود.
4. **Success:** هر دو success checkpoint؛ completed_count=3، active=0، uncertain=0، current_step=3، aggregate completed. verify failure/unknown یا stale نتیجه موفقیت release محسوب نمی‌شود. این workflow همچنان publish نمی‌کند.
5. **Failure:** inspect failure هیچ child را dispatch نمی‌کند؛ reason parent_failed. اگر version failure هنگام verify running رخ دهد، admission تازه متوقف؛ verify drain و نتیجه آن حفظ؛ aggregate failed و failed branch مشخص. retry خودکار صفر؛ parent effect rollback نمی‌شود. اگر verify uncertain cleanup باشد، uncertainty aggregate اولویت دارد.
6. **Cancel:** پیش از inspect dispatch، marker صفر و pendingها cancelled. بین children، startedها signal/deferred drain؛ completed version محفوظ. verify با owned analyzer stop-hook پس از P6 یا deferred bounded behavior خود settle می‌شود؛ cancelling تا اثبات cleanup، نه immediately cancelled. cancellation موفق به معنی پاک‌شدن diagnostics یا undo file نیست.
7. **MCP/worker crash:** اگر version success persist و verify may_dispatch باشد، پس از lease expiry recovery version را حفظ و verify را uncertain می‌کند؛ current_step=کمترین index ناتمام، authority همان states است. verify فقط چون read-only label دارد replay نمی‌شود؛ arbitrary pytest ممکن است اثر داشته باشد. پس از ownership/effect reconciliation یا operator resolution، safe resume تنها branch مجاز ناتمام را اجرا می‌کند؛ success قبلی دوباره اجرا نمی‌شود.
8. **Durable job variant:** اگر در custom workflow دیگری step واقعی `run_durable_job` به‌جای verify استفاده شد، `DurableJobInput.idempotency_key` و postcondition `job_request_key_intent` الزامی؛ parent crash به attach همان job/request key منتهی می‌شود، نه submit تازه. effect intent و external_ref و child terminal state برای release claim لازم‌اند.

این مثال گارانتی speedup برای repo واقعی نمی‌دهد. DAG وابستگی را آشکار می‌کند؛ resource safety و budget تعیین می‌کنند کدام overlap مجاز است. benchmark سرعت فقط بعد proof استقلال منابع و روی همان fixture/input/SHA معنی دارد.

## ۲۰. Release Gate و Definition of Done کلی

release readiness تنها وقتی صحیح است که تمام موارد زیر evidence واقعی همان diff/head داشته باشند:

- هر شش GO gate و تمام Taskهای این plan DONE؛ هیچ required task با DEFERRED یا NOT_RUN پوشانده نشده؛ pointer/journal/ledgerها با source هماهنگ‌اند.
- default serial، foreground fix، diagnostic merge/order/unknown و workflow legacy hashes/IDs/positional contracts با regressions ثابت‌اند.
- typed schema، validate-before-dispatch، one owner، attempt fencing، bounded queues/outputs/caps، all-success join و no duplicate dispatch در processهای واقعی اثبات شده‌اند.
- resource claimها durable، canonical، deadlock-safe و cross-process‌اند؛ direct managed writer coverage مستند؛ unknown footprint conservative؛ external writer limitation و SHA guards روشن‌اند.
- cancellation fan-out، lease loss، child orphan، worker/MCP/supervisor restart و uncertainty/resume/retry matrix بدون blind replay پاس شده‌اند؛ completed branchها حفظ‌اند.
- old schema migration، concurrent open، corruption/too-new denial، exact payload preservation و rollback/forward recovery drill روی داده ایزوله پاس شده‌اند.
- required tests، Ruff، mypy، compileall، startup/catalog، health، Full Doctor، platform validation Windows/Linux/macOS و performance targets ثبت و actual=PASS متناسب‌اند؛ skip به‌عنوان pass تلقی نشده است.
- secret canary، redaction، lifecycle admission، policy/native routing و zero-extension regressions پاس؛ resource pressure/leak/cleanup failureها تعیین تکلیف شده‌اند.
- independent review معنادار انجام و Critical/High blocking issues resolved با commit/test؛ benchmark نتیجه ساختگی ندارد و regression budget پیشاپیش ثبت شده است.
- شش CI، سه Platform و Packaging Smoke روی exact PR head و پس از merge روی exact main SHA موفق؛ پنج archive deterministic و checksums/manifests مستقلاً verify شده‌اند؛ publication فقط guarded same-SHA flow مجاز است.

این نوبت هیچ‌یک از gateهای implementation یا release را پاس اعلام نمی‌کند. نسخه نهایی plan به معنی آمادگی شروع P1 پس از دستور اجراست.

## ۲۱. Final Master Checklist

- [ ] **PHASE 1 / G1:** expose foreground parameter، default1، reused validation، schema/docs/profile و invalid-no-dispatch؛ P1-T01..T08 DONE.
- [ ] **PHASE 2 / G2:** bounded diagnostics، deterministic request/result ordering، merge/dedup/truncation/unknown و timeout/cancellation؛ P2-T01..T08 DONE.
- [ ] **PHASE 3 / G3:** explicit graph/identity، cycle/unknown/self/duplicate validation، migration/readiness، legacy exact sequential/hash/IDs و execution gate بسته؛ P3-T01..T08 DONE.
- [ ] **PHASE 4 / G4:** bounded ready scheduler، single owner/heartbeat، attempt/CAS، all-success/failure/join و all-branch crash foundation با activation بسته؛ P4-T01..T08 DONE.
- [ ] **PHASE 5 / G5:** authoritative derived footprint، persisted claims، native conflict coverage، deadlock/stale/SHA، nested budgets با activation بسته؛ P5-T01..T08 DONE.
- [ ] **PHASE 6 / G6:** fan-out cancel، restart/reconcile-before-resume، retry guards، process/lease/partial-success hardening و exhaustive tests؛ P6-T01..T08 DONE.
- [ ] **Migration readiness:** backup/integrity/legacy compatibility/concurrent upgrade و rollback drill پاس و نسخه واقعی ledger ثبت شده.
- [ ] **Safety readiness:** uncertainty محفوظ، blind replay صفر، denied paths dispatch صفر، quarantine و no duplicate effect در crash fixtureها ثابت.
- [ ] **Performance readiness:** serial baseline، cap/queue/DB/cancel/restart/pressure measurements و targets واقعی ثبت، بدون تعمیم mock به host واقعی.
- [ ] **Documentation/ledger readiness:** current pointer، journal، PAR resolution، ADR decisions، verification و commit/PR ledgers با source هماهنگ.
- [ ] **Release readiness:** independent review، full local gates، ده hosted same-SHA checks، deterministic packaging/checksums و guarded release flow تأیید شده.

### ۲۱.۱ Self-review artifact پیش از تحویل

بازبینی محتوایی انجام شده: current/proposed/unverified تفکیک شده؛ source names با checkout تطبیق دارند؛ APIهای تازه پیشنهادی علامت خورده‌اند؛ legacy name/serialization، current_step linear recovery، non-pure pytest، same-owner lease و process-local limits به‌صراحت پوشش دارند؛ شش فاز دقیقاً هشت Task اصلی دارند؛ data migration/rollback و activation gap بین P4/P5/P6 پنهان نشده‌اند. هر Task dashboard همه فیلدهای الزامی را دارد و status آن صادقانه NOT_STARTED است. sectionهای نمونه و اعداد target methodology، نتیجه اجرا معرفی نشده‌اند. نقص ساختاری احتمالی باید قبل actual PASS در V-PLAN اصلاح شود؛ check نهایی UTF-8/links/fences/count/status/git در همان ledger ثبت می‌شود.

Artifact روی disk وجود دارد و به دلیل `.gitignore:63` ignored است؛ هیچ ignore exception یا force-add در این نوبت انجام نشده است. برای انتقال به clone/PR آینده، مجازبودن انتقال همین plan با artifact قدیمی v0.8 اشتباه نشود و تصمیم tracking همین فایل صریح ثبت شود؛ جای canonical artifact را بی‌خبر عوض نکنید.




### J-0006 — 2026-10-17T20:38:12+00:00

- scope صریح کاربر تا پایان P5 و P6 گسترش یافت؛ activation منوط به تمام گیت‌ها باقی ماند.
- checkpoint تست‌شده `45715d6` روی `fix/parallel-resource-ownership` به origin push شد؛ PR draft: https://github.com/dibbed/computerpilot-mcp/pull/81 .
- Codebase Memory با اتصال stdio تازه نسخه 0.12.1 و root صحیح تأیید شد؛ exact lookup retention مسیرهای caller را نشان داد. Graphify قدیمی d721aa8 فقط راهنمای مسیرهای موجود است، index تازه ادعا نمی‌شود.
- P5-T06 IN_PROGRESS: retention terminal workflow می‌تواند resource request حل‌نشده را با FK cascade حذف کند؛ تست پیش از اصلاح اضافه می‌شود.

### V-P5-LIFETIME-20261018

- RED: 4 retention failures proved FK cascade erased pending/active/quarantined claims, including a scan/delete race; 3 cleanup failures proved conflicting dispatch was admitted after denied/incomplete ownership cleanup.
- GREEN: `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_job_worker.py tests/test_parallel_workflow_resources.py tests/test_workflow_retention.py tests/test_workflow_concurrency_gates.py --tb=short --basetemp=.pytest-tmp-resource-lifetime-final-20261018` => 44 passed, 2 skipped (Windows unavailable link/POSIX-only cases), exit0. Includes inherited claims and failed quarantine persistence.
- Ruff/mypy on all six changed Python files and git diff --check passed. This is focused acceptance, not all G5/G6.
- Changes: core/resource_claims.py, core/workflow_retention.py, scripts/job_worker.py, tests/test_job_worker.py, tests/test_parallel_workflow_resources.py, tests/test_workflow_retention.py, CHANGELOG.md. Next: native writer coverage audit and missing cross-process/pressure cases.

### V-P5-NATIVE-WRITERS-20261018

- Codebase Memory exact symbol searches identified atomic_write, apply_workspace_edit and document_write_lock callers; current source verified local-lock boundaries before edits.
- RED: six public native file writers ignored a read-only parent capability and mutated the fixture. GREEN: scope denial before effect, native writer blocked by durable reader before acquiring a local lock, document/LSP denial.
- `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_filesystem_registry.py tests/test_document_foundation.py tests/test_language_hardening.py tests/test_language_registry.py tests/test_parallel_workflow_resources.py --tb=short --basetemp=.pytest-tmp-native-writers-final2-20261018` => 36 passed, exit0. Ruff and mypy six affected Python files passed.
- Native process background handles remain outside the cooperative guarantee; no global guarantee against nonparticipating/external writers is claimed. Next acceptance: capacity/storage bounds and remaining lifecycle/pressure cases.

### V-P5-ADMISSION-BOUNDS-20261018

- RED: three failures for unbounded resource/capacity records and claim bytes; four failures for live capability after expired lease/replaced attempt/cancel/removed execution admission.
- GREEN: `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_parallel_workflow_resources.py tests/test_parallel_workflow_scheduler.py tests/test_shared_analyzer_capacity.py tests/test_workflow_retention.py --tb=short --basetemp=.pytest-tmp-g5-matrix-20261018` => 49 passed, 1 skipped, exit0. Includes real-process read/read, read/write, write/write, subtree/prefix independence and the existing 200-request pressure case. Ruff/mypy four affected source/test files passed.
- Config: MCP_MAX_RUNTIME_ADMISSION_RECORDS default10000 range1..100000 per table; released-only eviction, no TTL unlock; keys64KiB/request. Public activation remains closed.
- Remaining P5 finding: analyzer cleanup uncertainty needs persisted parent-claim linkage, including storage failure on quarantine; schema tip verified9, additive migration planned10. Independent read-only reviewer permission requested for P6-T07; answer is pending and no subagent was spawned.

### V-P5-CAPACITY-PARENT-20261018

- RED: three failures showed denied analyzer cleanup or failed quarantine storage could release the parent claim. Two health cases falsely reported healthy with native ownership quarantine.
- Schema tip9 verified before additive migration10: nullable capacity.resource_request_id FK SET NULL + resource/state index; legacy capacity rows preserved; active legacy claims quarantined when unlinked unresolved permits exist. No history deleted.
- GREEN: migrations/resources/capacity/background verification/job worker suite =>112 passed,1 skipped in39.35s; health/migration/capacity final after fixture assertion correction =>31 passed,1 skipped in5.95s. Both exit0; exact base dirs .pytest-tmp-resource-schema10-20261018 and .pytest-tmp-schema10-focused2-20261018. Ruff/mypy six affected files passed. Upgrade-denied CREATE INDEX rolls back added column and version; reopen upgrades successfully; integrity/FK checks pass.
- New runtime health reason resource_ownership_unresolved; public DAG remains disabled. Remaining acceptance: settlement must never project success with unresolved child ownership, native process cleanup denial and G5 pressure/coverage audit; P6 not yet started.

### V-P5-SETTLEMENT-FENCE-20261018

- RED: two DAG executions falsely completed despite unreleased/quarantined analyzer ownership; four stale workflow capability cases still admitted capacity through the Store API.
- GREEN: scheduler/resources/capacity =>44 passed in22.77s, exit0 (`.pytest-tmp-settlement-fence-green-20261018`); shared live-owner SQL now gates capacity under its transaction, attempt settlement checks linked ownership atomically, and executor uses actual settled state for resource disposition. Ruff/mypy four affected Python files passed.
- Commit prior checkpoint e4006ca pushed (schema10 linkage); current changes remain on original workspace fix/parallel-resource-ownership. Public activation remains closed. Next: operator resolution cannot bypass child ownership, native process cleanup/ownership proof, final G5 coverage/pressure gates.

### V-P5-WINDOWS-OWNERSHIP-20261018

- Codebase Memory exact WindowsJob.close lookup identified close_ownership and spawn error cleanup callers; source verified before edits. Five RED cases: failed CloseHandle silently succeeded; query/termination/active-process uncertainty was forgotten; psutil fallback could kill a PID outside native authority.
- Official Microsoft accounting/query APIs verified. A disposable native probe confirmed ActiveProcesses=0 after root wait while Popen retains its handle (total2 due venv launcher). Production cleanup now terminates the owned Job Object, polls native active count within5s, validates CloseHandle, and preserves uncertainty after failure; no PID fallback kills.
- GREEN: `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_windows_job_unit.py tests/test_windows_job_backend.py tests/test_process_ownership.py tests/test_posix_process_ownership.py tests/test_job_worker.py tests/test_executor.py --tb=line --basetemp=.pytest-tmp-job-proof-final-20261018` =>61 passed,5 skipped in4.47s,exit0. Native Windows parent/grandchild are dead immediately on cleanup return; portable fake denial/PID-reuse/ABI48-byte accounting cases pass. Ruff/mypy three affected files pass.
- No public DAG activation. Next G5: native ownership required for workflow children, child-safe operator resolution, remaining footprint/fairness/storage denial and measured pressure; then final C5/CSTATIC and G5 decision.

### V-P5-CHILD-OWNERSHIP-20261018

- RED: workflow child executed before barrier or with native ownership disabled; operator ack released a claim while its child permit remained active; pure pre-spawn native denial stranded a fresh claim.
- GREEN: `.venv/Scripts/python.exe -m pytest -o addopts= -q tests/test_shared_analyzer_capacity.py tests/test_parallel_workflow_resources.py tests/test_job_worker.py tests/test_executor.py tests/test_terminal_platform.py --tb=line --basetemp=.pytest-tmp-g5-owned-children-final4-20261018` =>65 passed,2 skipped in28.94s,exit0; Ruff/mypy seven affected Python files passed (then import-order-only lint correction passed).
- Native ownership is mandatory under any managed resource capability; default native settings remain usable, disabled/unavailable ownership denies before effects. Raw unmanaged executor fallback is preserved. Workflow children additionally need may_dispatch. Real subprocess fixture quarantines capacity, confirms owned exit, reclaims matching PID/create-time, and explicitly resolves parent evidence; pending/live child blocks operator ack. Fresh native denial before activation releases only its newly held claim.
- Original workspace preserved; panel and uv.lock hashes unchanged. Next G5 acceptance: fair older writer, Git common-dir, storage-failure no-dispatch, alias revalidation and measured 200-request pressure plus final C5/CSTATIC. Then P6.

### C5 command reconciliation — 2026-10-18

The proposed tests/test_parallel_admission.py was not created: its real capacity/admission cases are implemented in tests/test_shared_analyzer_capacity.py and tests/test_parallel_workflow_resources.py. C5 now names those existing files explicitly; this is reuse, not a silently skipped check. P6 proposed recovery test remains to be created in P6-T01.

### V-P5-PRESSURE-AND-ORPHAN-20261018

- Finite pressure: 200 multi-key requests completed in21.482s, p95admission2939.755ms/max5439.793ms, RSS+573440bytes; all200released. Four real verification jobs using Ruff/mypy/pytest all succeeded in15.265s, observed jobpeak4/analyzerpeak3, resources4released/capacity12released, no unresolved rows. Artifact TEMP/computerpilot-g5-measure-5c7611f.json. First benchmark fixture failed only Ruff I001 (one missing blank line), fixed the disposable fixture before repeat; no product behavior weakened.
- Measurement correction: retained pool/diagnostic connection inflated cold handle delta16. After closing/deleting/GC/settling, four40-request warm waves had handle deltas1,0,0,0, own open files0, RSS deltas1802240,98304,0,4096bytes. Three200-request waves all600released; DB458752bytes. No sustained warm handle growth observed; these are host-specific measurements, not an SLA.
- CSTATIC all passed: Ruff, mypy269, compileall, diff check; startup151 and all five health smoke checks passed. Full pytest was interrupted (exit15) after1218 reports: deliberate safe-refactor crash fixture left one active default-state claim, causing two downstream LSP timeout failures and blocking native command admission. This full run is NOT a pass; final rerun required after fixes.
- Root fixture cleanup only: native-resource-11ea8e80b6c34aa1910a77f698baa059 quarantined then explicitly resolved; native-resource-7acd04cc6f8445dead27a77e3422f905 pending released. Original owner PIDs22704/26504 with recorded creation times absent; controlled target equals value2; validation was only sleep under native ownership. Native mutation journal stays uncertain; no history deleted/replay. Root unresolved resources0.
- RED: stale native capability survived owner death/reuse/denied inspection/zombie. GREEN: ownership/recovery/resources/capacity/job suite88passed1skipped, compatibility12passed1skipped; final native ownership/child/alias/common-dir/fairness cases10passed; Ruff/mypy five files pass. Tests: .pytest-tmp-native-orphan-green-20261018, .pytest-tmp-native-orphan-compat-20261018, .pytest-tmp-native-owner-final-20261018.
- Four final matrix cases passed: old writer fairness, Git common-dir shared across worktrees, real Windows junction swap denied before effect, claim storage denial rolls back request/claims and no intent/action dispatch.
- Codebase Memory0.12.1 retrieval works via fresh stdio; full indexing failed twice and fast indexing once with aborted_previous_preserved; cached4995nodes/25704edges retained. Current-source fallback is explicit for changed relationships. Graphify queries were scoped and implementation-critical results source-checked; no fresh-index claim.
- P5 still VERIFYING: audit Git hook/filter ownership footprint and finish final full/C5 gate. P6 remains not started; permission for read-only independent reviewer is still pending.


### V-P5-GIT-CALLBACKS-20261018

- RED: hook descendant survived mutation return; read/intent/reconcile/verification Git callbacks executed fsmonitor, signature verifier, external diff/textconv, and implicit promisor transport in disposable local repositories. Positive controls proved callbacks executed before policy. Windows loose-object fixture needed write permission before unlink.
- GREEN:20 Git callback/ownership cases passed; focused compatibility114passed,1skipped in41.31s, exit0 (.pytest-tmp-git-boundaries-final). Ruff/mypy nine affected Python files and diff check passed. Shared policy disables automatic callbacks/transport; explicit mutation hooks run under exclusive coordination and owned-tree cleanup.
- Original workspace; user panel/uv.lock excluded. Next: final full G5 gate on committed code, then P6 all-branch recovery.


### J-P6-RECOVERY-20261018

- P6-T01/T03/T04 IN_PROGRESS with public activation closed. Four RED recovery cases plus three RED atomic-resume blockers reproduced. Shared Store aggregate now scans all branches; reserved/prepared no-child attempts can pause safely, may_dispatch uncertainty and admissions persist, terminal projections repair without replay, one-branch resolution preserves siblings. Focused27passed. Final G5 baseline e338bee suite still running; no G5/G6 GO asserted.


### V-P6-CORE-CHECKPOINT-20261018

- RED: four all-branch recovery/projection/resolution failures, three atomic queue blockers, never-policy replay, missing DAG transient retry, intent deadline dispatch, unbounded file contains, and job cancellation/resource-wait spawn race.
- GREEN:96passed,1skipped in19.00s (.pytest-tmp-p6-core-checkpoint); final deadline caller suite63passed in8.06s (.pytest-tmp-p6-deadline-callers); job migration/barrier56passed,1skipped. Ruff/mypy270/compileall/diff check passed after test formatting fixes. Real owned executor-process termination preserved two effect markers and three uncertain admissions; fan-out cancel preserved success and blocked join.
- Broad regression1493passed,23skipped in323.37s (TEMP/computerpilot-g5-e338bee.xml). Started at e338bee; P6 files changed while it ran, so it is broad diagnostic evidence, not a final immutable-diff gate. Final full suite remains required.
- Job schema3 verified before additive4 dispatch-phase migration; old history remains legacy. Public DAG remains closed. Remaining: exhaustive restart/child/CAS/security/rollback matrix, independent review, final local/platform/hosted same-head gates.


### V-P6-RESOLUTION-AND-PRIVACY-20261018

- RED: final explicit resource resolution left a fully acknowledged DAG permanently uncertain; generic parallel exception payload leaked into operation/step/aggregate durable evidence. Both fixed at existing Store/executor boundaries.
- GREEN:84resource/capacity/reconciliation cases in40.39s;22retention/recovery cases,1skip in5.17s;41CAS/migration/retention cases,1skip in6.90s. Concurrent two-constructor recovery publishes one event and preserves success/failure/cancel/uncertain precedence. Managed terminal-job retention race cannot recreate the command. Exception canary absent from SQLite dump and public result.
- Stronger real-death fixture now runs the actual WorkflowExecutor in an owned subprocess with three active branches, two durable effects, then kills it and verifies three uncertain admissions and no replay (1passed in1.25s). Earlier manual protocol fixture is superseded by this actual executor evidence.
- Latest checkpoint88c55ab pushed; focused final recovery/retention/CAS and full static acceptance run before next checkpoint. Remaining immutable full, local platform/Doctor, packaging/rollback, hosted gates and independent review. Activation stays closed.


### V-P6-FULL-AND-HOSTED-FIX-20261018

- Immutable b5919d5 local full:1514passed,23skipped in329.24s; JUnit1537tests/0failures/0errors/23skip. Packaging all5targets twice byte-identical, independent hashes/manifests pinned b5919d5 verified (TEMP/computerpilot-pack-b5919d5-p7193s92). Rollback drill from d721aa8 old implementation upgraded history, rejected downgrade, restored pre-upgrade backups and reopened old implementation with history intact (TEMP/computerpilot-rollback-b5919d5-1qja3xvc).
- Controlled local HTTP measurement: three paired samples, median cap1=.750s/cap2=.625s; cap2 observed peak1or2 and one sample slower than serial. These small workloads include SQLite/setup overhead; no production throughput claim. TEMP/computerpilot-p6-http-titrgndc/report.json.
- Local platform validation passed: clean launcher8.500s, Full Doctor5.688s, quick benchmark40.312s,60-workflow/2restart/3durable-job soak13.485s, zero leaked leases/active jobs/orphan operations/events. Root-handle edit overlapped the platform run, so final same-diff platform gate will rerun.
- Hosted b5919d5: six checks succeeded, four CI failed. Python3.10 migration fixture set_authorizer(None) unsupported (official Python docs: disabling with None added3.11); use closed fault-injection connection and fresh inspection. Windows native nested-job test exposed missing root-handle exit wait after accounting0. Two portable RED cases proved false successful cleanup; root wait now shares5s deadline and fails closed.
- GREEN focused71passed,1skip; Ruff/mypy three affected files. Six real pytest subprocesses under a proven outer Job Object all passed inner parent/grandchild immediate-exit assertions. Final full/platform/hosted gates required for new head.
- Human approved one read-only independent reviewer; agent01a14d37-64a6-7740-acad-ba1013ef1975 is reviewing PR81, no write authority.

### J-P6-INDEPENDENT-REVIEW-FIXES-20261018

- Reviewer independently reproduced three blockers at95c7eae: PAR-014/P1 terminal cleanup error released ownership; PAR-015/P2 normal root exit left dead-child analyzer permits; PAR-016/P2 a retry intent failure could not settle FAILED->FAILED behind its new reserved barrier. Four own RED cases confirmed all three, including cancel during retry preparation.
- Shared fixes in progress: default cleanup-uncertainty quarantine for all managed writers, positive dead-owner reclaim scoped by parent capability after native cleanup (live/uninspectable owners retained), and fenced pre-dispatch retry failure/cancel settlement. Extra inherited-storage RED proved a failed quarantine could replace cleanup uncertainty and release an outer default claim; bounded typed propagation now preserves that outer fence.
- First expanded run used a misplaced local helper and failed NameError cases; helper scope corrected, fresh verification required. No success claimed from that run. Tests and source remain local until current checks pass. Agent requested to independently recheck fixes.
- Public activation remains closed. Temp reviewer scratch ownership-review-k4j_vf6h retained after automatic approval policy denied removal (blocked by policy); no cleanup retry/bypass.


### V-P6-INDEPENDENT-FIX-CHECKPOINT-20261018

- Current expanded GREEN115passed,2skipped in42.58s (.pytest-tmp-review-fixes-current-final), all original reviewer reproductions included, plus live/denied/reused/zombie/unknown permit-owner cases and inherited failed-quarantine canary. Ruff/mypy270/compileall/diff-check passed. Misplaced-helper intermediate run9failed was corrected and superseded by this fresh run; no failed result counted as success.
- PAR-014/015/016 fixed in source; reviewer recheck pending. Default/inherited cleanup always fences uncertainty, storage failure remains bounded and cannot release outer ownership, normal successful root cleanup reclaims only positively dead bound owners, and failed retry pre-dispatch settlement/cancel completes atomically.
- Hosted95c7eae all10checks SUCCESS, proving Python3.10 and nested Windows root-wait repairs on actual Windows/Linux/macOS.95c7eae broad local1516passed/23skip in323.67s; review edits overlapped this run, so final post-review immutable full gate remains required.


### J-P6-POLICY-COMPATIBILITY-20261018

- PAR-017/HIGH: section238 requires incompatible budget/policy fail-closed. Two real-process independent/read-read cases admitted with analyzer budgets1 vs3. Extended12 real-process cases RED then GREEN; inherited scope/process/capacity denied before marker/permit, and FIFO profile-switch drain prevents starvation.
- Schema11 uses one fingerprint column on existing resource requests plus SQLite activation/insert triggers, no registry. Legacy active claims quarantine; undispatched pending release; all capacity/history retained. CREATE_TRIGGER denial rollback leaves schema10 and active owners intact; reopen upgrades.
- Fresh focused123passed,1skipped in42.45s (.pytest-tmp-policy11-resumed); Ruff/mypy3 passed. A fake-settings fixture was corrected to replace real Settings, preserving real budget fields. Public activation stays closed.
- Independent reviewer18targeted cases closes PAR-014/015/016 atb8be8c4. Hosted b8be8c4 Windows3.10/3.12 still fail successful-root descendant permit test; other8checks succeed. PAR-015 reopened for hosted follow-up. No failed gate counted as pass.


### V-P6-WINDOWS-EXIT-AND-SUCCESS-20261018

- Reviewer rechecked293216a: policy delta18independent tests plus direct triggers pass, no new policy finding. PAR-015 remains open: single live-PID sample plus success-before-finalization independently reproduces succeeded with active permit and quarantined parent.
- Native Windows probe pins a SYNCHRONIZE handle, rechecks creation identity and reads signaled termination. Live/denied/wait-failed/close-failed retain ownership. Real Popen integration checks live then exited with its handle retained.
- Shared managed boundary now raises bounded cleanup uncertainty if fresh ownership cannot release; worker closes context before publishing terminal status. RED regression proved silent successful exit with unresolved child; new worker case proves failed status with fences intact. Four existing quarantine fixtures now explicitly expect propagated cleanup failure rather than suppressing it.
- GREEN178passed,1skipped in47.66s, then final touched-fixtures10passed in2.98s; Ruff/mypy270 passed after line-length/type-narrowing fixture correction. Prior incomplete intermediate probe run2failed/115passed superseded; missing import and positive NoSuchProcess probe handling corrected. Final immutable full/hosted and independent recheck required.


### J-P6-OPERATOR-GATE-20261018

- P6-T06 IN_PROGRESS: construction-only DAG gate made production MCP/worker execution unreachable. Add one default-off Settings field/env interface reused centrally by WorkflowExecutor; no new scheduler or public bypass argument. Preserve plan/start/status/dry-run, sequential behavior and cap1. Activation environment remains unchanged.
- Two MCP-client tests RED for missing setting, then actual worker on/off subprocess cases. First worker fixture incorrectly assumed MCP_STATE_DIR exists; replaced config before importing worker in the isolated subprocess. Read-only audit of default DB confirmed no workflows updated since2026-09-28; no user workflow dispatched. Wrong health key replaced with existing active_admissions API.
- Final same-head gates, independent review, rollback and operator fixture execution required before GO.


### V-P6-PORTABLE-NATIVE-PROBE-20261018

-43eb744 four hosted POSIX CI jobs failed mypy (Windows-only _winapi attributes). Reviewer independently reproduced six errors with mypy --platform linux --no-incremental. No behavior/config finding; four public MCP/worker on/off tests and explicit overrides passed independently.
- Add explicit sys.platform win32 boundary before native import, matching actual supported backend. Portable native-unit fixtures simulate that module platform without changing the process-wide platform. GREEN mypy linux+darwin no-incremental,53passed/1skip native/worker tests and Ruff.
-43eb744 rollback drill preserved one old workflow and one job, upgraded schema11/4, old implementation rejected newer schemas, pre-upgrade connection backups restored and old readers reopened unchanged history. Artifact TEMP/computerpilot-rollback-43eb744-p00r2y5k. Initial drill harness assumed old recover keyword and typed job error; corrected to actual old contracts; no production data touched.
-43eb744 full still running during portability edit; this run is diagnostic, final immutable head verification required. Public runtime setting remains disabled.


### V-P6-FINAL-ACCEPTANCE-IN-PROGRESS-20261018

- Independent reviewer01a14d37 PASS at01b10b9: no remaining scoped findings; PAR-014/015/016 can close, schema11 policy and operator configuration evidence retained. Reviewer independently executed ten final boundary cases and Linux/Darwin mypy; broader full/hosted results remain implementer responsibility.
-01b10b9 full immutable started with environment extensions0/DAG0, log TEMP/computerpilot-final-01b10b9.log and JUnit.xml. Prior43eb744 run1552passed/23skip376.04s overlapped portable guard edit, diagnostic only.
- Seeded event-controlled completion proof: seeds0..49,49seven-node DAGs plus one100nodewide DAG,443nodes total, peak3exactly, no lost result/admission/claim/uncertainty.34.391s total,7.969s max; timings are diagnostic under simultaneous checks, not performance claims. Artifact TEMP/computerpilot-seeded-01b10b9-x73ft6j4/verified.json, reproducible TEMP/computerpilot-seeded-dag-proof.py. Events control actual completion order; no random sleeps.
- All5package targets built twice byte-identical; independent SHA256 and each manifest source_commit01b10b9 verified. Artifact TEMP/computerpilot-pack-01b10b9-e8jtk98g/verified.json. No tag/release published; package is not proof of every native architecture.
- Rollback drill actual old d721aa8 source from previous isolated core/tools archive: one workflow/job history retained through upgrade11/4, old readers reject newer schema, pre-upgrade SQLite backup restored and old readers reopen identical rows. Artifact TEMP/computerpilot-rollback-01b10b9-pj71zt_5/verified.json. No real state used.
- Ruff/mypy270/compileall/startup151unique/health ok; Linux and Darwin full-tree mypy270 passed. Hosted currently8success/2pending, no bypass. Local final platform/FullDoctor and idle paired HTTP measurement follow immutable full. User panel/uv.lock SHA256 still unchanged and excluded from every commit.


### J-P6-HOSTED-EXIT-OPEN-RACE-20261018

- Immutable01b10b9 local full1552passed/23skip342.52s, JUnit1575zero errors/failures; hosted Windows3.10/3.12 still fail the same descendant test, now correctly failed rather than false success. Other8gates green. PAR-015 remains open; exact hosted cause unverified.61ea78e diagnostic-only test message pushed to expose bounded stored error.
- Five local runs under actual outer Job Object all pass (TEMP/computerpilot-outer-reclaim-vy97fn8g). Reviewer independently confirmed a real process-disappearance race: OpenProcess WinError87 then positive psutil NoSuchProcess, while current probe returnedNone. This is an independently confirmed candidate path, not proof of hosted cause.
- Own portable race RED proves None vspositive death. OpenProcess denial now rechecks identity/absence; only positive disappearance or changed birth releases. Matching live and metadata-denied remain unknown. GREEN54passed/1skip then46passed/1skip including denial; Ruff format corrected, mypyWindows/Linux/Darwin pass.
- Local platform61ea78e pass: launcher6.406s,FullDoctor5.562s,benchmark35.969s,60workflows+2restarts+3durablejobs13.031s; leaked leases/orphans/activejobs0. Exit-probe edit overlapped final part, so final new-head platform remains required. No activation; no gate bypass.


### V-P6-EXIT-OPEN-CANDIDATE-20261018

-1eb9760 independently verified by reviewer: real WinError87 reproduction now returnsTrue,11targeted tests and four explicit OpenProcess-failure identity checks pass. No finding in candidate patch; exact earlier hosted cause still UNVERIFIED. PAR-015 closure NOT granted until hosted Windows evidence.
- All source static Win270 passed at1eb9760; final immutable full running (TEMP/computerpilot-final-1eb9760.log/.xml), current10hosted gates pending.Two new native probe cases are included; final totals will be recorded only from actual output.
-1eb9760 rollback11/4 passed with one preserved workflow/job, old reader reject and exact backup restore (TEMP/computerpilot-rollback-1eb9760-97odbkcj/verified.json). Packages being independently rebuilt twice for exact source_commit. No release/tag/main merge/activation yet.


### V-P6-IMMUTABLE-FULL-1eb9760-20261018

- Immutable1eb97602aa76d1c60412e30f6c19ac6a561a570c full:1554passed,23skipped357.86s, exit0. JUnit1577tests/0failures/0errors/23skip, TEMP/computerpilot-final-1eb9760.xml. No relevant source edits during run.
- Final same-head5targets built twice byte-identical with independent checksums/manifests: TEMP/computerpilot-pack-1eb9760-0z6qochb/verified.json. Rollback11/4 samehead: TEMP/computerpilot-rollback-1eb9760-97odbkcj/verified.json.
-61ea78e diagnostic hosted Windows both confirm error Managed resource ownership remains unresolved.; eight other checks green. This confirms retained child fence, not exact probe-stage cause.1eb9760 currently8success/2Windows pending; PAR015 stays open. Local full Doctor/platform samehead in progress.
- User panel/uv.lock preserved at original hashes, only unrelated dirty paths. No force/reset/clean, release publication, main merge or runtime enable performed.


### V-P6-FINAL-GATES-81d9f42-20261018

- Source branch `fix/parallel-resource-ownership`, exact HEAD `81d9f423ade550f75f02147e2716314498bcbcbc`, PR #81; base `origin/main`. Foreground verification parameter, parallel diagnostics, typed/durable DAG foundation, bounded DAG scheduler, coordinated resource admission, and cancellation/recovery code present. Public DAG remains default-off (`MCP_WORKFLOW_DAG_ENABLED=false`). No release/tag or main merge during this checkpoint.
- Current immutable local full suite: `.venv/Scripts/python.exe -m pytest -o addopts= -q --tb=short --basetemp=.pytest-tmp-parallel-final-v2` with `MCP_EXTENSIONS_ENABLED=0`: **1555 passed, 23 skipped, 0 failed, exit 0, 348.27 s**. First concurrent earlier full attempt was interrupted; it is NOT acceptance evidence. Focused boundary tests (Windows positive child exit and cleanup, DAG join barrier, backup isolated count, diagnostics deadline) 4/4 PASS.
- Static checks exact HEAD: Ruff full tree exit0; mypy `core tools scripts tests` exit0, 270 files; compileall core/tools/scripts/tests exit0; git diff --check exit0. `main.py --check` exit0, tool_count151 and unique tools. Full Doctor `python -m scripts.doctor --mode local-http` exit0; dependency imports, pip check, browser imports, MCP filesystem/terminal, local doctor all PASS. Standalone `health_check --json` first attempt exit1 ExceptionGroup after64s; subsequent `scripts.health_check.run_checks()` exit0 with all import/startup/registration/filesystem/terminal checks PASS and no unresolved operations/claims; **intermittency remains recorded**, not silently erased.
- Hosted **10/10 same SHA**: GitHub CI run39119215151 6/6 success (Windows/Linux/macOS x Python3.10/3.12); Platform Release Validation39119215157 3/3 success; Release Packaging Smoke39119215182 success. No bypass, ignored/stale statuses are not PASS. Previous Windows ownership CI failure PAR-015 no longer reproduces on current hosted Windows3.10/3.12, macOS scheduler overlap/barrier and diagnostics deadlines pass, backup retention isolated counts pass.
- Commits since earlier 43e3d92: 5e96f3a (DAG test barrier), 602ae8f (delayed Windows death RED test), 6fa7e45 (bounded positive death confirmation), 4a18a37 (Windows-only settlement grace); b2ab242 (cleanup hard deadline guard), 705bb83 (backup scan isolation), 81d9f42 (diagnostics pre-admission expiry). All pushed, original local branch fast-forwarded, user panel SHA256 `6F682BAEBF08556A4B5BAB2D9D9E34F541CCF6EBBEE13ABDA611A3C9B67BD3D4` and untracked uv.lock SHA256 `E9BBEE1693A12EBDDE82D62EF98722036E5B14DB754842BD6C865375594E56AA` unchanged and excluded from staging.
- Existing independent review of 01b10b9 and seeded event-controlled 443-node stress / schema11/4 rollback and deterministic 5-target packaging recorded in earlier sections remain evidence of **their original SHA**, not automatically proof for 81d9f42. A fresh Codex read-only review against origin/main is in progress for the current head. Do not mark entire P6 release gate DONE without review result and any required main-SHA postmerge checks.


### V-P6-REVIEW-REPAIR-6dcd3e1-20261018

- Independent Codex read-only review of `81d9f42` vs `origin/main` completed with **6 reproducible blocking findings (P1 x2, P2 x4)**. Acceptance explicitly reopened; green CI on old SHA was not used to override review.
- **P1 job spawn:** a known `FileNotFoundError` while spawning before child creation no longer quarantines a fresh exclusive claim. RED test `test_pre_spawn_missing_executable_releases_exclusive_job_claim` reproduced permanent quarantine; GREEN and full `tests/test_job_worker.py` **30 passed, 1 skipped**. Other unknown/active cleanup still quarantines.
- **P1 sequential verification:** `_verify_changes` now obtains authoritative coarse repository write + coordination claims even outside DAG before analyzers start; inherited DAG capability remains validated. RED conflict test followed by `tests/test_parallel_workflow_resources.py` **69 passed** (80.00s).
- **P2 idle aggregate:** unchanged DAG aggregate no longer increments version or emits polling events. New RED assertion saw 7 events instead of 1 after 6 idle recomputes; GREEN `tests/test_parallel_workflow_scheduler.py` **11 passed**.
- **P2 explicit DAG retry:** safely resumable failures move from `failed` to `created` atomically at an explicitly authorized failed/paused -> queued transition, preserving immutable event history/attempt counter. Earlier RED resumed `check_file` remained failed; GREEN `tests/test_parallel_workflow_recovery.py` **22 passed**. Existing `_require_safe_resume` denies unresolved resource/child ownership and unproven effectful replay.
- **P2 durable job identity:** immutable step-defined `durable_job_timeout_sec` carried in `ActionContext` becomes the submitted job timeout for fingerprint, while dynamically computed remaining budget bounds the caller's wait. RED constructor/test reproduced missing stable context, GREEN `tests/test_workflow_cancellation.py` **5 passed**.
- **P2 pre-dispatch footprint:** `fail_unadmitted_operation` atomically records a known preflight failure (operation/step/event) under current lease, without claim/dispatch. RED reproduced invalid Git repository throwing out of DAG coordinator; GREEN `tests/test_parallel_workflow_scheduler.py` **11 passed**.
- Focused combined workflow+job+resource regression: **148 passed, 1 skipped, 70.48s**, zero failures. Current checkout static: Ruff all passed, mypy **270** source files no issues, compileall exit0, startup/catalog **151 unique tools**. `git diff --check` passed. Reviewer-generated temporary pytest directories removed **only** by exact path; user panel and uv.lock untouched.
- Focused Conventional Commits: `6436493` job pre-spawn; `6677f37` sequential verification; `a2ec330` durable job identity; `b665d67` preflight/polling; `6dcd3e1` explicit DAG resume. All pushed to PR #81 at exact HEAD `6dcd3e1cb1391ae2252d4bd4584c024019bb8d26`.
- **Pending:** full immutable local pytest on 6dcd3e1 currently running (background PID26844); current same-head three hosted workflow families in flight (CI6, platform3, package1), and independent second Codex read-only review of patch `81d9f42..6dcd3e1` PID17816 underway. Do not merge/enable default DAG or claim final GO until all finish. Release publication not authorized.


### V-P6-SECOND-REVIEW-CORRECTIONS-20261018

User authorization: **fix remaining defects and MERGE PR #81 when independently verified, but DO NOT bump version, create tag, publish release, or enable public DAG by default.** Actual version remains `0.8.0`; source branch `fix/parallel-resource-ownership`.

Independent Codex delta review at `6dcd3e1` reproduced **two additional P2 defects**, despite local `1561 passed/23 skipped` and `10/10` hosted checks on that historical tip:

1. **Confirmed zero dispatch was not sufficient for safe resume.** Atomic `fail_unadmitted_operation` now persists bounded `{"dispatch_phase":"preflight_rejected","no_dispatch":true}` as durable evidence in the same SQLite transaction as failed operation/step/event. Resume guard recognizes this only for failed attempts with no unresolved resource/admission or external child reference; effectful attempts whose dispatch is uncertain remain blocked. Reproduction: `git_stage` before Git initialization failed safely; after `git init` and adding file, explicitly requeued/resumed mutating step safely.
2. **Sequential `verify_changes` budget ignored resource admission latency.** One monotonic deadline now starts before admission; effective analyzer timeout is clipped to the *remaining* seconds after claim acquisition, with fail-closed timeout if depleted. Original test held read claim for 150ms and saw unchanged 0.8s analyzer budget; patched same test receives a bounded remaining budget.

Both tests were RED before fixes and GREEN after fixes, without weakening no-blind-replay rules. `tests/test_parallel_workflow_scheduler.py`, `tests/test_parallel_workflow_recovery.py`, `tests/test_parallel_workflow_resources.py`, `tests/test_workflow_cancellation.py`, `tests/test_workflow_migrations.py`, and `tests/test_job_worker.py`: **152 passed, 1 skipped**. Ruff clean, mypy checked **270 files** clean, `git diff --check` clean. New isolated commits `a23bc5c` (atomic no-dispatch evidence) and `bc2242a` (verification deadline), following five earlier review-fix commits. **All end-to-end acceptance must be repeated on the eventual final PR tip; the 10/10 on 6dcd3e1 is historic, not final evidence.**

This canonical master plan is currently Git-ignored by the broad `docs/roadmap/` rule; to preserve it in clones without tracking unrelated drafts, use an explicit `git add -f -- <exact file>`, and verify staged files before commit. The phase/task dashboard's historic row timestamps and historical NOT_RUN fields are not proof of current incompleteness; maintain factual verification pointers and GO ledger without inventing per-task execution times.
