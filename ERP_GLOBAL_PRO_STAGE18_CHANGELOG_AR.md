# ERP Global Pro — Stage 18 Change Log

**الموضوع:** بوابة تكامل HTTP فعلية وCI قابل لإعادة التشغيل.

## التغييرات

1. إضافة `.github/workflows/erp-backend-ci.yml` لتشغيل اختبارات backend على Python 3.11 و3.12 في GitHub Actions.
2. إضافة Werkzeug صراحةً إلى `backend/requirements.txt` لتوضيح اعتماد HTTP runtime.
3. إضافة `backend/scripts/require_http_dependencies.py` لفحص الاعتمادات المطلوبة قبل الاختبار.
4. إضافة `backend/scripts/run_http_tests.py` لتشغيل سيناريوهات Flask test client، واعتبار أي skip فشلًا.
5. إضافة ستة سيناريوهات HTTP: منع تبديل الشركة لغير العضو، إبطال token بعد تعليق العضوية، بقاء بوابة العمليات مغلقة، منع تنزيل الملف pending، تنزيل clean مع headers آمنة، كشف تغيير bytes وحظر التنزيل، وسياسة CORS المقيدة.
6. إضافة أربعة اختبارات readiness مستقلة عن Flask للتحقق من workflow والاعتمادات وno-skip gate وبقاء multi-tenant flag مغلقًا.

## ما لم يتم

- لم تُنفذ اختبارات HTTP الحقيقية في بيئة البناء؛ Flask/Werkzeug غير مثبتين وتعذر pip بسبب DNS إلى package index.
- لم يتم تشغيل GitHub Actions لأن مستودع ERP لم يُنشأ أو يُرفع.
- لم تُنفذ Browser E2E أو ClamAV الحقيقي أو PostgreSQL/RLS.
- لم يتم تغيير مخطط SQLite أو قاعدة إنتاج أو نشر التطبيق.

## قرار الأمان

`TENANT_DATA_SCOPING_ENABLED=False` باقٍ كما هو. لا تعتمد multi-company release قبل صفر اختبارات HTTP متخطاة، واختبارات المتصفح، وPostgreSQL composite FKs/RLS.
