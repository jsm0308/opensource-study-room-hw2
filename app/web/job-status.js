const duration=ms=>{const seconds=Math.max(0,Math.ceil(ms/1000));return seconds<60?`${seconds}초`:`${Math.floor(seconds/60)}분${seconds%60?` ${seconds%60}초`:''}`;};
export function jobWaitText(job={},fallbackElapsed=0){
  const timing=job.timing||job,elapsed=timing.elapsedMs??(job.elapsedSec!=null?job.elapsedSec*1000:fallbackElapsed);
  const low=timing.expectedMinMs||timing.lowerMs,high=timing.expectedMaxMs||timing.upperMs,expected=timing.expectedMs;
  const message=job.message||'준비 중';
  const total=low&&high?`${duration(low)}–${duration(high)}`:expected?`약 ${duration(expected)}`:null;
  const basis=(timing.estimateBasis||timing.basis)==='recent'?'최근 처리 기준':'초기 추정';
  return [message,`경과 ${duration(Math.max(0,elapsed))}`,total?`예상 총 ${total} (${basis})`:'예상 시간을 계산 중',(high||expected)&&elapsed>(high||expected)?'예상보다 지연되고 있습니다. 완료되면 자동으로 표시됩니다.':''].filter(Boolean).join(' · ');
}
