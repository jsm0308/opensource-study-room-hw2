// Presentation of linked notes. Never copies source text into card storage.
import {lectureAdditions} from './lecture-notes.js';
import {applyReviewOverlays} from './review-overlays.js';
function displayWeekTitle(week){
  const title=String(week?.title||'').replace(/^\s*\d+\s*주차\s*\d+\s*강\s*(?:[·ㆍ-]\s*)?/,'').trim();
  const number=Number(week?.weekNumber),lesson=Number(week?.lessonNumber);
  const prefix=Number.isInteger(number)&&number>0&&Number.isInteger(lesson)&&lesson>0?number+'주차 '+lesson+'강':'';
  return [prefix,title].filter(Boolean).join(' · ');
}
export const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function mathMarkup(tex,display=false){
  try{return globalThis.katex.renderToString(tex,{displayMode:display,throwOnError:true,trust:false,strict:'ignore',output:'htmlAndMathml'}).replace(/\r?\n/g,' ');}
  catch{return `<span class="math-failure" title="${escapeHtml(tex)}">수식을 표시하지 못했습니다. 원문을 확인해주세요.</span>`;}
}
export function isExplicitTeX(value){
  const text=String(value??'').trim();
  // Backticks are usually code. Treat only unmistakable TeX syntax as mathematics.
  if(/^\\[ntr]$/.test(text))return false;
  // A generic `\\word` match turns Windows paths such as `dl-env\\Scripts\\activate`
  // into invalid mathematics.  Keep this list deliberately mathematical; display
  // delimiters ($, \\(, \\[) still handle arbitrary TeX outside code spans.
  const command=/\\(?:alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega|sum|prod|int|iint|iiint|lim|log|ln|exp|sin|cos|tan|frac|dfrac|tfrac|sqrt|left|right|cdot|times|div|pm|mp|leq|geq|neq|approx|infty|partial|nabla|mathbf|mathbb|mathrm|text|operatorname)(?=[^A-Za-z]|$)/;
  if(command.test(text)||/(?:_|\^)\s*\{/.test(text))return true;
  // Preserve compact variable subscripts such as x_i and powers such as x^2,
  // without treating program identifiers like max_iter as equations.
  return /(?:^|[^A-Za-z0-9_])[A-Za-z](?:_[A-Za-z0-9]+|\^[A-Za-z0-9]+)(?=$|[^A-Za-z0-9_])/.test(text);
}
export function inlineMarkdown(text){
  const tokens=[];
  const token=html=>`\u0000${tokens.push(html)-1}\u0000`;
  let s=String(text).replace(/`([^`]+)`/g,(_,v)=>token(isExplicitTeX(v)?mathMarkup(v):`<code>${escapeHtml(v)}</code>`))
    .replace(/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]/g,(_,a,b)=>token(mathMarkup(a||b,true)))
    .replace(/\\\(([\s\S]+?)\\\)/g,(_,v)=>token(mathMarkup(v)))
    .replace(/\$([^$\n]+)\$/g,(_,v)=>token(mathMarkup(v)))
    .replace(/(!?)\[([^\]]+)\]\(([^)]+)\)/g,(_,image,label,url)=>{
      if(/^\/(?:demo\/)?api\/(?:materials\/|source\?)/.test(url)||url.startsWith('/web/slides/'))return token(image?`<img class="lecture-slide" loading="lazy" src="${escapeHtml(url)}" alt="${escapeHtml(label)}">`:`<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`);
      if(/^https?:\/\//i.test(url))return token(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`);
      let path=url;try{path=decodeURIComponent(url);}catch{}
      return token(`<span class="source-reference" title="${escapeHtml(path)}">${escapeHtml(label)} <small>원문 참조</small></span>`);
    });
  return escapeHtml(s).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/\u0000(\d+)\u0000/g,(_,n)=>tokens[Number(n)]);
}
export function explainSource(text){
  return String(text).replace('사용자가 적은 “bias의 목표는 threshold를 빼는 것”은 이번 강의 맥락에서 맞다. 정확한 관계는 부호까지 포함해 기억해야 한다.', 'Bias는 threshold를 가중합의 상수항으로 흡수한다. 가중합에서 threshold를 빼는 표현과 bias를 더하는 표현이 같으려면 $b=-\\theta$여야 한다.').replace('수업 예시에서는 사용자가 경험적으로 선택했다.', '수업 예시에서는 모델 설계자가 경험적으로 선택했다.');
}
export function prepareLectureSource(text,sourceUrl,sourceIdentity=''){
  text=applyReviewOverlays(text,sourceUrl,sourceIdentity);
  // Convert only known document wrappers; arbitrary HTML remains escaped below.
  return explainSource(text).replace(/\r\n/g,'\n').replace(/<a\s+id="[^"]*"\s*><\/a>/g,'').replace(/^\^[\w-]+\s*$/gm,'')
    .replace(/<\/?(?:small|details|summary)(?:\s[^>]*)?>/g,'')
    .replace(/^#{3,4} (?=(?:PDF )?p\.)/gm,'## ')
    .replace(/슬라이드 관련 교수 발화\s*\n\n([^\n]+)/g,'[교수님 말씀] $1')
    .replace(/(!?)\[([^\]]+)\]\(([^)]+)\)/g,(whole,img,label,url)=>{
      if((!img&&!/\.pdf(?:#|$)/i.test(url))||/^(?:https?:|\/|#|<)/.test(url))return whole;
      return `${img}[${label}](${sourceUrl}&attachment=${encodeURIComponent(url)})`;
    });
}
export function renderMarkdown(text){
  const lines=explainSource(text).replace(/`\[강의자료(?:\s*\+\s*교수\s*발화)?\]`\s*/g,'').replace(/\r\n/g,'\n').split('\n'),out=[];
  for(let i=0;i<lines.length;){
    const line=lines[i];if(!line.trim()){i++;continue;}
    if(/^---+$/.test(line.trim())){out.push('<hr>');i++;continue;}
    const teacher=line.match(/^\s*(?:[-*]\s+)?(?:>\s*)?\[교수님 말씀(?:\s*·\s*([^\]]+))?\]\s*(.+)$/);
    if(teacher){out.push(`<aside class="professor-note"><strong>교수님 말씀</strong>${teacher[1]?`<small>${escapeHtml(teacher[1])}</small>`:''}<p>${inlineMarkdown(teacher[2])}</p></aside>`);i++;continue;}
    if(line.startsWith('```')||line.trim()==='$$'){
      const math=line.trim()==='$$',end=math?'$$':'```',body=[];i++;
      while(i<lines.length&&!(math?lines[i].trim()==='$$':lines[i].startsWith(end)))body.push(lines[i++]);if(i<lines.length)i++;
      out.push(math?`<div class="source-equation" tabindex="0">${mathMarkup(body.join('\n'),true)}</div>`:`<pre><code>${escapeHtml(body.join('\n'))}</code></pre>`);continue;
    }
    if(/^\|/.test(line)&&/^\|[\s:|-]+\|\s*$/.test(lines[i+1]||'')){
      const cells=s=>s.replace(/^\||\|\s*$/g,'').split('|').map(x=>inlineMarkdown(x.trim()));
      const header=cells(line);i+=2;const rows=[];while(i<lines.length&&/^\|/.test(lines[i]))rows.push(cells(lines[i++]));
      out.push(`<div class="source-table" tabindex="0" aria-label="가로로 이동 가능한 강의 표"><table><thead><tr>${header.map(c=>`<th>${c}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);continue;
    }
    const heading=line.match(/^(#{1,6})\s+(.+)$/);
    if(heading){const level=Math.min(4,Math.max(2,heading[1].length));out.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);i++;continue;}
    if(/^[-*] |^\d+\. /.test(line)){
      const ordered=/^\d+\./.test(line),tag=ordered?'ol':'ul',items=[];
      while(i<lines.length&&/^[-*] |^\d+\. /.test(lines[i]))items.push(inlineMarkdown(lines[i++].replace(/^([-*]|\d+\.)\s+/,'')));
      out.push(`<${tag}>${items.map(v=>`<li>${v}</li>`).join('')}</${tag}>`);continue;
    }
    const paragraph=[];while(i<lines.length&&lines[i].trim()&&!/^\s*(?:#{1,6}\s+|```|\$\$|\||[-*]\s+|\d+\.\s+)/.test(lines[i]))paragraph.push(lines[i++]);
    if(!paragraph.length)paragraph.push(lines[i++]);out.push(`<p class="selectable">${inlineMarkdown(paragraph.join('\n')).replace(/\n/g,'<br>')}</p>`);
  }
  return out.join('\n');
}
export function splitDocument(text){
  const original=String(text).replace(/\r\n/g,'\n');
  const front=original.match(/^---\n([\s\S]*?)\n---\n/);
  const body=front?original.slice(front[0].length):original;
  const parts=body.split(/(?=^## )/m).filter(x=>x.trim());
  return {metadata:front?.[1]||'',parts:parts.map((text,index)=>({title:text.match(/^## (.+)/)?.[1]||(index===0?'수업 개요':'내용'),text}))};
}
export function matchingCard(cards,sourceCard,title){
  return cards.find(c=>c.weekId===sourceCard.weekId&&(c.source?.assetId||c.source?.path)===(sourceCard.source?.assetId||sourceCard.source?.path)&&String(c.source?.locator||'').split(/\s*;\s*| \/ /).some(loc=>loc.replace(/^#+\s*/,'').toLowerCase()===title.toLowerCase()));
}
export function referencedSlidePages(text){
  const pages=new Set(), source=String(text??'');
  for(const match of source.matchAll(/\bp\.\s*(\d+)(?:\s*(?:~|–|-)\s*(\d+))?/gi)){
    const start=Number(match[1]),end=Number(match[2]||match[1]);
    if(!Number.isFinite(start)||!Number.isFinite(end)||end<start||end-start>300)continue;
    for(let page=start;page<=end;page++)pages.add(page);
  }
  return pages;
}
export function imageSlidePages(text){
  const pages=new Set();
  for(const match of String(text??'').matchAll(/!\[([^\]]*)\]\(([^)]+)\)/g)){
    const target=`${match[1]} ${match[2]}`;
    for(const page of target.matchAll(/(?:page|slide|p)[-_\s]?0*(\d{1,3})(?!\d)/gi))pages.add(Number(page[1]));
  }
  return pages;
}
export function hasCompleteEmbeddedDeck(text){
  const cited=referencedSlidePages(text),embedded=imageSlidePages(text);
  if(!cited.size||cited.size!==embedded.size)return false;
  const max=Math.max(...embedded);
  if(max!==embedded.size)return false;
  for(let page=1;page<=max;page++)if(!embedded.has(page)||!cited.has(page))return false;
  return true;
}
export function slideMaterialsForPart(slides,text){
  const ids=[...new Set(slides.map(p=>p.materialId))];
  if(ids.length<=1)return new Set(ids);
  const source=String(text??'').toLowerCase(),matched=ids.filter(id=>{
    const page=slides.find(p=>p.materialId===id),name=String(page?.materialTitle||'').toLowerCase();
    return name&&source.includes(name);
  });
  return new Set(matched);
}
const seedSlides={
 '전체 흐름':[3,4,5,6], '1. ANN과 Perceptron':[7,8,9], '2. Threshold를 bias로 흡수하는 두 표기':[15],
 '3. SLP와 MLP':[10,11,14], '4. Task가 input과 output node를 결정한다':[16,17,18],
 '5. Softmax와 cross-entropy':[19], '시험 직전 회수':[20]
};
function slideMarkup(pages){return pages.map(page=>`<figure class="slide-source"><a href="/web/slides/ai-w04-l01/page-${String(page).padStart(2,'0')}.png" target="_blank" rel="noopener"><img class="lecture-slide" loading="lazy" src="/web/slides/ai-w04-l01/page-${String(page).padStart(2,'0')}.png" alt="강의 슬라이드 ${page}쪽"></a><figcaption>강의자료 · ${page}쪽 <span>눌러서 크게 보기</span></figcaption></figure>`).join('');}
export function sourceSlides(pages){return pages.map(p=>{
  const width=Number(p.width),height=Number(p.height);
  const dimensions=Number.isInteger(width)&&width>0&&Number.isInteger(height)&&height>0?` width="${width}" height="${height}"`:'';
  return `<figure class="slide-source"><a href="${escapeHtml(p.url)}" target="_blank" rel="noopener"><img class="lecture-slide" loading="lazy" src="${escapeHtml(p.url)}" alt="${escapeHtml(p.title)} ${p.page}쪽"${dimensions}></a><figcaption>${escapeHtml(p.title)} · ${p.page}쪽 <span>원본 슬라이드</span></figcaption></figure>`;
}).join('');}
export function pairLecture(slides,explanation){
  if(!slides)return `<div class="explanation-column">${explanation}</div>`;
  const frames=slides.match(/<figure[\s\S]*?<\/figure>/g)||[];
  return `<div class="lecture-pair"><div class="slide-column"><div class="slide-deck"><div class="pair-label">강의자료 <span>눌러서 크게 보기</span></div>${frames.length>1?`<nav class="slide-pages" aria-label="이 절의 PPT 페이지">${frames.map((f,i)=>`<button data-slide-index="${i}" aria-pressed="${i===0}">${escapeHtml(f.match(/· (\d+)쪽/)?.[1]||i+1)}쪽</button>`).join('')}</nav>`:''}${frames.map((f,i)=>`<div class="slide-frame" ${i?'hidden':''}>${f}</div>`).join('')}</div></div><div class="explanation-column"><p class="pair-label">설명</p>${explanation}</div></div>`;
}
export function arrangeDocument(parsed){
 const corrections=parsed.parts.find(p=>p.title.includes('정확성 보정'))?.text.split('\n').filter(l=>l.startsWith('- '))||[];
 return parsed.parts.filter(p=>!p.title.includes('메모 검증')&&!p.title.includes('정확성 보정')).map(p=>({...p,corrections:corrections.filter(c=>p.title.startsWith('5.')?c.includes('log(0.84)'):p.title.startsWith('4.')?c.includes('regression'):p.title.startsWith('3.')?c.includes('DNN'):false)}));
}
export async function mountReview(ctx){
  const {$,esc,state,cards,course,api,lessonMarkup,bindLessonActions,restoreChat,displayCourseTitle,choose,jumpTo,renderChatContext,setView,showTextDialog,saveUi,request,uuid}=ctx;
  const courseId=state.courseId,reading=$('#reading');
  reading.dataset.loading='true';
  const initialScroll=state.readingPositions[`${courseId}/`]||0;
  const token=String((Number(reading.dataset.renderToken)||0)+1);reading.dataset.renderToken=token;
  state.railObserver?.disconnect();$('#readingRail').innerHTML='';
  if(!$('#reviewOutline'))$('#sidebar').innerHTML='<div id="reviewOutline" aria-label="과목과 문서 목차"></div>';
  $('#reviewScope').textContent=displayCourseTitle(course);
  reading.innerHTML='<div class="reading-inner"><p role="status">연결된 강의 내용을 불러오고 있습니다.</p></div>';
  const sources=new Map();for(const c of cards){const source=c.source?.assetId||c.source?.path,key=`${c.weekId}/${source}`;if(source&&!sources.has(key))sources.set(key,c);}
  const docs=await Promise.all([...sources.values()].map(async c=>{
    const url=`${api}/source?cardId=${encodeURIComponent(c.id)}`;
    try{const response=await fetch(url);if(!response.ok)throw new Error('원문을 불러오지 못했습니다.');const type=response.headers.get('Content-Type')||'';if(!type.includes('text/'))return {card:c,url,type};return {card:c,url,text:await response.text(),type};}
    catch(e){return {card:c,url,error:e.message};}
  }));
  let materials=[];try{const r=await fetch(`${api}/materials?courseId=${encodeURIComponent(courseId)}`);if(r.ok)materials=(await r.json()).materials||[];}catch{}
  // PDF rendering is intentionally serial: generating a first manifest can be expensive.
  for(const doc of docs){
    doc.slides=[];doc.slideErrors=[];
    const embedded=imageSlidePages(doc.text||''),ids=doc.card.source?.materialIds||[];
    const pdfs=materials.filter(m=>ids.includes(m.id)&&m.mime==='application/pdf');
    for(const m of pdfs){
      if(pdfs.length===1&&hasCompleteEmbeddedDeck(doc.text||''))continue;
      const knownPages=(m.slides?.pages||[]).map(p=>Number(p.page));
      // A document that already carries every rendered PDF page needs no second deck.
      if(knownPages.length&&knownPages.every(page=>embedded.has(page)))continue;
      try{const r=await fetch(`${api}/materials/${encodeURIComponent(m.id)}/slides`);if(!r.ok)throw Error('슬라이드 불러오기 실패');const manifest=await r.json();doc.slides.push(...manifest.pages.map(p=>({...p,key:`${m.id}/${p.page}`,materialId:m.id,materialTitle:m.title||m.filename,title:m.title||m.filename,url:`${api}/materials/${encodeURIComponent(m.id)}/slides/${p.page}`})));}catch{doc.slideErrors.push(m.title||m.filename);}
    }
  }
  if(reading.dataset.renderToken!==token||state.view!=='review'||state.courseId!==courseId)return;
  const used=new Set(),html=[];
  for(const [docIndex,doc] of docs.entries()){
    const w=course.weeks?.find(w=>w.id===doc.card.weekId);
    html.push(`<div class="review-source-head" id="source-week-${esc(doc.card.weekId)}"><p class="eyebrow">${esc(w?.title||'연결된 자료')}</p><h1>${esc(doc.card.source.label||displayCourseTitle(course))}</h1>${w?.overview?`<p class="intro">${esc(w.overview)}</p>`:''}<a class="source-original" href="${esc(doc.url)}" target="_blank" rel="noopener">공개 요약 ↗</a>${w?.preview?`<details class="preview"><summary>대표 과제 미리보기</summary><p>${esc(w.preview.prompt)}</p><button class="quiet" data-review-preview="${esc(w.id)}">대표 풀이 보기</button></details>`:''}</div>`);
    if(doc.text!==undefined){
      const sourceIdentity=[doc.card.source?.path,doc.card.source?.label,doc.card.source?.filename,...(doc.card.source?.originalPaths||[])].filter(Boolean).join(' ');
      const parsed=splitDocument(prepareLectureSource(doc.text,doc.url,sourceIdentity));
      const parts=arrangeDocument(parsed),seed=false;
      const shownSlides=new Set();
      for(const [i,part] of parts.entries()){
        if(/^(?:근거|출처).*(?:매핑|범위)/.test(part.title)){html.push(`<details class="source-brief"><summary>출처와 수업 범위</summary>${renderMarkdown(part.text.replace(/^## .+\n/,''))}</details>`);continue;}
        const match=matchingCard(cards,doc.card,part.title);
        const originalSlides=[...part.text.matchAll(/!\[([^\]]+)\]\(([^)]+)\)/g)].filter(m=>m[2].startsWith(doc.url+'&attachment='));
        const bodyText=part.text.replace(/!\[([^\]]+)\]\(([^)]+)\)/g,(whole,label,url)=>url.startsWith(doc.url+'&attachment=')?'':whole);
        const rendered=renderMarkdown(bodyText),heading=rendered.match(/^<h[1-4]>[\s\S]*?<\/h[1-4]>/)?.[0]||'';
        const referencedPages=referencedSlidePages(part.text),embeddedPages=imageSlidePages(part.text),materialIds=slideMaterialsForPart(doc.slides,part.text);
        const embeddedMaterialIds=materialIds.size?materialIds:slideMaterialsForPart(doc.slides,'');
        doc.slides.filter(p=>embeddedMaterialIds.has(p.materialId)&&embeddedPages.has(p.page)).forEach(p=>shownSlides.add(p.key));
        const sectionSlides=doc.slides.filter(p=>!shownSlides.has(p.key)&&materialIds.has(p.materialId)&&referencedPages.has(p.page)&&!embeddedPages.has(p.page));sectionSlides.forEach(p=>shownSlides.add(p.key));
        const id=match&&!used.has(match.id)?`source-${match.id}`:`source-section-${docIndex}-${i}`;if(match)used.add(match.id);
        const addition=seed?lectureAdditions[part.title]:null;
        const explanation=rendered.slice(heading.length)+(addition?.text?renderMarkdown(addition.text):'')+(addition?.professor||[]).map(p=>`<aside class="professor-note"><strong>교수님 말씀</strong><small>발화 요지 · ${esc(p.time)}</small><p>${inlineMarkdown(p.text)}</p></aside>`).join('')+(match&&match.version>1?`<aside class="accuracy-note"><strong>보완한 설명</strong><p>${inlineMarkdown(match.core)}</p>${(match.details||[]).filter(d=>d.supplementary).map(d=>`<p>${inlineMarkdown(d.text)}</p>`).join('')}</aside>`:'')+part.corrections.map(c=>`<aside class="accuracy-note">${inlineMarkdown(c.replace(/^- /,'').replace(/`\[[^\]]+\]` /,''))}</aside>`).join('');
        const localSlides=originalSlides.map(m=>`<figure class="slide-source"><a href="${esc(m[2])}" target="_blank" rel="noopener"><img class="lecture-slide" src="${esc(m[2])}" alt="${esc(m[1])}" loading="lazy"></a><figcaption>${esc(m[1])} <span>원본 슬라이드</span></figcaption></figure>`).join('');
        html.push(`<section class="lesson source-section" id="${esc(id)}" data-card="${esc(match?.id||'')}" data-week="${esc(doc.card.weekId)}" data-source-title="${esc(part.title)}">${heading}${pairLecture(seed?slideMarkup(seedSlides[part.title]||[]):localSlides||sourceSlides(sectionSlides),explanation)}${match?`<div class="lesson-actions"><button class="quiet" data-ask="${esc(match.id)}">이 부분에 질문</button><button class="quiet" data-source="${esc(match.id)}">출처·기록</button><button class="quiet" data-edit="${esc(match.id)}">설명 보완</button></div>`:''}</section>`);
      }
      const rest=doc.slides.filter(p=>!shownSlides.has(p.key));
      if(rest.length)html.push(`<section class="source-section"><h2>함께 확인할 강의 슬라이드</h2><p class="muted">본문 위치가 지정되지 않은 원본 페이지입니다.</p>${sourceSlides(rest)}</section>`);
      if(doc.slideErrors.length)html.push(`<p class="source-note">슬라이드를 불러오지 못했습니다: ${esc(doc.slideErrors.join(', '))}. 자료 추가 화면에서 원본을 열어주세요.</p>`);
      if(parsed.metadata)html.push(`<details class="source-metadata"><summary>문서 정보</summary><pre>${esc(parsed.metadata)}</pre></details>`);
    }else html.push(`<p class="source-note">${esc(doc.error||'이 파일은 원문 열기에서 확인할 수 있습니다. 아래에 저장된 개념 카드를 함께 표시합니다.')}</p>`);
  }
  for(const w of course.weeks||[]){
    if(w.contentStatus!=='needs-explanation')continue;
    html.push(`<section class="source-section" id="source-week-${esc(w.id)}"><h2>${esc(w.title)}</h2><p class="muted">원본 자료는 등록했습니다. 이 범위의 상세 해설은 생성 승인 대기 중입니다.</p><button class="quiet" data-pending-materials>입력 자료에서 원본 열기 →</button></section>`);
  }
  const missing=cards.filter(c=>!docs.some(d=>d.text&&(d.card.source?.assetId||d.card.source?.path)===(c.source?.assetId||c.source?.path)&&d.card.weekId===c.weekId));
  if(missing.length)html.push(missing.map(c=>lessonMarkup({...c,figure:{type:'none'}})).join(''));

  reading.innerHTML=`<div class="reading-inner">${html.join('')}<section class="reading-completions"><h2>읽은 범위 완료하기</h2><p class="muted">설명을 다 읽은 주차만 완료로 표시하세요. 관리 캘린더에 기록됩니다.</p>${(course.weeks||[]).filter(w=>cards.some(c=>c.weekId===w.id)).map(w=>`<div class="reading-check" data-completion-week="${esc(w.id)}"><div><strong>${esc(displayWeekTitle(w))}</strong><p class="completion-status" role="status">완료 기록을 확인하고 있습니다.</p></div><button data-complete-week="${esc(w.id)}" disabled>복습 완료</button></div>`).join('')}</section><div class="quiz-finish"><button class="primary" id="finishQuiz">오늘 공부 퀴즈</button></div></div>`;
  reading.querySelectorAll('[data-pending-materials]').forEach(button=>button.onclick=()=>setView('materials'));
  reading.querySelectorAll('.slide-deck').forEach(deck=>deck.querySelectorAll('[data-slide-index]').forEach(button=>button.onclick=()=>{const n=Number(button.dataset.slideIndex);deck.querySelectorAll('.slide-frame').forEach((frame,i)=>frame.hidden=i!==n);deck.querySelectorAll('[data-slide-index]').forEach((b,i)=>b.setAttribute('aria-pressed',String(i===n)));}));
  try{const complete=await request(`/reading?courseId=${encodeURIComponent(courseId)}`);if(reading.dataset.renderToken!==token)return;for(const row of reading.querySelectorAll('[data-completion-week]')){const saved=(complete.completions||[]).find(c=>c.weekId===row.dataset.completionWeek),button=row.querySelector('button'),status=row.querySelector('.completion-status');button.disabled=false;if(saved){button.textContent='✓ 복습 완료';button.classList.add('completed');status.textContent=`${new Date(saved.completedAt).toLocaleDateString('ko-KR')}에 읽기 완료를 기록했습니다.`;}else status.textContent='아직 완료 표시하지 않았습니다.';}}
  catch{reading.querySelectorAll('.completion-status').forEach(s=>s.textContent='완료 기록을 불러오지 못했습니다. 완료 버튼을 누르면 저장을 다시 시도합니다.');reading.querySelectorAll('[data-complete-week]').forEach(b=>b.disabled=false);}
  reading.querySelectorAll('[data-complete-week]').forEach(button=>button.onclick=async()=>{const status=button.closest('.reading-check').querySelector('.completion-status');button.disabled=true;try{await request('/reading/complete',{method:'POST',body:JSON.stringify({courseId,weekId:button.dataset.completeWeek,requestId:uuid()})});button.textContent='✓ 복습 완료';button.classList.add('completed');status.textContent='저장했습니다. 관리 캘린더에서 완료와 다음 복습 일정을 볼 수 있습니다.';}catch(e){status.textContent=e.message;}finally{button.disabled=false;}});
  $('#finishQuiz').onclick=()=>setView('quiz');
  reading.querySelectorAll('[data-review-preview]').forEach(b=>b.onclick=()=>showTextDialog('대표 풀이',course.weeks.find(w=>w.id===b.dataset.reviewPreview)?.preview?.solution||''));
  bindLessonActions(reading);
  reading.querySelectorAll('[data-card-jump]').forEach(b=>b.onclick=()=>document.getElementById(`lesson-${b.dataset.cardJump}`)?.scrollIntoView({block:'start',behavior:'instant'}));
  const outline=$('#reviewOutline');
  outline.innerHTML=`<div class="outline-label">과목 <span class="course-switch"><button id="reviewCoursePrev" class="quiet" aria-label="이전 과목">‹</button><button id="reviewCourseNext" class="quiet" aria-label="다음 과목">›</button></span></div><div class="review-courses">${state.data.courses.map(c=>`<button class="course-button ${c.id===courseId?'selected':''}" data-review-course="${esc(c.id)}">${esc(displayCourseTitle(c))}</button>`).join('')}</div><p class="outline-label">문서 목차 <span>모든 주차</span></p><div class="review-toc">${course.weeks.map(w=>`<details class="review-week"><summary>${esc(displayWeekTitle(w))}</summary>${cards.filter(c=>c.weekId===w.id).map(c=>`<button class="outline-button" data-review-jump="${esc(c.id)}">${esc(c.title)}</button>`).join('')}</details>`).join('')}</div>`;
  const courseOffset=delta=>{const all=state.data.courses,index=all.findIndex(c=>c.id===courseId);choose(all[(index+delta+all.length)%all.length].id,null);};
  $('#reviewCoursePrev').onclick=()=>courseOffset(-1);$('#reviewCourseNext').onclick=()=>courseOffset(1);
  outline.querySelectorAll('[data-review-course]').forEach(b=>b.onclick=()=>choose(b.dataset.reviewCourse,null));
  outline.querySelectorAll('[data-review-jump]').forEach(b=>b.onclick=()=>{jumpTo(b.dataset.reviewJump);$('#sidebar').classList.remove('open');});
  outline.querySelectorAll('[data-week-jump]').forEach(b=>b.onclick=()=>{const el=document.getElementById(`source-week-${b.dataset.weekJump}`)||document.getElementById(`lesson-${cards.find(c=>c.weekId===b.dataset.weekJump)?.id}`);el?.scrollIntoView({block:'start',behavior:'instant'});});
  $('#reviewInputs')?.addEventListener('click',()=>setView('materials'));
  let loadedWeek;
  const sync=()=>{
    const top=reading.getBoundingClientRect().top+80;
    const sections=[...reading.querySelectorAll('.lesson')];
    const here=sections.find(x=>{const r=x.getBoundingClientRect();return r.top<=top&&r.bottom>top;})||sections.find(x=>x.getBoundingClientRect().top>top);
    if(!here)return;
    if(state.cardId!==here.dataset.card)state.selection='';
    state.cardId=here.dataset.card;
    const c=cards.find(c=>c.id===state.cardId),weekId=here.dataset.week||c?.weekId,w=course.weeks.find(w=>w.id===weekId);
    state.reviewWeekId=weekId;
    if(loadedWeek!==weekId){loadedWeek=weekId;restoreChat();}
    $('#reviewScope').textContent=[w?.title,here.dataset.sourceTitle||c?.title].filter(Boolean).join(' · ');
    $('#reviewScope').title=$('#reviewScope').textContent;
    outline.querySelectorAll('[data-review-jump]').forEach(b=>{b.classList.toggle('selected',b.dataset.reviewJump===state.cardId);if(b.dataset.reviewJump===state.cardId)b.setAttribute('aria-current','location');else b.removeAttribute('aria-current');});renderChatContext();saveUi();if(state.cardId&&state.lastReviewExposure!==state.cardId){state.lastReviewExposure=state.cardId;ctx.exposure?.('read',state.cardId);}
  };
  reading.dataset.loading='false';reading.onscroll=sync;reading.scrollTo({top:initialScroll,behavior:'instant'});
  if(state.pendingReviewCard){const target=document.getElementById(`source-${state.pendingReviewCard}`)||document.getElementById(`lesson-${state.pendingReviewCard}`);target?.scrollIntoView({block:'start',behavior:'instant'});delete state.pendingReviewCard;}
  sync();
  if(state.reviewResize)window.removeEventListener('resize',state.reviewResize);
  state.reviewResize=()=>{if(state.view==='review')sync();};window.addEventListener('resize',state.reviewResize);
}
