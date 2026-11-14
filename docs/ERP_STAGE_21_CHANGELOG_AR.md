# ERP Global Pro — Stage 21 Change Log

## القرار

- اختيار Psycopg 3 كـDB-API driver للـprototype الأول، وتأجيل SQLAlchemy Core.
- السبب: التطبيق يستخدم DB-API وSQL خامًا بالفعل؛ Psycopg 3 أقرب إلى ذلك من إدخال Engine/Connection وCore statement model دفعة واحدة.

## الملفات

- `backend/erp/db_adapters/postgres_psycopg.py`: adapter prototype lazy-import، row factory، placeholder scanner، transaction methods، `RETURNING id` behavior.
- `backend/tests/test_postgres_psycopg_adapter_spike.py`: 32 اختبارًا باستخدام fake DB-API connection/cursor.
- `backend/requirements-postgres.txt`: متطلبات PostgreSQL الاختيارية، غير مثبتة وغير مضافة إلى المتطلبات العامة.
- `backend/erp/db_adapters/__init__.py`: export للـprototype.
- جعل `begin(write=True)` يفشل بوضوح لأن دلالة SQLite `BEGIN IMMEDIATE` لم تُنقل إلى PostgreSQL بعد.

## حدود التغيير

- لم يتم توصيل adapter بـ`erp/db.py` أو اختيار backend من environment.
- SQLite لا تزال المحرك الوحيد الفعلي.
- لا خادم PostgreSQL ولا سائق psycopg مثبت في بيئة الفحص.
- لا schema/migrations PostgreSQL ولا Composite FKs ولا RLS.
- لا تغييرات على SQLite schema؛ تبقى v8.
- لا نشر أو GitHub push.

راجع `docs/ERP_STAGE_21_PSYCOPG3_VS_SQLALCHEMY_CORE_AR.md`.
