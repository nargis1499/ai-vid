const $=s=>document.querySelector(s);const titlesEl=$('#titles');let activeBatch=null,pollTimer=null,toastTimer;
const VIDEO_STEPS=[
 {key:'script',label:'Script & scene plan',hint:'Draft narration, review it locally, or revise your custom script.'},
 {key:'images',label:'Create scene visuals',hint:'Generate visuals with local AI, stock search, or fallback artwork.'},
 {key:'clips',label:'Find or generate motion clips',hint:'Animate scenes or find matching stock footage.'},
 {key:'audio',label:'Generate voice narration',hint:'Create spoken audio for each scene.'},
 {key:'assembly',label:'Assemble scenes & transitions',hint:'Arrange scene timing and apply transitions.'},
 {key:'subtitles',label:'Create captions & SRT',hint:'Time English captions and save the subtitle file.'},
 {key:'music',label:'Mix background music',hint:'Add a title-matched instrumental track.'},
 {key:'encoding',label:'Encode final MP4',hint:'Render the selected resolution and frame rate.'},
 {key:'seo',label:'Prepare YouTube metadata',hint:'Generate title, description, tags, and publishing details.'},
 {key:'thumbnail',label:'Create thumbnail',hint:'Design the cover image for the video.'},
 {key:'upload',label:'Upload to YouTube',hint:'Runs only when automatic publishing is enabled.'},
 {key:'finalize',label:'Finish & prepare downloads',hint:'Make completed video and subtitle files available.'}
];
function toast(message){const el=$('#toast');el.textContent=message;el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),3500)}
function stepDetailsMarkup(){return `<details class="step-details"><summary><span><i class="fa-solid fa-list-check"></i> Step-by-step progress</span><span class="step-count">0/${VIDEO_STEPS.length}</span></summary><ol class="step-list">${VIDEO_STEPS.map((step,index)=>`<li class="step-item" data-step="${step.key}"><span class="step-marker">${String(index+1).padStart(2,'0')}</span><div class="step-copy"><b>${step.label}</b><small class="step-message">Waiting to start</small><span class="step-mini-track"><i></i></span></div><span class="step-status">WAITING</span></li>`).join('')}</ol></details>`}
function makeVideoRow(title,index){return `<div class="video-row" data-index="${index}"><span class="video-number">${String(index+1).padStart(2,'0')}</span><div class="video-row-main"><b>${escapeHtml(title)}</b><small class="video-progress-message">Queued</small><div class="video-progress-track"><div class="video-progress-fill"></div></div>${stepDetailsMarkup()}</div><span class="row-state">WAITING</span></div>`}
function updateStepDetails(row,video){
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
$('#generate').addEventListener('click',startBatch);$('#refresh-batches').addEventListener('click',loadHistory);
$('#help-button').addEventListener('click',()=>toast('See the setup notes in Settings for local services, keys and FFmpeg.'));
const openSettings=()=>{loadSettingsForm();$('#settings-dialog').showModal()};$('#settings-button').addEventListener('click',openSettings);$('#open-settings-link').addEventListener('click',event=>{event.preventDefault();openSettings()});
$('#close-settings').addEventListener('click',()=>$('#settings-dialog').close());
$('#cancel-settings').addEventListener('click',()=>$('#settings-dialog').close());
$('#settings-dialog').addEventListener('click',event=>{if(event.target===$('#settings-dialog'))$('#settings-dialog').close()});
$('#save-settings').addEventListener('click',saveSettingsForm);
$('#setting-script-provider').addEventListener('change',updateOpenRouterFields);
$('#setting-use-ollama').addEventListener('change',updateOpenRouterFields);
$('#refresh-openrouter-models').addEventListener('click',()=>loadOpenRouterModels($('#setting-openrouter-model').value,true));
$('#start-ollama').addEventListener('click',startOllamaFromSettings);
$('#pull-ollama').addEventListener('click',pullOllamaFromSettings);
$('#youtube-credentials-file').addEventListener('change',uploadYoutubeCredentials);
$('#connect-youtube').addEventListener('click',async()=>{const btn=$('#connect-youtube');btn.disabled=true;btn.textContent='Opening Google sign-in…';try{const r=await fetch('/api/youtube/auth',{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.error||'Could not connect YouTube');toast('YouTube channel connected.');await loadYoutubeStatus()}catch(e){toast(e.message||'YouTube sign-in did not complete. Run the app locally and try again.')}finally{btn.disabled=false;btn.innerHTML='<i class="fa-brands fa-youtube"></i> Connect channel'}});
async function startBatch(){
 const titles=titlesEl.value.split('\n').map(x=>x.trim()).filter(Boolean);
 if(!titles.length){toast('Add at least one video title to get started.');titlesEl.focus();return}
 const useCustom=$('#use-custom-script').checked;
 let customScripts=[];
 if(useCustom){
  customScripts=[...$('#custom-script-editors').querySelectorAll('.custom-script-input')].map(input=>input.value.trim());
  if(customScripts.length!==titles.length){toast('Add a custom script field for every video title.');return}
  if(customScripts.some(script=>!script)){toast('Paste a script for each video, or switch off custom scripts to use the configured script provider.');return}
  if(customScripts.some(script=>script.length>40000)){toast('Each custom script must be 40,000 characters or fewer.');return}
 }
 const button=$('#generate');button.disabled=true;button.querySelector('span:nth-child(2)').textContent='Starting your batch…';
 try{
  const payload={titles,duration:Number($('#duration').value),resolution:$('#resolution').value,niche:$('#niche').value,privacy:$('#privacy').value,auto_upload:$('#auto-upload').checked};
  if(useCustom)payload.custom_scripts=customScripts;
  const response=await fetch('/api/batch/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Unable to start batch');
  activeBatch=data.batch_id;$('#progress-section').classList.remove('hidden');$('#total-count').textContent=titles.length;$('#done-count').textContent='0';$('#failed-count').textContent='0 failed';$('#progress-title').textContent='Preparing your videos';$('#progress-message').textContent=useCustom?'Preparing your scripts and scenes…':'Starting the creative pipeline…';
  $('#video-list').innerHTML=titles.map(makeVideoRow).join('');
  $('#progress-section').scrollIntoView({behavior:'smooth',block:'nearest'});await pollBatch();pollTimer=setInterval(pollBatch,3000)
 }catch(e){toast(e.message)}finally{button.disabled=false;button.querySelector('span:nth-child(2)').textContent='Generate video batch'}
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
  updateGpuStatus(data.gpu_status||{});
  $('#progress-title').textContent=data.status==='completed'?'Batch complete':'Creating your videos';
  const activeVideo=(data.videos||[]).find(video=>video.status==='processing');
  if(data.status==='completed')$('#progress-message').textContent='All generation steps have finished.';
  else if(activeVideo)$('#progress-message').textContent=`${activeVideo.title}: ${activeVideo.progress_message||activeVideo.current_stage} · ${activeVideo.progress||0}%`;
  else $('#progress-message').textContent=`Waiting to process ${data.total} video${data.total===1?'':'s'}…`;
  $('#batch-status').innerHTML=`<span class="status-dot ${data.status==='completed'?'green':''}"></span> ${escapeHtml(String(data.status).toUpperCase())}`;
  data.videos.forEach((video,index)=>{
   const row=$(`.video-row[data-index="${index}"]`);
   if(!row)return;
   const state=video.status||'waiting';
   const pct=state==='done'?100:Number(video.progress||0);
   const message=state==='processing'?(video.progress_message||stepsLabel(video.steps)):state==='done'?(video.video_encoder?`Finished · ${video.video_encoder}`:'Finished processing')+(video.music_warning?' · music unavailable':'')+(video.voice_warning?` · ${video.voice_fallback_engine||'TTS'} fallback used`:''):state==='failed'?(video.error||video.upload_error||'Something went wrong'):'Waiting to start';
   row.querySelector('.video-progress-message').textContent=state==='processing'?`${message} · video ${pct}%`:message;
   const progressFill=row.querySelector('.video-progress-fill');
   progressFill.style.width=`${pct}%`;
   progressFill.setAttribute('aria-valuenow',String(pct));
   const badge=row.querySelector('.row-state');
   badge.className=`row-state ${state}`;
   badge.textContent=state.toUpperCase();
   updateStepDetails(row,video);
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
   if(video.youtube_url&&!row.querySelector('.youtube-link')){const link=document.createElement('a');link.href=video.youtube_url;link.target='_blank';link.rel='noopener';link.className='youtube-link';link.innerHTML='<i class="fa-brands fa-youtube"></i> View';badge.append(link)}
  });
  if(data.status==='completed'){clearInterval(pollTimer);pollTimer=null;loadHistory();toast(data.failed?`Batch finished with ${data.failed} failed video${data.failed===1?'':'s'}.`:'Your video batch is ready!')}
 }catch(error){console.error(error)}
}
function stepsLabel(steps){if(!steps)return'Processing';const names={script:'Script',images:'Visuals',clips:'Motion clips',audio:'Narration',assembly:'Scene assembly',subtitles:'Captions and SRT',music:'Music',encoding:'MP4 encoding',seo:'YouTube metadata',thumbnail:'Thumbnail',upload:'Upload',finalize:'Finishing'};const done=Object.keys(steps).filter(k=>['done','fallback','skipped'].includes(steps[k])).map(k=>names[k]||k);return done.length?`Completed: ${done.slice(-2).join(', ')}`:'Processing'}
function escapeHtml(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function updateGpuStatus(gpu){const pill=$('#gpu-status');const label=pill.querySelector('.gpu-status-label');const state=gpu.state||'unavailable';pill.className=`gpu-status state-${state}`;pill.title=gpu.message||'GPU status unavailable';let text;if(state==='active')text=`${gpu.device||'GPU'} IN USE · ${gpu.operation||'accelerated processing'}${gpu.vram_used_gb!=null?` · ${gpu.vram_used_gb} GB VRAM`:''}`;else if(state==='loaded')text=`${gpu.device||'GPU'} idle · Ollama model loaded${gpu.vram_used_gb!=null?` · ${gpu.vram_used_gb} GB VRAM`:''}`;else if(state==='ready')text=`${gpu.device||'NVIDIA GPU'} detected · GPU idle (ready)`;else if(state==='disabled')text=`${gpu.device||'NVIDIA GPU'} detected · not in use (local AI is off)`;else text=gpu.message||'NVIDIA GPU not detected by PyTorch';label.textContent=text}
async function loadYoutubeStatus(){try{const r=await fetch('/api/youtube/status');const d=await r.json();$('#youtube-state').textContent=d.connected?'Connected':'Not connected';$('#youtube-dot').classList.toggle('green',d.connected);$('#youtube-detail').textContent=d.connected?`${d.uploads_today} of ${d.max_uploads_per_day} daily uploads used.`:(d.error||'Connect your channel to enable publishing.');$('#connect-youtube').innerHTML=d.connected?'<i class="fa-solid fa-check"></i> Channel connected':'<i class="fa-brands fa-youtube"></i> Connect channel'}catch(e){$('#youtube-state').textContent='Offline'}}
async function loadOllamaStatus(){const pill=$('#ollama-pill'),dot=$('#ollama-dot'),label=$('#ollama-state');try{const r=await fetch('/api/ollama/status',{cache:'no-store'});const d=await r.json();const state=d.state||'error';const configuredModel=String(d.model||'qwen2.5:7b');$('#ollama-model-label').textContent=configuredModel.toLowerCase().startsWith('qwen')?'Qwen':configuredModel.split(':')[0];pill.className=`ollama-pill state-${state}`;pill.title=d.detail||'Ollama status unavailable';dot.className='status-dot';if(state==='running')dot.classList.add('green');else if(['ready','warming','starting','pulling'].includes(state))dot.classList.add('amber');const labels={running:'Running',ready:'Idle · ready',missing:'Model missing',offline:'Offline',disabled:'Disabled',error:'Unavailable',checking:'Checking',starting:'Starting',warming:'Loading',pulling:'Downloading','not-installed':'Not installed'};label.textContent=labels[state]||'Unavailable';const settingsLabel=$('#settings-ollama-status');if(settingsLabel&&$('#settings-dialog').open)settingsLabel.textContent=d.detail||labels[state]||'Ollama status unavailable';const progress=d.runtime&&d.runtime.progress;if(progress!==null&&progress!==undefined&&state==='pulling'&&settingsLabel)settingsLabel.textContent=`${d.runtime.detail} ${progress}%`}catch(e){pill.className='ollama-pill state-offline';pill.title='Could not check the local Ollama service';label.textContent='Offline';dot.className='status-dot';const settingsLabel=$('#settings-ollama-status');if(settingsLabel&&$('#settings-dialog').open)settingsLabel.textContent='Cannot reach the app status service.'} }
function updateOpenRouterFields(){const active=$('#setting-script-provider').value==='openrouter';$('#openrouter-fields').classList.toggle('hidden',!active);const status=$('#settings-openrouter-status');if(status&&active&&!$('#setting-use-ollama').checked)status.textContent='Enable Ollama in the Local AI section: OpenRouter drafts are always reviewed and finalized locally.'}
async function loadOpenRouterModels(selected='',refresh=false){const select=$('#setting-openrouter-model'),status=$('#settings-openrouter-status'),previous=selected||select.value;if(refresh)status.textContent='Loading current free text models from OpenRouter…';try{const response=await fetch('/api/openrouter/free-models',{cache:refresh?'no-store':'default'}),data=await response.json();if(!response.ok)throw new Error(data.error||'Could not load OpenRouter models');const models=Array.isArray(data.models)?data.models:[];select.replaceChildren();const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=models.length?'Select a free model':'No free models are currently listed';select.append(placeholder);for(const model of models){const option=document.createElement('option');option.value=model.id;option.textContent=model.context_length?`${model.name} · ${model.context_length.toLocaleString()} context`:model.name;select.append(option)}if(previous&&!models.some(model=>model.id===previous)){const option=document.createElement('option');option.value=previous;option.textContent=`${previous} · not currently listed`;select.append(option)}select.value=previous||models[0]?.id||'';status.textContent=models.length?`${models.length} free text model${models.length===1?'':'s'} loaded from OpenRouter. Catalog and quotas can change.`:'OpenRouter returned no free text models.';updateOpenRouterFields()}catch(error){if(previous){select.replaceChildren();const option=document.createElement('option');option.value=previous;option.textContent=`${previous} · catalog unavailable`;select.append(option);select.value=previous}else{select.replaceChildren();const option=document.createElement('option');option.value='';option.textContent='Could not load free models';select.append(option)}status.textContent=error.message||'Could not load the OpenRouter model list.'}}
async function loadSettingsForm(){try{const response=await fetch('/api/settings');const s=await response.json();if(!response.ok)throw new Error(s.error||'Could not load settings');$('#setting-use-ollama').checked=!!s.USE_OLLAMA;$('#setting-script-provider').value=s.SCRIPT_PROVIDER||'ollama';$('#setting-openrouter-key').value='';$('#setting-openrouter-key').placeholder=s.OPENROUTER_API_KEY_CONFIGURED?'Key saved — leave blank to keep it':'Paste OpenRouter API key';$('#openrouter-configured').textContent=s.OPENROUTER_API_KEY_CONFIGURED?'Saved locally':'';$('#clear-openrouter-key').checked=false;updateOpenRouterFields();loadOpenRouterModels(s.OPENROUTER_MODEL||'');$('#setting-ollama-model').value=s.OLLAMA_MODEL||'qwen2.5:7b';$('#setting-ollama-url').value=s.OLLAMA_URL||'http://localhost:11434';$('#setting-ollama-exe').value=s.OLLAMA_EXECUTABLE||'';$('#setting-ollama-autostart').checked=!!s.OLLAMA_AUTO_START;$('#setting-ollama-keepalive').checked=!!s.OLLAMA_KEEP_ALIVE;$('#setting-local-images').checked=!!s.USE_LOCAL_IMAGES;$('#setting-local-video').checked=!!s.USE_LOCAL_VIDEO;$('#setting-use-nvenc').checked=s.USE_NVENC!==false;$('#setting-pexels-key').value='';$('#setting-pexels-key').placeholder=s.PEXELS_API_KEY_CONFIGURED?'Key saved — leave blank to keep it':'Paste Pexels API key';$('#pexels-configured').textContent=s.PEXELS_API_KEY_CONFIGURED?'Saved locally':'';$('#clear-pexels-key').checked=false;$('#setting-gpu-memory').value=s.GPU_MEMORY_LIMIT_GB||7;$('#setting-ffmpeg').value=s.FFMPEG_BINARY||'ffmpeg-imageio';$('#setting-voice').value=s.EDGE_TTS_VOICE||'en-US-AriaNeural';$('#setting-tts-engine').value=s.TTS_ENGINE||'chatterbox';$('#setting-chatterbox-python').value=s.CHATTERBOX_PYTHON||'';$('#setting-privacy').value=s.YOUTUBE_PRIVACY_STATUS||'private';$('#setting-category').value=s.YOUTUBE_CATEGORY_ID||'22';$('#setting-upload-limit').value=s.MAX_UPLOADS_PER_DAY||5;$('#setting-batch-delay').value=s.BATCH_DELAY_SECONDS??10;$('#setting-output-dir').value=s.OUTPUT_DIR||'';$('#youtube-credential-label').textContent=s.YOUTUBE_CREDENTIALS_CONFIGURED?'Google OAuth credentials are configured':'Google OAuth credentials not configured';$('#settings-ollama-status').textContent='Checking local model status…';await loadOllamaStatus()}catch(e){toast(e.message||'Could not load app settings')}}
async function saveSettingsForm(){const button=$('#save-settings');button.disabled=true;button.innerHTML='<i class="fa-solid fa-spinner fa-spin"></i> Saving…';const payload={USE_OLLAMA:$('#setting-use-ollama').checked,SCRIPT_PROVIDER:$('#setting-script-provider').value,OPENROUTER_MODEL:$('#setting-openrouter-model').value, CLEAR_OPENROUTER_API_KEY:$('#clear-openrouter-key').checked,OLLAMA_MODEL:$('#setting-ollama-model').value.trim(),OLLAMA_URL:$('#setting-ollama-url').value.trim(),OLLAMA_EXECUTABLE:$('#setting-ollama-exe').value.trim(),OLLAMA_AUTO_START:$('#setting-ollama-autostart').checked,OLLAMA_KEEP_ALIVE:$('#setting-ollama-keepalive').checked,USE_LOCAL_IMAGES:$('#setting-local-images').checked,USE_LOCAL_VIDEO:$('#setting-local-video').checked,USE_NVENC:$('#setting-use-nvenc').checked,GPU_MEMORY_LIMIT_GB:Number($('#setting-gpu-memory').value),FFMPEG_BINARY:$('#setting-ffmpeg').value.trim(),EDGE_TTS_VOICE:$('#setting-voice').value,TTS_ENGINE:$('#setting-tts-engine').value,CHATTERBOX_PYTHON:$('#setting-chatterbox-python').value.trim(),YOUTUBE_PRIVACY_STATUS:$('#setting-privacy').value,YOUTUBE_CATEGORY_ID:$('#setting-category').value.trim(),MAX_UPLOADS_PER_DAY:Number($('#setting-upload-limit').value),BATCH_DELAY_SECONDS:Number($('#setting-batch-delay').value),OUTPUT_DIR:$('#setting-output-dir').value.trim(),CLEAR_PEXELS_API_KEY:$('#clear-pexels-key').checked};const key=$('#setting-pexels-key').value.trim();if(key)payload.PEXELS_API_KEY=key;const openrouterKey=$('#setting-openrouter-key').value.trim();if(openrouterKey)payload.OPENROUTER_API_KEY=openrouterKey;try{const response=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not save settings');$('#settings-dialog').close();$('#setting-pexels-key').value='';$('#setting-openrouter-key').value='';$('#setting-openrouter-key').placeholder=data.OPENROUTER_API_KEY_CONFIGURED?'Key saved — leave blank to keep it':'Paste OpenRouter API key';$('#openrouter-configured').textContent=data.OPENROUTER_API_KEY_CONFIGURED?'Saved locally':'';$('#clear-openrouter-key').checked=false;$('#clear-pexels-key').checked=false;$('#privacy').value=data.YOUTUBE_PRIVACY_STATUS||'private';await loadOllamaStatus();await loadYoutubeStatus();toast('Settings saved on this PC.')}catch(e){toast(e.message||'Could not save settings')}finally{button.disabled=false;button.innerHTML='<i class="fa-solid fa-check"></i> Save settings'}}
async function startOllamaFromSettings(){const label=$('#settings-ollama-status');label.textContent='Starting Ollama and loading the selected model…';try{const r=await fetch('/api/ollama/start',{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.error||'Could not start Ollama');label.textContent=d.detail||'Startup requested.';setTimeout(loadOllamaStatus,1200)}catch(e){label.textContent=e.message;toast(e.message)}}
async function pullOllamaFromSettings(){const model=$('#setting-ollama-model').value.trim()||'qwen2.5:7b';if(!window.confirm(`Download ${model}? This may use several gigabytes of disk space and internet data.`))return;const label=$('#settings-ollama-status');label.textContent=`Starting download of ${model}…`;try{const r=await fetch('/api/ollama/pull',{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.error||'Could not start model download');label.textContent=d.detail||'Download started.';loadOllamaStatus()}catch(e){label.textContent=e.message;toast(e.message)}}
async function uploadYoutubeCredentials(){const input=$('#youtube-credentials-file');const file=input.files&&input.files[0];if(!file)return;const label=$('#youtube-credential-label');label.textContent='Saving OAuth credentials…';try{const form=new FormData();form.append('file',file);const r=await fetch('/api/youtube/credentials',{method:'POST',body:form});const d=await r.json();if(!r.ok)throw new Error(d.error||'Could not save credentials');label.textContent='Google OAuth credentials are configured';await loadYoutubeStatus();toast('Google OAuth credentials saved locally.')}catch(e){label.textContent=e.message;toast(e.message)}finally{input.value=''}}
async function loadHistory(){try{const r=await fetch('/api/batch/list');const d=await r.json();const list=d.batches||[];$('#queue-count').textContent=list.filter(x=>x.status==='running'||x.status==='queued').length;if(!list.length){$('#batch-history').innerHTML='<div class="empty-history"><span><i class="fa-regular fa-folder-open"></i></span><b>Your workspace is ready</b><small>Created batches will show up here.</small></div>';return}$('#batch-history').innerHTML=list.slice(0,5).map(b=>`<div class="history-card"><div><b>${b.total} video${b.total===1?'':'s'} · ${escapeHtml(b.batch_id)}</b><small>${new Date(b.created_at).toLocaleString()} · ${b.completed}/${b.total} processed</small></div><span class="history-state">${escapeHtml(b.status.toUpperCase())}</span></div>`).join('')}catch(e){}}
async function loadSavedDefaults(){try{const r=await fetch('/api/settings');const s=await r.json();if(s.YOUTUBE_PRIVACY_STATUS)$('#privacy').value=s.YOUTUBE_PRIVACY_STATUS}catch(e){}}
loadYoutubeStatus();loadOllamaStatus();loadHistory();loadSavedDefaults();setInterval(loadOllamaStatus,5000);
