# ERP Global Pro — سجل تغييرات Stage 15

**التاريخ:** 13 نوفمبر 2026
**الأساس:** Stage 14 — `erp-global-pro-stage14-reporting-data-audit-scope.zip`
**مخطط SQLite:** الإصدار 7

## ما تم تنفيذه

### 1. جدول مرفقات منفصل

أضاف الترحيل v7 جدول `file_objects` ويحتوي على:

- `company_id` إلزامي ومرتبط بجدول `companies`.
- `storage_key` عشوائي غير مشتق من اسم الملف ولا يقبل من العميل.
- الاسم الأصلي المنظف والعنوان والتصنيف والمرجع والملاحظة.
- MIME تم تحديده من توقيع المحتوى لا من Content-Type المرسل من العميل.
- حجم الملف وبصمة SHA-256 وهوية الرافع ووقت الإنشاء.
- فهارس حسب الشركة والوقت وبصمة المحتوى.

الصفوف القديمة في `files` هي بيانات وصفية/روابط فقط؛ لم يتم تخمين bytes أو إنشاء صفوف `file_objects` لها.

### 2. التخزين الخاص

- القيمة الافتراضية: `backend/instance/files`، ويمكن ضبطها عبر `ERP_FILE_STORAGE_DIR`.
- يُرفض وضع مجلد التخزين داخل frontend/static.
- لكل شركة مجلدها العددي المستقل؛ أسماء الملفات على القرص عشوائية ولا يستخدم اسم العميل كمسار.
- صلاحيات المجلدات `0700` والملفات `0600` حيث يدعم نظام التشغيل ذلك.
- تُفحص المسارات بعد resolve وتُرفض الروابط الرمزية التي تهرب خارج مجلد الشركة.
- لا يُعاد `storage_key` أو مسار الملف إلى العميل.

### 3. واجهات API

تُسجل في `backend/erp/api/file_objects.py` وتستخدم صلاحيات `documents.read` و`documents.write`:

- `GET /api/file-objects` — قائمة metadata الخاصة بالشركة النشطة فقط.
- `POST /api/file-objects/upload` — رفع ملف؛ الشركة تؤخذ من سياق JWT/العضوية على الخادم.
- `GET /api/file-objects/<id>` — بيانات المرفق الخاص بالشركة.
- `GET /api/file-objects/<id>/download` — تنزيل كمرفق attachment، وليس inline.
- `DELETE /api/file-objects/<id>` — حذف metadata ومحتوى المرفق ضمن نطاق الشركة.

### 4. سياسة الملفات

الأنواع المسموحة حاليًا: PDF, PNG, JPG/JPEG, WEBP, TXT, CSV. الحد الأقصى 10 MiB. يتم رفض HTML وSVG والامتدادات غير المعروفة، وفحص توقيع المحتوى ومطابقته للامتداد، ورفض الأسماء ذات المسارات أو أحرف التحكم.

### 5. روابط الملفات القديمة وسلامة المرفقات

- أضيفت قائمة سماح HTTP/HTTPS مطلقة فقط لروابط metadata القديمة؛ تُرفض javascript/data/file/protocol-relative والروابط ذات بيانات اعتماد أو أحرف تحكم.

### 6. سلامة المرفقات والنسخ الاحتياطي

- أضيف `python manage.py check-files` لمقارنة metadata مع وجود bytes والحجم وSHA-256.
- تم تعديل API backup وCLI backup/restore لرفض نسخة SQLite فقط عندما توجد مرفقات خارجية، كي لا تتولد نسخة ناقصة أو استعادة يتيمة المرفقات.
- لم يتم تنفيذ DB+files archive/restore بعد؛ يجب أن تكون تلك حزمة واحدة ذرية مع manifest قبل السماح بالنسخ عند وجود ملفات.

### 7. cache والمهام الخلفية

- cache الإعدادات والحسابات محفوظ داخل `flask.g` لكل request.
- `_cols_cache` في `db.py` يحمل معلومات أعمدة/مخطط فقط، لا بيانات أعمال أو استجابات مستأجر.
- لا يوجد queue أو scheduler أو notification engine حاليًا. أي worker مستقبلي يجب أن يستقبل `company_id` صريحًا، ويمرره للخدمات، ولا يحتفظ بـ `flask.g` خارج دورة الطلب.

## الاختبارات

أضيف `backend/tests/test_tenant_stage15_file_storage.py` ويضم 16 اختبارًا:

- migration v7 على المخطط الكامل وidempotency.
- قيود الشركة والحجم والمفاتيح الأجنبية.
- فصل قوائم الشركات والسماح بتكرار اسم الملف بين شركتين.
- منع القراءة والتنزيل والحذف عبر ID تابع لشركة أخرى.
- رفض path traversal وHTML/SVG والملفات ذات التوقيع غير المطابق والملفات الفارغة أو الأكبر من الحد.
- حماية مجلد التخزين من أن يكون داخل frontend.
- التحقق من download attachment headers.
- اكتشاف الملفات المفقودة والمتغيرة باستخدام SHA-256.

النتيجة الموحدة لاختبارات `test_tenant*.py`: 127/127 PASS.
`python -m compileall -q backend`: PASS.
JavaScript syntax: 19/19 PASS.

## ما لم يتم

- لم يُدمج مضاد فيروسات أو malware scanning. التحقق من التوقيع والامتداد ليس بديلًا عن AV.
- لم تُربط واجهة frontend بإدارة المرفقات؛ API فقط.
- لم يُنشأ DB+files backup/restore.
- لم تُشغّل اختبارات Flask/HTTP/E2E حقيقية؛ Flask غير مثبت في بيئة الفحص.
- لا PostgreSQL أو RLS أو اختبارات pool/session leakage.
- لا notifications أو background queue؛ لا توجد عمليات مجدولة حالية.
- لم تتغير `TENANT_DATA_SCOPING_ENABLED=False`.
- لا نشر إنتاجي ولا إنشاء/رفع GitHub.
