"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const state = {
    tab: "scan", settings: null, scan: {}, reconstruction: {},
    sessions: [], selected: "current", currentSession: null, loadedId: null,
    loadingId: null, calibration: null, lastStatusError: null
  };
  let noticeTimer;

  function notice(message, kind="info") {
    const el = $("notice");
    el.textContent = message;
    el.className = "notice " + kind;
    el.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { el.hidden = true; }, kind === "error" ? 9000 : 5000);
  }
  async function api(path, options={}) {
    const response = await fetch(path, {
      cache: "no-store", ...options,
      headers: options.body ? { "Content-Type": "application/json" } : undefined
    });
    let json;
    try { json = await response.json(); } catch (_) { throw Error("Resposta inválida do equipamento."); }
    if (!response.ok || !json.success) throw Error(json.message || "Operação não concluída.");
    return json.data || {};
  }
  function post(path, data={}) { return api(path, {method:"POST", body:JSON.stringify(data)}); }
  function displayDate(s) {
    const date = s.created_at_iso ? new Date(s.created_at_iso) : null;
    return date && !Number.isNaN(date.getTime()) ?
      date.toLocaleString("pt-BR", {dateStyle:"short",timeStyle:"short"}) :
      (s.created_at || "Horário não informado");
  }
  function displayName(s) {
    return s.display_name || (s.created_at_iso ? "Escaneamento " + displayDate(s) : s.session_id);
  }
  function safeDownload(sessionId) {
    return "/api/sessions/" + encodeURIComponent(sessionId) + "/ply?download=1";
  }
  function safePreview(sessionId) {
    return "/api/sessions/" + encodeURIComponent(sessionId) + "/ply";
  }
  function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + tab));
    // Avoid keeping two MJPEG streams open when only one camera panel is visible.
    $("camera-scan").src = tab === "scan" ? "/video" : "";
    $("camera-calibration").src = tab === "calibration" ? "/video" : "";
    if (tab === "history") refreshHistory();
    if (tab === "calibration") refreshCalibration();
    if (tab === "settings") refreshSettings();
    if (tab === "scan") {
      renderer.resize();
      if (state.selected === "current") loadCurrentIfReady();
    }
  }

  class PointViewer {
    constructor(canvas) {
      this.canvas = canvas;
      this.gl = canvas.getContext("webgl", {antialias:true, alpha:false});
      this.count = 0;
      this.yaw = -0.45; this.pitch = 0.3; this.zoom = 1;
      this.pan = [0,0]; this.mode = "rgb";
      if (!this.gl) { $("viewer-empty").textContent = "Este navegador não oferece WebGL."; return; }
      const gl = this.gl;
      const shader = (type,source) => {
        const s = gl.createShader(type);
        gl.shaderSource(s,source); gl.compileShader(s);
        if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)) throw Error(gl.getShaderInfoLog(s));
        return s;
      };
      const program = gl.createProgram();
      gl.attachShader(program,shader(gl.VERTEX_SHADER,
        "attribute vec3 aPos; attribute vec3 aCol; uniform float yaw; uniform float pitch; uniform float zoom; uniform vec2 pan; uniform bool mono; varying vec3 color; void main(){ float cy=cos(yaw),sy=sin(yaw),cp=cos(pitch),sp=sin(pitch); vec3 v=vec3(cy*aPos.x+sy*aPos.z,aPos.y,-sy*aPos.x+cy*aPos.z); v=vec3(v.x,cp*v.y-sp*v.z,sp*v.y+cp*v.z); gl_Position=vec4(v.xy*(zoom/1.6)+pan,v.z*0.2,1.0); gl_PointSize=3.0; color=mono ? vec3(0.38,0.81,1.0):aCol; }"
      ));
      gl.attachShader(program,shader(gl.FRAGMENT_SHADER,
        "precision mediump float; varying vec3 color; void main(){ vec2 p=gl_PointCoord-vec2(0.5); if(dot(p,p)>0.25) discard; gl_FragColor=vec4(color,1.0); }"
      ));
      gl.linkProgram(program);
      if(!gl.getProgramParameter(program,gl.LINK_STATUS)) throw Error(gl.getProgramInfoLog(program));
      this.program = program;
      this.posLoc = gl.getAttribLocation(program,"aPos");
      this.colLoc = gl.getAttribLocation(program,"aCol");
      this.posBuffer = gl.createBuffer();
      this.colBuffer = gl.createBuffer();
      this.uniforms = Object.fromEntries(["yaw","pitch","zoom","pan","mono"].map(n=>[n,gl.getUniformLocation(program,n)]));
      let last = null, mode = "rotate";
      canvas.addEventListener("pointerdown", e => {
        last=[e.clientX,e.clientY]; mode = e.shiftKey || e.button === 2 ? "pan" : "rotate";
        canvas.setPointerCapture(e.pointerId);
      });
      canvas.addEventListener("pointermove", e => {
        if (!last) return;
        const dx=e.clientX-last[0], dy=e.clientY-last[1];
        last=[e.clientX,e.clientY];
        if(mode==="pan"){this.pan[0]+=dx*0.003;this.pan[1]-=dy*0.003;}
        else {this.yaw+=dx*0.008;this.pitch=Math.max(-1.55,Math.min(1.55,this.pitch+dy*0.008));}
        this.draw();
      });
      canvas.addEventListener("pointerup",()=>last=null);
      canvas.addEventListener("pointercancel",()=>last=null);
      canvas.addEventListener("contextmenu",e=>e.preventDefault());
      canvas.addEventListener("wheel",e=>{
        e.preventDefault(); this.zoom=Math.max(0.3,Math.min(10,this.zoom*Math.exp(-e.deltaY*0.001)));
        this.draw();
      },{passive:false});
      window.addEventListener("resize",()=>this.resize());
      this.resize();
    }
    resize() {
      if (!this.gl) return;
      const dpr=Math.min(window.devicePixelRatio||1,2);
      const w=Math.max(1,Math.floor(this.canvas.clientWidth*dpr));
      const h=Math.max(1,Math.floor(this.canvas.clientHeight*dpr));
      if(this.canvas.width!==w||this.canvas.height!==h){this.canvas.width=w;this.canvas.height=h;}
      this.gl.viewport(0,0,w,h);
      this.draw();
    }
    reset() {this.yaw=-0.45;this.pitch=0.3;this.zoom=1;this.pan=[0,0];this.draw();}
    clear() {this.count=0;$("viewer-empty").hidden=false;this.draw();}
    setMode(mode) {this.mode=mode;this.draw();}
    setPoints(points,colors,count) {
      if(!this.gl)return;
      const gl=this.gl, min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
      for(let i=0;i<count;i++) for(let j=0;j<3;j++){
        const n=points[i*3+j];min[j]=Math.min(n,min[j]);max[j]=Math.max(n,max[j]);
      }
      const center=min.map((n,j)=>(n+max[j])/2);
      const extent=Math.max(0.00001,...min.map((n,j)=>max[j]-n));
      const normalized=new Float32Array(points.length);
      for(let i=0;i<count;i++)for(let j=0;j<3;j++)normalized[i*3+j]=(points[i*3+j]-center[j])*2/extent;
      gl.bindBuffer(gl.ARRAY_BUFFER,this.posBuffer);gl.bufferData(gl.ARRAY_BUFFER,normalized,gl.STATIC_DRAW);
      gl.bindBuffer(gl.ARRAY_BUFFER,this.colBuffer);gl.bufferData(gl.ARRAY_BUFFER,colors,gl.STATIC_DRAW);
      this.count=count;
      $("viewer-empty").hidden=true;
      this.draw();
    }
    draw() {
      if(!this.gl)return;
      const gl=this.gl;
      gl.clearColor(0.07,0.105,0.16,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
      if(!this.count)return;
      gl.useProgram(this.program);
      gl.bindBuffer(gl.ARRAY_BUFFER,this.posBuffer);gl.enableVertexAttribArray(this.posLoc);
      gl.vertexAttribPointer(this.posLoc,3,gl.FLOAT,false,0,0);
      gl.bindBuffer(gl.ARRAY_BUFFER,this.colBuffer);gl.enableVertexAttribArray(this.colLoc);
      gl.vertexAttribPointer(this.colLoc,3,gl.FLOAT,false,0,0);
      gl.uniform1f(this.uniforms.yaw,this.yaw);
      gl.uniform1f(this.uniforms.pitch,this.pitch);
      gl.uniform1f(this.uniforms.zoom,this.zoom);
      gl.uniform2f(this.uniforms.pan,...this.pan);
      gl.uniform1i(this.uniforms.mono,this.mode==="mono" ? 1 : 0);
      gl.drawArrays(gl.POINTS,0,this.count);
    }
  }

  function parsePly(text) {
    const lines=text.replace(/\r/g,"").split("\n");
    if(lines[0].trim()!=="ply" || !lines[1].includes("ascii"))throw Error("A visualização aceita arquivos PLY ASCII.");
    const end=lines.indexOf("end_header");
    if(end<0)throw Error("Cabeçalho PLY incompleto.");
    let count=0,inVertex=false,props=[];
    for(let i=1;i<end;i++){
      const words=lines[i].trim().split(/\s+/);
      if(words[0]==="element"){inVertex=words[1]==="vertex"; if(inVertex)count=Number(words[2]);}
      if(words[0]==="property" && inVertex)props.push(words[words.length-1]);
    }
    if(!Number.isInteger(count)||count<1||count>500000)throw Error("Quantidade de pontos inválida ou muito elevada.");
    const ix=["x","y","z"].map(n=>props.indexOf(n));
    if(ix.some(n=>n<0))throw Error("Coordenadas XYZ não encontradas.");
    const rgb=["red","green","blue"].map(n=>props.indexOf(n));
    const positions=new Float32Array(count*3),colors=new Float32Array(count*3);
    for(let i=0;i<count;i++){
      const values=(lines[end+1+i]||"").trim().split(/\s+/).map(Number);
      for(let j=0;j<3;j++){
        const n=values[ix[j]];
        if(!Number.isFinite(n))throw Error("Coordenadas PLY incorretas.");
        positions[i*3+j]=n;
        const channel=rgb[j]>=0?values[rgb[j]]:null;
        colors[i*3+j]=Number.isFinite(channel)?Math.max(0,Math.min(255,channel))/255:0.65;
      }
    }
    return {positions,colors,count};
  }

  const renderer=new PointViewer($("viewer-canvas"));
  async function loadPly(sessionId) {
    if(!sessionId || state.loadedId===sessionId || state.loadingId===sessionId)return;
    state.loadingId=sessionId;
    $("model-state").textContent="Carregando PLY...";
    try{
      const response=await fetch(safePreview(sessionId),{cache:"no-store"});
      if(!response.ok)throw Error("PLY ainda não disponível.");
      const cloud=parsePly(await response.text());
      if(state.loadingId!==sessionId)return;
      renderer.setPoints(cloud.positions,cloud.colors,cloud.count);
      const meta=state.sessions.find(s=>s.session_id===sessionId)||{};
      $("model-name").textContent=displayName(meta);
      $("model-points").textContent=cloud.count.toLocaleString("pt-BR");
      $("model-file").textContent=(meta.point_clouds||[]).slice(-1)[0]||"PLY da sessão";
      $("model-state").textContent="Disponível";
      $("viewer-download").href=safeDownload(sessionId);
      $("viewer-download").setAttribute("aria-disabled","false");
      state.loadedId=sessionId;
    }catch(error){
      renderer.clear();
      $("model-state").textContent=error.message;
      $("viewer-download").setAttribute("aria-disabled","true");
      $("viewer-download").removeAttribute("href");
    }finally{if(state.loadingId===sessionId)state.loadingId=null;}
  }
  function selectedSessionId(){
    return state.selected==="current"?state.currentSession:state.selected;
  }
  function loadCurrentIfReady(){
    const sessionId=selectedSessionId();
    const record=state.sessions.find(s=>s.session_id===sessionId);
    if(record&&record.ply_available)loadPly(sessionId);
  }
  function refreshOptions(){
    const select=$("viewer-source");
    const previous=select.value||state.selected;
    select.replaceChildren(new Option("Escaneamento atual","current"));
    state.sessions.forEach(s=>{
      if(s.ply_available)select.add(new Option(displayName(s),s.session_id));
    });
    if(previous==="current"||state.sessions.some(s=>s.session_id===previous&&s.ply_available))select.value=previous;
    else select.value="current";
    state.selected=select.value;
  }
  async function refreshHistory(){
    try{
      const data=await api("/api/sessions");
      state.sessions=data.sessions||[];
      $("history-count").textContent=state.sessions.length+" sessões";
      renderHistory();
      refreshOptions();
      if(state.tab==="scan")loadCurrentIfReady();
    }catch(error){if(state.tab==="history")notice(error.message,"error");}
  }
  function makeButton(text,action,variant="subtle",disabled=false){
    const button=document.createElement("button");
    button.type="button";button.className="btn "+variant;button.textContent=text;
    button.disabled=disabled;button.addEventListener("click",action);return button;
  }
  function renderHistory(){
    const list=$("history-list");list.replaceChildren();
    if(!state.sessions.length){const p=document.createElement("p");p.className="empty-message";p.textContent="Nenhum escaneamento salvo.";list.append(p);return;}
    for(const s of state.sessions){
      const item=document.createElement("article");item.className="session-item";
      const info=document.createElement("div");
      const title=document.createElement("div");title.className="session-title";title.textContent=displayName(s);
      const meta=document.createElement("div");meta.className="session-meta";
      meta.textContent=displayDate(s)+" · "+(s.captures||[]).length+" fotos · "+(s.reconstruction_status==="finished"?"PLY pronto":s.status==="stopped"?"Interrompido":s.reconstruction_status==="error"?"Falha na reconstrução":s.status==="running"?"Capturando": "Aguardando PLY");
      info.append(title,meta);item.append(info);
      const actions=document.createElement("div");actions.className="session-actions";
      actions.append(makeButton("Visualizar",()=>{
        state.selected=s.session_id;switchTab("scan");refreshOptions();
        $("viewer-source").value=s.session_id;loadPly(s.session_id);
      },"subtle",!s.ply_available));
      const anchor=document.createElement("a");anchor.className="btn subtle";anchor.textContent="↓ Baixar PLY";anchor.href=s.ply_available?safeDownload(s.session_id):"#";
      if(!s.ply_available)anchor.setAttribute("aria-disabled","true");
      actions.append(anchor);
      actions.append(makeButton("Renomear",async()=>{
        const input=prompt("Novo nome deste escaneamento:",displayName(s));
        if(input===null)return;
        try{await post("/api/sessions/"+encodeURIComponent(s.session_id)+"/rename",{name:input});notice("Nome atualizado.","success");await refreshHistory();}
        catch(error){notice(error.message,"error");}
      }));
      actions.append(makeButton("Excluir",async()=>{
        if(!confirm("Excluir este escaneamento, suas fotografias e o PLY associado? Esta ação não pode ser desfeita."))return;
        try{await api("/api/sessions/"+encodeURIComponent(s.session_id),{method:"DELETE"});notice("Escaneamento excluído.","success");if(state.loadedId===s.session_id){state.loadedId=null;renderer.clear();}await refreshHistory();}
        catch(error){notice(error.message,"error");}
      },"danger-outline"));
      item.append(actions);list.append(item);
    }
  }
  async function refreshSettings(){
    try{
      const data=await api("/settings");state.settings=data.settings;
      $("settings-angle").value=String(data.settings.angle_deg).replace(".",",");
      $("settings-step-delay").value=data.settings.step_delay;
      $("settings-capture-delay").value=data.settings.capture_delay;
      calculateAngle();
    }catch(error){notice(error.message,"error");}
  }
  function calculateAngle(){
    const angle=Number($("settings-angle").value.replace(",","."));
    const valid=Number.isFinite(angle)&&angle>=0.5&&angle<=90;
    const count=valid?Math.max(4,Math.ceil(360/angle-1e-9)):0;
    $("settings-count").textContent=valid?String(count):"Inválido";
    $("settings-effective").textContent=valid?(360/count).toFixed(3).replace(".",",")+"°":"—";
    $("settings-save").disabled=!valid;
  }

  async function refreshStatus(){
    try{
      const results=await Promise.all([api("/status"),api("/api/reconstruction/status")]);
      state.scan=results[0].status||{};state.reconstruction=results[1]||{};
      const scan=state.scan,rec=state.reconstruction;
      const running=Boolean(scan.running),reconstructing=Boolean(rec.running);
      state.currentSession=scan.current_session||scan.last_session||state.currentSession;
      $("connection-text").textContent="Serviço conectado";
      $("connection-led").className="led ok";
      $("mode-chip").textContent=scan.simulation_mode?"Modo de simulação":"Modo físico";
      $("scan-start").disabled=running||reconstructing;
      $("scan-stop").disabled=!running;
      $("scan-angle").textContent=scan.angle_deg ? scan.angle_deg+"°":(scan.scan_steps? (360/scan.scan_steps).toFixed(2)+"°":"—");
      $("scan-total").textContent=scan.scan_steps||"—";
      $("scan-session").textContent=state.currentSession||"—";
      const done=Number(scan.frames_captured)||0,total=Number(scan.scan_steps)||0;
      const percent=total?Math.max(0,Math.min(100,Math.round(done/total*100))):0;
      $("scan-progress-label").textContent=done+" / "+total+" ("+percent+"%)";
      $("scan-progress-bar").style.width=percent+"%";
      $("scan-progress-track").setAttribute("aria-valuenow",String(percent));
      const statusText=running?"Capturando fotografias":reconstructing?"Reconstruindo nuvem de pontos":rec.error?"Falha na reconstrução":scan.last_error?"Erro na captura":scan.point_cloud_generated?"Modelo concluído":"Pronto para escanear";
      $("scan-state").textContent=statusText;
      $("scan-message").textContent=rec.error||scan.last_error||(reconstructing?rec.message:running?"Mesa em operação. Aguarde as capturas.":scan.point_cloud_generated?"PLY pronto para visualizar e baixar.":"Posicione o objeto na mesa para começar.");
      if(!state.lastStatusError && (rec.error||scan.last_error)){notice(rec.error||scan.last_error,"error");state.lastStatusError=rec.error||scan.last_error;}
      if(!rec.error&&!scan.last_error)state.lastStatusError=null;
      if(state.tab==="scan"&&state.selected==="current"&&scan.point_cloud_generated&&state.loadedId!==state.currentSession)await refreshHistory();
    }catch(error){
      $("connection-text").textContent="Sem conexão";
      $("connection-led").className="led error";
      $("scan-start").disabled=true;
    }
  }
  function calibrationBadge(id, label, type="pending") {
    const element=$(id);
    element.textContent=label;
    element.className="cal-state is-"+type;
  }
  function calibrationFeedback(type,title,message,progress=0) {
    $("cal-feedback").dataset.state=type;
    $("cal-feedback-symbol").textContent=({
      running:"↻", success:"✓", error:"!", prepared:"→", idle:"○"
    })[type]||"○";
    $("cal-feedback-title").textContent=title;
    $("cal-status").textContent=message;
    const percentage=Math.round(Math.max(0,Math.min(100,progress)));
    $("cal-progress").style.width=percentage+"%";
    $("cal-progress-track").setAttribute("aria-valuenow",String(percentage));
  }
  async function refreshCalibration(){
    try{
      const data=await api("/api/calibration/workflow");
      state.calibration=data;
      const profiles=data.profiles||{},run=data.run||{},stages=data.stages||{};
      const cam=Boolean(profiles.camera?.calibrated&&profiles.turntable?.calibrated);
      const laser=Boolean(profiles.laser?.calibrated);
      const pending=Boolean(data.pending_recalibration);
      const operation=String(run.operation||"");
      const cameraActive=Boolean(run.running&&operation.endsWith("_camera"));
      const laserActive=Boolean(run.running&&operation.endsWith("_laser"));
      const completed=(!run.running&&!run.error&&String(run.message||"").startsWith("Pronto!"));
      if(cameraActive)calibrationBadge("cal-camera-state","Em andamento","running");
      else if(pending)calibrationBadge("cal-camera-state","Calculado · aguarda laser","complete");
      else if(cam)calibrationBadge("cal-camera-state",completed&&operation==="compute_laser"?"Calibrado":"Perfil salvo","complete");
      else if(run.error&&operation.endsWith("_camera"))calibrationBadge("cal-camera-state","Falha","error");
      else calibrationBadge("cal-camera-state","Pendente","pending");
      if(laserActive)calibrationBadge("cal-laser-state","Em andamento","running");
      else if(pending)calibrationBadge("cal-laser-state","Nova calibração pendente","prepared");
      else if(laser)calibrationBadge("cal-laser-state",completed&&operation==="compute_laser"?"Calibrado":"Perfil salvo","complete");
      else if(run.error&&operation.endsWith("_laser"))calibrationBadge("cal-laser-state","Falha","error");
      else calibrationBadge("cal-laser-state","Pendente","pending");
      $("calibration-chip").textContent=pending?"Aguardando laser":cam&&laser?"Calibração disponível":"Calibração incompleta";
      const inProgress=Boolean(run.running);
      const percent=Number(run.total)>0?100*(Number(run.done)||0)/Number(run.total):0;
      const text=String(run.message||"");
      if(inProgress)calibrationFeedback(
        "running","Calibração em andamento",text||"Aguarde a finalização da etapa.",percent
      );
      else if(run.error)calibrationFeedback(
        "error","Calibração não concluída",
        String(run.error)+(cam&&laser?" O perfil válido anterior continua salvo.":""),
        0
      );
      else if(completed&&pending)calibrationFeedback(
        "prepared","Câmera e eixo concluídos",
        text,100
      );
      else if(completed)calibrationFeedback(
        "success","Pronto! Calibração concluída",text,100
      );
      else if(pending)calibrationFeedback(
        "prepared","Aguardando calibração do laser",
        "Os novos parâmetros da câmera e do eixo foram calculados. Calibre o laser para ativar o conjunto completo.",
        0
      );
      else if(cam&&laser)calibrationFeedback(
        "success","Pronto! Perfil de calibração salvo",
        "Há uma calibração válida salva no equipamento. Você pode reutilizá-la sem calibrar novamente.",
        100
      );
      else calibrationFeedback(
        "idle","Aguardando calibração",
        "Confira a iluminação, posicione o tabuleiro e inicie a primeira etapa.",
        0
      );
      const busy=inProgress||!!state.scan.running||!!state.reconstruction.running;
      $("cal-camera-capture").disabled=busy;
      $("cal-laser-capture").disabled=busy||(!cam&&!pending);
      // Recalculation is for recovery from saved photographs; no second
      // button press is needed in the normal automatic calibration flow.
      $("cal-camera-compute").disabled=busy||Number(stages.camera1?.board_found||0)<8;
      $("cal-laser-compute").disabled=busy||Number(stages.laser1?.board_found||0)<1||(!cam&&!pending);
      $("cal-stop").disabled=!inProgress;
      $("cal-reset-photos").disabled=busy;
    }catch(error){
      calibrationBadge("cal-camera-state","Indisponível","error");
      calibrationBadge("cal-laser-state","Indisponível","error");
      calibrationFeedback("error","Não foi possível consultar a calibração",error.message,0);
    }
  }
  async function calibrationCommand(path,body){
    try{await post(path,body);notice(path.endsWith("/stop")?"Parada da calibração solicitada.":"Procedimento iniciado. Aguarde o resultado no painel.");await refreshCalibration();}
    catch(error){notice(error.message,"error");}
  }
  function bind(){
    document.querySelectorAll(".nav-item").forEach(b=>b.addEventListener("click",()=>switchTab(b.dataset.tab)));
    $("scan-start").addEventListener("click",async()=>{
      try{await post("/start-scan");if(state.selected==="current"){state.loadedId=null;renderer.clear();}notice("Escaneamento iniciado.","success");await refreshStatus();}
      catch(error){notice(error.message,"error");}
    });
    $("scan-stop").addEventListener("click",async()=>{try{await post("/stop-scan");notice("Parada solicitada.");}catch(e){notice(e.message,"error");}});
    $("history-refresh").addEventListener("click",refreshHistory);
    $("viewer-source").addEventListener("change",()=>{
      state.selected=$("viewer-source").value;state.loadedId=null;
      renderer.clear();loadCurrentIfReady();
    });
    $("viewer-color").addEventListener("change",()=>renderer.setMode($("viewer-color").value));
    $("viewer-reset").addEventListener("click",()=>renderer.reset());
    $("viewer-download").addEventListener("click",event=>{if($("viewer-download").getAttribute("aria-disabled")==="true")event.preventDefault();});
    $("settings-angle").addEventListener("input",calculateAngle);
    $("settings-form").addEventListener("submit",async e=>{
      e.preventDefault();
      try{
        await post("/settings",{
          angle_deg:$("settings-angle").value,
          step_delay:Number($("settings-step-delay").value),
          capture_delay:Number($("settings-capture-delay").value)
        });notice("Configurações salvas.","success");await refreshSettings();await refreshStatus();
      }catch(error){notice(error.message,"error");}
    });
    $("cal-check").addEventListener("click",async()=>{
      try{
        const data=await post("/api/calibration/workflow/check-board");
        $("board-result").textContent=data.board_detected?"54 cantos encontrados.":"Tabuleiro não reconhecido. Ajuste a posição e a iluminação.";
        $("board-preview").hidden=!data.preview_url;
        if(data.preview_url)$("board-preview").src=data.preview_url+"?t="+Date.now();
      }catch(error){notice(error.message,"error");}
    });
    for(const kind of ["camera","laser"]){
      $("cal-"+kind+"-capture").addEventListener("click",()=>calibrationCommand(
        "/api/calibration/workflow/capture",{
          kind,placement:1,square_size_mm:Number($("cal-square").value),
          right_direction:Number($("cal-direction").value)
        }
      ));
      $("cal-"+kind+"-compute").addEventListener("click",()=>calibrationCommand(
        "/api/calibration/workflow/compute",{kind}
      ));
    }
    $("cal-stop").addEventListener("click",()=>calibrationCommand("/api/calibration/workflow/stop"));
    $("cal-reset-photos").addEventListener("click",async()=>{
      if(!confirm("Apagar fotografias e dados temporários das tentativas anteriores de calibração? Os perfis ativos serão mantidos."))return;
      try{await post("/api/calibration/workflow/reset");notice("Fotos temporárias removidas; perfis ativos preservados.","success");await refreshCalibration();}
      catch(error){notice(error.message,"error");}
    });
  }
  bind();
  switchTab("scan");
  Promise.all([refreshSettings(),refreshHistory(),refreshStatus()]).catch(()=>{});
  setInterval(refreshStatus,1800);
  setInterval(refreshHistory,9000);
  setInterval(()=>{if(state.tab==="calibration")refreshCalibration();},1800);
})();