# دليل تشغيل اختبارات HTTP الحقيقية — Stage 17

1. افتح مجلد `backend`.
2. ثبّت المتطلبات: `python -m pip install -r requirements.txt`.
3. شغّل: `python -m unittest tests.test_stage17_http_readiness -v`.
4. شغّل المجموعة الكاملة: `python -m unittest discover -s tests -v`.
5. شغّل فحص البيئة: `python scripts/runtime_preflight.py`.

الاختبارات الفعلية تستخدم Flask `test_client`، ولا تستبدله بـstub. تختبر تسجيل الدخول، تبديل الشركة، headers، رفع المرفقات، منع قراءة/تنزيل/حذف ملف الشركة الأخرى، منع العميل من إرسال `company_id`، وضع الصيانة، ومنع بدء التطبيق عند وجود restore journal.

في هذه النسخة، هذه الاختبارات الخمسة موجودة لكنها تظهر SKIPPED لأن Flask وWerkzeug غير مثبتين. لا تعتبرها ناجحة قبل تشغيلها في بيئة متصلة.
