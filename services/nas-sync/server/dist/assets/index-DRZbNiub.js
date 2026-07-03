(function(){let e=document.createElement(`link`).relList;if(e&&e.supports&&e.supports(`modulepreload`))return;for(let e of document.querySelectorAll(`link[rel="modulepreload"]`))n(e);new MutationObserver(e=>{for(let t of e)if(t.type===`childList`)for(let e of t.addedNodes)e.tagName===`LINK`&&e.rel===`modulepreload`&&n(e)}).observe(document,{childList:!0,subtree:!0});function t(e){let t={};return e.integrity&&(t.integrity=e.integrity),e.referrerPolicy&&(t.referrerPolicy=e.referrerPolicy),e.crossOrigin===`use-credentials`?t.credentials=`include`:e.crossOrigin===`anonymous`?t.credentials=`omit`:t.credentials=`same-origin`,t}function n(e){if(e.ep)return;e.ep=!0;let n=t(e);fetch(e.href,n)}})();var e=[],t=[],n=null,r=``,i=document.getElementById(`tab-explorer`),a=document.getElementById(`tab-activities`),o=document.getElementById(`page-explorer`),s=document.getElementById(`page-activities`),c=document.getElementById(`folder-tree-root`),l=document.getElementById(`breadcrumb`),u=document.getElementById(`files-tbody`),d=document.getElementById(`activities-tbody`),f=document.getElementById(`refresh-activities`);i.addEventListener(`click`,()=>{i.classList.add(`bg-white`,`shadow-sm`,`text-gray-800`),i.classList.remove(`text-gray-500`),a.classList.remove(`bg-white`,`shadow-sm`,`text-gray-800`),a.classList.add(`text-gray-500`),o.classList.remove(`hidden`),s.classList.add(`hidden`)}),a.addEventListener(`click`,()=>{a.classList.add(`bg-white`,`shadow-sm`,`text-gray-800`),a.classList.remove(`text-gray-500`),i.classList.remove(`bg-white`,`shadow-sm`,`text-gray-800`),i.classList.add(`text-gray-500`),s.classList.remove(`hidden`),o.classList.add(`hidden`),v()});async function p(){try{let t=await fetch(`/api/web/folders/unified`);if(!t.ok)throw Error(`Failed to fetch folders`);e=await t.json(),m()}catch(e){console.error(e),c.innerHTML=`<li class="text-sm text-red-500 p-2">フォルダ一覧の取得に失敗しました。</li>`}}function m(){if(e.length===0){c.innerHTML=`<li class="text-sm text-gray-400 italic p-2">同期フォルダがありません</li>`;return}let t=``;e.forEach(e=>{let i=n===e.folder_id,a=`同期デバイス: ${e.devices.join(`, `)}`;t+=`
      <li class="space-y-1">
        <div class="flex items-center justify-between p-2 rounded-md cursor-pointer transition-colors ${i&&r===``?`bg-blue-50 text-[#2563EB] font-bold`:`hover:bg-gray-50 text-gray-700`}" onclick="selectFolder(${e.folder_id}, '')" title="${b(a)}">
          <span class="flex items-center space-x-2 text-sm">
            <svg class="w-4.5 h-4.5 text-[#E05236]" width="18" height="18" fill="currentColor" viewBox="0 0 20 20"><path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"></path></svg>
            <span class="truncate max-w-[160px] font-semibold text-gray-800">${b(e.virtual_name)}</span>
          </span>
          <span class="text-[9px] bg-[#FFF0ED] text-[#E05236] px-2 py-0.5 rounded-full font-bold shrink-0 border border-[#FFDAD3]">${e.devices.length}台で同期</span>
        </div>
        ${i?h(e.folder_id):``}
      </li>
    `}),c.innerHTML=t}function h(e){let n=new Set;if(t.forEach(e=>{if(e.relative_path.includes(`/`)){let t=e.relative_path.split(`/`),r=``;for(let e=0;e<t.length-1;e++)r=r?`${r}/${t[e]}`:t[e],n.add(r)}}),n.size===0)return``;let i=Array.from(n).sort(),a=`<ul class="pl-4 border-l border-gray-200 ml-4 space-y-1 mt-1">`;return i.forEach(t=>{let n=r===t,i=t.split(`/`).pop()||t,o=(t.split(`/`).length-1)*8;a+=`
      <li style="padding-left: ${o}px">
        <div class="flex items-center space-x-1.5 p-1.5 text-xs rounded-md cursor-pointer transition-colors ${n?`bg-blue-50 text-[#2563EB] font-bold`:`hover:bg-gray-50 text-gray-500`}" onclick="selectFolder(${e}, '${x(t)}')">
          <span>📁</span>
          <span class="truncate">${b(i)}</span>
        </div>
      </li>
    `}),a+=`</ul>`,a}window.selectFolder=async(t,i)=>{n=t,r=i;let a=e.find(e=>e.folder_id===t);a&&(l.textContent=`${a.virtual_name} ${i?` / `+i:``}`),await g(t),m(),_()};async function g(e){try{let n=await fetch(`/api/web/files?folder_id=${e}`);if(!n.ok)throw Error(`Failed to load files`);t=await n.json()}catch(e){console.error(`Failed to load files:`,e),t=[]}}function _(){if(!n)return;let e=t.filter(e=>{if(r===``)return!e.relative_path.includes(`/`);{let t=`${r}/`;return e.relative_path.startsWith(t)?!e.relative_path.substring(t.length).includes(`/`):!1}});if(e.length===0){u.innerHTML=`
      <tr>
        <td colspan="5" class="py-12 text-center text-gray-400 italic">
          このフォルダは空です。
        </td>
      </tr>
    `;return}u.innerHTML=e.map((e,t)=>{let n=t%2==1?`bg-[#F8FAFC]`:`bg-white`,r=y(e.file_size),i=new Date(e.last_modified_at).toLocaleString();return`
      <tr class="${n} hover:bg-blue-50/50 transition-colors">
        <td class="py-3 px-6 font-medium text-gray-900 flex items-center space-x-2">
          <span class="text-lg">📄</span>
          <span class="truncate max-w-sm" title="${b(e.file_name)}">${b(e.file_name)}</span>
        </td>
        <td class="py-3 px-4 text-gray-500">${r}</td>
        <td class="py-3 px-4 text-gray-400 text-xs">${i}</td>
        <td class="py-3 px-4 text-gray-500 text-xs font-semibold">${b(e.device_name)}</td>
        <td class="py-3 px-6 text-right space-x-3 shrink-0">
          <a href="/api/sync/download?folder_id=${e.folder_id}&relative_path=${encodeURIComponent(e.relative_path)}" 
             class="text-[#2563EB] hover:text-blue-800 font-semibold" download="${b(e.file_name)}">ダウンロード</a>
          <button onclick="deleteFile(${e.folder_id}, '${x(e.relative_path)}')"
                  class="text-[#DC2626] hover:text-red-800 font-semibold">削除</button>
        </td>
      </tr>
    `}).join(``)}window.deleteFile=async(e,t)=>{if(confirm(`本当にこのファイルを削除しますか？\n${t}`))try{let n=await fetch(`/api/sync/delete`,{method:`DELETE`,headers:{"Content-Type":`application/json`},body:JSON.stringify({folder_id:e,relative_path:t})});if(n.ok)alert(`削除しました。`),await g(e),_();else{let e=await n.text();alert(`削除に失敗しました: ${e}`)}}catch(e){alert(`通信エラーが発生しました: ${e}`)}};async function v(){try{let e=await fetch(`/api/web/activities`);if(!e.ok)throw Error(`Failed to fetch activities`);let t=await e.json();if(t.length===0){d.innerHTML=`
        <tr>
          <td colspan="4" class="py-8 text-center text-gray-400 italic">
            アクティビティ履歴はありません。
          </td>
        </tr>
      `;return}d.innerHTML=t.map(e=>{let t=new Date(e.created_at).toLocaleString(),n=`bg-gray-100 text-gray-600`,r=e.action_type;return e.action_type===`CREATE`?(n=`bg-green-50 text-green-700 border border-green-200`,r=`新規作成`):e.action_type===`UPDATE`?(n=`bg-blue-50 text-blue-700 border border-blue-200`,r=`更新`):e.action_type===`DELETE`&&(n=`bg-red-50 text-red-700 border border-red-200`,r=`削除`),`
        <tr class="hover:bg-gray-50/50 transition-colors">
          <td class="py-3.5 px-6 text-gray-400 text-xs">${t}</td>
          <td class="py-3.5 px-4 font-semibold text-gray-600">${b(e.device_name)}</td>
          <td class="py-3.5 px-4 text-xs">
            <span class="px-2 py-0.5 rounded-full font-bold ${n}">${r}</span>
          </td>
          <td class="py-3.5 px-6 text-gray-700 font-mono text-xs break-all">${b(e.file_path)}</td>
        </tr>
      `}).join(``)}catch(e){console.error(e),d.innerHTML=`
      <tr>
        <td colspan="4" class="py-8 text-center text-red-500 italic">
          アクティビティ履歴の読み込みに失敗しました。
        </td>
      </tr>
    `}}f.addEventListener(`click`,v);function y(e,t=2){if(!+e)return`0 Bytes`;let n=1024,r=t<0?0:t,i=[`Bytes`,`KB`,`MB`,`GB`,`TB`],a=Math.floor(Math.log(e)/Math.log(n));return`${parseFloat((e/n**a).toFixed(r))} ${i[a]}`}function b(e){return e.replace(/&/g,`&amp;`).replace(/</g,`&lt;`).replace(/>/g,`&gt;`).replace(/"/g,`&quot;`).replace(/'/g,`&#039;`)}function x(e){return e.replace(/\\/g,`\\\\`).replace(/'/g,`\\'`)}p();