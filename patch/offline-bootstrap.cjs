"use strict";
// Offline transport layer. Original device IPC and native drivers stay in index.jsc.
const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');
const {app, protocol, net, ipcMain, shell} = require('electron');
const root = path.resolve(__dirname, '../../../offline');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
const configPath = path.join(root, 'offline-config.json');
const config = fs.existsSync(configPath) ? JSON.parse(fs.readFileSync(configPath, 'utf8')) : {};
const profile = config.profile || path.join(app.getPath('appData'), 'MCHOSEHUB');
fs.mkdirSync(profile, {recursive:true});
const logfile = path.join(profile, 'offline-patch.log');
function log(s) { if(fs.existsSync(logfile) && fs.statSync(logfile).size>5*1024*1024) fs.writeFileSync(logfile,''); fs.appendFileSync(logfile, new Date().toISOString()+' '+s+'\n'); }
const setPath = app.setPath.bind(app);
app.setPath = (name, value) => setPath(name, name === 'userData' ? profile : value);
app.setPath('userData', profile);
app.commandLine.appendSwitch('disable-background-networking');
const originalIpcHandle=ipcMain.handle.bind(ipcMain);
ipcMain.handle=(channel,handler)=>originalIpcHandle(channel,/^updater:(start|apply)/.test(channel)?async()=>{throw new Error('Updates are disabled in the offline build.');}:handler);
// External website actions must not leave the offline application.
const openExternal=shell.openExternal.bind(shell);
shell.openExternal=async (url,options)=>{if(/^https?:/i.test(String(url))){log('BLOCK external website');return;}return openExternal(url,options);};
const types = {'.js':'application/javascript','.css':'text/css','.html':'text/html','.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.woff2':'font/woff2','.webm':'video/webm','.ico':'image/x-icon'};
function lookup(raw) {
 let u = new URL(raw);
 let key = u.protocol === 'mchose-renderer:' ? u.pathname.replace(/^\//,'') : u.host+u.pathname;
 if (key === 'www.mchose.com.cn/') key='cdn.mchose.com.cn/customPage/prod/index.html';
 let item=manifest[key];
 if (!item && key.endsWith('/')) item=manifest[key+'index.html'];
 return {key,item};
}
function response(raw, method='GET') {
 let found;
 try { found=lookup(raw); } catch { return new Response('Invalid URL',{status:400}); }
 const {key,item}=found;
 if (!item) {log('MISS '+method+' '+key);return new Response('Unavailable offline',{status:404});}
 const headers={'Content-Type':item.type || types[path.extname(item.file)] || 'application/octet-stream','Access-Control-Allow-Origin':'*','Cache-Control':'no-store'};
 log('LOCAL '+method+' '+key);
 return new Response(method==='HEAD'?null:fs.readFileSync(path.join(root,item.file)),{status:200,headers});
}
// No network fallback: every HTTP request resolves from the packaged snapshot.
const localFetch=async (input,options={})=>response(typeof input==='string'?input:input.url,options.method || input.method || 'GET');
global.fetch=localFetch;
net.fetch=localFetch;
const originalHandle=protocol.handle.bind(protocol);
protocol.handle=(scheme,handler)=>originalHandle(scheme,scheme==='mchose-renderer'?req=>response(req.url,req.method):handler);
app.on('session-created',sess=>{
 sess.enableNetworkEmulation({offline:true});
 for(const scheme of ['http','https']) sess.protocol.handle(scheme,req=>response(req.url,req.method));
});
// Node's HTTP clients (including axios) use a loopback-only transport.
const originalRequest=http.request.bind(http);
const server=http.createServer(async(req,res)=>{
 try {
  const raw=decodeURIComponent(req.url.slice(1));
  const r=response(raw,req.method);
  res.writeHead(r.status,Object.fromEntries(r.headers));res.end(Buffer.from(await r.arrayBuffer()));
 } catch(e) {log('HTTP ERROR '+e.message);res.writeHead(400);res.end('Invalid offline request');}
});
server.listen(0,'127.0.0.1');
{
 function request(input, options, callback) {
  if(typeof options==='function'){callback=options;options={};}
  let opts=typeof input==='string'||input instanceof URL?{...options}: {...input,...options};
  let raw;
  if(typeof input==='string'||input instanceof URL)raw=String(input);
  else raw=(opts.protocol || 'https:')+'//'+(opts.hostname || opts.host)+(opts.port?':'+opts.port:'')+(opts.path || '/');
  opts={...opts,protocol:'http:',hostname:'127.0.0.1',host:'127.0.0.1',port:server.address().port,path:'/'+encodeURIComponent(raw),agent:false};
  delete opts.createConnection;
  return originalRequest(opts,callback);
 }
 http.request=request;https.request=request;
 http.get=https.get=(...args)=>{const req=request(...args);req.end();return req;};
 log('START resources='+Object.keys(manifest).length);
 process.on('uncaughtException',e=>log('ERROR '+e.stack));
 require('./bytecode-loader.cjs');require('./index.jsc');
 app.whenReady().then(()=>log('READY'));
}
app.on('will-quit',()=>server.close());
