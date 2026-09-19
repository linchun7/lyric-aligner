const assert = require('node:assert/strict');
const {abSegments,createPlayback,neighborStartMs,boundaryInputsValid} = require('./gap_review_ui.js');

async function run() {
  assert.deepEqual(abSegments(4,6,1.5,10),[[2.5,4],[6,7.5]]);
  assert.deepEqual(abSegments(0.2,9.8,1.5,10),[[0,0.2],[9.8,10]]);
  const live=new Map([['next',{origin:1000,values:{start:3}}]]);
  assert.equal(neighborStartMs({review_id:'next',start_ms:3500},live),4000);
  live.get('next').values.start=4;
  assert.equal(neighborStartMs({review_id:'next',start_ms:3500},live),5000);
  assert.equal(neighborStartMs({review_id:null,start_ms:3500},live),3500);
  const fields={start:{value:'1',valueAsNumber:1},end:{value:'2',valueAsNumber:2}};
  assert.equal(boundaryInputsValid(fields,{start:1,end:2},10),true);
  fields.start={value:'',valueAsNumber:NaN};
  assert.equal(boundaryInputsValid(fields,{start:1,end:2},10),false);
  fields.start={value:'3',valueAsNumber:3};
  assert.equal(boundaryInputsValid(fields,{start:1,end:2},10),false);
  const origin = 1000.9977324263039;
  const fractional = {start:(1200-origin)/1000,end:(2300-origin)/1000};
  const displayed = Object.fromEntries(Object.entries(fractional).map(([k,v])=>[k,{value:v.toFixed(3),valueAsNumber:Number(v.toFixed(3))}]));
  assert.equal(boundaryInputsValid(displayed,fractional,10),true,'untouched 44.1 kHz origins must export');
  assert.equal(Math.round(origin+fractional.start*1000),1200,'original absolute boundary is preserved');
  const pending = new Map(); let id=0;
  const clock = {setTimeout(fn,ms){pending.set(++id,{fn,ms});return id;},clearTimeout(i){pending.delete(i);}};
  const calls=[], states=[];
  const audio={currentTime:0,ended:false,pause(){calls.push(['pause',this.currentTime]);},
    async play(){calls.push(['play',this.currentTime]);}};
  const playback=createPlayback(audio,s=>states.push(s),clock);
  async function fire() {
    const [key,entry]=pending.entries().next().value;
    pending.delete(key); await entry.fn(); await Promise.resolve();
    return entry.ms;
  }
  await playback.play(abSegments(4,6,1.5,10),0.4);
  assert.deepEqual(calls.filter(c=>c[0]==='play'),[['play',2.5]]);
  audio.currentTime=4; await fire();
  assert.equal(states.at(-1),'A / B 间停顿');
  assert.equal(await fire(),400);
  assert.deepEqual(calls.filter(c=>c[0]==='play'),[['play',2.5],['play',6]]);
  audio.currentTime=7.5;await fire();
  assert.equal(states.at(-1),'试听完成');
  await playback.play([[1,2],[3,4]],0.5);
  audio.currentTime=2;await fire();
  const count=calls.filter(c=>c[0]==='play').length;
  playback.cancel();
  assert.equal(pending.size,0);
  assert.equal(calls.filter(c=>c[0]==='play').length,count);
  const denied=createPlayback({currentTime:0,pause(){},async play(){throw Error('audio unavailable');}},s=>states.push(s),clock);
  await denied.play([[0,1]],0);
  assert.match(states.at(-1),/audio unavailable/);
  console.log('A/B playback, context bounds, pause, cancellation and error checks passed');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
