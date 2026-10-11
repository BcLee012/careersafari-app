// Optional browser collection. No credentials or persistent browser profile.
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const [url, python] = process.argv.slice(2);
const path = require('node:path');
const checker = `import sys,json
from careersafari.security import assert_safe_url,allowlist_mode,is_domain_allowed
try:
 u=sys.stdin.read(); assert_safe_url(u)
 if allowlist_mode()=='enforce' and not is_domain_allowed(u)[0]: raise ValueError()
 print('ok')
except Exception: print('blocked')`;
function safe(u) {
 const r=spawnSync(python,['-c',checker],{input:u,encoding:'utf8',timeout:4000,
   cwd:path.resolve(__dirname,'..'),maxBuffer:10000});
 return r.status===0 && r.stdout.trim()==='ok';
}
(async()=>{
 let browser;
 try {
  if(!safe(url)) throw new Error('浏览器目标未通过来源检查');
  browser=await chromium.launch({headless:true,channel:'chrome'});
  const context=await browser.newContext({serviceWorkers:'block',acceptDownloads:false});
  const page=await context.newPage();
  let n=0, bytes=0, finalUrl=url;
  await context.route('**/*',async route=>{
   const req=route.request();
   if(++n>120 || ['image','media','font'].includes(req.resourceType())) return route.abort();
   let target=req.url();
   try {
    for(let hop=0;hop<=4;hop++){
     if(!safe(target)) return route.abort();
     // Prevent the browser transport from following an unchecked redirect.
     const response=await route.fetch({url:target,maxRedirects:0,timeout:8000});
     const headers=response.headers();
     if([301,302,303,307,308].includes(response.status()) && headers.location){
      target=new URL(headers.location,target).href; await response.dispose(); continue;
     }
     if(Number(headers['content-length']||0)>3*1024*1024){await response.dispose();return route.abort();}
     const body=await response.body();
     bytes+=body.length;
     if(body.length>3*1024*1024 || bytes>16*1024*1024){await response.dispose();return route.abort();}
     if(req.isNavigationRequest() && req.frame()===page.mainFrame()) finalUrl=target;
     await route.fulfill({response,body}); await response.dispose();return;
    }
    await route.abort();
   }catch {await route.abort().catch(()=>{});}
  });
  await page.goto(url,{waitUntil:'domcontentloaded',timeout:25000});
  // Wait for an actual job section, not a navigation-heavy shell.
  await page.waitForFunction(()=>/job description|responsibilities|岗位职责|职位描述|任职要求/i.test(document.body.innerText),null,{timeout:12000}).catch(()=>{});
  const html=await page.content();
  if(Buffer.byteLength(html)>3*1024*1024) throw new Error('浏览器页面超过体积上限');
  process.stdout.write(JSON.stringify({ok:true,html,final_url:finalUrl,network_requests:n}));
 }catch(e){
  const message=String(e.message||'');
  // Do not leak network response bodies or local environment values.
  process.stdout.write(JSON.stringify({ok:false,error:/未通过|体积/.test(message)?message:'浏览器未能完成招聘页面加载'}));
 }finally{if(browser) await browser.close();}
})().catch(()=>{process.stdout.write(JSON.stringify({ok:false,error:'浏览器采集运行失败'}));});
