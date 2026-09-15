'use strict';
(() => {
  const config=window.hamSdrSiteConfig;
  if(!config)return;
  document.title=`${config.receiver_name} · Unstable`;
  const heading=document.getElementById('site-name');
  const logo=document.getElementById('site-logo');
  const description=document.getElementById('site-details');
  const footerOperator=document.getElementById('site-footer-operator');
  const footerText=document.getElementById('site-footer-text');
  heading.textContent=config.receiver_name;
  logo.hidden=!config.logo.enabled;
  if(config.logo.enabled){logo.src=config.logo.url;logo.alt=config.logo.alt;}
  description.querySelectorAll('[data-site-description]').forEach(item=>item.remove());
  description.prepend(...config.description.map(line=>{
    const item=document.createElement('li');item.dataset.siteDescription='';item.textContent=line;return item;
  }));
  footerOperator.textContent=config.callsign;
  document.getElementById('site-footer-operator-row').hidden=!config.show_admin;
  footerText.textContent=`HamSDR v${config.version}`;
})();
