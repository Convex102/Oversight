let apiKey = localStorage.getItem("ASGL_API_KEY") || "";
let apiBase = localStorage.getItem("ASGL_API_BASE") || (window.location.origin || "http://localhost:8000");
let lastEventCount = 0;
let lastJobId = null;

function $(id){ return document.getElementById(id); }

function log(m){
  const out = $("out");
  out.textContent += m + "\n";
  out.scrollTop = out.scrollHeight;
}

function normalizeKey(raw){
  const k = (raw || "").trim();
  if (!k) return "";
  if (k.toLowerCase().startsWith("bearer ")) return k;
  return k;
}

function authHeaders(){
  const k = apiKey;
  if (!k) return {};
  if (k.toLowerCase().startsWith("bearer ")){
    return { "Authorization": k };
  }
  return { "X-API-Key": k };
}

function save(){
  apiKey = normalizeKey($("api").value);
  apiBase = ($("base").value || "").trim() || apiBase;
  localStorage.setItem("ASGL_API_KEY", apiKey);
  localStorage.setItem("ASGL_API_BASE", apiBase);
  log("Saved configuration.");
}

function clearCfg(){
  localStorage.removeItem("ASGL_API_KEY");
  localStorage.removeItem("ASGL_API_BASE");
  apiKey = "";
  apiBase = window.location.origin || "http://localhost:8000";
  $("api").value = "";
  $("base").value = apiBase;
  log("Cleared configuration.");
}

async function uploadOne(file){
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(apiBase + "/v1/uploads", { method: "POST", headers: authHeaders(), body: fd });
  if(!r.ok){
    const t = await r.text();
    throw new Error("Upload failed (" + file.name + "): " + t);
  }
  return await r.json();
}

async function runJob(){
  save();
  if(!apiKey){ log("Missing API key"); return; }
  const files = $("file").files;
  if(!files || !files.length){ log("Select one or more files"); return; }

  $("btnRun").disabled = true;
  $("btnDownload").disabled = true;
  lastEventCount = 0;
  lastJobId = null;

  try{
    log("Uploading " + files.length + " file(s)...");
    const uploadIds = [];
    for(const f of files){
      const meta = await uploadOne(f);
      uploadIds.push(meta.upload_id);
      log("✓ Uploaded: " + (meta.filename || f.name) + " (upload_id=" + meta.upload_id + (meta.dedup ? ", dedup" : "") + ")");
    }

    log("Creating job...");
    const jr = await fetch(apiBase + "/v1/asgl-a/jobs", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders(), { "Idempotency-Key": "excel-addin-" + Date.now() }),
      body: JSON.stringify({ upload_ids: uploadIds, options: {} })
    });
    if(!jr.ok){
      throw new Error("Job create failed: " + await jr.text());
    }
    const job = await jr.json();
    lastJobId = job.job_id;
    log("✓ Job created: " + lastJobId + " (status=" + job.status + ")");

    await pollJob(lastJobId);

  } catch(e){
    log("ERROR: " + (e && e.message ? e.message : String(e)));
  } finally {
    $("btnRun").disabled = false;
  }
}

async function pollJob(jobId){
  let done = false;
  while(!done){
    await new Promise(r=>setTimeout(r, 1800));

    // progress events (optional)
    try{
      const er = await fetch(apiBase + "/v1/asgl-a/jobs/" + jobId + "/events?limit=250", { headers: authHeaders() });
      if(er.ok){
        const ej = await er.json();
        const ev = ej.events || [];
        if(ev.length > lastEventCount){
          for(const rec of ev.slice(lastEventCount)){
            const pct = (rec.progress === null || rec.progress === undefined) ? "" : (" " + Math.round(rec.progress) + "%");
            log("• " + (rec.event || "event") + pct + " — " + (rec.message || ""));
          }
          lastEventCount = ev.length;
        }
      }
    }catch(_){}

    const gr = await fetch(apiBase + "/v1/asgl-a/jobs/" + jobId, { headers: authHeaders() });
    if(!gr.ok){
      log("Poll error: " + await gr.text());
      continue;
    }
    const gd = await gr.json();
    log("status=" + gd.status);

    if(gd.status === "succeeded"){
      done = true;
      $("btnDownload").disabled = false;
      log("✓ Completed. You can download the report.");
    } else if(gd.status === "failed"){
      done = true;
      log("✗ Failed: " + (gd.error || "unknown error"));
    }
  }
}

async function downloadReport(){
  if(!lastJobId){ log("No job available."); return; }
  save();
  $("btnDownload").disabled = true;
  try{
    const r = await fetch(apiBase + "/v1/asgl-a/jobs/" + lastJobId + "/artifact/report", { headers: authHeaders() });
    if(!r.ok) throw new Error(await r.text());
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "asgl_report_" + lastJobId + ".pdf";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    log("Downloaded report.");
  } catch(e){
    log("Download failed: " + (e && e.message ? e.message : String(e)));
  } finally {
    $("btnDownload").disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("api").value = apiKey;
  $("base").value = apiBase;

  $("btnSave").onclick = save;
  $("btnClear").onclick = clearCfg;
  $("btnRun").onclick = runJob;
  $("btnDownload").onclick = downloadReport;
});
