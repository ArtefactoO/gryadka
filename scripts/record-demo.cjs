// Capture synthetic API fixtures in the real frontend. No live login or target operations.
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.DEMO_URL || 'http://127.0.0.1:4187';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw Error('Use a loopback preview');
const output = process.env.DEMO_OUTPUT;
if (!output) throw Error('Set DEMO_OUTPUT');
fs.mkdirSync(output, {recursive:true});
async function run(setup, story) {
 const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_EXECUTABLE_PATH || undefined, args:['--no-sandbox']});
 try {
  const context = await browser.newContext({viewport:{width:1360,height:860},colorScheme:'dark',reducedMotion:'reduce'});
  await context.route('**/*', r => r.request().url().startsWith(base + '/') ? r.continue() : r.abort());
  await setup(context);
  const page = await context.newPage(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  let n = 0;
  const frame = async () => {await page.waitForTimeout(650); await page.screenshot({path:path.join(output, String(n++).padStart(2,'0')+'.png')});};
  await story(page, frame);
  fs.writeFileSync(path.join(output,'capture.json'), JSON.stringify({frames:n,errors},null,2));
  if(errors.length) throw Error(errors.join('\n'));
 } finally { await browser.close(); }
}
const {mongo,redis,rabbit,pg,pgOther,kafka,redpanda,targets,user} = require('../frontend/tests/monitor-fixtures.cjs');
pg.nodes[0].databases = pg.nodes[0].databases.filter(d=>['app','analytics'].includes(d.name));
run(async context=>{
 await context.addInitScript(user=>{localStorage.setItem('gryadka-ui',JSON.stringify({mode:'dark',palette:'signal'}));localStorage.setItem('auth_token','synthetic-ui-fixture');localStorage.setItem('auth_user',JSON.stringify(user));},user);
 await context.route('**/api/**',r=>{
  const p=new URL(r.request().url()).pathname;let data={};
  if(p==='/api/auth/me')data=user;
  else if(p==='/api/targets')data=targets;
  else if(p==='/api/targets/unreachable/list')data=[];
  else if(p==='/api/meta'||p.endsWith('/version'))data={version:'demo'};
  else if(p.endsWith('/changelog'))data={content:'Synthetic demonstration'};
  else if(p==='/api/clusters')data=['mongo-demo'];
  else if(p==='/api/clusters/mongo-demo')data=mongo;
  else if(p==='/api/clusters/databases-overview')data={'mongo-demo':['app','analytics']};
  else if(p==='/api/postgresql/databases-overview')data={'pg-demo':pg.nodes[0].databases.map(x=>x.name),'pg-other':['ops_orders']};
  else if(p==='/api/postgresql/pg-other')data=pgOther;
  else if(p==='/api/postgresql/pg-demo')data=pg;
  else if(p.startsWith('/api/redis/'))data=redis;
  else if(p.startsWith('/api/rabbitmq/'))data=rabbit;
  else if(p.startsWith('/api/kafka/'))data=kafka;
  else if(p.startsWith('/api/redpanda/'))data=redpanda;
  else if(p.endsWith('/catalog'))data={database:'app',tables:[{schema:'public',name:'orders',kind:'r',estimated_rows:123456,table_bytes:800000000,index_bytes:130000000}],indexes:[{schema:'public',table_name:'orders',name:'orders_pkey',is_unique:true,is_primary:true,is_valid:true,size_bytes:130000000,columns:['id']}],truncated:false};
  if(p==='/api/events/stream')return r.fulfill({contentType:'text/event-stream',body:': demo\n\n'});
  return r.fulfill({json:data});
 });
},async(page,frame)=>{
 await page.goto(base+'/?type=mongodb&target=mongo-demo');await page.getByRole('heading',{name:'Nodes',exact:true}).waitFor();await frame();
 await page.goto(base+'/?type=postgresql&target=pg-demo');await page.getByRole('heading',{name:'Nodes',exact:true}).waitFor();await frame();
 await page.getByRole('button',{name:'app',exact:true}).click();await frame();
 await page.goto(base+'/?type=kafka&target=kafka-demo');await page.getByRole('heading',{name:'Nodes',exact:true}).waitFor();await frame();
 await page.goto(base+'/?type=mongodb&target=mongo-demo');await page.getByRole('heading',{name:'Nodes',exact:true}).waitFor();await frame();
}).catch(e=>{console.error(e);process.exitCode=1;});
