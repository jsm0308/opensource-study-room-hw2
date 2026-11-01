// One header and panel contract for the learner room and management page.
export function mountWorkspaceHeader({admin=false}={}){
  const host=document.querySelector('.topbar');
  const views=[['materials','자료실'],['board','과목 보드'],['review','복습'],['quiz','퀴즈'],['history','기록 및 관리']];
  host.innerHTML=`<button class="quiet panel-toggle" id="sidebarToggle" aria-controls="sidebar" aria-expanded="true" title="왼쪽 패널 열고 닫기">☰ <span>${admin?'목차':'과목'}</span></button><a class="brand" href="/#board" aria-label="학습실 홈">오픈소스SW 학습실</a><nav aria-label="학습 화면">${views.map(([id,label])=>admin?`<a data-study-view="${id}" href="/#${id}">${label}</a>`:`<button data-view="${id}">${label}</button>`).join('')}</nav><span class="connection" id="connection" hidden></span><div class="space-menu"><button class="quiet" id="spaceMenuButton" aria-expanded="false" aria-label="설계와 안내">···</button><div class="space-options" id="spaceOptions" hidden><a href="/web/about.html">과제 소개</a></div></div><button hidden id="importOpen" aria-label="자료 추가">＋</button>`;
  const menu=document.querySelector('#spaceMenuButton');
  menu.onclick=()=>{const options=document.querySelector('#spaceOptions');options.hidden=!options.hidden;menu.setAttribute('aria-expanded',String(!options.hidden));};
}
export function bindWorkspacePanels(){
  const preview=new URLSearchParams(location.search).get('readOnlyPreview')==='1';
  let pref={};try{pref=JSON.parse(localStorage.getItem('study-room-panels')||'{}');}catch{}
  let leftClosed=pref.leftClosed??matchMedia('(max-width:700px)').matches;
  let chatClosed=pref.chatClosed??matchMedia('(max-width:1500px)').matches;
  const reading=document.querySelector('#reading'),chat=document.querySelector('#chat');
  function apply(){
    document.body.classList.toggle('sidebar-collapsed',leftClosed);
    document.querySelector('#sidebarToggle')?.setAttribute('aria-expanded',String(!leftClosed));
    chat?.classList.toggle('closed',chatClosed);
    const toggle=document.querySelector('#chatToggle');if(toggle){toggle.setAttribute('aria-expanded',String(!chatClosed));toggle.setAttribute('aria-controls','chat');toggle.textContent=chatClosed?'질문 열기':'질문 닫기';}
  }
  function change(side,closed){
    const top=reading?.getBoundingClientRect().top||0;
    const anchor=reading&&[...reading.querySelectorAll('.source-section')].find(x=>x.getBoundingClientRect().bottom>top+20);
    const offset=anchor?anchor.getBoundingClientRect().top-top:0;
    if(side==='left')leftClosed=closed;else chatClosed=closed;
    apply();if(!preview)try{localStorage.setItem('study-room-panels',JSON.stringify({leftClosed,chatClosed}));}catch{}
    requestAnimationFrame(()=>{if(anchor&&reading)reading.scrollTop+=anchor.getBoundingClientRect().top-reading.getBoundingClientRect().top-offset;});
  }
  document.querySelector('#sidebarToggle').onclick=()=>change('left',!leftClosed);
  if(chat){document.querySelector('#chatToggle').onclick=()=>change('chat',!chatClosed);document.querySelector('#chatClose').onclick=()=>change('chat',true);}
  apply();return {openChat:()=>change('chat',false),closeSidebar:()=>change('left',true)};
}
