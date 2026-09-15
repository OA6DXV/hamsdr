'use strict';
(() => {
  const config=window.hamSdrSiteConfig;
  if(!config)return;
  document.documentElement.lang=config.language;
  document.title=`${config.receiver_name} · Unstable`;
  const heading=document.getElementById('site-name');
  const logo=document.getElementById('site-logo');
  const description=document.getElementById('site-details');
  const operator=document.getElementById('site-operator');
  const footerOperator=document.getElementById('site-footer-operator');
  const footerText=document.getElementById('site-footer-text');
  heading.textContent=config.receiver_name;
  logo.hidden=!config.logo.enabled;
  if(config.logo.enabled){logo.src=config.logo.url;logo.alt=config.logo.alt;}
  description.querySelectorAll('[data-site-description]').forEach(item=>item.remove());
  description.prepend(...config.description.map(line=>{
    const item=document.createElement('li');item.dataset.siteDescription='';item.textContent=line;return item;
  }));
  const applyOperator=element=>{
    element.textContent=config.administrator.name;
    if(config.administrator.url){element.href=config.administrator.url;element.hidden=false;}
    else{element.removeAttribute('href');element.hidden=false;}
  };
  document.getElementById('site-operator-label').textContent=`${config.administrator_label} `;
  document.getElementById('site-footer-operator-label').textContent=`${config.footer_administrator_label}: `;
  applyOperator(operator);applyOperator(footerOperator);
  footerText.textContent=`HamSDR v${config.version}`;
})();
