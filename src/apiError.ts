export class ApiError extends Error {
 constructor(public status:number,message:string){super(message);this.name='ApiError'}
}
export function apiErrorMessage(detail:unknown,status:number):string {
 if(typeof detail==='string'&&detail.trim())return detail
 if(Array.isArray(detail)){
  const messages=detail.map((issue:any)=>{
   const field=Array.isArray(issue?.loc)?issue.loc.at(-1):''
   if(field==='password')return '密码需要 8—128 个字符，请检查后重试'
   if(field==='username')return '用户名需要 3—32 个字符，只能包含文字、数字、下划线或短横线'
   // Never include validation input: it may contain credentials or a whole trip.
   const labels:Record<string,string>={prompt:'调整需求',duration:'停留时长',people:'同行人数',start:'出发时间',end:'结束时间',title:'旅行名称',candidateIds:'候选地点',activities:'当天地点'}
   return labels[field]?`${labels[field]}格式不正确，请检查后重试`:'提交内容格式不正确，请检查后重试'
  })
  if(messages.length)return [...new Set(messages)].join('；')
 }
 return status===401?'登录已失效，请重新登录':status===429?'请求较多，请稍后重试':status>=500?'服务暂时繁忙，请稍后重试':`请求未能完成（${status}）`
}
