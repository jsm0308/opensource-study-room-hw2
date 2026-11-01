import {mountLearningRecords} from './learning-records.js';
import {mountStudyCalendar} from './study-calendar.js';
import {mountAdminPanel,invalidateAdminSnapshot} from './admin-v2.js';
import {jobWaitText} from './job-status.js';
const tabs=[['records','테스트 기록'],['calendar','캘린더'],['summary','전체 현황'],['flow','학습 흐름'],['assessment','평가·루브릭'],['system','학습 규칙·구현']];
export function mountManagement(ctx){
  const {state,request,setView,refreshCatalog}=ctx,nav=document.querySelector('#managementTabs'),page=document.querySelector('#historyPage');
  nav.setAttribute('role','tablist');
  nav.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;const buttons=[...nav.querySelectorAll('[role=tab]')],index=buttons.indexOf(document.activeElement);if(index<0)return;event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[next].focus();buttons[next].click();};
  const initial=new URLSearchParams(location.search).get('panel');
  if(!state.managementTab)state.managementTab=tabs.some(([id])=>id===initial)?initial:'records';
  let generation=0;
  async function select(id){
    const current=++generation;state.managementTab=id;
    const url=new URL(location.href);if(id==='records')url.searchParams.delete('panel');else url.searchParams.set('panel',id);history.replaceState(null,'',url);
    const hadTabFocus=nav.contains(document.activeElement);
    nav.innerHTML=tabs.map(([key,label])=>`<button role="tab" tabindex="${id===key?'0':'-1'}" aria-selected="${id===key}" aria-controls="managementPanel" data-management-tab="${key}">${label}</button>`).join('');
    if(hadTabFocus)nav.querySelector('[aria-selected=true]')?.focus();
    nav.querySelectorAll('button').forEach(button=>button.onclick=()=>select(button.dataset.managementTab));
    page.innerHTML='<div id="managementPanel" role="tabpanel"></div>';page.scrollTop=0;const host=page.firstElementChild;
    if(id==='records'){host.id='recordsPanel';nav.querySelectorAll('button').forEach(button=>button.setAttribute('aria-controls','recordsPanel'));return mountLearningRecords(ctx,{host});}
    if(id==='calendar')return mountStudyCalendar({host,request,courses:state.data.courses,courseId:state.courseId,onStart:async(job,status,item)=>{
      // Opening another panel must not turn a completed background job into navigation.
      while(true){const progress=await request('/jobs/'+encodeURIComponent(job.jobId));
        if(progress.status==='done'){await refreshCatalog();const quiz=progress.result?.quiz||(progress.result?.questions?progress.result:null);if(!quiz)throw Error('준비된 테스트를 불러오지 못했습니다. 다시 확인해주세요.');state.data.activeQuiz=quiz;state.quiz=quiz;invalidateAdminSnapshot();if(host.isConnected&&current===generation&&state.view==='history'){setView('quiz');}return;}
        if(['error','cancelled'].includes(progress.status))throw Error(progress.error||'테스트 준비가 중단됐습니다.');
        status.textContent=jobWaitText(progress);await new Promise(resolve=>setTimeout(resolve,900));
      }
    }});
    return mountAdminPanel({host,request,section:id,courseId:state.courseId,courses:state.data.courses});
  }
  select(state.managementTab);
}
