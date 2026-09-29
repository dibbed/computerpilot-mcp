# گزارش کامل Backend Audit + Windows Integration

تاریخ: 2026-09-29  
پروژه: ComputerPilot MCP  
مسیر محلی:

```text
C:\Users\Meliodas\Downloads\tunnel-client-v0.0.11-windows-amd64
```

Branch نهایی:

```text
fix/backend-deep-audit
```

Base اصلی audit:

```text
11ef2bb50a16d4a96b69307a47333acbc460f05b
```

HEAD قبل از ثبت همین سند:

```text
2a10e646872b7ad488cfac1a6f6230b6180e0daa
```

> این فایل handoff تمام کارهایی را که برای ادغام backend audit، حفظ تغییرات محلی PC، بررسی Windows واقعی، رفع regressionها و validation نهایی انجام شد ثبت می‌کند.

---

## 1. هدف کار

هدف این مرحله این بود که تغییرات backend audit تولیدشده در محیط جداگانه، بدون از بین رفتن تغییرات جدیدتر روی PC، وارد branch محلی شوند.

اصول رعایت‌شده:

- `main` تغییر نکند.
- هیچ push یا merge به remote انجام نشود.
- تغییرات قبلی BrowserManager حفظ شوند.
- تغییر `same_process(psutil.Error)` حفظ شود.
- audit bundle کورکورانه overwrite نشود.
- همه commitهای audit ابتدا جدا وارد شوند.
- ادغام روی branch واسط انجام شود.
- تست واقعی Windows قبل از پذیرش نهایی اجرا شود.
- باگ‌های Windows فقط بعد از reproduce واقعی تغییر داده شوند.
- working tree در پایان clean باشد.

---

## 2. وضعیت محلی قبل از integration

Branch فعال اولیه:

```text
fix/backend-deep-audit
```

چهار فایل محلی تغییر داشتند:

```text
tools/browser/manager.py
tests/test_browser_hardening.py
core/jobs.py
tests/test_jobs.py
```

این تغییرات مربوط به دو fix قبلی بودند.

### 2.1 Browser stale-session capacity bug

مشکل:

- browser disconnect می‌کرد.
- session مربوطه stale می‌شد.
- session stale همچنان در `_sessions` باقی می‌ماند.
- session مرده quota را مصرف می‌کرد.
- با `max_sessions=1`، باز کردن session جدید ممکن بود `browser_session_limit` بدهد.

تغییرات:

- cleanup loop تا وقتی pool یا session وجود دارد ادامه پیدا می‌کند.
- session stale/unusable بدون انتظار برای idle timeout reclaim می‌شود.
- قبل از رد کردن درخواست به‌دلیل capacity، stale cleanup اجرا می‌شود.

Regression test:

```text
tests/test_browser_hardening.py
test_disconnected_stale_session_does_not_consume_session_budget
```

Validation:

```text
31 passed
```

Commit:

```text
3686303 fix(browser): reclaim stale sessions after disconnect
```

### 2.2 Job process identity / psutil.AccessDenied

مشکل:

`core/jobs.py -> same_process()` فقط `psutil.NoSuchProcess` را catch می‌کرد.

اگر `psutil.AccessDenied` یا error دیگری از خانواده `psutil.Error` رخ می‌داد، job reconciliation ممکن بود exception بدهد.

Fix:

```python
except psutil.Error:
    return False
```

این رفتار عمداً fail-closed است.

Regression test:

```text
tests/test_jobs.py
test_same_process_fails_closed_when_process_metadata_is_inaccessible
```

Validation پیرامونی:

```text
47 passed
```

Commit:

```text
c8d144a fix(jobs): fail closed on inaccessible process metadata
```

بعد از این دو commit، working tree clean شد.

---

## 3. دریافت و بررسی Git bundle

Bundle:

```text
C:\Users\Meliodas\Downloads\computerpilot-backend-audit-20260929.bundle
```

Bundle با `git bundle verify` بررسی شد.

Branch داخل bundle:

```text
audit/backend-correctness-performance-20260928
```

Base:

```text
11ef2bb50a16d4a96b69307a47333acbc460f05b
```

تعداد commitهای audit bundle:

```text
44 commits
```

Branch bundle به repo محلی import شد و هیچ push انجام نشد.

