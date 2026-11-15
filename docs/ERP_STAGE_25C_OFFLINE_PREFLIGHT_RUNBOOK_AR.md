# ERP Global Pro — دليل Stage 25C لنسخة SQLite v8 وفحص Preflight

**الحالة:** إجراء محلي آمن؛ لا ينفذ ترحيلاً ولا يغيّر قاعدة التطبيق  
**قاعدة المصدر:** SQLite v8  
**وجهة الخطوة التالية:** PostgreSQL 16 مؤقتة (disposable) بعد مراجعة التقرير  
**المنع الثابت:** لا إنتاج، لا dual-write، ولا تفعيل `TENANT_DATA_SCOPING_ENABLED`

## 1. الغرض والحدود

يشرح هذا الدليل كيف تنشئ نسخة SQLite متسقة ومستقلة عن قاعدة التطبيق، وتتحقق من سلامتها، ثم تشغّل أداة:

`backend/scripts/control_plane_preflight.py`

الأداة تفتح المصدر بوضع القراءة فقط (`mode=ro` و`PRAGMA query_only=ON`) وتنتج تقريراً عن المخطط والعدادات والتعارضات المحتملة. هي **ليست أداة ترحيل**، ولا يثبت نجاحها وحده أن ERP جاهز لـPostgreSQL أو أن عزل الشركات مكتمل.

لا ترفع ملف SQLite أو ملف النسخ الاحتياطي أو ملفات WAL/SHM إلى GitHub أو ChatGPT. قد تحتوي هذه الملفات بيانات مالية وشخصية وبيانات اعتماد. احتفظ بها محلياً في مجلد خاص ومقيّد الصلاحيات.

## 2. شروط البدء

قبل النسخ:

1. تأكد من نسخة ERP والبيئة التي تنتمي إليها قاعدة البيانات.
2. اقرأ قيمة `ERP_DB_PATH` من الإعداد المحلي أو ملف البيئة **دون نسخ قيمة الأسرار أو نشر ملف البيئة**. القيمة الافتراضية في `.env.example` هي `backend/instance/erp.sqlite3`، لكن استخدم المسار الفعلي لنسختك.
3. أوقف خدمات الكتابة/العمال المجدولة، أو ضع التطبيق في وضع الصيانة وتأكد من عدم وجود عمليات كتابة. لا تنسخ ملف SQLite الحي بواسطة `Copy-Item` وحده، خصوصاً إذا كان WAL مفعّلاً.
4. تأكد أن لديك مساحة كافية لنسخة مستقلة وتقرير JSON. لا تكتب النسخة فوق ملف المصدر.
5. لا تغيّر `TENANT_DATA_SCOPING_ENABLED=False`؛ يجب أن يبقى الحاجز مغلقاً.

## 3. إنشاء نسخة مستقلة على Windows 10/PowerShell

أنشئ مجلداً خاصاً للنسخة، ثم عيّن المسارين التاليين في PowerShell. استبدل المسارين بمساراتك الفعلية:

```powershell
$Source = "C:\ERP\backend\instance\erp.sqlite3"
$BackupDir = "C:\ERP-Private\Stage25C"
$Snapshot = Join-Path $BackupDir "erp-v8-stage25c.sqlite3"
$Report = Join-Path $BackupDir "control-plane-preflight.json"

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
```

استخدم SQLite Backup API لإنشاء لقطة متسقة. لا يستخدم هذا المقطع `shutil.copy` ولا يكتب إلى المصدر:

```powershell
@'
import pathlib
import sqlite3
import sys

source = pathlib.Path(sys.argv[1]).expanduser().resolve(strict=True)
target = pathlib.Path(sys.argv[2]).expanduser().resolve()

if source == target:
    raise SystemExit("Source and destination must be different files")
if target.exists():
    raise SystemExit("Refusing to overwrite an existing snapshot")

target.parent.mkdir(parents=True, exist_ok=True)
source_uri = source.as_uri() + "?mode=ro"
src = sqlite3.connect(source_uri, uri=True, timeout=30)
dst = sqlite3.connect(str(target), timeout=30)

try:
    src.execute("PRAGMA query_only=ON")
    if src.execute("PRAGMA query_only").fetchone()[0] != 1:
        raise RuntimeError("Source connection is not read-only")

    src.backup(dst)
    dst.commit()

    integrity = [row[0] for row in dst.execute("PRAGMA integrity_check").fetchall()]
    fk_violations = dst.execute("PRAGMA foreign_key_check").fetchmany(21)

    print("Snapshot:", target)
    print("Integrity check:", integrity)
    print("Foreign-key violations sampled:", len(fk_violations))

    if integrity != ["ok"]:
        raise SystemExit("Snapshot failed SQLite integrity_check")
    if fk_violations:
        print("Foreign-key violation sample:", fk_violations[:5])
        raise SystemExit("Snapshot has foreign-key violations; stop and investigate")
finally:
    dst.close()
    src.close()
'@ | py -3.12 - $Source $Snapshot

if ($LASTEXITCODE -ne 0) {
    throw "SQLite snapshot creation or integrity validation failed"
}
```

