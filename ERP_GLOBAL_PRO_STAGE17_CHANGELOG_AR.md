# ERP Global Pro — Stage 17 Change Log

## الهدف
تجهيز بوابة اختبارات HTTP حقيقية وتدقيق جاهزية runtime دون الادعاء بأن التكامل الحقيقي تم اختباره في بيئة لا تحتوي Flask/Werkzeug.

## التغييرات
- إضافة `backend/scripts/runtime_preflight.py`، أداة فحص للقراءة فقط.
- إضافة 11 static readiness tests و5 Flask HTTP integration tests قابلة للتشغيل بعد تثبيت التبعيات.
- إصلاح ترتيب bootstrap setup: إنشاء عضوية مالك الشركة والتحقق منها وربط سياقها في الطلب قبل استدعاء خدمة الإعدادات.
- توثيق أوامر التشغيل وقيود بيئة البناء.

## التحقق
- Tenant focused tests: 155/155 PASS.
- Stage 15 + Stage 16 repeated confirmation: 44/44 PASS (included within the 155 tenant tests).
- Stage 17 readiness: 11 PASS, 5 SKIPPED (real Flask HTTP tests skipped because Flask/Werkzeug are absent).
- Python compileall: PASS.
- JavaScript syntax: 19/19 PASS.
- Runtime preflight: BLOCKED because Flask and Werkzeug are unavailable. Pip install attempt failed due DNS/network resolution failure.

## حدود الإصدار
هذه ليست موافقة إطلاق. HTTP/E2E الحقيقي لم يتم تشغيله، ClamAV غير مثبت، PostgreSQL/RLS غير منفذ، وبوابة تعدد الشركات ما زالت مغلقة.
