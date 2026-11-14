# دليل تشغيل مخزن المرفقات — Stage 15

## الإعداد

```env
ERP_FILE_STORAGE_DIR=backend/instance/files
```

يجب أن يكون المسار خارج `frontend/` وأي web root. في الخادم الفعلي، فضّل مسار volume خاصًا غير قابل للوصول عبر HTTP، بصلاحيات نظام ملفات مقيدة. لا تضع المرفقات داخل Git أو مجلد static.

## API

- `GET /api/file-objects?limit=100&offset=0`
- `POST /api/file-objects/upload` — multipart form-data يحتوي `file`، ويمكنه استقبال `title`, `category`, `related_to`, `note`. لا ترسل `company_id`؛ سيتم رفضه.
- `GET /api/file-objects/<id>`
- `GET /api/file-objects/<id>/download`
- `DELETE /api/file-objects/<id>`

يجب توفر صلاحيات `documents.read` أو `documents.write`، ويجب أن تكون العضوية النشطة في شركة صالحة.

مثال توضيحي بعد الحصول على token صالح:

```bash
curl -X POST http://127.0.0.1:8000/api/file-objects/upload \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@invoice.pdf" \
  -F "title=Supplier invoice" \
  -F "category=finance"
```

الملف الواحد لا يتجاوز 10 MiB. الأنواع المسموحة PDF وPNG وJPG/JPEG وWEBP وTXT وCSV.

## فحص سلامة الملفات

```bash
cd backend
python manage.py check-files
```

الأمر يفحص كل سجل `file_objects` على مستوى المنصة (platform operator)، ويبلغ عن المحتوى المفقود أو الحجم غير المطابق أو SHA-256 غير المطابق. لا يطبع المسارات الكاملة.

## النسخ الاحتياطي والاستعادة

`file_objects` يشير إلى bytes مخزنة خارج SQLite. لذلك أوامر backup/restore ذات قاعدة البيانات وحدها ترفض العمل عندما توجد مرفقات. لا تتجاوز هذا الرفض يدويًا؛ يلزم تنفيذ Stage 16 DB+files archive/restore قبل أخذ نسخة موثوقة أو الاستعادة.

## تنبيه أمني

لا يوجد malware scanner بعد. لا تفتح الملفات inline. لا تستخدم المرفقات في الإنتاج أو للمستندات غير الموثوقة حتى دمج quarantine وفحص مضاد للبرمجيات الخبيثة، وإضافة quotas وrate limits وربط واجهة frontend.
