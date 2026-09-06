// Runtime fixture loads the real .ts extensions with Node native type stripping.
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { EventEmitter } from 'node:events';
const nativeTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...args) => nativeTimeout(fn, ms === 15000 ? 15 : ms, ...args);
let response = 'allow';
let invoked = [];
childProcess.spawn = (python, args, options) => {
  assert.equal(options.windowsHide, true);
  invoked.push(args[0]);
  if (response === 'spawn') throw Error('fixture');
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stdin = new EventEmitter();
  child.kill = () => { queueMicrotask(() => child.emit('close', -1)); };
  child.stdin.end = () => {
    if (response === 'timeout') return;
    queueMicrotask(() => {
      if (response === 'stdin') return child.stdin.emit('error', Error('fixture'));
      const output = args.includes('compress') ? JSON.stringify({wrap: true, text: 'COMPRESSED'})
        : response === 'malformed' ? '{}'
        : JSON.stringify({fleet_policy:1, decision:response, message:response === 'allow' ? '' : 'fixture advisory'});
      child.stdout.emit('data', output);
      child.emit('close', response === 'exit' ? 1 : 0);
    });
  };
  return child;
};
syncBuiltinESMExports();
const policy = await import('../pi/extensions/policy_hooks.ts');
const compression = await import('../pi/extensions/context_filter.ts');
const lifecycle = await import('../pi/extensions/session_state.ts');
const ctx = {cwd: process.cwd(), sessionManager:{getSessionId:()=> 'synthetic'}};
const event = {type:'tool_call',toolName:'bash',toolCallId:'a',input:{command:'echo harmless'}};
for (const failure of ['spawn','timeout','stdin','exit','malformed']) {
 response=failure;
 const result=await policy.runGuard('venv_discipline', JSON.stringify(event));
 assert.equal(result.decision,'block',failure);
 assert.match(result.message,/unavailable.*not verified/,failure);
}
process.env.FLEET_CONTEXT_FILTER_MODE='rewrite';
for (const extensions of [[policy,compression,lifecycle],[compression,lifecycle,policy]]) {
 const handlers=new Map();
 const api={on:(name,handler)=>handlers.set(name,[...(handlers.get(name)??[]),handler])};
 for(const extension of extensions) extension.default(api);
 const emit=async(name,event)=>{
   let current={...event};
   for(const handler of handlers.get(name)??[]) {
     const patch=await handler(current,ctx);
     if(patch?.block) return patch;
     if(patch) current={...current,...patch};
   }
   return current;
 };
 response='block';
 assert.equal((await emit('tool_call',event)).block,true);
 response='allow';
 assert.equal((await emit('tool_call',event)).block,undefined);
 assert.equal((await emit('tool_call',{...event,toolName:'custom'})).block,true);
 response='warn';
 await emit('tool_call',event);
 response='allow';
 const content=[{type:'text',text:'original'},{type:'image',data:'fixture',mimeType:'image/png'}];
 const result=await emit('tool_result',{...event,type:'tool_result',content,details:{exitCode:7},isError:true});
 assert.equal(result.isError,true);
 assert.deepEqual(result.details,{exitCode:7});
 assert.equal(result.content[0].text,'COMPRESSED');
 assert(result.content.some(block=>block.text?.startsWith(policy.WARNING_PREFIX)));
 assert(result.content.some(block=>block.type==='image'));
 const next=await emit('tool_result',{...event,toolCallId:'b',content:[],isError:false});
 assert.equal(next.content.length,0,'warning leaked across call ids');
 response='exit';
 const unresolved=await emit('tool_call',{...event,toolName:'edit',input:{path:null}});
 assert.equal(unresolved.block,true);
 assert.match(unresolved.reason,/target resolution unavailable/);
 const failed=await emit('tool_result',{...event,toolName:'edit',content:[{type:'text',text:'original'}],isError:true});
 assert.equal(failed.content[0].text,'original');
 assert.equal(failed.isError,true);
 assert(failed.content.some(block=>block.text?.includes('not verified')));
 response='allow';
 for(const name of ['input','agent_settled','session_shutdown']) await emit(name,{});
 assert(invoked.some(path=>path.endsWith('session_state_pi.py')));
}
console.log('Pi middleware fixtures PASS (failures, blocking, warning isolation, both composition orders, lifecycle)');
