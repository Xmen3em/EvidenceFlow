# MCP practice lab

<div dir="rtl" lang="ar">
<p>ابدأ بالـ <bdi>demo</bdi> في <a href="../README.md">README</a> وسيب <bdi>Hugging Face</bdi> مقفول لأول خمس تمارين. كل تمرين فيه نتيجة تقدر تتأكد منها بنفسك.</p>
</div>

## 1. Evidence and missing evidence

<div dir="rtl" lang="ar">
<p>افتح <bdi>PO-042</bdi> وشوف مصدر كل فرق. بعد كده افتح <bdi>PO-088</bdi>. رغم إن الإجماليين متساويين، الـ <bdi>goods receipt</bdi> ناقص.</p>
<p><strong>Checkpoint</strong> · الأول فيه <bdi>total_differs</bdi> و<bdi>quantity_differs_CMP-KIT</bdi>. التاني فيه <bdi>missing_receipt</bdi>. جرّب تضيف إيصال صناعي لنفس الـ <bdi>PO</bdi> بكمية 24 لنفس الـ <bdi>SKU</bdi> وشوف الفجوة وهي بتختفي بعد الاستخراج.</p>
</div>

## 2. Discovery over Streamable HTTP

<div dir="rtl" lang="ar">
<p>من <bdi>MCP lab</bdi> في حالة واحدة اعمل <bdi>token</bdi>. شغّل الأمر والصقه في طلب الإدخال المخفي.</p>
</div>

```sh
docker compose exec app python -m app.cli mcp-inspect
# For a host-side Python client
uv run --locked python -m app.cli mcp-inspect --url http://localhost:8080/protocol/mcp
```

<div dir="rtl" lang="ar">
<p><strong>Checkpoint</strong> · هتشوف <bdi>Protocol: 2026-07-28</bdi>، أربع أدوات، موردين، وقالب <bdi>triage</bdi>. مورد <bdi>case/current</bdi> ونتيجة الأداة لازم يشاوروا على نفس الحالة. في <bdi>Activity</bdi> شوف <bdi>mcp.resource.current_case</bdi> و<bdi>mcp.tool.review_discrepancies</bdi>.</p>
</div>

## 3. Swap the transport to stdio

<div dir="rtl" lang="ar">
<p>خد <bdi>case_id</bdi> من الخطوة اللي فاتت وحطه في الكود. شغّل الكود من مشروعك على جهاز فيه <bdi>uv</bdi> و<bdi>Docker Compose</bdi>، باستخدام <bdi>uv run --locked python lab_stdio.py</bdi> بعد ما تحفظه كملف في جذر المشروع.</p>
</div>

```python
import asyncio

from mcp import Client, StdioServerParameters

async def main():
    server = StdioServerParameters(
        command="docker",
        args=[
            "compose", "exec", "-T", "app", "python", "-m", "app.cli", "mcp-stdio",
            "--workspace", "demo", "--email", "analyst@example.test",
            "--case", "REPLACE_WITH_CASE_UUID",
        ],
    )
    async with Client(server) as client:
        result = await client.call_tool("review_discrepancies", {})
        print(client.protocol_version, result.structured_content)

asyncio.run(main())
```

<div dir="rtl" lang="ar">
<p><strong>Checkpoint</strong> · نفس الحالة ونفس الفروق. الـ <bdi>-T</bdi> بيقفل الـ <bdi>TTY</bdi> عشان مخرجات العملية تبقى رسائل <bdi>MCP</bdi>. مسار <bdi>stdio</bdi> ده محتاج صلاحية مشغّل محلي.</p>
</div>

## 4. Queue a write, then approve it

<div dir="rtl" lang="ar">
<p>كـ <bdi>analyst</bdi> استخدم نفس <bdi>token</bdi> والـ <bdi>revision</bdi> اللي ظهر في النتيجة. بدّل <bdi>6</bdi> لو النسخة عندك مختلفة. استخدم نسخة أدلة مفيهاش اقتراح مفتوح أو قرار معتمد.</p>
</div>

```sh
docker compose exec app python -m app.cli mcp-inspect --propose follow_up --revision 6 --note "Verify received quantity with the warehouse."
```

<div dir="rtl" lang="ar">
<p><strong>Checkpoint</strong> · الأداة ترجع <bdi>pending</bdi>، والـ <bdi>analyst</bdi> ما عندوش زر موافقة. افتح نفس الحالة بحساب <bdi>reviewer</bdi> مختلف وسجّل قرار. في تمرين تاني، ارفع مستند بعد عمل اقتراح وقبل الموافقة. الاقتراح القديم يبقى <bdi>Stale</bdi> والسيرفر يرفض الموافقة على نسخته.</p>
</div>

## 5. Check scope, revocation and schemas

<div dir="rtl" lang="ar">
<p>شغّل اختبار الـ <bdi>Streamable HTTP</bdi> على قاعدة اختبار. اقرا <bdi>tests/test_integration.py</bdi> وشوف إزاي عميلين متزامنين بيرجعوا حالتين مختلفتين. بعدين اضغط <bdi>Revoke my tokens</bdi> وجرّب نفس الـ <bdi>token</bdi> القديم.</p>
<p><strong>Checkpoint</strong> · طلب الـ <bdi>document ID</bdi> من حالة مختلفة بيرجع <bdi>tool error</bdi>، والـ <bdi>token</bdi> الملغي بيرجع <bdi>401</bdi>. أضف أداة قراءة ترجع عدد الملفات في كل <bdi>status</bdi> باستخدام نوع نتيجة محدد. تأكد إن <bdi>list_tools</bdi> بيعرض الـ <bdi>input_schema</bdi> والـ <bdi>output_schema</bdi> وإن الـ <bdi>Activity</bdi> فيه أثر الاستدعاء.</p>
</div>

```sh
uv run --locked python -m pytest -q -k streamable
```

## 6. Trace a Hugging Face tool call

<div dir="rtl" lang="ar">
<p>ابدأ بالاختبار الوهمي اللي بيرجع <bdi>tool_calls</bdi>. تتبّع <bdi>app/assistant.py</bdi> من <bdi>list_tools</bdi>، لتنفيذ الأداة، لحد رسالة <bdi>role=tool</bdi> المربوطة بنفس <bdi>tool_call_id</bdi>. غيّر كود المصدر في نتيجة الموديل لرمز مش موجود وشوف التحقق بيرفضه.</p>
<p><strong>Checkpoint</strong> · الـ <bdi>model</bdi> شايف أداتي قراءة بس. شغّل الاستدعاء الحقيقي اختياريًا بعد إعداد <bdi>HF_TOKEN</bdi> والموافقة على إرسال الأدلة الصناعية. النتيجة تبقى <bdi>AI commentary</bdi> ومعاها مراجع من الأداة، والـ <bdi>Activity</bdi> يظهر فيه بداية التحقيق واستدعاء الأداة ونهايته.</p>
</div>

```sh
uv run --locked python -m pytest -q -k hf_provider
```
