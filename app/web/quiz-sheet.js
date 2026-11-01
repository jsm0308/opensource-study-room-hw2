// One continuous quiz sheet; answers remain keyed by question, never by DOM position.
export function mountQuizSheet(ctx){
  const {$,esc,state,api,request,poll,uuid,saveUi,refreshCatalog,inlineError,showTextDialog,feedbackMarkup,bindFeedback,finishQuiz,pauseQuiz,inlineMarkdown}=ctx;
  const quiz=state.quiz;if(!quiz?.questions?.length)return;
  const key=q=>`${quiz.id}/${q.id}`;
  const host=$('#quizPage'),scroll=host.scrollTop;
  host.innerHTML=`<div class="quiz-inner"><header class="sheet-head"><div><p class="eyebrow">오늘 공부한 내용 · 한 세트</p><h1>오늘 공부 퀴즈</h1><p class="muted">${quiz.questions.length}문제 · ${quiz.questions.filter(q=>quiz.answers?.[q.id]?.submitted).length}문제 제출</p></div><button id="sheetPause">저장하고 나중에</button></header><nav class="question-index" aria-label="문제 이동">${quiz.questions.map((q,i)=>`<button data-question-jump="${esc(q.id)}">${i+1}</button>`).join('')}</nav>${quiz.questions.map((q,i)=>{const a=quiz.answers?.[q.id]||{},d=state.quizDrafts[key(q)]||{};return `<section class="question sheet-question" id="question-${esc(q.id)}" data-question="${esc(q.id)}"><p class="question-number">문제 ${i+1} <span>${a.submitted?'제출됨':'풀어볼 문제'}</span></p><h2>${esc(q.title)}</h2><div class="question-prompt">${inlineMarkdown(q.prompt).replace(/\n/g,'<br>')}</div><label class="answer-label">풀이와 생각<textarea class="answer" aria-label="${i+1}번 답안">${esc(d.text??a.text??'')}</textarea></label><div class="asset-preview">${d.assetId||a.assetId?`<img src="${api}/assets/${encodeURIComponent(d.assetId||a.assetId)}" alt="${i+1}번 풀이 사진">`:''}</div><div class="quiz-actions"><div><input type="file" hidden accept="image/*" capture="environment"><button data-photo>사진 첨부</button><button class="quiet" data-hint>도움 보기</button></div><button class="primary" data-submit>${a.submitted?'다시 제출':'답안 제출'}</button></div><p class="assist-note">${a.assisted?'도움을 보기 전 답안과 구분해 기록합니다.':''}</p><p class="question-progress" role="status"></p><div class="question-feedback">${a.feedback?feedbackMarkup(a.feedback):''}</div></section>`;}).join('')}<div class="sheet-finish"><p class="muted">제출한 답안과 남은 문제를 함께 보관합니다.</p><button class="primary" id="finishEarly">세트 마치기</button></div></div>`;
  let busy=false;
  const cache=(q,box)=>{const previous=state.quizDrafts[key(q)]||{},a=quiz.answers?.[q.id]||{};return state.quizDrafts[key(q)]={text:$('.answer',box).value,assetId:previous.assetId??a.assetId};};
  const lock=value=>{busy=value;host.querySelectorAll('[data-submit],[data-hint],#finishEarly,#sheetPause,[data-photo]').forEach(b=>b.disabled=value);};
  for(const q of quiz.questions){
    const box=$(`[data-question="${CSS.escape(q.id)}"]`,host),input=$('.answer',box);
    input.oninput=()=>{cache(q,box);saveUi();};
    const file=$('input[type=file]',box);
    $('[data-photo]',box).onclick=()=>file.click();
    file.onchange=async()=>{const f=file.files[0];if(!f)return;if(!f.type.startsWith('image/')||f.size>8_000_000){inlineError(new Error('8MB 이하의 PNG·JPG·WebP 사진을 선택해주세요.'));return;}
      lock(true);try{const data=await new Promise((ok,no)=>{const r=new FileReader();r.onload=()=>ok(r.result.split(',')[1]);r.onerror=no;r.readAsDataURL(f);});const result=await request('/assets',{method:'POST',body:JSON.stringify({name:f.name,mime:f.type,data})});cache(q,box).assetId=result.id;saveUi();$('.asset-preview',box).innerHTML=`<img src="${api}/assets/${encodeURIComponent(result.id)}" alt="첨부한 풀이 사진">`;}
      catch(e){inlineError(e);}finally{lock(false);}
    };
    const run=async action=>{if(busy)return;const draft=cache(q,box);saveUi();lock(true);$('.question-progress',box).textContent=action==='answer'?'답안을 확인하고 있습니다.':'도움을 준비하고 있습니다.';
      try{const job=await request(`/quiz/${encodeURIComponent(quiz.id)}/${action}`,{method:'POST',body:JSON.stringify({questionId:q.id,text:draft.text,assetId:draft.assetId,comment:'',requestId:uuid()})});const result=await poll(job.jobId);state.quiz=result.quiz;state.data.activeQuiz=result.quiz;if(action==='answer'){delete state.quizDrafts[key(q)];saveUi();}await refreshCatalog();mountQuizSheet(ctx);if(action==='hint')showTextDialog('도움',result.hint);}
      catch(e){$('.question-progress',box).textContent='처리하지 못했습니다. 답안은 초안에 남아 있습니다.';inlineError(e);}finally{lock(false);}
    };
    $('[data-submit]',box).onclick=()=>run('answer');$('[data-hint]',box).onclick=()=>run('hint');
    if(quiz.answers?.[q.id]?.feedback){
      bindFeedback($('.feedback',box),quiz.answers[q.id].feedback);
      const back=document.createElement('button');back.textContent='이 설명 다시 읽기';back.className='quiet feedback-read';back.onclick=()=>{state.pendingReviewCard=quiz.answers[q.id].feedback.reviewCardId||q.cardId;ctx.setView('review');};$('.question-feedback',box).append(back);
    }
  }
  host.querySelectorAll('[data-question-jump]').forEach(b=>b.onclick=()=>document.getElementById(`question-${b.dataset.questionJump}`)?.scrollIntoView({block:'start',behavior:'smooth'}));
  $('#sheetPause').onclick=()=>pauseQuiz();$('#finishEarly').onclick=()=>{const left=quiz.questions.filter(q=>!quiz.answers?.[q.id]?.submitted).length;if(!left||confirm(`아직 ${left}문제를 제출하지 않았습니다. 여기까지 마칠까요?`))finishQuiz();};
  host.scrollTop=scroll;
}
