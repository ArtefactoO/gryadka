// Synthetic API fixtures only. No real login, key reads or queue mutations.
const { chromium } = require(process.env.GRYADKA_PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const out=process.env.GRYADKA_BROWSER_ARTIFACTS;
if(!out)throw Error('Set GRYADKA_BROWSER_ARTIFACTS to a writable output directory');
fs.mkdirSync(out,{recursive:true});
const base=process.env.GRYADKA_BROWSER_URL || 'http://127.0.0.1:4187';
if(!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Only a local preview is allowed');
const {mongo,redis,rabbit,pg,pgOther,kafka,redpanda,targets,user}=require('./monitor-fixtures.cjs');
(async()=>{
 const browser=await chromium.launch({headless:true});const report=[];const errors=[];
 for(const width of [1440,390]) for(const mode of ['dark','light']) {
  const context=await browser.newContext({viewport:{width,height:1000},hasTouch:width===390,isMobile:width===390,reducedMotion:'reduce'});
  await context.addInitScript(({mode,user})=>{if(!localStorage.getItem('gryadka-ui'))localStorage.setItem('gryadka-ui',JSON.stringify({mode,palette:'signal'}));localStorage.setItem('auth_token','fixture-token');localStorage.setItem('auth_user',JSON.stringify(user));},{mode,user});
  let failRefresh=false;let redisData=structuredClone(redis);let failedTargets=0;let failedRedis=0;let catalogRequests=0;let mongoIndexRequests=0;
  await context.route('**/api/**',async route=>{
   const path=new URL(route.request().url()).pathname;let value={};let status=200;
   if(path==='/api/auth/me') value=user;
   else if(path==='/api/targets') value=targets;
   else if(path==='/api/targets/unreachable/list') value=[];
   else if(path==='/api/meta'||path.endsWith('/version'))value={version:'local-preview'};
   else if(path.endsWith('/changelog'))value={content:'Local preview'};
   else if(path==='/api/clusters')value=['mongo-demo'];
   else if(path==='/api/clusters/mongo-demo')value=mongo;
   else if(path==='/api/clusters/mongo-demo/databases/app/collections')value=[{name:'orders',count:123456,size:8*1024**3,indexes_count:1}];
   else if(path==='/api/clusters/mongo-demo/databases/app/collections/orders/indexes'){mongoIndexRequests++;value=[{name:'orders_pkey',key:'id:1',unique:true}];}
   else if(path.startsWith('/api/redis/'))value=redis;
   else if(path==='/api/postgresql/databases-overview')value={'pg-demo':pg.nodes[0].databases.map(db=>db.name),'pg-other':['ops_orders']};
   else if(path==='/api/postgresql/pg-other')value=pgOther;
   else if(path==='/api/postgresql/pg-demo')value=pg;
   else if(path.endsWith('/catalog') && new URL(route.request().url()).searchParams.get('database')==='analytics'){status=403;value={detail:'Нет доступа к базе analytics. Используйте существующие права мониторинга.'};}
   else if(path.endsWith('/catalog'))value={database:'app',tables:[{schema:'public',name:'orders',kind:'r',estimated_rows:123456,table_bytes:8*1024**3,index_bytes:1.3*1024**3}],indexes:[{schema:'public',table_name:'orders',name:'orders_pkey',is_unique:true,is_primary:true,is_valid:true,size_bytes:1.3*1024**3,columns:['id']}],truncated:false};
   else if(path.endsWith('/roles'))value={roles:[{name:'gryadka',login:true,superuser:false,create_database:false,create_role:false,connection_limit:-1,memberships:['pg_read_all_stats']}],truncated:false};
   else if(path.endsWith('/hba'))value={nodes:[{node:'pg-demo',rules:[{line_number:1,type:'hostssl',database:['app'],user_name:['gryadka'],address:'10.0.0.0',netmask:'255.0.0.0',auth_method:'scram-sha-256',invalid:false}]}]};
   else if(path.startsWith('/api/kafka/'))value=kafka;
   else if(path.startsWith('/api/rabbitmq/'))value=rabbit;
   else if(path.startsWith('/api/redpanda/'))value=redpanda;
   else if(path==='/api/clusters/databases-overview')value={'mongo-demo':['app','analytics']};
   if(failRefresh&&path==='/api/targets'){status=503;value={detail:'Список мониторов временно недоступен.'};failedTargets++;}
   if(path==='/api/redis/redis-demo'){
    value=redisData;
    if(failRefresh){status=503;value={detail:'Redis временно недоступен.'};failedRedis++;}
   }
   if(path==='/api/redis/redis-demo/keys'){status=403;value={detail:'Поиск ключей недоступен: учетной записи Redis не разрешены SCAN, TYPE, TTL или выбор базы.'};}
   if(path.endsWith('/catalog'))catalogRequests++;
   if(path==='/api/events/stream')return route.fulfill({status:200,contentType:'text/event-stream',body:': fixture\n\n'});
   return route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  });
  const page=await context.newPage();await page.clock.install();page.on('pageerror',e=>{errors.push(e.message);console.log('PAGEERROR',e.message)});
  for(const [type,target] of [['mongodb','mongo-demo'],['postgresql','pg-demo'],['kafka','kafka-demo'],['redis','redis-demo'],['rabbitmq','rabbit-demo'],['redpanda','redpanda-demo']]) {
   console.log('CASE',width,mode,type);
   await page.goto(`${base}/?type=${type}&target=${target}`);await page.waitForTimeout(550);
   await page.getByRole('heading',{name:'Nodes',exact:true}).waitFor();
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
   if(overflow)throw Error('page overflow '+type+'/'+width+'/'+mode);
   if(['mongodb','redis'].includes(type)) {
    if(await page.getByText('Pod',{exact:true}).count())throw Error('placement badge repeated in a resource row');
    if(!await page.getByRole('button',{name:'Kubernetes',exact:true}).count())throw Error('Kubernetes explanation missing beside Nodes');
    if(await page.getByText('Disk N/A',{exact:true}).count())throw Error('VM placeholder in Kubernetes monitor');
    await page.getByText('CPU 22m',{exact:true}).waitFor();
   }
   if(mode==='dark')await page.screenshot({path:`${out}/${type}-${width}-${mode}.png`,fullPage:width!==390});
   if(type==='mongodb') {
    await page.getByRole('tab',{name:'Collections',exact:true}).click();
    await page.getByRole('button',{name:/^Collections/}).click();
    await page.getByRole('button',{name:'app',exact:true}).click();await page.getByText('orders',{exact:true}).waitFor();
    if(mongoIndexRequests!==0)throw Error('MongoDB indexes eagerly fetched');
    await page.getByRole('button',{name:/^Indexes/}).click();await page.getByText('orders_pkey',{exact:true}).waitFor();
    if(mongoIndexRequests!==1)throw Error('MongoDB index query count mismatch');
    if(mode==='dark')await page.screenshot({path:`${out}/mongo-collections-${width}.png`,fullPage:width!==390});
    report.push({width,mode,type:'mongo-shared-cards',indexesLazy:true});
   }
   if(type==='postgresql') {
    if(await page.getByRole('combobox').count())throw Error('Legacy database select remains');
    if(catalogRequests!==0)throw Error('Catalog queried from database list');
    await page.getByRole('button',{name:'Поиск по базам PostgreSQL',exact:true}).click();
    await page.getByRole('textbox',{name:'Поиск по базам PostgreSQL',exact:true}).fill('OPS_ORD');
    await page.getByRole('button',{name:'pg-other → ops_orders',exact:true}).click();
    const selected=page.getByRole('button',{name:'ops_orders',exact:true});await selected.waitFor();
    await page.getByLabel('Статистика базы ops_orders',{exact:true}).waitFor();
    if(await selected.getAttribute('aria-pressed')!=='true')throw Error('PostgreSQL search did not select database chip');
    await page.getByRole('tab',{name:'pg-demo',exact:true}).click();await page.getByRole('button',{name:'Виртуальные машины',exact:true}).waitFor();
    await page.getByRole('button',{name:'app',exact:true}).click();await page.getByLabel('Статистика базы app',{exact:true}).waitFor();
    const panel=page.getByRole('tabpanel').first();
    if(await panel.getByRole('table').count())throw Error('Redundant database statistics table remains');
    if(mode==='dark')await page.screenshot({path:`${out}/postgresql-databases-${width}.png`,fullPage:width!==390});
    await page.getByRole('button',{name:'app',exact:true}).click();
    if(await page.getByLabel('Статистика базы app',{exact:true}).count())throw Error('Database details did not collapse');
    report.push({width,mode,type:'postgresql-search-and-database-chips',databaseCount:pg.nodes[0].databases.length});
    if(width===390){const sizes=await page.getByRole('tab').evaluateAll(els=>els.filter(e=>e.getBoundingClientRect().width>0).map(e=>({name:e.textContent,height:e.getBoundingClientRect().height})));if(sizes.some(e=>e.height<44))throw Error('touch tabs below44px '+JSON.stringify(sizes));}
    await page.getByRole('tab',{name:'Tables & Indexes',exact:true}).click();
    if(catalogRequests!==0)throw Error('Catalog queried before section expansion');
    await page.getByRole('button',{name:'Tables & Indexes',exact:true}).click();
    if(catalogRequests!==0)throw Error('All database catalogs eagerly queried');
    await page.getByRole('button',{name:'Каталог базы app',exact:true}).click();await page.getByText('public.orders',{exact:true}).waitFor();
    if(catalogRequests!==1)throw Error('One database expansion did not make exactly one catalog request');
    await page.getByRole('button',{name:/^Indexes/}).click();await page.getByText('orders_pkey',{exact:true}).waitFor();
    if(mode==='dark')await page.screenshot({path:`${out}/postgresql-catalog-${width}.png`,fullPage:width!==390});
    await page.getByRole('button',{name:'Поиск по базам и просмотренным таблицам',exact:true}).click();
    await page.getByRole('textbox',{name:'Поиск по базам и просмотренным таблицам',exact:true}).fill('ORDERS');
    await page.getByText('public.orders',{exact:true}).waitFor();if(catalogRequests!==1)throw Error('Table search queried unopened databases');
    await page.getByRole('textbox',{name:'Поиск по базам и просмотренным таблицам',exact:true}).fill('');
    await page.getByRole('button',{name:'Каталог базы analytics',exact:true}).click();await page.getByText('Нет доступа к базе analytics. Используйте существующие права мониторинга.',{exact:true}).waitFor();
    await page.getByRole('tab',{name:'Roles',exact:true}).click();await page.getByRole('cell',{name:'pg_read_all_stats',exact:true}).waitFor();
    await page.getByRole('tab',{name:'HBA',exact:true}).click();await page.getByText('scram-sha-256',{exact:true}).waitFor();
   }
   if(type==='redis') {
    await page.getByRole('tab',{name:'Keys',exact:true}).click();
    await page.getByRole('textbox',{name:'Шаблон ключа',exact:true}).fill('jobs:*');
    await page.getByRole('spinbutton',{name:'База',exact:true}).fill('3');
    await page.getByRole('button',{name:'Найти',exact:true}).click();
    await page.getByText(/Поиск ключей недоступен: учетной записи/).waitFor();
    const expand=page.getByRole('button',{name:/Развернуть строку redis/});await expand.click();
    await page.evaluate(()=>{window.fixtureSection=document.querySelector('[aria-label="redis-sections"]');});
    failRefresh=true;
    // Poll plus all query retries, with real HTTP settlement between virtual clock advances.
    for(let n=0;n<10;n++){await page.clock.runFor(5000);await page.waitForTimeout(40);}
    if(failedTargets<4||failedRedis<2)throw Error(`background failure was not exercised targets=${failedTargets} redis=${failedRedis}`);
    if(!await page.getByRole('tab',{name:'Keys',exact:true}).getAttribute('aria-selected').then(x=>x==='true'))throw Error('Redis tab reset during error');
    if(await page.getByRole('textbox',{name:'Шаблон ключа'}).inputValue()!=='jobs:*')throw Error('pattern reset during error');
    if(await page.getByRole('spinbutton',{name:'База'}).inputValue()!=='3')throw Error('DB reset during error');
    if(!await page.getByRole('button',{name:/Свернуть строку redis/}).count())throw Error('expanded node reset during error');
    failRefresh=false;redisData.nodes[0].redis_version='7.1';redisData.databases.push({name:'db4',keys:999,expires:0});
    await page.clock.runFor(31000);await page.waitForTimeout(100);
    await page.getByText('Redis Version: 7.1',{exact:true}).waitFor();
    await page.clock.resume();
    if(!await page.evaluate(()=>window.fixtureSection===document.querySelector('[aria-label="redis-sections"]')))throw Error('Redis sections remounted');
    if(await page.getByRole('spinbutton',{name:'База'}).inputValue()!=='3')throw Error('DB reset after default DB changed');
    if(await page.getByRole('textbox',{name:'Шаблон ключа'}).inputValue()!=='jobs:*')throw Error('pattern reset after reconnect');
    if(!await page.getByRole('button',{name:/Свернуть строку redis/}).count())throw Error('expanded node reset after recovery');
    if(mode==='dark')await page.screenshot({path:`${out}/redis-keys-${width}.png`,fullPage:width!==390});
    report.push({width,mode,type:'redis-background-error-recovery',failedTargets,failedRedis,statePreserved:true});
   }
   if(type==='rabbitmq') {
    await page.getByRole('tab',{name:'Queues',exact:true}).click();
    const row=page.getByRole('row').filter({has:page.getByText('jobs',{exact:true})});
    await row.getByRole('button',{name:'Удалить сообщения',exact:true}).click();
    let dialog=page.getByRole('dialog');await dialog.getByText(/Unacked/).first().waitFor();
    await dialog.getByRole('button',{name:'Отмена',exact:true}).click();
    await row.getByRole('button',{name:'Удалить очередь',exact:true}).click();
    dialog=page.getByRole('dialog');const confirm=dialog.getByRole('button',{name:'Удалить очередь',exact:true});
    if(!await confirm.isDisabled())throw Error('queue deletion lacks name confirmation');
    await dialog.getByRole('textbox').fill('wrong');if(!await confirm.isDisabled())throw Error('wrong queue name accepted');
    await dialog.getByRole('textbox').fill('jobs');if(await confirm.isDisabled())throw Error('exact queue name rejected');
    await dialog.getByRole('button',{name:'Отмена',exact:true}).click();
    const empty=page.getByRole('row').filter({has:page.getByText('empty-jobs',{exact:true})});
    if(!await empty.getByRole('button',{name:'Удалить сообщения',exact:true}).isDisabled())throw Error('empty queue purge enabled');
    if(mode==='dark')await page.screenshot({path:`${out}/rabbitmq-queues-${width}.png`,fullPage:width!==390});
   }
   if(type==='kafka'||type==='redpanda') {
    await page.getByRole('tab',{name:'Consumer Groups',exact:true}).click();await page.getByText('unknown-worker',{exact:true}).waitFor();
    await page.getByText('Недоступен',{exact:true}).waitFor();
    if(mode==='dark')await page.screenshot({path:`${out}/kafka-groups-${width}.png`,fullPage:width!==390});
   }
   report.push({width,mode,type,overflow:false});
  }
  const palette=page.getByRole('button',{name:'Выбрать цветовую систему',exact:true});await palette.click();
  await page.getByRole('menuitemradio',{name:'Cobalt',exact:true}).click();
  if(await page.evaluate(()=>document.documentElement.dataset.palette)!=='cobalt')throw Error('palette did not apply');
  const before=await page.evaluate(()=>document.documentElement.dataset.theme);
  await page.getByRole('button',{name:before==='dark'?'Включить светлую тему':'Включить темную тему',exact:true}).click();
  if(await page.evaluate(()=>document.documentElement.dataset.theme)===before)throw Error('mode did not toggle');
  await page.reload();await page.getByRole('button',{name:'Выбрать цветовую систему',exact:true}).waitFor();
  if(await page.evaluate(()=>document.documentElement.dataset.palette)!=='cobalt')throw Error('palette not persisted');
  await context.close();
 }
 await browser.close();fs.writeFileSync(out+'/visual-report.json',JSON.stringify({report,errors},null,2));
 if(errors.length)throw Error(errors.join('\n'));console.log(JSON.stringify({cases:report.length,pageErrors:errors.length}));
})().catch(e=>{console.error(e);process.exit(1)});
