// The hosted UI works without the private local database or collector credentials.
export function previewApi(path:string,options?:RequestInit):unknown {
  const url=new URL(path,'https://dongsell.local');
  const route=url.pathname;
  if(route.startsWith('/workspace/')) {
    const key='dongsell.preview.v1.'+route.split('/').pop();
    if(options?.method==='POST') {
      const body=JSON.parse(String(options.body||'{}'));
      localStorage.setItem(key,JSON.stringify(body.payload||{}));
      return {ok:true};
    }
    try{return JSON.parse(localStorage.getItem(key)||'{}');}catch{return {};}
  }
  if(route==='/products')return {items:[],total:0,page:Number(url.searchParams.get('page')||1)};
  if(route==='/jobs'&&(!options?.method||options.method==='GET'))return [];
  if(route==='/settings')return {default_mode:'mock',credentials_present:false,ds_enabled:false,notes:['화면 미리보기 · 실제 서비스 연동 전입니다.']};
  if(route==='/duplicates')return {duplicate_groups:[],prevented:0};
  if(route==='/health')return {ok:true,mode:'ui-preview'};
  throw Error('화면 미리보기입니다. 실제 상품 수집과 마켓 연결은 이후 단계에서 진행합니다.');
}
