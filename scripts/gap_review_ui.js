/* Offline boundary editor. Playback never edits a boundary; user gestures do. */
function abSegments(pointA, pointB, context, duration) {
  return [[Math.max(0, pointA - context), Math.min(duration, pointA)],
          [Math.max(0, pointB), Math.min(duration, pointB + context)]];
}

function neighborStartMs(next, liveBoundaries) {
  const live = liveBoundaries.get(next.review_id);
  return live ? live.origin + live.values.start * 1000 : next.start_ms;
}

function boundaryInputsValid(fields, values, duration) {
  return ['start','end'].every(kind => fields[kind].value.trim() !== '' &&
    Number.isFinite(fields[kind].valueAsNumber) && Number.isFinite(values[kind]) &&
    values[kind] >= 0 && values[kind] <= duration &&
    // Display rounds to milliseconds; retain the exact absolute boundary when
    // the decoded clip starts on a fractional millisecond (e.g. 44.1 kHz).
    (Math.abs(fields[kind].valueAsNumber-values[kind]) < 1e-9 ||
     fields[kind].valueAsNumber === Number(values[kind].toFixed(3))));
}

function createPlayback(audio, onState, clock = globalThis) {
  let generation = 0;
  let timer = null;
  function cancel() {
    generation += 1;
    if (timer !== null) clock.clearTimeout(timer);
    timer = null;
    audio.pause();
    onState('已暂停');
  }
  async function play(parts, pauseSeconds) {
    cancel();
    const run = generation;
    async function next(index) {
      if (run !== generation) return;
      if (index >= parts.length) { onState('试听完成'); return; }
      const [start, end] = parts[index];
      if (end <= start) { await next(index + 1); return; }
      audio.currentTime = start;
      onState(parts.length > 1 ? (index === 0 ? 'A · 边界之前' : 'B · 边界之后') : '播放中');
      try { await audio.play(); }
      catch (error) { if (run === generation) onState('无法播放：' + error.message); return; }
      if (run !== generation) return;
      function tick() {
        if (run !== generation) return;
        if (audio.currentTime >= end || audio.ended) {
          audio.pause();
          if (index + 1 < parts.length) {
            onState('A / B 间停顿');
            timer = clock.setTimeout(() => next(index + 1), pauseSeconds * 1000);
          } else onState('试听完成');
        } else timer = clock.setTimeout(tick, 20);
      }
      tick();
    }
    await next(0);
  }
  return {cancel, play};
}

