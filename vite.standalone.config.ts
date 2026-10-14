import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export default defineConfig(({mode}) => {
  // Proxy only to the password-protected local gateway. No API secret is sent
  // to Vite or embedded in browser assets; normal session cookies authenticate.
  const env=loadEnv(mode,here,"MORPHOPILOT_");
  const target=env.MORPHOPILOT_DEV_GATEWAY||"http://127.0.0.1:8787";
  const address=new URL(target);
  if(address.protocol!=="http:"||!["127.0.0.1","localhost","[::1]"].includes(address.hostname)||address.username||address.password||address.pathname!=="/"||address.search||address.hash)throw new Error("MORPHOPILOT_DEV_GATEWAY must be a loopback HTTP origin, e.g. http://127.0.0.1:8787");
  return {
    root: path.resolve(here, "standalone"),
    plugins: [react()],
    resolve: { alias: { "@": here } },
    css: { postcss: path.resolve(here) },
    server: {host:"127.0.0.1",port:5173,strictPort:true,proxy:Object.fromEntries(["/api/gpu","/login","/auth","/healthz"].map(route=>[route,{target,changeOrigin:false}]))},
    build: { outDir: path.resolve(here, "standalone/dist"), emptyOutDir: true, license: true },
  };
});