---

## 4. Integration branch

Branch واسط:

```text
integrate/backend-audit-20260929
```

از:

```text
fix/backend-deep-audit
```

ساخته شد.

سپس هر 44 commit audit به ترتیب cherry-pick شدند.

نتیجه:

- 44 commit بدون conflict وارد شدند.
- BrowserManager fix حفظ شد.
- `same_process(psutil.Error)` حفظ شد.
- تغییرات محلی PC overwrite نشدند.

Overlap مهم قبل از integration:

```text
core/jobs.py
tests/test_jobs.py
```

با وجود overlap، cherry-pick بدون conflict انجام شد.

---

## 5. bug fixهای اصلی bundle

### Worker launch / psutil metadata

```text
f52f8a3 fix: retain launched worker when psutil metadata is unavailable
```

از failed شدن اشتباه workerی که واقعاً با Popen اجرا شده بود جلوگیری می‌کند.

### Workflowهای بیشتر از page اول

```text
e095c89 fix: execute and retrieve workflows beyond first operation page
```

### Crash gap قبل از action checkpoint

```text
1764584 fix: pause workflows crashed before action checkpoint
```

### PID reuse در orphan cancellation

```text
cb5af62 fix: revalidate orphan process identity inside termination
```

### Concurrent workflow migrations

```text
e6f4c2f fix: serialize concurrent workflow schema migrations
```

### Same-millisecond workflow ordering

```text
dca950f fix: preserve workflow insertion order for same-millisecond queue entries
```

### Validation قبل از execution/reconciliation

```text
ba02280 fix: validate materialized definitions before execution or reconciliation
```

### Truncated search snapshot continuation

```text
4248a9c fix: reject truncated search snapshot continuation
```

### Windows Job Object در زمان psutil inspection failure

```text
d2af98d fix: terminate Windows Job Object despite psutil inspection failure
```

### Background PID reuse output-capture leak

```text
e5eb3a5 fix: release background captures when a process ID is reused
```

### Resource health directory symlink cycle

```text
ce0207d fix: avoid directory symlink cycles in resource health scans
```

### Workflow retention lease race

```text
8336481 fix: recheck active workflow lease during retention delete
```

### Benchmark scratch lifecycle

```text
946221b fix: protect active benchmark scratch and recover staged cleanup
```

### Expired search snapshot cleanup

```text
7d38ca8 fix: sweep expired search snapshots during runtime maintenance
```

### Workflow WAL startup lock

```text
42ce232 fix: retry transient workflow WAL setup lock and close failed connections
```

### State path / symlink / reparse protection

```text
e9f78f0 fix: avoid following linked search state during cleanup
5161317 fix: keep backup retention inside unlinked state directory
29cd692 fix: keep artifact retention inside unlinked state directory
ec5ec56 fix: reject linked ancestors during state garbage collection
a4d0c72 fix: keep job history cleanup within unlinked state
59d75aa fix: keep workflow history cleanup within unlinked state
```

### PID metadata بعد از child exit

```text
d1f177e fix: discard worker PID metadata after original child exits
6cc79b9 fix: discard command PID metadata after original child exits
```

### POSIX durable command process-group ownership

```text
d239869 fix: give POSIX durable job commands process-group ownership
```

### Benchmark report correctness

```text
1fa8290 fix: publish unique benchmark reports atomically
a2fac4b fix: bound managed benchmark reports and reclaim crashed writes
```

### Async health collection

```text
d2739df perf: move synchronous health collection off the MCP event loop
507f68e fix: preserve serial health reads after thread offload
```

---

## 6. Performance optimizationهای اصلی

### Streaming postcondition hashes

```text
8f9fde3 perf: stream multi-file postcondition hashes
```

کاهش peak memory برای فایل‌های بزرگ.

### Job admission index

```text
2464ad0 perf: index job admission and queue ordering queries
```

کاهش scan و contention روی SQLite.

### حذف rereadهای غیرضروری operation

```text
aad05cd perf: omit unused operation rereads after executor checkpoints
f1e2043 perf: avoid unused operation page after integrity validation
```

### Job retention scans

```text
a0c4c14 perf: avoid redundant job history scans and orphan connections
```

### Resource health scan