function initializeGapReview(payload) {
  const answers = [];
  const liveBoundaries = new Map();
  const neighborDisplays = [];
  let activeController = null;
  const contextInput = document.querySelector('#context');
  const pauseInput = document.querySelector('#pause');
  const stepInput = document.querySelector('#step');
  const stepTargets = [];
  function stepSeconds() {
    const value=Number(stepInput.value);
    return Number.isFinite(value) && value >= 0.001 && value <= 10 ? value : 0.1;
  }
  function updateStep() { stepTargets.forEach(input => { input.step=String(stepSeconds()); }); }
  stepInput.addEventListener('input',updateStep);
  for(const value of [0.01,0.05,0.1,0.5,1]) {
    const preset=document.createElement('button');preset.type='button';preset.textContent=value+' 秒';
    preset.addEventListener('click',()=>{stepInput.value=String(value);updateStep();});
    document.querySelector('#step-presets').append(preset);
  }
  const format = ms => {
    const total = Math.round(ms);
    return `${Math.floor(total / 60000)}:${String(Math.floor(total / 1000) % 60).padStart(2, '0')}.${String(total % 1000).padStart(3, '0')}`;
  };
  const element = (tag, text, parent) => {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    if (parent) parent.append(e);
    return e;
  };
  for (const c of payload.cases) {
    const article = element('article', undefined, document.querySelector('#cases'));
    element('h2', `${c.id} · ${c.track} · 歌词行 ${c.lrc_indices}`, article);
    element('p', c.text, article);
    const previous = payload.previous.records.find(r => r.id === c.id);
    const unchangedClip = previous.clip_sha256 === c.clip_sha256;
    element('p', unchangedClip && previous.human_confirmed ? '已保留上次确认；无需重复试听。' : '保留上次编辑值，尚未确认。此页可选使用，不是继续升级算法的前置条件。', article).className = 'hint';
    element('p', '上次备注：' + (previous.note || '无'), article);
    const audio = element('audio', undefined, article);
    audio.src = c.audio;
    audio.preload = 'metadata';
    const state = element('p', '待试听', article);
    state.setAttribute('role', 'status');
    const controller = createPlayback(audio, text => { state.textContent = text; });
    const duration = (c.clip_end_ms - c.clip_start_ms) / 1000;
    const values = {start: (previous.start_ms - c.clip_start_ms) / 1000, end: (previous.end_ms - c.clip_start_ms) / 1000};
    liveBoundaries.set(c.id, {values,origin:c.clip_start_ms});
    const mode = element('fieldset', undefined, article);
    element('legend', '拖动进度条时修改', mode);
    let editKind = 'start';
    for (const [value, title] of [['start','起点'],['end','终点'],['listen','只试听，不修改']]) {
      const label = element('label', undefined, mode);
      const input = element('input', undefined, label);
      input.type = 'radio'; input.name = c.id + '-mode'; input.value = value; input.checked = value === editKind;
      label.append(document.createTextNode(title));
      input.addEventListener('change', () => { editKind = value; });
    }
    const canvas = element('canvas', undefined, article);
    canvas.width = 900; canvas.height = 96; canvas.setAttribute('aria-label', '音频波形，可拖动定位；也可使用下方键盘进度条');
    const seekLabel = element('label', '进度 / 边界定位', article);
    const seek = element('input', undefined, seekLabel);
    seek.type = 'range'; seek.min = '0'; seek.max = String(duration); seek.step = String(stepSeconds()); seek.value = '0';
    stepTargets.push(seek);
    const clockLabel = element('output', undefined, article);
    const boundaryLabel = element('output', undefined, article);
    const fields = {};
    const inputs = element('div', undefined, article); inputs.className = 'row';
    for (const kind of ['start','end']) {
      const label = element('label', kind === 'start' ? '起点（片段秒）' : '终点（片段秒）', inputs);
      const input = element('input', undefined, label);
      input.type = 'number'; input.min = '0'; input.max = String(duration); input.step = String(stepSeconds()); input.value = values[kind].toFixed(3);
      stepTargets.push(input);
      fields[kind] = input;
      input.addEventListener('focus', () => {
        editKind = kind; mode.querySelector(`input[value="${kind}"]`).checked = true;
      });
      input.addEventListener('input', () => {
        check.checked = false;
        const time=input.valueAsNumber;
        if (!Number.isFinite(time) || time < 0 || time > duration) return;
        // Preserve what the user is typing, including a trailing decimal point.
        // Formatting each keystroke moves the caret and makes multi-digit entry impossible.
        manualSeek(time, kind, false);
      });
    }
    const presenceLabel = element('label', '演唱情况 ', article);
    const presence = element('select', undefined, presenceLabel);
    for (const [value,title] of [['present','确实唱出'],['absent','未唱出'],['uncertain','仍不确定']]) {
      const option = element('option', title, presence); option.value = value;
    }
    presence.value = previous.presence;
    const confirmLabel = element('label', undefined, article);
    const check = element('input', undefined, confirmLabel); check.type = 'checkbox';
    check.checked = unchangedClip && previous.human_confirmed === true;
    confirmLabel.append(document.createTextNode('我已试听并确认当前起止点'));
    presence.addEventListener('change', () => { check.checked = false; });
    const noteLabel = element('label', '备注（连续人声、无明显分界等）', article);
    const note = element('textarea', undefined, noteLabel); note.value = previous.note || '';
    function refresh() {
      const at = audio.currentTime || 0;
      seek.value = String(at);
      clockLabel.textContent = `当前：片段 ${at.toFixed(3)} 秒 · 整曲 ${format(c.clip_start_ms + at * 1000)}`;
      boundaryLabel.textContent = `起点 ${format(c.clip_start_ms + values.start * 1000)} → 终点 ${format(c.clip_start_ms + values.end * 1000)}`;
      const ctx = canvas.getContext('2d'); ctx.clearRect(0,0,900,96);
      const max = Math.max(...c.peaks, 0.001);
      ctx.strokeStyle = '#687d77'; ctx.beginPath();
      c.peaks.forEach((p,i) => { const x=i/c.peaks.length*900, h=p/max*35; ctx.moveTo(x,48-h);ctx.lineTo(x,48+h); }); ctx.stroke();
      for (const [time,color] of [[values.start,'#137346'],[values.end,'#b43c30'],[at,'#245cbe']]) {
        ctx.strokeStyle=color; ctx.beginPath();ctx.moveTo(time/duration*900,0);ctx.lineTo(time/duration*900,96);ctx.stroke();
      }
    }
    function manualSeek(time, kind = editKind, formatField = true) {
      if (activeController) activeController.cancel();
      controller.cancel();
      const bounded = formatField ? Math.round(Math.max(0, Math.min(duration, time))*1000)/1000 : time;
      audio.currentTime = bounded;
      if (kind !== 'listen') {
        values[kind] = bounded;
        if(formatField) fields[kind].value = bounded.toFixed(3);
        check.checked = false;
      }
      refresh();
      neighborDisplays.forEach(update => update());
    }
    seek.addEventListener('input', () => manualSeek(Number(seek.value)));
    let dragging = false;
    const pointerSeek = e => manualSeek((e.clientX-canvas.getBoundingClientRect().left)/canvas.getBoundingClientRect().width*duration);
    canvas.addEventListener('pointerdown', e => { dragging=true;canvas.setPointerCapture(e.pointerId);pointerSeek(e); });
    canvas.addEventListener('pointermove', e => { if(dragging) pointerSeek(e); });
    canvas.addEventListener('pointerup', () => { dragging=false; });
    canvas.addEventListener('pointercancel', () => { dragging=false; });
    audio.addEventListener('timeupdate', refresh);
    audio.addEventListener('loadedmetadata', refresh);
    const buttons = element('div', undefined, article); buttons.className = 'row';
    function button(title, run) { const b=element('button',title,buttons);b.type='button';b.addEventListener('click',run); }
    function play(parts) {
      if(activeController) activeController.cancel();
      activeController=controller;
      controller.play(parts, Math.max(0, Math.min(3,Number(pauseInput.value)||0)));
    }
    function context() { return Math.max(0.2,Math.min(60,Number(contextInput.value)||1.5)); }
    function selectedPoint() { return editKind==='listen' ? audio.currentTime : values[editKind]; }
    button('← 向前一步', () => manualSeek(selectedPoint()-stepSeconds()));
    button('向后一步 →', () => manualSeek(selectedPoint()+stepSeconds()));
    button('听到所选点结束', () => play([[Math.max(0,selectedPoint()-context()),selectedPoint()]]));
    button('从所选点开始听', () => play([[selectedPoint(),Math.min(duration,selectedPoint()+context())]]));
    button('所选点前后 A / B', () => play(abSegments(selectedPoint(),selectedPoint(),context(),duration)));
    button('从当前位置播放', () => play([[audio.currentTime,duration]]));
    button('暂停', () => controller.cancel());
    button('起点 A / B', () => play(abSegments(values.start,values.start,context(),duration)));
    button('终点 A / B', () => play(abSegments(values.end,values.end,context(),duration)));
    if(c.next) {
      const nextLabel = element('p', undefined, article);
      const nextStartMs = () => neighborStartMs(c.next,liveBoundaries);
      const updateLabel = () => { nextLabel.textContent = `下一行：${c.next.text} · 当前起点 ${format(nextStartMs())}。A 听本句结尾，停顿后 B 听下一句起点。`; };
      neighborDisplays.push(updateLabel);
      button('本句结尾 A → 下一句起点 B', () => play(abSegments(values.end,(nextStartMs()-c.clip_start_ms)/1000,context(),duration)));
    }
    if(c.previous) element('p', `前一行：${c.previous.text} · 上次已应用的结束点 ${format(c.previous.end_ms)}`,article);
    refresh();
    answers.push(() => {
      if(!boundaryInputsValid(fields,values,duration)) throw Error(c.id+'：请填写完整、有效的起止时间后再导出。');
      return {id:c.id,track:c.track,lrc_indices:c.lrc_indices,text:c.text,clip_sha256:c.clip_sha256,
      presence:presence.value,human_confirmed:check.checked,start_ms:Math.round(c.clip_start_ms+values.start*1000),
      end_ms:Math.round(c.clip_start_ms+values.end*1000),note:note.value};
    });
  }
  neighborDisplays.forEach(update => update());
  document.querySelector('#export').addEventListener('click', () => {
    let records;
    try { records=answers.map(read=>read()); }
    catch(error) { document.querySelector('#message').textContent=error.message;return; }
    for(let i=0;i<records.length;i++) {
      const r=records[i],c=payload.cases[i];
      if(r.human_confirmed && r.presence==='present' && !(c.clip_start_ms<=r.start_ms && r.start_ms<r.end_ms && r.end_ms<=c.clip_end_ms)) {
        document.querySelector('#message').textContent=r.id+'：起止顺序或范围不合法，请修正后再导出。';return;
      }
    }
    const result={schema_version:'human-gap-boundary-review-1.0',selection_lock_sha256:payload.lock.lock_sha256,
      final_audio_sha256:payload.lock.final_audio_sha256,task_fingerprint_sha256:payload.lock.task_fingerprint_sha256,
      reviewed_at:new Date().toISOString(),records};
    const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download='human-gap-review-ab.json';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    document.querySelector('#message').textContent=`已导出 ${records.filter(r=>r.human_confirmed).length}/${records.length} 段确认。`;
  });
}

if(typeof module !== 'undefined') module.exports={abSegments,createPlayback,neighborStartMs,boundaryInputsValid,initializeGapReview};
if(typeof document !== 'undefined') initializeGapReview(JSON.parse(document.querySelector('#payload').textContent));
