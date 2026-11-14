# ERP Global Pro — Stage 18: HTTP integration gate

## الهدف

إضافة بوابة CI قابلة للتكرار لتشغيل اختبارات Flask/Werkzeug الحقيقية على runner متصل، وتوسيع تغطية سيناريوهات المصادقة والعضوية وعزل المرفقات وCORS. لا تستبدل هذه الاختبارات باختبارات AST أو Flask stubs.

## التغييرات

- أضيف `Werkzeug>=3.0,<4` صراحةً إلى `backend/requirements.txt`.
- أضيف `.github/workflows/erp-backend-ci.yml` بمصفوفة Python 3.11 و3.12. يثبت الاعتمادات، يشغّل كامل unittest suite، يفرض تشغيل HTTP tests دون skips، ثم يفحص compileall وJavaScript.
- أضيف `backend/scripts/require_http_dependencies.py` لفشل واضح عند غياب Flask/Werkzeug.
- أضيف `backend/scripts/run_http_tests.py` لتشغيل `Stage17RealHttpIntegrationTests` مع اعتبار أي skipped فشلًا في بوابة HTTP.
- أضيفت سيناريوهات HTTP فعلية (تُشغّل عند توفر Flask) لـ: منع تحويل الشركة لغير الأعضاء، إبطال token بعد تعليق العضوية، استمرار حظر العمليات مع إغلاق بوابة multi-tenant، منع تنزيل المرفقات pending والسماح بتنزيل clean مع headers آمنة، حجب التنزيل بعد اختلاف SHA-256 وتحويل الحالة إلى error، وقصر CORS على origin المصرح.

## ما أمكن تشغيله في بيئة البناء الحالية

- مجموعة عزل المستأجرين قبل التعديلات: 155/155 PASS.
- اختبار Stage 17 readiness: 11 PASS و5 SKIPPED بسبب غياب Flask/Werkzeug.
- `python -m compileall -q backend`: PASS.
- فحوص JavaScript: 19/19 PASS.
- محاولة تثبيت Flask/Werkzeug فشلت بسبب فشل DNS عند الوصول إلى package index.

بعد إضافة 6 سيناريوهات HTTP جديدة، لا يمكن تنفيذها هنا. `run_http_tests.py` يفشل برمز 2 عند غياب الاعتمادات، عمدًا، ولا يستخدم mocks على أنها HTTP حقيقي.

## تشغيل الاختبارات في بيئة متصلة

من `backend/`:

```bash
python -m pip install -r requirements.txt
python scripts/require_http_dependencies.py
python -m unittest discover -s tests -v
python scripts/run_http_tests.py
python -m compileall -q erp tests scripts manage.py run.py
```

يمكن تشغيل workflow عبر GitHub Actions بعد رفع المشروع إلى مستودعه المستقل. لم يُنشأ أو يُرفع أي مستودع ضمن Stage 18.

## حدود Stage 18

- لا يوجد تنفيذ محلي لاختبارات Flask HTTP في هذه البيئة؛ الاختبارات الجديدة موجودة لكنها غير منفذة حتى تعمل في CI.
- لا اختبارات متصفح E2E أو ClamAV حقيقي أو PostgreSQL/RLS.
- `TENANT_DATA_SCOPING_ENABLED=False` ويجب أن تبقى مغلقة.
- لا تغيير في مخطط قاعدة البيانات أو production state.