إذا لم يكن Python 3.12 متاحاً، استخدم نسخة Python المعتمدة للمشروع بعد التحقق من التوافق. لا تثبّت أو تشغّل سكربتاً من مصدر غير موثوق.

## 4. التحقق من الملف قبل Preflight

احسب SHA-256 للنسخة المستقلة واحفظه ضمن سجل العملية المحلي:

```powershell
Get-FileHash -Algorithm SHA256 $Snapshot
Get-Item $Snapshot | Select-Object FullName, Length, LastWriteTimeUtc
```

يجب أن يطابق التقرير لاحقاً SHA-256 نفسه. إذا ظهر أي خطأ في `integrity_check` أو `foreign_key_check`، أوقف العملية ولا تنتقل إلى ETL.

لا تحذف ملفات المصدر أو WAL/SHM الأصلية ولا تغيّرها في هذه المرحلة. لا تعتبر اللقطة بديلاً عن النسخ الاحتياطي المعتاد.

## 5. تشغيل Preflight بوضع القراءة فقط

من مجلد `backend` في نسخة المصدر البرمجية المطابقة لـERP:

```powershell
py -3.12 .\scripts\control_plane_preflight.py $Snapshot --output $Report --fail-on-blockers
$PreflightExitCode = $LASTEXITCODE
Get-FileHash -Algorithm SHA256 $Snapshot
```

معاني رمز الخروج:

| الرمز | المعنى | الإجراء |
|---|---|---|
| `0` | لا توجد Blockers في الفحوص المنفذة | راجع warnings والمطابقة؛ يسمح فقط بدراسة import مؤقت |
| `2` | توجد Blockers | أوقف Stage 25C؛ حلّل أسبابها ولا تبدأ ETL |
| `1` | فشل في قراءة الملف أو تشغيل الأداة | تحقق من المسار والصلاحيات وسلامة اللقطة ثم أعد الفحص |

لا تستخدم `--include-email-values` في التشغيل العادي. هذا الخيار قد يضيف بيانات بريد إلكتروني إلى التقرير ويحوّله إلى ملف يحوي PII؛ لا تستخدمه إلا عند الضرورة وبعد حصر الوصول إليه.

## 6. مراجعة التقرير

راجع هذه العناصر قبل مشاركة ملخص:

- `source_database_sha256`: يجب أن يطابق قيمة `Get-FileHash`.
- `schema_version`: يجب أن تكون 8.
- `sqlite_integrity_check`: يجب أن تكون `ok`.
- `sqlite_foreign_key_violations_sampled`: يجب أن تكون صفراً.
- `summary.blocker_count` و`summary.warning_count`.
- `summary.ready_for_control_plane_import`: تعني أن فحوص control-plane المحددة لم تجد blockers فقط؛ لا تعني أن كل بيانات ERP أو عزل الشركات جاهزان.
- `blockers` و`warnings`: راجع الأكواد والأعداد والرسائل.
- `details`: قد تتضمن معرّفات صفوف أو قوائم تعارضات. احفظ التقرير كبيانات داخلية ولا تنشره كاملاً قبل تنقيحه.

عند مشاركة نتيجة مع فريق التطوير، شارك ملخصاً منقحاً فقط: إصدار المخطط، نتيجة integrity/FK، عدد users/roles/companies/memberships، أعداد blockers/warnings وأكوادها، وSHA-256. احذف البريد الإلكتروني والمعرّفات الحساسة وأي حقول لا تلزم التشخيص.

## 7. ما الذي لا يحدث في Stage 25C بعد؟

حتى لو كان التقرير جاهزاً:

- لم يُنشأ PostgreSQL schema نهائي للتطبيق.
- لم تُنقل أي بيانات إلى PostgreSQL.
- لم يتغير محول SQLite أو قاعدة التطبيق النشطة.
- لم يحدث dual-write.
- لم تُفعّل RLS أو tenant scoping على التطبيق.
- لم تُغيّر أي أسرار أو إعدادات إنتاج.

الخطوة التالية بعد مراجعة نسخة حقيقية وتقريرها هي تشغيل ETL على قاعدة PostgreSQL 16 مؤقتة فقط، مع staging tables وrow-count reconciliation وFK/unique checks وقياس الفروقات، ثم إسقاط القاعدة وإعادة التجربة من الصفر لإثبات قابلية التكرار. لا يُربط التطبيق بقاعدة PostgreSQL قبل اعتماد هذه النتائج وخطة تراجع مستقلة.

## 8. سجل التنفيذ المطلوب

احتفظ بسجل محلي يتضمن:

- تاريخ النسخ والبيئة التي أُخذت منها اللقطة.
- المسار المحلي للنسخة (لا تدرجه في Git).
- حجم الملف وSHA-256.
- نتائج SQLite integrity/FK checks.
- نسخة أداة preflight وcommit المصدر.
- رمز خروج الأداة، وأعداد blockers/warnings.
- الشخص الذي راجع الفروقات وقرار الاستمرار/الإيقاف.

**بوابة التوقف:** لا تشارك ملف SQLite أو نسخة من ملف `.env` لتسهيل الفحص. المطلوب هو نسخة محلية مستعادة وتقرير منقح فقط.
