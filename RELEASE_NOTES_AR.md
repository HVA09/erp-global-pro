# ملاحظات إصدار ERP Global Pro — Stage 24

## الجديد

- إضافة `current_user_id()` التي لا تكشف principal إلا بعد التحقق الناجح من سياق شركة موقّع.
- إضافة `list_my_companies()` لإرجاع الشركات ذات العضويات النشطة للمستخدم الموثق فقط.
- إضافة `get_current_company_profile()` لإرجاع بيانات الشركة الحالية غير السرية.
- إضافة `get_current_membership()` لعرض عضوية الشركة الحالية.
- جميع الوظائف الأربع تستخدم `SECURITY DEFINER` مع مالك أمان NOLOGIN و`search_path` ثابت، وREVOKE من PUBLIC ومنح EXECUTE صريحًا لدور التطبيق. لا يوجد وصول SELECT مباشر إلى control tables.
- إضافة 4 اختبارات تصميم جديدة و7 اختبارات PostgreSQL حية جديدة.

## التحقق

- Stage 21/22 focused regression: 48/48 PASS.
- Stage 23 signer + SQL design: 30/30 PASS.
- الإجمالي المحلي المركّز: **78/78 PASS**.
- Python compileall: PASS؛ JavaScript syntax: 19/19 PASS؛ YAML parse: PASS.
- PostgreSQL live tests: 30 اختبارًا مجهزًا، **NOT RUN LOCALLY** لعدم توفر PostgreSQL/Psycopg/DSN.

## الحدود

- الوظائف غير موصولة بـFlask/JWT أو routes الحالية.
- `list_my_companies()` لا تستخدم لتسجيل الدخول الأولي؛ تتطلب سياق شركة موقّعًا بالفعل.
- لا توجد وظائف إدارة عضويات أو صلاحيات إدارية. الدور المعاد للعرض لا يكفي لاتخاذ قرار تفويض.
- لم تُنفذ migrations PostgreSQL أو Composite FKs أو RLS على جداول ERP الفعلية.
- SQLite v8 تظل قاعدة التطبيق، و`TENANT_DATA_SCOPING_ENABLED=False` إلزامية.
- المستودع `HVA09/erp-global-pro` لم يُنشأ أو يُرفع بعد.

## القرار

Stage 24 نموذج قراءة آمن لمخطط التحكم وتجهيز CI؛ ليس إصدارًا جاهزًا للإنتاج أو لتعدد الشركات.
