import { beforeEach,afterEach,it,expect,vi } from 'vitest'
beforeEach(()=>{vi.resetModules();vi.useFakeTimers();vi.stubEnv('VITE_API_BASE','https://example.onrender.com/api/v1')})
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();vi.unstubAllEnvs()})
const healthy=()=>new Response(JSON.stringify({status:'ok',model:false}),{headers:{'content-type':'application/json'}})
it('共享唤醒过程，503 后恢复而不缓存失败',async()=>{
 const fetch=vi.fn().mockResolvedValueOnce(new Response('waking',{status:503})).mockResolvedValue(healthy());vi.stubGlobal('fetch',fetch)
 const {checkAPI,connection}=await import('./api');const a=checkAPI(),b=checkAPI()
 expect(a).toBe(b);await vi.advanceTimersByTimeAsync(1);expect(connection.status).toBe('waking')
 await vi.advanceTimersByTimeAsync(3000);expect((await a)?.status).toBe('ok');expect(connection.status).toBe('online');expect(fetch).toHaveBeenCalledTimes(2)
})
it('限时结束并支持手动重新连接',async()=>{
 const fetch=vi.fn().mockRejectedValue(new TypeError('offline'));vi.stubGlobal('fetch',fetch)
 const {checkAPI,connection}=await import('./api');const pending=checkAPI();await vi.advanceTimersByTimeAsync(101000)
 expect(await pending).toBeNull();expect(connection.status).toBe('offline')
 fetch.mockResolvedValue(healthy());expect((await checkAPI(true))?.status).toBe('ok')
})
it('非 JSON 唤醒页面不会被当作健康响应',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response('<html>starting</html>',{headers:{'content-type':'text/html'}})).mockResolvedValue(healthy()))
 const {checkAPI}=await import('./api');const pending=checkAPI();await vi.advanceTimersByTimeAsync(3001);expect((await pending)?.status).toBe('ok')
})
it('写入结果不确定时不自动重发，避免重复注册或分享',async()=>{
 const fetch=vi.fn().mockResolvedValueOnce(healthy()).mockRejectedValueOnce(new TypeError('connection lost'));vi.stubGlobal('fetch',fetch)
 const {api}=await import('./api');await expect(api('/auth/register','POST',{})).rejects.toThrow('未能确认')
 expect(fetch).toHaveBeenCalledTimes(2)
})

it('模型服务 502 不应让整个后端离线；地点搜索仍可用',async()=>{
 const fetch=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({status:'ok',model:true}),{headers:{'content-type':'application/json'}})).mockResolvedValueOnce(new Response(JSON.stringify({detail:'免费模型暂不可用'}),{status:502})).mockResolvedValueOnce(new Response(JSON.stringify({places:[]})))
 vi.stubGlobal('fetch',fetch)
 const {api,checkAPI,connection}=await import('./api')
 await checkAPI(true)
 await expect(api('/planner/suggest','POST',{})).rejects.toThrow('免费模型暂不可用')
 expect(connection.status).toBe('online');expect(connection.model).toBe(true)
 await expect(api('/places/search?q=灵隐寺')).resolves.toEqual({places:[]})
})
it('网络中断与业务校验拒绝使用不同状态',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({status:'ok',model:true}),{headers:{'content-type':'application/json'}})).mockRejectedValueOnce(new TypeError('fetch failed')))
 const {api,checkAPI,connection}=await import('./api');await checkAPI(true)
 await expect(api('/places/search?q=灵隐寺')).rejects.toThrow('网络连接失败')
 expect(connection.status).toBe('offline')
})

it('规划拒绝保留结构化问题和选项，供连续修复',async()=>{
 const detail={code:'infeasible',issues:[{code:'day_overflow',message:'仍有冲突',options:[{action:'depart_earlier',minutes:15,day:0}]}],routes:{}}
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(healthy()).mockResolvedValueOnce(new Response(JSON.stringify({detail}),{status:422})))
 const {api}=await import('./api')
 try{await api('/planner/confirm','POST',{});expect.unreachable()}catch(error){expect(error).toMatchObject({status:422,detail,message:'仍有冲突'})}
})

it('SSE 流收到 401 时清除令牌，不残留已登录状态',async()=>{
 const store=new Map<string,string>()
 vi.stubGlobal('sessionStorage',{
  getItem:(k:string)=>store.get(k)??null,
  setItem:(k:string,v:string)=>{store.set(k,v)},
  removeItem:(k:string)=>{store.delete(k)}
 })
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('unauthorized',{status:401})))
 const {streamRun,setToken}=await import('./api')
 setToken('stale-token')
 expect(store.get('tripcanvas.session')).toBe('stale-token')
 await expect(streamRun('run-1',()=>{})).rejects.toThrow('无法读取运行轨迹')
 expect(store.has('tripcanvas.session')).toBe(false)
})
