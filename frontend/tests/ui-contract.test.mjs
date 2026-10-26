import test from 'node:test'
import { invalidateTargetQueries } from '../src/utils/queryInvalidation.ts'
import assert from 'node:assert/strict'
import { findDatabaseMatches } from '../src/utils/databaseSearch.ts'
import { formatUptime } from '../src/utils/metricFormat.ts'
import { themeTokens, motionTokens } from '../packages/project-ui/src/tokens.ts'
const luminance = color => {
 const values = color.slice(1).match(/../g).map(part => parseInt(part,16)/255).map(v => v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4)
 return values[0]*0.2126+values[1]*0.7152+values[2]*0.0722
}
const contrast=(a,b)=>(Math.max(luminance(a),luminance(b))+0.05)/(Math.min(luminance(a),luminance(b))+0.05)
const blend=(foreground,background,opacity)=>'#'+foreground.slice(1).match(/../g).map((part,i)=>Math.round(parseInt(part,16)*opacity+parseInt(background.slice(1+i*2,3+i*2),16)*(1-opacity)).toString(16).padStart(2,'0')).join('')
test('every palette keeps body text, metadata and action labels readable in both modes',()=>{
 for(const [palette,modes] of Object.entries(themeTokens)) for(const [mode,tokens] of Object.entries(modes)) {
  for(const background of [tokens.bg,tokens.panel,tokens.panel2]) for(const foreground of [tokens.text,tokens.muted]) assert.ok(contrast(foreground,background)>=4.5,`${palette}/${mode} text contrast`)
  assert.ok(contrast(tokens.accent,tokens['accent-ink'])>=4.5,`${palette}/${mode} button contrast`)
  for(const background of [tokens.bg,tokens.panel,tokens.panel2]) {
   const selected=blend(tokens.accent,background,.12)
   const engine=blend(tokens.accent,blend(tokens.accent,background,.08),.11)
   for(const surface of [selected,engine])assert.ok(contrast(tokens.accent,surface)>=4.5,`${palette}/${mode} selected label contrast on ${surface}`)
   assert.ok(contrast(tokens.focus,background)>=3,`${palette}/${mode} field boundary and focus contrast`)
  }
 }
})
test('UI feedback is bounded and cannot introduce long blocking transitions',()=>{
 assert.ok(motionTokens.fast>0 && motionTokens.fast<=motionTokens.state)
 assert.ok(motionTokens.state<=200 && motionTokens.panel<=200)
})

test('uptime preserves a real zero and uses the same compact units across monitors',()=>{ assert.equal(formatUptime(0),'0m'); assert.equal(formatUptime(90061),'1d 1h'); assert.equal(formatUptime(null),'N/A') })

test('database search shares substring, case and whitespace behavior across engines and targets',()=>{
 const source={'pg-a':['app','Orders_history'],'pg-b':['orders','logs']}
 assert.deepEqual(findDatabaseMatches(source,'  ORDERs  '),[{targetName:'pg-a',dbName:'Orders_history'},{targetName:'pg-b',dbName:'orders'}])
 assert.deepEqual(findDatabaseMatches(source,'absent'),[])
 assert.deepEqual(findDatabaseMatches(source,'  '),[])
})

test('PostgreSQL refresh invalidates lazy catalogs and roles as well as the overview',()=>{
 const keys=[];invalidateTargetQueries({invalidateQueries: ({queryKey})=>{keys.push(queryKey);return Promise.resolve()}},'postgresql','fixture')
 for(const prefix of ['postgresql-target','postgresql-catalog','postgresql-roles']) assert.ok(keys.some(key=>key[0]===prefix&&key[1]==='fixture'))
})

test('RabbitMQ purge retains Unacked and never removes the queue snapshot',async()=>{
 const {applyQueueActionSnapshot,rabbitQueueKey}=await import('../src/utils/rabbitQueueState.ts')
 const queue={name:'jobs',vhost:'/',messages_ready:10,messages_unacknowledged:3,messages:13}
 const cluster={queues:[queue],overview:{messages_ready:10,messages_unacknowledged:3,messages:13,queues:1}}
 const purge=applyQueueActionSnapshot(cluster,queue,'purge')
 assert.equal(purge.queues.length,1);assert.equal(purge.queues[0].messages_ready,0)
 assert.equal(purge.queues[0].messages_unacknowledged,3);assert.equal(purge.queues[0].messages,3)
 assert.equal(purge.overview.messages_unacknowledged,3)
 const deleted=applyQueueActionSnapshot(cluster,queue,'delete')
 assert.equal(deleted.queues.length,0);assert.equal(deleted.overview.queues,0)
 assert.notEqual(rabbitQueueKey({vhost:'a:b',name:'c'}),rabbitQueueKey({vhost:'a',name:'b:c'}))
})

test('status presentation keeps unavailable measurements neutral and preserves real warnings',async()=>{
 const {monitorStatus}=await import('../src/utils/monitorStatus.ts')
 for(const status of [undefined,null,'','UNKNOWN','N/A','—'])assert.deepEqual(monitorStatus(status),{label:'UNKNOWN',tone:'neutral'})
 assert.deepEqual(monitorStatus('RUNNING'),{label:'UP',tone:'success'})
 assert.deepEqual(monitorStatus('UNREACHABLE'),{label:'DOWN',tone:'error'})
 assert.deepEqual(monitorStatus('DEGRADED'),{label:'DEGRADED',tone:'warning'})
 assert.deepEqual(monitorStatus('MAINTANENCE'),{label:'MAINTENANCE',tone:'warning'})
 assert.deepEqual(monitorStatus('new-engine-state'),{label:'NEW-ENGINE-STATE',tone:'neutral'})
})

test('attention banner distinguishes missing telemetry, partial collection and unavailable nodes',async()=>{
 const {attentionStatus}=await import('../src/utils/monitorStatus.ts')
 assert.equal(attentionStatus('no_data').label,'NO DATA')
 assert.equal(attentionStatus('no_data').tone,'neutral')
 assert.equal(attentionStatus('DEGRADED').tone,'warning')
 assert.equal(attentionStatus('DOWN').tone,'error')
 assert.match(attentionStatus('no_data').description,/не подтверждает недоступность/)
 assert.match(attentionStatus('DEGRADED').description,/не обязательно отказ/)
})

test('shared metric formatting preserves zero, missing data, capacity rounding and technical units',async()=>{
 const {formatBytes,formatBytesRate,formatRate}=await import('../src/utils/metricFormat.ts')
 assert.equal(formatBytes(1536),'1,5 KB')
 assert.equal(formatBytes(1024**3*1.1,{maximumFractionDigits:0,roundUp:true}),'2 GB')
 assert.equal(formatBytes(0),'0 B')
 assert.equal(formatRate(0),'0/s')
 assert.equal(formatBytesRate(1536),'1,5 KB/s')
 for(const value of [null,undefined,NaN,Infinity]){
  assert.equal(formatBytes(value),'N/A');assert.equal(formatBytesRate(value),'N/A');assert.equal(formatRate(value),'N/A')
 }
})
