'use strict';
const BASE='http://127.0.0.1:8000/api/bliss-studio';
let looping=false;
let loopAgain=false;
let lastRenderWake=0;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const accountPathFor=url=>new URL(url).pathname.match(/^\/u\/\d+(?=\/|$)/)?.[0]||'/u/0';
const accountScopedUrl=(accountPath,pathname='/prompts/new_chat')=>`https://aistudio.google.com${accountPath}${pathname}`;
async function api(path,body,token){
  const response=await fetch(BASE+path,{method:'POST',headers:{'Content-Type':'application/json','X-Narration-Extension':chrome.runtime.id,...(token?{Authorization:'Bearer '+token}:{})},body:JSON.stringify(body||{}),signal:AbortSignal.timeout(12000)});
  const value=await response.json();
  if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'Local browser connection failed. Check the app and pair again.');
  return value;
}
async function chosenTab(action){
  const {tabId,accountPath}=await chrome.storage.local.get(['tabId','accountPath']);
  if(!Number.isInteger(tabId))throw Error('Open AI Studio in your normal browser, then connect that tab from the extension popup.');
  let tab;
  try{
    tab=await chrome.tabs.get(tabId);
  }catch(error){
    if(!/No tab with id/i.test(error.message))throw error;
    // Never silently create a replacement tab: it may be signed into a different
    // Google account from the one the user explicitly paired.
    throw Object.assign(Error('The paired AI Studio tab was closed. Open AI Studio on the Google account you want, pair that tab again, then Resume. No batch was sent.'),{code:'tab_lost'});
  }
  if(!tab.url?.startsWith('https://aistudio.google.com/'))throw Error('The selected tab is not ready in AI Studio. Finish signing in yourself, then Resume in the app.');
  if(accountPath&&accountPathFor(tab.url)!==accountPath)throw Object.assign(Error(`The paired AI Studio tab changed Google accounts (expected account slot ${accountPath}). Open AI Studio on the intended account, pair that tab again, then Resume.`),{code:'tab_lost'});
  return tab;
}
async function renderingWindow(tab){
  // One paired tab in its own window can stay active while Stage 5 is viewed.
  const {narrationWindowId}=await chrome.storage.local.get('narrationWindowId');
  if(narrationWindowId!==tab.windowId){
    const window=await chrome.windows.create({tabId:tab.id,type:'normal',focused:true,state:'maximized'});
    lastRenderWake=Date.now();
    await chrome.storage.local.set({narrationWindowId:window.id});
    tab=await chrome.tabs.get(tab.id);
  }
  const window=await chrome.windows.get(tab.windowId);
  if(window.state!=='maximized')await chrome.windows.update(window.id,{state:'maximized'});
  if(!tab.active)await chrome.tabs.update(tab.id,{active:true});
  return tab;
}
async function ready(tabId,previousDocument,expiresAt=Date.now()+25000){
  for(let i=0;i<60&&Date.now()<expiresAt;i++){
    try{const value=await chrome.tabs.sendMessage(tabId,{type:'narration-command',action:'ping'});if(value?.ready&&value.documentId!==previousDocument)return value;}catch(_){}
    await delay(500);
  }
  throw Error('The AI Studio tab did not become ready. Finish signing in yourself, then Resume.');
}
async function execute(command){
  if(!['hello','prepare','snapshot','send','recover'].includes(command.action))throw Error('Unknown narration action.');
  let tab=await chosenTab(command.action);
  const {token,accountPath='/u/0'}=await chrome.storage.local.get(['token','accountPath']);
  const progress=step=>api('/progress',{id:command.id,step},token);
  if(Date.now()>command.expiresAt)throw Error('This browser action expired. Inspect the tab before resuming.');
  if(command.action==='hello'){const value=await ready(tab.id,undefined,command.expiresAt);return {url:tab.url,protocol:value.protocol,window_visibility:true};}
  tab=await renderingWindow(tab);
  if(command.action==='prepare'&&command.payload.refresh){
    await progress('opening_tab');
    let previousDocument;
    try{previousDocument=(await chrome.tabs.sendMessage(tab.id,{type:'narration-command',action:'ping'}))?.documentId;}catch(_){}
    await chrome.tabs.reload(tab.id);
    await ready(tab.id,previousDocument,Math.min(command.expiresAt,Date.now()+30000));
  }
  if(command.action==='recover'||(command.action==='prepare'&&command.payload.fresh)){
    let url=command.action==='recover'?command.payload.url:accountScopedUrl(accountPath);
    const parsed=new URL(url);
    if(parsed.origin!=='https://aistudio.google.com'||!/^\/(?:u\/\d+\/)?prompts\//.test(parsed.pathname)||parsed.search||parsed.hash)throw Error('Invalid AI Studio conversation address.');
    // Google AI Studio uses /u/N to select a signed-in account. Preserve the
    // account explicitly paired by the user even when restoring an old chat URL.
    const conversationPath=parsed.pathname.replace(/^\/u\/\d+(?=\/)/,'');
    url=accountScopedUrl(accountPath,conversationPath);
    let previousDocument;
    await progress('opening_tab');
    try{previousDocument=(await chrome.tabs.sendMessage(tab.id,{type:'narration-command',action:'ping'}))?.documentId;}catch(_){}
    await chrome.tabs.update(tab.id,{url});
    // Wait for the new document, so an old content script cannot prepare a new batch.
    let loaded=false;
    for(let i=0;i<70&&Date.now()<command.expiresAt;i++){await delay(300);const current=await chrome.tabs.get(tab.id);if(current.status==='complete'&&current.url===url){loaded=true;break;}}
    if(!loaded)throw Error('AI Studio navigation did not reach the requested chat. Nothing was submitted. Check the selected tab and resume.');
    await ready(tab.id,previousDocument,command.expiresAt);
    if(command.action==='recover')return {url};
  }else await ready(tab.id,undefined,command.expiresAt);
  if(Date.now()>command.expiresAt)throw Error('This browser action expired. Inspect the tab before resuming.');
  if(command.action==='send'){await progress('submitting');await api('/permit',{id:command.id},token);}
  const message={type:'narration-command',action:command.action,payload:command.payload,expiresAt:command.expiresAt,commandId:command.id};
  let value=await chrome.tabs.sendMessage(tab.id,message);
  const unfocusedPending=value?.focused===false&&!value?.finished&&Date.now()-lastRenderWake>=15000;
  if(command.action==='snapshot'&&(value?.visible===false||unfocusedPending)){
    // Some Windows browsers also defer rendering when a window is occluded.
    // Wake only this paired window; never replay send/prepare or reload the chat.
    await chrome.windows.update(tab.windowId,{focused:true});
    lastRenderWake=Date.now();
    await delay(500);
    if(Date.now()>command.expiresAt)throw Error('The reply read expired while waking AI Studio. Inspect its tab before resuming.');
    value=await chrome.tabs.sendMessage(tab.id,message);
    if(value?.visible===false)return {...value,generating:true,finished:false};
  }
  return value;
}
async function loop(){
  if(looping){loopAgain=true;return;}looping=true;
  try{
    while(true){
      const {token}=await chrome.storage.local.get('token');if(!token)break;
      try{
        const {command}=await api('/poll',{},token);
        await chrome.storage.local.set({connectionNote:'Connected to the local app.'});
        if(command){
          let value;
          const heartbeat=setInterval(()=>api('/heartbeat',{},token).catch(()=>{}),5000);
          try{value=await execute(command);}catch(error){value={error:error.message,code:error.code||(/No tab with id/i.test(error.message)?'tab_lost':'extension'),retry_safe:command.action==='prepare'};}finally{clearInterval(heartbeat);}
          // Retrying this acknowledgement is safe; executing the command again is not.
          let saved=false;
          for(let i=0;i<3&&!saved;i++){try{await api('/result',{id:command.id,value},token);saved=true;}catch(_){await delay(500);}}
          if(!saved)throw Error('The app did not acknowledge the last browser action. Check queue status before resuming.');
        }
      }catch(error){await chrome.storage.local.set({connectionNote:error.message});break;}
      await delay(2000);
    }
  }finally{looping=false;if(loopAgain){loopAgain=false;queueMicrotask(()=>loop());}}
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(sender.id!==chrome.runtime.id)return;
  if(message.type==='progress'&&sender.tab&&sender.url?.startsWith('https://aistudio.google.com/')){
    chrome.storage.local.get(['token','tabId']).then(async value=>{
      if(value.tabId!==sender.tab.id)throw Error('This tab is not paired.');
      return api('/progress',{id:message.id,step:message.step},value.token);
    }).then(reply,error=>reply({error:error.message}));return true;
  }
  if(!sender.url?.startsWith(chrome.runtime.getURL('')))return;
  if(message.type==='pair'){
    (async()=>{
      const [activeTab]=await chrome.tabs.query({active:true,currentWindow:true});
      let tab=activeTab;
      if(!tab?.url?.startsWith('https://aistudio.google.com/')){
        const {tabId}=await chrome.storage.local.get('tabId');
        if(Number.isInteger(tabId))try{tab=await chrome.tabs.get(tabId);}catch(_){}
      }
      if(!tab?.url?.startsWith('https://aistudio.google.com/'))throw Error('Open AI Studio on the account you want, then connect from that tab.');
      const value=await api('/pair',{code:message.code});
      const accountPath=accountPathFor(tab.url);
      await chrome.storage.local.set({token:value.token,tabId:tab.id,accountPath,pairedTabTitle:tab.title||tab.url,narrationWindowId:null,connectionNote:`Paired to AI Studio ${accountPath} tab ${tab.id}: ${tab.title||tab.url}`});
      loop();return {ok:true,tabId:tab.id,tabTitle:tab.title||tab.url,accountPath};
    })().then(reply,error=>reply({error:error.message}));return true;
  }
  if(message.type==='wake'){loop();reply({ok:true});}
});
chrome.alarms.create('narration-connection',{periodInMinutes:.5});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='narration-connection')loop();});
chrome.runtime.onStartup.addListener(()=>loop());
loop();
