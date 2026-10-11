// 使用现成 Playwright 运行页面检查；服务端为 ui_server 中的临时数据与固定模型响应。
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  const page = await browser.newPage({viewport:{width:1280,height:900}});
  const errors=[];
  page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto('http://127.0.0.1:8767');
    await page.waitForLoadState('networkidle');
    assert.equal(await page.locator('#f-stage').inputValue(), '实习');
    // 未操作能力项不能自动变成 3 星自述。
    assert.equal(await page.locator('.stars .on').count(), 0);
    await page.locator('.step').nth(1).click();
    const sql = page.locator('#skills-transferable .sk').filter({hasText:'SQL 取数'});
    await sql.locator('.ev-btn').click();
    await sql.locator('.ev-source').selectOption('practical');
    await sql.locator('.artifact-ref').fill('测试作品-1');
    await sql.locator('input[placeholder="做过什么、负责哪一步、产出是什么"]').fill('为课程项目编写 SQL 取数，并输出查询结果。');
    // 方向变化会重绘表单，材料与来源必须保留。
    await page.locator('.step').first().click();
    await page.locator('#f-dir').selectOption({label:'大数据分析与决策'});
    await page.locator('.step').nth(1).click();
    assert.equal(await sql.locator('.artifact-ref').inputValue(), '测试作品-1');
    assert.equal(await sql.locator('.ev-source').inputValue(), 'practical');
    await page.locator('.step').last().click();
    await page.getByRole('button', {name:'生成求职意向定义卡'}).click();
    await page.waitForResponse(r => r.url().endsWith('/api/profile') && r.status()===200);
    await page.locator('#card-out').getByText('岗位探索报告', {exact:true}).waitFor();
    const report = await page.locator('#card-out').innerText();
    assert.match(report, /数据分析 · 实习/);
    assert.match(report, /提供作品引用/);
    assert.doesNotMatch(report, /已验证覆盖率|\d+%/);
    assert.match(report, /O\*NET 31\.0/);
    assert.match(report, /美国职业参考/);
    await page.locator('#tab-jd').click();
    await page.locator('#positions .pos').first().waitFor();
    assert.match(await page.locator('#positions').innerText(), /通过自动检查/);
    // 无来源的输入应落入待确认，不弹出人工逐条入库按钮。
    await page.evaluate(() => localStorage.setItem('cs_token','test-only-token'));
    await page.evaluate(() => refreshJds());
    assert.match(await page.locator('#coverage-status').innerText(), /2 \/ 28/);
    assert.equal(await page.locator('#coverage-status table tr').count(), 29);
    await page.locator('#knowledge-family').selectOption('采购');
    await page.locator('#knowledge-question').fill('采购相关职业通常怎样选择和评估供应商？');
    const explanationResponse = page.waitForResponse(r=>r.url().endsWith('/api/knowledge/explain'));
    await page.locator('#knowledge-button').click();
    const explanation = await (await explanationResponse).json();
    assert.equal(explanation.status, 'supported');
    assert.equal(explanation.cached, true); // Previously checked real RAG answer; no live model in fixture.
    assert.match(await page.locator('#knowledge-result').innerText(), /ONET-31\.0:task_statements:/);
    assert.equal(await page.locator('#knowledge-button').isEnabled(), true);
    await page.locator('#jd-text').fill('岗位名称：测试数据分析实习。任职要求：熟悉 SQL 并具备沟通能力。工作职责：分析业务指标，输出分析报告。招聘阶段：实习。');
    await page.getByRole('button',{name:'自动检查并保存'}).click();
    await page.locator('#parse-out').getByText('已保存原文，进入待确认队列', {exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'确认入库', exact:true}).count(), 0);
    assert.match(await page.locator('#parse-out').innerText(), /缺少可追溯/);
    await page.evaluate(()=>Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{}))));
    await page.screenshot({path:'docs/evaluations/2026-10-11-coverage/browser.png', fullPage:true});
    assert.deepEqual(errors, []);
    console.log('页面检查通过：画像材料保留、岗位汇总报告、28项覆盖缺口、O*NET引用与有依据的问答、异常隔离，无页面错误。');
  } finally { await browser.close(); }
})().catch(e=>{ console.error(e); process.exit(1); });
