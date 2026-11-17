'use strict';
const get=id=>document.getElementById(id);
chrome.storage.local.get('connectionNote').then(v=>get('status').textContent=v.connectionNote||'Not connected yet.');
get('connect').addEventListener('click',async()=>{
  get('connect').disabled=true;
  try{
    const result=await chrome.runtime.sendMessage({type:'pair',code:get('code').value.trim()});
    if(result?.error)throw Error(result.error);
    if(result?.ok!==true)throw Error('The extension did not confirm pairing. Reload the extension and try a new code.');
    get('code').value='';get('status').textContent=`Paired to AI Studio tab ${result.tabId}: ${result.tabTitle}. Return to Bliss Narration to start or resume.`;
  }catch(error){get('status').textContent=error.message;}finally{get('connect').disabled=false;}
});
get('open').addEventListener('click',async()=>{
  const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
  const match=tab?.url?.match(/^https:\/\/aistudio\.google\.com(\/u\/\d+)?(?:\/|$)/);
  chrome.tabs.create({url:`https://aistudio.google.com${match?.[1]||'/u/0'}/prompts/new_chat`});
});
chrome.runtime.sendMessage({type:'wake'}).catch(()=>{});
