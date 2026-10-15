// SPDX-License-Identifier: GPL-3.0-only
'use strict';
(() => {
  const languages=['es','en','pt-BR'],storageKey='hamsdr-language';
  let language='es';
  try{const saved=localStorage.getItem(storageKey);if(languages.includes(saved))language=saved;}catch{}
  const catalog=new Map(),patterns=[];
  const escape=value=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  for(const [source,en,pt,es=source] of window.HamTranslations){
    const entry={es,en,'pt-BR':pt};catalog.set(source,entry);
    if(source.includes('{')){
      const names=[];let cursor=0,expression='^';
      for(const match of source.matchAll(/\{([a-z]+)\}/g)){
        expression+=escape(source.slice(cursor,match.index))+'(.*?)';names.push(match[1]);cursor=match.index+match[0].length;
      }
      expression+=escape(source.slice(cursor))+'$';
      patterns.push({expression:new RegExp(expression,'s'),names,entry});
    }
  }
  function text(source,depth=0){
    if(typeof source!=='string'||!source||depth>4)return source;
    const direct=catalog.get(source);if(direct)return direct[language];
    for(const {expression,names,entry} of patterns){
      const match=expression.exec(source);if(!match)continue;
      const values=Object.fromEntries(names.map((name,index)=>[name,text(match[index+1],depth+1)]));
      return entry[language].replace(/\{([a-z]+)\}/g,(_,key)=>values[key]);
    }
    return source;
  }

  // These are application data, not UI strings. Never translate user submissions,
  // station customization, decoder output, memory names or callsigns by content.
  const excluded='script,style,[data-i18n-ignore],#language option,#site-name,#site-details,#site-logo,#site-footer-operator,#site-footer-text,#chatbox,#logbook,#digital-log tbody,#rtty-terminal,#cw-terminal,#rtty-multi-table tbody tr:not(.rtty-multi-empty) td:nth-child(3),#cw-streams tbody td:nth-child(3),#markers,#users,#memories option:not([value=""])';
  const sources=new WeakMap(),attributes=new WeakMap();
  const skip=node=>!!(node.nodeType===1?node:node.parentElement)?.closest(excluded);
  function translateText(node){
    if(skip(node))return;
    const current=node.nodeValue,saved=sources.get(node),source=saved?.last===current?saved.source:current;
    const match=/^(\s*)([\s\S]*?)(\s*)$/.exec(source);
    const translated=match[1]+text(match[2])+match[3];
    sources.set(node,{source,last:translated});if(current!==translated)node.nodeValue=translated;
  }
  const translatedAttributes=['title','aria-label','placeholder'];
  function translateAttributes(element){
    if(skip(element))return;
    const saved=attributes.get(element)||{};
    for(const name of translatedAttributes){
      const current=element.getAttribute(name);if(current===null)continue;
      const source=saved[name]?.last===current?saved[name].source:current,translated=text(source);
      saved[name]={source,last:translated};if(current!==translated)element.setAttribute(name,translated);
    }
    attributes.set(element,saved);
  }
  function translate(root=document.body){
    if(skip(root))return;
    if(root.nodeType===3){translateText(root);return;}
    if(root.nodeType!==1)return;
    translateAttributes(root);
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT,{
      acceptNode:node=>skip(node)?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT,
    });
    while(walker.nextNode()){
      const node=walker.currentNode;if(node.nodeType===3)translateText(node);else translateAttributes(node);
    }
  }
  function listeners(){
    const label=document.getElementById('listeners-label');if(!label)return;
    const source=Number(document.getElementById('listeners').textContent)===1?'usuario conectado.':'usuarios conectados.';
    const node=label.firstChild||label.appendChild(document.createTextNode(''));
    const last=text(source);sources.set(node,{source,last});if(node.nodeValue!==last)node.nodeValue=last;
  }
  function setLanguage(value){
    if(!languages.includes(value))return false;
    language=value;document.documentElement.lang=language;
    const select=document.getElementById('language');if(select)select.value=language;
    try{localStorage.setItem(storageKey,language);}catch{}
    translate();listeners();
    window.dispatchEvent(new CustomEvent('languagechange',{detail:{language}}));return true;
  }
  const observer=new MutationObserver(records=>{
    // Translate only changed UI subtrees. FFT canvases and log data never trigger
    // a full-page scan; changing language does not restart receiver/decoder work.
    const roots=new Set();let changedListeners=false;
    for(const record of records){
      if(record.target.id==='listeners')changedListeners=true;
      if(skip(record.target))continue;
      if(record.type==='attributes')translateAttributes(record.target);
      else if(record.type==='characterData')translateText(record.target);
      else for(const node of record.addedNodes)roots.add(node);
    }
    for(const root of roots)translate(root);
    if(changedListeners)listeners();
  });
  window.HamI18n={t:text,translate,setLanguage,country:value=>window.HamCountries?.name(value,language)||value,get language(){return language;}};
  document.documentElement.lang=language;
  const select=document.getElementById('language');select.value=language;select.addEventListener('change',()=>setLanguage(select.value));
  translate();listeners();
  observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:translatedAttributes});
})();