```text
c1f11ee perf: collect job output health totals in one filesystem walk
050d4e1 perf: stat each resource health file once
```

### Workflow history incremental backfill

```text
6c19106 perf: backfill workflow history once and repair missing operations incrementally
```

### Artifact و backup stat reduction

```text
c366364 perf: stat artifacts once per retention scan
fa6a651 perf: skip duplicate stats and invalid timestamp parses in backup scans
```

### Configure WAL once per store startup

```text
28593cc perf: configure workflow WAL once per store startup
```

### Standalone lifecycle polling

```text
d38ce48 perf: skip supervisor control polling in standalone runtime
```

### Timing batch writes

```text
7c37fc3 perf: append fitting timing batches in one write
```

### Search snapshot maintenance

```text
1dcf385 perf: scan expired search snapshots once per maintenance sweep
8163376 perf: run snapshot maintenance off the MCP event loop
```

---

## 7. وضعیت .agent_state

Audit مشخص کرد که پاک کردن کامل `.agent_state` بعد از هر کار منطقی و امن نیست.

### باید durable بمانند

- `jobs.sqlite3`
- `workflows.sqlite3`
- SQLite WAL
- unresolved recovery evidence
- active PID ownership metadata
- active lock files
- configuration
- authentication/secrets
- selected tunnel metadata/binary
- backupهایی که هنوز داخل retention policy هستند

### قابل GC با policy

- expired search snapshots
- completed benchmark scratch
- crashed benchmark scratch وقتی inactivity قابل اثبات است
- managed benchmark reports پس از retention
- terminal job/workflow history طبق policy

### Benchmark report retention

```text
retention = 30 days
max_count = 100
max_bytes = 64 MiB
recent_grace = 24 hours
```

### عمداً auto-delete نشدند

- user-provided --output
- screenshotهای تحویلی به user
- secrets
- configuration
- active locks
- active PID metadata
- unresolved recovery evidence
- supervisor generation leftovers بدون اثبات امن owner inactivity

---

## 8. Windows validation واقعی

بعد از ورود bundle، تست‌ها روی Windows واقعی اجرا شدند.

### Symlink privilege

روی Windows بدون Developer Mode یا Admin، ساخت symlink با:

```text
WinError 1314
```

fail می‌کرد.

این failure مربوط به test environment بود، نه production code.

تست‌های عمومی link طوری تغییر کردند که اگر privilege موجود نبود skip شوند.

Commit:

```text
d6f8e73 test(windows): tolerate unavailable symlink privilege
```

### Junction واقعی Windows

برای اینکه reparse-point safety واقعاً verify شود، Junction واقعی ساخته شد.

نتیجه cleanup:

```text
removed 0
old_exists True
new_exists True
```

یعنی cleanup وارد target بیرونی نشد.

---

## 9. باگ واقعی Windows در timing rotation

در full-suite یک failure order-dependent دیده شد.

گاهی:

- آخرین record به‌جای index 59، indexهایی مثل 39، 35 یا 49 بود.
- فایل نامعمول زیر ایجاد می‌شد:

```text
timings.1.1.jsonl
```

### Root cause

`timing_file()` برای هر رکورد از:

```python
Path(...).resolve(strict=False)
```

استفاده می‌کرد.

همزمان writer thread:

```text
timings.jsonl -> timings.1.jsonl
```

را rename می‌کرد.

در Windows، race بین resolve و rename می‌توانست مسیر configured را موقتاً به نام rotated file resolve کند.

این رفتار با stress reproducer روی Windows اثبات شد.

### Regression test

```text
tests/test_timing_retention.py
test_configured_timing_path_does_not_resolve_rotating_output
```

تست قبل از fix fail شد.

### Fix

قبل:

```python
Path(configured).expanduser().resolve(strict=False)
```

بعد:

```python
Path(os.path.abspath(Path(configured).expanduser()))
```

این مسیر lexical absolute است و identity آن با rename target تغییر نمی‌کند.

همچنین annotation rotation handle برای mypy اصلاح شد.

Commit:

```text
195705f fix(timings): keep configured output stable during rotation
```

---

## 10. Supervisor watchdog flake

در یکی از full-suiteهای میانی:

```text
test_watchdog_recovers_hung_child
```

