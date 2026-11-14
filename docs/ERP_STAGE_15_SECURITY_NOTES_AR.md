# Stage 15 — ملاحظات أمن المرفقات والتشغيل

## نموذج التهديد المختصر

نعتبر اسم الملف وامتداده وContent-Type وبيانات multipart كلها مدخلات غير موثوقة. كذلك لا نثق في company_id أو file ID المرسل من العميل لتحديد الملكية.

## الضوابط المطبقة

1. تحديد الشركة من سياق الخادم عبر membership/JWT، ثم الاستعلام بـ `(id, company_id)`.
2. عدم قبول `company_id` من form upload.
3. اسم تخزين عشوائي UUID؛ مسارات النظام لا تأتي من العميل.
4. التخزين خارج frontend/static، ومجلد منفصل لكل شركة.
5. رفض مسارات `/` و`\\` وأحرف التحكم؛ لا path traversal.
6. allowlist للامتداد والتحقق من magic bytes أو UTF-8 النصي.
7. منع HTML وSVG والملفات النشطة غير المدعومة.
8. إرسال التنزيل كـ attachment مع `X-Content-Type-Options: nosniff` و`Cache-Control: no-store`.
9. حد 10 MiB للملف الواحد؛ body request يسمح بزيادة صغيرة لترويسة multipart.
10. SHA-256 للتحقق من سلامة البايتات بعد الرفع.
11. روابط metadata القديمة مقيدة بـHTTP/HTTPS فقط، دون `javascript:` أو `data:` أو `file:` أو protocol-relative.
12. منع SQLite-only backup/restore عندما توجد bytes خارجية.

## مخاطر متبقية / شروط ما قبل الإنتاج

- **مضاد الفيروسات غير موجود.** يجب فحص الملفات في quarantine باستخدام ClamAV أو خدمة فحص موثوقة قبل إتاحة تنزيلها في بيئة الإنتاج. لا تُعامل MIME sniffing على أنه malware scan.
- **لا يوجد حد إجمالي للتخزين لكل شركة** أو معدل رفع لكل مستخدم حتى الآن؛ أضف quotas وrate limits قبل الإطلاق العام.
- **لا يوجد DB+files backup/restore موحد**؛ النسخ والاستعادة ترفض وجود مرفقات حتى لا تفقد bytes.
- **الواجهة الأمامية غير موصولة** بواجهة المرفقات؛ يلزم UI يعرض حالة الرفع والأخطاء والتنزيل.
- حذف السجل ثم فشل حذف الملف قد يترك orphan bytes؛ استخدم `check-files` للـ metadata integrity، وأضف orphan sweeper موثوقًا عند بناء backup service.
- لم تختبر منافذ HTTP أو WSGI أو متصفحًا حقيقيًا في هذه البيئة.
- لا PostgreSQL/RLS بعد، ولا تفعيل بوابة تعدد الشركات.

## أوامر التشغيل

```bash
python manage.py check-files
python -m unittest discover -s tests -p 'test_tenant*.py' -v
```

نفّذ الاختبارات على نسخة development/test، ولا تستخدم قاعدة إنتاج حية لتجارب الرفع أو الحذف.
