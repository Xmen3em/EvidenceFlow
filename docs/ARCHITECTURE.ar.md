<div dir="rtl" lang="ar">

<h1>معمارية EvidenceFlow</h1>

<p>المنتج معمول لفريق مشتريات صغير بيحاول يجاوب سؤال محدد: إيه اللي مختلف في الـ <bdi>PO</bdi> والـ <bdi>invoice</bdi> والـ <bdi>goods receipt</bdi>، وكل معلومة جاية منين؟ لما الإيصال ناقص، الحالة بتعرض <bdi>evidence gap</bdi>. لما التلات مستندات موجودين، المقارنة بتفضل على الحقول اللي الاستخراج بيدعمها. المراجع البشري بيسجّل قرار المراجعة، والتطبيق ما بيصدرش تصريح دفع.</p>

<h2>من الرفع لحد القرار</h2>

<ol>
<li>الـ <bdi>browser</bdi> بيبعت الملف للـ <bdi>FastAPI backend</bdi> بنفس الـ <bdi>origin</bdi>. السيرفر بيتحقق من الـ <bdi>session cookie</bdi> ومن إن الحالة تابعة لنفس الـ <bdi>workspace</bdi>.</li>
<li>الـ <bdi>storage adapter</bdi> بيخزّن الأصل. <bdi>Postgres</bdi> بيخزّن الـ <bdi>checksum</bdi>، النوع، وحالة الاستخراج. على جهازك التخزين محلي، وفي النشر ممكن تستخدم <bdi>R2</bdi> بواجهة <bdi>S3</bdi>.</li>
<li>الـ <bdi>background worker</bdi> بيحجز شغل من <bdi>Postgres</bdi> بمهلة. لو العملية وقفت، العامل التالي يقدر يعيد الحجز. نتيجة عامل قديم ما تقدرش تكتب فوق حجز أحدث.</li>
<li>الـ <bdi>rule engine</bdi> بيقارن رقم الطلب، المورد، الإجمالي والعملة، وكميات الـ <bdi>SKU</bdi>. كل <bdi>finding</bdi> بيتربط باسم الملف ورقم الصفحة والسطر لما يبقى فيه مصدر. نقص المستند نفسه بيتعرض كفجوة.</li>
<li>الاقتراح بيتسجّل <bdi>pending</bdi> على رقم <bdi>evidence revision</bdi>. مراجع مختلف بيقبل أو يرفض من الواجهة. إضافة مستند أو انتهاء استخراجه بيغيّر النسخة. السيرفر يرفض الموافقة على اقتراح من نسخة أقدم، والقرار وسجل النشاط بيتحفظوا في نفس الـ <bdi>transaction</bdi>.</li>
</ol>

<h2>إيه دور MCP هنا؟</h2>

<p>لو صفحة في الـ <bdi>PDF</bdi> فيها صورة ومفيهاش نص، الـ <bdi>worker</bdi> بيحوّلها لصورة بـ <bdi>Poppler</bdi> ويستخرج النص الإنجليزي بـ <bdi>Tesseract OCR</bdi>. المراجع يقدر يفتح صورة الصفحة جوه التطبيق ويصحّح حقل اتعرّف عليه النظام. <bdi>Postgres</bdi> بيحفظ التصحيح والسبب واسم المراجع، والقيمة الأصلية بتفضل مربوطة بالسطر المستخرج. المقارنة والـ <bdi>MCP</bdi> بيستخدموا القيمة الجديدة، والاقتراحات المرتبطة بالنسخة الأقدم بتظهر <bdi>Stale</bdi>.</p>

<p>الـ <bdi>host</bdi> هنا هو تطبيق <bdi>EvidenceFlow</bdi>. لما تدوس <bdi>Run offline</bdi>، التطبيق بيستدعي أداة المراجعة بالـ <bdi>official MCP Client</bdi> جوه العملية نفسها. عند الاتصال من برنامج تاني، نفس تعريفات الأدوات بتشتغل عبر <bdi>Streamable HTTP</bdi>. في تمرين <bdi>stdio</bdi>، الـ <bdi>client</bdi> بيشغّل السيرفر كعملية محلية ويتكلم معاه على <bdi>stdin/stdout</bdi>.</p>

<ul>
<li><bdi>Tools</bdi> للعمليات. جرّب <bdi>review_discrepancies</bdi> لمراجعة الفروق، و<bdi>propose_disposition</bdi> لتسجيل طلب مراجعة.</li>
<li><bdi>Resources</bdi> لقراءة سياق معروف بعنوان. مثال <bdi>evidenceflow://case/current</bdi> بيرجع بيانات الحالة اللي الـ <bdi>token</bdi> مربوط بيها.</li>
<li><bdi>Prompts</bdi> لقوالب إعادة الاستخدام. <bdi>triage</bdi> بيرجع تعليمات التحقيق. الـ <bdi>host</bdi> هو اللي يقرر يستخدمها إمتى.</li>
</ul>

<p>قفلنا الإصدار على <bdi>MCP Python SDK 2.3.0</bdi> في <bdi>uv.lock</bdi> بعد مراجعة الإصدار الرسمي. إصدار البروتوكول المستخدم في الاختبار هو <bdi>2026-07-28</bdi>. عشان كده هتلاقي <bdi>MCPServer</bdi> و<bdi>Client</bdi> في الكود، مقابل <bdi>FastMCP</bdi> و<bdi>ClientSession</bdi> في مثال الدرس القديم.</p>

<h2>فين Hugging Face في الرحلة؟</h2>

<p>الـ <bdi>host</bdi> بيقرأ تعريفات الأدوات بـ <bdi>list_tools</bdi>، وبيحوّل الأدوات المسموح بيها لـ <bdi>function schemas</bdi> يقدر الـ <bdi>HF Router</bdi> يفهمها. الموديل بيطلب استدعاء أداة. التطبيق بيتحقق من اسمها ومدخلاتها وبينفّذها بالـ <bdi>MCP Client</bdi>، ويرجّع الناتج للموديل. ملخص الموديل بيظهر لوحده، والمراجع اللي تحته بتيجي من مخرجات الأدوات الفعلية. الـ <bdi>model</bdi> عنده أدوات قراءة فقط. الـ <bdi>remote MCP client</bdi> يقدر يقترح قرار، والموافقة تفضل في واجهة المراجع.</p>

<p>اختيار <bdi>same-origin</bdi> خلّى تشغيل الـ <bdi>frontend</bdi> والـ <bdi>API</bdi> في خدمة واحدة ممكن، مع <bdi>HttpOnly cookie</bdi> وقواعد <bdi>Origin</bdi> بسيطة. كمان استخدمنا <bdi>Postgres</bdi> للطابور عشان الـ <bdi>MVP</bdi> يشتغل من غير خدمة طابور منفصلة.</p>

</div>

- [Official Python SDK v2.3.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0)
- [MCP 2026-07-28 Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- [Hugging Face Function Calling](https://huggingface.co/docs/inference-providers/guides/function-calling)
