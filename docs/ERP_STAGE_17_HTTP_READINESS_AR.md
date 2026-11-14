# Stage 17 — جاهزية HTTP وتدقيق مسارات التشغيل

**الحالة:** جزئي / بوابة بيئية مفتوحة. أضيفت اختبارات HTTP حقيقية قابلة للتشغيل عند تثبيت Flask وWerkzeug، لكنها لم تعمل في بيئة البناء الحالية لغياب هاتين الحزمتين.

## التغييرات الفعلية

1. أضيف `backend/scripts/runtime_preflight.py` لفحص تبعيات Python، إعدادات الإنتاج، الفصل بين مسار الملفات الخاصة والواجهة الأمامية، عدم تداخل DB والتخزين، symlinks، وضع الصيانة، وتوفر ClamAV. الفحص للقراءة فقط ولا ينشئ ملفات أو مجلدات ولا يفتح قاعدة البيانات.
2. أضيف `backend/tests/test_stage17_http_readiness.py`.
   - 11 اختبارًا ساكنًا بدون اعتماد Flask للتحقق من تسجيل الـblueprints، الحارس العام للمصادقة، قائمة المسارات العامة، إعداد قفل تعدد الشركات، حارس restore journal، وحماية مسارات المرفقات.
   - 5 اختبارات باستخدام Flask test client حقيقي، وهي مهيأة لتعمل بعد تثبيت Flask/Werkzeug. لا تستخدم Flask stub؛ تُعلّم كـskipped عندما لا تتوفر التبعيات.
3. أصلح مسار إعداد النظام الأولي في `backend/erp/api/auth.py`: كان إعداد الشركة يستدعي خدمة الإعدادات tenant-aware قبل إنشاء عضوية المالك وتثبيت سياق الشركة في الطلب. أصبح الإعداد ينشئ عضوية المدير أولًا، يتحقق منها من قاعدة البيانات، يثبت `g.company` من العضوية الموثقة، ثم يحفظ الإعدادات صراحةً للشركة ذاتها. هذا إصلاح برمجي مستند إلى تتبع الكود، ويحتاج تأكيدًا باختبار HTTP الحقيقي عند توفر Flask.

## الاختبارات

- `python -m unittest discover -s tests -p 'test_tenant*.py' -v`: **155/155 PASS**.
- `python -m unittest tests.test_tenant_stage16_backup_bundle tests.test_tenant_stage15_file_storage -v`: **44/44 PASS** (إعادة تشغيل تأكيدية؛ هذه الاختبارات داخلة أصلًا ضمن مجموعة `test_tenant*.py` وليست 44 اختبارًا إضافيًا على المجموع).
- `python -m unittest tests.test_stage17_http_readiness -v`: **11 PASS / 5 SKIPPED**. الاختبارات الخمسة التي تم تخطيها هي اختبارات Flask HTTP الحقيقية فقط بسبب عدم توفر Flask وWerkzeug.
- `python -m compileall -q .`: PASS.
- فحص `node --check` لكل ملفات JavaScript: **19/19 PASS**.

## عائق البيئة

- `Flask` و`Werkzeug` غير مثبتين.
- جرت محاولة تثبيتهما بواسطة pip، لكن حل أسماء النطاقات إلى مستودع الحزم فشل بسبب عدم وجود اتصال DNS، لذلك لم يمكن تشغيل خادم Flask أو TestClient الحقيقي.
- `ClamAV` و`gunicorn` غير مثبتين في بيئة البناء. غياب ClamAV يعني بقاء فحص الملفات fail-closed؛ لا يجوز اعتبار الملفات pending سليمة.
- تشغيل `python scripts/runtime_preflight.py --json` أظهر `status=blocked` بسبب Flask وWerkzeug. لم يطبع أي أسرار ولم يغير النظام.

## خطوات تحقق مطلوبة في بيئة متصلة

من مجلد `backend`:

```bash
python -m pip install -r requirements.txt
python -m unittest tests.test_stage17_http_readiness -v
python -m unittest discover -s tests -v
python scripts/runtime_preflight.py
```

يجب أن تمر اختبارات setup/login/switch-company والمرفقات عبر شركتين، وmaintenance mode، وstartup guard عند وجود restore journal قبل الانتقال إلى PostgreSQL/RLS.

## ما لم يُنجز

- لم يتم تشغيل HTTP حقيقي أو Browser E2E في هذه الجولة.
- لم يتم تنفيذ PostgreSQL أو composite foreign keys أو RLS.
- لم يتم تشغيل ClamAV فعلي أو اختبار AV مع signatures حديثة.
- لم يتم فتح `TENANT_DATA_SCOPING_ENABLED`؛ يجب أن يبقى `False`.
- لا نشر إنتاجي ولا اتصال بقاعدة إنتاج ولا GitHub push.
