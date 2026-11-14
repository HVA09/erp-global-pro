# ERP Global Pro — Stage 19 Change Log

- أُعيد إدراج تدقيق PostgreSQL كوثيقة قابلة للتسليم.
- يوضح التدقيق أن المصدر ما زال SQLite-specific ولا يمكن تحويله عبر تغيير connection string.
- حُددت مخاطر PRAGMA وsqlite_master وtriggers وAUTOINCREMENT وlastrowid وBEGIN IMMEDIATE وqmark placeholders وbackup/restore.
- لا يوجد PostgreSQL server أو driver أو migration/RLS في هذه المرحلة.
- أداة الجرد الآلي أضيفت فعليًا في حزمة Stage 20 مع اختبارات لها.

راجع `docs/ERP_STAGE_19_POSTGRES_AUDIT_AR.md`.
