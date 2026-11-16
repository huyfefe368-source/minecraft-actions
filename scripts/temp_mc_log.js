// Terminal styling rendered only through text nodes; log content is never HTML.
const logPalette=['#1b2330','#ff747e','#9ee786','#f5cf78','#83b4ff','#d6a0ff','#7ee2da','#e1e8f2','#8190a6','#ff9da5','#b9f49f','#ffe4a2','#adcfff','#e6bfff','#a5f8ee','#ffffff'];
const mcPalette=['#151c26','#5555ff','#55aa55','#55aaaa','#ff5555','#aa55aa','#ffaa00','#b9c8dc','#77869c','#7799ff','#88ff88','#88ffff','#ff8888','#ff88ff','#ffff88','#ffffff'];
function terminalColor(n){if(n<16)return logPalette[n];if(n<232){const v=[0,95,135,175,215,255],i=n-16;return `rgb(${v[Math.floor(i/36)]},${v[Math.floor(i/6)%6]},${v[i%6]})`}return `rgb(${8+(n-232)*10},${8+(n-232)*10},${8+(n-232)*10})`}
function logTokens(value,style={}){
 const text=String(value).replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g,'');const tokens=[];let pos=0;
 const re=/\x1b\[([0-9;:]*)m|§([0-9a-fk-or])/gi;let match;
 function emit(t){t=t.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,'').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g,'');if(t)tokens.push({text:t,style:{...style}})}
 while((match=re.exec(text))){emit(text.slice(pos,match.index));pos=re.lastIndex;
  if(match[2]){const c=match[2].toLowerCase(),i='0123456789abcdef'.indexOf(c);if(i>=0){for(const k of Object.keys(style))delete style[k];style.color=mcPalette[i]}else if(c==='r'){for(const k of Object.keys(style))delete style[k]}else if(c==='l')style.fontWeight='700';else if(c==='o')style.fontStyle='italic';else if(c==='n')style.textDecoration='underline';else if(c==='m')style.textDecoration='line-through';continue}
  const codes=(match[1]||'0').replace(/:/g,';').split(';').map(Number);
  for(let i=0;i<codes.length;i++){const c=codes[i];if(c===0){for(const k of Object.keys(style))delete style[k]}else if(c===1)style.fontWeight='700';else if(c===2)style.opacity='.7';else if(c===3)style.fontStyle='italic';else if(c===4)style.textDecoration='underline';else if(c===9)style.textDecoration='line-through';else if(c===22){delete style.fontWeight;delete style.opacity}else if(c===23)delete style.fontStyle;else if(c===24||c===29)delete style.textDecoration;else if(c===39)delete style.color;else if(c===49)delete style.backgroundColor;else if((c>=30&&c<=37)||(c>=90&&c<=97))style.color=logPalette[c>=90?c-90+8:c-30];else if((c>=40&&c<=47)||(c>=100&&c<=107))style.backgroundColor=logPalette[c>=100?c-100+8:c-40];else if(c===38||c===48){const key=c===38?'color':'backgroundColor';if(codes[i+1]===5&&Number.isInteger(codes[i+2])&&codes[i+2]>=0&&codes[i+2]<=255){style[key]=terminalColor(codes[i+2]);i+=2}else if(codes[i+1]===2&&codes.slice(i+2,i+5).length===3&&codes.slice(i+2,i+5).every(v=>Number.isInteger(v)&&v>=0&&v<=255)){style[key]=`rgb(${codes.slice(i+2,i+5).join(',')})`;i+=4}}}
 }
 emit(text.slice(pos));return tokens;
}
function renderLog(element,text){
 if(element.dataset.raw===text)return;element.dataset.raw=text;const fragment=document.createDocumentFragment(),style={};
 for(const line of text.split('\n')){const row=document.createElement('span');row.className='log-line';const plain=line.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,'').replace(/§[0-9a-fk-or]/gi,'');
  if(/\b(?:ERROR|SEVERE|FATAL)\b|^\s*(?:Caused by:|Suppressed:)/.test(plain))row.classList.add('log-error');else if(/\bWARN(?:ING)?\b/.test(plain))row.classList.add('log-warn');else if(/\b(?:DEBUG|TRACE)\b/.test(plain))row.classList.add('log-debug');else if(/Done \(|Saved the game|joined the game/.test(plain))row.classList.add('log-success');
  for(const token of logTokens(line,style)){const span=document.createElement('span');span.textContent=token.text;Object.assign(span.style,token.style);row.appendChild(span)}row.appendChild(document.createTextNode('\n'));fragment.appendChild(row);
 }element.replaceChildren(fragment);
}
