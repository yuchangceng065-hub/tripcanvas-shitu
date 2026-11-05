import {defineConfig} from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({plugins:[vue()],server:{port:5173,proxy:{'/api':{target:'https://tripcanvas-api.onrender.com',changeOrigin:true,configure(proxy){proxy.on('proxyReq',req=>req.setHeader('Origin','https://tripcanvas-shitu-melody.yuchangceng065.chatgpt.site'))}}}}})
