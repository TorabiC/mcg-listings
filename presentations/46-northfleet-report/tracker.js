/* MCG page engagement tracker (reusable)
   Source of truth: TorabiC/mcg-listings, presentations/46-northfleet-report/tracker.js
   Backup: /mnt/project-files/46-northfleet/webflow/tracker.js
   First used: 46 Northfleet Drive report, November 17, 2026.

   What it does: logs one row per visit to the private Airtable base "MCG Page Engagement"
   (table Visits, view "Seller visits") through the Make webhook below. The row is upserted
   about every 45 seconds while the page is in use, and again when the visitor leaves.

   To add it to a new presentation page:
   1. Inline this file in a <script> on the page (prod.py does this for the 46 Northfleet build).
   2. Give the page a slug: <html lang="en" data-trk-page="your-page-slug"> (a-z, 0-9, hyphens).
   3. Mark each section to time with data-sec="Name", e.g. <section data-sec="Market">.
   4. Log clicks with TRK.ev(key, label). Keys with readable names are in LABEL below; the keys in
      HOME_K also add the label to the row's Homes list.
   5. If the page is iframed into Webflow, the parent embed passes the owner/test flag in its
      nfParent postMessage ({type:"nfParent", test:true}); the page's handler calls
      __trk.touch() and __trk.setTest(true). See embed-new.html beside the backup.
   Owner and test visits: open the Webflow page once with ?mcgowner=1 (clears with ?mcgowner=0),
   or add ?mcgtest=1 for a single visit. Those rows get the Test box ticked and are hidden from
   the Seller visits view.
   The same webhook and table serve every page; the Page column tells them apart.
   No keys or passwords live here: the webhook only accepts this payload shape (Make filter). */
const TRK=(function(){
  const HOOK="https://hook.us2.make.com/jsmmjd4g82qy644ixstrhgmy8h476rvm";
  const PAGE=(document.documentElement.dataset.trkPage||"page").toLowerCase().replace(/[^a-z0-9-]/g,"").slice(0,60)||"page";
  const LABEL={tab:"Tab",nav:"Menu",map_layer:"Map layer",map_pin:"Map pin",history_open:"Opened history",history_close:"Closed history",
    card_map:"Card map",jump_from_map:"Jumped from map",listing_link:"Opened listing",rental_link:"Opened rental listing",an_jump:"Analytics jump",
    an_metric:"Analytics metric",an_period:"Analytics period",an_compare:"Analytics compare",vm_mode:"View mode",section:"Section"};
  const HOME_K={history_open:1,map_pin:1,listing_link:1,rental_link:1,card_map:1};
  const rid=()=>{let s="";const a=new Uint8Array(10);(window.crypto||{getRandomValues:x=>x.map(()=>Math.random()*256|0)}).getRandomValues(a);a.forEach(b=>s+=("0"+b.toString(16)).slice(-2));return s;};
  const clean=v=>String(v==null?"":v).replace(/[\u0000-\u001f\u007f"\\<>]/g," ").replace(/\s+/g," ").trim().slice(0,90);
  let vid;try{vid=localStorage.getItem("mcgVid");if(!/^[a-z0-9]{8,32}$/.test(vid||"")){vid=rid();localStorage.setItem("mcgVid",vid);}}catch(e){vid="ns-"+rid().slice(0,12);}
  const visit=rid(), started=new Date().toISOString();
  let test=false, active=0, lastInput=Date.now(), lastAt=started, dirty=true, sentSig="", lastSend=0;
  const evs=[], homes=[], secT={};
  const ua=navigator.userAgent||"";
  const device=clean((/iPad|Tablet/.test(ua)||(/Macintosh/.test(ua)&&navigator.maxTouchPoints>1)?"Tablet":/Mobi|iPhone|Android/.test(ua)?"Phone":"Desktop")+" · "+
    (/Edg\//.test(ua)?"Edge":/CriOS|Chrome\//.test(ua)?"Chrome":/FxiOS|Firefox\//.test(ua)?"Firefox":/Safari\//.test(ua)?"Safari":"Other")+" · "+
    (/iPhone|iPad/.test(ua)?"iOS":/Android/.test(ua)?"Android":/Mac OS X/.test(ua)?"Mac":/Windows/.test(ua)?"Windows":"Other"));
  const tfmt=d=>{try{return d.toLocaleTimeString("en-US",{timeZone:"America/Chicago",hour:"numeric",minute:"2-digit",second:"2-digit"});}catch(e){return d.toISOString().slice(11,19);}};
  function touch(){lastInput=Date.now();}
  ["pointerdown","pointermove","keydown","wheel","touchstart","scroll"].forEach(t=>addEventListener(t,touch,{passive:true}));
  function ev(k,l){
    touch();const lab=clean(l);if(evs.length<300)evs.push(tfmt(new Date())+"  "+(LABEL[k]||clean(k))+(lab?": "+lab:""));
    if(HOME_K[k]&&lab&&homes.indexOf(lab)<0&&homes.length<60)homes.push(lab);
    lastAt=new Date().toISOString();dirty=true;
  }
  function curSec(){
    const mid=(typeof VH==="function"?VH():innerHeight)/2;let name="";
    document.querySelectorAll("[data-sec]").forEach(s=>{const r=typeof VR==="function"?VR(s):s.getBoundingClientRect();if(r.top<=mid&&r.bottom>=mid)name=s.dataset.sec;});
    return name;
  }
  let lastSec="";
  setInterval(()=>{
    if(document.visibilityState!=="visible"||Date.now()-lastInput>90000)return;
    active+=5;const s=curSec();if(s){secT[s]=(secT[s]||0)+5;if(s!==lastSec){lastSec=s;ev("section",s);}}
    lastAt=new Date().toISOString();dirty=true;
  },5000);
  function payload(){
    const secs=Object.keys(secT).sort((a,b)=>secT[b]-secT[a]).map(k=>k+": "+(secT[k]/60).toFixed(1)+" min");
    const tail=evs.length>120?["(first "+(evs.length-120)+" events not shown)"].concat(evs.slice(-120)):evs;
    return new URLSearchParams({visit:visit,page:PAGE,vid:vid,started:started,last:lastAt,mins:(active/60).toFixed(1),device:device,
      sections:secs.join("\\n"),homes:homes.join("\\n"),count:String(evs.length),events:tail.join("\\n"),test:test?"1":"0"});
  }
  function send(final){
    if(!dirty&&!final)return;
    if(active<5&&evs.length===0)return; /* ignore bounces with no activity */
    const body=payload(),sig=body.toString();if(sig===sentSig)return;
    sentSig=sig;dirty=false;lastSend=Date.now();
    try{if(final&&navigator.sendBeacon&&navigator.sendBeacon(HOOK,body))return;}catch(e){}
    try{fetch(HOOK,{method:"POST",body:body,mode:"no-cors",keepalive:true,credentials:"omit"});}catch(e){}
  }
  setInterval(()=>{if(Date.now()-lastSend>=45000)send(false);},15000);
  addEventListener("pagehide",()=>send(true));
  document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="hidden")send(true);});
  function setTest(v){if(v&&!test){test=true;dirty=true;}}
  try{if(/(^|[?&])mcgtest=1(&|$)/.test(location.search))setTest(true);}catch(e){}
  window.__trk={touch:touch,setTest:setTest};
  return {ev:ev,state:()=>"on"};
})();
