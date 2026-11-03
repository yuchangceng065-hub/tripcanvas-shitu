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
