# نموذج التهديد — PostgreSQL Signed Tenant Context (Stage 23)

## الهدف

منع الاعتماد على متغير PostgreSQL مخصص مثل `app.company_id` كدليل ثقة. متغيرات GUC هي قناة نقل فقط؛ لا تُعد تصريحًا أو إثبات عضوية بمفردها.

## نموذج الثقة المقترح

1. خدمة المصادقة تتحقق من هوية المستخدم وعضويته النشطة من مصدر موثوق.
2. خدمة المصادقة تصدر payload canonical قصير العمر يحتوي `sub`, `company_id`, `kid`, `iat`, `exp`, `iss`, `aud`, و`jti`، وتوقّعه بـHMAC-SHA256.
3. تحفظ مفاتيح التحقق في schema محمي، لا يستطيع دور اتصال التطبيق قراءته.
4. يمرر التطبيق payload والتوقيع داخل المعاملة عبر `erp.tenant_payload` و`erp.tenant_signature`.
5. دالة PostgreSQL `SECURITY DEFINER`، بمالك `NOLOGIN`, `NOSUPERUSER`, `NOBYPASSRLS`، تتحقق من التوقيع والوقت والجمهور، ثم تعيد فحص عضوية المستخدم وحالة الشركة من جداول التحكم.
6. سياسات RLS تعتمد `erp_security.current_company_id()`، ولا تستخدم `app.company_id` مباشرة.
7. تنتهي القيم المعاملاتية عند commit/rollback؛ ويجب اختبار إعادة استخدام الاتصال نفسه داخل pool.

## الأصول الواجب حمايتها

- مفاتيح HMAC والقدرة على توقيع claims.
- جداول `companies` و`company_memberships`.
- بيانات كل شركة ومرفقاتها وسجلاتها المالية.
- وظائف `SECURITY DEFINER` وسياسات RLS.
- إعدادات Connection Pool وrole privileges.

## التهديدات التي يعالجها النموذج الأولي

- تغيير `company_id` الخام في GUC.
- تزوير claim أو تعديلها دون توقيع صحيح.
- استخدام claim منتهي الصلاحية.
- استخدام عضوية تم تعليقها بعد إصدار claim؛ الدالة تعيد فحص الحالة الحالية.
- إعادة استخدام اتصال pooled مع بقاء قيمة سياق قديمة؛ القيم المقترحة transaction-local.
- محاولة دور التطبيق قراءة جدول مفاتيح التوقيع أو تعديل العضوية مباشرة في بيئة الاختبار.

## المخاطر التي لا يحلها

- اختراق خادم التطبيق أو تسريب مفتاح HMAC من بيئة التطبيق.
- SQL injection يسمح بسرقة token الحالي أو تنفيذ أوامر ضمن الشركة الحالية.
- وجود جداول أو views أو functions تتجاوز RLS أو تستخدم owner/SUPERUSER/BYPASSRLS.
- امتلاك دور التطبيق صلاحيات مباشرة لتغيير `company_memberships`؛ يجب نقل هذه التغييرات إلى API إداري محمي أو إجراءات `SECURITY DEFINER` تُراجع صلاحيات المستدعي.
- أخطاء business authorization داخل الشركة؛ RLS يحدد الشركة ولا يحدد كل صلاحيات الدور.
- غياب Composite Foreign Keys؛ RLS لا يمنع كل العلاقات المرجعية الخاطئة وحده.
- تسرب بيانات من backups, exports, background jobs, cache, audit logs أو object storage إذا لم تُعزل كل هذه المسارات.

## متطلبات قبل اعتماد التصميم

- إدارة مفاتيح آمنة، تدوير، إبطال، ومراقبة key IDs.
- فصل صلاحيات أدوار migration owner وsecurity owner وapp runtime.
- حظر دور التطبيق من امتلاك الجداول أو تجاوز RLS.
- منع الكتابة المباشرة على membership control tables، وتوفير إجراءات إدارية ذات صلاحيات دقيقة.
- اختبار على مخطط ERP الفعلي، لا على جدول تجريبي فقط.
- اختبارات SQL injection/IDOR وmembership revocation وpool reuse والتزامن وrollback.
- مراجعة مستقلة للوظائف الأمنية وسياسات RLS.

**الحالة:** نموذج Stage 23 غير موصول بالتطبيق، ولم يُشغّل على خادم PostgreSQL في بيئة البناء الحالية.