یک بار launch count برابر 3 شد، در حالی که expected برابر 2 بود.

Captured output:

```text
MCP restart in 0s; reason=watchdog_unhealthy
MCP restart in 0s; reason=watchdog_unhealthy
```

بررسی انجام‌شده:

- focused test پنج بار پشت سر هم اجرا شد و هر پنج بار پاس شد.
- مجموعه تست‌های اطراف supervisor اجرا شد.

نتیجه:

```text
51 passed
4 skipped
```

کد supervisor بررسی شد و heartbeat قبل از هر launch حذف می‌شود، بنابراین stale heartbeat file علت نبود.

احتمال باقی‌مانده load-sensitive Windows scheduling است، مخصوصاً چون تست `grace=0.15` دارد.

چون reproduction پایدار وجود نداشت، production code حدسی تغییر داده نشد.

Full-suite نهایی این تست را پاس کرد.

---

## 11. Ruff cleanup

Ruff بعد از integration چند مورد import-only پیدا کرد:

```text
scripts/perf_benchmark.py
tests/test_executor.py
tests/test_workflow_migrations.py
tests/test_workflow_retention.py
```

فقط import ordering / modern import source اصلاح شد و behavior تغییر نکرد.

Commit:

```text
2a10e64 style: normalize backend audit imports
```

---

## 12. Validation نهایی

### Browser

```text
31 passed
```

### Jobs

```text
47 passed
```

### Windows lifecycle focused suite

شامل Windows Job Object، job worker، job scheduler و supervisor:

```text
37 passed
1 skipped
0 failed
```

### Timing focused

```text
4 passed
1 skipped
0 failed
```

### Ruff

روی:

```text
core
tools
scripts
tests
main.py
local_pc_mcp.py
```

نتیجه:

```text
All checks passed!
0 violations
```

### mypy

```text
Success: no issues found in 107 source files
```

### compileall

```text
PASS
```

### main.py --check

با:

```text
.venv\Scripts\python.exe
```

نتیجه:

```json
{"ok":true,"server":"ali_windows_agent_mcp","version":"0.3.0","tool_count":112,"unique_tool_names":true}
```

### Health check

```text
PASS
tool_count = 112
queued_workflows = 0
running_workflows = 0
uncertain_workflows = 0
unresolved_operation_count = 0
active_lease_count = 0
```

### pip check / doctor

System Python دارای dependency conflictهای پروژه‌های دیگر بود و doctor سراسری به همین دلیل fail شد.

اما:

```text
.venv\Scripts\python.exe -m pip check
```

نتیجه:

```text
No broken requirements found.
```

Doctor با `.venv`:

```text
PASS dependency imports
PASS pip check
PASS MCP filesystem and terminal smoke
PASS browser imports
PASS full doctor
```

بنابراین interpreter معتبر پروژه همان `.venv` است.

### Full pytest

Command:

```text
.venv\Scripts\python.exe -m pytest -q --maxfail=20
```

Collected:

```text
751 tests
96 test files
```

نتیجه:

```text
729 passed
22 skipped
0 failed
0 errors
```

Exit code:

```text
0
```

### git diff --check

```text
PASS
```

### Working tree

```text
clean
```

---

## 13. Final branch integration

بعد از validation:

```text
integrate/backend-audit-20260929
```

با:

```text
git merge --ff-only integrate/backend-audit-20260929
```

روی:

```text
fix/backend-deep-audit
```

fast-forward شد.

هیچ merge commit اضافه ساخته نشد.

`main` تغییر نکرد.

remote تغییر نکرد.

push انجام نشد.

---

## 14. Commitهای Windows integration pass

```text
195705f fix(timings): keep configured output stable during rotation
d6f8e73 test(windows): tolerate unavailable symlink privilege
2a10e64 style: normalize backend audit imports
```

---

## 15. لیست کامل commitها از base تا HEAD قبل از این سند

تعداد:

```text
51 commits
```

