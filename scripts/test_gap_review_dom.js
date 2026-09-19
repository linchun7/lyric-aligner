/* Exercise real DOM event handlers without loading media or a browser. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this._value='';this._text='';this.currentTime=0;this.ended=false;this.playCalls=[];}
  set value(v){this._value=String(v);} get value(){return this._value;}
  get valueAsNumber(){return this.value.trim()===''?NaN:Number(this.value);}
  set textContent(v){this._text=String(v);} get textContent(){return this._text;}
  append(e){this.children.push(e);}
  setAttribute(k,v){this[k]=v;}
  addEventListener(k,fn){(this.listeners[k]??=[]).push(fn);}
  dispatch(k){for(const fn of this.listeners[k]||[])fn({});}
  all(){return [this,...this.children.flatMap(c=>c.all())];}
  querySelector(selector){const value=/value="(.*?)"/.exec(selector)?.[1];return this.all().find(e=>e.tagName==='input'&&e.value===value);}
  getContext(){return {clearRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){}};}
  pause(){} async play(){this.playCalls.push(this.currentTime);}
}
const ids=Object.fromEntries(['cases','context','pause','step','step-presets','export','message','payload'].map(k=>[k,new Element(k)]));
ids.context.value='3';ids.pause.value='.4';ids.step.value='.1';
ids.payload.textContent=JSON.stringify({lock:{},cases:[{id:'one',track:'track',text:'phrase',lrc_indices:'1',clip_start_ms:0,clip_end_ms:60000,clip_sha256:'clip',audio:'test',peaks:[0.1]}],
  previous:{records:[{id:'one',clip_sha256:'clip',start_ms:1200,end_ms:3000,human_confirmed:true,presence:'present',note:''}]}});
const document={querySelector:s=>ids[s.slice(1)],createElement:t=>new Element(t),createTextNode:t=>{const e=new Element('text');e.textContent=t;return e;}};
const timers=new Map();let timer=0;
const context={document,module:{exports:{}},console,setTimeout:(fn)=>{timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id)};
vm.runInNewContext(fs.readFileSync(process.argv[2]||require.resolve('./gap_review_ui.js'),'utf8'),context);
const all=ids.cases.all(),numbers=all.filter(e=>e.tagName==='input'&&e.type==='number');
const start=numbers[0],audio=all.find(e=>e.tagName==='audio'),check=all.find(e=>e.type==='checkbox');
for(const value of ['1','12','12.','12.5']){
  start.value=value;start.dispatch('input');
  assert.equal(start.value,value,'typing must not reformat or move the caret');
}
assert.equal(audio.currentTime,12.5);assert.equal(check.checked,false);
const button=text=>{const b=all.find(e=>e.tagName==='button'&&e.textContent===text);assert.ok(b,text);return b;};
button('听到所选点结束').dispatch('click');assert.equal(audio.playCalls.at(-1),9.5);button('暂停').dispatch('click');
button('从所选点开始听').dispatch('click');assert.equal(audio.playCalls.at(-1),12.5);button('暂停').dispatch('click');
ids.step.value='0.5';ids.step.dispatch('input');
assert.equal(start.step,'0.5');button('向后一步 →').dispatch('click');assert.equal(Number(start.value),13);
const preset=ids['step-presets'].children.find(e=>e.textContent==='1 秒');preset.dispatch('click');
assert.equal(start.step,'1');button('← 向前一步').dispatch('click');assert.equal(Number(start.value),12);
start.value='';start.dispatch('input');ids.export.dispatch('click');assert.match(ids.message.textContent,/完整/);
console.log('DOM typing, editable steps, nudges, standalone boundary playback and invalid export passed');
