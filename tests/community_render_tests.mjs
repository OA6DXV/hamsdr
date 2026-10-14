// SPDX-License-Identifier: GPL-3.0-only
// Dependency-free DOM contract test for incremental live chat updates.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.style={};this.value='';this.scrollTop=0;this.clientHeight=10;this.replacements=0;}
  addEventListener(){}
  get scrollHeight(){return this.children.length;}
  get firstElementChild(){return this.children[0]??null;}
  get lastElementChild(){return this.children.at(-1)??null;}
  get previousElementSibling(){const siblings=this.parent?.children??[];return siblings[siblings.indexOf(this)-1]??null;}
  append(...items){
    for(const item of items){
      if(item.tag==='fragment')this.append(...item.children.splice(0));
      else{item.parent=this;this.children.push(item);}
    }
  }
  prepend(item){item.parent=this;this.children.unshift(item);}
  after(item){const siblings=this.parent.children;item.parent=this.parent;siblings.splice(siblings.indexOf(this)+1,0,item);}
  remove(){const siblings=this.parent?.children;if(siblings)siblings.splice(siblings.indexOf(this),1);}
  replaceChildren(...items){this.replacements++;this.children=[];this.append(...items);}
}

const elements=new Map();
const getElementById=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const handlers=new Map();
const storage=()=>{const values=new Map();return{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};};
const context={
  document:{getElementById,createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('fragment'),
    createTextNode:text=>Object.assign(new Element('text'),{textContent:text})},
  window:{addEventListener:(name,handler)=>handlers.set(name,handler),radioSend:()=>{}},
  crypto:{randomUUID:()=> '01234567-89ab-cdef-0123-456789abcdef'},
  sessionStorage:storage(),localStorage:storage(),setTimeout:()=>{},
};
vm.runInNewContext(readFileSync(new URL('../web/community.js',import.meta.url),'utf8'),context);
const dispatch=(type,data)=>handlers.get('radio-event')({detail:{type,...data}});
const event=id=>({id,kind:'chat',time:1700000000,name:'N0CALL',text:`message ${id}`,
  frequency:7100000,mode:'USB',call:''});
dispatch('history',{kind:'chat',before:0,more:false,events:Array.from({length:1000},(_,index)=>event(index+1))});
const chat=getElementById('chatbox');
assert.equal(chat.children.length,1000);
assert.equal(chat.replacements,1);
const original=chat.firstElementChild;
dispatch('event',{event:event(1001)});
assert.equal(chat.children.length,1001);
assert.equal(chat.replacements,1,'one new message must not rebuild history');
assert.equal(chat.firstElementChild,original,'previous rows retain DOM identity');
dispatch('ack',{event:event(1001),request_id:'absent'});
assert.equal(chat.children.length,1001,'ack must not duplicate a broadcast event');
dispatch('event',{event:event(999)});
assert.equal(chat.children.length,1001,'an existing older id must not duplicate a row');
dispatch('history',{kind:'chat',before:0,more:false,events:Array.from({length:10000},(_,index)=>event(index+2))});
assert.equal(chat.children.length,10000);
dispatch('ack',{event:event(1),request_id:'old-request'});
dispatch('ack',{event:event(1),request_id:'old-request'});
assert.equal(chat.children.length,10000,'an evicted acknowledgement must not add stale DOM rows');
assert.equal(chat.firstElementChild.dataset.id,2,'the oldest retained event must remain first');
console.log('community incremental rendering: ok');
