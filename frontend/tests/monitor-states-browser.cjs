// Local fixtures only: exercise failures without touching a live monitor.
const {chromium}=require(process.env.GRYADKA_PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs');const assert=require('assert/strict');
const {mongo,redis,rabbit,pg,pgOther,kafka,redpanda,targets,user,infra,now}=require('./monitor-fixtures.cjs');
const out=process.env.GRYADKA_BROWSER_ARTIFACTS;const base=process.env.GRYADKA_BROWSER_URL || 'http://127.0.0.1:4187';
if(!out||!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw Error('Local preview and artifact directory required');fs.mkdirSync(out,{recursive:true});
const es={name:'es-demo',overview:{status:'green',number_of_nodes:1,number_of_data_nodes:1,active_primary_shards:1,active_shards:1,unassigned_shards:0,total_docs:1,total_store_size_bytes:1024,cluster_name:'demo',version:'8.0'},nodes:[{node_id:'n0',name:'es-demo-0',role:'MASTER',docs_count:1,store_size_bytes:1024,infrastructure:infra}],last_update:now};
const emqx={name:'emqx-demo',overview:{status:'UP',nodes_total:1,nodes_up:1,connections:1,version:'5.8'},nodes:[{name:'emqx@demo',source:'api',status:'UP',connections:1,uptime_seconds:0,infrastructure:infra}],last_update:now};
const hpOverview={total_proxies:0,total_servers:0,servers_up:0,servers_down:0,version:'2.8'};
const haproxy={name:'haproxy-demo',overview:hpOverview,proxies:[],pods:[{url:'http://proxy.demo:80',label:'haproxy-demo-0',overview:hpOverview,proxies:[]}],last_update:now,restart_supported:false};
const rollout={name:'canary-demo',namespace:'demo',status:'Paused',strategy:'Canary',current_replicas:1,desired_replicas:1,available_replicas:1,current_revision:2,current_image:'fixture:v1',replicasets:[],canary_steps:[],k8s_pods:[]};
const extra=[['elasticsearch','es-demo'],['emqx','emqx-demo'],['haproxy','haproxy-demo'],['argo-rollout','argo-demo'],['redis','redis-other']].map(([type,name])=>({id:type+':'+name,type,name,status:'ready',last_update:now}));
const samples={mongodb:mongo,redis,rabbitmq:rabbit,postgresql:pg,kafka,redpanda,elasticsearch:es,emqx,haproxy,'argo-rollout':{rollouts:[rollout],deployments:[]}};
const report={cases:[],errors:[]};let phase='init';
(async()=>{const b=await chromium.launch({headless:true});try{
 for(const [width,mode] of [[1440,'dark'],[1440,'light'],[390,'dark']]){
  const ctx=await b.newContext({viewport:{width,height:1000},hasTouch:width===390,isMobile:width===390,reducedMotion:'reduce'});
  await ctx.addInitScript(({user,mode})=>{localStorage.setItem('auth_token','fixture-token');localStorage.setItem('auth_user',JSON.stringify(user));localStorage.setItem('gryadka-ui',JSON.stringify({mode,palette:'signal'}))},{user,mode});
  let failedType=null;let emptyType=null;let unknownRedpanda=false;let refreshFailure=false;let sentinelDown=false;let kafkaVersion=null;const refreshIds=[];
  await ctx.route('**/api/**',async route=>{const u=new URL(route.request().url());const path=u.pathname;let value={};let status=200;
   if(path==='/api/auth/me')value=user;
   else if(path==='/api/targets')value=[...targets,...extra];
   else if(path==='/api/targets/unreachable/list')value=[];
   else if(path==='/api/meta')value={version:'fixture'};
   else if(path==='/api/clusters/databases-overview')value={'mongo-demo':['app','analytics']};
   else if(path==='/api/postgresql/databases-overview')value={'pg-demo':['app','analytics'],'pg-other':['ops_orders']};
   else if(path.endsWith('/operations'))value=[];
   else if(path.endsWith('/refresh')){refreshIds.push(decodeURIComponent(path.split('/')[3]));status=refreshFailure?503:200;value=refreshFailure?{detail:'Обновление не удалось поставить в очередь.'}:{queued:true};}
   else if(path.startsWith('/api/argo_rollout/'))value=path.split('/').length>4?rollout:samples['argo-rollout'];
   else {const type=path.startsWith('/api/clusters/')?'mongodb':path.split('/')[2];value=structuredClone(samples[type]||{});
    if(type==='kafka'&&kafkaVersion){value.version_info=kafkaVersion.summary;value.nodes.forEach(node=>{node.version_info=kafkaVersion.node});}
    if(type===failedType){status=503;value={detail:'Временная ошибка fixture.'};}
    else if(type===emptyType){if(type==='redpanda')value.brokers=[];else value.nodes=[];}
    else if(type==='redpanda'&&unknownRedpanda)value.brokers[0].status=null;
    else if(type==='redis'&&sentinelDown){value.nodes[0].status='DOWN';value.nodes.push({...redis.nodes[0],name:'sentinel',kind:'SENTINEL',status:'DOWN'});}
   }
   if(path==='/api/events/stream')return route.fulfill({status:200,contentType:'text/event-stream',body:': fixture\n\n'});
   await route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  });
  const p=await ctx.newPage();await p.clock.install();p.on('pageerror',e=>report.errors.push({phase,type:e.name,message:e.message}));
  const open=async(type,name)=>{await p.goto(base+'/?type='+type+'&target='+name);await p.getByText('Nodes',{exact:true}).waitFor();};
  for(const [type,name] of [['mongodb','mongo-demo'],['rabbitmq','rabbit-demo'],['redis','redis-demo'],['postgresql','pg-demo'],['kafka','kafka-demo'],['redpanda','redpanda-demo']]){
   phase=type+'-spacing-'+width+'-'+mode;await open(type,name);
   const gap=await p.evaluate(()=>{const panel=document.querySelector('[role="tabpanel"]');const title=[...document.querySelectorAll('*')].find(e=>!e.children.length&&e.textContent==='Nodes');return title.parentElement.getBoundingClientRect().top-panel.getBoundingClientRect().bottom});assert.ok(gap>=23&&gap<=27);
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
   if(type==='kafka'){
    assert.equal(await p.getByText('Kafka · DEGRADED',{exact:true}).count(),0);await p.getByText('Cluster status: DEGRADED',{exact:true}).waitFor();
    await p.getByRole('tab',{name:'Topics',exact:true}).click();await p.getByText('Cluster status: DEGRADED',{exact:true}).waitFor();await p.getByText(/Lag доступен не для всех групп/).waitFor();
   }
   if(mode==='dark')await p.screenshot({path:out+'/'+type+'-nodes-'+width+'.png',fullPage:width!==390});
   report.cases.push({type,width,mode,nodesGap:Math.round(gap)});
  }
  for(const sample of [
   {name:'runtime',summary:{value:'5.7.2',source:'app_info',versions:['5.7.2'],complete:true},node:{value:'5.7.2',source:'app_info'},label:'Kafka Version: 5.7.2',tooltip:/Версия работающего брокера/},
   {name:'image',summary:{value:'5.7.2-custom-build',source:'container_image',versions:['5.7.2-custom-build'],complete:true},node:{value:'5.7.2-custom-build',source:'container_image',image:'registry.invalid/team/kafka:5.7.2-custom-build'},label:'Image Version: 5.7.2-custom-build',tooltip:/не подтверждает версию работающего процесса/},
   {name:'mixed',summary:{value:null,source:'app_info',versions:['5.7.2','5.8.0'],complete:true},node:{value:'5.7.2',source:'app_info'},label:'Kafka Version: Mixed',tooltip:/различаются/},
   {name:'partial',summary:{value:null,source:'app_info',versions:['5.7.2'],complete:false},node:{value:null,source:null,reason:'Метрики брокера не найдены.'},label:'Kafka Version: Partial',tooltip:/не для всех брокеров/},
   {name:'missing',summary:{value:null,source:null,versions:[],complete:false},node:{value:null,source:null},label:'Kafka Version: N/A',tooltip:/ApiVersions не определяет версию Kafka/},
  ]){
   phase='kafka-version-'+sample.name;kafkaVersion=sample;await open('kafka','kafka-demo');const badge=p.getByText(sample.label,{exact:true});await badge.waitFor();await badge.locator('..').focus();await p.getByRole('tooltip').filter({hasText:sample.tooltip}).waitFor();await p.keyboard.press('Tab');await p.getByText('Cluster status: DEGRADED',{exact:true}).waitFor();
   assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
   if(sample.name==='image')await p.screenshot({path:out+'/kafka-version-'+width+'-'+mode+'.png',fullPage:width!==390});
   report.cases.push({type:'kafka-version-'+sample.name,width,mode});
  }kafkaVersion=null;
  phase='unknown-redpanda';unknownRedpanda=true;await open('redpanda','redpanda-demo');const unknown=p.getByText('UNKNOWN',{exact:true});await unknown.waitFor();const colors=await unknown.locator('..').evaluate(el=>{const sample=document.createElement('span');sample.style.color='var(--sl-muted)';document.body.append(sample);const expected=getComputedStyle(sample).color;sample.remove();return {actual:getComputedStyle(el).color,expected}});assert.equal(colors.actual,colors.expected);
  unknownRedpanda=false;report.cases.push({type:'unknown-neutral',width,mode});
  for(const [type,name] of [['elasticsearch','es-demo'],['emqx','emqx-demo'],['haproxy','haproxy-demo']]){
   phase=type+'-cached-error';await open(type,name);const exp=p.getByRole('button',{name:/^Развернуть строку/}).first();await exp.click();assert.equal(await p.getByRole('button',{name:/^Свернуть строку/}).first().getAttribute('aria-expanded'),'true');failedType=type;await p.clock.runFor(45000);await p.getByText(/Временная ошибка fixture/).waitFor();assert.equal(await p.getByText('Nodes',{exact:true}).count(),1);assert.equal(await p.getByRole('button',{name:/^Свернуть строку/}).first().getAttribute('aria-expanded'),'true');failedType=null;report.cases.push({type:type+'-cached-state',width,mode});
  }
  phase='refresh-error';refreshFailure=true;await open('redis','redis-demo');await p.getByRole('button',{name:'Обновить данные текущего монитора',exact:true}).click();await p.getByText('Обновление не удалось поставить в очередь.',{exact:true}).waitFor();await p.getByRole('tab',{name:'redis-other',exact:true}).click();assert.equal(await p.getByText('Обновление не удалось поставить в очередь.',{exact:true}).count(),0);assert.ok(refreshIds.includes('redis:redis-demo'));refreshFailure=false;report.cases.push({type:'refresh-error-scoped',width,mode});
  for(const [type,name] of [['mongodb','mongo-demo'],['rabbitmq','rabbit-demo'],['redis','redis-demo'],['postgresql','pg-demo'],['kafka','kafka-demo'],['redpanda','redpanda-demo']]){phase=type+'-empty';emptyType=type;await open(type,name);await p.getByText(type==='kafka'?'Брокеры не получены из метаданных Kafka.':'Сведения об узлах не получены.',{exact:true}).waitFor();emptyType=null;report.cases.push({type:type+'-empty',width,mode});}
  phase='redis-and-sentinel-down';sentinelDown=true;await open('redis','redis-demo');const downColor=await p.getByText('DOWN',{exact:true}).first().locator('..').evaluate(el=>getComputedStyle(el).color);const errorColor=await p.evaluate(()=>{const x=document.createElement('span');x.style.color='var(--sl-danger)';document.body.append(x);const c=getComputedStyle(x).color;x.remove();return c});assert.equal(downColor,errorColor);sentinelDown=false;report.cases.push({type:'redis-down-not-downgraded',width,mode});
  phase='argo-actions';await p.goto(base+'/?type=argo-rollout&target=argo-demo');await p.getByRole('button',{name:'Развернуть Rollout canary-demo',exact:true}).click();await p.getByRole('button',{name:'Продолжить развертывание',exact:true}).waitFor();await p.getByRole('button',{name:'Завершить развертывание без оставшихся этапов',exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'Promote',exact:true}).count(),0);report.cases.push({type:'argo-action-copy',width,mode});
  await ctx.close();console.log(JSON.stringify({width,mode,cases:report.cases.length}));
 }assert.equal(report.errors.length,0);report.success=true;
}catch(e){report.success=false;report.phase=phase;report.errorType=e.name;report.errorSummary=e.message.split('\n')[0];}finally{await b.close();fs.writeFileSync(out+'/states-report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));}if(!report.success)process.exitCode=1;})();