```text
2a1e72d feat(panel): redesign local operations console
b9f0498 docs(changelog): record operations console redesign
3686303 fix(browser): reclaim stale sessions after disconnect
c8d144a fix(jobs): fail closed on inaccessible process metadata
f52f8a3 fix: retain launched worker when psutil metadata is unavailable
e095c89 fix: execute and retrieve workflows beyond first operation page
1764584 fix: pause workflows crashed before action checkpoint
cb5af62 fix: revalidate orphan process identity inside termination
e6f4c2f fix: serialize concurrent workflow schema migrations
8f9fde3 perf: stream multi-file postcondition hashes
2464ad0 perf: index job admission and queue ordering queries
aad05cd perf: omit unused operation rereads after executor checkpoints
a0c4c14 perf: avoid redundant job history scans and orphan connections
c1f11ee perf: collect job output health totals in one filesystem walk
6c19106 perf: backfill workflow history once and repair missing operations incrementally
dca950f fix: preserve workflow insertion order for same-millisecond queue entries
ba02280 fix: validate materialized definitions before execution or reconciliation
f1e2043 perf: avoid unused operation page after integrity validation
4248a9c fix: reject truncated search snapshot continuation
d2af98d fix: terminate Windows Job Object despite psutil inspection failure
c366364 perf: stat artifacts once per retention scan
fa6a651 perf: skip duplicate stats and invalid timestamp parses in backup scans
e5eb3a5 fix: release background captures when a process ID is reused
ce0207d fix: avoid directory symlink cycles in resource health scans
050d4e1 perf: stat each resource health file once
0470828 fix: bound timing diagnostics with locked rotation
8336481 fix: recheck active workflow lease during retention delete
946221b fix: protect active benchmark scratch and recover staged cleanup
7d38ca8 fix: sweep expired search snapshots during runtime maintenance
42ce232 fix: retry transient workflow WAL setup lock and close failed connections
28593cc perf: configure workflow WAL once per store startup
e9f78f0 fix: avoid following linked search state during cleanup
d38ce48 perf: skip supervisor control polling in standalone runtime
7c37fc3 perf: append fitting timing batches in one write
5161317 fix: keep backup retention inside unlinked state directory
29cd692 fix: keep artifact retention inside unlinked state directory
ec5ec56 fix: reject linked ancestors during state garbage collection
a4d0c72 fix: keep job history cleanup within unlinked state
59d75aa fix: keep workflow history cleanup within unlinked state
d1f177e fix: discard worker PID metadata after original child exits
d239869 fix: give POSIX durable job commands process-group ownership
6cc79b9 fix: discard command PID metadata after original child exits
1dcf385 perf: scan expired search snapshots once per maintenance sweep
8163376 perf: run snapshot maintenance off the MCP event loop
1fa8290 fix: publish unique benchmark reports atomically
a2fac4b fix: bound managed benchmark reports and reclaim crashed writes
d2739df perf: move synchronous health collection off the MCP event loop
507f68e fix: preserve serial health reads after thread offload
195705f fix(timings): keep configured output stable during rotation
d6f8e73 test(windows): tolerate unavailable symlink privilege
2a10e64 style: normalize backend audit imports
```

---

## 16. وضعیت نهایی قبل از ثبت این document

Branch:

```text
fix/backend-deep-audit
```

HEAD:

```text
2a10e646872b7ad488cfac1a6f6230b6180e0daa
```

Working tree:

```text
clean
```

Remote push:

```text
NOT PERFORMED
```

Main merge:

```text
NOT PERFORMED
```

---

## 17. مواردی که هنوز عمداً باز هستند

### Supervisor watchdog flake

یک failure load-sensitive یک بار دیده شد ولی پایدار reproduce نشد.

production change حدسی انجام نشد.

### Windows symlink tests

بدون Developer Mode/Admin بعضی symlink tests skip می‌شوند.

Reparse-point protection با Junction واقعی verify شد.

### System Python

Python سراسری Windows dependency conflicts دارد.

این مشکل متعلق به environment سراسری است و `.venv` پروژه clean است.

### Push / release

در این مرحله هیچ push، merge به main، tag یا release انجام نشده است.

---

## 18. مرحله بعد پیشنهادی

اگر قرار است این تغییرات منتشر شوند:

1. review سریع history
2. update CHANGELOG / release notes
3. تعیین version هدف
4. push branch
5. CI روی Windows/Linux
6. merge به main
7. tag
8. release
9. در صورت ساخت binary، artifact/provenance validation

تا این مرحله branch محلی validated و clean است.
