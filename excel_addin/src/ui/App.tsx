import React, { useEffect, useMemo, useState } from "react";
import { endpoints, ASGL_BASE_URL } from "../config";
import { request } from "../api";
import { captureSelection } from "../cellCapture";
import { log, exportLogs } from "../logger";

function Card({title,children}:{title:string,children:any}){
  return (
    <div style={{background:"#fff",border:"1px solid #d2d6de",borderRadius:16,padding:14,marginBottom:12}}>
      <div style={{fontWeight:900,marginBottom:10}}>{title}</div>
      {children}
    </div>
  );
}

export default function App(){
  const [apiKey,setApiKey]=useState(localStorage.getItem("asgl_api_key")||"");
  const [tenant,setTenant]=useState(localStorage.getItem("asgl_tenant")||"");
  const [xlsx,setXlsx]=useState("");
  const [pdf,setPdf]=useState("");
  const [selection,setSelection]=useState<string[]>([]);
  const [job,setJob]=useState<string|undefined>();
  const [status,setStatus]=useState<string>("");
  const [progress,setProgress]=useState<number>(0);

  const canRun = apiKey && xlsx && pdf;

  function saveSettings(){
    localStorage.setItem("asgl_api_key", apiKey);
    localStorage.setItem("asgl_tenant", tenant);
    alert("Saved.");
  }

  async function doCapture(){
    const sel = await captureSelection();
    setSelection(sel);
    log("INFO","cell_capture",sel);
  }

  function buildAutoMap(){
    // Auto-map: create a variable per selection address (simple starter; can evolve)
    return {
      sheet: "ActiveSheet",
      variables: selection.map((addr,i)=>({
        id: `v_${i}`,
        name: `Var_${i+1}`,
        ranges: [{ name: `Var_${i+1}`, areas: addr.split(",").map(a=>({start:a.trim(), end:a.trim()})) }]
      }))
    };
  }

  async function submit(){
    try{
      log("INFO","job_submit_clicked",{tenant});
      const mapping = selection.length?buildAutoMap():null;
      const j = await request("POST", endpoints.submit, apiKey, {
        spreadsheet_path:xlsx,
        pdf_path:pdf,
        source:"excel_addin",
        telemetry:{tenant,user:"addin"},
        mapping
      });
      const jobId = j.job_id || j.id;
      setJob(jobId);
      setStatus("submitted");
      poll(jobId);
    }catch(e:any){
      alert("Submit failed: "+e.message);
    }
  }

  async function poll(jobId:string){
    const start=Date.now();
    while(Date.now()-start < 5*60*1000){
      try{
        const j = await request("GET", endpoints.status(jobId), apiKey);
        setStatus(j.status||"unknown");
        setProgress(j.progress||0);
        if(j.status==="completed" || j.status==="failed") break;
      }catch(e){
        // keep polling
      }
      await new Promise(r=>setTimeout(r,2000));
    }
  }

  function openReport(){
    if(!job) return;
    window.open(ASGL_BASE_URL + endpoints.report(job));
  }

  function downloadDiagnostics(){
    const blob = new Blob([exportLogs()],{type:"application/json"});
    const a=document.createElement("a");
    a.href=URL.createObjectURL(blob);
    a.download="asgl_addin_diagnostics.json";
    a.click();
  }

  return (
    <div style={{fontFamily:"system-ui",background:"#f5f6f8",minHeight:"100vh",padding:14}}>
      <div style={{fontWeight:1000,fontSize:18,marginBottom:12}}>ASGL‑A Excel Add‑in (v3)</div>

      <Card title="Auth & Tenant">
        <div style={{display:"flex",gap:10,flexWrap:"wrap"}}>
          <input value={apiKey} onChange={e=>setApiKey(e.target.value)} placeholder="API Key"
            style={inp}/>
          <input value={tenant} onChange={e=>setTenant(e.target.value)} placeholder="Tenant/Firm ID"
            style={{...inp,minWidth:220}}/>
          <button onClick={saveSettings} style={btnPrimary}>Save</button>
        </div>
        <div style={{color:"#7a8294",fontSize:13,marginTop:8}}>OAuth-ready stubs can replace API keys later without refactor.</div>
      </Card>

      <Card title="Cell Capture & Auto Mapping">
        <div style={{display:"flex",gap:10,flexWrap:"wrap"}}>
          <button onClick={doCapture} style={btnGhost}>Capture Selected Cells</button>
          <button onClick={()=>alert(JSON.stringify(buildAutoMap(),null,2))} style={btnGhost}>Preview Auto Map JSON</button>
          <button onClick={()=>window.open(ASGL_BASE_URL+endpoints.templatePicker)} style={btnGhost}>Open Template Picker</button>
        </div>
        <pre style={pre}>{selection.join("\n")||"No selection captured."}</pre>
      </Card>

      <Card title="Run Compare">
        <div style={{display:"grid",gap:10}}>
          <input value={xlsx} onChange={e=>setXlsx(e.target.value)} placeholder="Spreadsheet path (server-accessible)" style={inp}/>
          <input value={pdf} onChange={e=>setPdf(e.target.value)} placeholder="PDF path (server-accessible)" style={inp}/>
          <div style={{display:"flex",gap:10,flexWrap:"wrap"}}>
            <button disabled={!canRun} onClick={submit} style={canRun?btnPrimary:btnDisabled}>Run ASGL‑A</button>
            <button disabled={!job} onClick={openReport} style={job?btnGhost:btnDisabled}>Open report</button>
          </div>
          <div style={{color:"#7a8294",fontSize:13}}>Status: <b>{status||"—"}</b> • Progress: <b>{Math.round(progress*100)}%</b></div>
        </div>
      </Card>

      <Card title="Diagnostics">
        <button onClick={downloadDiagnostics} style={btnGhost}>Download diagnostics bundle</button>
      </Card>
    </div>
  );
}

const btnPrimary:React.CSSProperties={border:"1px solid transparent",background:"#215aaa",color:"#fff",padding:"10px 12px",borderRadius:14,fontWeight:900,cursor:"pointer"};
const btnGhost:React.CSSProperties={border:"1px solid #d2d6de",background:"#f2f4f8",padding:"10px 12px",borderRadius:14,fontWeight:900,cursor:"pointer"};
const btnDisabled:React.CSSProperties={...btnPrimary,opacity:.5,cursor:"not-allowed"};
const inp:React.CSSProperties={border:"1px solid #d2d6de",borderRadius:14,padding:"10px 12px",fontWeight:800,minWidth:320};
const pre:React.CSSProperties={background:"#fff",border:"1px solid #d2d6de",borderRadius:14,padding:10,marginTop:10,maxHeight:180,overflow:"auto",fontSize:12};