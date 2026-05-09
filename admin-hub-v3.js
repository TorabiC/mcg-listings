/**
 * MCG Marketing Hub v5.0 — Full React Dashboard
 * Replaces the Webflow admin-hub static content with the complete Marketing Hub UI.
 * Includes auth (login screen + JWT token management).
 * Backend: https://mcg-dashboard-production.up.railway.app
 */
(function() {
  'use strict';

  // ── Step 1: Hide Webflow chrome, set base styles ──────────────────────
  var styleEl = document.createElement('style');
  styleEl.textContent = `*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Lato','Helvetica Neue',Arial,sans-serif!important;background:#f5f0eb!important;color:#16162a;overflow-x:hidden}
.w-nav,.w-nav-bar,.w-nav-button,.navbar,nav,header,.mcg-header,.navbar-wrapper,.nav-wrapper,.nav-menu-2,.w-container{display:none!important}
.page-wrapper,.main-wrapper{padding:0!important;margin:0!important;max-width:none!important}
::-webkit-scrollbar{width:6px}
::-webkit-scrollbar-track{background:#e8e3dc}
::-webkit-scrollbar-thumb{background:#c4a35a;border-radius:3px}
::selection{background:#c4a35a;color:#fff}`;
  document.head.appendChild(styleEl);

  // ── Step 2: Inject Google Fonts ───────────────────────────────────────
  var fontLink = document.createElement('link');
  fontLink.rel = 'stylesheet';
  fontLink.href = 'https://fonts.googleapis.com/css2?family=Lato:wght@100;300;400;700;900&display=swap';
  document.head.appendChild(fontLink);

  // ── Step 3: Create root mount point ──────────────────────────────────
  function ensureRoot() {
    var r = document.getElementById('mcg-hub-root');
    if (!r) {
      r = document.createElement('div');
      r.id = 'mcg-hub-root';
      document.body.appendChild(r);
    }
    return r;
  }

  // ── Step 4: Load React, ReactDOM, then Babel, then boot app ──────────
  function loadScript(src, onload) {
    var s = document.createElement('script');
    s.src = src;
    s.crossOrigin = 'anonymous';
    s.onload = onload;
    s.onerror = function() { console.error('Failed to load:', src); };
    document.head.appendChild(s);
  }

  function bootApp() {
    // Inject the Babel/JSX code as a text/babel script so Babel processes it
    var babelScript = document.createElement('script');
    babelScript.type = 'text/babel';
    babelScript.setAttribute('data-presets', 'react');
    babelScript.textContent = `
// ─── AUTH ───────────────────────────────────────────
const API_BASE='https://mcg-dashboard-production.up.railway.app';
const TOKEN_KEY='mcg_admin_token';
const TOKEN_TS_KEY='mcg_admin_token_ts';
const TOKEN_TTL=23*60*60*1000;
const getToken=()=>{const t=localStorage.getItem(TOKEN_KEY);const ts=parseInt(localStorage.getItem(TOKEN_TS_KEY)||'0',10);return(t&&Date.now()-ts<TOKEN_TTL)?t:null;};
const storeToken=t=>{localStorage.setItem(TOKEN_KEY,t);localStorage.setItem(TOKEN_TS_KEY,Date.now().toString());};
const clearToken=()=>{localStorage.removeItem(TOKEN_KEY);localStorage.removeItem(TOKEN_TS_KEY);};

const {useState,useEffect,useRef,Fragment}=React;

// ─── BRAND ───────────────────────────────────────
const B={
  navy:'#16162a',crimson:'#ab012e',cta:'#b80003',gold:'#c4a35a',
  cream:'#f5f0eb',cream2:'#faf7f3',white:'#fff',dark:'#2a2a3a',
  gray100:'#f5f2ee',gray200:'#e8e3dc',gray400:'#a09888',gray600:'#6b6258',gray800:'#3d3832',
  font:"'Lato','Helvetica Neue',Arial,sans-serif"
};

// ─── ICONS (SVG paths) ──────────────────────────
const Icon=({name,size=20,color='currentColor'})=>{
  const d={
    home:'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-4 0a1 1 0 01-1-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 01-1 1',
    plus:'M12 4v16m8-8H4',
    grid:'M4 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM14 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1V5zM4 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1v-4zM14 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z',
    mail:'M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z',
    doc:'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
    book:'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253',
    send:'M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z',
    code:'M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4',
    check:'M5 13l4 4L19 7',
    eye:'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z',
    star:'M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z',
    map:'M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z',
    settings:'M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z',
    image:'M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z',
    edit:'M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z',
    copy:'M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3',
    chart:'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
    blog:'M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2',
    download:'M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4',
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={d[name]||d.home}/></svg>;
};

// ─── DATA ────────────────────────────────────────
const SAMPLE_LISTINGS=[
  {id:1,street:'407 Old Forge Dr',city:'Bentonville',state:'AR',zip:'72712',price:549900,beds:4,baths:3,sqft:2847,type:'Residential',status:'Published',mls:'MLS-240871',flipBookReady:true,flyerReady:true,emailTemplateReady:true,embedReady:true,configValid:true,auditPassed:true,url:'https://masoncapitalgroup.com/properties/407-old-forge-dr'},
  {id:2,street:'610 W Central Ave',city:'Bentonville',state:'AR',zip:'72712',price:1580000,beds:0,baths:2,sqft:3200,type:'Commercial',status:'OM Generated',mls:'MLS-240955',flipBookReady:true,flyerReady:false,emailTemplateReady:false,embedReady:false,configValid:true,auditPassed:true,url:'https://masoncapitalgroup.com/properties/610-w-central-ave'}
];

const EMAIL_TEMPLATES=[
  {id:1,name:'Buyer Welcome Drip',subject:'Welcome to Mason Capital Group',type:'drip',steps:5},
  {id:2,name:'Seller Welcome Drip',subject:'Your Property Journey Starts Here',type:'drip',steps:4},
  {id:3,name:'Monthly Market Update',subject:'NWA Market Update — {month}',type:'newsletter',steps:1},
  {id:4,name:'New Listing Announcement',subject:'Just Listed: {address}',type:'announcement',steps:1},
  {id:5,name:'Lead Nurture Sequence',subject:'Northwest Arkansas Real Estate Insights',type:'drip',steps:8},
  {id:6,name:'Newsletter',subject:'MCG Monthly — {month}',type:'newsletter',steps:1}
];

const OM_PAGES=['Cover','Interior Photos','Exterior Photos','Location Photos','Property Overview','Location & Proximity','Area Amenities','Demographics','Amenities Map','NWA Regional','Contact'];

// ─── STYLES ──────────────────────────────────────
const S={
  sidebar:{position:'fixed',top:0,left:0,width:240,height:'100vh',background:B.navy,color:B.white,display:'flex',flexDirection:'column',zIndex:100,transition:'transform .3s'},
  sidebarHidden:{transform:'translateX(-240px)'},
  logo:{padding:'24px 20px',borderBottom:\`1px solid \${B.dark}\`},
  logoTitle:{fontSize:15,fontWeight:700,color:B.gold,letterSpacing:.5},
  logoSub:{fontSize:11,color:B.gray400,marginTop:2,letterSpacing:1,textTransform:'uppercase'},
  nav:{flex:1,padding:'16px 0',overflowY:'auto'},
  navItem:(active)=>({display:'flex',alignItems:'center',gap:12,padding:'12px 20px',cursor:'pointer',fontSize:13,fontWeight:active?700:400,color:active?B.gold:B.gray200,background:active?'rgba(196,163,90,0.08)':'transparent',borderLeft:active?\`3px solid \${B.gold}\`:'3px solid transparent',transition:'all .2s'}),
  main:{marginLeft:240,minHeight:'100vh',padding:'24px 32px'},
  card:{background:B.white,borderRadius:12,padding:24,boxShadow:'0 1px 3px rgba(0,0,0,0.06)',border:\`1px solid \${B.gray200}\`},
  statCard:{background:B.white,borderRadius:12,padding:20,boxShadow:'0 1px 3px rgba(0,0,0,0.06)',border:\`1px solid \${B.gray200}\`,flex:'1 1 200px'},
  btn:(variant='primary')=>({display:'inline-flex',alignItems:'center',gap:8,padding:'10px 20px',borderRadius:8,border:'none',cursor:'pointer',fontSize:13,fontWeight:700,letterSpacing:.3,transition:'all .2s',
    ...(variant==='primary'?{background:B.crimson,color:B.white}:variant==='gold'?{background:B.gold,color:B.navy}:variant==='outline'?{background:'transparent',color:B.navy,border:\`1px solid \${B.gray200}\`}:{background:B.gray100,color:B.navy})
  }),
  input:{width:'100%',padding:'10px 14px',borderRadius:8,border:\`1px solid \${B.gray200}\`,fontSize:13,fontFamily:B.font,outline:'none',transition:'border .2s'},
  label:{display:'block',fontSize:11,fontWeight:700,letterSpacing:.5,color:B.gray600,marginBottom:6,textTransform:'uppercase'},
  sectionTitle:{fontSize:22,fontWeight:700,color:B.navy,marginBottom:4},
  sectionSub:{fontSize:13,color:B.gray600,marginBottom:24},
  badge:(color)=>({display:'inline-block',padding:'3px 10px',borderRadius:20,fontSize:10,fontWeight:700,letterSpacing:.5,textTransform:'uppercase',background:color==='green'?'#d1fae5':color==='blue'?'#dbeafe':color==='yellow'?'#fef3c7':color==='purple'?'#ede9fe':'#f3f4f6',color:color==='green'?'#059669':color==='blue'?'#1d4ed8':color==='yellow'?'#d97706':color==='purple'?'#7c3aed':'#6b7280'}),
  tab:(active)=>({padding:'10px 20px',cursor:'pointer',fontSize:13,fontWeight:active?700:400,color:active?B.crimson:B.gray600,borderBottom:active?\`2px solid \${B.crimson}\`:'2px solid transparent',transition:'all .2s'}),
};

const statusColor=(s)=>s==='Published'?'green':s==='OM Generated'?'blue':s==='Flyer Ready'?'yellow':s==='Email Sent'?'purple':'gray';
const fmt=(n)=>n>=1e6?'$'+(n/1e6).toFixed(2)+'M':'$'+n.toLocaleString();

// ─── PIPELINE STATUS ─────────────────────────────
const PipelineStatus=({listing})=>{
  const stages=[
    {label:'Config',done:listing.configValid!==false},
    {label:'Flip Book',done:listing.flipBookReady},
    {label:'Flyer',done:listing.flyerReady},
    {label:'Email',done:listing.emailTemplateReady},
    {label:'Embed',done:listing.embedReady},
    {label:'Audit',done:listing.auditPassed!==false}
  ];
  return <div style={{display:'flex',gap:8,marginTop:12,flexWrap:'wrap'}}>
    {stages.map((s,i)=><div key={i} style={{display:'flex',alignItems:'center',gap:6,padding:'6px 12px',borderRadius:20,fontSize:10,fontWeight:700,background:s.done?'#d1fae5':'#f3f4f6',color:s.done?'#059669':'#9ca3af',letterSpacing:.3}}>
      {s.done?'✓':'○'} {s.label}
    </div>)}
  </div>;
};

// ─── LISTING CARD ────────────────────────────────
const ListingCard=({listing,onClick})=><div onClick={onClick} style={{...S.card,cursor:'pointer',transition:'transform .2s,box-shadow .2s',overflow:'hidden',padding:0}} onMouseOver={e=>{e.currentTarget.style.transform='translateY(-2px)';e.currentTarget.style.boxShadow='0 8px 24px rgba(0,0,0,0.1)'}} onMouseOut={e=>{e.currentTarget.style.transform='';e.currentTarget.style.boxShadow=''}}>
  <div style={{height:160,background:\`linear-gradient(135deg,\${B.navy},\${B.dark})\`,display:'flex',alignItems:'center',justifyContent:'center',position:'relative'}}>
    <Icon name="image" size={40} color={B.gold}/>
    <div style={{position:'absolute',top:12,right:12}}><span style={S.badge(statusColor(listing.status))}>{listing.status}</span></div>
    <div style={{position:'absolute',bottom:12,left:12,background:'rgba(0,0,0,0.6)',color:B.white,padding:'4px 10px',borderRadius:6,fontSize:11,fontWeight:700}}>{listing.type}</div>
  </div>
  <div style={{padding:16}}>
    <div style={{fontSize:15,fontWeight:700,color:B.navy}}>{listing.street}</div>
    <div style={{fontSize:12,color:B.gray600,marginTop:2}}>{listing.city}, {listing.state} {listing.zip}</div>
    <div style={{fontSize:18,fontWeight:700,color:B.crimson,marginTop:8}}>{fmt(listing.price)}</div>
    {listing.beds>0&&<div style={{fontSize:11,color:B.gray400,marginTop:4}}>{listing.beds} BD | {listing.baths} BA | {listing.sqft.toLocaleString()} SF</div>}
    <PipelineStatus listing={listing}/>
  </div>
</div>;

// ─── VIEWS ───────────────────────────────────────

// Dashboard
const DashboardView=({listings,setView,setSelected})=>{
  const stats=[
    {label:'Total Listings',value:listings.length,icon:'home',color:B.crimson},
    {label:'OMs Generated',value:listings.filter(l=>l.flipBookReady).length,icon:'book',color:B.gold},
    {label:'Emails Sent',value:listings.filter(l=>l.emailTemplateReady).length,icon:'mail',color:'#059669'},
    {label:'Blog Posts',value:12,icon:'blog',color:'#6366f1'}
  ];
  return <div>
    <div style={S.sectionTitle}>Dashboard</div>
    <div style={S.sectionSub}>Mason Capital Group Marketing Overview</div>
    <div style={{display:'flex',gap:16,flexWrap:'wrap',marginBottom:32}}>
      {stats.map((s,i)=><div key={i} style={S.statCard}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
          <div>
            <div style={{fontSize:11,fontWeight:700,color:B.gray400,letterSpacing:.5,textTransform:'uppercase'}}>{s.label}</div>
            <div style={{fontSize:32,fontWeight:700,color:B.navy,marginTop:4}}>{s.value}</div>
          </div>
          <div style={{width:48,height:48,borderRadius:12,background:s.color+'15',display:'flex',alignItems:'center',justifyContent:'center'}}>
            <Icon name={s.icon} size={24} color={s.color}/>
          </div>
        </div>
      </div>)}
    </div>
    <div style={{display:'flex',gap:12,marginBottom:32}}>
      <button style={S.btn('primary')} onClick={()=>setView('new-listing')}>
        <Icon name="plus" size={16} color={B.white}/> New Listing
      </button>
      <button style={S.btn('gold')} onClick={()=>setView('om')}>
        <Icon name="book" size={16} color={B.navy}/> Generate OM
      </button>
      <button style={S.btn('outline')} onClick={()=>setView('email')}>
        <Icon name="mail" size={16} color={B.navy}/> Send Campaign
      </button>
    </div>
    <div style={S.sectionTitle}>Active Listings</div>
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(320px,1fr))',gap:20,marginTop:16}}>
      {listings.map(l=><ListingCard key={l.id} listing={l} onClick={()=>{setSelected(l);setView('listing-detail')}}/>)}
    </div>
  </div>;
};

// Listing Detail
const ListingDetailView=({listing,setView})=>{
  const [tab,setTab]=useState('overview');
  return <div>
    <button style={{...S.btn('outline'),marginBottom:20}} onClick={()=>setView('dashboard')}>← Back</button>
    <div style={{display:'flex',alignItems:'center',gap:16,marginBottom:8}}>
      <div style={S.sectionTitle}>{listing.street}</div>
      <span style={S.badge(statusColor(listing.status))}>{listing.status}</span>
    </div>
    <div style={S.sectionSub}>{listing.city}, {listing.state} {listing.zip} — {fmt(listing.price)}</div>
    <div style={{display:'flex',gap:0,borderBottom:\`1px solid \${B.gray200}\`,marginBottom:24}}>
      {['overview','flipbook','flyer','email','embed'].map(t=><div key={t} style={S.tab(tab===t)} onClick={()=>setTab(t)}>{t.charAt(0).toUpperCase()+t.slice(1)}</div>)}
    </div>
    <div style={S.card}>
      {tab==='overview'&&<div>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20}}>
          <div>
            <div style={S.label}>Property Details</div>
            <div style={{fontSize:14,lineHeight:1.8,color:B.gray800}}>
              <div><strong>MLS:</strong> {listing.mls}</div>
              <div><strong>Type:</strong> {listing.type}</div>
              {listing.beds>0&&<div><strong>Bedrooms:</strong> {listing.beds}</div>}
              <div><strong>Bathrooms:</strong> {listing.baths}</div>
              <div><strong>Square Feet:</strong> {listing.sqft.toLocaleString()}</div>
            </div>
          </div>
          <div>
            <div style={S.label}>Pipeline</div>
            <PipelineStatus listing={listing}/>
            <div style={{marginTop:20,display:'flex',gap:8,flexWrap:'wrap'}}>
              <button style={S.btn('primary')}><Icon name="book" size={14} color={B.white}/> Generate OM</button>
              <button style={S.btn('gold')}><Icon name="doc" size={14} color={B.navy}/> Create Flyer</button>
            </div>
          </div>
        </div>
      </div>}
      {tab==='flipbook'&&<div>
        <div style={S.label}>11-Page OM Flip Book</div>
        <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(180px,1fr))',gap:12,marginTop:12}}>
          {OM_PAGES.map((p,i)=><div key={i} style={{padding:16,background:B.cream,borderRadius:8,border:\`1px solid \${B.gray200}\`,textAlign:'center'}}>
            <div style={{width:40,height:40,borderRadius:8,background:B.navy,margin:'0 auto 8px',display:'flex',alignItems:'center',justifyContent:'center',color:B.gold,fontSize:14,fontWeight:700}}>{i+1}</div>
            <div style={{fontSize:12,fontWeight:600,color:B.navy}}>{p}</div>
          </div>)}
        </div>
        <div style={{marginTop:20,display:'flex',gap:8}}>
          <button style={S.btn('primary')}><Icon name="eye" size={14} color={B.white}/> Preview</button>
          <button style={S.btn('gold')}><Icon name="download" size={14} color={B.navy}/> Download HTML</button>
          <button style={S.btn('outline')}><Icon name="code" size={14}/> Embed Code</button>
        </div>
      </div>}
      {tab==='flyer'&&<div>
        <div style={S.label}>Flyer Templates</div>
        <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:16,marginTop:12}}>
          {['Print Flyer','Digital Flyer','Social Media'].map((t,i)=><div key={i} style={{padding:20,background:B.cream,borderRadius:8,border:\`1px solid \${B.gray200}\`,textAlign:'center',cursor:'pointer'}} onMouseOver={e=>e.currentTarget.style.borderColor=B.gold} onMouseOut={e=>e.currentTarget.style.borderColor=B.gray200}>
            <Icon name="doc" size={32} color={B.crimson}/>
            <div style={{fontSize:13,fontWeight:700,color:B.navy,marginTop:8}}>{t}</div>
          </div>)}
        </div>
      </div>}
      {tab==='email'&&<div>
        <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:16}}>
          <div style={S.label}>Email via IXACT CRM</div>
          <span style={{...S.badge('green'),fontSize:9}}>Connected</span>
        </div>
        <div style={{padding:16,background:B.cream,borderRadius:8,marginBottom:16}}>
          <div style={{fontSize:12,color:B.gray600}}>From: <strong>info@masoncapitalgroup.com</strong> via IXACT CRM</div>
        </div>
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
          {EMAIL_TEMPLATES.filter(t=>t.type==='announcement'||t.type==='newsletter').map(t=><div key={t.id} style={{padding:16,background:B.white,borderRadius:8,border:\`1px solid \${B.gray200}\`,cursor:'pointer'}}>
            <div style={{fontSize:13,fontWeight:700,color:B.navy}}>{t.name}</div>
            <div style={{fontSize:11,color:B.gray400,marginTop:4}}>{t.subject}</div>
          </div>)}
        </div>
      </div>}
      {tab==='embed'&&<EmbedView listing={listing}/>}
    </div>
  </div>;
};

// Embed Code View
const EmbedView=({listing})=>{
  const [copied,setCopied]=useState(false);
  const slug=listing.street.toLowerCase().replace(/\\s+/g,'-').replace(/[^a-z0-9-]/g,'');
  const embedCode=\`<!-- MCG Flip Book Embed: \${listing.street} -->
<div class="mcg-fb-wrapper" style="position:relative;width:100%;max-width:1424px;margin:0 auto">
  <iframe src="https://masoncapitalgroup.com/om/\${slug}/index.html"
    style="width:100%;height:0;padding-bottom:77.25%;border:none;border-radius:12px"
    allowfullscreen loading="lazy"
    sandbox="allow-scripts allow-same-origin allow-popups">
  </iframe>
</div>
<script>
(function(){var w=document.querySelector('.mcg-fb-wrapper');
if(w&&window.ResizeObserver){new ResizeObserver(function(){
w.style.maxWidth=Math.min(w.parentElement.offsetWidth,1424)+'px'
}).observe(w.parentElement)}})();
<\\/script>\`;
  const copy=()=>{navigator.clipboard.writeText(embedCode).then(()=>{setCopied(true);setTimeout(()=>setCopied(false),2000)})};
  return <div>
    <div style={S.label}>Responsive Embed Code</div>
    <div style={{position:'relative'}}>
      <pre style={{background:B.navy,color:'#a5f3fc',padding:20,borderRadius:8,fontSize:11,lineHeight:1.6,overflow:'auto',maxHeight:300,whiteSpace:'pre-wrap'}}>{embedCode}</pre>
      <button onClick={copy} style={{position:'absolute',top:12,right:12,...S.btn('gold'),padding:'6px 14px',fontSize:11}}>
        {copied?'Copied!':'Copy'}
      </button>
    </div>
  </div>;
};

// New Listing Wizard
const NewListingView=({onAdd,setView})=>{
  const [step,setStep]=useState(1);
  const [form,setForm]=useState({street:'',city:'Bentonville',state:'AR',zip:'72712',price:'',sqft:'',beds:'3',baths:'2',type:'Residential',mls:'',yearBuilt:'',description:'',features:'',url:''});
  const upd=(k,v)=>setForm({...form,[k]:v});
  const Field=({label,field,type='text',placeholder='',wide})=><div style={{gridColumn:wide?'1/-1':undefined}}>
    <label style={S.label}>{label}</label>
    {wide?<textarea value={form[field]} onChange={e=>upd(field,e.target.value)} placeholder={placeholder} rows={4} style={{...S.input,resize:'vertical'}}/>
    :<input type={type} value={form[field]} onChange={e=>upd(field,e.target.value)} placeholder={placeholder} style={S.input} onFocus={e=>e.target.style.borderColor=B.gold} onBlur={e=>e.target.style.borderColor=B.gray200}/>}
  </div>;
  const submit=()=>{
    const newListing={id:Date.now(),street:form.street,city:form.city,state:form.state,zip:form.zip,price:Number(form.price)||0,beds:Number(form.beds),baths:Number(form.baths),sqft:Number(form.sqft)||0,type:form.type,status:'Draft',mls:form.mls,flipBookReady:false,flyerReady:false,emailTemplateReady:false,embedReady:false,configValid:true,auditPassed:false,url:form.url};
    onAdd(newListing);setView('dashboard');
  };
  return <div>
    <button style={{...S.btn('outline'),marginBottom:20}} onClick={()=>setView('dashboard')}>← Back</button>
    <div style={S.sectionTitle}>New Listing</div>
    <div style={S.sectionSub}>Step {step} of 4</div>
    <div style={{display:'flex',gap:4,marginBottom:24}}>
      {[1,2,3,4].map(s=><div key={s} style={{flex:1,height:4,borderRadius:2,background:s<=step?B.gold:B.gray200,transition:'background .3s'}}/>)}
    </div>
    <div style={S.card}>
      {step===1&&<div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
        <Field label="Street Address" field="street" placeholder="407 Old Forge Dr" wide/>
        <Field label="City" field="city"/>
        <Field label="State" field="state"/>
        <Field label="ZIP" field="zip"/>
        <Field label="MLS Number" field="mls" placeholder="MLS-240871"/>
        <Field label="Property Type" field="type"/>
        <Field label="Public IDX URL" field="url" placeholder="https://masoncapitalgroup.com/properties/..." wide/>
      </div>}
      {step===2&&<div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
        <Field label="Price ($)" field="price" type="number" placeholder="549900"/>
        <Field label="Square Feet" field="sqft" type="number" placeholder="2847"/>
        <Field label="Bedrooms" field="beds" type="number"/>
        <Field label="Bathrooms" field="baths" type="number"/>
        <Field label="Year Built" field="yearBuilt" type="number" placeholder="2005"/>
      </div>}
      {step===3&&<div style={{display:'grid',gridTemplateColumns:'1fr',gap:16}}>
        <Field label="Property Description" field="description" placeholder="Describe the property..." wide/>
        <Field label="Key Features" field="features" placeholder="List key features..." wide/>
      </div>}
      {step===4&&<div>
        <div style={S.label}>Review</div>
        <div style={{background:B.cream,borderRadius:8,padding:20,marginTop:8}}>
          <div style={{fontSize:16,fontWeight:700,color:B.navy}}>{form.street||'—'}</div>
          <div style={{fontSize:13,color:B.gray600}}>{form.city}, {form.state} {form.zip}</div>
          <div style={{fontSize:20,fontWeight:700,color:B.crimson,marginTop:8}}>{form.price?fmt(Number(form.price)):'—'}</div>
          <div style={{fontSize:12,color:B.gray400,marginTop:4}}>{form.beds} BD | {form.baths} BA | {Number(form.sqft).toLocaleString()} SF | {form.type}</div>
          {form.url&&<div style={{fontSize:11,color:B.gold,marginTop:8}}>IDX URL: {form.url}</div>}
        </div>
      </div>}
      <div style={{display:'flex',justifyContent:'space-between',marginTop:24}}>
        {step>1?<button style={S.btn('outline')} onClick={()=>setStep(step-1)}>← Previous</button>:<div/>}
        {step<4?<button style={S.btn('primary')} onClick={()=>setStep(step+1)}>Next →</button>
        :<button style={S.btn('primary')} onClick={submit}><Icon name="check" size={14} color={B.white}/> Create Listing</button>}
      </div>
    </div>
  </div>;
};

// OM Generator View
const STEPS=[
  {id:'scrape',  label:'Scraping listing data'},
  {id:'research',label:'Running market research'},
  {id:'listing', label:'Generating listing page'},
  {id:'flipbook',label:'Building flip book (11 pages)'},
  {id:'print',   label:'Generating PDF & 1-page flyer'},
  {id:'done',    label:'Finalizing & pushing to IXACT'},
];

const OMView=({listings,settings})=>{
  const backendUrl=(settings.backendUrl||'').replace(/\\/$/,'');
  const ixactKey=settings.ixactKey||'';

  const [mode,setMode]=useState('url'); // 'url' | 'listing'
  const [inputUrl,setInputUrl]=useState('');
  const [selId,setSelId]=useState(listings[0]?.id);
  const [phase,setPhase]=useState('idle'); // idle | running | done | error
  const [step,setStep]=useState('');
  const [log,setLog]=useState('');
  const [showLog,setShowLog]=useState(false);
  const [result,setResult]=useState(null);
  const [ixactStatus,setIxactStatus]=useState(null); // null | 'pushing' | 'ok' | 'error'
  const [copyState,setCopyState]=useState({});
  const pollRef=useRef(null);

  const listing=listings.find(l=>l.id===selId);

  const copyText=(key,text)=>{
    navigator.clipboard.writeText(text).then(()=>{
      setCopyState(s=>({...s,[key]:true}));
      setTimeout(()=>setCopyState(s=>({...s,[key]:false})),1800);
    }).catch(()=>prompt('Copy:',text));
  };

  const pushToIxact=async(slug,omUrl,address)=>{
    if(!ixactKey||!backendUrl) return;
    setIxactStatus('pushing');
    try{
      const r=await fetch(\`\${backendUrl}/api/push-ixact\`,{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          ixactKey,
          slug,
          subject:\`New MCG Listing: \${address}\`,
          fromEmail:settings.emailFrom||'info@masoncapitalgroup.com',
          fromName:'Cameron Torabi, Mason Capital Group',
        }),
      });
      const d=await r.json();
      setIxactStatus(d.success?'ok':'error');
    }catch(e){
      setIxactStatus('error');
    }
  };

  const startGeneration=async()=>{
    if(!backendUrl){
      alert('No backend URL configured. Go to Settings → Backend URL.');
      return;
    }
    const url=mode==='url'?inputUrl:(listing?.url||'');
    if(!url){alert('Enter a listing URL first.');return;}

    setPhase('running');
    setStep('scrape');
    setLog('');
    setResult(null);
    setIxactStatus(null);

    try{
      const _tok=getToken();const r=await fetch(\`\${backendUrl}/api/generate\`,{
        method:'POST',
        headers:{'Content-Type':'application/json',...(_tok?{'Authorization':'Bearer '+_tok}:{})},
        body:JSON.stringify({url}),
      });
      const d=await r.json();
      if(d.error){setPhase('error');setLog(d.error);return;}

      // Poll for status
      pollRef.current=setInterval(async()=>{
        try{
          const sr=await fetch(\`\${backendUrl}/api/status/\${d.jobId}\`);
          const sd=await sr.json();
          if(sd.log) setLog(sd.log);
          if(sd.step) setStep(sd.step);
          if(sd.status==='done'){
            clearInterval(pollRef.current);
            setPhase('done');
            const base=backendUrl;
            const omUrl=\`\${base}/output/\${sd.slug}/flipbook.html\`;
            const listingUrl=\`\${base}/output/\${sd.slug}/listing-page.html\`;
            const emailUrl=\`\${base}/output/\${sd.slug}/email-campaign.html\`;
            const flyerUrl=\`\${base}/output/\${sd.slug}/flyer.html\`;
            const flyerPdf=\`\${base}/output/\${sd.slug}/flyer.pdf\`;
            const omPdf=\`\${base}/output/\${sd.slug}/om.pdf\`;
            const embedCode=\`<iframe src="\${omUrl}" style="width:100%;height:720px;border:none" loading="lazy" title="Offering Memorandum"><\\/iframe>\`;
            const files=sd.files||[];
            setResult({slug:sd.slug,omUrl,listingUrl,emailUrl,flyerUrl,flyerPdf,omPdf,embedCode,files});
            pushToIxact(sd.slug,omUrl,url);
          }else if(sd.status==='error'){
            clearInterval(pollRef.current);
            setPhase('error');
            setLog(sd.error||'Generation failed');
          }
        }catch{}
      },900);
    }catch(e){
      setPhase('error');
      setLog(e.message);
    }
  };

  const reset=()=>{
    if(pollRef.current) clearInterval(pollRef.current);
    setPhase('idle');setStep('');setLog('');setResult(null);setIxactStatus(null);setInputUrl('');
  };

  const stepIdx=STEPS.findIndex(s=>s.id===step);
  const pct=phase==='done'?100:phase==='running'?Math.max(10,Math.round((stepIdx/STEPS.length)*100)):0;

  return <div>
    <div style={S.sectionTitle}>OM Generator</div>
    <div style={S.sectionSub}>Paste a listing URL → listing page + interactive flip book generated simultaneously + pushed to IXACT</div>

    {!backendUrl&&<div style={{padding:'10px 16px',background:'#fef3c7',border:'1px solid #f59e0b',borderRadius:8,marginBottom:20,fontSize:12,color:'#92400e'}}>
      ⚠ Backend URL not configured. <span style={{cursor:'pointer',textDecoration:'underline'}} onClick={()=>{}}>Go to Settings → Backend URL</span> to connect the Railway server.
    </div>}

    <div style={{display:'flex',gap:20}}>
      {/* Left: Input Panel */}
      <div style={{width:320,flexShrink:0}}>
        <div style={S.card}>
          {/* Mode toggle */}
          <div style={{display:'flex',gap:0,marginBottom:16,borderRadius:6,overflow:'hidden',border:\`1px solid \${B.gray200}\`}}>
            {[{id:'url',label:'Paste URL'},{id:'listing',label:'From Listings'}].map(m=>
              <div key={m.id} onClick={()=>setMode(m.id)} style={{flex:1,padding:'8px 0',textAlign:'center',fontSize:11,fontWeight:700,cursor:'pointer',background:mode===m.id?B.navy:B.white,color:mode===m.id?B.gold:B.gray600,transition:'all .15s'}}>
                {m.label}
              </div>
            )}
          </div>

          {mode==='url'
            ?<div>
              <label style={S.label}>Listing URL</label>
              <input
                value={inputUrl}
                onChange={e=>setInputUrl(e.target.value)}
                placeholder="https://masoncapitalgroup.com/properties/..."
                style={{...S.input,marginBottom:4,fontSize:12}}
                onFocus={e=>e.target.style.borderColor=B.gold}
                onBlur={e=>e.target.style.borderColor=B.gray200}
                disabled={phase==='running'}
              />
              <div style={{fontSize:10,color:B.gray400,marginBottom:16}}>MLS Matrix, IDX, or MCG listing page</div>
            </div>
            :<div>
              <label style={S.label}>Select Listing</label>
              {listings.map(l=><div key={l.id} onClick={()=>setSelId(l.id)} style={{padding:10,borderRadius:6,marginBottom:6,cursor:'pointer',background:selId===l.id?B.cream:B.white,border:\`1px solid \${selId===l.id?B.gold:B.gray200}\`,transition:'all .15s'}}>
                <div style={{fontSize:12,fontWeight:700,color:B.navy}}>{l.street}</div>
                <div style={{fontSize:10,color:B.gray400}}>{l.city}, {l.state} · {fmt(l.price)}</div>
              </div>)}
            </div>
          }

          {/* Generate button */}
          {phase==='idle'&&<button style={{...S.btn('primary'),width:'100%'}} onClick={startGeneration}>
            <Icon name="star" size={14} color={B.white}/> Generate OM Package
          </button>}
          {phase==='running'&&<button style={{...S.btn('primary'),width:'100%',opacity:.6}} disabled>Generating…</button>}
          {(phase==='done'||phase==='error')&&<button style={{...S.btn('outline'),width:'100%'}} onClick={reset}>Generate Another</button>}

          {/* OM page list (reference) */}
          <div style={{marginTop:20,paddingTop:16,borderTop:\`1px solid \${B.gray200}\`}}>
            <div style={{fontSize:10,fontWeight:700,letterSpacing:1,textTransform:'uppercase',color:B.gray600,marginBottom:8}}>11 Pages Generated</div>
            {OM_PAGES.map((p,i)=><div key={i} style={{display:'flex',alignItems:'center',gap:8,padding:'4px 0',borderBottom:\`1px solid \${B.gray200}\`}}>
              <span style={{width:18,height:18,borderRadius:4,background:B.navy,color:B.gold,fontSize:9,fontWeight:700,display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0}}>{i+1}</span>
              <span style={{fontSize:11,color:B.navy}}>{p}</span>
            </div>)}
          </div>
        </div>
      </div>

      {/* Right: Progress + Results */}
      <div style={{flex:1}}>
        {/* Progress */}
        {phase==='running'&&<div style={S.card}>
          <div style={{fontSize:13,fontWeight:700,color:B.navy,marginBottom:12}}>Generating…</div>
          <div style={{height:6,background:B.gray200,borderRadius:3,marginBottom:16,overflow:'hidden'}}>
            <div style={{height:'100%',background:\`linear-gradient(90deg,\${B.crimson},\${B.gold})\`,width:\`\${pct}%\`,transition:'width .5s',borderRadius:3}}/>
          </div>
          {STEPS.map((s,i)=>{
            const done=i<stepIdx;
            const active=s.id===step;
            const err=phase==='error'&&active;
            return <div key={s.id} style={{display:'flex',alignItems:'center',gap:10,padding:'8px 0',borderBottom:\`1px solid \${B.gray200}\`,opacity:i>stepIdx?.4:1}}>
              <div style={{width:22,height:22,borderRadius:'50%',background:err?'#ef4444':done?'#10b981':active?B.navy:B.gray200,color:B.white,display:'flex',alignItems:'center',justifyContent:'center',fontSize:10,fontWeight:700,flexShrink:0}}>
                {done?'✓':active?<span style={{display:'inline-block',width:10,height:10,border:'2px solid rgba(255,255,255,.4)',borderTopColor:B.white,borderRadius:'50%',animation:'spin 0.8s linear infinite'}}/>:i+1}
              </div>
              <span style={{fontSize:12,color:active?B.navy:done?'#10b981':B.gray400,fontWeight:active?700:400}}>{s.label}</span>
            </div>;
          })}
          <div style={{marginTop:12,fontSize:11,color:B.gray400,cursor:'pointer'}} onClick={()=>setShowLog(!showLog)}>
            {showLog?'Hide':'Show'} console output
          </div>
          {showLog&&<pre style={{marginTop:8,padding:10,background:B.navy,color:'rgba(255,255,255,.7)',borderRadius:6,fontSize:10,maxHeight:140,overflow:'auto',whiteSpace:'pre-wrap',wordBreak:'break-all'}}>{log}</pre>}
        </div>}

        {/* Error */}
        {phase==='error'&&<div style={{...S.card,border:\`1px solid #ef4444\`}}>
          <div style={{fontSize:13,fontWeight:700,color:'#ef4444',marginBottom:8}}>Generation Failed</div>
          <pre style={{fontSize:11,color:B.gray600,whiteSpace:'pre-wrap',wordBreak:'break-all'}}>{log}</pre>
        </div>}

        {/* Results */}
        {phase==='done'&&result&&<div>
          {/* Listing Page */}
          <div style={{...S.card,marginBottom:12,borderLeft:\`3px solid \${B.crimson}\`}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:8}}>
              <div>
                <div style={{fontSize:12,fontWeight:700,color:B.navy}}>Listing Page</div>
                <div style={{fontSize:10,color:B.gray400,marginTop:2}}>Full property page with OM embedded inline</div>
              </div>
              <span style={{background:'#fce7f3',color:B.crimson,fontSize:9,fontWeight:700,padding:'3px 8px',borderRadius:20,textTransform:'uppercase',letterSpacing:1}}>OM Embedded</span>
            </div>
            <div style={{display:'flex',gap:8}}>
              <a href={result.listingUrl} target="_blank" style={{...S.btn('primary'),padding:'6px 14px',fontSize:11,textDecoration:'none'}}><Icon name="eye" size={12} color={B.white}/> Preview</a>
              <button style={{...S.btn('outline'),padding:'6px 14px',fontSize:11}} onClick={()=>copyText('listing',result.listingUrl)}>{copyState.listing?'Copied!':'Copy URL'}</button>
            </div>
          </div>

          {/* Standalone OM */}
          <div style={{...S.card,marginBottom:12,borderLeft:\`3px solid \${B.gold}\`}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:6}}>
              <div>
                <div style={{fontSize:12,fontWeight:700,color:B.navy}}>Standalone OM Flip Book</div>
                <div style={{fontSize:10,color:B.gray400,marginTop:2}}>Direct link for email campaigns, text, social</div>
              </div>
              <span style={{background:'#fef9ec',color:B.gold,fontSize:9,fontWeight:700,padding:'3px 8px',borderRadius:20,textTransform:'uppercase',letterSpacing:1,border:\`1px solid \${B.gold}\`}}>Email Marketing</span>
            </div>
            <div style={{fontSize:10,fontFamily:'monospace',color:B.gray600,padding:'6px 10px',background:B.cream,borderRadius:4,marginBottom:10,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{result.omUrl}</div>
            <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
              <a href={result.omUrl} target="_blank" style={{...S.btn('gold'),padding:'6px 14px',fontSize:11,textDecoration:'none'}}><Icon name="eye" size={12} color={B.navy}/> Preview</a>
              <button style={{...S.btn('outline'),padding:'6px 14px',fontSize:11}} onClick={()=>copyText('om',result.omUrl)}>{copyState.om?'Copied!':'Copy URL'}</button>
              <button style={{...S.btn('outline'),padding:'6px 14px',fontSize:11}} onClick={()=>copyText('embed',result.embedCode)}>{copyState.embed?'Copied!':'Copy Embed Code'}</button>
            </div>
          </div>

          {/* Print OM PDF */}
          <div style={{...S.card,marginBottom:12,borderLeft:\`3px solid #6366f1\`}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:8}}>
              <div>
                <div style={{fontSize:12,fontWeight:700,color:B.navy}}>Print-Ready OM</div>
                <div style={{fontSize:10,color:B.gray400,marginTop:2}}>Full 11-page PDF — send to investors or print</div>
              </div>
              <span style={{background:'#eef2ff',color:'#6366f1',fontSize:9,fontWeight:700,padding:'3px 8px',borderRadius:20,textTransform:'uppercase',letterSpacing:1}}>PDF · 11 pages</span>
            </div>
            <div style={{display:'flex',gap:8}}>
              {result.files&&result.files.includes('om.pdf')
                ?<><a href={result.omPdf} target="_blank" style={{...S.btn('outline'),padding:'6px 14px',fontSize:11,textDecoration:'none'}}><Icon name="eye" size={12}/> Open PDF</a>
                   <a href={result.omPdf} download style={{...S.btn('outline'),padding:'6px 14px',fontSize:11,textDecoration:'none'}}>↓ Download</a></>
                :<span style={{fontSize:11,color:B.gray400}}>PDF not generated (check server logs)</span>}
            </div>
          </div>

          {/* 1-Page Flyer */}
          <div style={{...S.card,marginBottom:12,borderLeft:\`3px solid #0891b2\`}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:8}}>
              <div>
                <div style={{fontSize:12,fontWeight:700,color:B.navy}}>1-Page Flyer</div>
                <div style={{fontSize:10,color:B.gray400,marginTop:2}}>Single-page marketing flyer for print or email</div>
              </div>
              <span style={{background:'#ecfeff',color:'#0891b2',fontSize:9,fontWeight:700,padding:'3px 8px',borderRadius:20,textTransform:'uppercase',letterSpacing:1}}>Print / Email</span>
            </div>
            <div style={{display:'flex',gap:8}}>
              {result.files&&result.files.includes('flyer.html')
                ?<><a href={result.flyerUrl} target="_blank" style={{...S.btn('outline'),padding:'6px 14px',fontSize:11,textDecoration:'none'}}><Icon name="eye" size={12}/> Preview</a>
                   {result.files.includes('flyer.pdf')&&<a href={result.flyerPdf} download style={{...S.btn('outline'),padding:'6px 14px',fontSize:11,textDecoration:'none'}}>↓ PDF</a>}</>
                :<span style={{fontSize:11,color:B.gray400}}>Flyer not generated</span>}
            </div>
          </div>

          {/* IXACT Status */}
          <div style={{...S.card,marginBottom:12,borderLeft:\`3px solid \${ixactStatus==='ok'?'#10b981':ixactStatus==='error'?'#ef4444':B.navy}\`}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
              <div>
                <div style={{fontSize:12,fontWeight:700,color:B.navy}}>IXACT Marketing Resource</div>
                <div style={{fontSize:10,color:B.gray400,marginTop:2}}>Email campaign template auto-pushed to CRM</div>
              </div>
              {ixactStatus==='pushing'&&<span style={{fontSize:10,color:B.gray400}}>Pushing…</span>}
              {ixactStatus==='ok'&&<span style={{background:'#d1fae5',color:'#059669',fontSize:9,fontWeight:700,padding:'3px 8px',borderRadius:20,textTransform:'uppercase'}}>✓ Saved to IXACT</span>}
              {ixactStatus==='error'&&<span style={{background:'#fee2e2',color:'#dc2626',fontSize:9,fontWeight:700,padding:'3px 8px',borderRadius:20,textTransform:'uppercase'}}>Push Failed</span>}
              {!ixactStatus&&<span style={{fontSize:10,color:B.gray400}}>Queued</span>}
            </div>
            <div style={{display:'flex',gap:8}}>
              <a href={result.emailUrl} target="_blank" style={{...S.btn('outline'),padding:'6px 14px',fontSize:11,textDecoration:'none'}}><Icon name="eye" size={12}/> Preview Email</a>
              {ixactStatus==='error'&&<button style={{...S.btn('outline'),padding:'6px 14px',fontSize:11}} onClick={()=>pushToIxact(result.slug,result.omUrl,'listing')}>Retry Push</button>}
            </div>
          </div>
        </div>}

        {/* Idle state */}
        {phase==='idle'&&<div style={{...S.card,textAlign:'center',padding:60,color:B.gray400}}>
          <Icon name="book" size={48} color={B.gray200}/>
          <div style={{marginTop:12,fontSize:14}}>Enter a listing URL and click Generate</div>
          <div style={{fontSize:11,marginTop:4}}>Listing page + 11-page OM + IXACT email created simultaneously</div>
        </div>}
      </div>
    </div>
  </div>;
};

// Flyer Generator View
const FlyerView=({listings})=>{
  const [sel,setSel]=useState(listings[0]?.id);
  const [template,setTemplate]=useState('print');
  const listing=listings.find(l=>l.id===sel);
  const templates=[{id:'print',name:'Print Flyer',desc:'8.5x11 print-ready'},{id:'digital',name:'Digital Flyer',desc:'Web-optimized'},{id:'social',name:'Social Media',desc:'1080x1080 square'}];
  return <div>
    <div style={S.sectionTitle}>Flyer Generator</div>
    <div style={S.sectionSub}>Create branded property flyers</div>
    <div style={{display:'flex',gap:20}}>
      <div style={{width:240}}>
        <div style={S.label}>Select Listing</div>
        {listings.map(l=><div key={l.id} onClick={()=>setSel(l.id)} style={{padding:12,borderRadius:8,marginBottom:8,cursor:'pointer',background:sel===l.id?B.cream:B.white,border:\`1px solid \${sel===l.id?B.gold:B.gray200}\`}}>
          <div style={{fontSize:12,fontWeight:700,color:B.navy}}>{l.street}</div>
        </div>)}
        <div style={{marginTop:20}}>
          <div style={S.label}>Template</div>
          {templates.map(t=><div key={t.id} onClick={()=>setTemplate(t.id)} style={{padding:12,borderRadius:8,marginBottom:8,cursor:'pointer',background:template===t.id?B.cream:B.white,border:\`1px solid \${template===t.id?B.gold:B.gray200}\`}}>
            <div style={{fontSize:12,fontWeight:700,color:B.navy}}>{t.name}</div>
            <div style={{fontSize:10,color:B.gray400}}>{t.desc}</div>
          </div>)}
        </div>
      </div>
      <div style={{flex:1,...S.card}}>
        {listing&&<div>
          <div style={{background:\`linear-gradient(135deg,\${B.navy},\${B.dark})\`,borderRadius:12,padding:40,textAlign:'center',marginBottom:20,position:'relative'}}>
            <div style={{position:'absolute',top:16,left:16,background:B.crimson,color:B.white,padding:'4px 12px',borderRadius:4,fontSize:10,fontWeight:700}}>FOR SALE</div>
            <div style={{color:B.gold,fontSize:11,fontWeight:700,letterSpacing:2,marginBottom:8}}>MASON CAPITAL GROUP</div>
            <div style={{color:B.white,fontSize:24,fontWeight:700}}>{listing.street}</div>
            <div style={{color:B.gray400,fontSize:13,marginTop:4}}>{listing.city}, {listing.state} {listing.zip}</div>
            <div style={{color:B.gold,fontSize:28,fontWeight:700,marginTop:16}}>{fmt(listing.price)}</div>
            {listing.beds>0&&<div style={{color:B.gray200,fontSize:12,marginTop:8}}>{listing.beds} BD | {listing.baths} BA | {listing.sqft.toLocaleString()} SF</div>}
            <div style={{borderTop:\`1px solid \${B.dark}\`,marginTop:20,paddingTop:12,color:B.gray400,fontSize:10}}>Cameron Torabi, Broker | 479-925-3333 | info@masoncapitalgroup.com</div>
          </div>
          <div style={{display:'flex',gap:8}}>
            <button style={S.btn('primary')}><Icon name="download" size={14} color={B.white}/> Download HTML</button>
            <button style={S.btn('outline')}><Icon name="eye" size={14}/> Preview</button>
          </div>
        </div>}
      </div>
    </div>
  </div>;
};

// Email Campaign View
const EmailView=({listings})=>{
  const [selTemplate,setSelTemplate]=useState(null);
  const [subject,setSubject]=useState('');
  const [body,setBody]=useState('');
  const [recipients,setRecipients]=useState('');
  const [sent,setSent]=useState(false);
  const selectTpl=(t)=>{setSelTemplate(t);setSubject(t.subject);setBody(\`Dear [First Name],\\n\\nThank you for your interest in Northwest Arkansas real estate.\\n\\n[Email content here]\\n\\nBest regards,\\nCameron Torabi\\nMason Capital Group\\n479-925-3333\`);setSent(false)};
  return <div>
    <div style={S.sectionTitle}>Email Campaigns</div>
    <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4}}>
      <div style={S.sectionSub}>Powered by IXACT CRM</div>
      <span style={{...S.badge('green'),fontSize:9,marginBottom:24}}>API Connected</span>
    </div>
    <div style={{display:'flex',gap:20}}>
      <div style={{width:260}}>
        <div style={S.label}>Email Templates</div>
        {EMAIL_TEMPLATES.map(t=><div key={t.id} onClick={()=>selectTpl(t)} style={{padding:14,borderRadius:8,marginBottom:8,cursor:'pointer',background:selTemplate?.id===t.id?B.cream:B.white,border:\`1px solid \${selTemplate?.id===t.id?B.gold:B.gray200}\`,transition:'all .2s'}}>
          <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
            <div style={{fontSize:12,fontWeight:700,color:B.navy}}>{t.name}</div>
            <span style={{...S.badge(t.type==='drip'?'purple':'blue'),fontSize:8}}>{t.type}</span>
          </div>
          <div style={{fontSize:10,color:B.gray400,marginTop:4}}>{t.subject}</div>
          {t.steps>1&&<div style={{fontSize:9,color:B.gold,marginTop:4}}>{t.steps} steps in sequence</div>}
        </div>)}
      </div>
      <div style={{flex:1,...S.card}}>
        {selTemplate?<div>
          <div style={{padding:12,background:B.cream,borderRadius:8,marginBottom:16,display:'flex',justifyContent:'space-between',alignItems:'center'}}>
            <div style={{fontSize:12,color:B.gray600}}>From: <strong>info@masoncapitalgroup.com</strong></div>
            <div style={{fontSize:10,color:B.gold}}>via IXACT CRM</div>
          </div>
          <div style={{marginBottom:16}}>
            <label style={S.label}>Subject</label>
            <input value={subject} onChange={e=>setSubject(e.target.value)} style={S.input} onFocus={e=>e.target.style.borderColor=B.gold} onBlur={e=>e.target.style.borderColor=B.gray200}/>
          </div>
          <div style={{marginBottom:16}}>
            <label style={S.label}>Recipients</label>
            <input value={recipients} onChange={e=>setRecipients(e.target.value)} placeholder="Enter email addresses or select from IXACT contacts..." style={S.input} onFocus={e=>e.target.style.borderColor=B.gold} onBlur={e=>e.target.style.borderColor=B.gray200}/>
          </div>
          <div style={{marginBottom:16}}>
            <label style={S.label}>Body</label>
            <textarea value={body} onChange={e=>setBody(e.target.value)} rows={10} style={{...S.input,resize:'vertical',fontFamily:'monospace',fontSize:12,lineHeight:1.6}}/>
          </div>
          {sent?<div style={{padding:16,background:'#d1fae5',borderRadius:8,color:'#059669',fontWeight:700,textAlign:'center'}}>Campaign queued via IXACT CRM</div>
          :<div style={{display:'flex',gap:8}}>
            <button style={S.btn('primary')} onClick={()=>setSent(true)}><Icon name="send" size={14} color={B.white}/> Send Campaign</button>
            <button style={S.btn('gold')}><Icon name="clock" size={14} color={B.navy}/> Schedule</button>
            <button style={S.btn('outline')}><Icon name="eye" size={14}/> Preview</button>
          </div>}
        </div>:<div style={{textAlign:'center',padding:60,color:B.gray400}}>
          <Icon name="mail" size={48} color={B.gray200}/>
          <div style={{marginTop:12,fontSize:14}}>Select a template to compose</div>
        </div>}
      </div>
    </div>
  </div>;
};

// Blog Generator View
const BlogView=()=>{
  const [locations,setLocations]=useState('');
  const [posts,setPosts]=useState([]);
  const [preview,setPreview]=useState(null);
  const generate=()=>{
    const locs=locations.split('\\n').filter(l=>l.trim());
    const newPosts=locs.map((loc,i)=>{
      const slug=loc.trim().toLowerCase().replace(/\\s+/g,'-').replace(/[^a-z0-9-]/g,'');
      return {id:Date.now()+i,title:loc.trim(),slug,url:\`masoncapitalgroup.com/blog/\${slug}\`,excerpt:\`Discover what makes \${loc.trim()} a top destination in Northwest Arkansas...\`,status:'draft'};
    });
    setPosts([...posts,...newPosts]);
    setLocations('');
  };
  return <div>
    <div style={S.sectionTitle}>Blog Auto-Generator</div>
    <div style={S.sectionSub}>Create Webflow CMS blog posts for OM proximity and amenity links</div>
    <div style={{display:'flex',gap:20}}>
      <div style={{width:320,...S.card}}>
        <div style={S.label}>Batch Generate</div>
        <div style={{fontSize:11,color:B.gray400,marginBottom:8}}>Enter location names (one per line)</div>
        <textarea value={locations} onChange={e=>setLocations(e.target.value)} rows={8} placeholder={"Crystal Bridges Museum\\nThe Record\\n8th Street Market\\nPea Ridge National Park\\nWar Eagle Cavern"} style={{...S.input,resize:'vertical',fontSize:12}}/>
        <div style={{marginTop:12,display:'flex',gap:8}}>
          <button style={S.btn('primary')} onClick={generate}><Icon name="blog" size={14} color={B.white}/> Generate Posts</button>
        </div>
        <div style={{marginTop:16,padding:12,background:B.cream,borderRadius:8}}>
          <div style={{fontSize:10,color:B.gray600}}>
            <div><strong>CMS Collection:</strong> 69ab56...4e7b</div>
            <div><strong>Category:</strong> Community News</div>
            <div><strong>URL Pattern:</strong> /blog/&#123;slug&#125;</div>
          </div>
        </div>
      </div>
      <div style={{flex:1}}>
        {posts.length>0?<div>
          <div style={S.label}>{posts.length} Blog Posts</div>
          <div style={{display:'grid',gap:8}}>
            {posts.map(p=><div key={p.id} onClick={()=>setPreview(p)} style={{...S.card,padding:14,cursor:'pointer',border:\`1px solid \${preview?.id===p.id?B.gold:B.gray200}\`}}>
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                <div>
                  <div style={{fontSize:13,fontWeight:700,color:B.navy}}>{p.title}</div>
                  <div style={{fontSize:10,color:B.gold,marginTop:2}}>/{p.slug}</div>
                </div>
                <div style={{display:'flex',gap:6,alignItems:'center'}}>
                  <span style={S.badge('yellow')}>Draft</span>
                  <button style={{...S.btn('outline'),padding:'4px 10px',fontSize:10}} onClick={e=>{e.stopPropagation()}}>Push to CMS</button>
                </div>
              </div>
            </div>)}
          </div>
        </div>:<div style={{...S.card,textAlign:'center',padding:60,color:B.gray400}}>
          <Icon name="blog" size={48} color={B.gray200}/>
          <div style={{marginTop:12,fontSize:14}}>Generate blog posts from locations</div>
          <div style={{fontSize:11,marginTop:4}}>These posts will be linked from OM proximity and amenity cards</div>
        </div>}
      </div>
    </div>
  </div>;
};

const SETTINGS_KEY='mcg_hub_settings';
const DEFAULT_SETTINGS={
  backendUrl:'https://mcg-dashboard-production.up.railway.app',
  ixactKey:'Lf2Yr-xYyXErVJ@zpYoYfQ',
  webflowSiteId:'699cb0b733f309dd4bda1b56',
  blogCollectionId:'69ab56787f5bcd0a8faf4e7b',
  emailFrom:'info@masoncapitalgroup.com',
};
const loadSettings=()=>{try{return {...DEFAULT_SETTINGS,...JSON.parse(localStorage.getItem(SETTINGS_KEY)||'{}')};}catch{return DEFAULT_SETTINGS;}};
const saveSettings=(s)=>{try{localStorage.setItem(SETTINGS_KEY,JSON.stringify(s));}catch{}};

const SettingsView=({settings,setSettings})=>{
  const [local,setLocal]=useState(settings);
  const [saved,setSaved]=useState(false);
  const upd=(k,v)=>setLocal(s=>({...s,[k]:v}));
  const save=()=>{
    setSettings(local);
    saveSettings(local);
    setSaved(true);
    setTimeout(()=>setSaved(false),2000);
  };
  const Field=({label,field,type='text',hint})=><div style={{marginBottom:20}}>
    <label style={S.label}>{label}</label>
    <input type={type} value={local[field]||''} onChange={e=>upd(field,e.target.value)} style={S.input} onFocus={e=>e.target.style.borderColor=B.gold} onBlur={e=>e.target.style.borderColor=B.gray200}/>
    {hint&&<div style={{fontSize:10,color:B.gray400,marginTop:4}}>{hint}</div>}
  </div>;
  return <div>
    <div style={S.sectionTitle}>Settings</div>
    <div style={S.sectionSub}>Configure the Railway backend and API integrations — saved to your browser</div>
    <div style={{maxWidth:600,...S.card}}>
      <div style={{fontSize:14,fontWeight:700,color:B.navy,marginBottom:16,paddingBottom:12,borderBottom:\`1px solid \${B.gray200}\`}}>Backend</div>
      <Field label="Railway Backend URL" field="backendUrl" hint="e.g. https://mcg-marketing-hub.up.railway.app — no trailing slash"/>
      <div style={{fontSize:14,fontWeight:700,color:B.navy,marginBottom:16,marginTop:32,paddingBottom:12,borderBottom:\`1px solid \${B.gray200}\`}}>API Integrations</div>
      <Field label="IXACT CRM API Key" field="ixactKey"/>
      <Field label="Webflow Site ID" field="webflowSiteId"/>
      <Field label="Blog CMS Collection ID" field="blogCollectionId"/>
      <div style={{fontSize:14,fontWeight:700,color:B.navy,marginBottom:16,marginTop:32,paddingBottom:12,borderBottom:\`1px solid \${B.gray200}\`}}>Email Defaults</div>
      <Field label="Default From Address" field="emailFrom"/>
      <div style={{fontSize:14,fontWeight:700,color:B.navy,marginBottom:16,marginTop:32,paddingBottom:12,borderBottom:\`1px solid \${B.gray200}\`}}>Brand Colors</div>
      <div style={{display:'flex',gap:16,marginBottom:20}}>
        {[{label:'Navy',color:B.navy},{label:'Crimson',color:B.crimson},{label:'Gold',color:B.gold},{label:'Cream',color:B.cream}].map(c=><div key={c.label} style={{textAlign:'center'}}>
          <div style={{width:48,height:48,borderRadius:8,background:c.color,border:\`1px solid \${B.gray200}\`}}/>
          <div style={{fontSize:10,color:B.gray600,marginTop:4}}>{c.label}</div>
          <div style={{fontSize:9,color:B.gray400}}>{c.color}</div>
        </div>)}
      </div>
      {saved
        ?<div style={{padding:12,background:'#d1fae5',borderRadius:8,color:'#059669',fontWeight:700,textAlign:'center'}}>Settings saved to browser</div>
        :<button style={S.btn('primary')} onClick={save}><Icon name="check" size={14} color={B.white}/> Save Settings</button>}
    </div>
  </div>;
};


// ─── LOGIN ──────────────────────────────────────────
const LoginScreen=({onLogin})=>{
  const [user,setUser]=useState('');
  const [pass,setPass]=useState('');
  const [err,setErr]=useState('');
  const [loading,setLoading]=useState(false);
  const submit=async()=>{
    if(!user||!pass){setErr('Enter your username and password.');return;}
    setLoading(true);setErr('');
    try{
      const res=await fetch(API_BASE+'/api/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:user,password:pass})});
      const data=await res.json();
      if(res.ok&&data.token){storeToken(data.token);setPass('');onLogin(data.token);}
      else setErr(data.error||'Invalid credentials.');
    }catch{setErr('Cannot reach server. Try again.');}
    finally{setLoading(false);}
  };
  return(
    <div style={{position:'fixed',inset:0,zIndex:999999,background:B.navy,display:'flex',alignItems:'center',justifyContent:'center',fontFamily:B.font}}>
      <div style={{background:B.white,borderRadius:12,padding:'40px 36px',width:340,maxWidth:'90vw',textAlign:'center',boxShadow:'0 20px 60px rgba(0,0,0,.4)'}}>
        <img src="https://cdn.prod.website-files.com/699cb0b733f309dd4bda1b56/69a1adfa32ad89b96dade636_NEW%20LOGO%20COLOR%20copy.png" style={{height:44,width:'auto',display:'block',margin:'0 auto 18px'}} alt="Mason Capital Group"/>
        <h2 style={{fontSize:20,fontWeight:900,color:B.navy,margin:'0 0 6px'}}>Marketing Hub</h2>
        <div style={{fontSize:13,color:B.gray600,marginBottom:24}}>Sign in to access your dashboard</div>
        <input value={user} onChange={e=>setUser(e.target.value)} type="text" placeholder="Username" autoComplete="off"
          style={{width:'100%',padding:'11px 14px',border:'1.5px solid #e5e7eb',borderRadius:6,fontSize:14,marginBottom:10,boxSizing:'border-box',fontFamily:B.font,outline:'none'}}
          onKeyDown={e=>e.key==='Enter'&&submit()}/>
        <input value={pass} onChange={e=>setPass(e.target.value)} type="password" placeholder="Password"
          style={{width:'100%',padding:'11px 14px',border:'1.5px solid #e5e7eb',borderRadius:6,fontSize:14,marginBottom:14,boxSizing:'border-box',fontFamily:B.font,outline:'none'}}
          onKeyDown={e=>e.key==='Enter'&&submit()}/>
        <button onClick={submit} disabled={loading}
          style={{width:'100%',padding:12,background:B.crimson,color:B.white,border:'none',borderRadius:6,fontSize:14,fontWeight:700,cursor:'pointer',fontFamily:B.font,opacity:loading?.6:1}}>
          {loading?'Signing in…':'Sign In'}
        </button>
        {err&&<div style={{color:B.crimson,fontSize:13,marginTop:10}}>{err}</div>}
        <div style={{fontSize:11,color:'#9ca3af',marginTop:16}}>Mason Capital Group • Authorized Access Only</div>
      </div>
    </div>
  );
};

// ─── APP ─────────────────────────────────────────
const App=()=>{
  const [authToken,setAuthToken]=useState(()=>getToken());
  const handleLogin=tok=>{setAuthToken(tok);};
  const handleLogout=()=>{clearToken();setAuthToken(null);};
  const [view,setView]=useState('dashboard');
  const [listings,setListings]=useState(SAMPLE_LISTINGS);
  const [selected,setSelected]=useState(null);
  const [sidebarOpen,setSidebarOpen]=useState(true);
  const [settings,setSettings]=useState(loadSettings);

  const addListing=(l)=>setListings([...listings,l]);

  const navItems=[
    {id:'dashboard',label:'Dashboard',icon:'home'},
    {id:'listings',label:'Listings',icon:'grid'},
    {id:'new-listing',label:'New Listing',icon:'plus'},
    {id:'om',label:'OM Generator',icon:'book'},
    {id:'flyer',label:'Flyer Generator',icon:'doc'},
    {id:'email',label:'Email Campaigns',icon:'mail'},
    {id:'blog',label:'Blog Generator',icon:'blog'},
    {id:'embed',label:'Embed Codes',icon:'code'},
    {id:'settings',label:'Settings',icon:'settings'}
  ];

  const renderView=()=>{
    switch(view){
      case 'dashboard': return <DashboardView listings={listings} setView={setView} setSelected={setSelected}/>;
      case 'listings': return <DashboardView listings={listings} setView={setView} setSelected={setSelected}/>;
      case 'new-listing': return <NewListingView onAdd={addListing} setView={setView}/>;
      case 'listing-detail': return selected?<ListingDetailView listing={selected} setView={setView}/>:null;
      case 'om': return <OMView listings={listings} settings={settings}/>;
      case 'flyer': return <FlyerView listings={listings}/>;
      case 'email': return <EmailView listings={listings}/>;
      case 'blog': return <BlogView/>;
      case 'settings': return <SettingsView settings={settings} setSettings={setSettings}/>;
      default: return <DashboardView listings={listings} setView={setView} setSelected={setSelected}/>;
    }
  };

  if(!authToken) return <LoginScreen onLogin={handleLogin}/>;
  return <div>
    <div style={{...S.sidebar,...(!sidebarOpen?S.sidebarHidden:{})}}>
      <div style={S.logo}>
        <div style={S.logoTitle}>Mason Capital Group</div>
        <div style={S.logoSub}>Marketing Hub</div>
      </div>
      <div style={S.nav}>
        {navItems.map(item=><div key={item.id} style={S.navItem(view===item.id||(view==='listing-detail'&&item.id==='listings'))} onClick={()=>setView(item.id)}>
          <Icon name={item.icon} size={18} color={view===item.id?B.gold:B.gray400}/>
          {item.label}
        </div>)}
      </div>
      <div style={{padding:'16px 20px',borderTop:\`1px solid \${B.dark}\`,fontSize:10,color:B.gray400}}>
        MCG Marketing Hub v2.0<br/>info@masoncapitalgroup.com
      </div>
    </div>
    <div style={{...S.main,...(!sidebarOpen?{marginLeft:0}:{})}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:24}}>
        <button onClick={()=>setSidebarOpen(!sidebarOpen)} style={{background:'none',border:'none',cursor:'pointer',padding:8}}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={B.navy} strokeWidth="2"><path d="M3 12h18M3 6h18M3 18h18"/></svg>
        </button>
        <div style={{display:'flex',alignItems:'center',gap:12}}>
          <div style={{fontSize:11,color:B.gray400}}>Cameron Torabi | Broker/Principal</div>
          <button onClick={handleLogout} style={{fontSize:10,color:B.gray600,background:'none',border:'1px solid '+B.gray200,borderRadius:4,padding:'3px 10px',cursor:'pointer',fontFamily:B.font}}>Sign Out</button>
        </div>
      </div>
      {renderView()}
    </div>
  </div>;
};

ReactDOM.createRoot(document.getElementById('root')).render(<App/>);
// Mount to dedicated root
var hubRoot = document.getElementById('mcg-hub-root') || document.body;
ReactDOM.createRoot(hubRoot).render(<App/>);
`;
    document.body.appendChild(babelScript);
    // Babel standalone auto-processes new text/babel scripts in some versions;
    // explicitly call it to be safe.
    if (window.Babel && Babel.transformScriptTags) {
      Babel.transformScriptTags();
    }
  }

  function init() {
    ensureRoot();
    loadScript('https://unpkg.com/react@18/umd/react.production.min.js', function() {
      loadScript('https://unpkg.com/react-dom@18/umd/react-dom.production.min.js', function() {
        loadScript('https://unpkg.com/@babel/standalone/babel.min.js', bootApp);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
