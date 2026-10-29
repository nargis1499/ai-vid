const $=s=>document.querySelector(s);const titlesEl=$('#titles');let activeBatch=null,pollTimer=null,toastTimer,lastTerminalToastBatch=null,textProvider='openrouter',modelLoadToken=0;const selectedTextModels={openrouter:'',groq:''};
const VIDEO_STEPS=[
 {key:'script',label:'Script & scene plan',hint:'The selected text provider writes and reviews narration and scene plans, including custom drafts.'},
 {key:'images',label:'Find footage & create visuals',hint:'Prefer the closest Pexels or Pixabay clip, then a stock still or designed gradient fallback.'},
 {key:'clips',label:'Use footage & transitions',hint:'Each scene cut randomly uses opposing panel slices or shattered-glass shards.'},
 {key:'audio',label:'Generate voice narration',hint:'Create narration at the selected Slow, Medium, or Faster pace.'},
 {key:'assembly',label:'Assemble scenes & transitions',hint:'Arrange scene timing and apply transitions.'},
 {key:'subtitles',label:'Create captions & SRT',hint:'Time English captions and save the subtitle file.'},
 {key:'music',label:'Mix background music',hint:'Add a title-matched instrumental track.'},
 {key:'encoding',label:'Encode final MP4',hint:'Render the selected resolution and frame rate.'},
 {key:'seo',label:'Prepare YouTube metadata',hint:'Generate title, description, tags, and publishing details.'},
 {key:'thumbnail',label:'Create thumbnail',hint:'Build the template-matched cover from relevant scene visuals and script subjects.'},
 {key:'upload',label:'Publish to YouTube',hint:'Uploads automatically when enabled, or use Publish to YouTube on a completed video.'},
 {key:'finalize',label:'Finish & prepare downloads',hint:'Make the video, thumbnail, and subtitle files available.'}
];
function toast(message){const el=$('#toast');el.textContent=message;el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),3500)}
function stepDetailsMarkup(){return `<details class="step-details"><summary><span><i class="fa-solid fa-list-check"></i> Step-by-step progress</span><span class="step-count">0/${VIDEO_STEPS.length}</span></summary><ol class="step-list">${VIDEO_STEPS.map((step,index)=>`<li class="step-item" data-step="${step.key}"><span class="step-marker">${String(index+1).padStart(2,'0')}</span><div class="step-copy"><b>${step.label}</b><small class="step-message">Waiting to start</small><span class="step-mini-track"><i></i></span></div><span class="step-status">WAITING</span><button class="step-retry-button" type="button" data-retry-step="${step.key}" hidden><i class="fa-solid fa-rotate-right"></i> Retry</button></li>`).join('')}</ol></details>`}
function makeVideoRow(title,index){return `<div class="video-row" data-index="${index}"><span class="video-number">${String(index+1).padStart(2,'0')}</span><div class="video-row-main"><b>${escapeHtml(title)}</b><small class="video-progress-message">Queued</small><div class="video-progress-track"><div class="video-progress-fill"></div></div>${stepDetailsMarkup()}</div><span class="row-state">WAITING</span></div>`}
function updateStepDetails(row,video,batchStatus){
 const details=row.querySelector('.step-details');
 if(!details)return;
 const steps=video.steps||{};
 const messages=video.step_messages||{};
 const completeStates=new Set(['done','fallback','skipped']);
 let complete=0;
 for(const definition of VIDEO_STEPS){
  const item=details.querySelector(`[data-step="${definition.key}"]`);
  if(!item)continue;
  const state=steps[definition.key]||'pending';
  if(completeStates.has(state))complete++;
  const message=messages[definition.key]||(state==='running'&&video.current_stage===definition.key?video.progress_message:null)||(completeStates.has(state)?({done:'Completed',fallback:'Completed with fallback',skipped:'Skipped'}[state]):state==='failed'?'This step failed.':definition.hint);
  const text=String(message||'');
  const percentMatch=text.match(/(\d{1,3})%/);
  const itemMatch=text.match(/\b(\d+)\s+of\s+(\d+)\b/i);
  const measured=state==='running'&&Boolean(percentMatch||itemMatch);
  item.className=`step-item state-${state}${video.current_stage===definition.key&&state==='running'?' is-current':''}${measured?' has-measured-progress':''}`;
  item.querySelector('.step-message').textContent=message;
  item.querySelector('.step-status').textContent=({pending:'WAITING',running:'IN PROGRESS',done:'DONE',fallback:'FALLBACK',skipped:'SKIPPED',failed:'FAILED'})[state]||String(state).toUpperCase();
  const retryButton=item.querySelector('.step-retry-button');
  retryButton.innerHTML='<i class="fa-solid fa-rotate-right"></i> Retry';
  const batchBusy=['queued','running','stopping'].includes(batchStatus);
  retryButton.hidden=state!=='failed'||video.status==='processing'||batchBusy;
  retryButton.disabled=video.status==='processing'||batchBusy;
  let amount=completeStates.has(state)||state==='failed'?100:0;
  if(state==='running')amount=percentMatch?Math.max(0,Math.min(100,Number(percentMatch[1]))):itemMatch?Math.round(100*Number(itemMatch[1])/Math.max(1,Number(itemMatch[2]))):28;
  item.querySelector('.step-mini-track i').style.width=`${amount}%`;
 }
 details.querySelector('.step-count').textContent=`${complete}/${VIDEO_STEPS.length} complete`;
 if(video.status==='processing'&&!row.dataset.stepDetailsAutoOpened){details.open=true;row.dataset.stepDetailsAutoOpened='true'}
}
function renderCustomScriptEditors(){const wrap=$('#custom-script-editors');const titles=titlesEl.value.split('\n').map(x=>x.trim()).filter(Boolean);const previous=[...wrap.querySelectorAll('.custom-script-input')].map(input=>input.value);if(!titles.length){wrap.innerHTML='<small class="script-empty-hint">Add video titles above to create a script field for each one.</small>';return}wrap.innerHTML=titles.map((title,i)=>`<label class="custom-script-editor"><span>VIDEO ${String(i+1).padStart(2,'0')} · ${escapeHtml(title)}</span><textarea class="custom-script-input" maxlength="40000" placeholder="Paste the exact narration for this video…">${escapeHtml(previous[i]||'')}</textarea></label>`).join('')}
titlesEl.addEventListener('input',()=>{const n=titlesEl.value.split('\n').map(x=>x.trim()).filter(Boolean).length;$('#title-count').textContent=`${n} title${n===1?'':'s'}`;if($('#use-custom-script').checked)renderCustomScriptEditors()});
$('#use-custom-script').addEventListener('change',event=>{const panel=$('#custom-scripts-panel');panel.classList.toggle('hidden',!event.target.checked);if(event.target.checked)renderCustomScriptEditors()});
$('#generate').addEventListener('click',startBatch);$('#thumbnail-only').addEventListener('change',updateThumbnailOnlyMode);$('#stop-batch').addEventListener('click',stopBatch);$('#video-list').addEventListener('click',handleVideoAction);$('#refresh-batches').addEventListener('click',loadHistory);
$('#help-button').addEventListener('click',()=>toast('See Settings to choose OpenRouter or Groq, configure its API key, and select a model.'));
const openSettings=()=>{loadSettingsForm();$('#settings-dialog').showModal()};$('#settings-button').addEventListener('click',openSettings);$('#open-settings-link').addEventListener('click',event=>{event.preventDefault();openSettings()});
$('#close-settings').addEventListener('click',()=>$('#settings-dialog').close());
$('#cancel-settings').addEventListener('click',()=>$('#settings-dialog').close());
$('#settings-dialog').addEventListener('click',event=>{if(event.target===$('#settings-dialog'))$('#settings-dialog').close()});
$('#save-settings').addEventListener('click',saveSettingsForm);
document.querySelectorAll('.provider-choice').forEach(button=>button.addEventListener('click',()=>changeTextProvider(button.dataset.textProvider)));
$('#setting-text-model').addEventListener('change',()=>selectedTextModels[textProvider]=$('#setting-text-model').value);
$('#refresh-text-models').addEventListener('click',()=>loadTextModels(textProvider,$('#setting-text-model').value,true));
$('#youtube-credentials-file').addEventListener('change',uploadYoutubeCredentials);
$('#connect-youtube').addEventListener('click',async()=>{const btn=$('#connect-youtube');btn.disabled=true;btn.textContent='Opening Google sign-in…';try{const r=await fetch('/api/youtube/auth',{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.error||'Could not connect YouTube');toast('YouTube channel connected.');await loadYoutubeStatus()}catch(e){toast(e.message||'YouTube sign-in did not complete. Run the app locally and try again.')}finally{btn.disabled=false;btn.innerHTML='<i class="fa-brands fa-youtube"></i> Connect channel'}});
function updateThumbnailOnlyMode(){const enabled=$('#thumbnail-only').checked;const autoUpload=$('#auto-upload');if(enabled)autoUpload.checked=false;autoUpload.disabled=enabled;$('#generate').querySelector('span:nth-child(2)').textContent=enabled?'Generate thumbnails':'Generate video batch'}
async function startBatch(){
 const titles=titlesEl.value.split('\n').map(x=>x.trim()).filter(Boolean);
 if(!titles.length){toast('Add at least one video title to get started.');titlesEl.focus();return}
 const useCustom=$('#use-custom-script').checked;
 const thumbnailOnly=$('#thumbnail-only').checked;
 let customScripts=[];
 if(useCustom){
  customScripts=[...$('#custom-script-editors').querySelectorAll('.custom-script-input')].map(input=>input.value.trim());
  if(customScripts.length!==titles.length){toast('Add a custom script field for every video title.');return}
  if(customScripts.some(script=>!script)){toast('Paste a script for each video, or switch off custom scripts to use the configured script provider.');return}
  if(customScripts.some(script=>script.length>40000)){toast('Each custom script must be 40,000 characters or fewer.');return}
 }
 const button=$('#generate');button.disabled=true;button.querySelector('span:nth-child(2)').textContent=thumbnailOnly?'Starting thumbnail generation…':'Starting your batch…';
 try{
  const payload={titles,duration:Number($('#duration').value),resolution:$('#resolution').value,voice_speed:$('#voice-speed').value,niche:$('#niche').value,privacy:$('#privacy').value,auto_upload:thumbnailOnly?false:$('#auto-upload').checked,thumbnail_only:thumbnailOnly};
  if(useCustom)payload.custom_scripts=customScripts;
  const response=await fetch('/api/batch/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Unable to start batch');
  activeBatch=data.batch_id;$('#progress-section').classList.remove('hidden');$('#total-count').textContent=titles.length;$('#done-count').textContent='0';$('#failed-count').textContent='0 failed';$('#progress-title').textContent=thumbnailOnly?'Preparing your thumbnails':'Preparing your videos';$('#progress-message').textContent=thumbnailOnly?'Preparing scripts and visuals for thumbnail previews…':useCustom?'Preparing your scripts and scenes…':'Starting the creative pipeline…';
  $('#video-list').innerHTML=titles.map(makeVideoRow).join('');
  $('#progress-section').scrollIntoView({behavior:'smooth',block:'nearest'});await pollBatch();pollTimer=setInterval(pollBatch,3000)
 }catch(e){toast(e.message)}finally{button.disabled=false;button.querySelector('span:nth-child(2)').textContent=$('#thumbnail-only').checked?'Generate thumbnails':'Generate video batch'}
}
async function stopBatch(){
 if(!activeBatch)return;
 if(!window.confirm('Stop generating this batch? The current operation will be cancelled as soon as possible.'))return;
 const button=$('#stop-batch');button.disabled=true;button.innerHTML='<i class="fa-solid fa-spinner fa-spin"></i> Stopping…';
 try{const response=await fetch(`/api/batch/${encodeURIComponent(activeBatch)}/stop`,{method:'POST'});const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not stop the batch');$('#progress-message').textContent=data.message||'Stop requested…';if(!pollTimer)pollTimer=setInterval(pollBatch,1500);await pollBatch()}catch(error){toast(error.message||'Could not stop generation');button.disabled=false;button.innerHTML='<i class="fa-solid fa-stop"></i> Stop generation'}
}
async function handleVideoAction(event){
 const retryButton=event.target.closest('.step-retry-button');
 if(retryButton){
  const row=retryButton.closest('.video-row'),index=Number(row.dataset.index),step=retryButton.dataset.retryStep;
  retryButton.disabled=true;retryButton.innerHTML='<i class="fa-solid fa-spinner fa-spin"></i> Retrying…';
  try{const response=await fetch(`/api/batch/${encodeURIComponent(activeBatch)}/retry/${index}/${encodeURIComponent(step)}`,{method:'POST'});const data=await response.json();if(!response.ok)throw new Error(data.error||`Could not retry ${step}`);toast(data.message||`Retrying ${step}…`);if(!pollTimer)pollTimer=setInterval(pollBatch,1500);await pollBatch()}catch(error){retryButton.disabled=false;retryButton.innerHTML='<i class="fa-solid fa-rotate-right"></i> Retry';toast(error.message||`Could not retry ${step}`)}
  return
 }
 const button=event.target.closest('.publish-button');if(!button)return;
 const index=Number(button.dataset.index);button.disabled=true;button.textContent='Starting publish…';
 try{const response=await fetch(`/api/batch/${encodeURIComponent(activeBatch)}/publish/${index}`,{method:'POST'});const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not publish video');toast('YouTube publishing started.');if(!pollTimer)pollTimer=setInterval(pollBatch,1500);await pollBatch()}catch(error){button.disabled=false;button.textContent='Publish to YouTube';toast(error.message||'Could not publish video')}
}
async function pollBatch(){
 if(!activeBatch)return;
 try{
  const response=await fetch(`/api/batch/status/${encodeURIComponent(activeBatch)}`);
  const data=await response.json();
  if(!response.ok)throw new Error(data.error);
  $('#progress-bar').style.width=`${data.progress||0}%`;
  $('#done-count').textContent=data.completed||0;
  $('#failed-count').textContent=`${data.failed||0} failed`;
  const terminal=['completed','cancelled','failed'].includes(data.status);
  $('#progress-title').textContent=data.status==='completed'?(data.thumbnail_only?'Thumbnail batch complete':'Batch complete'):data.status==='stopping'?'Stopping generation…':data.status==='cancelled'?'Generation stopped':data.status==='failed'?'Batch failed':data.thumbnail_only?'Creating thumbnails…':'Creating your videos';
  const stopButton=$('#stop-batch');stopButton.classList.toggle('hidden',terminal);stopButton.disabled=data.status==='stopping';stopButton.innerHTML=data.status==='stopping'?'<i class="fa-solid fa-spinner fa-spin"></i> Stopping…':'<i class="fa-solid fa-stop"></i> Stop generation';
  const activeVideo=(data.videos||[]).find(video=>video.status==='processing');
  if(data.status==='completed')$('#progress-message').textContent=data.thumbnail_only?'All thumbnails are ready. Video generation was skipped.':'All generation steps have finished.';
  else if(data.status==='cancelled')$('#progress-message').textContent=data.thumbnail_only?'Thumbnail generation was stopped. Completed thumbnail files are still available below.':'Generation was stopped. Completed videos are still available below.';
  else if(data.status==='failed')$('#progress-message').textContent=data.error||'The batch stopped because of an unexpected error.';
  else if(data.status==='stopping')$('#progress-message').textContent='Stop requested; cancelling the current operation…';
  else if(activeVideo)$('#progress-message').textContent=`${activeVideo.title}: ${activeVideo.progress_message||activeVideo.current_stage} · ${activeVideo.progress||0}%`;
  else $('#progress-message').textContent=data.thumbnail_only?`Waiting to create ${data.total} thumbnail${data.total===1?'':'s'}…`:`Waiting to process ${data.total} video${data.total===1?'':'s'}…`;
  $('#failed-count').textContent=`${data.failed||0} failed${data.cancelled?` · ${data.cancelled} stopped`:''}`;
  $('#batch-status').innerHTML=`<span class="status-dot ${data.status==='completed'?'green':''}"></span> ${escapeHtml(String(data.status).toUpperCase())}`;
  data.videos.forEach((video,index)=>{
   const row=$(`.video-row[data-index="${index}"]`);
   if(!row)return;
   const state=video.status||'waiting';
   const pct=state==='done'?100:Number(video.progress||0);
   const message=state==='processing'?(video.progress_message||stepsLabel(video.steps)):state==='done'?video.thumbnail_only?'Thumbnail ready · video rendering skipped':(video.video_encoder?`Finished · ${video.video_encoder}`:'Finished processing')+(video.music_warning?' · music unavailable':'')+(video.voice_warning?` · ${video.voice_fallback_engine||'TTS'} fallback used`:''):state==='failed'?(video.error||video.upload_error||'Something went wrong'):state==='cancelled'?'Stopped by user.':'Waiting to start';
   row.querySelector('.video-progress-message').textContent=state==='processing'?`${message} · ${video.thumbnail_only?'thumbnail':'video'} ${pct}%`:message;
   const progressFill=row.querySelector('.video-progress-fill');
   progressFill.style.width=`${pct}%`;
   progressFill.setAttribute('aria-valuenow',String(pct));
   const badge=row.querySelector('.row-state');
   badge.className=`row-state ${state}`;
   badge.textContent=state.toUpperCase();
   updateStepDetails(row,video,data.status);
   if(video.script&&Array.isArray(video.script.scenes)&&!row.querySelector('.script-preview')){
    const details=document.createElement('details');
    details.className='script-preview';
    const summary=document.createElement('summary');
    summary.textContent=video.script.custom_script_reviewed?'View AI-reviewed narration':'View generated narration';
    const preview=document.createElement('div');
    preview.className='script-preview-content';
    if(video.script.review_summary){const note=document.createElement('small');note.textContent=video.script.review_summary;preview.append(note)}
    const narration=document.createElement('pre');
    narration.textContent=video.script.scenes.map(scene=>scene.narration||'').filter(Boolean).join(String.fromCharCode(10,10));
    preview.append(narration);details.append(summary,preview);row.querySelector('.video-row-main').append(details)
   }
   if(video.video_path&&!row.querySelector('.video-download')){const link=document.createElement('a');link.href=`/api/video/${encodeURIComponent(activeBatch)}/${index}`;link.textContent='Download video';link.className='video-download';link.setAttribute('download','');row.querySelector('b').append(' ',link)}
   if(video.subtitle_path&&!row.querySelector('.subtitle-download')){const link=document.createElement('a');link.href=`/api/subtitles/${encodeURIComponent(activeBatch)}/${index}`;link.textContent='Subtitles (.srt)';link.className='subtitle-download';link.setAttribute('download','');row.querySelector('b').append(' ',link)}
   if(video.thumbnail_path&&!row.querySelector('.thumbnail-download')){const link=document.createElement('a');link.href=`/api/thumbnail/${encodeURIComponent(activeBatch)}/${index}`;link.textContent='Thumbnail (.jpg)';link.className='thumbnail-download';link.setAttribute('download','');row.querySelector('b').append(' ',link)}
   let publishButton=row.querySelector('.publish-button'),publishError=row.querySelector('.publish-error');
   if(data.auto_upload===false&&video.status==='done'&&video.video_path&&!video.youtube_url){
    if(!publishButton){publishButton=document.createElement('button');publishButton.type='button';publishButton.className='publish-button';publishButton.dataset.index=String(index);row.querySelector('.video-row-main').append(publishButton)}
    const uploading=video.upload_status==='uploading';publishButton.disabled=uploading;publishButton.textContent=uploading?`Publishing… ${video.upload_progress||0}%`:video.upload_status==='failed'?'Retry YouTube publish':'Publish to YouTube';
    if(video.upload_error){if(!publishError){publishError=document.createElement('small');publishError.className='publish-error';row.querySelector('.video-row-main').append(publishError)}publishError.textContent=video.upload_error}else if(publishError)publishError.remove();
   }else{if(publishButton)publishButton.remove();if(publishError)publishError.remove()}
   if(video.youtube_url&&!row.querySelector('.youtube-link')){const link=document.createElement('a');link.href=video.youtube_url;link.target='_blank';link.rel='noopener';link.className='youtube-link';link.innerHTML='<i class="fa-brands fa-youtube"></i> View';badge.append(link)}
  });
  const publishing=(data.videos||[]).some(video=>video.upload_status==='uploading');
  if(terminal&&!publishing){clearInterval(pollTimer);pollTimer=null;loadHistory();if(lastTerminalToastBatch!==activeBatch){lastTerminalToastBatch=activeBatch;if(data.status==='cancelled')toast(data.thumbnail_only?'Thumbnail generation stopped. Completed thumbnails remain available.':'Generation stopped. Completed videos remain available.');else if(data.status==='failed')toast(data.error||'The batch failed.');else toast(data.failed?`Batch finished with ${data.failed} failed ${data.thumbnail_only?'thumbnail':'video'}${data.failed===1?'':'s'}.`:data.thumbnail_only?'Your thumbnails are ready!':'Your video batch is ready!')}}
 }catch(error){console.error(error)}
}
function stepsLabel(steps){if(!steps)return'Processing';const names={script:'Script',images:'Visuals',clips:'Motion clips',audio:'Narration',assembly:'Scene assembly',subtitles:'Captions and SRT',music:'Music',encoding:'MP4 encoding',seo:'YouTube metadata',thumbnail:'Thumbnail',upload:'Upload',finalize:'Finishing'};const done=Object.keys(steps).filter(k=>['done','fallback','skipped'].includes(steps[k])).map(k=>names[k]||k);return done.length?`Completed: ${done.slice(-2).join(', ')}`:'Processing'}
function escapeHtml(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function loadYoutubeStatus(){try{const r=await fetch('/api/youtube/status');const d=await r.json();$('#youtube-state').textContent=d.connected?'Connected':'Not connected';$('#youtube-dot').classList.toggle('green',d.connected);$('#youtube-detail').textContent=d.connected?`${d.uploads_today} of ${d.max_uploads_per_day} daily uploads used.`:(d.error||'Connect your channel to enable publishing.');$('#connect-youtube').innerHTML=d.connected?'<i class="fa-solid fa-check"></i> Channel connected':'<i class="fa-brands fa-youtube"></i> Connect channel'}catch(e){$('#youtube-state').textContent='Offline'}}
function modelPriceLabel(model,provider){
 if(model.free)return'FREE';
 if(provider==='groq')return'GroqCloud API';
 if(model.input_price==null||model.output_price==null)return'pricing varies';
 const input=Number(model.input_price),output=Number(model.output_price);
 if(Number.isFinite(input)&&Number.isFinite(output))return`$${(input*1e6).toFixed(2)}/$${(output*1e6).toFixed(2)} per 1M in/out`;
 return'pricing varies'
}
function applyTextProvider(provider){
 textProvider=provider==='groq'?'groq':'openrouter';
 document.querySelectorAll('.provider-choice').forEach(button=>{
  const active=button.dataset.textProvider===textProvider;
  button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
 });
 $('#provider-openrouter-panel').hidden=textProvider!=='openrouter';
 $('#provider-groq-panel').hidden=textProvider!=='groq';
 $('#active-text-provider').textContent=textProvider==='groq'?'Groq AI':'OpenRouter AI';
}
function changeTextProvider(provider){
 selectedTextModels[textProvider]=$('#setting-text-model').value;
 applyTextProvider(provider);
 return loadTextModels(textProvider,selectedTextModels[textProvider]||'',false);
}
async function loadTextModels(provider=textProvider,selected='',refresh=false){
 const select=$('#setting-text-model'),status=$('#settings-text-status');
 const requestToken=++modelLoadToken;
 if(refresh)status.textContent=`Loading ${provider==='groq'?'Groq':'free OpenRouter'} models…`;
 try{
  const query=new URLSearchParams({provider});if(refresh)query.set('refresh','1');
  const response=await fetch(`/api/text/models?${query.toString()}`,{cache:refresh?'no-store':'default'});
  const data=await response.json();if(!response.ok)throw new Error(data.error||`Could not load ${provider} models`);
  if(requestToken!==modelLoadToken||provider!==textProvider)return;
  const models=Array.isArray(data.models)?data.models:[];
  select.replaceChildren();
  const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=models.length?'Select a model':'No models are currently listed';select.append(placeholder);
  for(const model of models){
   const option=document.createElement('option');option.value=model.id;
   const context=model.context_length?` · ${Number(model.context_length).toLocaleString()} context`:'';
   option.textContent=`${model.name} · ${modelPriceLabel(model,provider)}${context}`;select.append(option)
  }
  const selectedAvailable=models.some(model=>model.id===selected);
  select.value=selectedAvailable?selected:(models[0]?.id||'');
  selectedTextModels[provider]=select.value;
  if(data.notice)status.textContent=data.notice;
  else if(provider==='openrouter')status.textContent=models.length?'Only free OpenRouter text models are listed. Choose one and save your settings.':'OpenRouter returned no free text models.';
  else status.textContent=models.length?'Groq models are loaded from GroqCloud; availability and rate limits depend on your account.':'No Groq text models were returned.';
  if(selected&&!selectedAvailable&&models.length)status.textContent=`Your previous model is not in this provider's current list; choose a listed model and save. ${status.textContent}`
 }catch(error){
  if(requestToken!==modelLoadToken||provider!==textProvider)return;
  select.replaceChildren();
  if(provider==='groq'){
   for(const model of ['openai/gpt-oss-120b','openai/gpt-oss-20b','llama-3.3-70b-versatile']){const option=document.createElement('option');option.value=model;option.textContent=`${model} · Groq API`;select.append(option)}
   select.value=selected&&[...select.options].some(option=>option.value===selected)?selected:'openai/gpt-oss-120b';selectedTextModels[provider]=select.value;
  }else if(selected){const option=document.createElement('option');option.value=selected;option.textContent=`${selected} · catalog unavailable`;select.append(option);select.value=selected;selectedTextModels[provider]=selected}
  else{const option=document.createElement('option');option.value='';option.textContent='Could not load models';select.append(option)}
  status.textContent=error.message||`Could not load ${provider} models.`
 }
}
async function loadSettingsForm(){
 try{
  const response=await fetch('/api/settings'),s=await response.json();if(!response.ok)throw new Error(s.error||'Could not load settings');
  textProvider=s.TEXT_PROVIDER==='groq'?'groq':'openrouter';selectedTextModels.openrouter='';selectedTextModels.groq='';selectedTextModels[textProvider]=s.TEXT_MODEL||'';applyTextProvider(textProvider);
  $('#setting-openrouter-key').value='';$('#setting-openrouter-key').placeholder=s.OPENROUTER_API_KEY_CONFIGURED?'Key saved — leave blank to keep it':'Paste OpenRouter API key';$('#openrouter-configured').textContent=s.OPENROUTER_API_KEY_CONFIGURED?'Saved locally':'';$('#clear-openrouter-key').checked=false;
  $('#setting-groq-key').value='';$('#setting-groq-key').placeholder=s.GROQ_API_KEY_CONFIGURED?'Key saved — leave blank to keep it':'Paste Groq (GroqCloud) API key';$('#groq-configured').textContent=s.GROQ_API_KEY_CONFIGURED?'Saved locally':'';$('#clear-groq-key').checked=false;
  loadTextModels(textProvider,s.TEXT_MODEL||'');
  $('#setting-pexels-key').value='';$('#setting-pexels-key').placeholder=s.PEXELS_API_KEY_CONFIGURED?'Key saved — leave blank to keep it':'Paste Pexels API key';$('#pexels-configured').textContent=s.PEXELS_API_KEY_CONFIGURED?'Saved locally':'';$('#clear-pexels-key').checked=false;
  $('#setting-pixabay-key').value='';$('#setting-pixabay-key').placeholder=s.PIXABAY_API_KEY_CONFIGURED?'Key saved — leave blank to keep it':'Paste Pixabay API key';$('#pixabay-configured').textContent=s.PIXABAY_API_KEY_CONFIGURED?'Saved locally':'';$('#clear-pixabay-key').checked=false;
  $('#setting-ffmpeg').value=s.FFMPEG_BINARY||'ffmpeg-imageio';$('#setting-voice').value=s.EDGE_TTS_VOICE||'en-US-AriaNeural';$('#setting-privacy').value=s.YOUTUBE_PRIVACY_STATUS||'private';$('#setting-category').value=s.YOUTUBE_CATEGORY_ID||'22';$('#setting-upload-limit').value=s.MAX_UPLOADS_PER_DAY||5;$('#setting-batch-delay').value=s.BATCH_DELAY_SECONDS??10;$('#setting-output-dir').value=s.OUTPUT_DIR||'';$('#youtube-credential-label').textContent=s.YOUTUBE_CREDENTIALS_CONFIGURED?'Google OAuth credentials are configured':'Google OAuth credentials not configured'
 }catch(error){toast(error.message||'Could not load app settings')}
}
async function saveSettingsForm(){
 const button=$('#save-settings');button.disabled=true;button.innerHTML='<i class="fa-solid fa-spinner fa-spin"></i> Saving…';
 const selectedModel=$('#setting-text-model').value;
 if(!selectedModel){toast('Choose a model for the selected provider before saving.');button.disabled=false;button.innerHTML='<i class="fa-solid fa-check"></i> Save settings';return}
 const payload={TEXT_PROVIDER:textProvider,TEXT_MODEL:selectedModel,CLEAR_OPENROUTER_API_KEY:$('#clear-openrouter-key').checked,CLEAR_GROQ_API_KEY:$('#clear-groq-key').checked,FFMPEG_BINARY:$('#setting-ffmpeg').value.trim(),EDGE_TTS_VOICE:$('#setting-voice').value,YOUTUBE_PRIVACY_STATUS:$('#setting-privacy').value,YOUTUBE_CATEGORY_ID:$('#setting-category').value.trim(),MAX_UPLOADS_PER_DAY:Number($('#setting-upload-limit').value),BATCH_DELAY_SECONDS:Number($('#setting-batch-delay').value),OUTPUT_DIR:$('#setting-output-dir').value.trim(),CLEAR_PEXELS_API_KEY:$('#clear-pexels-key').checked,CLEAR_PIXABAY_API_KEY:$('#clear-pixabay-key').checked};
 const key=$('#setting-pexels-key').value.trim();if(key)payload.PEXELS_API_KEY=key;
 const pixabayKey=$('#setting-pixabay-key').value.trim();if(pixabayKey)payload.PIXABAY_API_KEY=pixabayKey;
 const openrouterKey=$('#setting-openrouter-key').value.trim();if(openrouterKey)payload.OPENROUTER_API_KEY=openrouterKey;
 const groqKey=$('#setting-groq-key').value.trim();if(groqKey)payload.GROQ_API_KEY=groqKey;
 try{
  const response=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),data=await response.json();if(!response.ok)throw new Error(data.error||'Could not save settings');
  selectedTextModels[textProvider]=data.TEXT_MODEL||selectedModel;$('#active-text-provider').textContent=textProvider==='groq'?'Groq AI':'OpenRouter AI';
  $('#setting-pexels-key').value='';$('#setting-pixabay-key').value='';$('#setting-openrouter-key').value='';$('#setting-groq-key').value='';
  $('#setting-openrouter-key').placeholder=data.OPENROUTER_API_KEY_CONFIGURED?'Key saved — leave blank to keep it':'Paste OpenRouter API key';$('#openrouter-configured').textContent=data.OPENROUTER_API_KEY_CONFIGURED?'Saved locally':'';
  $('#setting-groq-key').placeholder=data.GROQ_API_KEY_CONFIGURED?'Key saved — leave blank to keep it':'Paste Groq (GroqCloud) API key';$('#groq-configured').textContent=data.GROQ_API_KEY_CONFIGURED?'Saved locally':'';
  $('#pexels-configured').textContent=data.PEXELS_API_KEY_CONFIGURED?'Saved locally':'';$('#pixabay-configured').textContent=data.PIXABAY_API_KEY_CONFIGURED?'Saved locally':'';
  $('#clear-openrouter-key').checked=false;$('#clear-groq-key').checked=false;$('#clear-pexels-key').checked=false;$('#clear-pixabay-key').checked=false;$('#privacy').value=data.YOUTUBE_PRIVACY_STATUS||'private';
  $('#settings-dialog').close();await loadYoutubeStatus();toast('Settings saved on this PC.')
 }catch(error){toast(error.message||'Could not save app settings')}
 finally{button.disabled=false;button.innerHTML='<i class="fa-solid fa-check"></i> Save settings'}
}
async function uploadYoutubeCredentials(){const input=$('#youtube-credentials-file');const file=input.files&&input.files[0];if(!file)return;const label=$('#youtube-credential-label');label.textContent='Saving OAuth credentials…';try{const form=new FormData();form.append('file',file);const r=await fetch('/api/youtube/credentials',{method:'POST',body:form});const d=await r.json();if(!r.ok)throw new Error(d.error||'Could not save credentials');label.textContent='Google OAuth credentials are configured';await loadYoutubeStatus();toast('Google OAuth credentials saved locally.')}catch(e){label.textContent=e.message;toast(e.message)}finally{input.value=''}}
async function loadHistory(){try{const r=await fetch('/api/batch/list');const d=await r.json();const list=d.batches||[];$('#queue-count').textContent=list.filter(x=>x.status==='running'||x.status==='queued'||x.status==='stopping').length;if(!list.length){$('#batch-history').innerHTML='<div class="empty-history"><span><i class="fa-regular fa-folder-open"></i></span><b>Your workspace is ready</b><small>Created batches will show up here.</small></div>';return}$('#batch-history').innerHTML=list.slice(0,5).map(b=>`<div class="history-card"><div><b>${b.total} video${b.total===1?'':'s'} · ${escapeHtml(b.batch_id)}</b><small>${new Date(b.created_at).toLocaleString()} · ${b.completed}/${b.total} processed</small></div><span class="history-state">${escapeHtml(b.status.toUpperCase())}</span></div>`).join('')}catch(e){}}
async function loadSavedDefaults(){try{const r=await fetch('/api/settings');const s=await r.json();if(s.YOUTUBE_PRIVACY_STATUS)$('#privacy').value=s.YOUTUBE_PRIVACY_STATUS;if(s.TEXT_PROVIDER)$('#active-text-provider').textContent=s.TEXT_PROVIDER==='groq'?'Groq AI':'OpenRouter AI'}catch(e){}}
loadYoutubeStatus();loadHistory();loadSavedDefaults();
